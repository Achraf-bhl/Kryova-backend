"""What one agent turn cost, measured -- and kept.

Before this module the numbers that decide whether the agent is affordable existed
only as log lines: `agent step N/M: ... 41,200 prompt tokens` in the loop and
`prompt cache: 9,000 of 10,000 prompt tokens were hits` in the provider. A log line
cannot be summed, compared between two settings or shown to an administrator, and
every optimisation in ROAD_TO_10 Phase 1 (tool retrieval, replay digests, a
token-sized window) is a claim about exactly these numbers. So one row per turn
(`TurnMetric`) holds them, and `TurnMeter` is how the loop fills it in.

**The meter is owned by the caller, not by the loop.** `stream_agent` used to keep
its token total in a local, which meant a turn that died on step 9 -- the model
refused, the network dropped, the client hung up -- took steps 1 to 8 with it:
real spend, never billed, in a route whose comment promised it was. Handing the
loop a meter the route already holds means whatever was spent by the time the
generator is torn down is still there to read, however it was torn down.

`TurnMeter` imports no database and no provider; `record_turn` is the one function
that writes. Both are tested without a model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai import pricing
from app.ai.provider import TokenUsage
from app.models import Conversation, TurnMetric, User

#: Stop reasons the *loop* sets, in one place so a dashboard grouping by reason and
#: the loop that writes it cannot drift. The first six are the ones a client already
#: reads off the `done` event; the last two are decided by the route, because the
#: loop is not there to say them (an exception, or a client that hung up).
STOP_FINISHED = "finished"
STOP_CANCELLED = "cancelled"
STOP_STEP_BUDGET = "step_budget"
STOP_REPEATED_CALLS = "repeated_calls"
STOP_NEEDS_INPUT = "needs_input"
STOP_AWAITING_APPROVAL = "awaiting_approval"
STOP_ERROR = "error"
STOP_DISCONNECTED = "disconnected"


@dataclass
class TurnMeter:
    """A running tally of one turn, mutated by the loop and read by whoever owns it."""

    usage: TokenUsage = field(default_factory=TokenUsage)
    #: Provider calls that were answered and billed -- a step, a summary fold, a
    #: routing decision, the closing answer. Not tool calls.
    model_calls: int = 0
    #: Loop iterations, which is what `AI_MAX_STEPS` bounds.
    rounds: int = 0
    #: How many tool schemas the model was offered. Zero until the loop has chosen.
    tools_offered: int = 0
    #: The step budget this turn ran under, so "used 41 of 60" is answerable later.
    step_budget: int = 0
    tool_calls: int = 0
    #: Calls that ran and failed.
    tool_calls_failed: int = 0
    #: Calls the loop turned back for repeating a read or a refused write. They never
    #: ran, so they are not failures, and counting them as such would blame a tool
    #: for the model's loop.
    tool_calls_blocked: int = 0
    #: The largest single request this turn sent, in prompt tokens: what the context
    #: window actually had to hold at its worst.
    peak_prompt_tokens: int = 0
    stop_reason: str | None = None
    started: float = field(default_factory=time.monotonic)

    def charge(self, spent: TokenUsage, *, calls: int = 1) -> None:
        """Add one provider call's usage. `calls=0` for usage with no call behind it."""
        self.usage += spent
        self.model_calls += calls
        self.peak_prompt_tokens = max(self.peak_prompt_tokens, spent.prompt_tokens)

    @property
    def wall_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def cost_micro_usd(self, model: str) -> int | None:
        """What the turn cost on `model`; None when that model has no price."""
        return pricing.cost_micro_usd(self.usage, model)


def record_turn(
    db: Session,
    *,
    user: User,
    conversation: Conversation | None,
    meter: TurnMeter,
    provider: str,
    model: str,
    default_stop_reason: str = STOP_ERROR,
) -> TurnMetric | None:
    """Persist the turn, or return None when nothing happened worth a row.

    A turn that made no model call and spent no token (a budget refusal, a request
    that failed before the provider was reached) is not a turn to measure, and a
    row of zeros would drag every median toward them.

    `default_stop_reason` is what the *route* says when the loop did not: an
    exception, or a client that hung up. The loop's own reason always wins.
    """
    if not meter.model_calls and not meter.usage.total_tokens:
        return None
    row = TurnMetric(
        user_id=user.id,
        conversation_id=conversation.id if conversation is not None else None,
        provider=provider,
        model=model,
        rounds=meter.rounds,
        model_calls=meter.model_calls,
        step_budget=meter.step_budget,
        tools_offered=meter.tools_offered,
        tool_calls=meter.tool_calls,
        tool_calls_failed=meter.tool_calls_failed,
        tool_calls_blocked=meter.tool_calls_blocked,
        prompt_tokens=meter.usage.prompt_tokens,
        cached_prompt_tokens=meter.usage.cached_prompt_tokens,
        completion_tokens=meter.usage.completion_tokens,
        peak_prompt_tokens=meter.peak_prompt_tokens,
        cost_micro_usd=meter.cost_micro_usd(model),
        wall_ms=meter.wall_ms,
        stop_reason=meter.stop_reason or default_stop_reason,
    )
    db.add(row)
    db.flush()
    return row
