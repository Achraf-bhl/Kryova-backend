"""CATIA V5 endpoints.

The agent drives CATIA through `app/ai/tools.py`; these routes exist for the
desktop shell's status panel and for a user who wants to trigger a sync by hand
rather than by asking.

`/status` never fails -- a machine without CATIA is a normal state to render,
not an error. Everything that actually needs CATIA returns 503 with an
actionable message when it is absent.
"""

from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, MediaServiceDep, OwnedProject
from app.catia import (
    CATIABridgeError,
    CatiaStatus,
    get_status,
    launch,
    list_open_documents,
    new_part,
)

router = APIRouter(prefix="/catia", tags=["catia"])


class CatiaStatusRead(BaseModel):
    running: bool
    version: str | None = None
    open_documents: int = 0
    active_document: str | None = None
    detail: str | None = Field(
        default=None, description="Why CATIA is unavailable, and what to do about it."
    )


class CatiaDocumentRead(BaseModel):
    name: str
    path: str | None
    doc_type: str


class LaunchRequest(BaseModel):
    new_part: bool = Field(
        default=True, description="Also open an empty CATPart to model in."
    )


def _as_read(status_obj: CatiaStatus) -> CatiaStatusRead:
    return CatiaStatusRead(
        running=status_obj.running,
        version=status_obj.version,
        open_documents=status_obj.document_count,
        active_document=status_obj.active_document,
        detail=status_obj.detail,
    )


def _unavailable(exc: CATIABridgeError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))


@router.get("/status", response_model=CatiaStatusRead)
def catia_status(current_user: CurrentUser) -> CatiaStatusRead:
    """Always 200: 'CATIA is not here' is a state the UI renders, not a failure."""
    return _as_read(get_status())


@router.get("/documents", response_model=list[CatiaDocumentRead])
def catia_documents(current_user: CurrentUser) -> list[CatiaDocumentRead]:
    try:
        return [
            CatiaDocumentRead(name=doc.name, path=doc.path, doc_type=doc.doc_type)
            for doc in list_open_documents()
        ]
    except CATIABridgeError as exc:
        raise _unavailable(exc) from exc


@router.post("/launch", response_model=CatiaStatusRead)
def catia_launch(
    current_user: CurrentUser,
    payload: Annotated[LaunchRequest, Body()] = LaunchRequest(),
) -> CatiaStatusRead:
    """Start CATIA and put it on screen, optionally with a fresh part."""
    try:
        result = launch(visible=True)
        if payload.new_part:
            new_part()
            result = get_status()
    except CATIABridgeError as exc:
        raise _unavailable(exc) from exc
    return _as_read(result)


class SyncRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post(
    "/projects/{project_id}/sync",
    status_code=status.HTTP_201_CREATED,
)
def sync_geometry(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    media: MediaServiceDep,
    payload: Annotated[SyncRequest, Body()] = SyncRequest(),
) -> dict:
    """Export the active CATIA document into the project as a geometry version.

    Shares its implementation with the agent tool, so the button and the
    assistant cannot drift apart.
    """
    from app.ai.tools import ToolBox, ToolError

    toolbox = ToolBox(db=db, user=current_user, project_id=project.id)
    try:
        return toolbox.call(
            "sync_geometry_from_catia",
            {"project_id": project.id, "note": payload.note},
            allow_mutations=True,
        )
    except ToolError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
