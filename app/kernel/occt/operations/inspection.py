"""Reading the part: measurement and analysis, without changing anything.

Four operations along two axes — *what* is measured, and *how much of a premise* the
answer carries.

`catia_measure` reports what the whole part **is**: always defined, always cheap enough
to run after every operation. `catia_measure_item` asks the same of **one element** — the
length of an edge, the diameter of a bore. `catia_measure_between` asks about **a pair**,
which is how a clearance is checked without guessing from a screenshot. All three are
plain properties. `catia_analysis_part` is the odd one out: it asks what the part can be
**made into**, which has a premise, can be inapplicable, and costs thousands of kernel
calls.

`catia_measure` is deliberately more than a dimension read — it returns bounding box,
volume, mass, centre of gravity and surface area in one call, because those are what a
following FEA setup or a sanity check actually need, and three round trips to collect
them is three chances to go wrong. That reasoning is the registry's, and this honours it.
The two element-scoped operations follow it: `catia_measure_between` computes distance,
overlap and closest points from the one extremum search that yields all three, and
`kind` selects the *headline* rather than gating the work.

**What each element reference may name lives in `app.kernel.occt.elements`**, not here,
so the two operations cannot drift apart on what `boss#top` means.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Final

from app.kernel import provenance
from app.kernel.errors import OperationNotSupported
from app.kernel.interrogation import InterrogationPayload
from app.kernel.measurement import Detail
from app.kernel.occt import classify, elements, metrology
from app.kernel.occt.binding import symbol
from app.kernel.occt.operations.context import BuildContext
from app.kernel.occt.selectors import select_faces
from app.kernel.occt.topology import edges, faces

MEASURE = "catia_measure"
MEASURE_BETWEEN = "catia_measure_between"
MEASURE_ITEM = "catia_measure_item"
ANALYSIS = "catia_analysis_part"
LIST_FACES = "catia_list_faces"
LIST_EDGES = "catia_list_edges"

#: Face-kind words that name one surface classification, mapped to OCCT's spelling.
#: `all` and `other` are deliberately absent: they are not surface types, and putting
#: them here with an empty string would make the complement test in `_face_matches`
#: compare against a surface no face can have.
_FACE_KINDS: Final[dict[str, str]] = {
    "planar": "Plane",
    "cylindrical": "Cylinder",
    "conical": "Cone",
    "spherical": "Sphere",
}

#: Every face-kind word the registry's enum allows, which is the four above plus the two
#: that are not surface types.
_FACE_KIND_WORDS: Final[frozenset[str]] = frozenset({*_FACE_KINDS, "all", "other"})

#: Edge-kind words from the registry's own enum. Unlike faces these are not all surface
#: classifications — `convex`/`concave` are adjacency facts — so the filter is a chain of
#: tests rather than a lookup.
_EDGE_KINDS: Final[frozenset[str]] = frozenset(
    {"all", "linear", "circular", "convex", "concave"}
)

#: What `catia_measure_between` can be asked for, in the registry's own spelling.
#: `minimum_distance` and `closest_points` come from one search and differ only in which
#: number is the headline; `angle` is a different computation with a different premise.
_BETWEEN_KINDS: Final[frozenset[str]] = frozenset(
    {"minimum_distance", "angle", "closest_points"}
)

_DEFAULT_BETWEEN_KIND: Final = "minimum_distance"

#: The registry names a *plane* for the pull, which is how a CATIA user thinks about it —
#: "pulled off the XY plane". A pull direction is the plane's normal. Declared here rather
#: than derived so the mapping is visible: getting it wrong silently analyses draft
#: against the wrong axis and reports a plausible number for the wrong question.
_PULL_NORMALS: Final[dict[str, tuple[float, float, float]]] = {
    "XY": (0.0, 0.0, 1.0),
    "YZ": (1.0, 0.0, 0.0),
    "ZX": (0.0, 1.0, 0.0),
}

_DEFAULT_PULL: Final = "XY"


def measure(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Measure the part as it currently stands.

    An explicit `catia_measure` always computes at least `Detail.FULL`, whatever the
    runner's default post-state level is: a caller who asked to measure wants the
    numbers, and returning a cheaper payload because a batch replay lowered the default
    would silently answer a different question than the one asked.
    """
    document = context.require_document()
    detail = Detail.INERTIA if arguments.get("include_inertia") else Detail.FULL
    return document.measure(detail=detail)


def measure_between(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Minimum distance, overlap and closest points between two named elements.

    **Both numbers are measured, not sampled** — `BRepExtrema_DistShapeShape` is an exact
    extremum search over the B-rep and the overlap is a real boolean whose volume is
    integrated. That is worth saying because clearance *sounds* like the kind of thing
    that would be approximated, and here it is not.

    Distance and overlap are not redundant: two shapes that interpenetrate have a minimum
    distance of zero, and so do two that merely touch. Only the common volume separates
    "in contact" from "inside each other".
    """
    from app.kernel.occt.interrogate import measure_clearance, plane_separation

    document = context.require_document()
    kind = str(arguments.get("kind") or _DEFAULT_BETWEEN_KIND).strip().lower()
    if kind not in _BETWEEN_KINDS:
        supported = ", ".join(sorted(_BETWEEN_KINDS))
        raise OperationNotSupported(
            subject=MEASURE_BETWEEN,
            reason=f"{kind!r} is not a measurement this backend takes. Supported: {supported}",
            backend="occt",
        )

    first, second = elements.resolve_elements(
        document, arguments.get("elements"), tool=MEASURE_BETWEEN
    )

    payload: dict[str, Any] = {
        "measurement": kind,
        "elements": [first.to_dict(), second.to_dict()],
    }

    if kind == "angle":
        payload.update(elements.angle_between(first, second, tool=MEASURE_BETWEEN))
        provenance.attach(
            payload, "angle_deg", provenance.measured("exact directions of both elements")
        )
        return payload

    if first.is_plane and second.is_plane:
        report = plane_separation(first.frame, second.frame)
    else:
        report = measure_clearance(first.require_shape(), second.require_shape())
        if first.is_plane or second.is_plane:
            report = replace(
                report,
                interference_unavailable=(
                    "a construction plane bounds no volume, so an overlap between these "
                    "two elements is not a measurable quantity."
                ),
            )

    payload.update(report.to_payload())
    return payload


def measure_item(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Measure one element — whatever kind of thing it turns out to be.

    **The payload says which kind it found**, because that is what makes an unexpected
    answer traceable rather than mysterious: asking for the diameter of something that
    turned out to be a planar face returns an area and the word `Plane`, not a silence
    and not a zero.

    A selector that matched several entities is measured in aggregate — total length for
    edges, total area for faces — with the count reported beside it. Aggregating without
    saying how many were aggregated is how "the area of the top face" quietly becomes the
    area of four of them.
    """
    document = context.require_document()
    element = elements.resolve_element(
        document, arguments.get("element"), tool=MEASURE_ITEM
    )

    payload: dict[str, Any] = {"element": element.to_dict()}

    if element.kind == "body":
        # A named feature is a whole solid, and "measure this body" means the same
        # numbers `catia_measure` gives for the part — volume, mass, centre of gravity,
        # box. Reporting only its surface area because this operation is element-scoped
        # would be an answer nobody wants when the full one is one call away.
        payload["measured_kind"] = "body"
        payload.update(
            metrology.measure(
                element.require_shape(),
                density_kg_m3=document.density_kg_m3,
                detail=Detail.FULL,
            )
        )
        return payload

    if element.kind in {"point", "axis_system"}:
        payload["measured_kind"] = "point"
        payload["position_mm"] = list(element.position or (0.0, 0.0, 0.0))
        provenance.attach(payload, "position_mm", provenance.measured("exact construction"))
        return payload

    if element.is_plane:
        payload["measured_kind"] = "plane"
        payload["position_mm"] = list(element.position or (0.0, 0.0, 0.0))
        normal = element.frame.Direction()
        payload["normal"] = [normal.X(), normal.Y(), normal.Z()]
        provenance.attach(payload, "normal", provenance.measured("exact construction"))
        return payload

    if element.kind == "edges":
        found = edges(element.require_shape())
        payload["measured_kind"] = (
            classify.edge_curve_type(found[0]) if len(found) == 1 else "edges"
        )
        payload["length_mm"] = sum(classify.edge_length_mm(edge) for edge in found)
        provenance.attach(payload, "length_mm", provenance.measured("curve integration"))
        _add_circle_size(payload, found)
        return payload

    found = faces(element.require_shape())
    payload["measured_kind"] = (
        classify.face_surface_type(found[0]) if len(found) == 1 else "faces"
    )
    payload["area_mm2"] = sum(classify.face_area_mm2(face) for face in found)
    provenance.attach(payload, "area_mm2", provenance.measured("surface integration"))

    if len(found) == 1:
        normal = classify.face_normal(found[0])
        if normal is not None:
            payload["normal"] = list(normal)
            provenance.attach(
                payload, "normal", provenance.measured("outward normal at the face centre")
            )
        diameter = classify.cylinder_diameter_mm(found[0])
        if diameter is not None:
            payload["diameter_mm"] = diameter
            payload["radius_mm"] = diameter / 2.0
            provenance.attach(
                payload, "diameter_mm", provenance.measured("exact cylindrical surface")
            )
    return payload


def _add_circle_size(payload: dict[str, Any], found: list[Any]) -> None:
    """Diameter of a single circular edge — the bore question, asked of an edge.

    Only for exactly one edge: summing the diameters of four holes would produce a number
    with no meaning that reads exactly like one with meaning.
    """
    if len(found) != 1 or classify.edge_curve_type(found[0]) != "Circle":
        return
    radius = float(symbol("BRepAdaptor_Curve")(found[0]).Circle().Radius())
    payload["radius_mm"] = radius
    payload["diameter_mm"] = radius * 2.0
    provenance.attach(
        payload, "diameter_mm", provenance.measured("exact circular edge")
    )


def list_faces(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Every face of the part, or of one feature, with area, centre and outward normal.

    **This is what turns a guess into a selector.** Its whole job is to be read before an
    operation that acts on a face, so the design can name what it wants — and the payload
    carries the *predicate* that would select each face, not just a number, because an
    index into this list is exactly the fragility `app.kernel.selection` exists to remove.
    """
    document = context.require_document()
    shape = _shape_to_list(context, document, arguments, LIST_FACES)

    kind = str(arguments.get("kind") or "all").lower()
    if kind not in _FACE_KIND_WORDS:
        allowed = ", ".join(sorted(_FACE_KIND_WORDS))
        raise OperationNotSupported(
            subject=LIST_FACES,
            reason=f"{kind!r} is not a face kind. Supported: {allowed}",
            backend="occt",
        )

    minimum = float(arguments.get("min_area_mm2") or 0.0)
    listed = []
    for face in faces(shape):
        surface = classify.face_surface_type(face)
        if not _face_matches(kind, surface):
            continue
        area = classify.face_area_mm2(face)
        if area < minimum:
            continue

        entry: dict[str, Any] = {
            "surface": surface,
            "area_mm2": area,
            "centre_mm": list(elements.face_centre(face)),
        }
        normal = classify.face_normal(face)
        if normal is not None:
            entry["normal"] = list(normal)
            entry["selector"] = {"normal": list(normal), "planar": surface == "Plane"}
        diameter = classify.cylinder_diameter_mm(face)
        if diameter is not None:
            entry["diameter_mm"] = diameter
            entry["selector"] = {"cylindrical": True, "diameter_mm": diameter}
        listed.append(entry)

    return {"faces": listed, "count": len(listed), "of": arguments.get("feature") or "part"}


def list_edges(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Every edge, with length, midpoint and whether it is convex or concave.

    Convexity is the reason this is not just a length list: an outside corner takes a
    round and an inside corner takes a different radius and often a different intent.
    An edge with fewer than two faces, or a tangent join, is **neither** and says so
    rather than defaulting to one — which is what stops a selector from filleting a face
    that is already round.
    """
    document = context.require_document()
    shape = _shape_to_list(context, document, arguments, LIST_EDGES)

    kind = str(arguments.get("kind") or "all").lower()
    if kind not in _EDGE_KINDS:
        allowed = ", ".join(sorted(_EDGE_KINDS))
        raise OperationNotSupported(
            subject=LIST_EDGES,
            reason=f"{kind!r} is not an edge kind. Supported: {allowed}",
            backend="occt",
        )

    candidates = edges(shape)
    if arguments.get("face"):
        wanted = select_faces(
            document.shape, arguments["face"], tool=LIST_EDGES, document=document
        )
        bounding = [edge for face in wanted for edge in edges(face)]
        candidates = [
            edge for edge in candidates if any(edge.IsSame(other) for other in bounding)
        ]

    # Always built, and always from the **whole part**: convexity is a fact about the two
    # faces meeting at an edge, so a feature-restricted compound of edges alone would
    # report every edge as neither convex nor concave. Same trap `resolve._resolve_edges`
    # documents, reached by a different route.
    mapping = classify.faces_by_edge(document.shape)

    minimum = float(arguments.get("min_length_mm") or 0.0)
    listed = []
    for edge in candidates:
        curve = classify.edge_curve_type(edge)
        if kind == "linear" and curve != "Line":
            continue
        if kind == "circular" and curve not in {"Circle", "Ellipse"}:
            continue

        convex = classify.edge_is_convex(edge, classify.adjoining_faces(mapping, edge))
        if kind == "convex" and convex is not True:
            continue
        if kind == "concave" and convex is not False:
            continue

        length = classify.edge_length_mm(edge)
        if length < minimum:
            continue

        listed.append(
            {
                "curve": curve,
                "length_mm": length,
                "midpoint_mm": list(elements.edge_midpoint(edge)),
                "convex": convex,
            }
        )

    return {"edges": listed, "count": len(listed), "of": arguments.get("feature") or "part"}


def _shape_to_list(
    context: BuildContext, document: Any, arguments: Mapping[str, Any], tool: str
) -> Any:
    """The whole part, or one feature's own entities as a compound."""
    part = context.require_shape(tool)
    named = arguments.get("feature")
    if not named:
        return part
    return elements.resolve_element(document, f"{named}#all", tool=tool).require_shape()


def _face_matches(kind: str, surface: str) -> bool:
    """Does a face of this surface type belong in a listing filtered by `kind`?

    `other` is everything the four named kinds do not cover — a B-spline, a torus, a
    surface of revolution — which is why it is a complement rather than another entry
    in the mapping.
    """
    if kind == "all":
        return True
    if kind == "other":
        return surface not in _FACE_KINDS.values()
    return surface == _FACE_KINDS[kind]


def analysis_part(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Draft, wall thickness, curvature or validity, on the part as it stands.

    Every arm returns its numbers **with a provenance sidecar** saying whether they were
    measured or approximated, because they are not the same: validity and draft-on-planes
    are exact, thickness and curvature are sampled. A payload that flattened them into
    one confidence would make the exact numbers untrustworthy and the sampled ones
    overtrusted at the same time.

    `draft` also reports undercuts, which the registry has no separate kind for and which
    share its premise exactly — the same pull direction answers both, and "this face has
    good draft but the tool cannot reach it" is the answer a mould designer needs to see
    together rather than in two calls.
    """
    from app.kernel.occt import interrogate

    document = context.require_document()
    shape = document.shape
    if shape is None:
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                "this document has no geometry yet, so there is nothing to analyse. "
                "Build a feature before asking about its draft or wall thickness"
            ),
            backend="occt",
        )

    kind = str(arguments.get("kind") or "").strip().lower()
    payload = InterrogationPayload()

    if kind == "thickness":
        report = interrogate.scan_thickness(shape)
        payload.add(report)
        minimum = _optional_float(arguments.get("minimum_mm"))
        if minimum is not None:
            payload.merge(_below_minimum(report, minimum))

    elif kind == "draft":
        pull = _pull_direction(arguments.get("direction"))
        required = _optional_float(arguments.get("minimum_mm")) or 0.0
        payload.add(interrogate.analyse_draft(shape, pull, required_deg=required))
        payload.add(interrogate.find_undercuts(shape, pull))

    elif kind == "curvature":
        payload.add(interrogate.scan_curvature(shape))
        payload.add(interrogate.scan_continuity(shape))

    elif kind == "validity":
        payload.add(interrogate.check_validity(shape))

    else:
        supported = ", ".join(sorted(_SUPPORTED_KINDS))
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                f"{kind!r} is not an analysis this backend runs. Supported: {supported}"
            ),
            backend="occt",
        )

    payload.values["analysis_kind"] = kind
    return payload.as_dict()


#: What `analysis_part` implements, checked against the registry's own enum by the tests
#: so a kind added to the vocabulary cannot be silently left unimplemented here.
_SUPPORTED_KINDS: Final[frozenset[str]] = frozenset(
    {"thickness", "draft", "curvature", "validity"}
)


def _below_minimum(report: Any, minimum_mm: float) -> dict[str, Any]:
    """Which sampled points fall under a stated minimum wall.

    Reported as points rather than as a bare count: the registry's summary promises this
    finds walls "too thin to mould or print before they reach the shop floor", and a
    count alone sends someone hunting for them.
    """
    thin = [
        {"point_mm": list(sample.point), "thickness_mm": sample.thickness_mm}
        for sample in report.samples
        if sample.thickness_mm < minimum_mm
    ]
    return {
        "minimum_required_mm": minimum_mm,
        "below_minimum_count": len(thin),
        # Capped so a badly-thin part cannot return a payload of thousands of points.
        # The count above is complete; this is the sample to go and look at.
        "below_minimum_points": thin[:_MAX_REPORTED_POINTS],
        "below_minimum_truncated": len(thin) > _MAX_REPORTED_POINTS,
    }


#: How many offending points a thickness analysis lists. The count is always exact; this
#: bounds the payload, and `below_minimum_truncated` says when it bit.
_MAX_REPORTED_POINTS: Final = 20


def _pull_direction(value: Any) -> tuple[float, float, float]:
    if value is None:
        return _PULL_NORMALS[_DEFAULT_PULL]
    name = str(value).strip().upper()
    if name not in _PULL_NORMALS:
        allowed = ", ".join(sorted(_PULL_NORMALS))
        raise OperationNotSupported(
            subject=ANALYSIS,
            reason=(
                f"{value!r} is not a pull direction. Give the plane the part is drawn "
                f"off, one of: {allowed}"
            ),
            backend="occt",
        )
    return _PULL_NORMALS[name]


def _optional_float(value: Any) -> float | None:
    """A number the registry declares as a string, or None when absent or unreadable.

    `minimum_mm` is typed as a string in the operation schema because the CATIA daemon
    receives it that way. Refusing to parse it would make the argument unusable; guessing
    a default when it is malformed would silently analyse against a threshold nobody
    chose. None means "no threshold was given", and the analysis still reports its
    minimum.
    """
    if value is None or value == "":
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = ["ANALYSIS", "MEASURE", "analysis_part", "measure"]
