"""Is the prompt cache working -- as a number an operator can watch, and a drop they are told about.

On a hosted model the prompt is the bill, and the part of it that makes the bill small is
the cache: a request that begins with the same bytes as the last one is billed at a fraction
of the price (`app/ai/pricing.py`). Nothing errors when that stops working. Something that
changes between steps -- a timestamp, a counter, an unsorted set -- slips ahead of the
transcript and every step is quietly billed at the full price again. `turn_metrics` records
the hit count per turn (ROAD_TO_10 1.1, 0.2); this reads it back (1.11).

Three rules, each pinned by a test that fails when it is removed:

1. **The rate is weighted by tokens, not averaged over turns.** A one-step turn and a
   sixty-step turn are not the same amount of money; `sum(cached) / sum(prompt)` is the
   fraction of input actually billed at the cache price.
2. **A rate of zero is not a drop.** A provider that does not report cached tokens, or one
   with no prompt cache, records 0 on every turn, and an alert that fired for it would be
   noise from day one. The alert needs a *healthy* earlier stretch and a recent stretch that
   fell well below it; otherwise `reported` says whether the number means anything.
3. **Too few turns is "not enough", never a rate.** Ten turns of a quiet night is not a
   baseline. `None` is returned for the comparison, and the console renders that as the
   sentence it is.

Pure: `assess` takes numbers and returns a verdict, so the thresholds are testable without a
database. `read` is the one place that touches the table, in two bounded queries.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TurnMetric

#: The newest turns that are compared against everything older in the window.
RECENT_TURNS = 20
#: Fewest recent / earlier turns that make a comparison worth showing.
MIN_RECENT_TURNS = 10
MIN_BASELINE_TURNS = 20
#: The earlier stretch has to have been this good for a fall to mean something. Below it the
#: provider is probably not reporting a cache, and "fell from 3% to 1%" is not an incident.
HEALTHY_RATE = 0.4
#: A recent rate under this fraction of the earlier one raises the alert.
DROP_FACTOR = 0.6


@dataclass(frozen=True, slots=True)
class CacheHealth:
    turns: int
    prompt_tokens: int
    cached_prompt_tokens: int
    #: Over the whole window; None when there is nothing to divide by.
    hit_rate: float | None
    recent_turns: int
    #: Over the newest `RECENT_TURNS`; None when fewer than `MIN_RECENT_TURNS`.
    recent_hit_rate: float | None
    #: Whether any turn in the window recorded a cached count. False means the rate is
    #: unmeasured on this deployment, not that the cache is missing (rule 2).
    reported: bool
    alert: str | None


def _rate(prompt: int, cached: int) -> float | None:
    if prompt <= 0:
        return None
    return min(1.0, max(0, cached) / prompt)


def _percent(rate: float) -> str:
    return f"{round(rate * 100)}%"


def assess(
    *,
    turns: int,
    prompt_tokens: int,
    cached_prompt_tokens: int,
    recent: Sequence[tuple[int, int]],
) -> CacheHealth:
    """The verdict on a window: its totals, and `(prompt, cached)` of its newest turns.

    The totals are exact aggregates and `recent` is the newest `RECENT_TURNS` rows, newest
    first; the earlier stretch is the totals less the recent turns, so a month-long window
    is two cheap queries and not a read of every row.
    """
    recent = recent[:RECENT_TURNS]
    recent_prompt = sum(p for p, _ in recent)
    recent_cached = sum(c for _, c in recent)
    baseline_turns = turns - len(recent)

    overall = _rate(prompt_tokens, cached_prompt_tokens)
    recent_rate = (
        _rate(recent_prompt, recent_cached) if len(recent) >= MIN_RECENT_TURNS else None
    )
    baseline_rate = (
        _rate(prompt_tokens - recent_prompt, cached_prompt_tokens - recent_cached)
        if baseline_turns >= MIN_BASELINE_TURNS
        else None
    )

    alert: str | None = None
    if (
        recent_rate is not None
        and baseline_rate is not None
        and baseline_rate >= HEALTHY_RATE
        and recent_rate < baseline_rate * DROP_FACTOR
    ):
        alert = (
            f"The prompt-cache hit rate fell from {_percent(baseline_rate)} over the earlier "
            f"turns to {_percent(recent_rate)} over the last {len(recent)}. Something that "
            "changes from one step to the next has probably been placed ahead of the "
            "transcript (a timestamp, a counter, an unsorted collection): every step is being "
            "billed at the full input price. Compare the prompts of two consecutive steps."
        )

    return CacheHealth(
        turns=turns,
        prompt_tokens=prompt_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        hit_rate=overall,
        recent_turns=len(recent),
        recent_hit_rate=recent_rate,
        reported=cached_prompt_tokens > 0,
        alert=alert,
    )


def read(db: Session, since: datetime) -> CacheHealth:
    """The cache health of every turn recorded at or after `since`.

    Turns with no prompt tokens (one that reached no model) are not turns of this
    question and are left out, as they are from `turn_metrics` itself.
    """
    window = (TurnMetric.created_at >= since, TurnMetric.prompt_tokens > 0)
    count, prompt, cached = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(TurnMetric.prompt_tokens), 0),
            func.coalesce(func.sum(TurnMetric.cached_prompt_tokens), 0),
        ).where(*window)
    ).one()
    recent = db.execute(
        select(TurnMetric.prompt_tokens, TurnMetric.cached_prompt_tokens)
        .where(*window)
        .order_by(TurnMetric.created_at.desc(), TurnMetric.id.desc())
        .limit(RECENT_TURNS)
    ).all()
    return assess(
        turns=int(count),
        prompt_tokens=int(prompt),
        cached_prompt_tokens=int(cached),
        recent=[(int(p), int(c)) for p, c in recent],
    )
