"""The files that leave the building: DXF for the drawing, STEP for the solid.

Two artefacts for two readers, and the board recorded both as unverified.

**Units are the failure that costs a part.** A DXF with no `$INSUNITS` opens in
whatever the receiving system defaults to, and on an American seat that is
inches: the same numbers, read 25.4 times too small, on a drawing where every
dimension still says the right thing. So the header is pinned, and so is
`$MEASUREMENT`, which is what decides whether the linetype and hatch pattern
definitions loaded are the metric ones.

**A dimension's text is the design's number, never the length of the line under
it.** The bracket lands on an A4 at 1:2, so the line under its 120 mm edge is
60 mm long on the paper. `TestTheDimensionTextIsTheDesignsNumber` measures both:
the DIMENSION entity's own defpoints are 60 apart and its text says 120. An
associative dimension — what every CAD system writes — would say 60.

**Determinism, and its blind spot.** `app/render/` refuses anti-aliasing, rounds
with `floor(v+0.5)` and hand-writes its PNG encoder, all so two runs of the same
geometry produce the same bytes. The DXF inherits that and it is not free: ezdxf
stamps two GUIDs, four julian dates and its own version-and-timestamp markers,
two of them regenerated inside `Document.write` where they cannot be pre-set.
`dxf._scrub` removes them, and `test_two_writes_of_the_same_drawing_are_the_same_bytes`
is what says so. But byte-equality cannot see a mirrored drawing — a consistently
flipped sheet is byte-identical to itself — so the positional assertions live in
`test_manufacture_sheet.py` and this file only claims what bytes can carry.

**STEP round-trips and its bytes do not.** `write_step` produces a file OCCT
reads back to the same volume, extent and topology, to nine figures — it is an
exact B-rep exchange and the only loss is decimal text precision. It is *not*
reproducible: a STEP header carries the wall-clock time OCCT wrote it, in a
single C++ call this codebase does not write. So the claim made here is about the
geometry, measured, rather than about the file being non-empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.kernel.occt.metrology import bounding_box_mm, surface_area_mm2, volume_mm3
from app.manufacture import dxf
from app.manufacture.drawing import DimensionKind
from app.manufacture.errors import ExportError
from app.manufacture.export import (
    ROUND_TRIP_TOLERANCE,
    StepSchema,
    read_step,
    step_schema_of,
    write_step,
)
from app.manufacture.layout import DetailRequest
from tests.test_manufacture_dimensions import built

ezdxf = pytest.importorskip("ezdxf")


def _drawing(**overrides):
    return built().drawing(**overrides)


def _sectioned():
    """A sheet with a cut on it, so the hatch and cutting-plane layers are used."""
    return built().drawing(
        sections=("mid-y",),
        details=(
            DetailRequest(
                parent="top", centre_mm=(45.0, 0.0), radius_mm=12.0, magnification=2.0
            ),
        ),
    )


# -- DXF ---------------------------------------------------------------------


class TestTheFileIsADxfSomethingElseCanOpen:
    def test_it_is_written_and_reopens(self, tmp_path: Path) -> None:
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        assert doc.dxfversion == ezdxf.DXF2010
        assert len(list(doc.modelspace())) > 0

    def test_it_reopens_through_the_recover_reader_too(self, tmp_path: Path) -> None:
        """`ezdxf.recover` is what a forgiving CAD system does. If it reports
        errors on a file we wrote, the file is malformed in a way `readfile`
        happened to tolerate."""
        from ezdxf import recover

        target = dxf.write(_sectioned(), tmp_path / "bracket.dxf")

        _, auditor = recover.readfile(target)

        assert not auditor.errors

    def test_a_path_that_is_not_a_dxf_is_refused(self, tmp_path: Path) -> None:
        """Every receiving system picks its reader from the extension."""
        with pytest.raises(ExportError) as refusal:
            dxf.write(_drawing(), tmp_path / "bracket.dwg")

        assert ".dxf" in str(refusal.value)

    def test_the_directory_is_created_rather_than_the_write_failing(
        self, tmp_path: Path
    ) -> None:
        target = dxf.write(_drawing(), tmp_path / "out" / "deep" / "bracket.dxf")

        assert target.is_file()


class TestTheUnitsAreDeclaredAndNotAssumed:
    def test_the_header_says_millimetres(self, tmp_path: Path) -> None:
        """`$INSUNITS` 4. A file that does not say opens in inches once, and the
        part comes back 25.4 times too small with every dimension still right."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        assert doc.header["$INSUNITS"] == dxf.INSUNITS_MILLIMETRES == 4

    def test_and_that_the_drawing_is_metric(self, tmp_path: Path) -> None:
        """`$MEASUREMENT` decides which linetype and hatch pattern file is
        loaded. Millimetres drawn with imperial dash lengths is what setting one
        and not the other gives."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        assert doc.header["$MEASUREMENT"] == dxf.MEASUREMENT_METRIC == 1

    def test_the_sheet_size_travels_as_geometry_not_as_a_header_variable(
        self, tmp_path: Path
    ) -> None:
        """`$LIMMAX` and `$EXTMAX` are recomputed by ezdxf inside `write`, so
        setting them writes a promise that never reaches the disk. The sheet is
        the outer rectangle on the FRAME layer instead, at 1:1 in millimetres —
        what a reader sees and what a plotter measures."""
        drawing = _drawing()
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        corners = [
            tuple(point)[:2]
            for one in doc.modelspace()
            if one.dxftype() == "LWPOLYLINE" and one.dxf.layer == dxf.FRAME
            for point in one.get_points("xy")
        ]
        assert (0.0, 0.0) in corners
        assert (drawing.sheet.width_mm, drawing.sheet.height_mm) in corners


class TestEverythingLandsOnADeclaredLayer:
    def test_every_declared_layer_exists_in_the_file(self, tmp_path: Path) -> None:
        target = dxf.write(_sectioned(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        present = {layer.dxf.name for layer in doc.layers}
        assert {one.name for one in dxf.LAYERS} <= present

    def test_nothing_is_left_on_layer_zero(self, tmp_path: Path) -> None:
        """An entity on `0` inherits whatever the block it lands in is drawn as,
        which is how a hidden edge ends up looking solid."""
        target = dxf.write(_sectioned(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        stray = [one.dxftype() for one in doc.modelspace() if one.dxf.layer == "0"]
        assert not stray

    def test_every_entity_is_on_a_layer_this_writer_declares(
        self, tmp_path: Path
    ) -> None:
        target = dxf.write(_sectioned(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        declared = {one.name for one in dxf.LAYERS}
        for entity in doc.modelspace():
            assert entity.dxf.layer in declared, (
                f"{entity.dxftype()} landed on {entity.dxf.layer!r}"
            )

    def test_the_visible_and_hidden_edges_stay_apart(self, tmp_path: Path) -> None:
        """The most informative distinction in a drawing: it is what says a
        feature is behind another one, and merging the two loses which."""
        drawing = _drawing()
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)
        by_layer: dict[str, int] = {}
        for entity in doc.modelspace():
            if entity.dxftype() == "LWPOLYLINE":
                by_layer[entity.dxf.layer] = by_layer.get(entity.dxf.layer, 0) + 1

        expected_visible = sum(len(one.visible) for one in drawing.views)
        expected_hidden = sum(len(one.hidden) for one in drawing.views)
        assert expected_visible and expected_hidden
        assert by_layer[dxf.OUTLINE] == expected_visible
        assert by_layer[dxf.HIDDEN] == expected_hidden

    def test_the_hidden_layer_is_actually_dashed(self, tmp_path: Path) -> None:
        """A hidden line drawn solid is a visible line, whatever layer it is on."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        assert doc.layers.get(dxf.HIDDEN).dxf.linetype == "DASHED"
        assert doc.layers.get(dxf.OUTLINE).dxf.linetype == "CONTINUOUS"

    def test_the_outline_is_drawn_wider_than_the_dimensions(self, tmp_path: Path) -> None:
        """ISO 128's two line groups. A sheet printed at one weight throughout is
        one where the part stops standing out from what is written on it."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        assert (
            doc.layers.get(dxf.OUTLINE).dxf.lineweight
            > doc.layers.get(dxf.DIMENSION).dxf.lineweight
        )
        assert doc.header["$LWDISPLAY"] == 1

    def test_a_section_puts_its_hatch_and_its_cut_line_on_their_own_layers(
        self, tmp_path: Path
    ) -> None:
        target = dxf.write(_sectioned(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        kinds = {(one.dxftype(), one.dxf.layer) for one in doc.modelspace()}
        assert ("HATCH", dxf.HATCH) in kinds
        assert ("LWPOLYLINE", dxf.SECTION) in kinds

    def test_the_centre_marks_reach_the_file(self, tmp_path: Path) -> None:
        """A hole with no centre mark is a hole an inspector cannot find the
        middle of."""
        drawing = _drawing()
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        marks = [one for one in doc.modelspace() if one.dxf.layer == dxf.CENTRE]
        assert len(marks) == sum(len(one.centre_lines) for one in drawing.views)
        assert marks

    def test_an_undeclared_layer_is_refused_rather_than_created(self) -> None:
        """The guard that keeps `LAYERS` a declaration rather than a comment: an
        entity written to a layer nobody created lands in the file with default
        properties and looks *almost* right."""
        with pytest.raises(ExportError) as refusal:
            dxf._check_layer("GD&T")

        assert dxf.OUTLINE in str(refusal.value)


class TestTheDimensionTextIsTheDesignsNumber:
    def test_every_placed_dimension_becomes_a_dimension_entity(
        self, tmp_path: Path
    ) -> None:
        drawing = _drawing()
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        entities = [one for one in doc.modelspace() if one.dxftype() == "DIMENSION"]
        assert len(entities) == len(drawing.dimensions)
        assert {one.dxf.text for one in entities} == {
            one.text for one in drawing.dimensions
        }

    def test_the_text_is_the_model_number_and_the_line_is_the_paper_length(
        self, tmp_path: Path
    ) -> None:
        """The whole reason for the override. At 1:2 the line under a 120 mm edge
        is 60 mm long on the sheet, and an associative dimension would print 60 —
        a drawing that lies at every scale but 1:1."""
        drawing = _drawing()
        assert drawing.title_block.scale == pytest.approx(0.5)
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        width = next(
            one
            for one in doc.modelspace()
            if one.dxftype() == "DIMENSION" and one.dxf.text == "120"
        )
        start, end = width.dxf.defpoint2, width.dxf.defpoint3
        drawn = ((start[0] - end[0]) ** 2 + (start[1] - end[1]) ** 2) ** 0.5
        assert drawn == pytest.approx(60.0, abs=1e-6)

    def test_the_iso_count_prefix_and_the_symbol_survive_into_the_file(
        self, tmp_path: Path
    ) -> None:
        """`6X Ø8`, not `Ø8` six times and not `8`. An associative dimension
        cannot know to add either half."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        texts = {one.dxf.text for one in doc.modelspace() if one.dxftype() == "DIMENSION"}
        assert "6X Ø8" in texts
        assert "4X R6" in texts

    def test_a_diameter_dimension_points_at_the_feature_it_measures(
        self, tmp_path: Path
    ) -> None:
        """The leader lands on the bore's centre in *sheet* millimetres — the
        drawing's own conversion, not a second one written here."""
        drawing = _drawing()
        bore = next(
            one
            for one in drawing.dimensions
            if one.kind is DimensionKind.DIAMETER and one.parameter == "bore_mm"
        )
        expected = drawing.view_named(bore.view).to_sheet_mm(bore.centre)
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        entity = next(
            one
            for one in doc.modelspace()
            if one.dxftype() == "DIMENSION" and one.dxf.text == bore.text
        )
        # A DXF diameter dimension is anchored by the two ends of its diameter
        # line, not by a centre; the centre is their midpoint.
        far, near = entity.dxf.defpoint, entity.dxf.defpoint4
        centre = ((far[0] + near[0]) / 2.0, (far[1] + near[1]) / 2.0)
        assert centre == pytest.approx(expected, abs=1e-6)


class TestTheSheetCarriesItsOwnHonestyBlock:
    def test_the_incompleteness_statement_is_printed_on_the_drawing(
        self, tmp_path: Path
    ) -> None:
        """Not only returned in `DimensionReport`. A warning nobody prints is not
        a warning, and the binding rule for this phase is that the drawing itself
        says what it could not dimension."""
        drawing = _drawing()
        assert not drawing.fully_dimensioned
        target = dxf.write(drawing, tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        notes = " ".join(
            one.dxf.text
            for one in doc.modelspace()
            if one.dxftype() == "TEXT" and one.dxf.layer == dxf.NOTES
        )
        assert "DO NOT MANUFACTURE" in notes

    def test_the_unplaced_dimension_and_its_reason_are_both_on_the_sheet(
        self, tmp_path: Path
    ) -> None:
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        notes = " ".join(
            one.dxf.text
            for one in doc.modelspace()
            if one.dxftype() == "TEXT" and one.dxf.layer == dxf.NOTES
        )
        assert "plate.edge_break" in notes
        assert "overall extent" in notes

    def test_the_title_block_prints_what_is_not_known_rather_than_a_blank(
        self, tmp_path: Path
    ) -> None:
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        text = " ".join(
            one.dxf.text for one in doc.modelspace() if one.dxftype() == "TEXT"
        )
        assert "NOT MEASURED" in text
        assert "NONE STATED" in text
        assert "DIMENSIONS IN MILLIMETRES" in text
        assert "SCALE 1:2" in text

    def test_the_projection_convention_is_stamped_in_words_and_in_a_symbol(
        self, tmp_path: Path
    ) -> None:
        """A symbol nobody was taught to read is decoration; words alone are what
        a hurried reader skips. Both, on every sheet."""
        target = dxf.write(_drawing(), tmp_path / "bracket.dxf")

        doc = ezdxf.readfile(target)

        text = " ".join(
            one.dxf.text for one in doc.modelspace() if one.dxftype() == "TEXT"
        )
        assert "FIRST ANGLE" in text
        circles = [
            one
            for one in doc.modelspace()
            if one.dxftype() == "CIRCLE" and one.dxf.layer == dxf.FRAME
        ]
        assert len(circles) == 2
        # First angle: the large circle of the truncated cone is on the left.
        big, small = sorted(circles, key=lambda one: -one.dxf.radius)
        assert big.dxf.center[0] < small.dxf.center[0]

    def test_third_angle_draws_the_symbol_the_other_way_round(
        self, tmp_path: Path
    ) -> None:
        from app.manufacture.sheet import Projection

        target = dxf.write(
            _drawing(projection=Projection.THIRD_ANGLE), tmp_path / "bracket.dxf"
        )

        doc = ezdxf.readfile(target)

        circles = [
            one
            for one in doc.modelspace()
            if one.dxftype() == "CIRCLE" and one.dxf.layer == dxf.FRAME
        ]
        big, small = sorted(circles, key=lambda one: -one.dxf.radius)
        assert big.dxf.center[0] > small.dxf.center[0]


class TestTheBytesAreReproducible:
    def test_two_writes_of_the_same_drawing_are_the_same_bytes(
        self, tmp_path: Path
    ) -> None:
        """Two runs of the same design must produce the same file or a reviewer
        cannot diff them, which is how anyone sees what an edit did."""
        drawing = _drawing()

        first = dxf.write(drawing, tmp_path / "a.dxf").read_bytes()
        second = dxf.write(drawing, tmp_path / "b.dxf").read_bytes()

        assert first == second

    def test_it_survives_laying_the_drawing_out_again(self, tmp_path: Path) -> None:
        first = dxf.write(_drawing(), tmp_path / "a.dxf").read_bytes()
        second = dxf.write(_drawing(), tmp_path / "b.dxf").read_bytes()

        assert first == second

    def test_a_different_drawing_produces_different_bytes(self, tmp_path: Path) -> None:
        """Otherwise the two above pass on a writer that emits a constant."""
        first = dxf.write(_drawing(), tmp_path / "a.dxf").read_bytes()
        second = dxf.write(_sectioned(), tmp_path / "b.dxf").read_bytes()

        assert first != second

    def test_the_clock_stamps_are_gone_rather_than_merely_equal(self) -> None:
        """Named explicitly, because "the two files matched" would also be true
        of two writes in the same millisecond — and then fail in CI at midnight.
        `$VERSIONGUID` and the ezdxf marker are regenerated *inside*
        `Document.write`, so they cannot be pre-set and are scrubbed after."""
        text = dxf.to_text(_drawing())

        assert "$VERSIONGUID" in text
        assert text.count("{00000000-0000-0000-0000-000000000000}") >= 2
        assert " @ 20" not in text, "an ezdxf timestamp marker survived the scrub"

    def test_the_scrub_rewrites_values_and_never_a_group_code(self) -> None:
        """A DXF is strictly two lines per tag. A scrub that shifted the stream
        would produce a file that still parses and means something else."""
        text = dxf.to_text(_drawing())
        lines = text.split("\n")

        codes = [lines[index] for index in range(0, len(lines) - 1, 2)]
        assert all(one.strip().lstrip("-").isdigit() for one in codes)
        assert lines[0].strip() == "0" and lines[1] == "SECTION"


# -- STEP --------------------------------------------------------------------


class TestTheSolidRoundTripsThroughStep:
    def test_the_volume_comes_back(self, tmp_path: Path) -> None:
        """Measured, not asserted from the file's size. STEP is an exact B-rep
        exchange, so the only loss is decimal text precision — a tolerance loose
        enough to pass a tessellated export would not be checking anything."""
        shape = built().shape
        record = write_step(shape, tmp_path / "bracket.step")

        back = read_step(record.path)

        assert volume_mm3(back) == pytest.approx(
            volume_mm3(shape), rel=ROUND_TRIP_TOLERANCE
        )

    def test_and_so_does_the_extent_and_the_surface_area(self, tmp_path: Path) -> None:
        """Volume alone survives a part that came back inside out or scaled in
        one axis; the extent is what says the shape is the same shape."""
        shape = built().shape
        record = write_step(shape, tmp_path / "bracket.step")

        back = read_step(record.path)

        assert bounding_box_mm(back)["size"] == pytest.approx(
            bounding_box_mm(shape)["size"], rel=ROUND_TRIP_TOLERANCE
        )
        assert surface_area_mm2(back) == pytest.approx(
            surface_area_mm2(shape), rel=ROUND_TRIP_TOLERANCE
        )

    def test_the_topology_comes_back_and_not_a_mesh(self, tmp_path: Path) -> None:
        """A tessellated export has thousands of triangular faces and the same
        volume. Face count is what tells the two apart."""
        from app.kernel.occt.topology import FACE, explore

        shape = built().shape
        record = write_step(shape, tmp_path / "bracket.step")

        back = read_step(record.path)

        assert len(list(explore(back, FACE))) == len(list(explore(shape, FACE)))

    def test_the_record_says_what_was_written(self, tmp_path: Path) -> None:
        record = write_step(built().shape, tmp_path / "bracket.step")

        assert record.unit == "mm"
        assert record.schema is StepSchema.AP242
        assert record.size_bytes == record.path.stat().st_size
        assert record.to_dict()["format"] == "STEP"


class TestTheSchemaIsTheOneThatWasAskedFor:
    def test_ap242_is_actually_in_the_file(self, tmp_path: Path) -> None:
        """The gotcha this pins: `Interface_Static.SetCVal_s` returns `False`
        with no effect until a `STEPControl_Writer` has been constructed, and
        every example on the internet discards the return value. Set the schema
        before the constructor and the file comes out AP214 with the manifest
        claiming AP242 — and nothing anywhere says so."""
        record = write_step(built().shape, tmp_path / "bracket.step")

        assert "AP242" in step_schema_of(record.path).upper()

    def test_and_ap214_is_when_that_is_asked_for(self, tmp_path: Path) -> None:
        """The pair. If the parameter were being ignored, one of these two fails
        whichever way OCCT's default happens to fall."""
        record = write_step(
            built().shape, tmp_path / "old.step", schema=StepSchema.AP214
        )

        declared = step_schema_of(record.path).upper()
        assert "AUTOMOTIVE_DESIGN" in declared
        assert "AP242" not in declared

    def test_a_file_with_no_schema_header_is_not_a_step_file(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "not.step"
        target.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\n", encoding="utf-8")

        with pytest.raises(ExportError):
            step_schema_of(target)


class TestStepRefusesRatherThanWritingSomethingUseless:
    def test_an_empty_document_is_refused(self, tmp_path: Path) -> None:
        """A header-only STEP opens without error everywhere and shows an empty
        part — the export equivalent of a blank drawing."""
        with pytest.raises(ExportError) as refusal:
            write_step(None, tmp_path / "empty.step")

        assert "nothing to export" in str(refusal.value)

    def test_a_path_with_the_wrong_extension_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError) as refusal:
            write_step(built().shape, tmp_path / "bracket.iges")

        assert ".step" in str(refusal.value)

    def test_stp_is_accepted_because_half_the_world_writes_it(
        self, tmp_path: Path
    ) -> None:
        record = write_step(built().shape, tmp_path / "bracket.stp")

        assert record.path.is_file()

    def test_reading_a_file_that_is_not_there_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ExportError):
            read_step(tmp_path / "absent.step")

    def test_reading_something_that_is_not_step_is_refused(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "rubbish.step"
        target.write_text("this is not a STEP file at all\n", encoding="utf-8")

        with pytest.raises(ExportError):
            read_step(target)


class TestWhatStepDoesNotPromise:
    def test_the_bytes_are_not_reproducible_and_the_module_says_which_stamp(
        self, tmp_path: Path
    ) -> None:
        """Stated rather than quietly hoped for. OCCT writes the header in one
        C++ call and puts the wall clock in `FILE_NAME`, so a STEP export cannot
        join the render hash and the plan digest as an identity check the way the
        DXF can. Asserting the two files *differ* would be the wrong test — two
        writes in the same second are identical — so this pins the stamp that
        makes the claim unavailable.
        """
        record = write_step(built().shape, tmp_path / "bracket.step")

        header = record.path.read_text(encoding="utf-8", errors="replace")[:600]
        assert "FILE_NAME" in header
        assert "20" in header.split("FILE_NAME", 1)[1][:120]

    def test_no_pmi_is_written_and_none_is_implied(self, tmp_path: Path) -> None:
        """AP242 *can* carry tolerances; this build has none in the design IR, so
        it writes none. A file whose schema advertises PMI and carries none is
        fine; one that carried invented tolerances would not be."""
        record = write_step(built().shape, tmp_path / "bracket.step")

        body = record.path.read_text(encoding="utf-8", errors="replace")
        assert "DIMENSIONAL_SIZE" not in body
        assert "GEOMETRIC_TOLERANCE" not in body
