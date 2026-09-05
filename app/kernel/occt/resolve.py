"""Turning a predicate into the actual edges or faces it names.

The evaluation half of `app.kernel.selection`, which declares the vocabulary. Kept apart
because the vocabulary is backend-neutral — the CATIA backend will answer the same
predicates from a real seat — while this is entirely about OCCT.

**Cheap tests run first.** A predicate is a conjunction, so the first failing test
decides the entity and everything after it is wasted work. The order is: type
classification (an adaptor lookup) → extent along an axis (vertex coordinates) →
containment in a box (the same coordinates) → length or area (a `BRepGProp`
integration) → convexity (two face normals and a derivative). On a part with 40,000
faces that ordering is the difference between a selector being instant and being
noticeable.

**The edge→face map is built once per resolution**, not per edge. Searching every face
for each edge is quadratic, and a convexity predicate over an assembly is exactly where
that would be felt.
"""

from __future__ import annotations

import math
from typing import Any

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt import classify
from app.kernel.occt.binding import require
from app.kernel.occt.topology import edges, faces
from app.kernel.selection import AXIS_INDEX, Predicate, unit

#: How close an entity must sit to the shape's extreme along an axis to count as being
#: "at" it. A face is flat to within kernel noise; an edge chain around a fillet is not,
#: so this is a real tolerance rather than an equality.
EXTREME_TOLERANCE_MM = 1e-6


def restrict_to_feature(
    document: Any, predicate: Predicate, candidates: list[Any]
) -> list[Any]:
    """Keep only the entities the predicate's named feature contributed.

    **A feature that has not recorded its contribution is refused, never widened.** The
    tempting fallback is to ignore `of` and select across the whole part, which would
    make `boss#top` quietly mean `part#top` — the same face on the example everyone
    tests with, and a different one on any part where the boss is not the highest thing.
    That is the class of wrong this codebase refuses everywhere else.
    """
    feature = document.feature(predicate.of)
    owned = (
        feature.contributed_faces
        if predicate.kind == "face"
        else feature.contributed_edges
    )
    if owned is None:
        raise OperationNotSupported(
            f"selecting the {predicate.kind}s of {predicate.of!r}",
            f"{feature.tool} does not record which {predicate.kind}s it contributed, so "
            f"there is no honest way to answer {predicate.of}#... against it. Select "
            "across the whole part with a predicate instead",
        )
    return [entity for entity in candidates if any(entity.IsSame(o) for o in owned)]


def resolve(shape: Any, predicate: Predicate, document: Any = None) -> list[Any]:
    """Every entity of the predicate's kind that satisfies it, in topological order.

    Order is OCCT's traversal order, which is stable for a given shape — that matters
    because a per-entity parameter list (Phase 2.3) is matched against this order, and
    a set would make "the third edge" mean something different on every run.
    """
    require()
    if predicate.of is not None:
        if document is None:
            raise GeometryError(
                f"{predicate.of!r} names a feature, and resolving that needs the "
                "document. This call site does not pass one — selecting a feature's own "
                "entities is only available where the operation has the part open."
            )
        return _within_feature(shape, predicate, document)
    if predicate.kind == "edge":
        return _resolve_edges(shape, predicate)
    return _resolve_faces(shape, predicate)


def _within_feature(shape: Any, predicate: Predicate, document: Any) -> list[Any]:
    """Resolve a predicate against one feature's own entities.

    The feature restriction is applied **before** everything else and the extremes are
    then measured against the feature rather than the part — see `Predicate.of`. That is
    the whole difference between "the top of the boss" and "the top of the part", and it
    is why this cannot be written as one more filter in the existing chain.
    """
    from app.kernel.occt.binding import symbol

    candidates = edges(shape) if predicate.kind == "edge" else faces(shape)
    owned = restrict_to_feature(document, predicate, candidates)
    if not owned:
        return []

    # Rebuild a compound of just this feature's entities so that `axis`/`side` measures
    # the feature's own extent. Measuring against `shape` would make `boss#top` mean the
    # top of the whole part, which is the confusion this field exists to remove.
    builder = symbol("BRep_Builder")()
    compound = symbol("TopoDS_Compound")()
    builder.MakeCompound(compound)
    for entity in owned:
        builder.Add(compound, entity)

    narrowed = Predicate(**{**predicate.__dict__, "of": None})
    return _resolve_edges(compound, narrowed) if predicate.kind == "edge" else (
        _resolve_faces(compound, narrowed)
    )


# -- edges -------------------------------------------------------------------


def _resolve_edges(shape: Any, predicate: Predicate) -> list[Any]:
    candidates = edges(shape)

    if predicate.circular is not None:
        wanted = predicate.circular
        candidates = [
            edge for edge in candidates
            if (classify.edge_curve_type(edge) in ("Circle", "Ellipse")) is wanted
        ]

    candidates = _filter_by_orientation(candidates, predicate, _edge_alignments)
    candidates = _filter_by_extent(shape, candidates, predicate)
    candidates = _filter_by_box(candidates, predicate)

    if predicate.longer_than_mm is not None or predicate.shorter_than_mm is not None:
        kept: list[Any] = []
        for edge in candidates:
            length = classify.edge_length_mm(edge)
            if predicate.longer_than_mm is not None and length <= predicate.longer_than_mm:
                continue
            if predicate.shorter_than_mm is not None and length >= predicate.shorter_than_mm:
                continue
            kept.append(edge)
        candidates = kept

    if predicate.convex is not None:
        # Built last, and only when convexity is actually asked for: the map costs a
        # full traversal of the shape's faces.
        mapping = classify.faces_by_edge(shape)
        candidates = [
            edge for edge in candidates
            if classify.edge_is_convex(edge, classify.adjoining_faces(mapping, edge))
            is predicate.convex
        ]

    return candidates


# -- faces -------------------------------------------------------------------


def _resolve_faces(shape: Any, predicate: Predicate) -> list[Any]:
    candidates = faces(shape)

    if predicate.planar is not None:
        wanted = predicate.planar
        candidates = [
            face for face in candidates
            if (classify.face_surface_type(face) == "Plane") is wanted
        ]

    if predicate.cylindrical is not None:
        wanted = predicate.cylindrical
        candidates = [
            face for face in candidates
            if (classify.face_surface_type(face) == "Cylinder") is wanted
        ]

    if predicate.diameter_mm is not None:
        kept: list[Any] = []
        for face in candidates:
            diameter = classify.cylinder_diameter_mm(face)
            if diameter is None:
                continue
            if abs(diameter - predicate.diameter_mm) <= predicate.diameter_tolerance_mm:
                kept.append(face)
        candidates = kept

    candidates = _filter_by_orientation(candidates, predicate, _face_alignments)
    candidates = _filter_by_extent(shape, candidates, predicate)
    candidates = _filter_by_box(candidates, predicate)

    if predicate.normal is not None:
        target = unit(predicate.normal)
        limit = math.cos(math.radians(predicate.normal_tolerance_deg))
        kept = []
        for face in candidates:
            normal = classify.face_normal(face)
            if normal is None:
                continue
            dot = sum(normal[i] * target[i] for i in range(3))
            if dot >= limit:
                kept.append(face)
        candidates = kept

    if predicate.larger_than_mm2 is not None or predicate.smaller_than_mm2 is not None:
        kept = []
        for face in candidates:
            area = classify.face_area_mm2(face)
            if predicate.larger_than_mm2 is not None and area <= predicate.larger_than_mm2:
                continue
            if predicate.smaller_than_mm2 is not None and area >= predicate.smaller_than_mm2:
                continue
            kept.append(face)
        candidates = kept

    return candidates


# -- shared filters ----------------------------------------------------------


def _filter_by_extent(shape: Any, candidates: list[Any], predicate: Predicate) -> list[Any]:
    """Keep entities lying *in* the shape's extreme plane along an axis.

    Two decisions here, both learned by getting them wrong.

    **The reference extreme comes from the whole shape**, not from the candidate set, so
    `axis="z", side="max"` means "at the top of the part" and not "at the top of whatever
    survived the previous filter" — otherwise the answer would depend on the order the
    predicate's fields happen to be evaluated in.

    **An entity must lie in the extreme plane, not merely reach it.** A box's side wall
    runs from the bottom to the top, so it *touches* z-max; testing only the near end
    selected the top face and all four sides, and a shell asked to open "the top" opened
    five faces and left the bottom slab. So both ends of the entity's extent must sit at
    the reference: it is *at* the top only if all of it is.

    The consequence, stated because it is a real limit: a curved face that crests the
    extreme — the top of a sphere — spans the axis and is therefore not selected by this
    predicate. That is the honest answer for a vocabulary built on flat references;
    pointing at such a face is what `normal` is for.
    """
    if predicate.axis is None or predicate.side is None or not candidates:
        return candidates

    index = AXIS_INDEX[predicate.axis]

    from app.kernel.occt.metrology import bounding_box_mm

    box = bounding_box_mm(shape)
    reference = box["max"][index] if predicate.side == "max" else box["min"][index]

    kept: list[Any] = []
    for entity in candidates:
        low, high = classify.entity_extent(entity, index)
        if (
            abs(low - reference) <= EXTREME_TOLERANCE_MM
            and abs(high - reference) <= EXTREME_TOLERANCE_MM
        ):
            kept.append(entity)
    return kept


def _face_alignments(face: Any) -> list[tuple[float, float, float]] | None:
    """A face's orientation, as the one direction that characterises it: its normal.

    Returned as a list so faces and edges share one filter, and as `None` — not an empty
    list — when the normal will not evaluate, so "could not tell" is distinguishable from
    "has no direction". The filter drops both, but only one of them is a defect.
    """
    normal = classify.face_normal(face)
    return None if normal is None else [normal]


def _edge_alignments(edge: Any) -> list[tuple[float, float, float]] | None:
    directions = classify.edge_directions(edge)
    return directions or None


def _filter_by_orientation(
    candidates: list[Any],
    predicate: Predicate,
    alignments: Any,
) -> list[Any]:
    """Keep entities parallel or perpendicular to the predicate's direction.

    **The face convention is inverted relative to the arithmetic, deliberately.** A face
    *parallel to* Z is one whose plane contains Z, which means its normal is
    perpendicular to Z. Every CAD system says it this way and every engineer reads it
    this way — "the walls are parallel to the pull" — so the vocabulary matches the
    engineer and the inversion is absorbed here, once, rather than by each caller.

    For an edge the question is asked of its own direction with no inversion, and must
    hold at **every** sampled tangent: a horizontal circle is perpendicular to Z all the
    way round and matches, while an arc that climbs matches neither.

    Unsigned throughout — `abs()` on every dot product. A wall parallel to the pull is
    parallel to it whichever way it faces, and requiring a sign would turn "the four
    walls" back into four separate selections, which is the gap this closes.
    """
    wanted = predicate.parallel_to or predicate.perpendicular_to
    if wanted is None or not candidates:
        return candidates

    target = unit(wanted)
    tolerance = math.radians(predicate.angle_tolerance_deg)

    # A face parallel to the direction has a normal perpendicular to it, and vice versa.
    want_aligned = (
        (predicate.perpendicular_to is not None)
        if predicate.kind == "face"
        else (predicate.parallel_to is not None)
    )

    # **Two thresholds, not one negated threshold.** `abs(dot) >= cos(tol)` is the test
    # for aligned; its negation is *not* the test for perpendicular, because it accepts
    # everything from the tolerance all the way to 90°. Written that way, a face at 45°
    # passed as "parallel to Z" and the predicate selected nearly every face on the part.
    # Perpendicular has its own bound: `abs(dot) <= sin(tol)`.
    if want_aligned:
        limit = math.cos(tolerance)

        def matches(dot: float) -> bool:
            return dot >= limit
    else:
        limit = math.sin(tolerance)

        def matches(dot: float) -> bool:
            return dot <= limit

    kept: list[Any] = []
    for entity in candidates:
        directions = alignments(entity)
        if directions is None:
            continue
        if all(
            matches(abs(sum(d[i] * target[i] for i in range(3)))) for d in directions
        ):
            kept.append(entity)
    return kept


def _filter_by_box(candidates: list[Any], predicate: Predicate) -> list[Any]:
    """Keep entities lying entirely inside the predicate's box."""
    if predicate.inside is None:
        return candidates

    from app.kernel.occt.topology import point_of, vertices

    kept: list[Any] = []
    for entity in candidates:
        points = [point_of(vertex) for vertex in vertices(entity)]
        if points and all(predicate.inside.contains(point) for point in points):
            kept.append(entity)
    return kept


def require_matches(matched: list[Any], predicate: Predicate, tool: str) -> list[Any]:
    """Refuse an empty match, naming what was asked for.

    A feature applied to nothing reports success and leaves a part that is wrong in a
    way no assertion about that feature can catch — because the feature is not there to
    be measured.
    """
    if matched:
        return matched
    raise GeometryError(
        f"{tool} matched no {predicate.describe()} on this shape. Check the predicate "
        "against the geometry that exists at this point in the build, not against the "
        "finished part."
    )


__all__ = ["EXTREME_TOLERANCE_MM", "require_matches", "resolve"]
