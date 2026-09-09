"""Plane-stress and plane-strain elements on 3-node and 6-node triangles.

A plane model is not a thin solid, and the two idealisations it can carry are
not the same physics. **Plane stress** says the out-of-plane stress is zero and
the material contracts freely through the thickness: a flat plate loaded in its
own plane. **Plane strain** says the out-of-plane *strain* is zero because
something long holds it: a dam, a long bore, a slice of an extrusion. They share
every in-plane term and differ in one 3x3 matrix and in one number reported back
-- SZZ -- and getting that number wrong produces a plausible answer rather than
an error, which is why it is pinned by name in `tests/test_solver_plane.py`.

`PlaneCase` reuses the existing region vocabulary unchanged: the same `Fixture`,
the same `Selector` union, the same `Load` union. That reuse is the whole reason
`app.mesh.planar.TriMesh` carries a zero z column -- Decision 2 says the load and
selector vocabulary is the asset, and a second copy of it for two dimensions
would be the thing to avoid rather than the thing to build.

What the mesh has no room for is the third displacement component, and a plane
model has no out-of-plane degree of freedom to restrain. A fixture that names
`z` is therefore **refused by name**, not quietly dropped: `dofs=["z"]` on a
plane model is somebody carrying a 3-D symmetry plane across, and silently
ignoring it would leave them with a model that is free in a direction they
believe they held. The one exception is `kind="clamp"`, which does not name `z`
at all -- it names "built in", and on a plane model that is x and y.

Element orders follow `linear_static`'s convention: one class covers both, and
`solve` dispatches on what the mesh carries. Tri3 is constant-strain and stiff
in bending for the same reason tet4 is; tri6 has a linear strain field and is
what a stress benchmark should be run on.
"""

from __future__ import annotations

import time
import warnings as warnings_module
from abc import ABC, abstractmethod
from enum import StrEnum

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from app import observe
from app.mesh.planar import TRI6_EDGES, TriMesh
from app.solve.base import SolveOutput
from app.solve.linear_static import von_mises
from app.solve.selection import select_nodes
from app.solve.types import (
    Fixture,
    ForceLoad,
    Load,
    Material,
    PressureLoad,
    SolverError,
    StaticResult,
)

#: Two degrees of freedom per node, x then y. The whole module is written
#: against this number rather than against a literal 2, so the one place a
#: reader has to check "is this the 3-D one?" is here.
_DOF_PER_NODE = 2

_AXIS_INDEX = {"x": 0, "y": 1}

# Derivatives of the tri3 shape functions with respect to the natural
# coordinates, where L = (1 - xi - eta, xi, eta).
_DL_DNAT = np.array([[-1.0, -1.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float64)

# Three-point Gauss rule on the reference triangle, exact to degree 2.
#
# The tri6 stiffness integrand is B^T D B |det J|. For a straight-sided tri6 --
# midside nodes on the chords, which is what a mesher produces unless it is
# deliberately curving the boundary -- the mapping is affine, so |det J| is
# constant and dN/dx is linear in (xi, eta). The integrand is therefore a
# polynomial of **degree 2**, and this rule integrates degree 2 exactly. It is
# the same statement `linear_static` makes about the four-point tet rule, and
# the same caveat applies from the other side: it is a *quadrature* choice
# justified by the polynomial degree, not a tolerance. The tet10 mass matrix in
# `app/solve/modal.py` is the counter-example worth remembering -- its integrand
# N^T N is quartic, so the stiffness rule is not good enough for it and it is
# integrated analytically instead.
#
# A genuinely curved-sided tri6 (a midside node pulled onto an arc) has a
# non-constant Jacobian and its integrand is a rational function, so no fixed
# rule is exact. The error is O(h^2) in the geometry, which is the same order as
# the element itself, so the rule is kept rather than raised: a higher-order
# rule would integrate the wrong integrand more precisely.
_TRI_GAUSS_POINTS: tuple[tuple[float, float], ...] = (
    (1.0 / 6.0, 1.0 / 6.0),
    (2.0 / 3.0, 1.0 / 6.0),
    (1.0 / 6.0, 2.0 / 3.0),
)
#: The three equal weights sum to the reference triangle's area, 1/2.
_TRI_GAUSS_WEIGHT = 1.0 / 6.0

_CENTROID = (1.0 / 3.0, 1.0 / 3.0)

#: Where each of a tri6's six nodes sits in natural coordinates, in the local
#: order `TriMesh.connectivity` uses: the three corners, then the midsides in
#: `TRI6_EDGES` order. Derived from that table rather than typed out, for the
#: reason `_TET10_NATURAL_NODES` gives: a mesh whose midside ordering changed
#: would otherwise leave this describing the old one, and the stress would be
#: evaluated at the wrong point of the right element -- a plausible wrong number
#: and not an error.
_TRI6_CORNERS_NATURAL: tuple[tuple[float, float], ...] = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))


def _midside_natural(a: int, b: int) -> tuple[float, float]:
    first, second = _TRI6_CORNERS_NATURAL[a], _TRI6_CORNERS_NATURAL[b]
    return (0.5 * (first[0] + second[0]), 0.5 * (first[1] + second[1]))


_TRI6_NATURAL_NODES: tuple[tuple[float, float], ...] = (
    *_TRI6_CORNERS_NATURAL,
    *(_midside_natural(a, b) for a, b in TRI6_EDGES),
)

#: Twice the area below which a triangle is treated as degenerate, in mm^2.
_MIN_JACOBIAN = 1e-12

# Above this DOF count, use CG with a Jacobi preconditioner instead of a direct
# factorisation. Same threshold and same reasoning as `linear_static`.
_ITERATIVE_THRESHOLD_DOF = 100_000

# Equilibrium residual accepted as "solved", relative to the applied load.
_RESIDUAL_TOLERANCE = 1e-8


class PlaneState(StrEnum):
    """Which two-dimensional idealisation a plane model is posed under."""

    #: Out-of-plane stress is zero; the material contracts freely through the
    #: thickness. A flat plate loaded in its own plane.
    STRESS = "stress"
    #: Out-of-plane strain is zero; something long holds the material. A slice
    #: of a dam, a long bore, an extrusion.
    STRAIN = "strain"


class PlaneCase(BaseModel):
    """What to solve for a plane-stress or plane-strain run.

    The mirror of `LoadCase`, and deliberately the *same* fixtures and loads:
    a plane model restrains and loads its regions with the vocabulary every
    other analysis uses. Two things it adds.

    `thickness_mm` is the **out-of-plane thickness**, and it is required in both
    states. For plane stress it is the plate's real thickness and every stress
    depends on it. For plane strain the convention is a unit slice, 1 mm, and it
    is still carried explicitly rather than assumed: a reaction, an applied
    total force and a mass are all *per what they were integrated over*, and a
    number reported in newtons with an implicit and unstated slice depth is a
    number nobody can check. Naming it costs one field and makes "5 kN on this
    edge" mean something.

    `delta_t_k` is a uniform temperature change, as on `LoadCase`. The thermal
    strain differs between the two states -- plane strain carries a `(1 + nu)`
    factor because the out-of-plane restraint feeds back in-plane -- so it is
    resolved here rather than borrowed from the 3-D thermal module.
    """

    name: str = "Plane load case"
    material: Material
    #: Out-of-plane thickness in mm. See the class docstring: for plane strain
    #: this is the slice depth, conventionally 1 mm, and it is never implicit.
    thickness_mm: float = Field(gt=0)
    state: PlaneState
    fixtures: list[Fixture] = Field(min_length=1)
    loads: list[Load] = Field(min_length=1)
    #: Uniform temperature change over the whole part, in kelvin. None is the
    #: isothermal case and costs nothing. A value needs
    #: `thermal_expansion_per_k` on the material, and the solver says so by name
    #: rather than assuming one.
    delta_t_k: float | None = Field(default=None, ge=-2000.0, le=5000.0)


# -- constitutive ------------------------------------------------------------


def plane_stress_matrix(material: Material) -> NDArray[np.float64]:
    """Plane-stress elasticity matrix, Voigt order [xx, yy, xy].

        D = E / (1 - v^2) * [[1, v, 0], [v, 1, 0], [0, 0, (1 - v) / 2]]

    The shear term is `(1 - v) / 2` rather than `1 / 2` because the factor
    outside is `E / (1 - v^2)`: multiplied out it is `E / (2 (1 + v))`, which is
    G. `tests/test_solver_plane.py` recovers G from a solved pure-shear square
    rather than from this expression, so a typo here cannot agree with itself.
    """
    e = material.youngs_modulus_mpa
    nu = material.poissons_ratio
    factor = e / (1.0 - nu * nu)
    return factor * np.array(
        [[1.0, nu, 0.0], [nu, 1.0, 0.0], [0.0, 0.0, 0.5 * (1.0 - nu)]], dtype=np.float64
    )


def plane_strain_matrix(material: Material) -> NDArray[np.float64]:
    """Plane-strain elasticity matrix, Voigt order [xx, yy, xy].

        D = E / ((1 + v)(1 - 2v))
            * [[1 - v, v, 0], [v, 1 - v, 0], [0, 0, (1 - 2v) / 2]]

    The shear term multiplies out to `E / (2 (1 + v))` = G, the same as plane
    stress: shear in the plane does not care what the out-of-plane condition is.
    Only the two normal terms differ.
    """
    e = material.youngs_modulus_mpa
    nu = material.poissons_ratio
    factor = e / ((1.0 + nu) * (1.0 - 2.0 * nu))
    return factor * np.array(
        [
            [1.0 - nu, nu, 0.0],
            [nu, 1.0 - nu, 0.0],
            [0.0, 0.0, 0.5 * (1.0 - 2.0 * nu)],
        ],
        dtype=np.float64,
    )


def constitutive_matrix(material: Material, state: PlaneState) -> NDArray[np.float64]:
    """The 3x3 elasticity matrix for whichever idealisation is asked for."""
    if state is PlaneState.STRESS:
        return plane_stress_matrix(material)
    return plane_strain_matrix(material)


def thermal_strain(
    material: Material, state: PlaneState, delta_t_k: float
) -> NDArray[np.float64]:
    """In-plane thermal strain, Voigt order [xx, yy, xy].

    Plane stress is the obvious `alpha dT` on both normal components. Plane
    strain carries an extra `(1 + v)`: the out-of-plane expansion is restrained,
    that restraint puts the material into out-of-plane compression, and Poisson
    feeds it straight back into the plane. Dropping the factor understates a
    restrained thermal stress by 30% for steel, which is the sort of error that
    reads as a mesh being slightly coarse.
    """
    alpha = material.thermal_expansion_per_k
    if alpha is None:
        raise SolverError(
            f"{material.name!r} has no coefficient of thermal expansion, so its thermal "
            "stress cannot be computed. Set thermal_expansion_per_k on the material "
            "(per kelvin -- 23.6e-6 for aluminium)."
        )
    magnitude = float(alpha) * float(delta_t_k)
    if state is PlaneState.STRAIN:
        magnitude *= 1.0 + material.poissons_ratio
    return np.array([magnitude, magnitude, 0.0], dtype=np.float64)


# -- element machinery -------------------------------------------------------


def _winding(mesh: TriMesh) -> float:
    """+1 if every triangle is wound anticlockwise, -1 if every one is clockwise.

    Refuses a degenerate or mixed-winding mesh, and it has to: the outward normal
    of a boundary edge is recovered from that edge's winding, so a mesh whose
    triangles disagree has no consistent answer to "which way is out" and a
    pressure applied to it would push into the material along part of a face and
    out of it along the rest. Nothing downstream would notice.
    """
    areas = mesh.signed_areas()
    degenerate = np.abs(areas) < _MIN_JACOBIAN
    if degenerate.any():
        raise SolverError(
            f"{int(degenerate.sum())} element(s) have zero area; the mesh is degenerate. "
            "Re-mesh with a smaller element size, or check the boundary for a duplicated point."
        )
    if bool(np.all(areas > 0.0)):
        return 1.0
    if bool(np.all(areas < 0.0)):
        return -1.0
    raise SolverError(
        "The mesh mixes clockwise and anticlockwise triangles, so a boundary edge has no "
        "consistent outward direction and a pressure cannot be applied to it. Re-mesh, or "
        "flip the inverted triangles so every one is wound the same way."
    )


def _tri3_shape_gradients(mesh: TriMesh) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Per-element dN/dx, shape (n_tris, 3, 2), and area, shape (n_tris,).

    Closed form: a linear triangle's shape-function gradients are the opposite
    edge vectors over twice the signed area, so there is nothing to invert and
    nothing to integrate. Strain is constant over the element, which is exactly
    why tri3 is stiff in bending.
    """
    p = mesh.nodes[mesh.tris][:, :, :2]  # (n_tris, 3, 2)
    x, y = p[:, :, 0], p[:, :, 1]
    twice_area = 2.0 * mesh.signed_areas()

    grads = np.empty((len(mesh.tris), 3, 2), dtype=np.float64)
    for node in range(3):
        j, k = (node + 1) % 3, (node + 2) % 3
        grads[:, node, 0] = (y[:, j] - y[:, k]) / twice_area
        grads[:, node, 1] = (x[:, k] - x[:, j]) / twice_area
    return grads, np.abs(0.5 * twice_area)


def _tri6_shape_gradients(xi: float, eta: float) -> NDArray[np.float64]:
    """dN/d(xi, eta) for the 6-node triangle, shape (6, 2).

    In area coordinates L = (1 - xi - eta, xi, eta) the shape functions are
    N_i = L_i (2 L_i - 1) at the corners and N_ab = 4 L_a L_b at the midside of
    edge (a, b), so the derivatives fall out of the product rule. Midside
    ordering is `TRI6_EDGES`, the same table the mesher fills in.
    """
    lam = np.array([1.0 - xi - eta, xi, eta], dtype=np.float64)
    grad = np.zeros((6, 2), dtype=np.float64)
    for corner in range(3):
        grad[corner] = (4.0 * lam[corner] - 1.0) * _DL_DNAT[corner]
    for local, (a, b) in enumerate(TRI6_EDGES, start=3):
        grad[local] = 4.0 * (lam[b] * _DL_DNAT[a] + lam[a] * _DL_DNAT[b])
    return grad


def _mapped_gradients(
    points: NDArray[np.float64], dn_dnat: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Physical dN/dx and |det J| for every element at one natural point.

    `points` is (n_elem, nodes, 2); `dn_dnat` is (nodes, 2).
    """
    jacobian = np.einsum("ji,ejk->eik", dn_dnat, points)  # J[i,k] = dx_k/dxi_i
    determinant = np.abs(np.asarray(np.linalg.det(jacobian), dtype=np.float64))
    degenerate = determinant < _MIN_JACOBIAN
    if degenerate.any():
        raise SolverError(
            f"{int(degenerate.sum())} element(s) have zero area; the mesh is degenerate"
        )
    inverse = np.linalg.inv(jacobian)
    # Chain rule: dN/dx = dN/dxi @ inv(J).T
    grads = np.asarray(np.einsum("ij,ekj->eik", dn_dnat, inverse), dtype=np.float64)
    return grads, determinant


def _strain_displacement(grads: NDArray[np.float64]) -> NDArray[np.float64]:
    """B matrices, shape (n_elem, 3, 2 * nodes_per_element), engineering shear.

    Voigt order [xx, yy, xy]. Shared by both element orders: the layout depends
    only on how many nodes an element has, which is `grads.shape[1]`.
    """
    n, per_element = grads.shape[0], grads.shape[1]
    b = np.zeros((n, 3, _DOF_PER_NODE * per_element), dtype=np.float64)
    gx, gy = grads[..., 0], grads[..., 1]
    for node in range(per_element):
        col = _DOF_PER_NODE * node
        b[:, 0, col + 0] = gx[:, node]
        b[:, 1, col + 1] = gy[:, node]
        b[:, 2, col + 0] = gy[:, node]
        b[:, 2, col + 1] = gx[:, node]
    return b


def _element_dofs(connectivity: NDArray[np.int64]) -> NDArray[np.int64]:
    """Global DOF index for each element-local DOF, shape (n_elem, 2 * nodes)."""
    per_element = connectivity.shape[1]
    return (
        connectivity[:, :, None] * _DOF_PER_NODE + np.arange(_DOF_PER_NODE)[None, None, :]
    ).reshape(len(connectivity), _DOF_PER_NODE * per_element)


def _scatter(
    mesh: TriMesh, dofs: NDArray[np.int64], ke: NDArray[np.float64]
) -> sp.csr_matrix:
    """Sum element matrices into the global sparse stiffness matrix."""
    width = dofs.shape[1]
    rows = np.repeat(dofs, width, axis=1).ravel()
    cols = np.tile(dofs, (1, width)).ravel()
    n_dof = _DOF_PER_NODE * mesh.node_count
    return sp.coo_matrix((ke.ravel(), (rows, cols)), shape=(n_dof, n_dof)).tocsr()


def assemble_stiffness(
    mesh: TriMesh, material: Material, state: PlaneState, thickness_mm: float
) -> sp.csr_matrix:
    """Global stiffness matrix, for whichever element order the mesh carries."""
    if mesh.midside is not None:
        return assemble_stiffness_tri6(mesh, material, state, thickness_mm)
    return assemble_stiffness_tri3(mesh, material, state, thickness_mm)


def assemble_stiffness_tri3(
    mesh: TriMesh, material: Material, state: PlaneState, thickness_mm: float
) -> sp.csr_matrix:
    """Ke = t A B^T D B, one evaluation per element.

    Tri3 strain is constant over the element, so one evaluation integrates the
    element matrix exactly -- there is no quadrature error to trade off, only
    the element's inability to represent a strain gradient at all.
    """
    grads, areas = _tri3_shape_gradients(mesh)
    b = _strain_displacement(grads)
    d = constitutive_matrix(material, state)
    ke = (thickness_mm * areas)[:, None, None] * np.einsum("eji,jk,ekl->eil", b, d, b)
    return _scatter(mesh, _element_dofs(mesh.tris), ke)


def assemble_stiffness_tri6(
    mesh: TriMesh, material: Material, state: PlaneState, thickness_mm: float
) -> sp.csr_matrix:
    """Global stiffness for quadratic triangles: 6 nodes, 12 local DOFs each.

    Unlike tri3, strain varies over the element, so the element matrix is
    integrated numerically -- at the three Gauss points that make the rule exact
    for the degree-2 integrand a straight-sided tri6 produces. See
    `_TRI_GAUSS_POINTS`.
    """
    if mesh.midside is None:
        raise SolverError("mesh has no midside nodes; it is not a tri6 mesh")

    connectivity = mesh.connectivity
    points = mesh.nodes[connectivity][:, :, :2]  # (n_tris, 6, 2)
    d = constitutive_matrix(material, state)
    ke = np.zeros((len(connectivity), 12, 12), dtype=np.float64)

    for point in _TRI_GAUSS_POINTS:
        grads, detj = _mapped_gradients(points, _tri6_shape_gradients(*point))
        b = _strain_displacement(grads)
        ke += (
            _TRI_GAUSS_WEIGHT
            * thickness_mm
            * detj[:, None, None]
            * np.einsum("eji,jk,ekl->eil", b, d, b)
        )

    return _scatter(mesh, _element_dofs(connectivity), ke)


def thermal_load(mesh: TriMesh, case: PlaneCase) -> NDArray[np.float64]:
    """Equivalent nodal forces for a uniform temperature change, (2 * n_nodes,).

    Integrated the same way the stiffness is, so the two agree element by
    element: one evaluation for tri3, whose strain is constant, and the
    three-point rule for tri6.
    """
    if case.delta_t_k is None:  # pragma: no cover - the caller checks first
        return np.zeros(_DOF_PER_NODE * mesh.node_count, dtype=np.float64)

    strain = thermal_strain(case.material, case.state, case.delta_t_k)
    stress = constitutive_matrix(case.material, case.state) @ strain

    connectivity = mesh.connectivity
    element_dofs = _element_dofs(connectivity)
    forces = np.zeros(_DOF_PER_NODE * mesh.node_count, dtype=np.float64)

    if mesh.midside is None:
        grads, areas = _tri3_shape_gradients(mesh)
        b = _strain_displacement(grads)
        local = (case.thickness_mm * areas)[:, None] * np.einsum("eij,i->ej", b, stress)
    else:
        points = mesh.nodes[connectivity][:, :, :2]
        local = np.zeros(
            (len(connectivity), _DOF_PER_NODE * connectivity.shape[1]), dtype=np.float64
        )
        for point in _TRI_GAUSS_POINTS:
            grads, detj = _mapped_gradients(points, _tri6_shape_gradients(*point))
            b = _strain_displacement(grads)
            local += (
                _TRI_GAUSS_WEIGHT
                * case.thickness_mm
                * detj[:, None]
                * np.einsum("eij,i->ej", b, stress)
            )

    np.add.at(forces, element_dofs.ravel(), local.ravel())
    return forces


# -- restraints --------------------------------------------------------------


def in_plane_dofs(fixture: Fixture) -> list[str]:
    """The x/y degrees of freedom a fixture holds on a plane model.

    Refuses a fixture that names `z`. A plane model has two displacement
    components per node and there is no third one to restrain: under plane
    stress the material contracts freely through the thickness, and under plane
    strain it is already held everywhere by the idealisation itself. Accepting
    `dofs=["z"]` and dropping it would leave a caller who wrote a 3-D symmetry
    plane with a model free in a direction they believe they fixed, and nothing
    downstream would say so.

    `kind="clamp"` is the deliberate exception, because it does not name `z` --
    it names "built in", and `Fixture._resolve_dofs` expanded that into the
    3-D spelling before this module ever saw it. On a plane model a clamp holds
    x and y, which is what the caller meant. Refusing the commonest fixture in
    the vocabulary would also push people towards writing `dofs=["x","y","z"]`
    by analogy, which is the mistake this refusal exists to catch.
    """
    held: list[str] = list(fixture.held)
    if "z" not in held:
        return held
    if fixture.kind == "clamp":
        return ["x", "y"]
    name = f"{fixture.name!r} " if fixture.name else ""
    raise SolverError(
        f"Fixture {name}holds 'z', and a plane model has no out-of-plane degree of freedom: "
        "plane stress lets the material contract freely through the thickness, and plane "
        "strain already holds it everywhere by definition. Use kind='clamp' for a fully "
        "built-in edge (it holds x and y here), or dofs=['x'] / dofs=['y'] for a symmetry "
        "roller in the plane."
    )


def _fixed_dofs(mesh: TriMesh, fixtures: list[Fixture]) -> NDArray[np.int64]:
    blocks = []
    for fixture in fixtures:
        offsets = np.array([_AXIS_INDEX[axis] for axis in in_plane_dofs(fixture)], dtype=np.int64)
        nodes = select_nodes(mesh, fixture.where)
        blocks.append((nodes[:, None] * _DOF_PER_NODE + offsets[None, :]).ravel())
    return np.unique(np.concatenate(blocks))


# -- loads -------------------------------------------------------------------


def _selected_boundary_edges(
    mesh: TriMesh, nodes: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.int64] | None]:
    """Boundary edges whose two corners are both in `nodes`, and their midsides.

    The 2-D counterpart of `selection._boundary_faces_within`, and selected the
    same way: on the **corners** only. A quadratic edge's midside node is a
    carrier of load, not a member of the region -- on a curved boundary it sits
    a sagitta inside the arc, so testing it against a cylinder selector's
    tolerance would drop whole edges from a bore that is plainly selected.
    """
    membership = np.zeros(mesh.node_count, dtype=bool)
    membership[nodes] = True
    edges = mesh.boundary_edges
    selected = membership[edges].all(axis=1)
    midsides = mesh.boundary_edge_midsides
    return edges[selected], None if midsides is None else midsides[selected]


def _edge_geometry(
    mesh: TriMesh, edges: NDArray[np.int64], orientation: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Chord length and outward unit normal of each boundary edge.

    `TriMesh.boundary_edges` is wound as its owning triangle wound it, so for an
    anticlockwise mesh walking an edge from its first node to its second leaves
    the material on the left and the outward normal is the tangent rotated
    clockwise, `(dy, -dx)`. `orientation` carries the mesh's winding from
    `_winding` so a wholly clockwise mesh flips rather than pushing a pressure
    the wrong way through the wall.
    """
    p = mesh.nodes[edges][:, :, :2]
    tangent = p[:, 1] - p[:, 0]
    length = np.linalg.norm(tangent, axis=1)
    # A zero-length edge carries no load and has no defined normal; guarding the
    # division keeps it at zero rather than producing NaN that would poison the
    # whole solve.
    safe = np.where(length > 0.0, length, 1.0)
    normal = orientation * np.column_stack([tangent[:, 1], -tangent[:, 0]]) / safe[:, None]
    return length, normal


def _edge_shape_weights(
    mesh: TriMesh,
    edges: NDArray[np.int64],
    midsides: NDArray[np.int64] | None,
    lengths: NDArray[np.float64],
    thickness_mm: float,
) -> NDArray[np.float64]:
    """Tributary area of each node on the selected boundary, shape (n_nodes,).

    The weights are the integrals of the *edge* shape functions times the
    out-of-plane thickness, so they are areas and a traction times a weight is a
    force. For a 2-node edge each end takes `t L / 2`. For a 3-node (quadratic)
    edge the corner functions integrate to `t L / 6` each and the midside one to
    `2 t L / 3`.

    **This is not the tri6 face case in `selection.distribute_force`, and the
    difference is easy to carry across by mistake.** On a quadratic *triangular
    face* in 3-D the corner shape functions integrate to exactly zero and the
    whole facet load sits on the midside nodes. On a quadratic *line* they do
    not: they integrate to a positive sixth. Putting a line load entirely on the
    midside nodes by analogy, or splitting it 1/3-1/3-1/3 by intuition, is
    statically equivalent either way and changes the peak stress next to the
    loaded edge -- silently, because the resultant still checks out.
    """
    weights = np.zeros(mesh.node_count, dtype=np.float64)
    if len(edges) == 0:
        return weights
    scaled = thickness_mm * lengths
    if midsides is None:
        np.add.at(weights, edges, (scaled / 2.0)[:, None])
    else:
        np.add.at(weights, edges, (scaled / 6.0)[:, None])
        np.add.at(weights, midsides, scaled * (2.0 / 3.0))
    return weights


def distribute_edge_force(
    mesh: TriMesh,
    nodes: NDArray[np.int64],
    force: NDArray[np.float64],
    thickness_mm: float,
) -> tuple[NDArray[np.float64], str | None]:
    """Spread a total force over a selected boundary as a consistent nodal load.

    Returns ((n_nodes, 2) force array, warning or None). Tributary length times
    thickness keeps the applied load mesh-independent, exactly as tributary area
    does for a solid; if the selection has no complete boundary edge (a box
    picking interior nodes, say) it falls back to an equal split and says so,
    the same fallback and the same wording `distribute_force` uses.
    """
    edges, midsides = _selected_boundary_edges(mesh, nodes)
    lengths, _ = _edge_geometry(mesh, edges, 1.0)
    weights = _edge_shape_weights(mesh, edges, midsides, lengths, thickness_mm)

    total = float(weights.sum())
    warning: str | None = None
    if total <= 0.0:
        weights = np.zeros(mesh.node_count, dtype=np.float64)
        weights[nodes] = 1.0
        total = float(len(nodes))
        warning = (
            "Load region contains no complete boundary edges; the force was split "
            "equally between its nodes instead of by length."
        )

    return (weights / total)[:, None] * force[None, :], warning


def _pressure_forces(
    mesh: TriMesh,
    nodes: NDArray[np.int64],
    pressure_mpa: float,
    thickness_mm: float,
    orientation: float,
) -> NDArray[np.float64]:
    """Uniform pressure along each boundary edge's own outward normal.

    Integrated edge by edge rather than applied as one resultant, for the reason
    `loads._pressure` gives in 3-D: a curved boundary's normal varies along it,
    so a pressure on a bore has no net resultant at all and treating it as one
    vector would invent a large spurious force.
    """
    edges, midsides = _selected_boundary_edges(mesh, nodes)
    if len(edges) == 0:
        raise SolverError(
            "A pressure needs a boundary to act on and this selection has no complete "
            "boundary edges. Select an edge of the region rather than a box of interior "
            "nodes."
        )
    lengths, normals = _edge_geometry(mesh, edges, orientation)

    # Positive pressure pushes inward, so it acts along -n. Same sign convention
    # as `PressureLoad` documents and as the 3-D path uses.
    traction = -pressure_mpa * normals  # N/mm^2, per unit area of the edge face
    per_node = np.zeros((mesh.node_count, 2), dtype=np.float64)
    scaled = thickness_mm * lengths
    if midsides is None:
        np.add.at(per_node, edges, ((scaled / 2.0)[:, None] * traction)[:, None, :])
    else:
        np.add.at(per_node, edges, ((scaled / 6.0)[:, None] * traction)[:, None, :])
        np.add.at(per_node, midsides, (scaled * (2.0 / 3.0))[:, None] * traction)
    return per_node


def assemble_loads(
    mesh: TriMesh, case: PlaneCase, orientation: float
) -> tuple[NDArray[np.float64], list[str]]:
    """Total nodal force vector for every load, and any warnings raised.

    Returns a flat (2 * node_count,) array in the solver's DOF ordering. Public
    so a test can weigh the applied load without solving: "refining the mesh
    must not change the total force" is the invariant tributary weighting exists
    for, and it is only checkable if the vector is reachable.

    Force and pressure are the two load types a plane model carries today. The
    others are refused **by name** rather than approximated: a moment and a
    bearing load are both defined over a 3-D region in `app.solve.loads`, and
    gravity and centrifugal loads are body loads that would need an area
    integral this module does not have. Each is a real gap with an obvious
    shape, and a wrong answer with the right units is worse than a refusal.
    """
    total = np.zeros(_DOF_PER_NODE * mesh.node_count, dtype=np.float64)
    warnings: list[str] = []

    for load in case.loads:
        if isinstance(load, ForceLoad):
            if load.force_n[2] != 0.0:
                # Refused, not dropped, for the same reason a fixture naming 'z'
                # is refused: there is no out-of-plane degree of freedom to push
                # against, so the component would apply nothing at all and the
                # caller would read a smaller answer as a real one.
                raise SolverError(
                    f"Force {load.name!r} has an out-of-plane component "
                    f"({load.force_n[2]:g} N in z), and a plane model has no out-of-plane "
                    "degree of freedom for it to act on. Give the in-plane force as "
                    "(fx, fy, 0), or solve the part as a solid."
                )
            nodes = select_nodes(mesh, load.where)
            per_node, warning = distribute_edge_force(
                mesh, nodes, np.asarray(load.force_n[:2], dtype=np.float64), case.thickness_mm
            )
        elif isinstance(load, PressureLoad):
            nodes = select_nodes(mesh, load.where)
            per_node = _pressure_forces(
                mesh, nodes, load.pressure_mpa, case.thickness_mm, orientation
            )
            warning = None
        else:
            raise SolverError(
                f"A {type(load).__name__} cannot be applied to a plane model. Plane models "
                "take a ForceLoad or a PressureLoad on a boundary; moment, bearing, gravity "
                "and centrifugal loads are defined over a solid and have no plane form here "
                "yet."
            )

        if warning:
            warnings.append(f"{load.name or type(load).__name__}: {warning}")
        total += per_node.ravel()

    return total, warnings


# -- solving -----------------------------------------------------------------


def _residual_is_small(
    k_ff: sp.spmatrix, solution: NDArray[np.float64], applied: NDArray[np.float64]
) -> bool:
    scale = np.linalg.norm(applied)
    if scale == 0.0:
        return True
    residual = np.linalg.norm(k_ff @ solution - applied)
    return bool(float(residual) <= _RESIDUAL_TOLERANCE * scale)


def _under_constrained(detail: str) -> SolverError:
    return SolverError(
        "The model is under-constrained: part of it can still move or spin freely "
        f"({detail}). Check that the fixtures remove all three rigid-body motions of a "
        "plane model — the two in-plane translations and the rotation about z."
    )


def _nodal_average(mesh: TriMesh, element_values: NDArray[np.float64]) -> NDArray[np.float64]:
    """Spread constant-per-element tensors onto nodes by simple averaging.

    The plane counterpart of `postprocess.nodal_average`, written here rather
    than imported because that one is typed on `TetMesh` and reads `tet_count`'s
    neighbours. Widening it to a protocol is a change to a file this module does
    not own; a dozen lines is the cheaper honest answer.
    """
    connectivity = mesh.connectivity
    totals = np.zeros((mesh.node_count, element_values.shape[1]), dtype=np.float64)
    counts = np.zeros(mesh.node_count, dtype=np.int64)
    np.add.at(totals, connectivity, element_values[:, None, :])
    np.add.at(counts, connectivity, 1)
    divisor = counts[:, None]
    return np.divide(totals, divisor, out=np.zeros_like(totals), where=divisor > 0)


class PlanarSolver(ABC):
    """Interface every plane-stress / plane-strain solver implements.

    **A sibling of `Solver`, not a method on it**, for exactly the reason
    `ModalSolver` is one: the two answer their question from a different input.
    A `PlanarSolver` takes a `TriMesh` and a `PlaneCase`; a `Solver` takes a
    `TetMesh` and a `LoadCase`. A single `solve` accepting a union of meshes and
    a union of cases would push a branch into every caller — the job runner, the
    verification harness, an oracle comparison — and each of them would have to
    re-derive which combination it was holding. Keeping them apart means a
    surrogate plane solver can drop in here without the solid path knowing it
    exists, which is the whole point of the seam.

    The *output* type is deliberately shared. `SolveOutput` is what the
    verification machinery, the provenance record and the viewer already read,
    and a plane result is the same four fields — displacement, von Mises, a
    stress tensor and a summary. A second output type would fork all of that for
    no gain.
    """

    name: str

    @abstractmethod
    def solve(self, mesh: TriMesh, case: PlaneCase) -> SolveOutput: ...


class PlaneSolver(PlanarSolver):
    name = "plane"

    def solve(self, mesh: TriMesh, case: PlaneCase) -> SolveOutput:
        started = time.perf_counter()
        warnings: list[str] = []

        orientation = _winding(mesh)
        n_dof = _DOF_PER_NODE * mesh.node_count
        forces, load_warnings = assemble_loads(mesh, case, orientation)
        warnings.extend(load_warnings)

        # Restrained thermal expansion is a load like any other, so it is added
        # here rather than solved separately: a part that is both heated and
        # pushed has one displacement field, not two to superpose by hand.
        if case.delta_t_k:
            forces = forces + thermal_load(mesh, case)

        fixed = _fixed_dofs(mesh, case.fixtures)
        free = np.setdiff1d(np.arange(n_dof), fixed)
        if len(free) == 0:
            raise SolverError("Every degree of freedom is fixed; there is nothing to solve")

        # Assembly and factorisation get separate spans for the reason
        # `linear_static` gives: a slow assembly and a slow solve have different
        # fixes, and one span over both cannot tell them apart.
        with observe.span(
            "solve.plane",
            nodes=mesh.node_count,
            elements=mesh.element_count,
            degrees_of_freedom=int(n_dof),
            state=str(case.state),
        ) as timing:
            timing.set("stage", "assemble")
            stiffness = assemble_stiffness(mesh, case.material, case.state, case.thickness_mm)
            k_ff = stiffness[free][:, free].tocsc()
            k_ff.eliminate_zeros()

            displacements = np.zeros(n_dof, dtype=np.float64)
            applied = forces[free]

            timing.set("stage", "factorise")
            if n_dof > _ITERATIVE_THRESHOLD_DOF:
                timing.set("method", "iterative")
                solution = self._solve_iterative(k_ff, applied)
            else:
                timing.set("method", "direct")
                solution = self._solve_direct(k_ff, applied)

        # SuperLU returns a finite, meaningless vector for a singular system, so
        # the test is equilibrium and not finiteness. Never replace this with a
        # NaN check.
        if not np.all(np.isfinite(solution)) or not _residual_is_small(k_ff, solution, applied):
            raise _under_constrained("the solution does not satisfy equilibrium")
        displacements[free] = solution

        # (n_nodes, 3) with the z column zero, so every consumer of
        # `SolveOutput.displacements` — the viewer, the provenance record, the
        # convergence harness — reads a plane result without a special case.
        planar = np.zeros((mesh.node_count, 3), dtype=np.float64)
        planar[:, :2] = displacements.reshape(-1, _DOF_PER_NODE)

        element_stress = self._recover_stress(mesh, case, displacements)
        mises = von_mises(element_stress)
        result = self._summarise(
            mesh, case, planar, mises, warnings, time.perf_counter() - started
        )
        return SolveOutput(
            result=result,
            displacements=planar,
            von_mises=mises,
            nodal_stress=self._recover_nodal_stress(mesh, case, displacements),
        )

    @staticmethod
    def _solve_direct(k_ff: sp.csc_matrix, applied: NDArray[np.float64]) -> NDArray[np.float64]:
        try:
            with warnings_module.catch_warnings():
                # A singular K raises MatrixRankWarning rather than failing.
                warnings_module.simplefilter("error", spla.MatrixRankWarning)
                solution = spla.spsolve(k_ff, applied, permc_spec="COLAMD", use_umfpack=False)
        except (RuntimeError, spla.MatrixRankWarning) as exc:
            raise _under_constrained(str(exc)) from exc
        return np.asarray(solution, dtype=np.float64)

    @staticmethod
    def _solve_iterative(
        k_ff: sp.csc_matrix, applied: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """Preconditioned CG, Jacobi rather than ILU.

        The same choice `linear_static._solve_iterative` documents at length and
        for the same reason: SuperLU's ILUTP pivots, so the operator it produces
        is not symmetric, and CG's convergence proof needs an SPD
        preconditioner. K is SPD here by the same argument — symmetric assembly,
        and the free-DOF restriction of a properly constrained stiffness matrix
        is positive definite — so its diagonal is strictly positive and Jacobi
        is SPD by construction.
        """
        diagonal = k_ff.diagonal()
        if not np.all(diagonal > 0.0):
            raise _under_constrained("the stiffness matrix is not positive definite")

        inverse_diagonal = 1.0 / diagonal
        precond = spla.LinearOperator(
            k_ff.shape, matvec=lambda x: inverse_diagonal * x, dtype=np.float64
        )
        solution, info = spla.cg(k_ff, applied, rtol=_RESIDUAL_TOLERANCE, maxiter=5000, M=precond)
        if info != 0:
            raise SolverError(
                f"Conjugate gradient failed to converge (info={info}). The model may be "
                "under-constrained, or the mesh may need refining."
            )
        return np.asarray(solution, dtype=np.float64)

    def _expand_stress(
        self, inplane: NDArray[np.float64], case: PlaneCase
    ) -> NDArray[np.float64]:
        """(n, 3) in-plane Voigt -> (n, 6) SXX SYY SZZ SXY SYZ SZX.

        **SZZ is the one number that separates the two idealisations, and it is
        the one an implementation gets wrong silently.** Plane stress says the
        out-of-plane stress is zero by definition. Plane strain says the
        out-of-plane *strain* is zero, and the stress that enforces that is
        `nu (SXX + SYY)` — minus `E alpha dT` when the slice is also heated,
        because part of the restraint is then absorbed by the thermal expansion
        it is preventing. Reporting zero for a plane-strain model does not fail
        anything: the in-plane numbers stay right and only von Mises quietly
        drops, by 30% or so for steel, which reads as a model being a little
        optimistic rather than as an error.

        SYZ and SZX are zero in both: an in-plane problem has no out-of-plane
        shear, which is exactly the assumption that makes it two-dimensional.
        """
        sxx, syy, sxy = inplane[:, 0], inplane[:, 1], inplane[:, 2]
        szz = np.zeros_like(sxx)
        if case.state is PlaneState.STRAIN:
            szz = case.material.poissons_ratio * (sxx + syy)
            if case.delta_t_k:
                # `thermal_strain` has already refused a material with no alpha
                # by the time this runs; the fallback keeps the arithmetic total
                # rather than adding a second refusal in a different voice.
                alpha = case.material.thermal_expansion_per_k or 0.0
                szz = szz - case.material.youngs_modulus_mpa * alpha * case.delta_t_k
        zero = np.zeros_like(sxx)
        return np.column_stack([sxx, syy, szz, sxy, zero, zero])

    def _stress_at(
        self,
        mesh: TriMesh,
        case: PlaneCase,
        displacements: NDArray[np.float64],
        natural: tuple[float, float],
    ) -> NDArray[np.float64]:
        """The stress tensor of every element at one natural coordinate.

        (n_elements, 6), SXX SYY SZZ SXY SYZ SZX — the tensor rather than von
        Mises, because the caller needs both and the invariant throws away the
        signed components a benchmark reads.
        """
        if mesh.midside is None:
            # Tri3 strain is constant over the element, so the natural point is
            # irrelevant and one evaluation is exact.
            grads, _ = _tri3_shape_gradients(mesh)
        else:
            grads, _ = _mapped_gradients(
                mesh.nodes[mesh.connectivity][:, :, :2], _tri6_shape_gradients(*natural)
            )
        b = _strain_displacement(grads)
        element_u = displacements[_element_dofs(mesh.connectivity)]
        strain = np.einsum("eij,ej->ei", b, element_u)
        if case.delta_t_k:
            # Only the *mechanical* part of the strain carries stress. Skipping
            # this subtraction reports the stress of a part that was free to
            # expand, which for a restrained slice is the wrong sign as well as
            # the wrong size.
            strain = strain - thermal_strain(case.material, case.state, case.delta_t_k)
        inplane = strain @ constitutive_matrix(case.material, case.state).T
        return self._expand_stress(inplane, case)

    def _recover_stress(
        self, mesh: TriMesh, case: PlaneCase, displacements: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """The stress reported *per element*, sampled at the centroid.

        The centroid is the element's superconvergent point, and this is the
        number the headline peak stress and the factor of safety come from.
        """
        return self._stress_at(mesh, case, displacements, _CENTROID)

    def _recover_nodal_stress(
        self, mesh: TriMesh, case: PlaneCase, displacements: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """The stress reported *per node*, (n_nodes, 6).

        **Evaluated at each node's own natural coordinate and then averaged, not
        by averaging centroid values.** The two sound alike and differ by the
        thing a stress benchmark measures: a tri6's strain is linear, so on a
        part in bending the centroid value is the stress partway in from the
        surface. Spreading it to the nodes under-reads the surface — and because
        the error shrinks with refinement, it reads as a converging answer
        rather than as a systematic offset. That is the defect E7 task 1 found
        on tet10 and it is the same defect here.

        For tri3 the strain is constant, so this necessarily reduces to
        averaging the element values: a constant-strain element has no
        within-element variation to recover, which is the same reason it cannot
        represent bending in the first place.
        """
        if mesh.midside is None:
            return _nodal_average(mesh, self._stress_at(mesh, case, displacements, _CENTROID))

        connectivity = mesh.connectivity
        totals = np.zeros((mesh.node_count, 6), dtype=np.float64)
        counts = np.zeros(mesh.node_count, dtype=np.int64)
        for local, natural in enumerate(_TRI6_NATURAL_NODES):
            stress = self._stress_at(mesh, case, displacements, natural)
            np.add.at(totals, connectivity[:, local], stress)
            np.add.at(counts, connectivity[:, local], 1)
        divisor = counts[:, None]
        return np.divide(totals, divisor, out=np.zeros_like(totals), where=divisor > 0)

    @staticmethod
    def _summarise(
        mesh: TriMesh,
        case: PlaneCase,
        displacements: NDArray[np.float64],
        von_mises_per_element: NDArray[np.float64],
        warnings: list[str],
        seconds: float,
    ) -> StaticResult:
        """The summary of one plane run, built exactly as `summarise_static`
        builds a solid one.

        Not `postprocess.summarise_static` itself: that function is typed on
        `TetMesh` and `LoadCase` and reads `mesh.volume` and `mesh.tet_count`,
        neither of which a `TriMesh` has. The **rules** are copied verbatim and
        that is the part that matters — the peak stress is the raw per-element
        value and never the smoothed one, because a factor of safety read off a
        smoothed field is optimistic exactly at the concentration.

        The one genuinely new line is the volume: a plane model's volume is its
        meshed area times the out-of-plane thickness, which for a plane-strain
        slice is a per-slice mass rather than the part's mass. That is why
        `thickness_mm` is carried explicitly even when it is 1.
        """
        magnitudes = np.linalg.norm(displacements, axis=1)
        peak_node = int(np.argmax(magnitudes))
        peak_element = int(np.argmax(von_mises_per_element))
        peak_stress = float(von_mises_per_element[peak_element])

        yield_strength = case.material.yield_strength_mpa
        fos = yield_strength / peak_stress if peak_stress > 0.0 else float("inf")
        volume_mm3 = mesh.area * case.thickness_mm

        return StaticResult(
            max_displacement_mm=float(magnitudes[peak_node]),
            max_displacement_node=peak_node,
            max_von_mises_mpa=peak_stress,
            max_von_mises_element=peak_element,
            factor_of_safety=fos,
            yields=peak_stress >= yield_strength,
            mass_kg=volume_mm3 * 1e-9 * case.material.density_kg_m3,
            volume_mm3=volume_mm3,
            node_count=mesh.node_count,
            element_count=mesh.element_count,
            solve_seconds=seconds,
            warnings=warnings,
        )


__all__ = [
    "PlaneCase",
    "PlaneSolver",
    "PlaneState",
    "PlanarSolver",
    "assemble_loads",
    "assemble_stiffness",
    "assemble_stiffness_tri3",
    "assemble_stiffness_tri6",
    "constitutive_matrix",
    "distribute_edge_force",
    "in_plane_dofs",
    "plane_strain_matrix",
    "plane_stress_matrix",
    "thermal_load",
    "thermal_strain",
]
