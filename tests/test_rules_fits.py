"""Fits: what a hole and shaft pair gives, and which pair is chosen (E13.2).

Every deviation below is a test fixture, deliberately not an ISO 286 value, with a source that
says so; the arithmetic is worked beside each assertion.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import pytest

from app.rules.errors import SourceError
from app.rules.fits import (
    ClearanceRequirement,
    Feature,
    Fit,
    FitError,
    FitKind,
    ToleranceZone,
    select_fit,
)

FIXTURE = "test fixture deviations, not read from ISO 286"


def _hole(name: str, upper: float, lower: float, nominal: float = 20.0) -> ToleranceZone:
    return ToleranceZone(name, Feature.HOLE, nominal, upper, lower, FIXTURE)


def _shaft(name: str, upper: float, lower: float, nominal: float = 20.0) -> ToleranceZone:
    return ToleranceZone(name, Feature.SHAFT, nominal, upper, lower, FIXTURE)


HOLE = _hole("h-a", 0.020, 0.000)
LOOSE = Fit(HOLE, _shaft("s-loose", -0.020, -0.040))
SNUG = Fit(HOLE, _shaft("s-snug", 0.010, -0.005))
TIGHT = Fit(HOLE, _shaft("s-tight", 0.045, 0.030))


class TestWhatAFitGives:
    def test_a_clearance_fit_by_hand(self) -> None:
        # largest: 20.020 - 19.960 = 0.060; smallest: 20.000 - 19.980 = 0.020.
        assert LOOSE.largest_clearance_mm == pytest.approx(0.060)
        assert LOOSE.smallest_clearance_mm == pytest.approx(0.020)
        assert LOOSE.kind is FitKind.CLEARANCE

    def test_a_transition_fit_can_go_either_way(self) -> None:
        # largest 20.020 - 19.995 = 0.025; smallest 20.000 - 20.010 = -0.010.
        assert SNUG.largest_clearance_mm == pytest.approx(0.025)
        assert SNUG.smallest_clearance_mm == pytest.approx(-0.010)
        assert SNUG.kind is FitKind.TRANSITION

    def test_an_interference_fit_never_clears(self) -> None:
        # largest 20.020 - 20.030 = -0.010; smallest 20.000 - 20.045 = -0.045.
        assert TIGHT.largest_clearance_mm == pytest.approx(-0.010)
        assert TIGHT.kind is FitKind.INTERFERENCE

    def test_the_combined_tolerance_is_both_zones(self) -> None:
        assert LOOSE.combined_tolerance_mm == pytest.approx(0.040)

    def test_the_payload_carries_both_sources(self) -> None:
        assert LOOSE.to_dict()["sources"] == [FIXTURE, FIXTURE]


class TestChoosingAFit:
    def test_only_fits_that_always_clear_the_minimum_qualify(self) -> None:
        running = ClearanceRequirement(0.010, 0.080, "fixture oil film")
        selection = select_fit([LOOSE, SNUG, TIGHT], running)
        assert [fit.name for fit in selection.qualifying] == ["h-a/s-loose"]
        assert selection.chosen is LOOSE
        reasons = {r.fit.name: r.reason for r in selection.rejected}
        assert "below" in reasons["h-a/s-snug"]

    def test_a_press_fit_needs_grip_and_a_ceiling(self) -> None:
        press = ClearanceRequirement(-0.050, -0.005, "fixture grip")
        selection = select_fit([LOOSE, SNUG, TIGHT], press)
        assert selection.chosen is TIGHT

    def test_the_widest_qualifying_tolerance_is_chosen(self) -> None:
        wide = Fit(_hole("h-wide", 0.033, 0.0), _shaft("s-wide", -0.020, -0.053))
        running = ClearanceRequirement(0.010, 0.100, "fixture oil film")
        selection = select_fit([LOOSE, wide], running)
        assert {f.name for f in selection.qualifying} == {"h-a/s-loose", "h-wide/s-wide"}
        assert selection.chosen is wide

    def test_nothing_qualifies_is_none_with_every_reason(self) -> None:
        impossible = ClearanceRequirement(0.100, 0.200, "fixture")
        selection = select_fit([LOOSE, SNUG], impossible)
        assert selection.chosen is None
        assert len(selection.rejected) == 2
        assert selection.to_dict()["chosen"] is None


class TestWhatIsRefused:
    def test_a_zone_with_no_source(self) -> None:
        with pytest.raises(SourceError):
            ToleranceZone("x", Feature.HOLE, 20.0, 0.02, 0.0, "")

    def test_deviations_the_wrong_way_round(self) -> None:
        with pytest.raises(FitError, match="Swap"):
            _hole("x", 0.0, 0.02)

    def test_a_fit_across_two_nominal_sizes(self) -> None:
        with pytest.raises(FitError, match="one nominal size"):
            Fit(_hole("h", 0.02, 0.0, 20.0), _shaft("s", 0.0, -0.02, 25.0))

    def test_a_shaft_passed_as_the_hole(self) -> None:
        with pytest.raises(FitError, match="hole zone with a shaft zone"):
            Fit(_shaft("s", 0.0, -0.02), HOLE)

    def test_an_empty_requirement(self) -> None:
        with pytest.raises(FitError, match="empty"):
            ClearanceRequirement(0.05, 0.01, "fixture")

    def test_no_candidates(self) -> None:
        with pytest.raises(FitError, match="no candidate"):
            select_fit([], ClearanceRequirement(0.0, 0.1, "fixture"))
