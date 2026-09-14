"""Binding a structural run to a finished thermal run's temperature field.

Master plan E10 task 1's coupling, made reachable from a request. The solver has
taken a per-node temperature change since 2026-09-09 (`LinearStaticSolver.solve(
temperatures=)`); this is the check that a *named* field can be honoured, and the
record of exactly which field it was.

One implementation for the two callers that can ask — the HTTP route and the
agent's `run_simulation` tool — because every refusal below is the answer to a
plausible wrong result, and a second copy is where the next one would be
forgotten. Each caller translates the two exception types into its own
vocabulary: a 404 and a 422 for the route, a `ToolError` for the agent.

What is checked, and why each is a refusal rather than a warning:

1. **The source is in this project**, or it does not exist as far as the caller
   is concerned — `SourceNotFound`, which the route answers with 404, the rule
   every other id in this API follows.
2. **It is a thermal run that succeeded and still has its stored field.**
3. **It analysed the same geometry version, meshed the same way.** A field is
   bound to the mesh it was solved on; interpolating it onto another would be a
   sampled field reported as solved. The runner re-checks the node coordinates
   after meshing, so the expectation that gmsh meshes identically from the same
   inputs is verified on every coupled run rather than assumed.
4. **A time sample is asked of a transient run only, and exists.**

The binding written onto the new row carries the **digest of the source's stored
archive**, so a stress is bound to the exact temperatures that produced it and
the cache key moves whenever they would.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import GeometryVersion, JobStatus, SimulationJob
from app.schemas.simulation import TemperatureSource

#: Mirrors `app.simulation.runner.THERMAL_ANALYSES`; imported there rather than
#: from there, because the runner imports the mesher and this module is imported
#: by a route and a tool that should not pay for gmsh to validate a request.
_THERMAL = ("thermal-conduction", "thermal-transient")
_TRANSIENT = "thermal-transient"


class CouplingRefused(ValueError):
    """The named field exists and cannot be honoured by this run. Says why."""


class SourceNotFound(LookupError):
    """No thermal run by that id in this project — including one that exists elsewhere."""


def bind(
    db: Session,
    project_id: str,
    geometry: GeometryVersion,
    source_ref: TemperatureSource,
    *,
    element_size_mm: float | None,
    element_order: int,
) -> dict[str, Any]:
    """The `temperature_source` value for a new run, or the reason there is none."""
    source = db.get(SimulationJob, source_ref.simulation_id)
    if source is None or source.project_id != project_id:
        raise SourceNotFound(
            "The simulation named in temperature_from was not found in this project"
        )
    if source.analysis not in _THERMAL:
        raise CouplingRefused(
            f"temperature_from names a {source.analysis} run, which has no temperature "
            "field. Name a thermal-conduction or thermal-transient run."
        )
    if source.status is not JobStatus.SUCCEEDED or source.fields_media is None:
        raise CouplingRefused(
            f"temperature_from names a run that is {source.status.value} and has no stored "
            "field to read. Wait for it to succeed, or name one that has."
        )
    if source.geometry_version_id != geometry.id:
        raise CouplingRefused(
            "temperature_from names a run on a different geometry version. A temperature "
            "field is bound to the part it was solved on; run the thermal analysis on this "
            "version first."
        )
    if (source.element_size_mm, source.element_order) != (element_size_mm, element_order):
        raise CouplingRefused(
            "A borrowed temperature field is bound to the mesh it was solved on, so this run "
            f"must be meshed the same way: element_size_mm={source.element_size_mm} and "
            f"element_order={source.element_order}, as simulation {source.id} was."
        )
    if source_ref.step is not None and source.analysis != _TRANSIENT:
        raise CouplingRefused(
            "temperature_from.step applies only to a thermal-transient source; a steady run "
            "has one field and no time axis. Omit step."
        )
    step_count = (source.result or {}).get("step_count")
    if source_ref.step is not None and step_count is not None and source_ref.step > step_count:
        raise CouplingRefused(
            f"The thermal run stored {step_count + 1} time samples, numbered 0 to {step_count}; "
            f"there is no sample {source_ref.step}."
        )
    return {
        "simulation_id": source.id,
        "step": source_ref.step,
        "reference_temperature_k": source_ref.reference_temperature_k,
        "fields_sha256": source.fields_media.sha256,
    }
