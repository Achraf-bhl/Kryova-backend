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
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import (
    Completion,
    LLMError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    LoadCaseDraft,
    ResultInterpretation,
    draft_load_case,
    generate_title,
    get_provider,
    interpret_result,
)
from app.ai import usage as token_usage
from app.ai.agent import AgentReply, run_agent, stream_agent, summarise_step
from app.ai.prompts import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from app.ai.provider import TokenUsage
from app.ai.state import bound_document_name
from app.ai.tools import ToolBox, tool_label
from app.api.deps import (
    CurrentUser,
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    OwnedProject,
    SessionScopeDep,
)
from app.core.config import settings
from app.models import (
    Conversation,
    ConversationMessage,
    GeometryVersion,
    JobStatus,
    MessageRole,
    SimulationJob,
    User,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


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


def _enforce_budget(db: Session, user: User) -> None:
    """Refuse a turn that starts over the daily allowance.

    Checked before the call, never during: an agent cut off between a tool call
    and its result leaves a transcript describing work whose outcome nobody
    saw, which is worse for the user than a slightly overrun budget.
    """
    if token_usage.over_budget(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=token_usage.budget_message(db, user.id),
        )


def _record(
    db: Session,
    user: User,
    usage: TokenUsage,
    *,
    purpose: str,
    provider: LLMProvider,
    conversation: Conversation | None = None,
) -> None:
    token_usage.record(
        db,
        user=user,
        usage=usage,
        purpose=purpose,
        provider=provider.name,
        model=provider.model,
        conversation=conversation,
    )
    db.commit()


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
    project: OwnedProject, db: DbSession, current_user: CurrentUser, simulation_id: str
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

    _enforce_budget(db, current_user)
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
    )
    return completion.value


@router.post("/projects/{project_id}/ai/load-case", response_model=LoadCaseDraft)
def draft_project_load_case(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
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

    _enforce_budget(db, current_user)
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
    )
    return completion.value


# --------------------------------------------------------------------------
# Agent chat: the model chooses the tools and drives the flow itself.
# --------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
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
        title=payload.message[:60],
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


@router.post("/ai/chat", response_model=ChatResponse)
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
    _enforce_budget(db, current_user)
    conversation = _resolve_conversation(db, current_user, payload)

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
    )

    try:
        reply: AgentReply = run_agent(
            db=db,
            provider=provider,
            conversation=conversation,
            toolbox=toolbox,
            user_message=payload.message,
            user=current_user,
            allow_mutations=payload.allow_mutations,
            max_tokens=settings.ai_max_tokens,
        )
    except LLMError as exc:
        # The user turn is already persisted, so roll back to the last committed
        # state rather than leaving a question with no answer in the transcript.
        db.rollback()
        raise _translate(exc) from exc

    # The agent may have created a project this turn. Adopt it as the
    # conversation's scope so the next turn resolves "the latest run" without
    # the client having to pass an id it only just learned about.
    if conversation.project_id is None and toolbox.project_id:
        conversation.project_id = toolbox.project_id

    _maybe_title(db, current_user, provider, conversation, payload.message, reply.text)
    _record(
        db,
        current_user,
        reply.usage,
        purpose=token_usage.PURPOSE_CHAT,
        provider=provider,
        conversation=conversation,
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


@router.post("/ai/chat/stream")
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
    _enforce_budget(db, current_user)
    conversation = _resolve_conversation(db, current_user, payload)
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
    )
    conversation_id = conversation.id

    def events() -> Iterator[str]:
        # The conversation id goes first so the client can store it before any
        # work happens -- a stream that dies mid-turn still leaves a resumable
        # conversation rather than an orphan.
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': conversation_id})}\n\n"
        reply_text = ""
        spent = TokenUsage()
        recorded = False

        def settle() -> None:
            """Persist what this turn actually cost, exactly once.

            This runs from `finally`, so it also runs when the client hangs up
            mid-stream and `GeneratorExit` is thrown at a `yield`. Previously
            accounting sat after the loop with no `finally`: the transcript was
            already committed by `stream_agent`, but no `AITokenUsage` row was
            ever written, so aborting every stream was unmetered, unlimited
            spend against a budget that never advanced.
            """
            nonlocal recorded
            if recorded:
                return
            recorded = True
            if not (spent.prompt_tokens or spent.completion_tokens):
                return
            try:
                _record(
                    db,
                    current_user,
                    spent,
                    purpose=token_usage.PURPOSE_CHAT,
                    provider=provider,
                    conversation=conversation,
                )
            except Exception:  # noqa: BLE001 - accounting must not mask the real error
                logger.exception(
                    "Failed to record token usage for conversation %s", conversation_id
                )
                db.rollback()

        try:
            for event in stream_agent(
                db=db,
                provider=provider,
                conversation=conversation,
                toolbox=toolbox,
                user_message=payload.message,
                user=current_user,
                allow_mutations=payload.allow_mutations,
                max_tokens=settings.ai_max_tokens,
            ):
                if event["type"] == "message":
                    reply_text = event["content"]
                elif event["type"] == "done":
                    spent = TokenUsage(
                        prompt_tokens=event.get("prompt_tokens", 0),
                        completion_tokens=event.get("completion_tokens", 0),
                    )
                yield f"data: {json.dumps(event, default=str)}\n\n"

            # Same adoption as the non-streaming route: a project created
            # mid-stream has to outlive this request.
            if conversation.project_id is None and toolbox.project_id:
                conversation.project_id = toolbox.project_id
            _maybe_title(db, current_user, provider, conversation, payload.message, reply_text)
            settle()
            yield ("data: " + json.dumps({"type": "title", "title": conversation.title}) + "\n\n")
        except LLMError as exc:
            # Roll back the failed unit of work, then still bill the provider
            # calls this turn already made -- steps 1..N-1 of a multi-step turn
            # are real spend even though step N failed.
            db.rollback()
            settle()
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
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


class ConversationRead(BaseModel):
    conversation_id: str
    title: str
    project_id: str | None
    created_at: str
    updated_at: str
    has_catia_document: bool
    catia_document: str | None
    prompt_tokens: int
    completion_tokens: int
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


class ConversationPage(BaseModel):
    total: int = Field(ge=0)
    page: int = Field(gt=0)
    page_size: int = Field(gt=0)
    items: list[ConversationSummaryRead]


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


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
) -> ConversationPage:
    """The user's conversations, newest activity first.

    Ordered by `updated_at` rather than `created_at`: a sidebar is a list of
    what you were last working on, not what you started first.
    """
    total = (
        db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.owner_id == current_user.id)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(Conversation)
            .where(Conversation.owner_id == current_user.id)
            .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
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
            )
            for row in rows
        ],
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
            )
        )

    document = bound_document_name(db, conversation.id)
    return ConversationRead(
        conversation_id=conversation.id,
        title=conversation.title,
        project_id=conversation.project_id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat(),
        has_catia_document=document is not None,
        catia_document=document,
        prompt_tokens=conversation.prompt_tokens,
        completion_tokens=conversation.completion_tokens,
        messages=messages,
    )


@router.patch("/ai/conversations/{conversation_id}", response_model=ConversationRead)
def rename_conversation(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    payload: Annotated[ConversationUpdate, ...],
) -> ConversationRead:
    """Rename a conversation. The only field a user may edit."""
    conversation = _owned_conversation(db, current_user, conversation_id)
    conversation.title = payload.title.strip()[:255]
    db.commit()
    return read_conversation(db, current_user, conversation_id)


@router.delete("/ai/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(db: DbSession, current_user: CurrentUser, conversation_id: str) -> None:
    """Delete a conversation and its transcript.

    The token-usage ledger survives: its foreign key is SET NULL, so deleting a
    chat does not erase the record of what it spent.
    """
    conversation = _owned_conversation(db, current_user, conversation_id)
    db.delete(conversation)
    db.commit()
