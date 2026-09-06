"""The sheet a drawing is drawn on: size, frame, title block, scale, projection.

Four decisions live here, and three of them are the ones that get a part made wrong.

**The projection convention is a parameter and is printed on every sheet.** First
angle (ISO, the European convention, and the one the CATIA seat this product is
tested against runs in) puts the view from above *below* the front view and the
view from the right *to its left*; third angle (ANSI) puts each on the side it is
seen from. The two layouts are mirror images of each other, and a third-angle
reader handed a first-angle drawing manufactures the part reversed — with nothing
in the geometry to say so, because both drawings are internally consistent. So
`Projection` has no silent default at the point it matters: `TitleBlock` carries
it, `dxf.py` draws the ISO 128 truncated-cone symbol for it, and the words appear
in the title block as well as the symbol. A drawing that does not declare its
convention is not a drawing.

**The scale is chosen from the ISO 5455 preferred series, never computed.** A
drawing at 1:2.73 is a drawing nobody can measure off, and a scale bar cannot be
read to three figures. `choose_scale` takes the largest preferred scale that still
fits and refuses — with the sheet sizes that would work — when even the smallest
does not.

**Millimetres, stated.** The whole codebase is mm-N-MPa and nothing converts
(CLAUDE.md), so the sheet is in millimetres and the DXF says so in `$INSUNITS`.
The title block prints "DIMENSIONS IN MILLIMETRES" because a drawing that does not
say is a drawing that gets read in inches once.

**The frame follows ISO 5457**: a wide filing margin on the left edge, narrower
margins elsewhere, and the title block hard against the bottom-right corner of the
frame, which is where a reader's eye goes and where every CAD system puts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.manufacture.errors import DrawingError


class Projection(StrEnum):
    """Which orthographic projection convention a sheet is drawn in.

    The values are the words printed in the title block. There is deliberately no
    `AUTO`: the convention is a property of the reader's shop, not of the part,
    and nothing in the geometry can be asked.
    """

    FIRST_ANGLE = "first angle"
    THIRD_ANGLE = "third angle"


#: The convention used when a caller does not say. ISO/European, because the
#: product ships against a French CATIA seat and ISO 128 is what it draws in.
#: Defaulting is safe *only* because the choice is stamped on every sheet — the
#: symbol and the words both — so a third-angle shop reading one is told.
DEFAULT_PROJECTION: Final = Projection.FIRST_ANGLE


@dataclass(frozen=True)
class SheetSize:
    """One ISO 5457 sheet, landscape, with its frame margins.

    `left_margin_mm` is wider than the rest: it is the filing margin, the strip a
    binder punches through, and drawing into it loses the drawing.
    """

    name: str
    width_mm: float
    height_mm: float
    left_margin_mm: float
    margin_mm: float

    @property
    def frame(self) -> tuple[float, float, float, float]:
        """The drawing frame as `(x0, y0, x1, y1)` in sheet millimetres."""
        return (
            self.left_margin_mm,
            self.margin_mm,
            self.width_mm - self.margin_mm,
            self.height_mm - self.margin_mm,
        )


#: ISO 5457 A-series, landscape. Ordered smallest first, which is the order
#: `smallest_sheet_for` walks: a drawing belongs on the smallest sheet it reads
#: well on, because an A0 sheet carrying an A4's worth of part is a sheet nobody
#: has a printer for.
SHEET_SIZES: Final[tuple[SheetSize, ...]] = (
    SheetSize("A4", 297.0, 210.0, 20.0, 10.0),
    SheetSize("A3", 420.0, 297.0, 20.0, 10.0),
    SheetSize("A2", 594.0, 420.0, 20.0, 20.0),
    SheetSize("A1", 841.0, 594.0, 20.0, 20.0),
    SheetSize("A0", 1189.0, 841.0, 20.0, 20.0),
)

_BY_NAME: Final[dict[str, SheetSize]] = {sheet.name: sheet for sheet in SHEET_SIZES}


def sheet_named(name: str) -> SheetSize:
    """One sheet size by name, refusing an unknown one with the list."""
    try:
        return _BY_NAME[name.strip().upper()]
    except KeyError:
        known = ", ".join(sheet.name for sheet in SHEET_SIZES)
        raise DrawingError(
            f"{name!r} is not a sheet size this build draws on. The sizes are: {known}."
        ) from None


#: Title block, ISO 7200 proportions: 180 mm wide is the standard width and fits
#: inside an A4 frame (297 − 20 − 10 = 267 mm) with room to spare.
TITLE_BLOCK_WIDTH_MM: Final = 180.0
TITLE_BLOCK_HEIGHT_MM: Final = 56.0

#: The ISO 5455 preferred scales, as multiplying factors, largest first.
#: Enlargements, full size, then reductions. A scale outside this list is not a
#: scale an engineer can read a drawing at.
PREFERRED_SCALES: Final[tuple[float, ...]] = (
    50.0,
    20.0,
    10.0,
    5.0,
    2.0,
    1.0,
    0.5,
    0.2,
    0.1,
    0.05,
    0.02,
    0.01,
    0.005,
    0.002,
    0.001,
    0.0005,
    0.0002,
    0.0001,
)


def scale_text(factor: float) -> str:
    """A scale factor as it is written on a drawing: `1:1`, `2:1`, `1:5`.

    Written from the factor rather than carried alongside it so the number on the
    sheet cannot drift from the number the geometry was drawn at — the failure
    that makes every dimension on a drawing a lie at once.
    """
    if factor <= 0.0:
        raise DrawingError(
            f"A scale of {factor:g} is not a scale. Pick one from the preferred "
            "series with choose_scale."
        )
    if factor >= 1.0:
        ratio = factor
        return f"{_whole(ratio)}:1"
    return f"1:{_whole(1.0 / factor)}"


def _whole(value: float) -> str:
    """A preferred-series ratio as an integer where it is one, and honestly otherwise."""
    nearest = round(value)
    if abs(value - nearest) < 1e-9:
        return str(nearest)
    return f"{value:g}"


def choose_scale(
    content_mm: tuple[float, float], area_mm: tuple[float, float]
) -> float:
    """The largest preferred scale at which `content_mm` fits inside `area_mm`.

    Both are `(width, height)` in millimetres — the content in model millimetres,
    the area in sheet millimetres. Returns a factor from `PREFERRED_SCALES`, so
    the answer is always a scale that can be written on a drawing and read off it.

    A content extent of zero in a direction never binds — a flat plate seen
    edge-on has no height and must still be drawn — which is the same rule
    `render.views.frame_for` applies for the same reason.

    Refuses rather than returning the smallest scale anyway: a part that needs
    1:20000 on the sheet it was given is a part on the wrong sheet, and silently
    drawing it at 1:10000 produces a sheet with the part running off the frame
    and every dimension still claiming to be right.
    """
    width, height = content_mm
    area_width, area_height = area_mm
    if area_width <= 0.0 or area_height <= 0.0:
        raise DrawingError(
            f"The drawing area is {area_width:g} x {area_height:g} mm, which has no "
            "room in it. Use a larger sheet, or a smaller title block."
        )

    limits = [
        area_width / width if width > 0.0 else float("inf"),
        area_height / height if height > 0.0 else float("inf"),
    ]
    fits = min(limits)
    if fits == float("inf"):
        # Nothing to draw has no scale it fails at; 1:1 is the honest answer.
        return 1.0

    for candidate in PREFERRED_SCALES:
        if candidate <= fits:
            return candidate

    raise DrawingError(
        f"A part {width:g} x {height:g} mm does not fit in a {area_width:g} x "
        f"{area_height:g} mm drawing area at any preferred scale — it would need "
        f"about {scale_text(_previous_preferred(fits))} or finer. Draw it on a "
        "larger sheet, or split it across several."
    )


def _previous_preferred(fits: float) -> float:
    """The preferred scale just below what would fit, for the refusal message."""
    return min(PREFERRED_SCALES[-1], fits) if fits > 0.0 else PREFERRED_SCALES[-1]


def smallest_sheet_for(
    content_mm: tuple[float, float], *, minimum_scale: float = 0.1
) -> SheetSize:
    """The smallest sheet on which `content_mm` fits at `minimum_scale` or better.

    `minimum_scale` is the smallest reduction a drawing is allowed to be chosen
    *automatically* at — 1:10 by default, because past that a fillet radius stops
    being visible and the drawing stops answering the question it was made for.
    A caller who genuinely wants 1:50 says so by naming a sheet.
    """
    for sheet in SHEET_SIZES:
        try:
            if choose_scale(content_mm, drawing_area(sheet)) >= minimum_scale:
                return sheet
        except DrawingError:
            continue
    return SHEET_SIZES[-1]


def drawing_area(sheet: SheetSize) -> tuple[float, float]:
    """How much of `sheet` is free for views, once frame and title block are taken.

    The title block sits in the bottom-right corner, so the free region is not a
    rectangle. This returns the largest rectangle that is definitely free — full
    frame width, frame height less the title block — which is the conservative
    answer and the one a layout can trust. Views placed above the title block's
    left edge get the extra room back for free by simply being there.
    """
    x0, y0, x1, y1 = sheet.frame
    return (x1 - x0, (y1 - y0) - TITLE_BLOCK_HEIGHT_MM)


@dataclass(frozen=True)
class TitleBlock:
    """What the title block says. Every field is printed; none is inferred.

    `mass_kg` is `None` when the part has not been weighed, and prints as
    "NOT MEASURED" rather than as a blank or a zero — the same rule
    `app.kernel.provenance` applies to every other number in this codebase. A
    blank mass field reads as "light enough not to matter"; a zero reads as a
    fact.
    """

    title: str
    drawing_number: str
    projection: Projection = DEFAULT_PROJECTION
    scale: float = 1.0
    sheet_of: str = "1 / 1"
    material: str | None = None
    mass_kg: float | None = None
    revision: str = "-"
    drawn_by: str = ""
    approved_by: str = ""
    date: str = ""
    owner: str = ""
    general_tolerance: str = ""

    #: Printed under the title block. This is where the dimension-completeness
    #: statement lands, and it is why the field exists: a drawing that could not
    #: be fully dimensioned must say so *on the drawing*, not only in a return
    #: value nobody prints.
    notes: tuple[str, ...] = ()

    def mass_text(self) -> str:
        if self.mass_kg is None:
            return "NOT MEASURED"
        return f"{self.mass_kg:.3f} kg"

    def material_text(self) -> str:
        return self.material if self.material else "NOT STATED"

    def tolerance_text(self) -> str:
        """The general tolerance note, or an explicit refusal to imply one.

        Never falls back to a plausible default such as ISO 2768-m. A general
        tolerance is a commercial commitment — it decides what the shop is
        allowed to ship — and inventing one on a drawing Kryova produced would be
        Kryova signing for it.
        """
        return self.general_tolerance if self.general_tolerance else "NONE STATED"


__all__ = [
    "DEFAULT_PROJECTION",
    "PREFERRED_SCALES",
    "SHEET_SIZES",
    "TITLE_BLOCK_HEIGHT_MM",
    "TITLE_BLOCK_WIDTH_MM",
    "Projection",
    "SheetSize",
    "TitleBlock",
    "choose_scale",
    "drawing_area",
    "scale_text",
    "sheet_named",
    "smallest_sheet_for",
]
