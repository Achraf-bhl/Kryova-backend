"""Turning a STEP export from CATIA into a Kryova geometry version.

This is the seam that closes the loop:

    chat -> CATIA geometry -> STEP -> geometry version -> mesh -> solve ->
    interpret -> propose a change -> apply it in CATIA -> re-run.

Everything here goes through the same machinery as a browser upload -- the same
`MediaService`, the same content-addressed blob store, the same
`geometry.inspect`, the same `GeometryVersion` row with the same version
numbering. A STEP file that arrived over the bridge is indistinguishable
downstream from one a user dragged in, which is the point: the mesher, the
solver and the viewer must not need to know where geometry came from.

The one thing deliberately *not* reused is `app.api.routes.geometry._attach`.
It is a route helper that raises `HTTPException`, and this is called from an
agent tool, not from a request. Importing a route module into a service would
also invert the layering the rest of the codebase keeps (route -> service, never
the other way). The four lines of version numbering are duplicated instead, and
`app/api/routes/geometry.py` is the file to keep them in step with.
"""

import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.geometry.formats import detect_format
from app.geometry.inspect import GeometryError, inspect
from app.media import MediaService, MediaTooLarge
from app.models import GeometryVersion, MediaKind

logger = logging.getLogger(__name__)


class GeometryImportError(RuntimeError):
    """The exported file could not become a geometry version. Message is for the model."""


def _next_version_number(db: Session, project_id: str) -> int:
    highest = db.scalar(
        select(func.max(GeometryVersion.version_number)).where(
            GeometryVersion.project_id == project_id
        )
    )
    return (highest or 0) + 1


def import_step_export(
    db: Session,
    media: MediaService,
    *,
    owner_id: str,
    project_id: str,
    path: Path,
    filename: str,
    note: str | None,
) -> GeometryVersion:
    """Register a STEP file exported from CATIA as a new geometry version.

    `path` is a temporary file the caller owns; the blob store copies out of it.
    """
    file_format = detect_format(filename)
    if file_format is None:
        # Only reachable if the daemon renamed the export. Named explicitly
        # because the alternative is a confusing failure three steps later in
        # the mesher.
        raise GeometryImportError(
            f"CATIA exported {filename!r}, which is not a format the mesher reads. "
            "The export must be STEP (.step or .stp)."
        )

    try:
        stored = media.store_path(
            owner_id=owner_id,
            kind=MediaKind.CAD,
            path=path,
            filename=filename,
            content_type="application/step",
            meta={"source": "catia_bridge"},
        )
    except MediaTooLarge as exc:
        raise GeometryImportError(f"The exported STEP file is too large to store: {exc}") from exc

    try:
        stats = inspect(media.local_path(stored), file_format)
    except GeometryError as exc:
        # Same posture as the upload route: drop the row and let the orphan
        # sweep decide the blob's fate, because content addressing means another
        # record may legitimately share it.
        media.delete(stored)
        raise GeometryImportError(
            f"CATIA produced a STEP file the geometry reader rejected: {exc}. "
            "Check that the part actually contains solid geometry, then export again."
        ) from exc

    version = GeometryVersion(
        project_id=project_id,
        media_id=stored.id,
        version_number=_next_version_number(db, project_id),
        filename=filename,
        file_format=file_format,
        note=note,
        stats=stats,
    )
    db.add(version)
    db.flush()
    logger.info(
        "CATIA export became geometry version %d of project %s",
        version.version_number,
        project_id,
    )
    return version
