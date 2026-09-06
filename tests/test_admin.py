"""The admin API: bounded power, and every use of it on the record (P3.2--P3.6).

The claims under test, in the order they matter:

1. To anyone who is not staff the console **does not exist** -- 404, never 403.
2. Impersonation records **both** identities, never one.
3. An impersonated write is refused unless somebody escalated the session on
   the record, and the escalation is itself audited.
4. A **refused** action is logged. This is the one a naive implementation
   drops, because nothing happened.
5. A tenant is never special-cased open for staff: an organisation's own audit
   slice answers 404 to a platform administrator who is not a member of it.

Every guard is checked by breaking what it guards -- the staff grant is revoked
and the same request stops working, the session is escalated and the same POST
starts working -- rather than by watching it hold once.
"""

from datetime import datetime, timedelta, timezone
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.rate_limit import auth_limiter
from app.jobs import JobQueue, get_job_queue
from app.main import app
from app.models import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Project,
    SimulationJob,
    StaffGrant,
    StaffRole,
)
from tests.typing import AuthenticatedTestClient

API = "/api/v1"

#: Every route under `/admin` an ordinary account might stumble onto. Each one
#: has to be invisible, not forbidden.
ADMIN_ROUTES = [
    "/admin/whoami",
    "/admin/organisations",
    "/admin/users",
    "/admin/jobs",
    "/admin/audit",
    "/admin/audit/verify",
    "/admin/impersonation",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sign_in(client: AuthenticatedTestClient):
    """Register and sign in another account against the same app and session.

    A client of its own per account, because cookies are per-client and sharing
    one would make "the staff member" and "the customer" the same browser.
    """

    def _sign_in(email: str, password: str = "correct-horse-battery") -> AuthenticatedTestClient:
        auth_limiter.reset()
        peer = cast(AuthenticatedTestClient, TestClient(app))
        registered = peer.post(f"{API}/auth/register", json={"email": email, "password": password})
        assert registered.status_code == 201, registered.text
        signed_in = peer.post(
            f"{API}/auth/login", data={"username": email, "password": password}
        )
        assert signed_in.status_code == 200, signed_in.text
        peer.headers["x-csrf-token"] = peer.cookies["kryova_csrf"]
        return peer

    return _sign_in


def _user_id(peer: AuthenticatedTestClient) -> str:
    return peer.get(f"{API}/auth/me").json()["id"]


@pytest.fixture
def make_staff(db_session: Session):
    """Grant staff standing directly, because there is no API that does.

    That absence is deliberate (see `StaffGrant`): an endpoint that mints staff
    is an escalation path out of whichever staff account is compromised first.
    """

    def _make_staff(user_id: str, role: StaffRole) -> StaffGrant:
        grant = StaffGrant(user_id=user_id, role=role, reason="test fixture")
        db_session.add(grant)
        db_session.flush()
        return grant

    return _make_staff


@pytest.fixture
def customer(sign_in) -> AuthenticatedTestClient:
    return sign_in("customer@example.com")


@pytest.fixture
def support(sign_in, make_staff) -> AuthenticatedTestClient:
    peer = sign_in("support@kryova.dev")
    make_staff(_user_id(peer), StaffRole.SUPPORT)
    return peer


@pytest.fixture
def operator(sign_in, make_staff) -> AuthenticatedTestClient:
    peer = sign_in("operator@kryova.dev")
    make_staff(_user_id(peer), StaffRole.OPERATOR)
    return peer


@pytest.fixture
def platform_admin(sign_in, make_staff) -> AuthenticatedTestClient:
    peer = sign_in("admin@kryova.dev")
    make_staff(_user_id(peer), StaffRole.PLATFORM_ADMIN)
    return peer


class _RecordingQueue(JobQueue):
    """Swallow the submitted job instead of meshing and solving it.

    `InlineJobQueue` -- what the suite normally installs -- would run the real
    simulation on the request thread, fail for want of a blob, and mark the job
    it had just re-queued as failed again. What is under test here is the
    console's decision and its audit entry, not the solver.
    """

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, job) -> None:
        self.submitted += 1


@pytest.fixture
def quiet_queue():
    queue = _RecordingQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    yield queue
    # `client` clears the whole override map at teardown; this only puts the
    # inline queue back for anything later in the same test.
    app.dependency_overrides.pop(get_job_queue, None)


@pytest.fixture
def failed_job(customer: AuthenticatedTestClient, db_session: Session) -> SimulationJob:
    """A job that ran and failed, in the customer's own organisation."""
    owner_id = _user_id(customer)
    created = customer.post(f"{API}/projects", json={"name": "Press frame"})
    assert created.status_code == 201, created.text
    project = db_session.get(Project, created.json()["id"])
    assert project is not None

    media = Media(
        owner_id=owner_id,
        kind=MediaKind.CAD,
        filename="frame.stl",
        size_bytes=1024,
        sha256="0" * 64,
        meta={},
    )
    db_session.add(media)
    db_session.flush()
    geometry = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename="frame.stl",
        file_format="stl",
        stats={},
    )
    db_session.add(geometry)
    db_session.flush()
    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=JobStatus.FAILED,
        solver="mock",
        load_case={},
        error="Solver ran out of memory",
    )
    db_session.add(job)
    db_session.flush()
    return job


def _events(db_session: Session, action: AuditAction) -> list[AuditEvent]:
    return list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.sequence)
        )
    )


# ---------------------------------------------------------------------------


class TestTheConsoleIsInvisibleToEveryoneElse:
    @pytest.mark.parametrize("route", ADMIN_ROUTES)
    def test_a_signed_in_non_staff_user_gets_404(
        self, customer: AuthenticatedTestClient, route: str
    ) -> None:
        response = customer.get(f"{API}{route}")
        assert response.status_code == 404, response.text
        # And the body says nothing about what is there. "Forbidden" would be a
        # map: it confirms the path exists and only the account is wrong.
        assert response.json()["detail"] == "Not found"

    @pytest.mark.parametrize("route", ADMIN_ROUTES)
    def test_an_anonymous_request_gets_401(
        self, client: AuthenticatedTestClient, route: str
    ) -> None:
        assert client.get(f"{API}{route}").status_code == 401

    def test_revoking_the_grant_closes_the_door_again(
        self, support: AuthenticatedTestClient, db_session: Session
    ) -> None:
        """Break the guard: the same request, with and without the grant.

        Without this the 404s above would also pass against a console that is
        simply broken for everybody.
        """
        assert support.get(f"{API}/admin/whoami").status_code == 200

        grant = db_session.scalars(select(StaffGrant)).one()
        grant.revoked_at = datetime.now(timezone.utc)
        db_session.flush()

        assert support.get(f"{API}/admin/whoami").status_code == 404

    def test_the_console_reports_the_role_it_will_draw(
        self, platform_admin: AuthenticatedTestClient
    ) -> None:
        body = platform_admin.get(f"{API}/admin/whoami").json()
        assert body["role"] == "platform_admin"
        assert body["email"] == "admin@kryova.dev"


class TestTheStaffLadder:
    def test_support_cannot_reach_an_operator_route(
        self, support: AuthenticatedTestClient, failed_job: SimulationJob
    ) -> None:
        response = support.post(f"{API}/admin/jobs/{failed_job.id}/retry")
        assert response.status_code == 404, response.text

    def test_an_operator_can(
        self,
        operator: AuthenticatedTestClient,
        failed_job: SimulationJob,
        quiet_queue: _RecordingQueue,
    ) -> None:
        response = operator.post(f"{API}/admin/jobs/{failed_job.id}/retry")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "queued"
        assert quiet_queue.submitted == 1

    def test_support_cannot_escalate_an_impersonation_session(
        self, support: AuthenticatedTestClient, customer: AuthenticatedTestClient
    ) -> None:
        started = support.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": _user_id(customer), "reason": "ticket 41, cannot upload"},
        )
        assert started.status_code == 201, started.text
        session_id = started.json()["id"]

        refused = support.post(
            f"{API}/admin/impersonation/{session_id}/escalate",
            json={"reason": "I would like to fix it myself"},
        )
        assert refused.status_code == 404, refused.text


class TestImpersonationRecordsBothIdentities:
    def test_starting_a_session_names_the_staff_member_and_the_subject(
        self,
        support: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        actor_id, subject_id = _user_id(support), _user_id(customer)
        started = support.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": subject_id, "reason": "ticket 41, cannot upload"},
        )
        assert started.status_code == 201, started.text
        assert started.json()["mode"] == "read", "read-only is the default, not an option"
        assert started.json()["token"]

        entry = _events(db_session, AuditAction.IMPERSONATION_STARTED)[-1]
        assert entry.actor_user_id == actor_id
        assert entry.subject_user_id == subject_id
        assert entry.actor_user_id != entry.subject_user_id
        assert entry.reason == "ticket 41, cannot upload"
        assert entry.outcome is AuditOutcome.SUCCEEDED

    def test_every_impersonated_request_carries_the_staff_actor(
        self,
        support: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        actor_id, subject_id = _user_id(support), _user_id(customer)
        token = _start_impersonation(support, subject_id)
        seat = _seat(token)

        listed = seat.get(f"{API}/projects")
        assert listed.status_code == 200, listed.text

        entry = _events(db_session, AuditAction.IMPERSONATED_REQUEST)[-1]
        assert entry.actor_user_id == actor_id
        assert entry.subject_user_id == subject_id
        assert entry.impersonated is True
        # PERMITTED, not SUCCEEDED: the row says the door was opened.
        assert entry.outcome is AuditOutcome.PERMITTED
        assert entry.detail == {"method": "GET", "path": f"{API}/projects"}

    def test_the_seat_is_the_subjects_and_grants_the_staff_member_nothing_extra(
        self,
        support: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
    ) -> None:
        """Impersonation composes with the tenancy guards rather than bypassing
        them: the request runs as the subject, so it sees the subject's
        organisations -- not the staff member's, and not everyone's."""
        created = customer.post(f"{API}/projects", json={"name": "Customer part"})
        assert created.status_code == 201

        seat = _seat(_start_impersonation(support, _user_id(customer)))
        assert [item["name"] for item in seat.get(f"{API}/projects").json()["items"]] == [
            "Customer part"
        ]
        # And the console itself is closed to that token: a staff member sitting
        # in a customer's seat is doing the customer's work.
        assert seat.get(f"{API}/admin/whoami").status_code == 404


class TestReadOnlyIsTheDefault:
    def test_an_impersonated_write_is_refused_and_recorded(
        self,
        support: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        actor_id, subject_id = _user_id(support), _user_id(customer)
        seat = _seat(_start_impersonation(support, subject_id))

        refused = seat.post(f"{API}/projects", json={"name": "Not yours to make"})
        assert refused.status_code == 403, refused.text
        assert "read-only" in refused.json()["detail"]

        # Nothing was created.
        assert (
            db_session.scalar(select(Project).where(Project.name == "Not yours to make")) is None
        )
        # And the refusal is on the record, with both identities -- this is the
        # entry the whole read-only default exists to produce.
        entry = _events(db_session, AuditAction.IMPERSONATED_WRITE_REFUSED)[-1]
        assert entry.outcome is AuditOutcome.REFUSED
        assert entry.actor_user_id == actor_id
        assert entry.subject_user_id == subject_id
        assert entry.detail == {"method": "POST", "path": f"{API}/projects"}

    def test_escalation_is_audited_and_then_the_same_write_succeeds(
        self,
        platform_admin: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        """Break the guard the sanctioned way, and watch it open.

        The identical POST is refused before the escalation and accepted after
        it, with nothing else changed -- which is what makes the refusal
        attributable to the read-only default rather than to anything else about
        the request.
        """
        actor_id, subject_id = _user_id(platform_admin), _user_id(customer)
        started = platform_admin.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": subject_id, "reason": "ticket 41, cannot upload"},
        ).json()
        seat = _seat(started["token"])

        assert seat.post(f"{API}/projects", json={"name": "Repair"}).status_code == 403

        escalated = platform_admin.post(
            f"{API}/admin/impersonation/{started['id']}/escalate",
            json={"reason": "re-uploading the geometry the customer lost"},
        )
        assert escalated.status_code == 200, escalated.text
        assert escalated.json()["mode"] == "write"

        entry = _events(db_session, AuditAction.IMPERSONATION_ESCALATED)[-1]
        assert entry.outcome is AuditOutcome.SUCCEEDED
        assert entry.actor_user_id == actor_id
        assert entry.subject_user_id == subject_id
        assert entry.reason == "re-uploading the geometry the customer lost"
        # The reason the session was *opened* is kept too: a look and a change
        # are different decisions and the log holds both justifications.
        assert entry.detail["opened_because"] == "ticket 41, cannot upload"

        # Same token, same request. Write mode lives on the session row, which
        # is why no new token had to be issued.
        allowed = seat.post(f"{API}/projects", json={"name": "Repair"})
        assert allowed.status_code == 201, allowed.text

    def test_escalation_never_extends_the_deadline(
        self, platform_admin: AuthenticatedTestClient, customer: AuthenticatedTestClient
    ) -> None:
        started = platform_admin.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": _user_id(customer), "reason": "ticket 41, cannot upload"},
        ).json()
        escalated = platform_admin.post(
            f"{API}/admin/impersonation/{started['id']}/escalate",
            json={"reason": "re-uploading the geometry the customer lost"},
        ).json()
        assert escalated["expires_at"] < started["expires_at"]

    def test_ending_a_session_stops_the_token_at_once(
        self,
        platform_admin: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        started = platform_admin.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": _user_id(customer), "reason": "ticket 41, cannot upload"},
        ).json()
        seat = _seat(started["token"])
        assert seat.get(f"{API}/projects").status_code == 200

        ended = platform_admin.post(f"{API}/admin/impersonation/{started['id']}/end")
        assert ended.status_code == 200, ended.text

        # The token is unchanged and unexpired, and no longer works: authority
        # is read from the session row on every request.
        assert seat.get(f"{API}/projects").status_code == 401
        assert _events(db_session, AuditAction.IMPERSONATION_ENDED)[-1].subject_user_id == (
            _user_id(customer)
        )


class TestImpersonationRefusalsAreRecorded:
    def test_impersonating_a_colleague_is_refused_and_logged(
        self,
        support: AuthenticatedTestClient,
        operator: AuthenticatedTestClient,
        db_session: Session,
    ) -> None:
        """The escalation path this closes: support borrows operator's power.

        The refusal is the interesting row -- "who tried to impersonate whom and
        was stopped" is the first question after a support account is
        compromised.
        """
        response = support.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": _user_id(operator), "reason": "just having a look"},
        )
        assert response.status_code == 409, response.text

        entry = _events(db_session, AuditAction.IMPERSONATION_STARTED)[-1]
        assert entry.outcome is AuditOutcome.REFUSED
        assert entry.actor_user_id == _user_id(support)
        assert entry.target_id == _user_id(operator)
        assert entry.detail["refused_because"] == "the subject holds a staff grant"

    def test_an_unknown_subject_is_404_and_still_logged(
        self, support: AuthenticatedTestClient, db_session: Session
    ) -> None:
        response = support.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": "no-such-user", "reason": "fishing expedition"},
        )
        assert response.status_code == 404
        entry = _events(db_session, AuditAction.IMPERSONATION_STARTED)[-1]
        assert entry.outcome is AuditOutcome.REFUSED
        assert entry.target_id == "no-such-user"


class TestJobOperationsAreAuditedEitherWay:
    def test_a_retry_is_recorded_against_the_customers_organisation(
        self,
        operator: AuthenticatedTestClient,
        failed_job: SimulationJob,
        db_session: Session,
        quiet_queue: _RecordingQueue,
    ) -> None:
        assert operator.post(f"{API}/admin/jobs/{failed_job.id}/retry").status_code == 200
        entry = _events(db_session, AuditAction.JOB_RETRIED)[-1]
        assert entry.outcome is AuditOutcome.SUCCEEDED
        assert entry.target_id == failed_job.id
        project = db_session.get(Project, failed_job.project_id)
        assert entry.organisation_id == project.organisation_id
        assert entry.detail == {"previous_status": "failed"}

    def test_a_refused_retry_is_recorded_too(
        self, operator: AuthenticatedTestClient, failed_job: SimulationJob, db_session: Session
    ) -> None:
        """The log survives the action failing. "An operator tried to re-run a
        finished job at 03:12" is the line that explains a duplicate result."""
        failed_job.status = JobStatus.SUCCEEDED
        db_session.flush()

        response = operator.post(f"{API}/admin/jobs/{failed_job.id}/retry")
        assert response.status_code == 409, response.text

        entry = _events(db_session, AuditAction.JOB_RETRIED)[-1]
        assert entry.outcome is AuditOutcome.REFUSED
        assert entry.target_id == failed_job.id
        assert "succeeded" in (entry.reason or "")
        # And the job is untouched.
        db_session.refresh(failed_job)
        assert failed_job.status is JobStatus.SUCCEEDED

    def test_failing_a_job_writes_the_reason_to_the_row_and_the_log(
        self, operator: AuthenticatedTestClient, failed_job: SimulationJob, db_session: Session
    ) -> None:
        failed_job.status = JobStatus.RUNNING
        db_session.flush()

        response = operator.post(
            f"{API}/admin/jobs/{failed_job.id}/fail",
            json={"reason": "the worker was lost in a restart"},
        )
        assert response.status_code == 200, response.text
        db_session.refresh(failed_job)
        assert failed_job.status is JobStatus.FAILED
        assert "the worker was lost in a restart" in (failed_job.error or "")

        entry = _events(db_session, AuditAction.JOB_FAILED)[-1]
        assert entry.outcome is AuditOutcome.SUCCEEDED
        assert entry.reason == "the worker was lost in a restart"


class TestTheConsoleReads:
    def test_organisations_and_users_are_listed_with_their_counts(
        self, support: AuthenticatedTestClient, customer: AuthenticatedTestClient
    ) -> None:
        assert customer.post(f"{API}/projects", json={"name": "Gearbox"}).status_code == 201

        organisations = support.get(f"{API}/admin/organisations").json()
        assert organisations["total"] >= 1
        mine = [row for row in organisations["items"] if row["project_count"] == 1]
        assert mine and mine[0]["member_count"] == 1

        users = support.get(f"{API}/admin/users", params={"q": "customer"}).json()
        assert [row["email"] for row in users["items"]] == ["customer@example.com"]
        assert users["items"][0]["staff_role"] is None

    def test_usage_says_how_each_number_was_obtained(
        self, support: AuthenticatedTestClient, failed_job: SimulationJob, db_session: Session
    ) -> None:
        project = db_session.get(Project, failed_job.project_id)
        usage = support.get(
            f"{API}/admin/organisations/{project.organisation_id}/usage"
        ).json()
        assert usage["projects"] == 1
        assert usage["geometry_versions"] == 1
        assert usage["jobs_by_status"]["failed"] == 1
        assert usage["storage_bytes"] == 1024
        # Honesty conventions: an attributed number and a global limit both say
        # what they are rather than looking like measurements of this tenant.
        assert "media owned by" in usage["storage_attribution"]
        assert "not implemented" in usage["quota_source"]

    def test_an_organisation_that_does_not_exist_is_404_for_staff_too(
        self, support: AuthenticatedTestClient
    ) -> None:
        assert support.get(f"{API}/admin/organisations/nope").status_code == 404
        assert support.get(f"{API}/admin/organisations/nope/usage").status_code == 404

    def test_the_chain_verifies_through_the_api(
        self, support: AuthenticatedTestClient, customer: AuthenticatedTestClient
    ) -> None:
        support.post(
            f"{API}/admin/impersonation",
            json={"subject_user_id": _user_id(customer), "reason": "ticket 41, cannot upload"},
        )
        body = support.get(f"{API}/admin/audit/verify").json()
        assert body["intact"] is True
        assert body["checked"] >= 1
        assert "intact" in body["summary"]


class TestAnOrganisationsOwnSlice:
    def test_the_owner_sees_their_organisations_rows(
        self,
        operator: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        failed_job: SimulationJob,
        db_session: Session,
        quiet_queue: _RecordingQueue,
    ) -> None:
        project = db_session.get(Project, failed_job.project_id)
        assert operator.post(f"{API}/admin/jobs/{failed_job.id}/retry").status_code == 200

        slice_ = customer.get(f"{API}/organisations/{project.organisation_id}/audit")
        assert slice_.status_code == 200, slice_.text
        actions = {row["action"] for row in slice_.json()["items"]}
        assert "job.retried" in actions
        # Platform events carry no tenant and belong in nobody's slice.
        assert all(
            row["organisation_id"] == project.organisation_id for row in slice_.json()["items"]
        )

    def test_a_stranger_gets_404(
        self,
        customer: AuthenticatedTestClient,
        sign_in,
        failed_job: SimulationJob,
        db_session: Session,
    ) -> None:
        project = db_session.get(Project, failed_job.project_id)
        stranger = sign_in("stranger@example.com")
        response = stranger.get(f"{API}/organisations/{project.organisation_id}/audit")
        assert response.status_code == 404, response.text

    def test_a_platform_administrator_who_is_not_a_member_gets_404_as_well(
        self,
        platform_admin: AuthenticatedTestClient,
        customer: AuthenticatedTestClient,
        failed_job: SimulationJob,
        db_session: Session,
    ) -> None:
        """Requirement 5, exactly: staff standing is not a membership.

        The tenant slice never reads a staff grant, so there is no branch that
        could special-case an administrator into a 200. If they need to see it,
        they read `/admin/audit`, where the reading is itself bounded and the
        route is not pretending to be the customer's own view.
        """
        project = db_session.get(Project, failed_job.project_id)
        response = platform_admin.get(f"{API}/organisations/{project.organisation_id}/audit")
        assert response.status_code == 404, response.text

        # ...and the same administrator can see it on the platform surface.
        platform = platform_admin.get(
            f"{API}/admin/audit", params={"organisation_id": project.organisation_id}
        )
        assert platform.status_code == 200, platform.text


class TestTheAuditTokenIsNotAnAccessToken:
    def test_an_access_token_is_not_accepted_as_an_impersonation_token(
        self, customer: AuthenticatedTestClient
    ) -> None:
        from app.core.security import create_impersonation_token, decode_access_token

        token = create_impersonation_token(
            actor_id="a",
            subject_id="b",
            session_id="c",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        # The `type` claim is what stops an impersonation token being a silent
        # login as the subject with the actor's name dropped.
        assert decode_access_token(token) is None

    def test_a_forged_session_id_is_refused(
        self, support: AuthenticatedTestClient, customer: AuthenticatedTestClient
    ) -> None:
        from app.core.security import create_impersonation_token

        forged = create_impersonation_token(
            actor_id=_user_id(support),
            subject_id=_user_id(customer),
            session_id="00000000-0000-0000-0000-000000000000",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        assert _seat(forged).get(f"{API}/projects").status_code == 401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_impersonation(
    staff_client: AuthenticatedTestClient, subject_id: str, reason: str = "ticket 41, cannot upload"
) -> str:
    started = staff_client.post(
        f"{API}/admin/impersonation", json={"subject_user_id": subject_id, "reason": reason}
    )
    assert started.status_code == 201, started.text
    return started.json()["token"]


def _seat(token: str) -> AuthenticatedTestClient:
    """A client carrying only the impersonation token.

    A bearer header rather than a cookie, which is also what the frontend will
    do: it keeps the staff member's own session and the borrowed one in
    different places, so neither can be used by accident for the other.
    """
    peer = cast(AuthenticatedTestClient, TestClient(app))
    peer.headers["authorization"] = f"Bearer {token}"
    return peer
