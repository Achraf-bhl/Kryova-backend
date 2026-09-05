"""Measuring the properties a predicate asks about: length, area, type, direction, convexity.

Split from `resolve.py` so that "what is true of this edge" is testable without going
through a selector, and so the expensive questions are visible as their own functions.

**Cost.** Length and area are `BRepGProp` integrations, cheap per entity and not cheap
over a 40,000-face assembly. `resolve.py` is arranged to ask the cheap questions first;
that ordering is the reason these are separate functions rather than one `describe()`
that computes everything.

**Convexity is the interesting one.** An edge is convex when the material sits on the
inside of the angle between its two faces — what "fillet the outside corners" means, and
what CATIA's `convex` selector means. Two plausible ways to compute it are wrong, and
`edge_is_convex` documents both along with the one that works; an edge with fewer than
two faces, or a tangent join, is neither convex nor concave and says so rather than
defaulting.
"""

from __future__ import annotations

import math
from typing import Any, Final

from app.kernel.occt.binding import symbol
from app.kernel.occt.topology import point_of, vertices

#: Parameter fraction along an edge at which convexity is sampled. The middle, because
#: an edge's ends are shared with other edges where normals are ambiguous.
_SAMPLE: Final = 0.5

#: Below this, the bisector of two normals has no meaningful direction — the faces are
#: tangent, the smooth case where an edge is neither convex nor concave.
_TANGENT_EPSILON: Final = 1e-7

#: Resolution passed to `BRepLProp_SLProps`. Not a modelling tolerance — it is the step
#: the local-property evaluator uses to decide a derivative is degenerate.
_NORMAL_TOLERANCE: Final = 1e-7

#: Points at which an edge's tangent is sampled when asking about its direction. Enough
#: to catch an arc that is parallel to an axis at one end and not at the other.
_DIRECTION_SAMPLES: Final = 8


def edge_length_mm(edge: Any) -> float:
    props = symbol("GProp_GProps")()
    symbol("BRepGProp").LinearProperties_s(edge, props)
    return float(props.Mass())


def face_area_mm2(face: Any) -> float:
    props = symbol("GProp_GProps")()
    symbol("BRepGProp").SurfaceProperties_s(face, props)
    return float(props.Mass())


def edge_curve_type(edge: Any) -> str:
    """`"Line"`, `"Circle"`, `"BSplineCurve"`, … — OCCT's own classification."""
    adaptor = symbol("BRepAdaptor_Curve")(edge)
    return str(adaptor.GetType()).rsplit(".", 1)[-1].removeprefix("GeomAbs_")


def face_surface_type(face: Any) -> str:
    """`"Plane"`, `"Cylinder"`, `"Sphere"`, … — OCCT's own classification."""
    adaptor = symbol("BRepAdaptor_Surface")(face)
    return str(adaptor.GetType()).rsplit(".", 1)[-1].removeprefix("GeomAbs_")


def cylinder_diameter_mm(face: Any) -> float | None:
    """The diameter of a cylindrical face, or None if it is not one.

    None rather than an exception: asking "is this the Ø6 bore?" of every face in a part
    is the normal way to find it, and most faces are not cylinders.
    """
    adaptor = symbol("BRepAdaptor_Surface")(face)
    if face_surface_type(face) != "Cylinder":
        return None
    return float(adaptor.Cylinder().Radius()) * 2.0


def face_normal(face: Any) -> tuple[float, float, float] | None:
    """The outward normal at the middle of a face, or None where it has none.

    Taken at the parametric centre rather than a corner, where a trimmed face's normal
    can belong to a neighbour.
    """
    surface = symbol("BRepAdaptor_Surface")(face)
    u = (surface.FirstUParameter() + surface.LastUParameter()) / 2.0
    v = (surface.FirstVParameter() + surface.LastVParameter()) / 2.0
    return face_normal_at(face, u, v)


def face_normal_at(face: Any, u: float, v: float) -> tuple[float, float, float] | None:
    """The outward normal at one parametric point on a face, or None where it has none.

    **Reversed faces are honoured, and this is the load-bearing line.** OCCT stores a
    face's orientation separately from its surface, so the surface normal points the same
    way whether the face is FORWARD or REVERSED. Ignoring that leaves half the normals on
    a solid pointing *into* the material — which would make `normal: "+z"` select the
    bottom of a part as often as the top, and would send every inward-cast thickness ray
    straight out of the solid.

    Separate from `face_normal` because interrogation samples many points per face, while
    selection asks about the face as a whole. One implementation, two entry points, so
    the orientation correction cannot be applied in one and forgotten in the other.
    """
    surface = symbol("BRepAdaptor_Surface")(face)
    props = symbol("BRepLProp_SLProps")(surface, u, v, 1, _NORMAL_TOLERANCE)
    if not props.IsNormalDefined():
        return None

    normal = props.Normal()
    orientation = str(face.Orientation()).rsplit(".", 1)[-1]
    sign = -1.0 if orientation == "TopAbs_REVERSED" else 1.0
    return (normal.X() * sign, normal.Y() * sign, normal.Z() * sign)


def edge_is_convex(edge: Any, faces_of_edge: list[Any]) -> bool | None:
    """Is the material on the inside of the angle at this edge?

    Returns None when the question does not apply: a free boundary with fewer than two
    faces, or a tangent join where the faces meet smoothly and the edge is neither
    convex nor concave. Both are reported rather than guessed, because a selector that
    quietly counted a smooth join as convex would fillet a face that is already round.

    **The test is `(n₁ × t) · n₂`, and the whole difficulty is in `t`.**

    Take the first face's outward normal `n₁` and the edge tangent `t` *as the edge is
    oriented within that face*. Their cross product points from the edge into that face's
    interior, because a face's boundary loop runs anticlockwise seen from outside. Dot
    that with the second face's outward normal `n₂`: negative means the second face falls
    away from the first — convex; positive means it folds back over the material —
    concave.

    Two versions of this were wrong before this one, and both are worth recording:

    * Using the tangent of the edge **as stored in the shape map** rather than as
      oriented in the face. A map keeps one representative per edge with whatever
      orientation it happened to have, so the sign was arbitrary: on a plain box, all
      twelve of whose edges are convex, that version reported seven.
    * Probing along the **bisector of the two outward normals** and asking the solid
      classifier where the probe landed. At a convex edge the bisector points out of the
      material and at a concave edge it points into the cavity — both outside the solid,
      so it cannot tell them apart. Unoriented normals carry no information about which
      side the material is on; orientation is not optional here.
    """
    if len(faces_of_edge) != 2:
        return None

    reference_face = faces_of_edge[0]
    normal = face_normal(reference_face)
    other_normal = face_normal(faces_of_edge[1])
    if normal is None or other_normal is None:
        return None

    oriented = _edge_as_oriented_in(reference_face, edge)
    if oriented is None:
        return None

    curve = symbol("BRepAdaptor_Curve")(oriented)
    parameter = curve.FirstParameter() + _SAMPLE * (
        curve.LastParameter() - curve.FirstParameter()
    )
    point = symbol("gp_Pnt")()
    tangent = symbol("gp_Vec")()
    curve.D1(parameter, point, tangent)

    # The edge's own orientation within the face flips the loop direction, and with it
    # the sign of everything below.
    orientation = str(oriented.Orientation()).rsplit(".", 1)[-1]
    sense = -1.0 if orientation == "TopAbs_REVERSED" else 1.0
    direction = (tangent.X() * sense, tangent.Y() * sense, tangent.Z() * sense)
    length = math.sqrt(sum(component * component for component in direction))
    if length < _TANGENT_EPSILON:
        return None
    direction = (direction[0] / length, direction[1] / length, direction[2] / length)

    into_face = (
        normal[1] * direction[2] - normal[2] * direction[1],
        normal[2] * direction[0] - normal[0] * direction[2],
        normal[0] * direction[1] - normal[1] * direction[0],
    )

    fold = sum(into_face[i] * other_normal[i] for i in range(3))
    if abs(fold) < _TANGENT_EPSILON:
        # The faces meet tangentially: a smooth join, neither convex nor concave.
        return None
    return fold < 0.0


def _edge_as_oriented_in(face: Any, edge: Any) -> Any | None:
    """The same edge, carrying the orientation it has *within this face*.

    A shape map returns one representative per edge; a face's own boundary carries the
    orientation that says which way round the loop runs, and that is the only version
    from which "into the face" can be computed.
    """
    from app.kernel.occt.topology import explore_oriented

    cast = symbol("TopoDS").Edge_s
    for candidate in explore_oriented(face, "EDGE"):
        if candidate.IsSame(edge):
            return cast(candidate)
    return None


def edge_directions(edge: Any, samples: int = _DIRECTION_SAMPLES) -> list[
    tuple[float, float, float]
]:
    """Unit tangents along an edge, sampled end to end.

    A *list* rather than one direction, because most edges do not have a single one. A
    line does; a circle's tangent sweeps through every direction in its plane; an arc
    that climbs points somewhere different at each end. Orientation questions are then
    answered by requiring the property to hold at **every** sample, which is what makes
    "perpendicular to Z" true of a horizontal circle (every tangent is) and false of a
    rising arc (some are not) — a distinction a single end-to-end comparison cannot make.

    Degenerate samples are dropped rather than normalised, since a zero tangent has no
    direction to report and would divide by zero if treated as one.
    """
    curve = symbol("BRepAdaptor_Curve")(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    if not (math.isfinite(first) and math.isfinite(last)):
        return []

    span = last - first
    directions: list[tuple[float, float, float]] = []
    for index in range(samples + 1):
        point = symbol("gp_Pnt")()
        tangent = symbol("gp_Vec")()
        curve.D1(first + span * index / samples, point, tangent)
        vector = (tangent.X(), tangent.Y(), tangent.Z())
        length = math.sqrt(sum(component * component for component in vector))
        if length < _TANGENT_EPSILON:
            continue
        directions.append(
            (vector[0] / length, vector[1] / length, vector[2] / length)
        )
    return directions


def faces_by_edge(shape: Any) -> Any:
    """A map from each edge to the faces that adjoin it.

    Built once per shape and passed around, because the alternative — searching every
    face for each edge — is quadratic, and a selector over a real assembly is exactly
    where that would show.
    """
    mapping = symbol("TopTools_IndexedDataMapOfShapeListOfShape")()
    enum = symbol("TopAbs_ShapeEnum")
    symbol("TopExp").MapShapesAndAncestors_s(
        shape, enum.TopAbs_EDGE, enum.TopAbs_FACE, mapping
    )
    return mapping


def adjoining_faces(mapping: Any, edge: Any) -> list[Any]:
    """The faces adjoining one edge, from a map built by `faces_by_edge`."""
    from app.kernel.occt.topology import shape_list

    if not mapping.Contains(edge):
        return []
    cast = symbol("TopoDS").Face_s
    return [cast(face) for face in shape_list(mapping.FindFromKey(edge))]


def entity_extent(entity: Any, axis_index: int) -> tuple[float, float]:
    """How far an entity reaches along one axis, as (minimum, maximum).

    Vertices rather than a bounding box: a bounding box on a curved face includes the
    bulge, and "the edges at the top" means the ones whose *ends* are at the top.
    """
    points = [point_of(vertex) for vertex in vertices(entity)]
    if not points:
        box = symbol("Bnd_Box")()
        box.SetGap(0.0)
        symbol("BRepBndLib").Add_s(entity, box, True)
        low = box.Get()[axis_index]
        high = box.Get()[axis_index + 3]
        return (low, high)
    values = [point[axis_index] for point in points]
    return (min(values), max(values))


__all__ = [
    "adjoining_faces",
    "cylinder_diameter_mm",
    "edge_curve_type",
    "edge_directions",
    "edge_is_convex",
    "edge_length_mm",
    "entity_extent",
    "face_area_mm2",
    "face_normal",
    "face_normal_at",
    "face_surface_type",
    "faces_by_edge",
]
