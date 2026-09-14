"""Finished runs as surrogate datapoints — `app/simulation/datapoints.py`, master plan E10.4.

A harvest is training data, so what is pinned is mostly what must be left out,
each with a reason somebody can read: another tenant's solves, a cached copy of
an answer already counted, a failed run, a load no single force describes, a
geometry with no bounding box. And what is kept carries where it came from —
which solver, which version, and whether its mesh dependence was ever measured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import (
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    Organisation,
    Project,
    SimulationJob,
    User,
)
from app.optimise import VariableError
from app.simulation.datapoints import FEATURES, harvest
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, PressureLoad
from tests.test_solver import uniaxial_case

WANTED = ("bounding_box_x_mm", "bounding_box_z_mm", "youngs_modulus_mpa", "applied_force_n")
BOX = {"bounding_box": {"min": [0.0, 0.0, 0.0], "max": [10.0, 12.0, 100.0], "size": [10.0, 12.0, 100.0]}}
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


class _World:
    def __init__(self, organisation: Organisation, project: Project, geometry: GeometryVersion) -> None:
        self.organisation = organisation
        self.project = project
        self.geometry = geometry


def _world(db: Session, *, slug: str = "harvest-co", stats: dict[str, Any] | None = None) -> _World:
    org = Organisation(name=slug, slug=slug, is_personal=False)
    db.add(org)
    db.flush()
    owner = User(email=f"{slug}@kryova.dev", hashed_password="x", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(name="Bar", owner_id=owner.id, organisation_id=org.id)
    db.add(project)
    db.flush()
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename="bar.step",
        size_bytes=2048,
        sha256=slug.ljust(64, "0")[:64],
        meta={},
    )
    db.add(media)
    db.flush()
    geometry = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename="bar.step",
        file_format="step",
        stats=BOX if stats is None else stats,
    )
    db.add(geometry)
    db.flush()
    return _World(org, project, geometry)


def _load_case(force_n: float = 1000.0) -> dict[str, Any]:
    return uniaxial_case(MATERIALS["steel-1018"], force_n).model_dump(mode="json")


def _job(
    db: Session,
    world: _World,
    *,
    status: JobStatus = JobStatus.SUCCEEDED,
    load_case: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    minutes_ago: int = 0,
    **columns: Any,
) -> SimulationJob:
    values: dict[str, Any] = {
        "solver": "calculix",
        "solver_version": "2.22",
        "element_size_mm": 4.0,
        "element_order": 1,
        "grids": 1,
        "analysis": "solid",
    }
    values.update(columns)
    job = SimulationJob(
        project_id=world.project.id,
        geometry_version_id=world.geometry.id,
        status=status,
        load_case=_load_case() if load_case is None else load_case,
        result={"max_displacement_mm": 0.0476, "mesh_convergence": {"basis": "single-grid"}}
        if result is None
        else result,
        finished_at=NOW - timedelta(minutes=minutes_ago),
        **values,
    )
    db.add(job)
    db.flush()
    return job


def _harvest(db: Session, world: _World, **overrides: Any) -> Any:
    options: dict[str, Any] = {"response": "max_displacement_mm", "features": WANTED}
    options.update(overrides)
    return harvest(db, world.organisation.id, **options)


class TestAFinishedRunIsADatapoint:
    def test_its_features_are_read_off_the_geometry_and_the_load_case(self, db_session: Session) -> None:
        world = _world(db_session)
        job = _job(db_session, world, load_case=_load_case(2500.0))

        found = _harvest(db_session, world)

        assert found.skipped == ()
        (point,) = found.datapoints
        assert point.source == job.id
        assert point.response == pytest.approx(0.0476)
        assert point.features == {
            "bounding_box_x_mm": 10.0,
            "bounding_box_z_mm": 100.0,
            "youngs_modulus_mpa": MATERIALS["steel-1018"].youngs_modulus_mpa,
            "applied_force_n": 2500.0,
        }

    def test_it_carries_the_solver_its_version_and_its_mesh_dependence(self, db_session: Session) -> None:
        world = _world(db_session)
        _job(db_session, world, minutes_ago=2)
        _job(
            db_session,
            world,
            result={"max_displacement_mm": 0.05, "mesh_convergence": {"basis": "grid-convergence-index"}},
        )

        newest, oldest = _harvest(db_session, world).datapoints

        assert (newest.solver, newest.solver_version) == ("calculix", "2.22")
        assert newest.mesh_convergence == "grid-convergence-index"
        assert oldest.mesh_convergence == "single-grid"

    def test_the_forces_are_summed_as_vectors(self, db_session: Session) -> None:
        world = _world(db_session)
        case = _load_case(3.0)
        case["loads"].append({**case["loads"][0], "force_n": [4.0, 0.0, 0.0]})
        _job(db_session, world, load_case=case)

        (point,) = _harvest(db_session, world).datapoints

        assert point.features["applied_force_n"] == pytest.approx(5.0)


class TestWhatIsLeftOutSaysWhy:
    def test_another_organisations_runs_are_not_training_data(self, db_session: Session) -> None:
        mine = _world(db_session, slug="mine-co")
        theirs = _world(db_session, slug="theirs-co")
        _job(db_session, theirs)

        found = _harvest(db_session, mine)

        assert found.datapoints == () and found.skipped == ()

    def test_a_run_that_did_not_succeed_is_not_read(self, db_session: Session) -> None:
        world = _world(db_session)
        for status in (JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.RUNNING):
            _job(db_session, world, status=status)

        found = _harvest(db_session, world)

        assert found.datapoints == () and found.skipped == ()

    def test_a_cached_copy_is_counted_once_where_it_was_solved(self, db_session: Session) -> None:
        world = _world(db_session)
        solved = _job(db_session, world, minutes_ago=5)
        copy = _job(db_session, world, cache_hit=True, cache_source_id=solved.id)

        found = _harvest(db_session, world)

        assert [one.source for one in found.datapoints] == [solved.id]
        (reason,) = found.skipped
        assert reason.startswith(f"{copy.id}:") and solved.id in reason and "counted once" in reason

    def test_a_pressure_has_no_single_force(self, db_session: Session) -> None:
        world = _world(db_session)
        case = _load_case()
        case["loads"] = [
            PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=2.0).model_dump(mode="json")
        ]
        job = _job(db_session, world, load_case=case)

        found = _harvest(db_session, world)

        assert found.datapoints == ()
        (reason,) = found.skipped
        assert reason.startswith(f"{job.id}: lacks applied_force_n")
        assert "PressureLoad, which has no single force" in reason

    def test_a_pressure_run_is_still_a_datapoint_for_features_that_do_not_need_a_force(
        self, db_session: Session
    ) -> None:
        world = _world(db_session)
        case = _load_case()
        case["loads"] = [
            PressureLoad(where=FaceSelector(axis="z", side="max"), pressure_mpa=2.0).model_dump(mode="json")
        ]
        _job(db_session, world, load_case=case)

        found = _harvest(db_session, world, features=("bounding_box_z_mm",))

        assert len(found.datapoints) == 1 and found.skipped == ()

    def test_a_geometry_with_no_bounding_box(self, db_session: Session) -> None:
        world = _world(db_session, stats={})
        job = _job(db_session, world)

        (reason,) = _harvest(db_session, world).skipped

        assert reason.startswith(f"{job.id}: lacks bounding_box_x_mm, bounding_box_z_mm")
        assert "no bounding box" in reason

    def test_a_result_without_the_response(self, db_session: Session) -> None:
        world = _world(db_session)
        job = _job(db_session, world, result={"max_von_mises_mpa": 12.0})

        (reason,) = _harvest(db_session, world).skipped

        assert reason == f"{job.id}: its result has no max_displacement_mm."

    def test_a_run_on_a_borrowed_temperature_field(self, db_session: Session) -> None:
        world = _world(db_session)
        job = _job(db_session, world, temperature_source={"simulation_id": "t", "fields_sha256": "a" * 64})

        (reason,) = _harvest(db_session, world).skipped

        assert reason.startswith(f"{job.id}:") and "temperature field" in reason

    def test_a_plane_run_is_not_a_solid_datapoint(self, db_session: Session) -> None:
        world = _world(db_session)
        _job(db_session, world, analysis="plane-stress", thickness_mm=2.0)

        found = _harvest(db_session, world)

        assert found.datapoints == () and found.skipped == ()


class TestTheHarvestIsBounded:
    def test_the_newest_runs_are_read_first_up_to_the_limit(self, db_session: Session) -> None:
        world = _world(db_session)
        jobs = [_job(db_session, world, minutes_ago=minutes) for minutes in (30, 10, 20)]

        found = _harvest(db_session, world, limit=2)

        assert [one.source for one in found.datapoints] == [jobs[1].id, jobs[2].id]

    def test_an_unknown_response_or_feature_is_refused_by_name(self, db_session: Session) -> None:
        world = _world(db_session)
        with pytest.raises(VariableError, match="'max_strain' is not a stored response"):
            _harvest(db_session, world, response="max_strain")
        with pytest.raises(VariableError, match="wall_thickness_mm"):
            _harvest(db_session, world, features=("wall_thickness_mm",))
        assert "bounding_box_x_mm" in FEATURES
