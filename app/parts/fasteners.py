"""A first-party set of ISO metric fasteners, with the data that makes them usable.

Hex bolts (ISO 4014), hex nuts (ISO 4032) and plain washers (ISO 7089) in M5 to
M16, in property classes 8.8, 10.9 and 12.9.

**Why first-party rather than BOLTS.** The master plan names
[BOLTS](https://boltsparts.github.io/) as the base for standard parts. It was
checked on 2026-09-06 and it is not usable as a dependency here, for three
reasons and only the third is fatal:

1. it is not on PyPI under any name — installing it means vendoring a git
   checkout, and its `bolttools` loader is Python-2 era;
2. the repository has had no commit since May 2023;
3. **it carries dimensions and nothing else.** `data/hex.blt` gives `d1`, `k`,
   `s`, `e`, `l`, `pitch` — everything needed to draw a bolt, and no proof load,
   no property class, no stress area, no mass, no torque. What a bought-in part
   has to contribute here is precisely the half BOLTS does not have.

So the geometry BOLTS would provide is the cheap half, and it is what is written
out below. `app.parts.catalogue.PartSource` is the seam it would arrive through
if that changes: a `.blt` importer implements `PartSource` and the rest of the
system does not learn about it. Its data is LGPL 2.1+, which an importer must
respect; nothing is redistributed here.

**Engineering data and where it comes from.** Dimensions are the basic sizes in
ISO 4014/4032/7089 and are `SPECIFIED`. Strengths, stress areas and proof loads
are ISO 898-1/898-2 and are `SPECIFIED` — they are minima a supplier is held to,
not typical values. Mass is computed from nominal geometry and is `ESTIMATED`.
Tightening torque is **not stored at all**: it is not a property of the bolt.

**Units.** mm, N, mm², N·mm for torque (matching `MomentLoad.moment_n_mm`), kg
for mass. Every number below is already in those units and still goes through
`transcribe`, so there is one way properties are written and one place to look
for a conversion.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.solve.materials import Property, Source, SourceKind, Status, transcribe

from .types import Designation, PartKind, StandardPart, indexed

# --- sources ----------------------------------------------------------------

ISO_4014 = Source(
    citation="ISO 4014:2011 — Hexagon head bolts, product grades A and B",
    kind=SourceKind.STANDARD,
    year=2011,
)
ISO_4032 = Source(
    citation="ISO 4032:2012 — Hexagon regular nuts (style 1), product grades A and B",
    kind=SourceKind.STANDARD,
    year=2012,
)
ISO_7089 = Source(
    citation="ISO 7089:2000 — Plain washers, normal series, product grade A",
    kind=SourceKind.STANDARD,
    year=2000,
)
ISO_898_1 = Source(
    citation="ISO 898-1:2013 — Mechanical properties of fasteners, carbon and alloy steel bolts",
    kind=SourceKind.STANDARD,
    year=2013,
    note="Property class values are guaranteed minima, not typical values.",
)
ISO_898_2 = Source(
    citation="ISO 898-2:2012 — Nuts with specified property classes, coarse thread",
    kind=SourceKind.STANDARD,
    year=2012,
)
ISO_261 = Source(
    citation="ISO 261:1998 — ISO general purpose metric screw threads, coarse pitch series",
    kind=SourceKind.STANDARD,
    year=1998,
)
ISO_273 = Source(
    citation="ISO 273:1979 — Fasteners, clearance holes for bolts and screws (medium series)",
    kind=SourceKind.STANDARD,
    year=1979,
)
VDI_2230 = Source(
    citation="VDI 2230 Part 1:2015 — Systematic calculation of highly stressed bolted joints",
    kind=SourceKind.STANDARD,
    year=2015,
    note=(
        "The closed-form assembly equations, section 5.4. A full VDI 2230 joint "
        "calculation needs the joint's stiffness and load introduction as well."
    ),
)
_GEOMETRIC = Source(
    citation="Computed from the nominal dimensions of the governing standard",
    kind=SourceKind.DERIVED,
)


# --- thread data ------------------------------------------------------------


@dataclass(frozen=True)
class Thread:
    """One ISO metric coarse thread.

    `pitch_diameter_mm` and `minor_diameter_mm` are the ISO 68-1 60° profile's
    exact basic values, ``d - 0.6495 P`` and ``d - 1.2269 P``, rather than
    tabulated roundings — the torque model differentiates between them and a
    rounded d2 moves the answer by about a per cent.
    """

    size: str
    diameter_mm: float
    pitch_mm: float
    #: Tensile stress area, ISO 898-1. Tabulated rather than recomputed, because
    #: the table is what a supplier's certificate is written against.
    stress_area_mm2: float

    @property
    def pitch_diameter_mm(self) -> float:
        return self.diameter_mm - 0.6495 * self.pitch_mm

    @property
    def minor_diameter_mm(self) -> float:
        return self.diameter_mm - 1.2269 * self.pitch_mm


THREADS: Final[Mapping[str, Thread]] = {
    thread.size: thread
    for thread in [
        Thread("M5", 5.0, 0.8, 14.2),
        Thread("M6", 6.0, 1.0, 20.1),
        Thread("M8", 8.0, 1.25, 36.6),
        Thread("M10", 10.0, 1.5, 58.0),
        Thread("M12", 12.0, 1.75, 84.3),
        Thread("M16", 16.0, 2.0, 157.0),
    ]
}

#: ISO 273 medium-series clearance hole, per size. The medium series is the one
#: a general assembly uses; fine and coarse exist and are not held here.
CLEARANCE_HOLE_MM: Final[Mapping[str, float]] = {
    "M5": 5.5,
    "M6": 6.6,
    "M8": 9.0,
    "M10": 11.0,
    "M12": 13.5,
    "M16": 17.5,
}


@dataclass(frozen=True)
class PropertyClass:
    """An ISO 898-1 property class: the three stresses that define a bolt's steel.

    Values below M16 and at M16 are the same in ISO 898-1; above M16, class 8.8
    steps up to 830/660/600 MPa. The catalogue stops at M16 so the single column
    is correct throughout, and `_check_range` refuses to build outside it rather
    than quietly applying the wrong column.
    """

    name: str
    tensile_strength_mpa: float
    yield_strength_mpa: float
    proof_stress_mpa: float


PROPERTY_CLASSES: Final[Mapping[str, PropertyClass]] = {
    grade.name: grade
    for grade in [
        PropertyClass("8.8", 800.0, 640.0, 580.0),
        PropertyClass("10.9", 1040.0, 940.0, 830.0),
        PropertyClass("12.9", 1220.0, 1100.0, 970.0),
    ]
}

#: ISO 898-2 nut property class 8: the proof stress a style-1 nut is tested at.
_NUT_PROOF_STRESS_MPA: Final = {"8": 800.0, "10": 1040.0}

#: Density used for computed fastener masses. Plain carbon and alloy fastener
#: steels sit between 7830 and 7870 kg/m3; 7850 is the conventional figure and
#: the resulting mass is `ESTIMATED` anyway.
FASTENER_STEEL_DENSITY_KG_M3: Final = 7850.0

#: ISO 4014 hexagon head bolt: (across flats s, head height k, bearing diameter
#: dw, thread length b for lengths up to 125 mm).
_HEX_HEAD: Final[Mapping[str, tuple[float, float, float, float]]] = {
    "M5": (8.0, 3.5, 6.9, 16.0),
    "M6": (10.0, 4.0, 8.9, 18.0),
    "M8": (13.0, 5.3, 11.6, 22.0),
    "M10": (16.0, 6.4, 14.6, 26.0),
    "M12": (18.0, 7.5, 16.6, 30.0),
    "M16": (24.0, 10.0, 22.5, 38.0),
}

#: ISO 4032 hexagon nut: (across flats s, height m).
_HEX_NUT: Final[Mapping[str, tuple[float, float]]] = {
    "M5": (8.0, 4.7),
    "M6": (10.0, 5.2),
    "M8": (13.0, 6.8),
    "M10": (16.0, 8.4),
    "M12": (18.0, 10.8),
    "M16": (24.0, 14.8),
}

#: ISO 7089 plain washer: (inside d1, outside d2, thickness h).
_WASHER: Final[Mapping[str, tuple[float, float, float]]] = {
    "M5": (5.3, 10.0, 1.0),
    "M6": (6.4, 12.0, 1.6),
    "M8": (8.4, 16.0, 1.6),
    "M10": (10.5, 20.0, 2.0),
    "M12": (13.0, 24.0, 2.5),
    "M16": (17.0, 30.0, 3.0),
}

#: Lengths stocked per size. Every one is longer than the thread length b, so
#: the bolt has a plain shank and ISO 4014 applies; a bolt shorter than that is
#: fully threaded and is an ISO 4017, which is a different part.
_BOLT_LENGTHS: Final[Mapping[str, tuple[float, ...]]] = {
    "M5": (20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0),
    "M6": (25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 60.0),
    "M8": (30.0, 35.0, 40.0, 45.0, 50.0, 60.0, 70.0, 80.0),
    "M10": (35.0, 40.0, 45.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0),
    "M12": (40.0, 45.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0),
    "M16": (50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 120.0),
}

#: Why a bolt record carries no tightening torque. Quoted verbatim by `require`.
_NO_STORED_TORQUE = (
    "Tightening torque is a property of the joint, not of the bolt: it depends on "
    "the thread and head friction coefficients, which vary by a factor of three "
    "between a dry zinc-plated bolt and a lubricated one, and on the utilisation "
    "chosen at assembly. Call app.parts.fasteners.tightening_torque() with a "
    "friction coefficient you can defend."
)
_NO_FATIGUE = (
    "No fatigue rating is held. Fastener endurance depends on the rolled-thread "
    "condition, the preload and the joint's load introduction, and no free source "
    "gives it at a quality worth signing off."
)


def _hex_prism_volume_mm3(across_flats_mm: float, height_mm: float) -> float:
    """Volume of a regular hexagonal prism given its width across flats."""
    return (math.sqrt(3.0) / 2.0) * across_flats_mm**2 * height_mm


def _mass_kg(volume_mm3: float) -> float:
    """Steel mass in kg from a volume in mm3. The mm-to-m boundary, once."""
    return volume_mm3 * 1e-9 * FASTENER_STEEL_DENSITY_KG_M3


def _check_range(size: str, grade: str | None = None) -> Thread:
    if size not in THREADS:
        raise ValueError(
            f"{size!r} is not a size this catalogue holds. Available: "
            f"{', '.join(THREADS)}. Sizes above M16 need the second ISO 898-1 "
            f"strength column, which is not written down here."
        )
    if grade is not None and grade not in PROPERTY_CLASSES:
        raise ValueError(
            f"{grade!r} is not a property class this catalogue holds. Available: "
            f"{', '.join(PROPERTY_CLASSES)}."
        )
    return THREADS[size]


# --- builders ---------------------------------------------------------------


def hex_bolt(size: str, length_mm: float, grade: str = "8.8") -> StandardPart:
    """An ISO 4014 hexagon head bolt, with its ISO 898-1 strength data.

    The clamp length range is derived, not tabulated, and the derivation is on
    the properties: the shortest stack is the plain shank length ``l - b``,
    because a shank inside the nut cannot be tightened, and the longest is
    ``l - m - 2P``, leaving the nut fully engaged with two pitches of thread
    protruding. `m` is the ISO 4032 nut of the same size — a different nut moves
    the upper bound, which is why the note says which one was assumed.
    """
    thread = _check_range(size, grade)
    property_class = PROPERTY_CLASSES[grade]
    across_flats, head_height, bearing_diameter, thread_length = _HEX_HEAD[size]
    nut_height = _HEX_NUT[size][1]

    if length_mm <= thread_length:
        raise ValueError(
            f"An {size}x{length_mm:g} bolt is shorter than ISO 4014's thread length "
            f"b = {thread_length:g} mm, so it is fully threaded — that is an ISO 4017 "
            f"screw, not an ISO 4014 bolt. Use a length above {thread_length:g} mm."
        )

    def spec(name: str, value: float, unit: str, note: str = "", src: Source = ISO_4014):
        return transcribe(name, value, unit, status=Status.SPECIFIED, source=src, note=note)

    geometry = [
        spec("nominal_diameter_mm", thread.diameter_mm, "mm", src=ISO_261),
        spec("thread_pitch_mm", thread.pitch_mm, "mm", src=ISO_261),
        transcribe(
            "pitch_diameter_mm", thread.pitch_diameter_mm, "mm",
            status=Status.ESTIMATED, source=ISO_261,
            note="Basic d2 = d - 0.6495 P from the ISO 68-1 profile, not a tolerance class.",
        ),
        transcribe(
            "minor_diameter_mm", thread.minor_diameter_mm, "mm",
            status=Status.ESTIMATED, source=ISO_261,
            note="Basic d3 = d - 1.2269 P from the ISO 68-1 profile.",
        ),
        spec("length_mm", length_mm, "mm"),
        spec("thread_length_mm", thread_length, "mm", note="b, for lengths up to 125 mm."),
        spec("width_across_flats_mm", across_flats, "mm"),
        transcribe(
            "width_across_corners_mm", across_flats * 2.0 / math.sqrt(3.0), "mm",
            status=Status.ESTIMATED, source=_GEOMETRIC,
            note=(
                "2s/sqrt(3) for an ideal hexagon. The standard's minimum e is a little "
                "smaller because the corners are chamfered; use this for envelope, not "
                "for spanner selection."
            ),
        ),
        spec("head_height_mm", head_height, "mm"),
        spec("head_bearing_diameter_mm", bearing_diameter, "mm", note="dw, minimum."),
        spec("clearance_hole_mm", CLEARANCE_HOLE_MM[size], "mm", src=ISO_273),
    ]

    min_clamp = length_mm - thread_length
    max_clamp = length_mm - nut_height - 2.0 * thread.pitch_mm
    volume = (
        _hex_prism_volume_mm3(across_flats, head_height)
        + math.pi / 4.0 * thread.diameter_mm**2 * (length_mm - thread_length)
        + thread.stress_area_mm2 * thread_length
    )

    engineering = [
        spec("tensile_stress_area_mm2", thread.stress_area_mm2, "mm2", src=ISO_898_1),
        spec("yield_strength_mpa", property_class.yield_strength_mpa, "MPa", src=ISO_898_1),
        spec(
            "ultimate_tensile_strength_mpa",
            property_class.tensile_strength_mpa,
            "MPa",
            src=ISO_898_1,
        ),
        spec(
            "proof_load_n",
            property_class.proof_stress_mpa * thread.stress_area_mm2,
            "N",
            note="Sp x As, which is how ISO 898-1 tabulates it.",
            src=ISO_898_1,
        ),
        spec(
            "min_breaking_load_n",
            property_class.tensile_strength_mpa * thread.stress_area_mm2,
            "N",
            note="Rm,min x As.",
            src=ISO_898_1,
        ),
        transcribe(
            "mass_kg", _mass_kg(volume), "kg",
            status=Status.ESTIMATED, source=_GEOMETRIC,
            note=(
                "Hexagon head prism + plain shank at d + threaded length at the tensile "
                f"stress area, at {FASTENER_STEEL_DENSITY_KG_M3:g} kg/m3. Ignores the head "
                "chamfer and the thread's helix, so it reads within a few per cent of a "
                "catalogue mass rather than exactly."
            ),
        ),
    ]

    absent = {"tightening_torque_n_mm": _NO_STORED_TORQUE, "fatigue_strength_mpa": _NO_FATIGUE}
    if max_clamp >= min_clamp:
        engineering += [
            transcribe(
                "min_clamp_length_mm", min_clamp, "mm",
                status=Status.ESTIMATED, source=_GEOMETRIC,
                note=(
                    "l - b: the plain shank must not enter the nut. Shorter stacks need "
                    "a shorter bolt or a fully threaded ISO 4017 screw."
                ),
            ),
            transcribe(
                "max_clamp_length_mm", max_clamp, "mm",
                status=Status.ESTIMATED, source=_GEOMETRIC,
                note=(
                    f"l - m - 2P with an ISO 4032 nut (m = {nut_height:g} mm), leaving the "
                    "nut fully engaged and two pitches protruding. A thicker nut or a "
                    "washer under it reduces this; washers count as part of the stack."
                ),
            ),
        ]
    else:  # pragma: no cover - unreachable for the stocked lengths, kept as the honest branch
        absent["min_clamp_length_mm"] = absent["max_clamp_length_mm"] = (
            f"l - b = {min_clamp:g} mm exceeds l - m - 2P = {max_clamp:g} mm, so there is "
            f"no stack this bolt can clamp with an ISO 4032 nut. It is the wrong length."
        )

    return StandardPart(
        designation=Designation("ISO 4014", size, length_mm, grade),
        kind=PartKind.BOLT,
        display_name=f"Hexagon head bolt {size}x{length_mm:g} ISO 4014 {grade}",
        source=ISO_4014,
        geometry={prop.name: prop for prop in geometry},
        engineering={prop.name: prop for prop in engineering},
        absent=absent,
        material_note=(
            f"Property class {grade} steel per ISO 898-1 — specified by class, not by "
            f"alloy, so it has no slug in the material library. Do not substitute a "
            f"structural steel's yield strength for it."
        ),
        notes=(
            "Dimensions are the standard's basic sizes, not toleranced.",
            "Coarse thread. A fine-pitch bolt of the same size has a larger stress area.",
        ),
    )


def hex_nut(size: str, grade: str = "8") -> StandardPart:
    """An ISO 4032 style-1 hexagon nut with its ISO 898-2 proof load.

    A nut is graded to match or exceed the bolt it runs on — class 8 for an 8.8
    bolt, class 10 for a 10.9. A nut weaker than its bolt strips instead of the
    bolt breaking, which is a failure mode with no warning.
    """
    thread = _check_range(size)
    if grade not in _NUT_PROOF_STRESS_MPA:
        raise ValueError(
            f"{grade!r} is not a nut property class this catalogue holds. Available: "
            f"{', '.join(_NUT_PROOF_STRESS_MPA)}. Class 8 suits an 8.8 bolt and class "
            f"10 a 10.9; a nut below its bolt's class strips before the bolt breaks."
        )
    across_flats, height = _HEX_NUT[size]
    proof_stress = _NUT_PROOF_STRESS_MPA[grade]

    hole_area = math.pi / 4.0 * ((thread.diameter_mm + thread.minor_diameter_mm) / 2.0) ** 2
    volume = _hex_prism_volume_mm3(across_flats, height) - hole_area * height

    def spec(name: str, value: float, unit: str, note: str = "", src: Source = ISO_4032):
        return transcribe(name, value, unit, status=Status.SPECIFIED, source=src, note=note)

    geometry = [
        spec("nominal_diameter_mm", thread.diameter_mm, "mm", src=ISO_261),
        spec("thread_pitch_mm", thread.pitch_mm, "mm", src=ISO_261),
        spec("width_across_flats_mm", across_flats, "mm"),
        spec("nut_height_mm", height, "mm", note="m."),
    ]
    engineering = [
        spec("tensile_stress_area_mm2", thread.stress_area_mm2, "mm2", src=ISO_898_1),
        spec(
            "proof_load_n",
            proof_stress * thread.stress_area_mm2,
            "N",
            note="Nut proof stress x the mating bolt's stress area.",
            src=ISO_898_2,
        ),
        transcribe(
            "mass_kg", _mass_kg(volume), "kg",
            status=Status.ESTIMATED, source=_GEOMETRIC,
            note=(
                "Hexagon prism less a bore at the mean of the nominal and minor thread "
                f"diameters, at {FASTENER_STEEL_DENSITY_KG_M3:g} kg/m3. Ignores the "
                "chamfers, so it reads a few per cent heavy."
            ),
        ),
    ]

    return StandardPart(
        designation=Designation("ISO 4032", size, None, grade),
        kind=PartKind.NUT,
        display_name=f"Hexagon nut {size} ISO 4032 class {grade}",
        source=ISO_4032,
        geometry={prop.name: prop for prop in geometry},
        engineering={prop.name: prop for prop in engineering},
        absent={"tightening_torque_n_mm": _NO_STORED_TORQUE},
        material_note=f"Nut property class {grade} per ISO 898-2.",
        notes=("Style 1. A style-2 or a thin (ISO 4035) nut has a different height.",),
    )


def plain_washer(size: str) -> StandardPart:
    """An ISO 7089 plain washer, normal series, 200 HV.

    The honest example of a part with no engineering data: ISO 7089 specifies
    dimensions and a hardness class and says nothing about load. A washer's job
    — spreading the head's bearing pressure over a soft clamped member — is a
    property of the joint, so this record carries no proof load and explains
    why, instead of implying it can carry the bolt's.
    """
    _check_range(size)
    inner, outer, thickness = _WASHER[size]
    volume = math.pi / 4.0 * (outer**2 - inner**2) * thickness

    def spec(name: str, value: float, unit: str, note: str = ""):
        return transcribe(name, value, unit, status=Status.SPECIFIED, source=ISO_7089, note=note)

    geometry = [
        spec("inner_diameter_mm", inner, "mm", note="d1."),
        spec("outer_diameter_mm", outer, "mm", note="d2."),
        spec("thickness_mm", thickness, "mm", note="h."),
    ]
    engineering = [
        transcribe(
            "mass_kg", _mass_kg(volume), "kg",
            status=Status.ESTIMATED, source=_GEOMETRIC,
            note=f"Annulus at {FASTENER_STEEL_DENSITY_KG_M3:g} kg/m3, ignoring the edge break.",
        ),
    ]

    return StandardPart(
        designation=Designation("ISO 7089", size, None, "200HV"),
        kind=PartKind.WASHER,
        display_name=f"Plain washer {size} ISO 7089 200 HV",
        source=ISO_7089,
        geometry={prop.name: prop for prop in geometry},
        engineering={prop.name: prop for prop in engineering},
        absent={
            "proof_load_n": (
                "ISO 7089 specifies dimensions and a hardness class only; it states no "
                "load rating, and a washer's bearing capacity depends on the clamped "
                "member's material, not on the washer."
            ),
            "yield_strength_mpa": (
                "Only a hardness class (200 HV) is specified. Converting hardness to "
                "yield strength is a correlation, not a measurement, and is not done here."
            ),
        },
        material_note="Steel, hardness class 200 HV per ISO 7089.",
        notes=("Normal series. Large (ISO 7093) and small (ISO 7092) series differ in d2.",),
    )


# --- derived joint quantities ----------------------------------------------
#
# Not stored on the parts, because neither is a property of a part.


def assembly_preload(
    bolt: StandardPart, *, thread_friction: float = 0.14, utilisation: float = 0.9
) -> Property:
    """The bolt tension to aim for at assembly, in N. VDI 2230 closed form.

    ``F_M = nu * As * Rp0.2 / sqrt(1 + 3 * k_tau^2)`` with
    ``k_tau = 1.5 * (d2/ds) * (P / (pi * d2) + 1.155 * mu)``. The divisor is the
    torsion the tightening itself puts into the shank: tightening to a torque
    twists the bolt as well as stretching it, so the tension it can safely reach
    is lower than the pure-tension limit — for M8 8.8 at mu = 0.14, 18.1 kN
    against a 21.1 kN pure-tension figure.

    `ESTIMATED`, and it must stay so. `utilisation` (0.9 is the usual assembly
    figure) and the friction coefficient are both choices, not measurements, and
    a real joint calculation also needs its stiffness and load introduction.
    """
    if bolt.kind is not PartKind.BOLT:
        raise ValueError(
            f"{bolt.designation} is a {bolt.kind}, not a bolt, so it has no assembly "
            f"preload. Preload is the tension left in the *bolt* of a joint; pass the "
            f"bolt, and see app.parts.catalogue.nut_for for the nut that runs on it."
        )
    if not 0.0 < thread_friction <= 0.5:
        raise ValueError(
            f"A thread friction coefficient of {thread_friction} is outside anything "
            f"real. Assembled fasteners run about 0.08 lubricated, 0.14 as-received "
            f"zinc plated, 0.20 dry and dirty."
        )
    if not 0.0 < utilisation <= 1.0:
        raise ValueError(
            f"Utilisation is the fraction of the bolt's yield strength used at assembly "
            f"and must lie in (0, 1]; {utilisation} does not. The usual value is 0.9."
        )

    pitch = bolt.require("thread_pitch_mm").value
    d2 = bolt.require("pitch_diameter_mm").value
    d3 = bolt.require("minor_diameter_mm").value
    stress_area = bolt.require("tensile_stress_area_mm2").value
    yield_strength = bolt.require("yield_strength_mpa").value

    ds = math.sqrt(d2 * d3)
    k_tau = 1.5 * (d2 / ds) * (pitch / (math.pi * d2) + 1.155 * thread_friction)
    preload = utilisation * stress_area * yield_strength / math.sqrt(1.0 + 3.0 * k_tau**2)

    return transcribe(
        "assembly_preload_n", preload, "N",
        status=Status.ESTIMATED,
        source=VDI_2230,
        note=(
            f"VDI 2230 closed form at thread friction {thread_friction:g} and "
            f"{utilisation:g} of yield. Both are assembly choices, not measurements: "
            f"halving the friction raises this by about a tenth (M8 8.8, 0.14 to 0.07, "
            f"18.12 kN to 19.75 kN — computed here, pinned in tests/test_parts.py)."
        ),
    )


def tightening_torque(
    bolt: StandardPart,
    *,
    thread_friction: float = 0.14,
    head_friction: float | None = None,
    utilisation: float = 0.9,
) -> Property:
    """The torque that reaches `assembly_preload`, in N·mm. VDI 2230 closed form.

    ``M_A = F_M * (0.16 P + 0.58 d2 mu_G + 0.5 D_Km mu_K)``, where ``D_Km`` is the
    mean bearing diameter under the head, taken as the mean of the head's bearing
    diameter and the clearance hole.

    **Most of this torque never becomes tension.** For M8 8.8 at mu = 0.14 the
    three terms come out 48% friction under the head, 39% thread friction and
    13% the thread helix — so seven eighths of the torque is spent overcoming
    friction (computed here, pinned in `tests/test_parts.py`). That is why the
    result is `ESTIMATED` however precisely it is computed, and why a critical
    joint is tightened by angle or by measuring length rather than by torque.

    `head_friction` defaults to `thread_friction`, which is the usual assumption
    when only one coefficient is quoted.
    """
    head_mu = thread_friction if head_friction is None else head_friction
    if not 0.0 < head_mu <= 0.5:
        raise ValueError(
            f"A head friction coefficient of {head_mu} is outside anything real. Under a "
            f"bolt head expect about 0.08 lubricated to 0.20 dry."
        )

    preload = assembly_preload(
        bolt, thread_friction=thread_friction, utilisation=utilisation
    ).value
    pitch = bolt.require("thread_pitch_mm").value
    d2 = bolt.require("pitch_diameter_mm").value
    bearing = bolt.require("head_bearing_diameter_mm").value
    hole = bolt.require("clearance_hole_mm").value

    mean_bearing_diameter = (bearing + hole) / 2.0
    torque = preload * (
        0.16 * pitch + 0.58 * d2 * thread_friction + 0.5 * mean_bearing_diameter * head_mu
    )

    return transcribe(
        "tightening_torque_n_mm", torque, "N.mm",
        status=Status.ESTIMATED,
        source=VDI_2230,
        note=(
            f"VDI 2230 closed form at thread friction {thread_friction:g}, head friction "
            f"{head_mu:g}, {utilisation:g} of yield, on an ISO 273 medium clearance hole. "
            f"Friction dominates: this figure is not transferable to a different surface "
            f"finish, coating or lubricant."
        ),
    )


def clamp_length_range_mm(bolt: StandardPart) -> tuple[float, float]:
    """The shortest and longest joint stack this bolt can clamp, in mm.

    Refuses by name when the record does not carry the range, rather than
    returning a range nobody derived.
    """
    return (
        bolt.require("min_clamp_length_mm").value,
        bolt.require("max_clamp_length_mm").value,
    )


def clamps(bolt: StandardPart, stack_mm: float) -> bool:
    """Whether this bolt can clamp a stack of that thickness, washers included."""
    low, high = clamp_length_range_mm(bolt)
    return low <= stack_mm <= high


# --- the shipped set --------------------------------------------------------


def _build() -> dict[str, StandardPart]:
    parts: list[StandardPart] = []
    for size, lengths in _BOLT_LENGTHS.items():
        for length in lengths:
            for grade in PROPERTY_CLASSES:
                parts.append(hex_bolt(size, length, grade))
    for size in THREADS:
        parts.extend(hex_nut(size, grade) for grade in _NUT_PROOF_STRESS_MPA)
        parts.append(plain_washer(size))
    return indexed(parts)


#: Every fastener this repository ships, keyed by `Designation.key`.
FASTENERS: Final[Mapping[str, StandardPart]] = _build()
