"""Machining: the NC products, their operations, and what comes out at the end.

The structure is fixed across every machining workbench and is worth stating
once, because most "the operation will not compute" questions are really "the
Part Operation is incomplete": a **Part Operation** names the machine, the
machining axis system, the design part, the stock and the fixture; a **Program**
under it holds the operations in order; each **operation** needs its geometry,
its tool, its feeds and speeds, and its macros (approach, retract, linking).

The output is not G-code. It is APT or a CLfile, which a **post-processor**
turns into the code a specific controller accepts -- so "CATIA produced the wrong
G-code" is almost always a post-processor question.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_PMG = "prismatic_machining"
_SMG = "surface_machining"
_LMG = "lathe_machining"
_INF = "nc_manufacturing_infrastructure"


_DETAILED: list[Entry] = [
    command(
        "Part Operation",
        workbench=_INF,
        toolbar="Manufacturing Program",
        aliases=(
            "part operation", "setup", "machining setup", "operation d usinage",
            "machining axis system", "set up the machine", "define the machine",
        ),
        summary="The setup: which machine, which machining axis system, which design part, stock and fixture, and the safety plane.",
        menu="Insert > Part Operation",
        fields=(
            "Machine -- 3-axis, 3-axis with rotary table, 5-axis, horizontal/vertical mill, lathe, mill-turn; carries the spindle and post-processor words table",
            "Machining Axis System -- the programme origin; everything is output relative to it",
            "Design Part / Stock / Fixture -- the three geometry roles simulation and remaining-material analysis need",
            "Safety plane / Transition plane",
            "Part Operation options -- tool change point, table centre",
        ),
        needs=("A .CATProcess document",),
        failures=(
            "Operations compute but the simulation shows nothing removed -- no Stock was assigned",
            "The NC output is in the wrong coordinates because the Machining Axis System was left at the document origin",
            "Collision checking reports nothing because no Fixture was assigned",
        ),
        fixes=("Fill all three geometry roles before programming anything; the rest of the workbench assumes them",),
        licence="Included with any machining product",
        see_also=("nc_manufacturing_infrastructure.post_processor", "prismatic_machining.pocketing"),
    ),
    command(
        "Post Processor",
        workbench=_INF,
        toolbar="Output",
        aliases=(
            "post processor", "postprocessor", "post", "g code", "gcode", "nc code",
            "pptable", "post processor words table", "ims", "icam", "cenit", "generate nc code",
        ),
        summary="Translates the machine-independent APT or CLfile into the code one specific controller accepts.",
        menu="Right-click the Program > Generate NC Code Interactively",
        fields=(
            "Output type -- APT source, Clfile, NC code",
            "PP words table (.pptable) -- how CATIA's syntactic words map to the controller's",
            "Post processor engine -- IMS, ICAM, CENIT, or a site-supplied one",
            "NC documentation -- shop floor documentation in HTML",
        ),
        failures=(
            "The G-code is wrong for the controller -- the post-processor or the PP words table is wrong, not the tool path; the tool path is the APT and it is machine-independent",
            "No post-processor is configured, so only APT can be produced",
        ),
        fixes=("Verify the tool path in Replay and Material Removal Simulation *before* posting; if it is right there, the fault is downstream",),
        licence="Machining products; post-processor engines are separately licensed",
        see_also=("format.apt", "nc_manufacturing_infrastructure.tool_path_replay"),
    ),
]


_PRISMATIC = bulk(
    """
Facing | facing, face milling, surfacage
Pocketing | pocketing, pocket milling, poche, 2.5 axis pocket
Profile Contouring | profile contouring, contouring, contournage, profile milling
Curve Following | curve following, follow a curve, suivi de courbe
Groove Milling | groove milling, slot milling, rainurage
Point to Point | point to point, ptp, point to point machining
Drilling | drilling, drill, percage, drill cycle
Spot Drilling | spot drilling, centre drill, spot drill, pointage
Drilling Deep Hole | deep hole drilling, peck drilling, percage profond
Drilling Dwell Delay | drilling dwell, dwell delay
Break Chip | break chip, chip breaking, brise copeaux
Tapping | tapping, tap, taraudage
Reverse Threading | reverse threading, left hand tapping
Thread Milling | thread milling, mill a thread, fraisage de filet
Reaming | reaming, ream, alesage
Boring | boring, bore, alesage a l alesoir
Boring and Chamfering | boring and chamfering
Boring Spindle Stop | boring spindle stop
Back Boring | back boring, back bore
Counterboring | counterboring, counterbore, lamage
Countersinking | countersinking, countersink, fraisurage
Counterdrilling | counterdrilling
T-Slotting | t-slotting, t slot
Circular Milling | circular milling, helical interpolation, interpolation circulaire
Sequential Milling | sequential milling, sequential
Prismatic Rework | prismatic rework, rework area, reprise
""",
    workbench=_PMG,
    toolbar="Machining Operations",
    licence="P1 PG1 / P2 PMG",
)

_SURFACE = bulk(
    """
Roughing | roughing, rough machining, ebauche
Sweeping | sweeping, sweep machining, balayage
Pencil | pencil, pencil machining, crayon
Spiral Milling | spiral milling, spiral
Contour-driven | contour driven, contour-driven machining
Isoparametric Machining | isoparametric machining, isopara machining
Projection Machining | projection machining
ZLevel | zlevel, z level, waterline, constant z
Multi-Axis Curve Machining | multi-axis curve, 5 axis curve machining
Multi-Axis Sweeping | multi-axis sweeping, 5 axis sweeping
Multi-Axis Flank Contouring | flank contouring, flank milling, usinage en flanc
Multi-Axis Tube Machining | tube machining, multi-axis tube
Multi-Axis Drilling | multi-axis drilling, 5 axis drilling
""",
    workbench=_SMG,
    toolbar="Machining Operations",
    licence="P2 SMG / AMG / MMG",
)

_LATHE = bulk(
    """
Rough Turning | rough turning, turning roughing, ebauche de tournage
Finish Turning | finish turning, turning finishing, finition de tournage
Groove Turning | groove turning, grooving, gorge de tournage
Recess Turning | recess turning, recessing
Thread Turning | thread turning, threading, filetage de tournage
Ramp Rough Turning | ramp rough turning, ramp turning
Sequential Turning | sequential turning
Axial Machining on a Lathe | axial machining, drilling on a lathe
""",
    workbench=_LMG,
    toolbar="Machining Operations",
    licence="P1 LG1 / P2 LMG",
)

_ENTITIES = bulk(
    """
Manufacturing Program | manufacturing program, program, programme d usinage
Machine | machine, machine tool, machine editor, machine 3 axes, 5 axis machine, mill turn
Design Part | design part, part to machine, piece a usiner
Stock | stock, raw material, brut
Fixture | fixture, clamp, montage d usinage
Safety Plane | safety plane, plan de securite, clearance plane
Machining Axis System | machining axis system, programme origin, repere d usinage
Tool Change | tool change, change the tool, changement d outil
Machining Feature | machining feature, feature to machine
Machining Pattern | machining pattern, hole pattern for machining
Tool Assembly | tool assembly, tool holder, assemblage d outil
Tool Catalogue | tool catalogue, tool library, catalogue d outils
Insert | insert, cutting insert, plaquette
Tool Compensation | tool compensation, cutter comp, correcteur d outil
Feeds and Speeds | feeds and speeds, feedrate, spindle speed, avance et vitesse
Approach Macro | approach macro, approach, macro d approche
Retract Macro | retract macro, retract, macro de retrait
Linking Macro | linking macro, linking, macro de liaison
Clearance Macro | clearance macro, clearance
Transition Macro | transition macro, transition path
""",
    workbench=_INF,
    toolbar="Manufacturing entities",
)

_VERIFY = bulk(
    """
Tool Path Replay | tool path replay, replay, rejouer le parcours, check the toolpath
Photo Simulation | photo simulation, photo, simulation photo
Video Simulation | video simulation, video, material removal simulation, simulation d enlevement de matiere
Collision Check | collision check, gouge check, check for collisions
Remaining Material Analysis | remaining material, rest material, matiere restante
Machining Time | machining time, cycle time, temps d usinage
Shop Floor Documentation | shop floor documentation, shop docs, documentation d atelier
NC Manufacturing Review | nc manufacturing review, review the program
""",
    workbench=_INF,
    toolbar="Verification",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_PRISMATIC,
    *_SURFACE,
    *_LATHE,
    *_ENTITIES,
    *_VERIFY,
]

SECTION = Section("machining", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
