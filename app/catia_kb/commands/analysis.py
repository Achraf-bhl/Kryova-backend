"""Analysis & Simulation: GPS, GAS, ELFINI, Advanced Meshing Tools.

Kryova runs its own linear static solver, so this section exists to answer
questions about CATIA's analysis workbenches rather than to drive them -- and
the most useful thing it can carry is the set of results that are *wrong for a
reason a beginner cannot see*: the stress singularity at a re-entrant corner
that refines to infinity, the tetra mesh through a thin skin, the clamp that
over-stiffens a joint, the missing connection that lets two parts pass through
each other.

Those are recorded on the commands that produce them, not as general advice,
because "check your mesh" is not an answer and "the peak is at a sharp internal
corner, so refine it twice and watch whether the value converges or climbs" is.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_GPS = "gps"
_GAS = "gas"
_EST = "elfini"
_AMT = "advanced_meshing_tools"


_DETAILED: list[Entry] = [
    command(
        "Clamp",
        workbench=_GPS,
        toolbar="Restraints",
        aliases=("clamp", "fixed support", "encastrement", "fully fixed", "restraint", "fix a face", "built in"),
        summary="Fixes every degree of freedom on the selected faces, edges or vertices.",
        menu="Insert > Restraints > Clamp",
        needs=("A mesh part and at least one geometric selection",),
        failures=(
            "A clamp on a face that is really a bolted joint over-stiffens the model and moves the peak stress into the clamped edge, where it is an artefact",
            "Clamping a single vertex or edge produces an infinite stress at that point by construction",
            "Under-restrained models fail to solve at all: \"the model is not sufficiently restrained\", which is a singular stiffness matrix, not a CATIA bug",
        ),
        fixes=(
            "Model a bolted joint as a virtual bolt-tightening connection on the bearing area, not as a clamp on the whole face",
            "Use Isostatic Restraint when the loads are self-equilibrating and only rigid-body motion needs removing",
            "Ignore stress reported *inside* a clamped region; it is a boundary-condition artefact",
        ),
        alternatives=("Surface Slider, Ball Join, Pivot, Sliding Pivot, User-defined Restraint, Isostatic Restraint",),
        licence="P1/P2 -- GPS",
        see_also=("gps.static_case_solution", "diagnostic.stress_singularity"),
    ),
    command(
        "Static Case Solution",
        workbench=_GPS,
        toolbar="Compute",
        aliases=("static case", "static analysis", "compute", "solve", "run the analysis", "cas statique", "linear static"),
        summary="Solves the linear static case and produces the displacement and stress fields.",
        menu="Insert > Static Case, then Compute",
        fields=(
            "Compute -- All, Mesh Only, or a selected object",
            "Solution method -- Gauss (direct), Gradient (iterative), Auto",
            "Estimated memory and disk, shown before it runs",
        ),
        needs=("A mesh, a material, at least one restraint and at least one load",),
        failures=(
            "\"Singular stiffness matrix\" / \"the model is not sufficiently restrained\" -- a rigid body mode remains",
            "The solve runs out of disk in the CATTemp directory on a large model",
            "The result is elastic-only: any reported stress above yield is a linear extrapolation, not a prediction",
        ),
        fixes=(
            "Add the missing restraint, or use Isostatic Restraint to remove exactly the six rigid-body modes",
            "Point CATTemp at a disk with room; the estimate in the dialog is honest, use it",
        ),
        licence="P1/P2",
        see_also=("gps.clamp", "gps.precision_error_estimate"),
    ),
    command(
        "Precision / Error Estimate",
        workbench=_GPS,
        toolbar="Analysis Results",
        aliases=(
            "precision", "error estimate", "estimated local error", "global error", "erreur estimee",
            "is my mesh good enough", "mesh convergence", "energy error",
        ),
        summary="The estimated energy error per element, which is how you tell whether the mesh is fine enough where it matters.",
        menu="Insert > Analysis Results > Precision",
        fields=("Global estimated error rate, as a percentage", "Local error image, per element"),
        failures=(
            "A low global error hides a high local error at exactly the feature under investigation -- the global number is an average",
        ),
        fixes=(
            "Read the local error image, refine only where it is high, and re-solve; a uniform refinement costs everything and answers nothing",
            "If the peak stress keeps climbing as the mesh refines and never settles, it is a singularity, not a result",
        ),
        licence="P1/P2",
        see_also=("diagnostic.stress_singularity", "advanced_meshing_tools.local_mesh_size"),
    ),
    command(
        "Fastened Connection",
        workbench=_GAS,
        toolbar="Connections",
        aliases=("fastened connection", "fastened", "bonded contact", "glued", "connexion fixe", "tie two parts"),
        summary="Ties two meshed parts together at coincident faces, so they behave as one continuous body.",
        menu="Insert > Connection Properties > Fastened Connection",
        needs=("An assembly constraint or an Analysis Connection between the two parts",),
        failures=(
            "No connection at all: the parts are meshed independently, share no nodes, and pass through each other under load with no warning",
            "Fastened where the real joint can separate -- it carries tension a bolted lap joint cannot",
        ),
        fixes=(
            "Use Contact Connection where the surfaces can separate, and Bolt Tightening where a preload matters",
            "Check the deformed shape before reading any stress: two parts intersecting is the tell",
        ),
        alternatives=("Contact, Bolt Tightening, Rigid, Smooth, Virtual Bolt, Virtual Spring Bolt, Welding, Pressure Fitting, Node-to-node",),
        licence="P2 -- GAS",
        see_also=("gas.contact_connection",),
    ),
]


_RESTRAINTS = bulk(
    """
Isostatic Restraint | isostatic restraint, isostatic, statically determinate restraint, remove rigid body modes
Surface Slider | surface slider, slider, roller support, appui plan
Ball Join | ball join, ball joint restraint, spherical support, rotule
Pivot | pivot, pivot restraint, hinge support
Sliding Pivot | sliding pivot, sliding hinge
User-defined Restraint | user-defined restraint, custom restraint, choose degrees of freedom
Enforced Displacement | enforced displacement, prescribed displacement, imposed displacement, deplacement impose
""",
    workbench=_GPS,
    toolbar="Restraints",
)

_LOADS = bulk(
    """
Pressure | pressure, pressure load, pression
Distributed Force | distributed force, force, resultant force, force repartie
Moment | moment, distributed moment, couple, torque
Bearing Load | bearing load, bearing, pin load, charge de palier
Imported Force | imported force, force from a file
Imported Moment | imported moment
Acceleration | acceleration, gravity, gravity load, body force, pesanteur
Rotation Force | rotation force, centrifugal, rotational body force
Line Force Density | line force density, force per unit length
Surface Force Density | surface force density, traction, force per unit area
Volume Force Density | volume force density, body force density
Force Density | force density
Temperature Field | temperature field, thermal load, imported temperature
""",
    workbench=_GPS,
    toolbar="Loads",
)

_MASSES = bulk(
    """
Distributed Mass | distributed mass, added mass, lumped mass, masse repartie
Line Mass Density | line mass density, mass per unit length
Surface Mass Density | surface mass density, mass per unit area
Non-structural Mass | non structural mass, nsm, equipment mass
""",
    workbench=_GPS,
    toolbar="Masses",
)

_CONNECTIONS = bulk(
    """
Contact Connection | contact connection, contact, separation allowed, non linear contact, connexion de contact
Bolt Tightening Connection | bolt tightening, preload, bolt preload, serrage de boulon
Rigid Connection | rigid connection, rigid, rbe2
Smooth Connection | smooth connection, smooth, rbe3, distributing coupling
Virtual Bolt Tightening Connection | virtual bolt, virtual bolt tightening, bolt without geometry
Virtual Spring Bolt Tightening Connection | virtual spring bolt, spring bolt
Spot Welding Connection | spot welding connection, spot weld analysis, point de soudure
Seam Welding Connection | seam welding connection, seam weld analysis
Surface Welding Connection | surface welding connection
Pressure Fitting Connection | pressure fitting, interference fit, shrink fit, frettage
Node to Node Connection | node to node connection, node to node
Analysis Connection | analysis connection, general analysis connection, connection without an assembly constraint
""",
    workbench=_GAS,
    toolbar="Connection Properties",
    licence="P2 -- GAS",
)

_VIRTUAL = bulk(
    """
Rigid Virtual Part | rigid virtual part, rigid virtual, piece virtuelle rigide
Smooth Virtual Part | smooth virtual part, smooth virtual
Contact Virtual Part | contact virtual part
Rigid Spring Virtual Part | rigid spring virtual part, spring virtual part
Smooth Spring Virtual Part | smooth spring virtual part
Periodicity Condition | periodicity condition, cyclic symmetry
""",
    workbench=_GPS,
    toolbar="Virtual Parts",
)

_SOLUTIONS = bulk(
    """
Frequency Case | frequency case, modal analysis, natural frequency, eigenvalue, modes propres, vibration
Buckling Case | buckling case, buckling, linear buckling, flambage, critical load factor
Harmonic Dynamic Response | harmonic dynamic response, harmonic response, frequency response
Transient Dynamic Response | transient dynamic response, transient response, time history
Combined Case | combined case, load combination, superposition
Modulation | modulation, excitation, load excitation set
Damping | damping, modal damping, amortissement
""",
    workbench=_EST,
    toolbar="Analysis Case",
    licence="P2 -- ELFINI (EST) / Dynamic Response (GDY)",
)

_RESULTS = bulk(
    """
Von Mises Stress | von mises, von mises stress, equivalent stress, contrainte de von mises, vm stress
Displacement | displacement, deformation, deplacement, translational displacement
Principal Stress | principal stress, principal stress directions, contrainte principale
Deformation | deformation image, deformed shape, deformee
Local Sensor | local sensor, sensor, capteur, probe a value
Global Sensor | global sensor, global reaction, reaction force, energy sensor
Cut Plane Analysis | cut plane analysis, section the result, plan de coupe des resultats
Animate | animate, animate the result, animation
Image Extrema | image extrema, extrema, find the maximum, locate the peak
Report Generation | report, html report, analysis report, generer un rapport
Adaptivity | adaptivity, adaptive refinement, global adaptivity, adaptivite
""",
    workbench=_GPS,
    toolbar="Analysis Results / Analysis Tools",
)

_MESHING = bulk(
    """
OCTREE Tetrahedron Mesher | octree tetrahedron mesher, tetra mesh, octree tetra, 3d mesh, maillage tetraedrique
OCTREE Triangle Mesher | octree triangle mesher, triangle mesh, surface mesh, 2d mesh
Advancing Front Surface Mesher | advancing front, surface mesher, quad mesh, mailleur surfacique
Beam Mesher | beam mesher, 1d mesh, beam elements, poutres
Local Mesh Size | local mesh size, local size, refine locally, taille locale
Local Mesh Sag | local mesh sag, sag, local sag, fleche
Mesh Quality Analysis | mesh quality, quality analysis, aspect ratio, jacobian, skewness, element quality
Element Type | element type, linear or parabolic, tet4 tet10, first order second order
Mesh Part Transition | mesh transition, connect two mesh parts, transition
Nodes and Elements | nodes and elements, node group, element group
Free Edges | free edges, check free edges, aretes libres
Group by Neighborhood | group by neighborhood, group, groupe
""",
    workbench=_AMT,
    toolbar="Meshing Methods / Mesh Specification",
    licence="P2 -- Advanced Meshing Tools (AMT)",
)

_EXPORT = bulk(
    """
Export to Nastran | export nastran, bdf, nastran deck, export to nastran
Export to Abaqus | export abaqus, inp, abaqus input file
Export to ANSYS | export ansys, cdb
Export to Patran | export patran
External Storage | external storage, catanalysiscomputations, catanalysisresults, result files
""",
    workbench=_EST,
    toolbar="External Solvers",
    licence="P2 -- ELFINI (EST)",
)

_OPTIMIZATION = bulk(
    """
Optimization | optimization, optimisation, optimize, minimize mass
Free Parameters | free parameters, design variables, parametres libres
Constraints (Optimization) | optimization constraints, design constraints
Objective | objective, goal, target function
Simulated Annealing | simulated annealing, annealing algorithm, recuit simule
Gradient Algorithm | gradient algorithm, conjugate gradient
Design of Experiments | design of experiments, doe, plan d experiences
Results Table | optimization results table, results table
""",
    workbench="product_engineering_optimizer",
    toolbar="Optimization",
    licence="P2 -- Product Engineering Optimizer (PEO)",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_RESTRAINTS,
    *_LOADS,
    *_MASSES,
    *_CONNECTIONS,
    *_VIRTUAL,
    *_SOLUTIONS,
    *_RESULTS,
    *_MESHING,
    *_EXPORT,
    *_OPTIMIZATION,
]

SECTION = Section("analysis", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
