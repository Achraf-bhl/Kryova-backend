"""Macros, the V5 automation object model, and CAA.

Kryova drives CATIA through this API, so this section is load-bearing rather
than decorative: the CATIA bridge is a COM client, and every behaviour recorded
here is a behaviour the bridge either relies on or works around.

Three facts do most of the work in an answer about automation:

**The object model is not localised.** `AddNewPad` is `AddNewPad` on every
language install. What *is* localised is data -- generated feature names, the
material catalogue, dialog text -- so a script that matches on `"Pad.1"` is the
one that breaks abroad. See `languages.api_localisation`.

**Everything is in millimetres and degrees internally.** The automation layer
does not honour the document's display units: a length passed to `AddNewPad` is
millimetres whatever the user's unit setting says. Reading a parameter through
`Parameter.Value` likewise gives internal units. This is the single most common
silent factor-of-25.4 in CATIA scripting.

**A modal dialog blocks the client.** If a command opens a dialog, the COM call
does not return until it closes, and a headless run hangs forever rather than
failing. Anything scripted must use the non-interactive API path.
"""

from __future__ import annotations

from app.catia_kb.types import Kind, Section, bulk, entry

_API = "api"


_DETAILED = [
    entry(
        "api.automation_root",
        "CATIA (the automation root)",
        Kind.API,
        aliases=(
            "catia object", "catia application", "automation root", "getobject catia",
            "createobject catia", "win32com catia", "catia.application", "connect to catia",
        ),
        summary="The top-level Application object every script starts from; reached over COM.",
        fields=(
            "VBA/VBScript -- `Set CATIA = GetObject(, \"CATIA.Application\")` for a running session, `CreateObject(\"CATIA.Application\")` to start one",
            "Python -- `win32com.client.Dispatch(\"CATIA.Application\")`, or `GetActiveObject` for an existing session",
            "Members -- Documents, ActiveDocument, Windows, ActiveWindow, DisplayFileAlerts, Visible, RefreshDisplay, HSOSynchronized, StartCommand, Quit",
        ),
        failures=(
            "`CreateObject` starts a *second* CATIA rather than attaching to the one on screen -- use `GetObject` first and fall back",
            "The call fails with an access error when CATIA runs elevated and the client does not (or the reverse)",
            "`DisplayFileAlerts = True` leaves a modal save prompt that blocks the script indefinitely",
        ),
        fixes=(
            "Set `CATIA.DisplayFileAlerts = False` at the start of any unattended run",
            "Set `CATIA.RefreshDisplay = False` and `CATIA.HSOSynchronized = False` around bulk work, then restore them -- it is often a 10x speed-up",
        ),
        see_also=("api.localisation", "api.units"),
    ),
    entry(
        "api.units",
        "Units in automation are always mm and degrees",
        Kind.API,
        aliases=("automation units", "macro units", "inches in a macro", "unit conversion in vba", "parameter value units"),
        summary="The API works in millimetres, degrees and kilogrammes internally regardless of the document's display units.",
        failures=(
            "A script written on an inch-configured seat sets a length of 2 and gets 2 mm",
            "`Parameter.Value` returns internal units while `Parameter.ValuateFromString` parses a unit-bearing string -- mixing them gives a silent 25.4",
        ),
        fixes=(
            "Use `ValuateFromString(\"2in\")` when a unit must be explicit, and `Value` only when millimetres are intended",
            "Never format a number into a string for the user without labelling the unit",
        ),
        see_also=("api.automation_root",),
    ),
    entry(
        "api.modal_dialog",
        "Modal dialogs block automation",
        Kind.API,
        aliases=("macro hangs", "script hangs", "modal dialog", "catia not responding macro", "sendkeys", "startcommand"),
        summary="A COM call that opens a dialog does not return until the dialog closes, so an unattended script stops dead.",
        failures=(
            "`CATIA.StartCommand` launches an interactive command and returns immediately, leaving the dialog open and the next line running against a busy application",
            "SendKeys-based automation depends on window focus, menu text and keyboard layout, so it breaks on another machine, another language, or a slow day",
        ),
        fixes=(
            "Use the object model (`ShapeFactory`, `HybridShapeFactory`) rather than `StartCommand` for anything that must run unattended",
            "Turn off file alerts, and never automate through the interface",
        ),
        see_also=("api.automation_root", "api.localisation"),
    ),
]


_OBJECT_MODEL = bulk(
    """
Documents | documents collection, catia.documents, open a document in vba
PartDocument | partdocument, part document object
Part | part object, catia part object, part.update
Bodies | bodies, bodies collection, partbody in vba
HybridBodies | hybridbodies, geometrical sets in vba, hybrid bodies collection
Sketches | sketches, sketches collection, add a sketch in vba
Factory2D | factory2d, sketch factory, create 2d geometry in vba
ShapeFactory | shapefactory, addnewpad, addnewpocket, addnewshaft, solid factory
HybridShapeFactory | hybridshapefactory, surface factory, addnewextrude, wireframe in vba
Constraints | constraints collection, add a constraint in vba
Parameters | parameters collection, get a parameter in vba, parameter object
Relations | relations collection, formulas in vba
Products | products collection, product structure in vba
ProductDocument | productdocument, product document object
DrawingDocument | drawingdocument, drawing document object
Sheets | sheets collection, drawing sheets in vba
Views | views collection, drawing views in vba
Selection | selection object, catia selection, add to selection, search in vba
SearchCriteria | search criteria, selection.search, search string
Cameras | cameras collection, named views in vba
Viewer | viewer object, capturetofile, screenshot in vba
SPAWorkbench | spaworkbench, measurable, getmeasurable, measure in vba
Measurable | measurable, measurable object, getarea, getvolume, mass properties in vba
Inertia | inertia object, mass, centre of gravity in vba
AnalyzeDocument | analyzedocument, analysis document in vba
INFITF | infitf, infrastructure type library
MECMOD | mecmod, mechanical modeler type library
PARTITF | partitf, part interfaces type library
HybridShapeTypeLib | hybridshapetypelib, hybrid shape type library
KnowledgewareTypeLib | knowledgewaretypelib, knowledgeware type library
DraftingItf | draftingitf, drafting type library
ProductStructureTypeLib | productstructuretypelib, product structure type library
SPATypeLib | spatypelib, space analysis type library
NavigatorTypeLib | navigatortypelib, dmu navigator type library
""",
    kind=Kind.API,
    prefix="api",
    toolbar="V5 Automation object model",
)

_MACRO_TOOLS = bulk(
    """
Start Recording | start recording, record a macro, macro recording, enregistrer une macro
Macros dialog | macros dialog, alt f8, run a macro, macro library
Macro Libraries | macro libraries, catvba library, macro directory, bibliotheque de macros
Visual Basic Editor | vba editor, visual basic editor, edit a macro, alt f11
Assign a macro to an icon | macro on a toolbar, assign a macro to a button, custom icon for a macro
CATScript | catscript, .catscript, catia script
CATVBS | catvbs, .catvbs, vbscript macro
CATVBA | catvba, .catvba, vba project
VB.NET / C# via COM | vb net catia, c# catia, dotnet automation, interop
Python via win32com | python catia, pywin32, win32com.client, python automation
""",
    kind=Kind.API,
    prefix="api",
    toolbar="Tools > Macro",
)

_TASKS = bulk(
    """
Batch export | batch export, export many files, batch step export, convert a folder
Batch rename | batch rename, rename many parts
Batch property set | batch properties, set attributes on many documents
BOM extraction to Excel | bom to excel, extract the bill of material, nomenclature vers excel
Parameter driving from Excel | drive parameters from excel, excel to catia parameters
Mass properties extraction | extract mass properties, get mass in a macro
Drawing title block auto-fill | fill the title block, cartouche automatique
Screenshot generation | screenshot, capturetofile, capture the view, image export macro
Tree traversal recursion | traverse the tree, recurse the product structure, walk the assembly
Hole pattern generation | generate holes from a table, hole pattern macro
Clash report export | export the clash report, interference to excel
Publish creation | create publications in a macro, publish by script
Link repair | repair links by script, batch link repair
Save management scripting | save management macro, batch save
""",
    kind=Kind.API,
    prefix="api",
    toolbar="Common automation tasks",
)

_CAA = bulk(
    """
CAA V5 | caa, caa v5, c++ api, component application architecture
RADE | rade, caa rade, rapid application development environment
Object Modeler | caa object modeler, object modeler
Interactive Dashboard | interactive dashboard, caa dashboard
C++ Source Checker | source checker, caa source checker
Framework | caa framework, framework, module
CAA Interface | caa interface, catia interfaces, catiaalias
mkmk | mkmk, caa build, build a caa framework
""",
    kind=Kind.API,
    prefix="api",
    toolbar="CAA / RADE",
)


ENTRIES = [*_DETAILED, *_OBJECT_MODEL, *_MACRO_TOOLS, *_TASKS, *_CAA]

SECTION = Section("automation", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
