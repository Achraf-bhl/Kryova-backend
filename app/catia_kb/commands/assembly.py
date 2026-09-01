"""Assembly Design and Product Structure.

The distinction that decides most assembly answers is reference versus
instance. A .CATProduct holds *instances* of documents; the document is the
reference. Renaming an instance does not rename the part, changing a part
changes every instance of it, and a "duplicate" made by copying a node is
usually a second instance of one reference rather than a second part. Nearly
every "I changed one and they all changed" report is that.

Contextual design -- a part shaped by geometry belonging to another part -- is
recorded with its failure mode rather than as a feature, because the link it
creates is the thing that later breaks.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_WB = "assembly_design"


_DETAILED: list[Entry] = [
    command(
        "Coincidence Constraint",
        workbench=_WB,
        toolbar="Constraints",
        aliases=(
            "coincidence", "coincidence constraint", "concentric", "coaxial", "align axes",
            "coincidence de contrainte", "kongruenz", "coincidenza", "coincidencia", "mate",
        ),
        summary="Makes two axes, points or planes share the same line, point or plane -- the constraint that does concentricity and coaxiality.",
        menu="Insert > Coincidence",
        icon="two coaxial circles joined by a dashed centreline",
        fields=("Orientation -- Same, Opposite, or Undefined", "The two elements"),
        needs=("Two components, at least one of them not fixed",),
        failures=(
            "\"The constraint cannot be created between these two elements\" -- they are in the same component, or one is under a rigid sub-assembly",
            "Orientation Undefined lets the part flip on update; Same or Opposite pins it",
            "Constraints between two components that are both inside a *rigid* sub-assembly do nothing",
        ),
        fixes=(
            "Set orientation explicitly rather than leaving it undefined",
            "Make the sub-assembly Flexible if its internal parts must move relative to each other",
        ),
        alternatives=("Contact for surfaces that must touch; Offset for a controlled gap",),
        licence="P1 -- Assembly Design 1 (AS1)",
        see_also=("assembly_design.flexible_rigid_sub_assembly", "assembly_design.degrees_of_freedom"),
    ),
    command(
        "Update",
        workbench=_WB,
        toolbar="Update",
        aliases=("update", "mise a jour", "aktualisieren", "aggiorna", "actualizar", "refresh", "rebuild", "update all"),
        summary="Recomputes everything marked out of date -- constraints, features, contextual links.",
        menu="Edit > Update, or the Update icon",
        shortcut="Ctrl+U in most configurations",
        fields=("Automatic or Manual update mode -- Tools > Options > Infrastructure > Part/Product Infrastructure",),
        failures=(
            "\"Update Error\" opens a diagnosis dialog listing every failing feature; the *first* one is usually the only real failure and the rest are consequences",
            "An update loop, where two contextual parts each need the other updated first",
        ),
        fixes=(
            "Work down the Update Diagnosis list from the top, fixing one and re-updating rather than reading all of them",
            "Break the loop by publishing the driving geometry from a skeleton part that depends on nothing",
        ),
        licence="P1",
        see_also=("diagnostic.update_error", "practice.skeleton"),
    ),
    command(
        "Flexible/Rigid Sub-Assembly",
        workbench=_WB,
        toolbar="Constraints / context menu",
        aliases=(
            "flexible sub-assembly", "rigid sub-assembly", "flexible rigid", "make flexible",
            "sous-ensemble flexible", "flexible component", "why won't my sub-assembly move",
        ),
        summary="Switches a sub-assembly between moving as one block (rigid, the default) and letting its own constraints move its children independently per instance.",
        menu="Right-click the sub-assembly > Flexible/Rigid Sub-Assembly",
        needs=("A sub-assembly instance selected",),
        failures=(
            "A mechanism inside a sub-assembly refuses to move at the top level -- it is rigid, which is the default and the answer to most \"my constraints do nothing\" reports",
            "Made flexible, each *instance* then poses independently, which is the point but surprises people who expected one shared pose",
        ),
        licence="P1",
        see_also=("assembly_design.coincidence_constraint",),
    ),
    command(
        "Publication",
        workbench=_WB,
        toolbar="Tools",
        aliases=(
            "publication", "publish", "publier", "published element", "published geometry",
            "publications", "external reference interface", "expose geometry",
        ),
        summary="Names an element inside a part so other documents reference the *name* rather than the topology, which is what makes a contextual link survive a redesign.",
        menu="Tools > Publication",
        needs=("A part document, and the elements to expose",),
        failures=(
            "Not using publications: a contextual link picks a face directly, the face is regenerated by an upstream edit, and the link breaks with nothing to repair it against",
            "A publication is deleted or renamed in the source, breaking every consumer at once",
        ),
        fixes=(
            "Publish the interface geometry deliberately -- planes, axes, curves -- and pick only published elements across documents",
            "Tools > Options > Infrastructure > Part Infrastructure > \"Keep link with selected object\" governs whether the link is even created",
        ),
        aerospace="How an interface control document is enforced in the model: the mating plane and hole pattern are published from the skeleton, and every partner's part references the publication rather than the neighbour's geometry.",
        licence="P1",
        see_also=("practice.publications", "practice.skeleton", "diagnostic.broken_link"),
    ),
    command(
        "Compute Clash",
        workbench=_WB,
        toolbar="Space Analysis",
        aliases=("compute clash", "clash", "interference", "collision", "check interference", "interference check", "analyse d interference"),
        summary="Reports contact, clearance and clash between selected components, from inside Assembly Design.",
        menu="Analyze > Compute Clash",
        fields=("Type -- Contact + Clash, or Clearance + Contact + Clash with a clearance value", "Selection -- between two, inside one selection, or all"),
        failures=("Two parts that touch by design are reported as contact, which floods the list",),
        fixes=("Use DMU Space Analysis' Interference command for a real study: it has rule-based filtering, saved results and a report",),
        alternatives=("DMU Space Analysis > Interference (SPA licence) for anything beyond a quick check",),
        licence="P1 for the basic check; P2 SPA for the full study",
        see_also=("dmu_space_analysis",),
    ),
]


_STRUCTURE = bulk(
    """
Existing Component | insert existing component, insert component, add a part, existing component
New Part | new part, insert new part, add a new part
New Product | new product, insert new product, sub-assembly
New Component | new component, insert new component
Component from Selection | component from selection, group into a component
Replace Component | replace component, swap a part, remplacer un composant
Graph Tree Reordering | graph tree reordering, reorder the tree, reordonner l arbre
Generate Numbering | generate numbering, auto number, numerotation
Selective Load | selective load, load selected, chargement selectif
Manage Representations | manage representations, representation, cgr representation
Fast Multi Instantiation | fast multi instantiation, multi instantiate, duplicate many
Define Multi Instantiation | define multi instantiation, instantiate with spacing
""",
    workbench=_WB,
    toolbar="Product Structure Tools",
)

_MOVE = bulk(
    """
Manipulation | manipulation, move component, drag a part, manipuler, with respect to constraints
Snap | snap, snap two elements, aligner
Smart Move | smart move, smart move component, snap and constrain
Explode | explode, exploded view, eclate, explosionsdarstellung, esploso, explosionar, 3d explode
Stop Manipulate On Clash | stop manipulate on clash, clash while dragging, collision detection while moving
""",
    workbench=_WB,
    toolbar="Move",
)

_CONSTRAINTS = bulk(
    """
Contact Constraint | contact constraint, contact, surface contact, kontakt, contatto, contacto, touching faces
Offset Constraint | offset constraint, offset, distance constraint, decalage, abstand, desfase, gap
Angle Constraint | angle constraint, angle, parallelism constraint, perpendicularity constraint, planar angle, winkel, angolo
Fix Component | fix component, fix, anchor, fixite, fixieren, ground a part, fijar
Fix Together | fix together, group components, rigid group
Quick Constraint | quick constraint, quick, automatic constraint
Change Constraint | change constraint, edit constraint type, changer une contrainte
Reuse Pattern | reuse pattern, instantiate on a pattern, pattern components, reutiliser une repetition
Reconnect | reconnect, reconnect a constraint, repair a constraint
Deactivate Constraint | deactivate constraint, suppress a constraint, desactiver une contrainte
""",
    workbench=_WB,
    toolbar="Constraints",
)

_FEATURES = bulk(
    """
Assembly Split | assembly split, split in assembly, cut parts with a surface
Assembly Hole | assembly hole, hole through several parts, match drill
Assembly Pocket | assembly pocket, pocket across parts
Assembly Add | assembly add, add bodies across parts
Assembly Remove | assembly remove, remove across parts
Assembly Symmetry | assembly symmetry, mirror a component, symetrie d assemblage
Assembly Remove Lump | assembly remove lump
""",
    workbench=_WB,
    toolbar="Assembly Features",
)

_ANALYSIS = bulk(
    """
Constraints Analysis | constraints analysis, analyse des contraintes, constraint status, check constraints
Degrees of Freedom | degrees of freedom, dof, remaining degrees of freedom, degres de liberte, why can this still move
Dependencies | dependencies, assembly dependencies, dependance
Bill of Material | bill of material, bom, nomenclature, stuckliste, distinta base, lista de materiales, parts list
Mass Properties | mass properties, assembly mass, inertia, masse, weight of the assembly
Broken Link Analysis | broken link, links analysis, edit links, liens, desk
""",
    workbench=_WB,
    toolbar="Analysis / Edit menu",
)

_STRUCTURE_METADATA = bulk(
    """
Part Number | part number, pn, reference, numero de piece, item number
Instance Name | instance name, instance, nom d instance, occurrence name
Revision | revision, rev, revision level
Nomenclature | nomenclature, definition, description
Source (Made/Bought) | source, made, bought, make or buy
Reference vs Instance | reference and instance, reference vs instance, why did they all change
Design Mode | design mode, mode conception, full document load
Visualization Mode | visualization mode, visu mode, cgr mode, mode visualisation, lightweight load
Activate Node | activate node, deactivate node, activate terminal node, deactivate representation
""",
    workbench=_WB,
    toolbar="Properties / Cache",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_STRUCTURE,
    *_MOVE,
    *_CONSTRAINTS,
    *_FEATURES,
    *_ANALYSIS,
    *_STRUCTURE_METADATA,
]

SECTION = Section("assembly", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
