"""Design rules checked against a measured part, and whether the verdict is *proved*.

Phase 13.1. A design rule is a limit somebody adopted — minimum wall for a
process, a draft angle, a fillet a cutter can actually reach — attached to a
quantity the kernel measures, so that breaking one is a red build rather than a
comment in a review.

**Three things this module deliberately does not invent.**

1. **Not a fourth verdict vocabulary.** `PASSED` / `FAILED` / `UNMEASURED` come
   from `app.design.assertions.Outcome`, and the comparison itself is
   `check_assertions`. A `Rule` *is* an `Assertion` with a source and a
   provenance reading on top, and `as_assertion()` is where it becomes one. The
   reason is the one `machine_checks.py` gives about itself: a second comparison
   language means a second `==`-without-a-tolerance bug, a second set of
   messages, and two things to keep in step.
2. **Not a second validator.** `Rule.__post_init__` builds its own assertion and
   lets `Assertion` refuse it, so an unknown comparison and an exact equality
   with no tolerance are refused here in the words `assertions.py` already uses.
3. **Not a fourth vocabulary of quantities.** `app.rules.vocabulary` reads
   `app.kernel.contract`, and a rule naming something outside it is refused **at
   construction**. A rule that could never be checked reports `UNMEASURED` for
   ever, and in a DFM suite that reads exactly like a gate that always passes.

**The subtle half: a pass on a sampled number is not a proof.** `minimum_wall_mm`
and `undercut_face_count` come out of `app.kernel.interrogation` as **bounds from
a finite ray set**, and the payload's provenance sidecar says so. Which *way*
they err is not in the sidecar — it is a property of what the scan does — so it
is declared once in `app.rules.vocabulary.SAMPLED_BOUNDS`, and this module reads
the two together:

* `minimum_wall_mm >= 2.5` measuring **2.0** — the reported minimum is an upper
  bound, so the true minimum is at most 2.0 and certainly below 2.5. The
  violation is **proved**.
* `minimum_wall_mm >= 2.5` measuring **2.6** — the true minimum is at most 2.6
  and could be anything below it. The rule reads `PASSED`, `provisional`, and
  `RuleReport.proven` is false. A denser scan moves the bound; it does not make
  this a proof.
* `minimum_wall_mm <= 6.0` measuring **2.6** — same number, same sampling, and
  now the pass *is* proved, because the claim runs the same way as the bound.

A payload that says **nothing** about how it arrived at a quantity the contract
marks as normally sampled is read as sampled, not as exact. That silence is real
— `thinnest_point_mm` is written into an OCCT payload with no sidecar entry of
its own — and reading it the other way would print `proven` beside a ray-cast
number. `_rests_on_a_bound` is where the three sources are ordered.

So `ok` and `proven` are different questions and both are on the report. Calling
a provisional pass `UNMEASURED` instead was considered and rejected for the
reason `app/design/missions.py` records about its pending rungs: every ray-cast
wall rule would be unmeasured for ever, and a suite that is red for ever is a
suite somebody switches off. A provisional pass is a screening result and says
which it is, in words, in `__str__` and in `to_dict`.

**A rule needs a source** (`SourceError` without one), which is Decision 3
applied to the thing doing the judging rather than to the measurement: a wall
thickness limit with no process, standard or supplier behind it is a number
nobody can argue with, and a red build nobody can argue with gets switched off
too.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from app.design.assertions import (
    Assertion,
    AssertionReport,
    AssertionResult,
    Outcome,
    check_assertions,
)
from app.design.errors import SpecError
from app.rules.errors import RuleError, SourceError
from app.rules.vocabulary import (
    BoundDirection,
    bound_direction,
    payload_states_a_basis,
    require_measurable,
    typical_basis_is_approximated,
    unit,
)

#: Comparisons whose claim is "the truth is at least this much".
_CLAIMS_AT_LEAST = frozenset({">=", ">"})

#: Comparisons whose claim is "the truth is at most this much".
_CLAIMS_AT_MOST = frozenset({"<=", "<"})


class Rule:
    """One adopted limit on one measurable quantity.

    Constructed, not declared as a frozen dataclass, only because it validates by
    *building its assertion* and keeping it — see the module docstring. Attributes
    are read-only by convention and nothing here mutates one.
    """

    __slots__ = ("_assertion", "comparison", "limit", "measure", "name", "process",
                 "rationale", "source", "tolerance")

    def __init__(
        self,
        *,
        name: str,
        measure: str,
        comparison: str,
        limit: float,
        source: str,
        rationale: str = "",
        process: str = "",
        tolerance: float = 0.0,
    ) -> None:
        if not name or not name.strip():
            raise RuleError(
                "A design rule needs a name; it is what a red build is reported under."
            )
        if not source or not source.strip():
            raise SourceError(
                f"{name}: a design rule needs a source — the process sheet, the standard, "
                "the supplier's guideline or the person who adopted it. Pass source='…'. "
                "An undefended limit is one nobody can argue with, and a red build nobody "
                "can argue with is a check somebody switches off."
            )
        if isinstance(limit, bool) or not isinstance(limit, (int, float)):
            raise RuleError(
                f"{name}: limit must be a number, got {type(limit).__name__}. A design "
                "rule compares a measurement to a figure, not to an expression."
            )
        if not math.isfinite(float(limit)):
            raise RuleError(
                f"{name}: limit is {limit}, which is not a finite number. A rule with a "
                "non-finite limit passes or fails everything, silently."
            )

        # Refused here rather than at check time. See the module docstring.
        require_measurable(measure, rule_name=name)

        self.name = name
        self.measure = measure
        self.comparison = comparison
        self.limit = float(limit)
        self.source = source
        self.rationale = rationale
        self.process = process
        self.tolerance = float(tolerance)

        # `Assertion` owns comparison validation and the equality-needs-a-tolerance
        # rule. Building one now moves both refusals to construction time and keeps
        # this module from growing a second copy of either.
        try:
            self._assertion = self._build_assertion()
        except SpecError as exc:
            # Not re-prefixed with the name: the assertion is built with this rule's
            # name, so every message it can raise here already opens with it, and
            # adding it again printed "wall: wall: …" at the user.
            raise RuleError(str(exc)) from exc

    def _build_assertion(self) -> Assertion:
        note = "; ".join(part for part in (self.rationale, f"source: {self.source}") if part)
        return Assertion(
            name=self.name,
            measure=self.measure,
            comparison=self.comparison,
            bound=self.limit,
            tolerance=self.tolerance,
            note=note,
        )

    def as_assertion(self) -> Assertion:
        """The assertion this rule is checked as. Built at construction."""
        return self._assertion

    @property
    def unit(self) -> str:
        """The unit the measurement contract declares for this rule's quantity."""
        return unit(self.measure)

    def __repr__(self) -> str:  # pragma: no cover - diagnostic
        return (
            f"Rule({self.name!r}, {self.measure} {self.comparison} {self.limit}, "
            f"source={self.source!r})"
        )

    def __str__(self) -> str:
        tail = f" {self.unit}" if self.unit else ""
        return f"{self.name}: {self.measure} {self.comparison} {self.limit:g}{tail}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "measure": self.measure,
            "comparison": self.comparison,
            "limit": self.limit,
            "unit": self.unit,
            "source": self.source,
        }
        if self.tolerance:
            out["tolerance"] = self.tolerance
        if self.rationale:
            out["rationale"] = self.rationale
        if self.process:
            out["process"] = self.process
        return out


class RuleResult:
    """One rule checked, and whether the verdict rests on a bound or on a number."""

    __slots__ = ("direction", "result", "rule", "sampled")

    def __init__(
        self,
        *,
        rule: Rule,
        result: AssertionResult,
        direction: BoundDirection,
        sampled: bool,
    ) -> None:
        self.rule = rule
        self.result = result
        self.sampled = sampled
        # A number the payload marked approximate but for which nothing declares a
        # direction is `UNKNOWN`, never `EXACT`: knowing it is approximate and not
        # knowing which way is strictly less information than knowing it is exact,
        # and reading it as exact is the one mistake that manufactures a proof.
        self.direction = (
            BoundDirection.UNKNOWN
            if sampled and direction is BoundDirection.EXACT
            else direction
        )

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def outcome(self) -> Outcome:
        return self.result.outcome

    @property
    def measured(self) -> float | None:
        return self.result.measured

    @property
    def ok(self) -> bool:
        """Whether the rule was satisfied. Says nothing about whether that is proved."""
        return self.outcome is Outcome.PASSED

    @property
    def proven(self) -> bool:
        """Whether the verdict follows from the reported number *whatever* the truth is.

        True for any verdict on a number the payload did not mark approximate.
        On a sampled number it is true only when the rule's claim runs the same
        way as the bound — see the module docstring's three worked cases.
        """
        if self.outcome is Outcome.UNMEASURED:
            return False
        if not self.sampled or self.direction is BoundDirection.EXACT:
            return True
        if self.direction is BoundDirection.UNKNOWN:
            return False
        comparison = self.rule.comparison
        if self.direction is BoundDirection.UPPER_BOUND:
            # Reported >= truth, so every conclusion of the form "truth is at most X"
            # survives: a satisfied at-most rule, and a broken at-least rule.
            proves_at_most = comparison in _CLAIMS_AT_MOST and self.ok
            proves_at_least_broken = comparison in _CLAIMS_AT_LEAST and not self.ok
            return proves_at_most or proves_at_least_broken
        # LOWER_BOUND: reported <= truth, and the mirror argument.
        proves_at_least = comparison in _CLAIMS_AT_LEAST and self.ok
        proves_at_most_broken = comparison in _CLAIMS_AT_MOST and not self.ok
        return proves_at_least or proves_at_most_broken

    @property
    def provisional(self) -> bool:
        """A verdict that was reached but not proved. Never true of an `UNMEASURED`."""
        return self.outcome is not Outcome.UNMEASURED and not self.proven

    @property
    def gap(self) -> float | None:
        """How far the wrong side of the limit it landed, or None if it did not."""
        return self.result.gap

    def __str__(self) -> str:
        if self.outcome is Outcome.UNMEASURED:
            return f"{self.name}: not checked — {self.result.reason}"
        line = str(self.result)
        if self.provisional:
            line += f" [provisional: {self._why_provisional()}]"
        return line

    def _why_provisional(self) -> str:
        measure = self.rule.measure
        if self.direction is BoundDirection.UPPER_BOUND:
            which = f"{measure} is sampled and reads at or above the true value"
        elif self.direction is BoundDirection.LOWER_BOUND:
            which = f"{measure} is sampled and reads at or below the true value"
        else:
            which = f"{measure} was approximated and nothing declares which way it errs"
        verdict = "pass" if self.ok else "failure"
        return (
            f"{which}, so this {verdict} does not follow from it. A denser scan moves "
            "the bound; it does not make this a proof."
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rule": self.rule.to_dict(),
            **self.result.to_dict(),
            "sampled": self.sampled,
            "bound_direction": str(self.direction),
            "proven": self.proven,
            "provisional": self.provisional,
        }
        if self.provisional:
            out["provisional_because"] = self._why_provisional()
        return out


class RuleReport:
    """Every rule checked against one part, and how much of it is established.

    `ok` answers "did the part satisfy the rules". `proven` answers "does the
    evidence support that", and it is the stricter of the two — a suite of
    provisional passes is a screening result, not a clearance.
    """

    __slots__ = ("assertions", "results")

    def __init__(self, results: Iterable[RuleResult], assertions: AssertionReport) -> None:
        self.results = tuple(results)
        #: The underlying assertion report, kept so a caller that already speaks
        #: `AssertionReport` — the Phase-5 machinery — needs no adapter.
        self.assertions = assertions

    @property
    def passed(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.PASSED)

    @property
    def failed(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.FAILED)

    @property
    def unmeasured(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.outcome is Outcome.UNMEASURED)

    @property
    def provisional(self) -> tuple[RuleResult, ...]:
        return tuple(r for r in self.results if r.provisional)

    @property
    def ok(self) -> bool:
        """Everything checked and everything satisfied. An unmeasured rule is not a pass."""
        return bool(self.results) and not self.failed and not self.unmeasured

    @property
    def proven(self) -> bool:
        """`ok`, and no verdict resting on a sampled number pointing the wrong way."""
        return self.ok and not self.provisional

    def __bool__(self) -> bool:
        return self.ok

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Any:
        return iter(self.results)

    def summary(self) -> str:
        if not self.results:
            return "No design rules to check."
        bits = [f"{len(self.passed)}/{len(self.results)} satisfied"]
        if self.failed:
            bits.append(f"{len(self.failed)} broken")
        if self.unmeasured:
            bits.append(f"{len(self.unmeasured)} not measurable")
        if self.provisional:
            bits.append(f"{len(self.provisional)} provisional")
        line = ", ".join(bits) + "."
        if self.ok and not self.proven:
            line += (
                " Every rule is satisfied, but not every verdict is proved: the ones "
                "marked provisional rest on sampled measurements that could hide a "
                "violation between samples."
            )
        detail = [str(r) for r in self.results if r.outcome is not Outcome.PASSED or r.provisional]
        return line + ("\n" + "\n".join(detail) if detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "proven": self.proven,
            "satisfied": len(self.passed),
            "broken": len(self.failed),
            "unmeasured": len(self.unmeasured),
            "provisional": len(self.provisional),
            "results": [result.to_dict() for result in self.results],
        }


def check_rules(rules: Iterable[Rule], measurements: Mapping[str, Any]) -> RuleReport:
    """Check every rule against one measurement payload.

    The comparison is `app.design.assertions.check_assertions`, unchanged and
    unwrapped; everything this function adds is the reading of the provenance
    sidecar against the declared bound direction.
    """
    ordered = tuple(rules)
    report = check_assertions((rule.as_assertion() for rule in ordered), measurements)
    results = [
        RuleResult(
            rule=rule,
            result=result,
            direction=bound_direction(rule.measure),
            sampled=_rests_on_a_bound(measurements, rule.measure, result),
        )
        for rule, result in zip(ordered, report.results, strict=True)
    ]
    return RuleReport(results, report)


def _rests_on_a_bound(
    measurements: Mapping[str, Any], measure: str, result: AssertionResult
) -> bool:
    """Whether this verdict rests on a bound rather than on a number.

    The payload's own sidecar is consulted first and is the truth for the run: a
    backend that measures wall thickness exactly says `MEASURED` and is never
    penalised for the fact that another one samples it.

    **A payload that says nothing at all about a normally-sampled quantity is
    the case this function exists for**, and it is not hypothetical.
    `ThicknessReport.to_payload` writes `thinnest_point_mm` with no sidecar
    entry of its own, so `app.design.assertions` — which resolves silence to
    "not approximate", correctly, because an assertion only reports what the
    payload claims — hands back `approximate=False` for a ray-cast location.
    Reading that silence as exact here would print `proven` on a number nobody
    measured, which is the one failure this module exists to prevent.
    `app.kernel.provenance` is explicit that no entry is *not* `MEASURED`, so
    the contract's `typical_basis` is the last thing left to read; it decides
    nothing about the number's quality, only whether a verdict on it may be
    called proved.
    """
    if result.approximate:
        return True
    if payload_states_a_basis(measurements, measure):
        return False
    return typical_basis_is_approximated(measure)


__all__ = [
    "Rule",
    "RuleReport",
    "RuleResult",
    "check_rules",
]
