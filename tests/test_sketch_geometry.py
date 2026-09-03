"""The 2D constructions behind the sketch-editing tools.

These check geometry, not plumbing: that a fillet is actually tangent, that a
chamfer dimensioned by angle meets that angle, that a mirrored arc is the arc
that was asked for rather than its complement. All of it is decidable on any
machine, which is the reason `sketch_geometry` is a module of its own — the COM
binding around it cannot be tested off Windows, and this is the part where the
mistakes would otherwise be invisible until a part came out wrong.
"""

from __future__ import annotations

import math

import pytest

from scripts.catia_bridge.sketch_geometry import (
    Arc,
    Segment,
    SketchGeometryError,
    apply,
    chamfer,
    circular_pattern,
    corner,
    line_intersection,
    offset,
    rectangular_pattern,
    reflection,
    rotation,
    scaling,
    translation,
    trim,
)

#: A right angle at the origin: one arm up the +u axis, one up the +v axis.
RIGHT_ANGLE = (Segment((50.0, 0.0), (0.0, 0.0)), Segment((0.0, 0.0), (0.0, 50.0)))


def distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.dist(first, second)


class TestLineIntersection:
    def test_crossing_lines_meet_where_they_cross(self) -> None:
        point = line_intersection(
            Segment((-10.0, 0.0), (10.0, 0.0)), Segment((0.0, -10.0), (0.0, 10.0))
        )
        assert point == pytest.approx((0.0, 0.0))

    def test_segments_that_stop_short_still_meet(self) -> None:
        # The infinite lines are what matter: extending to a corner is half of
        # what the trim tool is for, and refusing here would reject that case.
        point = line_intersection(
            Segment((-10.0, 0.0), (-5.0, 0.0)), Segment((0.0, 5.0), (0.0, 10.0))
        )
        assert point == pytest.approx((0.0, 0.0))

    def test_parallel_lines_are_refused_by_name(self) -> None:
        with pytest.raises(SketchGeometryError, match="parallel"):
            line_intersection(
                Segment((0.0, 0.0), (10.0, 0.0)), Segment((0.0, 5.0), (10.0, 5.0))
            )


class TestCorner:
    def test_the_arc_is_tangent_to_both_elements(self) -> None:
        # Tangency is the whole property. A fillet that is nearly tangent pads
        # into a solid with a visible facet at the join.
        result = corner(*RIGHT_ANGLE, 10.0)
        assert distance(result.arc.centre, (10.0, 10.0)) == pytest.approx(0.0, abs=1e-9)
        assert result.arc.radius == pytest.approx(10.0)

    def test_the_elements_are_trimmed_to_the_tangent_points(self) -> None:
        result = corner(*RIGHT_ANGLE, 10.0)
        assert result.first.end == pytest.approx((10.0, 0.0))
        assert result.second.start == pytest.approx((0.0, 10.0))

    def test_the_surviving_arm_keeps_its_far_endpoint(self) -> None:
        result = corner(*RIGHT_ANGLE, 10.0)
        assert result.first.start == pytest.approx((50.0, 0.0))
        assert result.second.end == pytest.approx((0.0, 50.0))

    def test_the_arc_sweeps_the_short_way_round(self) -> None:
        # An arc taken in the order the caller named the elements is the
        # complementary arc half the time -- the 270 degrees that are not the
        # corner. The sweep must be the minor arc.
        result = corner(*RIGHT_ANGLE, 10.0)
        sweep = (result.arc.end_angle - result.arc.start_angle) % (2 * math.pi)
        assert sweep == pytest.approx(math.pi / 2)

    def test_a_sharp_corner_takes_a_longer_bite_than_a_right_angle(self) -> None:
        # The trim distance is r / tan(theta / 2), so it grows without bound as
        # the corner closes: the radius that eats 5 mm of a right angle eats
        # nearly 19 mm of a 30-degree one. Getting the half-angle wrong still
        # produces a tangent arc, just not the one the drawing dimensions.
        sharp = (
            Segment((100.0, 0.0), (0.0, 0.0)),
            Segment(
                (0.0, 0.0),
                (
                    100.0 * math.cos(math.radians(30.0)),
                    100.0 * math.sin(math.radians(30.0)),
                ),
            ),
        )
        result = corner(*sharp, 5.0)
        assert distance(result.corner, result.first.end) == pytest.approx(
            5.0 / math.tan(math.radians(15.0))
        )
        assert distance(result.corner, result.first.end) > 5.0

    def test_a_radius_that_does_not_fit_says_so_with_both_numbers(self) -> None:
        short = (Segment((5.0, 0.0), (0.0, 0.0)), Segment((0.0, 0.0), (0.0, 5.0)))
        with pytest.raises(SketchGeometryError, match="smaller radius"):
            corner(*short, 40.0)

    def test_collinear_elements_have_no_corner(self) -> None:
        with pytest.raises(SketchGeometryError, match="parallel"):
            corner(
                Segment((0.0, 0.0), (10.0, 0.0)), Segment((10.0, 0.0), (20.0, 0.0)), 1.0
            )

    def test_all_but_parallel_elements_are_caught_by_the_second_guard(self) -> None:
        # Not parallel enough for the determinant test -- these genuinely meet,
        # a kilometre away. Two arms that are all but in line have no usable
        # bisector, and without this guard the fillet centre lands off in space
        # and CATIA is handed coordinates in the millions.
        with pytest.raises(SketchGeometryError, match="in line"):
            corner(
                Segment((0.0, 0.0), (100.0, 0.0)),
                Segment((0.0, 1e-8), (100.0, 2e-8)),
                1.0,
            )

    def test_a_negative_radius_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="greater than zero"):
            corner(*RIGHT_ANGLE, -1.0)


class TestChamfer:
    def test_equal_lengths_at_forty_five_degrees(self) -> None:
        result = chamfer(*RIGHT_ANGLE, 10.0)
        assert result.second_length == pytest.approx(10.0)
        assert result.line.start == pytest.approx((10.0, 0.0))
        assert result.line.end == pytest.approx((0.0, 10.0))

    def test_two_explicit_lengths_are_used_as_given(self) -> None:
        result = chamfer(*RIGHT_ANGLE, 10.0, second_length=4.0)
        assert result.first_length == pytest.approx(10.0)
        assert result.second_length == pytest.approx(4.0)
        assert result.line.end == pytest.approx((0.0, 4.0))

    def test_the_angle_form_produces_that_angle(self) -> None:
        # The property being checked is the one on the drawing: the angle
        # between the chamfer and the first element. Solved by the sine rule,
        # so this is what catches an inverted or complementary mistake.
        result = chamfer(*RIGHT_ANGLE, 10.0, angle_deg=30.0)
        # Measured at the chamfer's own end of element one, between the element
        # (running back to the corner) and the chamfer -- which is the angle a
        # drawing dimensions and the one the sine rule was solved for.
        towards_corner = (
            result.corner[0] - result.line.start[0],
            result.corner[1] - result.line.start[1],
        )
        along_chamfer = (
            result.line.end[0] - result.line.start[0],
            result.line.end[1] - result.line.start[1],
        )
        cosine = (
            towards_corner[0] * along_chamfer[0] + towards_corner[1] * along_chamfer[1]
        ) / (math.hypot(*towards_corner) * math.hypot(*along_chamfer))
        assert math.degrees(math.acos(cosine)) == pytest.approx(30.0)

    def test_an_angle_that_cannot_close_the_triangle_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="must stay below"):
            chamfer(*RIGHT_ANGLE, 10.0, angle_deg=120.0)

    def test_a_chamfer_longer_than_its_element_is_refused(self) -> None:
        short = (Segment((5.0, 0.0), (0.0, 0.0)), Segment((0.0, 0.0), (0.0, 5.0)))
        with pytest.raises(SketchGeometryError, match="shorter chamfer"):
            chamfer(*short, 20.0)


class TestTrim:
    def test_overlong_elements_are_cut_back_to_the_crossing(self) -> None:
        first, second = trim(
            Segment((-2.0, 0.0), (10.0, 0.0)), Segment((0.0, -10.0), (0.0, 2.0))
        )
        assert first == Segment((0.0, 0.0), (10.0, 0.0))
        assert second == Segment((0.0, -10.0), (0.0, 0.0))

    def test_the_longer_portion_is_the_one_that_survives(self) -> None:
        # The interactive Sketcher settles this by asking which side you
        # clicked. There is no click here, so the short stub past the corner is
        # what goes -- and the rule has to be the same every time, or a profile
        # closes or opens depending on which order the elements were drawn in.
        first, _ = trim(
            Segment((-10.0, 0.0), (2.0, 0.0)), Segment((0.0, -10.0), (0.0, 10.0))
        )
        assert first == Segment((-10.0, 0.0), (0.0, 0.0))

    def test_elements_that_stop_short_are_extended_to_meet(self) -> None:
        # Same call, opposite effect. This is why the tool reports which of the
        # two happened rather than claiming to have trimmed.
        first, second = trim(
            Segment((-10.0, 0.0), (-5.0, 0.0)), Segment((0.0, 5.0), (0.0, 10.0))
        )
        assert first.end == pytest.approx((0.0, 0.0))
        assert second.start == pytest.approx((0.0, 0.0))
        assert first.length > 5.0

    def test_keeping_the_first_leaves_the_second_alone(self) -> None:
        original = Segment((0.0, -10.0), (0.0, 2.0))
        first, second = trim(Segment((-2.0, 0.0), (10.0, 0.0)), original, keep="first")
        assert first == Segment((0.0, 0.0), (10.0, 0.0))
        assert second == original

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="not a trim mode"):
            trim(*RIGHT_ANGLE, keep="neither")


class TestOffset:
    def test_a_line_moves_along_its_own_normal(self) -> None:
        moved = offset(Segment((0.0, 0.0), (10.0, 0.0)), 5.0)
        assert moved.start == pytest.approx((0.0, 5.0))
        assert moved.end == pytest.approx((10.0, 5.0))

    def test_reversing_offsets_the_other_way(self) -> None:
        moved = offset(Segment((0.0, 0.0), (10.0, 0.0)), 5.0, reverse=True)
        assert moved.start == pytest.approx((0.0, -5.0))

    def test_a_circle_changes_radius_and_keeps_its_centre(self) -> None:
        moved = offset(Arc((3.0, 4.0), 10.0), 2.5)
        assert moved.centre == pytest.approx((3.0, 4.0))
        assert moved.radius == pytest.approx(12.5)

    def test_offsetting_a_circle_inwards_past_its_centre_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="collapses it to nothing"):
            offset(Arc((0.0, 0.0), 5.0), 5.0, reverse=True)


class TestTransforms:
    def test_translation_moves_a_segment(self) -> None:
        moved = apply(Segment((0.0, 0.0), (1.0, 0.0)), translation((3.0, 4.0)))
        assert moved.start == pytest.approx((3.0, 4.0))
        assert moved.end == pytest.approx((4.0, 4.0))

    def test_rotation_turns_about_the_given_centre(self) -> None:
        moved = apply(Segment((10.0, 0.0), (20.0, 0.0)), rotation((0.0, 0.0), math.pi / 2))
        assert moved.start == pytest.approx((0.0, 10.0), abs=1e-9)
        assert moved.end == pytest.approx((0.0, 20.0), abs=1e-9)

    def test_rotation_carries_an_arc_sweep_with_it(self) -> None:
        turned = apply(Arc((0.0, 0.0), 5.0, 0.0, math.pi / 2), rotation((0.0, 0.0), math.pi))
        assert turned.start_angle == pytest.approx(math.pi)
        assert turned.end_angle == pytest.approx(3 * math.pi / 2)

    def test_scaling_holds_the_centre_still(self) -> None:
        moved = apply(Segment((10.0, 10.0), (20.0, 10.0)), scaling((10.0, 10.0), 2.0))
        assert moved.start == pytest.approx((10.0, 10.0))
        assert moved.end == pytest.approx((30.0, 10.0))

    def test_scaling_grows_an_arc_radius(self) -> None:
        grown = apply(Arc((0.0, 0.0), 5.0, 0.0, math.pi), scaling((0.0, 0.0), 3.0))
        assert grown.radius == pytest.approx(15.0)

    def test_a_zero_scale_factor_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="greater than zero"):
            scaling((0.0, 0.0), 0.0)

    def test_reflection_about_the_u_axis_flips_v(self) -> None:
        mirrored = apply(
            Segment((1.0, 2.0), (3.0, 4.0)), reflection(Segment((0.0, 0.0), (1.0, 0.0)))
        )
        assert mirrored.start == pytest.approx((1.0, -2.0))
        assert mirrored.end == pytest.approx((3.0, -4.0))

    def test_reflection_about_a_slanted_axis(self) -> None:
        # The 45-degree line swaps u and v; an axis that is not on a coordinate
        # direction is what catches a reflection matrix built from the wrong
        # angle, since the axis-aligned cases pass either way.
        mirrored = apply(
            Segment((1.0, 0.0), (5.0, 0.0)), reflection(Segment((0.0, 0.0), (1.0, 1.0)))
        )
        assert mirrored.start == pytest.approx((0.0, 1.0), abs=1e-9)
        assert mirrored.end == pytest.approx((0.0, 5.0), abs=1e-9)

    def test_a_mirrored_arc_is_the_arc_asked_for_not_its_complement(self) -> None:
        # Reflection reverses the direction of travel, so the two angles swap as
        # well as move. Leaving them in order draws the other 270 degrees of the
        # circle -- geometry that is present, wrong, and easy to miss on screen.
        quarter = Arc((0.0, 0.0), 10.0, 0.0, math.pi / 2)
        mirrored = apply(quarter, reflection(Segment((0.0, 0.0), (1.0, 0.0))))
        sweep = (mirrored.end_angle - mirrored.start_angle) % (2 * math.pi)
        assert sweep == pytest.approx(math.pi / 2)

    def test_a_mirrored_full_circle_stays_a_full_circle(self) -> None:
        mirrored = apply(Arc((5.0, 5.0), 2.0), reflection(Segment((0.0, 0.0), (1.0, 0.0))))
        assert mirrored.closed
        assert mirrored.centre == pytest.approx((5.0, -5.0))

    def test_a_zero_length_mirror_axis_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="zero length"):
            reflection(Segment((1.0, 1.0), (1.0, 1.0)))


class TestRectangularPattern:
    def test_the_original_is_not_repeated_on_top_of_itself(self) -> None:
        # A transform for the original would draw a second copy exactly over the
        # first: a profile that looks right and reports as ambiguous.
        assert len(rectangular_pattern(4, 10.0)) == 3

    def test_instances_step_by_the_spacing(self) -> None:
        placements = rectangular_pattern(3, 10.0)
        assert [p.apply((0.0, 0.0)) for p in placements] == [
            pytest.approx((10.0, 0.0)),
            pytest.approx((20.0, 0.0)),
        ]

    def test_a_grid_fills_both_directions(self) -> None:
        placements = rectangular_pattern(3, 10.0, second_count=2, second_spacing=25.0)
        assert len(placements) == 5
        assert placements[-1].apply((0.0, 0.0)) == pytest.approx((20.0, 25.0))

    def test_the_second_spacing_defaults_to_the_first(self) -> None:
        placements = rectangular_pattern(2, 10.0, second_count=2)
        assert placements[-1].apply((0.0, 0.0)) == pytest.approx((10.0, 10.0))

    def test_zero_spacing_is_refused(self) -> None:
        with pytest.raises(SketchGeometryError, match="greater than zero"):
            rectangular_pattern(3, 0.0)


class TestCircularPattern:
    def test_a_full_circle_divides_by_the_count(self) -> None:
        # Eight holes on a bolt circle are 45 degrees apart, not 51.4.
        placements = circular_pattern(8, (0.0, 0.0))
        assert len(placements) == 7
        assert math.degrees(placements[0].rotation) == pytest.approx(45.0)

    def test_a_partial_arc_puts_an_instance_at_each_end(self) -> None:
        placements = circular_pattern(3, (0.0, 0.0), total_angle=math.radians(90.0))
        assert math.degrees(placements[-1].rotation) == pytest.approx(90.0)

    def test_instances_orbit_the_given_centre(self) -> None:
        placements = circular_pattern(4, (10.0, 0.0))
        assert placements[0].apply((20.0, 0.0)) == pytest.approx((10.0, 10.0), abs=1e-9)

    def test_one_instance_is_not_a_pattern(self) -> None:
        with pytest.raises(SketchGeometryError, match="at least two"):
            circular_pattern(1, (0.0, 0.0))
