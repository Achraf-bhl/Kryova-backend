"""The material library: a record with provenance, not a row of numbers.

Phase 12.2. A material property that an engineer is expected to sign off has to
say three things beyond its value: **where it came from** (a standard, a supplier
datasheet, a textbook), **what condition it applies to** (temper, heat treatment,
rolling direction, product form), and **what kind of claim it is** (a
specification limit, a test result, a handbook typical, or something derived).
A bare float says none of that, and two floats from different conditions look
identical.

The master plan's research finding is what shapes this module: the open
materials databases (Materials Project, AFLOW, OQMD, OPTIMADE) are DFT and
atomistic — excellent, and useless for an engineering S-N curve — and the free
engineering sources are partial and licence-varied. So the deliverable is the
**schema, the provenance model and the ingestion boundary**, with a small
curated set behind it. Buying Granta or MatWeb later is then data loading, not
re-architecture.

**A property nobody has a source for is absent, and says so.** It is never
filled with a plausible number. Every material in the curated set below is
missing ``fatigue_strength_mpa`` for exactly that reason: nobody here holds an
S-N curve for these grades, and inventing one is how a part gets signed off and
then breaks. `MaterialRecord.require` names the missing property rather than
returning zero.

**Nothing in the curated set is `MEASURED`.** These are aggregated handbook and
datasheet figures — good enough to design with, not a lot certificate. When a
supplier's test report for an actual batch arrives, it lands as `MEASURED` with
that certificate as its source, and only then.

**Units.** Everything stored here is already in the codebase's mm-N-MPa system:
moduli and strengths in MPa, density in kg/m³, thermal expansion per kelvin.
`transcribe()` is the single boundary where a datasheet in GPa, ksi, psi, g/cm³,
lb/in³ or per-°F becomes those units — it happens there, once, and `Property`
cannot be constructed with a foreign unit at all, because a `Property` carries
no unit field to get wrong: its unit comes from `PROPERTY_UNITS`, keyed on the
property name.

**Back-compatible on purpose.** `MATERIALS` is still `dict[str, Material]` with
the same eight slugs and the same numbers as before, because prompts, tool
schemas, the CATIA dispatcher, the OCCT document operations and a dozen tests
read it. It is now *derived* from `RECORDS` rather than typed out, so the
provenance and the solver's view cannot drift apart.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.solve.types import Material

# ---------------------------------------------------------------------------
# The controlled vocabulary: what a property is called, its unit, its meaning.
# ---------------------------------------------------------------------------
#
# Modelled on `app/kernel/contract.py`: a name that is not in this table cannot
# be recorded, and a name in it has exactly one unit. That is what makes "the
# conversion happens once, at the boundary" enforceable rather than aspirational
# — there is nowhere downstream for a GPa figure to hide.

_QUANTITIES: dict[str, tuple[str, str]] = {
    "youngs_modulus_mpa": ("MPa", "Elastic (Young's) modulus in tension"),
    "poissons_ratio": ("-", "Lateral contraction ratio, dimensionless"),
    "shear_modulus_mpa": ("MPa", "Elastic modulus in shear"),
    "yield_strength_mpa": (
        "MPa",
        "0.2% proof stress, or stress at yield for a polymer",
    ),
    "ultimate_tensile_strength_mpa": ("MPa", "Stress at failure in tension"),
    "compressive_strength_mpa": ("MPa", "Stress at failure in compression"),
    "fatigue_strength_mpa": (
        "MPa",
        "Fully reversed stress amplitude at the cited life; needs an S-N curve behind it",
    ),
    "elongation_at_break_pct": ("%", "Engineering strain at fracture, per cent"),
    "hardness_hv": ("HV", "Vickers hardness"),
    "density_kg_m3": ("kg/m3", "Mass density"),
    "thermal_expansion_per_k": ("1/K", "Coefficient of linear thermal expansion"),
    "thermal_conductivity_w_m_k": ("W/(m.K)", "Thermal conductivity"),
    "specific_heat_j_kg_k": ("J/(kg.K)", "Specific heat capacity"),
    "melting_point_k": ("K", "Melting point, or softening point for a polymer"),
    "max_service_temperature_k": ("K", "Highest temperature the grade is rated for"),
}

#: The material vocabulary, frozen at import. `PROPERTY_UNITS` grows as other
#: packages register their own quantities; this does not, so "which properties
#: could a material have?" stays a question about materials.
MATERIAL_PROPERTIES: Final[tuple[str, ...]] = tuple(_QUANTITIES)

#: quantity name -> (unit, what it means), for every quantity any package has
#: declared. A read-only live view: `register_quantity` is the only way in.
#:
#: It is shared rather than per-package because `Property` is shared. A bolt's
#: proof load and an alloy's yield strength are the same kind of claim — a
#: number, a unit, a status and a source — and giving them two near-identical
#: record types would mean two provenance models that drift.
PROPERTY_UNITS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(_QUANTITIES)


def register_quantity(name: str, unit: str, meaning: str) -> None:
    """Declare a quantity outside the material vocabulary, e.g. for a bought-in part.

    Idempotent for an identical redeclaration, and a refusal for a conflicting
    one — two modules disagreeing about a quantity's unit is the exact failure
    the registry exists to prevent, and it must not be resolved by import order.
    """
    existing = _QUANTITIES.get(name)
    if existing is not None:
        if existing != (unit, meaning):
            raise ValueError(
                f"{name!r} is already declared as {existing[0]!r} ({existing[1]}) and "
                f"cannot be redeclared as {unit!r} ({meaning}). Pick a different name, "
                f"or agree on one unit — a quantity with two units is how a torque in "
                f"N.m gets stored where N.mm is expected."
            )
        return
    _QUANTITIES[name] = (unit, meaning)


#: The properties `to_material()` needs to hand the solver a `Material`. A record
#: missing any of them cannot be solved with, and says which one is missing.
SOLVER_REQUIRED: Final[tuple[str, ...]] = (
    "youngs_modulus_mpa",
    "poissons_ratio",
    "yield_strength_mpa",
    "density_kg_m3",
)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class SourceKind(StrEnum):
    """What sort of document a number came out of.

    The kind matters independently of the citation, because it decides how much
    the number can be leaned on: a standard states a limit the supplier is
    contractually held to, a test report states what one batch actually did, and
    a handbook states what the grade usually does.
    """

    STANDARD = "standard"
    TEST_REPORT = "test-report"
    DATASHEET = "datasheet"
    TEXTBOOK = "textbook"
    DERIVED = "derived"


@dataclass(frozen=True)
class Source:
    """Where a number came from, in enough detail to go and check it.

    `citation` is what a reviewer would type into a search box or pull off a
    shelf — "ISO 898-1:2013 Table 3", not "ISO". Vague citations are the reason
    provenance systems stop being used.
    """

    citation: str
    kind: SourceKind
    #: Publication or issue year, where the source has one. Absent is honest for
    #: a rolling web datasheet; a wrong year is not.
    year: int | None = None
    url: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not self.citation.strip():
            raise ValueError(
                "A source needs a citation someone can follow. Name the standard, "
                "the datasheet or the report — an empty citation is the same as no "
                "provenance at all."
            )

    def __str__(self) -> str:
        return f"{self.citation} ({self.kind})" if self.kind else self.citation

    def to_dict(self) -> dict[str, str | int]:
        out: dict[str, str | int] = {"citation": self.citation, "kind": str(self.kind)}
        if self.year is not None:
            out["year"] = self.year
        if self.url:
            out["url"] = self.url
        if self.note:
            out["note"] = self.note
        return out


class Status(StrEnum):
    """What kind of claim a property value is.

    The master plan names three — measured, typical, estimated. There are four
    here, and the extra one is `SPECIFIED`, because folding it into either
    neighbour is wrong in a way that reaches the part:

    * `SPECIFIED` — a limit a standard requires and a supplier is held to. An
      ISO 898-1 minimum proof stress is not a measurement of anything and it is
      not a typical value either: it is a **floor**, and it is the only kind of
      number you may legitimately size to without a test report.
    * `MEASURED` — a test result for identified material. A mill certificate, a
      coupon test. Also a floor you may size to, for that batch.
    * `TYPICAL` — a handbook or datasheet representative figure. Roughly the
      middle of a population, so about half of real stock is below it. Fine for
      stiffness and mass, **not a design minimum for strength**.
    * `ESTIMATED` — derived by correlation, interpolation or analogy. Must name
      the derivation. Never a design basis.

    `is_design_basis` is that distinction made checkable.
    """

    SPECIFIED = "specified"
    MEASURED = "measured"
    TYPICAL = "typical"
    ESTIMATED = "estimated"

    @property
    def is_design_basis(self) -> bool:
        """Whether a strength allowable may be taken from a value of this status."""
        return self in (Status.SPECIFIED, Status.MEASURED)


@dataclass(frozen=True)
class Condition:
    """The state of the material the numbers apply to.

    6061 in the O temper and 6061-T6 differ by a factor of five in yield
    strength; hot-rolled and cold-drawn 1018 differ by nearly two; a rolled plate
    is not isotropic. A property quoted without its condition is not wrong so
    much as unusable, and this is the field that stops two records from
    different conditions looking like the same material.
    """

    temper: str = ""
    #: Heat treatment or process history — "cold drawn", "annealed", "solution
    #: treated and aged", "as printed, no anneal".
    process: str = ""
    #: For anisotropic stock: "L", "LT", "ST", or a build direction for a print.
    direction: str = ""
    #: Product form — "extruded bar", "sheet 1-6 mm", "injection moulded".
    form: str = ""
    note: str = ""

    def __str__(self) -> str:
        parts = [p for p in (self.temper, self.process, self.direction, self.form) if p]
        return ", ".join(parts) if parts else "unspecified condition"

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("temper", self.temper),
                ("process", self.process),
                ("direction", self.direction),
                ("form", self.form),
                ("note", self.note),
            )
            if value
        }


@dataclass(frozen=True)
class Property:
    """One property value with its unit, its status and its source.

    **There is no unit argument.** The unit is looked up from `PROPERTY_UNITS`
    by name, so a `Property` can only ever hold the canonical mm-N-MPa unit for
    that property. A datasheet in other units goes through `transcribe()`, which
    is the one place a conversion happens.
    """

    name: str
    value: float
    status: Status
    source: Source
    #: How an `ESTIMATED` value was arrived at, or any caveat on the others —
    #: "spread across grades is roughly +/-40%", "interpolated between M12 and M16".
    note: str = ""

    def __post_init__(self) -> None:
        if self.name not in PROPERTY_UNITS:
            close = difflib.get_close_matches(self.name, PROPERTY_UNITS, n=3, cutoff=0.6)
            hint = f" Did you mean {' or '.join(repr(c) for c in close)}?" if close else ""
            raise ValueError(
                f"{self.name!r} is not a property this library knows.{hint} Add it to "
                f"PROPERTY_UNITS with its unit and meaning first — an unlisted name has "
                f"no declared unit, which is how a GPa figure gets stored as MPa."
            )
        if self.status is Status.ESTIMATED and not self.note.strip():
            raise ValueError(
                f"{self.name!r} is estimated, so it must say how it was estimated. "
                f"An unexplained estimate cannot be reviewed, only believed."
            )

    @property
    def unit(self) -> str:
        return PROPERTY_UNITS[self.name][0]

    @property
    def meaning(self) -> str:
        return PROPERTY_UNITS[self.name][1]

    @property
    def is_design_basis(self) -> bool:
        """Whether this value may be used as a strength allowable. See `Status`."""
        return self.status.is_design_basis

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit} ({self.status}, {self.source.citation})"

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "meaning": self.meaning,
            "status": str(self.status),
            "source": self.source.to_dict(),
        }
        if self.note:
            out["note"] = self.note
        return out


# ---------------------------------------------------------------------------
# The unit boundary
# ---------------------------------------------------------------------------
#
# Datasheets are published in whatever the publisher uses: GPa in Europe, ksi
# and Msi in the US, g/cm3 or lb/in3 for density, per-degF for expansion. This
# is the only place in the codebase where any of those becomes mm-N-MPa. Nothing
# downstream converts, and `Property` has no unit field for a foreign unit to
# hide in, so there is exactly one edge to audit.

#: (foreign unit, canonical unit) -> multiplier. Exact conversion factors, not
#: rounded ones: 1 psi is 6894.757293168361 Pa by definition of the pound-force
#: and the inch, and rounding it here is a silent error at the fourth digit of
#: every American datasheet.
_CONVERSIONS: Final[Mapping[tuple[str, str], float]] = {
    ("MPa", "MPa"): 1.0,
    ("GPa", "MPa"): 1_000.0,
    ("kPa", "MPa"): 1e-3,
    ("Pa", "MPa"): 1e-6,
    ("N/mm2", "MPa"): 1.0,
    ("psi", "MPa"): 0.006894757293168361,
    ("ksi", "MPa"): 6.894757293168361,
    ("Msi", "MPa"): 6894.757293168361,
    ("kg/m3", "kg/m3"): 1.0,
    ("g/cm3", "kg/m3"): 1_000.0,
    ("kg/dm3", "kg/m3"): 1_000.0,
    ("lb/in3", "kg/m3"): 27_679.904710203122,
    # A strain per degree Fahrenheit is smaller than the same strain per kelvin,
    # because a kelvin is 1.8 degF: per K = per degF x 1.8.
    ("1/K", "1/K"): 1.0,
    ("1/C", "1/K"): 1.0,
    ("1/F", "1/K"): 1.8,
    ("ppm/K", "1/K"): 1e-6,
    ("ppm/C", "1/K"): 1e-6,
    ("ppm/F", "1/K"): 1.8e-6,
    ("-", "-"): 1.0,
    ("%", "%"): 1.0,
    ("HV", "HV"): 1.0,
    ("W/(m.K)", "W/(m.K)"): 1.0,
    ("J/(kg.K)", "J/(kg.K)"): 1.0,
    ("K", "K"): 1.0,
    ("C", "K"): 1.0,  # handled as an offset below, listed so the pair is known
}

#: Absolute-zero offset for the one unit that is not a pure scaling.
_KELVIN_OFFSET: Final[Mapping[tuple[str, str], float]] = {("C", "K"): 273.15}


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert one number between units. The whole of the codebase's unit edge.

    Raises rather than guessing: an unknown pair means somebody transcribed a
    datasheet in units nothing here understands, and quietly treating it as
    already-correct is exactly the failure this function exists to prevent.
    """
    if from_unit == to_unit:
        # A no-op still goes through here so every transcription has one code
        # path, but it needs no table entry: a newly registered unit (N.mm, mm2)
        # would otherwise have to be added to _CONVERSIONS to be a no-op.
        return value
    key = (from_unit, to_unit)
    if key not in _CONVERSIONS:
        known = ", ".join(sorted({f"{a}->{b}" for a, b in _CONVERSIONS if a != b}))
        raise ValueError(
            f"No conversion from {from_unit!r} to {to_unit!r}. Convert the figure by "
            f"hand and record the unit you converted it to, or add the factor to "
            f"_CONVERSIONS. Known conversions: {known}."
        )
    return value * _CONVERSIONS[key] + _KELVIN_OFFSET.get(key, 0.0)


def transcribe(
    name: str,
    value: float,
    unit: str,
    *,
    status: Status,
    source: Source,
    note: str = "",
) -> Property:
    """Take a figure off a datasheet in its own units and record it in ours.

    This is the boundary. `unit` is the unit **as printed in the source**; the
    stored value is in `PROPERTY_UNITS[name]`'s unit, converted exactly once,
    here. Transcribing 10.0 "Msi" gives 68947.6 MPa; transcribing 68.9 "GPa"
    gives 68900 MPa; transcribing a number that is already in our units is a
    no-op and still goes through here, so every property in the library was
    written the same way.
    """
    canonical = PROPERTY_UNITS[name][0] if name in PROPERTY_UNITS else None
    if canonical is None:
        # Let Property's own validator produce the message with the near-miss
        # suggestions, rather than writing a second worse one here.
        return Property(name=name, value=value, status=status, source=source, note=note)
    return Property(
        name=name,
        value=convert(value, unit, canonical),
        status=status,
        source=source,
        note=note,
    )


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


class UnknownMaterial(LookupError):
    """A material slug that is not in the library. Never substituted."""


class IncompleteMaterial(ValueError):
    """A record that is missing a property the caller needs, named."""


@dataclass(frozen=True)
class MaterialRecord:
    """A material as a set of sourced properties in one stated condition.

    A record is one grade *in one condition*. 6061-T6 and 6061-O are two
    records, not one with a note, because every number differs.
    """

    slug: str
    display_name: str
    category: str
    condition: Condition
    properties: Mapping[str, Property] = field(default_factory=dict)
    #: Common names an engineer might type. Used only to make a refusal helpful;
    #: never to resolve a lookup, because "aluminium" is not a material.
    also_known_as: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        for key, prop in self.properties.items():
            if key != prop.name:
                raise ValueError(
                    f"{self.slug}: property filed under {key!r} but named {prop.name!r}. "
                    f"The key and the property's own name must agree, or a lookup "
                    f"returns the wrong unit."
                )

    def get(self, name: str) -> Property | None:
        """The property, or None when the library has no sourced value for it.

        None means *absent*, and absent is the honest answer for a property
        nobody has a source for. It is never 0.0, and callers must not treat it
        as one.
        """
        return self.properties.get(name)

    def value(self, name: str) -> float | None:
        """The bare number, or None when absent. For arithmetic on optionals."""
        prop = self.properties.get(name)
        return None if prop is None else prop.value

    def require(self, name: str) -> Property:
        """The property, or a refusal naming what is missing and what is present."""
        prop = self.properties.get(name)
        if prop is not None:
            return prop
        meaning = PROPERTY_UNITS.get(name, ("", "not a known property"))[1]
        have = ", ".join(sorted(self.properties)) or "nothing"
        raise IncompleteMaterial(
            f"{self.slug} has no {name} ({meaning}). No source for it is held here, "
            f"so there is no honest value to return — it is absent, not zero. "
            f"Supply it from a supplier datasheet on a custom Material, or choose a "
            f"grade that carries it. This record has: {have}."
        )

    def missing(self, names: Iterable[str]) -> tuple[str, ...]:
        """Which of `names` this record has no sourced value for, in order."""
        return tuple(name for name in names if name not in self.properties)

    def not_design_basis(self) -> tuple[str, ...]:
        """Strength properties held only as typical or estimated values.

        What a reviewer wants before signing: which allowables in this record are
        population averages rather than guaranteed minima.
        """
        strengths = (
            "yield_strength_mpa",
            "ultimate_tensile_strength_mpa",
            "compressive_strength_mpa",
            "fatigue_strength_mpa",
        )
        return tuple(
            name
            for name in strengths
            if (prop := self.properties.get(name)) is not None and not prop.is_design_basis
        )

    def to_material(self) -> Material:
        """The solver's view: the four numbers a linear-static run needs.

        Deliberately lossy. `Material` is the frozen, validated shape the FEA
        code and the API have always taken, and it carries no provenance —
        provenance belongs to the record, and a solver that branched on it would
        be making an engineering judgement no solver should make.
        """
        absent = self.missing(SOLVER_REQUIRED)
        if absent:
            raise IncompleteMaterial(
                f"{self.slug} cannot be solved with: no sourced value for "
                f"{', '.join(absent)}. A stress result computed against a guessed "
                f"modulus or density is not a result. Add the property with its "
                f"source, or pass a custom Material carrying your own numbers."
            )
        expansion = self.value("thermal_expansion_per_k")
        return Material(
            name=self.slug,
            youngs_modulus_mpa=self.require("youngs_modulus_mpa").value,
            poissons_ratio=self.require("poissons_ratio").value,
            yield_strength_mpa=self.require("yield_strength_mpa").value,
            density_kg_m3=self.require("density_kg_m3").value,
            thermal_expansion_per_k=expansion,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "category": self.category,
            "condition": self.condition.to_dict(),
            "properties": {name: prop.to_dict() for name, prop in sorted(self.properties.items())},
            "absent": list(self.missing(MATERIAL_PROPERTIES)),
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Sources used by the curated set
# ---------------------------------------------------------------------------

_ASM_AEROSPACE = Source(
    citation="ASM Aerospace Specification Metals — alloy datasheet",
    kind=SourceKind.DATASHEET,
    note=(
        "An aggregator of published grade data, not a lot certificate. Values are "
        "representative of the grade, not guaranteed for a delivered batch."
    ),
)
_MATWEB_GRADE = Source(
    citation="MatWeb material property data — grade overview",
    kind=SourceKind.DATASHEET,
    note=(
        "Aggregated from supplier data across many producers. Typical, not minimum; "
        "for a design allowable use MMPDS, EN 10025 or the supplier's certificate."
    ),
)
_POLYMER_GRADES = Source(
    citation="Unfilled thermoplastic supplier datasheets, spread across grades",
    kind=SourceKind.DATASHEET,
    note=(
        "Thermoplastic properties are grade-, fill- and process-dependent to a much "
        "greater degree than metals. Treat these as an order of magnitude for "
        "concept work and replace them with the datasheet for the actual grade."
    ),
)
_ELASTICITY_TEXT = Source(
    citation="Standard elasticity data for the class (Poisson's ratio)",
    kind=SourceKind.TEXTBOOK,
    note="Poisson's ratio varies little within a material class and is rarely certified.",
)


def _record(
    slug: str,
    display_name: str,
    category: str,
    condition: Condition,
    props: Iterable[Property],
    *,
    also_known_as: tuple[str, ...] = (),
    note: str = "",
) -> MaterialRecord:
    return MaterialRecord(
        slug=slug,
        display_name=display_name,
        category=category,
        condition=condition,
        properties={prop.name: prop for prop in props},
        also_known_as=also_known_as,
        note=note,
    )


_METAL_NOTE = (
    "No fatigue data is held for this grade. An S-N curve is not freely available at "
    "a quality worth signing off, so it is absent rather than approximated."
)


RECORDS: dict[str, MaterialRecord] = {
    record.slug: record
    for record in [
        _record(
            "aluminium-6061-t6",
            "Aluminium 6061-T6",
            "aluminium alloy",
            Condition(temper="T6", process="solution treated and artificially aged",
                      form="extruded bar, plate and sheet"),
            [
                transcribe("youngs_modulus_mpa", 68.9, "GPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("poissons_ratio", 0.33, "-",
                           status=Status.TYPICAL, source=_ELASTICITY_TEXT),
                transcribe("yield_strength_mpa", 276.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE,
                           note="Typical for T6/T651; thin sheet and weld zones run lower."),
                transcribe("ultimate_tensile_strength_mpa", 310.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("density_kg_m3", 2700.0, "kg/m3",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("thermal_expansion_per_k", 23.6e-6, "1/K",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE,
                           note="20-100 C range."),
            ],
            also_known_as=("aluminium", "aluminum", "6061", "alu"),
            note=_METAL_NOTE,
        ),
        _record(
            "aluminium-7075-t6",
            "Aluminium 7075-T6",
            "aluminium alloy",
            Condition(temper="T6", process="solution treated and artificially aged",
                      form="extruded bar and plate"),
            [
                transcribe("youngs_modulus_mpa", 71.7, "GPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("poissons_ratio", 0.33, "-",
                           status=Status.TYPICAL, source=_ELASTICITY_TEXT),
                transcribe("yield_strength_mpa", 503.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("ultimate_tensile_strength_mpa", 572.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("density_kg_m3", 2810.0, "kg/m3",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("thermal_expansion_per_k", 23.4e-6, "1/K",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE,
                           note="20-100 C range."),
            ],
            also_known_as=("7075", "zicral"),
            note=_METAL_NOTE + " 7075 is also notably stress-corrosion sensitive in T6.",
        ),
        _record(
            "steel-1018",
            "Steel AISI 1018, cold drawn",
            "carbon steel",
            Condition(process="cold drawn", form="round bar 19-32 mm"),
            [
                transcribe("youngs_modulus_mpa", 205.0, "GPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("poissons_ratio", 0.29, "-",
                           status=Status.TYPICAL, source=_ELASTICITY_TEXT),
                transcribe("yield_strength_mpa", 370.0, "MPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE,
                           note=(
                               "Cold drawn. Hot rolled 1018 yields nearer 220 MPa, and "
                               "the cold-drawn figure falls with increasing bar diameter."
                           )),
                transcribe("ultimate_tensile_strength_mpa", 440.0, "MPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("density_kg_m3", 7870.0, "kg/m3",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("thermal_expansion_per_k", 11.7e-6, "1/K",
                           status=Status.TYPICAL, source=_MATWEB_GRADE,
                           note="20-100 C range."),
            ],
            also_known_as=("steel", "mild steel", "1018", "acier"),
            note=_METAL_NOTE,
        ),
        _record(
            "stainless-304",
            "Stainless steel AISI 304, annealed",
            "stainless steel",
            Condition(process="annealed", form="sheet, plate and bar"),
            [
                transcribe("youngs_modulus_mpa", 193.0, "GPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("poissons_ratio", 0.29, "-",
                           status=Status.TYPICAL, source=_ELASTICITY_TEXT),
                transcribe("yield_strength_mpa", 215.0, "MPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE,
                           note=(
                               "Annealed. 304 work-hardens strongly, so cold-worked "
                               "stock is far stronger and much less ductile."
                           )),
                transcribe("ultimate_tensile_strength_mpa", 505.0, "MPa",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("density_kg_m3", 8000.0, "kg/m3",
                           status=Status.TYPICAL, source=_MATWEB_GRADE),
                transcribe("thermal_expansion_per_k", 17.3e-6, "1/K",
                           status=Status.TYPICAL, source=_MATWEB_GRADE,
                           note="0-100 C range."),
            ],
            also_known_as=("stainless", "inox", "304", "a2"),
            note=_METAL_NOTE,
        ),
        _record(
            "titanium-ti6al4v",
            "Titanium Ti-6Al-4V (Grade 5), annealed",
            "titanium alloy",
            Condition(process="annealed", form="bar and plate"),
            [
                transcribe("youngs_modulus_mpa", 113.8, "GPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("poissons_ratio", 0.342, "-",
                           status=Status.TYPICAL, source=_ELASTICITY_TEXT),
                transcribe("yield_strength_mpa", 880.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("ultimate_tensile_strength_mpa", 950.0, "MPa",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("density_kg_m3", 4430.0, "kg/m3",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE),
                transcribe("thermal_expansion_per_k", 8.6e-6, "1/K",
                           status=Status.TYPICAL, source=_ASM_AEROSPACE,
                           note="20-100 C range."),
            ],
            also_known_as=("titanium", "ti64", "grade 5", "tial6v4"),
            note=_METAL_NOTE,
        ),
        _record(
            "abs",
            "ABS, unfilled",
            "thermoplastic",
            Condition(process="injection moulded or extruded", form="unfilled, general purpose"),
            [
                transcribe("youngs_modulus_mpa", 2200.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES,
                           note="Grade spread is roughly 1.6-2.6 GPa."),
                transcribe("poissons_ratio", 0.35, "-",
                           status=Status.ESTIMATED, source=_ELASTICITY_TEXT,
                           note=(
                               "Class-typical for a glassy amorphous thermoplastic; "
                               "rarely quoted per grade, and time-dependent in practice."
                           )),
                transcribe("yield_strength_mpa", 40.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES,
                           note="Short-term tensile yield at 23 C; creep governs under load."),
                transcribe("density_kg_m3", 1040.0, "kg/m3",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
                transcribe("thermal_expansion_per_k", 90.0e-6, "1/K",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
            ],
            note=(
                "Polymer strength here is short-term and isothermal. Creep, temperature "
                "and layer adhesion in a printed part are not represented at all, and no "
                "fatigue data is held."
            ),
        ),
        _record(
            "pla",
            "PLA, unfilled",
            "thermoplastic",
            Condition(process="fused filament fabrication or moulded", form="unfilled"),
            [
                transcribe("youngs_modulus_mpa", 3500.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES,
                           note="Bulk moulded value; a printed part is anisotropic and weaker."),
                transcribe("poissons_ratio", 0.36, "-",
                           status=Status.ESTIMATED, source=_ELASTICITY_TEXT,
                           note="Class-typical for a glassy thermoplastic; seldom quoted per grade."),
                transcribe("yield_strength_mpa", 50.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
                transcribe("density_kg_m3", 1240.0, "kg/m3",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
                transcribe("thermal_expansion_per_k", 68.0e-6, "1/K",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
            ],
            note=(
                "PLA softens near 60 C, so anything warm is outside these numbers. A "
                "printed part's strength across layers is a fraction of these values."
            ),
        ),
        _record(
            "nylon-pa12",
            "Nylon PA12, unfilled",
            "thermoplastic",
            Condition(process="laser sintered or moulded", form="unfilled, dry as moulded"),
            [
                transcribe("youngs_modulus_mpa", 1700.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES,
                           note="Dry as moulded; conditioned PA12 is markedly softer."),
                transcribe("poissons_ratio", 0.39, "-",
                           status=Status.ESTIMATED, source=_ELASTICITY_TEXT,
                           note="Class-typical for a semi-crystalline polyamide."),
                transcribe("yield_strength_mpa", 48.0, "MPa",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
                transcribe("density_kg_m3", 1010.0, "kg/m3",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
                transcribe("thermal_expansion_per_k", 110.0e-6, "1/K",
                           status=Status.TYPICAL, source=_POLYMER_GRADES),
            ],
            note=(
                "Polyamides absorb moisture and soften as they do. These are dry-as-moulded "
                "figures; a conditioned part is a different material for design purposes."
            ),
        ),
    ]
}


#: The solver's and the API's view of the library, unchanged in shape and in
#: numbers from before provenance existed. Derived from `RECORDS` so the two
#: cannot drift: two tables of the same physical constants always do.
MATERIALS: dict[str, Material] = {slug: record.to_material() for slug, record in RECORDS.items()}


def available() -> tuple[str, ...]:
    """Every material slug in the library, sorted. What a refusal should name."""
    return tuple(sorted(RECORDS))


def resolve(slug: str) -> MaterialRecord:
    """The record for a slug, or a refusal. **Never substitutes.**

    This exists because the alternative was measured and it was bad: the CATIA
    bridge used to fall back to steel for any name it did not recognise, so
    asking for "Aluminium" — a perfectly reasonable thing to type — attached
    steel and returned a mass 2.9x too heavy, with the structured result fields
    still reading as success. A wrong material that reports success is worse
    than no material at all, so an unknown slug raises, and the message names
    the near misses and then the whole library.
    """
    record = RECORDS.get(slug)
    if record is not None:
        return record

    lowered = slug.strip().lower()
    suggestions = [candidate for candidate in RECORDS if candidate == lowered]
    if not suggestions:
        suggestions = [
            candidate
            for candidate, record_ in RECORDS.items()
            if lowered and lowered in record_.also_known_as
        ]
    if not suggestions:
        suggestions = difflib.get_close_matches(lowered, list(RECORDS), n=3, cutoff=0.5)

    hint = (
        f" Did you mean {' or '.join(repr(s) for s in suggestions)}?"
        if suggestions
        else " A material class such as 'aluminium' is not a material: a temper has to be"
        " chosen, because 6061-O and 6061-T6 differ by a factor of five in strength."
    )
    raise UnknownMaterial(
        f"{slug!r} is not in the material library.{hint} "
        f"Available: {', '.join(available())}. "
        f"To use a grade that is not listed, pass a Material with your own values from "
        f"the supplier's datasheet rather than picking the nearest name."
    )


def material_for(slug: str) -> Material:
    """The solver's `Material` for a slug, or the same loud refusal as `resolve`."""
    return resolve(slug).to_material()
