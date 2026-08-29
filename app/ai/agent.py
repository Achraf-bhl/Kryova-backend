"""The agent loop.

The model decides what to do. It is given the transcript so far and a set of
tools; it calls whichever it needs, reads the results, and keeps going until it
has an answer. Nothing here scripts the order of operations.

Four properties this loop is built to guarantee, because an LLM will not
guarantee them on its own:

**It terminates.** A step budget bounds the loop. A model that keeps calling
tools forever gets cut off with a message saying so, rather than running until
a timeout somewhere else.

**Failures are recoverable, not fatal.** A tool that raises is turned into a
tool *result* marked `is_error` and fed back. The model sees what went wrong
and can correct itself -- a wrong argument name or a stale project id costs one
step, not the turn.

**It does not repeat itself.** Every step, including the failures, is persisted
to the conversation. Later turns replay a bounded window of that transcript
plus a running summary of everything older, so the model can still see that it
already listed the projects, already tried that id, already ran that simulation.

**It knows what is true right now.** The transcript is history, not state, so a
block of live facts is rebuilt from the database every turn and injected ahead
of the newest question. Where the two disagree, the block wins -- see
`state.py`.

Everything crossing the boundary from a tool into the transcript is fenced as
untrusted data first (`sanitise.py`): part names, file metadata and parameter
comments are attacker-controlled in exactly the way the prompt-injection
literature describes.
"""

import json
import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai import prompts
from app.ai.context import build_messages, maybe_summarise
from app.ai.provider import LLMError, LLMProvider, TokenUsage
from app.ai.sanitise import MAX_TOOL_RESULT_CHARS, fence_tool_result
from app.ai.tools import ToolBox, ToolError
from app.core.config import settings
from app.models import Conversation, ConversationMessage, MessageRole, User

logger = logging.getLogger(__name__)

#: Ceiling on tool round-trips in a single user turn. A CATIA modelling session
#: legitimately needs a dozen -- sketch, pad, pocket, fillet, measure, capture,
#: export -- so the old budget of 8 cut off real work, not just confused models.
#: Still bounded: a model that has spent this many steps without answering is
#: stuck, and more steps will not unstick it.
DEFAULT_MAX_STEPS = 20


def max_steps() -> int:
    """The step budget for one turn, raisable without a code change.

    `Settings` is the intended home and is checked first. The environment is a
    documented fallback rather than a shortcut: `Settings` is configured with
    `extra="ignore"`, so an `AI_MAX_STEPS` variable is silently dropped until
    the field exists, and a deployment tuning a heavy CATIA workflow would find
    the knob had no effect. Reading it here makes the knob real today and stops
    mattering the moment the field lands.

    A malformed value falls back to the default: a typo in an environment
    variable should not take the agent down.
    """
    configured = getattr(settings, "ai_max_steps", None)
    if configured is None:
        configured = os.environ.get("AI_MAX_STEPS")
    if configured is None:
        return DEFAULT_MAX_STEPS
    try:
        return max(1, int(configured))
    except (TypeError, ValueError):
        logger.warning("Ignoring unusable AI_MAX_STEPS value %r", configured)
        return DEFAULT_MAX_STEPS


def system_prompt() -> str:
    """The frozen prefix for this deployment.

    Two constants rather than one prompt with an optional section: a prefix that
    grows a paragraph when a feature flag flips is a different cache key, and
    the whole point of freezing these strings is that the prefix is stable.
    """
    return prompts.AGENT_SYSTEM_CATIA if settings.catia_enabled else prompts.AGENT_SYSTEM


def summarise_step(tool: str, result: Any, ok: bool) -> str:
    """One line describing what a tool actually found.

    The raw JSON goes to the model; this goes to the human, so it says
    "Found 3 projects" rather than dumping the payload into the UI.
    """
    if not ok:
        return str(result.get("error", "Failed"))[:200] if isinstance(result, dict) else "Failed"
    if not isinstance(result, dict):
        return "Done"

    if tool == "create_project":
        return f"Created {result.get('name', 'project')}"
    if tool == "list_projects":
        return f"Found {len(result.get('projects', []))} project(s)"
    if tool == "list_materials":
        return f"{len(result.get('materials', []))} materials available"
    if tool == "list_geometry":
        versions = result.get("geometry_versions", [])
        latest = versions[0]["filename"] if versions else "none"
        return f"{len(versions)} version(s), latest {latest}"
    if tool == "list_simulations":
        return f"{len(result.get('simulations', []))} previous run(s)"
    if tool == "get_simulation":
        status = result.get("status", "?")
        fos = (result.get("result") or {}).get("factor_of_safety")
        return f"Status {status}" + (f", factor of safety {fos:.2f}" if fos else "")
    if tool == "run_simulation":
        return f"Queued run {result.get('id', '')}".strip()
    if tool == "delete_simulation":
        return "Run deleted"
    if tool.startswith("catia_"):
        return _catia_summary(result)
    return "Done"


def _catia_summary(result: dict[str, Any]) -> str:
    """Say what the part looks like now, not just that the call returned."""
    parts: list[str] = []
    if result.get("feature"):
        parts.append(str(result["feature"]))
    if result.get("document"):
        parts.append(str(result["document"]))
    if result.get("mass_kg") is not None:
        parts.append(f"{result['mass_kg']} kg")
    if result.get("geometry_version") is not None:
        parts.append(f"geometry v{result['geometry_version']}")
    return ", ".join(parts) if parts else "Done"


@dataclass
class AgentStep:
    """One tool call and its outcome, for the caller to display."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    result: Any


@dataclass
class AgentReply:
    text: str
    steps: list[AgentStep] = field(default_factory=list)
    #: True when the step budget cut the loop off before the model finished.
    truncated: bool = False
    #: Everything this turn cost, including summarisation.
    usage: TokenUsage = field(default_factory=TokenUsage)


def _serialise(value: Any) -> str:
    """Render a tool result as fenced, untrusted text.

    The fence is not cosmetic. Everything a tool returns is text somebody else
    wrote -- a CATIA feature name, a filename, a parameter comment -- and this
    is the boundary where it stops being able to pass as instruction.
    """
    try:
        text = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return fence_tool_result(text, max_chars=MAX_TOOL_RESULT_CHARS)


def _append(
    db: Session, conversation: Conversation, role: MessageRole, **fields: Any
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=_next_sequence(conversation),
        role=role,
        **fields,
    )
    conversation.messages.append(message)
    db.add(message)
    db.flush()
    return message


def _next_sequence(conversation: Conversation) -> int:
    """One past the highest sequence stored.

    Not `len(messages)`: once older messages are folded into the summary they
    are still stored, but any future pruning of them would make length-based
    numbering collide with sequences already used.
    """
    return max((message.sequence for message in conversation.messages), default=-1) + 1


def stream_agent(
    *,
    db: Session,
    provider: LLMProvider,
    conversation: Conversation,
    toolbox: ToolBox,
    user_message: str,
    user: User | None = None,
    allow_mutations: bool = False,
    max_tokens: int = 4_000,
) -> Iterator[dict[str, Any]]:
    """The loop, as a generator of events.

    This is the single implementation; `run_agent` collects it. Yielding at
    each boundary is what lets a UI show the agent working -- which tool it
    reached for, what came back, how long it took -- instead of a spinner that
    sits there for thirty seconds.

    Everything is persisted as it happens, so a crash mid-loop leaves a
    transcript that still reflects what actually ran.
    """
    owner = user if user is not None else toolbox.user
    budget = max_steps()
    system = system_prompt()
    labels = toolbox.labels()
    usage = TokenUsage()

    _append(db, conversation, MessageRole.USER, content=user_message)

    # Fold before building the window, so the material being folded is still
    # present to be read and the window that follows is already compacted.
    usage += maybe_summarise(db, provider, conversation)

    steps: list[AgentStep] = []
    schemas = toolbox.schemas(include_mutating=allow_mutations)

    for step in range(budget):
        yield {"type": "thinking", "step": step + 1, "max_steps": budget}
        turn = provider.chat(
            system=system,
            messages=build_messages(db, owner, conversation),
            tools=schemas,
            max_tokens=max_tokens,
        )
        usage += turn.usage

        if not turn.wants_tools:
            text = turn.text
            if turn.truncated:
                # A cut-off answer presented as a finished one is the worst
                # outcome here: the user reads a confident half-sentence about
                # their part and has no way to know the rest was lost.
                text += (
                    "\n\n[This answer was cut off at the model's output limit. "
                    "Ask me to continue, or narrow the question.]"
                )
            _append(db, conversation, MessageRole.ASSISTANT, content=text)
            db.commit()
            yield {"type": "message", "content": text}
            yield {
                "type": "done",
                "conversation_id": conversation.id,
                "project_id": toolbox.project_id,
                "truncated": False,
                "steps": len(steps),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            }
            return

        # Persist the assistant turn *including* its tool calls before running
        # them: if a tool crashes the process, the transcript still shows what
        # was attempted rather than silently losing the step.
        _append(
            db,
            conversation,
            MessageRole.ASSISTANT,
            content=turn.text or None,
            tool_calls=[
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in turn.tool_calls
            ],
        )

        if turn.text:
            # Models often narrate before acting ("Let me check your projects").
            # Surface it so the UI is not silent while tools run.
            yield {"type": "narration", "content": turn.text}

        for call in turn.tool_calls:
            yield {
                "type": "tool_start",
                "id": call.id,
                "tool": call.name,
                "label": labels.get(call.name, call.name.replace("_", " ")),
                "arguments": call.arguments,
            }
            started = time.monotonic()
            try:
                result: Any = toolbox.call(
                    call.name, call.arguments, allow_mutations=allow_mutations
                )
                ok = True
            except ToolError as exc:
                result, ok = {"error": str(exc)}, False
            except Exception as exc:  # noqa: BLE001 - must not kill the turn
                logger.exception("Tool %s raised", call.name)
                result, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
            elapsed_ms = int((time.monotonic() - started) * 1000)

            steps.append(AgentStep(tool=call.name, arguments=call.arguments, ok=ok, result=result))
            _append(
                db,
                conversation,
                MessageRole.TOOL,
                content=_serialise(result),
                tool_call_id=call.id,
                tool_name=call.name,
                is_error=not ok,
                duration_ms=elapsed_ms,
            )
            yield {
                "type": "tool_end",
                "id": call.id,
                "tool": call.name,
                "arguments": call.arguments,
                "ok": ok,
                "result": result,
                "summary": summarise_step(call.name, result, ok),
                "duration_ms": elapsed_ms,
            }

        db.commit()
        logger.debug("agent step %d: %d tool call(s)", step + 1, len(turn.tool_calls))

    # Out of steps. Ask for a final answer with tools withdrawn, so the user
    # gets the model's best summary instead of a bare "gave up".
    try:
        closing = provider.chat(
            system=system + prompts.AGENT_OUT_OF_STEPS,
            messages=build_messages(db, owner, conversation),
            tools=[],
            max_tokens=max_tokens,
        )
        text = closing.text
        usage += closing.usage
    except LLMError:
        text = (
            "I used all my tool calls for this turn without reaching an answer. "
            "Try narrowing the question."
        )

    _append(db, conversation, MessageRole.ASSISTANT, content=text)
    db.commit()
    yield {"type": "message", "content": text}
    yield {
        "type": "done",
        "conversation_id": conversation.id,
        "project_id": toolbox.project_id,
        "truncated": True,
        "steps": len(steps),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
    }


def run_agent(
    *,
    db: Session,
    provider: LLMProvider,
    conversation: Conversation,
    toolbox: ToolBox,
    user_message: str,
    user: User | None = None,
    allow_mutations: bool = False,
    max_tokens: int = 4_000,
) -> AgentReply:
    """Run the loop to completion and return the result.

    A thin collector over `stream_agent` -- there is one loop, so the streaming
    and non-streaming endpoints can never drift apart.
    """
    text = ""
    steps: list[AgentStep] = []
    truncated = False
    usage = TokenUsage()

    for event in stream_agent(
        db=db,
        provider=provider,
        conversation=conversation,
        toolbox=toolbox,
        user_message=user_message,
        user=user,
        allow_mutations=allow_mutations,
        max_tokens=max_tokens,
    ):
        if event["type"] == "tool_end":
            steps.append(
                AgentStep(
                    tool=event["tool"],
                    arguments=event.get("arguments", {}),
                    ok=event["ok"],
                    result=event["result"],
                )
            )
        elif event["type"] == "message":
            text = event["content"]
        elif event["type"] == "done":
            truncated = event["truncated"]
            usage = TokenUsage(
                prompt_tokens=event.get("prompt_tokens", 0),
                completion_tokens=event.get("completion_tokens", 0),
            )

    return AgentReply(text=text, steps=steps, truncated=truncated, usage=usage)
