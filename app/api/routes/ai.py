"""AI endpoints.

Both routes are thin: resolve and authorise the row, hand it to the service,
translate provider failures into HTTP. The model that answers is chosen by
configuration -- see `app/ai/providers/`.
"""

import json
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai import (
    LLMError,
    LLMRefusal,
    LLMUnavailable,
    LoadCaseDraft,
    ResultInterpretation,
    draft_load_case,
    get_provider,
    interpret_result,
)
from app.ai.agent import AgentReply, run_agent, stream_agent
from app.ai.tools import ToolBox
from app.api.deps import CurrentUser, DbSession, OwnedProject
from app.core.config import settings
from app.models import Conversation, GeometryVersion, JobStatus, SimulationJob

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


def _provider_or_503():
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
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        )
    if isinstance(exc, LLMRefusal):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


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
    project: OwnedProject, db: DbSession, simulation_id: str
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

    provider = _provider_or_503()
    try:
        return interpret_result(
            provider,
            result=job.result,
            load_case=job.load_case,
            mesh_stats=job.mesh_stats,
            element_size_mm=job.element_size_mm,
        )
    except LLMError as exc:
        raise _translate(exc) from exc


@router.post("/projects/{project_id}/ai/load-case", response_model=LoadCaseDraft)
def draft_project_load_case(
    project: OwnedProject, db: DbSession, payload: Annotated[LoadCaseRequest, ...]
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

    provider = _provider_or_503()
    try:
        return draft_load_case(
            provider, description=payload.description, bounding_box=bounding_box
        )
    except LLMError as exc:
        raise _translate(exc) from exc


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
    reply: str
    steps: list[AgentStepRead] = Field(
        description="Tool calls the agent made, in order, so the UI can show its work."
    )
    truncated: bool = Field(
        description="True when the step budget ran out before the agent finished."
    )


def _resolve_conversation(
    db: DbSession, user: Any, payload: ChatRequest
) -> Conversation:
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        # 404 rather than 403 for someone else's conversation, matching the
        # rest of the API -- never confirm that an id exists.
        if conversation is None or conversation.owner_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )
        return conversation

    conversation = Conversation(
        owner_id=user.id,
        project_id=payload.project_id,
        title=payload.message[:60],
    )
    db.add(conversation)
    db.flush()
    return conversation


@router.post("/ai/chat", response_model=ChatResponse)
def chat(
    db: DbSession, current_user: CurrentUser, payload: Annotated[ChatRequest, ...]
) -> ChatResponse:
    """Talk to the agent. It decides which tools to call and in what order.

    The conversation is the memory: pass `conversation_id` back on each turn and
    the agent replays everything it did before, including the calls that failed,
    so it does not repeat them.
    """
    conversation = _resolve_conversation(db, current_user, payload)

    if payload.project_id and conversation.project_id is None:
        conversation.project_id = payload.project_id

    provider = _provider_or_503()
    toolbox = ToolBox(db=db, user=current_user, project_id=conversation.project_id)

    try:
        reply: AgentReply = run_agent(
            db=db,
            provider=provider,
            conversation=conversation,
            toolbox=toolbox,
            user_message=payload.message,
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
        db.commit()

    return ChatResponse(
        conversation_id=conversation.id,
        reply=reply.text,
        steps=[
            AgentStepRead(tool=s.tool, arguments=s.arguments, ok=s.ok, result=s.result)
            for s in reply.steps
        ],
        truncated=reply.truncated,
    )


class ConversationMessageRead(BaseModel):
    """One stored turn. Typed rather than a bare dict so the schema is not `any`."""

    role: str
    content: str | None
    tool_name: str | None
    is_error: bool
    created_at: str


class ConversationRead(BaseModel):
    conversation_id: str
    title: str
    project_id: str | None
    messages: list[ConversationMessageRead]


@router.get("/ai/conversations/{conversation_id}", response_model=ConversationRead)
def read_conversation(
    db: DbSession, current_user: CurrentUser, conversation_id: str
) -> ConversationRead:
    """The stored transcript, so a client can rehydrate a conversation."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return ConversationRead(
        conversation_id=conversation.id,
        title=conversation.title,
        project_id=conversation.project_id,
        messages=[
            ConversationMessageRead(
                role=m.role.value,
                content=m.content,
                tool_name=m.tool_name,
                is_error=m.is_error,
                created_at=m.created_at.isoformat(),
            )
            for m in conversation.messages
        ],
    )


@router.post("/ai/chat/stream")
def chat_stream(
    db: DbSession, current_user: CurrentUser, payload: Annotated[ChatRequest, ...]
) -> StreamingResponse:
    """The same agent loop, streamed as Server-Sent Events.

    The UI subscribes and renders each step as it happens -- which tool the
    agent reached for, what it found, how long it took -- instead of showing a
    spinner for however long the whole turn takes.

    Event types: `thinking`, `narration`, `tool_start`, `tool_end`, `message`,
    `done`, `error`.
    """
    conversation = _resolve_conversation(db, current_user, payload)
    if payload.project_id and conversation.project_id is None:
        conversation.project_id = payload.project_id

    provider = _provider_or_503()
    toolbox = ToolBox(db=db, user=current_user, project_id=conversation.project_id)
    conversation_id = conversation.id

    def events() -> Iterator[str]:
        # The conversation id goes first so the client can store it before any
        # work happens -- a stream that dies mid-turn still leaves a resumable
        # conversation rather than an orphan.
        yield f"data: {json.dumps({'type': 'start', 'conversation_id': conversation_id})}\n\n"
        try:
            for event in stream_agent(
                db=db,
                provider=provider,
                conversation=conversation,
                toolbox=toolbox,
                user_message=payload.message,
                allow_mutations=payload.allow_mutations,
                max_tokens=settings.ai_max_tokens,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
            # Same adoption as the non-streaming route: a project created
            # mid-stream has to outlive this request.
            if conversation.project_id is None and toolbox.project_id:
                conversation.project_id = toolbox.project_id
                db.commit()
        except LLMError as exc:
            db.rollback()
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

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
