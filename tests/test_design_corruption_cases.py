"""The E22.2 case set on the mission designs, and the command that runs it (THE QUEUE D2).

Offline: the mission specs compile without a kernel, and the model is a canned provider.

**Written on Linux on 2026-09-15 and not run there as pytest**, at the user's instruction that the
Windows machine runs the tests. The target table below was produced by a one-off script over
`mission_cases()` on that date.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.design.corruption import MATERIAL, EditOutcome, measure, measure_case
from app.design.corruption_cases import mission_cases

TARGET_COUNTS = {
    "m1-thicker": 1,
    "m1-wider": 1,
    "m1-bore": 1,
    "m1-corners": 1,
    "m1-sharp": 1,
    "m1-steel": 1,
    "m6-od": 2,
    "m6-wall": 1,
    "m6-length": 1,
    "m3-thicker": 20,
    "m3-one-gland": 3,
    "m3-taller": 16,
}


def _case(name: str):  # type: ignore[no-untyped-def]
    return next(case for case in mission_cases() if case.name == name)


class TestTheCaseSet:
    def test_the_set_is_the_table(self) -> None:
        assert [case.name for case in mission_cases()] == list(TARGET_COUNTS)

    @pytest.mark.parametrize("name", list(TARGET_COUNTS))
    def test_each_reference_is_exact_against_itself_and_reaches_what_it_should(self, name: str) -> None:
        case = _case(name)
        result = measure_case(case, lambda spec, _: case.reference)
        assert result.outcome is EditOutcome.EXACT
        assert len(result.targeted) == TARGET_COUNTS[name]

    def test_a_formula_consequence_is_a_target(self) -> None:
        targeted = measure_case(_case("m6-od"), lambda spec, _: spec.set_parameter("od_mm", 60.0)).targeted
        assert set(targeted) == {"roller.outline", "roller.bore_outline"}

    def test_the_material_case_targets_the_part_and_nothing_else(self) -> None:
        case = _case("m1-steel")
        assert measure_case(case, lambda spec, _: case.reference).targeted == (MATERIAL,)

    def test_the_set_is_deterministic(self) -> None:
        editor = lambda spec, _: spec  # noqa: E731
        first = measure(mission_cases(), editor, editor_name="x").case_set_digest
        assert measure(mission_cases(), editor, editor_name="x").case_set_digest == first


class TestWhatTheM3CasesExpose:
    """M3's section is written from literals, so its parameter table does not reach it."""

    @pytest.mark.parametrize(("parameter", "value"), [("thickness_mm", 2.0), ("width_mm", 220.0)])
    def test_setting_an_unread_parameter_builds_the_same_cover(self, parameter: str, value: float) -> None:
        case = _case("m3-thicker")
        result = measure_case(case, lambda spec, _: spec.set_parameter(parameter, value))
        assert result.outcome is EditOutcome.NO_CHANGE

    def test_setting_the_height_alone_moves_the_glands_and_misses_the_section(self) -> None:
        result = measure_case(_case("m3-taller"), lambda spec, _: spec.set_parameter("height_mm", 70.0))
        assert result.outcome is EditOutcome.CLEAN
        assert "cover.seg01" in result.missed


class TestTheCommand:
    class _Faithful:
        """Hands back each case's reference, found by its instruction."""

        name = "canned"
        model = "faithful-1"

        def __init__(self) -> None:
            self.by_instruction = {case.instruction: case.reference for case in mission_cases()}

        def complete(self, *, system: str, user: str, schema: Any, effort: str, max_tokens: int) -> Any:
            from app.ai.provider import Completion

            instruction = user.rsplit("Instruction:\n", 1)[1]
            return Completion(value=schema(spec_json=json.dumps(self.by_instruction[instruction].to_dict())))

    def test_it_writes_a_report_naming_the_model_the_effort_and_the_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.ai.providers as providers
        from app.ai.design_editor import EDIT_SYSTEM, main

        monkeypatch.setattr(providers, "get_provider", lambda: self._Faithful())
        out = tmp_path / "d2.json"
        assert main(["--out", str(out), "--effort", "low"]) == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["counts"]["exact"] == len(TARGET_COUNTS)
        assert report["corruption_rate"] == 0.0
        assert report["editor"].startswith("canned:faithful-1 effort=low prompt=")
        import hashlib

        assert hashlib.sha256(EDIT_SYSTEM.encode()).hexdigest()[:12] in report["editor"]
