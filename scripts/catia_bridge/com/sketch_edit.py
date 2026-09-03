"""Editing sketch geometry that is already drawn: corner, trim, mirror, pattern.

Everything here works around one fact about the automation API. `Factory2D`
creates geometry and nothing else — there is no `CreateCorner`, no trim, no
mirror, no offset. Those are interactive Sketcher commands, and a script does
not get them. So the bridge reads the elements' coordinates, computes the
result in `sketch_geometry` (pure, and tested on Linux), and writes it back
with `SetData` and fresh `Create…` calls.

Two consequences worth stating plainly, because they are visible to whoever
uses these tools:

* **A computed mirror is not an associative one.** CATIA's own mirror links the
  copy to the original. Copying coordinates does not. `sketch_mirror` therefore
  adds a real symmetry constraint between each pair afterwards, which restores
  the property that matters — edit either half and the other follows — through
  the constraint solver rather than through a feature link.
* **Offset, translate, rotate and scale copies are independent** once made.
  That matches CATIA's own defaults for those commands, so it is not a
  surprise, but the result says so rather than leaving it to be discovered.

`sketch_project` and `sketch_intersect_3d` are the exceptions: `CreateProjections`
and `CreateIntersections` do exist, they are genuinely associative, and these
two just call them.
"""

from __future__ import annotations

import math
from typing import Any

from .. import sketch_geometry as geom
from ..backend import CatiaOperationError
from ..sketch_geometry import Arc, Element, Segment, SketchGeometryError
from ._context import ComContext, resolve_element

#: `catConstraintType` for symmetry, the constraint `sketch_mirror` adds to make
#: its copies behave like CATIA's own associative mirror.
_SYMMETRY = 15

#: How `CreateProjections` is told what kind of projection to make. Normal is
#: the default and the only one every release supports; the others are attempted
#: and reported honestly when the installed CATIA does not have them.
_PROJECTION_MODES = {"normal": "CreateProjections", "silhouette": "CreateSilhouettes"}


class SketchEditMixin:
    """Corner, chamfer, trim, offset, and the four transforms."""

    # -- reading and writing 2D elements -------------------------------------

    def _element_2d(self: ComContext, sketch: Any, name: str) -> Any:  # pragma: no cover
        """One named element inside a sketch, or a message naming what is there.

        Deliberately not `Part.FindObjectByName`: that searches the whole part
        and would happily return a 3D feature with the same name as the sketch
        element the caller meant, which then fails much later with an error
        about geometry rather than about the name.
        """
        elements = sketch.GeometricElements
        for index in range(1, int(elements.Count) + 1):
            element = elements.Item(index)
            if str(element.Name) == name:
                return element
        known = ", ".join(
            str(elements.Item(i).Name)
            for i in range(1, min(int(elements.Count), 12) + 1)
        )
        raise CatiaOperationError(
            f"The sketch has no element named {name!r}. It contains: {known or '(nothing)'}."
        )

    def _read_2d(self: ComContext, element: Any) -> Element:  # pragma: no cover
        """A `Segment` or `Arc` from a CATIA 2D element.

        `GeometricType` is not reliably exposed on every release, so the shape
        is identified by which accessors the object answers to. That is uglier
        than a type check and it is also the thing that keeps working across
        R21 to R33.
        """
        try:
            centre = [0.0, 0.0]
            element.GetCenter(centre)
            return Arc(
                centre=(float(centre[0]), float(centre[1])),
                radius=float(element.Radius),
                start_angle=float(element.StartAngle),
                end_angle=float(element.EndAngle),
            )
        except Exception:  # noqa: BLE001 - probing which shape this is
            pass

        try:
            start_point, end_point = [0.0, 0.0], [0.0, 0.0]
            element.GetStartPoint(start_point)
            element.GetEndPoint(end_point)
            return Segment(
                (float(start_point[0]), float(start_point[1])),
                (float(end_point[0]), float(end_point[1])),
            )
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"{element.Name!r} is not a line or a circle, and these tools only "
                "work on those. Splines, conics and imported curves have to be "
                "redrawn rather than edited."
            ) from error

    def _write_2d(self: ComContext, element: Any, shape: Element) -> None:  # pragma: no cover
        """Move an existing element onto new coordinates, in place.

        In place rather than delete-and-recreate, because every constraint and
        dimension in the sketch is attached to *this* object. Recreating it
        would silently drop them, which turns a trim into an unconstrained
        profile that looks identical and behaves completely differently.
        """
        if isinstance(shape, Segment):
            element.SetData(
                shape.start[0], shape.start[1], shape.end[0], shape.end[1]
            )
            return
        element.SetData(shape.centre[0], shape.centre[1], shape.radius)
        if not shape.closed:
            element.StartAngle = shape.start_angle
            element.EndAngle = shape.end_angle

    def _create_2d(  # pragma: no cover - Windows only
        self: ComContext, factory: Any, shape: Element, construction: bool
    ) -> Any:
        """Draw a computed element into the sketch."""
        if isinstance(shape, Segment):
            created = factory.CreateLine(
                shape.start[0], shape.start[1], shape.end[0], shape.end[1]
            )
        elif shape.closed:
            created = factory.CreateClosedCircle(
                shape.centre[0], shape.centre[1], shape.radius
            )
        else:
            created = factory.CreateCircle(
                shape.centre[0],
                shape.centre[1],
                shape.radius,
                shape.start_angle,
                shape.end_angle,
            )
        if construction:
            try:
                created.Construction = True
            except Exception:  # noqa: BLE001 - not every element supports it
                pass
        return created

    def _pair(  # pragma: no cover - Windows only
        self: ComContext, sketch: Any, names: list[str]
    ) -> tuple[Any, Any, Segment, Segment]:
        """The two named elements and their geometry, both required to be lines.

        Corner, chamfer and trim all solve a line-line corner. Arc-to-line
        tangency is a genuinely different construction with up to four answers
        and no way for the caller to say which, so it is refused by name here
        rather than half-supported.
        """
        first_com = self._element_2d(sketch, names[0])
        second_com = self._element_2d(sketch, names[1])
        first, second = self._read_2d(first_com), self._read_2d(second_com)
        if not isinstance(first, Segment) or not isinstance(second, Segment):
            raise CatiaOperationError(
                "This operation works between two straight elements. One of "
                f"{names[0]!r} and {names[1]!r} is an arc, and rounding or cutting "
                "against an arc has several valid answers with no way here to say "
                "which one you meant."
            )
        return first_com, second_com, first, second

    # -- corner and chamfer --------------------------------------------------

    def sketch_corner(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        radius_mm: float,
        elements: list[str],
        trim: bool = True,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        target, factory = self._open_sketch(sketch)
        first_com, second_com, first, second = self._pair(target, elements)

        with _translated():
            result = geom.corner(first, second, float(radius_mm))

        arc = self._create_2d(factory, result.arc, construction)
        if trim:
            self._write_2d(first_com, result.first)
            self._write_2d(second_com, result.second)
        return {
            "element": str(arc.Name),
            "radius_mm": float(radius_mm),
            "corner": [round(value, 6) for value in result.corner],
            "trimmed": bool(trim),
        }

    def sketch_chamfer(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        length_mm: float,
        elements: list[str],
        angle_deg: float | None = None,
        second_length_mm: float | None = None,
        trim: bool = True,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        target, factory = self._open_sketch(sketch)
        first_com, second_com, first, second = self._pair(target, elements)

        with _translated():
            result = geom.chamfer(
                first,
                second,
                float(length_mm),
                angle_deg=angle_deg,
                second_length=second_length_mm,
            )

        line = self._create_2d(factory, result.line, construction)
        if trim:
            self._write_2d(first_com, result.first)
            self._write_2d(second_com, result.second)
        return {
            "element": str(line.Name),
            "first_length_mm": round(result.first_length, 6),
            "second_length_mm": round(result.second_length, 6),
            "trimmed": bool(trim),
        }

    # -- trim ----------------------------------------------------------------

    def sketch_trim(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        keep: str = "both",
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        del construction  # trimming never changes what an element is for
        target, _ = self._open_sketch(sketch)
        first_com, second_com, first, second = self._pair(target, elements)

        with _translated():
            trimmed_first, trimmed_second = geom.trim(first, second, keep)

        if keep in {"both", "first"}:
            self._write_2d(first_com, trimmed_first)
        if keep in {"both", "second"}:
            self._write_2d(second_com, trimmed_second)

        extended = (
            trimmed_first.length > first.length or trimmed_second.length > second.length
        )
        return {
            "elements": list(elements),
            "keep": keep,
            # Worth reporting: "trim" that lengthened an element is correct here
            # and surprising, and a caller checking a profile is closed wants to
            # know which of the two happened.
            "extended": extended,
        }

    # -- offset --------------------------------------------------------------

    def sketch_offset(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        distance_mm: float,
        reversed: bool = False,  # noqa: A002 - the schema's name
        propagate: bool = True,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        del propagate  # tangent propagation needs the topology COM will not give
        target, factory = self._open_sketch(sketch)

        created: list[str] = []
        with _translated():
            for name in elements:
                shape = self._read_2d(self._element_2d(target, name))
                moved = geom.offset(shape, float(distance_mm), reverse=bool(reversed))
                created.append(str(self._create_2d(factory, moved, construction).Name))

        return {
            "elements": created,
            "distance_mm": float(distance_mm),
            # Said out loud because CATIA's interactive offset *is* associative
            # and this one is not: the copy will not follow if the original moves.
            "associative": False,
        }

    # -- transforms ----------------------------------------------------------

    def _transform(  # pragma: no cover - Windows only
        self: ComContext,
        sketch_name: str,
        names: list[str],
        placements: list[geom.Transform],
        *,
        keep_original: bool,
        construction: bool,
    ) -> list[str]:
        """Apply placements to elements, moving them or leaving copies behind.

        With `keep_original` false and exactly one placement this moves the
        elements in place, which preserves their constraints; every other case
        has to create new geometry, because there is nothing to move an element
        *to* when it is being repeated.
        """
        target, factory = self._open_sketch(sketch_name)
        found = [self._element_2d(target, name) for name in names]
        shapes = [(element, self._read_2d(element)) for element in found]

        if not keep_original and len(placements) == 1:
            for com_element, shape in shapes:
                self._write_2d(com_element, geom.apply(shape, placements[0]))
            return [str(com_element.Name) for com_element, _ in shapes]

        created: list[str] = []
        for placement in placements:
            for _, shape in shapes:
                moved = geom.apply(shape, placement)
                created.append(str(self._create_2d(factory, moved, construction).Name))
        return created

    def sketch_translate(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        offset: list[float],
        copies: int = 0,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        step = (float(offset[0]), float(offset[1]))
        with _translated():
            if copies:
                placements = [
                    geom.translation((step[0] * index, step[1] * index))
                    for index in range(1, int(copies) + 1)
                ]
            else:
                placements = [geom.translation(step)]
            names = self._transform(
                sketch,
                elements,
                placements,
                keep_original=bool(copies),
                construction=construction,
            )
        return {"elements": names, "copies": int(copies), "offset": list(step)}

    def sketch_rotate(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        centre: list[float],
        angle_deg: float,
        copies: int = 0,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        pivot = (float(centre[0]), float(centre[1]))
        step = math.radians(float(angle_deg))
        with _translated():
            if copies:
                placements = [
                    geom.rotation(pivot, step * index) for index in range(1, int(copies) + 1)
                ]
            else:
                placements = [geom.rotation(pivot, step)]
            names = self._transform(
                sketch,
                elements,
                placements,
                keep_original=bool(copies),
                construction=construction,
            )
        return {"elements": names, "copies": int(copies), "angle_deg": float(angle_deg)}

    def sketch_scale(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        centre: list[float],
        factor: float,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        with _translated():
            placement = geom.scaling((float(centre[0]), float(centre[1])), float(factor))
            names = self._transform(
                sketch, elements, [placement], keep_original=False, construction=construction
            )
        return {"elements": names, "factor": float(factor)}

    def sketch_mirror(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        axis: str,
        keep_original: bool = True,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        """Reflect elements about a line, and constrain the halves to stay symmetric.

        The constraint is the point. Reflecting coordinates gives two halves
        that match now; a symmetry constraint gives two halves that go on
        matching when either is edited, which is what CATIA's own mirror
        provides and what anyone drawing half a profile is relying on.
        """
        target, factory = self._open_sketch(sketch)
        axis_com = self._element_2d(target, axis)
        axis_shape = self._read_2d(axis_com)
        if not isinstance(axis_shape, Segment):
            raise CatiaOperationError(
                f"{axis!r} is a circle, and a mirror needs a straight line to reflect "
                "about. Draw a construction line, or use the sketch's centre line."
            )

        part = self._part()
        with _translated():
            placement = geom.reflection(axis_shape)
            pairs: list[tuple[Any, Any]] = []
            for name in elements:
                original = self._element_2d(target, name)
                moved = geom.apply(self._read_2d(original), placement)
                pairs.append((original, self._create_2d(factory, moved, construction)))

        constrained = 0
        for original, copy in pairs:
            try:
                target.Constraints.AddTriEltCst(
                    _SYMMETRY,
                    part.CreateReferenceFromObject(original),
                    part.CreateReferenceFromObject(copy),
                    part.CreateReferenceFromObject(axis_com),
                )
                constrained += 1
            except Exception:  # noqa: BLE001 - an over-constrained sketch refuses it
                pass

        if not keep_original:
            for original, _ in pairs:
                try:
                    original.Delete()
                except Exception:  # noqa: BLE001 - a constrained element may refuse
                    pass

        return {
            "elements": [str(copy.Name) for _, copy in pairs],
            "axis": axis,
            # Not decoration: without the constraint the two halves are merely
            # equal today, and a caller that knows the difference can add its own.
            "symmetry_constraints": constrained,
            "associative": constrained == len(pairs),
        }

    # -- patterns ------------------------------------------------------------

    def sketch_pattern(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        kind: str,
        count: int,
        spacing_mm: float | None = None,
        second_count: int = 1,
        second_spacing_mm: float | None = None,
        centre: list[float] | None = None,
        total_angle_deg: float = 360.0,
        sketch: str = "",
        construction: bool = False,
    ) -> dict[str, Any]:
        with _translated():
            if kind == "rectangular":
                if spacing_mm is None:
                    raise CatiaOperationError(
                        "A rectangular pattern needs `spacing_mm` — the gap between "
                        "instances along the first direction."
                    )
                placements = geom.rectangular_pattern(
                    int(count),
                    float(spacing_mm),
                    second_count=int(second_count),
                    second_spacing=second_spacing_mm,
                )
            else:
                pivot = (
                    (float(centre[0]), float(centre[1])) if centre else (0.0, 0.0)
                )
                placements = geom.circular_pattern(
                    int(count), pivot, total_angle=math.radians(float(total_angle_deg))
                )

            names = self._transform(
                sketch, elements, placements, keep_original=True, construction=construction
            )

        return {
            "elements": names,
            "kind": kind,
            "instances": len(placements) + 1,
            "created": len(names),
        }

    # -- 3D geometry brought into the sketch ---------------------------------

    def sketch_project(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        mode: str = "normal",
        construction: bool = False,
        sketch: str = "",
    ) -> dict[str, Any]:
        """Project 3D edges onto the sketch plane, associatively.

        One of the two operations in this module that CATIA does implement, and
        the associativity is why it is worth preferring over measuring an edge
        and redrawing it: the projected curve follows when the 3D geometry moves.
        """
        target, factory = self._open_sketch(sketch)
        part = self._part()

        if mode == "along_direction":
            raise CatiaOperationError(
                "Projection along a direction is not available through automation; "
                "only 'normal' and 'silhouette' are. Sketch on a plane normal to the "
                "direction you wanted and project normally."
            )
        call = _PROJECTION_MODES.get(mode, "CreateProjections")

        references = [
            part.CreateReferenceFromObject(resolve_element(part, name)) for name in elements
        ]
        try:
            created = getattr(factory, call)(references)
        except AttributeError as error:
            raise CatiaOperationError(
                f"This CATIA does not offer {mode} projection through automation. "
                "Use mode 'normal'."
            ) from error
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "CATIA refused to project that geometry. It has to be visible from "
                "the sketch plane — an edge exactly perpendicular to the plane "
                f"projects to a point. ({error})"
            ) from error

        names = _mark_collection(created, construction)
        return {"elements": names, "mode": mode, "associative": True}

    def sketch_intersect_3d(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        construction: bool = False,
        sketch: str = "",
    ) -> dict[str, Any]:
        target, factory = self._open_sketch(sketch)
        del target
        part = self._part()

        references = [
            part.CreateReferenceFromObject(resolve_element(part, name)) for name in elements
        ]
        try:
            created = factory.CreateIntersections(references)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "CATIA found no intersection between that geometry and the sketch "
                "plane. The plane has to actually cut through it. "
                f"({error})"
            ) from error

        names = _mark_collection(created, construction)
        return {"elements": names, "associative": True}


def _mark_collection(created: Any, construction: bool) -> list[str]:  # pragma: no cover
    """Name every element of a returned collection, flagging construction geometry.

    `CreateProjections` returns a collection when the input had several curves
    and a bare element when it had one, and which of the two is not documented.
    Both shapes are handled rather than guessed at.
    """
    try:
        total = int(created.Count)
    except (AttributeError, TypeError):
        items = [created]
    else:
        items = [created.Item(index) for index in range(1, total + 1)]

    names: list[str] = []
    for item in items:
        if construction:
            try:
                item.Construction = True
            except Exception:  # noqa: BLE001
                pass
        names.append(str(item.Name))
    return names


class _translated:
    """Turn a geometry failure into the bridge's own error type.

    `sketch_geometry` raises `SketchGeometryError` so that it can be imported
    and tested without the bridge. Its messages are already written for whoever
    called the tool, so this re-raises rather than rewrites — the translation is
    of the exception type, not of the words.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, kind: type | None, error: BaseException | None, trace: Any) -> bool:
        if isinstance(error, SketchGeometryError):
            raise CatiaOperationError(str(error)) from error
        return False
