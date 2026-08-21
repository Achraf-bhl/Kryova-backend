"""Startup behaviour, tested directly rather than through every TestClient."""

from contextlib import contextmanager

import pytest

from app.main import _fail_orphaned_jobs
from app.models import (
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Project,
    SimulationJob,
    User,
)


@pytest.fixture
def scope(db_session):
    @contextmanager
    def _scope():
        yield db_session

    return _scope


@pytest.fixture
def job(db_session) -> SimulationJob:
    user = User(email="startup@kryova.dev", hashed_password="not-a-real-hash")
    project = Project(name="Orphan", owner=user)
    media = Media(
        owner=user,
        kind=MediaKind.CAD,
        filename="part.stl",
        size_bytes=1,
        sha256="0" * 64,
    )
    version = GeometryVersion(
        project=project, media=media, version_number=1, filename="part.stl", file_format="stl"
    )
    simulation = SimulationJob(
        project=project,
        geometry_version=version,
        status=JobStatus.RUNNING,
        solver="linear-static-tet4",
        load_case={},
    )
    db_session.add_all([user, project, media, version, simulation])
    db_session.flush()
    return simulation


def test_jobs_orphaned_by_a_restart_are_failed(db_session, scope, job) -> None:
    # The in-process queue does not survive a restart, so a job still marked
    # RUNNING at startup has no worker and would be polled forever.
    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.FAILED
    assert "restart" in job.error


def test_a_queued_job_is_also_failed(db_session, scope, job) -> None:
    job.status = JobStatus.QUEUED
    db_session.flush()

    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.FAILED


def test_finished_jobs_are_left_alone(db_session, scope, job) -> None:
    job.status = JobStatus.SUCCEEDED
    db_session.flush()

    _fail_orphaned_jobs(session_factory=scope)

    db_session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED
