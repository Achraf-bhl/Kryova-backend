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

#: How close two drawn ends must be to count as the same point when chaining segments.
#:
#: Deliberately much looser than `MIN_SEGMENT_MM`, because it answers a different
#: question. That constant asks "is this edge degenerate", where any separation at all
#: is a real edge. This one asks "did the author mean these to join", and nobody draws a
#: contour whose corner misses by a micron on purpose — while a coordinate that has been
#: through the sketch's plane transform and back can easily land that far out.
JOIN_TOLERANCE_MM: Final = 1e-6


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
class OpenCurve:
    """A run of drawn segments that has not closed back on itself.

    Its ends are carried in the sketch's own 2D coordinates rather than read back off
    the wire. That is where the author put them, and reading them back would ask OCCT's
    vertex tolerance a question the drawing has already answered exactly.
    """

    wire: Any
    start: tuple[float, float]
    end: tuple[float, float]

    def is_closed(self) -> bool:
        return _same_point(self.start, self.end)


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

    #: Points drawn on the sketch, in **its own 2D coordinates**, in the order drawn.
    #:
    #: Held as (u, v) pairs rather than as `TopoDS_Vertex` for the reason
    #: `ReferencePoint` is: a point is a place, not geometry. Letting one into `profiles`
    #: would put it into the face a pad extrudes, where it means nothing and changes the
    #: determinism digest. A user pattern reads these through `world_points()`.
    points: list[tuple[float, float]] = field(default_factory=list)

    #: Runs of segments that have not closed — a rib's spine, a stiffener's line, a
    #: contour still being drawn.
    #:
    #: **An open run is not a profile and must never be padded.** A pad over a sketch
    #: takes `profiles` alone, so a half-drawn contour cannot silently become a face
    #: with a straight edge across the gap the author had not filled in yet.
    curves: list[OpenCurve] = field(default_factory=list)

    #: The revolution axis, in sketch coordinates, when the design drew one.
    #:
    #: A shaft with no axis revolves about the sketch's own vertical, which is CATIA's
    #: convention and the right default for a single-profile part. Drawing an axis is
    #: how a design says otherwise, and it is per-sketch rather than per-feature because
    #: that is where CATIA puts it.
    axis: tuple[tuple[float, float], tuple[float, float]] | None = None

    @property
    def is_empty(self) -> bool:
        return not self.profiles

    def to_world(self, point: tuple[float, float]) -> tuple[float, float, float]:
        """One of this sketch's own (u, v) coordinates, as a world position."""
        frame = self.frame()
        origin, x_axis, y_axis = frame.Location(), frame.XDirection(), frame.YDirection()
        return (
            origin.X() + point[0] * x_axis.X() + point[1] * y_axis.X(),
            origin.Y() + point[0] * x_axis.Y() + point[1] * y_axis.Y(),
            origin.Z() + point[0] * x_axis.Z() + point[1] * y_axis.Z(),
        )

    def world_points(self) -> list[tuple[float, float, float]]:
        """Every drawn point, in world coordinates and in the order drawn."""
        return [self.to_world(point) for point in self.points]

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

    def add_segment(
        self, edge: Any, start: tuple[float, float], end: tuple[float, float]
    ) -> str:
        """Draw one segment, chaining it onto the run it continues.

        This is what makes four `catia_sketch_line` calls into a square a pad can
        extrude, rather than four unrelated edges — which is how a sketcher behaves and
        what the registry's own summary promises ("chain these to build a contour").

        Chaining is **forward, onto the most recent open run only**. A segment whose
        start does not meet that run's end begins a new one. Searching every open run
        for a match would join two contours the author was drawing side by side, and the
        symptom is a profile with a bridge across it that nobody drew.

        Returns what happened — `started`, `extended` or `closed` — so the operation can
        report it. A run that closes moves out of `curves` and into `profiles`, because
        from that moment it *is* a profile.
        """
        require()
        if start == end or _same_point(start, end):
            raise GeometryError(
                "This segment begins and ends at the same point, so it has no length. "
                "Check the coordinates, or use catia_sketch_circle for a closed shape."
            )

        run = self.curves[-1] if self.curves else None
        if run is None or not _same_point(run.end, start):
            self.curves.append(OpenCurve(wire=_wire_of(edge), start=start, end=end))
            return "started"

        run.wire = _extended_wire(run.wire, edge)
        run.end = end
        if run.is_closed():
            self.curves.remove(run)
            self.profiles.append(run.wire)
            return "closed"
        return "extended"

    def path(self, *, tool: str) -> Any:
        """The single curve this sketch offers as a sweep path.

        A rib follows a spine and a sketch may hold several curves, so the ambiguous
        cases are refused by name rather than resolved by picking the first. An open run
        is preferred over a closed profile because that is what a spine normally is, but
        a closed one is accepted — a sweep round a closed loop is a real thing to want.
        """
        if len(self.curves) == 1:
            return self.curves[0].wire
        if not self.curves and len(self.profiles) == 1:
            return self.profiles[0]
        if not self.curves and not self.profiles:
            raise GeometryError(
                f"{tool} was given sketch {self.name!r} as a path, and nothing is drawn "
                "on it. Draw the path with catia_sketch_line, catia_sketch_polyline, "
                "catia_sketch_arc or catia_sketch_spline."
            )
        raise GeometryError(
            f"{tool} needs one curve to follow, and sketch {self.name!r} holds "
            f"{len(self.curves)} open and {len(self.profiles)} closed. Draw the path in "
            "a sketch of its own so there is no doubt which one is the spine."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sketch": self.name,
            "support": self.support,
            "profiles": len(self.profiles),
            "construction": len(self.construction),
            "points": len(self.points),
            "curves": len(self.curves),
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


def line_edge(
    sketch: Sketch, start: tuple[float, float], end: tuple[float, float]
) -> Any:
    """A straight edge between two sketch-local points."""
    require()
    return symbol("BRepBuilderAPI_MakeEdge")(
        point_on(sketch, start), point_on(sketch, end)
    ).Edge()


def arc_edge(
    sketch: Sketch,
    centre: tuple[float, float],
    radius_mm: float,
    start_angle_deg: float,
    end_angle_deg: float,
) -> tuple[Any, tuple[float, float], tuple[float, float]]:
    """A circular arc, with the sketch-local points its ends land on.

    The ends are returned rather than recovered from the edge because chaining compares
    them against what the *next* call passes in, and a value computed the same way twice
    matches exactly where one read back through a curve parameterisation may not.

    Angles run anticlockwise from the sketch's own horizontal axis, which is what the
    registry documents. A span of a full turn or more is refused: an arc is not a
    circle, and `catia_sketch_circle` is the operation that draws one.
    """
    require()
    if radius_mm <= 0:
        raise GeometryError(f"An arc needs a positive radius; got {radius_mm}.")

    span = end_angle_deg - start_angle_deg
    if abs(span) < _MIN_ARC_SPAN_DEG:
        raise GeometryError(
            f"This arc starts and ends at {start_angle_deg}°, so it has no length. "
            "Give a different end angle."
        )
    if abs(span) >= 360.0:
        raise GeometryError(
            f"This arc spans {abs(span)}°, which is a whole circle or more. Use "
            "catia_sketch_circle for a circle, or reduce the angles."
        )

    start_uv = _on_circle(centre, radius_mm, start_angle_deg)
    end_uv = _on_circle(centre, radius_mm, end_angle_deg)
    middle_uv = _on_circle(centre, radius_mm, start_angle_deg + span / 2.0)

    arc = symbol("GC_MakeArcOfCircle")(
        point_on(sketch, start_uv), point_on(sketch, middle_uv), point_on(sketch, end_uv)
    )
    return symbol("BRepBuilderAPI_MakeEdge")(arc.Value()).Edge(), start_uv, end_uv


def arc_through_edge(
    sketch: Sketch,
    start: tuple[float, float],
    through: tuple[float, float],
    end: tuple[float, float],
) -> Any:
    """A circular arc through three sketch-local points, in order."""
    require()
    try:
        arc = symbol("GC_MakeArcOfCircle")(
            point_on(sketch, start), point_on(sketch, through), point_on(sketch, end)
        )
        return symbol("BRepBuilderAPI_MakeEdge")(arc.Value()).Edge()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"No arc passes through those three points: {exc}. They may be collinear, "
            "in which case the shape wanted is a line, or two of them may coincide."
        ) from exc


def spline_edge(sketch: Sketch, through: list[tuple[float, float]], *, closed: bool) -> Any:
    """A smooth B-spline through sketch-local points, in order."""
    require()
    if len(through) < 2:
        raise GeometryError(f"A spline needs at least two points; got {len(through)}.")

    corners = list(through)
    if closed and not _same_point(corners[0], corners[-1]):
        corners.append(corners[0])

    array = symbol("TColgp_Array1OfPnt")(1, len(corners))
    for index, uv in enumerate(corners, start=1):
        array.SetValue(index, point_on(sketch, uv))

    try:
        curve = symbol("GeomAPI_PointsToBSpline")(array).Curve()
        return symbol("BRepBuilderAPI_MakeEdge")(curve).Edge()
    except Exception as exc:  # noqa: BLE001 - OCCT's Standard_Failure hierarchy
        raise GeometryError(
            f"No spline could be fitted through those points: {exc}. Check that no two "
            "consecutive points are the same."
        ) from exc


def ellipse_wire(
    sketch: Sketch,
    centre: tuple[float, float],
    major_radius_mm: float,
    minor_radius_mm: float,
    rotation_deg: float,
) -> Any:
    """A closed elliptical wire on the sketch plane.

    OCCT requires the major radius to be the larger of the two and gives an unhelpful
    error when it is not, so the case is caught here and named: an ellipse whose minor
    axis is longer is the same ellipse turned through 90°, which is almost always a
    swapped pair of arguments rather than what the author meant.
    """
    require()
    if major_radius_mm <= 0 or minor_radius_mm <= 0:
        raise GeometryError(
            f"An ellipse needs two positive radii; got {major_radius_mm} and "
            f"{minor_radius_mm}."
        )
    if minor_radius_mm > major_radius_mm:
        raise GeometryError(
            f"The minor radius ({minor_radius_mm} mm) is larger than the major "
            f"({major_radius_mm} mm). Swap them, and add 90 to rotation_deg if the "
            "orientation was deliberate."
        )

    frame = sketch.frame()
    local_x = _rotated_direction(frame, rotation_deg)
    axis = symbol("gp_Ax2")(point_on(sketch, centre), frame.Direction(), local_x)
    ellipse = symbol("gp_Elips")(axis, major_radius_mm, minor_radius_mm)
    maker = symbol("BRepBuilderAPI_MakeWire")()
    maker.Add(symbol("BRepBuilderAPI_MakeEdge")(ellipse).Edge())
    return maker.Wire()


# -- chaining helpers ---------------------------------------------------------

#: Below this an arc's two angles are the same angle and the arc has no length.
_MIN_ARC_SPAN_DEG: Final = 1e-9


def _same_point(first: tuple[float, float], second: tuple[float, float]) -> bool:
    return math.hypot(first[0] - second[0], first[1] - second[1]) <= JOIN_TOLERANCE_MM


def _on_circle(
    centre: tuple[float, float], radius: float, angle_deg: float
) -> tuple[float, float]:
    radians = math.radians(angle_deg)
    return (centre[0] + radius * math.cos(radians), centre[1] + radius * math.sin(radians))


def _rotated_direction(frame: Any, degrees: float) -> Any:
    """The sketch's local X axis, turned in the sketch plane."""
    x_axis, y_axis = frame.XDirection(), frame.YDirection()
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    return symbol("gp_Dir")(
        x_axis.X() * cos + y_axis.X() * sin,
        x_axis.Y() * cos + y_axis.Y() * sin,
        x_axis.Z() * cos + y_axis.Z() * sin,
    )


def _wire_of(edge: Any) -> Any:
    maker = symbol("BRepBuilderAPI_MakeWire")()
    maker.Add(edge)
    return maker.Wire()


def _extended_wire(wire: Any, edge: Any) -> Any:
    """The same run with one more segment on its end."""
    maker = symbol("BRepBuilderAPI_MakeWire")(wire, edge)
    if not maker.IsDone():
        raise GeometryError(
            "That segment could not be joined onto the run being drawn, although its "
            "start matches the run's end. The two may lie on different planes."
        )
    return maker.Wire()


__all__ = [
    "JOIN_TOLERANCE_MM",
    "MIN_SEGMENT_MM",
    "OpenCurve",
    "Sketch",
    "arc_edge",
    "arc_through_edge",
    "circle_wire",
    "closed_wire",
    "ellipse_wire",
    "frame_of",
    "line_edge",
    "plane_of",
    "point_on",
    "polygon_corners",
    "rectangle_corners",
    "rotate_uv",
    "slot_wire",
    "spline_edge",
]
