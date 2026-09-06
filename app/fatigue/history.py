"""What is loading the part, over time — and what the numbers in it actually mean.

Phase 8 exists because *structures fail from fatigue, not from a single static
load*. The first thing that has to change, therefore, is the input: a
`LoadCase` (`app/solve/types.py`) is one instant, and a fatigue assessment
needs a **history**. This module is that vocabulary, and it is ours — Decision 2
federates the arithmetic to pyLife and keeps the meaning here.

Two attributes on a history carry more weight than the numbers do, and both are
mandatory because getting either wrong produces a plausible answer that is
wrong by a large factor.

**`basis` — where in the part the stress was read.** An FE stress at a fillet
root *already contains* the stress concentration. Multiplying it by Kt again is
the single most common error in FE-based fatigue and it squares the very factor
the assessment is most sensitive to. So a history says whether it is nominal
(section force over section area, Kt still to be applied), notch-root (a local
FE stress, Kt already in it), or hot-spot (a structural stress extrapolated to
a weld toe, where the notch effect lives inside the detail category instead).
`app.fatigue.assessment` refuses the combinations that would double-count.

**`sign` — whether the history can go negative.** `SolveOutput.von_mises` is a
norm: it is non-negative by construction, so a fully reversed cycle
(+200 MPa → −200 MPa) appears in it as *two* excursions from zero to 200. A
rainflow count of that series reports twice as many cycles at half the range,
and at a Wöhler slope of 5 that is wrong by a factor of ~16 in life. There is no
correction for it — the sign was destroyed upstream — so counting an unsigned
history is refused rather than approximated. `from_von_mises` exists so the
refusal is *visible* at the point somebody reaches for the field that is
available today, instead of appearing as a silently wrong number.

That refusal is a live constraint on Phase E6: to be usable here, the federated
solver has to expose a signed scalar — a signed von Mises (signed by the first
stress invariant), a principal stress, or a stress component along a declared
direction. `app/solve/base.py` does not expose one today.

Units are the codebase's mm-N-MPa throughout: stresses in MPa, and nothing in
this package converts. A collective read out of a standard in stress *range*
Δσ is halved to an amplitude at the boundary where it is read (see
`material.WeldDetail`), never deeper in.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.fatigue.sources import require_source

#: The stress at which a history is so nearly constant that counting it is
#: meaningless. Not a tolerance on the answer — a guard against handing the
#: counter a flat line and reading its empty result as "no damage", which is
#: true but is a statement about the input, not about the part.
FLAT_HISTORY_MPA: float = 1e-9


class StressBasis(StrEnum):
    """Where in the part the stresses of a history or collective were read.

    The three values are not degrees of quality; they select *which* method the
    assessment is entitled to use, and the assessment refuses a combination
    that would apply a notch effect twice.
    """

    #: Section stress, with no local geometric effect in it. A stress
    #: concentration is *required* before this can be assessed.
    NOMINAL = "nominal"

    #: A local elastic stress at the notch root, as an FE model reports it.
    #: Kt is already inside the number; applying one is refused.
    NOTCH_ROOT = "notch_root"

    #: Structural (geometric) stress extrapolated to a weld toe. The notch
    #: effect of the weld is inside the detail category of the S-N curve, so
    #: applying a concentration is refused here too.
    HOT_SPOT = "hot_spot"


class SignConvention(StrEnum):
    """Whether a history's numbers can go negative.

    `UNSIGNED` is not a lesser form of `SIGNED`; it is a different quantity, and
    the module docstring says why it cannot be rainflow counted.
    """

    SIGNED = "signed"
    UNSIGNED = "unsigned"


@dataclass(frozen=True)
class LoadHistory:
    """A stress-time history at one point, in MPa.

    Not a time *series*: the sample times are not carried, because rainflow
    counting is rate-independent and storing times would invite somebody to
    read a frequency out of a signal that has none. If a rate matters — creep,
    a frequency-dependent weld correction — it belongs in the duty cycle
    (`Assessment.design_life_blocks`), stated, not inferred here.

    `values_mpa` is one *block*: the shortest sequence that repeats over the
    life. How many times it repeats is the assessment's business, not the
    history's, so that one history can serve several duty cycles.
    """

    name: str
    values_mpa: tuple[float, ...]
    basis: StressBasis
    sign: SignConvention
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A load history"))
        if len(self.values_mpa) < 3:
            raise ValueError(
                "A load history needs at least three points — two turning points and the "
                f"reversal between them — before any cycle can close in it; got "
                f"{len(self.values_mpa)}."
            )
        if not all(math.isfinite(v) for v in self.values_mpa):
            raise ValueError(
                "A load history contains a non-finite stress. A NaN here propagates "
                "silently through the count into a damage sum that still looks like a "
                "number, which is why it is refused at the boundary."
            )

    @classmethod
    def from_values(
        cls,
        name: str,
        values_mpa: Iterable[float],
        *,
        basis: StressBasis,
        sign: SignConvention,
        source: str,
    ) -> LoadHistory:
        return cls(
            name=name,
            values_mpa=tuple(float(v) for v in values_mpa),
            basis=basis,
            sign=sign,
            source=source,
        )

    @classmethod
    def from_von_mises(
        cls, name: str, values_mpa: Iterable[float], *, source: str
    ) -> LoadHistory:
        """A history taken from `SolveOutput.von_mises` — deliberately `UNSIGNED`.

        This constructor exists to make a wrong assessment impossible rather
        than to enable one. Von Mises is a norm, so the history it produces
        cannot be rainflow counted (module docstring), and `Assessment.run`
        will return `UNMEASURED` naming the sign convention. That refusal is
        the correct outcome today; the fix is upstream, in what the solver
        federation chooses to expose.

        The basis is `NOTCH_ROOT`, because an element stress from a meshed
        model is a local stress by construction.
        """
        return cls.from_values(
            name,
            values_mpa,
            basis=StressBasis.NOTCH_ROOT,
            sign=SignConvention.UNSIGNED,
            source=source,
        )

    @property
    def peak_to_peak_mpa(self) -> float:
        return max(self.values_mpa) - min(self.values_mpa)

    @property
    def is_flat(self) -> bool:
        return self.peak_to_peak_mpa <= FLAT_HISTORY_MPA


@dataclass(frozen=True)
class CycleBlock:
    """One row of a load collective: `cycles` repetitions at this amplitude and mean.

    Amplitude, not range. Standards are written both ways — Eurocode 3 and
    BS 7608 are in range Δσ, S-N curves for unwelded metal are usually in
    amplitude σa — and the halving happens once, at the point the standard is
    read (`material.WeldDetail.eurocode_3`), never twice and never deeper in.
    """

    amplitude_mpa: float
    mean_mpa: float
    cycles: float

    def __post_init__(self) -> None:
        if self.amplitude_mpa < 0.0:
            raise ValueError("A cycle amplitude is a half-range and cannot be negative.")
        if self.cycles < 0.0:
            raise ValueError("A cycle count cannot be negative.")
        for value, what in ((self.amplitude_mpa, "amplitude"), (self.mean_mpa, "mean stress")):
            if not math.isfinite(value):
                raise ValueError(f"A cycle block's {what} must be finite.")

    @property
    def range_mpa(self) -> float:
        return 2.0 * self.amplitude_mpa

    @property
    def stress_ratio(self) -> float | None:
        """R = σ_min / σ_max, or None where σ_max is zero and R is undefined."""
        maximum = self.mean_mpa + self.amplitude_mpa
        if maximum == 0.0:
            return None
        return (self.mean_mpa - self.amplitude_mpa) / maximum

    def scaled(self, factor: float) -> CycleBlock:
        """Both amplitude and mean multiplied — what applying an elastic Kf does.

        The mean concentrates with the amplitude: at an elastically-behaving
        notch the whole local stress is the nominal stress times Kf, not only
        its alternating part. Scaling the amplitude alone would understate the
        mean stress and therefore the correction that follows it.
        """
        return CycleBlock(
            amplitude_mpa=self.amplitude_mpa * factor,
            mean_mpa=self.mean_mpa * factor,
            cycles=self.cycles,
        )


@dataclass(frozen=True)
class Collective:
    """A counted load collective: what a history becomes, or what a duty cycle is given as.

    Carries the same `basis` and `sign` as the history it came from, because
    those constrain what may be done to it downstream and losing them at the
    counting step would put the double-counting guard out of reach.

    `residue` is the part of a three-point rainflow count that never closed —
    the turning points left on the stack. It is reported and *not* silently
    counted: for a block that repeats over a life the right treatment is to
    count the block twice and take the difference, and for a one-shot history
    the residue is genuinely uncounted damage. Either way the number of
    unclosed reversals belongs in the result, and `Assessment` puts it there as
    a stated assumption.
    """

    blocks: tuple[CycleBlock, ...]
    basis: StressBasis
    sign: SignConvention
    source: str
    counting_method: str = ""
    residue_mpa: tuple[float, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", require_source(self.source, "A load collective"))

    @classmethod
    def from_blocks(
        cls,
        blocks: Sequence[tuple[float, float, float]],
        *,
        basis: StressBasis,
        sign: SignConvention = SignConvention.SIGNED,
        source: str,
        counting_method: str = "",
    ) -> Collective:
        """Build a collective from (amplitude, mean, cycles) triples.

        The route a *duty cycle* takes: an engineer stating "500 000 cycles at
        ±120 MPa about 40 MPa, then 20 000 at ±260 MPa about 0" has already
        done the counting, so `counting_method` is empty by default and the
        result will say the collective was declared rather than counted.
        """
        return cls(
            blocks=tuple(
                CycleBlock(amplitude_mpa=a, mean_mpa=m, cycles=n) for a, m, n in blocks
            ),
            basis=basis,
            sign=sign,
            source=source,
            counting_method=counting_method,
        )

    @property
    def total_cycles(self) -> float:
        return float(sum(b.cycles for b in self.blocks))

    @property
    def has_mean_stress(self) -> bool:
        """Whether any block sits off zero mean, i.e. whether a correction is owed."""
        return any(b.mean_mpa != 0.0 for b in self.blocks)

    @property
    def largest_amplitude_mpa(self) -> float:
        return max((b.amplitude_mpa for b in self.blocks), default=0.0)

    def scaled(self, factor: float) -> Collective:
        """Every block scaled — how a stress concentration reaches a collective."""
        return Collective(
            blocks=tuple(b.scaled(factor) for b in self.blocks),
            basis=self.basis,
            sign=self.sign,
            source=self.source,
            counting_method=self.counting_method,
            residue_mpa=self.residue_mpa,
        )


__all__ = [
    "FLAT_HISTORY_MPA",
    "Collective",
    "CycleBlock",
    "LoadHistory",
    "SignConvention",
    "StressBasis",
]
