"""The named load recipes — Phase 12.1, `app/solve/load_library.py`.

The library landed with no tests at all. What it claims about itself is the
thing worth pinning, and it is Decision 2 restated: *this composes the existing
vocabulary, it does not replace it.* So the assertions here are of two kinds.

**It is made of `app/solve/types.py`.** Every recipe returns ordinary `Load`
objects, `compose` returns an ordinary `LoadCase`, and no second load type or
parallel selector appears anywhere. `TestItIsTheExistingVocabulary` checks that
against the union `types.py` actually declares rather than against a list
written here, so a new load type added there cannot quietly leave this module
inventing its own.

**A case it builds can be solved.** A load library that produces cases nothing
can solve is a vocabulary, not a library, so `TestASolvedCase` hands one to
`LinearStaticSolver` and checks the answer against the closed form — a uniform
pressure on the end of a prismatic bar is exactly sigma = p. `uniaxial_case` and
`STEEL` come from `tests/test_solver.py` (as `test_solver_oracle.py` already
does) rather than being written a third time.

The one number in this module that is not a multiplication is `scaled` on a
centrifugal load, which scales the *speed* by sqrt(factor) so the *force* scales
by factor. That is checked where it can actually be wrong: through
`app.solve.loads.assemble_loads`, on a real mesh, comparing nodal forces —
asserting on the rpm alone would only restate the implementation.

Offline: no database, no kernel, no network.
"""

from __future__ import annotations

import math
import typing

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.solve import types as solve_types
from app.solve.linear_static import LinearStaticSolver
from app.solve.load_library import (
    RECIPES,
    ULTIMATE_FACTOR,
    LoadRecipe,
    bolt_preload,
    catalogue,
    compose,
    describe,
    inertial,
    pressure,
    scaled,
    self_weight,
    spin,
    ultimate,
)
from app.solve.loads import assemble_loads
from app.solve.materials import Source, SourceKind
from app.solve.types import (
    STANDARD_GRAVITY_MM_S2,
    BoxSelector,
    CentrifugalLoad,
    FaceSelector,
    Fixture,
    ForceLoad,
    GravityLoad,
    LoadCase,
    PressureLoad,
)
from tests.test_solver import STEEL, uniaxial_case

#: The bar every solved case here is run on: 10 x 20 x 100 mm, rollers at the
#: origin end. Same arrangement as `tests/test_solver.py::uniaxial_case`, so the
#: stress stays uniform and there is a closed form to check against.
WIDTH, DEPTH, LENGTH = 10.0, 20.0, 100.0
AREA = WIDTH * DEPTH

ROLLERS = [
    Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
    Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
    Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
]


def bar(divisions: tuple[int, int, int] = (2, 2, 8)):
    return box_mesh((WIDTH, DEPTH, LENGTH), divisions=divisions)


# -- it is the existing vocabulary -------------------------------------------


class TestItIsTheExistingVocabulary:
    """Decision 2: `loads.py` and `selection.py` are the asset. This composes them."""

    @staticmethod
    def _declared_load_types() -> tuple[type, ...]:
        """Every concrete load `types.py` declares, read from the union itself.

        Read rather than listed, so a load type added to `types.py` is
        automatically part of what this test demands the library stay inside.
        """
        annotated = solve_types.Load
        union = typing.get_args(annotated)[0]
        return tuple(typing.get_args(union))

    def test_every_recipe_returns_only_types_declared_in_types_py(self) -> None:
        allowed = self._declared_load_types()
        assert len(allowed) >= 6

        produced = [
            *self_weight(),
            *inertial((0.0, 0.0, -1.0), 3.0),
            *pressure(FaceSelector(axis="z", side="max"), 2.0),
            *spin((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3_000.0),
            *bolt_preload(
                under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
                under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
                axis=(0.0, 0.0, -1.0),
                preload_n=5_000.0,
            ),
        ]

        assert produced
        for load in produced:
            assert isinstance(load, allowed), f"{type(load).__name__} is not in types.Load"

    def test_the_recipes_produce_the_specific_types_their_names_promise(self) -> None:
        assert isinstance(self_weight()[0], GravityLoad)
        assert isinstance(inertial((0.0, 0.0, -1.0), 2.0)[0], GravityLoad)
        assert isinstance(pressure(FaceSelector(axis="z", side="max"), 1.0)[0], PressureLoad)
        assert isinstance(
            spin((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 100.0)[0], CentrifugalLoad
        )

    def test_a_preload_is_two_ordinary_force_loads_on_caller_supplied_selectors(
        self,
    ) -> None:
        head = BoxSelector(min=(0, 0, 90), max=(10, 20, 100))
        nut = BoxSelector(min=(0, 0, 0), max=(10, 20, 10))
        loads = bolt_preload(
            under_head=head, under_nut=nut, axis=(0.0, 0.0, -1.0), preload_n=5_000.0
        )

        assert [type(one) for one in loads] == [ForceLoad, ForceLoad]
        # The selectors are handed straight through — the module says in its own
        # docstring that it cannot derive an annular bearing region, and deriving
        # one quietly would be the worse answer.
        assert loads[0].where == head
        assert loads[1].where == nut

    def test_compose_returns_an_ordinary_load_case(self) -> None:
        case = compose("Lift", STEEL, ROLLERS, self_weight())

        assert isinstance(case, LoadCase)
        assert case.name == "Lift"
        assert case.material is STEEL
        assert [type(one) for one in case.fixtures] == [Fixture, Fixture, Fixture]

    def test_compose_concatenates_groups_in_the_order_written(self) -> None:
        case = compose(
            "Combined",
            STEEL,
            ROLLERS,
            self_weight(),
            pressure(FaceSelector(axis="z", side="max"), 1.0),
            inertial((1.0, 0.0, 0.0), 2.0),
        )

        assert [type(one) for one in case.loads] == [GravityLoad, PressureLoad, GravityLoad]

    def test_nothing_here_reaches_into_the_solver(self) -> None:
        """The module's own claim: no solver import, no mesh, no selection rewrite."""
        import app.solve.load_library as module

        source = module.__doc__ or ""
        assert "does not replace it" in source
        imported = set(vars(module))
        assert "LinearStaticSolver" not in imported
        assert "assemble_loads" not in imported
        assert "select_nodes" not in imported


# -- a case it builds can actually be solved ---------------------------------


class TestASolvedCase:
    """A library whose cases nothing can solve is a vocabulary, not a library."""

    gauge_mpa = 12.0

    @pytest.fixture
    def solved(self):
        mesh = bar()
        case = compose(
            "End pressure",
            STEEL,
            ROLLERS,
            pressure(FaceSelector(axis="z", side="max"), self.gauge_mpa),
        )
        return mesh, LinearStaticSolver().solve(mesh, case)

    def test_a_uniform_pressure_reproduces_its_own_magnitude_as_stress(
        self, solved
    ) -> None:
        """sigma = p exactly: the pressure is the stress on a prismatic bar."""
        _, output = solved
        assert output.result.max_von_mises_mpa == pytest.approx(self.gauge_mpa, rel=1e-6)

    def test_the_shortening_matches_hookes_law(self, solved) -> None:
        mesh, output = solved
        expected = -self.gauge_mpa * LENGTH / STEEL.youngs_modulus_mpa
        far = np.flatnonzero(np.isclose(mesh.nodes[:, 2], LENGTH))
        assert output.displacements[far, 2].mean() == pytest.approx(expected, rel=1e-6)

    def test_it_is_compression_because_a_positive_gauge_pushes_in(self, solved) -> None:
        mesh, output = solved
        far = np.flatnonzero(np.isclose(mesh.nodes[:, 2], LENGTH))
        assert output.displacements[far, 2].mean() < 0.0

    def test_self_weight_assembles_to_the_parts_actual_weight(self) -> None:
        """rho.V.g in newtons, from the load vector the solver would be handed."""
        mesh = bar()
        total, warnings = assemble_loads(mesh, self_weight(), STEEL.density_kg_m3)

        volume_mm3 = WIDTH * DEPTH * LENGTH
        weight_n = volume_mm3 * 1e-12 * STEEL.density_kg_m3 * STANDARD_GRAVITY_MM_S2
        assert not warnings
        assert total.reshape(-1, 3)[:, 2].sum() == pytest.approx(-weight_n, rel=1e-9)

    def test_a_three_g_case_is_exactly_three_times_the_one_g_case(self) -> None:
        mesh = bar()
        one, _ = assemble_loads(mesh, self_weight(), STEEL.density_kg_m3)
        three, _ = assemble_loads(
            mesh, inertial((0.0, 0.0, -1.0), 3.0), STEEL.density_kg_m3
        )
        assert three == pytest.approx(3.0 * one, rel=1e-12)


# -- factoring ---------------------------------------------------------------


class TestFactoring:
    def test_the_ultimate_factor_is_one_and_a_half(self) -> None:
        assert ULTIMATE_FACTOR == 1.5

    def test_an_ultimate_case_solves_to_exactly_the_factored_stress(self) -> None:
        """Reuses `uniaxial_case` rather than posing a third bar."""
        mesh = bar()
        limit = uniaxial_case(STEEL, 5_000.0)
        factored = compose(
            "Ultimate", STEEL, limit.fixtures, ultimate(limit.loads)
        )

        limit_out = LinearStaticSolver().solve(mesh, limit)
        ultimate_out = LinearStaticSolver().solve(mesh, factored)

        expected = ULTIMATE_FACTOR * 5_000.0 / AREA
        assert ultimate_out.result.max_von_mises_mpa == pytest.approx(expected, rel=1e-6)
        assert ultimate_out.result.max_von_mises_mpa == pytest.approx(
            ULTIMATE_FACTOR * limit_out.result.max_von_mises_mpa, rel=1e-9
        )

    def test_the_originals_are_untouched(self) -> None:
        limit = uniaxial_case(STEEL, 1_000.0)
        before = [one.model_copy(deep=True) for one in limit.loads]
        scaled(limit.loads, 4.0)
        assert list(limit.loads) == before

    def test_a_pressure_scales_by_the_factor(self) -> None:
        [one] = scaled(pressure(FaceSelector(axis="z", side="max"), 3.0), 2.0)
        assert isinstance(one, PressureLoad)
        assert one.pressure_mpa == pytest.approx(6.0)

    def test_a_gravity_load_scales_by_the_factor(self) -> None:
        [one] = scaled(self_weight(), 2.5)
        assert isinstance(one, GravityLoad)
        assert one.magnitude_mm_s2 == pytest.approx(2.5 * STANDARD_GRAVITY_MM_S2)

    def test_a_moment_scales_by_the_factor(self) -> None:
        moment = solve_types.MomentLoad(
            where=FaceSelector(axis="z", side="max"), moment_n_mm=(0.0, 0.0, 100.0)
        )
        [one] = scaled([moment], 3.0)
        assert one.moment_n_mm == pytest.approx((0.0, 0.0, 300.0))

    def test_a_bearing_load_scales_by_the_factor(self) -> None:
        bearing = solve_types.BearingLoad(
            where=solve_types.CylinderSelector(
                axis_point=(5.0, 10.0, 50.0),
                axis_direction=(0.0, 0.0, 1.0),
                radius=4.0,
            ),
            force_n=(100.0, 0.0, 0.0),
        )
        [one] = scaled([bearing], 2.0)
        assert one.force_n == pytest.approx((200.0, 0.0, 0.0))


class TestSpeedIsScaledBySquareRoot:
    """The one place a plain multiplication would be wrong, checked as force.

    The module's whole argument for `scaled` existing rather than a comprehension
    at each call site is that a centrifugal load is quadratic in speed. So the
    check is on the assembled nodal forces, through `app.solve.loads` on a real
    mesh — the rpm on its own would only restate the code.
    """

    axis_point = (WIDTH / 2.0, DEPTH / 2.0, 0.0)
    axis_direction = (0.0, 0.0, 1.0)
    rpm = 3_000.0

    def _magnitude(self, loads) -> float:
        mesh = bar((2, 2, 4))
        total, _ = assemble_loads(mesh, loads, STEEL.density_kg_m3)
        # Summed as magnitudes: the vector sum of a symmetric centrifugal field
        # cancels to zero and would compare equal at any speed at all.
        return float(np.linalg.norm(total.reshape(-1, 3), axis=1).sum())

    def test_the_speed_moves_by_the_square_root(self) -> None:
        [one] = scaled(spin(self.axis_point, self.axis_direction, self.rpm), 2.25)
        assert isinstance(one, CentrifugalLoad)
        assert one.rpm == pytest.approx(1.5 * self.rpm)

    def test_the_force_moves_by_the_factor(self) -> None:
        base = spin(self.axis_point, self.axis_direction, self.rpm)
        assert self._magnitude(scaled(base, 1.5)) == pytest.approx(
            1.5 * self._magnitude(base), rel=1e-9
        )

    def test_an_ultimate_spin_case_is_one_and_a_half_the_load_not_two_and_a_quarter(
        self,
    ) -> None:
        """The defect the sqrt exists to prevent, stated as the number it would be."""
        base = spin(self.axis_point, self.axis_direction, self.rpm)
        limit = self._magnitude(base)
        assert self._magnitude(ultimate(base)) == pytest.approx(1.5 * limit, rel=1e-9)
        assert self._magnitude(ultimate(base)) != pytest.approx(2.25 * limit, rel=1e-3)

    def test_an_overspeed_factor_multiplies_the_speed_not_the_load(self) -> None:
        [rated] = spin(self.axis_point, self.axis_direction, self.rpm)
        [over] = spin(self.axis_point, self.axis_direction, self.rpm, overspeed=1.1)
        assert over.rpm == pytest.approx(1.1 * rated.rpm)
        # ...and therefore 21% more load, which is the caveat the recipe carries.
        assert self._magnitude([over]) == pytest.approx(
            1.21 * self._magnitude([rated]), rel=1e-9
        )


# -- a preload is internal ---------------------------------------------------


class TestAPreloadDoesNotAccelerateTheModel:
    def test_the_two_forces_sum_to_zero(self) -> None:
        head, nut = bolt_preload(
            under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
            under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
            axis=(0.0, 0.0, -1.0),
            preload_n=5_000.0,
        )
        assert [a + b for a, b in zip(head.force_n, nut.force_n, strict=True)] == [
            0.0,
            0.0,
            0.0,
        ]

    def test_the_assembled_nodal_forces_sum_to_zero_on_a_real_mesh(self) -> None:
        """Self-equilibrating through `assemble_loads`, not just on paper."""
        loads = bolt_preload(
            under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
            under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
            axis=(0.0, 0.0, -1.0),
            preload_n=5_000.0,
        )
        total, warnings = assemble_loads(bar(), loads, STEEL.density_kg_m3)
        assert not warnings
        assert total.reshape(-1, 3).sum(axis=0) == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)

    def test_the_axis_is_normalised_so_its_length_is_not_a_magnitude(self) -> None:
        head, _ = bolt_preload(
            under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
            under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
            axis=(0.0, 0.0, -7.0),
            preload_n=5_000.0,
        )
        assert math.hypot(*head.force_n) == pytest.approx(5_000.0)

    def test_no_negative_zero_reaches_the_report(self) -> None:
        _, nut = bolt_preload(
            under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
            under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
            axis=(0.0, 0.0, -1.0),
            preload_n=1_000.0,
        )
        assert not any(math.copysign(1.0, c) < 0 and c == 0.0 for c in nut.force_n)


# -- the refusals ------------------------------------------------------------


class TestTheRefusals:
    """Every guard, exercised by handing it the thing it exists to stop."""

    def test_a_zero_or_negative_g_load_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no magnitude"):
            inertial((0.0, 0.0, -1.0), 0.0)
        with pytest.raises(ValueError, match="positive multiple"):
            inertial((0.0, 0.0, -1.0), -3.0)

    def test_more_suction_than_a_vacuum_is_refused(self) -> None:
        with pytest.raises(ValueError, match="perfect vacuum"):
            pressure(FaceSelector(axis="z", side="max"), -0.2)

    def test_a_real_vacuum_is_allowed(self) -> None:
        [one] = pressure(FaceSelector(axis="z", side="max"), -0.101325)
        assert one.pressure_mpa == pytest.approx(-0.101325)

    def test_a_non_positive_overspeed_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not a speed"):
            spin((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 100.0, overspeed=0.0)

    def test_a_preload_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not a preload"):
            bolt_preload(
                under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
                under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
                axis=(0.0, 0.0, -1.0),
                preload_n=0.0,
            )

    def test_a_bolt_with_no_axis_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no direction"):
            bolt_preload(
                under_head=BoxSelector(min=(0, 0, 90), max=(10, 20, 100)),
                under_nut=BoxSelector(min=(0, 0, 0), max=(10, 20, 10)),
                axis=(0.0, 0.0, 0.0),
                preload_n=100.0,
            )

    def test_a_non_positive_load_factor_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is not a factor"):
            scaled(self_weight(), 0.0)
        with pytest.raises(ValueError, match="reverse the load"):
            scaled(self_weight(), -1.5)

    def test_a_case_with_no_loads_is_refused(self) -> None:
        with pytest.raises(ValueError, match="has no loads"):
            compose("Empty", STEEL, ROLLERS)

    def test_an_unknown_load_type_is_refused_rather_than_passed_through(self) -> None:
        """The `else` in `scaled`: a new load type must not silently go unfactored."""

        class Invented:
            name = "invented"

        with pytest.raises(TypeError, match="has not been taught to"):
            scaled([Invented()], 2.0)  # type: ignore[list-item]

    def test_an_unknown_recipe_key_names_the_ones_that_exist(self) -> None:
        with pytest.raises(KeyError) as caught:
            describe("ultimate-load")
        message = str(caught.value)
        assert "not a load recipe" in message
        for key in RECIPES:
            assert key in message

    def test_two_recipes_cannot_share_a_key(self) -> None:
        from app.solve import load_library

        duplicate = LoadRecipe(
            key="self-weight",
            title="A second self weight",
            summary="...",
            source=Source(citation="nowhere", kind=SourceKind.DERIVED),
        )
        with pytest.raises(ValueError, match="share the key"):
            load_library._register(duplicate)
        # And the registry is unharmed: the original is still the one registered.
        assert RECIPES["self-weight"].title == "Self weight"


# -- provenance --------------------------------------------------------------


class TestProvenance:
    def test_every_recipe_carries_a_citable_source(self) -> None:
        assert RECIPES
        for key, recipe in RECIPES.items():
            assert recipe.key == key
            assert recipe.title.strip()
            assert recipe.summary.strip()
            assert recipe.source.citation.strip()
            assert isinstance(recipe.source.kind, SourceKind)

    def test_every_recipe_is_reachable_by_key(self) -> None:
        for key in RECIPES:
            assert describe(key) is RECIPES[key]

    def test_the_catalogue_is_sorted_and_is_a_copy(self) -> None:
        listed = catalogue()
        assert list(listed) == sorted(RECIPES)
        assert listed is not RECIPES

    def test_describe_records_the_recipe_its_source_and_what_was_applied(self) -> None:
        record = describe("factored").describe(factor=ULTIMATE_FACTOR, case="Lift")

        assert record["recipe"] == "factored"
        assert record["applied"] == {"factor": 1.5, "case": "Lift"}
        assert "CS-25.303" in str(record["source"])
        assert record["caveats"]

    def test_the_caveats_are_carried_not_summarised_away(self) -> None:
        """Every one of these has been the reason a plausible analysis was wrong."""
        assert any("separat" in one for one in describe("bolt-preload").caveats)
        assert any("Quadratic" in one for one in describe("spin").caveats)
        assert any("Gauge" in one for one in describe("pressure").caveats)

    def test_a_recipe_record_is_json_shaped(self) -> None:
        import json

        for key in RECIPES:
            json.dumps(describe(key).describe())
