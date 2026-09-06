"""Arranging views on a sheet, and putting the dimensions where they belong.

**The projection convention decides the arrangement, and getting it wrong makes
the part backwards.** In third angle (ANSI) each view is placed on the side it is
seen from: the view from above goes above the front view, the view from the right
goes to its right. In first angle (ISO) each goes on the opposite side: above
becomes below, right becomes left. The two sheets are mirror images and both are
internally consistent, so nothing in the line work can betray a reader given the
wrong one — which is why `sheet.py` stamps the convention on every drawing and why
this module takes it as an argument rather than picking one.

**Views are placed on a grid because orthographic projection is an alignment
promise.** The top view sits directly above or below the front view and shares its
horizontal extent; the right view sits directly beside it and shares its vertical
one. That is not a tidiness convention — it is what lets an engineer carry a
dimension from one view to another with a straightedge. Views live in grid cells
and cells are centred in their row and column, so the promise holds by
construction rather than by arithmetic that could drift.

**The scale is chosen once, for all the orthographic views together.** Framing
each view to its own extent is the obvious implementation and it makes a
multi-view sheet unreadable — the same trap `render.render_views` documents. A
detail view is the deliberate exception and says its own scale in its label.

**Overall dimensions come from the bounding box, not from the projected outline.**
HLR flattens a curved edge into segments, so a projected extent can fall a
fraction of a micron inside the true one, and an overall dimension read off it
would print 119.9999 for a 120 mm plate. The anchors are taken from the
projection — they only need to be in the right place — and the *value* from
`bounding_box_mm`, which is exact. This is the same discipline `drawing.py`
describes: a dimension's text is never derived from the length of the line it is
drawn beside.

**Each length is dimensioned once.** Width appears on the front view, height on
the front view, depth on the top view; the right view repeats two of the three and
carries none of them. A drawing that dimensions the same length twice is a drawing
with two numbers that can disagree after an edit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Final

from app.manufacture.drawing import (
    DIMENSION_OFFSET_MM,
    CuttingPlane,
    Dimension,
    DimensionKind,
    DimensionReport,
    DimensionSource,
    Drawing,
    DrawnView,
    Polyline,
    TracedDimension,
    Unplaced,
    ViewKind,
)
from app.manufacture.errors import DrawingError
from app.manufacture.locate import MATCH_TOLERANCE_MM, CircleGroup, find_circles
from app.manufacture.sheet import (
    DEFAULT_PROJECTION,
    TITLE_BLOCK_HEIGHT_MM,
    Projection,
    SheetSize,
    TitleBlock,
    choose_scale,
    drawing_area,
    scale_text,
    sheet_named,
    smallest_sheet_for,
)

#: Space between two views on the sheet, in sheet millimetres. Wide enough that
#: a dimension line placed between two views is unambiguous about which one it
#: belongs to, which is the failure a tight gap causes.
VIEW_GAP_MM: Final = 30.0

#: Room reserved outside the block of views for dimension lines and their text,
#: per side, in sheet millimetres. Two rows of dimensions plus the text height.
DIMENSION_ALLOWANCE_MM: Final = 22.0

#: Width of the notes column down the right-hand side of the drawing area, above
#: the title block. This is where the dimension-completeness statement, the table
#: of unplaced dimensions and the design's own rationale notes are printed — the
#: block that a drawing which could not be fully dimensioned must carry.
NOTES_WIDTH_MM: Final = 62.0

#: The order views are searched for a round feature to hang a radius or diameter
#: dimension on. Top first: a plate's holes are drilled through its thickness, so
#: the top view is where they read as circles, and searching it first makes the
#: usual case deterministic without a tie-break.
_CIRCLE_SEARCH_ORDER: Final[tuple[str, ...]] = ("top", "front", "right")

#: Leader angles for successive round-feature dimensions in one view, degrees.
#: Cycled by index so two leaders in the same view never lie on top of each other,
#: and fixed rather than computed so the same part always produces the same sheet.
_LEADER_ANGLES: Final[tuple[float, ...]] = (45.0, 135.0, 225.0, 315.0)

#: Where each canonical orthographic view sits on the projection grid, as
#: `(row, col)`, stated in the **third angle** sense: a higher row is higher on
#: the sheet and a higher column is further right. `_place` mirrors both axes for
#: first angle, and that mirror is the entire difference between the conventions.
#:
#: The front view is the middle cell rather than a corner because `left` and
#: `bottom` exist and have to go somewhere. This table used to give `left` the
#: same cell as `right` and `back` the same cell as the pictorial view, which
#: drew two views at one origin — and put the view *from the left* on the
#: right-hand side of a third-angle sheet, which is precisely the mirrored-part
#: failure this module's docstring warns about, with nothing in the line work to
#: betray it.
_VIEW_CELLS: Final[dict[str, tuple[int, int]]] = {
    "bottom": (0, 1),
    "left": (1, 0),
    "front": (1, 1),
    "right": (1, 2),
    "back": (1, 3),
    "top": (2, 1),
}

#: The pictorial view's cell: diagonally off the front view, so it never shares a
#: row or a column with an orthographic view and cannot be read as one.
_ISO_CELL: Final[tuple[int, int]] = (2, 2)

#: The column sections and details start in — under the front view rather than
#: under whatever happens to be leftmost, so the sheet reads down the middle.
_EXTRA_COL: Final = 1


@dataclass(frozen=True)
class DetailRequest:
    """A magnified crop of one view, at its own scale.

    `centre_mm` and `radius_mm` are in the parent view's own millimetres, which
    is the only frame in which "the top-left corner of the flange" can be said
    without knowing the sheet scale.
    """

    parent: str
    centre_mm: tuple[float, float]
    radius_mm: float
    magnification: float = 2.0
    label: str = ""


@dataclass(frozen=True)
class LayoutRequest:
    """Everything about a sheet that is a decision rather than a measurement."""

    title: str
    drawing_number: str
    sheet: str | None = None
    projection: Projection = DEFAULT_PROJECTION
    views: tuple[str, ...] = ("front", "top", "right")
    include_iso: bool = True

    #: Canonical section names from `app.render.section` — `mid-x`, `mid-y`,
    #: `mid-z`. Each becomes one hatched view and one cutting-plane line on the
    #: orthographic view that shows the plane edge-on.
    sections: tuple[str, ...] = ()

    details: tuple[DetailRequest, ...] = ()
    material: str | None = None
    mass_kg: float | None = None
    revision: str = "-"
    drawn_by: str = ""
    approved_by: str = ""
    date: str = ""
    owner: str = ""
    general_tolerance: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Cell:
    """One view, before it knows where on the sheet it is or how big."""

    row: int
    col: int
    name: str
    label: str
    kind: ViewKind
    visible: tuple[Polyline, ...]
    hidden: tuple[Polyline, ...]
    extent: tuple[float, float, float, float]
    hatch: tuple[Polyline, ...] = ()

    #: This cell's scale relative to the sheet's. 1.0 for everything except a
    #: detail view, which is the one view allowed to be drawn larger and which
    #: says so in its own label.
    magnify: float = 1.0

    #: The camera, kept so dimension placement can ask which world axis the
    #: view's own x and y point along.
    camera: Any = None

    @property
    def span(self) -> tuple[float, float]:
        low_x, low_y, high_x, high_y = self.extent
        return ((high_x - low_x) * self.magnify, (high_y - low_y) * self.magnify)


def lay_out(
    shape: Any,
    request: LayoutRequest,
    *,
    traced: tuple[TracedDimension, ...] = (),
    suppressed: tuple[str, ...] = (),
    non_dimensional: tuple[TracedDimension, ...] = (),
) -> Drawing:
    """Build the whole sheet: views, scale, dimensions and the honesty block.

    `traced` is what the design states, from `dimensions.trace_dimensions`. Pass
    none and the sheet still draws, with every dimension measured off the solid
    and the report saying so — which is the right answer for a part that was
    built call by call and has no parameters.
    """
    cells = _build_cells(shape, request)
    if not cells:
        raise DrawingError(
            "There is nothing to draw: every requested view came back empty. Check "
            "that the shape holds a solid — a failed last operation leaves one that "
            "projects to nothing."
        )

    sheet = _pick_sheet(request, cells)
    area_width, area_height = drawing_area(sheet)
    columns, rows = _grid(cells)
    content = (sum(columns), sum(rows))
    usable = (
        area_width - NOTES_WIDTH_MM - 2.0 * DIMENSION_ALLOWANCE_MM - VIEW_GAP_MM * (len(columns) - 1),
        area_height - 2.0 * DIMENSION_ALLOWANCE_MM - VIEW_GAP_MM * (len(rows) - 1),
    )
    scale = choose_scale(content, usable)

    placed = _place(cells, columns, rows, scale, sheet, request.projection)
    report, dimensions, views = _dimension(
        shape, cells, placed, traced, suppressed, non_dimensional
    )
    planes = _cutting_planes(shape, request, cells)

    notes = tuple(request.notes) + _rationale_notes(traced)
    return Drawing(
        title=request.title,
        sheet=sheet,
        title_block=TitleBlock(
            title=request.title,
            drawing_number=request.drawing_number,
            projection=request.projection,
            scale=scale,
            material=request.material,
            mass_kg=request.mass_kg,
            revision=request.revision,
            drawn_by=request.drawn_by,
            approved_by=request.approved_by,
            date=request.date,
            owner=request.owner,
            general_tolerance=request.general_tolerance,
            notes=report.statement(),
        ),
        views=views,
        dimensions=dimensions,
        cutting_planes=planes,
        report=report,
        notes=notes,
    )


# -- views ------------------------------------------------------------------


def _build_cells(shape: Any, request: LayoutRequest) -> tuple[_Cell, ...]:
    """Project everything the request asks for, and assign it a grid cell.

    Cells are addressed by `_VIEW_CELLS`, in the third-angle sense: front in the
    middle, top above it, bottom below, right to its right, left to its left.
    Sections and details fill the rows *below* the block, two to a row, so they
    never take a cell an orthographic view could want. Which *direction* rows and
    columns grow is the projection convention's business, applied in `_place` —
    the grid itself is the same either way, which is what keeps the convention a
    single sign rather than two layout algorithms that can disagree.

    The grid is compacted at the end, so a sheet that asks for no left view does
    not carry an empty column where one would have gone.
    """
    from app.render.project import project
    from app.render.views import view_named

    cells: list[_Cell] = []

    for name in request.views:
        key = name.strip().lower()
        if key not in _VIEW_CELLS:
            known = ", ".join(sorted(_VIEW_CELLS))
            raise DrawingError(
                f"{name!r} is not an orthographic view and cannot go on the projection "
                f"grid. The orthographic views are: {known}. A pictorial view is asked "
                "for with include_iso."
            )
        camera = view_named(key)
        projection = project(shape, camera)
        if projection.is_empty:
            continue
        row, col = _VIEW_CELLS[key]
        cells.append(
            _Cell(
                row=row,
                col=col,
                name=key,
                label=key.upper(),
                kind=ViewKind.ORTHOGRAPHIC,
                visible=projection.visible,
                hidden=projection.hidden,
                extent=projection.extent,
                camera=camera,
            )
        )

    if request.include_iso:
        camera = view_named("iso")
        projection = project(shape, camera)
        if not projection.is_empty:
            cells.append(
                _Cell(
                    row=_ISO_CELL[0],
                    col=_ISO_CELL[1],
                    name="iso",
                    label="ISOMETRIC",
                    kind=ViewKind.PICTORIAL,
                    visible=projection.visible,
                    # A pictorial view is drawn without hidden detail. Every edge
                    # behind another one, dashed, on an isometric of anything with
                    # a bore turns the picture into a thicket and it is not what
                    # the view is for — it is there to say what the part looks
                    # like, and the orthographic views carry the internal detail.
                    hidden=(),
                    extent=projection.extent,
                    camera=camera,
                )
            )

    cells.extend(_section_cells(shape, request, start_row=-1))
    cells.extend(_detail_cells(request, cells))
    return _compact(cells)


def _compact(cells: list[_Cell]) -> tuple[_Cell, ...]:
    """Renumber rows and columns to consecutive indices, order preserved.

    `_VIEW_CELLS` addresses a fixed six-cell grid and sections sit on negative
    rows below it, so most sheets use a handful of scattered indices. `_place`
    walks the index space, and an index nothing occupies still costs a
    `VIEW_GAP_MM` — a band of blank paper where the left view would have been.
    Compaction removes those without moving anything relative to anything else,
    so the alignment promise between the front view and its neighbours survives
    it: a row is still a row and the order is still the order.
    """
    rows = {row: index for index, row in enumerate(sorted({cell.row for cell in cells}))}
    columns = {col: index for index, col in enumerate(sorted({cell.col for cell in cells}))}
    return tuple(
        replace(cell, row=rows[cell.row], col=columns[cell.col]) for cell in cells
    )


def _section_cells(shape: Any, request: LayoutRequest, *, start_row: int) -> list[_Cell]:
    """One hatched view per requested section, labelled A, B, C…

    Uses `app.render.section` as it stands: the cut, the natural view, the faces
    that lie on the plane and their ordered outlines. Nothing about the cutting
    convention is restated here — the normal points at the material that is
    removed, and this module never has to know that, because it asks
    `Section.natural_view()` which side to look from.
    """
    from app.render.project import project
    from app.render.section import cut, face_outlines, section_faces, section_named

    cells: list[_Cell] = []
    for index, name in enumerate(request.sections):
        letter = _section_letter(index)
        section = section_named(shape, name)
        sectioned = cut(shape, section)
        camera = section.natural_view()
        projection = project(sectioned, camera)
        if projection.is_empty:
            continue
        outlines = tuple(
            tuple(one)
            for face in section_faces(sectioned, section)
            for one in face_outlines(face, camera)
            if len(one) >= 3
        )
        cells.append(
            _Cell(
                row=start_row - index // 2,
                col=_EXTRA_COL + index % 2,
                name=f"section_{letter.lower()}",
                label=f"SECTION {letter}-{letter}",
                kind=ViewKind.SECTION,
                visible=projection.visible,
                hidden=(),
                extent=projection.extent,
                hatch=outlines,
                camera=camera,
            )
        )
    return cells


def _detail_cells(request: LayoutRequest, existing: list[_Cell]) -> list[_Cell]:
    """One magnified crop per requested detail, clipped to its own circle.

    The crop is a real clip, not a re-render: the parent view's polylines are cut
    where they cross the detail circle, so the detail shows exactly the line work
    the parent shows and cannot disagree with it about the geometry.
    """
    by_name = {cell.name: cell for cell in existing}
    row = min((cell.row for cell in existing), default=0) - 1
    cells: list[_Cell] = []
    for index, detail in enumerate(request.details):
        parent = by_name.get(detail.parent)
        if parent is None:
            known = ", ".join(sorted(by_name)) or "none"
            raise DrawingError(
                f"A detail view was asked for on {detail.parent!r}, which is not a view "
                f"on this sheet. The views are: {known}."
            )
        if detail.radius_mm <= 0.0:
            raise DrawingError(
                f"A detail view needs a radius greater than zero; got {detail.radius_mm:g} mm."
            )
        visible = _clip_to_circle(parent.visible, detail.centre_mm, detail.radius_mm)
        hidden = _clip_to_circle(parent.hidden, detail.centre_mm, detail.radius_mm)
        if not visible and not hidden:
            raise DrawingError(
                f"A detail view centred at {detail.centre_mm} with radius "
                f"{detail.radius_mm:g} mm on {detail.parent} contains no geometry. Move "
                "the centre, or widen the radius."
            )
        letter = detail.label or _detail_letter(index)
        cells.append(
            _Cell(
                row=row - index // 2,
                col=_EXTRA_COL + index % 2,
                name=f"detail_{letter.lower()}",
                label=f"DETAIL {letter} ({scale_text(detail.magnification)})",
                kind=ViewKind.DETAIL,
                visible=visible,
                hidden=hidden,
                extent=(
                    detail.centre_mm[0] - detail.radius_mm,
                    detail.centre_mm[1] - detail.radius_mm,
                    detail.centre_mm[0] + detail.radius_mm,
                    detail.centre_mm[1] + detail.radius_mm,
                ),
                magnify=detail.magnification,
                camera=parent.camera,
            )
        )
    return cells


def _clip_to_circle(
    lines: tuple[Polyline, ...], centre: tuple[float, float], radius: float
) -> tuple[Polyline, ...]:
    """Every part of `lines` inside a circle, as separate polylines.

    Segment by segment, splitting a polyline wherever it leaves and re-enters, so
    a line crossing the detail circle twice comes back as two runs rather than
    one with a chord through the middle of it.
    """
    kept: list[Polyline] = []
    for line in lines:
        run: list[tuple[float, float]] = []
        for start, end in zip(line, line[1:], strict=False):
            inside_start = _inside(start, centre, radius)
            inside_end = _inside(end, centre, radius)
            if inside_start and inside_end:
                if not run:
                    run.append(start)
                run.append(end)
                continue
            crossing = _circle_crossing(start, end, centre, radius)
            if inside_start and crossing is not None:
                if not run:
                    run.append(start)
                run.append(crossing[-1])
            elif inside_end and crossing is not None:
                run = [crossing[0], end]
                continue
            if run:
                kept.append(tuple(run))
                run = []
        if run:
            kept.append(tuple(run))
    return tuple(one for one in kept if len(one) >= 2)


def _inside(point: tuple[float, float], centre: tuple[float, float], radius: float) -> bool:
    return (point[0] - centre[0]) ** 2 + (point[1] - centre[1]) ** 2 <= radius * radius


def _circle_crossing(
    start: tuple[float, float],
    end: tuple[float, float],
    centre: tuple[float, float],
    radius: float,
) -> tuple[tuple[float, float], ...] | None:
    """Where a segment crosses a circle, in order along the segment.

    Solves the quadratic rather than stepping along the segment: a stepped
    intersection lands on a different point for a different step count, and a
    drawing whose detail view moves when the deflection changes is a drawing
    nobody can diff.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    fx, fy = start[0] - centre[0], start[1] - centre[1]
    a = dx * dx + dy * dy
    if a <= 0.0:
        return None
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - radius * radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return None
    root = discriminant**0.5
    hits = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if 0.0 <= t <= 1.0:
            hits.append((start[0] + t * dx, start[1] + t * dy))
    return tuple(hits) if hits else None


def _section_letter(index: int) -> str:
    return chr(ord("A") + index % 26)


def _detail_letter(index: int) -> str:
    """Detail letters start after the section letters would run out of obvious ones."""
    return chr(ord("P") + index % 11)


# -- placement --------------------------------------------------------------


def _pick_sheet(request: LayoutRequest, cells: tuple[_Cell, ...]) -> SheetSize:
    if request.sheet is not None:
        return sheet_named(request.sheet)
    columns, rows = _grid(cells)
    return smallest_sheet_for(
        (
            sum(columns) + NOTES_WIDTH_MM + 2.0 * DIMENSION_ALLOWANCE_MM,
            sum(rows) + 2.0 * DIMENSION_ALLOWANCE_MM,
        )
    )


def _grid(cells: tuple[_Cell, ...]) -> tuple[list[float], list[float]]:
    """Column widths and row heights, in model millimetres.

    A column is as wide as its widest cell and a row as tall as its tallest, so a
    view is centred in its cell and the alignment promise between the front view
    and its neighbours holds without any view having to know about any other.
    """
    columns = max((cell.col for cell in cells), default=0) + 1
    rows = max((cell.row for cell in cells), default=0) + 1
    widths = [0.0] * columns
    heights = [0.0] * rows
    for cell in cells:
        span_x, span_y = cell.span
        widths[cell.col] = max(widths[cell.col], span_x)
        heights[cell.row] = max(heights[cell.row], span_y)
    return widths, heights


def _place(
    cells: tuple[_Cell, ...],
    columns: list[float],
    rows: list[float],
    scale: float,
    sheet: SheetSize,
    projection: Projection,
) -> tuple[DrawnView, ...]:
    """Turn cells into placed views, applying the projection convention's signs.

    First angle reverses both the row order and the column order relative to
    third angle, and that is the entire difference between the two sheets. Two
    signs, in one place, rather than a layout routine per convention — which is
    what makes it possible to draw the same part both ways and diff them.
    """
    frame_x0, frame_y0, frame_x1, frame_y1 = sheet.frame
    free_x0 = frame_x0 + DIMENSION_ALLOWANCE_MM
    free_x1 = frame_x1 - NOTES_WIDTH_MM - DIMENSION_ALLOWANCE_MM
    free_y0 = frame_y0 + TITLE_BLOCK_HEIGHT_MM + DIMENSION_ALLOWANCE_MM
    free_y1 = frame_y1 - DIMENSION_ALLOWANCE_MM

    column_mm = [width * scale for width in columns]
    row_mm = [height * scale for height in rows]
    total_width = sum(column_mm) + VIEW_GAP_MM * (len(column_mm) - 1)
    total_height = sum(row_mm) + VIEW_GAP_MM * (len(row_mm) - 1)

    block_x = free_x0 + ((free_x1 - free_x0) - total_width) / 2.0
    block_y = free_y0 + ((free_y1 - free_y0) - total_height) / 2.0

    first_angle = projection is Projection.FIRST_ANGLE
    placed: list[DrawnView] = []
    for cell in cells:
        before_x = sum(column_mm[: cell.col]) + VIEW_GAP_MM * cell.col
        before_y = sum(row_mm[: cell.row]) + VIEW_GAP_MM * cell.row
        left = (
            block_x + total_width - before_x - column_mm[cell.col]
            if first_angle
            else block_x + before_x
        )
        bottom = (
            block_y + total_height - before_y - row_mm[cell.row]
            if first_angle
            else block_y + before_y
        )
        placed.append(
            DrawnView(
                name=cell.name,
                label=cell.label,
                kind=cell.kind,
                origin_mm=(left + column_mm[cell.col] / 2.0, bottom + row_mm[cell.row] / 2.0),
                scale=scale * cell.magnify,
                visible=cell.visible,
                hidden=cell.hidden,
                extent=cell.extent,
                hatch=cell.hatch,
            )
        )
    return tuple(placed)


# -- dimensions -------------------------------------------------------------


@dataclass
class _Placement:
    """Working state while dimensions are being assigned. Mutable on purpose."""

    placed: list[Dimension] = field(default_factory=list)
    tabled: list[Unplaced] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    centre_lines: dict[str, list[Polyline]] = field(default_factory=dict)

    def table(self, dimension: TracedDimension, reason: str) -> None:
        """Record a stated dimension that did not reach a view — once.

        Once, because the same dimension is offered to every extent that could
        carry it: a 12 mm thickness is tried against the front view's height and
        the top view's depth, and on a cube every stated length is offered to
        three extents at once. Appending per offer made the sheet report "9
        stated by the design but not placed" over a design stating three — a
        drawing whose honesty block was itself wrong about how much was missing,
        which is the one thing this block exists not to be.

        The first reason wins. It is the one from the view that came closest to
        carrying the dimension, and a second sentence saying the same thing about
        another view adds nothing a reader can act on.
        """
        for existing in self.tabled:
            if existing.dimension == dimension:
                return
        self.tabled.append(Unplaced(dimension, reason))

    def note_ambiguity(self, message: str) -> None:
        """Record a collision, once. Two views can report the identical clash."""
        if message not in self.ambiguous:
            self.ambiguous.append(message)


def _dimension(
    shape: Any,
    cells: tuple[_Cell, ...],
    views: tuple[DrawnView, ...],
    traced: tuple[TracedDimension, ...],
    suppressed: tuple[str, ...],
    non_dimensional: tuple[TracedDimension, ...],
) -> tuple[DimensionReport, tuple[Dimension, ...], tuple[DrawnView, ...]]:
    """Place what can be placed exactly, table the rest with a reason.

    Two passes over the round features, and the order is load-bearing. The first
    pass works out which traced dimension wants which group of circles; the
    second places only the groups exactly one dimension wants. Placing greedily
    in one pass would give the first-listed dimension the leader and leave the
    second looking merely unplaced, hiding the fact that the drawing could not
    tell two design parameters apart.
    """
    state = _Placement()
    by_name = {cell.name: cell for cell in cells}
    lengths = [one for one in traced if one.kind is DimensionKind.LINEAR and one.unit]
    rounds = [
        one
        for one in traced
        if one.kind in (DimensionKind.DIAMETER, DimensionKind.RADIUS) and one.unit
    ]
    angles = [one for one in traced if one.kind is DimensionKind.ANGULAR]

    consumed = _place_overall(shape, by_name, lengths, state)
    _place_rounds(shape, by_name, rounds, state)

    for length in lengths:
        if length not in consumed:
            state.table(
                length,
                "no overall extent of any view is this length, so there is no pair "
                "of edges on the sheet to dimension between",
            )
    for angle in angles:
        state.table(
            angle,
            "this build places no angular dimensions; the value is stated here "
            "instead of being drawn",
        )

    # The views are rebuilt rather than mutated: `DrawnView` is frozen, and
    # placing a hole dimension is also what discovers where that hole's centre
    # mark goes. Returning the marked views is a wider contract than returning
    # the report alone, and it is the honest one — the alternative is writing
    # through `object.__setattr__` into a value the caller believes is immutable.
    marked = tuple(
        DrawnView(
            name=view.name,
            label=view.label,
            kind=view.kind,
            origin_mm=view.origin_mm,
            scale=view.scale,
            visible=view.visible,
            hidden=view.hidden,
            extent=view.extent,
            hatch=view.hatch,
            centre_lines=tuple(state.centre_lines.get(view.name, ())),
        )
        for view in views
    )

    report = DimensionReport(
        placed=tuple(state.placed),
        tabled=tuple(state.tabled),
        non_dimensional=tuple(non_dimensional),
        suppressed=tuple(suppressed),
        ambiguous=tuple(state.ambiguous),
    )
    return report, tuple(state.placed), marked


def _place_overall(
    shape: Any,
    cells: dict[str, _Cell],
    lengths: list[TracedDimension],
    state: _Placement,
) -> set[TracedDimension]:
    """Overall width, height and depth, attributed to a parameter where one fits.

    Three dimensions on two views, never the same length twice: the front view
    carries the two extents it shows, and the top view contributes only the third
    — its own vertical extent, which is the one the front view cannot show.

    Attribution is by value, and it is refused when two parameters share a value:
    `corner_mm = 5` and `boss_r_mm = 5` are indistinguishable to anything except
    the geometry they were used on, and claiming one of them for a dimension
    would be a guess printed as a fact. The dimension is still placed, because a
    drawing without overall dimensions is useless — it is placed as measured, and
    the collision is reported.
    """
    from app.kernel.occt.metrology import bounding_box_mm

    box = bounding_box_mm(shape)
    consumed: set[TracedDimension] = set()

    front = cells.get("front")
    top = cells.get("top")
    if front is not None:
        consumed |= _overall_pair(front, box, lengths, state, horizontal=True, vertical=True)
    if top is not None:
        consumed |= _overall_pair(
            top, box, lengths, state, horizontal=front is None, vertical=True, skip_repeats=front
        )
    return consumed


def _overall_pair(
    cell: _Cell,
    box: dict[str, list[float]],
    lengths: list[TracedDimension],
    state: _Placement,
    *,
    horizontal: bool,
    vertical: bool,
    skip_repeats: _Cell | None = None,
) -> set[TracedDimension]:
    """The overall dimensions of one view, along whichever of its axes are asked for."""
    low_x, low_y, high_x, high_y = cell.extent
    consumed: set[TracedDimension] = set()
    already = _world_axes(skip_repeats) if skip_repeats is not None else set()

    if horizontal:
        axis = _axis_index(cell.camera.right())
        if axis not in already:
            value = box["size"][axis]
            consumed |= _add_linear(
                cell,
                DimensionKind.LINEAR_HORIZONTAL,
                value,
                (low_x, low_y),
                (high_x, low_y),
                -DIMENSION_OFFSET_MM,
                lengths,
                state,
            )
    if vertical:
        axis = _axis_index(cell.camera.frame_up())
        if axis not in already:
            value = box["size"][axis]
            consumed |= _add_linear(
                cell,
                DimensionKind.LINEAR_VERTICAL,
                value,
                (low_x, low_y),
                (low_x, high_y),
                -DIMENSION_OFFSET_MM,
                lengths,
                state,
            )
    return consumed


def _world_axes(cell: _Cell) -> set[int]:
    """Which world axes a view already dimensioned, so the next one does not repeat."""
    return {_axis_index(cell.camera.right()), _axis_index(cell.camera.frame_up())}


def _axis_index(vector: tuple[float, float, float]) -> int:
    """Which world axis a unit vector points along.

    Every canonical orthographic view's own axes are world axes, so this is exact
    for the views that carry dimensions. An oblique view would land on whichever
    component is biggest, which is why the pictorial view is never dimensioned.
    """
    return max(range(3), key=lambda index: abs(vector[index]))


def _add_linear(
    cell: _Cell,
    kind: DimensionKind,
    value: float,
    start: tuple[float, float],
    end: tuple[float, float],
    offset_mm: float,
    lengths: list[TracedDimension],
    state: _Placement,
) -> set[TracedDimension]:
    matches = [one for one in lengths if abs(one.value - value) <= MATCH_TOLERANCE_MM]
    if len(matches) == 1:
        traced = matches[0]
        state.placed.append(
            Dimension(
                view=cell.name,
                kind=kind,
                source=DimensionSource.PARAMETER,
                value=traced.value,
                start=start,
                end=end,
                offset_mm=offset_mm,
                parameter=", ".join(traced.parameters) or None,
                feature=traced.feature,
                note=traced.note,
            )
        )
        return {traced}
    if len(matches) > 1:
        state.note_ambiguity(
            f"{value:g} mm on the {cell.name} view matches "
            + ", ".join(f"{one.feature}.{one.argument}" for one in matches)
        )
        for one in matches:
            state.table(
                one,
                "more than one stated dimension has this value, so attributing the "
                "overall size to one of them would be a guess",
            )
    state.placed.append(
        Dimension(
            view=cell.name,
            kind=kind,
            source=DimensionSource.GEOMETRY,
            value=value,
            start=start,
            end=end,
            offset_mm=offset_mm,
        )
    )
    return set(matches)


def _place_rounds(
    shape: Any,
    cells: dict[str, _Cell],
    rounds: list[TracedDimension],
    state: _Placement,
) -> None:
    """Diameter and radius dimensions, on the first view that shows the circle."""
    circles: dict[str, tuple[CircleGroup, ...]] = {}
    for name in _CIRCLE_SEARCH_ORDER:
        cell = cells.get(name)
        if cell is not None:
            circles[name] = find_circles(shape, cell.camera)

    wanted: dict[tuple[str, int], list[TracedDimension]] = {}
    found: dict[tuple[str, int], CircleGroup] = {}
    misses: dict[TracedDimension, str] = {}
    for traced in rounds:
        target = traced.value / 2.0 if traced.kind is DimensionKind.DIAMETER else traced.value
        for name, groups in circles.items():
            match = next(
                (one for one in groups if abs(one.radius_mm - target) <= MATCH_TOLERANCE_MM),
                None,
            )
            if match is not None:
                key = (name, int(round(target * 1e6)))
                wanted.setdefault(key, []).append(traced)
                found[key] = match
                break
        else:
            misses[traced] = (
                f"no round feature of R{target:g} reads as a circle in any dimensioned "
                "view, so there is nothing on the sheet to point a leader at"
            )

    for traced, reason in misses.items():
        state.table(traced, reason)

    index = 0
    for key in sorted(wanted, key=lambda one: (one[0], -one[1])):
        claimants = wanted[key]
        group = found[key]
        if len(claimants) > 1:
            state.note_ambiguity(
                f"R{group.radius_mm:g} on the {key[0]} view is wanted by "
                + ", ".join(f"{one.feature}.{one.argument}" for one in claimants)
            )
            for one in claimants:
                state.table(
                    one,
                    "two stated dimensions resolve to the same round feature, so "
                    "the leader would point at one of them arbitrarily",
                )
            continue
        traced = claimants[0]
        state.placed.append(
            Dimension(
                view=key[0],
                kind=traced.kind,
                source=(
                    DimensionSource.PARAMETER if traced.is_traced else DimensionSource.GEOMETRY
                ),
                value=traced.value,
                count=group.count,
                centre=group.anchor,
                radius_mm=group.radius_mm,
                leader_deg=_LEADER_ANGLES[index % len(_LEADER_ANGLES)],
                parameter=", ".join(traced.parameters) or None,
                feature=traced.feature,
                note=traced.note,
            )
        )
        state.centre_lines.setdefault(key[0], []).extend(group.centre_marks())
        index += 1


# -- section indication -----------------------------------------------------


def _cutting_planes(
    shape: Any, request: LayoutRequest, cells: tuple[_Cell, ...]
) -> tuple[CuttingPlane, ...]:
    """Where each section was taken, drawn on a view that shows the plane edge-on.

    A section view with no cutting-plane line on the parent is a picture of a part
    nobody can locate the cut in — it could have been taken anywhere. The line is
    placed by projecting two points of the plane, so it is where the cut is rather
    than where it looks about right.
    """
    from app.render.section import section_named

    orthographic = [cell for cell in cells if cell.kind is ViewKind.ORTHOGRAPHIC]
    planes: list[CuttingPlane] = []
    for index, name in enumerate(request.sections):
        section = section_named(shape, name)
        normal = section.normal
        parent = next(
            (
                cell
                for cell in orthographic
                if abs(_dot(cell.camera.direction, normal)) < 1e-9
            ),
            None,
        )
        if parent is None:
            continue
        along = _cross(normal, parent.camera.direction)
        origin = parent.camera.to_view_mm(*section.origin)
        direction = (
            along[0] * parent.camera.right()[0]
            + along[1] * parent.camera.right()[1]
            + along[2] * parent.camera.right()[2],
            along[0] * parent.camera.frame_up()[0]
            + along[1] * parent.camera.frame_up()[1]
            + along[2] * parent.camera.frame_up()[2],
        )
        length = max(
            parent.extent[2] - parent.extent[0], parent.extent[3] - parent.extent[1]
        )
        reach = length * 0.6
        scale = (direction[0] ** 2 + direction[1] ** 2) ** 0.5
        if scale < 1e-12:
            continue
        unit = (direction[0] / scale, direction[1] / scale)
        looking = parent.camera.to_view_mm(*(-one for one in normal))
        planes.append(
            CuttingPlane(
                label=_section_letter(index),
                view=parent.name,
                start=(origin[0] - unit[0] * reach, origin[1] - unit[1] * reach),
                end=(origin[0] + unit[0] * reach, origin[1] + unit[1] * reach),
                looking=looking,
            )
        )
    return tuple(planes)


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _rationale_notes(traced: tuple[TracedDimension, ...]) -> tuple[str, ...]:
    """The design's own rationale, one line per feature that wrote one.

    `FeatureSpec.note` is the master plan's H5 slot — *"why is this rib here"* has
    an answer in six months, from the artefact. A drawing is the one document a
    manufacturer definitely reads, so it is where that answer is worth printing.
    De-duplicated by feature: a note belongs to a feature, not to each of its
    dimensions, and printing it three times because a pad had three numbers would
    bury the ones that differ.
    """
    seen: dict[str, str] = {}
    for one in traced:
        if one.note and one.feature not in seen:
            seen[one.feature] = one.note
    return tuple(f"{feature}: {note}" for feature, note in seen.items())


__all__ = [
    "DIMENSION_ALLOWANCE_MM",
    "NOTES_WIDTH_MM",
    "VIEW_GAP_MM",
    "DetailRequest",
    "LayoutRequest",
    "lay_out",
]
