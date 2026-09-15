from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, MediaServiceDep, OwnedProject
from app.core.config import settings
from app.geometry.formats import GEOMETRY_FORMATS, detect_format, rejection_reason
from app.geometry.inspect import GeometryError, inspect
from app.media import MediaNotFound, MediaTooLarge
from app.models import GeometryVersion, MediaKind
from app.models.attachment import Attachment
from app.schemas import GeometryVersionPage, GeometryVersionRead

router = APIRouter(prefix="/projects/{project_id}/geometry", tags=["geometry"])


def _require_supported(filename: str) -> str:
    file_format = detect_format(filename)
    if file_format is None:
        # A native CAD file (.CATPart, .sldprt) gets told how to convert it,
        # rather than a bare list of extensions it has to work backwards from.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=rejection_reason(filename),
        )
    return file_format


def _next_version_number(db: DbSession, project_id: str) -> int:
    highest = db.scalar(
        select(func.max(GeometryVersion.version_number)).where(
            GeometryVersion.project_id == project_id
        )
    )
    return (highest or 0) + 1


@router.post("", response_model=GeometryVersionRead, status_code=status.HTTP_201_CREATED)
def upload_geometry(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    media: MediaServiceDep,
    file: Annotated[UploadFile, File()],
    note: Annotated[str | None, Form()] = None,
) -> GeometryVersion:
    """Upload a CAD file. Every upload becomes a new immutable version.

    For files large enough that a single request is a bad bet, use the resumable
    chunked flow under `/media/uploads` and then `POST .../geometry/attach`.
    """
    filename = Path(file.filename or "").name
    file_format = _require_supported(filename)

    try:
        stored = media.store_stream(
            owner_id=current_user.id,
            kind=MediaKind.CAD,
            filename=filename,
            stream=file.file,
            content_type=file.content_type or "application/octet-stream",
            max_bytes=settings.max_upload_bytes,
        )
    except MediaTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_upload_bytes} byte limit",
        ) from exc

    if stored.size_bytes == 0:
        raise HTTPException(status_code=422, detail="File is empty")

    return _attach(db, media, project.id, stored, file_format, note)


@router.post("/attach", response_model=GeometryVersionRead, status_code=status.HTTP_201_CREATED)
def attach_uploaded_geometry(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    media: MediaServiceDep,
    media_id: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
) -> GeometryVersion:
    """Turn an already-uploaded media blob into a geometry version.

    This is the tail of the resumable chunked upload: the bytes are already on
    disk, so all that is left is to validate and register them.
    """
    from app.models import Media

    stored = db.get(Media, media_id)
    if stored is None or stored.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    if stored.kind is not MediaKind.CAD:
        raise HTTPException(
            status_code=422,
            detail=f"Media {media_id} is a {stored.kind.value}, not a CAD file",
        )

    file_format = _require_supported(stored.filename)
    return _attach(db, media, project.id, stored, file_format, note)


@router.post(
    "/from-attachment", response_model=GeometryVersionRead, status_code=status.HTTP_201_CREATED
)
def geometry_from_attachment(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    media: MediaServiceDep,
    attachment_id: Annotated[str, Form()],
    note: Annotated[str | None, Form()] = None,
) -> GeometryVersion:
    """Make a CAD file attached to a conversation a geometry version (P4.2).

    The attachment reader refuses solid geometry, because there is no text in it
    to quote, and its message says to use the file as geometry. Until 2026-09-15
    that meant uploading the same bytes a second time. They are already stored,
    so this registers them.

    * **Access comes from the path, never from the attachment.** Writing to the
      project needs `OwnedProject`, and the attachment must be the caller's own.
      Either miss is 404. `Attachment.project_id` is not consulted.
    * **Only what was detected as solid geometry.** Detection read the bytes, so
      a `.step` that is really a text file was recorded as text and is refused
      here, saying what it was read as.
    * **A file that does not inspect is refused and its blob is kept.** The
      upload routes discard an unreadable CAD blob. This one is an attachment
      the user can still see, and deleting it would leave that row pointing at
      nothing.
    """
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    file_format = attachment.detected_format
    # Only the solid-geometry detection yields one of these labels; a document
    # is `text`, `pdf`, `xlsx`, `dxf` and so on.
    if file_format not in GEOMETRY_FORMATS:
        *others, last = [name.upper() for name in GEOMETRY_FORMATS]
        supported = f"{', '.join(others)} or {last}"
        raise HTTPException(
            status_code=422,
            detail=(
                f"This attachment was read as {attachment.detected_format}, not as solid "
                f"geometry, so it cannot become a geometry version. Attach the part as "
                f"{supported} and use that."
            ),
        )
    return _attach(db, media, project.id, attachment.media, file_format, note, discard=False)


def _attach(
    db, media, project_id: str, stored, file_format: str, note: str | None, *, discard: bool = True
):
    try:
        stats = inspect(media.local_path(stored), file_format)
    except GeometryError as exc:
        # The blob is content-addressed and may be shared, so let the orphan
        # sweep decide whether it can go rather than deleting it here. A blob
        # under an attachment is not offered to the sweep at all.
        if discard:
            media.delete(stored)
            db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MediaNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Uploaded file is no longer on disk"
        ) from exc

    version = GeometryVersion(
        project_id=project_id,
        media_id=stored.id,
        version_number=_next_version_number(db, project_id),
        filename=stored.filename,
        file_format=file_format,
        note=note,
        stats=stats,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.get("", response_model=GeometryVersionPage)
def list_geometry_versions(
    project: OwnedProject,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> GeometryVersionPage:
    owner_filter = GeometryVersion.project_id == project.id
    stmt = (
        select(GeometryVersion)
        .where(owner_filter)
        .order_by(GeometryVersion.version_number.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = db.scalar(select(func.count()).select_from(GeometryVersion).where(owner_filter)) or 0
    return GeometryVersionPage(
        total=total, page=page, page_size=page_size, items=list(db.scalars(stmt))
    )


def _get_version(db: DbSession, project_id: str, version_number: int) -> GeometryVersion:
    version = db.scalar(
        select(GeometryVersion).where(
            GeometryVersion.project_id == project_id,
            GeometryVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Geometry version not found"
        )
    return version


@router.get("/{version_number}", response_model=GeometryVersionRead)
def read_geometry_version(
    project: OwnedProject, db: DbSession, version_number: int
) -> GeometryVersion:
    return _get_version(db, project.id, version_number)


@router.get("/{version_number}/download")
def download_geometry_version(
    project: OwnedProject, db: DbSession, media: MediaServiceDep, version_number: int
):
    version = _get_version(db, project.id, version_number)
    if not media.exists(version.media):
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Stored file is no longer available"
        )
    # Streamed in chunks: a CAD file can be hundreds of megabytes.
    return StreamingResponse(
        media.iter_chunks(version.media),
        media_type=version.media.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{version.filename}"',
            "Content-Length": str(version.media.size_bytes),
        },
    )
