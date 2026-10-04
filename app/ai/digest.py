"""An old tool result, shortened for the replay -- from the stored row alone.

Every agent step resends the transcript, and the bulk of a long design session is tool
results: a feature tree, a measurement, a listing, each up to `MAX_TOOL_RESULT_CHARS`
(6,000). By step twenty the model is re-reading, and the account is re-paying for, the
raw output of step two, which it needed for one decision and has not looked at since.
This module turns such a result into one line -- tool, outcome, the numbers that mattered
-- and says how to get the full text back.

**The full result is never lost.** The row in `conversation_messages` is untouched; only
what is *replayed* shrinks, and `recall_earlier_result` (a read-only tool) returns the
stored text on demand. A digest the model cannot expand would be a summary, and the
codebase's rule about summaries (`context.py`) is that what they drop is gone.

Four rules, each pinned by a test that fails when it is removed:

1. **Deterministic, built from the row, never from a model.** Two builds of the same row
   are byte-identical, whatever the key order the JSON arrived in, because a prompt that
   differs between two steps is billed at the full price instead of the cache price. A
   model-written digest would also be a paraphrase placed between the engineer and the
   evidence, on the one screen where the exact numbers are the point.
2. **Numbers first.** A result's key facts are its numbers (a volume, a count, a stress),
   so those are listed before any name or id, and a budget of eight fields cannot be spent
   entirely on identifiers.
3. **An error is never reduced to "ok".** A failed call keeps its first line of cause, up
   to 200 characters -- the model recovers from a refusal by reading why it was refused.
4. **It is still untrusted text, inside the fence.** A digest quotes values a CAD file or a
   part name supplied, so it goes through `fence_tool_result` like the original did: control
   characters stripped, every structural marker defanged, and each value capped so one hostile
   name cannot become the whole line.

Which results are digested -- and why the boundary moves in blocks rather than sliding --
is `app.ai.context.digest_boundary`, because it is a property of the replay and not of a
single result.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.prompts import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from app.ai.sanitise import fence_tool_result

#: The tool that returns a digested result's full text. Named here so the digest and the
#: tool cannot disagree about it; a test asserts the registry offers it.
RECALL_TOOL = "recall_earlier_result"

#: Fields a digest lists. Eight numbers and names carry a result's gist; more is the
#: result again.
MAX_FIELDS = 8
#: Each value is cut to this, so a long string field cannot be the whole line.
FIELD_VALUE_CHARS = 48
#: The first line of an error's cause.
ERROR_CHARS = 200
#: A plain-text result, when it is not JSON, is shown to this length.
TEXT_CHARS = 160
#: Hard ceiling on the digest line itself, before its fence.
DIGEST_MAX_CHARS = 420

MARKER = "[earlier result, shortened]"


def inner_text(content: str) -> str:
    """The text inside the fence a result was stored in; all of it if it is not fenced."""
    text = content.strip()
    if text.startswith(UNTRUSTED_OPEN):
        text = text[len(UNTRUSTED_OPEN) :]
        if text.rstrip().endswith(UNTRUSTED_CLOSE):
            text = text.rstrip()[: -len(UNTRUSTED_CLOSE)]
    return text.strip()


def _clip(text: str, limit: int) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # `repr` would print 0.30000000000000004; four significant figures is what
        # anyone reads off a measurement, and it is the same on every platform.
        return f"{value:.4g}"
    return _clip(str(value), FIELD_VALUE_CHARS)


def _collect(node: Any, path: str, depth: int, out: dict[str, list[str]]) -> None:
    """Sort a result's leaves into numbers, short strings and container sizes.

    Depth is bounded at two: the measurements that matter sit one level down
    (`{"measurement": {"mass_kg": 1.2}}`), and walking further turns a digest back into
    the result. Keys are visited in sorted order so the output does not depend on how the
    JSON happened to be written.
    """
    if isinstance(node, dict):
        for key in sorted(node, key=str):
            child = node[key]
            here = f"{path}.{key}" if path else str(key)
            if isinstance(child, dict):
                if depth < 1:
                    _collect(child, here, depth + 1, out)
                else:
                    out["sizes"].append(f"{here}: {len(child)} fields")
            elif isinstance(child, list):
                out["sizes"].append(f"{here}: {len(child)} items")
            else:
                _bucket(here, child, out)


def _bucket(path: str, value: Any, out: dict[str, list[str]]) -> None:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        out["numbers"].append(f"{path}={_render(value)}")
    elif value is None:
        return
    elif isinstance(value, str) and value.strip():
        out["strings"].append(f"{path}={_render(value)}")


def _summarise(parsed: Any) -> str:
    if isinstance(parsed, dict):
        buckets: dict[str, list[str]] = {"numbers": [], "strings": [], "sizes": []}
        _collect(parsed, "", 0, buckets)
        fields = (buckets["numbers"] + buckets["strings"] + buckets["sizes"])[:MAX_FIELDS]
        return "; ".join(fields) if fields else "an empty result"
    if isinstance(parsed, list):
        return f"{len(parsed)} items"
    return _clip(str(parsed), TEXT_CHARS)


def _error_cause(body: str) -> str:
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("error", "message", "detail", "reason"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return _clip(parsed[key], ERROR_CHARS)
    first = next((line for line in body.splitlines() if line.strip()), "")
    return _clip(first, ERROR_CHARS)


def summarise(*, tool_name: str, tool_call_id: str, content: str, is_error: bool) -> str:
    """The digest of one stored result, unfenced. See the module rules."""
    body = inner_text(content)
    name = tool_name or "a tool"
    if is_error:
        gist = f"ERROR -- {_error_cause(body)}"
    else:
        try:
            gist = "ok -- " + _summarise(json.loads(body))
        except (TypeError, ValueError):
            gist = "ok -- " + _clip(body, TEXT_CHARS)
    line = (
        f"{MARKER} {name} {gist} "
        f"(full text kept: {RECALL_TOOL} tool_call_id={tool_call_id or '?'})"
    )
    if len(line) > DIGEST_MAX_CHARS:
        # Cut the gist, never the pointer: a digest without its way back is a summary.
        pointer = f" (full text kept: {RECALL_TOOL} tool_call_id={tool_call_id or '?'})"
        room = DIGEST_MAX_CHARS - len(pointer) - 1
        line = line[: len(line) - len(pointer)][:room] + "…" + pointer
    return line


def digest_content(
    *, tool_name: str | None, tool_call_id: str | None, content: str | None, is_error: bool
) -> str:
    """The replay form of a stored tool result: its digest, fenced like the original."""
    text = summarise(
        tool_name=tool_name or "",
        tool_call_id=tool_call_id or "",
        content=content or "",
        is_error=bool(is_error),
    )
    return fence_tool_result(text, max_chars=DIGEST_MAX_CHARS + 40)


def is_digest(content: str) -> bool:
    """Whether replayed content is one of these digests (for tests and the recall tool)."""
    return inner_text(content).startswith(MARKER)


__all__ = [
    "DIGEST_MAX_CHARS",
    "MARKER",
    "RECALL_TOOL",
    "digest_content",
    "inner_text",
    "is_digest",
    "summarise",
]
