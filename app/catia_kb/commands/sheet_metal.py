"""Sheet metal, in both products, plus Structure Design and Weld Design.

Generative Sheetmetal Design (SMD) and Aerospace Sheet Metal Design (ASL) are
different products with different commands, and conflating them is the most
consequential mistake available in this whole domain. SMD builds from a *wall*
of constant thickness with bends of constant radius; ASL builds from a *web* on
a support surface with flanges that follow a curved edge and can carry an angle
law. A rib for an aircraft is an ASL part; a bracket bent from flat stock is an
SMD part. Telling an airframe engineer to use SMD's Wall On Edge for a curved
flange on a lofted rib sends them down a day of work that cannot succeed.

Joggle is the giveaway. It exists in ASL as a first-class feature and does not
exist in SMD at all; anyone asking how to make one in Sheet Metal Design is in
the wrong workbench.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_SMD = "sheet_metal_design"
_ASL = "aerospace_sheet_metal"
_STR = "structure_design"
_WELD = "weld_design"


_SMD_DETAILED: list[Entry] = [
    command(
        "Sheet Metal Parameters",
        workbench=_SMD,
        toolbar="Sheet Metal Parameters",
        aliases=(
            "sheet metal parameters", "sheetmetal parameters", "thickness and radius",
            "k factor", "bend allowance", "parametres de tolerie", "bend table", "set thickness",
        ),
        summary="The part-wide thickness, default bend radius, bend allowance and relief defaults. Nothing else in the workbench works until this exists.",
        menu="Insert > Sheet Metal Parameters",
        fields=(
            "Parameters tab -- Thickness, Default Bend Radius",
            "Bend Extremities tab -- Minimum with no relief, Square relief, Round relief, Linear, Tangent, Maximum, Closed, Trapezoidal relief (each with its own L1/L2 or angle)",
            "Bend Allowance tab -- K Factor, or a Bend Allowance value, or a bend table (DIN, company .xls)",
            "Tolerance / Sheet Standards Files -- a site-supplied standards file overrides the manual values",
        ),
        needs=("Must be the first feature; every wall and bend inherits from it",),
        failures=(
            "Changing thickness after the part is built rebuilds every bend and can fail features that had no relief clearance",
            "A K factor left at the default gives a flat pattern that does not match the press brake, and nobody notices until the first part is cut",
        ),
        fixes=("Load the shop's bend table rather than typing a K factor, whenever there is one",),
        licence="P1/P2 -- SMD",
        see_also=("sheet_metal_design.unfold", "aerospace_sheet_metal.aerospace_sheet_metal_parameters"),
    ),
    command(
        "Wall",
        workbench=_SMD,
        toolbar="Walls",
        aliases=("wall", "first wall", "paroi", "wand", "parete", "pared", "base wall", "sheet metal wall"),
        summary="The first, flat face of a sheet metal part, extruded from a profile at the part thickness.",
        menu="Insert > Walls > Wall",
        needs=("Sheet Metal Parameters must exist", "A closed profile"),
        failures=("Created before Sheet Metal Parameters, the command simply is not available",),
        licence="P1/P2",
    ),
    command(
        "Wall On Edge",
        workbench=_SMD,
        toolbar="Walls",
        aliases=("wall on edge", "flange from edge", "bend up an edge", "paroi sur arete", "add a flange"),
        summary="Adds a wall on an existing edge, with the bend generated automatically.",
        menu="Insert > Walls > Wall On Edge",
        fields=(
            "Type -- Automatic or Sketch-based",
            "Height & Angle -- Height type (Height / Length), measured from Inner, Outer or Bend centre",
            "Clearance -- None, Monodirectional, Bidirectional, with a value",
            "Reverse position / Reverse direction",
            "With bend -- and the radius; Trim support",
        ),
        failures=(
            "The bend cannot be built because the edge is curved -- SMD bends about a straight line only",
            "Adjacent walls collide at a corner with no relief",
        ),
        fixes=(
            "For a curved edge, this is the wrong product: use Aerospace Sheet Metal's Flange, which follows a curved support",
            "Add a Corner Relief, or change the relief type in Sheet Metal Parameters",
        ),
        alternatives=("Flange, Hem, Tear Drop, User Flange for shaped edges; ASL Flange for curved ones",),
        licence="P1/P2",
        see_also=("aerospace_sheet_metal.flange",),
    ),
    command(
        "Unfold",
        workbench=_SMD,
        toolbar="Bending",
        aliases=("unfold", "flatten", "deplier", "abwickeln", "sviluppa", "desplegar", "flat pattern", "develop", "unbend"),
        summary="Switches between the folded part and its flat pattern; both are the same feature tree seen two ways.",
        menu="Insert > Bending > Unfold, or the Fold/Unfold toggle",
        fields=("Reference wall -- the face that stays put", "Bends to unfold -- all, or a selection"),
        failures=(
            "\"The part cannot be unfolded\" where a stamp or a non-developable surface is in the way",
            "The flat pattern length is wrong because the bend allowance or K factor does not match the shop's",
        ),
        fixes=(
            "Exclude non-developable features, or model them as recognised stamps rather than generic pockets",
            "Check Sheet Metal Parameters against the shop's bend table before trusting a blank length",
        ),
        alternatives=("Multi-viewer, to see folded and flat side by side; DXF export of the flat for nesting",),
        licence="P1/P2",
        see_also=("workflow.sheet_metal", "format.dxf"),
    ),
]


_SMD_BULK = bulk(
    """
Extrusion | sheet metal extrusion, extruded wall
Rolled Wall | rolled wall, rolled, cylinder wall, paroi enroulee
Hopper | hopper, hopper wall, tremie
Flange | flange, sheet metal flange, bord tombe, bordel, pestana
Hem | hem, hemmed edge, ourlet, saum, orlo, folded edge
Tear Drop | tear drop, teardrop hem
User Flange | user flange, custom flange profile
Bend | bend, pli, biegung, piegatura, plegado, bend between walls
Conical Bend | conical bend, tapered bend, pli conique
Bend From Flat | bend from flat, fold a line in the flat, bend from a line
Fold | fold, plier, falten, refold
Point or Curve Mapping | point mapping, curve mapping, transfer a curve to the flat
Cutout | cutout, sheet metal cutout, decoupe, ausschnitt, ritaglio, recorte
Hole | sheet metal hole, hole in a wall
Circular Stamp | circular stamp, round stamp, emboutissage circulaire
Rectangular Stamp | rectangular stamp
Curve Stamp | curve stamp
Surface Stamp | surface stamp, stamp from a surface
User Stamp | user stamp, custom stamp
Bead | bead, stiffening bead, nervure d emboutissage
Flanged Hole | flanged hole, extruded hole, collar, dawn hole
Flanged Cutout | flanged cutout, extruded cutout
Louver | louver, louvre, persienne, vent stamp
Bridge | bridge, bridge stamp
Stiffening Rib | stiffening rib, sheet metal rib
Dowel | dowel, dowel stamp, locating dimple
Corner | corner, sheet metal corner, corner relief radius
Chamfer | sheet metal chamfer, corner chamfer
Corner Relief | corner relief, relief, degagement d angle, relief cut
Mitre Corner | mitre corner, miter corner, corner mitre
Junction | junction, junction between walls
Recognize | recognize, convert to sheet metal, recognise sheet metal, reconnaissance
Multi-Viewer | multi-viewer, folded and flat side by side, fenetre multiple
Stamping Catalogue | stamping catalogue, stamp catalog, punch catalog
""",
    workbench=_SMD,
    toolbar="Walls / Bending / Cutting-Stamping",
    licence="P1 Sheetmetal Design 1 (SM1) / P2 Sheetmetal Design 2 (SMD)",
)


_ASL_DETAILED: list[Entry] = [
    command(
        "Web",
        workbench=_ASL,
        toolbar="Walls",
        aliases=("web", "aerospace web", "ame", "base web", "web on a surface", "asl web"),
        summary="The base face of an aerospace sheet metal part, lying on a support surface rather than on a plane.",
        menu="Insert > Walls > Web",
        fields=(
            "Support -- the surface the web lies on (the OML, a rib plane, a station plane)",
            "Contour -- the profile bounding it",
            "Thickness and material side",
        ),
        needs=("Aerospace Sheet Metal Parameters, and a support surface",),
        failures=("A support that is not a single connected surface -- join it first",),
        aerospace="The web of a rib, frame or spar. Because it sits on a real support surface, a change to the OML propagates through it, which is the whole reason this product exists.",
        licence="P3 -- Aerospace Sheetmetal Design 3 (ASL)",
        see_also=("aerospace_sheet_metal.flange", "workflow.airframe_structure"),
    ),
    command(
        "Flange",
        workbench=_ASL,
        toolbar="Walls",
        aliases=("aerospace flange", "asl flange", "surfacic flange", "curved flange", "flange on a curved edge"),
        summary="A flange that follows a curved edge of a web, standing off the support surface at an angle that can vary along its length.",
        menu="Insert > Walls > Flange",
        fields=(
            "Support / Web edge -- the curved edge it runs along",
            "Bend radius -- constant or from the parameters",
            "Flange angle -- a constant or a law along the edge",
            "Height -- constant, variable, or up to a surface",
            "Bend relief / Extremity conditions",
        ),
        needs=("A Web, and the edge to flange",),
        failures=(
            "The flange self-intersects on a tight inside curve",
            "The angle law produces a flange that will not flatten within springback tolerance",
        ),
        fixes=("Break the flange at the tight region and run two flanges; check the flattening before committing",),
        aerospace="This is the command SMD does not have. A rib flange that follows the OML curvature and changes angle along the chord is one ASL Flange, and is not achievable with SMD's Wall On Edge.",
        licence="P3 -- ASL",
        see_also=("sheet_metal_design.wall_on_edge", "aerospace_sheet_metal.joggle"),
    ),
    command(
        "Joggle",
        workbench=_ASL,
        toolbar="Aerospace Sheet Metal Features",
        aliases=("joggle", "joggling", "jog", "offset step", "runout", "run-out", "joggle a flange", "step in a flange"),
        summary="Steps a flange or web out of plane by one part thickness over a defined run-out length, so a second part can lap under it.",
        menu="Insert > Joggle",
        fields=(
            "Joggle height -- normally one thickness of the mating part, plus clearance",
            "Runout length -- how far the step is spread over",
            "Joggle start / limits along the flange",
            "Runout type -- the transition shape",
        ),
        needs=("A flange or web to joggle",),
        failures=(
            "A runout shorter than the shop's minimum for the material and thickness cannot be formed, whatever CATIA shows",
            "The joggle blocks the flattening because the transition is not developable at that runout",
        ),
        fixes=("Take the runout length from the process spec for the alloy and gauge, not from what looks right",),
        aerospace="Where two skins or a skin and a doubler overlap, the outer part is joggled so the outer mould line stays flush. Runout length is a process constraint, and it is the number this feature exists to carry.",
        licence="P3 -- ASL",
        see_also=("aero.joggle", "aerospace_sheet_metal.flange"),
    ),
    command(
        "Flattening",
        workbench=_ASL,
        toolbar="Flattening",
        aliases=("aerospace flattening", "asl flattening", "flatten aerospace part", "flatten", "flattening", "springback", "developed view", "mise a plat"),
        summary="Produces the flat blank for an aerospace part, accounting for the forming allowances and springback the part will actually see.",
        menu="Insert > Flattening",
        fields=("Reference element", "Flattening parameters -- allowances per bend and joggle", "Manufacturing view generation"),
        failures=("A feature that is not developable stops the flattening; the report names it",),
        aerospace="The output is the tooling and NC-router blank. It is not the same as SMD's Unfold: the allowances are per-feature and are set from process data.",
        licence="P3 -- ASL",
    ),
]


_ASL_BULK = bulk(
    """
Aerospace Sheet Metal Parameters | aerospace sheet metal parameters, asl parameters, aerospace parameters
Swept Wall | swept wall, aerospace swept wall
Bead | aerospace bead, asl bead
Cutback | cutback, cut back, flange cutback, corner cutback
Extremity Trim | extremity trim, trim the end of a flange
Bend Relief | aerospace bend relief, relief at a bend
Curved Flange | curved flange, flange following a curve
Lightening Hole | lightening hole, lightning hole, weight relief hole, flanged lightening hole
Stringer | stringer, longitudinal stiffener, lisse
Stiffener | aerospace stiffener, asl stiffener
Cleat | cleat, angle cleat, attachment cleat
Shear Tie | shear tie, shear clip, tie
Clip | clip, frame clip, skin clip
Doubler | doubler, doubler plate, reinforcement plate
Reference Plane | reference plane, asl reference plane, station reference plane
Support Surface | support surface, asl support, driving surface
Manufacturing View | manufacturing view, asl manufacturing view, flat manufacturing view
""",
    workbench=_ASL,
    toolbar="Aerospace Sheet Metal",
    licence="P3 -- Aerospace Sheetmetal Design 3 (ASL)",
    aerospace="Airframe detail-part vocabulary; all of it is ASL, none of it exists in Generative Sheetmetal Design.",
)


_STRUCTURE = bulk(
    """
Place Section | place section, place a profile, section catalogue, put a beam
Structure Member | structure member, member, beam member, poutre
Beam | beam, i beam, structural beam
Column | column, post, pillar, poteau
Plate | plate, structural plate, tole structurelle
Ladder | ladder, echelle
Stair | stair, staircase, escalier
Handrail | handrail, guard rail, garde-corps
Footing | footing, base plate, foundation
End Cut | end cut, cut the end of a member, coupe d extremite
Cutback | structure cutback, cut back a member
Section Catalogue | section catalogue, profile catalogue, i u l t c z tube angle, catalogue de profiles
""",
    workbench=_STR,
    toolbar="Structure",
    licence="P1/P2 -- Structure Design 1 (SR1)",
)

_WELD_BULK = bulk(
    """
Fillet Weld | fillet weld, soudure d angle, kehlnaht
Butt Weld | butt weld, groove weld, soudure bout a bout
Spot Weld | spot weld, resistance spot weld, point de soudure
Seam Weld | seam weld, continuous weld
Groove Weld | groove weld, v groove, u groove
Plug Weld | plug weld, slot weld
Edge Weld | edge weld
Surfacing Weld | surfacing weld, weld overlay, cladding
Welding Symbol | welding symbol, weld symbol, iso 2553, aws a2.4, symbole de soudure
""",
    workbench=_WELD,
    toolbar="Weld Features",
    licence="P1 -- Weld Design 1 (WD1)",
)


ENTRIES: list[Entry] = [
    *_SMD_DETAILED,
    *_SMD_BULK,
    *_ASL_DETAILED,
    *_ASL_BULK,
    *_STRUCTURE,
    *_WELD_BULK,
]

SECTION = Section("sheet_metal", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
