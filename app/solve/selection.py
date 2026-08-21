import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.types import BoxSelector, FaceSelector, Selector, SolverError

_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def select_nodes(mesh: TetMesh, selector: Selector) -> NDArray[np.int64]:
    """Resolve a selector to node indices. Raises if it matches nothing."""
    if isinstance(selector, FaceSelector):
        nodes = _select_face(mesh, selector)
        description = f"{selector.side} {selector.axis} face"
    elif isinstance(selector, BoxSelector):
        nodes = _select_box(mesh, selector)
        description = f"box {selector.min} to {selector.max}"
    else:  # pragma: no cover - the discriminated union makes this unreachable
        raise SolverError(f"unknown selector: {selector!r}")

    if len(nodes) == 0:
        raise SolverError(f"Selection matched no nodes: {description}")
    return nodes


def _select_face(mesh: TetMesh, selector: FaceSelector) -> NDArray[np.int64]:
    axis = _AXIS_INDEX[selector.axis]
    coords = mesh.nodes[:, axis]
    lo, hi = mesh.bounding_box
    span = float(hi[axis] - lo[axis])
    # A flat part has zero span on one axis; fall back to the diagonal so the
    # tolerance stays meaningful instead of collapsing to an exact comparison.
    if span <= 0.0:
        span = float(np.linalg.norm(hi - lo)) or 1.0
    band = span * selector.tolerance

    if selector.side == "min":
        return np.flatnonzero(coords <= coords.min() + band)
    return np.flatnonzero(coords >= coords.max() - band)


def _select_box(mesh: TetMesh, selector: BoxSelector) -> NDArray[np.int64]:
    lo = np.asarray(selector.min, dtype=np.float64)
    hi = np.asarray(selector.max, dtype=np.float64)
    if np.any(hi < lo):
        raise SolverError("Box selector has a max corner below its min corner")
    inside = np.all((mesh.nodes >= lo) & (mesh.nodes <= hi), axis=1)
    return np.flatnonzero(inside)


def surface_triangles_within(mesh: TetMesh, nodes: NDArray[np.int64]) -> NDArray[np.int64]:
    """Boundary triangles whose three corners are all in `nodes`."""
    membership = np.zeros(mesh.node_count, dtype=bool)
    membership[nodes] = True
    triangles = mesh.surface_triangles
    return triangles[membership[triangles].all(axis=1)]


def distribute_force(
    mesh: TetMesh, nodes: NDArray[np.int64], force: NDArray[np.float64]
) -> tuple[NDArray[np.float64], str | None]:
    """Spread a total force over selected nodes, weighted by tributary area.

    Returns (per-node force array aligned with `nodes`, warning or None). Area
    weighting keeps the applied load mesh-independent; if the selection has no
    complete surface triangles (e.g. a box picking interior nodes) it falls back
    to an equal split and says so.
    """
    triangles = surface_triangles_within(mesh, nodes)
    weights = np.zeros(mesh.node_count, dtype=np.float64)
    warning: str | None = None

    if len(triangles) > 0:
        p = mesh.nodes[triangles]
        areas = 0.5 * np.linalg.norm(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
        # Each triangle gives a third of its area to each of its corners.
        np.add.at(weights, triangles, (areas / 3.0)[:, None])

    total = weights[nodes].sum()
    if total <= 0.0:
        weights = np.zeros(mesh.node_count, dtype=np.float64)
        weights[nodes] = 1.0
        total = float(len(nodes))
        warning = (
            "Load region contains no complete surface facets; the force was split "
            "equally between its nodes instead of by area."
        )

    share = weights[nodes] / total
    return share[:, None] * force[None, :], warning
