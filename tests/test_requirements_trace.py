"""Phase 11.3 — traceability, both ways.

The links are read out of the design's own `note` fields, so these tests use
real `DesignSpec` and `Plan` objects rather than a stub: the whole claim is that
nothing new has to be stored, and a test against a hand-made note object would
not check it.

Offline — compiling a spec touches no CATIA and no kernel, which is the property
`app/design/` was built to have.
"""

from __future__ import annotations

from app.design.compile import compile_spec
from app.design.spec import DesignSpec, FeatureSpec
from app.requirements.model import Requirement, RequirementSet, Source, Status
from app.requirements.trace import (
    CITATION_PATTERN,
    FEATURE,
    Note,
    notes_from_assertions,
    notes_from_plan,
    notes_from_spec,
    trace,
)


def requirements() -> RequirementSet:
    top = Requirement(
        id="REQ-001",
        statement="The bracket shall weigh no more than 2.4 kg.",
        measure="mass_kg",
        comparison="<=",
        target=2.4,
        source=Source.CUSTOMER,
        citation="Acme RFQ 2026-03 §2.1",
    )
    stiffness = Requirement(
        id="REQ-014",
        statement="The flange shall not deflect more than 0.5 mm under the panic-brake case.",
        measure="machine.fos.panic_brake.factor_of_safety",
        comparison=">=",
        target=2.0,
        source=Source.DERIVED,
        parents=("REQ-001",),
        rationale="Flowed down from the mass budget: a thinner flange needs the rib.",
    )
    untouched = Requirement(
        id="REQ-020",
        statement="The bracket shall be paintable.",
        needs="no surface-finish model exists (Phase 13)",
    )
    return RequirementSet.of("bracket", [top, stiffness, untouched])


def design() -> DesignSpec:
    return DesignSpec.of(
        "bracket",
        features=[
            FeatureSpec(name="plate.sketch", op="catia_sketch_create", args={"support": "XY"}),
            FeatureSpec(
                name="plate.profile",
                op="catia_sketch_rectangle",
                args={"sketch": "@plate.sketch", "width_mm": 60.0, "height_mm": 40.0},
                note="Envelope from REQ-001.",
            ),
            FeatureSpec(
                name="plate.body",
                op="catia_pad",
                args={"sketch": "@plate.sketch", "length_mm": 20.0},
            ),
            FeatureSpec(
                name="plate.rib",
                op="catia_pad",
                args={"sketch": "@plate.sketch", "length_mm": 4.0},
                note="REQ-014: stiffens the flange under the panic-brake case.",
            ),
        ],
        description="A bracket for REQ-001.",
    )


class TestFromAFeatureBackToTheRequirement:
    def test_a_feature_note_citing_an_id_is_the_trace_link(self) -> None:
        """Nothing new is stored — the note that was already there is the link."""
        report = trace(requirements(), notes_from_spec(design()))
        assert report.requirements_for("plate.rib") == ("REQ-014",)
        assert report.requirements_for("plate.profile") == ("REQ-001",)

    def test_a_feature_with_no_note_is_justified_by_nothing(self) -> None:
        report = trace(requirements(), notes_from_spec(design()))
        assert report.requirements_for("plate.body") == ()

    def test_walking_up_from_the_rib_reaches_the_customer(self) -> None:
        """The literal 'why is this rib here' answer, end to end."""
        found = requirements()
        report = trace(found, notes_from_spec(design()))
        (justifying,) = report.requirements_for("plate.rib")
        ancestors = found.ancestors_of(justifying)
        assert [one.id for one in ancestors] == ["REQ-001"]
        assert ancestors[0].source is Source.CUSTOMER
        assert ancestors[0].citation


class TestFromARequirementToWhatSatisfiesIt:
    def test_evidence_lists_where_it_was_cited(self) -> None:
        report = trace(requirements(), notes_from_spec(design()))
        links = report.evidence_for("REQ-001")
        assert {one.target for one in links} == {"plate.profile", "bracket"}
        assert all(one.kind in (FEATURE, "design") for one in links)

    def test_a_compiled_plan_carries_the_same_rationale_through(self) -> None:
        plan = compile_spec(design())
        report = trace(requirements(), notes_from_plan(plan))
        assert "REQ-014" in report.traced
        assert any(one.kind == "call" for one in report.evidence_for("REQ-014"))

    def test_an_assertion_named_after_a_requirement_traces_itself(self) -> None:
        found = requirements()
        report = trace(found, notes_from_assertions(found.assertions()))
        assert report.requirements_for("REQ-014") == ("REQ-014",)


class TestARequirementNothingCitesIsReported:
    def test_untraced_names_it(self) -> None:
        """Broken to verify: dropped the `untraced` computation and the matrix
        listed only the rows it could fill — which looks complete, and REQ-020
        simply was not there."""
        report = trace(requirements(), notes_from_spec(design()))
        assert report.untraced == ("REQ-020",)
        assert not report
        assert "has not been designed against" in report.summary()

    def test_the_matrix_carries_untraced_rows_as_empty_not_absent(self) -> None:
        matrix = trace(requirements(), notes_from_spec(design())).matrix()
        assert matrix["REQ-020"] == ()
        assert set(matrix) == {"REQ-001", "REQ-014", "REQ-020"}

    def test_a_retired_requirement_is_not_owed_a_justification(self) -> None:
        found = requirements()
        retired = found.with_requirements(
            [
                one
                if one.id != "REQ-020"
                else Requirement.from_dict({**one.to_dict(), "status": "obsolete"})
                for one in found
            ]
        )
        report = trace(retired, notes_from_spec(design()))
        assert "REQ-020" not in report.untraced

    def test_a_note_citing_a_retired_requirement_is_a_link_not_a_dangle(self) -> None:
        found = RequirementSet.of(
            "bracket",
            [
                Requirement(
                    id="REQ-001",
                    statement="Old mass budget.",
                    measure="mass_kg",
                    comparison="<=",
                    target=2.4,
                    status=Status.OBSOLETE,
                )
            ],
        )
        report = trace(found, [Note(kind=FEATURE, target="rib", text="REQ-001 wanted it.")])
        assert report.dangling == ()
        assert report.requirements_for("rib") == ("REQ-001",)


class TestPrecisionIsTheHardHalf:
    def test_a_citation_of_an_id_that_is_not_in_the_set_is_reported(self) -> None:
        """A note left behind by a renamed requirement points at nothing, and the
        next person to read it will believe it. Broken to verify: dropped the
        dangling branch, and the note silently meant nothing at all."""
        report = trace(
            requirements(),
            [Note(kind=FEATURE, target="rib", text="Required by REQ-999.")],
        )
        assert [one.citation for one in report.dangling] == ["REQ-999"]
        assert "not in this requirement set" in report.summary()

    def test_ordinary_engineering_prose_produces_no_citations(self) -> None:
        """The whole reason links are matched against real ids rather than a pattern."""
        prose = (
            "Units are mm-N-MPa; a well-formed V5R21 part with an M6 bolt to ISO 898-1, "
            "compliant with 2006/42/EC, meshed as tet4."
        )
        report = trace(requirements(), [Note(kind=FEATURE, target="rib", text=prose)])
        assert report.dangling == ()
        assert report.links == ()

    def test_the_citation_pattern_itself_does_not_fire_on_that_prose(self) -> None:
        prose = "mm-N-MPa well-formed V5R21 ISO 898-1 tet4 2006/42/EC"
        assert CITATION_PATTERN.findall(prose) == []

    def test_a_shorter_id_does_not_match_inside_a_longer_one(self) -> None:
        """`REQ-1` must not be found inside `REQ-14`."""
        found = RequirementSet.of(
            "x",
            [
                Requirement(id="REQ-1", statement="a", needs="nothing yet"),
                Requirement(id="REQ-14", statement="b", needs="nothing yet"),
            ],
        )
        report = trace(found, [Note(kind=FEATURE, target="rib", text="Because of REQ-14.")])
        assert report.requirements_for("rib") == ("REQ-14",)

    def test_a_link_is_recorded_once_per_target_however_often_it_is_cited(self) -> None:
        report = trace(
            requirements(),
            [
                Note(kind=FEATURE, target="rib", text="REQ-014 again."),
                Note(kind="call", target="rib", text="REQ-014 once more."),
            ],
        )
        assert report.requirements_for("rib") == ("REQ-014",)
        assert report.targets_for("REQ-014") == ("rib",)


class TestAdaptersReadWhatIsAlreadyThere:
    def test_an_empty_note_is_not_a_note(self) -> None:
        spec = DesignSpec.of(
            "x", features=[FeatureSpec(name="a.b", op="catia_pad", args={"length": 1.0})]
        )
        assert notes_from_spec(spec) == ()

    def test_the_designs_own_description_is_a_note(self) -> None:
        found = notes_from_spec(design())
        assert any(one.kind == "design" and "REQ-001" in one.text for one in found)
