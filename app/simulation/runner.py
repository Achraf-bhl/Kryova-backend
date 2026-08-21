"""The geometry -> mesh -> solve pipeline, as run by a background job.

Everything here happens off the request thread and owns its own database
session. Failures are recorded on the job row rather than raised, because there
is no caller left to raise to.
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
from app.media import LocalMediaStore, MediaService
from app.mesh.gmsh_mesher import generate_tet_mesh
from app.mesh.types import MeshError, TetMesh
from app.models import JobStatus, MediaKind, SimulationJob
from app.solve.base import Solver
from app.solve.linear_static import LinearStaticSolver
from app.solve.postprocess import nodal_average
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
    solver = solver or LinearStaticSolver()

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

        try:
            mesh, mesh_stats, output = _execute(job, media, solver)
        except (MeshError, SolverError, ValueError) as exc:
            # Expected, explainable failures: a bad mesh or an ill-posed model.
            _fail(db, job, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a crashed job must still be recorded
            logger.exception("Simulation job %s crashed", job_id)
            _fail(db, job, f"Unexpected solver failure: {exc}")
            return

        fields = _store_fields(media, job, mesh, output)
        job.fields_media_id = fields.id
        job.mesh_stats = mesh_stats
        job.result = output.result.model_dump()
        job.status = JobStatus.SUCCEEDED
        job.finished_at = datetime.now(timezone.utc)
        db.commit()


def _fail(db: Session, job: SimulationJob, error: str) -> None:
    job.status = JobStatus.FAILED
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _execute(job: SimulationJob, media: MediaService, solver: Solver):
    version = job.geometry_version
    case = LoadCase.model_validate(job.load_case)

    # Blobs live on this machine, so gmsh can read the file in place -- no
    # staging copy, however large the part is.
    path = media.local_path(version.media)
    mesh, mesh_stats = generate_tet_mesh(path, version.file_format, job.element_size_mm)

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
