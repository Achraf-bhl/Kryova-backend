"""How many solver workers the fleet should run, from the job table alone (master plan E15.2).

The half of autoscaling that belongs to the application. **This module decides a number and
changes nothing.** An orchestrator acts on it (a Kubernetes HPA on an external metric, KEDA, a
cron that resizes a VM scale set). The deployment this was written for has no fleet, so the
actuating half is not here and not claimed.

Why the application owns the number rather than the orchestrator's own CPU rule: a solver
worker's CPU is **flat at 100% whether one job is waiting or four hundred**, so a CPU target
scales on nothing. The signal that means "users are waiting" is the durable queue, which is a
table here. So the input is three counts off `SimulationJob`, and the rule is written down
before anyone tunes it:

1. **Enough workers for the backlog.** `ceil((queued + running) / jobs_per_worker)`.
2. **Never below what is running.** A worker holding a job must not be the one removed. A solve
   already handed to CalculiX cannot be stopped (`app/core/interruption.py`), so taking its
   worker away discards compute the user is billed for.
3. **Wait time wins over the backlog count.** If the oldest queued job has waited longer than
   `target_wait_s`, the answer is at least one more than the fleet has now. The backlog rule
   alone would hold a fleet of one while one very slow job blocks four short ones.
4. **Down one step at a time.** Scaling down by the whole difference in one step is how a queue
   that drains for thirty seconds between two bursts loses its warm workers twice.
5. **Clamped to `[min_workers, max_workers]`**, and the answer says when the ceiling clipped it.
   A capped recommendation that does not say so reads as "the fleet is big enough".

Every answer carries its reason as a sentence, because the one person who reads this number is
an operator in the middle of an incident.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: The rule's name in every recommendation, so a dashboard can tell two policies apart.
POLICY_NAME: Final = "backlog-and-wait, down one step"


@dataclass(frozen=True)
class ScalingPolicy:
    """The four numbers an operator sets. None has a default in this dataclass on purpose."""

    min_workers: int
    max_workers: int
    jobs_per_worker: int
    target_wait_s: float

    def __post_init__(self) -> None:
        if self.min_workers < 0:
            raise ValueError(f"min_workers cannot be negative; got {self.min_workers}.")
        if self.max_workers < max(1, self.min_workers):
            raise ValueError(
                f"max_workers ({self.max_workers}) must be at least 1 and at least min_workers "
                f"({self.min_workers}). A fleet that may never run a worker never runs a job."
            )
        if self.jobs_per_worker < 1:
            raise ValueError(
                f"jobs_per_worker must be at least 1; got {self.jobs_per_worker}. It is the "
                "JOB_WORKERS pool size inside one worker process."
            )
        if not (self.target_wait_s > 0.0 and math.isfinite(self.target_wait_s)):
            raise ValueError(
                f"target_wait_s must be positive and finite; got {self.target_wait_s!r}."
            )


@dataclass(frozen=True)
class QueueSnapshot:
    """The queue as the job table holds it at one instant."""

    queued: int
    running: int
    #: How long the oldest `QUEUED` job has waited, in seconds; None when nothing is queued.
    oldest_wait_s: float | None

    def __post_init__(self) -> None:
        if self.queued < 0 or self.running < 0:
            raise ValueError("A job count cannot be negative.")
        if self.queued == 0 and self.oldest_wait_s is not None:
            raise ValueError("An empty queue has no oldest job; pass oldest_wait_s=None.")
        if self.queued > 0 and self.oldest_wait_s is None:
            raise ValueError(
                "A non-empty queue has an oldest job; its wait is what rule 3 reads."
            )


@dataclass(frozen=True)
class Recommendation:
    """How many workers, and why, in words."""

    desired_workers: int
    #: The fleet size the caller said it has, or None when it did not say.
    current_workers: int | None
    reason: str
    #: True when `max_workers` cut the answer short of what the queue asks for.
    capped: bool
    policy: str = POLICY_NAME


def recommend(
    snapshot: QueueSnapshot, policy: ScalingPolicy, *, current_workers: int | None = None
) -> Recommendation:
    """The worker count the rules in the module docstring give, with the rule that decided it."""
    if current_workers is not None and current_workers < 0:
        raise ValueError(f"current_workers cannot be negative; got {current_workers}.")

    per = policy.jobs_per_worker
    for_backlog = math.ceil((snapshot.queued + snapshot.running) / per)
    for_running = math.ceil(snapshot.running / per)
    wanted = max(for_backlog, for_running)
    reasons = [
        f"{snapshot.queued} queued and {snapshot.running} running at {per} per worker "
        f"need {for_backlog}"
    ]

    waited_too_long = (
        snapshot.oldest_wait_s is not None and snapshot.oldest_wait_s > policy.target_wait_s
    )
    if waited_too_long and current_workers is not None and wanted <= current_workers:
        wanted = current_workers + 1
        reasons.append(
            f"the oldest job has waited {snapshot.oldest_wait_s:.0f} s against a "
            f"{policy.target_wait_s:.0f} s target, so one more than the {current_workers} running"
        )

    if current_workers is not None and wanted < current_workers - 1:
        floor = max(current_workers - 1, for_running)
        reasons.append(f"down one step at a time, from {current_workers} to {floor}")
        wanted = floor

    capped = wanted > policy.max_workers
    desired = min(max(wanted, policy.min_workers), policy.max_workers)
    if capped:
        reasons.append(
            f"capped at max_workers = {policy.max_workers}; the queue asks for {wanted}"
        )
    elif desired != wanted:
        reasons.append(f"raised to min_workers = {policy.min_workers}")

    return Recommendation(
        desired_workers=desired,
        current_workers=current_workers,
        reason="; ".join(reasons) + ".",
        capped=capped,
    )


def wait_seconds(oldest_created_at: datetime | None, now: datetime) -> float | None:
    """Seconds since the oldest queued job was created, never negative (clocks drift)."""
    if oldest_created_at is None:
        return None
    return max(0.0, (now - oldest_created_at).total_seconds())


__all__ = [
    "POLICY_NAME",
    "QueueSnapshot",
    "Recommendation",
    "ScalingPolicy",
    "recommend",
    "wait_seconds",
]
