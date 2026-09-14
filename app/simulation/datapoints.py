"""Finished runs as labelled datapoints for the surrogate flywheel (E10.4).

The database half of `app/optimise/flywheel.py`, kept here so the optimiser
package stays free of sessions and models the way `app/design/` is. A datapoint
is **derived on read and never stored**: a second copy of a result is a copy
that can drift from the row it came from.

Scoped to the organisation, as `cache.py` scopes a lookup, and for the same
reason — one tenant's solves are not another tenant's training data.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.simulation import JobStatus, SimulationJob
from app.optimise.errors import VariableError
from app.optimise.flywheel import Datapoint
from app.solve.types import ForceLoad, LoadCase

#: Responses a structural run stores that a surrogate may be trained on.
RESPONSES: Final = ("max_displacement_mm", "max_von_mises_mpa", "mass_kg")

#: Every feature `features_of` can read off a run, with where it comes from.
FEATURES: Final[dict[str, str]] = {
    "bounding_box_x_mm": "the geometry version's bounding box, x extent",
    "bounding_box_y_mm": "the geometry version's bounding box, y extent",
    "bounding_box_z_mm": "the geometry version's bounding box, z extent",
    "volume_mm3": "the geometry version's B-rep volume (STEP and IGES only)",
    "youngs_modulus_mpa": "the load case's material",
    "applied_force_n": "the magnitude of the vector sum of the load case's forces",
    "element_size_mm": "the mesh size the run was solved at",
}

#: Newest runs read by one harvest. Paginated by recency rather than unbounded:
#: an organisation's whole history is not loaded to train one estimate.
DEFAULT_HARVEST_LIMIT: Final = 10_000


@dataclass(frozen=True)
class Harvest:
    response: str
    datapoints: tuple[Datapoint, ...]
    #: `"<simulation id>: why"` for every finished run that was not used.
    skipped: tuple[str, ...]


def features_of(job: SimulationJob) -> tuple[dict[str, float], list[str]]:
    """Every feature this run carries, and a sentence for each it does not."""
    found: dict[str, float] = {}
    missing: list[str] = []
    stats = (job.geometry_version.stats if job.geometry_version else None) or {}
    size = (stats.get("bounding_box") or {}).get("size")
    if isinstance(size, Sequence) and len(size) == 3:
        for axis, value in zip("xyz", size, strict=True):
            found[f"bounding_box_{axis}_mm"] = float(value)
    else:
        missing.append("the geometry has no bounding box")
    if isinstance(stats.get("volume_mm3"), (int, float)):
        found["volume_mm3"] = float(stats["volume_mm3"])
    else:
        missing.append("the geometry has no B-rep volume (only STEP and IGES carry one)")
    if job.element_size_mm is not None:
        found["element_size_mm"] = float(job.element_size_mm)

    try:
        case = LoadCase.model_validate(job.load_case or {})
    except ValidationError:
        missing.append("its load case does not read as a LoadCase")
        return found, missing
    found["youngs_modulus_mpa"] = case.material.youngs_modulus_mpa
    others = sorted({type(load).__name__ for load in case.loads if not isinstance(load, ForceLoad)})
    if others:
        missing.append(
            f"it carries {', '.join(others)}, which has no single force a feature can hold"
        )
    else:
        total = np.sum([load.force_n for load in case.loads if isinstance(load, ForceLoad)], axis=0)
        found["applied_force_n"] = float(np.linalg.norm(total))
    return found, missing


def harvest(
    db: Session,
    organisation_id: str,
    *,
    response: str,
    features: Sequence[str],
    limit: int = DEFAULT_HARVEST_LIMIT,
) -> Harvest:
    """An organisation's finished solid runs as datapoints for one response.

    **Scoped to the organisation**, the way `simulation/cache.py` scopes a cache
    lookup and for the same reason: one tenant's solves are not another's
    training data. Newest first, at most `limit` rows.
    """
    if response not in RESPONSES:
        raise VariableError(
            f"{response!r} is not a stored response. Available: {', '.join(RESPONSES)}."
        )
    unknown = [name for name in features if name not in FEATURES]
    if unknown:
        raise VariableError(
            f"{unknown} are not features a run carries. Available: {', '.join(FEATURES)}."
        )
    rows = db.scalars(
        select(SimulationJob)
        .join(Project, SimulationJob.project_id == Project.id)
        .where(
            Project.organisation_id == organisation_id,
            SimulationJob.status == JobStatus.SUCCEEDED,
            SimulationJob.analysis == "solid",
        )
        .order_by(SimulationJob.finished_at.desc(), SimulationJob.id)
        .limit(limit)
    ).all()

    datapoints: list[Datapoint] = []
    skipped: list[str] = []
    for job in rows:
        if job.cache_hit:
            skipped.append(
                f"{job.id}: a copy of {job.cache_source_id}'s answer, which is counted once "
                "where it was solved."
            )
            continue
        if job.temperature_source is not None:
            skipped.append(f"{job.id}: carries a borrowed temperature field no feature describes.")
            continue
        result = job.result or {}
        value = result.get(response)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            skipped.append(f"{job.id}: its result has no {response}.")
            continue
        found, missing = features_of(job)
        absent = [name for name in features if name not in found]
        if absent:
            why = "; ".join(missing) or f"no {', '.join(absent)}"
            skipped.append(f"{job.id}: lacks {', '.join(absent)} ({why}).")
            continue
        convergence = (result.get("mesh_convergence") or {}).get("basis", "single-grid")
        datapoints.append(
            Datapoint(
                source=job.id,
                features={name: found[name] for name in features},
                response=float(value),
                solver=job.solver,
                solver_version=job.solver_version,
                mesh_convergence=str(convergence),
            )
        )
    return Harvest(response=response, datapoints=tuple(datapoints), skipped=tuple(skipped))


__all__ = [
    "DEFAULT_HARVEST_LIMIT",
    "FEATURES",
    "RESPONSES",
    "Harvest",
    "features_of",
    "harvest",
]
