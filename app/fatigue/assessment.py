"""Putting it together — and refusing to, when an input a method needs is missing.

This is the module that is *ours*. `backend.py` federates the arithmetic (Decision 2);
everything decided here is a judgement about method, and the whole design is arranged
so that a judgement nobody made shows up as a refusal with a name on it rather than as
a number.

**A fatigue number without its method attached is not a result.** `DamageResult`
therefore cannot be constructed without a `Method`, and `Method` cannot be constructed
without a damage rule. The counting algorithm, the damage rule, the mean-stress
correction, the library and version, and the standard if one was applied all travel
with the number, because a damage of 0.6 means nothing until you know it came from a
three-point rainflow count under Miner-original against a median curve.

**Three outcomes, not two**, reusing `app.design.assertions.Outcome` rather than
inventing a parallel vocabulary: `PASSED`, `FAILED`, `UNMEASURED`. The recoveries are
different — a failure means change the design, an unmeasured result means go and get
the input — so they are never merged, and `UNMEASURED` always names what was missing.
The provenance sidecar is `app.kernel.provenance`, likewise reused; see
`_provenance()` for why it is imported on use.

## What is refused, and why each one is a real defect and not pedantry

* **The backend is not installed.** No arithmetic ran; a life would be fiction.
* **A required factor is absent** (`factors.REQUIRED_FACTORS`). An empty factor set
  multiplies to 1.0 exactly as a complete set of unity factors does, and the two mean
  opposite things.
* **A nominal-stress history with no stress concentration.** Nominal stress by
  definition excludes the local effect; assessing it as if it were the local stress
  understates the stress at the very place the crack starts.
* **A stress concentration supplied against a notch-root or hot-spot history.** The
  concentration is already inside those numbers. Applying it again squares the factor
  the answer is most sensitive to — at k1 = 5, a spurious Kt of 2.5 is a factor of
  ~100 in life, in the unsafe direction.
* **Mean stress present with no mean-stress sensitivity.** Correcting with M = 0 is
  not "no correction", it is the claim that mean stress does not matter, which is
  false for every metal and very false in tension.
* **A survival probability asked of a curve with no scatter.** There is no
  transformation from a median curve to a design curve without the scatter band.
* **An unsigned history.** `history.py` explains it; the count would be wrong by a
  factor, not by a margin.

## What a qualified engineer still decides — the honest boundary (Decision 5)

Nothing in this package signs anything, and the list below is not a disclaimer, it is
the actual set of inputs that decide the answer. Every one of them is a constructor
argument carrying a `source`, and every one is reproduced in `DamageResult.inputs` so
the reviewer reads them next to the number:

1. **The S-N curve** — which data, at what survival probability, for this heat, this
   process, this environment (`material.SNCurve`, `source`).
2. **What happens below the knee** — Miner original, elementary, or Haibach
   (`SNCurve.slope_k2`, `SNCurve.with_haibach_slope`).
3. **The weld detail category**, where there is a weld — the judgement Phase 8.3 is
   marked *needs an ME* for (`material.WeldDetail`).
4. **Surface, size, temperature and any other strength factor**, and which chart they
   were read from (`factors.Factor.source`).
5. **Kt, and the notch sensitivity that turns it into Kf** (`factors.StressConcentration`).
6. **The mean-stress model and its sensitivity M**, or the declaration that mean
   stress is irrelevant here and why (`MeanStressPolicy`).
7. **The duty cycle** — how many times the counted block occurs over the design life,
   and whether that block is representative (`Assessment.design_life_blocks`).
8. **The damage limit** — Miner's rule is calibrated at D = 1 but is routinely applied
   at 0.5 or 0.2 for critical parts (`Assessment.damage_limit`).

A `PASSED` verdict from this module means *the declared inputs, summed by the declared
rule, give a damage below the declared limit*. It is not a certification, and no
prompt, docstring or user-facing string in this package may present it as one.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.design.assertions import Outcome
from app.fatigue.backend import FatigueBackend, PyLifeBackend
from app.fatigue.backend import available as backend_available
from app.fatigue.backend import import_error as backend_import_error
from app.fatigue.factors import REQUIRED_FACTORS, FactorSet, StressConcentration
from app.fatigue.history import Collective, LoadHistory, SignConvention, StressBasis
from app.fatigue.material import SNCurve

#: The damage rule, named once. Every result carries it, including the refused
#: ones — knowing what *would* have been done is part of reading a refusal.
MINER_RULE = "Palmgren–Miner linear damage accumulation (D = Σ n_i / N_i)"

#: Where a damage result's provenance sidecar records the damage number itself.
DAMAGE_PATH = "damage"

#: …and the life in blocks derived from it.
LIFE_PATH = "life_blocks"


class MeanStressPolicy(StrEnum):
    """What to do about the mean stress in a collective.

    There is no `IGNORE`. Ignoring a mean stress silently is the failure mode
    this enum exists to prevent, so the only way to proceed without a correction
    is `DECLARED_IRRELEVANT`, which demands a written justification and prints it
    in the result.
    """

    #: Transform every block to its R = −1 equivalent using the material's M.
    #: Refuses when the material declares no mean-stress sensitivity.
    CORRECT = "correct"

    #: Proceed uncorrected because the method already accounts for mean stress —
    #: the honest case being an as-welded joint assessed to EN 1993-1-9, where
    #: residual stress at yield makes the applied mean immaterial. Requires
    #: `mean_stress_justification`.
    DECLARED_IRRELEVANT = "declared_irrelevant"


class Confidence(StrEnum):
    """How well characterised the *inputs* were. Not a statistical confidence.

    This is bookkeeping over provenance, and it is deliberately coarse: any
    finer scale would look like a probability and be read as one. It says
    nothing about whether the model is right — a perfectly characterised curve
    applied to the wrong location is still wrong.
    """

    #: Nothing was computed.
    NONE = "none"

    #: A median or otherwise unqualified curve with no declared scatter. Usable
    #: for ranking two designs against each other; not for a life claim.
    SCREENING = "screening"

    #: The curve declares its scatter and the result was produced at a stated
    #: survival probability, and every factor the method requires was supplied
    #: with a source.
    CHARACTERISED = "characterised"


@dataclass(frozen=True)
class Method:
    """How a damage number was arrived at. A result cannot exist without one.

    `damage_rule` has no default and is refused empty: it is the one field that
    is always known, even for a refusal, and a `Method` that says nothing is
    indistinguishable from no method at all.
    """

    damage_rule: str
    counting: str = ""
    mean_stress_correction: str = "none"
    library: str = ""
    standard: str = ""

    def __post_init__(self) -> None:
        if not self.damage_rule.strip():
            raise ValueError(
                "A fatigue result must name the damage rule it used. A number with no "
                "method attached is not a result, and this is the field that makes the "
                "difference — a Miner sum and a critical-plane damage are not comparable "
                "quantities even when they print the same."
            )

    def to_dict(self) -> dict[str, str]:
        out = {"damage_rule": self.damage_rule}
        for key, value in (
            ("counting", self.counting),
            ("mean_stress_correction", self.mean_stress_correction),
            ("library", self.library),
            ("standard", self.standard),
        ):
            if value:
                out[key] = value
        return out

    def describe(self) -> str:
        parts = [self.damage_rule]
        if self.counting:
            parts.append(f"cycles counted by {self.counting}")
        parts.append(f"mean stress: {self.mean_stress_correction}")
        if self.standard:
            parts.append(f"standard: {self.standard}")
        if self.library:
            parts.append(f"library: {self.library}")
        return "; ".join(parts)


@dataclass(frozen=True)
class BlockDamage:
    """One row of the answer: what this part of the duty cycle costs.

    Per-block damage is not decoration. A Miner sum is dominated by one or two
    blocks almost every time, and knowing which is the difference between "the
    part is 30% over" and "the part is 30% over *because of the 200 overload
    cycles at commissioning*, which are not part of normal service".
    """

    amplitude_mpa: float
    mean_mpa: float
    cycles: float
    cycles_to_failure: float
    damage: float

    @property
    def fraction_of(self) -> float:
        """Damage as a fraction of one — i.e. what this block alone would allow."""
        return self.damage


@dataclass(frozen=True)
class DamageResult:
    """A damage number bound to the method, assumptions and inputs that produced it.

    `outcome` first and `method` second, both without defaults, so the two things
    that must never be absent cannot be omitted at a call site.

    `damage` is `None` for an `UNMEASURED` result — not `0.0`. Zero damage is a
    real and reachable answer (every amplitude below the endurance limit under
    Miner original) and conflating it with "not assessed" would turn the most
    dangerous case into the most reassuring one.
    """

    outcome: Outcome
    method: Method
    damage: float | None = None
    damage_limit: float = 1.0
    blocks: tuple[BlockDamage, ...] = ()
    life_blocks: float | None = None
    confidence: Confidence = Confidence.NONE
    assumptions: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    failure_probability: float | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.outcome is Outcome.UNMEASURED and not self.missing:
            raise ValueError(
                "An unmeasured fatigue result must name the input it is missing. "
                "'Could not assess' with no subject is the message that sends an "
                "engineer looking through the whole model."
            )
        if self.outcome is not Outcome.UNMEASURED and self.damage is None:
            raise ValueError(
                "A passed or failed fatigue result must carry the damage it was decided "
                "on; a verdict with no number behind it cannot be reviewed."
            )

    @property
    def assessed(self) -> bool:
        """Whether a damage number exists at all. Never read this as a pass."""
        return self.outcome is not Outcome.UNMEASURED

    def to_payload(self) -> dict[str, Any]:
        """The result as a measurement payload with an `app.kernel.provenance` sidecar.

        The damage is recorded as **`APPROXIMATED`, never `MEASURED`**, and the
        method text says why: a Miner sum is a model prediction over a counted
        collective, not a quantity anybody integrated off the part. That is a
        deliberate reuse rather than a fourth basis — `APPROXIMATED` already
        means "the number depends on how it was computed and is not exact", and
        adding a `PREDICTED` basis would fork a vocabulary three other packages
        already read.

        An `UNMEASURED` result records `UNAVAILABLE` with the missing inputs as
        its reason, so an assertion written against `damage` comes back
        `UNMEASURED` *and can say why*.
        """
        provenance = _provenance()
        payload: dict[str, Any] = {
            "method": self.method.to_dict(),
            "confidence": str(self.confidence),
            "outcome": str(self.outcome),
        }
        if self.assumptions:
            payload["assumptions"] = list(self.assumptions)
        if self.inputs:
            payload["inputs"] = list(self.inputs)
        if self.failure_probability is not None:
            payload["failure_probability"] = self.failure_probability
        if self.damage is None:
            reason = "not assessed: " + "; ".join(self.missing)
            provenance.attach(payload, DAMAGE_PATH, provenance.unavailable(reason))
            provenance.attach(payload, LIFE_PATH, provenance.unavailable(reason))
            return payload
        payload[DAMAGE_PATH] = self.damage
        provenance.attach(
            payload,
            DAMAGE_PATH,
            provenance.approximated(self.method.describe()),
        )
        if self.life_blocks is not None:
            payload[LIFE_PATH] = self.life_blocks
            provenance.attach(
                payload,
                LIFE_PATH,
                provenance.approximated(
                    f"{self.method.damage_rule}, inverted at a damage limit of "
                    f"{self.damage_limit:g}"
                ),
            )
        return payload

    def summary(self) -> str:
        """One line for a log or a chat turn. Never claims more than the verdict does."""
        if self.outcome is Outcome.UNMEASURED:
            return "Fatigue not assessed — missing: " + "; ".join(self.missing)
        assert self.damage is not None
        life = (
            "life beyond the assessed spectrum"
            if self.life_blocks is None or math.isinf(self.life_blocks)
            else f"{self.life_blocks:.3g} repetitions of the duty cycle to the limit"
        )
        verdict = "within" if self.outcome is Outcome.PASSED else "over"
        return (
            f"Damage {self.damage:.4g} against a limit of {self.damage_limit:g} "
            f"({verdict}); {life}. {self.method.describe()}."
        )


@dataclass(frozen=True)
class Assessment:
    """One fatigue check: a load, a curve, the factors between them, and a duty cycle.

    Frozen and side-effect free, like the rest of this package, so an optimiser
    can build ten thousand of these and `run` them without any of them touching
    a database, a socket or a seat.

    `loading` takes either a `LoadHistory` — which is counted here — or an
    already-counted `Collective`, which is how a duty cycle stated as blocks
    ("500 000 at ±120 about 40") enters. The result says which it was.
    """

    loading: LoadHistory | Collective
    curve: SNCurve
    factors: FactorSet = field(default_factory=FactorSet)
    concentration: StressConcentration | None = None
    #: How many times the counted block occurs over the design life.
    design_life_blocks: float = 1.0
    #: Miner is calibrated at 1.0; 0.5 or 0.2 are common on critical parts, and
    #: choosing one is a judgement, so it is stated rather than assumed away.
    damage_limit: float = 1.0
    mean_stress_policy: MeanStressPolicy = MeanStressPolicy.CORRECT
    mean_stress_justification: str = ""
    #: The survival probability to assess at. `None` uses the curve as given and
    #: reports the probability the curve itself declares.
    failure_probability: float | None = None
    required_factors: frozenset[str] = REQUIRED_FACTORS
    #: Free text naming what is being assessed — "web-to-flange fillet, station 3".
    location: str = ""

    def __post_init__(self) -> None:
        if not (self.design_life_blocks > 0.0 and math.isfinite(self.design_life_blocks)):
            raise ValueError(
                "The design life is a positive, finite number of repetitions of the "
                "counted block. An infinite design life is a conclusion, not an input."
            )
        if not (0.0 < self.damage_limit <= 1.0):
            raise ValueError(
                "A Miner damage limit lies in (0, 1]. Above 1 it is not a limit; at or "
                "below 0 nothing can pass."
            )

    def run(self, backend: FatigueBackend | None = None) -> DamageResult:
        """Assess, or refuse and say what is missing. Never raises for a missing input."""
        assumptions: list[str] = []
        inputs: list[str] = list(self._describe_inputs())
        standard = self.curve.standard

        if backend is None:
            if not backend_available():
                return _unmeasured(
                    Method(damage_rule=MINER_RULE, standard=standard),
                    missing=("fatigue backend (pyLife): " + backend_import_error(),),
                    inputs=inputs,
                )
            backend = PyLifeBackend()

        method_library = getattr(backend, "version", "") or getattr(backend, "name", "")

        # -- the collective ------------------------------------------------
        if isinstance(self.loading, LoadHistory):
            if self.loading.sign is SignConvention.UNSIGNED:
                return _unmeasured(
                    Method(damage_rule=MINER_RULE, library=method_library, standard=standard),
                    missing=(
                        "a signed stress history — this one is unsigned (a von Mises norm "
                        "or similar) and rainflow counting it would report every reversal "
                        "as two half-range cycles",
                    ),
                    inputs=inputs,
                )
            if self.loading.is_flat:
                return _unmeasured(
                    Method(damage_rule=MINER_RULE, library=method_library, standard=standard),
                    missing=(
                        "a varying stress history — this one is constant, so it contains "
                        "no cycles to count and says nothing about fatigue",
                    ),
                    inputs=inputs,
                )
            collective = backend.count(self.loading)
        else:
            collective = self.loading

        counting = collective.counting_method or "declared as blocks, not counted here"
        if collective.residue_mpa:
            assumptions.append(
                f"{len(collective.residue_mpa)} unclosed reversals were left as a residue "
                "and are not counted. For a block that repeats over the life, count two "
                "consecutive blocks and take the difference; for a one-shot history this "
                "is uncounted damage."
            )

        # -- the notch -----------------------------------------------------
        refusal = self._check_concentration(collective.basis)
        if refusal is not None:
            return _unmeasured(
                Method(
                    damage_rule=MINER_RULE,
                    counting=counting,
                    library=method_library,
                    standard=standard,
                ),
                missing=(refusal,),
                inputs=inputs,
            )
        if self.concentration is not None:
            collective = collective.scaled(self.concentration.kf)
            if self.concentration.assumed_full_sensitivity:
                assumptions.append(
                    "Kf was taken equal to Kt: full notch sensitivity assumed, which is "
                    "the conservative bound. Supplying a notch sensitivity q for this "
                    "material and root radius will usually lengthen the predicted life."
                )

        # -- the factors ---------------------------------------------------
        missing_factors = self.factors.missing_from(sorted(self.required_factors))
        if missing_factors:
            return _unmeasured(
                Method(
                    damage_rule=MINER_RULE,
                    counting=counting,
                    library=method_library,
                    standard=standard,
                ),
                missing=tuple(
                    f"the {name} factor, with the chart or test it was read from"
                    for name in missing_factors
                ),
                inputs=inputs,
            )

        curve = self.curve
        if self.factors.factors:
            curve = curve.scaled(
                self.factors.value,
                "; ".join(self.factors.describe()),
            )
            assumptions.append(
                "Strength factors were applied by scaling the endurance amplitude with "
                "the knee cycle count held fixed, which shifts the finite-life line "
                "parallel to itself. See SNCurve.scaled for the alternative construction "
                "and why this one was chosen."
            )

        # -- the survival probability --------------------------------------
        if self.failure_probability is not None:
            if not curve.has_scatter:
                return _unmeasured(
                    Method(
                        damage_rule=MINER_RULE,
                        counting=counting,
                        library=method_library,
                        standard=standard,
                    ),
                    missing=(
                        "the scatter of the S-N data (TN or TS) — without it the curve "
                        f"cannot be restated at a failure probability of "
                        f"{self.failure_probability:g}",
                    ),
                    inputs=inputs,
                )
            curve = backend.at_failure_probability(curve, self.failure_probability)

        # -- the mean stress -----------------------------------------------
        mean_correction = "none — every counted cycle is fully reversed"
        if collective.has_mean_stress:
            if self.mean_stress_policy is MeanStressPolicy.DECLARED_IRRELEVANT:
                if not self.mean_stress_justification.strip():
                    return _unmeasured(
                        Method(
                            damage_rule=MINER_RULE,
                            counting=counting,
                            library=method_library,
                            standard=standard,
                        ),
                        missing=(
                            "a written justification for treating mean stress as "
                            "irrelevant here — the policy is allowed (an as-welded joint "
                            "to EN 1993-1-9 is the honest case) but not silently",
                        ),
                        inputs=inputs,
                    )
                mean_correction = (
                    "not applied, declared irrelevant: "
                    f"{self.mean_stress_justification.strip()}"
                )
                assumptions.append(
                    "The collective carries non-zero mean stress which was deliberately "
                    "not corrected for. " + self.mean_stress_justification.strip()
                )
            elif curve.mean_stress_sensitivity is None:
                return _unmeasured(
                    Method(
                        damage_rule=MINER_RULE,
                        counting=counting,
                        library=method_library,
                        standard=standard,
                    ),
                    missing=(
                        "the material's mean-stress sensitivity M — the collective has "
                        "non-zero mean stress, and assessing it with M = 0 is not 'no "
                        "correction', it is the claim that mean stress does not matter",
                    ),
                    inputs=inputs,
                )
            else:
                m = curve.mean_stress_sensitivity
                collective = backend.correct_mean_stress(collective, m=m, m2=None)
                mean_correction = (
                    f"FKM-Goodman Haigh diagram, M = {m:.4g}, M2 = M/3, transformed to R = −1"
                )

        # -- the damage ----------------------------------------------------
        if curve.slope_k2 is None:
            assumptions.append(
                "Amplitudes below the endurance limit were treated as doing no damage "
                "(Miner original). Under a spectrum containing amplitudes above the knee "
                "this is non-conservative; SNCurve.with_haibach_slope applies k2 = 2·k1−1 "
                "instead, and choosing between them is an engineering judgement."
            )

        per_block = backend.damage(collective, curve)
        block_results: list[BlockDamage] = []
        for block, damage in zip(collective.blocks, per_block, strict=True):
            block_results.append(
                BlockDamage(
                    amplitude_mpa=block.amplitude_mpa,
                    mean_mpa=block.mean_mpa,
                    cycles=block.cycles * self.design_life_blocks,
                    cycles_to_failure=backend.cycles_to_failure(curve, block.amplitude_mpa),
                    damage=damage * self.design_life_blocks,
                )
            )
        total = float(sum(b.damage for b in block_results))

        per_repetition = float(sum(per_block))
        life = math.inf if per_repetition == 0.0 else self.damage_limit / per_repetition

        method = Method(
            damage_rule=MINER_RULE,
            counting=counting,
            mean_stress_correction=mean_correction,
            library=method_library,
            standard=standard,
        )
        return DamageResult(
            outcome=Outcome.PASSED if total <= self.damage_limit else Outcome.FAILED,
            method=method,
            damage=total,
            damage_limit=self.damage_limit,
            blocks=tuple(block_results),
            life_blocks=life,
            confidence=self._confidence(curve),
            assumptions=tuple(assumptions),
            inputs=tuple(inputs),
            failure_probability=curve.failure_probability,
            detail=self.location,
        )

    # -- internals ---------------------------------------------------------

    def _check_concentration(self, basis: StressBasis) -> str | None:
        """The double-counting guard, in one place. Returns the refusal or None."""
        if basis is StressBasis.NOMINAL and self.concentration is None:
            return (
                "a stress concentration factor — the stress history is nominal, so the "
                "local effect of the notch is not in it yet and assessing it as-is "
                "understates the stress exactly where the crack starts"
            )
        if basis is not StressBasis.NOMINAL and self.concentration is not None:
            where = "at the notch root" if basis is StressBasis.NOTCH_ROOT else "a hot-spot stress"
            carrier = (
                "the FE stress" if basis is StressBasis.NOTCH_ROOT else "the detail category"
            )
            return (
                f"a nominal-stress history to apply the given concentration to — the "
                f"history supplied is already {where}, so the concentration is inside "
                f"{carrier} and applying it again squares it. Either drop the "
                "concentration or supply nominal stresses"
            )
        return None

    def _confidence(self, curve: SNCurve) -> Confidence:
        if curve.has_scatter and self.failure_probability is not None:
            return Confidence.CHARACTERISED
        return Confidence.SCREENING

    def _describe_inputs(self) -> tuple[str, ...]:
        """Every declared input, in the words its source was written in.

        Reproduced on the result so the reviewer reads the judgements next to the
        number rather than having to reconstruct the call that produced it.
        """
        described = [f"S-N curve: {self.curve.source}"]
        if self.curve.mean_stress_sensitivity is not None:
            described.append(f"mean-stress sensitivity M = {self.curve.mean_stress_sensitivity:.4g}")
        described.extend(f"factor {text}" for text in self.factors.describe())
        if self.concentration is not None:
            described.append(f"concentration {self.concentration.describe()}")
        described.append(f"loading: {self.loading.source}")
        described.append(f"duty cycle: {self.design_life_blocks:g} repetitions of the block")
        described.append(f"damage limit: {self.damage_limit:g}")
        if self.location:
            described.append(f"location: {self.location}")
        return tuple(described)


def _unmeasured(
    method: Method, *, missing: Sequence[str], inputs: Iterable[str]
) -> DamageResult:
    return DamageResult(
        outcome=Outcome.UNMEASURED,
        method=method,
        missing=tuple(missing),
        inputs=tuple(inputs),
        confidence=Confidence.NONE,
    )


def _provenance() -> Any:
    """`app.kernel.provenance`, imported on use rather than at module load.

    Same reason `app.design.assertions._provenance` does it: importing
    `app.kernel` pulls OCP — ~166 MB and about a second — into a package whose
    load-bearing property is that its tests run offline and fast. This package
    depends on the *vocabulary* of provenance, not on the geometry kernel, and
    paying for the kernel to borrow three words would be the wrong trade.
    """
    from app.kernel import provenance

    return provenance


__all__ = [
    "DAMAGE_PATH",
    "LIFE_PATH",
    "MINER_RULE",
    "Assessment",
    "BlockDamage",
    "Confidence",
    "DamageResult",
    "MeanStressPolicy",
    "Method",
]
