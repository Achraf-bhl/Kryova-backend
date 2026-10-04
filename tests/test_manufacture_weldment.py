"""A welded frame's cut list, beads and weld sizing, and a bent tube's bend table (E17.3).

Every expected number is worked by hand beside its assertion. Strengths, ratios and loads are
test fixtures with sources that say so.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math

import pytest

from app.design.assertions import Outcome
from app.manufacture.tubing import TubeError, route
from app.manufacture.weldment import (
    EndCut,
    FilletWeld,
    Member,
    WeldmentError,
    WeldSide,
    WeldStrength,
    weldment,
)
from app.rules.processes import Limit
from app.solve.sections import BeamSection, BoxProfile

FIXTURE = "test fixture, not a standard"
RHS = BoxProfile(width_mm=60.0, height_mm=40.0, wall_mm=4.0)
FLAT = BeamSection(profile=RHS, n1=(0.0, 0.0, 1.0))  # the 60 face up, 40 deep in plane


def _rectangle(**kwargs: object):  # type: ignore[no-untyped-def]
    corners = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (1000.0, 600.0, 0.0), (0.0, 600.0, 0.0)]
    members = [
        Member(f"side{i}", FLAT, corners[i], corners[(i + 1) % 4]) for i in range(4)
    ]
    return weldment("frame", members, **kwargs)  # type: ignore[arg-type]


class TestTheCutList:
    def test_a_rectangle_is_two_pairs_of_double_mitred_lengths(self) -> None:
        lines = _rectangle().cut_list()
        assert [(line.quantity, line.centreline_mm) for line in lines] == [
            (2, pytest.approx(1000.0)),
            (2, pytest.approx(600.0)),
        ]
        assert all(line.ends == ("mitre 45.0", "mitre 45.0") for line in lines)

    def test_a_45_degree_mitre_adds_half_the_depth_in_the_plane_at_each_end(self) -> None:
        # depth in the xy plane is the 40 mm side: 40/2 x tan 45 = 20 per end.
        [long_line] = [line for line in _rectangle().cut_list() if line.centreline_mm > 900]
        assert long_line.long_point_mm == pytest.approx(1040.0)

    def test_standing_the_section_on_edge_changes_the_depth_that_is_mitred(self) -> None:
        tall = BeamSection(profile=RHS, n1=(0.0, 1.0, 0.0))
        corners = [(0.0, 0.0, 0.0), (500.0, 0.0, 0.0), (500.0, 500.0, 0.0)]
        a = Member("a", tall, corners[0], corners[1])
        b = Member("b", BeamSection(profile=RHS, n1=(1.0, 0.0, 0.0)), corners[1], corners[2])
        piece = next(p for p in weldment("l", [a, b]).pieces() if p.member == "a")
        # axis 1 (60) now lies in the plane: 60/2 x tan 45 = 30 at the mitred end only.
        assert piece.ends[0].cut is EndCut.SQUARE
        assert piece.long_point_mm == pytest.approx(530.0)

    def test_a_section_rotated_off_the_joint_plane_has_no_mitre_length(self) -> None:
        s = 1.0 / math.sqrt(2.0)
        skew = BeamSection(profile=RHS, n1=(0.0, s, s))
        a = Member("a", skew, (0.0, 0.0, 0.0), (500.0, 0.0, 0.0))
        b = Member("b", FLAT, (500.0, 0.0, 0.0), (500.0, 500.0, 0.0))
        piece = next(p for p in weldment("l", [a, b]).pieces() if p.member == "a")
        assert piece.ends[1].cut is EndCut.MITRE
        assert piece.long_point_mm is None
        assert "not computed" in piece.ends[1].note

    def test_three_members_at_one_node_are_coped_not_computed(self) -> None:
        node = (0.0, 0.0, 0.0)
        members = [
            Member("x", FLAT, node, (500.0, 0.0, 0.0)),
            Member("y", FLAT, node, (0.0, 500.0, 0.0)),
            Member("z", BeamSection(profile=RHS, n1=(1.0, 0.0, 0.0)), node, (0.0, 0.0, 500.0)),
        ]
        for piece in weldment("tripod", members).pieces():
            assert piece.ends[0].cut is EndCut.COPED
            assert piece.long_point_mm is None

    def test_two_members_in_line_butt_square(self) -> None:
        a = Member("a", FLAT, (0.0, 0.0, 0.0), (500.0, 0.0, 0.0))
        b = Member("b", FLAT, (500.0, 0.0, 0.0), (900.0, 0.0, 0.0))
        piece = next(p for p in weldment("bar", [a, b]).pieces() if p.member == "a")
        assert piece.ends[1].cut is EndCut.SQUARE
        assert "in line" in piece.ends[1].note

    def test_stock_lengths_total_the_centrelines(self) -> None:
        assert _rectangle().stock_lengths_mm() == {"RHS 60x40x4": pytest.approx(3200.0)}


class TestBeadsAndLabels:
    def test_the_leg_is_the_throat_times_root_two(self) -> None:
        weld = FilletWeld("w", ("side0", "side1"), throat_mm=5.0, length_mm=100.0)
        assert weld.leg_mm == pytest.approx(5.0 * math.sqrt(2.0))

    def test_the_bead_is_a_squared_per_run(self) -> None:
        weld = FilletWeld("w", ("side0", "side1"), 4.0, 40.0, side=WeldSide.BOTH)
        assert weld.bead_volume_mm3 == pytest.approx(2 * 16.0 * 40.0)

    def test_the_label_uses_the_iso_2553_throat_prefix(self) -> None:
        weld = FilletWeld("w", ("side0", "side1"), 4.0, 40.0, side=WeldSide.BOTH, all_around=True)
        assert weld.label() == "a4 fillet 40, both sides, all around"

    def test_the_mass_adds_weld_metal_to_the_members(self) -> None:
        weld = FilletWeld("w", ("side0", "side1"), 4.0, 40.0, side=WeldSide.BOTH)
        frame = _rectangle(welds=[weld], density_kg_m3=7850.0, density_source=FIXTURE)
        # RHS area 60x40 - 52x32 = 736 mm2; 736 x 3200 + 1280 mm3 of weld.
        expected = (736.0 * 3200.0 + 1280.0) * 1e-9 * 7850.0
        assert frame.mass_kg() == pytest.approx(expected)

    def test_no_density_is_no_mass(self) -> None:
        assert _rectangle().mass_kg() is None
        assert _rectangle().to_dict()["mass_kg"] is None


class TestWeldSizing:
    STRENGTH = WeldStrength(design_shear_strength=Limit(200.0, FIXTURE))

    def _frame(self, **weld_kwargs: object):  # type: ignore[no-untyped-def]
        kwargs: dict[str, object] = {"throat_mm": 4.0, "length_mm": 100.0}
        kwargs.update(weld_kwargs)
        weld = FilletWeld("corner", ("side0", "side1"), **kwargs)  # type: ignore[arg-type]
        return _rectangle(welds=[weld])

    def test_a_throat_above_force_over_strength_passes(self) -> None:
        # 600 N/mm / 200 MPa = 3 mm required; 4 mm given.
        frame = self._frame(force_per_length_n_mm=600.0, force_source="fixture load")
        [result] = frame.check_welds(self.STRENGTH).results
        assert result.outcome is Outcome.PASSED
        assert result.measured == pytest.approx(1.0)

    def test_a_throat_below_it_fails(self) -> None:
        # 1000 / 200 = 5 mm required; 4 mm given.
        frame = self._frame(force_per_length_n_mm=1000.0, force_source="fixture load")
        [result] = frame.check_welds(self.STRENGTH).results
        assert result.outcome is Outcome.FAILED

    def test_a_weld_with_no_force_is_unmeasured_not_passed(self) -> None:
        report = self._frame().check_welds(self.STRENGTH)
        assert report.results[0].outcome is Outcome.UNMEASURED
        assert not report.ok

    @pytest.mark.parametrize(("throat", "outcome"), [(2.0, Outcome.PASSED), (4.0, Outcome.FAILED)])
    def test_the_throat_against_the_thinner_wall(self, throat: float, outcome: Outcome) -> None:
        strength = WeldStrength(
            design_shear_strength=Limit(200.0, FIXTURE),
            maximum_throat_to_wall=Limit(0.7, FIXTURE),
        )
        frame = self._frame(throat_mm=throat, force_per_length_n_mm=100.0, force_source="fixture")
        by_name = {r.name: r.outcome for r in frame.check_welds(strength).results}
        assert by_name["corner.not_oversized"] is outcome

    def test_the_margin_is_approximated(self) -> None:
        frame = self._frame(force_per_length_n_mm=600.0, force_source="fixture load")
        report = frame.check_welds(self.STRENGTH)
        assert report.results[0].approximate


class TestWhatAWeldmentRefuses:
    def test_an_n1_along_the_member(self) -> None:
        with pytest.raises(WeldmentError, match="not perpendicular"):
            Member("a", BeamSection(profile=RHS, n1=(1.0, 0.0, 0.0)), (0, 0, 0), (100.0, 0, 0))  # type: ignore[arg-type]

    def test_a_member_of_no_length(self) -> None:
        with pytest.raises(WeldmentError, match="no length"):
            Member("a", FLAT, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

    def test_members_named_twice(self) -> None:
        a = Member("a", FLAT, (0.0, 0.0, 0.0), (100.0, 0.0, 0.0))
        with pytest.raises(WeldmentError, match="named twice"):
            weldment("f", [a, a])

    def test_a_weld_to_a_member_that_is_not_there(self) -> None:
        with pytest.raises(WeldmentError, match="not a member"):
            _rectangle(welds=[FilletWeld("w", ("side0", "ghost"), 4.0, 40.0)])

    def test_a_force_with_no_source(self) -> None:
        with pytest.raises(WeldmentError, match="source"):
            FilletWeld("w", ("a", "b"), 4.0, 40.0, force_per_length_n_mm=100.0)

    def test_a_density_with_no_source(self) -> None:
        with pytest.raises(WeldmentError, match="source"):
            _rectangle(density_kg_m3=7850.0)


class TestATubeRoute:
    def test_an_l_bend_by_hand(self) -> None:
        tube = route(
            "l",
            [(0, 0, 0), (100, 0, 0), (100, 100, 0)],
            bend_radius_mm=20.0,
            outside_diameter_mm=12.0,
            wall_mm=1.0,
        )
        [bend] = tube.bends
        # theta = 90, T = 20 tan 45 = 20, straights 80 and 80, arc 10 pi.
        assert bend.angle_deg == pytest.approx(90.0)
        assert bend.tangent_mm == pytest.approx(20.0)
        assert bend.feed_mm == pytest.approx(80.0)
        assert tube.final_straight_mm == pytest.approx(80.0)
        assert tube.developed_length_mm == pytest.approx(160.0 + 10.0 * math.pi)

    def test_the_rotation_between_bend_planes_is_signed_about_the_feed(self) -> None:
        tube = route(
            "z",
            [(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100)],
            bend_radius_mm=10.0,
            outside_diameter_mm=12.0,
            wall_mm=1.0,
        )
        first, second = tube.bends
        # plane normals z then x; (z cross x) = y, the feed direction, so +90.
        assert first.rotation_deg == 0.0
        assert second.rotation_deg == pytest.approx(90.0)
        # the middle run loses a tangent at each end: 100 - 10 - 10.
        assert second.feed_mm == pytest.approx(80.0)

    def test_bends_that_overlap_are_refused(self) -> None:
        with pytest.raises(TubeError, match="overlap"):
            route(
                "tight",
                [(0, 0, 0), (100, 0, 0), (100, 30, 0), (0, 30, 0)],
                bend_radius_mm=20.0,
                outside_diameter_mm=12.0,
                wall_mm=1.0,
            )

    def test_a_waypoint_on_a_straight_line_is_refused(self) -> None:
        with pytest.raises(TubeError, match="straight line"):
            route(
                "s",
                [(0, 0, 0), (50, 0, 0), (100, 0, 0)],
                bend_radius_mm=10.0,
                outside_diameter_mm=12.0,
                wall_mm=1.0,
            )

    def test_a_wall_that_does_not_fit(self) -> None:
        with pytest.raises(TubeError, match="does not fit"):
            route("w", [(0, 0, 0), (1, 0, 0)], bend_radius_mm=1.0, outside_diameter_mm=4.0, wall_mm=2.0)

    def test_the_bender_limits_are_the_callers(self) -> None:
        tube = route(
            "z",
            [(0, 0, 0), (100, 0, 0), (100, 100, 0), (100, 100, 100)],
            bend_radius_mm=10.0,
            outside_diameter_mm=12.0,
            wall_mm=1.0,
        )
        report = tube.check(
            minimum_bend_radius=Limit(24.0, FIXTURE),
            minimum_straight_between_bends=Limit(50.0, FIXTURE),
        )
        by_name = {r.name: r.outcome for r in report.results}
        assert by_name["z.bend_radius"] is Outcome.FAILED
        assert by_name["z.straight_between_bends"] is Outcome.PASSED
