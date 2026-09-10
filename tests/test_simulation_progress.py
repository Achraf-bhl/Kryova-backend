"""Where a running simulation has got to (P5 task 2).

The run view's last missing surface. The claims worth pinning are the two that
keep it honest: a count is never half a count, and no percentage is invented
inside a stage that has nothing countable in it.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy.orm import Session

from app.models import JobStatus, SimulationJob
from app.simulation import progress


class TestTheSnapshot:
    def test_a_stage_with_no_count_carries_neither_number(self) -> None:
        payload = progress.snapshot(progress.Stage.SOLVING, detail="411 elements")

        assert payload["index"] is None and payload["total"] is None
        assert payload["stage"] == "solving"

    def test_a_numerator_with_no_denominator_is_refused(self) -> None:
        """Half a count is a fraction a client would have to guess how to
        render, and it would guess wrong in a way that looks like progress."""
        with pytest.raises(ValueError, match="numerator with no denominator"):
            progress.snapshot(progress.Stage.MESHING, index=2)

        with pytest.raises(ValueError, match="numerator with no denominator"):
            progress.snapshot(progress.Stage.MESHING, total=3)

    def test_no_stage_carries_a_percentage(self) -> None:
        """The decision, asserted rather than left to a comment. CalculiX
        reports one increment for a linear-static run, so any fraction inside a
        solve would be invented — and an invented bar over a twenty-minute solve
        teaches a user to predict a finish time nobody measured."""
        for stage in progress.STAGE_ORDER:
            payload = progress.snapshot(stage, index=1, total=3)
            assert "percent" not in payload
            assert "fraction" not in payload
            assert not any(isinstance(value, float) for value in payload.values())


class TestTheSentence:
    def test_a_run_that_has_not_reported_yet_says_nothing(self) -> None:
        """Rendered as "starting" by the caller — never as stage zero of four,
        which would claim a stage had begun."""
        assert progress.describe(None) == ""
        assert progress.describe({}) == ""

    def test_a_single_grid_run_names_the_stage_without_a_count(self) -> None:
        line = progress.describe(progress.snapshot(progress.Stage.MESHING))

        assert line == "Building the mesh"

    def test_a_study_names_which_grid_of_how_many(self) -> None:
        line = progress.describe(
            progress.snapshot(progress.Stage.SOLVING, index=2, total=3, detail="8,120 elements")
        )

        assert "grid 2 of 3" in line
        assert "8,120 elements" in line

    def test_a_count_of_one_is_not_rendered_as_a_count(self) -> None:
        # "Grid 1 of 1" is noise dressed as information.
        line = progress.describe(progress.snapshot(progress.Stage.SOLVING, index=1, total=1))

        assert "grid" not in line


class TestReporting:
    def test_it_writes_the_snapshot_onto_the_job(self, db_session: Session) -> None:
        job = _job(db_session)

        progress.report(_scope(db_session), job.id, progress.Stage.MESHING, detail="4 mm")

        db_session.refresh(job)
        assert job.progress is not None
        assert job.progress["stage"] == "meshing"
        assert job.progress["detail"] == "4 mm"

    def test_a_failure_to_record_progress_never_reaches_the_caller(
        self, db_session: Session
    ) -> None:
        """A progress line is worth a few seconds of a watcher's patience and is
        never worth a run. Verified by handing it a scope that raises."""
        job = _job(db_session)

        @contextmanager
        def broken():  # type: ignore[no-untyped-def]
            raise RuntimeError("the database is gone")
            yield  # pragma: no cover

        progress.report(broken, job.id, progress.Stage.SOLVING)

        db_session.refresh(job)
        assert job.progress is None

    def test_a_job_that_vanished_is_not_an_error(
        self, db_session: Session
    ) -> None:
        progress.report(_scope(db_session), "no-such-job", progress.Stage.STORING)


def _job(db: Session) -> SimulationJob:
    """A job with every foreign key it really needs.

    Reaches the database because what is being tested is a write *through* it —
    the same reason `tests/test_interruption.py` builds one rather than using a
    detached row.
    """
    from app.models import GeometryVersion, Media, MediaKind, Organisation, Project, User

    org = Organisation(name="Progress Co", slug="progress-co", is_personal=False)
    db.add(org)
    db.flush()
    owner = User(email="watcher@kryova.dev", hashed_password="x", is_active=True)
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
        sha256="1" * 64,
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


def _scope(db: Session):
    """A session scope that hands back the test's own session.

    The same shape `tests/conftest.py` injects for the job queue: the runner
    would open a second connection and see none of the uncommitted test data.
    """
    from contextlib import nullcontext

    return lambda: nullcontext(db)
