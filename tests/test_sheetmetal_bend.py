"""Bend allowance, setback and deduction, checked against arithmetic done here.

Every expected number in this file is either computed from the formula in the
test itself or written out as a decimal somebody can check with a calculator —
never copied from a run. The worked example throughout is the one the phase
brief names: a 90 degree bend, 2 mm material, 3 mm inside radius, K = 0.44.

    BA = (pi/180) * 90 * (3 + 0.44*2) = (pi/2) * 3.88 = 6.094689747964199 mm
    SB = tan(45 deg) * (3 + 2)        = 5.0 mm
    BD = 2*5.0 - 6.094689747964199    = 3.905310252035801 mm

Offline, no database, no kernel.
"""

from __future__ import annotations

import math

import pytest

from app.sheetmetal.bend import (
    STRAIGHT_ANGLE_DEG,
    Bend,
    BendDirection,
    LengthConvention,
    bend_allowance_mm,
    bend_deduction_mm,
    setback_mm,
)
from app.sheetmetal.errors import BendError
from app.sheetmetal.kfactor import assumed, din6935

#: The brief's worked example. `assumed` because 0.44 is a round number chosen to
#: make the hand arithmetic legible, not a value off a table — and the package
#: refuses to let that pass unlabelled, which `test_sheetmetal_kfactor` checks.
K44 = assumed(0.44, why="the phase brief's worked example, pinned by hand arithmetic")

THICKNESS = 2.0
RADIUS = 3.0
ANGLE = 90.0

EXPECTED_BA = 6.094689747964199
EXPECTED_SB = 5.0
EXPECTED_BD = 3.905310252035801


class TestBendAllowance:
    def test_the_worked_example_matches_hand_arithmetic(self) -> None:
        by_hand = (math.pi / 180.0) * 90.0 * (3.0 + 0.44 * 2.0)
        assert by_hand == pytest.approx(EXPECTED_BA, abs=1e-12)
        assert bend_allowance_mm(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        ) == pytest.approx(EXPECTED_BA, abs=1e-12)

    def test_the_worked_example_matches_a_decimal_written_out(self) -> None:
        # (pi/2) * 3.88, to five decimal places, checkable on a calculator.
        assert bend_allowance_mm(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        ) == pytest.approx(6.09469, abs=5e-6)

    def test_allowance_is_the_neutral_arc_so_a_full_turn_is_its_circumference(self) -> None:
        """A 180 degree bend traverses half the neutral circle. Independent check."""
        half_circle = math.pi * (RADIUS + 0.44 * THICKNESS)
        assert bend_allowance_mm(
            angle_deg=180.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        ) == pytest.approx(half_circle, abs=1e-12)

    def test_allowance_is_linear_in_the_angle(self) -> None:
        one = bend_allowance_mm(
            angle_deg=30.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        )
        three = bend_allowance_mm(
            angle_deg=90.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        )
        assert three == pytest.approx(3.0 * one, abs=1e-12)

    def test_k_of_a_half_puts_the_neutral_axis_at_the_mid_plane(self) -> None:
        mid_plane_arc = (math.pi / 2.0) * (RADIUS + THICKNESS / 2.0)
        assert bend_allowance_mm(
            angle_deg=90.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.5
        ) == pytest.approx(mid_plane_arc, abs=1e-12)


class TestSetbackAndDeduction:
    def test_setback_of_the_worked_example(self) -> None:
        by_hand = math.tan(math.radians(90.0) / 2.0) * (RADIUS + THICKNESS)
        assert by_hand == pytest.approx(EXPECTED_SB, abs=1e-12)
        assert setback_mm(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS
        ) == pytest.approx(EXPECTED_SB, abs=1e-12)

    def test_deduction_of_the_worked_example(self) -> None:
        assert bend_deduction_mm(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        ) == pytest.approx(EXPECTED_BD, abs=1e-12)
        assert EXPECTED_BD == pytest.approx(2 * EXPECTED_SB - EXPECTED_BA, abs=1e-12)

    def test_deduction_is_exactly_two_setbacks_less_the_allowance(self) -> None:
        """The identity, at an angle where nothing is round."""
        angle, radius, thickness, k = 37.5, 1.6, 1.2, 0.41
        allowance = bend_allowance_mm(
            angle_deg=angle, inside_radius_mm=radius, thickness_mm=thickness, k=k
        )
        setback = setback_mm(
            angle_deg=angle, inside_radius_mm=radius, thickness_mm=thickness
        )
        assert bend_deduction_mm(
            angle_deg=angle, inside_radius_mm=radius, thickness_mm=thickness, k=k
        ) == pytest.approx(2.0 * setback - allowance, abs=1e-12)

    def test_inside_mould_line_setback_drops_the_thickness(self) -> None:
        inside = setback_mm(
            angle_deg=ANGLE,
            inside_radius_mm=RADIUS,
            thickness_mm=THICKNESS,
            convention=LengthConvention.INSIDE_MOULD_LINE,
        )
        assert inside == pytest.approx(math.tan(math.pi / 4.0) * RADIUS, abs=1e-12)
        assert inside == pytest.approx(3.0, abs=1e-12)
        outside = setback_mm(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS
        )
        # The two differ by t*tan(theta/2) per bend end -- 2 mm here.
        assert outside - inside == pytest.approx(THICKNESS * math.tan(math.pi / 4.0), abs=1e-12)

    def test_tangent_convention_has_no_setback_so_the_deduction_is_minus_the_allowance(
        self,
    ) -> None:
        assert (
            setback_mm(
                angle_deg=ANGLE,
                inside_radius_mm=RADIUS,
                thickness_mm=THICKNESS,
                convention=LengthConvention.TANGENT,
            )
            == 0.0
        )
        assert bend_deduction_mm(
            angle_deg=ANGLE,
            inside_radius_mm=RADIUS,
            thickness_mm=THICKNESS,
            k=0.44,
            convention=LengthConvention.TANGENT,
        ) == pytest.approx(-EXPECTED_BA, abs=1e-12)

    def test_a_shallow_bend_has_a_small_setback(self) -> None:
        setback = setback_mm(angle_deg=10.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS)
        assert setback == pytest.approx(math.tan(math.radians(5.0)) * 5.0, abs=1e-12)
        assert setback == pytest.approx(0.4374433, abs=1e-6)

    def test_the_outside_deduction_is_positive_at_every_angle(self) -> None:
        """Not an observation: `tan(x) >= x` and `r+t > r+K*t`, so 2*SB > BA always.

        Worth pinning because a negative deduction would read as "the blank is
        longer than the mould-line dimensions", which is true in the tangent
        convention and never in this one — and a sign error there is a blank
        wrong by twice the deduction rather than by nothing.
        """
        for angle in (1.0, 10.0, 45.0, 90.0, 135.0, 179.0):
            deduction = bend_deduction_mm(
                angle_deg=angle, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
            )
            assert deduction > 0.0, angle
        # It grows without bound as the bend approaches a hem.
        assert bend_deduction_mm(
            angle_deg=179.9, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
        ) > 1000.0


class TestGuards:
    """Each of these is a guard verified by breaking the thing it guards."""

    def test_a_hem_has_no_outside_mould_line_and_the_setback_is_refused(self) -> None:
        # tan(90 deg) is 1.6e16 in floating point, not an error, so without the
        # guard this returns a blank length of tens of millions of millimetres.
        with pytest.raises(BendError, match="never meet"):
            setback_mm(
                angle_deg=STRAIGHT_ANGLE_DEG,
                inside_radius_mm=RADIUS,
                thickness_mm=THICKNESS,
            )

    def test_a_hem_still_has_a_bend_allowance(self) -> None:
        assert bend_allowance_mm(
            angle_deg=STRAIGHT_ANGLE_DEG,
            inside_radius_mm=RADIUS,
            thickness_mm=THICKNESS,
            k=0.44,
        ) > 0.0

    def test_a_hem_dimensioned_tangent_to_tangent_is_fine(self) -> None:
        assert (
            setback_mm(
                angle_deg=STRAIGHT_ANGLE_DEG,
                inside_radius_mm=RADIUS,
                thickness_mm=THICKNESS,
                convention=LengthConvention.TANGENT,
            )
            == 0.0
        )

    def test_a_zero_angle_is_not_a_bend(self) -> None:
        with pytest.raises(BendError, match="not a bend"):
            bend_allowance_mm(
                angle_deg=0.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
            )

    def test_an_angle_past_180_is_refused_and_the_message_names_the_included_angle(
        self,
    ) -> None:
        with pytest.raises(BendError, match="back past itself"):
            bend_allowance_mm(
                angle_deg=225.0, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=0.44
            )

    def test_a_zero_radius_is_refused(self) -> None:
        with pytest.raises(BendError):
            bend_allowance_mm(
                angle_deg=ANGLE, inside_radius_mm=0.0, thickness_mm=THICKNESS, k=0.44
            )

    def test_a_zero_thickness_is_refused(self) -> None:
        with pytest.raises(BendError):
            bend_allowance_mm(
                angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=0.0, k=0.44
            )

    @pytest.mark.parametrize("bad_k", [0.0, -0.1, 0.6, 1.0, math.nan])
    def test_a_k_outside_the_physical_range_is_refused(self, bad_k: float) -> None:
        with pytest.raises(BendError):
            bend_allowance_mm(
                angle_deg=ANGLE, inside_radius_mm=RADIUS, thickness_mm=THICKNESS, k=bad_k
            )


class TestBendObject:
    def test_a_bend_delegates_to_the_same_arithmetic(self) -> None:
        bend = Bend(
            angle_deg=ANGLE,
            inside_radius_mm=RADIUS,
            direction=BendDirection.UP,
            k=K44,
            name="b1",
        )
        assert bend.allowance_mm(THICKNESS) == pytest.approx(EXPECTED_BA, abs=1e-12)
        assert bend.setback_mm(
            THICKNESS, LengthConvention.OUTSIDE_MOULD_LINE
        ) == pytest.approx(EXPECTED_SB, abs=1e-12)
        assert bend.deduction_mm(
            THICKNESS, LengthConvention.OUTSIDE_MOULD_LINE
        ) == pytest.approx(EXPECTED_BD, abs=1e-12)
        assert bend.r_over_t(THICKNESS) == pytest.approx(1.5)

    def test_direction_changes_nothing_in_the_arithmetic(self) -> None:
        up = Bend(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, direction=BendDirection.UP, k=K44
        )
        down = Bend(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, direction=BendDirection.DOWN, k=K44
        )
        assert up.allowance_mm(THICKNESS) == down.allowance_mm(THICKNESS)
        assert up.setback_mm(THICKNESS, LengthConvention.OUTSIDE_MOULD_LINE) == down.setback_mm(
            THICKNESS, LengthConvention.OUTSIDE_MOULD_LINE
        )

    def test_an_unnamed_bend_still_has_a_label(self) -> None:
        bend = Bend(
            angle_deg=ANGLE, inside_radius_mm=RADIUS, direction=BendDirection.UP, k=K44
        )
        assert bend.label == "bend 90 deg R3"

    def test_a_bend_refuses_an_impossible_angle_at_construction(self) -> None:
        with pytest.raises(BendError):
            Bend(
                angle_deg=181.0,
                inside_radius_mm=RADIUS,
                direction=BendDirection.UP,
                k=K44,
            )

    def test_to_dict_carries_the_k_and_its_basis(self) -> None:
        bend = Bend(
            angle_deg=ANGLE,
            inside_radius_mm=RADIUS,
            direction=BendDirection.DOWN,
            k=din6935(inside_radius_mm=RADIUS, thickness_mm=THICKNESS),
            name="b1",
        )
        payload = bend.to_dict(THICKNESS)
        assert payload["direction"] == "down"
        assert payload["r_over_t"] == pytest.approx(1.5)
        assert payload["k"]["has_stated_basis"] is True
        assert "DIN 6935" in payload["k"]["source"]["citation"]
