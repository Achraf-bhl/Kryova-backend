"""What an organisation has spent on the model, what it may spend, and the warnings between.

`app/ai/usage.py` bounds one *user*. A team's bill is the sum of its members, so ten
people each under their own daily allowance can still spend ten times what the
organisation's owner agreed to -- and the owner is the one who is invoiced. This
module is the other half of that: a cap on the whole organisation, per UTC day and per
UTC calendar month, in money (`app/ai/pricing.py`), with a mail to the owners at 80 %
and again at 100 %, and the same numbers served live to the in-app banner.

Four rules, each pinned by a test that fails when it is removed:

1. **A cap is read from the tenant's override first, then the global setting, and
   `0` is a real answer.** `BillingAccount.ai_org_*_cost_budget_micro_usd` is NULL for
   "use the global" and 0 for "this tenant is unlimited"; collapsing the two would make
   it impossible to exempt one customer from a global cap.
2. **Only priced calls are summed, and the unpriced ones are counted beside it.** A cost
   cap cannot see a model nobody priced. That is a production boot error
   (`Settings.unpriced_cost_budget`), and where it still happens the status carries
   `unpriced_calls` so a banner reading "$3.10 of $10" never silently means "$3.10 plus
   everything we could not price".
3. **A warning is sent once.** `AIBudgetAlert`'s unique constraint is the lock: two turns
   finishing together both see the organisation cross 80 %, both try to claim the row,
   one wins and sends. Where one turn jumps past both thresholds, only the higher mail
   goes -- two mails seconds apart say less than one.
4. **Nothing here may fail a turn.** The mail is best-effort and `mail.send` does not
   raise; the claim and the send are wrapped so a broken SMTP configuration costs a
   warning and never the answer the user already paid for.

The module imports nothing from `app.ai.usage` (which imports this): keep it that way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mail
from app.ai import pricing
from app.core.config import settings
from app.core.metering import billing_account
from app.models import (
    AIBudgetAlert,
    AITokenUsage,
    Membership,
    Organisation,
    Project,
    User,
)
from app.models.organisation import OrgRole, organisation_ids_for_user

logger = logging.getLogger(__name__)

PERIOD_DAY = "day"
PERIOD_MONTH = "month"

#: Where a cap came from, said in every payload that carries one.
SOURCE_TENANT = "tenant override"
SOURCE_GLOBAL = "global settings"

_ADJECTIVE = {PERIOD_DAY: "daily", PERIOD_MONTH: "monthly"}

#: Percent of a cap at which the owners are told. 80 warns, 100 says it is reached.
THRESHOLDS: tuple[int, ...] = (80, 100)


def billed_organisation(db: Session, user: User, project_id: str | None = None) -> str | None:
    """The tenant a call is billed to, or None when there is none to bill.

    A call on a project is billed to that project's organisation *if the user belongs
    to it*; otherwise to the user's own, taking the first by id so the answer is the
    same on every call. A user in no organisation is billed to nobody, rather than to
    a guess -- a wrong organisation on an invoice is worse than a missing line. The
    usage ledger (`app/core/metering.py`) and the budget read this one function, so
    what an organisation is charged and what it is capped on cannot be two different
    sets of calls.

    It takes a project id and not a conversation because the budget is checked *before*
    a new conversation exists -- creating one just to refuse the turn would leave an
    empty chat in the sidebar.
    """
    tenants = organisation_ids_for_user(db, user)
    if not tenants:
        return None
    if project_id:
        project = db.get(Project, project_id)
        if project is not None and project.organisation_id in tenants:
            return str(project.organisation_id)
    return str(sorted(tenants)[0])


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _month_bounds(today: date) -> tuple[date, date]:
    """`[first of this month, first of next month)` -- half-open, like the bill."""
    start = today.replace(day=1)
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, following


@dataclass(frozen=True, slots=True)
class OrgCap:
    """One period's cap and spend for one organisation."""

    period: str
    #: Micro-dollars. 0 means no cap in force.
    cap_micro_usd: int
    source: str
    spent_micro_usd: int
    period_start: date
    #: First day of the next period: when the spend resets to zero.
    resets_on: date

    @property
    def capped(self) -> bool:
        return self.cap_micro_usd > 0

    @property
    def percent(self) -> int | None:
        """Whole percent of the cap spent (it can exceed 100), None when uncapped."""
        if not self.capped:
            return None
        return self.spent_micro_usd * 100 // self.cap_micro_usd

    @property
    def reached(self) -> bool:
        return self.capped and self.spent_micro_usd >= self.cap_micro_usd

    def crossed(self, threshold: int) -> bool:
        """Whether spend has reached `threshold` percent. Integer arithmetic, no rounding."""
        return self.capped and self.spent_micro_usd * 100 >= self.cap_micro_usd * threshold

    @property
    def resets_text(self) -> str:
        if self.period == PERIOD_DAY:
            return "at 00:00 UTC"
        return f"on {self.resets_on:%d %B} at 00:00 UTC"


@dataclass(frozen=True, slots=True)
class OrgBudgetStatus:
    organisation_id: str
    caps: tuple[OrgCap, ...]
    #: Calls in the current month on a model with no configured price. See rule 2.
    unpriced_calls: int


def _cap_for(db: Session, organisation_id: str) -> tuple[tuple[int, str], tuple[int, str]]:
    """`((daily micro-USD, source), (monthly micro-USD, source))`. Rule 1."""
    account = billing_account(db, organisation_id)
    daily = account.ai_org_daily_cost_budget_micro_usd if account is not None else None
    monthly = account.ai_org_monthly_cost_budget_micro_usd if account is not None else None
    return (
        (
            (pricing.budget_micro(settings.ai_org_daily_cost_budget_usd), SOURCE_GLOBAL)
            if daily is None
            else (max(0, int(daily)), SOURCE_TENANT)
        ),
        (
            (pricing.budget_micro(settings.ai_org_monthly_cost_budget_usd), SOURCE_GLOBAL)
            if monthly is None
            else (max(0, int(monthly)), SOURCE_TENANT)
        ),
    )


def status(db: Session, organisation_id: str, *, today: date | None = None) -> OrgBudgetStatus:
    """Caps and spend for both periods, from one ledger read (none when nothing is capped)."""
    today = today or _today()
    (daily_cap, daily_source), (monthly_cap, monthly_source) = _cap_for(db, organisation_id)
    month_start, next_month = _month_bounds(today)

    spent_day = spent_month = unpriced = 0
    if daily_cap or monthly_cap:
        row = db.execute(
            select(
                func.coalesce(
                    func.sum(AITokenUsage.cost_micro_usd).filter(
                        AITokenUsage.usage_date == today
                    ),
                    0,
                ),
                func.coalesce(func.sum(AITokenUsage.cost_micro_usd), 0),
                func.count().filter(AITokenUsage.cost_micro_usd.is_(None)),
            ).where(
                AITokenUsage.organisation_id == organisation_id,
                AITokenUsage.usage_date >= month_start,
                AITokenUsage.usage_date < next_month,
            )
        ).one()
        spent_day, spent_month, unpriced = int(row[0]), int(row[1]), int(row[2])

    return OrgBudgetStatus(
        organisation_id=organisation_id,
        caps=(
            OrgCap(
                PERIOD_DAY, daily_cap, daily_source, spent_day, today,
                date.fromordinal(today.toordinal() + 1),
            ),
            OrgCap(
                PERIOD_MONTH, monthly_cap, monthly_source, spent_month, month_start, next_month
            ),
        ),
        unpriced_calls=unpriced,
    )


def _dollars(micro: int) -> str:
    value = pricing.usd(micro)
    assert value is not None
    return f"${value:,.2f}"


def refusal(db: Session, organisation_id: str) -> str | None:
    """The 429 detail if the organisation is at a cap, else None.

    When both caps are reached the one that resets *last* is the one reported: a user
    told "resets at 00:00 UTC" when it is in fact the month that is spent would try
    again tomorrow and be refused again.
    """
    current = status(db, organisation_id)
    blocking = [cap for cap in current.caps if cap.reached]
    if not blocking:
        return None
    cap = max(blocking, key=lambda c: c.resets_on)
    return (
        f"Your organisation has reached its {_ADJECTIVE[cap.period]} AI spending cap of "
        f"{_dollars(cap.cap_micro_usd)} ({_dollars(cap.spent_micro_usd)} spent). "
        f"It resets {cap.resets_text}. Simulations, uploads and results are unaffected. "
        "An organisation owner can raise the cap in the billing settings."
    )


@dataclass(frozen=True, slots=True)
class Notice:
    """One line for the in-app banner."""

    period: str
    percent: int
    #: "warning" from 80 %, "exhausted" at 100 %.
    level: str
    message: str


def notices(current: OrgBudgetStatus) -> list[Notice]:
    """The banner's lines: every capped period that has reached 80 %, highest first."""
    out: list[Notice] = []
    for cap in current.caps:
        if not cap.crossed(THRESHOLDS[0]):
            continue
        percent = cap.percent or 0
        reached = cap.reached
        out.append(
            Notice(
                period=cap.period,
                percent=percent,
                level="exhausted" if reached else "warning",
                message=(
                    f"Your organisation has {'reached' if reached else 'used ' + str(percent) + '% of'} "
                    f"its {_ADJECTIVE[cap.period]} AI spending cap "
                    f"({_dollars(cap.spent_micro_usd)} of {_dollars(cap.cap_micro_usd)}). "
                    f"It resets {cap.resets_text}."
                ),
            )
        )
    return sorted(out, key=lambda n: -n.percent)


def _claim(db: Session, organisation_id: str, cap: OrgCap, threshold: int) -> bool:
    """Try to take the right to send this warning. Rule 3: the unique row is the lock."""
    try:
        with db.begin_nested():
            db.add(
                AIBudgetAlert(
                    organisation_id=organisation_id,
                    period=cap.period,
                    period_start=cap.period_start,
                    threshold=threshold,
                )
            )
            db.flush()
    except IntegrityError:
        return False
    return True


def _recipients(db: Session, organisation_id: str) -> list[str]:
    """Owners' addresses; admins when the organisation has no active owner."""
    for role in (OrgRole.OWNER, OrgRole.ADMIN):
        rows = db.scalars(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organisation_id == organisation_id,
                Membership.role == role,
                User.is_active.is_(True),
            )
            .order_by(User.email)
        ).all()
        if rows:
            return list(rows)
    return []


def alert_if_crossed(db: Session, organisation_id: str | None) -> int:
    """Send the 80 % / 100 % warnings an organisation has newly earned. Returns mails handed on.

    "Handed on" is the transport's accept, not a delivery: `mail.send` reports that
    outcome as data and this does not act on it.

    Called after a turn is recorded. Never raises (rule 4).
    """
    if organisation_id is None:
        return 0
    try:
        current = status(db, organisation_id)
        sent = 0
        organisation = db.get(Organisation, organisation_id)
        name = organisation.name if organisation is not None else "Your organisation"
        for cap in current.caps:
            crossed = [t for t in THRESHOLDS if cap.crossed(t)]
            won = [t for t in crossed if _claim(db, organisation_id, cap, t)]
            if not won:
                continue
            for address in _recipients(db, organisation_id):
                mail.send(
                    mail.templates.ai_budget_alert(
                        to=address,
                        organisation=name,
                        period=_ADJECTIVE[cap.period],
                        percent=max(won),
                        spent=_dollars(cap.spent_micro_usd),
                        cap=_dollars(cap.cap_micro_usd),
                        resets=cap.resets_text,
                    )
                )
                sent += 1
        db.commit()
        return sent
    except Exception:  # noqa: BLE001 - a warning must never fail the turn it follows
        logger.exception("Could not send the AI budget alert for organisation %s", organisation_id)
        db.rollback()
        return 0
