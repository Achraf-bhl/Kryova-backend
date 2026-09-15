"""What a notch costs: the notch sensitivity that turns Kt into Kf, and the plastic notch stress.

Master plan E8.5. `factors.StressConcentration` already holds Kt and an optional notch
sensitivity q, and applies Kf = 1 + q·(Kt − 1). With no q it takes Kf = Kt and says it assumed
full sensitivity. This module supplies the two things that sat outside it.

**1. q from the notch radius, by Neuber's technical factor.** Kuhn and Hardrath, NACA TN 2805
(1952), formula (1) on p. 4, read on the page image:

    K_N = 1 + (K_T − 1) / (1 + π/(π − ω) · √(A/R))

where R is the notch root radius, ω the flank angle (their Figure 2) and A the "Neuber constant",
a length. So q = 1 / (1 + π/(π − ω)·√(A/R)), and `StressConcentration.kf` then equals K_N
exactly. Three things the report says, which the source of every concentration built here repeats:

* **A is a material constant nobody has derived.** The report's Figure 3 (p. 27) is a curve of
  √A against ultimate strength for steels, "obtained by a trial-and-error process" (p. 7). It is
  a plotted curve, not a table, so reading a value off it is the engineer's act and goes in the
  constant's source. **No value of A is held here**, and the curve's axes are in inches and ksi:
  the conversion to mm happens where it is read, never in this module.
* **It is an estimate, with a stated accuracy.** "The Neuber formula (1), used in conjunction
  with the curve of figure 3, predicts the fatigue factor with an accuracy of ±10 percent for
  69 percent of the tests if specimens with notch radii equal to or less than 0.01 inch are
  excluded and for 56 percent of the tests if no specimens are excluded" (pp. 8–9).
* **Its scope is steel near 10⁷ cycles.** The title says steel, and the fatigue factors it was
  checked against came from S-N curves "in the region of concern herein (that is, N = 10⁷)" (p. 9).

Peterson's form of q, the other common one, was not found in a document that could be read, so it
is not offered. `StressConcentration(notch_sensitivity=…)` still takes a q from any source.

**2. The elastic-plastic notch stress, by the extended Neuber rule, through pyLife.** A linear FE
model reports the stress a notch would carry if the material stayed elastic. Past yield the real
stress is lower and the strain higher. pyLife's `ExtendedNeuber` (FKM nonlinear, 2019, chapter
2.5.7, as pyLife's docstrings cite it) maps the elastic stress L onto a Ramberg–Osgood cyclic
curve, ε = σ/E + (σ/K')^(1/n'), with a limit-load factor K_p for yielding of the net section.
The arithmetic is pyLife's (Decision 2) and sits behind `backend.FatigueBackend`. What is ours is
that every constant is an input with a source, and that the root is checked.

* **K_p has no default, and it moves the answer.** On a probe curve at an elastic notch stress of
  853 MPa, K_p = 1000 gave 450 MPa and K_p = 3 gave 477 MPa. The rule approaches classical Neuber,
  σ·ε = L²/E, as K_p grows, and `tests/test_fatigue_notch.py` holds it to that limit. pyLife
  raises a `TypeError` when K_p is left out. A caller who wants classical Neuber is choosing to
  ignore net-section yielding, and says so through the factor's source.
* **K_p is at least 1.** A limit load is never below the load at first yield. The elastic
  solution at first yield is a statically admissible field inside the yield surface, so it is a
  lower bound on the limit load. That is a theorem of plasticity, not a number read anywhere.

Not here, and not claimed: **a variable-amplitude history.** Past the first loading, the local
stress depends on every earlier reversal (material memory), which FKM nonlinear handles with its
HCM counting. This module answers one first loading or one hysteresis range. **Strain-life
damage**: this package has S-N curves and no ε-N or P_RAM curve, so nothing consumes the plastic
strain yet. **FKM nonlinear itself** was not read; the rule is as pyLife implements it.

Units are the codebase's mm-N-MPa; strain is dimensionless. Nothing here converts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.fatigue.backend import FatigueBackend, PyLifeBackend
from app.fatigue.factors import StressConcentration
from app.fatigue.sources import require_source

#: Where Neuber's technical factor and everything said about it here was read.
NACA_TN_2805: Final[str] = (
    "P. Kuhn and H. F. Hardrath, 'An engineering method for estimating notch-size effect in "
    "fatigue tests on steel', NACA Technical Note 2805, October 1952, formula (1) p. 4, Figure 3 "
    "p. 27, accuracy pp. 8–9; read 2026-09-15 from "
    "https://ntrs.nasa.gov/api/citations/19930083528/downloads/19930083528.pdf"
)

#: The report's own statement of how good the estimate is, carried into every source built here.
NEUBER_ACCURACY: Final[str] = (
    "an estimate: with Figure 3's constant, within ±10% of the measured fatigue factor for 69% of "
    "the report's steel tests excluding notch radii ≤ 0.01 in, and 56% with none excluded, at "
    "N = 10⁷ (pp. 8–9)"
)

#: The rule pyLife implements, as its own docstrings cite it.
EXTENDED_NEUBER: Final[str] = (
    "extended Neuber rule, FKM nonlinear (2019) §2.5.7 eqs 2.5-45 and 2.5-46 as implemented by "
    "pyLife's ExtendedNeuber (the guideline itself not read)"
)


@dataclass(frozen=True)
class NeuberConstant:
    """Neuber's material length A, in mm, with where it was read."""

    length_mm: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A Neuber constant"))
        if not (self.length_mm > 0.0 and math.isfinite(self.length_mm)):
            raise ValueError(
                f"Neuber's constant A is a positive length; got {self.length_mm!r}. At A = 0 the "
                "formula gives full notch sensitivity, which StressConcentration already takes "
                "without a q."
            )


def neuber_sensitivity(radius_mm: float, constant: NeuberConstant, *, flank_angle_deg: float) -> float:
    """q = 1 / (1 + π/(π − ω)·√(A/R)), from NACA TN 2805 formula (1).

    `flank_angle_deg` is ω, the angle between the notch flanks (TN 2805 Figure 2): 0 for a notch
    with parallel flanks, such as a U-groove or a hole. It has no default, because a V-notch's
    angle changes q.
    """
    if not (radius_mm > 0.0 and math.isfinite(radius_mm)):
        raise ValueError(
            f"The notch root radius must be positive and finite; got {radius_mm!r}. A sharp crack "
            "has R = 0, where the formula has no meaning and fracture mechanics applies instead."
        )
    if not (0.0 <= flank_angle_deg < 180.0):
        raise ValueError(
            f"The flank angle ω lies in [0°, 180°); got {flank_angle_deg!r}. At 180° the flanks are "
            "one straight surface and there is no notch."
        )
    omega = math.radians(flank_angle_deg)
    return 1.0 / (1.0 + math.pi / (math.pi - omega) * math.sqrt(constant.length_mm / radius_mm))


def neuber_concentration(
    *,
    kt: float,
    kt_source: str,
    radius_mm: float,
    constant: NeuberConstant,
    flank_angle_deg: float,
    location: str,
) -> StressConcentration:
    """A `StressConcentration` whose Kf is Neuber's technical factor K_N."""
    q = neuber_sensitivity(radius_mm, constant, flank_angle_deg=flank_angle_deg)
    kt_from = require_source(kt_source, "A stress concentration factor")
    return StressConcentration(
        kt=kt,
        location=location,
        notch_sensitivity=q,
        source=(
            f"Kt from {kt_from}; q by Neuber's technical factor [{NACA_TN_2805}] with R = "
            f"{radius_mm:g} mm, ω = {flank_angle_deg:g}°, A = {constant.length_mm:g} mm "
            f"[{constant.source}]; {NEUBER_ACCURACY}"
        ),
    )


@dataclass(frozen=True)
class CyclicCurve:
    """A cyclic stress-strain curve, ε = σ/E + (σ/K')^(1/n'), with where its constants came from.

    Cyclic, not monotonic: K' and n' come from a cyclic test or a stated estimate, and the source
    says which. A monotonic curve here would describe the first quarter-cycle only.
    """

    youngs_modulus_mpa: float
    strength_coefficient_mpa: float
    hardening_exponent: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A cyclic stress-strain curve"))
        for value, what in (
            (self.youngs_modulus_mpa, "Young's modulus E"),
            (self.strength_coefficient_mpa, "the cyclic strength coefficient K'"),
            (self.hardening_exponent, "the cyclic hardening exponent n'"),
        ):
            if not (value > 0.0 and math.isfinite(value)):
                raise ValueError(f"{what} must be positive and finite; got {value!r}.")


@dataclass(frozen=True)
class LimitLoadFactor:
    """K_p, the ratio of the plastic limit load to the load at first yield, with its source."""

    value: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A limit-load factor"))
        if not (self.value >= 1.0 and math.isfinite(self.value)):
            raise ValueError(
                f"The limit-load factor K_p is at least 1; got {self.value!r}. The load at first "
                "yield is a lower bound on the plastic limit load, so their ratio cannot fall below 1."
            )


class Branch(StrEnum):
    """Which part of the local stress-strain path an answer is on."""

    FIRST_LOADING = "first loading"
    HYSTERESIS = "hysteresis branch"


@dataclass(frozen=True)
class NotchStrain:
    """The local elastic-plastic answer at a notch, and everything it was computed from."""

    branch: Branch
    #: The linear-elastic notch stress (first loading) or stress range (hysteresis).
    elastic_mpa: float
    #: The elastic-plastic stress, or stress range, on `branch`.
    stress_mpa: float
    #: The total strain, or strain range, on `branch`.
    strain: float
    source: str


def first_loading(
    elastic_mpa: float,
    curve: CyclicCurve,
    limit_load: LimitLoadFactor,
    *,
    backend: FatigueBackend | None = None,
) -> NotchStrain:
    """The notch stress and strain the first time the elastic notch stress `elastic_mpa` is reached."""
    if not math.isfinite(elastic_mpa):
        raise ValueError(f"The elastic notch stress must be finite; got {elastic_mpa!r}.")
    return _solve(Branch.FIRST_LOADING, elastic_mpa, curve, limit_load, backend)


def hysteresis(
    elastic_range_mpa: float,
    curve: CyclicCurve,
    limit_load: LimitLoadFactor,
    *,
    backend: FatigueBackend | None = None,
) -> NotchStrain:
    """The stress and strain ranges on a hysteresis branch for the elastic range `elastic_range_mpa`.

    The branch is the cyclic curve doubled (Masing), which is how pyLife's secondary branch is
    built. It is the closed loop of a constant-amplitude cycle, not a reversal inside a history.
    """
    if not (elastic_range_mpa >= 0.0 and math.isfinite(elastic_range_mpa)):
        raise ValueError(
            f"A stress range is non-negative and finite; got {elastic_range_mpa!r}. The direction of "
            "a reversal is not part of its range."
        )
    return _solve(Branch.HYSTERESIS, elastic_range_mpa, curve, limit_load, backend)


def _solve(
    branch: Branch,
    elastic_mpa: float,
    curve: CyclicCurve,
    limit_load: LimitLoadFactor,
    backend: FatigueBackend | None,
) -> NotchStrain:
    engine = backend if backend is not None else PyLifeBackend()
    stress, strain = engine.extended_neuber(
        elastic_mpa,
        youngs_modulus_mpa=curve.youngs_modulus_mpa,
        strength_coefficient_mpa=curve.strength_coefficient_mpa,
        hardening_exponent=curve.hardening_exponent,
        limit_load_factor=limit_load.value,
        as_range=branch is Branch.HYSTERESIS,
    )
    return NotchStrain(
        branch=branch,
        elastic_mpa=elastic_mpa,
        stress_mpa=stress,
        strain=strain,
        source=(
            f"{EXTENDED_NEUBER}, {branch}, via {engine.version or engine.name}; cyclic curve E = "
            f"{curve.youngs_modulus_mpa:g} MPa, K' = {curve.strength_coefficient_mpa:g} MPa, n' = "
            f"{curve.hardening_exponent:g} [{curve.source}]; K_p = {limit_load.value:g} [{limit_load.source}]"
        ),
    )


__all__ = [
    "EXTENDED_NEUBER",
    "NACA_TN_2805",
    "NEUBER_ACCURACY",
    "Branch",
    "CyclicCurve",
    "LimitLoadFactor",
    "NeuberConstant",
    "NotchStrain",
    "first_loading",
    "hysteresis",
    "neuber_concentration",
    "neuber_sensitivity",
]
