"""Mesh data structures.

Node coordinates are in **millimetres** throughout, matching the CAD files they
come from. Combined with forces in N and moduli in MPa this is the standard
self-consistent mm-N-MPa system, so the solver needs no unit conversion: it
returns displacements in mm and stresses in MPa directly.

One class covers both element orders. A tet4 mesh has `midside=None`; a tet10
mesh adds six midside nodes per element and keeps the same four corners, so
everything geometric -- volume, bounding box, the boundary triangles the viewer
draws -- is computed from the corners and reads identically either way.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# The four triangular faces of a tet, each wound outward for a positive-volume tet.
_TET_FACES = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]], dtype=np.int64)

# The three midside nodes of each of those faces, as local indices 0..5 into a
# tet's `midside` row. Entry i of a face is the node between its corner i and
# corner i+1, so the ordering matches _TET_FACES. Derived from TET10_EDGES
# below, and checked against it by `test_mesh`.
_TET_FACE_MIDSIDES = np.array([[2, 1, 0], [0, 5, 3], [1, 4, 5], [3, 4, 2]], dtype=np.int64)

# Which corner pair each midside node sits between, in local node order 4..9.
# This is gmsh's own ordering for its 10-node tetrahedron (element type 11) and
# is the single source of truth: `solve.linear_static` builds its shape
# functions from it, and `_extract` in the mesher asserts gmsh still agrees.
TET10_EDGES: tuple[tuple[int, int], ...] = (
    (0, 1),
    (1, 2),
    (0, 2),
    (0, 3),
    (2, 3),
    (1, 3),
)


class MeshError(ValueError):
    """The mesh could not be generated, or is unusable for analysis."""


@dataclass
class TetMesh:
    """A tetrahedral volume mesh, linear (tet4) or quadratic (tet10)."""

    nodes: NDArray[np.float64]  # (n_nodes, 3), millimetres
    tets: NDArray[np.int64]  # (n_tets, 4) corner nodes
    midside: NDArray[np.int64] | None = None  # (n_tets, 6), see TET10_EDGES
    _surface: NDArray[np.int64] | None = field(default=None, repr=False, compare=False)
    _surface_midside: NDArray[np.int64] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.nodes = np.ascontiguousarray(self.nodes, dtype=np.float64)
        self.tets = np.ascontiguousarray(self.tets, dtype=np.int64)
        if self.nodes.ndim != 2 or self.nodes.shape[1] != 3:
            raise MeshError(f"nodes must have shape (n, 3), got {self.nodes.shape}")
        if self.tets.ndim != 2 or self.tets.shape[1] != 4:
            raise MeshError(f"tets must have shape (m, 4), got {self.tets.shape}")
        if len(self.tets) == 0:
            raise MeshError("mesh contains no tetrahedra")
        if self.tets.max(initial=-1) >= len(self.nodes):
            raise MeshError("tet references a node index outside the node array")

        if self.midside is None:
            return
        self.midside = np.ascontiguousarray(self.midside, dtype=np.int64)
        if self.midside.shape != (len(self.tets), 6):
            raise MeshError(
                f"midside must have shape ({len(self.tets)}, 6), got {self.midside.shape}"
            )
        if self.midside.max(initial=-1) >= len(self.nodes) or self.midside.min(initial=0) < 0:
            raise MeshError("midside node index is outside the node array")

    @property
    def element_order(self) -> int:
        """1 for tet4, 2 for tet10."""
        return 1 if self.midside is None else 2

    @property
    def element_type(self) -> str:
        return "tet4" if self.midside is None else "tet10"

    @property
    def connectivity(self) -> NDArray[np.int64]:
        """Every node of every element: (n_tets, 4) or (n_tets, 10)."""
        if self.midside is None:
            return self.tets
        return np.ascontiguousarray(np.hstack([self.tets, self.midside]))

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def tet_count(self) -> int:
        return len(self.tets)

    def signed_volumes(self) -> NDArray[np.float64]:
        """Signed volume of every tet, in mm^3. Negative means inverted winding."""
        p = self.nodes[self.tets]
        return np.linalg.det(p[:, 1:] - p[:, :1]) / 6.0

    @property
    def volume(self) -> float:
        return float(np.abs(self.signed_volumes()).sum())

    @property
    def bounding_box(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return self.nodes.min(axis=0), self.nodes.max(axis=0)

    @property
    def surface_triangles(self) -> NDArray[np.int64]:
        """Boundary triangles: the faces belonging to exactly one tet.

        Corner nodes only, whatever the element order — this is also what the
        frontend renders, and the volume mesh interior is never drawn.
        """
        self._extract_surface()
        assert self._surface is not None
        return self._surface

    @property
    def surface_midside_nodes(self) -> NDArray[np.int64] | None:
        """The three midside nodes of each boundary triangle, or None for tet4.

        Row-aligned with `surface_triangles`. Needed to put a surface load on a
        quadratic face: the consistent nodal load for a 6-node triangle lives
        entirely on its midside nodes.
        """
        self._extract_surface()
        return self._surface_midside

    def _extract_surface(self) -> None:
        if self._surface is not None:
            return
        faces = self.tets[:, _TET_FACES].reshape(-1, 3)
        # Sorting each face gives an orientation-independent key for matching pairs.
        keys = np.sort(faces, axis=1)
        _, index, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
        boundary = index[counts == 1]
        self._surface = faces[boundary]
        if self.midside is not None:
            self._surface_midside = self.midside[:, _TET_FACE_MIDSIDES].reshape(-1, 3)[boundary]

    def save(self, path: Path) -> None:
        if self.midside is None:
            np.savez_compressed(path, nodes=self.nodes, tets=self.tets)
        else:
            np.savez_compressed(path, nodes=self.nodes, tets=self.tets, midside=self.midside)

    @classmethod
    def load(cls, path: Path) -> "TetMesh":
        with np.load(path) as data:
            return cls(
                nodes=data["nodes"],
                tets=data["tets"],
                midside=data["midside"] if "midside" in data else None,
            )


def quality(mesh: TetMesh) -> dict[str, Any]:
    """Per-mesh quality summary.

    The shape measure is 12 * (3V)^(2/3) / sum(edge^2), which is 1.0 for a regular
    tetrahedron and approaches 0 for a degenerate sliver. Below ~0.1 a linear
    static result should not be trusted.
    """
    volumes = mesh.signed_volumes()
    p = mesh.nodes[mesh.tets]
    pairs = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
    edge_sq = sum(((p[:, a] - p[:, b]) ** 2).sum(axis=1) for a, b in pairs)

    with np.errstate(divide="ignore", invalid="ignore"):
        shape = 12.0 * (3.0 * np.abs(volumes)) ** (2.0 / 3.0) / edge_sq
    shape = np.nan_to_num(shape, nan=0.0, posinf=0.0, neginf=0.0)

    lo, hi = mesh.bounding_box
    return {
        "node_count": mesh.node_count,
        "element_count": mesh.tet_count,
        "element_type": mesh.element_type,
        "volume_mm3": mesh.volume,
        "bounding_box_mm": {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist()},
        "min_quality": float(shape.min()),
        "mean_quality": float(shape.mean()),
        "sliver_count": int((shape < 0.1).sum()),
        "inverted_count": int((volumes <= 0.0).sum()),
    }
