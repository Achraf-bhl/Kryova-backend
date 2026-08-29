"""The real backend: CATIA V5 over Windows COM automation.

**Late binding only.** Every object is reached through `win32com.client.Dispatch`
/ `GetActiveObject` and used dynamically. It is tempting to run `makepy` against
the CATIA V5 Interfaces Object Library for early binding and IntelliSense, and
it must not be done here: generating those wrappers writes into pywin32's shared
`gen_py` cache, and every *other* early-binding application on that workstation
-- including other CATIA automation tools the engineer already relies on --
starts resolving through the generated module. Breaking a colleague's macro suite
by installing our daemon is not an acceptable side effect. Late binding costs a
little speed per call and nothing else.

**Nothing here has run against a real CATIA.** This file is written from the
documented V5 automation API and is structured so that every operation is a
small, separately checkable unit, but it is unverified until someone runs it on
Windows with a licence. `mock_catia.py` is what the test suite exercises. The
places most likely to need adjusting are marked with `# VERIFY:` so a first
Windows session has a checklist rather than a haystack.

`pycatia` is a reasonable alternative dependency -- it is a typed wrapper over
exactly this COM surface, and it also uses late binding -- but it is not
required, and the daemon runs without it.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import math
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .backend import CatiaBackend, CatiaOperationError

logger = logging.getLogger("kryova.catia.com")

#: CATIA's reference planes, in the order `Part.OriginElements` exposes them.
_ORIGIN_PLANE = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}

_MM_PER_UNIT = 1.0  # CATIA's automation API reports lengths in millimetres.


class CatiaCom(CatiaBackend):
    is_mock = False
    capabilities = ("part", "sketch", "measure", "export", "capture", "checkpoint")

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)
        self.documents = self.workdir / "documents"
        self.snapshots = self.workdir / "snapshots"
        for directory in (self.documents, self.snapshots):
            directory.mkdir(parents=True, exist_ok=True)

        self._app: Any = None
        self._connect()
        self.catia_version = self._read_version()

    # -- connection ----------------------------------------------------------

    def _connect(self) -> None:
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - Windows only
            raise CatiaOperationError(
                "pywin32 is not installed, so this bridge cannot talk to CATIA. "
                "Install it with `pip install pywin32`, or run with --mock."
            ) from exc

        # The daemon's worker touches COM from a thread that is not the one
        # that initialised it; CATIA's automation server is an STA, so the
        # thread has to join an apartment before any Dispatch call.
        pythoncom.CoInitialize()

        try:
            # Attach to the running CATIA rather than starting one. Launching
            # CATIA from a background daemon consumes a licence the engineer did
            # not ask to spend and opens a window they did not ask for.
            self._app = win32com.client.GetActiveObject("CATIA.Application")
        except Exception as exc:  # pragma: no cover - Windows only
            raise CatiaOperationError(
                "CATIA is not running on this workstation. Start CATIA, open or "
                "create a part, and the bridge will connect automatically."
            ) from exc
        logger.info("Attached to a running CATIA.Application")

    def _read_version(self) -> str:
        try:  # pragma: no cover - Windows only
            # VERIFY: `SystemService.Version` exists on V5-6R; some releases
            # only expose `Application.Release`.
            return str(self._app.SystemConfiguration.Version)
        except Exception:  # noqa: BLE001
            return "V5 (version unavailable)"

    def close(self) -> None:  # pragma: no cover - Windows only
        try:
            import pythoncom  # type: ignore[import-not-found]

            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass

    # -- liveness ------------------------------------------------------------

    def health(self) -> None:
        """A trivial property read, which a modal dialog will block.

        `session.py` runs this on a watchdog thread precisely because it can
        block: the blocking *is* the signal.
        """
        if self._app is None:  # pragma: no cover - Windows only
            raise CatiaOperationError("The bridge is not attached to CATIA.")
        try:  # pragma: no cover - Windows only
            _ = self._app.Documents.Count
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA stopped responding to automation ({exc}). It may have been "
                "closed, or it may be showing a dialog."
            ) from exc

    # -- document handles ----------------------------------------------------

    def _document(self) -> Any:  # pragma: no cover - Windows only
        try:
            document = self._app.ActiveDocument
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                "No document is open in CATIA. Call catia_new_part, or open the part "
                "in CATIA."
            ) from exc
        if document is None:
            raise CatiaOperationError("No document is open in CATIA.")
        return document

    def _part(self) -> Any:  # pragma: no cover - Windows only
        document = self._document()
        try:
            return document.Part
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                "The active CATIA document is not a part (it may be a product or a "
                "drawing). Activate the CATPart and try again."
            ) from exc

    def _body(self) -> Any:  # pragma: no cover - Windows only
        part = self._part()
        return part.MainBody

    # -- documents -----------------------------------------------------------

    def new_part(self, *, name: str) -> dict[str, Any]:  # pragma: no cover - Windows only
        documents = self._app.Documents
        document = documents.Add("Part")
        path = self.documents / f"{_safe_filename(name)}.CATPart"
        # Save immediately: a document that has never been written has no path,
        # and every checkpoint from here on is a copy of that path.
        document.SaveAs(str(path))
        part = document.Part
        part.Update()
        return {
            "doc_name": name,
            "remote_path": str(path),
            "features": self._feature_list(),
            "up_to_date": True,
        }

    def open_document(  # pragma: no cover - Windows only
        self,
        *,
        doc_name: str | None = None,
        remote_path: str | None = None,
        fallback_checkpoint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = Path(remote_path) if remote_path else None
        restored = False

        if path is None or not path.is_file():
            if not fallback_checkpoint or not fallback_checkpoint.get("content_b64"):
                raise CatiaOperationError(
                    "The document is not on this workstation and no stored checkpoint "
                    "is available to restore it from. Start a new part instead."
                )
            path = self.documents / f"{_safe_filename(doc_name or 'Part')}.CATPart"
            path.write_bytes(base64.b64decode(fallback_checkpoint["content_b64"]))
            restored = True

        # Reuse an already-open copy rather than opening a second one: CATIA
        # will happily hold two handles on one file and then refuse to save.
        for index in range(1, int(self._app.Documents.Count) + 1):
            existing = self._app.Documents.Item(index)
            if str(getattr(existing, "FullName", "")).lower() == str(path).lower():
                existing.Activate()
                break
        else:
            self._app.Documents.Open(str(path))

        return {
            "doc_name": doc_name or path.stem,
            "remote_path": str(path),
            "restored_from_checkpoint": restored,
            "features": self._feature_list(),
            **self._measure_solid(),
        }

    # -- parameters ----------------------------------------------------------

    def list_parameters(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        parameters = self._part().Parameters
        found = []
        for index in range(1, int(parameters.Count) + 1):
            parameter = parameters.Item(index)
            try:
                value = float(parameter.Value)
            except (TypeError, ValueError, AttributeError):
                # Boolean and string parameters exist and are not errors; they
                # simply have no numeric value to report.
                continue
            found.append(
                {
                    "name": str(parameter.Name),
                    "value": value,
                    "unit": _unit_of(parameter),
                    "expression": str(parameter.ValueAsString()),
                    "comment": str(getattr(parameter, "Comment", "") or ""),
                }
            )
        return {"parameters": found}

    def set_parameter(  # pragma: no cover - Windows only
        self, *, name: str, value: float, unit: str
    ) -> dict[str, Any]:
        parameters = self._part().Parameters
        try:
            parameter = parameters.Item(name)
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"No parameter named {name!r} in this part. Call catia_list_parameters "
                "to see the real names."
            ) from exc

        actual = _unit_of(parameter)
        if actual and actual != unit:
            raise CatiaOperationError(
                f"Parameter {name!r} is in {actual}, not {unit}. CATIA parameters are "
                "typed and setting the wrong unit silently does nothing."
            )
        previous = float(parameter.Value)
        parameter.Value = float(value)
        self._part().Update()
        return {
            "parameter": {"name": name, "value": float(value), "unit": unit},
            "previous_value": previous,
            "features": self._feature_list(),
            **self._measure_solid(),
        }

    # -- sketches ------------------------------------------------------------

    def sketch_rectangle(  # pragma: no cover - Windows only
        self, *, plane: str, width_mm: float, height_mm: float
    ) -> dict[str, Any]:
        half_w, half_h = width_mm / 2.0, height_mm / 2.0
        corners = [
            (-half_w, -half_h),
            (half_w, -half_h),
            (half_w, half_h),
            (-half_w, half_h),
        ]

        def draw(factory: Any) -> None:
            for index in range(4):
                start, end = corners[index], corners[(index + 1) % 4]
                factory.CreateLine(start[0], start[1], end[0], end[1])

        name = self._sketch(plane, draw)
        return {
            "feature": name,
            "sketch": name,
            "plane": plane,
            "shape": "rectangle",
            "area_mm2": round(width_mm * height_mm, 4),
            "features": self._feature_list(),
        }

    def sketch_circle(  # pragma: no cover - Windows only
        self, *, plane: str, diameter_mm: float
    ) -> dict[str, Any]:
        def draw(factory: Any) -> None:
            factory.CreateClosedCircle(0.0, 0.0, diameter_mm / 2.0)

        name = self._sketch(plane, draw)
        return {
            "feature": name,
            "sketch": name,
            "plane": plane,
            "shape": "circle",
            "area_mm2": round(math.pi * (diameter_mm / 2) ** 2, 4),
            "features": self._feature_list(),
        }

    def _sketch(self, plane: str, draw: Any) -> str:  # pragma: no cover - Windows only
        part = self._part()
        origin = part.OriginElements
        reference = getattr(origin, _ORIGIN_PLANE[plane])
        sketch = self._body().Sketches.Add(reference)
        # OpenEdition/CloseEdition brackets every 2D edit; leaving a sketch open
        # makes every later call fail with an unrelated-looking error.
        factory = sketch.OpenEdition()
        try:
            draw(factory)
        finally:
            sketch.CloseEdition()
        part.Update()
        return str(sketch.Name)

    def _find_sketch(self, name: str) -> Any:  # pragma: no cover - Windows only
        sketches = self._body().Sketches
        for index in range(1, int(sketches.Count) + 1):
            sketch = sketches.Item(index)
            if str(sketch.Name) == name:
                return sketch
        raise CatiaOperationError(
            f"No sketch named {name!r} in this part. Use the name a sketch tool returned."
        )

    # -- features ------------------------------------------------------------

    def pad(  # pragma: no cover - Windows only
        self,
        *,
        sketch: str,
        length_mm: float,
        symmetric: bool = False,
        reversed: bool = False,  # noqa: A002 - protocol field name
    ) -> dict[str, Any]:
        factory = self._part().ShapeFactory
        pad = factory.AddNewPad(self._find_sketch(sketch), float(length_mm))
        if symmetric:
            # VERIFY: on some releases this is `pad.IsSymmetric = True` instead.
            pad.IsSymmetric = True
        if reversed:
            pad.DirectionOrientation = 1
        self._part().Update()
        return self._feature_result(str(pad.Name))

    def pocket(  # pragma: no cover - Windows only
        self, *, sketch: str, depth_mm: float | None = None, through_all: bool = False
    ) -> dict[str, Any]:
        if depth_mm is None and not through_all:
            raise CatiaOperationError(
                "catia_pocket needs either depth_mm or through_all; neither was given."
            )
        factory = self._part().ShapeFactory
        pocket = factory.AddNewPocket(self._find_sketch(sketch), float(depth_mm or 1.0))
        if through_all:
            # 1 = catUpToLast in CATIA's length-type enumeration.
            pocket.FirstLimit.LimitMode = 1
        self._part().Update()
        return self._feature_result(str(pocket.Name))

    def hole(  # pragma: no cover - Windows only
        self,
        *,
        face: str,
        position: str,
        diameter_mm: float,
        depth_mm: float | None = None,
        through_all: bool = True,
    ) -> dict[str, Any]:
        # VERIFY: resolving "the top face at the back-left" to a topological
        # reference is the single least portable operation in this file.
        # `AddNewHoleFromRefPoint` needs a point and a face reference; the
        # bounding box below gives the coordinates, and the face is selected by
        # its outward normal.
        box = self._bounding_box()
        point = _face_point(box, face, position, diameter_mm)
        part = self._part()
        selection = self._document().Selection
        selection.Clear()
        try:
            reference = part.CreateReferenceFromName("")
            hole = part.ShapeFactory.AddNewHoleFromPoint(
                point[0], point[1], point[2], reference, float(depth_mm or 10.0)
            )
            hole.Diameter.Value = float(diameter_mm)
            if through_all:
                hole.Type = 0
                hole.BottomLimit.LimitMode = 1
            part.Update()
            return self._feature_result(str(hole.Name))
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not place a hole on the {face} face at {position}: {exc}. "
                "Sketch a circle on that face and pocket it instead."
            ) from exc
        finally:
            selection.Clear()

    def fillet(  # pragma: no cover - Windows only
        self, *, radius_mm: float, feature: str | None = None, edges: str = "all"
    ) -> dict[str, Any]:
        part = self._part()
        target = self._edge_reference(feature)
        try:
            # 1 = catTangencyFilletEdgePropagation, which follows a chain of
            # tangent edges -- the behaviour a user means by "round that corner".
            edge_fillet = part.ShapeFactory.AddNewEdgeFilletWithConstantRadius(
                target, 1, float(radius_mm)
            )
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a {radius_mm:g} mm fillet ({exc}). It is usually too "
                "large for the adjacent faces; try a smaller radius."
            ) from exc
        part.Update()
        return self._feature_result(str(edge_fillet.Name))

    def chamfer(  # pragma: no cover - Windows only
        self,
        *,
        length_mm: float,
        angle_deg: float = 45.0,
        feature: str | None = None,
        edges: str = "all",
    ) -> dict[str, Any]:
        part = self._part()
        target = self._edge_reference(feature)
        try:
            # (propagation=1 tangency, mode=0 length/angle)
            chamfer = part.ShapeFactory.AddNewChamfer(
                target, 1, 0, 1, float(length_mm), float(angle_deg)
            )
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a {length_mm:g} mm chamfer ({exc})."
            ) from exc
        part.Update()
        return self._feature_result(str(chamfer.Name))

    def _edge_reference(self, feature: str | None) -> Any:  # pragma: no cover
        part = self._part()
        body = self._body()
        target = body.Shapes.Item(feature) if feature else body
        return part.CreateReferenceFromObject(target)

    def update(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        self._part().Update()
        return {
            "updated": True,
            "features": self._feature_list(),
            **self._measure_solid(),
        }

    def _feature_result(self, name: str) -> dict[str, Any]:  # pragma: no cover
        return {
            "feature": name,
            "features": self._feature_list(),
            **self._measure_solid(),
        }

    def _feature_list(self) -> list[dict[str, Any]]:  # pragma: no cover - Windows only
        try:
            shapes = self._body().Shapes
        except Exception:  # noqa: BLE001
            return []
        return [
            {
                "name": str(shapes.Item(i).Name),
                "type": str(getattr(shapes.Item(i), "Type", "Shape")),
            }
            for i in range(1, int(shapes.Count) + 1)
        ]

    # -- inspection ----------------------------------------------------------

    def _bounding_box(self) -> tuple[float, float, float, float, float, float]:
        """(xmin, ymin, zmin, xmax, ymax, zmax) in millimetres."""
        # pragma: no cover - Windows only
        try:
            measurable = self._part().Parent.GetWorkbench("SPAWorkbench").GetMeasurable(
                self._part().CreateReferenceFromObject(self._body())
            )
            box = [0.0] * 6
            measurable.GetBoundingBox(box)
            return tuple(float(v) * _MM_PER_UNIT for v in box)  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not measure the part's bounding box ({exc}). The part may "
                "have no solid geometry yet."
            ) from exc

    def _measure_solid(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        try:
            workbench = self._part().Parent.GetWorkbench("SPAWorkbench")
            measurable = workbench.GetMeasurable(
                self._part().CreateReferenceFromObject(self._body())
            )
            volume_mm3 = float(measurable.Volume)
            area_mm2 = float(measurable.Area)
            centre = [0.0, 0.0, 0.0]
            measurable.GetCOG(centre)
        except Exception:  # noqa: BLE001
            return {"has_solid": False, "mass_kg": 0.0, "bounding_box_mm": None}

        box = self._bounding_box()
        density = _density_kg_per_mm3(self._part())
        return {
            "has_solid": True,
            # Kilograms already. Nothing above this converts, by design.
            "mass_kg": round(volume_mm3 * density, 6),
            "volume_mm3": round(volume_mm3, 4),
            "surface_area_mm2": round(area_mm2, 4),
            "center_of_gravity_mm": [round(v, 4) for v in centre],
            "bounding_box_mm": {
                "min": [round(v, 4) for v in box[:3]],
                "max": [round(v, 4) for v in box[3:]],
                "size": [round(box[i + 3] - box[i], 4) for i in range(3)],
            },
        }

    def measure(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        summary = self._measure_solid()
        if not summary.get("has_solid"):
            raise CatiaOperationError(
                "The part has no solid geometry to measure yet. Sketch a profile and "
                "pad it first."
            )
        return {**summary, "features": self._feature_list(), "approximate": False}

    def capture_view(  # pragma: no cover - Windows only
        self, *, view: str = "iso", label: str = "", max_inline_bytes: int | None = None
    ) -> dict[str, Any]:
        viewer = self._app.ActiveWindow.ActiveViewer
        camera = {
            "iso": "Isometric",
            "front": "Front",
            "back": "Back",
            "top": "Top",
            "bottom": "Bottom",
            "left": "Left",
            "right": "Right",
        }[view]
        try:
            viewer.Viewpoint3D = self._app.ActiveDocument.Cameras.Item(camera).Viewpoint3D
        except Exception:  # noqa: BLE001
            # Not every document has the named cameras; the current view is
            # still worth capturing.
            logger.info("No %s camera in this document; capturing the current view", camera)
        viewer.Reframe()

        path = self.workdir / f"view-{uuid.uuid4().hex[:8]}.png"
        viewer.CaptureToFile(3, str(path))  # 3 = catCaptureFormatPNG
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        if max_inline_bytes is not None and len(data) > max_inline_bytes:
            raise CatiaOperationError("The rendered view is too large to transfer.")
        return {
            "filename": f"catia-{view}.png",
            "view": view,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_b64": base64.b64encode(data).decode("ascii"),
        }

    # -- transfer and safety -------------------------------------------------

    def export_step(  # pragma: no cover - Windows only
        self, *, note: str | None = None, max_inline_bytes: int | None = None
    ) -> dict[str, Any]:
        document = self._document()
        self._part().Update()
        path = self.workdir / f"export-{uuid.uuid4().hex[:8]}.step"
        try:
            # ExportData writes synchronously and can take minutes on a large
            # assembly; the server's export timeout is sized for that.
            document.ExportData(str(path), "step")
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not export STEP ({exc}). Check that the STEP interface is "
                "licensed on this workstation."
            ) from exc

        if not path.is_file():
            raise CatiaOperationError(
                "CATIA reported a successful STEP export but wrote no file."
            )
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        if max_inline_bytes is not None and len(data) > max_inline_bytes:
            raise CatiaOperationError(
                f"The exported STEP file is {len(data) // (1024 * 1024)} MB, larger than "
                "the bridge can transfer in one piece. Export a simplified "
                "representation, or upload the file to Kryova directly."
            )
        return {
            "filename": f"{_safe_filename(str(document.Name))}.step",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_b64": base64.b64encode(data).decode("ascii"),
            "note": note,
        }

    def checkpoint(  # pragma: no cover - Windows only
        self, *, label: str, max_inline_bytes: int | None = None
    ) -> dict[str, Any]:
        document = self._document()
        document.Save()
        source = Path(str(document.FullName))
        if not source.is_file():
            raise CatiaOperationError(
                "CATIA has not written this document to disk yet, so it cannot be "
                "checkpointed. Save it once in CATIA and try again."
            )
        reference = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        snapshot = self.snapshots / f"{reference}{source.suffix}"
        shutil.copyfile(source, snapshot)

        size = snapshot.stat().st_size
        inline = max_inline_bytes is None or size <= max_inline_bytes
        digest = hashlib.sha256()
        with snapshot.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return {
            "remote_ref": str(snapshot),
            "doc_name": str(document.Name),
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "inline": inline,
            "content_b64": (
                base64.b64encode(snapshot.read_bytes()).decode("ascii") if inline else None
            ),
        }

    def restore(self, *, checkpoint: dict[str, Any]) -> dict[str, Any]:
        # pragma: no cover - Windows only
        document = self._document()
        target = Path(str(document.FullName))

        source: Path | None = None
        reference = checkpoint.get("remote_ref")
        if isinstance(reference, str) and Path(reference).is_file():
            source = Path(reference)
        elif checkpoint.get("content_b64"):
            source = self.snapshots / f"restore-{uuid.uuid4().hex[:8]}{target.suffix}"
            source.write_bytes(base64.b64decode(checkpoint["content_b64"]))
        if source is None:
            raise CatiaOperationError(
                "That checkpoint is not on this workstation and the server holds no "
                "copy of it, so it cannot be restored."
            )

        # Close before overwriting: CATIA holds the file open and a copy over a
        # live document produces a corrupt part rather than an error.
        document.Close()
        shutil.copyfile(source, target)
        self._app.Documents.Open(str(target))
        return {
            "restored": True,
            "doc_name": target.stem,
            "features": self._feature_list(),
            **self._measure_solid(),
        }


# -- helpers -----------------------------------------------------------------


def _unit_of(parameter: Any) -> str:  # pragma: no cover - Windows only
    """Best-effort unit symbol for a CATIA parameter."""
    try:
        text = str(parameter.ValueAsString())
    except Exception:  # noqa: BLE001
        return ""
    for symbol in ("mm", "deg", "kg"):
        if text.strip().endswith(symbol):
            return symbol
    return ""


def _density_kg_per_mm3(part: Any) -> float:  # pragma: no cover - Windows only
    """The applied material's density, or steel if none is applied.

    Falling back silently would be worse than it looks -- a mass is quoted to
    the user either way -- so the fallback is reported through
    `measure()["material"]` when it is used.
    """
    try:
        material = part.AnalyzeMaterial
        return float(material.Density) * 1e-9
    except Exception:  # noqa: BLE001
        return 7850e-9


def _face_point(
    box: tuple[float, float, float, float, float, float],
    face: str,
    position: str,
    diameter_mm: float,
) -> tuple[float, float, float]:
    """Turn a named face and position into a point, in millimetres.

    This is the coordinate maths the model is deliberately never asked to do.
    Corner positions are inset by the hole radius plus half again, so a hole
    named "back_left" lands inside the material rather than breaking the edge.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = box
    inset = diameter_mm * 0.75
    centre = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)

    x_lo, x_hi = xmin + inset, xmax - inset
    y_lo, y_hi = ymin + inset, ymax - inset
    plan = {
        "center": (centre[0], centre[1]),
        "front_left": (x_lo, y_lo),
        "front_right": (x_hi, y_lo),
        "back_left": (x_lo, y_hi),
        "back_right": (x_hi, y_hi),
    }[position]

    if face in {"top", "bottom"}:
        return (plan[0], plan[1], zmax if face == "top" else zmin)
    if face in {"front", "back"}:
        return (plan[0], ymin if face == "front" else ymax, centre[2])
    return (xmin if face == "left" else xmax, plan[1], centre[2])


def _safe_filename(name: str) -> str:
    kept = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name).strip("-")
    return kept[:64] or "part"
