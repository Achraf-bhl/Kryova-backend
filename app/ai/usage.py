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

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.ai import org_budget, pricing
from app.ai.provider import TokenUsage
from app.core.config import settings
from app.core.metering import quota_envelope
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
#: Describing a PNG or JPEG a user attached (P4.2). One call per picture.
PURPOSE_ATTACHMENT_IMAGE = "attachment_image"


def daily_token_budget() -> int:
    """Tokens one user may spend per UTC day. Zero means unlimited."""
    configured = getattr(settings, "ai_daily_token_budget", DEFAULT_DAILY_TOKEN_BUDGET)
    try:
        return max(0, int(configured))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_TOKEN_BUDGET


def effective_daily_token_budget(db: Session, organisation_id: str | None) -> int:
    """Tokens one user of this tenant may spend per UTC day, honouring the tenant's override.

    `BillingAccount.ai_daily_token_budget` and a plan's allowance have been settable
    since P8 and the quota page has reported them, but nothing on the chat path ever
    read them: `daily_token_budget()` is the *global* setting, so an owner who set a
    tighter (or looser) limit for their organisation was shown it as "in force" while
    the product kept enforcing the global number. Resolved through the same envelope
    the quota page reads, so the page and the enforcement cannot disagree again. A
    user with no organisation has no tenant to override anything, and gets the global.
    """
    if organisation_id is None:
        return daily_token_budget()
    limit = quota_envelope(db, organisation_id).limit_for("ai_daily_token_budget")
    if limit is None:
        return daily_token_budget()
    return max(0, int(limit.limit))


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
            func.coalesce(func.sum(AITokenUsage.cached_prompt_tokens), 0),
        ).where(AITokenUsage.user_id == user_id)
    ).one()
    return TokenUsage(
        prompt_tokens=int(row[0]),
        completion_tokens=int(row[1]),
        cached_prompt_tokens=int(row[2]),
    )


def daily_cost_budget_micro() -> int:
    """Micro-dollars one user may spend per UTC day. Zero means unlimited."""
    return pricing.budget_micro(settings.ai_daily_cost_budget_usd)


@dataclass(frozen=True, slots=True)
class DayUsage:
    """What one user has spent today, split so a budget can say *which* it hit.

    `cost_micro_usd` sums **only priced calls**; `unpriced_calls` counts the rest.
    Reported together on purpose: a total that silently left out a model nobody
    priced would read as "you have spent $0.40" when the honest sentence is
    "$0.40 on calls we could price, plus 12 we could not".
    """

    prompt_tokens: int
    completion_tokens: int
    cached_prompt_tokens: int
    cost_micro_usd: int
    unpriced_calls: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _ledger_usage(db: Session, *conditions: ColumnElement[bool]) -> DayUsage:
    """Sum the ledger rows matching `conditions`, priced and unpriced kept apart."""
    row = db.execute(
        select(
            func.coalesce(func.sum(AITokenUsage.prompt_tokens), 0),
            func.coalesce(func.sum(AITokenUsage.completion_tokens), 0),
            func.coalesce(func.sum(AITokenUsage.cached_prompt_tokens), 0),
            func.coalesce(func.sum(AITokenUsage.cost_micro_usd), 0),
            # Counted in SQL rather than by loading rows: this runs on every chat
            # turn and the ledger is append-only and large.
            func.count().filter(AITokenUsage.cost_micro_usd.is_(None)),
        ).where(*conditions)
    ).one()
    return DayUsage(
        prompt_tokens=int(row[0]),
        completion_tokens=int(row[1]),
        cached_prompt_tokens=int(row[2]),
        cost_micro_usd=int(row[3]),
        unpriced_calls=int(row[4]),
    )


def usage_today(db: Session, user_id: str) -> DayUsage:
    """Everything this user has spent on the current UTC day, in one index lookup."""
    today = datetime.now(timezone.utc).date()
    return _ledger_usage(db, AITokenUsage.user_id == user_id, AITokenUsage.usage_date == today)


def conversation_usage(db: Session, conversation_id: str) -> DayUsage:
    """Everything one conversation has cost, from the ledger -- titles and summaries included.

    The ledger rather than `Conversation.prompt_tokens`: those two columns are a running
    token total with no money in them, and a price is a fact about the model *at the time of
    the call* (`record` prices it then), so a sum over rows is the only honest figure.
    """
    return _ledger_usage(db, AITokenUsage.conversation_id == conversation_id)


#: The point at which the composer starts saying how much of the day is left.
WARN_AT_PERCENT = 80


@dataclass(frozen=True, slots=True)
class Allowance:
    """How much of today's allowance a user has used, as one answer.

    `percent` is the larger of the token and cost percentages -- the ceiling that will be hit
    first -- and `None` when neither ceiling exists. `level` is `unlimited`, `ok`, `warning`
    (from `WARN_AT_PERCENT`) or `exhausted` (at 100), and `basis` names which ceiling it is
    about, so "80% of your tokens" never reads as "80% of your money".
    """

    daily_token_budget: int
    daily_cost_budget_micro_usd: int
    percent: int | None
    level: str
    basis: str | None


def allowance(used: DayUsage, *, token_budget: int, cost_budget_micro: int) -> Allowance:
    shares: list[tuple[int, str]] = []
    if token_budget > 0:
        shares.append((used.total_tokens * 100 // token_budget, "tokens"))
    if cost_budget_micro > 0:
        shares.append((used.cost_micro_usd * 100 // cost_budget_micro, "cost"))
    if not shares:
        return Allowance(token_budget, cost_budget_micro, None, "unlimited", None)
    percent, basis = max(shares)
    level = "exhausted" if percent >= 100 else "warning" if percent >= WARN_AT_PERCENT else "ok"
    return Allowance(token_budget, cost_budget_micro, percent, level, basis)


def over_budget(db: Session, user_id: str) -> bool:
    """Whether this user has reached either daily ceiling -- tokens or dollars."""
    return exceeded(db, user_id) is not None


def exceeded(db: Session, user_id: str, *, token_budget: int | None = None) -> str | None:
    """Which daily ceiling is reached (`"tokens"` or `"cost"`), or None.

    One ledger read serves both checks. Tokens are checked first: it is the
    ceiling that exists on every deployment, while the cost one needs a price.
    `token_budget` is the tenant-resolved figure (`effective_daily_token_budget`);
    left out, it is the global setting.
    """
    if token_budget is None:
        token_budget = daily_token_budget()
    cost_budget = daily_cost_budget_micro()
    if not token_budget and not cost_budget:
        return None
    used = usage_today(db, user_id)
    if token_budget and used.total_tokens >= token_budget:
        return "tokens"
    if cost_budget and used.cost_micro_usd >= cost_budget:
        return "cost"
    return None


def budget_message(db: Session, user_id: str, *, token_budget: int | None = None) -> str:
    """A 429 detail that tells the user what happened and when it clears."""
    if token_budget is None:
        token_budget = daily_token_budget()
    used = usage_today(db, user_id)
    which = exceeded(db, user_id, token_budget=token_budget) or "tokens"
    if which == "cost":
        spent = pricing.usd(used.cost_micro_usd)
        allowance = pricing.usd(daily_cost_budget_micro())
        return (
            f"You have used your daily AI allowance of ${allowance:,.2f} "
            f"(${spent:,.2f} spent today"
            + (
                f", plus {used.unpriced_calls} call(s) on a model with no configured price"
                if used.unpriced_calls
                else ""
            )
            + "). It resets at 00:00 UTC. Simulations, uploads and results are unaffected."
        )
    return (
        f"You have used your daily AI allowance of {token_budget:,} tokens "
        f"({used.total_tokens:,} spent today). It resets at 00:00 UTC. "
        "Simulations, uploads and results are unaffected."
    )


def refusal(db: Session, user: User, project_id: str | None = None) -> str | None:
    """Why this user may not start a turn right now, or None if they may.

    Two ceilings, checked in this order: the user's own (tokens, resolved through
    their tenant's override, then dollars) and then the organisation's (dollars,
    per day and per month, summed over every member). The user's own comes first
    because its message is the one that is theirs to act on; an organisation cap is
    the owner's to raise.
    """
    organisation_id = org_budget.billed_organisation(db, user, project_id)
    token_budget = effective_daily_token_budget(db, organisation_id)
    if exceeded(db, user.id, token_budget=token_budget) is not None:
        return budget_message(db, user.id, token_budget=token_budget)
    if organisation_id is not None:
        return org_budget.refusal(db, organisation_id)
    return None


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
            organisation_id=org_budget.billed_organisation(
                db, user, conversation.project_id if conversation is not None else None
            ),
            usage_date=datetime.now(timezone.utc).date(),
            purpose=purpose,
            provider=provider,
            model=model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_prompt_tokens=usage.cached_prompt_tokens,
            # Priced now, from the configuration in force now. None -- not 0 --
            # when the model has no price: see `app/ai/pricing.py`.
            cost_micro_usd=pricing.cost_micro_usd(usage, model),
        )
    )
    if conversation is not None:
        conversation.prompt_tokens += usage.prompt_tokens
        conversation.completion_tokens += usage.completion_tokens
    db.flush()
