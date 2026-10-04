"""An organisation's AI spending cap, its warnings, and the tenant limit nothing used to read.

ROAD_TO_10 1.3. A per-user budget cannot protect the person who is invoiced: ten users each
under their own allowance can spend ten times what the owner agreed to. The claims, each of
which a weaker implementation passes a weaker test for:

* a call is billed to the **project's** organisation when the user belongs to it, else to
  the user's own, and the usage ledger and the cap read the same function;
* the cap sums **every member's** priced spend, not the caller's;
* a tenant's own cap beats the global one, and ``0`` means "unlimited", not "unset";
* a warning goes out **once** per organisation, period and threshold -- and a turn that
  jumps past both thresholds sends one mail, not two;
* nothing about the mail can fail the turn it follows;
* the tenant's ``ai_daily_token_budget`` -- shown as "in force" on the quota page since P8 and
  read by nothing on the chat path -- is now what the chat path enforces.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import mail
from app.ai import org_budget, pricing
from app.ai import usage as token_usage
from app.ai.provider import AssistantTurn, LLMError, LLMProvider, TokenUsage
from app.api.routes import ai as ai_routes
from app.core.config import ModelPrice, settings
from app.core.security import hash_password
from app.mail import MailKind, Outbox
from app.models import (
    AIBudgetAlert,
    AITokenUsage,
    Conversation,
    Membership,
    Organisation,
    Project,
    User,
)
from app.models.billing import BillingAccount, Meter, UsageRecord
from app.models.organisation import OrgRole

from .conftest import register_verified

DOLLAR = 1_000_000


def _org(db: Session, slug: str) -> Organisation:
    org = Organisation(name=slug.title(), slug=slug, is_personal=False)
    db.add(org)
    db.flush()
    return org


def _member(
    db: Session, org: Organisation, email: str, role: OrgRole = OrgRole.MEMBER
) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, hashed_password=hash_password("a-long-enough-password"))
        db.add(user)
        db.flush()
    db.add(Membership(organisation_id=org.id, user_id=user.id, role=role))
    db.flush()
    # `known_memberships` trusts a loaded collection; a stale one would hide the row.
    db.expire(user, ["memberships"])
    return user


def _spend(
    db: Session,
    user: User,
    org: Organisation | None,
    micro: int | None,
    *,
    day: date | None = None,
) -> None:
    """One ledger row with a chosen cost (None = an unpriced call)."""
    db.add(
        AITokenUsage(
            user_id=user.id,
            organisation_id=org.id if org is not None else None,
            usage_date=day or org_budget._today(),
            purpose="chat",
            provider="p",
            model="m",
            prompt_tokens=100,
            completion_tokens=10,
            cost_micro_usd=micro,
        )
    )
    db.flush()


@pytest.fixture
def caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """$10 a day and $100 a month, globally."""
    monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal("10"))
    monkeypatch.setattr(settings, "ai_org_monthly_cost_budget_usd", Decimal("100"))


class TestACallIsBilledToTheRightOrganisation:
    def test_a_project_in_a_second_organisation_bills_that_organisation(
        self, db_session: Session
    ) -> None:
        first = _org(db_session, "aaa-first")
        second = _org(db_session, "zzz-second")
        user = _member(db_session, first, "two-orgs@kryova.dev")
        _member(db_session, second, "two-orgs@kryova.dev")
        project = Project(name="P", owner_id=user.id, organisation_id=second.id)
        db_session.add(project)
        db_session.flush()

        assert org_budget.billed_organisation(db_session, user, project.id) == second.id

    def test_without_a_project_it_is_the_first_organisation_by_id_every_time(
        self, db_session: Session
    ) -> None:
        a = _org(db_session, "bbb-a")
        b = _org(db_session, "ccc-b")
        user = _member(db_session, a, "stable@kryova.dev")
        _member(db_session, b, "stable@kryova.dev")
        expected = sorted([a.id, b.id])[0]
        assert {org_budget.billed_organisation(db_session, user) for _ in range(5)} == {expected}

    def test_a_project_of_an_organisation_the_user_is_not_in_is_ignored(
        self, db_session: Session
    ) -> None:
        mine = _org(db_session, "mine-co")
        theirs = _org(db_session, "theirs-co")
        owner = _member(db_session, theirs, "owner@kryova.dev")
        stranger = _member(db_session, mine, "stranger@kryova.dev")
        project = Project(name="Theirs", owner_id=owner.id, organisation_id=theirs.id)
        db_session.add(project)
        db_session.flush()
        # Naming someone else's project must not move the bill onto them.
        assert org_budget.billed_organisation(db_session, stranger, project.id) == mine.id

    def test_a_user_in_no_organisation_is_billed_to_nobody(self, db_session: Session) -> None:
        loner = User(email="loner@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(loner)
        db_session.flush()
        assert org_budget.billed_organisation(db_session, loner) is None

    def test_the_ledger_row_carries_the_organisation_it_was_billed_to(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        org = _org(db_session, "ledger-co")
        user = _member(db_session, org, "ledger@kryova.dev")
        token_usage.record(
            db_session, user=user, usage=TokenUsage(10, 1), purpose="chat", provider="p", model="m"
        )
        assert db_session.scalar(select(AITokenUsage.organisation_id)) == org.id


class TestTheCapSumsEveryMember:
    def test_a_colleagues_spend_counts_against_my_turn(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "team-co")
        alice = _member(db_session, org, "alice@kryova.dev")
        bob = _member(db_session, org, "bob@kryova.dev")
        _spend(db_session, alice, org, 10 * DOLLAR)

        assert token_usage.refusal(db_session, bob) is not None
        # ...and Bob has spent nothing himself: his own allowance is untouched.
        assert token_usage.exceeded(db_session, bob.id) is None

    def test_another_organisations_spend_does_not(self, db_session: Session, caps: None) -> None:
        mine = _org(db_session, "mine-co")
        other = _org(db_session, "other-co")
        me = _member(db_session, mine, "me@kryova.dev")
        them = _member(db_session, other, "them@kryova.dev")
        _spend(db_session, them, other, 500 * DOLLAR)
        assert token_usage.refusal(db_session, me) is None

    def test_just_under_the_cap_is_allowed_and_exactly_at_it_is_not(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "edge-co")
        user = _member(db_session, org, "edge@kryova.dev")
        _spend(db_session, user, org, 10 * DOLLAR - 1)
        assert token_usage.refusal(db_session, user) is None
        _spend(db_session, user, org, 1)
        assert token_usage.refusal(db_session, user) is not None

    def test_yesterdays_spend_counts_toward_the_month_and_not_toward_today(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "month-co")
        user = _member(db_session, org, "month@kryova.dev")
        today = org_budget._today()
        yesterday = today - timedelta(days=1)
        if yesterday.month != today.month:  # the 1st: there is no earlier day this month
            pytest.skip("yesterday is in last month")
        _spend(db_session, user, org, 50 * DOLLAR, day=yesterday)
        current = org_budget.status(db_session, org.id)
        day, month = current.caps
        assert (day.spent_micro_usd, month.spent_micro_usd) == (0, 50 * DOLLAR)

    def test_last_months_spend_does_not_count_this_month(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "lastmonth-co")
        user = _member(db_session, org, "lastmonth@kryova.dev")
        last_day_of_last_month = org_budget._today().replace(day=1) - timedelta(days=1)
        _spend(db_session, user, org, 500 * DOLLAR, day=last_day_of_last_month)
        day, month = org_budget.status(db_session, org.id).caps
        assert (day.spent_micro_usd, month.spent_micro_usd) == (0, 0)
        assert token_usage.refusal(db_session, user) is None

    def test_a_spent_month_is_what_the_refusal_names_not_a_day_with_room(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "both-co")
        user = _member(db_session, org, "both@kryova.dev")
        _spend(db_session, user, org, 100 * DOLLAR)  # month AND day (cap 10) are both over
        message = token_usage.refusal(db_session, user)
        assert message is not None
        assert "monthly" in message and "$100.00" in message
        # "resets at 00:00 UTC" would send the user back tomorrow to be refused again.
        assert "at 00:00 UTC" in message and " on " in message

    def test_unpriced_calls_are_counted_and_never_summed(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "unpriced-co")
        user = _member(db_session, org, "unpriced@kryova.dev")
        _spend(db_session, user, org, 3 * DOLLAR)
        _spend(db_session, user, org, None)
        _spend(db_session, user, org, None)
        current = org_budget.status(db_session, org.id)
        assert current.caps[0].spent_micro_usd == 3 * DOLLAR
        assert current.unpriced_calls == 2

    def test_no_cap_configured_reads_nothing_and_refuses_nothing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal(0))
        monkeypatch.setattr(settings, "ai_org_monthly_cost_budget_usd", Decimal(0))
        org = _org(db_session, "free-co")
        user = _member(db_session, org, "free@kryova.dev")
        _spend(db_session, user, org, 10_000 * DOLLAR)
        assert token_usage.refusal(db_session, user) is None
        assert all(not cap.capped for cap in org_budget.status(db_session, org.id).caps)


class TestATenantsOwnCapBeatsTheGlobalOne:
    def _account(self, db: Session, org: Organisation, **fields: Any) -> None:
        db.add(BillingAccount(organisation_id=org.id, **fields))
        db.flush()

    def test_a_tighter_tenant_cap_refuses_where_the_global_one_would_not(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "tight-co")
        user = _member(db_session, org, "tight@kryova.dev")
        self._account(db_session, org, ai_org_daily_cost_budget_micro_usd=2 * DOLLAR)
        _spend(db_session, user, org, 2 * DOLLAR)
        assert token_usage.refusal(db_session, user) is not None
        assert org_budget.status(db_session, org.id).caps[0].source == org_budget.SOURCE_TENANT

    def test_zero_is_unlimited_not_unset(self, db_session: Session, caps: None) -> None:
        org = _org(db_session, "vip-co")
        user = _member(db_session, org, "vip@kryova.dev")
        self._account(
            db_session,
            org,
            ai_org_daily_cost_budget_micro_usd=0,
            ai_org_monthly_cost_budget_micro_usd=0,
        )
        _spend(db_session, user, org, 1_000 * DOLLAR)
        assert token_usage.refusal(db_session, user) is None

    def test_null_falls_back_to_the_global_and_says_so(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "default-co")
        self._account(db_session, org)
        day, month = org_budget.status(db_session, org.id).caps
        assert (day.source, month.source) == (org_budget.SOURCE_GLOBAL, org_budget.SOURCE_GLOBAL)
        assert (day.cap_micro_usd, month.cap_micro_usd) == (10 * DOLLAR, 100 * DOLLAR)


class TestTheWarningIsSentOnce:
    def _setup(self, db: Session) -> tuple[Organisation, User]:
        org = _org(db, "alert-co")
        _member(db, org, "owner@alert.dev", OrgRole.OWNER)
        _member(db, org, "admin@alert.dev", OrgRole.ADMIN)
        worker = _member(db, org, "worker@alert.dev")
        return org, worker

    def test_crossing_eighty_percent_mails_the_owner_and_not_the_admin_or_the_worker(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)

        sent = outbox.of_kind(MailKind.AI_BUDGET_ALERT)
        assert [m.to for m in sent] == ["owner@alert.dev"]
        assert "80%" in sent[0].subject and "daily" in sent[0].subject

    def test_asking_again_sends_nothing_more(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)
        org_budget.alert_if_crossed(db_session, org.id)
        org_budget.alert_if_crossed(db_session, org.id)
        assert len(outbox.of_kind(MailKind.AI_BUDGET_ALERT)) == 1

    def test_reaching_the_cap_sends_a_second_and_different_mail(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)
        _spend(db_session, worker, org, 2 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)

        subjects = [m.subject for m in outbox.of_kind(MailKind.AI_BUDGET_ALERT)]
        assert len(subjects) == 2
        assert "80%" in subjects[0] and "reached" in subjects[1]

    def test_a_turn_that_jumps_past_both_thresholds_sends_only_the_higher_mail(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 10 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)

        sent = outbox.of_kind(MailKind.AI_BUDGET_ALERT)
        assert len(sent) == 1 and "reached" in sent[0].subject
        # Both claims are taken, so the 80 % warning cannot arrive late behind it.
        thresholds = db_session.scalars(
            select(AIBudgetAlert.threshold).where(
                AIBudgetAlert.organisation_id == org.id, AIBudgetAlert.period == "day"
            )
        ).all()
        assert sorted(thresholds) == [80, 100]

    def test_a_new_day_is_a_new_claim(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)
        day = org_budget.status(db_session, org.id).caps[0]
        # The same claim for tomorrow is a different row, so a new day warns again.
        assert org_budget._claim(db_session, org.id, day, 80) is False
        tomorrow = type(day)(
            day.period, day.cap_micro_usd, day.source, day.spent_micro_usd,
            day.period_start + timedelta(days=1), day.resets_on + timedelta(days=1),
        )
        assert org_budget._claim(db_session, org.id, tomorrow, 80) is True

    def test_below_the_threshold_nothing_is_sent_or_claimed(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR - 1)  # 79.99999 %
        org_budget.alert_if_crossed(db_session, org.id)
        assert len(outbox) == 0
        assert db_session.scalar(select(func.count()).select_from(AIBudgetAlert)) == 0

    def test_an_organisation_with_no_owner_warns_its_admins(
        self, db_session: Session, caps: None, outbox: Outbox
    ) -> None:
        org = _org(db_session, "ownerless-co")
        _member(db_session, org, "admin@ownerless.dev", OrgRole.ADMIN)
        worker = _member(db_session, org, "worker@ownerless.dev")
        _spend(db_session, worker, org, 8 * DOLLAR)
        org_budget.alert_if_crossed(db_session, org.id)
        assert [m.to for m in outbox.of_kind(MailKind.AI_BUDGET_ALERT)] == ["admin@ownerless.dev"]

    def test_a_broken_mail_transport_cannot_fail_the_turn(
        self, db_session: Session, caps: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        org, worker = self._setup(db_session)
        _spend(db_session, worker, org, 8 * DOLLAR)

        def boom(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("smtp is down")

        monkeypatch.setattr(mail, "send", boom)
        assert org_budget.alert_if_crossed(db_session, org.id) == 0  # no exception

    def test_no_organisation_means_no_alert_and_no_error(self, db_session: Session) -> None:
        assert org_budget.alert_if_crossed(db_session, None) == 0


class TestTheBannerReadsTheSameNumbers:
    def test_nothing_is_said_below_eighty_percent(self, db_session: Session, caps: None) -> None:
        org = _org(db_session, "quiet-co")
        user = _member(db_session, org, "quiet@kryova.dev")
        _spend(db_session, user, org, 7 * DOLLAR)
        assert org_budget.notices(org_budget.status(db_session, org.id)) == []

    def test_a_warning_then_an_exhausted_notice_with_the_period_named(
        self, db_session: Session, caps: None
    ) -> None:
        org = _org(db_session, "loud-co")
        user = _member(db_session, org, "loud@kryova.dev")
        _spend(db_session, user, org, 8 * DOLLAR + DOLLAR // 2)
        (warning,) = org_budget.notices(org_budget.status(db_session, org.id))
        assert (warning.period, warning.percent, warning.level) == ("day", 85, "warning")
        assert "$8.50 of $10.00" in warning.message

        _spend(db_session, user, org, 2 * DOLLAR)
        notice = org_budget.notices(org_budget.status(db_session, org.id))[0]
        assert notice.level == "exhausted"


class TestTheTenantsTokenLimitIsFinallyEnforced:
    """`BillingAccount.ai_daily_token_budget` was reported as in force and read by nothing."""

    def test_a_tenant_override_replaces_the_global_budget(self, db_session: Session) -> None:
        org = _org(db_session, "override-co")
        user = _member(db_session, org, "override@kryova.dev")
        assert token_usage.effective_daily_token_budget(db_session, org.id) == (
            token_usage.daily_token_budget()
        )
        db_session.add(BillingAccount(organisation_id=org.id, ai_daily_token_budget=500))
        db_session.flush()
        assert token_usage.effective_daily_token_budget(db_session, org.id) == 500

        _spend(db_session, user, org, None)  # 110 tokens
        assert token_usage.refusal(db_session, user) is None
        for _ in range(4):
            _spend(db_session, user, org, None)  # 550 in all, over 500
        message = token_usage.refusal(db_session, user)
        assert message is not None and "500 tokens" in message

    def test_zero_means_this_tenant_is_unlimited(self, db_session: Session) -> None:
        org = _org(db_session, "unlimited-co")
        db_session.add(BillingAccount(organisation_id=org.id, ai_daily_token_budget=0))
        db_session.flush()
        assert token_usage.effective_daily_token_budget(db_session, org.id) == 0

    def test_a_user_with_no_organisation_gets_the_global_budget(self, db_session: Session) -> None:
        assert token_usage.effective_daily_token_budget(db_session, None) == (
            token_usage.daily_token_budget()
        )


class _Says(LLMProvider):
    """A provider that answers every turn with `text`, spending `usage`."""

    name = "scripted"
    model = "m"

    def __init__(self, usage: TokenUsage, text: str = "Noted.") -> None:
        self.usage = usage
        self.text = text
        self.calls = 0

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("not used")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        # Only the first call of a turn spends: the title that follows it is a second
        # ledger row of its own, and charging it too would double every figure here.
        self.calls += 1
        return AssistantTurn(
            text=self.text, usage=self.usage if self.calls == 1 else TokenUsage(0, 0, 0)
        )


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in this module may reach a hosted model, whatever a failing test does next.

    A turn that gets past a budget guard that should have stopped it goes on to call
    `get_provider()`; unpatched, that is a live request with a real key. Tests that
    need a particular answer patch it again, and the later patch wins.
    """
    monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(0, 0, 0)))


class TestOnTheRealRoutes:
    def test_a_refused_turn_is_a_429_naming_the_organisation_and_creates_no_conversation(
        self,
        auth_client: Any,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal("1"))
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(0, 0, 0)))
        user = db_session.get(User, current_user_id)
        assert user is not None
        org = _org(db_session, "route-co")
        _member(db_session, org, "eng@kryova.dev")
        # The registered user may already sit in a personal organisation; the explicit
        # project names which one is billed, so the test does not depend on id order.
        project = Project(name="P", owner_id=user.id, organisation_id=org.id)
        db_session.add(project)
        db_session.flush()
        _spend(db_session, user, org, DOLLAR)

        before = db_session.scalar(select(func.count()).select_from(Conversation))

        response = auth_client.post(
            "/api/v1/ai/chat/stream", json={"message": "hello", "project_id": project.id}
        )

        assert response.status_code == 429
        assert "organisation has reached its daily AI spending cap" in response.json()["detail"]
        # The check ran before a conversation was committed for a turn that never starts.
        assert db_session.scalar(select(func.count()).select_from(Conversation)) == before

    def test_the_usage_route_reports_spend_caps_notices_and_whether_a_turn_is_blocked(
        self,
        auth_client: Any,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal("10"))
        user = db_session.get(User, current_user_id)
        assert user is not None
        org = _org(db_session, "usage-co")
        _member(db_session, org, "eng@kryova.dev")
        project = Project(name="P", owner_id=user.id, organisation_id=org.id)
        db_session.add(project)
        db_session.flush()
        _spend(db_session, user, org, 9 * DOLLAR)

        body = auth_client.get("/api/v1/ai/usage", params={"project_id": project.id}).json()

        assert body["organisation_id"] == org.id
        day = next(cap for cap in body["organisation_caps"] if cap["period"] == "day")
        assert (day["cap_micro_usd"], day["spent_micro_usd"], day["percent"]) == (
            10 * DOLLAR,
            9 * DOLLAR,
            90,
        )
        assert [n["level"] for n in body["notices"]] == ["warning"]
        assert body["blocked"] is None
        assert body["today"]["prompt_tokens"] == 100

    def test_another_users_usage_is_not_visible(
        self, auth_client: Any, client: Any, db_session: Session
    ) -> None:
        # The route reads the signed-in principal's own ledger and nothing else.
        other = register_verified(client, db_session, "someone-else@kryova.dev")
        other_user = db_session.get(User, other)
        assert other_user is not None
        org = _org(db_session, "else-co")
        _member(db_session, org, "someone-else@kryova.dev")
        _spend(db_session, other_user, org, 5 * DOLLAR)
        body = auth_client.get("/api/v1/ai/usage").json()
        assert body["today"]["cost_micro_usd"] == 0


class TestTheBillingEndpointSetsAndClearsTheCaps:
    def _owned_org(self, auth_client: Any) -> str:
        created = auth_client.post(
            "/api/v1/organisations", json={"name": "Cap Co", "slug": "cap-co-1"}
        )
        assert created.status_code in (200, 201), created.text
        return created.json()["id"]

    def test_set_read_zero_and_clear(self, auth_client: Any) -> None:
        org_id = self._owned_org(auth_client)
        url = f"/api/v1/organisations/{org_id}/billing"

        put = auth_client.put(
            url,
            json={"ai_org_daily_cost_budget_usd": "2.50", "ai_org_monthly_cost_budget_usd": "40"},
        )
        assert put.status_code == 200, put.text
        assert Decimal(put.json()["ai_org_daily_cost_budget_usd"]) == Decimal("2.5")
        assert Decimal(put.json()["ai_org_monthly_cost_budget_usd"]) == Decimal("40")

        zero = auth_client.put(url, json={"ai_org_daily_cost_budget_usd": "0"}).json()
        assert Decimal(zero["ai_org_daily_cost_budget_usd"]) == 0  # unlimited, not unset

        cleared = auth_client.put(url, json={"ai_org_daily_cost_budget_usd": "-1"}).json()
        assert cleared["ai_org_daily_cost_budget_usd"] is None
        # Leaving a field out leaves it alone.
        assert Decimal(cleared["ai_org_monthly_cost_budget_usd"]) == Decimal("40")

    def test_the_money_is_stored_as_integer_micro_dollars(
        self, auth_client: Any, db_session: Session
    ) -> None:
        org_id = self._owned_org(auth_client)
        auth_client.put(
            f"/api/v1/organisations/{org_id}/billing",
            json={"ai_org_daily_cost_budget_usd": "0.000001"},
        )
        stored = db_session.scalar(
            select(BillingAccount.ai_org_daily_cost_budget_micro_usd).where(
                BillingAccount.organisation_id == org_id
            )
        )
        assert stored == 1 == pricing.budget_micro(Decimal("0.000001"))


class TestTheWholeTurnWarnsAndThenRefuses:
    """The wiring, through the real route: nothing here calls `org_budget` directly."""

    @pytest.fixture
    def tenant(
        self, auth_client: Any, db_session: Session, current_user_id: str, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Organisation, Project]:
        # $1 per million tokens, so 1,000 prompt tokens cost exactly 1,000 micro-dollars,
        # against a cap of 1,200: one turn is 83 %, two are 166 %.
        monkeypatch.setattr(
            settings, "ai_prices", {"m": ModelPrice(input=Decimal(1), output=Decimal(1))}
        )
        monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal("0.0012"))
        monkeypatch.setattr(settings, "ai_org_monthly_cost_budget_usd", Decimal(0))
        org = _org(db_session, "wired-co")
        _member(db_session, org, "eng@kryova.dev", OrgRole.OWNER)
        project = Project(name="P", owner_id=current_user_id, organisation_id=org.id)
        db_session.add(project)
        db_session.flush()
        return org, project

    def test_the_second_turn_reaches_the_cap_and_the_third_is_refused(
        self,
        auth_client: Any,
        db_session: Session,
        tenant: tuple[Organisation, Project],
        outbox: Outbox,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        org, project = tenant
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(1_000, 0, 0)))
        body = {"message": "hello", "project_id": project.id}

        first = auth_client.post("/api/v1/ai/chat/stream", json=body)
        assert first.status_code == 200
        warnings = outbox.of_kind(MailKind.AI_BUDGET_ALERT)
        assert [m.to for m in warnings] == ["eng@kryova.dev"]
        assert "80%" in warnings[0].subject

        second = auth_client.post("/api/v1/ai/chat/stream", json=body)
        assert second.status_code == 200  # it started under the cap, and a turn is allowed to finish
        subjects = [m.subject for m in outbox.of_kind(MailKind.AI_BUDGET_ALERT)]
        assert len(subjects) == 2 and "reached" in subjects[1]

        third = auth_client.post("/api/v1/ai/chat/stream", json=body)
        assert third.status_code == 429
        assert len(outbox.of_kind(MailKind.AI_BUDGET_ALERT)) == 2  # a refused turn warns nobody again

    def test_the_ledger_of_that_turn_names_the_organisation_it_was_billed_to(
        self,
        auth_client: Any,
        db_session: Session,
        tenant: tuple[Organisation, Project],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        org, project = tenant
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(1_000, 0, 0)))
        auth_client.post("/api/v1/ai/chat/stream", json={"message": "hi", "project_id": project.id})
        row = db_session.scalars(select(AITokenUsage).where(AITokenUsage.purpose == "chat")).one()
        assert row.organisation_id == org.id
        assert row.cost_micro_usd == 1_000

    def test_the_bill_and_the_cap_are_one_set_of_calls(
        self,
        auth_client: Any,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A user in two organisations works on a project of the one that is *not* first
        # by id. The usage bill used to take the first, so the invoice named an
        # organisation the cap never counted; both now read `billed_organisation`.
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(1_000, 0, 0)))
        a, b = _org(db_session, "bill-a-co"), _org(db_session, "bill-b-co")
        _member(db_session, a, "eng@kryova.dev")
        _member(db_session, b, "eng@kryova.dev")
        later = max((a, b), key=lambda o: o.id)
        project = Project(name="P", owner_id=current_user_id, organisation_id=later.id)
        db_session.add(project)
        db_session.flush()

        auth_client.post("/api/v1/ai/chat/stream", json={"message": "hi", "project_id": project.id})

        billed = db_session.scalars(
            select(UsageRecord.organisation_id).where(UsageRecord.meter == Meter.AI_TOKENS)
        ).all()
        assert billed and set(billed) == {later.id}
        ledger = db_session.scalars(
            select(AITokenUsage.organisation_id).where(AITokenUsage.purpose == "chat")
        ).all()
        assert set(ledger) == {later.id}

    def test_an_existing_conversation_is_checked_against_its_own_projects_organisation(
        self,
        auth_client: Any,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "ai_org_daily_cost_budget_usd", Decimal("1"))
        # If the guard under test failed, the turn would go on to call a model. It must
        # never be a real one: a test that can spend money on failure is a hazard.
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _Says(TokenUsage(0, 0, 0)))
        user = db_session.get(User, current_user_id)
        assert user is not None
        a, b = _org(db_session, "conv-a-co"), _org(db_session, "conv-b-co")
        _member(db_session, a, "eng@kryova.dev")
        _member(db_session, b, "eng@kryova.dev")
        # The organisation that is *not* first by id is the one the fallback would miss.
        project_org = max((a, b), key=lambda o: o.id)
        project = Project(name="P", owner_id=user.id, organisation_id=project_org.id)
        db_session.add(project)
        db_session.flush()  # a Python-side default id does not exist until here
        conversation = Conversation(owner_id=user.id, project_id=project.id, title="old")
        db_session.add(conversation)
        db_session.flush()
        assert conversation.project_id == project.id
        _spend(db_session, user, project_org, DOLLAR)

        response = auth_client.post(
            "/api/v1/ai/chat/stream",
            json={"message": "continue", "conversation_id": conversation.id},
        )
        assert response.status_code == 429

    def test_someone_elses_conversation_id_does_not_leak_through_the_billing_read(
        self, auth_client: Any, client: Any, db_session: Session
    ) -> None:
        other_id = register_verified(client, db_session, "owner-of-it@kryova.dev")
        theirs = Conversation(owner_id=other_id, title="private")
        db_session.add(theirs)
        db_session.flush()
        response = auth_client.post(
            "/api/v1/ai/chat/stream", json={"message": "hi", "conversation_id": theirs.id}
        )
        assert response.status_code == 404
