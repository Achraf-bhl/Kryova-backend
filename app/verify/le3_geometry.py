"""NAFEMS LE3 — "Hemispherical shell with point loads": **the surface only**.

A shell benchmark, so the model is the mid-surface and the 40 mm wall is a
`ShellSection` property, never geometry. Every number here is read off
`docs/nafems-le3-geometry.md` §1.5, which cites the scanned NAFEMS figure
(`x^2 + y^2 + z^2 = 100`, `r = 10m`, `Thickness = 0.04m`), ESRD's text, a
committed Abaqus deck and Altair's page. The sourced figures are metres; this
codebase is mm-N-MPa, so each is multiplied by 1000 once, here.

**The whole hemisphere is built, not the quarter the sources model.** The
quarter model restrains rotations on its two symmetry edges, and CalculiX has no
shell formulation of its own: it expands S6 into solids joined by knots, and a
rotational restraint on a knot is exactly where an expanded shell misbehaves.
The full hemisphere needs no symmetry restraint at all — only three isostatic
translational supports — so the model the solver sees is the benchmark's
surface with nothing CalculiX has to translate. `app.verify.nafems.run_le3`
states what that costs in the loads (they double) and in the read-out (half the
diametral change).

**Why it is sewn rather than revolved once.** A single 360 deg revolution gives
one face with a seam edge and a degenerate pole. gmsh meshed a gmsh-written
four-quarter model as four *disconnected* patches (65 duplicate nodes along the
quarter boundaries, measured 2026-09-15), so a load at A landed on two unjoined
nodes. Revolving one quarter, rotating copies of it and sewing them with
`BRepBuilderAPI_Sewing` gives one shell with shared edges, and the node at A is
a single node on the boundary of two patches.
"""

from __future__ import annotations

import math
from typing import Any, Final

#: Mid-surface radius, mm. Sourced as 10 m in four places (§1.5); the
#: mid-surface reading is the one INFERRED item (§1.7), and a shell encoding
#: never has to make it — the sphere *is* the modelled surface.
LE3_RADIUS_MM: Final = 10_000.0

#: Shell thickness, mm. Sourced as 0.04 m in four places (§1.5).
LE3_THICKNESS_MM: Final = 40.0

#: The points the benchmark names, on the full hemisphere. The NAFEMS figure's
#: triad puts z on the polar axis, x through A and y through C (§1.1). A' and C'
#: are the antipodes carrying the other two arrows of the full-hemisphere view.
LE3_POINT_A: Final = (LE3_RADIUS_MM, 0.0, 0.0)
LE3_POINT_A_OPPOSITE: Final = (-LE3_RADIUS_MM, 0.0, 0.0)
LE3_POINT_C: Final = (0.0, LE3_RADIUS_MM, 0.0)
LE3_POINT_C_OPPOSITE: Final = (0.0, -LE3_RADIUS_MM, 0.0)
LE3_POINT_E: Final = (0.0, 0.0, LE3_RADIUS_MM)

#: The exact mid-surface area of a hemisphere, 2 pi R^2, against which a mesh's
#: corner area is checked. A coarse triangulation of a sphere chords it inward,
#: so the meshed area falls short as the element grows — the shell analogue of
#: LE10's volume check.
LE3_AREA_MM2: Final = 2.0 * math.pi * LE3_RADIUS_MM**2


def hemisphere_shell(radius_mm: float = LE3_RADIUS_MM) -> Any:
    """The closed-at-the-pole hemisphere z >= 0 as one sewn OCCT shell.

    Four quarter patches, each a 90 deg revolution of the meridian arc E-A
    about z, sewn at a tolerance far below any mesh size. The arc uses the
    three-point form through an explicit midpoint: the circle-and-two-points
    form returns the major arc on this OCP build (CLAUDE.md, kernel item 10).
    """
    from app.kernel.occt.binding import symbol

    if not radius_mm > 0.0:
        raise ValueError(f"radius_mm must be positive, got {radius_mm!r}")

    point = symbol("gp_Pnt")
    direction = symbol("gp_Dir")
    pole = point(0.0, 0.0, radius_mm)
    equator = point(radius_mm, 0.0, 0.0)
    middle = point(radius_mm / math.sqrt(2.0), 0.0, radius_mm / math.sqrt(2.0))
    arc = symbol("BRepBuilderAPI_MakeEdge")(
        symbol("GC_MakeArcOfCircle")(pole, middle, equator).Value()
    ).Edge()

    axis = symbol("gp_Ax1")(point(0.0, 0.0, 0.0), direction(0.0, 0.0, 1.0))
    quarter = symbol("BRepPrimAPI_MakeRevol")(arc, axis, math.pi / 2.0).Shape()

    sewing = symbol("BRepBuilderAPI_Sewing")(1e-6)
    for turn in range(4):
        rotation = symbol("gp_Trsf")()
        rotation.SetRotation(axis, turn * math.pi / 2.0)
        sewing.Add(symbol("BRepBuilderAPI_Transform")(quarter, rotation, True).Shape())
    sewing.Perform()
    return sewing.SewedShape()


__all__ = [
    "LE3_AREA_MM2",
    "LE3_POINT_A",
    "LE3_POINT_A_OPPOSITE",
    "LE3_POINT_C",
    "LE3_POINT_C_OPPOSITE",
    "LE3_POINT_E",
    "LE3_RADIUS_MM",
    "LE3_THICKNESS_MM",
    "hemisphere_shell",
]
