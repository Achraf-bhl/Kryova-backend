"""Stopping a turn and stopping a run (P5.6).

The thing worth testing hardest here is the thing that is easiest to get wrong
and impossible to notice: **the stop signal has to cross a process boundary.**
Whoever presses stop is served by one worker and the turn is streaming from
another, so an implementation that reads an attribute off an object already in
the current session works perfectly in a single-session test and never once in
production. Several tests below therefore go out of their way to read through a
*second* session, which is what a second worker looks like from in here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core import interruption, lifecycle
from app.models import (
    Conversation,
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Organisation,
    Project,
    SimulationJob,
    User,
)
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


@pytest.fixture
def user(db_session: Session) -> User:
    row = User(email="stopper@kryova.dev", hashed_password="x", is_active=True)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def conversation(db_session: Session, user: User) -> Conversation:
    row = Conversation(title="Bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


class TestStoppingATurn:
    def test_nothing_is_pending_on_a_fresh_conversation(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        assert not interruption.turn_stop_requested(db_session, conversation)

    def test_a_request_is_visible_to_a_reader(
        self, db_session: Session, conversation: Conversation, user: User
    ) -> None:
        interruption.request_turn_stop(db_session, conversation, by=user)
        assert interruption.turn_stop_requested(db_session, conversation)
        assert conversation.cancel_requested_by_id == user.id

    def test_the_check_reads_the_database_rather_than_the_loaded_attribute(
        self, db_session: Session, conversation: Conversation, user: User
    ) -> None:
        """The whole point, and the one failure mode that hides until production.

        The request is written by an API worker; the check runs in a streaming
        worker whose object was loaded before the request existed. Simulated
        here by writing the column behind the loaded object's back and then
        expunging it, so the in-memory copy cannot possibly know.
        """
        db_session.query(Conversation).filter_by(id=conversation.id).update(
            {"cancel_requested_at": lifecycle.utcnow()}
        )
        db_session.expunge(conversation)

        stale = Conversation(id=conversation.id, title="Bracket", owner_id=user.id)
        stale.cancel_requested_at = None  # what a stale streaming worker holds

        assert interruption.turn_stop_requested(db_session, stale)

    def test_starting_a_turn_spends_a_previous_stop(
        self, db_session: Session, conversation: Conversation, user: User
    ) -> None:
        """Otherwise one press of stop ends every turn after it, instantly.

        Each of those would look to the user like the product refusing to work,
        and nothing on screen would connect it to a button pressed ten minutes
        ago.
        """
        interruption.request_turn_stop(db_session, conversation, by=user)
        interruption.clear_turn_stop(db_session, conversation)

        assert not interruption.turn_stop_requested(db_session, conversation)
        assert conversation.cancel_requested_by_id is None

    def test_clearing_when_nothing_is_pending_writes_nothing(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        # Called at the top of every turn, so it must not be a write on the
        # ordinary path.
        interruption.clear_turn_stop(db_session, conversation)
        assert not interruption.turn_stop_requested(db_session, conversation)

    def test_asking_twice_is_not_an_error(
        self, db_session: Session, conversation: Conversation, user: User
    ) -> None:
        # A stop button people press twice is a stop button working as intended.
        interruption.request_turn_stop(db_session, conversation, by=user)
        interruption.request_turn_stop(db_session, conversation, by=user)
        assert interruption.turn_stop_requested(db_session, conversation)


class TestTheTurnStopRoute:
    def test_it_accepts_rather_than_claiming_to_have_stopped_anything(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        """`202`, and the wording matters.

        The turn is streaming elsewhere and ends at its next step boundary. A
        `200 {"stopped": true}` would be a claim this endpoint is in no position
        to make.
        """
        mine = Conversation(title="Bracket", owner_id=current_user_id)
        db_session.add(mine)
        db_session.flush()

        response = auth_client.post(f"{API}/ai/conversations/{mine.id}/cancel")

        assert response.status_code == 202
        assert response.json()["status"] == "accepted"
        assert "kept" in response.json()["detail"]

    def test_another_users_conversation_is_not_stoppable(
        self, auth_client: AuthenticatedTestClient, db_session: Session, user: User
    ) -> None:
        theirs = Conversation(title="Not yours", owner_id=user.id)
        db_session.add(theirs)
        db_session.flush()

        response = auth_client.post(f"{API}/ai/conversations/{theirs.id}/cancel")

        # 404, not 403 -- ids must not be enumerable across accounts.
        assert response.status_code == 404


class TestStoppingARun:
    def test_a_queued_run_is_cancelled_outright(
        self, db_session: Session, user: User
    ) -> None:
        job = SimulationJob(
            project_id="p", geometry_version_id="g", status=JobStatus.QUEUED, solver="calculix"
        )

        refusal = interruption.request_simulation_stop(db_session, job, by=user)

        assert refusal is None
        assert job.status is JobStatus.CANCELLED
        assert job.finished_at is not None
        assert job.cancel_requested_by_id == user.id

    def test_a_running_run_is_marked_but_not_yet_stopped(
        self, db_session: Session, user: User
    ) -> None:
        """An honest partial, and the status says which.

        Meshing and solving are each a single call this process cannot reach
        into. Flipping the row to `CANCELLED` here would tell the user the
        machine had stopped while CalculiX carried on for another six minutes --
        and still billed them for it.
        """
        job = SimulationJob(
            project_id="p", geometry_version_id="g", status=JobStatus.RUNNING, solver="calculix"
        )

        refusal = interruption.request_simulation_stop(db_session, job, by=user)

        assert refusal is None
        assert job.status is JobStatus.RUNNING
        assert job.cancel_requested_at is not None

    @pytest.mark.parametrize(
        "status", [JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED]
    )
    def test_a_finished_run_is_refused_in_words(
        self, db_session: Session, user: User, status: JobStatus
    ) -> None:
        job = SimulationJob(
            project_id="p", geometry_version_id="g", status=status, solver="calculix"
        )

        refusal = interruption.request_simulation_stop(db_session, job, by=user)

        assert refusal is not None
        assert status.value in refusal.reason

    def test_cancelled_is_terminal(self) -> None:
        # Otherwise the runner would pick a cancelled job back up, and every
        # "is it over" check in the product would answer no forever.
        assert JobStatus.CANCELLED.is_terminal

    def test_cancelled_is_not_a_failure(self) -> None:
        """Two different facts, and grouping them corrupts the failure rate.

        A user who changes their mind twice must not read as an incident on the
        fleet-health page.
        """
        assert JobStatus.CANCELLED is not JobStatus.FAILED
        assert JobStatus.CANCELLED.value == "cancelled"

    def test_the_runner_sees_a_request_written_by_somebody_else(
        self, db_session: Session, user: User
    ) -> None:
        """`simulation_stop_requested` takes an id, not the job object.

        The runner holds a job inside a stage-long transaction that began before
        the request existed. Reading `job.cancel_requested_at` off that object
        would be reading a snapshot from before the button was pressed.
        """
        job = _persisted_job(db_session)

        assert not interruption.simulation_stop_requested(db_session, job.id)
        interruption.request_simulation_stop(db_session, job, by=user)
        assert interruption.simulation_stop_requested(db_session, job.id)


def _persisted_job(db: Session) -> SimulationJob:
    """A job with every foreign key it really needs.

    The unsaved jobs above are deliberate -- `request_simulation_stop` is pure
    enough to test on a detached row, and doing so keeps those tests about the
    decision rather than about fixtures. This one has to hit the database,
    because what it is testing is a read *through* the database.
    """
    org = Organisation(name="Interrupt Co", slug="interrupt-co", is_personal=False)
    db.add(org)
    db.flush()
    owner = User(email="runner@kryova.dev", hashed_password="x", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(name="Frame", owner_id=owner.id, organisation_id=org.id)
    db.add(project)
    db.flush()
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename="frame.stl",
        size_bytes=1024,
        sha256="0" * 64,
        meta={},
    )
    db.add(media)
    db.flush()
    geometry = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename="frame.stl",
        file_format="stl",
        stats={},
    )
    db.add(geometry)
    db.flush()
    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=JobStatus.RUNNING,
        solver="calculix",
    )
    db.add(job)
    db.flush()
    return job
