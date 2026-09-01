"""Part Design: sketch-based features, dress-up, transformations, booleans.

The dress-up commands carry the most detail here, and deliberately. A pad
either works or it does not, and the reason is almost always in the sketch. A
fillet fails in ways that are specific to fillets -- a radius larger than the
adjacent face, a ribbon that cannot be trimmed, a corner where three ribbons
meet -- and telling a user "try a smaller radius" when the real problem is the
corner is the kind of confidently wrong answer this package exists to stop.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_WB = "part_design"

_LIMITS = (
    "First/Second Limit type -- Dimension, Up to next, Up to last, Up to plane, Up to surface",
    "Length, or the limiting element when the type is not Dimension",
    "Offset -- a signed distance from the limiting element, valid with Up to plane/surface",
    "Direction -- Normal to profile (default), or Reference to extrude along another element",
    "Mirrored extent -- the same length both sides of the sketch plane",
    "Thick -- turns the solid into a thin-walled one with Thickness1 and Thickness2",
    "Reverse Side -- which side of an open profile is filled",
    "Reverse Direction -- which way the material goes",
)


_DETAILED: list[Entry] = [
    command(
        "Pad",
        workbench=_WB,
        toolbar="Sketch-Based Features",
        aliases=("pad", "extrude", "extrusion", "protrusion", "boss", "extrude sketch", "block", "prisma", "extrusionar"),
        summary="Extrudes a sketch profile normal to its plane to make solid material.",
        menu="Insert > Sketch-Based Features > Pad",
        icon="a rectangle being pushed up into a solid block, with an upward arrow",
        fields=_LIMITS,
        needs=(
            "A sketch, a planar face, or a closed surface/wireframe profile",
            "A body must be in work (Define In Work Object) -- the pad lands there",
        ),
        failures=(
            "\"The pad cannot be created because the profile is open and not limited\" -- an open profile needs surrounding material to close against, or Thick",
            "The profile self-intersects, or two sub-profiles overlap",
            "The profile has more than one closed contour and they are not nested -- use Multi-Pad instead",
            "\"Up to next\" finds nothing because there is no material in that direction yet",
            "The result is disjoint from the existing body, which is legal but usually not intended",
        ),
        fixes=(
            "Run Tools > Sketch Analysis and use its corrective actions before touching the pad",
            "For an open profile, either close it, tick Thick, or choose limits that terminate on real faces",
            "Reorder the pad after the feature that provides the material \"Up to next\" needs",
        ),
        alternatives=(
            "Multi-Pad -- several closed contours in one sketch, each with its own length",
            "Drafted Filleted Pad -- pad, draft and fillets in one feature, for moulded parts",
            "Rib -- when the profile must follow a path rather than go straight",
            "Thick Surface / Close Surface -- when the shape is surface-driven",
        ),
        licence="P1 -- Part Design 1 (PD1)",
        see_also=("sketcher.sketch_analysis", "part_design.pocket", "diagnostic.open_profile"),
    ),
    command(
        "Pocket",
        workbench=_WB,
        toolbar="Sketch-Based Features",
        aliases=("pocket", "cut", "extruded cut", "poche", "tasche", "tasca", "bolsillo", "remove material", "cutout"),
        summary="Extrudes a profile and removes the material it sweeps through.",
        menu="Insert > Sketch-Based Features > Pocket",
        icon="a block with a rectangular recess cut into its top face",
        fields=_LIMITS,
        needs=("A profile, and existing material for it to cut",),
        failures=(
            "\"The result is not a solid\" or the feature removes everything -- the pocket consumed the whole body",
            "The pocket is fully outside the material, so it changes nothing and the tree shows an update warning",
            "An open profile with no Thick and no limiting faces cannot decide what to remove",
        ),
        fixes=(
            "Check the direction arrow before accepting the dialog; Reverse Direction is the usual fix",
            "Use \"Up to last\" rather than a dimension when the wall thickness may change later",
        ),
        alternatives=(
            "Multi-Pocket, Hole (for round holes with a real thread and standard), Groove (for a revolved cut), Split (surface-driven removal)",
        ),
        licence="P1",
        see_also=("part_design.hole", "part_design.pad"),
    ),
    command(
        "Shaft",
        workbench=_WB,
        toolbar="Sketch-Based Features",
        aliases=("shaft", "revolve", "revolution", "revolved boss", "turn", "welle", "alberello", "revolucion", "lathe feature"),
        summary="Revolves a profile about an axis to make solid material.",
        menu="Insert > Sketch-Based Features > Shaft",
        icon="a profile sweeping around a vertical dashed axis",
        fields=(
            "First angle / Second angle -- degrees, measured from the profile",
            "Axis -- Selection, or the sketch's own axis if it has one",
            "Profile / Selection -- the sketch",
            "Thick Profile -- Thickness1 and Thickness2 for a thin revolved wall",
            "Reverse Direction",
        ),
        needs=(
            "A closed profile, and an axis that does not cross it",
            "The axis is normally a construction Axis inside the sketch",
        ),
        failures=(
            "\"The profile crosses the axis\" -- a revolved solid cannot pass through its own centre line",
            "The axis was drawn as a standard line rather than a construction Axis, so the profile is not closed",
        ),
        fixes=(
            "Put the axis on the sketch's Axis element, or select an external line/axis in the dialog",
            "Move the profile fully to one side of the axis",
        ),
        alternatives=("Groove for the removing equivalent; Rib for a swept rather than revolved shape",),
        licence="P1",
        see_also=("part_design.groove",),
    ),
    command(
        "Hole",
        workbench=_WB,
        toolbar="Sketch-Based Features",
        aliases=("hole", "drill", "bore", "trou", "bohrung", "foro", "agujero", "tapped hole", "counterbore", "countersink", "threaded hole"),
        summary="A real hole feature with a type, a depth strategy, a bottom shape and an optional standard thread -- not a revolved pocket.",
        menu="Insert > Sketch-Based Features > Hole",
        icon="a block with a cylindrical bore and a centreline through it",
        fields=(
            "Extension tab -- Blind / Up to next / Up to last / Up to plane / Up to surface; Diameter; Depth; Offset",
            "Bottom -- Flat / V-Bottom (with angle); \"Trimmed\" for up-to types",
            "Type tab -- Simple, Tapered (with angle), Counterbored, Countersunk, Counterdrilled",
            "Thread Definition tab -- Threaded checkbox, Type (Metric Thin/Thick Pitch, No Standard, or an added standard), Thread Diameter, Hole Diameter, Thread Depth, Pitch, Right/Left-Threaded",
            "Positioning Sketch -- the hole's own sketch, editable to constrain the centre properly",
            "Direction -- Normal to surface, or a chosen reference",
        ),
        needs=(
            "A face to place it on. Pre-selecting a circular edge centres it there; pre-selecting a face plus a point uses the point",
        ),
        failures=(
            "The hole is placed but unconstrained, so it drifts when the face is patterned or resized",
            "A threaded hole shows no thread in the 3D view -- threads are not modelled geometry, they are a property; the drawing and the analysis read them, the render does not",
            "\"Up to next\" stops at an unexpected face when there is internal geometry in the way",
        ),
        fixes=(
            "Edit the positioning sketch and constrain the centre point to real references",
            "Add company standards through Tools > Standards to get non-metric thread tables in the Type list",
        ),
        alternatives=("Pocket with a circular profile, when no thread or standard is needed; Thread/Tap to add a thread to an existing cylinder",),
        aerospace="Hole class, edge margin and pitch are the constraints that matter on an airframe; model the hole so a fastener catalogue and a pattern can drive it, rather than dimensioning each one.",
        licence="P1",
        see_also=("part_design.thread_tap", "aero.edge_margin"),
    ),
    command(
        "Edge Fillet",
        workbench=_WB,
        toolbar="Dress-Up Features",
        aliases=(
            "edge fillet", "fillet", "round", "radius", "blend edge", "conge", "conge d arete",
            "kantenverrundung", "verrundung", "raccordo", "redondeo", "break the edge",
        ),
        summary="Rolls a constant-radius ribbon along selected edges or around a face.",
        menu="Insert > Dress-Up Features > Edge Fillet",
        icon="a cube with one edge rounded off",
        fields=(
            "Radius",
            "Object(s) to fillet -- edges, faces (fillets every edge of the face), or a feature",
            "Propagation -- Tangency (follows the tangent chain) / Minimal / Intersection",
            "Trim ribbons -- lets two ribbons that overlap cut each other instead of failing",
            "Conic parameter -- a value between 0 and 1 replaces the circular section with a conic",
            "Edges to keep -- edges the ribbon must not swallow",
            "Limiting element -- a plane or surface that stops the ribbon partway",
            "Blend corner(s) -- reshapes the corner where three or more ribbons meet",
        ),
        needs=("Edges or faces on an existing solid",),
        failures=(
            "\"The fillet cannot be created with the specified radius\" -- the radius exceeds the adjacent face, or the ribbon would self-intersect further along the tangent chain",
            "Three ribbons meeting at a vertex fail even though each works alone",
            "Tangency propagation picks up far more edges than intended and one of them is the one that fails",
            "The fillet works, then breaks later when an upstream feature changes the edge it referenced -- topological naming",
        ),
        fixes=(
            "Reduce the radius, but check *where* it fails first -- the narrowest adjacent face sets the ceiling, not the edge you clicked",
            "Tick Trim ribbons for overlapping ribbons; use Blend corner at a failing vertex",
            "Switch propagation from Tangency to Minimal to stop it running away along the chain",
            "Fillet later rather than earlier: a fillet high in the tree is the most fragile thing in a part",
        ),
        alternatives=(
            "Variable Radius Fillet -- radius varies along the edge",
            "Face-Face Fillet -- two faces that do not share an edge",
            "Tritangent Fillet -- removes a face entirely, rolling between the two either side",
            "Chordal Fillet -- a constant chord width rather than a constant radius",
            "Shape Fillet in GSD -- when the input is surfaces, not a solid",
        ),
        licence="P1",
        see_also=("part_design.variable_radius_fillet", "practice.fillet_late", "diagnostic.fillet_fails"),
    ),
    command(
        "Chamfer",
        workbench=_WB,
        toolbar="Dress-Up Features",
        aliases=("chamfer", "bevel", "chanfrein", "fase", "smusso", "chaflan", "break edge", "45 degree edge"),
        summary="Cuts a flat bevel along selected edges.",
        menu="Insert > Dress-Up Features > Chamfer",
        icon="a cube with one edge cut flat at an angle",
        fields=(
            "Mode -- Length1/Angle, or Length1/Length2",
            "Length1, Angle or Length2",
            "Propagation -- Tangency / Minimal",
            "Reverse -- which face the angle is measured from",
            "Edge(s) to chamfer",
        ),
        needs=("Edges on a solid",),
        failures=("The same over-run failures as a fillet: too large for the adjacent face, or an unresolvable vertex",),
        fixes=("Reduce the length, or chamfer the edges in separate features so the failing one is isolated",),
        alternatives=("Sketch-level Chamfer, when the bevel should be part of the profile rather than a dress-up feature",),
        licence="P1",
    ),
    command(
        "Draft Angle",
        workbench=_WB,
        toolbar="Dress-Up Features",
        aliases=(
            "draft", "draft angle", "taper", "depouille", "angle de depouille", "formschrage",
            "formschraege", "sformo", "desmoldeo", "mould draft", "release angle",
        ),
        summary="Tilts faces away from a pulling direction so the part can leave a mould or die.",
        menu="Insert > Dress-Up Features > Draft Angle",
        icon="a block whose side face leans, with an upward pull arrow",
        fields=(
            "Angle",
            "Face(s) to draft",
            "Neutral element -- the face or plane that keeps its size; Selection, and Propagation None/Smooth",
            "Pulling Direction -- defaults to the neutral element's normal; can be any direction",
            "Parting Element -- splits the draft either side of a surface or plane",
            "Draft Both Sides",
            "Definition mode -- Standard or Draft Both Sides / Variable angle",
        ),
        needs=("Faces on a solid, and a neutral element",),
        failures=(
            "The drafted face no longer meets its neighbours and the solid fails",
            "A face adjacent to a fillet cannot draft, because the fillet was applied first",
            "The pulling direction is inherited from the neutral element and is not the one intended",
        ),
        fixes=(
            "Draft before filleting -- this is the classic ordering rule in Part Design",
            "Set the pulling direction explicitly rather than accepting the default",
            "Use a Parting Element when the part drafts both ways from a split line",
        ),
        alternatives=("Draft with parting element; Variable Angle Draft; Draft Analysis to check the result",),
        licence="P1",
        see_also=("part_design.draft_analysis", "practice.feature_order"),
    ),
    command(
        "Shell",
        workbench=_WB,
        toolbar="Dress-Up Features",
        aliases=("shell", "hollow", "coque", "schalenelement", "guscio", "vaciado", "thin wall", "scoop out"),
        summary="Hollows a solid to a wall thickness, optionally opening chosen faces.",
        menu="Insert > Dress-Up Features > Shell",
        icon="a box with its top face removed, showing the wall thickness",
        fields=(
            "Default inside thickness",
            "Default outside thickness",
            "Faces to remove -- the openings",
            "Other thickness faces -- faces that get a different wall from the default",
        ),
        needs=("A solid body",),
        failures=(
            "\"The thickness is too large\" -- the wall exceeds the smallest radius of curvature somewhere on the part, usually inside a fillet that was applied first",
            "Shelling after filleting fails where shelling before filleting would have worked",
        ),
        fixes=(
            "Shell before adding internal fillets; add them to the shelled result",
            "Reduce the thickness, or use Other thickness faces to thin only the region that fails",
        ),
        alternatives=("Thickness, to change a wall locally; Thick Surface, when the shape is surface-driven",),
        licence="P1",
        see_also=("practice.feature_order",),
    ),
    command(
        "Thread/Tap",
        workbench=_WB,
        toolbar="Dress-Up Features",
        aliases=("thread", "tap", "tapping", "filetage", "taraudage", "gewinde", "filettatura", "rosca", "add thread"),
        summary="Declares a thread on an existing cylindrical face. Carried as a property, not as modelled helical geometry.",
        menu="Insert > Dress-Up Features > Thread/Tap",
        fields=(
            "Lateral Face -- the cylinder",
            "Limit Face -- where the thread starts",
            "Type -- Metric Thick Pitch / Metric Thin Pitch / No Standard / an added standard",
            "Thread Diameter, Support Diameter, Thread Depth, Support Depth, Pitch",
            "Right-Threaded / Left-Threaded",
        ),
        failures=(
            "Users expect to see a helix in the 3D and report the command as broken; it is working -- Drafting renders the thread, the 3D does not",
            "A non-metric thread is unavailable because no standard has been added to the environment",
        ),
        fixes=("Add the standard via Tools > Standards, or via CATCollectionStandard for the whole site",),
        licence="P1",
        see_also=("part_design.hole", "setting.catia_environment"),
    ),
    command(
        "Rectangular Pattern",
        workbench=_WB,
        toolbar="Transformation Features",
        aliases=(
            "rectangular pattern", "linear pattern", "array", "repetition rectangulaire",
            "rechteckmuster", "ripetizione rettangolare", "patron rectangular", "repeat feature",
        ),
        summary="Repeats a feature or body in one or two directions on a grid.",
        menu="Insert > Transformation Features > Rectangular Pattern",
        fields=(
            "First/Second Direction -- Parameters: Instances & Length, Instances & Spacing, Spacing & Length, or Instances & Unequal Spacing",
            "Reference element -- the edge, line or axis giving the direction; Reverse",
            "Object to Pattern -- the feature, or the Current Solid",
            "Keep specifications -- instances keep the original's up-to limits rather than copying its resolved geometry",
            "Position of object in pattern -- which grid cell holds the original; Row/Column in direction 1 and 2, Rotation angle",
            "Simplified representation -- hides instances to keep display fast",
        ),
        needs=("A feature to pattern and a direction reference",),
        failures=(
            "An instance lands off the material and the whole pattern fails",
            "\"Keep specifications\" ticked with an Up to next limit makes instances resolve to different depths -- sometimes wanted, usually a surprise",
            "The pattern breaks when the referenced edge is regenerated by an upstream change",
        ),
        fixes=(
            "Untick instances that fall outside, in the grid preview",
            "Reference a datum line or axis rather than a model edge",
        ),
        alternatives=("Circular Pattern; User Pattern (positions from a sketch); Mirror; Assembly-level Reuse Pattern",),
        aerospace="A fastener row is a User Pattern driven by a sketch of hole centres, so pitch and edge margin are constrained explicitly rather than implied by a grid.",
        licence="P1",
        see_also=("part_design.user_pattern", "practice.robust_references"),
    ),
    command(
        "Mirror",
        workbench=_WB,
        toolbar="Transformation Features",
        aliases=("mirror", "symmetry", "symetrie", "spiegeln", "simmetria", "simetria", "mirror feature", "mirror body"),
        summary="Mirrors a feature, a body or the whole solid about a plane or planar face.",
        menu="Insert > Transformation Features > Mirror",
        fields=("Mirroring element -- plane or planar face", "Object to mirror -- a feature, or the Current Solid"),
        failures=("Mirroring the Current Solid mirrors everything built so far, including features added later only if the mirror stays last in the tree",),
        fixes=("Keep the mirror at the end of the tree, or mirror specific features rather than the current solid",),
        alternatives=("Symmetry, which moves rather than duplicates; Assembly Symmetry for a whole component",),
        licence="P1",
    ),
    command(
        "Draft Analysis",
        workbench=_WB,
        toolbar="Analysis",
        aliases=("draft analysis", "analyse de depouille", "mould draft check", "colour draft", "undercut check"),
        summary="Colours faces by their angle to a pulling direction, so undercuts and insufficient draft are visible.",
        menu="Insert > Analysis > Draft Analysis (or View > Render Style)",
        fields=(
            "Mode -- Quick analysis (two colours) or Full analysis (a graduated scale)",
            "Draft angle limits -- the thresholds the colours change at",
            "Compass direction -- the pulling direction, taken from the 3D compass",
            "On the fly -- updates as the part is rotated",
        ),
        needs=("A solid, and the compass oriented to the pull direction",),
        failures=("The result is meaningless if the compass is not aligned to the real pulling direction; that is the usual mistake",),
        licence="P1/P2",
        see_also=("part_design.draft_angle",),
    ),
    command(
        "Define In Work Object",
        workbench=_WB,
        toolbar="Tools / context menu",
        aliases=(
            "define in work object", "diwo", "in work object", "set current body",
            "objet de travail", "insert here", "change the insertion point", "make active",
        ),
        summary="Sets which body, geometrical set or point in the history new features are added to.",
        menu="Right-click a node in the tree > Define In Work Object",
        needs=("A node selected in the specification tree",),
        failures=(
            "Features appear in the wrong body, or in the middle of the history, because the work object was left somewhere unexpected -- the single most common source of \"my pad went to the wrong place\"",
            "In an Ordered Geometrical Set the work object also decides the *insertion point*, so new geometry can land before existing features and change what they see",
        ),
        fixes=("Watch the underlined node in the tree -- that is the current work object",),
        licence="P1",
        see_also=("practice.container_choice", "part_design.insert_new_body"),
    ),
    command(
        "Copy/Paste Special",
        workbench=_WB,
        toolbar="Edit menu",
        aliases=(
            "paste special", "copy paste special", "as result", "as result with link",
            "as specified", "collage special", "break link", "paste as result",
        ),
        summary="Controls what a paste actually creates: a dead copy, a linked copy, or a full re-specified feature tree.",
        menu="Edit > Paste Special",
        fields=(
            "As specified in Part document -- pastes the whole feature history; fully editable, fully re-associative to its own new parents",
            "As Result With Link -- one dead-looking body that still updates when the source changes",
            "As Result -- a dead copy with no link; the source can be deleted",
            "Break link -- available afterwards on a linked result, to freeze it",
        ),
        failures=(
            "\"As Result With Link\" is chosen for convenience and then the source document is moved, producing a broken link the user cannot explain",
            "\"As specified\" pastes references to geometry that does not exist in the target, and every pasted feature errors at once",
        ),
        fixes=(
            "Use Edit > Links to see and repair what a linked paste depends on",
            "Prefer Publications and contextual design over ad-hoc linked pastes",
        ),
        licence="P1",
        see_also=("assembly_design.publication", "diagnostic.broken_link"),
    ),
]


_SKETCH_BASED = bulk(
    """
Drafted Filleted Pad | drafted filleted pad, pad with draft and fillet
Multi-Pad | multi-pad, multi pad, multiple pad, several profiles one pad
Drafted Filleted Pocket | drafted filleted pocket, pocket with draft and fillet
Multi-Pocket | multi-pocket, multi pocket, multiple pocket
Groove | groove, revolved cut, gorge, nut, scanalatura, ranura, revolved groove
Rib | rib, sweep solid, swept boss, nervure, rippe, nervatura, nervio | Sweeps a profile along a centre curve to add material
Slot | slot, swept cut, rainure, swept slot | Sweeps a profile along a centre curve to remove material
Solid Combine | solid combine, combine, two profile intersection solid
Stiffener | stiffener, raidisseur, versteifung, gusset, web stiffener, refuerzo
Multi-sections Solid | multi-sections solid, loft, lofted boss, solide multi-sections, blend solid, multi section
Removed Multi-sections Solid | removed multi-sections solid, removed loft, lofted cut
""",
    workbench=_WB,
    toolbar="Sketch-Based Features",
)

_DRESS_UP = bulk(
    """
Variable Radius Fillet | variable radius fillet, variable fillet, varying radius, conge variable
Chordal Fillet | chordal fillet, constant chord fillet, chord width fillet
Face-Face Fillet | face-face fillet, face to face fillet, fillet between two faces
Tritangent Fillet | tritangent fillet, tri-tangent fillet, remove face fillet
Variable Angle Draft | variable angle draft, variable draft
Draft with Parting Element | draft with parting element, parting draft, split draft
Thickness | thickness, add thickness, local thickness, surepaisseur, aufmass, spessore, espesor
Remove Face | remove face, delete face, defeature, simplify solid
Replace Face | replace face, substitute face, cut solid with surface
Sew Surface | sew surface, sew, couture, add surface to solid
Close Surface | close surface, fill surface into solid, remplissage de surface, surface to solid
Split | split, cut by surface, decoupage, split solid with surface
Thick Surface | thick surface, offset surface into solid, surface epaisse, give thickness to a surface
""",
    workbench=_WB,
    toolbar="Dress-Up Features / Surface-Based Features",
)

_TRANSFORM = bulk(
    """
Translation | translation, move body, verschieben, translate solid
Rotation | rotation, rotate body, drehen, rotate solid
Symmetry | symmetry, move by symmetry, symetrie de deplacement
Axis to Axis | axis to axis, move from axis system to axis system
Circular Pattern | circular pattern, polar pattern, repetition circulaire, kreismuster, ripetizione circolare, patron circular, radial array
User Pattern | user pattern, sketch driven pattern, repetition utilisateur, pattern from points
Scaling | scaling, scale, facteur d echelle, massstab, resize solid
Affinity | affinity, non-uniform scale, scale differently per axis
Explode Pattern | explode pattern, break pattern, dissociate instances
""",
    workbench=_WB,
    toolbar="Transformation Features",
)

_BOOLEAN = bulk(
    """
Assemble | assemble, boolean assemble, assembler
Add | add, boolean add, union, ajouter
Remove | remove, boolean remove, subtract, retirer, difference
Intersect | intersect, boolean intersect, common volume, intersection
Union Trim | union trim, trim and union, union-trim
Remove Lump | remove lump, delete lump, remove disconnected solid
Insert New Body | insert body, new body, corps, nouveau corps
Insert Geometrical Set | insert geometrical set, new geometrical set, set geometrique
Insert Ordered Geometrical Set | insert ordered geometrical set, ordered geometrical set, ogs
Change Body | change body, move feature to another body
""",
    workbench=_WB,
    toolbar="Boolean Operations / Insert menu",
)

_REFERENCE = bulk(
    """
Point | point, reference point, punkt, punto | Coordinates, on curve, on plane, on surface, circle centre, tangent point, between
Line | line, reference line, droite, gerade | Point-point, point-direction, angle to curve, tangent to curve, normal to surface, bisecting
Plane | plane, reference plane, plan, ebene, piano, plano | Offset from plane, parallel through point, angle to plane, through three points, through two lines, through point and line, normal to curve, mean through points, equation
Axis System | axis system, coordinate system, csys, repere, local axis system, datum axis
Create Datum | create datum, datum mode, no history, isolated geometry, datum toggle
Extract | extract, extraire, extract face, extract edge, copy geometry
Multiple Extract | multiple extract, extract several faces
Publication | publication, publish, publier, published element, external reference interface
""",
    workbench=_WB,
    toolbar="Reference Elements / Tools",
)

_ANALYSIS_TOOLS = bulk(
    """
Update | update, mise a jour, aktualisieren, aggiorna, actualizar, refresh model, rebuild
Deactivate | deactivate, deactivate feature, desactiver, suppress feature, turn off feature
Activate | activate, reactivate, activer
Reorder | reorder, reorder feature, move feature in tree, reordonner
Parent/Children | parent children, parents and children, dependencies, what uses this
Scan or Define In Work Object | scan, replay history, scan the tree, playback
Thickness Analysis | thickness analysis, wall thickness check, analyse d epaisseur
Curvature Analysis | curvature analysis, courbure, curvature check
Measure Between | measure between, mesure entre, distance between, messen
Measure Item | measure item, mesure d un element, measure a face
Measure Inertia | measure inertia, mass properties, inertia, centre of gravity, cog, masse, mass properties of a part
Apply Material | apply material, appliquer un materiau, assign material, materiau
Isolate | isolate, isolate external references, break external link, isoler
Part Comparison | part comparison, compare parts, 3d compare
Save Management | save management, gestion des sauvegardes, save all, propagate directory
""",
    workbench=_WB,
    toolbar="Tools / Analysis / Measure",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_SKETCH_BASED,
    *_DRESS_UP,
    *_TRANSFORM,
    *_BOOLEAN,
    *_REFERENCE,
    *_ANALYSIS_TOOLS,
]

SECTION = Section("part_design", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
