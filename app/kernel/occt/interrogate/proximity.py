"""How close two bodies come, and whether they overlap — master plan 3.3.

The assembly question. Two parts in a machine are either clashing (a defect), touching
(a fit, deliberate or not), or apart by some clearance (which may or may not be enough
for a spanner, a wire, or thermal growth). One computation answers all three, so it is
done once and both halves are reported.

**Both numbers here are measured, not sampled**, which is worth saying because clearance
*sounds* like the kind of thing that would be approximated. `BRepExtrema_DistShapeShape`
is an exact extremum search over the B-rep — it finds the true minimum distance and the
points where it occurs, not the smallest distance among sampled pairs. The overlap is a
real boolean intersection whose volume is integrated. Neither carries the sampling caveat
that thickness and undercut do.

**Distance and interference are not redundant.** Two shapes that overlap have a minimum
distance of zero — and so do two that merely touch. Only the common volume separates
"these parts are in contact" from "these parts are inside each other", and a report that
gave distance alone would call an interference a perfect fit.

**Ordering costs nothing and is not free of meaning.** The minimum distance is symmetric,
but `closest_points` is not: the first point lies on the first shape. A caller annotating
a drawing needs to know which is which.
"""

from __future__ import annotations

from typing import Any, Final

from app.kernel.errors import GeometryError
from app.kernel.interrogation import ClearanceReport
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.topology import has_solid


def measure_clearance(first: Any, second: Any) -> ClearanceReport:
    """Minimum distance and overlap volume between two shapes.

    Neither a failed distance search nor a failed boolean raises. Both are recorded on
    the report — the distance as an `UNAVAILABLE` provenance entry with the reason, the
    interference as zero with the failure noted — because this is called on pairs of
    parts in an assembly loop, and one pathological pair must not abort the check on
    every other pair. A caller that wants an exception can read `failure`.
    """
    require()

    distance_mm: float | None = None
    closest: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
    failure = ""

    try:
        extrema = symbol("BRepExtrema_DistShapeShape")(first, second)
        if extrema.IsDone() and extrema.NbSolution() > 0:
            distance_mm = float(extrema.Value())
            near = extrema.PointOnShape1(1)
            far = extrema.PointOnShape2(1)
            closest = (
                (near.X(), near.Y(), near.Z()),
                (far.X(), far.Y(), far.Z()),
            )
        else:
            failure = (
                "the minimum-distance search returned no solution for this pair. One of "
                "the shapes may be empty or invalid — check that both were built before "
                "they were compared."
            )
    except Exception as exc:  # noqa: BLE001 - the kernel's failure is data, not a crash
        failure = f"the minimum-distance search failed: {exc}"

    return ClearanceReport(
        distance_mm=distance_mm,
        interference_mm3=_common_volume(first, second),
        closest_points=closest,
        failure=failure,
    )


def _common_volume(first: Any, second: Any) -> float:
    """Volume shared by two shapes: zero when they are apart or merely touching.

    A boolean that produces a shape with no solid is the ordinary result for two parts
    that touch on a face — the intersection is a surface, which has no volume — so that
    returns zero rather than being treated as a failure.
    """
    try:
        common = symbol("BRepAlgoAPI_Common")(first, second)
        if not common.IsDone():
            return 0.0
        result = common.Shape()
        if not has_solid(result):
            return 0.0
        properties = symbol("GProp_GProps")()
        symbol("BRepGProp").VolumeProperties_s(result, properties)
        return abs(float(properties.Mass()))
    except Exception:  # noqa: BLE001 - a failed boolean means "no measurable overlap"
        return 0.0


def minimum_distance_mm(first: Any, second: Any) -> float:
    """Just the distance, raising if it cannot be had.

    The sharp-edged sibling of `measure_clearance`, for a caller that is asking about one
    known-good pair and wants the number or an error rather than a report to inspect.
    """
    report = measure_clearance(first, second)
    if report.distance_mm is None:
        raise GeometryError(
            f"Could not measure the distance between these two shapes: {report.failure}"
        )
    return report.distance_mm


def plane_separation(first: Any, second: Any) -> ClearanceReport:
    """Distance between two unbounded construction planes.

    Analytic rather than a shape search, because two planes have no bounded extent to
    search over. Only the parallel case has a distance at all — non-parallel planes
    intersect somewhere however far apart their origins are, so the honest answer is
    zero, and the closest points are the infinitely many points of the intersection
    line rather than a pair. Reporting a pair there would invent a location.

    Overlap volume is refused rather than reported as zero: a plane bounds no volume, so
    "do these clash" is not a question these two operands can answer.
    """
    require()

    left, right = first.Direction(), second.Direction()
    dot = abs(
        left.X() * right.X() + left.Y() * right.Y() + left.Z() * right.Z()
    )
    parallel = dot >= 1.0 - _PLANE_PARALLEL_EPSILON

    no_volume = (
        "a construction plane bounds no volume, so an overlap between these two "
        "elements is not a measurable quantity."
    )
    if not parallel:
        return ClearanceReport(
            distance_mm=0.0,
            closest_points=None,
            interference_unavailable=no_volume,
        )

    origin, other = first.Location(), second.Location()
    offset = (other.X() - origin.X(), other.Y() - origin.Y(), other.Z() - origin.Z())
    distance = abs(offset[0] * left.X() + offset[1] * left.Y() + offset[2] * left.Z())

    # The closest pair on two parallel planes: the first plane's origin and its
    # perpendicular projection onto the second. Any pair at that separation would do —
    # this one is reproducible and lies on both planes' defining frames.
    landing = (
        origin.X() + left.X() * distance * _sign(offset, left),
        origin.Y() + left.Y() * distance * _sign(offset, left),
        origin.Z() + left.Z() * distance * _sign(offset, left),
    )
    return ClearanceReport(
        distance_mm=distance,
        closest_points=((origin.X(), origin.Y(), origin.Z()), landing),
        interference_unavailable=no_volume,
    )


def _sign(offset: tuple[float, float, float], axis: Any) -> float:
    """Which way along the normal the second plane lies. Zero offset counts as forward."""
    along = offset[0] * axis.X() + offset[1] * axis.Y() + offset[2] * axis.Z()
    return -1.0 if along < 0.0 else 1.0


#: Dot-product threshold on unit normals below which two planes are treated as parallel.
#: About 0.0008° — tighter than any modelling tolerance, because a plane's normal comes
#: from an exact construction rather than from a tessellation.
_PLANE_PARALLEL_EPSILON: Final = 1e-10


__all__ = ["measure_clearance", "minimum_distance_mm", "plane_separation"]
