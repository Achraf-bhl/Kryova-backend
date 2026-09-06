"""A turn may not close on a requirement nobody measured.

The unit half of master plan 16.2's checking step. `app/ai/planning.py` says
what was asked for; `app/ai/verification.py` says which of it this conversation
actually measured; `tests/test_agent_verification.py` drives the loop that acts
on the answer.

Every number in here was written down on the seat before it was written into a
test. The failure being pinned has appeared four times with the same shape: most
tool calls return `ok`, the model writes a confident closing report, and two
thirds of what the engineer asked for was never measured.
"""

from __future__ import annotations

import pytest

from app.ai.planning import extract_objectives, plan_for
from app.ai.verification import (
    Measurement,
    assess,
    measurements_in,
    shortfall_note,
    unverified,
    unverified_footnote,
)


def _measure(**values: float) -> list[Measurement]:
    """Measurements as a tool would report them, keyed the way results are."""
    return measurements_in(dict(values), "catia_measure")


class TestWhatCountsAsAMeasurement:
    def test_a_key_that_names_its_unit(self) -> None:
        found = measurements_in({"mass_kg": 2.4}, "catia_measure")
        assert found == [Measurement(2.4, "mass", "catia_measure")]

    def test_a_bounding_box_is_three_lengths(self) -> None:
        found = measurements_in({"bounding_box_mm": [60.0, 40.0, 20.0]}, "catia_measure")
        assert [m.value for m in found] == [60.0, 40.0, 20.0]
        assert {m.kind for m in found} == {"length"}

    def test_a_nested_result(self) -> None:
        """A component list carries a mass per component, one level down."""
        found = measurements_in(
            {"components": [{"component": "Shaft", "mass_kg": 0.0589}]}, "catia_analysis"
        )
        assert [m.value for m in found] == [0.0589]

    def test_an_unlabelled_number_is_not_evidence(self) -> None:
        """`count`, `index`, `id` -- a number whose key does not say what it
        measures cannot confirm anything, and guessing is how a requirement gets
        confirmed by a coincidence."""
        assert measurements_in({"count": 4, "index": 25, "name": "Shaft"}, "t") == []

    def test_a_requested_value_is_not_a_measured_one(self) -> None:
        """`clearance_mm` echoed back from the caller's own argument is the
        question, not the answer. Measured on S2: the clash payload repeats what
        it was asked for, and reading that as a measurement would confirm a
        clearance requirement without DMU having found anything."""
        assert measurements_in({"requested_clearance_mm": 5.0}, "catia_assembly_clash") == []
        assert measurements_in({"target_mass_kg": 2.4}, "t") == []

    def test_a_boolean_is_not_a_number(self) -> None:
        assert measurements_in({"clear_mm": True}, "t") == []

    def test_a_cycle_cannot_hang_the_check(self) -> None:
        loop: dict[str, object] = {"mass_kg": 1.0}
        loop["inner"] = loop
        assert len(measurements_in(loop, "t")) >= 1


class TestConfirmingARequirement:
    def test_a_length_that_was_measured(self) -> None:
        objectives = extract_objectives("Make a plate 60 mm long")
        plan = assess(objectives, _measure(bounding_box_mm=60.0))
        assert plan.confirmed and not plan.missed

    def test_a_length_that_was_not_measured_stays_outstanding(self) -> None:
        objectives = extract_objectives("Make a plate 60 mm long")
        plan = assess(objectives, _measure(mass_kg=2.4))
        assert not plan.confirmed and not plan.missed
        assert unverified(plan)

    def test_every_number_in_the_clause_must_match(self) -> None:
        """The measured S2 bushing: bore, outside diameter and length stated,
        and confirming it on the length alone is a bushing nobody checked the
        bore of."""
        objectives = extract_objectives(
            "a bushing with a 25.2 mm bore 45 mm outside diameter 40 mm long"
        )
        partial = assess(objectives, _measure(bounding_box_mm=40.0))
        assert not partial.confirmed
        full = assess(
            objectives,
            measurements_in({"bounding_box_mm": [45.0, 45.0, 40.0], "bore_mm": 25.2}, "m"),
        )
        assert full.confirmed

    def test_a_bare_count_cannot_be_confirmed_by_a_dimension(self) -> None:
        """"four holes" states a four that no length confirms. A 4 mm fillet
        somewhere in the part must not tick it off."""
        objectives = extract_objectives("Drill four holes")
        plan = assess(objectives, _measure(radius_mm=4.0))
        assert not plan.confirmed
        # ...and it is not held against the turn either, since nothing could
        # ever measure it: see `unverified`.
        assert not unverified(plan)


class TestUnitsAreMadeComparableHere:
    """`planning.py` records the engineer's own words and says in as many words
    that making them comparable is the consumer's job. This is that consumer,
    and it is the only place in the codebase that converts."""

    def test_centimetres_against_millimetres(self) -> None:
        plan = assess(extract_objectives("a bar 6 cm long"), _measure(length_mm=60.0))
        assert plan.confirmed

    def test_inches_against_millimetres(self) -> None:
        plan = assess(extract_objectives("a shaft 1 in diameter"), _measure(diameter_mm=25.4))
        assert plan.confirmed

    def test_grams_against_kilograms(self) -> None:
        plan = assess(extract_objectives("it must weigh 500 g"), _measure(mass_kg=0.5))
        assert plan.confirmed


class TestAStatedToleranceDecidesIt:
    def test_the_measured_s1_failure(self) -> None:
        """Ladder prompt S1: "make it 2.4 kg", closed at 2.459 kg described as
        "within 20 grams", which is 59 grams out. With the tolerance read from
        the request, that is a contradiction and is now called one."""
        objectives = extract_objectives("Make it 2.4 kg to within 20 grams")
        plan = assess(objectives, _measure(mass_kg=2.459))
        assert plan.missed
        assert not plan.confirmed

    def test_the_same_mass_inside_the_stated_tolerance(self) -> None:
        objectives = extract_objectives("Make it 2.4 kg to within 20 grams")
        plan = assess(objectives, _measure(mass_kg=2.41))
        assert plan.confirmed and not plan.missed

    def test_a_percentage_tolerance(self) -> None:
        objectives = extract_objectives("a bar 100 mm long to within 2%")
        assert assess(objectives, _measure(length_mm=101.5)).confirmed
        assert not assess(objectives, _measure(length_mm=105.0)).confirmed

    def test_with_no_stated_tolerance_a_small_error_still_passes(self) -> None:
        plan = assess(extract_objectives("a bar 100 mm long"), _measure(length_mm=100.0001))
        assert plan.confirmed


class TestMissedIsClaimedOnlyWhereItIsTrue:
    def test_a_mass_is_contradicted(self) -> None:
        """A part has exactly one mass, so a measured mass outside tolerance is
        wrong and can be said to be wrong."""
        plan = assess(extract_objectives("it must weigh 2.4 kg"), _measure(mass_kg=62.88))
        assert plan.missed

    def test_a_length_that_does_not_appear_is_not_a_contradiction(self) -> None:
        """The part may simply not have been measured that way. Outstanding is
        the honest word, and calling it wrong would send the agent off to fix
        something that may be right."""
        plan = assess(
            extract_objectives("a plate 60 mm long"), _measure(bounding_box_mm=999.0)
        )
        assert not plan.missed
        assert unverified(plan)

    def test_no_mass_measured_at_all_is_not_a_contradiction(self) -> None:
        plan = assess(extract_objectives("it must weigh 2.4 kg"), _measure(length_mm=60.0))
        assert not plan.missed
        assert unverified(plan)


class TestWhatTheModelIsToldBeforeItMayFinish:
    def test_the_clauses_are_named_not_counted(self) -> None:
        """A model told "2 requirements are unverified" asserts they are fine.
        A model handed the sentence goes and measures it."""
        plan = plan_for("Make a shaft 25 mm diameter x 120 long")
        note = shortfall_note(plan)
        assert "25" in note and "120" in note
        assert "Not checked is not the same as fine." in note

    def test_a_measured_contradiction_is_named_separately_and_first(self) -> None:
        plan = assess(
            extract_objectives("Make it 2.4 kg and 300 mm long"), _measure(mass_kg=62.88)
        )
        note = shortfall_note(plan)
        assert note.index("WRONG") < note.index("nothing in this")

    def test_it_forbids_inventing_a_reason_a_tool_failed(self) -> None:
        """Measured on S2 run 6: the clash call failed with an error the agent
        did not understand, so it invented an FEA prerequisite and offered to
        run an analysis. Saying "unverified" has to be an available answer or
        the model will manufacture a better-sounding one."""
        note = shortfall_note(plan_for("check there is no interference within 5 mm"))
        assert "do not invent a reason a tool failed" in note

    def test_nothing_to_say_when_everything_was_measured(self) -> None:
        plan = assess(extract_objectives("a bar 60 mm long"), _measure(length_mm=60.0))
        assert shortfall_note(plan) == ""

    def test_nothing_to_say_when_no_requirement_carries_a_number(self) -> None:
        assert shortfall_note(plan_for("what does 6061 yield at?")) == ""


class TestWhatTheUserIsToldWhenItClosesAnyway:
    def test_the_footnote_names_the_requirements(self) -> None:
        plan = plan_for("Make a shaft 25 mm diameter x 120 long")
        footnote = unverified_footnote(plan)
        assert "Not verified in this turn" in footnote
        assert "nothing measured this" in footnote

    def test_a_contradiction_says_it_does_not_meet_the_requirement(self) -> None:
        plan = assess(extract_objectives("Make it 2.4 kg"), _measure(mass_kg=62.88))
        assert "does not meet the requirement" in unverified_footnote(plan)

    def test_it_says_the_answer_above_may_disagree(self) -> None:
        """The model's own words are left intact, so the footnote has to carry
        the warning that they may claim otherwise."""
        footnote = unverified_footnote(plan_for("a plate 60 mm long"))
        assert "whatever the answer above says" in footnote

    def test_nothing_is_appended_when_everything_was_measured(self) -> None:
        plan = assess(extract_objectives("a bar 60 mm long"), _measure(length_mm=60.0))
        assert unverified_footnote(plan) == ""


class TestOnlyCheckableRequirementsHoldATurnOpen:
    @pytest.mark.parametrize(
        "request_text",
        [
            "add a chamfer on the top edge",
            "make it look tidy",
            "drill four holes",
        ],
    )
    def test_a_clause_with_nothing_to_compare(self, request_text: str) -> None:
        """These are real requirements and they are not arithmetic. Holding a
        turn open for them would mean never closing one; they are what the
        visual check is for."""
        assert unverified(plan_for(request_text)) == []


#: `catia_measure`'s real answer for the PRO4 ram, copied from the operation log
#: on 2026-09-07 rather than written from memory. The shape is the point: the
#: key that declares the unit holds a *dict*, and the keys inside it name
#: corners rather than units.
REAL_MEASURE_RESULT = {
    "mass_kg": 0.444473,
    "has_solid": True,
    "volume_mm3": 56548.6678,
    "approximate": False,
    "density_kg_m3": 7860.0,
    "bounding_box_mm": {
        "max": [10.0, 10.0, 180.0],
        "min": [-10.0, -10.0, 0.0],
        "size": [20.0, 20.0, 180.0],
    },
    "material_applied": True,
    "surface_area_mm2": 11938.0521,
    "mass_is_provisional": False,
    "center_of_gravity_mm": [0.0, 0.0, 90.0],
    "features": [{"name": "Extrusion.1", "type": "Shape"}],
}


class TestAgainstWhatTheSeatActuallyReturns:
    """The false negative measured on PRO4 turn 2, 2026-09-07.

    The ram was built to 20 mm diameter and 180 mm long, measured, and reported
    as "Diameter: 20 mm, Length: 180 mm" -- and the footnote underneath said
    both were unmeasured. Every length in every measurement was being dropped,
    because the key that declares the unit holds a dict and the collector only
    walked scalars and lists. A footnote that cries wolf is worse than none.
    """

    def test_the_lengths_are_found(self) -> None:
        found = measurements_in(REAL_MEASURE_RESULT, "catia_measure")
        lengths = {m.value for m in found if m.kind == "length"}
        assert 20.0 in lengths and 180.0 in lengths

    def test_the_mass_and_volume_are_found(self) -> None:
        found = measurements_in(REAL_MEASURE_RESULT, "catia_measure")
        assert 0.444473 in {m.value for m in found if m.kind == "mass"}
        assert 56548.6678 in {m.value for m in found if m.kind == "volume"}

    def test_the_ram_requirement_confirms(self) -> None:
        objectives = extract_objectives(
            "build just the ram: a 20 mm diameter round bar, 180 mm long"
        )
        plan = assess(objectives, measurements_in(REAL_MEASURE_RESULT, "catia_measure"))
        assert not unverified(plan)
        assert unverified_footnote(plan) == ""

    def test_a_dimension_the_part_does_not_have_is_still_unverified(self) -> None:
        """The fix must not make everything confirm: a 250 mm bar is not this
        one, and a collector that swept up every number would say it was."""
        objectives = extract_objectives("a 250 mm long bar")
        plan = assess(objectives, measurements_in(REAL_MEASURE_RESULT, "catia_measure"))
        assert unverified(plan)

    def test_a_flag_beside_the_numbers_is_not_one_of_them(self) -> None:
        found = measurements_in(REAL_MEASURE_RESULT, "catia_measure")
        assert 1.0 not in {m.value for m in found if m.kind == "mass"}
