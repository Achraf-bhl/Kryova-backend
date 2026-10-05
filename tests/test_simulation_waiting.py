"""A run past the user's ceiling waits instead of being refused (ROAD_TO_10 3.4).

What has to hold, because each half is the reason for the next:

* the run past the ceiling is **accepted and held**, shown with its place in line, and
  **not handed to the job queue** -- a waiting run costs no thread and no meshing;
* only a full line is refused, naming both numbers, and `max_waiting... = 0` is the old
  plain refusal;
* **a slot is handed on by whatever ended the run** -- success, failure, cancellation or a
  crash in the runner itself -- and exactly as many runs move as there are free slots;
* **two workers cannot promote one run twice**, and a run cancelled while waiting is not
  resurrected by a promotion that read it a moment earlier;
* the line is first in, first out across a user's projects, and another user's runs are not in it;
* a restart does not strand the line: `WAITING` survives it and is resumed;
* the agent is told the truth about a held run, and a held run counts as in flight.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core import limits
from app.core.config import settings
from app.core.security import hash_password
from app.jobs import InlineJobQueue, get_job_queue
from app.jobs.queue import JobQueue
from app.models import (
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Project,
    SimulationJob,
    StaffGrant,
    StaffRole,
    User,
)
from app.simulation import waiting
from tests.test_agent import LOAD_CASE
from tests.test_mesh import box_stl
from tests.test_simulations import BOX, load_case
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


# ---------------------------------------------------------------------------
# Doubles. The runner is replaced by a recorder that obeys the one rule the real runner
# has (a job that is not QUEUED is skipped) and ends the job as the test says, so what is
# being measured is the hand-on and not a mesh.
# ---------------------------------------------------------------------------


class HoldingQueue(JobQueue):
    """Keeps what it is given, so a test decides when a run 'happens'."""

    def __init__(self) -> None:
        self.pending: list[Any] = []

    def submit(self, job: Any) -> None:
        self.pending.append(job)

    def run_next(self) -> None:
        self.pending.pop(0)()

    def run_all(self) -> None:
        while self.pending:
            self.run_next()


class FakeRunner:
    def __init__(self, outcome: JobStatus = JobStatus.SUCCEEDED, *, raises: bool = False) -> None:
        self.outcome = outcome
        self.raises = raises
        self.ran: list[str] = []

    def __call__(self, job_id: str, session_scope: Any, store: Any, solver: Any = None) -> None:
        with session_scope() as db:
            job = db.get(SimulationJob, job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                return
            job.status = self.outcome
            db.commit()
        self.ran.append(job_id)
        if self.raises:
            raise RuntimeError("the runner itself broke")


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    fake = FakeRunner()
    monkeypatch.setattr(waiting, "run_simulation", fake)
    return fake


@pytest.fixture
def queue() -> HoldingQueue:
    return HoldingQueue()


def scope_for(db: Session):
    return lambda: nullcontext(db)


@pytest.fixture(autouse=True)
def _a_one_slot_line_of_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 1)
    monkeypatch.setattr(settings, "max_waiting_simulations_per_user", 3)
    limits.forget()


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db_session: Session) -> User:
    account = User(email="owner@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


def make_project(db: Session, owner: User, name: str = "Bracket") -> Project:
    row = Project(name=name, owner_id=owner.id)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def project(db_session: Session, owner: User) -> Project:
    return make_project(db_session, owner)


def make_geometry(db: Session, project: Project, owner: User) -> GeometryVersion:
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename="bracket.stl",
        content_type="model/stl",
        size_bytes=128,
        sha256="0" * 64,
    )
    db.add(media)
    db.flush()
    version = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename="bracket.stl",
        file_format="stl",
        stats={"bounding_box": {"min": [0, 0, 0], "max": [10, 20, 5]}},
    )
    db.add(version)
    db.flush()
    return version


@pytest.fixture
def geometry(db_session: Session, project: Project, owner: User) -> GeometryVersion:
    return make_geometry(db_session, project, owner)


def make_job(
    db: Session, project: Project, geometry: GeometryVersion, status: JobStatus
) -> SimulationJob:
    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=status,
        solver="linear-static",
        load_case=LOAD_CASE,
    )
    db.add(job)
    db.flush()
    return job


def statuses(db: Session, *jobs: SimulationJob) -> list[JobStatus]:
    for job in jobs:
        db.refresh(job)
    return [job.status for job in jobs]


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


class TestAdmission:
    def test_a_free_slot_starts_the_run_now(
        self, db_session: Session, owner: User, project: Project
    ) -> None:
        admission = waiting.admit(db_session, owner.id, project.organisation_id)

        assert admission.status is JobStatus.QUEUED
        assert admission.position is None

    def test_no_free_slot_but_room_in_the_line_means_waiting_at_the_next_place(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        make_job(db_session, project, geometry, JobStatus.RUNNING)

        first = waiting.admit(db_session, owner.id, project.organisation_id)
        make_job(db_session, project, geometry, JobStatus.WAITING)
        second = waiting.admit(db_session, owner.id, project.organisation_id)

        assert (first.status, first.position) == (JobStatus.WAITING, 1)
        assert (second.status, second.position) == (JobStatus.WAITING, 2)

    def test_a_full_line_is_refused_naming_both_numbers(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        make_job(db_session, project, geometry, JobStatus.RUNNING)
        for _ in range(3):
            make_job(db_session, project, geometry, JobStatus.WAITING)

        with pytest.raises(waiting.Refused) as refused:
            waiting.admit(db_session, owner.id, project.organisation_id)

        message = str(refused.value)
        assert "limit of 1" in message
        assert "3 more waiting" in message
        assert "limit of 3" in message

    def test_a_line_of_zero_is_the_plain_refusal_and_does_not_mention_waiting(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_waiting_simulations_per_user", 0)
        make_job(db_session, project, geometry, JobStatus.QUEUED)

        with pytest.raises(waiting.Refused) as refused:
            waiting.admit(db_session, owner.id, project.organisation_id)

        assert "Wait for one to finish" in str(refused.value)
        assert "waiting" not in str(refused.value)

    def test_a_waiting_run_does_not_hold_a_slot(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        make_job(db_session, project, geometry, JobStatus.WAITING)

        # Only the line is occupied; the slot is free, so this one starts.
        assert waiting.admit(db_session, owner.id, project.organisation_id).status is JobStatus.QUEUED

    def test_the_ceiling_counts_across_the_owners_projects(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        other = make_project(db_session, owner, "Second")
        make_job(db_session, other, make_geometry(db_session, other, owner), JobStatus.RUNNING)

        assert waiting.admit(db_session, owner.id, project.organisation_id).status is JobStatus.WAITING

    def test_another_users_runs_are_not_in_this_users_line(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        stranger = User(email="stranger@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = make_project(db_session, stranger, "Theirs")
        busy = make_job(db_session, theirs, make_geometry(db_session, theirs, stranger), JobStatus.RUNNING)

        assert waiting.admit(db_session, owner.id, project.organisation_id).status is JobStatus.QUEUED
        assert busy.status is JobStatus.RUNNING

    def test_another_users_full_line_is_not_this_users_line(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        stranger = User(email="s4@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = make_project(db_session, stranger, "Theirs")
        theirs_geometry = make_geometry(db_session, theirs, stranger)
        for _ in range(3):
            make_job(db_session, theirs, theirs_geometry, JobStatus.WAITING)
        make_job(db_session, project, geometry, JobStatus.RUNNING)

        # Three people's worth of waiting would refuse this one if the line were shared.
        admission = waiting.admit(db_session, owner.id, project.organisation_id)

        assert (admission.status, admission.position) == (JobStatus.WAITING, 1)

    def test_the_organisations_override_decides_the_ceiling(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.models.billing import BillingAccount

        make_job(db_session, project, geometry, JobStatus.RUNNING)
        db_session.add(
            BillingAccount(
                organisation_id=project.organisation_id,
                max_concurrent_simulations_per_user=2,
                max_waiting_simulations_per_user=0,
            )
        )
        db_session.flush()

        assert waiting.admit(db_session, owner.id, project.organisation_id).status is JobStatus.QUEUED
        make_job(db_session, project, geometry, JobStatus.RUNNING)
        with pytest.raises(waiting.Refused):
            waiting.admit(db_session, owner.id, project.organisation_id)


class TestTheOwnersLine:
    """Count-then-insert is a race between two requests, so it is serialised per owner."""

    def _statements(self, db: Session) -> tuple[list[str], Any]:
        from sqlalchemy import event

        seen: list[str] = []

        def record(conn, cursor, statement, *_rest):  # noqa: ANN001
            seen.append(statement)

        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        return seen, lambda: event.remove(engine, "before_cursor_execute", record)

    @pytest.mark.skipif(
        "postgresql" not in settings.database_url, reason="advisory locks are Postgres's"
    )
    def test_admission_and_promotion_each_take_the_owners_advisory_lock(
        self, db_session: Session, owner: User, project: Project
    ) -> None:
        seen, detach = self._statements(db_session)
        try:
            waiting.admit(db_session, owner.id, project.organisation_id)
            admitted = [s for s in seen if "pg_advisory_xact_lock" in s]
            waiting.promote(db_session, owner.id)
            promoted = [s for s in seen if "pg_advisory_xact_lock" in s]
        finally:
            detach()

        assert len(admitted) == 1
        assert len(promoted) == 2

    def test_the_lock_is_a_no_op_where_the_database_has_none(self, owner: User) -> None:
        class NoLocks:
            class dialect:  # noqa: N801
                name = "sqlite"

        class Session_:
            def get_bind(self) -> Any:
                return NoLocks()

            def execute(self, *_args: Any, **_kwargs: Any) -> None:
                raise AssertionError("took a lock the database does not have")

        waiting.lock_owner(Session_(), owner.id)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Where a waiting run stands
# ---------------------------------------------------------------------------


class TestPositions:
    def test_places_follow_the_order_the_runs_arrived_in(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        first = make_job(db_session, project, geometry, JobStatus.WAITING)
        second = make_job(db_session, project, geometry, JobStatus.WAITING)
        third = make_job(db_session, project, geometry, JobStatus.WAITING)

        found = waiting.positions(db_session, owner.id, [third.id, first.id, second.id])

        assert found == {first.id: 1, second.id: 2, third.id: 3}

    def test_a_run_that_is_not_waiting_has_no_place(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        running = make_job(db_session, project, geometry, JobStatus.RUNNING)
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        assert waiting.positions(db_session, owner.id, [running.id, held.id]) == {held.id: 1}

    def test_the_rank_is_over_the_whole_line_not_over_the_page_asked_about(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        # A page of one project's runs says nothing about another project's runs ahead of them.
        other = make_project(db_session, owner, "Second")
        ahead = make_job(db_session, other, make_geometry(db_session, other, owner), JobStatus.WAITING)
        mine = make_job(db_session, project, geometry, JobStatus.WAITING)

        assert waiting.positions(db_session, owner.id, [mine.id]) == {mine.id: 2}
        assert ahead.status is JobStatus.WAITING

    def test_another_users_waiting_runs_are_not_counted(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        stranger = User(email="s2@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = make_project(db_session, stranger, "Theirs")
        make_job(db_session, theirs, make_geometry(db_session, theirs, stranger), JobStatus.WAITING)
        mine = make_job(db_session, project, geometry, JobStatus.WAITING)

        assert waiting.positions(db_session, owner.id, [mine.id]) == {mine.id: 1}

    def test_annotating_sets_the_place_and_clears_it_on_the_rest(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        held = make_job(db_session, project, geometry, JobStatus.WAITING)
        done = make_job(db_session, project, geometry, JobStatus.SUCCEEDED)
        done.queue_position = 9  # a stale value must not survive

        waiting.annotate(db_session, owner.id, [held, done])

        assert (held.queue_position, done.queue_position) == (1, None)

    def test_no_ids_is_no_query_and_no_answer(self, db_session: Session, owner: User) -> None:
        assert waiting.positions(db_session, owner.id, []) == {}


# ---------------------------------------------------------------------------
# A slot is handed on
# ---------------------------------------------------------------------------


class TestASlotIsHandedOn:
    def _line(
        self, db: Session, project: Project, geometry: GeometryVersion, count: int = 2
    ) -> tuple[SimulationJob, list[SimulationJob]]:
        holder = make_job(db, project, geometry, JobStatus.QUEUED)
        return holder, [make_job(db, project, geometry, JobStatus.WAITING) for _ in range(count)]

    @pytest.mark.parametrize(
        "outcome", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
    )
    def test_the_end_of_a_run_however_it_ended_promotes_the_next_one(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        monkeypatch: pytest.MonkeyPatch,
        outcome: JobStatus,
    ) -> None:
        runner = FakeRunner(outcome)
        monkeypatch.setattr(waiting, "run_simulation", runner)
        holder, (first, second) = self._line(db_session, project, geometry)

        waiting.start(queue, holder.id, scope_for(db_session), object())
        queue.run_next()

        assert statuses(db_session, holder, first, second) == [
            outcome,
            JobStatus.QUEUED,
            JobStatus.WAITING,
        ]
        assert len(queue.pending) == 1, "the promoted run, and only it, was handed to the queue"

    def test_a_crash_in_the_runner_itself_still_hands_the_slot_on(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(waiting, "run_simulation", FakeRunner(JobStatus.FAILED, raises=True))
        holder, (first, _second) = self._line(db_session, project, geometry)

        waiting.start(queue, holder.id, scope_for(db_session), object())
        with pytest.raises(RuntimeError, match="the runner itself broke"):
            queue.run_next()

        # The run's own failure is what the caller sees, and the line moved anyway.
        assert statuses(db_session, first) == [JobStatus.QUEUED]

    def test_the_whole_line_drains_in_order_one_at_a_time(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        runner: FakeRunner,
    ) -> None:
        holder, (first, second) = self._line(db_session, project, geometry)

        waiting.start(queue, holder.id, scope_for(db_session), object())
        queue.run_all()

        assert runner.ran == [holder.id, first.id, second.id]
        assert statuses(db_session, holder, first, second) == [JobStatus.SUCCEEDED] * 3

    def test_exactly_as_many_move_as_there_are_free_slots(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_concurrent_simulations_per_user", 3)
        limits.forget()
        holders = [make_job(db_session, project, geometry, JobStatus.RUNNING) for _ in range(3)]
        line = [make_job(db_session, project, geometry, JobStatus.WAITING) for _ in range(3)]

        # One ends: one slot is free, so one run moves -- not all three.
        holders[0].status = JobStatus.SUCCEEDED
        db_session.flush()
        waiting.hand_on(queue, scope_for(db_session), object(), owner.id)

        assert statuses(db_session, *line) == [JobStatus.QUEUED, JobStatus.WAITING, JobStatus.WAITING]

    def test_nothing_moves_while_every_slot_is_still_held(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
    ) -> None:
        make_job(db_session, project, geometry, JobStatus.RUNNING)
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        waiting.hand_on(queue, scope_for(db_session), object(), owner.id)

        assert statuses(db_session, held) == [JobStatus.WAITING]
        assert queue.pending == []

    def test_the_line_is_first_in_first_out_across_projects(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        other = make_project(db_session, owner, "Second")
        older = make_job(db_session, other, make_geometry(db_session, other, owner), JobStatus.WAITING)
        newer = make_job(db_session, project, geometry, JobStatus.WAITING)

        promoted = waiting.promote(db_session, owner.id)

        assert promoted == [older.id]
        assert statuses(db_session, older, newer) == [JobStatus.QUEUED, JobStatus.WAITING]

    def test_another_users_slot_is_not_this_users_to_take(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        stranger = User(email="s3@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add(stranger)
        db_session.flush()
        theirs = make_project(db_session, stranger, "Theirs")
        theirs_geometry = make_geometry(db_session, theirs, stranger)
        make_job(db_session, project, geometry, JobStatus.RUNNING)  # owner is full
        make_job(db_session, theirs, theirs_geometry, JobStatus.SUCCEEDED)  # stranger has room
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        # The stranger finishing a run frees *their* slot; it must not start the owner's run.
        assert waiting.promote(db_session, stranger.id) == []
        assert statuses(db_session, held) == [JobStatus.WAITING]

    def test_a_run_cancelled_while_the_promotion_was_deciding_is_not_resurrected(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two workers promote, or a user cancels, between the read and the write. The update
        # is conditional on the run still waiting, and only a matched row is submitted.
        victim = make_job(db_session, project, geometry, JobStatus.WAITING)
        real = limits.for_organisation

        def cancel_then_answer(db: Session, organisation_id: str, name: str):
            db.query(SimulationJob).filter(SimulationJob.id == victim.id).update(
                {"status": JobStatus.CANCELLED}, synchronize_session=False
            )
            return real(db, organisation_id, name)

        monkeypatch.setattr(waiting.limits, "for_organisation", cancel_then_answer)

        promoted = waiting.promote(db_session, owner.id)

        assert promoted == []
        assert statuses(db_session, victim) == [JobStatus.CANCELLED]

    def test_a_second_promotion_finds_nothing_left_to_do(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        assert waiting.promote(db_session, owner.id) == [held.id]
        assert waiting.promote(db_session, owner.id) == []

    def test_a_failure_while_handing_on_does_not_replace_the_runs_own_outcome(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        runner: FakeRunner,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        holder, _ = self._line(db_session, project, geometry, 1)

        def broken(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("the database blinked")

        monkeypatch.setattr(waiting, "hand_on", broken)
        waiting.start(queue, holder.id, scope_for(db_session), object())

        queue.run_next()  # does not raise

        assert statuses(db_session, holder) == [JobStatus.SUCCEEDED]
        assert any("Could not hand" in record.getMessage() for record in caplog.records)

    def test_a_run_that_vanished_hands_nothing_on(
        self, db_session: Session, queue: HoldingQueue, runner: FakeRunner
    ) -> None:
        waiting.start(queue, "no-such-job", scope_for(db_session), object())

        queue.run_next()  # no owner to hand to; no error

        assert runner.ran == []


# ---------------------------------------------------------------------------
# After a restart
# ---------------------------------------------------------------------------


class TestAfterARestart:
    def test_orphaned_runs_are_failed_and_waiting_ones_are_left_alone(
        self, db_session: Session, project: Project, geometry: GeometryVersion
    ) -> None:
        from app.main import _fail_orphaned_jobs

        running = make_job(db_session, project, geometry, JobStatus.RUNNING)
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        _fail_orphaned_jobs(session_factory=scope_for(db_session))

        assert statuses(db_session, running, held) == [JobStatus.FAILED, JobStatus.WAITING]

    def test_resume_starts_a_waiting_run_when_nothing_is_running(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
        runner: FakeRunner,
    ) -> None:
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        served = waiting.resume(queue, scope_for(db_session), object())
        queue.run_all()

        assert served == 1
        assert runner.ran == [held.id]
        assert statuses(db_session, held) == [JobStatus.SUCCEEDED]

    def test_resume_leaves_the_line_alone_while_a_slot_is_held(
        self,
        db_session: Session,
        project: Project,
        geometry: GeometryVersion,
        queue: HoldingQueue,
    ) -> None:
        make_job(db_session, project, geometry, JobStatus.QUEUED)
        held = make_job(db_session, project, geometry, JobStatus.WAITING)

        waiting.resume(queue, scope_for(db_session), object())

        assert statuses(db_session, held) == [JobStatus.WAITING]
        assert queue.pending == []

    def test_resume_with_an_empty_line_serves_nobody(
        self, db_session: Session, queue: HoldingQueue
    ) -> None:
        assert waiting.resume(queue, scope_for(db_session), object()) == 0

    def test_a_failure_to_resume_is_logged_and_does_not_stop_the_boot(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app import main

        def broken(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("no database")

        monkeypatch.setattr(waiting, "resume", broken)

        main._resume_waiting_runs()  # must not raise

        assert any("could not resume" in r.getMessage().lower() for r in caplog.records)

    def test_the_application_calls_it_at_start(self) -> None:
        # A function nothing calls is a comment: read the lifespan's own source.
        import inspect

        from app import main

        source = inspect.getsource(main.lifespan)
        assert source.index("_fail_orphaned_jobs()") < source.index("_resume_waiting_runs()")


# ---------------------------------------------------------------------------
# Through the HTTP routes
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_geometry(auth_client: AuthenticatedTestClient, project_id: str) -> str:
    response = auth_client.post(
        f"{API}/projects/{project_id}/geometry",
        files={"file": ("box.stl", box_stl(BOX), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return project_id


@pytest.fixture
def held_queue() -> HoldingQueue:
    """The route's queue, replaced by one that keeps what it is given."""
    from app.main import app

    queue = HoldingQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    return queue


def _start(client: AuthenticatedTestClient, project_id: str):
    return client.post(
        f"{API}/projects/{project_id}/simulations",
        json={"load_case": load_case(), "element_size_mm": 10.0},
    )


def _occupy_a_slot(client: AuthenticatedTestClient, project_id: str) -> str:
    version_id = client.get(f"{API}/projects/{project_id}/geometry").json()["items"][0]["id"]
    job = SimulationJob(
        project_id=project_id,
        geometry_version_id=version_id,
        status=JobStatus.RUNNING,
        solver="linear-static",
        load_case=load_case(),
    )
    client.media.db.add(job)
    client.media.db.flush()
    return str(job.id)


class TestTheRouteHoldsARunInsteadOfRefusingIt:
    def test_a_run_past_the_ceiling_is_accepted_and_waiting_at_place_one(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)

        response = _start(auth_client, project_with_geometry)

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "waiting"
        assert body["queue_position"] == 1

    def test_a_waiting_run_is_not_handed_to_the_job_queue(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)

        _start(auth_client, project_with_geometry)

        assert held_queue.pending == []

    def test_a_run_within_the_ceiling_is_still_handed_to_the_queue(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        response = _start(auth_client, project_with_geometry)

        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert response.json()["queue_position"] is None
        assert len(held_queue.pending) == 1

    def test_the_second_waiting_run_is_place_two_and_the_read_agrees(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)
        first = _start(auth_client, project_with_geometry).json()
        second = _start(auth_client, project_with_geometry).json()

        assert (first["queue_position"], second["queue_position"]) == (1, 2)
        single = auth_client.get(
            f"{API}/projects/{project_with_geometry}/simulations/{second['id']}"
        ).json()
        assert (single["status"], single["queue_position"]) == ("waiting", 2)

    def test_the_list_carries_every_runs_place_in_one_page(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)
        first = _start(auth_client, project_with_geometry).json()
        second = _start(auth_client, project_with_geometry).json()

        page = auth_client.get(f"{API}/projects/{project_with_geometry}/simulations").json()

        places = {row["id"]: row["queue_position"] for row in page["items"]}
        assert places[first["id"]] == 1
        assert places[second["id"]] == 2
        assert sorted(p for p in places.values() if p is None) == [None]  # the occupied slot

    def test_a_full_line_is_a_429_naming_both_numbers(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "max_waiting_simulations_per_user", 1)
        _occupy_a_slot(auth_client, project_with_geometry)
        assert _start(auth_client, project_with_geometry).status_code == 202

        refused = _start(auth_client, project_with_geometry)

        assert refused.status_code == 429
        assert "1 more waiting" in refused.json()["detail"]
        assert "limit of 1" in refused.json()["detail"]

    def test_the_organisations_override_of_the_line_is_the_one_in_force(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
        db_session: Session,
    ) -> None:
        project = db_session.get(Project, project_with_geometry)
        assert project is not None
        auth_client.put(
            f"{API}/organisations/{project.organisation_id}/billing",
            json={"max_waiting_simulations_per_user": 0},
        )
        _occupy_a_slot(auth_client, project_with_geometry)

        refused = _start(auth_client, project_with_geometry)

        assert refused.status_code == 429


class TestTheRoutesOwnStartHandsOn:
    def test_a_run_the_route_started_passes_its_slot_on_when_it_ends(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
        runner: FakeRunner,
    ) -> None:
        started = _start(auth_client, project_with_geometry).json()
        behind = _start(auth_client, project_with_geometry).json()
        assert (started["status"], behind["status"]) == ("queued", "waiting")

        held_queue.run_all()

        # Through the real route and the real queue seam: the second run was never handed to
        # anything by the request that created it, and is finished because the first ended.
        assert runner.ran == [started["id"], behind["id"]]


class TestCancellingInTheLine:
    def test_cancelling_a_waiting_run_closes_the_gap_behind_it(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)
        first = _start(auth_client, project_with_geometry).json()
        second = _start(auth_client, project_with_geometry).json()

        cancelled = auth_client.post(
            f"{API}/projects/{project_with_geometry}/simulations/{first['id']}/cancel"
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        again = auth_client.get(
            f"{API}/projects/{project_with_geometry}/simulations/{second['id']}"
        ).json()
        assert again["queue_position"] == 1
        # Nothing was running on it, so no slot was freed and nothing was started.
        assert again["status"] == "waiting"

    def test_cancelling_a_queued_run_hands_its_slot_on_now(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
        runner: FakeRunner,
    ) -> None:
        queued = _start(auth_client, project_with_geometry).json()
        waiter = _start(auth_client, project_with_geometry).json()
        assert (queued["status"], waiter["status"]) == ("queued", "waiting")

        auth_client.post(
            f"{API}/projects/{project_with_geometry}/simulations/{queued['id']}/cancel"
        )

        # Before the pool ever reaches the cancelled entry: the slot moved at the cancel.
        moved = auth_client.get(
            f"{API}/projects/{project_with_geometry}/simulations/{waiter['id']}"
        ).json()
        assert moved["status"] == "queued"
        assert len(held_queue.pending) == 2  # the cancelled entry still there, and the promoted run

    def test_a_waiting_run_can_be_deleted_only_once_it_is_over(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        held_queue: HoldingQueue,
    ) -> None:
        _occupy_a_slot(auth_client, project_with_geometry)
        held = _start(auth_client, project_with_geometry).json()

        refused = auth_client.delete(
            f"{API}/projects/{project_with_geometry}/simulations/{held['id']}"
        )

        assert refused.status_code == 409
        assert "waiting" in refused.json()["detail"]


class TestARealRunDrainsTheLine:
    def test_the_runs_behind_a_real_solve_start_by_themselves_in_order(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        db_session: Session,
    ) -> None:
        # No doubles: the real runner and the inline queue. Two runs wait behind a real
        # one; ending it starts the first, whose end starts the second.
        version_id = auth_client.get(f"{API}/projects/{project_with_geometry}/geometry").json()[
            "items"
        ][0]["id"]
        holder = SimulationJob(
            project_id=project_with_geometry,
            geometry_version_id=version_id,
            status=JobStatus.QUEUED,
            solver="linear-static",
            load_case=load_case(),
            element_size_mm=10.0,
        )
        db_session.add(holder)
        db_session.flush()
        first = _start(auth_client, project_with_geometry).json()
        second = _start(auth_client, project_with_geometry).json()
        assert (first["status"], second["status"]) == ("waiting", "waiting")

        waiting.start(InlineJobQueue(), holder.id, scope_for(db_session), auth_client.store)

        jobs = [db_session.get(SimulationJob, i) for i in (holder.id, first["id"], second["id"])]
        for job in jobs:
            assert job is not None
            db_session.refresh(job)
        assert [j.status for j in jobs if j] == [JobStatus.SUCCEEDED] * 3
        started = [j.started_at for j in jobs if j]
        assert started == sorted(started), "first in, first out"


# ---------------------------------------------------------------------------
# The autoscaler and the operator
# ---------------------------------------------------------------------------


class TestTheFleetOnlySeesWhatIsHandedToIt:
    def test_a_waiting_run_is_not_compute_backlog(
        self,
        auth_client: AuthenticatedTestClient,
        project_with_geometry: str,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        version_id = auth_client.get(f"{API}/projects/{project_with_geometry}/geometry").json()[
            "items"
        ][0]["id"]
        for _ in range(4):
            db_session.add(
                SimulationJob(
                    project_id=project_with_geometry,
                    geometry_version_id=version_id,
                    status=JobStatus.WAITING,
                    solver="linear-static",
                    load_case=load_case(),
                )
            )
        db_session.flush()

        body = auth_client.get(f"{API}/admin/compute/scaling").json()

        # Held back by their owner's own ceiling, not waiting for a worker: scaling the
        # fleet up for them would buy nothing, because they are not allowed to use it.
        assert body["queued"] == 0
        assert body["oldest_wait_s"] is None


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


@pytest.fixture
def toolbox(
    db_session: Session, owner: User, project: Project, geometry: GeometryVersion, queue: HoldingQueue
):
    from app.ai.tools import ToolBox

    return ToolBox(
        db=db_session,
        user=owner,
        project_id=project.id,
        job_queue=queue,
        session_scope=scope_for(db_session),
        media_store=object(),
    )


class TestTheAgentIsToldTheTruthAboutAHeldRun:
    def _other_project_busy(self, db: Session, owner: User) -> None:
        other = make_project(db, owner, "Elsewhere")
        make_job(db, other, make_geometry(db, other, owner), JobStatus.RUNNING)

    def test_a_run_past_the_ceiling_comes_back_waiting_with_its_place(
        self, db_session: Session, owner: User, toolbox: Any, queue: HoldingQueue
    ) -> None:
        self._other_project_busy(db_session, owner)

        result = toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

        assert result["status"] == "waiting"
        assert result["queue_position"] == 1
        assert "Waiting for a slot" in result["note"]
        assert "number 1" in result["note"]
        assert "Do not report a result yet" in result["note"]
        assert queue.pending == [], "a held run is not handed to the queue"

    def test_a_run_within_the_ceiling_says_exactly_what_it_said_before_waiting_existed(
        self, toolbox: Any, queue: HoldingQueue
    ) -> None:
        result = toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

        assert result["status"] == "queued"
        assert "queue_position" not in result
        assert result["note"].startswith("Queued. Meshing and solving take minutes;")
        assert len(queue.pending) == 1

    def test_a_held_run_counts_as_in_flight_so_the_agent_cannot_pile_up_duplicates(
        self, db_session: Session, owner: User, toolbox: Any
    ) -> None:
        from app.ai.tools import ToolError

        self._other_project_busy(db_session, owner)
        toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

        with pytest.raises(ToolError, match=r"already queued or running.*1 of them waiting"):
            toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

    def test_get_simulation_reports_the_place(
        self, db_session: Session, owner: User, toolbox: Any
    ) -> None:
        self._other_project_busy(db_session, owner)
        held = toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

        read = toolbox.call("get_simulation", {"simulation_id": held["id"]}, allow_mutations=False)

        assert (read["status"], read["queue_position"]) == ("waiting", 1)

    def test_a_full_line_is_a_refusal_the_agent_can_read(
        self,
        db_session: Session,
        owner: User,
        project: Project,
        geometry: GeometryVersion,
        toolbox: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.ai.tools import ToolError

        monkeypatch.setattr(settings, "max_waiting_simulations_per_user", 0)
        self._other_project_busy(db_session, owner)

        with pytest.raises(ToolError, match="which is the limit of 1"):
            toolbox.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

    def test_deleting_a_project_is_refused_while_a_run_is_only_waiting(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion, toolbox: Any
    ) -> None:
        from app.ai.tools import ToolError

        make_job(db_session, project, geometry, JobStatus.WAITING)

        with pytest.raises(ToolError, match="still queued or running"):
            toolbox.call("delete_project", {"project_id": project.id}, allow_mutations=True)

    def test_the_state_block_says_a_run_is_waiting_and_not_that_none_are_in_flight(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        from app.ai.state import _project_lines

        make_job(db_session, project, geometry, JobStatus.WAITING)

        block = "\n".join(_project_lines(db_session, project))

        assert "runs_waiting: 1" in block
        assert "runs_in_flight" not in block, "nothing is queued or running; the run is held"

    def test_the_state_block_is_unchanged_when_nothing_waits(
        self, db_session: Session, owner: User, project: Project, geometry: GeometryVersion
    ) -> None:
        from app.ai.state import _project_lines

        make_job(db_session, project, geometry, JobStatus.RUNNING)

        block = "\n".join(_project_lines(db_session, project))

        assert "runs_in_flight: 1 (queued or running right now)" in block
        assert "runs_waiting" not in block
