"""Every CATIA V5 workbench, by full name, product trigram and colloquial name.

The Start menu is how a user reaches a command, so "which workbench is this in"
is the single most common question about CATIA that has a wrong answer worth
avoiding. Each entry therefore carries the exact Start-menu path and the product
trigram that gates it, because "it is in Generative Shape Design" is only useful
to someone whose licence includes GSD.

The keys here are what every command entry points at through `Entry.workbench`,
and `WORKBENCH_NAMES` is populated at the bottom so a command can name its own
workbench without importing this module back.

Naming discipline: these are **V5 / V5-6R** names. 3DEXPERIENCE renamed most of
them (Part Design became Part Design App, GSD became Generative Shape Design
under a different licence model), and answering a V5 question with a V6 menu
path is the failure this package exists to prevent.
"""

from __future__ import annotations

from app.catia_kb.types import WORKBENCH_NAMES, Disambiguation, Entry, Kind, Section

_MECH = "Start > Mechanical Design"
_SHAPE = "Start > Shape"
_ANALYSIS = "Start > Analysis & Simulation"
_DMU = "Start > Digital Mockup"
_EQUIP = "Start > Equipment & Systems"
_ERGO = "Start > Ergonomics Design & Analysis"
_MACH = "Start > Machining"
_KNOW = "Start > Knowledgeware"
_INFRA = "Start > Infrastructure"


def _wb(
    key: str,
    name: str,
    menu: str,
    *,
    aliases: tuple[str, ...] = (),
    summary: str = "",
    licence: str = "",
    aerospace: str = "",
    see_also: tuple[str, ...] = (),
) -> Entry:
    return Entry(
        key=key,
        name=name,
        kind=Kind.WORKBENCH,
        aliases=aliases,
        summary=summary,
        menu=f"{menu} > {name}",
        licence=licence,
        aerospace=aerospace,
        see_also=see_also,
    )


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

_INFRASTRUCTURE = [
    _wb(
        "product_structure",
        "Product Structure",
        _INFRA,
        aliases=("product structure editor", "assembly tree editor", "structure produit"),
        summary="Builds and edits the product tree without the constraint solver: insert, replace, reorder, renumber, multi-instantiate.",
        licence="P1 -- CATIA Object Manager (CO1/COM); always present",
        see_also=("assembly_design",),
    ),
    _wb(
        "material_library",
        "Material Library",
        _INFRA,
        aliases=("apply material", "materials", "bibliotheque de materiaux", "catalogue materiaux"),
        summary="The .CATMaterial catalogue and the Apply Material command; carries rendering, drafting hatch and analysis properties on one material.",
        licence="P1 -- included in Object Manager",
    ),
    _wb(
        "catalog_editor",
        "Catalog Editor",
        _INFRA,
        aliases=("catalogue editor", "catalog", "editeur de catalogue", "catalogue"),
        summary="Creates and browses .catalog files: chapters, families, keywords and the resolved parts that Piping, Tubing, Structure and Power Copy instantiation draw from.",
        licence="P1 -- included in Object Manager",
    ),
    _wb(
        "photo_studio",
        "Photo Studio",
        _INFRA,
        aliases=("photo studio optimizer", "rendering", "rendu", "raytracing", "render image"),
        summary="Offline ray-traced still and animated rendering: environments, lights, camera, shooting parameters.",
        licence="P2 -- Photo Studio (PHS); Optimizer is a separate product",
    ),
    _wb(
        "real_time_rendering",
        "Real Time Rendering",
        _INFRA,
        aliases=("rtr", "real-time rendering", "rendu temps reel", "apply texture"),
        summary="Interactive materials, textures, environments and mapping applied live in the 3D view.",
        licence="P1 -- Real Time Rendering 1 (RT1) / 2 (RTR)",
    ),
    _wb(
        "feature_dictionary_editor",
        "Feature Dictionary Editor",
        _INFRA,
        aliases=("catfct", "feature dictionary", "dictionnaire de caracteristiques"),
        summary="Authors the .CATfct dictionaries that define user feature types for Piping, Tubing, HVAC, Electrical and Structure.",
        licence="P3 -- Feature Dictionary Editor",
    ),
    _wb(
        "object_library",
        "Object Library",
        _INFRA,
        aliases=("object naming", "objects library", "bibliotheque d objets"),
        summary="Stores and reuses named 3D objects across documents; the pre-catalogue way of sharing standard geometry.",
        licence="P2",
    ),
    _wb(
        "v4_integration",
        "V4 Integration",
        _INFRA,
        aliases=(
            "v4 integration",
            "catia v4",
            "model file",
            ".model",
            "v4 to v5",
            "v5 to v4",
            "v4i",
            "v41",
        ),
        summary="Reads and writes CATIA V4 .model, .session, .exp and .dlv files, and maps V4 spaces onto V5 geometrical sets.",
        licence="P1 -- V4 Integration 1 (V41) / 2 (V4I)",
        see_also=("format.model",),
    ),
    _wb(
        "3d_xml_player",
        "3D XML Player",
        _INFRA,
        aliases=("3dxml", "3d xml", "3dxml player", "3dxml publishing"),
        summary="Publishes and plays lightweight .3dxml scenes for review outside a CATIA seat.",
        licence="P1 -- free player, publishing needs a CATIA seat",
        see_also=("format.3dxml",),
    ),
    _wb(
        "batch_monitor",
        "Batch Monitor",
        _INFRA,
        aliases=("catutil", "batch monitor", "catstart", "batch", "utility"),
        summary="Runs and schedules CATIA utilities headlessly -- CATDUA, Data Upward Assistant, migration, printing, downward compatibility.",
        licence="P1 -- included in Object Manager",
        see_also=("setting.command_line", "catdua"),
    ),
    _wb(
        "catdua",
        "CATDUA V5",
        _INFRA,
        aliases=(
            "catdua",
            "catdua v5",
            "data upward assistant",
            "dua",
            "clean document",
            "check document",
        ),
        summary="Checks and cleans document internal structure. Check mode reports error codes; Clean mode repairs them. The first thing to run on a document that behaves oddly.",
        licence="P1 -- Object Manager; run through Batch Monitor or CATUTIL",
        see_also=("diagnostic.corrupt_document", "batch_monitor"),
    ),
    _wb(
        "standards_editor",
        "Standards Editor",
        _INFRA,
        aliases=("tools standards", "standards", "drafting standard", "normes"),
        summary="Edits the XML standard files behind Drafting and Tolerancing (ISO, ANSI, ASME, JIS, DIN and company derivatives).",
        licence="P1 -- admin mode required to write into CATCollectionStandard",
        see_also=("setting.catia_environment", "drafting"),
    ),
]


# ---------------------------------------------------------------------------
# Mechanical Design
# ---------------------------------------------------------------------------

_MECHANICAL = [
    _wb(
        "sketcher",
        "Sketcher",
        _MECH,
        aliases=("sketch", "2d sketch", "esquisse", "sketch mode", "skd", "skt"),
        summary="2D profile creation on a plane or planar face, with geometric and dimensional constraints; the input to almost every solid feature.",
        licence="P1 -- included wherever Part Design is",
        see_also=("part_design", "diagnostic.open_profile"),
    ),
    _wb(
        "part_design",
        "Part Design",
        _MECH,
        aliases=("part design", "pdg", "pd1", "solid modelling", "conception de pieces", "part"),
        summary="Feature-based solid modelling: pads, pockets, shafts, holes, dress-up and patterns, in a single .CATPart.",
        licence="P1 -- Part Design 1 (PD1) / P2 -- Part Design 2 (PDG)",
        see_also=("sketcher", "gsd", "assembly_design"),
    ),
    _wb(
        "assembly_design",
        "Assembly Design",
        _MECH,
        aliases=("assembly", "asd", "as1", "assemblage", "product design", "constraints"),
        summary="Positions components with constraints in a .CATProduct, computes degrees of freedom, and cuts assembly-level features across parts.",
        licence="P1 -- Assembly Design 1 (AS1) / P2 -- Assembly Design 2 (ASD)",
        see_also=("product_structure", "dmu_space_analysis"),
    ),
    _wb(
        "drafting",
        "Drafting",
        _MECH,
        aliases=(
            "generative drafting",
            "interactive drafting",
            "drawing",
            "mise en plan",
            "gdr",
            "gd1",
            "id1",
            "drd",
            "dr1",
            "2d drawing",
            "blueprint",
        ),
        summary="Produces .CATDrawing sheets. Generative Drafting projects views from the 3D and keeps them associative; Interactive Drafting draws 2D that is not linked to anything.",
        licence="P1 -- Interactive Drafting (ID1) / Generative Drafting 1 (GD1) / P2 -- Generative Drafting 2 (GDR)",
        see_also=("fta", "format.dxf"),
    ),
    _wb(
        "sheet_metal_design",
        "Generative Sheetmetal Design",
        _MECH,
        aliases=(
            "sheet metal",
            "sheet metal design",
            "sheetmetal",
            "smd",
            "sm1",
            "gsm",
            "tolerie",
            "tole",
            "bending",
            "unfold part",
        ),
        summary="Wall/flange/bend modelling with a single thickness and a bend table, and a synchronised folded and flattened view of the same part.",
        licence="P1 -- Sheetmetal Design 1 (SM1) / P2 -- Sheetmetal Design 2 (SMD)",
        aerospace="General sheet metal. Airframe skins, ribs and clips with joggles and runouts belong in Aerospace Sheet Metal Design (ASL), which is a different product with different commands.",
        see_also=("aerospace_sheet_metal", "sheet_metal_production"),
    ),
    _wb(
        "sheet_metal_production",
        "Sheetmetal Production",
        _MECH,
        aliases=("sheet metal production", "sh1", "unfold for production", "recognize sheetmetal"),
        summary="Recognises sheet metal features on an imported or non-native solid and produces the flat pattern for manufacture.",
        licence="P1 -- Sheetmetal Production 1 (SH1)",
        see_also=("sheet_metal_design",),
    ),
    _wb(
        "aerospace_sheet_metal",
        "Aerospace Sheet Metal Design",
        _MECH,
        aliases=(
            "aerospace sheetmetal",
            "aerospace sheet metal",
            "asl",
            "aero sheet metal",
            "tolerie aeronautique",
            "asm",
        ),
        summary="Surface-driven sheet metal for airframe parts: a Web on a support surface, surfacic flanges that follow a curved edge, joggles, cutbacks and springback-aware flattening.",
        licence="P3 -- Aerospace Sheetmetal Design 3 (ASL)",
        aerospace="This is the airframe workbench. Ribs, frames, clips, cleats, shear ties and skin doublers are modelled here because the flange follows a lofted support surface rather than a constant-thickness wall, and because Joggle is a first-class feature rather than something faked with a stamp.",
        see_also=("sheet_metal_design", "composites_design"),
    ),
    _wb(
        "structure_design",
        "Structure Design",
        _MECH,
        aliases=("structure design", "sr1", "beams", "profiles", "steelwork", "charpente"),
        summary="Places catalogue sections (I, U, L, T, C, Z, tube, angle) along lines as members, plus plates, ladders, stairs, handrails and footings, with end cuts and cutbacks.",
        licence="P1/P2 -- Structure Design 1 (SR1)",
        see_also=("weld_design", "equipment_support_structures"),
    ),
    _wb(
        "weld_design",
        "Weld Design",
        _MECH,
        aliases=("weld design", "wd1", "welding", "soudure", "weld bead"),
        summary="Creates weld features between parts in an assembly (fillet, butt, spot, seam, groove, plug, edge, surfacing) that carry into the drawing as ISO 2553 / AWS A2.4 symbols and into mass properties.",
        licence="P1 -- Weld Design 1 (WD1)",
    ),
    _wb(
        "mold_tooling_design",
        "Mold Tooling Design",
        _MECH,
        aliases=("mold tooling", "mould tooling", "mtd", "moule", "injection mould"),
        summary="Assembles a mould base from standard component catalogues (DME, Hasco, Futaba), adds inserts, ejectors, cooling channels and slides.",
        licence="P2 -- Mold Tooling Design 2 (MTD)",
        see_also=("core_cavity_design",),
    ),
    _wb(
        "core_cavity_design",
        "Core & Cavity Design",
        _MECH,
        aliases=("core and cavity", "ccv", "parting line", "core cavity"),
        summary="Splits a moulded part into core and cavity: pulling-direction analysis, transfer areas, parting line and parting surface.",
        licence="P2 -- Core & Cavity Design 2 (CCV)",
    ),
    _wb(
        "functional_molded_part",
        "Functional Molded Part",
        _MECH,
        aliases=("functional molded part", "fmp", "fm1", "functional moulded part", "plastic part"),
        summary="Models plastic parts by functional feature -- boss, rib, hole pattern, snap fit, grill -- on top of a shelled body, rather than as generic pads and pockets.",
        licence="P2 -- Functional Molded Parts 2 (FMP)",
    ),
    _wb(
        "fta",
        "3D Functional Tolerancing & Annotation",
        _MECH,
        aliases=(
            "fta",
            "ft1",
            "functional tolerancing and annotation",
            "3d annotation",
            "pmi",
            "mbd",
            "model based definition",
            "3d master",
            "gd&t in 3d",
            "cotation 3d",
        ),
        summary="Puts semantic GD&T, datums, dimensions and notes on the 3D model itself, in annotation planes and captured views, so the model can replace the drawing.",
        licence="P1 -- FTA 1 (FT1) / P2 -- FTA 2 (FTA)",
        aerospace="The basis of a 3D-master release: AS9102 first-article inspection and CMM programming consume the semantic PMI through STEP AP242 rather than a PDF drawing.",
        see_also=("drafting", "format.step"),
    ),
    _wb(
        "wireframe_surface",
        "Wireframe and Surface Design",
        _MECH,
        aliases=(
            "wireframe and surface",
            "wireframe & surface",
            "wsf",
            "ws1",
            "wireframe surface design",
            "filaire et surfacique",
        ),
        summary="The surfacing subset that ships inside Part Design's menu: points, lines, planes, extrude, revolve, offset, sweep, fill, join, split. Same commands as GSD, fewer of them.",
        licence="P1 -- Wireframe & Surface 1 (WS1)",
        see_also=("gsd", "part_design"),
    ),
    _wb(
        "healing_assistant",
        "Healing Assistant",
        _MECH,
        aliases=("healing assistant", "ha1", "heal imported geometry", "gap analysis"),
        summary="Batch-diagnoses and repairs imported surfaces: gaps, tangency breaks, tiny faces, and the topology that makes a join fail.",
        licence="P1 -- Healing Assistant 1 (HA1)",
        see_also=("diagnostic.not_connex", "gsd"),
    ),
    _wb(
        "part_design_feature_recognition",
        "Part Design Feature Recognition",
        _MECH,
        aliases=("feature recognition", "fr1", "recognize features", "dumb solid to features"),
        summary="Rebuilds a specification tree of recognisable features (pads, pockets, holes, fillets) on an imported dead solid.",
        licence="P1 -- Part Design Feature Recognition 1 (FR1)",
    ),
    _wb(
        "2d_layout",
        "2D Layout for 3D Design",
        _MECH,
        aliases=("2d layout", "lo1", "2d layout for 3d design", "layout"),
        summary="2D layout geometry stored inside the 3D document, so a scheme drawing and the model it drives stay in one file.",
        licence="P1 -- 2D Layout for 3D Design 1 (LO1)",
    ),
]


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

_SHAPE_WBS = [
    _wb(
        "gsd",
        "Generative Shape Design",
        _SHAPE,
        aliases=(
            "generative shape design",
            "gsd",
            "gs1",
            "surfacing",
            "surface design",
            "conception de formes",
            "surfaces",
        ),
        summary="The full associative surfacing workbench: every wireframe and surface command, the operations that trim and join them, and the analyses that check continuity.",
        licence="P1 -- GSD 1 (GS1) / P2 -- GSD 2 (GSD)",
        aerospace="Where the outer mould line lives. Loft the OML, cut station planes, extract rib and frame profiles, then feed those into Part Design or Aerospace Sheet Metal.",
        see_also=("wireframe_surface", "freestyle", "generative_shape_optimizer"),
    ),
    _wb(
        "generative_shape_optimizer",
        "Generative Shape Optimizer",
        _SHAPE,
        aliases=("shape optimizer", "gso", "wrap surface", "global deformation"),
        summary="Adds global deformation to GSD: wrap curve, wrap surface, bump, shape morphing, diabolo, affinity on a whole surface set.",
        licence="P2 -- Generative Shape Optimizer 2 (GSO)",
    ),
    _wb(
        "freestyle",
        "FreeStyle",
        _SHAPE,
        aliases=(
            "freestyle shaper",
            "freestyle",
            "fss",
            "fs1",
            "fso",
            "fsp",
            "freestyle optimizer",
            "freestyle profiler",
            "explicit surfaces",
            "control points",
        ),
        summary="Explicit (non-history) NURBS modelling by control point: build a patch, push its poles, and check the result with curvature and reflection analyses.",
        licence="P1 -- FreeStyle Shaper 1 (FS1) / P2 -- Shaper 2 (FSS), Optimizer (FSO), Profiler (FSP)",
        see_also=("gsd", "imagine_and_shape", "icem_shape_design"),
    ),
    _wb(
        "sketch_tracer",
        "Sketch Tracer",
        _SHAPE,
        aliases=("sketch tracer", "fsk", "skt", "immersive sketch", "image sketch", "blueprint image"),
        summary="Positions scanned sketches or photographs on planes in 3D so surfaces can be modelled over them.",
        licence="P1 -- FreeStyle Sketch Tracer 1 (FSK)",
    ),
    _wb(
        "imagine_and_shape",
        "Imagine & Shape",
        _SHAPE,
        aliases=("imagine and shape", "ima", "subdivision surface", "subdiv", "sub-d"),
        summary="Subdivision-surface concept modelling: start from a primitive cage, push and pull faces, then convert to NURBS.",
        licence="P2 -- Imagine & Shape 2 (IMA)",
    ),
    _wb(
        "dse",
        "Digitized Shape Editor",
        _SHAPE,
        aliases=("digitized shape editor", "dse", "point cloud", "scan data", "mesh import", "nuage de points"),
        summary="Imports and conditions scanned data: cloud import, filter, align, remove, tessellate, section, and curve creation on the mesh.",
        licence="P2 -- Digitized Shape Editor 2 (DSE)",
        see_also=("qsr", "format.stl"),
    ),
    _wb(
        "qsr",
        "Quick Surface Reconstruction",
        _SHAPE,
        aliases=("quick surface reconstruction", "qsr", "reverse engineering", "retro-conception"),
        summary="Turns conditioned scan data into surfaces: curvature mapping, automatic surface, power fit, canonical recognition.",
        licence="P2 -- Quick Surface Reconstruction 2 (QSR)",
        see_also=("dse",),
    ),
    _wb(
        "icem_shape_design",
        "ICEM Shape Design",
        _SHAPE,
        aliases=("icem", "icem shape design", "class a", "automotive class a", "aca", "icem surf"),
        summary="Class-A surfacing: the tightest continuity and highlight-line control CATIA offers, for visible exterior skin.",
        licence="Separate product line (ICEM Surf / Automotive Class A); not part of a standard MD configuration",
    ),
    _wb(
        "developed_shapes",
        "Developed Shapes",
        _SHAPE,
        aliases=("developed shapes", "dl1", "unfold surface", "flatten surface", "developpement"),
        summary="Unfolds ruled and developable surfaces to flat, and transfers curves and points between the folded and flat states.",
        licence="P1 -- Developed Shapes 1 (DL1)",
        aerospace="How a non-sheet-metal skin panel or a fairing is flattened for a template or a stretch-form tool.",
    ),
    _wb(
        "realistic_shape_optimizer",
        "Realistic Shape Optimizer",
        _SHAPE,
        aliases=("realistic shape optimizer", "rso", "springback compensation"),
        summary="Deforms a nominal surface onto measured data -- springback and tooling compensation.",
        licence="P2 -- Realistic Shape Optimizer 2 (RSO)",
    ),
    _wb(
        "shape_sculptor",
        "Shape Sculptor",
        _SHAPE,
        aliases=("shape sculptor", "dss", "mesh sculpting", "tessellated modelling"),
        summary="Direct sculpting of tessellated (mesh) geometry, for concept forms that are not going to be NURBS.",
        licence="P2 -- Shape Sculptor 2 (DSS)",
    ),
    _wb(
        "automotive_biw_fastening",
        "Automotive Body in White Fastening",
        _SHAPE,
        aliases=("body in white fastening", "biw", "abf", "spot welds", "biw fastening"),
        summary="Places and manages large populations of spot welds, adhesive beads and clinches on a body-in-white assembly.",
        licence="P3 -- Automotive Body in White Fastening 3 (ABF)",
    ),
]


# ---------------------------------------------------------------------------
# Composites -- aerospace-critical, so each product is listed separately.
# ---------------------------------------------------------------------------

_COMPOSITES = [
    _wb(
        "composites_design",
        "Composites Design",
        _MECH,
        aliases=(
            "composites design",
            "composite design",
            "cpd",
            "composites",
            "composite",
            "materiaux composites",
            "layup",
            "lay-up",
            "ply design",
        ),
        summary="Ply-based and zone-based composite part definition on a support surface: rosettes, laminates, zones, transition zones, stacking, plies, cores, cut pieces and drop-offs.",
        licence="P3 -- Composites Design 3 (CPD)",
        aerospace="The workbench a composite skin, spar or rib is actually defined in. Preliminary design settles thickness by zone; detailed design turns each zone into individual plies with an Edge of Part contour and a drop-off ramp.",
        see_also=("composites_manufacturing", "composites_engineering", "aerospace_sheet_metal"),
    ),
    _wb(
        "composites_manufacturing",
        "Composites Design for Manufacturing",
        _SHAPE,
        aliases=(
            "composites design for manufacturing",
            "composites manufacturing",
            "cpm",
            "producibility",
            "ply flattening",
            "fiber simulation",
        ),
        summary="Turns a designed layup into something that can be made: producibility (fibre draping) simulation, darts and splices, flattening, flat-pattern export and laser-projection files.",
        licence="P2 -- Composites Design for Manufacturing 2 (CPM)",
        aerospace="Where a ply that is geometrically fine is shown to be undrapable: the fibre simulation reports warp/weft deviation, and the fix is a dart, a splice or a re-oriented rosette.",
        see_also=("composites_design",),
    ),
    _wb(
        "composites_engineering",
        "Composites Engineering Design",
        _SHAPE,
        aliases=("composites engineering design", "composites engineering", "cpe"),
        summary="The engineering-side composites product: virtual stacking, ply exchange with analysis codes, grid design for stiffened panels, and the numerical/graphical ply analyses.",
        licence="P2 -- Composites Engineering 2 (CPE)",
        see_also=("composites_design", "format.ply_xml"),
    ),
    _wb(
        "composites_grid_design",
        "Composites Grid Design",
        _SHAPE,
        aliases=("composites grid design", "grid design", "stiffened panel", "grid stiffened"),
        summary="Lays out stiffener grids (iso-grid, ortho-grid) on a composite panel and drives the ply definition from them.",
        licence="Part of the Composites Engineering (CPE) scope",
    ),
]


# ---------------------------------------------------------------------------
# Analysis & Simulation
# ---------------------------------------------------------------------------

_ANALYSIS_WBS = [
    _wb(
        "gps",
        "Generative Part Structural Analysis",
        _ANALYSIS,
        aliases=(
            "generative part structural analysis",
            "gps",
            "gp1",
            "part analysis",
            "fea",
            "analyse structurale",
            "stress analysis",
            "static analysis",
        ),
        summary="Linear static and frequency analysis of a single part, meshed automatically, with restraints, loads and a von Mises image.",
        licence="P1 -- GPS 1 (GP1) / P2 -- GPS 2 (GPS)",
        see_also=("gas", "elfini", "advanced_meshing_tools"),
    ),
    _wb(
        "gas",
        "Generative Assembly Structural Analysis",
        _ANALYSIS,
        aliases=(
            "generative assembly structural analysis",
            "gas",
            "assembly analysis",
            "connections",
            "bolt tightening",
        ),
        summary="Extends GPS to an assembly: the connection types (fastened, contact, bolt tightening, rigid, smooth, welding, pressure fitting) that let two meshed parts interact.",
        licence="P2 -- GAS 2 (GAS); requires GPS",
        see_also=("gps", "elfini"),
    ),
    _wb(
        "elfini",
        "ELFINI Structural Analysis",
        _ANALYSIS,
        aliases=("elfini", "est", "elfini structural analysis", "advanced analysis", "buckling"),
        summary="The advanced solver layer: buckling, combined solutions, sensors, adaptivity, mesh refinement, custom solution parameters and external solver export.",
        licence="P2 -- ELFINI Structural Analysis 2 (EST); requires GPS",
        see_also=("gps", "gas", "generative_dynamic_response"),
    ),
    _wb(
        "generative_dynamic_response",
        "Generative Dynamic Response Analysis",
        _ANALYSIS,
        aliases=("dynamic response", "gdy", "harmonic response", "transient response", "modal"),
        summary="Harmonic and transient dynamic response on a modal basis: excitations, damping, restraint and load excitation sets.",
        licence="P2 -- Generative Dynamic Response Analysis 2 (GDY); requires EST",
    ),
    _wb(
        "thermal_analysis",
        "Thermal Analysis",
        _ANALYSIS,
        aliases=("thermal analysis", "heat transfer", "temperature field", "thermique"),
        summary="Steady-state and transient thermal solutions whose temperature field can then load a structural solution.",
        licence="P2 -- requires EST",
    ),
    _wb(
        "advanced_meshing_tools",
        "Advanced Meshing Tools",
        _ANALYSIS,
        aliases=(
            "advanced meshing tools",
            "amt",
            "meshing",
            "octree",
            "mesh part",
            "maillage",
            "beam mesher",
            "surface mesher",
        ),
        summary="Explicit mesh control: OCTREE tetrahedron and triangle meshers, the advancing-front surface mesher, beam mesher, mesh quality analysis and transitions between mesh parts.",
        licence="P2 -- Advanced Meshing Tools (AMT); requires GPS",
        see_also=("fem_surface", "fem_solid"),
    ),
    _wb(
        "fem_surface",
        "FEM Surface",
        _ANALYSIS,
        aliases=("fem surface", "fms", "shell mesh", "2d mesh", "surface mesh"),
        summary="Surface (shell) meshing and its properties, for thin-walled structures where a tetra mesh would be wrong.",
        licence="P2 -- FEM Surface 2 (FMS)",
        aerospace="The correct idealisation for skins, webs and ribs. A tetra mesh of a 1.6 mm skin needs three elements through thickness before it stops being nonsense; a shell needs one.",
    ),
    _wb(
        "fem_solid",
        "FEM Solid",
        _ANALYSIS,
        aliases=("fem solid", "fmd", "solid mesh", "3d mesh", "tetra mesh"),
        summary="Solid meshing and its properties, including the tetrahedron filler and the linear/parabolic element choice.",
        licence="P2 -- FEM Solid 2 (FMD)",
    ),
    _wb(
        "product_engineering_optimizer",
        "Product Engineering Optimizer",
        _KNOW,
        aliases=("product engineering optimizer", "peo", "optimization", "design of experiments", "doe"),
        summary="Drives free parameters against an objective under constraints, by gradient or simulated annealing, and runs designs of experiments.",
        licence="P2 -- Product Engineering Optimizer 2 (PEO)",
        see_also=("knowledge_advisor",),
    ),
]


# ---------------------------------------------------------------------------
# Digital Mockup
# ---------------------------------------------------------------------------

_DMU_WBS = [
    _wb(
        "dmu_navigator",
        "DMU Navigator",
        _DMU,
        aliases=("dmu navigator", "dmn", "dn1", "dmu", "walkthrough", "mockup review", "maquette numerique"),
        summary="Reviewing a large mockup: viewpoints, annotated views, markup, hyperlinks, groups, fly and walk navigation, turntable and publishing.",
        licence="P1 -- DMU Navigator 1 (DN1) / P2 -- DMU Navigator 2 (DMN)",
        see_also=("dmu_space_analysis", "setting.cache_management"),
    ),
    _wb(
        "dmu_space_analysis",
        "DMU Space Analysis",
        _DMU,
        aliases=(
            "dmu space analysis",
            "spa",
            "sp1",
            "clash",
            "interference",
            "clash detection",
            "sectioning",
            "interference check",
            "collision",
        ),
        summary="Clash, clearance and contact interference between components; sectioning; distance and band analysis; 3D compare; thickness and silhouette.",
        licence="P1 -- DMU Space Analysis 1 (SP1) / P2 -- DMU Space Analysis 2 (SPA)",
        see_also=("dmu_kinematics", "dmu_fitting"),
    ),
    _wb(
        "dmu_kinematics",
        "DMU Kinematics",
        _DMU,
        aliases=(
            "dmu kinematics",
            "kin",
            "kinematics",
            "mechanism",
            "joints",
            "cinematique",
            "motion simulation",
        ),
        summary="Builds a mechanism from joints, drives it with commands and laws, replays it, sweeps volumes and detects collisions during the motion.",
        licence="P2 -- DMU Kinematics Simulator 2 (KIN)",
        see_also=("dmu_fitting", "assembly_design"),
    ),
    _wb(
        "dmu_fitting",
        "DMU Fitting Simulation",
        _DMU,
        aliases=("dmu fitting", "fit", "fitting simulation", "assembly path", "removal path", "shuttle"),
        summary="Assembly and disassembly path studies: shuttle, track, smooth, automatic clash-aware path finder and maintainability sequences.",
        licence="P2 -- DMU Fitting Simulator 2 (FIT)",
        aerospace="How a removal-path study is done: can the LRU actually come out through the access panel, with a hand on it.",
    ),
    _wb(
        "dmu_optimizer",
        "DMU Optimizer",
        _DMU,
        aliases=("dmu optimizer", "dmo", "simplification", "wrapping", "space reservation"),
        summary="Produces simplified stand-ins for heavy geometry: wrapping, silhouette, thickness, offset, section and space reservation volumes.",
        licence="P2 -- DMU Optimizer 2 (DMO)",
    ),
    _wb(
        "dmu_tolerancing_review",
        "DMU Tolerancing Review",
        _DMU,
        aliases=("dmu tolerancing review", "dt1", "tolerancing review", "pmi review"),
        summary="Reads and reviews 3D tolerancing and annotation without an FTA licence to author it.",
        licence="P1 -- DMU Dimensioning & Tolerancing Review 1 (DT1)",
    ),
    _wb(
        "dmu_space_engineering_assistant",
        "DMU Space Engineering Assistant",
        _DMU,
        aliases=("space engineering assistant", "sea", "spe", "rule based check", "dmu checks"),
        summary="Runs rule-based checks over a mockup -- clearance rules, zoning rules, segregation rules -- rather than one interactive clash at a time.",
        licence="P2 -- DMU Space Engineering Assistant 2 (SPE)",
        aerospace="Where systems segregation rules live: hydraulic away from electrical, fuel away from ignition sources, each as a checkable rule rather than a reviewer's memory.",
    ),
    _wb(
        "dmu_composites_review",
        "DMU Composites Review",
        _DMU,
        aliases=("dmu composites review", "cpr", "ply review"),
        summary="Reviews a composite layup -- ply boundaries, stacking, drop-offs -- without a Composites Design licence.",
        licence="P2 -- DMU Composites Review 2 (CPR)",
    ),
    _wb(
        "dmu_2d_viewer",
        "DMU 2D Viewer",
        _DMU,
        aliases=("dmu 2d viewer", "2d viewer", "drawing viewer"),
        summary="Views and marks up 2D drawings alongside the 3D mockup.",
        licence="P2",
    ),
    _wb(
        "dmu_fastening_review",
        "DMU Fastening Review",
        _DMU,
        aliases=("dmu fastening review", "far", "fastener review"),
        summary="Reviews fastener populations placed as DMU fastener elements rather than as modelled solids.",
        licence="P2 -- DMU Fastening Review 2 (FAR)",
        aerospace="The answer to 'do not model 200,000 rivets as solids': fasteners exist as lightweight elements that can still be counted, checked and reviewed.",
    ),
]


# ---------------------------------------------------------------------------
# Equipment & Systems
# ---------------------------------------------------------------------------

_SYSTEMS = [
    _wb(
        "electrical_library",
        "Electrical Library",
        _EQUIP,
        aliases=("electrical library", "elb", "connectors", "cavities", "electrical devices"),
        summary="Defines electrical devices, connectors, contacts, cavities and bundle connectors as reusable catalogue components.",
        licence="P2 -- Electrical Library 2 (ELB)",
        see_also=("electrical_harness_installation",),
    ),
    _wb(
        "electrical_wire_routing",
        "Electrical Wire Routing",
        _EQUIP,
        aliases=("electrical wire routing", "ewr", "wire routing", "route wires"),
        summary="Routes individual wires through a defined bundle network and reports lengths back to the diagram.",
        licence="P2 -- Electrical Wire Routing 2 (EWR)",
    ),
    _wb(
        "electrical_harness_installation",
        "Electrical Harness Installation",
        _EQUIP,
        aliases=("electrical harness installation", "ehi", "harness", "bundle segment", "faisceau"),
        summary="Builds the physical harness in the 3D mockup: bundle segments, protective coverings, supports and branch points.",
        licence="P2 -- Electrical Harness Installation 2 (EHI)",
        see_also=("electrical_harness_flattening",),
    ),
    _wb(
        "electrical_harness_flattening",
        "Electrical Harness Flattening",
        _EQUIP,
        aliases=(
            "electrical harness flattening",
            "ehf",
            "formboard",
            "nailboard",
            "flatten harness",
            "harness drawing",
        ),
        summary="Flattens a 3D harness into the 2D formboard (nailboard) drawing the harness shop actually builds on.",
        licence="P2 -- Electrical Harness Flattening 2 (EHF)",
        aerospace="The deliverable at the end of the harness chain: 3D route, then flatten, then the full-size formboard drawing with branch lengths and connector callouts.",
    ),
    _wb(
        "electrical_connectivity_diagram",
        "Electrical Connectivity Diagrams",
        _EQUIP,
        aliases=("electrical connectivity diagrams", "eld", "wiring diagram", "electrical diagram", "schema electrique"),
        summary="The 2D logical wiring diagram whose connectivity the 3D routing must satisfy.",
        licence="P2 -- Electrical Connectivity Diagrams 2 (ELD)",
    ),
    _wb(
        "electrical_cableway_routing",
        "Electrical Cableway Routing",
        _EQUIP,
        aliases=("electrical cableway routing", "ecr", "cableway", "cable tray", "chemin de cables"),
        summary="Routes cableways and trays as a network the harness then follows.",
        licence="P2 -- Electrical Cableway Routing 2 (ECR)",
    ),
    _wb(
        "electrical_system_functional_definition",
        "Electrical System Functional Definition",
        _EQUIP,
        aliases=("electrical system functional definition", "efd", "functional electrical", "signal routing"),
        summary="The functional layer above the diagram: systems, signals and their allocation to physical wires.",
        licence="P2 -- Electrical System Functional Definition 2 (EFD)",
    ),
    _wb(
        "circuit_board_design",
        "Circuit Board Design",
        _EQUIP,
        aliases=("circuit board design", "cbd", "pcb", "idf", "printed circuit board"),
        summary="Board outline, keep-out areas and component placement, exchanged with ECAD through IDF.",
        licence="P1 -- Circuit Board Design 1 (CBD)",
    ),
    _wb(
        "tubing_design",
        "Tubing Design",
        _EQUIP,
        aliases=("tubing design", "tub", "tubing", "tube routing", "tuyauterie"),
        summary="Routes small-bore tubing from a specification catalogue, with bends, connectors and flow direction.",
        licence="P2 -- Tubing Design 2 (TUB)",
        see_also=("piping_design", "tubing_diagrams"),
    ),
    _wb(
        "tubing_diagrams",
        "Tubing Diagrams",
        _EQUIP,
        aliases=("tubing diagrams", "tud"),
        summary="The 2D logical tubing schematic the 3D route is validated against.",
        licence="P2 -- Tubing Diagrams 2 (TUD)",
    ),
    _wb(
        "piping_design",
        "Piping Design",
        _EQUIP,
        aliases=("piping design", "pip", "piping", "pipe routing", "line id"),
        summary="Routes piping runs from a spec-driven catalogue: pipes, reducers, valves, flanges, line IDs and bends resolved against a .spec file.",
        licence="P2 -- Piping Design 2 (PIP)",
        see_also=("piping_instrumentation_diagrams", "tubing_design"),
    ),
    _wb(
        "piping_instrumentation_diagrams",
        "Piping & Instrumentation Diagrams",
        _EQUIP,
        aliases=("piping and instrumentation diagrams", "p&id", "pid", "p and id"),
        summary="The P&ID schematic, whose line list and instrument tags drive the 3D piping route.",
        licence="P2 -- Piping & Instrumentation Diagrams 2 (PID)",
    ),
    _wb(
        "hvac_design",
        "HVAC Design",
        _EQUIP,
        aliases=("hvac design", "hva", "ducting", "hvac", "air conditioning", "gaine"),
        summary="Routes ducting from an HVAC specification catalogue: rectangular and round ducts, transitions, fittings.",
        licence="P2 -- HVAC Design 2 (HVA)",
    ),
    _wb(
        "hvac_diagrams",
        "HVAC Diagrams",
        _EQUIP,
        aliases=("hvac diagrams", "hvd"),
        summary="The 2D HVAC schematic behind the 3D duct route.",
        licence="P2 -- HVAC Diagrams 2 (HVD)",
    ),
    _wb(
        "waveguide_design",
        "Waveguide Design",
        _EQUIP,
        aliases=("waveguide design", "wav", "waveguide", "guide d ondes"),
        summary="Routes rectangular waveguide runs with their bends, twists and flanges.",
        licence="P2 -- Waveguide Design 2 (WAV)",
    ),
    _wb(
        "waveguide_diagrams",
        "Waveguide Diagrams",
        _EQUIP,
        aliases=("waveguide diagrams", "wgd"),
        summary="The 2D waveguide schematic.",
        licence="P2 -- Waveguide Diagrams 2 (WGD)",
    ),
    _wb(
        "raceway_conduit_design",
        "Raceway & Conduit Design",
        _EQUIP,
        aliases=("raceway and conduit design", "rcd", "raceway", "conduit"),
        summary="Routes raceways and conduits as a spec-driven network.",
        licence="P2 -- Raceway & Conduit Design 2 (RCD)",
    ),
    _wb(
        "hanger_design",
        "Hanger Design",
        _EQUIP,
        aliases=("hanger design", "hgr", "hanger", "pipe support", "support"),
        summary="Places supports and hangers against routed runs, from a catalogue.",
        licence="P2 -- Hanger Design 2 (HGR)",
    ),
    _wb(
        "equipment_support_structures",
        "Equipment Support Structures",
        _EQUIP,
        aliases=("equipment support structures", "ess", "eqt", "equipment arrangement", "support structure"),
        summary="Arranges equipment and builds the secondary structure that carries it.",
        licence="P2 -- Equipment Arrangement 2 (EQT)",
    ),
    _wb(
        "systems_space_reservation",
        "Systems Space Reservation",
        _EQUIP,
        aliases=("systems space reservation", "ssr", "space reservation", "reservation volume"),
        summary="Declares the volume a system will need before it is routed, so the space is defended in the mockup.",
        licence="P2 -- Systems Space Reservation 2 (SSR)",
        aerospace="How routing corridors and maintenance-access envelopes are claimed at the zoning stage rather than argued about at first assembly.",
    ),
    _wb(
        "systems_routing",
        "Systems Routing",
        _EQUIP,
        aliases=("systems routing", "srt", "route definition", "run"),
        summary="The generic routing engine (runs, route paths, connectors) that the discipline products specialise.",
        licence="P1 -- Systems Routing 1 (SRT)",
    ),
    _wb(
        "systems_diagrams",
        "Systems Diagrams",
        _EQUIP,
        aliases=("systems diagrams", "sdi", "logical diagram", "schematic"),
        summary="The generic 2D schematic editor the discipline diagram products are built on.",
        licence="P2 -- Systems Diagrams 2 (SDI)",
    ),
    _wb(
        "compartment_and_access",
        "Compartment and Access",
        _EQUIP,
        aliases=("compartment and access", "cna", "compartment", "zoning", "access"),
        summary="Defines compartments and access zones as objects in the mockup.",
        licence="P2 -- Compartment & Access 2 (CNA)",
        aerospace="Where ATA-style zone breakdown is modelled so systems segregation and access can be checked against it.",
    ),
    _wb(
        "plant_layout",
        "Plant Layout",
        _EQUIP,
        aliases=("plant layout", "plo", "aec", "plant"),
        summary="Grid-based plant and facility layout with area objects and equipment placement.",
        licence="P1 -- Plant Layout 1 (PLO)",
    ),
    _wb(
        "ship_structure_detail_design",
        "Ship Structure Detail Design",
        _EQUIP,
        aliases=("ship structure detail design", "sdd", "ship structure"),
        summary="Marine structural detail: plates, stiffeners, brackets and openings on a hull.",
        licence="P2 -- Ship Structure Detail Design 2 (SDD)",
    ),
    _wb(
        "structure_functional_design",
        "Structure Functional Design",
        _EQUIP,
        aliases=("structure functional design", "sfd"),
        summary="Functional-level structural definition ahead of detailed member modelling.",
        licence="P2 -- Structure Functional Design 2 (SFD)",
    ),
]


# ---------------------------------------------------------------------------
# Ergonomics
# ---------------------------------------------------------------------------

_ERGONOMICS = [
    _wb(
        "human_builder",
        "Human Builder",
        _ERGO,
        aliases=("human builder", "hbr", "manikin", "mannequin", "human model", "dummy"),
        summary="Creates and positions manikins by percentile, gender and population (ANSUR, NHANES), with vision windows, reach envelopes and inverse-kinematics posture.",
        licence="P2 -- Human Builder 2 (HBR)",
        aerospace="Cockpit reach and vision studies, cabin egress, and whether a technician can physically get an arm to the fastener being designed.",
        see_also=("human_posture_analysis", "human_activity_analysis"),
    ),
    _wb(
        "human_measurements_editor",
        "Human Measurements Editor",
        _ERGO,
        aliases=("human measurements editor", "hme", "anthropometry", "manikin dimensions"),
        summary="Edits the anthropometric variables behind a manikin, individually or by population statistics.",
        licence="P2 -- Human Measurements Editor 2 (HME)",
    ),
    _wb(
        "human_posture_analysis",
        "Human Posture Analysis",
        _ERGO,
        aliases=("human posture analysis", "hpa", "posture", "segment angles", "rula"),
        summary="Scores a manikin's posture against preferred joint angles and reports per-segment comfort.",
        licence="P2 -- Human Posture Analysis 2 (HPA)",
    ),
    _wb(
        "human_activity_analysis",
        "Human Activity Analysis",
        _ERGO,
        aliases=(
            "human activity analysis",
            "haa",
            "lift lower",
            "niosh",
            "snook",
            "push pull",
            "biomechanics",
            "rula analysis",
        ),
        summary="Task-level ergonomic assessment: lift/lower (NIOSH, Snook-Ciriello), push, pull, carry, RULA and single-action biomechanics.",
        licence="P2 -- Human Activity Analysis 2 (HAA)",
    ),
]


# ---------------------------------------------------------------------------
# Machining
# ---------------------------------------------------------------------------

_MACHINING = [
    _wb(
        "prismatic_machining",
        "Prismatic Machining",
        _MACH,
        aliases=("prismatic machining", "pmg", "pg1", "2.5 axis", "milling", "pocketing", "usinage"),
        summary="2.5-axis milling and drilling: facing, pocketing, profile contouring, curve following, groove milling and the whole drilling family.",
        licence="P1 -- Prismatic Machining 1 (PG1) / P2 -- Prismatic Machining 2 (PMG)",
        see_also=("surface_machining", "advanced_machining"),
    ),
    _wb(
        "surface_machining",
        "Surface Machining",
        _MACH,
        aliases=("surface machining", "smg", "3 axis surface machining", "3 axis milling"),
        summary="3-axis surface milling: roughing, sweeping, pencil, spiral, contour-driven, isoparametric and projection operations.",
        licence="P2 -- 3 Axis Surface Machining 2 (SMG)",
    ),
    _wb(
        "advanced_machining",
        "Advanced Machining",
        _MACH,
        aliases=("advanced machining", "amg", "4 axis", "5 axis machining", "multi axis"),
        summary="Combines prismatic and surface machining and adds 4- and 5-axis operations, mill-turn and machine simulation.",
        licence="P2 -- Advanced Machining 2 (AMG)",
    ),
    _wb(
        "multi_axis_surface_machining",
        "Multi-Axis Surface Machining",
        _MACH,
        aliases=(
            "multi-axis surface machining",
            "mmg",
            "5 axis surface machining",
            "flank contouring",
            "tube machining",
        ),
        summary="4- and 5-axis surface operations: multi-axis curve, sweeping, flank contouring, tube machining and multi-axis drilling.",
        licence="P2 -- Multi-Axis Surface Machining 2 (MMG)",
    ),
    _wb(
        "lathe_machining",
        "Lathe Machining",
        _MACH,
        aliases=("lathe machining", "lmg", "lg1", "turning", "tournage"),
        summary="Turning: rough turning, finish turning, grooving, threading, recess and axial drilling on a lathe part operation.",
        licence="P1 -- Lathe Machining 1 (LG1) / P2 -- Lathe Machining 2 (LMG)",
    ),
    _wb(
        "multi_slide_lathe_machining",
        "Multi-Slide Lathe Machining",
        _MACH,
        aliases=("multi-slide lathe machining", "mlg", "multi slide", "swiss lathe"),
        summary="Turning on machines with several slides and spindles, with synchronisation between channels.",
        licence="P2 -- Multi-Slide Lathe Machining 2 (MLG)",
    ),
    _wb(
        "wire_edm",
        "Wire EDM",
        _MACH,
        aliases=("wire edm", "edm", "wire cut", "electroerosion"),
        summary="2- and 4-axis wire electro-discharge machining operations.",
        licence="P2",
    ),
    _wb(
        "stl_machining",
        "STL Machining",
        _MACH,
        aliases=("stl machining", "mesh machining"),
        summary="Machining driven directly from tessellated STL geometry rather than exact surfaces.",
        licence="P2",
    ),
    _wb(
        "prismatic_machining_preparation_assistant",
        "Prismatic Machining Preparation Assistant",
        _MACH,
        aliases=("prismatic machining preparation assistant", "mpa", "machining feature recognition"),
        summary="Recognises machinable features (holes, pockets, profiles) on the design part and proposes the operations for them.",
        licence="P2 -- Prismatic Machining Preparation Assistant 2 (MPA)",
    ),
    _wb(
        "nc_manufacturing_review",
        "NC Manufacturing Review",
        _MACH,
        aliases=("nc manufacturing review", "ncg", "ng1", "toolpath review", "replay"),
        summary="Replays and reviews tool paths, material removal and machining time without a licence to author them.",
        licence="P1 -- NC Manufacturing Review 1 (NG1) / P2 (NCG)",
    ),
    _wb(
        "nc_manufacturing_infrastructure",
        "NC Manufacturing Infrastructure",
        _MACH,
        aliases=("nc manufacturing infrastructure", "manufacturing infrastructure", "part operation", "machining axis"),
        summary="The shared machining foundation: part operation, machine, setup, design part, stock, fixture, safety plane, tool catalogue and post-processor tables.",
        licence="Included with any machining product",
        see_also=("prismatic_machining", "format.apt"),
    ),
    _wb(
        "machine_tool_builder",
        "NC Machine Tool Builder",
        _MACH,
        aliases=("nc machine tool builder", "mbg", "machine builder", "kinematic machine"),
        summary="Builds the kinematic model of a machine tool so simulation can detect real axis limits and collisions.",
        licence="P2 -- NC Machine Tool Builder 2 (MBG)",
    ),
    _wb(
        "machine_tool_simulation",
        "NC Machine Tool Simulation",
        _MACH,
        aliases=("nc machine tool simulation", "msg", "machine simulation", "collision check"),
        summary="Simulates the tool path on the modelled machine, in machine coordinates, with collision and travel-limit checking.",
        licence="P2 -- NC Machine Tool Simulation 2 (MSG)",
    ),
    _wb(
        "stl_rapid_prototyping",
        "STL Rapid Prototyping",
        _MACH,
        aliases=("stl rapid prototyping", "stl export", "tl1", "3d printing", "additive"),
        summary="Tessellates a solid to STL with an explicit sag/deviation control, for printing or downstream mesh use.",
        licence="P1 -- STL Rapid Prototyping 1 (TL1) / P2 (STL)",
        see_also=("format.stl",),
    ),
]


# ---------------------------------------------------------------------------
# Knowledgeware
# ---------------------------------------------------------------------------

_KNOWLEDGEWARE = [
    _wb(
        "knowledge_advisor",
        "Knowledge Advisor",
        _KNOW,
        aliases=("knowledge advisor", "kwa", "formulas", "rules", "parameters", "knowledgeware", "f(x)"),
        summary="Parameters, formulas, rules, checks, reactions and design tables inside a part or product.",
        licence="P2 -- Knowledge Advisor 2 (KWA)",
        see_also=("knowledge_expert", "product_knowledge_template"),
    ),
    _wb(
        "knowledge_expert",
        "Knowledge Expert",
        _KNOW,
        aliases=("knowledge expert", "kwe", "ke1", "expert rules", "rule base", "expert check"),
        summary="Rule bases that apply across many documents: expert rules and checks stored in a .CATRuleBase and run as a compliance sweep.",
        licence="P1 -- Knowledge Expert 1 (KE1) / P2 -- Knowledge Expert 2 (KWE)",
    ),
    _wb(
        "product_knowledge_template",
        "Product Knowledge Template",
        _KNOW,
        aliases=(
            "product knowledge template",
            "pkt",
            "kt1",
            "power copy",
            "powercopy",
            "user feature",
            "udf",
            "document template",
        ),
        summary="Reusable design templates: Power Copy, User Feature (UDF), Document Template and contextual UDFs, instantiated from a catalogue.",
        licence="P1 -- Product Knowledge Template 1 (KT1) / P2 (PKT)",
        see_also=("catalog_editor", "knowledge_advisor"),
    ),
    _wb(
        "business_process_knowledge_template",
        "Business Process Knowledge Template",
        _KNOW,
        aliases=("business process knowledge template", "bkt", "bk2", "business process"),
        summary="Captures a whole design process, not one feature: sequences of actions with their own knowledge and UI.",
        licence="P3 -- Business Process Knowledge Template 3 (BKT)",
    ),
]


# ---------------------------------------------------------------------------
# Adjacent product lines the user will still name.
# ---------------------------------------------------------------------------

_ADJACENT = [
    _wb(
        "enovia_vpm",
        "ENOVIA V5 VPM",
        _INFRA,
        aliases=("enovia", "vpm", "vpm navigator", "vpn", "enovia lca", "smarteam", "pdm"),
        summary="The V5-era PLM back end: check-in/check-out, revisions, effectivity, where-used and the VPM Navigator client inside CATIA.",
        licence="Separate ENOVIA product line",
        see_also=("workflow.change_release",),
    ),
    _wb(
        "delmia_v5",
        "DELMIA V5",
        _INFRA,
        aliases=("delmia", "dpm", "process engineer", "robotics", "digital manufacturing"),
        summary="The V5 manufacturing product line: DPM assembly and machining process planning, robotics, resource layout and shop-floor documentation.",
        licence="Separate DELMIA product line",
    ),
    _wb(
        "caa_rade",
        "CAA V5 / RADE",
        _INFRA,
        aliases=("caa", "caa v5", "rade", "c++ api", "caa rade", "component application architecture"),
        summary="The C++ API and its development environment: object modeler, interactive dashboard, source checker, unit test manager. Builds compiled add-ins, not macros.",
        licence="Separate CAA RADE licence; requires a matching compiler version per release",
        see_also=("api.automation_root",),
    ),
]


_ALL: list[Entry] = [
    *_INFRASTRUCTURE,
    *_MECHANICAL,
    *_SHAPE_WBS,
    *_COMPOSITES,
    *_ANALYSIS_WBS,
    *_DMU_WBS,
    *_SYSTEMS,
    *_ERGONOMICS,
    *_MACHINING,
    *_KNOWLEDGEWARE,
    *_ADJACENT,
]

# Published before anything imports a command module, so `Entry.location()` can
# resolve a workbench key to its display name without a circular import.
WORKBENCH_NAMES.update({item.key: item.name for item in _ALL})


_DISAMBIGUATIONS = [
    Disambiguation(
        term="sheet metal",
        aliases=("sheetmetal", "tolerie", "sheet metal design"),
        options=(
            "Generative Sheetmetal Design (SMD) -- general sheet metal, constant thickness, wall/flange/bend, bend table",
            "Aerospace Sheet Metal Design (ASL) -- surface-driven airframe parts, Web + surfacic Flange + Joggle",
            "Sheetmetal Production (SH1) -- recognising sheet metal on an imported solid to get a flat pattern",
        ),
        guidance="If the part follows a lofted support surface, has a joggle, or is called a rib/frame/clip/cleat, it is ASL. If it is a bracket bent from flat stock with a constant bend radius, it is SMD.",
    ),
    Disambiguation(
        term="surface design",
        aliases=("surfacing", "surface workbench"),
        options=(
            "Generative Shape Design (GSD) -- the full associative surfacing workbench",
            "Wireframe and Surface Design (WSF/WS1) -- the same commands, a smaller subset, inside Part Design's menu",
            "FreeStyle (FSS) -- explicit control-point NURBS, no history",
            "Imagine & Shape (IMA) -- subdivision surfaces for concept form",
        ),
        guidance="GSD and WSF share command names and dialogs; a WSF licence simply does not offer the advanced ones (adaptive sweep, styling fillet, wrap). FreeStyle is a different modelling paradigm, not a bigger GSD.",
    ),
    Disambiguation(
        term="structural analysis",
        aliases=("fea", "stress analysis", "analysis workbench"),
        options=(
            "Generative Part Structural Analysis (GPS) -- one part",
            "Generative Assembly Structural Analysis (GAS) -- several parts plus connections",
            "ELFINI Structural Analysis (EST) -- buckling, sensors, adaptivity, advanced solution control",
            "Generative Dynamic Response Analysis (GDY) -- harmonic and transient response",
        ),
        guidance="They are layers, not alternatives: GAS needs GPS, and EST needs both. Connections between parts are the GAS licence; a buckling case is the EST licence.",
    ),
    Disambiguation(
        term="dmu",
        aliases=("digital mockup", "mockup"),
        options=(
            "DMU Navigator (DMN) -- reviewing, navigating, marking up",
            "DMU Space Analysis (SPA) -- clash, clearance, sectioning, distance",
            "DMU Kinematics (KIN) -- joints, mechanisms, motion",
            "DMU Fitting Simulation (FIT) -- assembly and removal paths",
            "DMU Optimizer (DMO) -- simplification and space reservation",
        ),
        guidance="Clash between two static parts is SPA. Clash during a motion is KIN's collision-during-simulation. Clash along a removal path is FIT.",
    ),
    Disambiguation(
        term="drafting",
        aliases=("drawing", "mise en plan"),
        options=(
            "Generative Drafting (GD1/GDR) -- views projected from the 3D, associative, update from model",
            "Interactive Drafting (ID1) -- 2D drawn by hand in the sheet, linked to nothing",
        ),
        guidance="A view that updates when the part changes is generative. A view that has to be redrawn is interactive. One .CATDrawing can hold both.",
    ),
    Disambiguation(
        term="geometrical set",
        aliases=("geometric set", "ordered geometrical set", "body", "corps"),
        options=(
            "Body (PartBody and other bodies) -- holds solid features, participates in boolean operations",
            "Geometrical Set -- holds wireframe and surfaces, no order, no Define In Work Object",
            "Ordered Geometrical Set -- wireframe and surfaces with an explicit order and an insertion point",
        ),
        guidance="Choose an Ordered Geometrical Set when the construction order matters and features must be inserted mid-history; choose a plain Geometrical Set for a flat container of reference geometry. Mixing conventions within one part is the usual source of confusion.",
    ),
]


SECTION = Section("workbenches", _ALL, _DISAMBIGUATIONS)
