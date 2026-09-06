"""Flat patterns, checked against the flat-length arithmetic rather than against a run.

The running example is 2 mm sheet, 3 mm inside radius, 90 degree bends,
K = 0.44 — the phase brief's worked example, for which

    BA = (pi/2) * 3.88 = 6.094689747964199 mm
    SB = tan(45 deg) * 5 = 5.0 mm       (outside mould line)
    BD = 10 - BA = 3.905310252035801 mm

so an L of two 40 and 30 mm outside mould-line flanges flattens to
`40 + 30 - 3.905310252035801 = 66.094689747964199` mm. Every number below is
either that arithmetic written out again in the test or derived from it.

The most valuable test here is
`TestTheThreeConventionsDescribeTheSamePart`: one physical part declared three
ways — outside mould line, inside mould line, tangent to tangent — must produce
one blank. Getting a convention wrong is the commonest way a real flat pattern
is wrong, and it cannot be caught by checking any single declaration against
itself.

Offline, no database, no kernel.
"""

from __future__ import annotations

import math

import pytest

from app.sheetmetal.bend import Bend, BendDirection, LengthConvention
from app.sheetmetal.errors import BendError, UnfoldError
from app.sheetmetal.kfactor import assumed, din6935
from app.sheetmetal.material import SheetMaterial, sheet_material
from app.sheetmetal.unfold import (
    Edge,
    Flange,
    Hole,
    Joint,
    SheetMetalPart,
    unfold,
)

THICKNESS = 2.0
RADIUS = 3.0
BA = 6.094689747964199
SB = 5.0
BD = 3.905310252035801

K44 = assumed(0.44, why="the phase brief's worked example, pinned by hand arithmetic")


def steel() -> SheetMaterial:
    return sheet_material("steel_mild_cr", thickness_mm=THICKNESS)


def right_angle(name: str = "b1", k: object = None) -> Bend:
    return Bend(
        angle_deg=90.0,
        inside_radius_mm=RADIUS,
        direction=BendDirection.UP,
        k=K44 if k is None else k,  # type: ignore[arg-type]
        name=name,
    )


def l_bracket(
    convention: LengthConvention = LengthConvention.OUTSIDE_MOULD_LINE,
    *,
    base_length: float = 40.0,
    flange_length: float = 30.0,
    width: float = 100.0,
) -> SheetMetalPart:
    flange = Flange(name="flange", length_mm=flange_length)
    base = Flange(
        name="base",
        length_mm=base_length,
        width_mm=width,
        joints=(Joint(edge=Edge.FAR, bend=right_angle(), flange=flange),),
    )
    return SheetMetalPart(
        name="L bracket", material=steel(), convention=convention, root=base
    )


class TestFlatLength:
    def test_the_worked_example_flattens_to_the_hand_arithmetic(self) -> None:
        pattern = unfold(l_bracket())
        assert pattern.flat_length_mm == pytest.approx(40.0 + 30.0 - BD, abs=1e-12)
        assert pattern.flat_length_mm == pytest.approx(66.094689747964199, abs=1e-12)

    def test_it_also_equals_the_legs_plus_the_allowance(self) -> None:
        """The other half of `BD = 2*SB - BA`, computed independently here."""
        legs = (40.0 - SB) + (30.0 - SB)
        assert unfold(l_bracket()).flat_length_mm == pytest.approx(legs + BA, abs=1e-12)

    def test_the_blank_is_exactly_the_flat_length_long(self) -> None:
        pattern = unfold(l_bracket())
        width, height = pattern.blank_size_mm
        assert width == pytest.approx(pattern.flat_length_mm, abs=1e-12)
        assert height == pytest.approx(100.0, abs=1e-12)

    def test_a_u_channel_deducts_once_per_bend(self) -> None:
        far = Flange(name="far", length_mm=30.0)
        web = Flange(
            name="web",
            length_mm=60.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b2"), flange=far),),
        )
        near = Flange(
            name="near",
            length_mm=30.0,
            width_mm=80.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b1"), flange=web),),
        )
        part = SheetMetalPart(
            name="U channel",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=near,
        )
        pattern = unfold(part)
        assert pattern.flat_length_mm == pytest.approx(120.0 - 2.0 * BD, abs=1e-12)
        assert pattern.flat_length_mm == pytest.approx(112.1893794959284, abs=1e-12)
        assert len(pattern.bend_lines) == 2

    def test_a_shallower_bend_removes_less_material(self) -> None:
        """A 45 degree bend: BA and SB recomputed here from the formulae."""
        shallow = Bend(
            angle_deg=45.0,
            inside_radius_mm=RADIUS,
            direction=BendDirection.UP,
            k=K44,
            name="b45",
        )
        flange = Flange(name="flange", length_mm=30.0)
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=shallow, flange=flange),),
        )
        part = SheetMetalPart(
            name="45 bracket",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        allowance = (math.pi / 180.0) * 45.0 * (RADIUS + 0.44 * THICKNESS)
        setback = math.tan(math.radians(45.0) / 2.0) * (RADIUS + THICKNESS)
        assert unfold(part).flat_length_mm == pytest.approx(
            70.0 - (2.0 * setback - allowance), abs=1e-12
        )


class TestTheThreeConventionsDescribeTheSamePart:
    """One physical L declared three ways gives one blank."""

    def test_outside_inside_and_tangent_agree(self) -> None:
        outside = unfold(l_bracket(LengthConvention.OUTSIDE_MOULD_LINE))
        # Inside mould line: setback is tan(45)*r = 3.0, so the same legs are
        # 35+3 and 25+3 long.
        inside = unfold(
            l_bracket(
                LengthConvention.INSIDE_MOULD_LINE, base_length=38.0, flange_length=28.0
            )
        )
        # Tangent to tangent: the legs themselves.
        tangent = unfold(
            l_bracket(LengthConvention.TANGENT, base_length=35.0, flange_length=25.0)
        )
        assert outside.flat_length_mm == pytest.approx(66.094689747964199, abs=1e-12)
        assert inside.flat_length_mm == pytest.approx(outside.flat_length_mm, abs=1e-12)
        assert tangent.flat_length_mm == pytest.approx(outside.flat_length_mm, abs=1e-12)

    def test_and_place_every_face_identically(self) -> None:
        outside = unfold(l_bracket(LengthConvention.OUTSIDE_MOULD_LINE))
        tangent = unfold(
            l_bracket(LengthConvention.TANGENT, base_length=35.0, flange_length=25.0)
        )
        for name in ("base", "flange"):
            a, b = outside.face_named(name), tangent.face_named(name)
            assert a.origin_mm == pytest.approx(b.origin_mm, abs=1e-12)
            assert a.tangent_length_mm == pytest.approx(b.tangent_length_mm, abs=1e-12)


class TestBlankGeometry:
    def test_a_chain_traces_to_a_single_rectangle(self) -> None:
        pattern = unfold(l_bracket())
        assert len(pattern.outline) == 1
        loop = pattern.outline[0]
        assert loop[0] == loop[-1]
        assert len(loop) == 5  # four corners, closed
        xs = sorted({round(p[0], 9) for p in loop})
        ys = sorted({round(p[1], 9) for p in loop})
        assert xs == [0.0, round(66.094689747964199, 9)]
        assert ys == [0.0, 100.0]

    def test_the_bend_line_sits_between_the_two_tangent_lines(self) -> None:
        pattern = unfold(l_bracket())
        (line,) = pattern.bend_lines
        assert line.between == ("base", "flange")
        assert line.allowance_mm == pytest.approx(BA, abs=1e-12)
        assert line.direction is BendDirection.UP
        # The base's flat portion ends at 35; the flange starts at 35 + BA.
        assert line.start[0] == pytest.approx(35.0 + BA / 2.0, abs=1e-12)
        assert line.end[0] == pytest.approx(35.0 + BA / 2.0, abs=1e-12)
        assert {line.start[1], line.end[1]} == {0.0, 100.0}

    def test_the_blank_area_is_the_sum_of_its_regions(self) -> None:
        pattern = unfold(l_bracket())
        expected = 100.0 * (35.0 + BA + 25.0)
        assert pattern.blank_area_mm2 == pytest.approx(expected, abs=1e-9)

    def test_regions_cover_each_face_and_each_bend(self) -> None:
        pattern = unfold(l_bracket())
        assert len(pattern.regions) == 3  # two faces, one bend zone
        assert len(pattern.faces) == 2


class TestHoles:
    def test_a_hole_on_the_root_lands_where_it_was_declared(self) -> None:
        flange = Flange(name="flange", length_mm=30.0)
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            holes=(Hole(name="h1", diameter_mm=6.0, u_mm=10.0, v_mm=50.0),),
            joints=(Joint(edge=Edge.FAR, bend=right_angle(), flange=flange),),
        )
        part = SheetMetalPart(
            name="drilled L",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        (hole,) = unfold(part).holes
        # The root's NEAR edge is free, so its mould line and tangent line coincide.
        assert hole.centre_mm == pytest.approx((10.0, 50.0), abs=1e-12)

    def test_a_hole_on_a_bent_flange_moves_by_that_flange_s_setback(self) -> None:
        flange = Flange(
            name="flange",
            length_mm=30.0,
            holes=(Hole(name="h2", diameter_mm=6.0, u_mm=10.0, v_mm=50.0),),
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle(), flange=flange),),
        )
        part = SheetMetalPart(
            name="drilled L",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        (hole,) = unfold(part).holes
        # 10 mm from the mould line is 5 mm from the tangent line, and the
        # flange's tangent line is at 35 + BA on the blank.
        assert hole.centre_mm[0] == pytest.approx(35.0 + BA + 5.0, abs=1e-12)
        assert hole.centre_mm[1] == pytest.approx(50.0, abs=1e-12)

    def test_a_hole_off_its_face_is_refused(self) -> None:
        flange = Flange(
            name="flange",
            length_mm=30.0,
            holes=(Hole(name="h2", diameter_mm=6.0, u_mm=45.0, v_mm=50.0),),
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle(), flange=flange),),
        )
        part = SheetMetalPart(
            name="drilled L",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="off the flat part"):
            unfold(part)

    def test_a_hole_with_no_diameter_is_refused(self) -> None:
        with pytest.raises(UnfoldError, match="not a hole"):
            Hole(name="h", diameter_mm=0.0, u_mm=1.0, v_mm=1.0)


def enclosure() -> SheetMetalPart:
    """A 200 x 150 base with four 40 mm walls — M3's shape, and a tree not a chain."""
    walls = tuple(
        Joint(
            edge=edge,
            bend=right_angle(f"wall {edge}"),
            flange=Flange(name=f"wall {edge}", length_mm=40.0),
        )
        for edge in (Edge.FAR, Edge.NEAR, Edge.LEFT, Edge.RIGHT)
    )
    base = Flange(name="base", length_mm=200.0, width_mm=150.0, joints=walls)
    return SheetMetalPart(
        name="enclosure",
        material=steel(),
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=base,
    )


class TestATreeUnfolds:
    def test_the_base_loses_a_setback_at_each_of_its_four_edges(self) -> None:
        pattern = unfold(enclosure())
        base = pattern.face_named("base")
        assert base.tangent_length_mm == pytest.approx(200.0 - 2.0 * SB, abs=1e-12)
        assert base.tangent_width_mm == pytest.approx(150.0 - 2.0 * SB, abs=1e-12)

    def test_a_wall_with_no_declared_width_spans_the_edge_it_is_bent_from(self) -> None:
        pattern = unfold(enclosure())
        assert pattern.face_named("wall far").tangent_width_mm == pytest.approx(140.0)
        assert pattern.face_named("wall left").tangent_width_mm == pytest.approx(190.0)

    def test_the_blank_is_the_base_plus_two_walls_and_two_allowances_each_way(self) -> None:
        pattern = unfold(enclosure())
        width, height = pattern.blank_size_mm
        assert width == pytest.approx(190.0 + 2.0 * (BA + 35.0), abs=1e-12)
        assert height == pytest.approx(140.0 + 2.0 * (BA + 35.0), abs=1e-12)
        assert width == pytest.approx(272.1893794959284, abs=1e-9)
        assert height == pytest.approx(222.1893794959284, abs=1e-9)

    def test_it_traces_to_a_cross_with_twelve_corners(self) -> None:
        pattern = unfold(enclosure())
        assert len(pattern.outline) == 1
        loop = pattern.outline[0]
        assert loop[0] == loop[-1]
        assert len(loop) == 13

    def test_a_branching_part_reports_no_single_flat_length_and_says_why(self) -> None:
        pattern = unfold(enclosure())
        assert pattern.flat_length_mm is None
        assert any("branches" in caveat for caveat in pattern.caveats)

    def test_four_bend_lines_one_per_wall(self) -> None:
        pattern = unfold(enclosure())
        assert len(pattern.bend_lines) == 4
        assert {line.between[1] for line in pattern.bend_lines} == {
            "wall far",
            "wall near",
            "wall left",
            "wall right",
        }

    def test_it_is_deterministic(self) -> None:
        first = unfold(enclosure()).to_dict()
        second = unfold(enclosure()).to_dict()
        assert first == second


class TestRefusals:
    """Guards, each verified by breaking the thing it guards."""

    def test_a_flange_shorter_than_its_own_setbacks_has_no_flat_portion(self) -> None:
        far = Flange(name="far", length_mm=30.0)
        # 8 mm between two 90 degree bends, each of which takes 5 mm.
        middle = Flange(
            name="middle",
            length_mm=8.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b2"), flange=far),),
        )
        root = Flange(
            name="root",
            length_mm=30.0,
            width_mm=80.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b1"), flange=middle),),
        )
        part = SheetMetalPart(
            name="pinched",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=root,
        )
        with pytest.raises(UnfoldError, match="run into each other"):
            unfold(part)

    def test_a_flange_wider_than_the_edge_it_is_bent_from_is_refused(self) -> None:
        wall = Flange(name="wall", length_mm=40.0, width_mm=150.0)
        base = Flange(
            name="base",
            length_mm=200.0,
            width_mm=150.0,
            joints=(
                Joint(edge=Edge.FAR, bend=right_angle("far"), flange=wall),
                Joint(
                    edge=Edge.LEFT,
                    bend=right_angle("left"),
                    flange=Flange(name="side", length_mm=40.0),
                ),
                Joint(
                    edge=Edge.RIGHT,
                    bend=right_angle("right"),
                    flange=Flange(name="other side", length_mm=40.0),
                ),
            ),
        )
        part = SheetMetalPart(
            name="box with no corner relief",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="already in another bend"):
            unfold(part)

    def test_a_negative_offset_is_refused(self) -> None:
        wall = Flange(name="wall", length_mm=40.0, width_mm=50.0)
        base = Flange(
            name="base",
            length_mm=200.0,
            width_mm=150.0,
            joints=(
                Joint(edge=Edge.FAR, bend=right_angle(), flange=wall, offset_mm=-10.0),
            ),
        )
        part = SheetMetalPart(
            name="offset off the edge",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="off the start of that edge"):
            unfold(part)

    def test_two_flanges_on_one_edge_are_refused_by_name(self) -> None:
        with pytest.raises(UnfoldError, match="two bends on its far edge"):
            Flange(
                name="base",
                length_mm=200.0,
                width_mm=150.0,
                joints=(
                    Joint(
                        edge=Edge.FAR,
                        bend=right_angle("a"),
                        flange=Flange(name="tab a", length_mm=20.0, width_mm=30.0),
                    ),
                    Joint(
                        edge=Edge.FAR,
                        bend=right_angle("b"),
                        flange=Flange(name="tab b", length_mm=20.0, width_mm=30.0),
                        offset_mm=60.0,
                    ),
                ),
            )

    def test_a_child_cannot_bend_off_the_edge_it_arrived_on(self) -> None:
        grandchild = Flange(name="grandchild", length_mm=20.0)
        child = Flange(
            name="child",
            length_mm=30.0,
            joints=(Joint(edge=Edge.NEAR, bend=right_angle("b2"), flange=grandchild),),
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b1"), flange=child),),
        )
        with pytest.raises(UnfoldError, match="already the bend back to"):
            SheetMetalPart(
                name="doubled back",
                material=steel(),
                convention=LengthConvention.OUTSIDE_MOULD_LINE,
                root=base,
            )

    def test_two_flanges_with_one_name_are_refused(self) -> None:
        child = Flange(name="base", length_mm=30.0)
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle(), flange=child),),
        )
        with pytest.raises(UnfoldError, match="are called"):
            SheetMetalPart(
                name="ambiguous",
                material=steel(),
                convention=LengthConvention.OUTSIDE_MOULD_LINE,
                root=base,
            )

    def test_a_root_with_no_width_is_refused(self) -> None:
        part = SheetMetalPart(
            name="widthless",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=Flange(name="base", length_mm=40.0),
        )
        with pytest.raises(UnfoldError, match="has no width"):
            unfold(part)

    def test_a_hem_dimensioned_to_a_mould_line_is_refused_by_the_bend_arithmetic(
        self,
    ) -> None:
        hem = Bend(
            angle_deg=180.0,
            inside_radius_mm=1.0,
            direction=BendDirection.UP,
            k=K44,
            name="hem",
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(
                Joint(
                    edge=Edge.FAR, bend=hem, flange=Flange(name="return", length_mm=10.0)
                ),
            ),
        )
        part = SheetMetalPart(
            name="hemmed",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(BendError, match="never meet"):
            unfold(part)

    def test_the_same_hem_unfolds_tangent_to_tangent(self) -> None:
        hem = Bend(
            angle_deg=180.0,
            inside_radius_mm=1.0,
            direction=BendDirection.UP,
            k=K44,
            name="hem",
        )
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(
                Joint(
                    edge=Edge.FAR, bend=hem, flange=Flange(name="return", length_mm=10.0)
                ),
            ),
        )
        part = SheetMetalPart(
            name="hemmed",
            material=steel(),
            convention=LengthConvention.TANGENT,
            root=base,
        )
        allowance = math.pi * (1.0 + 0.44 * THICKNESS)
        assert unfold(part).flat_length_mm == pytest.approx(50.0 + allowance, abs=1e-12)

    def test_a_flat_pattern_that_overlaps_itself_is_refused(self) -> None:
        """A spiral: each flange turns the same way and the fourth lands on the base."""
        d = Flange(name="d", length_mm=150.0)
        c = Flange(
            name="c",
            length_mm=150.0,
            joints=(Joint(edge=Edge.LEFT, bend=right_angle("b4"), flange=d),),
        )
        b = Flange(
            name="b",
            length_mm=150.0,
            joints=(Joint(edge=Edge.LEFT, bend=right_angle("b3"), flange=c),),
        )
        a = Flange(
            name="a",
            length_mm=100.0,
            joints=(Joint(edge=Edge.LEFT, bend=right_angle("b2"), flange=b),),
        )
        base = Flange(
            name="base",
            length_mm=100.0,
            width_mm=100.0,
            joints=(Joint(edge=Edge.FAR, bend=right_angle("b1"), flange=a),),
        )
        part = SheetMetalPart(
            name="spiral",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="overlaps itself"):
            unfold(part)


class TestTheFlatLengthCrossCheck:
    """The one guard here that cannot be broken with data, only with a defect.

    `unfold` computes a chain's flat length twice — once from the deductions and
    once from the allowances — and refuses a disagreement, because the two are
    the same statement. Nothing a caller can declare makes them differ, so the
    guard is verified by making the arithmetic wrong on purpose.
    """

    def test_a_wrong_deduction_is_caught_rather_than_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = Bend.deduction_mm

        def wrong(
            self: Bend, thickness_mm: float, convention: LengthConvention
        ) -> float:
            return original(self, thickness_mm, convention) + 0.5

        monkeypatch.setattr(Bend, "deduction_mm", wrong)
        with pytest.raises(UnfoldError, match="same statement"):
            unfold(l_bracket())

    def test_and_the_unpatched_part_is_fine(self) -> None:
        assert unfold(l_bracket()).flat_length_mm is not None


class TestProvenanceReachesTheResult:
    def test_an_assumed_k_makes_the_pattern_provisional(self) -> None:
        pattern = unfold(l_bracket())
        assert pattern.provisional
        assert pattern.assumed_k_factors == ("b1",)
        assert "PROVISIONAL" in pattern.explain()
        assert pattern.to_dict()["provisional"] is True

    def test_a_sourced_k_does_not(self) -> None:
        flange = Flange(name="flange", length_mm=30.0)
        base = Flange(
            name="base",
            length_mm=40.0,
            width_mm=100.0,
            joints=(
                Joint(
                    edge=Edge.FAR,
                    bend=right_angle(
                        "b1", k=din6935(inside_radius_mm=RADIUS, thickness_mm=THICKNESS)
                    ),
                    flange=flange,
                ),
            ),
        )
        part = SheetMetalPart(
            name="sourced L",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        pattern = unfold(part)
        assert not pattern.provisional
        assert pattern.assumed_k_factors == ()
        assert "PROVISIONAL" not in pattern.explain()

    def test_the_k_travels_into_the_serialised_bend_line(self) -> None:
        payload = unfold(l_bracket()).to_dict()
        (line,) = payload["bend_lines"]
        assert line["k"]["value"] == pytest.approx(0.44)
        assert line["k"]["has_stated_basis"] is False

    def test_explain_names_the_convention_and_the_material(self) -> None:
        text = unfold(l_bracket()).explain()
        assert "outside mould line" in text
        assert "steel_mild_cr" in text
        assert "66.095" in text
