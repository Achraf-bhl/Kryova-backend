"""Reference geometry: planes that are not the three the part started with.

Master plan Phase 2.4. Until this, a sketch could only sit on `XY`, `YZ` or `ZX`, which
means every profile in a design had to pass through the world origin. That is enough for
a first pad and nothing after it: **a boss on top of a pad needs a plane at the top of
the pad**, and there was no way to say so. This is the module that makes the second
feature of a real part possible.

**A reference plane is a frame, not a face.** It carries a `gp_Ax3` — where it sits, which
way it faces, and which way its local X runs — and no geometry at all. Nothing is added to
the part by creating one; it is a place to draw, exactly as in CATIA, where a plane is a
construction element rather than a body. That also means creating one cannot fail
downstream in the way a real surface can.

**The local X axis is inherited, and that is the whole reason offsets compose.** A plane
offset from `XY` keeps `XY`'s local X, so a rectangle placed `at (10, 5)` on the offset
plane lands directly above the same rectangle on `XY`. Deriving a fresh local X from the
normal — which is the obvious implementation, and what `gp_Ax3(point, normal)` does if you
let it — silently rotates the sketch frame, so the boss comes out square to nothing in
particular. Every plane built here carries its parent's X direction forward.

**Offsets are signed along the normal.** `distance_mm` positive moves along the reference
plane's normal, negative moves against it, and `reversed` flips whatever was given —
mirroring the operation's own schema, where both spellings exist because a CATIA user
reaches for either.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.kernel.errors import GeometryError
from app.kernel.occt.binding import require, symbol


@dataclass(frozen=True)
class ReferencePlane:
    """A named plane the design can sketch on or measure from.

    `derived_from` is kept for the feature listing rather than for the geometry: a plane
    called `boss_top` is far easier to reason about when the report can say it is `XY`
    offset by 20 mm than when it is three direction cosines.
    """

    name: str
    frame: Any
    derived_from: str = ""
    description: str = ""

    def plane(self) -> Any:
        """The plane as an OCCT surface, for the operations that want one."""
        return symbol("gp_Pln")(self.frame)

    def origin_mm(self) -> tuple[float, float, float]:
        location = self.frame.Location()
        return (location.X(), location.Y(), location.Z())

    def normal(self) -> tuple[float, float, float]:
        direction = self.frame.Direction()
        return (direction.X(), direction.Y(), direction.Z())

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane": self.name,
            "origin_mm": list(self.origin_mm()),
            "normal": list(self.normal()),
            "derived_from": self.derived_from,
            "description": self.description,
        }


@dataclass(frozen=True)
class ReferencePoint:
    """A named location in the part's frame.

    Deliberately plain coordinates rather than a `TopoDS_Vertex`. A reference point is a
    place, not geometry — nothing in the part is built from it, and holding it as a shape
    would invite it into a boolean and into the face and edge counts, where it means
    nothing and changes the determinism digest.
    """

    name: str
    position: tuple[float, float, float]
    derived_from: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": self.name,
            "position_mm": list(self.position),
            "derived_from": self.derived_from,
            "description": self.description,
        }


@dataclass(frozen=True)
class AxisSystem:
    """A named local coordinate system: an origin and three orthonormal axes.

    What a design uses to place a sub-assembly, or to state a measurement in something
    other than world coordinates. `frame` is a `gp_Ax3`, so its Z is the axis system's Z
    and its X direction is the local X — the same object a sketch is drawn in, which is
    what lets a plane and an axis system be interchangeable wherever a frame is wanted.
    """

    name: str
    frame: Any
    derived_from: str = ""

    def origin_mm(self) -> tuple[float, float, float]:
        location = self.frame.Location()
        return (location.X(), location.Y(), location.Z())

    def axis(self, letter: str) -> tuple[float, float, float]:
        """One axis as a unit vector. `letter` is x, y or z, in any case."""
        chooser = {
            "x": self.frame.XDirection,
            "y": self.frame.YDirection,
            "z": self.frame.Direction,
        }
        try:
            direction = chooser[letter.lower()]()
        except KeyError:
            raise GeometryError(
                f"{letter!r} is not an axis of {self.name!r}. Use x, y or z."
            ) from None
        return (direction.X(), direction.Y(), direction.Z())

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_system": self.name,
            "origin_mm": list(self.origin_mm()),
            "x_direction": list(self.axis("x")),
            "y_direction": list(self.axis("y")),
            "z_direction": list(self.axis("z")),
            "derived_from": self.derived_from,
        }


def axis_frame(
    origin: tuple[float, float, float],
    x_direction: tuple[float, float, float] | None = None,
    y_direction: tuple[float, float, float] | None = None,
) -> Any:
    """A right-handed frame at `origin`, aligned to whichever directions were given.

    Three cases, and the middle one is the one worth stating:

    * **Neither given** — the world axes, moved to the origin. What "a local axis system
      here" means with no further qualification.
    * **Only X given** — Z is chosen perpendicular to X, and *which* perpendicular is
      arbitrary. That is honest but rarely what someone wants, so the choice is made
      deterministically (against the world axis least parallel to X) rather than left to
      whatever OCCT would pick, so the same input always gives the same frame. Two
      directions is the way to get the frame you meant.
    * **Both given** — Z is X × Y. If Y was not perpendicular to X it is *not* used
      as-is: the frame's Y becomes Z × X, which is the component of the given Y
      perpendicular to X. This is the standard reading and the only one that can produce
      an orthonormal frame at all, but it does mean a sloppy Y is silently squared up.
    """
    require()
    location = symbol("gp_Pnt")(*origin)

    if x_direction is None and y_direction is None:
        return symbol("gp_Ax3")(
            location, symbol("gp_Dir")(0.0, 0.0, 1.0), symbol("gp_Dir")(1.0, 0.0, 0.0)
        )
    if x_direction is None:
        raise GeometryError(
            "An axis system given only a y_direction has no X to align to. Give "
            "x_direction, or give both."
        )

    if y_direction is None:
        z_direction = _any_perpendicular(x_direction)
    else:
        z_direction = _cross(x_direction, y_direction)
        if _norm(z_direction) < 1e-12:
            raise GeometryError(
                "x_direction and y_direction are parallel, so they define no plane and "
                "no axis system. Give two directions that differ."
            )

    return symbol("gp_Ax3")(
        location, symbol("gp_Dir")(*z_direction), symbol("gp_Dir")(*x_direction)
    )


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm(vector: tuple[float, float, float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _any_perpendicular(direction: tuple[float, float, float]) -> tuple[float, float, float]:
    """Some unit vector perpendicular to `direction`, chosen the same way every time.

    Crossing with the world axis *least* parallel to the input, because crossing with a
    nearly-parallel one gives a tiny vector whose direction is mostly rounding error.
    """
    world = min(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        key=lambda axis: abs(sum(axis[i] * direction[i] for i in range(3))),
    )
    return _cross(direction, world)


def offset_frame(base: Any, distance_mm: float) -> Any:
    """A frame parallel to `base`, moved `distance_mm` along its normal.

    The direction and the local X are carried over unchanged — see the module docstring
    on why inheriting X is what makes offsets compose. Only the location moves.
    """
    require()
    location = base.Location()
    normal = base.Direction()
    moved = symbol("gp_Pnt")(
        location.X() + normal.X() * distance_mm,
        location.Y() + normal.Y() * distance_mm,
        location.Z() + normal.Z() * distance_mm,
    )
    return symbol("gp_Ax3")(moved, normal, base.XDirection())


def translated_frame(base: Any, offset: tuple[float, float, float]) -> Any:
    """A frame moved by `offset` expressed in **its own** axes, not the world's.

    This is what a sketch's `origin` argument means — *where the sketch's own (0, 0) sits
    on the support* — and it is the only reading that behaves the same on every plane. A
    world-coordinate reading gives the same answer on `XY`, where the frame axes happen to
    be the world axes, and a different one on `YZ`, so the inconsistency stays invisible
    until somebody sketches on the side of a part.

    It is also what `sketching.point_on` already does for every 2D point drawn on a
    sketch, so this makes the sketch's own origin obey the rule its contents obey.
    """
    require()
    location = base.Location()
    x_dir, y_dir, normal = base.XDirection(), base.YDirection(), base.Direction()
    u, v, w = offset
    moved = symbol("gp_Pnt")(
        location.X() + x_dir.X() * u + y_dir.X() * v + normal.X() * w,
        location.Y() + x_dir.Y() * u + y_dir.Y() * v + normal.Y() * w,
        location.Z() + x_dir.Z() * u + y_dir.Z() * v + normal.Z() * w,
    )
    return symbol("gp_Ax3")(moved, normal, x_dir)


def frame_from_points(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    third: tuple[float, float, float],
) -> Any:
    """A frame through three points, with local X along the first two.

    Collinear points are refused rather than producing a degenerate frame: three points
    on a line define no plane, and OCCT's own failure for it arrives much later and says
    much less.
    """
    require()
    edge_a = tuple(second[i] - first[i] for i in range(3))
    edge_b = tuple(third[i] - first[i] for i in range(3))
    normal = (
        edge_a[1] * edge_b[2] - edge_a[2] * edge_b[1],
        edge_a[2] * edge_b[0] - edge_a[0] * edge_b[2],
        edge_a[0] * edge_b[1] - edge_a[1] * edge_b[0],
    )
    if sum(component * component for component in normal) < 1e-18:
        raise GeometryError(
            "These three points are collinear, so they define no plane. Move one of them "
            "off the line through the other two."
        )
    return symbol("gp_Ax3")(
        symbol("gp_Pnt")(*first),
        symbol("gp_Dir")(*normal),
        symbol("gp_Dir")(*edge_a),
    )


def rotated_frame(base: Any, axis_origin: tuple[float, float, float],
                  axis_direction: tuple[float, float, float], angle_deg: float) -> Any:
    """`base`, hinged about a line by an angle — what `catia_plane_angle` builds.

    The rotation is applied to the whole frame, so the plane's local X turns with it and
    a sketch drawn on the result keeps the orientation its reference had. Rotating only
    the normal would leave the X axis pointing out of the new plane, which is not a frame
    at all.
    """
    require()
    transform = symbol("gp_Trsf")()
    transform.SetRotation(
        symbol("gp_Ax1")(symbol("gp_Pnt")(*axis_origin), symbol("gp_Dir")(*axis_direction)),
        math.radians(angle_deg),
    )
    return base.Transformed(transform)


__all__ = [
    "AxisSystem",
    "ReferencePlane",
    "ReferencePoint",
    "axis_frame",
    "frame_from_points",
    "offset_frame",
    "rotated_frame",
    "translated_frame",
]
