"""Wireframe over COM: 3D curves, and the curves derived from other geometry.

The derived curves at the end are the ones that earn their place: a projection
or an intersection *follows* the geometry it was built from, so a trim line
stays on the panel when the panel changes shape. A curve of the same shape drawn
by hand does not, and the difference only shows up after someone edits the part.

The helix is the other reason this module exists separately from `sketcher`: it
does not lie in a plane, so no sketch can hold it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import (
    ComContext,
    append_and_name,
    direction_of,
    resolve_element,
    resolve_support,
)

logger = logging.getLogger("kryova.catia.com.wireframe")


class WireframeMixin:
    """3D curves, built directly and derived from other geometry."""

    def _curve(  # pragma: no cover - Windows only
        self: ComContext, element: Any, name: str, what: str
    ) -> str:
        part = self._part()
        created = append_and_name(part, element, name)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard(self._document(), part, element)
            raise CatiaOperationError(f"The {what} could not be built: {exc}") from exc
        return created

    def curve_circle(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        centre: str = "",
        radius_mm: float = 0.0,
        points: list[str] | None = None,
        support: str = "",
        start_angle_deg: float = 0.0,
        end_angle_deg: float = 360.0,
        name: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        plane = resolve_support(self, support) if support else None

        if kind == "three_points":
            if not points or len(points) != 3:
                raise CatiaOperationError(
                    "A three-point circle needs exactly three named points."
                )
            circle = factory.AddNewCircle3Points(
                *(resolve_element(part, point) for point in points)
            )
        elif kind == "centre_radius":
            if not centre:
                raise CatiaOperationError("A centre-radius circle needs a `centre` point.")
            if plane is None:
                raise CatiaOperationError(
                    "A 3D circle needs a `support` plane — without one there are "
                    "infinitely many circles through the same centre."
                )
            circle = factory.AddNewCircleCtrRadWithAngles(
                resolve_element(part, centre),
                plane,
                False,
                float(radius_mm),
                float(start_angle_deg),
                float(end_angle_deg),
            )
        elif kind == "centre_point":
            if not centre or not points:
                raise CatiaOperationError(
                    "A centre-point circle needs a `centre` and one point in `points`."
                )
            circle = factory.AddNewCircleCtrPt(
                resolve_element(part, centre), resolve_element(part, points[0]), plane, False
            )
        else:
            raise CatiaOperationError(
                f"The {kind!r} circle needs tangency references this bridge cannot form "
                "yet. Use 'centre_radius', 'centre_point' or 'three_points'."
            )
        return {"curve": self._curve(circle, name, "circle")}

    def curve_spline(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        points: list[str],
        start_tangent: list[float] | None = None,
        end_tangent: list[float] | None = None,
        support: str = "",
        closed: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        spline = part.HybridShapeFactory.AddNewSpline()
        for point in points:
            spline.AddPoint(resolve_element(part, point))
        if support:
            spline.SetSupport(resolve_element(part, support))
        if start_tangent is not None:
            spline.SetTangentAt(1, direction_of(part, start_tangent), False)
        if end_tangent is not None:
            spline.SetTangentAt(len(points), direction_of(part, end_tangent), False)
        if closed:
            try:
                spline.SetClosing(True)
            except Exception:  # noqa: BLE001 - not on every release
                pass
        return {"curve": self._curve(spline, name, "spline"), "points": len(points)}

    def curve_helix(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        axis: str,
        start_point: str,
        pitch_mm: float,
        height_mm: float,
        clockwise: bool = True,
        taper_deg: float = 0.0,
        start_angle_deg: float = 0.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        helix = part.HybridShapeFactory.AddNewHelix(
            resolve_element(part, axis),
            resolve_element(part, start_point),
            # (axis, start, inverted, pitch, height, clockwise, start angle,
            #  taper angle, taper outward) -- the order CATIA documents.
            False,
            float(pitch_mm),
            float(height_mm),
            bool(clockwise),
            float(start_angle_deg),
            float(taper_deg),
            False,
        )
        turns = float(height_mm) / float(pitch_mm) if pitch_mm else 0.0
        return {"curve": self._curve(helix, name, "helix"), "turns": round(turns, 3)}

    def curve_spiral(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        support: str,
        centre: str,
        start_radius_mm: float,
        pitch_mm: float = 0.0,
        end_radius_mm: float = 0.0,
        turns: int = 1,
        clockwise: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        plane = resolve_support(self, support)
        axis = factory.AddNewLineNormal(
            resolve_element(part, centre), plane, 0.0, 10.0, False
        )
        append_and_name(part, axis)
        spiral = factory.AddNewSpiral(
            plane,
            resolve_element(part, centre),
            axis,
            float(start_radius_mm),
            1 if clockwise else 0,
            float(turns) * 360.0,
            float(pitch_mm),
            float(end_radius_mm),
            0.0,
            0.0,
        )
        return {"curve": self._curve(spiral, name, "spiral")}

    def curve_polyline(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        points: list[str],
        radius_mm: float = 0.0,
        closed: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        polyline = part.HybridShapeFactory.AddNewPolyline()
        for index, point in enumerate(points, start=1):
            polyline.InsertElement(resolve_element(part, point), index)
            if radius_mm and 1 < index < len(points):
                polyline.SetRadius(index, float(radius_mm))
        if closed:
            polyline.Closure = True
        return {"curve": self._curve(polyline, name, "polyline")}

    def curve_corner(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        radius_mm: float,
        support: str = "",
        trim: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        if len(elements) != 2:
            raise CatiaOperationError(
                f"A corner rounds between exactly two curves, not {len(elements)}."
            )
        part = self._part()
        first, second = (resolve_element(part, element) for element in elements)
        corner = part.HybridShapeFactory.AddNewCorner(
            first,
            second,
            resolve_support(self, support) if support else None,
            float(radius_mm),
            1,
            1,
            bool(trim),
        )
        return {"curve": self._curve(corner, name, "corner")}

    def curve_connect(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        first_curve: str,
        second_curve: str,
        continuity: str = "tangent",
        first_tension: float = 1.0,
        second_tension: float = 1.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        mode = {"point": 0, "tangent": 1, "curvature": 2}.get(continuity, 1)
        connect = part.HybridShapeFactory.AddNewConnect(
            resolve_element(part, first_curve),
            None,
            0,
            mode,
            float(first_tension),
            resolve_element(part, second_curve),
            None,
            0,
            mode,
            float(second_tension),
            False,
        )
        return {"curve": self._curve(connect, name, "connect curve")}

    # -- derived, associative ------------------------------------------------

    def curve_project(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        element: str,
        support: str,
        direction: list[float] | None = None,
        nearest: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        projection = part.HybridShapeFactory.AddNewProject(
            resolve_element(part, element), resolve_element(part, support)
        )
        try:
            if direction is not None:
                projection.Direction = direction_of(part, direction)
                projection.NormalProjection = False
            projection.SolutionType = 0 if nearest else 1
        except Exception:  # noqa: BLE001 - optional refinements
            logger.debug("Projection options not applied", exc_info=True)
        return {"curve": self._curve(projection, name, "projection")}

    def curve_intersect(  # pragma: no cover - Windows only
        self: ComContext, *, elements: list[str], extend: bool = False, name: str = ""
    ) -> dict[str, Any]:
        if len(elements) != 2:
            raise CatiaOperationError(
                f"An intersection takes exactly two elements, not {len(elements)}."
            )
        part = self._part()
        first, second = (resolve_element(part, element) for element in elements)
        intersection = part.HybridShapeFactory.AddNewIntersection(first, second)
        try:
            intersection.ExtendedIntersection = bool(extend)
        except Exception:  # noqa: BLE001
            pass
        try:
            return {"result": self._curve(intersection, name, "intersection")}
        except CatiaOperationError as exc:
            raise CatiaOperationError(
                f"{exc} Two elements that do not actually meet have no intersection — "
                "set extend to true, or check they overlap."
            ) from exc

    def curve_combine(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        first_curve: str,
        second_curve: str,
        first_direction: list[float] | None = None,
        second_direction: list[float] | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        combine = part.HybridShapeFactory.AddNewCombine(
            resolve_element(part, first_curve),
            resolve_element(part, second_curve),
            False,
            False,
        )
        if first_direction is not None:
            combine.SetDirection(1, direction_of(part, first_direction))
        if second_direction is not None:
            combine.SetDirection(2, direction_of(part, second_direction))
        return {"curve": self._curve(combine, name, "combined curve")}

    def curve_parallel(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        curve: str,
        support: str,
        distance_mm: float,
        reversed: bool = False,  # noqa: A002
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        parallel = part.HybridShapeFactory.AddNewCurvePar(
            resolve_element(part, curve),
            resolve_element(part, support),
            float(distance_mm),
            1 if reversed else 0,
            0,
            False,
        )
        return {"curve": self._curve(parallel, name, "parallel curve")}

    def curve_offset_3d(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        curve: str,
        distance_mm: float,
        direction: list[float],
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        offset = part.HybridShapeFactory.AddNew3DCurveOffset(
            resolve_element(part, curve),
            direction_of(part, direction),
            float(distance_mm),
        )
        return {"curve": self._curve(offset, name, "3D curve offset")}

    def curve_section(  # pragma: no cover - Windows only
        self: ComContext, *, element: str, plane: str, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        section = part.HybridShapeFactory.AddNewIntersection(
            resolve_element(part, element), resolve_support(self, plane)
        )
        return {"curve": self._curve(section, name, "section")}

    def curve_extremum(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        element: str,
        direction: list[float],
        second_direction: list[float] | None = None,
        maximum: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        extremum = part.HybridShapeFactory.AddNewExtremum(
            resolve_element(part, element),
            direction_of(part, direction),
            1 if maximum else 0,
        )
        if second_direction is not None:
            try:
                extremum.SetDirection2(direction_of(part, second_direction))
            except Exception:  # noqa: BLE001 - tie-breaking is optional
                logger.debug("Second extremum direction not applied", exc_info=True)
        return {"point": self._curve(extremum, name, "extremum")}

    def curve_reflect_line(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        direction: list[float],
        angle_deg: float = 90.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        reflect = part.HybridShapeFactory.AddNewReflectLine(
            resolve_element(part, surface),
            direction_of(part, direction),
            float(angle_deg),
            1,
            0,
        )
        return {"curve": self._curve(reflect, name, "reflect line")}
