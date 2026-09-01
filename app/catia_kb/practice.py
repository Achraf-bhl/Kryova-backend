"""Methodology: what to do, why, and what it costs when you do not.

Advice is only worth recording when it is specific enough to be wrong. "Name
your features" is not; "name them, because a Power Copy whose inputs are called
Plane.3 and Line.7 does not get reused" is. Every entry here states the failure
it prevents, because that is the part a user can check against their own model.
"""

from __future__ import annotations

from app.catia_kb.types import Kind, Section, entry

_P = Kind.PRACTICE


def _practice(
    key: str,
    name: str,
    aliases: tuple[str, ...],
    summary: str,
    *,
    fixes: tuple[str, ...] = (),
    failures: tuple[str, ...] = (),
    aerospace: str = "",
    see_also: tuple[str, ...] = (),
):
    return entry(
        f"practice.{key}",
        name,
        _P,
        aliases=aliases,
        summary=summary,
        fixes=fixes,
        failures=failures,
        aerospace=aerospace,
        see_also=see_also,
    )


ENTRIES = [
    _practice(
        "feature_order",
        "Feature order: draft before fillet, shell before internal fillets",
        ("feature order", "order of features", "draft before fillet", "shell before fillet", "modelling order"),
        "The order features are created in decides which ones can succeed at all, not just how the tree reads.",
        failures=(
            "Drafting a face that already carries a fillet fails, where drafting first and filleting after works",
            "Shelling after filleting hits the \"thickness too large\" limit at the smallest internal radius",
            "Fillets high in the tree reference edges that later features regenerate, so they break on every change",
        ),
        fixes=(
            "Bulk material, then draft, then shell, then fillets, then holes and patterns",
            "Keep cosmetic fillets at the very end, where they can be deactivated for analysis and re-activated for release",
        ),
        see_also=("part_design.draft_angle", "part_design.shell", "practice.fillet_late"),
    ),
    _practice(
        "fillet_late",
        "Fillet last, and be able to switch them off",
        ("fillet late", "fillets at the end", "deactivate fillets", "fillets for analysis"),
        "Fillets are the most fragile features in a part and the least needed by anything downstream.",
        failures=("A model that cannot be meshed sensibly because every corner carries a cosmetic 1 mm fillet",),
        fixes=(
            "Group cosmetic fillets at the end of the tree so they can be deactivated in one action before meshing",
            "Model the structural fillets that carry load; deactivate the rest",
        ),
        see_also=("workflow.fea", "part_design.edge_fillet"),
    ),
    _practice(
        "fully_constrain",
        "Fully constrain sketches, and anchor them to the origin",
        ("fully constrained", "constrain the sketch", "green sketch", "sketch discipline"),
        "An under-constrained sketch will move at some point, and it will move at the worst time.",
        failures=("The part changes shape when an unrelated upstream feature is edited, because the profile was free to slide",),
        fixes=(
            "Anchor to the H and V axes first, then dimension outwards",
            "Green means fully constrained; white means it can still move",
            "One closed profile per sketch, and construction geometry for everything that is not the profile",
        ),
        see_also=("sketcher.sketch_analysis", "diagnostic.under_constrained"),
    ),
    _practice(
        "robust_references",
        "Reference datums and publications, never picked faces and edges",
        (
            "robust references", "topological naming", "picked edges break", "reference geometry",
            "why do my features break", "stable references",
        ),
        "CATIA identifies a face or edge by a generated name, and that name is not guaranteed to survive an upstream change.",
        failures=(
            "A fillet, a sketch support or an assembly constraint that referenced a model face fails after an unrelated edit to the feature that produced it",
            "The failure is delayed: it appears weeks later, in someone else's edit",
        ),
        fixes=(
            "Sketch on datum planes, not model faces",
            "Pattern along axes and lines you created, not along model edges",
            "Across documents, reference published elements only",
        ),
        see_also=("assembly_design.publication", "practice.skeleton", "diagnostic.update_error"),
    ),
    _practice(
        "skeleton",
        "Skeleton parts, and the rule that a skeleton depends on nothing",
        ("skeleton", "skeleton part", "master geometry", "master model", "driving geometry"),
        "One part holds the geometry everything else is positioned by, and it is deliberately free of dependencies.",
        failures=(
            "A skeleton that references a detail part creates an update loop that cannot be resolved without breaking a link",
            "Without a skeleton, every part references its neighbours and the assembly becomes one mutually dependent web",
        ),
        fixes=(
            "Skeleton contains datums only: planes, axes, curves, interface surfaces. No solids, no dependencies",
            "Publish everything other parts need, with names that say what they are",
            "Fix the skeleton in the assembly",
        ),
        aerospace="Station, buttock and water planes, the OML, and the interface surfaces at every work-share boundary live here. A partner receives the skeleton and nothing else.",
        see_also=("workflow.top_down", "aero.station_coordinates", "assembly_design.publication"),
    ),
    _practice(
        "publications",
        "Publish interfaces instead of picking across documents",
        ("publications", "publish geometry", "external references", "contextual links", "interface geometry"),
        "A publication gives a cross-document reference a stable name that survives the source being remodelled.",
        failures=("Direct picks across documents break silently and leave nothing to repair against",),
        fixes=(
            "Publish, name meaningfully, and pick only publications across a document boundary",
            "Tools > Options > Infrastructure > Part Infrastructure > \"Keep link with selected object\" decides whether links are created at all",
        ),
        see_also=("assembly_design.publication", "practice.robust_references"),
    ),
    _practice(
        "naming",
        "Naming: features, sets, bodies, parts, publications",
        ("naming", "naming convention", "rename features", "feature names", "name your geometry"),
        "Generated names are the reason a model is unmaintainable by anyone but its author.",
        failures=(
            "A Power Copy whose inputs are `Plane.3` and `Line.7` cannot be instantiated by anyone else",
            "A tree of `Pad.1 ... Pad.47` has to be re-read from scratch on every visit",
        ),
        fixes=(
            "Name anything another feature, document or person will reference",
            "Do not name what nothing references; the effort is better spent on the interfaces",
        ),
        see_also=("product_knowledge_template.power_copy", "api.localisation"),
    ),
    _practice(
        "container_choice",
        "Body, Geometrical Set, or Ordered Geometrical Set",
        ("body or geometrical set", "which container", "ordered geometrical set", "geometrical set vs body"),
        "Three containers with different rules, and mixing conventions inside one part is what makes it confusing.",
        fixes=(
            "Body -- solid features, participates in boolean operations",
            "Geometrical Set -- wireframe and surfaces, unordered, a flat container of reference geometry",
            "Ordered Geometrical Set -- wireframe and surfaces with an explicit order and an insertion point, for when construction order matters",
            "Pick one convention per part and state it; hybrid design mode changes where things land by default",
        ),
        see_also=("part_design.define_in_work_object", "part_design.insert_geometrical_set"),
    ),
    _practice(
        "reuse",
        "Reuse: catalogues, Power Copies, UDFs, templates",
        ("reuse", "standard parts", "catalogue reuse", "template reuse", "design reuse"),
        "The same detail modelled a hundred times is a hundred chances to be inconsistent.",
        fixes=(
            "Standard parts in a catalogue, instantiated rather than copied",
            "Power Copy where the result should stay editable; User Feature where the internals should be protected",
            "Document Templates for whole documents that follow a pattern",
        ),
        aerospace="Clips, cleats, lightening-hole treatments and fastener patterns are the obvious candidates; each is a rule as much as a shape.",
        see_also=("product_knowledge_template.power_copy", "catalog_editor"),
    ),
    _practice(
        "quality_gates",
        "Quality gates before release",
        ("quality gates", "release checklist", "before release", "model checks", "design checks"),
        "The checks that are cheap before release and expensive after it.",
        fixes=(
            "Clash-free, with the campaign run at a sag fine enough to trust",
            "Mass properties checked against the weight target, with the material actually applied",
            "Drawing or PMI conformance to the standard",
            "GD&T complete: every functional surface toleranced and every datum defined",
            "Link integrity: no broken links, no unresolved references",
            "CATDUA clean",
        ),
        aerospace="Weight roll-up per zone and effectivity are part of this on a programme, not afterthoughts -- a part released without effectivity cannot be built against a tail number.",
        see_also=("workflow.change_release", "catdua"),
    ),
    _practice(
        "pitfalls",
        "Common pitfalls",
        ("pitfalls", "common mistakes", "bad practice", "what not to do", "antipatterns"),
        "The recurring ways CATIA models become unworkable.",
        failures=(
            "Over-constraining sketches, then fighting the solver on every edit",
            "\"Keep link\" used indiscriminately, so a part carries dozens of links nobody intended",
            "One enormous body holding an entire assembly's worth of features",
            "Hidden geometry nobody manages, so the tree contains more dead than live elements",
            "Non-associative imported geometry treated as if it were parametric",
            "Duplicated geometry -- the same curve built three times by three people",
            "Hybrid and non-hybrid conventions mixed in one part",
            "Saving into the wrong directory, which breaks every link at once",
        ),
        fixes=("Each of these is cheap to avoid at the start and expensive to unpick later; none of them announces itself",),
        see_also=("practice.container_choice", "practice.robust_references", "diagnostic.broken_link"),
    ),
    _practice(
        "performance_modelling",
        "Modelling for performance",
        ("modelling performance", "make the model faster", "update is slow", "shallow tree"),
        "Update cost is decided by the tree's shape, not its size.",
        fixes=(
            "Shallow trees: a long chain of parent-child dependencies means every update walks the whole chain",
            "Avoid contextual links that are not needed; each one couples two documents' update cycles",
            "Manual update mode while doing exploratory work",
            "Deactivate features that are not currently relevant rather than deleting and re-creating them",
        ),
        see_also=("diagnostic.assembly_slow", "assembly_design.update"),
    ),
]

SECTION = Section("practice", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
