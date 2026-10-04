"""What the tool registry costs to send, per tool and in total.

On a hosted model the registry is resent on **every agent step**, so its size is a line
on the bill that nothing else in the product comes close to (ROAD_TO_10 1.6). This is
the one place it is measured; `tests/test_tool_registry_size.py` holds the product to
the numbers it prints.

    venv/bin/python -m scripts.schema_report              # totals and the 30 largest
    venv/bin/python -m scripts.schema_report --top 60
    venv/bin/python -m scripts.schema_report --json       # for a diff between two commits

**Bytes are counted the way they travel**: compact JSON, no spaces after `,` or `:`
(`json.dumps(..., separators=(",", ":"))`, what an HTTP client sends). The default
`json.dumps` pads both and reads about 8 % larger, which would make every figure here
disagree with the wire. Tokens are *estimated* from bytes and labelled as such: the
exact count is the vendor's tokenizer's, and a figure that looked precise would be the
unmeasured claim this codebase refuses to print.

The registry is read with no database and no user, so this runs anywhere, in a second.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from app.ai.tokens import CHARS_PER_TOKEN

#: Bytes per token for JSON-with-prose on a typical BPE tokenizer. An estimate, kept in
#: one place (`app/ai/tokens.py`, which the context window uses too) so a report that
#: quotes tokens says which assumption it made.
BYTES_PER_TOKEN_ESTIMATE = CHARS_PER_TOKEN


@dataclass(frozen=True)
class ToolSize:
    name: str
    bytes: int
    #: The tool's prose: what the model reads to decide whether to call it.
    description_bytes: int
    #: Its parameter schema: types, bounds and per-parameter prose.
    parameter_bytes: int


def compact(value: Any) -> str:
    """JSON as it travels: the unit every figure in this module is counted in."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def measure(*, include_mutating: bool) -> list[ToolSize]:
    """Every tool the registry offers, largest first."""
    from app.ai.tools import ToolBox

    box = ToolBox(db=cast(Any, None), user=cast(Any, None))
    sizes = []
    for schema in box.schemas(include_mutating=include_mutating):
        function = schema["function"]
        sizes.append(
            ToolSize(
                name=function["name"],
                bytes=len(compact(schema)),
                description_bytes=len(compact(function["description"])),
                parameter_bytes=len(compact(function["parameters"])),
            )
        )
    return sorted(sizes, key=lambda s: (-s.bytes, s.name))


def total_bytes(sizes: list[ToolSize]) -> int:
    return sum(size.bytes for size in sizes)


def estimated_tokens(byte_count: int) -> int:
    return int(byte_count / CHARS_PER_TOKEN)


def report(top: int = 30) -> str:
    lines: list[str] = []
    for label, mutating in (("all tools (mutation allowed)", True), ("read-only tools", False)):
        sizes = measure(include_mutating=mutating)
        total = total_bytes(sizes)
        lines.append(
            f"{label}: {len(sizes)} tools, {total:,} bytes "
            f"(~{estimated_tokens(total):,} tokens, estimated at "
            f"{BYTES_PER_TOKEN_ESTIMATE} bytes/token)"
        )
        lines.append(
            f"  prose {sum(s.description_bytes for s in sizes):,} B, "
            f"parameters {sum(s.parameter_bytes for s in sizes):,} B"
        )
        if mutating:
            lines.append(f"  the {top} largest:")
            for size in sizes[:top]:
                lines.append(
                    f"    {size.bytes:>6,}  {size.name}  "
                    f"(prose {size.description_bytes:,}, parameters {size.parameter_bytes:,})"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.json:
        print(
            json.dumps(
                {
                    "all": [asdict(s) for s in measure(include_mutating=True)],
                    "read_only": [asdict(s) for s in measure(include_mutating=False)],
                },
                indent=1,
            )
        )
    else:
        print(report(args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
