"""The CATIA V5 knowledge base: data integrity, recognition, and honesty.

No database fixture is requested anywhere in this file, so it runs in the fast
offline loop. The knowledge base is pure data and pure functions over it.

Three kinds of test live here and they defend different things.

**Coverage** is a contract, not a metric. The specification this package
implements names a surface area the assistant must recognise, and the tests in
`TestCoverage` assert that every part of it is actually present. They are
written as term lists rather than counts on purpose: a count passes when someone
deletes Aerospace Sheet Metal and adds thirty machining operations, and a term
list does not.

**Precision** is the harder half. This vocabulary contains `fit`, `add`, `part`,
`box` and `pip`, and an index that matches them unanchored is worse than no
index: it answers confidently about the wrong thing. `TestPrecision` is the set
of sentences that must produce *nothing*.

**Honesty** is what the package promises and what a wrong answer costs most on.
A missing German translation must be reported as missing, never filled in with
the English name; an informal product code must say it is informal. Those are
`TestHonesty`.
"""

from __future__ import annotations

import pytest

from app.catia_kb import (
    Kind,
    brief,
    catia_knowledge,
    describe,
    expand_query,
    localised,
    normalise_language,
    product,
    recognise,
    registry,
    reset_catia_knowledge,
    translations,
)
from app.catia_kb.languages import LANGUAGES, NAMES, TRANSLATED
from app.catia_kb.licensing import INFORMAL_TRIGRAMS, TRIGRAMS
from app.catia_kb.recognise import AMBIGUOUS_WORDS, NEVER_BARE
from app.catia_kb.registry import _fold, missing_cross_references, untranslated
from app.catia_kb.types import Entry, bulk, slug


@pytest.fixture(scope="module")
def index():
    return registry()


def _keys(text: str, **kwargs) -> set[str]:
    return set(recognise(text, **kwargs).keys())


def _matches_any(text: str, *keys: str, **kwargs) -> bool:
    found = _keys(text, **kwargs)
    return any(key in found for key in keys)


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


class TestIntegrity:
    def test_registry_builds_and_is_substantial(self, index):
        # Not a coverage assertion -- a floor that catches a data module failing
        # to import and silently contributing nothing.
        assert len(index) > 1_000

    def test_no_duplicate_keys(self):
        # `registry()` raises on a duplicate; this asserts the guard is reached
        # rather than that it happens to hold today.
        assert registry() is registry()

    def test_every_cross_reference_resolves(self):
        assert missing_cross_references() == []

    def test_no_orphaned_translations(self):
        # A renamed command silently orphans its German name, because the
        # translation tables are keyed by entry key.
        assert untranslated() == []

    def test_every_entry_has_a_key_name_and_kind(self, index):
        for entry in index:
            assert entry.key and entry.name and isinstance(entry.kind, Kind)

    def test_every_command_names_a_real_workbench(self, index):
        for entry in index.by_kind(Kind.COMMAND):
            assert entry.workbench, f"{entry.key} has no workbench"
            assert entry.workbench in index.entries, f"{entry.key} -> {entry.workbench}"

    def test_surface_index_covers_every_entry(self, index):
        for entry in index:
            assert index.lookup(entry.name), f"{entry.key} is not reachable by its own name"

    def test_bulk_rejects_duplicates_in_one_block(self):
        with pytest.raises(ValueError, match="duplicate"):
            bulk("Pad | pad\nPad | extrude", workbench="part_design")

    def test_bulk_skips_comments_and_blanks(self):
        entries = bulk("# a note\n\nPad | pad, extrude\n", workbench="part_design")
        assert [e.name for e in entries] == ["Pad"]
        assert "extrude" in entries[0].aliases

    def test_entry_refuses_to_exist_without_identity(self):
        with pytest.raises(ValueError):
            Entry(key="", name="x", kind=Kind.COMMAND)

    def test_slug_is_stable_over_punctuation(self):
        assert slug("Thread/Tap") == "thread_tap"
        assert slug("Multi-sections Solid") == "multi_sections_solid"

    def test_folding_unifies_accents_case_and_punctuation(self):
        assert _fold("Congé d'arête") == _fold("CONGE D ARETE") == "conge d arete"


# ---------------------------------------------------------------------------
# Coverage: the specification, asserted
# ---------------------------------------------------------------------------


class TestCoverage:
    """Every domain the assistant is required to recognise is actually present."""

    @pytest.mark.parametrize(
        "term",
        [
            # Infrastructure
            "Product Structure", "Material Library", "Catalog Editor", "Photo Studio",
            "Feature Dictionary Editor", "V4 Integration", "CATDUA V5", "Batch Monitor",
            # Mechanical Design
            "Sketcher", "Part Design", "Assembly Design", "Drafting",
            "Generative Sheetmetal Design", "Aerospace Sheet Metal Design",
            "Sheetmetal Production", "Structure Design", "Weld Design",
            "Mold Tooling Design", "Functional Molded Part",
            "3D Functional Tolerancing & Annotation", "Wireframe and Surface Design",
            "Healing Assistant", "Part Design Feature Recognition",
            # Shape
            "Generative Shape Design", "Generative Shape Optimizer", "FreeStyle",
            "Imagine & Shape", "Sketch Tracer", "Digitized Shape Editor",
            "Quick Surface Reconstruction", "ICEM Shape Design", "Developed Shapes",
            "Realistic Shape Optimizer", "Shape Sculptor",
            "Automotive Body in White Fastening",
            # Composites
            "Composites Design", "Composites Design for Manufacturing",
            "Composites Engineering Design", "Composites Grid Design",
            # Analysis
            "Generative Part Structural Analysis", "Generative Assembly Structural Analysis",
            "ELFINI Structural Analysis", "Generative Dynamic Response Analysis",
            "Thermal Analysis", "Advanced Meshing Tools", "FEM Surface", "FEM Solid",
            "Product Engineering Optimizer",
            # DMU
            "DMU Navigator", "DMU Space Analysis", "DMU Kinematics",
            "DMU Fitting Simulation", "DMU Optimizer", "DMU Tolerancing Review",
            "DMU Space Engineering Assistant", "DMU Composites Review", "DMU 2D Viewer",
            # Equipment & Systems
            "Electrical Library", "Electrical Wire Routing",
            "Electrical Harness Installation", "Electrical Harness Flattening",
            "Electrical Connectivity Diagrams", "Electrical Cableway Routing",
            "Circuit Board Design", "Tubing Design", "Piping Design",
            "Piping & Instrumentation Diagrams", "HVAC Design", "Waveguide Design",
            "Raceway & Conduit Design", "Hanger Design", "Equipment Support Structures",
            "Systems Space Reservation", "Systems Routing", "Systems Diagrams",
            "Compartment and Access", "Plant Layout",
            # Ergonomics
            "Human Builder", "Human Measurements Editor", "Human Posture Analysis",
            "Human Activity Analysis",
            # Machining
            "Prismatic Machining", "Surface Machining", "Advanced Machining",
            "Multi-Axis Surface Machining", "Lathe Machining",
            "Multi-Slide Lathe Machining", "Wire EDM", "STL Machining",
            "NC Manufacturing Review", "NC Machine Tool Builder", "STL Rapid Prototyping",
            # Knowledgeware
            "Knowledge Advisor", "Knowledge Expert", "Product Knowledge Template",
            "Business Process Knowledge Template",
        ],
    )
    def test_every_workbench_is_present(self, index, term):
        found = index.lookup(term)
        assert found, f"workbench missing: {term}"
        assert any(e.kind is Kind.WORKBENCH for e in found), term

    def test_every_workbench_says_where_to_find_it_and_what_it_costs(self, index):
        for entry in index.by_kind(Kind.WORKBENCH):
            assert entry.menu.startswith("Start >"), f"{entry.key} has no Start-menu path"
            assert entry.licence, f"{entry.key} has no licence tier"

    @pytest.mark.parametrize(
        "term",
        [
            # Sketcher (3.1)
            "Profile", "Elongated Hole", "Keyhole Profile", "Tri-Tangent Circle",
            "Quick Trim", "Project 3D Elements", "Auto Constraint", "Sketch Analysis",
            "Positioned Sketch", "Reflect Line",
            # Part Design (3.2)
            "Pad", "Multi-Pad", "Pocket", "Shaft", "Groove", "Hole", "Rib", "Slot",
            "Stiffener", "Multi-sections Solid", "Edge Fillet", "Variable Radius Fillet",
            "Chordal Fillet", "Face-Face Fillet", "Tritangent Fillet", "Chamfer",
            "Draft Angle", "Shell", "Thickness", "Thread/Tap", "Remove Face",
            "Replace Face", "Sew Surface", "Close Surface", "Thick Surface",
            "Rectangular Pattern", "Circular Pattern", "User Pattern", "Affinity",
            "Union Trim", "Remove Lump", "Define In Work Object", "Copy/Paste Special",
            # GSD / WSF (3.3)
            "Extrude", "Revolve", "Offset", "Variable Offset", "Rough Offset", "Sweep",
            "Adaptive Sweep", "Fill", "Multi-sections Surface", "Blend", "Join",
            "Healing", "Untrim", "Disassemble", "Split", "Trim", "Boundary",
            "Extrapolate", "Shape Fillet", "Invert Orientation", "Unfold", "Wrap Curve",
            "Wrap Surface", "Bump", "Shape Morphing", "Connect Checker",
            "Porcupine Curvature Analysis", "Isophote Mapping", "Distance Analysis",
            "Helix", "Spiral", "Spine", "Law", "Extremum",
            # FreeStyle / Imagine & Shape (3.4)
            "Control Points", "Match Surface", "Multi-Side Surface", "Net Surface",
            "Styling Sweep", "Convert to NURBS", "Crease",
            # Assembly (3.5)
            "Coincidence Constraint", "Contact Constraint", "Offset Constraint",
            "Angle Constraint", "Fix Component", "Quick Constraint", "Reuse Pattern",
            "Flexible/Rigid Sub-Assembly", "Assembly Split", "Assembly Hole",
            "Bill of Material", "Degrees of Freedom", "Explode", "Publication",
            "Fast Multi Instantiation", "Visualization Mode",
            # Sheet metal (3.6)
            "Sheet Metal Parameters", "Wall", "Wall On Edge", "Rolled Wall", "Hopper",
            "Hem", "Tear Drop", "Conical Bend", "Bend From Flat", "Flanged Hole",
            "Louver", "Bridge", "Mitre Corner", "Corner Relief", "Recognize",
            # Aerospace sheet metal (3.6, ASL)
            "Web", "Joggle", "Cutback", "Lightening Hole", "Shear Tie", "Cleat",
            "Doubler", "Swept Wall", "Manufacturing View",
            # Structure / weld (3.7)
            "Place Section", "Structure Member", "End Cut", "Fillet Weld", "Spot Weld",
            "Welding Symbol",
            # Drafting (3.8)
            "Front View", "Auxiliary View", "Offset Section View", "Aligned Section Cut",
            "Detail View", "Broken View", "Breakout View", "View Creation Wizard",
            "Chained Dimensions", "Coordinate Dimensions", "Hole Dimension Table",
            "Balloon", "Area Fill", "2D Component", "Frame and Title Block",
            "Generative View Style",
            # FTA (3.9)
            "Annotation Plane", "Capture", "Geometrical Tolerance", "Flag Note",
            "Tolerancing Advisor", "Semantic Annotation",
            # Analysis (2.5)
            "Clamp", "Isostatic Restraint", "Surface Slider", "Bearing Load",
            "Distributed Mass", "Fastened Connection", "Bolt Tightening Connection",
            "Virtual Bolt Tightening Connection", "Rigid Virtual Part", "Frequency Case",
            "Buckling Case", "Von Mises Stress", "Precision / Error Estimate",
            "OCTREE Tetrahedron Mesher", "Advancing Front Surface Mesher", "Beam Mesher",
            "Local Mesh Size", "Export to Nastran", "Export to Abaqus",
            # DMU (2.6)
            "Interference", "Sectioning", "Distance and Band Analysis", "3D Compare",
            "Revolute Joint", "Prismatic Joint", "Cylindrical Joint", "Spherical Joint",
            "Planar Joint", "Rigid Joint", "Point Curve Joint", "Slide Curve Joint",
            "Roll Curve Joint", "Point Surface Joint", "Universal Joint", "CV Joint",
            "Gear Joint", "Rack Joint", "Cable Joint", "Screw Joint", "Swept Volume",
            "Simulation with Laws", "Shuttle", "Automatic Path Finder", "Wrapping",
            "Space Reservation",
            # Composites (2.4)
            "Composites Parameters", "Rosette", "Zone", "Transition Zone", "Stacking",
            "Laminate", "Ply", "Core", "Cut Piece", "Edge of Part", "MEOP", "EEOP",
            "Limit Contour", "Drop-off", "Ramp Support", "Skin Swap", "Material Excess",
            "ITP", "Producibility", "Flattening", "Dart", "Splice", "Ply Data Export",
            "Laser Projection Export", "Core Sampling", "Ply Table", "Ply Exchange",
            # Systems (2.7)
            "Bundle Segment", "Route a Wire", "Flatten", "Formboard Drawing",
            "Route a Pipe", "Route a Duct", "Line ID", "Specification", "Hanger",
            # Ergonomics (2.8)
            "Insert a Manikin", "Vision Window", "Reach Envelope", "RULA Analysis",
            "Lift-Lower Analysis", "Anthropometry",
            # Machining (2.9)
            "Part Operation", "Post Processor", "Facing", "Pocketing",
            "Profile Contouring", "Drilling", "Tapping", "Counterboring",
            "Countersinking", "T-Slotting", "Thread Milling", "Circular Milling",
            "Roughing", "Sweeping", "Pencil", "Spiral Milling", "Isoparametric Machining",
            "Multi-Axis Flank Contouring", "Rough Turning", "Thread Turning",
            "Tool Path Replay", "Machining Time", "Shop Floor Documentation",
            # Knowledgeware (2.10)
            "Formula", "Design Table", "Rule", "Check", "Reaction", "Power Copy",
            "User Feature", "Document Template", "Knowledge Inspector", "Expert Rule",
            "Rule Base", "Optimization", "Design of Experiments",
            # Automation (3.10)
            "Start Recording", "CATScript", "CATVBS", "CATVBA", "ShapeFactory",
            "HybridShapeFactory", "SPAWorkbench", "Measurable", "Selection",
            "SearchCriteria", "CAA V5", "RADE",
        ],
    )
    def test_every_command_family_is_present(self, index, term):
        assert index.lookup(term), f"command missing: {term}"

    @pytest.mark.parametrize(
        "term",
        [
            ".CATPart", ".CATProduct", ".CATDrawing", ".CATProcess", ".CATAnalysis",
            ".CATMaterial", ".CATfct", ".CATRuleBase", ".catalog", ".cgr", ".3dxml",
            ".CATScript", ".CATSettings", ".model", ".exp", ".session", ".dlv",
            ".pptable", "STEP (ISO 10303)", "IGES 5.3", "STL", "Parasolid", "ACIS SAT",
            "JT", "VRML", "OBJ", "3MF", "U3D", "DXF", "DWG", "CGM", "SVG", "HPGL",
            "Nastran BDF", "Abaqus INP", "ANSYS CDB", "HyperMesh", "Fibersim",
            "Ply XML", "APT", "CLfile", "ISO G-code", "IDF", "Elysium", "Theorem",
            "Datakit", "CADfix", "MultiCAx", "Send To", "Pack and Go", "Desk",
            "Search Order", "Downward Compatibility",
        ],
    )
    def test_every_file_format_is_present(self, index, term):
        assert index.lookup(term), f"format missing: {term}"

    @pytest.mark.parametrize(
        "term",
        [
            "Sketch to drawing (the basic part loop)", "Top-down design with a skeleton",
            "Bottom-up assembly", "Surface modelling to a solid",
            "Outer mould line to structural profiles", "Airframe detail part from the OML",
            "Sheet metal to a flat pattern", "Composite part, design to ply export",
            "Systems installation to formboard", "Digital mockup review",
            "Kinematics simulation", "Finite element analysis in CATIA",
            "CAM programming", "Automating a design with knowledge",
            "Drawing production and release",
            "3D master (model-based definition) release",
            "Working with a large assembly", "Change and release",
            "Repairing imported geometry", "Handing geometry to CFD",
        ],
    )
    def test_every_workflow_is_present(self, index, term):
        found = index.lookup(term)
        assert found, f"workflow missing: {term}"
        assert found[0].fields, f"{term} records no steps"

    @pytest.mark.parametrize(
        "term",
        [
            "STA / BL / WL aircraft coordinates", "Frame", "Bulkhead", "Longeron",
            "Stringer", "Skin", "Doubler", "Shear Tie", "Clip", "Cleat", "Intercostal",
            "Rib", "Spar", "Spar Cap", "Web", "Joggle", "Splice", "Butt Strap",
            "Lightening Hole", "Mouse Hole", "Fail-Safe Strap", "Wing Box", "Keel Beam",
            "Floor Beam", "Seat Track", "Fairing", "Nacelle", "Pylon", "Empennage",
            "Leading Edge", "Trailing Edge", "Slat", "Flap", "Aileron", "Spoiler",
            "Elevator", "Rudder", "Hinge Line", "Outer Mould Line", "Inner Mould Line",
            "Rivet", "Hi-Lok", "Hi-Lite", "Lockbolt", "Blind Fastener", "Edge Margin",
            "Pitch", "Hole Class", "Wet Install", "Coordination Hole", "Drill Plate",
            "Shimming", "Determinant Assembly", "Key Characteristic",
            "Tolerance Stack-Up", "Prepreg", "Tape", "Fabric", "Warp", "Weft",
            "Stacking Sequence", "Symmetric Laminate", "Balanced Laminate",
            "Ply Drop-off", "Ramp Rate", "Core", "Potting", "Co-cure", "Co-bond",
            "AFP", "ATL", "Hand Layup", "Autoclave", "Mandrel", "Springback",
            "Zoning", "Systems Segregation", "Routing Corridor", "Clearance Envelope",
            "Maintenance Access", "Weight and Balance", "Weight Roll-Up",
            "Non-Geometric Mass", "Effectivity", "Configuration", "ICD",
            "Build-to-Package", "Work Share", "AS9100", "AS9102", "CS-25", "ITAR",
            "ASME Y14.5", "ASME Y14.41", "ISO 1101", "ISO 8015", "ISO 2768",
            "ISO 5459", "ISO 2553", "AWS A2.4",
        ],
    )
    def test_every_aerospace_term_is_present(self, index, term):
        assert index.lookup(term), f"aerospace term missing: {term}"

    @pytest.mark.parametrize(
        "phrase",
        [
            "the profile is open and not limited",
            "the selected element is not connex",
            "update error",
            "broken link",
            "over constrained",
            "under constrained",
            "the fillet cannot be created with the specified radius",
            "greyed out",
            "unable to load document",
            "not sufficiently restrained",
            "created with a more recent version",
            "stress singularity",
            "assembly is slow",
            "drawing is slow",
            "self intersecting",
            "import is not a solid",
            "certified driver",
            "catia is behaving strangely",
        ],
    )
    def test_every_documented_failure_is_recognised_verbatim(self, index, phrase):
        found = index.lookup(phrase)
        assert found, f"diagnostic missing: {phrase}"
        assert any(e.kind is Kind.DIAGNOSTIC for e in found), phrase

    def test_every_diagnostic_carries_causes_and_fixes(self, index):
        for entry in index.by_kind(Kind.DIAGNOSTIC):
            assert entry.failures, f"{entry.key} lists no causes"
            assert entry.fixes, f"{entry.key} lists no fixes"

    @pytest.mark.parametrize(
        "term",
        [
            "3D Accuracy (sag)", "Cache Management", "CATIA environment file",
            "CATSettings", "Command line switches", "Licensing",
            "User interface language", "Tools > Options",
            "Display > Performance > 3D Accuracy", "Parameters and Measure",
            "Infrastructure > Part Infrastructure", "Infrastructure > Product Structure",
            "Compatibility", "Mechanical Design > Sketcher", "Knowledgeware",
            "Specification Tree", "Compass", "Toolbar", "Layers", "Search",
            "Selection Sets", "Edit Links", "Graphic Properties", "Hide/Show",
            "Power Input", "Named Views", "Scenes",
        ],
    )
    def test_every_platform_setting_is_present(self, index, term):
        assert index.lookup(term), f"setting missing: {term}"

    @pytest.mark.parametrize(
        "term",
        [
            "Feature order: draft before fillet, shell before internal fillets",
            "Fillet last, and be able to switch them off",
            "Fully constrain sketches, and anchor them to the origin",
            "Reference datums and publications, never picked faces and edges",
            "Skeleton parts, and the rule that a skeleton depends on nothing",
            "Publish interfaces instead of picking across documents",
            "Naming: features, sets, bodies, parts, publications",
            "Body, Geometrical Set, or Ordered Geometrical Set",
            "Reuse: catalogues, Power Copies, UDFs, templates",
            "Quality gates before release",
            "Common pitfalls",
            "Modelling for performance",
        ],
    )
    def test_every_practice_is_present(self, index, term):
        assert index.lookup(term), f"practice missing: {term}"

    @pytest.mark.parametrize(
        "code",
        [
            "PDG", "ASD", "GSD", "ASL", "SMD", "DR1", "KWA", "KWE", "TUB", "PIP",
            "HVA", "SDD", "PMG", "SMG", "MMG", "LMG", "DMN", "SPA", "KIN", "FIT",
            "DMO", "CPD", "CPM", "CPE", "FTA", "GPS", "GAS", "EST", "FMS", "MTD",
            "FMP", "WD1", "SR1", "ELB", "EWR", "EHF", "EHI", "ECR", "EQT", "CCV",
            "DSE", "QSR", "IMA", "FSK", "FSS", "DL1", "PX1", "HBR", "HME", "HPA",
            "HAA", "CBD", "WAV", "BKT", "PKT", "PEO", "RCD", "HGR", "SSR", "CNA",
        ],
    )
    def test_catalogue_trigrams_resolve(self, code):
        assert product(code), f"trigram missing: {code}"

    @pytest.mark.parametrize("code", ["WSF", "AMT", "ANL", "ICM", "SKT", "DRD", "ESS", "PX2", "CNV", "HAI", "VPM"])
    def test_informal_trigrams_resolve_and_say_they_are_informal(self, code):
        assert code in INFORMAL_TRIGRAMS
        assert product(code)

    def test_trigram_table_is_substantial(self):
        assert len(TRIGRAMS) > 100


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


class TestRecognition:
    def test_canonical_name(self):
        assert "part_design.edge_fillet" in _keys("how do I use Edge Fillet")

    def test_common_misname(self):
        # A user says "round" or "radius"; CATIA calls it Edge Fillet.
        assert "part_design.edge_fillet" in _keys("I want to round this edge")
        assert "part_design.edge_fillet" in _keys("add a blend edge here")

    def test_multi_word_longest_match_wins_exclusively(self):
        found = recognise("the edge fillet failed")
        assert "part_design.edge_fillet" in found.keys()
        # Not also a bare Sketcher Corner from the word "fillet" left over.
        surfaces = {m.surface.lower() for m in found.matches}
        assert "edge fillet" in surfaces

    def test_error_message_pasted_verbatim(self):
        found = _keys("it says the pad cannot be created because the profile is open and not limited")
        assert "diagnostic.open_profile" in found

    @pytest.mark.parametrize(
        "text,key",
        [
            ("comment faire un congé d'arête", "part_design.edge_fillet"),
            ("meine Tasche funktioniert nicht", "part_design.pocket"),
            ("il raccordo di uno spigolo non funziona", "part_design.edge_fillet"),
            ("cómo hago un desmoldeo", "part_design.draft_angle"),
            ("Kantenverrundung schlägt fehl", "part_design.edge_fillet"),
            ("la poche ne fonctionne pas", "part_design.pocket"),
            ("Formschräge einstellen", "part_design.draft_angle"),
            ("lo sformo della faccia", "part_design.draft_angle"),
            ("el redondeo de arista", "part_design.edge_fillet"),
        ],
    )
    def test_localised_command_names_are_recognised(self, text, key):
        assert key in _keys(text), f"{text!r} did not reach {key}"

    def test_french_typed_without_accents(self):
        assert "part_design.edge_fillet" in _keys("comment faire un conge d arete")

    def test_product_code_needs_capitals_when_it_collides_with_english(self):
        assert "piping_design" in _keys("we use PIP for the fuel lines")
        assert "piping_design" not in _keys("pip install something")

    def test_product_code_without_a_collision_works_either_way(self):
        assert "gsd" in _keys("gsd or wsf")
        assert "gsd" in _keys("GSD or WSF")

    def test_typos_still_reach_the_entry(self):
        assert _matches_any("Genrative Shape Desing is twisting my loft", "gsd")
        assert _matches_any("the filet radius is too big", "part_design.edge_fillet")

    def test_aerospace_vocabulary(self):
        found = _keys("the joggle on the rib flange at STA 1420")
        assert "aero.joggle" in found or "aerospace_sheet_metal.joggle" in found
        assert "aero.station_coordinates" in found

    def test_workflow_from_a_description_of_the_goal(self):
        assert _matches_any(
            "I need the flat pattern for this bracket",
            "workflow.sheet_metal",
            "sheet_metal_design.unfold",
        )

    def test_limit_keeps_what_was_mentioned_first(self):
        found = recognise("first a pocket, then a shaft, then a groove, then a chamfer", limit=2)
        assert len(found.matches) == 2
        assert found.matches[0].entry.key == "part_design.pocket"

    def test_recognition_is_ordered_by_position(self):
        found = recognise("first a pocket, then an edge fillet")
        positions = [m.position for m in found.matches]
        assert positions == sorted(positions)

    def test_empty_and_whitespace(self):
        assert not recognise("")
        assert not recognise("   ")


class TestPrecision:
    """Sentences that must produce nothing. This is the half that is hard."""

    @pytest.mark.parametrize(
        "text",
        [
            "pip install requests",
            "that's a good fit for the team",
            "please run the tests and check the results",
            "can you add a note to the readme",
            "the box is in the corner of the room",
            "I need to update my part of the document",
            "set the view to the top",
            "make a copy and paste it there",
            "what's the source of this text table",
        ],
    )
    def test_ordinary_english_produces_nothing(self, text):
        assert not recognise(text), f"false positive on {text!r}: {_keys(text)}"

    def test_a_verb_is_not_the_boolean_command(self):
        # "add" is the boolean Add operation *and* the commonest English verb.
        assert "part_design.add" not in _keys("how do I add an edge fillet")
        # Still reachable when it is actually meant.
        assert "part_design.add" in _keys("use boolean add to merge the bodies")

    def test_a_weak_single_word_does_not_corroborate_the_rest(self):
        # `box` alone must not establish "this is about CATIA" and thereby let
        # `corner` through.
        assert not recognise("put the box in the corner")

    def test_the_two_word_tiers_are_disjoint(self):
        # A word in both lists would have its AMBIGUOUS_WORDS entry as dead
        # code, because NEVER_BARE is checked first and is stricter.
        assert AMBIGUOUS_WORDS & NEVER_BARE == set()

    def test_distinctive_catia_words_are_in_neither_tier(self):
        # These must match on their own: "how do I make a pocket" carries no
        # other signal, and it is a question the assistant has to answer.
        for word in ("pocket", "fillet", "chamfer", "sketch", "joggle", "stringer"):
            assert word not in NEVER_BARE
            assert word not in AMBIGUOUS_WORDS
            assert recognise(f"how do I make a {word}"), word

    @pytest.mark.parametrize(
        "text",
        [
            "la poche ne marche pas",
            "le conge ne fonctionne pas apres la depouille",
            "die Tasche wird nicht erzeugt und der Block auch nicht",
            "il raccordo non riesce con una superficie",
        ],
    )
    def test_grammar_in_an_interface_language_is_not_a_command(self, text):
        # `pas` is the French for a thread pitch *and* the negation particle,
        # and `le` is Leading Edge *and* the definite article, so every French
        # sentence used to drag both into the brief. The real terms in the
        # sentence must still come through.
        keys = _keys(text)
        assert "aero.pitch" not in keys, f"false positive on {text!r}"
        assert "aero.leading_edge" not in keys, f"false positive on {text!r}"
        assert recognise(text), f"lost the real terms in {text!r}"

    def test_the_guard_is_case_sensitive_so_codes_survive(self):
        # Lower-case `est` is the French verb; upper-case `EST` is the ELFINI
        # trigram, and blocking the first must not cost the second.
        assert "trigram.est" in _keys("we have an EST licence")
        assert "trigram.est" not in _keys("le raccord est trop grand")

    @pytest.mark.parametrize(
        "text,key",
        [
            ("quel est le pas de vis", "aero.pitch"),
            ("le pas des rivets", "aero.pitch"),
            ("the leading edge skin", "aero.leading_edge"),
        ],
    )
    def test_a_blocked_word_stays_reachable_as_a_phrase(self, text, key):
        # Blocking a bare word must never make the concept unfindable: the
        # phrase a user actually writes still resolves.
        assert key in _keys(text)

    def test_assume_catia_lowers_the_bar_but_never_raises_it(self):
        plain = _keys("the web is too thin")
        assumed = _keys("the web is too thin", assume_catia=True)
        assert plain <= assumed
        assert assumed  # with context established, `web` is meaningful


class TestDisambiguation:
    @pytest.mark.parametrize(
        "term,must_mention",
        [
            ("sheet metal", ("ASL", "SMD")),
            ("surface design", ("FreeStyle", "GSD")),
            ("structural analysis", ("GAS", "GPS")),
            ("dmu", ("Kinematics", "Space Analysis")),
            ("drafting", ("Generative", "Interactive")),
            ("geometrical set", ("Ordered Geometrical Set", "Body")),
            ("step", ("AP242",)),
            ("flange", ("Aerospace", "Sheet Metal Design")),
            ("rib", ("Part Design", "wing")),
            ("language", ("interface", "COM")),
        ],
    )
    def test_forks_are_named_not_resolved(self, index, term, must_mention):
        fork = index.disambiguation(term)
        assert fork is not None, f"no disambiguation for {term!r}"
        blob = " ".join(fork.options) + " " + fork.guidance
        for token in must_mention:
            assert token in blob, f"{term!r} fork does not mention {token!r}"

    def test_a_surface_the_disambiguation_table_knows_reports_a_fork(self):
        # `sheet metal` resolves to one workbench in the index and still has to
        # be forked out loud, because SMD and ASL are different products.
        found = recognise("which sheet metal workbench do I need")
        assert "sheet metal" in found.forks

    def test_a_surface_never_expands_to_everything(self):
        # `corner` is a Sketcher command, a Sheet Metal command and a GSD
        # command. Reporting all three is right; reporting nine would not be.
        found = recognise("the corner relief on the sheet metal part")
        by_surface: dict[str, int] = {}
        for match in found.matches:
            by_surface[match.surface] = by_surface.get(match.surface, 0) + 1
        assert max(by_surface.values()) <= 3


# ---------------------------------------------------------------------------
# Language
# ---------------------------------------------------------------------------


class TestHonesty:
    def test_untranslated_command_says_so_and_does_not_substitute_english(self):
        entry = registry().entries["aerospace_sheet_metal.joggle"]
        payload = describe(entry, language="ja")
        block = payload["localised_name"]
        assert block["name"] is None
        assert "not recorded" in block["note"]
        # The English name must not appear as though it were the Japanese one.
        assert block.get("name") != entry.name

    def test_translated_command_gives_the_localised_name(self):
        entry = registry().entries["part_design.pocket"]
        payload = describe(entry, language="de")
        assert payload["localised_name"]["name"] == "Tasche"

    def test_empty_fields_are_omitted_rather_than_reported_as_absent(self):
        entry = registry().entries["sheet_metal_design.hem"]
        payload = entry.to_dict()
        assert "aerospace" not in payload
        assert "failures" not in payload

    def test_informal_codes_are_labelled_informal(self, index):
        entry = index.entries["trigram.wsf"]
        assert "informal" in entry.summary.lower()
        assert "WS1" in entry.licence

    def test_localised_returns_none_rather_than_a_guess(self):
        assert localised("part_design.pocket", "de") == "Tasche"
        assert localised("part_design.pocket", "ja") is None
        assert localised("part_design.pocket", "en") is None
        assert localised("nonexistent.key", "de") is None


class TestLanguages:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("fr", "fr"), ("FR", "fr"), ("fr-FR", "fr"), ("French", "fr"),
            ("français", None), ("francais", "fr"), ("Deutsch", "de"),
            ("German", "de"), ("de_DE", "de"), ("italiano", "it"), ("Spanish", "es"),
            ("Japanese", "ja"), ("zh-CN", "zh"), ("klingon", None), ("", None),
            (None, None),
        ],
    )
    def test_language_normalisation(self, raw, expected):
        # `français` with the cedilla is not in the alias table and must return
        # None rather than a guess -- a wrong language is a wrong menu name.
        assert normalise_language(raw) == expected

    def test_every_catia_interface_language_is_listed(self):
        codes = {lang.code for lang in LANGUAGES}
        assert {"en", "fr", "de", "it", "es", "ja", "zh", "ko", "ru", "pt"} <= codes

    def test_the_translated_set_matches_what_is_actually_recorded(self):
        recorded = {code for table in NAMES.values() for code in table}
        assert TRANSLATED <= recorded | {"en"}

    def test_core_commands_are_translated_into_every_supported_language(self):
        # The commands a user is most likely to name must work on any seat.
        for key in ("part_design.pad", "part_design.pocket", "part_design.edge_fillet"):
            table = translations(key)
            assert {"fr", "de", "it", "es"} <= set(table), f"{key} is missing a language"

    def test_the_automation_localisation_rule_is_recorded(self, index):
        entry = index.entries["api.localisation"]
        assert "never translate" in entry.summary.lower() or "not" in entry.summary.lower()
        assert any("Pad.1" in failure for failure in entry.failures)

    def test_changing_the_interface_language_is_answerable(self, index):
        entry = index.entries["setting.ui_language"]
        assert "Tools > Customize" in entry.menu
        assert any("restart" in text.lower() for text in (*entry.fields, *entry.failures))


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


class TestExpansion:
    def test_english_query_reaches_the_french_manual(self):
        expanded = expand_query("draft angle on a moulded part")
        assert "Dépouille" in expanded

    def test_french_query_reaches_the_english_manual(self):
        expanded = expand_query("angle de dépouille")
        assert "Draft Angle" in expanded

    def test_german_query_is_expanded_too(self):
        expanded = expand_query("Kantenverrundung Radius")
        assert "Edge Fillet" in expanded

    def test_expansion_is_purely_additive(self):
        original = "edge fillet radius too large"
        assert expand_query(original).startswith(original)

    def test_expansion_is_capped(self):
        long_query = "pad pocket shaft groove hole rib slot stiffener fillet chamfer draft shell"
        added = expand_query(long_query, max_added=4).removeprefix(long_query).split()
        # Four *terms*, some of which are multi-word names, so bound generously
        # but not unboundedly.
        assert len(added) <= 16

    def test_a_query_naming_nothing_is_returned_unchanged(self):
        assert expand_query("what is the weather today") == "what is the weather today"
        assert expand_query("") == ""

    def test_the_preferred_language_is_added_first(self):
        expanded = expand_query("draft angle", language="de")
        assert "Formschräge" in expanded


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


class TestBrief:
    def test_names_the_workbench_and_the_menu_path(self):
        text = brief("how do I use edge fillet")
        assert "Part Design" in text
        assert "Insert > Dress-Up Features > Edge Fillet" in text

    def test_carries_the_localised_name_when_a_language_is_known(self):
        text = brief("Kantenverrundung", language="de")
        assert "Kantenverrundung" in text
        assert "Edge Fillet" in text

    def test_is_empty_when_nothing_was_recognised(self):
        assert brief("what is the weather today") == ""
        assert brief("") == ""

    def test_is_bounded(self):
        text = brief(
            "pad pocket shaft groove hole rib slot stiffener edge fillet chamfer "
            "draft angle shell thickness joggle web flange cutback doubler stringer"
        )
        assert len(text) < 2_000

    def test_mentions_a_fork_when_the_term_is_ambiguous(self):
        text = brief("which sheet metal workbench")
        assert "ambiguous" in text.lower()


# ---------------------------------------------------------------------------
# The service contract: nothing raises
# ---------------------------------------------------------------------------


class TestService:
    def test_lookup_returns_structured_records(self):
        results = catia_knowledge().lookup("joggle")
        assert results
        assert any(r.get("workbench") for r in results)

    def test_lookup_on_nonsense_is_empty_not_an_error(self):
        assert catia_knowledge().lookup("zzzzqqqq") == []
        assert catia_knowledge().lookup("") == []

    def test_expand_falls_back_to_the_original_query(self, monkeypatch):
        service = catia_knowledge()
        monkeypatch.setattr(
            "app.catia_kb.service.expand_query",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert service.expand("edge fillet") == "edge fillet"

    def test_brief_swallows_failures(self, monkeypatch):
        service = catia_knowledge()
        monkeypatch.setattr(
            "app.catia_kb.service.render_brief",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert service.brief("edge fillet") == ""

    def test_disabled_service_is_simply_unavailable(self, monkeypatch):
        from app.catia_kb.service import CatiaKnowledge

        service = CatiaKnowledge(enabled=False)
        assert service.available is False
        assert service.lookup("edge fillet") == []
        assert service.brief("edge fillet") == ""
        assert service.expand("edge fillet") == "edge fillet"
        assert service.stats()["available"] is False

    def test_stats_report_coverage(self):
        stats = catia_knowledge().stats()
        assert stats["available"] is True
        assert stats["command"] > 500
        assert stats["workbench"] > 80

    def test_singleton_can_be_reset(self):
        first = catia_knowledge()
        reset_catia_knowledge()
        assert catia_knowledge() is not first


# ---------------------------------------------------------------------------
# Integration with the agent layer
# ---------------------------------------------------------------------------


class TestAgentIntegration:
    def test_the_prompt_carries_the_domain_contract(self):
        from app.ai import prompts

        for prompt in (
            prompts.AGENT_SYSTEM,
            prompts.AGENT_SYSTEM_DOCS,
            prompts.AGENT_SYSTEM_CATIA,
            prompts.AGENT_SYSTEM_CATIA_DOCS,
        ):
            assert "explain_catia_term" in prompt
            assert "V5" in prompt
            # The distinctions that must never be blurred.
            assert "Aerospace Sheet Metal" in prompt
            assert "Never guess a translation" in prompt

    def test_the_prompts_stay_frozen_constants(self):
        from app.ai import prompts

        # Prompt caching is a prefix match: these must be identical on every
        # call, so nothing volatile may have crept into the f-strings.
        assert prompts.AGENT_SYSTEM == prompts.AGENT_SYSTEM
        assert prompts.AGENT_SYSTEM_CATIA_DOCS.startswith(prompts.AGENT_SYSTEM_CATIA)

    def test_the_tool_is_labelled_for_the_step_list(self):
        from app.ai.tools import tool_label

        assert tool_label("explain_catia_term") == "Checking the CATIA reference"

    def test_the_tool_is_not_a_bridge_tool(self):
        # A `catia_`-prefixed name would be routed to the bridge dispatcher and
        # gated on a workstation being connected. This one must not be.
        assert not "explain_catia_term".startswith("catia_")

    def test_retrieval_widens_a_query_before_searching(self, monkeypatch):
        from app.retrieval.service import KnowledgeService

        widened = KnowledgeService._widen("draft angle", language="fr")
        assert "Dépouille" in widened

    def test_retrieval_widening_can_be_switched_off(self, monkeypatch):
        from app.core.config import settings
        from app.retrieval.service import KnowledgeService

        monkeypatch.setattr(settings, "catia_knowledge_expand_queries", False)
        assert KnowledgeService._widen("draft angle", language="fr") == "draft angle"
