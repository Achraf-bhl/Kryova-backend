"""Equipment & Systems: electrical, piping, tubing, HVAC, waveguide, structure.

Every routing product in V5 works the same way and the shared vocabulary is
worth stating once: a **specification** (a `.spec` file plus catalogues) decides
what components are legal at a given size and service; a **run** or **route** is
the path; **resolved parts** are the catalogue components CATIA places on that
path; and **connectors** are what make two of them join. A route that will not
accept a component is nearly always a specification problem rather than a
geometry problem, and that is the answer this section exists to give.

The electrical chain is the one with a distinct deliverable at the end: diagram,
then 3D bundle, then flattening to a formboard drawing the harness shop builds
on.
"""

from __future__ import annotations

from app.catia_kb.types import Entry, Section, bulk, command

_EHI = "electrical_harness_installation"
_EHF = "electrical_harness_flattening"
_ELB = "electrical_library"
_EWR = "electrical_wire_routing"
_PIP = "piping_design"
_TUB = "tubing_design"
_HVA = "hvac_design"
_ERGO = "human_builder"


_DETAILED: list[Entry] = [
    command(
        "Bundle Segment",
        workbench=_EHI,
        toolbar="Electrical Harness",
        aliases=(
            "bundle segment", "bundle", "harness segment", "faisceau", "wire bundle",
            "cable bundle", "branch", "route a harness",
        ),
        summary="A physical run of the harness between two points, with a diameter, a bend radius and the wires routed inside it.",
        menu="Insert > Bundle Segment",
        fields=(
            "Definition -- Slack or Bend radius mode",
            "Diameter -- computed from the routed wires, or imposed",
            "Minimum bend radius -- as a ratio of diameter, which the route must respect",
            "Points and supports along the run",
        ),
        needs=("A geometrical bundle in a product, and connection points or supports to route between",),
        failures=(
            "The route violates the minimum bend radius at a support and the segment turns red",
            "The diameter never updates because no wires have actually been routed through it yet",
        ),
        fixes=(
            "Add an intermediate support to spread the bend, or raise the allowed radius if the cable spec permits",
            "Route the wires (Electrical Wire Routing) before judging the bundle diameter",
        ),
        aerospace="Separation rules apply here: a bundle carrying flight-critical signals has a minimum distance from hydraulic and fuel lines, and that is checked in DMU Space Engineering Assistant rather than by eye.",
        licence="P2 -- Electrical Harness Installation (EHI)",
        see_also=("electrical_harness_installation.flatten", "dmu_space_engineering_assistant"),
    ),
    command(
        "Route a Pipe",
        workbench=_PIP,
        toolbar="Piping",
        aliases=("route a pipe", "pipe routing", "route pipe", "run a pipe", "piping route", "cheminement de tuyauterie"),
        summary="Places a run of pipe along a route, resolving fittings, bends and reducers from the piping specification.",
        menu="Insert > Route a Pipe",
        fields=(
            "Line ID -- carries the service, the specification and the insulation",
            "Nominal size and specification",
            "Routing mode -- along a run, point to point, or by offset from geometry",
            "Bend or elbow at direction changes, taken from the spec",
        ),
        needs=("A project resource management setup pointing at the specification and catalogues",),
        failures=(
            "\"No part found in the specification\" -- the size/service combination has no catalogue entry, which is a data problem not a modelling one",
            "The route is built but has no line ID, so nothing downstream (isometrics, BOM) can classify it",
        ),
        fixes=("Check the resolved specification for that Line ID before assuming the geometry is at fault",),
        licence="P2 -- Piping Design (PIP)",
        see_also=("piping_instrumentation_diagrams",),
    ),
]


_ELECTRICAL = bulk(
    """
Electrical Device | electrical device, device, equipement electrique
Connector | connector, electrical connector, connecteur
Contact | contact, electrical contact
Cavity | cavity, connector cavity, cavite
Bundle Connector | bundle connector, harness connector
Geometrical Bundle | geometrical bundle, bundle geometry, faisceau geometrique
Multi-Branchable Document | multi-branchable, branchable document
Protective Covering | protective covering, sleeve, sheath, protection
Support (Electrical) | electrical support, clamp support, harness support, attache
Route a Wire | route a wire, wire routing, cheminer un fil
Wire Definition | wire definition, wire list, definition de fil
Flatten | flatten, flatten the harness, formboard, nailboard, flattening, mise a plat du faisceau
Formboard Drawing | formboard drawing, nailboard drawing, harness drawing, planche a clous
Extract Data | extract data, harness report, wire length report
Connectivity Diagram | connectivity diagram, wiring diagram, schema de cablage
Signal | signal, electrical signal, signal routing
Cableway | cableway, cable tray, cable route, chemin de cables
""",
    workbench=_EHI,
    toolbar="Electrical",
    licence="P2 -- ELB / EWR / EHI / EHF / ECR",
)

_FLUID = bulk(
    """
Run | run, routing run, route path, cheminement
Route a Tube | route a tube, tube routing, tubing route
Route a Duct | route a duct, hvac route, duct routing, gaine
Route a Waveguide | route a waveguide, waveguide route
Place a Part | place a part, place a component, poser un composant
Part in Placement Mode | placement mode, place in a run
Bend | pipe bend, tube bend, cintrage
Reducer | reducer, size change, reduction
Flow Direction | flow direction, sens d ecoulement
Line ID | line id, line identifier, service, specification
Specification | piping specification, spec file, .spec, tubing specification, resolved part
Connector (Fluid) | piping connector, tubing connector, port
Hanger | hanger, pipe support, hanger placement
Insulation | insulation, insulated pipe, calorifuge
Isometric Generation | isometric, piping isometric, isometrie
Design Rules | design rules, routing rules, regles de conception
""",
    workbench=_PIP,
    toolbar="Routing / Placement",
    licence="P2 -- PIP / TUB / HVA / WAV",
)

_SPACE = bulk(
    """
Space Reservation Volume | space reservation volume, reserved space, reservation
Compartment | compartment, compartiment, zone
Access Zone | access zone, access envelope, zone d acces
Segregation Rule | segregation rule, separation rule, regle de segregation
Routing Corridor | routing corridor, corridor, cheminement reserve
""",
    workbench="systems_space_reservation",
    toolbar="Space Reservation",
    licence="P2 -- SSR / CNA",
    aerospace="Zoning, ATA-chapter breakdown and systems segregation are modelled as objects here rather than agreed in a meeting; a corridor claimed early is a corridor that survives detailed design.",
)

_ERGONOMICS = bulk(
    """
Insert a Manikin | insert a manikin, new manikin, create a manikin, inserer un mannequin
Percentile | percentile, 5th percentile, 95th percentile, percentile manikin
Population | population, ansur, nhanes, anthropometric population
Vision Window | vision window, field of view, vision cone, champ de vision
Reach Envelope | reach envelope, reach, enveloppe d atteinte
Inverse Kinematics | inverse kinematics, ik, ik behaviour, posture solving
Carry | carry, manikin carry, attach an object
Walk | walk, manikin walk, trajectoire de marche
Posture Editor | posture editor, edit posture, editeur de posture
Preferred Angles | preferred angles, comfort angles, angles preferentiels
RULA Analysis | rula, rapid upper limb assessment, rula analysis
Lift-Lower Analysis | lift lower, niosh, lifting analysis, snook ciriello
Push-Pull Analysis | push pull, push pull analysis
Biomechanics Single Action | biomechanics, single action analysis, biomecanique
Anthropometry | anthropometry, manikin measurements, anthropometrie
""",
    workbench=_ERGO,
    toolbar="Ergonomics",
    licence="P2 -- HBR / HME / HPA / HAA",
    aerospace="Cockpit reach and vision, cabin egress and maintenance access are all done here; the useful question is nearly always 'can the 5th-percentile technician reach it' rather than 'does it fit'.",
)


ENTRIES: list[Entry] = [
    *_DETAILED,
    *_ELECTRICAL,
    *_FLUID,
    *_SPACE,
    *_ERGONOMICS,
]

SECTION = Section("systems", ENTRIES)

__all__ = ["ENTRIES", "SECTION"]
