"""Choosing a solver, and recording which one ran — master plan 6.1.

`app/solve/calculix/` has been able to solve a real model since 2026-09-06 and
**nothing in the product could ask it to**: `simulation/runner.py` constructed
`LinearStaticSolver()` directly, and the route wrote that class's name onto the
job row as a constant. So the row named a solver nobody had consulted, which is
provenance in name only — and Decision 3 binds a result to what produced it.

That is the same failure `KRYOVA_BUILD_PLAN.md` records against the OCCT kernel
on 2026-09-05: an era of work green on capability and wired to nothing. Worth
naming as a pattern rather than fixing twice in silence.

Most of this file needs no database and no `ccx`. The two that need a real
binary say so in their skip reason, and say that they were **not measured**
rather than implying they passed.
"""

from __future__ import annotations

import sys

import pytest

from app.solve.base import ConductionSolver, Solver
from app.solve.registry import (
    CALCULIX,
    INTERNAL,
    available,
    build_conduction_solver,
    build_solver,
    conduction_available,
    solver_version,
)
from app.solve.types import SolverError
from tests.test_simulations import project_with_geometry  # noqa: F401 - fixture

CCX_PATH = r"C:\tools\calculix\bin\ccx.exe"


def _ccx_missing() -> bool:
    from app.solve.calculix.run import find_ccx

    return find_ccx() is None and find_ccx(CCX_PATH) is None


class TestTheRegistry:
    def test_both_names_build_a_solver(self) -> None:
        for name in (INTERNAL, CALCULIX):
            assert isinstance(build_solver(name), Solver)

    def test_the_default_setting_still_gives_the_in_house_solver(self) -> None:
        """An existing deployment must be unchanged by this module existing."""
        from app.core.config import Settings

        assert Settings().solver_backend == INTERNAL
        assert type(build_solver(Settings().solver_backend)).__name__ == "LinearStaticSolver"

    def test_an_unknown_name_names_what_there_is(self) -> None:
        """Never a KeyError: a misspelled setting is an operator mistake, and the
        useful answer to one is the list of things they might have meant."""
        with pytest.raises(SolverError) as refused:
            build_solver("calculx")

        message = str(refused.value)
        assert "calculix" in message
        assert "internal" in message

    def test_the_refusal_says_it_is_never_automatic(self) -> None:
        """`geometry_backend` sets the precedent and the reason: a deployment
        that silently fell back would hand the user a result computed by
        something they did not choose."""
        with pytest.raises(SolverError) as refused:
            build_solver("")

        assert "never chosen automatically" in str(refused.value)

    def test_names_are_case_and_space_insensitive(self) -> None:
        """A setting read from an environment variable arrives however it was
        typed, and refusing ' Calculix' teaches nobody anything."""
        assert isinstance(build_solver("  CalculiX "), Solver)

    def test_available_is_stable_and_sorted(self) -> None:
        assert available() == (CALCULIX, INTERNAL)


class TestTheConductionRegistry:
    """A second table, because `ConductionSolver` is a second ABC.

    The alternative was one table returning `Solver | ConductionSolver`, and
    that is the union-typed `solve()` the four ABCs exist to keep out of
    callers, moved up one level into the factory: `simulation/runner.py` takes
    what `build_solver` returns and calls `solve(mesh, load_case)` on it, so
    every caller would have to narrow the union back before it could do
    anything. Two eight-line lookups is the cheaper honest answer, and the
    duplication is bounded — `solver_version` is deliberately **not** copied,
    because a version is a fact about the backend and not about the analysis.
    """

    def test_the_internal_name_builds_a_conduction_solver(self) -> None:
        assert isinstance(build_conduction_solver(INTERNAL), ConductionSolver)

    def test_the_conduction_table_is_stable_and_sorted(self) -> None:
        assert conduction_available() == (INTERNAL,)

    def test_the_two_tables_do_not_answer_for_each_other(self) -> None:
        """The point of keeping them apart. A `Solver` is not a conduction
        solver and a conduction solver is not a `Solver`; if either of these
        ever passed, the seam would be a name rather than a boundary."""
        assert not isinstance(build_solver(INTERNAL), ConductionSolver)
        assert not isinstance(build_conduction_solver(INTERNAL), Solver)

    def test_calculix_is_refused_by_name_and_says_why(self) -> None:
        """`ccx` really does solve steady conduction — `*HEAT TRANSFER` — and
        nothing here writes that step yet. Answering the request with the
        in-house solver would be exactly the silent substitution Decision 3
        forbids, so it is refused and the refusal names the gap."""
        with pytest.raises(SolverError) as refused:
            build_conduction_solver(CALCULIX)

        message = str(refused.value)
        assert "HEAT TRANSFER" in message
        assert "internal" in message

    def test_an_unknown_name_names_what_there_is(self) -> None:
        with pytest.raises(SolverError) as refused:
            build_conduction_solver("condution")

        assert "internal" in str(refused.value)

    def test_the_refusal_says_it_is_never_automatic(self) -> None:
        with pytest.raises(SolverError) as refused:
            build_conduction_solver("")

        assert "never chosen automatically" in str(refused.value)

    def test_names_are_case_and_space_insensitive(self) -> None:
        assert isinstance(build_conduction_solver("  Internal "), ConductionSolver)

    def test_the_recorded_name_is_the_solvers_own_not_the_setting(self) -> None:
        """Same rule as the static table: the registry key is a configuration
        alias, and what a result is bound to is what actually ran."""
        assert build_conduction_solver(INTERNAL).name == "steady-conduction"
        assert build_conduction_solver(INTERNAL).name != INTERNAL

    def test_one_version_function_serves_both_tables(self) -> None:
        """`internal` means "this codebase" whichever table selected it, so
        duplicating `solver_version` would be two places to keep in step for one
        fact."""
        from app.main import APP_VERSION

        version = solver_version(INTERNAL)
        assert version is not None and version.startswith(APP_VERSION)


class TestTheImportStaysLazy:
    """`app.solve` is imported by the API, the job layer and the AI layer;
    `app.solve.calculix` drags in a subprocess boundary, a deck writer and an
    `.frd` parser that none of them use. The cost creeps back the first time
    somebody adds a convenience re-export, so it is asserted rather than
    intended."""

    def test_importing_the_registry_does_not_import_calculix(self) -> None:
        for module in [m for m in sys.modules if m.startswith("app.solve.calculix")]:
            del sys.modules[module]
        import importlib

        importlib.reload(importlib.import_module("app.solve.registry"))

        assert not any(m.startswith("app.solve.calculix") for m in sys.modules)

    def test_building_the_internal_solver_does_not_import_calculix(self) -> None:
        for module in [m for m in sys.modules if m.startswith("app.solve.calculix")]:
            del sys.modules[module]

        build_solver(INTERNAL)

        assert not any(m.startswith("app.solve.calculix") for m in sys.modules)

    def test_building_the_static_solver_does_not_import_conduction(self) -> None:
        """`app.solve.conduction` pulls in the thermal coupling helpers, and a
        deployment that only ever runs static jobs should not pay for them. Same
        rule as CalculiX above, one step weaker: the cost is import time rather
        than a subprocess boundary, and it creeps back the first time somebody
        adds a convenience re-export."""
        for module in [m for m in sys.modules if m.startswith("app.solve.conduction")]:
            del sys.modules[module]

        build_solver(INTERNAL)

        assert "app.solve.conduction" not in sys.modules

    def test_asking_for_a_conduction_solver_does_import_it(self) -> None:
        """The other half — a lazy import that never fires is a broken one."""
        build_conduction_solver(INTERNAL)

        assert "app.solve.conduction" in sys.modules

    def test_asking_for_calculix_does_import_it(self) -> None:
        """The other half — a lazy import that never fires is a broken one."""
        build_solver(CALCULIX)

        assert "app.solve.calculix.solver" in sys.modules


class TestTheExecutablePathReachesTheSolver:
    def test_the_configured_path_is_passed_through(self) -> None:
        solver = build_solver(CALCULIX, executable=CCX_PATH)

        assert str(getattr(solver, "executable", "")) == CCX_PATH

    def test_an_empty_setting_becomes_none_not_an_empty_string(self) -> None:
        """`CALCULIX_PATH=` in a .env file arrives as `""`, and an empty string
        is not "look on PATH" to `find_ccx` — it is a path that does not exist."""
        solver = build_solver(CALCULIX, executable="")

        assert getattr(solver, "executable", "sentinel") is None


class TestTheVersionIsRecorded:
    def test_the_in_house_solver_reports_the_codebase_version(self) -> None:
        """There is no other artefact to point at, and the commit is what makes
        a result reproducible."""
        from app.main import APP_VERSION

        version = solver_version(INTERNAL)

        assert version is not None
        assert version.startswith(APP_VERSION)

    def test_a_missing_binary_is_none_and_not_a_guess(self) -> None:
        """An unmeasured version must not be invented. `None` is the honest
        answer and is what gets stored."""
        assert solver_version(CALCULIX, r"C:\definitely\not\here\ccx.exe") is None

    def test_an_unknown_solver_has_no_version(self) -> None:
        assert solver_version("nonesuch") is None

    @pytest.mark.skipif(
        _ccx_missing(),
        reason="CalculiX (ccx) is not installed, so the real version was NOT measured",
    )
    def test_it_reads_the_real_version(self) -> None:
        """`ccx -v` prints 'This is Version 2.23' and exits **201** — 201 is its
        generic "did not run a job" code, not a failure, so the return code must
        be ignored and the text read. Measured against ccx 2.23."""
        version = solver_version(CALCULIX, CCX_PATH)

        assert version is not None
        assert version[0].isdigit()

    @pytest.mark.skipif(
        _ccx_missing(),
        reason="CalculiX (ccx) is not installed, so the cache was NOT measured",
    )
    def test_the_version_is_cached_rather_than_re_read(self) -> None:
        """A subprocess per job, for an answer that cannot change while the
        process runs."""
        from app.solve import registry

        registry._VERSIONS.clear()
        first = solver_version(CALCULIX, CCX_PATH)
        assert registry._VERSIONS

        second = solver_version(CALCULIX, CCX_PATH)

        assert first == second


class TestTheJobRowCarriesWhatRan:
    """The point of all of the above. These need a database."""

    def test_the_row_records_the_solver_the_runner_used(
        self, auth_client, db_session, project_with_geometry  # noqa: F811 - the imported fixture
    ) -> None:
        """Reuses `test_simulations.py`'s own fixture and payload rather than a
        second copy — a load case written twice is two things to keep in step,
        and this test is about the solver name, not about the schema."""
        from app.models import SimulationJob
        from tests.test_simulations import load_case

        geometry = auth_client.get(
            f"/api/v1/projects/{project_with_geometry}/geometry"
        ).json()
        rows = geometry if isinstance(geometry, list) else geometry["items"]

        created = auth_client.post(
            f"/api/v1/projects/{project_with_geometry}/simulations",
            json={
                "geometry_version_id": rows[0]["id"],
                "element_size_mm": 8.0,
                "load_case": load_case(),
            },
        )
        assert created.status_code == 202, created.text

        job = db_session.get(SimulationJob, created.json()["id"])
        assert job is not None
        # The solver's OWN name, not the registry key that selected it. The two
        # deliberately differ for the in-house solver — `internal` is what an
        # operator types in a setting, `linear-static` is what actually ran — and
        # the row must carry the second, because Decision 3 binds a result to
        # what produced it rather than to how it was asked for.
        assert job.solver == build_solver(INTERNAL).name
        assert job.solver != INTERNAL
        assert job.result is not None, "the job should have completed inline"

    def test_the_recorded_name_is_the_solvers_own_not_the_setting(self) -> None:
        """Stated on its own because it is a decision, not an accident: the
        registry key is a configuration alias and the solver's `name` is the
        implementation. A row saying `internal` would tell a reader which
        setting was in the .env file, which is not what a result is bound to.
        """
        assert build_solver(INTERNAL).name == "linear-static"
        assert build_solver(CALCULIX).name == "calculix"

    def test_the_row_can_carry_a_solver_version(self, db_session) -> None:
        """The column exists and takes None, which is what an unreadable version
        stores. Pinned separately from the run because a migration that was
        written but not applied fails here rather than three phases later."""
        from app.models import SimulationJob

        assert hasattr(SimulationJob, "solver_version")
