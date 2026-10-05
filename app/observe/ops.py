"""Turn cost, CATIA operation latency and queue depth, read from the rows that record them
(ROAD_TO_10 9.6).

The span ledger (`ledger.py`) is one process's memory. These three are durable: `turn_metrics`
holds a row per agent turn, `catia_operations` one per call to the seat, `simulation_jobs` the
queue. Reading them is bounded -- aggregates in SQL for the turn table, and for the bridge the
newest `MAX_OPERATIONS` rows reduced in Python with the same `Distribution` the span ledger uses,
so there is one definition of "p95" in the product and one place that says when it is the maximum.

A cost of NULL is "no price configured", not free (`TurnMetric.cost_micro_usd`), so a turn with
no price is *counted apart* and never added to the total as zero.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CatiaOperation, JobStatus, SimulationJob, TurnMetric
from app.observe.records import Distribution

#: The newest bridge operations read per request. Bounded so a busy seat cannot make the
#: console slow; the response says it is the newest N when the window held more.
MAX_OPERATIONS = 20_000


@dataclass(frozen=True, slots=True)
class TurnCost:
    turns: int
    #: Turns with a recorded price; the figures below are over these only.
    priced_turns: int
    unpriced_turns: int
    total_micro_usd: int
    mean_micro_usd: int | None
    max_micro_usd: int | None
    median_wall_ms: int | None
    p95_wall_ms: int | None
    #: Turns by how they ended, so "most turns hit the step budget" is visible.
    by_stop_reason: dict[str, int]


@dataclass(frozen=True, slots=True)
class OperationLatency:
    tool: str
    count: int
    failures: int
    median_ms: int
    p95_ms: int
    max_ms: int
    p95_is_the_maximum: bool


@dataclass(frozen=True, slots=True)
class BridgeLatency:
    operations: tuple[OperationLatency, ...]
    #: True when the window held more than `MAX_OPERATIONS` rows and only the newest were read.
    truncated: bool


def turn_cost(db: Session, since: datetime) -> TurnCost:
    window = TurnMetric.created_at >= since
    turns = int(db.scalar(select(func.count()).where(window)) or 0)
    priced, total, biggest = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(TurnMetric.cost_micro_usd), 0),
            func.max(TurnMetric.cost_micro_usd),
        ).where(window, TurnMetric.cost_micro_usd.is_not(None))
    ).one()
    priced = int(priced)
    walls = [
        int(wall)
        for (wall,) in db.execute(select(TurnMetric.wall_ms).where(window)).all()
        if wall is not None
    ]
    spread = Distribution.of([float(w) for w in walls]) if walls else None
    reasons = {
        str(reason): int(count)
        for reason, count in db.execute(
            select(TurnMetric.stop_reason, func.count()).where(window).group_by(
                TurnMetric.stop_reason
            )
        ).all()
    }
    return TurnCost(
        turns=turns,
        priced_turns=priced,
        unpriced_turns=turns - priced,
        total_micro_usd=int(total),
        mean_micro_usd=int(total) // priced if priced else None,
        max_micro_usd=int(biggest) if biggest is not None else None,
        median_wall_ms=int(spread.median_seconds) if spread else None,
        p95_wall_ms=int(spread.p95_seconds) if spread else None,
        by_stop_reason=dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
    )


def bridge_latency(db: Session, since: datetime) -> BridgeLatency:
    rows = db.execute(
        select(CatiaOperation.tool, CatiaOperation.duration_ms, CatiaOperation.ok)
        .where(CatiaOperation.created_at >= since)
        .order_by(CatiaOperation.created_at.desc())
        .limit(MAX_OPERATIONS + 1)
    ).all()
    truncated = len(rows) > MAX_OPERATIONS
    durations: dict[str, list[float]] = defaultdict(list)
    failed: dict[str, int] = defaultdict(int)
    for tool, duration_ms, ok in rows[:MAX_OPERATIONS]:
        durations[str(tool)].append(float(duration_ms or 0))
        if not ok:
            failed[str(tool)] += 1
    operations = []
    for tool, samples in durations.items():
        spread = Distribution.of(samples)
        operations.append(
            OperationLatency(
                tool=tool,
                count=spread.count,
                failures=failed[tool],
                median_ms=int(spread.median_seconds),
                p95_ms=int(spread.p95_seconds),
                max_ms=int(spread.max_seconds),
                p95_is_the_maximum=spread.p95_is_the_maximum,
            )
        )
    # Slowest tail first: the operator is looking for what is slow, not for the alphabet.
    operations.sort(key=lambda row: (-row.p95_ms, row.tool))
    return BridgeLatency(operations=tuple(operations), truncated=truncated)


def queue_depth(db: Session) -> dict[str, int]:
    depth = {member.value: 0 for member in JobStatus}
    for status, count in db.execute(
        select(SimulationJob.status, func.count()).group_by(SimulationJob.status)
    ).all():
        depth[JobStatus(status).value] = int(count)
    return depth
