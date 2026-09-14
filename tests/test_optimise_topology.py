"""Topology optimisation by SIMP — `app/optimise/topology.py`, master plan 10.3.

Three closed-form anchors, because a topology has no published answer to check a
picture against:

* **The sensitivity is the derivative.** ∂c/∂ρ against a central finite
  difference of the compliance, element by element.
* **A uniform layout scales exactly.** K(ρ) = (ρ_min + (1 − ρ_min)ρᵖ)K_solid, so
  the compliance of a uniform layout is the solid compliance divided by that
  factor, to round-off.
* **The convex case finds the known optimum.** With penalty 1 the problem is the
  variable-thickness sheet, which is convex. Under uniform uniaxial tension the
  optimum compliance is F²L/(E·v·H·t): the uniform layout reaches it, and so does
  any layout of parallel full-length fibres, so the *compliance* is unique and
  the layout is not — which is why the test reads the number and not the picture.
  Started from a random layout the optimiser must reach that number, and no
  layout may ever beat it.

The penalised case (p = 3) has no closed form. What is pinned there is what the
result claims, plus one bound that is a theorem: ρ³ ≤ ρ on [0, 1], so every
penalised layout is at most as stiff as the same layout unpenalised, and the
penalised optimum can never beat the convex one.
"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

from app.mesh.planar import TriMesh
from app.optimise import VariableError
from app.optimise.topology import (
    CONCEPT_NOT_PART,
    MIN_STIFFNESS_RATIO,
    TopologyProblem,
    TopologyResult,
    compliance,
    density_filter,
    optimise_topology,
)
from app.solve.materials import MATERIALS
from app.solve.plane import PlaneCase, PlaneState, assemble_stiffness, element_stiffness
from app.solve.types import BoxSelector, FaceSelector, Fixture, ForceLoad, SolverError
from tests.test_solver_plane import rectangle_mesh

STEEL = MATERIALS["steel-1018"]
LENGTH, HEIGHT, THICKNESS, FORCE = 100.0, 20.0, 2.0, 1000.0


def _tension() -> PlaneCase:
    """Uniform uniaxial tension: a roller on the left edge, one node held in y."""
    return PlaneCase(
        material=STEEL,
        thickness_mm=THICKNESS,
        state=PlaneState.STRESS,
        fixtures=[
            Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
            Fixture(where=BoxSelector(min=(-0.1, -0.1, -0.1), max=(0.1, 0.1, 0.1)), dofs=["y"]),
        ],
        loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(FORCE, 0.0, 0.0))],
    )


def _cantilever() -> PlaneCase:
    """Clamped on the left, a downward load on a short patch at the right edge's middle."""
    return PlaneCase(
        material=STEEL,
        thickness_mm=THICKNESS,
        state=PlaneState.STRESS,
        fixtures=[Fixture(where=FaceSelector(axis="x", side="min"), kind="clamp")],
        loads=[
            ForceLoad(
                where=BoxSelector(min=(LENGTH - 0.1, 8.9, -0.1), max=(LENGTH + 0.1, 11.1, 0.1)),
                force_n=(0.0, -FORCE, 0.0),
            )
        ],
    )


def _problem(case: PlaneCase, **overrides: object) -> TopologyProblem:
    fields: dict[str, object] = {"case": case, "volume_fraction": 0.4, "filter_radius_mm": 0.0}
    fields.update(overrides)
    return TopologyProblem(**fields)  # type: ignore[arg-type]


class TestTheElementIsThePlaneSolversElement:
    def test_summed_element_matrices_are_the_assembled_stiffness(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 6, 3)
        ke, dofs = element_stiffness(mesh, STEEL, PlaneState.STRESS, THICKNESS)
        assembled = assemble_stiffness(mesh, STEEL, PlaneState.STRESS, THICKNESS).toarray()
        summed = np.zeros_like(assembled)
        for matrix, index in zip(ke, dofs, strict=True):
            summed[np.ix_(index, index)] += matrix
        assert np.allclose(summed, assembled, rtol=0.0, atol=1e-9 * np.abs(assembled).max())


class TestTheSensitivityIsTheDerivative:
    def test_against_a_central_difference_on_every_element(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 5, 2)
        problem = _problem(_cantilever())
        rng = np.random.default_rng(7)
        densities = rng.uniform(0.3, 1.0, mesh.element_count)
        at = compliance(mesh, problem, densities)
        # Measured on this case: the worst relative mismatch is 4.5e-6 at a step of
        # 1e-3 (truncation), 2.1e-7 at 1e-4, and 1.0e-5 at 1e-5 and 2.6e-5 at 1e-6,
        # where the solve's round-off divided by the step takes over. 1e-4 is the
        # bottom of that curve and the tolerance sits fifty times above it.
        step = 1e-4
        for element in range(mesh.element_count):
            up, down = densities.copy(), densities.copy()
            up[element] += step
            down[element] -= step
            numeric = (compliance(mesh, problem, up).value - compliance(mesh, problem, down).value) / (
                2.0 * step
            )
            assert at.gradient[element] == pytest.approx(numeric, rel=1e-5)
        assert np.all(at.gradient <= 0.0), "adding material never makes a part less stiff"


class TestAUniformLayoutScalesExactly:
    @pytest.mark.parametrize(("density", "penalty"), [(0.5, 3.0), (0.25, 1.0), (0.8, 2.0)])
    def test_compliance_is_the_solid_one_over_the_interpolation(self, density: float, penalty: float) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 8, 4)
        problem = _problem(_cantilever(), penalty=penalty)
        solid = compliance(mesh, problem, np.ones(mesh.element_count)).value
        uniform = compliance(mesh, problem, np.full(mesh.element_count, density)).value
        factor = MIN_STIFFNESS_RATIO + (1.0 - MIN_STIFFNESS_RATIO) * density**penalty
        assert uniform == pytest.approx(solid / factor, rel=1e-9)

    def test_the_solid_bar_is_the_closed_form(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 10, 2)
        solid = compliance(mesh, _problem(_tension()), np.ones(mesh.element_count)).value
        E = STEEL.youngs_modulus_mpa
        assert solid == pytest.approx(FORCE**2 * LENGTH / (E * HEIGHT * THICKNESS), rel=1e-9)


class TestTheConvexCaseFindsTheKnownOptimum:
    def test_from_a_random_start_it_walks_to_the_uniform_layout(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 10, 4)
        volume = 0.4
        problem = _problem(_tension(), penalty=1.0, volume_fraction=volume, change_tolerance=1e-4,
                           max_iterations=500)
        rng = np.random.default_rng(3)
        start = rng.uniform(0.15, 0.65, mesh.element_count)
        result = optimise_topology(mesh, problem, start=start)

        E = STEEL.youngs_modulus_mpa
        optimum = FORCE**2 * LENGTH / (E * volume * HEIGHT * THICKNESS)
        assert result.converged, result.message
        assert result.volume_fraction == pytest.approx(volume, abs=1e-6)
        assert result.compliance_n_mm == pytest.approx(optimum, rel=1e-3)
        assert result.compliance_n_mm < result.history[0], "the random start was not already optimal"

    def test_no_layout_it_visits_beats_the_optimum(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 10, 4)
        volume = 0.4
        problem = _problem(_tension(), penalty=1.0, volume_fraction=volume, max_iterations=30)
        start = np.random.default_rng(5).uniform(0.15, 0.65, mesh.element_count)
        result = optimise_topology(mesh, problem, start=start)
        E = STEEL.youngs_modulus_mpa
        optimum = FORCE**2 * LENGTH / (E * volume * HEIGHT * THICKNESS)
        # Only layouts on the volume target are comparable with the bound.
        assert result.compliance_n_mm >= optimum * (1.0 - 1e-9)


PENALISED_MESH = (50, 10)
PENALISED_FILTER_MM = 2.5


@pytest.fixture(scope="module")
def result() -> TopologyResult:
    mesh = rectangle_mesh(LENGTH, HEIGHT, *PENALISED_MESH)
    problem = _problem(_cantilever(), penalty=3.0, volume_fraction=0.4, filter_radius_mm=PENALISED_FILTER_MM)
    return optimise_topology(mesh, problem)


@pytest.fixture(scope="module")
def convex() -> TopologyResult:
    mesh = rectangle_mesh(LENGTH, HEIGHT, *PENALISED_MESH)
    problem = _problem(
        _cantilever(), penalty=1.0, volume_fraction=0.4, filter_radius_mm=PENALISED_FILTER_MM,
        change_tolerance=1e-3, max_iterations=1000,
    )
    return optimise_topology(mesh, problem)


class TestAPenalisedLayoutClaimsOnlyWhatItShows:
    """Measured on this mesh and filter: converges in 172 iterations, grey fraction
    0.51, 16% of the area solid and 33% void, compliance 2739 N·mm against the
    convex optimum's 1662 and a uniform p = 3 layout's 18,937."""

    def test_it_converged_with_the_volume_on_target(self, result: TopologyResult) -> None:
        assert result.converged, result.message
        assert result.volume_fraction == pytest.approx(0.4, abs=1e-6)

    def test_it_never_beats_the_convex_optimum(self, result: TopologyResult, convex: TopologyResult) -> None:
        assert convex.converged, convex.message
        assert result.compliance_n_mm >= convex.compliance_n_mm * (1.0 - 1e-6)

    def test_it_is_far_stiffer_than_the_uniform_layout_of_the_same_material(self, result: TopologyResult) -> None:
        uniform = result.solid_compliance_n_mm / (0.4**3)
        assert result.compliance_n_mm < 0.25 * uniform
        assert result.history[-1] < result.history[0]

    def test_it_moved_towards_solid_and_void(self, result: TopologyResult) -> None:
        # The uniform start is grey everywhere; a filtered layout keeps a grey band
        # about two radii wide around every member, so it is not black and white.
        assert result.grey_fraction < 0.6
        assert float(np.mean(result.densities > 0.9)) > 0.1
        assert float(np.mean(result.densities < 0.1)) > 0.25

    def test_it_says_what_it_is_not(self, result: TopologyResult) -> None:
        assert result.statement == CONCEPT_NOT_PART
        assert CONCEPT_NOT_PART in result.summary()
        for forbidden in ("part", "geometry", "optimum", "solution"):
            assert not hasattr(result, forbidden)


class TestANonConvergedLayoutSaysSo:
    def test_the_iteration_limit_is_not_convergence(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 20, 4)
        result = optimise_topology(mesh, _problem(_cantilever(), filter_radius_mm=6.0, max_iterations=2))
        assert not result.converged
        assert result.iterations == 2
        assert "not a converged one" in result.message
        assert "did NOT converge" in result.summary()

    def test_densities_that_stopped_moving_off_the_volume_target_are_not_convergence(self) -> None:
        """A move limit under the change tolerance: every step is 'small' from the
        first iteration, and the volume is still far from its target."""
        mesh = rectangle_mesh(LENGTH, HEIGHT, 20, 4)
        problem = _problem(_cantilever(), filter_radius_mm=6.0, move_limit=0.005, max_iterations=5)
        result = optimise_topology(mesh, problem, start=np.full(mesh.element_count, 0.9))
        assert not result.converged
        assert result.iterations == 5
        assert result.volume_fraction > problem.volume_fraction + 0.1


class TestTheFilter:
    def test_radius_zero_is_the_identity(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        assert np.array_equal(density_filter(mesh, 0.0).toarray(), np.eye(mesh.element_count))

    def test_rows_sum_to_one_and_a_uniform_field_passes_unchanged(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 12, 4)
        weights = density_filter(mesh, 12.0)
        assert np.allclose(np.asarray(weights.sum(axis=1)).ravel(), 1.0)
        assert np.allclose(weights @ np.full(mesh.element_count, 0.37), 0.37)

    def test_it_reaches_only_within_the_radius(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 12, 4)
        weights = density_filter(mesh, 10.0).toarray()
        centroids = mesh.nodes[mesh.tris][:, :, :2].mean(axis=1)
        distance = np.linalg.norm(centroids[:, None, :] - centroids[None, :, :], axis=2)
        assert np.all(weights[distance >= 10.0] == 0.0)
        assert np.all(weights[distance < 10.0] > 0.0)

    def test_a_larger_neighbour_counts_for_more(self) -> None:
        """On a graded mesh the weight is cone × area, row-normalised — checked
        element by element against the definition, not against a uniform mesh
        where every area is the same and the weighting cannot be seen."""
        xs = np.array([0.0, 1.0, 2.5, 5.0, 9.0, 15.0])
        ys = np.array([0.0, 2.0, 4.0])
        nodes = np.array([[x, y, 0.0] for x in xs for y in ys])
        tris = []
        for i in range(len(xs) - 1):
            for j in range(len(ys) - 1):
                a, b = i * len(ys) + j, (i + 1) * len(ys) + j
                c, d = b + 1, a + 1
                tris.extend([[a, b, c], [a, c, d]])
        mesh = TriMesh(nodes=nodes, tris=np.array(tris))
        radius = 7.0
        weights = density_filter(mesh, radius).toarray()

        centroids = mesh.nodes[mesh.tris][:, :, :2].mean(axis=1)
        areas = np.abs(mesh.signed_areas())
        distance = np.linalg.norm(centroids[:, None, :] - centroids[None, :, :], axis=2)
        cone = np.maximum(0.0, radius - distance) * areas[None, :]
        assert np.allclose(weights, cone / cone.sum(axis=1, keepdims=True), rtol=1e-12, atol=1e-15)


class TestAStatementThatCannotBeOptimisedIsRefused:
    @pytest.mark.parametrize("volume", [0.0, 1.0, -0.2, 1.5])
    def test_a_volume_fraction_with_nothing_to_decide(self, volume: float) -> None:
        with pytest.raises(VariableError, match="strictly between 0 and 1"):
            _problem(_cantilever(), volume_fraction=volume)

    def test_a_penalty_that_rewards_grey(self) -> None:
        with pytest.raises(VariableError, match="rewards intermediate density"):
            _problem(_cantilever(), penalty=0.5)

    def test_a_negative_filter_radius(self) -> None:
        with pytest.raises(VariableError, match="not a length"):
            _problem(_cantilever(), filter_radius_mm=-1.0)

    def test_a_thermal_load_whose_sensitivity_is_not_modelled(self) -> None:
        case = _cantilever().model_copy(update={"delta_t_k": 50.0})
        with pytest.raises(VariableError, match="depend on the\\s+design"):
            _problem(case)

    def test_a_load_case_with_no_force(self) -> None:
        case = _cantilever().model_copy(
            update={"loads": [ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, 0.0, 0.0))]}
        )
        with pytest.raises(VariableError, match="applies no force"):
            optimise_topology(rectangle_mesh(LENGTH, HEIGHT, 4, 2), _problem(case))

    def test_an_under_constrained_design_space(self) -> None:
        # A roller that leaves the part free to slide in y, under a load in y.
        case = _cantilever().model_copy(
            update={"fixtures": [Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"])]}
        )
        with pytest.raises(SolverError, match="under-constrained"):
            optimise_topology(rectangle_mesh(LENGTH, HEIGHT, 4, 2), _problem(case))

    def test_a_solve_the_factorisation_calls_singular_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scipy.sparse.linalg as spla

        def singular(*args: object, **kwargs: object) -> None:
            warnings.warn("Matrix is exactly singular", spla.MatrixRankWarning, stacklevel=1)

        monkeypatch.setattr(spla, "spsolve", singular)
        with pytest.raises(SolverError, match="under-constrained.*exactly singular"):
            compliance(rectangle_mesh(LENGTH, HEIGHT, 4, 2), _problem(_cantilever()), np.ones(16))

    def test_a_solution_that_is_not_in_equilibrium_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SuperLU hands back a finite, meaningless vector for a singular system
        without always warning; equilibrium is the check that does not depend on it."""
        import scipy.sparse.linalg as spla

        monkeypatch.setattr(spla, "spsolve", lambda matrix, rhs, **kwargs: np.ones(len(rhs)))
        with pytest.raises(SolverError, match="does not satisfy equilibrium"):
            compliance(rectangle_mesh(LENGTH, HEIGHT, 4, 2), _problem(_cantilever()), np.ones(16))

    def test_densities_of_the_wrong_shape(self) -> None:
        mesh = rectangle_mesh(LENGTH, HEIGHT, 4, 2)
        with pytest.raises(VariableError, match="one per element"):
            compliance(mesh, _problem(_cantilever()), np.ones(3))
        with pytest.raises(VariableError, match="one density in \\[0, 1\\] per element"):
            optimise_topology(mesh, _problem(_cantilever()), start=np.full(mesh.element_count, 2.0))
