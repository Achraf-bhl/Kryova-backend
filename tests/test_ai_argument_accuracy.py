"""Selection and argument accuracy, per turn, each over its own denominator (E22.4).

The choosers are scripts, so every outcome is known before the harness runs; the registry is
three fixture tools so the offer is predictable. Offline: no database, no model.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ai.argument_accuracy import (
    AccuracyError,
    Call,
    Expected,
    OfferedTool,
    TurnCase,
    matches,
    measure,
    measure_turn,
    model_chooser,
)
from app.ai.provider import AssistantTurn, ToolCall


@dataclass(frozen=True)
class FixtureTool:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


TOOLS = [
    FixtureTool("catia_pad", "Extrude a profile"),
    FixtureTool("catia_hole", "Drill a hole"),
    FixtureTool("catia_shell", "Hollow the body"),
]

HOLE = TurnCase(
    "hole",
    "drill a hole of 8 mm diameter",
    "catia_hole",
    {"diameter_mm": Expected(8.0, abs_tol=0.01), "through": Expected(True)},
)


def _always(call: Call | None):  # type: ignore[no-untyped-def]
    def choose(message: str, offered: Sequence[OfferedTool], context: str) -> Call | None:
        return call

    return choose


class TestComparingValues:
    @pytest.mark.parametrize(
        ("got", "expected", "tol", "ok"),
        [
            (8.0, 8.0, 0.0, True),
            (8.005, 8.0, 0.01, True),
            (8.02, 8.0, 0.01, False),
            ("8", 8.0, 0.01, False),
            (True, 1, 0.0, False),
            (1, True, 0.0, False),
            ("XY", "XY", 0.0, True),
            ([1.0, 2.004], [1.0, 2.0], 0.01, True),
            ([1.0], [1.0, 2.0], 0.01, False),
            ({"x": 1.001}, {"x": 1.0}, 0.01, True),
            ({"x": 1.0, "y": 0}, {"x": 1.0}, 0.01, False),
            (float("nan"), 8.0, 1e9, False),
        ],
    )
    def test_matches(self, got: Any, expected: Any, tol: float, ok: bool) -> None:
        assert matches(got, expected, tol) is ok


class TestOneTurn:
    def test_the_right_tool_with_the_right_arguments(self) -> None:
        call = Call("catia_hole", {"diameter_mm": 8.0, "through": True, "name": "H1"})
        result = measure_turn(HOLE, TOOLS, _always(call), limit=2)
        assert result.offered and result.selected and result.arguments_exact
        assert result.right_arguments == ("diameter_mm", "through")
        assert result.extra_arguments == ("name",)

    def test_the_right_tool_with_a_wrong_number_is_named(self) -> None:
        call = Call("catia_hole", {"diameter_mm": 6.0, "through": True})
        result = measure_turn(HOLE, TOOLS, _always(call), limit=2)
        assert result.selected and not result.arguments_exact
        assert result.wrong_arguments == ("diameter_mm",)

    def test_a_missing_argument_is_not_a_right_one(self) -> None:
        result = measure_turn(HOLE, TOOLS, _always(Call("catia_hole", {"diameter_mm": 8.0})), limit=2)
        assert result.missing_arguments == ("through",)
        assert not result.arguments_exact

    def test_the_wrong_tool_scores_no_arguments(self) -> None:
        result = measure_turn(HOLE, TOOLS, _always(Call("catia_pad", {"diameter_mm": 8.0})), limit=2)
        assert not result.selected and not result.arguments_exact
        assert result.called == "catia_pad"

    def test_no_call_at_all(self) -> None:
        result = measure_turn(HOLE, TOOLS, _always(None), limit=2)
        assert result.called is None and not result.selected

    def test_a_tool_the_offer_dropped_is_a_retrieval_miss_even_when_the_model_calls_it(self) -> None:
        case = TurnCase("shell", "drill a hole of 8 mm", "catia_shell", {"thickness_mm": Expected(2.0)})
        seen: list[set[str]] = []

        def chooser(message: str, offered: Sequence[OfferedTool], context: str) -> Call:
            seen.append({tool.name for tool in offered})
            return Call("catia_shell", {"thickness_mm": 2.0})

        result = measure_turn(case, TOOLS, chooser, limit=2)
        assert "catia_shell" not in seen[0]
        assert not result.offered
        assert result.selected and result.arguments_exact

    def test_a_gold_tool_outside_the_registry_is_refused(self) -> None:
        case = TurnCase("x", "anything", "catia_loft", {"a": Expected(1)})
        with pytest.raises(AccuracyError, match="not in the registry"):
            measure_turn(case, TOOLS, _always(None))


class TestTheReport:
    def test_each_rate_has_its_own_denominator(self) -> None:
        cases = [
            HOLE,
            TurnCase("hole-wrong", "drill a hole of 6 mm", "catia_hole", {"diameter_mm": Expected(6.0)}),
            TurnCase("pad", "extrude the profile 10 mm", "catia_pad", {"length_mm": Expected(10.0)}),
            TurnCase("shell", "drill a hole of 8 mm", "catia_shell", {"thickness_mm": Expected(2.0)}),
        ]
        script = {
            "hole": Call("catia_hole", {"diameter_mm": 8.0, "through": True}),  # exact
            "hole-wrong": Call("catia_hole", {"diameter_mm": 8.0}),  # right tool, wrong value
            "pad": None,  # no call
            "shell": Call("catia_hole", {}),  # not offered, wrong tool
        }
        messages = {case.message: case.name for case in cases}
        # two cases share a message, so the script keys on the case through the gold tool
        order = iter(case.name for case in cases)

        def chooser(message: str, offered: Sequence[OfferedTool], context: str) -> Call | None:
            assert message in messages
            return script[next(order)]

        report = measure(cases, TOOLS, chooser, chooser_name="scripted", limit=2)
        assert report.selection_accuracy == pytest.approx(2 / 4)
        assert report.argument_accuracy_given_tool == pytest.approx(1 / 2)
        assert report.end_to_end == pytest.approx(1 / 4)
        assert report.offer_recall == pytest.approx(3 / 4)
        assert report.selection_given_offered == pytest.approx(2 / 3)
        assert report.to_dict()["chooser"] == "scripted"

    def test_argument_accuracy_with_no_right_tool_is_unmeasured(self) -> None:
        report = measure([HOLE], TOOLS, _always(None), chooser_name="silent", limit=2)
        assert report.argument_accuracy_given_tool is None
        assert report.end_to_end == 0.0

    def test_the_digest_moves_with_a_gold_value(self) -> None:
        first = measure([HOLE], TOOLS, _always(None), chooser_name="x", limit=2)
        other = TurnCase(HOLE.name, HOLE.message, HOLE.gold_tool, {"diameter_mm": Expected(9.0)})
        assert measure([other], TOOLS, _always(None), chooser_name="x", limit=2).case_set_digest != first.case_set_digest


class TestWhatIsRefused:
    def test_a_case_with_no_gold_arguments(self) -> None:
        with pytest.raises(AccuracyError, match="gold arguments"):
            TurnCase("x", "drill a hole", "catia_hole", {})

    def test_a_negative_tolerance(self) -> None:
        with pytest.raises(AccuracyError, match="not a tolerance"):
            Expected(1.0, abs_tol=-0.1)

    def test_an_unnamed_chooser(self) -> None:
        with pytest.raises(AccuracyError, match="Name the chooser"):
            measure([HOLE], TOOLS, _always(None), chooser_name=" ")

    def test_two_cases_with_one_name(self) -> None:
        with pytest.raises(AccuracyError, match="share a name"):
            measure([HOLE, HOLE], TOOLS, _always(None), chooser_name="x")


class TestAModelAsTheChooser:
    class _Provider:
        name = "canned"
        model = "canned-1"

        def __init__(self, turn: AssistantTurn) -> None:
            self.turn = turn
            self.tools: list[list[str]] = []

        def chat(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> AssistantTurn:
            self.tools.append([tool["function"]["name"] for tool in tools])
            return self.turn

    def test_the_first_tool_call_is_the_choice_and_only_the_offer_is_sent(self) -> None:
        provider = self._Provider(
            AssistantTurn(tool_calls=[ToolCall("1", "catia_hole", {"diameter_mm": 8.0, "through": True})])
        )
        chooser = model_chooser(provider, max_tokens=500)  # type: ignore[arg-type]
        result = measure_turn(HOLE, TOOLS, chooser, limit=2)
        assert result.arguments_exact
        assert "catia_shell" not in provider.tools[0]

    def test_a_turn_with_no_tool_call_is_no_choice(self) -> None:
        chooser = model_chooser(self._Provider(AssistantTurn(text="I would drill it.")), max_tokens=500)  # type: ignore[arg-type]
        assert measure_turn(HOLE, TOOLS, chooser, limit=2).called is None
