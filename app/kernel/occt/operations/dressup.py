"""Dress-up features: fillet, chamfer and draft, applied to selected entities.

These are the first operations that *transform* existing geometry rather than create it,
which makes them the first that must record a naming history — and the reason the naming
rules in `app.kernel.occt.naming` were discovered here rather than in the primitives.

Draft belongs here rather than with the solid features for the reason CATIA puts it in
the same toolbar: it does not add or remove a feature, it tilts faces that already exist.
It is also the other half of the draft *analysis* in `app.kernel.occt.interrogate.draft`
— one finds the walls that will drag in the mould, the other fixes them, and they are
checked against each other (taper a box by 3° and the analyser must then measure 3°).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt import classify
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import (
    descendants_of,
    edges_bounding,
    evolution_of,
    faces_generated_by,
    record_derived,
)
from app.kernel.occt.operations.context import (
    BuildContext,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.selectors import SUB_ENTITY_MARK, select_edges, select_faces
from app.kernel.occt.topology import edges, faces, has_solid

FILLET = "catia_fillet"
CHAMFER = "catia_chamfer"
DRAFT = "catia_draft"
FILLET_EDGES = "catia_fillet_edges"
FILLET_VARIABLE = "catia_fillet_variable"
FILLET_TRITANGENT = "catia_fillet_tritangent"
THICKNESS = "catia_thickness"
REMOVE_FACE = "catia_remove_face"

#: Join tolerance for a face offset. OCCT's own default for this operation — the same
#: value `catia_shell` uses, and for the same reason: tightening it makes the offset fail
#: on ordinary parts rather than making it stricter.
THICKNESS_TOLERANCE_MM = 1e-3

#: The origin planes a `neutral` argument may name, as (point, normal). The neutral plane
#: is the one the taper pivots about — the section that keeps its original size — so
#: naming it wrongly tapers the part about the wrong height and changes every dimension
#: the design cared about.
_ORIGIN_PLANES: Final[dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]] = {
    "XY": ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "YZ": ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
    "ZX": ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}

#: Draft modes the registry allows that this backend does not do, and what each needs.
#:
#: Neither is the *parting element*, which is a separate argument and is implemented —
#: a reflect-line draft pivots about the silhouette OCCT would have to compute, where a
#: parting element is a plane the design already named.
_UNSUPPORTED_MODES: Final[dict[str, str]] = {
    "reflect_line": (
        "the silhouette itself is available now — catia_curve_reflect_line builds it — but "
        "the draft is not, and the reason is a different one: OCCT's draft takes a neutral "
        "*plane*, and a reflect-line draft pivots about a curve lying on the face. Doing it "
        "means building the ruled surface that leaves that curve at the draft angle and "
        "replacing the face with it, which is surfacing work rather than a missing "
        "argument. A draft split at a plane is the `parting` argument and does not need "
        "this mode"
    ),
    "variable": (
        "a variable-angle draft needs a law along the face, which the operation "
        "vocabulary has no way to carry yet"
    ),
}

#: How far past the part a parting half-space reaches, as a multiple of the distance from
#: the parting plane to the furthest corner of the part's bounding box. Only has to clear
#: the material; the intersection trims it back to the part either way.
_HALF_SPACE_FACTOR: Final = 1.5

#: Advice appended to a dress-up failure. The dominant real cause by a wide margin, and
#: naming it turns an opaque kernel refusal into something the agent can act on.
_ADVICE = (
    "The commonest cause is a size larger than the narrowest face the feature runs "
    "along — reduce it, or apply it to fewer edges. Some edges cannot take the feature "
    "at all (a sphere's seam, for one)."
)


def fillet(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    return _dress_up(context, arguments, FILLET, "BRepFilletAPI_MakeFillet", "radius_mm")


def chamfer(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    return _dress_up(context, arguments, CHAMFER, "BRepFilletAPI_MakeChamfer", "length_mm")


def _dress_up(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    maker_symbol: str,
    size_argument: str,
) -> Mapping[str, Any]:
    document = context.require_document()
    source = context.require_shape(tool)

    # The registry names a fillet's size `radius_mm` and a chamfer's `length_mm`, but
    # both accept the other spelling in practice; read the operation's own first.
    raw = arguments.get(size_argument)
    if raw is None:
        raw = arguments.get("radius_mm") if size_argument != "radius_mm" else arguments.get(
            "length_mm"
        )

    selected = select_edges(
        source, _scoped_selector(arguments, tool=tool), tool=tool, document=document
    )
    if not selected:
        raise GeometryError(
            f"{tool} matched no edges on this shape. The selector "
            f"{arguments.get('edges')!r} found nothing — check it against the geometry "
            "that exists at this point in the build, not the finished part."
        )

    sizes = _sizes_for(raw, len(selected), tool=tool, argument=size_argument)

    maker = symbol(maker_symbol)(source)
    for edge, size in zip(selected, sizes, strict=True):
        maker.Add(size, edge)
    span = f"{sizes[0]} mm" if len(set(sizes)) == 1 else f"{min(sizes)}–{max(sizes)} mm"
    result = build_or_raise(
        maker, tool=f"{tool} at {span} on {len(selected)} edge(s)", detail=_ADVICE
    )

    feature = document.add_feature(
        feature_name(arguments, tool.removeprefix("catia_")), tool
    )
    modified, generated = evolution_of(maker, source)
    blend = faces_generated_by(maker, source)
    document.set_result(
        feature,
        result,
        contributed=(blend, edges_bounding(blend)),
        evolved_by=maker,
    )
    record_derived(
        feature.labels,
        result=result,
        source=source,
        modified=modified,
        generated=generated,
    )
    return context.result_for(feature)


def _scoped_selector(arguments: Mapping[str, Any], *, tool: str) -> Any:
    """`edges`, narrowed to `feature` when one was named.

    **`feature` was declared and silently dropped**, which is the worst shape a bug can
    take here: `catia_fillet(feature="boss", edges="vertical")` rounded every vertical
    edge on the part, reported success, and produced a part that looks plausible in a
    screenshot. The design suite's own bracket fixture is written that way, so the
    canonical example of the vocabulary was relying on an argument nothing read. Found by
    E2's Proof — the first thing to author a part through the design IR and the kernel
    together, which is exactly where two layers that each look right disagree.

    The two spellings mean the same thing and compose to the same predicate: naming
    `feature` alongside a word is `feature#word`, and alongside a predicate it sets `of`.
    Giving both a `feature` and an `edges` that already carries a `#` is refused rather
    than resolved by precedence — two answers to "whose edges" is not a question this
    should be picking a winner for.
    """
    selector = arguments.get("edges")
    scope = arguments.get("feature")
    if not scope:
        return selector

    if isinstance(selector, Mapping):
        if selector.get("of") not in (None, scope):
            raise GeometryError(
                f"{tool} was given feature={scope!r} and a predicate that already names "
                f"of={selector.get('of')!r}. Those are two answers to whose edges these "
                "are — give one."
            )
        return {**selector, "of": scope}

    word = str(selector or "all").strip()
    if SUB_ENTITY_MARK in word:
        raise GeometryError(
            f"{tool} was given feature={scope!r} and edges={word!r}, which already names "
            "a feature of its own. Give one or the other."
        )
    return f"{scope}{SUB_ENTITY_MARK}{word}"


def _sizes_for(raw: Any, count: int, *, tool: str, argument: str) -> list[float]:
    """One size per selected edge — master plan 2.3, per-entity parameters.

    A single number applies to every edge, which is the common case and what the
    vocabulary meant before this. A list gives each selected edge its own size, matched
    against the resolution order `resolve()` guarantees.

    The lengths must match exactly. Padding a short list with a default, or ignoring a
    long one, would silently fillet some edges at a size nobody chose — and a fillet at
    the wrong radius is a part that looks right and is not.
    """
    if isinstance(raw, (list, tuple)):
        if len(raw) != count:
            raise GeometryError(
                f"{tool} was given {len(raw)} sizes for {count} selected edge(s). A "
                "per-edge list must match the selection exactly; give one number to "
                "apply the same size to all of them."
            )
        return [
            as_positive_length(value, argument=f"{argument}[{index}]", tool=tool)
            for index, value in enumerate(raw)
        ]

    return [as_positive_length(raw, argument=argument, tool=tool)] * count


# -- draft --------------------------------------------------------------------


def draft(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Taper faces away from a pulling direction so the part can leave a mould.

    The repair half of the loop `interrogate.draft` opens: that scan names the faces that
    will drag, this tilts them. The two are verified against each other rather than
    against recorded output — taper a box by 3° and the analyser must then measure 3° on
    it, which neither could fake alone.

    **The neutral plane is the one that does not move.** Everything else pivots about its
    intersection with each face, so a wall tapered about the base keeps its footprint and
    loses width at the top, while the same wall tapered about the top does the opposite.
    That is a real change to every dimension downstream, which is why `neutral` is
    required by the operation rather than defaulted to something convenient.

    **Only planar, cylindrical and conical faces can be tapered**, and OCCT propagates
    the taper along tangent-continuous neighbours of whatever is named. Both are the
    kernel's rules, not this wrapper's; a face it will not take is reported by name
    rather than silently skipped.

    **A `parting` element makes it a two-sided draft**, which is a different part: each
    side of the parting plane tapers away from it, so the section is widest (or
    narrowest) *there* rather than at one end. That is what a two-part mould needs — both
    halves have to release — and it is the case a single taper cannot express at all.
    """
    document = context.require_document()
    source = context.require_shape(DRAFT)

    mode = str(arguments.get("mode") or "standard").strip().lower()
    if mode in _UNSUPPORTED_MODES:
        raise OperationNotSupported(f"{DRAFT} in {mode!r} mode", _UNSUPPORTED_MODES[mode])
    if mode != "standard":
        raise OperationNotSupported(
            f"{DRAFT} in {mode!r} mode", "the modes this backend knows are: standard"
        )

    angle_deg = arguments.get("angle_deg")
    if not isinstance(angle_deg, (int, float)) or isinstance(angle_deg, bool):
        raise GeometryError(
            f"{DRAFT} needs angle_deg as a number of degrees, got {angle_deg!r}. A "
            "typical moulding draft is 1–3°."
        )

    neutral_plane, plane_normal = _neutral_plane(
        document, source, arguments.get("neutral")
    )
    pull = _pull_direction(arguments.get("pulling_direction"), plane_normal)

    selected = select_faces(source, arguments.get("faces"), tool=DRAFT, document=document)
    if not selected:
        raise GeometryError(
            f"{DRAFT} matched no faces on this shape. The selector "
            f"{arguments.get('faces')!r} found nothing — check it against the geometry "
            "that exists at this point in the build, not the finished part."
        )

    if arguments.get("parting"):
        return _parted_draft(
            context,
            document,
            arguments,
            source,
            selected,
            angle_deg=float(angle_deg),
            pull=pull,
        )

    maker = symbol("BRepOffsetAPI_DraftAngle")(source)
    for face in selected:
        maker.Add(face, pull, math.radians(float(angle_deg)), neutral_plane)

    result = build_or_raise(
        maker,
        tool=f"{DRAFT} at {angle_deg}° on {len(selected)} face(s)",
        detail="Only planar, cylindrical and conical faces can be tapered, and a taper "
        "steep enough to consume a face will fail. Reduce the angle, or select fewer "
        "faces.",
    )

    feature = document.add_feature(feature_name(arguments, "draft"), DRAFT)
    modified, generated = evolution_of(maker, source)
    # The faces that were *asked* for, mapped to where they ended up. Not
    # `faces_modified_by`: OCCT propagates a taper along tangent-continuous neighbours,
    # so drafting four walls of a filleted block tilts eight faces — the four walls and
    # the four blends between them. The blends are a side effect, exactly like a fillet's
    # trimming of its neighbours, and stay with whatever built them.
    tapered = descendants_of(maker, selected, kind="face")
    document.set_result(
        feature,
        result,
        contributed=(tapered, edges_bounding(tapered)),
        evolved_by=maker,
    )
    record_derived(
        feature.labels,
        result=result,
        source=source,
        modified=modified,
        generated=generated,
    )
    return context.result_for(feature)


def _neutral_plane(
    document: Any, source: Any, value: Any
) -> tuple[Any, tuple[float, float, float]]:
    """The plane the taper pivots about, and its normal."""
    if value is None:
        raise GeometryError(
            f"{DRAFT} needs a neutral plane — the section that keeps its size while "
            f"everything else tapers about it. Name one of: {', '.join(_ORIGIN_PLANES)}, "
            "a plane this design constructed, or a planar face of the part."
        )
    plane, _, normal = _plane_named(document, source, value, argument="neutral")
    return plane, normal


def _plane_named(
    document: Any, source: Any, value: Any, *, argument: str
) -> tuple[Any, tuple[float, float, float], tuple[float, float, float]]:
    """Resolve a plane argument to (plane, origin, normal), in a stated order.

    Origin plane, then a plane the design constructed, then a planar face of the part —
    the same precedence `sketcher.resolve_support` uses, and for the same reason: `XY` is
    vocabulary rather than a name, so nothing can shadow it.

    **The face case is resolved, not refused.** It used to be turned away as needing
    `feature#selector`, which is now built: `slab#top` names one face exactly, and a
    neutral element taken from the part is how a real draft is set up — the parting face
    of a moulding is a face of the moulding, not one of the three origin planes.
    """
    name = str(value).strip()
    if name.upper() in _ORIGIN_PLANES:
        origin, normal = _ORIGIN_PLANES[name.upper()]
        plane = symbol("gp_Pln")(symbol("gp_Pnt")(*origin), symbol("gp_Dir")(*normal))
        return plane, origin, normal

    if document.has_plane(name):
        constructed = document.plane(name)
        return constructed.plane(), constructed.origin_mm(), constructed.normal()

    faces_named = select_faces(source, value, tool=DRAFT, document=document)
    planar = [
        face for face in faces_named if classify.face_surface_type(face) == "Plane"
    ]
    if len(planar) != 1:
        known = ", ".join(document.plane_names()) or "none yet"
        raise GeometryError(
            f"{DRAFT} needs {argument}={value!r} to be exactly one plane, and it "
            f"resolved to {len(planar)} planar face(s) of the part. The origin planes "
            f"are {', '.join(_ORIGIN_PLANES)}; planes this design has constructed: "
            f"{known}. Name a single face with the feature#selector syntax, or build a "
            "plane with catia_plane_offset."
        )

    surface = symbol("BRepAdaptor_Surface")(planar[0])
    geometric = surface.Plane()
    location, direction = geometric.Location(), geometric.Axis().Direction()
    origin = (location.X(), location.Y(), location.Z())
    normal = (direction.X(), direction.Y(), direction.Z())
    return geometric, origin, normal


def _parted_draft(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    source: Any,
    selected: list[Any],
    *,
    angle_deg: float,
    pull: Any,
) -> Mapping[str, Any]:
    """Taper both sides of a parting plane, each away from it.

    **Built by drafting the whole part twice and keeping one half of each**, rather than
    by cutting the part in two and drafting the pieces. Both readings give the same
    solid, and this one keeps the face selection honest: the selector is resolved once,
    against the part the caller was looking at. Splitting first would ask it to match
    against two halves that did not exist when the design named anything, and a name that
    resolved on the whole part and not on a half would silently taper less than was
    asked.

    The join is exact rather than merely close. The parting plane *is* the neutral plane
    for both drafts, so the section there is the one thing neither taper moves — the two
    halves meet on identical geometry, and the fuse has nothing to reconcile.
    """
    parting = arguments.get("parting")
    plane, origin, normal = _plane_named(document, source, parting, argument="parting")

    radians = math.radians(angle_deg)
    reversed_pull = symbol("gp_Dir")(-pull.X(), -pull.Y(), -pull.Z())

    halves = []
    for direction, side_pull in ((normal, pull), (_negated(normal), reversed_pull)):
        maker = symbol("BRepOffsetAPI_DraftAngle")(source)
        for face in selected:
            maker.Add(face, side_pull, radians, plane)
        tapered = build_or_raise(
            maker,
            tool=f"{DRAFT} at {angle_deg}° about {parting!r} on {len(selected)} face(s)",
            detail="Only planar, cylindrical and conical faces can be tapered, and a "
            "taper steep enough to consume a face will fail. Reduce the angle, or "
            "select fewer faces.",
        )
        keep = symbol("BRepAlgoAPI_Common")(
            tapered, _half_space(source, origin, direction)
        )
        kept = build_or_raise(
            keep,
            tool=f"{DRAFT} (keeping the material on one side of {parting!r})",
            detail="The parting plane does not cut the part. Check that it passes "
            "through the material rather than beside it.",
        )
        if not has_solid(kept):
            raise GeometryError(
                f"{DRAFT} found no material on one side of the parting element "
                f"{parting!r}. A parting element splits the part in two, so it has to "
                "pass through it — a plane that misses the part drafts one side of "
                "nothing."
            )
        halves.append((maker, keep, kept))

    fuse = symbol("BRepAlgoAPI_Fuse")(halves[0][2], halves[1][2])
    result = build_or_raise(
        fuse,
        tool=f"{DRAFT} (rejoining the two sides of {parting!r})",
        detail="The two drafted halves did not rejoin. They meet on the parting plane, "
        "which neither taper moves, so this points at a parting element that is not "
        "planar where it crosses the part.",
    )

    # Where the drafted faces ended up, followed through every step that touched them.
    # `descendants_of` reads each algorithm's own history, so chaining it is the whole
    # answer — a face tapered by one half, trimmed by its intersection and carried
    # through the fuse arrives as the face it actually became.
    tapered_faces = [
        face
        for taper_maker, keep_maker, _ in halves
        for face in descendants_of(
            fuse,
            descendants_of(
                keep_maker, descendants_of(taper_maker, selected, kind="face"),
                kind="face",
            ),
            kind="face",
        )
    ]

    feature = document.add_feature(feature_name(arguments, "draft"), DRAFT)
    modified, generated = evolution_of(fuse, halves[0][2])
    document.set_result(
        feature,
        result,
        contributed=(tapered_faces, edges_bounding(tapered_faces)),
        evolved_by=fuse,
    )
    record_derived(
        feature.labels,
        result=result,
        source=source,
        modified=modified,
        generated=generated,
    )
    return context.result_for(feature)


def _half_space(source: Any, origin: tuple[float, float, float], direction: Any) -> Any:
    """A box covering everything on one side of a plane, and nothing on the other.

    A box rather than `BRepPrimAPI_MakeHalfSpace`: an intersection against a genuinely
    infinite solid is where boolean robustness goes, and the box only has to clear the
    part's own bounding box to mean the same thing.
    """
    box = symbol("Bnd_Box")()
    symbol("BRepBndLib").Add_s(source, box, True)
    x_min, y_min, z_min, x_max, y_max, z_max = box.Get()
    reach = _HALF_SPACE_FACTOR * max(
        math.dist(origin, corner)
        for corner in (
            (x_min, y_min, z_min),
            (x_max, y_max, z_max),
            (x_min, y_max, z_min),
            (x_max, y_min, z_max),
            (x_min, y_min, z_max),
            (x_max, y_max, z_min),
            (x_min, y_max, z_max),
            (x_max, y_min, z_min),
        )
    )

    axis = symbol("gp_Ax2")(symbol("gp_Pnt")(*origin), symbol("gp_Dir")(*direction))
    x_dir, y_dir = axis.XDirection(), axis.YDirection()
    corner = tuple(
        origin[index] - (x_dir.Coord(index + 1) + y_dir.Coord(index + 1)) * reach
        for index in range(3)
    )
    placed = symbol("gp_Ax2")(
        symbol("gp_Pnt")(*corner), symbol("gp_Dir")(*direction), x_dir
    )
    return build_or_raise(
        symbol("BRepPrimAPI_MakeBox")(placed, 2 * reach, 2 * reach, reach),
        tool=f"{DRAFT} (the half-space on one side of the parting element)",
        detail="The parting element's own frame could not be built into a half-space.",
    )


def _negated(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-vector[0], -vector[1], -vector[2])


def _pull_direction(value: Any, plane_normal: tuple[float, float, float]) -> Any:
    """The direction the mould opens, defaulting to the neutral plane's own normal.

    The default is the overwhelmingly common case — a part drawn off the XY plane is
    pulled along Z — and defaulting to it means a caller who names a neutral plane and
    nothing else gets the taper they meant.
    """
    if value is None:
        return symbol("gp_Dir")(*plane_normal)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise GeometryError(
            f"{DRAFT} needs pulling_direction as [x, y, z], got {value!r}."
        )
    x, y, z = (float(component) for component in value)
    if math.sqrt(x * x + y * y + z * z) < 1e-12:
        raise GeometryError(
            f"{DRAFT} was given a zero-length pulling direction, which points nowhere. "
            "Give the axis the mould opens along, e.g. [0, 0, 1]."
        )
    return symbol("gp_Dir")(x, y, z)


def fillet_edges(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """`catia_fillet_edges` — the same rounding, with the edge list named `edges`.

    A separate registry entry rather than an alias because its schema carries
    `propagation` and `edge_relimitation`, which `catia_fillet` does not. Both are
    accepted and only `propagation="tangency"` changes anything here: OCCT's fillet
    already walks the tangent chain from each seeded edge, so tangency is what this
    backend does natively and `minimal` is the mode it cannot honour.
    """
    propagation = str(arguments.get("propagation") or "tangency").lower()
    if propagation not in {"tangency", "minimal"}:
        raise GeometryError(
            f"{FILLET_EDGES} takes propagation as 'tangency' or 'minimal'; got "
            f"{propagation!r}."
        )
    if propagation == "minimal":
        raise OperationNotSupported(
            f"{FILLET_EDGES} with propagation='minimal'",
            "OCCT's fillet always continues along the tangent chain from a seeded edge, "
            "so stopping at exactly the named edge is not something this backend can "
            "ask it for. Use propagation='tangency', or select the chain you want and "
            "accept that it will be walked",
        )
    return _dress_up(context, arguments, FILLET_EDGES, "BRepFilletAPI_MakeFillet", "radius_mm")


def fillet_variable(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Round one edge with a radius that changes along its length — master plan 2.5.

    **The radii are placed by parameter, not by arc length.** `BRepFilletAPI_MakeFillet`
    takes (u, radius) pairs where u runs 0→1 along the edge, so two radii put one at
    each end and three put the middle one at the halfway *parameter*. On a line those
    are the same thing; on a spline they are not, and saying so is the difference
    between a documented convention and a surprise.
    """
    document = context.require_document()
    source = context.require_shape(FILLET_VARIABLE)

    selected = select_edges(
        source, arguments.get("edge") or arguments.get("edges"),
        tool=FILLET_VARIABLE, document=document,
    )
    radii = arguments.get("radii")
    if not isinstance(radii, (list, tuple)) or len(radii) < 2:
        raise GeometryError(
            f"{FILLET_VARIABLE} needs radii as a list of at least two numbers — one for "
            f"each end of the edge, and any in between; got {radii!r}."
        )
    sizes = [
        as_positive_length(value, argument="radii", tool=FILLET_VARIABLE) for value in radii
    ]

    variation = str(arguments.get("variation") or "linear").lower()
    if variation not in {"linear", "cubic"}:
        raise GeometryError(
            f"{FILLET_VARIABLE} takes variation as 'linear' or 'cubic'; got {variation!r}."
        )

    # The same law is applied to every selected edge, which is what CATIA does when
    # several are picked: each edge runs from `radii[0]` at its start to `radii[-1]` at
    # its end. Refusing more than one edge would make "taper all four corners" four
    # calls, and the law is per-edge in any case.
    maker = symbol("BRepFilletAPI_MakeFillet")(source)
    for edge in selected:
        parameters = symbol("TColgp_Array1OfPnt2d")(1, len(sizes))
        for index, size in enumerate(sizes):
            position = index / (len(sizes) - 1)
            parameters.SetValue(index + 1, symbol("gp_Pnt2d")(position, size))
        maker.Add(parameters, edge)

    result = build_or_raise(
        maker,
        tool=(
            f"{FILLET_VARIABLE} from {sizes[0]} to {sizes[-1]} mm on "
            f"{len(selected)} edge(s)"
        ),
        detail=_ADVICE,
    )
    return _record_dressup(
        context, document, arguments, FILLET_VARIABLE, source, result, maker
    )


def fillet_tritangent(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Round between three faces, removing the middle one.

    Not a radius operation: the radius is *implied* by the three faces, which is the
    whole point — a tritangent fillet is how a boss blends into a wall and disappears,
    and stating a radius as well would over-constrain it.
    """
    document = context.require_document()
    source = context.require_shape(FILLET_TRITANGENT)

    faces_named = arguments.get("faces")
    if not isinstance(faces_named, (list, tuple)) or len(faces_named) != 2:
        raise GeometryError(
            f"{FILLET_TRITANGENT} needs faces as the two faces the fillet stays tangent "
            f"to, with removed_face naming the one it consumes; got {faces_named!r}."
        )

    removed = select_faces(
        source, arguments.get("removed_face"), tool=FILLET_TRITANGENT, document=document
    )
    if len(removed) != 1:
        raise GeometryError(
            f"{FILLET_TRITANGENT} removes exactly one face, and removed_face matched "
            f"{len(removed)}."
        )

    kept = [
        face
        for named in faces_named
        for face in select_faces(source, named, tool=FILLET_TRITANGENT, document=document)
    ]
    if len(kept) != 2:
        raise GeometryError(
            f"{FILLET_TRITANGENT} stays tangent to exactly two faces, and the selectors "
            f"matched {len(kept)}."
        )

    maker = symbol("BRepFilletAPI_MakeFillet")(source)
    maker.Add(kept[0], removed[0], kept[1])
    result = build_or_raise(
        maker,
        tool=FILLET_TRITANGENT,
        detail="The three faces do not admit a tangent blend. The face being removed "
        "must lie between the other two and touch both.",
    )
    return _record_dressup(
        context, document, arguments, FILLET_TRITANGENT, source, result, maker
    )


def thickness(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Offset named faces outward or inward without hollowing the part.

    Safer than re-shelling with a different value, which is the reason the operation
    exists: a shell rebuilds every wall, and one that succeeded at 2 mm can fail at 2.5.
    """
    document = context.require_document()
    source = context.require_shape(THICKNESS)

    raw = arguments.get("thickness_mm")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw == 0.0:
        raise GeometryError(
            f"{THICKNESS} needs thickness_mm as a non-zero number of millimetres — "
            f"positive adds material, negative removes it; got {raw!r}."
        )

    named = arguments.get("faces")
    if not isinstance(named, (list, tuple)) or not named:
        raise GeometryError(
            f"{THICKNESS} needs faces as a list naming the faces to offset; got {named!r}."
        )

    selected = [
        face
        for selector in named
        for face in select_faces(source, selector, tool=THICKNESS, document=document)
    ]

    # **Not `MakeThickSolidByJoin`, which is the shell.** That call *removes* the faces
    # it is given and offsets everything else — the exact opposite of this operation,
    # and it fails silently plausibly: offsetting the top of a 60×40×10 plate by 5 gave
    # 26,974 mm³ where the answer is 36,000, which is wrong by an amount that looks like
    # a fillet rather than like a bug. Thickness moves *only* the named faces, so the
    # material it adds is each face swept along its own normal, fused (or cut) back.
    added = None
    for face in selected:
        if classify.face_surface_type(face) != "Plane":
            raise OperationNotSupported(
                f"{THICKNESS} on a {classify.face_surface_type(face).lower()} face",
                "this backend sweeps a face along its own normal, which is exact for a "
                "planar face and is not an offset for a curved one. Name the flat walls, "
                "or use catia_shell to offset the whole boundary",
            )
        normal = classify.face_normal(face)
        if normal is None:  # pragma: no cover - a planar face always has one
            raise GeometryError(f"{THICKNESS} could not read the normal of a named face.")

        reach = abs(float(raw))
        # Positive adds material outwards; negative removes it inwards. The sweep runs
        # the same way either way and the boolean decides which it was.
        sense = 1.0 if raw > 0 else -1.0
        vector = symbol("gp_Vec")(*[normal[i] * reach * sense for i in range(3)])
        slab = build_or_raise(
            symbol("BRepPrimAPI_MakePrism")(face, vector),
            tool=f"{THICKNESS} of {raw} mm",
            detail="A named face could not be swept along its normal.",
        )
        added = slab if added is None else _fused(added, slab)

    if added is None:  # pragma: no cover - an empty selection raises in select_faces
        raise GeometryError(f"{THICKNESS} matched no faces to offset.")

    combine = "BRepAlgoAPI_Fuse" if raw > 0 else "BRepAlgoAPI_Cut"
    maker = symbol(combine)(source, added)
    result = build_or_raise(
        maker,
        tool=f"{THICKNESS} of {raw} mm on {len(selected)} face(s)",
        detail="The offset material could not be combined with the part. A negative "
        "thickness deeper than the wall removes more than is there.",
    )
    if not has_solid(result):
        raise GeometryError(
            f"{THICKNESS} of {raw} mm consumed the whole part. The faces were offset "
            "inwards by more than the material behind them."
        )
    return _record_dressup(context, document, arguments, THICKNESS, source, result, maker)


def _fused(first: Any, second: Any) -> Any:
    maker = symbol("BRepAlgoAPI_Fuse")(first, second)
    if not maker.IsDone():
        raise GeometryError(
            "The offset slabs for two named faces could not be joined. They may meet at "
            "an edge where the two normals disagree about which way is out."
        )
    return maker.Shape()


def remove_face(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Delete faces and heal the surrounding surfaces back together.

    The repair operation for imported geometry: a hole nobody wants, a fillet from an
    earlier revision. `keep_faces` is accepted and refused rather than ignored — it
    inverts the meaning of the argument, so honouring only `faces` when both are given
    would delete exactly the faces the caller asked to keep.
    """
    document = context.require_document()
    source = context.require_shape(REMOVE_FACE)

    if arguments.get("keep_faces"):
        raise OperationNotSupported(
            f"{REMOVE_FACE} with keep_faces",
            "this backend removes the faces named in `faces`; inverting the selection "
            "would need the face set to be enumerated first. Name the faces to remove",
        )

    named = arguments.get("faces")
    if not isinstance(named, (list, tuple)) or not named:
        raise GeometryError(
            f"{REMOVE_FACE} needs faces as a list naming the faces to delete; got "
            f"{named!r}."
        )
    selected = [
        face
        for selector in named
        for face in select_faces(source, selector, tool=REMOVE_FACE, document=document)
    ]

    maker = symbol("BRepAlgoAPI_Defeaturing")()
    maker.SetShape(source)
    for face in selected:
        maker.AddFaceToRemove(face)
    maker.Build()
    if not maker.IsDone():
        raise GeometryError(
            f"{REMOVE_FACE} could not heal the part after removing {len(selected)} "
            "face(s). The surrounding surfaces do not extend far enough to meet each "
            "other — this happens when the face being removed is the only thing joining "
            "two otherwise separate regions."
        )
    result = maker.Shape()
    if not has_solid(result):
        raise GeometryError(
            f"{REMOVE_FACE} left no solid. Removing those faces opened the part rather "
            "than healing it."
        )
    return _record_dressup(context, document, arguments, REMOVE_FACE, source, result, None)


def _record_dressup(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    source: Any,
    result: Any,
    maker: Any,
) -> Mapping[str, Any]:
    """Record a dress-up feature's result and its naming history.

    Shared by every operation above that is not `_dress_up`'s per-edge shape. The
    contribution is the faces the operation *generated*, which for a blend is the blend
    surface and for an offset is the moved wall — never the neighbours it merely trimmed,
    which stay with whatever built them.
    """
    feature = document.add_feature(
        feature_name(arguments, tool.removeprefix("catia_")), tool
    )
    if maker is None:
        modified: list[Any] = []
        generated: list[Any] = []
        contributed = (faces(result), edges(result))
    else:
        modified, generated = evolution_of(maker, source)
        blend = faces_generated_by(maker, source)
        contributed = (blend, edges_bounding(blend)) if blend else (faces(result), edges(result))

    document.set_result(feature, result, contributed=contributed, evolved_by=maker)
    record_derived(
        feature.labels, result=result, source=source, modified=modified, generated=generated
    )
    return context.result_for(feature)


__all__ = [
    "CHAMFER",
    "DRAFT",
    "FILLET",
    "FILLET_EDGES",
    "FILLET_TRITANGENT",
    "FILLET_VARIABLE",
    "REMOVE_FACE",
    "THICKNESS",
    "THICKNESS_TOLERANCE_MM",
    "chamfer",
    "draft",
    "fillet",
    "fillet_edges",
    "fillet_tritangent",
    "fillet_variable",
    "remove_face",
    "thickness",
]
