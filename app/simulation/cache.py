"""Not solving the same thing twice (E15 task 2).

**FEA is not a request-path workload**, which the phase says in as many words,
and the corollary is that a solve nobody needed is the most expensive mistake in
this product. A convergence study is three solves; a sweep is twenty; a mission
re-run after a docstring change is all of them again. The cheapest solve is the
one that already happened.

**The key is the provenance digest, and that is the whole design.** A result is
reusable for a new job if and only if *everything the result depends on* is the
same — the geometry bytes, the load case, the element size and order, the
analysis, the number of grids, the solver and its version. Every one of those is
already recorded, because Decision 3 requires a result to be bound to what
produced it; this hashes exactly that binding and nothing else.

Three things it deliberately excludes, each of which would be a bug:

**The job id, the project and the user.** Two identical runs in one tenant are
the same computation, and keying on identity would mean never hitting. The
lookup *is* scoped to the tenant — see `find`, and the reason there is that a
result crossing a tenant boundary is a data leak wearing a performance
improvement, not that the physics differs.

**Anything not measured.** `solver_version` is `None` when it could not be read,
and a `None` version does **not** hash to the same value as a known one — it
hashes to a distinct sentinel. Treating "we do not know which CalculiX" as a
match would let a result computed by one binary be served as though it came from
another, which is the exact claim Decision 3 forbids.

**Timestamps.** Obvious, and worth saying: including one would make every key
unique and the cache a no-op that still costs a query, which is the failure mode
where a cache looks fine because it never breaks anything.

**A hit is recorded as a hit.** `SimulationJob.cache_hit` is set, and the result
carries the id of the run it was copied from. A result silently reappearing with
`solve_seconds` from a run three weeks ago would make the fleet's timing figures
meaningless, and would make "why was this instant" unanswerable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeometryVersion, JobStatus, Project, SimulationJob

logger = logging.getLogger(__name__)

#: Bumped when the *meaning* of a key changes — a new input starts affecting the
#: result, or an existing one stops. Every old key then misses, which is the
#: correct and safe outcome: recomputing is expensive, serving a result computed
#: under different rules is wrong.
KEY_VERSION = 1

#: What an unmeasured solver version hashes as. A distinct string rather than
#: `None` or `""`, so it can never collide with a version that happens to be
#: falsey, and so a grep for it in a key explains itself.
UNKNOWN_VERSION = "\x00unknown-solver-version"


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
    element_size_mm: float | None
    element_order: int
    analysis: str
    grids: int
    thickness_mm: float | None
    solver: str
    solver_version: str | None

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
            "element_size_mm": self.element_size_mm,
            "element_order": self.element_order,
            "analysis": self.analysis,
            "grids": self.grids,
            "thickness_mm": self.thickness_mm,
            "solver": self.solver,
            "solver_version": self.solver_version or UNKNOWN_VERSION,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inputs_for(db: Session, job: SimulationJob) -> Inputs | None:
    """The binding for one job, or `None` when it cannot be established.

    `None` when the geometry has no checksum to hash — a job whose blob is gone
    is a job that cannot be matched to anything, and inventing a key for it
    would mean two such jobs matching each other.
    """
    version = db.get(GeometryVersion, job.geometry_version_id)
    if version is None or not version.media or not version.media.sha256:
        return None
    return Inputs(
        geometry_sha256=version.media.sha256,
        load_case=job.load_case,
        thermal_case=job.thermal_case,
        element_size_mm=job.element_size_mm,
        element_order=job.element_order,
        analysis=job.analysis,
        grids=job.grids,
        thickness_mm=job.thickness_mm,
        solver=job.solver,
        solver_version=job.solver_version,
    )


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
    "UNKNOWN_VERSION",
    "Inputs",
    "adopt",
    "find",
    "inputs_for",
    "note",
]
