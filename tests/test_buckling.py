"""Linear buckling, checked against Euler's column formula.

Euler is the exact answer for a slender prismatic column, so a solver that gets
the geometric stiffness wrong cannot pass these by accident: the load factor is
a hard number, not a trend.

Offline, like the other physics tests -- no database fixture is requested.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.solve.buckling import (
    LinearBucklingSolver,
    assemble_geometric_stiffness,
    euler_critical_load_n,
)
from app.solve.materials import MATERIALS
from app.solve.types import (
    BucklingCase,
    FaceSelector,
    Fixture,
    ForceLoad,
    LoadCase,
)

STEEL = MATERIALS["steel-1018"]

# A genuinely slender column: 600 mm long on a 6 mm square section, so the
# Euler load is ~150 N and the slenderness ratio is high enough for the formula
# to be the accurate description rather than the approximate one.
LENGTH, SIDE = 600.0, 6.0


def column(order: int = 2, divisions: tuple[int, int, int] = (40, 2, 2)):
    mesh = box_mesh((LENGTH, SIDE, SIDE), divisions)
    return promote_to_tet10(mesh) if order == 2 else mesh


def compressive_case(newtons: float, modes: int = 4) -> BucklingCase:
    """Clamped at x=0, pushed back along -x at the free end."""
    return BucklingCase(
        material=STEEL,
        fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
        loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(-newtons, 0.0, 0.0))],
        modes=modes,
    )


class TestGeometricStiffness:
    def test_zero_displacement_gives_a_zero_matrix(self) -> None:
        # No stress, no destabilising term. Obvious, and it catches a sign or
        # index slip that would otherwise only show as a wrong load factor.
        mesh = column(order=1, divisions=(6, 1, 1))
        zero = np.zeros(3 * mesh.node_count, dtype=np.float64)

        kg = assemble_geometric_stiffness(mesh, STEEL, zero)

        assert kg.nnz == 0 or np.allclose(kg.toarray(), 0.0)

    def test_the_matrix_is_symmetric(self) -> None:
        mesh = column(order=1, divisions=(6, 1, 1))
        rng = np.random.default_rng(0)
        displacements = rng.normal(scale=1e-3, size=3 * mesh.node_count)

        kg = assemble_geometric_stiffness(mesh, STEEL, displacements).toarray()

        assert np.allclose(kg, kg.T, rtol=1e-9, atol=1e-12)

    def test_compression_and_tension_have_opposite_sign(self) -> None:
        # The whole physics of buckling in one assertion: compression
        # destabilises, tension stiffens.
        mesh = column(order=1, divisions=(8, 1, 1))
        solver = LinearBucklingSolver()

        def axial(newtons: float) -> LoadCase:
            return LoadCase(
                material=STEEL,
                fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
                loads=[
                    ForceLoad(
                        where=FaceSelector(axis="x", side="max"),
                        force_n=(newtons, 0.0, 0.0),
                    )
                ],
            )

        pushed = solver._static.solve(mesh, axial(-100.0))
        pulled = solver._static.solve(mesh, axial(100.0))

        compressed = assemble_geometric_stiffness(
            mesh, STEEL, pushed.displacements.ravel()
        ).toarray()
        stretched = assemble_geometric_stiffness(
            mesh, STEEL, pulled.displacements.ravel()
        ).toarray()

        assert np.allclose(compressed, -stretched, rtol=1e-6, atol=1e-12)


class TestEulerColumn:
    def test_critical_load_matches_euler(self) -> None:
        applied = 50.0
        result, _, static = LinearBucklingSolver().solve(column(), compressive_case(applied))

        exact = euler_critical_load_n(LENGTH, SIDE, SIDE, STEEL, "clamped-free")
        computed = result.critical_load_factor * applied

        # 12%: a tet mesh of a slender column is a demanding discretisation, and
        # the FE answer converges from above.
        assert computed == pytest.approx(exact, rel=0.12)
        # And the static run that the factor multiplies came back with it.
        assert static.result.max_von_mises_mpa > 0.0

    def test_the_factor_scales_inversely_with_the_load(self) -> None:
        """Doubling the load halves the factor -- exactly. This is what makes a
        load factor meaningful, and it is exact because the problem is linear."""
        mesh = column(divisions=(30, 2, 2))
        solver = LinearBucklingSolver()

        light, _, _ = solver.solve(mesh, compressive_case(25.0, modes=2))
        heavy, _, _ = solver.solve(mesh, compressive_case(50.0, modes=2))

        assert light.critical_load_factor == pytest.approx(
            2.0 * heavy.critical_load_factor, rel=1e-6
        )

    def test_a_stubby_column_needs_far_more_load_than_a_slender_one(self) -> None:
        # P_cr goes as 1/L^2, so halving the length should roughly quadruple it.
        solver = LinearBucklingSolver()
        long_mesh = box_mesh((600.0, SIDE, SIDE), (30, 2, 2))
        short_mesh = box_mesh((300.0, SIDE, SIDE), (15, 2, 2))

        long_result, _, _ = solver.solve(
            promote_to_tet10(long_mesh), compressive_case(50.0, modes=2)
        )
        short_result, _, _ = solver.solve(
            promote_to_tet10(short_mesh), compressive_case(50.0, modes=2)
        )

        ratio = short_result.critical_load_factor / long_result.critical_load_factor
        assert ratio == pytest.approx(4.0, rel=0.15)

    def test_a_column_in_tension_is_effectively_stable(self) -> None:
        """Pull instead of push and the column does not buckle in any meaningful
        sense.

        Note what this does *not* assert. A tension case still returns a finite
        positive factor -- measured at ~68,000x here -- and that is correct, not
        a bug: a real 3D bar in tension has small compressive pockets where the
        load is introduced and where Poisson contraction is restrained at the
        clamp, and those can go unstable at an absurd load. The meaningful
        statement is the ratio, so that is what is checked. Asserting "no
        positive factor" looked cleaner and was simply false.
        """
        mesh = column(divisions=(24, 2, 2))
        solver = LinearBucklingSolver()

        pulled = BucklingCase(
            material=STEEL,
            fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
            loads=[ForceLoad(where=FaceSelector(axis="x", side="max"), force_n=(50.0, 0.0, 0.0))],
            modes=3,
        )

        tension, _, _ = solver.solve(mesh, pulled)
        compression, _, _ = solver.solve(mesh, compressive_case(50.0, modes=3))

        assert tension.critical_load_factor > 100.0 * compression.critical_load_factor


class TestOutput:
    def test_shapes_are_one_field_per_reported_factor(self) -> None:
        mesh = column(divisions=(24, 2, 2))
        result, shapes, _ = LinearBucklingSolver().solve(mesh, compressive_case(40.0, modes=3))

        assert shapes.shape == (len(result.load_factors), mesh.node_count, 3)
        assert np.all(np.isfinite(shapes))

    def test_the_clamped_end_does_not_move_in_any_mode(self) -> None:
        mesh = column(divisions=(24, 2, 2))
        _, shapes, _ = LinearBucklingSolver().solve(mesh, compressive_case(40.0, modes=3))

        clamped = mesh.nodes[:, 0] <= 1e-9
        assert np.allclose(shapes[:, clamped, :], 0.0, atol=1e-12)

    def test_the_summary_carries_the_critical_factor(self) -> None:
        result, _, _ = LinearBucklingSolver().solve(
            column(divisions=(20, 2, 2)), compressive_case(40.0, modes=2)
        )

        summary = result.summary()
        assert summary["critical_load_factor"] == result.critical_load_factor
        assert summary["element_count"] > 0

    def test_a_case_with_no_load_is_refused_by_the_schema(self) -> None:
        with pytest.raises(ValueError):
            BucklingCase(
                material=STEEL,
                fixtures=[Fixture(where=FaceSelector(axis="x", side="min"))],
                loads=[],
            )
