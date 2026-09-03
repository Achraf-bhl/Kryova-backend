"""Generative Shape Design: surfaces and the operations that trim them together.

87 of GSD's 88 documented commands had no tool at all. They collapse into far
fewer operations than that number suggests, because CATIA's own API groups them
— `HybridShapeFactory` has one `AddNewSweepExplicit` behind what the menus
present as four sweep variants, and one `AddNewFill` behind Fill and Volume
Fill.

The split between this module and `wireframe.py` follows the toolbar: curves
and points there, surfaces and the operations on them here. `part_design.py`
keeps the solids. A surface becomes a solid through `catia_close_surface` or
`catia_thick_surface`, which is the seam between the two workbenches and the
step people most often forget.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    angle,
    direction3,
    distance,
    feature_length,
    flag,
    length,
    name_list,
    new_name,
    one_of,
    optional,
    required,
    thickness,
)

_WB = Workbench.GENERATIVE_SHAPE_DESIGN

OPERATIONS: tuple[Operation, ...] = (
    # -- creating surfaces ---------------------------------------------------
    Operation(
        name="catia_surface_extrude",
        summary=(
            "Sweep a curve along a straight direction to make a surface.\n"
            "The surface equivalent of a pad, and usually the first step when a shape "
            "is easier to describe as skin than as material."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("profile", vocab.element_reference("The curve to sweep.")),
            required("direction", direction3("Which way to sweep it.")),
            required("length_mm", length("How far to sweep.")),
            optional("second_length_mm", distance("Extent in the opposite direction.")),
            optional("symmetric", flag("Extend equally both ways. Default false.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_revolve",
        summary=(
            "Revolve a curve about an axis to make a surface of revolution.\n"
            "The surface counterpart of a shaft. Unlike catia_shaft the profile need "
            "not be closed, which is what lets it make an open skin such as a cone or "
            "a dished end."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("profile", vocab.element_reference("The curve to revolve.")),
            required("axis", vocab.element_reference("The axis to revolve about.")),
            optional("angle_deg", angle("How far to revolve. Default 360.")),
            optional("second_angle_deg", angle("Sweep in the opposite direction as well.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_offset",
        summary=(
            "Create a surface parallel to an existing one, at a distance.\n"
            "Offsetting fails where the offset distance exceeds a local radius of "
            "curvature — the surface would self-intersect. When it does, reduce the "
            "distance or offset in sections rather than fighting it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to offset from.")),
            required("distance_mm", distance("How far to offset.")),
            optional("reversed", flag("Offset the other way. Default false.")),
            optional("both_sides", flag("Create a surface on each side. Default false.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_fill",
        summary=(
            "Fill a closed boundary of curves with a surface.\n"
            "How a hole in a skin is patched. Give `continuity: 'tangent'` and the "
            "supporting surfaces so the patch meets its neighbours smoothly rather "
            "than with a visible crease."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("boundary", name_list("The curves forming the closed boundary, in order.")),
            optional("supports", name_list("The neighbouring surfaces to match continuity against.")),
            optional(
                "continuity",
                one_of(("point", "tangent", "curvature"), "How smoothly it meets its supports. Default point."),
            ),
            optional("passing_point", vocab.element_reference("A point the surface must pass through.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_loft",
        summary=(
            "Loft a surface through a series of section curves — a Multi-sections Surface.\n"
            "The workhorse of shape design: a wing, a duct, a bottle. Give guides to "
            "control the path between sections, and a spine when the sections should "
            "stay square to a particular curve rather than to each other."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sections", name_list("The section curves, in order along the shape.", minimum=2)),
            optional("guides", name_list("Curves that steer the surface between sections.")),
            optional("spine", vocab.element_reference("A curve the sections stay normal to.")),
            optional("closed", flag("Close the loft back onto its first section. Default false.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_sweep",
        summary=(
            "Sweep a profile along a guide curve to make a surface.\n"
            "Four shapes in one operation, matching CATIA's own: an explicit profile, "
            "a line, a circle or a conic. The `explicit` kind sweeps a curve you drew; "
            "the others generate the profile from the parameters, which is far more "
            "robust for a constant-radius pipe or fillet-like blend."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(("explicit", "line", "circle", "conic"), "What shape is swept."),
            ),
            required("guide", vocab.element_reference("The curve to sweep along.")),
            optional("profile", vocab.element_reference("The curve to sweep, for the explicit kind.")),
            optional("spine", vocab.element_reference("A curve controlling the sweep's orientation.")),
            optional("reference_surface", vocab.element_reference("A surface the profile stays at an angle to.")),
            optional("angle_deg", angle("Angle to the reference surface.", maximum=179.0)),
            optional("radius_mm", feature_length("Radius, for the circle kind.")),
            optional("second_guide", vocab.element_reference("A second guide curve.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_blend",
        summary=(
            "Blend a surface between two curves, each on its own supporting surface.\n"
            "Unlike a fillet, a blend does not need a constant radius and does not need "
            "the two surfaces to nearly touch. It is what joins two shapes that were "
            "designed independently."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first_curve", vocab.element_reference("The curve at one end.")),
            required("second_curve", vocab.element_reference("The curve at the other end.")),
            optional("first_support", vocab.element_reference("The surface the first curve lies on.")),
            optional("second_support", vocab.element_reference("The surface the second curve lies on.")),
            optional(
                "continuity",
                one_of(("point", "tangent", "curvature"), "Continuity with the supports. Default tangent."),
            ),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    Operation(
        name="catia_surface_primitive",
        summary=(
            "Create a analytic surface primitive — a sphere or a cylinder — from its "
            "centre or axis and its radius.\n"
            "Faster and more robust than revolving a curve when the shape genuinely is "
            "a sphere or a cylinder."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("kind", one_of(("sphere", "cylinder"), "Which primitive.")),
            required("radius_mm", length("Radius.")),
            optional("centre", vocab.element_reference("Centre point, for a sphere.")),
            optional("axis", vocab.element_reference("Axis line, for a cylinder.")),
            optional("length_mm", length("Length, for a cylinder.")),
            optional("name", new_name("A name for the surface.")),
        ),
    ),
    # -- operations on surfaces ---------------------------------------------
    Operation(
        name="catia_join",
        summary=(
            "Join surfaces or curves into a single element.\n"
            "Almost every downstream operation — close, thick, split, a solid boolean — "
            "wants one surface rather than fourteen. Joining is the step that makes "
            "them one, and `tolerance_mm` is what bridges the small gaps left by "
            "imported data."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The surfaces or curves to join.", minimum=2)),
            optional("tolerance_mm", feature_length("Largest gap to bridge. Default CATIA's own.")),
            optional("check_connexity", flag("Fail if the result is not connected. Default true.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_split",
        summary=(
            "Cut a surface or curve with another element and keep one side.\n"
            "`keep` chooses the side; if the wrong half survives, flip it rather than "
            "rebuilding the cut."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("What is being cut.")),
            required("cutting", vocab.element_reference("What cuts it.")),
            optional("keep", one_of(("first", "second", "both"), "Which side survives. Default first.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_trim",
        summary=(
            "Cut two surfaces or curves against each other and keep a piece of each.\n"
            "Split discards one side of one element; trim keeps a chosen part of both, "
            "which is what joins two intersecting skins into one continuous one."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("Exactly two elements to trim together.", minimum=2)),
            optional("keep_first", flag("Keep the first element's near side. Default true.")),
            optional("keep_second", flag("Keep the second element's near side. Default true.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_extract",
        summary=(
            "Extract a face, edge or set of them from existing geometry as its own "
            "element.\n"
            "This is how a surface is derived from a solid so it can be offset, "
            "trimmed or handed to another operation without touching the solid."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The faces or edges to extract.")),
            optional(
                "propagation",
                one_of(
                    ("none", "tangent", "point_continuity"),
                    "How far to spread from the seed. Default none.",
                ),
            ),
            optional("complementary", flag("Take everything except the selection. Default false.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_boundary",
        summary=(
            "Extract the boundary curve of a surface or of one of its faces.\n"
            "The usual first step in filling a gap or building a blend: the boundary "
            "is associative, so the patch built on it follows when the surface changes."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to take the boundary of.")),
            optional(
                "propagation",
                one_of(("complete", "point_continuity", "tangent_continuity"), "How much of the boundary. Default complete."),
            ),
            optional("limit_from", vocab.element_reference("Start the boundary here.")),
            optional("limit_to", vocab.element_reference("End it here.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_extrapolate",
        summary=(
            "Extend a surface or curve past its edge, by a length or up to an element.\n"
            "The fix when a surface is very slightly too small to trim against its "
            "neighbour — extend it and trim, rather than rebuilding it larger."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("What to extend.")),
            required("boundary", vocab.element_reference("The edge or endpoint to extend from.")),
            optional("length_mm", length("How far to extend.")),
            optional("up_to", vocab.element_reference("Extend until it reaches this instead.")),
            optional(
                "continuity",
                one_of(("tangent", "curvature"), "How the extension continues the shape. Default tangent."),
            ),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_healing",
        summary=(
            "Close small gaps between surfaces that should be continuous.\n"
            "The standard first move on imported geometry, where surfaces that met "
            "exactly in the source CAD arrive here fractions of a millimetre apart and "
            "every downstream operation refuses them."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The surfaces to heal together.")),
            optional("merging_distance_mm", feature_length("Largest gap to close.")),
            optional("tangency_angle_deg", angle("Largest kink to smooth out.", maximum=90.0)),
            optional(
                "continuity",
                one_of(("point", "tangent"), "How smooth the result must be. Default point."),
            ),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_untrim",
        summary=(
            "Restore a trimmed surface to its full underlying extent.\n"
            "Useful when a surface was cut too small earlier and the original "
            "construction is no longer available to change."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to untrim.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_disassemble",
        summary=(
            "Break a multi-cell surface or curve into its separate pieces.\n"
            "The inverse of join. Needed when one element of a joined skin has to be "
            "treated differently from the rest."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("What to break up.")),
            optional(
                "mode",
                one_of(("all_cells", "domains"), "Break into every cell or into connected domains. Default domains."),
            ),
        ),
    ),
    # -- surface to solid ----------------------------------------------------
    Operation(
        name="catia_close_surface",
        summary=(
            "Turn a closed surface into solid material.\n"
            "This is the seam between shape design and part design. The surface must "
            "actually be closed — join everything first, and if it refuses, the gap "
            "catia_healing would close is the reason."
        ),
        tier=Tier.WRITE,
        workbench=Workbench.PART_DESIGN,
        params=(required("surface", vocab.element_reference("The closed surface to fill with material.")),),
    ),
    Operation(
        name="catia_thick_surface",
        summary=(
            "Give an open surface a thickness, turning it into a solid.\n"
            "The other route from skin to material, and the right one when the shape "
            "was designed as a shell — a panel, a moulded cover — rather than as a "
            "closed volume."
        ),
        tier=Tier.WRITE,
        workbench=Workbench.PART_DESIGN,
        params=(
            required("surface", vocab.element_reference("The surface to thicken.")),
            required("thickness_mm", thickness("Thickness to add on the first side.")),
            optional("second_thickness_mm", thickness("Thickness on the other side. Default 0.")),
            optional("reversed", flag("Swap which side is which. Default false.")),
        ),
    ),
    Operation(
        name="catia_sew_surface",
        summary=(
            "Sew a surface onto a solid, adding or removing material to match it.\n"
            "Used to impress a shaped face onto an otherwise prismatic part."
        ),
        tier=Tier.WRITE,
        workbench=Workbench.PART_DESIGN,
        params=(
            required("surface", vocab.element_reference("The surface to sew on.")),
            optional("remove", flag("Remove material rather than add it. Default false.")),
            optional("reversed", flag("Sew to the other side of the surface. Default false.")),
        ),
    ),
    # -- shape analysis ------------------------------------------------------
    Operation(
        name="catia_surface_analysis",
        summary=(
            "Analyse surface quality — curvature, draft, connection gaps or continuity "
            "between neighbours.\n"
            "Run `connect` before trusting a joined skin: it reports the real gap and "
            "angle between surfaces, which is what decides whether a downstream close "
            "or thicken will succeed. Run `draft` before committing to a moulding "
            "direction."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("curvature", "draft", "connect", "continuity", "reflection", "isophote"),
                    "Which analysis to run.",
                ),
            ),
            required("elements", name_list("The surfaces or curves to analyse.")),
            optional("direction", direction3("Pulling direction, for a draft analysis.")),
            optional("tolerance_mm", feature_length("Gap tolerance, for a connect analysis.")),
        ),
    ),
)
