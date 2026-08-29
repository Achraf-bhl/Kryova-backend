"""Token accounting: what the model spent, and who spent it.

FEA compute has been metered since the beginning -- `max_elements`,
`max_concurrent_simulations_per_user`, an element-size floor. LLM spend had no
equivalent, which in a chat-first product is the larger of the two bills: a
single agent turn can be a dozen provider round trips, each replaying a
transcript, and nothing stopped one account from running that in a loop.

Every model call in this layer lands in the `ai_token_usage` ledger. The daily
budget reads one index; the conversation view reads a denormalised total on the
conversation row. Both derive from the same rows, so they cannot disagree.

The budget is a *soft* boundary enforced at the start of a turn, not a hard cap
mid-call: a turn that begins under budget is allowed to finish. Cutting an agent
off between a tool call and its result would leave the transcript describing
work whose outcome nobody ever saw, which is worse than a slightly overrun
budget.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.provider import TokenUsage
from app.core.config import settings
from app.models import AITokenUsage, Conversation, User

#: Fallback when `AI_DAILY_TOKEN_BUDGET` is not configured. Generous: a heavy
#: day of interactive CATIA modelling is well inside it, while a runaway loop
#: reaches it in minutes. Zero (or a negative value) means unlimited.
DEFAULT_DAILY_TOKEN_BUDGET = 2_000_000

#: Purposes, so the ledger can answer "what is costing us" rather than only
#: "how much". Strings rather than an enum: this column is a label for humans
#: reading a dashboard, and a new call site should not need a migration.
PURPOSE_CHAT = "chat"
PURPOSE_INTERPRET = "interpret"
PURPOSE_LOAD_CASE = "load_case"
PURPOSE_SUMMARY = "summary"
PURPOSE_TITLE = "title"


def daily_token_budget() -> int:
    """Tokens one user may spend per UTC day. Zero means unlimited."""
    configured = getattr(settings, "ai_daily_token_budget", DEFAULT_DAILY_TOKEN_BUDGET)
    try:
        return max(0, int(configured))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_TOKEN_BUDGET


def tokens_used_today(db: Session, user_id: str) -> int:
    """Total tokens this user has spent on the current UTC day."""
    today = datetime.now(timezone.utc).date()
    return (
        db.scalar(
            select(
                func.coalesce(
                    func.sum(AITokenUsage.prompt_tokens + AITokenUsage.completion_tokens), 0
                )
            ).where(
                AITokenUsage.user_id == user_id,
                AITokenUsage.usage_date == today,
            )
        )
        or 0
    )


def user_totals(db: Session, user_id: str) -> TokenUsage:
    """Lifetime totals for one user, for the conversation read endpoint."""
    row = db.execute(
        select(
            func.coalesce(func.sum(AITokenUsage.prompt_tokens), 0),
            func.coalesce(func.sum(AITokenUsage.completion_tokens), 0),
        ).where(AITokenUsage.user_id == user_id)
    ).one()
    return TokenUsage(prompt_tokens=int(row[0]), completion_tokens=int(row[1]))


def over_budget(db: Session, user_id: str) -> bool:
    budget = daily_token_budget()
    return bool(budget) and tokens_used_today(db, user_id) >= budget


def budget_message(db: Session, user_id: str) -> str:
    """A 429 detail that tells the user what happened and when it clears."""
    return (
        f"You have used your daily AI allowance of {daily_token_budget():,} tokens "
        f"({tokens_used_today(db, user_id):,} spent today). It resets at 00:00 UTC. "
        "Simulations, uploads and results are unaffected."
    )


def record(
    db: Session,
    *,
    user: User,
    usage: TokenUsage,
    purpose: str,
    provider: str,
    model: str,
    conversation: Conversation | None = None,
) -> None:
    """Append one call to the ledger and roll it into the conversation total.

    A zero-token call is still written. A provider that reports nothing is a
    fact worth being able to see in the ledger -- otherwise "we spent nothing"
    and "we do not know what we spent" look identical.
    """
    db.add(
        AITokenUsage(
            user_id=user.id,
            conversation_id=conversation.id if conversation is not None else None,
            usage_date=datetime.now(timezone.utc).date(),
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )
    )
    if conversation is not None:
        conversation.prompt_tokens += usage.prompt_tokens
        conversation.completion_tokens += usage.completion_tokens
    db.flush()
