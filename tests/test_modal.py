"""Modal analysis, checked against closed-form solutions rather than recorded output.

The same standard the static solver is held to: a bar in axial vibration and a
cantilever in bending both have exact analytic frequencies, so the solver is
compared with those and not with numbers a previous run happened to produce.

These never ask for a database fixture, so they run offline in under a second.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.solve.materials import MATERIALS
from app.solve.modal import (
    RIGID_BODY_HZ,
    ModalEigenSolver,
    assemble_mass,
    bar_axial_hz,
    beam_bending_hz,
)
from app.solve.types import FaceSelector, Fixture, ModalCase, SolverError

STEEL = MATERIALS["steel-1018"]
ALUMINIUM = MATERIALS["aluminium-6061-t6"]


def clamped_at_x_min() -> list[Fixture]:
    return [Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x", "y", "z"])]


class TestMassMatrix:
    """The mass matrix is the half of the eigenproblem the static solver never
    exercised, so it is checked on its own before any frequency is trusted."""

    @pytest.mark.parametrize("order", [1, 2])
    def test_total_mass_is_the_part_mass(self, order: int) -> None:
        # Sum of every entry = 3x the mass, because each of the three directions
        # carries the whole of it. This is the check that catches the unit
        # conversion: get the 1e-12 wrong and this is out by 1e12.
        mesh = box_mesh((60.0, 20.0, 10.0), (3, 2, 2))
        if order == 2:
            mesh = promote_to_tet10(mesh)

        mass = assemble_mass(mesh, STEEL)
        expected_tonnes = mesh.volume * STEEL.density_kg_m3 * 1e-12

        assert mass.sum() == pytest.approx(3.0 * expected_tonnes, rel=1e-12)

    @pytest.mark.parametrize("order", [1, 2])
    def test_mass_matrix_is_symmetric_and_positive_definite(self, order: int) -> None:
        mesh = box_mesh((20.0, 10.0, 10.0), (2, 1, 1))
        if order == 2:
            mesh = promote_to_tet10(mesh)

        mass = assemble_mass(mesh, STEEL).toarray()

        assert np.allclose(mass, mass.T, rtol=0, atol=1e-18)
        # Positive definite: a mass matrix that is not would let a mode have
        # zero or negative kinetic energy.
        assert np.all(np.linalg.eigvalsh(mass) > 0.0)

    def test_a_material_with_no_density_is_refused(self) -> None:
        weightless = STEEL.model_copy(update={"density_kg_m3": 0.0})
        with pytest.raises(SolverError, match="no density"):
            assemble_mass(box_mesh((10.0, 10.0, 10.0), (1, 1, 1)), weightless)


class TestAxialVibration:
    """A clamped-free bar. Its axial frequency depends on E, rho and length only
    -- not on the cross-section -- so a wrong area cannot hide in it."""

    def test_first_axial_mode_matches_the_closed_form(self) -> None:
        length = 200.0
        mesh = promote_to_tet10(box_mesh((length, 20.0, 20.0), (10, 2, 2)))
        case = ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=6)

        out = ModalEigenSolver().solve(mesh, case)
        exact = bar_axial_hz(length, STEEL)

        # The axial mode is not the lowest -- bending is far softer -- so it is
        # found by matching, not by taking frequencies[0].
        found = min(out.result.frequencies_hz, key=lambda f: abs(f - exact))
        assert found == pytest.approx(exact, rel=0.02)

    def test_frequency_scales_with_the_square_root_of_stiffness(self) -> None:
        """Every frequency scales as sqrt(E/rho). A solver with E or rho in the
        wrong place still passes a single-material test and fails this one.

        The two materials differ *only* in E and density. Using the library's
        steel and aluminium instead looked equivalent and is not: their Poisson
        ratios are 0.29 and 0.33, and in three-dimensional elasticity nu enters
        the stiffness, so the ratio comes out 0.4% off sqrt(E/rho) for reasons
        that have nothing to do with the mass matrix this test is checking.
        """
        soft = STEEL.model_copy(
            update={"name": "test-soft", "youngs_modulus_mpa": 70_000.0, "density_kg_m3": 2700.0}
        )
        stiff = STEEL.model_copy(
            update={"name": "test-stiff", "youngs_modulus_mpa": 210_000.0, "density_kg_m3": 7800.0}
        )
        mesh = promote_to_tet10(box_mesh((200.0, 20.0, 20.0), (10, 2, 2)))
        fixtures = clamped_at_x_min()

        solver = ModalEigenSolver()
        soft_out = solver.solve(mesh, ModalCase(material=soft, fixtures=fixtures, modes=4))
        stiff_out = solver.solve(mesh, ModalCase(material=stiff, fixtures=fixtures, modes=4))

        expected = bar_axial_hz(200.0, soft) / bar_axial_hz(200.0, stiff)
        for soft_hz, stiff_hz in zip(
            soft_out.result.frequencies_hz, stiff_out.result.frequencies_hz
        ):
            assert soft_hz / stiff_hz == pytest.approx(expected, rel=1e-9)


class TestCantileverBending:
    def test_first_bending_mode_matches_euler_bernoulli(self) -> None:
        length, width, height = 200.0, 20.0, 10.0
        mesh = promote_to_tet10(box_mesh((length, width, height), (16, 2, 2)))
        case = ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=4)

        out = ModalEigenSolver().solve(mesh, case)
        exact = beam_bending_hz(length, width, height, STEEL, mode=1)

        # 8% because Euler-Bernoulli itself is the approximation here: it
        # ignores shear and rotary inertia, which a 200/10 beam has a little of.
        # The FE answer is the more accurate of the two, so the tolerance is the
        # closed form's error budget, not the solver's.
        assert out.result.fundamental_hz == pytest.approx(exact, rel=0.08)

    def test_tet10_is_closer_to_the_closed_form_than_tet4(self) -> None:
        # The same weakness the static tests pin: tet4 is stiff in bending, so
        # it over-predicts the frequency. Equal element count, so this compares
        # the elements and not two meshes.
        length, width, height = 200.0, 20.0, 10.0
        linear = box_mesh((length, width, height), (12, 2, 2))
        quadratic = promote_to_tet10(linear)
        case = ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=3)
        exact = beam_bending_hz(length, width, height, STEEL, mode=1)

        solver = ModalEigenSolver()
        linear_error = abs(solver.solve(linear, case).result.fundamental_hz - exact) / exact
        quadratic_error = (
            abs(solver.solve(quadratic, case).result.fundamental_hz - exact) / exact
        )

        assert quadratic_error < linear_error
        assert quadratic_error < 0.10

    def test_the_first_three_bending_modes_all_match(self) -> None:
        """The fundamental alone is a weak check -- almost any mass matrix gets
        it roughly right. Higher modes curve more within each element, so this
        is where a wrong shape function or an under-integrated mass matrix
        shows up.

        The beam vibrates in two planes, and the 20x10 section makes the
        stiff-plane modes distinct from the soft-plane ones, so the soft-plane
        series is picked out by matching rather than by index.
        """
        length, width, height = 240.0, 20.0, 10.0
        mesh = promote_to_tet10(box_mesh((length, width, height), (24, 3, 2)))
        out = ModalEigenSolver().solve(
            mesh, ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=12)
        )
        found = out.result.frequencies_hz

        for mode, tolerance in ((1, 0.08), (2, 0.10), (3, 0.14)):
            exact = beam_bending_hz(length, width, height, STEEL, mode=mode)
            closest = min(found, key=lambda f: abs(f - exact))
            assert closest == pytest.approx(exact, rel=tolerance), (
                f"bending mode {mode}: expected ~{exact:.1f} Hz, "
                f"nearest computed was {closest:.1f} Hz"
            )

    def test_modes_come_back_in_ascending_order(self) -> None:
        mesh = promote_to_tet10(box_mesh((200.0, 20.0, 10.0), (10, 2, 2)))
        out = ModalEigenSolver().solve(
            mesh, ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=5)
        )

        frequencies = out.result.frequencies_hz
        assert frequencies == sorted(frequencies)
        assert all(f > 0.0 for f in frequencies)


class TestFreeFree:
    def test_an_unconstrained_part_has_six_rigid_body_modes(self) -> None:
        # The textbook check on a modal solver, and the reason ModalCase does
        # not require a fixture: six zero-frequency modes are three translations
        # and three rotations, and their presence proves the stiffness matrix
        # has the right null space.
        mesh = box_mesh((40.0, 30.0, 20.0), (3, 3, 2))
        out = ModalEigenSolver().solve(mesh, ModalCase(material=STEEL, modes=9))

        assert out.result.rigid_body_modes == 6
        assert all(f < RIGID_BODY_HZ for f in out.result.frequencies_hz[:6])
        # And the first elastic mode is a real, well-separated frequency.
        assert out.result.fundamental_hz > 1_000.0


class TestOutputShape:
    def test_shapes_are_one_displacement_field_per_mode(self) -> None:
        mesh = box_mesh((60.0, 20.0, 20.0), (4, 2, 2))
        out = ModalEigenSolver().solve(
            mesh, ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=4)
        )

        assert out.shapes.shape == (4, mesh.node_count, 3)
        assert np.all(np.isfinite(out.shapes))
        # A clamped node cannot move in any mode.
        clamped = mesh.nodes[:, 0] <= 1e-9
        assert np.allclose(out.shapes[:, clamped, :], 0.0, atol=1e-12)

    def test_the_summary_reports_mass_and_the_fundamental(self) -> None:
        mesh = box_mesh((60.0, 20.0, 20.0), (3, 2, 2))
        out = ModalEigenSolver().solve(
            mesh, ModalCase(material=STEEL, fixtures=clamped_at_x_min(), modes=3)
        )

        summary = out.result.summary()
        assert summary["mass_kg"] == pytest.approx(
            mesh.volume * 1e-9 * STEEL.density_kg_m3, rel=1e-9
        )
        assert summary["fundamental_hz"] == out.result.frequencies_hz[0]
        assert summary["element_count"] == mesh.tet_count

    def test_a_fully_fixed_model_is_refused_with_a_readable_reason(self) -> None:
        mesh = box_mesh((10.0, 10.0, 10.0), (1, 1, 1))
        everything = [
            Fixture(where=FaceSelector(axis="x", side="min")),
            Fixture(where=FaceSelector(axis="x", side="max")),
        ]
        # A 1x1x1 box has no interior node, so clamping both x faces fixes all of them.
        with pytest.raises(SolverError, match="nothing to vibrate"):
            ModalEigenSolver().solve(
                mesh, ModalCase(material=STEEL, fixtures=everything, modes=2)
            )
