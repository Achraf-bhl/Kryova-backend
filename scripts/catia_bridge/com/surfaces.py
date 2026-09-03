"""Generative Shape Design over COM: surfaces, and the operations joining them.

`HybridShapeFactory` is one object with 132 methods, and it is why this module
covers most of a workbench in a few hundred lines: what the menus present as
four sweep commands is one `AddNewSweep*` family, and Fill, Volume Fill and
Patch are one `AddNewFill`.

Two conventions run through the whole module.

**Everything lands in a geometrical set.** Surfaces and curves cannot live loose
in a part the way solid features can. `append_and_name` puts each result in one
shared set so the tree stays readable, and returns the name the next call will
reference it by.

**A failure is discarded, not left.** A hybrid shape that fails to update stays
in the tree and makes every later `Update()` fail with an error naming *it*,
several operations after the one the user actually got wrong.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import (
    ComContext,
    append_and_name,
    direction_of,
    geometrical_set,
    resolve_element,
)

logger = logging.getLogger("kryova.catia.com.surfaces")

#: `catGSMContinuity`-style values shared by fill, blend and connect.
_CONTINUITY = {"point": 0, "tangent": 1, "curvature": 2}


class SurfacesMixin:
    """Create surfaces, trim them together, and turn them into solids."""

    def _build(  # pragma: no cover - Windows only
        self: ComContext, element: Any, name: str, what: str
    ) -> str:
        """Append, update, and clean up if the update fails."""
        part = self._part()
        created = append_and_name(part, element, name)
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard(self._document(), part, element)
            raise CatiaOperationError(
                f"The {what} could not be built: {exc}. It has been removed so it does "
                "not block later operations."
            ) from exc
        return created

    # -- creating ------------------------------------------------------------

    def surface_extrude(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        profile: str,
        direction: list[float],
        length_mm: float,
        second_length_mm: float = 0.0,
        symmetric: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        back = float(length_mm) if symmetric else float(second_length_mm)
        surface = part.HybridShapeFactory.AddNewExtrude(
            resolve_element(part, profile),
            float(length_mm),
            back,
            direction_of(part, direction),
        )
        return {"surface": self._build(surface, name, "extruded surface")}

    def surface_revolve(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        profile: str,
        axis: str,
        angle_deg: float = 360.0,
        second_angle_deg: float = 0.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        surface = part.HybridShapeFactory.AddNewRevol(
            resolve_element(part, profile),
            float(angle_deg),
            float(second_angle_deg),
            resolve_element(part, axis),
        )
        return {"surface": self._build(surface, name, "surface of revolution")}

    def surface_offset(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        distance_mm: float,
        reversed: bool = False,  # noqa: A002
        both_sides: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        offset = part.HybridShapeFactory.AddNewOffset(
            resolve_element(part, surface), float(distance_mm), 1 if reversed else 0, 0.0
        )
        try:
            offset.BothSidesOffset = bool(both_sides)
        except Exception:  # noqa: BLE001 - not every release exposes it
            pass
        try:
            return {"surface": self._build(offset, name, "offset surface")}
        except CatiaOperationError as exc:
            raise CatiaOperationError(
                f"{exc} An offset larger than the tightest radius of curvature on the "
                "surface has no solution — try a smaller distance."
            ) from exc

    def surface_fill(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        boundary: list[str],
        supports: list[str] | None = None,
        continuity: str = "point",
        passing_point: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        fill = part.HybridShapeFactory.AddNewFill()
        supporting = list(supports or [])
        for index, curve in enumerate(boundary):
            support = supporting[index] if index < len(supporting) else None
            fill.AddBound(resolve_element(part, curve))
            if support is not None:
                fill.SetSupport(resolve_element(part, support), index + 1)
        fill.Continuity = _CONTINUITY.get(continuity, 0)
        if passing_point:
            try:
                fill.SetPassingPoint(resolve_element(part, passing_point))
            except Exception:  # noqa: BLE001 - optional refinement
                logger.debug("Passing point not applied", exc_info=True)
        try:
            return {"surface": self._build(fill, name, "fill surface")}
        except CatiaOperationError as exc:
            raise CatiaOperationError(
                f"{exc} A fill needs a boundary that actually closes — run "
                "catia_surface_analysis with kind 'connect' to find the gap."
            ) from exc

    def surface_loft(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        sections: list[str],
        guides: list[str] | None = None,
        spine: str = "",
        closed: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        loft = part.HybridShapeFactory.AddNewLoft()
        for section in sections:
            loft.AddSectionToLoft(resolve_element(part, section), 1, None)
        for guide in guides or []:
            loft.AddGuide(resolve_element(part, guide))
        if spine:
            loft.SetSpine(resolve_element(part, spine))
        try:
            loft.SectionCoupling = 1  # ratio coupling; the robust default
            if closed:
                loft.Canonical = True
        except Exception:  # noqa: BLE001 - optional refinements
            pass
        try:
            return {"surface": self._build(loft, name, "lofted surface")}
        except CatiaOperationError as exc:
            raise CatiaOperationError(
                f"{exc} A loft twists or fails when its sections start at unrelated "
                "points — add a guide curve, or check the sections run the same way."
            ) from exc

    def surface_sweep(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        guide: str,
        profile: str = "",
        spine: str = "",
        reference_surface: str = "",
        angle_deg: float = 0.0,
        radius_mm: float = 0.0,
        second_guide: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        guide_curve = resolve_element(part, guide)

        if kind == "explicit":
            if not profile:
                raise CatiaOperationError(
                    "An explicit sweep needs a `profile` — the curve being swept. For a "
                    "generated profile use kind 'line', 'circle' or 'conic'."
                )
            sweep = factory.AddNewSweepExplicit(resolve_element(part, profile), guide_curve)
        elif kind == "line":
            sweep = factory.AddNewSweepLine(guide_curve)
        elif kind == "circle":
            sweep = factory.AddNewSweepCircle(guide_curve)
            if radius_mm:
                try:
                    sweep.SetRadius(1, float(radius_mm))
                except Exception:  # noqa: BLE001 - subtype dependent
                    logger.debug("Sweep radius not applied", exc_info=True)
        else:
            sweep = factory.AddNewSweepConic(guide_curve)

        if second_guide:
            try:
                sweep.SetSecondGuideCurve(resolve_element(part, second_guide))
            except Exception:  # noqa: BLE001 - subtype dependent
                logger.debug("Second guide not applied", exc_info=True)
        if spine:
            try:
                sweep.SetSpine(resolve_element(part, spine))
            except Exception:  # noqa: BLE001
                logger.debug("Spine not applied", exc_info=True)
        if reference_surface:
            try:
                sweep.SetReferenceSurface(resolve_element(part, reference_surface))
                sweep.SetAngle(1, float(angle_deg))
            except Exception:  # noqa: BLE001
                logger.debug("Reference surface not applied", exc_info=True)

        return {"surface": self._build(sweep, name, f"{kind} sweep")}

    def surface_blend(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        first_curve: str,
        second_curve: str,
        first_support: str = "",
        second_support: str = "",
        continuity: str = "tangent",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        blend = part.HybridShapeFactory.AddNewBlend()
        blend.SetCurve(1, resolve_element(part, first_curve))
        blend.SetCurve(2, resolve_element(part, second_curve))
        if first_support:
            blend.SetSupport(1, resolve_element(part, first_support))
        if second_support:
            blend.SetSupport(2, resolve_element(part, second_support))
        mode = _CONTINUITY.get(continuity, 1)
        blend.SetContinuity(1, mode)
        blend.SetContinuity(2, mode)
        return {"surface": self._build(blend, name, "blend surface")}

    def surface_primitive(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        radius_mm: float,
        centre: str = "",
        axis: str = "",
        length_mm: float = 0.0,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        if kind == "sphere":
            if not centre:
                raise CatiaOperationError("A sphere needs a `centre` point.")
            element = factory.AddNewSphere(
                resolve_element(part, centre), None, float(radius_mm), -90.0, 90.0, 0.0, 360.0
            )
        else:
            if not axis:
                raise CatiaOperationError("A cylinder needs an `axis` line.")
            element = factory.AddNewCylinder(
                resolve_element(part, axis), float(radius_mm), 0.0, float(length_mm)
            )
        return {"surface": self._build(element, name, kind)}

    # -- operations ----------------------------------------------------------

    def join(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        tolerance_mm: float = 0.0,
        check_connexity: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        resolved = [resolve_element(part, element) for element in elements]
        joined = part.HybridShapeFactory.AddNewJoin(resolved[0], resolved[1])
        for extra in resolved[2:]:
            joined.AddElement(extra)
        if tolerance_mm:
            joined.SetConnexityChecker(bool(check_connexity))
            joined.MergingDistance = float(tolerance_mm)
        try:
            return {"surface": self._build(joined, name, "join"), "joined": len(resolved)}
        except CatiaOperationError as exc:
            raise CatiaOperationError(
                f"{exc} Imported surfaces frequently sit fractions of a millimetre "
                "apart — raise tolerance_mm, or run catia_healing first."
            ) from exc

    def split(  # pragma: no cover - Windows only
        self: ComContext, *, element: str, cutting: str, keep: str = "first", name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        result = part.HybridShapeFactory.AddNewHybridSplit(
            resolve_element(part, element), resolve_element(part, cutting),
            1 if keep == "first" else -1,
        )
        return {"surface": self._build(result, name, "split")}

    def trim(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        keep_first: bool = True,
        keep_second: bool = True,
        name: str = "",
    ) -> dict[str, Any]:
        if len(elements) != 2:
            raise CatiaOperationError(
                f"A trim takes exactly two elements, not {len(elements)}."
            )
        part = self._part()
        first, second = (resolve_element(part, element) for element in elements)
        result = part.HybridShapeFactory.AddNewHybridTrim(
            first, 1 if keep_first else -1, second, 1 if keep_second else -1
        )
        return {"surface": self._build(result, name, "trim")}

    def extract(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        propagation: str = "none",
        complementary: bool = False,
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        mode = {"none": 0, "point_continuity": 1, "tangent": 2}.get(propagation, 0)
        if len(elements) == 1:
            result = part.HybridShapeFactory.AddNewExtract(self._face_reference(elements[0]))
            result.PropagationType = mode
        else:
            result = part.HybridShapeFactory.AddNewExtractMulti(
                self._face_reference(elements[0])
            )
            for name_ in elements[1:]:
                result.AddElementToExtract(self._face_reference(name_))
        try:
            result.ComplementaryExtract = bool(complementary)
        except Exception:  # noqa: BLE001 - not on every subtype
            pass
        return {"surface": self._build(result, name, "extract")}

    def boundary(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        propagation: str = "complete",
        limit_from: str = "",
        limit_to: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        mode = {"complete": 0, "point_continuity": 1, "tangent_continuity": 2}.get(
            propagation, 0
        )
        curve = part.HybridShapeFactory.AddNewBoundaryOfSurface(
            self._face_reference(surface)
        )
        try:
            curve.PropagationType = mode
            if limit_from:
                curve.BeginningElement = resolve_element(part, limit_from)
            if limit_to:
                curve.EndElement = resolve_element(part, limit_to)
        except Exception:  # noqa: BLE001 - limits are optional
            logger.debug("Boundary limits not applied", exc_info=True)
        return {"curve": self._build(curve, name, "boundary")}

    def extrapolate(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        element: str,
        boundary: str,
        length_mm: float = 0.0,
        up_to: str = "",
        continuity: str = "tangent",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        factory = part.HybridShapeFactory
        source = resolve_element(part, element)
        edge = resolve_element(part, boundary)
        if up_to:
            result = factory.AddNewExtrapolUntil(edge, source, resolve_element(part, up_to))
        else:
            result = factory.AddNewExtrapolLength(edge, source, float(length_mm))
        try:
            result.Continuity = _CONTINUITY.get(continuity, 1)
        except Exception:  # noqa: BLE001
            pass
        return {"surface": self._build(result, name, "extrapolation")}

    def healing(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        merging_distance_mm: float = 0.0,
        tangency_angle_deg: float = 0.0,
        continuity: str = "point",
        name: str = "",
    ) -> dict[str, Any]:
        part = self._part()
        heal = part.HybridShapeFactory.AddNewHealing(
            resolve_element(part, elements[0])
        )
        for extra in elements[1:]:
            heal.AddElementToHeal(resolve_element(part, extra))
        if merging_distance_mm:
            heal.MergingDistance = float(merging_distance_mm)
        if tangency_angle_deg:
            heal.TangencyAngle = float(tangency_angle_deg)
        heal.Continuity = _CONTINUITY.get(continuity, 0)
        return {"surface": self._build(heal, name, "healing"), "healed": len(elements)}

    def untrim(  # pragma: no cover - Windows only
        self: ComContext, *, surface: str, name: str = ""
    ) -> dict[str, Any]:
        part = self._part()
        result = part.HybridShapeFactory.AddNewUnTrim(resolve_element(part, surface))
        return {"surface": self._build(result, name, "untrim")}

    def disassemble(  # pragma: no cover - Windows only
        self: ComContext, *, element: str, mode: str = "domains"
    ) -> dict[str, Any]:
        """Break a multi-cell element into pieces.

        `Disassemble` is a `HybridShapeFactory` method that creates several
        elements at once and returns none of them, so the result is found by
        diffing the geometrical set before and after. That is ugly and it is
        what the API offers.
        """
        part = self._part()
        target = geometrical_set(part)
        before = {
            str(target.HybridShapes.Item(i).Name)
            for i in range(1, int(target.HybridShapes.Count) + 1)
        }
        part.HybridShapeFactory.Disassemble(
            resolve_element(part, element), 1 if mode == "all_cells" else 0, True
        )
        part.Update()
        after = [
            str(target.HybridShapes.Item(i).Name)
            for i in range(1, int(target.HybridShapes.Count) + 1)
        ]
        created = [name for name in after if name not in before]
        return {"pieces": created, "count": len(created)}

    # -- surface to solid ----------------------------------------------------

    def close_surface(  # pragma: no cover - Windows only
        self: ComContext, *, surface: str
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewCloseSurface(resolve_element(part, surface))
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"Could not fill the surface with material: {exc}. The surface has to "
                "be genuinely closed — join every piece, then run catia_healing if a "
                "gap remains."
            ) from exc
        return self._feature_result(str(feature.Name))

    def thick_surface(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        thickness_mm: float,
        second_thickness_mm: float = 0.0,
        reversed: bool = False,  # noqa: A002
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        first = float(second_thickness_mm) if reversed else float(thickness_mm)
        second = float(thickness_mm) if reversed else float(second_thickness_mm)
        feature = part.ShapeFactory.AddNewThickSurface(
            resolve_element(part, surface), 0, first, second
        )
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(
                f"Could not thicken the surface: {exc}. A thickness larger than the "
                "surface's tightest radius self-intersects — try a thinner wall."
            ) from exc
        return self._feature_result(str(feature.Name))

    def sew_surface(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        surface: str,
        remove: bool = False,
        reversed: bool = False,  # noqa: A002
    ) -> dict[str, Any]:
        self._require_closed()
        part = self._part()
        feature = part.ShapeFactory.AddNewSewSurface(
            resolve_element(part, surface), not remove, bool(reversed)
        )
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(feature)
            raise CatiaOperationError(f"Could not sew the surface: {exc}") from exc
        return self._feature_result(str(feature.Name))

    # -- analysis ------------------------------------------------------------

    def surface_analysis(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        elements: list[str],
        direction: list[float] | None = None,
        tolerance_mm: float = 0.0,
    ) -> dict[str, Any]:
        """Measure surface quality.

        CATIA's shape-analysis commands are visual overlays with no automation
        API — there is no call that returns a curvature map as numbers. What is
        measurable through automation is the *connect* case, and that is the one
        that decides whether a downstream close or thicken will work, so it is
        implemented and the others say plainly that they are not.
        """
        if kind != "connect":
            raise CatiaOperationError(
                f"The {kind} analysis is a visual overlay in CATIA with no automation "
                "equivalent, so this bridge cannot return numbers for it. Run it "
                "through catia_run_command and read the result with catia_capture_view, "
                "or use kind 'connect', which is measurable."
            )
        if len(elements) < 2:
            raise CatiaOperationError(
                "A connect analysis compares two or more elements; give at least two."
            )
        part = self._part()
        workbench = part.Parent.GetWorkbench("SPAWorkbench")
        gaps: list[dict[str, Any]] = []
        first = resolve_element(part, elements[0])
        measurable = workbench.GetMeasurable(part.CreateReferenceFromObject(first))
        for other in elements[1:]:
            target = resolve_element(part, other)
            gap = float(
                measurable.GetMinimumDistance(part.CreateReferenceFromObject(target))
            )
            gaps.append(
                {
                    "between": [elements[0], other],
                    "gap_mm": round(gap, 6),
                    "within_tolerance": gap <= float(tolerance_mm) if tolerance_mm else None,
                }
            )
        return {"analysis": "connect", "gaps": gaps}
