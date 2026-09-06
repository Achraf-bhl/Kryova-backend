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
        """The measurement itself, pinned. 220k was the size before."""
        assert len(REGISTRY_JSON) < 215_000

    def test_pad_still_describes_its_sketch_and_length(self) -> None:
        pad = next(s for s in CATIA_TOOL_SPECS if s.name == "catia_pad")
        properties = pad.parameters["properties"]
        assert "profile" in properties["sketch"]["description"].lower()
        assert "extrude" in properties["length_mm"]["description"].lower()
