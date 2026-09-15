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

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

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


def editor_name(provider: LLMProvider, *, effort: str) -> str:
    """Names what a rate belongs to: provider, model, effort and the exact system prompt."""
    prompt = hashlib.sha256(EDIT_SYSTEM.encode()).hexdigest()[:12]
    return f"{provider.name}:{provider.model} effort={effort} prompt={prompt}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the mission case set against the configured provider and write the report (QUEUE D2)."""
    from app.ai.providers import get_provider
    from app.design.corruption import measure
    from app.design.corruption_cases import mission_cases

    parser = argparse.ArgumentParser(prog="python -m app.ai.design_editor")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the JSON report.")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--max-tokens", type=int, default=16000)
    args = parser.parse_args(argv)

    provider = get_provider()
    report = measure(
        mission_cases(),
        model_editor(provider, effort=args.effort, max_tokens=args.max_tokens),
        editor_name=editor_name(provider, effort=args.effort),
    )
    args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"{report.editor}: corruption {report.corruption_rate}, exact {report.exact_rate}, "
        f"counts {dict(report.counts)}, cases {report.case_set_digest[:12]}"
    )
    return 0


__all__ = ["EDIT_SYSTEM", "EditedSpec", "edit_message", "editor_name", "main", "model_editor"]


if __name__ == "__main__":  # pragma: no cover - a command, exercised by THE QUEUE D2
    sys.exit(main())
