"""A shape as triangles for a viewer -- master plan P6.1, the backend half.

Rendering (`app/render/`) is hidden-line removal and never tessellates; the solver meshes through
gmsh. This is the third thing geometry becomes: a display mesh, at a stated chordal deflection,
for `app/render/gltf.py` to package.

Three facts it rests on, each checked on this OCP build on 2026-09-15:

* **`BRepMesh_IncrementalMesh` writes the triangulation onto the shape it is given.** Meshing the
  caller's shape at a second deflection would leave the first triangulation where an unrelated
  reader finds it, so each call meshes a `BRepBuilderAPI_Copy` and the caller's shape is
  untouched.
* **A face's triangles follow the surface's parametric orientation, not the solid's outward
  side.** A REVERSED face's triangles are wound inward, so their second and third nodes are
  swapped here. The closed-mesh signed volume is the check: it equals the solid's volume only
  when every triangle is wound outward, and `TriangleMesh.signed_volume_mm3` exposes it.
* **The triangulation is in the face's local frame.** `BRep_Tool.Triangulation_s(face, loc)` fills
  `loc`, and the nodes are transformed by it; a part moved by a location would otherwise be drawn
  at its origin.

Nodes are not welded across faces: a vertex on a shared edge appears once per face. That is what
a flat-shaded viewer wants (glTF computes flat normals when none are given) and it keeps the
indices of one face independent of every other.

**Every triangle records the face it came from** (`TriangleMesh.face_of_triangle`, added 2026-09-16
with P6.6). That is what makes a pick answerable: a ray hits a triangle, and the question anyone
then asks -- measure this, fillet this, what is this called -- is about the *face*. Without it the
mesh is a picture you cannot point at. `app/kernel/occt/propose.py` is the other half.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.kernel.errors import KernelError
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.topology import FACE, explore_oriented, index_map


@dataclass(frozen=True)
class TriangleMesh:
    """Triangles in mm. `positions` is (n, 3) float64; `indices` is (m, 3) int64."""

    positions: np.ndarray
    indices: np.ndarray
    linear_deflection_mm: float
    angular_deflection_rad: float

    #: Which B-rep face each triangle came from: an (m,) int64 array of ordinals into
    #: `topology.faces()` -- the de-duplicated map order, which is the list
    #: `app/kernel/occt/resolve.py` filters. So an ordinal here and a face a predicate
    #: resolved to are the same numbering. It is deliberately *not* the order of the
    #: `explore_oriented` walk the loop below uses; see the comment there.
    #:
    #: This is what turns a click on a triangle into a face, which is the half of P6.4's
    #: measure and P6.6's selection that the mesh alone cannot answer. It is `None` on a
    #: mesh nobody tessellated -- a hand-built one in a test -- and `face_of` refuses such
    #: a mesh by name rather than guessing an ordinal, because a wrong face is a wrong
    #: measurement that looks like a right one.
    face_of_triangle: np.ndarray | None = None

    @property
    def vertex_count(self) -> int:
        return int(self.positions.shape[0])

    @property
    def triangle_count(self) -> int:
        return int(self.indices.shape[0])

    @property
    def face_count(self) -> int:
        """How many B-rep faces contributed triangles, or 0 when the partition is absent."""
        if self.face_of_triangle is None or self.face_of_triangle.size == 0:
            return 0
        return int(self.face_of_triangle.max()) + 1

    def face_of(self, triangle_index: int) -> int:
        """The face ordinal a picked triangle belongs to.

        The whole point of the pick: a viewer knows which triangle the ray hit and needs
        the face, because every predicate in `app/kernel/selection.py` is about faces and
        none of them is about triangles.
        """
        if self.face_of_triangle is None:
            raise KernelError(
                "This mesh carries no face partition, so a triangle cannot be traced back to "
                "a face. Meshes from `tessellate` carry one; a hand-built mesh does not."
            )
        if not 0 <= triangle_index < self.triangle_count:
            raise KernelError(
                f"Triangle {triangle_index} is not in this mesh, which has "
                f"{self.triangle_count} triangles (0..{self.triangle_count - 1})."
            )
        return int(self.face_of_triangle[triangle_index])

    @property
    def bounds_mm(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        low = self.positions.min(axis=0)
        high = self.positions.max(axis=0)
        return (
            (float(low[0]), float(low[1]), float(low[2])),
            (float(high[0]), float(high[1]), float(high[2])),
        )

    @property
    def signed_volume_mm3(self) -> float:
        """Σ a·(b×c)/6 over the triangles: the enclosed volume when wound outward, negative inward."""
        a = self.positions[self.indices[:, 0]]
        b = self.positions[self.indices[:, 1]]
        c = self.positions[self.indices[:, 2]]
        return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def tessellate(
    shape: object,
    *,
    linear_deflection_mm: float,
    angular_deflection_rad: float = 0.5,
) -> TriangleMesh:
    """Triangulate `shape` at the given deflections, without touching `shape` itself."""
    require()
    if not (math.isfinite(linear_deflection_mm) and linear_deflection_mm > 0.0):
        raise KernelError(
            f"A linear deflection of {linear_deflection_mm} mm is not a tolerance; give the largest "
            "distance a triangle may sit from the surface, in mm."
        )
    if not (math.isfinite(angular_deflection_rad) and 0.0 < angular_deflection_rad < math.pi):
        raise KernelError(
            f"An angular deflection of {angular_deflection_rad} rad is outside (0, π)."
        )
    copy = symbol("BRepBuilderAPI_Copy")(shape).Shape()
    mesher = symbol("BRepMesh_IncrementalMesh")(
        copy, linear_deflection_mm, False, angular_deflection_rad, False
    )
    if not mesher.IsDone():
        raise KernelError("OCCT could not triangulate this shape at that deflection.")

    reversed_ = symbol("TopAbs_Orientation").TopAbs_REVERSED
    as_face = symbol("TopoDS").Face_s
    tool = symbol("BRep_Tool")
    location_type = symbol("TopLoc_Location")

    positions: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    face_of_triangle: list[int] = []
    untriangulated = 0
    # The ordinal is the face's index in `topology.faces()` order -- the de-duplicated
    # map -- because that is the list `resolve.py` filters and therefore the only
    # numbering a predicate can be checked against.
    #
    # This walk has to be `explore_oriented`, which is a *different* list: it keeps the
    # orientation the flip below needs, and it keeps duplicates.
    #
    # **The two agreed on every shape tried on 2026-09-16** -- a bored plate (7 faces),
    # a compound of two disjoint boxes (12), two touching boxes compounded (12), their
    # fuse (10) and a sewing of both (12, not 11: sewing joins edges into a shell and
    # leaves the two coincident interface faces distinct). So the map lookup below is
    # **defensive rather than demonstrated**: no shape here proves it is needed, and it
    # is used anyway because `explore` de-duplicates by design and the day one face has
    # two parents, counting off this walk shifts every later ordinal by one with nothing
    # raising. Cheap insurance against a silent wrong face; not a pinned guard, and the
    # test file says so rather than implying a break was watched.
    face_map = index_map(copy, FACE)
    for face_shape in explore_oriented(copy, FACE):
        face = as_face(face_shape)
        ordinal = face_map.FindIndex(face_shape) - 1
        location = location_type()
        triangulation = tool.Triangulation_s(face, location)
        if triangulation is None:
            untriangulated += 1
            continue
        transform = location.Transformation()
        base = len(positions)
        for index in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(index).Transformed(transform)
            positions.append((point.X(), point.Y(), point.Z()))
        flip = face.Orientation() == reversed_
        for index in range(1, triangulation.NbTriangles() + 1):
            n1, n2, n3 = triangulation.Triangle(index).Get()
            if flip:
                n2, n3 = n3, n2
            triangles.append((base + n1 - 1, base + n2 - 1, base + n3 - 1))
            face_of_triangle.append(ordinal)
    if untriangulated:
        raise KernelError(
            f"{untriangulated} face(s) came back with no triangulation, so the display mesh would "
            "have holes in it. Try a larger linear deflection."
        )
    if not triangles:
        raise KernelError("This shape has no faces to draw.")
    return TriangleMesh(
        positions=np.asarray(positions, dtype=np.float64),
        indices=np.asarray(triangles, dtype=np.int64),
        linear_deflection_mm=linear_deflection_mm,
        angular_deflection_rad=angular_deflection_rad,
        face_of_triangle=np.asarray(face_of_triangle, dtype=np.int64),
    )


def levels_of_detail(
    shape: object, deflections_mm: tuple[float, ...], *, angular_deflection_rad: float = 0.5
) -> tuple[TriangleMesh, ...]:
    """One mesh per deflection, finest first. Deflections must be given coarsening.

    A triangle count is set by whichever deflection is tighter, so a coarse level needs a loose
    angular deflection as well: at 0.05 rad a Ø10 cylinder gave 1,004 triangles at 0.01, 0.1 and
    1 mm alike, and at 1.5 rad it gave 280, 88 and 32 (measured 2026-09-15).
    """
    if not deflections_mm:
        raise KernelError("Give at least one linear deflection.")
    if list(deflections_mm) != sorted(set(deflections_mm)):
        raise KernelError(
            f"Deflections {deflections_mm} must be distinct and increasing: level 0 is the finest."
        )
    return tuple(
        tessellate(shape, linear_deflection_mm=d, angular_deflection_rad=angular_deflection_rad)
        for d in deflections_mm
    )


__all__ = ["TriangleMesh", "levels_of_detail", "tessellate"]
