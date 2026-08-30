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
            "blames a stress concentration on a sharp internal corner -- a fillet is "
            "usually the cheapest fix available, and re-running the simulation "
            "afterwards is how you show it worked."
        ),
        parameters=_object(
            {
                "radius_mm": _length("Fillet radius.", maximum=1_000.0),
                "feature": _FEATURE_NAME,
                "edges": _enum(
                    EDGE_SELECTORS,
                    "Which edges of the feature to round. Default 'all'.",
                ),
            },
            required=["radius_mm"],
        ),
        tier=CatiaTier.WRITE,
    ),
    CatiaToolSpec(
        name="catia_chamfer",
        description=(
            "Break edges with a chamfer of a given length and angle. Use for "
            "assembly lead-ins and deburring callouts; use catia_fillet instead when "
            "the goal is to reduce stress."
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
                "edges": _enum(EDGE_SELECTORS, "Which edges to chamfer. Default 'all'."),
            },
            required=["length_mm"],
        ),
        tier=CatiaTier.WRITE,
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
]

TOOL_SPECS_BY_NAME: dict[str, CatiaToolSpec] = {spec.name: spec for spec in CATIA_TOOL_SPECS}


def get_spec(tool: str) -> CatiaToolSpec | None:
    return TOOL_SPECS_BY_NAME.get(tool)
