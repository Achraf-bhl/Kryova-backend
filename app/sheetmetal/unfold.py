"""The folded part, and its flat pattern.

Phase 17.3. A sheet-metal part here is a **tree of rectangular flanges joined by
straight cylindrical bends**, and `unfold()` turns one into the blank you cut:
an outline, a bend line per bend with its direction and angle, and every hole
mapped into blank coordinates.

**What can be unfolded, exactly.**

* A chain — flange, bend, flange, bend, flange. The classic profile part, and
  the case where `flat_length_mm` is reported and equals
  `sum(declared lengths) - sum(bend deductions)`.
* A tree — several flanges off one base, and flanges off those. An enclosure is
  four flanges off a base; that is M3 on the mission ladder and it is why this
  workstream was pulled forward.
* Holes on any flat face, carried into the blank through the same arithmetic
  that places the face.

**What cannot be, and how each is prevented rather than mishandled.**

* **Non-developable surfaces** — anything stretch-formed, drawn, or curved in
  two directions at once. A flat pattern of one does not exist: flattening it
  needs a forming simulation, not arithmetic. Nothing in this module can
  *express* one, which is the strongest form of refusing it.
* **Conical and variable-radius bends.** `Bend` carries one radius about one
  straight axis. A lofted flange cannot be declared, so it cannot be silently
  unfolded wrongly.
* **Non-rectangular flanges.** `Flange` is a rectangle. A trapezoidal or
  profiled flange would unfold correctly by the same arithmetic — the length
  along the bend does not change — but its *outline* would not be a rectangle,
  and emitting a rectangle for it is exactly the silent mis-handling this
  module refuses to do.
* **Two flanges off one edge.** One joint per edge; a second is refused by
  name. Two tabs on one edge is a real thing and would need per-span setbacks,
  which the rectangle model cannot carry exactly.
* **A flat pattern that overlaps itself.** Two faces landing on the same patch
  of blank cannot be cut. Refused, naming both, because the alternative is a
  drawing that looks right.
* **Legs shorter than their own setbacks.** A flange whose declared length is
  consumed by the two bends at its ends has no flat portion at all: the bends
  run into each other. Refused with the length that would be needed.
* **A 180 degree bend dimensioned to a mould line.** `bend.py` refuses the
  setback; the message says to declare those flanges tangent-to-tangent.

**How the placement works.** Every face gets a frame — an origin and a `u`/`v`
pair of unit axes — where `u` runs away from the bend that attaches it to its
parent and `v` runs along that bend. Because a child attaches to one of four
edges of a rectangle, every child frame is its parent's rotated by a multiple of
90 degrees, so **every rectangle in the blank stays axis-aligned**. That is what
makes overlap detection exact interval arithmetic rather than polygon
intersection, and it is why the outline can be traced from a grid rather than
unioned with a general polygon library.

**The flat length is cross-checked against itself.** For a chain it is computed
as `sum(lengths) - sum(deductions)` and, independently, as
`sum(tangent lengths) + sum(bend allowances)`. Those are algebraically the same
statement (`BD = 2*SB - BA`), so a disagreement means a coding error or two
conventions mixed in one part, and it raises rather than being reported. It is
one subtraction on the one number this package exists to produce.

**Interface for `app/manufacture/`.** This module does not import that package
and nothing here reaches into it. What it offers, for a later flat-pattern
drawing, is: `FlatPattern.outline` and `FlatPattern.regions` as closed
polylines, `FlatPattern.bend_lines` (centre line, zone rectangle, direction,
angle, radius, allowance), `FlatPattern.holes` (centre and diameter), and
`FlatPattern.extent_mm`. `Polyline` below is structurally the same alias
`app.manufacture.drawing` declares — `tuple[tuple[float, float], ...]`,
millimetres — so a drawing module can consume these directly without a
conversion, and the two are kept apart only so that sheet metal does not depend
on drawings.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.sheetmetal.bend import Bend, BendDirection, LengthConvention
from app.sheetmetal.errors import UnfoldError
from app.sheetmetal.kfactor import KFactor
from app.sheetmetal.material import SheetMaterial

#: A run of points in blank millimetres. Structurally identical to
#: `app.manufacture.drawing.Polyline` on purpose; see the module docstring.
Polyline = tuple[tuple[float, float], ...]

Vector = tuple[float, float]

#: Two rectangles overlapping by less than this in either axis are touching, not
#: overlapping. Faces and their bend zones abut exactly, so a bare `>` on
#: floating-point extents would report every adjacency as a collision.
TOUCH_TOLERANCE_MM: Final = 1e-9

#: How far the two ways of computing a chain's flat length may differ before it
#: is treated as a defect rather than as rounding, relative to the length.
_CROSS_CHECK_TOLERANCE: Final = 1e-9


class Edge(StrEnum):
    """Which side of a rectangular flange something attaches to.

    Named in the flange's own frame: `NEAR` is the edge at `u = 0`, which for
    every flange except the root is the bend back to its parent. `FAR` is the
    opposite edge, `LEFT` is `v = 0` and `RIGHT` is `v = width`.
    """

    NEAR = "near"
    FAR = "far"
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class Hole:
    """A hole on a flat face, positioned in the face's own declared coordinates.

    `u_mm` is measured from the face's `NEAR` edge and `v_mm` from its `LEFT`
    edge, **both in the part's length convention** — the same origin the
    flange's own `length_mm` and `width_mm` are measured from. Any other choice
    would need the reader to know where the tangent lines fell before they could
    place a hole, which is the wrong way round.
    """

    name: str
    diameter_mm: float
    u_mm: float
    v_mm: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.diameter_mm) or self.diameter_mm <= 0.0:
            raise UnfoldError(
                f"Hole {self.name!r} has a diameter of {self.diameter_mm!r} mm, which is "
                f"not a hole. Give the finished diameter in millimetres."
            )


@dataclass(frozen=True)
class Joint:
    """A bend, the edge of the parent it sits on, and the flange it carries.

    `offset_mm` positions a flange narrower than the edge it is bent from,
    measured along that edge **from the tangent line at the edge's start** —
    `LEFT` for a joint on `NEAR`/`FAR`, `NEAR` for one on `LEFT`/`RIGHT`. It is
    in tangent coordinates rather than mould-line ones because the span it has
    to fit inside is the parent's tangent extent, and expressing a position in
    one frame against a limit in another is how an off-by-a-setback happens.
    """

    edge: Edge
    bend: Bend
    flange: Flange
    offset_mm: float = 0.0


@dataclass(frozen=True)
class Flange:
    """One flat rectangular face of the part.

    `width_mm` may be `None` on any flange that is not the root, and that means
    *span the parent's edge exactly*. It is the ergonomic half of a real
    constraint: a flange flush with a base that itself has bends on its other
    edges cannot be as wide as the base's mould-line dimension, because the
    corners belong to the adjacent bend zones. Making the caller subtract two
    setbacks by hand to say "flush" is how a box gets declared with a corner
    overlap it did not mean.
    """

    name: str
    length_mm: float
    width_mm: float | None = None
    holes: tuple[Hole, ...] = ()
    joints: tuple[Joint, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise UnfoldError(
                "Every flange needs a name. It is what the bend lines, the holes and "
                "every refusal in this package are reported against."
            )
        if not math.isfinite(self.length_mm) or self.length_mm <= 0.0:
            raise UnfoldError(
                f"Flange {self.name!r} has a length of {self.length_mm!r} mm. A flange "
                f"is a rectangle and needs a positive length in millimetres."
            )
        if self.width_mm is not None and (
            not math.isfinite(self.width_mm) or self.width_mm <= 0.0
        ):
            raise UnfoldError(
                f"Flange {self.name!r} has a width of {self.width_mm!r} mm. Give a "
                f"positive width, or None to span the edge it is bent from."
            )
        seen: set[Edge] = set()
        for joint in self.joints:
            if joint.edge in seen:
                raise UnfoldError(
                    f"Flange {self.name!r} carries two bends on its {joint.edge} edge. "
                    f"This package places one flange per edge: two tabs off one edge need "
                    f"a setback per span, which a single rectangle cannot carry exactly. "
                    f"Split the flange, or model the second tab once partial flanges land."
                )
            seen.add(joint.edge)


@dataclass(frozen=True)
class SheetMetalPart:
    """A folded part: one sheet, one length convention, one tree of flanges.

    `convention` has no default. See `LengthConvention` — the three differ by a
    setback per bend end and a flange dimension carries nothing to say which was
    meant.
    """

    name: str
    material: SheetMaterial
    convention: LengthConvention
    root: Flange

    def __post_init__(self) -> None:
        names: set[str] = set()
        for flange, _parent, _joint in self.walk():
            if flange.name in names:
                raise UnfoldError(
                    f"Two flanges in {self.name!r} are called {flange.name!r}. Names "
                    f"identify faces on the flat pattern and in every finding, so they "
                    f"have to be unique."
                )
            names.add(flange.name)
        for flange, parent, _joint in self.walk():
            if parent is None:
                continue
            for joint in flange.joints:
                if joint.edge is Edge.NEAR:
                    raise UnfoldError(
                        f"Flange {flange.name!r} declares a bend on its NEAR edge, which "
                        f"is already the bend back to {parent.name!r}. Put the new flange "
                        f"on FAR, LEFT or RIGHT."
                    )

    def walk(self) -> Iterator[tuple[Flange, Flange | None, Joint | None]]:
        """Every flange, with the flange it hangs off and the joint that holds it.

        Depth first and deterministic in declaration order, so the flat pattern
        is the same run to run — the same property `app/render/` defends for its
        images and for the same reason.
        """
        stack: list[tuple[Flange, Flange | None, Joint | None]] = [(self.root, None, None)]
        while stack:
            flange, parent, joint = stack.pop()
            yield flange, parent, joint
            for child in reversed(flange.joints):
                stack.append((child.flange, flange, child))

    @property
    def bends(self) -> tuple[Bend, ...]:
        return tuple(
            joint.bend for _flange, _parent, joint in self.walk() if joint is not None
        )

    @property
    def k_factors(self) -> tuple[KFactor, ...]:
        return tuple(bend.k for bend in self.bends)

    @property
    def is_chain(self) -> bool:
        """Whether the part is a single run of flanges, each bent off the last.

        The case the `sum(lengths) - sum(deductions)` flat length describes.
        """
        for flange, _parent, _joint in self.walk():
            if len(flange.joints) > 1:
                return False
            if flange.joints and flange.joints[0].edge is not Edge.FAR:
                return False
        return True


@dataclass(frozen=True)
class _Frame:
    """Where a face sits in the blank, and which way its own axes point."""

    origin: Vector
    u_dir: Vector
    v_dir: Vector

    def at(self, u: float, v: float) -> Vector:
        return (
            self.origin[0] + u * self.u_dir[0] + v * self.v_dir[0],
            self.origin[1] + u * self.u_dir[1] + v * self.v_dir[1],
        )

    def rectangle(self, u0: float, v0: float, u1: float, v1: float) -> Polyline:
        corners = (
            self.at(u0, v0),
            self.at(u1, v0),
            self.at(u1, v1),
            self.at(u0, v1),
        )
        return (*corners, corners[0])


def _rot90(vector: Vector) -> Vector:
    return (-vector[1], vector[0])


def _negate(vector: Vector) -> Vector:
    return (-vector[0], -vector[1])


@dataclass(frozen=True)
class FlatFace:
    """One face as it lands on the blank."""

    name: str
    outline: Polyline
    tangent_length_mm: float
    tangent_width_mm: float
    origin_mm: Vector
    u_dir: Vector
    v_dir: Vector

    @property
    def area_mm2(self) -> float:
        return self.tangent_length_mm * self.tangent_width_mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tangent_length_mm": self.tangent_length_mm,
            "tangent_width_mm": self.tangent_width_mm,
            "origin_mm": list(self.origin_mm),
            "outline": [list(point) for point in self.outline],
        }


@dataclass(frozen=True)
class FlatBendLine:
    """A bend as it is marked on the blank.

    `start`/`end` is the centre line an operator lines up on the die; `zone` is
    the rectangle between the two tangent lines, which is the material that
    actually deforms and the region no hole may sit in.
    """

    name: str
    between: tuple[str, str]
    start: Vector
    end: Vector
    zone: Polyline
    angle_deg: float
    inside_radius_mm: float
    direction: BendDirection
    allowance_mm: float
    k: KFactor

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "between": list(self.between),
            "k": self.k.to_dict(),
            "start": list(self.start),
            "end": list(self.end),
            "angle_deg": self.angle_deg,
            "inside_radius_mm": self.inside_radius_mm,
            "direction": str(self.direction),
            "bend_allowance_mm": self.allowance_mm,
        }


@dataclass(frozen=True)
class FlatHole:
    """A hole in blank coordinates, with the face it belongs to."""

    name: str
    face: str
    diameter_mm: float
    centre_mm: Vector

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "face": self.face,
            "diameter_mm": self.diameter_mm,
            "centre_mm": list(self.centre_mm),
        }


@dataclass(frozen=True)
class FlatPattern:
    """The blank: what to cut, where to fold, and what is not certain about it.

    `provisional` is true when any bend's K-factor has no stated basis. It is
    not a footnote: the flat length is a linear function of K, so a blank cut
    from an assumed K is a blank whose length nobody has justified. The same
    rule `app/rules/engine.py` applies to a pass measured off a sampled bound.
    """

    part_name: str
    material: SheetMaterial
    convention: LengthConvention
    faces: tuple[FlatFace, ...]
    bend_lines: tuple[FlatBendLine, ...]
    holes: tuple[FlatHole, ...]
    regions: tuple[Polyline, ...]
    outline: tuple[Polyline, ...]
    extent_mm: tuple[float, float, float, float]
    blank_area_mm2: float
    flat_length_mm: float | None
    caveats: tuple[str, ...] = ()

    @property
    def blank_size_mm(self) -> Vector:
        """Width and height of the smallest rectangle the blank fits in."""
        low_x, low_y, high_x, high_y = self.extent_mm
        return (high_x - low_x, high_y - low_y)

    @property
    def assumed_k_factors(self) -> tuple[str, ...]:
        """The bends whose K-factor has no stated basis. Empty is what you want."""
        return tuple(line.name for line in self.bend_lines if not line.k.has_stated_basis)

    @property
    def provisional(self) -> bool:
        return bool(self.assumed_k_factors)

    def face_named(self, name: str) -> FlatFace:
        for face in self.faces:
            if face.name == name:
                return face
        known = ", ".join(f.name for f in self.faces)
        raise UnfoldError(f"No face called {name!r} in this flat pattern. It has {known}.")

    def explain(self) -> str:
        """The flat pattern in words, including what is not certain about it."""
        width, height = self.blank_size_mm
        lines = [
            f"{self.part_name}: blank {width:.3f} x {height:.3f} mm "
            f"in {self.material.name} {self.material.thickness_mm:g} mm, "
            f"{len(self.bend_lines)} bend(s), dimensioned to the {self.convention}.",
        ]
        if self.flat_length_mm is not None:
            lines.append(f"Flat length along the chain: {self.flat_length_mm:.3f} mm.")
        for line in self.bend_lines:
            lines.append(
                f"  {line.name}: {line.angle_deg:g} deg {line.direction} at "
                f"R{line.inside_radius_mm:g}, allowance {line.allowance_mm:.3f} mm."
            )
        if self.provisional:
            lines.append(
                "PROVISIONAL: the K-factor has no stated basis on "
                + ", ".join(self.assumed_k_factors)
                + ". The blank length is a linear function of K — get a test bend or a "
                "cited table before cutting."
            )
        lines.extend(f"  caveat: {c}" for c in self.caveats)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_name": self.part_name,
            "material": self.material.to_dict(),
            "convention": str(self.convention),
            "blank_size_mm": list(self.blank_size_mm),
            "extent_mm": list(self.extent_mm),
            "blank_area_mm2": self.blank_area_mm2,
            "flat_length_mm": self.flat_length_mm,
            "provisional": self.provisional,
            "assumed_k_factors": list(self.assumed_k_factors),
            "faces": [f.to_dict() for f in self.faces],
            "bend_lines": [b.to_dict() for b in self.bend_lines],
            "holes": [h.to_dict() for h in self.holes],
            "outline": [[list(p) for p in loop] for loop in self.outline],
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True)
class _Placed:
    """A face after its frame and its tangent extents are known."""

    flange: Flange
    frame: _Frame
    tangent_length_mm: float
    tangent_width_mm: float
    near_setback_mm: float
    left_setback_mm: float


def _setbacks(
    flange: Flange,
    *,
    parent_joint: Joint | None,
    thickness_mm: float,
    convention: LengthConvention,
) -> dict[Edge, float]:
    """The setback consumed at each of a flange's four edges."""
    out = dict.fromkeys(Edge, 0.0)
    if parent_joint is not None:
        out[Edge.NEAR] = parent_joint.bend.setback_mm(thickness_mm, convention)
    for joint in flange.joints:
        out[joint.edge] = joint.bend.setback_mm(thickness_mm, convention)
    return out


def _resolve_width(
    flange: Flange,
    *,
    parent: _Placed | None,
    parent_joint: Joint | None,
    side_setbacks_mm: float,
) -> float:
    """The flange's declared width, or the parent edge it was told to span.

    `None` means "span the parent's edge", and the parent's edge is a *tangent*
    extent while a declared width is a mould-line one. So the setbacks the
    flange's own side bends will take back out are added on here: the intent is
    that the resulting tangent width equals the parent's, not that it ends up
    two setbacks narrower.
    """
    if flange.width_mm is not None:
        return flange.width_mm
    if parent is None or parent_joint is None:
        raise UnfoldError(
            f"The root flange {flange.name!r} has no width. Only a flange bent off "
            f"another one may leave its width as None, meaning 'span that edge'."
        )
    if parent_joint.edge in (Edge.NEAR, Edge.FAR):
        available = parent.tangent_width_mm
    else:
        available = parent.tangent_length_mm
    return available - parent_joint.offset_mm + side_setbacks_mm


def _child_frame(
    parent: _Placed, joint: Joint, *, allowance_mm: float, child_width_mm: float
) -> _Frame:
    """The child's frame, one rotation of the parent's, offset by the bend allowance."""
    frame = parent.frame
    offset = joint.offset_mm
    if joint.edge is Edge.FAR:
        return _Frame(
            origin=frame.at(parent.tangent_length_mm + allowance_mm, offset),
            u_dir=frame.u_dir,
            v_dir=frame.v_dir,
        )
    if joint.edge is Edge.NEAR:
        return _Frame(
            origin=frame.at(-allowance_mm, offset + child_width_mm),
            u_dir=_negate(frame.u_dir),
            v_dir=_negate(frame.v_dir),
        )
    if joint.edge is Edge.LEFT:
        return _Frame(
            origin=frame.at(offset, -allowance_mm),
            u_dir=_negate(frame.v_dir),
            v_dir=frame.u_dir,
        )
    return _Frame(
        origin=frame.at(offset + child_width_mm, parent.tangent_width_mm + allowance_mm),
        u_dir=frame.v_dir,
        v_dir=_negate(frame.u_dir),
    )


def _zone_rectangle(
    parent: _Placed, joint: Joint, *, allowance_mm: float, child_width_mm: float
) -> Polyline:
    """The deforming strip between the two tangent lines, in the parent's frame."""
    frame = parent.frame
    lo = joint.offset_mm
    hi = joint.offset_mm + child_width_mm
    if joint.edge is Edge.FAR:
        u0, u1 = parent.tangent_length_mm, parent.tangent_length_mm + allowance_mm
        return frame.rectangle(u0, lo, u1, hi)
    if joint.edge is Edge.NEAR:
        return frame.rectangle(-allowance_mm, lo, 0.0, hi)
    if joint.edge is Edge.LEFT:
        return frame.rectangle(lo, -allowance_mm, hi, 0.0)
    v0, v1 = parent.tangent_width_mm, parent.tangent_width_mm + allowance_mm
    return frame.rectangle(lo, v0, hi, v1)


def _centre_line(zone: Polyline) -> tuple[Vector, Vector]:
    """The bend's centre line: the mid-line of the zone, along the bend axis.

    The zone is an axis-aligned rectangle, so the centre line runs along its
    longer-lived axis — the one the bend turns about, which is the direction the
    two tangent lines are parallel to.
    """
    xs = [p[0] for p in zone[:4]]
    ys = [p[1] for p in zone[:4]]
    low_x, high_x = min(xs), max(xs)
    low_y, high_y = min(ys), max(ys)
    if (high_x - low_x) >= (high_y - low_y):
        mid = (low_y + high_y) / 2.0
        return ((low_x, mid), (high_x, mid))
    mid = (low_x + high_x) / 2.0
    return ((mid, low_y), (mid, high_y))


def _bounds(rectangle: Polyline) -> tuple[float, float, float, float]:
    xs = [p[0] for p in rectangle]
    ys = [p[1] for p in rectangle]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return (
        min(a[2], b[2]) - max(a[0], b[0]) > TOUCH_TOLERANCE_MM
        and min(a[3], b[3]) - max(a[1], b[1]) > TOUCH_TOLERANCE_MM
    )


def _trace_outline(rectangles: list[tuple[float, float, float, float]]) -> tuple[Polyline, ...]:
    """The boundary of a union of axis-aligned rectangles, as closed loops.

    Every rectangle in a flat pattern is axis-aligned (see the module
    docstring), so the union can be resolved on the grid of their own edge
    coordinates: mark the covered cells, emit the cell sides that face an
    uncovered neighbour, and chain them. Outer loops come out counter-clockwise
    and any enclosed hole clockwise, which is the convention a DXF consumer
    expects.

    Written here rather than pulled from a geometry library because the whole
    package runs offline with no kernel — the same property `app/design/` keeps
    — and because a general polygon union on axis-aligned input is a much larger
    dependency for a much weaker guarantee.
    """
    if not rectangles:
        return ()
    xs = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
    ys = sorted({value for rect in rectangles for value in (rect[1], rect[3])})
    covered: set[tuple[int, int]] = set()
    for i in range(len(xs) - 1):
        cx = (xs[i] + xs[i + 1]) / 2.0
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2.0
            for rect in rectangles:
                if rect[0] < cx < rect[2] and rect[1] < cy < rect[3]:
                    covered.add((i, j))
                    break
    edges: dict[Vector, list[Vector]] = {}

    def emit(start: Vector, end: Vector) -> None:
        edges.setdefault(start, []).append(end)

    for i, j in sorted(covered):
        x0, x1 = xs[i], xs[i + 1]
        y0, y1 = ys[j], ys[j + 1]
        if (i, j - 1) not in covered:
            emit((x0, y0), (x1, y0))
        if (i + 1, j) not in covered:
            emit((x1, y0), (x1, y1))
        if (i, j + 1) not in covered:
            emit((x1, y1), (x0, y1))
        if (i - 1, j) not in covered:
            emit((x0, y1), (x0, y0))

    loops: list[Polyline] = []
    while edges:
        start = next(iter(sorted(edges)))
        loop = [start]
        current = start
        while True:
            following = edges.get(current)
            if not following:
                break
            nxt = following.pop()
            if not following:
                del edges[current]
            loop.append(nxt)
            current = nxt
            if current == start:
                break
        loops.append(_drop_collinear(tuple(loop)))
    return tuple(loops)


def _drop_collinear(loop: Polyline) -> Polyline:
    """Merge runs of points on one straight side into a single segment."""
    if len(loop) < 3:
        return loop
    closed = loop[0] == loop[-1]
    points = list(loop[:-1]) if closed else list(loop)
    kept: list[Vector] = []
    count = len(points)
    for index in range(count):
        before = points[(index - 1) % count]
        here = points[index]
        after = points[(index + 1) % count]
        cross = (here[0] - before[0]) * (after[1] - here[1]) - (here[1] - before[1]) * (
            after[0] - here[0]
        )
        if abs(cross) > TOUCH_TOLERANCE_MM:
            kept.append(here)
    if not kept:
        return loop
    return (*kept, kept[0]) if closed else tuple(kept)


def unfold(part: SheetMetalPart) -> FlatPattern:
    """Flatten a folded part into the blank that makes it.

    Raises `UnfoldError` for anything that cannot be flattened — a leg shorter
    than its own setbacks, a flange hanging off the edge it is bent from, a hole
    off its face, a blank that overlaps itself. Whether the part can be *formed*
    is a separate question with a separate answer: `formability.check_part`.
    """
    thickness = part.material.thickness_mm
    convention = part.convention
    placed: dict[str, _Placed] = {}
    faces: list[FlatFace] = []
    bend_lines: list[FlatBendLine] = []
    regions: list[Polyline] = []
    rectangles: list[tuple[float, float, float, float]] = []
    named_rectangles: list[tuple[str, tuple[float, float, float, float]]] = []

    for flange, parent_flange, joint in part.walk():
        parent = placed[parent_flange.name] if parent_flange is not None else None
        setbacks = _setbacks(
            flange, parent_joint=joint, thickness_mm=thickness, convention=convention
        )
        width = _resolve_width(
            flange,
            parent=parent,
            parent_joint=joint,
            side_setbacks_mm=setbacks[Edge.LEFT] + setbacks[Edge.RIGHT],
        )
        tangent_length = flange.length_mm - setbacks[Edge.NEAR] - setbacks[Edge.FAR]
        tangent_width = width - setbacks[Edge.LEFT] - setbacks[Edge.RIGHT]
        if tangent_length <= 0.0:
            raise UnfoldError(
                f"Flange {flange.name!r} is {flange.length_mm:g} mm to the {convention}, "
                f"and the bends at its ends consume "
                f"{setbacks[Edge.NEAR] + setbacks[Edge.FAR]:.3f} mm of that. There is no "
                f"flat portion left: the two bends run into each other. Lengthen it past "
                f"{setbacks[Edge.NEAR] + setbacks[Edge.FAR]:.3f} mm, open the radii, or "
                f"drop a bend."
            )
        if tangent_width <= 0.0:
            raise UnfoldError(
                f"Flange {flange.name!r} is {width:g} mm wide and the bends on its sides "
                f"consume {setbacks[Edge.LEFT] + setbacks[Edge.RIGHT]:.3f} mm of that, "
                f"leaving nothing flat between them. Widen it or open the radii."
            )
        if parent is None or joint is None:
            frame = _Frame(origin=(0.0, 0.0), u_dir=(1.0, 0.0), v_dir=(0.0, 1.0))
        else:
            available = (
                parent.tangent_width_mm
                if joint.edge in (Edge.NEAR, Edge.FAR)
                else parent.tangent_length_mm
            )
            if joint.offset_mm < -TOUCH_TOLERANCE_MM:
                raise UnfoldError(
                    f"Flange {flange.name!r} is offset {joint.offset_mm:g} mm along the "
                    f"{joint.edge} edge of {parent.flange.name!r}, which puts it off the "
                    f"start of that edge. Offsets run from the edge's start and are "
                    f"positive."
                )
            if joint.offset_mm + tangent_width > available + TOUCH_TOLERANCE_MM:
                raise UnfoldError(
                    f"Flange {flange.name!r} spans {tangent_width:.3f} mm from "
                    f"{joint.offset_mm:g} mm along the {joint.edge} edge of "
                    f"{parent.flange.name!r}, whose flat extent there is only "
                    f"{available:.3f} mm. A flange cannot be bent off material that is "
                    f"already in another bend — relieve the corner and narrow it to "
                    f"{available - joint.offset_mm:.3f} mm, or leave its width as None "
                    f"to span the edge exactly."
                )
            allowance = joint.bend.allowance_mm(thickness)
            frame = _child_frame(
                parent, joint, allowance_mm=allowance, child_width_mm=tangent_width
            )
            zone = _zone_rectangle(
                parent, joint, allowance_mm=allowance, child_width_mm=tangent_width
            )
            start, end = _centre_line(zone)
            bend_lines.append(
                FlatBendLine(
                    name=joint.bend.label,
                    between=(parent.flange.name, flange.name),
                    start=start,
                    end=end,
                    zone=zone,
                    angle_deg=joint.bend.angle_deg,
                    inside_radius_mm=joint.bend.inside_radius_mm,
                    direction=joint.bend.direction,
                    allowance_mm=allowance,
                    k=joint.bend.k,
                )
            )
            regions.append(zone)
            bounds = _bounds(zone)
            rectangles.append(bounds)
            named_rectangles.append((f"bend {joint.bend.label}", bounds))

        entry = _Placed(
            flange=flange,
            frame=frame,
            tangent_length_mm=tangent_length,
            tangent_width_mm=tangent_width,
            near_setback_mm=setbacks[Edge.NEAR],
            left_setback_mm=setbacks[Edge.LEFT],
        )
        placed[flange.name] = entry
        outline = frame.rectangle(0.0, 0.0, tangent_length, tangent_width)
        faces.append(
            FlatFace(
                name=flange.name,
                outline=outline,
                tangent_length_mm=tangent_length,
                tangent_width_mm=tangent_width,
                origin_mm=frame.origin,
                u_dir=frame.u_dir,
                v_dir=frame.v_dir,
            )
        )
        regions.append(outline)
        bounds = _bounds(outline)
        rectangles.append(bounds)
        named_rectangles.append((f"flange {flange.name}", bounds))

    for index, (name_a, box_a) in enumerate(named_rectangles):
        for name_b, box_b in named_rectangles[index + 1 :]:
            if _overlap(box_a, box_b):
                raise UnfoldError(
                    f"The flat pattern of {part.name!r} overlaps itself: {name_a} and "
                    f"{name_b} land on the same material. A blank cannot be cut from "
                    f"this. Shorten a flange, offset one of them along its edge, or "
                    f"relieve the corner between them."
                )

    holes = _place_holes(part, placed, convention=convention)
    blank_outline = _trace_outline(rectangles)
    low_x = min(r[0] for r in rectangles)
    low_y = min(r[1] for r in rectangles)
    high_x = max(r[2] for r in rectangles)
    high_y = max(r[3] for r in rectangles)
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in rectangles)
    flat_length, caveats = _chain_flat_length(part, placed)

    return FlatPattern(
        part_name=part.name,
        material=part.material,
        convention=convention,
        faces=tuple(faces),
        bend_lines=tuple(bend_lines),
        holes=holes,
        regions=tuple(regions),
        outline=blank_outline,
        extent_mm=(low_x, low_y, high_x, high_y),
        blank_area_mm2=area,
        flat_length_mm=flat_length,
        caveats=caveats,
    )


def _place_holes(
    part: SheetMetalPart,
    placed: dict[str, _Placed],
    *,
    convention: LengthConvention,
) -> tuple[FlatHole, ...]:
    """Map every hole from its face's declared coordinates into the blank."""
    out: list[FlatHole] = []
    for flange, _parent, _joint in part.walk():
        entry = placed[flange.name]
        for hole in flange.holes:
            u_tangent = hole.u_mm - entry.near_setback_mm
            v_tangent = hole.v_mm - entry.left_setback_mm
            if not (
                -TOUCH_TOLERANCE_MM <= u_tangent <= entry.tangent_length_mm + TOUCH_TOLERANCE_MM
                and -TOUCH_TOLERANCE_MM <= v_tangent <= entry.tangent_width_mm
                + TOUCH_TOLERANCE_MM
            ):
                raise UnfoldError(
                    f"Hole {hole.name!r} sits at ({hole.u_mm:g}, {hole.v_mm:g}) mm on "
                    f"{flange.name!r}, which is off the flat part of that face — its flat "
                    f"portion runs 0 to {entry.tangent_length_mm:.3f} mm by 0 to "
                    f"{entry.tangent_width_mm:.3f} mm from the tangent lines "
                    f"({entry.near_setback_mm:.3f} and {entry.left_setback_mm:.3f} mm in "
                    f"from the {convention}). A hole in the bend zone is a hole that "
                    f"deforms; move it or drop the bend."
                )
            out.append(
                FlatHole(
                    name=hole.name,
                    face=flange.name,
                    diameter_mm=hole.diameter_mm,
                    centre_mm=entry.frame.at(u_tangent, v_tangent),
                )
            )
    return tuple(out)


def _chain_flat_length(
    part: SheetMetalPart, placed: dict[str, _Placed]
) -> tuple[float | None, tuple[str, ...]]:
    """`sum(lengths) - sum(deductions)`, cross-checked, for a chain part.

    Returns `None` and a caveat for a tree, where "the flat length" is not a
    single number — the blank has an extent in two directions and no chain to
    sum along. Reporting the longer side of the bounding box as a flat length
    would be a number that looks like the arithmetic and is not.
    """
    if not part.is_chain:
        return None, (
            "This part branches, so it has no single flat length: the blank has an "
            "extent in two directions. Use blank_size_mm, and read flat_length_mm as "
            "the chain quantity it is.",
        )
    thickness = part.material.thickness_mm
    lengths = 0.0
    deductions = 0.0
    tangents = 0.0
    allowances = 0.0
    for flange, _parent, joint in part.walk():
        lengths += flange.length_mm
        tangents += placed[flange.name].tangent_length_mm
        if joint is not None:
            deductions += joint.bend.deduction_mm(thickness, part.convention)
            allowances += joint.bend.allowance_mm(thickness)
    by_deduction = lengths - deductions
    by_tangent = tangents + allowances
    if abs(by_deduction - by_tangent) > _CROSS_CHECK_TOLERANCE * max(1.0, abs(by_deduction)):
        raise UnfoldError(
            f"The flat length of {part.name!r} comes out as {by_deduction:.6f} mm from "
            f"the bend deductions and {by_tangent:.6f} mm from the bend allowances. Those "
            f"are the same statement (BD = 2*SB - BA), so one of them is wrong. This is a "
            f"defect in the arithmetic or two length conventions mixed in one part, not "
            f"something to work around."
        )
    return by_deduction, ()


__all__ = [
    "TOUCH_TOLERANCE_MM",
    "Edge",
    "FlatBendLine",
    "FlatFace",
    "FlatHole",
    "FlatPattern",
    "Flange",
    "Hole",
    "Joint",
    "Polyline",
    "SheetMetalPart",
    "unfold",
]
