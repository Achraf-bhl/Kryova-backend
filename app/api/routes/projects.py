from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, MediaServiceDep, OwnedProject
from app.models import Project
from app.schemas import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DbSession, current_user: CurrentUser) -> Project:
    project = Project(
        name=payload.name, description=payload.description, owner_id=current_user.id
    )
    db.add(project)
    db.commit()
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(db: DbSession, current_user: CurrentUser) -> list[Project]:
    stmt = (
        select(Project)
        .where(Project.owner_id == current_user.id)
        .order_by(Project.updated_at.desc())
    )
    return list(db.scalars(stmt))


@router.get("/{project_id}", response_model=ProjectRead)
def read_project(project: OwnedProject) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(payload: ProjectUpdate, project: OwnedProject, db: DbSession) -> Project:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    return project


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
