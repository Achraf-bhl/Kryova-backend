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

from .. import edges as edge_geometry
from .. import vba
from ..backend import CatiaOperationError
from ._context import (
    FACE_AXES,
    ComContext,
    append_and_name,
    direction_of,
    reference_to,
    resolve_element,
    resolve_support,
)

logger = logging.getLogger("kryova.catia.com.reference")


#: `Selection.Search`'s keywords are localized, the same way the edge grammars
#: in `catia_com._SEARCH_GRAMMARS` are. Whole rows, tried in order: the prefix
#: and the keyword are translated together, so a cross product would issue a
#: dozen failing searches to learn what one row answers.
_FACE_GRAMMARS: tuple[tuple[str, str], ...] = (
    ("Topologie", "Face"),
    ("Topology", "Face"),
    ("Topologie", "Fl\u00e4che"),
    ("Topologia", "Cara"),
    ("Topologia", "Faccia"),
)


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
        """Every face, with area in mm2, centre of gravity and outward normal.

        Measured through `vba.face_map` -- one Evaluate for the whole part --
        because a face `Measurable` answers almost nothing from Python. `Area`
        works but arrives in **square metres**, and `GetCOG` and `GetPlane`
        both return without error having written nothing into the list handed
        to them. Measured on V5-R33, 2026-09-06, on ladder prompt H4: every
        face of a 8 x 120 x 20 bracket reported centre [0, 0, 0], normal
        [0, 0, 0] and an area of 0.001 mm2 against a real 1600. The agent read
        that, could not place a hole from it, and spent the rest of its budget
        creating empty sketches.

        **`normal` is the normal of the face's plane, and its sign is CATIA's,
        not the material's.** Measured on the seat: the bottom face of a block
        reports [0, 0, 1], pointing up into the solid. Where the face lies on
        the part's bounding box the outward direction is not a guess -- a face
        in the z = zmin plane faces -Z, whatever its parameterisation says --
        so the sign is corrected there and `normal_is_outward` is true. Every
        other face keeps the plane's own sign with `normal_is_outward` false,
        because a heuristic that is right on a block and wrong in the notch of
        an L-bracket is worse than a flag: an unmarked wrong direction is
        acted on, and a marked unknown one is checked.

        `kind` is CATIA's own classification, read off the search result --
        it types each hit `PlanarFace`, `CylindricalFace`, `ConicalFace`,
        `SphericalFace` or a bare `Face`. That replaced deducing the kind from
        which measurement calls a face answered, which was a guess where a
        fact was available.

        Ids are `Face.<n>` in search order, which is the order
        `_face_reference` resolves them in, and holds until the topology
        changes -- exactly the lifetime the result claims for it.
        """
        selection, found, scoped_query, scope_shape = self._found_faces(feature or None)
        # CATIA's own type per hit, read while the search result is still in
        # the selection. `_bounding_box` below builds and measures reference
        # planes, which clears it -- so anything read from the selection has to
        # be read first. That ordering cost a run to find.
        kinds = {index: _face_kind(selection.Item2(index).Type) for index in found}
        measured = vba.face_map(self._app, self._part(), scoped_query, scope_shape)
        box = self._bounding_box()

        faces: list[dict[str, Any]] = []
        unmeasured = 0
        for position, index in enumerate(found, start=1):
            facts = measured.get(index)
            if facts is None:
                unmeasured += 1
                continue
            entry: dict[str, Any] = {
                "id": f"Face.{position}",
                "area_mm2": round(facts.area_mm2, 4),
                "centre": [round(value, 4) for value in facts.centre],
                "kind": kinds[index],
            }
            if facts.normal is not None:
                normal, outward = _outward_normal(facts.normal, facts.centre, box)
                entry["normal"] = [round(value, 6) for value in normal]
                entry["normal_is_outward"] = outward
            if entry["area_mm2"] < float(min_area_mm2):
                continue
            if kind != "all" and kind != entry["kind"]:
                continue
            faces.append(entry)

        result: dict[str, Any] = {
            "faces": faces,
            "count": len(faces),
            "feature": feature or None,
            "kind": kind,
        }
        if unmeasured:
            result["unmeasured"] = unmeasured
            result["note"] = (
                f"{unmeasured} face(s) could not be measured by CATIA and are not "
                "listed; their ids are still counted, so the ids above are valid."
            )
        return result

    def list_edges(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        feature: str = "",
        face: str = "",
        kind: str = "all",
        min_length_mm: float = 0.0,
    ) -> dict[str, Any]:
        """Every solid edge, measured the one way this seat will measure them.

        The first version asked `Measurable` per edge for `GetCOG` and
        `GetDirection`, and every edge of every part came back
        `kind: "unknown"` with no midpoint: `GetCOG` raises E_NOTIMPL on an
        edge and `GetDirection` cannot fill a Python list (see `edges.py`).
        Measured on ladder prompt H3, 2026-09-06 -- 16 edges, 16 unknowns, and
        a `kind` filter that could match nothing. Now it runs `vba.edge_map`
        -- one Evaluate, three points per edge -- and shares its classifier
        with `_select_edges`, so the edges this lists as vertical are the edges
        `catia_fillet edges='vertical'` rounds.

        Ids are positions in the search result, and `_edge_references`
        resolves them from the same enumeration, so an id read here is the id
        `catia_fillet_edges` acts on -- as long as both are scoped the same
        way. A `feature=` scope renumbers, and the result says so.

        `convex`/`concave` are refused rather than returned empty: nothing
        here measures them, and an empty list reads as "there are none".
        """
        if kind in ("convex", "concave"):
            raise CatiaOperationError(edge_geometry.UNMEASURED_CONVEXITY)
        on_plane = self._face_plane(face) if face else None
        selection, found, measured = self._measured_edges(feature or None)
        z_top, z_bottom = edge_geometry.z_extent(list(measured.values()))
        workbench = self._part().Parent.GetWorkbench("SPAWorkbench")

        edges: list[dict[str, Any]] = []
        unmeasured = 0
        for position, index in enumerate(found, start=1):
            triple = measured.get(index)
            if triple is None:
                unmeasured += 1
                continue
            radius: float | None = None
            if not edge_geometry.is_linear(triple):
                # `Radius` is the one per-edge Measurable call that works over
                # COM, and it refuses on anything that is not an arc -- which
                # is exactly the question.
                try:
                    reference = selection.Item2(index).Reference
                    radius = float(workbench.GetMeasurable(reference).Radius)
                except Exception:  # noqa: BLE001 - not an arc, and that is the answer
                    radius = None
            entry: dict[str, Any] = {"id": f"Edge.{position}"}
            entry.update(edge_geometry.describe(triple, z_top, z_bottom, radius_mm=radius))
            if float(entry["length_mm"]) < float(min_length_mm):
                continue
            if on_plane is not None:
                axis, value = on_plane
                if not all(abs(p[axis] - value) < edge_geometry.TOLERANCE for p in triple):
                    continue
            if kind != "all" and kind != entry["kind"] and kind not in entry["orientation"]:
                continue
            edges.append(entry)

        result: dict[str, Any] = {
            "edges": edges,
            "count": len(edges),
            "feature": feature or None,
            "face": face or None,
            "kind": kind,
        }
        if unmeasured:
            result["unmeasured"] = unmeasured
            result["note"] = (
                f"{unmeasured} edge(s) could not be measured by CATIA and are not "
                "listed; their ids are still counted, so the ids above are valid."
            )
        if feature:
            result["id_scope"] = (
                f"Ids are numbered within {feature}'s edges. catia_fillet_edges numbers "
                "the whole part: call catia_list_edges without feature= before naming "
                "ids to it, or use catia_fillet with feature= and an edge group."
            )
        return result

    def _face_plane(self: ComContext, face: str) -> tuple[int, float]:  # pragma: no cover
        """(axis index, coordinate) of a named bounding-box face, for filtering.

        Only the six named faces: a `Face.N` id would need the face's own
        geometry, which `_search_topology` returns as an element and not as a
        plane, and pretending to support it would return every edge or none.
        """
        key = face.strip().casefold()
        if key not in FACE_AXES:
            raise CatiaOperationError(
                f"face={face!r}: catia_list_edges filters by the named faces only "
                f"({', '.join(FACE_AXES)}). A Face.N id cannot be used as a filter "
                "here yet; list the edges of the whole part or of one feature instead."
            )
        box = self._bounding_box()
        if box is None:
            raise CatiaOperationError(
                "The part's bounding box could not be measured, so its edges cannot "
                "be filtered by face. Try again without face=."
            )
        letter, sign = FACE_AXES[key]
        axis = "xyz".index(letter)
        return axis, (box[axis + 3] if sign > 0 else box[axis])

    def _found_faces(  # pragma: no cover - Windows only
        self: ComContext, feature: str | None = None
    ) -> tuple[Any, list[int], str, Any]:
        """The faces of the part or of one feature, as CATIA's selection.

        The same shape as `_found_edges` and for the same reason: `face_map`
        repeats the search inside CATIA, so it needs the query and the scope,
        and the ids handed back to a caller must be positions in *this*
        enumeration and no other.
        """
        selection = self._document().Selection
        if feature:
            try:
                scope_shape = self._body().Shapes.Item(feature)
            except Exception as exc:  # noqa: BLE001
                known = ", ".join(entry["name"] for entry in self._feature_list()) or "(none)"
                raise CatiaOperationError(
                    f"No feature named {feature!r} in this part. Features: {known}."
                ) from exc
        else:
            scope_shape = self._body()

        errors: list[str] = []
        for prefix, word in _FACE_GRAMMARS:
            query = f"{prefix}.{word},sel"
            selection.Clear()
            selection.Add(scope_shape)
            try:
                selection.Search(query)
            except Exception as exc:  # noqa: BLE001 - wrong language, try the next
                errors.append(f"{prefix}: {exc}")
                continue
            # A face search answers `PlanarFace`, `CylindricalFace`,
            # `ConicalFace`, `SphericalFace` or a bare `Face` -- *not* the
            # `TriDim...` an edge search answers, which is why the edge
            # filter copied over here matched nothing at all and every part
            # looked as though it had no faces.
            found = [
                index
                for index in range(1, int(selection.Count2) + 1)
                if "Face" in str(selection.Item2(index).Type)
            ]
            if not found:
                raise CatiaOperationError(
                    "This part has no solid faces yet. Pad or revolve something first."
                )
            return selection, found, query, scope_shape

        raise CatiaOperationError(
            "CATIA refused every face-search grammar this bridge knows "
            f"({'; '.join(errors)}). Its UI language may be one the bridge has no "
            "query vocabulary for yet -- see _FACE_GRAMMARS in com/reference.py."
        )

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
        """Topological references for edges named `Edge.<n>` by `list_edges`.

        Resolved through `_found_edges`, the enumeration `list_edges` numbers
        over, and pulled as `Item2(...).Reference` -- the form the seat's own
        fillet has been verified to accept. Resolving through a different
        search than the one that produced the ids is how 'Edge.4' comes to
        mean a different edge on the way back.
        """
        selection, found, _, _ = self._found_edges(feature or None)
        references = []
        for name in edges:
            if not (name.startswith("Edge.") and name[5:].isdigit()):
                raise CatiaOperationError(
                    f"{name!r} is not an edge id. Call catia_list_edges and use the "
                    "ids it reports, such as 'Edge.4'."
                )
            position = int(name[5:])
            if not 1 <= position <= len(found):
                raise CatiaOperationError(
                    f"{name!r} is out of range: this part has {len(found)} edges. "
                    "Call catia_list_edges again — the topology has changed."
                )
            references.append(selection.Item2(found[position - 1]).Reference)
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


def _face_kind(reported: Any) -> str:  # pragma: no cover - Windows only
    """CATIA's own face type, as the vocabulary the tool's schema offers.

    `PlanarFace` -> `planar`, `CylindricalFace` -> `cylindrical`, and so on;
    anything else -- a bare `Face`, a surface type this table does not name --
    is `other`, which is what the schema calls it too.
    """
    name = str(reported)
    for kind in ("planar", "cylindrical", "conical", "spherical"):
        if name.lower().startswith(kind):
            return kind
    return "other"


def _outward_normal(
    normal: tuple[float, float, float],
    centre: tuple[float, float, float],
    box: tuple[float, float, float, float, float, float] | None,
) -> tuple[tuple[float, float, float], bool]:
    """The face normal, turned to face out of the material where that is known.

    Decidable exactly when the face sits in one of the bounding box's six
    planes with its normal along that axis: such a face is on the outside of
    the part, so it faces away from the box's middle, and no heuristic is
    involved. Anything else -- a pocket floor, the inner face of an
    L-bracket, a rib flank -- keeps the plane's own sign and is reported as
    not outward.

    The sign matters because it is the difference between drilling into a part
    and drilling away from it, and CATIA's parameterisation says nothing about
    which side the material is on.
    """
    if box is None:
        return normal, False
    tolerance = 1e-6
    for axis in range(3):
        if abs(abs(normal[axis]) - 1.0) > tolerance:
            continue  # not axis-aligned along this one
        if any(abs(normal[other]) > tolerance for other in range(3) if other != axis):
            continue  # not axis-aligned at all
        low, high = box[axis], box[axis + 3]
        if abs(centre[axis] - high) < 1e-4:
            sign = 1.0
        elif abs(centre[axis] - low) < 1e-4:
            sign = -1.0
        else:
            return normal, False
        turned = [0.0, 0.0, 0.0]
        turned[axis] = sign
        return (turned[0], turned[1], turned[2]), True
    return normal, False
