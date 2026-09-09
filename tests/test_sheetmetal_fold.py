"""Placing a folded sheet-metal part in space — phase 17.3 task 3, the arithmetic.

`app/sheetmetal/fold.py` says where every face and every bend of a part sits. This
file checks that against arithmetic done by hand, never against recorded output,
exactly as `test_sheetmetal_bend.py` does for the allowance. It needs no kernel and
no database, so it runs in milliseconds — the property the whole package keeps.

The claims that matter, and why each is here:

* **A folded part and its blank are the same material.** The two layouts are
  computed from one `tangent_extents` walk, so the difference between the folded
  volume and `blank area * t` must be exactly `sum(theta * t^2 * w * (0.5 - K))` —
  and zero when K is 0.5. That identity is the only thing standing between "the
  drawing makes the part" and "somebody typed the same numbers twice".
* **An outside mould-line dimension is an outside dimension.** A 60 mm leg bent up
  off a 60 mm base reaches z = 60 mm, whatever the radius. Getting the bend centre
  on the wrong side of the sheet leaves that wrong by 2t and nothing else notices.
* **Up and down are mirror images and not the same part.** They have equal volume,
  so a volume check alone would pass a part folded the wrong way.
"""

import math

import pytest

from app.sheetmetal import (
    Bend,
    BendDirection,
    Edge,
    Flange,
    Hole,
    Joint,
    LengthConvention,
    SheetMetalPart,
    machinerys_handbook,
    measured,
    sheet_material,
    unfold,
)
from app.sheetmetal.errors import UnfoldError
from app.sheetmetal.fold import (
    blank_volume_difference_mm3,
    fold_layout,
    folded_volume_mm3,
)
from app.solve.materials import Source, SourceKind

TOL = 1e-9

#: A K of exactly one half, declared as a measurement so it carries a source. It
#: is the value at which the neutral axis is the mid-plane and the blank accounts
#: for the folded material exactly — the case that turns a tolerance into an
#: identity.
MID_PLANE_SOURCE = Source(
    citation="K = 0.5 is the mid-plane by definition, not a measurement of a grade",
    kind=SourceKind.DERIVED,
    note="Used here only to turn a tolerance into an identity; no part is designed on it.",
)


def steel(thickness_mm: float = 2.0):
    return sheet_material("steel_mild_cr", thickness_mm=thickness_mm)


def right_angle(*, radius_mm: float = 3.0, thickness_mm: float = 2.0, k=None, direction=None):
    return Bend(
        angle_deg=90.0,
        inside_radius_mm=radius_mm,
        direction=direction or BendDirection.UP,
        k=k or machinerys_handbook(inside_radius_mm=radius_mm, thickness_mm=thickness_mm),
        name="corner",
    )


def bracket(
    *,
    thickness_mm: float = 2.0,
    direction: BendDirection = BendDirection.UP,
    convention: LengthConvention = LengthConvention.OUTSIDE_MOULD_LINE,
    k=None,
    base_length_mm: float = 60.0,
    leg_length_mm: float = 30.0,
) -> SheetMetalPart:
    """One base, one leg bent off its far edge. The simplest part with a bend."""
    bend = right_angle(thickness_mm=thickness_mm, direction=direction, k=k)
    leg = Flange(name="leg", length_mm=leg_length_mm)
    base = Flange(
        name="base",
        length_mm=base_length_mm,
        width_mm=40.0,
        joints=(Joint(edge=Edge.FAR, bend=bend, flange=leg),),
    )
    return SheetMetalPart(
        name="L-bracket",
        material=steel(thickness_mm),
        convention=convention,
        root=base,
    )


def enclosure(thickness_mm: float = 1.5) -> SheetMetalPart:
    """M3's shape: a base with a wall bent up off all four edges."""
    joints = tuple(
        Joint(
            edge=edge,
            bend=Bend(
                angle_deg=90.0,
                inside_radius_mm=2.0,
                direction=BendDirection.UP,
                k=machinerys_handbook(inside_radius_mm=2.0, thickness_mm=thickness_mm),
                name=f"wall {edge}",
            ),
            flange=Flange(name=f"side-{edge}", length_mm=25.0),
        )
        for edge in (Edge.FAR, Edge.NEAR, Edge.LEFT, Edge.RIGHT)
    )
    return SheetMetalPart(
        name="enclosure",
        material=steel(thickness_mm),
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=Flange(name="base", length_mm=200.0, width_mm=150.0, joints=joints),
    )


class TestTheRootLiesWhereThePartCoordinatesSayItDoes:
    def test_the_root_face_starts_at_the_origin_along_x(self) -> None:
        layout = fold_layout(bracket())
        root = layout.faces[0]
        assert root.name == "base"
        assert root.origin_mm == (0.0, 0.0, 0.0)
        assert root.u_dir == (1.0, 0.0, 0.0)
        assert root.v_dir == (0.0, 1.0, 0.0)
        assert root.normal == pytest.approx((0.0, 0.0, 1.0))

    def test_the_root_face_is_the_tangent_rectangle_not_the_declared_one(self) -> None:
        """A 60 mm base with one 90 degree bend on it is 60 - SB long, flat."""
        part = bracket()
        setback = part.root.joints[0].bend.setback_mm(2.0, part.convention)
        assert setback == pytest.approx(5.0)  # tan(45) * (3 + 2)
        assert fold_layout(part).face_named("base").length_mm == pytest.approx(55.0)


class TestAnOutsideMouldLineDimensionIsAnOutsideDimension:
    """The check that catches a bend centre on the wrong side of the sheet."""

    def test_a_leg_bent_up_reaches_its_declared_length_in_z(self) -> None:
        layout = fold_layout(bracket(leg_length_mm=30.0))
        leg = layout.face_named("leg")
        # The far end of the leg's outer surface: u = L, n = 0 is the outer face
        # of an up bend, and the mould line is where the two outside faces meet.
        tip = leg.at(leg.length_mm, 0.0, 0.0)
        assert tip[2] == pytest.approx(30.0)

    def test_the_leg_stands_perpendicular_to_the_base(self) -> None:
        layout = fold_layout(bracket())
        assert layout.face_named("leg").u_dir == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)

    def test_a_tighter_radius_does_not_move_the_mould_line(self) -> None:
        """r changes the arc, the setback and the blank — never the outside box."""
        for radius in (1.0, 3.0, 8.0):
            part = bracket(k=machinerys_handbook(inside_radius_mm=radius, thickness_mm=2.0))
            bend = Bend(
                angle_deg=90.0,
                inside_radius_mm=radius,
                direction=BendDirection.UP,
                k=machinerys_handbook(inside_radius_mm=radius, thickness_mm=2.0),
            )
            leg = Flange(name="leg", length_mm=30.0)
            base = Flange(
                name="base",
                length_mm=60.0,
                width_mm=40.0,
                joints=(Joint(edge=Edge.FAR, bend=bend, flange=leg),),
            )
            part = SheetMetalPart(
                name="L",
                material=steel(),
                convention=LengthConvention.OUTSIDE_MOULD_LINE,
                root=base,
            )
            leg_face = fold_layout(part).face_named("leg")
            assert leg_face.at(leg_face.length_mm, 0.0, 0.0)[2] == pytest.approx(30.0)


class TestUpAndDownAreDifferentParts:
    def test_the_two_directions_have_the_same_volume(self) -> None:
        """Which is why a volume check alone cannot tell them apart."""
        assert folded_volume_mm3(bracket(direction=BendDirection.UP)) == pytest.approx(
            folded_volume_mm3(bracket(direction=BendDirection.DOWN))
        )

    def test_a_down_bend_puts_the_leg_below_the_base(self) -> None:
        up = fold_layout(bracket(direction=BendDirection.UP)).face_named("leg")
        down = fold_layout(bracket(direction=BendDirection.DOWN)).face_named("leg")
        assert up.at(up.length_mm, 0.0, 0.0)[2] == pytest.approx(30.0)
        assert down.at(down.length_mm, 0.0, 0.0)[2] == pytest.approx(-28.0)

    def test_the_bend_centre_sits_on_the_concave_side(self) -> None:
        """t + r above the frame plane bending up, r below it bending down."""
        up = fold_layout(bracket(direction=BendDirection.UP)).bends[0]
        down = fold_layout(bracket(direction=BendDirection.DOWN)).bends[0]
        assert up.centre_mm[2] == pytest.approx(2.0 + 3.0)
        assert down.centre_mm[2] == pytest.approx(-3.0)


class TestTheBlankAndTheFoldedPartAreTheSameMaterial:
    """The identity that makes `flat.volume_mismatch_mm3` evidence rather than noise."""

    def test_a_mid_plane_neutral_axis_conserves_the_volume_exactly(self) -> None:
        half = measured(0.5, source=MID_PLANE_SOURCE, note="the mid-plane, by definition")
        part = bracket(k=half)
        blank = unfold(part).blank_area_mm2 * part.material.thickness_mm
        assert folded_volume_mm3(part) == pytest.approx(blank, abs=1e-9)
        assert blank_volume_difference_mm3(part) == pytest.approx(0.0, abs=1e-12)

    def test_below_the_mid_plane_the_blank_is_short_by_a_known_amount(self) -> None:
        part = bracket()
        thickness = part.material.thickness_mm
        k = part.root.joints[0].bend.k.value
        expected = math.radians(90.0) * thickness**2 * 40.0 * (0.5 - k)
        assert k < 0.5
        blank = unfold(part).blank_area_mm2 * thickness
        assert folded_volume_mm3(part) - blank == pytest.approx(expected, rel=1e-12)
        assert blank_volume_difference_mm3(part) == pytest.approx(expected, rel=1e-12)

    def test_the_enclosure_agrees_the_same_way_over_four_bends(self) -> None:
        part = enclosure()
        blank = unfold(part).blank_area_mm2 * part.material.thickness_mm
        assert folded_volume_mm3(part) - blank == pytest.approx(
            blank_volume_difference_mm3(part), rel=1e-12
        )


class TestTheVolumeIsClosedForm:
    def test_a_plate_is_length_times_width_times_thickness(self) -> None:
        part = bracket()
        layout = fold_layout(part)
        base = layout.face_named("base")
        assert base.volume_mm3 == pytest.approx(55.0 * 40.0 * 2.0)

    def test_a_sector_is_theta_times_t_times_the_mean_radius_times_width(self) -> None:
        bend = fold_layout(bracket()).bends[0]
        assert bend.volume_mm3 == pytest.approx(
            math.radians(90.0) * 2.0 * (3.0 + 1.0) * 40.0
        )

    def test_the_layout_volume_is_the_sum_of_its_pieces(self) -> None:
        layout = fold_layout(enclosure())
        assert layout.volume_mm3 == pytest.approx(
            sum(f.volume_mm3 for f in layout.faces) + sum(b.volume_mm3 for b in layout.bends)
        )


class TestABendRunsAlongTheBendAndNotAlongItsRotationAxis:
    """The `width_dir` field. An up bend turns about -v and its material runs +v."""

    def test_the_width_direction_is_the_child_face_width_direction(self) -> None:
        for direction in (BendDirection.UP, BendDirection.DOWN):
            layout = fold_layout(bracket(direction=direction))
            bend = layout.bends[0]
            assert bend.width_dir == pytest.approx(layout.face_named("leg").v_dir)

    def test_an_up_bend_turns_about_the_axis_opposite_its_width(self) -> None:
        bend = fold_layout(bracket(direction=BendDirection.UP)).bends[0]
        assert bend.axis_dir == pytest.approx(tuple(-x for x in bend.width_dir))

    def test_a_down_bend_turns_about_its_width_direction(self) -> None:
        bend = fold_layout(bracket(direction=BendDirection.DOWN)).bends[0]
        assert bend.axis_dir == pytest.approx(bend.width_dir)


class TestTheBendMeetsBothFacesItJoins:
    """A sector that does not touch its own faces makes a part in two pieces."""

    def test_the_inside_surface_starts_on_the_parent_tangent_line(self) -> None:
        layout = fold_layout(bracket())
        bend = layout.bends[0]
        base = layout.face_named("base")
        start = tuple(
            bend.centre_mm[i] + bend.start_dir[i] * bend.inside_radius_mm for i in range(3)
        )
        # For an up bend the parent's inside surface is its n = t face.
        assert start == pytest.approx(base.at(base.length_mm, 0.0, base.thickness_mm))

    def test_every_face_after_a_bend_keeps_unit_axes(self) -> None:
        for part in (bracket(), enclosure(), bracket(direction=BendDirection.DOWN)):
            for face in fold_layout(part).faces:
                for axis in (face.u_dir, face.v_dir, face.normal):
                    assert math.hypot(*axis) == pytest.approx(1.0, abs=TOL)


class TestWhatAFoldedPartRefuses:
    def test_a_leg_its_own_bends_have_eaten_is_refused(self) -> None:
        """The same refusal the flat pattern gives, because it is the part's fault."""
        with pytest.raises(UnfoldError, match="no flat portion left"):
            fold_layout(bracket(leg_length_mm=1.0, base_length_mm=6.0))

    def test_a_flange_hanging_off_the_end_of_its_edge_is_refused(self) -> None:
        bend = right_angle()
        tab = Flange(name="tab", length_mm=10.0, width_mm=200.0)
        base = Flange(
            name="base",
            length_mm=60.0,
            width_mm=40.0,
            joints=(Joint(edge=Edge.FAR, bend=bend, flange=tab),),
        )
        part = SheetMetalPart(
            name="overhang",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="whose flat extent there is only"):
            fold_layout(part)

    def test_a_part_whose_blank_would_overlap_is_still_a_solid(self) -> None:
        """An over-refusal is not safe: this part folds, it just cannot be nested flat.

        Two long flanges bent the same way off opposite edges of a short base run
        into each other **on the blank**, which `unfold` refuses. The folded part
        is perfectly buildable, so `fold_layout` must not refuse it.
        """
        thickness = 1.0
        k = machinerys_handbook(inside_radius_mm=1.0, thickness_mm=thickness)
        wings = tuple(
            Joint(
                edge=edge,
                bend=Bend(
                    angle_deg=90.0,
                    inside_radius_mm=1.0,
                    direction=BendDirection.UP,
                    k=k,
                    name=f"wing {edge}",
                ),
                flange=Flange(name=f"wing-{edge}", length_mm=40.0),
            )
            for edge in (Edge.LEFT, Edge.RIGHT)
        )
        part = SheetMetalPart(
            name="channel",
            material=steel(thickness),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=Flange(name="web", length_mm=120.0, width_mm=30.0, joints=wings),
        )
        layout = fold_layout(part)
        assert len(layout.faces) == 3
        assert layout.volume_mm3 > 0.0


def drilled(*, u_mm: float = 20.0, diameter_mm: float = 8.0) -> SheetMetalPart:
    """The bracket with one hole through its base."""
    part = bracket()
    base = part.root
    holed = Flange(
        name=base.name,
        length_mm=base.length_mm,
        width_mm=base.width_mm,
        holes=(Hole(name="M8 clearance", diameter_mm=diameter_mm, u_mm=u_mm, v_mm=20.0),),
        joints=base.joints,
    )
    return SheetMetalPart(
        name=part.name, material=part.material, convention=part.convention, root=holed
    )


class TestAHoleIsMaterialTheFoldedPartDoesNotHave:
    """A folded solid that ignored a declared hole would weigh more than the part."""

    def test_the_hole_is_placed_from_the_tangent_lines_not_the_mould_lines(self) -> None:
        layout = fold_layout(drilled(u_mm=20.0))
        hole = layout.holes[0]
        base = layout.face_named("base")
        assert hole.face == "base"
        # u is declared from the NEAR edge, which on this part carries no bend, so
        # the tangent coordinate is the declared one; v likewise.
        assert hole.centre_mm == pytest.approx(base.at(20.0, 20.0))
        assert hole.axis_dir == pytest.approx(base.normal)

    def test_the_volume_drops_by_exactly_the_cylinder(self) -> None:
        solid = folded_volume_mm3(drilled())
        assert folded_volume_mm3(bracket()) - solid == pytest.approx(
            math.pi * 4.0**2 * 2.0
        )

    def test_a_hole_in_a_bend_zone_is_refused(self) -> None:
        """The same refusal the blank gives: a hole there deforms."""
        with pytest.raises(UnfoldError, match="off the flat part of that face"):
            fold_layout(drilled(u_mm=59.0))


class TestTheLayoutIsReportable:
    def test_the_payload_names_every_face_and_bend(self) -> None:
        payload = fold_layout(enclosure()).to_dict()
        assert payload["part"] == "enclosure"
        assert [face["name"] for face in payload["faces"]][0] == "base"
        assert len(payload["bends"]) == 4
        assert payload["holes"] == []
        assert payload["volume_mm3"] == pytest.approx(folded_volume_mm3(enclosure()))

    def test_an_unknown_face_is_a_key_error_not_a_none(self) -> None:
        with pytest.raises(KeyError):
            fold_layout(bracket()).face_named("no such flange")
