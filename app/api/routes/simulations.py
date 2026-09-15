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
from app.schemas import (
    SimulationCreate,
    SimulationPage,
    SimulationRead,
    SurfaceField,
    SurfaceTemperature,
)
from app.schemas.fatigue import FatigueRead, FatigueRequest
from app.simulation import coupling
from app.simulation.runner import (
    FLOW,
    NOT_STRUCTURAL,
    THERMAL_ANALYSES,
    TRANSIENT,
    backend_for,
    run_simulation,
)

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


def _requested_solver(analysis: str) -> str:
    """What a queued row names before the runner overwrites it with what ran.

    `runner.backend_for`, so the row, the job cache and the runner read one answer
    rather than three copies of it that can drift.
    """
    return backend_for(analysis)


def _bind_temperature_source(
    db: DbSession, project_id: str, geometry: GeometryVersion, payload: SimulationCreate
) -> dict[str, object]:
    """`coupling.bind`, with its refusals spoken as HTTP."""
    assert payload.temperature_from is not None
    try:
        return coupling.bind(
            db,
            project_id,
            geometry,
            payload.temperature_from,
            element_size_mm=payload.element_size_mm,
            element_order=payload.element_order,
        )
    except coupling.SourceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except coupling.CouplingRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _refuse_a_thermal_run(job: SimulationJob) -> None:
    """A structural field route asked about a run that has no structural field.

    Before 2026-09-14 both surface routes read `displacements` straight out of
    the archive, and a conduction run's archive holds temperatures — so asking
    for the surface of any thermal run was a `KeyError` and a 500. A 409 that
    names the route that does serve it is the answer the caller can act on. A
    flow run is refused the same way: its archive holds cell velocities and
    pressures, and its summary is on the run itself.
    """
    if job.analysis == FLOW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is a flow-laminar run: it has a velocity and a pressure in the fluid "
                "and no displacement or stress in the part. Its pressure drop and any heat "
                "carried are in the run's result."
            ),
        )
    if job.analysis in NOT_STRUCTURAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This is a {job.analysis} run: it has a temperature field and no "
                "displacement or stress. Read it from the temperature route instead."
            ),
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
    temperature_source = (
        _bind_temperature_source(db, project.id, geometry, payload)
        if payload.temperature_from is not None
        else None
    )

    job = SimulationJob(
        project_id=project.id,
        geometry_version_id=geometry.id,
        status=JobStatus.QUEUED,
        # What was *asked for*. The runner overwrites it with the solver that
        # actually ran, which is the one a result can be attributed to — and for
        # a conduction run that is a different solver entirely, chosen by
        # `CONDUCTION_BACKEND` rather than by `SOLVER_BACKEND`.
        solver=_requested_solver(payload.analysis),
        load_case=payload.load_case.model_dump() if payload.load_case else None,
        thermal_case=(
            payload.thermal_case.model_dump() if payload.thermal_case else None
        ),
        transient_case=(
            payload.transient_case.model_dump() if payload.transient_case else None
        ),
        flow_case=payload.flow_case.model_dump() if payload.flow_case else None,
        temperature_source=temperature_source,
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
    _refuse_a_thermal_run(job)
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
    _refuse_a_thermal_run(job)
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


@router.get("/{simulation_id}/temperature", response_model=SurfaceTemperature)
def read_surface_temperature(
    project: OwnedProject,
    db: DbSession,
    media: MediaServiceDep,
    simulation_id: str,
    step: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "For a transient run, which stored time sample to read, 0 being the "
                "starting field. Omit for the final one. Refused on a steady run, "
                "which has one field and no time axis."
            ),
        ),
    ] = None,
) -> SurfaceTemperature:
    """The temperature on the part's surface, for either thermal analysis.

    The steady and transient archives both carry the final field as
    `temperatures_k`, so this reads one array whichever ran; `step` reaches into
    a transient run's stored history. The range reported beside the surface is
    over **every node** of that sample, interior included — a part hotter inside
    than on its skin is exactly the case a surface-only maximum would hide.
    """
    job = _get_job(db, project.id, simulation_id)
    if job.status is not JobStatus.SUCCEEDED or job.fields_media is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Simulation is {job.status.value}; results are not available",
        )
    if job.analysis == FLOW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is a flow-laminar run. Any temperature it carries is the fluid's, on "
                "OpenFOAM's cells rather than on the part's surface, so this route does not "
                "serve it; the outlet and wall temperatures are in the run's result."
            ),
        )
    if job.analysis not in THERMAL_ANALYSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This is a {job.analysis} run and has no temperature field. Read its "
                "displacement and stress from the surface route."
            ),
        )
    if step is not None and job.analysis != TRANSIENT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "step applies only to a thermal-transient run. A steady conduction run "
                "has one temperature field and no time axis; omit step."
            ),
        )
    try:
        handle = media.open(job.fields_media)
    except MediaNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="Result fields are no longer available"
        ) from exc

    with handle as fh, np.load(fh) as data:
        time_s: float | None = None
        step_count: int | None = None
        if job.analysis == TRANSIENT:
            history = data["temperature_history_k"]
            times = data["times_s"]
            step_count = len(times) - 1
            index = step_count if step is None else step
            if index > step_count:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"This run stored {step_count + 1} time samples, numbered 0 to "
                        f"{step_count}; there is no sample {index}."
                    ),
                )
            field = history[index]
            time_s = float(times[index])
        else:
            field = data["temperatures_k"]
        triangles = data["surface_triangles"]
        used, renumbered = np.unique(triangles, return_inverse=True)
        return SurfaceTemperature(
            node_positions=data["nodes"][used].tolist(),
            triangles=renumbered.reshape(triangles.shape).tolist(),
            temperatures_k=field[used].tolist(),
            min_temperature_k=float(field.min()),
            max_temperature_k=float(field.max()),
            time_s=time_s,
            step=None if time_s is None else index,
            step_count=step_count,
        )


@router.post("/{simulation_id}/fatigue", response_model=FatigueRead)
def assess_simulation_fatigue(
    project: OwnedProject,
    db: DbSession,
    media: MediaServiceDep,
    simulation_id: str,
    body: FatigueRequest,
) -> FatigueRead:
    """A fatigue check at one node of a finished structural run (E8.6).

    The solved load, scaled by `signal`, read as a signed history at the node, and
    assessed against `curve` with the stated factors. A POST because the request
    carries a whole assessment, but it writes nothing: the answer is computed from
    the archive on every call. `app/simulation/fatigue.py` holds the refusals, and
    the agent's `assess_fatigue` tool calls the same function.
    """
    from app.simulation.fatigue import FatigueRefused, assess_run, refuse_unless_assessable

    job = _get_job(db, project.id, simulation_id)
    try:
        refuse_unless_assessable(job.analysis, job.status.value)
        if job.fields_media is None:
            raise FatigueRefused("This run stored no result fields.", status=409)
        try:
            handle = media.open(job.fields_media)
        except MediaNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Result fields are no longer available"
            ) from exc
        with handle as fh, np.load(fh) as data:
            return assess_run(
                data,
                body,
                simulation_id=job.id,
                solver=job.solver or "",
                result=job.result,
            )
    except FatigueRefused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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
