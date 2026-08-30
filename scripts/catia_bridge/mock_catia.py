"""A complete in-memory CATIA, so the whole system is testable without CATIA.

This is not a stub that returns `{"ok": true}`. It keeps a real part model --
named parameters with units, an ordered feature tree, a solid whose bounding box
and mass actually change when you pad or pocket it -- and it produces real
artefacts: a valid AP214 STEP file that OpenCASCADE reads and gmsh meshes, and a
decodable PNG of the part.

That fidelity is the point. With it, every server-side path, every agent tool,
every UI state and the entire test suite exercise the same code on a Linux
laptop that they will exercise against CATIA on Windows; only the COM calls
themselves stay unverified until someone runs it there. With a stub instead, the
first real run would be the first time the STEP path, the blob store, the
checkpoint round trip and the geometry-version creation had ever executed, and
they would all be discovered broken at once, on the machine least convenient for
debugging.

The solid model is deliberately a box that pads and pockets adjust. It is not a
modelling kernel and does not pretend to be: a pocket removes its swept volume
from the mass rather than actually cutting the shape, and `catia_measure` says
so through `approximate: true`. Being obviously approximate is better than being
subtly wrong -- nobody should ever mistake a mock mass for a real one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .backend import CatiaBackend, CatiaOperationError
from .png_writer import Canvas
from .step_writer import write_box_step

#: Steel, matching the solver's library so a mock mass is at least plausible.
_DENSITY_KG_PER_MM3 = 7850e-9

_PLANE_AXES = {"XY": (0, 1, 2), "YZ": (1, 2, 0), "ZX": (2, 0, 1)}


class _Sketch:
    def __init__(self, name: str, plane: str, shape: str, size: tuple[float, float]) -> None:
        self.name = name
        self.plane = plane
        self.shape = shape
        self.size = size
        self.consumed = False

    def area_mm2(self) -> float:
        if self.shape == "circle":
            return math.pi * (self.size[0] / 2.0) ** 2
        if self.shape.startswith("polygon-"):
            sides = int(self.shape.split("-", 1)[1])
            radius = self.size[0] / 2.0
            return 0.5 * sides * radius**2 * math.sin(2 * math.pi / sides)
        return self.size[0] * self.size[1]


class MockCatia(CatiaBackend):
    catia_version = "V5-6R2021 (mock)"
    is_mock = True
    capabilities = ("part", "sketch", "measure", "export", "capture", "checkpoint")

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)
        self.documents = self.workdir / "documents"
        self.snapshots = self.workdir / "snapshots"
        for directory in (self.documents, self.snapshots):
            directory.mkdir(parents=True, exist_ok=True)
        self._reset()

    # -- internal state ------------------------------------------------------

    def _reset(self) -> None:
        self.doc_name: str | None = None
        self.doc_path: Path | None = None
        self.parameters: dict[str, dict[str, Any]] = {}
        self.features: list[dict[str, Any]] = []
        self.sketches: dict[str, _Sketch] = {}
        #: Bounding-box extents in mm. None until something is padded.
        self.size: tuple[float, float, float] | None = None
        self.removed_volume_mm3 = 0.0
        #: None until catia_set_material; the mock then weighs the part with it,
        #: exactly as the real backend does.
        self.material: str | None = None
        self.density_kg_m3 = _DENSITY_KG_PER_MM3 * 1e9
        self.up_to_date = True
        self._counters: dict[str, int] = {}

    def _name(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}.{self._counters[prefix]}"

    def _require_document(self) -> None:
        if self.doc_name is None:
            raise CatiaOperationError(
                "No document is open in CATIA. Call catia_new_part to start one, or "
                "catia_open_document to reopen this conversation's part."
            )

    def _require_solid(self) -> None:
        self._require_document()
        if self.size is None:
            raise CatiaOperationError(
                "The part has no solid geometry yet. Sketch a profile and pad it first."
            )

    def health(self) -> None:
        """Always healthy. There is no CATIA to wedge."""

    # -- documents -----------------------------------------------------------

    def new_part(self, *, name: str) -> dict[str, Any]:
        self._reset()
        self.doc_name = name
        self.doc_path = self.documents / f"{_safe_filename(name)}.CATPart"
        self._write_document()
        return {
            "doc_name": self.doc_name,
            "remote_path": str(self.doc_path),
            "features": [],
            "up_to_date": True,
        }

    def open_document(
        self,
        *,
        doc_name: str | None = None,
        remote_path: str | None = None,
        fallback_checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = Path(remote_path) if remote_path else None
        restored = False

        if path is None or not path.is_file():
            # The workstation lost the file -- a cleaned temp directory, a
            # reimaged laptop. This is exactly what the cloud checkpoint copy is
            # for, and using it is what makes "resume tomorrow" survive.
            if not fallback_checkpoint or not fallback_checkpoint.get("content_b64"):
                raise CatiaOperationError(
                    "The document is not on this workstation and no stored checkpoint "
                    "is available to restore it from. Start a new part instead."
                )
            path = self.documents / f"{_safe_filename(doc_name or 'Part')}.CATPart"
            path.write_bytes(base64.b64decode(fallback_checkpoint["content_b64"]))
            restored = True

        self._reset()
        self._load_document(path)
        self.doc_name = self.doc_name or doc_name or path.stem
        self.doc_path = path
        return {
            "doc_name": self.doc_name,
            "remote_path": str(path),
            "restored_from_checkpoint": restored,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    # -- parameters ----------------------------------------------------------

    def list_parameters(self) -> dict[str, Any]:
        self._require_document()
        return {
            "parameters": [
                {
                    "name": name,
                    "value": entry["value"],
                    "unit": entry["unit"],
                    "expression": f"{entry['value']:g}{entry['unit']}",
                    "comment": entry.get("comment", ""),
                }
                for name, entry in sorted(self.parameters.items())
            ]
        }

    def set_parameter(self, *, name: str, value: float, unit: str) -> dict[str, Any]:
        self._require_document()
        existing = self.parameters.get(name)
        if existing is None:
            known = ", ".join(sorted(self.parameters)) or "(none defined yet)"
            raise CatiaOperationError(
                f"No parameter named {name!r} in this part. Defined parameters: {known}."
            )
        if existing["unit"] != unit:
            raise CatiaOperationError(
                f"Parameter {name!r} is in {existing['unit']}, not {unit}. CATIA "
                "parameters are typed and setting the wrong unit does nothing."
            )
        previous = existing["value"]
        existing["value"] = float(value)
        self._apply_driving_parameter(name, float(value))
        self._write_document()
        return {
            "parameter": {"name": name, "value": float(value), "unit": unit},
            "previous_value": previous,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    def _apply_driving_parameter(self, name: str, value: float) -> None:
        """Re-drive the solid when a dimension the pad depends on changes.

        Crude, and honest about it: only the three extents are driving. It is
        enough to make `catia_set_parameter` -> `catia_measure` show a real
        change, which is the behaviour every layer above depends on.
        """
        if self.size is None:
            return
        axis = {"Width": 0, "Depth": 1, "Height": 2, "Length": 2, "Thickness": 2}.get(name)
        if axis is None:
            return
        extents = list(self.size)
        extents[axis] = max(value, 1e-3)
        self.size = (extents[0], extents[1], extents[2])

    # -- material ------------------------------------------------------------

    def set_material(self, *, material: str, density_kg_m3: float) -> dict[str, Any]:
        self._require_document()
        self.material = material
        self.density_kg_m3 = float(density_kg_m3)
        self._write_document()
        return {
            "material": material,
            "density_kg_m3": round(float(density_kg_m3), 1),
            # No catalogue here, and saying so beats claiming a CATIA-side
            # application that never happened -- the mock's whole value is that
            # it does not lie about what it did.
            "applied_in_catia": False,
            "detail": "Mock CATIA: the material is recorded but nothing is attached.",
            **self._solid_summary(),
        }

    # -- sketches ------------------------------------------------------------

    def sketch_rectangle(self, *, plane: str, width_mm: float, height_mm: float) -> dict[str, Any]:
        return self._add_sketch(plane, "rectangle", (width_mm, height_mm))

    def sketch_circle(self, *, plane: str, diameter_mm: float) -> dict[str, Any]:
        return self._add_sketch(plane, "circle", (diameter_mm, diameter_mm))

    def sketch_polygon(self, *, plane: str, sides: int, diameter_mm: float) -> dict[str, Any]:
        result = self._add_sketch(plane, f"polygon-{sides}", (diameter_mm, diameter_mm))
        radius = diameter_mm / 2.0
        result["area_mm2"] = round(0.5 * sides * radius**2 * math.sin(2 * math.pi / sides), 4)
        return result

    def _add_sketch(self, plane: str, shape: str, size: tuple[float, float]) -> dict[str, Any]:
        self._require_document()
        if plane not in _PLANE_AXES:
            raise CatiaOperationError(f"{plane!r} is not one of the XY, YZ, ZX planes.")
        sketch = _Sketch(self._name("Sketch"), plane, shape, size)
        self.sketches[sketch.name] = sketch
        self.features.append({"name": sketch.name, "type": "Sketch", "plane": plane})
        self._write_document()
        return {
            "feature": sketch.name,
            "sketch": sketch.name,
            "plane": plane,
            "shape": shape,
            "area_mm2": round(sketch.area_mm2(), 4),
            "features": self._feature_names(),
        }

    def _sketch(self, name: str) -> _Sketch:
        sketch = self.sketches.get(name)
        if sketch is None:
            known = ", ".join(sorted(self.sketches)) or "(none)"
            raise CatiaOperationError(f"No sketch named {name!r}. Sketches in this part: {known}.")
        return sketch

    # -- features ------------------------------------------------------------

    def pad(
        self,
        *,
        sketch: str,
        length_mm: float,
        symmetric: bool = False,
        reversed: bool = False,  # noqa: A002 - protocol field name
    ) -> dict[str, Any]:
        self._require_document()
        profile = self._sketch(sketch)
        if profile.consumed:
            raise CatiaOperationError(
                f"{sketch} has already been used by another feature. Sketch a new "
                "profile rather than reusing one."
            )
        profile.consumed = True

        # The pad's extents in the part frame: the sketch's two in-plane sizes,
        # and the pad length along the plane normal.
        in_a, in_b, normal = _PLANE_AXES[profile.plane]
        extents = [0.0, 0.0, 0.0]
        extents[in_a], extents[in_b] = profile.size[0], profile.size[1]
        extents[normal] = length_mm

        if self.size is None:
            self.size = (extents[0], extents[1], extents[2])
        else:
            # Padding onto an existing solid grows the bounding box.
            self.size = tuple(max(a, b) for a, b in zip(self.size, extents))  # type: ignore[assignment]

        name = self._name("Pad")
        self.parameters.setdefault(
            "Length", {"value": float(length_mm), "unit": "mm", "comment": "Pad length"}
        )
        self._record_extent_parameters()
        self.features.append(
            {
                "name": name,
                "type": "Pad",
                "sketch": sketch,
                "length_mm": length_mm,
                "symmetric": bool(symmetric),
                "reversed": bool(reversed),
            }
        )
        return self._mutation_result(name)

    def pocket(
        self, *, sketch: str, depth_mm: float | None = None, through_all: bool = False
    ) -> dict[str, Any]:
        self._require_solid()
        profile = self._sketch(sketch)
        if profile.consumed:
            raise CatiaOperationError(f"{sketch} has already been used by another feature.")
        profile.consumed = True

        assert self.size is not None  # noqa: S101 - _require_solid guarantees it
        _, _, normal = _PLANE_AXES[profile.plane]
        depth = self.size[normal] if through_all else float(depth_mm or 0.0)
        if not through_all and depth <= 0:
            raise CatiaOperationError(
                "catia_pocket needs either depth_mm or through_all; neither was given."
            )
        removed = profile.area_mm2() * depth
        self._remove_volume(removed, "pocket")

        name = self._name("Pocket")
        self.features.append(
            {
                "name": name,
                "type": "Pocket",
                "sketch": sketch,
                "depth_mm": depth,
                "through_all": bool(through_all),
                "removed_mm3": removed,
            }
        )
        return self._mutation_result(name)

    def hole(
        self,
        *,
        face: str,
        position: str,
        diameter_mm: float,
        depth_mm: float | None = None,
        through_all: bool = True,
        inset_mm: float | None = None,
    ) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        axis = {"top": 2, "bottom": 2, "front": 1, "back": 1, "left": 0, "right": 0}[face]
        depth = self.size[axis] if through_all else float(depth_mm or 0.0)
        if depth <= 0:
            raise CatiaOperationError(
                "catia_hole needs either depth_mm or through_all; neither was given."
            )
        if diameter_mm >= min(self.size):
            raise CatiaOperationError(
                f"A {diameter_mm:g} mm hole does not fit in a part whose smallest "
                f"dimension is {min(self.size):g} mm."
            )
        removed = math.pi * (diameter_mm / 2) ** 2 * depth
        self._remove_volume(removed, "hole")

        name = self._name("Hole")
        self.parameters.setdefault(
            f"{name}_Diameter",
            {"value": float(diameter_mm), "unit": "mm", "comment": f"{face} {position} hole"},
        )
        self.features.append(
            {
                "name": name,
                "type": "Hole",
                "face": face,
                "position": position,
                "diameter_mm": diameter_mm,
                "depth_mm": depth,
                "through_all": bool(through_all),
                "removed_mm3": removed,
            }
        )
        return self._mutation_result(name)

    def fillet(
        self, *, radius_mm: float, feature: str | None = None, edges: str = "all"
    ) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        if radius_mm > min(self.size) / 2:
            raise CatiaOperationError(
                f"A {radius_mm:g} mm fillet is larger than half the part's smallest "
                f"dimension ({min(self.size):g} mm) and would consume the face."
            )
        # (2 - pi/2) r^2 per unit length is the corner material a round removes.
        count = {"all": 12, "vertical": 4, "horizontal": 8, "top": 4, "bottom": 4}[edges]
        removed = (2 - math.pi / 2) * radius_mm**2 * (sum(self.size) / 3) * count / 12
        self._remove_volume(removed, "fillet")
        name = self._name("EdgeFillet")
        self.features.append(
            {
                "name": name,
                "type": "EdgeFillet",
                "radius_mm": radius_mm,
                "edges": edges,
                "on_feature": feature,
                "removed_mm3": removed,
            }
        )
        return self._mutation_result(name)

    def chamfer(
        self,
        *,
        length_mm: float,
        angle_deg: float = 45.0,
        feature: str | None = None,
        edges: str = "all",
    ) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        count = {"all": 12, "vertical": 4, "horizontal": 8, "top": 4, "bottom": 4}[edges]
        area = 0.5 * length_mm**2 * math.tan(math.radians(angle_deg))
        removed = area * (sum(self.size) / 3) * count / 12
        self._remove_volume(removed, "chamfer")
        name = self._name("Chamfer")
        self.features.append(
            {
                "name": name,
                "type": "Chamfer",
                "length_mm": length_mm,
                "angle_deg": angle_deg,
                "edges": edges,
                "on_feature": feature,
                "removed_mm3": removed,
            }
        )
        return self._mutation_result(name)

    def shaft(self, *, sketch: str, angle_deg: float = 360.0) -> dict[str, Any]:
        self._require_document()
        profile = self._sketch(sketch)
        if profile.consumed:
            raise CatiaOperationError(f"{sketch} has already been used by another feature.")
        profile.consumed = True

        # Revolving about the sketch's vertical axis: a rectangle becomes a
        # cylinder, a circle a sphere. Extents follow from the profile size --
        # crude, and `catia_measure` already says the mock is approximate.
        in_a, in_b, normal = _PLANE_AXES[profile.plane]
        diameter, height = profile.size[0], profile.size[1]
        extents = [0.0, 0.0, 0.0]
        extents[in_a] = diameter
        extents[normal] = diameter
        extents[in_b] = height

        if profile.shape == "circle":
            volume = math.pi * diameter**3 / 6.0  # sphere
            extents[in_b] = diameter
        else:
            volume = math.pi * (diameter / 2.0) ** 2 * height  # cylinder
        volume *= min(max(angle_deg, 0.0), 360.0) / 360.0

        if self.size is None:
            self.size = (extents[0], extents[1], extents[2])
        else:
            self.size = tuple(max(a, b) for a, b in zip(self.size, extents))  # type: ignore[assignment]
        # Fold the revolve into the box-minus-cuts model: the box grew by the
        # shaft's extents, so the difference between that growth and the true
        # swept volume is recorded as removed material.
        self.removed_volume_mm3 = max(
            self._gross_volume_mm3() - (self._net_volume_mm3() + volume), 0.0
        )

        name = self._name("Shaft")
        self._record_extent_parameters()
        self.features.append(
            {"name": name, "type": "Shaft", "sketch": sketch, "angle_deg": float(angle_deg)}
        )
        return self._mutation_result(name)

    def groove(self, *, sketch: str, angle_deg: float = 360.0) -> dict[str, Any]:
        self._require_solid()
        profile = self._sketch(sketch)
        if profile.consumed:
            raise CatiaOperationError(f"{sketch} has already been used by another feature.")
        profile.consumed = True

        diameter, height = profile.size[0], profile.size[1]
        volume = math.pi * (diameter / 2.0) ** 2 * height
        volume *= min(max(angle_deg, 0.0), 360.0) / 360.0
        self._remove_volume(volume, "groove")

        name = self._name("Groove")
        self.features.append(
            {
                "name": name,
                "type": "Groove",
                "sketch": sketch,
                "angle_deg": float(angle_deg),
                "removed_mm3": volume,
            }
        )
        return self._mutation_result(name)

    def mirror(self, *, plane: str) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        _, _, normal = _PLANE_AXES[plane]
        extents = list(self.size)
        extents[normal] *= 2.0
        self.size = (extents[0], extents[1], extents[2])
        # Cuts mirror with the material, so removed volume doubles too.
        self.removed_volume_mm3 *= 2.0

        name = self._name("Mirror")
        self._record_extent_parameters()
        self.features.append({"name": name, "type": "Mirror", "plane": plane})
        return self._mutation_result(name)

    def delete_feature(self, *, feature: str) -> dict[str, Any]:
        self._require_document()
        entry = next((f for f in self.features if f["name"] == feature), None)
        if entry is None:
            known = ", ".join(f["name"] for f in self.features) or "(none)"
            raise CatiaOperationError(
                f"No feature named {feature!r} in this part. Features: {known}."
            )
        if entry["type"] in {"Pad", "Shaft", "Mirror"}:
            # The mock's solid is a box less its cuts; it cannot recompute the
            # box after losing an additive feature. Being unable is better than
            # being silently wrong about the part's mass.
            raise CatiaOperationError(
                f"The mock backend cannot recompute the solid after deleting the "
                f"additive feature {feature!r}. Restore the checkpoint taken before "
                "it instead (real CATIA can delete it directly)."
            )

        removed = entry.get("removed_mm3")
        if isinstance(removed, (int, float)):
            self.removed_volume_mm3 = max(self.removed_volume_mm3 - float(removed), 0.0)
        self.features = [f for f in self.features if f["name"] != feature]
        if entry["type"] == "Sketch":
            self.sketches.pop(feature, None)
        return {
            "deleted": feature,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    def list_features(self) -> dict[str, Any]:
        self._require_document()
        return {"features": self._feature_names()}

    def update(self) -> dict[str, Any]:
        self._require_document()
        self.up_to_date = True
        self._write_document()
        return {"updated": True, "features": self._feature_names(), **self._solid_summary()}

    def _remove_volume(self, volume_mm3: float, what: str) -> None:
        gross = self._gross_volume_mm3()
        if self.removed_volume_mm3 + volume_mm3 >= gross:
            raise CatiaOperationError(
                f"That {what} removes more material than the part contains. Check the "
                "dimensions -- the result would be an empty solid."
            )
        self.removed_volume_mm3 += volume_mm3

    def _mutation_result(self, feature_name: str) -> dict[str, Any]:
        """Every mutating tool returns rich post-state, never just 'ok'.

        The agent is prompted to react to what it sees, and it cannot react to a
        boolean. Mass and bounding box come back with the feature name so one
        round trip is usually enough to notice a mistake.
        """
        self._write_document()
        return {
            "feature": feature_name,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    # -- inspection ----------------------------------------------------------

    def _gross_volume_mm3(self) -> float:
        return math.prod(self.size) if self.size else 0.0

    def _net_volume_mm3(self) -> float:
        return max(self._gross_volume_mm3() - self.removed_volume_mm3, 0.0)

    def _solid_summary(self) -> dict[str, Any]:
        if self.size is None:
            return {"has_solid": False, "mass_kg": 0.0, "bounding_box_mm": None}
        volume = self._net_volume_mm3()
        return {
            "has_solid": True,
            # Kilograms, as the rest of the system expects. Nothing converts on
            # the way up; this is already the number the UI shows.
            "mass_kg": round(volume * self.density_kg_m3 * 1e-9, 6),
            # Reported alongside the mass because the real backend reports it,
            # and because the mass means nothing without it.
            "density_kg_m3": round(self.density_kg_m3, 1),
            "material": self.material,
            "material_applied": True,
            "mass_is_provisional": False,
            "volume_mm3": round(volume, 4),
            "bounding_box_mm": {
                "min": [0.0, 0.0, 0.0],
                "max": [round(v, 4) for v in self.size],
                "size": [round(v, 4) for v in self.size],
            },
        }

    def measure(self) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        summary = self._solid_summary()
        width, depth, height = self.size
        return {
            **summary,
            "surface_area_mm2": round(2 * (width * depth + depth * height + width * height), 4),
            "center_of_gravity_mm": [round(v / 2, 4) for v in self.size],
            "features": self._feature_names(),
            "material": "Steel (mock default)",
            # Never let a mock number be mistaken for a measured one.
            "approximate": True,
            "note": (
                "Mock CATIA: mass and volume are computed from the bounding box less "
                "the swept volume of each cut, not from a real B-rep."
            ),
        }

    def capture_view(
        self, *, view: str = "iso", label: str = "", max_inline_bytes: int | None = None
    ) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        png = _render_box_png(self.size, view=view, caption=label or (self.doc_name or "Part"))
        if max_inline_bytes is not None and len(png) > max_inline_bytes:  # pragma: no cover
            raise CatiaOperationError("The rendered view is too large to transfer.")
        return {
            "filename": f"{_safe_filename(self.doc_name or 'part')}-{view}.png",
            "view": view,
            "width_px": _VIEW_WIDTH,
            "height_px": _VIEW_HEIGHT,
            "size_bytes": len(png),
            "sha256": hashlib.sha256(png).hexdigest(),
            "content_b64": base64.b64encode(png).decode("ascii"),
        }

    # -- transfer and safety -------------------------------------------------

    def export_step(
        self, *, note: str | None = None, max_inline_bytes: int | None = None
    ) -> dict[str, Any]:
        self._require_solid()
        assert self.size is not None  # noqa: S101
        # A real export re-tessellates and takes appreciable time; a mock that
        # returns instantly hides every ordering bug the timeout logic exists
        # for. A tenth of a second is enough to be real without being annoying.
        time.sleep(0.1)
        text = write_box_step(size_mm=self.size, part_name=self.doc_name or "Part")
        data = text.encode("utf-8")
        if max_inline_bytes is not None and len(data) > max_inline_bytes:  # pragma: no cover
            raise CatiaOperationError("The exported STEP file is too large to transfer.")
        return {
            "filename": f"{_safe_filename(self.doc_name or 'part')}.step",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_b64": base64.b64encode(data).decode("ascii"),
            "note": note,
        }

    def checkpoint(self, *, label: str, max_inline_bytes: int | None = None) -> dict[str, Any]:
        self._require_document()
        self._write_document()
        assert self.doc_path is not None  # noqa: S101
        reference = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        snapshot = self.snapshots / f"{reference}.CATPart"
        shutil.copyfile(self.doc_path, snapshot)

        data = snapshot.read_bytes()
        # Above the ceiling the snapshot still exists locally and the server
        # records the checkpoint without a cloud copy, rather than the mutation
        # being refused outright.
        inline = max_inline_bytes is None or len(data) <= max_inline_bytes
        return {
            "remote_ref": str(snapshot),
            "doc_name": self.doc_name,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "inline": inline,
            "content_b64": base64.b64encode(data).decode("ascii") if inline else None,
        }

    def restore(self, *, checkpoint: dict[str, Any]) -> dict[str, Any]:
        self._require_document()
        source: Path | None = None
        reference = checkpoint.get("remote_ref")
        if isinstance(reference, str) and Path(reference).is_file():
            source = Path(reference)

        if source is None:
            content = checkpoint.get("content_b64")
            if not content:
                raise CatiaOperationError(
                    "That checkpoint is not on this workstation and the server holds no "
                    "copy of it, so it cannot be restored."
                )
            source = self.snapshots / f"restore-{uuid.uuid4().hex[:8]}.CATPart"
            source.write_bytes(base64.b64decode(content))

        assert self.doc_path is not None  # noqa: S101
        shutil.copyfile(source, self.doc_path)
        self._load_document(self.doc_path)
        return {
            "restored": True,
            "doc_name": self.doc_name,
            "features": self._feature_names(),
            **self._solid_summary(),
        }

    # -- persistence ---------------------------------------------------------

    def _feature_names(self) -> list[dict[str, Any]]:
        return [{"name": f["name"], "type": f["type"]} for f in self.features]

    def _record_extent_parameters(self) -> None:
        if self.size is None:
            return
        for name, value in zip(("Width", "Depth", "Height"), self.size):
            self.parameters.setdefault(
                name, {"value": round(value, 4), "unit": "mm", "comment": "Overall size"}
            )
            self.parameters[name]["value"] = round(value, 4)

    def _write_document(self) -> None:
        """Persist the part. A checkpoint is a copy of this file, as in CATIA."""
        if self.doc_path is None:
            return
        self.doc_path.write_text(
            json.dumps(
                {
                    "doc_name": self.doc_name,
                    "parameters": self.parameters,
                    "features": self.features,
                    "sketches": {
                        name: {
                            "plane": s.plane,
                            "shape": s.shape,
                            "size": list(s.size),
                            "consumed": s.consumed,
                        }
                        for name, s in self.sketches.items()
                    },
                    "size": list(self.size) if self.size else None,
                    "removed_volume_mm3": self.removed_volume_mm3,
                    "counters": self._counters,
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    def _load_document(self, path: Path) -> None:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatiaOperationError(f"The document could not be read: {exc}") from exc
        self.doc_name = state.get("doc_name")
        self.doc_path = path
        self.parameters = state.get("parameters") or {}
        self.features = state.get("features") or []
        self.sketches = {
            name: _restore_sketch(name, entry)
            for name, entry in (state.get("sketches") or {}).items()
        }
        size = state.get("size")
        self.size = tuple(size) if size else None  # type: ignore[assignment]
        self.removed_volume_mm3 = float(state.get("removed_volume_mm3") or 0.0)
        self._counters = state.get("counters") or {}
        self.up_to_date = True


def _restore_sketch(name: str, entry: dict[str, Any]) -> _Sketch:
    sketch = _Sketch(name, entry["plane"], entry["shape"], tuple(entry["size"]))
    sketch.consumed = bool(entry.get("consumed"))
    return sketch


def _safe_filename(name: str) -> str:
    kept = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name).strip("-")
    return kept[:64] or "part"


# -- the preview image -------------------------------------------------------

_VIEW_WIDTH, _VIEW_HEIGHT = 640, 480
_BACKGROUND = (247, 248, 250)
_FACE_TOP = (150, 178, 214)
_FACE_LEFT = (108, 140, 182)
_FACE_RIGHT = (86, 114, 152)
_EDGE = (32, 42, 58)
_AXIS = (196, 202, 212)


def _render_box_png(size: tuple[float, float, float], *, view: str, caption: str) -> bytes:
    """Draw the part's bounding solid from a standard viewpoint.

    A wireframe box with shaded faces, which is genuinely what the part *is* in
    mock mode. Drawing something more elaborate would misrepresent how much the
    mock knows.
    """
    canvas = Canvas(_VIEW_WIDTH, _VIEW_HEIGHT, _BACKGROUND)
    width, depth, height = size
    corners = [(x * width, y * depth, z * height) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
    projected = [_project(c, view) for c in corners]

    span = max(
        max(px for px, _ in projected) - min(px for px, _ in projected),
        max(py for _, py in projected) - min(py for _, py in projected),
        1e-6,
    )
    scale = min(_VIEW_WIDTH, _VIEW_HEIGHT) * 0.62 / span
    cx = (max(px for px, _ in projected) + min(px for px, _ in projected)) / 2
    cy = (max(py for _, py in projected) + min(py for _, py in projected)) / 2
    screen = [
        (
            int(_VIEW_WIDTH / 2 + (px - cx) * scale),
            # Screen y grows downward; model "up" should not render upside down.
            int(_VIEW_HEIGHT / 2 - (py - cy) * scale),
        )
        for px, py in projected
    ]

    # Corner order is (x, y, z) with z fastest, so index = 4x + 2y + z.
    quads = [
        ([1, 3, 7, 5], _FACE_RIGHT),  # x = max
        ([2, 3, 7, 6], _FACE_LEFT),  # y = max
        ([1, 3, 2, 0], _FACE_TOP) if view in {"bottom"} else ([5, 7, 6, 4], _FACE_TOP),
    ]
    for indices, colour in quads:
        canvas.fill_polygon([screen[i] for i in indices], colour)

    for a, b in _BOX_EDGES:
        canvas.line(*screen[a], *screen[b], _EDGE, width=2)

    _draw_caption(canvas, caption, view, size)
    return canvas.to_png()


_BOX_EDGES = [
    (0, 1),
    (2, 3),
    (4, 5),
    (6, 7),
    (0, 2),
    (1, 3),
    (4, 6),
    (5, 7),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
]

#: Orthographic basis per viewpoint: (right vector, up vector) in model space.
_VIEW_BASIS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((1, 0, 0), (0, 0, 1)),
    "back": ((-1, 0, 0), (0, 0, 1)),
    "left": ((0, -1, 0), (0, 0, 1)),
    "right": ((0, 1, 0), (0, 0, 1)),
    "top": ((1, 0, 0), (0, 1, 0)),
    "bottom": ((1, 0, 0), (0, -1, 0)),
}
_ISO = ((0.866, -0.866, 0.0), (0.5, 0.5, 1.0))


def _project(point: tuple[float, float, float], view: str) -> tuple[float, float]:
    right, up = _VIEW_BASIS.get(view, _ISO)
    return (
        sum(p * r for p, r in zip(point, right)),
        sum(p * u for p, u in zip(point, up)),
    )


def _draw_caption(
    canvas: Canvas, caption: str, view: str, size: tuple[float, float, float]
) -> None:
    """A legend bar. Deliberately not text -- a bitmap font is not worth carrying.

    The three bars encode the part's proportions, and the caption travels as
    structured data in the tool result where the agent can actually read it.
    """
    for index, extent in enumerate(size):
        length = int(min(extent / max(size) * 180, 180))
        y = 24 + index * 12
        canvas.line(24, y, 24 + max(length, 2), y, (_FACE_RIGHT, _FACE_LEFT, _FACE_TOP)[index], 5)
    canvas.line(24, 12, 24 + min(len(caption) * 4, 240), 12, _AXIS, 3)
    canvas.line(24, _VIEW_HEIGHT - 20, 24 + (len(view) * 8), _VIEW_HEIGHT - 20, _AXIS, 3)
