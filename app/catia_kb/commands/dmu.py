"""Digital Mockup: Navigator, Space Analysis, Kinematics, Fitting, Optimizer.

The joint list in DMU Kinematics is written out in full because choosing the
wrong joint is the commonest kinematics failure and it presents as "the
mechanism will not move" or "it has too many degrees of freedom" rather than as
an error. A mechanism needs exactly as many commands as it has degrees of
freedom before it can be simulated, and that arithmetic is what the DOF readout
in the mechanism dialog is telling you.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_NAV = "dmu_navigator"
_SPA = "dmu_space_analysis"
_KIN = "dmu_kinematics"
_FIT = "dmu_fitting"
_OPT = "dmu_optimizer"


_DETAILED: list[Entry] = [
    command(
        "Interference",
        workbench=_SPA,
        toolbar="Space Analysis",
        aliases=(
            "interference", "clash", "clash detection", "collision", "interference check",
            "analyse d interference", "penetration", "check for clashes", "clearance check",
        ),
        summary="Computes clash, contact and clearance between components and stores the result as a reviewable, filterable list.",
        menu="Analyze > Compute Clash, or Insert > Interference",
        icon="two overlapping solids with a red intersection region",
        fields=(
            "Type -- Contact + Clash, Clearance + Contact + Clash (with a clearance value), Authorized penetration",
            "Selection -- Between all components, Inside one selection, Selection against all, Between two selections",
            "Results -- each conflict as Clash, Contact or Clearance, with penetration depth and the intersection curve or volume",
            "Filters -- by status (Relevant, Irrelevant), by type, by rule",
            "Save/export -- to a .CATProduct of results, an XML or an HTML report",
        ),
        needs=("Two or more components loaded, at least in visualisation mode",),
        failures=(
            "Thousands of contacts reported because every fastener touches its hole by design",
            "A clash is missed because one component was in cache/visualisation mode with a coarse CGR -- the CGR sag decides the accuracy of the check",
            "Results are computed against the *current* positions, so a mechanism has to be posed before the check means anything",
        ),
        fixes=(
            "Set contacts to Irrelevant once reviewed, and save the results so the next run only shows what changed",
            "Regenerate CGRs at a finer sag, or run the final check in design mode, before signing anything off",
        ),
        alternatives=("Assembly Design's Compute Clash for a quick look; DMU Space Engineering Assistant for rule-based checking at scale",),
        aerospace="A zone-by-zone clash campaign is how an airframe mockup is signed off; the saved result set and the Irrelevant flag are what make it repeatable between builds rather than a fresh argument each time.",
        licence="P1 SP1 / P2 SPA",
        see_also=("dmu_space_engineering_assistant", "setting.cache_management"),
    ),
    command(
        "Sectioning",
        workbench=_SPA,
        toolbar="Space Analysis",
        aliases=("sectioning", "section", "cut plane", "slice", "section plane", "coupe", "3d section", "cross section dmu"),
        summary="A live section plane, slice or box through the mockup, with a 2D view of the cut and optional filling.",
        menu="Insert > Sectioning",
        fields=(
            "Definition -- Plane, Slice, Box",
            "Positioning -- by the compass, by coordinates, normal to a face, through three points",
            "Behavior -- Freeze, Volume cut, Clipping",
            "Section fill -- hatches the cut faces",
            "Result -- the 2D section window, exportable to a drawing",
        ),
        failures=("The section shows nothing because it is outside the loaded components' bounding box",),
        licence="P1/P2",
    ),
    command(
        "Revolute Joint",
        workbench=_KIN,
        toolbar="Kinematics Joints",
        aliases=("revolute joint", "revolute", "hinge", "pin joint", "pivot", "liaison pivot", "rotation joint"),
        summary="One rotational degree of freedom about a shared axis, with the two parts held coincident along it.",
        menu="Insert > New Joint > Revolute",
        fields=(
            "Line 1 / Line 2 -- the axes that become coaxial",
            "Plane 1 / Plane 2 -- the planes that stay coincident, which is what removes the axial slide",
            "Angle driven -- makes this joint a command",
        ),
        needs=("A mechanism, and one part already fixed",),
        failures=(
            "Chosen instead of a cylindrical joint, it removes an axial freedom the real mechanism has",
            "The mechanism cannot be simulated because commands do not equal degrees of freedom",
        ),
        fixes=(
            "Read the \"can be simulated\" line in the Mechanism dialog: it states the DOF remaining and how many commands exist",
            "Use Cylindrical when the part must also slide along the axis",
        ),
        alternatives=("Cylindrical, Prismatic, Spherical, Planar, Rigid, Point Curve, Slide Curve, Roll Curve, Point Surface, Universal, CV, Gear, Rack, Cable, Screw",),
        licence="P2 -- DMU Kinematics (KIN)",
        see_also=("dmu_kinematics.fix_part", "dmu_kinematics.simulation_with_laws"),
    ),
]


_NAVIGATOR = bulk(
    """
Fly Mode | fly, fly mode, flythrough, walkthrough, navigation, se deplacer
Walk Mode | walk, walk mode, walkthrough mode
Examine Mode | examine, examine mode, orbit
Viewpoint | viewpoint, saved viewpoint, point de vue
Annotated View | annotated view, 3d annotated view, vue annotee
Markup | markup, 2d markup, redlining, annotation de revue
Hyperlink (DMU) | dmu hyperlink, link to a document
Group | group, dmu group, grouping components
Magnifier | magnifier, loupe, zoom window
Turntable | turntable, turn table, rotate continuously
Enhanced Scene | enhanced scene, scene, scenes, saved position scene
Publish (DMU) | publish, dmu publish, publish a review
""",
    workbench=_NAV,
    toolbar="DMU Navigator",
    licence="P1 DN1 / P2 DMN",
)

_SPACE = bulk(
    """
Distance and Band Analysis | distance and band analysis, minimum distance, band analysis, distance entre
3D Compare | 3d compare, compare two versions, comparaison 3d, geometry compare
Thickness Analysis (DMU) | dmu thickness, wall thickness analysis
Silhouette | silhouette, silhouette extraction
Fastener Group | fastener, fastener group, dmu fastener
Measure Between (DMU) | dmu measure between, measure in the mockup
Measure Item (DMU) | dmu measure item
Section Fill | section fill, fill the section, hachurage de coupe
Snap to Section | snap section, freeze section
""",
    workbench=_SPA,
    toolbar="Space Analysis / Measure",
    licence="P1 SP1 / P2 SPA",
)

_KINEMATICS = bulk(
    """
Mechanism | mechanism, new mechanism, mecanisme
Fix Part | fix part, fixed part, piece fixe, ground part
Prismatic Joint | prismatic joint, prismatic, slider, sliding joint, liaison glissiere
Cylindrical Joint | cylindrical joint, cylindrical, rotate and slide, liaison pivot glissant
Spherical Joint | spherical joint, spherical, ball joint, liaison rotule
Planar Joint | planar joint, planar, liaison appui plan
Rigid Joint | rigid joint, rigid, weld joint, liaison encastrement
Point Curve Joint | point curve joint, point on curve, liaison ponctuelle sur courbe
Slide Curve Joint | slide curve joint, curve on curve sliding
Roll Curve Joint | roll curve joint, rolling curve
Point Surface Joint | point surface joint, point on surface
Universal Joint | universal joint, u joint, cardan, hooke joint
CV Joint | cv joint, constant velocity joint, homocinetique
Gear Joint | gear joint, gear, engrenage, gear ratio
Rack Joint | rack joint, rack and pinion, pignon cremaillere
Cable Joint | cable joint, cable, pulley relation
Screw Joint | screw joint, screw, lead screw, vis
Command | command, joint command, commande, driven joint
Degrees of Freedom (Kinematics) | kinematics dof, remaining degrees of freedom, can this be simulated
Simulation with Commands | simulation with commands, simulate, simulation
Simulation with Laws | simulation with laws, law driven simulation, time law, loi
Replay | replay, replay a simulation, rejeu
Swept Volume | swept volume, volume balaye, envelope of motion
Trace | trace, trace a point, trajectory, trajectoire
Mechanism Dressup | mechanism dressup, dress up the mechanism
Clash Detection During Simulation | clash during simulation, collision during motion, detection de collision
Speed and Acceleration | speed and acceleration, velocity sensor, vitesse
""",
    workbench=_KIN,
    toolbar="DMU Kinematics",
    licence="P2 -- DMU Kinematics Simulator (KIN)",
)

_FITTING = bulk(
    """
Shuttle | shuttle, navette, moving group
Track | track, path, trajectoire de montage
Smooth | smooth, smooth a track, lisser une trajectoire
Automatic Path Finder | automatic path finder, auto path, find a path, recherche automatique de chemin
Sequence | sequence, assembly sequence, sequencement
Clash Aware Path | clash aware path, collision free path
Maintainability Study | maintainability, maintenance study, removal path, depose
""",
    workbench=_FIT,
    toolbar="DMU Fitting",
    licence="P2 -- DMU Fitting Simulator (FIT)",
)

_OPTIMIZER = bulk(
    """
Simplification | simplification, simplify, simplifier
Wrapping | wrapping, wrap, envelope, enveloppe
Silhouette (Optimizer) | silhouette optimizer, silhouette volume
Offset (Optimizer) | offset volume, offset a component
Space Reservation | space reservation, reserved volume, volume de reservation
Thickness (Optimizer) | thickness optimizer, thickness volume
""",
    workbench=_OPT,
    toolbar="DMU Optimizer",
    licence="P2 -- DMU Optimizer (DMO)",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_NAVIGATOR,
    *_SPACE,
    *_KINEMATICS,
    *_FITTING,
    *_OPTIMIZER,
]

SECTION = Section("dmu", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
