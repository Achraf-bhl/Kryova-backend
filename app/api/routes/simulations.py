import struct
from datetime import date, timedelta
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import (
    AuditDep,
    CurrentUser,
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    OwnedProject,
    PrincipalDep,
    SessionScopeDep,
)
from app.api.rate_limit import RateLimit
from app.core import interruption
from app.core.config import settings
from app.core.metering import check_quota
from app.media import MediaNotFound
from app.models import GeometryVersion, JobStatus, Meter, Project, SimulationJob
from app.models.audit import AuditAction, AuditOutcome
from app.schemas import SimulationCreate, SimulationPage, SimulationRead, SurfaceField
from app.simulation.runner import run_simulation

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


def _assert_within_quota(db: DbSession, owner_id: str) -> None:
    """Refuse a run when the user already holds their share of the workers.

    Meshing and solving are the most expensive thing this service does, and the
    queue is shared, so without a per-user ceiling one account can occupy every
    worker and every other user's job waits behind it. The agent tool applies
    the same rule before it proposes a run (`app/ai/tools.py`); this is the one
    that actually binds, because the HTTP route is reachable without it.
    """
    limit = settings.max_concurrent_simulations_per_user
    running = (
        db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .join(Project, Project.id == SimulationJob.project_id)
            .where(
                Project.owner_id == owner_id,
                SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        or 0
    )
    if running >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"You already have {running} simulation(s) queued or running, which is the "
                f"limit of {limit}. Wait for one to finish, or delete a queued run, "
                "before starting another."
            ),
        )


def _assert_within_allowance(db: DbSession, organisation_id: str) -> None:
    """Refuse a run the tenant has no allowance left for (P8.3).

    **Never a bare 429.** The plan's words are "the honest envelope — what ran
    out, what it costs to continue, what remains free", and
    `QuotaDecision.detail()` is that envelope: the meter, what has been used,
    what the allowance was, what remains, the credit balance and the plan. A
    refusal that says only "quota exceeded" leaves somebody with a machine to
    design and no idea what to do next, which is how a limit becomes a support
    ticket instead of a purchase.

    Checked against `SOLVER_SECONDS` alone. A simulation moves that meter and
    `MESH_ELEMENT_SECONDS`, but refusing on the second would mean a tenant with
    solver time left is stopped by a mesh number nobody prices, and one limit
    per action is what a person can act on.

    **A deployment with no allowances set is unaffected**, because `check_quota`
    answers "allowed" with a reason when nothing is set rather than denying what
    it has no policy for — which is what stops a quota system from stopping the
    product the day it is switched on.
    """
    today = date.today()
    period_start = today.replace(day=1)
    decision = check_quota(
        db, organisation_id, Meter.SOLVER_SECONDS, period_start, today + timedelta(days=1)
    )
    if decision.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        # 402, not 429: this is not "too fast", it is "there is none left", and
        # the two need different responses from the person reading them. A 429
        # invites a retry, which here would be a retry that can only fail.
        detail=decision.detail(),
    )


#: Per-principal, like the chat limit (P1.6). Distinct from
#: `max_concurrent_simulations_per_user`, which caps how many run at once: this
#: caps how fast they can be *asked for*, which is what a client stuck in a
#: retry loop does to a queue.
_simulation_rate_limit = RateLimit(
    "simulations.create", settings.simulation_requests_per_minute, window_seconds=60
)


@router.post(
    "",
    response_model=SimulationRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_simulation_rate_limit)],
)
def create_simulation(
    payload: SimulationCreate,
    project: OwnedProject,
    db: DbSession,
    store: MediaStoreDep,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
) -> SimulationJob:
    """Queue a mesh-and-solve run. Returns immediately with a job to poll."""
    _assert_within_quota(db, project.owner_id)
    _assert_within_allowance(db, project.organisation_id)
    geometry = _resolve_geometry(db, project.id, payload.geometry_version)

    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=JobStatus.QUEUED,
        # What was *asked for*. The runner overwrites it with the solver that
        # actually ran, which is the one a result can be attributed to — and for
        # a conduction run that is a different solver entirely, chosen by
        # `CONDUCTION_BACKEND` rather than by `SOLVER_BACKEND`.
        solver=(
            settings.conduction_backend
            if payload.analysis == "thermal-conduction"
            else settings.solver_backend
        ),
        load_case=payload.load_case.model_dump() if payload.load_case else None,
        thermal_case=(
            payload.thermal_case.model_dump() if payload.thermal_case else None
        ),
        element_size_mm=payload.element_size_mm,
        element_order=payload.element_order,
        grids=payload.grids,
        analysis=payload.analysis,
        thickness_mm=payload.thickness_mm,
    )
    db.add(job)
    db.commit()

    # Commit first: the worker looks the job up by id in its own session.
    queue.submit(lambda: run_simulation(job.id, session_scope, store))
    db.refresh(job)
    return job


@router.get("", response_model=SimulationPage)
def list_simulations(
    project: OwnedProject,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SimulationPage:
    stmt = (
        select(SimulationJob)
        .where(SimulationJob.project_id == project.id)
        .order_by(SimulationJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .where(SimulationJob.project_id == project.id)
        )
        or 0
    )
    return SimulationPage(total=total, page=page, page_size=page_size, items=list(db.scalars(stmt)))


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
            max_displacement_mm=float(np.linalg.norm(data["displacements"], axis=1).max()),
        )


@router.get("/{simulation_id}/surface/binary")
def read_surface_field_binary(
    project: OwnedProject, db: DbSession, media: MediaServiceDep, simulation_id: str
) -> Response:
    """High-performance packed binary surface field stream for 3D viewers."""
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
        used, renumbered = np.unique(triangles, return_inverse=True)

        nodes = np.ascontiguousarray(data["nodes"][used], dtype=np.float32)
        tri_indices = np.ascontiguousarray(renumbered.reshape(triangles.shape), dtype=np.uint32)
        displacements = np.ascontiguousarray(data["displacements"][used], dtype=np.float32)
        von_mises = np.ascontiguousarray(data["von_mises_nodal"][used], dtype=np.float32)

        num_nodes = len(nodes)
        num_triangles = len(tri_indices)
        max_von_mises = float(data["von_mises_element"].max())
        max_disp = float(np.linalg.norm(data["displacements"], axis=1).max())

        header = struct.pack(
            "<4sIIIff8s",
            b"KRYO",
            1,  # Format version 1
            num_nodes,
            num_triangles,
            max_von_mises,
            max_disp,
            b"\x00" * 8,
        )

        body = (
            header
            + nodes.tobytes()
            + tri_indices.tobytes()
            + displacements.tobytes()
            + von_mises.tobytes()
        )

        return Response(
            content=body,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="surface_{simulation_id}.bin"'},
        )


@router.post("/{simulation_id}/cancel", response_model=SimulationRead)
def cancel_simulation(
    project: OwnedProject,
    db: DbSession,
    current_user: CurrentUser,
    audit: AuditDep,
    principal: PrincipalDep,
    simulation_id: str,
) -> SimulationJob:
    """Stop a run (P5.6). What that means depends on where the run is.

    **Queued** — cancelled outright. Nothing started, so nothing unwinds.

    **Running** — the request is recorded and the runner honours it at its next
    stage boundary: after meshing and before solving, and between the grids of
    a study. Meshing and solving are each a single call into gmsh or CalculiX
    that the API cannot reach into, so a solve already handed to CalculiX
    finishes, **and the machine time it used is still billed**. The response
    says so; a `202` that let the caller believe the work stopped would be a
    small lie, and it is the kind a billing dispute is built out of.

    The status therefore comes back as it *is*, `cancelled` or still `running`,
    rather than as what was asked for.
    """
    job = _get_job(db, project.id, simulation_id)
    refusal = interruption.request_simulation_stop(db, job, by=current_user)
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=refusal.reason)

    audit.record(
        AuditAction.RUN_CANCELLED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="simulation_job",
        target_id=job.id,
        detail={"status": job.status.value},
    )
    db.commit()
    return job


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
