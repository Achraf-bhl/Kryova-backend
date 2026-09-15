"""A measurement plan for a CMM, derived from the part's GD&T scheme -- master plan E17 task 5.

`app.rules.gdt` holds a part's tolerancing as data and refuses a scheme that makes no sense,
but it deliberately evaluates nothing: whether a real axis sits in a Ø0.2 zone is inspection,
and needs the part. This module is the step before that inspection: **what to measure, in what
order, with which points, against which datums.**

The order is the one the tolerancing itself imposes:

1. **Align.** The datum features are measured first, in precedence (primary, secondary,
   tertiary), because every oriented or located tolerance is evaluated in the frame they set up.
2. **Size first where a tolerance depends on it.** A frame at MMC or LMC gains bonus tolerance
   from the feature's as-produced size, so the size is measured before the geometric tolerance
   it modifies.
3. **Each feature control frame**, with the evaluation it needs named (a fitted plane's
   deviation range for flatness, the fitted axis's distance from true position for position),
   in the datum reference frame the frame names.

**Points.** Each feature carries its nominal geometry (a planar rectangle or a cylinder) and
the plan lays points on it. **How many is the caller's sampling strategy, with its source**
(a company standard, a customer requirement, ISO 10360 practice as a quality engineer reads
it): a count typed into this file would be somebody's habit with the product's authority.
What is fixed here is geometry. A plane is determined by 3 points and a cylinder by 5, so a
form tolerance measured at that minimum has a residual of zero by construction and would
pass any part; such a count is refused by name. A feature with no strategy is listed unset,
and a plan with anything unset or unresolved is not complete.

Points are laid cell-centred, so none sits on an edge a probe cannot reach: a rectangle as a
grid proportioned to its sides, a cylinder as rings proportioned to its length and
circumference. The probe approach is the surface normal into the material: along the
declared outward normal for a face, radially outward for a bore and inward for a boss.

Nothing here writes DMIS or drives a machine, and a planned point is not a measured one.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.manufacture.errors import ManufactureError
from app.rules.gdt import Category, Characteristic, FeatureControlFrame, MaterialCondition, Tolerancing
from app.rules.processes import Limit

Vec3 = tuple[float, float, float]


class InspectionError(ManufactureError):
    """A measurement plan that cannot be laid out as described."""


class Geometry(StrEnum):
    PLANE = "plane"
    CYLINDER = "cylinder"


#: Points that determine each geometry exactly: a plane's three, a cylinder's five
#: (four for its axis line, one for its radius).
DETERMINING_POINTS: Final[Mapping[Geometry, int]] = {Geometry.PLANE: 3, Geometry.CYLINDER: 5}

#: What a CMM evaluates for each characteristic, in words.
EVALUATION: Final[Mapping[Characteristic, str]] = {
    Characteristic.STRAIGHTNESS: "fit a line to each measured line element; report the widest deviation range",
    Characteristic.FLATNESS: "fit a plane; report the range of deviations from it",
    Characteristic.CIRCULARITY: "fit a circle to each ring; report the widest radial range",
    Characteristic.CYLINDRICITY: "fit a cylinder; report the radial range over all points",
    Characteristic.PROFILE_OF_A_LINE: "compare each line element to its nominal profile; report twice the largest deviation",
    Characteristic.PROFILE_OF_A_SURFACE: "compare every point to the nominal surface; report twice the largest deviation",
    Characteristic.ANGULARITY: "fit the feature; report the range of its points about the nominal angle to the datums",
    Characteristic.PERPENDICULARITY: "fit the feature; report the range of its points about a plane or axis square to the datums",
    Characteristic.PARALLELISM: "fit the feature; report the range of its points about a plane or axis parallel to the datums",
    Characteristic.POSITION: "fit the feature's axis or centre plane; report twice its distance from true position",
    Characteristic.CONCENTRICITY: "fit median points of opposed elements; report the diameter of the zone about the datum axis holding them",
    Characteristic.SYMMETRY: "fit median points of opposed elements; report the width of the zone about the datum centre plane holding them",
    Characteristic.CIRCULAR_RUNOUT: "at each ring, the range of radial deviation about the datum axis; a rotary indicator check is the direct method",
    Characteristic.TOTAL_RUNOUT: "the range of radial deviation over the whole surface about the datum axis; a rotary indicator check is the direct method",
}

_FORM: Final = frozenset(
    {
        Characteristic.STRAIGHTNESS,
        Characteristic.FLATNESS,
        Characteristic.CIRCULARITY,
        Characteristic.CYLINDRICITY,
    }
)


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Vec3) -> Vec3:
    n = math.sqrt(_dot(a, a))
    if n <= 0.0:
        raise InspectionError("A direction of zero length has no direction.")
    return (a[0] / n, a[1] / n, a[2] / n)


@dataclass(frozen=True)
class ProbePoint:
    at_mm: Vec3
    #: Unit vector the probe travels along to touch.
    approach: Vec3


@dataclass(frozen=True)
class PlaneFeature:
    """A planar face: a rectangle from `corner_mm` along `u` and `v`, outward normal u × v."""

    name: str
    corner_mm: Vec3
    u: Vec3
    v: Vec3
    u_length_mm: float
    v_length_mm: float

    geometry: Geometry = field(default=Geometry.PLANE, init=False)

    def __post_init__(self) -> None:
        if self.u_length_mm <= 0.0 or self.v_length_mm <= 0.0:
            raise InspectionError(f"{self.name}: a face needs positive side lengths.")
        if abs(_dot(_unit(self.u), _unit(self.v))) > 1e-9:
            raise InspectionError(f"{self.name}: u and v must be perpendicular.")

    def points(self, count: int) -> tuple[ProbePoint, ...]:
        u, v = _unit(self.u), _unit(self.v)
        normal = _cross(u, v)
        aspect = self.u_length_mm / self.v_length_mm
        columns = max(1, round(math.sqrt(count * aspect)))
        rows = math.ceil(count / columns)
        out: list[ProbePoint] = []
        for row in range(rows):
            for column in range(columns):
                if len(out) == count:
                    break
                su = (column + 0.5) / columns * self.u_length_mm
                sv = (row + 0.5) / rows * self.v_length_mm
                at = _add(self.corner_mm, _add(_scale(u, su), _scale(v, sv)))
                out.append(ProbePoint(at, _scale(normal, -1.0)))
        return tuple(out)


@dataclass(frozen=True)
class CylinderFeature:
    """A bore (`internal`) or a boss, from `base_mm` along `axis` for `length_mm`."""

    name: str
    base_mm: Vec3
    axis: Vec3
    radius_mm: float
    length_mm: float
    internal: bool

    geometry: Geometry = field(default=Geometry.CYLINDER, init=False)

    def __post_init__(self) -> None:
        if self.radius_mm <= 0.0 or self.length_mm <= 0.0:
            raise InspectionError(f"{self.name}: a cylinder needs a positive radius and length.")

    def points(self, count: int) -> tuple[ProbePoint, ...]:
        axis = _unit(self.axis)
        helper: Vec3 = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
        e1 = _unit(_cross(axis, helper))
        e2 = _cross(axis, e1)
        circumference = 2.0 * math.pi * self.radius_mm
        rings = max(2, round(math.sqrt(count * self.length_mm / circumference)))
        per_ring = max(3, math.ceil(count / rings))
        out: list[ProbePoint] = []
        for ring in range(rings):
            height = (ring + 0.5) / rings * self.length_mm
            offset = (ring % 2) * 0.5
            for k in range(per_ring):
                if len(out) == count:
                    break
                angle = 2.0 * math.pi * (k + offset) / per_ring
                radial = _add(_scale(e1, math.cos(angle)), _scale(e2, math.sin(angle)))
                at = _add(_add(self.base_mm, _scale(axis, height)), _scale(radial, self.radius_mm))
                approach = radial if self.internal else _scale(radial, -1.0)
                out.append(ProbePoint(at, approach))
        return tuple(out)


Feature = PlaneFeature | CylinderFeature


class StepKind(StrEnum):
    ALIGN = "align"
    SIZE = "size"
    TOLERANCE = "tolerance"


@dataclass(frozen=True)
class Step:
    kind: StepKind
    feature: str
    what: str
    datums: tuple[str, ...] = ()
    points: tuple[ProbePoint, ...] = ()
    sampling_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "feature": self.feature,
            "what": self.what,
            "datums": list(self.datums),
            "points": [{"at_mm": list(p.at_mm), "approach": list(p.approach)} for p in self.points],
            "sampling_source": self.sampling_source,
        }


@dataclass(frozen=True)
class InspectionPlan:
    steps: tuple[Step, ...]
    #: Features named by the tolerancing with no geometry given.
    unresolved: tuple[str, ...] = ()
    #: Features with geometry and no sampling strategy.
    unset: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return bool(self.steps) and not self.unresolved and not self.unset

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "steps": [s.to_dict() for s in self.steps],
            "unresolved": list(self.unresolved),
            "unset": list(self.unset),
            "notes": list(self.notes),
            "basis": "planned points on nominal geometry; nothing here is a measurement",
        }


def _strategy(
    feature: Feature, sampling: Mapping[str, Limit]
) -> Limit | None:
    return sampling.get(feature.name) or sampling.get(feature.geometry.value)


def _count(feature: Feature, limit: Limit, *, form: bool) -> int:
    count = int(limit.value)
    if count != limit.value or count < 1:
        raise InspectionError(f"{feature.name}: a point count is a whole number; got {limit.value}.")
    minimum = DETERMINING_POINTS[feature.geometry]
    if count < minimum:
        raise InspectionError(
            f"{feature.name}: {count} points cannot determine a {feature.geometry.value}, which "
            f"needs {minimum}."
        )
    if form and count <= minimum:
        raise InspectionError(
            f"{feature.name}: {count} points exactly determine a {feature.geometry.value}, so a "
            "form deviation measured on them is zero on any part. Give more than "
            f"{minimum}."
        )
    return count


def plan_inspection(
    tolerancing: Tolerancing,
    features: Mapping[str, Feature],
    sampling: Mapping[str, Limit],
) -> InspectionPlan:
    """The measurement plan for one part.

    `features` maps each feature name the tolerancing uses to its nominal geometry.
    `sampling` gives a point count, with its source, by feature name or by geometry
    (`"plane"`, `"cylinder"`); a name wins over a geometry.
    """
    for name, feature in features.items():
        if name != feature.name:
            raise InspectionError(f"The feature filed under {name!r} is called {feature.name!r}.")

    steps: list[Step] = []
    unresolved: list[str] = []
    unset: list[str] = []
    notes: list[str] = []

    def lay(feature_name: str, *, form: bool) -> tuple[tuple[ProbePoint, ...], str] | None:
        feature = features.get(feature_name)
        if feature is None:
            if feature_name not in unresolved:
                unresolved.append(feature_name)
            return None
        limit = _strategy(feature, sampling)
        if limit is None:
            if feature_name not in unset:
                unset.append(feature_name)
            return None
        return feature.points(_count(feature, limit, form=form)), limit.source

    for datum in tolerancing.scheme.datums:
        laid = lay(datum.feature, form=False)
        steps.append(
            Step(
                StepKind.ALIGN,
                datum.feature,
                f"establish datum {datum.letter}",
                points=laid[0] if laid else (),
                sampling_source=laid[1] if laid else "",
            )
        )
    if not tolerancing.scheme.datums:
        notes.append("no datum scheme: only form tolerances can be evaluated")

    measured_size: set[str] = set()
    for frame in tolerancing.frames:
        form = frame.characteristic in _FORM
        laid = lay(frame.feature, form=form)
        if _needs_size(frame) and frame.feature not in measured_size:
            measured_size.add(frame.feature)
            steps.append(
                Step(
                    StepKind.SIZE,
                    frame.feature,
                    "measure the as-produced size first: the tolerance below is at "
                    f"{frame.condition.value.upper()} and its bonus comes from it",
                    points=laid[0] if laid else (),
                    sampling_source=laid[1] if laid else "",
                )
            )
        steps.append(
            Step(
                StepKind.TOLERANCE,
                frame.feature,
                f"{frame.characteristic.value} {frame.tolerance_mm:g} mm: "
                f"{EVALUATION[frame.characteristic]}",
                datums=tuple(ref.letter for ref in frame.datums),
                points=laid[0] if laid else (),
                sampling_source=laid[1] if laid else "",
            )
        )
        if frame.category is Category.RUNOUT:
            notes.append(f"{frame.feature}: runout is defined by rotation about the datum axis")

    return InspectionPlan(
        steps=tuple(steps),
        unresolved=tuple(unresolved),
        unset=tuple(unset),
        notes=tuple(notes),
    )


def _needs_size(frame: FeatureControlFrame) -> bool:
    return frame.condition is not MaterialCondition.RFS or any(
        ref.condition is not MaterialCondition.RFS for ref in frame.datums
    )


__all__ = [
    "DETERMINING_POINTS",
    "EVALUATION",
    "CylinderFeature",
    "Geometry",
    "InspectionError",
    "InspectionPlan",
    "PlaneFeature",
    "ProbePoint",
    "Step",
    "StepKind",
    "plan_inspection",
]
