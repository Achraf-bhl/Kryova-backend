"""The public status page and its incident history (P10.4).

**A status page that only its operator can read is a private dashboard.** The
whole value is that somebody whose run is stuck can find out whether it is them
or us without opening a ticket — which means no account, and which means this is
the second public surface in the service after `trust`. So it is paid for the
same way: by deciding, field by field, what an outsider may know.

## What is published, and what is deliberately not

Published: whether the service is operating, degraded or in maintenance; the
maintenance message; live announcements; and a history of past windows with
their durations.

**Not published: the fleet's numbers.** `GET /admin/health` carries queue depth,
job counts, storage bytes, live sessions and user totals, and every one of those
is commercially and operationally sensitive — "live_sessions: 3" on a public URL
tells a competitor the size of the business and tells an attacker when nobody is
watching. The status page answers "is it working", which needs none of them.

**Not published: failure detail.** `FleetHealthRead.failures` groups on the
runner's own recorded message, and an `ERRORED` outcome's message is an
exception's text — the same field that put `C:\\Users\\<a customer>` on a
Windows seat, which is why `trust` scrubs it. There is no scrubbing here because
there is nothing to scrub: no free text from a run reaches this module.

## Where the state comes from

`MaintenanceWindow` and `Announcement`, both of which already exist (P3.7) and
both of which an operator already drives from the console. This publishes their
public face rather than introducing a second place to declare an incident — an
incident declared twice is an incident described two ways, and the version
customers read would be the one nobody updates.

**Degraded is not inferred.** There is no rule here that watches a failure rate
and decides the service is unhealthy, because a threshold nobody agreed to would
put this product on a public outage page for a quiet hour with two bad runs. A
window is declared by a person, which is also who has to write the sentence
explaining it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import maintenance
from app.models import Announcement, MaintenanceWindow

#: How far back the published history goes. Ninety days is long enough to show a
#: pattern and short enough that the page stays readable; anything older is a
#: question for a person rather than a page.
HISTORY_DAYS = 90


class ServiceState(StrEnum):
    """Three, and no `unknown`.

    A status page that can say "unknown" says it during exactly the incident it
    exists for. If this module cannot reach the database it must still answer,
    and it answers `OPERATIONAL` with the caveat that it is reporting what it
    can see — see `read_status`.
    """

    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class Incident:
    """One past maintenance window, as an outsider reads it."""

    started_at: datetime
    ended_at: datetime | None
    #: The *customer-facing* message, never `MaintenanceWindow.reason`. The
    #: reason is the operator's own note — "migrating the primary database" is
    #: not something a customer can act on and it names infrastructure to
    #: whoever is asking.
    message: str

    @property
    def minutes(self) -> int | None:
        if self.ended_at is None:
            return None
        return max(0, int((self.ended_at - self.started_at).total_seconds() // 60))

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "minutes": self.minutes,
            "message": self.message,
            # An open incident is said in words rather than left for a reader to
            # work out from a null end time.
            "ongoing": self.ended_at is None,
        }


@dataclass(frozen=True)
class Status:
    state: ServiceState
    summary: str
    notice: str | None
    announcements: tuple[tuple[str, str], ...]
    history: tuple[Incident, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "summary": self.summary,
            "notice": self.notice,
            "announcements": [
                {"level": level, "message": message} for level, message in self.announcements
            ],
            "history": [incident.to_dict() for incident in self.history],
            "history_days": HISTORY_DAYS,
        }


_SUMMARY = {
    ServiceState.OPERATIONAL: "Kryova is operating normally.",
    ServiceState.MAINTENANCE: "Kryova is read-only for maintenance. Reads and navigation work.",
    ServiceState.DEGRADED: "Kryova is degraded. Some work may fail or be slow.",
}


def read_status(db: Session, *, now: datetime | None = None) -> Status:
    """The public status, from the maintenance and announcement records."""
    moment = now or datetime.now(timezone.utc)
    window = maintenance.current_window(db, now=moment)

    if window is not None:
        state = ServiceState.MAINTENANCE
        notice: str | None = maintenance.refusal_message(window)
    else:
        state = ServiceState.OPERATIONAL
        notice = None

    live = maintenance.live_announcements(db, now=moment)
    # A critical announcement with no maintenance window is how an operator says
    # "something is wrong and we have not taken the service read-only". Without
    # this the page would read "operating normally" above a banner saying solves
    # are failing, which is the specific contradiction a status page exists to
    # prevent.
    if state is ServiceState.OPERATIONAL and any(
        announcement.level.value == "critical" for announcement in live
    ):
        state = ServiceState.DEGRADED

    return Status(
        state=state,
        summary=_SUMMARY[state],
        notice=notice,
        announcements=tuple(
            (announcement.level.value, announcement.message) for announcement in live
        ),
        history=incident_history(db, now=moment),
    )


def incident_history(db: Session, *, now: datetime | None = None) -> tuple[Incident, ...]:
    """Past maintenance windows, newest first, within `HISTORY_DAYS`."""
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(days=HISTORY_DAYS)
    rows = db.scalars(
        select(MaintenanceWindow)
        .where(MaintenanceWindow.started_at >= since)
        .order_by(MaintenanceWindow.started_at.desc())
        .limit(50)
    ).all()
    return tuple(
        Incident(
            started_at=row.started_at,
            ended_at=row.ended_at,
            message=row.message.strip() or "Maintenance.",
        )
        for row in rows
    )


def live_announcement_rows(db: Session, *, now: datetime | None = None) -> list[Announcement]:
    """Re-exported so the route needs one import. See `maintenance`."""
    return maintenance.live_announcements(db, now=now)


__all__ = [
    "HISTORY_DAYS",
    "Incident",
    "ServiceState",
    "Status",
    "incident_history",
    "live_announcement_rows",
    "read_status",
]
