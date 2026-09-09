"""Steady-state heat conduction on 4-node and 10-node tetrahedra.

`app/solve/thermal.py` solves the *restrained* thermal problem: one uniform
`delta_t_k` over the whole part, applied as an equivalent load. Its own docstring
names what it cannot do — "a real thermal problem has a temperature *field*,
which needs a conduction solve with its own boundary conditions" — and this is
that solve. It answers

    div(k grad T) + q_v = 0

with three boundary conditions built from the existing `Selector` vocabulary: a
prescribed temperature (Dirichlet), a convection film (Robin), and a surface heat
flux (Neumann). The Robin term is the one that makes the analysis useful rather
than decorative — a part in air is not held at a temperature, it exchanges heat
with what is around it — and it is the one that has to be *assembled* rather than
merely accepted, because it puts terms into the conductivity matrix and not only
into the load vector.

**Conductivity is not on `Material`, and that is a deliberate stopping point.**
`Material` carries what a stress or modal run needs plus `thermal_expansion_per_k`,
and adding `conductivity_w_mk` to it would mean every material in
`app/solve/materials.py` either gains a transcribed value with a citation or gains
a `None` that the first conduction run refuses by name. That is the right end
state — the property belongs on the material, next to the expansion coefficient
that the same physics uses — and it is a change to `app/solve/types.py` and to the
material register together, in one pass with the sources read off a document the
way `materials.transcribe` requires. Until then the number arrives on the *case*,
where the caller has to state it explicitly and cannot inherit a default nobody
checked.

**Units.** Everything the caller writes is in the units an engineer quotes:
conductivity in W/(m·K), film coefficient in W/(m²·K), flux in W/m², volumetric
source in W/m³, temperature in **kelvin, absolute, never celsius**. Everything
assembled is in the mm-based system this codebase uses, because the mesh is in
millimetres — so `k` becomes W/(mm·K), `h` becomes W/(mm²·K) and so on. That
conversion happens **once, at the top of `SteadyConductionSolver.solve`, through the
named constants below**, in exactly the sense `app/solve/calculix/deck.py`
converts density at the deck boundary. It is not a violation of "nothing
converts": mm-N-MPa fixes length, force and stress and says nothing about watts,
so this is a new quantity landing in the system at its boundary rather than an
existing one being re-expressed. Get it wrong and a pure-Dirichlet profile still
looks perfect (the scale cancels), while every convection and flux answer is
wrong by three or six orders of magnitude — which is why the film-coefficient
test below is written against a closed form that contains `h`, `k` and `L`
together and not against a temperature difference alone.

**The one guard that matters most.** A model with no prescribed temperature and
no convection film is *floating*: `div(k grad T) = 0` with only insulated and
flux boundaries determines the temperature up to an arbitrary additive constant,
so the conductivity matrix is singular and a direct solve returns a finite,
meaningless vector — the same trap `linear_static` documents for an
under-constrained structure. It is caught twice: structurally, before assembly,
because a case declaring neither boundary type cannot be anything else; and
numerically, by a heat-balance residual on the solution, because a convection
region that selected nodes but no complete surface facets declares a film and
contributes nothing. Neither check looks for NaNs.
"""

from __future__ import annotations

import time
import warnings as warnings_module
from collections.abc import Sequence
from dataclasses import dataclass, field
from math import factorial
from typing import Annotated, Any, Final, Literal

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from app import observe
from app.mesh.types import TET10_EDGES, TetMesh
from app.solve.base import ConductionSolver
from app.solve.linear_static import (
    _CENTROID,
    _TET_GAUSS_POINTS,
    _TET_GAUSS_WEIGHT,
    _element_dofs,
    _mapped_gradients,
    _shape_gradients,
    _strain_displacement,
    _tet10_shape_gradients,
    constitutive_matrix,
)
from app.solve.selection import _boundary_faces_within, select_nodes
from app.solve.thermal import thermal_strain, thermal_stress_correction
from app.solve.types import Material, MeshConvergence, Selector, SolverError

# -- the unit boundary --------------------------------------------------------
#
# Four conversions, all in one direction, all applied exactly once in
# `SteadyConductionSolver.solve`. Nothing downstream of that point sees a W/m
# anything.
# They are constants rather than inline literals for the reason `deck.py` gives:
# a run that corrects one of them should correct one value, not hunt a scatter of
# format strings.

#: W/(m·K) -> W/(mm·K). Steel's 51.9 becomes 0.0519.
CONDUCTIVITY_W_MK_TO_W_MMK: Final = 1e-3

#: W/(m²·K) -> W/(mm²·K). Free convection in air, ~10, becomes 1e-5.
FILM_W_M2K_TO_W_MM2K: Final = 1e-6

#: W/m² -> W/mm². A 1 kW/m² surface load becomes 1e-3 W/mm².
FLUX_W_M2_TO_W_MM2: Final = 1e-6

#: W/m³ -> W/mm³. Ohmic heating quoted per cubic metre becomes per cubic mm.
SOURCE_W_M3_TO_W_MM3: Final = 1e-9

# Above this many nodes, use preconditioned CG rather than a direct factorisation
# — the same crossover and the same reasoning as `linear_static`, one degree of
# freedom per node instead of three, so the same matrix size is reached at three
# times the node count.
_ITERATIVE_THRESHOLD_NODES = 100_000

#: Heat-balance residual accepted as "solved", relative to the applied heat.
_RESIDUAL_TOLERANCE = 1e-8


# -- boundary conditions ------------------------------------------------------
#
# Regions are named with the *existing* `Selector` union. A second selector
# vocabulary for thermal would mean a bore that a restraint can name and a film
# cannot, and the whole point of `app/solve/selection.py` is that a region
# survives a re-mesh because it is described geometrically.


class FixedTemperature(BaseModel):
    """A region held at a known temperature — a Dirichlet condition.

    `temperature_k` is **absolute kelvin**, never celsius, and that is enforced
    only as far as it can be: `gt=0` refuses a negative celsius value but cannot
    tell 20 K from 20 °C. The whole module uses one scale so that a difference
    and an absolute value never have to be told apart at a call site.
    """

    type: Literal["fixed_temperature"] = "fixed_temperature"
    where: Selector
    temperature_k: float = Field(gt=0)
    name: str | None = None


class Convection(BaseModel):
    """A convection film on a surface — a Robin condition.

    The surface exchanges `h (T_ambient - T_surface)` watts per unit area with
    its surroundings, so heat flows in where the part is cold and out where it is
    hot without anyone having to say which. This is the condition that makes a
    thermal model an analysis: a part in still air, in a fan stream, or in oil
    differs only in `film_coefficient_w_m2k`, and nothing about the part is held
    at a temperature that somebody had to guess.

    Unlike a flux, this puts terms into the conductivity matrix as well as into
    the load vector — `integral h N_i N_j dA` — which is also why a film alone,
    with no fixed temperature anywhere, is a perfectly well-posed model.
    """

    type: Literal["convection"] = "convection"
    where: Selector
    film_coefficient_w_m2k: float = Field(gt=0)
    ambient_temperature_k: float = Field(gt=0)
    name: str | None = None


class HeatFlux(BaseModel):
    """A heat flux on a surface — a Neumann condition, W/m².

    **Positive puts heat into the part**, which is the sense a heater, a friction
    interface or an absorbed radiation load has. Negative takes it out.

    Distributed over the selected surface by the shape-function integrals, the
    same weighting `selection.distribute_force` uses for a mechanical traction:
    each corner of a linear face takes a third of its area, and on a quadratic
    face the corner functions integrate to zero and the whole of the load sits on
    the midside nodes. Refining the mesh therefore does not change the total heat
    applied, exactly as refining it does not change an applied force.

    A surface with no flux and no film is **insulated**, and it is insulated by
    saying nothing: a boundary term that is not written is zero, which is the
    natural condition of the weak form. That is a real modelling statement (a
    symmetry plane, a lagged pipe) rather than an omission, so there is no
    `Insulated` boundary type to write.
    """

    type: Literal["heat_flux"] = "heat_flux"
    where: Selector
    flux_w_m2: float
    name: str | None = None


ThermalBoundary = Annotated[
    FixedTemperature | Convection | HeatFlux,
    Field(discriminator="type"),
]


class ThermalCase(BaseModel):
    """What to solve for a steady-state conduction run.

    A sibling of `LoadCase` rather than a field on it, for the reason `ModalCase`
    is a sibling: the inputs are different (no fixtures, no forces, a
    conductivity instead of a modulus) and the output is a different quantity, so
    folding them together would invite a caller to believe the mechanical loads
    influenced the temperature.

    `material` is deliberately absent. Steady conduction needs conductivity and
    nothing else about the material — not the modulus, not Poisson's ratio, not
    the density, since a steady state has no stored heat — and requiring a full
    `Material` would suggest otherwise. The material reappears at the coupling
    seam below, where the temperature field becomes a thermal strain and the
    expansion coefficient and the modulus are what turn it into stress.
    """

    name: str = "Steady-state conduction"
    #: Isotropic thermal conductivity, W/(m·K). 51.9 for mild steel, 167 for
    #: aluminium, 0.2 for a typical unfilled polymer. See the module docstring
    #: for why it lives here and not on `Material`.
    conductivity_w_mk: float = Field(gt=0)
    boundaries: list[ThermalBoundary] = Field(min_length=1)
    #: Uniform internal heat generation, W/m³ — ohmic heating, cure exotherm,
    #: absorbed friction. Zero costs nothing: no source term is assembled.
    volumetric_source_w_m3: float = 0.0
    #: The temperature at which the part is unstrained, absolute kelvin. Used by
    #: the coupling helpers below and by nothing in the conduction solve itself,
    #: which is invariant to it.
    reference_temperature_k: float = Field(default=293.15, gt=0)


class ConductionResult(BaseModel):
    """Summary of a conduction run. The nodal field is large and stays out of the DB."""

    name: str
    min_temperature_k: float
    max_temperature_k: float
    min_temperature_node: int
    max_temperature_node: int
    #: Net heat the fixed-temperature regions supply to the part, W — positive in,
    #: negative out. It is `sum(K T - f)`, which is exactly zero on every free
    #: node and therefore collects only what had to be pushed through the
    #: prescribed regions to hold them where they were held.
    #:
    #: Three readings, all useful and none of them a pass/fail: with no fixed
    #: temperature anywhere it is zero, which is the statement that the films and
    #: fluxes balance; with a flux or a source and one held region it is minus the
    #: applied heat, because a steady state stores nothing; and with two held
    #: regions at different temperatures it is zero again, because what one
    #: supplies the other removes. Reported rather than asserted, because a
    #: surprising value is a diagnosis — a film on a region with no facets, a flux
    #: on interior nodes — and not always a failure.
    fixed_temperature_heat_w: float
    node_count: int
    element_count: int
    solve_seconds: float
    warnings: list[str] = Field(default_factory=list)
    #: Same reasoning as `StaticResult`: a single grid says nothing about its own
    #: discretisation error, and an absent field would read as one nobody needed.
    mesh_convergence: MeshConvergence = Field(default_factory=MeshConvergence)

    def summary(self) -> dict[str, Any]:
        return self.model_dump()


@dataclass
class ThermalField:
    """Full conduction output. `result` is the summary that gets persisted.

    Mirrors `SolveOutput`: the summary is small and the fields are large enough
    to belong in object storage.
    """

    result: ConductionResult
    #: (n_nodes,) absolute kelvin.
    temperatures_k: NDArray[np.float64] = field(repr=False)
    #: (n_elements, 3) W/m², the Fourier flux `-k grad T` at each element's
    #: centroid, converted back out of the mm system at the same boundary the
    #: inputs came in through. The centroid is the element's superconvergent
    #: point for a gradient, the same reason `linear_static` recovers stress
    #: there.
    heat_flux_w_m2: NDArray[np.float64] = field(repr=False)


# -- surface shape-function integrals ----------------------------------------
#
# The Robin and flux terms are integrals over a boundary triangle, and both need
# the same two things: `integral N_i dA` for the load and `integral N_i N_j dA`
# for the matrix. They are computed here from the exact barycentric monomial
# formula rather than from a quadrature table, because a recalled quadrature rule
# and a recalled mass matrix are the same kind of unverifiable constant, and this
# one can be checked by hand: `integral L1 dA = A/3`, `integral L1^2 dA = A/6`.

_Term = tuple[float, tuple[int, int, int]]
_Shape = tuple[_Term, ...]


def _barycentric_integral(a: int, b: int, c: int) -> float:
    """`integral L1^a L2^b L3^c dA` over a triangle of **unit area**.

    The standard result `2A a! b! c! / (a+b+c+2)!`, with A = 1. Checks: (0,0,0)
    gives 1, (1,0,0) gives 1/3, (2,0,0) gives 1/6, (1,1,0) gives 1/12.
    """
    return 2.0 * factorial(a) * factorial(b) * factorial(c) / factorial(a + b + c + 2)


#: The linear triangle: N_i = L_i.
_T3_SHAPES: Final[tuple[_Shape, ...]] = (
    ((1.0, (1, 0, 0)),),
    ((1.0, (0, 1, 0)),),
    ((1.0, (0, 0, 1)),),
)

#: The quadratic triangle, in the node order a tet10 boundary face arrives in:
#: the three corners of `TetMesh.surface_triangles`, then the three midsides of
#: `TetMesh.surface_midside_nodes`, whose entry i sits between corner i and
#: corner i+1. Written homogeneously — `N_corner = L(2L-1)` becomes
#: `L^2 - L*L' - L*L''` using `L1 + L2 + L3 = 1` — so every term is a degree-2
#: monomial and the integral formula above applies directly.
_T6_SHAPES: Final[tuple[_Shape, ...]] = (
    ((1.0, (2, 0, 0)), (-1.0, (1, 1, 0)), (-1.0, (1, 0, 1))),
    ((1.0, (0, 2, 0)), (-1.0, (1, 1, 0)), (-1.0, (0, 1, 1))),
    ((1.0, (0, 0, 2)), (-1.0, (1, 0, 1)), (-1.0, (0, 1, 1))),
    ((4.0, (1, 1, 0)),),
    ((4.0, (0, 1, 1)),),
    ((4.0, (1, 0, 1)),),
)


def _surface_integrals(shapes: Sequence[_Shape]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """`integral N_i dA` and `integral N_i N_j dA` over a unit-area triangle."""
    count = len(shapes)
    load = np.zeros(count, dtype=np.float64)
    mass = np.zeros((count, count), dtype=np.float64)
    for i, shape_i in enumerate(shapes):
        for coefficient, exponents in shape_i:
            load[i] += coefficient * _barycentric_integral(*exponents)
    for i, shape_i in enumerate(shapes):
        for j, shape_j in enumerate(shapes):
            for ci, ei in shape_i:
                for cj, ej in shape_j:
                    mass[i, j] += ci * cj * _barycentric_integral(
                        ei[0] + ej[0], ei[1] + ej[1], ei[2] + ej[2]
                    )
    return load, mass


_T3_LOAD_UNIT, _T3_MASS_UNIT = _surface_integrals(_T3_SHAPES)
_T6_LOAD_UNIT, _T6_MASS_UNIT = _surface_integrals(_T6_SHAPES)


def _tet10_shape_values(xi: float, eta: float, zeta: float) -> NDArray[np.float64]:
    """N for the 10-node tet at one natural point, shape (10,).

    The values whose gradients `linear_static._tet10_shape_gradients` returns,
    and built from the same `TET10_EDGES` table for the same reason: a mesh whose
    midside ordering changed must not leave this quietly describing the old one.
    """
    lam = np.array([1.0 - xi - eta - zeta, xi, eta, zeta], dtype=np.float64)
    values = np.zeros(10, dtype=np.float64)
    values[:4] = lam * (2.0 * lam - 1.0)
    for local, (a, b) in enumerate(TET10_EDGES, start=4):
        values[local] = 4.0 * lam[a] * lam[b]
    return values


# -- assembly -----------------------------------------------------------------


def _scatter_nodal(
    node_count: int, indices: NDArray[np.int64], blocks: NDArray[np.float64]
) -> sp.csr_matrix:
    """Sum per-entity matrices into a global (n_nodes, n_nodes) sparse matrix.

    One degree of freedom per node, so `indices` is the connectivity itself
    rather than `linear_static._element_dofs`' three-per-node expansion.
    """
    width = indices.shape[1]
    rows = np.repeat(indices, width, axis=1).ravel()
    cols = np.tile(indices, (1, width)).ravel()
    return sp.coo_matrix(
        (blocks.ravel(), (rows, cols)), shape=(node_count, node_count)
    ).tocsr()


def assemble_conductivity(mesh: TetMesh, conductivity_w_mmk: float) -> sp.csr_matrix:
    """Global conductivity matrix `integral k grad N . grad N dV`, W/K.

    Dispatches on what the mesh carries, the way `assemble_stiffness` does.
    `conductivity_w_mmk` is already in the mm system: this function is downstream
    of the unit boundary and does not convert.
    """
    if mesh.midside is not None:
        return _assemble_conductivity_tet10(mesh, conductivity_w_mmk)
    return _assemble_conductivity_tet4(mesh, conductivity_w_mmk)


def _assemble_conductivity_tet4(mesh: TetMesh, conductivity_w_mmk: float) -> sp.csr_matrix:
    grads, volumes = _shape_gradients(mesh)
    # Tet4 temperature is linear, so its gradient is constant over the element
    # and one evaluation integrates the product exactly.
    ke = conductivity_w_mmk * volumes[:, None, None] * np.einsum("eik,ejk->eij", grads, grads)
    return _scatter_nodal(mesh.node_count, mesh.tets, ke)


def _assemble_conductivity_tet10(mesh: TetMesh, conductivity_w_mmk: float) -> sp.csr_matrix:
    if mesh.midside is None:  # pragma: no cover - the caller dispatches on this
        raise SolverError("mesh has no midside nodes; it is not a tet10 mesh")

    connectivity = mesh.connectivity
    points = mesh.nodes[connectivity]
    ke = np.zeros((len(connectivity), 10, 10), dtype=np.float64)
    for point in _TET_GAUSS_POINTS:
        grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
        ke += (
            _TET_GAUSS_WEIGHT
            * conductivity_w_mmk
            * detj[:, None, None]
            * np.einsum("eik,ejk->eij", grads, grads)
        )
    return _scatter_nodal(mesh.node_count, connectivity, ke)


def volumetric_source_load(mesh: TetMesh, source_w_mm3: float) -> NDArray[np.float64]:
    """Equivalent nodal heat for a uniform internal source, W, shape (n_nodes,).

    `integral q_v N_i dV`, integrated the way the conductivity is: exactly for
    tet4, whose shape functions are linear, and with the four-point rule for
    tet10, which is exact for the quadratic integrand. The sum over all nodes is
    therefore `q_v * volume` to machine precision whatever the element order —
    the partition of unity — which is what a test asserts rather than a recorded
    per-node value.
    """
    heat = np.zeros(mesh.node_count, dtype=np.float64)
    if source_w_mm3 == 0.0:
        return heat

    if mesh.midside is None:
        _, volumes = _shape_gradients(mesh)
        local = np.repeat((source_w_mm3 * volumes / 4.0)[:, None], 4, axis=1)
        np.add.at(heat, mesh.tets.ravel(), local.ravel())
        return heat

    connectivity = mesh.connectivity
    points = mesh.nodes[connectivity]
    local = np.zeros((len(connectivity), 10), dtype=np.float64)
    for point in _TET_GAUSS_POINTS:
        _, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
        local += (
            _TET_GAUSS_WEIGHT * source_w_mm3 * detj[:, None] * _tet10_shape_values(*point)[None, :]
        )
    np.add.at(heat, connectivity.ravel(), local.ravel())
    return heat


def _facet_integrals(
    mesh: TetMesh, nodes: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Boundary facets fully inside `nodes`, and their shape-function integrals.

    Returns `(carriers, areas, load_unit, mass_unit)` where `carriers` is
    (n_facets, 3) for tet4 or (n_facets, 6) for tet10 — corners then midsides, in
    the order `_T6_SHAPES` is written for.

    The facet set comes from `selection._boundary_faces_within`, so a thermal
    region and a mechanical region resolve identically: a film and a pressure
    named with the same selector act on exactly the same triangles.
    """
    triangles, midside = _boundary_faces_within(mesh, nodes)
    corners = mesh.nodes[triangles]
    areas = 0.5 * np.linalg.norm(
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]), axis=1
    )
    if midside is None:
        return triangles, areas, _T3_LOAD_UNIT, _T3_MASS_UNIT
    return np.hstack([triangles, midside]), areas, _T6_LOAD_UNIT, _T6_MASS_UNIT


# -- refusals -----------------------------------------------------------------


def _thermally_floating(detail: str) -> SolverError:
    return SolverError(
        "The model is thermally floating: nothing sets its temperature level "
        f"({detail}), so a steady conduction solve determines the temperature only "
        "up to an arbitrary constant. Add a fixed_temperature boundary on a region "
        "whose temperature you know, or a convection boundary with a film "
        "coefficient and an ambient temperature."
    )


def _heat_balance(
    k_ff: sp.spmatrix, solution: NDArray[np.float64], applied: NDArray[np.float64]
) -> bool:
    """Whether the solution actually satisfies the assembled heat balance.

    The counterpart of `linear_static._residual_is_small`, and it differs in one
    place on purpose: the scale is the larger of the applied heat and the heat
    the solution claims to carry, not the applied heat alone. A floating model
    reached with zero applied heat — every boundary insulated, no source — has an
    applied norm of zero, and a tolerance relative to zero accepts anything. A
    genuinely zero solution still passes, because then both norms are zero.

    **That difference is unpinned, and this says so rather than implying it was
    verified.** Breaking it back to the applied-only scale makes no test in
    `tests/test_conduction.py` fail, because the two structural refusals above
    reach every floating model this vocabulary can express *before* the solve
    does — a model with a fixed temperature or a contributing film has a
    non-singular matrix, and one with neither never gets here. It is kept as the
    correct form for a boundary condition that does not exist yet (a periodic
    pair, a multi-point constraint), which could be singular with zero applied
    heat. The residual check itself is pinned: `test_a_film_that_selected_no_facets_is_refused_not_solved`
    only reaches a refusal at all because a singular system is caught.
    """
    carried = k_ff @ solution
    scale = max(float(np.linalg.norm(applied)), float(np.linalg.norm(carried)))
    if scale == 0.0:
        return True
    return bool(float(np.linalg.norm(carried - applied)) <= _RESIDUAL_TOLERANCE * scale)


# -- the solver ---------------------------------------------------------------


class SteadyConductionSolver(ConductionSolver):
    """Steady-state heat conduction, in-house, tet4 and tet10.

    The implementation behind the `ConductionSolver` ABC in `app/solve/base.py`,
    which this class was written against before that ABC existed — the seam it
    asked for in its own docstring, closed on 2026-09-09.

    **Renamed from `ConductionSolver`** in the same change, because the ABC
    wanted that name: the register in `base.py` names an *analysis* (`Solver`,
    `ModalSolver`, `PlanarSolver`, `ConductionSolver`) and an implementation
    names *how it answers* (`LinearStaticSolver`, `ModalEigenSolver`,
    `PlaneSolver`, and now `SteadyConductionSolver`). `name` was already
    `"steady-conduction"`, so the class name and the recorded name now say the
    same thing — which matters because `name` is what a result is bound to.
    """

    name = "steady-conduction"

    def solve(self, mesh: TetMesh, case: ThermalCase) -> ThermalField:
        started = time.perf_counter()
        warnings: list[str] = []

        # The unit boundary. Everything below this point is mm-W-K.
        conductivity = case.conductivity_w_mk * CONDUCTIVITY_W_MK_TO_W_MMK
        source = case.volumetric_source_w_m3 * SOURCE_W_M3_TO_W_MM3

        fixed_boundaries = [b for b in case.boundaries if isinstance(b, FixedTemperature)]
        films = [b for b in case.boundaries if isinstance(b, Convection)]
        if not fixed_boundaries and not films:
            raise _thermally_floating(
                "every boundary is insulated or carries only a heat flux"
            )

        # Assembly and factorisation get separate stages inside one span, for
        # the reason `linear_static` gives: a slow assembly and a slow solve have
        # different fixes, and a single undifferentiated duration cannot tell
        # them apart.
        #
        # `degrees_of_freedom` equals `nodes` here — steady conduction carries
        # one temperature per node where a structural run carries three
        # displacements — and it is reported anyway rather than left implicit,
        # because it is the field that makes two analyses comparable. A
        # 300k-node conduction solve and a 100k-node static solve factorise
        # matrices of the same order, and a report that only had node counts
        # would show that as unexplained variance.
        with observe.span(
            "solve.conduction",
            nodes=mesh.node_count,
            elements=mesh.tet_count,
            degrees_of_freedom=int(mesh.node_count),
        ) as timing:
            timing.set("stage", "assemble")
            conductance = assemble_conductivity(mesh, conductivity)
            heat = volumetric_source_load(mesh, source)

            heat += self._flux_load(mesh, case, warnings)
            film_matrix, film_heat = self._convection_terms(mesh, films, warnings)
            conductance = (conductance + film_matrix).tocsr()
            heat += film_heat

            fixed, prescribed = self._prescribed(mesh, fixed_boundaries)
            if len(fixed) == 0 and film_matrix.nnz == 0:
                # A *declared* film is not an assembled one: every film here
                # landed on a region with no complete surface facets, so nothing
                # sets the level after all. Caught here rather than left to the
                # factorisation, because a numerically-singular matrix is
                # sometimes factored with a tiny pivot and no warning, and the
                # resulting garbage then satisfies a heat balance whose applied
                # heat is also zero.
                raise _thermally_floating(
                    "the only boundaries that could set it are convection films that "
                    "matched no surface facets"
                )
            free = np.setdiff1d(np.arange(mesh.node_count), fixed)
            if len(free) == 0:
                raise SolverError(
                    "Every node's temperature is prescribed, so there is nothing to solve. "
                    "Leave at least one region free, or read the boundary values directly."
                )

            k_ff = conductance[free][:, free].tocsc()
            k_ff.eliminate_zeros()
            applied = heat[free]
            if len(fixed) > 0:
                # Move the known temperatures to the right-hand side rather than
                # zeroing rows: the matrix stays symmetric, so CG still applies.
                applied = applied - conductance[free][:, fixed] @ prescribed

            timing.set("stage", "factorise")
            if mesh.node_count > _ITERATIVE_THRESHOLD_NODES:
                timing.set("method", "iterative")
                solution = self._solve_iterative(k_ff, applied)
            else:
                timing.set("method", "direct")
                solution = self._solve_direct(k_ff, applied)

        if not np.all(np.isfinite(solution)) or not _heat_balance(k_ff, solution, applied):
            raise _thermally_floating("the temperature field does not satisfy the heat balance")

        temperatures = np.zeros(mesh.node_count, dtype=np.float64)
        temperatures[free] = solution
        if len(fixed) > 0:
            temperatures[fixed] = prescribed

        flux = self.heat_flux(mesh, temperatures, conductivity)
        # What the whole assembled system says is left over once the field is
        # known: the heat that had to cross the prescribed-temperature regions.
        supplied = float(np.sum(conductance @ temperatures - heat))
        result = ConductionResult(
            name=case.name,
            min_temperature_k=float(temperatures.min()),
            max_temperature_k=float(temperatures.max()),
            min_temperature_node=int(np.argmin(temperatures)),
            max_temperature_node=int(np.argmax(temperatures)),
            fixed_temperature_heat_w=supplied,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return ThermalField(
            result=result,
            temperatures_k=temperatures,
            heat_flux_w_m2=flux / FLUX_W_M2_TO_W_MM2,
        )

    # -- boundary assembly ----------------------------------------------------

    @staticmethod
    def _prescribed(
        mesh: TetMesh, boundaries: Sequence[FixedTemperature]
    ) -> tuple[NDArray[np.int64], NDArray[np.float64]]:
        """The fixed nodes and their temperatures.

        Two boundaries that name the same node with **different** temperatures
        are refused rather than resolved by ordering. Selectors carry tolerance
        bands, so an overlap is easy to write by accident, and silently keeping
        whichever was listed last would put a discontinuity in the boundary data
        that the solution then smooths into something plausible.
        """
        values: dict[int, tuple[float, str]] = {}
        for index, boundary in enumerate(boundaries):
            label = boundary.name or f"fixed_temperature[{index}]"
            for node in select_nodes(mesh, boundary.where):
                previous = values.get(int(node))
                if previous is not None and previous[0] != boundary.temperature_k:
                    raise SolverError(
                        f"{previous[1]!r} holds a node at {previous[0]} K and {label!r} holds "
                        f"the same node at {boundary.temperature_k} K. Two boundaries cannot "
                        "prescribe one node twice; tighten one selector's tolerance so the "
                        "regions do not overlap."
                    )
                values[int(node)] = (boundary.temperature_k, label)

        nodes = np.array(sorted(values), dtype=np.int64)
        return nodes, np.array([values[int(n)][0] for n in nodes], dtype=np.float64)

    @staticmethod
    def _flux_load(mesh: TetMesh, case: ThermalCase, warnings: list[str]) -> NDArray[np.float64]:
        """Nodal heat from every `HeatFlux` boundary, W, shape (n_nodes,)."""
        heat = np.zeros(mesh.node_count, dtype=np.float64)
        for index, boundary in enumerate(case.boundaries):
            if not isinstance(boundary, HeatFlux):
                continue
            label = boundary.name or f"heat_flux[{index}]"
            nodes = select_nodes(mesh, boundary.where)
            carriers, areas, load_unit, _ = _facet_integrals(mesh, nodes)
            if len(carriers) == 0:
                # No equal-split fallback, unlike `distribute_force`. A flux is
                # defined per unit area; with no facets there is no area to
                # spread it over, so an equal split would invent a total that
                # depends on how many nodes the selector happened to catch.
                warnings.append(
                    f"{label!r} selected no complete surface facets, so it applied no heat. "
                    "A heat flux needs a surface: name a face or a wall, not interior nodes."
                )
                continue
            flux = boundary.flux_w_m2 * FLUX_W_M2_TO_W_MM2
            local = flux * areas[:, None] * load_unit[None, :]
            np.add.at(heat, carriers.ravel(), local.ravel())
        return heat

    @staticmethod
    def _convection_terms(
        mesh: TetMesh, films: Sequence[Convection], warnings: list[str]
    ) -> tuple[sp.csr_matrix, NDArray[np.float64]]:
        """The Robin matrix `integral h N_i N_j dA` and its load `integral h T_inf N_i dA`.

        Both halves or neither: a film that contributed a load without its
        matrix term would heat the part towards ambient with nothing stopping it,
        which is a runaway rather than an equilibrium.
        """
        matrix = sp.csr_matrix((mesh.node_count, mesh.node_count), dtype=np.float64)
        heat = np.zeros(mesh.node_count, dtype=np.float64)
        for index, film in enumerate(films):
            label = film.name or f"convection[{index}]"
            nodes = select_nodes(mesh, film.where)
            carriers, areas, load_unit, mass_unit = _facet_integrals(mesh, nodes)
            if len(carriers) == 0:
                warnings.append(
                    f"{label!r} selected no complete surface facets, so it exchanged no heat. "
                    "A convection film needs a surface: name a face or a wall, not interior nodes."
                )
                continue
            h = film.film_coefficient_w_m2k * FILM_W_M2K_TO_W_MM2K
            matrix = matrix + _scatter_nodal(
                mesh.node_count, carriers, h * areas[:, None, None] * mass_unit[None, :, :]
            )
            local = h * film.ambient_temperature_k * areas[:, None] * load_unit[None, :]
            np.add.at(heat, carriers.ravel(), local.ravel())
        return matrix, heat

    # -- linear solve ---------------------------------------------------------

    @staticmethod
    def _solve_direct(k_ff: sp.csc_matrix, applied: NDArray[np.float64]) -> NDArray[np.float64]:
        try:
            with warnings_module.catch_warnings():
                # A singular conductance raises MatrixRankWarning rather than failing.
                warnings_module.simplefilter("error", spla.MatrixRankWarning)
                solution = spla.spsolve(k_ff, applied, permc_spec="COLAMD", use_umfpack=False)
        except (RuntimeError, spla.MatrixRankWarning) as exc:
            raise _thermally_floating(str(exc)) from exc
        return np.asarray(solution, dtype=np.float64)

    @staticmethod
    def _solve_iterative(k_ff: sp.csc_matrix, applied: NDArray[np.float64]) -> NDArray[np.float64]:
        """Jacobi-preconditioned CG, for the same reasons `linear_static` gives.

        The conductance matrix restricted to the free nodes is SPD once anything
        sets the temperature level, so its diagonal is strictly positive and the
        inverse diagonal is an SPD preconditioner. An ILU is not: SuperLU's ILUTP
        pivots, and the resulting operator is not symmetric, which breaks CG's
        convergence proof — measured on the structural problem and the same
        arithmetic here.
        """
        diagonal = k_ff.diagonal()
        if not np.all(diagonal > 0.0):
            raise _thermally_floating("the conductance matrix is not positive definite")
        inverse_diagonal = 1.0 / diagonal
        precond = spla.LinearOperator(
            k_ff.shape, matvec=lambda x: inverse_diagonal * x, dtype=np.float64
        )
        solution, info = spla.cg(k_ff, applied, rtol=_RESIDUAL_TOLERANCE, maxiter=5000, M=precond)
        if info != 0:
            raise SolverError(
                f"Conjugate gradient failed to converge on the temperature field (info={info}). "
                "The model may be thermally floating, or the mesh may need refining."
            )
        return np.asarray(solution, dtype=np.float64)

    # -- post-processing ------------------------------------------------------

    @staticmethod
    def heat_flux(
        mesh: TetMesh, temperatures_k: NDArray[np.float64], conductivity_w_mmk: float
    ) -> NDArray[np.float64]:
        """Fourier flux `-k grad T` per element at its centroid, W/mm², (n_elem, 3).

        In the mm system: this is below the unit boundary and `solve` converts the
        answer back out. A uniform temperature field has zero gradient and
        therefore exactly zero flux, whatever the mesh — the cheapest statement
        that the gradient operator is assembled correctly.
        """
        if mesh.midside is None:
            grads, _ = _shape_gradients(mesh)
        else:
            grads, _ = _mapped_gradients(
                mesh.nodes[mesh.connectivity], _tet10_shape_gradients(*_CENTROID)
            )
        element_t = temperatures_k[mesh.connectivity]
        gradient = np.einsum("eij,ei->ej", grads, element_t)
        return np.asarray(-conductivity_w_mmk * gradient, dtype=np.float64)


# -- coupling to stress -------------------------------------------------------
#
# The payoff. `LoadCase.delta_t_k` is one number for the whole part, and
# `LinearStaticSolver` reads it in two places: `thermal.thermal_load` builds the
# equivalent nodal force, and `_stress_at` subtracts `thermal_stress_correction`
# during recovery. Everything below is the same pair of operations for a
# *computed field* instead of a constant, written so that a uniform field
# reproduces the uniform path bit for bit.
#
# **The seam, precisely.** Nothing here is reachable from `LinearStaticSolver`
# yet, and making it so is a change to two files this module does not own:
#
#   1. `app/solve/types.py` — `LoadCase` needs a way to carry a field. The
#      smallest honest shape is a sibling of `delta_t_k` holding a reference to a
#      stored nodal temperature array (a media blob id, since a field is too
#      large for the DB row) plus the reference temperature, with a validator
#      refusing both at once: a case carrying a uniform change *and* a field is
#      two answers to one question.
#   2. `app/solve/linear_static.py` — three edits. `solve` calls
#      `thermal_load_from_field` instead of `thermal.thermal_load` when the field
#      is present; `_stress_at` subtracts a per-element correction, so its
#      `delta_t_k: float | None` parameter widens to accept an (n_elem,) array
#      and the subtraction becomes `stress - correction` with correction already
#      shaped (n_elem, 6) — which is what `thermal_stress_correction_field`
#      returns and why it returns that rather than a single row; and
#      `_recover_nodal_stress` passes the same array through unchanged.
#
# Neither edit changes any existing answer: a `None` field takes exactly the
# path it takes today. Until they are made, the functions below are verified
# against the uniform case and are not wired to anything.


def element_temperature_change(
    mesh: TetMesh, temperatures_k: NDArray[np.float64], reference_temperature_k: float
) -> NDArray[np.float64]:
    """Each element's temperature change from the reference, K, shape (n_elem,).

    Sampled at the centroid — `linear_static._CENTROID`, the same point
    `_recover_stress` evaluates stress at, so the thermal correction and the
    mechanical stress it is subtracted from describe the same place in the
    element. For tet4 that is the mean of the four corner values, which is also
    the element's exact mean.
    """
    if mesh.midside is None:
        element_t = temperatures_k[mesh.tets].mean(axis=1)
    else:
        element_t = temperatures_k[mesh.connectivity] @ _tet10_shape_values(*_CENTROID)
    return np.asarray(element_t - reference_temperature_k, dtype=np.float64)


def thermal_load_from_field(
    mesh: TetMesh,
    material: Material,
    temperatures_k: NDArray[np.float64],
    reference_temperature_k: float,
) -> NDArray[np.float64]:
    """Equivalent nodal forces for a computed temperature field, shape (3 * n_nodes,).

    `thermal.thermal_load` with the constant `delta_t_k` replaced by the field's
    own value at each integration point, and integrated the same way — one
    evaluation for tet4, whose strain is constant, and the four-point rule for
    tet10. A field that happens to be uniform therefore returns exactly what
    `thermal_load` returns for the same change, which is what pins this against
    the closed form `sigma = -E alpha dT` without re-deriving it.

    **This and `thermal.thermal_load` are two paths and not a duplicate**, and
    the difference is where the field is sampled. `thermal.thermal_load` takes
    one value per *element* — right for a temperature that was prescribed as a
    formula and collapsed to the element, which is what NAFEMS LE11 wants — and
    this one takes the *nodal* field a conduction solve produces and reads it at
    each Gauss point, which is strictly more accurate on tet10 because the field
    varies inside the element. Prefer this one whenever the nodal values exist.

    Missing `thermal_expansion_per_k` is refused by name by `thermal_strain`,
    the same refusal the uniform path gives.
    """
    unit_strain = thermal_strain(material, 1.0)  # alpha in Voigt form
    unit_stress = constitutive_matrix(material) @ unit_strain

    connectivity = mesh.connectivity
    forces = np.zeros(3 * mesh.node_count, dtype=np.float64)
    nodal_change = temperatures_k - reference_temperature_k

    if mesh.midside is None:
        grads, volumes = _shape_gradients(mesh)
        b = _strain_displacement(grads)
        delta_t = nodal_change[mesh.tets].mean(axis=1)
        stress = delta_t[:, None] * unit_stress[None, :]
        local = volumes[:, None] * np.einsum("eij,ei->ej", b, stress)
    else:
        points = mesh.nodes[connectivity]
        element_change = nodal_change[connectivity]
        local = np.zeros((len(connectivity), 3 * connectivity.shape[1]), dtype=np.float64)
        for point in _TET_GAUSS_POINTS:
            grads, detj = _mapped_gradients(points, _tet10_shape_gradients(*point))
            b = _strain_displacement(grads)
            delta_t = element_change @ _tet10_shape_values(*point)
            stress = delta_t[:, None] * unit_stress[None, :]
            local += _TET_GAUSS_WEIGHT * detj[:, None] * np.einsum("eij,ei->ej", b, stress)

    np.add.at(forces, _element_dofs(connectivity).ravel(), local.ravel())
    return forces


def thermal_stress_correction_field(
    material: Material, delta_t_k: NDArray[np.float64]
) -> NDArray[np.float64]:
    """`D * epsilon_thermal` per element, shape (n_elem, 6).

    **A delegation, not a second implementation.** `thermal.thermal_stress_correction`
    takes a per-element array as readily as a scalar, so this is the same
    formula under a name that says which shape you are asking for — kept because
    the call site reads better for it, and because a reader following the field
    path should land in `thermal.py` rather than find the constitutive
    arithmetic written out twice.

    Returned per element because that is the shape the recovery step needs: the
    whole difference between a field and a constant is that this number is no
    longer the same everywhere.
    """
    return thermal_stress_correction(material, np.asarray(delta_t_k, dtype=np.float64))


def convecting_bar_tip_temperature_k(
    base_temperature_k: float,
    ambient_temperature_k: float,
    conductivity_w_mk: float,
    film_coefficient_w_m2k: float,
    length_mm: float,
) -> float:
    """Closed form for a laterally insulated bar, held at one end and convecting
    at the other.

    Steady 1-D conduction with no source makes `T` linear, so the whole problem
    is the flux match at the tip, `-k dT/dx = h (T_tip - T_inf)`:

        T_tip = (T_base + Bi T_inf) / (1 + Bi),    Bi = h L / k

    `Bi` is the Biot number and it is dimensionless, which is what forces the
    unit conversions to agree with each other: `h` in W/(m²·K), `k` in W/(m·K)
    and `L` in **metres**, so the millimetre length converts here and only here.
    A film coefficient scaled with the wrong power of ten moves `Bi` and moves
    this number, which a temperature difference alone would not reveal.

    Here rather than in the tests, for the reason `thermal.restrained_bar_stress_mpa`
    is: the expected physics belongs next to the implementation it checks.
    """
    biot = film_coefficient_w_m2k * (length_mm * 1e-3) / conductivity_w_mk
    return (base_temperature_k + biot * ambient_temperature_k) / (1.0 + biot)


def hollow_cylinder_temperature_k(
    radius_mm: NDArray[np.float64] | float,
    inner_radius_mm: float,
    outer_radius_mm: float,
    inner_temperature_k: float,
    outer_temperature_k: float,
) -> NDArray[np.float64]:
    """Closed form for radial conduction through a tube wall:

        T(r) = Ti + (To - Ti) ln(r / ri) / ln(ro / ri)

    Logarithmic, not linear, because the area heat crosses grows with radius.
    Conductivity does not appear: it sets how much heat flows, not the shape of
    the profile, which is the cheapest available check that the conductivity
    multiplies the whole matrix and nothing else.
    """
    r = np.asarray(radius_mm, dtype=np.float64)
    ratio = np.log(r / inner_radius_mm) / np.log(outer_radius_mm / inner_radius_mm)
    return np.asarray(inner_temperature_k + (outer_temperature_k - inner_temperature_k) * ratio)
