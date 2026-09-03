"""The daemon's own copy of the tool vocabulary, schemas and approval tiers.

This file exists because **the server is not trusted to describe its own
request**. The frames arriving on the socket are shaped by a language model's
output several layers up; treating them as validated because something upstream
said so is exactly the mistake that turns a prompt injection into a destroyed
CAD model. So the daemon looks up the tool here, checks the tier here, and
validates the arguments here, using its own table -- and refuses anything that
is not in it.

Two consequences worth stating plainly:

* An agent that emits `{"tool": "catia_restore", "tier": "read"}` gets nothing:
  the tier is never read off the wire, it is looked up by tool name.
* A tool the server knows about and this table does not is refused. When the
  two versions drift, the daemon fails closed and says so.

The per-tool server fields are the one concession: the server legitimately adds
context the model never supplies -- the document path to reopen, the checkpoint
bytes to restore, the inline-transfer ceiling. Those keys are enumerated per
tool and everything else is rejected, so "the server may add fields" does not
become "any field is accepted".

**Where the table comes from.** `TOOLS` is no longer written here by hand. It is
generated from the server's operation registry into `generated_tools.py` by
`scripts/gen_bridge_tools.py`, and this module imports it. That keeps the
security property -- the daemon still validates against a table it ships and
loads offline, never against anything on the wire -- while removing the failure
mode the hand-written copy actually had: drifting from the server, and then
failing closed on a tool it should accept, which reaches the user as a broken
product rather than as a caught mistake.

The logic below is what stays hand-written, because it is the part that has to
be *read* to be trusted: the tier lookup, the approval check, and the refusal
messages.
"""

from typing import Any

from .generated_tools import LONG_RUNNING, SERVER_ONLY, TOOL_METHODS, TOOLS
from .validation import SchemaError, validate

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

__all__ = [
    "DESTRUCTIVE",
    "EDGE_SELECTORS",
    "FACE_POSITIONS",
    "LONG_RUNNING",
    "NAMED_FACES",
    "PARAMETER_UNITS",
    "READ",
    "SERVER_ONLY",
    "SKETCH_PLANES",
    "TOOLS",
    "TOOL_METHODS",
    "VIEWPOINTS",
    "WRITE",
    "ToolRefused",
    "check_call",
    "tier_of",
]

#: Value vocabularies the daemon's own COM code reads -- `catia_com` maps a
#: named face onto a sketch plane, and the contract test asserts the two agree.
#: Derived from the generated schemas rather than restated, so they cannot drift
#: from what the daemon actually accepts.
def _enum_of(tool: str, field: str) -> list[str]:
    """The accepted values of one enum field, read off the generated schema."""
    return list(TOOLS[tool][1]["properties"][field]["enum"])


SKETCH_PLANES = _enum_of("catia_pattern_rectangular", "plane")
NAMED_FACES = _enum_of("catia_hole", "face")
FACE_POSITIONS = _enum_of("catia_hole", "position")
EDGE_SELECTORS = _enum_of("catia_fillet", "edges")
VIEWPOINTS = _enum_of("catia_capture_view", "view")
PARAMETER_UNITS = _enum_of("catia_set_parameter", "unit")


class ToolRefused(Exception):
    """The daemon will not run this call. The message goes back as the error."""




def tier_of(tool: str) -> str:
    entry = TOOLS.get(tool)
    if entry is None:
        raise ToolRefused(f"{tool!r} is not a tool this bridge implements.")
    return entry[0]


def check_call(tool: str, arguments: Any, *, approval_token: str | None) -> dict[str, Any]:
    """Validate one incoming call and return the arguments to execute.

    Raises `ToolRefused` with a message the server relays verbatim to the agent.
    """
    if tool in SERVER_ONLY:
        raise ToolRefused(f"{tool} is answered by the server and must not be sent to a bridge.")
    entry = TOOLS.get(tool)
    if entry is None:
        raise ToolRefused(
            f"{tool!r} is not a tool this bridge implements. The bridge may be older "
            "than the server; update it."
        )
    tier, schema, server_fields = entry

    if not isinstance(arguments, dict):
        raise ToolRefused(f"{tool}: arguments must be an object.")

    # The tier comes from this table, never from the frame. A caller cannot
    # relabel a destructive operation as a read one.
    if tier == DESTRUCTIVE and not approval_token:
        raise ToolRefused(
            f"{tool} is a destructive operation and arrived without a server-signed "
            "approval token. Refused."
        )

    model_args = {k: v for k, v in arguments.items() if k not in server_fields}
    try:
        validate(model_args, schema)
    except SchemaError as exc:
        raise ToolRefused(f"{tool}: {exc}") from exc

    if tool == "catia_restore":
        checkpoint = arguments.get("checkpoint")
        if not isinstance(checkpoint, dict) or not checkpoint.get("checkpoint_id"):
            raise ToolRefused("catia_restore: the server did not resolve a checkpoint.")

    return dict(arguments)
