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
import ctypes
import hashlib
import logging
import math
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import gear, ui_policy, vba
from . import ui_automation as ui
from .backend import CatiaBackend, CatiaOperationError
from .com import (
    InspectionMixin,
    KnowledgeMixin,
    PartDesignMixin,
    ReferenceMixin,
    SketcherMixin,
    SurfacesMixin,
    WireframeMixin,
)

logger = logging.getLogger("kryova.catia.com")

#: CATIA's reference planes, in the order `Part.OriginElements` exposes them.
_ORIGIN_PLANE = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX"}

#: A pattern's two directions both come from one origin plane: CATIA steps
#: along that plane's *first* in-plane axis for direction 1 and its second for
#: direction 2. Not along its normal, which is the reading that produced
#: diagonal rows until it was measured properly on a live V5-R33.
#:
#: The other thing worth knowing, because it costs an afternoon otherwise:
#: arguments 6 and 7 of `AddNewRectPattern`/`AddNewCircPattern` are the
#: **1-based grid position of the original**, not repartition lengths. Passing
#: 0.0 there leaves the seed outside the grid, so CATIA builds n*m copies and
#: keeps the original as an extra -- five requested holes came out as six.
#: Passing 1, 1 makes the seed instance (1,1) and the counts exact.
_PATTERN_PLANE_AXES = {"XY": ("x", "y"), "YZ": ("y", "z"), "ZX": ("z", "x")}

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

#: `Selection.Search` grammars, one per CATIA UI language. The query keywords
#: are LOCALIZED -- a French CATIA answers "La méthode Search a échoué" to
#: "Topology.Edge,all" and 16 edges to "Topologie.Arête,tout" -- which is why
#: every earlier attempt here concluded Search was broken. `_edge_search`
#: detects which grammar this session speaks once and remembers it.
_SEARCH_GRAMMARS: tuple[tuple[str, str], ...] = (
    ("Topologie.Arête,tout", "fr"),
    ("Topology.Edge,all", "en"),
)

#: How many solid edges a top/bottom/vertical/horizontal selector will
#: classify. Classification measures every candidate, the cost is CATIA's own
#: measurable construction (~0.1 s per edge, in-process or over COM alike),
#: and the daemon kills any call at 30 s -- a padded gear's thousand edges
#: took 138 s live. edges='all' has no such limit because it measures nothing.
_EDGE_CLASSIFY_LIMIT = 150

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


class CatiaCom(
    SketcherMixin,
    ReferenceMixin,
    PartDesignMixin,
    SurfacesMixin,
    WireframeMixin,
    KnowledgeMixin,
    InspectionMixin,
    CatiaBackend,
):
    """The real backend, assembled from one mixin per workbench.

    `CatiaBackend` sits last so the mixins' concrete methods satisfy its
    abstract ones; the methods defined in this class body come first in
    resolution order and stay authoritative for the tools they already cover.

    `SketcherMixin` leads because it owns `_require_closed`, which the feature
    mixins call before building anything from a profile.
    """

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
        self.ui_language = self._detect_ui_language()

    def _detect_ui_language(self) -> str:  # pragma: no cover - Windows only
        """Read the menu bar and say which language it is in, or nothing.

        Done once at startup and reported in the `hello` frame, because the
        interface language cannot change without restarting CATIA -- and if
        CATIA restarts, so does this connection.

        Failure is not an error. An empty answer means the server sends command
        names in English and the daemon finds this seat's real label by reading
        the same menu, which works in every language including the ones the
        table above has never heard of.
        """
        try:
            return ui.detect_language(ui.read_menu(self._main_window(), max_depth=1))
        except Exception:  # noqa: BLE001 - a language we cannot read is not a failure
            logger.info("Could not determine CATIA's interface language; using English names")
            return ""

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

    def _document_key(self) -> str | None:  # pragma: no cover - Windows only
        """Identity of the active document, for state that must not outlive it.

        The full path where there is one; a new document that has not been
        saved yet has only a name. None when nothing is open, which never
        matches a recorded key -- the safe direction, since a density that
        cannot be tied to a document must not be applied to one.
        """
        try:
            document = self._app.ActiveDocument
            return str(getattr(document, "FullName", "") or document.Name)
        except Exception:  # noqa: BLE001 - "no document" is a legitimate answer
            return None

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
        # Tagged with the document it belongs to. The daemon outlives any one
        # part, so an untagged density silently followed the engineer into the
        # next one: after any part had a material set, every later part -- a
        # brand new one included -- was measured at that density, and because
        # the override moved the number away from CATIA's 1000 kg/m3 default it
        # also suppressed the `mass_is_provisional` warning below. Observed
        # live: a fresh 10 mm cube with no material reported 2700 kg/m3 and
        # "material_applied": true, inherited from an aluminium plate built in
        # an earlier conversation, and the agent quoted the mass as fact.
        self._density_kg_m3 = float(density_kg_m3)
        self._density_document = self._document_key()
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

    def sketch_gear_profile(  # pragma: no cover - Windows only
        self,
        *,
        plane: str,
        module_mm: float,
        teeth: int,
        pressure_angle_deg: float = 20.0,
    ) -> dict[str, Any]:
        """The closed outline of an involute spur gear, ready to pad or pocket.

        The geometry comes from `gear.outline` -- pure math shared with the
        mock -- and is drawn as one closed chain of lines. Every face of the
        padded result is planar, so the padded volume equals the outline's
        shoelace area times the pad length exactly, which is what the tests
        check against live CATIA.
        """
        try:
            points = gear.outline(float(module_mm), int(teeth), float(pressure_angle_deg))
        except ValueError as exc:
            raise CatiaOperationError(str(exc)) from exc

        def draw(factory: Any) -> None:
            for index, (x0, y0) in enumerate(points):
                x1, y1 = points[(index + 1) % len(points)]
                factory.CreateLine(x0, y0, x1, y1)

        had_solid = self._solid_volume() > 0.0
        name = self._sketch(plane, draw)
        return {
            "feature": name,
            "sketch": name,
            "plane": plane,
            "shape": f"gear-{teeth}",
            "module_mm": float(module_mm),
            "teeth": int(teeth),
            "pressure_angle_deg": float(pressure_angle_deg),
            "area_mm2": round(gear.area_mm2(points), 4),
            **{k: round(v, 4) for k, v in gear.dimensions(module_mm, teeth).items()},
            # Said here, at the moment of use, because the tool description
            # alone was not enough: asked for a ring gear, a live agent skipped
            # the disc, padded this profile directly and reported the external
            # gear it got as a 70 mm ring.
            "next_step": (
                "catia_pad this sketch for an EXTERNAL gear. "
                + (
                    "catia_pocket it through the existing solid for an INTERNAL ring gear."
                    if had_solid
                    else "For an INTERNAL ring gear this part needs its disc FIRST: "
                    "the part is still empty, so pad a circle larger than "
                    "tip_diameter_mm before drawing the gear profile, then pocket."
                )
            ),
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
        part = self._part()
        factory = part.ShapeFactory
        before = self._solid_volume()
        pocket = factory.AddNewPocket(self._find_sketch(sketch), float(depth_mm or 1.0))
        if through_all:
            # 1 = catUpToLast in CATIA's length-type enumeration.
            pocket.FirstLimit.LimitMode = 1
        part.Update()

        # A sketch on an origin plane can sit on the far side of the solid, in
        # which case the cut goes away from the material and takes nothing with
        # it -- CATIA builds the feature and reports success either way. Do it
        # and look, exactly as `hole` does; this is the same defect, and it was
        # live here: the agent pocketed a 4x3 hole grid into a 140x100x8 plate
        # and the part came back at 112000 mm3, its full solid volume, with the
        # assistant reporting the holes as cut.
        if before and self._solid_volume() >= before:
            pocket.DirectionOrientation = 1
            part.Update()

        if before and self._solid_volume() >= before:
            raise CatiaOperationError(
                f"The pocket from {sketch} removed no material, so it missed the "
                "solid entirely. Check that the sketch overlaps the part -- a profile "
                "drawn on an origin plane the solid does not straddle cuts into empty "
                "space."
            )
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
        selected = self._select_edges(edges, feature)
        edge_fillet = None
        try:
            # 1 = catTangencyFilletEdgePropagation, which follows a chain of
            # tangent edges -- the behaviour a user means by "round that corner".
            edge_fillet = part.ShapeFactory.AddNewEdgeFilletWithConstantRadius(
                selected[0], 1, float(radius_mm)
            )
            for reference in selected[1:]:
                edge_fillet.AddObjectToFillet(reference)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(edge_fillet)
            raise CatiaOperationError(
                f"CATIA refused a {radius_mm:g} mm fillet on the {edges} edges "
                f"({exc}). The radius is usually too large for the adjacent faces "
                "-- try a smaller one -- or two of the selected edges meet in a "
                "corner the fillet cannot resolve; fillet fewer edges at once."
            ) from exc
        return self._feature_result(str(edge_fillet.Name))

    def _discard_failed_feature(self, shape: Any) -> None:  # pragma: no cover
        """Remove a feature whose Update failed, so the part stays buildable.

        A refused fillet is not a no-op: `AddNew...` already put the feature in
        the tree, and after the failed Update it sits there in an error state
        that makes every LATER Update fail too. Observed live: a 200 mm fillet
        was refused with a clean message, and a perfectly reasonable 1 mm
        fillet on the same part then failed with the same bare COM error. The
        refusal must take its wreckage with it.
        """
        if shape is None:
            return
        try:
            selection = self._document().Selection
            selection.Clear()
            selection.Add(shape)
            selection.Delete()
            self._part().Update()
        except Exception:  # noqa: BLE001 - cleanup must never mask the real error
            logger.warning("Could not remove a failed feature from the tree", exc_info=True)

    def chamfer(  # pragma: no cover - Windows only
        self,
        *,
        length_mm: float,
        angle_deg: float = 45.0,
        feature: str | None = None,
        edges: str = "all",
    ) -> dict[str, Any]:
        part = self._part()
        selected = self._select_edges(edges, feature)
        chamfer = None
        try:
            # (propagation=1 tangency, mode=1 length+angle, orientation=0).
            # Mode 0 is TWO LENGTHS: it reads the 45.0 default as a 45 mm
            # second leg, which cost one live part 1800 mm3 from a single edge
            # before the enum was swept. Verified: mode=1 with leg 4 at 45deg
            # removes exactly 0.5 * 4^2 * h per edge.
            chamfer = part.ShapeFactory.AddNewChamfer(
                selected[0], 1, 1, 0, float(length_mm), float(angle_deg)
            )
            for reference in selected[1:]:
                chamfer.AddElementToChamfer(reference)
            part.Update()
        except Exception as exc:  # noqa: BLE001
            self._discard_failed_feature(chamfer)
            raise CatiaOperationError(
                f"CATIA refused a {length_mm:g} mm chamfer on the {edges} edges "
                f"({exc}). The leg length is usually too large for the adjacent "
                "faces; try a smaller one, or chamfer fewer edges at once."
            ) from exc
        return self._feature_result(str(chamfer.Name))

    def _select_edges(self, edges: str, feature: str | None = None) -> list[Any]:  # pragma: no cover
        """References for the edges the caller means by "top", "vertical", ...

        `Selection.Search` is the one automation route to real topological
        references, and two things about it shaped this method:

        * Its query keywords are **localized to the UI language** -- a French
          V5-R33 refuses "Topology.Edge,all" with the same bare COM error it
          gives malformed queries, and answers "Topologie.Arête,tout" with
          every edge in the part. Two sessions concluded Search was broken;
          it was the grammar. The working one is detected once and cached.
        * Measuring the found edges one Evaluate call at a time is O(edges)
          COM round trips, and a padded gear has a thousand solid edges --
          classifying it that way blew through the daemon's 30 s watchdog on a
          live request. `vba.edge_map` searches AND measures in one Evaluate,
          then this method pulls references only for the edges it keeps.

        Classification is against the part's Z axis with a 1e-6 mm tolerance --
        these are exact machine coordinates, not floating measurements.
        """
        selection = self._document().Selection
        if feature is not None:
            try:
                scope_shape = self._body().Shapes.Item(feature)
            except Exception as exc:  # noqa: BLE001
                known = ", ".join(entry["name"] for entry in self._feature_list()) or "(none)"
                raise CatiaOperationError(
                    f"No feature named {feature!r} in this part. Features: {known}."
                ) from exc
        else:
            # An unscoped request scopes to the whole body: the query is then
            # always the ",sel" form, and the edge-map script needs a scope
            # object regardless (it clears the selection itself).
            scope_shape = self._body()

        grammar = getattr(self, "_search_grammar", None)
        errors: list[str] = []
        found = None
        for query, language in _SEARCH_GRAMMARS:
            if grammar is not None and grammar != language:
                continue
            query = query.rsplit(",", 1)[0] + ",sel"
            selection.Clear()
            selection.Add(scope_shape)
            try:
                selection.Search(query)
            except Exception as exc:  # noqa: BLE001 - wrong language, try the next
                errors.append(f"{language}: {exc}")
                continue
            self._search_grammar = language
            found = [
                index
                for index in range(1, int(selection.Count2) + 1)
                if "TriDim" in str(selection.Item2(index).Type)
            ]
            scoped_query = query
            break
        if found is None:
            raise CatiaOperationError(
                "CATIA refused every edge-search grammar this bridge knows "
                f"({'; '.join(errors)}). Its UI language may be one the bridge has "
                "no query vocabulary for yet -- see _SEARCH_GRAMMARS in catia_com.py."
            )
        if not found:
            raise CatiaOperationError(
                "This part has no solid edges yet. Pad or revolve something first."
            )

        if edges == "all":
            # No classification, so no measurement: references come straight
            # off the live selection, whatever the edge count.
            return [selection.Item2(index).Reference for index in found]

        # Classifying means measuring, and the cost is CATIA's measurable
        # construction itself (~0.1 s per edge, in-process or not), so it is
        # capped where the daemon's 30 s call budget still holds. A padded
        # gear has over a thousand solid edges; classifying it took 138 s
        # live, well past the watchdog.
        if len(found) > _EDGE_CLASSIFY_LIMIT:
            raise CatiaOperationError(
                f"This selection has {len(found)} solid edges, and working out "
                f"which are {edges!r} means measuring each one -- too slow for a "
                "part this detailed. Use edges='all', or narrow the selection "
                "with feature=<name> first."
            )

        measured = vba.edge_map(self._app, self._part(), scoped_query, scope_shape)
        if not measured:
            raise CatiaOperationError(
                f"No edge of this part could be measured, so {edges!r} cannot be "
                "resolved. Use edges='all' instead."
            )

        z_values = [p[2] for triple in measured.values() for p in triple]
        z_top, z_bottom = max(z_values), min(z_values)
        tolerance = 1e-6

        def matches(triple: tuple) -> bool:
            start, middle, end = triple
            points = (start, middle, end)
            if edges == "top":
                return all(abs(p[2] - z_top) < tolerance for p in points)
            if edges == "bottom":
                return all(abs(p[2] - z_bottom) < tolerance for p in points)
            if edges == "horizontal":
                return (
                    abs(start[2] - end[2]) < tolerance
                    and abs(start[2] - middle[2]) < tolerance
                )
            if edges == "vertical":
                return (
                    abs(start[0] - end[0]) < tolerance
                    and abs(start[1] - end[1]) < tolerance
                    and abs(start[2] - end[2]) > tolerance
                )
            return False

        chosen = {index: triple for index, triple in measured.items() if matches(triple)}
        if not chosen:
            raise CatiaOperationError(
                f"No {edges} edges on this part. Try edges='all', or a "
                "different selector."
            )
        # The selection still holds the edge-map's search result; indices are
        # stable because the script ran the same scoped query.
        return [selection.Item2(index).Reference for index in sorted(chosen)]

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

    def pattern_rectangular(  # pragma: no cover - Windows only
        self,
        *,
        plane: str,
        count: int,
        spacing_mm: float,
        second_count: int = 1,
        second_spacing_mm: float | None = None,
        feature: str | None = None,
    ) -> dict[str, Any]:
        """Repeat a feature on a grid lying in one of the origin planes.

        `count` and `second_count` are totals *including* the feature already
        there, because the seed is placed at grid position (1, 1) -- see
        `_PATTERN_PLANE_AXES`. Both directions come from the one plane: for XY
        that is X then Y.
        """
        part = self._part()
        target = self._shape_or_last(feature)
        reference = part.CreateReferenceFromObject(getattr(part.OriginElements, _ORIGIN_PLANE[plane]))
        second = int(second_count or 1)
        step2 = float(second_spacing_mm if second_spacing_mm is not None else 0.0)
        if second > 1 and step2 <= 0.0:
            raise CatiaOperationError(
                "second_spacing_mm is needed whenever second_count is more than 1, "
                "or the second row lands on top of the first."
            )

        try:
            pattern = part.ShapeFactory.AddNewRectPattern(
                target, int(count), second, float(spacing_mm), step2, 1, 1,
                reference, reference, True, True, 0.0,
            )
            part.Update()
        except Exception as exc:  # noqa: BLE001
            first_axis, second_axis = _PATTERN_PLANE_AXES[plane]
            raise CatiaOperationError(
                f"CATIA could not repeat {target.Name} {count} times along "
                f"{first_axis} ({exc}). Check the count and spacing against the "
                f"part's size along {first_axis}"
                + (f" and {second_axis}" if second > 1 else "")
                + "."
            ) from exc
        return self._pattern_result(pattern, int(count) * second)

    def _pattern_result(self, pattern: Any, instances: int) -> dict[str, Any]:
        """A pattern's post-state, saying plainly how many instances were asked for.

        A pattern is laid out from the seed outwards, so copies can fall past
        the edge of the part -- and CATIA does not refuse that. It builds the
        feature and silently cuts only the copies that meet material, leaving
        partial holes at the boundary. Measured live: a 4x3 grid at 25 mm on a
        140x100 plate with the seed at the origin removed 7.5 holes' worth of
        material, not 12, and nothing in the result said so.

        The count cannot be recovered from the volume without knowing what one
        instance removes, so this does not guess. It states the number
        requested and leaves the reported volume beside it, which is what lets
        a caller notice the two disagree.
        """
        result = self._feature_result(str(pattern.Name))
        result["instances_requested"] = instances
        result["instance_note"] = (
            f"{instances} instances were requested, counting the original. Copies "
            "that reach past the edge of the part are cut short or dropped without "
            "an error, so confirm volume_mm3 against the shape you expected before "
            "telling the user how many features there are."
        )
        return result

    def pattern_circular(  # pragma: no cover - Windows only
        self,
        *,
        count: int,
        plane: str = "XY",
        total_angle_deg: float = 360.0,
        feature: str | None = None,
    ) -> dict[str, Any]:
        """Repeat a feature evenly around the origin, in the named plane.

        The seed must sit *off* the axis: rotating something already centred
        produces copies on top of it and changes nothing, which is what this
        looked like before the arguments were right.

        CATIA wants a point for the centre of rotation and this bridge has no
        vocabulary for one, so a sketch holding a single point at the origin is
        made on demand -- the same on-demand trick `_sketch_with_axis` uses for
        the revolution axis.
        """
        part = self._part()
        target = self._shape_or_last(feature)

        sketch = self._body().Sketches.Add(getattr(part.OriginElements, _ORIGIN_PLANE[plane]))
        factory = sketch.OpenEdition()
        try:
            factory.CreatePoint(0.0, 0.0)
        finally:
            sketch.CloseEdition()
        part.Update()

        centre = part.CreateReferenceFromObject(sketch)
        axis = part.CreateReferenceFromObject(getattr(part.OriginElements, _ORIGIN_PLANE[plane]))
        step_deg = float(total_angle_deg) / int(count)

        before = self._solid_volume()
        try:
            pattern = part.ShapeFactory.AddNewCircPattern(
                target, 1, int(count), 0.0, step_deg, 1, 1, centre, axis, True, 0.0, False
            )
            part.Update()
        except Exception as exc:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA could not repeat {target.Name} {count} times around the "
                f"{plane} plane ({exc}). Two things cause this: the feature sits on "
                "the axis, so every copy lands on top of it -- give it an off-centre "
                "position first, with catia_hole's inset_mm -- or the copies swing "
                "off the edge of the material, which fails rather than trimming."
            ) from exc

        result = self._pattern_result(pattern, int(count))
        if isinstance(result.get("volume_mm3"), (int, float)) and before and count > 1:
            # A centred seed rotates onto itself: the feature builds, nothing
            # changes, and the caller is told a bolt circle exists that does not.
            if abs(float(result["volume_mm3"]) - before) < 1e-6:
                raise CatiaOperationError(
                    f"Repeating {target.Name} around the {plane} plane changed nothing, "
                    "so the copies landed on top of the original. A circular pattern "
                    "needs a feature that is off-centre; move it away from the axis first."
                )
        return result

    def _shape_or_last(self, feature: str | None) -> Any:  # pragma: no cover
        """The named shape, or the most recently built one when none is named."""
        body = self._body()
        if feature:
            try:
                return body.Shapes.Item(feature)
            except Exception as exc:  # noqa: BLE001
                known = ", ".join(entry["name"] for entry in self._feature_list()) or "(none)"
                raise CatiaOperationError(
                    f"No feature named {feature!r} in this part. Features: {known}."
                ) from exc
        shapes = body.Shapes
        if int(shapes.Count) == 0:
            raise CatiaOperationError(
                "This part has no features yet, so there is nothing to repeat. Build "
                "one with catia_pad, catia_pocket or catia_hole first."
            )
        return shapes.Item(int(shapes.Count))

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
        # ...but only for the document it was chosen for. See `set_material`.
        chosen = getattr(self, "_density_kg_m3", None)
        belongs_here = getattr(self, "_density_document", None) == self._document_key()
        if chosen and belongs_here and abs(density_kg_m3 - CATIA_DEFAULT_DENSITY_KG_M3) <= 5.0:
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

    # -- driving the interface -----------------------------------------------
    #
    # Everything above this line goes through COM and stops working the moment
    # CATIA puts up a modal dialog. Everything below goes through the window
    # tree instead (`ui_automation.py`), which is exactly why it keeps working
    # then -- and why these are the tools that get CATIA unstuck.
    #
    # The main window handle is cached for the same reason. Finding it needs
    # `Application.Caption`, which is a COM read; if the handle were looked up
    # per call, the first thing a blocked CATIA would break is the tool meant
    # to unblock it.

    def _main_window(self) -> int:  # pragma: no cover - Windows only
        cached = getattr(self, "_hwnd", 0)
        if cached and ui.AVAILABLE:
            try:
                if ctypes.WinDLL("user32").IsWindow(cached):  # type: ignore[attr-defined]
                    return int(cached)
            except Exception:  # noqa: BLE001 - fall through to a fresh lookup
                pass
        caption = ""
        try:
            caption = str(self._app.Caption)
        except Exception:  # noqa: BLE001 - COM may be blocked; that is survivable
            caption = ""
        handle = 0
        if caption:
            handle = ui.window_titled(caption)
        if not handle:
            handle = ui.main_window()
        self._hwnd = handle
        return handle

    def list_commands(  # pragma: no cover - Windows only
        self, *, search: str = "", menu: str = ""
    ) -> dict[str, Any]:
        window = self._main_window()
        try:
            items = ui.read_menu(window)
        except ui.UiUnavailable as exc:
            raise CatiaOperationError(str(exc)) from exc
        wanted = ui_policy.fold(search)
        top = ui_policy.fold(menu)
        found: list[dict[str, Any]] = []
        for root in items:
            if top and ui_policy.fold(root.label) != top:
                continue
            for item in root.walk():
                if item.is_submenu:
                    continue
                path = " > ".join(item.path)
                if wanted and wanted not in ui_policy.fold(path):
                    continue
                found.append({"command": item.label, "menu": path, "available": item.enabled})
        return {
            "workbench": self._workbench_name(),
            "commands": found[:200],
            "truncated": len(found) > 200,
            "note": (
                "These are this seat's own labels, read from its live menus. Use them "
                "verbatim; they are in the interface's language."
            ),
        }

    def _workbench_name(self) -> str:  # pragma: no cover - Windows only
        try:
            return str(self._app.ActiveDocument.GetWorkbench("").Name)
        except Exception:  # noqa: BLE001 - there is no reliable "current workbench"
            # CATIA exposes no property for the active workbench, and guessing
            # from the window title is wrong as often as it is right. Empty is
            # the honest answer.
            return ""

    def run_command(  # pragma: no cover - Windows only
        self,
        *,
        command: str,
        candidates: list[str] | None = None,
        command_name: str = "",
        command_key: str = "",
        menu_hint: list[str] | None = None,
    ) -> dict[str, Any]:
        ui_policy.check([command, command_name, *(candidates or [])])
        window = self._main_window()
        if ui.active_dialog(window) is not None:
            raise CatiaOperationError(
                "A CATIA dialog is already open and waiting for input. Read it with "
                "catia_describe_dialog and finish or cancel it first."
            )

        wanted = [c for c in [*(candidates or []), command] if c]
        folded = {ui_policy.fold(c) for c in wanted}

        # The menu first, because it is verifiable. `StartCommand` accepts any
        # string and silently ignores the ones it does not know, so a wrong
        # translation reports success and builds nothing. A menu item either
        # exists on this seat or it does not, and if it exists but is greyed
        # out we can say *that* instead of "it did not work".
        try:
            items = ui.read_menu(window)
        except ui.UiUnavailable:
            items = []
        match = ui.find_menu_item(
            items, lambda item: not item.is_submenu and ui_policy.fold(item.label) in folded
        )
        if match is not None:
            reason = ui_policy.refusal(match.label)
            if reason is not None:
                raise CatiaOperationError(
                    f"The bridge does not drive {match.label!r}: it {reason}."
                )
            if not match.enabled:
                raise CatiaOperationError(
                    f"{match.label!r} is on the menu at {' > '.join(match.path)} but is "
                    "greyed out, so CATIA will not run it. Its preconditions are not "
                    "met -- usually nothing is selected, or another workbench owns it. "
                    "Select the input with catia_select, or switch workbench."
                )
            try:
                ui.invoke_menu(window, match)
            except ui.UiUnavailable as exc:
                raise CatiaOperationError(str(exc)) from exc
            return self._after_command(
                window, command_name or command, match.label, "menu", " > ".join(match.path)
            )

        # Not on a menu: a toolbar-only command, or a label this seat words
        # differently. `StartCommand` is the fallback and its silence is
        # reported honestly rather than dressed up as success.
        for candidate in wanted:
            try:
                self._app.StartCommand(candidate)
            except Exception:  # noqa: BLE001 - an unknown name is not an error to CATIA
                continue
            result = self._after_command(window, command_name or command, candidate, "command", "")
            if result["dialog_open"]:
                return result
        return self._after_command(
            window, command_name or command, wanted[0] if wanted else command, "command", ""
        ) | {
            "verified": False,
            "note": (
                "CATIA was asked to start this command but reports nothing back, and no "
                "dialog opened. It may have run, or it may not recognise the name on "
                "this interface. Check with catia_list_features, or find the seat's own "
                "label with catia_list_commands."
            ),
        }

    def _after_command(  # pragma: no cover - Windows only
        self, window: int, command: str, label: str, how: str, path: str
    ) -> dict[str, Any]:
        """Let the dialog appear, then report what is on screen."""
        dialog = None
        deadline = time.monotonic() + _DIALOG_WAIT_S
        while time.monotonic() < deadline:
            try:
                dialog = ui.active_dialog(window)
            except ui.UiUnavailable:
                dialog = None
            if dialog is not None:
                break
            time.sleep(_DIALOG_POLL_S)
        return {
            "command": command,
            "matched_label": label,
            "started_via": how,
            "menu": path,
            "started": True,
            "verified": True,
            "dialog_open": dialog is not None,
            "dialog": dialog.describe() if dialog else None,
            "next": (
                "Fill the dialog and press OK; nothing is built until you do."
                if dialog
                else "No dialog opened, so the command either finished or is waiting "
                "for a selection in the viewport."
            ),
        }

    def describe_dialog(self) -> dict[str, Any]:  # pragma: no cover - Windows only
        window = self._main_window()
        try:
            dialog = ui.active_dialog(window)
        except ui.UiUnavailable as exc:
            raise CatiaOperationError(str(exc)) from exc
        if dialog is None:
            return {
                "dialog_open": False,
                "note": "CATIA is not showing a dialog; nothing is waiting for input.",
            }
        described = dialog.describe()
        unknown = [c for c in dialog.controls if c.kind == "other" and c.value]
        if not described["fields"] and unknown:
            # Self-diagnosing rather than silently empty: CATIA draws some of
            # its own widgets, and on a seat where none of them classify, this
            # is the line that says which window classes to teach `_classify`.
            described["unrecognised_controls"] = [
                {"class": c.window_class, "text": c.value[:80]} for c in unknown[:20]
            ]
            described["note"] = (
                "This dialog's fields are drawn with widgets the bridge does not "
                "recognise, so they cannot be filled in. Press Cancel and use a "
                "purpose-built tool, or ask the user to complete it."
            )
        return {"dialog_open": True, **described}

    def fill_dialog(  # pragma: no cover - Windows only
        self, *, fields: list[dict[str, Any]]
    ) -> dict[str, Any]:
        window = self._main_window()
        dialog = ui.active_dialog(window)
        if dialog is None:
            raise CatiaOperationError(
                "No CATIA dialog is open, so there is nothing to fill in. Run the "
                "command first with catia_run_command."
            )
        filled: list[str] = []
        for item in fields:
            name = str(item.get("name", ""))
            value = str(item.get("value", ""))
            control = _match_control(dialog, name)
            try:
                if control.kind == "choice":
                    ui.set_choice(control, value)
                elif control.kind in {"checkbox", "radio"}:
                    ui.set_checked(control, value.strip().lower() in _TRUTHY)
                else:
                    ui.set_text(control, value)
            except ui.UiUnavailable as exc:
                raise CatiaOperationError(str(exc)) from exc
            filled.append(control.label or name)
        after = ui.active_dialog(window)
        return {
            "filled": filled,
            "dialog": after.describe() if after else None,
            "next": "Press OK with catia_dialog_action to apply it.",
        }

    def dialog_action(  # pragma: no cover - Windows only
        self, *, action: str, button: str = "", labels: list[str] | None = None
    ) -> dict[str, Any]:
        window = self._main_window()
        dialog = ui.active_dialog(window)
        if dialog is None:
            raise CatiaOperationError("No CATIA dialog is open, so there is no button to press.")

        wanted = [button] if button else list(labels or [])
        target = None
        for candidate in wanted:
            folded = ui_policy.fold(candidate)
            target = next(
                (b for b in dialog.buttons() if ui_policy.fold(b.label) == folded), None
            )
            if target is not None:
                break
        if target is None and not button:
            # Nothing matched by label. A dialog built on the common controls
            # still numbers OK 1 and Cancel 2 whatever they read, which is the
            # language-proof second chance.
            control_id = _STANDARD_IDS.get(action)
            if control_id is not None:
                target = next(
                    (b for b in dialog.buttons() if b.control_id == control_id), None
                )
        if target is None:
            offered = ", ".join(b.label for b in dialog.buttons() if b.label) or "none it can read"
            raise CatiaOperationError(
                f"The {dialog.title!r} dialog has no {button or action!r} button. "
                f"It offers: {offered}."
            )
        try:
            ui.click(dialog.handle, target)
        except ui.UiUnavailable as exc:
            raise CatiaOperationError(str(exc)) from exc
        time.sleep(_DIALOG_POLL_S)
        still = ui.active_dialog(window)
        return {
            "pressed": target.label,
            "action": action,
            "dialog_open": still is not None,
            "dialog": still.describe() if still else None,
        }

    def press_key(self, *, key: str) -> dict[str, Any]:  # pragma: no cover - Windows only
        window = self._main_window()
        dialog = ui.active_dialog(window)
        try:
            ui.press_key(dialog.handle if dialog else window, key)
        except ui.UiUnavailable as exc:
            raise CatiaOperationError(str(exc)) from exc
        time.sleep(_DIALOG_POLL_S)
        still = ui.active_dialog(window)
        return {"key": key, "dialog_open": still is not None}

    def switch_workbench(  # pragma: no cover - Windows only
        self,
        *,
        workbench: str,
        workbench_id: str = "",
        workbench_name: str = "",
        menu_path: list[str] | None = None,
        licence: str = "",
    ) -> dict[str, Any]:
        name = workbench_name or workbench
        if workbench_id:
            try:
                self._app.StartWorkbench(workbench_id)
                return {
                    "workbench": name,
                    "reached_by": "identifier",
                    "identifier": workbench_id,
                    "licence": licence,
                }
            except Exception:  # noqa: BLE001 - fall back to the Start menu
                pass

        window = self._main_window()
        try:
            items = ui.read_menu(window)
        except ui.UiUnavailable as exc:
            raise CatiaOperationError(str(exc)) from exc
        # The Start menu is the first item on every V5 menu bar whatever it is
        # called, which is the one property of a menu that survives translation.
        start = items[0] if items else None
        wanted = ui_policy.fold((menu_path or [name])[-1])
        match = (
            ui.find_menu_item(
                [start], lambda item: not item.is_submenu and ui_policy.fold(item.label) == wanted
            )
            if start
            else None
        )
        if match is None:
            raise CatiaOperationError(
                f"{name} is not on this seat's Start menu. Either it is not installed "
                f"or the licence for it is missing ({licence or 'licence unknown'}). "
                "catia_list_commands shows what the Start menu really offers."
            )
        if not match.enabled:
            raise CatiaOperationError(
                f"{name} is on the Start menu but greyed out, which on a workbench "
                f"means the licence is not available ({licence or 'licence unknown'})."
            )
        ui.invoke_menu(window, match)
        return {
            "workbench": name,
            "reached_by": "Start menu",
            "displayed_as": match.label,
            "licence": licence,
        }

    def select(  # pragma: no cover - Windows only
        self, *, features: list[str], add: bool = False
    ) -> dict[str, Any]:
        document = self._document()
        selection = document.Selection
        if not features:
            selection.Clear()
            return {"selected": [], "count": 0, "note": "Selection cleared."}
        if not add:
            selection.Clear()
        part = self._part()
        missing: list[str] = []
        selected: list[str] = []
        for name in features:
            element = _find_named(part, name)
            if element is None:
                missing.append(name)
                continue
            selection.Add(element)
            selected.append(name)
        if missing:
            raise CatiaOperationError(
                f"Not in this part: {', '.join(missing)}. Call catia_list_features to "
                "see what is there; names are case-sensitive and end in a number."
            )
        return {"selected": selected, "count": _selection_count(selection, len(selected))}


# -- helpers -----------------------------------------------------------------

#: How long to wait for a command's dialog to appear before reporting that none
#: did. CATIA opens one in well under a second on a warm session; the ceiling is
#: for a cold one that is still loading the workbench's resources.
_DIALOG_WAIT_S = 3.0
_DIALOG_POLL_S = 0.15

_TRUTHY = {"true", "1", "yes", "on", "checked"}

#: Win32 standard dialog control ids, for the fallback in `dialog_action`.
_STANDARD_IDS = {"ok": 1, "cancel": 2, "yes": 6, "no": 7, "close": 8}


def _match_control(dialog: Any, name: str) -> Any:  # pragma: no cover - Windows only
    """The field a label names, matched the way a human reads the dialog."""
    folded = ui_policy.fold(name)
    for control in dialog.fields():
        if ui_policy.fold(control.label) == folded:
            return control
    # Prefix, because a dialog labels a box `Length:` and the agent asks for
    # `Length` -- and because `First limit` is what `Length` sits under.
    for control in dialog.fields():
        if folded and ui_policy.fold(control.label).startswith(folded):
            return control
    offered = ", ".join(c.label for c in dialog.fields() if c.label) or "none it can read"
    raise CatiaOperationError(
        f"{name!r} is not a field of the {dialog.title!r} dialog. It has: {offered}."
    )


def _selection_count(selection: Any, fallback: int) -> int:
    """How many things are selected, or how many we just added.

    `Count2` is the current interface and `Count` the older one, and this is a
    cosmetic number on a successful result. Letting a property read fail the
    whole call would mean the selection was made and the agent was told it was
    not -- which is the one outcome worth ruling out here.
    """
    for attribute in ("Count2", "Count"):
        try:
            return int(getattr(selection, attribute))
        except Exception:  # noqa: BLE001 - try the next one, then give up
            continue
    return fallback


def _find_named(part: Any, name: str) -> Any:  # pragma: no cover - Windows only
    """A feature, sketch or body by the name shown in the specification tree."""
    for collection in ("Bodies", "Sketches", "HybridBodies"):
        try:
            group = getattr(part, collection)
        except Exception:  # noqa: BLE001 - not every document has every collection
            continue
        try:
            return group.Item(name)
        except Exception:  # noqa: BLE001 - Item raises rather than returning None
            pass
    try:
        return part.FindObjectByName(name)
    except Exception:  # noqa: BLE001 - the name is simply not in this part
        return None


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
