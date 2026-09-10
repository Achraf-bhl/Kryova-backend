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
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.ai import prompts
from app.ai.context import build_messages, maybe_summarise
from app.ai.malformed import correction_for, find_written_tool_calls, is_contentless
from app.ai.planning import extract_objectives
from app.ai.provider import LLMError, LLMProvider, TokenUsage
from app.ai.sanitise import MAX_TOOL_RESULT_CHARS, fence_tool_result
from app.ai.tools import ToolBox, ToolError
from app.ai.verification import (
    assess,
    measurements_in,
    shortfall_note,
    unverified_footnote,
)
from app.core import interruption
from app.core.config import settings
from app.models import Conversation, ConversationMessage, MessageRole, User
from app.retrieval import knowledge_service

logger = logging.getLogger(__name__)

#: Ceiling on tool round-trips in a single user turn.
#:
#: Raised 8 -> 20 -> 60. The reasoning behind the first two numbers was that a
#: step costs something, and here it does not: the provider is a local Ollama
#: model on the engineer's own machine, so the only price of another round is
#: wall-clock. Against that, the thing Kryova exists to build is a *machine*,
#: and a machine is not a dozen calls. Measured on the seat 2026-09-07, a
#: four-part punch press -- C-frame, ram, punch, die block, then a product,
#: three component adds and the constraints -- passed step 34 with the assembly
#: only starting. At 20 it could not have been reached from one prompt at all,
#: and the run before it ended on the cap with nothing built.
#:
#: A cap is a poor way to stop a stuck agent anyway, and it is no longer the way
#: it is done: `MAX_IDENTICAL_READS` refuses a read repeated verbatim,
#: `_refused_before` refuses a write already refused, and `MAX_BLOCKED_REPEATS`
#: ends the turn after a few of either. Those fire on *behaviour* and end a
#: looping turn in seconds regardless of what is left in the budget. So the cap
#: now only bites on an agent that is genuinely working, which is the one case
#: it should never have been deciding.
#:
#: Sixty steps is roughly twenty minutes of wall-clock at this model's ~20 s per
#: round. `AI_MAX_STEPS` (or the `ai_max_steps` setting) moves it without a code
#: change -- see `max_steps`.
DEFAULT_MAX_STEPS = 60

#: How many times *in a row* the model may be told to re-issue a tool call it
#: wrote as prose, or to answer at all after returning nothing. Two is enough to
#: clear a one-off formatting slip; a model that needs more is not going to get
#: there, and every retry is time the user spends watching a spinner.
#:
#: **Consecutive, not lifetime.** It was a lifetime count until 2026-09-06, and
#: ladder prompt H4 turn 2 measured what that costs. The model went blank at
#: step 3 and again at step 6; both were corrected and both recovered
#: immediately, doing real work on the steps that followed. That spent the
#: whole budget, so the blank at step 11 had nothing left and ended the turn --
#: with five tool calls of work done and no write-up, on a turn that had nine
#: of its twenty steps still unused.
#:
#: The budget exists to stop a stuck model looping, and a blank the model
#: recovered from is not evidence of a stuck model. So a step that actually
#: calls a tool clears the count, and only blanks with nothing in between add
#: up. A genuinely stuck model still stops after two, because it never gets a
#: successful step to reset it.
MAX_CORRECTIONS = 2


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


#: Human labels for the step list in the UI. A user reading "list_geometry"
#: has to decode it; "Checking geometry versions" they can just read.
TOOL_LABELS: dict[str, str] = {
    "create_project": "Creating the project",
    "list_projects": "Looking up your projects",
    "list_materials": "Checking the material library",
    "search_documentation": "Checking the documentation",
    "list_geometry": "Checking geometry versions",
    "list_simulations": "Reviewing previous runs",
    "get_simulation": "Reading the simulation result",
    "run_simulation": "Preparing the analysis",
    "catia_status": "Checking CATIA",
    "open_in_catia": "Opening CATIA",
    "sync_geometry_from_catia": "Importing geometry from CATIA",
}


def system_prompt() -> str:
    """The frozen prefix for this deployment.

    Four whole constants rather than one prompt assembled from optional
    sections. The point of freezing these strings is that a given deployment
    sends byte-identical prefix text on every turn, which is what a provider's
    prompt cache keys on; building the prompt by concatenation at call time
    would work equally well right up until someone made a section depend on
    something that varies per turn, and then the cache would silently never hit.

    The two axes are independent -- CATIA on or off, reference manuals present
    or not -- so there are four, chosen once here.

    The documentation axis is decided by whether an index actually exists, not
    by the setting alone: `_build_knowledge` withholds the tool on the same
    condition, and a prompt that describes a tool the model has not been given
    is a prompt that teaches it to hallucinate a call.
    """
    has_docs = settings.knowledge_enabled and knowledge_service().available
    if settings.catia_enabled:
        return prompts.AGENT_SYSTEM_CATIA_DOCS if has_docs else prompts.AGENT_SYSTEM_CATIA
    return prompts.AGENT_SYSTEM_DOCS if has_docs else prompts.AGENT_SYSTEM


def _shown_tools(toolbox: Any, user_message: str) -> set[str] | None:
    """Which tools to put in front of the model this turn — master plan 16.1.

    `None` means all of them, which is the default and what every deployment did
    before this existed: retrieval is opt-in via `AI_TOOL_LIMIT` because it
    changes what the model sees, and a change to that must be measured before it
    is switched on rather than after.

    It narrows the *offer* only. `ToolBox.call` still accepts every tool, so
    nothing here can make a capability unreachable — the worst case is a turn
    where the model has to name a tool from memory instead of reading it, and
    that call still works.

    Never raises. A retrieval failure falls back to offering everything, on
    `KnowledgeService.search`'s contract: consulting an index may improve an
    answer and must never be the reason there is not one.
    """
    limit = getattr(settings, "ai_tool_limit", 0)
    if not limit:
        return None
    try:
        from app.ai.tool_retrieval import select

        specs = [
            SimpleNamespace(name=tool.name, description=tool.description)
            for tool in toolbox.every_tool()
        ]
        if len(specs) <= limit:
            return None
        selection = select(
            specs,
            user_message,
            recent=toolbox.recent_tool_names(),
            context=toolbox.recent_user_messages(),
            limit=limit,
        )
        # Logged rather than discarded, because the failure this can cause is
        # silent: a needed tool is absent, the model does something else, and
        # the part comes out wrong with every call returning `ok`. The only way
        # that gets diagnosed after the fact is from a record of what the offer
        # was and which rule shaped it.
        logger.info("tool retrieval: %s", selection.to_dict())
        return selection.names()
    except Exception:  # pragma: no cover - retrieval must never break a turn
        logger.exception("tool retrieval failed; offering the whole registry")
        return None


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
    if tool == "search_documentation":
        passages = result.get("passages", [])
        if not passages:
            return "Nothing in the manuals on that"
        # Name the document rather than counting passages. "3 passages" tells
        # the user nothing they can act on; "Part Design, p. 147" is a place
        # they can go and read for themselves.
        first = passages[0]
        where = f"{first.get('source', 'the manuals')}, p. {first.get('page', '?')}"
        extra = f" (+{len(passages) - 1} more)" if len(passages) > 1 else ""
        return f"Found {where}{extra}"
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
        return (
            f"Queued run {result.get('id', '')}".strip() or "Load case validated, ready to submit"
        )
    if tool == "delete_simulation":
        return "Run deleted"
    if tool == "design_history":
        # Says how much of the record was read and how much was left, because
        # this is the one tool whose answer is deliberately partial -- a user
        # watching the step list should be able to see that it paged.
        unresolved = len(result.get("unresolved") or [])
        older = result.get("older_not_shown") or 0
        summary = f"{result.get('returned', 0)} of {result.get('total', 0)} operation(s)"
        if older:
            summary += f", {older} older not shown"
        return summary + (f", {unresolved} unfinished" if unresolved else "")
    if tool == "catia_status":
        if not result.get("running", result.get("connected")):
            return "CATIA is not running"
        active = result.get("active_document") or "no document"
        return f"CATIA {result.get('version', result.get('catia_version', ''))} up, {active}"
    if tool == "open_in_catia":
        created = result.get("created_document")
        return "CATIA open" + (f", created {created}" if created else "")
    if tool == "sync_geometry_from_catia":
        return (
            f"Imported version {result.get('geometry_version', result.get('geometry_version_id', ''))} "
            f"({result.get('filename', 'geometry')})"
        )
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
    if result.get("cut"):
        # A cut's reach, in the flow list, because "Done" is what let a 25 mm
        # bore be described as going through a 45 mm part -- ladder prompt S5
        # turn 2, 2026-09-06. The user reads this line; the model reads the
        # payload; both now say the same thing.
        parts.append(str(result["cut"]))
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


def _blank_turn_message(steps: list[AgentStep], labels: dict[str, str]) -> str:
    """What to say when the model finished with no text of its own.

    The distinction this draws is the whole point. A turn that called nothing
    and wrote nothing produced nothing, and saying so is accurate. A turn that
    built a part and then wrote nothing produced a part -- and telling that user
    "I did not manage to produce an answer" is a false statement about their
    workstation, which they will act on by asking again and getting a second
    copy of the part.

    Measured on ladder prompt H2, 2026-09-06: twelve steps, a correct flange,
    and that sentence.

    The work is listed rather than summarised because summarising it is exactly
    what the model just failed to do; repeating the step labels is something
    this function can be sure is true.
    """
    done = [step for step in steps if step.ok]
    if not done:
        return (
            "I did not manage to produce an answer for that. Nothing was run, so "
            "nothing has changed. Try asking again, or more specifically."
        )

    performed = []
    for step in done:
        label = labels.get(step.tool, step.tool.replace("_", " "))
        if label not in performed:
            performed.append(label)

    return (
        "**The work below ran, but I did not manage to write up the result.** "
        "Nothing needs redoing -- ask me to describe what I built, or to check "
        "it, rather than asking for it again.\n\n"
        + "\n".join(f"- {label}" for label in performed)
    )


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

    # Any stop left over from an earlier turn is spent. Without this one press
    # of stop would end every turn after it, instantly, each looking to the user
    # like the product refusing to work.
    interruption.clear_turn_stop(db, conversation)

    _append(db, conversation, MessageRole.USER, content=user_message)

    # Fold before building the window, so the material being folded is still
    # present to be read and the window that follows is already compacted.
    usage += maybe_summarise(db, provider, conversation)

    steps: list[AgentStep] = []
    #: Read-only calls made this turn, by fingerprint, so a loop is caught.
    #: Per turn rather than per conversation: re-reading the part on a later
    #: turn is exactly right, because by then something may have changed it.
    reads: dict[str, int] = {}
    #: Writes that were refused this turn, by fingerprint, with the reason. A
    #: refused write ran nothing, so repeating it verbatim is the same dead end
    #: as repeating a read -- see `_refused_before`.
    refusals: dict[str, str] = {}
    #: How many calls this turn were turned back for being repeats. See
    #: MAX_BLOCKED_REPEATS: past a few, the budget is better spent ending the
    #: turn than on more of them.
    blocked = 0
    schemas = toolbox.schemas(
        include_mutating=allow_mutations, only=_shown_tools(toolbox, user_message)
    )
    known = set(labels)
    corrections = 0
    #: How many times this turn was held open for unmeasured requirements.
    #: See MAX_VERIFICATION_NUDGES.
    nudges = 0
    #: Consecutive rounds in which nothing was built. See
    #: MAX_READS_WITHOUT_PROGRESS -- a loop with varying arguments is still
    #: a loop, and this is the only counter that can see one.
    barren = 0
    #: Every feature or document this turn actually created, for the moment
    #: the model has to be reminded it is not starting from nothing.
    built: list[str] = []
    #: Documents opened and solids produced this turn. See
    #: MAX_EMPTY_DOCUMENTS -- these are counted apart because creating a
    #: document is the one mutation that changes nothing about the part.
    documents_created = 0
    solids_built = 0
    #: Whether the empty-document nudge has already been given this turn.
    warned_about_empty_documents = False
    #: Why the loop stopped, for the user-facing line at the end of a turn that
    #: did not finish. Two exits reach the same closing code -- falling out of
    #: the step budget, and breaking on repeated blocked calls -- and until
    #: 2026-09-08 both were reported as "ran out of tool rounds".
    #:
    #: Measured on ladder prompt PRO1: the turn ended at step 31 of 60 because
    #: the agent kept re-issuing a read the tool layer had already refused, and
    #: the user was told it had run out of rounds and should "ask for one thing
    #: at a time". Neither half was true -- half the budget was unspent, and
    #: asking for less would not have stopped the repeat. Advice that does not
    #: match the cause sends the user to change the one thing that was fine.
    stop_reason = "step_budget"

    for step in range(budget):
        # P5 task 6, checked here and nowhere else in the loop. Before the model
        # call, so a stop is not followed by one more paid round trip; after the
        # previous step's tools have finished, so nothing is left half-applied.
        if interruption.turn_stop_requested(db, conversation):
            logger.info("turn stopped on request at step %d/%d", step + 1, budget)
            _append(
                db, conversation, MessageRole.ASSISTANT, content=interruption.TURN_STOPPED_MESSAGE
            )
            db.commit()
            yield {"type": "message", "content": interruption.TURN_STOPPED_MESSAGE}
            yield {
                "type": "done",
                "conversation_id": conversation.id,
                "project_id": toolbox.project_id,
                # True: the turn did not reach an answer. The frontend uses this
                # to decide whether to offer "continue", which is exactly what a
                # stopped turn should offer.
                "truncated": True,
                "stop_reason": "cancelled",
                "steps": len(steps),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            }
            return

        yield {"type": "thinking", "step": step + 1, "max_steps": budget}
        # A turn is the model thinking plus the tools running, and the two are
        # optimised in completely different places -- one is GPU offload and
        # context size, the other is COM round trips to CATIA. Timed
        # separately, because a total tells you a turn was slow and nothing
        # about which half to look at.
        thinking_started = time.perf_counter()
        turn = provider.chat(
            system=system,
            messages=build_messages(db, owner, conversation),
            tools=schemas,
            max_tokens=max_tokens,
        )
        thinking_ms = (time.perf_counter() - thinking_started) * 1000.0
        usage += turn.usage
        step_timings: list[tuple[str, float]] = []

        if not turn.wants_tools:
            # A turn with no tool calls is the model saying it is finished, and
            # whatever it wrote becomes the answer. Two ways that is a lie, and
            # both reach the user as a normal reply unless they are caught here.
            written = find_written_tool_calls(turn.text, known)
            blank = is_contentless(turn.text)
            if (written or blank) and corrections < MAX_CORRECTIONS:
                corrections += 1
                # The model's own words go in first: it has to see what it
                # actually produced, or the correction is about nothing.
                _append(db, conversation, MessageRole.ASSISTANT, content=turn.text or None)
                _append(
                    db,
                    conversation,
                    MessageRole.USER,
                    content=prompts.CONTROL_NOTE + (
                        correction_for(written)
                        if written
                        else prompts.AGENT_EMPTY_TURN_AFTER_WORK
                        if any(s.ok for s in steps)
                        else prompts.AGENT_EMPTY_TURN
                    ),
                )
                db.commit()
                logger.info(
                    "correcting turn: %s (attempt %d/%d)",
                    f"tool call written as text: {written}" if written else "empty response",
                    corrections,
                    MAX_CORRECTIONS,
                )
                continue
            if blank:
                # Out of corrections and still nothing. Say so, rather than
                # closing the turn with an empty chat bubble -- but say it
                # about the *writing up*, not about the work, because those
                # are not the same thing and only one of them failed.
                turn.text = _blank_turn_message(steps, labels)
            elif written:
                # Out of corrections, and the text still describes work that was
                # never done. Showing it as written would tell the user their
                # part was built. Keep it, and say plainly that it did not run.
                turn.text = (
                    "I wrote out the steps below instead of running them, so "
                    "**nothing has actually been done** -- no part was created "
                    "and no file was changed. Ask me to run it and I will try "
                    "again.\n\n" + turn.text
                )

            # The model says it is finished. Before that is accepted, compare
            # what it was asked for against what it actually measured. This is
            # the check that was missing when a turn closed on six requirements
            # with three built, and again when the one thing the user asked
            # about -- was there interference -- was never checked at all.
            plan = _requirement_plan(conversation, steps)
            shortfall = shortfall_note(plan)
            # Reaching here with `written` or `blank` still set means the
            # correction budget is spent, and the two branches above have just
            # replaced `turn.text` with the only honest account of the turn --
            # "nothing has actually been done", or "I did not manage to produce
            # an answer". **Holding the turn open would throw that away.** The
            # loop would `continue`, the next turn would close normally, and the
            # user would be shown that turn's text with a verification footnote
            # instead: "Done." over a model that ran nothing and said it had.
            # That is precisely the silent failure `tests/test_written_tool_calls.py`
            # exists to prevent, reintroduced from a different direction -- and
            # the nudge cannot help anyway, since it asks the model to go and
            # measure something and this model has just failed MAX_CORRECTIONS
            # times to emit a tool call at all. The footnote below still lands:
            # it adds what went unverified without contradicting the message.
            exhausted = bool(written or blank)
            if shortfall and not exhausted and nudges < MAX_VERIFICATION_NUDGES and step + 1 < budget:
                nudges += 1
                _append(db, conversation, MessageRole.ASSISTANT, content=turn.text or None)
                _append(
                    db, conversation, MessageRole.USER,
                    content=prompts.CONTROL_NOTE + shortfall,
                )
                db.commit()
                logger.info(
                    "holding the turn open: %d requirement(s) unverified at step %d/%d",
                    len(plan.outstanding()) + len(plan.missed),
                    step + 1,
                    budget,
                )
                yield {"type": "verification", "outstanding": plan.to_dict()}
                continue

            text = turn.text
            if shortfall:
                # It was told, and it closed anyway. The answer stands as
                # written; what it left out is stated beside it, because the
                # user cannot see the difference between measured and assumed.
                text += unverified_footnote(plan)
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
                "stop_reason": "finished",
                "steps": len(steps),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            }
            return

        # The model asked for tools, so whatever went wrong on an earlier step
        # is behind it. See MAX_CORRECTIONS: the budget counts consecutive
        # failures, because a blank the model recovered from says nothing about
        # whether it is stuck now.
        corrections = 0

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
                # A read repeated verbatim cannot tell the model anything it was
                # not told the first time, and a model that does it three times
                # is looping rather than working. See MAX_IDENTICAL_READS.
                looping = (
                    _refused_before(call.name, call.arguments, refusals)
                    if toolbox.is_mutating(call.name)
                    else _looping_on(call.name, call.arguments, reads)
                )
                if looping is not None:
                    blocked += 1
                    raise ToolError(looping)
                result: Any = toolbox.call(
                    call.name, call.arguments, allow_mutations=allow_mutations
                )
                ok = True
            except ToolError as exc:
                result, ok = {"error": str(exc)}, False
                if toolbox.is_mutating(call.name):
                    refusals.setdefault(
                        _read_fingerprint(call.name, call.arguments), str(exc)
                    )
            except Exception as exc:  # noqa: BLE001 - must not kill the turn
                logger.exception("Tool %s raised", call.name)
                result, ok = {"error": f"{type(exc).__name__}: {exc}"}, False
            elapsed_ms = int((time.monotonic() - started) * 1000)
            step_timings.append((call.name, float(elapsed_ms)))

            steps.append(AgentStep(tool=call.name, arguments=call.arguments, ok=ok, result=result))
            if ok and isinstance(result, dict):
                # Which of the two happened is the whole of the empty-document
                # guard below. Every solid-producing operation comes back
                # through `_feature_result`, which carries `feature`; opening a
                # document carries `doc_name` and no solid. Counting them
                # together is what let five empty parts look like five pieces
                # of progress.
                if result.get("feature"):
                    solids_built += 1
                elif call.name in _DOCUMENT_TOOLS:
                    documents_created += 1
            if ok and toolbox.is_mutating(call.name):
                made = result.get("feature") or result.get("doc_name") if isinstance(result, dict) else None
                if made and str(made) not in built:
                    built.append(str(made))
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

        # A round that built nothing is not necessarily wrong; six in a row are
        # a loop, whatever their arguments were.
        if _made_progress(steps[-len(turn.tool_calls) :], toolbox.is_mutating):
            barren = 0
        else:
            barren += 1
            if barren >= MAX_READS_WITHOUT_PROGRESS:
                _append(
                    db,
                    conversation,
                    MessageRole.USER,
                    content=prompts.CONTROL_NOTE + _reading_in_circles(barren, built),
                )
                barren = 0

        # Opening documents is not the same failure as reading in circles and
        # is invisible to the counter above, because each `catia_new_part`
        # succeeds and resets it. See MAX_EMPTY_DOCUMENTS.
        if (
            not warned_about_empty_documents
            and solids_built == 0
            and documents_created > MAX_EMPTY_DOCUMENTS
        ):
            warned_about_empty_documents = True
            logger.info(
                "nudging: %d document(s) opened, no solid built, at step %d/%d",
                documents_created,
                step + 1,
                budget,
            )
            _append(
                db,
                conversation,
                MessageRole.USER,
                content=prompts.CONTROL_NOTE + _nothing_built_yet(documents_created),
            )

        db.commit()
        if blocked >= MAX_BLOCKED_REPEATS:
            # Out of patience rather than out of budget. Ending here leaves the
            # remaining rounds unspent and gets the user a summary and a
            # question, instead of the same refusal until the cap.
            logger.info(
                "ending the turn after %d blocked repeat(s) at step %d/%d",
                blocked,
                step + 1,
                budget,
            )
            stop_reason = "repeated_calls"
            break

        # INFO, not DEBUG. This is the one line that says where a turn's time
        # went, and a number nobody can see is a number nobody optimises --
        # the whole reason the GPU offload defect survived as long as it did.
        logger.info(
            "agent step %d/%d: model %.0f ms, %d tool call(s)%s, %d prompt tokens",
            step + 1,
            budget,
            thinking_ms,
            len(step_timings),
            (" [" + ", ".join(f"{name} {ms:.0f}ms" for name, ms in step_timings) + "]")
            if step_timings
            else "",
            turn.usage.prompt_tokens,
        )

    # Out of steps. Ask for a final answer with tools withdrawn, so the user
    # gets the model's best summary instead of a bare "gave up".
    #
    # The requirement ledger applies here too, and this is the path where it
    # matters most. A turn that ends on the round cap has by definition not
    # finished, and the closing summary is written with the tools already taken
    # away -- so the model cannot measure anything even if it wanted to, and
    # what it produces is its best recollection. Measured on ladder prompt PRO4,
    # 2026-09-07: seventeen steps, a C-frame built, and not one of the stated
    # requirements -- the punching force, the lever ratio, whether the frame is
    # stiff enough -- computed or checked. Without this the summary goes out
    # with nothing saying so.
    plan = _requirement_plan(conversation, steps)
    shortfall = shortfall_note(plan)
    try:
        closing = provider.chat(
            system=system + prompts.AGENT_OUT_OF_STEPS,
            messages=build_messages(db, owner, conversation)
            + (
                [{"role": "user", "content": prompts.CONTROL_NOTE + shortfall}]
                if shortfall
                else []
            ),
            tools=[],
            max_tokens=max_tokens,
        )
        text = closing.text
        usage += closing.usage
    except LLMError:
        # The fallback follows the same rule as the banner: say which of the two
        # happened, because the remedies are opposite. Out of rounds means the
        # request was too big for one turn; repeating a refused call means it
        # was stuck, and asking for less would not have helped.
        text = (
            "I stopped because I kept repeating a call that had already been "
            "refused, and re-sending it could not change the answer. Tell me "
            "what to do differently and I will carry on from what is built."
            if stop_reason == "repeated_calls"
            else "I used all my tool calls for this turn without reaching an answer. "
            "Try narrowing the question."
        )
    if shortfall:
        text += unverified_footnote(plan)

    _append(db, conversation, MessageRole.ASSISTANT, content=text)
    db.commit()
    yield {"type": "message", "content": text}
    yield {
        "type": "done",
        "conversation_id": conversation.id,
        "project_id": toolbox.project_id,
        "truncated": True,
        "stop_reason": stop_reason,
        "steps": len(steps),
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
    }


#: How many times one read-only call may be repeated, byte for byte, inside a
#: single turn before the tool layer refuses it.
#:
#: Measured on ladder prompt H4, 2026-09-06, on the seat. From step 12 the
#: agent ran `catia_select` -> `design_history` -> `catia_list_features` ->
#: `catia_select` -> `design_history` -> `catia_select` -> `design_history`.
#: Nine of its twenty rounds, every call succeeding, every call returning
#: exactly what it had returned before, and no geometry built. The turn ended
#: on the round cap with a rectangle and a polygon in one sketch and nothing
#: extruded.
#:
#: A read that has already been answered cannot answer anything new -- the part
#: has not changed, because reading it changed nothing. So the second identical
#: call is served with a note, and the third is refused in words that say what
#: to do instead. Two, not one, because a legitimate re-read does happen: a
#: model checks a list, acts, and checks it again. What never happens
#: legitimately is the same read three times with nothing between them.
#:
#: Only reads. A repeated *write* is a different question -- it may be a
#: deliberate second hole -- and the mutating tools have their own guards.
MAX_IDENTICAL_READS = 2

#: How many calls a turn may spend being turned back by the two guards above
#: before the turn ends instead of spending the rest of its budget.
#:
#: The guards stop the *work*; they do not stop the *rounds*. Measured on the
#: seat 2026-09-06, ladder prompt S2: `catia_new_part` was refused, correctly,
#: and then called six more times. Each refusal took 0 ms and cost a round, so
#: a guard written to save rounds spent seven of twenty.
#:
#: A model that has been told three times that a call is already answered is
#: not going to be told a fourth time to any effect. Ending the turn hands the
#: user a summary and a question, which is Phase 16.4's escalate step, and it
#: is strictly better than burning the remaining budget in silence.
MAX_BLOCKED_REPEATS = 3


#: How many times in a turn the model may be sent back to measure a requirement
#: it stated it had met -- or never mentioned again -- before the turn closes
#: anyway with the omission written into the answer.
#:
#: One, and only one. The nudge is not a negotiation: the model is handed the
#: exact clauses nothing has measured and the tools to measure them. A model
#: that comes back a second time without having measured them is not going to
#: on a third, and every extra round is the user watching a spinner. What
#: happens instead is strictly better than another round -- the answer goes out
#: with the unverified requirements listed under it, so the user sees what was
#: skipped even when the model does not say so.
MAX_VERIFICATION_NUDGES = 1


def _requirement_plan(conversation: Conversation, steps: list[AgentStep]) -> Any:
    """What this conversation asked for, marked against what it measured.

    Both halves are read from records rather than from the transcript, for the
    reason `resume.py` gives: the window trims from the front and the summary is
    a paraphrase, so by the time a long turn is closing, the requirement stated
    in the first message may no longer be in the context at all. The objectives
    come from every user message; the measurements come from the tool results of
    this turn, which are the only ones that can have measured anything new.
    """
    objectives: list[Any] = []
    seen: set[str] = set()
    for message in conversation.messages:
        if message.role != MessageRole.USER or not message.content:
            continue
        for objective in extract_objectives(str(message.content)):
            key = " ".join(objective.text.lower().split())
            if key not in seen:
                seen.add(key)
                objectives.append(objective)

    measured = []
    for record in steps:
        if record.ok:
            measured.extend(measurements_in(record.result, record.tool))
    return assess(objectives, measured)


#: How many consecutive rounds may read without anything being built before the
#: model is told so.
#:
#: `MAX_IDENTICAL_READS` catches a call repeated byte for byte, and it cannot
#: catch the shape measured on the punch-press prompt, 2026-09-07: fifteen
#: rounds alternating `catia_open_document` with `catia_list_faces`, a different
#: document each time, so no two calls were identical and nothing tripped. The
#: agent was hunting for a face to constrain against, in a vocabulary where only
#: origin planes resolve by name, and the turn died with four parts built, an
#: assembly holding all four, and not one constraint.
#:
#: So the question is not "have I seen this call before" but "has anything
#: changed since I started reading". Six is chosen to sit above legitimate
#: reading -- listing a part, measuring it, reading the tree and the faces
#: before deciding is four or five -- and below the point where a turn is spent.
MAX_READS_WITHOUT_PROGRESS = 6


#: The tools that open a new CATIA document and build nothing in it.
_DOCUMENT_TOOLS = frozenset({"catia_new_part", "catia_product_create"})


#: How many documents a turn may open before it has put a single solid in any
#: of them.
#:
#: **Measured on ladder prompt PRO1, 2026-09-08, and the agent diagnosed itself
#: in its own closing words: "We have 5 empty part documents created but no
#: geometry built yet."** Thirty-one steps, five parts, six sketches, and not
#: one pad. It kept answering "produce the frame, the ram, the rack, the pinion
#: and the table as parts" by producing the *documents* those parts would live
#: in, which is the shape of the request rather than the substance of it.
#:
#: Neither existing guard can see this. `MAX_IDENTICAL_READS` needs a repeated
#: call and these have different names each time; `MAX_READS_WITHOUT_PROGRESS`
#: asks whether anything mutated, and `catia_new_part` *is* a successful
#: mutation, so it reset the barren counter on every one of the five. Creating
#: a document is the single mutation that changes nothing about the part -- it
#: is the container, not the content -- and it is exactly the one that was
#: being mistaken for progress.
#:
#: Two, not one: opening a part and then a product to hold it is an ordinary
#: opening move, and refusing that would refuse every assembly. Three empty
#: documents is not a plan.
#:
#: A nudge rather than a refusal, and once rather than every time. The model is
#: not doing anything forbidden -- it is doing something useless in an order
#: that will not converge -- and the same reasoning as MAX_VERIFICATION_NUDGES
#: applies: a model that ignores this once will ignore it twice, and the rounds
#: are better spent letting it build.
MAX_EMPTY_DOCUMENTS = 2


def _nothing_built_yet(documents: int) -> str:
    """What to say to a turn that has opened documents and filled none.

    Names the remedy in the order the tools have to be called, because the
    failure is not that the model does not know what a pad is -- it is that it
    is working breadth-first across a parts list and never reaching the bottom
    of one. Telling it to "build something" produces another sketch; telling it
    to finish *one* part produces a solid.
    """
    return (
        f"You have opened {documents} CATIA documents this turn and built no solid "
        "in any of them. A part with no solid is an empty file -- it is not the "
        "part, and the requirement it stands for is still unmet.\n\n"
        "Finish one document before opening another: create the sketch, draw the "
        "closed profile, then call catia_pad on the name the sketch tool returned. "
        "Measure it, and only then move to the next part. Opening more documents "
        "cannot make the ones you have any less empty."
    )



def _made_progress(records: list["AgentStep"], is_mutating: Any) -> bool:
    """Whether this round changed anything, as opposed to only looking.

    Both halves are needed and each has been the reason a guard misfired. A
    call that *would* mutate but was refused changed nothing -- the part is
    exactly as it was, so a turn full of refused pads is as barren as a turn
    full of reads. And a call that succeeded but only reads changed nothing
    either, which is the case this exists for.
    """
    return any(record.ok and is_mutating(record.tool) for record in records)


def _reading_in_circles(rounds: int, built: list[str]) -> str:
    """What to say to a model that has read for `rounds` without building.

    Names what it already has, because the failure is not ignorance of the part
    -- it has read the part repeatedly -- it is not knowing that reading is not
    the move. Written as an instruction with an alternative in it: told only to
    stop, a model tries the neighbouring read, which is the same loop one tool
    over.
    """
    return (
        f"The last {rounds} calls all read something and changed nothing. Reading "
        "again cannot help: nothing has altered between them, and it will not now. "
        + (f"You have already built: {', '.join(built)}. " if built else "")
        + "Decide with what you have. If you were looking for geometry to "
        "reference and could not find it, use the tool that needs no reference -- "
        "a component can be placed at a coordinate with catia_component_move, and "
        "an assembly constraint takes a component's origin planes by name. If you "
        "genuinely cannot proceed, say what is blocking you and stop."
    )


def _read_fingerprint(name: str, arguments: Any) -> str:
    """A stable key for one read-only call and its arguments."""
    try:
        return name + ":" + json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover - arguments are JSON already
        return name + ":" + repr(arguments)


def _looping_on(name: str, arguments: Any, seen: dict[str, int]) -> str | None:
    """The refusal for a call that has been made too many times, or None.

    Written as an instruction rather than as a complaint, because the model's
    response to a bare "no" is to try a neighbouring call, which is the same
    loop one tool over.
    """
    key = _read_fingerprint(name, arguments)
    seen[key] = seen.get(key, 0) + 1
    if seen[key] <= MAX_IDENTICAL_READS:
        return None
    return (
        f"You have already called {name} with these exact arguments "
        f"{seen[key] - 1} times in this turn, and the answer has not changed -- "
        "reading something does not alter it, and a call that was refused "
        "changed nothing either. Nothing further will come from asking again. "
        "Use what you were told the first time: act on it, or say what you "
        "found and what you are going to do about it."
    )


def _refused_before(name: str, arguments: Any, refusals: dict[str, str]) -> str | None:
    """The refusal for a *write* that was already refused with these arguments.

    The read guard above exempts mutating tools, because a repeated write can be
    a deliberate second hole. A repeated *refused* write cannot: the call did
    not run, so nothing about the part is different, and sending it again gets
    the same answer for the same reason.

    Measured on ladder prompt S1, 2026-09-06, on the seat. `catia_new_part` was
    refused at step 10 -- "this conversation already owns the CATIA document
    'Steel counterweight'" -- and sent again, byte for byte, at step 19, for the
    same refusal. Two of twenty rounds on a call that had already been answered,
    on a turn that ended out of rounds with a 62.88 kg block against a 2.4 kg
    target.

    The original refusal is repeated first, because it is the useful half and
    the model plainly did not act on it the first time.
    """
    key = _read_fingerprint(name, arguments)
    previous = refusals.get(key)
    if previous is None:
        return None
    return (
        f"{previous}\n\nThis is the second time {name} has been called with "
        "these exact arguments and it was refused for this reason the first "
        "time. The call did not run, so nothing has changed and it will not "
        "run now. Do what the refusal above says, or tell the user what is "
        "blocking you."
    )


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
