"""Knowledgeware: parameters, formulas, design tables, rules and checks.

A parametric model is only parametric if something drives it. `catia_set_parameter`
existed and could change a value; nothing could *create* a parameter, relate two
of them, or drive a family of parts from a table — which meant every part was a
one-off however many dimensions it carried.

The order that matters: create the parameter, publish a dimension under that
name (`catia_sketch_dimension`'s `parameter_name`), then a formula or a design
table can drive it. A formula written against a dimension CATIA named itself
breaks the moment the feature is rebuilt.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    count,
    flag,
    name_list,
    new_name,
    one_of,
    optional,
    raw,
    required,
    text,
)

_WB = Workbench.KNOWLEDGE_ADVISOR

OPERATIONS: tuple[Operation, ...] = (
    # -- parameters ----------------------------------------------------------
    Operation(
        name="catia_list_parameters",
        summary=(
            "List the part's named parameters with their current values and units.\n"
            "Call this before setting anything: it is the only way to know what the "
            "model actually exposes, and a set against a name that does not exist is "
            "an error rather than a silent no-op."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("filter", text("Only report parameters whose name contains this.", maximum=120)),
            optional("include_dimensions", flag("Include CATIA's own feature dimensions. Default false.")),
        ),
    ),
    Operation(
        name="catia_set_parameter",
        summary=(
            "Set a named parameter to a value, and rebuild the part.\n"
            "The unit is required and is not a formality: CATIA parameters are typed, "
            "and setting a length in degrees is a silent no-op that leaves the model "
            "looking unchanged with no error to explain why."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", vocab.element_reference("The parameter to set.")),
            required(
                "value",
                raw(
                    {
                        "type": ["number", "string", "boolean"],
                        "description": (
                            "The new value. A number for a length or angle, a string for "
                            "a text parameter, true/false for a boolean."
                        ),
                    }
                ),
            ),
            required("unit", one_of(vocab.PARAMETER_UNITS, "The parameter's unit. Empty string for unitless.")),
        ),
    ),
    Operation(
        name="catia_parameter_create",
        summary=(
            "Create a new named parameter — a length, angle, integer, real, boolean, "
            "string or list.\n"
            "This is the first step in making a part configurable: create the "
            "parameter, then drive a dimension from it with a formula. A part whose "
            "dimensions are all literal numbers cannot be resized as a family."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the parameter.")),
            required(
                "kind",
                one_of(
                    ("length", "angle", "real", "integer", "boolean", "string", "mass"),
                    "What kind of value it holds.",
                ),
            ),
            required(
                "value",
                raw(
                    {
                        "type": ["number", "string", "boolean"],
                        "description": "Its initial value.",
                    }
                ),
            ),
            optional("set", vocab.element_reference("A parameter set to create it inside.")),
            optional("minimum", raw({"type": "number", "description": "Lowest value it may take."})),
            optional("maximum", raw({"type": "number", "description": "Highest value it may take."})),
        ),
    ),
    Operation(
        name="catia_parameter_set_create",
        summary=(
            "Create a parameter set — a folder that groups related parameters.\n"
            "Worth doing as soon as a part has more than a handful: an unstructured "
            "list of forty parameters is unreadable to whoever inherits the model."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the set.")),
            optional("parent", vocab.element_reference("A set to nest it inside.")),
        ),
    ),
    # -- relations -----------------------------------------------------------
    Operation(
        name="catia_formula_create",
        summary=(
            "Bind a parameter to an expression of other parameters.\n"
            "This is what makes a model intelligent: `Hole_Spacing = Plate_Width / 4` "
            "keeps four holes evenly spread through every later change to the width. "
            "The expression uses CATIA's own knowledge language and refers to "
            "parameters by their published names.\n"
            "A parameter driven by a formula can no longer be set directly — that is "
            "the point, and catia_set_parameter will say so rather than fail silently."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("parameter", vocab.element_reference("The parameter the formula drives.")),
            required(
                "expression",
                text(
                    "The expression, in CATIA's knowledge language, e.g. "
                    "'Plate_Width / 4' or 'Length * sin(Angle)'.",
                    maximum=1000,
                ),
            ),
            optional("name", new_name("A name for the formula.")),
            optional("active", flag("Whether the formula is active. Default true.")),
        ),
    ),
    Operation(
        name="catia_design_table_create",
        summary=(
            "Drive a set of parameters from a table of configurations — a design table.\n"
            "Each row is one variant of the part; switching the active row switches "
            "every dimension at once. This is how a family of sizes is modelled as one "
            "part rather than forty files.\n"
            "The columns must match parameter names exactly; a mismatched column is "
            "reported rather than silently ignored."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the design table.")),
            required(
                "columns",
                name_list("The parameter names the table drives, one per column."),
            ),
            required(
                "rows",
                raw(
                    {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 200,
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 50,
                            "items": {"type": ["number", "string", "boolean"]},
                        },
                        "description": (
                            "The configurations, one array per row, values in the same "
                            "order as `columns`."
                        ),
                    }
                ),
            ),
            optional("active_row", count("Which row to make active. Default 1.", maximum=200)),
        ),
    ),
    Operation(
        name="catia_design_table_activate",
        summary=(
            "Switch a design table to a different configuration row and rebuild.\n"
            "Every parameter the table drives changes at once, so check the result "
            "with catia_measure — a configuration that is geometrically impossible "
            "fails at rebuild, not here."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("table", vocab.element_reference("The design table.")),
            required("row", count("Which row to activate.", maximum=1000)),
        ),
    ),
    Operation(
        name="catia_rule_create",
        summary=(
            "Create a rule — a piece of knowledge language that runs when the model "
            "updates and sets values conditionally.\n"
            "Where a formula computes one value, a rule can branch: 'if the plate is "
            "thicker than 10 mm use an M8 bolt, otherwise M6'. Keep rules short; a long "
            "one is a program hiding in a CAD model and nobody will find the bug in it."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the rule.")),
            required("body", text("The rule body, in CATIA's knowledge language.", maximum=4000)),
            optional("active", flag("Whether the rule is active. Default true.")),
        ),
    ),
    Operation(
        name="catia_check_create",
        summary=(
            "Create a check — a condition the model is verified against, which reports "
            "rather than changes anything.\n"
            "Design rules as executable statements: 'wall thickness must be at least "
            "2 mm', 'this hole must clear that boss'. A check that fails flags the part "
            "instead of quietly producing something unmakeable."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the check.")),
            required("condition", text("The condition that must hold.", maximum=2000)),
            optional("message", text("What to say when it fails.", maximum=500)),
            optional(
                "severity",
                one_of(("information", "warning", "error"), "How serious a failure is. Default warning."),
            ),
        ),
    ),
    Operation(
        name="catia_knowledge_report",
        summary=(
            "Report every relation in the part — formulas, rules, checks and design "
            "tables — and which checks currently fail.\n"
            "The way to understand an inherited model before changing it: it shows what "
            "is driven by what, so a change to one parameter has visible consequences "
            "rather than surprising ones."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional(
                "kind",
                one_of(
                    ("all", "formulas", "rules", "checks", "design_tables"),
                    "Which relations to report. Default all.",
                ),
            ),
            optional("failing_only", flag("Only report checks that currently fail. Default false.")),
        ),
    ),
    Operation(
        name="catia_measure_publish",
        summary=(
            "Publish a measurement — a distance, area, volume or mass — as a live "
            "parameter that updates with the model.\n"
            "The bridge between geometry and knowledge: once a measurement is a "
            "parameter, a formula or a check can act on it. This is how 'keep the mass "
            "under 2 kg' becomes something the model itself enforces."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the published parameter.")),
            required(
                "measurement",
                one_of(
                    ("distance", "angle", "length", "area", "volume", "mass", "centre_of_gravity"),
                    "What to measure.",
                ),
            ),
            required("elements", name_list("The geometry to measure.")),
        ),
    ),
)
