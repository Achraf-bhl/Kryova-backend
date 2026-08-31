"""The daemon's own copy of the tool vocabulary, schemas and approval tiers.

This file exists because **the server is not trusted to describe its own
request**. The frames arriving on the socket are shaped by a language model's
output several layers up; treating them as validated because something upstream
said so is exactly the mistake that turns a prompt injection into a destroyed
CAD model. So the daemon looks up the tool here, checks the tier here, and
validates the arguments here, using its own table -- and refuses anything that
is not in it.

Two consequences worth stating plainly:

* An agent that emits `{"tool": "catia_restore", "tier": "read"}` gets nothing:
  the tier is never read off the wire, it is looked up by tool name.
* A tool the server knows about and this table does not is refused. When the
  two versions drift, the daemon fails closed and says so.

`SERVER_FIELDS` is the one concession: the server legitimately adds context the
model never supplies -- the document path to reopen, the checkpoint bytes to
restore, the inline-transfer ceiling. Those keys are enumerated per tool and
everything else is rejected, so "the server may add fields" does not become "any
field is accepted".

Keep in step with `app/catia/tool_specs.py`.
"""

from typing import Any

from .validation import SchemaError, validate

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

SKETCH_PLANES = ["XY", "YZ", "ZX"]
NAMED_FACES = ["top", "bottom", "front", "back", "left", "right"]
FACE_POSITIONS = ["center", "front_left", "front_right", "back_left", "back_right"]
EDGE_SELECTORS = ["all", "vertical", "horizontal", "top", "bottom"]
VIEWPOINTS = ["iso", "front", "back", "top", "bottom", "left", "right"]
PARAMETER_UNITS = ["mm", "deg", "kg"]


class ToolRefused(Exception):
    """The daemon will not run this call. The message goes back as the error."""


def _object(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _length(maximum: float = 10_000.0) -> dict[str, Any]:
    return {"type": "number", "exclusiveMinimum": 0, "maximum": maximum}


_NAME = {"type": "string", "maxLength": 120}

#: tool -> (tier, schema for the model-supplied arguments, server-added keys)
TOOLS: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {
    "catia_new_part": (
        WRITE,
        _object({"name": {"type": "string", "minLength": 1, "maxLength": 120}}, ["name"]),
        (),
    ),
    "catia_open_document": (
        WRITE,
        _object({}),
        ("doc_name", "remote_path", "fallback_checkpoint"),
    ),
    "catia_list_parameters": (READ, _object({}), ()),
    "catia_set_parameter": (
        WRITE,
        _object(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "value": {"type": "number"},
                "unit": {"type": "string", "enum": PARAMETER_UNITS},
            },
            ["name", "value", "unit"],
        ),
        (),
    ),
    "catia_sketch_rectangle": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "width_mm": _length(),
                "height_mm": _length(),
            },
            ["plane", "width_mm", "height_mm"],
        ),
        (),
    ),
    "catia_sketch_circle": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "diameter_mm": _length(),
            },
            ["plane", "diameter_mm"],
        ),
        (),
    ),
    "catia_pad": (
        WRITE,
        _object(
            {
                "sketch": _NAME,
                "length_mm": _length(),
                "symmetric": {"type": "boolean"},
                "reversed": {"type": "boolean"},
            },
            ["sketch", "length_mm"],
        ),
        (),
    ),
    "catia_pocket": (
        WRITE,
        _object(
            {
                "sketch": _NAME,
                "depth_mm": _length(),
                "through_all": {"type": "boolean"},
            },
            ["sketch"],
        ),
        (),
    ),
    "catia_set_material": (
        WRITE,
        _object(
            {
                "material": {"type": "string", "minLength": 1, "maxLength": 60},
                # Supplied by the server from Kryova's material library, never by
                # the model -- which is why it is required rather than optional.
                "density_kg_m3": {"type": "number", "exclusiveMinimum": 0, "maximum": 30_000},
            },
            ["material", "density_kg_m3"],
        ),
        (),
    ),
    "catia_hole": (
        WRITE,
        _object(
            {
                "face": {"type": "string", "enum": NAMED_FACES},
                "position": {"type": "string", "enum": FACE_POSITIONS},
                "diameter_mm": _length(1_000.0),
                "depth_mm": _length(),
                "through_all": {"type": "boolean"},
                "inset_mm": _length(),
            },
            ["face", "position", "diameter_mm"],
        ),
        (),
    ),
    "catia_fillet": (
        WRITE,
        _object(
            {
                "radius_mm": _length(1_000.0),
                "feature": _NAME,
                "edges": {"type": "string", "enum": EDGE_SELECTORS},
            },
            ["radius_mm"],
        ),
        (),
    ),
    "catia_chamfer": (
        WRITE,
        _object(
            {
                "length_mm": _length(1_000.0),
                "angle_deg": {"type": "number", "exclusiveMinimum": 0, "maximum": 89.0},
                "feature": _NAME,
                "edges": {"type": "string", "enum": EDGE_SELECTORS},
            },
            ["length_mm"],
        ),
        (),
    ),
    "catia_sketch_revolve_profile": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "outer_diameter_mm": _length(),
                "length_mm": _length(),
                "inner_diameter_mm": _length(),
            },
            ["plane", "outer_diameter_mm", "length_mm"],
        ),
        (),
    ),
    "catia_sketch_groove_profile": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "shaft_diameter_mm": _length(),
                "width_mm": _length(),
                "depth_mm": _length(),
                "distance_from_end_mm": {"type": "number", "minimum": 0, "maximum": 10_000.0},
            },
            ["plane", "shaft_diameter_mm", "width_mm", "depth_mm", "distance_from_end_mm"],
        ),
        (),
    ),
    "catia_sketch_gear_profile": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "module_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 50.0},
                "teeth": {"type": "integer", "minimum": 6, "maximum": 100},
                "pressure_angle_deg": {"type": "number", "minimum": 10.0, "maximum": 30.0},
            },
            ["plane", "module_mm", "teeth"],
        ),
        (),
    ),
    "catia_pattern_rectangular": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "count": {"type": "integer", "minimum": 2, "maximum": 100},
                "spacing_mm": _length(),
                "second_count": {"type": "integer", "minimum": 1, "maximum": 100},
                "second_spacing_mm": _length(),
                "feature": _NAME,
            },
            ["plane", "count", "spacing_mm"],
        ),
        (),
    ),
    "catia_pattern_circular": (
        WRITE,
        _object(
            {
                "count": {"type": "integer", "minimum": 2, "maximum": 100},
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "total_angle_deg": {"type": "number", "exclusiveMinimum": 0, "maximum": 360.0},
                "feature": _NAME,
            },
            ["count"],
        ),
        (),
    ),
    "catia_shell": (
        WRITE,
        _object({"thickness_mm": _length(1_000.0)}, ["thickness_mm"]),
        (),
    ),
    "catia_sketch_polygon": (
        WRITE,
        _object(
            {
                "plane": {"type": "string", "enum": SKETCH_PLANES},
                "sides": {"type": "integer", "minimum": 3, "maximum": 12},
                "diameter_mm": _length(),
            },
            ["plane", "sides", "diameter_mm"],
        ),
        (),
    ),
    "catia_shaft": (
        WRITE,
        _object(
            {
                "sketch": _NAME,
                "angle_deg": {"type": "number", "exclusiveMinimum": 0, "maximum": 360.0},
            },
            ["sketch"],
        ),
        (),
    ),
    "catia_groove": (
        WRITE,
        _object(
            {
                "sketch": _NAME,
                "angle_deg": {"type": "number", "exclusiveMinimum": 0, "maximum": 360.0},
            },
            ["sketch"],
        ),
        (),
    ),
    "catia_mirror": (
        WRITE,
        _object({"plane": {"type": "string", "enum": SKETCH_PLANES}}, ["plane"]),
        (),
    ),
    "catia_delete_feature": (
        WRITE,
        _object({"feature": _NAME}, ["feature"]),
        (),
    ),
    "catia_list_features": (READ, _object({}), ()),
    "catia_measure": (READ, _object({}), ()),
    "catia_capture_view": (
        READ,
        _object(
            {
                "view": {"type": "string", "enum": VIEWPOINTS},
                "label": {"type": "string", "maxLength": 120},
            }
        ),
        ("max_inline_bytes",),
    ),
    "catia_export_step": (
        WRITE,
        _object({"note": {"type": "string", "maxLength": 500}}),
        ("max_inline_bytes",),
    ),
    "catia_checkpoint": (
        WRITE,
        _object({"label": {"type": "string", "minLength": 1, "maxLength": 200}}, ["label"]),
        ("max_inline_bytes",),
    ),
    "catia_restore": (
        DESTRUCTIVE,
        # The model's own arguments are replaced by the server with a resolved
        # checkpoint, so there is nothing model-supplied left to validate here;
        # `checkpoint` is checked as a server field below.
        _object({}),
        ("checkpoint",),
    ),
    "catia_update": (WRITE, _object({}), ()),
}

#: `catia_status` is answered by the server and never reaches a device. If one
#: arrives, something is wrong upstream and it is refused rather than guessed at.
SERVER_ONLY = frozenset({"catia_status"})


def tier_of(tool: str) -> str:
    entry = TOOLS.get(tool)
    if entry is None:
        raise ToolRefused(f"{tool!r} is not a tool this bridge implements.")
    return entry[0]


def check_call(tool: str, arguments: Any, *, approval_token: str | None) -> dict[str, Any]:
    """Validate one incoming call and return the arguments to execute.

    Raises `ToolRefused` with a message the server relays verbatim to the agent.
    """
    if tool in SERVER_ONLY:
        raise ToolRefused(f"{tool} is answered by the server and must not be sent to a bridge.")
    entry = TOOLS.get(tool)
    if entry is None:
        raise ToolRefused(
            f"{tool!r} is not a tool this bridge implements. The bridge may be older "
            "than the server; update it."
        )
    tier, schema, server_fields = entry

    if not isinstance(arguments, dict):
        raise ToolRefused(f"{tool}: arguments must be an object.")

    # The tier comes from this table, never from the frame. A caller cannot
    # relabel a destructive operation as a read one.
    if tier == DESTRUCTIVE and not approval_token:
        raise ToolRefused(
            f"{tool} is a destructive operation and arrived without a server-signed "
            "approval token. Refused."
        )

    model_args = {k: v for k, v in arguments.items() if k not in server_fields}
    try:
        validate(model_args, schema)
    except SchemaError as exc:
        raise ToolRefused(f"{tool}: {exc}") from exc

    if tool == "catia_restore":
        checkpoint = arguments.get("checkpoint")
        if not isinstance(checkpoint, dict) or not checkpoint.get("checkpoint_id"):
            raise ToolRefused("catia_restore: the server did not resolve a checkpoint.")

    return dict(arguments)
