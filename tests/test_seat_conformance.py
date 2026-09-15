"""Comparing against a real seat is a different question from comparing two kernels.

`app/kernel/conformance.py` has been backend-neutral since 2026-09-05, and until
2026-09-11 **both sides of every run had been an `OcctRunner`**. THE QUEUE B1 put
a licensed CATIA V5-R33 on the right-hand side for the first time, and three
things the harness assumed turned out to be true only of OCCT-versus-OCCT. None
of them is a geometry bug; all three meant the comparison could never pass.

1. **Two spellings for one quantity.** The kernel reports `centre_of_mass_mm` and
   the bridge reports `center_of_gravity_mm`, so `compare` read `None` on one
   side and silently could not compare the centre of mass at all — the check that
   moves when a feature lands in the wrong place while the volume stays right.
2. **A tolerance finer than the other implementation prints.** CATIA reports four
   decimal places, so a bored plate whose volume is 22869.026644707676 mm3 comes
   back as 22869.0266. At `CONFORMANCE_TOLERANCE_MM3` (1e-6) that 4.5e-5 is a
   divergence; it is the report's precision, not the modelling.
3. **One tolerance across mm3, mm2 and kg.** Harmless at 1e-6 and not at 1e-3,
   where a mass slack of a gram would swallow the 0.127% that CATIA's catalogue
   density genuinely produces. That is why the harness prints deltas rather than
   only a verdict — see `scripts/catia_conformance.py`.

The measured answer, for the record: volume and surface area agree **exactly** on
a 30x20x5 plate and to 0.000% on a bored 60x40x10 one; mass differs by 0.127%
because CATIA applies its catalogue *Acier* at 7860 kg/m3 where the kernel holds
7870 for steel-1018 — a deliberate choice recorded in
`scripts/catia_bridge/catia_com.py`, so that the mass Kryova quotes is the mass
the CATPart itself reports.

Offline: these pin the comparator, not the seat.
"""

from __future__ import annotations

import pytest

from app.kernel.measurement import (
    CENTRE_OF_MASS_ALIASES,
    CENTRE_OF_MASS_MM,
    CONFORMANCE_TOLERANCE_MM3,
    SEAT_TOLERANCE_MM3,
    centre_of_mass,
    compare,
)


class TestTheCentreOfMassIsOneQuantityUnderFourNames:
    def test_the_kernels_spelling_is_found(self) -> None:
        assert centre_of_mass({CENTRE_OF_MASS_MM: [1.0, 2.0, 3.0]}) == [1.0, 2.0, 3.0]

    def test_the_bridges_spelling_is_found(self) -> None:
        """`center_of_gravity_mm` is what a real CATIA seat returns."""
        assert centre_of_mass({"center_of_gravity_mm": [1.0, 2.0, 3.0]}) == [1.0, 2.0, 3.0]

    @pytest.mark.parametrize("spelling", CENTRE_OF_MASS_ALIASES)
    def test_every_alias_resolves(self, spelling: str) -> None:
        assert centre_of_mass({spelling: [0.0, 0.0, 2.5]}) == [0.0, 0.0, 2.5]

    def test_absent_is_none_rather_than_a_guess(self) -> None:
        assert centre_of_mass({"volume_mm3": 10.0}) is None

    def test_the_two_spellings_compare_as_one(self) -> None:
        """The defect: a kernel payload and a seat payload describing the same
        point were never compared, because neither held the other's key."""
        kernel = {CENTRE_OF_MASS_MM: [0.0, 0.0, 2.5]}
        seat = {"center_of_gravity_mm": [0.0, 0.0, 2.5]}
        assert CENTRE_OF_MASS_MM not in compare(kernel, seat)

    def test_and_still_disagree_when_they_really_differ(self) -> None:
        """The fix must not turn the check off — it must make it possible."""
        kernel = {CENTRE_OF_MASS_MM: [0.0, 0.0, 2.5]}
        seat = {"center_of_gravity_mm": [0.0, 0.0, 7.5]}
        assert CENTRE_OF_MASS_MM in compare(kernel, seat)

    def test_the_canonical_name_is_what_gets_reported(self) -> None:
        """A reader asked about a part should not be told about a spelling."""
        kernel = {CENTRE_OF_MASS_MM: [0.0, 0.0, 2.5]}
        seat = {"center_of_gravity_mm": [9.0, 0.0, 2.5]}
        assert compare(kernel, seat) == [CENTRE_OF_MASS_MM]


class TestTheSeatToleranceIsForAnotherImplementationsPrecision:
    def test_it_is_looser_than_the_two_kernel_one(self) -> None:
        assert SEAT_TOLERANCE_MM3 > CONFORMANCE_TOLERANCE_MM3

    def test_catias_four_decimal_places_are_not_a_divergence(self) -> None:
        """The measured case: 22869.026644707676 printed as 22869.0266."""
        kernel = {"volume_mm3": 22869.026644707676}
        seat = {"volume_mm3": 22869.0266}
        assert compare(kernel, seat) == ["volume_mm3"], "at the default it is a divergence"
        assert compare(kernel, seat, tolerance=SEAT_TOLERANCE_MM3) == []

    def test_a_real_difference_still_fails_at_the_seat_tolerance(self) -> None:
        """Loose enough for printing, tight enough that a feature cannot hide:
        the smallest thing either backend builds moves a volume by far more."""
        kernel = {"volume_mm3": 22869.0266}
        seat = {"volume_mm3": 22868.0266}
        assert compare(kernel, seat, tolerance=SEAT_TOLERANCE_MM3) == ["volume_mm3"]


class TestAQuantityOnlyOneSideReportsIsStillADivergence:
    def test_the_bridge_not_reporting_topology_is_reported(self) -> None:
        """Measured on the seat: `catia_measure` returns no face/edge/solid count,
        so topology cannot be compared at all. That is a finding, not a pass —
        `compare`'s own rule is that a key present on one side only disagrees."""
        kernel = {"face_count": 6, "edge_count": 12, "solid_count": 1}
        seat: dict[str, object] = {}
        assert set(compare(kernel, seat)) == {"face_count", "edge_count", "solid_count"}

    def test_a_key_absent_from_both_is_not_a_divergence(self) -> None:
        assert compare({"volume_mm3": 1.0}, {"volume_mm3": 1.0}) == []


class TestTheLadderReachesTheSeatThroughM4:
    """E3's phase proof names the ladder through M4; `--ladder` is how a seat run covers it.

    Written 2026-09-15 on Linux and not run there (the user's instruction: Windows tests).
    """

    def test_every_rung_through_m4_is_either_built_or_skipped_with_a_reason(self) -> None:
        from scripts.catia_conformance import LADDER_RUNGS, ladder_plans

        plans, skipped = ladder_plans()
        built = {label.split(" ", 1)[0] for label, _ in plans}
        assert built | set(skipped) == set(LADDER_RUNGS)
        assert not built & set(skipped)
        for rung, reason in skipped.items():
            assert reason.strip(), f"{rung} is skipped with no reason"

    def test_m1_and_every_m2_component_are_handed_to_the_seat(self) -> None:
        from app.design.missions import LADDER
        from scripts.catia_conformance import ladder_plans

        plans, _ = ladder_plans()
        labels = {label for label, _ in plans}
        by_rung = {mission.rung: mission for mission in LADDER}
        assert f"M1 {by_rung['M1'].title}" in labels
        assembly = by_rung["M2"].assembly
        assert assembly is not None
        for component in assembly.parts:
            assert f"M2 {component}" in labels

    def test_the_folded_rung_says_why_it_cannot_reach_catia(self) -> None:
        from scripts.catia_conformance import ladder_plans

        _, skipped = ladder_plans()
        assert "sheet-metal" in skipped["M3"]
        assert "E1" in skipped["M3"]

    def test_every_handed_plan_is_a_compiled_plan_with_calls(self) -> None:
        from scripts.catia_conformance import ladder_plans

        plans, _ = ladder_plans()
        assert plans
        for label, plan in plans:
            assert len(plan) > 0, f"{label} compiled to nothing"
