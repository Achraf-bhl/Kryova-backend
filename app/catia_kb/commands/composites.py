"""Composites Design, Design for Manufacturing, and Engineering Design.

Command names here were taken from the Dassault Composites Design documentation
rather than reconstructed: EOP, MEOP, EEOP, ITP, Limit Contour, Zones Group,
Plies Group, Material Excess, Core Sampling, Stack-Up File and Solid From Zones
are the vendor's own terms, and every one of them is something a composites
engineer will type expecting to be understood.

The workflow this section models is the real one and it runs in one direction:
support surface, rosette and material catalogue first; then preliminary design
by zone to settle thickness; then plies, cores and cut pieces; then
producibility, which is where a design that was geometrically fine turns out to
be undrapable; then flattening and export. A ply that fails producibility is not
fixed by moving the ply -- it is fixed by a dart, a splice, or a re-oriented
rosette, and saying so is the difference between a useful answer and a shrug.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_CPD = "composites_design"
_CPM = "composites_manufacturing"
_CPE = "composites_engineering"


_DETAILED: list[Entry] = [
    command(
        "Composites Parameters",
        workbench=_CPD,
        toolbar="Composites Parameters",
        aliases=(
            "composites parameters", "composite parameters", "parametres composites",
            "material catalogue", "ply material", "set up composites",
        ),
        summary="The part-level composites setup: the material catalogue, the ply thickness law, the allowed directions and the tolerances. Nothing else in the workbench works until this exists.",
        menu="Insert > Composites Parameters",
        fields=(
            "Materials -- linked from a composites material catalogue (.catalog), each with a cured ply thickness",
            "Directions -- the allowed fibre orientations, usually 0 / 45 / -45 / 90",
            "Draping tolerances -- the warp and weft deviation limits producibility is judged against",
            "Core materials and their thickness handling",
        ),
        needs=("A composites material catalogue reachable through the search order",),
        failures=(
            "Plies come out at the wrong thickness because the catalogue's cured thickness was not set for the material actually used",
            "A direction the design needs is not in the allowed list, so the ply cannot be created",
        ),
        fixes=("Import the material table from the site's Excel/catalogue rather than typing values per part",),
        licence="P3 -- Composites Design 3 (CPD)",
        see_also=("composites_design.rosette", "workflow.composite"),
    ),
    command(
        "Rosette",
        workbench=_CPD,
        toolbar="Preliminary Design",
        aliases=("rosette", "rosettes", "fibre origin", "fiber orientation origin", "0 degree direction", "reference direction"),
        summary="The axis system that defines what 0 degrees means on the part; every ply orientation is measured from it.",
        menu="Insert > Rosette",
        needs=("An axis system or a set of reference directions on the support surface",),
        failures=(
            "One rosette on a doubly-curved part makes fibre angles drift away from the intended orientation at the extremes -- the producibility check is what reveals it",
            "A rosette placed without regard to the load path produces a laminate that is nominally correct and structurally wrong",
        ),
        fixes=(
            "Use several rosettes across a large or curved part and assign each zone the one nearest its region",
            "Re-orient the rosette when producibility reports high warp/weft deviation, before touching the ply boundaries",
        ),
        aerospace="On a wing skin the rosette normally follows the spar direction rather than the aircraft axis, so 0 degrees means \"spanwise\" everywhere the ply is laid.",
        licence="P3 -- CPD",
        see_also=("composites_manufacturing.producibility",),
    ),
    command(
        "Zone",
        workbench=_CPD,
        toolbar="Preliminary Design",
        aliases=("zone", "zones", "define a zone", "thickness zone", "zone based design", "zone de composite"),
        summary="A region of the support surface with one laminate: a stacking of directions and counts, giving a thickness.",
        menu="Insert > Zone",
        fields=(
            "Contour -- the boundary on the support surface",
            "Rosette -- which direction reference applies here",
            "Laminate / Stacking -- the sequence of plies by material and direction",
            "Draping strategy",
        ),
        failures=("Adjacent zones with incompatible stackings cannot be reconciled by a transition zone; the ply drop-off has nowhere to go",),
        fixes=("Design the transition first when two zones differ by many plies; a drop-off ratio is a structural rule, not a modelling preference",),
        aerospace="Preliminary design settles the thickness map before any individual ply exists. That is the stage where the ply drop-off ratio and the ramp rate get agreed, and both are stress requirements.",
        licence="P3 -- CPD",
        see_also=("composites_design.transition_zone", "aero.ply_drop_off"),
    ),
    command(
        "Producibility",
        workbench=_CPM,
        toolbar="Producibility",
        aliases=(
            "producibility", "draping", "drapability", "fiber simulation", "fibre simulation",
            "can this ply be laid", "warp weft deviation", "simulation de drapage",
        ),
        summary="Simulates laying the ply onto the surface and reports where the fibres deviate beyond tolerance, so the design can be fixed before the tool is cut.",
        menu="Insert > Producibility",
        fields=(
            "Seed point / seed curve -- where the lay-up starts, which changes the whole result",
            "Draping strategy",
            "Warp and weft deviation limits, from Composites Parameters",
            "Darts -- cuts that relieve the deviation",
            "Splices -- joins where one piece of material cannot cover the ply",
        ),
        needs=("A ply with a contour and a direction, on a support surface",),
        failures=(
            "Deviation exceeds tolerance over part of the ply -- the material physically cannot lie down there without wrinkling",
            "Moving the seed point changes the answer completely, which is correct behaviour and surprises people",
        ),
        fixes=(
            "Add a dart, add a splice, re-orient the rosette, or split the ply -- in that order of preference",
            "Do not fix it by loosening the tolerance; the tolerance came from the process",
        ),
        aerospace="This is the gate between a laminate that satisfies the stress office and one the shop can actually lay. AFP and ATL have their own steering-radius limits on top of it.",
        licence="P2 -- Composites Design for Manufacturing (CPM)",
        see_also=("composites_manufacturing.flattening", "aero.afp"),
    ),
]


_PRELIMINARY = bulk(
    """
Preliminary Design | preliminary design, create preliminary design, conception preliminaire
Zones Group | zones group, group of zones, groupe de zones
Transition Zone | transition zone, transition, zone de transition, ramp between zones
Constant Thickness | constant thickness, uniform thickness zone
Variable Thickness | variable thickness, tapered zone
Stacking | stacking, stack, empilement, layup sequence
Virtual Stacking | virtual stacking, virtual stack
Laminate | laminate, laminate definition, stratifie
Import a Laminate | import a laminate, laminate import, import stacking
Sequence | sequence, ply sequence, sequence de plis
Solid From Zones | solid from zones, create a solid from zones, solide a partir des zones
Stack-Up File From Zones | stack-up file from zones, stackup from zones
""",
    workbench=_CPD,
    toolbar="Preliminary Design",
    licence="P3 -- Composites Design 3 (CPD)",
)

_DETAILED_DESIGN = bulk(
    """
Ply | ply, plies, pli, single ply, create a ply
Plies Group | plies group, group of plies, groupe de plis
Plies From Zones | plies from zones, generate plies from zones
Plies Manually | plies manually, create plies manually, manual ply
Core | core, honeycomb core, core material, ame
Core Sampling | core sampling, sample the core
Cut Piece | cut piece, cut pieces, piece de coupe, kit piece
Ply Explode | ply explode, explode plies
Ply Split | ply split, split a ply
Edge of Part | edge of part, eop, edge of the part
MEOP | meop, manufacturing edge of part
EEOP | eeop, engineering edge of part
Limit Contour | limit contour, contour limite, boundary contour
Contour | contour, ply contour, ply boundary
Drop-off | drop-off, drop off, ply drop, ply termination, degressivite
Ramp Support | ramp support, ramp, support de rampe
Skin Swap | skin swap, swap the skin, change support surface
Material Excess | material excess, excess material, sur-longueur
ITP | itp, intermediate tool position, interlaminar tool position
Stack-Up File From Plies | stack-up file from plies, stackup from plies
""",
    workbench=_CPD,
    toolbar="Detailed Design",
    licence="P3 -- CPD",
)

_MANUFACTURING = bulk(
    """
Flattening | flattening, flatten a ply, flat pattern of a ply, mise a plat d un pli
Flat Pattern Export | flat pattern export, export the flat pattern, export flattened plies
Dart | dart, darts, pince, relief cut in a ply
Splice | splice, splices, join two pieces of material, raccord de pli
Ply Data Export | ply data export, export ply data, xml ply export
Laser Projection Export | laser projection, laser projection file, ply projection
Manufacturing Document | manufacturing document, ply book, creer un document de fabrication
Manufacturing Process | manufacturing process, create a manufacturing process
Export Data | export data, composites export
""",
    workbench=_CPM,
    toolbar="Manufacturing",
    licence="P2 -- Composites Design for Manufacturing (CPM)",
    aerospace="The ply book and the flat patterns are the shop deliverable; the laser projection file is what puts the ply outline on the tool for hand layup.",
)

_INSPECTION = bulk(
    """
Ply Table | ply table, ply list, tableau des plis
Numerical Analysis | numerical analysis, composites numerical analysis
Graphical Analysis | graphical analysis, composites graphical analysis
Interference (Composites) | composites interference, ply interference
Top Surface Generation | top surface, generate the top surface, surface superieure
Solid Generation | solid generation, generate the composite solid
Ply Exchange | ply exchange, export to abaqus, export to nastran, fibersim exchange, ply xml
Composites Grid Design | composites grid, grid design, iso-grid, ortho-grid, stiffened panel grid
""",
    workbench=_CPE,
    toolbar="Analysis / Exchange",
    licence="P2 -- Composites Engineering (CPE)",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_PRELIMINARY,
    *_DETAILED_DESIGN,
    *_MANUFACTURING,
    *_INSPECTION,
]

SECTION = Section("composites", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
