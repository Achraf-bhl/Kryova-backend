"""Turning a `Load` into the nodal force vector a **shell** model assembles.

`app/solve/loads.py` does this for a solid. It cannot do it for a shell, and
`app/solve/selection.py` says so where `distribute_force` is defined: tributary
area there is defined over a solid's *boundary triangles*, and a shell's faces
are not boundary triangles of anything. This module is the other distribution,
written rather than approximated.

**The invariant is `loads.py`'s and is the reason this file exists at all:
refining the mesh must not change the applied load.** Splitting a force equally
between the selected nodes would run, look right, and move the load every time
the mesh changed — which is exactly what `write_frame_deck` refused to pretend
was done when it took a raw force vector instead of a `LoadCase`.

**There is no consistent *edge* load here, and the common shell load is an edge
load.** `shell_faces_within` needs whole faces, so a traction on a free edge — a
cantilever tip, and the way most shell benchmarks are posed — selects a band of
nodes one row deep, matches no face, and takes `distribute_force_over_shell`'s
equal-split fallback. **The resultant is right and the distribution is not.**
Measured on a quad8 plate: the consistent edge load is (1/6, 2/3, 1/6) per
element, and the fallback gives an end corner 1/9 where it should have 1/24 —
2.7x too much — while a midside gets 1/9 against 1/6. That is the same error
class this module's own tri6 note quantifies at ~20%, and it is larger. It is
*warned* rather than silent, so the fallback's warning must be surfaced by
whatever eventually calls this; the fix is an edge rule with its own shape
integrals over `SHELL_TRI_EDGES`/`SHELL_QUAD_EDGES`, which is real work and is
not started. `app/solve/plane.py` has the 1-D analogue (L/6 - 2L/3 - L/6) and is
the shape to copy.

**What is different from the solid path, and it is not a detail.** On a solid,
every node of a loaded facet takes a positive share of it. On an 8-node
quadrilateral the four *corner* shape functions integrate to a **negative**
number, so a correctly loaded S8R face pulls its corners backwards while its
midside nodes carry more than the whole load. That is not a sign error; it is
what the serendipity shape functions integrate to, and the four values in
`SHELL_SHAPE_INTEGRALS` are the whole of the difference between a consistent
shell load and a plausible one.
"""

from __future__ import annotations

from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.structural import ShellMesh
from app.solve.loads import _DENSITY_KG_M3_TO_TONNE_MM3
from app.solve.selection import radial_offsets, select_nodes
from app.solve.types import (
    BearingLoad,
    CentrifugalLoad,
    ForceLoad,
    GravityLoad,
    Load,
    MomentLoad,
    PressureLoad,
    SolverError,
)

#: `(corner, midside)` — the integral of one shape function over a face, as a
#: fraction of that face's area, keyed by `ShellMesh.element_type`.
#:
#: These are the consistent nodal load factors for a uniform traction, i.e.
#: `∫ N_i dA / A`, and each row sums (over all the face's nodes) to exactly 1,
#: which is what makes the applied resultant equal the requested one. They are
#: the same family of facts as the tet10 rule in `selection.distribute_force`,
#: which is why the quadratic rows put the load on the midside nodes.
#:
#: * `tri3` — a third each, the classic tributary third of a triangle.
#: * `tri6` — the corner functions integrate to **zero** and the midside ones to
#:   a third each. Loading the corners instead is only statically equivalent and
#:   overstates the peak stress near the loaded edge; `distribute_force` states
#:   the same thing at around 20% for a tet10 face.
#: * `quad4` — a quarter each.
#: * `quad8` — **corners take −1/12 and midside nodes +1/3.** Check it:
#:   `4 × (−1/12) + 4 × (1/3) = −1/3 + 4/3 = 1`. The negative corner term is the
#:   trap in this table — a "tributary area" intuition gives every node a
#:   positive share, and an S8R face loaded that way is wrong in a way that
#:   solves cleanly.
#:
#: Exact for a straight-sided element with a constant Jacobian, which is what
#: `Mesh.SecondOrderLinear` makes the mesher produce (`app/mesh/gmsh_mesher.py`);
#: on a distorted face they are the standard approximation, and the same one the
#: solid path already takes.
SHELL_SHAPE_INTEGRALS: Final[dict[str, tuple[float, float]]] = {
    "tri3": (1.0 / 3.0, 0.0),
    "tri6": (0.0, 1.0 / 3.0),
    "quad4": (0.25, 0.0),
    "quad8": (-1.0 / 12.0, 1.0 / 3.0),
}


def shell_node_weights(
    mesh: ShellMesh, faces: NDArray[np.int64] | None = None
) -> NDArray[np.float64]:
    """`∫ N_i dA` at every node of the mesh, in mm², over `faces`.

    `faces` is a set of face *row indices*, or None for the whole mesh. The
    result is a `(node_count,)` array whose sum is the total area of those
    faces — a node's weight is how much area its shape function commands, which
    is the quantity every distribution below divides by.
    """
    corner_factor, midside_factor = SHELL_SHAPE_INTEGRALS[mesh.element_type]
    areas = mesh.face_areas()
    corners = mesh.faces
    if faces is not None:
        areas = areas[faces]
        corners = corners[faces]

    weights = np.zeros(mesh.node_count, dtype=np.float64)
    if len(corners) == 0:
        return weights

    np.add.at(weights, corners, (areas * corner_factor)[:, None])
    if mesh.midside is not None:
        midside = mesh.midside if faces is None else mesh.midside[faces]
        np.add.at(weights, midside, (areas * midside_factor)[:, None])
    return weights


def shell_faces_within(mesh: ShellMesh, nodes: NDArray[np.int64]) -> NDArray[np.int64]:
    """Row indices of the faces whose every **corner** is in `nodes`.

    Corners only, deliberately, matching `_boundary_faces_within` on the solid
    side. A midside node of a curved shell sits off the chord between its two
    corners, so a plane selector that catches both corners can miss the midside
    between them — requiring it too would drop faces from the middle of a
    selected region and shrink the loaded area as the mesh got finer, which is
    the mesh-dependence this module exists to prevent.
    """
    membership = np.zeros(mesh.node_count, dtype=bool)
    membership[nodes] = True
    return np.flatnonzero(membership[mesh.faces].all(axis=1))


def distribute_force_over_shell(
    mesh: ShellMesh, nodes: NDArray[np.int64], force: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Spread a total force over a selected region of a shell.

    Returns `(per-node force (k, 3), the k nodes it applies to, warning)`.

    **It returns the nodes rather than taking them as the answer**, which is the
    one place this deliberately does not mirror `selection.distribute_force`.
    That function returns forces aligned with the *selection*, and on a
    quadratic mesh the load lands on midside nodes that the selection need not
    contain — the solid path loses that share silently. Here the carriers of a
    selected face are all of its nodes, so the resultant delivered is the
    resultant asked for, and the caller is told which nodes it went to.
    """
    faces = shell_faces_within(mesh, nodes)
    weights = shell_node_weights(mesh, faces)
    total = float(weights.sum())
    warning: str | None = None

    if len(faces) == 0 or total <= 0.0:
        carriers = np.asarray(nodes, dtype=np.int64)
        if len(carriers) == 0:
            raise SolverError(
                "A force was applied to a region that selects no nodes at all, so there "
                "is nothing to apply it to. Widen the selector or check its tolerance."
            )
        share = np.full(len(carriers), 1.0 / len(carriers))
        warning = (
            "Load region contains no complete shell faces; the force was split equally "
            "between its nodes instead of by area."
        )
        return share[:, None] * force[None, :], carriers, warning

    carriers = np.flatnonzero(weights != 0.0)
    share = weights[carriers] / total
    return share[:, None] * force[None, :], carriers, warning


def face_normals(mesh: ShellMesh) -> NDArray[np.float64]:
    """Each face's unit normal, from its own winding. (n_faces, 3).

    **A shell has no outward side and this codebase will not invent one.** On a
    solid, `loads._facet_areas_and_normals` can speak of an outward normal
    because the mesher orients the boundary outward, and that is what lets a
    positive pressure push into the material without the caller saying which way
    that is. A mid-surface has material on both sides, so the winding the CAD
    file happened to give the face is the only answer available. The convention
    is kept identical to the solid path — a positive pressure acts along `-n` —
    and which side that is, is the caller's to know.

    A quadrilateral's normal comes from the cross product of its diagonals,
    which is the bilinear surface's normal at the centre and degrades gracefully
    on a warped face; a triangle's from two of its edges.
    """
    points = mesh.nodes[mesh.faces]
    if mesh.is_triangular:
        cross = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
    else:
        cross = np.cross(points[:, 2] - points[:, 0], points[:, 3] - points[:, 1])
    magnitude = np.linalg.norm(cross, axis=1)
    # A degenerate face has no defined normal and carries no load; guarding the
    # division keeps it at zero rather than seeding NaN through the whole solve.
    safe = np.where(magnitude > 0.0, magnitude, 1.0)
    return cross / safe[:, None]


def assemble_shell_loads(
    mesh: ShellMesh,
    loads: list[Load],
    thickness_mm: float,
    density_kg_m3: float,
) -> tuple[NDArray[np.float64], list[str]]:
    """Total nodal force vector for a shell model, and any warnings raised.

    Returns a flat `(3 * node_count,)` array in the same node-major, three
    translations per node ordering `assemble_loads` uses and `cload_data_lines`
    writes. Rotational degrees of freedom are not loaded — `write_frame_deck`
    states that gap and this does not close it.

    `thickness_mm` is taken rather than read off the mesh for the reason
    `app/mesh/structural.py` gives for not putting it there: one mesh is
    legitimately solved at several thicknesses. It is only used by the body
    loads, which are the only ones that need to know how much material a face
    stands for.
    """
    if thickness_mm <= 0.0:
        raise SolverError(
            f"A shell thickness of {thickness_mm} mm encloses no material, so a body "
            "load over it would be zero and a pressure would act on nothing."
        )

    total = np.zeros(3 * mesh.node_count, dtype=np.float64)
    warnings: list[str] = []

    for load in loads:
        if isinstance(load, ForceLoad):
            nodal, nodes, warning = _force(mesh, load)
        elif isinstance(load, PressureLoad):
            nodal, nodes, warning = _pressure(mesh, load)
        elif isinstance(load, MomentLoad):
            nodal, nodes, warning = _moment(mesh, load)
        elif isinstance(load, GravityLoad):
            nodal, nodes, warning = _gravity(mesh, load, thickness_mm, density_kg_m3)
        elif isinstance(load, CentrifugalLoad):
            nodal, nodes, warning = _centrifugal(mesh, load, thickness_mm, density_kg_m3)
        elif isinstance(load, BearingLoad):
            raise SolverError(
                "A bearing load is a pin pressing on the wall of a bore, and a shell "
                "mid-surface has no bore wall — the hole through a shell is an edge, "
                "and the cosine distribution this load applies is defined over the "
                "half of a cylindrical surface facing the pin. Model the lug as a "
                "solid, or apply the pin's resultant as a force on the hole's edge."
            )
        else:  # pragma: no cover - the discriminated union makes this unreachable
            raise SolverError(f"unknown load type: {load!r}")

        if warning:
            warnings.append(f"{load.name or type(load).__name__}: {warning}")
        np.add.at(total, _dofs(nodes), nodal.ravel())

    return total, warnings


def _dofs(nodes: NDArray[np.int64]) -> NDArray[np.int64]:
    """The three DOF indices of each node, flattened."""
    return (3 * nodes[:, None] + np.arange(3)[None, :]).ravel()


def _force(
    mesh: ShellMesh, load: ForceLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    nodes = select_nodes(mesh, load.where)
    return distribute_force_over_shell(mesh, nodes, np.asarray(load.force_n, dtype=np.float64))


def _pressure(
    mesh: ShellMesh, load: PressureLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Uniform pressure along each face's own normal, integrated face by face.

    Face by face rather than as one resultant, for the reason the solid path
    gives: a curved surface's normal varies across it, so a pressure on a dome
    has a much smaller resultant than its integrated magnitude and treating it
    as a single vector invents a large spurious force.
    """
    nodes = select_nodes(mesh, load.where)
    faces = shell_faces_within(mesh, nodes)
    if len(faces) == 0:
        raise SolverError(
            "A pressure needs a surface to act on and this selection contains no "
            "complete shell face. Select a region covering whole faces rather than a "
            "band of nodes across them."
        )

    corner_factor, midside_factor = SHELL_SHAPE_INTEGRALS[mesh.element_type]
    # Positive pressure acts along -n, the same convention as the solid path.
    face_force = (-load.pressure_mpa * mesh.face_areas()[faces])[:, None] * face_normals(mesh)[
        faces
    ]

    per_node = np.zeros((mesh.node_count, 3), dtype=np.float64)
    np.add.at(per_node, mesh.faces[faces], (face_force * corner_factor)[:, None, :])
    if mesh.midside is not None:
        np.add.at(per_node, mesh.midside[faces], (face_force * midside_factor)[:, None, :])

    carriers = np.flatnonzero(np.abs(per_node).sum(axis=1) > 0.0)
    return per_node[carriers], carriers, None


def _moment(
    mesh: ShellMesh, load: MomentLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """A moment as a statically equivalent tangential force field.

    Identical in construction to the solid path's: the region's nodes each take
    a force perpendicular to the axis and to their own offset from the centroid,
    scaled so that the summed `r x F` is the requested moment. It is a *node*
    construction rather than an area one, so it transfers to a shell unchanged —
    which is why this is a near-copy rather than a call: the solid version is
    annotated `TetMesh` and reaches for nothing a shell lacks, but making it
    generic would put a `PointCloud` in `loads.py` whose other five functions
    all genuinely need a solid.
    """
    nodes = select_nodes(mesh, load.where)
    moment = np.asarray(load.moment_n_mm, dtype=np.float64)
    magnitude = float(np.linalg.norm(moment))
    if magnitude <= 0.0:
        raise SolverError("A moment of (0, 0, 0) applies nothing. Give a non-zero moment.")

    axis = moment / magnitude
    positions = mesh.nodes[nodes]
    if len(positions) == 0:
        raise SolverError(
            "A moment was applied to a region that selects no nodes. Widen the selector."
        )
    relative = positions - positions.mean(axis=0)
    perpendicular = relative - (relative @ axis)[:, None] * axis[None, :]
    lever = np.linalg.norm(perpendicular, axis=1)

    tangential = np.cross(np.broadcast_to(axis, perpendicular.shape), perpendicular)
    denominator = float(np.sum(lever**2))
    if denominator <= 0.0:
        raise SolverError(
            "Every node in the moment's region lies on its axis, so there is no lever "
            "arm to apply it through. Select a region that extends away from the axis."
        )
    return tangential * (magnitude / denominator), nodes, None


def _body_force(
    mesh: ShellMesh, per_face_force: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Spread a per-face force onto that face's nodes by the shape integrals.

    The same table the surface tractions use, rather than the corners-only split
    `loads._body_force` takes for a tet10. The solid path calls that a mild
    approximation and it is; here there is no reason to take it, because the
    table is already in hand and applying it costs one extra `np.add.at`.
    """
    corner_factor, midside_factor = SHELL_SHAPE_INTEGRALS[mesh.element_type]
    per_node = np.zeros((mesh.node_count, 3), dtype=np.float64)
    np.add.at(per_node, mesh.faces, (per_face_force * corner_factor)[:, None, :])
    if mesh.midside is not None:
        np.add.at(per_node, mesh.midside, (per_face_force * midside_factor)[:, None, :])
    nodes = np.arange(mesh.node_count, dtype=np.int64)
    return per_node, nodes, None


def _face_masses(
    mesh: ShellMesh, thickness_mm: float, density_kg_m3: float
) -> NDArray[np.float64]:
    """Mass of each face in tonnes: density x area x thickness.

    Area times thickness is where a shell body load differs from a solid one —
    a face has no volume of its own, and the thickness the mesh deliberately
    does not carry is what turns it into material.
    """
    density = density_kg_m3 * _DENSITY_KG_M3_TO_TONNE_MM3
    return density * mesh.face_areas() * float(thickness_mm)


def _gravity(
    mesh: ShellMesh, load: GravityLoad, thickness_mm: float, density_kg_m3: float
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    direction = np.asarray(load.direction, dtype=np.float64)
    norm = float(np.linalg.norm(direction))
    if norm <= 0.0:
        raise SolverError(
            "A gravity direction of (0, 0, 0) has no direction. The usual value is "
            "(0, 0, -1)."
        )
    masses = _face_masses(mesh, thickness_mm, density_kg_m3)
    face_force = (masses * load.magnitude_mm_s2)[:, None] * (direction / norm)[None, :]
    return _body_force(mesh, face_force)


def _centrifugal(
    mesh: ShellMesh, load: CentrifugalLoad, thickness_mm: float, density_kg_m3: float
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Rotation as a body load: rho * t * omega^2 * r outward from the axis."""
    omega = float(load.rpm) * 2.0 * np.pi / 60.0
    masses = _face_masses(mesh, thickness_mm, density_kg_m3)
    centroids = mesh.nodes[mesh.faces].mean(axis=1)
    perpendicular, _ = radial_offsets(centroids, load.axis_point, load.axis_direction)
    face_force = (masses * omega**2)[:, None] * perpendicular
    return _body_force(mesh, face_force)


__all__ = [
    "SHELL_SHAPE_INTEGRALS",
    "assemble_shell_loads",
    "distribute_force_over_shell",
    "face_normals",
    "shell_faces_within",
    "shell_node_weights",
]
