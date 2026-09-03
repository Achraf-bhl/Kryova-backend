"""Measurement, selection and inspection of what is already modelled.

These are the read operations the agent leans on hardest: they are how it finds
out what it built, and every write operation downstream names something one of
these reported. `catia_list_features` in particular is the entry point to the
whole naming scheme — a model that has not called it is guessing at names.

`catia_measure` is deliberately more than a dimension read: it returns the
bounding box, mass, volume and centre of gravity in one call, because those are
what a following FEA setup or a sanity check actually needs and three round
trips to get them is three chances to go wrong.
"""

from __future__ import annotations

from app.catia.ops import vocabulary as vocab
from app.catia.ops.spec import (
    Operation,
    Tier,
    Workbench,
    flag,
    name_list,
    one_of,
    optional,
    required,
    text,
)

_WB = Workbench.PART_DESIGN

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="catia_list_features",
        summary=(
            "List the features, sketches and bodies in the part, in tree order, with "
            "their type and whether they are up to date.\n"
            "The first call to make on any document you did not just build yourself. "
            "Every other tool names features exactly as this reports them."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("body", vocab.element_reference("Restrict to one body or geometrical set.")),
            optional("kind", text("Only report features of this type, e.g. 'Pad'.", maximum=60)),
            optional("include_sketches", flag("Include sketches. Default true.")),
        ),
    ),
    Operation(
        name="catia_measure",
        summary=(
            "Measure the part: bounding box, volume, mass, centre of gravity and "
            "surface area.\n"
            "Mass uses the density Kryova holds for the applied material, not CATIA's "
            "own material library, so it is the same number the simulation will use. "
            "Run it after any change you want to confirm actually happened — a feature "
            "that failed silently shows up here as a volume that did not move."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            optional("body", vocab.element_reference("Measure one body rather than the whole part.")),
            optional("include_inertia", flag("Also report the inertia matrix. Default false.")),
        ),
    ),
    Operation(
        name="catia_measure_between",
        summary=(
            "Measure between two pieces of geometry — minimum distance, angle, or the "
            "points where they are closest.\n"
            "This is how a clearance is checked without guessing from a screenshot. It "
            "works between any two elements: two faces, a point and a surface, two "
            "components of an assembly."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            required("elements", name_list("Exactly two elements to measure between.", minimum=2)),
            optional(
                "kind",
                one_of(
                    ("minimum_distance", "angle", "closest_points"),
                    "What to measure. Default minimum_distance.",
                ),
            ),
        ),
    ),
    Operation(
        name="catia_measure_item",
        summary=(
            "Measure one element — the length of an edge, the area and normal of a "
            "face, the radius of a cylinder, the coordinates of a point.\n"
            "What it reports depends on what the element is, and the result says which "
            "kind it found, so an unexpected answer is traceable rather than mysterious."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(required("element", vocab.element_reference("The element to measure.")),),
    ),
    Operation(
        name="catia_select",
        summary=(
            "Put things into CATIA's selection, which is what most commands act on.\n"
            "Select a sketch then run Pad; select a face then run Pocket. Name features "
            "exactly as catia_list_features reported them.\n"
            "Selecting changes nothing on its own and is always safe. Call it with an "
            "empty list to clear the selection, which is how you recover when a command "
            "reports the wrong input."
        ),
        tier=Tier.WRITE,
        workbench=Workbench.INFRASTRUCTURE,
        params=(
            required(
                "features",
                {
                    "type": "array",
                    "maxItems": 50,
                    "description": (
                        "Feature or sketch names to select. An empty array clears the "
                        "selection."
                    ),
                    "items": {"type": "string", "minLength": 1, "maxLength": 120},
                },
            ),
            optional("add", flag("Add to what is already selected instead of replacing it. Default false.")),
        ),
    ),
    Operation(
        name="catia_delete_feature",
        summary=(
            "Delete a feature, sketch or body from the part.\n"
            "Check catia_feature_parents first: deleting something other features "
            "depend on breaks all of them, and the tree does not always make that "
            "dependency visible."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(
            required("feature", vocab.element_reference("The feature to delete.")),
            optional(
                "with_children",
                flag("Also delete everything that depends on it. Default false."),
            ),
        ),
    ),
    Operation(
        name="catia_update",
        summary=(
            "Rebuild the part and report any feature that failed.\n"
            "CATIA does not always rebuild automatically. Run this after a run of edits "
            "and before measuring or exporting, or you may be reading a stale model."
        ),
        tier=Tier.WRITE,
        workbench=_WB,
        params=(optional("feature", vocab.element_reference("Update only this feature and its parents.")),),
    ),
    Operation(
        name="catia_analysis_part",
        summary=(
            "Analyse the part itself — draft angles against a pulling direction, wall "
            "thickness, curvature, or the geometry's validity.\n"
            "`thickness` finds walls too thin to mould or print before they reach the "
            "shop floor. `draft` finds faces that will not release from a mould. "
            "`validity` finds the small self-intersections that make an export fail "
            "with an unhelpful message."
        ),
        tier=Tier.READ,
        workbench=_WB,
        params=(
            required(
                "kind",
                one_of(("draft", "thickness", "curvature", "validity"), "Which analysis to run."),
            ),
            optional("direction", vocab.origin_plane("Pulling direction, for a draft analysis.")),
            optional("minimum_mm", text("Flag anything below this, for a thickness analysis.", maximum=40)),
            optional("faces", name_list("Restrict the analysis to these faces.")),
        ),
    ),
)
