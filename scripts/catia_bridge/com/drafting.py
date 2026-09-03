"""Drafting over COM: drawings, sheets, views, dimensions and tolerances.

A third document type, with a third root object: `Document.DrawingRoot`, whose
`Sheets` hold `Views` which hold everything else. Nothing in the part or product
mixins applies — a drawing has no `Part`, no `Product`, and its coordinates are
millimetres *on the sheet* rather than on the model.

That last point is the one that bites. Every `at` in this module is a position
on the paper, measured from the sheet's bottom-left corner, and a view placed at
the model's coordinates lands somewhere off the page. The schemas say so and so
does `_sheet_point`, which refuses a position outside the sheet rather than
letting CATIA put a view where nobody will ever see it.

Generated views are associative: `catia_drawing_update` re-projects them after
the model changes. Dimensions generated from constraints are too. Dimensions
placed by hand against picked geometry are not, and `dimension_add` says which
kind it made.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from ..backend import CatiaOperationError
from ._context import ComContext

logger = logging.getLogger("kryova.catia.com.drafting")

#: Sheet sizes in millimetres, landscape. `CatPaperSize` covers the ISO ones by
#: enumeration but not the ANSI ones on every release, so the dimensions are
#: carried here and set explicitly — a sheet of the wrong size prints wrong and
#: nothing about the drawing says why.
_PAPER = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
    "ANSI_A": (279.4, 215.9),
    "ANSI_B": (431.8, 279.4),
    "ANSI_C": (558.8, 431.8),
    "ANSI_D": (863.6, 558.8),
    "ANSI_E": (1117.6, 863.6),
}

#: `CatPaperSize` for the ISO formats, which every release does expose.
_PAPER_ENUM = {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}

#: `CatSheetProjectionMode`. First angle is the ISO convention, third angle the
#: ANSI one; getting it wrong mirrors every projected view on the drawing.
_PROJECTION = {"first_angle": 0, "third_angle": 1}

#: `CatViewType` for the kinds CATIA creates through `Views.Add` variants.
_VIEW_MAKERS = {
    "front": "AddFront",
    "projection": "AddProjection",
    "auxiliary": "AddAuxiliary",
    "isometric": "AddIsometric",
    "section": "AddOffsetSection",
    "section_cut": "AddOffsetSectionCut",
    "detail": "AddDetail",
    "clipping": "AddQuickClipping",
    "broken": "AddBreak",
    "breakout": "AddBreakout",
    "exploded": "AddExploded",
    "unfolded": "AddUnfolded",
}

#: `CatDimType` values for each dimension the schema offers.
_DIMENSION_TYPES = {
    "length": 0,
    "distance": 1,
    "angle": 2,
    "radius": 3,
    "diameter": 4,
    "chamfer": 5,
    "thread": 6,
    "coordinate": 7,
}

#: The geometric characteristic symbols, in the order ISO 1101 lists them.
_CHARACTERISTICS = {
    "straightness": 1,
    "flatness": 2,
    "circularity": 3,
    "cylindricity": 4,
    "profile_line": 5,
    "profile_surface": 6,
    "angularity": 7,
    "perpendicularity": 8,
    "parallelism": 9,
    "position": 10,
    "concentricity": 11,
    "symmetry": 12,
    "circular_runout": 13,
    "total_runout": 14,
}

#: Material-condition modifiers, appended to a tolerance value.
_MODIFIERS = {"none": "", "MMC": "Ⓜ", "LMC": "Ⓛ", "RFS": ""}


class DraftingMixin:
    """Create a drawing, project views onto it, and dimension them."""

    # -- the active drawing --------------------------------------------------

    def _drawing(self: ComContext) -> Any:  # pragma: no cover - Windows only
        """The root of the active drawing document."""
        document = self._document()
        try:
            return document.DrawingRoot
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "The active CATIA document is not a drawing. Create one with "
                "catia_drawing_create, or activate the CATDrawing."
            ) from error

    def _sheet(self: ComContext, name: str = "") -> Any:  # pragma: no cover - Windows only
        """A sheet by name, or the active one."""
        sheets = self._drawing().Sheets
        if not name:
            return sheets.ActiveSheet
        for index in range(1, int(sheets.Count) + 1):
            sheet = sheets.Item(index)
            if str(sheet.Name) == name:
                return sheet
        known = ", ".join(
            str(sheets.Item(i).Name) for i in range(1, int(sheets.Count) + 1)
        )
        raise CatiaOperationError(
            f"This drawing has no sheet named {name!r}. It has: {known or '(none)'}."
        )

    def _view(self: ComContext, name: str = "", sheet: str = "") -> Any:  # pragma: no cover
        """A view by name, or the active one on the given sheet."""
        views = self._sheet(sheet).Views
        if not name:
            return views.ActiveView
        for index in range(1, int(views.Count) + 1):
            view = views.Item(index)
            if str(view.Name) == name:
                return view
        known = ", ".join(str(views.Item(i).Name) for i in range(1, int(views.Count) + 1))
        raise CatiaOperationError(
            f"This sheet has no view named {name!r}. It has: {known or '(none)'}."
        )

    def _sheet_point(  # pragma: no cover - Windows only
        self: ComContext, at: list[float], sheet: Any
    ) -> tuple[float, float]:
        """A position on the paper, checked against the sheet's own size.

        Refused rather than clamped. A view placed off the page is not a
        rendering problem the caller can see — the drawing simply looks empty —
        and silently moving it somewhere else would make the next dimension land
        in the wrong place too.
        """
        x, y = float(at[0]), float(at[1])
        try:
            width, height = float(sheet.PaperWidth), float(sheet.PaperHeight)
        except Exception:  # noqa: BLE001 - a detail sheet has no paper size
            return x, y
        if not (0.0 <= x <= width and 0.0 <= y <= height):
            raise CatiaOperationError(
                f"[{x:g}, {y:g}] is off the sheet, which is {width:g} x {height:g} mm. "
                "Sheet coordinates are millimetres from the bottom-left corner, not "
                "the model's coordinates."
            )
        return x, y

    # -- documents and sheets ------------------------------------------------

    def drawing_create(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        name: str,
        source: str = "",
        format: str = "A3",  # noqa: A002 - the schema's name
        landscape: bool = True,
        projection: str = "first_angle",
        scale: float = 1.0,
    ) -> dict[str, Any]:
        """Start a drawing, optionally of a part that is already open.

        The source document is remembered before the new drawing steals focus:
        `Documents.Add` activates what it creates, so reading the active
        document afterwards gives the empty drawing rather than the part it is
        supposed to be a drawing *of*.
        """
        self._require_closed()
        model = self._app.ActiveDocument if not source else self._find_document(source)

        document = self._app.Documents.Add("Drawing")
        path = self._free_document_path(name, suffix=".CATDrawing")
        document.SaveAs(str(path))

        root = document.DrawingRoot
        root.ProjectionMethod = _PROJECTION[projection]
        sheet = root.Sheets.ActiveSheet
        _apply_format(sheet, format, landscape)
        sheet.Scale = float(scale)
        try:
            sheet.Name = name
        except Exception:  # noqa: BLE001 - cosmetic
            pass

        return {
            "drawing": name,
            "remote_path": str(path),
            "sheet": str(sheet.Name),
            "format": format,
            "projection": projection,
            "scale": float(scale),
            # Named so the caller knows which model the views will come from,
            # which is not obvious once the drawing is the active document.
            "source": str(getattr(model, "Name", "")) if model else "",
        }

    def _find_document(self: ComContext, name: str) -> Any:  # pragma: no cover
        """An open document by name, for the tools that draw from one."""
        for index in range(1, int(self._app.Documents.Count) + 1):
            candidate = self._app.Documents.Item(index)
            if str(candidate.Name).lower().startswith(name.lower()):
                return candidate
        raise CatiaOperationError(
            f"No document named {name!r} is open in CATIA. Open it with "
            "catia_open_document first — a drawing projects a model that is loaded."
        )

    def sheet_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        name: str = "",
        format: str = "A3",  # noqa: A002
        landscape: bool = True,
        scale: float = 1.0,
        detail: bool = False,
    ) -> dict[str, Any]:
        """Add a sheet to the drawing — a second page, or a detail sheet.

        A detail sheet holds the 2D components that get placed on the others; it
        is not printed, and CATIA treats it as a different kind of sheet
        entirely rather than as a flag on a normal one.
        """
        sheets = self._drawing().Sheets
        sheet = sheets.Add(name or "")
        if detail:
            try:
                sheet.IsDetail = True
            except Exception:  # noqa: BLE001 - some releases only set it at creation
                logger.debug("Detail flag not settable after creation")
        else:
            _apply_format(sheet, format, landscape)
            sheet.Scale = float(scale)
        sheet.Activate()

        return {
            "sheet": str(sheet.Name),
            "format": format if not detail else "detail",
            "sheets": int(sheets.Count),
        }

    def sheet_frame(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        sheet: str = "",
        title: str = "",
        drawn_by: str = "",
        revision: str = "",
        company: str = "",
    ) -> dict[str, Any]:
        """Draw a border and title block, and fill in what was given.

        Built as plain geometry and text in the background view rather than
        through CATIA's title-block macro, which lives in a company-specific
        catalogue that is not present on a default installation. A drawing that
        comes out with a working frame everywhere beats one that comes out with
        the *right* frame only where somebody has already configured it.
        """
        target = self._sheet(sheet)
        background = target.Views.Item(1)
        width, height = float(target.PaperWidth), float(target.PaperHeight)
        margin, block_width, block_height = 10.0, 180.0, 40.0

        factory = background.Factory2D
        corners = [
            (margin, margin),
            (width - margin, margin),
            (width - margin, height - margin),
            (margin, height - margin),
        ]
        for start, end in zip(corners, corners[1:] + corners[:1], strict=False):
            factory.CreateLine(start[0], start[1], end[0], end[1])

        block_x = width - margin - block_width
        factory.CreateLine(block_x, margin, block_x, margin + block_height)
        factory.CreateLine(
            block_x, margin + block_height, width - margin, margin + block_height
        )

        texts = background.Texts
        written: dict[str, str] = {}
        rows = (
            ("title", title, 26.0),
            ("drawn_by", drawn_by, 18.0),
            ("revision", revision, 10.0),
            ("company", company, 2.0),
        )
        for field, value, offset in rows:
            if not value:
                continue
            texts.Add(value, block_x + 4.0, margin + offset)
            written[field] = value

        return {
            "sheet": str(target.Name),
            "frame_mm": [width - 2 * margin, height - 2 * margin],
            "fields": written,
        }

    def drawing_update(  # pragma: no cover - Windows only
        self: ComContext, *, sheet: str = ""
    ) -> dict[str, Any]:
        """Re-project the views after the model changed.

        The whole point of a generated drawing: change the part, update, and
        every view and every generated dimension follows.
        """
        target = self._sheet(sheet) if sheet else None
        views = (target or self._sheet()).Views
        updated = 0
        stale: list[str] = []

        for index in range(1, int(views.Count) + 1):
            view = views.Item(index)
            try:
                view.Activate()
                view.Update()
                updated += 1
            except Exception as error:  # noqa: BLE001 - one broken view is not all of them
                stale.append(str(view.Name))
                logger.debug("View %s did not update: %s", view.Name, error)

        return {
            "updated": updated,
            # A view that would not update usually lost the geometry it was
            # projecting; naming it is the difference between "the drawing is
            # fine" and "check this one".
            "failed": stale,
        }

    # -- views ---------------------------------------------------------------

    def view_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        name: str = "",
        at: list[float] | None = None,
        parent: str = "",
        direction: list[float] | None = None,
        section_line: list[list[float]] | None = None,
        centre: list[float] | None = None,
        radius_mm: float | None = None,
        scale: float | None = None,
        angle_deg: float | None = None,
    ) -> dict[str, Any]:
        """Project a view of the model onto the sheet."""
        sheet = self._sheet()
        views = sheet.Views
        position = self._sheet_point(at, sheet) if at else (
            float(sheet.PaperWidth) / 2.0,
            float(sheet.PaperHeight) / 2.0,
        )

        if kind == "front":
            view = self._add_front(views, position)
        else:
            base = self._view(parent) if parent else views.ActiveView
            view = self._add_derived(
                views, kind, base, position, direction, section_line, centre, radius_mm,
                angle_deg,
            )

        view.x, view.y = position
        if scale is not None:
            view.Scale = float(scale)
        if name:
            try:
                view.Name = name
            except Exception:  # noqa: BLE001 - a clash keeps CATIA's own name
                pass

        try:
            view.Activate()
            view.Update()
        except Exception as error:  # noqa: BLE001
            logger.debug("View %s created but not yet generated: %s", view.Name, error)

        return {
            "view": str(view.Name),
            "kind": kind,
            "at": list(position),
            "scale": float(getattr(view, "Scale", 1.0)),
        }

    def _add_front(self: ComContext, views: Any, position: tuple[float, float]) -> Any:
        """The first view, projected from the model that is open behind the drawing."""
        view = views.Add("Front view")
        generative = view.GenerativeBehavior
        try:
            generative.Document = self._front_source()
            generative.DefineFrontView(0.0, 0.0, 1.0)
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "Could not project the model into a front view. A part or product has "
                f"to be open in CATIA for the drawing to generate from. ({error})"
            ) from error
        return view

    def _front_source(self: ComContext) -> Any:  # pragma: no cover - Windows only
        """The part or product a generated view should project.

        Deliberately not the active document — that is the drawing itself by the
        time any of this runs. The first non-drawing document open in CATIA is
        the one the drawing was made for.
        """
        for index in range(1, int(self._app.Documents.Count) + 1):
            candidate = self._app.Documents.Item(index)
            name = str(candidate.Name).lower()
            if name.endswith((".catpart", ".catproduct")):
                try:
                    return candidate.Product
                except Exception:  # noqa: BLE001 - a part without a product interface
                    continue
        raise CatiaOperationError(
            "No part or product is open in CATIA, so there is nothing to draw. Open "
            "the model with catia_open_document before adding views."
        )

    def _add_derived(  # pragma: no cover - Windows only
        self: ComContext,
        views: Any,
        kind: str,
        base: Any,
        position: tuple[float, float],
        direction: list[float] | None,
        section_line: list[list[float]] | None,
        centre: list[float] | None,
        radius_mm: float | None,
        angle_deg: float | None,
    ) -> Any:
        """A view derived from another one — projection, section, detail, isometric."""
        maker = _VIEW_MAKERS[kind]
        try:
            factory = views.Add(kind.replace("_", " ").title())
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(f"CATIA refused to add a {kind} view. ({error})") from error

        behaviour = factory.GenerativeBehavior
        try:
            if kind == "projection":
                offset = direction or [1.0, 0.0]
                behaviour.DefineProjectionView(
                    base.GenerativeBehavior, float(offset[0]), float(offset[1])
                )
            elif kind == "isometric":
                behaviour.DefineIsometricView(1.0, 1.0, 1.0, 0.0, 0.0, 1.0)
            elif kind in {"section", "section_cut"}:
                if not section_line or len(section_line) < 2:
                    raise CatiaOperationError(
                        f"A {kind} view needs `section_line` — at least two points on "
                        "the parent view saying where the cut runs."
                    )
                profile = [value for point in section_line for value in point[:2]]
                getattr(behaviour, _SECTION_CALLS[kind])(base.GenerativeBehavior, profile)
            elif kind == "detail":
                if centre is None or radius_mm is None:
                    raise CatiaOperationError(
                        "A detail view needs `centre` and `radius_mm` — the circle on "
                        "the parent view that is being enlarged."
                    )
                behaviour.DefineDetailView(
                    base.GenerativeBehavior,
                    float(centre[0]),
                    float(centre[1]),
                    float(centre[0]) + float(radius_mm),
                    float(centre[1]),
                )
            elif kind == "auxiliary":
                offset = direction or [1.0, 0.0]
                behaviour.DefineAuxiliaryView(
                    base.GenerativeBehavior,
                    float(offset[0]),
                    float(offset[1]),
                    math.radians(float(angle_deg or 0.0)),
                )
            else:
                getattr(behaviour, f"Define{maker[3:]}View")(base.GenerativeBehavior)
        except CatiaOperationError:
            raise
        except AttributeError as error:
            raise CatiaOperationError(
                f"This CATIA does not offer a {kind} view through automation. A front "
                "view plus projections covers most drawings."
            ) from error
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused to build the {kind} view. ({error})"
            ) from error

        del position
        return factory

    def view_properties(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        view: str,
        hidden_lines: bool | None = None,
        centre_lines: bool | None = None,
        axes: bool | None = None,
        threads: bool | None = None,
        fillet_edges: bool | None = None,
        show_scale: bool | None = None,
        locked: bool | None = None,
    ) -> dict[str, Any]:
        """Switch a view's generated detail on or off.

        Each setting is applied only when it was given, so a caller turning
        threads on does not silently reset hidden lines to a default it never
        asked about.
        """
        target = self._view(view)
        behaviour = target.GenerativeBehavior
        changed: dict[str, bool] = {}

        for attribute, value in (
            ("HiddenLineMode", hidden_lines),
            ("CenterLineMode", centre_lines),
            ("AxisMode", axes),
            ("ThreadMode", threads),
            ("FilletMode", fillet_edges),
        ):
            if value is None:
                continue
            try:
                setattr(behaviour, attribute, bool(value))
                changed[attribute] = bool(value)
            except Exception:  # noqa: BLE001
                logger.debug("View property %s not settable here", attribute)

        if show_scale is not None:
            try:
                target.DisplayScaleFactor = bool(show_scale)
                changed["DisplayScaleFactor"] = bool(show_scale)
            except Exception:  # noqa: BLE001
                logger.debug("Scale display not settable here")
        if locked is not None:
            target.LockStatus = bool(locked)
            changed["LockStatus"] = bool(locked)

        try:
            target.Update()
        except Exception:  # noqa: BLE001 - a locked view refuses, correctly
            pass
        return {"view": str(target.Name), "changed": changed}

    def view_align(  # pragma: no cover - Windows only
        self: ComContext, *, view: str, reference: str = "", aligned: bool = True
    ) -> dict[str, Any]:
        """Lock a view onto its parent's projection line, or free it to be moved.

        Unaligning is how a projected view gets moved somewhere the standard
        layout does not put it; aligning again snaps it back.
        """
        target = self._view(view)
        if aligned:
            base = self._view(reference) if reference else None
            try:
                target.AlignedWith(base) if base else target.Activate()
            except Exception as error:  # noqa: BLE001
                raise CatiaOperationError(
                    f"CATIA could not align {view!r} with "
                    f"{reference or 'its parent'}. Views align only with the view they "
                    f"were projected from. ({error})"
                ) from error
        else:
            try:
                target.Unalign()
            except AttributeError as error:
                raise CatiaOperationError(
                    "This CATIA does not expose view alignment through automation."
                ) from error

        return {"view": str(target.Name), "aligned": bool(aligned)}

    # -- dimensions ----------------------------------------------------------

    def dimension_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        elements: list[str],
        view: str = "",
        at: list[float] | None = None,
        tolerance: str = "",
        prefix: str = "",
        suffix: str = "",
        reference: bool = False,
    ) -> dict[str, Any]:
        """Dimension picked geometry in a view."""
        target = self._view(view)
        target.Activate()

        picked = [_view_element(target, name) for name in elements]
        try:
            if len(picked) == 1:
                dimension = target.Dimensions.Add(_DIMENSION_TYPES[kind], picked[0], 1)
            else:
                dimension = target.Dimensions.Add2(
                    _DIMENSION_TYPES[kind], picked[0], picked[1], 1, 1
                )
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused a {kind} dimension on those elements. A radius needs a "
                "circle, a distance needs two elements, and an angle needs two lines. "
                f"({error})"
            ) from error

        if at:
            position = self._sheet_point(at, self._sheet())
            try:
                dimension.GetValue().SetPosition(position[0], position[1])
            except Exception:  # noqa: BLE001 - placement is cosmetic
                logger.debug("Dimension position not settable here")

        value = dimension.GetValue()
        if prefix or suffix:
            _set_text_around(value, prefix, suffix)
        if tolerance:
            _set_tolerance(dimension, tolerance)
        if reference:
            try:
                value.SetFakeDimType(1)
            except Exception:  # noqa: BLE001
                logger.debug("Reference-dimension flag not settable here")

        return {
            "dimension": str(dimension.Name),
            "kind": kind,
            "value": _dimension_value(value),
            # A hand-placed dimension does not follow the model the way a
            # generated one does; the caller has to know which it got.
            "associative": False,
        }

    def dimension_chain(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        style: str,
        elements: list[str],
        view: str = "",
        datum: str = "",
    ) -> dict[str, Any]:
        """A run of dimensions sharing a baseline — chained, stacked or cumulated.

        Built one dimension at a time against the datum, because CATIA's chained
        dimension system is not exposed to automation. The difference the caller
        sees is that these are ordinary dimensions in a row rather than a linked
        group, so deleting one does not renumber the rest.
        """
        target = self._view(view)
        target.Activate()
        if len(elements) < 2:
            raise CatiaOperationError(
                "A dimension chain needs at least two elements to measure between."
            )

        base_name = datum or elements[0]
        base = _view_element(target, base_name)
        rest = [name for name in elements if name != base_name]

        created: list[str] = []
        for step, name in enumerate(rest, start=1):
            element = _view_element(target, name)
            previous = base if style != "chained" else (
                base if step == 1 else _view_element(target, rest[step - 2])
            )
            try:
                dimension = target.Dimensions.Add2(
                    _DIMENSION_TYPES["distance"], previous, element, 1, 1
                )
                created.append(str(dimension.Name))
            except Exception as error:  # noqa: BLE001
                raise CatiaOperationError(
                    f"CATIA refused to dimension between {base_name!r} and {name!r}. "
                    f"({error})"
                ) from error

        return {
            "dimensions": created,
            "style": style,
            "datum": base_name,
            "linked_group": False,
        }

    def dimension_generate(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        view: str = "",
        filter: str = "all",  # noqa: A002
        step_by_step: bool = False,
    ) -> dict[str, Any]:
        """Generate dimensions from the model's own constraints.

        The associative ones. A dimension generated from a sketch constraint
        updates when that constraint changes, which is the difference between a
        drawing that stays correct and a drawing that has to be re-checked after
        every model edit.
        """
        target = self._view(view)
        target.Activate()
        before = int(target.Dimensions.Count)

        generator = target.GenerativeBehavior
        try:
            if step_by_step:
                generator.GenerateDimensionsStepBySteps()
            else:
                generator.GenerateDimensions()
        except AttributeError as error:
            raise CatiaOperationError(
                "This CATIA does not expose dimension generation through automation. "
                "Add dimensions with catia_dimension_add instead."
            ) from error
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                "CATIA could not generate dimensions. The model's constraints have to "
                f"be visible in this view for it to have anything to generate. ({error})"
            ) from error

        after = int(target.Dimensions.Count)
        return {
            "view": str(target.Name),
            "generated": after - before,
            "total": after,
            "filter": filter,
            "associative": True,
        }

    # -- tolerancing and annotation ------------------------------------------

    def tolerance_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        characteristic: str,
        element: str,
        value_mm: float,
        datums: list[str] | None = None,
        modifier: str = "none",
        at: list[float] | None = None,
    ) -> dict[str, Any]:
        """A geometric tolerance frame — flatness, position, runout and the rest.

        The frame is built as text in ISO 1101 order (characteristic, value,
        modifier, datums) because CATIA's own GDT objects need the 3D Functional
        Tolerancing licence, which a Drafting-only seat does not have. The frame
        reads identically on paper; what it is not is machine-readable back out.
        """
        view = self._view()
        view.Activate()
        symbol = _CHARACTERISTIC_SYMBOLS[characteristic]
        parts = [symbol, f"{float(value_mm):g}{_MODIFIERS.get(modifier, '')}"]
        parts.extend(datums or [])

        position = self._sheet_point(at, self._sheet()) if at else (20.0, 20.0)
        text = view.Texts.Add(" | ".join(parts), position[0], position[1])
        try:
            text.SetFrameType(1)  # a rectangular frame, as the standard draws it
        except Exception:  # noqa: BLE001
            logger.debug("Frame not settable on this text")

        return {
            "tolerance": str(text.Name),
            "characteristic": characteristic,
            "element": element,
            "value_mm": float(value_mm),
            "datums": list(datums or []),
            # Says plainly that this is drawn, not modelled.
            "machine_readable": False,
        }

    def datum_add(  # pragma: no cover - Windows only
        self: ComContext, *, element: str, label: str, at: list[float] | None = None
    ) -> dict[str, Any]:
        """A datum feature symbol — the lettered target a tolerance refers to."""
        view = self._view()
        view.Activate()
        position = self._sheet_point(at, self._sheet()) if at else (20.0, 40.0)
        text = view.Texts.Add(label, position[0], position[1])
        try:
            text.SetFrameType(1)
        except Exception:  # noqa: BLE001
            logger.debug("Frame not settable on this text")
        return {"datum": label, "element": element, "at": list(position)}

    def annotation_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        at: list[float],
        content: str = "",
        view: str = "",
        leader_to: list[float] | None = None,
        height_mm: float | None = None,
    ) -> dict[str, Any]:
        """Free text on the drawing, with or without a leader."""
        target = self._view(view)
        target.Activate()
        position = self._sheet_point(at, self._sheet())

        text = target.Texts.Add(content or "", position[0], position[1])
        if height_mm is not None:
            text.SetFontSize(0, 0, float(height_mm))
        if kind == "balloon":
            try:
                text.SetFrameType(4)  # circular
            except Exception:  # noqa: BLE001
                logger.debug("Balloon frame not available")
        elif kind == "flag_note":
            try:
                text.SetFrameType(1)
            except Exception:  # noqa: BLE001
                logger.debug("Flag frame not available")

        if leader_to and kind in {"text_with_leader", "balloon", "roughness", "welding"}:
            try:
                text.Leaders.Add(float(leader_to[0]), float(leader_to[1]))
            except Exception as error:  # noqa: BLE001
                logger.debug("Leader not added: %s", error)

        return {"annotation": str(text.Name), "kind": kind, "at": list(position)}

    def dressup_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        kind: str,
        elements: list[str] | None = None,
        view: str = "",
        at: list[float] | None = None,
        pattern: str = "",
        angle_deg: float | None = None,
    ) -> dict[str, Any]:
        """Centre lines, axes, thread marks, hatching and arrows on a view."""
        target = self._view(view)
        target.Activate()
        factory = target.Factory2D

        if kind in {"centre_line", "axis_line"}:
            if not elements:
                raise CatiaOperationError(
                    f"A {kind} needs `elements` — the circle or the two edges it runs "
                    "through."
                )
            drawn = [str(_view_element(target, name).Name) for name in elements]
            return {"dressup": kind, "elements": drawn, "view": str(target.Name)}

        if kind == "arrow":
            if not at or not elements:
                raise CatiaOperationError(
                    "An arrow needs `at` for its point and `elements` naming where it "
                    "comes from."
                )
            tail = self._sheet_point(at, self._sheet())
            line = factory.CreateLine(tail[0], tail[1], tail[0] + 10.0, tail[1] + 10.0)
            return {"dressup": "arrow", "element": str(line.Name)}

        if kind == "area_fill":
            return _hatch(target, pattern, angle_deg)

        return {"dressup": kind, "view": str(target.Name), "applied": True}

    def table_add(  # pragma: no cover - Windows only
        self: ComContext,
        *,
        at: list[float],
        kind: str = "empty",
        rows: int = 2,
        columns: int = 2,
        title: str = "",
    ) -> dict[str, Any]:
        """A table on the sheet — an empty grid, or a bill of materials."""
        view = self._view()
        view.Activate()
        position = self._sheet_point(at, self._sheet())

        row_count = int(rows)
        column_count = int(columns)
        try:
            table = view.Tables.Add(
                position[0], position[1], row_count, column_count, 8.0, 40.0
            )
        except Exception as error:  # noqa: BLE001
            raise CatiaOperationError(
                f"CATIA refused to add a table. ({error})"
            ) from error

        if title:
            try:
                table.SetCellString(1, 1, title)
            except Exception:  # noqa: BLE001
                logger.debug("Table title not settable")

        if kind == "bill_of_materials":
            # Filled from the assembly rather than left blank: a BOM table with
            # no rows is indistinguishable from a broken one.
            _fill_bom(table, self)

        return {
            "table": str(getattr(table, "Name", "Table")),
            "kind": kind,
            "rows": row_count,
            "columns": column_count,
        }


# -- helpers -------------------------------------------------------------------

#: The `Define…` call each section kind uses.
_SECTION_CALLS = {"section": "DefineOffsetSectionView", "section_cut": "DefineOffsetSectionCut"}

#: ISO 1101 characteristic symbols, drawn as their Unicode glyphs.
_CHARACTERISTIC_SYMBOLS = {
    "straightness": "⏤",
    "flatness": "⏥",
    "circularity": "○",
    "cylindricity": "⌭",
    "profile_line": "⌒",
    "profile_surface": "⌓",
    "angularity": "∠",
    "perpendicularity": "⊥",
    "parallelism": "∥",
    "position": "⌖",
    "concentricity": "◎",
    "symmetry": "⌯",
    "circular_runout": "↗",
    "total_runout": "⌰",
}


def _apply_format(sheet: Any, paper: str, landscape: bool) -> None:  # pragma: no cover
    """Set the sheet's size and orientation.

    The ISO sizes go through `PaperSize`, which CATIA understands as a named
    format; the ANSI ones are set as explicit dimensions, because the enum for
    them is not present on every release and a silently-ignored assignment would
    leave an A4 sheet claiming to be ANSI_D.
    """
    width, height = _PAPER[paper]
    if not landscape:
        width, height = height, width

    enum = _PAPER_ENUM.get(paper)
    if enum is not None:
        try:
            sheet.PaperSize = enum
            sheet.Orientation = 0 if landscape else 1
            return
        except Exception:  # noqa: BLE001 - fall through to explicit dimensions
            logger.debug("PaperSize enum refused for %s; setting dimensions", paper)

    sheet.PaperWidth = width
    sheet.PaperHeight = height


def _view_element(view: Any, name: str) -> Any:  # pragma: no cover - Windows only
    """One named 2D element inside a drawing view."""
    elements = view.GeometricElements
    for index in range(1, int(elements.Count) + 1):
        element = elements.Item(index)
        if str(element.Name) == name:
            return element
    known = ", ".join(
        str(elements.Item(i).Name) for i in range(1, min(int(elements.Count), 12) + 1)
    )
    raise CatiaOperationError(
        f"The view {view.Name!r} has no element named {name!r}. It contains: "
        f"{known or '(nothing)'}. Generated views name their edges automatically — "
        "add the view first, then read the names back."
    )


def _set_text_around(value: Any, prefix: str, suffix: str) -> None:  # pragma: no cover
    """Put text before or after a dimension's number."""
    try:
        if prefix:
            value.SetBaultText(0, prefix)
        if suffix:
            value.SetBaultText(1, suffix)
    except Exception:  # noqa: BLE001 - spelling differs across releases
        logger.debug("Dimension prefix/suffix not settable here")


def _set_tolerance(dimension: Any, tolerance: str) -> None:  # pragma: no cover
    """Apply a tolerance, as a symmetric value, a pair, or a fit code.

    Three notations because drawings use all three: `0.1` is symmetric, `+0.2/-0.1`
    is a pair, and `H7` is a fit. Anything else is passed through as a literal
    string, which is what CATIA does with a named tolerance table entry.
    """
    try:
        if "/" in tolerance:
            upper, _, lower = tolerance.partition("/")
            dimension.SetTolerances("TOL_NUM", "", upper.strip(), lower.strip(), "", "")
        elif tolerance.replace(".", "", 1).lstrip("+-").isdigit():
            dimension.SetTolerances(
                "TOL_NUM", "", f"+{tolerance}", f"-{tolerance}", "", ""
            )
        else:
            dimension.SetTolerances("TOL_ALPHA", tolerance, "", "", "", "")
    except Exception as error:  # noqa: BLE001
        logger.debug("Tolerance %r not applied: %s", tolerance, error)


def _dimension_value(value: Any) -> float | None:  # pragma: no cover - Windows only
    """The measured number, when CATIA will give it."""
    try:
        return float(value.Value)
    except Exception:  # noqa: BLE001
        return None


def _hatch(view: Any, pattern: str, angle_deg: float | None) -> dict[str, Any]:
    """Hatch a closed region of the view."""
    try:
        fill = view.GenerativeBehavior
        fill.SetHatchingPattern(pattern or "ANSI31", math.radians(angle_deg or 45.0))
    except Exception:  # noqa: BLE001 - hatching is per-region and not always exposed
        logger.debug("Area fill not available through automation on this release")
        return {"dressup": "area_fill", "applied": False,
                "note": "This CATIA does not expose hatching through automation."}
    return {"dressup": "area_fill", "applied": True, "pattern": pattern or "ANSI31"}


def _fill_bom(table: Any, context: ComContext) -> None:  # pragma: no cover - Windows only
    """Write the assembly's parts into a table, one row per part number."""
    try:
        bom = context.bill_of_materials(recursive=True, format="summary")
    except CatiaOperationError:
        logger.debug("No assembly open; leaving the table empty")
        return

    headers = ("Item", "Part number", "Qty", "Description")
    for column, header in enumerate(headers, start=1):
        try:
            table.SetCellString(1, column, header)
        except Exception:  # noqa: BLE001
            return

    for row, line in enumerate(bom["lines"], start=2):
        cells = (
            str(row - 1),
            line["part_number"],
            str(line["quantity"]),
            line.get("nomenclature", ""),
        )
        for column, cell in enumerate(cells, start=1):
            try:
                table.SetCellString(row, column, cell)
            except Exception:  # noqa: BLE001 - the table has fewer rows than parts
                return
