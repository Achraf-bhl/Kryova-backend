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

from . import vba
from .backend import CatiaBackend, CatiaOperationError

logger = logging.getLogger("kryova.catia.com")

#: CATIA's reference planes, in the order `Part.OriginElements` exposes them.
_ORIGIN_PLANE = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}

#: Which origin plane to sketch on for a hole in each named face, and which two
#: components of a 3D point become the sketch's horizontal and vertical
#: coordinates. A sketch's 2D frame is not the same as the plane's name suggests
#: -- `ZX` puts z first and x second -- so this was measured rather than
#: assumed: a circle drawn at (u=10, v=20) and padded 2 mm lands at
#:
#:     XY -> (x=10, y=20)    YZ -> (y=10, z=20)    ZX -> (z=20, x=10)
#:
#: A face is drilled along its own normal, so the plane is the one that normal
#: is perpendicular to: top and bottom are ±Z, hence XY.
_SKETCH_FRAME = {
    "top": ("XY", 0, 1),
    "bottom": ("XY", 0, 1),
    "front": ("ZX", 2, 0),
    "back": ("ZX", 2, 0),
    "left": ("YZ", 1, 2),
    "right": ("YZ", 1, 2),
}

#: How far outside the part `_bounding_box` puts its measuring planes, in mm.
#: Big enough to clear anything Kryova will mesh and solve, small enough that
#: subtracting it from a distance does not lose precision in a double.
_BBOX_REACH = 10_000.0

#: `CatCaptureFormat`'s JPEG member. The full enumeration is CGM(0), EMF(1),
#: TIFF(2), TIFFGreyScale(3), BMP(4), JPEG(5) -- there is no PNG in CATIA V5,
#: which is why `capture_view` writes a `.jpg`.
_CAPTURE_FORMAT_JPEG = 5

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
        """The version string the UI shows, as `V5-R33`.

        `SystemConfiguration.Version` on its own returns the bare version number
        -- the IDL reference says "usually version 5", and a live V5-6R2023
        returns exactly `"5"`. Shown alone that is useless to an engineer, and it
        is what reaches `GET /catia/status` as `catia_version`. The release is a
        separate property, so both are read and joined the same way
        `app/catia/bridge.py::_version_string` does, keeping the two code paths
        reporting one format.
        """
        try:  # pragma: no cover - Windows only
            config = self._app.SystemConfiguration
            return f"V{config.Version}-R{config.Release}"
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
        path = self._free_document_path(name)
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

    def _free_document_path(self, name: str) -> Path:
        """A path under `documents/` that no file is using yet.

        `SaveAs` onto an existing file does not overwrite it: CATIA puts up the
        "Save As"/"Enregistrer sous" dialog and waits for a human to click. That
        dialog blocks the automation surface, so the call the agent made never
        returns, the device's queue stops draining, and the bridge is wedged
        until someone walks over to the workstation -- the exact failure the
        watchdog in `session.py` exists to detect but cannot undo.

        Asking for a part name that was used before is not a mistake worth
        failing on, and deleting whatever is already there would throw away a
        part the engineer may still want, so the new document takes the next
        free `-2`, `-3` … suffix. `remote_path` in the result carries the real
        path, which is what `open_document` and every checkpoint use.
        """
        stem = _safe_filename(name)
        candidate = self.documents / f"{stem}.CATPart"
        counter = 2
        while candidate.exists():
            candidate = self.documents / f"{stem}-{counter}.CATPart"
            counter += 1
        return candidate

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
        """Drill a named hole, as a sketched circle pocketed through the solid.

        Not `AddNewHoleFromPoint`, which is what this used to call and which
        cannot work here. That method needs a *face* reference, and the only way
        to name a face in CATIA V5 automation is `CreateReferenceFromBRepName`
        with a topological name string -- fragile, release-specific, and
        unavailable to a caller who has only "the top face". Passing
        `CreateReferenceFromName("")` instead, as this did, gets
        `La methode AddNewHoleFromPoint a echoue` for every hole ever requested.

        A circle sketched on an origin plane and pocketed through the material
        is the same geometry by a route that only uses calls already proven
        here. It gives up CATIA's hole *feature* -- no threading, no
        countersink, no tapping standard -- which is a real loss worth knowing
        about, but a through hole of a stated diameter is what `catia_hole`
        promises and all Kryova's mesher sees.
        """
        box = self._bounding_box()
        if box is None:
            raise CatiaOperationError(
                "Placing a hole by face and position needs the part's bounding box, "
                "and it could not be measured. Check that the part has a solid body."
            )
        point = _face_point(box, face, position, diameter_mm)
        plane, first, second = _SKETCH_FRAME[face]
        part = self._part()

        before = self._solid_volume()
        sketch = self._sketch(
            plane,
            lambda factory: factory.CreateClosedCircle(
                point[first], point[second], float(diameter_mm) / 2.0
            ),
        )
        try:
            pocket = part.ShapeFactory.AddNewPocket(
                self._find_sketch(sketch), float(depth_mm or 1.0)
            )
            if through_all:
                # 1 = catUpToLast: cut until there is no more material.
                pocket.FirstLimit.LimitMode = 1
            part.Update()

            # The sketch sits on an origin plane, and the solid may be entirely
            # on the far side of it -- in which case the pocket cuts away from
            # the part and removes nothing at all. Rather than reason about
            # which way is "into the material" from the bounding box, do it and
            # look: no material gone means it went the wrong way.
            if self._solid_volume() >= before:
                pocket.DirectionOrientation = 1
                part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not cut a {diameter_mm:g} mm hole in the {face} face at "
                f"{position}: {exc}. Check that the hole fits inside the part."
            ) from exc

        if self._solid_volume() >= before:
            raise CatiaOperationError(
                f"The {diameter_mm:g} mm hole at {position} on the {face} face removed "
                "no material, so it missed the part. Check the diameter against the "
                "part's bounding box."
            )
        return self._feature_result(str(pocket.Name))

    def _solid_volume(self) -> float:  # pragma: no cover - Windows only
        """Volume in mm3, or 0.0 if there is no solid yet.

        Deliberately not `_measure_solid`: that constructs a bounding box out of
        six offset planes, and this is called three times per hole.
        """
        try:
            return float(self._document().Product.Analyze.Volume)
        except Exception:  # noqa: BLE001 - "no solid" is a legitimate answer
            return 0.0

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

    def _bounding_box(self) -> tuple[float, float, float, float, float, float] | None:
        """(xmin, ymin, zmin, xmax, ymax, zmax) in mm, or None if it cannot be had.

        **CATIA V5 has no bounding-box call.** `Measurable` exposes `Volume`,
        `Area`, `GetCOG`, `GetPlane`, `GetAxis` and friends; `GetBoundingBox`
        appears nowhere in the IDL reference and raises `AttributeError` on
        every release. So the box is constructed rather than queried, the way
        `pycatia`'s `create_bounding_box` does it:

        1. Put a reference plane a long way outside the part on each of the six
           sides, by offsetting an origin plane by `_BBOX_REACH`.
        2. Measure the minimum distance from the solid to each one.
        3. The extent is the offset minus that distance.
        4. Delete the geometrical set the planes live in.

        Step 4 is not optional. These are construction features in the
        engineer's own part; leaving them behind would grow the specification
        tree by six entries on every measurement, and `_measure_solid` runs
        after every mutation.

        The obvious alternative -- six `AddNewExtremum` points, which is how
        `pycatia`'s bounding-box script does it -- was tried first and rejected
        on evidence. It is exact on a prismatic solid and simply fails on a
        curved one: reading an extremum's position needs `GetCOG`, and on the
        silhouette of a cylinder CATIA answers `La methode GetCOG a echoue`. A
        bolt and a gear are the two shapes most likely to be asked for, so a
        bounding box that only works on boxes is not worth having. Minimum
        distance is defined for any solid, and it needs no VBScript helper
        either: `GetMinimumDistance` returns a plain double.

        Returns None rather than raising if any of it fails. A measurement is
        something the agent takes *after* a successful pad, and an exception
        here would report that pad as failed -- which is how this went wrong
        before: the agent's reasonable next move is to pad again, and the part
        ends up with two.
        """
        part = self._part()
        document = self._document()
        temporary = None
        try:
            factory = part.HybridShapeFactory
            origin = part.OriginElements
            workbench = part.Parent.GetWorkbench("SPAWorkbench")
            solid = workbench.GetMeasurable(
                part.CreateReferenceFromObject(self._body())
            )
            temporary = part.HybridBodies.Add()

            extents: dict[tuple[str, int], float] = {}
            for axis, plane in (
                ("x", origin.PlaneYZ),
                ("y", origin.PlaneZX),
                ("z", origin.PlaneXY),
            ):
                for sign in (1, -1):
                    offset = factory.AddNewPlaneOffset(plane, sign * _BBOX_REACH, False)
                    temporary.AppendHybridShape(offset)
                    part.Update()
                    gap = float(
                        solid.GetMinimumDistance(part.CreateReferenceFromObject(offset))
                    )
                    # The plane sits `_BBOX_REACH` out; whatever is left of that
                    # after the gap is how far the solid reaches on that side.
                    extents[(axis, sign)] = sign * (_BBOX_REACH - gap)
        except Exception:  # noqa: BLE001 - a measurement must not fail a mutation
            logger.warning("Could not construct a bounding box", exc_info=True)
            return None
        finally:
            if temporary is not None:
                self._discard(document, part, temporary)

        # `+ 0.0` turns a negative zero into zero: a face exactly on an origin
        # plane otherwise reports "-0.0", which reads as a bug to anyone
        # checking the numbers.
        return (
            extents[("x", -1)] + 0.0,
            extents[("y", -1)] + 0.0,
            extents[("z", -1)] + 0.0,
            extents[("x", 1)] + 0.0,
            extents[("y", 1)] + 0.0,
            extents[("z", 1)] + 0.0,
        )

    def _discard(self, document: Any, part: Any, feature: Any) -> None:  # pragma: no cover
        """Remove a construction feature, without letting the failure escape."""
        try:
            selection = document.Selection
            selection.Clear()
            selection.Add(feature)
            selection.Delete()
            part.Update()
        except Exception:  # noqa: BLE001 - leaves clutter, not a broken part
            logger.warning("Could not delete temporary construction geometry", exc_info=True)

    def _measure_solid(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        """Mass and size of the current solid, in Kryova's mm-N-MPa system.

        Measured through `Product.Analyze`, not through `SPAWorkbench`'s
        `Measurable`, and the difference is not stylistic:

        * **`Analyze` already reports millimetre units.** `Analyze.Volume` on a
          100 mm cube returns `1000000.0` (mm3) and `Analyze.WetArea` returns
          `60000.0` (mm2) -- the IDL reference says so outright for WetArea
          ("This method uses mm2 instead of default Catia V5 unit").
          `Measurable` is the one that does not: the same cube gives
          `Measurable.Volume == 0.001` and `Measurable.Area == 0.06`, which are
          m3 and m2. This code used to read those and label them `_mm3`/`_mm2`,
          so every mass it produced was out by 1e9 and rounded to `0.0 kg`.

        * **`Analyze.Mass` is the applied material's mass**, in kilograms. The
          replaced code multiplied volume by a density read from
          `Part.AnalyzeMaterial`, which is not a CATIA interface at all -- it
          appears nowhere in the IDL reference and raises `AttributeError` on a
          live V5-6R2023 -- so the density silently fell back to steel for every
          part, whatever material the engineer had applied.

        The centre of gravity comes back through `vba.centre_of_gravity`.
        `GetCOG` returns it in a SAFEARRAY out-parameter that late binding
        cannot marshal -- pywin32 accepts the call and leaves the list it was
        handed untouched, so the old code reported the `[0, 0, 0]` it had
        initialised as a measured value. A frozen VBScript helper does the
        out-parameter dance and hands back a string; see `catia_bridge/vba.py`
        for why that is not the code-execution hatch it resembles.
        """
        try:
            analyze = self._document().Product.Analyze
            volume_mm3 = float(analyze.Volume)
            mass_kg = float(analyze.Mass)
            area_mm2 = float(analyze.WetArea)
        except Exception:  # noqa: BLE001
            return {"has_solid": False, "mass_kg": 0.0, "bounding_box_mm": None}

        if volume_mm3 <= 0.0:
            return {"has_solid": False, "mass_kg": 0.0, "bounding_box_mm": None}

        box = self._bounding_box()
        try:
            centre = vba.centre_of_gravity(self._app, self._part(), self._body())
        except Exception:  # noqa: BLE001 - a measurement must not fail a mutation
            logger.warning("Could not read the centre of gravity", exc_info=True)
            centre = None

        return {
            "has_solid": True,
            # Kilograms already. Nothing above this converts, by design.
            "mass_kg": round(mass_kg, 6),
            "volume_mm3": round(volume_mm3, 4),
            "surface_area_mm2": round(area_mm2, 4),
            "center_of_gravity_mm": (
                [round(v, 4) for v in centre] if centre is not None else None
            ),
            # Constructed from six extremum points rather than queried -- see
            # `_bounding_box`. None if that construction failed, because a
            # measurement must never turn a successful mutation into a failure.
            "bounding_box_mm": (
                {
                    "min": [round(v, 4) for v in box[:3]],
                    "max": [round(v, 4) for v in box[3:]],
                    "size": [round(box[i + 3] - box[i], 4) for i in range(3)],
                }
                if box is not None
                else None
            ),
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

        # JPEG, and it has to be: **CATIA V5 cannot write a PNG.** The whole of
        # `CatCaptureFormat` is CGM(0), EMF(1), TIFF(2), TIFFGreyScale(3),
        # BMP(4), JPEG(5) -- there is no PNG member. This used to pass `3` with
        # a comment calling it `catCaptureFormatPNG`, so every "look at the
        # part" call wrote a *greyscale TIFF*, named it `.png`, and handed it to
        # the agent and the browser as one. The agent is told to look at its own
        # work; it was being shown a file neither it nor the viewer could decode.
        path = self.workdir / f"view-{uuid.uuid4().hex[:8]}.jpg"
        viewer.CaptureToFile(_CAPTURE_FORMAT_JPEG, str(path))
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        if max_inline_bytes is not None and len(data) > max_inline_bytes:
            raise CatiaOperationError("The rendered view is too large to transfer.")
        return {
            "filename": f"catia-{view}.jpg",
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
        # `.stp`, and the format token below, are both deliberate. CATIA's
        # `Document.ExportData(fileName, format)` takes the *short* translator
        # token -- the IDL reference's own example is `Doc.ExportData("IGESDoc",
        # "igs")`, not `"iges"`. Passing `"step"` does not fall back to STEP; on
        # a live V5-6R2023 it raises `La methode ExportData a echoue`, which the
        # handler below then reports as a licensing problem on a workstation
        # whose STEP licence is fine. `app/catia/bridge.py::ExportFormat` pins
        # the same token as `"stp"` for the server-side COM path.
        path = self.workdir / f"export-{uuid.uuid4().hex[:8]}.stp"
        try:
            # ExportData writes synchronously and can take minutes on a large
            # assembly; the server's export timeout is sized for that.
            document.ExportData(str(path), "stp")
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
