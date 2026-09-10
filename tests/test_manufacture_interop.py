"""What the STEP file actually carries — measured, and measured again here.

`test_manufacture_export.py` proves a solid survives the trip. This proves the
*matrix* is honest about everything else, which is the harder claim: a capability
statement is read as evidence, so a row that says `carried` without a measurement
behind it does more damage than no row at all.

**The claim this file exists to stop the product making.** OCCT's
`write.step.schema` takes `AP242DIS` and refuses `AP242IS` — the only AP242 this
build can write is the one named after the Draft International Standard. The
master plan (E17 task 2) called AP242 "the one that carries PMI" and E21 records
why that is a promise the kernel does not currently keep. If a future OCCT starts
accepting the IS spelling, `test_this_build_cannot_write_a_published_ap242_edition`
fails — deliberately — and whoever sees it gets to widen the claim on purpose
rather than discovering years later that it could have been widened.

**Why the metadata rows are `NOT_ATTEMPTED` and never `LOST`.** "We wrote it and
it did not come back" is a defect in OCCT or in STEP. "We never wrote it" is
scope. Recording the second as the first would file a bug against somebody
else's code for work we have not done, and would make the matrix useless as a
list of what to build next.
"""

from __future__ import annotations

import pytest

from app.manufacture.export import StepSchema
from app.manufacture.interop import (
    AP242_SPELLINGS,
    CARRIER,
    METADATA_CARRIER,
    REFERENCE_ANNOTATIONS,
    Carriage,
    EntityClass,
    accepted_schema_values,
    measure_metadata_round_trip,
    measure_round_trip,
)

pytest.importorskip("OCP", reason="the STEP interop matrix is measured on the real kernel")


@pytest.fixture(scope="module")
def block():
    """A plain box. The matrix is about metadata, so the geometry stays boring."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape()


@pytest.fixture(scope="module")
def matrix(block):
    return measure_round_trip(block)


@pytest.fixture(scope="module")
def metadata_matrix(block):
    return measure_metadata_round_trip(block)


class TestWhatThisBuildWillCallItsFile:
    def test_this_build_cannot_write_a_published_ap242_edition(self) -> None:
        """The measurement behind E21 task 1, and the tripwire on it.

        A failure here is not a regression — it is OCCT gaining the spelling, and
        the fix is to widen what the product is allowed to say, in a commit that
        says so.
        """
        accepted = accepted_schema_values()
        assert accepted["AP242DIS"] is True, (
            "this build cannot write AP242 at all, which is a bigger finding than the "
            "one this test was written for"
        )
        assert accepted["AP242IS"] is False
        assert accepted["AP242"] is False

    def test_ap214_is_offered_as_a_published_standard_and_ap242_is_not(self) -> None:
        """The asymmetry is the evidence, and it is why this is not a spelling quirk.

        OCCT knows how to name a published IS — it does it for AP214. That it
        does not do it for AP242 is a statement about the schema it implements.
        """
        assert accepted_schema_values(("AP214IS", "AP242IS"))["AP214IS"] is True
        assert accepted_schema_values(("AP214IS", "AP242IS"))["AP242IS"] is False

    def test_a_nonsense_schema_is_refused_so_a_true_answer_means_something(self) -> None:
        """Verified by breaking what it guards: if `SetCVal_s` accepted anything,
        every `True` above would be worthless and this test is what says so."""
        assert accepted_schema_values(("definitely-not-a-schema",)) == {
            "definitely-not-a-schema": False
        }

    def test_the_file_declares_the_schema_it_was_written_to(self, matrix) -> None:
        assert "AP242" in matrix.file_schema
        assert matrix.claims_an_is_edition is False


class TestWhatCrossesAndWhatWasNeverTried:
    def test_geometry_and_topology_are_carried_with_the_measurement_attached(
        self, matrix
    ) -> None:
        geometry = matrix.row(EntityClass.GEOMETRY)
        assert geometry.carriage is Carriage.CARRIED
        # The evidence is the point: a verdict with no number in it is a claim.
        assert "volume" in geometry.evidence and "mm³" in geometry.evidence
        assert matrix.row(EntityClass.TOPOLOGY).carriage is Carriage.CARRIED

    def test_the_volume_row_reports_the_drift_it_measured(self, matrix) -> None:
        """STEP is an exact B-rep exchange, so the only loss is decimal text
        precision — but the row prints the drift rather than asserting there is
        none, because "identical" is a word and 3.1e-16 is a measurement."""
        assert "relative drift" in matrix.row(EntityClass.GEOMETRY).evidence

    @pytest.mark.parametrize("entity_class", sorted(CARRIER, key=str))
    def test_every_metadata_class_says_not_attempted_and_names_its_carrier(
        self, matrix, entity_class
    ) -> None:
        row = matrix.row(entity_class)
        assert row.carriage is Carriage.NOT_ATTEMPTED
        assert row.owner, f"{entity_class} says nothing about what would carry it"
        assert row.evidence, f"{entity_class} says nothing about why it was not tried"

    def test_nothing_in_this_matrix_is_reported_lost(self, matrix) -> None:
        """A `LOST` row here would mean we wrote something and it vanished. Today
        that cannot happen, because the only things written are the two that are
        checked — and if it ever does, this test is where it surfaces."""
        assert [r.entity_class for r in matrix.rows if r.carriage is Carriage.LOST] == []

    def test_the_holes_are_reachable_without_reading_every_row(self, matrix) -> None:
        """A caller must be able to ask "what did nobody measure?" in one call.

        Without this the matrix is read the way every capability table is read —
        by scanning for red — and a class that is simply absent from the reader's
        attention becomes a class that passed.
        """
        assert set(matrix.unmeasured) == set(CARRIER)
        assert EntityClass.SEMANTIC_PMI in matrix.unmeasured
        assert EntityClass.GEOMETRY not in matrix.unmeasured

    def test_the_matrix_publishes_as_data(self, matrix) -> None:
        published = matrix.to_dict()
        assert published["claims_an_is_edition"] is False
        assert published["schema_requested"] == str(StepSchema.AP242)
        assert {row["class"] for row in published["rows"]} == {
            str(c) for c in EntityClass
        }
        assert set(published["schema_values_accepted"]) == set(AP242_SPELLINGS)


class TestTheXdeMatrixIsADifferentProgramsMatrix:
    """Two writers write STEP here and they carry different things.

    The temptation a published pair of matrices creates is to read the union of
    their green rows as the product's capability. It is true of neither, which
    is why each matrix names its writer and why these tests assert the two
    disagree rather than asserting either one alone.
    """

    def test_each_matrix_names_the_writer_that_produced_it(
        self, matrix, metadata_matrix
    ) -> None:
        assert matrix.writer == "STEPControl_Writer"
        assert metadata_matrix.writer == "STEPCAFControl_Writer"

    def test_the_classes_the_shipped_path_never_tries_are_measured_on_the_xde_path(
        self, matrix, metadata_matrix
    ) -> None:
        """The point of the whole exercise: six classes move from "nobody tried"
        to a measurement."""
        moved = set(matrix.unmeasured) - set(metadata_matrix.unmeasured)
        assert moved == {
            EntityClass.ASSEMBLY_STRUCTURE,
            EntityClass.PART_NAMES,
            EntityClass.COLOURS,
            EntityClass.LAYERS,
            EntityClass.VALIDATION_PROPERTIES,
            EntityClass.SEMANTIC_PMI,
        }

    @pytest.mark.parametrize(
        "entity_class",
        [
            EntityClass.GEOMETRY,
            EntityClass.TOPOLOGY,
            EntityClass.PART_NAMES,
            EntityClass.ASSEMBLY_STRUCTURE,
            EntityClass.COLOURS,
            EntityClass.LAYERS,
            EntityClass.VALIDATION_PROPERTIES,
        ],
    )
    def test_the_classes_that_survive_intact(self, metadata_matrix, entity_class) -> None:
        row = metadata_matrix.row(entity_class)
        assert row.carriage is Carriage.CARRIED, row.evidence
        assert row.evidence

    def test_only_presentation_pmi_and_saved_views_remain_untried(
        self, metadata_matrix
    ) -> None:
        assert set(metadata_matrix.unmeasured) == set(METADATA_CARRIER)
        assert set(METADATA_CARRIER) == {
            EntityClass.PRESENTATION_PMI,
            EntityClass.SAVED_VIEWS,
        }

    def test_the_rows_compare_against_what_was_authored_not_against_what_arrived(
        self, metadata_matrix
    ) -> None:
        """`STEPCAFControl_Reader` hands back the word `SOLID` as the name of a
        part nobody named, so "something came back" scores as carried unless the
        row knows what went out. The authored name is in the evidence for that
        reason."""
        assert REFERENCE_ANNOTATIONS.name is not None
        assert REFERENCE_ANNOTATIONS.name in metadata_matrix.row(EntityClass.PART_NAMES).evidence


class TestTheToleranceRowIsNeitherCarriedNorLost:
    def test_semantic_pmi_comes_back_corrupted_and_says_by_how_much(
        self, metadata_matrix
    ) -> None:
        """The finding E21 task 1 was reopened to establish.

        A failure here is OCCT having fixed the unit it tags the tolerance
        measure with — not a regression. See `app/manufacture/xde.py`.
        """
        row = metadata_matrix.row(EntityClass.SEMANTIC_PMI)
        assert row.carriage is Carriage.CORRUPTED
        assert "×1000" in row.evidence
        assert "0.05 mm" in row.evidence and "50.0 mm" in row.evidence

    def test_a_corrupted_row_is_invisible_to_a_reader_scanning_for_absences(
        self, metadata_matrix
    ) -> None:
        """Which is the whole argument for the verdict existing.

        Nothing is missing from this matrix — no class is `LOST`, and every
        class but two has a measurement behind it. A reader looking for holes
        finds none, and one of the numbers is a thousand times too big.
        """
        assert [r.entity_class for r in metadata_matrix.rows if r.carriage is Carriage.LOST] == []
        assert metadata_matrix.corrupted == (EntityClass.SEMANTIC_PMI,)

    def test_the_corruption_is_reachable_without_reading_every_row(
        self, metadata_matrix
    ) -> None:
        published = metadata_matrix.to_dict()
        assert published["corrupted"] == [str(EntityClass.SEMANTIC_PMI)]
        assert published["writer"] == "STEPCAFControl_Writer"

    def test_the_shipped_path_has_nothing_corrupted_because_it_writes_nothing(
        self, matrix
    ) -> None:
        assert matrix.corrupted == ()


class TestTheMatrixCannotBeReadOptimistically:
    def test_asking_for_a_class_the_matrix_does_not_hold_raises(self, matrix) -> None:
        """Rather than returning a default row, which would read as `carried`
        for a class nobody thought about."""
        with pytest.raises(KeyError):
            matrix.row("saved_view")  # type: ignore[arg-type]

    def test_a_shape_with_no_solid_is_not_attempted_rather_than_carried(self) -> None:
        """The one way `_geometry_row` could lie: two nothings agreeing perfectly.

        A wire has no volume on either side of the trip. A naive relative
        comparison reads `0.0 == 0.0` as a flawless round trip and prints
        `carried` — the same false pass as a zero gradient telling an optimiser
        it has arrived. The geometry row must decline; the topology row still
        measures, because an edge that came back an edge is a real result.
        """
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Pnt

        edge = BRepBuilderAPI_MakeEdge(gp_Pnt(0, 0, 0), gp_Pnt(10, 0, 0)).Edge()
        result = measure_round_trip(edge)
        geometry = result.row(EntityClass.GEOMETRY)
        assert geometry.carriage is Carriage.NOT_ATTEMPTED
        assert "no volume to compare" in geometry.evidence
        assert result.row(EntityClass.TOPOLOGY).carriage is Carriage.CARRIED
        assert EntityClass.GEOMETRY in result.unmeasured
