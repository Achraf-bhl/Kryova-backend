"""A drawing as a document: views, dimensions, notes, and what it could not say.

**This is not an image.** `app/render/` already makes pictures of a part, and a
picture is the wrong artefact to hand a manufacturer: it has no scale, no
projection convention, no dimensions with values, and nothing that says which of
its numbers came from a design decision and which were read off the model. What
lives here is the *document* — a sheet, views placed on it in a stated
projection, dimensions that each know where they came from, and an explicit
account of what is missing. `dxf.py` writes it out; nothing here knows about DXF.

**The three coordinate systems, because getting them confused is the whole job.**

* *Model millimetres* — the part, in world coordinates. Never appears here.
* *View millimetres* — a projection of the part onto one camera's plane, +x right
  and +y up, exactly what `app.render.project.Projection` reports. Every polyline
  and every dimension anchor in this module is in view millimetres, at 1:1.
* *Sheet millimetres* — where it lands on the paper. `DrawnView.to_sheet_mm`
  is the only place the two are joined, and it is the only place the scale is
  applied.

Keeping the geometry at 1:1 in view millimetres rather than pre-scaling it is
what makes a dimension's *value* independent of the scale it is drawn at. A
drawing scaled 1:2 whose dimension text said 60 because the line was 60 mm long
on paper would be a drawing that lies at every scale but 1:1.

**A dimension knows where it came from.** `DimensionSource.PARAMETER` means a
design parameter says so — `width_mm = 120`, and if that parameter moves the part
moves with it. `DimensionSource.GEOMETRY` means it was measured off the model as
built: true, but it is a consequence rather than a decision, and nothing
guarantees it survives an edit. The two print identically on the sheet, because a
shop measures to the number either way, and they are told apart in the
`DimensionReport` and in the manifest, where the difference is what an engineer
reviewing provenance actually wants.

**What is missing is part of the document.** `DimensionReport.complete` is false
whenever anything the design states could not be placed on a view, and
`statement()` is the block of words that goes on the sheet when it is. A drawing
that silently omits a dimension is how a part gets made wrong; this codebase's
standing rule is that an unmeasured thing is never reported as a pass, and a
drawing is where that rule matters most.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.manufacture.errors import DrawingError
from app.manufacture.sheet import SheetSize, TitleBlock, scale_text

#: A run of connected points in view millimetres. The same shape
#: `app.render.project.Projection` reports, deliberately: a projection can be
#: handed straight to a `DrawnView` with no conversion step to get wrong.
Polyline = tuple[tuple[float, float], ...]

#: How far a dimension line sits off the feature it measures, in sheet
#: millimetres, when the caller does not say. ISO 129-1 wants at least 10 mm off
#: the outline for the first row and 7 mm between rows; 10 is the readable one.
DIMENSION_OFFSET_MM: Final = 10.0

#: Gap between successive dimension rows on the same side of a view, sheet mm.
DIMENSION_ROW_MM: Final = 8.0


class ViewKind(StrEnum):
    """What sort of view this is, which decides how it is labelled and placed."""

    #: One of the six orthographic views. Aligned with its neighbours.
    ORTHOGRAPHIC = "orthographic"

    #: A pictorial view. Carries no dimensions and is not aligned with anything —
    #: an isometric view is not an orthographic projection and placing it on the
    #: projection grid would imply it was.
    PICTORIAL = "pictorial"

    #: A cut, drawn hatched, labelled `SECTION A-A`.
    SECTION = "section"

    #: A magnified crop of another view, labelled `DETAIL B (2:1)`.
    DETAIL = "detail"


class DimensionKind(StrEnum):
    """What a dimension measures, which decides how it is drawn and prefixed."""

    #: A length whose direction on the sheet is not decided yet. What tracing a
    #: design produces: `thick_mm = 8` is a length, and which view it lies across
    #: is a question about the geometry, answered later by `layout.py`.
    LINEAR = "linear"

    LINEAR_HORIZONTAL = "linear_horizontal"
    LINEAR_VERTICAL = "linear_vertical"
    DIAMETER = "diameter"
    RADIUS = "radius"
    ANGULAR = "angular"


#: The kinds drawn between two anchor points rather than off a leader.
LINEAR_KINDS: Final[frozenset[DimensionKind]] = frozenset(
    {
        DimensionKind.LINEAR,
        DimensionKind.LINEAR_HORIZONTAL,
        DimensionKind.LINEAR_VERTICAL,
    }
)


class DimensionSource(StrEnum):
    """Where a dimension's number came from.

    The distinction this package exists to exploit. A `PARAMETER` dimension is a
    stated design decision with a name — it can be changed, and the part
    regenerates. A `GEOMETRY` dimension was measured off the model: correct as
    drawn, and a consequence of decisions made elsewhere.
    """

    PARAMETER = "parameter"
    GEOMETRY = "geometry"


@dataclass(frozen=True)
class TracedDimension:
    """One dimension the *design* states, before anything tries to place it.

    Produced by `dimensions.trace_dimensions` from a `DesignSpec` and its
    compiled `Plan`. It is the leverage the master plan's Phase 17 note points
    at: a CAD system reverse-engineers dimensions from a solid, and this knows
    the pad was 12 mm because `wall_mm` said 12.

    `parameters` is the set of design parameters the value depends on — one for
    `=thick_mm`, several for `=plate_mm + 2 * gap_mm`, and empty for a literal
    typed straight into the feature, which is exactly the case the report calls
    *untraced*.
    """

    feature: str
    op: str
    argument: str
    kind: DimensionKind
    value: float
    unit: str
    parameters: tuple[str, ...] = ()
    expression: str | None = None
    note: str = ""

    @property
    def is_traced(self) -> bool:
        """Does a design parameter stand behind this number?"""
        return bool(self.parameters)

    @property
    def label(self) -> str:
        """How this dimension is written on a sheet or in a table."""
        return _dimension_text(self.kind, self.value, self.unit)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "feature": self.feature,
            "op": self.op,
            "argument": self.argument,
            "kind": str(self.kind),
            "value": self.value,
            "unit": self.unit,
        }
        if self.parameters:
            out["parameters"] = list(self.parameters)
        if self.expression is not None:
            out["expression"] = self.expression
        if self.note:
            out["note"] = self.note
        return out


def _dimension_text(kind: DimensionKind, value: float, unit: str, count: int = 1) -> str:
    """Dimension text to ISO 129-1: a count prefix, a symbol, then the number.

    `4X R5` and not `R5 (4 places)`: the count prefix is the ISO form and the one
    a CNC programmer's eye is trained on. The unit is never printed on a linear
    dimension — the title block says the sheet is in millimetres, and repeating
    it on every dimension is what ISO 129-1 exists to stop. An angle keeps its
    degree sign, because a bare number next to a linear one would be read as mm.
    """
    prefix = f"{count}X " if count > 1 else ""
    if kind is DimensionKind.DIAMETER:
        return f"{prefix}Ø{_number(value)}"
    if kind is DimensionKind.RADIUS:
        return f"{prefix}R{_number(value)}"
    if kind is DimensionKind.ANGULAR:
        return f"{prefix}{_number(value)}°"
    if unit and unit != "mm":
        return f"{prefix}{_number(value)} {unit}"
    return f"{prefix}{_number(value)}"


def _number(value: float) -> str:
    """A dimension value as a drawing prints it: no trailing zeros, no exponent.

    Rounded to the micron before printing. A drawing that says `60.000001`
    because the kernel integrated a face to a part in 10^8 is a drawing an
    inspector will reject the part against, and the micron is already finer than
    anything this codebase's tolerances mean.
    """
    rounded = round(float(value), 3)
    if abs(rounded) < 5e-4:
        rounded = 0.0
    text = f"{rounded:.3f}".rstrip("0").rstrip(".")
    return text if text and text != "-0" else "0"


@dataclass(frozen=True)
class Dimension:
    """One dimension placed on one view, in view millimetres.

    A linear dimension is anchored by `start` and `end` — the two points it
    measures between, on the geometry — and drawn `offset_mm` (sheet millimetres)
    clear of them. A diameter or radius is anchored by `centre` and `radius_mm`
    and drawn as a leader.

    `value` is the number that gets printed, and it is **not** derived from the
    distance between the anchors. For a `PARAMETER` dimension the design's number
    is authoritative and the anchors are only where to draw it; for a `GEOMETRY`
    one they agree by construction. Deriving the text from the anchors would make
    a drawing whose dimensions are a restatement of its own line work, which is
    the property that makes a CAD system's reverse-engineered dimensions worth so
    much less than these.
    """

    view: str
    kind: DimensionKind
    source: DimensionSource
    value: float
    unit: str = "mm"
    count: int = 1
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    centre: tuple[float, float] | None = None
    radius_mm: float | None = None
    offset_mm: float = DIMENSION_OFFSET_MM
    #: Which way the leader leaves the feature, in degrees anticlockwise from the
    #: sheet's horizontal. `None` lets the renderer choose. `layout.py` sets it
    #: explicitly when several round features share a view, because leaders that
    #: all set off in the same direction cross each other and land on top of one
    #: another — a drawing whose dimensions are illegible is as unusable as one
    #: whose dimensions are missing.
    leader_deg: float | None = None
    parameter: str | None = None
    feature: str | None = None
    #: The feature argument this number came from — `length_mm`, `radius_mm[2]`.
    #: The same key `TracedDimension` and `Unplaced` carry, so the three buckets
    #: of `DimensionReport` can be shown to be disjoint rather than asserted to
    #: be. Without it a placed dimension is identified only by its feature, and a
    #: feature with two numbers in it could be in two buckets with nothing able
    #: to see that it was.
    argument: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if self.source is DimensionSource.PARAMETER and not self.parameter:
            raise DrawingError(
                f"A dimension on {self.view} claims a design parameter set it but does "
                "not say which one. A drawing that asserts a provenance it cannot name "
                "is worse than one that admits the number was measured — the whole "
                "value of the distinction is that a reviewer can go and look at the "
                "parameter. Use DimensionSource.GEOMETRY for a literal typed straight "
                "into a feature."
            )
        linear = self.kind in LINEAR_KINDS
        if linear and (self.start is None or self.end is None):
            raise DrawingError(
                f"A {self.kind} dimension on {self.view} needs the two points it "
                "measures between; without them there is nowhere to draw it."
            )
        if not linear and self.centre is None:
            raise DrawingError(
                f"A {self.kind} dimension on {self.view} needs a centre to point its "
                "leader at. An unplaced dimension belongs in the table, not on a view."
            )

    @property
    def text(self) -> str:
        return _dimension_text(self.kind, self.value, self.unit, self.count)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "view": self.view,
            "kind": str(self.kind),
            "source": str(self.source),
            "value": self.value,
            "unit": self.unit,
            "text": self.text,
        }
        if self.count > 1:
            out["count"] = self.count
        if self.parameter:
            out["parameter"] = self.parameter
        if self.feature:
            out["feature"] = self.feature
        if self.argument:
            out["argument"] = self.argument
        return out


@dataclass(frozen=True)
class CuttingPlane:
    """Where a section was taken, drawn on the view it was taken from.

    ISO 128-40: a long-dash-dot line across the parent view, thickened at its
    ends, with arrows pointing **the way the section is viewed** and the section
    letter beside each arrow.

    `looking` is the direction the arrows point, in the parent view's own
    millimetres. It is derived from the section's own convention in
    `app.render.section` — where the normal points at the material that is
    *removed* — so the arrows point back along that normal, at the material that
    is kept. Two conventions for one question is how a part ends up mirrored with
    every test green (CLAUDE.md), so this module never restates the rule; it
    reads the `Section` it was given.
    """

    label: str
    view: str
    start: tuple[float, float]
    end: tuple[float, float]
    looking: tuple[float, float]


@dataclass(frozen=True)
class DrawnView:
    """One view, its line work, and where it sits on the sheet.

    `visible` and `hidden` are kept apart the whole way to the DXF, where they
    land on different layers with different line types — the distinction is the
    most informative thing in a drawing, and merging them loses which features
    are behind others.
    """

    name: str
    label: str
    kind: ViewKind
    origin_mm: tuple[float, float]
    scale: float
    visible: tuple[Polyline, ...] = ()
    hidden: tuple[Polyline, ...] = ()
    extent: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    #: Closed boundaries to hatch, in view millimetres. Only a section view has
    #: any. They are boundaries rather than a fill pattern because the pattern is
    #: a property of the output format — DXF has a HATCH entity that does it
    #: properly, and pre-computing hatch lines here would throw that away.
    hatch: tuple[Polyline, ...] = ()

    #: Centre lines, in view millimetres. Drawn on their own layer with a
    #: dash-dot line type, because a hole with no centre mark is a hole an
    #: inspector cannot find the middle of.
    centre_lines: tuple[Polyline, ...] = ()

    def to_sheet_mm(self, point: tuple[float, float]) -> tuple[float, float]:
        """One point in view millimetres to its place on the sheet.

        The single join between the two coordinate systems in this package. The
        view's extent centre lands on `origin_mm`, so a view moves by moving its
        origin and nothing else has to be recomputed.
        """
        low_x, low_y, high_x, high_y = self.extent
        centre_x, centre_y = (low_x + high_x) / 2.0, (low_y + high_y) / 2.0
        return (
            self.origin_mm[0] + (point[0] - centre_x) * self.scale,
            self.origin_mm[1] + (point[1] - centre_y) * self.scale,
        )

    @property
    def size_mm(self) -> tuple[float, float]:
        """How much sheet this view's line work covers, at its own scale."""
        low_x, low_y, high_x, high_y = self.extent
        return ((high_x - low_x) * self.scale, (high_y - low_y) * self.scale)

    @property
    def is_empty(self) -> bool:
        return not self.visible and not self.hidden


@dataclass(frozen=True)
class Unplaced:
    """A dimension the design states that did not reach a view, and why.

    The reason is the whole value of this type. "4 dimensions not placed" tells a
    reader nothing they can act on; "bracket.bore diameter 14: no round feature of
    R7 reads as a circle in any view" tells them the bore is not where the drawing
    thinks it is, or that a view is missing. It is printed in the sheet's table
    and carried into the manifest.
    """

    dimension: TracedDimension
    reason: str

    def to_dict(self) -> dict[str, Any]:
        out = self.dimension.to_dict()
        out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class DimensionReport:
    """What the drawing dimensioned, and — the part that matters — what it did not.

    Three buckets, disjoint by construction:

    * `placed` — on a view, with a value and an anchor. Each records whether its
      number came from a design parameter or was measured off the solid.
    * `tabled` — the design states the number and this build could not place it:
      no round feature of that radius reads as a circle in any view, no overall
      extent matches the length, or two features matched and picking one would be
      a guess. It is printed in a table on the sheet, so the value still reaches
      the shop, and it counts against completeness — a tabled dimension is not a
      dimensioned feature.
    * `non_dimensional` — numeric arguments that are not dimensions at all: a
      pattern count, an instance index. Listed rather than dropped, because "the
      report did not mention it" and "the report decided it did not matter" read
      identically to somebody checking.

    **Two different completeness questions, and conflating them would hide one.**
    `complete` asks whether every dimension the design states reached a view — the
    question a shop asks, and the one that decides whether the DO-NOT-MANUFACTURE
    banner is printed. `fully_traced` asks whether every dimension on the sheet
    has a design parameter behind it — the question an engineer reviewing
    provenance asks, and a drawing can be complete without being fully traced: a
    part built call by call in a conversation has no parameters at all, so every
    number on its drawing is measured off the solid. That is a real and useful
    drawing; it just cannot promise that an edit to the design would move any of
    those numbers.

    `complete` is true only when `tabled` is empty **and** something was actually
    placed. An empty design that dimensioned nothing is not a completely
    dimensioned drawing; it is a blank sheet, and reporting it as complete is
    exactly the false green `app.design.assertions` refuses to give an unmeasured
    assertion.
    """

    placed: tuple[Dimension, ...] = ()
    tabled: tuple[Unplaced, ...] = ()
    non_dimensional: tuple[TracedDimension, ...] = ()

    #: Features suppressed by their `when` expression, so they are not on the
    #: part and must not be dimensioned. Named, because "there is no pocket" is a
    #: mystery and "the pocket is not there below 6 mm" is an answer.
    suppressed: tuple[str, ...] = ()

    #: Where two stated dimensions matched the same feature in the geometry, so
    #: placing either would be a guess about which. Both are also in `tabled`;
    #: this says what collided.
    ambiguous: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return bool(self.placed) and not self.tabled

    @property
    def untraced_placed(self) -> tuple[Dimension, ...]:
        """Placed dimensions with no design parameter behind them.

        Two different things land here and they deserve the same warning: a
        number measured off the solid, and a number typed as a literal straight
        into a feature. Neither is controlled by anything — edit the design and
        nothing moves them — which is the only property the sheet's reader cares
        about, so they are one bucket rather than two.
        """
        return tuple(one for one in self.placed if one.source is DimensionSource.GEOMETRY)

    @property
    def fully_traced(self) -> bool:
        """Does every dimension on this sheet trace back to a design decision?"""
        return self.complete and not self.untraced_placed

    def statement(self) -> tuple[str, ...]:
        """The words that go on the sheet. Empty only when nothing needs saying.

        Deliberately blunt where it matters. A shop reading a drawing needs to
        know before it quotes, not after it cuts, that the drawing does not carry
        every dimension the part needs — and the sentence has to survive being
        read at a glance next to a title block.

        The provenance line is a separate, milder sentence, because a complete
        drawing whose numbers were measured rather than declared is usable and a
        drawing missing a dimension is not. Printing one warning for both would
        teach a reader to ignore it.
        """
        lines: list[str] = []
        if not self.complete:
            lines.append(
                "INCOMPLETE DIMENSION SCHEME - DO NOT MANUFACTURE FROM THIS SHEET ALONE."
            )
            if not self.placed:
                lines.append("No dimension could be placed on any view.")
            else:
                lines.append(f"{len(self.placed)} dimension(s) placed on the views.")
            if self.tabled:
                lines.append(
                    f"{len(self.tabled)} stated by the design but not placed on a view; "
                    "each is listed with its reason in the table below."
                )
            if self.ambiguous:
                lines.append("Ambiguous: " + "; ".join(self.ambiguous) + ".")
        if self.untraced_placed:
            lines.append(
                f"{len(self.untraced_placed)} dimension(s) have no design parameter "
                "behind them - the number was measured from the model or typed straight "
                "into the feature, and nothing would move it if the design changed."
            )
        if self.suppressed:
            lines.append(
                "Suppressed features, not present on the part: "
                + ", ".join(self.suppressed)
                + "."
            )
        if lines:
            lines.append("No geometric tolerancing is applied; none is implied.")
        return tuple(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "fully_traced": self.fully_traced,
            "placed": [one.to_dict() for one in self.placed],
            "tabled": [one.to_dict() for one in self.tabled],
            "non_dimensional": [one.to_dict() for one in self.non_dimensional],
            "suppressed": list(self.suppressed),
            "ambiguous": list(self.ambiguous),
            "statement": list(self.statement()),
        }


@dataclass(frozen=True)
class Drawing:
    """One sheet: the views on it, the dimensions, the title block, the honesty.

    Immutable, and carries no bytes. `dxf.write` turns it into a file; nothing
    here knows what a file is, which is what keeps a second output format (a PDF,
    a CATIA drawing through the bridge) a new writer rather than a rewrite.
    """

    title: str
    sheet: SheetSize
    title_block: TitleBlock
    views: tuple[DrawnView, ...] = ()
    dimensions: tuple[Dimension, ...] = ()
    cutting_planes: tuple[CuttingPlane, ...] = ()
    report: DimensionReport = field(default_factory=DimensionReport)

    #: Free notes printed as a numbered list above the title block — the design's
    #: own rationale, carried from `FeatureSpec.note`. Most CAD systems have
    #: nowhere for this to come from; here it travels with the design (master
    #: plan H5) and a drawing is the one place a manufacturer will read it.
    notes: tuple[str, ...] = ()

    def view_named(self, name: str) -> DrawnView:
        for candidate in self.views:
            if candidate.name == name:
                return candidate
        known = ", ".join(one.name for one in self.views) or "none"
        raise DrawingError(f"This drawing has no view called {name!r}. Views: {known}.")

    def dimensions_for(self, view: str) -> tuple[Dimension, ...]:
        return tuple(one for one in self.dimensions if one.view == view)

    @property
    def fully_dimensioned(self) -> bool:
        """Whether every dimension the design states reached a view.

        The single question a caller must be able to ask, and it is a property of
        the drawing rather than something buried in the report, because a caller
        who forgets to look at the report should still trip over this.
        """
        return self.report.complete

    def to_dict(self) -> dict[str, Any]:
        """The drawing as data — what the manifest records about it."""
        return {
            "title": self.title,
            "sheet": self.sheet.name,
            "scale": scale_text(self.title_block.scale),
            "projection": str(self.title_block.projection),
            "units": "mm",
            "views": [
                {
                    "name": one.name,
                    "label": one.label,
                    "kind": str(one.kind),
                    "scale": scale_text(one.scale),
                }
                for one in self.views
            ],
            "dimensions": [one.to_dict() for one in self.dimensions],
            "fully_dimensioned": self.fully_dimensioned,
            "report": self.report.to_dict(),
            "notes": list(self.notes),
        }


__all__ = [
    "DIMENSION_OFFSET_MM",
    "DIMENSION_ROW_MM",
    "LINEAR_KINDS",
    "CuttingPlane",
    "Dimension",
    "DimensionKind",
    "DimensionReport",
    "DimensionSource",
    "Drawing",
    "DrawnView",
    "Polyline",
    "TracedDimension",
    "Unplaced",
    "ViewKind",
]
