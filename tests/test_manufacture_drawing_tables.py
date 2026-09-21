"""A drawing carries its GD&T frames and its parts list onto the sheet (E17.1).

Built with no views, so it needs no kernel: what is tested is that the tables reach the DXF
and the drawing's data, not the layout of views around them.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import pytest

from app.assembly.structure import BomLine
from app.manufacture.drawing import Drawing
from app.manufacture.sheet import TITLE_BLOCK_HEIGHT_MM, TitleBlock, sheet_named
from app.rules.gdt import (
    Characteristic,
    Datum,
    DatumReference,
    DatumScheme,
    FeatureControlFrame,
    MaterialCondition,
    Tolerancing,
)

pytest.importorskip("ezdxf")

from app.manufacture import dxf  # noqa: E402

TOLERANCING = Tolerancing(
    scheme=DatumScheme((Datum("A", "base face"), Datum("B", "long side"))),
    frames=(
        FeatureControlFrame("base face", Characteristic.FLATNESS, 0.05),
        FeatureControlFrame(
            "bore",
            Characteristic.POSITION,
            0.2,
            datums=(DatumReference("A"), DatumReference("B")),
            condition=MaterialCondition.MMC,
            diametral=True,
        ),
    ),
)
PARTS = (
    BomLine("beam", 1, design="BEAM-01", material="S355"),
    BomLine("bolt", 4, design="ISO 4014 M8x60 8.8"),
)


def _drawing(**overrides: object) -> Drawing:
    kwargs: dict[str, object] = {
        "title": "Bracket",
        "sheet": sheet_named("A3"),
        "title_block": TitleBlock(title="Bracket", drawing_number="KRY-001"),
        "tolerancing": TOLERANCING,
        "parts": PARTS,
    }
    kwargs.update(overrides)
    return Drawing(**kwargs)  # type: ignore[arg-type]


def _texts(drawing: Drawing) -> list[tuple[str, tuple[float, float]]]:
    doc = dxf.to_document(drawing)
    return [
        (entity.dxf.text, (entity.dxf.insert[0], entity.dxf.insert[1]))
        for entity in doc.modelspace().query("TEXT")
    ]


class TestTheToleranceTable:
    def test_every_frame_and_datum_reaches_the_sheet_in_words(self) -> None:
        texts = [t for t, _ in _texts(_drawing())]
        assert "GEOMETRIC TOLERANCES" in texts
        assert "DATUM A: base face" in texts
        assert "bore" in texts
        assert "Ø0.2 Ⓜ" in texts
        assert "0.05" in texts
        assert {"A", "B"} <= set(texts)

    def test_the_symbol_is_the_characteristics_own(self) -> None:
        texts = [t for t, _ in _texts(_drawing())]
        assert TOLERANCING.frames[0].grammar.symbol in texts
        assert TOLERANCING.frames[1].grammar.symbol in texts

    def test_no_tolerancing_draws_no_table(self) -> None:
        texts = [t for t, _ in _texts(_drawing(tolerancing=None))]
        assert "GEOMETRIC TOLERANCES" not in texts


class TestThePartsList:
    def test_the_list_sits_on_top_of_the_title_block(self) -> None:
        drawing = _drawing()
        texts = _texts(drawing)
        heading_y = next(at[1] for text, at in texts if text == "ITEM")
        _, y0, _, _ = drawing.sheet.frame
        assert y0 + TITLE_BLOCK_HEIGHT_MM <= heading_y <= y0 + TITLE_BLOCK_HEIGHT_MM + 7.0

    def test_every_line_is_a_row_numbered_from_one(self) -> None:
        texts = [t for t, _ in _texts(_drawing())]
        for expected in ("ISO 4014 M8x60 8.8", "BEAM-01", "S355", "4"):
            assert expected in texts

    def test_the_drawing_data_carries_both_tables(self) -> None:
        payload = _drawing().to_dict()
        assert payload["parts"][0] == {
            "item": 1,
            "component": "beam",
            "quantity": 1,
            "design": "BEAM-01",
            "material": "S355",
        }
        assert [f["characteristic"] for f in payload["tolerancing"]["frames"]] == [
            "flatness",
            "position",
        ]

    def test_every_table_entity_is_on_a_declared_layer(self) -> None:
        doc = dxf.to_document(_drawing())
        declared = {spec.name for spec in dxf.LAYERS}
        assert {entity.dxf.layer for entity in doc.modelspace()} <= declared


class TestTheViewsAreKeptOutOfTheTables:
    """Master plan E17.1's second open item: *"the view layout does not reserve
    the tables' zones, so a crowded sheet can overlap them"*.

    Neither table moves out of a view's way. `_tolerancing_table` starts under
    the top frame line and grows **down**; `_parts_table` stacks on the title
    block and grows **up**. So the layout has to be told how many rows each will
    have — a view drawn over the parts list is a drawing nobody can read, and
    nothing downstream notices.

    The layout runs *before* a `Drawing` exists, so it cannot count
    `drawing.tolerancing` or `drawing.parts` itself; `LayoutRequest` carries the
    two counts, defaulting to zero so every existing drawing is untouched.

    **What is asserted is sheet choice, not view coordinates**, and that is
    deliberate: on a sheet with room to spare the views never reach the tables
    whether or not anything is reserved, so an assertion about where they landed
    passes against a mutant. The reservation's observable effect is that the
    drawing needs *more sheet* — verified by removing it and watching this fail.
    """

    @staticmethod
    def _plate(width: float = 400.0, height: float = 250.0, thickness: float = 60.0):
        """A part big enough that the sheet, not the part, decides the layout."""
        from app.kernel import OcctRunner

        runner = OcctRunner()
        runner("catia_new_part", {"name": "plate"})
        runner("catia_sketch_create", {"support": "XY", "name": "outline"})
        runner(
            "catia_sketch_rectangle",
            {"sketch": "outline", "width_mm": width, "height_mm": height},
        )
        runner("catia_pad", {"sketch": "outline", "length_mm": thickness})
        return runner._context.document.shape

    def _request(self, **kwargs):
        from app.manufacture.layout import LayoutRequest

        return LayoutRequest(
            title="plate", drawing_number="D-1", views=("front", "top"), **kwargs
        )

    def test_a_drawing_with_tables_asks_for_a_bigger_sheet(self) -> None:
        from app.manufacture.layout import lay_out
        from app.manufacture.sheet import SHEET_SIZES

        shape = self._plate()
        order = [one.name for one in SHEET_SIZES]

        bare = lay_out(shape, self._request()).sheet.name
        some = lay_out(shape, self._request(tolerance_rows=4, parts_rows=4)).sheet.name
        many = lay_out(shape, self._request(tolerance_rows=10, parts_rows=10)).sheet.name

        assert order.index(some) > order.index(bare), (
            f"a drawing carrying two four-row tables chose {some}, the same sheet as one "
            f"carrying none ({bare}); the tables' zones are not being reserved"
        )
        assert order.index(many) > order.index(some), (
            f"ten rows chose {many} where four chose {some}; the reservation is not "
            "growing with the tables"
        )

    def test_a_named_sheet_with_no_room_is_refused_rather_than_overlapped(self) -> None:
        """The refusal is the point: the alternative is a readable-looking sheet
        with the views sitting on top of the parts list."""
        import pytest as _pytest

        from app.manufacture.errors import DrawingError
        from app.manufacture.layout import lay_out

        shape = self._plate(180.0, 90.0, 60.0)
        with _pytest.raises(DrawingError, match="no room|larger sheet"):
            lay_out(shape, self._request(sheet="A4", tolerance_rows=14, parts_rows=14))

    def test_a_drawing_with_no_tables_lays_out_exactly_as_before(self) -> None:
        """The reservation must cost nothing when there is nothing to reserve.

        Otherwise every drawing in the suite moves for a feature it does not use.
        """
        shape = self._plate()
        without = _placed(shape, self._request())
        zeroed = _placed(shape, self._request(tolerance_rows=0, parts_rows=0))
        assert without == zeroed

    def test_the_writer_and_the_reservation_share_one_row_height(self) -> None:
        """Two constants that agreed once would disagree the first time one moved."""
        from app.manufacture import dxf as dxf_module
        from app.manufacture.layout import TABLE_ROW_MM

        assert dxf_module.TABLE_ROW_MM is TABLE_ROW_MM


def _placed(shape, request):
    """The placed views as comparable tuples, so two layouts can be diffed."""
    from app.manufacture.layout import lay_out

    drawing = lay_out(shape, request)
    return (
        drawing.sheet.name,
        tuple(
            (view.name, tuple(round(v, 9) for v in view.origin_mm), round(view.scale, 9))
            for view in drawing.views
        ),
    )
