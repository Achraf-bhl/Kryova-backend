"""Features swept along a path: `catia_rib`, `catia_slot` and `catia_stiffener`.

A rib takes a closed profile and drags it along a guide curve, leaving the solid it
traced; a slot does the same and removes it. This is how a handle, a pipe run, an O-ring
groove or a cable channel is modelled — every shape whose cross-section is constant and
whose *path* is the interesting part.

**The two are one operation with opposite signs**, exactly as pad and pocket are, and for
the same reason: a fix to how the sweep is oriented must not have to be found twice.

**The profile is swept from where it was drawn**, with no contact translation and no
correction rotation. OCCT will happily slide a profile onto the spine and turn it normal
to the path, and doing so would make a rib whose section sits somewhere the author did
not draw it — plausible-looking, and a different part. The registry's own summary says
the profile belongs on a plane normal to the start of the guide, and this honours that
instruction rather than repairing it silently.

A stiffener is the third member of the family and the odd one: its profile is **open**,
and where it stops is not stated at all — it runs until it meets the material it braces.
That is what makes it survive a wall moving, and it is why the operation is built by
subtraction rather than by extrusion to a length nobody gave.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.operations.context import (
    BuildContext,
    as_positive_length,
    build_or_raise,
)
from app.kernel.occt.operations.features import combine_into_part
from app.kernel.occt.sketching import Sketch
from app.kernel.occt.topology import SOLID, compound, edges, explore

RIB = "catia_rib"
SLOT = "catia_slot"
STIFFENER = "catia_stiffener"

#: How the profile is held as it travels, and what each means to OCCT.
#:
#: `keep_angle` is the Frenet frame: the section keeps its angle to the path, so a
#: rectangular rib stays square to the curve all the way round a bend. `pulling_direction`
#: pins one axis of the section to a fixed direction instead, which is what a moulded rib
#: needs — it must stay parallel to the draw or it cannot come out of the tool.
KEEP_ANGLE: Final = "keep_angle"
PULLING_DIRECTION: Final = "pulling_direction"
REFERENCE_SURFACE: Final = "reference_surface"

#: Named directions a `pulling_direction` reference may use, beyond a raw vector.
_NAMED_DIRECTIONS: Final[dict[str, tuple[float, float, float]]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}


def rib(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sweep a closed profile along a guide curve, adding the material it traces."""
    return _swept_feature(context, arguments, RIB, adds_material=True)


def slot(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sweep a closed profile along a guide curve and remove what it traces."""
    return _swept_feature(context, arguments, SLOT, adds_material=False)


def stiffener(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Thicken an open profile into a rib that runs until it meets the material.

    The gusset between a wall and a base: draw the diagonal across the corner and this
    fills the triangle behind it. **The profile is open and its ends are not where the
    stiffener ends** — the walls decide that, which is the whole point of the feature and
    why it stays correct when they move. A closed profile is a pad or a rib and is
    refused as one.

    **Built by subtraction, never by extrusion to a length.** The thickened profile is
    grown well past the part, the part is cut out of it, and what is left is exactly the
    voids the profile spans. Extruding "far enough" and trimming would need a length
    nobody gave, and any length picked here would be this module's guess rather than the
    part's answer.

    **Which way it grows is stated, not detected.** The sweep runs to the left of the
    direction the profile was drawn — the sketch normal crossed into the chord from its
    start to its end — and `reversed` sends it the other way. Sniffing for the material
    was the alternative and is worse: the corner a stiffener fills is *empty*, so every
    cheap test (is the midpoint inside the solid, where is the centroid) answers about
    somewhere the stiffener is not. What replaces the sniffing is a check with a real
    answer: a stiffener that runs off the end of its own sweep never met material, and
    that is refused by name with `reversed` offered as the fix.
    """
    document = context.require_document()
    part = document.shape
    if part is None:
        raise GeometryError(
            f"{STIFFENER} braces material that is already there, and this part has none "
            "yet. Build the walls it stiffens with a pad or a shaft first."
        )

    sketch = _sketch_named(document, arguments, "profile", STIFFENER)
    profile, chord = _open_profile(sketch)
    thickness_mm = as_positive_length(
        arguments.get("thickness_mm"), argument="thickness_mm", tool=STIFFENER
    )

    normal = _sketch_normal(sketch)
    sweep = _sweep_direction(normal, chord, reversed_=bool(arguments.get("reversed")))
    reach = _reach_over(part, profile)

    # `symmetric` defaults to true, which is CATIA's neutral fibre: the drawn line is the
    # middle of the plate. `false` puts the whole thickness on the sketch normal's side.
    offset = (
        _scaled(normal, -thickness_mm / 2.0)
        if arguments.get("symmetric") is not False
        else (0.0, 0.0, 0.0)
    )
    base = _translated(profile, offset) if any(offset) else profile

    band = _band(base, normal, sweep, thickness_mm, reach)
    cap = _prism(_translated(base, _scaled(sweep, reach)), _scaled(normal, thickness_mm))
    blank = _closed_by_the_part(band, part, profile, cap, reach)
    return combine_into_part(
        context, document, arguments, STIFFENER, blank, adds_material=True
    )


# -- the stiffener's own construction -----------------------------------------

#: How far past the part the plate is grown before the part is subtracted from it.
#:
#: A multiple of the bounding diagonal rather than a fixed length, and it does not need
#: tuning: the sweep only has to clear the material for the subtraction to leave the
#: voids, and everything beyond that is cut away again. Too *short* is the only error
#: that matters, and it is reported rather than absorbed — a piece that reaches the far
#: end of the sweep is refused.
_REACH_FACTOR: Final = 1.5

#: How close a piece must come to the profile to count as reached by it, and to the far
#: cap to count as having run off the end. Both are contact tests against surfaces the
#: same boolean produced, so this is a numerical-noise margin, not a modelling choice.
_CONTACT_TOLERANCE_MM: Final = 1e-6


def _open_profile(sketch: Sketch) -> tuple[Any, tuple[float, float, float]]:
    """The sketch's single open run, and the chord from its start to its end."""
    if sketch.profiles:
        raise GeometryError(
            f"{STIFFENER} needs an open profile — a line, or a chain of them — and "
            f"sketch {sketch.name!r} holds {len(sketch.profiles)} closed one(s). A "
            "closed profile states its own boundary, which is what catia_pad and "
            "catia_rib take; a stiffener finds its boundary against the part."
        )
    if len(sketch.curves) != 1:
        drawn = len(sketch.curves)
        raise GeometryError(
            f"{STIFFENER} needs exactly one open profile on sketch {sketch.name!r}, and "
            f"it holds {drawn}. "
            + (
                "Draw the stiffener's line with catia_sketch_line or "
                "catia_sketch_polyline."
                if drawn == 0
                else "Draw each stiffener on a sketch of its own so there is no doubt "
                "which line is which."
            )
        )

    run = sketch.curves[0]
    start, end = sketch.to_world(run.start), sketch.to_world(run.end)
    chord = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
    if _length(chord) < _CONTACT_TOLERANCE_MM:
        raise GeometryError(
            f"{STIFFENER} takes the direction it grows from the chord between the "
            f"profile's ends, and the profile on sketch {sketch.name!r} begins and ends "
            "at the same place. Draw it as an open line across the corner."
        )
    return run.wire, chord


def _sketch_normal(sketch: Sketch) -> tuple[float, float, float]:
    direction = sketch.frame().Direction()
    return (direction.X(), direction.Y(), direction.Z())


def _sweep_direction(
    normal: tuple[float, float, float],
    chord: tuple[float, float, float],
    *,
    reversed_: bool,
) -> tuple[float, float, float]:
    """In the sketch plane, square to the profile's chord — left of it, unless reversed."""
    across = (
        normal[1] * chord[2] - normal[2] * chord[1],
        normal[2] * chord[0] - normal[0] * chord[2],
        normal[0] * chord[1] - normal[1] * chord[0],
    )
    length = _length(across)
    if length < _CONTACT_TOLERANCE_MM:  # pragma: no cover - a sketch chord is in-plane
        raise GeometryError(
            f"{STIFFENER} could not square the profile's chord against its sketch plane. "
            "The profile appears to run along the plane's own normal."
        )
    unit = _scaled(across, 1.0 / length)
    return _scaled(unit, -1.0) if reversed_ else unit


def _band(
    base: Any,
    normal: tuple[float, float, float],
    sweep: tuple[float, float, float],
    thickness_mm: float,
    reach: float,
) -> Any:
    """The profile grown across the part, one solid per drawn segment, fused.

    Per segment rather than sweeping the wire whole: a prism of an *edge* is a face and a
    prism of a face is a solid, both of which OCCT states plainly, where a prism of a
    multi-edge wire is a shell whose promotion to a solid is not.
    """
    slabs = [
        _prism(_prism(edge, _scaled(sweep, reach)), _scaled(normal, thickness_mm))
        for edge in edges(base)
    ]
    band = slabs[0]
    for extra in slabs[1:]:
        maker = symbol("BRepAlgoAPI_Fuse")(band, extra)
        band = build_or_raise(
            maker,
            tool=f"{STIFFENER} (joining the segments of its profile)",
            detail="Two segments of the profile could not be joined into one plate. "
            "Check that the profile does not double back on itself.",
        )
    return band


def _closed_by_the_part(band: Any, part: Any, profile: Any, cap: Any, reach: float) -> Any:
    """What is left of the plate once the part is taken out of it.

    The two rules that make this the stiffener rather than a slab: a piece counts only if
    the profile actually reaches it, and a piece that also reaches the far end of the
    sweep is not closed by the part at all — the plate ran past the material instead of
    into it, which is the wrong-way-round case and is refused rather than built.
    """
    maker = symbol("BRepAlgoAPI_Cut")(band, part)
    remainder = build_or_raise(
        maker,
        tool=f"{STIFFENER} (cutting the part out of its plate)",
        detail="The stiffener's plate could not be subtracted from the part.",
    )

    pieces = [symbol("TopoDS").Solid_s(shape) for shape in explore(remainder, SOLID)]
    reached = [
        piece for piece in pieces if _distance(piece, profile) <= _CONTACT_TOLERANCE_MM
    ]
    if not reached:
        raise GeometryError(
            f"{STIFFENER} found no void along its profile. The profile lies inside the "
            "material it was meant to brace, so there is nothing for the stiffener to "
            "fill — draw it across the corner rather than through the wall."
        )

    overrun = [
        piece for piece in reached if _distance(piece, cap) <= _CONTACT_TOLERANCE_MM
    ]
    if overrun:
        raise GeometryError(
            f"{STIFFENER} swept {reach:.4g} mm from the profile and never met material: "
            "what it built runs off the end of its own sweep rather than being closed by "
            "the part. The sweep runs to the left of the direction the profile was "
            "drawn — pass reversed: true to send it the other way, or check that the "
            "profile spans the corner it is meant to brace."
        )
    return reached[0] if len(reached) == 1 else compound(reached)


def _reach_over(part: Any, profile: Any) -> float:
    """Far enough to clear both the part and the profile, whatever their placement."""
    box = symbol("Bnd_Box")()
    add = symbol("BRepBndLib").Add_s
    add(part, box, True)
    add(profile, box, True)
    x_min, y_min, z_min, x_max, y_max, z_max = box.Get()
    diagonal = _length((x_max - x_min, y_max - y_min, z_max - z_min))
    return diagonal * _REACH_FACTOR


def _prism(shape: Any, vector: tuple[float, float, float]) -> Any:
    return symbol("BRepPrimAPI_MakePrism")(shape, symbol("gp_Vec")(*vector)).Shape()


def _translated(shape: Any, vector: tuple[float, float, float]) -> Any:
    transformation = symbol("gp_Trsf")()
    transformation.SetTranslation(symbol("gp_Vec")(*vector))
    return symbol("BRepBuilderAPI_Transform")(shape, transformation, True).Shape()


def _distance(first: Any, second: Any) -> float:
    """Exact minimum distance, and `inf` when the search has no answer to give.

    Infinity rather than zero on failure, because both callers read a small distance as
    "these touch": a search that failed would otherwise be counted as contact, which
    would keep a piece the profile never reached or refuse one that never ran over.
    """
    extrema = symbol("BRepExtrema_DistShapeShape")(first, second)
    if not extrema.IsDone() or extrema.NbSolution() <= 0:  # pragma: no cover - defensive
        return math.inf
    return float(extrema.Value())


def _scaled(vector: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (vector[0] * factor, vector[1] * factor, vector[2] * factor)


def _length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2)


# -- shared construction ------------------------------------------------------


def _swept_feature(
    context: BuildContext,
    arguments: Mapping[str, Any],
    tool: str,
    *,
    adds_material: bool,
) -> Mapping[str, Any]:
    document = context.require_document()

    if arguments.get("thick"):
        raise OperationNotSupported(
            f"{tool} with thick=true",
            "A thin-walled sweep needs the profile offset into two walls, which is "
            "surface work (Phase 2.6). Sweep the wall's own closed section instead, or "
            "sweep the solid and hollow it with catia_shell",
        )

    profile = _sketch_named(document, arguments, "profile", tool)
    spine = _sketch_named(document, arguments, "centre_curve", tool).path(tool=tool)

    if profile.is_empty:
        raise GeometryError(
            f"{tool} needs a closed profile to sweep, and sketch {profile.name!r} has "
            "none. A rib's section is a closed shape — draw a rectangle, a circle, or a "
            "contour that meets itself."
        )

    blank = _swept_solid(profile, spine, arguments, tool)
    return combine_into_part(
        context, document, arguments, tool, blank, adds_material=adds_material
    )


def _swept_solid(
    profile: Sketch, spine: Any, arguments: Mapping[str, Any], tool: str
) -> Any:
    """The solid the profile traces along the spine.

    A sketch's later profiles are holes in its first — the rule `Sketch.face()` already
    applies — so each is swept in turn and cut out of the first. That is what makes a
    pipe run with a bore expressible: outer circle, inner circle, one rib.
    """
    solid = _sweep_one(profile.profiles[0], spine, arguments, tool)
    for inner in profile.profiles[1:]:
        bore = _sweep_one(inner, spine, arguments, tool)
        maker = symbol("BRepAlgoAPI_Cut")(solid, bore)
        maker.Build()
        if not maker.IsDone():
            raise GeometryError(
                f"{tool} could not hollow the swept solid with the inner profile of "
                f"sketch {profile.name!r}. Check that the inner profile lies inside the "
                "outer one."
            )
        solid = maker.Shape()
    return solid


def _sweep_one(wire: Any, spine: Any, arguments: Mapping[str, Any], tool: str) -> Any:
    """One closed wire, dragged along the spine and capped into a solid."""
    maker = symbol("BRepOffsetAPI_MakePipeShell")(spine)
    _apply_control(maker, arguments, tool)

    try:
        maker.Add(wire, False, False)
        maker.Build()
        done = maker.IsDone()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"{tool} could not sweep the profile along the guide: {exc}. The guide may "
            "double back on itself, or turn more tightly than the profile is wide."
        ) from exc

    if not done or not maker.MakeSolid():
        raise GeometryError(
            f"{tool} swept the profile but could not close it into a solid. The profile "
            "must be a closed section, and the guide must not cross itself."
        )
    return maker.Shape()


def _apply_control(maker: Any, arguments: Mapping[str, Any], tool: str) -> None:
    """How the section is held as it travels."""
    control = str(arguments.get("control") or KEEP_ANGLE)

    if control == KEEP_ANGLE:
        maker.SetMode(True)
        return

    if control == PULLING_DIRECTION:
        maker.SetMode(symbol("gp_Dir")(*_pulling_direction(arguments, tool)))
        return

    if control == REFERENCE_SURFACE:
        raise OperationNotSupported(
            f"{tool} with control='{REFERENCE_SURFACE}'",
            "Orienting the section against a surface needs constructed surfaces "
            f"(Phase 2.6). '{KEEP_ANGLE}' and '{PULLING_DIRECTION}' are available",
        )

    raise GeometryError(
        f"{tool} does not know the control mode {control!r}. Use "
        f"'{KEEP_ANGLE}', '{PULLING_DIRECTION}' or '{REFERENCE_SURFACE}'."
    )


def _pulling_direction(arguments: Mapping[str, Any], tool: str) -> tuple[float, float, float]:
    """The fixed direction the section is pinned to.

    Refused rather than defaulted when absent: `pulling_direction` with no direction is
    a mode that has not been given its one argument, and picking +Z for it would build a
    rib oriented by this module's convenience rather than by the mould's draw.
    """
    reference = arguments.get("reference")
    if reference is None:
        raise GeometryError(
            f"{tool} was asked to hold the section to a pulling direction and no "
            "`reference` says which. Give a direction as [x, y, z], or the name of an "
            f"axis: {', '.join(_NAMED_DIRECTIONS)}."
        )

    if isinstance(reference, str):
        named = _NAMED_DIRECTIONS.get(reference.upper())
        if named is None:
            raise GeometryError(
                f"{tool} does not recognise {reference!r} as a direction. Use "
                f"{', '.join(_NAMED_DIRECTIONS)}, or give [x, y, z]."
            )
        return named

    if isinstance(reference, (list, tuple)) and len(reference) == 3:
        vector = (float(reference[0]), float(reference[1]), float(reference[2]))
        if any(vector):
            return vector
        raise GeometryError(
            f"{tool} was given a zero-length pulling direction, which points nowhere."
        )

    raise GeometryError(
        f"{tool} needs `reference` to be [x, y, z] or an axis name, got {reference!r}."
    )


def _sketch_named(document: Any, arguments: Mapping[str, Any], key: str, tool: str) -> Sketch:
    named = arguments.get(key)
    if not named:
        raise GeometryError(f"{tool} needs `{key}` and none was named.")
    sketch: Sketch = document.sketch(str(named))
    return sketch


__all__ = [
    "KEEP_ANGLE",
    "PULLING_DIRECTION",
    "REFERENCE_SURFACE",
    "RIB",
    "SLOT",
    "STIFFENER",
    "rib",
    "slot",
    "stiffener",
]
