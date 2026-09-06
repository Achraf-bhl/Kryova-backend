"""Verifying a requirement set against a part that was actually built.

Master plan **11.2** (requirement → assertion binding) and **11.4** (coverage:
verified / by-what-evidence / unverified).

The comparing is `app.design.assertions`' job and stays there. What this module
adds is the three things a requirements report needs that an assertion report
cannot supply:

**1. Nothing is omitted.** `RequirementSet.assertions()` can only produce
assertions for requirements that name a measurement, so piping that into
`check_assertions` would drop every requirement waiting on a capability — the
duty cycle, the service life, the cost ceiling — and report green over a set
nobody finished checking. `verify_requirements` starts from the *set*, not from
the assertions, so every active requirement appears in the report exactly once,
including the ones that could never have been checked. That is the single
failure mode this codebase exists to refuse, restated one layer up from
`Outcome.UNMEASURED`.

**2. Coverage is a first-class output, and coverage is not the same as passing.**
A requirement that was measured and *failed* has been verified: we know the
answer, and the answer is no. A requirement that could not be measured has not
been verified, and it is the only kind that damages coverage. So
`Coverage.fraction` counts `PASSED` and `FAILED` alike and counts `UNMEASURED`
against, while `RequirementReport.ok` counts only `PASSED`. Reading one for the
other is the mistake this split exists to make impossible: a set can be 100%
covered and entirely violated, or entirely passing at 40% coverage, and those are
different situations with different next actions.

**3. Evidence, per requirement.** *By what evidence* is half of 11.4, and the
answer is already in the measurement payload: `app.kernel.provenance` records per
path whether a number was integrated exactly, sampled, or unavailable with a
reason. So evidence is read from the sidecar rather than invented here, and a
requirement met by a ray-cast wall thickness is visibly not the same as one met
by an integrated mass. `Coverage` reports the split, because a signing engineer
who is told "94% verified" will ask how much of it was sampled, and should not
have to.

**A result is bound to what produced it** (Decision 3). `bound_to` carries the
geometry version, plan digest, backend and solver version the caller had — free
text, because this package cannot know what a caller measures with — and
`contract_version` is stamped automatically from the measurement vocabulary in
force. A report with no binding says so rather than looking authoritative.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.design.assertions import AssertionResult, Outcome, check_assertions
from app.design.params import ResolvedParameters
from app.requirements import vocabulary
from app.requirements.errors import RequirementError
from app.requirements.model import Requirement, RequirementSet

#: What is recorded when a payload carries no provenance sidecar at all. Not
#: "measured": a payload written before provenance existed, or by a third-party
#: backend, is making no claim either way, and inventing one would be exactly the
#: false confidence `app.kernel.provenance` exists to prevent.
UNRECORDED: Final = "unrecorded"

#: What is recorded when there was nothing to measure in the first place.
NOT_ATTEMPTED: Final = "not attempted"


@dataclass(frozen=True)
class Evidence:
    """How one requirement's number was arrived at — 11.4's "by what evidence".

    `basis` is `app.kernel.provenance.Basis` as a string, plus two values that
    basis has no member for and should not: `unrecorded` (the payload carries no
    sidecar) and `not attempted` (the requirement had nothing to measure).
    Strings rather than a widened enum, because widening `Basis` would put
    "nobody asked" into the vocabulary every kernel measurement is described in.
    """

    basis: str = NOT_ATTEMPTED
    method: str = ""

    #: The measurement path the evidence is about, so a reader of a coverage
    #: table does not have to look the requirement back up to see what was read.
    path: str = ""

    @property
    def exact(self) -> bool:
        """True only for a number that was integrated or evaluated, not sampled."""
        return self.basis == "measured"

    @property
    def approximate(self) -> bool:
        return self.basis == "approximated"

    def __str__(self) -> str:
        if self.basis == NOT_ATTEMPTED:
            return "no measurement attempted"
        head = f"{self.path}: {self.basis}" if self.path else self.basis
        return f"{head} ({self.method})" if self.method else head

    def to_dict(self) -> dict[str, str]:
        out = {"basis": self.basis}
        if self.path:
            out["path"] = self.path
        if self.method:
            out["method"] = self.method
        return out


@dataclass(frozen=True)
class RequirementResult:
    """One requirement, checked, with the evidence that checked it."""

    requirement: Requirement
    outcome: Outcome
    evidence: Evidence = field(default_factory=Evidence)

    #: The underlying assertion result, when there was one to run. `None` for a
    #: requirement with nothing to measure — which is a real state and not an
    #: error, and is why this is not simply an `AssertionResult`.
    assertion: AssertionResult | None = None

    #: Why it could not be checked, when it could not be. Empty otherwise.
    reason: str = ""

    @property
    def id(self) -> str:
        return self.requirement.id

    @property
    def met(self) -> bool:
        return self.outcome is Outcome.PASSED

    @property
    def verified(self) -> bool:
        """Whether we know the answer — *not* whether the answer is yes.

        A violated requirement is verified. This is the distinction the whole
        coverage half of 11.4 rests on.
        """
        return self.outcome in (Outcome.PASSED, Outcome.FAILED)

    @property
    def measured(self) -> float | None:
        return None if self.assertion is None else self.assertion.measured

    @property
    def expected(self) -> float | None:
        return None if self.assertion is None else self.assertion.expected

    @property
    def gap(self) -> float | None:
        """How far the wrong side of the target it landed — what a repair aims at.

        Handed straight through from `AssertionResult.gap` so
        `app.design.sensitivity.aim` can take a requirement's shortfall without
        this package knowing anything about geometry.
        """
        return None if self.assertion is None else self.assertion.gap

    def __str__(self) -> str:
        req = self.requirement
        if self.outcome is Outcome.UNMEASURED:
            return f"{req.id} NOT VERIFIED — {self.reason} ({req.statement})"
        if self.outcome is Outcome.PASSED:
            tail = "" if self.evidence.exact else f" [{self.evidence.basis}]"
            return f"{req.id} met{tail} — {req.statement}"
        gap = self.gap
        over = f", out by {abs(gap):g}{(' ' + req.unit) if req.unit else ''}" if gap else ""
        return f"{req.id} NOT MET{over} — {req.statement}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "outcome": str(self.outcome),
            "statement": self.requirement.statement,
            "source": str(self.requirement.source),
            "evidence": self.evidence.to_dict(),
        }
        if self.requirement.measure is not None:
            out["measure"] = self.requirement.measure
        if self.measured is not None:
            out["measured"] = self.measured
        if self.expected is not None:
            out["target"] = self.expected
        if self.gap is not None:
            out["gap"] = self.gap
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True)
class Coverage:
    """How much of the requirement set has actually been checked — 11.4.

    Counted over *active* requirements only. An obsolete one is retired, not
    unverified, and counting it against coverage would make retiring a
    requirement look like losing ground.
    """

    total: int = 0
    passed: int = 0
    failed: int = 0
    unverified: int = 0
    by_measurement: int = 0
    by_approximation: int = 0

    @property
    def verified(self) -> int:
        """Checked, whether it passed or not."""
        return self.passed + self.failed

    @property
    def fraction(self) -> float:
        """Verified over total, 0.0 to 1.0. An empty set is 0.0, not 1.0.

        A set with no requirements has verified nothing, and reporting 100% for
        it is how an empty specification comes to look finished.
        """
        return (self.verified / self.total) if self.total else 0.0

    def __str__(self) -> str:
        if not self.total:
            return "No requirements to verify."
        line = (
            f"{self.verified}/{self.total} requirements verified "
            f"({self.fraction * 100:.0f}%): {self.passed} met, {self.failed} not met, "
            f"{self.unverified} not verifiable."
        )
        if self.by_approximation:
            line += (
                f" {self.by_approximation} of the {self.verified} verified rest on an "
                "approximated measurement."
            )
        return line

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "verified": self.verified,
            "fraction": self.fraction,
            "passed": self.passed,
            "failed": self.failed,
            "unverified": self.unverified,
            "by_measurement": self.by_measurement,
            "by_approximation": self.by_approximation,
        }


@dataclass(frozen=True)
class RequirementReport:
    """Every active requirement in one set, checked at once.

    Truthy only when every active requirement was checked *and* met. An
    unverified requirement makes the report falsey — the conservative reading and
    the correct one, and the same rule `AssertionReport` applies.
    """

    name: str = ""
    results: tuple[RequirementResult, ...] = ()
    coverage: Coverage = field(default_factory=Coverage)

    #: Requirements excluded because they are retired. Listed rather than dropped:
    #: a report that silently omits them cannot be told apart from one run against
    #: a set somebody quietly deleted requirements from.
    obsolete: tuple[Requirement, ...] = ()

    #: What produced the numbers — geometry version, plan digest, backend, solver.
    #: Decision 3: a result is bound to what produced it. Free text because this
    #: package cannot know what the caller measured with.
    bound_to: Mapping[str, str] = field(default_factory=dict)

    #: Measurement contract version in force when this ran, stamped automatically.
    contract_version: str = ""

    @property
    def met(self) -> tuple[RequirementResult, ...]:
        return tuple(one for one in self.results if one.outcome is Outcome.PASSED)

    @property
    def violated(self) -> tuple[RequirementResult, ...]:
        return tuple(one for one in self.results if one.outcome is Outcome.FAILED)

    @property
    def unverified(self) -> tuple[RequirementResult, ...]:
        """The ones nobody checked. Never silently omitted, never a pass."""
        return tuple(one for one in self.results if one.outcome is Outcome.UNMEASURED)

    @property
    def ok(self) -> bool:
        return bool(self.results) and not self.violated and not self.unverified

    @property
    def traceable(self) -> bool:
        """Whether this report says what produced it (Decision 3)."""
        return bool(self.bound_to)

    def __bool__(self) -> bool:
        return self.ok

    def __iter__(self) -> Any:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def result_for(self, requirement_id: str) -> RequirementResult:
        for one in self.results:
            if one.id == requirement_id:
                return one
        known = ", ".join(one.id for one in self.results) or "none"
        raise RequirementError(
            f"{requirement_id!r} is not in this report. Checked here: {known}. An "
            "obsolete requirement is excluded from verification and listed separately."
        )

    def summary(self) -> str:
        """The sentence a person reads. It can never be mistaken for coverage.

        Deliberately never says "the requirements passed": it says how many were
        met, how many were not, and how many were never checked, because those
        are three different facts and only one of them is good news.
        """
        if not self.results:
            return f"{self.name or 'This set'} has no active requirements to verify."
        lines = [str(self.coverage)]
        if self.obsolete:
            lines[0] += f" {len(self.obsolete)} retired and not counted."
        if not self.traceable:
            lines.append(
                "Not bound to a geometry version — this report cannot be traced back to "
                "what produced it."
            )
        lines.extend(str(one) for one in self.results if one.outcome is not Outcome.PASSED)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "coverage": self.coverage.to_dict(),
            "contract_version": self.contract_version,
            "bound_to": dict(sorted(self.bound_to.items())),
            "results": [one.to_dict() for one in self.results],
            "obsolete": [one.id for one in self.obsolete],
        }


def verify_requirements(
    requirements: RequirementSet | Iterable[Requirement],
    measurements: Mapping[str, Any],
    *,
    parameters: ResolvedParameters | None = None,
    bound_to: Mapping[str, str] | None = None,
) -> RequirementReport:
    """Check a requirement set against one measurement payload.

    The payload is whatever measured the part — `catia_measure`'s result, an
    `OcctRunner` measurement, `machine_checks.machine_measurements`, or the union
    of those. This package never measures anything itself, for the reason
    `app.design.execute` never opens a socket: it keeps every requirement testable
    with no seat, no kernel and no network.

    Every active requirement comes back exactly once. The ones with nothing to
    measure come back `UNMEASURED` carrying their `needs` as the reason, which is
    what makes them count against coverage instead of vanishing from it.
    """
    given = (
        requirements
        if isinstance(requirements, RequirementSet)
        else RequirementSet.of("requirements", requirements)
    )
    active = given.active

    measurable = [one for one in active if one.measurable]
    checked = check_assertions(
        (one.assertion() for one in measurable), measurements, parameters=parameters
    )
    by_id = {one.name: one for one in checked.results}

    results: list[RequirementResult] = []
    for one in active:
        if not one.measurable:
            results.append(
                RequirementResult(
                    requirement=one,
                    outcome=Outcome.UNMEASURED,
                    reason=(
                        f"nothing in this build measures it: {one.needs.rstrip('.')}."
                    ),
                    evidence=Evidence(basis=NOT_ATTEMPTED),
                )
            )
            continue
        found = by_id[one.id]
        results.append(
            RequirementResult(
                requirement=one,
                outcome=found.outcome,
                assertion=found,
                reason=found.reason,
                evidence=_evidence(measurements, str(one.measure), found),
            )
        )

    return RequirementReport(
        name=given.name,
        results=tuple(results),
        coverage=_coverage(results),
        obsolete=given.obsolete,
        bound_to=dict(bound_to or {}),
        contract_version=vocabulary.contract_version(),
    )


def _evidence(
    measurements: Mapping[str, Any], path: str, result: AssertionResult
) -> Evidence:
    """Read how the number was arrived at from the payload's provenance sidecar.

    Read rather than inferred. `AssertionResult.approximate` already folds the
    payload-wide fallback flag into a boolean, which is the right answer for "may
    I trust this number" and the wrong one for "by what evidence" — it cannot
    tell an exactly integrated mass from a payload that recorded nothing at all,
    and a coverage report that calls the second one measured is making a claim
    nobody made.
    """
    provenance = _provenance()
    basis = provenance.basis_of(measurements, path)
    if basis is None:
        # No sidecar entry. Fall back to the payload-wide flag the CATIA mock sets,
        # which says the whole payload was estimated — and say `unrecorded` rather
        # than `measured` when even that is absent.
        if result.approximate:
            return Evidence(basis=str(provenance.Basis.APPROXIMATED), path=path)
        return Evidence(basis=UNRECORDED, path=path)
    return Evidence(
        basis=str(basis),
        method=provenance.method_for(measurements, path),
        path=path,
    )


def _coverage(results: Iterable[RequirementResult]) -> Coverage:
    passed = failed = unverified = by_measurement = by_approximation = 0
    total = 0
    for one in results:
        total += 1
        if one.outcome is Outcome.PASSED:
            passed += 1
        elif one.outcome is Outcome.FAILED:
            failed += 1
        else:
            unverified += 1
            continue
        # Only a verified requirement contributes evidence. An unverified one has
        # none by definition, and counting its absent basis would put "unrecorded"
        # rows into a table that is supposed to say what the checking rested on.
        if one.evidence.exact:
            by_measurement += 1
        elif one.evidence.approximate:
            by_approximation += 1
    return Coverage(
        total=total,
        passed=passed,
        failed=failed,
        unverified=unverified,
        by_measurement=by_measurement,
        by_approximation=by_approximation,
    )


def _provenance() -> Any:
    """`app.kernel.provenance`, imported on use — see `assertions._provenance`.

    Same reason, restated because it is easy to undo: importing it at module load
    executes `app/kernel/__init__.py` and pulls the OCCT binding in with it, and a
    requirements document must be readable and verifiable on a machine that has
    no geometry kernel at all.
    """
    from app.kernel import provenance

    return provenance


__all__ = [
    "NOT_ATTEMPTED",
    "UNRECORDED",
    "Coverage",
    "Evidence",
    "RequirementReport",
    "RequirementResult",
    "verify_requirements",
]
