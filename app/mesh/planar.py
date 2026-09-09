"""Triangular surface meshes, for models posed in a plane.

A plane model is not a thin solid. Plane stress and plane strain are different
*idealisations* — each assumes something about the out-of-plane direction that
the mesh cannot represent and the solver must supply — so they get their own
mesh type rather than a `TetMesh` with one element through the thickness.

**Nodes are (n, 3) with z = 0, deliberately.** The obvious alternative is (n, 2),
and it would immediately fork every geometric selector in
`app.solve.selection`: `BoxSelector`, `CylinderSelector`,
`EllipticalWallSelector` and the rest all read `mesh.nodes` as three columns.
Carrying a zero column costs one float per node and lets the whole region
vocabulary — which Decision 2 calls the real asset here — work on a plane model
unchanged. The cost is that the third column is meaningless and must stay zero;
`__post_init__` refuses a mesh that has drifted off the plane, because a
selector answering about `z` on a mesh that is secretly a shell would be wrong
in a way nothing else would notice.

Like `TetMesh`, one class covers both element orders: a tri3 mesh has
`midside=None`, a tri6 mesh adds three midside nodes per triangle and keeps the
same three corners, so every geometric quantity is computed from the corners
and reads identically either way.
"""

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import MeshError

# Which corner pair each midside node sits between, in local node order 3..5.
# This is gmsh's own ordering for its 6-node triangle (element type 9) and is
# the single source of truth: `solve.plane` builds its shape functions from it,
# and the mesher asserts gmsh still agrees.
TRI6_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1),
    (1, 2),
    (0, 2),
)

# The three edges of a triangle as local corner pairs, wound consistently, with
# the midside node that sits on each. Derived from TRI6_EDGES so the two orders
# cannot drift; `_EDGE_MIDSIDE[i]` is the local midside index for `_EDGES[i]`.
_EDGES = np.array([[0, 1], [1, 2], [2, 0]], dtype=np.int64)
_EDGE_MIDSIDE = np.array([0, 1, 2], dtype=np.int64)

#: How far off the z = 0 plane a node may sit before the mesh is refused, in mm.
#: Not a modelling tolerance — it exists only to absorb the arithmetic a mesher
#: does when it places a node that is exactly on the plane.
_PLANARITY_TOLERANCE_MM = 1e-6


@dataclass
class TriMesh:
    """A triangular mesh of a region of the z = 0 plane, linear or quadratic."""

    nodes: NDArray[np.float64]  # (n_nodes, 3), millimetres, z == 0
    tris: NDArray[np.int64]  # (n_tris, 3) corner nodes
    midside: NDArray[np.int64] | None = None  # (n_tris, 3), see TRI6_EDGES
    _boundary: NDArray[np.int64] | None = field(default=None, repr=False, compare=False)
    _boundary_midside: NDArray[np.int64] | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.nodes = np.ascontiguousarray(self.nodes, dtype=np.float64)
        self.tris = np.ascontiguousarray(self.tris, dtype=np.int64)
        if self.nodes.ndim != 2 or self.nodes.shape[1] != 3:
            raise MeshError(f"nodes must have shape (n, 3), got {self.nodes.shape}")
        if self.tris.ndim != 2 or self.tris.shape[1] != 3:
            raise MeshError(f"tris must have shape (m, 3), got {self.tris.shape}")
        if len(self.tris) == 0:
            raise MeshError("mesh contains no triangles")
        if self.tris.max(initial=-1) >= len(self.nodes):
            raise MeshError("triangle references a node index outside the node array")

        out_of_plane = float(np.abs(self.nodes[:, 2]).max(initial=0.0))
        if out_of_plane > _PLANARITY_TOLERANCE_MM:
            raise MeshError(
                f"A plane mesh must lie in z = 0; a node sits {out_of_plane:g} mm off it. "
                "The third coordinate is carried so the geometric selectors keep working "
                "and is not a shell: a curved surface needs a shell element, which this "
                "codebase does not have."
            )

        if self.midside is None:
            return
        self.midside = np.ascontiguousarray(self.midside, dtype=np.int64)
        if self.midside.shape != (len(self.tris), 3):
            raise MeshError(
                f"midside must have shape ({len(self.tris)}, 3), got {self.midside.shape}"
            )
        if self.midside.max(initial=-1) >= len(self.nodes) or self.midside.min(initial=0) < 0:
            raise MeshError("midside node index is outside the node array")

    @property
    def element_order(self) -> int:
        """1 for tri3, 2 for tri6."""
        return 1 if self.midside is None else 2

    @property
    def element_type(self) -> str:
        return "tri3" if self.midside is None else "tri6"

    @property
    def connectivity(self) -> NDArray[np.int64]:
        """Every node of every element: (n_tris, 3) or (n_tris, 6)."""
        if self.midside is None:
            return self.tris
        return np.ascontiguousarray(np.hstack([self.tris, self.midside]))

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def element_count(self) -> int:
        return len(self.tris)

    def signed_areas(self) -> NDArray[np.float64]:
        """Signed area of every triangle in mm^2, positive for anticlockwise
        winding seen from +z. Negative means the triangle is wound the other
        way, which is legal geometry and a broken element: the plane
        strain-displacement matrix divides by twice this."""
        p = self.nodes[self.tris]
        u = p[:, 1, :2] - p[:, 0, :2]
        v = p[:, 2, :2] - p[:, 0, :2]
        return 0.5 * (u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0])

    @property
    def area(self) -> float:
        """Total meshed area in mm^2."""
        return float(np.abs(self.signed_areas()).sum())

    @property
    def bounding_box(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return self.nodes.min(axis=0), self.nodes.max(axis=0)

    @property
    def boundary_edges(self) -> NDArray[np.int64]:
        """Corner pairs on the boundary: the edges belonging to exactly one
        triangle. (n_edges, 2), each wound as its owning triangle wound it, so
        the outward normal can be recovered from the winding.

        The 2-D counterpart of `TetMesh.surface_triangles`, and it exists for
        the same reason: a traction on an edge is distributed by tributary
        *length*, so the edges have to be identified before a pressure can be
        applied mesh-independently.
        """
        self._build_boundary()
        assert self._boundary is not None
        return self._boundary

    @property
    def boundary_edge_midsides(self) -> NDArray[np.int64] | None:
        """The midside node of each boundary edge, aligned with
        `boundary_edges`, or None on a tri3 mesh."""
        if self.midside is None:
            return None
        self._build_boundary()
        return self._boundary_midside

    def _build_boundary(self) -> None:
        if self._boundary is not None:
            return
        edges = self.tris[:, _EDGES]  # (n_tris, 3, 2)
        flat = edges.reshape(-1, 2)
        keys = np.sort(flat, axis=1)
        _, index, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
        once = index[counts == 1]
        once.sort()  # keep the mesh's own order, so the answer is reproducible
        self._boundary = np.ascontiguousarray(flat[once])
        if self.midside is None:
            self._boundary_midside = None
            return
        mids = self.midside[:, _EDGE_MIDSIDE].reshape(-1)
        self._boundary_midside = np.ascontiguousarray(mids[once])


def planar_quality(mesh: TriMesh) -> dict[str, float | int | str]:
    """Quality summary for a triangular mesh.

    `min_quality` is the normalised shape factor `4*sqrt(3)*A / (l1^2+l2^2+l3^2)`,
    which is 1 for an equilateral triangle and 0 for a degenerate one — the 2-D
    analogue of the radius ratio `app.mesh.types.quality` reports for tets, and
    on the same 0-to-1 scale so a report can print either without a legend.
    """
    p = mesh.nodes[mesh.tris][:, :, :2]
    areas = np.abs(mesh.signed_areas())
    sides = np.stack(
        [
            np.sum((p[:, 1] - p[:, 0]) ** 2, axis=1),
            np.sum((p[:, 2] - p[:, 1]) ** 2, axis=1),
            np.sum((p[:, 0] - p[:, 2]) ** 2, axis=1),
        ],
        axis=1,
    )
    perimeter_sq = sides.sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        shape = np.where(perimeter_sq > 0.0, 4.0 * np.sqrt(3.0) * areas / perimeter_sq, 0.0)
    # `shape.min()` rather than `shape.min(initial=0.0)`: numpy's `initial` is a
    # seed value the reduction starts from, not a fallback for an empty array, so
    # `initial=0.0` pins every answer at 0 and the report says "worst element is
    # degenerate" about a perfectly good mesh. `__post_init__` refuses an empty
    # mesh, so there is always at least one element to take the minimum of.
    return {
        "element_type": mesh.element_type,
        "element_count": int(mesh.element_count),
        "node_count": int(mesh.node_count),
        "area_mm2": float(mesh.area),
        "min_quality": float(shape.min()),
        "mean_quality": float(shape.mean()),
        "sliver_count": int((shape < 0.1).sum()),
    }
