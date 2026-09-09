"""A folded sheet-metal part as an OCCT solid — phase 17.3 task 3, the geometry.

`app/kernel/occt/sheetmetal.py` builds what `app/sheetmetal/fold.py` places. The
arithmetic is checked without a kernel in `tests/test_sheetmetal_fold.py`; what is
checked here is that OCCT builds *that* shape and not a plausible neighbour of it.

Three things this file exists to catch, each of which produces a part that looks
right in a picture:

* **A measured volume that is not the closed-form one.** Every case compares
  `volume_mm3` against `fold.folded_volume_mm3`, which is arithmetic, never a
  recorded number — the rule the solver suite keeps.
* **A part in more than one piece.** Faces and bends are fused; a compound of
  touching solids measures exactly the same volume and is not a part.
* **A bend swept the wrong way.** The sector turns about `-v` for an up bend while
  its material runs along `+v`, so sweeping along the rotation axis mirrors the
  part about its own root — same volume, same face count, wrong machine.

They **skip** where OCCT is absent, for `test_kernel.py`'s reason: the rest of the
suite runs on a machine without a 700 MB dependency.
"""

import math

import pytest

from app.kernel import available
from app.kernel.occt.metrology import bounding_box_mm, volume_mm3
from app.kernel.occt.topology import connected_pieces, count
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
    sheet_material,
    unfold,
)
from app.sheetmetal.errors import UnfoldError
from app.sheetmetal.fold import blank_volume_difference_mm3, fold_layout, folded_volume_mm3

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

#: The volume comparison is between two exact statements about the same solid, so
#: the only difference either side may carry is OCCT's own integration error.
VOLUME_TOL = 1e-9

#: Where a bounding-box face may sit against the dimension it is meant to be.
POSITION_TOL = 1e-6


def steel(thickness_mm: float = 2.0):
    return sheet_material("steel_mild_cr", thickness_mm=thickness_mm)


def bend_at(
    *,
    angle_deg: float = 90.0,
    radius_mm: float = 3.0,
    thickness_mm: float = 2.0,
    direction: BendDirection = BendDirection.UP,
    name: str = "corner",
) -> Bend:
    return Bend(
        angle_deg=angle_deg,
        inside_radius_mm=radius_mm,
        direction=direction,
        k=machinerys_handbook(inside_radius_mm=radius_mm, thickness_mm=thickness_mm),
        name=name,
    )


def bracket(
    *,
    direction: BendDirection = BendDirection.UP,
    angle_deg: float = 90.0,
    thickness_mm: float = 2.0,
) -> SheetMetalPart:
    leg = Flange(name="leg", length_mm=30.0)
    base = Flange(
        name="base",
        length_mm=60.0,
        width_mm=40.0,
        joints=(
            Joint(
                edge=Edge.FAR,
                bend=bend_at(
                    angle_deg=angle_deg, direction=direction, thickness_mm=thickness_mm
                ),
                flange=leg,
            ),
        ),
    )
    return SheetMetalPart(
        name="L-bracket",
        material=steel(thickness_mm),
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=base,
    )


def channel() -> SheetMetalPart:
    """A chain: base, leg, and a return flange bent off the leg."""
    lip = Flange(name="lip", length_mm=15.0)
    leg = Flange(
        name="leg",
        length_mm=40.0,
        joints=(Joint(edge=Edge.FAR, bend=bend_at(name="second"), flange=lip),),
    )
    base = Flange(
        name="base",
        length_mm=80.0,
        width_mm=50.0,
        joints=(Joint(edge=Edge.FAR, bend=bend_at(name="first"), flange=leg),),
    )
    return SheetMetalPart(
        name="channel",
        material=steel(),
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=base,
    )


def enclosure(thickness_mm: float = 1.5) -> SheetMetalPart:
    """M3's shape: a base with a wall bent up off all four edges."""
    joints = tuple(
        Joint(
            edge=edge,
            bend=bend_at(
                radius_mm=2.0, thickness_mm=thickness_mm, name=f"wall {edge}"
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


def build(part: SheetMetalPart):
    from app.kernel.occt.sheetmetal import fold

    return fold(part)


class TestTheSolidWeighsWhatTheArithmeticSaysItWeighs:
    """Closed form against measurement, the way the solver suite is verified."""

    @pytest.mark.parametrize(
        "part",
        [
            bracket(),
            bracket(direction=BendDirection.DOWN),
            bracket(angle_deg=135.0),
            channel(),
            enclosure(),
        ],
        ids=["up", "down", "obtuse", "chain", "enclosure"],
    )
    def test_the_measured_volume_is_the_closed_form_volume(self, part) -> None:
        measured = volume_mm3(build(part))
        assert measured == pytest.approx(folded_volume_mm3(part), rel=VOLUME_TOL)

    def test_a_hem_folded_flat_back_on_itself_builds(self) -> None:
        """180 degrees is the arc `GC_MakeArcOfCircle`'s two-point form gets wrong.

        It has no mould line either, so the flanges are dimensioned tangent to
        tangent — `bend.py` refuses the setback and says exactly this.
        """
        hem = Flange(name="hem", length_mm=12.0)
        base = Flange(
            name="base",
            length_mm=50.0,
            width_mm=30.0,
            joints=(
                Joint(edge=Edge.FAR, bend=bend_at(angle_deg=180.0, radius_mm=1.0), flange=hem),
            ),
        )
        part = SheetMetalPart(
            name="hemmed panel",
            material=steel(),
            convention=LengthConvention.TANGENT,
            root=base,
        )
        assert volume_mm3(build(part)) == pytest.approx(
            folded_volume_mm3(part), rel=VOLUME_TOL
        )

    def test_the_blank_and_the_solid_differ_by_exactly_what_k_predicts(self) -> None:
        """The measured form of `flat.volume_mismatch_mm3`, against closed form."""
        part = enclosure()
        solid = volume_mm3(build(part))
        blank = unfold(part).blank_area_mm2 * part.material.thickness_mm
        assert solid - blank == pytest.approx(
            blank_volume_difference_mm3(part), rel=1e-8
        )


class TestADeclaredHoleIsCutInTheSolid:
    def test_the_solid_loses_exactly_the_cylinder(self) -> None:
        base = Flange(
            name="base",
            length_mm=60.0,
            width_mm=40.0,
            holes=(Hole(name="M8 clearance", diameter_mm=8.0, u_mm=20.0, v_mm=20.0),),
            joints=(Joint(edge=Edge.FAR, bend=bend_at(), flange=Flange(name="leg", length_mm=30.0)),),
        )
        part = SheetMetalPart(
            name="drilled bracket",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        solid = build(part)
        assert volume_mm3(solid) == pytest.approx(folded_volume_mm3(part), rel=VOLUME_TOL)
        assert volume_mm3(solid) == pytest.approx(
            volume_mm3(build(bracket())) - math.pi * 4.0**2 * 2.0, rel=1e-9
        )
        assert count(solid, "SOLID") == 1


class TestThePartIsOnePart:
    def test_the_faces_and_bends_are_fused_not_merely_touching(self) -> None:
        shape = build(enclosure())
        assert count(shape, "SOLID") == 1
        assert connected_pieces(shape) == 1

    def test_a_chain_of_two_bends_is_still_one_solid(self) -> None:
        assert count(build(channel()), "SOLID") == 1


class TestTheOutsideDimensionsAreTheDeclaredOnes:
    """An outside mould-line part fits the box it was dimensioned in."""

    def test_the_bracket_fills_its_declared_envelope(self) -> None:
        box = bounding_box_mm(build(bracket()))
        assert box["size"] == pytest.approx([60.0, 40.0, 30.0], abs=POSITION_TOL)

    def test_the_enclosure_fills_its_declared_envelope(self) -> None:
        box = bounding_box_mm(build(enclosure()))
        assert box["size"] == pytest.approx([200.0, 150.0, 25.0], abs=POSITION_TOL)

    def test_a_down_bend_puts_the_material_below_the_base(self) -> None:
        """Same size box, opposite side of z — a volume check cannot see this."""
        up = bounding_box_mm(build(bracket()))
        down = bounding_box_mm(build(bracket(direction=BendDirection.DOWN)))
        assert up["size"] == pytest.approx(down["size"], abs=POSITION_TOL)
        assert up["max"][2] == pytest.approx(30.0, abs=POSITION_TOL)
        assert down["min"][2] == pytest.approx(-28.0, abs=POSITION_TOL)


class TestTheBendRunsAlongThePartAndNotBackwardsFromIt:
    """The `width_dir` guard, in geometry rather than in vectors.

    Sweeping the sector along the rotation axis instead of the bend direction
    builds a part of exactly the same volume with the bend hanging off the far
    side of the root — measured here as a bounding box twice as wide as the sheet.
    """

    def test_the_solid_is_no_wider_than_the_sheet_it_is_folded_from(self) -> None:
        """A 40 mm wide bracket occupies 40 mm across, not 80.

        Sweeping the sector the wrong way doubles the extent across the bend and
        leaves everything else — volume, solid count, the length and height of the
        part — exactly as it should be.
        """
        for part in (bracket(), bracket(direction=BendDirection.DOWN), channel()):
            width = part.root.width_mm
            assert width is not None
            box = bounding_box_mm(build(part))
            assert box["size"][1] == pytest.approx(width, abs=POSITION_TOL)
            assert box["min"][1] == pytest.approx(0.0, abs=POSITION_TOL)

    def test_the_bracket_bend_lies_between_the_two_faces_it_joins(self) -> None:
        box = bounding_box_mm(build(bracket()))
        assert box["min"] == pytest.approx([0.0, 0.0, 0.0], abs=POSITION_TOL)


class TestWhatTheBuilderRefuses:
    def test_a_part_whose_bends_eat_a_leg_never_reaches_the_kernel(self) -> None:
        short = Flange(name="leg", length_mm=1.0)
        base = Flange(
            name="base",
            length_mm=6.0,
            width_mm=20.0,
            joints=(Joint(edge=Edge.FAR, bend=bend_at(), flange=short),),
        )
        part = SheetMetalPart(
            name="impossible",
            material=steel(),
            convention=LengthConvention.OUTSIDE_MOULD_LINE,
            root=base,
        )
        with pytest.raises(UnfoldError, match="no flat portion left"):
            build(part)


class TestTheSolidIsWhereTheLayoutSaysItIs:
    def test_every_face_frame_lands_on_the_solid(self) -> None:
        """A frame origin off the material means the layout and the build disagree."""
        layout = fold_layout(channel())
        box = bounding_box_mm(build(channel()))
        for face in layout.faces:
            for corner in (
                face.at(0.0, 0.0),
                face.at(face.length_mm, face.width_mm),
                face.at(face.length_mm / 2.0, face.width_mm / 2.0, face.thickness_mm),
            ):
                for axis, value in enumerate(corner):
                    assert box["min"][axis] - POSITION_TOL <= value
                    assert value <= box["max"][axis] + POSITION_TOL

    def test_an_obtuse_bend_turns_through_the_angle_it_declares(self) -> None:
        """135 degrees of turn leaves the legs at 45 — the convention `bend.py` fixes."""
        layout = fold_layout(bracket(angle_deg=135.0))
        leg = layout.face_named("leg")
        base = layout.face_named("base")
        cosine = sum(a * b for a, b in zip(leg.u_dir, base.u_dir, strict=True))
        assert math.degrees(math.acos(cosine)) == pytest.approx(135.0)
