"""Building a folded sheet-metal part as an OCCT solid — phase 17.3 task 3.

`app.sheetmetal.fold` places every face and every bend of a `SheetMetalPart` in
space; this turns that placement into geometry. A flat face becomes a box, a bend
becomes a cylindrical sector, and the whole part is one fused solid in the same
mm-N-MPa coordinates every other shape in this repository lives in.

**Why this module exists at all.** Until it did, a sheet-metal part had two
descriptions that nothing connected: the blank `app.sheetmetal` computes and a
solid somebody drew by hand as a cross-section to extrude — `app/design/missions.py`
says so about M3 and publishes `flat.volume_mismatch_mm3` as the only evidence they
were the same part. Now the solid is *generated from the fold tree*, so that
residual measures the arithmetic instead of measuring whether two sets of numbers
were typed the same way.

**The dependency runs one way, on purpose.** `app.sheetmetal` knows nothing about
OCCT and keeps its offline property; this module imports it. Do not invert that to
"save an import": a flat pattern that needs 166 MB of OCP to test is a flat pattern
nobody tests.

**No sheet-metal operation is added to the CATIA registry by this.** The registry
describes what the *seat* can be told to do, and a `catia_sheetmetal_wall` there
would be a promise the bridge cannot keep — CATIA's SheetMetal Design workbench is
real, but the COM calls behind it are unwritten and unverifiable without a licensed
seat. That work is a row in THE QUEUE (`docs/WINDOWS_VERIFICATION.md`), not a
declaration made on a machine that cannot test it. What is available here is the
open-kernel path Decision 1 asks for: geometry that builds headless, free, in CI.

**Three things that are easy to get wrong and are pinned by tests.**

1. `BRepPrimAPI_MakeBox(gp_Ax2(P, n, u), L, W, t)` fills `u x [0,L]`, `v x [0,W]`,
   `n x [0,t]` — because `gp_Ax2`'s Y direction is `main x X`, i.e. `n x u = v`.
   That is the same asymmetry `app/render/project.py` documents from the other
   side, and building the box off `gp_Ax2(P, u, n)` instead silently mirrors it.
2. The sector's arcs are built through a **midpoint** (`GC_MakeArcOfCircle`'s
   three-point form), never from a `gp_Circ` and two ends: measured on this OCP
   build, that overload returns the major arc for both senses, so a 90-degree bend
   comes back as 270 degrees of material — see `app/verify/le11_geometry.py`, which
   hit it first.
3. A `theta`-degree sector is one face swept along the bend, and it is fused
   to the plates rather than glued: `BRepAlgoAPI_Fuse` on two solids sharing a face
   gives one solid whose volume is the sum, which is exactly what the analytic
   oracle claims. A compound would measure the same and behave differently
   everywhere downstream.
"""

from __future__ import annotations

import math
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt.binding import require, symbol
from app.sheetmetal.fold import (
    FoldedBend,
    FoldedFace,
    FoldedHole,
    FoldedLayout,
    Vector3,
    fold_layout,
)
from app.sheetmetal.unfold import SheetMetalPart


def _point(vector: Vector3) -> Any:
    return symbol("gp_Pnt")(vector[0], vector[1], vector[2])


def _direction(vector: Vector3) -> Any:
    return symbol("gp_Dir")(vector[0], vector[1], vector[2])


def _vector(vector: Vector3, scale: float = 1.0) -> Any:
    return symbol("gp_Vec")(vector[0] * scale, vector[1] * scale, vector[2] * scale)


def _rotated(about: Vector3, vector: Vector3, angle_rad: float) -> Vector3:
    """Rodrigues' rotation of `vector` about the unit axis `about`.

    Written out rather than routed through `gp_Trsf` because the sector's corner
    points are needed as plain numbers to build the wire, and converting into and
    out of OCCT types to rotate three vectors is more code than the formula.
    """
    cos = math.cos(angle_rad)
    sin = math.sin(angle_rad)
    dot = about[0] * vector[0] + about[1] * vector[1] + about[2] * vector[2]
    cross = (
        about[1] * vector[2] - about[2] * vector[1],
        about[2] * vector[0] - about[0] * vector[2],
        about[0] * vector[1] - about[1] * vector[0],
    )
    return (
        vector[0] * cos + cross[0] * sin + about[0] * dot * (1.0 - cos),
        vector[1] * cos + cross[1] * sin + about[1] * dot * (1.0 - cos),
        vector[2] * cos + cross[2] * sin + about[2] * dot * (1.0 - cos),
    )


def _plate(face: FoldedFace) -> Any:
    """One flat face as a box: `u x [0,L]`, `v x [0,W]`, `n x [0,t]`."""
    frame = symbol("gp_Ax2")(
        _point(face.origin_mm), _direction(face.normal), _direction(face.u_dir)
    )
    return symbol("BRepPrimAPI_MakeBox")(
        frame, face.length_mm, face.width_mm, face.thickness_mm
    ).Shape()


def _sector(bend: FoldedBend) -> Any:
    """One bend as a cylindrical sector swept along the bend.

    The cross-section is the annulus segment between `r` and `r + t` from the bend
    centre, turned from `start_dir` through `angle_deg` about `axis_dir`; the
    prism carries it `width_mm` along `width_dir`, which is the direction the
    material runs and **not** the rotation axis — see `FoldedBend`.
    """
    centre = bend.centre_mm
    axis = bend.axis_dir
    inner = bend.inside_radius_mm
    outer = inner + bend.thickness_mm
    theta = math.radians(bend.angle_deg)

    def at(radius: float, angle_rad: float) -> Any:
        radial = _rotated(axis, bend.start_dir, angle_rad)
        return _point(
            (
                centre[0] + radial[0] * radius,
                centre[1] + radial[1] * radius,
                centre[2] + radial[2] * radius,
            )
        )

    edge = symbol("BRepBuilderAPI_MakeEdge")
    arc = symbol("GC_MakeArcOfCircle")
    inner_start, inner_end = at(inner, 0.0), at(inner, theta)
    outer_start, outer_end = at(outer, 0.0), at(outer, theta)

    wire = symbol("BRepBuilderAPI_MakeWire")()
    wire.Add(edge(arc(inner_start, at(inner, theta / 2.0), inner_end).Value()).Edge())
    wire.Add(edge(inner_end, outer_end).Edge())
    wire.Add(edge(arc(outer_end, at(outer, theta / 2.0), outer_start).Value()).Edge())
    wire.Add(edge(outer_start, inner_start).Edge())

    section = symbol("BRepBuilderAPI_MakeFace")(
        symbol("gp_Pln")(_point(centre), _direction(axis)), wire.Wire()
    ).Face()
    return symbol("BRepPrimAPI_MakePrism")(
        section, _vector(bend.width_dir, bend.width_mm)
    ).Shape()


def _drill(hole: FoldedHole) -> Any:
    """One declared hole as a cylinder through its face, and a little beyond.

    The cylinder starts one thickness short of the face and runs three, so the
    cut passes cleanly through both surfaces: a cylinder that ends *on* a face
    leaves a coincident-face boolean, which OCCT resolves into a zero-thickness
    sliver rather than a hole often enough to matter.
    """
    start: Vector3 = (
        hole.centre_mm[0] - hole.axis_dir[0] * hole.thickness_mm,
        hole.centre_mm[1] - hole.axis_dir[1] * hole.thickness_mm,
        hole.centre_mm[2] - hole.axis_dir[2] * hole.thickness_mm,
    )
    frame = symbol("gp_Ax2")(_point(start), _direction(hole.axis_dir))
    return symbol("BRepPrimAPI_MakeCylinder")(
        frame, hole.diameter_mm / 2.0, hole.thickness_mm * 3.0
    ).Shape()


def build_layout(layout: FoldedLayout) -> Any:
    """Fuse a placed layout into one solid, and cut its declared holes.

    Separate from `fold` so a caller that already has a layout — a drawing, a
    selector, a future partial rebuild — does not place the part twice.
    """
    require()
    shapes = [_plate(face) for face in layout.faces]
    shapes.extend(_sector(bend) for bend in layout.bends)
    if not shapes:  # pragma: no cover - a part always has a root flange
        raise GeometryError(
            f"{layout.part_name!r} placed no faces and no bends, so there is nothing "
            "to build. A sheet-metal part has at least its root flange."
        )
    fuse = symbol("BRepAlgoAPI_Fuse")
    solid = shapes[0]
    for piece in shapes[1:]:
        operation = fuse(solid, piece)
        operation.Build()
        if not operation.IsDone():
            raise GeometryError(
                f"Fusing {layout.part_name!r} failed while adding one of its "
                f"{len(shapes)} pieces. The faces and bends of a folded part meet on "
                "shared tangent faces, so a failure here means two of them are not "
                "touching where the layout says they are — check the bend directions "
                "and the flange offsets."
            )
        solid = operation.Shape()

    cut = symbol("BRepAlgoAPI_Cut")
    for hole in layout.holes:
        operation = cut(solid, _drill(hole))
        operation.Build()
        if not operation.IsDone():
            raise GeometryError(
                f"Cutting {hole.name!r} out of {layout.part_name!r} failed. The hole "
                f"is placed on the flat part of {hole.face!r} — a hole in a bend zone "
                "is refused before this point — so a failure here is a boolean "
                "problem, not a placement one."
            )
        solid = operation.Shape()
    return solid


def fold(part: SheetMetalPart) -> Any:
    """Build a folded sheet-metal part as a single OCCT solid, in millimetres.

    The root flange lies in the `z = 0` plane with its material filling
    `z in [0, t]`, its length along `+x` and its width along `+y`; everything else
    follows from the bends. Raises `UnfoldError` for a part whose own dimensions do
    not describe a solid — a leg its bends have eaten, a flange hanging off the end
    of the edge it is bent from — and `KernelUnavailable` where OCCT is not
    installed.
    """
    return build_layout(fold_layout(part))


__all__ = ["build_layout", "fold"]
