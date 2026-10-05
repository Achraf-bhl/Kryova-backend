"""Limits per plan, not global constants (ROAD_TO_10 3.5).

Six numbers used to be module constants read at import: the concurrent-run ceiling, the
length of the queue behind it, and the per-minute budgets for chat, simulations, MCP and
CATIA. A paid team and a free account cannot be told apart by a constant.

What has to hold, because each half is the reason for the next:

* the order is **tenant override, then the plan, then the global setting**, everywhere;
* **the number an owner is shown is the number a request meets** -- before this, the quota
  surface listed the concurrent-run limit with its source while the route and the agent tool
  read `settings` directly, so an override was displayed and never applied;
* a typo in `PLAN_LIMITS` is a startup error, not a limit that silently never applies;
* a person in several organisations is not throttled to the least generous one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core import limits
from app.core.config import LIMIT_FIELDS, PLAN_NAMES, Settings, settings
from app.core.metering import _QUOTA_FIELDS, PlanAllowance, build_plans, quota_envelope
from app.models import JobStatus, SimulationJob
from app.models.billing import BillingAccount, Plan
from app.models.organisation import organisation_ids_for
from app.models.project import Project
from app.models.user import User
from app.schemas.billing import BillingAccountUpdate
from tests.conftest import register_verified
from tests.test_mesh import box_stl
from tests.test_simulations import BOX, load_case
from tests.typing import AuthenticatedTestClient

RATES = tuple(name for name in LIMIT_FIELDS if name.endswith("_per_minute"))


@pytest.fixture
def organisation_id(auth_client: AuthenticatedTestClient) -> str:
    """A second organisation the signed-in user owns, beside their personal one."""
    response = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture
def project_with_geometry(auth_client: AuthenticatedTestClient, project_id: str) -> str:
    response = auth_client.post(
        f"/api/v1/projects/{project_id}/geometry",
        files={"file": ("box.stl", box_stl(BOX), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return project_id


def _billing(client: AuthenticatedTestClient, organisation_id: str, **fields: object):
    response = client.put(f"/api/v1/organisations/{organisation_id}/billing", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def _plans(monkeypatch: pytest.MonkeyPatch, table: dict[str, dict[str, int]]) -> None:
    monkeypatch.setattr(settings, "plan_limits", table)
    limits.forget()


class TestTheListsThatMustAgreeDo:
    def test_the_plan_names_are_the_plans_there_are(self) -> None:
        # `config` cannot import the model, so it carries its own copy; this is the weld.
        assert PLAN_NAMES == {plan.value for plan in Plan}

    @pytest.mark.parametrize("name", LIMIT_FIELDS)
    def test_every_limit_is_a_setting_an_allowance_a_column_a_field_and_a_quota_line(
        self, name: str
    ) -> None:
        assert hasattr(settings, name)
        assert name in PlanAllowance.__dataclass_fields__
        assert hasattr(BillingAccount, name)
        assert name in BillingAccountUpdate.model_fields
        assert name in {field for field, _ in _QUOTA_FIELDS}

    def test_the_quota_surface_lists_no_limit_the_resolver_cannot_answer(self) -> None:
        # The other direction: a line the envelope shows that `limits` refuses to resolve is
        # a number an owner can read and no request will ever meet.
        listed = {field for field, _ in _QUOTA_FIELDS}
        for name in LIMIT_FIELDS:
            assert name in listed

    def test_an_unknown_limit_is_an_error_not_the_global_default(self, db_session: Session) -> None:
        with pytest.raises(KeyError, match="not a limit a plan can change"):
            limits.global_limit("max_media_bytes")
        with pytest.raises(KeyError):
            limits.for_organisation(db_session, "any", "nonsense")
        with pytest.raises(KeyError):
            limits.for_user(db_session, "any", "nonsense")


class TestPlanLimitsIsCheckedWhenTheSettingsLoad:
    def _load(self, monkeypatch: pytest.MonkeyPatch, value: str) -> Settings:
        monkeypatch.setenv("PLAN_LIMITS", value)
        return Settings(_env_file=None)  # type: ignore[call-arg]

    def test_a_table_for_a_plan_reads_from_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loaded = self._load(monkeypatch, '{"team": {"chat_requests_per_minute": 40}}')
        assert loaded.plan_limits == {"team": {"chat_requests_per_minute": 40}}

    def test_nothing_set_means_nothing_changed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PLAN_LIMITS", raising=False)
        assert Settings(_env_file=None).plan_limits == {}  # type: ignore[call-arg]

    def test_a_plan_that_does_not_exist_is_refused_naming_the_ones_that_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError, match="names a plan 'gold'.*enterprise, free, team"):
            self._load(monkeypatch, '{"gold": {"chat_requests_per_minute": 40}}')

    def test_a_limit_that_does_not_exist_is_refused_naming_the_ones_that_do(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError, match="names a limit 'chat_per_min'.*chat_requests_per_minute"):
            self._load(monkeypatch, '{"free": {"chat_per_min": 40}}')

    @pytest.mark.parametrize("name", RATES)
    def test_a_rate_of_zero_is_refused(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        with pytest.raises(ValidationError, match="must be at least 1"):
            self._load(monkeypatch, f'{{"free": {{"{name}": 0}}}}')

    def test_a_queue_of_zero_is_real_and_means_nothing_waits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loaded = self._load(monkeypatch, '{"free": {"max_waiting_simulations_per_user": 0}}')
        assert loaded.plan_limits["free"]["max_waiting_simulations_per_user"] == 0

    def test_a_negative_number_is_refused_even_for_the_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError, match="must be at least 0"):
            self._load(monkeypatch, '{"free": {"max_waiting_simulations_per_user": -1}}')


class TestTheOrderIsOverrideThenPlanThenGlobal:
    def test_with_nothing_written_the_global_setting_answers(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        resolved = limits.for_organisation(db_session, organisation_id, "chat_requests_per_minute")

        assert resolved.value == settings.chat_requests_per_minute
        assert resolved.from_settings
        assert resolved.source == "global settings"

    def test_a_plan_that_wrote_a_limit_beats_the_global_setting(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"team": {"chat_requests_per_minute": 77}})
        _billing(auth_client, organisation_id, plan="team")

        resolved = limits.for_organisation(db_session, organisation_id, "chat_requests_per_minute")

        assert (resolved.value, resolved.source) == (77, "plan team")
        assert resolved.organisation_id == organisation_id
        assert not resolved.from_settings

    def test_a_plan_that_wrote_nothing_for_this_limit_falls_through(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"team": {"chat_requests_per_minute": 77}})
        _billing(auth_client, organisation_id, plan="team")

        resolved = limits.for_organisation(db_session, organisation_id, "mcp_requests_per_minute")

        assert resolved.from_settings
        assert resolved.value == settings.mcp_requests_per_minute

    def test_a_tenant_override_beats_the_plan(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"team": {"chat_requests_per_minute": 77}})
        _billing(auth_client, organisation_id, plan="team", chat_requests_per_minute=5)

        resolved = limits.for_organisation(db_session, organisation_id, "chat_requests_per_minute")

        assert (resolved.value, resolved.source) == (5, "tenant override")

    def test_clearing_the_override_returns_to_the_plan_not_to_the_global_setting(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"team": {"chat_requests_per_minute": 77}})
        _billing(auth_client, organisation_id, plan="team", chat_requests_per_minute=5)

        _billing(auth_client, organisation_id, chat_requests_per_minute=-1)

        resolved = limits.for_organisation(db_session, organisation_id, "chat_requests_per_minute")
        assert (resolved.value, resolved.source) == (77, "plan team")

    def test_an_organisation_with_no_billing_account_is_on_the_free_plan(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"free": {"simulation_requests_per_minute": 3}})

        resolved = limits.for_organisation(
            db_session, organisation_id, "simulation_requests_per_minute"
        )

        assert (resolved.value, resolved.source) == (3, "plan free")

    def test_the_envelope_and_the_resolver_never_disagree(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The defect this item exists for: the owner is shown one number, the request meets another.
        _plans(monkeypatch, {"team": {"catia_ops_per_minute": 90, "chat_requests_per_minute": 20}})
        _billing(auth_client, organisation_id, plan="team", mcp_requests_per_minute=9)

        envelope = quota_envelope(db_session, organisation_id)

        for name in LIMIT_FIELDS:
            shown = envelope.limit_for(name)
            met = limits.for_organisation(db_session, organisation_id, name)
            assert shown is not None
            assert (met.value, met.source) == (shown.limit, shown.source), name


class TestEveryPlanCarriesWhatWasWrittenForIt:
    @pytest.mark.parametrize("plan", list(Plan))
    def test_each_plans_allowance_holds_its_own_table_and_only_that(
        self, monkeypatch: pytest.MonkeyPatch, plan: Plan
    ) -> None:
        # An enterprise plan has no meter allowances and is built by a different line from
        # the other two; a limit written for it must still arrive.
        _plans(monkeypatch, {plan.value: {"chat_requests_per_minute": 321}})

        allowances = build_plans()

        assert allowances[plan].chat_requests_per_minute == 321
        for other in Plan:
            if other is not plan:
                assert allowances[other].chat_requests_per_minute is None


class TestTheBillingRouteTakesEveryLimit:
    @pytest.mark.parametrize("name", LIMIT_FIELDS)
    def test_an_override_is_recorded_shown_and_cleared(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        db_session: Session,
        name: str,
    ) -> None:
        def row(body: dict) -> dict:
            return next(r for r in body["quotas"]["limits"] if r["name"] == name)

        body = _billing(auth_client, organisation_id, **{name: 17})
        assert (row(body)["limit"], row(body)["source"]) == (17, "tenant override")
        assert limits.for_organisation(db_session, organisation_id, name).value == 17

        body = _billing(auth_client, organisation_id, **{name: -1})
        assert row(body)["source"] == "global settings"

    @pytest.mark.parametrize("name", RATES)
    def test_a_rate_of_zero_is_refused_in_words(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, name: str
    ) -> None:
        response = auth_client.put(
            f"/api/v1/organisations/{organisation_id}/billing", json={name: 0}
        )

        assert response.status_code == 422
        assert "ambiguous" in response.text

    def test_a_queue_of_zero_is_accepted_and_means_nothing_waits(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        _billing(auth_client, organisation_id, max_waiting_simulations_per_user=0)

        resolved = limits.for_organisation(
            db_session, organisation_id, "max_waiting_simulations_per_user"
        )
        assert (resolved.value, resolved.source) == (0, "tenant override")

    def test_a_change_is_felt_at_once_by_the_process_that_made_it(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]
        before = limits.for_user(db_session, user_id, "chat_requests_per_minute").value

        _billing(auth_client, organisation_id, chat_requests_per_minute=before + 500)

        # Not 30 s later: the PUT forgets the cache in the process that handled it.
        assert limits.for_user(db_session, user_id, "chat_requests_per_minute").value == before + 500


class TestAPersonInSeveralOrganisationsGetsTheMostGenerous:
    def test_a_free_organisation_does_not_throttle_a_paid_teams_engineer(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,  # creates the personal organisation, which is on the free plan
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(
            monkeypatch,
            {"free": {"chat_requests_per_minute": 2}, "team": {"chat_requests_per_minute": 60}},
        )
        _billing(auth_client, organisation_id, plan="team")
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]
        assert len(organisation_ids_for(db_session, user_id)) >= 2

        resolved = limits.for_user(db_session, user_id, "chat_requests_per_minute")

        assert resolved.value == 60
        assert resolved.organisation_id == organisation_id

    def test_each_limit_takes_its_own_most_generous_organisation(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        organisation_id: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Generous for chat in one organisation and for MCP in the other: the person gets both.
        _plans(monkeypatch, {"free": {"mcp_requests_per_minute": 500}})
        _billing(auth_client, organisation_id, chat_requests_per_minute=900, mcp_requests_per_minute=1)
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]

        assert limits.for_user(db_session, user_id, "chat_requests_per_minute").value == 900
        assert limits.for_user(db_session, user_id, "mcp_requests_per_minute").value == 500

    def test_a_person_in_no_organisation_is_answered_by_the_global_setting(
        self, db_session: Session
    ) -> None:
        resolved = limits.for_user(db_session, "a-user-nobody-belongs-with", "catia_ops_per_minute")

        assert resolved.from_settings
        assert resolved.value == settings.catia_ops_per_minute
        assert resolved.organisation_id is None


class TestTheAnswerIsCachedBrieflyPerPerson:
    def _count_selects(self, db_session: Session):
        statements: list[str] = []

        def before(conn, cursor, statement, *_args):  # noqa: ANN001
            statements.append(statement)

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", before)
        return statements, lambda: event.remove(engine, "before_cursor_execute", before)

    def test_a_second_ask_costs_no_query(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]
        limits.for_user(db_session, user_id, "chat_requests_per_minute")

        statements, detach = self._count_selects(db_session)
        try:
            limits.for_user(db_session, user_id, "chat_requests_per_minute")
            limits.for_user(db_session, user_id, "mcp_requests_per_minute")
        finally:
            detach()

        assert statements == []

    def test_the_cache_lapses(
        self, auth_client: AuthenticatedTestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]
        clock = [1000.0]
        monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
        limits.forget()
        limits.for_user(db_session, user_id, "chat_requests_per_minute")

        clock[0] += limits.CACHE_SECONDS + 1
        statements, detach = self._count_selects(db_session)
        try:
            limits.for_user(db_session, user_id, "chat_requests_per_minute")
        finally:
            detach()

        assert statements, "an entry past its time must be looked up again"

    def test_forgetting_drops_every_entry(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        user_id = auth_client.get("/api/v1/auth/me").json()["id"]
        limits.for_user(db_session, user_id, "chat_requests_per_minute")

        limits.forget()
        statements, detach = self._count_selects(db_session)
        try:
            limits.for_user(db_session, user_id, "chat_requests_per_minute")
        finally:
            detach()

        assert statements


class TestARequestMeetsThePlansBudget:
    CHAT = "/api/v1/ai/chat"

    @pytest.fixture(autouse=True)
    def _a_personal_organisation(self, auth_client: AuthenticatedTestClient, project_id: str) -> None:
        """A person is on a plan only through an organisation, and the personal one is made
        with the first project. Without one the global setting answers -- which is its own test."""

    def _spend(self, client: AuthenticatedTestClient, count: int):
        # An invalid body is a 422, which still passes through the rate limit first, so no
        # provider is involved and the headers are the whole observation.
        return [client.post(self.CHAT, json={"message": 5}) for _ in range(count)]

    def test_the_budget_in_the_headers_is_the_plans(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"free": {"chat_requests_per_minute": 2}})

        (first,) = self._spend(auth_client, 1)

        assert first.headers["ratelimit-limit"] == "2"

    def test_the_third_request_on_a_two_a_minute_plan_is_refused_with_retry_after(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"free": {"chat_requests_per_minute": 2}})

        responses = self._spend(auth_client, 3)

        assert [r.status_code for r in responses][2] == 429
        assert 1 <= int(responses[2].headers["retry-after"]) <= 60
        assert responses[2].headers["ratelimit-limit"] == "2"

    def test_raising_the_plan_raises_what_the_next_request_is_told(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(
            monkeypatch,
            {"free": {"chat_requests_per_minute": 2}, "team": {"chat_requests_per_minute": 50}},
        )
        (before,) = self._spend(auth_client, 1)
        assert before.headers["ratelimit-limit"] == "2"

        _billing(auth_client, organisation_id, plan="team")
        (after,) = self._spend(auth_client, 1)

        assert after.headers["ratelimit-limit"] == "50"

    def test_two_people_on_one_plan_do_not_share_a_budget(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"free": {"chat_requests_per_minute": 1}})
        assert self._spend(auth_client, 2)[1].status_code == 429

        register_verified(auth_client, db_session, "colleague@kryova.dev")  # type: ignore[arg-type]
        signed_in = auth_client.post(
            "/api/v1/auth/login",
            data={"username": "colleague@kryova.dev", "password": "correct-horse-battery"},
        )
        assert signed_in.status_code == 200, signed_in.text
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]
        assert auth_client.post("/api/v1/projects", json={"name": "Theirs"}).status_code == 201

        (response,) = self._spend(auth_client, 1)

        # Same plan, same one-a-minute budget -- and the colleague has all of it.
        assert response.status_code != 429
        assert response.headers["ratelimit-remaining"] == "0"

    def test_the_simulation_budget_is_the_plans_too(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _plans(monkeypatch, {"free": {"simulation_requests_per_minute": 4}})

        response = auth_client.post(f"/api/v1/projects/{project_id}/simulations", json={})

        assert response.headers["ratelimit-limit"] == "4"

    def test_the_mcp_budget_is_the_plans_too(
        self, auth_client: AuthenticatedTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _plans(monkeypatch, {"free": {"mcp_requests_per_minute": 6}})

        response = auth_client.post(
            "/api/v1/mcp/conversations/none", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )

        assert response.headers["ratelimit-limit"] == "6"


class TestACallerNobodySignedInHasNoPlan:
    def test_the_global_budget_answers_and_no_database_is_asked(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _plans(monkeypatch, {"free": {"chat_requests_per_minute": 1}})

        response = client.post("/api/v1/ai/chat", json={"message": "hello"})

        # Refused for being nobody -- after the budget was counted, against the address.
        assert response.status_code == 401
        assert response.headers["ratelimit-limit"] == str(settings.chat_requests_per_minute)


class TestTheConcurrencyCeilingIsTheOrganisationsNotTheGlobalSetting:
    """The defect found beside 3.5: an override was displayed and never applied."""

    @pytest.fixture(autouse=True)
    def _waiting_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # These are about which *ceiling* applies, so a run past it must be a refusal; with
        # the waiting line on it would be accepted and held (`tests/test_simulation_waiting.py`).
        monkeypatch.setattr(settings, "max_waiting_simulations_per_user", 0)

    @staticmethod
    def _queue_jobs(client: AuthenticatedTestClient, project_id: str, count: int) -> None:
        version_id = client.get(f"/api/v1/projects/{project_id}/geometry").json()["items"][0]["id"]
        for _ in range(count):
            client.media.db.add(
                SimulationJob(
                    project_id=project_id,
                    geometry_version_id=version_id,
                    status=JobStatus.QUEUED,
                    solver="linear-static",
                    load_case=load_case(),
                )
            )
        client.media.db.flush()

    @staticmethod
    def _start(client: AuthenticatedTestClient, project_id: str):
        return client.post(
            f"/api/v1/projects/{project_id}/simulations",
            json={"load_case": load_case(), "element_size_mm": 10.0},
        )

    def test_a_raised_override_lets_a_run_through_where_the_global_setting_would_not(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 1)
        self._queue_jobs(auth_client, project_with_geometry, 1)
        assert self._start(auth_client, project_with_geometry).status_code == 429

        project = db_session.get(Project, project_with_geometry)
        assert project is not None
        _billing(auth_client, project.organisation_id, max_concurrent_simulations_per_user=5)

        # Before 3.5 this was still a 429: the override was shown on the quota page and the
        # route read the setting.
        assert self._start(auth_client, project_with_geometry).status_code == 202

    def test_a_lowered_override_refuses_where_the_global_setting_would_not(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 10)
        self._queue_jobs(auth_client, project_with_geometry, 2)
        project = db_session.get(Project, project_with_geometry)
        assert project is not None
        _billing(auth_client, project.organisation_id, max_concurrent_simulations_per_user=2)

        response = self._start(auth_client, project_with_geometry)

        assert response.status_code == 429
        assert "limit of 2" in response.json()["detail"]

    def test_a_plan_can_set_the_ceiling(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 10)
        _plans(monkeypatch, {"free": {"max_concurrent_simulations_per_user": 1}})
        self._queue_jobs(auth_client, project_with_geometry, 1)

        response = self._start(auth_client, project_with_geometry)

        assert response.status_code == 429
        assert "limit of 1" in response.json()["detail"]

    def test_the_agents_tool_applies_the_same_ceiling(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.ai.tools import ToolBox, ToolError

        monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 10)
        self._queue_jobs(auth_client, project_with_geometry, 2)
        project = db_session.get(Project, project_with_geometry)
        assert project is not None
        _billing(auth_client, project.organisation_id, max_concurrent_simulations_per_user=2)

        box = ToolBox.__new__(ToolBox)
        box.db = db_session
        box.user = db_session.get(User, project.owner_id)  # type: ignore[assignment]
        with pytest.raises(ToolError, match="which is the limit"):
            box._admit(project)
