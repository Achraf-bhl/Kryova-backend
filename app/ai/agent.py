"""The agent loop.

The model decides what to do. It is given the transcript so far and a set of
tools; it calls whichever it needs, reads the results, and keeps going until it
has an answer. Nothing here scripts the order of operations.

Three properties this loop is built to guarantee, because an LLM will not
guarantee them on its own:

**It terminates.** `MAX_STEPS` bounds the loop. A model that keeps calling
tools forever gets cut off with a message saying so, rather than running until
a timeout somewhere else.

**Failures are recoverable, not fatal.** A tool that raises is turned into a
tool *result* marked `is_error` and fed back. The model sees what went wrong
and can correct itself -- a wrong argument name or a stale project id costs one
step, not the turn.

**It does not repeat itself.** Every step, including the failures, is persisted
to the conversation. The next turn replays the whole transcript, so the model
can see it already listed the projects, already tried that id, already ran that
simulation.
"""

import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.provider import LLMError, LLMProvider
from app.ai.tools import ToolBox, ToolError
from app.models.conversation import Conversation, ConversationMessage, MessageRole

logger = logging.getLogger(__name__)

#: Ceiling on tool round-trips in a single user turn. Generous enough for
#: list -> inspect -> validate -> answer, tight enough that a confused model
#: cannot spin.
MAX_STEPS = 8

#: Tool output handed back to the model is truncated to this, so one chatty
#: result cannot crowd the rest of the transcript out of the context window.
MAX_TOOL_RESULT_CHARS = 6_000

AGENT_SYSTEM = """\
You are Kryova's engineering assistant. You help a mechanical engineer analyse \
parts: finding their projects and geometry, building load cases, running linear \
static FE analyses, and explaining results.

You have tools. Use them rather than guessing:

- Never invent an id. If the user names something in words, call the listing \
tool and match it. If nothing matches, say so and show what does exist.
- Never state a physics number you did not read from a tool result. You do not \
compute, convert or adjust stresses, factors of safety or masses -- the solver \
does that, and you report what it produced.
- Check before acting. Before running a simulation, confirm the geometry exists \
and the material is one the library actually has.
- A tool result marked as an error is information, not a dead end. Read it, fix \
what it tells you, and continue. Do not repeat the identical call.
- When you have enough to answer, answer. Do not keep calling tools to be sure.

The unit system is mm-N-MPa: lengths and displacements in millimetres, forces \
in newtons, stresses in megapascals, mass in kilograms. Nothing is converted \
anywhere.

Running a simulation costs real compute and takes minutes. Never submit one \
the user did not ask for. When a run is ready, say what will be analysed and \
what you assumed, and let them confirm.

Answer as an engineer talking to an engineer: lead with the outcome, keep it \
short, and say plainly when something is unknown or unverified.\
"""



#: Human labels for the step list in the UI. A user reading "list_geometry"
#: has to decode it; "Checking geometry versions" they can just read.
TOOL_LABELS: dict[str, str] = {
    "list_projects": "Looking up your projects",
    "list_materials": "Checking the material library",
    "list_geometry": "Checking geometry versions",
    "list_simulations": "Reviewing previous runs",
    "get_simulation": "Reading the simulation result",
    "run_simulation": "Preparing the analysis",
}


def _summarise(tool: str, result: Any, ok: bool) -> str:
    """One line describing what a tool actually found.

    The raw JSON goes to the model; this goes to the human, so it says
    "Found 3 projects" rather than dumping the payload into the UI.
    """
    if not ok:
        return str(result.get("error", "Failed"))[:200] if isinstance(result, dict) else "Failed"
    if not isinstance(result, dict):
        return "Done"

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
        return "Load case validated, ready to submit"
    return "Done"


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
    #: True when MAX_STEPS cut the loop off before the model finished.
    truncated: bool = False


def _serialise(value: Any) -> str:
    try:
        text = json.dumps(value, default=str, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        return text[:MAX_TOOL_RESULT_CHARS] + "\n...[truncated]"
    return text


def _transcript(conversation: Conversation) -> list[dict[str, Any]]:
    """Replay stored messages into the provider's normal form.

    This is the memory. Tool calls and their results are replayed too, not just
    the prose, so the model can see what it already tried.
    """
    messages: list[dict[str, Any]] = []
    for stored in conversation.messages:
        if stored.role is MessageRole.USER:
            messages.append({"role": "user", "content": stored.content or ""})
        elif stored.role is MessageRole.ASSISTANT:
            entry: dict[str, Any] = {"role": "assistant", "content": stored.content or ""}
            if stored.tool_calls:
                entry["tool_calls"] = stored.tool_calls
            messages.append(entry)
        elif stored.role is MessageRole.TOOL:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": stored.tool_call_id or "",
                    "name": stored.tool_name or "",
                    "content": stored.content or "",
                    "is_error": stored.is_error,
                }
            )
    return messages


def _append(
    db: Session, conversation: Conversation, role: MessageRole, **fields: Any
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=len(conversation.messages),
        role=role,
        **fields,
    )
    conversation.messages.append(message)
    db.add(message)
    db.flush()
    return message


def stream_agent(
    *,
    db: Session,
    provider: LLMProvider,
    conversation: Conversation,
    toolbox: ToolBox,
    user_message: str,
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
    _append(db, conversation, MessageRole.USER, content=user_message)

    steps: list[AgentStep] = []
    schemas = toolbox.schemas(include_mutating=allow_mutations)

    for step in range(MAX_STEPS):
        yield {"type": "thinking", "step": step + 1, "max_steps": MAX_STEPS}
        turn = provider.chat(
            system=AGENT_SYSTEM,
            messages=_transcript(conversation),
            tools=schemas,
            max_tokens=max_tokens,
        )

        if not turn.wants_tools:
            _append(db, conversation, MessageRole.ASSISTANT, content=turn.text)
            db.commit()
            yield {"type": "message", "content": turn.text}
            yield {
                "type": "done",
                "conversation_id": conversation.id,
                "truncated": False,
                "steps": len(steps),
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
                "label": TOOL_LABELS.get(call.name, call.name.replace("_", " ")),
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

            steps.append(
                AgentStep(tool=call.name, arguments=call.arguments, ok=ok, result=result)
            )
            _append(
                db,
                conversation,
                MessageRole.TOOL,
                content=_serialise(result),
                tool_call_id=call.id,
                tool_name=call.name,
                is_error=not ok,
            )
            yield {
                "type": "tool_end",
                "id": call.id,
                "tool": call.name,
                "arguments": call.arguments,
                "ok": ok,
                "result": result,
                "summary": _summarise(call.name, result, ok),
                "duration_ms": elapsed_ms,
            }

        db.commit()
        logger.debug("agent step %d: %d tool call(s)", step + 1, len(turn.tool_calls))

    # Out of steps. Ask for a final answer with tools withdrawn, so the user
    # gets the model's best summary instead of a bare "gave up".
    try:
        closing = provider.chat(
            system=AGENT_SYSTEM
            + "\n\nYou have run out of tool calls for this turn. Answer with what "
            "you have, and say plainly what is still unresolved.",
            messages=_transcript(conversation),
            tools=[],
            max_tokens=max_tokens,
        )
        text = closing.text
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
        "truncated": True,
        "steps": len(steps),
    }


def run_agent(
    *,
    db: Session,
    provider: LLMProvider,
    conversation: Conversation,
    toolbox: ToolBox,
    user_message: str,
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

    for event in stream_agent(
        db=db,
        provider=provider,
        conversation=conversation,
        toolbox=toolbox,
        user_message=user_message,
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

    return AgentReply(text=text, steps=steps, truncated=truncated)
