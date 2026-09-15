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
