"""Features swept along a path: `catia_rib` and `catia_slot`.

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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.binding import symbol
from app.kernel.occt.operations.context import BuildContext
from app.kernel.occt.operations.features import combine_into_part
from app.kernel.occt.sketching import Sketch

RIB = "catia_rib"
SLOT = "catia_slot"

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
    "rib",
    "slot",
]
