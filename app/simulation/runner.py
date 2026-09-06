"""The geometry -> mesh -> solve pipeline, as run by a background job.

Everything here happens off the request thread and owns its own database
session. Failures are recorded on the job row rather than raised, because there
is no caller left to raise to.

**A job is also the unit that gets billed** (Phase P8). `usage_scope` puts the
tenant and the job on a context variable for the length of the run, and every
`app.observe` span that finishes inside it — the gmsh critical section, the
in-house solver, the `ccx` subprocess — is metered from the timing that was
already being taken. Nothing here times anything twice, and nothing here can
fail because of metering: `app.core.metering` absorbs its own errors, counts
them and writes them down. The scope posts its batch in a `finally`, so a solve
that raised after nine minutes is still billed for the nine minutes.
"""

import logging
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.metering import Cause, LedgerSink, UsageScope, record_storage, usage_scope
from app.media import LocalMediaStore, MediaService
from app.mesh.gmsh_mesher import generate_tet_mesh
from app.mesh.types import MeshError, TetMesh
from app.models import JobStatus, MediaKind, SimulationJob
from app.simulation.limits import check_mesh_request
from app.solve.base import Solver
from app.solve.postprocess import nodal_average
from app.solve.registry import build_solver, solver_version
from app.solve.types import LoadCase, SolverError

logger = logging.getLogger(__name__)

SessionScope = Callable[[], AbstractContextManager[Session]]


def run_simulation(
    job_id: str,
    session_scope: SessionScope,
    store: LocalMediaStore,
    solver: Solver | None = None,
) -> None:
    """Execute one simulation job to completion, recording the outcome."""
    # Through the registry rather than by construction, so `SOLVER_BACKEND` can
    # reach CalculiX at all. An injected solver still wins: the tests pass one,
    # and so does anything that has already made the choice itself.
    solver = solver or build_solver(
        settings.solver_backend, executable=settings.calculix_path or None
    )

    with session_scope() as db:
        media = MediaService(db, store)
        job = db.get(SimulationJob, job_id)
        if job is None:
            logger.error("Simulation job %s vanished before it could run", job_id)
            return
        if job.status is not JobStatus.QUEUED:
            logger.warning("Job %s is %s, not queued; skipping", job_id, job.status.value)
            return

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        # `LedgerSink(session_scope)`, not the session above: a metering write
        # inside this transaction could roll back the result it was measuring,
        # which is the one thing P8 says metering must never do.
        with usage_scope(
            _usage_cause(job), LedgerSink(session_scope), fault_scope=session_scope
        ) as usage:
            try:
                mesh, mesh_stats, output = _execute(job, media, solver, usage)
            except (MeshError, SolverError, ValueError) as exc:
                # Expected, explainable failures: a bad mesh or an ill-posed model.
                _fail(db, job, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - a crashed job must still be recorded
                logger.exception("Simulation job %s crashed", job_id)
                _fail(db, job, f"Unexpected solver failure: {exc}")
                return

            # Recorded from the solver that *ran*, not from the name the route wrote
            # when the job was queued. Decision 3 binds a result to what produced it,
            # and a row naming a solver nobody consulted is provenance in name only.
            # The version is `None` when it could not be read, and stored as None:
            # an unmeasured version must not be guessed at.
            job.solver = solver.name
            version = solver_version(solver.name, settings.calculix_path or None)
            if version:
                job.solver_version = version
            usage.annotate(solver=solver.name, solver_version=version or "unavailable")

            fields = _store_fields(media, job, mesh, output)
            record_storage(
                usage,
                size_bytes=fields.size_bytes,
                media_id=fields.id,
                sha256=fields.sha256,
                deduplicated=bool((fields.meta or {}).get("deduplicated")),
            )
            job.fields_media_id = fields.id
            job.mesh_stats = mesh_stats
            job.result = output.result.model_dump()
            job.status = JobStatus.SUCCEEDED
            job.finished_at = datetime.now(timezone.utc)
            db.commit()


def _usage_cause(job: SimulationJob) -> Cause:
    """Bind this job's usage to everything a person could open to check it.

    The tenant comes from the project, which is the only place it lives (P2):
    `SimulationJob` has no organisation of its own, and denormalising one here
    would be a second answer to a question `Project.organisation_id` already
    answers.
    """
    project = job.project
    return Cause(
        organisation_id=project.organisation_id,
        source="simulation.runner",
        subject_type="simulation_job",
        subject_id=job.id,
        project_id=job.project_id,
        simulation_job_id=job.id,
        geometry_version_id=job.geometry_version_id,
        user_id=project.owner_id,
        detail={
            "element_order": job.element_order,
            "element_size_mm": job.element_size_mm,
        },
    )


def _fail(db: Session, job: SimulationJob, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _execute(job: SimulationJob, media: MediaService, solver: Solver, usage: UsageScope):
    version = job.geometry_version
    case = LoadCase.model_validate(job.load_case)

    # Before gmsh, not after: the post-mesh check below only fires once the
    # machine has already paid for the mesh, and a small enough element size
    # makes that bill unbounded.
    check_mesh_request(version.stats, job.element_size_mm)

    # Blobs live on this machine, so gmsh can read the file in place -- no
    # staging copy, however large the part is.
    path = media.local_path(version.media)
    mesh, mesh_stats = generate_tet_mesh(
        path, version.file_format, job.element_size_mm, element_order=job.element_order
    )
    # Annotated here rather than after the solve, so a run that fails *in* the
    # solver is still billed for the meshing it really did. The gmsh span
    # declares no fields (`app.observe.catalogue`), so without this the
    # element-seconds meter has no multiplier and reports a gap instead of a
    # number — which is the honest outcome, and not the one we want.
    usage.annotate(elements=mesh.tet_count, nodes=mesh.node_count)

    if mesh.tet_count > settings.max_elements:
        raise MeshError(
            f"The mesh has {mesh.tet_count:,} elements, over the {settings.max_elements:,} "
            "limit. Increase element_size_mm to coarsen it."
        )

    return mesh, mesh_stats, solver.solve(mesh, case)


def _store_fields(media: MediaService, job: SimulationJob, mesh: TetMesh, output):
    """Persist the full result fields alongside the surface the viewer draws.

    These are tens of megabytes for a real part, so they go to the local media
    store; only the summary goes to the cloud database.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "fields.npz"
        np.savez_compressed(
            path,
            nodes=mesh.nodes,
            tets=mesh.tets,
            surface_triangles=mesh.surface_triangles,
            displacements=output.displacements,
            von_mises_element=output.von_mises,
            von_mises_nodal=nodal_average(mesh, output.von_mises),
        )
        return media.store_path(
            owner_id=job.project.owner_id,
            kind=MediaKind.RESULT_FIELDS,
            path=path,
            filename=f"{job.id}-fields.npz",
            content_type="application/x-npz",
            meta={"simulation_id": job.id, "project_id": job.project_id},
        )
