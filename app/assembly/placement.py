"""Where a thing sits, composed down a chain of nested assemblies.

**There is no second transform type here, and that is the point.** `app.dynamics.pose`
already declares `Frame` — a row-major 3x3 rotation and an origin in mm — and the
mechanism sweep in `app.dynamics.clearance` measures clearance between two bodies at two
`Frame`s. An assembly places a bolt at a `Frame` and asks the same question. A second
placement type would mean a conversion at that seam, and a conversion is where a
mechanism's travel and an assembly's placement silently stop agreeing about which way
`+Z` points. So this module contains **compositions of `pose.Frame`, not a rival to it**.

What is genuinely missing from `pose` is frame *composition*: it has `compose` for two
rotations, and nothing that puts a child frame into its parent's parent. A product
structure is nested by construction — `frame/leg.2/bracket.1/bolt.3` is four placements
multiplied together — so the walk needs it on every occurrence. It lives here rather
than in `pose.py` because `pose.py` belongs to the dynamics package and this is the only
consumer; if a second one appears, this is a three-function move.

**Angles are radians, as in `pose`.** Degrees appear only where something is printed,
suffixed `_deg`. A package that spells one quantity two ways eventually multiplies by
the wrong one — and here that is a machine assembled 57.3 times over.

**A world box is conservative and must stay that way.** `Box.transformed` re-boxes the
eight rotated corners of an axis-aligned box, which is a *larger* box than the rotated
shape needs. That over-estimate is the only thing that makes the broad phase in
`clash.py` sound: a pair rejected because their boxes are far apart must really be far
apart. A tighter box that is occasionally wrong would let a clash through, and a clash
that is never looked at is indistinguishable from one that is not there.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.dynamics.pose import (
    IDENTITY,
    Frame,
    Rot3,
    Vec3,
    add,
    apply,
)
from app.dynamics.pose import (
    compose as compose_rotations,
)
from app.dynamics.pose import (
    from_axis_angle as _from_axis_angle,
)
from app.dynamics.pose import (
    sub as _sub,
)
from app.dynamics.pose import (
    transpose as _transpose,
)

#: The frame every product structure's root sits at. Named rather than spelled inline so
#: "the root is at the origin, unrotated" is a statement somebody can find and change.
WORLD: Frame = Frame(IDENTITY, (0.0, 0.0, 0.0))


def at(x_mm: float = 0.0, y_mm: float = 0.0, z_mm: float = 0.0) -> Frame:
    """A pure translation, in mm. The placement most instances actually have."""
    return Frame(IDENTITY, (float(x_mm), float(y_mm), float(z_mm)))


def turned(axis: Vec3, angle_rad: float, *, origin_mm: Vec3 = (0.0, 0.0, 0.0)) -> Frame:
    """A rotation of `angle_rad` about `axis` through `origin_mm`.

    The origin matters and is easy to leave out: a bracket rotated 90 degrees about the
    global Z is not the same bracket rotated 90 degrees about its own mounting boss, and
    the difference is a part hanging in space. Defaulting to the global origin is the
    conventional choice, and naming the argument is how the other one gets said.
    """
    rotation = _from_axis_angle(axis, angle_rad)
    # p' = R(p - o) + o, written as a frame: rotation R, origin o - R·o.
    shifted = _sub(origin_mm, apply(rotation, origin_mm))
    return Frame(rotation, shifted)


def compose(outer: Frame, inner: Frame) -> Frame:
    """`inner`, which is given relative to `outer`, expressed in `outer`'s parent.

    The one operation the occurrence walk performs, once per level of nesting. Read it
    as "apply inner first, then outer", the same order `pose.compose` uses for two
    rotations, so the two cannot drift apart in a reader's head.
    """
    return Frame(
        compose_rotations(outer.rotation, inner.rotation),
        add(outer.origin_mm, apply(outer.rotation, inner.origin_mm)),
    )


def chain(frames: Sequence[Frame]) -> Frame:
    """Compose a whole chain, outermost first. An empty chain is the world frame."""
    result = WORLD
    for frame in frames:
        result = compose(result, frame)
    return result


def invert(frame: Frame) -> Frame:
    """The frame that undoes `frame`.

    Exact for a rotation matrix, because the inverse of an orthonormal matrix is its
    transpose — no solve, no conditioning, no tolerance. `pose.is_rotation` is what says
    whether a frame that has been through a long chain still qualifies.
    """
    inverse = _transpose(frame.rotation)
    origin = apply(inverse, frame.origin_mm)
    return Frame(inverse, (-origin[0], -origin[1], -origin[2]))


def relative(reference: Frame, target: Frame) -> Frame:
    """`target` expressed in `reference`'s coordinates.

    What an interface contract measures against: "the bolt hole is 25 mm from the
    bracket's datum" is a claim about a relative frame, and stating it in world
    coordinates would make it stop being true the moment the sub-assembly moved.
    """
    return compose(invert(reference), target)


@dataclass(frozen=True)
class Box:
    """An axis-aligned bounding box in mm, in whatever frame the holder means.

    Deliberately not `app.kernel.metrology`'s bounding-box dict. That one is a
    *measurement payload* shape — `{"min": [...], "size": [...]}` — read by assertions
    through a path, and it has no arithmetic on it. This one is a value with arithmetic:
    it is transformed, unioned and asked for separations, thousands of times in a broad
    phase, and doing that through dict lookups on a payload would be both slower and a
    second place for the key names to be spelled. `from_payload` converts at the seam.
    """

    min_mm: Vec3
    max_mm: Vec3

    def __post_init__(self) -> None:
        for axis in range(3):
            if self.max_mm[axis] < self.min_mm[axis]:
                raise ValueError(
                    f"A bounding box's maximum ({self.max_mm}) is below its minimum "
                    f"({self.min_mm}) on axis {axis}. Give the corners as (min, max); a "
                    "box built the other way round reports every pair as separated, "
                    "which is a clash check that passes everything."
                )

    @classmethod
    def from_payload(cls, payload: dict[str, Sequence[float]]) -> Box:
        """From `app.kernel.occt.metrology.bounding_box_mm`'s `{"min", "size"}` form."""
        low = tuple(float(v) for v in payload["min"])
        size = tuple(float(v) for v in payload["size"])
        return cls(
            min_mm=(low[0], low[1], low[2]),
            max_mm=(low[0] + size[0], low[1] + size[1], low[2] + size[2]),
        )

    @property
    def centre_mm(self) -> Vec3:
        return tuple(  # type: ignore[return-value]
            (self.min_mm[i] + self.max_mm[i]) * 0.5 for i in range(3)
        )

    @property
    def size_mm(self) -> Vec3:
        return tuple(  # type: ignore[return-value]
            self.max_mm[i] - self.min_mm[i] for i in range(3)
        )

    def corners(self) -> tuple[Vec3, ...]:
        """The eight corners, in a fixed order so a transform is reproducible."""
        return tuple(
            (
                self.max_mm[0] if index & 1 else self.min_mm[0],
                self.max_mm[1] if index & 2 else self.min_mm[1],
                self.max_mm[2] if index & 4 else self.min_mm[2],
            )
            for index in range(8)
        )

    def transformed(self, frame: Frame) -> Box:
        """The axis-aligned box of this box's eight corners, moved by `frame`.

        **Conservative, never tight.** For a rotated box this is strictly larger than the
        rotated shape needs — up to sqrt(3) on a cube at 45 degrees — and that slack is
        the guarantee the broad phase rests on. Shrinking it would make `separation_mm`
        occasionally optimistic, and an optimistic broad phase silently drops the pair
        that clashes.
        """
        moved = [frame.point(corner) for corner in self.corners()]
        return Box(
            min_mm=(
                min(p[0] for p in moved),
                min(p[1] for p in moved),
                min(p[2] for p in moved),
            ),
            max_mm=(
                max(p[0] for p in moved),
                max(p[1] for p in moved),
                max(p[2] for p in moved),
            ),
        )

    def separation_mm(self, other: Box) -> float:
        """Shortest distance between the two boxes; zero when they overlap or touch.

        A **lower bound** on the distance between the shapes inside them, because each
        shape is contained in its box. That direction is what makes rejecting a pair
        sound: if the boxes are more than the threshold apart, the shapes certainly are.
        """
        gaps = [
            max(
                other.min_mm[axis] - self.max_mm[axis],
                self.min_mm[axis] - other.max_mm[axis],
                0.0,
            )
            for axis in range(3)
        ]
        return math.sqrt(gaps[0] ** 2 + gaps[1] ** 2 + gaps[2] ** 2)

    def union(self, other: Box) -> Box:
        return Box(
            min_mm=(
                min(self.min_mm[0], other.min_mm[0]),
                min(self.min_mm[1], other.min_mm[1]),
                min(self.min_mm[2], other.min_mm[2]),
            ),
            max_mm=(
                max(self.max_mm[0], other.max_mm[0]),
                max(self.max_mm[1], other.max_mm[1]),
                max(self.max_mm[2], other.max_mm[2]),
            ),
        )

    def contains(self, point_mm: Vec3, *, tolerance_mm: float = 0.0) -> bool:
        return all(
            self.min_mm[axis] - tolerance_mm <= point_mm[axis] <= self.max_mm[axis] + tolerance_mm
            for axis in range(3)
        )

    def to_payload(self) -> dict[str, list[float]]:
        """Back to the kernel's `{"min", "size"}` measurement shape."""
        return {
            "min": list(self.min_mm),
            "size": list(self.size_mm),
        }


__all__ = [
    "WORLD",
    "Box",
    "Frame",
    "Rot3",
    "Vec3",
    "at",
    "chain",
    "compose",
    "invert",
    "relative",
    "turned",
]
