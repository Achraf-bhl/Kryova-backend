"""Not solving the same thing twice (E15 task 2).

**FEA is not a request-path workload**, which the phase says in as many words,
and the corollary is that a solve nobody needed is the most expensive mistake in
this product. A convergence study is three solves; a sweep is twenty; a mission
re-run after a docstring change is all of them again. The cheapest solve is the
one that already happened.

**The key is the provenance digest, and that is the whole design.** A result is
reusable for a new job if and only if *everything the result depends on* is the
same — the geometry bytes, the load case, the element size and order, the
analysis, the number of grids, and **the engine that computes it, identified
before it runs**. Decision 3 requires a result to be bound to what produced it;
this hashes exactly that binding and nothing else.

Three things it deliberately excludes, each of which would be a bug:

**The job id, the project and the user.** Two identical runs in one tenant are
the same computation, and keying on identity would mean never hitting. The
lookup *is* scoped to the tenant — see `find`, and the reason there is that a
result crossing a tenant boundary is a data leak wearing a performance
improvement, not that the physics differs.

**Anything not measured — and an engine that cannot be named is not keyed at
all.** Until 2026-09-14 the key hashed `job.solver_version`, which the runner only
sets *after* the solve, so at key time it was always empty and every key carried
the same "unknown version" sentinel: a CalculiX upgrade, or a fix to the in-house
solver, was served the previous build's answers. The docstring said an unmeasured
version could never match a known one; in practice nothing was ever measured, so
unknown matched unknown everywhere. The engine is now read **before** the run
(`engine_for`): a CalculiX binary by version and bytes, the in-house solvers by
the source that computes the answer, OpenFOAM by its image id, and every one of
them with the mesher's version beside it. Where that cannot be done, `inputs_for`
returns None and the run is neither looked up nor offered for reuse. After the
run, `unbound` checks the key still describes what answered.

**Timestamps.** Obvious, and worth saying: including one would make every key
unique and the cache a no-op that still costs a query, which is the failure mode
where a cache looks fine because it never breaks anything.

**A hit is recorded as a hit.** `SimulationJob.cache_hit` is set, and the result
carries the id of the run it was copied from. A result silently reappearing with
`solve_seconds` from a run three weeks ago would make the fleet's timing figures
meaningless, and would make "why was this instant" unanswerable.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeometryVersion, JobStatus, Project, SimulationJob

logger = logging.getLogger(__name__)

#: Bumped when the *meaning* of a key changes — a new input starts affecting the
#: result, or an existing one stops. Every old key then misses, which is the
#: correct and safe outcome: recomputing is expensive, serving a result computed
#: under different rules is wrong.
#:
#: 2 (2026-09-14): `transient_case` joined the key with the `thermal-transient`
#: analysis, and `temperature_source` — which carries the digest of the borrowed
#: field's archive, so two runs coupled to different temperatures never match. Every version-1 key misses once, and a steady or structural run
#: re-solves the first time it is asked for again.
#:
#: 3 (2026-09-14): `flow_case` joined with the `flow-laminar` analysis, and
#: `engine` — the OpenFOAM image's content id, read before the run — because a
#: flow run's `solver` is always "openfoam" and the tag in `OPENFOAM_IMAGE` can be
#: pointed at a different build. A flow run whose engine cannot be identified
#: first (the `local` launcher) is never cached at all.
#:
#: 4 (2026-09-14): `solver_version` left the key — it was always empty at key time —
#: and `engine` became required for every analysis, read before the run. `solver`
#: is now the backend that will answer (`runner.backend_for`), not the label the
#: route wrote at queue time. Every version-3 key misses once.
KEY_VERSION = 4

#: The source an in-house result depends on, beyond the fingerprint V&V already
#: keeps: the runner decides what is stored (nodal averaging on a plane mesh, the
#: pressure conversion, a study's grid spacing) and `coupling` decides which
#: temperatures a structural run borrows.
_IN_HOUSE_SOURCES: Final[tuple[str, ...]] = (
    "app/simulation/runner.py",
    "app/simulation/coupling.py",
)


@dataclass(frozen=True)
class Inputs:
    """Everything a linear-static result depends on, and nothing else.

    A dataclass rather than a dict so that adding an input is a change the type
    checker sees at every call site — the failure this guards against is a new
    knob that changes the answer and is not in the key, which produces a cache
    that confidently serves the wrong number.
    """

    geometry_sha256: str
    load_case: dict[str, Any] | None
    thermal_case: dict[str, Any] | None
    transient_case: dict[str, Any] | None
    flow_case: dict[str, Any] | None
    temperature_source: dict[str, Any] | None
    element_size_mm: float | None
    element_order: int
    analysis: str
    grids: int
    thickness_mm: float | None
    #: The backend that will answer, read from this deployment's settings when the
    #: run starts (`runner.backend_for`) — not the label a route wrote at queue time.
    solver: str
    #: The exact engine behind that name, identified before the run: see
    #: `engine_for`. Never None — a run whose engine cannot be named has no key.
    engine: str

    def digest(self) -> str:
        """A stable hash of the binding. Same inputs, same key, on any machine.

        `sort_keys` and the compact separators are what make it stable across
        Python versions and dict insertion order — the same reasoning
        `DesignSpec.digest` records, and for the same reason: a key that moved
        when a dict happened to be built differently would make the cache a
        random-hit generator.
        """
        payload = {
            "version": KEY_VERSION,
            "geometry": self.geometry_sha256,
            "load_case": self.load_case,
            "thermal_case": self.thermal_case,
            "transient_case": self.transient_case,
            "flow_case": self.flow_case,
            "temperature_source": self.temperature_source,
            "element_size_mm": self.element_size_mm,
            "element_order": self.element_order,
            "analysis": self.analysis,
            "grids": self.grids,
            "thickness_mm": self.thickness_mm,
            "solver": self.solver,
            "engine": self.engine,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inputs_for(db: Session, job: SimulationJob) -> Inputs | None:
    """The binding for one job, or `None` when it cannot be established.

    `None` when the geometry has no checksum to hash — a job whose blob is gone
    is a job that cannot be matched to anything, and inventing a key for it
    would mean two such jobs matching each other — and `None` when the engine
    that will answer cannot be identified before it runs, for the same reason.
    """
    version = db.get(GeometryVersion, job.geometry_version_id)
    if version is None or not version.media or not version.media.sha256:
        return None
    # Imported here: the runner imports this module.
    from app.simulation.runner import backend_for

    backend = backend_for(job.analysis)
    engine = engine_for(backend)
    if engine is None:
        return None
    return Inputs(
        geometry_sha256=version.media.sha256,
        load_case=job.load_case,
        thermal_case=job.thermal_case,
        transient_case=job.transient_case,
        flow_case=job.flow_case,
        temperature_source=job.temperature_source,
        element_size_mm=job.element_size_mm,
        element_order=job.element_order,
        analysis=job.analysis,
        grids=job.grids,
        thickness_mm=job.thickness_mm,
        solver=backend,
        engine=engine,
    )


def engine_for(backend: str) -> str | None:
    """What will compute a result on `backend`, named before it runs — or None.

    * **CalculiX**: the binary's version and sha256 (`registry.calculix_identity`).
    * **In-house** (`internal`, whichever table selected it): the source that
      computes the answer, plus numpy's and scipy's versions (`in_house_identity`).
    * **OpenFOAM**: the Docker image's content id (`openfoam.run.engine_identity`).

    Every one carries the mesher's version beside it, because every analysis here
    meshes with gmsh first and a different gmsh is a different mesh. A backend
    this build does not know, or one whose identity cannot be read, is None.
    """
    import gmsh

    from app.core.config import settings
    from app.solve.registry import CALCULIX, INTERNAL, OPENFOAM, calculix_identity

    identity: str | None
    if backend == INTERNAL:
        identity = in_house_identity()
    elif backend == CALCULIX:
        identity = calculix_identity(settings.calculix_path or None)
    elif backend == OPENFOAM:
        from app.solve.openfoam.run import engine_identity

        identity = engine_identity(settings.openfoam_launcher, settings.openfoam_image)
    else:
        identity = None
    return f"{identity}; gmsh {gmsh.__version__}" if identity else None


def source_identity(root: Path | None = None) -> str:
    """A digest of the source an in-house result depends on.

    V&V's own fingerprint (`app/solve`, `app/mesh` and the verification modules
    that decide a number) with `_IN_HOUSE_SOURCES` folded in, normalised the same
    way — so it moves when anything that computes or stores an answer moves, and
    a docstring edit elsewhere in `app/` does not throw the cache away.
    """
    from app.verify.recorded import _REPO_ROOT, code_fingerprint

    base = Path(_REPO_ROOT) if root is None else root
    digest = hashlib.sha256(code_fingerprint(base).encode("utf-8"))
    for relative in _IN_HOUSE_SOURCES:
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256((base / relative).read_bytes().replace(b"\r\n", b"\n")).digest())
    return "sha256:" + digest.hexdigest()


@functools.cache
def in_house_identity() -> str:
    """`source_identity` for the code this process imported, plus its numerics.

    Once per process, on purpose: the code a worker runs is the code it imported,
    and a file edited on disk under a running server is not what answers until the
    server restarts. numpy and scipy are named because `spsolve` and `eigsh` are
    theirs, and a release that changes an answer changes it here.
    """
    import numpy
    import scipy

    return f"kryova {source_identity()}; numpy {numpy.__version__}; scipy {scipy.__version__}"


def unbound(inputs: Inputs, ran: str) -> str | None:
    """Why a key bound before a run does not describe the run that happened, or None.

    Two ways: a different backend answered than the one keyed (a solver handed in
    by the caller, or a setting that moved between the lookup and the solve), or
    the engine the key named is no longer the engine — a binary replaced or an
    image re-tagged while the run was in flight. Either way a row carrying the key
    would be served to the next job as a computation it is not.
    """
    from app.solve.registry import backend_of

    if backend_of(ran) != inputs.solver:
        return f"the key was bound to the {inputs.solver!r} backend and {ran!r} answered"
    if engine_for(inputs.solver) != inputs.engine:
        return f"the {inputs.solver} engine changed while the run was in flight"
    return None


def find(db: Session, job: SimulationJob, key: str) -> SimulationJob | None:
    """A finished run with this key that this job's tenant is allowed to see.

    **Scoped to the organisation, and that is a security boundary rather than a
    tuning choice.** The physics does not care whose geometry it was, but a
    result crossing a tenant boundary tells one customer that another has a part
    with this exact checksum, load case and mass — which is a data leak wearing
    a performance improvement.

    Newest first: a repeated run is most likely to match the last one, and the
    ordering also means a cache filled over months serves the most recent
    binding rather than the oldest.
    """
    project = db.get(Project, job.project_id)
    if project is None:  # pragma: no cover - FK-enforced
        return None
    return db.scalar(
        select(SimulationJob)
        .join(Project, SimulationJob.project_id == Project.id)
        .where(
            SimulationJob.cache_key == key,
            SimulationJob.status == JobStatus.SUCCEEDED,
            SimulationJob.id != job.id,
            # A cached row that was itself a hit is fine to copy from — the
            # result is the same bytes either way — but the *source* recorded on
            # the new job follows the chain to the original, so `cache_source_id`
            # always names a run that actually solved. See `adopt`.
            Project.organisation_id == project.organisation_id,
        )
        .order_by(SimulationJob.finished_at.desc())
        .limit(1)
    )


def adopt(job: SimulationJob, source: SimulationJob) -> None:
    """Copy a finished result onto `job`, recorded as the copy it is.

    Everything that describes the *answer* is copied; everything that describes
    *this run* is not. `started_at`/`finished_at` stay this job's own, so the
    fleet's timing figures continue to measure wall-clock rather than absorbing
    a three-week-old solve as though it happened just now.
    """
    job.result = source.result
    job.mesh_stats = source.mesh_stats
    job.fields_media_id = source.fields_media_id
    job.solver = source.solver
    job.solver_version = source.solver_version
    job.cache_hit = True
    # Follow the chain, so this always names a run that really solved rather
    # than another hit. One hop is enough: a source that was itself a hit has
    # already been given the original's id by this same line.
    job.cache_source_id = source.cache_source_id or source.id


def note(job: SimulationJob, source: SimulationJob) -> str:
    """One line for a log and for the job's own record.

    Says *which* run it came from. "Served from cache" with no id makes "why was
    this instant, and can I trust it" unanswerable, which is the question a
    cache in a verification product has to be able to answer.
    """
    return (
        f"Reused the result from run {source.id}, which has the same geometry, load case, "
        f"mesh and solver. Nothing was re-solved."
    )


__all__ = [
    "KEY_VERSION",
    "Inputs",
    "adopt",
    "engine_for",
    "find",
    "in_house_identity",
    "inputs_for",
    "note",
    "source_identity",
    "unbound",
]
