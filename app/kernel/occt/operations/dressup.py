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
from app.kernel.occt.selectors import select_edges, select_faces

FILLET = "catia_fillet"
CHAMFER = "catia_chamfer"
DRAFT = "catia_draft"

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
_UNSUPPORTED_MODES: Final[dict[str, str]] = {
    "reflect_line": (
        "drafting about a reflect line needs the silhouette curve of the pull direction, "
        "which is generative-shape work (Phase 2.6)"
    ),
    "variable": (
        "a variable-angle draft needs a law along the face, which the operation "
        "vocabulary has no way to carry yet"
    ),
}

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

    selected = select_edges(source, arguments.get("edges"), tool=tool, document=document)
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
    if arguments.get("parting"):
        raise OperationNotSupported(
            f"{DRAFT} with a parting element",
            "splitting the draft at a parting line needs the parting surface resolved "
            "against the part, which is Phase 2.6 work",
        )

    angle_deg = arguments.get("angle_deg")
    if not isinstance(angle_deg, (int, float)) or isinstance(angle_deg, bool):
        raise GeometryError(
            f"{DRAFT} needs angle_deg as a number of degrees, got {angle_deg!r}. A "
            "typical moulding draft is 1–3°."
        )

    neutral_plane, plane_normal = _neutral_plane(arguments.get("neutral"))
    pull = _pull_direction(arguments.get("pulling_direction"), plane_normal)

    selected = select_faces(source, arguments.get("faces"), tool=DRAFT, document=document)
    if not selected:
        raise GeometryError(
            f"{DRAFT} matched no faces on this shape. The selector "
            f"{arguments.get('faces')!r} found nothing — check it against the geometry "
            "that exists at this point in the build, not the finished part."
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


def _neutral_plane(value: Any) -> tuple[Any, tuple[float, float, float]]:
    """The plane the taper pivots about, and its normal.

    Only the origin planes are accepted. A neutral plane taken from a *face* of the part
    is the other half of this argument's meaning in CATIA and needs `feature#selector`
    (Phase 2.2) to name one — so it is refused with that reason rather than approximated
    by whichever origin plane happens to be nearest, which would tilt the part about the
    wrong height and change every dimension downstream.
    """
    if value is None:
        raise GeometryError(
            f"{DRAFT} needs a neutral plane — the section that keeps its size while "
            f"everything else tapers about it. Name one of: {', '.join(_ORIGIN_PLANES)}."
        )
    name = str(value).strip().upper()
    if name not in _ORIGIN_PLANES:
        raise OperationNotSupported(
            f"{DRAFT} with neutral={value!r}",
            "this backend takes an origin plane as the neutral element "
            f"({', '.join(sorted(_ORIGIN_PLANES))}). Using a face of the part needs the "
            "feature#selector syntax (Phase 2.2)",
        )
    origin, normal = _ORIGIN_PLANES[name]
    plane = symbol("gp_Pln")(symbol("gp_Pnt")(*origin), symbol("gp_Dir")(*normal))
    return plane, normal


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


__all__ = ["CHAMFER", "DRAFT", "FILLET", "chamfer", "draft", "fillet"]
