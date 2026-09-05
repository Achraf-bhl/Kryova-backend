"""Turning a `Load` into the nodal force vector the solver assembles.

One function per load type, dispatched by `assemble_loads`. It lives apart from
`linear_static` because the two answer different questions — "what forces act"
and "how does the structure respond" — and because the load vocabulary is the
half that keeps growing.

The invariant every one of these preserves: **refining the mesh must not change
the applied load.** A total force is spread by tributary area, a pressure is
integrated over real facet areas, a body load is integrated over element
volumes. Splitting anything equally between nodes would make the answer depend
on where the mesher happened to put them, which is the classic way an FEA result
becomes quietly mesh-dependent.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.selection import (
    distribute_force,
    radial_offsets,
    select_nodes,
    surface_triangles_within,
)
from app.solve.types import (
    BearingLoad,
    CentrifugalLoad,
    CylinderSelector,
    ForceLoad,
    GravityLoad,
    Load,
    MomentLoad,
    PressureLoad,
    SolverError,
)

#: Convert kg/m³ to the solver's mm-N-MPa system: tonne per mm³.
#:
#: The factor is 1e-12, and it is the one place in the solver where a unit
#: conversion is unavoidable — the material library is in kg/m³ because that is
#: how datasheets quote density, and a body force must come out in N/mm³. Doing
#: it here once, named, is the alternative to doing it inline three times.
_DENSITY_KG_M3_TO_TONNE_MM3 = 1e-12


def assemble_loads(
    mesh: TetMesh, loads: list[Load], density_kg_m3: float
) -> tuple[NDArray[np.float64], list[str]]:
    """Total nodal force vector for every load, and any warnings raised.

    Returns a flat (3 * node_count,) array in the solver's DOF ordering.
    """
    total = np.zeros(3 * mesh.node_count, dtype=np.float64)
    warnings: list[str] = []

    for load in loads:
        if isinstance(load, ForceLoad):
            nodal, nodes, warning = _force(mesh, load)
        elif isinstance(load, PressureLoad):
            nodal, nodes, warning = _pressure(mesh, load)
        elif isinstance(load, MomentLoad):
            nodal, nodes, warning = _moment(mesh, load)
        elif isinstance(load, BearingLoad):
            nodal, nodes, warning = _bearing(mesh, load)
        elif isinstance(load, GravityLoad):
            nodal, nodes, warning = _gravity(mesh, load, density_kg_m3)
        elif isinstance(load, CentrifugalLoad):
            nodal, nodes, warning = _centrifugal(mesh, load, density_kg_m3)
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
    mesh: TetMesh, load: ForceLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    nodes = select_nodes(mesh, load.where)
    nodal, warning = distribute_force(
        mesh, nodes, np.asarray(load.force_n, dtype=np.float64)
    )
    return nodal, nodes, warning


def _facet_areas_and_normals(
    mesh: TetMesh, nodes: NDArray[np.int64]
) -> tuple[NDArray[np.int64], NDArray[np.float64], NDArray[np.float64]]:
    """Selected boundary triangles, their areas, and their outward unit normals.

    The normals come from the surface triangles' own winding, which the mesher
    orients outward. That orientation is what makes a positive pressure push
    *into* the material without the caller having to say which way that is.
    """
    triangles = surface_triangles_within(mesh, nodes)
    if len(triangles) == 0:
        return triangles, np.zeros(0), np.zeros((0, 3))
    corners = mesh.nodes[triangles]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    magnitude = np.linalg.norm(cross, axis=1)
    areas = 0.5 * magnitude
    # A degenerate facet has zero area and no defined normal; it also carries no
    # load, so guarding the division keeps it at zero rather than producing NaN
    # that would poison the whole solve.
    safe = np.where(magnitude > 0.0, magnitude, 1.0)
    return triangles, areas, cross / safe[:, None]


def _pressure(
    mesh: TetMesh, load: PressureLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Uniform pressure along each facet's own outward normal.

    Integrated facet by facet rather than applied as one resultant, because a
    curved surface's normal varies across it — a pressure on a bore produces no
    net force at all, and treating it as a single vector would produce a large
    spurious one.
    """
    nodes = select_nodes(mesh, load.where)
    triangles, areas, normals = _facet_areas_and_normals(mesh, nodes)
    if len(triangles) == 0:
        raise SolverError(
            "A pressure needs a surface to act on and this selection has no complete "
            "surface facets. Select a face rather than a box of interior nodes."
        )

    # Positive pressure pushes inward, so it acts along -n.
    facet_force = (-load.pressure_mpa * areas)[:, None] * normals
    per_node = np.zeros((mesh.node_count, 3), dtype=np.float64)
    np.add.at(per_node, triangles, (facet_force / 3.0)[:, None, :])
    return per_node[nodes], nodes, None


def _moment(
    mesh: TetMesh, load: MomentLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """A moment as a statically equivalent tangential force field.

    Each node gets a force perpendicular to both the moment axis and its own
    offset from the centroid, scaled by that offset. Summing r x F over the
    region then reproduces the requested moment exactly, and the net force is
    zero by symmetry of the construction.
    """
    nodes = select_nodes(mesh, load.where)
    moment = np.asarray(load.moment_n_mm, dtype=np.float64)
    magnitude = float(np.linalg.norm(moment))
    if magnitude <= 0.0:
        raise SolverError("A moment of (0, 0, 0) applies nothing. Give a non-zero moment.")

    axis = moment / magnitude
    positions = mesh.nodes[nodes]
    centroid = positions.mean(axis=0)
    relative = positions - centroid
    # Only the part perpendicular to the axis contributes a moment about it.
    perpendicular = relative - (relative @ axis)[:, None] * axis[None, :]
    lever = np.linalg.norm(perpendicular, axis=1)

    tangential = np.cross(np.broadcast_to(axis, perpendicular.shape), perpendicular)
    # Scale so that sum(r x F) == the requested moment. The denominator is
    # sum(r^2); when every node sits on the axis there is no lever arm and no
    # force distribution can produce the moment.
    denominator = float(np.sum(lever**2))
    if denominator <= 0.0:
        raise SolverError(
            "Every node in the moment's region lies on its axis, so there is no lever "
            "arm to apply it through. Select a region that extends away from the axis."
        )
    return tangential * (magnitude / denominator), nodes, None


def _bearing(
    mesh: TetMesh, load: BearingLoad
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """A pin's load on a bore, as a cosine-distributed bearing pressure.

    Only the half of the bore facing the load carries any of it, weighted by
    cos^n of the angle from the load direction. That is what makes the peak
    pressure land at the contact point, which is where a lug actually fails —
    a uniform distribution over the whole bore understates it by roughly a
    factor of two and moves the peak somewhere else entirely.
    """
    nodes = select_nodes(mesh, load.where)
    force = np.asarray(load.force_n, dtype=np.float64)
    magnitude = float(np.linalg.norm(force))
    if magnitude <= 0.0:
        raise SolverError("A bearing load of (0, 0, 0) applies nothing.")
    direction = force / magnitude

    where = load.where
    # By type, not by hasattr: the cylinder is the only selector carrying an
    # axis, and asking for the attribute would accept anything that happened to
    # grow one later while telling neither the reader nor the type checker which
    # selector this actually requires.
    if not isinstance(where, CylinderSelector):
        raise SolverError(
            "A bearing load must be applied to a cylinder selector — it is the bore's "
            "axis that decides which half of the surface carries the load."
        )
    perpendicular, _ = radial_offsets(mesh.nodes[nodes], where.axis_point, where.axis_direction)
    radius = np.linalg.norm(perpendicular, axis=1)
    safe = np.where(radius > 0.0, radius, 1.0)
    radial = perpendicular / safe[:, None]

    # cos of the angle between each node's radial direction and the load.
    #
    # The sign here is the thing to get right, and it is easy to get backwards.
    # A pin pushing the lug in +x contacts the bore wall on the +x side: that is
    # the material the pin presses against. Those nodes have radial = +x, so the
    # loaded half is where `radial . direction` is *positive*.
    #
    # Reasoning about the surface normal instead inverts it — a bore's outward
    # normal points into the hole, i.e. along -radial — which is exactly the
    # mistake this comment exists to stop the next reader making.
    alignment = radial @ direction
    weight = np.clip(alignment, 0.0, None) ** load.distribution
    total = float(weight.sum())
    if total <= 0.0:
        raise SolverError(
            "No part of the selected bore faces the load direction. Check the "
            "cylinder's axis and the sign of the force."
        )
    return (weight / total)[:, None] * force[None, :], nodes, None


def _element_volumes(mesh: TetMesh) -> NDArray[np.float64]:
    """Signed-corrected volume of every tetrahedron, in mm³."""
    corners = mesh.nodes[mesh.tets]
    edges = corners[:, 1:4] - corners[:, 0:1]
    return np.abs(np.linalg.det(edges)) / 6.0


def _body_force(
    mesh: TetMesh, per_element_force: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    """Spread a per-element force vector onto that element's four corners.

    A quarter each: the exact consistent load vector for a constant body force
    over a linear tetrahedron. For a tet10 mesh this puts the load on the corner
    nodes rather than the midside ones, which is a mild approximation and, for a
    body load, an unimportant one — unlike a surface traction, where it matters
    and `distribute_force` handles it properly.
    """
    per_node = np.zeros((mesh.node_count, 3), dtype=np.float64)
    np.add.at(per_node, mesh.tets, (per_element_force / 4.0)[:, None, :])
    nodes = np.arange(mesh.node_count, dtype=np.int64)
    return per_node, nodes


def _gravity(
    mesh: TetMesh, load: GravityLoad, density_kg_m3: float
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    direction = np.asarray(load.direction, dtype=np.float64)
    norm = float(np.linalg.norm(direction))
    if norm <= 0.0:
        raise SolverError(
            "A gravity direction of (0, 0, 0) has no direction. The usual value is "
            "(0, 0, -1)."
        )
    density = density_kg_m3 * _DENSITY_KG_M3_TO_TONNE_MM3
    volumes = _element_volumes(mesh)
    # rho * V * a, in N.
    element_force = (density * volumes * load.magnitude_mm_s2)[:, None] * (direction / norm)[None, :]
    per_node, nodes = _body_force(mesh, element_force)
    return per_node, nodes, None


def _centrifugal(
    mesh: TetMesh, load: CentrifugalLoad, density_kg_m3: float
) -> tuple[NDArray[np.float64], NDArray[np.int64], str | None]:
    """Rotation as a body load: rho * omega^2 * r outward from the axis.

    Quadratic in speed, which is the thing to remember when scaling a design:
    doubling the rpm quadruples the load.
    """
    omega = float(load.rpm) * 2.0 * np.pi / 60.0
    density = density_kg_m3 * _DENSITY_KG_M3_TO_TONNE_MM3
    volumes = _element_volumes(mesh)

    centroids = mesh.nodes[mesh.tets].mean(axis=1)
    perpendicular, _ = radial_offsets(centroids, load.axis_point, load.axis_direction)
    element_force = (density * volumes * omega**2)[:, None] * perpendicular
    per_node, nodes = _body_force(mesh, element_force)
    return per_node, nodes, None
