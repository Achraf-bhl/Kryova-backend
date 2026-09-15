"""Which AP242 edition a STEP file is, read from its own header (master plan E21.2).

Offline, on the real kernel. **Written on Linux on 2026-09-15 and not run there as pytest** (the
user's rule). What the writer produces was checked by a one-off script the same day: AP242 wrote
`{1 0 10303 442 1 1 4 }`, AP214 wrote `{ 1 0 10303 214 1 1 1 1 }`, AP203 wrote no identifier, and
the XDE writer matched the plain one.

Kept out of `tests/test_manufacture_export.py`, whose `importorskip("ezdxf")` would skip these too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.manufacture.export import (
    AP242_EDITIONS,
    AP242_EDITIONS_READ_ON,
    AP242_EDITIONS_SOURCE,
    AP242_SCHEMA_NAME,
    StepSchema,
    ap242_edition_of,
    object_identifier_of,
    write_step,
)

pytest.importorskip("OCP", reason="the edition is read from a file this build writes")

AP242_HEADER = "FILE_SCHEMA(('" + AP242_SCHEMA_NAME + " {}'))"


def _box():  # type: ignore[no-untyped-def]
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()


class TestTheIdentifierDecidesTheEdition:
    @pytest.mark.parametrize(
        ("identifier", "edition"),
        [
            ("{1 0 10303 442 1 1 4 }", 1),  # OCCT's spacing
            ("{ 1 0 10303 442 1 1 4 }", 1),  # STEP Tools' spacing
            ("{ 1 0 10303 442 3 1 4 }", 2),
            ("{ 1 0 10303 442 4 1 4 }", 3),
        ],
    )
    def test_each_listed_identifier_names_its_edition(self, identifier: str, edition: int) -> None:
        assert ap242_edition_of(AP242_HEADER.format(identifier)) == edition

    def test_the_second_edition_is_version_arc_three_not_two(self) -> None:
        """The trap a formula would fall into: edition = arc is wrong for edition 2."""
        assert ap242_edition_of(AP242_HEADER.format("{ 1 0 10303 442 2 1 4 }")) is None

    def test_an_identifier_the_table_does_not_hold_is_unknown_not_the_nearest(self) -> None:
        assert ap242_edition_of(AP242_HEADER.format("{ 1 0 10303 442 5 1 4 }")) is None

    def test_an_ap242_header_with_no_identifier_names_no_edition(self) -> None:
        assert ap242_edition_of(AP242_HEADER.format("")) is None

    def test_another_protocol_is_not_an_ap242_edition(self) -> None:
        header = "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'))"
        assert object_identifier_of(header) == (1, 0, 10303, 214, 1, 1, 1, 1)
        assert ap242_edition_of(header) is None

    def test_the_table_says_where_it_was_read(self) -> None:
        assert AP242_EDITIONS_SOURCE.startswith("https://www.steptools.com/")
        assert AP242_EDITIONS_READ_ON == "2026-09-15"
        assert sorted(AP242_EDITIONS.values()) == [1, 2, 3]
        assert all(identifier[:4] == (1, 0, 10303, 442) for identifier in AP242_EDITIONS)


class TestWhatThisBuildWrites:
    def test_kryovas_ap242_is_the_first_edition(self, tmp_path: Path) -> None:
        """Fails on purpose the day OCCT writes another identifier, like
        `test_this_build_cannot_write_a_published_ap242_edition` in the interop tests."""
        record = write_step(_box(), tmp_path / "box.step", schema=StepSchema.AP242)

        assert record.object_identifier == (1, 0, 10303, 442, 1, 1, 4)
        assert record.ap242_edition == 1
        assert record.to_dict()["ap242_edition"] == 1
        assert record.to_dict()["object_identifier"] == [1, 0, 10303, 442, 1, 1, 4]

    def test_ap214_carries_its_identifier_and_no_ap242_edition(self, tmp_path: Path) -> None:
        record = write_step(_box(), tmp_path / "box.step", schema=StepSchema.AP214)

        assert record.object_identifier == (1, 0, 10303, 214, 1, 1, 1, 1)
        assert record.ap242_edition is None

    def test_ap203_declares_no_identifier_and_the_record_says_so(self, tmp_path: Path) -> None:
        record = write_step(_box(), tmp_path / "box.step", schema=StepSchema.AP203)

        assert record.object_identifier is None
        assert record.ap242_edition is None

    def test_the_metadata_writer_names_the_same_edition(self, tmp_path: Path) -> None:
        from app.manufacture.xde import Annotations, write_step_with_metadata

        record = write_step_with_metadata(_box(), tmp_path / "named.step", Annotations(name="Box"))

        assert record.ap242_edition == 1
