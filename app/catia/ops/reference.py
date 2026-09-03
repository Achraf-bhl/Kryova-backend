"""Reference geometry: planes, points, lines, axis systems.

Small module, disproportionate effect. Almost every limit in the old tool set
traced back to having no way to *name a place* — a sketch could only sit on one
of three planes, a hole could only sit at one of five spots on a face, a fillet
could only take a keyword instead of an edge. All of those are downstream of
there being no reference geometry to point at.

Creating a plane 40 mm above the top face and sketching on it is two calls here
and was not expressible at all before. The same goes for a point at a real
coordinate, which is what `catia_hole_at` consumes.

Everything here maps onto `HybridShapeFactory`, whose datum-creating half is
the part of the CAA API that the rest of the modelling vocabulary rests on.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    count,
    direction3,
    distance,
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
)

_WB = Workbench.GENERATIVE_SHAPE_DESIGN

OPERATIONS: tuple[Operation, ...] = (
    # -- planes --------------------------------------------------------------
    Operation(
        name="catia_plane_offset",
        summary=(
            "Create a plane parallel to an existing plane or planar face, at a distance.\n"
            "This is the single most useful reference element: it is how a sketch "
            "reaches a height that is not one of the three origin planes. Negative "
            "offsets go the other way."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("reference", vocab.support("The plane or planar face to offset from.")),
            required("distance_mm", distance("How far to offset.")),
            optional("name", new_name("A name for the plane. CATIA numbers it if omitted.")),
            optional("reversed", flag("Offset to the other side. Default false.")),
        ),
    ),
    Operation(
        name="catia_plane_angle",
        summary=(
            "Create a plane at an angle to an existing plane, hinged about an axis.\n"
            "Use it for a draft face, an angled boss, or any sketch that has to sit "
            "square to something that is itself at an angle."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("reference", vocab.support("The plane to measure the angle from.")),
            required("axis", vocab.element_reference("The line or axis the plane rotates about.")),
            required("angle_deg", signed_angle("Angle from the reference plane.")),
            optional("name", new_name("A name for the plane.")),
        ),
    ),
    Operation(
        name="catia_plane_through_points",
        summary=(
            "Create a plane through three points.\n"
            "The most direct way to define a plane from geometry that already exists — "
            "three vertices of a part, or three points you have placed."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "points",
                name_list("Exactly three point names the plane passes through.", minimum=3),
            ),
            optional("name", new_name("A name for the plane.")),
        ),
    ),
    Operation(
        name="catia_plane_normal_to_curve",
        summary=(
            "Create a plane perpendicular to a curve at a point on it.\n"
            "This is how a sweep profile is placed square to its path — the usual first "
            "step in building a pipe, a cable run or a swept rib."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("curve", vocab.element_reference("The curve to stand perpendicular to.")),
            optional("point", vocab.element_reference("Where on the curve. Defaults to its start.")),
            optional("name", new_name("A name for the plane.")),
        ),
    ),
    Operation(
        name="catia_plane_tangent_to_surface",
        summary=(
            "Create a plane tangent to a surface at a point on it.\n"
            "Use it to sketch on a curved wall — the plane touches the surface at "
            "that point, so a boss built on it sits square to the local shape."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to be tangent to.")),
            required("point", vocab.element_reference("The point on the surface.")),
            optional("name", new_name("A name for the plane.")),
        ),
    ),
    Operation(
        name="catia_plane_mean",
        summary=(
            "Create the best-fit mean plane through a set of points.\n"
            "Use it to get a working plane out of scanned or measured data, where no "
            "three points are exactly representative."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", name_list("The points to fit through.", minimum=3)),
            optional("name", new_name("A name for the plane.")),
        ),
    ),
    Operation(
        name="catia_planes_between",
        summary=(
            "Create a run of equally spaced planes between two existing planes.\n"
            "One call for what would otherwise be many offsets — the setup for a "
            "sectioning study or a multi-section loft."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first", vocab.support("The plane at one end.")),
            required("second", vocab.support("The plane at the other end.")),
            required("count", count("How many planes to create between them.", minimum=1)),
        ),
    ),
    # -- points --------------------------------------------------------------
    Operation(
        name="catia_point_at",
        summary=(
            "Create a point at explicit coordinates in the part's frame.\n"
            "Points are the anchors everything else references: a hole position, a "
            "spline vertex, a plane definition, an FEA sensor location."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("at", point3("Where to put the point.")),
            optional("name", new_name("A name for the point.")),
            optional(
                "reference",
                vocab.element_reference("Measure the coordinates from this point instead of the origin."),
            ),
        ),
    ),
    Operation(
        name="catia_point_on_curve",
        summary=(
            "Create a point on a curve, at a distance or a proportion along it.\n"
            "Give `ratio` for a proportion (0.5 is the midpoint) or `distance_mm` for "
            "an absolute length along the curve, not both."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("curve", vocab.element_reference("The curve to sit on.")),
            optional("ratio", ratio("Proportion along the curve, 0 to 1.")),
            optional("distance_mm", distance("Distance along the curve from its start.")),
            optional("from_end", flag("Measure from the far end instead. Default false.")),
            optional("name", new_name("A name for the point.")),
        ),
    ),
    Operation(
        name="catia_point_on_surface",
        summary=(
            "Create a point on a surface, offset from a reference point along a direction.\n"
            "The point stays on the surface as it moves, which is what makes it a "
            "usable anchor for a hole or a fastener on a curved face."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to sit on.")),
            optional("reference", vocab.element_reference("The point to measure from.")),
            optional("direction", direction3("Which way to move along the surface.")),
            optional("distance_mm", distance("How far to move.")),
            optional("name", new_name("A name for the point.")),
        ),
    ),
    Operation(
        name="catia_point_centre",
        summary=(
            "Create a point at the centre of a circle, arc, sphere or planar face.\n"
            "The reliable way to reference the middle of an existing feature rather "
            "than measuring it and typing a coordinate that then goes stale."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("The circle, arc, sphere or face.")),
            optional("name", new_name("A name for the point.")),
        ),
    ),
    Operation(
        name="catia_point_between",
        summary="Create a point between two other points, at a proportion along the line joining them.",
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", name_list("Exactly two point names.", minimum=2)),
            optional("ratio", ratio("Proportion from the first point. Default 0.5 (midpoint).")),
            optional("name", new_name("A name for the point.")),
        ),
    ),
    # -- lines and axes ------------------------------------------------------
    Operation(
        name="catia_line_between",
        summary=(
            "Create a line between two points.\n"
            "The extension parameters push the ends past the points, which is how a "
            "rotation axis or a cutting line is made long enough to reach the "
            "geometry it has to act on."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", name_list("Exactly two point names.", minimum=2)),
            optional("extend_start_mm", distance("Extend beyond the first point.")),
            optional("extend_end_mm", distance("Extend beyond the second point.")),
            optional("name", new_name("A name for the line.")),
        ),
    ),
    Operation(
        name="catia_line_direction",
        summary=(
            "Create a line from a point, along a direction, for a length.\n"
            "The usual way to make a rotation axis or a sweep spine that is not already "
            "an edge of the part."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("point", vocab.element_reference("Where the line starts.")),
            required("direction", direction3("Which way it runs.")),
            required("length_mm", length("How long it is.")),
            optional("both_sides", flag("Extend the same length backwards too. Default false.")),
            optional("name", new_name("A name for the line.")),
        ),
    ),
    Operation(
        name="catia_line_normal",
        summary=(
            "Create a line normal to a surface at a point on it.\n"
            "The direction a hole would be drilled or a fastener seated at that "
            "point — reach for it whenever 'perpendicular to this face' needs to be "
            "an actual object."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to stand off.")),
            required("point", vocab.element_reference("The point on it.")),
            required("length_mm", length("How long the line is.")),
            optional("name", new_name("A name for the line.")),
        ),
    ),
    Operation(
        name="catia_line_tangent",
        summary=(
            "Create a line tangent to a curve at a point on it.\n"
            "Gives the local direction of travel along the curve, which is what a "
            "sweep orientation or a run-out ramp is built from."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("curve", vocab.element_reference("The curve to be tangent to.")),
            required("point", vocab.element_reference("The point on it.")),
            required("length_mm", length("How long the line is.")),
            optional("name", new_name("A name for the line.")),
        ),
    ),
    Operation(
        name="catia_axis_system",
        summary=(
            "Create a local axis system at a point, optionally aligned to two directions.\n"
            "An axis system is what makes a sub-assembly's geometry addressable in its "
            "own frame — and what a measurement or an export can be reported relative "
            "to instead of the global origin."
        ),
        tier=Tier.WRITE,
        workbench=Workbench.PART_DESIGN,
        params=(
            required("origin", vocab.element_reference("The point the axis system sits at.")),
            optional("x_direction", direction3("Direction of the local X axis.")),
            optional("y_direction", direction3("Direction of the local Y axis.")),
            optional("name", new_name("A name for the axis system.")),
            optional(
                "set_current",
                flag("Make it the active axis system for what follows. Default false."),
            ),
        ),
    ),
    # -- inspection ----------------------------------------------------------
    #
    # These two are why edge and face picking can exist at all. The old tool set
    # had five edge keywords because nothing could enumerate the real edges; a
    # model that can list them can name one, and then a per-edge radius follows.
    Operation(
        name="catia_list_faces",
        summary=(
            "List the faces of the part or of one feature, with their area, centre and "
            "outward normal.\n"
            "Call this before any operation that acts on a face — draft, shell, "
            "face fillet, an FEA restraint — so you can name the face rather than "
            "guessing at a bounding-box label. The returned ids stay valid until the "
            "part's topology changes."
        ),
        tier=Tier.READ,
        workbench=Workbench.PART_DESIGN,
        params=(
            optional("feature", vocab.element_reference("Restrict to faces created by this feature.")),
            optional(
                "kind",
                one_of(
                    ("all", "planar", "cylindrical", "conical", "spherical", "other"),
                    "Only report faces of this kind. Default all.",
                ),
            ),
            optional("min_area_mm2", length("Ignore faces smaller than this, in mm². Default 0.")),
        ),
    ),
    Operation(
        name="catia_list_edges",
        summary=(
            "List the edges of the part or of one feature, with their length, midpoint "
            "and whether they are convex or concave.\n"
            "This is what turns 'fillet the top edges' into 'fillet these four edges at "
            "3 mm and that one at 1 mm'. Convexity matters: an outside corner takes a "
            "round, an inside corner takes a different radius and often a different "
            "sign of intent."
        ),
        tier=Tier.READ,
        workbench=Workbench.PART_DESIGN,
        params=(
            optional("feature", vocab.element_reference("Restrict to edges created by this feature.")),
            optional("face", vocab.face_reference("Restrict to edges bounding this face.")),
            optional(
                "kind",
                one_of(
                    ("all", "linear", "circular", "convex", "concave"),
                    "Only report edges of this kind. Default all.",
                ),
            ),
            optional("min_length_mm", length("Ignore edges shorter than this. Default 0.")),
        ),
    ),
)
