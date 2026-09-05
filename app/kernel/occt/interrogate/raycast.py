"""Firing a ray at a shape and asking what it hit.

The one piece of machinery two different analyses in this package need. Wall thickness
casts a ray *into* the material from a point on the surface and measures how far it
travels before coming out the other side. Undercut detection casts a ray *away* from the
material along the mould's pull direction and asks whether anything is in the way. Same
intersection, opposite questions, so it is written once.

**`gp_Lin` is a line, not a ray.** It extends infinitely in both directions, and
`BRepIntCurveSurface_Inter` returns hits on both halves. The parameter `W()` is signed —
negative behind the origin, positive ahead — so "forward only" is a filter this module
applies and every caller would otherwise have to remember. Forgetting it makes a ray cast
inward from the top of a part report the distance to something *above* it.

**The origin sits on the surface, so the origin is a hit.** A ray fired from a point on a
face intersects that face at `W ≈ 0`. Everything below `_SELF_HIT_MM` is therefore
discarded as the ray leaving its own starting surface. That threshold is the one number
here that could hide a real wall: a wall thinner than it would be skipped rather than
measured. It is set well under any thickness a machine shop would call a wall, and the
scan reports its own misses, so a part that vanishes into this gap shows up as misses
rather than as a confident wrong answer.

**Cost.** Each cast is a curve/surface intersection against the whole shape. That is the
expensive part of every scan in this package, and the reason sample counts are modest by
default and stated in the report rather than assumed.
"""

from __future__ import annotations

from typing import Any, Final

from app.kernel.occt.binding import symbol

#: Hits closer than this to the origin are the ray leaving the surface it started on,
#: not a wall. In millimetres.
_SELF_HIT_MM: Final = 1e-6

#: Tolerance handed to the intersector. OCCT's documented default for this class.
_INTERSECTION_TOLERANCE: Final = 1e-7

#: How far outside a face a visibility ray starts, in mm. Far enough to be clear of the
#: surface's own tolerance, small enough that nothing else can fit in the gap.
ESCAPE_OFFSET_MM: Final = 1e-4


def _line(origin: tuple[float, float, float], direction: tuple[float, float, float]) -> Any:
    return symbol("gp_Lin")(
        symbol("gp_Pnt")(*origin),
        symbol("gp_Dir")(*direction),
    )


def forward_hit_distances(
    shape: Any,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    *,
    minimum_mm: float = _SELF_HIT_MM,
) -> list[float]:
    """Distances to every intersection ahead of the origin, nearest first.

    `direction` must be unit length; it becomes a `gp_Dir`, which normalises but refuses
    a zero vector. Callers get their directions from
    `app.kernel.interrogation.unit_vector`, which refuses zero with a message about what
    a pull direction is for.
    """
    intersector = symbol("BRepIntCurveSurface_Inter")()
    intersector.Init(shape, _line(origin, direction), _INTERSECTION_TOLERANCE)

    distances: list[float] = []
    while intersector.More():
        parameter = float(intersector.W())
        if parameter > minimum_mm:
            distances.append(parameter)
        intersector.Next()

    distances.sort()
    return distances


def first_hit_distance(
    shape: Any,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    *,
    minimum_mm: float = _SELF_HIT_MM,
) -> float | None:
    """How far to the nearest thing ahead, or None if the ray leaves without hitting.

    None is the honest answer for "nothing there" and is what separates a thin wall from
    an open one. A caller that treated None as zero would report a wall of zero thickness
    everywhere the part is open, which is both wrong and alarming.
    """
    distances = forward_hit_distances(shape, origin, direction, minimum_mm=minimum_mm)
    return distances[0] if distances else None


def is_blocked(
    shape: Any,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
) -> bool:
    """Does the shape stand between this point and infinity in that direction?

    The demouldability primitive. The origin is expected to be already clear of the
    surface — see `escape_point` — because a point exactly on a face always "hits" that
    face and would report every face on the part as blocked.
    """
    return first_hit_distance(shape, origin, direction) is not None


def escape_point(
    point: tuple[float, float, float],
    normal: tuple[float, float, float],
    *,
    offset_mm: float = ESCAPE_OFFSET_MM,
) -> tuple[float, float, float]:
    """The same point, nudged just off the surface along its outward normal.

    Needed by every visibility question. Without it the first thing a ray finds is the
    face it started from, and every face on the part reads as occluded.
    """
    return (
        point[0] + normal[0] * offset_mm,
        point[1] + normal[1] * offset_mm,
        point[2] + normal[2] * offset_mm,
    )


def opposite(direction: tuple[float, float, float]) -> tuple[float, float, float]:
    return (-direction[0], -direction[1], -direction[2])


__all__ = [
    "ESCAPE_OFFSET_MM",
    "escape_point",
    "first_hit_distance",
    "forward_hit_distances",
    "is_blocked",
    "opposite",
]
