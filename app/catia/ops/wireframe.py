"""Wireframe: 3D curves, and the curves derived from other geometry.

The distinction from `sketcher.py` is worth stating because it decides which
module a new curve belongs in: a sketch curve lives in a 2D sketch on a support
and is edited in the Sketcher; a wireframe curve lives directly in 3D space and
is built from references. A helix cannot be sketched — it does not lie in a
plane — which is exactly why this module exists.

The derived curves at the end (projection, intersection, combine, parallel) are
the associative ones: they are defined by other geometry and follow it when it
moves. That is what makes them worth more than drawing the same shape by hand.
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
    flag,
    length,
    name_list,
    new_name,
    one_of,
    optional,
    ratio,
    required,
    signed_angle,
)

_WB = Workbench.GENERATIVE_SHAPE_DESIGN

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_curve_circle",
        summary=(
            "Create a 3D circle or arc from a centre and radius, three points, or a "
            "centre and a tangent.\n"
            "A 3D circle needs a support plane or a normal direction; without one there "
            "are infinitely many circles through the same centre."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("centre_radius", "three_points", "centre_point", "bitangent", "tritangent"),
                    "How the circle is defined.",
                ),
            ),
            optional("centre", vocab.element_reference("Centre point.")),
            optional("radius_mm", length("Radius.")),
            optional("points", name_list("The defining points, for the three-point kind.")),
            optional("support", vocab.support("The plane or surface the circle lies on.")),
            optional("start_angle_deg", signed_angle("Trim the circle to an arc starting here.")),
            optional("end_angle_deg", signed_angle("And ending here.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_spline",
        summary=(
            "Create a 3D spline through a list of points.\n"
            "Give tangent directions at the ends to control how the curve leaves them; "
            "without them the spline chooses its own and can bulge unexpectedly near "
            "the first and last points."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", name_list("The points to pass through, in order.", minimum=2)),
            optional("start_tangent", direction3("Direction the curve leaves the first point.")),
            optional("end_tangent", direction3("Direction it arrives at the last point.")),
            optional("support", vocab.element_reference("A surface the spline must lie on.")),
            optional("closed", flag("Close the spline into a loop. Default false.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_helix",
        summary=(
            "Create a helix from an axis, a pitch, a height and a starting point.\n"
            "The path a thread, a spring or a spiral stair follows. Set `taper_deg` for "
            "a conical helix, and a negative pitch or `clockwise: false` for a "
            "left-hand one."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("axis", vocab.element_reference("The axis the helix winds around.")),
            required("start_point", vocab.element_reference("Where the helix begins.")),
            required("pitch_mm", length("Rise per full turn.")),
            required("height_mm", length("Total height.")),
            optional("clockwise", flag("Wind clockwise looking along the axis. Default true.")),
            optional("taper_deg", angle("Taper angle for a conical helix. Default 0.", maximum=89.0)),
            optional("start_angle_deg", signed_angle("Angular offset of the start. Default 0.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_spiral",
        summary=(
            "Create a planar spiral from a centre, a start radius and a pitch.\n"
            "A spiral stays in its plane as it grows outwards, unlike a helix which "
            "climbs. This is the path of a scroll compressor or a spiral heat "
            "exchanger, not of a thread."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("support", vocab.support("The plane the spiral lies in.")),
            required("centre", vocab.element_reference("The centre point.")),
            required("start_radius_mm", length("Radius at the start.")),
            optional("pitch_mm", length("Radial growth per turn.")),
            optional("end_radius_mm", length("Radius at the end, instead of a turn count.")),
            optional("turns", count("How many turns.", maximum=1000)),
            optional("clockwise", flag("Wind clockwise. Default true.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_polyline",
        summary=(
            "Create a 3D polyline through a list of points, optionally with rounded corners.\n"
            "Giving a radius rounds every corner in the same call, which matters when "
            "the polyline is about to become a sweep path — a sharp corner there makes "
            "the sweep fail."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("points", name_list("The points to join, in order.", minimum=2)),
            optional("radius_mm", feature_length("Round every corner to this radius.")),
            optional("closed", flag("Join the last point back to the first. Default false.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_corner",
        summary=(
            "Create an arc tangent to two curves — a 3D corner.\n"
            "The wireframe equivalent of a sketch fillet, for curves that do not share "
            "a plane."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("Exactly two curves to round between.", minimum=2)),
            required("radius_mm", feature_length("Radius of the corner.")),
            optional("support", vocab.support("The plane or surface the corner lies on.")),
            optional("trim", flag("Trim both curves back to the arc. Default true.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_connect",
        summary=(
            "Create a curve joining the ends of two other curves, with chosen continuity.\n"
            "Continuity is the whole point: `curvature` produces a join with no visible "
            "break in a reflection, which `point` and `tangent` do not."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first_curve", vocab.element_reference("The curve at one end.")),
            required("second_curve", vocab.element_reference("The curve at the other end.")),
            optional(
                "continuity",
                one_of(("point", "tangent", "curvature"), "How smoothly it joins. Default tangent."),
            ),
            optional("first_tension", ratio("How strongly it follows the first curve. Default 1.")),
            optional("second_tension", ratio("How strongly it follows the second. Default 1.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    # -- derived, associative curves ----------------------------------------
    Operation(
        name="catia_curve_project",
        summary=(
            "Project a curve or point onto a surface.\n"
            "Associative: when the source curve moves, the projection follows. This is "
            "how a trim line is put onto a shaped panel."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("The curve or point to project.")),
            required("support", vocab.element_reference("The surface to project onto.")),
            optional("direction", direction3("Project along this direction instead of normally.")),
            optional("nearest", flag("Keep only the nearest solution. Default true.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_curve_intersect",
        summary=(
            "Create the curve or point where two elements cross.\n"
            "Surfaces give a curve, a curve and a surface give a point. The reliable "
            "way to find where two shapes actually meet rather than where they look "
            "like they meet."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("elements", name_list("Exactly two elements to intersect.", minimum=2)),
            optional("extend", flag("Extend the elements to find an intersection. Default false.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_curve_combine",
        summary=(
            "Create a 3D curve from two planar curves, each extruded and intersected.\n"
            "How a shape defined by two orthogonal drawing views becomes a real 3D "
            "curve — the wireframe counterpart of catia_solid_combine."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("first_curve", vocab.element_reference("The curve in the first view.")),
            required("second_curve", vocab.element_reference("The curve in the second view.")),
            optional("first_direction", direction3("Extrusion direction of the first curve.")),
            optional("second_direction", direction3("Extrusion direction of the second.")),
            optional("name", new_name("A name for the result.")),
        ),
    ),
    Operation(
        name="catia_curve_parallel",
        summary=(
            "Create a curve parallel to another, on a surface, at a distance.\n"
            "The offset of a curve within a surface — how a border or a seam allowance "
            "is derived from an edge."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("curve", vocab.element_reference("The curve to offset.")),
            required("support", vocab.element_reference("The surface it lies on.")),
            required("distance_mm", distance("How far to offset.")),
            optional("reversed", flag("Offset the other way. Default false.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_offset_3d",
        summary="Create a curve offset from another in 3D space, without needing a support surface.",
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("curve", vocab.element_reference("The curve to offset.")),
            required("distance_mm", distance("How far to offset.")),
            required("direction", direction3("Which way to offset.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_section",
        summary=(
            "Create the section curve where a plane cuts through geometry.\n"
            "Used to take a real profile off an existing shape — the first step when "
            "reverse-engineering or when adding a mating part."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("The surface or solid to cut.")),
            required("plane", vocab.support("The plane to cut with.")),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
    Operation(
        name="catia_curve_extremum",
        summary=(
            "Find the extreme point of an element along a direction — its highest, "
            "lowest, leftmost point.\n"
            "An associative way to reference 'the top of this shape' that stays correct "
            "when the shape changes."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("element", vocab.element_reference("The element to search.")),
            required("direction", direction3("The direction to find the extreme along.")),
            optional("second_direction", direction3("Break ties along this direction.")),
            optional("maximum", flag("Find the maximum rather than the minimum. Default true.")),
            optional("name", new_name("A name for the point.")),
        ),
    ),
    Operation(
        name="catia_curve_reflect_line",
        summary=(
            "Create the line on a surface where the view or light direction meets it at "
            "a given angle — a reflect line.\n"
            "This is how a silhouette or a highlight line is captured as real geometry, "
            "which matters for class-A surfacing and for finding a moulding parting line."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("surface", vocab.element_reference("The surface to find the line on.")),
            required("direction", direction3("The viewing or lighting direction.")),
            optional("angle_deg", angle("Angle to the surface normal. Default 90 (silhouette).", maximum=180.0)),
            optional("name", new_name("A name for the curve.")),
        ),
    ),
)
