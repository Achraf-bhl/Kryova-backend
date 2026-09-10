"""What OCCT's XDE layer carries, and the one thing it silently gets wrong.

`test_manufacture_export.py` proves a solid survives the trip and
`test_manufacture_interop.py` proves the *matrix* is honest. This proves the
path underneath both: the writer that puts names, colours, layers, validation
properties and tolerances into a STEP file, and the reader that gets them out.

**The test in here that matters most is a tripwire.**
`test_a_tolerance_does_not_survive_its_own_round_trip` asserts that a flatness
tolerance authored at 0.05 mm comes back as 50.0 mm. That is not a test of
desired behaviour — it is a measurement of a defect, pinned so that the day OCCT
fixes it the suite says so and somebody widens what the product is allowed to
claim, on purpose. Writing it the other way round (asserting the value survives,
marked xfail) would let the fix land silently, and this codebase's rule is that
a claim gets widened deliberately or not at all.

**Every read here is a guard against a segfault, not against a wrong answer.**
`TDF_Label.FindAttribute` on an absent attribute takes the interpreter down —
so `test_a_file_with_no_metadata_reads_back_as_nothing_rather_than_crashing` is
the whole of `_attribute`'s coverage, and it fails by killing the run rather
than by printing an assertion. That is worth knowing before debugging a
"hanging" suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.manufacture.errors import ExportError
from app.manufacture.export import StepSchema
from app.manufacture.xde import (
    SEMANTIC_PMI_IS_WRONG_BY,
    Annotations,
    Occurrence,
    Tolerance,
    ValidationProperties,
    read_step_with_metadata,
    semantic_pmi_survives_a_round_trip,
    write_step_with_metadata,
)

pytest.importorskip("OCP", reason="the XDE path is measured on the real kernel")

REFERENCE = Annotations(
    name="Bracket",
    colour_rgb=(0.2, 0.4, 0.9),
    layer="KRYOVA-PART",
    properties=ValidationProperties(
        volume_mm3=6000.0, area_mm2=2200.0, centroid_mm=(5.0, 10.0, 15.0)
    ),
    tolerance=Tolerance(value_mm=0.05),
    occurrences=(Occurrence("Leg.1"), Occurrence("Leg.2", (100.0, 0.0, 0.0))),
)


@pytest.fixture(scope="module")
def block():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()


@pytest.fixture(scope="module")
def written(block, tmp_path_factory):
    target = tmp_path_factory.mktemp("xde") / "annotated.step"
    export = write_step_with_metadata(block, target, REFERENCE)
    return export, read_step_with_metadata(target)


class TestWhatTheXdeLayerCarries:
    def test_the_part_name_survives(self, written) -> None:
        _, recovered = written
        assert recovered.name == "Bracket"

    def test_the_colour_survives_but_not_as_the_type_it_was_written_as(self, written) -> None:
        """Written `ColorGen`, returned `ColorSurf` and `ColorCurv`.

        A caller that asks for the type it wrote back is told there is no
        colour, which is how a carried class gets recorded as lost.
        """
        _, recovered = written
        assert recovered.colour_rgb is not None
        assert recovered.colour_rgb == pytest.approx((0.2, 0.4, 0.9), abs=1e-6)
        assert "Gen" not in recovered.colour_types
        assert set(recovered.colour_types) == {"Surf", "Curv"}

    def test_the_colour_is_single_precision_so_an_exact_comparison_would_fail(
        self, written
    ) -> None:
        """`Quantity_Color` holds floats, not doubles: 0.2 returns 0.20000000298…"""
        _, recovered = written
        assert recovered.colour_rgb != (0.2, 0.4, 0.9)

    def test_the_layer_survives_and_is_reachable_from_the_layers_side(self, written) -> None:
        """`GetLayers(shapeLabel, seq)` returns True with nothing in it.

        The assignment is real and comes back through `GetShapesOfLayer_s`.
        Believing the first accessor records a carried class as lost.
        """
        _, recovered = written
        assert recovered.layers == ("KRYOVA-PART",)

    def test_the_validation_properties_survive_exactly(self, written) -> None:
        _, recovered = written
        assert recovered.properties is not None
        assert recovered.properties.volume_mm3 == 6000.0
        assert recovered.properties.area_mm2 == 2200.0
        assert recovered.properties.centroid_mm == (5.0, 10.0, 15.0)

    def test_the_assembly_occurrences_survive_with_their_names(self, written) -> None:
        _, recovered = written
        assert recovered.occurrences == ("Leg.1", "Leg.2")
        assert recovered.entity_counts["NEXT_ASSEMBLY_USAGE_OCCURRENCE"] == 2

    def test_the_solid_comes_back_with_the_metadata_in_one_trip(self, written) -> None:
        """So a matrix can measure geometry and metadata on the same file rather
        than on two files that were never the same file."""
        from app.kernel.occt.metrology import volume_mm3

        _, recovered = written
        assert recovered.shape is not None
        assert volume_mm3(recovered.shape) == pytest.approx(6000.0, rel=1e-9)


class TestTheEntityNamesInTheFile:
    def test_a_tolerance_lands_as_the_concrete_subtype_not_as_geometric_tolerance(
        self, written
    ) -> None:
        """AP242 writes `FLATNESS_TOLERANCE`. The literal string
        `GEOMETRIC_TOLERANCE` is in a correct file **zero** times, so a probe
        grepping for it concludes OCCT writes no PMI — which is exactly what the
        first pass at E21 task 1 concluded."""
        _, recovered = written
        assert recovered.entity_counts["FLATNESS_TOLERANCE"] == 1
        assert recovered.entity_counts["GEOMETRIC_TOLERANCE"] == 0

    def test_validation_properties_need_the_attributes_not_just_the_mode(
        self, block, tmp_path
    ) -> None:
        """`SetPropsMode(True)` is a permission, not a calculation.

        Verified by leaving the attributes off with the mode still on: the file
        gets no `PROPERTY_DEFINITION` at all, the write succeeds, and nothing
        anywhere says the properties were not written.
        """
        target = tmp_path / "no-props.step"
        write_step_with_metadata(block, target, Annotations(name="Bare"))
        recovered = read_step_with_metadata(target)
        assert recovered.entity_counts["PROPERTY_DEFINITION"] == 0
        assert recovered.entity_counts["VOLUME_MEASURE"] == 0
        assert recovered.properties is None


class TestTheToleranceValueIsWrongAndThatIsMeasured:
    def test_a_tolerance_does_not_survive_its_own_round_trip(self, written) -> None:
        """The finding, and the tripwire on it.

        A value written and read by one build, through that build's own writer
        and reader, comes back multiplied by a thousand. **A failure here is not
        a regression** — it is OCCT having fixed the unit it tags the measure
        with, and the response is to widen what the product may claim about
        semantic PMI, in a commit that says so.
        """
        _, recovered = written
        assert recovered.tolerance is not None
        assert recovered.tolerance.kind == "flatness"
        assert recovered.tolerance.value_mm == 0.05 * SEMANTIC_PMI_IS_WRONG_BY

    def test_the_module_re_measures_the_defect_rather_than_reciting_it(self, tmp_path) -> None:
        intact, evidence = semantic_pmi_survives_a_round_trip(tmp_path)
        assert intact is False
        assert "×1000" in evidence
        assert ".METRE." in evidence and ".MILLI." in evidence

    def test_nothing_compensates_for_it_on_the_way_out(self, block, tmp_path) -> None:
        """Scaling by 1/1000 here would make the round trip look perfect and put
        a number in the file that no part of this codebase believes. The units
        rule (CLAUDE.md) is why the defect is reported instead."""
        target = tmp_path / "uncompensated.step"
        write_step_with_metadata(block, target, Annotations(tolerance=Tolerance(value_mm=0.05)))
        text = target.read_text(encoding="utf-8", errors="replace")
        assert "LENGTH_MEASURE(5.E-02)" in text
        assert "SI_UNIT($,.METRE.)" in text.replace(" ", "")


class TestWhatThisPathRefuses:
    def test_a_tolerance_under_ap214_is_refused_rather_than_dropped(
        self, block, tmp_path
    ) -> None:
        """OCCT accepts the request, returns `RetDone` and writes no tolerance.

        Verified by breaking what it guards: with the refusal removed the file
        writes cleanly and contains zero `FLATNESS_TOLERANCE` entities, so a
        caller who asked for a tolerance has a clean success and no tolerance.
        """
        with pytest.raises(ExportError, match="cannot be written to AP214"):
            write_step_with_metadata(
                block,
                tmp_path / "ap214.step",
                Annotations(tolerance=Tolerance(value_mm=0.05)),
                schema=StepSchema.AP214,
            )

    def test_a_schema_with_no_tolerance_asked_for_is_allowed(self, block, tmp_path) -> None:
        """The refusal is about the tolerance, not about AP214 — over-refusal is
        its own failure mode."""
        export = write_step_with_metadata(
            block, tmp_path / "ap214.step", Annotations(name="Plain"), schema=StepSchema.AP214
        )
        assert export.schema is StepSchema.AP214

    def test_an_empty_document_is_refused(self, tmp_path) -> None:
        with pytest.raises(ExportError, match="nothing to export"):
            write_step_with_metadata(None, tmp_path / "empty.step", Annotations())

    def test_a_path_with_the_wrong_extension_is_refused(self, block, tmp_path) -> None:
        with pytest.raises(ExportError, match=r"\.step or \.stp"):
            write_step_with_metadata(block, tmp_path / "part.iges", Annotations())

    def test_reading_a_file_that_is_not_there_is_refused_by_name(self, tmp_path) -> None:
        with pytest.raises(ExportError, match="no STEP file at"):
            read_step_with_metadata(tmp_path / "absent.step")


class TestTheReadsCannotTakeTheProcessDown:
    def test_a_file_with_no_metadata_reads_back_as_nothing_rather_than_crashing(
        self, block, tmp_path
    ) -> None:
        """`_attribute`'s entire coverage, and it fails by segfault.

        `TDF_Label.FindAttribute(id, attr)` does not return `False` for an
        absent attribute — it ends the process. Verified by removing the
        `IsAttribute` guard and watching the whole run die with no assertion and
        no traceback. If this file ever appears to *hang* rather than fail, that
        guard is what to look at first.
        """
        target = tmp_path / "bare.step"
        write_step_with_metadata(block, target, Annotations())
        recovered = read_step_with_metadata(target)
        assert recovered.properties is None
        assert recovered.tolerance is None
        assert recovered.layers == ()
        assert recovered.occurrences == ()

    def test_a_name_nobody_wrote_comes_back_as_the_word_solid(self, block, tmp_path) -> None:
        """The reader invents an identity where none was authored.

        Nothing named this part, and `STEPCAFControl_Reader` hands back
        `'SOLID'` — the shape type, standing in for a name. It matters because
        of how a matrix reads: a `part_names` row measured on a file where no
        name was written comes back non-empty and scores as **carried**. Any row
        in `interop.py` therefore compares against what was *authored* rather
        than checking that something arrived.
        """
        target = tmp_path / "unnamed.step"
        write_step_with_metadata(block, target, Annotations())
        assert read_step_with_metadata(target).name == "SOLID"


def test_the_written_file_reports_itself_honestly(written) -> None:
    export, _ = written
    assert export.schema is StepSchema.AP242
    assert export.unit == "mm"
    assert export.size_bytes == Path(export.path).stat().st_size > 0
