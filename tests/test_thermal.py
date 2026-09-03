"""Thermal stress, checked against the restrained-bar closed form.

A bar held along one axis and free on the other two carries `sigma = -E alpha dT`
and nothing else -- no mesh dependence, no shape factor, no approximation. That
makes it the sharpest possible check on the two halves of a thermal
implementation: the equivalent load, and the subtraction of thermal strain
during stress recovery. Get either wrong and this number moves.

Offline, like the other physics tests.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.thermal import (
    restrained_bar_stress_mpa,
    thermal_load,
    thermal_strain,
    thermal_stress_correction,
)
from app.solve.types import (
    BoxSelector,
    FaceSelector,
    Fixture,
    ForceLoad,
    LoadCase,
    SolverError,
)

STEEL = MATERIALS["steel-1018"]
ALUMINIUM = MATERIALS["aluminium-6061-t6"]

# A load the schema requires but the physics does not want. Thermal stress is a
# restrained-expansion effect, so these tests need no mechanical load at all --
# this is a token one, small enough to be negligible beside the thermal stress.
NEGLIGIBLE = ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, 0.0, 1e-9))


def axially_restrained(delta_t_k: float, material=STEEL) -> LoadCase:
    """Clamped in x at both ends, and *only* in x -- so y and z expand freely.

    That freedom is the whole point: it is what makes the stress state uniaxial
    and equal to `-E alpha dT`. Restraining the ends in all three directions
    instead would give a biaxial state at the clamps and a different number.

    Which leaves three rigid-body motions to remove -- translation in y, in z,
    and rotation about x -- and they have to be removed at *points*, not over a
    face, or the restraint itself would resist the lateral expansion. Pinning
    y and z at one corner node and z at a second node on the same face does it
    with exactly three constraints. Fixing y,z over a whole region instead is
    what made this model report "under-constrained": three DOFs at one node
    cannot stop the bar spinning about its own axis.
    """
    return LoadCase(
        material=material,
        fixtures=axial_fixtures(),
        loads=[NEGLIGIBLE],
        delta_t_k=delta_t_k,
    )


def axial_fixtures() -> list[Fixture]:
    """The constraint set described in `axially_restrained`, shared so the
    superposition test cannot drift out of step with it."""
    corner = BoxSelector(min=(-0.1, -0.1, -0.1), max=(0.1, 0.1, 0.1))
    along_y = BoxSelector(min=(-0.1, 19.9, -0.1), max=(0.1, 20.1, 0.1))
    return [
        Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
        Fixture(where=FaceSelector(axis="x", side="max"), dofs=["x"]),
        Fixture(where=corner, dofs=["y", "z"]),
        Fixture(where=along_y, dofs=["z"]),
    ]


class TestThermalStrain:
    def test_expansion_is_dilatational_not_shear(self) -> None:
        strain = thermal_strain(STEEL, 100.0)

        assert strain[:3] == pytest.approx([STEEL.thermal_expansion_per_k * 100.0] * 3)
        assert np.allclose(strain[3:], 0.0), "heating a part must not shear it"

    def test_cooling_reverses_the_sign(self) -> None:
        assert thermal_strain(STEEL, -50.0) == pytest.approx(-thermal_strain(STEEL, 50.0))

    def test_a_material_without_a_coefficient_is_refused_by_name(self) -> None:
        anonymous = STEEL.model_copy(update={"thermal_expansion_per_k": None})
        with pytest.raises(SolverError, match="thermal expansion"):
            thermal_strain(anonymous, 100.0)

    def test_the_correction_is_the_fully_restrained_stress(self) -> None:
        # D * alpha dT is what a completely restrained element carries. For a
        # triaxially restrained solid that is E alpha dT / (1 - 2 nu), which is
        # larger than the uniaxial case -- a useful sanity anchor on the sign.
        correction = thermal_stress_correction(STEEL, 100.0)
        triaxial = (
            STEEL.youngs_modulus_mpa
            * STEEL.thermal_expansion_per_k
            * 100.0
            / (1.0 - 2.0 * STEEL.poissons_ratio)
        )

        assert correction[:3] == pytest.approx([triaxial] * 3, rel=1e-9)
        assert np.allclose(correction[3:], 0.0)


class TestThermalLoad:
    @pytest.mark.parametrize("order", [1, 2])
    def test_a_free_part_has_no_net_thermal_force(self, order: int) -> None:
        """The equivalent load is self-equilibrating: heating a part does not
        push it anywhere, it only tries to stretch it. A non-zero resultant
        would mean the assembly is wrong."""
        mesh = box_mesh((60.0, 20.0, 20.0), (3, 2, 2))
        if order == 2:
            mesh = promote_to_tet10(mesh)

        forces = thermal_load(mesh, STEEL, 120.0).reshape(-1, 3)

        assert np.allclose(forces.sum(axis=0), 0.0, atol=1e-6)

    def test_the_load_is_linear_in_temperature(self) -> None:
        mesh = box_mesh((40.0, 20.0, 20.0), (2, 2, 2))

        single = thermal_load(mesh, STEEL, 50.0)
        double = thermal_load(mesh, STEEL, 100.0)

        assert double == pytest.approx(2.0 * single, rel=1e-12)


class TestRestrainedBar:
    @pytest.mark.parametrize("order", [1, 2])
    def test_stress_matches_minus_e_alpha_delta_t(self, order: int) -> None:
        mesh = box_mesh((120.0, 20.0, 20.0), (6, 2, 2))
        if order == 2:
            mesh = promote_to_tet10(mesh)

        out = LinearStaticSolver().solve(mesh, axially_restrained(100.0))

        exact = abs(restrained_bar_stress_mpa(STEEL, 100.0))
        # von Mises of a uniaxial state is |sigma|, so the magnitudes compare
        # directly. Away from the clamped faces this is uniform, so the peak is
        # the answer.
        assert out.result.max_von_mises_mpa == pytest.approx(exact, rel=0.05)

    def test_heating_and_cooling_give_the_same_magnitude(self) -> None:
        mesh = box_mesh((120.0, 20.0, 20.0), (6, 2, 2))
        solver = LinearStaticSolver()

        hot = solver.solve(mesh, axially_restrained(80.0)).result.max_von_mises_mpa
        cold = solver.solve(mesh, axially_restrained(-80.0)).result.max_von_mises_mpa

        assert hot == pytest.approx(cold, rel=1e-9)

    def test_aluminium_stresses_less_than_steel_for_the_same_rise(self) -> None:
        """Aluminium expands twice as much but is three times softer, so
        `E alpha` is the number that matters and steel comes out higher. A
        solver that used alpha without E would get this backwards."""
        mesh = box_mesh((120.0, 20.0, 20.0), (6, 2, 2))
        solver = LinearStaticSolver()

        steel = solver.solve(mesh, axially_restrained(100.0, STEEL)).result.max_von_mises_mpa
        alu = solver.solve(
            mesh, axially_restrained(100.0, ALUMINIUM)
        ).result.max_von_mises_mpa

        assert steel > alu
        expected = abs(restrained_bar_stress_mpa(ALUMINIUM, 100.0)) / abs(
            restrained_bar_stress_mpa(STEEL, 100.0)
        )
        assert alu / steel == pytest.approx(expected, rel=0.05)


class TestNoRegression:
    def test_an_isothermal_case_is_bit_for_bit_what_it_was(self) -> None:
        """delta_t_k defaults to None and must cost nothing: the thermal work
        added a branch to the hot path of every existing static solve."""
        mesh = box_mesh((100.0, 20.0, 20.0), (5, 2, 2))
        case = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
            loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, -500.0, 0.0))],
        )
        explicit = case.model_copy(update={"delta_t_k": None})

        solver = LinearStaticSolver()
        default = solver.solve(mesh, case)
        stated = solver.solve(mesh, explicit)

        assert default.result.max_von_mises_mpa == stated.result.max_von_mises_mpa
        assert np.array_equal(default.displacements, stated.displacements)

    def test_zero_degrees_is_the_isothermal_answer(self) -> None:
        mesh = box_mesh((100.0, 20.0, 20.0), (5, 2, 2))
        base = LoadCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
            loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(0.0, -500.0, 0.0))],
        )

        solver = LinearStaticSolver()
        cold = solver.solve(mesh, base)
        zero = solver.solve(mesh, base.model_copy(update={"delta_t_k": 0.0}))

        assert zero.result.max_von_mises_mpa == pytest.approx(
            cold.result.max_von_mises_mpa, rel=1e-12
        )

    def test_a_mechanical_load_and_a_temperature_rise_superpose(self) -> None:
        """Both are linear, so solving them together must equal the sum of
        solving them apart. This is the check that the thermal term was added
        to the load vector rather than replacing it."""
        mesh = box_mesh((120.0, 20.0, 20.0), (6, 2, 2))
        fixtures = axial_fixtures()
        pull = ForceLoad(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, 800.0))
        solver = LinearStaticSolver()

        mechanical = solver.solve(
            mesh, LoadCase(material=STEEL, fixtures=fixtures, loads=[pull])
        )
        thermal = solver.solve(mesh, axially_restrained(60.0))
        both = solver.solve(
            mesh,
            LoadCase(material=STEEL, fixtures=fixtures, loads=[pull], delta_t_k=60.0),
        )

        assert both.displacements == pytest.approx(
            mechanical.displacements + thermal.displacements, abs=1e-9
        )
