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
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.metering import Cause, LedgerSink, UsageScope, record_storage, usage_scope
from app.media import LocalMediaStore, MediaService
from app.mesh.gmsh_mesher import (
    _DEFAULT_ELEMENTS_ALONG_DIAGONAL,
    generate_tet_mesh,
    generate_tri_mesh,
)
from app.mesh.planar import TriMesh
from app.mesh.types import MeshError, TetMesh
from app.models import JobStatus, MediaKind, SimulationJob
from app.simulation.limits import check_mesh_request
from app.solve.base import SolveOutput, Solver
from app.solve.plane import PlaneCase, PlaneSolver, PlaneState
from app.solve.postprocess import nodal_average
from app.solve.registry import build_solver, solver_version
from app.solve.types import LoadCase, MeshConvergence, SolverError

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
                mesh, mesh_stats, output, ran = _execute(job, media, solver, usage)
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
            job.solver = ran
            version = solver_version(ran, settings.calculix_path or None)
            if version:
                job.solver_version = version
            usage.annotate(solver=ran, solver_version=version or "unavailable")

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


#: The idealisations a job may name, and the plane state each maps onto.
#: `solid` is absent on purpose: it is not a plane state, and a dict lookup that
#: quietly returned one for it would be how a solid gets solved as a membrane.
PLANE_STATES: dict[str, PlaneState] = {
    "plane-stress": PlaneState.STRESS,
    "plane-strain": PlaneState.STRAIN,
}

#: The analysis that solves for a temperature field rather than a displacement.
#: Named rather than written as a literal in three places, and kept out of
#: `PLANE_STATES` because it is not an idealisation of a structural problem — it
#: is a different equation with a different case, a different solver ABC and a
#: different result type.
CONDUCTION: str = "thermal-conduction"

#: Every value `SimulationJob.analysis` may hold.
ANALYSES: tuple[str, ...] = ("solid", *PLANE_STATES, CONDUCTION)


def _execute(job: SimulationJob, media: MediaService, solver: Solver, usage: UsageScope):
    version = job.geometry_version

    # Before gmsh, not after: the post-mesh check below only fires once the
    # machine has already paid for the mesh, and a small enough element size
    # makes that bill unbounded.
    check_mesh_request(version.stats, job.element_size_mm)

    # Blobs live on this machine, so gmsh can read the file in place -- no
    # staging copy, however large the part is.
    path = media.local_path(version.media)

    if job.analysis == CONDUCTION:
        return _execute_conduction(job, path, version.file_format, usage)

    if job.load_case is None:
        raise ValueError(
            f"This {job.analysis} job has no load case, so there is nothing to solve. "
            "Only a thermal-conduction run is written without one."
        )
    case = LoadCase.model_validate(job.load_case)

    if job.analysis in PLANE_STATES:
        return _execute_plane(job, path, version.file_format, case, usage)

    if job.grids > 1:
        return _execute_study(job, path, version.file_format, case, solver, usage)

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

    return mesh, mesh_stats, solver.solve(mesh, case), solver.name


#: Each successive grid's target element size, as a fraction of the one before.
#: A ratio, not a subtraction, so the sequence means the same thing on a 5 mm
#: bracket and a 5 m frame. 1.4 is the spacing the NAFEMS studies in
#: `app/verify/nafems.py` settled on: wide enough that the discretisation trend
#: dominates gmsh's remeshing noise — which it does not at 1.2, measured — and
#: narrow enough that three grids stay affordable, since the finest costs about
#: `REFINEMENT_RATIO ** (3 * (grids - 1))` times the coarsest.
REFINEMENT_RATIO: float = 1.4


def _study_sizes(coarsest_mm: float, grids: int) -> list[float]:
    """The element sizes for a study, coarsest first.

    The *requested* size is treated as the **coarsest** grid rather than the
    finest, so asking for a study can only ever cost more time than the single
    run would have — never more memory than the caller has already shown it can
    afford. Refining below a size somebody chose would be the surprising
    direction: it is the one that runs out of RAM.
    """
    return [coarsest_mm * REFINEMENT_RATIO ** (grids - 1 - level) for level in range(grids)]


def _execute_study(
    job: SimulationJob,
    path: Path,
    file_format: str,
    case: LoadCase,
    solver: Solver,
    usage: UsageScope,
):
    """Solve the same case on successively finer grids and assess the result.

    **This is what makes a converged answer something a caller can ask for.**
    The machinery has existed in `app/verify/convergence.py` since E7 task 2 and
    nothing in the request path called it, so every stress this product had ever
    reported came from one mesh — measured at gate G1, where a factor of safety
    of 1303 was reported off a single 411-element tet4 mesh. The result now
    carries what the study found, and when a study cannot be formed it says so
    rather than quietly reporting the finest grid as though it were settled.

    The **finest** grid's output is what is stored and drawn: it is the best
    answer computed, and the study's verdict travels beside it rather than
    replacing it. A caller that asked for three grids and got a picture of the
    coarsest would rightly not believe any of it.
    """
    from app.verify.convergence import run_study
    from app.verify.quantities import MAX_VON_MISES

    sizes = _study_sizes(job.element_size_mm or _automatic_size(job), job.grids)
    check_mesh_request(job.geometry_version.stats, min(sizes))

    solved: dict[float, tuple[TetMesh, dict[str, Any], SolveOutput]] = {}

    def sample(element_size_mm: float) -> tuple[TetMesh, float]:
        mesh, stats = generate_tet_mesh(
            path, file_format, element_size_mm, element_order=job.element_order
        )
        usage.annotate(elements=mesh.tet_count, nodes=mesh.node_count)
        if mesh.tet_count > settings.max_elements:
            raise MeshError(
                f"Grid at {element_size_mm:g} mm has {mesh.tet_count:,} elements, over "
                f"the {settings.max_elements:,} limit. A study refines from the size you "
                "gave, so raise element_size_mm or ask for fewer grids."
            )
        output = solver.solve(mesh, case)
        solved[element_size_mm] = (mesh, stats, output)
        return mesh, MAX_VON_MISES.read(mesh, output)

    study = run_study(MAX_VON_MISES.name, MAX_VON_MISES.unit, sizes, sample)

    if not solved:
        raise MeshError("No grid in the study could be meshed and solved. " + study.report())

    mesh, mesh_stats, output = solved[min(solved)]
    output.result.mesh_convergence = MeshConvergence.from_study(study)
    mesh_stats = dict(mesh_stats) | {"study": study.to_dict()}
    return mesh, mesh_stats, output, solver.name


def _execute_conduction(
    job: SimulationJob,
    path: Path,
    file_format: str,
    usage: UsageScope,
) -> tuple[TetMesh, dict, object, str]:
    """A steady-state conduction run: temperatures out, no displacement anywhere.

    **The last of E7 task 6's three pieces.** The physics landed on 2026-09-09 and
    the seam the same day — `ConductionSolver` beside `Solver`, `ModalSolver` and
    `PlanarSolver`, with its own registry table — and neither made it reachable
    from a request: the solver could be selected by configuration and never
    *asked for*. This is that call, and it is the third time in four days this
    codebase has found a capability built and never connected.

    Two things it does not share with the structural path, both deliberate. The
    solver comes from `build_conduction_solver` and `CONDUCTION_BACKEND` rather
    than from the injected `Solver`: `SOLVER_BACKEND` names a solver for a
    different analysis, and reading it here is how `calculix` would come to
    select the in-house conduction solver by accident. And there is no
    convergence study: `run_study` assesses one scalar over successive grids and
    the quantity it is pointed at (`MAX_VON_MISES`) does not exist in a
    temperature field, so a `grids > 1` conduction job is refused by name rather
    than silently solved once — a study whose quantity was invented for it would
    be a number nobody asked for.
    """
    from app.solve.conduction import ThermalCase
    from app.solve.registry import build_conduction_solver

    if job.thermal_case is None:
        raise ValueError(
            "A thermal-conduction job needs a thermal case — a conductivity and at "
            "least one boundary condition. This one has none, so there is nothing to "
            "solve."
        )
    if job.grids > 1:
        raise ValueError(
            "A convergence study is not available for a thermal-conduction run yet: "
            "the study assesses the peak von Mises stress, which a temperature field "
            "does not have. Ask for one grid, or run the structural analysis whose "
            "convergence you want measured."
        )

    case = ThermalCase.model_validate(job.thermal_case)
    mesh, mesh_stats = generate_tet_mesh(
        path, file_format, job.element_size_mm, element_order=job.element_order
    )
    usage.annotate(elements=mesh.tet_count, nodes=mesh.node_count)
    if mesh.tet_count > settings.max_elements:
        raise MeshError(
            f"The mesh has {mesh.tet_count:,} elements, over the {settings.max_elements:,} "
            "limit. Increase element_size_mm to coarsen it."
        )

    conduction = build_conduction_solver(settings.conduction_backend)
    return mesh, mesh_stats, conduction.solve(mesh, case), conduction.name


def _automatic_size(job: SimulationJob) -> float:
    """The size a study starts from when the caller named none.

    `generate_tet_mesh` picks one from the bounding box when it is given None,
    but a study needs the number *before* meshing so it can space the grids, and
    two independent guesses would drift apart. So the mesher's own constant is
    imported rather than copied: if it is renamed this fails loudly at import,
    which is the intended failure — a silently diverging copy is not.
    """
    box = ((job.geometry_version.stats or {}).get("bounding_box") or {}).get("size")
    if not box:
        raise MeshError(
            "This geometry has no bounding box, so a convergence study cannot choose "
            "its grid sizes. Give element_size_mm explicitly."
        )
    diagonal = float(sum(float(value) ** 2 for value in box) ** 0.5)
    return diagonal / _DEFAULT_ELEMENTS_ALONG_DIAGONAL


def _execute_plane(
    job: SimulationJob,
    path: Path,
    file_format: str,
    case: LoadCase,
    usage: UsageScope,
) -> tuple[TriMesh, dict, object, str]:
    """A plane-stress or plane-strain run, on a triangular mesh of a planar face.

    A separate branch rather than a polymorphic solver, and that is the seam
    doing its job: `PlanarSolver` takes a different mesh and a different case
    from `Solver`, so the *only* thing that can decide between them is whoever
    knows which the job asked for. Folding them together would put a branch on
    a union type into every caller instead of this one.

    `SOLVER_BACKEND` is deliberately not consulted here. It selects between the
    in-house solid solver and CalculiX, and neither of those is what runs a
    plane model; picking a plane solver by a setting that means something else
    is how a run ends up reporting a solver that never saw it.
    """
    thickness = job.thickness_mm
    if thickness is None or thickness <= 0.0:
        raise SolverError(
            f"A {job.analysis} run needs an out-of-plane thickness and this job has "
            f"{thickness!r}. Every stress in a plane model scales with it, so it is "
            "asked for rather than assumed — resubmit with thickness_mm."
        )

    mesh, mesh_stats = generate_tri_mesh(
        path, file_format, job.element_size_mm, element_order=job.element_order
    )
    usage.annotate(elements=mesh.element_count, nodes=mesh.node_count)

    if mesh.element_count > settings.max_elements:
        raise MeshError(
            f"The mesh has {mesh.element_count:,} elements, over the "
            f"{settings.max_elements:,} limit. Increase element_size_mm to coarsen it."
        )

    plane_case = PlaneCase(
        name=case.name,
        material=case.material,
        thickness_mm=thickness,
        state=PLANE_STATES[job.analysis],
        fixtures=case.fixtures,
        loads=case.loads,
        delta_t_k=case.delta_t_k,
    )
    plane_solver = PlaneSolver()
    return mesh, mesh_stats, plane_solver.solve(mesh, plane_case), plane_solver.name


def _nodal_average_over(node_count: int, corners, element_values):
    """Element values averaged onto the nodes of any corner connectivity.

    The 2-D counterpart of `postprocess.nodal_average`, which is typed on a
    `TetMesh` and reads `mesh.tets`. Written here rather than by widening that
    function, because the peak-versus-smoothed distinction it documents is a
    solid-mesh argument that deserves to be made once in its own place; this is
    the display field and nothing reads a factor of safety off it.
    """
    values = np.asarray(element_values, dtype=float)
    totals = np.zeros(node_count, dtype=float)
    counts = np.zeros(node_count, dtype=np.int64)
    idx = np.asarray(corners)
    np.add.at(totals, idx, values[:, None])
    np.add.at(counts, idx, 1)
    return np.divide(totals, np.maximum(counts, 1))


def _store_fields(
    media: MediaService, job: SimulationJob, mesh: TetMesh | TriMesh, output
):
    """Persist the full result fields alongside the surface the viewer draws.

    These are tens of megabytes for a real part, so they go to the local media
    store; only the summary goes to the cloud database.

    A plane mesh needs no boundary extraction: its triangles *are* the surface,
    and every node is on it. The keys stay the same either way, so the viewer
    and the surface-field route read one shape rather than branching on a
    dimension they have no other reason to know about.
    """
    if isinstance(mesh, TriMesh):
        cells = mesh.tris
        surface = mesh.tris
    else:
        cells = mesh.tets
        surface = mesh.surface_triangles

    # A conduction run has no displacement and no stress, and writing zeros for
    # them would put a field into the archive that reads as an answer. The keys
    # differ because the physics differs; a reader that finds `temperatures_k`
    # knows it is not looking at a structural result, where one that found
    # `von_mises_nodal` full of zeros would not.
    if hasattr(output, "temperatures_k"):
        arrays: dict[str, Any] = {
            "temperatures_k": output.temperatures_k,
            "heat_flux_w_m2": output.heat_flux_w_m2,
        }
    else:
        nodal = (
            _nodal_average_over(mesh.node_count, mesh.tris, output.von_mises)
            if isinstance(mesh, TriMesh)
            else nodal_average(mesh, output.von_mises)
        )
        arrays = {
            "displacements": output.displacements,
            "von_mises_element": output.von_mises,
            "von_mises_nodal": nodal,
        }

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "fields.npz"
        np.savez_compressed(
            path,
            nodes=mesh.nodes,
            tets=cells,
            surface_triangles=surface,
            **arrays,
        )
        return media.store_path(
            owner_id=job.project.owner_id,
            kind=MediaKind.RESULT_FIELDS,
            path=path,
            filename=f"{job.id}-fields.npz",
            content_type="application/x-npz",
            meta={"simulation_id": job.id, "project_id": job.project_id},
        )
