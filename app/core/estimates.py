"""What a run will cost, from the meter that bills it (P8.3, P8.4).

**"The same meter that bills is the meter shown in cost estimates. One number,
two uses; divergence is a bug class of its own."** That is P8.4, and this module
is where it is made true rather than intended: an estimate is a *projection of
the same `Meter` values the ledger holds*, built from this tenant's own recorded
history, and it is refused when there is no history rather than filled in with a
figure somebody made up.

That refusal is the whole design. A cost estimate is the most tempting place in
a product to invent a number — it is advisory, it is shown before anything
happens, and nobody checks it afterwards. Which is exactly why an invented one
survives: it is never contradicted. `Estimate.basis` is `MEASURED` only when it
came from real rows, and `unavailable` with a reason otherwise, in the same
vocabulary `app/kernel/provenance.py` and `app/verify/provenance.py` use.

**An estimate is never a limit.** `check_quota` decides what may run;
this only says what it is likely to cost. Wiring an estimate into an enforcement
decision would make a projection binding, which is a different and much worse
product.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kernel.provenance import Basis
from app.models import Meter, UsageRecord

#: How far back an estimate looks for comparable runs. Ninety days is long
#: enough that an occasional user has history and short enough that it reflects
#: the current mesher and solver rather than last year's.
LOOKBACK_DAYS: Final = 90

#: Below this many comparable runs, no estimate is offered. Three is not a
#: statistical threshold — it is the point below which a median is one number
#: wearing a plural, and offering it would be the invention this module exists
#: to refuse.
MINIMUM_SAMPLES: Final = 3


@dataclass(frozen=True)
class Estimate:
    """What one meter is likely to cost, and whether we actually know.

    `units` is in the meter's *scaled* integer units, exactly as the ledger
    stores them — the same representation, so an estimate and a bill can be
    compared without a conversion in between. That identity is P8.4.
    """

    meter: Meter
    units: int | None
    basis: Basis
    how: str
    samples: int = 0

    @property
    def known(self) -> bool:
        return self.units is not None

    def human(self) -> str:
        """One sentence, in the register P8.3 asks for."""
        if self.units is None:
            return f"We cannot estimate {self.meter.what} yet: {self.how}"
        whole = Decimal(self.units) / Decimal(self.meter.scale)
        return f"About {whole:g} {self.meter.unit}, {self.how}"


def estimate_meter(
    db: Session,
    organisation_id: str,
    meter: Meter,
    *,
    today: date,
    lookback_days: int = LOOKBACK_DAYS,
) -> Estimate:
    """The median cost of one comparable run for this tenant.

    **The median, not the mean.** One 40-minute convergence study in a history
    of two-second solves drags a mean past every individual run in it, and an
    estimate that is higher than anything that has ever happened is one people
    learn to ignore.

    **This tenant's own history, not the fleet's.** A team meshing engine blocks
    and a team meshing brackets have nothing to say about each other's costs,
    and an estimate built from somebody else's parts is a number about a
    different product.
    """
    since = today - timedelta(days=lookback_days)
    rows = list(
        db.scalars(
            select(UsageRecord.quantity_units).where(
                UsageRecord.organisation_id == organisation_id,
                UsageRecord.meter == meter,
                UsageRecord.usage_date >= since,
            )
        ).all()
    )
    if len(rows) < MINIMUM_SAMPLES:
        return Estimate(
            meter=meter,
            units=None,
            basis=Basis.UNAVAILABLE,
            how=(
                f"this organisation has {len(rows)} recorded "
                f"{'run' if len(rows) == 1 else 'runs'} in the last {lookback_days} days "
                f"and an estimate needs at least {MINIMUM_SAMPLES}"
            ),
            samples=len(rows),
        )
    ordered = sorted(int(value) for value in rows)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) // 2
    )
    return Estimate(
        meter=meter,
        units=median,
        basis=Basis.MEASURED,
        how=(
            f"the median of {len(ordered)} recorded runs in the last "
            f"{lookback_days} days"
        ),
        samples=len(ordered),
    )


def estimate_run(
    db: Session, organisation_id: str, *, today: date
) -> tuple[Estimate, ...]:
    """What a simulation is likely to cost, per meter.

    Only the meters a solve moves. Storage is a stock rather than a flow and AI
    tokens belong to a conversation, so including either would answer a question
    nobody asked and make the total look larger than the action.
    """
    return tuple(
        estimate_meter(db, organisation_id, meter, today=today)
        for meter in (Meter.SOLVER_SECONDS, Meter.MESH_ELEMENT_SECONDS)
    )


def total_recorded(db: Session, organisation_id: str, meter: Meter, *, since: date) -> int:
    """What this tenant has actually spent, for comparing an estimate against.

    The other half of P8.4: an estimate is only trustworthy if somebody can put
    it next to the outcome, so the outcome has to be as easy to read.
    """
    return int(
        db.scalar(
            select(func.coalesce(func.sum(UsageRecord.quantity_units), 0)).where(
                UsageRecord.organisation_id == organisation_id,
                UsageRecord.meter == meter,
                UsageRecord.usage_date >= since,
            )
        )
        or 0
    )


__all__ = [
    "LOOKBACK_DAYS",
    "MINIMUM_SAMPLES",
    "Estimate",
    "estimate_meter",
    "estimate_run",
    "total_recorded",
]
