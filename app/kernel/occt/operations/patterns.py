"""Repeating a feature — rectangular grids, bolt circles, and arbitrary point sets.

Master plan 2.5. Patterns are the largest single multiplier in the vocabulary: a bolt
circle, a row of cooling slots, a grid of mounting holes are each *one* authored feature
and twenty pieces of geometry, and without them a design has to spell out all twenty and
keep them consistent by hand through every later edit.

**A pattern repeats the feature's own material, not the whole part.** That distinction is
the entire design of this module. A feature's `shape` is the part *as it stood after* that
feature — so copying that and fusing the copies would, on a slab with one boss, produce
twenty overlapping slabs. What must be repeated is the material the feature added, which
is recoverable exactly: it is the difference between the part after the feature and the
part before it. `_material_of` computes that, and the two "before" cases are:

* the feature is not the first — subtract the previous generation, an exact boolean;
* the feature *is* the first — it built the part from nothing, so its material is all
  of it.

**Removal patterns are patterned as removals.** A pocket's material is the *void* it cut,
which the same subtraction recovers with the operands the other way round. So a patterned
pocket drills twenty holes rather than adding twenty plugs, and neither case needs the
caller to say which it was — the sign falls out of which generation is larger.

**Instance zero is the original and is never re-applied.** Fusing a copy exactly onto the
existing material is a boolean whose result is correct and whose cost is not, and on a
100-instance pattern it is 1% of the work for 0% of the geometry.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt import elements
from app.kernel.occt.binding import symbol
from app.kernel.occt.naming import contribution_of, evolution_of, record_derived
from app.kernel.occt.operations.context import BuildContext, build_or_raise, feature_name
from app.kernel.occt.topology import has_solid

PATTERN_RECTANGULAR = "catia_pattern_rectangular"
PATTERN_CIRCULAR = "catia_pattern_circular"
PATTERN_USER = "catia_pattern_user"

#: Volume below which a patterned difference is treated as empty. A boolean between two
#: generations of the same part leaves slivers along shared faces; they are numerical
#: noise, not material, and fusing them back produces invalid geometry.
EMPTY_VOLUME_MM3 = 1e-6

#: How many instances one pattern may create. Matches the registry's own cap, restated
#: here because the kernel is also driven directly by tests and by the compiler, neither
#: of which goes through the schema.
MAXIMUM_INSTANCES = 100


def pattern_rectangular(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Repeat a feature in a grid along the two in-plane axes of a named plane."""
    document = context.require_document()

    count = _as_count(arguments.get("count"), argument="count", tool=PATTERN_RECTANGULAR)
    spacing = _as_spacing(arguments.get("spacing_mm"), "spacing_mm", PATTERN_RECTANGULAR)
    second_count = _as_count(
        arguments.get("second_count") or 1,
        argument="second_count",
        tool=PATTERN_RECTANGULAR,
        minimum=1,
    )
    second_spacing = (
        _as_spacing(arguments.get("second_spacing_mm"), "second_spacing_mm", PATTERN_RECTANGULAR)
        if second_count > 1
        else 0.0
    )

    frame = elements.plane_frame(
        document, arguments.get("plane") or "XY", tool=PATTERN_RECTANGULAR
    )
    first_axis = _tuple(frame.XDirection())
    second_axis = _tuple(frame.YDirection())
    sign = -1.0 if arguments.get("reversed") else 1.0

    placements = []
    for column in range(count):
        for row in range(second_count):
            if column == 0 and row == 0:
                continue  # instance zero is the original
            offset = [
                sign
                * (
                    column * spacing * first_axis[i]
                    + row * second_spacing * second_axis[i]
                )
                for i in range(3)
            ]
            placements.append(_translation((offset[0], offset[1], offset[2])))

    return _apply_pattern(
        context, document, arguments, PATTERN_RECTANGULAR, placements,
        described=f"{count}×{second_count} grid",
    )


def pattern_circular(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Repeat a feature evenly around an axis — a bolt circle.

    **The last instance lands on the first for a full turn, and does not for a partial
    one.** Twelve instances over 360° sit 30° apart; twelve over 90° sit 90/11 apart,
    because both ends are occupied. Dividing by `count` in both cases is the classic
    off-by-one here and puts a partial pattern's last hole where nobody asked for it.
    """
    document = context.require_document()

    count = _as_count(arguments.get("count"), argument="count", tool=PATTERN_CIRCULAR)
    total_angle = float(arguments.get("total_angle_deg") or 360.0)
    if not 0.0 < total_angle <= 360.0:
        raise GeometryError(
            f"{PATTERN_CIRCULAR} spreads instances over an angle between 0 and 360 "
            f"degrees; got {total_angle}."
        )

    full_turn = abs(total_angle - 360.0) < 1e-9
    step = total_angle / (count if full_turn else count - 1)

    if arguments.get("axis"):
        axis = elements.axis_for(document, arguments["axis"], tool=PATTERN_CIRCULAR)
    else:
        frame = elements.plane_frame(
            document, arguments.get("plane") or "XY", tool=PATTERN_CIRCULAR
        )
        axis = symbol("gp_Ax1")(frame.Location(), frame.Direction())

    placements = []
    for instance in range(1, count):
        transformation = symbol("gp_Trsf")()
        transformation.SetRotation(axis, math.radians(step * instance))
        placements.append(transformation)

    spread = "a full turn" if full_turn else f"{total_angle}°"
    return _apply_pattern(
        context, document, arguments, PATTERN_CIRCULAR, placements,
        described=f"{count} instances over {spread}",
    )


def pattern_user(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Repeat a feature at every point of a sketch — the escape hatch.

    The original sits at `anchor`, or at the sketch's own origin when none is named, and
    every point is an offset from there. Treating each point as an absolute position
    instead would move the original as a side effect of adding the pattern.
    """
    document = context.require_document()

    named = arguments.get("positions")
    if not named:
        raise GeometryError(
            f"{PATTERN_USER} needs positions — the name of a sketch whose points give "
            "the instance locations."
        )
    sketch = document.sketch(str(named))
    points = sketch.world_points()
    if not points:
        raise GeometryError(
            f"The sketch {named!r} holds no points, so {PATTERN_USER} has nowhere to "
            "put an instance. Draw the positions with catia_sketch_point first."
        )
    if len(points) > MAXIMUM_INSTANCES:
        raise GeometryError(
            f"{PATTERN_USER} would create {len(points)} instances, over the limit of "
            f"{MAXIMUM_INSTANCES}. Split the positions across two patterns."
        )

    anchor = points[0]
    if arguments.get("anchor"):
        anchor = _named_point(document, str(arguments["anchor"]), points)

    placements = [
        _translation(tuple(point[i] - anchor[i] for i in range(3)))
        for point in points
        if any(abs(point[i] - anchor[i]) > 0.0 for i in range(3))
    ]
    return _apply_pattern(
        context, document, arguments, PATTERN_USER, placements,
        described=f"{len(placements) + 1} instances from {named}",
    )


# -- shared -------------------------------------------------------------------


def _apply_pattern(
    context: BuildContext,
    document: Any,
    arguments: Mapping[str, Any],
    tool: str,
    placements: list[Any],
    *,
    described: str,
) -> Mapping[str, Any]:
    """Copy one feature's material to every placement and combine it back in."""
    part = context.require_shape(tool)
    source = _feature_argument(document, arguments, tool)
    material, adds_material = _material_of(document, source, tool)

    if not placements:
        raise GeometryError(
            f"{tool} was asked for a pattern with no instances beyond the original, "
            "which would change nothing. Increase count."
        )
    if len(placements) + 1 > MAXIMUM_INSTANCES:
        raise GeometryError(
            f"{tool} would create {len(placements) + 1} instances, over the limit of "
            f"{MAXIMUM_INSTANCES}."
        )

    result = part
    combine = "BRepAlgoAPI_Fuse" if adds_material else "BRepAlgoAPI_Cut"
    maker = None
    for placement in placements:
        copy = build_or_raise(
            symbol("BRepBuilderAPI_Transform")(material, placement, True),
            tool=tool,
            detail="An instance could not be placed; the transformation is degenerate.",
        )
        maker = symbol(combine)(result, copy)
        result = build_or_raise(
            maker,
            tool=f"{tool} ({described})",
            detail="An instance could not be combined with the part. It may fall "
            "entirely outside the material, or exactly on another instance.",
        )

    if not has_solid(result):
        raise GeometryError(
            f"{tool} ({described}) left no solid. A pattern of cuts this dense removes "
            "everything the part was made of — reduce the count or the spacing."
        )

    feature = document.add_feature(feature_name(arguments, "pattern"), tool)
    modified, generated = evolution_of(maker, part)
    document.set_result(
        feature,
        result,
        contributed=contribution_of(maker, result) if maker is not None else None,
        evolved_by=maker,
    )
    record_derived(
        feature.labels, result=result, source=part, modified=modified, generated=generated
    )
    payload = dict(context.result_for(feature))
    payload["instances"] = len(placements) + 1
    payload["patterned"] = source.name
    return payload


def _material_of(document: Any, feature: Any, tool: str) -> tuple[Any, bool]:
    """The material one feature added, and whether it added rather than removed.

    Recovered as the difference between the part after the feature and the part before
    it — exact, and independent of which operation built it, which is what lets one
    pattern implementation repeat a pad, a pocket, a shaft or a boolean without knowing
    which it got. See the module docstring.
    """
    after = feature.shape
    if after is None:
        raise GeometryError(
            f"{feature.name!r} produced no geometry, so {tool} has nothing to repeat."
        )

    before = _generation_before(document, feature)
    if before is None:
        # The first solid feature *is* the part; everything it has is its own material.
        return after, True

    added = _difference(after, before)
    if added is not None:
        return added, True

    removed = _difference(before, after)
    if removed is not None:
        return removed, False

    raise GeometryError(
        f"{feature.name!r} did not change the part's material, so {tool} has nothing to "
        "repeat. A pattern repeats what a feature added or removed; a feature that only "
        "moved or renamed something has neither."
    )


def _generation_before(document: Any, feature: Any) -> Any:
    """The part as it stood *before* this feature, or None if it was the first."""
    previous = None
    for candidate in document:
        if candidate.name == feature.name:
            return previous
        if candidate.shape is not None:
            previous = candidate.shape
    raise GeometryError(
        f"{feature.name!r} is not a feature of this part, so it cannot be patterned."
    )


def _difference(first: Any, second: Any) -> Any:
    """`first` minus `second`, or None when the result holds no real material."""
    cut = symbol("BRepAlgoAPI_Cut")(first, second)
    if not cut.IsDone():
        return None
    result = cut.Shape()
    if not has_solid(result):
        return None

    properties = symbol("GProp_GProps")()
    symbol("BRepGProp").VolumeProperties_s(result, properties)
    return result if abs(float(properties.Mass())) > EMPTY_VOLUME_MM3 else None


def _feature_argument(document: Any, arguments: Mapping[str, Any], tool: str) -> Any:
    """The named feature, or the most recent one that produced geometry."""
    named = arguments.get("feature")
    if named:
        return document.feature(str(named))

    for candidate in reversed(list(document)):
        if candidate.shape is not None:
            return candidate
    raise GeometryError(
        f"{tool} has no feature to repeat — nothing has been built in {document.name} yet."
    )


def _named_point(
    document: Any, name: str, candidates: list[tuple[float, float, float]]
) -> tuple[float, float, float]:
    """A reference point by name, refused unless it is one of the pattern's positions."""
    position = document.point(name).position
    if not any(
        all(abs(position[i] - point[i]) <= 1e-9 for i in range(3)) for point in candidates
    ):
        raise GeometryError(
            f"{name!r} is not one of the positions in the pattern sketch, so it cannot "
            "be the anchor. The anchor names which position the original already sits "
            "on; every other position gets a copy."
        )
    return position


def _translation(offset: tuple[float, float, float]) -> Any:
    transformation = symbol("gp_Trsf")()
    transformation.SetTranslation(symbol("gp_Vec")(*offset))
    return transformation


def _tuple(direction: Any) -> tuple[float, float, float]:
    return (direction.X(), direction.Y(), direction.Z())


def _as_count(value: Any, *, argument: str, tool: str, minimum: int = 2) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise GeometryError(
            f"{tool} needs {argument} as a whole number of at least {minimum}; got "
            f"{value!r}."
        )
    return value


def _as_spacing(value: Any, argument: str, tool: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0.0:
        raise GeometryError(
            f"{tool} needs {argument} as a positive distance in millimetres; got "
            f"{value!r}."
        )
    return float(value)


__all__ = [
    "EMPTY_VOLUME_MM3",
    "MAXIMUM_INSTANCES",
    "PATTERN_CIRCULAR",
    "PATTERN_RECTANGULAR",
    "PATTERN_USER",
    "pattern_circular",
    "pattern_rectangular",
    "pattern_user",
]
