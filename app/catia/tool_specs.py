"""The CATIA tool vocabulary the agent may call.

Three rules shape every entry.

**Semantic operations, never raw coordinates.** There is no tool that takes an
(x, y, z), a sketch-plane origin or a transform. Language models are documented
to fail on reference frames, and a wrong sign in a translation does not raise --
it quietly builds the wrong part. The coordinate maths lives inside the tool
implementation, where it can be tested; the model names a plane, a face and a
dimension.

**No filesystem paths, and no arbitrary execution.** The model names documents,
never paths -- the daemon resolves everything inside its own working directory.
`SystemService.Evaluate`, CATIA's arbitrary-VBScript hatch, is not exposed and
must never be added: it would turn one prompt injection into remote code
execution on an engineer's workstation.

**Descriptions are prompt text.** The model reads them to choose, so they say
*when* to reach for a tool and what to do afterwards, not merely what it does.

The tier decides enforcement, not the UI:

* `read` runs freely.
* `write` needs `allow_mutations` on the turn and is auto-checkpointed first.
* `destructive` additionally needs a per-call approval token the server signs
  only after an explicit user click.

The daemon carries its own copy of the names, schemas and tiers and re-validates
every incoming call against it. A confused or compromised agent stream cannot
escalate by lying about a tier, and cannot smuggle an extra field past a schema.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.solve.materials import MATERIALS

#: CATIA's three standard reference planes, and the only planes a sketch may be
#: placed on in v1. Named rather than numbered so the model cannot invent one.
SKETCH_PLANES = ("XY", "YZ", "ZX")

#: Semantic faces of the part's bounding box, in the part's own frame. The
#: daemon resolves these to real topological faces; the model never sees a face
#: id, which would be meaningless across a rebuild anyway.
NAMED_FACES = ("top", "bottom", "front", "back", "left", "right")

#: Where on a face something goes. Enough vocabulary to place a bolt pattern
#: without ever handing the model a millimetre offset from an origin it cannot
#: see.
FACE_POSITIONS = ("center", "front_left", "front_right", "back_left", "back_right")

#: Which edges of a feature an operation applies to.
EDGE_SELECTORS = ("all", "vertical", "horizontal", "top", "bottom")

#: Which part axes a pattern runs along when it is drawn in a named plane.
#: A pattern takes both its directions from one plane -- the first in-plane
#: axis, then the second -- so naming the plane names the directions, and the
#: model never handles a vector. Measured on a live V5-R33.
PATTERN_PLANE_AXES = {"XY": ("X", "Y"), "YZ": ("Y", "Z"), "ZX": ("Z", "X")}

#: Standard viewpoints for a screenshot.
VIEWPOINTS = ("iso", "front", "back", "top", "bottom", "left", "right")

#: The material library the agent may choose from, named rather than described
#: by density: the model picks a material, the server looks up what it weighs.
#: Sourced from the solver's own library so the two can never disagree about
#: what "steel-1018" means.
MATERIAL_KEYS = tuple(MATERIALS)

#: Units a named parameter may be set in. CATIA parameters carry a unit and
#: setting a length in degrees is a silent no-op, so the unit is required.
PARAMETER_UNITS = ("mm", "deg", "kg")


class CatiaTier(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class CatiaToolSpec:
    """One callable CATIA operation.

    `parameters` is a JSON Schema object with `additionalProperties: false`.
    Strictness is the point: an unknown field is a model that has misunderstood
    the tool, and letting it through means the daemon silently ignores whatever
    the model actually meant.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    tier: CatiaTier
    #: Uses `catia_export_timeout_s` instead of `catia_call_timeout_s`. A STEP
    #: export re-tessellates the whole part and legitimately takes minutes.
    long_running: bool = False

    @property
    def mutating(self) -> bool:
        return self.tier is not CatiaTier.READ


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _enum(options: tuple[str, ...], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(options), "description": description}


def _length(description: str, maximum: float = 10_000.0) -> dict[str, Any]:
    # Every length in this codebase is millimetres and nothing converts. The
    # ceiling is not a physical limit, it is a typo guard: a model that means
    # 12 mm and emits 12000 should be refused rather than build a part the size
    # of a house and then fail the mesher ten minutes later.
    return {
        "type": "number",
        "exclusiveMinimum": 0,
        "maximum": maximum,
        "description": f"{description} Millimetres.",
    }


_FEATURE_NAME = {
    "type": "string",
    "maxLength": 120,
    "description": (
        "Name of an existing feature or sketch, exactly as catia_measure or a "
        "previous tool result reported it (e.g. 'Sketch.1', 'Pad.1')."
    ),
}


CATIA_TOOL_SPECS: list[CatiaToolSpec] = [
    CatiaToolSpec(
        name="catia_status",
        description=(
            "Check whether a CATIA workstation is connected to this account, which "
            "CATIA version it is running, and which document (if any) this "
            "conversation is already bound to. Call this FIRST in any conversation "
            "where the user mentions CATIA, before promising to build anything -- if "
            "no bridge is connected, say so and tell the user to start the Kryova "
            "CATIA bridge on their Windows machine rather than attempting the work."
        ),
        parameters=_object({}),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_new_part",
        description=(
            "Create a new, empty CATPart in CATIA and bind it to this conversation. "
            "Call this once, when the user wants to model something from scratch and "
            "catia_status reported no document. Do not call it again in the same "
            "conversation -- a second part would orphan the first. To continue "
            "earlier work, use catia_open_document instead."
        ),
        parameters=_object(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": (
                        "Short part name, letters, digits, spaces, hyphens and "
                        "underscores only, e.g. 'Mounting bracket'."
                    ),
                }
            },
            required=["name"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_open_document",
        description=(
            "Reopen the CATIA document this conversation was already working on. "
            "Call this when catia_status reports a bound document but CATIA has "
            "nothing open -- typically at the start of a resumed session the day "
            "after. If the file is missing on the workstation the daemon restores it "
            "from the latest checkpoint automatically; you do not need to ask."
        ),
        parameters=_object({}),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_list_parameters",
        description=(
            "List the document's named parameters with their values and units. Call "
            "this before changing a dimension, so you set a parameter that exists "
            "under its real name rather than inventing one. Also the fastest way to "
            "answer 'how thick is it' without measuring."
        ),
        parameters=_object({}),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_set_parameter",
        description=(
            "Set one named parameter and rebuild the part. This is the preferred way "
            "to change a dimension on a parametric model: it preserves design intent, "
            "where adding a new feature does not. Confirm the name with "
            "catia_list_parameters first, then call catia_measure afterwards to see "
            "what the change actually did to the mass and bounding box."
        ),
        parameters=_object(
            {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Parameter name exactly as catia_list_parameters reported it.",
                },
                "value": {"type": "number", "description": "New value, in `unit`."},
                "unit": _enum(
                    PARAMETER_UNITS,
                    "Unit of `value`. CATIA parameters are typed: setting a length "
                    "in degrees silently does nothing, so this is required.",
                ),
            },
            required=["name", "value", "unit"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_rectangle",
        description=(
            "Draw a rectangle centred on the origin of a named reference plane and "
            "leave it as a new sketch. This is the usual first step of a solid: "
            "sketch, then catia_pad. The returned sketch name is what catia_pad and "
            "catia_pocket take."
        ),
        parameters=_object(
            {
                "plane": _enum(
                    SKETCH_PLANES,
                    "Reference plane to sketch on. XY is the horizontal plane; a "
                    "plate lying flat is sketched on XY and padded upwards.",
                ),
                "width_mm": _length("Size along the plane's first axis."),
                "height_mm": _length("Size along the plane's second axis."),
            },
            required=["plane", "width_mm", "height_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_circle",
        description=(
            "Draw a circle centred on the origin of a named reference plane and leave "
            "it as a new sketch. Pad it for a cylinder, or pocket it for a bore "
            "through an existing solid."
        ),
        parameters=_object(
            {
                "plane": _enum(SKETCH_PLANES, "Reference plane to sketch on."),
                "diameter_mm": _length("Circle diameter."),
            },
            required=["plane", "diameter_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_pad",
        description=(
            "Extrude a sketch into a solid by a length. Call this after a sketch tool, "
            "passing the sketch name the sketch tool returned. Symmetric pads grow "
            "equally either side of the sketch plane, which is what you want when the "
            "sketch sits on the part's mid-plane."
        ),
        parameters=_object(
            {
                "sketch": _FEATURE_NAME,
                "length_mm": _length("Extrusion length."),
                "symmetric": {
                    "type": "boolean",
                    "description": (
                        "Extrude half the length either side of the sketch plane "
                        "instead of the full length in one direction. Default false."
                    ),
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Extrude the opposite way along the plane normal. Default false.",
                },
            },
            required=["sketch", "length_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_pocket",
        description=(
            "Cut a sketch into an existing solid, either to a depth or all the way "
            "through. Use `through_all` for a clearance slot: a depth chosen by eye "
            "leaves a sliver of material when the part later gets thicker."
        ),
        parameters=_object(
            {
                "sketch": _FEATURE_NAME,
                "depth_mm": _length(
                    "Cut depth. Omit entirely when `through_all` is true -- do not "
                    "send 0, which is rejected as a depth."
                ),
                "through_all": {
                    "type": "boolean",
                    "description": "Cut entirely through the material. Default false.",
                },
            },
            required=["sketch"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_set_material",
        description=(
            "Set the part's material. Call this as soon as the user names one -- "
            "'a steel bracket' is naming one -- and before quoting any mass, because "
            "an unspecified part is weighed at CATIA's default 1000 kg/m3 and every "
            "mass you report until then is wrong by the density ratio (7.9x for "
            "steel). Also call it when the user asks for a mass, a weight or a "
            "structural analysis and no material has been set yet: ask which one "
            "only if their request gives you nothing to go on. Applying it in CATIA "
            "needs the Material Library product, and the result says whether that "
            "happened; the mass Kryova reports is correct either way."
        ),
        parameters=_object(
            {
                "material": _enum(
                    MATERIAL_KEYS,
                    "Which material, from Kryova's library. Pick the closest match to "
                    "what the user said -- 'steel' is 'steel-1018', 'aluminium' is "
                    "'aluminium-6061-t6' -- and say which one you chose.",
                )
            },
            required=["material"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_hole",
        description=(
            "Put a hole through a named face of the part at a named position. For a "
            "bolt pattern, call it once per position. Cut as a circle sketched on "
            "the face's plane and pocketed through the material, so it is a plain "
            "through hole: no thread, no countersink, no tapping standard. If the "
            "user needs any of those, say so rather than implying this produced them."
        ),
        parameters=_object(
            {
                "face": _enum(
                    NAMED_FACES,
                    "Which face of the part to drill into, in the part's own frame.",
                ),
                "position": _enum(
                    FACE_POSITIONS,
                    "Where on that face. Corner positions are inset from the edge by "
                    "a clearance the daemon computes from the hole diameter, unless "
                    "`inset_mm` says otherwise.",
                ),
                "diameter_mm": _length("Hole diameter.", maximum=1_000.0),
                "depth_mm": _length(
                    "Hole depth. Omit entirely when `through_all` is true -- do not "
                    "send 0, which is rejected as a depth.",
                    10_000.0,
                ),
                "through_all": {
                    "type": "boolean",
                    "description": "Drill entirely through the part. Default true.",
                },
                # A bolt pattern is specified as "15 mm in from each corner"
                # far more often than it is left to a default. Without this the
                # distance had nowhere to go: observed live, the model was
                # asked for "four M8 bolt holes, 15 mm in from each corner",
                # had no field for the 15, and spent the whole turn guessing
                # invalid `face` and `position` values until it ran out of
                # steps and answered nothing.
                "inset_mm": _length(
                    "Distance from the two nearest edges to the hole centre, for a "
                    "corner position. Use this whenever the user gives a distance "
                    "from the edge. Ignored for `center`.",
                    10_000.0,
                ),
            },
            required=["face", "position", "diameter_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_fillet",
        description=(
            "Round edges by a radius. Reach for this when a result interpretation "
            "blames a stress concentration on a sharp internal corner -- a fillet "
            "is usually the cheapest fix available, and re-running the simulation "
            "afterwards is how you show it worked. `edges` picks which edges by "
            "their geometry: 'top' and 'bottom' are the highest and lowest edge "
            "loops, 'vertical' the edges running straight up, 'horizontal' every "
            "level edge, 'all' everything. Name a `feature` to round only that "
            "feature's own edges (a groove's rim, a boss's crown). If CATIA "
            "refuses, the radius is too large for the adjacent faces -- try a "
            "smaller one before giving up, and the part is left exactly as it was."
        ),
        parameters=_object(
            {
                "radius_mm": _length("Fillet radius.", maximum=1_000.0),
                "feature": _FEATURE_NAME,
                "edges": _enum(
                    EDGE_SELECTORS,
                    "Which edges to round, classified against the part's Z axis: "
                    "'top'/'bottom' are the highest/lowest edge loops, 'vertical' "
                    "runs along Z, 'horizontal' is any level edge. On a turned "
                    "part made with catia_shaft the axis lies along X, so these "
                    "names lose their meaning -- use 'all' with a `feature` "
                    "scope there instead. Default 'all'.",
                ),
            },
            required=["radius_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_chamfer",
        description=(
            "Break edges with a chamfer of a given leg length and angle. Use for "
            "assembly lead-ins and deburring callouts; use catia_fillet instead "
            "when the goal is to reduce stress. Targets edges exactly the way "
            "catia_fillet does: `edges` selects by geometry (top / bottom / "
            "vertical / horizontal / all) and `feature` narrows to one feature's "
            "edges. A refusal leaves the part unchanged; a smaller leg usually "
            "fixes it."
        ),
        parameters=_object(
            {
                "length_mm": _length("Chamfer leg length.", maximum=1_000.0),
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 89.0,
                    "description": "Chamfer angle in degrees. Default 45.",
                },
                "feature": _FEATURE_NAME,
                "edges": _enum(
                    EDGE_SELECTORS,
                    "Which edges to chamfer -- same Z-axis classification as "
                    "catia_fillet, same caveat on turned parts. Default 'all'.",
                ),
            },
            required=["length_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_polygon",
        description=(
            "Draw a regular polygon (3 to 12 sides) centred on the origin of a named "
            "reference plane and leave it as a new sketch. Pad it for a hex boss or "
            "prismatic body; the diameter is measured across the corners "
            "(circumscribed circle). Use catia_sketch_rectangle or "
            "catia_sketch_circle for those shapes rather than a 4- or many-sided "
            "polygon."
        ),
        parameters=_object(
            {
                "plane": _enum(SKETCH_PLANES, "Reference plane to sketch on."),
                "sides": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 12,
                    "description": "Number of sides, e.g. 6 for a hex.",
                },
                "diameter_mm": _length("Across-corners diameter of the polygon."),
            },
            required=["plane", "sides", "diameter_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_revolve_profile",
        description=(
            "Draw the profile of a round part -- a rod, a shaft or a tube -- placed "
            "beside the revolution axis, ready for catia_shaft. THIS is how turned "
            "parts are made: the other sketch tools draw on the origin, and a profile "
            "sitting on the axis cannot be revolved, so catia_shaft needs this one. "
            "Give the finished diameters and length and the placement is worked out "
            "for you; leave inner_diameter_mm out for a solid rod, or give it for a "
            "tube's bore. Follow with catia_shaft."
        ),
        parameters=_object(
            {
                "plane": _enum(
                    SKETCH_PLANES,
                    "Plane to draw on. The part is revolved about this plane's "
                    "vertical axis, and grows along it -- ZX gives a shaft lying "
                    "along Z.",
                ),
                "outer_diameter_mm": _length("Outside diameter of the finished part."),
                "length_mm": _length("Length along the revolution axis."),
                "inner_diameter_mm": _length(
                    "Bore diameter, for a tube. Omit for a solid rod."
                ),
            },
            required=["plane", "outer_diameter_mm", "length_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_groove_profile",
        description=(
            "Draw the profile of a circumferential groove -- an o-ring gland, a "
            "circlip seat, a relief cut -- on an existing round part, ready for "
            "catia_groove. Give the diameter of the shaft it is being cut into and "
            "where along the shaft it sits; the profile is placed against the shaft "
            "wall for you. Use the same plane the shaft was made on, then call "
            "catia_groove."
        ),
        parameters=_object(
            {
                "plane": _enum(
                    SKETCH_PLANES,
                    "Plane to draw on. Use the same one the shaft's own profile used.",
                ),
                "shaft_diameter_mm": _length(
                    "Outside diameter of the shaft the groove is cut into."
                ),
                "width_mm": _length("Groove width, along the shaft."),
                "depth_mm": _length("Groove depth, measured in from the shaft surface."),
                "distance_from_end_mm": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 10_000.0,
                    "description": (
                        "Distance from the shaft's starting end to the near wall of "
                        "the groove. Millimetres."
                    ),
                },
            },
            required=[
                "plane",
                "shaft_diameter_mm",
                "width_mm",
                "depth_mm",
                "distance_from_end_mm",
            ],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_sketch_gear_profile",
        description=(
            "Draw the closed outline of an involute spur gear, centred on the "
            "origin, ready to turn into a solid. This is how gears are made here: "
            "for an EXTERNAL gear, draw this profile and catia_pad it to the face "
            "width. For an INTERNAL (ring) gear, first pad a plain disc larger than "
            "the gear's tip diameter, then draw this profile and catia_pocket it "
            "through -- the teeth then point inward. Give the module and tooth "
            "count; pitch, tip and root diameters come back so you can size the "
            "blank. The involute flanks are drawn as fine line segments -- correct "
            "for CAD models and FEA meshing, not a manufacturing-grade flank -- and "
            "the root has no trochoidal fillet, so add a small catia_fillet on the "
            "gear feature if a result interpretation flags root stress."
        ),
        parameters=_object(
            {
                "plane": _enum(SKETCH_PLANES, "Plane to draw on; XY then pad along Z."),
                "module_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 50.0,
                    "description": (
                        "Gear module in millimetres (pitch diameter = module x teeth). "
                        "Meshing gears must share it."
                    ),
                },
                "teeth": {
                    "type": "integer",
                    "minimum": 6,
                    "maximum": 100,
                    "description": "Number of teeth.",
                },
                "pressure_angle_deg": {
                    "type": "number",
                    "minimum": 10.0,
                    "maximum": 30.0,
                    "description": "Pressure angle in degrees. Default 20, the ISO standard.",
                },
            },
            required=["plane", "module_mm", "teeth"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_pattern_rectangular",
        description=(
            "Repeat a feature on an evenly spaced straight line or grid -- a row of "
            "bolt holes, a rack of slots, a field of cooling holes. Much better than "
            "calling catia_hole once per hole: it is a single feature the user can "
            "edit afterwards and the count stays a parameter. Both directions come "
            "from the plane you name: XY repeats along X then Y, YZ along Y then Z, "
            "ZX along Z then X. Counts are TOTALS including the feature already "
            "there, so count=5 leaves five holes in all. Repeats the last feature "
            "built unless you name another. The pattern grows from the seed feature "
            "outwards, and CATIA does NOT refuse copies that reach past the edge of "
            "the part -- it cuts them short or drops them and reports success, so a "
            "grid that does not fit comes back looking fine with fewer holes in it. "
            "Work out where the seed sits and keep count x spacing inside the part, "
            "then check volume_mm3 against what you expected before telling the user "
            "how many holes there are."
        ),
        parameters=_object(
            {
                "plane": _enum(
                    SKETCH_PLANES,
                    "Plane the pattern lies in. XY repeats along X (then Y for a "
                    "grid) -- the usual choice for features on the top of a plate.",
                ),
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": (
                        "Total instances along the plane's first axis, including "
                        "the original."
                    ),
                },
                "spacing_mm": _length("Centre-to-centre distance along the first axis."),
                "second_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": (
                        "Total instances along the plane's second axis, for a grid. "
                        "Leave at 1 (the default) for a single row."
                    ),
                },
                "second_spacing_mm": _length(
                    "Centre-to-centre distance along the second axis. Required "
                    "whenever second_count is more than 1."
                ),
                "feature": _FEATURE_NAME,
            },
            required=["plane", "count", "spacing_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_pattern_circular",
        description=(
            "Repeat a feature evenly around the centre of the part -- a bolt circle, "
            "a ring of lightening holes, splines round a hub. The feature must "
            "already be OFF-CENTRE: rotating something that sits on the axis puts "
            "every copy back on top of it, and this tool refuses that rather than "
            "reporting a bolt circle that is not there. Place the seed hole with "
            "catia_hole's inset_mm to set the bolt-circle radius, then repeat it. "
            "Copies that swing off the edge of the part are cut short or dropped "
            "without an error, so keep the bolt-circle radius inside the material "
            "and check volume_mm3 before quoting a hole count."
        ),
        parameters=_object(
            {
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": (
                        "Total instances including the original, spread evenly over "
                        "total_angle_deg. 6 over the default 360 gives holes every 60 degrees."
                    ),
                },
                "plane": _enum(
                    SKETCH_PLANES,
                    "Plane the ring lies in; the copies turn about this plane's "
                    "normal. XY for features on the top of a plate. Default XY.",
                ),
                "total_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": (
                        "Angle the instances are spread over. Default 360 for a full "
                        "circle; use less for an arc of holes."
                    ),
                },
                "feature": _FEATURE_NAME,
            },
            required=["count"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_shell",
        description=(
            "Hollow the part out, leaving walls of a given thickness -- how a cast "
            "housing, a cover or an enclosure is made light without losing its "
            "outside shape. The wall grows inwards, so every outside dimension "
            "already built stays exactly as it is. This bridge cannot open a face "
            "while hollowing, so the result is a closed shell; say so rather than "
            "describing an open box."
        ),
        parameters=_object(
            {"thickness_mm": _length("Wall thickness.", maximum=1_000.0)},
            required=["thickness_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_shaft",
        description=(
            "Revolve a sketch into a solid around the sketch plane's vertical axis "
            "through the origin. This is how turned parts are made: a rectangle "
            "revolved 360 degrees is a cylinder, a profile beside the axis is a "
            "tube. The profile must sit entirely on one side of the axis. Use "
            "catia_pad for prismatic shapes instead."
        ),
        parameters=_object(
            {
                "sketch": _FEATURE_NAME,
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Revolution angle in degrees. Default 360 (full).",
                },
            },
            required=["sketch"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_groove",
        description=(
            "Revolve a sketch around the sketch plane's vertical axis and REMOVE the "
            "swept material -- the revolved counterpart of catia_pocket. Use it for "
            "circumferential grooves, o-ring glands and relief cuts on turned parts. "
            "The part must already have solid material where the groove cuts."
        ),
        parameters=_object(
            {
                "sketch": _FEATURE_NAME,
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Revolution angle in degrees. Default 360 (full).",
                },
            },
            required=["sketch"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_mirror",
        description=(
            "Mirror the whole solid about a named reference plane, so modelling half "
            "a symmetric part and mirroring it replaces modelling both halves. Model "
            "the half on one side of the plane first, then mirror; check the mass "
            "roughly doubled."
        ),
        parameters=_object(
            {
                "plane": _enum(
                    SKETCH_PLANES,
                    "Reference plane to mirror about, e.g. ZX to mirror left-right "
                    "across Y.",
                )
            },
            required=["plane"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_delete_feature",
        description=(
            "Delete one named feature (and anything CATIA rebuilds away with it). "
            "This is the recovery move when a feature went in wrong -- a pad of the "
            "wrong length, a hole in the wrong face -- cheaper than rolling back to a "
            "checkpoint. It is checkpointed automatically first, so the deletion "
            "itself can be undone with catia_restore. Name the feature exactly as "
            "the feature list reports it."
        ),
        parameters=_object(
            {"feature": _FEATURE_NAME},
            required=["feature"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_list_features",
        description=(
            "The part's feature tree -- every sketch, pad, pocket, fillet and so on, "
            "in build order, with the sketch each feature consumed. Call it when "
            "resuming work on an existing part or before catia_delete_feature, so "
            "you name features that really exist."
        ),
        parameters=_object({}),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_measure",
        description=(
            "Mass, volume, bounding box and centre of gravity of the current part, "
            "plus its feature list. Call this after every mutation: it is how you "
            "find out that a pocket cut through the wrong wall, or that a part you "
            "believe is 2 kg is actually 20. Mass is in kilograms and lengths in "
            "millimetres -- report them as given, do not convert."
        ),
        parameters=_object({}),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_capture_view",
        description=(
            "Screenshot the CATIA viewport from a standard viewpoint and store the "
            "image. Call this after a shape change so you can describe what the part "
            "now looks like, and whenever the user asks 'show me'. An isometric view "
            "tells you more than any single orthographic one."
        ),
        parameters=_object(
            {
                "view": _enum(VIEWPOINTS, "Viewpoint to capture from. Default 'iso'."),
                "label": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Short caption stored with the image, e.g. 'after filleting'.",
                },
            }
        ),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_export_step",
        description=(
            "Export the current CATIA part to STEP and register it as a new geometry "
            "version on this conversation's project, ready to mesh and solve. This is "
            "the bridge between modelling and analysis: call it once the shape is "
            "what you intend to test, then run a simulation against the version "
            "number it returns. It takes appreciably longer than the other tools -- "
            "a minute or more on a large part is normal, not a hang."
        ),
        parameters=_object(
            {
                "note": {
                    "type": "string",
                    "maxLength": 500,
                    "description": (
                        "What changed since the last export, recorded on the geometry "
                        "version, e.g. 'added 3 mm fillet at the web/flange corner'."
                    ),
                }
            }
        ),
        tier=CatiaTier.WRITE,
        long_running=True,
    ),
    CatiaToolSpec(
        name="catia_checkpoint",
        description=(
            "Save the document and snapshot it, so it can be rolled back to this "
            "exact state later. Mutating tools checkpoint themselves, so you rarely "
            "need this -- call it explicitly only before a sequence of changes you "
            "want to be able to abandon as a whole, and give it a label that says "
            "what the part looks like right now."
        ),
        parameters=_object(
            {
                "label": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "What state this snapshot captures, e.g. 'before adding ribs'.",
                }
            },
            required=["label"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_restore",
        description=(
            "Roll the document back to a checkpoint, discarding everything modelled "
            "since. This destroys work and cannot itself be undone, so it requires an "
            "approval token the user grants with an explicit click -- ask for one, do "
            "not invent it. Name the checkpoint you mean; a previous catia_checkpoint "
            "result carries its id."
        ),
        parameters=_object(
            {
                "checkpoint_id": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 36,
                    "description": "Id of the checkpoint to roll back to.",
                },
                "approval_token": {
                    "type": "string",
                    # No `minLength`: an empty token has to reach the approval
                    # check, which explains what to do about it ("ask the user
                    # to confirm"). A schema error here would answer a missing
                    # approval with "must be at least 1 character", which tells
                    # the model to invent one.
                    "maxLength": 512,
                    "description": (
                        "Server-signed token proving the user approved this exact "
                        "destructive call. Supplied by the interface; never guessed."
                    ),
                },
            },
            required=["checkpoint_id", "approval_token"],
        ),
        tier=CatiaTier.DESTRUCTIVE,
    ),
    CatiaToolSpec(
        name="catia_update",
        description=(
            "Force CATIA to rebuild the part. Call this when a tool reported that the "
            "part is out of date, or when a measurement disagrees with a change you "
            "just made -- CATIA can defer an update, and a stale model measures as "
            "the old shape."
        ),
        parameters=_object({}),
        tier=CatiaTier.WRITE,
    ),
    # -- driving the interface itself ---------------------------------------
    #
    # Everything above is a semantic operation with the CATIA calls hidden
    # inside it, and that is the right shape for the operations Kryova performs
    # often. It is not a shape that scales to the whole of CATIA: there are
    # thousands of commands, most of them behind a dialog, and hand-writing a
    # tool for each is neither possible nor useful.
    #
    # These eight give the agent the interface itself -- read what is on the
    # menu, press it, read the dialog it opened, fill it in, press OK. That is
    # what an engineer does, and it reaches every command on the seat including
    # ones nobody anticipated, in whatever language the seat is installed in.
    #
    # Two properties make it safe enough to expose. The daemon refuses a short
    # list of commands no checkpoint can undo (see `app/catia_kb/ui.py`
    # `FORBIDDEN_COMMAND_TOKENS`), and `catia_run_command` is auto-checkpointed
    # like every other mutation, so "the agent pressed something unexpected" is
    # recoverable rather than final.
    CatiaToolSpec(
        name="catia_list_commands",
        description=(
            "Read CATIA's live menus: every command on this workstation right now, "
            "with the exact label this seat displays and whether it is available. "
            "Use it whenever you are unsure what a command is called here -- the "
            "interface may be in French, German, Japanese or anything else, and this "
            "reports what is really on the menu rather than a translation. A greyed "
            "command is reported with 'available': false, which is the answer to "
            "'why did nothing happen': CATIA disables a command whose preconditions "
            "are unmet, usually because nothing is selected or the wrong workbench is "
            "active. Filter with `search` rather than reading hundreds of entries."
        ),
        parameters=_object(
            {
                "search": {
                    "type": "string",
                    "maxLength": 60,
                    "description": (
                        "Only commands whose label or menu path contains this. Matched "
                        "without regard to case or accents, so 'fillet' finds 'Congé "
                        "d'arête' only if you search the local word -- prefer a short "
                        "fragment, and omit it entirely to see the whole menu."
                    ),
                },
                "menu": {
                    "type": "string",
                    "maxLength": 60,
                    "description": (
                        "Restrict to one top-level menu by its displayed name (e.g. "
                        "'Insert', 'Insertion', 'Einfügen'). Omit for all of them."
                    ),
                },
            }
        ),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_run_command",
        description=(
            "Press any CATIA command, by its English name -- the bridge translates it "
            "to whatever this seat calls it. This is how you reach the whole of CATIA: "
            "anything with a toolbar button or a menu entry, in any workbench. "
            "Prefer a purpose-built tool (catia_pad, catia_hole, catia_fillet) when "
            "one exists; they take dimensions directly and need no dialog. Use this "
            "for everything else.\n"
            "Most CATIA commands open a dialog and wait. The result tells you whether "
            "one opened and what is in it; when it did, the command has NOT run yet -- "
            "fill the dialog with catia_fill_dialog and confirm it with "
            "catia_dialog_action. Nothing is built until you press OK.\n"
            "Many commands need a selection first (Pad needs a profile, Edge Fillet "
            "needs edges). Select with catia_select, then run the command. If the "
            "result says the command was greyed out, that is the reason."
        ),
        parameters=_object(
            {
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": (
                        "The command's English name as CATIA V5 documents it: 'Pad', "
                        "'Edge Fillet', 'Rectangular Pattern', 'Isolate'. Do not "
                        "translate it yourself and do not guess an internal id."
                    ),
                },
            },
            required=["command"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_describe_dialog",
        description=(
            "Read the dialog CATIA is currently showing: its title, every field with "
            "its current value, and every button. Call it after catia_run_command to "
            "see what the command is asking for, and again after filling a field when "
            "a dialog enables or disables others in response.\n"
            "It answers 'no dialog is open' rather than failing, which is also how you "
            "check whether a command completed on its own. This tool keeps working "
            "when the others report CATIA as unresponsive -- an open dialog is exactly "
            "what makes CATIA unresponsive, and reading it is how you get out."
        ),
        parameters=_object({}),
        tier=CatiaTier.READ,
    ),
    CatiaToolSpec(
        name="catia_fill_dialog",
        description=(
            "Type into the open dialog's fields, by the label shown beside each one. "
            "Give the label exactly as catia_describe_dialog reported it -- it is in "
            "the seat's language, and that is the string the dialog knows.\n"
            "Values go in as text, the way you would type them: '25mm', '4deg', "
            "'true' for a checkbox, or the exact option text for a dropdown. Filling "
            "a field does not run the command; catia_dialog_action does."
        ),
        parameters=_object(
            {
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "description": "One entry per field to set.",
                    "items": _object(
                        {
                            "name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                                "description": "The field's label, as the dialog shows it.",
                            },
                            "value": {
                                "type": "string",
                                "maxLength": 200,
                                "description": (
                                    "What to type. Include the unit for a dimension "
                                    "('25mm'); CATIA reads a bare number in the "
                                    "document's unit, which may not be millimetres."
                                ),
                            },
                        },
                        required=["name", "value"],
                    ),
                }
            },
            required=["fields"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_dialog_action",
        description=(
            "Press a button on the open dialog. 'ok' commits the command and closes "
            "the dialog; 'apply' commits and leaves it open; 'preview' shows the "
            "result without committing; 'cancel' abandons it and changes nothing.\n"
            "Ask for the action, not the label: this seat's OK button may read "
            "'Aceptar' and its Cancel 'Abbrechen', and the bridge knows which is "
            "which. For a button that is none of these -- 'Reverse Direction', "
            "'More>>' -- pass its exact label in `button` instead.\n"
            "Cancel is always safe and is the right move when a dialog is not what "
            "you expected. Never leave a dialog open at the end of a turn: it blocks "
            "every other CATIA operation until someone closes it."
        ),
        parameters=_object(
            {
                "action": _enum(
                    ("ok", "apply", "cancel", "close", "preview", "yes", "no"),
                    "What the button should do.",
                ),
                "button": {
                    "type": "string",
                    "maxLength": 120,
                    "description": (
                        "Exact label of a button that is not one of the standard "
                        "actions. When given, `action` is ignored."
                    ),
                },
            },
            required=["action"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_press_key",
        description=(
            "Send one keystroke to CATIA. A few commands take no dialog and end on a "
            "keypress -- Enter to confirm a chain of picks, Escape to abandon a "
            "command that is waiting for input, Delete to remove what is selected. "
            "Escape is the way out of a command that has left CATIA waiting."
        ),
        parameters=_object(
            {
                "key": _enum(
                    (
                        "enter",
                        "escape",
                        "tab",
                        "delete",
                        "space",
                        "up",
                        "down",
                        "left",
                        "right",
                        "home",
                        "end",
                    ),
                    "Which key to press.",
                )
            },
            required=["key"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_switch_workbench",
        description=(
            "Change the active workbench. A CATIA command only exists while its "
            "workbench is active: Pad is unreachable from Assembly Design and Fillet "
            "on a surface needs Generative Shape Design. When catia_list_commands "
            "cannot find a command you know exists, the workbench is the first thing "
            "to check.\n"
            "The result reports the licence the workbench needs. A seat without that "
            "licence cannot open it however the menu looks, and that is worth telling "
            "the user plainly rather than retrying."
        ),
        parameters=_object(
            {
                "workbench": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": (
                        "The workbench's English name: 'Part Design', 'Generative "
                        "Shape Design', 'Aerospace Sheet Metal Design'."
                    ),
                }
            },
            required=["workbench"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_select",
        description=(
            "Put things into CATIA's selection, which is what most commands act on. "
            "Select a sketch then run Pad; select a face then run Pocket. Name "
            "features exactly as catia_list_features reported them.\n"
            "Selecting changes nothing on its own and is always safe. Call it with an "
            "empty list to clear the selection, which is how you recover when a "
            "command reports the wrong input."
        ),
        parameters=_object(
            {
                "features": {
                    "type": "array",
                    "maxItems": 50,
                    "description": (
                        "Feature or sketch names to select. An empty array clears the "
                        "selection."
                    ),
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                },
                "add": {
                    "type": "boolean",
                    "description": (
                        "Add to what is already selected instead of replacing it. "
                        "Default false."
                    ),
                },
            },
            required=["features"],
        ),
        tier=CatiaTier.WRITE,
    ),
]

TOOL_SPECS_BY_NAME: dict[str, CatiaToolSpec] = {spec.name: spec for spec in CATIA_TOOL_SPECS}


def get_spec(tool: str) -> CatiaToolSpec | None:
    return TOOL_SPECS_BY_NAME.get(tool)
