"""Running one plan on two backends and asking whether they built the same thing.

Master plan Phase 1.5. This is `A1` — "validate the 201 operations against a real seat"
— done properly: instead of a human clicking through operations and ticking a matrix, the
same compiled `Plan` is executed twice and the resulting geometry is compared. It runs
unattended, it covers every operation a design actually uses, and it produces a *finding*
rather than an impression.

**A divergence names the quantity, not just the fact.** "volume agrees, centre of mass
does not" localises a bug immediately; `False` starts a bisect. That is why
`app.kernel.measurement.compare` returns keys.

**Three outcomes, not two.** A backend that has not implemented an operation is *not* a
failure — it is a known gap, and counting it as a failure would make the coverage figure
useless and the harness something people stop running. `OperationNotSupported` is
therefore separated out and reported as coverage.

Backend-neutral on purpose: it takes two `CallRunner`s. In production one is an
`OcctRunner` and the other is bound to `app.catia.dispatch`; in a test both can be
OCCT, which is how determinism (1.6) is checked with the same machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.design.compile import Plan
from app.design.execute import BuildReport, CallRunner, execute_plan
from app.kernel.errors import OperationNotSupported
from app.kernel.measurement import CONFORMANCE_TOLERANCE_MM3, compare


@dataclass(frozen=True)
class ConformanceResult:
    """What comparing two backends on one plan found.

    Truthy only when both backends built the plan and every compared quantity agreed.
    A plan that neither could build is not a pass.
    """

    design: str
    plan_digest: str

    left_name: str
    right_name: str

    left: BuildReport | None = None
    right: BuildReport | None = None

    #: Measured quantities that disagree beyond tolerance.
    divergences: tuple[str, ...] = ()

    #: Operations one backend does not implement. A known gap, never a failure.
    unsupported: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    #: Why a backend stopped, when it stopped for a reason other than coverage.
    failures: Mapping[str, str] = field(default_factory=dict)

    @property
    def comparable(self) -> bool:
        """Did both backends get far enough for a comparison to mean anything?"""
        return bool(self.left and self.left.ok and self.right and self.right.ok)

    @property
    def agrees(self) -> bool:
        return self.comparable and not self.divergences

    def __bool__(self) -> bool:
        return self.agrees

    def summary(self) -> str:
        if self.agrees:
            return (
                f"{self.design}: {self.left_name} and {self.right_name} built the same "
                f"geometry across {len(self.left or ())} calls."
            )
        if self.unsupported and not self.comparable:
            gaps = "; ".join(
                f"{backend} cannot do {', '.join(tools)}"
                for backend, tools in sorted(self.unsupported.items())
            )
            return f"{self.design}: not comparable — {gaps}."
        if self.failures:
            stops = "; ".join(f"{backend}: {why}" for backend, why in sorted(self.failures.items()))
            return f"{self.design}: a backend stopped — {stops}."
        return (
            f"{self.design}: the backends DISAGREE on "
            f"{', '.join(self.divergences)}."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "plan_digest": self.plan_digest,
            "backends": [self.left_name, self.right_name],
            "agrees": self.agrees,
            "comparable": self.comparable,
            "divergences": list(self.divergences),
            "unsupported": {k: list(v) for k, v in sorted(self.unsupported.items())},
            "failures": dict(sorted(self.failures.items())),
        }


def compare_backends(
    plan: Plan,
    left: CallRunner,
    right: CallRunner,
    *,
    left_name: str = "occt",
    right_name: str = "catia",
    tolerance: float = CONFORMANCE_TOLERANCE_MM3,
) -> ConformanceResult:
    """Build `plan` on both backends and report what differs.

    Neither backend's failure stops the other from running: knowing that OCCT built it
    and CATIA did not is the whole point, and short-circuiting would hide half the
    answer.
    """
    left_report, left_gaps = _run(plan, left)
    right_report, right_gaps = _run(plan, right)

    unsupported: dict[str, tuple[str, ...]] = {}
    if left_gaps:
        unsupported[left_name] = left_gaps
    if right_gaps:
        unsupported[right_name] = right_gaps

    failures: dict[str, str] = {}
    for name, report, gaps in (
        (left_name, left_report, left_gaps),
        (right_name, right_report, right_gaps),
    ):
        # A build that stopped because of a coverage gap is reported as coverage, not
        # as a failure; anything else is a real stop worth naming.
        if report is not None and not report.ok and not gaps and report.failure is not None:
            failures[name] = str(report.failure)

    divergences: tuple[str, ...] = ()
    if left_report and left_report.ok and right_report and right_report.ok:
        divergences = tuple(
            compare(
                left_report.last_result(),
                right_report.last_result(),
                tolerance=tolerance,
            )
        )

    return ConformanceResult(
        design=plan.design,
        plan_digest=plan.digest(),
        left_name=left_name,
        right_name=right_name,
        left=left_report,
        right=right_report,
        divergences=divergences,
        unsupported=unsupported,
        failures=failures,
    )


def _run(plan: Plan, runner: CallRunner) -> tuple[BuildReport | None, tuple[str, ...]]:
    """Execute a plan, separating coverage gaps from real failures.

    `execute_plan` turns every exception into a `BuildFailure`, which is right for a
    build and wrong for this: a missing operation and a broken one look identical in a
    report. So the runner is wrapped to record `OperationNotSupported` as it happens.
    """
    gaps: list[str] = []

    def recording(tool: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            return runner(tool, arguments)
        except OperationNotSupported as unsupported:
            gaps.append(unsupported.subject)
            raise

    report = execute_plan(plan, recording)
    return report, tuple(gaps)


__all__ = ["ConformanceResult", "compare_backends"]
