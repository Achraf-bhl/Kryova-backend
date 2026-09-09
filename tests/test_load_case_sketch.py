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


# ---------------------------------------------------------------------------
# Gate G1, 2026-09-08: the part-relative words, and the checks that would have
# caught the run that failed it.
# ---------------------------------------------------------------------------

#: The beam G1 actually built and got a wrong answer on: 20 x 10 x 200, lying
#: along Z. Every number below is the one from `docs/verification-2026-09-08-G1/`.
G1_BEAM = {"min": [-10.0, -5.0, 0.0], "max": [10.0, 5.0, 200.0], "size": [20.0, 10.0, 200.0]}


class TestTheEndsOfAPartCanBeNamed:
    """`far end` / `near end`, and why six absolute words were not enough.

    At G1 the drafting model was asked to fix one end of a beam and load the
    free end. It answered `left` and `right` — always the X faces — on a beam
    whose long axis was Z, and it had been given the bounding box. Turning a
    box into an axis is a reasoning step, and the fix is to remove the step
    rather than to ask for it more firmly.
    """

    def test_the_ends_follow_the_part_rather_than_the_viewer(self) -> None:
        assert face_selector("far end", G1_BEAM) == FaceSelector(axis="z", side="max")
        assert face_selector("near end", G1_BEAM) == FaceSelector(axis="z", side="min")

    def test_the_same_words_mean_a_different_axis_on_a_different_part(self) -> None:
        """The whole point: the word does not carry a direction, the part does."""
        flat = {"min": [0.0, 0.0, 0.0], "max": [300.0, 40.0, 8.0], "size": [300.0, 40.0, 8.0]}

        assert face_selector("far end", flat) == FaceSelector(axis="x", side="max")
        assert face_selector("far end", G1_BEAM) == FaceSelector(axis="z", side="max")

    def test_an_absolute_word_still_ignores_the_part(self) -> None:
        """The six are the viewer's frame and must not start moving: 'the top
        face' means the top whatever shape the part is."""
        for box in (G1_BEAM, None):
            assert face_selector("top", box) == FaceSelector(axis="z", side="max")
            assert face_selector("left", box) == FaceSelector(axis="x", side="min")

    def test_a_relative_word_with_no_box_is_refused_by_name(self) -> None:
        """Never a silent fallback to an axis. A wrong axis is the exact defect
        these words were added to remove, so guessing one here would reproduce
        it while looking like a feature."""
        with pytest.raises(SketchProblem) as refused:
            face_selector("far end", None)

        message = str(refused.value)
        assert "far end" in message
        assert "longest axis" in message
        assert "bounding box" in message

    def test_a_box_that_is_not_a_shape_is_treated_as_no_box(self) -> None:
        """A zero-size box is what an empty or failed export writes, and every
        ratio computed from it is noise."""
        with pytest.raises(SketchProblem):
            face_selector("far end", {"size": [0.0, 0.0, 0.0]})

    def test_a_box_with_only_corners_still_resolves(self) -> None:
        """`size` is what `geometry.inspect` writes, but a box carrying only
        min and max still describes a part; refusing it would be a refusal
        about bookkeeping rather than about geometry."""
        corners = {"min": [0.0, 0.0, 0.0], "max": [5.0, 400.0, 5.0]}

        assert face_selector("far end", corners) == FaceSelector(axis="y", side="max")

    def test_the_reading_is_recorded_as_an_assumption(self) -> None:
        """It is a choice the engineer did not make, so it travels with the
        draft rather than living in a prompt they never see."""
        draft = realise(
            _sketch(
                supports=[Support(face="near end")],
                loads=[AppliedLoad(face="far end", force_n=[0, -200, 0])],
            ),
            G1_BEAM,
        )

        assert any("longest direction" in a and "z" in a for a in draft.assumptions)

    def test_nothing_is_assumed_about_ends_when_no_end_was_named(self) -> None:
        draft = realise(_sketch(), G1_BEAM)

        assert not any("longest direction" in a for a in draft.assumptions)


class TestADraftedCaseIsCheckedAgainstThePart:
    """The `!` at G1: our own validation let a bad call through.

    The drafted case clamped a long side face and pushed the opposite long side
    face along the beam's own length — two faces 20 mm apart on a 200 mm part —
    and came back with a factor of safety of 1303 and no complaint from
    anything in Kryova. The agent caught it. Kryova did not.
    """

    def _g1_answer(self) -> LoadCaseSketch:
        """Exactly what the model said, from the report."""
        return _sketch(
            supports=[Support(face="left")],
            loads=[AppliedLoad(face="right", force_n=[0, 0, -200])],
        )

    def test_the_g1_case_is_now_reported_as_suspect(self) -> None:
        draft = realise(self._g1_answer(), G1_BEAM)

        assert draft.unresolved, "the run that failed G1 must not come back clean"

    def test_the_warning_carries_the_measurement_that_triggered_it(self) -> None:
        """"This looks wrong" is not actionable; "20 mm apart on a 200 mm part"
        is, and it is checkable by the engineer reading it."""
        (span,) = [u for u in realise(self._g1_answer(), G1_BEAM).unresolved if "apart" in u]

        assert "20 mm" in span
        assert "200 mm" in span

    def test_the_warning_names_the_words_that_would_fix_it(self) -> None:
        """A warning that does not say what to do next is a dead end, and the
        model's next move after one is to guess again."""
        span = " ".join(realise(self._g1_answer(), G1_BEAM).unresolved)

        assert "far end" in span and "near end" in span

    def test_a_correct_cantilever_is_not_warned_about(self) -> None:
        """The check that matters most. A warning on the right answer trains
        everyone to skip the list, and then it catches nothing."""
        draft = realise(
            _sketch(
                supports=[Support(face="near end")],
                loads=[AppliedLoad(face="far end", force_n=[0, -200, 0])],
            ),
            G1_BEAM,
        )

        assert draft.unresolved == []

    def test_an_axial_pull_on_an_end_is_not_warned_about_either(self) -> None:
        draft = realise(
            _sketch(
                supports=[Support(face="near end")],
                loads=[AppliedLoad(face="far end", force_n=[0, 0, 200])],
            ),
            G1_BEAM,
        )

        assert draft.unresolved == []

    def test_a_short_span_on_a_stubby_part_is_not_warned_about(self) -> None:
        """The ratio is the point, not the millimetres. On a near-cube every
        span is short and none of them is a mistake."""
        cube = {"min": [0.0, 0.0, 0.0], "max": [50.0, 50.0, 60.0], "size": [50.0, 50.0, 60.0]}
        draft = realise(self._g1_answer(), cube)

        assert not any("apart" in u for u in draft.unresolved)

    def test_holding_and_loading_the_same_face_is_reported(self) -> None:
        """Whatever comes back describes the fixture, not the part."""
        draft = realise(
            _sketch(
                supports=[Support(face="top")],
                loads=[AppliedLoad(face="top", force_n=[0, 0, -500])],
            ),
            G1_BEAM,
        )

        assert any("both held and loaded" in u for u in draft.unresolved)

    def test_a_side_face_pushed_along_the_parts_length_is_reported(self) -> None:
        """The other half of the G1 shape: a force along the axis the part is
        long in, applied to a face that is not an end."""
        draft = realise(self._g1_answer(), G1_BEAM)

        assert any("longest in" in u for u in draft.unresolved)

    def test_no_box_means_no_guessing_rather_than_no_checking(self) -> None:
        """Silence here is honest — there is nothing to measure against — and
        it must not become a warning invented from the sketch alone."""
        draft = realise(self._g1_answer(), None)

        assert draft.unresolved == []

    def test_the_checks_do_not_change_the_load_case_itself(self) -> None:
        """They are a report on the case, never an edit to it. A check that
        quietly moved a face would be far worse than the defect it replaces."""
        with_box = realise(self._g1_answer(), G1_BEAM).load_case
        without = realise(self._g1_answer(), None).load_case

        assert with_box.model_dump() == without.model_dump()


class TestThePromptTeachesTheWordsThatExist:
    def test_every_word_the_schema_allows_is_taught_in_the_prompt(self) -> None:
        """The schema is the grammar the model decodes against and the prompt is
        the only place it learns what the words *mean*. A word in one and not
        the other fails in a different direction each way: taught but not in the
        schema is a decode error, in the schema but not taught is a word nobody
        ever picks — which is exactly how G1 got `left` and `right` on a beam
        lying along Z while `far end` sat unused.

        Written against `FaceName` rather than a list typed here, so adding a
        word to the vocabulary and forgetting to teach it turns this red.
        """
        import typing

        from app.ai import prompts
        from app.ai.schemas import FaceName

        untaught = [
            word
            for word in typing.get_args(FaceName)
            if word not in prompts.PARSE_LOAD_CASE_SYSTEM
        ]

        assert not untaught, f"face words the model is never told about: {untaught}"

    def test_the_prompt_warns_against_the_answer_that_failed_the_gate(self) -> None:
        from app.ai import prompts

        assert "left" in prompts.PARSE_LOAD_CASE_SYSTEM
        assert "±X" in prompts.PARSE_LOAD_CASE_SYSTEM

    def test_every_face_word_the_schema_allows_can_be_resolved(self) -> None:
        """The schema is what the model decodes against, so a word in it that
        `realise` cannot turn into a selector is a guaranteed failure the
        moment the model picks it."""
        import typing

        from app.ai.schemas import FaceName

        for word in typing.get_args(FaceName):
            assert face_selector(word, G1_BEAM) is not None
