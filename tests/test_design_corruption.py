"""Silent corruption on edits: what the harness counts, and what it refuses to count (E22.2).

Every editor here is a script, not a model, so each outcome is known before the harness runs.
Offline: no database, no kernel.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import pytest

from app.design.corruption import (
    MATERIAL,
    CorruptionError,
    EditCase,
    EditOutcome,
    measure,
    measure_case,
)
from app.design.spec import DesignSpec, FeatureSpec, expr
from tests.test_design_compile import bracket

THICKER = EditCase("thicker", bracket(), "Make the plate 10 mm thick.", bracket().set_parameter("thick_mm", 10.0))


def _material(spec: DesignSpec, material: str) -> DesignSpec:
    return DesignSpec(
        name=spec.name, parameters=spec.parameters, features=spec.features, material=material
    )


class TestWhatAnEditIsAllowedToMove:
    def test_the_targets_come_from_the_reference_including_formula_consequences(self) -> None:
        result = measure_case(THICKER, lambda spec, _: spec.set_parameter("thick_mm", 10.0))
        # the pad reads thick_mm; the fillet radius is thick_mm / 2.
        assert {"plate.body", "plate.edges"} <= set(result.targeted)
        assert "plate.outline" not in result.targeted

    def test_a_feature_that_starts_being_built_is_a_target_when_the_reference_builds_it(self) -> None:
        case = EditCase("lighten", bracket(), "Make it 12 mm.", bracket().set_parameter("thick_mm", 12.0))
        result = measure_case(case, lambda spec, _: spec.set_parameter("thick_mm", 12.0))
        assert "plate.window" in result.targeted
        assert result.outcome is EditOutcome.EXACT


class TestTheOutcomes:
    def test_the_reference_edit_is_exact(self) -> None:
        result = measure_case(THICKER, lambda spec, _: spec.set_parameter("thick_mm", 10.0))
        assert result.outcome is EditOutcome.EXACT
        assert result.corrupted == () and result.missed == ()

    def test_the_right_feature_to_the_wrong_value_is_clean(self) -> None:
        result = measure_case(THICKER, lambda spec, _: spec.set_parameter("thick_mm", 11.0))
        assert result.outcome is EditOutcome.CLEAN

    def test_moving_a_feature_the_instruction_did_not_target_is_corrupting_and_named(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            return spec.set_parameter("thick_mm", 10.0).set_parameter("width_mm", 130.0)

        result = measure_case(THICKER, editor)
        assert result.outcome is EditOutcome.CORRUPTING
        assert result.corrupted == ("plate.outline",)

    def test_corruption_without_the_target_still_counts_and_names_what_was_missed(self) -> None:
        result = measure_case(THICKER, lambda spec, _: spec.set_parameter("width_mm", 130.0))
        assert result.outcome is EditOutcome.CORRUPTING
        assert "plate.body" in result.missed

    def test_an_unrequested_material_change_is_corruption_of_the_whole_part(self) -> None:
        result = measure_case(
            THICKER, lambda spec, _: _material(spec.set_parameter("thick_mm", 10.0), "steel-1018")
        )
        assert result.outcome is EditOutcome.CORRUPTING
        assert result.corrupted == (MATERIAL,)

    def test_a_removed_feature_the_instruction_did_not_target_is_corruption(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            thick = spec.set_parameter("thick_mm", 10.0)
            return thick.with_features([f for f in thick.features if f.name != "plate.window"])

        # plate.window is suppressed at 10 mm in both, so removing it builds the same calls,
        # but it is a feature of the design the instruction never mentioned.
        result = measure_case(THICKER, editor)
        assert "plate.window" in result.corrupted

    def test_returning_the_design_unchanged_is_no_change_not_success(self) -> None:
        result = measure_case(THICKER, lambda spec, _: spec)
        assert result.outcome is EditOutcome.NO_CHANGE
        assert not result.outcome.nominal_success
        assert set(result.missed) == set(result.targeted)

    def test_rewriting_only_a_note_is_no_change(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            return spec.with_features(
                [FeatureSpec(f.name, f.op, f.args, when=f.when, note="reworded") for f in spec.features]
            )

        assert measure_case(THICKER, editor).outcome is EditOutcome.NO_CHANGE

    def test_a_formula_rewritten_as_its_own_value_is_not_corruption(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            thick = spec.set_parameter("thick_mm", 10.0)
            features = []
            for feature in thick.features:
                if feature.name == "plate.outline":
                    args = dict(feature.args)
                    args["width_mm"] = 120.0
                    feature = FeatureSpec(feature.name, feature.op, args, when=feature.when)
                features.append(feature)
            return thick.with_features(features)

        result = measure_case(THICKER, editor)
        assert result.outcome is EditOutcome.EXACT, result

    def test_an_editor_that_raises_failed(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            raise RuntimeError("model timed out")

        result = measure_case(THICKER, editor)
        assert result.outcome is EditOutcome.FAILED
        assert "model timed out" in result.detail

    def test_an_edit_that_does_not_compile_failed(self) -> None:
        def editor(spec: DesignSpec, _: str) -> DesignSpec:
            broken = [
                FeatureSpec(f.name, f.op, {**dict(f.args), "length_mm": expr("no_such_mm")}, when=f.when)
                if f.name == "plate.body"
                else f
                for f in spec.features
            ]
            return spec.with_features(broken)

        assert measure_case(THICKER, editor).outcome is EditOutcome.FAILED


class TestTheReport:
    def _cases(self) -> list[EditCase]:
        wider = EditCase("wider", bracket(), "Make it 150 mm wide.", bracket().set_parameter("width_mm", 150.0))
        deeper = EditCase("deeper", bracket(), "Make it 90 mm deep.", bracket().set_parameter("depth_mm", 90.0))
        return [THICKER, wider, deeper]

    def test_the_rate_is_corrupting_over_nominal_successes(self) -> None:
        def editor(spec: DesignSpec, instruction: str) -> DesignSpec:
            if "thick" in instruction:
                return spec.set_parameter("thick_mm", 10.0)  # exact
            if "wide" in instruction:
                return spec.set_parameter("width_mm", 150.0).set_parameter("thick_mm", 9.0)  # corrupting
            raise RuntimeError("gave up")  # failed

        report = measure(self._cases(), editor, editor_name="scripted fixture")
        assert report.counts == {"failed": 1, "no_change": 0, "exact": 1, "clean": 0, "corrupting": 1}
        assert report.nominal_successes == 2
        assert report.corruption_rate == pytest.approx(0.5)
        assert report.exact_rate == pytest.approx(1 / 3)
        assert report.to_dict()["editor"] == "scripted fixture"

    def test_no_nominal_success_is_unmeasured_not_zero(self) -> None:
        report = measure(self._cases(), lambda spec, _: spec, editor_name="does nothing")
        assert report.corruption_rate is None
        assert report.exact_rate == 0.0

    def test_the_case_set_digest_moves_when_a_reference_does(self) -> None:
        editor = lambda spec, _: spec  # noqa: E731
        first = measure([THICKER], editor, editor_name="x")
        other = EditCase("thicker", bracket(), THICKER.instruction, bracket().set_parameter("thick_mm", 9.0))
        assert measure([other], editor, editor_name="x").case_set_digest != first.case_set_digest


class TestWhatIsRefused:
    def test_a_reference_that_changes_nothing(self) -> None:
        with pytest.raises(CorruptionError, match="builds the same part"):
            EditCase("none", bracket(), "Do nothing.", bracket())

    def test_a_case_with_no_instruction(self) -> None:
        with pytest.raises(CorruptionError, match="instruction"):
            EditCase("blank", bracket(), " ", bracket().set_parameter("thick_mm", 10.0))

    def test_an_unnamed_editor(self) -> None:
        with pytest.raises(CorruptionError, match="Name the editor"):
            measure([THICKER], lambda spec, _: spec, editor_name="")

    def test_two_cases_with_one_name(self) -> None:
        with pytest.raises(CorruptionError, match="share a name"):
            measure([THICKER, THICKER], lambda spec, _: spec, editor_name="x")


class TestAModelAsTheEditor:
    """`app.ai.design_editor` with a provider that replays a canned reply."""

    class _Provider:
        name = "canned"
        model = "canned-1"

        def __init__(self, reply: str) -> None:
            self.reply = reply
            self.users: list[str] = []

        def complete(self, *, system: str, user: str, schema, effort: str, max_tokens: int):  # type: ignore[no-untyped-def]
            from app.ai.provider import Completion

            self.users.append(user)
            return Completion(value=schema(spec_json=self.reply))

    def test_a_faithful_reply_is_exact_and_the_spec_and_instruction_were_sent(self) -> None:
        import json

        from app.ai.design_editor import model_editor

        reference = bracket().set_parameter("thick_mm", 10.0)
        provider = self._Provider(json.dumps(reference.to_dict()))
        editor = model_editor(provider, effort="high", max_tokens=4000)  # type: ignore[arg-type]
        assert measure_case(THICKER, editor).outcome is EditOutcome.EXACT
        assert "Make the plate 10 mm thick." in provider.users[0]
        assert '"thick_mm"' in provider.users[0]

    def test_a_reply_that_is_not_json_is_a_failed_edit(self) -> None:
        from app.ai.design_editor import model_editor

        editor = model_editor(self._Provider("Sure! Here it is."), effort="high", max_tokens=10)  # type: ignore[arg-type]
        result = measure_case(THICKER, editor)
        assert result.outcome is EditOutcome.FAILED
        assert "not JSON" in result.detail
