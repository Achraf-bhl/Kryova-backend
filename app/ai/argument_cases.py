"""The E22.4 case set: requests over the real tool registry, each with the gold call.

`app/ai/argument_accuracy.py` is the harness and ships no number. This is what it runs on: twelve
modelling requests an engineer types, each against a tool of `ToolBox.every_tool()` and scored on
the arguments the request *determines*, never on ones it leaves to a default. The argument names,
enums and defaults were read from the registry's own schemas on 2026-09-15, not recalled, and a
test re-checks every gold argument against the live schema so a renamed parameter fails there
rather than scoring every model zero.

**How gold arguments were chosen**, because a case set that scores guesses is noise:

* A number is gold only when the request states it, and exact (tolerance 0): a request for
  6.5 mm answered with 6.4 is a wrong part, not a near miss.
* An argument whose schema default already gives the requested behaviour is not gold. "Drill a
  hole through" does not require `through_all: true`, because the default is true; a model that
  omits it is right.
* A reference to an existing feature is gold only when the case's `context` names it, the way
  the conversation would have.
* Where two tools are plausible, the request disambiguates in words -- "sealed" for
  `catia_shell` against `catia_shell_faces`, "a hole in the middle of the top face" for
  `catia_hole` against `catia_hole_at` -- and the case says which words do it.

**This set has not been run against a model.** A rate from it is THE QUEUE D3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from app.ai.argument_accuracy import CHOOSE_SYSTEM, Expected, TurnCase


def registry_cases() -> tuple[TurnCase, ...]:
    return (
        TurnCase(
            "pad-profile",
            "Extrude the profile 25 mm.",
            "catia_pad",
            {"sketch": Expected("bracket.profile"), "length_mm": Expected(25.0)},
            context="The closed profile is the sketch named bracket.profile.",
        ),
        TurnCase(
            "pocket-through",
            "Cut the hole sketch all the way through the part.",
            "catia_pocket",
            {"sketch": Expected("hole.sketch"), "through_all": Expected(True)},
            context="The hole's circle is drawn in the sketch named hole.sketch.",
        ),
        TurnCase(
            "pocket-depth",
            "Pocket the slot 4 mm deep.",
            "catia_pocket",
            {"sketch": Expected("slot.sketch"), "depth_mm": Expected(4.0)},
            context="The slot outline is the sketch named slot.sketch.",
        ),
        TurnCase(
            "fillet-vertical",
            "Round the vertical edges with a 3 mm radius.",
            "catia_fillet",
            {"radius_mm": Expected(3.0), "edges": Expected("vertical")},
        ),
        TurnCase(
            "chamfer-top",
            "Put a 1 mm chamfer on the top edges.",
            "catia_chamfer",
            {"length_mm": Expected(1.0), "edges": Expected("top")},
        ),
        TurnCase(
            "hole-centre",
            "Drill a 6.5 mm hole in the middle of the top face.",
            "catia_hole",
            {
                "face": Expected("top"),
                "position": Expected("center"),
                "diameter_mm": Expected(6.5),
            },
        ),
        TurnCase(
            "pattern-circular",
            "Repeat the bolt hole six times around a full circle.",
            "catia_pattern_circular",
            {"count": Expected(6)},
            context="The bolt hole was cut by the pocket named flange.bolt.",
        ),
        TurnCase(
            "pattern-rectangular",
            "Pattern the slot 5 times along the XY plane, 12 mm apart.",
            "catia_pattern_rectangular",
            {"plane": Expected("XY"), "count": Expected(5), "spacing_mm": Expected(12.0)},
        ),
        TurnCase(
            "shell-sealed",
            "Hollow the part out to a 2 mm wall and leave it sealed.",
            "catia_shell",
            {"thickness_mm": Expected(2.0)},
        ),
        TurnCase(
            "new-part",
            "Start a new part called Motor mount.",
            "catia_new_part",
            {"name": Expected("Motor mount")},
        ),
        TurnCase(
            "design-parameter",
            "Make the wall 8 mm.",
            "set_design_parameter",
            {"name": Expected("wall_mm"), "value": Expected(8.0)},
            context="The design has a parameter named wall_mm, currently 6 mm.",
        ),
        TurnCase(
            "sketch-rectangle",
            "Draw a rectangle 120 mm wide and 80 mm high in the plate profile.",
            "catia_sketch_rectangle",
            {
                "sketch": Expected("plate.profile"),
                "width_mm": Expected(120.0),
                "height_mm": Expected(80.0),
            },
            context="The plate's sketch is named plate.profile and is open.",
        ),
    )


def introspection_tools() -> list[Any]:
    """The registry the product offers, built with no session: the chooser never runs a handler."""
    from app.ai.tools import ToolBox

    return ToolBox(db=cast(Any, None), user=cast(Any, None)).every_tool()


def chooser_name(provider: Any, *, limit: int) -> str:
    prompt = hashlib.sha256(CHOOSE_SYSTEM.encode()).hexdigest()[:12]
    return f"{provider.name}:{provider.model} limit={limit} prompt={prompt}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the registry case set against the configured provider and write the report (QUEUE D3)."""
    from app.ai.argument_accuracy import measure, model_chooser
    from app.ai.providers import get_provider
    from app.ai.tool_retrieval import DEFAULT_LIMIT

    parser = argparse.ArgumentParser(prog="python -m app.ai.argument_cases")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the JSON report.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-tokens", type=int, default=2000)
    args = parser.parse_args(argv)

    provider = get_provider()
    report = measure(
        registry_cases(),
        introspection_tools(),
        model_chooser(provider, max_tokens=args.max_tokens),
        chooser_name=chooser_name(provider, limit=args.limit),
        limit=args.limit,
    )
    args.out.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"{report.chooser}: offer recall {report.offer_recall}, selection {report.selection_accuracy}, "
        f"arguments given tool {report.argument_accuracy_given_tool}, end to end "
        f"{report.end_to_end}, cases {report.case_set_digest[:12]}"
    )
    return 0


__all__ = ["chooser_name", "introspection_tools", "main", "registry_cases"]


if __name__ == "__main__":  # pragma: no cover - a command, exercised by THE QUEUE D3
    sys.exit(main())
