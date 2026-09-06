"""The model sketches a load case in words; Python builds the solver's shape.

Measured on ladder prompt H4, 2026-09-06, run 8. The bracket was built, the
STEP exported, and `draft_load_case` -- the tool added after run 4 so the agent
would stop hand-writing load cases -- was called three times:

    Working out the loads -- LLMError: Ollama returned an empty response.   146,768 ms
    Working out the loads -- ... does not match the expected schema:
        load_case.loads.0.force.where Field required                        145,988 ms
    Working out the loads -- ... fixtures.0.where Unable to extract tag
        using discriminator 'type'                                            38,269 ms

Five and a half minutes, three of twenty rounds, and no load case. The cause
was the schema the tool handed the model: `LoadCaseDraft` wraps the solver's
own `LoadCase`, which is two discriminated unions, fourteen definitions and
14,445 characters of JSON Schema. A 9B model decoding against that grammar
walks it for thousands of tokens and, when Ollama cannot compile it fully,
falls back to free JSON and guesses the shape.

The fix is not a better prompt. It is the split this file tests: the model
fills a `LoadCaseSketch` -- six face words, a material slug, numbers -- and
`realise` builds the `LoadCase` deterministically. Nothing the model can say
produces a shape the solver rejects.

Offline: no model, no database, no CATIA.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.load_case_sketch import FACE_SELECTORS, SketchProblem, face_selector, realise
from app.ai.providers._json_schema import (
    LOCAL_SCHEMA_BUDGET_CHARS,
    object_unions,
    schema_characters,
)
from app.ai.schemas import AppliedLoad, LoadCaseDraft, LoadCaseSketch, Support
from app.solve.materials import MATERIALS
from app.solve.types import (
    FaceSelector,
    ForceLoad,
    GravityLoad,
    LoadCase,
    PressureLoad,
)


def _sketch(**overrides: object) -> LoadCaseSketch:
    base: dict[str, object] = {
        "name": "Tip load",
        "material": "steel-1018",
        "supports": [Support(face="left")],
        "loads": [AppliedLoad(face="right", force_n=[0, 0, -500])],
        "assumptions": ["Mild steel read as steel-1018."],
        "unresolved": [],
    }
    base.update(overrides)
    return LoadCaseSketch(**base)  # type: ignore[arg-type]


class TestTheSchemaIsSmall:
    """The property that makes it decodable at all, pinned as numbers."""

    def test_it_is_within_the_local_budget(self) -> None:
        assert schema_characters(LoadCaseSketch.model_json_schema()) <= LOCAL_SCHEMA_BUDGET_CHARS

    def test_it_has_no_choice_between_object_shapes(self) -> None:
        assert object_unions(LoadCaseSketch.model_json_schema()) == 0

    def test_the_solvers_own_type_would_fail_both(self) -> None:
        """What was being sent before. If this ever passes, the budget has
        been loosened until it guards nothing."""
        schema = LoadCaseDraft.model_json_schema()
        assert schema_characters(schema) > LOCAL_SCHEMA_BUDGET_CHARS
        assert object_unions(schema) > 0

    def test_every_face_word_is_in_the_schema(self) -> None:
        text = json.dumps(LoadCaseSketch.model_json_schema())
        for face in FACE_SELECTORS:
            assert f'"{face}"' in text

    def test_every_library_material_is_a_choice(self) -> None:
        """Built from the library, so a new material joins without an edit here."""
        text = json.dumps(LoadCaseSketch.model_json_schema())
        for slug in MATERIALS:
            assert f'"{slug}"' in text

    def test_a_material_outside_the_library_is_refused_by_the_schema(self) -> None:
        with pytest.raises(ValidationError):
            _sketch(material="unobtainium")


class TestFaces:
    def test_the_six_words_cover_both_ends_of_every_axis(self) -> None:
        pairs = set(FACE_SELECTORS.values())
        assert pairs == {(a, s) for a in ("x", "y", "z") for s in ("min", "max")}

    def test_top_is_plus_z(self) -> None:
        """The prompt says +Z is up; the selector must agree or every 'top'
        load lands on the underside."""
        assert face_selector("top") == FaceSelector(axis="z", side="max")
        assert face_selector("bottom") == FaceSelector(axis="z", side="min")

    def test_right_is_plus_x_and_back_is_plus_y(self) -> None:
        assert face_selector("right") == FaceSelector(axis="x", side="max")
        assert face_selector("back") == FaceSelector(axis="y", side="max")


class TestRealising:
    def test_a_cantilever_becomes_a_valid_load_case(self) -> None:
        draft = realise(_sketch())
        assert isinstance(draft, LoadCaseDraft)
        case = draft.load_case
        # Round-trips through the solver's own validation: the shape is right
        # by construction, and this is the assertion that says so.
        assert LoadCase.model_validate(case.model_dump()) == case
        assert case.material == MATERIALS["steel-1018"]
        assert case.fixtures[0].where == FaceSelector(axis="x", side="min")
        assert case.fixtures[0].kind == "clamp"
        load = case.loads[0]
        assert isinstance(load, ForceLoad)
        assert load.force_n == (0.0, 0.0, -500.0)
        assert load.where == FaceSelector(axis="x", side="max")

    def test_a_pressure_becomes_a_pressure_load(self) -> None:
        draft = realise(_sketch(loads=[AppliedLoad(face="top", pressure_mpa=2.5)]))
        load = draft.load_case.loads[0]
        assert isinstance(load, PressureLoad)
        assert load.pressure_mpa == 2.5

    def test_self_weight_adds_gravity(self) -> None:
        draft = realise(_sketch(self_weight=True))
        assert any(isinstance(load, GravityLoad) for load in draft.load_case.loads)

    def test_a_roller_is_normal_to_its_face(self) -> None:
        draft = realise(_sketch(supports=[Support(face="bottom", kind="roller")]))
        fixture = draft.load_case.fixtures[0]
        assert fixture.kind == "roller"
        assert fixture.normal == "z"
        assert fixture.dofs == ["z"]

    def test_a_symmetry_plane_is_normal_to_its_face(self) -> None:
        draft = realise(_sketch(supports=[Support(face="front", kind="symmetry")]))
        assert draft.load_case.fixtures[0].normal == "y"

    def test_the_face_convention_travels_with_the_draft(self) -> None:
        """The engineer never sees the prompt, so the one choice that decides
        which face is which has to be in the draft's own assumptions."""
        draft = realise(_sketch())
        assert any("+Z up" in line for line in draft.assumptions)
        assert "Mild steel read as steel-1018." in draft.assumptions

    def test_the_sketches_own_caveats_are_kept(self) -> None:
        draft = realise(_sketch(unresolved=["Is the 500 N static or a shock?"]))
        assert "Is the 500 N static or a shock?" in draft.unresolved

    def test_a_blank_name_gets_a_default(self) -> None:
        assert realise(_sketch(name="   ")).load_case.name == "Load case"


class TestALoadWithNoMagnitude:
    """The one thing a valid sketch can say that the solver cannot use."""

    def test_it_is_reported_not_dropped_silently(self) -> None:
        draft = realise(
            _sketch(
                loads=[
                    AppliedLoad(face="right", force_n=[0, 0, -500]),
                    AppliedLoad(face="top"),
                ]
            )
        )
        assert len(draft.load_case.loads) == 1
        assert any("top face has no magnitude" in line for line in draft.unresolved)

    def test_a_zero_vector_is_no_magnitude(self) -> None:
        draft = realise(
            _sketch(
                loads=[
                    AppliedLoad(face="right", force_n=[0, 0, -500]),
                    AppliedLoad(face="top", force_n=[0, 0, 0]),
                ]
            )
        )
        assert len(draft.load_case.loads) == 1

    def test_nothing_left_is_refused_in_words(self) -> None:
        with pytest.raises(SketchProblem, match="newtons"):
            realise(_sketch(loads=[AppliedLoad(face="top")]))

    def test_self_weight_alone_is_a_load(self) -> None:
        """A part under its own weight and nothing else is a real case."""
        draft = realise(_sketch(loads=[AppliedLoad(face="top")], self_weight=True))
        assert len(draft.load_case.loads) == 1


class TestTheServiceUsesTheSketch:
    def test_the_provider_is_asked_for_the_sketch_and_never_the_draft(self) -> None:
        """The draft is what comes *out*; sending it in is the defect."""
        from app.ai.provider import Completion, TokenUsage
        from app.ai.service import draft_load_case

        asked: list[type] = []

        class _Provider:
            def complete(self, *, system, user, schema, effort, max_tokens):
                asked.append(schema)
                return Completion(value=_sketch(), usage=TokenUsage())

        result = draft_load_case(
            _Provider(),  # type: ignore[arg-type]
            description="500 N off the free end",
            bounding_box={"size": [150, 40, 10]},
        )
        assert asked == [LoadCaseSketch]
        assert isinstance(result.value, LoadCaseDraft)

    def test_a_sketch_with_no_load_is_an_llm_error_to_the_caller(self) -> None:
        from app.ai.provider import Completion, LLMError, TokenUsage
        from app.ai.service import draft_load_case

        class _Provider:
            def complete(self, *, system, user, schema, effort, max_tokens):
                return Completion(
                    value=_sketch(loads=[AppliedLoad(face="top")]), usage=TokenUsage()
                )

        with pytest.raises(LLMError, match="magnitude"):
            draft_load_case(
                _Provider(),  # type: ignore[arg-type]
                description="a bracket",
                bounding_box={"size": [1, 1, 1]},
            )


class TestThePromptSpeaksTheSketch:
    """A prompt describing selectors the schema cannot express teaches a
    model to fight its own grammar."""

    def test_the_face_words_are_taught(self) -> None:
        from app.ai.prompts import PARSE_LOAD_CASE_SYSTEM

        for face in FACE_SELECTORS:
            assert f"`{face}`" in PARSE_LOAD_CASE_SYSTEM

    def test_the_old_selector_vocabulary_is_gone(self) -> None:
        from app.ai.prompts import PARSE_LOAD_CASE_SYSTEM

        assert "`box` selector" not in PARSE_LOAD_CASE_SYSTEM
        assert "axis x/y/z, side" not in PARSE_LOAD_CASE_SYSTEM

    def test_the_material_slugs_are_named_as_the_choice(self) -> None:
        from app.ai.prompts import PARSE_LOAD_CASE_SYSTEM

        assert '"mild steel" -> steel-1018' in PARSE_LOAD_CASE_SYSTEM
