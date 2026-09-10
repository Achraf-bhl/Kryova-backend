"""Choosing a rolling bearing, rather than modelling one (E12.4).

Phase 12.4's whole sentence is "given load, speed, life — choose the bearing;
never model what should be bought." A rolling bearing is the clearest case of
that rule in a machine: the physics inside it is Hertzian contact and fatigue
under a rotating stress field, it is not something a linear-static FEA of the
housing has any view of, and every manufacturer publishes the one number that
settles it. So this module does arithmetic from a standard and a lookup from a
catalogue, and contains no bearing model at all.

## What is arithmetic and what is data

**ISO 281 rating life is arithmetic**, fully specified, and reproduced here from
the standard: `L10 = (C/P)^p` in millions of revolutions, `p = 3` for ball
bearings and `10/3` for roller bearings. Everything in `life.py`-shaped
functions below is checkable against the standard's own definitions and against
worked examples in any bearing handbook, which is how it is tested.

**The load ratings are data, and they are the manufacturer's.** `C` (basic
dynamic load rating) and `C0` (basic static) depend on internal geometry — ball
count, ball diameter, raceway conformity, material and heat treatment — that
differs between makers for the same ISO boundary dimensions. ISO 281 gives a
formula for `C` from that internal geometry, but it needs `f_c` from a table
indexed on `D_w cos α / D_pw`, and the internal geometry of a specific bearing
is not published. **So a rating is transcribed from a catalogue with a citation,
or it is absent and this module refuses to select.**

That refusal is the point and it is the same rule `app/parts/types.py` already
enforces with `MissingEngineeringData`: a bearing whose `C` nobody sourced is
not a bearing you may size against, and inventing a plausible one produces a
selection that looks like engineering and is not. `SHIPPED_BEARINGS` below
therefore carries **ISO 15 boundary dimensions** — which are a standard and
citable — and marks every load rating as absent until an operator loads a real
catalogue through `BearingCatalogue`.

## What this deliberately does not do

* **No equivalent-load derivation for angular-contact or tapered bearings.**
  Their `X`/`Y` factors depend on `e`, which depends on `f0 Fa / C0`, which is a
  table per bearing series. Deep-groove ball bearings have the same problem and
  the same answer: `equivalent_dynamic_load` takes `X` and `Y` as arguments and
  refuses to guess them.
* **No life-modification factor `a_ISO`.** ISO 281:2007's `a_ISO` needs the
  fatigue load limit `Cu`, the contamination factor and the viscosity ratio, and
  two of those three are operating conditions nobody has told us. `L10` is the
  honest answer, `L_nm` is not, and the difference between them is a factor of
  ten in the wrong hands.
* **No thermal or lubrication check.** A bearing chosen on life alone can still
  fail on speed limit or on temperature, and `select` says so in `caveats`
  rather than implying it has checked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.solve.materials import Property, Source, SourceKind, register_quantity

# The two quantities a bearing carries that no other part does. Registered
# through the shared vocabulary rather than declared locally, for the reason
# `app/parts/types.py` gives: a bolt's proof load and a bearing's dynamic rating
# are the same kind of claim, and two provenance models would drift.
for _name, _unit, _meaning in [
    (
        "basic_dynamic_load_rating_n",
        "N",
        "ISO 281 C: the load at which L10 is one million revolutions",
    ),
    (
        "basic_static_load_rating_n",
        "N",
        "ISO 76 C0: the load producing a permanent deformation of 0.0001 of the "
        "rolling-element diameter",
    ),
]:
    register_quantity(_name, _unit, _meaning)
del _name, _unit, _meaning

# ---------------------------------------------------------------------------
# The standard's arithmetic
# ---------------------------------------------------------------------------


class BearingKind(StrEnum):
    """Enough to pick the life exponent, which is the only place it matters."""

    BALL = "ball"
    ROLLER = "roller"

    @property
    def life_exponent(self) -> float:
        """ISO 281's `p`. 3 for ball, 10/3 for roller.

        Not configurable and not a float parameter anywhere: it is a property of
        the contact — point contact for a ball, line contact for a roller — and
        a caller who could pass their own would eventually pass 3 for a roller
        and overstate its life by about 40% at a typical load ratio.
        """
        return 3.0 if self is BearingKind.BALL else 10.0 / 3.0


#: ISO 281 quotes life in millions of revolutions. Converting to hours needs the
#: speed, and this is the constant in that conversion — spelled out rather than
#: inlined so the formula below reads like the standard.
REVOLUTIONS_PER_MILLION: Final = 1_000_000.0
MINUTES_PER_HOUR: Final = 60.0


class BearingError(Exception):
    """A selection or a life calculation was refused, in words."""


def equivalent_dynamic_load(
    *, radial_n: float, axial_n: float, x_factor: float, y_factor: float
) -> float:
    """ISO 281 `P = X·Fr + Y·Fa`, in newtons.

    **`X` and `Y` are arguments and are not defaulted.** They come from a table
    per bearing type and, for a deep-groove ball bearing, depend on `e`, which
    depends on `f0·Fa/C0` — a lookup this module does not have and will not
    invent. A caller with a pure radial load passes `X=1, Y=0`, which is exact
    and is the common case; anything else needs the manufacturer's table.
    """
    if radial_n < 0 or axial_n < 0:
        raise BearingError("Loads are magnitudes here; pass them positive.")
    if x_factor < 0 or y_factor < 0:
        raise BearingError("X and Y are non-negative factors from a bearing table.")
    load = x_factor * radial_n + y_factor * axial_n
    if load <= 0:
        raise BearingError(
            "The equivalent load works out at zero, so rating life is undefined. "
            "A bearing carrying nothing does not have a fatigue life."
        )
    return load


def rating_life_revolutions(
    *, dynamic_rating_n: float, equivalent_load_n: float, kind: BearingKind
) -> float:
    """`L10` in **millions of revolutions**: `(C/P)^p`.

    The 10 in L10 is the reliability: 90% of a population reaches this life. It
    is not a guarantee for one bearing and `select` says so.
    """
    if dynamic_rating_n <= 0:
        raise BearingError("The basic dynamic load rating C must be positive.")
    if equivalent_load_n <= 0:
        raise BearingError("The equivalent load P must be positive.")
    return float((dynamic_rating_n / equivalent_load_n) ** kind.life_exponent)


def rating_life_hours(
    *, dynamic_rating_n: float, equivalent_load_n: float, speed_rpm: float, kind: BearingKind
) -> float:
    """`L10h = 10^6 / (60 n) · (C/P)^p`, in hours."""
    if speed_rpm <= 0:
        raise BearingError(
            "Life in hours needs a speed. A stationary bearing has no rating life; "
            "check its static rating C0 instead."
        )
    millions = rating_life_revolutions(
        dynamic_rating_n=dynamic_rating_n, equivalent_load_n=equivalent_load_n, kind=kind
    )
    return millions * REVOLUTIONS_PER_MILLION / (MINUTES_PER_HOUR * speed_rpm)


def required_dynamic_rating_n(
    *, equivalent_load_n: float, speed_rpm: float, life_hours: float, kind: BearingKind
) -> float:
    """The `C` a bearing needs to reach `life_hours`. `rating_life_hours` inverted.

    Used to turn a requirement into a shopping criterion, which is the direction
    an engineer actually works in: "I need 20,000 hours at 1,500 rpm under 4 kN"
    is the question, and "what C does that need" is the answer that makes a
    catalogue searchable.
    """
    if life_hours <= 0:
        raise BearingError("A required life must be positive.")
    if speed_rpm <= 0:
        raise BearingError("A required life in hours needs a speed.")
    millions = life_hours * MINUTES_PER_HOUR * speed_rpm / REVOLUTIONS_PER_MILLION
    return float(equivalent_load_n * millions ** (1.0 / kind.life_exponent))


def static_safety_factor(*, static_rating_n: float, static_load_n: float) -> float:
    """`s0 = C0 / P0`.

    A separate check from life and not a substitute for it: rating life is
    fatigue under rotation, and `s0` is about permanent indentation of the
    raceway under a load that may be applied while the shaft is stopped. A
    bearing can pass one and fail the other, which is why `select` reports both.
    """
    if static_load_n <= 0:
        raise BearingError("The static equivalent load P0 must be positive.")
    if static_rating_n <= 0:
        raise BearingError("The basic static load rating C0 must be positive.")
    return static_rating_n / static_load_n


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bearing:
    """One catalogue entry.

    Dimensions are millimetres, ratings newtons — the house units, unconverted.

    `dynamic_rating` and `static_rating` are `Property` records rather than
    floats, so a rating carries its source exactly as a yield strength does. A
    bearing with neither is a boundary-dimension stub: real enough to place in a
    layout, not enough to size against, and `is_selectable` is that distinction
    made checkable.
    """

    designation: str
    kind: BearingKind
    bore_mm: float
    outer_diameter_mm: float
    width_mm: float
    dynamic_rating: Property | None = None
    static_rating: Property | None = None
    #: Manufacturer limiting speed, where one was transcribed. Absent by
    #: default: it depends on lubrication and cage type, and a number carried
    #: without those is a number about a different bearing.
    limiting_speed_rpm: float | None = None
    source: Source | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("bore_mm", self.bore_mm),
            ("outer_diameter_mm", self.outer_diameter_mm),
            ("width_mm", self.width_mm),
        ):
            if value <= 0:
                raise ValueError(f"{self.designation}: {name} must be positive")
        if self.outer_diameter_mm <= self.bore_mm:
            raise ValueError(
                f"{self.designation}: the outer diameter must exceed the bore. "
                "A bearing whose outside is inside its bore is a transcription error, "
                "and it would sort correctly in a catalogue search while being nonsense."
            )

    @property
    def is_selectable(self) -> bool:
        """Whether this entry can be sized against.

        Both ratings, or neither is enough: life needs `C` and the static check
        needs `C0`, and a selection that silently skipped the static check
        because the number was missing would report a pass it never made.
        """
        return self.dynamic_rating is not None and self.static_rating is not None

    @property
    def missing(self) -> tuple[str, ...]:
        absent = []
        if self.dynamic_rating is None:
            absent.append("basic dynamic load rating C")
        if self.static_rating is None:
            absent.append("basic static load rating C0")
        return tuple(absent)


#: ISO 15:2017 boundary dimensions for the 6000 series, dimension series 18/19
#: excluded. **Boundary dimensions only.** They are a standard and are the same
#: for every maker, which is what makes them safe to ship; the load ratings are
#: not, and are absent by construction — see the module docstring.
_ISO_15 = Source(
    citation="ISO 15:2017, rolling bearings — radial bearings — boundary dimensions, Table 4",
    kind=SourceKind.STANDARD,
    year=2017,
)

SHIPPED_BEARINGS: Final[tuple[Bearing, ...]] = tuple(
    Bearing(
        designation=designation,
        kind=BearingKind.BALL,
        bore_mm=bore,
        outer_diameter_mm=outer,
        width_mm=width,
        source=_ISO_15,
    )
    for designation, bore, outer, width in (
        ("6000", 10.0, 26.0, 8.0),
        ("6001", 12.0, 28.0, 8.0),
        ("6002", 15.0, 32.0, 9.0),
        ("6003", 17.0, 35.0, 10.0),
        ("6004", 20.0, 42.0, 12.0),
        ("6005", 25.0, 47.0, 12.0),
        ("6006", 30.0, 55.0, 13.0),
        ("6007", 35.0, 62.0, 14.0),
        ("6008", 40.0, 68.0, 15.0),
        ("6009", 45.0, 75.0, 16.0),
        ("6010", 50.0, 80.0, 16.0),
        ("6200", 10.0, 30.0, 9.0),
        ("6201", 12.0, 32.0, 10.0),
        ("6202", 15.0, 35.0, 11.0),
        ("6203", 17.0, 40.0, 12.0),
        ("6204", 20.0, 47.0, 14.0),
        ("6205", 25.0, 52.0, 15.0),
        ("6206", 30.0, 62.0, 16.0),
        ("6207", 35.0, 72.0, 17.0),
        ("6208", 40.0, 80.0, 18.0),
        ("6300", 10.0, 35.0, 11.0),
        ("6301", 12.0, 37.0, 12.0),
        ("6302", 15.0, 42.0, 13.0),
        ("6303", 17.0, 47.0, 14.0),
        ("6304", 20.0, 52.0, 15.0),
        ("6305", 25.0, 62.0, 17.0),
        ("6306", 30.0, 72.0, 19.0),
        ("6307", 35.0, 80.0, 21.0),
        ("6308", 40.0, 90.0, 23.0),
    )
)


@dataclass
class BearingCatalogue:
    """What is available to choose from.

    A class rather than a module-level dict so a deployment can load a real
    manufacturer catalogue — which is the only way the shipped entries become
    selectable — without this module knowing anything about where it came from.
    The same seam `app/parts/catalogue.py` uses for fasteners.
    """

    bearings: Mapping[str, Bearing]

    @classmethod
    def of(cls, bearings: Iterable[Bearing]) -> "BearingCatalogue":
        return cls(MappingProxyType({bearing.designation: bearing for bearing in bearings}))

    def get(self, designation: str) -> Bearing:
        try:
            return self.bearings[designation]
        except KeyError as missing:
            raise BearingError(
                f"No bearing {designation!r} in this catalogue. "
                f"It holds {len(self.bearings)} entries."
            ) from missing

    def fitting(self, *, bore_mm: float, tolerance_mm: float = 0.0) -> tuple[Bearing, ...]:
        """Every bearing whose bore matches a shaft, smallest outside first.

        Sorted by outside diameter because that is what a housing has to
        accommodate: given two bearings that both fit the shaft and both reach
        the life, the smaller one is the answer unless something else says
        otherwise.
        """
        return tuple(
            sorted(
                (
                    bearing
                    for bearing in self.bearings.values()
                    if abs(bearing.bore_mm - bore_mm) <= tolerance_mm
                ),
                key=lambda bearing: (bearing.outer_diameter_mm, bearing.width_mm),
            )
        )


#: The shipped catalogue: boundary dimensions, no ratings, so nothing in it is
#: selectable until an operator loads real data. That is deliberate.
CATALOGUE: Final[BearingCatalogue] = BearingCatalogue.of(SHIPPED_BEARINGS)


# ---------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Duty:
    """What the bearing has to survive. The engineer's side of the question."""

    radial_n: float
    speed_rpm: float
    required_life_hours: float
    axial_n: float = 0.0
    #: From the manufacturer's table. Defaults are the exact pure-radial case.
    x_factor: float = 1.0
    y_factor: float = 0.0
    #: `s0` a design must clear. 1.0 means "no permanent indentation at rated
    #: static load"; machine-design practice asks more for shock. Required
    #: rather than defaulted to a rule of thumb.
    minimum_static_safety: float = 1.0

    def __post_init__(self) -> None:
        if self.required_life_hours <= 0:
            raise BearingError("A duty needs a required life in hours.")
        if self.speed_rpm <= 0:
            raise BearingError(
                "A duty needs a speed. For a bearing that does not rotate, check the "
                "static rating instead — rating life is fatigue under rotation."
            )


@dataclass(frozen=True)
class Selection:
    """A chosen bearing, the numbers behind it, and what was not checked."""

    bearing: Bearing
    equivalent_load_n: float
    life_hours: float
    static_safety: float
    required_dynamic_rating_n: float
    caveats: tuple[str, ...] = ()

    @property
    def margin(self) -> float:
        """How much life is in hand, as a ratio. 1.0 is exactly the requirement."""
        return self.life_hours


@dataclass(frozen=True)
class Refusal:
    """Why nothing was chosen. Never an empty result with no explanation."""

    reason: str
    considered: int
    unselectable: tuple[str, ...] = ()


#: Said on every selection, because none of it is checked here and a selection
#: that stayed silent would read as a full sign-off.
STANDING_CAVEATS: Final[tuple[str, ...]] = (
    "L10 is the life 90% of a population reaches; it is not a guarantee for one bearing.",
    "No ISO 281 a_ISO life modification is applied — that needs the fatigue load limit, "
    "the contamination level and the viscosity ratio, and two of those are operating "
    "conditions nobody has stated.",
    "Lubrication, operating temperature and limiting speed are not checked.",
    "Mounting, fits and shaft/housing tolerances are not checked.",
)


def select(
    duty: Duty,
    *,
    bore_mm: float,
    catalogue: BearingCatalogue = CATALOGUE,
    bore_tolerance_mm: float = 0.0,
    kind: BearingKind = BearingKind.BALL,
) -> Selection | Refusal:
    """The smallest bearing that fits the shaft and reaches the life.

    Returns a `Refusal` rather than raising or returning `None`, so a caller
    always has a sentence to show. "Nothing fits" and "everything that fits has
    no sourced load rating" are completely different problems with completely
    different fixes, and a bare `None` makes them the same.

    **A bearing with no sourced `C` is never chosen**, and the refusal names
    them. That is the rule this module exists to keep: a selection computed from
    an invented rating looks exactly like engineering and is not.
    """
    candidates = catalogue.fitting(bore_mm=bore_mm, tolerance_mm=bore_tolerance_mm)
    if not candidates:
        return Refusal(
            reason=(
                f"No bearing in this catalogue has a {bore_mm:g} mm bore "
                f"(tolerance {bore_tolerance_mm:g} mm). Change the shaft diameter to a "
                "standard bore, or load a catalogue that covers it."
            ),
            considered=len(catalogue.bearings),
        )

    unselectable = tuple(
        f"{bearing.designation} (missing {', '.join(bearing.missing)})"
        for bearing in candidates
        if not bearing.is_selectable
    )
    usable = [bearing for bearing in candidates if bearing.is_selectable and bearing.kind is kind]
    if not usable:
        return Refusal(
            reason=(
                "Every bearing that fits this shaft is missing its load ratings, so none "
                "can be sized against. Load a manufacturer catalogue: C and C0 are the "
                "maker's numbers and differ between makers for the same ISO dimensions, "
                "so this library ships boundary dimensions only."
            ),
            considered=len(candidates),
            unselectable=unselectable,
        )

    load = equivalent_dynamic_load(
        radial_n=duty.radial_n,
        axial_n=duty.axial_n,
        x_factor=duty.x_factor,
        y_factor=duty.y_factor,
    )
    needed = required_dynamic_rating_n(
        equivalent_load_n=load,
        speed_rpm=duty.speed_rpm,
        life_hours=duty.required_life_hours,
        kind=kind,
    )

    for bearing in usable:
        assert bearing.dynamic_rating is not None and bearing.static_rating is not None
        if bearing.dynamic_rating.value < needed:
            continue
        safety = static_safety_factor(
            static_rating_n=bearing.static_rating.value, static_load_n=load
        )
        if safety < duty.minimum_static_safety:
            # Passes on life, fails on indentation. Skipped rather than
            # returned with a warning: a selection that fails a stated check is
            # not a selection.
            continue
        return Selection(
            bearing=bearing,
            equivalent_load_n=load,
            life_hours=rating_life_hours(
                dynamic_rating_n=bearing.dynamic_rating.value,
                equivalent_load_n=load,
                speed_rpm=duty.speed_rpm,
                kind=kind,
            ),
            static_safety=safety,
            required_dynamic_rating_n=needed,
            caveats=STANDING_CAVEATS,
        )

    return Refusal(
        reason=(
            f"No bearing on this shaft reaches {duty.required_life_hours:g} hours at "
            f"{duty.speed_rpm:g} rpm under {load:g} N: the largest available has less "
            f"than the {needed:g} N dynamic rating it would need. Use a larger shaft, "
            "reduce the load, or accept a shorter life."
        ),
        considered=len(usable),
        unselectable=unselectable,
    )


def describe(selection: Selection) -> str:
    """One paragraph an engineer can put in a report."""
    bearing = selection.bearing
    return (
        f"{bearing.designation} ({bearing.bore_mm:g}×{bearing.outer_diameter_mm:g}×"
        f"{bearing.width_mm:g} mm): P = {selection.equivalent_load_n:g} N, "
        f"L10h = {selection.life_hours:,.0f} h, s0 = {selection.static_safety:.2f}. "
        f"Needed C ≥ {selection.required_dynamic_rating_n:g} N."
    )


#: Exported from `app.parts` as `select_bearing`: at package level, beside
#: `find` and `search` for fasteners, a bare `select` says nothing about
#: what it selects.
select_bearing = select

__all__ = [
    "CATALOGUE",
    "SHIPPED_BEARINGS",
    "STANDING_CAVEATS",
    "Bearing",
    "BearingCatalogue",
    "BearingError",
    "BearingKind",
    "Duty",
    "Refusal",
    "Selection",
    "describe",
    "equivalent_dynamic_load",
    "rating_life_hours",
    "rating_life_revolutions",
    "required_dynamic_rating_n",
    "select",
    "select_bearing",
    "static_safety_factor",
]