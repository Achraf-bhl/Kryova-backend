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
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import vba
from .backend import CatiaBackend, CatiaOperationError

logger = logging.getLogger("kryova.catia.com")

#: CATIA's reference planes, in the order `Part.OriginElements` exposes them.
_ORIGIN_PLANE = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}

# There is deliberately no rectangular/circular pattern here yet, and the
# reason is recorded so the next attempt starts from evidence rather than from
# the same dead ends. All of this was measured on a live V5-R33:
#
# * `AddNewRectPattern` takes 12 positional arguments. Fewer raises "Nombre de
#   paramètres non valide"; more is refused by pywin32.
# * Its direction arguments refuse a HybridShape line, a HybridShape Direction
#   and an AxisSystem -- each a bare "AddNewRectPattern a échoué". A reference
#   to an origin *plane* is the only thing it accepts.
# * But a plane reference does not mean "step along that plane's normal": the
#   instances march off diagonally, advancing in two axes at once. On a 100x60
#   plate a 5-instance pattern put three holes on the part, hung the fourth
#   half off the edge and missed the plate entirely with the fifth.
# * `AddNewCircPattern` behaves worse: with a plane it builds a feature that
#   produces no copies at all, and with any line/direction/axis it fails.
#
# A `direction: x|y|z` argument that silently produces a diagonal is exactly
# the "quietly builds the wrong part" failure this module's tool table exists
# to prevent, so no pattern tool is exposed until a direction can actually be
# controlled. A square test plate hides the bug -- test on a rectangle.

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

#: fillet()/chamfer() error text. `edges` (the tool's own targeting parameter,
#: e.g. "top") is never honoured -- see `_edge_reference` -- so the message
#: says that up front instead of blaming the radius or length, which the
#: previous wording did and which sent a caller retrying smaller values
#: forever against a request that could never succeed.
_EDGE_OPERATION_UNSUPPORTED = (
    "CATIA refused this {op}: {exc}. This bridge cannot yet target specific "
    "edges or faces on a real CATIA part (no reliable, version-independent way "
    "to reference one is exposed to automation) -- {op} always acts on the "
    "whole body's edge set, and a real CATIA solid usually rejects that as a "
    "single constant-radius/length operation. Retrying with a different "
    "radius or length will not fix this."
)

#: `CatCaptureFormat`'s JPEG member. The full enumeration is CGM(0), EMF(1),
#: TIFF(2), TIFFGreyScale(3), BMP(4), JPEG(5) -- there is no PNG in CATIA V5,
#: which is why `capture_view` writes a `.jpg`.
_CAPTURE_FORMAT_JPEG = 5

_MM_PER_UNIT = 1.0  # CATIA's automation API reports lengths in millimetres.

#: The density CATIA assumes for a part with no material applied, in kg/m3.
#: Confirmed on a live V5-R33: a part with nothing applied reports
#: `Part.Density == 1000.0`, and `Analyze.Mass / Analyze.Volume` agrees exactly.
#: Nothing in this bridge applies a material, so this is what every part it
#: builds is weighed with until someone does.
CATIA_DEFAULT_DENSITY_KG_M3 = 1000.0

#: Where CATIA keeps its shipped material catalogues, relative to the install
#: root. Searched in order; the first that opens wins.
_CATALOGUE_NAMES = ("Catalog.CATMaterial", "Advanced_Materials.CATMaterial")

#: Kryova's material keys mapped onto candidate names in CATIA's shipped
#: catalogue, most specific first.
#:
#: Names, not families, and several per key. The catalogue is installed in the
#: interface language: a French V5 ships `materials/French/Catalog.CATMaterial`
#: whose families are `Metaux`, `Divers`, `Textiles` and whose steel is called
#: `Acier`. Matching on an English family and an English name found nothing
#: there, which is most of why applying a material had never once worked on the
#: workstation this was written for. So every family is searched and only the
#: material name has to match one of these.
#:
#: A miss costs the catalogue application and nothing else: the density that
#: decides the reported mass comes from Kryova's own library and is passed in.
_CATIA_MATERIAL: dict[str, tuple[str, ...]] = {
    "aluminium-6061-t6": ("Aluminium", "Aluminum"),
    "aluminium-7075-t6": ("Aluminium", "Aluminum"),
    # The default catalogue ships no stainless grade, so plain steel is the
    # closest honest attachment. The reported mass still follows Kryova's own
    # density for the grade the user actually chose.
    "steel-1018": ("Acier", "Steel"),
    "stainless-304": ("Acier", "Steel"),
    "titanium-ti6al4v": ("Titane", "Titanium"),
    "abs": ("Plastique", "Plastic"),
    "pla": ("Plastique", "Plastic"),
    "nylon-pa12": ("Nylon",),
}


def _find_catalogue_material(catalogue: Any, names: tuple[str, ...]) -> Any:
    """The first material in any family whose name is one of `names`.

    Every family is searched rather than one named family, because the family
    names are localised too (`Metaux`, `Divers`) and a material is unique
    enough by name within the shipped catalogue.
    """
    for wanted in names:
        families = catalogue.Families
        for index in range(1, int(families.Count) + 1):
            materials = families.Item(index).Materials
            for position in range(1, int(materials.Count) + 1):
                candidate = materials.Item(position)
                if str(candidate.Name) == wanted:
                    return candidate
    return None


class CatiaCom(CatiaBackend):
    is_mock = False
    capabilities = ("part", "sketch", "measure", "export", "capture", "checkpoint")

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)
        self.documents = self.workdir / "documents"
        self.snapshots = self.workdir / "snapshots"
        for directory in (self.documents, self.snapshots):
            directory.mkdir(parents=True, exist_ok=True)

        # Per-thread, not per-object -- see `_app`.
        self._local = threading.local()
        self._connect()
        self.catia_version = self._read_version()

    # -- connection ----------------------------------------------------------

    @property
    def _app(self) -> Any:
        """The CATIA handle belonging to *this* thread.

        A COM interface pointer is marshalled for the apartment of the thread
        that acquired it. Use it from any other thread and COM refuses with
        RPC_E_WRONG_THREAD -- "the application called an interface that was
        marshalled for a different thread" -- which the daemon reported as
        "CATIA stopped responding to automation", pointing every diagnosis at
        CATIA instead of at the threading.

        That mattered because nothing here shares a thread. `__init__` runs on
        the main thread, operations arrive on `asyncio.to_thread` workers, and
        the liveness probe gets a fresh watchdog thread every single call. One
        handle on the object could serve at most one of the three.

        Handing each thread its own is legitimate and cheap: every thread joins
        its own apartment and gets its own proxy to the same out-of-process
        CATIA, verified live across three worker threads and a watchdog thread
        all reading `Documents.Count = 19` at once. Serialisation is already
        handled a level up by `session.py`'s lock.
        """
        local = self.__dict__.setdefault("_local", threading.local())
        app = getattr(local, "app", None)
        if app is None:
            self._connect()
            app = local.app
        return app

    @_app.setter
    def _app(self, value: Any) -> None:
        self.__dict__.setdefault("_local", threading.local()).app = value

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
        block: the blocking *is* the signal. That thread is a different one
        every call, and `_app` hands it a handle for its own apartment, so the
        probe measures CATIA rather than measuring the threading.

        It stays free of side effects beyond that first attach: repairing a
        handle here would repair the watchdog's, which dies with the thread
        moments later. `ensure_connected` repairs the one that matters.
        """
        try:  # pragma: no cover - Windows only
            _ = self._app.Documents.Count
        except CatiaOperationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA stopped responding to automation ({exc}). It may have been "
                "closed, or it may be showing a dialog."
            ) from exc

    def ensure_connected(self) -> None:
        """Validate *this* thread's handle, reconnecting if it has gone stale.

        Called on the operation thread, immediately before the operation runs,
        and that is the only place it can usefully happen. The watchdog probe in
        `health` cannot stand in for it: the watchdog is a fresh thread holding
        a fresh handle, so it reports a healthy CATIA while the worker thread
        that is about to do the work still holds a pointer into a CATIA process
        that no longer exists.

        Which is the failure this fixes. `_connect` used to run exactly once, so
        closing CATIA and reopening it -- several times in any working day --
        left every later call failing with "CATIA stopped responding to
        automation", curable only by restarting a daemon nobody is watching.

        One reconnection heals everything: documents, parts and bodies are all
        derived from `_app` per call and never cached.
        """
        try:  # pragma: no cover - Windows only
            _ = self._app.Documents.Count
            return
        except CatiaOperationError:
            raise
        except Exception:  # noqa: BLE001
            self._app = None
        self._connect()  # pragma: no cover - Windows only
        logger.info("Re-attached to CATIA after this thread's handle went stale")

    # -- document handles ----------------------------------------------------

    def _document(self) -> Any:  # pragma: no cover - Windows only
        try:
            document = self._app.ActiveDocument
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                "No document is open in CATIA. Call catia_new_part, or open the part in CATIA."
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

    # -- material ------------------------------------------------------------

    def set_material(  # pragma: no cover - Windows only
        self, *, material: str, density_kg_m3: float
    ) -> dict[str, Any]:
        """Record the part's material, and apply it in CATIA where possible.

        Two things happen, and only one of them can fail.

        **The density is recorded here, always.** It is what every mass this
        bridge reports is computed from, and it arrives from Kryova's own
        material library rather than being read out of CATIA -- so the mass is
        right whether or not the catalogue below is available. That matters
        because nothing applied a material before this existed, and CATIA
        weighed every part at its default 1000 kg/m3: a 120x80x10 steel bracket
        was reported as 0.095 kg against a real 0.755 kg.

        **The catalogue application is best effort, and now usually succeeds.**
        It was believed for a while that `Documents.Open` on a `.CATMaterial`
        fails without the Material Library product. It does not: the failures
        measured here were this bridge's own bugs, listed in
        `_apply_catalogue_material`. A live V5-R33 applies Acier and reports
        7860 kg/m3. Where it genuinely cannot -- an install with no catalogue --
        it is reported rather than raised, and the mass is still right.

        Note that a part CATIA has taken a material for reports CATIA's density
        for it, which is close to but not identical with Kryova's (7860 against
        7870 for steel). `_measure_solid` prefers what CATIA says once a
        material is attached, so the mass Kryova quotes and the mass the CATPart
        itself reports are the same number.
        """
        self._density_kg_m3 = float(density_kg_m3)
        applied, detail = self._apply_catalogue_material(material)
        try:
            self._part().Update()
        except Exception:  # noqa: BLE001 - an update failure must not lose the density
            logger.warning("Could not update the part after setting the material")

        return {
            "material": material,
            "density_kg_m3": round(float(density_kg_m3), 1),
            "applied_in_catia": applied,
            "detail": detail,
            **self._measure_solid(),
        }

    def _apply_catalogue_material(self, material: str) -> tuple[bool, str]:
        """Attach a real CATIA material to the part. Never raises.

        Three things had to be right and none of them were, which is why this
        reported "your CATIA cannot do materials" on a workstation whose
        material browser opens perfectly well from the toolbar.

        **The manager hangs off the Part, not the Document.** The V5 reference
        spells it `partDocument.GetItem("CATMatManagerVBExt")`, and on a live
        V5-R33 that call succeeds and hands back a *Product* -- an object with
        no `ApplyMaterialOnPart` on it, so the only symptom is an attribute
        error naming a method the documentation says exists. `part.GetItem(...)`
        returns the real `MaterialManager`. Asked to name the type, CATIA's own
        script engine says `Product` for the first and `MaterialManager` for the
        second.

        **Opening the catalogue changes the active document.** Everything here
        reaches the part through `ActiveDocument`, so a part looked up after the
        catalogue was opened *is* the catalogue -- and the failure surfaced as
        "the active CATIA document is not a part", which reads like the engineer
        clicked the wrong window. The part is captured first, and held.

        **The catalogue is installed in the interface language.** See
        `_CATIA_MATERIAL`.

        Success is confirmed by reading `Part.Density` back, not by the call
        failing to raise: applying a material CATIA does not take leaves the
        default 1000 kg/m3 in place without complaining.
        """
        names = _CATIA_MATERIAL.get(material, ("Acier", "Steel"))
        # Captured before the catalogue is opened, because opening it makes the
        # catalogue the active document.
        part = self._part()
        catalogue = None
        try:  # pragma: no cover - Windows only
            catalogue = self._open_material_catalogue()
            if catalogue is None:
                return False, (
                    "CATIA has no material catalogue this bridge could open, so no "
                    "material was attached in CATIA. Mass is still correct: Kryova "
                    "computes it from the material you chose."
                )

            chosen = _find_catalogue_material(catalogue, names)
            if chosen is None:
                return False, (
                    f"CATIA's catalogue has no material named any of {list(names)}. "
                    "Mass is still correct: Kryova computes it from the material "
                    "you chose."
                )

            manager = part.GetItem("CATMatManagerVBExt")
            # Mode 1 links the material to the part, which is what the Apply
            # Material dialog does and what makes it survive a save.
            manager.ApplyMaterialOnPart(part, chosen, 1)
            part.Update()

            density = float(part.Density)
            if density == CATIA_DEFAULT_DENSITY_KG_M3:
                return False, (
                    f"CATIA accepted {str(chosen.Name)!r} but still reports its "
                    "default density, so nothing was really attached."
                )
            return True, (
                f"Applied CATIA material {str(chosen.Name)!r}; CATIA now reports {density:g} kg/m3."
            )
        except Exception as exc:  # noqa: BLE001 - best effort by design
            logger.info("Could not apply a CATIA material: %s", exc)
            return False, (
                f"CATIA would not attach the material ({exc}). Mass is still correct: "
                "Kryova computes it from the material you chose."
            )
        finally:
            if catalogue is not None:
                try:
                    catalogue.Close()
                except Exception:  # noqa: BLE001
                    pass

    def _open_material_catalogue(self) -> Any:
        """The first shipped catalogue that opens, or None if none will."""
        for directory in _material_catalogue_dirs():
            for name in _CATALOGUE_NAMES:
                path = directory / name
                if not path.is_file():
                    continue
                try:  # pragma: no cover - Windows only
                    return self._app.Documents.Open(str(path))
                except Exception as exc:  # noqa: BLE001
                    logger.info("Material catalogue %s would not open: %s", path, exc)
        return None

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

    def sketch_polygon(  # pragma: no cover - Windows only
        self, *, plane: str, sides: int, diameter_mm: float
    ) -> dict[str, Any]:
        radius = diameter_mm / 2.0
        points = [
            (
                radius * math.cos(2 * math.pi * index / sides + math.pi / 2),
                radius * math.sin(2 * math.pi * index / sides + math.pi / 2),
            )
            for index in range(sides)
        ]

        def draw(factory: Any) -> None:
            for index in range(sides):
                start, end = points[index], points[(index + 1) % sides]
                factory.CreateLine(start[0], start[1], end[0], end[1])

        name = self._sketch(plane, draw)
        return {
            "feature": name,
            "sketch": name,
            "plane": plane,
            "shape": f"polygon-{sides}",
            "area_mm2": round(0.5 * sides * radius**2 * math.sin(2 * math.pi / sides), 4),
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
        inset_mm: float | None = None,
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
        point = _face_point(box, face, position, diameter_mm, inset_mm)
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
            raise CatiaOperationError(_EDGE_OPERATION_UNSUPPORTED.format(op="fillet", exc=exc)) from exc
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
            raise CatiaOperationError(_EDGE_OPERATION_UNSUPPORTED.format(op="chamfer", exc=exc)) from exc
        part.Update()
        return self._feature_result(str(chamfer.Name))

    def _edge_reference(self, feature: str | None) -> Any:  # pragma: no cover
        """A reference for AddNewEdgeFilletWithConstantRadius/AddNewChamfer.

        This is wrong on purpose, documented rather than hidden: both methods
        want a reference to an actual Edge (or Face), and a Body or a whole
        Shape is neither, so this always fails on real CATIA -- verified on
        V5-R33, not simulated. It is not a smaller problem than it looks: the
        `Shape` object returned by `body.Shapes.Item(...)` exposes no
        `Faces`/`Edges` collection over COM, and `Selection.Search` -- the
        other documented route to a topological reference -- fails outright
        for every query tried (including a plain by-name search with no
        topology in it at all), on a document created headless via
        `Documents.Add` with no open window. The one mechanism that reliably
        names a face or edge in CATIA V5 automation is
        `CreateReferenceFromBRepName`, and this bridge has already rejected
        that path once, for `AddNewHoleFromPoint`, as "fragile, release-
        specific" (see `hole()` above) -- introducing it here for fillet/
        chamfer would be the same trade-off repeated, not a fix, so it is left
        for a deliberate decision rather than snuck in.
        """
        part = self._part()
        body = self._body()
        target = body.Shapes.Item(feature) if feature else body
        return part.CreateReferenceFromObject(target)

    def _sketch_with_axis(self, name: str) -> Any:  # pragma: no cover - Windows only
        """The named sketch, with a revolution axis drawn along its V axis.

        A shaft or groove revolves a profile about an axis *inside the sketch*,
        and the sketch tools here draw profiles without one -- CATIA refuses the
        revolve with an unhelpful COM error. So the axis is added on demand: a
        line along the sketch's vertical axis through the origin, long enough to
        span any profile this bridge can draw.

        Verified on V5-R33: a plain CreateLine is *not* enough -- Update fails
        with a bare "La méthode Update a échoué", whether the line is a
        construction element or handed to shaft.RevoluteAxis. The line must be
        promoted to the sketch's axis element via `CenterLine`, after which
        AddNewShaft/AddNewGroove need nothing else.
        """
        sketch = self._find_sketch(name)
        factory = sketch.OpenEdition()
        try:
            line = factory.CreateLine(0.0, -_BBOX_REACH, 0.0, _BBOX_REACH)
            sketch.CenterLine = line
        finally:
            sketch.CloseEdition()
        return sketch

    def shaft(  # pragma: no cover - Windows only
        self, *, sketch: str, angle_deg: float = 360.0
    ) -> dict[str, Any]:
        part = self._part()
        profile = self._sketch_with_axis(sketch)
        try:
            shaft = part.ShapeFactory.AddNewShaft(profile)
            if angle_deg < 360.0:
                shaft.FirstAngle.Value = float(angle_deg)
                shaft.SecondAngle.Value = 0.0
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not revolve {sketch} into a shaft ({exc}). The profile "
                "must lie entirely on one side of the sketch's vertical axis; redraw "
                "it offset from the origin, or build the shape with pads instead."
            ) from exc
        return self._feature_result(str(shaft.Name))

    def groove(  # pragma: no cover - Windows only
        self, *, sketch: str, angle_deg: float = 360.0
    ) -> dict[str, Any]:
        part = self._part()
        profile = self._sketch_with_axis(sketch)
        before = self._solid_volume()
        try:
            groove = part.ShapeFactory.AddNewGroove(profile)
            if angle_deg < 360.0:
                groove.FirstAngle.Value = float(angle_deg)
                groove.SecondAngle.Value = 0.0
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not cut the groove from {sketch} ({exc}). The profile "
                "must overlap solid material and stay on one side of the sketch's "
                "vertical axis."
            ) from exc
        if self._solid_volume() >= before:
            raise CatiaOperationError(
                f"The groove from {sketch} removed no material, so it missed the "
                "part. Check the profile's position against the bounding box."
            )
        return self._feature_result(str(groove.Name))

    def mirror(self, *, plane: str) -> dict[str, Any]:  # pragma: no cover - Windows only
        part = self._part()
        origin = part.OriginElements
        reference = part.CreateReferenceFromObject(getattr(origin, _ORIGIN_PLANE[plane]))
        try:
            mirror = part.ShapeFactory.AddNewMirror(reference)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not mirror the solid about the {plane} plane ({exc}). "
                "The part needs solid material on one side of that plane first."
            ) from exc
        return self._feature_result(str(mirror.Name))

    def sketch_revolve_profile(  # pragma: no cover - Windows only
        self,
        *,
        plane: str,
        outer_diameter_mm: float,
        length_mm: float,
        inner_diameter_mm: float | None = None,
    ) -> dict[str, Any]:
        """A rod/tube profile placed beside the revolution axis, ready for a shaft.

        This exists because the other sketch tools draw on the origin, and a
        profile that straddles the revolution axis is refused by CATIA -- which
        left `catia_shaft` and `catia_groove` with no profile in the vocabulary
        they could actually consume. The caller still names only diameters and a
        length; the offset from the axis is computed here, which is exactly the
        split this module's tool table asks for.
        """
        outer_r = float(outer_diameter_mm) / 2.0
        inner_r = float(inner_diameter_mm or 0.0) / 2.0
        if inner_r >= outer_r:
            raise CatiaOperationError(
                f"inner_diameter_mm ({inner_diameter_mm:g}) must be smaller than "
                f"outer_diameter_mm ({outer_diameter_mm:g}); a tube's bore cannot reach "
                "its outside surface."
            )

        corners = [
            (inner_r, 0.0),
            (outer_r, 0.0),
            (outer_r, float(length_mm)),
            (inner_r, float(length_mm)),
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
            "shape": "revolve-profile",
            "outer_diameter_mm": float(outer_diameter_mm),
            "inner_diameter_mm": float(inner_diameter_mm or 0.0),
            "length_mm": float(length_mm),
            "area_mm2": round((outer_r - inner_r) * float(length_mm), 4),
            "features": self._feature_list(),
        }

    def sketch_groove_profile(  # pragma: no cover - Windows only
        self,
        *,
        plane: str,
        shaft_diameter_mm: float,
        width_mm: float,
        depth_mm: float,
        distance_from_end_mm: float,
    ) -> dict[str, Any]:
        """The ring-cut profile for `catia_groove`, placed against the shaft wall.

        The profile spans from `depth_mm` below the shaft's outside surface out
        to the surface itself, so revolving it removes exactly the ring the
        caller described and nothing else.
        """
        outer_r = float(shaft_diameter_mm) / 2.0
        inner_r = outer_r - float(depth_mm)
        if inner_r <= 0.0:
            raise CatiaOperationError(
                f"A {depth_mm:g} mm deep groove cuts through the centre of a "
                f"{shaft_diameter_mm:g} mm shaft. Reduce depth_mm below "
                f"{outer_r:g} mm."
            )

        near = float(distance_from_end_mm)
        far = near + float(width_mm)
        corners = [(inner_r, near), (outer_r, near), (outer_r, far), (inner_r, far)]

        def draw(factory: Any) -> None:
            for index in range(4):
                start, end = corners[index], corners[(index + 1) % 4]
                factory.CreateLine(start[0], start[1], end[0], end[1])

        name = self._sketch(plane, draw)
        return {
            "feature": name,
            "sketch": name,
            "plane": plane,
            "shape": "groove-profile",
            "shaft_diameter_mm": float(shaft_diameter_mm),
            "width_mm": float(width_mm),
            "depth_mm": float(depth_mm),
            "distance_from_end_mm": near,
            "area_mm2": round(float(depth_mm) * float(width_mm), 4),
            "features": self._feature_list(),
        }

    def shell(self, *, thickness_mm: float) -> dict[str, Any]:  # pragma: no cover
        """Hollow the solid out, leaving walls of `thickness_mm`.

        The wall is grown inwards, which is what "make it 2 mm walled" means to
        a caller reasoning about an outside dimension they already fixed. No
        face is opened: naming a face to remove needs an edge/face reference
        this bridge cannot form (see `_edge_reference`), so this produces a
        closed hollow rather than quietly opening the wrong side.
        """
        part = self._part()
        before = self._solid_volume()
        try:
            feature = part.ShapeFactory.AddNewShell(None, float(thickness_mm), 0.0)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not hollow the part to a {thickness_mm:g} mm wall ({exc}). "
                "The wall is usually thicker than half the part's smallest dimension; "
                "try a thinner wall."
            ) from exc
        if self._solid_volume() >= before:
            raise CatiaOperationError(
                f"A {thickness_mm:g} mm wall removed no material, so the part was "
                "already thinner than the wall requested."
            )
        return self._feature_result(str(feature.Name))

    def delete_feature(self, *, feature: str) -> dict[str, Any]:  # pragma: no cover
        part = self._part()
        document = self._document()
        body = self._body()
        try:
            target = body.Shapes.Item(feature)
        except Exception as exc:  # noqa: BLE001
            known = ", ".join(entry["name"] for entry in self._feature_list()) or "(none)"
            raise CatiaOperationError(
                f"No feature named {feature!r} in this part. Features: {known}."
            ) from exc

        selection = document.Selection
        selection.Clear()
        selection.Add(target)
        selection.Delete()
        try:
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"{feature} was deleted but the part no longer rebuilds ({exc}). "
                "A later feature probably depended on it -- restore the checkpoint "
                "taken before this deletion."
            ) from exc
        return {
            "deleted": feature,
            "features": self._feature_list(),
            **self._measure_solid(),
        }

    def list_features(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        self._document()
        return {"features": self._feature_list()}

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
            solid = workbench.GetMeasurable(part.CreateReferenceFromObject(self._body()))
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
                    gap = float(solid.GetMinimumDistance(part.CreateReferenceFromObject(offset)))
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

        # Density implied by what CATIA just reported, rather than a second COM
        # call that could disagree with it. `Part.Density` returns the same
        # number on a live V5-R33 (both 1000.0 on a part with no material), and
        # this cannot raise or drift out of step with the mass above it.
        density_kg_m3 = mass_kg / (volume_mm3 * 1e-9)

        # A material chosen through `catia_set_material` overrides CATIA, and has
        # to: on an install without the Material Library nothing can be attached
        # to the part, so CATIA goes on weighing it at 1000 kg/m3 however clearly
        # the user said "steel". The density here came from Kryova's own library,
        # so the mass is right on every workstation rather than only the licensed
        # ones. CATIA wins only when it has a real material of its own.
        chosen = getattr(self, "_density_kg_m3", None)
        if chosen and abs(density_kg_m3 - CATIA_DEFAULT_DENSITY_KG_M3) <= 5.0:
            density_kg_m3 = float(chosen)
            mass_kg = volume_mm3 * 1e-9 * density_kg_m3

        measurement: dict[str, Any] = {
            "has_solid": True,
            # Kilograms already. Nothing above this converts, by design.
            "mass_kg": round(mass_kg, 6),
            "density_kg_m3": round(density_kg_m3, 1),
            "volume_mm3": round(volume_mm3, 4),
            "surface_area_mm2": round(area_mm2, 4),
            "center_of_gravity_mm": ([round(v, 4) for v in centre] if centre is not None else None),
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

        if abs(density_kg_m3 - CATIA_DEFAULT_DENSITY_KG_M3) <= 5.0:
            # Nothing in this bridge applies a material, so this is the normal
            # case, and it makes `mass_kg` wrong by the density ratio of
            # whatever the engineer actually asked for -- 7.9x for steel, 2.7x
            # for aluminium. Observed live: the agent answered "how heavy is
            # it?" for a 120x80x10 steel bracket with "0.095 kg". The real
            # answer is 0.755 kg.
            #
            # The number is not hidden, because it is what CATIA reports and a
            # tool must not invent a different one. It is labelled instead, in
            # terms the model cannot restate as a plain mass.
            measurement["material_applied"] = False
            measurement["mass_is_provisional"] = True
            measurement["mass_warning"] = (
                f"No material is applied to this part in CATIA, so mass_kg was computed "
                f"with CATIA's default density of {CATIA_DEFAULT_DENSITY_KG_M3:g} kg/m3 and is "
                "NOT the real mass. Do not quote it as the part's weight. Quote "
                "volume_mm3 instead, or multiply it by the density of the material the "
                "user asked for and say which material you used."
            )
        else:
            measurement["material_applied"] = True
            measurement["mass_is_provisional"] = False
        return measurement

    def measure(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        summary = self._measure_solid()
        if not summary.get("has_solid"):
            raise CatiaOperationError(
                "The part has no solid geometry to measure yet. Sketch a profile and pad it first."
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
            raise CatiaOperationError("CATIA reported a successful STEP export but wrote no file.")
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


def _material_catalogue_dirs() -> list[Path]:
    """Every `startup/materials` directory a CATIA install might have.

    Discovered by walking the install roots rather than configured, because the
    version folder changes between releases (`B33` here) and an engineer should
    not have to put a path in a config file to get a material applied. Localised
    catalogues sit in subdirectories of the same folder and are searched too --
    a French install ships `materials/French/Catalog.CATMaterial`.
    """
    roots = [
        Path(r"C:\Program Files\Dassault Systemes"),
        Path(r"C:\Program Files (x86)\Dassault Systemes"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for version in sorted(root.iterdir(), reverse=True):
                materials = version / "win_b64" / "startup" / "materials"
                if materials.is_dir():
                    found.append(materials)
                    found.extend(sorted(d for d in materials.iterdir() if d.is_dir()))
        except OSError:  # pragma: no cover - unreadable install directory
            continue
    return found


def _face_point(
    box: tuple[float, float, float, float, float, float],
    face: str,
    position: str,
    diameter_mm: float,
    inset_mm: float | None = None,
) -> tuple[float, float, float]:
    """Turn a named face and position into a point, in millimetres.

    This is the coordinate maths the model is deliberately never asked to do.
    Corner positions are inset from the edge; without `inset_mm` that inset is
    the hole radius plus half again, which keeps the hole inside the material.

    `inset_mm` exists because "15 mm in from each corner" is how bolt patterns
    are actually specified, and the tool had no way to say it. Observed live:
    asked for "four M8 bolt holes, 15 mm in from each corner", the model had
    nowhere to put the 15 and spent the turn guessing invalid `face` and
    `position` values until it ran out of steps and answered nothing at all.
    Three of the four blank answers in a 66-turn run began this way.
    """
    xmin, ymin, zmin, xmax, ymax, zmax = box
    inset = diameter_mm * 0.75 if inset_mm is None else float(inset_mm)
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
