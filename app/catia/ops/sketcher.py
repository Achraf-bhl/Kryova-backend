"""Sketcher: 2D profiles, the operations on them, and their constraints.

This is the module that lifts the oldest limit in the tool set. Every sketch
primitive used to be centred on the origin, because no parameter could carry a
position — `catia_sketch_circle(plane, diameter_mm)` had nowhere to put an x
and a y. Every primitive here takes an optional `at`, and the existing tools
keep their old behaviour when it is omitted, so nothing that worked before
changes and everything that was impossible becomes expressible.

The second limit lifted is the support. `sketch_create` puts a sketch on any
plane the model can name, including one it created a moment ago, which is what
makes offset and angled sketch planes reachable at all.

Constraints get their own two operations rather than one per constraint type:
CATIA's own API is `AddMonoEltCst` / `AddBiEltCst` / `AddTriEltCst` keyed by a
`catConstraintType`, so a single `catia_sketch_constrain` with a `kind`
parameter mirrors the real interface instead of inventing seventeen tools that
would all dispatch to the same call.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    angle,
    count,
    feature_length,
    flag,
    length,
    name_list,
    name_pair,
    new_name,
    one_of,
    optional,
    point2,
    point_list,
    required,
    signed_angle,
)

_WB = Workbench.SKETCHER


def _sketch_target() -> tuple:
    """The two ways to say which sketch an operation edits.

    Naming a sketch is exact; omitting it means "the one you just created",
    which is how a model writes a five-primitive profile without repeating the
    name five times. The daemon tracks the last opened sketch per document.
    """
    return (
        optional(
            "sketch",
            vocab.element_reference("The sketch to draw in. Defaults to the most recent sketch."),
        ),
        optional(
            "construction",
            flag(
                "Draw as a construction element — geometry that guides other geometry "
                "but is not part of the profile and is never padded. Default false."
            ),
        ),
    )


OPERATIONS: tuple[Operation, ...] = (
    # -- the sketch itself ---------------------------------------------------
    Operation(
        name="catia_sketch_create",
        summary=(
            "Open a new empty sketch on a plane or planar face, and leave it as the "
            "target for the drawing tools that follow.\n"
            "Use this instead of the single-shape tools whenever the profile is more "
            "than one primitive: create the sketch, draw into it, constrain it, then "
            "close it and pad it. `support` accepts 'XY'/'YZ'/'ZX', any plane you have "
            "created, or a named face — which is how a sketch reaches an offset or "
            "angled plane at all."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("support", vocab.support("The plane or planar face to sketch on.")),
            optional("name", new_name("A name for the sketch. CATIA numbers it if omitted.")),
            optional(
                "origin",
                point2(
                    "Where the sketch's own (0, 0) sits on the support. Defaults to the "
                    "support's own origin."
                ),
            ),
        ),
    ),
    Operation(
        name="catia_sketch_close",
        summary=(
            "Finish editing the current sketch and return to the 3D part.\n"
            "Call this before padding or pocketing the profile. Leaving a sketch open "
            "is the usual reason a following feature reports that it cannot find its "
            "profile."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(optional("sketch", vocab.element_reference("The sketch to close. Defaults to the open one.")),),
    ),
    Operation(
        name="catia_sketch_analysis",
        summary=(
            "Report whether the current sketch is a closed, non-self-intersecting "
            "profile, and how many degrees of freedom remain unconstrained.\n"
            "Run this before padding when the profile was drawn point by point: an "
            "open contour and a self-intersecting one both fail at the pad with an "
            "error that does not say which it was."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(optional("sketch", vocab.element_reference("The sketch to analyse.")),),
    ),
    # -- points and lines ----------------------------------------------------
    Operation(
        name="catia_sketch_point",
        summary=(
            "Place a point in the sketch, by coordinates.\n"
            "Points are what constraints and later curves attach to; a spline through "
            "named points is far easier to edit afterwards than one drawn freehand."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("at", point2("Where to put the point.")), *_sketch_target()),
    ),
    Operation(
        name="catia_sketch_line",
        summary=(
            "Draw a straight line between two points in the sketch.\n"
            "Chain these to build an open or closed contour; close the contour exactly "
            "on the start point or the pad will refuse it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("start", point2("Where the line begins.")),
            required("end", point2("Where the line ends.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_polyline",
        summary=(
            "Draw a connected run of straight lines through a list of points.\n"
            "This is the fast way to lay down a profile outline. Repeat the first "
            "point as the last to close it, or set `closed`."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", point_list("The vertices, in order.", minimum=2)),
            optional(
                "closed",
                flag("Join the last point back to the first. Default false."),
            ),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_axis",
        summary=(
            "Draw the sketch's revolution axis.\n"
            "A shaft or groove needs one and will not build without it. Draw the "
            "profile to one side of this line, never crossing it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("start", point2("One end of the axis.")),
            required("end", point2("The other end.")),
            optional("sketch", vocab.element_reference("The sketch to draw in.")),
        ),
    ),
    # -- arcs and circles ----------------------------------------------------
    Operation(
        name="catia_sketch_circle",
        summary=(
            "Draw a circle in the sketch.\n"
            "Omit `at` and the circle is centred on the sketch origin, which is the "
            "old behaviour and still the right one for a single-profile part. Give "
            "`at` to place it anywhere — that is how a bolt circle or an off-centre "
            "boss gets drawn."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("diameter_mm", length("Diameter of the circle.")),
            optional("at", point2("Centre of the circle. Defaults to the sketch origin.")),
            optional("plane", vocab.support("Support to sketch on when no sketch is open.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_arc",
        summary=(
            "Draw a circular arc from a centre, a radius and a start and end angle.\n"
            "Angles are measured anticlockwise from the sketch's horizontal axis. For "
            "an arc through three known points use catia_sketch_arc_three_point."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("centre", point2("Centre of the arc.")),
            required("radius_mm", feature_length("Radius of the arc.")),
            required("start_angle_deg", signed_angle("Angle at which the arc starts.")),
            required("end_angle_deg", signed_angle("Angle at which the arc ends.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_arc_three_point",
        summary=(
            "Draw a circular arc through three points: start, a point on the arc, end.\n"
            "Use this when the arc is defined by where it must pass rather than by a "
            "centre you would have to solve for."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("start", point2("Where the arc begins.")),
            required("through", point2("A point the arc passes through.")),
            required("end", point2("Where the arc ends.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_ellipse",
        summary=(
            "Draw an ellipse from its centre and its two semi-axes.\n"
            "`rotation_deg` turns the major axis away from the sketch horizontal."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("centre", point2("Centre of the ellipse.")),
            required("major_radius_mm", length("Semi-major axis length.")),
            required("minor_radius_mm", length("Semi-minor axis length.")),
            optional("rotation_deg", signed_angle("Rotation of the major axis. Default 0.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_spline",
        summary=(
            "Draw a smooth spline through a list of points, in order.\n"
            "Prefer this to a many-segment polyline whenever the shape is meant to be "
            "smooth: a spline stays smooth when its points are later moved, a polyline "
            "does not."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", point_list("Points the spline passes through, in order.", minimum=2)),
            optional("closed", flag("Close the spline into a loop. Default false.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_conic",
        summary=(
            "Draw a conic — parabola, hyperbola or ellipse arc — from two endpoints, "
            "their tangent intersection, and a shape parameter.\n"
            "`parameter` below 0.5 gives an ellipse arc, exactly 0.5 a parabola, above "
            "0.5 a hyperbola. This is the primitive behind most aerofoil and fairing "
            "profiles."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("start", point2("One endpoint.")),
            required("end", point2("The other endpoint.")),
            required("tangent_intersection", point2("Where the two end tangents cross.")),
            optional(
                "parameter",
                {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 0.999,
                    "description": (
                        "Conic shape parameter: <0.5 ellipse, 0.5 parabola, >0.5 "
                        "hyperbola. Default 0.5."
                    ),
                },
            ),
            *_sketch_target(),
        ),
    ),
    # -- closed profiles -----------------------------------------------------
    Operation(
        name="catia_sketch_rectangle",
        summary=(
            "Draw a rectangle in the sketch.\n"
            "Omit `at` and it is centred on the sketch origin — the old behaviour. "
            "Give `at` to place it, and `rotation_deg` to turn it, which together "
            "cover CATIA's Centered Rectangle and Oriented Rectangle."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("width_mm", length("Width, along the sketch's horizontal axis.")),
            required("height_mm", length("Height, along the sketch's vertical axis.")),
            optional("at", point2("Centre of the rectangle. Defaults to the sketch origin.")),
            optional("rotation_deg", signed_angle("Rotation about the centre. Default 0.")),
            optional("plane", vocab.support("Support to sketch on when no sketch is open.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_parallelogram",
        summary=(
            "Draw a parallelogram from one corner, two edge vectors' lengths and the "
            "angle between them.\n"
            "Use it for a skewed rib or a lozenge cutout, where a rotated rectangle "
            "would still have square corners."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("corner", point2("The corner the two sides run from.")),
            required("width_mm", length("Length of the first side.")),
            required("height_mm", length("Length of the second side.")),
            required("angle_deg", angle("Angle between the two sides.", maximum=179.0)),
            optional("rotation_deg", signed_angle("Rotation of the first side. Default 0.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_polygon",
        summary=(
            "Draw a regular polygon — hexagon, octagon, triangle — inscribed in a circle.\n"
            "`diameter_mm` is across the circle the corners sit on. For a hex bar "
            "specified across the flats, divide by cos(180/sides) first."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sides", count("Number of sides.", minimum=3, maximum=64)),
            required("diameter_mm", length("Diameter of the circle the corners sit on.")),
            optional("at", point2("Centre of the polygon. Defaults to the sketch origin.")),
            optional("rotation_deg", signed_angle("Rotation about the centre. Default 0.")),
            optional("plane", vocab.support("Support to sketch on when no sketch is open.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_slot",
        summary=(
            "Draw an elongated hole — two parallel lines closed by a semicircle at "
            "each end — from its two centre points and a width.\n"
            "This is the shape of every adjustment slot and cable cutout, and drawing "
            "it as four constrained primitives by hand is where profiles usually go "
            "wrong."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("start", point2("Centre of the first end.")),
            required("end", point2("Centre of the second end.")),
            required("width_mm", length("Width across the slot — the diameter of its ends.")),
            *_sketch_target(),
        ),
    ),
    # -- operations on existing sketch geometry ------------------------------
    Operation(
        name="catia_sketch_corner",
        summary=(
            "Round the corner between two sketch elements — a 2D fillet.\n"
            "Rounding in the sketch rather than with a 3D edge fillet keeps the radius "
            "in the profile, so it survives a change to the pad and shows on the "
            "drawing as a profile dimension."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("radius_mm", feature_length("Radius of the rounded corner.")),
            required("elements", name_pair("The two sketch elements meeting at the corner.")),
            optional("trim", flag("Trim both elements back to the arc. Default true.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_chamfer",
        summary=(
            "Cut the corner between two sketch elements at an angle — a 2D chamfer.\n"
            "Give a length and an angle, or two lengths, depending on how the part is "
            "dimensioned on its drawing."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("length_mm", feature_length("Length of the chamfer along the first element.")),
            required("elements", name_pair("The two sketch elements meeting at the corner.")),
            optional("angle_deg", angle("Angle of the chamfer. Default 45.", maximum=179.0)),
            optional(
                "second_length_mm",
                feature_length("Length along the second element, instead of an angle."),
            ),
            optional("trim", flag("Trim both elements back to the chamfer. Default true.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_trim",
        summary=(
            "Trim or extend sketch elements to their intersection.\n"
            "This is how an over-drawn contour becomes a closed one. Trimming is the "
            "usual fix when catia_sketch_analysis reports an open profile."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_pair("The two elements to trim to each other.")),
            optional(
                "keep",
                one_of(
                    ("both", "first", "second"),
                    "Which side of the intersection survives. Default both.",
                ),
            ),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_offset",
        summary=(
            "Offset sketch elements by a distance, creating a parallel copy.\n"
            "Negative offsets go the other way. Use it to derive an inner wall from an "
            "outer profile without redrawing it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The sketch elements to offset.")),
            required("distance_mm", feature_length("How far to offset.")),
            optional("reversed", flag("Offset to the other side. Default false.")),
            optional("propagate", flag("Carry the offset along tangent neighbours. Default true.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_mirror",
        summary=(
            "Mirror sketch elements about a line in the same sketch.\n"
            "Draw half a symmetric profile, mirror it, and the two halves stay "
            "symmetric when either is edited — which is not true of two halves drawn "
            "separately."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The elements to mirror.")),
            required("axis", vocab.element_reference("The line or axis to mirror about.")),
            optional("keep_original", flag("Keep the original as well. Default true.")),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_translate",
        summary=(
            "Move or copy sketch elements by a 2D offset.\n"
            "Set `copies` above zero to leave the original in place and repeat it, "
            "which is how a row of identical slots is drawn without patterning."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The elements to move.")),
            required("offset", point2("How far to move them, as a 2D vector.")),
            optional("copies", count("Number of copies to leave behind. Default 0 (move).", minimum=0)),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_rotate",
        summary=(
            "Rotate or rotationally copy sketch elements about a point.\n"
            "With `copies` set this is the 2D equivalent of a circular pattern, and "
            "produces one profile rather than many features."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The elements to rotate.")),
            required("centre", point2("The point to rotate about.")),
            required("angle_deg", signed_angle("How far to rotate.")),
            optional("copies", count("Number of copies to leave behind. Default 0 (move).", minimum=0)),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_scale",
        summary=(
            "Scale sketch elements about a point.\n"
            "Scaling a profile is not the same as scaling the solid built from it: "
            "this keeps the feature's depth unchanged, which is usually what a "
            "shrinkage or clearance adjustment wants."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The elements to scale.")),
            required("centre", point2("The point to scale about.")),
            required("factor", {"type": "number", "exclusiveMinimum": 0, "maximum": 100.0,
                                "description": "Scale factor; 1.0 leaves the size unchanged."}),
            *_sketch_target(),
        ),
    ),
    Operation(
        name="catia_sketch_project",
        summary=(
            "Project existing 3D geometry — an edge, a face boundary — down into the "
            "current sketch.\n"
            "The projection stays associative: when the 3D edge moves, the sketch "
            "curve follows. This is how a mating profile is guaranteed to keep "
            "matching rather than merely starting out matched."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The 3D edges or faces to project.")),
            optional(
                "mode",
                one_of(
                    ("normal", "along_direction", "silhouette"),
                    "How to project. Default normal (straight onto the sketch plane).",
                ),
            ),
            optional("construction", flag("Bring it in as construction geometry. Default false.")),
            *_sketch_target()[:1],
        ),
    ),
    Operation(
        name="catia_sketch_intersect_3d",
        summary=(
            "Add the curve where 3D geometry crosses the sketch plane.\n"
            "Use it to pick up the exact section of a surface or solid the sketch cuts "
            "through, instead of measuring it and redrawing."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The 3D elements to intersect with the plane.")),
            optional("construction", flag("Bring it in as construction geometry. Default false.")),
            *_sketch_target()[:1],
        ),
    ),
    Operation(
        name="catia_sketch_pattern",
        summary=(
            "Repeat sketch elements in a rectangular or circular grid, inside the sketch.\n"
            "Patterning in the sketch produces one feature from many shapes; patterning "
            "the 3D feature afterwards produces many features. The first rebuilds "
            "faster and is usually what you want for a hole array."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("The elements to repeat.")),
            required("kind", one_of(("rectangular", "circular"), "Grid shape.")),
            required("count", count("How many instances along the first direction.", minimum=2)),
            optional("spacing_mm", length("Gap between instances, for a rectangular grid.")),
            optional("second_count", count("Instances along the second direction. Default 1.")),
            optional("second_spacing_mm", length("Gap along the second direction.")),
            optional("radius_mm", length("Radius of the circle, for a circular grid.")),
            optional("total_angle_deg", angle("Angle the circular grid spans. Default 360.")),
            *_sketch_target(),
        ),
    ),
    # -- constraints ---------------------------------------------------------
    Operation(
        name="catia_sketch_constrain",
        summary=(
            "Apply a geometric constraint between sketch elements — coincidence, "
            "tangency, parallelism, perpendicularity, symmetry, horizontal, vertical, "
            "concentricity, fix.\n"
            "Constraints are what make a sketch survive editing. An unconstrained "
            "profile changes shape unpredictably when any dimension is altered; run "
            "catia_sketch_analysis to see how many degrees of freedom are left."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(vocab.GEOMETRIC_CONSTRAINTS, "Which constraint to apply."),
            ),
            required(
                "elements",
                name_list(
                    "The elements to constrain: one for horizontal/vertical/fix, two for "
                    "most others, three for symmetry (the two elements then the axis)."
                ),
            ),
            *_sketch_target()[:1],
        ),
    ),
    Operation(
        name="catia_sketch_dimension",
        summary=(
            "Apply a dimensional constraint — a distance, length, radius, diameter or "
            "angle — and drive it to a value.\n"
            "Give `parameter_name` to publish the dimension as a named parameter, "
            "which is what lets catia_set_parameter drive it afterwards and what a "
            "design table binds to."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("kind", one_of(vocab.DIMENSIONAL_CONSTRAINTS, "Which dimension to apply.")),
            required("elements", name_list("The one or two elements being dimensioned.")),
            required(
                "value",
                {
                    "type": "number",
                    "minimum": -10_000.0,
                    "maximum": 10_000.0,
                    "description": (
                        "The value to drive it to — millimetres for a length, distance, "
                        "radius or diameter; degrees for an angle."
                    ),
                },
            ),
            optional(
                "parameter_name",
                new_name("Publish the dimension under this name so it can be driven later."),
            ),
            optional("reference", flag("Create it as a reference (driven) dimension. Default false.")),
            *_sketch_target()[:1],
        ),
    ),
)



# -- composite profile helpers ----------------------------------------------
#
# These three draw a whole constrained profile in one call. They are not
# redundant against the primitives above: a revolve profile is four lines whose
# relationship to the axis decides whether the shaft comes out solid or hollow,
# and a model that draws it line by line gets it wrong often enough to be worth
# a dedicated tool. The gear profile is an involute, which cannot be drawn from
# primitives at all without the maths that `scripts/catia_bridge/gear.py` holds.

PROFILE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_sketch_revolve_profile",
        summary=(
            "Draw the complete profile for a shaft or tube — the rectangle and the "
            "revolution axis — ready to be revolved by catia_shaft.\n"
            "Give an inner diameter for a tube and omit it for a solid rod. The profile "
            "is placed correctly relative to the axis, which is the part that goes "
            "wrong when it is drawn line by line."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "plane",
                vocab.origin_plane(
                    "Plane to draw on. The part is revolved about this plane's vertical "
                    "axis and grows along it — ZX gives a shaft lying along Z."
                ),
            ),
            required("outer_diameter_mm", length("Outside diameter of the finished part.")),
            required("length_mm", length("Length along the revolution axis.")),
            optional("inner_diameter_mm", length("Bore diameter, for a tube. Omit for a solid rod.")),
        ),
    ),
    Operation(
        name="catia_sketch_groove_profile",
        summary=(
            "Draw the complete profile for a groove cut into a shaft, ready to be "
            "revolved away by catia_groove.\n"
            "Use the same plane the shaft's own profile used, or the groove will be cut "
            "on a different axis from the shaft it belongs to."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("plane", vocab.origin_plane("Plane to draw on. Use the same one the shaft used.")),
            required("shaft_diameter_mm", length("Outside diameter of the shaft being cut into.")),
            required("width_mm", length("Width of the groove along the axis.")),
            required("depth_mm", length("How deep the groove cuts into the surface.")),
            required(
                "distance_from_end_mm",
                {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 10_000.0,
                    "description": "Distance from the shaft's near end to the groove. Millimetres.",
                },
            ),
        ),
    ),
    Operation(
        name="catia_sketch_gear_profile",
        summary=(
            "Draw a full involute spur-gear tooth profile, ready to be padded.\n"
            "The involute is generated from the module, tooth count and pressure angle "
            "— it is not an approximation by arcs, and it cannot be built from the "
            "primitives above. Module and tooth count set the pitch diameter "
            "(module × teeth), so check that against the space available before padding."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("plane", vocab.origin_plane("Plane to draw the gear on.")),
            required(
                "module_mm",
                {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 50.0,
                    "description": "Gear module — pitch diameter divided by tooth count. Millimetres.",
                },
            ),
            required("teeth", count("Number of teeth.", minimum=6, maximum=100)),
            optional(
                "pressure_angle_deg",
                {
                    "type": "number",
                    "minimum": 10.0,
                    "maximum": 30.0,
                    "description": "Pressure angle. 20 degrees is the modern standard. Degrees.",
                },
            ),
        ),
    ),
)

OPERATIONS = OPERATIONS + PROFILE_OPERATIONS
