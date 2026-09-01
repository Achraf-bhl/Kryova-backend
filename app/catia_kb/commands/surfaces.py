"""Generative Shape Design, Wireframe & Surface, FreeStyle, Imagine & Shape.

GSD and WSF are one command set with two licences: the names, dialogs and
behaviour are identical, and WSF simply does not offer the advanced half. They
share a workbench key here (`gsd`) with the licence field saying which product
carries each command, because a user who says "Wireframe and Surface" and a user
who says "GSD" are asking about the same Extrude dialog.

FreeStyle is a genuinely different paradigm -- explicit control points, no
history -- and is kept separate for that reason, not for licensing.

The continuity vocabulary (G0/G1/G2/G3) runs through every operation here and is
recorded once, on Connect Checker, because that is the command that measures it.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_WB = "gsd"
_FS = "freestyle"
_IMA = "imagine_and_shape"


_DETAILED: list[Entry] = [
    command(
        "Extrude",
        workbench=_WB,
        toolbar="Surfaces",
        aliases=("extrude", "extruded surface", "extrusion", "extrudieren", "estrusione", "extrude a curve"),
        summary="Sweeps a curve along a direction to make a surface.",
        menu="Insert > Surfaces > Extrude",
        fields=(
            "Profile -- the curve or sketch",
            "Direction -- a line, plane normal, or compass direction",
            "Limit 1 / Limit 2 -- Dimension or Up-to element",
            "Mirrored extent",
        ),
        needs=("A curve and a direction",),
        failures=("The direction defaults to the sketch normal, which is rarely what is wanted when the profile is a 3D curve",),
        licence="P1 -- WSF (WS1) and GSD (GS1/GSD)",
    ),
    command(
        "Sweep",
        workbench=_WB,
        toolbar="Surfaces",
        aliases=("sweep", "swept surface", "balayage", "sweep surface", "sweep along guide"),
        summary="Moves a profile along one or more guide curves, with the profile type deciding the whole dialog.",
        menu="Insert > Surfaces > Sweep",
        icon="a small profile shape following a curved path",
        fields=(
            "Profile type -- Explicit, Line, Circle, or Conic",
            "Explicit subtypes -- With reference surface / With two guide curves / With pulling direction",
            "Line subtypes -- Two limits / Limit and middle / With reference surface / With reference curve / With tangency surface / With draft direction / With two tangency surfaces",
            "Circle subtypes -- Three guides / Two guides and radius / Centre and two angles / Centre and radius / Two guides and tangency surface / One guide and tangency surface",
            "Spine -- the curve the profile stays normal to; defaults to the first guide",
            "Relimiter 1 / 2 -- where the sweep starts and stops",
            "Smooth sweeping -- angular and deviation thresholds that let a slightly kinked spine still produce a clean surface",
            "Law -- a variable scaling along the sweep",
        ),
        needs=("A profile and at least one guide curve",),
        failures=(
            "\"The sweep cannot be computed\" where the spine has a curvature discontinuity or a radius smaller than the profile",
            "The result twists, because the reference surface or pulling direction was left implicit",
            "The profile self-intersects around a tight corner of the guide",
        ),
        fixes=(
            "Give it an explicit spine -- a smooth, deliberately built curve, not the guide by default",
            "Turn on Smooth sweeping with a small deviation to absorb a minor kink",
            "Split the sweep at the discontinuity and join the halves",
        ),
        alternatives=("Adaptive Sweep (GSD only), Multi-sections Surface, Blend",),
        licence="P1 WSF has Explicit and Line; the full subtype set is GSD",
        see_also=("gsd.multi_sections_surface", "gsd.connect_checker"),
    ),
    command(
        "Multi-sections Surface",
        workbench=_WB,
        toolbar="Surfaces",
        aliases=(
            "multi-sections surface", "multi section surface", "loft", "lofted surface",
            "surface multi-sections", "multisektionsflache", "skin surface", "loft between sections",
        ),
        summary="Interpolates a surface through a series of section curves, optionally guided and spined.",
        menu="Insert > Surfaces > Multi-sections Surface",
        fields=(
            "Section -- each with a closing point and an orientation arrow",
            "Guide -- curves the surface must pass through along its length",
            "Spine -- computed, or an explicit curve",
            "Coupling -- Ratio / Tangency / Tangency then curvature / Vertices",
            "Relimitation -- whether the surface stops at the first and last sections",
            "Canonical surface detection -- lets the result become a plane or cylinder when it is one",
            "Tangent/Curvature continuity to adjacent surfaces at start and end",
        ),
        needs=("At least two section curves",),
        failures=(
            "The surface twists -- closing points are not aligned; the arrows in the preview show it",
            "\"Sections are not compatible\" -- differing numbers of segments with Vertices coupling",
            "A guide that does not actually touch every section makes the loft fail rather than approximate",
        ),
        fixes=(
            "Drag the closing point on each section until the arrows line up",
            "Switch coupling from Vertices to Ratio when the sections have different segment counts",
            "Add an explicit spine when the automatic one wanders",
        ),
        alternatives=("Blend for a two-boundary transition; Sweep when there is a clear profile and path",),
        aerospace="How a wing or fuselage OML is lofted: aerofoil sections at each station, guides along the leading and trailing edges, an explicit spine so the surface parameterisation is predictable when stations are added later.",
        licence="P1 WSF / P2 GSD",
        see_also=("workflow.oml_loft",),
    ),
    command(
        "Join",
        workbench=_WB,
        toolbar="Operations",
        aliases=("join", "assembler", "verbinden", "assembla", "unir", "sew surfaces", "merge surfaces", "combine surfaces"),
        summary="Merges adjacent surfaces or curves into one element, within a merging distance.",
        menu="Insert > Operations > Join",
        fields=(
            "Elements To Join",
            "Check tangency / Check connexity / Check manifold -- each turns a silent bad result into an explicit failure",
            "Simplify the result -- collapses the join to fewer faces where it can",
            "Ignore erroneous elements -- skips what cannot be joined instead of failing",
            "Merging distance -- the gap it is allowed to close (default 0.001 mm)",
            "Angular threshold",
            "Sub-Elements To Remove",
            "Federation -- names a group of faces so downstream selections survive a re-split",
        ),
        needs=("Two or more adjacent surfaces or curves",),
        failures=(
            "\"The elements are not connex\" -- there is a real gap larger than the merging distance, or the pieces genuinely do not touch",
            "Check tangency fails at a legitimate sharp edge -- untick it, that is a design feature not a defect",
            "The join succeeds but produces a non-manifold result that every downstream operation then refuses",
        ),
        fixes=(
            "Use Connect Checker first to see where and how big the gaps are, then set a merging distance that covers them and nothing else",
            "Heal the surfaces (Healing) rather than raising the merging distance to hide a real gap",
            "Untick Check tangency when the geometry has intended creases",
        ),
        alternatives=("Healing, for gaps too large to merge; Sew Surface, to attach a surface to a solid",),
        licence="P1",
        see_also=("gsd.healing", "gsd.connect_checker", "diagnostic.not_connex"),
    ),
    command(
        "Connect Checker",
        workbench=_WB,
        toolbar="Analysis / Shape Analysis",
        aliases=(
            "connect checker", "continuity check", "g0 g1 g2", "gap analysis", "check continuity",
            "analyse de connexion", "curvature continuity", "tangency check", "surface gap",
        ),
        summary="Measures how two surfaces or curves actually meet: positional gap (G0), tangency angle (G1), curvature difference (G2), and curvature-variation (G3).",
        menu="Insert > Analysis > Connect Checker",
        fields=(
            "Analysis type -- Surface-Surface, Curve-Curve, Curve-Surface",
            "Connexion -- Boundary / All / Projection",
            "Quick / Full analysis",
            "Distance, Tangency, Curvature, Overlap -- each with its own maximum-deviation display",
            "Comb display with amplification and density, Envelope, Discretisation",
            "Maximum gap / Maximum tangency / Maximum curvature thresholds, shown numerically",
        ),
        needs=("Two elements that are supposed to meet",),
        failures=(
            "The comb looks alarming purely because the amplification is high; read the numeric maximum, not the picture",
        ),
        fixes=(
            "G0 gaps below the model tolerance (0.001 mm by default) are acceptable to Join; anything larger needs Healing or a rebuilt boundary",
            "G1 breaks show as a step in the comb direction; fix by rebuilding with tangency continuity, not by joining harder",
        ),
        aerospace="The gate before an OML is released: a Class-A-adjacent surface set is expected G2 across every internal boundary, and a G1-only join is what later shows up as a visible line on a painted skin.",
        licence="P1 -- included with WSF and GSD",
        see_also=("gsd.join", "gsd.healing"),
    ),
    command(
        "Healing",
        workbench=_WB,
        toolbar="Operations",
        aliases=("healing", "heal", "reparation", "heilung", "riparazione", "close gaps", "fix surfaces", "repair imported surface"),
        summary="Deforms surfaces slightly so they actually meet, closing gaps a Join cannot merge.",
        menu="Insert > Operations > Healing",
        fields=(
            "Elements to heal",
            "Continuity -- Point or Tangent",
            "Merging distance -- the largest gap it will close",
            "Distance objective -- how close it should get",
            "Tangency angle / Tangency objective",
            "Sharpness angle -- above this, an edge is treated as intended and left alone",
            "Freeze -- surfaces that must not be deformed",
            "Canonic freeze -- keeps planes and cylinders exact",
        ),
        needs=("Surfaces that nearly meet",),
        failures=(
            "Healing deforms a surface that had to stay exact -- typically a mating face or a datum plane",
            "A merging distance set large enough to close the worst gap also closes gaps that were intended openings",
        ),
        fixes=(
            "Freeze the surfaces that must not move before running it",
            "Heal in stages: a tight merging distance first, then a larger one on what remains",
        ),
        alternatives=("Healing Assistant (HA1) for batch diagnosis; rebuilding the boundary, which is often faster than healing it",),
        licence="P2 -- GSD",
        see_also=("gsd.join", "healing_assistant"),
    ),
    command(
        "Split",
        workbench=_WB,
        toolbar="Operations",
        aliases=("split", "decouper", "cut surface", "teilen", "taglia", "dividir", "split surface with curve"),
        summary="Cuts an element with another and keeps the chosen side.",
        menu="Insert > Operations > Split",
        fields=(
            "Element to cut",
            "Cutting elements -- one or several",
            "Other side / Next -- which portion is kept",
            "Keep both sides -- produces two results",
            "Intersections computation, Automatic extrapolation -- extends a cutting element that does not quite reach",
        ),
        failures=(
            "The cutting element does not fully cross the target, so nothing is removed",
            "The wrong side is kept -- one click of \"Other side\" away, and easy to miss in a busy view",
        ),
        fixes=("Tick Automatic extrapolation when the cutter falls just short; extend it explicitly when it falls a long way short",),
        alternatives=("Trim, which keeps portions of *both* elements and mutually relimits them",),
        licence="P1",
        see_also=("gsd.trim",),
    ),
]


_WIREFRAME = bulk(
    """
Point | point, punkt, punto | Coordinates, On curve, On plane, On surface, Circle centre, Tangent point on curve, Between
Points and Planes Repetition | points and planes repetition, repeat points, divide a curve
Extremum | extremum, extreme point, max point, min point, highest point
Extremum Polar | extremum polar, polar extremum
Line | line, droite, gerade, retta | Point to point, Point-direction, Angle/normal to curve, Tangent to curve, Normal to surface, Bisecting
Axis | axis, reference axis, axe
Polyline | polyline, chained lines, ligne brisee
Plane | plane, plan, ebene, piano, plano
Planes Between | planes between, several planes, plans entre
Projection | projection, project onto surface, projeter, proiezione
Combine | combine, combined curve, two direction projection, combiner
Reflect Line | reflect line, silhouette line, ligne de reflexion, draft line
Intersection | intersection, intersect, intersect two surfaces, schnittmenge, intersezione
Parallel Curve | parallel curve, offset curve on surface, courbe parallele, geodesic offset
Rolling Offset | rolling offset, offset rolling on a surface
3D Curve Offset | 3d curve offset, offset a 3d curve
Circle | circle, cercle, kreis, cerchio | Centre and radius, Centre and point, Two points and radius, Three points, Centre and axis, Bitangent and radius, Bitangent and point, Tritangent, Centre and tangent
Corner | corner, corner between curves, conge 3d
Connect Curve | connect curve, connect two curves, courbe de connexion
Conic | conic, conic curve, conique
Spline | spline, 3d spline, courbe spline
Helix | helix, spiral helix, helice, coil, thread path
Spiral | spiral, flat spiral, spirale
Spine | spine, colonne vertebrale, guide spine
Isoparametric Curve | isoparametric curve, isoparametric, iso curve, isopara
Contour | contour, planar contour, contour on surface
Law | law, loi, linear law, s-type law, advanced law, law defined by curve
""",
    workbench=_WB,
    toolbar="Wireframe",
)

_SURFACES = bulk(
    """
Revolve | revolve, revolution surface, revolution, rotationskorper, rivoluzione, revolved surface
Sphere | sphere, spherical surface, sphere surface
Cylinder | cylinder, cylindrical surface, cylindre
Offset | offset, offset surface, decalage, surface offset, parallel surface
Variable Offset | variable offset, variable offset surface
Rough Offset | rough offset, rough offset surface, offset that always works
Adaptive Sweep | adaptive sweep, balayage adaptatif, sweep with varying section
Fill | fill, fill surface, remplissage, fullen, riempimento, relleno, patch a hole
Blend | blend, blend surface, raccord, transition surface, bridge two surfaces
""",
    workbench=_WB,
    toolbar="Surfaces",
)

_OPERATIONS = bulk(
    """
Trim | trim, relimiter, mutual trim, trimmen, relimita, recortar
Untrim | untrim, restore surface, restaurer, remove trim
Disassemble | disassemble, decomposer, explode surface, all cells, domains only
Boundary | boundary, frontiere, randkurve, extract boundary, edge curve, contorno
Extract | extract, extraire, extrahieren, extract a face, copy a face
Multiple Extract | multiple extract, extract several
Extrapolate | extrapolate, extrapoler, extend surface, extrapolieren, lengthen a curve
Shape Fillet | shape fillet, surface fillet, conge, bitangent fillet, tritangent fillet, styling fillet, fillet with hold curve
Invert Orientation | invert orientation, invert, flip normal, inverser l orientation, reverse normals
Near | near, nearest element, proche
Unfold | unfold, deplier, flatten a surface, unroll
Transfer | transfer, transfer curve to flat, transferer
Fit To Geometry | fit to geometry, fit surface, conform to
Wrap Curve | wrap curve, deform along a curve, enrouler
Wrap Surface | wrap surface, deform with a surface
Bump | bump, local deformation, bosse
Shape Morphing | shape morphing, morph, morphing
Diabolo | diabolo, diabolo deformation
Affinity | affinity, non uniform scale, affinite
Translate | translate, move surface, translation
Rotate | rotate, rotate surface, rotation
Symmetry | symmetry, mirror surface, symetrie
Scaling | scaling, scale surface, echelle
""",
    workbench=_WB,
    toolbar="Operations",
)

_VOLUMES = bulk(
    """
Volume Extrude | volume extrude, extruded volume
Volume Revolve | volume revolve, revolved volume
Volume Sweep | volume sweep, swept volume feature
Volume Fill | volume fill, filled volume
Volume Multi-sections | volume multi-sections, lofted volume
Volume Split | volume split, split a volume
Volume Trim | volume trim, trim a volume
Volume Join | volume join, join volumes
Thick Surface (volume) | thick surface volume, volume from thick surface
""",
    workbench=_WB,
    toolbar="Volumes",
)

_SHAPE_ANALYSIS = bulk(
    """
Surfacic Curvature Analysis | surfacic curvature analysis, gaussian curvature, minimum curvature, maximum curvature, inflection area, courbure surfacique
Porcupine Curvature Analysis | porcupine, curvature comb, porcupine analysis, comb analysis, herisson
Draft Analysis (surface) | surface draft analysis, feature draft analysis, depouille surfacique
Isophote Mapping | isophote, isophote mapping, isophotes
Reflection Lines | reflection lines, reflection line analysis, lignes de reflexion
Highlight Lines | highlight lines, highlight line analysis
Cutting Planes Analysis | cutting planes analysis, section analysis, planes de coupe
Distance Analysis | distance analysis, deviation analysis, analyse de distance, compare two surfaces
Surface Curvature Mapping | surface curvature mapping, curvature map, mapping de courbure
Geometric Information | geometric information, information, element information
Apply Dress-Up | apply dress-up, dress up surface, appliquer un habillage
Environment Mapping | environment mapping, reflection mapping, environment map
Inflection Lines | inflection lines, inflection analysis
""",
    workbench=_WB,
    toolbar="Shape Analysis",
)

_DEVELOPED = bulk(
    """
Unfold (Developed Shapes) | powered unfold, powered flatten, developed shapes unfold, unfold a ruled surface
Develop | develop, develop a wrapping curve, developpement
Transfer (Developed Shapes) | transfer to flat, transfer between folded and flat
""",
    workbench="developed_shapes",
    toolbar="Developed Shapes",
    licence="P1 -- Developed Shapes 1 (DL1)",
)

_FREESTYLE = bulk(
    """
3D Curve | 3d curve, freestyle curve, courbe 3d
Curve on Surface | curve on surface, cos, courbe sur surface
Sketch Curve | sketch curve, freestyle sketch curve
Project Curve | project curve, freestyle projection
Style Corner | style corner, styling corner
Match Curve | match curve, match two curves, raccorder des courbes
Curve Smooth | curve smooth, smooth a curve, lissage
Break Curve or Surface | break curve, break surface, casser
Concatenate | concatenate, concatener, merge into one curve
Fragmentation | fragmentation, fragment a curve
Curve Connect | curve connect, freestyle connect
Planar Patch | planar patch, flat patch
3-4 Point Patch | 3 point patch, 4 point patch, patch by points
Geometry Extraction | geometry extraction, extract for freestyle
Extrude Surface (FreeStyle) | freestyle extrude, styling extrude
Net Surface | net surface, surface from a net of curves
Styling Sweep | styling sweep, freestyle sweep
Styling Fillet | styling fillet, freestyle fillet
Blend Surface (FreeStyle) | freestyle blend, styling blend
Match Surface | match surface, match two surfaces, raccorder des surfaces
Multi-Side Surface | multi-side surface, n-sided patch, multi side
Control Points | control points, poles, pushing points, points de controle, ctrl points
Shape Modification | shape modification, deform surface, modification de forme
Global Deformation | global deformation, deform globally
Bend | bend, bend a surface
Twist | twist, twist a surface
Extend | extend, extend a freestyle surface
Symmetry (FreeStyle) | freestyle symmetry
""",
    workbench=_FS,
    toolbar="FreeStyle",
    licence="P1 FreeStyle Shaper 1 (FS1) / P2 Shaper 2 (FSS)",
)

_IMAGINE = bulk(
    """
Subdivision Primitive | subdivision primitive, subdiv primitive, imagine and shape primitive, start shape
Subdivide | subdivide, split face, add resolution
Extrude Face | extrude face, pull a face, push pull
Cut Face | cut face, cut a subdivision face
Weld | weld, weld vertices, merge points
Unweld | unweld, split vertices
Crease | crease, sharpen an edge, hard edge
Smooth (Imagine & Shape) | smooth subdivision, relax mesh
Attractor | attractor, attract points
Convert to NURBS | convert to nurbs, convert subdivision to surface, tesselate to nurbs
""",
    workbench=_IMA,
    toolbar="Imagine & Shape",
    licence="P2 -- Imagine & Shape 2 (IMA)",
)

_SKETCH_TRACER = bulk(
    """
Create Immersive Sketch | immersive sketch, sketch tracer immersive
Create Sketch (Sketch Tracer) | sketch tracer sketch, image on a plane, blueprint plane
Use Painted Sketch | painted sketch, use a painted sketch
""",
    workbench="sketch_tracer",
    toolbar="Sketch Tracer",
    licence="P1 -- FreeStyle Sketch Tracer 1 (FSK)",
)

_DSE_QSR = bulk(
    """
Import Cloud | import cloud, import point cloud, import scan, importer un nuage
Filter Cloud | filter cloud, thin the cloud, homogeneous filter, adaptive filter
Align Clouds | align clouds, best fit alignment, register scans, recalage
Remove | remove points, trim the cloud, erase points
Mesh Creation | mesh creation, tessellate cloud, create a mesh from points
Mesh Smoothing | mesh smoothing, smooth a mesh
Fill Holes | fill holes, fill mesh holes, boucher les trous
Planar Sections | planar sections, section a cloud, sections planes
Curve from Scan | curve from scan, curve on cloud, 3d curve on mesh
Curve from Cloud | curve from cloud, extract a curve from points
Activate | activate a region, activate cloud region
Deviation Analysis | deviation analysis, cloud to surface deviation, ecart
Power Fit | power fit, powerfit, fit a surface to a cloud
Automatic Surface | automatic surface, auto surface from mesh, surface automatique
Curvature Mapping (QSR) | qsr curvature mapping, curvature map on a mesh
Basic Surface Recognition | basic surface recognition, recognise a plane cylinder sphere, canonical recognition
""",
    workbench="dse",
    toolbar="Digitized Shape Editor / Quick Surface Reconstruction",
    licence="P2 -- DSE / QSR",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_WIREFRAME,
    *_SURFACES,
    *_OPERATIONS,
    *_VOLUMES,
    *_SHAPE_ANALYSIS,
    *_DEVELOPED,
    *_FREESTYLE,
    *_IMAGINE,
    *_SKETCH_TRACER,
    *_DSE_QSR,
]

SECTION = Section("surfaces", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
