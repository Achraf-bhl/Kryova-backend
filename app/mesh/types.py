"""Mesh data structures.

Node coordinates are in **millimetres** throughout, matching the CAD files they
come from. Combined with forces in N and moduli in MPa this is the standard
self-consistent mm-N-MPa system, so the solver needs no unit conversion: it
returns displacements in mm and stresses in MPa directly.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# The four triangular faces of a tet, each wound outward for a positive-volume tet.
_TET_FACES = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]], dtype=np.int64)


class MeshError(ValueError):
    """The mesh could not be generated, or is unusable for analysis."""


@dataclass
class TetMesh:
    """A linear (4-node) tetrahedral volume mesh."""

    nodes: NDArray[np.float64]  # (n_nodes, 3), millimetres
    tets: NDArray[np.int64]  # (n_tets, 4)
    _surface: NDArray[np.int64] | None = field(default=None, repr=False, compare=False)

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

        Also what the frontend renders — the volume mesh interior is never drawn.
        """
        if self._surface is None:
            self._surface = _extract_surface(self.tets)
        return self._surface

    def save(self, path: Path) -> None:
        np.savez_compressed(path, nodes=self.nodes, tets=self.tets)

    @classmethod
    def load(cls, path: Path) -> "TetMesh":
        with np.load(path) as data:
            return cls(nodes=data["nodes"], tets=data["tets"])


def _extract_surface(tets: NDArray[np.int64]) -> NDArray[np.int64]:
    faces = tets[:, _TET_FACES].reshape(-1, 3)
    # Sorting each face gives an orientation-independent key for matching pairs.
    keys = np.sort(faces, axis=1)
    _, index, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    return faces[index[counts == 1]]


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
        "element_type": "tet4",
        "volume_mm3": mesh.volume,
        "bounding_box_mm": {"min": lo.tolist(), "max": hi.tolist(), "size": (hi - lo).tolist()},
        "min_quality": float(shape.min()),
        "mean_quality": float(shape.mean()),
        "sliver_count": int((shape < 0.1).sum()),
        "inverted_count": int((volumes <= 0.0).sum()),
    }
