"""AI endpoints.

Both routes are thin: resolve and authorise the row, hand it to the service,
translate provider failures into HTTP. The model that answers is chosen by
configuration -- see `app/ai/providers/`.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
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
from app.api.deps import DbSession, OwnedProject
from app.core.config import settings
from app.models import GeometryVersion, JobStatus, SimulationJob

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
