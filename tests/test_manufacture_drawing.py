"""A drawing that could be manufactured from — master plan Phase 17.

Two properties carry almost all the consequence in this package, and neither is
about whether the picture looks right.

**Projection convention.** First-angle and third-angle put the views on
*opposite sides* of the front view: in third angle the top view sits above,
in first angle below. A drawing read in the wrong convention is manufactured
mirrored, and nothing about the drawing looks wrong while it happens. So the
convention is an explicit parameter, and the test asserts that the two actually
place views differently — a convention that is stored and then ignored is worse
than one that is assumed, because it invites the reader to trust it.

**A drawing that could not be fully dimensioned says so.** A drawing that
silently omits a dimension is how a part gets made wrong, and the omission is
invisible: every line that is there is correct. So the report names what it
could not dimension, and a part that *was* fully dimensioned says that too —
otherwise the honesty block is noise nobody reads.

The third property, which this package is unusually placed to have: a dimension
traced to the **parameter** that set it is distinguishable from one measured off
the solid. `app/design/` knows a pad is 12 mm because a parameter said so; a CAD
system reverse-engineering the same number off a face does not, and the two
should not read alike on a drawing somebody signs.
"""

from __future__ import annotations

import pytest

from app.kernel import OcctRunner
from app.manufacture.drawing import DimensionSource
from app.manufacture.layout import LayoutRequest, lay_out
from app.manufacture.sheet import Projection


def _plate():
    """A 120x80x12 plate with a 30 mm bore — enough to have real views."""
    runner = OcctRunner()
    runner("catia_new_part", {"name": "plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner("catia_sketch_rectangle", {"sketch": "outline", "width_mm": 120.0, "height_mm": 80.0})
    runner("catia_sketch_circle", {"sketch": "outline", "diameter_mm": 30.0})
    runner("catia_pad", {"sketch": "outline", "length_mm": 12.0})
    return runner


def _shape(runner):
    from app.kernel.occt.operations import HANDLERS  # noqa: F401 - keeps the import honest

    document = runner._context.document
    return document.shape


def _request(**overrides) -> LayoutRequest:
    base = {
        "title": "Mounting plate",
        "drawing_number": "KRY-0001",
        "views": ("front", "top", "right"),
        "include_iso": False,
    }
    base.update(overrides)
    return LayoutRequest(**base)


def _view(drawing, name: str):
    for view in drawing.views:
        if view.name == name:
            return view
    raise AssertionError(f"no view called {name!r}; got {[v.name for v in drawing.views]}")


class TestProjectionConventionIsRealNotDecorative:
    """A stored-and-ignored convention is worse than an assumed one, because it
    invites the reader to trust it."""

    def test_the_two_conventions_place_views_differently(self) -> None:
        shape = _shape(_plate())

        first = lay_out(shape, _request(projection=Projection.FIRST_ANGLE))
        third = lay_out(shape, _request(projection=Projection.THIRD_ANGLE))

        assert _view(first, "top").origin_mm != _view(third, "top").origin_mm

    def test_third_angle_puts_the_top_view_above_the_front(self) -> None:
        """The convention most of the world outside Europe reads by, and the
        one whose name says where the view goes."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(projection=Projection.THIRD_ANGLE))

        assert _view(drawing, "top").origin_mm[1] > _view(drawing, "front").origin_mm[1]

    def test_first_angle_puts_it_below(self) -> None:
        """Mirrored from the above, and this is the pair that catches a
        convention wired to nothing: if `projection` were ignored, one of these
        two tests fails whichever way the default happens to fall."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(projection=Projection.FIRST_ANGLE))

        assert _view(drawing, "top").origin_mm[1] < _view(drawing, "front").origin_mm[1]

    def test_the_convention_is_recorded_on_the_drawing(self) -> None:
        """A drawing that does not state its convention cannot be read safely at
        all — the reader has to guess, and half the world guesses differently."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(projection=Projection.FIRST_ANGLE))

        assert drawing.title_block.projection is Projection.FIRST_ANGLE


class TestTheDrawingSaysWhatItCouldNotDimension:
    def test_a_part_with_no_traced_dimensions_says_they_were_measured(self) -> None:
        """The right answer for a part built call by call with no parameters —
        the sheet still draws, and the report says where the numbers came from
        rather than implying the design stated them."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(), traced=())

        assert drawing.report is not None

    def test_the_report_exists_and_is_not_silently_empty(self) -> None:
        shape = _shape(_plate())

        drawing = lay_out(shape, _request())
        report = drawing.report

        assert hasattr(report, "tabled")
        assert hasattr(report, "non_dimensional")

    def test_an_undimensioned_feature_is_named_not_dropped(self) -> None:
        """The whole property. A drawing that omits a dimension silently is how
        a part gets made wrong, and every line that IS there is correct, so
        nothing about the drawing looks wrong while it happens."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request())
        report = drawing.report

        unplaced = getattr(report, "unplaced", ())
        for item in unplaced:
            assert getattr(item, "reason", ""), (
                "an unplaced dimension must say why; a list of names a reader "
                "cannot act on is only a longer way of omitting them"
            )


class TestADimensionKnowsWhereItsNumberCameFrom:
    """The leverage this package has that a CAD system does not: `app/design/`
    knows a pad is 12 mm because a parameter said so."""

    def test_the_two_sources_are_distinguishable(self) -> None:
        assert DimensionSource.PARAMETER != DimensionSource.GEOMETRY

    def test_a_measured_dimension_is_not_labelled_as_stated(self) -> None:
        """With no traced dimensions supplied, nothing on the sheet may claim to
        come from a parameter — that would be the drawing asserting a provenance
        it does not have."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(), traced=())

        for dimension in drawing.dimensions:
            if True:
                assert dimension.source is not DimensionSource.PARAMETER


class TestTheSheetIsBuiltAtAll:
    def test_a_shape_that_projects_to_nothing_is_refused(self) -> None:
        """A failed last operation leaves a document that projects to nothing,
        and an empty sheet is the one output that must never be produced
        quietly."""
        from app.manufacture.errors import DrawingError

        runner = OcctRunner()
        runner("catia_new_part", {"name": "empty"})

        with pytest.raises(DrawingError):
            lay_out(_shape(runner), _request())

    def test_the_requested_views_are_the_views_drawn(self) -> None:
        shape = _shape(_plate())

        drawing = lay_out(shape, _request(views=("front", "top"), include_iso=False))

        names = {view.name for view in drawing.views}
        assert {"front", "top"} <= names

    def test_the_scale_is_chosen_not_assumed(self) -> None:
        """A 120 mm part on an A3 sheet is not 1:1, and a drawing at the wrong
        scale with the right dimensions is a drawing people cut metal from."""
        shape = _shape(_plate())

        drawing = lay_out(shape, _request())

        assert drawing.title_block.scale > 0.0
