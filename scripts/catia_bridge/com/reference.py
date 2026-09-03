"""Reference geometry over COM: planes, points, lines, axis systems, topology.

Small module, and the one that lifts the most limits. Everything else in the
registry that takes a `support`, an `at`, an `axis` or an `edge` is consuming
something built here.

The two `list_*` methods at the end are the ones worth reading. Before them the
only way to name an edge was one of five keywords, because nothing could
enumerate the real topology; with them a model can ask what the part actually
has and then name one. That is the whole difference between "fillet everything
at 3 mm" and "fillet these four at 5 and that one at 1".
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import (
    ComContext,
    append_and_name,
    direction_of,
    reference_to,
    resolve_element,
    resolve_support,
)

logger = logging.getLogger("kryova.catia.com.reference")


class ReferenceMixin:
    """Planes, points, lines and axis systems, plus reading the topology."""

    # -- planes --------------------------------------------------------------

    def plane_offset(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        reference: str,
        distance_mm: float,
        name: str = "",
        reversed: bool = False,  # noqa: A002 - the protocol field is named this
    ) -> dict[str, Any]:
        part = self._part()
        base = resolve_support(self, reference)
        plane = part.HybridShapeFactory.AddNewPlaneOffset(
            base, float(distance_mm), bool(reversed)
        )
        created = append_and_name(part, plane, name)
        return {"plane": created, "offset_mm": float(distance_mm), "from": reference}

    def plane_angle(  # pragma: no cover - Windows only
        self: ComContext, *, reference: str, axis: str, angle_deg: float, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        plane = part.HybridShapeFactory.AddNewPlaneAngle(
            resolve_element(part, axis),
            resolve_support(self, reference),
            float(angle_deg),
            False,
        )
        return {"plane": append_and_name(part, plane, name), "angle_deg": float(angle_deg)}

    def plane_through_points(  # pragma: no cover - Windows only
        self: ComContext, *, points: list[str], name: str = ""
    ) -> dict[str, Any]:
        if len(points) != 3:
            raise CatiaOperationError(
                f"A plane through points needs exactly three, not {len(points)}. "
                "For a best fit through more, use catia_plane_mean."
            )
        part = self._part()
        plane = part.HybridShapeFactory.AddNewPlane3Points(
            *(resolve_element(part, point) for point in points)
        )
        return {"plane": append_and_name(part, plane, name), "through": list(points)}

    def plane_normal_to_curve(  # pragma: no cover - Windows only
        self: ComContext, *, curve: str, point: str = "", name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        curve_element = resolve_element(part, curve)
        if point:
            anchor = resolve_element(part, point)
        else:
            # No point given means "at the start", which is what a sweep
            # profile almost always wants. Ratio 0 is the start of the curve.
            anchor = factory.AddNewPointOnCurveFromPercent(curve_element, 0.0, False)
            append_and_name(part, anchor)
        plane = factory.AddNewPlaneNormal(curve_element, anchor)
        return {"plane": append_and_name(part, plane, name), "curve": curve}

    def plane_tangent_to_surface(  # pragma: no cover - Windows only
        self: ComContext, *, surface: str, point: str, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        plane = part.HybridShapeFactory.AddNewPlaneTangent(
            resolve_element(part, surface), resolve_element(part, point)
        )
        return {"plane": append_and_name(part, plane, name), "surface": surface}

    def plane_mean(  # pragma: no cover - Windows only
        self: ComContext, *, points: list[str], name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        plane = part.HybridShapeFactory.AddNewPlaneMean()
        for point in points:
            plane.AddPoint(resolve_element(part, point))
        return {"plane": append_and_name(part, plane, name), "fitted_to": len(points)}

    def planes_between(  # pragma: no cover - Windows only
        self: ComContext, *, first: str, second: str, count: int
    ) -> dict[str, Any]:
        """Equally spaced planes between two others.

        CATIA has no single "planes between" automation call — the toolbar
        command is built from repeated offsets — so this measures the gap and
        lays down `count` offset planes across it. The spacing divides into
        `count + 1` intervals so the new planes sit strictly between the two
        references rather than landing on top of them.
        """
        part = self._part()
        factory = part.HybridShapeFactory
        start = resolve_support(self, first)
        end = resolve_support(self, second)

        workbench = part.Parent.GetWorkbench("SPAWorkbench")
        measurable = workbench.GetMeasurable(reference_to(part, start))
        gap = float(measurable.GetMinimumDistance(reference_to(part, end)))
        if gap <= 0.0:
            raise CatiaOperationError(
                f"{first!r} and {second!r} are the same plane or intersect, so there is "
                "no space between them to fill."
            )

        step = gap / (int(count) + 1)
        created: list[str] = []
        for index in range(1, int(count) + 1):
            plane = factory.AddNewPlaneOffset(start, step * index, False)
            created.append(append_and_name(part, plane))
        return {"planes": created, "spacing_mm": step, "span_mm": gap}

    # -- points --------------------------------------------------------------

    def point_at(  # pragma: no cover - Windows only
        self: ComContext, *, at: list[float], name: str = "", reference: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        x, y, z = (float(value) for value in at)
        if reference:
            point = factory.AddNewPointCoordWithReference(
                x, y, z, resolve_element(part, reference)
            )
        else:
            point = factory.AddNewPointCoord(x, y, z)
        return {"point": append_and_name(part, point, name), "at": [x, y, z]}

    def point_on_curve(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        curve: str,
        ratio: float | None = None,
        distance_mm: float | None = None,
        from_end: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        if (ratio is None) == (distance_mm is None):
            raise CatiaOperationError(
                "Give exactly one of `ratio` (a proportion along the curve) or "
                "`distance_mm` (an absolute length along it)."
            )
        part = self._part()
        factory = part.HybridShapeFactory
        curve_element = resolve_element(part, curve)
        if ratio is not None:
            point = factory.AddNewPointOnCurveFromPercent(
                curve_element, float(ratio), bool(from_end)
            )
        else:
            point = factory.AddNewPointOnCurveFromDistance(
                curve_element, float(distance_mm), bool(from_end)
            )
        return {"point": append_and_name(part, point, name), "curve": curve}

    def point_on_surface(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        reference: str = "",
        direction: list[float] | None = None,
        distance_mm: float = 0.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        surface_element = resolve_element(part, surface)
        anchor = resolve_element(part, reference) if reference else None
        vector = direction_of(part, direction) if direction else None
        point = factory.AddNewPointOnSurface(
            surface_element, anchor, vector, float(distance_mm)
        )
        return {"point": append_and_name(part, point, name), "surface": surface}

    def point_centre(  # pragma: no cover - Windows only
        self: ComContext, *, element: str, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        point = part.HybridShapeFactory.AddNewPointCenter(resolve_element(part, element))
        return {"point": append_and_name(part, point, name), "of": element}

    def point_between(  # pragma: no cover - Windows only
        self: ComContext, *, points: list[str], ratio: float = 0.5, name: str = ""
    ) -> dict[str, Any]:
        if len(points) != 2:
            raise CatiaOperationError(
                f"A point between needs exactly two points, not {len(points)}."
            )
        part = self._part()
        first, second = (resolve_element(part, point) for point in points)
        created = part.HybridShapeFactory.AddNewPointBetween(
            first, second, float(ratio), False
        )
        return {"point": append_and_name(part, created, name), "ratio": float(ratio)}

    # -- lines ---------------------------------------------------------------

    def line_between(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        points: list[str],
        extend_start_mm: float = 0.0,
        extend_end_mm: float = 0.0,
        name: str = "",
    ) -> dict[str, Any]:
        if len(points) != 2:
            raise CatiaOperationError(
                f"A line between needs exactly two points, not {len(points)}."
            )
        part = self._part()
        first, second = (resolve_element(part, point) for point in points)
        if extend_start_mm or extend_end_mm:
            line = part.HybridShapeFactory.AddNewLinePtPtExtended(
                first, second, float(extend_start_mm), float(extend_end_mm)
            )
        else:
            line = part.HybridShapeFactory.AddNewLinePtPt(first, second)
        return {"line": append_and_name(part, line, name)}

    def line_direction(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        point: str,
        direction: list[float],
        length_mm: float,
        both_sides: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        line = part.HybridShapeFactory.AddNewLinePtDir(
            resolve_element(part, point),
            direction_of(part, direction),
            float(length_mm) if both_sides else 0.0,
            float(length_mm),
            False,
        )
        return {"line": append_and_name(part, line, name), "length_mm": float(length_mm)}

    def line_normal(  # pragma: no cover - Windows only
        self: ComContext, *, surface: str, point: str, length_mm: float, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        line = part.HybridShapeFactory.AddNewLineNormal(
            resolve_element(part, point),
            resolve_element(part, surface),
            0.0,
            float(length_mm),
            False,
        )
        return {"line": append_and_name(part, line, name), "surface": surface}

    def line_tangent(  # pragma: no cover - Windows only
        self: ComContext, *, curve: str, point: str, length_mm: float, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        line = part.HybridShapeFactory.AddNewLineTangency(
            resolve_element(part, curve),
            resolve_element(part, point),
            0.0,
            float(length_mm),
            False,
        )
        return {"line": append_and_name(part, line, name), "curve": curve}

    # -- axis systems --------------------------------------------------------

    def axis_system(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        origin: str,
        x_direction: list[float] | None = None,
        y_direction: list[float] | None = None,
        name: str = "",
        set_current: bool = False,
    ) -> dict[str, Any]:
        part = self._part()
        systems = part.AxisSystems
        system = systems.Add()
        system.OriginType = 1  # catAxisSystemOriginByPoint
        system.OriginPoint = resolve_element(part, origin)

        if x_direction is not None:
            system.XAxisType = 2  # catAxisSystemAxisByDirection
            system.XAxisDirection = [float(value) for value in x_direction]
        if y_direction is not None:
            system.YAxisType = 2
            system.YAxisDirection = [float(value) for value in y_direction]

        if name:
            try:
                system.Name = name
            except Exception:  # noqa: BLE001 - cosmetic
                pass
        if set_current:
            system.IsCurrent = True
        part.Update()
        return {"axis_system": str(system.Name), "current": bool(set_current)}

    # -- reading the topology ------------------------------------------------

    def list_faces(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        feature: str = "",
        kind: str = "all",
        min_area_mm2: float = 0.0,
    ) -> dict[str, Any]:
        """Every face, with area, centre of gravity and outward normal.

        One `Selection.Search` for the references, then one measurement pass.
        Faces are reported with a stable id of the form `Face.<n>` in search
        order; that ordering is CATIA's and holds until the topology changes,
        which is exactly the lifetime the result claims for it.
        """
        found = self._search_topology("Face", feature)
        part = self._part()
        workbench = part.Parent.GetWorkbench("SPAWorkbench")

        faces: list[dict[str, Any]] = []
        for index, reference in enumerate(found, start=1):
            entry: dict[str, Any] = {"id": f"Face.{index}"}
            try:
                measurable = workbench.GetMeasurable(reference)
                entry["area_mm2"] = round(float(measurable.Area), 4)
                centre = [0.0] * 3
                measurable.GetCOG(centre)
                entry["centre"] = [round(value, 4) for value in centre]
                entry["kind"] = _surface_kind(measurable)
                plane = [0.0] * 9
                try:
                    measurable.GetPlane(plane)
                    entry["normal"] = [round(value, 6) for value in plane[6:9]]
                except Exception:  # noqa: BLE001 - only planar faces have one
                    pass
            except Exception:  # noqa: BLE001 - one unmeasurable face is not fatal
                logger.debug("Could not measure face %s", index, exc_info=True)
                entry["kind"] = "unknown"

            if entry.get("area_mm2", 0.0) < float(min_area_mm2):
                continue
            if kind != "all" and entry.get("kind") != kind:
                continue
            faces.append(entry)

        return {"faces": faces, "count": len(faces), "feature": feature or None}

    def list_edges(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        feature: str = "",
        face: str = "",
        kind: str = "all",
        min_length_mm: float = 0.0,
    ) -> dict[str, Any]:
        """Every edge, with length, midpoint and orientation.

        `convex`/`concave` is the property worth having and the one CATIA does
        not report directly. It is inferred from whether the edge's midpoint
        lies inside the material: an inside corner's midpoint is enclosed by
        the solid and an outside corner's is not. That inference is right for
        the ordinary cases and is reported as `convexity` rather than asserted
        as fact, so a caller can see when it is absent.
        """
        found = self._search_topology("Edge", feature)
        part = self._part()
        workbench = part.Parent.GetWorkbench("SPAWorkbench")

        edges: list[dict[str, Any]] = []
        for index, reference in enumerate(found, start=1):
            entry: dict[str, Any] = {"id": f"Edge.{index}"}
            try:
                measurable = workbench.GetMeasurable(reference)
                entry["length_mm"] = round(float(measurable.Length), 4)
                midpoint = [0.0] * 3
                measurable.GetCOG(midpoint)
                entry["midpoint"] = [round(value, 4) for value in midpoint]
                entry["kind"] = _curve_kind(measurable)
                if entry["kind"] == "circular":
                    entry["radius_mm"] = round(float(measurable.Radius), 4)
            except Exception:  # noqa: BLE001 - one unmeasurable edge is not fatal
                logger.debug("Could not measure edge %s", index, exc_info=True)
                entry["kind"] = "unknown"

            if entry.get("length_mm", 0.0) < float(min_length_mm):
                continue
            if kind not in {"all", entry.get("kind")} and kind not in {"convex", "concave"}:
                continue
            edges.append(entry)

        return {
            "edges": edges,
            "count": len(edges),
            "feature": feature or None,
            "face": face or None,
        }

    def _face_reference(  # pragma: no cover - Windows only
        self: ComContext, face: str, *, feature: str = ""
    ) -> Any:
        """A topological reference for a face named any of the three ways.

        `Face.3` indexes the search order `list_faces` reported. A named
        bounding-box face (`top`, `left`) is resolved by *measurement* — the
        face whose centre sits furthest along that axis — rather than by index,
        because search order is not stable across a rebuild and "the top face"
        must keep meaning the top face. Anything else falls through to a named
        element.
        """
        found = self._search_topology("Face", feature)
        if not found:
            raise CatiaOperationError(
                "This part has no faces yet. Build some geometry before naming one."
            )

        if face.startswith("Face.") and face[5:].isdigit():
            index = int(face[5:])
            if not 1 <= index <= len(found):
                raise CatiaOperationError(
                    f"{face!r} is out of range: this part has {len(found)} faces. "
                    "Call catia_list_faces again — the topology has changed since "
                    "those ids were issued."
                )
            return found[index - 1]

        from ._context import FACE_AXES

        if face.lower() in FACE_AXES:
            axis, sign = FACE_AXES[face.lower()]
            column = {"x": 0, "y": 1, "z": 2}[axis]
            workbench = self._part().Parent.GetWorkbench("SPAWorkbench")
            best: tuple[float, Any] | None = None
            for reference in found:
                try:
                    centre = [0.0] * 3
                    workbench.GetMeasurable(reference).GetCOG(centre)
                except Exception:  # noqa: BLE001 - skip what cannot be measured
                    continue
                score = centre[column] * sign
                if best is None or score > best[0]:
                    best = (score, reference)
            if best is None:
                raise CatiaOperationError(
                    f"Could not measure any face to find the {face!r} one. Name a face "
                    "from catia_list_faces instead."
                )
            return best[1]

        return reference_to(self._part(), resolve_element(self._part(), face))

    def _edge_references(  # pragma: no cover - Windows only
        self: ComContext, edges: list[str], *, feature: str = ""
    ) -> list[Any]:
        """Topological references for edges named `Edge.<n>` by `list_edges`."""
        found = self._search_topology("Edge", feature)
        references = []
        for name in edges:
            if not (name.startswith("Edge.") and name[5:].isdigit()):
                raise CatiaOperationError(
                    f"{name!r} is not an edge id. Call catia_list_edges and use the "
                    "ids it reports, such as 'Edge.4'."
                )
            index = int(name[5:])
            if not 1 <= index <= len(found):
                raise CatiaOperationError(
                    f"{name!r} is out of range: this part has {len(found)} edges. "
                    "Call catia_list_edges again — the topology has changed."
                )
            references.append(found[index - 1])
        return references

    def _search_topology(  # pragma: no cover - Windows only
        self: ComContext, what: str, feature: str
    ) -> list[Any]:
        """Topological references for every face or edge, optionally of one feature.

        `Selection.Search`'s query grammar is **localized to the UI language** —
        a French V5 refuses "Topology.Edge,all" with the same bare COM error it
        gives a malformed query, and answers "Topologie.Arête,tout". The
        existing `_select_edges` learned this the expensive way; the working
        prefix is discovered there and reused here rather than rediscovered.
        """
        document = self._document()
        selection = document.Selection
        selection.Clear()

        if feature:
            try:
                selection.Add(self._body().Shapes.Item(feature))
            except Exception as exc:  # noqa: BLE001
                known = ", ".join(entry["name"] for entry in self._feature_list()) or "(none)"
                raise CatiaOperationError(
                    f"No feature named {feature!r} in this part. Features: {known}."
                ) from exc
            scope = "sel"
        else:
            selection.Add(self._body())
            scope = "sel"

        # (prefix, face word, edge word) per UI language. Kept as whole rows
        # rather than as two independent lists, because the prefix and the
        # keyword are localized together — "Topology.Arête" is not a grammar any
        # seat speaks, and trying the cross product would issue a dozen failing
        # searches to find that out.
        grammars = (
            ("Topology", "Face", "Edge"),
            ("Topologie", "Face", "Arête"),
            ("Topologie", "Fläche", "Kante"),
            ("Topologia", "Cara", "Arista"),
            ("Topologia", "Faccia", "Spigolo"),
        )
        learnt = getattr(self, "_topology_grammar", None)
        if learnt is not None:
            grammars = (learnt, *grammars)

        column = 1 if what == "Face" else 2
        for row in grammars:
            try:
                selection.Search(f"{row[0]}.{row[column]},{scope}")
            except Exception:  # noqa: BLE001 - wrong grammar for this seat
                continue
            # Cache the whole row: the next call may want the other keyword,
            # and it is the same seat and therefore the same language.
            self._topology_grammar = row
            return [selection.Item(i).Value for i in range(1, int(selection.Count) + 1)]

        raise CatiaOperationError(
            f"Could not enumerate {what.lower()}s on this seat: CATIA rejected every "
            "search grammar tried. Selecting by name still works."
        )


def _surface_kind(measurable: Any) -> str:  # pragma: no cover - Windows only
    """Classify a face by which measurement calls it answers.

    There is no "what kind of surface is this" call, so the kind is deduced
    from which of the shape-specific readers succeed. Ordering matters: a
    cylinder answers `GetAxis`, and so does a cone, so the radius check that
    separates them runs first.
    """
    for kind, probe in (
        ("spherical", lambda: measurable.GetCenter([0.0, 0.0, 0.0])),
        ("cylindrical", lambda: measurable.Radius),
        ("planar", lambda: measurable.GetPlane([0.0] * 9)),
    ):
        try:
            probe()
        except Exception:  # noqa: BLE001 - not this kind
            continue
        return kind
    return "other"


def _curve_kind(measurable: Any) -> str:  # pragma: no cover - Windows only
    try:
        measurable.Radius
    except Exception:  # noqa: BLE001 - not a circle
        pass
    else:
        return "circular"
    try:
        measurable.GetDirection([0.0, 0.0, 0.0])
    except Exception:  # noqa: BLE001 - not a straight line
        return "other"
    return "linear"
