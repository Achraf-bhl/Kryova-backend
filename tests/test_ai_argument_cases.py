"""The E22.4 case set over the real registry, and the command that runs it (THE QUEUE D3).

Offline: the registry is built with no session, and the model is a script.

**Written on Linux on 2026-09-15 and not run there as pytest**, at the user's instruction that the
Windows machine runs the tests. The schema check and the offer recall below were run as a
one-off script on that date (every gold argument present; 12/12 offered at limit 40).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from app.ai.argument_accuracy import Call, OfferedTool, measure
from app.ai.argument_cases import introspection_tools, main, registry_cases
from app.ai.tool_retrieval import DEFAULT_LIMIT

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _faithful(message: str, offered: Sequence[OfferedTool], context: str) -> Call:
    case = next(case for case in registry_cases() if case.message == message)
    return Call(case.gold_tool, {name: e.value for name, e in case.gold_arguments.items()})


class TestTheGoldCallsAreCallsTheRegistryAccepts:
    @pytest.mark.parametrize("case", registry_cases(), ids=lambda case: case.name)
    def test_every_gold_argument_is_a_declared_parameter_of_the_right_type(self, case) -> None:  # type: ignore[no-untyped-def]
        tools = {tool.name: tool for tool in introspection_tools()}
        assert case.gold_tool in tools
        properties = tools[case.gold_tool].parameters.get("properties", {})
        for name, expected in case.gold_arguments.items():
            assert name in properties, f"{case.gold_tool} declares no {name}"
            declared = properties[name].get("type")
            kinds = declared if isinstance(declared, list) else [declared]
            value = expected.value
            assert any(
                isinstance(value, _JSON_TYPES[kind]) and not (kind != "boolean" and isinstance(value, bool))
                for kind in kinds
            ), f"{case.gold_tool}.{name}: {value!r} is not {declared}"
            if "enum" in properties[name]:
                assert value in properties[name]["enum"]

    @pytest.mark.parametrize("case", registry_cases(), ids=lambda case: case.name)
    def test_every_required_argument_is_gold_or_given_by_context(self, case) -> None:  # type: ignore[no-untyped-def]
        tools = {tool.name: tool for tool in introspection_tools()}
        required = set(tools[case.gold_tool].parameters.get("required", []))
        assert required <= set(case.gold_arguments), required - set(case.gold_arguments)

    def test_the_names_are_unique(self) -> None:
        names = [case.name for case in registry_cases()]
        assert len(set(names)) == len(names)


class TestTheSetUnderTheDeployedSelector:
    def test_every_gold_tool_is_offered_at_the_default_limit(self) -> None:
        report = measure(registry_cases(), introspection_tools(), _faithful, chooser_name="faithful")
        assert report.limit == DEFAULT_LIMIT
        assert report.offer_recall == 1.0

    def test_a_faithful_chooser_scores_one_everywhere(self) -> None:
        report = measure(registry_cases(), introspection_tools(), _faithful, chooser_name="faithful")
        assert report.selection_accuracy == report.argument_accuracy_given_tool == report.end_to_end == 1.0


class TestTheCommand:
    class _Scripted:
        name = "canned"
        model = "scripted-1"

        def chat(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], max_tokens: int) -> Any:
            from app.ai.provider import AssistantTurn, ToolCall

            text = messages[0]["content"]
            case = next(case for case in registry_cases() if text.endswith(case.message))
            arguments = {name: e.value for name, e in case.gold_arguments.items()}
            return AssistantTurn(tool_calls=[ToolCall("1", case.gold_tool, arguments)])

    def test_it_writes_a_report_naming_the_model_the_limit_and_the_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.ai.providers as providers

        monkeypatch.setattr(providers, "get_provider", lambda: self._Scripted())
        out = tmp_path / "d3.json"
        assert main(["--out", str(out)]) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["end_to_end"] == 1.0
        assert report["turns"] == len(registry_cases())
        assert report["chooser"].startswith(f"canned:scripted-1 limit={DEFAULT_LIMIT} prompt=")
