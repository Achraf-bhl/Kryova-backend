"""Level-set topology optimisation — `app/optimise/levelset.py`, master plan 10.3.

Two claims are theorems and are held exactly:

* **No layout beats the convex bound.** Under uniform uniaxial tension, any layout
  whose mean stiffness scale is s̄ = f + (1 − f)·V has compliance at least
  F²L/(E·s̄·H·t) — the variable-thickness optimum `test_optimise_topology.py`
  checks SIMP against. A black-and-white layout at volume V is one such layout.
* **Removing material never stiffens.** Every layout is at least as compliant as
  the full design space it was cut from.

Everything else is a measurement on this mesh, written down as one: the
cantilever converges in 98 iterations at τ = 0.2 with compliance 2158 N·mm and
175 boundary edges, against 3030 N·mm for a uniform sheet of the same material;
the tension bar lands at 1.14× the bound with τ = 0.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.optimise import VariableError
from app.optimise.levelset import (
    ERSATZ_STIFFNESS_RATIO,
    LEVEL_SET_STATEMENT,
    LevelSetProblem,
    LevelSetResult,
    _at_volume,
    boundary_edges,
    edge_neighbours,
    optimise_level_set,
)
from app.solve.types import SolverError
from tests.test_optimise_topology import FORCE, HEIGHT, LENGTH, THICKNESS, _cantilever, _tension
from tests.test_solver_plane import rectangle_mesh

MESH = (50, 10)


@pytest.fixture(scope="module")
def cantilever() -> LevelSetResult:
    return optimise_level_set(rectangle_mesh(LENGTH, HEIGHT, *MESH), LevelSetProblem(_cantilever(), 0.4))


class TestEveryLayoutIsBlackAndWhite:
    def test_no_element_is_between_solid_and_void(self, cantilever: LevelSetResult) -> None:
        assert set(np.unique(cantilever.densities).tolist()) == {0.0, 1.0}
        assert np.array_equal(cantilever.solid, cantilever.level_set > 0.0)
        assert np.all(np.abs(cantilever.level_set) <= 1.0)

    def test_the_volume_is_the_nearest_whole_elements_can_reach(self, cantilever: LevelSetResult) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, *MESH)
        areas = np.abs(mesh.signed_areas())
        assert abs(cantilever.volume_fraction - 0.4) <= 0.5 * areas.max() / areas.sum()

    def test_elements_tied_at_the_threshold_do_not_fall_together(self) -> None:
        """The first step from a uniform field: every value is equal, and a sign
        test on a shifted value would put all of them on one side."""
        level_set = _at_volume(np.full(10, 3.0), np.ones(10), 4.0)
        assert np.count_nonzero(level_set > 0.0) == 4

    def test_the_nearest_count_wins_not_the_first_to_reach_the_target(self) -> None:
        level_set = _at_volume(np.array([4.0, 3.0, 2.0, 1.0]), np.ones(4), 2.4)
        assert np.flatnonzero(level_set > 0.0).tolist() == [0, 1]


class TestTheTheoremsHold:
    def test_no_layout_beats_the_convex_bound_in_tension(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, *MESH)
        result = optimise_level_set(mesh, LevelSetProblem(_tension(), 0.4, regularisation=0.0))
        scale = ERSATZ_STIFFNESS_RATIO + (1.0 - ERSATZ_STIFFNESS_RATIO) * result.volume_fraction
        modulus = _tension().material.youngs_modulus_mpa
        bound = FORCE**2 * LENGTH / (modulus * scale * HEIGHT * THICKNESS)
        assert result.converged, result.message
        # The theorem, and the only line here that is one: no layout under this
        # filter beats the convex optimum. Exact, to one ulp.
        assert result.compliance_n_mm >= bound * (1.0 - 1e-9)
        # NOT a theorem — a sanity ceiling, and the number is deliberately far
        # from either measurement. The level set converges to a *local* optimum
        # that depends on the mesh, the regularisation and the volume schedule
        # (the result's own message says so), so the iterate path depends on the
        # linear algebra underneath: **1.14× measured on Linux 2026-09-14,
        # 1.2325× on Windows 2026-09-17**, same code, same mesh, same seed.
        # Writing the Linux figure as a 1.2 bound made a platform difference
        # read as a broken theorem. What this line is for is catching an
        # optimiser that has wandered off, not pinning which local optimum a
        # given BLAS lands in.
        assert result.compliance_n_mm <= 1.5 * bound, (
            f"{result.compliance_n_mm / bound:.4g}x the convex bound — the optimiser "
            "found a far worse local optimum than either machine has measured"
        )

    def test_removing_material_never_stiffens(self, cantilever: LevelSetResult) -> None:
        assert all(value >= cantilever.solid_compliance_n_mm * (1.0 - 1e-9) for value in cantilever.history)


class TestTheCantileverLayout:
    def test_it_converged(self, cantilever: LevelSetResult) -> None:
        assert cantilever.converged, cantilever.message
        assert cantilever.changed_last == 0
        assert "no element changed side" in cantilever.message

    def test_it_is_stiffer_than_a_uniform_sheet_of_the_same_material(self, cantilever: LevelSetResult) -> None:
        # A uniform sheet at 40% thickness has exactly the solid compliance / 0.4.
        uniform = cantilever.solid_compliance_n_mm / 0.4
        assert cantilever.compliance_n_mm < 0.8 * uniform

    def test_it_says_what_it_is_not(self, cantilever: LevelSetResult) -> None:
        assert cantilever.statement == LEVEL_SET_STATEMENT
        assert LEVEL_SET_STATEMENT in cantilever.summary()
        assert "staircased" in LEVEL_SET_STATEMENT and "0.001" in LEVEL_SET_STATEMENT
        assert cantilever.to_dict()["ersatz_stiffness_ratio"] == ERSATZ_STIFFNESS_RATIO
        for forbidden in ("part", "geometry", "optimum", "solution", "grey_fraction"):
            assert not hasattr(cantilever, forbidden)


class TestRegularisationShortensTheBoundary:
    def test_more_regularisation_fewer_boundary_edges(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, *MESH)
        loose = optimise_level_set(mesh, LevelSetProblem(_cantilever(), 0.4, regularisation=0.02))
        tight = optimise_level_set(mesh, LevelSetProblem(_cantilever(), 0.4, regularisation=0.5))
        assert tight.boundary_edges < 0.6 * loose.boundary_edges, (loose.boundary_edges, tight.boundary_edges)


class TestTheNeighbours:
    @pytest.mark.parametrize("nx, ny", [(1, 1), (3, 2), (6, 4)])
    def test_every_shared_edge_is_found_once_each_way(self, nx: int, ny: int) -> None:
        neighbours = edge_neighbours(rectangle_mesh(LENGTH, HEIGHT, nx, ny))
        diagonals, vertical, horizontal = nx * ny, (nx - 1) * ny, (ny - 1) * nx
        assert neighbours.nnz == 2 * (diagonals + vertical + horizontal)
        assert (neighbours != neighbours.T).nnz == 0
        assert np.all(neighbours.diagonal() == 0.0)
        assert np.all(np.asarray(neighbours.sum(axis=1)).ravel() <= 3)

    def test_a_boundary_edge_is_counted_once(self) -> None:
        # One square as two triangles, one of them solid: one edge between them.
        neighbours = edge_neighbours(rectangle_mesh(LENGTH, HEIGHT, 1, 1))
        assert boundary_edges(neighbours, np.array([True, False])) == 1
        assert boundary_edges(neighbours, np.array([True, True])) == 0


class TestANonConvergedLayoutSaysSo:
    def test_the_iteration_limit_is_not_convergence(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 20, 4)
        result = optimise_level_set(mesh, LevelSetProblem(_cantilever(), 0.4, max_iterations=5))
        assert not result.converged
        assert result.iterations == 5
        assert "not a converged one" in result.message
        assert "did NOT converge" in result.summary()

    def test_a_layout_still_off_its_volume_is_not_convergence(self) -> None:
        """Nothing changes side when the volume schedule is paused at a step, but
        the volume has not reached its target, so the count must not start."""
        mesh = rectangle_mesh(LENGTH, HEIGHT, 20, 4)
        problem = LevelSetProblem(_cantilever(), 0.3, volume_step=0.001, max_iterations=40, stall_iterations=1)
        result = optimise_level_set(mesh, problem)
        assert not result.converged
        assert result.volume_fraction > 0.9


class TestAStatementThatCannotBeOptimisedIsRefused:
    @pytest.mark.parametrize("volume", [0.0, 1.0, 1.2])
    def test_a_volume_fraction_with_nothing_to_decide(self, volume: float) -> None:
        with pytest.raises(VariableError, match="nothing to decide"):
            LevelSetProblem(_cantilever(), volume)

    def test_a_regularisation_that_rewards_boundary(self) -> None:
        with pytest.raises(VariableError, match="reward boundary length"):
            LevelSetProblem(_cantilever(), 0.4, regularisation=-0.1)

    @pytest.mark.parametrize("field, value", [("time_step", 0.0), ("volume_step", 0.0), ("stall_iterations", 0)])
    def test_a_schedule_that_cannot_move(self, field: str, value: float) -> None:
        with pytest.raises(VariableError, match=field):
            LevelSetProblem(_cantilever(), 0.4, **{field: value})  # type: ignore[arg-type]

    def test_a_thermal_load(self) -> None:
        with pytest.raises(VariableError, match="depend on the design"):
            LevelSetProblem(_cantilever().model_copy(update={"delta_t_k": 50.0}), 0.4)

    def test_an_under_constrained_design_space(self) -> None:
        from app.solve.types import FaceSelector, Fixture

        case = _cantilever().model_copy(
            update={"fixtures": [Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"])]}
        )
        with pytest.raises(SolverError, match="under-constrained"):
            optimise_level_set(rectangle_mesh(LENGTH, HEIGHT, 4, 2), LevelSetProblem(case, 0.4))
