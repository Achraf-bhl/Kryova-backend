"""Sketcher over COM: 2D geometry with real coordinates, and its constraints.

The existing `catia_com` sketch helpers open a sketch, draw one shape centred
on the origin, and close it again — `_sketch(plane, draw)`. That shape is why
every profile was origin-centred: there was nowhere to put an x and a y, and no
way to add a second primitive to a sketch that had already closed.

This mixin keeps a sketch *open* across calls. `sketch_create` opens one and
records it; every drawing call finds it, draws into it, and leaves it open;
`sketch_close` ends the edition. That is what makes a five-primitive
constrained profile expressible, and it is the reason `_open` exists as state
rather than being derived — CATIA's `Sketch` object has no "am I in edition"
property to read back.

The one hazard of holding a sketch open is that every *other* COM call fails
oddly while one is. `_require_closed` is called by the operations that would
trip on it, so the error names the real cause instead of surfacing whatever
CATIA says about a feature it could not build.
"""

from __future__ import annotations

import math
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext, resolve_element, resolve_support

#: `catConstraintType` values for the geometric constraints. Numeric because
#: the automation API takes the enum's integer value, and the names below are
#: the registry's vocabulary rather than CATIA's own spelling.
_GEOMETRIC_CONSTRAINTS = {
    "coincidence": 1,
    "concentricity": 2,
    "tangency": 3,
    "parallelism": 4,
    "perpendicularity": 5,
    "horizontal": 8,
    "vertical": 9,
    "symmetry": 15,
    "equidistant": 16,
    "fix": 14,
}

#: `catConstraintType` values for the dimensional ones.
_DIMENSIONAL_CONSTRAINTS = {
    "distance": 6,
    "length": 7,
    "radius": 10,
    "diameter": 11,
    "angle": 12,
}

#: How many elements each constraint kind consumes. CATIA has three separate
#: add-constraint calls keyed by exactly this, so getting it wrong is a COM
#: error rather than a wrong answer — but the COM error does not say which
#: constraint or how many it wanted, which is why it is checked here first.
_CONSTRAINT_ARITY = {
    "coincidence": 2, "concentricity": 2, "tangency": 2, "parallelism": 2,
    "perpendicularity": 2, "horizontal": 1, "vertical": 1, "fix": 1,
    "symmetry": 3, "equidistant": 3,
    "distance": 2, "length": 1, "radius": 1, "diameter": 1, "angle": 2,
}


class SketcherMixin:
    """Open a sketch, draw into it with coordinates, constrain it, close it."""

    # -- the open sketch -----------------------------------------------------

    def _open_sketch(self: ComContext, name: str = "") -> tuple[Any, Any]:
        """The sketch being drawn into, and its 2D factory.

        Naming one that is not open is a mistake worth catching precisely: it
        is the difference between "you meant a different sketch" and "you
        forgot to open this one", and CATIA's own error distinguishes neither.
        """
        state = getattr(self, "_sketch_edition", None)
        if state is None:
            raise CatiaOperationError(
                "No sketch is open. Call catia_sketch_create first — the drawing "
                "tools add to an open sketch, they do not create one."
            )
        sketch, factory = state
        if name and str(sketch.Name) != name:
            raise CatiaOperationError(
                f"{name!r} is not the open sketch ({sketch.Name!r} is). Close that one "
                "with catia_sketch_close before editing another; CATIA edits one "
                "sketch at a time."
            )
        return sketch, factory

    def _require_closed(self: ComContext) -> None:
        """Refuse a 3D operation while a sketch is still in edition."""
        state = getattr(self, "_sketch_edition", None)
        if state is not None:
            raise CatiaOperationError(
                f"The sketch {state[0].Name!r} is still open. Call catia_sketch_close "
                "before building a feature from it — CATIA cannot use a profile it is "
                "still editing."
            )

    def sketch_create(  # pragma: no cover - Windows only
        self: ComContext, *, support: str, name: str = "", origin: list[float] | None = None
    ) -> dict[str, Any]:
        self._require_closed()
        sketch = self._body().Sketches.Add(resolve_support(self, support))
        if name:
            try:
                sketch.Name = name
            except Exception:  # noqa: BLE001 - cosmetic
                pass
        if origin is not None:
            # `SetAbsoluteAxisData` takes origin then the two in-plane axis
            # directions, nine doubles in all. Moving only the origin means
            # keeping the support's own axes, which is what an offset origin
            # should mean — rotating as well would be a different request.
            current = [0.0] * 9
            sketch.GetAbsoluteAxisData(current)
            current[0] += float(origin[0])
            current[1] += float(origin[1])
            sketch.SetAbsoluteAxisData(current)

        self._sketch_edition = (sketch, sketch.OpenEdition())
        return {"sketch": str(sketch.Name), "support": support, "open": True}

    def sketch_close(  # pragma: no cover - Windows only
        self: ComContext, *, sketch: str = ""
    ) -> dict[str, Any]:
        state = getattr(self, "_sketch_edition", None)
        if state is None:
            raise CatiaOperationError("No sketch is open, so there is nothing to close.")
        target, _ = state
        if sketch and str(target.Name) != sketch:
            raise CatiaOperationError(
                f"{sketch!r} is not the open sketch ({target.Name!r} is)."
            )
        target.CloseEdition()
        self._sketch_edition = None
        self._part().Update()
        return {"sketch": str(target.Name), "open": False}

    # -- drawing -------------------------------------------------------------

    def sketch_point(  # pragma: no cover - Windows only
        self: ComContext, *, at: list[float], sketch: str = "", construction: bool = False
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        element = factory.CreatePoint(float(at[0]), float(at[1]))
        _mark(element, construction)
        return {"element": str(element.Name), "at": [float(at[0]), float(at[1])]}

    def sketch_line(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        start: list[float],
        end: list[float],
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        element = factory.CreateLine(
            float(start[0]), float(start[1]), float(end[0]), float(end[1])
        )
        _mark(element, construction)
        return {"element": str(element.Name)}

    def sketch_polyline(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        points: list[list[float]],
        closed: bool = False,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        vertices = [(float(u), float(v)) for u, v in points]
        if closed and vertices[0] != vertices[-1]:
            vertices.append(vertices[0])

        created: list[str] = []
        for (u1, v1), (u2, v2) in zip(vertices, vertices[1:], strict=False):
            element = factory.CreateLine(u1, v1, u2, v2)
            _mark(element, construction)
            created.append(str(element.Name))
        return {"elements": created, "segments": len(created), "closed": bool(closed)}

    def sketch_axis(  # pragma: no cover - Windows only
        self: ComContext, *, start: list[float], end: list[float], sketch: str = ""
    ) -> dict[str, Any]:
        target, factory = self._open_sketch(sketch)
        line = factory.CreateLine(
            float(start[0]), float(start[1]), float(end[0]), float(end[1])
        )
        # A revolution axis is a construction element that the sketch also
        # carries as `.CenterLine`. Setting only one of the two produces a
        # sketch that looks right and that a shaft refuses.
        line.ReportName = 1
        line.Construction = True
        target.CenterLine = line
        return {"element": str(line.Name), "is_axis": True}

    def sketch_circle(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        diameter_mm: float,
        at: list[float] | None = None,
        plane: str = "",
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        u, v = (float(at[0]), float(at[1])) if at else (0.0, 0.0)
        radius = float(diameter_mm) / 2.0

        def draw(factory: Any) -> Any:
            element = factory.CreateClosedCircle(u, v, radius)
            _mark(element, construction)
            return element

        return self._draw(draw, plane=plane, sketch=sketch, at=[u, v])

    def sketch_arc(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        centre: list[float],
        radius_mm: float,
        start_angle_deg: float,
        end_angle_deg: float,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        element = factory.CreateCircle(
            float(centre[0]),
            float(centre[1]),
            float(radius_mm),
            math.radians(float(start_angle_deg)),
            math.radians(float(end_angle_deg)),
        )
        _mark(element, construction)
        return {"element": str(element.Name)}

    def sketch_arc_three_point(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        start: list[float],
        through: list[float],
        end: list[float],
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        """An arc through three points.

        `Factory2D` has no three-point arc, so the centre and the two angles are
        solved here — the perpendicular bisectors of the two chords meet at the
        centre. Collinear points have no such meeting, and that is refused with
        the reason rather than left to produce a divide-by-zero deep in COM.
        """
        (x1, y1), (x2, y2), (x3, y3) = (
            (float(p[0]), float(p[1])) for p in (start, through, end)
        )
        determinant = 2.0 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(determinant) < 1e-9:
            raise CatiaOperationError(
                "Those three points are in a straight line, so no arc passes through "
                "all of them. Move the middle point off the line, or draw a line."
            )
        sq1, sq2, sq3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
        cx = (sq1 * (y2 - y3) + sq2 * (y3 - y1) + sq3 * (y1 - y2)) / determinant
        cy = (sq1 * (x3 - x2) + sq2 * (x1 - x3) + sq3 * (x2 - x1)) / determinant
        radius = math.hypot(x1 - cx, y1 - cy)

        start_angle = math.atan2(y1 - cy, x1 - cx)
        mid_angle = math.atan2(y2 - cy, x2 - cx)
        end_angle = math.atan2(y3 - cy, x3 - cx)
        # CATIA sweeps anticlockwise from start to end. If the middle point is
        # not inside that sweep, the arc goes the long way round and misses it.
        if not _between_ccw(start_angle, mid_angle, end_angle):
            start_angle, end_angle = end_angle, start_angle

        _, factory = self._open_sketch(sketch)
        element = factory.CreateCircle(cx, cy, radius, start_angle, end_angle)
        _mark(element, construction)
        return {
            "element": str(element.Name),
            "centre": [round(cx, 6), round(cy, 6)],
            "radius_mm": round(radius, 6),
        }

    def sketch_ellipse(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        centre: list[float],
        major_radius_mm: float,
        minor_radius_mm: float,
        rotation_deg: float = 0.0,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        if float(minor_radius_mm) > float(major_radius_mm):
            raise CatiaOperationError(
                "The minor radius must not exceed the major one. Swap them, or rotate "
                "the ellipse by 90 degrees if that was the intent."
            )
        _, factory = self._open_sketch(sketch)
        angle = math.radians(float(rotation_deg))
        cu, cv = float(centre[0]), float(centre[1])
        # CATIA takes the *endpoint* of the major semi-axis, not a length and an
        # angle, so the rotation is applied here.
        major_u = cu + float(major_radius_mm) * math.cos(angle)
        major_v = cv + float(major_radius_mm) * math.sin(angle)
        element = factory.CreateClosedEllipse(
            cu, cv, major_u, major_v, float(minor_radius_mm)
        )
        _mark(element, construction)
        return {"element": str(element.Name)}

    def sketch_spline(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        points: list[list[float]],
        closed: bool = False,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        control = [factory.CreateControlPoint(float(u), float(v)) for u, v in points]
        element = factory.CreateSpline(control)
        if closed:
            try:
                element.CloseSpline()
            except Exception:  # noqa: BLE001 - not every release exposes it
                pass
        _mark(element, construction)
        return {"element": str(element.Name), "control_points": len(control)}

    def sketch_conic(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        start: list[float],
        end: list[float],
        tangent_intersection: list[float],
        parameter: float = 0.5,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        """A conic from two endpoints, their tangent intersection and a shape value.

        `Factory2D` exposes parabola and hyperbola by focus, neither of which is
        the two-tangents form an aerofoil or a fairing is specified in. The
        general conic is built as a rational quadratic Bézier: the three points
        are its control polygon and `parameter` is the weight, which is exactly
        what CATIA's own conic dialog calls the "parameter". Below 0.5 gives an
        ellipse arc, 0.5 a parabola, above it a hyperbola.
        """
        _, factory = self._open_sketch(sketch)
        weight = float(parameter)
        p0 = (float(start[0]), float(start[1]))
        p1 = (float(tangent_intersection[0]), float(tangent_intersection[1]))
        p2 = (float(end[0]), float(end[1]))

        # Sampled into a spline because Factory2D has no rational-Bézier call.
        # 24 samples holds the shape to well under a micron over any span these
        # schemas admit, and a spline is editable afterwards where a conic
        # primitive would not be.
        samples = []
        for step in range(25):
            t = step / 24.0
            b0 = (1 - t) ** 2
            b1 = 2 * t * (1 - t) * weight
            b2 = t**2
            denominator = b0 + b1 + b2
            samples.append(
                factory.CreateControlPoint(
                    (b0 * p0[0] + b1 * p1[0] + b2 * p2[0]) / denominator,
                    (b0 * p0[1] + b1 * p1[1] + b2 * p2[1]) / denominator,
                )
            )
        element = factory.CreateSpline(samples)
        _mark(element, construction)
        kind = "parabola" if weight == 0.5 else ("ellipse" if weight < 0.5 else "hyperbola")
        return {"element": str(element.Name), "conic": kind, "parameter": weight}

    def sketch_rectangle(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        width_mm: float,
        height_mm: float,
        at: list[float] | None = None,
        rotation_deg: float = 0.0,
        plane: str = "",
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        cu, cv = (float(at[0]), float(at[1])) if at else (0.0, 0.0)
        half_w, half_h = float(width_mm) / 2.0, float(height_mm) / 2.0
        corners = _rotate_about(
            [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)],
            math.radians(float(rotation_deg)),
            (cu, cv),
        )

        def draw(factory: Any) -> Any:
            for (u1, v1), (u2, v2) in zip(corners, corners[1:] + corners[:1], strict=False):
                _mark(factory.CreateLine(u1, v1, u2, v2), construction)
            return None

        return self._draw(draw, plane=plane, sketch=sketch, at=[cu, cv], corners=corners)

    def sketch_parallelogram(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        corner: list[float],
        width_mm: float,
        height_mm: float,
        angle_deg: float,
        rotation_deg: float = 0.0,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        _, factory = self._open_sketch(sketch)
        base = math.radians(float(rotation_deg))
        skew = base + math.radians(float(angle_deg))
        origin = (float(corner[0]), float(corner[1]))
        along = (float(width_mm) * math.cos(base), float(width_mm) * math.sin(base))
        up = (float(height_mm) * math.cos(skew), float(height_mm) * math.sin(skew))
        points = [
            origin,
            (origin[0] + along[0], origin[1] + along[1]),
            (origin[0] + along[0] + up[0], origin[1] + along[1] + up[1]),
            (origin[0] + up[0], origin[1] + up[1]),
        ]
        for (u1, v1), (u2, v2) in zip(points, points[1:] + points[:1], strict=False):
            _mark(factory.CreateLine(u1, v1, u2, v2), construction)
        return {"corners": [[round(u, 6), round(v, 6)] for u, v in points]}

    def sketch_polygon(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        sides: int,
        diameter_mm: float,
        at: list[float] | None = None,
        rotation_deg: float = 0.0,
        plane: str = "",
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        cu, cv = (float(at[0]), float(at[1])) if at else (0.0, 0.0)
        radius = float(diameter_mm) / 2.0
        offset = math.radians(float(rotation_deg))
        count = int(sides)
        corners = [
            (
                cu + radius * math.cos(offset + 2 * math.pi * i / count),
                cv + radius * math.sin(offset + 2 * math.pi * i / count),
            )
            for i in range(count)
        ]

        def draw(factory: Any) -> Any:
            for (u1, v1), (u2, v2) in zip(corners, corners[1:] + corners[:1], strict=False):
                _mark(factory.CreateLine(u1, v1, u2, v2), construction)
            return None

        return self._draw(draw, plane=plane, sketch=sketch, sides=count, at=[cu, cv])

    def sketch_slot(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        start: list[float],
        end: list[float],
        width_mm: float,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        """An elongated hole: two parallel lines closed by a semicircle at each end."""
        _, factory = self._open_sketch(sketch)
        x1, y1 = float(start[0]), float(start[1])
        x2, y2 = float(end[0]), float(end[1])
        radius = float(width_mm) / 2.0
        span = math.hypot(x2 - x1, y2 - y1)
        if span < 1e-9:
            raise CatiaOperationError(
                "A slot needs two distinct end centres. For a round hole of this "
                "width, use catia_sketch_circle."
            )
        # Unit normal to the slot's centre line: the two flanks are offset along
        # it by the radius, and the two end arcs sweep between those offsets.
        nx, ny = -(y2 - y1) / span, (x2 - x1) / span
        axis_angle = math.atan2(y2 - y1, x2 - x1)

        _mark(
            factory.CreateLine(
                x1 + nx * radius, y1 + ny * radius, x2 + nx * radius, y2 + ny * radius
            ),
            construction,
        )
        _mark(
            factory.CreateLine(
                x2 - nx * radius, y2 - ny * radius, x1 - nx * radius, y1 - ny * radius
            ),
            construction,
        )
        _mark(
            factory.CreateCircle(
                x2, y2, radius, axis_angle - math.pi / 2, axis_angle + math.pi / 2
            ),
            construction,
        )
        _mark(
            factory.CreateCircle(
                x1, y1, radius, axis_angle + math.pi / 2, axis_angle + 3 * math.pi / 2
            ),
            construction,
        )
        return {"length_mm": round(span + float(width_mm), 6), "width_mm": float(width_mm)}

    # -- constraints ---------------------------------------------------------

    def sketch_constrain(  # pragma: no cover - Windows only
        self: ComContext, *, kind: str, elements: list[str], sketch: str = ""
    ) -> dict[str, Any]:
        target, _ = self._open_sketch(sketch)
        wanted = _CONSTRAINT_ARITY[kind]
        if len(elements) != wanted:
            raise CatiaOperationError(
                f"A {kind} constraint takes {wanted} element(s), not {len(elements)}."
            )
        references = [self._sketch_reference(target, name) for name in elements]
        constraint = _add_constraint(
            target, _GEOMETRIC_CONSTRAINTS[kind], references
        )
        return {"constraint": str(constraint.Name), "kind": kind}

    def sketch_dimension(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        elements: list[str],
        value: float,
        parameter_name: str = "",
        reference: bool = False,
        sketch: str = "",
    ) -> dict[str, Any]:
        target, _ = self._open_sketch(sketch)
        wanted = _CONSTRAINT_ARITY[kind]
        if len(elements) != wanted:
            raise CatiaOperationError(
                f"A {kind} dimension takes {wanted} element(s), not {len(elements)}."
            )
        references = [self._sketch_reference(target, name) for name in elements]
        constraint = _add_constraint(target, _DIMENSIONAL_CONSTRAINTS[kind], references)

        # An angle is set in radians and a length in millimetres; the parameter
        # carries its own unit, so writing the number without regard to which
        # is a silent factor-of-57 error on angles.
        if kind == "angle":
            constraint.Dimension.Value = math.radians(float(value))
        else:
            constraint.Dimension.Value = float(value)
        constraint.Mode = 1 if reference else 0  # catCstModeDrivingDimension = 0

        if parameter_name:
            try:
                constraint.Dimension.Rename(parameter_name)
            except Exception:  # noqa: BLE001 - naming is a convenience
                pass
        return {
            "constraint": str(constraint.Name),
            "kind": kind,
            "value": float(value),
            "driving": not reference,
        }

    def _sketch_reference(  # pragma: no cover - Windows only
        self: ComContext, sketch: Any, name: str
    ) -> Any:
        """A `Reference` for one named element inside a sketch."""
        elements = sketch.GeometricElements
        for index in range(1, int(elements.Count) + 1):
            element = elements.Item(index)
            if str(element.Name) == name:
                return self._part().CreateReferenceFromObject(element)
        known = ", ".join(
            str(elements.Item(i).Name) for i in range(1, min(int(elements.Count), 12) + 1)
        )
        raise CatiaOperationError(
            f"The sketch has no element named {name!r}. It contains: {known or '(nothing)'}."
        )

    def sketch_analysis(  # pragma: no cover - Windows only
        self: ComContext, *, sketch: str = ""
    ) -> dict[str, Any]:
        """Whether the profile is usable, and how constrained it is.

        Reports rather than judges. `closed` is the property a pad depends on
        and the one whose absence produces the least helpful CATIA error, so it
        is the headline; the degrees of freedom say whether the profile will
        survive being edited, which is a different question and also worth
        asking before building on it.
        """
        state = getattr(self, "_sketch_edition", None)
        if state is not None and (not sketch or str(state[0].Name) == sketch):
            target = state[0]
        else:
            target = resolve_element(self._part(), sketch) if sketch else None
            if target is None:
                raise CatiaOperationError(
                    "Name a sketch to analyse, or open one with catia_sketch_create."
                )

        elements = target.GeometricElements
        constraints = target.Constraints
        geometry = sum(
            1
            for index in range(1, int(elements.Count) + 1)
            if not bool(elements.Item(index).Construction)
        )
        report: dict[str, Any] = {
            "sketch": str(target.Name),
            "elements": int(elements.Count),
            "profile_elements": geometry,
            "constraints": int(constraints.Count),
        }
        try:
            solving = int(target.Solve())
            report["fully_constrained"] = solving == 0
        except Exception:  # noqa: BLE001 - not every release exposes Solve
            report["fully_constrained"] = None
        if geometry == 0:
            report["closed"] = False
            report["note"] = (
                "The sketch has no profile geometry — only construction elements, or "
                "nothing at all. A pad or pocket will refuse it."
            )
        return report

    # -- shared --------------------------------------------------------------

    def _draw(  # pragma: no cover - Windows only
        self: ComContext, draw: Any, *, plane: str, sketch: str, **extra: Any
    ) -> dict[str, Any]:
        """Draw into the open sketch, or make a one-shot sketch on `plane`.

        The two modes are what keep the original single-shape tools working
        unchanged while the same tool also serves a multi-primitive profile:
        with a sketch open it draws into it and leaves it open, and with none
        open and a plane named it creates, draws and closes in one call — which
        is exactly what `catia_sketch_circle(plane=..., diameter_mm=...)` meant
        before any of this existed.
        """
        if getattr(self, "_sketch_edition", None) is not None or sketch:
            _, factory = self._open_sketch(sketch)
            element = draw(factory)
            result = {"sketch": str(self._sketch_edition[0].Name), "open": True, **extra}
            if element is not None:
                result["element"] = str(element.Name)
            return result

        if not plane:
            raise CatiaOperationError(
                "No sketch is open and no plane was given. Either name a `plane` to "
                "draw this shape on its own, or call catia_sketch_create first to "
                "build a profile from several shapes."
            )
        part = self._part()
        target = self._body().Sketches.Add(resolve_support(self, plane))
        factory = target.OpenEdition()
        try:
            element = draw(factory)
        finally:
            target.CloseEdition()
        part.Update()
        result = {"sketch": str(target.Name), "open": False, "plane": plane, **extra}
        if element is not None:
            result["element"] = str(element.Name)
        return result


def _mark(element: Any, construction: bool) -> Any:  # pragma: no cover - Windows only
    """Flag an element as construction geometry when asked.

    Construction elements guide other geometry and are never part of a profile,
    so a pad ignores them. Getting this wrong in either direction produces a
    profile that looks right on screen and fails to build.
    """
    if construction:
        try:
            element.Construction = True
        except Exception:  # noqa: BLE001 - not every 2D element supports it
            pass
    return element


def _add_constraint(  # pragma: no cover - Windows only
    sketch: Any, kind: int, references: list[Any]
) -> Any:
    """Dispatch to the right add-constraint call for the number of elements."""
    constraints = sketch.Constraints
    if len(references) == 1:
        return constraints.AddMonoEltCst(kind, references[0])
    if len(references) == 2:
        return constraints.AddBiEltCst(kind, references[0], references[1])
    return constraints.AddTriEltCst(kind, references[0], references[1], references[2])


def _rotate_about(
    points: list[tuple[float, float]], angle: float, centre: tuple[float, float]
) -> list[tuple[float, float]]:
    """Rotate points about the origin, then translate them to `centre`."""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    return [
        (centre[0] + u * cos_a - v * sin_a, centre[1] + u * sin_a + v * cos_a)
        for u, v in points
    ]


def _between_ccw(start: float, middle: float, end: float) -> bool:
    """Whether `middle` lies on the anticlockwise sweep from `start` to `end`."""
    span = (end - start) % (2 * math.pi)
    offset = (middle - start) % (2 * math.pi)
    return offset <= span
