"""Moving and reflecting geometry — translate, rotate, mirror, symmetry, scale.

A rigid transform preserves every measurement except position, which makes it the
cheapest useful check that the naming layer follows geometry rather than coordinates: a
name recorded before a translation must still resolve after it, and the face it finds
must have the same area in a different place.

**Mirror keeps the original; symmetry does not.** That is the whole difference between
them and it is easy to get backwards. `catia_mirror` is how a symmetric part is modelled
once and completed — the result is the union of the body and its reflection.
`catia_symmetry` *relocates* the body, which is what makes a left-hand version of a
right-hand part. Getting these the wrong way round produces a part that looks right in a
thumbnail and has twice or half the material.

**Scale about a point and scale about a plane are different transformations.** A point
reference scales uniformly in every direction — a similarity, which `gp_Trsf` expresses
exactly. A plane reference scales only along the plane's normal, which is not a
similarity at all: it needs the general `gp_GTrsf`. CATIA means the second when a plane
is named, so a "uniform scale about the plane's origin" would quietly build a different
part from the one asked for.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt import elements
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import contribution_of, evolution_of, record_derived
from app.kernel.occt.operations.context import (
    BuildContext,
    as_point,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.topology import edges, faces, has_solid

TRANSLATE = "catia_translate"
ROTATE = "catia_rotate"
MIRROR = "catia_mirror"
SYMMETRY = "catia_symmetry"
SCALE = "catia_scale"

#: OCCT copies the shape rather than transforming it in place. Required here: the source
#: shape is still referenced by the previous feature's naming history, and moving it
#: underneath that history would invalidate names already recorded against it.
COPY_ON_TRANSFORM = True


def translate(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Move the current shape by a vector, in millimetres."""
    document = context.require_document()
    source = context.require_shape(TRANSLATE)

    vector = as_point(
        arguments.get("vector") if arguments.get("vector") is not None
        else arguments.get("direction"),
        argument="vector",
    )

    transformation = symbol("gp_Trsf")()
    transformation.SetTranslation(symbol("gp_Vec")(*vector))
    maker = symbol("BRepBuilderAPI_Transform")(source, transformation, COPY_ON_TRANSFORM)
    result = build_or_raise(
        maker,
        tool=TRANSLATE,
        detail="A translation cannot fail on valid geometry; the source shape is "
        "probably empty or already invalid.",
    )

    feature = document.add_feature(feature_name(arguments, "translate"), TRANSLATE)
    modified, generated = evolution_of(maker, source)
    document.set_result(feature, result, contributed=(faces(result), edges(result)))
    record_derived(
        feature.labels,
        result=result,
        source=source,
        modified=modified,
        generated=generated,
    )
    return context.result_for(feature)


def rotate(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Turn the body about a named axis."""
    document = context.require_document()
    source = _body_argument(context, arguments, ROTATE)

    axis = elements.axis_for(document, arguments.get("axis"), tool=ROTATE)
    angle = arguments.get("angle_deg")
    if not isinstance(angle, (int, float)) or isinstance(angle, bool):
        raise GeometryError(
            f"{ROTATE} needs angle_deg as a number of degrees, got {angle!r}."
        )

    transformation = symbol("gp_Trsf")()
    transformation.SetRotation(axis, math.radians(float(angle)))
    return _apply(
        context,
        document,
        arguments,
        ROTATE,
        source,
        transformation,
        detail="A rotation cannot fail on valid geometry; the source shape is probably "
        "empty or already invalid.",
    )


def symmetry(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Replace the body with its mirror image — a left hand from a right hand."""
    document = context.require_document()
    source = _body_argument(context, arguments, SYMMETRY)

    transformation = symbol("gp_Trsf")()
    transformation.SetMirror(
        _mirror_axes(document, arguments.get("reference"), tool=SYMMETRY)
    )
    return _apply(
        context,
        document,
        arguments,
        SYMMETRY,
        source,
        transformation,
        detail="A reflection cannot fail on valid geometry; the source shape is "
        "probably empty or already invalid.",
    )


def mirror(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Reflect the body about a plane and **keep** the original, fusing the two.

    This is how a symmetric part is modelled once: build half, mirror it, and the halves
    stay identical through every later edit. A body that already straddles the mirror
    plane fuses into itself, which is harmless — the union is the same solid.
    """
    document = context.require_document()
    source = _body_argument(context, arguments, MIRROR)

    transformation = symbol("gp_Trsf")()
    transformation.SetMirror(_mirror_axes(document, arguments.get("plane"), tool=MIRROR))

    reflected = build_or_raise(
        symbol("BRepBuilderAPI_Transform")(source, transformation, COPY_ON_TRANSFORM),
        tool=MIRROR,
        detail="A reflection cannot fail on valid geometry; the source shape is "
        "probably empty or already invalid.",
    )
    maker = symbol("BRepAlgoAPI_Fuse")(source, reflected)
    result = build_or_raise(
        maker,
        tool=f"{MIRROR} (fuse)",
        detail="The body and its reflection could not be joined. They may not meet at "
        "the mirror plane at all, which leaves two separate solids rather than one.",
    )
    if not has_solid(result):
        raise GeometryError(
            f"{MIRROR} produced no solid. Check that the body is on one side of the "
            "mirror plane and actually touches it."
        )

    feature = document.add_feature(feature_name(arguments, "mirror"), MIRROR)
    modified, generated = evolution_of(maker, source)
    document.set_result(
        feature,
        result,
        # The mirrored half is this feature's own material; the original half stays with
        # whatever built it. That is the same rule every other operation follows.
        contributed=contribution_of(maker, reflected),
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=source, modified=modified, generated=generated
    )
    return context.result_for(feature)


def scale(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Scale the body — uniformly about a point, or along the normal of a plane.

    Shrinkage compensation on a mould is the reason this exists: the cavity is cut a
    percentage larger than the finished part, and the percentage is a material property
    rather than a modelling choice.
    """
    document = context.require_document()
    source = _body_argument(context, arguments, SCALE)

    factor = arguments.get("factor")
    if not isinstance(factor, (int, float)) or isinstance(factor, bool) or factor <= 0.0:
        raise GeometryError(
            f"{SCALE} needs factor as a positive ratio, where 1.0 leaves the size "
            f"unchanged; got {factor!r}."
        )

    reference = str(arguments.get("reference") or "")
    if document.has_point(reference):
        transformation = symbol("gp_Trsf")()
        transformation.SetScale(
            symbol("gp_Pnt")(*document.point(reference).position), float(factor)
        )
        return _apply(
            context, document, arguments, SCALE, source, transformation,
            detail="A uniform scale cannot fail on valid geometry.",
        )

    return _scale_along_plane_normal(
        context, document, arguments, source, reference, float(factor)
    )


def _scale_along_plane_normal(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    source: Any,
    reference: str,
    factor: float,
) -> Mapping[str, Any]:
    """CATIA's plane-referenced scale: stretched along the normal, unchanged across it.

    Built as `gp_GTrsf` rather than `gp_Trsf` because it is not a similarity — the two
    in-plane directions keep their size while the third does not, so no rigid-plus-scale
    transform can express it. `BRepBuilderAPI_GTransform` is the matching builder.
    """
    frame = elements.plane_frame(document, reference, tool=SCALE)
    normal = frame.Direction()
    axis = (normal.X(), normal.Y(), normal.Z())

    # Scaling by `f` along a unit direction n, leaving the plane through `origin` fixed:
    # M = I + (f - 1)·n·nᵀ, with the translation that pins the plane's own points.
    matrix = symbol("gp_Mat")()
    for row in range(3):
        for column in range(3):
            same = 1.0 if row == column else 0.0
            matrix.SetValue(row + 1, column + 1, same + (factor - 1.0) * axis[row] * axis[column])

    origin = frame.Location()
    anchor = (origin.X(), origin.Y(), origin.Z())
    along = sum(anchor[i] * axis[i] for i in range(3))
    shift = [(1.0 - factor) * along * axis[i] for i in range(3)]

    general = symbol("gp_GTrsf")()
    general.SetVectorialPart(matrix)
    general.SetTranslationPart(symbol("gp_XYZ")(*shift))

    maker = symbol("BRepBuilderAPI_GTransform")(source, general, COPY_ON_TRANSFORM)
    result = build_or_raise(
        maker,
        tool=f"{SCALE} about {reference}",
        detail="Scaling along a plane normal rebuilds every surface, and an analytic "
        "one that cannot be stretched (a sphere, a full cylinder) is where this fails.",
    )

    feature = document.add_feature(feature_name(arguments, "scale"), SCALE)
    document.set_result(feature, result, contributed=(faces(result), edges(result)))
    record_derived(
        feature.labels, result=result, source=source, modified=[], generated=[]
    )
    return context.result_for(feature)


# -- shared -------------------------------------------------------------------


def _apply(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    source: Any,
    transformation: Any,
    *,
    detail: str,
) -> Mapping[str, Any]:
    """Run one `gp_Trsf` over the body and record it as a feature.

    Every transform here **replaces** the part rather than adding to it, so the whole
    result is the feature's contribution — there is no earlier material left beside it.
    """
    maker = symbol("BRepBuilderAPI_Transform")(source, transformation, COPY_ON_TRANSFORM)
    result = build_or_raise(maker, tool=tool, detail=detail)

    feature = document.add_feature(
        feature_name(arguments, tool.removeprefix("catia_")), tool
    )
    modified, generated = evolution_of(maker, source)
    document.set_result(feature, result, contributed=(faces(result), edges(result)))
    record_derived(
        feature.labels, result=result, source=source, modified=modified, generated=generated
    )
    return context.result_for(feature)


def _body_argument(context: BuildContext, arguments: Mapping[str, Any], tool: str) -> Any:
    """The named body, or the whole part when none is named."""
    named = arguments.get("body")
    if not named:
        return context.require_shape(tool)
    return context.require_document().body(str(named))


def _mirror_axes(document: Any, reference: Any, *, tool: str) -> Any:
    """A plane reference as the `gp_Ax2` OCCT mirrors about.

    `gp_Trsf.SetMirror` reflects about the *plane* of a `gp_Ax2`, about the *line* of a
    `gp_Ax1`, and about a *point* for a `gp_Pnt` — three different transformations from
    one method name. This builds the plane one explicitly so no call site can pick the
    wrong overload by handing over the wrong type.
    """
    frame = elements.plane_frame(document, reference, tool=tool)
    return symbol("gp_Ax2")(frame.Location(), frame.Direction(), frame.XDirection())


__all__ = [
    "COPY_ON_TRANSFORM",
    "MIRROR",
    "ROTATE",
    "SCALE",
    "SYMMETRY",
    "TRANSLATE",
    "mirror",
    "rotate",
    "scale",
    "symmetry",
    "translate",
]
