"""A rule that applies to every tool is stated once, not repeated per argument.

Measured on ladder prompt H4 turn 2, 2026-09-06, on the real seat, with the
per-step latency logging added the same day:

    agent step  2/20: model 20038 ms, 1 tool call(s) [catia_list_features  744ms]
    agent step  8/20: model 25889 ms, 1 tool call(s) [catia_capture_view   721ms]
    agent step 11/20: model 15169 ms, 1 tool call(s) [catia_capture_view   532ms]

The model is 15-26 seconds a step and the CATIA calls are half a second. COM is
not the latency of a turn; the prompt is. And the prompt broke down as:

    system prompt : 23,698 chars ~ 5,924 tok
    tool schemas  : 59,467 chars ~14,866 tok   (50 tools)
    messages      : 16,029 chars ~ 4,007 tok
    TOTAL         : 99,194 chars ~24,798 tok

Sixty per cent of it is tool schemas, re-sent on every one of twenty steps.
Inside those, one sentence -- "Name it exactly as catia_list_features or the
tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2')" --
appeared **182 times** across the registry, because `element_reference`
appended it to every named argument.

It is a rule about how references work, not about any one argument, so it
belongs in the frozen system prefix where it is stated once and cached, rather
than in the per-turn payload where it is charged again every step. Moving it
saved 8,489 characters, about 2,122 tokens, on every model call of every turn
-- and took a 32,768-token window from 76% full to 69%, which matters because
Ollama truncates a prompt from the front in silence.

**Four more conventions moved the same way on 2026-09-07**, because the ceiling
below caught the registry drifting back over it as E14 and E16 added vocabulary.
The same argument settled all four -- each is a fact about how the tools work
rather than about the argument carrying it, and each was charged once per
argument per step:

    "[u, v] in millimetres, in the sketch's own 2D frame ..."   x35
    "Draw as a construction element -- geometry that ..."       x22
    "[x, y, z]; length is ignored ... zero is refused."         x23
    "Defaults to the most recent sketch."                       x26

215,365 -> 206,337 characters, about 2,250 tokens off every model call. That
compounds with a change made in the same stretch: `DEFAULT_MAX_STEPS` went 20
to 60, so a repeated sentence is now charged up to three times as often as when
the first measurement above was taken. Raising the ceiling instead would have
been the cheaper edit and the wrong one.

What is deliberately **not** moved: `support`, `face_reference` and
`edge_reference` still enumerate their legal values in the schema, even though
those repeat too. A convention can be read once and remembered; a list of
accepted strings is what the model is choosing from at the moment it writes the
call, and belongs where it is choosing.

Offline: schemas and strings.
"""

from __future__ import annotations

import json

from app.ai.prompts import AGENT_SYSTEM_CATIA, AGENT_SYSTEM_CATIA_DOCS
from app.catia.ops import vocabulary
from app.catia.tool_specs import CATIA_TOOL_SPECS

#: The sentence that was repeated. Any of it reappearing in a schema means the
#: duplication has come back.
REPEATED = "Name it exactly as catia_list_features"

#: The four moved on 2026-09-07, each keyed on a distinctive fragment rather
#: than the whole sentence, so a reworded reappearance is caught too.
MOVED_TO_THE_PROMPT = (
    "in the sketch's own 2D frame",
    "guides other geometry",
    "length is ignored",
    "Defaults to the most recent sketch",
)

REGISTRY_JSON = json.dumps(
    [{"name": s.name, "description": s.description, "parameters": s.parameters}
     for s in CATIA_TOOL_SPECS]
)


class TestItIsStatedOnce:
    def test_the_prompt_carries_the_rule(self) -> None:
        for prompt in (AGENT_SYSTEM_CATIA, AGENT_SYSTEM_CATIA_DOCS):
            assert "Naming." in prompt
            assert "catia_list_features" in prompt
            assert "'Sketch.1'" in prompt

    def test_the_prompt_says_a_name_is_never_translated(self) -> None:
        """The French seat calls a pad `Extrusion.1`, and a model that
        translates it back to `Pad.1` addresses nothing."""
        assert "Extrusion.1" in AGENT_SYSTEM_CATIA

    def test_no_schema_repeats_it(self) -> None:
        assert REPEATED not in REGISTRY_JSON

    def test_no_schema_repeats_the_conventions_either(self) -> None:
        for fragment in MOVED_TO_THE_PROMPT:
            assert fragment not in REGISTRY_JSON, f"{fragment!r} is back in the schemas"

    def test_the_prompt_carries_every_convention_the_schemas_gave_up(self) -> None:
        """The other half, and the half that makes the trim safe rather than lossy.

        Deleting a sentence from a schema and forgetting to state it anywhere is
        not a saving, it is a model guessing at a frame convention. Each check
        is the fact itself, not the wording, so the paragraph can be rewritten
        without this failing -- and cannot be dropped without it failing.
        """
        for prompt in (AGENT_SYSTEM_CATIA, AGENT_SYSTEM_CATIA_DOCS):
            assert "[u, v]" in prompt and "sketch's own 2D frame" in prompt
            assert "[x, y, z]" in prompt and "part's frame" in prompt
            assert "length is ignored" in prompt
            assert "zero is refused" in prompt
            assert "most recent sketch" in prompt
            assert "construction=true" in prompt
            assert "never padded" in prompt

    def test_element_reference_returns_the_description_it_was_given(self) -> None:
        """The helper that used to append it. Verified at the source, so a
        future edit that re-adds the sentence fails here as well as above."""
        built = vocabulary.element_reference("The sketch to draw in.")
        assert built["description"] == "The sketch to draw in."


class TestTheSchemasStayedUsable:
    """Trimming must not cost the model the information it calls tools with."""

    def test_every_named_argument_still_says_what_it_is(self) -> None:
        for spec in CATIA_TOOL_SPECS:
            for name, prop in (spec.parameters.get("properties") or {}).items():
                if prop.get("type") == "string":
                    assert prop.get("description"), f"{spec.name}.{name} lost its description"

    def test_the_registry_is_smaller_than_it_was(self) -> None:
        """The measurement itself, pinned. 220k -> 215k -> 206k.

        This is the test that earned its keep: set at 215,000 on 2026-09-06, it
        went red on 2026-09-07 at 215,365 after E14 and E16 added assembly and
        knowledge vocabulary. Nothing added was wrong -- the payload had simply
        drifted back, which is exactly what a budget with no guard does. The
        ceiling is 208,000 rather than the measured 206,337 so that a genuinely
        new tool is not a test failure, and low enough that the next few
        sentences of drift are.
        """
        assert len(REGISTRY_JSON) < 208_000

    def test_pad_still_describes_its_sketch_and_length(self) -> None:
        pad = next(s for s in CATIA_TOOL_SPECS if s.name == "catia_pad")
        properties = pad.parameters["properties"]
        assert "profile" in properties["sketch"]["description"].lower()
        assert "extrude" in properties["length_mm"]["description"].lower()
