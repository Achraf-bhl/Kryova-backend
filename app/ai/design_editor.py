"""A model as an editor of design specs, for the silent-corruption harness (E22.2).

`app/design/corruption.py` measures any `Editor` -- a callable from a spec and an instruction to
a spec -- and imports nothing outside `app/design/`. This is the one editor that is a model: the
configured `LLMProvider` is handed the whole spec in its canonical serialised form and the
instruction, and must hand back the whole spec. What it returns is parsed with
`DesignSpec.from_dict`, so a reply that is not a valid spec raises `SpecError` and the harness
records the case as FAILED rather than guessing what the model meant.

The instruction and the spec both go in the user message, never the system prompt: the system
prompt is frozen per call site, and a case set is data.

This is a measurement instrument, not a product path. The agent edits designs through
`set_design_parameter`, one decision at a time; whole-spec rewriting is exactly the interaction
the literature found corrupting, and it is measured here so that claim has a number for Kryova.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel, Field

from app.ai.provider import LLMProvider
from app.design.errors import SpecError
from app.design.spec import DesignSpec

EDIT_SYSTEM = (
    "You edit a mechanical part's design specification. You are given the specification as "
    "JSON and one instruction. Return the complete specification as JSON with the instruction "
    "carried out and nothing else changed: keep every parameter, feature, name, argument and "
    "the material exactly as they are unless the instruction requires otherwise. Put the JSON "
    "in the spec_json field."
)


class EditedSpec(BaseModel):
    spec_json: str = Field(description="The whole edited specification, as JSON.")


def edit_message(spec: DesignSpec, instruction: str) -> str:
    return (
        "Specification:\n"
        + json.dumps(spec.to_dict(), indent=2)
        + "\n\nInstruction:\n"
        + instruction.strip()
    )


def model_editor(
    provider: LLMProvider, *, effort: str, max_tokens: int
) -> Callable[[DesignSpec, str], DesignSpec]:
    """An `Editor` backed by `provider`. Returns a callable `(spec, instruction) -> spec`."""

    def edit(spec: DesignSpec, instruction: str) -> DesignSpec:
        completion = provider.complete(
            system=EDIT_SYSTEM,
            user=edit_message(spec, instruction),
            schema=EditedSpec,
            effort=effort,
            max_tokens=max_tokens,
        )
        try:
            data = json.loads(completion.value.spec_json)
        except ValueError as exc:
            raise SpecError(f"The model's reply is not JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise SpecError("The model's reply is JSON but not a specification object.")
        return DesignSpec.from_dict(data)

    return edit


__all__ = ["EDIT_SYSTEM", "EditedSpec", "edit_message", "model_editor"]
