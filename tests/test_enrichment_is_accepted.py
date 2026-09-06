"""What the server enriches, the daemon must accept.

`dispatch._enrich` adds the context a model never supplies -- a document's path,
a checkpoint's bytes, the resolved command candidates. `tool_table.check_call`
then strips the fields the operation declares in `server_fields` and validates
everything that is left against the daemon's own schema, which is
`additionalProperties: false`.

So the two lists have to agree exactly, and the failure when they do not is
total rather than partial: an enriched field that was never declared is not
ignored, it is the thing that fails validation, and **every call to that tool is
refused** with a message accusing the model of sending a field the server put
there itself.

That happened. `command_ids` was added to `catia_run_command`'s payload on
2026-09-06 -- the fix that stops a display label reaching `StartCommand` and
raising the modal that kills the seat -- without the matching `server_fields`
entry. Every `catia_run_command` on the seat was refused from then until ladder
prompt H3 surfaced it, twice in one run:

    catia_run_command: arguments has unknown field(s): command_ids.
    Accepted: command (string, required)

Nothing caught it. `test_bridge_table_is_generated.py` checks that every
*declared* parameter reaches the daemon; `test_backend_signatures.py` checks the
backend methods can take what they are sent. Neither runs the enrichment. This
does: it builds each enrichable tool's real payload and puts it through the
daemon's real gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.catia.dispatch import _enrich
from app.catia.tool_specs import get_spec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.tool_table import ToolRefused, check_call  # noqa: E402


def enrich(tool: str, arguments: dict[str, Any], *, language: str | None = "fr") -> dict[str, Any]:
    spec = get_spec(tool)
    assert spec is not None, tool
    return _enrich(
        None,  # type: ignore[arg-type]
        spec=spec,
        document=None,
        arguments=arguments,
        language=language,
    )


#: The tools `_enrich` resolves without touching the database, with a call the
#: model would plausibly make. The document- and blob-backed ones
#: (`catia_open_document`, `catia_restore`, `catia_import`) need a session and a
#: real row, and are covered by `test_catia_close_document.py`.
ENRICHED = [
    ("catia_run_command", {"command": "Edge Fillet"}),
    ("catia_run_command", {"command": "New Window"}),  # the one with a published id
    ("catia_run_command", {"command": "Fastener Pattern"}),  # not in the reference
    ("catia_dialog_action", {"action": "ok"}),
    ("catia_dialog_action", {"action": "ok", "button": "Aceptar"}),
    ("catia_switch_workbench", {"workbench": "Part Design"}),
    ("catia_checkpoint", {"label": "before the pocket"}),
    ("catia_export_step", {"note": "v1"}),
    ("catia_capture_view", {"view": "iso"}),
]


@pytest.mark.parametrize(
    ("tool", "arguments"),
    ENRICHED,
    ids=[f"{tool}-{i}" for i, (tool, _) in enumerate(ENRICHED)],
)
@pytest.mark.parametrize("language", ["fr", "de", "en", None])
def test_the_daemon_accepts_what_the_server_sends(
    tool: str, arguments: dict[str, Any], language: str | None
) -> None:
    """Every language, because enrichment depends on the seat's.

    A field that only appears for a translated seat would be refused on exactly
    the machines this product ships to and nowhere else.
    """
    payload = enrich(tool, arguments, language=language)
    try:
        check_call(tool, payload, approval_token="test-token")
    except ToolRefused as exc:
        pytest.fail(
            f"the server enriched {tool} with a field its own daemon refuses: {exc}\n"
            f"payload was {payload}\n"
            "Add the field to this operation's `server_fields` in app/catia/ops/ and "
            "re-run scripts/gen_bridge_tools.py."
        )


def test_the_regression_this_was_written_for() -> None:
    """`command_ids` specifically, named so the intent survives a rewrite."""
    payload = enrich("catia_run_command", {"command": "Edge Fillet"})
    assert "command_ids" in payload, "enrichment no longer sends it -- this test is a ghost"
    check_call("catia_run_command", payload, approval_token="test-token")


def test_a_field_the_model_invented_is_still_refused() -> None:
    """The property that makes the above non-trivial.

    `server_fields` is a hole in `additionalProperties: false`, and widening it
    is how "the server may add fields" quietly becomes "any field is accepted".
    A key that is neither declared in the schema nor in `server_fields` must
    still be rejected.
    """
    payload = enrich("catia_run_command", {"command": "Edge Fillet"})
    with pytest.raises(ToolRefused, match="unknown field"):
        check_call(
            "catia_run_command",
            {**payload, "start_immediately": True},
            approval_token="test-token",
        )
