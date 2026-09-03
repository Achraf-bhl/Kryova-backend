#!/usr/bin/env python3
"""Generate the daemon's tool table from the server's operation registry.

The daemon re-validates every incoming call against its *own* table, without
importing anything from the server. That is a real security property and not
ceremony: a server that has been talked into sending a malformed call must not
also be the thing that decides the call is well formed.

But "the daemon must not import the server" is not the same as "the two must be
written twice by hand". Hand-copying is what let them drift, and a drifted
daemon fails closed on a tool it should accept — which reads to the user as a
broken product. So the table is *generated* into a file the daemon ships and
loads offline, and `tests/test_bridge_table_is_generated.py` regenerates it and
fails if the checked-in copy differs.

    venv/bin/python scripts/gen_bridge_tools.py          # rewrite the file
    venv/bin/python scripts/gen_bridge_tools.py --check  # exit 1 if stale

Run it after adding an operation to `app/catia/ops/`. The `--check` form is
what CI should call.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.catia.ops import OPERATIONS  # noqa: E402
from app.catia.ops.registry import SERVER_ONLY  # noqa: E402

TARGET = REPO_ROOT / "scripts" / "catia_bridge" / "generated_tools.py"

_HEADER = '''"""The daemon's own copy of the tool vocabulary, schemas and tiers.

GENERATED FILE — DO NOT EDIT BY HAND.

    venv/bin/python scripts/gen_bridge_tools.py

Regenerate it after changing anything under `app/catia/ops/`.
`tests/test_bridge_table_is_generated.py` fails if this file is stale, so a
forgotten regeneration is caught before it becomes a daemon that refuses a tool
the server just started sending.

This file exists so the daemon can validate a call **without importing the
server**. Editing it by hand defeats both halves of that: the daemon stops
matching the server, and the next regeneration silently discards the edit.

Schema shapes here use only the keywords `validation.validate` implements —
type, properties, required, additionalProperties, enum, minimum, maximum,
exclusiveMinimum, minLength, maxLength, items, minItems, maxItems. A keyword
outside that set is not enforced, so the generator refuses to emit one.
"""

from typing import Any

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

'''

#: Exactly what `scripts/catia_bridge/validation.py` implements. A schema using
#: anything else would be accepted by the generator and silently unenforced by
#: the daemon, which is the worst of both worlds: a rule that looks present in
#: the source and does nothing at runtime.
_SUPPORTED_KEYWORDS = frozenset(
    {
        "type", "properties", "required", "additionalProperties", "enum",
        "minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength",
        "items", "minItems", "maxItems",
        # Prose for the model. Carried through because the daemon's refusal
        # messages read better with it, and ignored by the validator.
        "description",
    }
)


def _check_keywords(schema: object, where: str) -> None:
    """Refuse a schema the daemon's validator would not actually enforce.

    `properties` is the one place whose *keys* are arbitrary — they are the
    caller's field names, not schema keywords — so it is recursed into by value
    rather than checked by key. Everything else nests as plain sub-schemas.
    """
    if not isinstance(schema, dict):
        return

    unsupported = sorted(set(schema) - _SUPPORTED_KEYWORDS)
    if unsupported:
        raise SystemExit(
            f"{where}: schema uses {unsupported}, which "
            "scripts/catia_bridge/validation.py does not implement. It would look "
            "enforced and not be. Either add the keyword to the validator (and to "
            "_SUPPORTED_KEYWORDS here) or express the constraint differently."
        )

    for field, subschema in schema.get("properties", {}).items():
        _check_keywords(subschema, f"{where}.{field}")

    items = schema.get("items")
    if isinstance(items, dict):
        _check_keywords(items, f"{where}[]")


def render() -> str:
    """Build the generated module's source."""
    tiers = {"read": "READ", "write": "WRITE", "destructive": "DESTRUCTIVE"}
    lines = [_HEADER]
    lines.append("#: tool -> (tier, schema the daemon validates, keys the server may add)")
    lines.append("TOOLS: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {")

    for operation in OPERATIONS:
        if operation.server_only:
            continue
        schema = operation.daemon_schema()
        _check_keywords(schema, operation.name)
        # `repr`, not `json.dumps`: this is a Python module, and JSON's
        # `false`/`true`/`null` are not Python literals. The output is a single
        # long line and `ruff format` below is what makes it readable.
        lines.append(f"    {operation.name!r}: (")
        lines.append(f"        {tiers[operation.tier.value]},")
        lines.append(f"        {schema!r},")
        lines.append(f"        {operation.server_fields!r},")
        lines.append("    ),")

    lines.append("}")
    lines.append("")
    lines.append("#: Answered by the server. A frame carrying one of these arrived from")
    lines.append("#: somewhere it should not have, and is refused rather than guessed at.")
    lines.append(f"SERVER_ONLY: frozenset[str] = frozenset({sorted(SERVER_ONLY)!r})")
    lines.append("")
    lines.append("#: Tool -> backend method. Kept as data so `session.py` cannot reach a")
    lines.append("#: method that is not on this list, whatever arrives on the wire.")
    lines.append("TOOL_METHODS: dict[str, str] = {")
    for operation in OPERATIONS:
        if operation.server_only:
            continue
        lines.append(f"    {json.dumps(operation.name)}: {json.dumps(operation.method)},")
    lines.append("}")
    lines.append("")
    lines.append("#: Tools whose result the server should wait longer for.")
    long_running = sorted(op.name for op in OPERATIONS if op.long_running)
    lines.append(f"LONG_RUNNING: frozenset[str] = frozenset({long_running!r})")
    lines.append("")
    return "\n".join(lines)


def _formatted(source: str) -> str:
    """Run the project's formatter over the output, when it is installed.

    Generated code that does not match the repo's style shows up as noise in
    every diff of the file, which is how people learn to stop reading it.
    """
    ruff = REPO_ROOT / "venv" / "bin" / "ruff"
    if not ruff.exists():
        return source
    result = subprocess.run(  # noqa: S603 - fixed path, no shell
        [str(ruff), "format", "--stdin-filename", str(TARGET), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 and result.stdout else source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the checked-in file differs from what would be generated.",
    )
    args = parser.parse_args()

    generated = _formatted(render())

    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != generated:
            print(
                f"{TARGET.relative_to(REPO_ROOT)} is stale.\n"
                "Run: venv/bin/python scripts/gen_bridge_tools.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(REPO_ROOT)} is up to date.")
        return 0

    TARGET.write_text(generated, encoding="utf-8")
    tool_count = sum(1 for op in OPERATIONS if not op.server_only)
    print(f"Wrote {TARGET.relative_to(REPO_ROOT)} — {tool_count} tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
