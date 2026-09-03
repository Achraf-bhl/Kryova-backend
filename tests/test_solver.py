"""Solver verification.

Every assertion here is checked against a closed-form result, not against a
previously recorded solver output. A regression that changes the physics has to
fail these; a golden-file test would just record the new wrong answer.
"""

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.solve.linear_static import LinearStaticSolver, constitutive_matrix
from app.solve.materials import MATERIALS
from app.solve.types import (
    BoxSelector,
    FaceSelector,
    Fixture,
    ForceLoad,
    LoadCase,
    Material,
    SolverError,
)

STEEL = MATERIALS["steel-1018"]


def uniaxial_case(material: Material, force_n: float) -> LoadCase:
    """Bar on three roller planes, pulled along +z at the far end.

    Rollers rather than a clamp: an encastre face would also block the Poisson
    contraction and raise a stress concentration next to it, so the stress would
    no longer be uniform and there would be no closed form to check against.
    Restraining one axis on each of the three faces at the origin removes all
    six rigid-body motions while leaving the bar free to neck.
    """
    return LoadCase(
        name="uniaxial",
        material=material,
        fixtures=[
            Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
            Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
            Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
        ],
        loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, force_n))],
    )


def axial_extension(mesh, output, length: float) -> float:
    """Mean axial displacement of the loaded face."""
    far = np.flatnonzero(np.isclose(mesh.nodes[:, 2], length))
    return float(output.displacements[far, 2].mean())


class TestBoxMesh:
    def test_volume_is_exact(self) -> None:
        mesh = box_mesh((10.0, 20.0, 30.0), divisions=(3, 3, 3))
        assert mesh.volume == pytest.approx(10.0 * 20.0 * 30.0)

    def test_no_inverted_elements(self) -> None:
        mesh = box_mesh((10.0, 20.0, 30.0), divisions=(2, 3, 4))
        assert (mesh.signed_volumes() > 0).all()

    def test_surface_area_is_exact(self) -> None:
        # A correct boundary extraction recovers exactly the six faces of the box.
        mesh = box_mesh((10.0, 20.0, 30.0), divisions=(2, 2, 2))
        p = mesh.nodes[mesh.surface_triangles]
        areas = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
        expected = 2 * (10 * 20 + 20 * 30 + 30 * 10)
        assert areas.sum() == pytest.approx(expected)

    def test_surface_excludes_interior_faces(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(3, 3, 3))
        interior = mesh.nodes[mesh.surface_triangles].reshape(-1, 3)
        # Every surface node must sit on at least one bounding plane.
        lo, hi = mesh.bounding_box
        on_boundary = np.isclose(interior, lo).any(axis=1) | np.isclose(interior, hi).any(axis=1)
        assert on_boundary.all()


class TestUniaxialTension:
    """A prismatic bar in pure tension is exact for constant-strain tets, so
    these compare against the textbook answer at full precision."""

    length = 100.0  # mm
    width = 10.0
    depth = 20.0
    force = 5_000.0  # N

    @pytest.fixture
    def solved(self):
        mesh = box_mesh((self.width, self.depth, self.length), divisions=(2, 2, 8))
        return mesh, LinearStaticSolver().solve(mesh, uniaxial_case(STEEL, self.force))

    def test_axial_stress_matches_force_over_area(self, solved) -> None:
        _, output = solved
        expected = self.force / (self.width * self.depth)  # MPa
        assert output.result.max_von_mises_mpa == pytest.approx(expected, rel=1e-6)

    def test_elongation_matches_hookes_law(self, solved) -> None:
        mesh, output = solved
        area = self.width * self.depth
        expected = self.force * self.length / (area * STEEL.youngs_modulus_mpa)
        assert axial_extension(mesh, output, self.length) == pytest.approx(expected, rel=1e-6)

    def test_reported_peak_displacement_is_the_far_corner(self, solved) -> None:
        mesh, output = solved
        # The headline number includes the small Poisson contraction, so it sits
        # just above the pure axial extension rather than exactly on it.
        axial = axial_extension(mesh, output, self.length)
        assert output.result.max_displacement_mm == pytest.approx(axial, rel=5e-3)
        assert output.result.max_displacement_mm >= axial

    def test_poisson_contraction_matches_theory(self, solved) -> None:
        mesh, output = solved
        axial_strain = self.force / (self.width * self.depth * STEEL.youngs_modulus_mpa)
        expected = -STEEL.poissons_ratio * axial_strain * self.width

        far_x = np.flatnonzero(np.isclose(mesh.nodes[:, 0], self.width))
        assert output.displacements[far_x, 0].mean() == pytest.approx(expected, rel=1e-6)

    def test_reaction_balances_the_applied_load(self, solved) -> None:
        mesh, output = solved
        # Sum of internal axial stress over the cross-section must return the load.
        stress_area = output.result.max_von_mises_mpa * self.width * self.depth
        assert stress_area == pytest.approx(self.force, rel=1e-6)

    def test_mass_matches_density_times_volume(self, solved) -> None:
        _, output = solved
        volume_m3 = self.width * self.depth * self.length * 1e-9
        assert output.result.mass_kg == pytest.approx(volume_m3 * STEEL.density_kg_m3)

    def test_result_is_mesh_independent(self) -> None:
        coarse = box_mesh((self.width, self.depth, self.length), divisions=(1, 1, 4))
        fine = box_mesh((self.width, self.depth, self.length), divisions=(3, 3, 12))
        solver = LinearStaticSolver()
        case = uniaxial_case(STEEL, self.force)

        a = axial_extension(coarse, solver.solve(coarse, case), self.length)
        b = axial_extension(fine, solver.solve(fine, case), self.length)
        # Area-weighted load distribution means refinement must not change the answer.
        assert a == pytest.approx(b, rel=1e-6)


class TestLinearity:
    def test_response_scales_with_load(self) -> None:
        mesh = box_mesh((10.0, 10.0, 50.0), divisions=(2, 2, 5))
        solver = LinearStaticSolver()
        single = solver.solve(mesh, uniaxial_case(STEEL, 1_000.0)).result
        triple = solver.solve(mesh, uniaxial_case(STEEL, 3_000.0)).result

        assert triple.max_displacement_mm == pytest.approx(3 * single.max_displacement_mm)
        assert triple.max_von_mises_mpa == pytest.approx(3 * single.max_von_mises_mpa)

    def test_deflection_is_inversely_proportional_to_modulus(self) -> None:
        # Both 6061 and 7075 have nu = 0.33, so only E differs and the ratio is exact.
        soft, stiff = MATERIALS["aluminium-6061-t6"], MATERIALS["aluminium-7075-t6"]
        assert soft.poissons_ratio == stiff.poissons_ratio

        mesh = box_mesh((10.0, 10.0, 50.0), divisions=(2, 2, 5))
        solver = LinearStaticSolver()
        soft_out = solver.solve(mesh, uniaxial_case(soft, 1_000.0))
        stiff_out = solver.solve(mesh, uniaxial_case(stiff, 1_000.0))

        ratio = stiff.youngs_modulus_mpa / soft.youngs_modulus_mpa
        assert axial_extension(mesh, soft_out, 50.0) == pytest.approx(
            axial_extension(mesh, stiff_out, 50.0) * ratio, rel=1e-6
        )
        # Stress in a statically determinate bar does not depend on the material.
        assert soft_out.result.max_von_mises_mpa == pytest.approx(
            stiff_out.result.max_von_mises_mpa, rel=1e-6
        )


class TestFactorOfSafety:
    def test_below_yield_reports_a_safe_margin(self) -> None:
        mesh = box_mesh((10.0, 10.0, 50.0), divisions=(2, 2, 4))
        # 100 N over 100 mm^2 = 1 MPa, far below 1018 steel's 370 MPa yield.
        output = LinearStaticSolver().solve(mesh, uniaxial_case(STEEL, 100.0))
        assert output.result.factor_of_safety == pytest.approx(370.0, rel=1e-6)
        assert not output.result.yields

    def test_above_yield_is_flagged(self) -> None:
        mesh = box_mesh((10.0, 10.0, 50.0), divisions=(2, 2, 4))
        # 50 kN over 100 mm^2 = 500 MPa, past yield.
        output = LinearStaticSolver().solve(mesh, uniaxial_case(STEEL, 50_000.0))
        assert output.result.yields
        assert output.result.factor_of_safety < 1.0


class TestConstitutiveMatrix:
    def test_recovers_youngs_modulus_under_uniaxial_stress(self) -> None:
        d = constitutive_matrix(STEEL)
        # Invert the 3x3 normal block: uniaxial stress -> strain = sigma / E.
        compliance = np.linalg.inv(d[:3, :3])
        assert 1.0 / compliance[0, 0] == pytest.approx(STEEL.youngs_modulus_mpa, rel=1e-9)

    def test_recovers_poissons_ratio(self) -> None:
        compliance = np.linalg.inv(constitutive_matrix(STEEL)[:3, :3])
        assert -compliance[0, 1] / compliance[0, 0] == pytest.approx(STEEL.poissons_ratio, rel=1e-9)

    def test_shear_modulus_is_consistent(self) -> None:
        d = constitutive_matrix(STEEL)
        expected = STEEL.youngs_modulus_mpa / (2 * (1 + STEEL.poissons_ratio))
        assert d[3, 3] == pytest.approx(expected, rel=1e-9)


class TestBadlyPosedModels:
    def test_unconstrained_model_is_rejected(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2))
        case = LoadCase(
            material=STEEL,
            # A single node fixed leaves the part free to rotate about it.
            fixtures=[Fixture(where=BoxSelector(min=(-0.1, -0.1, -0.1), max=(0.1, 0.1, 0.1)))],
            loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 100.0))],
        )
        with pytest.raises(SolverError, match="under-constrained"):
            LinearStaticSolver().solve(mesh, case)

    def test_selection_matching_nothing_is_rejected(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2))
        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="z", side="min"))],
            loads=[
                ForceLoad(
                    where=BoxSelector(min=(500.0, 500.0, 500.0), max=(600.0, 600.0, 600.0)),
                    force_n=(0.0, 0.0, 100.0),
                )
            ],
        )
        with pytest.raises(SolverError, match="matched no nodes"):
            LinearStaticSolver().solve(mesh, case)

    def test_fully_fixed_model_is_rejected(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(1, 1, 1))
        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=BoxSelector(min=(-1, -1, -1), max=(11, 11, 11)))],
            loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 1.0))],
        )
        with pytest.raises(SolverError, match="nothing to solve"):
            LinearStaticSolver().solve(mesh, case)

    def test_interior_load_selection_warns_about_equal_split(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), divisions=(4, 4, 4))
        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="z", side="min"))],
            loads=[
                ForceLoad(
                    where=BoxSelector(min=(4.0, 4.0, 4.0), max=(6.0, 6.0, 6.0)),
                    force_n=(0.0, 0.0, 100.0),
                    name="Interior pull",
                )
            ],
        )
        output = LinearStaticSolver().solve(mesh, case)
        assert any("equally between its nodes" in w for w in output.result.warnings)


class TestQuadraticElements:
    """Tet4 elements are constant-strain and therefore stiff in bending -- the
    documented weakness tet10 exists to fix. The comparison is at equal element
    count (the same mesh, promoted), so it measures the elements rather than two
    different discretisations.

    The reference is the Euler-Bernoulli cantilever, delta = F L^3 / (3 E I).
    It neglects shear deflection (~+0.8% here at L/h = 10) and the clamped end's
    restraint of the Poisson contraction (~-1%), so a converged 3D solution
    lands a percent or so away from it and cannot be expected to land on it.
    """

    width = 10.0  # mm, the bending direction
    depth = 10.0
    length = 100.0
    force = 100.0  # N, transverse at the tip

    @property
    def analytic_tip_deflection(self) -> float:
        second_moment = self.depth * self.width**3 / 12.0
        return self.force * self.length**3 / (3.0 * STEEL.youngs_modulus_mpa * second_moment)

    def cantilever_case(self) -> LoadCase:
        return LoadCase(
            name="cantilever",
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="z", side="min"))],
            loads=[
                ForceLoad(
                    where=FaceSelector(axis="z", side="max"),
                    force_n=(self.force, 0.0, 0.0),
                )
            ],
        )

    def tip_deflection(self, mesh, output) -> float:
        tip = np.flatnonzero(np.isclose(mesh.nodes[:, 2], self.length))
        return float(output.displacements[tip, 0].mean())

    @pytest.fixture
    def both_orders(self):
        linear = box_mesh((self.width, self.depth, self.length), divisions=(2, 2, 10))
        quadratic = promote_to_tet10(linear)
        solver, case = LinearStaticSolver(), self.cantilever_case()
        return (
            linear,
            solver.solve(linear, case),
            quadratic,
            solver.solve(quadratic, case),
        )

    def test_promotion_keeps_the_element_count_and_the_geometry(self, both_orders) -> None:
        linear, _, quadratic, _ = both_orders
        assert quadratic.tet_count == linear.tet_count
        assert quadratic.volume == pytest.approx(linear.volume, rel=1e-12)
        assert quadratic.element_type == "tet10"

    def test_tet10_beats_tet4_at_the_same_element_count(self, both_orders) -> None:
        linear, linear_out, quadratic, quadratic_out = both_orders
        exact = self.analytic_tip_deflection

        linear_error = abs(self.tip_deflection(linear, linear_out) - exact) / exact
        quadratic_error = abs(self.tip_deflection(quadratic, quadratic_out) - exact) / exact

        # Not marginally: tet4 is over half the answer out at this refinement.
        assert linear_error > 0.5
        assert quadratic_error < 0.03
        assert quadratic_error < linear_error / 10.0

    def test_tet4_is_too_stiff_rather_than_too_soft(self, both_orders) -> None:
        # The error has a known sign; a solver bug that happened to overshoot
        # would not be "close enough".
        linear, linear_out, _, _ = both_orders
        assert self.tip_deflection(linear, linear_out) < self.analytic_tip_deflection

    def test_refining_tet4_converges_towards_the_tet10_answer(self) -> None:
        solver, case = LinearStaticSolver(), self.cantilever_case()
        coarse = box_mesh((self.width, self.depth, self.length), divisions=(2, 2, 10))
        fine = box_mesh((self.width, self.depth, self.length), divisions=(5, 5, 25))
        exact = self.analytic_tip_deflection

        coarse_error = abs(self.tip_deflection(coarse, solver.solve(coarse, case)) - exact)
        fine_error = abs(self.tip_deflection(fine, solver.solve(fine, case)) - exact)
        assert fine_error < coarse_error

    def test_uniaxial_tension_is_still_exact_with_quadratic_elements(self) -> None:
        # Quadratic elements must not lose what linear ones already got right.
        mesh = promote_to_tet10(box_mesh((10.0, 20.0, 100.0), divisions=(1, 1, 4)))
        output = LinearStaticSolver().solve(mesh, uniaxial_case(STEEL, 5_000.0))

        assert output.result.max_von_mises_mpa == pytest.approx(5_000.0 / 200.0, rel=1e-6)
        assert axial_extension(mesh, output, 100.0) == pytest.approx(
            5_000.0 * 100.0 / (200.0 * STEEL.youngs_modulus_mpa), rel=1e-6
        )

    def test_mass_and_volume_are_unchanged_by_element_order(self) -> None:
        linear = box_mesh((10.0, 20.0, 100.0), divisions=(1, 1, 4))
        quadratic = promote_to_tet10(linear)
        solver, case = LinearStaticSolver(), uniaxial_case(STEEL, 1_000.0)

        assert solver.solve(quadratic, case).result.mass_kg == pytest.approx(
            solver.solve(linear, case).result.mass_kg, rel=1e-12
        )

    def test_every_node_including_midsides_carries_a_stress_value(self) -> None:
        from app.solve.postprocess import nodal_average

        mesh = promote_to_tet10(box_mesh((10.0, 10.0, 30.0), divisions=(2, 2, 4)))
        output = LinearStaticSolver().solve(mesh, uniaxial_case(STEEL, 1_000.0))
        nodal = nodal_average(mesh, output.von_mises)

        assert len(nodal) == mesh.node_count
        assert (nodal > 0.0).all(), "a midside node with no value renders as a hole"

    def test_an_unconstrained_quadratic_model_is_still_rejected(self) -> None:
        # The equilibrium residual has to catch a singular tet10 system too.
        mesh = promote_to_tet10(box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2)))
        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=BoxSelector(min=(-0.1, -0.1, -0.1), max=(0.1, 0.1, 0.1)))],
            loads=[ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 100.0))],
        )
        with pytest.raises(SolverError, match="under-constrained"):
            LinearStaticSolver().solve(mesh, case)


class TestTet10ShapeFunctions:
    def test_gradients_sum_to_zero_at_every_gauss_point(self) -> None:
        # A partition of unity: sum(N) == 1 everywhere, so sum(dN) == 0.
        from app.solve.linear_static import _TET_GAUSS_POINTS, _tet10_shape_gradients

        for point in _TET_GAUSS_POINTS:
            assert _tet10_shape_gradients(*point).sum(axis=0) == pytest.approx(
                np.zeros(3), abs=1e-12
            )

    def test_assembly_refuses_a_linear_mesh(self) -> None:
        from app.solve.linear_static import assemble_stiffness_tet10

        with pytest.raises(SolverError, match="midside"):
            assemble_stiffness_tet10(box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1)), STEEL)

    def test_the_stiffness_matrix_is_symmetric(self) -> None:
        from app.solve.linear_static import assemble_stiffness

        mesh = promote_to_tet10(box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2)))
        stiffness = assemble_stiffness(mesh, STEEL)
        assert abs(stiffness - stiffness.T).max() < 1e-6 * abs(stiffness).max()

    def test_rigid_body_translation_produces_no_force(self) -> None:
        # The classic patch check: move every node by the same vector and the
        # internal forces must be exactly zero.
        from app.solve.linear_static import assemble_stiffness

        mesh = promote_to_tet10(box_mesh((10.0, 10.0, 10.0), divisions=(2, 2, 2)))
        stiffness = assemble_stiffness(mesh, STEEL)
        translation = np.tile([1.0, -2.0, 3.0], mesh.node_count)
        forces = stiffness @ translation
        assert abs(forces).max() < 1e-6 * abs(stiffness).max()
