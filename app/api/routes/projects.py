import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import String, cast, func, or_, select
from starlette.background import BackgroundTask

from app.api.deps import (
    CurrentUser,
    DbSession,
    MediaServiceDep,
    OwnedProject,
    ReadableProject,
    VerifiedUser,
)
from app.core import activity, project_archive, project_templates, projects
from app.core.config import settings
from app.models import Project, ProjectStar, User
from app.models.base import utcnow
from app.schemas import (
    ActivityEntryRead,
    ActivityPage,
    ProjectCreate,
    ProjectDuplicate,
    ProjectDuplicated,
    ProjectFromTemplate,
    ProjectFromTemplateRead,
    ProjectImported,
    ProjectPage,
    ProjectRead,
    ProjectTemplateRead,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DbSession, current_user: VerifiedUser) -> Project:
    """Create a project. The one route that requires a confirmed address (P1.5)."""
    project = Project(name=payload.name, description=payload.description, owner_id=current_user.id)
    db.add(project)
    db.commit()
    return project


def _read(db: DbSession, user: User, project: Project) -> ProjectRead:
    """`ProjectRead` with *this user's* star filled in."""
    read = ProjectRead.model_validate(project)
    read.starred = project.id in projects.starred_ids(db, user, [project.id])
    return read


@router.get("", response_model=ProjectPage)
def list_projects(
    db: DbSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=100, description="Name, description or tag.")] = None,
    tag: Annotated[str | None, Query(max_length=projects.MAX_TAG_LENGTH)] = None,
    starred: Annotated[bool, Query(description="Only the ones you starred.")] = False,
    archived: Annotated[
        Literal["exclude", "include", "only"],
        Query(description="Archived projects are hidden unless asked for."),
    ] = "exclude",
) -> ProjectPage:
    """What *you* made, newest activity first; starred ones are the same list filtered."""
    conditions = [Project.owner_id == current_user.id]
    if archived == "exclude":
        conditions.append(Project.archived_at.is_(None))
    elif archived == "only":
        conditions.append(Project.archived_at.is_not(None))
    if q and q.strip():
        pattern = projects.like_pattern(q.strip())
        conditions.append(
            or_(
                Project.name.ilike(pattern, escape="\\"),
                Project.description.ilike(pattern, escape="\\"),
                cast(Project.tags, String).ilike(pattern, escape="\\"),
            )
        )
    if tag and tag.strip():
        # Tags are normalised to a safe alphabet, so the quoted form identifies exactly one.
        wanted = projects.normalise_tags([tag])[0]
        conditions.append(
            cast(Project.tags, String).like(projects.like_pattern(f'"{wanted}"'), escape="\\")
        )
    if starred:
        conditions.append(
            Project.id.in_(
                select(ProjectStar.project_id).where(ProjectStar.user_id == current_user.id)
            )
        )
    stmt = (
        select(Project)
        .where(*conditions)
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = db.scalar(select(func.count()).select_from(Project).where(*conditions)) or 0
    rows = list(db.scalars(stmt))
    stars = projects.starred_ids(db, current_user, [project.id for project in rows])
    items = []
    for project in rows:
        read = ProjectRead.model_validate(project)
        read.starred = project.id in stars
        items.append(read)
    return ProjectPage(total=total, page=page, page_size=page_size, items=items)


@router.get("/templates", response_model=list[ProjectTemplateRead])
def list_project_templates(current_user: CurrentUser) -> list[ProjectTemplateRead]:
    """The mission-ladder rungs a project can be started from -- the ones that build."""
    return [
        ProjectTemplateRead(
            key=t.key,
            title=t.title,
            era=t.era,
            kind=t.kind,
            hard=t.hard,
            claims=list(t.claims),
            unproven=list(t.unproven),
            seeds_a_design=t.seeds_a_design,
        )
        for t in project_templates.catalogue()
    ]


@router.post(
    "/from-template", response_model=ProjectFromTemplateRead, status_code=status.HTTP_201_CREATED
)
def create_project_from_template(
    payload: ProjectFromTemplate, db: DbSession, current_user: VerifiedUser
) -> ProjectFromTemplateRead:
    """Start a project from a rung: its design (where it is one part), its claims, and what it
    does not prove, in the description."""
    try:
        started = project_templates.start(db, current_user, payload.template, name=payload.name)
    except project_templates.UnknownTemplate as refused:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused.args[0])
        ) from None
    db.commit()
    found = started.template
    return ProjectFromTemplateRead(
        project=_read(db, current_user, started.project),
        template=ProjectTemplateRead(
            key=found.key,
            title=found.title,
            era=found.era,
            kind=found.kind,
            hard=found.hard,
            claims=list(found.claims),
            unproven=list(found.unproven),
            seeds_a_design=found.seeds_a_design,
        ),
        conversation_id=started.conversation.id if started.conversation else None,
        design_note=started.design_note,
    )


@router.post("/import", response_model=ProjectImported, status_code=status.HTTP_201_CREATED)
def import_project(
    db: DbSession,
    current_user: VerifiedUser,
    media: MediaServiceDep,
    file: Annotated[UploadFile, File()],
    name: Annotated[str | None, Form(max_length=255)] = None,
) -> ProjectImported:
    """Import a project archive. Every member is verified against the manifest's hashes
    before anything is written; a damaged or altered archive creates nothing."""
    handle, name_on_disk = tempfile.mkstemp(prefix="kryova-import-", suffix=".zip")
    path = Path(name_on_disk)
    try:
        received = 0
        with os.fdopen(handle, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                received += len(chunk)
                if received > settings.max_media_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"The archive exceeds the {settings.max_media_bytes:,} byte limit.",
                    )
                out.write(chunk)
        try:
            imported = project_archive.import_archive(db, media, current_user, path, name=name)
        except project_archive.ArchiveRefusal as refused:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
            ) from None
        except projects.ProjectRefusal as refused:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
            ) from None
        db.commit()
        return ProjectImported(
            project=_read(db, current_user, imported.project),
            geometry_versions=imported.geometry_versions,
            designs=imported.designs,
            not_restored=list(imported.not_restored),
        )
    finally:
        path.unlink(missing_ok=True)


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(project: ReadableProject, db: DbSession, current_user: CurrentUser) -> ProjectRead:
    """Read is the one verb a viewer has (P2.2). Every other route on a project
    -- here and in geometry, simulations, media and CATIA -- keeps
    `OwnedProject`, which is member-or-better."""
    return _read(db, current_user, project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    payload: ProjectUpdate, project: OwnedProject, db: DbSession, current_user: CurrentUser
) -> ProjectRead:
    """Rename, describe, tag, and archive or restore."""
    changes = payload.model_dump(exclude_unset=True)
    archived = changes.pop("archived", None)
    if "tags" in changes:
        try:
            changes["tags"] = projects.normalise_tags(changes["tags"] or [])
        except projects.ProjectRefusal as refused:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
            ) from None
    for field, value in changes.items():
        setattr(project, field, value)
    if archived is True:
        projects.archive(project)
    elif archived is False:
        projects.restore(project)
    db.commit()
    return _read(db, current_user, project)


@router.put("/{project_id}/star", response_model=ProjectRead)
def star_project(project: ReadableProject, db: DbSession, current_user: CurrentUser) -> ProjectRead:
    """Star it for *you*. A viewer may: it changes nobody else's list and nothing in the project."""
    projects.set_star(db, current_user, project, True)
    db.commit()
    return _read(db, current_user, project)


@router.delete("/{project_id}/star", response_model=ProjectRead)
def unstar_project(
    project: ReadableProject, db: DbSession, current_user: CurrentUser
) -> ProjectRead:
    projects.set_star(db, current_user, project, False)
    db.commit()
    return _read(db, current_user, project)


@router.post(
    "/{project_id}/duplicate", response_model=ProjectDuplicated, status_code=status.HTTP_201_CREATED
)
def duplicate_project(
    payload: ProjectDuplicate, project: OwnedProject, db: DbSession, current_user: VerifiedUser
) -> ProjectDuplicated:
    """Copy the geometry and the designs into a new project in the same organisation.

    The response lists what a copy does not carry. The CATIA document is never copied by
    reference: one document belongs to one conversation on one seat.
    """
    copied = projects.duplicate(db, project, current_user, name=payload.name)
    db.commit()
    return ProjectDuplicated(
        project=_read(db, current_user, copied.project),
        geometry_versions=copied.geometry_versions,
        designs=copied.designs,
        left_out=list(copied.left_out),
    )


@router.get("/{project_id}/activity", response_model=ActivityPage)
def project_activity(
    project: ReadableProject,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=activity.MAX_LIMIT)] = activity.DEFAULT_LIMIT,
    before: Annotated[datetime | None, Query(description="Cursor: the previous page's next_before.")] = None,
) -> ActivityPage:
    """Who did what in this project, newest first, assembled from the records of each kind."""
    entries = activity.feed(db, project, limit=limit, before=before)
    items = [
        ActivityEntryRead(
            at=e.at,
            kind=e.kind,
            summary=e.summary,
            actor_kind=e.actor_kind,
            actor_user_id=e.actor_user_id,
            actor_email=e.actor_email,
            target_type=e.target_type,
            target_id=e.target_id,
        )
        for e in entries
    ]
    return ActivityPage(
        items=items, next_before=items[-1].at if len(items) == limit and items else None
    )


@router.get("/{project_id}/export")
def export_project(
    project: ReadableProject, db: DbSession, media: MediaServiceDep
) -> FileResponse:
    """One zip: geometry, designs with their revision chains, runs with provenance, the
    technical file, and a manifest of hashes. `POST /projects/import` verifies them."""
    handle, name_on_disk = tempfile.mkstemp(prefix="kryova-export-", suffix=".zip")
    os.close(handle)
    path = Path(name_on_disk)
    try:
        project_archive.export(db, media, project, path, now=utcnow())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    safe = project_archive._safe_name(project.name)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{safe}.kryova.zip",
        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: OwnedProject, db: DbSession, media: MediaServiceDep) -> None:
    owned_media = [version.media for version in project.geometry_versions]
    owned_media += [job.fields_media for job in project.simulations if job.fields_media]

    # Geometry rows hold a RESTRICT reference to their media, so the project (and
    # its cascade) has to be gone from the session before the media can follow.
    db.delete(project)
    db.flush()
    for stored in owned_media:
        media.delete(stored)
    db.commit()
