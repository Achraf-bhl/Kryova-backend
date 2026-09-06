"""The sheet itself: scale, size, title block, and where a feature actually lands.

Three properties, and the third is the one that is easy to get wrong twice.

**The scale is chosen from a series, never computed.** A drawing at 1:2.73 is a
drawing nobody can measure off, and a scale bar cannot be read to three figures.
`choose_scale` takes the largest ISO 5455 scale that still fits and refuses — with
what would have been needed — when even the smallest does not. A drawing at the
wrong scale with the right dimensions is a drawing people cut metal from.

**The title block says what it does not know.** Mass prints NOT MEASURED, material
NOT STATED, general tolerance NONE STATED. None of them falls back to a plausible
default: a general tolerance is a commercial commitment — it decides what the shop
is allowed to ship — and a drawing Kryova produced quietly carrying ISO 2768-m
would be Kryova signing for it.

**Determinism does not catch an upside-down part.** `app/render/` goes to real
lengths for byte-identical output, and this sheet inherits that: the same design
lays out to the same sheet, twice, down to the float. But a *consistently*
mirrored drawing is identical to itself, so determinism holds while every view is
wrong — which is exactly what happened in `app/render/project`, where OCCT's
`gp_Ax2` Y axis is the opposite of the declared up vector and the fix shipped a
day late because nothing could see it. So `TestWhereAKnownFeatureLands` asserts
*positions*, against a part built deliberately off centre: a bore at (20, 10) is
at (20, 10) and not at (20, −10), and a pad rising from z = 0 to z = 12 rises up
the front view rather than hanging below it.

The bracket fixture and its design live in `test_manufacture_dimensions.py`;
building it twice is the expensive part of this file, so it is imported rather
than repeated.
"""

from __future__ import annotations

import pytest

from app.manufacture.drawing import ViewKind
from app.manufacture.errors import DrawingError
from app.manufacture.layout import (
    DIMENSION_ALLOWANCE_MM,
    NOTES_WIDTH_MM,
    VIEW_GAP_MM,
    DetailRequest,
)
from app.manufacture.sheet import (
    PREFERRED_SCALES,
    SHEET_SIZES,
    TITLE_BLOCK_HEIGHT_MM,
    Projection,
    TitleBlock,
    choose_scale,
    drawing_area,
    scale_text,
    sheet_named,
    smallest_sheet_for,
)
from tests.test_manufacture_dimensions import Built, bracket, built

# -- scale -------------------------------------------------------------------


class TestTheScaleComesFromTheSeriesOrIsRefused:
    def test_the_answer_is_always_a_scale_that_can_be_written_down(self) -> None:
        for content in ((120.0, 80.0), (12.0, 8.0), (1200.0, 800.0), (3.0, 2.0)):
            assert choose_scale(content, (390.0, 221.0)) in PREFERRED_SCALES

    def test_it_is_the_largest_one_that_fits_and_not_merely_one_that_does(self) -> None:
        """A drawing shrunk further than it needed to be wastes the sheet and
        loses the small features; 390/120 and 221/80 both allow 2:1."""
        assert choose_scale((120.0, 80.0), (390.0, 221.0)) == 2.0

    def test_the_binding_direction_is_the_one_that_binds(self) -> None:
        """Height binds here even though width would allow much more."""
        assert choose_scale((10.0, 200.0), (390.0, 221.0)) == 1.0

    def test_a_flat_part_seen_edge_on_still_has_a_scale(self) -> None:
        """A zero extent in one direction never binds — the same rule
        `render.views.frame_for` applies, for the same reason."""
        assert choose_scale((120.0, 0.0), (390.0, 221.0)) == 2.0

    def test_a_part_with_no_extent_at_all_is_one_to_one(self) -> None:
        assert choose_scale((0.0, 0.0), (390.0, 221.0)) == 1.0

    def test_a_part_that_does_not_fit_is_refused_with_what_would(self) -> None:
        """Not silently drawn at the smallest scale in the list: that produces a
        sheet with the part running off the frame and every dimension on it still
        claiming to be right."""
        with pytest.raises(DrawingError) as refusal:
            choose_scale((5.0e7, 4.0e7), (390.0, 221.0))

        message = str(refusal.value)
        assert "larger sheet" in message
        assert "does not fit" in message

    def test_a_drawing_area_with_no_room_in_it_is_refused(self) -> None:
        with pytest.raises(DrawingError):
            choose_scale((10.0, 10.0), (0.0, 221.0))


class TestTheScaleOnTheSheetIsWrittenFromTheFactor:
    """Written from the number the geometry was drawn at, never carried beside
    it — the drift that makes every dimension on a drawing a lie at once."""

    @pytest.mark.parametrize(
        ("factor", "text"),
        [(1.0, "1:1"), (2.0, "2:1"), (10.0, "10:1"), (0.5, "1:2"), (0.1, "1:10")],
    )
    def test_it_reads_the_way_a_drawing_reads(self, factor: float, text: str) -> None:
        assert scale_text(factor) == text

    def test_a_scale_of_zero_is_not_a_scale(self) -> None:
        with pytest.raises(DrawingError):
            scale_text(0.0)

    def test_nor_is_a_negative_one(self) -> None:
        with pytest.raises(DrawingError):
            scale_text(-1.0)


class TestTheSheetIsTheSmallestThatReadsWell:
    def test_a_small_part_goes_on_a_small_sheet(self) -> None:
        assert smallest_sheet_for((120.0, 80.0)).name == "A4"

    def test_a_large_one_is_promoted(self) -> None:
        """An A0 carrying an A4's worth of part is a sheet nobody has a printer
        for; the reverse is a part nobody can see."""
        assert smallest_sheet_for((4000.0, 3000.0)).name in {"A2", "A1", "A0"}
        assert smallest_sheet_for((120.0, 80.0)) is SHEET_SIZES[0]

    def test_an_unknown_sheet_is_refused_with_the_list(self) -> None:
        with pytest.raises(DrawingError) as refusal:
            sheet_named("B2")

        assert "A4" in str(refusal.value) and "A0" in str(refusal.value)

    def test_a_sheet_name_is_read_forgivingly(self) -> None:
        assert sheet_named(" a3 ").name == "A3"

    def test_the_drawing_area_has_the_title_block_taken_out_of_it(self) -> None:
        sheet = sheet_named("A3")
        x0, y0, x1, y1 = sheet.frame

        width, height = drawing_area(sheet)

        assert width == pytest.approx(x1 - x0)
        assert height == pytest.approx((y1 - y0) - TITLE_BLOCK_HEIGHT_MM)

    def test_the_filing_margin_is_wider_than_the_others(self) -> None:
        """The strip a binder punches through. Drawing into it loses the
        drawing, and it is on the left of every ISO 5457 sheet."""
        for sheet in SHEET_SIZES:
            assert sheet.left_margin_mm >= sheet.margin_mm


# -- the title block ---------------------------------------------------------


class TestTheTitleBlockSaysWhatItDoesNotKnow:
    def test_an_unweighed_part_says_so_rather_than_printing_a_blank(self) -> None:
        """A blank mass field reads as "light enough not to matter"; a zero reads
        as a fact. Same rule `app.kernel.provenance` applies to every other
        number in this codebase."""
        assert TitleBlock("T", "1").mass_text() == "NOT MEASURED"

    def test_a_weighed_part_of_zero_is_a_fact_and_prints_as_one(self) -> None:
        assert TitleBlock("T", "1", mass_kg=0.0).mass_text() == "0.000 kg"

    def test_an_unstated_material_says_so(self) -> None:
        assert TitleBlock("T", "1").material_text() == "NOT STATED"

    def test_no_general_tolerance_is_ever_invented(self) -> None:
        """A general tolerance decides what the shop is allowed to ship. Falling
        back to ISO 2768-m would be Kryova signing a commercial commitment on the
        customer's behalf."""
        block = TitleBlock("T", "1")

        assert block.tolerance_text() == "NONE STATED"
        assert "2768" not in block.tolerance_text()

    def test_a_stated_one_is_printed_as_given(self) -> None:
        assert TitleBlock("T", "1", general_tolerance="ISO 2768-mK").tolerance_text() == (
            "ISO 2768-mK"
        )

    def test_the_convention_and_the_completeness_statement_both_reach_the_block(
        self,
    ) -> None:
        drawing = built().drawing()

        assert drawing.title_block.projection is Projection.FIRST_ANGLE
        assert drawing.title_block.notes == drawing.report.statement()


# -- determinism -------------------------------------------------------------


class TestTheSameDesignLaysOutTheSameSheet:
    def test_laying_the_same_part_out_twice_gives_the_same_sheet(self) -> None:
        fixture = built()

        first = fixture.drawing()
        second = fixture.drawing()

        assert first == second

    def test_and_so_does_building_the_part_again_from_the_spec(self) -> None:
        """The stronger claim, and the one a reviewer diffing two revisions
        depends on: the kernel run is in the loop, not just the layout."""
        again = Built(bracket())

        first = built().drawing()
        second = again.drawing()

        assert [one.origin_mm for one in first.views] == [
            one.origin_mm for one in second.views
        ]
        assert [one.text for one in first.dimensions] == [
            one.text for one in second.dimensions
        ]
        assert first.report.statement() == second.report.statement()

    def test_a_different_part_lays_out_differently(self) -> None:
        """Otherwise the test above passes on a function that returns a
        constant, which is a determinism check that checks nothing."""
        thicker = Built(bracket().set_parameter("thick_mm", 20.0))

        assert built().drawing() != thicker.drawing()


# -- orientation, which determinism cannot see -------------------------------


def _line_work(drawing, view_name: str) -> list[tuple[float, float]]:
    """Every projected point on one view, in view millimetres."""
    view = drawing.view_named(view_name)
    return [point for line in (*view.visible, *view.hidden) for point in line]


def _roundness(
    points: list[tuple[float, float]], centre: tuple[float, float], radius: float
) -> float:
    """How far the line work is from lying on a circle of `radius` about `centre`.

    Checked at twelve angles spread around the circle, each against the closest
    line-work point in the plane, and reported as the **worst** of the twelve —
    not the single nearest point overall. A minimum-over-all-points version of
    this passed on a coincidence: the bore here is radius 15 at (20, 10), and a
    wrong-mirror probe at (20, -10) is only 20 mm away, so the two circles of
    equal radius *intersect* — two circles of radius r with centres closer than
    2r always do — and a real vertex of the true bore near that intersection
    reads as "round" for the wrong centre too, with nothing actually circular
    there. Twelve points spread around the circle cannot all be satisfied by
    one or two coincidental intersections; only an actual circle does that.
    """
    import math

    worst = 0.0
    for index in range(12):
        theta = 2.0 * math.pi * index / 12
        target = (centre[0] + radius * math.cos(theta), centre[1] + radius * math.sin(theta))
        nearest = min(
            ((x - target[0]) ** 2 + (y - target[1]) ** 2) ** 0.5 for x, y in points
        )
        worst = max(worst, nearest)
    return worst


def _centre_marks(drawing, view_name: str) -> set[tuple[float, float]]:
    """Where the centre marks on one view actually cross, in view millimetres.

    Each mark is a pair of polylines through the centre, so the midpoint of
    either one is the feature's centre. Read back out of the drawing rather than
    recomputed, so this measures what the sheet says.
    """
    view = drawing.view_named(view_name)
    return {
        (
            round((line[0][0] + line[1][0]) / 2.0, 6),
            round((line[0][1] + line[1][1]) / 2.0, 6),
        )
        for line in view.centre_lines
    }


class TestWhereAKnownFeatureLands:
    """Determinism holds for a mirrored drawing too; only a position catches it."""

    def test_the_bore_is_where_the_design_put_it(self) -> None:
        """(20, 10) in the sketch, drilled through +Z, seen from the top: the
        top view's own axes are world X and world Y, so it lands at (20, 10)."""
        marks = _centre_marks(built().drawing(), "top")

        assert (20.0, 10.0) in marks

    def test_and_not_at_its_mirror_image(self) -> None:
        """The assertion above passes on a drawing flipped in x, in y, or in
        both — it would just find the bore somewhere else. These three are what
        make it a statement about handedness."""
        marks = _centre_marks(built().drawing(), "top")

        assert (20.0, -10.0) not in marks
        assert (-20.0, 10.0) not in marks
        assert (-20.0, -10.0) not in marks

    def test_the_centre_mark_lands_on_the_bore_the_line_work_drew(self) -> None:
        """The two paths from world coordinates to view millimetres have to agree.

        A drawing reaches this frame twice by different routes: the line work
        through HLR in `render.project`, which negates y to undo OCCT's `gp_Ax2`
        convention, and every dimension anchor through `View.to_view_mm`, written
        out by hand. `tests/test_render.py` asserts they agree at the source;
        this asserts it in the artefact, which is the only place a disagreement
        would actually hurt — a centre mark, a leader and a diameter all pointing
        at blank paper 20 mm from the hole they measure, on a sheet where every
        line is otherwise correct.

        Found by flipping `render.project` and discovering that the two tests
        above did not notice, because the centre marks do not come from it.
        """
        points = _line_work(built().drawing(), "top")

        # 0.15 mm, not 0.05: the true circle's own HLR chord spacing (arc length
        # over segment count) puts an arbitrary probe angle up to half a chord
        # from the nearest sampled vertex -- here, ~0.1 mm -- and that slack is
        # still two orders of magnitude below where a wrong centre lands.
        assert _roundness(points, (20.0, 10.0), 15.0) < 0.15
        assert _roundness(points, (20.0, -10.0), 15.0) > 1.0
        assert _roundness(points, (-20.0, 10.0), 15.0) > 1.0
        assert _roundness(points, (-20.0, -10.0), 15.0) > 1.0

    def test_the_pad_grows_up_the_front_view_not_down_from_it(self) -> None:
        """The part occupies world z = 0 to z = 12. The front view's own +y is
        world +Z, so its extent runs 0 to 12. An inverted frame-up would give
        −12 to 0, and every determinism check in this file would still pass."""
        low_x, low_y, high_x, high_y = built().drawing().view_named("front").extent

        assert low_y == pytest.approx(0.0, abs=1e-6)
        assert high_y == pytest.approx(12.0, abs=1e-6)
        assert (low_x, high_x) == pytest.approx((-60.0, 60.0), abs=1e-6)

    def test_the_top_view_shows_the_footprint_and_the_right_view_the_depth(self) -> None:
        """Which world axis each view's own x and y point along, asserted rather
        than assumed: the whole dimension-placement routine reads them."""
        drawing = built().drawing()

        top = drawing.view_named("top").extent
        assert top == pytest.approx((-60.0, -40.0, 60.0, 40.0), abs=1e-6)

        right = drawing.view_named("right").extent
        assert right[0] == pytest.approx(-40.0, abs=1e-6)
        assert right[2] == pytest.approx(40.0, abs=1e-6)
        assert right[1] == pytest.approx(0.0, abs=1e-6)

    def test_a_view_lands_on_the_sheet_where_its_origin_says(self) -> None:
        """`to_sheet_mm` is the only join between view millimetres and paper, and
        the promise it makes is that the extent centre lands on the origin — so a
        view moves by moving its origin and nothing else is recomputed."""
        view = built().drawing().view_named("top")
        low_x, low_y, high_x, high_y = view.extent

        centre = view.to_sheet_mm(((low_x + high_x) / 2.0, (low_y + high_y) / 2.0))

        assert centre == pytest.approx(view.origin_mm)

    def test_every_view_stays_inside_the_frame(self) -> None:
        drawing = built().drawing()
        x0, y0, x1, y1 = drawing.sheet.frame

        for view in drawing.views:
            width, height = view.size_mm
            assert view.origin_mm[0] - width / 2.0 >= x0 - 1e-6
            assert view.origin_mm[0] + width / 2.0 <= x1 + 1e-6
            assert view.origin_mm[1] - height / 2.0 >= y0 - 1e-6
            assert view.origin_mm[1] + height / 2.0 <= y1 + 1e-6


class TestFirstAngleMirrorsBothAxesAndNotOnlyOne:
    """`test_manufacture_drawing.py` pins the row mirror. The column mirror is
    the other half, and a convention applied to one axis only puts the view from
    the right on the right of a first-angle sheet — a mirrored part, with nothing
    in the line work to betray it."""

    def test_first_angle_puts_the_right_view_on_the_left(self) -> None:
        drawing = built().drawing(projection=Projection.FIRST_ANGLE)

        assert (
            drawing.view_named("right").origin_mm[0]
            < drawing.view_named("front").origin_mm[0]
        )

    def test_third_angle_puts_it_on_the_right(self) -> None:
        drawing = built().drawing(projection=Projection.THIRD_ANGLE)

        assert (
            drawing.view_named("right").origin_mm[0]
            > drawing.view_named("front").origin_mm[0]
        )

    def test_the_alignment_promise_survives_the_mirror(self) -> None:
        """The top view stays directly above or below the front view in both
        conventions — that is what lets an engineer carry a dimension between
        views with a straightedge, and it is not a tidiness rule."""
        for convention in (Projection.FIRST_ANGLE, Projection.THIRD_ANGLE):
            drawing = built().drawing(projection=convention)
            assert drawing.view_named("top").origin_mm[0] == pytest.approx(
                drawing.view_named("front").origin_mm[0]
            )
            assert drawing.view_named("right").origin_mm[1] == pytest.approx(
                drawing.view_named("front").origin_mm[1]
            )


# -- sections and details ----------------------------------------------------


class TestASectionIsACutAndSaysWhereItWasTaken:
    def test_a_section_becomes_its_own_hatched_view(self) -> None:
        drawing = built().drawing(sections=("mid-y",))

        section = drawing.view_named("section_a")
        assert section.kind is ViewKind.SECTION
        assert section.label == "SECTION A-A"
        assert section.hatch, "a section with no hatch is a view of a broken part"

    def test_the_hatch_boundaries_are_closed_outlines_not_a_fill_pattern(self) -> None:
        """Boundaries rather than pre-computed hatch lines, because the pattern
        is a property of the output format — DXF has a HATCH entity that does it
        properly, and pre-computing here would throw that away."""
        drawing = built().drawing(sections=("mid-y",))

        for boundary in drawing.view_named("section_a").hatch:
            assert len(boundary) >= 3

    def test_the_cut_is_marked_on_a_view_that_shows_the_plane_edge_on(self) -> None:
        """A section view with no cutting-plane line on the parent is a picture
        of a part nobody can locate the cut in — it could have been taken
        anywhere."""
        drawing = built().drawing(sections=("mid-y",))

        assert len(drawing.cutting_planes) == 1
        plane = drawing.cutting_planes[0]
        assert plane.label == "A"
        assert plane.view == "top"
        assert plane.start != plane.end

    def test_the_arrows_point_at_the_material_that_is_kept(self) -> None:
        """`app.render.section`'s normal points at the material that is
        *removed*, so the arrows point back along it. Two conventions for one
        question is how a part ends up mirrored with every test green, so this
        module reads the section rather than restating the rule — and this is the
        assertion that says it read it the right way round."""
        from app.render.section import section_named

        fixture = built()
        drawing = fixture.drawing(sections=("mid-y",))
        section = section_named(fixture.shape, "mid-y")
        parent = drawing.view_named("top")
        del parent

        plane = drawing.cutting_planes[0]
        # mid-y removes the +Y half, so the view looks back along −Y, which on
        # the top view (own +y is world +Y) is straight down the sheet.
        assert section.normal[1] > 0.0
        assert plane.looking[1] < 0.0

    def test_a_section_that_is_asked_for_is_the_section_that_is_drawn(self) -> None:
        drawing = built().drawing(sections=("mid-x", "mid-y"))

        names = {one.name for one in drawing.views}
        assert {"section_a", "section_b"} <= names
        assert {one.label for one in drawing.cutting_planes} == {"A", "B"}


class TestADetailIsAClipOfItsParentAndSaysItsOwnScale:
    def _detail(self, **overrides):
        request = {
            "parent": "top",
            "centre_mm": (45.0, 0.0),
            "radius_mm": 12.0,
            "magnification": 2.0,
        }
        request.update(overrides)
        return DetailRequest(**request)

    def test_a_detail_is_drawn_larger_than_the_sheet_scale(self) -> None:
        drawing = built().drawing(details=(self._detail(),))

        detail = drawing.view_named("detail_p")
        assert detail.kind is ViewKind.DETAIL
        assert detail.scale == pytest.approx(
            drawing.title_block.scale * 2.0
        )

    def test_the_label_states_the_scale_it_was_actually_drawn_at(self) -> None:
        """Not the magnification that was asked for.

        `magnification` is a factor relative to the sheet, so on a reduced sheet
        a 2x detail is not 2:1 — and a caption reading "(2:1)" beside line work
        drawn at some other scale is the wrong-scale failure this package's own
        docstring calls the one people cut metal from. The label is written from
        the scale the view carries, which is the same discipline `scale_text`
        applies to the sheet: never carry the number beside the geometry, always
        derive it from it.
        """
        drawing = built().drawing(details=(self._detail(),))
        view = drawing.view_named("detail_p")

        assert drawing.title_block.scale < 1.0
        assert view.scale == pytest.approx(drawing.title_block.scale * 2.0)
        assert view.label == f"DETAIL P ({scale_text(view.scale)})"
        assert view.label != "DETAIL P (2:1)"

    def test_an_orthographic_view_carries_no_scale_in_its_label(self) -> None:
        """Every orthographic view on the sheet is at the sheet scale, which the
        title block already states. Repeating it on each view invites a reader to
        look for the one that differs."""
        drawing = built().drawing(details=(self._detail(),))

        assert drawing.view_named("front").label == "FRONT"

    def test_every_line_in_the_detail_is_inside_its_circle(self) -> None:
        """A real clip of the parent's polylines, not a re-render — so the detail
        shows exactly the line work the parent shows and cannot disagree with it
        about the geometry."""
        detail = self._detail()
        drawing = built().drawing(details=(detail,))

        view = drawing.view_named("detail_p")
        assert view.visible or view.hidden
        for line in (*view.visible, *view.hidden):
            for x, y in line:
                distance = (
                    (x - detail.centre_mm[0]) ** 2 + (y - detail.centre_mm[1]) ** 2
                ) ** 0.5
                assert distance <= detail.radius_mm + 1e-6

    def test_a_detail_on_a_view_that_is_not_on_the_sheet_is_refused(self) -> None:
        with pytest.raises(DrawingError) as refusal:
            built().drawing(details=(self._detail(parent="bottom"),))

        assert "not a view on this sheet" in str(refusal.value)

    def test_a_detail_with_no_geometry_in_it_is_refused(self) -> None:
        """An empty magnified circle is a caption pointing at nothing, and it
        would be read as "there is nothing here" rather than "the crop missed"."""
        with pytest.raises(DrawingError) as refusal:
            built().drawing(details=(self._detail(centre_mm=(400.0, 400.0)),))

        assert "contains no geometry" in str(refusal.value)

    def test_a_detail_with_no_radius_is_refused(self) -> None:
        with pytest.raises(DrawingError):
            built().drawing(details=(self._detail(radius_mm=0.0),))


class TestTheSheetGrowsWhenTheArrangementNeedsIt:
    """The defect this pins: the sheet was chosen from an estimate that left the
    gaps out, so asking for a section and a detail on a part that fits an A4
    picked an A4 and then refused it — with a message about the title block."""

    def test_asking_for_a_section_and_a_detail_promotes_the_sheet(self) -> None:
        plain = built().drawing()
        crowded = built().drawing(
            sections=("mid-y",),
            details=(
                DetailRequest(
                    parent="top", centre_mm=(45.0, 0.0), radius_mm=12.0, magnification=2.0
                ),
            ),
        )

        assert plain.sheet.name == "A4"
        assert crowded.sheet.name == "A3"

    def test_the_chosen_sheet_has_room_for_the_gaps_the_layout_will_need(self) -> None:
        drawing = built().drawing(sections=("mid-y",))

        rows = {round(one.origin_mm[1], 6) for one in drawing.views}
        columns = {round(one.origin_mm[0], 6) for one in drawing.views}
        area_width, area_height = drawing_area(drawing.sheet)

        assert (
            area_width - NOTES_WIDTH_MM - 2.0 * DIMENSION_ALLOWANCE_MM
            - VIEW_GAP_MM * (len(columns) - 1)
        ) > 0.0
        assert (
            area_height - 2.0 * DIMENSION_ALLOWANCE_MM - VIEW_GAP_MM * (len(rows) - 1)
        ) > 0.0

    def test_a_named_sheet_that_cannot_hold_the_views_refuses_in_words(self) -> None:
        """A named sheet is never second-guessed, so it can be too small — and
        when it is, the refusal has to name what is actually wrong."""
        with pytest.raises(DrawingError) as refusal:
            built().drawing(
                sheet="A4",
                sections=("mid-x", "mid-y"),
                details=(
                    DetailRequest(
                        parent="top",
                        centre_mm=(45.0, 0.0),
                        radius_mm=12.0,
                        magnification=2.0,
                    ),
                ),
            )

        message = str(refusal.value)
        assert "larger sheet" in message or "fewer views" in message
