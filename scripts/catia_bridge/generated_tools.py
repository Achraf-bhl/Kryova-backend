"""The daemon's own copy of the tool vocabulary, schemas and tiers.

GENERATED FILE — DO NOT EDIT BY HAND.

    venv/bin/python scripts/gen_bridge_tools.py

Regenerate it after changing anything under `app/catia/ops/`.
`tests/test_bridge_table_is_generated.py` fails if this file is stale, so a
forgotten regeneration is caught before it becomes a daemon that refuses a tool
the server just started sending.

This file exists so the daemon can validate a call **without importing the
server**. Editing it by hand defeats both halves of that: the daemon stops
matching the server, and the next regeneration silently discards the edit.

Schema shapes here use only the keywords `validation.validate` implements —
type, properties, required, additionalProperties, enum, minimum, maximum,
exclusiveMinimum, minLength, maxLength, items, minItems, maxItems. A keyword
outside that set is not enforced, so the generator refuses to emit one.
"""

from typing import Any

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"


#: tool -> (tier, schema the daemon validates, keys the server may add)
TOOLS: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {
    "catia_new_part": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the part.",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_open_document": (
        WRITE,
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        ("doc_name", "remote_path", "fallback_checkpoint"),
    ),
    "catia_import": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The uploaded file to import, by name. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "format": {
                    "type": "string",
                    "enum": [
                        "step",
                        "iges",
                        "stl",
                        "dxf",
                        "dwg",
                        "parasolid",
                        "acis",
                        "jt",
                        "3dxml",
                        "vrml",
                        "vda",
                        "catpart",
                        "catproduct",
                        "v4model",
                        "cgr",
                    ],
                    "description": "Format, when the extension does not say.",
                },
                "import_as": {
                    "type": "string",
                    "enum": ["solid", "surface", "wireframe", "reference"],
                    "description": "How to bring the geometry in. Default solid where the file allows it.",
                },
                "heal": {
                    "type": "boolean",
                    "description": "Run healing on import to close small gaps. Default true for surfaces.",
                },
                "scale": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1000.0,
                    "description": "Scale the geometry on import.",
                },
            },
            "required": ["file"],
            "additionalProperties": False,
        },
        ("content_b64", "content_hash", "filename"),
    ),
    "catia_export": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": [
                        "step",
                        "iges",
                        "stl",
                        "dxf",
                        "dwg",
                        "parasolid",
                        "vrml",
                        "cgr",
                        "3dxml",
                        "pdf3d",
                    ],
                    "description": "Which format. Default step.",
                },
                "note": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "A note to record with the export.",
                },
                "step_schema": {
                    "type": "string",
                    "enum": ["ap203", "ap214", "ap242"],
                    "description": "STEP application protocol. Default ap214.",
                },
                "tolerance_mm": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 10.0,
                    "description": "Tessellation tolerance, for STL and VRML. mm.",
                },
                "binary": {
                    "type": "boolean",
                    "description": "Write STL as binary rather than ASCII. Default true.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        ("max_inline_bytes",),
    ),
    "catia_export_step": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "A note to record with the export.",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        ("max_inline_bytes",),
    ),
    "catia_set_material": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "material": {
                    "type": "string",
                    "enum": [
                        "aluminium-6061-t6",
                        "aluminium-7075-t6",
                        "steel-1018",
                        "stainless-304",
                        "titanium-ti6al4v",
                        "abs",
                        "pla",
                        "nylon-pa12",
                    ],
                    "description": "Which material from the library.",
                },
                "density_kg_m3": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30000.0,
                    "description": "Density from Kryova's material library, in kg/m³.",
                },
            },
            "required": ["material", "density_kg_m3"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_capture_view": (
        READ,
        {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["iso", "front", "back", "top", "bottom", "left", "right"],
                    "description": "Which viewpoint. Default iso.",
                },
                "label": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "A caption to store with the image.",
                },
                "fit": {
                    "type": "boolean",
                    "description": "Zoom to fit the whole part. Default true.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["shaded", "shaded_with_edges", "wireframe", "hidden_line"],
                    "description": "Display mode. Default shaded_with_edges.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        ("max_inline_bytes",),
    ),
    "catia_checkpoint": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "What this checkpoint represents.",
                }
            },
            "required": ["label"],
            "additionalProperties": False,
        },
        ("max_inline_bytes",),
    ),
    "catia_restore": (
        DESTRUCTIVE,
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        ("checkpoint",),
    ),
    "catia_list_features": (
        READ,
        {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to one body or geometrical set. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "kind": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Only report features of this type, e.g. 'Pad'.",
                },
                "include_sketches": {
                    "type": "boolean",
                    "description": "Include sketches. Default true.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_measure": (
        READ,
        {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Measure one body rather than the whole part. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "include_inertia": {
                    "type": "boolean",
                    "description": "Also report the inertia matrix. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_measure_between": (
        READ,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two elements to measure between.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["minimum_distance", "angle", "closest_points"],
                    "description": "What to measure. Default minimum_distance.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_measure_item": (
        READ,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The element to measure. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": ["element"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_select": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "features": {
                    "type": "array",
                    "maxItems": 50,
                    "description": "Feature or sketch names to select. An empty array clears the selection.",
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                },
                "add": {
                    "type": "boolean",
                    "description": "Add to what is already selected instead of replacing it. Default false.",
                },
            },
            "required": ["features"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_delete_feature": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to delete. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "with_children": {
                    "type": "boolean",
                    "description": "Also delete everything that depends on it. Default false.",
                },
            },
            "required": ["feature"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_update": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Update only this feature and its parents. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_analysis_part": (
        READ,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["draft", "thickness", "curvature", "validity"],
                    "description": "Which analysis to run.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "Pulling direction, for a draft analysis. One of: XY, YZ, ZX.",
                },
                "minimum_mm": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "Flag anything below this, for a thickness analysis.",
                },
                "faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Restrict the analysis to these faces.",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or planar face to sketch on. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the sketch. CATIA numbers it if omitted.",
                },
                "origin": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the sketch's own (0, 0) sits on the support. Defaults to the support's own origin. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
            },
            "required": ["support"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_close": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to close. Defaults to the open one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_analysis": (
        READ,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to analyse. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_point": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the point. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["at"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_line": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "start": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the line begins. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "end": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the line ends. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_polyline": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                        "description": "One vertex. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                    },
                    "description": "The vertices, in order.",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Join the last point back to the first. Default false.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_axis": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "start": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "One end of the axis. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "end": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "The other end. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_circle": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Diameter of the circle. Millimetres.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the circle. Defaults to the sketch origin. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "plane": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Support to sketch on when no sketch is open. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["diameter_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_arc": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the arc. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius of the arc. Millimetres.",
                },
                "start_angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Angle at which the arc starts. Degrees; negative reverses the direction.",
                },
                "end_angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Angle at which the arc ends. Degrees; negative reverses the direction.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["centre", "radius_mm", "start_angle_deg", "end_angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_arc_three_point": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "start": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the arc begins. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "through": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "A point the arc passes through. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "end": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the arc ends. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["start", "through", "end"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_ellipse": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the ellipse. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "major_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Semi-major axis length. Millimetres.",
                },
                "minor_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Semi-minor axis length. Millimetres.",
                },
                "rotation_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Rotation of the major axis. Default 0. Degrees; negative reverses the direction.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["centre", "major_radius_mm", "minor_radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_spline": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 2,
                        "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                        "description": "One vertex. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                    },
                    "description": "Points the spline passes through, in order.",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Close the spline into a loop. Default false.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_conic": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "start": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "One endpoint. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "end": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "The other endpoint. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "tangent_intersection": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the two end tangents cross. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "parameter": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 0.999,
                    "description": "Conic shape parameter: <0.5 ellipse, 0.5 parabola, >0.5 hyperbola. Default 0.5.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["start", "end", "tangent_intersection"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_rectangle": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "width_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Width, along the sketch's horizontal axis. Millimetres.",
                },
                "height_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Height, along the sketch's vertical axis. Millimetres.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the rectangle. Defaults to the sketch origin. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "rotation_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Rotation about the centre. Default 0. Degrees; negative reverses the direction.",
                },
                "plane": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Support to sketch on when no sketch is open. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["width_mm", "height_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_parallelogram": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "corner": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "The corner the two sides run from. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "width_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Length of the first side. Millimetres.",
                },
                "height_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Length of the second side. Millimetres.",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 179.0,
                    "description": "Angle between the two sides. Degrees.",
                },
                "rotation_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Rotation of the first side. Default 0. Degrees; negative reverses the direction.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["corner", "width_mm", "height_mm", "angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_polygon": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sides": {
                    "type": "integer",
                    "minimum": 3,
                    "maximum": 64,
                    "description": "Number of sides.",
                },
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Diameter of the circle the corners sit on. Millimetres.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the polygon. Defaults to the sketch origin. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "rotation_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Rotation about the centre. Default 0. Degrees; negative reverses the direction.",
                },
                "plane": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Support to sketch on when no sketch is open. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["sides", "diameter_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_slot": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "start": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the first end. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "end": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the second end. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "width_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Width across the slot — the diameter of its ends. Millimetres.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["start", "end", "width_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_corner": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius of the rounded corner. Millimetres.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The two sketch elements meeting at the corner.",
                },
                "trim": {
                    "type": "boolean",
                    "description": "Trim both elements back to the arc. Default true.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["radius_mm", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_chamfer": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Length of the chamfer along the first element. Millimetres.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The two sketch elements meeting at the corner.",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 179.0,
                    "description": "Angle of the chamfer. Default 45. Degrees.",
                },
                "second_length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Length along the second element, instead of an angle. Millimetres.",
                },
                "trim": {
                    "type": "boolean",
                    "description": "Trim both elements back to the chamfer. Default true.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["length_mm", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_trim": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The two elements to trim to each other.",
                },
                "keep": {
                    "type": "string",
                    "enum": ["both", "first", "second"],
                    "description": "Which side of the intersection survives. Default both.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_offset": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The sketch elements to offset.",
                },
                "distance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "How far to offset. Millimetres.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Offset to the other side. Default false.",
                },
                "propagate": {
                    "type": "boolean",
                    "description": "Carry the offset along tangent neighbours. Default true.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "distance_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_mirror": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to mirror.",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The line or axis to mirror about. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "keep_original": {
                    "type": "boolean",
                    "description": "Keep the original as well. Default true.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "axis"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_translate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to move.",
                },
                "offset": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "How far to move them, as a 2D vector. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "copies": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Number of copies to leave behind. Default 0 (move).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "offset"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_rotate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to rotate.",
                },
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "The point to rotate about. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "How far to rotate. Degrees; negative reverses the direction.",
                },
                "copies": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Number of copies to leave behind. Default 0 (move).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "centre", "angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_scale": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to scale.",
                },
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "The point to scale about. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "factor": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 100.0,
                    "description": "Scale factor; 1.0 leaves the size unchanged.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "centre", "factor"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_project": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The 3D edges or faces to project.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["normal", "along_direction", "silhouette"],
                    "description": "How to project. Default normal (straight onto the sketch plane).",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Bring it in as construction geometry. Default false.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_intersect_3d": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The 3D elements to intersect with the plane.",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Bring it in as construction geometry. Default false.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_pattern": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to repeat.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["rectangular", "circular"],
                    "description": "Grid shape.",
                },
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": "How many instances along the first direction.",
                },
                "spacing_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Gap between instances, for a rectangular grid. Millimetres.",
                },
                "second_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Instances along the second direction. Default 1.",
                },
                "second_spacing_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Gap along the second direction. Millimetres.",
                },
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Point to repeat around, for a circular grid. Default the sketch origin. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "total_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Angle the circular grid spans. Default 360. Degrees.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "construction": {
                    "type": "boolean",
                    "description": "Draw as a construction element — geometry that guides other geometry but is not part of the profile and is never padded. Default false.",
                },
            },
            "required": ["elements", "kind", "count"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_constrain": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "coincidence",
                        "concentricity",
                        "tangency",
                        "parallelism",
                        "perpendicularity",
                        "horizontal",
                        "vertical",
                        "symmetry",
                        "equidistant",
                        "fix",
                    ],
                    "description": "Which constraint to apply.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to constrain: one for horizontal/vertical/fix, two for most others, three for symmetry (the two elements then the axis).",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["kind", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_dimension": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["distance", "length", "radius", "diameter", "angle"],
                    "description": "Which dimension to apply.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The one or two elements being dimensioned.",
                },
                "value": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "The value to drive it to — millimetres for a length, distance, radius or diameter; degrees for an angle.",
                },
                "parameter_name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Publish the dimension under this name so it can be driven later.",
                },
                "reference": {
                    "type": "boolean",
                    "description": "Create it as a reference (driven) dimension. Default false.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch to draw in. Defaults to the most recent sketch. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["kind", "elements", "value"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_revolve_profile": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "Plane to draw on. The part is revolved about this plane's vertical axis and grows along it — ZX gives a shaft lying along Z. One of: XY, YZ, ZX.",
                },
                "outer_diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Outside diameter of the finished part. Millimetres.",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Length along the revolution axis. Millimetres.",
                },
                "inner_diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Bore diameter, for a tube. Omit for a solid rod. Millimetres.",
                },
            },
            "required": ["plane", "outer_diameter_mm", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_groove_profile": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "Plane to draw on. Use the same one the shaft used. One of: XY, YZ, ZX.",
                },
                "shaft_diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Outside diameter of the shaft being cut into. Millimetres.",
                },
                "width_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Width of the groove along the axis. Millimetres.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How deep the groove cuts into the surface. Millimetres.",
                },
                "distance_from_end_mm": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 10000.0,
                    "description": "Distance from the shaft's near end to the groove. Millimetres.",
                },
            },
            "required": [
                "plane",
                "shaft_diameter_mm",
                "width_mm",
                "depth_mm",
                "distance_from_end_mm",
            ],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sketch_gear_profile": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "Plane to draw the gear on. One of: XY, YZ, ZX.",
                },
                "module_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 50.0,
                    "description": "Gear module — pitch diameter divided by tooth count. Millimetres.",
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
                    "description": "Pressure angle. 20 degrees is the modern standard. Degrees.",
                },
            },
            "required": ["plane", "module_mm", "teeth"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_offset": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or planar face to offset from. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far to offset. Millimetres; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane. CATIA numbers it if omitted.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Offset to the other side. Default false.",
                },
            },
            "required": ["reference", "distance_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_angle": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane to measure the angle from. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The line or axis the plane rotates about. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Angle from the reference plane. Degrees; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane.",
                },
            },
            "required": ["reference", "axis", "angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_through_points": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly three point names the plane passes through.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_normal_to_curve": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to stand perpendicular to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Where on the curve. Defaults to its start. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane.",
                },
            },
            "required": ["curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_tangent_to_surface": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to be tangent to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point on the surface. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane.",
                },
            },
            "required": ["surface", "point"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_plane_mean": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The points to fit through.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the plane.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_planes_between": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane at one end. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "second": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane at the other end. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "How many planes to create between them.",
                },
            },
            "required": ["first", "second", "count"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_point_at": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "at": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the point. [x, y, z] in millimetres, in the part's own frame.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Measure the coordinates from this point instead of the origin. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["at"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_point_on_curve": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to sit on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "ratio": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Proportion along the curve, 0 to 1. A ratio, where 1.0 leaves the size unchanged.",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "Distance along the curve from its start. Millimetres; negative reverses the direction.",
                },
                "from_end": {
                    "type": "boolean",
                    "description": "Measure from the far end instead. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
            },
            "required": ["curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_point_on_surface": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to sit on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point to measure from. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way to move along the surface. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far to move. Millimetres; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
            },
            "required": ["surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_point_centre": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The circle, arc, sphere or face. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
            },
            "required": ["element"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_point_between": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two point names.",
                },
                "ratio": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Proportion from the first point. Default 0.5 (midpoint). A ratio, where 1.0 leaves the size unchanged.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_line_between": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two point names.",
                },
                "extend_start_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "Extend beyond the first point. Millimetres; negative reverses the direction.",
                },
                "extend_end_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "Extend beyond the second point. Millimetres; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the line.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_line_direction": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Where the line starts. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way it runs. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How long it is. Millimetres.",
                },
                "both_sides": {
                    "type": "boolean",
                    "description": "Extend the same length backwards too. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the line.",
                },
            },
            "required": ["point", "direction", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_line_normal": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to stand off. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point on it. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How long the line is. Millimetres.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the line.",
                },
            },
            "required": ["surface", "point", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_line_tangent": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to be tangent to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point on it. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How long the line is. Millimetres.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the line.",
                },
            },
            "required": ["curve", "point", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_axis_system": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point the axis system sits at. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "x_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Direction of the local X axis. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "y_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Direction of the local Y axis. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the axis system.",
                },
                "set_current": {
                    "type": "boolean",
                    "description": "Make it the active axis system for what follows. Default false.",
                },
            },
            "required": ["origin"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_list_faces": (
        READ,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to faces created by this feature. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "kind": {
                    "type": "string",
                    "enum": ["all", "planar", "cylindrical", "conical", "spherical", "other"],
                    "description": "Only report faces of this kind. Default all.",
                },
                "min_area_mm2": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Ignore faces smaller than this, in mm². Default 0. Millimetres.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_list_edges": (
        READ,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to edges created by this feature. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to edges bounding this face. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["all", "linear", "circular", "convex", "concave"],
                    "description": "Only report edges of this kind. Default all.",
                },
                "min_length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Ignore edges shorter than this. Default 0. Millimetres.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pad": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to extrude. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far to extrude. Millimetres.",
                },
                "thin": {
                    "type": "boolean",
                    "description": "Build a thin-walled pad instead of a solid one. Default false.",
                },
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Wall thickness, for a thin pad. Millimetres.",
                },
                "limit": {
                    "type": "string",
                    "enum": [
                        "dimension",
                        "up_to_next",
                        "up_to_last",
                        "up_to_plane",
                        "up_to_surface",
                    ],
                    "description": "How the feature ends. Default dimension.",
                },
                "up_to": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane, face or surface to stop at, for an up_to limit. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Extent in the opposite direction, for a two-sided feature. Millimetres.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Build in the opposite direction. Default false.",
                },
                "symmetric": {
                    "type": "boolean",
                    "description": "Extend equally both ways from the profile. Default false.",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Build along this direction instead of the profile's normal. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
            },
            "required": ["sketch", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pocket": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to cut. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How deep to cut. Omit with through_all. Millimetres.",
                },
                "through_all": {
                    "type": "boolean",
                    "description": "Cut all the way through the part. Default false.",
                },
                "thin": {
                    "type": "boolean",
                    "description": "Cut a thin-walled slot instead of the full profile. Default false.",
                },
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Wall thickness, for a thin pocket. Millimetres.",
                },
                "limit": {
                    "type": "string",
                    "enum": [
                        "dimension",
                        "up_to_next",
                        "up_to_last",
                        "up_to_plane",
                        "up_to_surface",
                    ],
                    "description": "How the feature ends. Default dimension.",
                },
                "up_to": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane, face or surface to stop at, for an up_to limit. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Extent in the opposite direction, for a two-sided feature. Millimetres.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Build in the opposite direction. Default false.",
                },
                "symmetric": {
                    "type": "boolean",
                    "description": "Extend equally both ways from the profile. Default false.",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Build along this direction instead of the profile's normal. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
            },
            "required": ["sketch"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_shaft": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to revolve. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "How far to revolve. Default 360 (a full turn). Degrees.",
                },
                "second_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Sweep in the opposite direction as well. Degrees.",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Revolve about this axis instead of the sketch's own. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "thin": {"type": "boolean", "description": "Build it thin-walled. Default false."},
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Wall thickness, for a thin shaft. Millimetres.",
                },
            },
            "required": ["sketch"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_groove": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to revolve and remove. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "How far to revolve. Default 360 (a full turn). Degrees.",
                },
                "second_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Sweep in the opposite direction as well. Degrees.",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Revolve about this axis instead of the sketch's own. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["sketch"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_hole": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "face": {
                    "type": "string",
                    "enum": ["top", "bottom", "front", "back", "left", "right"],
                    "description": "Which face of the part's bounding box.",
                },
                "position": {
                    "type": "string",
                    "enum": ["center", "front_left", "front_right", "back_left", "back_right"],
                    "description": "Where on that face.",
                },
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Diameter of the hole. Millimetres.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How deep. Omit with through_all. Millimetres.",
                },
                "through_all": {
                    "type": "boolean",
                    "description": "Drill all the way through. Default true.",
                },
                "inset_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far in from the face's edges the corner positions sit. Millimetres.",
                },
            },
            "required": ["face", "position", "diameter_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_fillet": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius to round to. Millimetres.",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to edges of this feature. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "edges": {
                    "type": "string",
                    "enum": ["all", "vertical", "horizontal", "top", "bottom", "convex", "concave"],
                    "description": "Which group of edges. Default all.",
                },
                "propagation": {
                    "type": "string",
                    "enum": ["tangency", "minimal", "intersection"],
                    "description": "How the fillet carries onto neighbours. Default tangency.",
                },
            },
            "required": ["radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_chamfer": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Length of the bevel. Millimetres.",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 89.0,
                    "description": "Angle of the bevel. Default 45. Degrees.",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict to edges of this feature. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "edges": {
                    "type": "string",
                    "enum": ["all", "vertical", "horizontal", "top", "bottom", "convex", "concave"],
                    "description": "Which group of edges. Default all.",
                },
                "second_length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Length on the second face, instead of giving an angle. Millimetres.",
                },
            },
            "required": ["length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_shell": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Wall thickness. Millimetres.",
                },
                "outward": {
                    "type": "boolean",
                    "description": "Add the wall outside the surface instead of inside. Default false.",
                },
            },
            "required": ["thickness_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_mirror": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane to mirror about. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Mirror only this feature, not the whole body. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["plane"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pattern_rectangular": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "plane": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "The plane whose two axes the grid runs along. One of: XY, YZ, ZX.",
                },
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": "How many instances along the first direction.",
                },
                "spacing_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Gap between instances along the first direction. Millimetres.",
                },
                "second_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Instances along the second direction. Default 1.",
                },
                "second_spacing_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Gap along the second direction. Millimetres.",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to repeat. Defaults to the last one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Run the grid the other way. Default false.",
                },
            },
            "required": ["plane", "count", "spacing_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pattern_circular": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": "How many instances in total.",
                },
                "plane": {
                    "type": "string",
                    "enum": ["XY", "YZ", "ZX"],
                    "description": "The plane the circle lies in. Default XY. One of: XY, YZ, ZX.",
                },
                "total_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Angle the instances spread over. Default 360. Degrees.",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to repeat. Defaults to the last one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Rotate about this axis instead of the plane's normal. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius of the circle, when it is not taken from the feature. Millimetres.",
                },
            },
            "required": ["count"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_rib": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to sweep. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "centre_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The path to sweep it along. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "control": {
                    "type": "string",
                    "enum": ["keep_angle", "pulling_direction", "reference_surface"],
                    "description": "How the profile is oriented as it travels. Default keep_angle.",
                },
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface or direction the control uses. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "thick": {
                    "type": "boolean",
                    "description": "Build it as a thin-walled sweep. Default false.",
                },
            },
            "required": ["profile", "centre_curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_slot": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed profile to sweep. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "centre_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The path to sweep it along. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "control": {
                    "type": "string",
                    "enum": ["keep_angle", "pulling_direction", "reference_surface"],
                    "description": "How the profile is oriented as it travels. Default keep_angle.",
                },
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface or direction the control uses. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["profile", "centre_curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_stiffener": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The open profile line. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Thickness of the stiffener. Millimetres.",
                },
                "symmetric": {
                    "type": "boolean",
                    "description": "Thicken equally both sides of the profile. Default true.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Build on the other side. Default false.",
                },
            },
            "required": ["profile", "thickness_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_multi_section_solid": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The closed profiles to loft through, in order.",
                },
                "guides": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Curves that steer the surface between sections.",
                },
                "spine": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A curve the sections stay normal to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Close the loft back onto its first section. Default false.",
                },
                "remove": {
                    "type": "boolean",
                    "description": "Remove the lofted volume instead of adding it. Default false.",
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_solid_combine": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first_profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The first closed profile. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The second closed profile. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "first_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Extrusion direction of the first profile. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "second_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Extrusion direction of the second profile. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
            },
            "required": ["first_profile", "second_profile"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pad_drafted_filleted": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The profile to pad. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far to pad. Millimetres.",
                },
                "draft_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 89.0,
                    "description": "Draft angle on the sides. Degrees.",
                },
                "neutral": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane the draft pivots about. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "lateral_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius on the vertical edges. Millimetres.",
                },
                "top_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius where the pad meets its top face. Millimetres.",
                },
                "bottom_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius where the pad meets the part. Millimetres.",
                },
            },
            "required": ["sketch", "length_mm", "draft_angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_hole_at": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The face to drill into. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "at": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the hole centre sits. [x, y, z] in millimetres, in the part's own frame.",
                },
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Diameter of the hole. Millimetres.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How deep. Omit with through_all. Millimetres.",
                },
                "through_all": {
                    "type": "boolean",
                    "description": "Drill all the way through. Default false.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["simple", "tapered", "counterbored", "countersunk", "counterdrilled"],
                    "description": "Hole type. Default simple.",
                },
                "head_diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Diameter of the counterbore or countersink. Millimetres.",
                },
                "head_depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Depth of the counterbore. Millimetres.",
                },
                "head_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 179.0,
                    "description": "Included angle of the countersink. Degrees.",
                },
                "bottom_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 179.0,
                    "description": "Included angle of the drill point. Degrees.",
                },
                "thread": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Thread designation, e.g. 'M6x1' or 'ISO metric M8'.",
                },
                "thread_depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far the thread runs down the hole. Millimetres.",
                },
            },
            "required": ["face", "at", "diameter_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_hole_pattern": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The face to drill into. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "points": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {
                        "type": "array",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                        "description": "One hole centre. [x, y, z] in millimetres, in the part's own frame.",
                    },
                    "description": "Where each hole goes.",
                },
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Diameter of every hole. Millimetres.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How deep. Omit with through_all. Millimetres.",
                },
                "through_all": {
                    "type": "boolean",
                    "description": "Drill all the way through. Default true.",
                },
                "thread": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Thread designation to tap every hole to.",
                },
            },
            "required": ["face", "points", "diameter_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_thread": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The cylindrical face to thread. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "designation": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "Thread designation, e.g. 'M10x1.5'.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far along the face the thread runs. Millimetres.",
                },
                "pitch_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Pitch, when the designation does not imply one. Millimetres.",
                },
                "left_handed": {
                    "type": "boolean",
                    "description": "Cut it left-handed. Default false.",
                },
                "tap": {
                    "type": "boolean",
                    "description": "It is an internal tap rather than an external thread. Default true.",
                },
            },
            "required": ["face", "designation"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_fillet_edges": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "edges": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "edge": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                                "description": "Which edge. Either an edge group (all, vertical, horizontal, top, bottom, convex, concave) or a specific edge id reported by catia_list_edges.",
                            },
                            "radius_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1000.0,
                                "description": "Radius for this edge. Millimetres.",
                            },
                        },
                        "required": ["edge", "radius_mm"],
                        "additionalProperties": False,
                    },
                    "description": "Each edge and the radius to round it to.",
                },
                "propagation": {
                    "type": "string",
                    "enum": ["tangency", "minimal", "intersection"],
                    "description": "How the fillet carries onto neighbours. Default tangency.",
                },
                "edge_relimitation": {
                    "type": "boolean",
                    "description": "Trim the fillet back to the edge ends. Default false.",
                },
            },
            "required": ["edges"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_fillet_variable": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "edge": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The edge to round. Either an edge group (all, vertical, horizontal, top, bottom, convex, concave) or a specific edge id reported by catia_list_edges.",
                },
                "radii": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "at_ratio": {
                                "type": "number",
                                "minimum": 0.001,
                                "maximum": 100.0,
                                "description": "Where along the edge, 0 at the start and 1 at the end. A ratio, where 1.0 leaves the size unchanged.",
                            },
                            "radius_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1000.0,
                                "description": "Radius at that point. Millimetres.",
                            },
                        },
                        "required": ["at_ratio", "radius_mm"],
                        "additionalProperties": False,
                    },
                    "description": "The radius at each point along the edge.",
                },
                "variation": {
                    "type": "string",
                    "enum": ["cubic", "linear"],
                    "description": "How the radius blends between points. Default cubic.",
                },
            },
            "required": ["edge", "radii"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_fillet_face": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first_face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "One face. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "second_face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The other face. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius of the blend. Millimetres.",
                },
                "hold_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A curve the fillet must pass through. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["first_face", "second_face", "radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_fillet_tritangent": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "faces": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly three face names.",
                },
                "removed_face": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which of the three is consumed. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                },
            },
            "required": ["faces", "removed_face"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_draft": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The faces to draft.",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 89.0,
                    "description": "Draft angle. Degrees.",
                },
                "neutral": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or face that keeps its dimensions. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "pulling_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Mould opening direction. Defaults to the neutral normal. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "parting": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A parting element that splits the draft. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["standard", "reflect_line", "variable"],
                    "description": "Draft mode. Default standard.",
                },
            },
            "required": ["faces", "angle_deg", "neutral"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_shell_faces": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Default wall thickness. Millimetres.",
                },
                "open_faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Faces to remove, leaving the part open there.",
                },
                "outward": {
                    "type": "boolean",
                    "description": "Add the wall outside the surface instead of inside. Default false.",
                },
                "face_thicknesses": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "face": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                                "description": "Which face. Either a bounding-box face name (top, bottom, front, back, left, right) or a face reported by catia_list_faces.",
                            },
                            "thickness_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 500.0,
                                "description": "Thickness for this face. Millimetres.",
                            },
                        },
                        "required": ["face", "thickness_mm"],
                        "additionalProperties": False,
                    },
                    "description": "Per-face thickness overrides.",
                },
            },
            "required": ["thickness_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_thickness": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The faces to offset.",
                },
                "thickness_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How much to add; negative removes. Millimetres; negative reverses the direction.",
                },
            },
            "required": ["faces", "thickness_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_remove_face": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The faces to remove.",
                },
                "keep_faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Faces to extend to close the gap.",
                },
            },
            "required": ["faces"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_replace_face": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "faces": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The faces to replace.",
                },
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to replace them with. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Keep the other side of the surface. Default false.",
                },
            },
            "required": ["faces", "surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_body_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the body.",
                },
                "activate": {
                    "type": "boolean",
                    "description": "Make it the body features go into. Default true.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_body_activate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to work in. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": ["body"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_boolean": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "remove", "intersect", "union_trim", "assemble"],
                    "description": "Which boolean to apply.",
                },
                "tool_body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body being combined in. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "target_body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to combine into. Defaults to the main body. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["operation", "tool_body"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_geometrical_set": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the set.",
                },
                "ordered": {
                    "type": "boolean",
                    "description": "Create an ordered geometrical set. Default false.",
                },
                "activate": {
                    "type": "boolean",
                    "description": "Make it the set new geometry goes into. Default true.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_translate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way to move. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far. Millimetres; negative reverses the direction.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to move. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["direction", "distance_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_rotate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The line or axis to rotate about. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "How far to rotate. Degrees; negative reverses the direction.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to rotate. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["axis", "angle_deg"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_symmetry": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane, point or line to reflect about. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to reflect. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_scale": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or point to scale about. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "factor": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Scale factor. A ratio, where 1.0 leaves the size unchanged.",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to scale. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["reference", "factor"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_affinity": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "x_factor": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Scale along the local X axis. A ratio, where 1.0 leaves the size unchanged.",
                },
                "y_factor": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Scale along the local Y axis. A ratio, where 1.0 leaves the size unchanged.",
                },
                "z_factor": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Scale along the local Z axis. A ratio, where 1.0 leaves the size unchanged.",
                },
                "axis_system": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The frame to scale in. Defaults to the part origin. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The body to scale. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["x_factor", "y_factor", "z_factor"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pattern_user": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "positions": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sketch whose points give the positions. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to repeat. Defaults to the last one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "anchor": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The point in the sketch the original sits on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["positions"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_pattern_explode": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The pattern to explode. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_feature_rename": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to rename. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Its new name.",
                },
            },
            "required": ["feature", "name"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_feature_activate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to change. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "active": {
                    "type": "boolean",
                    "description": "True to activate, false to deactivate.",
                },
            },
            "required": ["feature", "active"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_feature_reorder": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to move. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "after": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature it should follow. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["feature", "after"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_feature_parents": (
        READ,
        {
            "type": "object",
            "properties": {
                "feature": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The feature to inspect. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "depth": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How many levels to walk. Default 1.",
                },
            },
            "required": ["feature"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_circle": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "centre_radius",
                        "three_points",
                        "centre_point",
                        "bitangent",
                        "tritangent",
                    ],
                    "description": "How the circle is defined.",
                },
                "centre": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Centre point. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius. Millimetres.",
                },
                "points": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The defining points, for the three-point kind.",
                },
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or surface the circle lies on. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "start_angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Trim the circle to an arc starting here. Degrees; negative reverses the direction.",
                },
                "end_angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "And ending here. Degrees; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_spline": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The points to pass through, in order.",
                },
                "start_tangent": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Direction the curve leaves the first point. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "end_tangent": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Direction it arrives at the last point. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A surface the spline must lie on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Close the spline into a loop. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_helix": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The axis the helix winds around. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "start_point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Where the helix begins. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "pitch_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Rise per full turn. Millimetres.",
                },
                "height_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Total height. Millimetres.",
                },
                "clockwise": {
                    "type": "boolean",
                    "description": "Wind clockwise looking along the axis. Default true.",
                },
                "taper_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 89.0,
                    "description": "Taper angle for a conical helix. Default 0. Degrees.",
                },
                "start_angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Angular offset of the start. Default 0. Degrees; negative reverses the direction.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["axis", "start_point", "pitch_mm", "height_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_spiral": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane the spiral lies in. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "centre": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The centre point. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "start_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius at the start. Millimetres.",
                },
                "pitch_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radial growth per turn. Millimetres.",
                },
                "end_radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius at the end, instead of a turn count. Millimetres.",
                },
                "turns": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "How many turns.",
                },
                "clockwise": {"type": "boolean", "description": "Wind clockwise. Default true."},
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["support", "centre", "start_radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_polyline": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "points": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The points to join, in order.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Round every corner to this radius. Millimetres.",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Join the last point back to the first. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["points"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_corner": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two curves to round between.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius of the corner. Millimetres.",
                },
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane or surface the corner lies on. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "trim": {
                    "type": "boolean",
                    "description": "Trim both curves back to the arc. Default true.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["elements", "radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_connect": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve at one end. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve at the other end. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "continuity": {
                    "type": "string",
                    "enum": ["point", "tangent", "curvature"],
                    "description": "How smoothly it joins. Default tangent.",
                },
                "first_tension": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "How strongly it follows the first curve. Default 1. A ratio, where 1.0 leaves the size unchanged.",
                },
                "second_tension": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "How strongly it follows the second. Default 1. A ratio, where 1.0 leaves the size unchanged.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["first_curve", "second_curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_project": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve or point to project. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to project onto. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Project along this direction instead of normally. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "nearest": {
                    "type": "boolean",
                    "description": "Keep only the nearest solution. Default true.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["element", "support"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_intersect": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two elements to intersect.",
                },
                "extend": {
                    "type": "boolean",
                    "description": "Extend the elements to find an intersection. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_combine": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve in the first view. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve in the second view. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "first_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Extrusion direction of the first curve. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "second_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Extrusion direction of the second. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["first_curve", "second_curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_parallel": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to offset. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface it lies on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far to offset. Millimetres; negative reverses the direction.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Offset the other way. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["curve", "support", "distance_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_offset_3d": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to offset. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far to offset. Millimetres; negative reverses the direction.",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way to offset. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["curve", "distance_mm", "direction"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_section": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface or solid to cut. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "plane": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The plane to cut with. One of 'XY', 'YZ', 'ZX'; or the name of a plane you created (e.g. 'Plane.1'); or a bounding-box face name (top, bottom, front, back, left, right).",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["element", "plane"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_extremum": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The element to search. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "The direction to find the extreme along. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "second_direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Break ties along this direction. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "maximum": {
                    "type": "boolean",
                    "description": "Find the maximum rather than the minimum. Default true.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the point.",
                },
            },
            "required": ["element", "direction"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_curve_reflect_line": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to find the line on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "The viewing or lighting direction. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 180.0,
                    "description": "Angle to the surface normal. Default 90 (silhouette). Degrees.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["surface", "direction"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_extrude": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to sweep. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way to sweep it. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far to sweep. Millimetres.",
                },
                "second_length_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "Extent in the opposite direction. Millimetres; negative reverses the direction.",
                },
                "symmetric": {
                    "type": "boolean",
                    "description": "Extend equally both ways. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["profile", "direction", "length_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_revolve": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to revolve. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The axis to revolve about. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "How far to revolve. Default 360. Degrees.",
                },
                "second_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 360.0,
                    "description": "Sweep in the opposite direction as well. Degrees.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["profile", "axis"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_offset": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to offset from. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "distance_mm": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "How far to offset. Millimetres; negative reverses the direction.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Offset the other way. Default false.",
                },
                "both_sides": {
                    "type": "boolean",
                    "description": "Create a surface on each side. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["surface", "distance_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_fill": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "boundary": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The curves forming the closed boundary, in order.",
                },
                "supports": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The neighbouring surfaces to match continuity against.",
                },
                "continuity": {
                    "type": "string",
                    "enum": ["point", "tangent", "curvature"],
                    "description": "How smoothly it meets its supports. Default point.",
                },
                "passing_point": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A point the surface must pass through. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["boundary"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_loft": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The section curves, in order along the shape.",
                },
                "guides": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Curves that steer the surface between sections.",
                },
                "spine": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A curve the sections stay normal to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "closed": {
                    "type": "boolean",
                    "description": "Close the loft back onto its first section. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["sections"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_sweep": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["explicit", "line", "circle", "conic"],
                    "description": "What shape is swept.",
                },
                "guide": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to sweep along. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve to sweep, for the explicit kind. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "spine": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A curve controlling the sweep's orientation. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "reference_surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A surface the profile stays at an angle to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 179.0,
                    "description": "Angle to the reference surface. Degrees.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Radius, for the circle kind. Millimetres.",
                },
                "second_guide": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A second guide curve. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["kind", "guide"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_blend": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "first_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve at one end. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_curve": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The curve at the other end. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "first_support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface the first curve lies on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "second_support": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface the second curve lies on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "continuity": {
                    "type": "string",
                    "enum": ["point", "tangent", "curvature"],
                    "description": "Continuity with the supports. Default tangent.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["first_curve", "second_curve"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_primitive": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["sphere", "cylinder"],
                    "description": "Which primitive.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius. Millimetres.",
                },
                "centre": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Centre point, for a sphere. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Axis line, for a cylinder. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Length, for a cylinder. Millimetres.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the surface.",
                },
            },
            "required": ["kind", "radius_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_join": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The surfaces or curves to join.",
                },
                "tolerance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Largest gap to bridge. Default CATIA's own. Millimetres.",
                },
                "check_connexity": {
                    "type": "boolean",
                    "description": "Fail if the result is not connected. Default true.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_split": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "What is being cut. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "cutting": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "What cuts it. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "keep": {
                    "type": "string",
                    "enum": ["first", "second", "both"],
                    "description": "Which side survives. Default first.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["element", "cutting"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_trim": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Exactly two elements to trim together.",
                },
                "keep_first": {
                    "type": "boolean",
                    "description": "Keep the first element's near side. Default true.",
                },
                "keep_second": {
                    "type": "boolean",
                    "description": "Keep the second element's near side. Default true.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_extract": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The faces or edges to extract.",
                },
                "propagation": {
                    "type": "string",
                    "enum": ["none", "tangent", "point_continuity"],
                    "description": "How far to spread from the seed. Default none.",
                },
                "complementary": {
                    "type": "boolean",
                    "description": "Take everything except the selection. Default false.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_boundary": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to take the boundary of. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "propagation": {
                    "type": "string",
                    "enum": ["complete", "point_continuity", "tangent_continuity"],
                    "description": "How much of the boundary. Default complete.",
                },
                "limit_from": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Start the boundary here. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "limit_to": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "End it here. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the curve.",
                },
            },
            "required": ["surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_extrapolate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "What to extend. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "boundary": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The edge or endpoint to extend from. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "length_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "How far to extend. Millimetres.",
                },
                "up_to": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Extend until it reaches this instead. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "continuity": {
                    "type": "string",
                    "enum": ["tangent", "curvature"],
                    "description": "How the extension continues the shape. Default tangent.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["element", "boundary"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_healing": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The surfaces to heal together.",
                },
                "merging_distance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Largest gap to close. Millimetres.",
                },
                "tangency_angle_deg": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 90.0,
                    "description": "Largest kink to smooth out. Degrees.",
                },
                "continuity": {
                    "type": "string",
                    "enum": ["point", "tangent"],
                    "description": "How smooth the result must be. Default point.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_untrim": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to untrim. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the result.",
                },
            },
            "required": ["surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_disassemble": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "What to break up. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "mode": {
                    "type": "string",
                    "enum": ["all_cells", "domains"],
                    "description": "Break into every cell or into connected domains. Default domains.",
                },
            },
            "required": ["element"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_close_surface": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The closed surface to fill with material. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": ["surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_thick_surface": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to thicken. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Thickness to add on the first side. Millimetres.",
                },
                "second_thickness_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 500.0,
                    "description": "Thickness on the other side. Default 0. Millimetres.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Swap which side is which. Default false.",
                },
            },
            "required": ["surface", "thickness_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sew_surface": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "surface": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface to sew on. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "remove": {
                    "type": "boolean",
                    "description": "Remove material rather than add it. Default false.",
                },
                "reversed": {
                    "type": "boolean",
                    "description": "Sew to the other side of the surface. Default false.",
                },
            },
            "required": ["surface"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_surface_analysis": (
        READ,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "curvature",
                        "draft",
                        "connect",
                        "continuity",
                        "reflection",
                        "isophote",
                    ],
                    "description": "Which analysis to run.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The surfaces or curves to analyse.",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Pulling direction, for a draft analysis. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "tolerance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 1000.0,
                    "description": "Gap tolerance, for a connect analysis. Millimetres.",
                },
            },
            "required": ["kind", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_product_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the assembly.",
                },
                "part_number": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "The part number to record on it.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["new_part", "new_product", "existing", "instance_of"],
                    "description": "What to add.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the new component.",
                },
                "document": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The document to instantiate, for 'existing'. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "source": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to make another instance of. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "parent": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The sub-assembly to add it under. Defaults to the root. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "at": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to place it. Defaults to the assembly origin. [x, y, z] in millimetres, in the part's own frame.",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_multi_instantiate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to repeat. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "count": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 100,
                    "description": "How many instances in total.",
                },
                "spacing_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Gap between instances. Millimetres.",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Which way the row runs. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
            },
            "required": ["component", "count", "spacing_mm", "direction"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_replace": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to replace. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "replacement": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The document to put in its place. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "all_instances": {
                    "type": "boolean",
                    "description": "Replace every instance, not just this one. Default false.",
                },
            },
            "required": ["component", "replacement"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_remove": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to remove. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": ["component"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_properties": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to describe. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "part_number": {"type": "string", "maxLength": 120, "description": "Part number."},
                "revision": {"type": "string", "maxLength": 60, "description": "Revision."},
                "nomenclature": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Descriptive name for the BOM.",
                },
                "instance_name": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Name for this particular placement.",
                },
                "source": {
                    "type": "string",
                    "enum": ["made", "bought", "unknown"],
                    "description": "Made in-house or bought in.",
                },
            },
            "required": ["component"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_constrain": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "coincidence",
                        "contact",
                        "offset",
                        "angle",
                        "parallel",
                        "perpendicular",
                        "fix",
                        "fix_together",
                    ],
                    "description": "Which constraint to apply.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The geometry to constrain: one element for fix, two for everything else. Name a face, edge, axis or plane of a component.",
                },
                "value": {
                    "type": "number",
                    "minimum": -10000.0,
                    "maximum": 10000.0,
                    "description": "Offset distance, for an offset constraint. Millimetres; negative reverses the direction.",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Angle, for an angle constraint. Degrees; negative reverses the direction.",
                },
                "orientation": {
                    "type": "string",
                    "enum": ["same", "opposite", "undefined"],
                    "description": "Which way the two elements face. Default undefined.",
                },
            },
            "required": ["kind", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_constraint_update": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Update only this sub-assembly. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_constraint_set_active": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "constraint": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The constraint to change. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "active": {
                    "type": "boolean",
                    "description": "True to activate, false to deactivate.",
                },
            },
            "required": ["constraint", "active"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_move": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The component to move. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "translation": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "How far to move it, as a vector. [x, y, z] in millimetres, in the part's own frame.",
                },
                "axis": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Axis to rotate about. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "How far to rotate. Degrees; negative reverses the direction.",
                },
            },
            "required": ["component"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_component_fix": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The components to fix.",
                },
                "together": {
                    "type": "boolean",
                    "description": "Fix them relative to each other rather than in space. Default false.",
                },
            },
            "required": ["components"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_assembly_feature": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["hole", "pocket", "add", "remove", "split", "remove_lump"],
                    "description": "Which assembly feature.",
                },
                "affected": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The components the feature cuts through.",
                },
                "sketch": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The profile, for a pocket or add. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "at": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre, for a hole. [x, y, z] in millimetres, in the part's own frame.",
                },
                "diameter_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Diameter, for a hole. Millimetres.",
                },
                "depth_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Depth. Omit to go through everything. Millimetres.",
                },
                "cutting": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The surface or plane, for a split. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["kind", "affected"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_assembly_analysis": (
        READ,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "constraints",
                        "degrees_of_freedom",
                        "broken_links",
                        "dependencies",
                        "mass",
                    ],
                    "description": "Which analysis to run.",
                },
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Restrict the analysis to this component. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_assembly_clash": (
        READ,
        {
            "type": "object",
            "properties": {
                "components": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Which components to check. Defaults to all of them.",
                },
                "clearance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Also report pairs closer than this. Default 0. Millimetres.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["contact", "clash", "clearance"],
                    "description": "What counts as a problem. Default clash.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_bill_of_materials": (
        READ,
        {
            "type": "object",
            "properties": {
                "recursive": {
                    "type": "boolean",
                    "description": "Include sub-assemblies' contents. Default true.",
                },
                "format": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "How much detail per line. Default summary.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_scene_explode": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "depth": {
                    "type": "string",
                    "enum": ["first_level", "all_levels"],
                    "description": "How far down to explode. Default first_level.",
                },
                "factor": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 100.0,
                    "description": "How far apart to move things. 1.0 is CATIA's default spacing.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_drawing_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the drawing.",
                },
                "source": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The part or assembly to draw. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "format": {
                    "type": "string",
                    "enum": [
                        "A0",
                        "A1",
                        "A2",
                        "A3",
                        "A4",
                        "ANSI_A",
                        "ANSI_B",
                        "ANSI_C",
                        "ANSI_D",
                        "ANSI_E",
                    ],
                    "description": "Sheet size. Default A3.",
                },
                "landscape": {
                    "type": "boolean",
                    "description": "Landscape orientation. Default true.",
                },
                "projection": {
                    "type": "string",
                    "enum": ["first_angle", "third_angle"],
                    "description": "Projection convention. Default first_angle.",
                },
                "scale": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Drawing scale. 1.0 is full size. A ratio, where 1.0 leaves the size unchanged.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sheet_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the sheet.",
                },
                "format": {
                    "type": "string",
                    "enum": [
                        "A0",
                        "A1",
                        "A2",
                        "A3",
                        "A4",
                        "ANSI_A",
                        "ANSI_B",
                        "ANSI_C",
                        "ANSI_D",
                        "ANSI_E",
                    ],
                    "description": "Sheet size. Default A3.",
                },
                "landscape": {
                    "type": "boolean",
                    "description": "Landscape orientation. Default true.",
                },
                "scale": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "Sheet scale. 1.0 is full size. A ratio, where 1.0 leaves the size unchanged.",
                },
                "detail": {
                    "type": "boolean",
                    "description": "Create it as a detail sheet for reusable components. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_sheet_frame": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sheet": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which sheet. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "title": {"type": "string", "maxLength": 200, "description": "Drawing title."},
                "drawn_by": {"type": "string", "maxLength": 120, "description": "Who drew it."},
                "revision": {"type": "string", "maxLength": 60, "description": "Revision."},
                "company": {"type": "string", "maxLength": 200, "description": "Company name."},
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_view_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "front",
                        "projection",
                        "auxiliary",
                        "isometric",
                        "section",
                        "section_cut",
                        "detail",
                        "clipping",
                        "broken",
                        "breakout",
                        "exploded",
                        "unfolded",
                    ],
                    "description": "Which kind of view.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the view.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where on the sheet to place it, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "parent": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The view to project or detail from. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "direction": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {"type": "number", "minimum": -1000000.0, "maximum": 1000000.0},
                    "description": "Viewing direction, for a front or auxiliary view. [x, y, z]; length is ignored, only the direction is used. All three components zero is refused.",
                },
                "section_line": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Points defining the section line, for a section view.",
                },
                "centre": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Centre of the detail circle. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "radius_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Radius of the detail circle. Millimetres.",
                },
                "scale": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 100.0,
                    "description": "View scale. Defaults to the sheet's. A ratio, where 1.0 leaves the size unchanged.",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Rotate the view on the sheet. Degrees; negative reverses the direction.",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_view_properties": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The view to change. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "hidden_lines": {"type": "boolean", "description": "Show hidden lines."},
                "centre_lines": {"type": "boolean", "description": "Show centre lines."},
                "axes": {"type": "boolean", "description": "Show axes."},
                "threads": {"type": "boolean", "description": "Show thread representation."},
                "fillet_edges": {
                    "type": "boolean",
                    "description": "Show tangent edges of fillets.",
                },
                "show_scale": {
                    "type": "boolean",
                    "description": "Print the view's scale under it.",
                },
                "locked": {
                    "type": "boolean",
                    "description": "Lock the view against accidental edits.",
                },
            },
            "required": ["view"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_view_align": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The view to align. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The view to align it to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "aligned": {
                    "type": "boolean",
                    "description": "True to align, false to break the alignment. Default true.",
                },
            },
            "required": ["view"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_dimension_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "length",
                        "distance",
                        "angle",
                        "radius",
                        "diameter",
                        "chamfer",
                        "thread",
                        "coordinate",
                    ],
                    "description": "Which kind of dimension.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The one or two drawn elements to measure.",
                },
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which view. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the dimension text, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "tolerance": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Tolerance, e.g. '+0.1/-0.05' or 'H7'.",
                },
                "prefix": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Text before the value, e.g. '4x'.",
                },
                "suffix": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Text after the value.",
                },
                "reference": {
                    "type": "boolean",
                    "description": "Create it as a reference dimension, in brackets. Default false.",
                },
            },
            "required": ["kind", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_dimension_chain": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "enum": ["chained", "stacked", "cumulated"],
                    "description": "How the dimensions relate.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The elements to dimension, in order.",
                },
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which view. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "datum": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The element to measure everything from. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["style", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_dimension_generate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which view. Defaults to all of them. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "filter": {
                    "type": "string",
                    "enum": ["all", "constraints", "3d_annotations"],
                    "description": "What to generate from.",
                },
                "step_by_step": {
                    "type": "boolean",
                    "description": "Generate one at a time for review. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_tolerance_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "characteristic": {
                    "type": "string",
                    "enum": [
                        "straightness",
                        "flatness",
                        "circularity",
                        "cylindricity",
                        "profile_line",
                        "profile_surface",
                        "angularity",
                        "perpendicularity",
                        "parallelism",
                        "position",
                        "concentricity",
                        "symmetry",
                        "circular_runout",
                        "total_runout",
                    ],
                    "description": "Which geometric characteristic is controlled.",
                },
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The drawn element it applies to. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "value_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "The tolerance zone size. Millimetres.",
                },
                "datums": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "Datum references, in order (primary first).",
                },
                "modifier": {
                    "type": "string",
                    "enum": ["none", "MMC", "LMC", "RFS"],
                    "description": "Material condition modifier. Default none.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the frame, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
            },
            "required": ["characteristic", "element", "value_mm"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_datum_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "element": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The drawn element that is the datum. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "label": {"type": "string", "maxLength": 8, "description": "The datum letter."},
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the symbol, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
            },
            "required": ["element", "label"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_annotation_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [
                        "text",
                        "text_with_leader",
                        "balloon",
                        "roughness",
                        "welding",
                        "flag_note",
                    ],
                    "description": "Which annotation.",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put it, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "content": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "The text, symbol value or balloon number.",
                },
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which view. Defaults to the active one. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "leader_to": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where the leader line points. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "height_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 10000.0,
                    "description": "Text height. Defaults to the drawing standard's. Millimetres.",
                },
            },
            "required": ["kind", "at"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_dressup_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["centre_line", "axis_line", "thread", "area_fill", "arrow"],
                    "description": "Which dress-up element.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The drawn elements it attaches to.",
                },
                "view": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which view. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put it, for a free-standing element. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "pattern": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Hatch pattern name, for an area fill.",
                },
                "angle_deg": {
                    "type": "number",
                    "minimum": -360.0,
                    "maximum": 360.0,
                    "description": "Hatch or arrow angle. Degrees; negative reverses the direction.",
                },
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_table_add": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "at": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": -10000.0, "maximum": 10000.0},
                    "description": "Where to put the table, in millimetres. [u, v] in millimetres, in the sketch's own 2D frame — u is the sketch's horizontal axis, v its vertical.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["empty", "bill_of_materials"],
                    "description": "What kind of table. Default empty.",
                },
                "rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Number of rows, for an empty table.",
                },
                "columns": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "Number of columns, for an empty table.",
                },
                "title": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "A title for the table.",
                },
            },
            "required": ["at"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_drawing_update": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "sheet": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "Which sheet. Defaults to all of them. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_list_parameters": (
        READ,
        {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "Only report parameters whose name contains this.",
                },
                "include_dimensions": {
                    "type": "boolean",
                    "description": "Include CATIA's own feature dimensions. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_set_parameter": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The parameter to set. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "value": {
                    "type": ["number", "string", "boolean"],
                    "description": "The new value. A number for a length or angle, a string for a text parameter, true/false for a boolean.",
                },
                "unit": {
                    "type": "string",
                    "enum": ["mm", "deg", "kg", "mm2", "mm3", "N", "MPa", "deg_c", "s", ""],
                    "description": "The parameter's unit. Empty string for unitless.",
                },
            },
            "required": ["name", "value", "unit"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_parameter_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the parameter.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["length", "angle", "real", "integer", "boolean", "string", "mass"],
                    "description": "What kind of value it holds.",
                },
                "value": {
                    "type": ["number", "string", "boolean"],
                    "description": "Its initial value.",
                },
                "set": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A parameter set to create it inside. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "minimum": {"type": "number", "description": "Lowest value it may take."},
                "maximum": {"type": "number", "description": "Highest value it may take."},
            },
            "required": ["name", "kind", "value"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_parameter_set_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the set.",
                },
                "parent": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A set to nest it inside. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_formula_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "parameter": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The parameter the formula drives. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "expression": {
                    "type": "string",
                    "maxLength": 1000,
                    "description": "The expression, in CATIA's knowledge language, e.g. 'Plate_Width / 4' or 'Length * sin(Angle)'.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the formula.",
                },
                "active": {
                    "type": "boolean",
                    "description": "Whether the formula is active. Default true.",
                },
            },
            "required": ["parameter", "expression"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_design_table_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the design table.",
                },
                "columns": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The parameter names the table drives, one per column.",
                },
                "rows": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50,
                        "items": {"type": ["number", "string", "boolean"]},
                    },
                    "description": "The configurations, one array per row, values in the same order as `columns`.",
                },
                "active_row": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Which row to make active. Default 1.",
                },
            },
            "required": ["name", "columns", "rows"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_design_table_activate": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "The design table. Name it exactly as catia_list_features or the tool that created it reported it (e.g. 'Sketch.1', 'Pad.1', 'Plane.2').",
                },
                "row": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Which row to activate.",
                },
            },
            "required": ["table", "row"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_rule_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the rule.",
                },
                "body": {
                    "type": "string",
                    "maxLength": 4000,
                    "description": "The rule body, in CATIA's knowledge language.",
                },
                "active": {
                    "type": "boolean",
                    "description": "Whether the rule is active. Default true.",
                },
            },
            "required": ["name", "body"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_check_create": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the check.",
                },
                "condition": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "The condition that must hold.",
                },
                "message": {
                    "type": "string",
                    "maxLength": 500,
                    "description": "What to say when it fails.",
                },
                "severity": {
                    "type": "string",
                    "enum": ["information", "warning", "error"],
                    "description": "How serious a failure is. Default warning.",
                },
            },
            "required": ["name", "condition"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_knowledge_report": (
        READ,
        {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["all", "formulas", "rules", "checks", "design_tables"],
                    "description": "Which relations to report. Default all.",
                },
                "failing_only": {
                    "type": "boolean",
                    "description": "Only report checks that currently fail. Default false.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_measure_publish": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 120,
                    "description": "A name for the published parameter.",
                },
                "measurement": {
                    "type": "string",
                    "enum": [
                        "distance",
                        "angle",
                        "length",
                        "area",
                        "volume",
                        "mass",
                        "centre_of_gravity",
                    ],
                    "description": "What to measure.",
                },
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The geometry to measure.",
                },
            },
            "required": ["name", "measurement", "elements"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_list_commands": (
        READ,
        {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Only report commands matching this word.",
                },
                "menu": {
                    "type": "string",
                    "maxLength": 60,
                    "description": "Only report commands under this top-level menu.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_run_command": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "The command's English name, e.g. 'Edge Fillet'.",
                }
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        ("candidates", "command_name", "command_key", "menu_hint"),
    ),
    "catia_describe_dialog": (
        READ,
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        (),
    ),
    "catia_fill_dialog": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1, "maxLength": 120},
                            "value": {"type": "string", "maxLength": 200},
                        },
                        "required": ["name", "value"],
                        "additionalProperties": False,
                    },
                    "description": "Each field to set, by its displayed label, and its new value.",
                }
            },
            "required": ["fields"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_dialog_action": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["ok", "apply", "cancel", "close", "preview", "yes", "no"],
                    "description": "What the button should do.",
                },
                "button": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "An exact button label, when the role is not enough.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        ("labels",),
    ),
    "catia_press_key": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": [
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
                    ],
                    "description": "Which key to press.",
                }
            },
            "required": ["key"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_switch_workbench": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "workbench": {
                    "type": "string",
                    "maxLength": 120,
                    "description": "The workbench's English name: 'Part Design', 'Generative Shape Design', 'Aerospace Sheet Metal Design'.",
                }
            },
            "required": ["workbench"],
            "additionalProperties": False,
        },
        ("workbench_id", "workbench_name", "menu_path", "licence"),
    ),
    "catia_view_control": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "fit",
                        "zoom_in",
                        "zoom_out",
                        "viewpoint",
                        "render_mode",
                        "hide",
                        "show",
                        "isolate",
                    ],
                    "description": "What to change.",
                },
                "viewpoint": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "Which standard viewpoint, for the viewpoint action.",
                },
                "render_mode": {
                    "type": "string",
                    "enum": [
                        "shaded",
                        "shaded_with_edges",
                        "wireframe",
                        "hidden_line",
                        "transparent",
                    ],
                    "description": "Display mode, for the render_mode action.",
                },
                "elements": {
                    "type": "array",
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "What to hide, show or isolate.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        (),
    ),
    "catia_graphic_properties": (
        WRITE,
        {
            "type": "object",
            "properties": {
                "elements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                    "description": "The features or components to restyle.",
                },
                "colour": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "Colour name or #rrggbb hex.",
                },
                "transparency": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Transparency percentage; 0 is opaque, 100 invisible.",
                },
                "line_weight": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 63,
                    "description": "Line thickness index.",
                },
                "layer": {
                    "type": "string",
                    "maxLength": 40,
                    "description": "Layer name or number.",
                },
                "show": {"type": "boolean", "description": "Show or hide the elements."},
            },
            "required": ["elements"],
            "additionalProperties": False,
        },
        (),
    ),
}

#: Answered by the server. A frame carrying one of these arrived from
#: somewhere it should not have, and is refused rather than guessed at.
SERVER_ONLY: frozenset[str] = frozenset(["catia_status"])

#: Tool -> backend method. Kept as data so `session.py` cannot reach a
#: method that is not on this list, whatever arrives on the wire.
TOOL_METHODS: dict[str, str] = {
    "catia_new_part": "new_part",
    "catia_open_document": "open_document",
    "catia_import": "import_file",
    "catia_export": "export",
    "catia_export_step": "export_step",
    "catia_set_material": "set_material",
    "catia_capture_view": "capture_view",
    "catia_checkpoint": "checkpoint",
    "catia_restore": "restore",
    "catia_list_features": "list_features",
    "catia_measure": "measure",
    "catia_measure_between": "measure_between",
    "catia_measure_item": "measure_item",
    "catia_select": "select",
    "catia_delete_feature": "delete_feature",
    "catia_update": "update",
    "catia_analysis_part": "analysis_part",
    "catia_sketch_create": "sketch_create",
    "catia_sketch_close": "sketch_close",
    "catia_sketch_analysis": "sketch_analysis",
    "catia_sketch_point": "sketch_point",
    "catia_sketch_line": "sketch_line",
    "catia_sketch_polyline": "sketch_polyline",
    "catia_sketch_axis": "sketch_axis",
    "catia_sketch_circle": "sketch_circle",
    "catia_sketch_arc": "sketch_arc",
    "catia_sketch_arc_three_point": "sketch_arc_three_point",
    "catia_sketch_ellipse": "sketch_ellipse",
    "catia_sketch_spline": "sketch_spline",
    "catia_sketch_conic": "sketch_conic",
    "catia_sketch_rectangle": "sketch_rectangle",
    "catia_sketch_parallelogram": "sketch_parallelogram",
    "catia_sketch_polygon": "sketch_polygon",
    "catia_sketch_slot": "sketch_slot",
    "catia_sketch_corner": "sketch_corner",
    "catia_sketch_chamfer": "sketch_chamfer",
    "catia_sketch_trim": "sketch_trim",
    "catia_sketch_offset": "sketch_offset",
    "catia_sketch_mirror": "sketch_mirror",
    "catia_sketch_translate": "sketch_translate",
    "catia_sketch_rotate": "sketch_rotate",
    "catia_sketch_scale": "sketch_scale",
    "catia_sketch_project": "sketch_project",
    "catia_sketch_intersect_3d": "sketch_intersect_3d",
    "catia_sketch_pattern": "sketch_pattern",
    "catia_sketch_constrain": "sketch_constrain",
    "catia_sketch_dimension": "sketch_dimension",
    "catia_sketch_revolve_profile": "sketch_revolve_profile",
    "catia_sketch_groove_profile": "sketch_groove_profile",
    "catia_sketch_gear_profile": "sketch_gear_profile",
    "catia_plane_offset": "plane_offset",
    "catia_plane_angle": "plane_angle",
    "catia_plane_through_points": "plane_through_points",
    "catia_plane_normal_to_curve": "plane_normal_to_curve",
    "catia_plane_tangent_to_surface": "plane_tangent_to_surface",
    "catia_plane_mean": "plane_mean",
    "catia_planes_between": "planes_between",
    "catia_point_at": "point_at",
    "catia_point_on_curve": "point_on_curve",
    "catia_point_on_surface": "point_on_surface",
    "catia_point_centre": "point_centre",
    "catia_point_between": "point_between",
    "catia_line_between": "line_between",
    "catia_line_direction": "line_direction",
    "catia_line_normal": "line_normal",
    "catia_line_tangent": "line_tangent",
    "catia_axis_system": "axis_system",
    "catia_list_faces": "list_faces",
    "catia_list_edges": "list_edges",
    "catia_pad": "pad",
    "catia_pocket": "pocket",
    "catia_shaft": "shaft",
    "catia_groove": "groove",
    "catia_hole": "hole",
    "catia_fillet": "fillet",
    "catia_chamfer": "chamfer",
    "catia_shell": "shell",
    "catia_mirror": "mirror",
    "catia_pattern_rectangular": "pattern_rectangular",
    "catia_pattern_circular": "pattern_circular",
    "catia_rib": "rib",
    "catia_slot": "slot",
    "catia_stiffener": "stiffener",
    "catia_multi_section_solid": "multi_section_solid",
    "catia_solid_combine": "solid_combine",
    "catia_pad_drafted_filleted": "pad_drafted_filleted",
    "catia_hole_at": "hole_at",
    "catia_hole_pattern": "hole_pattern",
    "catia_thread": "thread",
    "catia_fillet_edges": "fillet_edges",
    "catia_fillet_variable": "fillet_variable",
    "catia_fillet_face": "fillet_face",
    "catia_fillet_tritangent": "fillet_tritangent",
    "catia_draft": "draft",
    "catia_shell_faces": "shell_faces",
    "catia_thickness": "thickness",
    "catia_remove_face": "remove_face",
    "catia_replace_face": "replace_face",
    "catia_body_create": "body_create",
    "catia_body_activate": "body_activate",
    "catia_boolean": "boolean",
    "catia_geometrical_set": "geometrical_set",
    "catia_translate": "translate",
    "catia_rotate": "rotate",
    "catia_symmetry": "symmetry",
    "catia_scale": "scale",
    "catia_affinity": "affinity",
    "catia_pattern_user": "pattern_user",
    "catia_pattern_explode": "pattern_explode",
    "catia_feature_rename": "feature_rename",
    "catia_feature_activate": "feature_activate",
    "catia_feature_reorder": "feature_reorder",
    "catia_feature_parents": "feature_parents",
    "catia_curve_circle": "curve_circle",
    "catia_curve_spline": "curve_spline",
    "catia_curve_helix": "curve_helix",
    "catia_curve_spiral": "curve_spiral",
    "catia_curve_polyline": "curve_polyline",
    "catia_curve_corner": "curve_corner",
    "catia_curve_connect": "curve_connect",
    "catia_curve_project": "curve_project",
    "catia_curve_intersect": "curve_intersect",
    "catia_curve_combine": "curve_combine",
    "catia_curve_parallel": "curve_parallel",
    "catia_curve_offset_3d": "curve_offset_3d",
    "catia_curve_section": "curve_section",
    "catia_curve_extremum": "curve_extremum",
    "catia_curve_reflect_line": "curve_reflect_line",
    "catia_surface_extrude": "surface_extrude",
    "catia_surface_revolve": "surface_revolve",
    "catia_surface_offset": "surface_offset",
    "catia_surface_fill": "surface_fill",
    "catia_surface_loft": "surface_loft",
    "catia_surface_sweep": "surface_sweep",
    "catia_surface_blend": "surface_blend",
    "catia_surface_primitive": "surface_primitive",
    "catia_join": "join",
    "catia_split": "split",
    "catia_trim": "trim",
    "catia_extract": "extract",
    "catia_boundary": "boundary",
    "catia_extrapolate": "extrapolate",
    "catia_healing": "healing",
    "catia_untrim": "untrim",
    "catia_disassemble": "disassemble",
    "catia_close_surface": "close_surface",
    "catia_thick_surface": "thick_surface",
    "catia_sew_surface": "sew_surface",
    "catia_surface_analysis": "surface_analysis",
    "catia_product_create": "product_create",
    "catia_component_add": "component_add",
    "catia_component_multi_instantiate": "component_multi_instantiate",
    "catia_component_replace": "component_replace",
    "catia_component_remove": "component_remove",
    "catia_component_properties": "component_properties",
    "catia_constrain": "constrain",
    "catia_constraint_update": "constraint_update",
    "catia_constraint_set_active": "constraint_set_active",
    "catia_component_move": "component_move",
    "catia_component_fix": "component_fix",
    "catia_assembly_feature": "assembly_feature",
    "catia_assembly_analysis": "assembly_analysis",
    "catia_assembly_clash": "assembly_clash",
    "catia_bill_of_materials": "bill_of_materials",
    "catia_scene_explode": "scene_explode",
    "catia_drawing_create": "drawing_create",
    "catia_sheet_add": "sheet_add",
    "catia_sheet_frame": "sheet_frame",
    "catia_view_add": "view_add",
    "catia_view_properties": "view_properties",
    "catia_view_align": "view_align",
    "catia_dimension_add": "dimension_add",
    "catia_dimension_chain": "dimension_chain",
    "catia_dimension_generate": "dimension_generate",
    "catia_tolerance_add": "tolerance_add",
    "catia_datum_add": "datum_add",
    "catia_annotation_add": "annotation_add",
    "catia_dressup_add": "dressup_add",
    "catia_table_add": "table_add",
    "catia_drawing_update": "drawing_update",
    "catia_list_parameters": "list_parameters",
    "catia_set_parameter": "set_parameter",
    "catia_parameter_create": "parameter_create",
    "catia_parameter_set_create": "parameter_set_create",
    "catia_formula_create": "formula_create",
    "catia_design_table_create": "design_table_create",
    "catia_design_table_activate": "design_table_activate",
    "catia_rule_create": "rule_create",
    "catia_check_create": "check_create",
    "catia_knowledge_report": "knowledge_report",
    "catia_measure_publish": "measure_publish",
    "catia_list_commands": "list_commands",
    "catia_run_command": "run_command",
    "catia_describe_dialog": "describe_dialog",
    "catia_fill_dialog": "fill_dialog",
    "catia_dialog_action": "dialog_action",
    "catia_press_key": "press_key",
    "catia_switch_workbench": "switch_workbench",
    "catia_view_control": "view_control",
    "catia_graphic_properties": "graphic_properties",
}

#: Tools whose result the server should wait longer for.
LONG_RUNNING: frozenset[str] = frozenset(["catia_export", "catia_export_step", "catia_import"])
