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
from pathlib import Path

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

CCX_220 = "calculix 2.20 sha256:" + "c" * 64


def _inputs(**overrides: object) -> cache.Inputs:
    base = dict(
        geometry_sha256="a" * 64,
        load_case=LOAD,
        thermal_case=None,
        transient_case=None,
        flow_case=None,
        temperature_source=None,
        element_size_mm=4.0,
        element_order=1,
        analysis="solid",
        grids=1,
        thickness_mm=None,
        solver="calculix",
        engine=CCX_220 + "; gmsh 4.15.2",
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
            ("solver", "internal"),
            ("engine", "calculix 2.20 sha256:" + "d" * 64 + "; gmsh 4.15.2"),
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
            "engine",
            # Covered by its own test below rather than parametrised, because
            # `None` is the interesting value and the parametrisation supplies
            # a non-default.
            "thermal_case",
            "transient_case",
            "flow_case",
            "temperature_source",
        }
        assert {field.name for field in fields(cache.Inputs)} == covered

    def test_a_thermal_case_changes_the_key(self) -> None:
        assert _inputs(thermal_case={"ambient_c": 20}).digest() != _inputs().digest()

    def test_a_transient_case_changes_the_key(self) -> None:
        assert _inputs(transient_case={"duration_s": 60}).digest() != _inputs().digest()

    def test_a_flow_case_changes_the_key(self) -> None:
        assert _inputs(flow_case={"cell_size_mm": 0.5}).digest() != _inputs().digest()
        assert (
            _inputs(flow_case={"cell_size_mm": 0.5}).digest()
            != _inputs(flow_case={"cell_size_mm": 0.4}).digest()
        )

    def test_a_different_engine_behind_the_same_solver_name_is_a_different_run(self) -> None:
        """A flow run's `solver` is always "openfoam"; the image id is what pins it."""
        one = _inputs(engine="docker opencfd/openfoam-default:2412 sha256:" + "1" * 64)
        two = _inputs(engine="docker opencfd/openfoam-default:2412 sha256:" + "2" * 64)
        assert one.digest() != two.digest() != _inputs().digest()

    def test_a_borrowed_field_with_a_different_digest_is_a_different_run(self) -> None:
        """The source's archive digest is in the key, so two runs coupled to the
        same thermal job id but different temperatures never share a result."""
        one = _inputs(temperature_source={"simulation_id": "s", "fields_sha256": "a" * 64})
        two = _inputs(temperature_source={"simulation_id": "s", "fields_sha256": "b" * 64})
        assert one.digest() != two.digest() != _inputs().digest()

    def test_the_same_dict_as_a_steady_or_a_transient_case_gives_different_keys(self) -> None:
        """One dict in the wrong column is a different run, not the same one."""
        case = {"conductivity_w_mk": 51.9}
        assert (
            _inputs(thermal_case=case).digest() != _inputs(transient_case=case).digest()
        )

    def test_the_same_version_from_a_different_build_is_a_different_run(self) -> None:
        """A version is a name. Two builds of 2.20 both print "Version 2.20"."""
        other_build = "calculix 2.20 sha256:" + "e" * 64 + "; gmsh 4.15.2"
        assert _inputs(engine=other_build).digest() != _inputs().digest()

    def test_no_version_read_after_the_run_is_part_of_the_key(self) -> None:
        """Until 2026-09-14 the key hashed `job.solver_version`, which the runner
        sets only after the solve — so it was empty at key time on every job, and
        every key carried the same "unknown" sentinel. Unknown matched unknown
        everywhere, and a CalculiX upgrade was served the old build's answers."""
        from dataclasses import fields

        assert "solver_version" not in {field.name for field in fields(cache.Inputs)}

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
        assert inputs.engine == cache.engine_for("internal")

    def test_the_key_names_the_backend_that_will_run_not_the_label_it_was_queued_with(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The row says `calculix` because that is what the route wrote; the
        deployment now runs the in-house solver, and the key must bind that."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "solver_backend", "internal")
        world = _world(db_session)
        job = _job(db_session, world)
        assert job.solver == "calculix"

        inputs = cache.inputs_for(db_session, job)

        assert inputs is not None and inputs.solver == "internal"
        assert inputs.engine.startswith("kryova sha256:")

    def test_a_calculix_run_is_keyed_on_the_binary_it_will_run(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "solver_backend", "calculix")
        monkeypatch.setattr("app.solve.registry.calculix_identity", lambda executable: CCX_220)
        world = _world(db_session)
        job = _job(db_session, world)

        inputs = cache.inputs_for(db_session, job)

        assert inputs is not None and inputs.solver == "calculix"
        assert inputs.engine.startswith(CCX_220 + "; gmsh ")

    def test_a_calculix_run_whose_binary_cannot_be_identified_is_never_cached(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """"We do not know which CalculiX" must never be served as though it came
        from a known one — nor from another unknown one, which is where it failed."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "solver_backend", "calculix")
        monkeypatch.setattr("app.solve.registry.calculix_identity", lambda executable: None)
        world = _world(db_session)
        job = _job(db_session, world)

        assert cache.inputs_for(db_session, job) is None

    def test_a_backend_this_build_does_not_know_is_never_cached(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "solver_backend", "nonesuch")
        world = _world(db_session)
        job = _job(db_session, world)

        assert cache.inputs_for(db_session, job) is None

    def test_a_plane_run_is_keyed_on_the_in_house_solver_on_a_calculix_deployment(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_execute_plane` never reads `SOLVER_BACKEND`, so neither may the key."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "solver_backend", "calculix")
        monkeypatch.setattr("app.solve.registry.calculix_identity", lambda executable: CCX_220)
        world = _world(db_session)
        job = _job(db_session, world)
        job.analysis, job.thickness_mm = "plane-stress", 2.0
        db_session.flush()

        inputs = cache.inputs_for(db_session, job)

        assert inputs is not None and inputs.solver == "internal"
        assert inputs.engine == cache.engine_for("internal")

    def test_a_flow_run_is_keyed_on_the_image_it_will_run(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world = _world(db_session)
        job = _job(db_session, world)
        job.analysis, job.flow_case, job.load_case = "flow-laminar", {"cell_size_mm": 0.5}, None
        db_session.flush()
        engine = "docker opencfd/openfoam-default:2412 sha256:" + "3" * 64
        monkeypatch.setattr("app.solve.openfoam.run.engine_identity", lambda launcher, image: engine)

        inputs = cache.inputs_for(db_session, job)

        assert inputs is not None and inputs.engine.startswith(engine + "; gmsh ")
        assert inputs.solver == "openfoam"
        assert inputs.flow_case == {"cell_size_mm": 0.5}

    def test_a_flow_run_whose_engine_cannot_be_named_is_never_cached(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The `local` launcher prints its version only once it has run, so there is
        nothing to bind a key to beforehand — and a key with a hole where the solver
        belongs would match runs it should not."""
        world = _world(db_session)
        job = _job(db_session, world)
        job.analysis, job.flow_case, job.load_case = "flow-laminar", {"cell_size_mm": 0.5}, None
        db_session.flush()
        monkeypatch.setattr("app.solve.openfoam.run.engine_identity", lambda launcher, image: None)

        assert cache.inputs_for(db_session, job) is None


class TestTheEngineIsNamedBeforeItRuns:
    def test_every_engine_carries_the_meshers_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every analysis meshes with gmsh first; a different gmsh is a different mesh."""
        import gmsh

        monkeypatch.setattr("app.solve.registry.calculix_identity", lambda executable: CCX_220)
        monkeypatch.setattr("app.solve.openfoam.run.engine_identity", lambda launcher, image: "docker x sha256:1")
        for backend in ("internal", "calculix", "openfoam"):
            engine = cache.engine_for(backend)
            assert engine is not None and engine.endswith(f"; gmsh {gmsh.__version__}")

    def test_the_in_house_engine_names_its_numerics(self) -> None:
        import numpy
        import scipy

        engine = cache.in_house_identity()
        assert f"numpy {numpy.__version__}" in engine and f"scipy {scipy.__version__}" in engine
        assert cache.source_identity() in engine


class TestTheInHouseSourceIdentity:
    """What an in-house result is keyed on: the code that computes and stores it."""

    def _tree(self, root: Path, newline: bytes = b"\n") -> Path:
        from app.verify.recorded import FINGERPRINTED

        for entry in (*FINGERPRINTED, "app/simulation/runner.py", "app/simulation/coupling.py"):
            target = root / (entry if entry.endswith(".py") else f"{entry}/module.py")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"def f():" + newline + b"    return 1" + newline)
        return root

    def test_a_change_to_a_solver_moves_it(self, tmp_path: Path) -> None:
        tree = self._tree(tmp_path)
        before = cache.source_identity(tree)
        (tree / "app/solve/module.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        assert cache.source_identity(tree) != before

    def test_a_change_to_the_runner_moves_it(self, tmp_path: Path) -> None:
        """The runner decides what is stored — a plane run's nodal averaging, a
        flow's pressure conversion — and V&V's fingerprint does not cover it."""
        tree = self._tree(tmp_path)
        before = cache.source_identity(tree)
        (tree / "app/simulation/runner.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        assert cache.source_identity(tree) != before

    def test_a_change_to_the_coupling_moves_it(self, tmp_path: Path) -> None:
        tree = self._tree(tmp_path)
        before = cache.source_identity(tree)
        (tree / "app/simulation/coupling.py").write_text("def f():\n    return 2\n", encoding="utf-8")
        assert cache.source_identity(tree) != before

    def test_line_endings_do_not_move_it(self, tmp_path: Path) -> None:
        """A Windows checkout must key the same source the same way a Linux one does."""
        lf = cache.source_identity(self._tree(tmp_path / "lf"))
        crlf = cache.source_identity(self._tree(tmp_path / "crlf", b"\r\n"))
        assert lf == crlf

    def test_a_file_that_computes_nothing_does_not_move_it(self, tmp_path: Path) -> None:
        """A route or a docstring elsewhere in `app/` must not throw the cache away."""
        tree = self._tree(tmp_path)
        before = cache.source_identity(tree)
        (tree / "app/api").mkdir(parents=True)
        (tree / "app/api/routes.py").write_text("x = 1\n", encoding="utf-8")
        assert cache.source_identity(tree) == before


class TestAKeyStillDescribesTheRun:
    def _bound(self) -> cache.Inputs:
        return _inputs(solver="internal", engine=cache.engine_for("internal"))

    def test_the_run_the_key_named_is_bound(self) -> None:
        assert cache.unbound(self._bound(), "linear-static") is None
        assert cache.unbound(self._bound(), "plane") is None

    def test_a_solver_from_another_backend_is_unbound(self) -> None:
        reason = cache.unbound(self._bound(), "calculix")
        assert reason is not None and "'internal'" in reason and "'calculix'" in reason

    def test_a_solver_this_build_never_made_is_unbound(self) -> None:
        """A caller that hands the runner its own solver has not run the engine the key named."""
        assert cache.unbound(self._bound(), "a-solver-handed-in") is not None

    def test_an_engine_that_moved_during_the_run_is_unbound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A binary replaced, or an image re-tagged, while the run was in flight."""
        inputs = self._bound()
        monkeypatch.setattr(cache, "engine_for", lambda backend: "something else")
        reason = cache.unbound(inputs, "linear-static")
        assert reason is not None and "changed while the run was in flight" in reason


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
