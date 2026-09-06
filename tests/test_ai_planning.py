"""What the engineer asked for, held apart from the conversation — 16.2, first step.

Scope note, matching the module's own: `app/ai/planning.py` is a seam and one
step of it. It extracts requirements and tracks whether each has been measured.
It does not sequence tools, choose an order, or replan, and it is not wired into
the agent loop — so there is nothing here about tool calls, and there should not
be until something connects it.

The behaviour these pin is the one the recorded runs lost. 2026-09-06, attempt
3: six stated requirements, three built, and a closing answer of *"11.5 mm,
2.459 kg, within 20 grams of 2.4 kg"* that was 59 grams out. Fourteen of fifteen
calls returned `ok`. Nothing was holding the list.
"""

from __future__ import annotations

import pytest

from app.ai.planning import Objective, Plan, Quantity, extract_objectives, plan_for

RUNG3 = (
    "Design a steel mounting plate: 200 mm by 150 mm, with a 60 mm diameter "
    "bore through the centre and four 12 mm clearance holes, one 20 mm in from "
    "each corner. It has to weigh 2.4 kg. Pick a starting thickness, build it, "
    "measure the mass, and then adjust the thickness until the measured mass is "
    "within 20 grams of 2.4 kg."
)


class TestItFindsWhatWasAskedFor:
    def test_the_rung_three_prompt_yields_its_requirements(self) -> None:
        objectives = extract_objectives(RUNG3)

        texts = [objective.text for objective in objectives]
        assert "with a 60 mm diameter bore through the centre" in texts
        assert "four 12 mm clearance holes" in texts
        assert "It has to weigh 2.4 kg" in texts

    def test_a_count_and_a_dimension_in_one_clause_are_both_kept(self) -> None:
        """"four 12 mm clearance holes" states two numbers, and the count is the
        one that gets silently dropped. Three of the five recorded rung-3 runs
        built the wrong number of holes."""
        objective = next(
            obj for obj in extract_objectives(RUNG3) if obj.text == "four 12 mm clearance holes"
        )

        assert Quantity(4.0, "", "count") in objective.quantities
        assert Quantity(12.0, "mm", "length") in objective.quantities

    def test_a_tolerance_is_recorded_as_a_tolerance(self) -> None:
        """It is the part of a requirement that decides whether it was met, and
        the part a model paraphrasing its own answer drops first."""
        objective = next(
            obj for obj in extract_objectives(RUNG3) if "within 20 grams" in obj.text
        )

        assert objective.tolerance == Quantity(20.0, "grams", "mass")

    def test_a_decimal_is_one_number_and_not_two_requirements(self) -> None:
        """Verified by breaking it: with a bare `.` in the clause splitter this
        prompt produced the objectives "It has to weigh 2" and "4 kg", which are
        two nonsense requirements where the engineer stated one."""
        texts = [objective.text for objective in extract_objectives(RUNG3)]

        assert "It has to weigh 2.4 kg" in texts
        assert "4 kg" not in texts
        assert "It has to weigh 2" not in texts

    def test_a_clause_with_no_number_is_still_a_requirement(self) -> None:
        """"with a chamfer on the top edge" has to be built. It just cannot be
        checked by a number, which is a different thing from not mattering."""
        objectives = extract_objectives("Make a bracket with a chamfer on the top edge")

        assert any("chamfer" in objective.text for objective in objectives)
        assert not all(objective.measurable for objective in objectives)

    def test_pleasantries_are_not_requirements(self) -> None:
        assert extract_objectives("thanks, that all looks great") == ()
        assert extract_objectives("") == ()
        assert extract_objectives("   ") == ()

    def test_the_words_are_the_engineers_own(self) -> None:
        """A paraphrase of a requirement is how a requirement quietly changes,
        and the point of this record is that it can be quoted back."""
        objectives = extract_objectives("Cut a 12 mm slot 40 mm from the left edge")

        assert objectives[0].text == "Cut a 12 mm slot 40 mm from the left edge"

    def test_order_is_the_order_it_was_asked_in(self) -> None:
        objectives = extract_objectives(RUNG3)

        assert [objective.position for objective in objectives] == sorted(
            objective.position for objective in objectives
        )


class TestUnitsAreRecordedAndNeverConverted:
    """The whole codebase is mm-N-MPa and nothing converts. A plan is a verbatim
    record of what was said, so a conversion here would be the worst possible
    place for one."""

    def test_grams_stay_grams_beside_kilograms(self) -> None:
        objectives = extract_objectives("it must weigh 2.4 kg to within 20 grams")

        assert objectives[0].tolerance == Quantity(20.0, "grams", "mass")
        assert Quantity(2.4, "kg", "mass") in objectives[0].quantities

    def test_a_word_after_a_number_is_not_automatically_a_unit(self) -> None:
        """"add 6 ribs" is a count of six ribs, not six in a unit called ribs.

        Inventing the unit is the worse failure of the two: the clause text is
        verbatim, so a human reading the plan sees the word `ribs` regardless,
        and a fabricated unit is a number that looks checkable and is not.
        """
        objectives = extract_objectives("add 6 ribs and make it 12 furlongs long")

        units = {quantity.unit for obj in objectives for quantity in obj.quantities}
        assert units == {""}
        assert "ribs" in objectives[0].text

    def test_a_bare_number_is_a_count_not_a_length(self) -> None:
        objectives = extract_objectives("add 6 ribs")

        assert Quantity(6.0, "", "count") in objectives[0].quantities

    def test_a_designation_is_not_a_quantity(self) -> None:
        """`M8` is a thread designation. Reading the 8 out of it as a number
        would put a phantom requirement in the list."""
        objectives = extract_objectives("four M8 clearance holes")

        assert Quantity(8.0, "", "count") not in objectives[0].quantities


class TestNotCheckedIsNotTheSameAsFine:
    """The rule `assertions.py` applies to an unmeasured assertion, and
    `VisualReview` to a check that could not run, applied to a requirement
    nobody looked at."""

    def test_a_fresh_plan_confirms_nothing(self) -> None:
        plan = plan_for(RUNG3)

        assert plan.confirmed == set()
        assert len(plan.outstanding()) == len(plan)

    def test_confirming_one_leaves_the_rest_outstanding(self) -> None:
        plan = plan_for(RUNG3)

        plan.mark_confirmed(0)

        assert len(plan.outstanding()) == len(plan) - 1

    def test_measured_and_wrong_is_its_own_state(self) -> None:
        """Three states, never two: a requirement measured and found wrong must
        not read the same as one nobody measured."""
        plan = plan_for(RUNG3)

        plan.mark_missed(1)

        assert 1 in plan.missed
        assert 1 not in plan.confirmed
        assert 1 not in {plan.objectives.index(obj) for obj in plan.outstanding()}

    def test_a_verdict_replaces_the_other_one(self) -> None:
        plan = plan_for(RUNG3)

        plan.mark_missed(0)
        plan.mark_confirmed(0)

        assert plan.confirmed == {0}
        assert plan.missed == set()

    def test_an_index_that_is_not_a_requirement_does_nothing(self) -> None:
        """A caller wiring this up must not be able to invent a confirmation."""
        plan = plan_for(RUNG3)

        plan.mark_confirmed(999)
        plan.mark_confirmed(-1)

        assert plan.confirmed == set()

    def test_the_brief_says_what_is_outstanding_rather_than_counting_it(self) -> None:
        plan = plan_for(RUNG3)
        plan.mark_confirmed(0)

        brief = plan.brief()

        assert "four 12 mm clearance holes [not checked]" in brief
        assert "Not checked is not the same as fine" in brief

    def test_the_brief_names_a_wrong_measurement_loudly(self) -> None:
        plan = plan_for(RUNG3)
        plan.mark_missed(2)

        assert "MEASURED AND WRONG" in plan.brief()

    def test_the_brief_is_empty_when_there_is_nothing_to_say(self) -> None:
        assert plan_for("thanks!").brief() == ""

    def test_the_summary_never_reports_unchecked_as_met(self) -> None:
        """Verified by breaking it: a `brief()` that counted anything not marked
        `missed` as satisfied would print "7 of 7" here, on a plan where nothing
        has been measured at all — which is the reading of attempt 3 that the
        product gave and that this exists to make impossible.
        """
        plan = plan_for(RUNG3)

        assert f"0 of {len(plan)} confirmed" in plan.brief()
        assert plan.to_dict()["confirmed"] == 0
        assert plan.to_dict()["outstanding"] == len(plan)


class TestTheRecordIsReadable:
    def test_to_dict_carries_the_state_of_each_requirement(self) -> None:
        plan = plan_for(RUNG3)
        plan.mark_confirmed(0)
        plan.mark_missed(1)

        rows = plan.to_dict()["objectives"]

        assert rows[0]["state"] == "confirmed"
        assert rows[1]["state"] == "missed"
        assert rows[2]["state"] == "outstanding"

    def test_a_quantity_prints_as_the_engineer_wrote_it(self) -> None:
        assert str(Quantity(2.4, "kg", "mass")) == "2.4 kg"
        assert str(Quantity(4.0, "", "count")) == "4"

    def test_an_objective_is_frozen_so_it_stays_quotable(self) -> None:
        objective = Objective(text="a 60 mm bore")

        with pytest.raises(AttributeError):
            objective.text = "a 70 mm bore"  # type: ignore[misc]


class TestItIsNotWiredIn:
    """Recorded as a test rather than only as a docstring, because "the seam
    exists but nothing calls it" is a claim that rots the moment somebody wires
    it up and forgets to say so. When this fails, update the scope note in the
    module docstring and in the board — do not delete the test.

    Half-wiring it would either add a schema to the payload 16.1 is busy
    shrinking, or describe machinery to the model that is not there.
    """

    def test_no_tool_and_no_prompt_mentions_it(self) -> None:
        from app.ai import prompts
        from app.ai.tools import BUILTIN_TOOL_LABELS

        text = "\n".join(
            value
            for name, value in vars(prompts).items()
            if not name.startswith("_") and isinstance(value, str)
        )

        assert "plan_for" not in text
        assert not [name for name in BUILTIN_TOOL_LABELS if "plan" in name]

    def test_nothing_in_the_agent_package_imports_it_yet(self) -> None:
        import pkgutil
        from pathlib import Path

        import app.ai

        root = Path(next(iter(app.ai.__path__)))
        importers = [
            path.name
            for path in root.glob("*.py")
            if path.name != "planning.py" and "ai.planning" in path.read_text(encoding="utf-8")
        ]

        assert not importers, f"{importers} import planning; the scope note is now stale"
        assert pkgutil is not None  # the import is what proves the package resolves


class TestPlanSizeIsBoundedByWhatWasSaid:
    def test_a_long_request_does_not_explode_into_fragments(self) -> None:
        """A plan padded with prose is a plan nobody reads."""
        plan = plan_for(RUNG3)

        assert 4 <= len(plan) <= 12

    def test_a_plan_over_nothing_is_empty_rather_than_a_placeholder(self) -> None:
        plan = Plan()

        assert len(plan) == 0
        assert plan.outstanding() == []
        assert plan.brief() == ""
