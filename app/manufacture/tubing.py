"""A bent tube between waypoints: its bend table and its developed length -- master plan E17 task 3.

A tube is routed through **waypoints the caller chooses** (the intersection points of its
straight runs, as a tube bender's drawing gives them) and bent at each interior waypoint on
one centreline radius. From that the geometry is exact:

* the bend angle θ at a waypoint is the angle between the incoming and outgoing runs;
* each bend eats a tangent length T = R·tan(θ/2) off both runs it joins;
* the straight left between two bends is the run length minus both tangents, and a run
  shorter than its two tangents is refused, because the bends would overlap;
* the developed (cut) length is the straights plus the arcs R·θ.

The bend table is the **LRA** form a CNC bender reads: for each bend, the straight to feed
(L), the rotation of the tube about its own axis from the previous bend's plane (R), and the
bend angle (A). The rotation is signed, positive by the right-hand rule about the feed
direction, from the previous bend plane's normal to this one's.

**What is not here.** Springback, the bender's clamp and minimum straight between bends,
wall thinning and ovality are properties of a machine and a tube and are the caller's, as
limits with sources, where a check is wanted. Routing *around* obstacles is not searched:
the waypoints are the route, and whether it clears the rest of the machine is
`app/assembly/clash.py`'s question.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.manufacture.errors import ManufactureError
from app.rules.processes import Limit

Vec3 = tuple[float, float, float]

#: A bend shallower than this, in degrees, is a waypoint on a straight line.
STRAIGHT_THROUGH_DEG = 1e-6


class TubeError(ManufactureError):
    """A route that cannot be bent as described."""


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec3) -> Vec3:
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


@dataclass(frozen=True)
class Bend:
    at_waypoint: int
    #: Straight fed before this bend, mm.
    feed_mm: float
    #: Rotation about the tube axis from the previous bend's plane, degrees. 0 for the first.
    rotation_deg: float
    angle_deg: float
    arc_mm: float
    tangent_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "waypoint": self.at_waypoint,
            "L_mm": self.feed_mm,
            "R_deg": self.rotation_deg,
            "A_deg": self.angle_deg,
            "arc_mm": self.arc_mm,
            "tangent_mm": self.tangent_mm,
        }


@dataclass(frozen=True)
class TubeRoute:
    name: str
    waypoints_mm: tuple[Vec3, ...]
    bend_radius_mm: float
    outside_diameter_mm: float
    wall_mm: float

    def __post_init__(self) -> None:
        if not self.name.strip() or "." in self.name:
            raise TubeError(f"A tube needs a name without dots; got {self.name!r}.")
        if len(self.waypoints_mm) < 2:
            raise TubeError(f"{self.name}: a route needs at least a start and an end.")
        if self.bend_radius_mm <= 0.0:
            raise TubeError(f"{self.name}: a centreline bend radius is positive.")
        if not 0.0 < 2.0 * self.wall_mm < self.outside_diameter_mm:
            raise TubeError(
                f"{self.name}: a {self.wall_mm} mm wall does not fit a "
                f"{self.outside_diameter_mm} mm tube."
            )
        for i in range(len(self.waypoints_mm) - 1):
            if _norm(_sub(self.waypoints_mm[i + 1], self.waypoints_mm[i])) <= 0.0:
                raise TubeError(f"{self.name}: waypoints {i} and {i + 1} are the same point.")
        self._bends()  # refuses overlap, straight-through and reversal at construction

    def _directions(self) -> list[Vec3]:
        w = self.waypoints_mm
        return [_unit(_sub(w[i + 1], w[i])) for i in range(len(w) - 1)]

    def _angles(self) -> list[float]:
        d = self._directions()
        out: list[float] = []
        for i in range(len(d) - 1):
            cosine = max(-1.0, min(1.0, _dot(d[i], d[i + 1])))
            theta = math.acos(cosine)
            if math.degrees(theta) <= STRAIGHT_THROUGH_DEG:
                raise TubeError(
                    f"{self.name}: waypoint {i + 1} lies on a straight line and bends nothing. "
                    "Remove it."
                )
            if math.pi - theta <= 1e-9:
                raise TubeError(
                    f"{self.name}: the route doubles back on itself at waypoint {i + 1}; a "
                    "180 degree bend has no plane and needs two bends."
                )
            out.append(theta)
        return out

    def _bends(self) -> tuple[Bend, ...]:
        w, d, angles = self.waypoints_mm, self._directions(), self._angles()
        r = self.bend_radius_mm
        tangents = [r * math.tan(theta / 2.0) for theta in angles]
        bends: list[Bend] = []
        previous_normal: Vec3 | None = None
        for i, theta in enumerate(angles):
            run = _norm(_sub(w[i + 1], w[i]))
            before = tangents[i - 1] if i > 0 else 0.0
            straight = run - before - tangents[i]
            if straight < -1e-9:
                raise TubeError(
                    f"{self.name}: the run into waypoint {i + 1} is {run:g} mm and its bends "
                    f"need {before + tangents[i]:g} mm of it, so they overlap. Move the "
                    "waypoints apart or bend on a smaller radius."
                )
            normal = _unit(_cross(d[i], d[i + 1]))
            if previous_normal is None:
                rotation = 0.0
            else:
                rotation = math.degrees(
                    math.atan2(_dot(d[i], _cross(previous_normal, normal)), _dot(previous_normal, normal))
                )
            previous_normal = normal
            bends.append(
                Bend(
                    at_waypoint=i + 1,
                    feed_mm=max(straight, 0.0),
                    rotation_deg=rotation,
                    angle_deg=math.degrees(theta),
                    arc_mm=r * theta,
                    tangent_mm=tangents[i],
                )
            )
        last_run = _norm(_sub(w[-1], w[-2]))
        tail = last_run - (tangents[-1] if tangents else 0.0)
        if tail < -1e-9:
            raise TubeError(
                f"{self.name}: the last run is {last_run:g} mm and the bend before it needs "
                f"{tangents[-1]:g} mm of it."
            )
        return tuple(bends)

    @property
    def bends(self) -> tuple[Bend, ...]:
        return self._bends()

    @property
    def final_straight_mm(self) -> float:
        w = self.waypoints_mm
        last_run = _norm(_sub(w[-1], w[-2]))
        bends = self.bends
        return last_run - (bends[-1].tangent_mm if bends else 0.0)

    @property
    def developed_length_mm(self) -> float:
        return sum(b.feed_mm + b.arc_mm for b in self.bends) + self.final_straight_mm

    def check(
        self,
        *,
        minimum_bend_radius: Limit | None = None,
        minimum_straight_between_bends: Limit | None = None,
    ) -> AssertionReport:
        """The route against the bender's limits, each the caller's with its source."""
        from app.kernel import provenance

        payload: dict[str, Any] = {
            "tube": {
                self.name: {
                    "bend_radius_mm": self.bend_radius_mm,
                    "shortest_straight_mm": min(
                        [b.feed_mm for b in self.bends[1:]] or [math.inf]
                    ),
                }
            }
        }
        provenance.attach(
            payload, f"tube.{self.name}.shortest_straight_mm", provenance.measured("route geometry")
        )
        rules: list[Assertion] = []
        if minimum_bend_radius is not None:
            rules.append(
                Assertion(
                    name=f"{self.name}.bend_radius",
                    measure=f"tube.{self.name}.bend_radius_mm",
                    comparison=">=",
                    bound=minimum_bend_radius.value,
                    note=f"source: {minimum_bend_radius.source}",
                )
            )
        if minimum_straight_between_bends is not None and len(self.bends) > 1:
            rules.append(
                Assertion(
                    name=f"{self.name}.straight_between_bends",
                    measure=f"tube.{self.name}.shortest_straight_mm",
                    comparison=">=",
                    bound=minimum_straight_between_bends.value,
                    note=f"source: {minimum_straight_between_bends.source}",
                )
            )
        return check_assertions(rules, payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outside_diameter_mm": self.outside_diameter_mm,
            "wall_mm": self.wall_mm,
            "bend_radius_mm": self.bend_radius_mm,
            "bends": [b.to_dict() for b in self.bends],
            "final_straight_mm": self.final_straight_mm,
            "developed_length_mm": self.developed_length_mm,
            "not_computed": ["springback", "wall thinning and ovality", "clearance to the machine"],
        }


def route(
    name: str,
    waypoints_mm: Sequence[Vec3],
    *,
    bend_radius_mm: float,
    outside_diameter_mm: float,
    wall_mm: float,
) -> TubeRoute:
    return TubeRoute(
        name=name,
        waypoints_mm=tuple(tuple(float(c) for c in p) for p in waypoints_mm),  # type: ignore[misc]
        bend_radius_mm=bend_radius_mm,
        outside_diameter_mm=outside_diameter_mm,
        wall_mm=wall_mm,
    )


__all__ = ["Bend", "TubeError", "TubeRoute", "route"]
