"""Shell and beam meshes — master plan 6.3.

`types.TetMesh` is a volume. These two are the other things a machine is made of:
a surface with a thickness the mesh does not carry, and a line with a cross
section the mesh does not carry. Both are geometry only — thickness and profile
live on `app/solve/sections.py`, for the same reason `TetMesh` carries no
material: one mesh is legitimately solved at several thicknesses, and a mesh that
owned its section could not be.

Node coordinates are in **millimetres**, as everywhere else here.

**A `ShellMesh` now has a producer; a `BeamMesh` still does not.**
`app.mesh.gmsh_mesher.generate_shell_mesh` meshes a curved surface into one, in
all four element types (2026-09-09). A `BeamMesh` is still authored — by a test,
or by a caller that knows where its nodes go — because gmsh's 1-D mesher is not
wired. That asymmetry is worth stating, because it is what the ordering question
below now turns on.

**The midside ordering is defined here to be CalculiX's, deliberately.** For
tets it is not, and cannot be: `TET10_EDGES` is *gmsh's* order for its 10-node
tet, gmsh is the producer, and `app/solve/calculix/deck.py` permutes at the
boundary — a permutation whose absence swaps two nodes on every element and
still solves. When these tables were written there was no producer to disagree
with, so adopting the solver's order at the source removed a permutation that
nobody could ever have watched fail, and the debt was named: a future gmsh shell
mesher must permute at the boundary rather than reorder these tables.

**That debt came due and was settled by measurement, not by argument.** Against
gmsh 4.15.2 the permutation is the **identity** for both shapes: gmsh numbers a
6-node triangle's midside nodes 0-1, 1-2, 0-2 against `SHELL_TRI_EDGES`' 0-1,
1-2, 2-0, and those are the same three node *pairs* with the last written the
other way round, which selects the same node; the 8-node quadrilateral agrees
outright. So the mesher writes these rows through unchanged — and
`_assert_shell_midside_ordering` re-checks it by coordinate on every quadratic
mesh, so a gmsh release that renumbered would fail loudly instead of returning a
plausible, wrong stiffness matrix. **Do not "restore" a permutation here**; the
identity is checked, not assumed. The beam half of the debt is still open and
still theoretical, for want of a beam mesher.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import MeshError

#: Which corner pair each midside node of a 6-node triangle sits between, in
#: local node order 3..5 (0-based). CalculiX's S6 numbering: the midside nodes
#: follow the three corners, each between consecutive corners. [M]
SHELL_TRI_EDGES: Final[tuple[tuple[int, int], ...]] = ((0, 1), (1, 2), (2, 0))

#: The same for an 8-node quadrilateral (S8/S8R), local node order 4..7. [M]
SHELL_QUAD_EDGES: Final[tuple[tuple[int, int], ...]] = ((0, 1), (1, 2), (2, 3), (3, 0))

#: Where the middle node of a 3-node beam (B32) sits: between the two ends.
#: Written as a table of one for the same reason the two above are tables — so
#: the fact is a value a test can read rather than a sentence in a docstring.
BEAM_EDGES: Final[tuple[tuple[int, int], ...]] = ((0, 1),)


@dataclass
class ShellMesh:
    """A surface mesh of triangles or quadrilaterals, linear or quadratic.

    One class covers both shapes and both orders, the way `TetMesh` covers tet4
    and tet10: `faces` is (m, 3) or (m, 4) corner nodes, and `midside` is either
    None or one node per edge of that shape. Mixing triangles and quadrilaterals
    in one mesh is refused rather than supported — CalculiX writes one
    `*ELEMENT, TYPE=` card per element type, so a mixed mesh is two element sets
    and two section cards, and pretending otherwise here would produce a deck
    whose second half was silently dropped.
    """

    nodes: NDArray[np.float64]  # (n_nodes, 3), millimetres
    faces: NDArray[np.int64]  # (n_faces, 3) or (n_faces, 4) corner nodes
    midside: NDArray[np.int64] | None = None  # (n_faces, 3) or (n_faces, 4)

    def __post_init__(self) -> None:
        self.nodes = np.ascontiguousarray(self.nodes, dtype=np.float64)
        self.faces = np.ascontiguousarray(self.faces, dtype=np.int64)
        if self.nodes.ndim != 2 or self.nodes.shape[1] != 3:
            raise MeshError(f"nodes must have shape (n, 3), got {self.nodes.shape}")
        if self.faces.ndim != 2 or self.faces.shape[1] not in (3, 4):
            raise MeshError(
                f"faces must have shape (m, 3) for triangles or (m, 4) for "
                f"quadrilaterals, got {self.faces.shape}"
            )
        if len(self.faces) == 0:
            raise MeshError("shell mesh contains no faces")
        if self.faces.max(initial=-1) >= len(self.nodes) or self.faces.min(initial=0) < 0:
            raise MeshError("face references a node index outside the node array")

        if self.midside is None:
            return
        self.midside = np.ascontiguousarray(self.midside, dtype=np.int64)
        expected = (len(self.faces), self.faces.shape[1])
        if self.midside.shape != expected:
            raise MeshError(
                f"midside must have shape {expected} — one node per edge of each "
                f"face — got {self.midside.shape}"
            )
        if self.midside.max(initial=-1) >= len(self.nodes) or self.midside.min(initial=0) < 0:
            raise MeshError("midside node index is outside the node array")

    @property
    def is_triangular(self) -> bool:
        return self.faces.shape[1] == 3

    @property
    def element_order(self) -> int:
        """1 for S3/S4, 2 for S6/S8."""
        return 1 if self.midside is None else 2

    @property
    def element_type(self) -> str:
        """This codebase's own name for the element, not CalculiX's.

        `app/solve/calculix/elements.py` maps these onto `S3`/`S4`/`S6`/`S8R`,
        and does it in one place: the solver's vocabulary belongs to the solver's
        adapter, so a second backend does not inherit CalculiX's spelling.
        """
        shape = "tri" if self.is_triangular else "quad"
        return f"{shape}{self.faces.shape[1] * self.element_order}"

    @property
    def edge_table(self) -> tuple[tuple[int, int], ...]:
        """Which corner pair each midside slot sits between, for this shape."""
        return SHELL_TRI_EDGES if self.is_triangular else SHELL_QUAD_EDGES

    @property
    def connectivity(self) -> NDArray[np.int64]:
        """Every node of every face, corners first then midside — the order
        CalculiX reads and the order every Abaqus-family quadratic element
        uses."""
        if self.midside is None:
            return self.faces
        return np.ascontiguousarray(np.hstack([self.faces, self.midside]))

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def bounding_box(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return self.nodes.min(axis=0), self.nodes.max(axis=0)

    def face_areas(self) -> NDArray[np.float64]:
        """Area of every face in mm^2, from the corner nodes only.

        A quadrilateral is split on the 0-2 diagonal and the two triangles are
        summed, which is exact for a planar face and is the area of the actual
        folded surface for one that is not — a warped quadrilateral has no single
        flat area, and this is the one a mid-surface load would act on.

        Midside nodes are ignored on purpose. A quadratic face bulges, so its
        true area is larger; using the corners keeps this reading identically for
        a mesh and its own quadratic promotion, which is what makes a mass
        comparison across element orders mean something.
        """
        points = self.nodes[self.faces]
        first = _triangle_area(points[:, 0], points[:, 1], points[:, 2])
        if self.is_triangular:
            return first
        return first + _triangle_area(points[:, 0], points[:, 2], points[:, 3])

    @property
    def area_mm2(self) -> float:
        return float(self.face_areas().sum())

    def volume_mm3(self, thickness_mm: float) -> float:
        """Area times thickness — the material a shell mesh stands for.

        Takes the thickness rather than holding one: see the module docstring.
        """
        if thickness_mm <= 0.0:
            raise MeshError(
                f"A shell thickness of {thickness_mm} mm encloses no material. Give a "
                "positive thickness."
            )
        return self.area_mm2 * float(thickness_mm)


@dataclass
class BeamMesh:
    """A mesh of line elements: a frame, a truss, a lattice.

    `segments` is (m, 2) — the two ends — and `midside` is (m, 1) for a quadratic
    beam. Kept as two arrays rather than one (m, 3) so that "which node is the
    middle one" is a fact about the *data structure* rather than a convention a
    reader has to remember. The order they are written to a deck in is
    `app/solve/calculix/elements.py`'s business and is stated there.
    """

    nodes: NDArray[np.float64]  # (n_nodes, 3), millimetres
    segments: NDArray[np.int64]  # (n_segments, 2) end nodes
    midside: NDArray[np.int64] | None = None  # (n_segments, 1)

    def __post_init__(self) -> None:
        self.nodes = np.ascontiguousarray(self.nodes, dtype=np.float64)
        self.segments = np.ascontiguousarray(self.segments, dtype=np.int64)
        if self.nodes.ndim != 2 or self.nodes.shape[1] != 3:
            raise MeshError(f"nodes must have shape (n, 3), got {self.nodes.shape}")
        if self.segments.ndim != 2 or self.segments.shape[1] != 2:
            raise MeshError(f"segments must have shape (m, 2), got {self.segments.shape}")
        if len(self.segments) == 0:
            raise MeshError("beam mesh contains no segments")
        if self.segments.max(initial=-1) >= len(self.nodes) or self.segments.min(initial=0) < 0:
            raise MeshError("segment references a node index outside the node array")
        if np.any(self.segments[:, 0] == self.segments[:, 1]):
            raise MeshError(
                "a segment starts and ends at the same node, so it has no length and "
                "no direction — the local frame of a zero-length beam is undefined"
            )

        if self.midside is None:
            return
        self.midside = np.ascontiguousarray(self.midside, dtype=np.int64)
        if self.midside.shape != (len(self.segments), 1):
            raise MeshError(
                f"midside must have shape ({len(self.segments)}, 1) — one middle node "
                f"per segment — got {self.midside.shape}"
            )
        if self.midside.max(initial=-1) >= len(self.nodes) or self.midside.min(initial=0) < 0:
            raise MeshError("midside node index is outside the node array")

    @property
    def element_order(self) -> int:
        """1 for a two-node beam, 2 for a three-node one."""
        return 1 if self.midside is None else 2

    @property
    def element_type(self) -> str:
        return "beam2" if self.midside is None else "beam3"

    @property
    def connectivity(self) -> NDArray[np.int64]:
        """Start, **middle**, end — the midside node between its two ends.

        Not corners-then-midside, which is what every other quadratic element in
        this family does and what this returned until 2026-09-09. CalculiX
        numbers a three-node beam along the member, so a row of
        `[start, end, middle]` is read as `[start, middle, end]`: the far end is
        taken for the midside node and the midside node for the far end, which
        folds the element back on itself.

        **Measured on the seat rather than reasoned about.** The deck is accepted
        — the connectivity is the right length and every index is in range — and
        the solve then dies with

            *ERROR in e_c3d: nonpositive jacobian determinant in element 1

        once per integration point, naming the element and not the ordering. So
        no quadratic beam deck this repo has ever written could solve, and
        nothing offline could have seen it: `element_rows` renumbers and does not
        reorder, on the strength of the module docstring's claim that the
        ordering here *is* CalculiX's. It was, for shells; it was not, for beams.

        The shell tables above are unaffected and stay as they are — an S4 deck
        solves, checked at the same time.
        """
        if self.midside is None:
            return self.segments
        return np.ascontiguousarray(
            np.hstack([self.segments[:, :1], self.midside, self.segments[:, 1:]])
        )

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def segment_count(self) -> int:
        return len(self.segments)

    @property
    def bounding_box(self) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        return self.nodes.min(axis=0), self.nodes.max(axis=0)

    def directions(self) -> NDArray[np.float64]:
        """Each segment's unit vector, end to end. (m, 3).

        End to end even for a quadratic beam: the middle node bends the element,
        and the *axis* the cross section is oriented about is the chord. A
        curved beam whose middle node is far off the chord is a mesh that needs
        refining, not a different definition of its axis.
        """
        vectors = self.nodes[self.segments[:, 1]] - self.nodes[self.segments[:, 0]]
        lengths = np.linalg.norm(vectors, axis=1)
        # `__post_init__` refuses a zero-length segment, so this cannot divide by
        # zero. Asserted rather than assumed because the guard is in another
        # method and this is where the division happens.
        if float(lengths.min(initial=1.0)) <= 0.0:  # pragma: no cover - refused above
            raise MeshError("a segment has zero length")
        return vectors / lengths[:, None]

    def segment_lengths(self) -> NDArray[np.float64]:
        """Chord length of every segment, in mm."""
        vectors = self.nodes[self.segments[:, 1]] - self.nodes[self.segments[:, 0]]
        return np.linalg.norm(vectors, axis=1)

    @property
    def length_mm(self) -> float:
        return float(self.segment_lengths().sum())

    def volume_mm3(self, area_mm2: float) -> float:
        """Total length times the cross-sectional area."""
        if area_mm2 <= 0.0:
            raise MeshError(
                f"A cross-sectional area of {area_mm2} mm^2 encloses no material. Give "
                "a section with positive area."
            )
        return self.length_mm * float(area_mm2)


def _triangle_area(
    a: NDArray[np.float64], b: NDArray[np.float64], c: NDArray[np.float64]
) -> NDArray[np.float64]:
    return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)


def shell_quality(mesh: ShellMesh) -> dict[str, float | int | str]:
    """Quality summary for a shell mesh, on the same 0-to-1 scale as the others.

    `min_quality` is `c * A / sum(edge^2)` with `c` chosen so that a perfect
    element reads exactly 1: `4 * sqrt(3)` for a triangle, which is
    `planar_quality`'s factor, and `4` for a quadrilateral, because a square of
    side `s` has area `s^2` and an edge-square sum of `4 s^2`. Both fall to 0 as
    the element degenerates. The two constants exist so a report can print a
    triangular and a quadrilateral mesh side by side without a legend — the same
    argument `app.mesh.types.quality` makes for the tet radius ratio.

    Area comes from `face_areas`, so it is the *corner* area of a quadratic
    element and the folded area of a warped quadrilateral. That is deliberate:
    it is the same number `app.solve.shell_loads` distributes a load over, and a
    quality figure computed from a different area than the load path uses would
    let a mesh look good and load wrong.

    **It measures skew and aspect ratio, and is close to blind to warp.** A 45
    degree parallelogram reads 0.707, but a 10 mm quadrilateral with one corner
    lifted 5 mm out of plane still reads 0.994 — only the four edge lengths
    enter the denominator and `face_areas` sums the two folded triangles, so a
    fold barely moves either. Warp is the characteristic defect of a
    quadrilateral shell element, so **a clean `min_quality` here is not evidence
    that a shell solver will be happy**; a warp measure is a separate quantity
    and is not written yet.
    """
    points = mesh.nodes[mesh.faces]
    areas = mesh.face_areas()
    sides = np.stack(
        [np.sum((points[:, b] - points[:, a]) ** 2, axis=1) for a, b in mesh.edge_table],
        axis=1,
    )
    perimeter_sq = sides.sum(axis=1)
    factor = 4.0 * np.sqrt(3.0) if mesh.is_triangular else 4.0
    with np.errstate(divide="ignore", invalid="ignore"):
        shape = np.where(perimeter_sq > 0.0, factor * areas / perimeter_sq, 0.0)
    # `shape.min()` rather than `shape.min(initial=0.0)`, for the reason
    # `planar_quality` spells out: `initial` seeds the reduction rather than
    # standing in for an empty array, so `initial=0.0` would report every mesh
    # as degenerate. `__post_init__` refuses a mesh with no faces.
    return {
        "element_type": mesh.element_type,
        "element_count": int(mesh.face_count),
        "node_count": int(mesh.node_count),
        "area_mm2": float(mesh.area_mm2),
        "min_quality": float(shape.min()),
        "mean_quality": float(shape.mean()),
    }


__all__ = [
    "BEAM_EDGES",
    "SHELL_QUAD_EDGES",
    "SHELL_TRI_EDGES",
    "BeamMesh",
    "ShellMesh",
    "shell_quality",
]
