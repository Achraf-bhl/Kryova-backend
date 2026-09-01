"""Sketcher: profiles, operations, constraints and the tools that diagnose them.

The sketch is where most CATIA parts fail, and they fail in a small number of
ways that all produce the same user report ("the pad won't work"). The four that
matter are recorded as failure modes on the commands that surface them: an open
profile, a self-intersecting profile, an under-constrained sketch that moves
when something upstream changes, and a sketch whose support was deleted.

`Sketch Analysis` is the command that answers all four, which is why it is
written out in full rather than left in the bulk list.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_WB = "sketcher"


_PROFILE = bulk(
    """
Profile | profile, polyline sketch, connected line, profil | Chained lines and arcs in one command; the default drawing tool
Line | line, segment, droite, gerade, retta, recta
Infinite Line | infinite line, construction line, unlimited line
Bi-Tangent Line | bi-tangent line, bitangent line, tangent to two circles
Bisecting Line | bisecting line, bisector, bissectrice
Line Normal To Curve | line normal to curve, normal line, perpendicular to curve
Axis | axis, centreline, center line, axe, revolution axis | The construction axis a Shaft or Groove revolves about
Point | point, punkt, punto, sketch point
Point by Coordinates | point by coordinates, point using coordinates, typed point
Equidistant Points | equidistant points, points on curve, divide curve
Intersection Point | intersection point, point at intersection
Projection Point | projection point, project point onto curve
Rectangle | rectangle, rechteck, rettangolo, box, square
Oriented Rectangle | oriented rectangle, angled rectangle, rotated rectangle
Parallelogram | parallelogram, parallelogramme
Elongated Hole | elongated hole, slot shape, obround, stadium, oblong, trou oblong
Cylindrical Elongated Hole | cylindrical elongated hole, arc slot, curved slot
Keyhole Profile | keyhole profile, keyhole, key hole slot
Hexagon | hexagon, hex, six sided, hexagone
Centered Rectangle | centered rectangle, centred rectangle, rectangle from centre
Centered Parallelogram | centered parallelogram, centred parallelogram
Circle | circle, cercle, kreis, cerchio, circulo, round
Three Point Circle | three point circle, 3 point circle, circle through 3 points
Circle Using Coordinates | circle using coordinates, circle by coordinates
Tri-Tangent Circle | tri-tangent circle, tritangent circle, circle tangent to three
Arc | arc, arc by centre, centre arc
Three Point Arc | three point arc, 3 point arc
Three Point Arc Starting With Limits | three point arc starting with limits, arc by endpoints
Ellipse | ellipse, oval, ellipse sketch
Parabola by Focus | parabola by focus, parabola
Hyperbola by Focus | hyperbola by focus, hyperbola
Conic | conic, conic curve, conique
Spline | spline, curve through points, b-spline, courbe
Connect Curve | connect curve, connect, curve connection
""",
    workbench=_WB,
    toolbar="Profile",
)

_OPERATION = bulk(
    """
Corner | corner, sketch fillet, round corner, fillet in sketch, conge, ecke
Chamfer | chamfer, sketch chamfer, bevel, chanfrein, fase, smusso
Trim | trim, relimit, extend and trim, relimiter, trimmen
Break | break, split curve, cut curve
Quick Trim | quick trim, quick delete, scissors, eraser trim
Close | close, close arc, complete circle
Complement | complement, invert arc, other side of arc
Mirror | mirror, symmetry keeping original, spiegeln, specchio
Symmetry | symmetry, move by symmetry, symetrie
Translate | translate, move, duplicate by translation, verschieben
Rotate | rotate, turn, drehen, rotazione
Scale | scale, resize, echelle, massstab
Offset | offset, parallel curve, decalage, propagation offset
Project 3D Elements | project 3d elements, project edge, projection, yellow projected geometry
Intersect 3D Elements | intersect 3d elements, intersection with sketch plane
Project 3D Silhouette Edges | project 3d silhouette edges, silhouette
Project 3D Canonical Edges | project 3d canonical edges, canonical edge projection
Isolate | isolate, break link to 3d, isolate projected geometry
Rectangular Pattern (2D) | 2d rectangular pattern, sketch pattern, repeat in sketch
User Pattern (2D) | 2d user pattern, sketch user pattern
""",
    workbench=_WB,
    toolbar="Operation",
)

_CONSTRAINT = bulk(
    """
Constraint Defined in Dialog Box | constraint dialog, constraints defined in dialog box, multi constraint dialog
Contact Constraint | contact constraint, sketch contact
Fix Together | fix together, group elements, rigid group in sketch
Auto Constraint | auto constraint, automatic constraint, constrain everything
Animate Constraint | animate constraint, animate dimension, sweep a dimension
Edit Multi-Constraint | edit multi-constraint, edit all dimensions, constraint table
Coincidence | coincidence, coincident, on point, coincidence constraint, koinzidenz
Concentricity | concentricity, concentric, same centre
Tangency | tangency, tangent, tangent constraint, tangente
Parallelism | parallelism, parallel, parallele
Perpendicularity | perpendicularity, perpendicular, normal constraint, 90 degrees
Horizontal | horizontal, horizontal constraint, h constraint
Vertical | vertical, vertical constraint, v constraint
Symmetry Constraint | symmetry constraint, symmetric about axis
Equidistant Point Constraint | equidistant point, equidistant constraint
Fix Constraint | fix, fixed, anchor, fixity, ground element
""",
    workbench=_WB,
    toolbar="Constraint",
)

_TOOLS = bulk(
    """
Grid | grid, snap grid, sketch grid, gitter, primary spacing, graduations
Snap to Point | snap to point, snap, magnetism, aimantation
Construction/Standard Element | construction geometry, construction element, dashed geometry, reference geometry, standard element
Geometrical Constraints toggle | geometrical constraints toggle, auto geometric constraints, stop creating constraints
Dimensional Constraints toggle | dimensional constraints toggle, auto dimensional constraints
Cut Part by Sketch Plane | cut part by sketch plane, section the part, cut by sketch plane
Change Sketch Support | change sketch support, move sketch to another plane, change plane
Reflect Line | reflect line, reflect line in sketch
Output Feature | output feature, sketch output, output profile
SmartPick | smartpick, smart pick, inference, automatic snapping
Sketch Tools numeric entry | sketch tools toolbar, numeric entry, type coordinates while sketching
""",
    workbench=_WB,
    toolbar="Sketch tools / Visualization",
)


_DETAILED: list[Entry] = [
    command(
        "Sketch",
        workbench=_WB,
        toolbar="Sketcher",
        aliases=("sketch", "esquisse", "skizze", "schizzo", "croquis", "enter sketcher", "new sketch"),
        summary="Opens the Sketcher on a plane or planar face and creates a Sketch feature under the current body or geometrical set.",
        menu="Insert > Sketcher > Sketch",
        icon="a 2D profile on a small plane",
        needs=(
            "A plane, a planar face or a planar surface must be selected first, or CATIA asks for one",
            "The current body (Define In Work Object) decides where the sketch lands in the tree",
        ),
        fields=(
            "Sketch support -- the plane or face; changed later with Change Sketch Support",
        ),
        failures=(
            "The sketch is created under the wrong body because Define In Work Object was somewhere unexpected",
            "Sketching on a *face* rather than a datum plane makes the sketch depend on that face's topology; a later change to the feature that owns the face can break it",
        ),
        fixes=(
            "Sketch on the three origin planes or on planes you created deliberately, not on model faces, whenever the sketch will outlive the face",
        ),
        alternatives=("Positioned Sketch, when the sketch origin and H/V direction must be controlled explicitly",),
        licence="P1 -- included with Part Design",
        see_also=("sketcher.positioned_sketch", "sketcher.sketch_analysis"),
    ),
    command(
        "Positioned Sketch",
        workbench=_WB,
        toolbar="Sketcher",
        aliases=("positioned sketch", "esquisse positionnee", "sketch positioning", "sketch origin", "sketch orientation"),
        summary="A sketch whose origin and H/V axes are set explicitly rather than inherited from the support, so the 2D frame does not flip when the support changes.",
        menu="Insert > Sketcher > Positioned Sketch",
        fields=(
            "Type -- Positioned",
            "Origin -- Implicit / Projection point / Intersection 2 lines / Curve intersection / Middle point / Barycenter",
            "Orientation -- Implicit / Parallel to line / Intersection plane / Components / Through point",
            "Reference -- the element the origin or orientation is taken from",
            "Swap / Reverse H / Reverse V",
        ),
        failures=(
            "Left implicit, a plain Sketch takes its H/V from the support's own parameterisation, and a change upstream can rotate the whole sketch by 90 degrees without any dimension changing",
        ),
        fixes=("Use a Positioned Sketch anywhere the sketch is reused, patterned or driven by a design table",),
        aerospace="The right default for anything positioned by station: origin on an intersection with the station plane, orientation parallel to a reference axis, so the profile cannot silently rotate when the OML is re-lofted.",
        licence="P1",
    ),
    command(
        "Constraint",
        workbench=_WB,
        toolbar="Constraint",
        aliases=("constraint", "dimension", "dimensional constraint", "contrainte", "bedingung", "vincolo", "restriccion", "add dimension", "cote"),
        summary="Adds a dimensional or geometric constraint between selected sketch elements; what you get depends on what is selected.",
        menu="Insert > Constraint > Constraint",
        icon="two arrows pointing outwards between two extension lines",
        shortcut="No default accelerator; commonly bound by sites",
        fields=(
            "Right-click while placing to choose the constraint type (distance, length, angle, radius, diameter, semi-major axis, semi-minor axis)",
            "Double-click the value to edit, and to set a reference (driven) dimension",
        ),
        needs=("One or two sketch elements selected",),
        failures=(
            "\"The constraint is not consistent with the other ones\" -- adding it would over-constrain the sketch",
            "Green sketch geometry means fully constrained; white means under-constrained and free to move",
        ),
        fixes=(
            "Delete the redundant constraint the solver names, or set one dimension to reference (driven) rather than driving",
            "Use Sketch Analysis to list what is still under-constrained before adding more",
        ),
        licence="P1",
        see_also=("sketcher.sketch_analysis", "practice.fully_constrain"),
    ),
    command(
        "Sketch Analysis",
        workbench=_WB,
        toolbar="Tools",
        aliases=(
            "sketch analysis",
            "analyse d esquisse",
            "sketch solving status",
            "check sketch",
            "why won't my pad work",
            "profile not closed",
            "diagnose sketch",
        ),
        summary="Reports what is wrong with the active sketch: open profiles, gaps, self-intersections, isolated elements, and how far from fully constrained it is.",
        menu="Tools > Sketch Analysis",
        fields=(
            "Geometry tab -- every element with a status: Closed, Opened, Isolated, Not-changed geometry",
            "Projections/Intersections tab -- the state of every projected 3D element and whether its link is broken",
            "Diagnostic tab -- Under-Constrained / Iso-Constrained / Over-Constrained, with the count",
            "Corrective actions -- Close the profile, Erase geometry, Set to construction geometry, Hide constraints",
        ),
        needs=("The sketch must be open in the Sketcher",),
        failures=(
            "It reports \"Opened\" profiles that look closed on screen -- two endpoints within a pixel but not coincident",
            "It reports isolated elements left over from construction, which a Pad silently refuses to use",
        ),
        fixes=(
            "Use the Corrective actions column: Close it, or convert the stray element to construction geometry",
            "Add a coincidence constraint at each reported gap rather than dragging the endpoints together",
        ),
        alternatives=("Tools > Sketch Solving Status, for the constraint count alone",),
        licence="P1",
        see_also=("diagnostic.open_profile", "diagnostic.self_intersecting"),
    ),
]

ENTRIES: list[Entry] = [*_DETAILED, *_PROFILE, *_OPERATION, *_CONSTRAINT, *_TOOLS]

SECTION = Section("sketcher", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
