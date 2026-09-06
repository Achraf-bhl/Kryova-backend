"""The drawing as a DXF file — the artefact that actually leaves the building.

`drawing.py` builds the document; this writes it. Nothing above this module knows
what a file is, which is what keeps a second output format a new writer rather
than a rewrite.

**Millimetres are declared, not assumed.** `$INSUNITS = 4` and `$MEASUREMENT = 1`
go in the header of every file. A DXF carrying no unit code opens in whatever the
receiving system defaults to, and the default on an American seat is inches — the
same numbers, read 25.4 times too small, on a drawing where every dimension still
says the right thing. That is not a theoretical failure mode; it is the reason
`$INSUNITS` exists. `tests/test_manufacture_export.py` pins it.

**Every entity is on a named layer, and the layers are declared here.** A DXF that
puts everything on layer `0` is a DXF a machinist cannot turn the hidden lines off
in, and one whose visible and hidden edges are indistinguishable once the line
types are lost — which is the distinction `DrawnView` keeps apart the whole way
down for exactly this reason. `LAYERS` is the declaration; `_check_layer` refuses
to write an entity to a layer that is not in it, so the table and the file cannot
drift apart.

**A dimension's text is the design's number, never the length of the line it is
drawn beside.** Every DIMENSION entity is written with an explicit text override.
At 1:2 the line under a 120 mm plate is 60 mm long on the paper, and a dimension
that measured its own geometry would print 60. The override is what makes a
scaled drawing legal, and it is the file-level half of the property `drawing.py`
describes.

**The bytes are reproducible, and they are not by default.** `app/render/` goes to
real lengths for byte-identical output — no anti-aliasing, `floor(v+0.5)`, a
hand-written PNG encoder rather than one that might add a timestamp chunk — and a
drawing has the same claim on it: two runs of the same design must produce the
same file or a reviewer cannot diff them. ezdxf stamps four things that are a
function of the clock and the random number generator rather than of the drawing:
`$FINGERPRINTGUID`, `$VERSIONGUID`, the julian dates, and its own
`CREATED_BY_EZDXF` / `WRITTEN_BY_EZDXF` markers. Two of the four are regenerated
*inside* `Document.write`, so they cannot be set beforehand. `_scrub` replaces
them in the written tag stream, which is the one place that can see all four.
It rewrites values only — never a group code, and never a length — so the file
stays a valid DXF.

**What this writer does not do, said plainly rather than papered over:** no GD&T
frames, no surface finish symbols, no datums, no BOM table, no revision block
history. None of it exists upstream in the design IR, so none of it is invented
here; the sheet says so in words, from `DimensionReport.statement()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.manufacture.drawing import (
    Dimension,
    DimensionKind,
    Drawing,
    DrawnView,
    Polyline,
    ViewKind,
)
from app.manufacture.errors import ExportError
from app.manufacture.sheet import (
    TITLE_BLOCK_HEIGHT_MM,
    TITLE_BLOCK_WIDTH_MM,
    Projection,
    scale_text,
)

#: The DXF version written. R2010 (`AC1024`) is the oldest release that carries
#: true colour, modern hatch definitions and unicode text without a codepage
#: dance, and every CAD system in current use reads it. R12 would be more
#: universal and has no LWPOLYLINE, no unicode and no real dimension styles.
DXF_VERSION: Final = "R2010"

#: `$INSUNITS` code 4 is millimetres. The whole codebase is mm-N-MPa and nothing
#: converts (CLAUDE.md); this states that fact in the file rather than hoping the
#: reader shares it.
INSUNITS_MILLIMETRES: Final = 4

#: `$MEASUREMENT` 1 is metric, which is what decides the linetype and hatch
#: pattern *file* a CAD system loads. Setting the units and leaving this at 0
#: gives millimetres drawn with imperial dash lengths.
MEASUREMENT_METRIC: Final = 1

#: Text heights, ISO 3098. 3.5 mm is the smallest size that survives a
#: photocopy, and it is what dimensions and notes are drawn at.
TEXT_MM: Final = 3.5
LABEL_MM: Final = 5.0
TITLE_MM: Final = 7.0


@dataclass(frozen=True)
class LayerSpec:
    """One layer this writer may put an entity on.

    `lineweight` is in hundredths of a millimetre, which is DXF's own unit for
    it. ISO 128 asks for a 2:1 ratio between the wide and narrow line groups —
    outline wide, everything else narrow — and a drawing printed with one weight
    throughout is one where the part's silhouette stops standing out from its
    dimensions. It is a property of the layer rather than of each entity so a
    reviewer can change it once.

    `purpose` is documentation only. DXF has no plain layer-description field
    below the AEC extension, so this is not written to the file; it is here
    because a layer table nobody can read the intent of is a layer table people
    invent a tenth layer beside.
    """

    name: str
    colour: int
    linetype: str
    lineweight: int
    purpose: str


#: Visible edges. Everything a straight-on look at the part would show.
OUTLINE: Final = "OUTLINE"
#: Edges behind other material. Dashed, and on their own layer so a busy view
#: can have them turned off without losing the outline with them.
HIDDEN: Final = "HIDDEN"
#: Centre marks and axes, dash-dot per ISO 128-23.
CENTRE: Final = "CENTRE"
#: Cutting-plane lines drawn on the view a section was taken from.
SECTION: Final = "SECTION"
#: Section hatching. A separate layer because hatch is the first thing anyone
#: turns off when they want to read the geometry under it.
HATCH: Final = "HATCH"
#: Dimensions, their extension lines and their text.
DIMENSION: Final = "DIMENSION"
#: View labels, section and detail captions.
TEXT: Final = "TEXT"
#: The sheet frame, the title block and the projection symbol.
FRAME: Final = "FRAME"
#: The notes column: the completeness statement, the table of what could not be
#: placed, and the design's own rationale.
NOTES: Final = "NOTES"

#: Every layer this writer may use, with the colour and line type each is drawn
#: in. Declared as data so a test can assert that the file's layer table is
#: exactly this and that nothing landed on layer `0` by accident — an entity on
#: `0` inherits whatever the block it lands in is drawn as, which is how a hidden
#: edge ends up looking solid.
LAYERS: Final[tuple[LayerSpec, ...]] = (
    LayerSpec(OUTLINE, 7, "CONTINUOUS", 50, "Visible edges"),
    LayerSpec(HIDDEN, 8, "DASHED", 25, "Edges hidden behind material"),
    LayerSpec(CENTRE, 4, "CENTER", 25, "Centre marks and axes"),
    LayerSpec(SECTION, 1, "DASHDOT", 50, "Cutting-plane lines"),
    LayerSpec(HATCH, 5, "CONTINUOUS", 25, "Section hatching"),
    LayerSpec(DIMENSION, 3, "CONTINUOUS", 25, "Dimensions and their text"),
    LayerSpec(TEXT, 7, "CONTINUOUS", 25, "View labels and captions"),
    LayerSpec(FRAME, 7, "CONTINUOUS", 50, "Sheet frame, title block, projection symbol"),
    LayerSpec(NOTES, 2, "CONTINUOUS", 25, "Completeness statement and notes"),
)

_LAYER_NAMES: Final[frozenset[str]] = frozenset(one.name for one in LAYERS)

#: The dimension style ezdxf's `setup=True` installs, sized for millimetres.
#: Named rather than built here because a style this file invented would have to
#: be maintained against every ezdxf release; the standard one is maintained
#: upstream and is the one every other ezdxf-written DXF uses.
DIMSTYLE: Final = "EZDXF"

#: The hatch pattern for a section. ANSI31 is the 45° thin-line pattern every
#: system has and every machinist reads as "cut material".
HATCH_PATTERN: Final = "ANSI31"

# -- reproducibility --------------------------------------------------------

#: The all-zero GUID. AutoCAD writes this when it has no drawing identity to
#: record, so it is a legal value rather than a marker we invented.
_FIXED_GUID: Final = "{00000000-0000-0000-0000-000000000000}"

#: 2000-01-01T00:00 as a julian date, which is the constant ezdxf's own
#: fixed-metadata mode uses. A drawing's creation time is not a property of the
#: drawing, and a file that changes every time it is written cannot be reviewed.
_FIXED_JULIAN: Final = "2451544.5"

#: What replaces the ezdxf version-and-timestamp marker strings.
_FIXED_MARKER: Final = "kryova"

_GUID_VARS: Final[frozenset[str]] = frozenset({"$FINGERPRINTGUID", "$VERSIONGUID"})
_DATE_VARS: Final[frozenset[str]] = frozenset(
    {"$TDCREATE", "$TDUCREATE", "$TDUPDATE", "$TDUUPDATE"}
)

#: ezdxf's marker string is `"<version> @ <iso timestamp>"`. Matched narrowly —
#: on the `@` and a full ISO date — so a note or a title that happens to start
#: with a version number is not rewritten out of the drawing.
_MARKER_RE: Final = re.compile(r"^\d+\.\d+\.\d+ @ \d{4}-\d{2}-\d{2}T")


def _ezdxf() -> Any:
    """The ezdxf module, or a refusal that says how to get it.

    Imported here rather than at module scope so `app.manufacture.drawing` and
    everything above it keeps importing on a machine that has not installed it —
    the same contract `app/kernel/occt/binding.py` holds for OCCT and
    `app/catia/bridge.py` for pywin32.
    """
    try:
        import ezdxf
    except ImportError as error:  # pragma: no cover - depends on the machine
        raise ExportError(
            "Writing a DXF needs the ezdxf package, which is not installed here. "
            "Install it with `pip install ezdxf` (it is in requirements.txt), or "
            "ask for the drawing as data with Drawing.to_dict()."
        ) from error
    return ezdxf


def _check_layer(name: str) -> str:
    """Refuse a layer this module has not declared.

    Cheap, and it is the guard that keeps `LAYERS` a declaration rather than a
    comment: a new entity written to a layer nobody created lands in the file
    with default properties and looks *almost* right.
    """
    if name not in _LAYER_NAMES:
        known = ", ".join(sorted(_LAYER_NAMES))
        raise ExportError(
            f"{name!r} is not a layer this writer declares, so nothing would create "
            f"it and the entity would carry default properties. The layers are: {known}."
        )
    return name


def to_document(drawing: Drawing) -> Any:
    """The drawing as an in-memory ezdxf document.

    Separate from `write` so a caller can inspect what was produced without
    touching a disk, which is what the export tests do — and so a second
    destination (a stream, a blob store) is a call rather than a temporary file.
    """
    ezdxf = _ezdxf()
    doc = ezdxf.new(DXF_VERSION, setup=True)
    doc.header["$INSUNITS"] = INSUNITS_MILLIMETRES
    doc.header["$MEASUREMENT"] = MEASUREMENT_METRIC
    # Without this the layer line weights are stored and never drawn, so the
    # ISO 128 wide/narrow distinction is in the file and invisible on the print.
    doc.header["$LWDISPLAY"] = 1
    # `$LIMMIN`/`$LIMMAX` and `$EXTMIN`/`$EXTMAX` are deliberately *not* set.
    # ezdxf recomputes all four from the active layout inside `Document.write`,
    # so writing the sheet size into them puts a promise in the file that is
    # overwritten before it reaches disk — worse than not making it. The sheet
    # size travels as geometry instead: the outer rectangle on the FRAME layer
    # is the sheet, at 1:1 in millimetres, which is what a reader sees and what
    # a plotter measures.

    for spec in LAYERS:
        doc.layers.add(
            spec.name,
            color=spec.colour,
            linetype=spec.linetype,
            lineweight=spec.lineweight,
        )

    space = doc.modelspace()
    _frame(space, drawing)
    _title_block(space, drawing)
    _notes(space, drawing)
    for view in drawing.views:
        _view(space, view)
    for plane in drawing.cutting_planes:
        _cutting_plane(space, drawing, plane)
    for dimension in drawing.dimensions:
        _dimension(space, drawing, dimension)
    return doc


def to_text(drawing: Drawing) -> str:
    """The DXF as text, with every clock- and random-seeded stamp removed.

    This is the reproducible artefact: two runs over the same `Drawing` return
    equal strings. `write` is this plus a file.
    """
    import io

    doc = to_document(drawing)
    stream = io.StringIO()
    doc.write(stream)
    return _scrub(stream.getvalue())


def write(drawing: Drawing, path: str | Path) -> Path:
    """Write the drawing to `path`, returning where it landed.

    UTF-8 and `\\n`, explicitly, on a product that develops on Linux and ships on
    Windows: a file whose bytes depend on the platform's default newline is a
    file two machines cannot compare, and half the point of a reproducible
    drawing is that they can.
    """
    target = Path(path)
    if target.suffix.lower() != ".dxf":
        raise ExportError(
            f"A DXF must be written to a .dxf path; got {target.name!r}. A CAD system "
            "picks its reader from the extension and will refuse this file."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(to_text(drawing), encoding="utf-8", newline="\n")
    return target


def _scrub(text: str) -> str:
    """Replace every value in the tag stream that is a function of the clock.

    A DXF is strictly two lines per tag — a group code then its value — from the
    first tag to `EOF`, so the stream can be walked in pairs with no parser. A
    header variable is the tag `9 <$NAME>` followed by the tag holding its value,
    which is why a match at `i` rewrites `i + 3`.

    Only values are rewritten, never a group code and never the number of lines,
    so the result is the same DXF with four stamps neutralised rather than a file
    that has to be re-validated.
    """
    lines = text.split("\n")
    out = list(lines)
    for index in range(0, len(lines) - 1, 2):
        value = lines[index + 1]
        if value in _GUID_VARS and index + 3 < len(out):
            out[index + 3] = _FIXED_GUID
        elif value in _DATE_VARS and index + 3 < len(out):
            out[index + 3] = _FIXED_JULIAN
        elif _MARKER_RE.match(value):
            out[index + 1] = _FIXED_MARKER
    return "\n".join(out)


# -- sheet furniture --------------------------------------------------------


def _frame(space: Any, drawing: Drawing) -> None:
    """The sheet edge and the ISO 5457 drawing frame."""
    sheet = drawing.sheet
    _polyline(
        space,
        ((0.0, 0.0), (sheet.width_mm, 0.0), (sheet.width_mm, sheet.height_mm), (0.0, sheet.height_mm)),
        FRAME,
        close=True,
    )
    x0, y0, x1, y1 = sheet.frame
    _polyline(space, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), FRAME, close=True)


def _title_block(space: Any, drawing: Drawing) -> None:
    """ISO 7200 title block, hard against the bottom-right of the frame.

    Every field is written, including the ones that say a thing is not known:
    `TitleBlock.mass_text` prints NOT MEASURED and `tolerance_text` prints NONE
    STATED, and printing those is the point — a blank mass field reads as "light
    enough not to matter" and a blank tolerance field reads as "the usual one".
    """
    block = drawing.title_block
    _, _, x1, y0 = (0.0, 0.0, drawing.sheet.frame[2], drawing.sheet.frame[1])
    left = x1 - TITLE_BLOCK_WIDTH_MM
    top = y0 + TITLE_BLOCK_HEIGHT_MM
    _polyline(
        space,
        ((left, y0), (x1, y0), (x1, top), (left, top)),
        FRAME,
        close=True,
    )
    _polyline(space, ((left, y0 + 14.0), (x1, y0 + 14.0)), FRAME)
    _polyline(space, ((left, y0 + 28.0), (x1, y0 + 28.0)), FRAME)

    _text(space, block.title, (left + 3.0, top - 9.0), TITLE_MM, TEXT)
    _text(space, block.drawing_number, (left + 3.0, y0 + 30.0), LABEL_MM, TEXT)
    _text(space, f"REV {block.revision}", (left + 96.0, y0 + 30.0), TEXT_MM, TEXT)
    _text(space, f"SHEET {block.sheet_of}", (left + 130.0, y0 + 30.0), TEXT_MM, TEXT)

    _text(space, f"SCALE {scale_text(block.scale)}", (left + 3.0, y0 + 17.0), TEXT_MM, TEXT)
    _text(space, f"MATERIAL {block.material_text()}", (left + 44.0, y0 + 17.0), TEXT_MM, TEXT)
    _text(space, f"MASS {block.mass_text()}", (left + 120.0, y0 + 17.0), TEXT_MM, TEXT)

    _text(space, "DIMENSIONS IN MILLIMETRES", (left + 3.0, y0 + 8.0), TEXT_MM, TEXT)
    _text(
        space,
        f"GENERAL TOLERANCE {block.tolerance_text()}",
        (left + 3.0, y0 + 3.0),
        TEXT_MM,
        TEXT,
    )
    _text(
        space,
        str(block.projection).upper(),
        (left + 120.0, y0 + 8.0),
        TEXT_MM,
        TEXT,
    )
    _projection_symbol(space, (left + 120.0, y0 + 3.5), block.projection)


def _projection_symbol(
    space: Any, at: tuple[float, float], projection: Projection
) -> None:
    """The ISO 128 truncated cone, drawn the way round the convention says.

    Two circles and the cone joining them, seen end-on and from the side. In
    first angle the large circle is on the *left* of the pair; in third angle it
    is on the right — which is the same mirror `layout._place` applies to the
    views, drawn small enough to fit in a title block. The words are printed
    beside it as well, because a symbol nobody was taught to read is decoration.
    """
    big, small = 2.6, 1.5
    span = 9.0
    first = projection is Projection.FIRST_ANGLE
    large_x = at[0] - span / 2.0 if first else at[0] + span / 2.0
    small_x = at[0] + span / 2.0 if first else at[0] - span / 2.0
    _circle(space, (large_x, at[1]), big, FRAME)
    _circle(space, (small_x, at[1]), small, FRAME)
    _polyline(space, ((large_x, at[1] + big), (small_x, at[1] + small)), FRAME)
    _polyline(space, ((large_x, at[1] - big), (small_x, at[1] - small)), FRAME)


def _notes(space: Any, drawing: Drawing) -> None:
    """The honesty block, the table of what could not be placed, and the notes.

    Printed on the sheet and not only returned in `DimensionReport`. A drawing
    that could not be fully dimensioned has to say so *where the drawing is
    read*; a return value nobody prints is not a warning, and the binding rule
    for this phase is that the drawing itself carries it.
    """
    x0, _, x1, y1 = drawing.sheet.frame
    left = x1 - 58.0
    y = y1 - TEXT_MM
    for line in drawing.title_block.notes:
        for wrapped in _wrap(line, 44):
            _text(space, wrapped, (left, y), TEXT_MM, NOTES)
            y -= TEXT_MM * 1.6
    if drawing.report.tabled:
        y -= TEXT_MM
        _text(space, "NOT PLACED ON A VIEW:", (left, y), TEXT_MM, NOTES)
        y -= TEXT_MM * 1.6
        for unplaced in drawing.report.tabled:
            row = (
                f"{unplaced.dimension.feature}.{unplaced.dimension.argument} "
                f"= {unplaced.dimension.label} - {unplaced.reason}"
            )
            for wrapped in _wrap(row, 44):
                _text(space, wrapped, (left, y), TEXT_MM, NOTES)
                y -= TEXT_MM * 1.6
    if drawing.report.non_dimensional:
        y -= TEXT_MM
        _text(space, "NUMBERS THAT ARE NOT DIMENSIONS:", (left, y), TEXT_MM, NOTES)
        y -= TEXT_MM * 1.6
        for one in drawing.report.non_dimensional:
            row = f"{one.feature}.{one.argument} = {one.value:g}"
            for wrapped in _wrap(row, 44):
                _text(space, wrapped, (left, y), TEXT_MM, NOTES)
                y -= TEXT_MM * 1.6
    if drawing.notes:
        y -= TEXT_MM
        _text(space, "NOTES:", (left, y), TEXT_MM, NOTES)
        y -= TEXT_MM * 1.6
        for index, note in enumerate(drawing.notes, start=1):
            for wrapped in _wrap(f"{index}. {note}", 44):
                _text(space, wrapped, (left, y), TEXT_MM, NOTES)
                y -= TEXT_MM * 1.6
    del x0


def _wrap(text: str, width: int) -> tuple[str, ...]:
    """Break a line to `width` characters on word boundaries, deterministically.

    A note that runs off the sheet is a note nobody reads, and DXF TEXT does not
    wrap. `textwrap` is not used because it drops trailing whitespace and
    collapses runs, and a dimension table row is easier to read as it was built.
    """
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return tuple(lines) or ("",)


# -- views ------------------------------------------------------------------


def _view(space: Any, view: DrawnView) -> None:
    """One view's line work, its hatch, its centre marks and its label."""
    for line in view.visible:
        _polyline(space, _to_sheet(view, line), OUTLINE)
    for line in view.hidden:
        _polyline(space, _to_sheet(view, line), HIDDEN)
    for line in view.centre_lines:
        _polyline(space, _to_sheet(view, line), CENTRE)
    for boundary in view.hatch:
        _hatch(space, _to_sheet(view, boundary))

    low_x, low_y, high_x, high_y = view.extent
    caption = view.label
    if view.kind is ViewKind.ORTHOGRAPHIC and view.scale != 0.0:
        caption = view.label
    below = view.to_sheet_mm(((low_x + high_x) / 2.0, low_y))
    _text(space, caption, (below[0], below[1] - LABEL_MM * 2.0), LABEL_MM, TEXT)
    del high_y


def _to_sheet(view: DrawnView, line: Polyline) -> tuple[tuple[float, float], ...]:
    """A polyline from view millimetres to where it lands on the paper.

    The single join between the two coordinate systems, and it is `DrawnView`'s
    own — this module never applies a scale of its own, which is what stops the
    file disagreeing with the `Drawing` about where anything is.
    """
    return tuple(view.to_sheet_mm(point) for point in line)


def _cutting_plane(space: Any, drawing: Drawing, plane: Any) -> None:
    """The cutting-plane line and its label, on the view the cut was taken from."""
    try:
        view = drawing.view_named(plane.view)
    except Exception:  # noqa: BLE001 - a plane on a view that was not drawn
        return
    start = view.to_sheet_mm(plane.start)
    end = view.to_sheet_mm(plane.end)
    _polyline(space, (start, end), SECTION)
    _text(space, plane.label, (start[0] - LABEL_MM, start[1]), LABEL_MM, SECTION)
    _text(space, plane.label, (end[0] + LABEL_MM / 2.0, end[1]), LABEL_MM, SECTION)


# -- dimensions -------------------------------------------------------------


def _dimension(space: Any, drawing: Drawing, dimension: Dimension) -> None:
    """One dimension as a real DXF DIMENSION entity, with its text overridden.

    The override is the whole point: `dimension.value` is the design's number and
    the geometry underneath it is at sheet scale, so a measuring dimension would
    print the paper length. `text` also carries the ISO 129-1 count prefix
    (`4X Ø7`) and the Ø or R symbol, which an associative dimension could not
    know to add.
    """
    try:
        view = drawing.view_named(dimension.view)
    except Exception:  # noqa: BLE001 - a dimension on a view that was not drawn
        return
    attributes = {"layer": _check_layer(DIMENSION)}

    if dimension.kind in (DimensionKind.DIAMETER, DimensionKind.RADIUS):
        if dimension.centre is None or dimension.radius_mm is None:
            return
        centre = view.to_sheet_mm(dimension.centre)
        radius = dimension.radius_mm * view.scale
        angle = dimension.leader_deg if dimension.leader_deg is not None else 45.0
        maker = (
            space.add_diameter_dim
            if dimension.kind is DimensionKind.DIAMETER
            else space.add_radius_dim
        )
        entity = maker(
            center=centre,
            radius=radius,
            angle=angle,
            text=dimension.text,
            dimstyle=DIMSTYLE,
            dxfattribs=attributes,
        )
        entity.render()
        return

    if dimension.start is None or dimension.end is None:
        return
    start = view.to_sheet_mm(dimension.start)
    end = view.to_sheet_mm(dimension.end)
    vertical = dimension.kind is DimensionKind.LINEAR_VERTICAL
    angle = 90.0 if vertical else 0.0
    offset = dimension.offset_mm
    if vertical:
        base = (min(start[0], end[0]) + offset, (start[1] + end[1]) / 2.0)
    else:
        base = ((start[0] + end[0]) / 2.0, min(start[1], end[1]) + offset)
    entity = space.add_linear_dim(
        base=base,
        p1=start,
        p2=end,
        angle=angle,
        text=dimension.text,
        dimstyle=DIMSTYLE,
        dxfattribs=attributes,
    )
    entity.render()


# -- primitives -------------------------------------------------------------


def _polyline(
    space: Any,
    points: tuple[tuple[float, float], ...],
    layer: str,
    *,
    close: bool = False,
) -> None:
    if len(points) < 2:
        return
    space.add_lwpolyline(
        points, close=close, dxfattribs={"layer": _check_layer(layer)}
    )


def _circle(space: Any, centre: tuple[float, float], radius: float, layer: str) -> None:
    space.add_circle(centre, radius, dxfattribs={"layer": _check_layer(layer)})


def _text(
    space: Any, content: str, at: tuple[float, float], height: float, layer: str
) -> None:
    if not content:
        return
    space.add_text(
        content,
        height=height,
        dxfattribs={"layer": _check_layer(layer)},
    ).set_placement(at)


def _hatch(space: Any, boundary: tuple[tuple[float, float], ...]) -> None:
    """One closed boundary, filled with the section pattern.

    The boundary goes in as a polyline path rather than as pre-computed hatch
    lines, so the receiving CAD system draws the pattern its own way and a
    reviewer can change it. `drawing.py` keeps hatch as boundaries for exactly
    this reason.
    """
    if len(boundary) < 3:
        return
    hatch = space.add_hatch(dxfattribs={"layer": _check_layer(HATCH)})
    hatch.set_pattern_fill(HATCH_PATTERN, scale=1.0)
    hatch.paths.add_polyline_path(boundary, is_closed=True)


__all__ = [
    "CENTRE",
    "DIMENSION",
    "DXF_VERSION",
    "FRAME",
    "HATCH",
    "HIDDEN",
    "INSUNITS_MILLIMETRES",
    "LAYERS",
    "MEASUREMENT_METRIC",
    "NOTES",
    "OUTLINE",
    "SECTION",
    "TEXT",
    "LayerSpec",
    "to_document",
    "to_text",
    "write",
]
