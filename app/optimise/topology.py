"""Topology optimisation by SIMP, on this codebase's own plane elements.

Master plan 10.3. Where material should be, inside a design space, to make a
plane part as stiff as possible for a given amount of it: minimise compliance
`c = Fᵀu` subject to `K(ρ) u = F` and an area-weighted volume fraction.

**No new physics.** Every element matrix is `app.solve.plane.element_stiffness`
— the same matrices `PlaneSolver` sums, verified there against σ = F/A, pure
shear and Lamé — and the loads and fixtures are `PlaneCase`'s, resolved by the
same selectors. What this module adds is the *interpolation* (SIMP: an element's
stiffness is `ρᵖ` of solid, with a floor), the sensitivity `∂c/∂ρ`, a density
filter, and the optimality-criteria update. Decision 2 is about solvers; this is
an optimiser that calls one.

Four things a SIMP result is not, and the result says so rather than leaving it
to be learned:

1. **It is not a part.** It is a density per element on a mesh of the design
   space. Turning it into geometry is interpretation — thresholding, smoothing,
   redrawing — and the interpreted part has to be meshed and analysed again,
   because none of this module's numbers are about it. `CONCEPT_NOT_PART` is
   printed with every result.
2. **It is not a global optimum.** With a penalty above 1 the problem is
   non-convex, and the answer depends on the mesh, the filter radius and the
   starting density. With penalty exactly 1 it is convex (the variable-thickness
   sheet) and the tests check it against the closed-form optimum.
3. **It is not converged unless it says so.** A run that reached its iteration
   limit reports `converged=False` and its densities are the last iterate,
   labelled as one.
4. **Intermediate density is not material.** `grey_fraction` is the share of the
   design area between 0.1 and 0.9 — neither solid nor void, and not something
   a manufacturer can make. A result with a large grey fraction has not decided
   where material goes.

A thermal load (`delta_t_k`) is refused: restrained expansion scales with the
density too, a design-dependent load this sensitivity does not include, and a
gradient missing a term steers confidently to the wrong design.
"""

from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass
from typing import Any, Final

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from app.mesh.planar import TriMesh
from app.optimise.errors import OptimisationError, VariableError
from app.solve.plane import (
    PlaneCase,
    assemble_loads,
    element_stiffness,
    restrained_dofs,
    winding,
)
from app.solve.types import SolverError

#: Stiffness of an empty element relative to a solid one. Not zero: a void
#: element with zero stiffness leaves nodes connected to nothing and the system
#: singular. Small enough that the floor carries no load a reader would notice.
MIN_STIFFNESS_RATIO: Final = 1e-9

DEFAULT_PENALTY: Final = 3.0
DEFAULT_MOVE_LIMIT: Final = 0.2
DEFAULT_CHANGE_TOLERANCE: Final = 0.01
DEFAULT_MAX_ITERATIONS: Final = 200

#: Density band counted as neither solid nor void.
GREY_BAND: Final = (0.1, 0.9)

#: Equilibrium residual accepted from each solve, relative to the load. The
#: same bound `app.solve.plane` holds its own solves to.
RESIDUAL_TOLERANCE: Final = 1e-8

CONCEPT_NOT_PART: Final = (
    "This is a material layout on a mesh of the design space, not a part. Interpreting "
    "it into geometry is a design decision, and the interpreted part must be meshed and "
    "analysed again: none of these numbers describe it. With a penalty above 1 the "
    "layout is a local optimum that depends on the mesh, the filter radius and the start."
)


#: Why a thermal load is refused, shared with `levelset.py`.
THERMAL_REFUSAL: Final = (
    "A thermal load cannot be topology-optimised here: restrained expansion scales with "
    "each element's material, which makes the load depend on the design, and the "
    "sensitivity in this module does not include that term. Optimise for the mechanical "
    "loads, or remove delta_t_k."
)


@dataclass(frozen=True)
class TopologyProblem:
    """Minimum compliance at a volume fraction, for one plane load case."""

    case: PlaneCase
    volume_fraction: float
    #: Filter radius in mm, measured between element centroids. Zero switches
    #: the filter off, which permits checkerboards — a pattern of alternating
    #: solid and void that is artificially stiff on linear triangles and is not
    #: a design. A radius of 1.5–3 element sizes is the usual choice.
    filter_radius_mm: float
    penalty: float = DEFAULT_PENALTY
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    change_tolerance: float = DEFAULT_CHANGE_TOLERANCE
    move_limit: float = DEFAULT_MOVE_LIMIT

    def __post_init__(self) -> None:
        if not 0.0 < self.volume_fraction < 1.0:
            raise VariableError(
                f"A volume fraction of {self.volume_fraction:g} leaves nothing to decide: at "
                "0 there is no material and at 1 there is nowhere without it. Give a "
                "fraction strictly between 0 and 1."
            )
        if self.penalty < 1.0:
            raise VariableError(
                f"A SIMP penalty of {self.penalty:g} rewards intermediate density — half the "
                "material would give more than half the stiffness — so the layout drifts "
                "grey rather than towards solid and void. Use 1 (the convex "
                "variable-thickness sheet) or more; 3 is conventional."
            )
        if self.filter_radius_mm < 0.0 or not math.isfinite(self.filter_radius_mm):
            raise VariableError(
                f"A filter radius of {self.filter_radius_mm:g} mm is not a length. Use 0 for "
                "no filter or a positive radius in mm."
            )
        if self.max_iterations < 1:
            raise VariableError("max_iterations must be at least 1.")
        if not 0.0 < self.change_tolerance < 1.0:
            raise VariableError(
                "change_tolerance is the largest density change per iteration that counts "
                "as converged, and must lie strictly between 0 and 1."
            )
        if not 0.0 < self.move_limit <= 1.0:
            raise VariableError("move_limit must lie in (0, 1].")
        if self.case.delta_t_k:
            raise VariableError(THERMAL_REFUSAL)


@dataclass(frozen=True)
class Compliance:
    """Compliance at one density field, its gradient, and the displacement."""

    value: float
    gradient: NDArray[np.float64]
    displacements: NDArray[np.float64]


@dataclass(frozen=True)
class TopologyResult:
    """A material layout and exactly what was established about it."""

    #: Physical (filtered) density per element, in [0, 1].
    densities: NDArray[np.float64]
    #: Compliance Fᵀu at the final layout, N·mm.
    compliance_n_mm: float
    #: Compliance of the fully solid design space under the same case, N·mm.
    solid_compliance_n_mm: float
    history: tuple[float, ...]
    volume_fraction: float
    converged: bool
    iterations: int
    grey_fraction: float
    message: str
    warnings: tuple[str, ...] = ()
    seconds: float = 0.0
    statement: str = CONCEPT_NOT_PART

    def summary(self) -> str:
        state = (
            f"converged in {self.iterations} iterations"
            if self.converged
            else f"did NOT converge in {self.iterations} iterations; these are the last iterate"
        )
        return "\n".join(
            [
                f"Topology {state}. Compliance {self.compliance_n_mm:.6g} N·mm at volume "
                f"fraction {self.volume_fraction:.4f} (solid design space: "
                f"{self.solid_compliance_n_mm:.6g} N·mm). Grey fraction "
                f"{self.grey_fraction:.3f}.",
                self.message,
                self.statement,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "densities": self.densities.tolist(),
            "compliance_n_mm": self.compliance_n_mm,
            "solid_compliance_n_mm": self.solid_compliance_n_mm,
            "history": list(self.history),
            "volume_fraction": self.volume_fraction,
            "converged": self.converged,
            "iterations": self.iterations,
            "grey_fraction": self.grey_fraction,
            "message": self.message,
            "warnings": list(self.warnings),
            "statement": self.statement,
        }


class _System:
    """Everything about the case that does not change with density."""

    def __init__(
        self, mesh: TriMesh, case: PlaneCase, penalty: float, floor: float = MIN_STIFFNESS_RATIO
    ) -> None:
        if mesh.element_count == 0:
            raise OptimisationError("The design space mesh has no elements.")
        self.mesh = mesh
        self.penalty = penalty
        self.floor = floor
        self.ke, dofs = element_stiffness(mesh, case.material, case.state, case.thickness_mm)
        width = dofs.shape[1]
        self.rows = np.repeat(dofs, width, axis=1).ravel()
        self.cols = np.tile(dofs, (1, width)).ravel()
        self.dofs = dofs
        self.n_dof = 2 * mesh.node_count
        self.forces, load_warnings = assemble_loads(mesh, case, winding(mesh))
        self.warnings = tuple(load_warnings)
        fixed = restrained_dofs(mesh, case.fixtures)
        self.free = np.setdiff1d(np.arange(self.n_dof), fixed)
        if len(self.free) == 0:
            raise SolverError("Every degree of freedom is fixed; there is nothing to optimise.")
        if not np.any(self.forces):
            raise VariableError(
                "The load case applies no force, so every layout has zero compliance and "
                "there is nothing to optimise."
            )
        self.areas = np.abs(mesh.signed_areas())

    def scale(self, densities: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.floor + (1.0 - self.floor) * densities**self.penalty

    def compliance(self, densities: NDArray[np.float64]) -> Compliance:
        scale = self.scale(densities)
        data = (scale[:, None, None] * self.ke).ravel()
        stiffness = sp.coo_matrix((data, (self.rows, self.cols)), shape=(self.n_dof, self.n_dof))
        k_ff = stiffness.tocsr()[self.free][:, self.free].tocsc()
        applied = self.forces[self.free]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", spla.MatrixRankWarning)
                solution = spla.spsolve(k_ff, applied, permc_spec="COLAMD", use_umfpack=False)
        except (RuntimeError, spla.MatrixRankWarning) as exc:
            raise SolverError(
                "The design space is under-constrained: part of it can still move freely "
                f"({exc}). Check that the fixtures remove all three rigid-body motions."
            ) from exc
        solution = np.asarray(solution, dtype=np.float64)
        residual = float(np.linalg.norm(k_ff @ solution - applied))
        if not np.all(np.isfinite(solution)) or residual > RESIDUAL_TOLERANCE * float(
            np.linalg.norm(applied)
        ):
            raise SolverError(
                "The design space is under-constrained: the solution does not satisfy "
                "equilibrium. Check that the fixtures remove all three rigid-body motions."
            )
        u = np.zeros(self.n_dof, dtype=np.float64)
        u[self.free] = solution
        ue = u[self.dofs]
        strain_energy = np.einsum("ei,eij,ej->e", ue, self.ke, ue)
        d_scale = (1.0 - self.floor) * self.penalty * densities ** (self.penalty - 1.0)
        return Compliance(
            value=float(self.forces @ u),
            gradient=np.asarray(-d_scale * strain_energy, dtype=np.float64),
            displacements=u,
        )


def compliance(mesh: TriMesh, problem: TopologyProblem, densities: NDArray[np.float64]) -> Compliance:
    """Compliance and ∂c/∂ρ at a *physical* density field. Public so it can be tested."""
    densities = np.asarray(densities, dtype=np.float64)
    if densities.shape != (mesh.element_count,):
        raise VariableError(
            f"{densities.shape} densities for {mesh.element_count} elements; give one per element."
        )
    return _System(mesh, problem.case, problem.penalty).compliance(densities)


def density_filter(mesh: TriMesh, radius_mm: float) -> sp.csr_matrix:
    """The row-normalised, area-weighted cone filter between element centroids.

    `ρ̃ = W ρ`, with `W_ij ∝ max(0, r − |c_i − c_j|) · A_j`. Area-weighted because an
    unstructured mesh's elements are not equal, and an unweighted filter would
    let small elements count as much as large ones. Radius 0 is the identity.
    """
    n = mesh.element_count
    if radius_mm == 0.0:
        return sp.identity(n, format="csr", dtype=np.float64)
    centroids = mesh.nodes[mesh.tris][:, :, :2].mean(axis=1)
    tree = cKDTree(centroids)
    distances = tree.sparse_distance_matrix(tree, radius_mm, output_type="coo_matrix")
    weights = sp.coo_matrix(
        (np.maximum(0.0, radius_mm - distances.data), (distances.row, distances.col)), shape=(n, n)
    ).tocsr()
    # The self-pair is already there: `sparse_distance_matrix` stores each point's
    # zero distance to itself, so the diagonal is `r`. Adding `r·I` on top once
    # doubled every element's own weight, invisibly on a uniform mesh.
    areas = np.abs(mesh.signed_areas())
    weighted = weights @ sp.diags(areas)
    row_sums = np.asarray(weighted.sum(axis=1)).ravel()
    return (sp.diags(1.0 / row_sums) @ weighted).tocsr()


#: How far the achieved volume fraction may sit from the target and still count
#: as meeting it. The multiplier search lands within round-off of the target
#: whenever the move limit allows it to be reached at all.
VOLUME_TOLERANCE: Final = 1e-6


def optimise_topology(
    mesh: TriMesh,
    problem: TopologyProblem,
    *,
    start: NDArray[np.float64] | None = None,
) -> TopologyResult:
    """Minimum compliance by SIMP with optimality-criteria updates.

    `start` is a design density per element (before filtering); the default is
    the target volume fraction everywhere. A start at another volume is walked to
    the target within the move limit, and a run is not reported converged until
    the volume is on target as well as the densities having stopped moving.
    """
    started = time.perf_counter()
    system = _System(mesh, problem.case, problem.penalty)
    filtering = density_filter(mesh, problem.filter_radius_mm)
    areas = system.areas
    total_area = float(areas.sum())
    # dV/dρ through the filter: V = aᵀ W ρ / A.
    volume_gradient = np.asarray(filtering.T @ areas, dtype=np.float64) / total_area

    def physical(design: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(filtering @ design, dtype=np.float64)

    def volume(design: NDArray[np.float64]) -> float:
        return float(areas @ physical(design)) / total_area

    solid = system.compliance(np.ones(mesh.element_count)).value
    if start is None:
        design = np.full(mesh.element_count, problem.volume_fraction, dtype=np.float64)
    else:
        design = np.asarray(start, dtype=np.float64).copy()
        if design.shape != (mesh.element_count,) or not np.all((design >= 0.0) & (design <= 1.0)):
            raise VariableError(
                f"A start needs one density in [0, 1] per element ({mesh.element_count}); "
                f"got shape {design.shape}."
            )
    history: list[float] = []
    converged = False
    iterations = 0
    last = system.compliance(physical(design))

    for iterations in range(1, problem.max_iterations + 1):
        history.append(last.value)
        gradient = np.asarray(filtering.T @ last.gradient, dtype=np.float64)
        drive = np.maximum(-gradient, 0.0) / np.maximum(volume_gradient, 1e-300)

        updated = _volume_matched(design, drive, volume, problem)

        change = float(np.max(np.abs(updated - design)))
        design = updated
        last = system.compliance(physical(design))
        on_target = abs(volume(design) - problem.volume_fraction) <= VOLUME_TOLERANCE
        if change < problem.change_tolerance and on_target:
            converged = True
            break

    densities = physical(design)
    grey = (densities > GREY_BAND[0]) & (densities < GREY_BAND[1])
    message = (
        f"The largest density change in the last iteration was below "
        f"{problem.change_tolerance:g}."
        if converged
        else (
            f"The iteration limit of {problem.max_iterations} was reached with densities still "
            "moving, so this layout is the last iterate and not a converged one. Raise "
            "max_iterations, or loosen change_tolerance knowingly."
        )
    )
    return TopologyResult(
        densities=densities,
        compliance_n_mm=last.value,
        solid_compliance_n_mm=solid,
        history=tuple(history + [last.value]),
        volume_fraction=float(areas @ densities) / total_area,
        converged=converged,
        iterations=iterations,
        grey_fraction=float(areas[grey].sum()) / total_area,
        message=message,
        warnings=system.warnings,
        seconds=time.perf_counter() - started,
    )


def _oc_step(
    design: NDArray[np.float64], drive: NDArray[np.float64], multiplier: float, move: float
) -> NDArray[np.float64]:
    """The optimality-criteria update at one Lagrange multiplier."""
    proposed = design * np.sqrt(drive / multiplier)
    lower = np.maximum(0.0, design - move)
    upper = np.minimum(1.0, design + move)
    return np.asarray(np.clip(proposed, lower, upper), dtype=np.float64)


def _volume_matched(
    design: NDArray[np.float64],
    drive: NDArray[np.float64],
    volume: Any,
    problem: TopologyProblem,
) -> NDArray[np.float64]:
    """The OC update whose multiplier puts the volume on its target.

    Bisected in **log** space. The multiplier carries the units of compliance
    per unit volume, which in N·mm on a steel part can sit forty decades from 1;
    a linear bisection from [0, 1] would spend its whole budget reaching that
    order and none refining it.
    """
    target = problem.volume_fraction
    move = problem.move_limit

    def too_much(multiplier: float) -> bool:
        return bool(volume(_oc_step(design, drive, multiplier, move)) > target)

    low = high = 1.0
    if too_much(high):
        while too_much(high) and high < 1e300:
            low, high = high, high * 1e3
    else:
        while not too_much(low) and low > 1e-300:
            low, high = low * 1e-3, low
    for _ in range(200):
        if high <= low * (1.0 + 1e-12):
            break
        middle = math.sqrt(low * high)
        if too_much(middle):
            low = middle
        else:
            high = middle
    return _oc_step(design, drive, high, move)


__all__ = [
    "CONCEPT_NOT_PART",
    "DEFAULT_PENALTY",
    "GREY_BAND",
    "MIN_STIFFNESS_RATIO",
    "THERMAL_REFUSAL",
    "VOLUME_TOLERANCE",
    "Compliance",
    "TopologyProblem",
    "TopologyResult",
    "compliance",
    "density_filter",
    "optimise_topology",
]
