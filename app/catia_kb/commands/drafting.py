"""Drafting and 3D Functional Tolerancing & Annotation.

Two ways of stating the same product definition, and the choice between them is
a programme decision rather than a preference: a drawing-based release puts the
tolerances on a .CATDrawing, a 3D-master release puts them on the model with FTA
and ships them through STEP AP242. Both are recorded here, and the GD&T
vocabulary is recorded once, on the FTA side, because the fourteen geometric
characteristics are the same symbols in both.

The Drafting failure modes that matter are all associativity: a view that will
not update, a dimension that detached from the geometry it measured, and a
drawing that opens with the 3D unloaded and silently shows the last saved
picture rather than the current part.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_WB = "drafting"
_FTA = "fta"


_DETAILED: list[Entry] = [
    command(
        "Front View",
        workbench=_WB,
        toolbar="Views",
        aliases=("front view", "vue de face", "vorderansicht", "vista frontale", "vista frontal", "first view", "base view"),
        summary="The first generative view: projects the 3D onto the sheet along a chosen direction, and every other projected view derives from it.",
        menu="Insert > Views > Projections > Front View",
        fields=(
            "View direction -- pick a planar face or three points in the 3D window, then rotate with the on-screen dial",
            "View properties -- Scale, Angle, Display (hidden lines, axes, threads, centrelines, fillets, 3D points, 3D specifications, dimensions)",
            "Generation mode -- Exact / CGR / Approximate, with a \"raster\" option for very heavy views",
        ),
        needs=("A 3D document open, and a drawing sheet active",),
        failures=(
            "The view generates empty because the 3D window lost focus before a face was picked",
            "The view will not update: the link to the 3D is broken, or the part is not loaded",
            "Hidden line removal on a large assembly takes minutes -- it is the dominant cost in drawing generation",
        ),
        fixes=(
            "Use Edit > Links to see and repair the link to the 3D",
            "Turn off hidden lines and 3D specifications while laying out, and turn them on once at the end",
        ),
        alternatives=("View Creation Wizard, for a whole standard layout at once; View from 3D, to take the current 3D orientation",),
        licence="P1 Generative Drafting 1 (GD1) / P2 (GDR)",
        see_also=("drafting.section_view", "diagnostic.drawing_slow"),
    ),
    command(
        "Section View",
        workbench=_WB,
        toolbar="Views",
        aliases=(
            "section view", "cross section", "vue en coupe", "schnittansicht", "vista in sezione",
            "vista de seccion", "cut view", "offset section", "aligned section", "section cut",
        ),
        summary="Cuts the model along a sketched profile and projects what is behind it.",
        menu="Insert > Views > Sections > Offset Section View",
        fields=(
            "Profile -- sketched on the parent view; a straight line, an offset (stepped) profile or an aligned one",
            "Section View versus Section Cut -- the Cut shows only the cut faces, the View also shows geometry behind them",
            "Callout properties -- arrow style, letter, and whether the callout is shown",
            "Hatching -- pattern, angle, pitch, taken from the standard and overridable per view",
        ),
        needs=("A parent view",),
        failures=(
            "Components that should not be cut (fasteners, shafts, ribs along their length) are cut anyway -- by convention they are not",
            "The section arrows point the wrong way and the view shows the opposite half",
        ),
        fixes=(
            "In an assembly section, set the components to \"not cut\" through the view's Properties or the Overload Properties dialog",
            "Double-click the profile and reverse it rather than deleting and re-sketching",
        ),
        licence="P1/P2",
    ),
    command(
        "Dimensions",
        workbench=_WB,
        toolbar="Dimensions",
        aliases=(
            "dimension", "dimensions", "cotation", "cote", "bemassung", "quotatura", "acotacion",
            "add a dimension", "auto dimension", "generate dimensions",
        ),
        summary="Places dimensions, either one at a time with automatic type detection or generated in bulk from the 3D constraints and FTA.",
        menu="Insert > Dimensioning > Dimensions",
        fields=(
            "Tools palette -- chained, cumulated, stacked, and the projection direction",
            "Dimension properties -- tolerance type (ISO numerical, ISO alphanumerical, ANSI), dual dimension, value format, precision, font",
            "Generative Dimension Generation -- step by step or in one pass, with filters for which constraints become dimensions",
        ),
        needs=("A generative view whose 3D carries constraints or FTA, for the generated route",),
        failures=(
            "Generated dimensions land on top of each other and take longer to tidy than dimensioning by hand",
            "A dimension goes red or detaches when the geometry it measured is regenerated",
        ),
        fixes=(
            "Use the step-by-step generation so each dimension can be placed as it appears",
            "Re-route the dimension onto surviving geometry rather than deleting and re-creating it",
        ),
        licence="P1/P2",
        see_also=("fta.geometrical_tolerance",),
    ),
    command(
        "Generative View Style",
        workbench=_WB,
        toolbar="Tools / Options",
        aliases=("generative view style", "gvs", "view style", "drawing style", "company drawing standard"),
        summary="The site-defined rule set that decides what a generated view shows by default -- threads, axes, centrelines, fillets, hidden lines.",
        menu="Tools > Options > Mechanical Design > Drafting > Generative View Style, and per-view Properties",
        failures=("Two engineers produce visibly different drawings from the same model because their GVS differs -- this is a deployment problem, not a CATIA one",),
        fixes=("Put the GVS and the standards XML in a shared CATCollectionStandard and lock it in admin mode",),
        licence="P1/P2",
        see_also=("setting.catia_environment", "standards_editor"),
    ),
    command(
        "Geometrical Tolerance",
        workbench=_FTA,
        toolbar="Annotations",
        aliases=(
            "geometrical tolerance", "gd&t", "gdt", "gtol", "feature control frame", "tolerance geometrique",
            "position tolerance", "flatness", "perpendicularity", "true position", "form and position",
        ),
        summary="A feature control frame: one of the fourteen geometric characteristics, its zone, its material condition modifier and its datum reference frame.",
        menu="Insert > Annotations > Geometrical Tolerance",
        fields=(
            "Characteristic -- straightness, flatness, circularity, cylindricity, profile of a line, profile of a surface, angularity, perpendicularity, parallelism, position, concentricity, symmetry, circular runout, total runout",
            "Tolerance zone value, and diameter/spherical-diameter modifier",
            "Material condition -- MMC (M), LMC (L), RFS (default)",
            "Datum references -- primary, secondary, tertiary, each with its own modifier",
            "Projected tolerance zone, tangent plane, free state, statistical",
            "Composite tolerance -- two rows in one frame",
        ),
        needs=("Datums to reference, and an annotation plane to place it on",),
        failures=(
            "A semantic tolerance is refused because the referenced datum does not exist or is not valid for that characteristic -- the Tolerancing Advisor says which",
            "Non-semantic annotation looks identical on screen but carries no meaning downstream: it exports as a picture, not as data",
        ),
        fixes=(
            "Use the Tolerancing Advisor rather than free-text annotation whenever the PMI has to be consumed by CMM or inspection",
        ),
        aerospace="AS9102 first-article inspection consumes these as data. A drawing-equivalent non-semantic note fails that consumption and puts the characteristic back on a human to transcribe.",
        licence="P1 FTA 1 (FT1) / P2 FTA 2 (FTA)",
        see_also=("standard.asme_y14_5", "format.step"),
    ),
]


_VIEWS = bulk(
    """
Unfolded View | unfolded view, sheet metal unfolded view, flat pattern view
View from 3D | view from 3d, current 3d orientation, vue depuis la 3d
Projection View | projection view, projected view, vue projetee, orthographic view
Auxiliary View | auxiliary view, vue auxiliaire, angled view
Isometric View | isometric view, iso view, vue isometrique, 3d view on the drawing
Advanced Front View | advanced front view, front view with options
Offset Section View | offset section view, stepped section
Aligned Section View | aligned section view, revolved section
Offset Section Cut | offset section cut, stepped section cut
Aligned Section Cut | aligned section cut
Detail View | detail view, vue de detail, detailansicht, blow up, enlarged view
Detail View Profile | detail view profile, detail with a sketched boundary
Quick Detail View | quick detail view
Clipping View | clipping view, vue tronquee, crop view
Clipping View Profile | clipping view profile
Broken View | broken view, break a long part, vue interrompue
Breakout View | breakout view, partial section, vue avec arrachement
Add 3D Clipping | 3d clipping, clip the 3d in a view
Exploded View | exploded view drawing, drawing of an exploded assembly
View Creation Wizard | view creation wizard, wizard, standard view layout
""",
    workbench=_WB,
    toolbar="Views",
)

_DIMENSIONS = bulk(
    """
Chained Dimensions | chained dimensions, chain dimension, cotation en chaine
Cumulated Dimensions | cumulated dimensions, cumulative, cotation cumulee
Stacked Dimensions | stacked dimensions, cotation superposee
Length/Distance Dimension | length dimension, distance dimension, linear dimension
Angle Dimension | angle dimension, angular dimension, cote angulaire
Radius Dimension | radius dimension, radial dimension, cote de rayon
Diameter Dimension | diameter dimension, cote de diametre
Chamfer Dimension | chamfer dimension, cote de chanfrein
Thread Dimension | thread dimension, cote de filetage
Coordinate Dimensions | coordinate dimensions, ordinate dimensioning, cotation par coordonnees
Hole Dimension Table | hole dimension table, hole table, hole chart
Coordinate Dimension Table | coordinate dimension table, ordinate table
Datum Feature | datum feature, datum, reference de base, datum letter
Dimension System | dimension system, automatic dimensioning, incremental, cumulated
Re-route Dimension | re-route dimension, reroute, reattach a dimension
""",
    workbench=_WB,
    toolbar="Dimensioning",
)

_ANNOTATIONS = bulk(
    """
Text | text, note, texte, annotation text
Text with Leader | text with leader, leader note, texte avec ligne de renvoi
Balloon | balloon, item balloon, bulle, positionsnummer, pallinatura, globo, bubble
Datum Target | datum target, datum target symbol
Roughness Symbol | roughness symbol, surface finish, rugosite, surface texture
Welding Symbol | welding symbol, weld symbol on the drawing
Table | table, drawing table, tableau
Table from CSV | table from csv, import a table
Bill of Material Table | bill of material table, parts list, nomenclature table
Hyperlink | hyperlink, link on a drawing
Text Replication | text replication, attribute link, dollar parameter, link to a parameter
""",
    workbench=_WB,
    toolbar="Annotations",
)

_DRESS_UP = bulk(
    """
Center Line | center line, centre line, ligne d axe
Thread (Drafting) | drawing thread, thread representation on a drawing
Axis Line | axis line, axe, drawing axis
Axis Line and Center Line | axis line and center line
Area Fill | area fill, hatching, hachurage, schraffur, tratteggio, sombreado, hatch pattern
Arrow | arrow, drawing arrow, fleche
2D Component | 2d component, ditto, detail sheet component, composant 2d
Instantiate 2D Component | instantiate 2d component, place a ditto
""",
    workbench=_WB,
    toolbar="Dress-Up",
)

_SETUP = bulk(
    """
Sheet Background | sheet background, background view, working views, fond de plan
Frame and Title Block | frame and title block, title block, cartouche, drawing frame, border
Sheet Setup | sheet setup, page setup, format, a0 a1 a2 a3 a4, ansi a b c d e, projection method, first angle, third angle
New Sheet | new sheet, add a sheet, multi-sheet, nouvelle feuille
Update Drawing | update drawing, update from 3d, mise a jour de la mise en plan
Print/Plot | print, plot, imprimer, print setup
""",
    workbench=_WB,
    toolbar="Drawing / File menu",
)

_FTA_BULK = bulk(
    """
Annotation Plane | annotation plane, plan d annotation, annotation plane on the fly, view plane
Annotation Set | annotation set, jeu d annotations
Capture | capture, 3d capture, saved annotation view, view capture
Datum (FTA) | fta datum, 3d datum, datum feature in 3d
Dimension (FTA) | 3d dimension, dimension in 3d, cotation 3d
Roughness (FTA) | 3d roughness, surface finish in 3d
Weld (FTA) | 3d weld symbol
Flag Note | flag note, flagnote, note drapeau
Text with Leader (FTA) | 3d text with leader, 3d note
Semantic Annotation | semantic annotation, semantic pmi, meaningful annotation
Non-Semantic Annotation | non-semantic annotation, non semantic, presentation only annotation
Tolerancing Advisor | tolerancing advisor, advisor, conseiller en tolerancement
Analysis Display Mode | analysis display mode, fta analysis display
Annotation Repositioning | annotation repositioning, reposition annotations
""",
    workbench=_FTA,
    toolbar="Annotations / Views / Analysis",
    licence="P1 FT1 / P2 FTA",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_VIEWS,
    *_DIMENSIONS,
    *_ANNOTATIONS,
    *_DRESS_UP,
    *_SETUP,
    *_FTA_BULK,
]

SECTION = Section("drafting", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
