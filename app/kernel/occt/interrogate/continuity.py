"""How faces meet along an edge — the continuity half of master plan 3.2.

**OCCT's own `BRep_Tool::Continuity` is not used here, and the reason matters.** That
call returns the continuity *registered on the edge* by whatever built it. On a solid
assembled from booleans and primitives — which is every part this kernel makes — almost
every edge is registered `C0` regardless of how the surfaces actually meet, because
nothing ever set anything else. Reading it would produce a report that is cheap, precise
in appearance, and says nothing about the geometry.

So continuity is **computed**: take the outward normal of each adjoining face at the
midpoint of the shared edge and measure the angle between the faces. Same two normals
`classify.edge_is_convex` already uses, asked a different question.

**The convention is the interior dihedral angle.** Normals parallel (0° apart) means the
faces continue smoothly into one another: dihedral 180°, a tangent join. Normals 90°
apart is a box corner: dihedral 90°. Normals nearly opposite is a knife edge approaching
0°. So *larger is smoother*, and the minimum is the sharpest feature on the part — which
is the number worth asserting on, because it is simultaneously the stress concentration,
the machining limit, and the thing a cast part cracks at.
"""

from __future__ import annotations

import math
from typing import Any, Final

from app.kernel.interrogation import ContinuityReport
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.classify import (
    adjoining_faces,
    face_normal_at,
    faces_by_edge,
)
from app.kernel.occt.topology import edges

#: Below this dihedral, an edge is reported as sharp. 179.5°–180° is tangent, and
#: everything under 179.5° is a real corner; the threshold exists to split the count into
#: two useful buckets, not to make a judgement about the part.
SHARP_THRESHOLD_DEG: Final = 179.5


def scan_continuity(shape: Any) -> ContinuityReport:
    """Dihedral angle at every edge shared by two faces.

    Free edges (fewer than two faces) and edges whose faces will not evaluate are counted
    separately rather than skipped, so a reader can tell a smooth part from one where the
    check could not see most of the joins.
    """
    require()

    mapping = faces_by_edge(shape)
    projector = symbol("BRep_Tool")

    minimum: float | None = None
    sharpest_index: int | None = None
    tangent = 0
    sharp = 0
    open_edges = 0
    unevaluated = 0

    for index, edge in enumerate(edges(shape)):
        adjoining = adjoining_faces(mapping, edge)
        if len(adjoining) < 2:
            open_edges += 1
            continue

        angle = _dihedral_deg(edge, adjoining[0], adjoining[1], projector)
        if angle is None:
            unevaluated += 1
            continue

        if angle >= SHARP_THRESHOLD_DEG:
            tangent += 1
        else:
            sharp += 1

        if minimum is None or angle < minimum:
            minimum = angle
            sharpest_index = index

    return ContinuityReport(
        minimum_dihedral_deg=minimum,
        tangent_edges=tangent,
        sharp_edges=sharp,
        open_edges=open_edges,
        unevaluated=unevaluated,
        sharpest_edge_index=sharpest_index,
    )


def _dihedral_deg(edge: Any, first: Any, second: Any, projector: Any) -> float | None:
    """The interior angle between two faces at the middle of their shared edge.

    The edge's parameter is taken from `BRepAdaptor_Curve` and then fed to each face's
    **pcurve** — the 2D curve the edge has in that face's parameter space. Two details
    make this the right route rather than the obvious one:

    * **The parameter is shared.** A B-rep guarantees that an edge's 3D curve and its
      pcurve on an adjoining face carry the same parameterisation, so one midpoint value
      lands at the same physical point on both faces. That is what makes the two normals
      comparable at all.
    * **Projecting the 3D point instead would break on a seam.** On a periodic face — any
      full cylinder — a point on the seam has two valid parameter values, and a
      projection is free to return the one belonging to the far side. The normal then
      comes from the wrong place and the dihedral is nonsense in a way nothing downstream
      can detect.

    OCP returns these curves directly rather than as a tuple with the parameter range,
    despite the C++ signature's out-parameters; the range therefore comes from the
    adaptor, not from `Curve_s`.
    """
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    midpoint = (adaptor.FirstParameter() + adaptor.LastParameter()) / 2.0
    if not math.isfinite(midpoint):
        return None

    normals = []
    for face in (first, second):
        pcurve = projector.CurveOnSurface_s(edge, face, 0.0, 0.0)
        if pcurve is None:
            return None
        uv = pcurve.Value(midpoint)
        normal = face_normal_at(face, uv.X(), uv.Y())
        if normal is None:
            return None
        normals.append(normal)

    dot = sum(normals[0][i] * normals[1][i] for i in range(3))
    # Both are unit vectors, so this is mathematically in [-1, 1]; floating point puts it
    # slightly outside on the coplanar case, which is the most common edge on a part.
    between = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    return 180.0 - between


__all__ = ["SHARP_THRESHOLD_DEG", "scan_continuity"]
