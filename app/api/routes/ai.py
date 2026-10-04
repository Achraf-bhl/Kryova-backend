"""AI endpoints.

The routes are thin: resolve and authorise the row, hand it to the service,
translate provider failures into HTTP. The model that answers is chosen by
configuration -- see `app/ai/providers/`.

Two things live here rather than deeper, on purpose. **Token budgets** are
checked at the route, alongside every other authorisation decision, because a
service that enforced its own quota would be enforcing it differently depending
on which caller reached it. And **wiring** -- the job queue, the media service,
the session scope -- is injected here, so the toolbox can submit a real
simulation without importing a queue itself.
"""

import json
import logging
import time
from collections.abc import Iterator
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.ai import (
    Completion,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    LoadCaseDraft,
    ResultInterpretation,
    branching,
    continuation,
    draft_load_case,
    generate_title,
    get_provider,
    interpret_result,
    org_budget,
    prompts,
    turn_events,
)
from app.ai import usage as token_usage
from app.ai.agent import AgentReply, run_agent, stream_agent, summarise_step
from app.ai.prompts import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from app.ai.provider import TokenUsage
from app.ai.resume import catia_activity
from app.ai.state import bound_document_name
from app.ai.tools import ToolBox, tool_label
from app.ai.turn_metrics import STOP_DISCONNECTED, STOP_ERROR, TurnMeter, record_turn
from app.api.deps import (
    CurrentUser,
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    OwnedProject,
    SessionScopeDep,
)
from app.api.rate_limit import RateLimit
from app.core import interruption
from app.core.config import settings
from app.core.metering import Cause, LedgerSink, record_tokens, usage_scope
from app.models import (
    Conversation,
    ConversationMessage,
    GeometryVersion,
    JobStatus,
    MessageRole,
    SimulationJob,
    User,
)
from app.simulation.runner import SessionScope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


class DayUsageRead(BaseModel):
    """What this user has spent on the model today (UTC), split the way it is billed."""

    prompt_tokens: int
    completion_tokens: int
    #: A subset of `prompt_tokens`, never added to it.
    cached_prompt_tokens: int
    #: Micro-dollars (1e-6 USD) over the calls that could be priced. Integers, because
    #: a period total is an exact sum and a float drifts.
    cost_micro_usd: int
    #: Calls on a model with no configured price, which `cost_micro_usd` cannot include.
    unpriced_calls: int


class UserAllowanceRead(BaseModel):
    #: 0 means unlimited. Resolved through the tenant's override, then the global setting.
    daily_token_budget: int
    #: 0 means unlimited.
    daily_cost_budget_micro_usd: int


class OrgCapRead(BaseModel):
    period: str = Field(description="`day` or `month` (UTC).")
    #: 0 means no cap in force.
    cap_micro_usd: int
    #: `tenant override` or `global settings`.
    source: str
    spent_micro_usd: int
    #: Whole percent of the cap spent; null when there is no cap.
    percent: int | None
    resets_on: date


class OrgNoticeRead(BaseModel):
    """One line for the in-app banner."""

    period: str
    percent: int
    level: str = Field(description="`warning` from 80 %, `exhausted` at 100 %.")
    message: str


class AIUsageRead(BaseModel):
    today: DayUsageRead
    allowance: UserAllowanceRead
    organisation_id: str | None
    organisation_caps: list[OrgCapRead]
    #: This month's calls on an unpriced model: a cap cannot see them, so say so.
    organisation_unpriced_calls: int
    notices: list[OrgNoticeRead]
    #: Why the next turn would be refused right now, or null if it would not be.
    blocked: str | None


class AIStatus(BaseModel):
    """Whether the AI features can serve a request right now."""

    enabled: bool
    provider: str
    model: str
    detail: str | None = Field(
        default=None, description="Why it is unavailable, and how to fix it."
    )


class LoadCaseRequest(BaseModel):
    description: str = Field(
        min_length=3,
        max_length=2_000,
        description="Plain language, e.g. 'clamp the bottom and hang 40 kg off the top face'.",
    )
    geometry_version: int | None = Field(
        default=None, description="Defaults to the project's latest version."
    )


def _provider_or_503() -> LLMProvider:
    """Build the configured provider, or explain what is wrong with it."""
    try:
        provider = get_provider()
        provider.health()
    except LLMUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return provider


def _translate(exc: LLMError) -> HTTPException:
    if isinstance(exc, LLMUnavailable):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, LLMRefusal):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def _enforce_budget(db: Session, user: User, project_id: str | None = None) -> None:
    """Refuse a turn that starts over the daily allowance or its organisation's cap.

    Checked before the call, never during: an agent cut off between a tool call
    and its result leaves a transcript describing work whose outcome nobody
    saw, which is worse for the user than a slightly overrun budget.

    `project_id` says which organisation's cap applies (`org_budget.billed_organisation`).
    """
    refused = token_usage.refusal(db, user, project_id)
    if refused is not None:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=refused)


def _turn_project_id(db: Session, user: User, payload: "ChatRequest") -> str | None:
    """The project a chat turn will be billed under, read *before* the conversation exists.

    An existing conversation answers with its own project (the user's own rows only --
    this is a read for billing, not an ownership check, and `_resolve_conversation`
    still 404s a stranger's id); a new one with the project the request names.
    """
    if payload.conversation_id:
        existing = db.scalar(
            select(Conversation.project_id).where(
                Conversation.id == payload.conversation_id,
                Conversation.owner_id == user.id,
            )
        )
        if existing:
            return str(existing)
    return payload.project_id


def _record(
    db: Session,
    user: User,
    usage: TokenUsage,
    *,
    purpose: str,
    provider: LLMProvider,
    conversation: Conversation | None = None,
    session_scope: SessionScope | None = None,
    model: str | None = None,
) -> None:
    """Record a turn's tokens against the daily budget **and** the bill (P8.1).

    Two ledgers on purpose, and they answer different questions.
    `app/ai/usage.py` is the *budget* — a daily ceiling that stops a runaway
    loop, keyed on the user, read before the next call. `app/core/metering.py`
    is the *bill* — keyed on the tenant, summed over a period, and the thing an
    invoice is reconstructed from. Merging them would mean either billing on a
    per-user counter that resets every day, or enforcing a ceiling by scanning
    a ledger, and neither is the shape the other needs.

    This is the single funnel every AI path already went through, which is why
    the billing half is wired here rather than at four call sites. Since P4.2
    that includes reading an attached picture (`routes/attachments.py`).

    `model` names the model that answered when it is not `provider.model`: a
    picture goes to the vision model, and a ledger row naming the chat model
    would put a vision call on the wrong line of the bill.
    """
    answered_by = model or provider.model
    token_usage.record(
        db,
        user=user,
        usage=usage,
        purpose=purpose,
        provider=provider.name,
        model=answered_by,
        conversation=conversation,
    )
    db.commit()
    _meter_tokens(
        db, user, usage, purpose=purpose, provider=provider,
        conversation=conversation, session_scope=session_scope, model=answered_by,
    )


def _meter_tokens(
    db: Session,
    user: User,
    usage: TokenUsage,
    *,
    purpose: str,
    provider: LLMProvider,
    conversation: Conversation | None,
    session_scope: SessionScope | None,
    model: str | None = None,
) -> None:
    """Post the turn's tokens to the usage ledger.

    Nothing here may fail the request: the reply has already been generated and
    paid for, and a metering write that 500s would lose the answer *and* the
    record. `usage_scope` absorbs its own write failures into a `MeteringFault`
    row; this guard covers the rest — a user with no organisation, most
    obviously, since a personal tenant is created lazily on first project.

    `LedgerSink(session_scope)` rather than the request session, for the reason
    `simulation/runner.py` gives: a metering write inside the caller's
    transaction could roll back the work it was measuring.
    """
    if session_scope is None:
        return
    try:
        organisation_id = org_budget.billed_organisation(
            db, user, conversation.project_id if conversation is not None else None
        )
        if organisation_id is None:
            # No tenant to bill. Skipped rather than attributed to a guess —
            # a wrong organisation on an invoice is worse than a missing line.
            return
        cause = Cause(
            organisation_id=organisation_id,
            source="ai.chat",
            subject_type="conversation" if conversation else "user",
            subject_id=conversation.id if conversation else user.id,
            conversation_id=conversation.id if conversation else None,
            user_id=user.id,
            detail={"purpose": purpose, "provider": provider.name},
        )
        with usage_scope(cause, LedgerSink(session_scope), fault_scope=session_scope) as scope:
            record_tokens(
                scope,
                prompt=usage.prompt_tokens,
                completion=usage.completion_tokens,
                model=model or provider.model,
            )
    except Exception:  # noqa: BLE001 - metering must never fail the work
        logger.exception("Could not meter AI tokens for user %s", user.id)


def _settle_turn(
    db: Session,
    user: User,
    provider: LLMProvider,
    conversation: Conversation | None,
    session_scope: SessionScope | None,
    meter: TurnMeter,
    *,
    default_stop_reason: str,
) -> None:
    """Bill what an agent turn spent and write its metrics row, however it ended.

    One call, from every exit of both chat routes: it finished, it raised
    mid-way, or the client hung up. The loop's tokens live on the `meter` the
    route owns, so steps 1..N-1 of a turn that died at step N are billed -- which
    the old comment in the streaming route promised and the code did not do
    (`spent` was assigned only from the final `done` event, so a failed turn
    recorded nothing at all).

    The metrics row is flushed before `_record` because `_record` commits; a turn
    that spent no token still gets its commit here. Nothing in this function may
    raise into the reply: accounting is never a reason to lose an answer.
    """
    try:
        record_turn(
            db,
            user=user,
            conversation=conversation,
            meter=meter,
            provider=provider.name,
            model=provider.model,
            default_stop_reason=default_stop_reason,
        )
        if meter.usage.prompt_tokens or meter.usage.completion_tokens:
            _record(
                db,
                user,
                meter.usage,
                purpose=token_usage.PURPOSE_CHAT,
                provider=provider,
                conversation=conversation,
                session_scope=session_scope,
            )
        else:
            db.commit()
    except Exception:  # noqa: BLE001 - accounting must not mask the real outcome
        logger.exception("Failed to record the turn for conversation %s", getattr(conversation, "id", None))
        db.rollback()
        return
    # After the turn is durable, so the warning is computed from spend that is real.
    # It never raises, and it is its own transaction.
    org_budget.alert_if_crossed(
        db,
        org_budget.billed_organisation(
            db, user, conversation.project_id if conversation is not None else None
        ),
    )


@router.get("/ai/usage", response_model=AIUsageRead)
def ai_usage(
    db: DbSession,
    current_user: CurrentUser,
    project_id: Annotated[str | None, Query()] = None,
) -> AIUsageRead:
    """What the model has cost today, what the limits are, and whether a turn would be refused.

    `project_id` picks which organisation's cap applies, the way it does for a chat turn;
    left out, it is the user's own organisation. This is the one place the user can see
    the numbers a 429 is made of, and the banner reads `notices` from it.
    """
    organisation_id = org_budget.billed_organisation(db, current_user, project_id)
    used = token_usage.usage_today(db, current_user.id)
    caps: list[OrgCapRead] = []
    unpriced = 0
    notices: list[OrgNoticeRead] = []
    if organisation_id is not None:
        current = org_budget.status(db, organisation_id)
        unpriced = current.unpriced_calls
        caps = [
            OrgCapRead(
                period=cap.period,
                cap_micro_usd=cap.cap_micro_usd,
                source=cap.source,
                spent_micro_usd=cap.spent_micro_usd,
                percent=cap.percent,
                resets_on=cap.resets_on,
            )
            for cap in current.caps
        ]
        notices = [
            OrgNoticeRead(period=n.period, percent=n.percent, level=n.level, message=n.message)
            for n in org_budget.notices(current)
        ]
    return AIUsageRead(
        today=DayUsageRead(
            prompt_tokens=used.prompt_tokens,
            completion_tokens=used.completion_tokens,
            cached_prompt_tokens=used.cached_prompt_tokens,
            cost_micro_usd=used.cost_micro_usd,
            unpriced_calls=used.unpriced_calls,
        ),
        allowance=UserAllowanceRead(
            daily_token_budget=token_usage.effective_daily_token_budget(db, organisation_id),
            daily_cost_budget_micro_usd=token_usage.daily_cost_budget_micro(),
        ),
        organisation_id=organisation_id,
        organisation_caps=caps,
        organisation_unpriced_calls=unpriced,
        notices=notices,
        blocked=token_usage.refusal(db, current_user, project_id),
    )


@router.get("/ai/status", response_model=AIStatus)
def ai_status() -> AIStatus:
    """Report whether the AI features are usable, so the UI can hide or explain them."""
    base = {"provider": settings.ai_provider, "model": settings.ai_model}
    try:
        get_provider().health()
    except LLMError as exc:
        return AIStatus(enabled=False, detail=str(exc), **base)
    return AIStatus(enabled=True, **base)


@router.post(
    "/projects/{project_id}/simulations/{simulation_id}/interpretation",
    response_model=ResultInterpretation,
)
def interpret_simulation(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    session_scope: SessionScopeDep,
    simulation_id: str,
) -> ResultInterpretation:
    """Explain a finished run: what the numbers mean and what to change.

    The interpretation is generated fresh rather than stored -- it is derived
    from the result row, which is itself immutable, so there is nothing to
    invalidate and no stale copy to serve.
    """
    job = db.get(SimulationJob, simulation_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    if job.status is not JobStatus.SUCCEEDED or not job.result:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation is {job.status.value}; there is nothing to interpret yet",
        )

    if job.load_case is None:
        # A thermal-conduction job has no mechanical case, and the interpreter is
        # written entirely around one — fixtures, loads, a factor of safety. It
        # would produce fluent prose about a load case that does not exist rather
        # than fail, which is the worst of the three possible outcomes.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This is a {job.analysis} run and has no load case, so the structural "
                "interpretation has nothing to read. Its result carries its answer "
                "directly — a temperature field, or a flow's pressure drop and heat."
            ),
        )

    _enforce_budget(db, current_user, project.id)
    provider = _provider_or_503()
    try:
        completion: Completion[ResultInterpretation] = interpret_result(
            provider,
            result=job.result,
            load_case=job.load_case,
            mesh_stats=job.mesh_stats,
            element_size_mm=job.element_size_mm,
        )
    except LLMError as exc:
        raise _translate(exc) from exc

    _record(
        db,
        current_user,
        completion.usage,
        purpose=token_usage.PURPOSE_INTERPRET,
        provider=provider,
        session_scope=session_scope,
    )
    return completion.value


@router.post("/projects/{project_id}/ai/load-case", response_model=LoadCaseDraft)
def draft_project_load_case(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    session_scope: SessionScopeDep,
    payload: Annotated[LoadCaseRequest, ...],
) -> LoadCaseDraft:
    """Draft a load case from a sentence, against a real geometry's bounding box.

    Returns a draft with its assumptions attached; it is meant to be reviewed
    and edited, not submitted to the solver unread.
    """
    stmt = select(GeometryVersion).where(GeometryVersion.project_id == project.id)
    if payload.geometry_version is None:
        stmt = stmt.order_by(GeometryVersion.version_number.desc())
    else:
        stmt = stmt.where(GeometryVersion.version_number == payload.geometry_version)
    version = db.scalars(stmt).first()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload a geometry before drafting a load case against it",
        )

    bounding_box = (version.stats or {}).get("bounding_box")
    if not bounding_box:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This geometry has no bounding box, so 'top' and 'bottom' cannot be resolved",
        )

    _enforce_budget(db, current_user, project.id)
    provider = _provider_or_503()
    try:
        completion: Completion[LoadCaseDraft] = draft_load_case(
            provider, description=payload.description, bounding_box=bounding_box
        )
    except LLMError as exc:
        raise _translate(exc) from exc

    _record(
        db,
        current_user,
        completion.usage,
        purpose=token_usage.PURPOSE_LOAD_CASE,
        provider=provider,
        session_scope=session_scope,
    )
    return completion.value


# --------------------------------------------------------------------------
# Agent chat: the model chooses the tools and drives the flow itself.
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=8_000,
        description=(
            "What the user typed. Required unless `continuation` is set, and refused "
            "alongside it: a Continue carries no text of the user's."
        ),
    )
    continuation: Literal["continue"] | None = Field(
        default=None,
        description=(
            "Press Continue on a stopped turn. The server writes the instruction itself "
            "(`app/ai/continuation.py`), from the stored turn record and the plan, and "
            "answers 409 when there is nothing to continue. Needs `conversation_id`."
        ),
    )
    conversation_id: str | None = Field(
        default=None,
        description="Omit to start a new conversation. Pass it back to continue one.",
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Scopes a new conversation to a project so the agent can resolve "
            "'the latest run' without being given ids every turn."
        ),
    )
    allow_mutations: bool = Field(
        default=False,
        description=(
            "Unlocks tools that change state or cost compute. The agent is told "
            "to ask before it needs this; send true only once the user has said yes."
        ),
    )

    @model_validator(mode="after")
    def _a_message_or_a_continuation(self) -> "ChatRequest":
        if self.continuation is not None:
            if self.message is not None:
                raise ValueError(
                    "A continuation carries no message: the server writes the instruction. "
                    "Send `message` to say something, or `continuation` to carry on, not both."
                )
            if not self.conversation_id:
                raise ValueError(
                    "A continuation needs `conversation_id`: there is nothing to continue "
                    "in a conversation that has not started."
                )
        elif self.message is None:
            raise ValueError("Send `message`, or `continuation` to carry on a stopped turn.")
        return self


class AgentStepRead(BaseModel):
    tool: str
    arguments: dict[str, Any]
    ok: bool
    result: Any


class ChatResponse(BaseModel):
    conversation_id: str
    title: str
    reply: str
    steps: list[AgentStepRead] = Field(
        description="Tool calls the agent made, in order, so the UI can show its work."
    )
    truncated: bool = Field(
        description="True when the step budget ran out before the agent finished."
    )
    prompt_tokens: int
    completion_tokens: int


class TurnCancelled(BaseModel):
    """The answer to a stop request (P5.6).

    A model rather than a bare `dict[str, str]` so the schema describes a shape
    instead of "an object with string values" — `tests/test_ai.py` asserts every
    AI endpoint's success response is described, and it is right to.
    """

    status: str = Field(
        description=(
            "Always 'accepted'. This endpoint does not wait for the turn to end, "
            "and never claims to have stopped it — the stream reports that itself."
        )
    )
    detail: str


def _owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    # 404 rather than 403 for someone else's conversation, matching the rest of
    # the API -- never confirm that an id exists.
    if conversation is None or conversation.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _resolve_conversation(db: Session, user: User, payload: ChatRequest) -> Conversation:
    if payload.conversation_id:
        return _owned_conversation(db, user, payload.conversation_id)

    conversation = Conversation(
        owner_id=user.id,
        project_id=payload.project_id,
        # A placeholder, replaced by a real title once the first exchange has
        # happened and there is something to name.
        title=(payload.message or "")[:60],
    )
    db.add(conversation)
    # Committed, not merely flushed, and that distinction is the whole point.
    # `chat_stream` hands this id to the browser in its first `start` event so a
    # turn that dies is still resumable -- but its `LLMError` handler calls
    # `db.rollback()`, which used to take the un-committed conversation row with
    # it. The client then held an id for a row that never existed, and every
    # later message in that chat answered 404 "Conversation not found": one
    # provider hiccup on the first turn bricked the conversation permanently.
    #
    # Same rule as a queued job row (see CLAUDE.md): never hand out an id that
    # is not durable yet.
    db.commit()
    return conversation


def _build_toolbox(
    db: Session,
    user: User,
    conversation: Conversation,
    *,
    queue: Any,
    session_scope: Any,
    store: Any,
    media: Any,
    provider: Any = None,
) -> ToolBox:
    """Wire the toolbox with everything a mutating tool actually needs.

    `run_simulation` submits a real job and `delete_simulation` drops a real
    blob; both need the same collaborators the HTTP routes use. Injecting them
    here keeps the tool layer free of a direct import of the queue.
    """
    return ToolBox(
        db=db,
        user=user,
        project_id=conversation.project_id,
        conversation=conversation,
        job_queue=queue,
        session_scope=session_scope,
        media_store=store,
        media=media,
        provider=provider,
    )


def _maybe_title(
    db: Session,
    user: User,
    provider: LLMProvider,
    conversation: Conversation,
    user_message: str,
    reply: str,
) -> None:
    """Name a conversation once, after its first exchange.

    Only the first: a title that changes as the conversation goes on moves
    around in the sidebar, and the opening exchange is what the user remembers
    the session by anyway. Failures are absorbed inside `generate_title`.
    """
    if _message_count(db, conversation.id) > _FIRST_EXCHANGE_MESSAGES:
        return
    title, usage = generate_title(provider, user_message=user_message, assistant_reply=reply)
    conversation.title = title
    token_usage.record(
        db,
        user=user,
        usage=usage,
        purpose=token_usage.PURPOSE_TITLE,
        provider=provider.name,
        model=provider.model,
        conversation=conversation,
    )


#: A first exchange is the user's message plus whatever the agent did to answer
#: it. Anything beyond this and the conversation has a history worth keeping the
#: existing title for.
_FIRST_EXCHANGE_MESSAGES = 12


def _message_count(db: Session, conversation_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
        )
        or 0
    )


#: Counted against the signed-in principal, not the address (P1.6). One office
#: behind one NAT is a single IP and a whole engineering team, so an
#: address-keyed budget on the most expensive route in the product either
#: throttles a customer or is set so high it stops nothing. Both chat routes
#: share one budget deliberately: they are two ways to ask for the same turn,
#: and separate budgets would just mean a client alternating between them gets
#: double.
_chat_rate_limit = RateLimit(
    "ai.chat", settings.chat_requests_per_minute, window_seconds=60
)


def _turn_message(db: Session, conversation: Conversation, payload: ChatRequest) -> str:
    """The text this turn answers: the user's, or the server's own for a Continue.

    Decided here and by the stored record, never by the client (`continuation.pending`):
    a button, a reload and a hand-written request all see the same answer, and a stale
    button -- pressed under an answer already continued -- is refused rather than resuming
    work that is running.
    """
    if payload.continuation is None:
        return payload.message or ""
    action = continuation.pending(db, conversation)
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "There is nothing to continue: the newest turn finished, was stopped, or is "
                "waiting on a decision, or something has been said since. Send a message "
                "instead."
            ),
        )
    return continuation.message_for(action, conversation)


@router.post("/ai/chat", response_model=ChatResponse, dependencies=[Depends(_chat_rate_limit)])
def chat(
    db: DbSession,
    current_user: CurrentUser,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
    store: MediaStoreDep,
    media: MediaServiceDep,
    payload: Annotated[ChatRequest, ...],
) -> ChatResponse:
    """Talk to the agent. It decides which tools to call and in what order.

    The conversation is the memory: pass `conversation_id` back on each turn and
    the agent replays a bounded window of everything it did before -- including
    the calls that failed -- plus a summary of anything older.
    """
    _enforce_budget(db, current_user, _turn_project_id(db, current_user, payload))
    conversation = _resolve_conversation(db, current_user, payload)
    user_message = _turn_message(db, conversation, payload)

    if payload.project_id and conversation.project_id is None:
        conversation.project_id = payload.project_id

    provider = _provider_or_503()
    toolbox = _build_toolbox(
        db,
        current_user,
        conversation,
        queue=queue,
        session_scope=session_scope,
        store=store,
        media=media,
        provider=provider,
    )

    meter = TurnMeter()
    try:
        reply: AgentReply = run_agent(
            db=db,
            provider=provider,
            conversation=conversation,
            toolbox=toolbox,
            user_message=user_message,
            user=current_user,
            allow_mutations=payload.allow_mutations,
            max_tokens=settings.ai_max_tokens,
            meter=meter,
        )
    except LLMError as exc:
        # The user turn is already persisted, so roll back to the last committed
        # state rather than leaving a question with no answer in the transcript.
        db.rollback()
        # Then bill what the turn had already spent, plus what the failing call
        # itself was billed for: a failure is not free.
        meter.charge(exc.usage, calls=1 if exc.usage.total_tokens else 0)
        _settle_turn(
            db, current_user, provider, conversation, session_scope, meter,
            default_stop_reason=STOP_ERROR,
        )
        raise _translate(exc) from exc

    # The agent may have created a project this turn. Adopt it as the
    # conversation's scope so the next turn resolves "the latest run" without
    # the client having to pass an id it only just learned about.
    if conversation.project_id is None and toolbox.project_id:
        conversation.project_id = toolbox.project_id

    if payload.continuation is None:
        _maybe_title(db, current_user, provider, conversation, user_message, reply.text)
    _settle_turn(
        db, current_user, provider, conversation, session_scope, meter,
        default_stop_reason=STOP_ERROR,
    )

    return ChatResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        reply=reply.text,
        steps=[
            AgentStepRead(tool=s.tool, arguments=s.arguments, ok=s.ok, result=s.result)
            for s in reply.steps
        ],
        truncated=reply.truncated,
        prompt_tokens=reply.usage.prompt_tokens,
        completion_tokens=reply.usage.completion_tokens,
    )


@router.post("/ai/chat/stream", dependencies=[Depends(_chat_rate_limit)])
def chat_stream(
    db: DbSession,
    current_user: CurrentUser,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
    store: MediaStoreDep,
    media: MediaServiceDep,
    payload: Annotated[ChatRequest, ...],
) -> StreamingResponse:
    """The same agent loop, streamed as Server-Sent Events.

    The UI subscribes and renders each step as it happens -- which tool the
    agent reached for, what it found, how long it took -- instead of showing a
    spinner for however long the whole turn takes.

    Event types: `start`, `thinking`, `narration`, `tool_start`, `tool_end`,
    `message`, `done`, `title`, `error`.

    `title` is last and is emitted only when this turn named the conversation --
    naming happens after the answer exists, so it cannot ride on `done`. A
    client that does not know the event ignores it and keeps the title it had.
    """
    _enforce_budget(db, current_user, _turn_project_id(db, current_user, payload))
    conversation = _resolve_conversation(db, current_user, payload)
    user_message = _turn_message(db, conversation, payload)
    if payload.project_id and conversation.project_id is None:
        conversation.project_id = payload.project_id

    provider = _provider_or_503()
    toolbox = _build_toolbox(
        db,
        current_user,
        conversation,
        queue=queue,
        session_scope=session_scope,
        store=store,
        media=media,
        provider=provider,
    )
    conversation_id = conversation.id

    # One id for this turn, so a reconnecting client can tell "the turn I lost"
    # from "that turn ended and another began". Minted here rather than inside
    # the loop because the `start` event carries it too.
    turn_id = uuid4().hex

    def emit(event: dict[str, Any]) -> str:
        """Persist one event, then format it for the wire.

        Recording before yielding is what makes the cursor meaningful: an event
        the client saw is always an event that is stored, so a reconnect asking
        for "everything after 12" cannot be told that 12 never happened.

        A failure to record must never take down a turn that is otherwise
        working. Losing the resume buffer costs a reconnecting reader a reload;
        losing the turn costs them the work. The `seq` is then omitted rather
        than invented, and `lib/agent-stream.ts` treats a missing cursor as
        "cannot resume from here", which is the truth.
        """
        try:
            sequence = turn_events.record(db, conversation_id, turn_id, event)
            turn_events.prune(db)
            db.commit()
            body = {**event, "seq": sequence, "turn_id": turn_id}
            return f"id: {sequence}\ndata: {json.dumps(body, default=str)}\n\n"
        except Exception:  # noqa: BLE001 - the resume buffer must not break the turn
            logger.exception("Could not record a turn event for conversation %s", conversation_id)
            db.rollback()
            return f"data: {json.dumps({**event, 'turn_id': turn_id}, default=str)}\n\n"

    def events() -> Iterator[str]:
        # The conversation id goes first so the client can store it before any
        # work happens -- a stream that dies mid-turn still leaves a resumable
        # conversation rather than an orphan.
        yield emit({"type": "start", "conversation_id": conversation_id})
        reply_text = ""
        meter = TurnMeter()
        recorded = False
        #: What `settle` says when the loop did not name a stop reason itself. The
        #: default is the one that reaches `finally` without passing either branch
        #: above it, which is the client hanging up mid-stream.
        ending = STOP_DISCONNECTED

        def settle() -> None:
            """Persist what this turn actually cost, exactly once.

            This runs from `finally`, so it also runs when the client hangs up
            mid-stream and `GeneratorExit` is thrown at a `yield`. Previously
            accounting sat after the loop with no `finally`: the transcript was
            already committed by `stream_agent`, but no `AITokenUsage` row was
            ever written, so aborting every stream was unmetered, unlimited
            spend against a budget that never advanced.

            It reads the tokens off the `meter` the loop has been writing into,
            not off the final `done` event: that event never arrives for a turn
            that failed or was abandoned, and those are exactly the turns that
            had spent something.
            """
            nonlocal recorded
            if recorded:
                return
            recorded = True
            _settle_turn(
                db, current_user, provider, conversation, session_scope, meter,
                default_stop_reason=ending,
            )

        try:
            for event in stream_agent(
                db=db,
                provider=provider,
                conversation=conversation,
                toolbox=toolbox,
                user_message=user_message,
                user=current_user,
                allow_mutations=payload.allow_mutations,
                max_tokens=settings.ai_max_tokens,
                meter=meter,
            ):
                if event["type"] == "message":
                    reply_text = event["content"]
                yield emit(event)

            # Same adoption as the non-streaming route: a project created
            # mid-stream has to outlive this request.
            if conversation.project_id is None and toolbox.project_id:
                conversation.project_id = toolbox.project_id
            # A Continue has no words of the user's to name a conversation from, and the
            # first exchange it follows was named when it finished.
            if payload.continuation is None:
                _maybe_title(db, current_user, provider, conversation, user_message, reply_text)
            settle()
            yield emit({"type": "title", "title": conversation.title})
        except LLMError as exc:
            # Roll back the failed unit of work, then still bill the provider
            # calls this turn already made -- steps 1..N-1 of a multi-step turn
            # are real spend even though step N failed.
            db.rollback()
            meter.charge(exc.usage, calls=1 if exc.usage.total_tokens else 0)
            ending = STOP_ERROR
            settle()
            yield emit({"type": "error", "message": str(exc)})
        finally:
            # Covers the client-disconnect path, where `GeneratorExit` unwinds
            # the generator without reaching either branch above.
            settle()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            # Tell nginx not to buffer, or events arrive in one batch at the end
            # and the whole point of streaming is lost.
            "x-accel-buffering": "no",
        },
    )


# --------------------------------------------------------------------------
# Conversation management: the sidebar, and rehydrating one session.
# --------------------------------------------------------------------------


class ConversationMessageRead(BaseModel):
    """One stored turn, carrying everything the UI needs to redraw the step.

    Tool `arguments` and `result` are included deliberately. Without them a
    rehydrated conversation shows the prose and nothing else, so a user who
    reloads the page loses the record of what the agent actually did -- which is
    the part of the transcript an engineer is most likely to want to check.
    """

    sequence: int
    role: str
    content: str | None
    tool_call_id: str | None
    tool_name: str | None
    label: str | None = Field(
        default=None, description="Human label for a tool step, matching the live stream."
    )
    arguments: dict[str, Any] | None = Field(
        default=None, description="Arguments the agent passed to this tool."
    )
    result: Any = Field(
        default=None, description="Parsed tool result, as the live stream reported it."
    )
    summary: str | None = Field(
        default=None, description="One-line outcome, matching the live stream."
    )
    is_error: bool
    duration_ms: int | None
    created_at: str
    branchable: bool = Field(
        default=False,
        description=(
            "True for an assistant answer a branch may start at (ROAD_TO_10 2.5): text, and no "
            "tool calls waiting on results. The UI offers Branch only here, so it never offers "
            "what the server would refuse."
        ),
    )
    continuation: bool = Field(
        default=False,
        description=(
            "True for the instruction the server wrote when the user pressed Continue. It is "
            "stored as a user message so the model reads it, but it is not the user's words "
            "and a client should draw it as a divider, not as something they said."
        ),
    )


class UnfinishedOperationRead(BaseModel):
    """A CATIA call whose most recent attempt in this conversation failed."""

    tool: str
    label: str = Field(description="The same human label the step list uses.")
    error: str
    attempts: int = Field(ge=1)


class OpenTaskRead(BaseModel):
    id: str
    title: str
    state: str


class NextActionRead(BaseModel):
    """What one press can do next on a stopped turn (ROAD_TO_10 2.2)."""

    kind: Literal["continue"]
    reason: str = Field(
        description="step_budget, repeated_calls, task_boundary or provider_busy."
    )
    label: str
    detail: str = Field(description="One sentence saying what the press will do.")
    open_tasks: list[OpenTaskRead] = Field(default_factory=list)


class PlanNextRead(BaseModel):
    id: str
    title: str


class ResumePlanRead(BaseModel):
    """The plan the agent declared, as the server recorded it (2.3)."""

    total: int = Field(ge=1)
    settled: int = Field(ge=0)
    open: list[OpenTaskRead]
    next: PlanNextRead | None = Field(
        default=None,
        description="What is ready to start now; null when everything left is blocked.",
    )


class ResumeDesignRead(BaseModel):
    """The recorded design, as a count and a revision -- the numbers are in the panel."""

    name: str
    revision: int = Field(ge=1)
    parameters: int = Field(ge=0)


class ConversationResumeRead(BaseModel):
    """What this conversation already did, for someone returning to it.

    The same facts the agent is given in its state block, read from the same
    operation log -- so the human and the model come back to the identical
    account of where the work got to. Two different answers to "what did we do"
    on the same screen is worse than one of them being absent.
    """

    operations: int = Field(ge=0, description="CATIA calls made in this conversation.")
    last_activity_at: str | None = Field(
        default=None, description="ISO timestamp of the most recent CATIA call."
    )
    unfinished: list[UnfinishedOperationRead] = Field(default_factory=list)
    plan: ResumePlanRead | None = Field(
        default=None,
        description="Null when the conversation never declared a plan.",
    )
    design: ResumeDesignRead | None = Field(
        default=None,
        description="Null when the conversation has no recorded design.",
    )


class BranchOriginRead(BaseModel):
    conversation_id: str
    title: str
    at_sequence: int | None


class ConversationRead(BaseModel):
    conversation_id: str
    title: str
    pinned: bool = False
    branched_from: BranchOriginRead | None = Field(
        default=None,
        description=(
            "Where this conversation was branched from, when the original is still the "
            "user's to read; null for any other conversation."
        ),
    )
    project_id: str | None
    created_at: str
    updated_at: str
    has_catia_document: bool
    catia_document: str | None
    resume: ConversationResumeRead
    prompt_tokens: int
    completion_tokens: int
    next_action: NextActionRead | None = Field(
        default=None,
        description=(
            "What Continue would do, when the newest turn stopped in a way that can be "
            "continued and nothing has been said since. Read from the stored turn record, "
            "so it survives a reload."
        ),
    )
    messages: list[ConversationMessageRead]


class ConversationSummaryRead(BaseModel):
    """One row of the sidebar."""

    conversation_id: str
    title: str
    project_id: str | None
    created_at: str
    updated_at: str
    message_count: int
    has_catia_document: bool
    prompt_tokens: int
    completion_tokens: int
    pinned: bool = Field(default=False, description="Pinned conversations sort first (2.6).")
    branched_from_id: str | None = Field(
        default=None, description="The conversation this one was branched from, if any (2.5)."
    )
    match: Literal["title", "message"] | None = Field(
        default=None,
        description="With `q`: whether the title or one of the user's messages matched.",
    )


class ConversationPage(BaseModel):
    total: int = Field(ge=0)
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    items: list[ConversationSummaryRead]


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    pinned: bool | None = Field(
        default=None, description="Pin to, or unpin from, the top of the sidebar."
    )

    @model_validator(mode="after")
    def _says_something(self) -> "ConversationUpdate":
        if self.title is not None:
            self.title = self.title.strip()
            if not self.title:
                raise ValueError("A title cannot be only spaces.")
        if self.title is None and self.pinned is None:
            raise ValueError("Send a `title`, `pinned`, or both.")
        return self


def _unfence(content: str | None) -> Any:
    """Recover a tool result from its stored, fenced form.

    Tool results are persisted exactly as the model saw them -- sanitised and
    wrapped -- because the transcript has to be a faithful record of the prompt.
    The UI wants the payload, so the fence is peeled here rather than storing a
    second unfenced copy that could drift from the first.
    """
    if not content:
        return None
    body = content.strip()
    if body.startswith(UNTRUSTED_OPEN) and body.endswith(UNTRUSTED_CLOSE):
        body = body[len(UNTRUSTED_OPEN) : -len(UNTRUSTED_CLOSE)].strip()
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return body


def _arguments_for(
    message: ConversationMessage, by_call_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    if message.role is not MessageRole.TOOL or not message.tool_call_id:
        return None
    call = by_call_id.get(message.tool_call_id)
    if call is None:
        return None
    return (call.get("function") or {}).get("arguments")


@router.get("/ai/conversations", response_model=ConversationPage)
def list_conversations(
    db: DbSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=100,
            description=(
                "Search the titles and the user's own messages, case-insensitively and as "
                "plain text (no wildcards). Only this user's conversations are searched."
            ),
        ),
    ] = None,
) -> ConversationPage:
    """The user's conversations: pinned first, then newest activity first.

    Ordered by `updated_at` rather than `created_at`: a sidebar is a list of
    what you were last working on, not what you started first. Pinning does not
    move `updated_at`.

    `q` filters to conversations whose title, or one of whose messages *the user wrote*,
    contains the text. The server's own notes (`CONTROL_NOTE`, a Continue) are not the
    user's words and are not searched, nor are the model's answers or tool results: a search
    for "M6" should find the conversation where the user said it, not every one whose
    log mentions a bolt. The scan is bounded by the owner's conversations (the owner filter
    is applied first) and has no index of its own -- stated, not hidden.
    """
    filters: list[Any] = [Conversation.owner_id == current_user.id]
    needle = " ".join(q.split()) if q else None
    if needle:
        filters.append(
            or_(
                Conversation.title.icontains(needle, autoescape=True),
                exists().where(
                    ConversationMessage.conversation_id == Conversation.id,
                    ConversationMessage.role == MessageRole.USER,
                    ~ConversationMessage.content.startswith(
                        prompts.CONTROL_NOTE, autoescape=True
                    ),
                    ConversationMessage.content.icontains(needle, autoescape=True),
                ),
            )
        )
    total = db.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Conversation)
            .where(*filters)
            # NULLs sort last under `IS NULL ASC`, whichever way the database orders them.
            .order_by(
                Conversation.pinned_at.is_(None),
                Conversation.pinned_at.desc(),
                Conversation.updated_at.desc(),
                Conversation.created_at.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    # One aggregate for the whole page rather than a count per row: this is the
    # first screen of the product and it must not be N+1.
    counts: dict[str, int] = {}
    if rows:
        ids = [row.id for row in rows]
        counts = {
            conversation_id: count
            for conversation_id, count in db.execute(
                select(ConversationMessage.conversation_id, func.count())
                .where(ConversationMessage.conversation_id.in_(ids))
                .group_by(ConversationMessage.conversation_id)
            ).all()
        }

    return ConversationPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            ConversationSummaryRead(
                conversation_id=row.id,
                title=row.title,
                project_id=row.project_id,
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
                message_count=counts.get(row.id, 0),
                has_catia_document=bound_document_name(db, row.id) is not None,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                pinned=row.pinned_at is not None,
                branched_from_id=row.branched_from_id,
                match=(
                    ("title" if needle.lower() in row.title.lower() else "message")
                    if needle
                    else None
                ),
            )
            for row in rows
        ],
    )


def _branch_origin(
    db: Session, user: User, conversation: Conversation
) -> BranchOriginRead | None:
    """The conversation this one was branched from, if it is still the user's to read."""
    if conversation.branched_from_id is None:
        return None
    source = db.get(Conversation, conversation.branched_from_id)
    if source is None or source.owner_id != user.id:
        return None
    return BranchOriginRead(
        conversation_id=source.id,
        title=source.title,
        at_sequence=conversation.branched_at_sequence,
    )


def _pending_action(db: Session, conversation: Conversation) -> NextActionRead | None:
    action = continuation.pending(db, conversation)
    return NextActionRead.model_validate(action.to_dict()) if action is not None else None


def _resume_plan(conversation: Conversation) -> ResumePlanRead | None:
    progress = continuation.plan_progress(continuation.graph_of(conversation))
    return ResumePlanRead.model_validate(progress) if progress is not None else None


def _resume_design(db: Session, conversation: Conversation) -> ResumeDesignRead | None:
    """The conversation's recorded design as a count, or None if it has none or this build
    cannot read it. A spec that will not parse must not fail the transcript the user
    reloaded to read: the panel reports the parse error itself."""
    from app.core import designs

    document = designs.load(db, conversation)
    if document is None:
        return None
    try:
        spec = designs.spec_of(document)
    except Exception:  # noqa: BLE001 - see above
        return None
    return ResumeDesignRead(
        name=spec.name, revision=document.revision_number, parameters=len(spec.parameters)
    )


@router.get("/ai/conversations/{conversation_id}", response_model=ConversationRead)
def read_conversation(
    db: DbSession, current_user: CurrentUser, conversation_id: str
) -> ConversationRead:
    """The stored transcript, so a client can rehydrate a conversation."""
    conversation = _owned_conversation(db, current_user, conversation_id)

    # Tool calls are recorded on the assistant turn that requested them and the
    # results on separate rows, so the arguments are stitched back on by call id
    # rather than duplicated into both.
    by_call_id: dict[str, dict[str, Any]] = {}
    for message in conversation.messages:
        for call in message.tool_calls or []:
            if call.get("id"):
                by_call_id[str(call["id"])] = call

    messages: list[ConversationMessageRead] = []
    for message in conversation.messages:
        result = _unfence(message.content) if message.role is MessageRole.TOOL else None
        label = None
        summary = None
        if message.role is MessageRole.TOOL and message.tool_name:
            label = tool_label(message.tool_name)
            summary = summarise_step(message.tool_name, result, not message.is_error)
        messages.append(
            ConversationMessageRead(
                sequence=message.sequence,
                role=message.role.value,
                content=message.content,
                tool_call_id=message.tool_call_id,
                tool_name=message.tool_name,
                label=label,
                arguments=_arguments_for(message, by_call_id),
                result=result,
                summary=summary,
                is_error=message.is_error,
                duration_ms=message.duration_ms,
                created_at=message.created_at.isoformat(),
                branchable=branching.is_boundary(message),
                continuation=(
                    message.role is MessageRole.USER
                    and continuation.is_continuation(message.content)
                ),
            )
        )

    document = bound_document_name(db, conversation.id)
    activity = catia_activity(db, conversation.id)
    return ConversationRead(
        conversation_id=conversation.id,
        title=conversation.title,
        pinned=conversation.pinned_at is not None,
        branched_from=_branch_origin(db, current_user, conversation),
        project_id=conversation.project_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        has_catia_document=document is not None,
        catia_document=document,
        resume=ConversationResumeRead(
            operations=activity.operations,
            last_activity_at=(
                activity.last_at.isoformat() if activity.last_at is not None else None
            ),
            unfinished=[
                UnfinishedOperationRead(
                    tool=item.tool,
                    label=tool_label(item.tool),
                    error=item.error,
                    attempts=item.attempts,
                )
                for item in activity.unresolved
            ],
            plan=_resume_plan(conversation),
            design=_resume_design(db, conversation),
        ),
        prompt_tokens=conversation.prompt_tokens,
        completion_tokens=conversation.completion_tokens,
        next_action=_pending_action(db, conversation),
        messages=messages,
    )


@router.patch("/ai/conversations/{conversation_id}", response_model=ConversationRead)
def rename_conversation(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    payload: Annotated[ConversationUpdate, ...],
) -> ConversationRead:
    """Rename a conversation, pin it, or both. The only fields a user may edit."""
    conversation = _owned_conversation(db, current_user, conversation_id)
    if payload.title is not None:
        conversation.title = payload.title[:255]
    if payload.pinned is not None:
        _set_pinned(db, conversation, payload.pinned)
    db.commit()
    # The pin was written by a Core statement, which the identity map does not see.
    db.expire(conversation)
    return read_conversation(db, current_user, conversation_id)


def _set_pinned(db: Session, conversation: Conversation, pinned: bool) -> None:
    """Pin or unpin without moving `updated_at`, which means "last worked on".

    A column with `onupdate` is bumped by every ORM UPDATE that does not name it, and
    assigning it the value it already has is no change at all to the ORM. Naming it in a Core
    statement is what stops the bump.
    """
    if (conversation.pinned_at is not None) == pinned:
        return
    db.flush()
    db.execute(
        update(Conversation)
        .where(Conversation.id == conversation.id)
        .values(
            pinned_at=datetime.now(timezone.utc) if pinned else None,
            updated_at=Conversation.updated_at,
        )
    )


@router.post(
    "/ai/conversations/{conversation_id}/cancel",
    response_model=TurnCancelled,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_turn(
    db: DbSession, current_user: CurrentUser, conversation_id: str
) -> TurnCancelled:
    """Ask the turn streaming for this conversation to stop (P5.6).

    **`202`, and it means accepted rather than done.** The turn is streaming
    from another worker and will end at its next step boundary — after any tool
    call already in flight finishes, because half an applied CATIA operation is
    a worse thing to own than four more seconds of waiting. The stream itself
    sends the `done` event with `stop_reason: "cancelled"`; this endpoint never
    pretends to have delivered it.

    Never refuses. There is no reliable way to know from here whether a turn is
    running, and refusing on a stale belief would deny the request in exactly
    the case that matters. A flag with nothing to stop is spent by the next turn
    that starts.
    """
    conversation = _owned_conversation(db, current_user, conversation_id)
    interruption.request_turn_stop(db, conversation, by=current_user)
    return TurnCancelled(
        status="accepted",
        detail="Stopping after the current step. Everything already done is kept.",
    )


@router.get("/ai/conversations/{conversation_id}/stream")
def resume_stream(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    """Rejoin a turn already in flight, from where the last stream stopped (P5.1).

    **A `GET`, and that is what makes it a resume.** `POST /ai/chat/stream`
    *starts* a turn; reconnecting to it with a POST would start a second one, so
    a client whose connection dropped would double every turn it lost. This
    endpoint runs no agent and writes no message. It replays what was recorded
    and then follows.

    Pass `after` as the last `seq` the client saw. Everything after it is
    replayed immediately, byte-identical to how it went out the first time, and
    then new events are followed until the turn ends. `after=0` means "whatever
    is still kept", which is what a client with no cursor asks for.

    **A gap is reported, never smoothed over.** Events are kept for ten minutes
    (`app/ai/turn_events.py`); a client away longer than that is sent a
    `resume_gap` event and should reload the conversation, whose transcript is
    complete. Handing back a turn with a silent bite out of the middle would
    render as an agent that skipped three steps, which is the one reading that
    must never be available.

    It is polled rather than pushed, for the reason the module argues: a broker
    would be a fifth interface and a second place to lose an event, for a
    feature whose entire job is surviving a connection that was already lost.
    """
    conversation = _owned_conversation(db, current_user, conversation_id)
    resolved = conversation.id

    def events() -> Iterator[str]:
        cursor = after
        replay = turn_events.read_after(db, resolved, cursor)
        if replay.gap:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "resume_gap",
                        "after": cursor,
                        "message": (
                            "This turn's live events are no longer kept. Reload the "
                            "conversation — everything that ran is in the transcript."
                        ),
                    }
                )
                + "\n\n"
            )
            return

        finished = False
        for event in replay.events:
            cursor = int(event.get("seq") or cursor)
            yield f"id: {cursor}\ndata: {json.dumps(event, default=str)}\n\n"
            if turn_events.is_terminal(event):
                finished = True

        idle = 0.0
        while not finished and idle < turn_events.FOLLOW_IDLE_TIMEOUT_S:
            time.sleep(turn_events.FOLLOW_INTERVAL_S)
            # A fresh read each poll: the writer is another worker in another
            # transaction, and this session would otherwise keep serving the
            # snapshot it opened with and follow nothing at all.
            db.rollback()
            batch = turn_events.read_after(db, resolved, cursor)
            if not batch.events:
                idle += turn_events.FOLLOW_INTERVAL_S
                continue
            idle = 0.0
            for event in batch.events:
                cursor = int(event.get("seq") or cursor)
                yield f"id: {cursor}\ndata: {json.dumps(event, default=str)}\n\n"
                if turn_events.is_terminal(event):
                    finished = True

        if not finished:
            # Two minutes of silence means the turn finished without writing a
            # terminal event, or is wedged. Either way the reader is better
            # served by being told than by a connection that never closes.
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "resume_idle",
                        "after": cursor,
                        "message": (
                            "Nothing has arrived for two minutes. Reload the "
                            "conversation to see where it got to."
                        ),
                    }
                )
                + "\n\n"
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


class BranchRequest(BaseModel):
    from_sequence: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The assistant answer to branch at. Omit for the newest answer. A message with "
            "tool calls, or one that is not the assistant's, is refused (422)."
        ),
    )
    title: str | None = Field(default=None, max_length=255)


class BranchRead(BaseModel):
    conversation_id: str
    title: str
    from_sequence: int
    copied_messages: int = Field(ge=0)
    design_revision: int | None = Field(
        description="The revision of the original's design that was copied, or null."
    )
    plan_copied: bool
    summary_kept: bool
    notes: list[str] = Field(
        description=(
            "What was and was not carried over, in words -- always including that the CATIA "
            "document is never copied."
        )
    )


class RewindRead(BaseModel):
    message: str = Field(description="The user's message, to send again or edit first.")
    removed_messages: int = Field(ge=1)


#: Branching, rewinding and the rest of the conversation edits share a budget: none of them
#: calls a model, and none of them should be a loop's inner step.
_conversation_edit_limit = RateLimit("ai.conversation_edit", 60, window_seconds=60)


@router.post(
    "/ai/conversations/{conversation_id}/branch",
    response_model=BranchRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_conversation_edit_limit)],
)
def branch_conversation(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    payload: Annotated[BranchRequest, ...],
) -> BranchRead:
    """Copy a conversation up to one of its answers into a new conversation (ROAD_TO_10 2.5).

    The branch carries the messages, the design as it stood at that answer and, where they
    are still true, the summary and the plan. It never carries the CATIA document: a
    document belongs to one conversation, and `notes` says so.
    """
    source = _owned_conversation(db, current_user, conversation_id)
    try:
        outcome = branching.branch(
            db, source, current_user, from_sequence=payload.from_sequence, title=payload.title
        )
    except branching.BadPoint as exc:
        # Both refusals are raised before anything is written, so there is nothing to undo.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except branching.Refused as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return BranchRead(
        conversation_id=outcome.conversation.id,
        title=outcome.conversation.title,
        from_sequence=outcome.from_sequence,
        copied_messages=outcome.copied_messages,
        design_revision=outcome.design_revision,
        plan_copied=outcome.plan_copied,
        summary_kept=outcome.summary_kept,
        notes=list(outcome.notes),
    )


@router.post(
    "/ai/conversations/{conversation_id}/rewind",
    response_model=RewindRead,
    dependencies=[Depends(_conversation_edit_limit)],
)
def rewind_conversation(
    db: DbSession, current_user: CurrentUser, conversation_id: str
) -> RewindRead:
    """Delete the newest user message and everything after it, and hand the text back.

    This is Retry and Edit (ROAD_TO_10 2.5): the client puts the text back in the composer, or
    sends it again at once. It is refused (409) when the turn changed anything, because the
    document and the design cannot be rolled back with the transcript -- the answer says what
    ran and offers a branch from before it.
    """
    conversation = _owned_conversation(db, current_user, conversation_id)
    toolbox = ToolBox(db=db, user=current_user, conversation=conversation)
    try:
        outcome = branching.rewind(db, conversation, toolbox)
    except branching.Refused as exc:
        # Every refusal is raised before the first delete, so there is nothing to undo.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return RewindRead(message=outcome.message, removed_messages=outcome.removed_messages)


@router.delete("/ai/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(db: DbSession, current_user: CurrentUser, conversation_id: str) -> None:
    """Delete a conversation and its transcript.

    The token-usage ledger survives: its foreign key is SET NULL, so deleting a
    chat does not erase the record of what it spent.
    """
    conversation = _owned_conversation(db, current_user, conversation_id)
    db.delete(conversation)
    db.commit()
