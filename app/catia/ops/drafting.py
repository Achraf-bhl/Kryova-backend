"""Drafting: sheets, views, dimensions and annotations.

All 64 documented Drafting commands were missing, and the omission was not
cosmetic — a drawing is still the contractual deliverable in most of the
industries that run V5. A part with no drawing cannot be quoted, inspected or
made.

The structure mirrors how a drawing is actually built: create a sheet, put
views on it, dimension the views, annotate them. Views are *generative* — they
are derived from the 3D part and update when it changes — which is the whole
reason to make the drawing in CATIA rather than draw it again in 2D.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    count,
    direction3,
    flag,
    length,
    name_list,
    new_name,
    one_of,
    optional,
    point2,
    ratio,
    required,
    signed_angle,
    text,
)

_WB = Workbench.DRAFTING

#: ISO and ANSI sheet sizes. Closed, because a drawing on a non-standard sheet
#: will not print correctly at the other end and the failure shows up late.
SHEET_FORMATS = (
    "A0", "A1", "A2", "A3", "A4",
    "ANSI_A", "ANSI_B", "ANSI_C", "ANSI_D", "ANSI_E",
)

OPERATIONS: tuple[Operation, ...] = (
    # -- the drawing and its sheets -----------------------------------------
    Operation(
        name="catia_drawing_create",
        summary=(
            "Create a drawing document, optionally linked to a part or assembly.\n"
            "Choose the projection convention deliberately: first-angle is the "
            "European default and third-angle the North American one, and a drawing "
            "read in the wrong convention is a mirrored part."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the drawing.")),
            optional("source", vocab.element_reference("The part or assembly to draw.")),
            optional("format", one_of(SHEET_FORMATS, "Sheet size. Default A3.")),
            optional("landscape", flag("Landscape orientation. Default true.")),
            optional(
                "projection",
                one_of(("first_angle", "third_angle"), "Projection convention. Default first_angle."),
            ),
            optional("scale", ratio("Drawing scale. 1.0 is full size.")),
        ),
    ),
    Operation(
        name="catia_sheet_add",
        summary=(
            "Add a sheet to the drawing.\n"
            "A multi-sheet drawing keeps the general arrangement and the detail views "
            "on separate pages. Set `detail` for a sheet holding reusable 2D "
            "components rather than views of the model."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("name", new_name("A name for the sheet.")),
            optional("format", one_of(SHEET_FORMATS, "Sheet size. Default A3.")),
            optional("landscape", flag("Landscape orientation. Default true.")),
            optional("scale", ratio("Sheet scale. 1.0 is full size.")),
            optional("detail", flag("Create it as a detail sheet for reusable components. Default false.")),
        ),
    ),
    Operation(
        name="catia_sheet_frame",
        summary=(
            "Put a frame and title block on the sheet, filled from the document's "
            "properties.\n"
            "A drawing without a title block has no scale, no revision and no author "
            "on it, which makes it unusable as a released document however correct "
            "the geometry is."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("sheet", vocab.element_reference("Which sheet. Defaults to the active one.")),
            optional("title", text("Drawing title.", maximum=200)),
            optional("drawn_by", text("Who drew it.", maximum=120)),
            optional("revision", text("Revision.", maximum=60)),
            optional("company", text("Company name.", maximum=200)),
        ),
    ),
    # -- views ---------------------------------------------------------------
    Operation(
        name="catia_view_add",
        summary=(
            "Add a view of the 3D model to the sheet — front, projected, auxiliary, "
            "isometric, section, detail, clipping, broken or exploded.\n"
            "Start with a front view and project the others from it: projected views "
            "inherit their alignment and scale, so they stay square to each other when "
            "anything moves. A section view needs `section_line`; a detail view needs "
            "`centre` and `radius_mm`."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("kind", one_of(vocab.DRAWING_VIEWS, "Which kind of view.")),
            optional("name", new_name("A name for the view.")),
            optional("at", point2("Where on the sheet to place it, in millimetres.")),
            optional("parent", vocab.element_reference("The view to project or detail from.")),
            optional("direction", direction3("Viewing direction, for a front or auxiliary view.")),
            optional("section_line", name_list("Points defining the section line, for a section view.")),
            optional("centre", point2("Centre of the detail circle.")),
            optional("radius_mm", length("Radius of the detail circle.")),
            optional("scale", ratio("View scale. Defaults to the sheet's.")),
            optional("angle_deg", signed_angle("Rotate the view on the sheet.")),
        ),
    ),
    Operation(
        name="catia_view_properties",
        summary=(
            "Set how a view is displayed — hidden lines, centre lines, threads, axes, "
            "fillet edges, and whether its scale is shown.\n"
            "Hidden lines off is the usual default for a busy assembly view and on for "
            "a single part; both are legitimate and the drawing standard decides."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("view", vocab.element_reference("The view to change.")),
            optional("hidden_lines", flag("Show hidden lines.")),
            optional("centre_lines", flag("Show centre lines.")),
            optional("axes", flag("Show axes.")),
            optional("threads", flag("Show thread representation.")),
            optional("fillet_edges", flag("Show tangent edges of fillets.")),
            optional("show_scale", flag("Print the view's scale under it.")),
            optional("locked", flag("Lock the view against accidental edits.")),
        ),
    ),
    Operation(
        name="catia_view_align",
        summary=(
            "Align a view to another, or break an alignment so it can be moved freely.\n"
            "Projected views arrive aligned to their parent. Breaking that is "
            "occasionally right — a detail view placed where there is room — and "
            "usually a mistake."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("view", vocab.element_reference("The view to align.")),
            optional("reference", vocab.element_reference("The view to align it to.")),
            optional("aligned", flag("True to align, false to break the alignment. Default true.")),
        ),
    ),
    # -- dimensions ----------------------------------------------------------
    Operation(
        name="catia_dimension_add",
        summary=(
            "Dimension geometry in a view — a length, distance, angle, radius, "
            "diameter, chamfer or thread.\n"
            "Dimension the view, not the model: the dimension attaches to the drawn "
            "edges and updates when the part changes. A dimension typed as text is a "
            "dimension that will eventually be wrong."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("kind", one_of(vocab.DIMENSION_KINDS, "Which kind of dimension.")),
            required("elements", name_list("The one or two drawn elements to measure.")),
            optional("view", vocab.element_reference("Which view. Defaults to the active one.")),
            optional("at", point2("Where to put the dimension text, in millimetres.")),
            optional("tolerance", text("Tolerance, e.g. '+0.1/-0.05' or 'H7'.", maximum=60)),
            optional("prefix", text("Text before the value, e.g. '4x'.", maximum=60)),
            optional("suffix", text("Text after the value.", maximum=60)),
            optional("reference", flag("Create it as a reference dimension, in brackets. Default false.")),
        ),
    ),
    Operation(
        name="catia_dimension_chain",
        summary=(
            "Create a run of chained, stacked or cumulated dimensions from one datum.\n"
            "Cumulated dimensions from a single datum accumulate no tolerance, which is "
            "why they are preferred for machined features; chained ones stack tolerance "
            "at every step."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "style",
                one_of(("chained", "stacked", "cumulated"), "How the dimensions relate."),
            ),
            required("elements", name_list("The elements to dimension, in order.", minimum=2)),
            optional("view", vocab.element_reference("Which view.")),
            optional("datum", vocab.element_reference("The element to measure everything from.")),
        ),
    ),
    Operation(
        name="catia_dimension_generate",
        summary=(
            "Generate dimensions automatically from the 3D model's own constraints.\n"
            "A fast first pass, never a finished drawing: it produces every dimension "
            "the model carries, in arbitrary positions, and the drawing still needs a "
            "human to choose which of them a maker actually needs."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("view", vocab.element_reference("Which view. Defaults to all of them.")),
            optional("filter", one_of(("all", "constraints", "3d_annotations"), "What to generate from.")),
            optional("step_by_step", flag("Generate one at a time for review. Default false.")),
        ),
    ),
    Operation(
        name="catia_tolerance_add",
        summary=(
            "Add a geometric tolerance — flatness, position, concentricity and the "
            "rest — with its datum references.\n"
            "This is GD&T proper, not a plus-minus on a dimension: it constrains form "
            "and position rather than size, and it is what an inspection department "
            "measures against."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "characteristic",
                one_of(
                    (
                        "straightness", "flatness", "circularity", "cylindricity",
                        "profile_line", "profile_surface", "angularity", "perpendicularity",
                        "parallelism", "position", "concentricity", "symmetry",
                        "circular_runout", "total_runout",
                    ),
                    "Which geometric characteristic is controlled.",
                ),
            ),
            required("element", vocab.element_reference("The drawn element it applies to.")),
            required("value_mm", length("The tolerance zone size.")),
            optional("datums", name_list("Datum references, in order (primary first).")),
            optional(
                "modifier",
                one_of(("none", "MMC", "LMC", "RFS"), "Material condition modifier. Default none."),
            ),
            optional("at", point2("Where to put the frame, in millimetres.")),
        ),
    ),
    Operation(
        name="catia_datum_add",
        summary=(
            "Add a datum feature symbol — the A, B, C a geometric tolerance references.\n"
            "Add the datums before the tolerances that cite them: a geometric "
            "tolerance referencing a datum that does not exist yet is left dangling."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("The drawn element that is the datum.")),
            required("label", text("The datum letter.", maximum=8)),
            optional("at", point2("Where to put the symbol, in millimetres.")),
        ),
    ),
    # -- annotation ----------------------------------------------------------
    Operation(
        name="catia_annotation_add",
        summary=(
            "Add an annotation to a view — text, text with a leader, a balloon, a "
            "roughness symbol or a welding symbol.\n"
            "A balloon references a bill-of-materials line, so it is only meaningful "
            "on an assembly drawing whose components carry part numbers."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("text", "text_with_leader", "balloon", "roughness", "welding", "flag_note"),
                    "Which annotation.",
                ),
            ),
            required("at", point2("Where to put it, in millimetres.")),
            optional("content", text("The text, symbol value or balloon number.")),
            optional("view", vocab.element_reference("Which view. Defaults to the active one.")),
            optional("leader_to", point2("Where the leader line points.")),
            optional("height_mm", length("Text height. Defaults to the drawing standard's.")),
        ),
    ),
    Operation(
        name="catia_dressup_add",
        summary=(
            "Add drawing dress-up — centre lines, axis lines, threads, area fill "
            "(hatching) or an arrow.\n"
            "Section views arrive hatched; this is for the hatching a broken-out or "
            "manually drawn region needs."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("centre_line", "axis_line", "thread", "area_fill", "arrow"),
                    "Which dress-up element.",
                ),
            ),
            optional("elements", name_list("The drawn elements it attaches to.")),
            optional("view", vocab.element_reference("Which view.")),
            optional("at", point2("Where to put it, for a free-standing element.")),
            optional("pattern", text("Hatch pattern name, for an area fill.", maximum=60)),
            optional("angle_deg", signed_angle("Hatch or arrow angle.")),
        ),
    ),
    Operation(
        name="catia_table_add",
        summary=(
            "Add a table to the sheet — an empty grid, or a bill of materials generated "
            "from the assembly.\n"
            "A generated BOM table stays linked: add a component to the assembly and "
            "the table gains a row."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("at", point2("Where to put the table, in millimetres.")),
            optional("kind", one_of(("empty", "bill_of_materials"), "What kind of table. Default empty.")),
            optional("rows", count("Number of rows, for an empty table.", maximum=200)),
            optional("columns", count("Number of columns, for an empty table.", maximum=50)),
            optional("title", text("A title for the table.", maximum=200)),
        ),
    ),
    Operation(
        name="catia_drawing_update",
        summary=(
            "Regenerate the drawing's views from the current state of the 3D model.\n"
            "Views do not follow the model automatically in every configuration. Run "
            "this before exporting or printing, or the drawing may show the part as it "
            "was several edits ago."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(optional("sheet", vocab.element_reference("Which sheet. Defaults to all of them.")),),
    ),
)
