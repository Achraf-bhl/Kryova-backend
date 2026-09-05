"""Section cuts: looking at the inside of a part.

Phase 4.1 names section cuts alongside the eight canonical views, and the work
is almost entirely the vocabulary for saying *where* to cut — the drawing itself
is a boolean against a half-space and then the same projection everything else
uses.

**Where a cut goes is stated, never inferred.** A cut is defined by a plane and
by which side of it is thrown away, and the two are one fact here: the normal
points at the material that is removed. That is the rule `catia_split` already
states for which side of a cut survives, and having two different conventions in
one codebase for the same question is how a part ends up mirrored with every
test green.

**A section is drawn hatched.** Not decoration: without it a section view is
indistinguishable from an ordinary view of a differently-shaped part, both to a
person and to the vision model in Phase 4.2 — the outline of a cut block and the
outline of a solid block are the same outline. The hatch is what says "you are
looking at material that has been cut through", which is the entire content of
the picture.

    from app.render.section import mid_section, render_section

    shot = render_section(shape, mid_section(shape, "x"))
    shot.png

The cut shape itself is available (`cut`) for anything that wants to measure it
rather than look at it, though a sectioned solid is a picture rather than a
part: it is the original with a corner of the world subtracted, and its mass is
not a fact about anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from app.kernel.occt.binding import symbol
from app.kernel.occt.metrology import bounding_box_mm
from app.render import Render
from app.render.project import project
from app.render.raster import DEFAULT_HEIGHT, DEFAULT_WIDTH, hatch, rasterise, to_png
from app.render.views import CANONICAL_VIEWS, View, frame_for, view_named

Axis = Literal["x", "y", "z"]

#: How far past the part the removing box extends, as a fraction of the part's
#: own size. Generous, because a box that stops short of the geometry leaves a
#: sliver of material standing in front of the cut and the section reads as a
#: pocket. Fractional rather than absolute so it holds for a 5 mm bracket and a
#: 5 m gantry alike.
OVERSHOOT: Final = 0.6

#: Below this the part has no extent along the axis being cut, and a cut through
#: it removes everything or nothing. Refused rather than rendered blank.
MINIMUM_SPAN_MM: Final = 1e-9

#: How near a face's plane must be to the cutting plane to be *the* cut face and
#: get hatched, in mm. Loose enough for the boolean's own tolerance, tight enough
#: that a wall parallel to the cut and a millimetre behind it is not hatched.
ON_THE_PLANE_MM: Final = 1e-6

_AXES: Final[dict[str, tuple[float, float, float]]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

#: The three cuts a caller asks for without thinking: straight through the
#: middle, one per axis. Every other section is spelled out.
CANONICAL_SECTIONS: Final[tuple[str, ...]] = ("mid-x", "mid-y", "mid-z")


class SectionError(ValueError):
    """The cut could not be placed, or would not show anything."""


@dataclass(frozen=True)
class Section:
    """A cutting plane, and which side of it goes away.

    `normal` points at the material that is **removed**. Stating it that way
    round rather than "kept" is what makes `natural_view` work without a second
    argument: you look at a section from the side the material was taken off,
    because that is the only side the cut face is visible from.
    """

    name: str
    origin: tuple[float, float, float]
    normal: tuple[float, float, float]

    def natural_view(self) -> View:
        """The view that actually shows this cut.

        A section looked at from behind is an ordinary view of a part with a
        piece missing — every interesting face is pointing away. The camera
        wants to sit where the removed material was, so its direction is the
        opposite of the normal, and for an axis-aligned cut that is exactly one
        of the canonical views.

        Falls back to `iso` for an oblique plane, which is honest: no canonical
        view faces it, and a caller wanting one must say so.
        """
        for candidate in CANONICAL_VIEWS.values():
            if all(
                abs(a + b) < 1e-9 for a, b in zip(candidate.direction, self.normal, strict=True)
            ):
                return candidate
        return view_named("iso")


def mid_section(shape: Any, axis: Axis) -> Section:
    """Straight through the middle of the part, perpendicular to `axis`.

    The middle of the *bounding box*, which is not the centre of mass and is not
    trying to be: this is a way of looking at a part, and "half way along it" is
    what somebody asking for a mid-section means. Centre of mass would move the
    plane whenever a boss was added on one side, so the same request would give
    a different picture of an unchanged region.
    """
    bounds = _bounds(shape, axis)
    return _section_at(axis, (bounds[0] + bounds[1]) / 2.0, f"mid-{axis}")


def offset_section(shape: Any, axis: Axis, at_mm: float) -> Section:
    """A cut at a stated coordinate, refused when it misses the part.

    Refused rather than returned empty: a section plane outside the part renders
    as the whole part with nothing cut, which looks exactly like a successful
    section of a part with no internal features. Getting a picture back that
    means something entirely different from what was asked is worse than being
    told the number was wrong.
    """
    low, high = _bounds(shape, axis)
    if not low - MINIMUM_SPAN_MM <= at_mm <= high + MINIMUM_SPAN_MM:
        raise SectionError(
            f"A section at {axis}={at_mm:g} mm misses the part, which spans "
            f"{low:g} to {high:g} mm along {axis}. Pick a value inside that range, "
            f"or use mid_section for the middle of it."
        )
    return _section_at(axis, at_mm, f"{axis}={at_mm:g}")


def section_named(shape: Any, name: str) -> Section:
    """One of `CANONICAL_SECTIONS` by name, refusing an unknown one with the list."""
    wanted = name.strip().lower()
    if wanted in CANONICAL_SECTIONS:
        return mid_section(shape, wanted.split("-")[1])  # type: ignore[arg-type]
    known = ", ".join(CANONICAL_SECTIONS)
    raise SectionError(
        f"{name!r} is not a canonical section. Those are: {known}. For anything "
        f"else, place the plane yourself with offset_section."
    )


def _section_at(axis: Axis, at_mm: float, name: str) -> Section:
    normal = _AXES[axis]
    return Section(
        name=name,
        origin=tuple(at_mm * one for one in normal),  # type: ignore[arg-type]
        normal=normal,
    )


def _bounds(shape: Any, axis: Axis) -> tuple[float, float]:
    if axis not in _AXES:
        raise SectionError(f"{axis!r} is not an axis. Use 'x', 'y' or 'z'.")
    box = bounding_box_mm(shape)
    index = "xyz".index(axis)
    low, high = box["min"][index], box["max"][index]
    if high - low < MINIMUM_SPAN_MM:
        raise SectionError(
            f"The part has no extent along {axis} ({high - low:g} mm), so a cut "
            f"perpendicular to it removes all of it or none of it."
        )
    return low, high


def cut(shape: Any, section: Section) -> Any:
    """`shape` with everything on the normal's side of the plane removed.

    A finite box rather than `BRepPrimAPI_MakeHalfSpace`, and that is deliberate.
    A half-space is an infinite solid; OCCT's booleans accept one and their
    robustness against one is materially worse, with failures that show up as a
    silently unchanged result rather than an error — and an uncut part returned
    from a section call is exactly the wrong-picture failure this module is
    trying not to have. A box sized from the part's own bounding box is finite,
    covers it with `OVERSHOOT` to spare, and cuts identically.
    """
    box = bounding_box_mm(shape)
    size = box["size"]
    reach = max(max(size), 1.0) * (1.0 + OVERSHOOT)

    # A cube of side 2 * reach, placed so one face lies on the cutting plane and
    # the rest of it extends the way the normal points.
    centre = [
        section.origin[axis] + section.normal[axis] * reach
        # The two directions the plane spans are covered by the cube's own size;
        # only the normal direction needs placing, and the part's centre keeps
        # the box over the part rather than over the origin.
        + (0.0 if section.normal[axis] else (box["min"][axis] + box["max"][axis]) / 2.0)
        for axis in range(3)
    ]
    corner = symbol("gp_Pnt")(*(one - reach for one in centre))
    remover = symbol("BRepPrimAPI_MakeBox")(corner, 2.0 * reach, 2.0 * reach, 2.0 * reach).Shape()

    operation = symbol("BRepAlgoAPI_Cut")(shape, remover)
    operation.Build()
    if not operation.IsDone():
        raise SectionError(
            f"OCCT could not cut this part at {section.name}. The geometry may be "
            "invalid; run catia_analysis_part before sectioning it."
        )
    return operation.Shape()


def section_faces(shape: Any, section: Section) -> list[Any]:
    """The faces of a cut shape that lie in the cutting plane — what gets hatched.

    Found by geometry rather than by asking the boolean what it made: OCCT's
    history is per-operation and is lost the moment the shape is passed anywhere,
    whereas "this face is planar and lies in that plane" is a property of the
    result and survives everything. A face is on the plane when its own plane is
    parallel to the cut and its origin sits on it.
    """
    from app.kernel.occt import classify
    from app.kernel.occt.topology import FACE, explore

    found = []
    for found_face in explore(shape, FACE):
        # Face_s once, up front: `explore` answers in base TopoDS_Shape, and both
        # classify and the adaptor are overloaded on the concrete type.
        face = symbol("TopoDS").Face_s(found_face)
        if classify.face_surface_type(face) != "Plane":
            continue
        # Through the adaptor rather than `BRep_Tool.Surface_s` + a downcast:
        # the adaptor is what `classify` already uses for every other surface
        # question here, and it hands back a `gp_Pln` without a handle in sight.
        axis = symbol("BRepAdaptor_Surface")(face).Plane().Axis()
        direction = axis.Direction()
        parallel = abs(abs(_dot(direction, section.normal)) - 1.0) < 1e-9
        if not parallel:
            continue
        location = axis.Location()
        offset = _dot(
            (
                location.X() - section.origin[0],
                location.Y() - section.origin[1],
                location.Z() - section.origin[2],
            ),
            section.normal,
        )
        if abs(offset) <= ON_THE_PLANE_MM:
            found.append(face)
    return found


def _dot(a: Any, b: tuple[float, float, float]) -> float:
    if hasattr(a, "X"):
        return a.X() * b[0] + a.Y() * b[1] + a.Z() * b[2]
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def render_section(
    shape: Any,
    section: Section,
    view: str | View | None = None,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    hatched: bool = True,
) -> Render:
    """Cut the part and draw it, with the cut face hatched.

    The view defaults to the one that actually shows the cut
    (`Section.natural_view`), because a section rendered from the wrong side is
    a picture with nothing in it and looks like a working render.

    The frame is fitted to the **cut** shape, not the original. A section of the
    left half framed to the whole part would sit in the left half of the canvas
    with the right half blank, which reads as a part that is missing something
    rather than a part that has been cut.
    """
    camera = (
        section.natural_view()
        if view is None
        else (view if isinstance(view, View) else view_named(view))
    )
    sectioned = cut(shape, section)
    projection = project(sectioned, camera)
    frame = frame_for(projection.extent, width, height)
    canvas = rasterise(frame, projection.visible, projection.hidden)

    if hatched:
        # Hatched first, then the lines are redrawn over it: a hatch crossing an
        # edge would otherwise break the outline it runs into, and the outline is
        # what the eye reads the shape from.
        outlines = [
            one
            for face in section_faces(sectioned, section)
            for one in face_outlines(face, camera)
            if len(one) >= 3
        ]
        hatch(canvas, frame, outlines)
        canvas = rasterise(frame, projection.visible, projection.hidden, onto=canvas)

    return Render(
        view=camera.name,
        png=to_png(canvas),
        width=frame.width,
        height=frame.height,
        frame=frame,
        projection=projection,
    )


def face_outlines(face: Any, camera: View) -> list[tuple[tuple[float, float], ...]]:
    """A face's wires, in order, projected into view millimetres.

    **Not HLR, and that is the reason this function exists.** `project.py` gets
    an unordered pile of edges back, which is everything a line drawing needs and
    useless for a fill: a polygon is defined by which point follows which, and
    edges in arbitrary order describe no polygon at all. `BRepTools_WireExplorer`
    walks a wire in connection order, so each wire comes back as a closed ring.

    The projection is `View.to_view_mm` — the same linear map HLR applies, and
    the two are asserted to agree, which is the guard against this second
    implementation drifting from the first.
    """
    from app.kernel.occt import classify
    from app.kernel.occt.topology import WIRE, explore

    outlines: list[tuple[tuple[float, float], ...]] = []
    for wire in explore(face, WIRE):
        walker = symbol("BRepTools_WireExplorer")(symbol("TopoDS").Wire_s(wire))
        points: list[tuple[float, float]] = []
        while walker.More():
            edge = symbol("TopoDS").Edge_s(walker.Current())
            adaptor = symbol("BRepAdaptor_Curve")(edge)
            first, last = adaptor.FirstParameter(), adaptor.LastParameter()

            # **Which way to walk this edge is asked of the wire, not of the
            # edge.** An edge's own parameterisation says nothing about the
            # direction the wire uses it in — half the edges of an ordinary
            # rectangle are stored backwards — and walking one the wrong way
            # puts a diagonal across the polygon. `CurrentVertex` is the vertex
            # this edge *starts* at in wire order, so the near end is the start.
            start = symbol("BRep_Tool").Pnt_s(walker.CurrentVertex())
            backwards = _distance(adaptor.Value(last), start) < _distance(
                adaptor.Value(first), start
            )

            # A straight edge needs its two ends and nothing between; a curve is
            # sampled. Each edge contributes its start and interior only, because
            # its end is the next edge's start — appending both would double
            # every vertex and hand the hatcher a zero-length edge per corner.
            steps = 1 if classify.edge_curve_type(edge) == "Line" else 48
            for index in range(steps):
                fraction = index / steps
                at = adaptor.Value(
                    last + (first - last) * fraction
                    if backwards
                    else first + (last - first) * fraction
                )
                points.append(camera.to_view_mm(at.X(), at.Y(), at.Z()))
            walker.Next()
        if len(points) >= 3:
            outlines.append(tuple(points))
    return outlines


def _distance(a: Any, b: Any) -> float:
    return ((a.X() - b.X()) ** 2 + (a.Y() - b.Y()) ** 2 + (a.Z() - b.Z()) ** 2) ** 0.5


__all__ = [
    "CANONICAL_SECTIONS",
    "MINIMUM_SPAN_MM",
    "ON_THE_PLANE_MM",
    "OVERSHOOT",
    "Axis",
    "Section",
    "SectionError",
    "cut",
    "mid_section",
    "offset_section",
    "render_section",
    "section_faces",
    "section_named",
]
