"""Tolerance stack-up: exact arithmetic, and an honest statistical result or none.

Phase 13.2. A chain of dimensions closes on a gap; each dimension has a
tolerance; the question is what the gap can actually be. The arithmetic is
exact and needs no engineer's judgement — **which is why it is here and the cost
model is not.** What *does* need judgement is whether a statistical result is
admissible at all, and this module's entire design is about not letting that
judgement be made silently.

**Two methods, and they answer different questions.**

* **Worst case** — every contributor sits at its limit at once, in the direction
  that hurts. It is interval arithmetic: exact, assumption-free, and true of
  every part that could ever be made to the drawing. It is what a
  safety-critical fit, a single-piece build or a no-rework assembly is designed
  to. It is also often two to three times wider than anything a production run
  will show, and designing to it can cost tolerances nobody needed to buy.
* **Statistical (RSS)** — variances add, so the band is the root sum of squares
  of the contributions rather than their arithmetic sum. It is what a production
  run *distribution* looks like. It is a **prediction about a population**, not
  a bound on a part: some assemblies land outside it, by design, and the whole
  point of choosing it is accepting that in exchange for wider component
  tolerances.

Concretely, three contributors at ±0.1 mm: worst case ±0.30 mm, RSS ±0.173 mm.
The RSS number is 42% smaller, and the parts it describes are the same parts.

**RSS is refused unless the assumptions behind it are on the record.** A stack
that prints an RSS number is going to be believed, so the four things it rests on
are checked and any that cannot be established become **refusals**, not
footnotes:

1. **Independence.** Variances add only for independent contributors. Two
   dimensions cut in one setup on one machine are not independent, and no data
   in this module can reveal that — it is a judgement about the process plan.
   Every statistical stack therefore carries this as an acknowledged risk, which
   is why one cannot be produced anonymously: `acknowledged_by` is required.
2. **Process capability.** RSS needs to know each contributor's spread. Without
   a capability study the standard premise is "the tolerance band is ±3σ and the
   process is centred" — which is a *statement about a factory*, not a fact, and
   is precisely the assumption that makes an RSS number optimistic when it is
   wrong. Supply `Capability` per contributor, or acknowledge
   `Risk.NO_CAPABILITY_DATA` and wear the assumption in writing.
3. **Distribution shape.** Variances add whatever the shape, so the *arithmetic*
   survives a non-normal contributor; what does not survive is reading ±3σ as
   99.73%. A uniform distribution has σ = t/√3, not t/3, and this module uses
   the right divisor when told the shape — but the interpretation of the band
   still needs acknowledging.
4. **Enough contributors.** The sum of a few random variables is not normal, so
   the central-limit argument that makes the band's tails meaningful has not
   kicked in. Below `MINIMUM_CONTRIBUTORS_FOR_RSS` this is a refusal.

An acknowledged risk is not a suppressed one: it is copied into `caveats`, into
`explain()`, and into `to_dict()`, beside the name of whoever signed for it.

**Asymmetric tolerances are handled exactly, not symmetrised.** A `+0.05/−0.02`
contributor has its interval `[nom−0.02, nom+0.05]`; the worst case sums the
intervals, and the statistical model splits it into a mean at
`nom + (plus−minus)/2` and a half-width of `(plus+minus)/2`, which is the only
correct way to give it a variance. Quietly reading it as ±0.05 would move the
predicted mean of the whole chain.

**Off-centre processes shift the mean, and the shift does not RSS.** When
`cpk < cp`, the process is running off the middle of its band by
`half × (1 − cpk/cp)`. That offset is a bias, not a random variation, and in
what direction is unknown — so it is summed arithmetically onto the statistical
half-width rather than being added in quadrature. With `cpk == cp` the term
vanishes and the result reduces exactly to textbook RSS, which is the property
`tests/test_rules_stackup.py` pins against a hand computation.

**Units are millimetres and nothing converts** (project rule). A contributor
whose real variation is angular enters through `sensitivity`, which is the
linearised millimetres of gap per millimetre of that dimension: that
linearisation is the engineer's, this module only multiplies by it.

**What this does not do.** It does not select fits (ISO 286 tables are adopted
data an ME chooses from, not arithmetic), it does not find the chain — somebody
declares the contributors, and choosing them wrongly is the commonest way a real
stack-up is wrong — and it does not compute sensitivities from geometry. It also
does not convert a geometric tolerance into a contributor: bonus tolerance under
MMC depends on the as-produced feature size, and that is inspection data, not
model data (see `app.rules.gdt`).

`app/design/machine_checks.py::StackUp` is the assertion-shaped surface of the
same idea and stays as it is; it is `MachineCheck`-shaped so it can run inside
the Phase-5 report. This is the arithmetic underneath it — asymmetric
tolerances, sensitivities, capability, and a refusal instead of an unearned RSS.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.design.assertions import Outcome
from app.rules.errors import SourceError, StackUpError

#: Below this many contributors the central-limit argument behind reading an RSS
#: band as a population fraction has not started to apply, so the band is a
#: number with no interpretation. Four is the common floor in the tolerance
#: literature; it is an **adopted convention, not a theorem**, which is why it is
#: a named constant that a caller can acknowledge past rather than a hidden test.
MINIMUM_CONTRIBUTORS_FOR_RSS: Final = 4

#: How many standard deviations the reported statistical band spans, each side.
#: 3.0 is the classic choice and corresponds to 2700 parts per million outside
#: the band *if* the total is normal. A company designing to six sigma passes
#: 6.0. It is an argument to `stack()` rather than a constant in the arithmetic
#: because it is a business decision about acceptable escape rate.
DEFAULT_SIGMA_MULTIPLE: Final = 3.0

#: Process capability below which the premise "the tolerance band contains the
#: process" is simply false — the spread is wider than the drawing allows, and a
#: statistical prediction built on it understates the gap.
MINIMUM_CAPABILITY: Final = 1.0


class Method(StrEnum):
    """How the contributions are combined."""

    #: Interval arithmetic. Exact, assumption-free, never refused.
    WORST_CASE = "worst_case"

    #: Variances add. A prediction about a population, admissible only with the
    #: assumptions in `Risk` established or acknowledged.
    STATISTICAL = "statistical"


class Distribution(StrEnum):
    """The shape a contributor's variation is assumed to take.

    Declared rather than assumed, because the divisor from tolerance to sigma
    depends on it and getting it wrong is a silent factor of 1.7.
    """

    NORMAL = "normal"
    UNIFORM = "uniform"
    TRIANGULAR = "triangular"


#: Tolerance half-width to one standard deviation, per shape, at unit capability.
#: Normal: the ±3σ convention. Uniform: σ = t/√3 exactly. Triangular: σ = t/√6
#: exactly. The two exact ones are why a declared non-normal contributor is
#: computed correctly rather than refused outright.
_SIGMA_DIVISOR: Final[dict[Distribution, float]] = {
    Distribution.NORMAL: 3.0,
    Distribution.UNIFORM: math.sqrt(3.0),
    Distribution.TRIANGULAR: math.sqrt(6.0),
}


class Risk(StrEnum):
    """An assumption a statistical stack rests on that this module cannot verify.

    Each is either established from the declared data or becomes a refusal. A
    caller who has reasons the data does not carry may acknowledge one — which
    records it, with a signature, rather than removing it.
    """

    #: Variances add only for independent contributors. Never establishable from
    #: the numbers; always present on a statistical stack.
    INDEPENDENCE = "independence"

    #: At least one contributor arrived with no capability study, so its spread
    #: is being assumed to be exactly its tolerance band, centred.
    NO_CAPABILITY_DATA = "no_capability_data"

    #: At least one contributor is not normally distributed, so the band's tails
    #: cannot be read as a population fraction.
    NON_NORMAL = "non_normal"

    #: Too few contributors for the central-limit argument.
    FEW_CONTRIBUTORS = "few_contributors"

    #: A declared capability below `MINIMUM_CAPABILITY`: the process is wider
    #: than the tolerance it is being held to.
    LOW_CAPABILITY = "low_capability"


_RISK_WORDS: Final[dict[Risk, str]] = {
    Risk.INDEPENDENCE: (
        "the contributors are assumed to vary independently. Nothing in these numbers "
        "can show that they do — two features cut in one setup, or two parts from one "
        "mould cavity, are correlated and their variances do not add. Check the process "
        "plan, not the arithmetic."
    ),
    Risk.NO_CAPABILITY_DATA: (
        "no process capability was given for {names}, so each tolerance band is being "
        "assumed to be exactly ±3 sigma and centred. That is a statement about a factory, "
        "not a fact. Supply Capability(cp=…, cpk=…, source=…) from an SPC study or a "
        "supplier's data."
    ),
    Risk.NON_NORMAL: (
        "{names} are not normally distributed, so the variances still add but the band "
        "cannot be read as a percentage of parts. Sigma is computed with the correct "
        "divisor for the declared shape."
    ),
    Risk.FEW_CONTRIBUTORS: (
        "{count} contributors is below {minimum}, so the total is not close to normal and "
        "the band has no population interpretation. The arithmetic is still the root sum "
        "of squares; what is missing is a reason to believe the tails."
    ),
    Risk.LOW_CAPABILITY: (
        "{names} report cp below {minimum}, meaning the process spread is wider than the "
        "tolerance it is held to. A statistical stack built on that understates the gap. "
        "Widen the tolerance, improve the process, or design to worst case."
    ),
}


@dataclass(frozen=True)
class Capability:
    """What a process actually does, from a study somebody ran.

    `cp` is the spread against the tolerance band; `cpk` is the same allowing for
    where the process is centred. `cpk < cp` means it runs off-centre, and the
    offset that implies is carried through the stack as a bias rather than as
    variation.

    **`source` is required.** A capability figure with no provenance is the
    single easiest way to make an RSS number look earned when it is not.
    """

    cp: float
    cpk: float
    source: str
    sample_size: int = 0
    distribution: Distribution = Distribution.NORMAL

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SourceError(
                "A capability figure must say where it came from — an SPC study, a "
                "supplier's capability sheet, a measured run. Pass source='…'; an "
                "unsourced cp is what makes an RSS number look earned when it is not."
            )
        if self.cp <= 0:
            raise StackUpError(
                f"cp must be positive, got {self.cp}. cp is the tolerance band divided "
                "by six sigma; a value of zero or less is not a process."
            )
        if self.cpk > self.cp + 1e-12:
            raise StackUpError(
                f"cpk ({self.cpk}) cannot exceed cp ({self.cp}). cpk is cp reduced by how "
                "far the process runs off centre, so cpk == cp is the centred case and "
                "the best it can be. Check which way round the two were entered."
            )
        if self.cpk <= 0:
            raise StackUpError(
                f"cpk must be positive, got {self.cpk}. A cpk at or below zero means the "
                "process mean is outside the tolerance band — that is not a stack-up "
                "problem, it is a process that cannot make the part."
            )

    @property
    def centring_fraction(self) -> float:
        """How far off centre the process runs, as a fraction of the half-width.

        `cpk = cp × (1 − k)` with `k` the offset over the half-width, so
        `k = 1 − cpk/cp`. Zero for a centred process.
        """
        return max(0.0, 1.0 - self.cpk / self.cp)


#: What is assumed when no capability study is supplied and the risk is
#: acknowledged: the tolerance band is exactly ±3 sigma, and the process is
#: centred. Declared as a named object so it appears in the report as an
#: assumption rather than living as a literal 3.0 inside the arithmetic.
ASSUMED_CAPABILITY: Final = Capability(
    cp=1.0,
    cpk=1.0,
    source="assumed — no capability study supplied; tolerance band read as ±3 sigma, centred",
)


@dataclass(frozen=True)
class Contributor:
    """One dimension in the chain.

    `nominal_mm` is **signed**: positive when the dimension makes the closing gap
    larger, negative when it makes it smaller. A bore of 40 and a shaft of 32
    that must fit inside it are `+40.0` and `−32.0`, and the chain closes on
    8.0 mm of clearance.

    `plus_mm` and `minus_mm` are the deviations, both given as positive
    magnitudes: `40 +0.05/−0.02` is `plus_mm=0.05, minus_mm=0.02`. Symmetric
    tolerances are the common case and `symmetric()` builds one.

    `sensitivity` is how many millimetres of gap one millimetre of this dimension
    moves — 1.0 for a dimension lying along the chain, `cos θ` for one at an
    angle, a lever ratio for a linkage. It is a positive multiplier; direction
    lives in the sign of `nominal_mm`. **Deriving it is the engineer's job**;
    this module multiplies by it and says so.
    """

    name: str
    nominal_mm: float
    plus_mm: float
    minus_mm: float
    sensitivity: float = 1.0
    capability: Capability | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise StackUpError(
                "Every contributor needs a name; it is what a dominant-contributor "
                "report and a failure message are written in terms of."
            )
        for label, value in (("plus_mm", self.plus_mm), ("minus_mm", self.minus_mm)):
            if value < 0:
                raise StackUpError(
                    f"{self.name}: {label} is {value}. Both deviations are given as "
                    "positive magnitudes — '40 +0.05/-0.02' is plus_mm=0.05, "
                    "minus_mm=0.02. The direction is already in the sign of nominal_mm."
                )
        if self.sensitivity <= 0:
            raise StackUpError(
                f"{self.name}: sensitivity is {self.sensitivity}. It is a positive "
                "multiplier — millimetres of gap per millimetre of this dimension. If "
                "this dimension shortens the gap, make nominal_mm negative instead."
            )
        for label, value in (
            ("nominal_mm", self.nominal_mm),
            ("plus_mm", self.plus_mm),
            ("minus_mm", self.minus_mm),
            ("sensitivity", self.sensitivity),
        ):
            if not math.isfinite(value):
                raise StackUpError(
                    f"{self.name}: {label} is {value}, which is not a finite number. A "
                    "stack with a non-finite term produces a non-finite answer that will "
                    "still print."
                )

    @property
    def half_width_mm(self) -> float:
        """Half the tolerance band, after sensitivity — the term RSS squares."""
        return self.sensitivity * (self.plus_mm + self.minus_mm) / 2.0

    @property
    def span_mm(self) -> float:
        """The whole tolerance band after sensitivity — the term worst case sums."""
        return self.sensitivity * (self.plus_mm + self.minus_mm)

    @property
    def mean_mm(self) -> float:
        """Where this contribution sits on average, allowing for asymmetry."""
        return self.sensitivity * (self.nominal_mm + (self.plus_mm - self.minus_mm) / 2.0)

    @property
    def minimum_mm(self) -> float:
        return self.sensitivity * (self.nominal_mm - self.minus_mm)

    @property
    def maximum_mm(self) -> float:
        return self.sensitivity * (self.nominal_mm + self.plus_mm)

    def sigma_mm(self, capability: Capability) -> float:
        """One standard deviation of this contribution, under a capability.

        `σ = half_width / (divisor × cp)`. At `cp = 1` and a normal shape this is
        the textbook `t/3`; a uniform contributor divides by √3 instead, which is
        exact rather than conservative.
        """
        divisor = _SIGMA_DIVISOR[capability.distribution]
        return self.half_width_mm / (divisor * capability.cp)

    def drift_mm(self, capability: Capability) -> float:
        """How far off centre this contribution runs, as a magnitude.

        A bias, not a variation: it is summed arithmetically into the band
        because its direction is not known and two off-centre processes may well
        drift the same way.
        """
        return self.half_width_mm * capability.centring_fraction


def symmetric(
    name: str,
    nominal_mm: float,
    tolerance_mm: float,
    *,
    sensitivity: float = 1.0,
    capability: Capability | None = None,
    source: str = "",
) -> Contributor:
    """A contributor written the usual way: `40 ± 0.05`."""
    return Contributor(
        name=name,
        nominal_mm=nominal_mm,
        plus_mm=tolerance_mm,
        minus_mm=tolerance_mm,
        sensitivity=sensitivity,
        capability=capability,
        source=source,
    )


@dataclass(frozen=True)
class Share:
    """One contributor's share of the total variation.

    The number a correction needs: "the bore is 61% of the variation" says which
    tolerance to buy, where "the stack is 0.19 mm" does not. Computed per method,
    because worst case shares linearly and statistical shares by variance — a
    contributor twice the size of another is twice as much of a worst case and
    four times as much of an RSS.
    """

    name: str
    magnitude_mm: float
    fraction: float


@dataclass(frozen=True)
class StackResult:
    """What a chain of tolerances can do, or why that cannot be said.

    `available` is false only for a statistical stack whose assumptions were
    neither established nor acknowledged. Worst case is interval arithmetic and
    is always available.
    """

    method: Method
    contributors: tuple[Contributor, ...]
    available: bool

    nominal_mm: float = 0.0
    mean_mm: float = 0.0
    minimum_mm: float | None = None
    maximum_mm: float | None = None
    half_width_mm: float | None = None
    sigma_mm: float | None = None
    sigma_multiple: float = DEFAULT_SIGMA_MULTIPLE

    #: Assumptions that were neither established nor acknowledged. Non-empty
    #: exactly when `available` is false.
    refusals: tuple[str, ...] = ()

    #: Assumptions that were acknowledged, in the words of `_RISK_WORDS`, so the
    #: caveat travels with the number wherever it is printed or serialised.
    caveats: tuple[str, ...] = ()

    #: Who signed for the acknowledged assumptions.
    acknowledged_by: str = ""

    shares: tuple[Share, ...] = ()

    @property
    def dominant(self) -> Share | None:
        """The contributor to aim a correction at, or None on an empty result."""
        return self.shares[0] if self.shares else None

    @property
    def variation_mm(self) -> float | None:
        """The full band, min to max — what most people mean by 'the stack'."""
        if self.minimum_mm is None or self.maximum_mm is None:
            return None
        return self.maximum_mm - self.minimum_mm

    def explain(self) -> str:
        """The sentences an engineer should read beside the number.

        Never returns a bare figure: a statistical result always prints its
        caveats, and an unavailable one prints why instead of a value.
        """
        if not self.available:
            head = (
                f"No {self.method.value} result: the assumptions behind it were not "
                "established."
            )
            return "\n".join([head, *(f"  - {line}" for line in self.refusals)])
        assert self.minimum_mm is not None and self.maximum_mm is not None
        head = (
            f"{self.method.value}: {self.nominal_mm:.4g} mm nominal, "
            f"{self.minimum_mm:.4g} to {self.maximum_mm:.4g} mm "
            f"(± {self.half_width_mm:.4g} mm about {self.mean_mm:.4g})"
        )
        if self.method is Method.STATISTICAL:
            head += (
                f", predicted at {self.sigma_multiple:g} sigma. This is a prediction "
                "about a population, not a bound on a part."
            )
        lines = [head]
        if self.dominant is not None:
            lines.append(
                f"  largest contributor: {self.dominant.name} "
                f"({self.dominant.fraction * 100:.0f}% of the variation)"
            )
        for caveat in self.caveats:
            lines.append(f"  ! {caveat}")
        if self.caveats and self.acknowledged_by:
            lines.append(f"  acknowledged by {self.acknowledged_by}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": str(self.method),
            "available": self.available,
            "nominal_mm": self.nominal_mm,
            "contributors": [item.name for item in self.contributors],
        }
        if self.available:
            out.update(
                {
                    "mean_mm": self.mean_mm,
                    "minimum_mm": self.minimum_mm,
                    "maximum_mm": self.maximum_mm,
                    "half_width_mm": self.half_width_mm,
                    "shares": [
                        {
                            "name": share.name,
                            "magnitude_mm": share.magnitude_mm,
                            "fraction": share.fraction,
                        }
                        for share in self.shares
                    ],
                }
            )
        if self.sigma_mm is not None:
            out["sigma_mm"] = self.sigma_mm
            out["sigma_multiple"] = self.sigma_multiple
        if self.refusals:
            out["refusals"] = list(self.refusals)
        if self.caveats:
            out["caveats"] = list(self.caveats)
            out["acknowledged_by"] = self.acknowledged_by
        return out


@dataclass(frozen=True)
class StackVerdict:
    """A stack checked against the limits it has to live inside.

    Reuses `app.design.assertions.Outcome` rather than inventing a fourth verdict
    vocabulary: `PASSED`, `FAILED`, and `UNMEASURED` for a statistical result
    that was refused — which is the correct reading, since the number was never
    obtained.
    """

    name: str
    outcome: Outcome
    result: StackResult
    at_least_mm: float | None = None
    at_most_mm: float | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome is Outcome.PASSED

    @property
    def margin_mm(self) -> float | None:
        """Distance from the nearer limit — negative when it is breached.

        The number a correction loop wants: "0.04 mm over at the low end" is
        actionable where "failed" is not.
        """
        if self.result.minimum_mm is None or self.result.maximum_mm is None:
            return None
        margins: list[float] = []
        if self.at_least_mm is not None:
            margins.append(self.result.minimum_mm - self.at_least_mm)
        if self.at_most_mm is not None:
            margins.append(self.at_most_mm - self.result.maximum_mm)
        return min(margins) if margins else None

    def __str__(self) -> str:
        if self.outcome is Outcome.UNMEASURED:
            return f"{self.name}: not checked — {self.reason}"
        margin = self.margin_mm
        tail = "" if margin is None else f", margin {margin:+.4g} mm"
        verdict = "passed" if self.passed else "FAILED"
        return f"{self.name}: {verdict}{tail}\n{self.result.explain()}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "outcome": str(self.outcome),
            "stack": self.result.to_dict(),
        }
        if self.at_least_mm is not None:
            out["at_least_mm"] = self.at_least_mm
        if self.at_most_mm is not None:
            out["at_most_mm"] = self.at_most_mm
        if self.margin_mm is not None:
            out["margin_mm"] = self.margin_mm
        if self.reason:
            out["reason"] = self.reason
        return out


def _validate_chain(contributors: Sequence[Contributor]) -> None:
    if not contributors:
        raise StackUpError(
            "A stack-up with no contributors is not a check — it closes on zero with zero "
            "variation, whatever the limit. Declare the dimensions in the chain."
        )
    seen: set[str] = set()
    for item in contributors:
        if item.name in seen:
            raise StackUpError(
                f"Two contributors are both called {item.name!r}. Names have to be "
                "distinct or the dominant-contributor report cannot say which one to "
                "aim at."
            )
        seen.add(item.name)
    if all(item.span_mm == 0.0 for item in contributors):
        raise StackUpError(
            "No contributor in this chain carries a tolerance, so the stack is exactly "
            "the nominal and always passes. Give the dimensions their tolerances, or "
            "check that the chain was not built from basic dimensions by mistake."
        )


def _assess(
    contributors: Sequence[Contributor], acknowledged: frozenset[Risk]
) -> tuple[dict[Risk, str], dict[Risk, str]]:
    """Split the statistical assumptions into refusals and acknowledged caveats."""
    found: dict[Risk, str] = {}

    found[Risk.INDEPENDENCE] = _RISK_WORDS[Risk.INDEPENDENCE]

    if len(contributors) < MINIMUM_CONTRIBUTORS_FOR_RSS:
        found[Risk.FEW_CONTRIBUTORS] = _RISK_WORDS[Risk.FEW_CONTRIBUTORS].format(
            count=len(contributors), minimum=MINIMUM_CONTRIBUTORS_FOR_RSS
        )

    unstudied = [item.name for item in contributors if item.capability is None]
    if unstudied:
        found[Risk.NO_CAPABILITY_DATA] = _RISK_WORDS[Risk.NO_CAPABILITY_DATA].format(
            names=", ".join(unstudied)
        )

    non_normal = [
        item.name
        for item in contributors
        if item.capability is not None
        and item.capability.distribution is not Distribution.NORMAL
    ]
    if non_normal:
        found[Risk.NON_NORMAL] = _RISK_WORDS[Risk.NON_NORMAL].format(
            names=", ".join(non_normal)
        )

    weak = [
        item.name
        for item in contributors
        if item.capability is not None and item.capability.cp < MINIMUM_CAPABILITY
    ]
    if weak:
        found[Risk.LOW_CAPABILITY] = _RISK_WORDS[Risk.LOW_CAPABILITY].format(
            names=", ".join(weak), minimum=MINIMUM_CAPABILITY
        )

    refusals = {risk: words for risk, words in found.items() if risk not in acknowledged}
    caveats = {risk: words for risk, words in found.items() if risk in acknowledged}
    return refusals, caveats


def _shares(contributors: Sequence[Contributor], *, squared: bool) -> tuple[Share, ...]:
    """Each contributor's share of the variation, largest first.

    `squared` for the statistical method, because variances add: a contributor
    with twice the tolerance carries four times the variance and only twice the
    worst case, and a correction aimed by the wrong one of those two rankings
    buys the wrong tolerance.
    """
    weights = [
        (item.name, item.span_mm**2 if squared else item.span_mm) for item in contributors
    ]
    total = sum(weight for _, weight in weights)
    if total <= 0:
        return ()
    ordered = sorted(weights, key=lambda pair: pair[1], reverse=True)
    return tuple(
        Share(name=name, magnitude_mm=math.sqrt(weight) if squared else weight,
              fraction=weight / total)
        for name, weight in ordered
    )


def stack(
    contributors: Iterable[Contributor],
    *,
    method: Method,
    acknowledged: Iterable[Risk] = (),
    acknowledged_by: str = "",
    sigma_multiple: float = DEFAULT_SIGMA_MULTIPLE,
) -> StackResult:
    """Close a chain of tolerances, by the method asked for.

    Worst case is exact interval arithmetic and always returns a result.

    Statistical returns a result **only** when every assumption in `Risk` is
    either established from the declared data or listed in `acknowledged` — and
    acknowledging anything requires `acknowledged_by`, because an assumption
    nobody signed for is an assumption nobody made. `Risk.INDEPENDENCE` can never
    be established from numbers, so it is always on that list: an anonymous RSS
    number is not obtainable from this function, which is the point.
    """
    chain = tuple(contributors)
    _validate_chain(chain)

    if sigma_multiple <= 0 or not math.isfinite(sigma_multiple):
        raise StackUpError(
            f"sigma_multiple is {sigma_multiple}; it is how many standard deviations the "
            "reported band spans each side, so it must be a positive number. 3.0 is the "
            "classic choice, 6.0 a six-sigma one."
        )

    if method is Method.WORST_CASE:
        return _worst_case(chain)
    return _statistical(
        chain,
        acknowledged=frozenset(acknowledged),
        acknowledged_by=acknowledged_by,
        sigma_multiple=sigma_multiple,
    )


def _worst_case(chain: tuple[Contributor, ...]) -> StackResult:
    nominal = sum(item.sensitivity * item.nominal_mm for item in chain)
    minimum = sum(item.minimum_mm for item in chain)
    maximum = sum(item.maximum_mm for item in chain)
    return StackResult(
        method=Method.WORST_CASE,
        contributors=chain,
        available=True,
        nominal_mm=nominal,
        mean_mm=(minimum + maximum) / 2.0,
        minimum_mm=minimum,
        maximum_mm=maximum,
        half_width_mm=(maximum - minimum) / 2.0,
        shares=_shares(chain, squared=False),
    )


def _statistical(
    chain: tuple[Contributor, ...],
    *,
    acknowledged: frozenset[Risk],
    acknowledged_by: str,
    sigma_multiple: float,
) -> StackResult:
    if acknowledged and not acknowledged_by.strip():
        raise SourceError(
            "Acknowledging a statistical assumption needs a name against it: pass "
            "acknowledged_by='…'. An assumption nobody signed for is an assumption "
            "nobody made, and this is the one number in the phase that gets believed "
            "without being checked."
        )
    refusals, caveats = _assess(chain, acknowledged)
    nominal = sum(item.sensitivity * item.nominal_mm for item in chain)
    if refusals:
        return StackResult(
            method=Method.STATISTICAL,
            contributors=chain,
            available=False,
            nominal_mm=nominal,
            refusals=tuple(refusals[risk] for risk in Risk if risk in refusals),
            caveats=tuple(caveats[risk] for risk in Risk if risk in caveats),
            acknowledged_by=acknowledged_by,
            sigma_multiple=sigma_multiple,
        )

    variance = 0.0
    drift = 0.0
    for item in chain:
        capability = item.capability or ASSUMED_CAPABILITY
        sigma = item.sigma_mm(capability)
        variance += sigma * sigma
        drift += item.drift_mm(capability)

    sigma_total = math.sqrt(variance)
    half_width = sigma_multiple * sigma_total + drift
    mean = sum(item.mean_mm for item in chain)
    return StackResult(
        method=Method.STATISTICAL,
        contributors=chain,
        available=True,
        nominal_mm=nominal,
        mean_mm=mean,
        minimum_mm=mean - half_width,
        maximum_mm=mean + half_width,
        half_width_mm=half_width,
        sigma_mm=sigma_total,
        sigma_multiple=sigma_multiple,
        caveats=tuple(caveats[risk] for risk in Risk if risk in caveats),
        acknowledged_by=acknowledged_by,
        shares=_shares(chain, squared=True),
    )


def check(
    result: StackResult,
    *,
    name: str,
    at_least_mm: float | None = None,
    at_most_mm: float | None = None,
) -> StackVerdict:
    """Judge a closed stack against the gap it has to stay inside.

    At least one limit is required: a stack with neither is not a check. A
    refused statistical result comes back `UNMEASURED` with the refusals as the
    reason — never `PASSED`, and never quietly downgraded to worst case, which
    would answer a different question than the one asked.
    """
    if at_least_mm is None and at_most_mm is None:
        raise StackUpError(
            f"{name}: a stack-up check needs a limit — at_least_mm (a clearance that must "
            "not close), at_most_mm (a gap that must not open), or both."
        )
    if at_least_mm is not None and at_most_mm is not None and at_least_mm > at_most_mm:
        raise StackUpError(
            f"{name}: at_least_mm ({at_least_mm}) is above at_most_mm ({at_most_mm}), so "
            "no gap can satisfy both. Check which way round the two limits go."
        )
    if not result.available:
        return StackVerdict(
            name=name,
            outcome=Outcome.UNMEASURED,
            result=result,
            at_least_mm=at_least_mm,
            at_most_mm=at_most_mm,
            reason=(
                f"the {result.method.value} result was refused: "
                + " ".join(result.refusals)
            ),
        )
    assert result.minimum_mm is not None and result.maximum_mm is not None
    breached = (at_least_mm is not None and result.minimum_mm < at_least_mm) or (
        at_most_mm is not None and result.maximum_mm > at_most_mm
    )
    return StackVerdict(
        name=name,
        outcome=Outcome.FAILED if breached else Outcome.PASSED,
        result=result,
        at_least_mm=at_least_mm,
        at_most_mm=at_most_mm,
    )


def compare(
    contributors: Iterable[Contributor],
    *,
    acknowledged: Iterable[Risk] = (),
    acknowledged_by: str = "",
    sigma_multiple: float = DEFAULT_SIGMA_MULTIPLE,
) -> tuple[StackResult, StackResult]:
    """Both methods on one chain, worst case first.

    The pair is what an engineer actually wants to see, because the decision is
    never "which number is right" — both are right about different questions —
    but "is the difference between them worth the risk of designing to the
    smaller one". The statistical half may well be unavailable, and that is a
    legitimate answer to that question.
    """
    chain = tuple(contributors)
    return (
        stack(chain, method=Method.WORST_CASE),
        stack(
            chain,
            method=Method.STATISTICAL,
            acknowledged=acknowledged,
            acknowledged_by=acknowledged_by,
            sigma_multiple=sigma_multiple,
        ),
    )


__all__ = [
    "ASSUMED_CAPABILITY",
    "DEFAULT_SIGMA_MULTIPLE",
    "MINIMUM_CAPABILITY",
    "MINIMUM_CONTRIBUTORS_FOR_RSS",
    "Capability",
    "Contributor",
    "Distribution",
    "Method",
    "Risk",
    "Share",
    "StackResult",
    "StackVerdict",
    "check",
    "compare",
    "stack",
    "symmetric",
]
