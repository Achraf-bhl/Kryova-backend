import numpy as np
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import (
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    OwnedProject,
    SessionScopeDep,
)
from app.media import MediaNotFound
from app.models import GeometryVersion, JobStatus, SimulationJob
from app.schemas import SimulationCreate, SimulationRead, SurfaceField
from app.simulation.runner import run_simulation
from app.solve.linear_static import LinearStaticSolver

router = APIRouter(prefix="/projects/{project_id}/simulations", tags=["simulations"])


def _resolve_geometry(db: DbSession, project_id: str, version: int | None) -> GeometryVersion:
    stmt = select(GeometryVersion).where(GeometryVersion.project_id == project_id)
    if version is None:
        stmt = stmt.order_by(GeometryVersion.version_number.desc())
    else:
        stmt = stmt.where(GeometryVersion.version_number == version)

    geometry = db.scalars(stmt).first()
    if geometry is None:
        detail = (
            "This project has no geometry yet; upload a CAD file first"
            if version is None
            else f"Geometry version {version} not found"
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return geometry


@router.post("", response_model=SimulationRead, status_code=status.HTTP_202_ACCEPTED)
def create_simulation(
    payload: SimulationCreate,
    project: OwnedProject,
    db: DbSession,
    store: MediaStoreDep,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
) -> SimulationJob:
    """Queue a mesh-and-solve run. Returns immediately with a job to poll."""
    geometry = _resolve_geometry(db, project.id, payload.geometry_version)

    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=JobStatus.QUEUED,
        solver=LinearStaticSolver.name,
        load_case=payload.load_case.model_dump(),
        element_size_mm=payload.element_size_mm,
    )
    db.add(job)
    db.commit()

    # Commit first: the worker looks the job up by id in its own session.
    queue.submit(lambda: run_simulation(job.id, session_scope, store))
    db.refresh(job)
    return job


@router.get("", response_model=list[SimulationRead])
def list_simulations(project: OwnedProject, db: DbSession) -> list[SimulationJob]:
    stmt = (
        select(SimulationJob)
        .where(SimulationJob.project_id == project.id)
        .order_by(SimulationJob.created_at.desc())
    )
    return list(db.scalars(stmt))


def _get_job(db: DbSession, project_id: str, simulation_id: str) -> SimulationJob:
    job = db.get(SimulationJob, simulation_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Simulation not found")
    return job


@router.get("/{simulation_id}", response_model=SimulationRead)
def read_simulation(project: OwnedProject, db: DbSession, simulation_id: str) -> SimulationJob:
    return _get_job(db, project.id, simulation_id)


@router.get("/{simulation_id}/surface", response_model=SurfaceField)
def read_surface_field(
    project: OwnedProject, db: DbSession, media: MediaServiceDep, simulation_id: str
) -> SurfaceField:
    """The result surface, ready to hand to a 3D viewer."""
    job = _get_job(db, project.id, simulation_id)
    if job.status is not JobStatus.SUCCEEDED or job.fields_media is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation is {job.status.value}; results are not available",
        )
    try:
        handle = media.open(job.fields_media)
    except MediaNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Result fields are no longer available"
        ) from exc

    with handle as fh, np.load(fh) as data:
        triangles = data["surface_triangles"]
        # Renumber to just the boundary nodes so the payload carries no interior.
        used, renumbered = np.unique(triangles, return_inverse=True)
        return SurfaceField(
            node_positions=data["nodes"][used].tolist(),
            triangles=renumbered.reshape(triangles.shape).tolist(),
            displacements=data["displacements"][used].tolist(),
            von_mises_mpa=data["von_mises_nodal"][used].tolist(),
            max_von_mises_mpa=float(data["von_mises_element"].max()),
            max_displacement_mm=float(
                np.linalg.norm(data["displacements"], axis=1).max()
            ),
        )


@router.delete("/{simulation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_simulation(
    project: OwnedProject, db: DbSession, media: MediaServiceDep, simulation_id: str
) -> None:
    job = _get_job(db, project.id, simulation_id)
    if not job.status.is_terminal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation is {job.status.value}; wait for it to finish",
        )
    fields = job.fields_media
    db.delete(job)
    db.flush()
    if fields is not None:
        media.delete(fields)
    db.commit()
