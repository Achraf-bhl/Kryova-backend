"""Assembly Design: products, components, constraints and the analyses on them.

53 of Assembly Design's 54 documented commands had no tool, and the consequence
was larger than the count: an assembly is the *precondition* for Drafting, DMU,
Generative Assembly Structural Analysis and any bill of materials. Nothing
multi-part was reachable at all.

The distinction that matters throughout: a **reference** is the part itself, an
**instance** is one placement of it in an assembly. Editing a reference changes
every instance; moving an instance changes only that one. Confusing the two is
how a change intended for one bolt silently moves all forty.
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
    required,
    signed_angle,
    text,
)

_WB = Workbench.ASSEMBLY_DESIGN

OPERATIONS: tuple[Operation, ...] = (
    # -- product structure ---------------------------------------------------
    Operation(
        name="catia_product_create",
        summary=(
            "Create a new empty assembly document.\n"
            "Everything else in this module needs one. An assembly holds components "
            "and the constraints between them; it holds no geometry of its own."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("name", new_name("A name for the assembly.")),
            optional("part_number", text("The part number to record on it.", maximum=120)),
        ),
    ),
    Operation(
        name="catia_component_add",
        summary=(
            "Add a component to the assembly — a new part, a new sub-assembly, or "
            "another instance of one already present.\n"
            "Adding a second instance of an existing part is how a repeated bolt is "
            "modelled: one reference, many instances, and a change to the reference "
            "reaches all of them."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("new_part", "new_product", "existing", "instance_of"),
                    "What to add.",
                ),
            ),
            optional("name", new_name("A name for the new component.")),
            optional("document", vocab.element_reference("The document to instantiate, for 'existing'.")),
            optional("source", vocab.element_reference("The component to make another instance of.")),
            optional("parent", vocab.element_reference("The sub-assembly to add it under. Defaults to the root.")),
            optional("at", point3("Where to place it. Defaults to the assembly origin.")),
        ),
    ),
    Operation(
        name="catia_component_multi_instantiate",
        summary=(
            "Create several instances of a component in a line, spaced along a "
            "direction.\n"
            "One call for what would otherwise be n adds and n constraints — a row of "
            "fasteners, a stack of plates."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("component", vocab.element_reference("The component to repeat.")),
            required("count", count("How many instances in total.", minimum=2)),
            required("spacing_mm", length("Gap between instances.")),
            required("direction", direction3("Which way the row runs.")),
        ),
    ),
    Operation(
        name="catia_component_replace",
        summary=(
            "Replace a component with a different one, keeping its constraints where "
            "they still apply.\n"
            "Constraints that referenced geometry the new part does not have will "
            "break; the result reports which, so they can be reconnected rather than "
            "silently lost."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("component", vocab.element_reference("The component to replace.")),
            required("replacement", vocab.element_reference("The document to put in its place.")),
            optional("all_instances", flag("Replace every instance, not just this one. Default false.")),
        ),
    ),
    Operation(
        name="catia_component_remove",
        summary=(
            "Remove a component and the constraints that referenced it.\n"
            "The constraints go too, so anything positioned relative to this component "
            "becomes under-constrained. Run catia_assembly_analysis afterwards to see "
            "what was left floating."
        ),
        # `write`, not `destructive`, and the distinction is exact: the
        # destructive tier means "no checkpoint can undo this", which is true of
        # catia_restore alone. Removing a component is fully recovered by the
        # automatic checkpoint taken before it, exactly as catia_delete_feature
        # is. Marking it destructive would demand a user click for something the
        # safety net already covers, and would teach people to click through the
        # approval that protects the one operation that genuinely needs it.
        tier=Tier.WRITE,
        workbench=_WB,
        params=(required("component", vocab.element_reference("The component to remove.")),),
    ),
    Operation(
        name="catia_component_properties",
        summary=(
            "Set a component's identity — part number, revision, nomenclature, instance "
            "name, and whether it is made or bought.\n"
            "These are the fields a bill of materials is built from, so an assembly "
            "with them unset produces a BOM that cannot be ordered against."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("component", vocab.element_reference("The component to describe.")),
            optional("part_number", text("Part number.", maximum=120)),
            optional("revision", text("Revision.", maximum=60)),
            optional("nomenclature", text("Descriptive name for the BOM.", maximum=200)),
            optional("instance_name", text("Name for this particular placement.", maximum=120)),
            optional("source", one_of(("made", "bought", "unknown"), "Made in-house or bought in.")),
        ),
    ),
    # -- constraints ---------------------------------------------------------
    Operation(
        name="catia_constrain",
        summary=(
            "Constrain two components to each other — coincidence, contact, offset, "
            "angle, parallel, perpendicular, or fix.\n"
            "Constrain rather than position: a constrained assembly stays correct when "
            "a part changes size, and a positioned one does not. Start by fixing one "
            "component, then constrain everything else to it — an assembly with nothing "
            "fixed floats and the solver's answer is arbitrary."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("kind", one_of(vocab.ASSEMBLY_CONSTRAINTS, "Which constraint to apply.")),
            required(
                "elements",
                name_list(
                    "The geometry to constrain: one element for fix, two for everything "
                    "else. Name a face, edge, axis or plane of a component."
                ),
            ),
            optional("value", distance("Offset distance, for an offset constraint.")),
            optional("angle_deg", signed_angle("Angle, for an angle constraint.")),
            optional(
                "orientation",
                one_of(("same", "opposite", "undefined"), "Which way the two elements face. Default undefined."),
            ),
        ),
    ),
    Operation(
        name="catia_constraint_update",
        summary=(
            "Solve the assembly's constraints and move the components accordingly.\n"
            "Constraints do nothing visible until this runs. If components do not move "
            "where expected, catia_assembly_analysis reports which constraints conflict."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(optional("component", vocab.element_reference("Update only this sub-assembly.")),),
    ),
    Operation(
        name="catia_constraint_set_active",
        summary=(
            "Deactivate or reactivate a constraint without deleting it.\n"
            "The way to test whether a constraint is over-constraining the assembly, "
            "and the way to temporarily free a part to look inside."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("constraint", vocab.element_reference("The constraint to change.")),
            required("active", flag("True to activate, false to deactivate.")),
        ),
    ),
    Operation(
        name="catia_component_move",
        summary=(
            "Move a component by a translation or a rotation, ignoring constraints.\n"
            "For positioning something before it is constrained, or for pulling a part "
            "aside to see behind it. Any later constraint update will pull it back."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("component", vocab.element_reference("The component to move.")),
            optional("translation", point3("How far to move it, as a vector.")),
            optional("axis", vocab.element_reference("Axis to rotate about.")),
            optional("angle_deg", signed_angle("How far to rotate.")),
        ),
    ),
    Operation(
        name="catia_component_fix",
        summary=(
            "Fix a component in space, or fix several together so they move as one.\n"
            "Every assembly needs at least one fixed component — it is the datum "
            "everything else is positioned relative to."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("components", name_list("The components to fix.")),
            optional(
                "together",
                flag("Fix them relative to each other rather than in space. Default false."),
            ),
        ),
    ),
    # -- assembly features ---------------------------------------------------
    Operation(
        name="catia_assembly_feature",
        summary=(
            "Cut a hole, pocket or split through several components at once — an "
            "assembly feature.\n"
            "This is how a bolt hole that passes through a bracket, a spacer and a "
            "plate is modelled as one thing. Cutting each part separately means three "
            "features that can drift out of alignment; this one cannot."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("hole", "pocket", "add", "remove", "split", "remove_lump"),
                    "Which assembly feature.",
                ),
            ),
            required("affected", name_list("The components the feature cuts through.")),
            optional("sketch", vocab.element_reference("The profile, for a pocket or add.")),
            optional("at", point3("Centre, for a hole.")),
            optional("diameter_mm", length("Diameter, for a hole.")),
            optional("depth_mm", length("Depth. Omit to go through everything.")),
            optional("cutting", vocab.element_reference("The surface or plane, for a split.")),
        ),
    ),
    # -- analysis ------------------------------------------------------------
    Operation(
        name="catia_assembly_analysis",
        summary=(
            "Analyse the assembly — constraint health, degrees of freedom, broken "
            "links, dependencies, or mass properties.\n"
            "`constraints` reports over-constrained and unsolvable sets, which is the "
            "first thing to check when an update does not move what it should. "
            "`degrees_of_freedom` reports what is still free to move, which is how you "
            "find the constraint you forgot."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(
                    ("constraints", "degrees_of_freedom", "broken_links", "dependencies", "mass"),
                    "Which analysis to run.",
                ),
            ),
            optional("component", vocab.element_reference("Restrict the analysis to this component.")),
        ),
    ),
    Operation(
        name="catia_assembly_clash",
        summary=(
            "Check whether components intersect, touch, or come within a clearance of "
            "each other.\n"
            "Run this before releasing any assembly. `clearance_mm` catches the parts "
            "that do not quite touch but leave no room for a tool, a hand or thermal "
            "growth — usually more useful than checking for hard interference alone."
        ),
        tier=Tier.READ,
        workbench=Workbench.DMU,
        params=(
            optional("components", name_list("Which components to check. Defaults to all of them.")),
            optional("clearance_mm", length("Also report pairs closer than this. Default 0.")),
            optional(
                "kind",
                one_of(("contact", "clash", "clearance"), "What counts as a problem. Default clash."),
            ),
        ),
    ),
    Operation(
        name="catia_bill_of_materials",
        summary=(
            "Produce the assembly's bill of materials — every component, its part "
            "number, quantity and source.\n"
            "Reports the fields set by catia_component_properties. Components missing a "
            "part number are listed as such rather than skipped, because a BOM that "
            "quietly omits a line is worse than one that flags a gap."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("recursive", flag("Include sub-assemblies' contents. Default true.")),
            optional(
                "format",
                one_of(("summary", "detailed"), "How much detail per line. Default summary."),
            ),
        ),
    ),
    Operation(
        name="catia_scene_explode",
        summary=(
            "Explode the assembly, moving components apart along their assembly "
            "directions.\n"
            "For an assembly instruction drawing or a visual check of stacking order. "
            "Does not change the constraints; catia_constraint_update puts it back."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            optional(
                "depth",
                one_of(("first_level", "all_levels"), "How far down to explode. Default first_level."),
            ),
            optional("factor", {"type": "number", "exclusiveMinimum": 0, "maximum": 100.0,
                                "description": "How far apart to move things. 1.0 is CATIA's default spacing."}),
        ),
    ),
)
