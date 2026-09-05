"""Drilling: the five named spots, exact coordinates, and the real hole types.

Master plan 2.5. A hole is a pocket with a shape nobody should have to draw — a
counterbore is two coaxial cylinders, a countersink is a cylinder and a cone, and a
tapered hole is a cone alone. Spelling those out as sketches and pockets is possible and
is exactly the tedium the vocabulary exists to remove.

**The tool is built as a solid and cut, rather than swept.** Every hole here is a
revolution profile, so a lathe-style construction would work; a fused stack of primitives
is used instead because each piece is analytic (`BRepPrimAPI_MakeCylinder`,
`MakeCone`) and therefore exact, and because a counterbore's step is then a real planar
face rather than a seam in one revolved surface.

**Depth is measured from the face, along its inward normal.** Not from the origin, not
along −Z. A hole in the side of a bracket goes into the bracket; deriving the direction
from anything but the face being drilled is how a hole ends up in mid-air on the first
part that is not a flat plate.

**`through_all` defaults to true**, matching the registry. A hole that stops 0.1 mm short
of breaking through looks identical in a thumbnail and is a different part.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError
from app.kernel.occt import elements
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import contribution_of, evolution_of, record_derived
from app.kernel.occt.operations.context import (
    BuildContext,
    as_positive_length,
    build_or_raise,
    feature_name,
)
from app.kernel.occt.topology import has_solid

HOLE = "catia_hole"
HOLE_AT = "catia_hole_at"

#: Bounding-box face words, as (axis index, take the maximum). The vocabulary's own
#: mapping — `front` is −Y and `back` is +Y, which is the convention the rest of the
#: registry uses and is worth stating because the opposite reading is equally plausible.
_BOX_FACES: Final[dict[str, tuple[int, bool]]] = {
    "right": (0, True),
    "left": (0, False),
    "back": (1, True),
    "front": (1, False),
    "top": (2, True),
    "bottom": (2, False),
}

#: Where the five named spots sit on the face, as fractions of its extent from the
#: centre. `inset_mm` moves the four corners in from the edges; the centre ignores it.
_SPOTS: Final[dict[str, tuple[float, float]]] = {
    "center": (0.0, 0.0),
    "front_left": (-1.0, -1.0),
    "front_right": (1.0, -1.0),
    "back_left": (-1.0, 1.0),
    "back_right": (1.0, 1.0),
}

#: Default distance in from the face's edges for the four corner spots.
DEFAULT_INSET_MM: Final = 10.0

#: Included angle of a twist-drill point, which is what a blind hole's bottom looks like
#: unless it was reamed flat. The registry's own default.
DEFAULT_BOTTOM_ANGLE_DEG: Final = 118.0

#: Default included angle of a countersink — a 90° head is the metric standard.
DEFAULT_HEAD_ANGLE_DEG: Final = 90.0

#: Hole types this backend builds, in the registry's spelling.
KINDS: Final[frozenset[str]] = frozenset(
    {"simple", "tapered", "counterbored", "countersunk", "counterdrilled"}
)


def hole(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drill at one of five named spots on a bounding-box face."""
    document = context.require_document()
    part = context.require_shape(HOLE)

    face_word = str(arguments.get("face") or "").lower()
    if face_word not in _BOX_FACES:
        allowed = ", ".join(sorted(_BOX_FACES))
        raise GeometryError(
            f"{HOLE} needs face as one of the bounding-box faces: {allowed}; got "
            f"{arguments.get('face')!r}."
        )

    spot = str(arguments.get("position") or "center").lower()
    if spot not in _SPOTS:
        allowed = ", ".join(sorted(_SPOTS))
        raise GeometryError(
            f"{HOLE} needs position as one of: {allowed}; got "
            f"{arguments.get('position')!r}."
        )

    inset = float(arguments.get("inset_mm") or DEFAULT_INSET_MM)
    origin, direction = _spot_on_box_face(part, face_word, spot, inset)
    return _drill(
        context, document, arguments, HOLE, part, origin, direction,
        described=f"{spot} of the {face_word} face",
    )


def hole_at(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Drill at an exact point on a named face, with a real hole type."""
    document = context.require_document()
    part = context.require_shape(HOLE_AT)

    frame = elements.plane_frame(document, arguments.get("face"), tool=HOLE_AT)
    at = arguments.get("at")
    if not isinstance(at, (list, tuple)) or len(at) != 2:
        raise GeometryError(
            f"{HOLE_AT} needs at as [u, v] in millimetres on the face's own plane; got "
            f"{at!r}."
        )

    x_axis, y_axis = frame.XDirection(), frame.YDirection()
    anchor = frame.Location()
    origin = (
        anchor.X() + float(at[0]) * x_axis.X() + float(at[1]) * y_axis.X(),
        anchor.Y() + float(at[0]) * x_axis.Y() + float(at[1]) * y_axis.Y(),
        anchor.Z() + float(at[0]) * x_axis.Z() + float(at[1]) * y_axis.Z(),
    )
    normal = frame.Direction()
    # Into the material: a face's frame points outwards, and a hole goes the other way.
    direction = (-normal.X(), -normal.Y(), -normal.Z())

    return _drill(
        context, document, arguments, HOLE_AT, part, origin, direction,
        described=f"at {list(at)} on {arguments.get('face')}",
    )


# -- the tool solid -----------------------------------------------------------


def _drill(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    part: Any,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    *,
    described: str,
) -> Mapping[str, Any]:
    """Build the cutting solid, subtract it, and record the feature."""
    diameter = as_positive_length(
        arguments.get("diameter_mm"), argument="diameter_mm", tool=tool
    )
    through = arguments.get("through_all")
    through = True if through is None else bool(through)

    depth = (
        _through_depth(part)
        if through
        else as_positive_length(arguments.get("depth_mm"), argument="depth_mm", tool=tool)
    )

    kind = str(arguments.get("kind") or "simple").lower()
    if kind not in KINDS:
        raise GeometryError(
            f"{kind!r} is not a hole type. Use one of: {', '.join(sorted(KINDS))}."
        )

    cutter = _cutting_solid(arguments, origin, direction, diameter, depth, kind, through, tool)

    maker = symbol("BRepAlgoAPI_Cut")(part, cutter)
    result = build_or_raise(
        maker,
        tool=f"{tool} Ø{diameter} {described}",
        detail="The hole could not be cut. It may fall entirely outside the material, "
        "in which case there is nothing to drill through.",
    )
    if not has_solid(result):
        raise GeometryError(
            f"{tool} Ø{diameter} {described} removed the whole part. The hole is wider "
            "than the material around it."
        )

    feature = document.add_feature(feature_name(arguments, "hole"), tool)
    modified, generated = evolution_of(maker, part)
    document.set_result(
        feature, result, contributed=contribution_of(maker, cutter), evolved_by=maker
    )
    record_derived(
        feature.labels, result=result, source=part, modified=modified, generated=generated
    )
    payload = dict(context.result_for(feature))
    payload["hole"] = {
        "kind": kind,
        "diameter_mm": diameter,
        "depth_mm": depth if not through else None,
        "through_all": through,
        "at_mm": list(origin),
        "direction": list(direction),
    }
    return payload


def _cutting_solid(
    arguments: Mapping[str, Any],
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    diameter: float,
    depth: float,
    kind: str,
    through: bool,
    tool: str,
) -> Any:
    """The solid whose removal is the hole.

    Started a hair *outside* the face rather than exactly on it. A cutter whose end face
    is coplanar with the face being drilled is a tangential boolean, which OCCT can
    resolve into a zero-thickness sliver rather than an opening — the classic symptom
    being a hole that measures right and leaves a film across its mouth.
    """
    start = _stepped(origin, direction, -_OVERSHOOT_MM)
    reach = depth + _OVERSHOOT_MM

    axis = symbol("gp_Ax2")(symbol("gp_Pnt")(*start), symbol("gp_Dir")(*direction))
    if kind == "tapered":
        # A tapered hole is a cone alone: full diameter at the face, narrowing inwards.
        small = max(_MINIMUM_TIP_MM, diameter / 2.0 - reach * math.tan(
            math.radians(float(arguments.get("bottom_angle_deg") or DEFAULT_BOTTOM_ANGLE_DEG) / 2.0)
        ))
        return _cone(axis, diameter / 2.0, small, reach)

    shaft = symbol("BRepPrimAPI_MakeCylinder")(axis, diameter / 2.0, reach).Shape()
    if not through:
        shaft = _fuse(shaft, _drill_point(origin, direction, diameter, depth, arguments))

    head = _head(arguments, start, direction, diameter, kind, tool)
    return shaft if head is None else _fuse(shaft, head)


def _head(
    arguments: Mapping[str, Any],
    start: tuple[float, float, float],
    direction: tuple[float, float, float],
    diameter: float,
    kind: str,
    tool: str,
) -> Any:
    """The counterbore, countersink or counterdrill above the shaft, or None."""
    if kind == "simple" or kind == "tapered":
        return None

    head_diameter = float(arguments.get("head_diameter_mm") or diameter * 2.0)
    if head_diameter <= diameter:
        raise GeometryError(
            f"{tool} was given head_diameter_mm={head_diameter}, which is not wider than "
            f"the Ø{diameter} shaft. A {kind} head has to be wider than the hole it sits "
            "over."
        )

    axis = symbol("gp_Ax2")(symbol("gp_Pnt")(*start), symbol("gp_Dir")(*direction))

    if kind == "countersunk":
        # A cone from the head diameter down to the shaft — the depth follows from the
        # included angle, so a countersink cannot be specified inconsistently.
        angle = float(arguments.get("head_angle_deg") or DEFAULT_HEAD_ANGLE_DEG)
        drop = (head_diameter - diameter) / 2.0 / math.tan(math.radians(angle / 2.0))
        return _cone(axis, head_diameter / 2.0, diameter / 2.0, drop + _OVERSHOOT_MM)

    depth = float(arguments.get("head_depth_mm") or diameter)
    bore = symbol("BRepPrimAPI_MakeCylinder")(
        axis, head_diameter / 2.0, depth + _OVERSHOOT_MM
    ).Shape()
    if kind == "counterbored":
        return bore

    # Counterdrilled: a flat bore with a countersunk lead-in beneath it.
    angle = float(arguments.get("head_angle_deg") or DEFAULT_HEAD_ANGLE_DEG)
    drop = (head_diameter - diameter) / 2.0 / math.tan(math.radians(angle / 2.0))
    below = _stepped(start, direction, depth + _OVERSHOOT_MM)
    lead = _cone(
        symbol("gp_Ax2")(symbol("gp_Pnt")(*below), symbol("gp_Dir")(*direction)),
        head_diameter / 2.0,
        diameter / 2.0,
        drop,
    )
    return _fuse(bore, lead)


def _drill_point(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    diameter: float,
    depth: float,
    arguments: Mapping[str, Any],
) -> Any:
    """The conical bottom a twist drill leaves on a blind hole.

    Modelled because it is real: a flat-bottomed blind hole is a *reamed* hole and a
    different manufacturing operation. Reporting one when the design asked for a drilled
    hole would make the FEA see a stress concentration that is not there.
    """
    angle = float(arguments.get("bottom_angle_deg") or DEFAULT_BOTTOM_ANGLE_DEG)
    drop = (diameter / 2.0) / math.tan(math.radians(angle / 2.0))
    tip = _stepped(origin, direction, depth)
    axis = symbol("gp_Ax2")(symbol("gp_Pnt")(*tip), symbol("gp_Dir")(*direction))
    return _cone(axis, diameter / 2.0, _MINIMUM_TIP_MM, drop)


def _stepped(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    distance: float,
) -> tuple[float, float, float]:
    """`origin` moved `distance` along `direction`. Written out rather than built from a
    generator so the result stays a 3-tuple to a type checker, not a variadic one."""
    return (
        origin[0] + direction[0] * distance,
        origin[1] + direction[1] * distance,
        origin[2] + direction[2] * distance,
    )


def _cone(axis: Any, bottom_radius: float, top_radius: float, height: float) -> Any:
    return symbol("BRepPrimAPI_MakeCone")(axis, bottom_radius, top_radius, height).Shape()


def _fuse(first: Any, second: Any) -> Any:
    maker = symbol("BRepAlgoAPI_Fuse")(first, second)
    if not maker.IsDone():
        raise GeometryError(
            "The pieces of the drilling tool could not be joined. Check that the head "
            "diameter and depth are consistent with the shaft."
        )
    return maker.Shape()


def _through_depth(part: Any) -> float:
    """Deep enough to clear the part from any direction, computed rather than assumed."""
    from app.kernel.occt.metrology import bounding_box_mm

    size = bounding_box_mm(part)["size"]
    return 2.0 * math.sqrt(sum(component * component for component in size))


def _spot_on_box_face(
    part: Any, face_word: str, spot: str, inset: float
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """One of the five named spots on a bounding-box face, and the drilling direction."""
    from app.kernel.occt.metrology import bounding_box_mm

    box = bounding_box_mm(part)
    low, high = box["min"], box["max"]
    axis_index, take_max = _BOX_FACES[face_word]

    centre = [(low[i] + high[i]) / 2.0 for i in range(3)]
    position = list(centre)
    position[axis_index] = high[axis_index] if take_max else low[axis_index]

    # The two axes lying *in* the face, in ascending index order, so `u` is always the
    # lower-numbered one and the corner words mean the same thing on every face.
    in_plane = [i for i in range(3) if i != axis_index]
    offsets = _SPOTS[spot]
    for slot, index in enumerate(in_plane):
        half = (high[index] - low[index]) / 2.0
        reach = max(0.0, half - inset)
        position[index] = centre[index] + offsets[slot] * reach

    direction = [0.0, 0.0, 0.0]
    direction[axis_index] = -1.0 if take_max else 1.0
    return (position[0], position[1], position[2]), (
        direction[0], direction[1], direction[2],
    )


#: How far outside the face the cutter starts, so the boolean is never tangential.
_OVERSHOOT_MM: Final = 0.01

#: Cone tips are built with a hair of radius rather than a true point: OCCT accepts a
#: zero top radius, and the resulting apex vertex is a degenerate edge that later
#: booleans and meshing both handle badly.
_MINIMUM_TIP_MM: Final = 1e-4


__all__ = [
    "DEFAULT_BOTTOM_ANGLE_DEG",
    "DEFAULT_HEAD_ANGLE_DEG",
    "DEFAULT_INSET_MM",
    "HOLE",
    "HOLE_AT",
    "KINDS",
    "hole",
    "hole_at",
]
