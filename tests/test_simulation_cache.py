"""Not solving the same thing twice, and never serving the wrong answer (E15.2).

A cache in a verification product is a liability before it is an optimisation:
the failure mode is not "slow", it is "a confident number computed under
different conditions". So almost everything here is about what must **miss** —
a different mesh, a different solver, a different tenant, an unmeasured version —
because a cache that hits too often is indistinguishable from a working one
right up until somebody signs a drawing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
from app.simulation import cache

LOAD = {"name": "Bracket", "fixtures": [{"kind": "fixed", "at": "base"}]}


def _inputs(**overrides: object) -> cache.Inputs:
    base = dict(
        geometry_sha256="a" * 64,
        load_case=LOAD,
        thermal_case=None,
        element_size_mm=4.0,
        element_order=1,
        analysis="solid",
        grids=1,
        thickness_mm=None,
        solver="calculix",
        solver_version="2.22",
    )
    base.update(overrides)
    return cache.Inputs(**base)  # type: ignore[arg-type]


class TestTheKey:
    def test_the_same_binding_gives_the_same_key(self) -> None:
        assert _inputs().digest() == _inputs().digest()

    def test_a_dict_built_in_a_different_order_gives_the_same_key(self) -> None:
        """A key that moved when a dict happened to be built differently would
        make the cache a random-hit generator."""
        one = _inputs(load_case={"a": 1, "b": 2})
        two = _inputs(load_case={"b": 2, "a": 1})

        assert one.digest() == two.digest()

    @pytest.mark.parametrize(
        "field, value",
        [
            ("geometry_sha256", "b" * 64),
            ("load_case", {"name": "Other"}),
            ("element_size_mm", 2.0),
            ("element_order", 2),
            ("analysis", "plane_stress"),
            ("grids", 3),
            ("thickness_mm", 5.0),
            ("solver", "linear-static"),
            ("solver_version", "2.21"),
        ],
    )
    def test_every_input_that_changes_the_answer_changes_the_key(
        self, field: str, value: object
    ) -> None:
        """The list is exhaustive by construction — `Inputs` is a dataclass, so
        adding a field that is not parametrised here shows up as a field with no
        test rather than as a cache that quietly ignores it."""
        assert _inputs(**{field: value}).digest() != _inputs().digest()

    def test_the_parametrised_fields_are_every_field_there_is(self) -> None:
        """The guard on the guard. A new input added to `Inputs` and not to the
        list above would make this fail, which is the whole point: an input
        missing from the key is a cache that serves the wrong number."""
        from dataclasses import fields

        covered = {
            "geometry_sha256",
            "load_case",
            "element_size_mm",
            "element_order",
            "analysis",
            "grids",
            "thickness_mm",
            "solver",
            "solver_version",
            # Covered by its own test below rather than parametrised, because
            # `None` is the interesting value and the parametrisation supplies
            # a non-default.
            "thermal_case",
        }
        assert {field.name for field in fields(cache.Inputs)} == covered

    def test_a_thermal_case_changes_the_key(self) -> None:
        assert _inputs(thermal_case={"ambient_c": 20}).digest() != _inputs().digest()

    def test_an_unmeasured_solver_version_does_not_match_a_known_one(self) -> None:
        """"We do not know which CalculiX" must never be served as though it
        came from a known one — that is the exact claim Decision 3 forbids."""
        assert _inputs(solver_version=None).digest() != _inputs(solver_version="2.22").digest()

    def test_two_unmeasured_versions_match_each_other(self) -> None:
        # They are the same state of knowledge, and refusing to match would
        # make the cache useless on every deployment where the version cannot
        # be read at all.
        assert _inputs(solver_version=None).digest() == _inputs(solver_version=None).digest()

    def test_the_key_version_is_in_the_hash(self) -> None:
        """Bumping it must miss every old key. Recomputing is expensive; serving
        a result computed under different rules is wrong."""
        import app.simulation.cache as module

        before = _inputs().digest()
        original = module.KEY_VERSION
        try:
            module.KEY_VERSION = original + 1
            assert _inputs().digest() != before
        finally:
            module.KEY_VERSION = original


class TestFindingAReusableRun:
    def test_a_finished_run_with_the_same_key_in_the_same_tenant_is_found(
        self, db_session: Session
    ) -> None:
        world = _world(db_session)
        done = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        done.finished_at = datetime.now(timezone.utc)
        fresh = _job(db_session, world, key="k1")
        db_session.flush()

        assert cache.find(db_session, fresh, "k1") is done

    def test_another_tenants_run_is_never_reused(self, db_session: Session) -> None:
        """A result crossing a tenant boundary tells one customer that another
        has a part with this exact checksum, load case and mass. That is a data
        leak wearing a performance improvement."""
        mine = _world(db_session, slug="mine")
        theirs = _world(db_session, slug="theirs", email="them@kryova.dev")
        done = _job(db_session, theirs, status=JobStatus.SUCCEEDED, key="k1")
        done.finished_at = datetime.now(timezone.utc)
        fresh = _job(db_session, mine, key="k1")
        db_session.flush()

        assert cache.find(db_session, fresh, "k1") is None

    def test_a_failed_run_is_not_reused(self, db_session: Session) -> None:
        world = _world(db_session)
        _job(db_session, world, status=JobStatus.FAILED, key="k1")
        fresh = _job(db_session, world, key="k1")
        db_session.flush()

        assert cache.find(db_session, fresh, "k1") is None

    def test_a_cancelled_run_is_not_reused(self, db_session: Session) -> None:
        # A cancellation is the product doing what it was told, and it has no
        # result — reusing one would serve a `None`.
        world = _world(db_session)
        _job(db_session, world, status=JobStatus.CANCELLED, key="k1")
        fresh = _job(db_session, world, key="k1")
        db_session.flush()

        assert cache.find(db_session, fresh, "k1") is None

    def test_a_job_never_matches_itself(self, db_session: Session) -> None:
        world = _world(db_session)
        job = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        db_session.flush()

        assert cache.find(db_session, job, "k1") is None

    def test_the_newest_match_wins(self, db_session: Session) -> None:
        world = _world(db_session)
        now = datetime.now(timezone.utc)
        old = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        old.finished_at = now - timedelta(days=30)
        new = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        new.finished_at = now
        fresh = _job(db_session, world, key="k1")
        db_session.flush()

        assert cache.find(db_session, fresh, "k1") is new


class TestAdoptingAResult:
    def test_the_answer_is_copied_and_recorded_as_a_copy(self, db_session: Session) -> None:
        world = _world(db_session)
        source = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        source.result = {"max_von_mises_mpa": 42.0}
        source.mesh_stats = {"tet_count": 411}
        fresh = _job(db_session, world, key="k1")

        cache.adopt(fresh, source)

        assert fresh.result == {"max_von_mises_mpa": 42.0}
        assert fresh.cache_hit is True
        assert fresh.cache_source_id == source.id

    def test_this_runs_own_timings_are_not_overwritten(self, db_session: Session) -> None:
        """A result reappearing with `finished_at` from three weeks ago makes
        the fleet's timing figures meaningless."""
        world = _world(db_session)
        source = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        source.started_at = datetime.now(timezone.utc) - timedelta(days=21)
        source.finished_at = source.started_at + timedelta(minutes=8)
        fresh = _job(db_session, world, key="k1")
        mine = datetime.now(timezone.utc)
        fresh.started_at = mine

        cache.adopt(fresh, source)

        assert fresh.started_at == mine

    def test_the_source_follows_the_chain_to_a_run_that_really_solved(
        self, db_session: Session
    ) -> None:
        """A hit copied from a hit must still name the original. Otherwise "how
        much did we actually solve" requires walking a chain of unknown length."""
        world = _world(db_session)
        original = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        first_copy = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        cache.adopt(first_copy, original)
        second_copy = _job(db_session, world, key="k1")

        cache.adopt(second_copy, first_copy)

        assert second_copy.cache_source_id == original.id

    def test_the_note_names_the_run_it_came_from(self, db_session: Session) -> None:
        """"Served from cache" with no id makes "why was this instant, and can I
        trust it" unanswerable — which is the question a cache in a verification
        product has to be able to answer."""
        world = _world(db_session)
        source = _job(db_session, world, status=JobStatus.SUCCEEDED, key="k1")
        fresh = _job(db_session, world, key="k1")

        assert source.id in cache.note(fresh, source)


class TestBuildingTheInputs:
    def test_a_geometry_with_no_checksum_gives_no_key(self, db_session: Session) -> None:
        """A job whose blob is gone cannot be matched to anything, and inventing
        a key for it would mean two such jobs matching each other."""
        world = _world(db_session)
        job = _job(db_session, world)
        job.geometry_version.media.sha256 = ""
        db_session.flush()

        assert cache.inputs_for(db_session, job) is None

    def test_the_inputs_come_off_the_job_and_its_geometry(self, db_session: Session) -> None:
        world = _world(db_session)
        job = _job(db_session, world)
        db_session.flush()

        inputs = cache.inputs_for(db_session, job)

        assert inputs is not None
        assert inputs.geometry_sha256 == job.geometry_version.media.sha256
        assert inputs.element_size_mm == job.element_size_mm


# -- fixtures ---------------------------------------------------------------


class _World:
    def __init__(self, project: Project, geometry: GeometryVersion) -> None:
        self.project = project
        self.geometry = geometry


def _world(db: Session, *, slug: str = "cache-co", email: str = "cache@kryova.dev") -> _World:
    org = Organisation(name=slug, slug=slug, is_personal=False)
    db.add(org)
    db.flush()
    owner = User(email=email, hashed_password="x", is_active=True)
    db.add(owner)
    db.flush()
    project = Project(name="Bracket", owner_id=owner.id, organisation_id=org.id)
    db.add(project)
    db.flush()
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename="bracket.stl",
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
        filename="bracket.stl",
        file_format="stl",
        stats={},
    )
    db.add(geometry)
    db.flush()
    return _World(project, geometry)


def _job(
    db: Session,
    world: _World,
    *,
    status: JobStatus = JobStatus.QUEUED,
    key: str | None = None,
) -> SimulationJob:
    job = SimulationJob(
        project_id=world.project.id,
        geometry_version_id=world.geometry.id,
        status=status,
        solver="calculix",
        element_size_mm=4.0,
        element_order=1,
        grids=1,
        analysis="solid",
        load_case=LOAD,
        cache_key=key,
    )
    db.add(job)
    db.flush()
    return job
