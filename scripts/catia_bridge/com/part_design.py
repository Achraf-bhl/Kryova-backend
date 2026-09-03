"""Part Design over COM: the features the original nine could not express.

The three limits named in the gap review are lifted here, and it is worth being
precise about which call does it in each case, because they were all downstream
of the same missing thing — a way to reference real topology.

* **Holes at coordinates.** `AddNewHoleFromPoint` places a hole at an (x, y, z)
  on a face reference. The old five-position enum existed because nothing could
  produce either the point or the face.
* **Per-edge fillets.** `AddNewSolidEdgeFilletWithConstantRadius` takes a
  reference, so `catia_list_edges` → `catia_fillet_edges` gives a radius per
  edge where the old tool had five keywords for the whole part.
* **Shells with an opening.** `AddNewShell` takes the faces to remove as its
  first argument. Passing `None` there — which is what the old `shell` did,
  because it had no face reference to pass — produces a sealed hollow.

Bodies and booleans are the other addition, and they are what make a part more
than one linear feature stack: model the cavity as its own body and remove it.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext, direction_of, geometrical_set, resolve_element, resolve_support

logger = logging.getLogger("kryova.catia.com.part_design")

#: `catCstAttrHoleType` values, in the order the registry's `kind` enum names them.
_HOLE_TYPES = {
    "simple": 0,
    "tapered": 1,
    "counterbored": 2,
    "countersunk": 3,
    "counterdrilled": 4,
}

#: `catBooleanShapeType`-equivalent: which ShapeFactory call each boolean uses.
_BOOLEANS = {
    "add": "AddNewAdd",
    "remove": "AddNewRemove",
    "intersect": "AddNewIntersect",
    "union_trim": "AddNewTrim",
    "assemble": "AddNewAssemble",
}


class PartDesignMixin:
    """Holes, dress-up, bodies, booleans and transformations."""

    # -- holes ---------------------------------------------------------------

    def hole_at(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        face: str,
        at: list[float],
        diameter_mm: float,
        depth_mm: float | None = None,
        through_all: bool = False,
        kind: str = "simple",
        head_diameter_mm: float | None = None,
        head_depth_mm: float | None = None,
        head_angle_deg: float | None = None,
        bottom_angle_deg: float | None = None,
        thread: str = "",
        thread_depth_mm: float | None = None,
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        x, y, z = (float(value) for value in at)
        reference = self._face_reference(face)

        hole = part.ShapeFactory.AddNewHoleFromPoint(x, y, z, reference, float(depth_mm or 1.0))
        try:
            hole.Diameter.Value = float(diameter_mm)
            hole.Type = _HOLE_TYPES[kind]
            if through_all:
                hole.BottomLimit.LimitMode = 1  # catUpToLastLimit
            else:
                if depth_mm is None:
                    raise CatiaOperationError(
                        "Give a depth_mm, or set through_all to drill straight through."
                    )
                hole.BottomLimit.LimitMode = 0  # catOffsetLimit
                hole.Depth.Value = float(depth_mm)

            if head_diameter_mm is not None:
                hole.HeadDiameter.Value = float(head_diameter_mm)
            if head_depth_mm is not None:
                hole.HeadDepth.Value = float(head_depth_mm)
            if head_angle_deg is not None:
                hole.HeadAngle.Value = float(head_angle_deg)
            if bottom_angle_deg is not None:
                hole.BottomAngle.Value = float(bottom_angle_deg)

            if thread:
                hole.ThreadingMode = 1  # catStandardThread
                hole.ThreadDescription = thread
                if thread_depth_mm is not None:
                    hole.ThreadDepth.Value = float(thread_depth_mm)
            part.Update()
        except CatiaOperationError:
            self._discard_failed_feature(hole)
            raise
        except Exception as exc:  # noqa: BLE001 - report, do not leave a broken feature
            self._discard_failed_feature(hole)
            raise CatiaOperationError(
                f"Could not drill the hole: {exc}. Check that the point lies on the "
                "named face — a point off the face has nothing to drill into."
            ) from exc

        result = self._feature_result(str(hole.Name))
        result |= {"at": [x, y, z], "face": face, "kind": kind}
        if thread:
            result["thread"] = thread
        return result

    def hole_pattern(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        face: str,
        points: list[list[float]],
        diameter_mm: float,
        depth_mm: float | None = None,
        through_all: bool = True,
        thread: str = "",
    ) -> dict[str, Any]:
        """Several identical holes on one face.

        CATIA has no multi-point hole feature in the automation API, so this is
        n holes reported as one result. The difference from n separate calls is
        that a failure part-way through is reported with the holes that *did*
        land, rather than leaving the caller to work out where it stopped.
        """
        created: list[str] = []
        for index, point in enumerate(points, start=1):
            try:
                result = self.hole_at(
                    face=face,
                    at=point,
                    diameter_mm=diameter_mm,
                    depth_mm=depth_mm,
                    through_all=through_all,
                    thread=thread,
                )
            except CatiaOperationError as exc:
                raise CatiaOperationError(
                    f"Drilled {len(created)} of {len(points)} holes; number {index} at "
                    f"{point} failed: {exc}"
                ) from exc
            created.append(str(result.get("feature", "")))
        return {"holes": created, "count": len(created), "face": face}

    def thread(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        face: str,
        designation: str,
        depth_mm: float | None = None,
        pitch_mm: float | None = None,
        left_handed: bool = False,
        tap: bool = True,
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        reference = self._face_reference(face)
        feature = part.ShapeFactory.AddNewThreadWithOutRef(reference)
        try:
            feature.ThreadSide = 1 if tap else 0
            feature.ThreadDescription = designation
            if depth_mm is not None:
                feature.ThreadDepth.Value = float(depth_mm)
            if pitch_mm is not None:
                feature.Pitch.Value = float(pitch_mm)
            feature.RightThreaded = not left_handed
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"Could not add the thread: {exc}. A thread needs a cylindrical face; "
                f"check that {face!r} is one."
            ) from exc
        # A thread is an annotation on the geometry, not modelled material, so
        # the mass deliberately does not change. Saying so here stops the caller
        # reading an unchanged mass as a failed operation.
        return self._feature_result(str(feature.Name)) | {
            "designation": designation,
            "note": "A thread is a specification, not modelled helical material; mass is unchanged.",
        }

    # -- dress-up ------------------------------------------------------------

    def fillet_edges(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        edges: list[dict[str, Any]],
        propagation: str = "tangency",
        edge_relimitation: bool = False,
    ) -> dict[str, Any]:
        """Round named edges, each at its own radius.

        Grouped by radius rather than one feature per edge: CATIA's own fillet
        takes a list, and a single feature per radius rebuilds faster and reads
        far better in the tree than fourteen numbered ones.

        Largest radius first, deliberately. A small fillet applied first is
        frequently consumed by a large one applied to a neighbouring edge, and
        the failure that produces names the *second* feature, which is the wrong
        place to look.
        """
        self._require_closed()
        part = self._part()
        factory = part.ShapeFactory
        mode = {"tangency": 1, "minimal": 2, "intersection": 3}.get(propagation, 1)

        by_radius: dict[float, list[str]] = {}
        for entry in edges:
            by_radius.setdefault(float(entry["radius_mm"]), []).append(str(entry["edge"]))

        created: list[dict[str, Any]] = []
        for radius in sorted(by_radius, reverse=True):
            names = by_radius[radius]
            references = self._edge_references(names)
            fillet = factory.AddNewSolidEdgeFilletWithConstantRadius(
                references[0], mode, radius
            )
            for extra in references[1:]:
                fillet.AddObjectToFillet(extra)
            if edge_relimitation:
                try:
                    fillet.EdgePropagation = 2
                except Exception:  # noqa: BLE001 - not every release exposes it
                    pass
            try:
                part.Update()
            except Exception as exc:  # noqa: BLE001
                self._discard_failed_feature(fillet)
                raise CatiaOperationError(
                    f"A {radius} mm fillet on {', '.join(names)} failed: {exc}. The "
                    "radius is usually too large for the geometry around the edge — "
                    "try a smaller one, or fillet fewer edges at once."
                ) from exc
            created.append({"feature": str(fillet.Name), "radius_mm": radius, "edges": names})

        return {"fillets": created, "edge_count": len(edges)}

    def fillet_variable(  # pragma: no cover - Windows only
        self: ComContext, *, edge: str, radii: list[dict[str, Any]], variation: str = "cubic"
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        reference = self._edge_references([edge])[0]
        ordered = sorted(radii, key=lambda entry: float(entry["at_ratio"]))
        fillet = part.ShapeFactory.AddNewSolidEdgeFilletWithVaryingRadius(
            reference, 1, float(ordered[0]["radius_mm"])
        )
        try:
            fillet.VariationType = 1 if variation == "cubic" else 0
            for entry in ordered[1:]:
                point = part.HybridShapeFactory.AddNewPointOnCurveFromPercent(
                    reference, float(entry["at_ratio"]), False
                )
                geometrical_set(part).AppendHybridShape(point)
                fillet.AddVariationPoint(point, float(entry["radius_mm"]))
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(fillet)
            raise CatiaOperationError(f"Could not build the variable fillet: {exc}") from exc
        return self._feature_result(str(fillet.Name)) | {"points": len(ordered)}

    def fillet_face(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        first_face: str,
        second_face: str,
        radius_mm: float,
        hold_curve: str = "",
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        fillet = part.ShapeFactory.AddNewSolidFaceFillet(
            self._face_reference(first_face),
            self._face_reference(second_face),
            float(radius_mm),
        )
        if hold_curve:
            try:
                fillet.HoldCurve = resolve_element(part, hold_curve)
            except Exception:  # noqa: BLE001 - optional refinement
                logger.debug("Hold curve not applied", exc_info=True)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(fillet)
            raise CatiaOperationError(
                f"The face-face fillet failed: {exc}. The two faces must be close "
                "enough for a {radius_mm} mm blend to reach between them."
            ) from exc
        return self._feature_result(str(fillet.Name))

    def fillet_tritangent(  # pragma: no cover - Windows only
        self: ComContext, *, faces: list[str], removed_face: str
    ) -> dict[str, Any]:
        self._require_closed()
        if len(faces) != 3:
            raise CatiaOperationError(
                f"A tritangent fillet needs exactly three faces, not {len(faces)}."
            )
        part = self._part()
        keep = [name for name in faces if name != removed_face]
        if len(keep) != 2:
            raise CatiaOperationError(
                f"{removed_face!r} must be one of the three faces given."
            )
        fillet = part.ShapeFactory.AddNewSolidTritangentFillet(
            self._face_reference(keep[0]),
            self._face_reference(keep[1]),
            self._face_reference(removed_face),
        )
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(fillet)
            raise CatiaOperationError(f"The tritangent fillet failed: {exc}") from exc
        return self._feature_result(str(fillet.Name))

    def draft(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        faces: list[str],
        angle_deg: float,
        neutral: str,
        pulling_direction: list[float] | None = None,
        parting: str = "",
        mode: str = "standard",
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewDraft(
            # `AddNewDraft` wants the neutral element, the parting element, the
            # pulling direction and the angle; the faces are added afterwards.
            resolve_support(self, neutral),
            resolve_support(self, parting) if parting else None,
            direction_of(part, pulling_direction) if pulling_direction else None,
            float(angle_deg),
            0,
        )
        try:
            domain = feature.DraftDomains.Item(1)
            for name in faces:
                domain.AddFaceToDraft(self._face_reference(name))
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The draft failed: {exc}. The commonest cause is a neutral element "
                "that does not actually touch the faces being drafted."
            ) from exc
        return self._feature_result(str(feature.Name)) | {"angle_deg": float(angle_deg)}

    def shell_faces(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        thickness_mm: float,
        open_faces: list[str] | None = None,
        outward: bool = False,
        face_thicknesses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Hollow the part and remove the named faces to leave it open.

        The whole point of the `open_faces` argument: `AddNewShell(None, ...)`
        produces a sealed hollow with no way in, which is what the original
        `catia_shell` could do and almost never what was wanted.
        """
        self._require_closed()
        part = self._part()
        inner = 0.0 if outward else float(thickness_mm)
        outer = float(thickness_mm) if outward else 0.0

        faces = list(open_faces or [])
        first = self._face_reference(faces[0]) if faces else None
        feature = part.ShapeFactory.AddNewShell(first, inner, outer)
        try:
            for name in faces[1:]:
                feature.AddFaceToRemove(self._face_reference(name))
            for entry in face_thicknesses or []:
                feature.AddThicknessFace(
                    self._face_reference(str(entry["face"])), float(entry["thickness_mm"])
                )
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The shell failed: {exc}. A wall of {thickness_mm} mm is usually too "
                "thick for the smallest radius in the part — try a thinner wall."
            ) from exc
        return self._feature_result(str(feature.Name)) | {
            "thickness_mm": float(thickness_mm),
            "opened": faces,
        }

    def thickness(  # pragma: no cover - Windows only
        self: ComContext, *, faces: list[str], thickness_mm: float
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewThickness(
            self._face_reference(faces[0]), float(thickness_mm)
        )
        try:
            for name in faces[1:]:
                feature.AddFaceToThickness(self._face_reference(name))
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(f"The thickness change failed: {exc}") from exc
        return self._feature_result(str(feature.Name))

    def remove_face(  # pragma: no cover - Windows only
        self: ComContext, *, faces: list[str], keep_faces: list[str] | None = None
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewRemoveFace(
            self._face_reference(faces[0]), None
        )
        try:
            for name in faces[1:]:
                feature.AddFaceToRemove(self._face_reference(name))
            for name in keep_faces or []:
                feature.AddFaceToKeep(self._face_reference(name))
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"Removing the face failed: {exc}. The surrounding faces must be able "
                "to extend and meet; when they cannot, remove fewer faces at once."
            ) from exc
        return self._feature_result(str(feature.Name))

    def replace_face(  # pragma: no cover - Windows only
        self: ComContext, *, faces: list[str], surface: str, reversed: bool = False  # noqa: A002
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewReplaceFace(
            resolve_element(part, surface), self._face_reference(faces[0])
        )
        try:
            feature.IsInverted = bool(reversed)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"Replacing the face failed: {exc}. The replacing surface has to "
                "overhang the solid on every side, or there is no complete boundary "
                "to trim against."
            ) from exc
        return self._feature_result(str(feature.Name))

    # -- bodies and booleans -------------------------------------------------

    def body_create(  # pragma: no cover - Windows only
        self: ComContext, *, name: str = "", activate: bool = True
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        body = part.Bodies.Add()
        if name:
            try:
                body.Name = name
            except Exception:  # noqa: BLE001 - cosmetic
                pass
        if activate:
            part.InWorkObject = body
            self._active_body = body
        part.Update()
        return {"body": str(body.Name), "active": bool(activate)}

    def body_activate(  # pragma: no cover - Windows only
        self: ComContext, *, body: str
    ) -> dict[str, Any]:
        part = self._part()
        target = resolve_element(part, body)
        part.InWorkObject = target
        self._active_body = target
        return {"body": str(target.Name), "active": True}

    def boolean(  # pragma: no cover - Windows only
        self: ComContext, *, operation: str, tool_body: str, target_body: str = ""
    ) -> dict[str, Any]:
        """Combine two bodies.

        Order is the thing to get right and the thing CATIA's argument names do
        not make obvious: the result replaces the *target*, and the tool body is
        consumed. `remove` with them swapped leaves the cavity and deletes the
        part, which looks like a catastrophic failure and is merely backwards.
        """
        self._require_closed()
        part = self._part()
        tool = resolve_element(part, tool_body)
        target = resolve_element(part, target_body) if target_body else self._body()
        if str(tool.Name) == str(target.Name):
            raise CatiaOperationError(
                f"Cannot combine {tool_body!r} with itself. Name two different bodies."
            )

        call = getattr(part.ShapeFactory, _BOOLEANS[operation])
        feature = call(tool, target)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The boolean {operation} failed: {exc}. The two bodies must actually "
                "intersect for remove and intersect to have a result."
            ) from exc
        return {
            "feature": str(feature.Name),
            "operation": operation,
            "target": str(target.Name),
            "consumed": str(tool.Name),
        }

    def geometrical_set(  # pragma: no cover - Windows only
        self: ComContext, *, name: str = "", ordered: bool = False, activate: bool = True
    ) -> dict[str, Any]:
        part = self._part()
        collection = part.OrderedGeometricalSets if ordered else part.HybridBodies
        created = collection.Add()
        if name:
            try:
                created.Name = name
            except Exception:  # noqa: BLE001 - cosmetic
                pass
        if activate:
            part.InWorkObject = created
        part.Update()
        return {"set": str(created.Name), "ordered": bool(ordered)}

    # -- transformations -----------------------------------------------------

    def translate(  # pragma: no cover - Windows only
        self: ComContext, *, direction: list[float], distance_mm: float, body: str = ""
    ) -> dict[str, Any]:
        return self._transform(
            "AddNewTranslate",
            body,
            direction_of(self._part(), direction),
            float(distance_mm),
        )

    def rotate(  # pragma: no cover - Windows only
        self: ComContext, *, axis: str, angle_deg: float, body: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        return self._transform(
            "AddNewRotate", body, resolve_element(part, axis), float(angle_deg)
        )

    def symmetry(  # pragma: no cover - Windows only
        self: ComContext, *, reference: str, body: str = ""
    ) -> dict[str, Any]:
        return self._transform("AddNewSymmetry", body, resolve_support(self, reference))

    def scale(  # pragma: no cover - Windows only
        self: ComContext, *, reference: str, factor: float, body: str = ""
    ) -> dict[str, Any]:
        return self._transform(
            "AddNewScaling", body, resolve_support(self, reference), float(factor)
        )

    def affinity(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        x_factor: float,
        y_factor: float,
        z_factor: float,
        axis_system: str = "",
        body: str = "",
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        frame = resolve_element(part, axis_system) if axis_system else None
        feature = part.ShapeFactory.AddNewAffinity2(
            frame, float(x_factor), float(y_factor), float(z_factor)
        )
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(f"The affinity failed: {exc}") from exc
        return self._feature_result(str(feature.Name)) | {
            "factors": [float(x_factor), float(y_factor), float(z_factor)]
        }

    def _transform(  # pragma: no cover - Windows only
        self: ComContext, call_name: str, body: str, *arguments: Any
    ) -> dict[str, Any]:
        """Shared shape for the transformation features.

        They differ only in which factory call and which arguments, and every
        one of them needs the same failure handling: a transformation that
        cannot update leaves a broken feature in the tree, and leaving it there
        makes every later operation fail with an error about *this* one.
        """
        self._require_closed()
        part = self._part()
        if body:
            part.InWorkObject = resolve_element(part, body)
        feature = getattr(part.ShapeFactory, call_name)(*arguments)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The transformation failed: {exc}. A transformation applies to a "
                "whole body; check the body actually has material in it."
            ) from exc
        return self._feature_result(str(feature.Name))

    # -- feature tree --------------------------------------------------------

    def feature_rename(  # pragma: no cover - Windows only
        self: ComContext, *, feature: str, name: str
    ) -> dict[str, Any]:
        part = self._part()
        target = resolve_element(part, feature)
        previous = str(target.Name)
        target.Name = name
        return {"feature": name, "was": previous}

    def feature_activate(  # pragma: no cover - Windows only
        self: ComContext, *, feature: str, active: bool
    ) -> dict[str, Any]:
        part = self._part()
        target = resolve_element(part, feature)
        if active:
            part.Activate(target)
        else:
            part.Inactivate(target)
        part.Update()
        return {"feature": str(target.Name), "active": bool(active)}

    def feature_reorder(  # pragma: no cover - Windows only
        self: ComContext, *, feature: str, after: str
    ) -> dict[str, Any]:
        """Move a feature to sit after another in the tree.

        There is no `Reorder` in the automation API — the toolbar command has no
        scriptable equivalent — so this is refused rather than approximated.
        Approximating it would mean delete-and-rebuild, which loses every
        reference into the feature and is a far worse outcome than an honest no.
        """
        raise CatiaOperationError(
            "Reordering the specification tree has no automation equivalent in CATIA "
            "V5, so this bridge cannot do it. Reach it through catia_run_command "
            "('Reorder'), or delete and rebuild the feature in the position you want."
        )

    def feature_parents(  # pragma: no cover - Windows only
        self: ComContext, *, feature: str, depth: int = 1
    ) -> dict[str, Any]:
        """What a feature depends on, and what depends on it.

        Built from the feature list rather than from a dependency call, because
        the automation API has no parent/child reader. Position in the tree is a
        real constraint in a history modeller — a feature can only depend on
        what precedes it — so "everything before it" is a sound over-estimate of
        the parents and "everything after" of the children. Reported as bounds,
        not as facts, and named as such in the payload.
        """
        entries = self._feature_list()
        names = [entry["name"] for entry in entries]
        if feature not in names:
            raise CatiaOperationError(
                f"No feature named {feature!r}. Features: {', '.join(names) or '(none)'}."
            )
        index = names.index(feature)
        window = max(int(depth), 1)
        return {
            "feature": feature,
            "position": index + 1,
            "possible_parents": names[max(0, index - window) : index],
            "possible_children": names[index + 1 : index + 1 + window],
            "note": (
                "CATIA V5 automation exposes no parent/child query. These are the "
                "features immediately before and after in the tree, which bound the "
                "real dependencies rather than stating them."
            ),
        }

    # -- sketch-based features ----------------------------------------------
    #
    # All five take a profile and something to guide it. What separates them is
    # what "guide" means: a rib follows a curve, a stiffener finds its own
    # boundaries against the surrounding material, a loft interpolates between
    # sections, and a solid combine intersects two extrusions.

    def rib(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        profile: str,
        centre_curve: str,
        control: str = "keep_angle",
        reference: str = "",
        thick: bool = False,
    ) -> dict[str, Any]:
        return self._swept_feature(
            "AddNewRib", profile, centre_curve, control, reference, thick=thick
        )

    def slot(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        profile: str,
        centre_curve: str,
        control: str = "keep_angle",
        reference: str = "",
    ) -> dict[str, Any]:
        return self._swept_feature(
            "AddNewSlot", profile, centre_curve, control, reference, thick=False
        )

    def _swept_feature(  # pragma: no cover - Windows only
        self: ComContext,
        call_name: str,
        profile: str,
        centre_curve: str,
        control: str,
        reference: str,
        *,
        thick: bool,
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = getattr(part.ShapeFactory, call_name)(
            resolve_element(part, profile), resolve_element(part, centre_curve)
        )
        try:
            # catRibKeepAngleType = 0, catRibPullingDirectionType = 1,
            # catRibReferenceSurfaceType = 2.
            feature.ProfileControlType = {
                "keep_angle": 0, "pulling_direction": 1, "reference_surface": 2
            }[control]
            if reference:
                feature.ControlElement = resolve_element(part, reference)
            if thick:
                feature.IsThin = True
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The sweep failed: {exc}. The profile must sit on a plane normal to "
                "the start of the centre curve — catia_plane_normal_to_curve builds "
                "that plane."
            ) from exc
        return self._feature_result(str(feature.Name))

    def stiffener(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        profile: str,
        thickness_mm: float,
        symmetric: bool = True,
        reversed: bool = False,  # noqa: A002
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewStiffener(resolve_element(part, profile))
        try:
            feature.Thickness1.Value = float(thickness_mm)
            feature.IsSymmetric = bool(symmetric)
            feature.IsInverted = bool(reversed)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The stiffener failed: {exc}. Its profile is a single open line that "
                "must reach the material on both sides — a closed profile is a rib, "
                "not a stiffener."
            ) from exc
        return self._feature_result(str(feature.Name))

    def multi_section_solid(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        sections: list[str],
        guides: list[str] | None = None,
        spine: str = "",
        closed: bool = False,
        remove: bool = False,
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        factory = part.ShapeFactory
        feature = factory.AddNewRemovedLoft() if remove else factory.AddNewLoft()
        try:
            for section in sections:
                feature.AddSectionToLoft(resolve_element(part, section), 1, None)
            for guide in guides or []:
                feature.AddGuide(resolve_element(part, guide))
            if spine:
                feature.SetSpine(resolve_element(part, spine))
            if closed:
                feature.Closed = True
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The multi-section solid failed: {exc}. Sections that start at "
                "unrelated points make the loft twist — add a guide curve, or check "
                "every section is a closed profile."
            ) from exc
        return self._feature_result(str(feature.Name)) | {"sections": len(sections)}

    def solid_combine(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        first_profile: str,
        second_profile: str,
        first_direction: list[float] | None = None,
        second_direction: list[float] | None = None,
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewSolidCombine(
            resolve_element(part, first_profile), resolve_element(part, second_profile)
        )
        try:
            if first_direction is not None:
                feature.SetDirection(1, direction_of(part, first_direction))
            if second_direction is not None:
                feature.SetDirection(2, direction_of(part, second_direction))
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"The solid combine failed: {exc}. The two extruded profiles must "
                "overlap — where they do not, there is no common volume to keep."
            ) from exc
        return self._feature_result(str(feature.Name))

    def pad_drafted_filleted(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        sketch: str,
        length_mm: float,
        draft_angle_deg: float,
        neutral: str = "",
        lateral_radius_mm: float | None = None,
        top_radius_mm: float | None = None,
        bottom_radius_mm: float | None = None,
    ) -> dict[str, Any]:
        """Pad, draft and fillet in one feature.

        More robust than the three separate features it replaces, and for a
        specific reason: a fillet added afterwards is computed against the
        drafted face, and if the draft angle later changes, a separate fillet
        can fail where this one simply recomputes.
        """
        self._require_closed()
        part = self._part()
        profile = resolve_element(part, sketch)
        neutral_element = resolve_support(self, neutral) if neutral else None

        factory = part.ShapeFactory
        feature = factory.AddNewPad(profile, float(length_mm))
        # Every feature this builds, so a failure part-way can unwind all of
        # them. Leaving a half-built stack behind is worse than not starting:
        # the next Update() fails naming a feature the caller never asked for.
        built: list[Any] = [feature]
        try:
            part.Update()

            draft = factory.AddNewDraft(
                neutral_element or profile, None, None, float(draft_angle_deg), 0
            )
            built.append(draft)
            part.Update()

            for radius in (lateral_radius_mm, top_radius_mm, bottom_radius_mm):
                if not radius:
                    continue
                fillet = factory.AddNewSolidEdgeFilletWithConstantRadius(
                    part.CreateReferenceFromObject(feature), 1, float(radius)
                )
                built.append(fillet)
                part.Update()
        except Exception as exc:  # noqa: BLE001
            for created in reversed(built):
                self._discard_failed_feature(created)
            raise CatiaOperationError(
                f"The drafted filleted pad failed: {exc}. Build it in stages with "
                "catia_pad, catia_draft and catia_fillet_edges to see which step is "
                "the one the geometry refuses."
            ) from exc
        return self._feature_result(str(feature.Name)) | {
            "draft_angle_deg": float(draft_angle_deg),
            "features": [str(created.Name) for created in built],
        }

    def pattern_user(  # pragma: no cover - Windows only
        self: ComContext, *, positions: str, feature: str = "", anchor: str = ""
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        shape = self._shape_or_last(feature or None)
        pattern = part.ShapeFactory.AddNewUserPattern(shape, 1)
        try:
            pattern.SetPositioningSketch(resolve_element(part, positions))
            if anchor:
                pattern.AnchorPoint = resolve_element(part, anchor)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(pattern)
            raise CatiaOperationError(
                f"The user pattern failed: {exc}. The positioning sketch must contain "
                "points, not lines or a profile."
            ) from exc
        return self._feature_result(str(pattern.Name))

    def pattern_explode(  # pragma: no cover - Windows only
        self: ComContext, *, pattern: str
    ) -> dict[str, Any]:
        """Break a pattern into independent features.

        There is no `Explode` on a pattern in the automation API, so this is
        refused rather than approximated. The approximation available — delete
        the pattern and rebuild n copies — silently loses every reference into
        the instances, which is worse than not doing it.
        """
        raise CatiaOperationError(
            "Exploding a pattern has no automation equivalent in CATIA V5, so this "
            "bridge cannot do it. Reach it with catia_run_command ('Explode'), having "
            "selected the pattern with catia_select first."
        )
