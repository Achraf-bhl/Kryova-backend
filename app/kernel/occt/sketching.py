"""Sketches: a plane, and closed profiles drawn on it.

**Why this needs no constraint solver, and where one is still required.** Master plan
1.3 budgets for PlaneGCS because a sketcher in general needs a 2D constraint solver. But
the registry's sketch vocabulary is *dimension-driven*: `catia_sketch_rectangle` takes a
width and a height, `catia_sketch_circle` a diameter, `catia_sketch_polygon` a side count
and a diameter. A rectangle whose width is given is fully determined — there is nothing
for a solver to solve. So the profiles that actually feed pads, pockets, shafts and
grooves are buildable now, directly, and PlaneGCS is deferred to the one operation that
genuinely needs it: `catia_sketch_constrain`, which applies arbitrary constraints to free
geometry. That operation refuses with the reason until the solver lands.

This is a real narrowing of scope, not a claim to have finished 1.3, and the coverage
figure reports it as such.

**A sketch is a plane plus an ordered list of closed profiles.** It is not geometry until
something consumes it: a pad extrudes it, a shaft revolves it. That matches both CATIA's
model and the design IR's, where `catia_sketch_create` produces something later features
reference by name, and it is why `Sketch` lives here rather than being a `TopoDS_Shape`
on the document.

**Profiles are built in the plane's own 2D frame and placed by its transform**, rather
than being drawn in world coordinates and hoped into position. A sketch on `YZ` with a
rectangle `at` (10, 5) puts that rectangle 10 mm along the plane's local X and 5 along
its local Y — which is what the author meant, and is not what world coordinates would
have given.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

from app.catia.ops import vocabulary
from app.kernel.errors import GeometryError
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.reference import translated_frame

#: The origin planes, mapped to (normal, local-X) direction vectors. Taken from the
#: shared vocabulary rather than re-spelled, so a plane the schema accepts is a plane
#: this can build. The local X axis is chosen to match CATIA's convention, so a
#: rectangle placed `at` a point lands where the author expects on every plane.
_PLANE_FRAMES: Final[dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]] = {
    "XY": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "YZ": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "ZX": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
}

#: Below this, two profile points are the same point and an edge between them is
#: degenerate. OCCT will refuse such an edge; catching it here says which points.
MIN_SEGMENT_MM: Final = 1e-9


def _validate_plane_vocabulary() -> None:
    """The plane table must cover exactly what the schema accepts.

    Checked at import: a plane the registry allows but this cannot build would fail
    deep inside a sketch operation with an OCCT error rather than at the boundary.
    """
    declared = set(vocabulary.ORIGIN_PLANES)
    built = set(_PLANE_FRAMES)
    if declared != built:
        raise GeometryError(
            f"The sketch plane table and the operation vocabulary disagree: "
            f"vocabulary has {sorted(declared)}, this module builds {sorted(built)}."
        )


_validate_plane_vocabulary()


@dataclass
class Sketch:
    """A plane and the closed profiles drawn on it.

    Not geometry on its own — a pad extrudes it, a shaft revolves it. Held on the
    document under the design's own name so a later feature can reference it.
    """

    name: str
    support: str
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)

    #: The resolved coordinate system this sketch is drawn in.
    #:
    #: **Resolved once, at creation, rather than looked up on every use.** `support` may
    #: name an origin plane or a plane the design constructed (Phase 2.4), and only the
    #: document knows the second kind — so recomputing the frame here would mean handing
    #: the document to every profile operation. It is also the honest model: a sketch is
    #: drawn on the plane as it stood when the sketch was opened.
    frame_ax3: Any = None

    #: Closed wires, in the order they were drawn. The first is the outer boundary;
    #: any that follow are treated as holes when a face is made (which is what a
    #: sketch with an inner circle means to a pad).
    profiles: list[Any] = field(default_factory=list)

    #: Profiles marked `construction` are reference geometry: they position other
    #: elements and are never extruded. Kept separate rather than filtered on use, so
    #: "why is my pad hollow" has an inspectable answer.
    construction: list[Any] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.profiles

    def plane(self) -> Any:
        """The OCCT plane this sketch is drawn on."""
        require()
        return symbol("gp_Pln")(self.frame())

    def frame(self) -> Any:
        """The sketch's local coordinate system, for placing 2D points in 3D."""
        if self.frame_ax3 is None:
            # A sketch built without a resolved frame falls back to the origin planes,
            # which is what every sketch was before Phase 2.4 and keeps a directly
            # constructed `Sketch(...)` working in a test.
            self.frame_ax3 = frame_of(self.support, self.origin)
        return self.frame_ax3

    def face(self) -> Any:
        """The profiles as a single face, ready to be extruded or revolved.

        Later profiles become holes in the first. That is what a sketch containing an
        outer rectangle and an inner circle means, and building it any other way makes
        a pad that ignores its own bore.
        """
        require()
        if self.is_empty:
            raise GeometryError(
                f"Sketch {self.name!r} has no closed profile, so there is nothing to "
                "build from. Draw a rectangle, circle or polygon on it first."
            )
        maker = symbol("BRepBuilderAPI_MakeFace")(self.plane(), self.profiles[0])
        for inner in self.profiles[1:]:
            maker.Add(inner)
        if not maker.IsDone():
            raise GeometryError(
                f"The profiles on sketch {self.name!r} do not form a valid face. They "
                "may overlap, or an inner profile may fall outside the outer one."
            )
        return maker.Face()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sketch": self.name,
            "support": self.support,
            "profiles": len(self.profiles),
            "construction": len(self.construction),
        }


def plane_of(support: str, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Any:
    """The OCCT plane named by an origin-plane word."""
    require()
    return symbol("gp_Pln")(frame_of(support, origin))


def frame_of(support: str, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Any:
    """A right-handed frame on one of the **origin** planes.

    Constructed planes are resolved by the document, not here — see
    `operations.sketcher.resolve_support`, which tries this first and then the design's
    own named planes.

    **`origin` shifts the frame along its own axes, not the world's.** The argument means
    *where the sketch's own (0, 0) sits on the support*, and that is the only reading
    which behaves the same on every plane: the two agree on `XY`, where the frame axes
    are the world axes, and disagree on `YZ`, so a world reading looks correct until
    somebody sketches on the side of a part. It is also the rule `point_on` already
    applies to every 2D point drawn on a sketch.
    """
    require()
    key = str(support).upper()
    axes = _PLANE_FRAMES.get(key)
    if axes is None:
        known = ", ".join(vocabulary.ORIGIN_PLANES)
        raise GeometryError(
            f"{support!r} is not an origin plane. Use one of: {known}, or the name of a "
            "plane this design constructed with catia_plane_offset."
        )
    normal, local_x = axes
    base = symbol("gp_Ax3")(
        symbol("gp_Pnt")(0.0, 0.0, 0.0),
        symbol("gp_Dir")(*normal),
        symbol("gp_Dir")(*local_x),
    )
    return translated_frame(base, origin) if any(origin) else base


def point_on(sketch: Sketch, uv: tuple[float, float]) -> Any:
    """Place a sketch-local (u, v) point into 3D on the sketch's plane.

    This is what makes `at: [10, 5]` mean the same thing on every plane: the offsets
    are along the sketch's own axes, not the world's.
    """
    require()
    frame = sketch.frame()
    location = frame.Location()
    x_dir, y_dir = frame.XDirection(), frame.YDirection()
    u, v = uv
    return symbol("gp_Pnt")(
        location.X() + x_dir.X() * u + y_dir.X() * v,
        location.Y() + x_dir.Y() * u + y_dir.Y() * v,
        location.Z() + x_dir.Z() * u + y_dir.Z() * v,
    )


def rotate_uv(uv: tuple[float, float], degrees: float) -> tuple[float, float]:
    """Rotate a sketch-local point about the sketch origin."""
    if not degrees:
        return uv
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    u, v = uv
    return (u * cos - v * sin, u * sin + v * cos)


def closed_wire(sketch: Sketch, corners: list[tuple[float, float]]) -> Any:
    """A closed wire through sketch-local points, in order.

    The polyline is closed by joining the last point back to the first, so callers pass
    corners rather than repeating the first point — repeating it produces a
    zero-length edge, which OCCT refuses with a message that does not mention the
    duplicate.
    """
    require()
    if len(corners) < 3:
        raise GeometryError(
            f"A closed profile needs at least three points; got {len(corners)}."
        )

    points = [point_on(sketch, uv) for uv in corners]
    maker = symbol("BRepBuilderAPI_MakeWire")()
    edge_of = symbol("BRepBuilderAPI_MakeEdge")

    for index in range(len(points)):
        start, end = points[index], points[(index + 1) % len(points)]
        if start.Distance(end) < MIN_SEGMENT_MM:
            raise GeometryError(
                f"Points {index} and {(index + 1) % len(points)} of this profile are "
                "the same point, so the edge between them has no length. Corners are "
                "given once each — the profile is closed automatically."
            )
        maker.Add(edge_of(start, end).Edge())

    if not maker.IsDone():
        raise GeometryError(
            "Those points do not form a closed profile. They may be collinear, or the "
            "outline may cross itself."
        )
    return maker.Wire()


def circle_wire(sketch: Sketch, centre_uv: tuple[float, float], diameter_mm: float) -> Any:
    """A closed circular wire on the sketch plane."""
    require()
    if diameter_mm <= 0:
        raise GeometryError(
            f"A circle needs a positive diameter; got {diameter_mm}. A zero-diameter "
            "circle is not a profile."
        )
    frame = sketch.frame()
    axis = symbol("gp_Ax2")(point_on(sketch, centre_uv), frame.Direction(), frame.XDirection())
    circle = symbol("gp_Circ")(axis, diameter_mm / 2.0)
    edge = symbol("BRepBuilderAPI_MakeEdge")(circle).Edge()
    maker = symbol("BRepBuilderAPI_MakeWire")()
    maker.Add(edge)
    return maker.Wire()


def rectangle_corners(
    width_mm: float, height_mm: float, at: tuple[float, float], rotation_deg: float
) -> list[tuple[float, float]]:
    """The four corners of a rectangle centred on `at`, rotated about it.

    Centred rather than corner-anchored because that is what the registry's `at` means
    everywhere else — a hole `at` a point is centred there — and a rectangle that
    disagreed would put every profile half a width out.
    """
    half_w, half_h = width_mm / 2.0, height_mm / 2.0
    local = [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)]
    return [
        (at[0] + rotated[0], at[1] + rotated[1])
        for rotated in (rotate_uv(corner, rotation_deg) for corner in local)
    ]


def polygon_corners(
    sides: int, diameter_mm: float, at: tuple[float, float], rotation_deg: float
) -> list[tuple[float, float]]:
    """A regular polygon inscribed in `diameter_mm`, centred on `at`."""
    if sides < 3:
        raise GeometryError(f"A polygon needs at least three sides; got {sides}.")
    if diameter_mm <= 0:
        raise GeometryError(f"A polygon needs a positive diameter; got {diameter_mm}.")
    radius = diameter_mm / 2.0
    start = math.radians(rotation_deg)
    return [
        (
            at[0] + radius * math.cos(start + 2 * math.pi * index / sides),
            at[1] + radius * math.sin(start + 2 * math.pi * index / sides),
        )
        for index in range(sides)
    ]


def slot_wire(
    sketch: Sketch,
    start_uv: tuple[float, float],
    end_uv: tuple[float, float],
    width_mm: float,
) -> Any:
    """An obround: two parallel sides capped by semicircles.

    Built from real arcs rather than approximated by a many-sided polygon, because a
    slot's ends are a bearing surface and a faceted one is a different part.
    """
    require()
    if width_mm <= 0:
        raise GeometryError(f"A slot needs a positive width; got {width_mm}.")

    ux, uy = end_uv[0] - start_uv[0], end_uv[1] - start_uv[1]
    length = math.hypot(ux, uy)
    if length < MIN_SEGMENT_MM:
        raise GeometryError(
            "A slot's start and end are the same point, so it has no direction. Use a "
            "circle if a round hole was meant."
        )

    radius = width_mm / 2.0
    # Unit normal to the slot axis, in sketch-local coordinates.
    nx, ny = -uy / length, ux / length
    offset = (nx * radius, ny * radius)

    left_start = (start_uv[0] + offset[0], start_uv[1] + offset[1])
    left_end = (end_uv[0] + offset[0], end_uv[1] + offset[1])
    right_end = (end_uv[0] - offset[0], end_uv[1] - offset[1])
    right_start = (start_uv[0] - offset[0], start_uv[1] - offset[1])
    beyond_end = (end_uv[0] + (ux / length) * radius, end_uv[1] + (uy / length) * radius)
    beyond_start = (
        start_uv[0] - (ux / length) * radius,
        start_uv[1] - (uy / length) * radius,
    )

    edge_of = symbol("BRepBuilderAPI_MakeEdge")
    arc_of = symbol("GC_MakeArcOfCircle")
    maker = symbol("BRepBuilderAPI_MakeWire")()

    maker.Add(edge_of(point_on(sketch, left_start), point_on(sketch, left_end)).Edge())
    maker.Add(
        edge_of(
            arc_of(
                point_on(sketch, left_end),
                point_on(sketch, beyond_end),
                point_on(sketch, right_end),
            ).Value()
        ).Edge()
    )
    maker.Add(edge_of(point_on(sketch, right_end), point_on(sketch, right_start)).Edge())
    maker.Add(
        edge_of(
            arc_of(
                point_on(sketch, right_start),
                point_on(sketch, beyond_start),
                point_on(sketch, left_start),
            ).Value()
        ).Edge()
    )

    if not maker.IsDone():
        raise GeometryError(
            "The slot's sides and end arcs did not join into a closed profile. Check "
            "that its width is smaller than the distance between its centres."
        )
    return maker.Wire()


__all__ = [
    "MIN_SEGMENT_MM",
    "Sketch",
    "circle_wire",
    "closed_wire",
    "frame_of",
    "plane_of",
    "point_on",
    "polygon_corners",
    "rectangle_corners",
    "rotate_uv",
    "slot_wire",
]
