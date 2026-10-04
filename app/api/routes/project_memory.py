"""Project memory over HTTP: see the facts, add one, edit one, confirm or dismiss one.

ROAD_TO_10 2.7. What the agent *proposes* appears here as `proposed` and is invisible to the
model until a person confirms it -- the rule the model file states. Reading is open to any
member of the project's organisation, including viewers (`ReadableProject`); writing is
`MEMBER` and above (`OwnedProject`), the same ladder the rest of the project follows.

**A fact is addressed through its project**, and an id that belongs to another project is a
404, so an id found in one project cannot be probed against another.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, OwnedProject, ReadableProject
from app.core import project_memory
from app.models import ProjectMemory
from app.schemas.project_memory import (
    MemoryCreate,
    MemoryPage,
    MemoryRead,
    MemoryUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/memory", tags=["project-memory"])

_NOT_FOUND = "No such fact in this project."


def _refusal(exc: project_memory.MemoryRefusal) -> HTTPException:
    code = (
        status.HTTP_422_UNPROCESSABLE_CONTENT
        if isinstance(exc, project_memory.BadText)
        else status.HTTP_409_CONFLICT
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.get("", response_model=MemoryPage)
def list_memory(
    db: DbSession,
    project: ReadableProject,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> MemoryPage:
    """The project's facts, oldest first. Proposals are included and say so."""
    total = (
        db.scalar(
            select(func.count())
            .select_from(ProjectMemory)
            .where(ProjectMemory.project_id == project.id)
        )
        or 0
    )
    rows = db.scalars(
        select(ProjectMemory)
        .where(ProjectMemory.project_id == project.id)
        .order_by(ProjectMemory.created_at, ProjectMemory.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return MemoryPage(
        items=[MemoryRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
def add_memory(
    db: DbSession,
    current_user: CurrentUser,
    project: OwnedProject,
    payload: MemoryCreate,
    response: Response,
) -> MemoryRead:
    """Add a fact. A person typed it, so it is confirmed at once.

    A sentence the project already holds comes back as it is (200, not 201), and if it was a
    proposal waiting for them, typing it again is the confirmation.
    """
    try:
        written = project_memory.create(db, project, current_user, payload.text)
    except project_memory.MemoryRefusal as exc:
        raise _refusal(exc) from exc
    db.commit()
    if not written.created:
        response.status_code = status.HTTP_200_OK
    return MemoryRead.model_validate(written.memory)


@router.patch("/{memory_id}", response_model=MemoryRead)
def edit_memory(
    db: DbSession, project: OwnedProject, memory_id: str, payload: MemoryUpdate
) -> MemoryRead:
    row = project_memory.get(db, project, memory_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    try:
        project_memory.edit(db, row, payload.text)
    except project_memory.MemoryRefusal as exc:
        raise _refusal(exc) from exc
    db.commit()
    return MemoryRead.model_validate(row)


@router.post("/{memory_id}/confirm", response_model=MemoryRead)
def confirm_memory(
    db: DbSession, current_user: CurrentUser, project: OwnedProject, memory_id: str
) -> MemoryRead:
    """Let the model read a proposed fact. The person who pressed this is recorded."""
    row = project_memory.get(db, project, memory_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    project_memory.confirm(db, row, current_user)
    db.commit()
    return MemoryRead.model_validate(row)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_memory(db: DbSession, project: OwnedProject, memory_id: str) -> None:
    """Delete a fact, or dismiss a proposal. Both are the same act."""
    row = project_memory.get(db, project, memory_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    project_memory.forget(db, row)
    db.commit()
