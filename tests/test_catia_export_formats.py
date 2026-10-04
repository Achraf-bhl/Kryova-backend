"""Which formats a CATIA document can be written as — E17 task 2, measured on the seat.

E17.2 has said since 2026-09-06 that export is "tested through OCCT" with "STEP/DXF export
from a live CATIA seat" open. It was run on a V5-R33 seat on 2026-09-18, both directions,
and it found a defect in the bridge's *refusal* rather than in its export:

    PartDocument    -> stp 8,966 B | igs 12,393 B | stl 3,244 B | 3dxml 6,057 B | dxf REFUSED
    DrawingDocument -> dxf 75,363 B (AC1027) | dwg 12,357 B     | stp REFUSED

**The DXF/DWG licence is present on this seat** — the same seat wrote 75 kB of valid DXF from
a drawing seconds after refusing it from a part. But `ExportData` answers the same
`La méthode ExportData a échoué` whether a licence is missing or the document is the wrong
kind, and `_FORMAT_LICENCE` turned that into *"This needs the DXF/DWG (D2/DW1) licence on
this workstation"*. That is the most expensive possible wrong answer: it sends an engineer to
a licence server for a licence they already hold, and the real fix is one drawing away.

So `wrong_kind_of_document` refuses first, by the cause, and the licence message is left for
the failures that are actually about a licence.

**It refuses only the two directions that were measured.** An unclassifiable document is
never refused — over-refusal is the failure mode `app/catia/` warns about, and the agent's
recovery from a refusal is to try something else, which becomes a wrongly built part.

Offline: these test the decision, not the seat. What needed the seat has been done and its
numbers are the constants above.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catia_bridge.com.infrastructure import (  # noqa: E402
    _DRAWING_FORMATS,
    _FORMAT_LICENCE,
    _FORMAT_TOKENS,
    wrong_kind_of_document,
)

#: Exactly what the seat wrote, in bytes, on 2026-09-18. Here so a future run that
#: disagrees has something to disagree with.
SEAT_PART = {"step": 8966, "iges": 12393, "stl": 3244, "3dxml": 6057}
SEAT_DRAWING = {"dxf": 75363, "dwg": 12357}


class TestADrawingFormatIsRefusedOnGeometry:
    @pytest.mark.parametrize("format", sorted(_DRAWING_FORMATS))
    @pytest.mark.parametrize("kind", ["part", "product"])
    def test_it_is_refused(self, format: str, kind: str) -> None:
        assert wrong_kind_of_document(format, kind) is not None

    def test_the_refusal_says_it_is_not_a_licence_problem(self) -> None:
        """**The whole point.** The seat holds the DXF/DWG licence and wrote 75 kB of DXF
        from a drawing; a message blaming the licence costs an afternoon at the licence
        server and does not mention the one thing that would work."""
        reason = wrong_kind_of_document("dxf", "part")

        assert reason is not None
        assert "not a licence problem" in reason
        assert "make a drawing" in reason

    def test_the_refusal_names_the_kind_it_was_given(self) -> None:
        assert "product" in (wrong_kind_of_document("dwg", "product") or "")
        assert "part" in (wrong_kind_of_document("dwg", "part") or "")


class TestAGeometryFormatIsRefusedOnADrawing:
    @pytest.mark.parametrize("format", ["step", "iges", "stl", "3dxml"])
    def test_it_is_refused(self, format: str) -> None:
        """Measured in this direction too: a drawing asked for `stp` answers
        `CATIADrawingDocument ... ExportData a échoué`."""
        assert wrong_kind_of_document(format, "drawing") is not None

    def test_the_refusal_says_what_a_drawing_can_write(self) -> None:
        reason = wrong_kind_of_document("step", "drawing")

        assert reason is not None
        for format in _DRAWING_FORMATS:
            assert format in reason


class TestWhatIsDeliberatelyNotRefused:
    @pytest.mark.parametrize("format", sorted(_FORMAT_TOKENS))
    def test_an_unclassifiable_document_is_never_refused(self, format: str) -> None:
        """Over-refusal is its own failure mode: the agent's recovery from a refusal is to
        try something else, so a wrong refusal becomes a wrongly built part. A document this
        cannot classify is one CATIA may well export perfectly well, so it is passed
        through and CATIA answers."""
        assert wrong_kind_of_document(format, "unknown") is None

    @pytest.mark.parametrize("format", ["step", "iges", "stl", "3dxml"])
    @pytest.mark.parametrize("kind", ["part", "product"])
    def test_geometry_out_of_geometry_is_allowed(self, format: str, kind: str) -> None:
        assert wrong_kind_of_document(format, kind) is None

    @pytest.mark.parametrize("format", sorted(_DRAWING_FORMATS))
    def test_a_drawing_format_out_of_a_drawing_is_allowed(self, format: str) -> None:
        assert wrong_kind_of_document(format, "drawing") is None

    def test_nothing_refuses_a_format_the_bridge_does_not_declare(self) -> None:
        """`export` checks the token table first, so an unknown format never reaches this.
        Pinned because a guard that also rejected unknown names would hide that earlier,
        better message."""
        assert wrong_kind_of_document("sketch", "part") is None


class TestTheTablesStillAgree:
    def test_every_drawing_format_is_a_declared_format(self) -> None:
        assert _DRAWING_FORMATS <= set(_FORMAT_TOKENS)

    def test_every_drawing_format_still_has_a_licence_named(self) -> None:
        """The licence message is not deleted — it is narrowed to the failures that are
        about a licence. A seat without D2/DW1 must still be told which licence it lacks."""
        for format in _DRAWING_FORMATS:
            assert format in _FORMAT_LICENCE

    @pytest.mark.parametrize("format", sorted(SEAT_PART))
    def test_every_format_the_seat_wrote_from_a_part_is_declared(self, format: str) -> None:
        assert format in _FORMAT_TOKENS

    @pytest.mark.parametrize("format", sorted(SEAT_DRAWING))
    def test_every_format_the_seat_wrote_from_a_drawing_is_declared(
        self, format: str
    ) -> None:
        assert format in _FORMAT_TOKENS
