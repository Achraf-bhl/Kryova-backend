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

The hazard of holding a sketch open used to be met with a refusal, and the
refusal cost more than the hazard: see `_end_sketch_edition`. The 3D
operations now end the open edition themselves, the way leaving the Sketcher
is implicit in clicking Pad. The drawing tools are the other way round --
`_open_sketch` refuses a sketch that is not open, because drawing into the
wrong sketch is the mistake worth catching.
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

        Three cases, and only the last is an error:

        * no name, or the name of the sketch already open -> that one;
        * the name of another sketch in this part -> switch to it, ending the
          current edition, which is what double-clicking a sketch in the tree
          does;
        * a name this part does not have -> refuse, and say what it does have.

        The middle case used to be the second error. It is the difference
        between "you meant a different sketch" -- which the caller is entitled
        to mean -- and "you named something that is not there", which is a
        mistake worth catching because drawing into the wrong sketch produces
        a wrong part with every call reporting success.
        """
        state = getattr(self, "_sketch_edition", None)
        if state is not None and (not name or str(state[0].Name) == name):
            return state

        if name:
            # Naming a sketch that exists is a request to draw into THAT one,
            # and the answer is to open it -- the way a person double-clicks a
            # sketch in the tree, which ends the edition they were in. This
            # used to be refused with "'Sketch.2' is not the open sketch
            # ('Sketch.3' is). Close that one with catia_sketch_close before
            # editing another", which is true, correct, and costs a round of a
            # budget of twenty every time it happens. Measured on ladder
            # prompt H4, 2026-09-06, twice in one run.
            #
            # The refusal survives for a name that is not in the part at all:
            # that one is a typo or an invention, and drawing into the wrong
            # sketch is the mistake this whole check exists to catch.
            existing = next(
                (s for s in self._sketches_in_part() if str(s.Name) == name), None
            )
            if existing is not None:
                self._end_sketch_edition()
                factory = existing.OpenEdition()
                self._sketch_edition = (existing, factory)
                return existing, factory
            known = ", ".join(str(s.Name) for s in self._sketches_in_part()) or "(none)"
            raise CatiaOperationError(
                f"No sketch named {name!r} in this part. It has: {known}. Create it "
                "with catia_sketch_create, or use one of those names."
            )

        raise CatiaOperationError(
            "No sketch is open. Call catia_sketch_create first — the drawing "
            "tools add to an open sketch, they do not create one."
        )

    def _end_sketch_edition(self: ComContext) -> str | None:
        """Close whatever sketch is open, and say which one it was.

        This used to be `_require_closed`, which *refused* the call instead:
        "The sketch 'Sketch.1' is still open. Call catia_sketch_close before
        building a feature from it". Measured on ladder prompt H3 three runs
        running (2026-09-06): the agent drew a rectangle, padded it -- which
        CATIA accepted with the sketch in edition -- rounded the edges, and was
        then refused a *second sketch* with a message about building a feature
        from the first. Two rounds went on closing it. On an earlier run the
        recovery was worse: the agent passed "Close Sketch" to
        catia_run_command, CATIA raised its unknown-command box, and the seat
        was dead until a human pressed OK (`tests/test_catia_unavailable_states.py`).

        A person does not get this refusal, because leaving the Sketcher is
        what starting the next thing *means*. So the 3D operations, and
        `sketch_create`, end the open edition themselves. Nothing about the
        sketch changes -- CloseEdition ends editing, it does not alter
        geometry -- and the drawing tools still refuse a sketch that is not
        open, which is the mistake worth catching.

        Returns the closed sketch's name so a result can say it happened.
        """
        state = getattr(self, "_sketch_edition", None)
        if state is None:
            return None
        sketch, _ = state
        name = str(sketch.Name)
        sketch.CloseEdition()
        self._sketch_edition = None
        return name

    def _empty_sketch_count(self: ComContext) -> int:  # pragma: no cover
        """How many sketches in this part have nothing drawn in them.

        Reported by `sketch_create`, not acted on. H4 left eight empty sketches
        in the tree, each created because the previous call had not produced
        what the agent expected -- and nothing in any result said so, because
        an empty sketch is a perfectly successful `sketch_create`. Deleting
        them here was the other option and is worse: a sketch the caller
        intends to draw into on the next call is empty at exactly this moment.
        """
        empty = 0
        for sketch in self._sketches_in_part():
            try:
                if int(sketch.GeometricElements.Count) <= 1:  # the axis only
                    empty += 1
            except Exception:  # noqa: BLE001 - unreadable is not empty
                continue
        return empty

    def _refuse_a_duplicate_name(self: ComContext, name: str) -> None:
        """A sketch name has to identify one sketch.

        CATIA will happily hold two sketches called `Sketch.1`, and everything
        here that resolves a sketch by name takes the first it finds. Measured
        on ladder prompt H4, 2026-09-06: the agent created `Sketch.1` twice and
        `Sketch.wall` twice, and `catia_list_features` reported

            Sketch.1 (4 elements), Sketch.2 (0), Sketch.1 (0),
            Sketch.3 (0), Sketch.wall (0), Sketch.wall (0)

        -- at which point neither it nor the bridge could say which `Sketch.1`
        a pad would extrude. Refusing costs one round and names the sketch
        that already exists; silently renaming would leave the agent believing
        it holds a name it does not.
        """
        for existing in self._sketches_in_part():
            if str(existing.Name) == name:
                raise CatiaOperationError(
                    f"This part already has a sketch called {name!r}, and two sketches "
                    "with one name cannot be told apart by any tool here. Draw into it "
                    f"with catia_sketch_rectangle(sketch={name!r}, ...), or create this "
                    "one under a different name."
                )

    def _sketches_in_part(self: ComContext) -> list[Any]:  # pragma: no cover
        """Every sketch of the main body, as CATIA objects."""
        sketches = self._body().Sketches
        return [sketches.Item(index) for index in range(1, int(sketches.Count) + 1)]

    def sketch_create(  # pragma: no cover - Windows only
        self: ComContext, *, support: str, name: str = "", origin: list[float] | None = None
    ) -> dict[str, Any]:
        closed = self._end_sketch_edition()
        if name:
            self._refuse_a_duplicate_name(name)
        reused = self._reuse_an_empty_sketch(support, name)
        if reused is not None:
            if closed is not None:
                reused["closed_previous"] = closed
            return reused
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
        result: dict[str, Any] = {"sketch": str(sketch.Name), "support": support, "open": True}
        if closed is not None:
            result["closed_previous"] = closed
        empty = self._empty_sketch_count()
        if empty > 1:
            result["note"] = (
                f"This part now holds {empty} sketches with nothing drawn in them. "
                "Draw into this one before creating another, or say what is not "
                "working -- creating more sketches will not make the last one build."
            )
        return result

    def _reuse_an_empty_sketch(  # pragma: no cover - Windows only
        self: ComContext, support: str, name: str
    ) -> dict[str, Any] | None:
        """Hand back an existing empty sketch rather than refusing the call.

        This used to raise, and the reasoning was sound for the failure it was
        written against: an agent that creates sketch after sketch without
        drawing in any of them is looping, and the tree fills with names that
        every later `catia_list_features` has to report. Ladder prompts H4 and
        PRO1 both measured it.

        What it could not know is that most of those empties are **debris this
        bridge creates itself**. `sketch_revolve_profile`, `sketch_gear_profile`
        and `sketch_groove_profile` build their own sketch from a plane; an
        agent that reasonably calls `catia_sketch_create(name='Gear section')`
        first and then asks for the profile gets the profile in a *new* sketch
        and its named one left empty. Two revolutions is two orphans, and the
        third `sketch_create` was refused -- so the guard was punishing the
        caller for a mess the tools made.

        Measured on the geared-shaft prompt, 2026-09-07: shaft and gear blank
        built, `Shaft profile` and `Gear section profile` left holding one
        geometric element each (the absolute axis, i.e. nothing), and every
        subsequent `sketch_create` refused. The teeth were never cut.

        Refusing costs the whole run. Reusing costs nothing: the caller wanted a
        sketch on `support`, an empty one on that support is indistinguishable
        from a fresh one, and it gets the name that was asked for. The tree does
        not grow, which was the guard's actual aim.

        Only a sketch on the *same support* is reused -- one on another plane is
        a different sketch and handing it over would silently draw the profile
        somewhere else, which is far worse than an extra name in the tree.
        """
        if self._empty_sketch_count() < 2:
            return None
        wanted = resolve_support(self, support)
        for sketch in self._sketches_in_part():
            if not _is_empty(sketch):
                continue
            try:
                if str(sketch.AbsoluteAxis.Parent.Name) != str(wanted.Name):
                    continue
            except Exception:  # noqa: BLE001 - support unreadable, do not guess
                continue
            if name:
                try:
                    sketch.Name = name
                except Exception:  # noqa: BLE001 - cosmetic
                    pass
            self._sketch_edition = (sketch, sketch.OpenEdition())
            return {
                "sketch": str(sketch.Name),
                "support": support,
                "open": True,
                "reused": True,
                "note": (
                    "An empty sketch already on this plane was reused rather than a "
                    "new one created, so the tree does not fill with names. Draw into "
                    "it as you would a new one."
                ),
            }
        return None

    def _refuse_to_pile_up_empty_sketches(self: ComContext) -> None:  # pragma: no cover
        """Refuse a new sketch while empty ones are already stacking up.

        **No longer called from `sketch_create`** -- see
        `_reuse_an_empty_sketch`, which does the same job by handing one over.
        Kept because the reasoning below is the record of why the empties
        matter, and because a caller with no reusable sketch on the wanted plane
        still gets the `note` on the way out.

        Reported and not acted on until 2026-09-06, and the report was not
        enough. Measured twice on the seat:

        * ladder prompt H4 run 1 -- eight empty sketches in the tree and a bare
          strip of a part;
        * ladder prompt PRO1 run 2 -- `Frame profile`, `C-Frame outline` and
          `Frame outline`, created and closed without a line in any of them,
          while the turn ran out of rounds.

        An empty sketch is a perfectly successful `sketch_create`, so nothing
        failed and nothing said stop. But creating a second one before drawing
        in the first is never the way out of anything: whatever went wrong with
        the last sketch is still wrong, and the tree fills with names that
        `catia_list_features` then has to report and the agent has to read past.

        The threshold is two, not one. One empty sketch is the ordinary state
        between `sketch_create` and the first line, and a caller that creates a
        sketch, thinks again and creates another on a different plane is doing
        something reasonable. Three is a loop.

        Deleting them instead was the other option and is worse: the sketch the
        caller means to draw into on the very next call is empty at exactly
        this moment.
        """
        empty = self._empty_sketch_count()
        if empty < 2:
            return
        names = [
            str(sketch.Name)
            for sketch in self._sketches_in_part()
            if _is_empty(sketch)
        ]
        raise CatiaOperationError(
            f"This part already holds {empty} sketches with nothing drawn in them "
            f"({', '.join(names[:4])}), so another one was not created. Nothing was "
            "changed. Draw into one of them -- catia_sketch_polyline takes a whole "
            "profile in one call -- or close it with catia_sketch_close. Creating "
            "more sketches will not make the last one build."
        )

    def sketch_close(  # pragma: no cover - Windows only
        self: ComContext, *, sketch: str = ""
    ) -> dict[str, Any]:
        state = getattr(self, "_sketch_edition", None)

        if sketch and (state is None or str(state[0].Name) != sketch):
            # The named sketch is not in edition, which is the state the caller
            # asked for. Answering "\'Sketch.2\' is not the open sketch
            # (\'BracketProfile\' is)" was accurate and cost a round of twenty
            # every time -- measured on ladder prompt H4, 2026-09-06, three
            # times in one run, because the agent lost track of which sketch it
            # had opened and closing them one by one is exactly how a careful
            # caller recovers from that.
            #
            # A name the part does not have is still refused: that is a typo or
            # an invention, and reporting it closed would be a lie about the
            # part.
            names = [str(existing.Name) for existing in self._sketches_in_part()]
            if sketch not in names:
                raise CatiaOperationError(
                    f"No sketch named {sketch!r} in this part. It has: "
                    f"{', '.join(names) or '(none)'}."
                )
            result: dict[str, Any] = {"sketch": sketch, "open": False, "already_closed": True}
            if state is not None:
                result["note"] = (
                    f"{sketch} was already closed. {state[0].Name} is the one open now; "
                    "call catia_sketch_close with no arguments to close it."
                )
            return result

        if state is None:
            raise CatiaOperationError("No sketch is open, so there is nothing to close.")
        target, _ = state
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
        dimension_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Draw a rectangle, and give its width and height drivable names.

        **Why the constraints are here and not optional.** Measured on the seat
        2026-09-06, ladder prompt S1 -- "get it to 2.4 kg by adjusting only its
        width and height". A rectangle drawn as four free lines has no
        dimensions: `catia_list_parameters` on a 40 x 40 x 200 block returned

            Extrusion.1\\Première limite\\Longueur   200 mm
            Extrusion.1\\Sketch.1\\Contact.1\\Activité   1
            ... and nine more booleans

        The pad length is drivable. The rectangle's width and height do not
        exist as anything at all, so there is nothing for `catia_set_parameter`
        to move and no way to converge on a mass target by changing the
        section. The agent invented a parameter called `WidthHeight`, was
        correctly refused, and had no way forward.

        Two length constraints on the two perpendicular sides fix that. They
        leave the rectangle under-constrained in position and rotation, which
        is deliberate -- it is what it was before, so nothing that already
        works can become over-constrained -- and they make the two numbers the
        user actually named into parameters with names.

        **Best-effort, and never fatal.** A release that will not take the
        constraint, or a profile that is already dimensioned some other way,
        must not turn a working rectangle into a failed call: ladder prompt H4
        passes through this exact path. A failure is reported in
        `dimensions_named` and the geometry is exactly what it was.
        """
        cu, cv = (float(at[0]), float(at[1])) if at else (0.0, 0.0)
        half_w, half_h = float(width_mm) / 2.0, float(height_mm) / 2.0
        corners = _rotate_about(
            [(-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h)],
            math.radians(float(rotation_deg)),
            (cu, cv),
        )
        wanted = [str(one) for one in (dimension_names or ["width", "height"])][:2]
        drawn: list[Any] = []

        def draw(factory: Any) -> Any:
            for (u1, v1), (u2, v2) in zip(corners, corners[1:] + corners[:1], strict=False):
                drawn.append(_mark(factory.CreateLine(u1, v1, u2, v2), construction))
            return None

        result = self._draw(draw, plane=plane, sketch=sketch, at=[cu, cv], corners=corners)
        if not construction and len(drawn) == 4 and len(wanted) == 2:
            named = _name_the_sides(
                self, result.get("sketch", ""), drawn, wanted, (float(width_mm), float(height_mm))
            )
            result["dimensions_named"] = named
            if named.get("named"):
                result["note"] = (
                    "The width and height are named parameters now: "
                    + ", ".join(named["named"])
                    + ". Drive them with catia_set_parameter and re-measure, rather "
                    "than redrawing the sketch."
                )
        return result

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
        #
        # **`.Dimension` is where this fails on a real seat, and it fails as a
        # bare COM error.** Measured on ladder prompt PRO1, 2026-09-08, three
        # runs, French V5-R33: `AddDimensionConstraint` returned a constraint
        # object and reading `.Dimension` off it raised
        # `(0, 'CATIAConstraint', 'La methode Dimension a echoue', ..., E_INVALIDARG)`.
        # The agent was handed that string, could do nothing with it, and
        # re-issued the call six times across the three runs.
        #
        # Two things were wrong and both are fixed here. The message is now
        # something an agent can act on, in the register the pad and pocket
        # refusals already use. And the half-made constraint is taken with it:
        # `AddDimensionConstraint` has already put it in the sketch, and a
        # constraint with no dimension is exactly the wreckage
        # `_discard_failed_feature` exists for -- on this run the sketch it was
        # left in went on to fail its pad three times.
        try:
            if kind == "angle":
                constraint.Dimension.Value = math.radians(float(value))
            else:
                constraint.Dimension.Value = float(value)
        except Exception as exc:  # noqa: BLE001 - turned into a refusal below
            self._discard_failed_feature(constraint)
            named = " and ".join(repr(name) for name in elements)
            raise CatiaOperationError(
                f"CATIA would not put a {kind} dimension of {value:g} on {named}. "
                "That usually means the sketch cannot take it: the elements are "
                "already fixed relative to each other, the dimension would "
                "over-constrain the profile, or the two elements cannot have that "
                "kind of dimension between them (a distance needs two elements that "
                "are actually apart; an angle needs two lines that are not parallel). "
                "The constraint has been removed, so the sketch is still usable. "
                "Draw the profile at the size you want with catia_sketch_polyline or "
                "catia_sketch_rectangle -- the coordinates you pass are millimetres "
                "and are the dimension -- and constrain only what you must. "
                f"CATIA reported: {exc}"
            ) from exc
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


def _name_the_sides(  # pragma: no cover - Windows only
    context: Any, sketch_name: str, lines: list[Any], names: list[str], sizes: tuple[float, float]
) -> dict[str, Any]:
    """Put a named length constraint on one horizontal and one vertical side.

    Returns what happened rather than raising. The caller has already drawn
    valid geometry, and a rectangle nobody can drive is worth strictly more
    than a call that failed.

    `lines` arrive in draw order -- bottom, right, top, left -- so index 0 is
    the width and index 1 is the height whatever the rotation, because both are
    measured along the shape's own sides rather than along the sketch axes.
    """
    report: dict[str, Any] = {"named": [], "skipped": ""}
    try:
        part = context._part()
        sketch = context._sketch_edition[0] if context._sketch_edition else None
        if sketch is None or str(sketch.Name) != sketch_name:
            sketch = resolve_element(part, sketch_name) if sketch_name else None
        if sketch is None:
            report["skipped"] = "the sketch could not be resolved after drawing"
            return report
        constraints = sketch.Constraints
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        report["skipped"] = f"the sketch offered no constraint collection ({exc})"
        return report

    for line, name, size in zip(lines[:2], names, sizes, strict=False):
        try:
            reference = part.CreateReferenceFromObject(line)
            constraint = constraints.AddMonoEltCst(_LENGTH_CONSTRAINT, reference)
            constraint.Dimension.Value = float(size)
            constraint.Name = name
            report["named"].append(str(constraint.Name))
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            report["skipped"] = f"{name}: {exc}"
            break
    return report


def _is_empty(sketch: Any) -> bool:  # pragma: no cover - Windows only
    """Whether a sketch holds nothing but its own axis system."""
    try:
        return int(sketch.GeometricElements.Count) <= 1
    except Exception:  # noqa: BLE001 - unreadable is not empty
        return False


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


#: `catCstTypeLength`. The length of one element, which is what a rectangle's
#: side is. Published in CATIA's own automation enumeration and stable across
#: releases -- and, unlike a command label, not localised.
_LENGTH_CONSTRAINT = 5


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
