"""Knowledgeware: parameters, formulas, design tables, rules, checks, templates.

The distinction that matters is what evaluates when. A **formula** is a directed
assignment that runs on update and always wins over a typed value. A **rule**
runs on update too but is a script that can set several things. A **check**
evaluates and reports, changing nothing. A **reaction** fires on an event rather
than on update, which is what makes it the one people cannot debug -- it does not
appear in the update sequence at all.

Design tables are the other reliable source of confusion: a design table drives
its parameters from the *active row*, so a parameter that "will not change" is
usually one a design table owns.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_KWA = "knowledge_advisor"
_KWE = "knowledge_expert"
_PKT = "product_knowledge_template"


_DETAILED: list[Entry] = [
    command(
        "Formula",
        workbench=_KWA,
        toolbar="Knowledge",
        aliases=("formula", "f(x)", "fx", "formule", "add a formula", "drive a dimension", "equation", "parametre pilote"),
        summary="Assigns a value to one parameter from an expression over others; evaluated on every update.",
        menu="Tools > Formula, or the f(x) icon",
        icon="the letters f(x)",
        fields=(
            "Parameter filter -- by type and by name, over the whole document",
            "New Parameter of type -- Real, Integer, String, Boolean, Length, Angle, Mass, Density, Force, Pressure, and any other magnitude",
            "Formula editor -- the parameter tree, the operator list, and the member function list",
            "Incremental / Multiple values / Range on a parameter",
        ),
        needs=("Knowledge Advisor for anything beyond simple formulas; basic formulas are available without it",),
        failures=(
            "A formula silently overrides a value the user then keeps re-typing -- the parameter is driven, and the typed value is discarded on update",
            "A circular reference between two formulas, reported at update rather than at creation",
            "Units: a Length parameter set from a bare number is interpreted in the document unit, which is not always millimetres",
        ),
        fixes=(
            "Look for the f(x) marker on the parameter in the tree before assuming the value is editable",
            "Deactivate the formula rather than deleting it when the value must be typed temporarily",
        ),
        alternatives=("Design Table, when the values come as a set of configurations; Rule, when several things change together",),
        licence="Basic formulas P1; the full language is Knowledge Advisor (KWA)",
        see_also=("knowledge_advisor.design_table", "knowledge_advisor.rule"),
    ),
    command(
        "Design Table",
        workbench=_KWA,
        toolbar="Knowledge",
        aliases=(
            "design table", "table de conception", "excel table", "configuration table",
            "family of parts", "variants", "drive from excel", "parameter table",
        ),
        summary="Drives a set of parameters from the rows of an Excel or tab-separated file; one active row at a time configures the document.",
        menu="Tools > Design Table",
        fields=(
            "Create from a pre-existing file, or from the current parameter values",
            "Orientation -- vertical (a row per configuration) is the normal one",
            "Associations -- which column drives which parameter",
            "Configurations tab -- picks the active row",
            "Synchronize -- re-reads the file; and the copy/link choice, which decides whether the sheet lives inside the document or beside it",
        ),
        needs=("Excel on Windows for .xls, or a tab-separated .txt which needs nothing",),
        failures=(
            "The table is linked to a path that only exists on the author's machine, and every other user sees a broken link",
            "A unit is missing from the column header, so `10` means 10 of whatever the document unit is",
            "A row exists that no combination of the constraints can actually build, and the part fails only when that row is selected",
        ),
        fixes=(
            "Use .txt over .xls for portability -- no Excel dependency, no locale decimal-separator surprises",
            "Put units in the column headers (`Length (mm)`), and keep the file next to the document or in a managed location",
        ),
        licence="P2 -- Knowledge Advisor (KWA)",
        see_also=("knowledge_advisor.formula", "diagnostic.broken_link"),
    ),
    command(
        "Power Copy",
        workbench=_PKT,
        toolbar="Templates",
        aliases=("power copy", "powercopy", "copie optimisee", "reusable feature", "template feature", "copy with inputs"),
        summary="A named group of features with declared inputs, instantiated elsewhere by supplying new inputs. The result is ordinary geometry, fully editable.",
        menu="Insert > Knowledge Templates > PowerCopy Creation",
        fields=(
            "Definition tab -- name, the features included",
            "Inputs tab -- each reference the copy needs, renamed to something meaningful",
            "Parameters tab -- which parameters are published for the user to set at instantiation",
            "Documents tab -- design tables and other files that must travel with it",
            "Icon tab -- the picture shown in the catalogue",
        ),
        needs=("The features to capture, and a catalogue to store it in for reuse across documents",),
        failures=(
            "Instantiation fails because an input was left as an internal reference the target document has no equivalent of",
            "The inputs are named `Plane.3`, `Line.7`, so at instantiation nobody knows what to pick",
        ),
        fixes=(
            "Rename every input to what it *is* (\"mounting plane\", \"hole axis\") before publishing",
            "Keep the input count small; a Power Copy needing eight picks does not get reused",
        ),
        alternatives=(
            "User Feature (UDF) -- the same idea but the result is one collapsed feature with published parameters, which protects the internals",
            "Document Template, for a whole document rather than a feature group",
        ),
        aerospace="How a standard clip, cleat or lightening-hole treatment is applied a thousand times consistently instead of being modelled a thousand times.",
        licence="P1 KT1 / P2 PKT",
        see_also=("product_knowledge_template.user_feature", "catalog_editor"),
    ),
]


_OBJECTS = bulk(
    """
Parameter | parameter, parametre, user parameter, add a parameter
Parameter Set | parameter set, set of parameters, jeu de parametres
Multiple Values | multiple values, list of values, valeurs multiples
Range | range, parameter range, bounds
Published Parameter | published parameter, publish a parameter
Rule | rule, regle, knowledge rule, if then rule
Check | check, verification, knowledge check, controle
Reaction | reaction, reaction knowledge, event driven
Action | action, knowledge action
Loop | loop, for loop, knowledge loop, boucle
Set of Relations | set of relations, relations, ensemble de relations
Knowledge Inspector | knowledge inspector, inspector, inspecteur de connaissance
Lock Parameter | lock parameter, unlock parameter, verrouiller un parametre
Deactivate Rule | deactivate rule, disable a rule
Equivalent Dimensions | equivalent dimensions, equivalent dimension, dimensions equivalentes
""",
    workbench=_KWA,
    toolbar="Knowledge",
    licence="P2 -- Knowledge Advisor (KWA)",
)

_LANGUAGE = bulk(
    """
distance() | distance function, distance between two elements in a rule
length() | length function, curve length in a rule
area() | area function, surface area in a rule
volume() | volume function, volume in a rule
smartVolume() | smartvolume, smart volume function
inertia() | inertia function, inertia in a rule
MessageBox() | messagebox, message box, show a message from a rule
Trace() | trace, trace function, log from a rule
Parameters.GetAttributeString | getattributestring, get an attribute in a rule
Feature access path | feature path, PartBody\\\\Pad.1\\\\FirstLimit\\\\Length, address a feature parameter
if / else | if else, conditional in a rule
for loop | for loop in a rule, loop over a list in a rule, iterate in a rule
Extended language libraries | extended language, load extended language libraries, knowledge libraries
""",
    workbench=_KWA,
    toolbar="Knowledge language",
    licence="P2 -- KWA",
)

_EXPERT = bulk(
    """
Expert Rule | expert rule, forward chaining rule, regle expert
Expert Check | expert check, expert verification
Rule Base | rule base, catrulebase, base de regles
Rule Set | rule set, set of expert rules
Solve | solve, run the rule base, resoudre
Report (Knowledge Expert) | expert report, compliance report, rapport de controle
""",
    workbench=_KWE,
    toolbar="Expert Rules",
    licence="P1 KE1 / P2 KWE",
)

_TEMPLATES = bulk(
    """
User Feature | user feature, udf, user defined feature, caracteristique utilisateur
Contextual User Feature | contextual udf, contextual user feature
Document Template | document template, template document, modele de document
Instantiate From Document | instantiate from document, instantiate a template
Instantiate From Catalog | instantiate from catalog, catalogue instantiation, instancier depuis un catalogue
Save In Catalog | save in catalog, add to a catalogue
""",
    workbench=_PKT,
    toolbar="Templates",
    licence="P1 KT1 / P2 PKT",
)


ENTRIES: list[Entry] = [*_DETAILED, *_OBJECTS, *_LANGUAGE, *_EXPERT, *_TEMPLATES]

SECTION = Section("knowledgeware", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
