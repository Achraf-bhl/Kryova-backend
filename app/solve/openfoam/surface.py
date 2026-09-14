"""The duct's closed surface, split into inlet, outlet and wall.

snappyHexMesh meshes the *inside* of a closed triangulated surface, and names the
boundary patches after the STL's solids. So the whole geometric question — which
part of the part is the inlet — is answered here, once, with the selector
vocabulary, and written into the STL as three named solids. Nothing downstream
selects anything.

The surface comes from the job's own tet mesh: its boundary triangles are the
part's surface at the resolution it was meshed, and they are what
`app.solve.selection` already resolves selectors against. A triangle belongs to
the inlet when all three of its corners are selected by the inlet's selector,
exactly as a surface load does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import _TET_FACES, TetMesh
from app.solve.openfoam.case import FlowCase
from app.solve.selection import select_nodes, surface_triangles_within
from app.solve.types import Selector, SolverError

INLET: Final = "inlet"
OUTLET: Final = "outlet"
WALL: Final = "wall"
REGIONS: Final = (INLET, OUTLET, WALL)

#: Cells across the inlet's hydraulic diameter below which a run is refused. The
#: laminar profile is a parabola; resolved by fewer cells than this it is a
#: staircase, and the pressure drop error stops being small (16 across gave 0.2 %
#: on a pipe, measured on this image — see tests/test_solver_openfoam.py).
MIN_CELLS_ACROSS: Final = 8


@dataclass(frozen=True)
class DuctSurface:
    """A closed surface, outward-wound, with a region per triangle."""

    nodes: NDArray[np.float64]  # (n, 3) mm
    triangles: NDArray[np.int64]  # (m, 3), outward-wound
    regions: NDArray[np.int64]  # (m,) index into REGIONS
    #: A point strictly inside the fluid, for snappyHexMesh's `locationInMesh`.
    inside: tuple[float, float, float]

    def __post_init__(self) -> None:
        present = {REGIONS[i] for i in np.unique(self.regions)}
        for needed in (INLET, OUTLET):
            if needed not in present:
                raise SolverError(f"The duct surface has no {needed} triangles.")

    def normals(self) -> NDArray[np.float64]:
        p = self.nodes[self.triangles]
        return np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])  # length = 2 × area

    def areas(self) -> NDArray[np.float64]:
        return 0.5 * np.linalg.norm(self.normals(), axis=1)

    def area_of(self, region: str) -> float:
        return float(self.areas()[self.regions == REGIONS.index(region)].sum())

    def inward_direction(self, region: str) -> NDArray[np.float64]:
        """Unit vector into the duct, area-averaged over a region's triangles."""
        total = self.normals()[self.regions == REGIONS.index(region)].sum(axis=0)
        length = float(np.linalg.norm(total))
        if length == 0.0:
            raise SolverError(
                f"The {region} faces point in every direction at once, so there is no one "
                "direction for the flow to enter or leave by. Select a single flat face."
            )
        return np.asarray(-total / length, dtype=np.float64)

    def perimeter_of(self, region: str) -> float:
        """Length of the region's boundary: edges used by exactly one of its triangles."""
        tris = self.triangles[self.regions == REGIONS.index(region)]
        edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
        unique, counts = np.unique(edges, axis=0, return_counts=True)
        rim = unique[counts == 1]
        return float(np.linalg.norm(self.nodes[rim[:, 0]] - self.nodes[rim[:, 1]], axis=1).sum())

    def hydraulic_diameter(self, region: str = INLET) -> float:
        """4A/P of a region — the length a duct's Reynolds number is defined on."""
        return 4.0 * self.area_of(region) / self.perimeter_of(region)

    def stl(self) -> str:
        """ASCII STL with one named solid per region, the names snappyHexMesh patches take."""
        normals = self.normals()
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        unit = normals / np.where(lengths == 0.0, 1.0, lengths)
        out: list[str] = []
        for index, name in enumerate(REGIONS):
            out.append(f"solid {name}\n")
            for row in np.flatnonzero(self.regions == index):
                n = unit[row]
                out.append(f"  facet normal {n[0]:.12g} {n[1]:.12g} {n[2]:.12g}\n    outer loop\n")
                for corner in self.nodes[self.triangles[row]]:
                    out.append(f"      vertex {corner[0]:.12g} {corner[1]:.12g} {corner[2]:.12g}\n")
                out.append("    endloop\n  endfacet\n")
            out.append(f"endsolid {name}\n")
        return "".join(out)


def duct_surface(mesh: TetMesh, case: FlowCase) -> DuctSurface:
    """The tet mesh's boundary, outward-wound, with inlet and outlet resolved by selector."""
    faces = mesh.tets[:, _TET_FACES].reshape(-1, 3)
    keys = np.sort(faces, axis=1)
    _, first, counts = np.unique(keys, axis=0, return_index=True, return_counts=True)
    boundary = first[counts == 1]
    triangles = faces[boundary].copy()
    # `_TET_FACES` winds outward on a positively oriented tet; flip where the
    # owning tet is inverted, so an inverted element cannot turn a wall inside out.
    owners = boundary // 4
    inverted = mesh.signed_volumes()[owners] < 0.0
    triangles[inverted] = triangles[inverted][:, ::-1]

    sorted_boundary = keys[boundary]

    def region(selector: Selector, name: str) -> NDArray[np.bool_]:
        chosen = surface_triangles_within(mesh, select_nodes(mesh, selector))
        mask = _rows_in(sorted_boundary, np.sort(chosen, axis=1))
        if not mask.any():
            raise SolverError(
                f"The {name} selector matched nodes but no whole boundary face. Select a face "
                "of the duct rather than an edge or a point."
            )
        return np.asarray(mask)

    inlet = region(case.inlet.where, INLET)
    outlet = region(case.outlet.where, OUTLET)
    overlap = inlet & outlet
    if overlap.any():
        raise SolverError(
            f"The inlet and outlet selectors both claim {int(overlap.sum())} boundary face(s). "
            "A face cannot both let fluid in and let it out: select them apart."
        )
    regions = np.full(len(triangles), REGIONS.index(WALL), dtype=np.int64)
    regions[inlet] = REGIONS.index(INLET)
    regions[outlet] = REGIONS.index(OUTLET)
    return DuctSurface(nodes=mesh.nodes, triangles=triangles, regions=regions, inside=_inside(mesh))


def _rows_in(rows: NDArray[np.int64], candidates: NDArray[np.int64]) -> NDArray[np.bool_]:
    """Which rows of `rows` appear among `candidates`, comparing whole rows."""
    if len(candidates) == 0:
        return np.zeros(len(rows), dtype=bool)
    width = np.dtype((np.void, rows.dtype.itemsize * rows.shape[1]))
    as_void = np.ascontiguousarray(rows).view(width).ravel()
    wanted = np.ascontiguousarray(candidates.astype(rows.dtype)).view(width).ravel()
    return np.asarray(np.isin(as_void, wanted))


def _inside(mesh: TetMesh) -> tuple[float, float, float]:
    """A point inside the largest tet, nudged off its centroid.

    snappyHexMesh refuses a `locationInMesh` that lies on a face of its background
    grid, and a centroid of a structured mesh can land on one exactly. The nudge is
    a small, irrational-looking fraction of the tet's own size, so it stays inside it.
    """
    volumes = np.abs(mesh.signed_volumes())
    tet = int(np.argmax(volumes))
    corners = mesh.nodes[mesh.tets[tet, :4]]
    weights = np.array([0.2613, 0.2381, 0.2459, 0.2547])
    point = weights @ corners
    return (float(point[0]), float(point[1]), float(point[2]))


__all__ = ["INLET", "MIN_CELLS_ACROSS", "OUTLET", "REGIONS", "WALL", "DuctSurface", "duct_surface"]
