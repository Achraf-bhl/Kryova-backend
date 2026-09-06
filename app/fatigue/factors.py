"""Everything between a laboratory S-N curve and the part on the bench.

A published S-N curve describes a small polished specimen in laboratory air at room
temperature under fully reversed axial load. The part is none of those things, and
the gap is closed by multipliers — surface finish, size, load type, temperature,
reliability — plus a stress concentration on the load side. Master plan 8.2.

**Every one of those multipliers is a judgement, and this module refuses to make any
of them.** There is no `surface_factor("machined")` here and there will not be one.
A machined factor depends on the ultimate strength, on what "machined" meant on the
drawing, and on whether the chart being read is Shigley's, the FKM guideline's or the
supplier's; three defensible sources give three different numbers, and a function
that picked one would hide the choice inside a call nobody re-reads. What this module
provides is a `Factor` that cannot exist without a source string, a `FactorSet` that
multiplies them and can list what it contains, and an assessment-side check that the
factors a method *requires* are actually present.

The one thing the module does decide is **where the factors act**: they scale the
material's endurance strength (`SNCurve.scaled`), and the stress concentration scales
the *load* (`Collective.scaled`). That split matters because the concentration
multiplies the mean stress as well as the amplitude, which a strength-side factor
would not, and because it keeps a Kt out of the curve that the curve then carries
into every other assessment made with it.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from app.fatigue.sources import require_source

#: The factors a nominal-stress high-cycle assessment of unwelded metal is not
#: allowed to omit. Surface finish and size are the two that move a laboratory
#: endurance limit by the most — a rough-machined, 100 mm-thick part sits at
#: roughly half the polished 8 mm specimen's endurance strength — and an
#: assessment that quietly left them at 1.0 would be optimistic by that factor
#: without saying so anywhere. Reliability is deliberately *not* here: it belongs
#: to the curve's `failure_probability`, and requiring it as a factor as well
#: would let it be applied twice.
#:
#: This is a floor, not a policy: `Assessment.required_factors` overrides it, and
#: a welded assessment overrides it to empty because the detail category already
#: contains the surface and size effects of the joint it describes.
REQUIRED_FACTORS: Final[frozenset[str]] = frozenset({"surface", "size"})


@dataclass(frozen=True)
class Factor:
    """One named strength-modifying multiplier, with the source it was read from.

    `value` is a multiplier on the endurance amplitude: 0.75 means the part's
    endurance strength is three-quarters of the specimen's. Values above 1.0 are
    permitted — shot peening and nitriding genuinely raise it — but they are the
    ones a reviewer will look at hardest, which is what the source is for.
    """

    name: str
    value: float
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.strip().lower())
        object.__setattr__(
            self, "source", require_source(self.source, f"The {self.name or 'unnamed'} factor")
        )
        if not self.name:
            raise ValueError(
                "A factor must be named — 'surface', 'size', 'temperature' — because the "
                "assessment checks for required factors by name and an unnamed one can "
                "never satisfy a requirement."
            )
        if not (self.value > 0.0 and math.isfinite(self.value)):
            raise ValueError(
                f"The {self.name} factor must be a positive, finite multiplier; got "
                f"{self.value!r}. Zero would say the part has no fatigue strength at all, "
                "which is a modelling error rather than a result."
            )

    def describe(self) -> str:
        return f"{self.name} = {self.value:.4g} [{self.source}]"


@dataclass(frozen=True)
class FactorSet:
    """The multipliers applied to the material's endurance strength, as one object.

    Ordered and de-duplicated by name: two factors called `surface` is a mistake
    that would otherwise square a correction, so it is refused at construction
    rather than found later in a life that is off by 0.75².
    """

    factors: tuple[Factor, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for factor in self.factors:
            if factor.name in seen:
                raise ValueError(
                    f"The factor {factor.name!r} is given twice. Applying it twice squares "
                    "it; if two effects genuinely apply, name them separately."
                )
            seen.add(factor.name)

    @classmethod
    def of(cls, factors: Iterable[Factor]) -> FactorSet:
        return cls(tuple(factors))

    @property
    def names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.factors)

    @property
    def value(self) -> float:
        """The product. 1.0 for an empty set — which is why absence is checked separately.

        An empty `FactorSet` multiplies to exactly the same number as a set of
        factors that all happen to be 1.0, and the two mean opposite things: one
        is "nobody looked", the other is "somebody looked and found no
        correction". `missing_from` is what tells them apart, and the assessment
        calls it before it calls this.
        """
        product = 1.0
        for factor in self.factors:
            product *= factor.value
        return product

    def missing_from(self, required: Iterable[str]) -> tuple[str, ...]:
        """Which required factors this set does not contain, in the order asked for."""
        present = self.names
        return tuple(name for name in required if name not in present)

    def describe(self) -> tuple[str, ...]:
        return tuple(f.describe() for f in self.factors)


@dataclass(frozen=True)
class StressConcentration:
    """The notch: Kt, and the judgement that turns it into Kf.

    **Kt is geometry and Kf is a judgement.** The elastic stress concentration
    factor Kt follows from the shape and can in principle be measured off the
    model. The fatigue notch factor Kf — what the notch actually costs in life —
    is smaller than Kt by an amount that depends on the material's notch
    sensitivity q, which depends on the notch root radius and on the ultimate
    strength, and which for a small radius in a soft steel can be below 0.5.

    So `notch_sensitivity` is an explicit optional input. When it is given,
    Kf = 1 + q·(Kt − 1) — Peterson's relation, which is a *definition* of q, not
    an estimate. When it is absent, Kf = Kt: fully notch-sensitive, the
    conservative bound, and the assessment records that it assumed it rather
    than leaving the reader to infer it from a missing field.

    `location` is free text naming where on the part this is — "fillet root,
    R3 under the bearing seat". It is not optional in practice: an assessment
    reported without saying which feature it is about is not reviewable.
    """

    kt: float
    source: str
    location: str = ""
    notch_sensitivity: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source", require_source(self.source, "A stress concentration factor")
        )
        if not (self.kt >= 1.0 and math.isfinite(self.kt)):
            raise ValueError(
                "A stress concentration factor is at least 1.0 — a notch cannot reduce the "
                f"local stress below the nominal; got {self.kt!r}. If the intent was to "
                "reduce a stress, that is a strength factor, not a concentration."
            )
        if self.notch_sensitivity is not None and not 0.0 <= self.notch_sensitivity <= 1.0:
            raise ValueError(
                "Notch sensitivity q lies in [0, 1]: 0 is a material that ignores the notch "
                f"entirely, 1 is one that feels all of it. Got {self.notch_sensitivity!r}."
            )

    @property
    def kf(self) -> float:
        """The fatigue notch factor actually applied to the stress."""
        if self.notch_sensitivity is None:
            return self.kt
        return 1.0 + self.notch_sensitivity * (self.kt - 1.0)

    @property
    def assumed_full_sensitivity(self) -> bool:
        """Whether Kf fell back to Kt because no notch sensitivity was supplied."""
        return self.notch_sensitivity is None

    def describe(self) -> str:
        where = f" at {self.location}" if self.location else ""
        if self.notch_sensitivity is None:
            return (
                f"Kt = {self.kt:.4g}{where}, Kf taken as Kt (full notch sensitivity assumed) "
                f"[{self.source}]"
            )
        return (
            f"Kt = {self.kt:.4g}{where}, q = {self.notch_sensitivity:.4g}, "
            f"Kf = {self.kf:.4g} [{self.source}]"
        )


__all__ = ["REQUIRED_FACTORS", "Factor", "FactorSet", "StressConcentration"]
