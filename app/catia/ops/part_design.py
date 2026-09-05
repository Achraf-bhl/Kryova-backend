"""Part Design: solid features, dress-up, bodies, booleans and transformations.

Where the old tool set had pad, pocket, shaft, groove, hole, fillet, chamfer,
shell and two patterns, this covers the whole `ShapeFactory` surface: the
sketch-based features it was missing (rib, slot, stiffener, multi-section
solid, drafted-filleted pad), the dress-up it was missing (draft, thickness,
thread, the fillet variants), and the two categories it had nothing for at all
— bodies with boolean operations between them, and the transformation features.

Three limits are lifted here specifically:

* **Holes get coordinates.** `catia_hole` kept its five named positions because
  they read well; `catia_hole_at` takes a point, and `catia_hole_pattern` takes
  a list, which is what a real bolt circle needs.
* **Fillets get edges.** `catia_fillet` still accepts its edge keywords;
  `catia_fillet_edges` takes edge ids from `catia_list_edges` and a per-edge
  radius.
* **Shells get faces.** `catia_shell` had a thickness and nothing else, so it
  could only hollow a part with no opening. It now takes the faces to remove.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    angle,
    count,
    direction3,
    distance,
    feature_length,
    feature_length_per_entity,
    flag,
    length,
    name_list,
    new_name,
    one_of,
    optional,
    point3,
    ratio,
    required,
    signed_angle,
    text,
    thickness,
    tilt,
)

_WB = Workbench.PART_DESIGN


def _limit_params() -> tuple:
    """How a pad, pocket or extrude decides where to stop.

    A `dimension` limit is a number; every other kind is a piece of geometry to
    stop against, which is what makes a feature survive a change to the part it
    is cut into. The old tools had only the number.
    """
    return (
        optional(
            "limit",
            one_of(vocab.LIMIT_TYPES, "How the feature ends. Default dimension."),
        ),
        optional(
            "up_to",
            vocab.element_reference("The plane, face or surface to stop at, for an up_to limit."),
        ),
        optional(
            "second_length_mm",
            length("Extent in the opposite direction, for a two-sided feature."),
        ),
        optional("reversed", flag("Build in the opposite direction. Default false.")),
        optional("symmetric", flag("Extend equally both ways from the profile. Default false.")),
        optional(
            "direction",
            direction3("Build along this direction instead of the profile's normal."),
        ),
    )


OPERATIONS: tuple[Operation, ...] = (
    # -- the original sketch-based features ----------------------------------
    #
    # These four keep the exact schema they shipped with; everything added is
    # optional, so a call that worked before works unchanged and means the same
    # thing. What they gain is `_limit_params`: a pad that stops at a face
    # instead of at a number is the difference between a feature that survives
    # the part changing around it and one that has to be re-dimensioned by hand.
    Operation(
        name="catia_pad",
        summary=(
            "Extrude a closed sketch profile into solid material.\n"
            "The commonest way to add material. By default it runs a fixed distance "
            "from the sketch; give `limit: 'up_to_next'` or `'up_to_surface'` with "
            "`up_to` to make it stop against real geometry instead, which keeps it "
            "correct when that geometry moves."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sketch", vocab.element_reference("The closed profile to extrude.")),
            required("length_mm", length("How far to extrude.")),
            optional("thin", flag("Build a thin-walled pad instead of a solid one. Default false.")),
            optional("thickness_mm", thickness("Wall thickness, for a thin pad.")),
            *_limit_params(),
        ),
    ),
    Operation(
        name="catia_pocket",
        summary=(
            "Cut a closed sketch profile down into the material.\n"
            "The subtractive twin of a pad. Set `through_all` to cut clean through "
            "whatever depth the part happens to be, which stays right when the part "
            "gets thicker."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sketch", vocab.element_reference("The closed profile to cut.")),
            optional("depth_mm", length("How deep to cut. Omit with through_all.")),
            optional("through_all", flag("Cut all the way through the part. Default false.")),
            optional("thin", flag("Cut a thin-walled slot instead of the full profile. Default false.")),
            optional("thickness_mm", thickness("Wall thickness, for a thin pocket.")),
            *_limit_params(),
        ),
    ),
    Operation(
        name="catia_shaft",
        summary=(
            "Revolve a closed profile about the sketch's axis to make a solid.\n"
            "How every turned part is modelled — a shaft, a pulley, a boss. The sketch "
            "needs an axis (catia_sketch_axis) and the profile must not cross it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sketch", vocab.element_reference("The closed profile to revolve.")),
            optional("angle_deg", angle("How far to revolve. Default 360 (a full turn).")),
            optional("second_angle_deg", angle("Sweep in the opposite direction as well.")),
            optional("axis", vocab.element_reference("Revolve about this axis instead of the sketch's own.")),
            optional("thin", flag("Build it thin-walled. Default false.")),
            optional("thickness_mm", thickness("Wall thickness, for a thin shaft.")),
        ),
    ),
    Operation(
        name="catia_groove",
        summary=(
            "Revolve a closed profile about the sketch's axis and remove it.\n"
            "The subtractive twin of a shaft: an O-ring groove, a circlip retainer, a "
            "relief undercut."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sketch", vocab.element_reference("The closed profile to revolve and remove.")),
            optional("angle_deg", angle("How far to revolve. Default 360 (a full turn).")),
            optional("second_angle_deg", angle("Sweep in the opposite direction as well.")),
            optional("axis", vocab.element_reference("Revolve about this axis instead of the sketch's own.")),
        ),
    ),
    Operation(
        name="catia_hole",
        summary=(
            "Drill a hole at one of five named spots on a named face.\n"
            "Quick and readable when the position is 'in the middle' or 'in each "
            "corner'. When the position actually matters — a bolt circle, a "
            "dimensioned pattern, anything on a drawing — use catia_hole_at, which "
            "takes real coordinates."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", one_of(vocab.NAMED_FACES, "Which face of the part's bounding box.")),
            required("position", one_of(vocab.FACE_POSITIONS, "Where on that face.")),
            required("diameter_mm", feature_length("Diameter of the hole.")),
            optional("depth_mm", length("How deep. Omit with through_all.")),
            optional("through_all", flag("Drill all the way through. Default true.")),
            optional("inset_mm", length("How far in from the face's edges the corner positions sit.")),
        ),
    ),
    Operation(
        name="catia_fillet",
        summary=(
            "Round edges of the part by a group name — all of them, or the vertical, "
            "horizontal, top or bottom ones.\n"
            "Convenient when the intent really is 'break all the sharp edges' — and "
            "radius_mm may be a list, one per selected edge in selection order, which is "
            "how 'the four vertical corners at 2, 3, 4 and 5 mm' is said against a "
            "predicate. Use catia_fillet_edges instead when the edges have to be named "
            "individually by id."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("radius_mm", feature_length_per_entity("Radius to round to.")),
            optional("feature", vocab.element_reference("Restrict to edges of this feature.")),
            optional("edges", one_of(vocab.EDGE_SELECTORS, "Which group of edges. Default all.")),
            optional(
                "propagation",
                one_of(vocab.PROPAGATION, "How the fillet carries onto neighbours. Default tangency."),
            ),
        ),
    ),
    Operation(
        name="catia_chamfer",
        summary=(
            "Bevel edges of the part by a group name.\n"
            "Same selection vocabulary as catia_fillet, list of per-edge sizes included. "
            "A chamfer is usually the right edge break on a machined part where a fillet "
            "would need a form tool."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("length_mm", feature_length_per_entity("Length of the bevel.")),
            optional("angle_deg", tilt("Angle of the bevel. Default 45.")),
            optional("feature", vocab.element_reference("Restrict to edges of this feature.")),
            optional("edges", one_of(vocab.EDGE_SELECTORS, "Which group of edges. Default all.")),
            optional(
                "second_length_mm",
                feature_length("Length on the second face, instead of giving an angle."),
            ),
        ),
    ),
    Operation(
        name="catia_shell",
        summary=(
            "Hollow the part out to a uniform wall thickness, leaving it sealed.\n"
            "Note the 'sealed': with no face removed the result has no opening. To "
            "leave it open — which is nearly always what is wanted — use "
            "catia_shell_faces and name the faces to remove."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("thickness_mm", thickness("Wall thickness.")),
            optional("outward", flag("Add the wall outside the surface instead of inside. Default false.")),
        ),
    ),
    Operation(
        name="catia_mirror",
        summary=(
            "Mirror the whole body about a plane, keeping the original.\n"
            "Model half a symmetric part and mirror it: the two halves then stay "
            "identical through every later edit."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("plane", vocab.support("The plane to mirror about.")),
            optional("feature", vocab.element_reference("Mirror only this feature, not the whole body.")),
        ),
    ),
    Operation(
        name="catia_pattern_rectangular",
        summary=(
            "Repeat a feature in a rectangular grid.\n"
            "Both directions come from the named plane — its first in-plane axis, then "
            "its second — so naming the plane names the directions."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("plane", vocab.origin_plane("The plane whose two axes the grid runs along.")),
            required("count", count("How many instances along the first direction.", minimum=2)),
            required("spacing_mm", length("Gap between instances along the first direction.")),
            optional("second_count", count("Instances along the second direction. Default 1.")),
            optional("second_spacing_mm", length("Gap along the second direction.")),
            optional("feature", vocab.element_reference("The feature to repeat. Defaults to the last one.")),
            optional("reversed", flag("Run the grid the other way. Default false.")),
        ),
    ),
    Operation(
        name="catia_pattern_circular",
        summary=(
            "Repeat a feature evenly around a circle.\n"
            "The tool for a bolt circle, a spline pattern or a set of cooling slots. "
            "Instances are spread over `total_angle_deg`, which defaults to a full turn."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("count", count("How many instances in total.", minimum=2)),
            optional("plane", vocab.origin_plane("The plane the circle lies in. Default XY.")),
            optional("total_angle_deg", angle("Angle the instances spread over. Default 360.")),
            optional("feature", vocab.element_reference("The feature to repeat. Defaults to the last one.")),
            optional("axis", vocab.element_reference("Rotate about this axis instead of the plane's normal.")),
            optional("radius_mm", length("Radius of the circle, when it is not taken from the feature.")),
        ),
    ),
    # -- sketch-based features ----------------------------------------------
    Operation(
        name="catia_rib",
        summary=(
            "Sweep a closed profile along a guide curve to make a solid — CATIA's Rib.\n"
            "This is how a handle, a pipe run or a swept boss is modelled. The profile "
            "should sit on a plane normal to the start of the centre curve; "
            "catia_plane_normal_to_curve makes that plane."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("profile", vocab.element_reference("The closed profile to sweep.")),
            required("centre_curve", vocab.element_reference("The path to sweep it along.")),
            optional(
                "control",
                one_of(
                    ("keep_angle", "pulling_direction", "reference_surface"),
                    "How the profile is oriented as it travels. Default keep_angle.",
                ),
            ),
            optional("reference", vocab.element_reference("The surface or direction the control uses.")),
            optional("thick", flag("Build it as a thin-walled sweep. Default false.")),
        ),
    ),
    Operation(
        name="catia_slot",
        summary=(
            "Sweep a closed profile along a guide curve and remove it — the Rib's "
            "subtractive twin.\n"
            "Use it for a swept channel, an O-ring groove that follows a curve, or a "
            "cable route cut into a housing."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("profile", vocab.element_reference("The closed profile to sweep.")),
            required("centre_curve", vocab.element_reference("The path to sweep it along.")),
            optional(
                "control",
                one_of(
                    ("keep_angle", "pulling_direction", "reference_surface"),
                    "How the profile is oriented as it travels. Default keep_angle.",
                ),
            ),
            optional("reference", vocab.element_reference("The surface or direction the control uses.")),
        ),
    ),
    Operation(
        name="catia_stiffener",
        summary=(
            "Thicken an open profile into a rib that runs until it meets the "
            "surrounding material — CATIA's Stiffener.\n"
            "The profile is a single open line, not a closed shape: the stiffener "
            "finds its own boundaries against the part, which is why it stays correct "
            "when the walls it braces move."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("profile", vocab.element_reference("The open profile line.")),
            required("thickness_mm", thickness("Thickness of the stiffener.")),
            optional("symmetric", flag("Thicken equally both sides of the profile. Default true.")),
            optional("reversed", flag("Build on the other side. Default false.")),
        ),
    ),
    Operation(
        name="catia_multi_section_solid",
        summary=(
            "Loft a solid through a series of closed profiles — CATIA's Multi-sections "
            "Solid.\n"
            "Give the sections in order along the shape. Guide curves control how the "
            "surface runs between them; without guides the loft takes the shortest "
            "path and can twist between sections that are rotated relative to each "
            "other."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sections", name_list("The closed profiles to loft through, in order.", minimum=2)),
            optional("guides", name_list("Curves that steer the surface between sections.")),
            optional("spine", vocab.element_reference("A curve the sections stay normal to.")),
            optional("closed", flag("Close the loft back onto its first section. Default false.")),
            optional("remove", flag("Remove the lofted volume instead of adding it. Default false.")),
        ),
    ),
    Operation(
        name="catia_solid_combine",
        summary=(
            "Make a solid from the common volume of two profiles extruded in different "
            "directions — CATIA's Solid Combine.\n"
            "The classic use is a cam or a bracket defined by its two orthogonal "
            "silhouettes, where neither view alone describes the shape."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first_profile", vocab.element_reference("The first closed profile.")),
            required("second_profile", vocab.element_reference("The second closed profile.")),
            optional("first_direction", direction3("Extrusion direction of the first profile.")),
            optional("second_direction", direction3("Extrusion direction of the second profile.")),
        ),
    ),
    Operation(
        name="catia_pad_drafted_filleted",
        summary=(
            "Pad a profile while drafting its sides and rounding its edges in one "
            "feature — CATIA's Drafted Filleted Pad.\n"
            "Modelling a moulded boss this way is far more robust than a pad followed "
            "by a separate draft and two fillets, because the fillets are recomputed "
            "against the drafted face rather than against a vertical one that no "
            "longer exists."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("sketch", vocab.element_reference("The profile to pad.")),
            required("length_mm", length("How far to pad.")),
            required("draft_angle_deg", tilt("Draft angle on the sides.")),
            optional("neutral", vocab.support("The plane the draft pivots about.")),
            optional("lateral_radius_mm", feature_length("Radius on the vertical edges.")),
            optional("top_radius_mm", feature_length("Radius where the pad meets its top face.")),
            optional("bottom_radius_mm", feature_length("Radius where the pad meets the part.")),
        ),
    ),
    # -- holes ---------------------------------------------------------------
    Operation(
        name="catia_hole_at",
        summary=(
            "Drill a hole at an exact point on a face, with a real hole type — simple, "
            "tapered, counterbored, countersunk, counterdrilled or threaded.\n"
            "Prefer this to catia_hole whenever the position matters: catia_hole's five "
            "named spots cannot express a bolt circle or a dimensioned hole pattern. "
            "For a threaded hole give `thread` and the standard, and CATIA adds the "
            "correct tap drill itself."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", vocab.face_reference("The face to drill into.")),
            required("at", point3("Where the hole centre sits.")),
            required("diameter_mm", feature_length("Diameter of the hole.")),
            optional("depth_mm", length("How deep. Omit with through_all.")),
            optional("through_all", flag("Drill all the way through. Default false.")),
            optional(
                "kind",
                one_of(
                    ("simple", "tapered", "counterbored", "countersunk", "counterdrilled"),
                    "Hole type. Default simple.",
                ),
            ),
            optional("head_diameter_mm", feature_length("Diameter of the counterbore or countersink.")),
            optional("head_depth_mm", length("Depth of the counterbore.")),
            optional("head_angle_deg", angle("Included angle of the countersink.", maximum=179.0)),
            optional("bottom_angle_deg", angle("Included angle of the drill point.", maximum=179.0)),
            optional(
                "thread",
                text("Thread designation, e.g. 'M6x1' or 'ISO metric M8'."),
            ),
            optional("thread_depth_mm", length("How far the thread runs down the hole.")),
        ),
    ),
    Operation(
        name="catia_hole_pattern",
        summary=(
            "Drill several identical holes at a list of points on one face, in a single "
            "feature.\n"
            "One feature rather than n features: it rebuilds faster, appears once in "
            "the tree, and can be edited as a set. This is the right tool for a bolt "
            "circle or a mounting-plate array whose spacing is not a regular grid."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", vocab.face_reference("The face to drill into.")),
            required(
                "points",
                {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": point3("One hole centre."),
                    "description": "Where each hole goes.",
                },
            ),
            required("diameter_mm", feature_length("Diameter of every hole.")),
            optional("depth_mm", length("How deep. Omit with through_all.")),
            optional("through_all", flag("Drill all the way through. Default true.")),
            optional("thread", text("Thread designation to tap every hole to.")),
        ),
    ),
    Operation(
        name="catia_thread",
        summary=(
            "Add a thread or tap to an existing cylindrical face.\n"
            "A thread in CATIA is an annotation on the geometry, not modelled helical "
            "material: it drives the drawing callout and downstream manufacturing, and "
            "deliberately does not change the mass. Model the helix only if it has to "
            "be printed or rendered."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("face", vocab.face_reference("The cylindrical face to thread.")),
            required("designation", text("Thread designation, e.g. 'M10x1.5'.")),
            optional("depth_mm", length("How far along the face the thread runs.")),
            optional("pitch_mm", feature_length("Pitch, when the designation does not imply one.")),
            optional("left_handed", flag("Cut it left-handed. Default false.")),
            optional("tap", flag("It is an internal tap rather than an external thread. Default true.")),
        ),
    ),
    # -- dress-up ------------------------------------------------------------
    Operation(
        name="catia_fillet_edges",
        summary=(
            "Round specific edges, each at its own radius.\n"
            "Use catia_list_edges first to get the ids. This is the tool that replaces "
            "guessing with edge keywords: 'the four vertical edges at 5 mm and the top "
            "rim at 1 mm' is one call here and was not expressible before.\n"
            "Order matters — apply large radii before small ones, or the small fillet "
            "lands on an edge the large one has already consumed."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "edges",
                {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "edge": vocab.edge_reference("Which edge."),
                            "radius_mm": feature_length("Radius for this edge."),
                        },
                        "required": ["edge", "radius_mm"],
                        "additionalProperties": False,
                    },
                    "description": "Each edge and the radius to round it to.",
                },
            ),
            optional(
                "propagation",
                one_of(vocab.PROPAGATION, "How the fillet carries onto neighbours. Default tangency."),
            ),
            optional("edge_relimitation", flag("Trim the fillet back to the edge ends. Default false.")),
        ),
    ),
    Operation(
        name="catia_fillet_variable",
        summary=(
            "Round an edge with a radius that changes along its length.\n"
            "Give the radius at two or more points on the edge; CATIA blends between "
            "them. This is how a fairing or a blended fillet transition is modelled."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("edge", vocab.edge_reference("The edge to round.")),
            required(
                "radii",
                {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "at_ratio": ratio("Where along the edge, 0 at the start and 1 at the end."),
                            "radius_mm": feature_length("Radius at that point."),
                        },
                        "required": ["at_ratio", "radius_mm"],
                        "additionalProperties": False,
                    },
                    "description": "The radius at each point along the edge.",
                },
            ),
            optional(
                "variation",
                one_of(("cubic", "linear"), "How the radius blends between points. Default cubic."),
            ),
        ),
    ),
    Operation(
        name="catia_fillet_face",
        summary=(
            "Round between two faces that need not share an edge — a face-face fillet.\n"
            "This is the one that works where an edge fillet fails, because it does not "
            "need a common edge to exist: it is how a bulge is blended into a wall."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first_face", vocab.face_reference("One face.")),
            required("second_face", vocab.face_reference("The other face.")),
            required("radius_mm", feature_length("Radius of the blend.")),
            optional("hold_curve", vocab.element_reference("A curve the fillet must pass through.")),
        ),
    ),
    Operation(
        name="catia_fillet_tritangent",
        summary=(
            "Round between three faces, removing the middle one — a tritangent fillet.\n"
            "The radius is found rather than given: the fillet is the one that touches "
            "all three faces. This is how the end of a rib is rounded off."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("faces", name_list("Exactly three face names.", minimum=3)),
            required("removed_face", vocab.face_reference("Which of the three is consumed.")),
        ),
    ),
    Operation(
        name="catia_draft",
        summary=(
            "Taper faces away from a pulling direction so the part can leave a mould.\n"
            "`neutral` is the plane that keeps its size — everything above it grows and "
            "everything below shrinks, or the other way round. Getting the neutral "
            "element wrong is the usual reason a drafted part no longer fits its mating "
            "face."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("faces", name_list("The faces to draft.")),
            required("angle_deg", tilt("Draft angle.")),
            required("neutral", vocab.support("The plane or face that keeps its dimensions.")),
            optional("pulling_direction", direction3("Mould opening direction. Defaults to the neutral normal.")),
            optional("parting", vocab.support("A parting element that splits the draft.")),
            optional(
                "mode",
                one_of(
                    ("standard", "reflect_line", "variable"),
                    "Draft mode. Default standard.",
                ),
            ),
        ),
    ),
    Operation(
        name="catia_shell_faces",
        summary=(
            "Hollow the part to a wall thickness, removing the named faces to leave it "
            "open.\n"
            "This is what catia_shell could not do: without a face to remove, a shell "
            "produces a sealed hollow with no way in. Give different thicknesses per "
            "face where a wall needs to be locally thicker."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("thickness_mm", thickness("Default wall thickness.")),
            optional("open_faces", name_list("Faces to remove, leaving the part open there.")),
            optional("outward", flag("Add the wall outside the surface instead of inside. Default false.")),
            optional(
                "face_thicknesses",
                {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "face": vocab.face_reference("Which face."),
                            "thickness_mm": thickness("Thickness for this face."),
                        },
                        "required": ["face", "thickness_mm"],
                        "additionalProperties": False,
                    },
                    "description": "Per-face thickness overrides.",
                },
            ),
        ),
    ),
    Operation(
        name="catia_thickness",
        summary=(
            "Add or remove material on specific faces, without hollowing the part.\n"
            "Use it to thicken one wall of an already-shelled part, which is far safer "
            "than re-shelling it with a different value."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("faces", name_list("The faces to offset.")),
            required("thickness_mm", distance("How much to add; negative removes.")),
        ),
    ),
    Operation(
        name="catia_remove_face",
        summary=(
            "Delete faces and heal the surrounding surfaces back together.\n"
            "The way to simplify a part for analysis — dropping a fillet or a small "
            "boss that only makes the mesh expensive without changing the answer."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("faces", name_list("The faces to remove.")),
            optional("keep_faces", name_list("Faces to extend to close the gap.")),
        ),
    ),
    Operation(
        name="catia_replace_face",
        summary=(
            "Replace faces of the solid with a surface, reshaping the part to it.\n"
            "How a flat top becomes a contoured one designed separately in surfaces. "
            "The surface must extend past the solid on every side or the replacement "
            "has no complete boundary to trim against, which is the usual failure."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("faces", name_list("The faces to replace.")),
            required("surface", vocab.element_reference("The surface to replace them with.")),
            optional("reversed", flag("Keep the other side of the surface. Default false.")),
        ),
    ),
    # -- bodies and booleans -------------------------------------------------
    Operation(
        name="catia_body_create",
        summary=(
            "Create a new body in the part and make it the one features are added to.\n"
            "Bodies are how a part is built from pieces that are combined later: model "
            "the boss in its own body, then add or remove it. Without this, a part is "
            "one linear feature stack and there is no way to subtract one shape from "
            "another."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("name", new_name("A name for the body.")),
            optional("activate", flag("Make it the body features go into. Default true.")),
        ),
    ),
    Operation(
        name="catia_body_activate",
        summary=(
            "Choose which body new features are added to — CATIA's Define In Work Object.\n"
            "Everything created after this call lands in the named body until it is "
            "changed again. Forgetting to switch back is the usual reason a feature "
            "appears in the wrong place in the tree."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("body", vocab.element_reference("The body to work in.")),),
    ),
    Operation(
        name="catia_boolean",
        summary=(
            "Combine two bodies — add, remove, intersect, union-trim or assemble.\n"
            "`remove` is how a cavity is cut from a block using a shape rather than a "
            "sketch, which is the only practical way to make a mould or a complex "
            "pocket. `assemble` respects the signs of the material inside the tool "
            "body, so a body containing both added and removed material behaves as one "
            "unit."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("operation", one_of(vocab.BOOLEAN_OPERATIONS, "Which boolean to apply.")),
            required("tool_body", vocab.element_reference("The body being combined in.")),
            optional("target_body", vocab.element_reference("The body to combine into. Defaults to the main body.")),
        ),
    ),
    Operation(
        name="catia_geometrical_set",
        summary=(
            "Create a geometrical set — a folder in the tree for construction geometry.\n"
            "Wireframe and surfaces created without one land loose in the part and "
            "clutter it. This is housekeeping that pays for itself the first time a "
            "part has to be edited by someone else."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional("name", new_name("A name for the set.")),
            optional("ordered", flag("Create an ordered geometrical set. Default false.")),
            optional("activate", flag("Make it the set new geometry goes into. Default true.")),
        ),
    ),
    # -- transformations -----------------------------------------------------
    Operation(
        name="catia_translate",
        summary=(
            "Move the body by a distance along a direction.\n"
            "A transformation feature moves the material itself, unlike an assembly "
            "constraint which moves a component. Use it to reposition a body before a "
            "boolean."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("direction", direction3("Which way to move.")),
            required("distance_mm", distance("How far.")),
            optional("body", vocab.element_reference("The body to move. Defaults to the active one.")),
        ),
    ),
    Operation(
        name="catia_rotate",
        summary=(
            "Rotate the body about an axis.\n"
            "A transformation feature moves the material itself, unlike an assembly "
            "constraint which moves a component. Use it to orient a body before a "
            "boolean combines it with another."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("axis", vocab.element_reference("The line or axis to rotate about.")),
            required("angle_deg", signed_angle("How far to rotate.")),
            optional("body", vocab.element_reference("The body to rotate. Defaults to the active one.")),
        ),
    ),
    Operation(
        name="catia_symmetry",
        summary=(
            "Move the body to its mirror image about a plane, point or line.\n"
            "Unlike catia_mirror this does not keep the original — it relocates it. "
            "That is what makes a left-hand version of a right-hand part."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("reference", vocab.support("The plane, point or line to reflect about.")),
            optional("body", vocab.element_reference("The body to reflect. Defaults to the active one.")),
        ),
    ),
    Operation(
        name="catia_scale",
        summary=(
            "Scale the body uniformly about a plane or point.\n"
            "Used for shrinkage compensation on a mould, where the cavity is cut a "
            "percentage larger than the finished part."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("reference", vocab.support("The plane or point to scale about.")),
            required("factor", ratio("Scale factor.")),
            optional("body", vocab.element_reference("The body to scale. Defaults to the active one.")),
        ),
    ),
    Operation(
        name="catia_affinity",
        summary=(
            "Scale the body by a different factor along each of three axes.\n"
            "Non-uniform scaling — how an aerofoil section is stretched in chord "
            "without changing its thickness."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("x_factor", ratio("Scale along the local X axis.")),
            required("y_factor", ratio("Scale along the local Y axis.")),
            required("z_factor", ratio("Scale along the local Z axis.")),
            optional("axis_system", vocab.element_reference("The frame to scale in. Defaults to the part origin.")),
            optional("body", vocab.element_reference("The body to scale. Defaults to the active one.")),
        ),
    ),
    Operation(
        name="catia_pattern_user",
        summary=(
            "Repeat a feature at a list of points defined by a sketch — a user pattern.\n"
            "The escape hatch when the positions are neither a grid nor a circle: draw "
            "the points, and every one becomes an instance."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("positions", vocab.element_reference("The sketch whose points give the positions.")),
            optional("feature", vocab.element_reference("The feature to repeat. Defaults to the last one.")),
            optional("anchor", vocab.element_reference("The point in the sketch the original sits on.")),
        ),
    ),
    Operation(
        name="catia_pattern_explode",
        summary=(
            "Break a pattern into its individual features so they can be edited apart.\n"
            "Irreversible in the sense that the pattern relationship is gone: the "
            "instances become ordinary features and no longer follow the original."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("pattern", vocab.element_reference("The pattern to explode.")),),
    ),
    # -- feature tree management --------------------------------------------
    Operation(
        name="catia_feature_rename",
        summary=(
            "Rename a feature, sketch or body.\n"
            "Worth doing on anything another feature references: 'Pad.7' tells the next "
            "reader nothing, and a named feature survives being reordered."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("feature", vocab.element_reference("The feature to rename.")),
            required("name", new_name("Its new name.")),
        ),
    ),
    Operation(
        name="catia_feature_activate",
        summary=(
            "Deactivate or reactivate a feature without deleting it.\n"
            "The safe way to test whether a feature is the cause of a rebuild error, "
            "and the safe way to produce a simplified variant of a part for analysis."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("feature", vocab.element_reference("The feature to change.")),
            required("active", flag("True to activate, false to deactivate.")),
        ),
    ),
    Operation(
        name="catia_feature_reorder",
        summary=(
            "Move a feature to a different position in the specification tree.\n"
            "Order is meaning in a history-based modeller: a fillet applied before a "
            "pocket is cut is a different part from the same fillet applied after."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("feature", vocab.element_reference("The feature to move.")),
            required("after", vocab.element_reference("The feature it should follow.")),
        ),
    ),
    Operation(
        name="catia_feature_parents",
        summary=(
            "Report what a feature depends on and what depends on it.\n"
            "Check this before deleting or reordering anything: it is the difference "
            "between removing one feature and breaking eleven."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            required("feature", vocab.element_reference("The feature to inspect.")),
            optional("depth", count("How many levels to walk. Default 1.", maximum=10)),
        ),
    ),
)
