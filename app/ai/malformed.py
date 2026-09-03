"""Catching a tool call the model wrote as prose instead of calling.

Every provider in `providers/` returns tool calls in a structured field, and the
agent loop acts on that field alone. A model that instead *writes out* what it
would have called -- a fenced JSON block, a leaked harmony channel header, a
`<tool_call>` tag -- produces a turn with no tool calls and a body of text, which
the loop reads as "the model is done, this is its answer".

That is the worst failure this agent has. Nothing ran, so nothing failed, so
there is no error anywhere; the text is then shown to the user as the reply, and
it typically says the work was done. Observed live: "Project created" over a
database with no new project in it. A wrong answer announces itself. A confident
description of geometry that was never built does not.

The remedy is not to run what we parse out of the prose. A call that never went
through the provider's tool channel never went through argument validation
either, and this agent's tools mutate real CAD documents. It is one round trip
to tell the model exactly what it did wrong and let it re-issue the call
properly -- which lands it back on the validated path.

The bar for the patterns below is deliberately high: they match call *syntax*,
never a name. "I'll use catia_pad to extrude it" is ordinary narration and must
pass through untouched, or the agent starts arguing with itself about sentences.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Harmony (gpt-oss) routes tool calls through a channel header. When the
#: template is applied to a truncated prompt, or a client reads the raw stream,
#: this leaks into the visible content. There is no reading of it as prose.
_HARMONY = re.compile(
    r"<\|channel\|>\s*commentary[^<]*?to=(?:functions\.)?([a-z_][a-z0-9_]*)", re.I
)

#: The tag family used by Qwen, Hermes and most llama.cpp chat templates.
_TAGGED = re.compile(r"<(?:tool_call|function_call)>|<function\s*=\s*([a-z_][a-z0-9_]*)", re.I)

#: `catia_pad({"length_mm": 10})` -- a call written as source. The opening brace
#: is required: `mesh(...)` in a sentence about meshing is not a tool call.
_CALL_SYNTAX = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(\s*\{")

#: Keys a model uses for the tool's name, and for its arguments, when it writes
#: a call as a JSON object. Both must be present -- see `_json_calls`.
_NAME_KEYS = ("name", "tool", "tool_name", "function", "recipient")
_ARGUMENT_KEYS = ("arguments", "parameters", "args", "input", "params")


def _json_objects(text: str) -> list[Any]:
    """Every top-level JSON object in the text, fenced or bare.

    A brace scanner rather than a regex: tool arguments nest, and the CATIA
    tools take nested objects (`{"material": {...}}`), so a non-greedy match on
    the first `}` truncates exactly the calls worth catching.
    """
    found: list[Any] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        found.append(json.loads(text[start : index + 1]))
                    except ValueError:
                        pass
                    start = -1
    return found


def _json_calls(text: str, known: set[str]) -> list[str]:
    """Tool names from JSON objects shaped like a call.

    Both a name key and an arguments key are required. One alone is far too
    common in honest output: this agent's tools *return* JSON containing
    `"name"`, and a model quoting a result back is not making a call.
    """
    names: list[str] = []
    for obj in _json_objects(text):
        if not isinstance(obj, dict):
            continue
        # `{"function": {"name": ..., "arguments": ...}}` -- the OpenAI shape,
        # written out rather than sent.
        inner = obj.get("function")
        candidates = [obj, inner] if isinstance(inner, dict) else [obj]
        for candidate in candidates:
            name = next(
                (
                    candidate[key]
                    for key in _NAME_KEYS
                    if isinstance(candidate.get(key), str) and candidate[key] in known
                ),
                None,
            )
            has_arguments = any(key in candidate for key in _ARGUMENT_KEYS)
            if name and (has_arguments or candidate is inner):
                names.append(name)
                break
    return names


def find_written_tool_calls(text: str, known: set[str]) -> list[str]:
    """Names of real tools this text *describes calling* rather than calls.

    Returns them in the order found, deduplicated. Empty means the text is
    ordinary prose -- which is the answer for almost everything, and has to be:
    a false positive here turns a correct final answer into a retry the user
    waits through.

    `known` is the live tool registry, so a hallucinated name is not reported.
    The model inventing `catia_list_projects` in prose is a different failure
    with its own handling in `ToolBox.call`, and treating it as a written call
    would send the loop after a tool that does not exist.
    """
    if not text or not text.strip():
        return []

    names: list[str] = []
    names.extend(match for match in _HARMONY.findall(text) if match in known)
    names.extend(match for match in _TAGGED.findall(text) if match and match in known)
    names.extend(_json_calls(text, known))
    names.extend(match for match in _CALL_SYNTAX.findall(text) if match in known)

    # A bare `<tool_call>` tag with an unparseable body is still unmistakably a
    # written call; report it so the turn is corrected rather than shown.
    if not names and _TAGGED.search(text):
        names.append("")

    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


#: What a model emits when it has nothing to say but still has to say something.
#: `{}` is the one seen live -- the agent accepted it as the final answer and the
#: user's chat ended with a two-character bubble -- but the family is the same:
#: a JSON husk with no content in it.
_EMPTY_JSON = frozenset({"{}", "[]", "null", '""', "''", "{ }", "[ ]"})


def is_contentless(text: str) -> bool:
    """Is this text an answer, or only the shape of one?

    `not text.strip()` is the obvious check and it misses the case that actually
    reached a user: a turn whose entire body was `{}`. That is not whitespace,
    so the agent read it as the model's considered reply, wrote it to the
    transcript and closed the turn -- and the conversation ended with an empty
    JSON object where the answer should have been.

    Treated as blank rather than as a written tool call, because it names no
    tool and describes no work: the right correction is "you said nothing, say
    something", which is exactly what the empty-turn path already sends.

    Deliberately a small closed set and not "does this parse as JSON". A real
    answer can legitimately contain JSON -- a model quoting the arguments it
    used, or a result it is explaining -- and treating every JSON-shaped reply
    as empty would retry good answers.
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    if stripped in _EMPTY_JSON:
        return True
    # The same husk inside a code fence, which is how a model that has been told
    # to answer in JSON most often emits it.
    return _unfenced(stripped) in _EMPTY_JSON


def _unfenced(text: str) -> str:
    """The body of a single ``` fence, or the text unchanged."""
    if not text.startswith("```") or not text.endswith("```"):
        return text
    body = text[3:-3]
    if body[:4].lower().startswith("json"):
        body = body[4:]
    return body.strip()


def correction_for(names: list[str]) -> str:
    """What to send back so the next step lands on the validated path.

    Written as an instruction the model can execute, not a scolding: name the
    mistake, name the fix, and state the one fact that stops it repeating --
    that nothing ran, so the user has not been told anything yet.
    """
    called = ", ".join(name for name in names if name)
    subject = f"a call to {called}" if called else "a tool call"
    return (
        f"Your last message contained {subject} written as text. Text is not a "
        "tool call: nothing ran, nothing changed, and the user has not been "
        "shown anything. Issue it as a real tool call now. If you meant to "
        "answer instead, answer in plain sentences with no JSON, no code fence "
        "and no tags -- and do not claim any work was done, because none was."
    )
