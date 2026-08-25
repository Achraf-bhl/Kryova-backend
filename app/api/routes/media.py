from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, MediaServiceDep
from app.core.config import settings
from app.media import MediaNotFound, UploadError
from app.models import Media, MediaUploadSession
from app.schemas import (
    MediaRead,
    UploadSessionCreate,
    UploadSessionRead,
)

router = APIRouter(prefix="/media", tags=["media"])


def _own_media(db: DbSession, current_user: CurrentUser, media_id: str) -> Media:
    media = db.get(Media, media_id)
    if media is None or media.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    return media


def _own_session(db: DbSession, current_user: CurrentUser, upload_id: str) -> MediaUploadSession:
    session = db.get(MediaUploadSession, upload_id)
    if session is None or session.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Upload session not found"
        )
    return session


# -- resumable chunked uploads ------------------------------------------------


@router.post("/uploads", response_model=UploadSessionRead, status_code=status.HTTP_201_CREATED)
def begin_upload(
    payload: UploadSessionCreate,
    current_user: CurrentUser,
    db: DbSession,
    media: MediaServiceDep,
) -> MediaUploadSession:
    """Open a resumable upload.

    The client splits the file into `chunk_size` pieces and PUTs each one by
    index. Order does not matter and a chunk may be retried, so a flaky
    connection costs only the chunks it dropped.
    """
    try:
        session = media.begin_upload(
            owner_id=current_user.id,
            kind=payload.kind,
            filename=payload.filename,
            total_size_bytes=payload.total_size_bytes,
            content_type=payload.content_type,
            chunk_size=payload.chunk_size,
            expected_sha256=payload.expected_sha256,
        )
    except UploadError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(session)
    return session


@router.put("/uploads/{upload_id}/chunks/{index}", response_model=UploadSessionRead)
async def upload_chunk(
    request: Request,
    upload_id: str,
    index: int,
    current_user: CurrentUser,
    db: DbSession,
    media: MediaServiceDep,
) -> MediaUploadSession:
    """Send one chunk as a raw request body."""
    session = _own_session(db, current_user, upload_id)
    body = await request.body()
    try:
        media.save_chunk(session, index, body)
    except UploadError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(session)
    return session


@router.get("/uploads/{upload_id}", response_model=UploadSessionRead)
def read_upload(
    upload_id: str, current_user: CurrentUser, db: DbSession
) -> MediaUploadSession:
    """Progress for a session, including which chunks are still missing."""
    return _own_session(db, current_user, upload_id)


@router.post("/uploads/{upload_id}/complete", response_model=MediaRead)
def complete_upload(
    upload_id: str, current_user: CurrentUser, db: DbSession, media: MediaServiceDep
) -> Media:
    """Assemble the chunks into a single blob and register it."""
    session = _own_session(db, current_user, upload_id)
    try:
        stored = media.complete_upload(session)
    except UploadError as exc:
        db.commit()  # keep the abort/error the service recorded
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(stored)
    return stored


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(
    upload_id: str, current_user: CurrentUser, db: DbSession, media: MediaServiceDep
) -> None:
    session = _own_session(db, current_user, upload_id)
    media.abort_upload(session, error="Aborted by the client")
    db.commit()


# -- stored media -------------------------------------------------------------


@router.get("", response_model=list[MediaRead])
def list_media(current_user: CurrentUser, db: DbSession) -> list[Media]:
    stmt = (
        select(Media)
        .where(Media.owner_id == current_user.id)
        .order_by(Media.created_at.desc())
    )
    return list(db.scalars(stmt))


@router.get("/{media_id}", response_model=MediaRead)
def read_media(media_id: str, current_user: CurrentUser, db: DbSession) -> Media:
    return _own_media(db, current_user, media_id)


@router.get("/{media_id}/content")
def download_media(
    media_id: str,
    current_user: CurrentUser,
    db: DbSession,
    media: MediaServiceDep,
    chunk_size: Annotated[int | None, Query(gt=0)] = None,
):
    stored = _own_media(db, current_user, media_id)
    try:
        chunks = media.iter_chunks(stored, chunk_size or settings.media_chunk_size)
    except MediaNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="File is no longer on disk"
        ) from exc
    return StreamingResponse(
        chunks,
        media_type=stored.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{stored.filename}"',
            "Content-Length": str(stored.size_bytes),
        },
    )


@router.post("/{media_id}/verify", response_model=dict)
def verify_media(
    media_id: str, current_user: CurrentUser, db: DbSession, media: MediaServiceDep
) -> dict:
    """Re-hash the blob on disk and confirm it still matches its checksum."""
    stored = _own_media(db, current_user, media_id)
    try:
        return {"media_id": stored.id, "sha256": stored.sha256, "intact": media.verify(stored)}
    except MediaNotFound:
        return {"media_id": stored.id, "sha256": stored.sha256, "intact": False}


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    media_id: str, current_user: CurrentUser, db: DbSession, media: MediaServiceDep
) -> None:
    stored = _own_media(db, current_user, media_id)
    media.delete(stored)
    db.commit()
