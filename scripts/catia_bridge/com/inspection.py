"""Measuring and inspecting what is already modelled.

`SPAWorkbench.GetMeasurable` is the whole of this module. It returns a
`Measurable` for any reference, and which of that object's readers succeed is
what identifies the geometry — there is no "what kind of thing is this" call, so
kind is deduced by probing.

`measure_between` is the one to reach for when checking a clearance. Reading it
off a screenshot is guesswork; `GetMinimumDistance` is the number.
"""

from __future__ import annotations

import logging
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext, resolve_element

logger = logging.getLogger("kryova.catia.com.inspection")


class InspectionMixin:
    """Measurements between and about elements, and part-level analyses."""

    def _measurable(self: ComContext, name: str) -> Any:  # pragma: no cover - Windows only
        part = self._part()
        workbench = part.Parent.GetWorkbench("SPAWorkbench")
        # A face or edge id resolves through the topology search; anything else
        # is a named element. Trying the topology first would make every named
        # lookup pay for a full search.
        if (name.startswith("Face.") or name.startswith("Edge.")) and name.split(".")[-1].isdigit():
            reference = (
                self._face_reference(name)
                if name.startswith("Face.")
                else self._edge_references([name])[0]
            )
        else:
            reference = part.CreateReferenceFromObject(resolve_element(part, name))
        return workbench.GetMeasurable(reference)

    def measure_between(  # pragma: no cover - Windows only
        self: ComContext, *, elements: list[str], kind: str = "minimum_distance"
    ) -> dict[str, Any]:
        if len(elements) != 2:
            raise CatiaOperationError(
                f"A measurement between takes exactly two elements, not {len(elements)}."
            )
        part = self._part()
        first = self._measurable(elements[0])
        second_name = elements[1]
        if (second_name.startswith("Face.") or second_name.startswith("Edge.")) and \
                second_name.split(".")[-1].isdigit():
            other = (
                self._face_reference(second_name)
                if second_name.startswith("Face.")
                else self._edge_references([second_name])[0]
            )
        else:
            other = part.CreateReferenceFromObject(resolve_element(part, second_name))

        if kind == "angle":
            try:
                return {
                    "between": elements,
                    "angle_deg": round(float(first.GetAngleBetween(other)), 6),
                }
            except Exception as exc:  # noqa: BLE001
                raise CatiaOperationError(
                    f"Could not measure an angle between those two: {exc}. An angle "
                    "needs two directional elements — two planes, two lines, two axes."
                ) from exc

        distance = round(float(first.GetMinimumDistance(other)), 6)
        result: dict[str, Any] = {"between": elements, "minimum_distance_mm": distance}
        if kind == "closest_points":
            points = [0.0] * 6
            try:
                first.GetMinimumDistancePoints(other, points)
                result["on_first"] = [round(value, 6) for value in points[0:3]]
                result["on_second"] = [round(value, 6) for value in points[3:6]]
            except Exception:  # noqa: BLE001 - the distance is still the answer
                logger.debug("Closest points unavailable", exc_info=True)
        if distance == 0.0:
            result["note"] = "They touch or intersect; the minimum distance is zero."
        return result

    def measure_item(  # pragma: no cover - Windows only
        self: ComContext, *, element: str
    ) -> dict[str, Any]:
        """Whatever this element can report about itself.

        The kind is reported alongside the numbers rather than assumed, because
        the same call answers very differently for a face and for an edge, and a
        caller comparing an unexpected number against the wrong assumption is
        the failure this avoids.
        """
        measurable = self._measurable(element)
        result: dict[str, Any] = {"element": element}

        for key, reader in (
            ("length_mm", lambda: float(measurable.Length)),
            ("area_mm2", lambda: float(measurable.Area)),
            ("radius_mm", lambda: float(measurable.Radius)),
            ("volume_mm3", lambda: float(measurable.Volume)),
        ):
            try:
                result[key] = round(reader(), 6)
            except Exception:  # noqa: BLE001 - this element has no such property
                continue

        try:
            centre = [0.0] * 3
            measurable.GetCOG(centre)
            result["centre"] = [round(value, 6) for value in centre]
        except Exception:  # noqa: BLE001
            pass

        try:
            plane = [0.0] * 9
            measurable.GetPlane(plane)
            result["normal"] = [round(value, 6) for value in plane[6:9]]
            result["kind"] = "planar face"
        except Exception:  # noqa: BLE001
            pass

        if "kind" not in result:
            if "radius_mm" in result and "area_mm2" in result:
                result["kind"] = "cylindrical face"
            elif "area_mm2" in result:
                result["kind"] = "face"
            elif "length_mm" in result:
                result["kind"] = "edge or curve"
            elif "centre" in result:
                result["kind"] = "point"
            else:
                result["kind"] = "unknown"
                result["note"] = (
                    "CATIA reported nothing measurable for this element. Check the name "
                    "against catia_list_features."
                )
        return result

    def analysis_part(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        direction: str = "",
        minimum_mm: str = "",
        faces: list[str] | None = None,
    ) -> dict[str, Any]:
        """Part-level checks: draft, thickness, curvature, validity.

        Only `validity` is answerable through automation. CATIA's draft,
        thickness and curvature analyses are visual overlays — they colour the
        model on screen and expose no numbers — so asking for them here returns
        an honest refusal that names the route that does work, rather than a
        plausible number this bridge did not actually measure.
        """
        if kind != "validity":
            raise CatiaOperationError(
                f"The {kind} analysis is a screen overlay in CATIA with no automation "
                "API, so no numbers can be returned for it. Run it with "
                "catia_run_command and read the colours with catia_capture_view. "
                "kind 'validity' is measurable and does work here."
            )

        part = self._part()
        problems: list[str] = []
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"The part does not rebuild cleanly: {exc}")

        volume = self._solid_volume()
        if volume <= 0.0:
            problems.append(
                "The part has no positive volume — every feature may have failed, or "
                "a boolean removed everything."
            )
        box = self._bounding_box()
        if box is None:
            problems.append("The part could not be measured, which usually means no solid.")

        return {
            "analysis": "validity",
            "valid": not problems,
            "problems": problems,
            "volume_mm3": round(volume, 4),
            "bounding_box": [round(value, 4) for value in box] if box else None,
        }
