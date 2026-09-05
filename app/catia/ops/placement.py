"""Polar placement: saying *where* a feature goes in the terms a drawing uses.

**This module exists because a bolt circle could not be expressed at all.**
Every sketch primitive took a Cartesian `at: [u, v]` and nothing else, so
"four holes on a 70 mm bolt circle, one in each corner direction" had to be
turned into (24.749, 24.749) by whoever was calling — and the caller is a
language model that the system prompt explicitly forbids from doing coordinate
arithmetic, because coordinate arithmetic belongs inside a tool where it can be
tested. Measured on this seat on 2026-09-05, asked for exactly that part: the
model wrote (35, 35), which is a bolt circle of 99 mm rather than 70. Every call
returned `ok`, the part built, and it was wrong. The vocabulary, not the model,
was the defect.

So the two ways an engineer states a position are both sayable now:
`at` for a Cartesian offset, `at_radius_mm` + `at_angle_deg` for a radial one.

**Server-consumed, deliberately.** The conversion happens once, here, before the
call reaches either backend — so the CATIA daemon on the workstation never sees a
polar argument and needs no change, and OCCT and CATIA cannot drift apart on the
angle convention. Two implementations of one piece of trigonometry is how a part
ends up mirrored with every test green.

**The angle runs anticlockwise from the sketch's horizontal axis**, which is
`catia_sketch_arc`'s existing convention (`start_angle_deg` / `end_angle_deg`).
Two conventions for one question inside one sketcher would be worse than having
no polar placement at all.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from app.catia.ops import limits
from app.catia.ops.spec import Param, optional_for_server

#: The Cartesian key the two polar ones resolve into.
CARTESIAN_KEY: Final = "at"

#: The keys the server consumes. Named once so a caller can test for them
#: without repeating the strings.
POLAR_KEYS: Final[tuple[str, str]] = ("at_radius_mm", "at_angle_deg")


class PlacementError(ValueError):
    """A position that cannot be resolved, with the reason in the message.

    A `ValueError` rather than a kernel or dispatch error because both layers
    consume this and each has its own type to translate into; raising one
    layer's exception from a module both import would make the other catch
    something it has no business knowing about.
    """


def polar_placement() -> tuple[Param, Param]:
    """The two optional parameters that let a primitive be placed radially.

    Returned as a pair so a tool declares them with one `*polar_placement()`
    and cannot end up offering the radius without the angle.
    """
    return (
        optional_for_server(
            "at_radius_mm",
            {
                "type": "number",
                "minimum": 0,
                "maximum": limits.MAX_COORD_MM,
                "description": (
                    "Distance from the sketch origin, for a feature placed radially "
                    "rather than by coordinates. Millimetres. Use this with "
                    "at_angle_deg instead of working out `at` yourself: on a 70 mm "
                    "bolt circle the radius is 35, not the coordinate."
                ),
            },
        ),
        optional_for_server(
            "at_angle_deg",
            {
                "type": "number",
                "minimum": -limits.MAX_ANGLE_DEG,
                "maximum": limits.MAX_ANGLE_DEG,
                "description": (
                    "Direction from the sketch origin, measured anticlockwise from "
                    "the sketch's horizontal axis. Degrees. Requires at_radius_mm; "
                    "defaults to 0 when only the radius is given."
                ),
            },
        ),
    )


def declares_polar(schema: Mapping[str, Any]) -> bool:
    """Whether an operation's model-facing schema offers polar placement.

    Asked of the schema rather than kept as a list of tool names, so adding
    `*polar_placement()` to a new operation is the whole of the change and
    there is no second place to forget.
    """
    properties = schema.get("properties")
    return isinstance(properties, Mapping) and POLAR_KEYS[0] in properties


def resolve_polar(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Turn polar placement into the Cartesian `at` both backends already take.

    Returns the arguments unchanged when no polar key is present, so this is
    safe to run over every call. The polar keys are always removed from the
    result: they are consumed here and the daemon's schema does not accept them.
    """
    radius = arguments.get(POLAR_KEYS[0])
    angle = arguments.get(POLAR_KEYS[1])
    if radius is None and angle is None:
        return dict(arguments)

    if arguments.get(CARTESIAN_KEY) is not None:
        raise PlacementError(
            "Give `at` or `at_radius_mm`/`at_angle_deg`, not both — they are two "
            "answers that can disagree about where the feature goes. Keep the one "
            "the request states: a bolt circle is a radius and an angle, an offset "
            "from the origin is a coordinate."
        )
    if radius is None:
        raise PlacementError(
            "at_angle_deg was given without at_radius_mm, so there is no distance "
            "to place the feature at. An angle alone names a direction, not a point."
        )

    distance = _number(radius, POLAR_KEYS[0], "a distance in millimetres")
    if distance < 0.0:
        raise PlacementError(
            f"at_radius_mm is a distance from the sketch origin and cannot be "
            f"negative; got {distance}. Point it the other way with at_angle_deg "
            "instead — 180 degrees from where it is now."
        )
    bearing = math.radians(_number(angle if angle is not None else 0.0, POLAR_KEYS[1], "an angle in degrees"))

    resolved = {key: value for key, value in arguments.items() if key not in POLAR_KEYS}
    resolved[CARTESIAN_KEY] = [distance * math.cos(bearing), distance * math.sin(bearing)]
    return resolved


def _number(value: Any, argument: str, expected: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as bad:
        raise PlacementError(f"{argument} must be {expected}; got {value!r}.") from bad
    if not math.isfinite(number):
        raise PlacementError(f"{argument} must be {expected}; got {value!r}.")
    return number


__all__ = [
    "CARTESIAN_KEY",
    "POLAR_KEYS",
    "PlacementError",
    "declares_polar",
    "polar_placement",
    "resolve_polar",
]
