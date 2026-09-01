"""Aerospace domain vocabulary: what the words mean and what they imply in CAD.

The target user is an airframe engineer, and airframe vocabulary is where a
general-purpose assistant is least reliable: `frame`, `rib`, `spar`, `stringer`
and `longeron` are all "a stiffening member" to a model that has not been told
otherwise, and confusing them produces an answer that is fluent and useless.

Each term therefore records not just what it is but what it means for the model
-- which workbench it belongs in, what drives its geometry, and what constraint
governs it. "A rib is a transverse member in a wing" is a definition; "a rib is
an ASL part whose web sits on a station plane and whose flanges follow the OML,
so it is re-lofted rather than re-modelled when the OML moves" is an answer.

Coordinates come first because everything else is positioned by them.
"""

from __future__ import annotations

from app.catia_kb.types import Disambiguation, Kind, Section, bulk, entry

_T = Kind.TERM


_COORDINATES = [
    entry(
        "aero.station_coordinates",
        "STA / BL / WL aircraft coordinates",
        _T,
        aliases=(
            "station", "sta", "fuselage station", "fs", "body station", "bs", "buttock line", "bl",
            "butt line", "waterline", "wl", "wing station", "ws", "aircraft coordinates",
            "station number", "rib station", "frame station",
        ),
        summary="The aircraft-level coordinate convention: Station along the fuselage, Buttock Line laterally from the centreline, Waterline vertically.",
        fields=(
            "STA / FS -- distance aft along the fuselage from a datum ahead of the nose, so every value is positive",
            "BL -- distance left or right of the aircraft centreline, signed or suffixed L/R",
            "WL -- height above a datum below the aircraft, again chosen so values stay positive",
            "WS -- wing station, measured along the wing rather than the fuselage",
            "Units are usually inches on US programmes and millimetres on European ones -- this is a programme convention, not a CATIA setting",
        ),
        failures=(
            "Modelling in aircraft coordinates but positioning parts by typed offsets, so a datum change means editing every part",
            "Mixing a component-local origin with aircraft coordinates in one assembly without an explicit transform",
        ),
        fixes=(
            "Create the station, buttock and water planes once in a skeleton part, publish them, and position everything by reference to the published planes",
            "Name the planes by their station number, so `STA_1420` is what a part is coincident with, not `Plane.17`",
        ),
        aerospace="This is the reason airframe work is skeleton-driven. Frames sit on station planes, stringers run along buttock lines, and floor beams sit at a waterline; if the planes are published, a datum shift is one edit.",
        see_also=("practice.skeleton", "assembly_design.publication", "workflow.airframe_structure"),
    ),
]


_AIRFRAME = bulk(
    """
Frame | frame, fuselage frame, former, cadre | Transverse fuselage member on a station plane; ASL part, web on the station plane, flanges following the skin
Bulkhead | bulkhead, pressure bulkhead, cloison | A frame that also closes the section; a pressure bulkhead carries cabin differential and is a primary structure item
Longeron | longeron, longitudinal member | Heavy longitudinal fuselage member carrying bending; fewer and larger than stringers
Stringer | stringer, lisse, stiffener run, longitudinal stiffener | Light longitudinal stiffener under the skin; runs along a buttock line, joggled where it crosses a frame
Skin | skin, outer skin, panel, revetement | The surface itself; in CATIA an ASL web or a composite ply set lying on the OML
Doubler | doubler, doubler plate, reinforcement, renfort | Local thickness added around a cutout or a joint; joggled so the OML stays flush
Shear Tie | shear tie, shear clip | Connects a frame to the skin, carrying shear rather than direct load
Clip | clip, frame clip, skin clip | Small ASL part attaching a stringer or skin to a frame
Cleat | cleat, angle cleat | Angle fitting joining two members
Intercostal | intercostal, intercostal member | Short member spanning between two frames or ribs
Rib | rib, wing rib, nervure, costilla | Transverse member in a wing or control surface; web on a station plane, flanges on the OML
Spar | spar, front spar, rear spar, longeron d aile | Primary spanwise beam; spar cap plus web
Spar Cap | spar cap, cap, semelle | The flange of a spar, carrying bending
Web | web, shear web, ame | The vertical plate carrying shear between caps or flanges
Stiffener | stiffener, raidisseur | Any secondary member added to raise buckling allowables
Joggle | joggle, jog, joggling, offset step | A step of one thickness over a defined runout so a lapped part stays flush; an ASL feature, not an SMD one
Splice | splice, splice plate, joint, eclisse | A joint between two lengths of structure, with its own fastener pattern
Butt Strap | butt strap, strap | Backing strap across a butt joint
Lightening Hole | lightening hole, weight relief hole, flanged hole | Hole cut to remove weight, usually flanged to restore stiffness
Mouse Hole | mouse hole, rat hole, snipe | Small relief cutout where two members cross
Fail-Safe Strap | fail safe strap, crack stopper, tear strap | Strap limiting crack growth in a skin
Wing Box | wing box, torque box, caisson | The closed cell between front and rear spars carrying torsion and bending
Keel Beam | keel beam, keel | Longitudinal beam in the lower fuselage
Floor Beam | floor beam, cross beam, traverse | Transverse beam carrying the cabin floor
Seat Track | seat track, rail de siege | Extruded rail in the floor taking seat fittings
Fairing | fairing, carenage | Non-structural aerodynamic cover
Nacelle | nacelle, engine nacelle | Engine cowling structure
Pylon | pylon, engine pylon, mat | Structure attaching the engine to the wing or fuselage
Empennage | empennage, tail, tail assembly | Horizontal and vertical stabiliser assembly
Leading Edge | leading edge, le, bord d attaque
Trailing Edge | trailing edge, te, bord de fuite
Slat | slat, leading edge slat, bec
Flap | flap, trailing edge flap, volet
Aileron | aileron
Spoiler | spoiler, airbrake, aerofrein
Elevator | elevator, gouverne de profondeur
Rudder | rudder, gouverne de direction
Hinge Line | hinge line, control surface hinge, ligne de charniere | Axis a control surface rotates about; drives the kinematics and the seal geometry
Outer Mould Line | outer mould line, oml, outer moldline, aerodynamic surface | The aerodynamic surface; the master geometry everything else is built from
Inner Mould Line | inner mould line, iml | The inner surface, usually OML offset by the skin thickness
""",
    kind=_T,
    prefix="aero",
    toolbar="Airframe structure",
    aerospace="Airframe structural vocabulary.",
)

_FASTENING = bulk(
    """
Rivet | rivet, solid rivet, riveting
Hi-Lok | hi-lok, hilok, hi lok
Hi-Lite | hi-lite, hilite
Lockbolt | lockbolt, lock bolt
Blind Fastener | blind fastener, blind rivet, cherrymax, one side access
Edge Margin | edge margin, edge distance, e/d, marge au bord | Minimum distance from a hole centre to the part edge; a structural rule, usually 2D minimum
Pitch | fastener pitch, hole pitch, spacing, pas | Distance between adjacent fastener centres along a row
Hole Class | hole class, fit class, interference fit, clearance fit
Wet Install | wet install, sealant install, wet assembly
Fastener Pattern | fastener pattern, rivet pattern, hole pattern, fastener row
Coordination Hole | coordination hole, coord hole, pilot hole, trou de coordination
Drill Plate | drill plate, drill jig, gabarit de percage
Shimming | shim, shimming, liquid shim, solid shim, cale | Filling the gap at an interface rather than forcing the parts together
Determinant Assembly | determinant assembly, da, self locating assembly
Key Characteristic | key characteristic, kc, critical characteristic
Tolerance Stack-Up | tolerance stack up, stack up, chaine de cotes, tolerance chain
""",
    kind=_T,
    prefix="aero",
    toolbar="Fastening and assembly",
)

_COMPOSITES_TERMS = bulk(
    """
Prepreg | prepreg, pre-impregnated, pre preg
Tape | tape, unidirectional tape, ud tape
Fabric | fabric, woven fabric, cloth, tissu
Warp | warp, warp direction, chaine
Weft | weft, fill direction, weft direction, trame
Stacking Sequence | stacking sequence, layup sequence, 0 45 -45 90, ply schedule
Symmetric Laminate | symmetric laminate, symmetric layup
Balanced Laminate | balanced laminate, balanced layup
Ply Drop-off | ply drop off, drop off ratio, ply termination ratio | How many plies may end over what length; a stress rule, commonly no steeper than 1 in 20
Ramp Rate | ramp rate, taper rate, ply ramp
Core | core, honeycomb, honeycomb core, nomex, nid d abeille
Potting | potting, potted insert, edge potting
Co-cure | co-cure, cocure, co curing
Co-bond | co-bond, cobond, co bonding
AFP | afp, automated fiber placement, automated fibre placement
ATL | atl, automated tape laying
Hand Layup | hand layup, hand lay-up, manual layup, drapage manuel
Autoclave | autoclave, autoclave cure
Mandrel | mandrel, layup tool, layup mandrel, moule
Springback | springback, spring back, tool compensation, retour elastique
""",
    kind=_T,
    prefix="aero",
    toolbar="Composites",
)

_PROGRAMME = bulk(
    """
Zoning | zoning, zone breakdown, ata chapter, ata zone
Systems Segregation | segregation, systems segregation, separation rules, hydraulic electrical separation
Routing Corridor | routing corridor, corridor, systems corridor
Clearance Envelope | clearance envelope, clearance volume
Maintenance Access | maintenance access, access envelope, removal path, depose
Weight and Balance | weight and balance, mass properties discipline, cg envelope, centre of gravity envelope, mass target
Weight Roll-Up | weight roll up, mass roll up, weight per zone
Non-Geometric Mass | non geometric mass, nsm, non structural mass, added mass
Effectivity | effectivity, applicability, line number, tail number, msn
Configuration | configuration, options, variants, customer configuration
ICD | icd, interface control document, interface control
Build-to-Package | build to package, btp, work package
Work Share | work share, workshare, partner package
AS9100 | as9100, quality standard
AS9102 | as9102, first article inspection, fai, first article
CS-25 | cs-25, cs25, easa cs-25, 14 cfr part 25, far 25, certification basis
ITAR | itar, ear, export control, export controlled data
Drawing Release | drawing release, release, stamping, signed off
Concurrent Engineering | concurrent engineering, global partner, co-design
""",
    kind=_T,
    prefix="aero",
    toolbar="Programme practice",
)

_STANDARDS = bulk(
    """
ASME Y14.5 | asme y14.5, y14.5, gd&t standard, 2009, 2018
ASME Y14.41 | asme y14.41, y14.41, digital product definition
ISO 1101 | iso 1101, geometrical tolerancing standard
ISO 8015 | iso 8015, fundamental tolerancing principle
ISO 2768 | iso 2768, general tolerances
ISO 5459 | iso 5459, datums and datum systems
ISO GPS | iso gps, geometrical product specification
ISO 2553 | iso 2553, welding symbols standard
AWS A2.4 | aws a2.4, welding symbols aws
""",
    kind=_T,
    prefix="standard",
    toolbar="Standards",
)


_DISAMBIGUATIONS = [
    Disambiguation(
        term="rib",
        aliases=("nervure",),
        options=(
            "Part Design Rib -- the command that sweeps a profile along a centre curve to add material",
            "An aircraft rib -- a transverse structural member in a wing or control surface, modelled in Aerospace Sheet Metal or as a machined part",
        ),
        guidance="Context decides: 'add a rib to this bracket' is the command; 'the rib at WS 210' is structure. When it is structure, the command is almost never Part Design's Rib.",
    ),
    Disambiguation(
        term="web",
        aliases=(),
        options=(
            "Aerospace Sheet Metal Web -- the base face of an ASL part, lying on a support surface",
            "A shear web -- the structural element between two caps",
        ),
        guidance="They usually coincide: the ASL Web command is how a shear web gets modelled. Say both.",
    ),
    Disambiguation(
        term="flange",
        aliases=("bord tombe",),
        options=(
            "Sheet Metal Design Flange -- a bend on a straight edge of a flat wall",
            "Aerospace Sheet Metal Flange -- follows a curved edge on a support surface, with an angle that can vary",
            "A pipe flange -- a Piping Design catalogue component",
            "The flange of a structural section -- the horizontal part of an I or C beam",
        ),
        guidance="If the edge is curved or the part sits on a lofted surface, it is the ASL Flange. If it is a bolted joint on a pipe, it is a catalogue part.",
    ),
    Disambiguation(
        term="station",
        aliases=("sta",),
        options=(
            "A fuselage station -- a position along the aircraft, and usually a plane named for it",
            "A workstation -- the machine CATIA runs on",
        ),
        guidance="In an airframe conversation it is always the coordinate. Say so and give the plane naming convention.",
    ),
]


ENTRIES = [*_COORDINATES, *_AIRFRAME, *_FASTENING, *_COMPOSITES_TERMS, *_PROGRAMME, *_STANDARDS]

SECTION = Section("aerospace", ENTRIES, _DISAMBIGUATIONS)

__all__ = ["ENTRIES", "SECTION"]
