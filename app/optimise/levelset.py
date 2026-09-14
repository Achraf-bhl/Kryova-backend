"""Topology optimisation by a level set, on the same plane elements as SIMP.

Master plan 10.3's second method. `topology.py` answers "how much material
where" with a density in [0, 1]; this answers "solid here or not" with a level
set φ ∈ [−1, 1] per element, solid where φ > 0. Every layout it visits is black
and white, so there is no grey fraction to interpret and no penalty deciding
how intermediate material is punished — which is the reason to have both.

The update is the reaction–diffusion form (Yamada, Izui, Nishiwaki and Takezawa,
*CMAME* 199, 2010): each iteration solves

    (I/Δt + τ L) φ⁺ = φ/Δt + ê − λ

where `ê` is each element's strain-energy density normalised by its mean, `L` is
the graph Laplacian of elements that share an edge, `τ` is the regularisation
that penalises boundary length, and `λ` is the multiplier that holds the volume.
Because `L·1 = 0`, `(I/Δt + τL)⁻¹·1 = Δt·1` exactly: `λ` only *shifts* the
solution, so the volume is met by ranking elements rather than by a search. The
matrix does not depend on the design, so it is factorised once.

**No new physics**, as in `topology.py`: the compliance, the displacements and the
element energies come from `topology._System`, which sums
`app.solve.plane.element_stiffness`.

What a result here is not, and says so:

1. **Not a part** (`LEVEL_SET_STATEMENT`): a set of whole elements on a mesh of the
   design space. Its boundary follows element edges, so it is staircased at the
   mesh size — this is an element-wise level set, not a smooth contour.
2. **Not a layout with nothing in the holes.** Void is an ersatz material at
   `ERSATZ_STIFFNESS_RATIO` of solid, because a truly empty element leaves nodes
   attached to nothing. The compliance is therefore slightly *below* the layout's
   own, and the result states the ratio.
3. **Not a global optimum.** The answer depends on the mesh, the regularisation,
   the time step and the volume schedule; it starts from the full design space and
   removes material at `volume_step` per iteration.
4. **Not converged unless it says so.** Converged means the volume has reached its
   target *and* no element has changed side for `stall_iterations` consecutive
   iterations. A layout still swapping boundary elements at the iteration limit is
   reported as the last iterate, with how many were still changing.

`regularisation` acts on the element graph, so it is **per element, not per
millimetre**: refining the mesh at a fixed `τ` smooths less in physical terms.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray

from app.mesh.planar import TriMesh
from app.optimise.errors import VariableError
from app.optimise.topology import THERMAL_REFUSAL, _System
from app.solve.plane import PlaneCase

#: Stiffness of a void element relative to solid. Far larger than SIMP's floor:
#: a black-and-white layout puts whole regions at the floor, and at 1e-9 the
#: system is too ill-conditioned for the equilibrium check to pass (measured).
ERSATZ_STIFFNESS_RATIO: Final = 1e-3

DEFAULT_REGULARISATION: Final = 0.2
DEFAULT_TIME_STEP: Final = 1.0
DEFAULT_VOLUME_STEP: Final = 0.01
DEFAULT_MAX_ITERATIONS: Final = 400
DEFAULT_STALL_ITERATIONS: Final = 10

LEVEL_SET_STATEMENT: Final = (
    "This is a layout of whole elements, each solid or void, on a mesh of the design space "
    "— not a part. Its boundary follows element edges and is staircased at the mesh size. "
    f"Void is modelled as a filler at {ERSATZ_STIFFNESS_RATIO:g} of the solid's stiffness, so "
    "the compliance is slightly below that of the layout alone. Interpreting it into "
    "geometry is a design decision, and the interpreted part must be meshed and analysed "
    "again: none of these numbers describe it. The layout is a local optimum that depends "
    "on the mesh, the regularisation and the volume schedule."
)


@dataclass(frozen=True)
class LevelSetProblem:
    """Minimum compliance at a volume fraction, as a black-and-white layout."""

    case: PlaneCase
    volume_fraction: float
    #: τ: how strongly boundary length is penalised, per element (see module docstring).
    regularisation: float = DEFAULT_REGULARISATION
    #: Δt: how far one iteration may move φ towards what the energies ask for.
    time_step: float = DEFAULT_TIME_STEP
    #: Volume fraction removed per iteration on the way down from the full design space.
    volume_step: float = DEFAULT_VOLUME_STEP
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    stall_iterations: int = DEFAULT_STALL_ITERATIONS

    def __post_init__(self) -> None:
        if not 0.0 < self.volume_fraction < 1.0:
            raise VariableError(
                f"A volume fraction of {self.volume_fraction:g} leaves nothing to decide: at "
                "0 there is no material and at 1 there is nowhere without it. Give a "
                "fraction strictly between 0 and 1."
            )
        if self.regularisation < 0.0 or not math.isfinite(self.regularisation):
            raise VariableError(
                f"A regularisation of {self.regularisation:g} would reward boundary length. "
                "Use 0 for none or a positive value."
            )
        if not (self.time_step > 0.0 and math.isfinite(self.time_step)):
            raise VariableError(f"time_step must be a positive number; got {self.time_step:g}.")
        if not 0.0 < self.volume_step <= 1.0:
            raise VariableError("volume_step must lie in (0, 1].")
        if self.max_iterations < 1:
            raise VariableError("max_iterations must be at least 1.")
        if self.stall_iterations < 1:
            raise VariableError("stall_iterations must be at least 1.")
        if self.case.delta_t_k:
            raise VariableError(THERMAL_REFUSAL)


@dataclass(frozen=True)
class LevelSetResult:
    """A black-and-white layout and exactly what was established about it."""

    #: φ per element in [−1, 1]; solid where φ > 0.
    level_set: NDArray[np.float64]
    #: Compliance Fᵀu of the final layout (void as ersatz), N·mm.
    compliance_n_mm: float
    #: Compliance of the fully solid design space under the same case, N·mm.
    solid_compliance_n_mm: float
    history: tuple[float, ...]
    volume_fraction: float
    converged: bool
    iterations: int
    #: Element edges with solid on one side and void on the other.
    boundary_edges: int
    #: Elements that changed side in the last iteration.
    changed_last: int
    message: str
    warnings: tuple[str, ...] = ()
    seconds: float = 0.0
    ersatz_stiffness_ratio: float = ERSATZ_STIFFNESS_RATIO
    statement: str = LEVEL_SET_STATEMENT

    @property
    def solid(self) -> NDArray[np.bool_]:
        return np.asarray(self.level_set > 0.0)

    @property
    def densities(self) -> NDArray[np.float64]:
        """0 or 1 per element — never anything between."""
        return self.solid.astype(np.float64)

    def summary(self) -> str:
        state = (
            f"converged in {self.iterations} iterations"
            if self.converged
            else f"did NOT converge in {self.iterations} iterations; this is the last iterate"
        )
        return "\n".join(
            [
                f"Level-set topology {state}. Compliance {self.compliance_n_mm:.6g} N·mm at "
                f"volume fraction {self.volume_fraction:.4f} (solid design space: "
                f"{self.solid_compliance_n_mm:.6g} N·mm), {self.boundary_edges} boundary "
                "edges.",
                self.message,
                self.statement,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "level_set": self.level_set.tolist(),
            "solid": self.solid.tolist(),
            "compliance_n_mm": self.compliance_n_mm,
            "solid_compliance_n_mm": self.solid_compliance_n_mm,
            "history": list(self.history),
            "volume_fraction": self.volume_fraction,
            "converged": self.converged,
            "iterations": self.iterations,
            "boundary_edges": self.boundary_edges,
            "changed_last": self.changed_last,
            "message": self.message,
            "warnings": list(self.warnings),
            "ersatz_stiffness_ratio": self.ersatz_stiffness_ratio,
            "statement": self.statement,
        }


def edge_neighbours(mesh: TriMesh) -> sp.csr_matrix:
    """Symmetric 0/1 adjacency of elements that share an edge."""
    tris = mesh.tris
    count = len(tris)
    edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    owner = np.tile(np.arange(count), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owner = edges[order], owner[order]
    shared = np.all(edges[1:] == edges[:-1], axis=1)
    first, second = owner[:-1][shared], owner[1:][shared]
    rows = np.concatenate([first, second])
    cols = np.concatenate([second, first])
    return sp.coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(count, count)).tocsr()


def boundary_edges(neighbours: sp.csr_matrix, solid: NDArray[np.bool_]) -> int:
    pairs = neighbours.tocoo()
    return int(np.count_nonzero(solid[pairs.row] != solid[pairs.col]) // 2)


def optimise_level_set(mesh: TriMesh, problem: LevelSetProblem) -> LevelSetResult:
    """Minimum compliance by a reaction–diffusion level set, from the full design space."""
    started = time.perf_counter()
    system = _System(mesh, problem.case, 1.0, ERSATZ_STIFFNESS_RATIO)
    areas = system.areas
    total_area = float(areas.sum())
    count = mesh.element_count
    neighbours = edge_neighbours(mesh)
    laplacian = sp.diags(np.asarray(neighbours.sum(axis=1)).ravel()) - neighbours
    evolution = spla.splu(
        (sp.identity(count, format="csc") / problem.time_step + problem.regularisation * laplacian).tocsc()
    )

    solid_compliance = system.compliance(np.ones(count)).value
    level_set = np.ones(count, dtype=np.float64)
    volume = 1.0
    history: list[float] = []
    converged = False
    unchanged = 0
    changed = 0
    iterations = 0

    for iterations in range(1, problem.max_iterations + 1):
        layout = (level_set > 0.0).astype(np.float64)
        state = system.compliance(layout)
        history.append(state.value)
        # The gradient at penalty 1 is −(1 − floor)·uᵀkᵉu; the element's own energy
        # carries its actual stiffness, so a void element asks for almost nothing.
        energy = -state.gradient * system.scale(layout) / areas
        drive = energy / float(np.mean(energy))
        proposed = evolution.solve(level_set / problem.time_step + drive)

        target = max(problem.volume_fraction, volume - problem.volume_step)
        updated = _at_volume(proposed, areas, target * total_area)

        changed = int(np.count_nonzero((updated > 0.0) != (level_set > 0.0)))
        level_set = updated
        volume = float(areas[level_set > 0.0].sum()) / total_area
        at_target = target == problem.volume_fraction
        unchanged = unchanged + 1 if (at_target and changed == 0) else 0
        if unchanged >= problem.stall_iterations:
            converged = True
            break

    solid = level_set > 0.0
    final = system.compliance(solid.astype(np.float64)).value
    message = (
        f"The volume reached its target and no element changed side for "
        f"{problem.stall_iterations} consecutive iterations."
        if converged
        else (
            f"The iteration limit of {problem.max_iterations} was reached with {changed} "
            "element(s) still changing side in the last iteration, so this layout is the last "
            "iterate and not a converged one. Raise max_iterations or the regularisation."
        )
    )
    return LevelSetResult(
        level_set=level_set,
        compliance_n_mm=final,
        solid_compliance_n_mm=solid_compliance,
        history=tuple(history + [final]),
        volume_fraction=volume,
        converged=converged,
        iterations=iterations,
        boundary_edges=boundary_edges(neighbours, solid),
        changed_last=changed,
        message=message,
        warnings=system.warnings,
        seconds=time.perf_counter() - started,
    )


def _at_volume(
    proposed: NDArray[np.float64], areas: NDArray[np.float64], target_area: float
) -> NDArray[np.float64]:
    """Shift by the multiplier that puts the nearest achievable area on the target.

    The solid set is decided by rank, not by the sign of a shifted value, so that
    elements tied at the threshold — the whole design space, on the first step of
    a uniform field — cannot all fall on one side of it together.
    """
    order = np.argsort(-proposed, kind="stable")
    cumulative = np.concatenate([[0.0], np.cumsum(areas[order])])
    keep = int(np.argmin(np.abs(cumulative - target_area)))
    keep = min(max(keep, 1), len(proposed) - 1)
    shift = 0.5 * (proposed[order[keep - 1]] + proposed[order[keep]])
    level_set = np.clip(proposed - shift, -1.0, 1.0)
    inside, outside = order[:keep], order[keep:]
    level_set[inside] = np.maximum(level_set[inside], np.finfo(np.float64).tiny)
    level_set[outside] = np.minimum(level_set[outside], 0.0)
    return np.asarray(level_set, dtype=np.float64)


__all__ = [
    "DEFAULT_REGULARISATION",
    "ERSATZ_STIFFNESS_RATIO",
    "LEVEL_SET_STATEMENT",
    "LevelSetProblem",
    "LevelSetResult",
    "boundary_edges",
    "edge_neighbours",
    "optimise_level_set",
]
