"""A fatigue check on a finished run: the one place the route and the agent tool share (E8.6).

`app.fatigue` is a library that never touches a job, a file or a session. This module is
the product's side of the seam: it reads the run's archive, builds one `LoadChannel` from the
archived nodal tensor, reads a signed history at one node, and runs an `Assessment`. The HTTP
route and the agent's `assess_fatigue` tool both call `assess_run`, so the two cannot drift on
what a fatigue answer is. A test of the tool is then a test of the path, not of a copy of it.

**What it refuses, each by name:**

* **A run that is not a finished structural solve** (`solid`, `plane-stress`, `plane-strain`).
  Thermal and flow runs hold no stress.
* **An archive with no tensor.** Runs archived before 2026-09-15 kept von Mises, a norm,
  and no signed stress can be recovered from it. The fix is to re-run, and the refusal says so.
* **A node that is not on the mesh**, or a direction on a scalar that does not read one.

**What it does not claim.** One solved load scaled by a signal is exact superposition for a
linear solve, and every structural solver here is linear. It is not a multiaxial,
non-proportional assessment: two independently varying loads need two solves and the library's
multi-channel path, which this route does not take yet. The damage is `APPROXIMATED`, never
measured (`DamageResult.to_payload`), and nothing here is validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import numpy as np

from app.fatigue.assessment import Assessment, MeanStressPolicy
from app.fatigue.factors import Factor, FactorSet
from app.fatigue.field import Direction, LoadChannel, Scalar, history_at, nearest_node
from app.fatigue.material import SNCurve
from app.schemas.fatigue import FatigueRead, FatigueRequest
from app.verify.standards import NOT_VALIDATED

#: The archive key the runner writes the (n_nodes, 6) tensor under.
TENSOR_KEY: Final = "nodal_stress_mpa"

#: The analyses whose archive holds a stress a history can be read from.
STRESS_ANALYSES: Final = frozenset({"solid", "plane-stress", "plane-strain"})


class FatigueRefused(ValueError):
    """A fatigue check this run cannot answer, with the reason a user can act on.

    `status` is the HTTP code the route answers with: 409 when the run is the wrong kind
    or holds no tensor, 422 when the request asks for something that is not there.
    """

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


def refuse_unless_assessable(analysis: str, status: str) -> None:
    """Refuse a run that cannot hold a fatigue answer, before its archive is opened."""
    if status != "succeeded":
        raise FatigueRefused(
            f"The run is {status}; a fatigue check reads a finished run's stress.", status=409
        )
    if analysis not in STRESS_ANALYSES:
        raise FatigueRefused(
            f"This is a {analysis} run. A fatigue check reads a static stress field, which "
            f"only {', '.join(sorted(STRESS_ANALYSES))} runs produce.",
            status=409,
        )


def assess_run(
    arrays: Mapping[str, Any],
    request: FatigueRequest,
    *,
    simulation_id: str,
    solver: str,
    result: dict[str, Any] | None,
) -> FatigueRead:
    """Read the history the request names from a run's archive, and assess it."""
    if TENSOR_KEY not in arrays:
        raise FatigueRefused(
            "This run's archive holds von Mises and no stress tensor, so no signed stress "
            "history can be read from it: von Mises is a norm, and a fully reversed cycle "
            "reads in it as two half-range cycles. Runs archived before 2026-09-15 are like "
            "this. Re-run the analysis and assess the new run.",
            status=409,
        )
    stress = np.asarray(arrays[TENSOR_KEY], dtype=np.float64)
    nodes = np.asarray(arrays["nodes"], dtype=np.float64)

    distance: float | None = None
    if request.node is not None:
        node = request.node
        if node >= len(nodes):
            raise FatigueRefused(
                f"Node {node} is not on this mesh, which has nodes 0 to {len(nodes) - 1}.",
                status=422,
            )
    else:
        assert request.point_mm is not None
        node, distance = nearest_node(nodes, request.point_mm)

    scalar = Scalar(request.scalar)
    try:
        direction = (
            Direction(vector=request.direction.vector, reason=request.direction.reason)
            if request.direction is not None
            else None
        )
        channel = LoadChannel(
            name="the solved load",
            stress_mpa=stress,
            signal=tuple(request.signal),
            source=request.signal_source,
            solver=solver,
        )
        read = history_at(
            [channel],
            node,
            scalar=scalar,
            direction=direction,
            nodes_mm=nodes,
            name=request.location or f"node {node}",
        )
        curve = SNCurve(
            slope_k1=request.curve.slope_k1,
            knee_cycles=request.curve.knee_cycles,
            knee_amplitude_mpa=request.curve.knee_amplitude_mpa,
            source=request.curve.source,
            slope_k2=request.curve.slope_k2,
            scatter_tn=request.curve.scatter_tn,
            failure_probability=request.curve.failure_probability,
            mean_stress_sensitivity=request.curve.mean_stress_sensitivity,
        )
        factors = FactorSet.of(
            Factor(name=f.name, value=f.value, source=f.source) for f in request.factors
        )
        assessment = Assessment(
            loading=read.history,
            curve=curve,
            factors=factors,
            design_life_blocks=request.design_life_blocks,
            damage_limit=request.damage_limit,
            mean_stress_policy=MeanStressPolicy(request.mean_stress_policy),
            mean_stress_justification=request.mean_stress_justification,
            location=request.location or f"node {node}",
        )
    except ValueError as exc:
        raise FatigueRefused(str(exc), status=422) from exc

    outcome = assessment.run()
    notes = list(read.notes)
    if distance is not None:
        notes.insert(
            0,
            f"The point asked for is {distance:.3g} mm from node {node}, the nearest; the "
            "history is that node's.",
        )
    return FatigueRead(
        simulation_id=simulation_id,
        node=node,
        position_mm=read.position_mm,
        distance_mm=distance,
        history_mpa=list(read.history.values_mpa),
        history_source=read.history.source,
        notes=notes,
        outcome=str(outcome.outcome),
        summary=outcome.summary(),
        assessment=outcome.to_payload(),
        result=result,
        statement=NOT_VALIDATED,
    )


__all__ = [
    "STRESS_ANALYSES",
    "TENSOR_KEY",
    "FatigueRefused",
    "assess_run",
    "refuse_unless_assessable",
]
