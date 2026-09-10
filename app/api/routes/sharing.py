"""Share links and project transfer (P2.5).

Two routers, and the split is the whole design:

* **`router`** — authenticated, under `/projects/{project_id}`. Issuing,
  listing and revoking links, and moving a project between organisations. Every
  route goes through the same ownership guards as the rest of the service.
* **`public_router`** — **unauthenticated**, under `/share`. The one place in
  this service that answers a request with no principal. It resolves a token to
  exactly one project and reads that project; it takes no id, so there is no
  identifier for a caller to substitute and no comparison for us to get wrong.

The public route deliberately does not use `DbSession`'s tenant context, because
there is no tenant to enter — RLS would (correctly) hide everything. That makes
`core.sharing.resolve` the only gate, which is why it is written to be as narrow
as it is. Read its docstring before changing anything here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import AuditDep, CurrentUser, DbSession, MediaServiceDep, OwnedProject
from app.api.rate_limit import RateLimit
from app.core import sharing
from app.core.config import settings
from app.models import GeometryVersion, Organisation, ShareLink, SimulationJob
from app.models.audit import AuditAction, AuditOutcome
from app.models.organisation import OrgRole, membership_for
from app.schemas.sharing import (
    ProjectTransferCreate,
    ProjectTransferRead,
    SharedGeometry,
    SharedPackage,
    SharedSimulation,
    ShareLinkCreate,
    ShareLinkIssued,
    ShareLinkRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["sharing"])
public_router = APIRouter(prefix="/share", tags=["sharing"])


def _share_url(token: str) -> str:
    """The link a human is given. Built from the frontend origin, because the
    page that renders a package is a frontend route — a link into the API would
    hand a supplier raw JSON."""
    return f"{settings.frontend_url.rstrip('/')}/shared/{token}"


# ---------------------------------------------------------------------------
# Issuing (authenticated)
# ---------------------------------------------------------------------------


@router.post(
    "/shares", response_model=ShareLinkIssued, status_code=status.HTTP_201_CREATED
)
def create_share_link(
    payload: ShareLinkCreate,
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
) -> ShareLinkIssued:
    """Mint a read-only link to this project.

    The raw token is in this response and nowhere else, ever. It is not stored,
    not logged and not returned by the list endpoint — losing it means issuing
    another, which is the same rule every credential here follows.
    """
    try:
        issued = sharing.issue(
            db,
            project=project,
            created_by=current_user,
            days=payload.days,
            label=payload.label,
            note=payload.note,
            allow_geometry_download=payload.allow_geometry_download,
        )
    except sharing.SharingError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    db.commit()
    body = ShareLinkRead.model_validate(issued.link).model_dump()
    return ShareLinkIssued(**body, token=issued.token, url=_share_url(issued.token))


@router.get("/shares", response_model=list[ShareLinkRead])
def list_share_links(project: OwnedProject, db: DbSession) -> list[ShareLinkRead]:
    """Every link ever issued for this project, live or not.

    Revoked and expired rows are included on purpose: "who did we share this
    with, and is it still open" is one question, and a list that silently drops
    the dead ones answers only half of it.
    """
    rows = db.scalars(
        select(ShareLink)
        .where(ShareLink.project_id == project.id)
        .order_by(ShareLink.created_at.desc())
    ).all()
    return [ShareLinkRead.model_validate(row) for row in rows]


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share_link(
    share_id: str, project: OwnedProject, db: DbSession
) -> None:
    """Close a link. Takes effect on the next request, not on the next expiry."""
    link = db.get(ShareLink, share_id)
    # A link belonging to another project is a 404, the same rule every
    # resource here follows -- otherwise share ids can be probed across
    # projects for existence.
    if link is None or link.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share link not found")
    sharing.revoke(db, link)
    db.commit()


# ---------------------------------------------------------------------------
# Transfer (authenticated)
# ---------------------------------------------------------------------------


@router.post("/transfer", response_model=ProjectTransferRead)
def transfer_project(
    payload: ProjectTransferCreate,
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    audit: AuditDep,
    request: Request,
) -> ProjectTransferRead:
    """Move this project to another organisation.

    **Administering both ends is required**, and both halves matter. Without the
    source check, any member could push a project out of a tenant; without the
    destination check, anyone could push their work *into* an organisation they
    merely belong to, and quota, billing and every member's visibility would
    follow it. `OwnedProject` gives the first, `membership_for` gives the
    second.

    The destination is a 404 when the caller is not in it — the same rule as
    everywhere else. An organisation you cannot administer is one you should not
    be able to confirm the existence of by id.
    """
    destination = db.get(Organisation, payload.to_organisation_id)
    membership = (
        membership_for(db, current_user.id, payload.to_organisation_id)
        if destination is not None
        else None
    )
    if destination is None or membership is None or not membership.role.at_least(OrgRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found"
        )

    source_membership = membership_for(db, current_user.id, project.organisation_id)
    if source_membership is None or not source_membership.role.at_least(OrgRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You need to be an administrator of the organisation that owns this "
                "project to move it out of there."
            ),
        )

    try:
        record = sharing.transfer(
            db,
            project=project,
            destination=destination,
            actor=current_user,
            reason=payload.reason,
        )
    except sharing.SharingError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused

    principal = getattr(request.state, "principal", None)
    if principal is not None:
        audit.record(
            AuditAction.PROJECT_TRANSFERRED,
            AuditOutcome.SUCCEEDED,
            principal=principal,
            target_type="project",
            target_id=project.id,
            reason=payload.reason,
            detail={
                "from_organisation_id": record.from_organisation_id,
                "to_organisation_id": record.to_organisation_id,
            },
        )
    db.commit()
    return ProjectTransferRead.model_validate(record)


@router.get("/transfers", response_model=list[ProjectTransferRead])
def list_transfers(project: OwnedProject, db: DbSession) -> list[ProjectTransferRead]:
    """Every tenant this project has belonged to, oldest first."""
    return [ProjectTransferRead.model_validate(row) for row in sharing.history(db, project)]


# ---------------------------------------------------------------------------
# Reading a package (UNAUTHENTICATED)
# ---------------------------------------------------------------------------

#: Keyed on the address, because by construction there is no principal here.
#: This is one of the few routes where an IP really is the best denominator
#: available — see `api/rate_limit.limit_key`.
_share_read_limit = RateLimit("share.read", max_requests=60, window_seconds=60)


def _package(db: DbSession, link: ShareLink) -> SharedPackage:
    geometry = db.scalars(
        select(GeometryVersion)
        .where(GeometryVersion.project_id == link.project_id)
        .order_by(GeometryVersion.version_number)
    ).all()
    runs = db.scalars(
        select(SimulationJob)
        .where(SimulationJob.project_id == link.project_id)
        .order_by(SimulationJob.created_at.desc())
    ).all()

    simulations = []
    for job in runs:
        result = job.result or {}
        simulations.append(
            SharedSimulation(
                analysis=job.analysis,
                status=job.status.value,
                created_at=job.created_at,
                # Straight through, unconverted. `mass_kg` is already
                # kilograms; the results page shipped a `/1000` here once and
                # this payload must not reintroduce it on the way out.
                max_von_mises_mpa=result.get("max_von_mises_mpa"),
                max_displacement_mm=result.get("max_displacement_mm"),
                mass_kg=result.get("mass_kg"),
                mesh_convergence=(result.get("mesh_convergence") or {}).get("verdict")
                if isinstance(result.get("mesh_convergence"), dict)
                else result.get("mesh_convergence"),
                solver=job.solver,
            )
        )

    return SharedPackage(
        project_name=link.project.name,
        project_description=link.project.description,
        organisation_name=link.organisation.name,
        shared_by=link.created_by.email,
        label=link.label,
        note=link.note,
        expires_at=link.expires_at,
        allow_geometry_download=link.allow_geometry_download,
        geometry=[
            SharedGeometry(
                version_number=version.version_number,
                filename=version.filename,
                created_at=version.created_at,
                size_bytes=version.size_bytes,
            )
            for version in geometry
        ],
        simulations=simulations,
    )


@public_router.get(
    "/{token}", response_model=SharedPackage, dependencies=[Depends(_share_read_limit)]
)
def read_shared_package(token: str, db: DbSession) -> SharedPackage:
    """The design package behind a share link. **No authentication.**

    Every way this can fail is a 404 with one message: expired, revoked, never
    existed, or the project has since moved to another tenant. Telling a holder
    that their token *was* valid is the one piece of information that makes
    guessing worth continuing.
    """
    link = sharing.resolve(db, token)
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link is not valid. It may have expired or been withdrawn.",
        )
    package = _package(db, link)
    sharing.record_view(db, link)
    db.commit()
    return package


@public_router.get(
    "/{token}/geometry/{version_number}",
    dependencies=[Depends(_share_read_limit)],
)
def download_shared_geometry(
    token: str, version_number: int, db: DbSession, media: MediaServiceDep
) -> StreamingResponse:
    """The CAD file itself, only if the link says so.

    `allow_geometry_download` is off by default and refused here with the same
    404 as an invalid link: a recipient who was sent a results package should
    not learn that the geometry exists behind a flag somebody could flip.

    Streamed, not read: a CAD file is routinely hundreds of megabytes, and
    nothing in this service loads a whole one into memory. Same shape as
    `routes/geometry.py::download_geometry_version`.
    """
    link = sharing.resolve(db, token)
    if link is None or not link.allow_geometry_download:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This link is not valid. It may have expired or been withdrawn.",
        )
    version = db.scalar(
        select(GeometryVersion).where(
            GeometryVersion.project_id == link.project_id,
            GeometryVersion.version_number == version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geometry not found")
    if not media.exists(version.media):
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="That file is no longer available"
        )
    return StreamingResponse(
        media.iter_chunks(version.media),
        media_type=version.media.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{version.filename}"',
            "Content-Length": str(version.media.size_bytes),
        },
    )

__all__ = ["public_router", "router"]
