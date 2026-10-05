"""The cost a user can see: this conversation's running total and how much of today is left
(ROAD_TO_10 8.1).

The per-turn figure already travelled on the `done` event. What was missing was the second and
third thing an engineer asks -- "what has this conversation cost" and "how close am I to the
limit" -- and the honesty rules that go with a money figure: an unpriced call is counted, never
silently left out, and a warning says which ceiling it is about.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai import usage as token_usage
from app.models import AITokenUsage, Conversation
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _row(
    db: Session,
    user_id: str,
    conversation_id: str | None,
    *,
    prompt: int = 100,
    completion: int = 10,
    micro: int | None = 1_000,
    purpose: str = "chat",
) -> None:
    db.add(
        AITokenUsage(
            user_id=user_id,
            conversation_id=conversation_id,
            usage_date=token_usage.datetime.now(token_usage.timezone.utc).date(),
            purpose=purpose,
            provider="p",
            model="m",
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_micro_usd=micro,
        )
    )
    db.flush()


def _used(tokens: int = 0, micro: int = 0) -> token_usage.DayUsage:
    return token_usage.DayUsage(
        prompt_tokens=tokens, completion_tokens=0, cached_prompt_tokens=0,
        cost_micro_usd=micro, unpriced_calls=0,
    )


class TestTheAllowanceNamesTheCeilingItIsAbout:
    def test_no_ceiling_is_unlimited_not_zero_percent(self) -> None:
        state = token_usage.allowance(_used(5_000), token_budget=0, cost_budget_micro=0)
        assert state.level == "unlimited" and state.percent is None and state.basis is None

    @pytest.mark.parametrize(
        ("tokens", "level"),
        [(0, "ok"), (79_999, "ok"), (80_000, "warning"), (99_999, "warning"), (100_000, "exhausted")],
    )
    def test_the_warning_starts_at_eighty_percent_and_exhausted_at_a_hundred(
        self, tokens: int, level: str
    ) -> None:
        state = token_usage.allowance(_used(tokens), token_budget=100_000, cost_budget_micro=0)
        assert state.level == level
        assert state.basis == "tokens"

    def test_the_ceiling_that_will_be_hit_first_is_the_one_reported(self) -> None:
        state = token_usage.allowance(
            _used(tokens=10_000, micro=900_000), token_budget=100_000, cost_budget_micro=1_000_000
        )
        assert (state.percent, state.basis, state.level) == (90, "cost", "warning")

    def test_a_budget_in_one_unit_only_ignores_the_other(self) -> None:
        state = token_usage.allowance(
            _used(tokens=50_000, micro=10**9), token_budget=100_000, cost_budget_micro=0
        )
        assert (state.percent, state.basis) == (50, "tokens")


class TestAConversationsCostIsALedgerSum:
    def test_it_sums_only_that_conversation_and_counts_the_unpriced(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        mine = Conversation(owner_id=current_user_id, title="a")
        other = Conversation(owner_id=current_user_id, title="b")
        db_session.add_all([mine, other])
        db_session.flush()
        _row(db_session, current_user_id, mine.id, micro=1_500)
        _row(db_session, current_user_id, mine.id, micro=500, purpose="title")
        _row(db_session, current_user_id, mine.id, micro=None)
        _row(db_session, current_user_id, other.id, micro=9_999)

        used = token_usage.conversation_usage(db_session, mine.id)
        assert used.cost_micro_usd == 2_000
        assert used.unpriced_calls == 1
        assert used.prompt_tokens == 300

    def test_an_empty_conversation_is_zero_not_missing(self, db_session: Session) -> None:
        used = token_usage.conversation_usage(db_session, "no-such-conversation")
        assert (used.cost_micro_usd, used.unpriced_calls, used.prompt_tokens) == (0, 0, 0)


class TestTheUsageRoute:
    def test_it_reports_the_conversation_the_day_and_the_allowance(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_daily_token_budget", 1_000)
        conversation = Conversation(owner_id=current_user_id, title="c")
        db_session.add(conversation)
        db_session.flush()
        _row(db_session, current_user_id, conversation.id, prompt=800, completion=10)

        body = auth_client.get(f"{API}/ai/conversations/{conversation.id}/usage").json()
        assert body["conversation"]["prompt_tokens"] == 800
        assert body["today"]["prompt_tokens"] >= 800
        assert body["allowance"]["level"] == "warning"
        assert body["allowance"]["basis"] == "tokens"
        assert body["allowance"]["percent"] == 81

    def test_another_users_conversation_is_a_404(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        from app.core.security import hash_password
        from app.models import User

        stranger = User(email="s@x.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = Conversation(owner_id=stranger.id, title="theirs")
        db_session.add(theirs)
        db_session.flush()
        assert auth_client.get(f"{API}/ai/conversations/{theirs.id}/usage").status_code == 404

    def test_the_account_wide_usage_route_carries_the_same_allowance_fields(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        allowance = auth_client.get(f"{API}/ai/usage").json()["allowance"]
        assert {"percent", "level", "basis"} <= set(allowance)

