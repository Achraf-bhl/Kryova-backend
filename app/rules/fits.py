"""Fits between a hole and a shaft: what a pair gives, and which pair to choose -- master plan 13.2.

`stackup.py` says it does not select fits, because ISO 286's tables are adopted data an
engineer chooses from rather than arithmetic. That stays true: **no deviation is shipped
here.** A tolerance zone arrives with its two deviations and the source they were read from
(ISO 286-2's table, a supplier's bearing seat recommendation, a company standard), the same
way every limit in `app/rules/` arrives. What is arithmetic, and is here:

* **What a fit gives.** The largest clearance is the hole's upper limit minus the shaft's
  lower; the smallest is the hole's lower minus the shaft's upper. A negative clearance is an
  interference. A fit is a *clearance* fit when even the smallest clearance is not negative,
  an *interference* fit when even the largest is not positive, and a *transition* fit when
  the pair can go either way.
* **Which fit to choose.** Given the clearance the function needs (a running fit's minimum
  oil gap, a press fit's minimum grip), with its source, a candidate qualifies when **every**
  part made inside both zones lands in that range: its smallest clearance is at least the
  minimum and its largest at most the maximum. Among those that qualify, the one with the
  widest combined tolerance is chosen, because a wider zone is the cheaper part to make;
  that preference is stated in the result, and a caller with a different one reads the
  qualifying list instead. Every rejected candidate carries its reason in numbers.

What is not here: reading ISO 286's tables (a document this codebase has not transcribed),
temperature and surface-finish effects on a press fit's real grip, and CATIA FTA.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.rules.errors import RuleError, SourceError


class FitError(RuleError):
    """A tolerance zone or fit that cannot mean what it says."""


class Feature(StrEnum):
    HOLE = "hole"
    SHAFT = "shaft"


@dataclass(frozen=True)
class ToleranceZone:
    """One feature of size: nominal size and its two deviations, in mm, with their source."""

    designation: str
    feature: Feature
    nominal_mm: float
    upper_deviation_mm: float
    lower_deviation_mm: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SourceError(
                f"{self.designation}: a tolerance zone needs the table or standard its deviations "
                "were read from."
            )
        values = (self.nominal_mm, self.upper_deviation_mm, self.lower_deviation_mm)
        if not all(math.isfinite(v) for v in values):
            raise FitError(f"{self.designation}: every size and deviation must be finite.")
        if self.nominal_mm <= 0.0:
            raise FitError(f"{self.designation}: a nominal size of {self.nominal_mm} mm is no feature.")
        if self.upper_deviation_mm < self.lower_deviation_mm:
            raise FitError(
                f"{self.designation}: the upper deviation {self.upper_deviation_mm} is below the "
                f"lower {self.lower_deviation_mm}. Swap them."
            )

    @property
    def tolerance_mm(self) -> float:
        return self.upper_deviation_mm - self.lower_deviation_mm

    @property
    def largest_mm(self) -> float:
        return self.nominal_mm + self.upper_deviation_mm

    @property
    def smallest_mm(self) -> float:
        return self.nominal_mm + self.lower_deviation_mm


class FitKind(StrEnum):
    CLEARANCE = "clearance"
    TRANSITION = "transition"
    INTERFERENCE = "interference"


@dataclass(frozen=True)
class Fit:
    hole: ToleranceZone
    shaft: ToleranceZone

    def __post_init__(self) -> None:
        if self.hole.feature is not Feature.HOLE or self.shaft.feature is not Feature.SHAFT:
            raise FitError("A fit pairs a hole zone with a shaft zone, in that order.")
        if abs(self.hole.nominal_mm - self.shaft.nominal_mm) > 1e-9:
            raise FitError(
                f"{self.hole.designation} is on {self.hole.nominal_mm:g} mm and "
                f"{self.shaft.designation} on {self.shaft.nominal_mm:g} mm; a fit is two zones on "
                "one nominal size."
            )

    @property
    def name(self) -> str:
        return f"{self.hole.designation}/{self.shaft.designation}"

    @property
    def largest_clearance_mm(self) -> float:
        return self.hole.largest_mm - self.shaft.smallest_mm

    @property
    def smallest_clearance_mm(self) -> float:
        return self.hole.smallest_mm - self.shaft.largest_mm

    @property
    def kind(self) -> FitKind:
        if self.smallest_clearance_mm >= 0.0:
            return FitKind.CLEARANCE
        if self.largest_clearance_mm <= 0.0:
            return FitKind.INTERFERENCE
        return FitKind.TRANSITION

    @property
    def combined_tolerance_mm(self) -> float:
        return self.hole.tolerance_mm + self.shaft.tolerance_mm

    def to_dict(self) -> dict[str, Any]:
        return {
            "fit": self.name,
            "kind": self.kind.value,
            "nominal_mm": self.hole.nominal_mm,
            "largest_clearance_mm": self.largest_clearance_mm,
            "smallest_clearance_mm": self.smallest_clearance_mm,
            "combined_tolerance_mm": self.combined_tolerance_mm,
            "sources": [self.hole.source, self.shaft.source],
        }


@dataclass(frozen=True)
class ClearanceRequirement:
    """The clearance range the function needs, mm; negative is interference."""

    minimum_mm: float
    maximum_mm: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise SourceError(
                "A clearance requirement needs its reason: the bearing maker's seat, the oil "
                "film, the grip a press fit must transmit."
            )
        if self.minimum_mm > self.maximum_mm:
            raise FitError(
                f"A clearance of at least {self.minimum_mm} and at most {self.maximum_mm} mm is "
                "empty."
            )


@dataclass(frozen=True)
class Rejection:
    fit: Fit
    reason: str


@dataclass(frozen=True)
class FitSelection:
    requirement: ClearanceRequirement
    qualifying: tuple[Fit, ...]
    rejected: tuple[Rejection, ...]

    @property
    def chosen(self) -> Fit | None:
        """The qualifying fit with the widest combined tolerance, or None."""
        if not self.qualifying:
            return None
        return max(self.qualifying, key=lambda fit: fit.combined_tolerance_mm)

    def to_dict(self) -> dict[str, Any]:
        chosen = self.chosen
        return {
            "requirement": {
                "minimum_mm": self.requirement.minimum_mm,
                "maximum_mm": self.requirement.maximum_mm,
                "source": self.requirement.source,
            },
            "chosen": None if chosen is None else chosen.to_dict(),
            "preference": "the widest combined tolerance among fits that always qualify",
            "qualifying": [fit.to_dict() for fit in self.qualifying],
            "rejected": [{"fit": r.fit.name, "reason": r.reason} for r in self.rejected],
        }


def select_fit(candidates: Sequence[Fit], requirement: ClearanceRequirement) -> FitSelection:
    """Every candidate judged against the requirement, and the cheapest that always meets it."""
    if not candidates:
        raise FitError("There are no candidate fits to choose from.")
    names = [fit.name for fit in candidates]
    if len(set(names)) != len(names):
        raise FitError("Two candidates are the same fit.")
    nominals = {fit.hole.nominal_mm for fit in candidates}
    if len(nominals) > 1:
        raise FitError("Candidates for one joint share one nominal size.")
    qualifying: list[Fit] = []
    rejected: list[Rejection] = []
    for fit in candidates:
        reasons = []
        if fit.smallest_clearance_mm < requirement.minimum_mm - 1e-12:
            reasons.append(
                f"its smallest clearance {fit.smallest_clearance_mm:+.4f} mm is below the "
                f"{requirement.minimum_mm:+.4f} mm needed"
            )
        if fit.largest_clearance_mm > requirement.maximum_mm + 1e-12:
            reasons.append(
                f"its largest clearance {fit.largest_clearance_mm:+.4f} mm is above the "
                f"{requirement.maximum_mm:+.4f} mm allowed"
            )
        if reasons:
            rejected.append(Rejection(fit, "; ".join(reasons)))
        else:
            qualifying.append(fit)
    return FitSelection(requirement, tuple(qualifying), tuple(rejected))


__all__ = [
    "ClearanceRequirement",
    "Feature",
    "Fit",
    "FitError",
    "FitKind",
    "FitSelection",
    "Rejection",
    "ToleranceZone",
    "select_fit",
]
