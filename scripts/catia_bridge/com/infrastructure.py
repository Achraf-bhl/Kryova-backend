"""File exchange and display: import, export, viewpoint, graphic properties.

Import is the one that matters commercially. Before it, nothing a customer
already owned could enter the product — Kryova could write STEP and read
nothing. CATIA reads a dozen formats given the Data Exchange licences, and the
whole job here is to call the right translator and to say *which licence* is
missing when one is, rather than reporting a generic failure on a seat whose
STEP licence is perfectly fine.

**The translator tokens are short, and that is not a typo.**
`Document.ExportData(path, format)` wants `"stp"`, not `"step"`; `"igs"`, not
`"iges"`. The IDL reference's own example is `Doc.ExportData("IGESDoc", "igs")`.
Passing the long name does not fall back — on a live V5-6R2023 it raises
`La methode ExportData a echoue`, which reads exactly like a licensing failure
and sends whoever is debugging it to the licence server for an afternoon.
`catia_com.export_step` learned this the hard way and pins `"stp"`; the table
below is the same knowledge for every other format.

The model never supplies a path. `catia_import` takes a *file name*, the server
resolves it against the uploads it holds and fills in `remote_path`, and the
daemon receives an already-validated location. A tool that let a model name an
arbitrary path would be an arbitrary-file-read primitive on an engineer's
workstation, reachable through one prompt injection.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext, resolve_element

logger = logging.getLogger("kryova.catia.com.infrastructure")

#: CATIA's short translator tokens, per format. See the module docstring: the
#: long names are silently wrong rather than rejected.
_FORMAT_TOKENS = {
    "step": "stp",
    "iges": "igs",
    "stl": "stl",
    "dxf": "dxf",
    "dwg": "dwg",
    "parasolid": "x_t",
    "acis": "sat",
    "jt": "jt",
    "3dxml": "3dxml",
    "vrml": "wrl",
    "vda": "vda",
    "cgr": "cgr",
    "pdf3d": "pdf",
    "catpart": "CATPart",
    "catproduct": "CATProduct",
    "v4model": "model",
}

#: The extension each format is written with, where it differs from the token.
_FORMAT_SUFFIX = {**{name: f".{token}" for name, token in _FORMAT_TOKENS.items()},
                  "step": ".stp", "iges": ".igs", "vrml": ".wrl", "parasolid": ".x_t",
                  "catpart": ".CATPart", "catproduct": ".CATProduct", "v4model": ".model"}

#: Which Data Exchange licence each format needs, so a refusal can name it.
_FORMAT_LICENCE = {
    "step": "STEP (ST1)",
    "iges": "IGES (IG1)",
    "stl": "STL (built in on most seats)",
    "dxf": "DXF/DWG (D2/DW1)",
    "dwg": "DXF/DWG (D2/DW1)",
    "parasolid": "Parasolid (PS1)",
    "acis": "ACIS (AS1)",
    "jt": "JT (JT1)",
    "3dxml": "3D XML (built in)",
    "vrml": "VRML (built in)",
    "vda": "VDA-FS (VD1)",
    "pdf3d": "3D PDF (built in on R21 and later)",
}

#: What each import mode asks CATIA to keep. `reference` links to the file
#: rather than copying it in, so the part follows the source when it changes.
_IMPORT_MODES = {"solid": 1, "surface": 2, "wireframe": 3, "reference": 0}

#: Named viewpoints, as the camera direction and up-vector each implies.
_VIEWPOINTS = {
    "iso": ((1.0, 1.0, 1.0), (0.0, 0.0, 1.0)),
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}

#: `CatRenderingMode` values.
_RENDER_MODES = {
    "shaded": 5,
    "shaded_with_edges": 3,
    "wireframe": 1,
    "hidden_line": 2,
    "transparent": 7,
}

#: Colours by name, as the RGB triples CATIA's `SetRealColor` takes. Named
#: rather than accepting hex, so a model cannot ask for a colour nobody can
#: describe and every drawing stays within one palette.
_COLOURS = {
    "red": (255, 0, 0),
    "green": (0, 176, 80),
    "blue": (0, 112, 192),
    "yellow": (255, 217, 0),
    "orange": (255, 128, 0),
    "purple": (112, 48, 160),
    "grey": (128, 128, 128),
    "gray": (128, 128, 128),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "brown": (132, 60, 12),
    "pink": (255, 153, 204),
    "cyan": (0, 176, 240),
    "magenta": (255, 0, 255),
}


class InfrastructureMixin:
    """Get data in and out, and control what CATIA is showing."""

    # -- import --------------------------------------------------------------

    def import_file(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        file: str,
        content_b64: str = "",
        content_hash: str = "",
        filename: str = "",
        format: str = "",  # noqa: A002 - the schema's name
        import_as: str = "solid",
        heal: bool | None = None,
        scale: float | None = None,
    ) -> dict[str, Any]:
        """Read an uploaded CAD file into CATIA and bring its geometry in.

        The bytes arrive with the call. The model names an upload and never a
        location, and there is no path that would mean the same thing on the
        server and on this workstation anyway — so the file is written to the
        bridge's own working directory, opened, and deleted. Empty bytes mean
        the server could not resolve the name, which is refused rather than
        guessed at: the alternative is opening whatever a made-up name hits.
        """
        if not content_b64:
            raise CatiaOperationError(
                f"The server did not resolve {file!r} to an uploaded file. Upload the "
                "file to Kryova first, then name it exactly as it appears there."
            )

        stem = filename or file
        detected = format or _format_from_suffix(Path(stem).suffix)
        if detected is None:
            raise CatiaOperationError(
                f"Cannot tell what kind of file {stem!r} is from its name. Say which "
                f"format it is: {', '.join(sorted(_FORMAT_TOKENS))}."
            )

        data = base64.b64decode(content_b64)
        if content_hash:
            _verify_transfer(data, content_hash)

        # Written under the file's real name, not a random one: CATIA picks its
        # translator from the extension, and the imported document inherits the
        # name the engineer will see in the tree.
        path = self.workdir / f"import-{uuid.uuid4().hex[:8]}-{_safe_name(stem)}"
        path.write_bytes(data)
        try:
            document = self._app.Documents.Open(str(path))
        except Exception as error:  # noqa: BLE001
            licence = _FORMAT_LICENCE.get(detected, detected)
            raise CatiaOperationError(
                f"CATIA could not read {stem!r} as {detected}. This needs the "
                f"{licence} Data Exchange licence on this workstation — ask for that "
                f"licence rather than retrying. ({error})"
            ) from error
        finally:
            # CATIA has loaded it into memory by now; leaving the temp file
            # behind would accumulate a copy of every import ever run.
            path.unlink(missing_ok=True)

        imported = _describe_import(document)
        if scale is not None and scale != 1.0:
            _scale_document(document, float(scale))
        if heal is not False and imported.get("surfaces", 0) > 0:
            imported["healed"] = _heal(document)

        return {
            "file": file,
            "format": detected,
            "document": str(document.Name),
            "import_as": import_as,
            **imported,
        }

    # -- export --------------------------------------------------------------

    def export(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        format: str = "step",  # noqa: A002
        note: str = "",
        step_schema: str = "ap214",
        tolerance_mm: float | None = None,
        binary: bool = True,
        max_inline_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Write the active document out in a neutral or native format."""
        document = self._document()
        try:
            self._part().Update()
        except CatiaOperationError:
            # A product or a drawing has no `Part` and does not need updating
            # the way a part does; exporting one is still perfectly valid.
            logger.debug("Active document is not a part; exporting without update")

        token = _FORMAT_TOKENS.get(format)
        if token is None:
            raise CatiaOperationError(
                f"{format!r} is not a format this can write. Use one of: "
                f"{', '.join(sorted(_FORMAT_TOKENS))}."
            )

        _apply_export_settings(self._app, format, step_schema, tolerance_mm, binary)
        path = self.workdir / f"export-{uuid.uuid4().hex[:8]}{_FORMAT_SUFFIX[format]}"
        try:
            document.ExportData(str(path), token)
        except Exception as error:  # noqa: BLE001
            licence = _FORMAT_LICENCE.get(format, format)
            raise CatiaOperationError(
                f"CATIA could not export {format}. This needs the {licence} licence on "
                f"this workstation. ({error})"
            ) from error

        if not path.is_file():
            raise CatiaOperationError(
                f"CATIA reported a successful {format} export but wrote no file."
            )
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)

        if max_inline_bytes is not None and len(data) > max_inline_bytes:
            raise CatiaOperationError(
                f"The exported file is {len(data) // (1024 * 1024)} MB, larger than the "
                "bridge can transfer in one piece. Export a simplified representation, "
                "or save it on the workstation and upload it directly."
            )

        return {
            "filename": f"{_safe_stem(str(document.Name))}{_FORMAT_SUFFIX[format]}",
            "format": format,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_b64": base64.b64encode(data).decode("ascii"),
            "note": note or None,
        }

    # -- display -------------------------------------------------------------

    def view_control(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        action: str,
        viewpoint: str = "",
        render_mode: str = "",
        elements: list[str] | None = None,
    ) -> dict[str, Any]:
        """Aim the camera, change the render mode, or hide and show geometry.

        Not cosmetic: `catia_capture_view` photographs whatever is on screen, so
        this is what decides what a screenshot actually shows. Isolating one
        feature and capturing it is how a model shows a user the thing it is
        talking about rather than the whole part.
        """
        try:
            window = self._app.ActiveWindow
            viewer = window.ActiveViewer
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "CATIA has no window open to control. This needs the application "
                "running with a document visible."
            ) from error

        if action == "fit":
            viewer.Reframe()
        elif action == "zoom_in":
            viewer.ZoomIn()
        elif action == "zoom_out":
            viewer.ZoomOut()
        elif action == "viewpoint":
            _aim(viewer, viewpoint or "iso")
        elif action == "render_mode":
            _render(viewer, render_mode or "shaded_with_edges")
        else:
            return self._visibility(action, elements or [])

        viewer.Update()
        return {
            "action": action,
            "viewpoint": viewpoint or None,
            "render_mode": render_mode or None,
        }

    def _visibility(  # pragma: no cover - Windows only
        self: ComContext, action: str, elements: list[str]
    ) -> dict[str, Any]:
        """Hide, show, or isolate named geometry.

        Isolate is the composite the others are worth having for: hide
        everything, then show only what was asked for. Doing it in that order
        matters — showing first and then hiding everything else hides what was
        just shown.
        """
        document = self._document()
        selection = document.Selection
        part = self._part()

        if action == "isolate":
            selection.Clear()
            selection.Add(part.MainBody)
            _set_visibility(selection, visible=False)
            selection.Clear()

        if not elements and action != "isolate":
            raise CatiaOperationError(
                f"{action} needs `elements` — the names of what to {action}."
            )

        selection.Clear()
        for name in elements:
            selection.Add(resolve_element(part, name))
        _set_visibility(selection, visible=action in {"show", "isolate"})
        selection.Clear()

        return {"action": action, "elements": list(elements)}

    def graphic_properties(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        elements: list[str],
        colour: str = "",
        transparency: int | None = None,
        line_weight: int | None = None,
        layer: str = "",
        show: bool | None = None,
    ) -> dict[str, Any]:
        """Colour, transparency, line weight and layer, on named geometry.

        Worth more than it looks: a model that has just built five features can
        colour the one it is describing, and the user sees which. Every setting
        is applied only when given, so setting a colour does not reset
        transparency to opaque.
        """
        document = self._document()
        part = self._part()
        selection = document.Selection
        selection.Clear()
        for name in elements:
            selection.Add(resolve_element(part, name))

        if int(selection.Count) == 0:
            raise CatiaOperationError(
                "None of those elements could be selected, so there was nothing to "
                "change. Check the names with catia_list_features."
            )

        properties = selection.VisProperties
        applied: dict[str, Any] = {}

        if colour:
            rgb = _COLOURS.get(colour.lower())
            if rgb is None:
                raise CatiaOperationError(
                    f"{colour!r} is not a colour this understands. Use one of: "
                    f"{', '.join(sorted(_COLOURS))}."
                )
            properties.SetRealColor(*rgb, 1)
            applied["colour"] = colour
        if transparency is not None:
            # CATIA takes opacity 0-255 where the tool takes transparency 0-100:
            # 100% transparent is opacity 0, and inverting this the wrong way
            # makes every "make it see-through" call turn things solid.
            opacity = int(round((100 - int(transparency)) * 255 / 100))
            properties.SetRealOpacity(opacity, 1)
            applied["transparency"] = int(transparency)
        if line_weight is not None:
            properties.SetRealWidth(int(line_weight), 1)
            applied["line_weight"] = int(line_weight)
        if layer:
            properties.SetLayer(0 if layer.lower() == "none" else 1, layer)
            applied["layer"] = layer
        if show is not None:
            properties.SetShow(0 if show else 1)
            applied["show"] = bool(show)

        selection.Clear()
        try:
            part.Update()
        except Exception:  # noqa: BLE001 - display changes need no rebuild
            pass
        return {"elements": list(elements), "applied": applied}


# -- helpers -------------------------------------------------------------------


def _format_from_suffix(suffix: str) -> str | None:
    """Which format an extension names, or None when it is not one we read."""
    cleaned = suffix.lower().lstrip(".")
    aliases = {
        "stp": "step", "step": "step", "igs": "iges", "iges": "iges", "stl": "stl",
        "dxf": "dxf", "dwg": "dwg", "x_t": "parasolid", "x_b": "parasolid",
        "sat": "acis", "jt": "jt", "3dxml": "3dxml", "wrl": "vrml", "vda": "vda",
        "catpart": "catpart", "catproduct": "catproduct", "model": "v4model",
        "cgr": "cgr",
    }
    return aliases.get(cleaned)


def _verify_transfer(data: bytes, expected: str) -> None:
    """Refuse bytes that are not the ones the server sent.

    The transfer is what is being checked, not the file's contents: a truncated
    one produces a CATIA import failure that reads exactly like a corrupt STEP
    file, and this turns it back into "the transfer did not finish".
    """
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise CatiaOperationError(
            "The file that arrived does not match the one uploaded — the transfer was "
            "incomplete. Try the import again."
        )


def _safe_name(name: str) -> str:
    """A filename safe to write, keeping the extension CATIA dispatches on."""
    cleaned = Path(name).name
    return "".join(
        character for character in cleaned if character.isalnum() or character in "-_."
    ) or "import.stp"


def _describe_import(document: Any) -> dict[str, Any]:  # pragma: no cover - Windows only
    """What actually came in: solids, surfaces, and whether anything did.

    An import that reads the file successfully and produces no geometry is the
    common failure with IGES and DXF, and CATIA reports it as success. Counting
    what arrived is the only way to tell the difference.
    """
    try:
        part = document.Part
    except Exception:  # noqa: BLE001 - a product import has no single part
        return {"bodies": None, "surfaces": None}

    solids = int(part.Bodies.Count)
    surfaces = 0
    try:
        for index in range(1, int(part.HybridBodies.Count) + 1):
            surfaces += int(part.HybridBodies.Item(index).HybridShapes.Count)
    except Exception:  # noqa: BLE001 - no construction geometry at all
        pass

    result: dict[str, Any] = {"bodies": solids, "surfaces": surfaces}
    if solids == 0 and surfaces == 0:
        result["warning"] = (
            "CATIA read the file but found no geometry in it. That usually means the "
            "file holds only assembly structure or an unsupported entity type."
        )
    return result


def _scale_document(document: Any, factor: float) -> None:  # pragma: no cover
    """Scale imported geometry, for a file authored in the wrong unit.

    Left as a warning rather than an error when CATIA will not do it: the
    geometry is already in, and failing the whole import over the scale would
    throw away a successful read.
    """
    try:
        part = document.Part
        part.HybridShapeFactory.AddNewScaling(part.MainBody, None, factor)
        part.Update()
    except Exception as error:  # noqa: BLE001
        logger.warning("Imported geometry could not be scaled by %s: %s", factor, error)


def _heal(document: Any) -> bool:  # pragma: no cover - Windows only
    """Close small gaps in imported surfaces. Reports whether it ran."""
    try:
        part = document.Part
        factory = part.HybridShapeFactory
        healing = factory.AddNewHealing()
        healing.AddElementToHeal(part.MainBody)
        part.Update()
        return True
    except Exception as error:  # noqa: BLE001 - healing is a licensed extra
        logger.debug("Healing not run on import: %s", error)
        return False


def _apply_export_settings(  # pragma: no cover - Windows only
    app: Any, format: str, step_schema: str, tolerance_mm: float | None, binary: bool
) -> None:
    """Push the format's options into CATIA's settings before exporting.

    These live in Tools > Options rather than on the export call, which is why
    they are set here and not passed as arguments. A failure is logged rather
    than raised: the export still works, it just uses whatever the workstation
    was already configured for, and that is better than not exporting at all.
    """
    try:
        if format == "step":
            settings = app.SettingControllers.Item("CATIAStepSettingCtrl")
            settings.SetApplicationProtocol({"ap203": 1, "ap214": 2, "ap242": 3}[step_schema])
        elif format in {"stl", "vrml"} and tolerance_mm is not None:
            settings = app.SettingControllers.Item("CATIAStlSettingCtrl")
            settings.SetMaximumSag(float(tolerance_mm))
            if format == "stl":
                settings.SetStlType(1 if binary else 0)
    except Exception as error:  # noqa: BLE001
        logger.debug("Export settings for %s left at their defaults: %s", format, error)


def _aim(viewer: Any, viewpoint: str) -> None:  # pragma: no cover - Windows only
    """Point the camera along a named direction."""
    direction, up = _VIEWPOINTS[viewpoint]
    camera = viewer.Viewpoint3D
    camera.SightDirection = [-value for value in direction]
    camera.UpDirection = list(up)
    viewer.Reframe()


def _render(viewer: Any, mode: str) -> None:  # pragma: no cover - Windows only
    """Switch the render mode, by name."""
    value = _RENDER_MODES.get(mode)
    if value is None:
        raise CatiaOperationError(
            f"{mode!r} is not a render mode. Use one of: {', '.join(sorted(_RENDER_MODES))}."
        )
    viewer.RenderingMode = value


def _set_visibility(selection: Any, *, visible: bool) -> None:  # pragma: no cover
    """Show or hide everything currently selected."""
    # `SetShow` takes 0 for shown and 1 for hidden -- inverted from every other
    # boolean in this file, and the reason this is a named helper rather than
    # two inline calls that would each have to remember it.
    selection.VisProperties.SetShow(0 if visible else 1)


def _safe_stem(name: str) -> str:
    """A filename stem with the extension and any path separators removed."""
    stem = Path(name).stem
    return "".join(character for character in stem if character.isalnum() or character in "-_") or "export"
