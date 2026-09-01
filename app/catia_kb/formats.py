"""File formats: native, neutral, and the ones this product actually consumes.

Kryova's own pipeline reads STEP, IGES and STL, so those three carry the most
detail -- what survives the round trip, what does not, and what to do when a
solid arrives as a bag of unstitched faces. The rest of the inventory is here
because a user will name it and expect to be understood.

The single most useful fact in this module: **STEP AP242 carries semantic PMI
and AP203/AP214 do not.** A programme that has invested in 3D-master FTA and
exports AP214 has thrown the tolerances away and usually does not know it.
"""

from __future__ import annotations

from app.catia_kb.types import Disambiguation, Kind, Section, bulk, entry

_FMT = Kind.FORMAT


_DETAILED = [
    entry(
        "format.step",
        "STEP (ISO 10303)",
        _FMT,
        aliases=(
            "step", ".step", ".stp", "iso 10303", "ap203", "ap214", "ap242", "step file",
            "export step", "import step", "step ap242", "step export",
        ),
        summary="The neutral exchange format that actually works for solids, and the one to prefer for anything leaving CATIA.",
        fields=(
            "AP203 -- configuration-controlled design; geometry and assembly structure, no colours in the original edition",
            "AP214 -- automotive; geometry, assembly, colours, layers. The common default",
            "AP242 -- merges 203 and 214 and adds **semantic PMI**, tessellated geometry and kinematics. The one to use for a 3D-master release",
            "Tools > Options > Compatibility > STEP -- application protocol, whether to write assemblies as a single file, healing on import",
        ),
        failures=(
            "Exported as AP214 from a model carrying FTA: the annotations are lost, silently",
            "Imported geometry arrives as a surface set rather than a solid because the original had gaps larger than the receiving tolerance",
            "Assembly structure flattens to one part when the export was configured for a single entity",
        ),
        fixes=(
            "Export AP242 whenever PMI matters; check the import back into a fresh CATIA session before shipping it",
            "For an import that is surfaces rather than a solid: Join with an appropriate merging distance, then Close Surface",
        ),
        see_also=("format.iges", "fta.geometrical_tolerance", "diagnostic.import_not_solid"),
    ),
    entry(
        "format.iges",
        "IGES 5.3",
        _FMT,
        aliases=("iges", ".igs", ".iges", "iges 5.3", "export iges", "import iges"),
        summary="The older neutral format: surfaces and wireframe, no reliable solid, no assembly structure.",
        fields=(
            "Entity 128 -- rational B-spline surface; entity 144 -- trimmed surface. These two carry nearly everything CATIA writes",
            "Tools > Options > Compatibility > IGES -- import mode (as one part or several), healing, tolerance",
        ),
        failures=(
            "Surfaces arrive unstitched with gaps at every edge, because IGES has no shared topology",
            "Trim curves are approximated, so the trimmed boundary does not exactly match the neighbour's",
        ),
        fixes=(
            "Use STEP unless the receiving system genuinely cannot read it",
            "On import: Join with a merging distance sized to the reported gaps, then Healing, then Close Surface",
        ),
        see_also=("format.step", "gsd.healing", "healing_assistant"),
    ),
    entry(
        "format.stl",
        "STL",
        _FMT,
        aliases=("stl", ".stl", "stereolithography", "mesh export", "3d print file", "tessellation export", "export stl"),
        summary="A bag of triangles with no topology, no units and no curvature. Adequate for printing and meshing, useless for editing.",
        fields=(
            "ASCII or binary -- binary is smaller and is what everything writes by default",
            "Sag / deviation -- the maximum distance between a triangle and the true surface; this is the only quality control the format has",
            "Maximum edge length, and the angular deviation",
        ),
        failures=(
            "Exported at the default sag, a curved part is visibly faceted in the print or the mesh",
            "STL has no unit -- the receiving tool assumes one, and a millimetre model can arrive as an inch model",
            "A non-watertight STL cannot be meshed for analysis; the holes come from surfaces that were not stitched before export",
        ),
        fixes=(
            "Set the sag explicitly rather than accepting the default -- one tenth of the smallest feature is a defensible starting point",
            "Check the solid is a single closed body before exporting, not after",
        ),
        aerospace="Fine for a form-and-fit check or a printed jig; never for a released definition, because it carries no tolerance, no PMI and no exact geometry.",
        see_also=("setting.3d_accuracy", "format.step"),
    ),
]


_NATIVE = bulk(
    """
.CATPart | catpart, .catpart, part file, catia part file
.CATProduct | catproduct, .catproduct, assembly file, product file
.CATDrawing | catdrawing, .catdrawing, drawing file
.CATProcess | catprocess, .catprocess, machining process file
.CATAnalysis | catanalysis, .catanalysis, analysis file
.CATMaterial | catmaterial, .catmaterial, material catalogue file
.CATfct | catfct, .catfct, feature dictionary file
.CATShape | catshape, .catshape
.CATSystem | catsystem, .catsystem
.CATRuleBase | catrulebase, .catrulebase, rule base file
.catalog | catalog file, .catalog, catalogue file
.cgr | cgr, .cgr, catia graphical representation, tessellated representation
.3dxml | 3dxml, .3dxml, 3d xml
.CATScript | catscript, .catscript
.catvbs | catvbs, .catvbs
.catvba | catvba, .catvba, vba project file
.CATSettings | catsettings file, .catsettings
.model | model, .model, v4 model file, catia v4 file
.exp | exp, .exp, v4 export file
.session | session, .session, v4 session file
.dlv | dlv, .dlv, v4 dlv file
.CATAnalysisComputations | catanalysiscomputations, computation file
.CATAnalysisResults | catanalysisresults, results file
.pptable | pptable, .pptable, post processor words table
.lib | tool catalogue lib, .lib
""",
    kind=_FMT,
    prefix="format",
    toolbar="Native formats",
)

_NEUTRAL = bulk(
    """
Parasolid | parasolid, .x_t, .x_b, xt file
ACIS SAT | acis, sat, .sat, acis sat
JT | jt, .jt, jt file, siemens jt, lod
VRML | vrml, .wrl, wrl file
OBJ | obj, .obj, wavefront obj
3MF | 3mf, .3mf
U3D | u3d, .u3d, 3d pdf, three d pdf
DXF | dxf, .dxf, autocad exchange, export dxf, dxf for nesting
DWG | dwg, .dwg, autocad drawing
CGM | cgm, .cgm, computer graphics metafile
SVG | svg, .svg
HCG | hcg, .hcg
HPGL | hpgl, plot file, .plt
TIFF | tiff, .tif, raster capture
PDF (2D) | pdf, 2d pdf, export to pdf, print to pdf
Nastran BDF | nastran, .bdf, .dat, nastran deck, bulk data file
Abaqus INP | abaqus, .inp, abaqus input
ANSYS CDB | ansys, .cdb
Patran | patran, neutral file
HyperMesh | hypermesh, .hm
Fibersim | fibersim, fibersim exchange
Ply XML | ply xml, composites xml, ply data xml
APT | apt, apt source, aptsource
CLfile | clfile, cl file, cutter location file
ISO G-code | g code, gcode, iso code, nc code, cnc program
IDF | idf, board exchange, ecad idf
""",
    kind=_FMT,
    prefix="format",
    toolbar="Neutral and downstream formats",
)

_TRANSLATORS = bulk(
    """
Elysium | elysium, elysium translator
Theorem | theorem, theorem solutions, cadverter
Datakit | datakit, datakit translator
CADfix | cadfix, transcendata
Capvidia | capvidia, compareVidia
Proficiency | proficiency, collaboration gateway
MultiCAx | multicax, multicad, nx reader, creo reader, solidworks reader, catia multicax
""",
    kind=_FMT,
    prefix="format",
    toolbar="Translator ecosystem",
)

_PLM = bulk(
    """
ENOVIA VPM | enovia vpm, vpm, vpm navigator, check in, check out
ENOVIA LCA | enovia lca, lca
SmarTeam | smarteam, smart team
Teamcenter | teamcenter, tc, siemens teamcenter, gateway
Windchill | windchill, ptc windchill
3DEXPERIENCE | 3dexperience, 3dx, 3d experience, v6 platform
Effectivity | effectivity, line number, tail number, applicability
EBOM | ebom, engineering bill of material
MBOM | mbom, manufacturing bill of material
""",
    kind=_FMT,
    prefix="plm",
    toolbar="PLM and data management",
)

_HEALING = bulk(
    """
Send To | send to, send to directory, send to zip, send to mail, envoyer vers
Pack and Go | pack and go, packandgo, collect all files, gather dependencies
Desk | desk, links tree, catia desk, document links, analyse des liens
Search Order | search order, ordre de recherche, how catia resolves a link
Data Upward Assistant | data upward assistant, dua, upgrade documents
Downward Compatibility | downward compatibility, save as an older release, save to a previous version
""",
    kind=_FMT,
    prefix="data",
    toolbar="Data management",
)


_DISAMBIGUATIONS = [
    Disambiguation(
        term="step",
        aliases=("stp", "step file"),
        options=(
            "STEP AP203 -- geometry and structure only",
            "STEP AP214 -- adds colours and layers; the common default",
            "STEP AP242 -- adds semantic PMI, tessellated geometry and kinematics",
        ),
        guidance="If the model carries FTA/PMI that has to survive, it must be AP242. AP203 and AP214 discard it without warning.",
    ),
    Disambiguation(
        term="export",
        aliases=("save as", "convert"),
        options=(
            "Exchange a solid -- STEP AP242 or AP214",
            "Exchange surfaces to an older system -- IGES",
            "Send for printing or meshing -- STL, with an explicit sag",
            "Send a drawing to a 2D system -- DXF or DWG",
            "Send a lightweight view for review -- 3DXML, CGR or JT",
        ),
        guidance="Ask what the receiving side does with it. That decides the format; nothing else does.",
    ),
]


ENTRIES = [*_DETAILED, *_NATIVE, *_NEUTRAL, *_TRANSLATORS, *_PLM, *_HEALING]

SECTION = Section("formats", ENTRIES, _DISAMBIGUATIONS)

__all__ = ["ENTRIES", "SECTION"]
