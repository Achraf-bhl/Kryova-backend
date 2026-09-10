"""Read-only mode, and the banner both clients render (P3.7).

**A maintenance mode that returns 500 is not a maintenance mode**, it is an
outage with a nicer name in the runbook. The whole value is that a user who
tries to start a simulation during a database migration is told *what is
happening and when it ends*, and that reads and navigation keep working so they
are not staring at a broken application while they wait.

So this refuses with `503` and a sentence, on mutating methods only. Reads pass
through untouched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Announcement, MaintenanceWindow

#: Methods that change nothing and are therefore always allowed. Matches
#: `core/audit.SAFE_METHODS`; both describe the same idea and neither should
#: grow a member the other lacks.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: How long a "no window" answer is trusted before asking the database again.
#:
#: This is the whole reason the cache below exists: `maintenance_windows` is
#: empty in essentially every deployment at essentially every moment, and
#: reading it on every authenticated mutating request adds a SELECT to every
#: write in the product to learn nothing. Ten seconds means declaring
#: maintenance takes effect within ten seconds across a worker pool — an
#: operator who has just typed a reason and a message is not counting
#: milliseconds — while the steady-state cost falls to one read per worker per
#: ten seconds instead of one per request.
CACHE_SECONDS = 10.0


class Window(Protocol):
    """What the refusal path needs of a window, whether ORM row or snapshot.

    Declared as properties rather than attributes so a frozen dataclass
    satisfies it: a bare `message: str` in a Protocol means a *settable*
    attribute, which `WindowSnapshot` deliberately is not.
    """

    @property
    def message(self) -> str: ...

    @property
    def expected_end_at(self) -> datetime | None: ...

    @property
    def allow_staff(self) -> bool: ...

    def is_active(self, now: datetime | None = None) -> bool: ...


@dataclass(frozen=True)
class WindowSnapshot:
    """A window, detached from the session that loaded it.

    Caching the `MaintenanceWindow` itself would hold an ORM object past the
    close of its `Session`, and the next request to read `window.message` would
    get `DetachedInstanceError` — during maintenance, on the path whose entire
    job is to explain the maintenance. A frozen copy of the five fields the
    refusal actually uses cannot fail that way.

    It keeps `started_at`/`ended_at` rather than a precomputed "active" flag so
    that a window which lapses inside the cache window is honoured the moment it
    lapses. Ending maintenance early enough to matter is `invalidate()`'s job;
    ending on schedule needs no round trip at all.
    """

    message: str
    started_at: datetime
    ended_at: datetime | None
    expected_end_at: datetime | None
    allow_staff: bool

    @classmethod
    def of(cls, window: MaintenanceWindow) -> "WindowSnapshot":
        return cls(
            message=window.message,
            started_at=window.started_at,
            ended_at=window.ended_at,
            expected_end_at=window.expected_end_at,
            allow_staff=window.allow_staff,
        )

    def is_active(self, now: datetime | None = None) -> bool:
        moment = now or datetime.now(timezone.utc)
        return self.ended_at is None or moment < self.ended_at


#: `(read_at, snapshot)`, where `None` is a cached *absence* — the common case,
#: and the one worth caching most.
_cache: tuple[float, WindowSnapshot | None] | None = None


def invalidate() -> None:
    """Forget the cached answer.

    Called by the admin routes that start and end a window, so the operator's
    own next request sees their own change rather than waiting out
    `CACHE_SECONDS`. **This is per-process**: other workers pick the change up
    on their own expiry, which is what bounds `CACHE_SECONDS`.
    """
    global _cache
    _cache = None


def current_window(
    db: Session,
    *,
    now: datetime | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> WindowSnapshot | None:
    """The window in force, from cache when the cache is fresh.

    `monotonic` rather than wall time because the TTL is a duration, and a clock
    step — an NTP correction, a suspended laptop — must not be able to freeze a
    cached answer in place or expire it early.
    """
    global _cache
    moment = now or datetime.now(timezone.utc)

    cached = _cache
    if cached is not None and monotonic() - cached[0] < CACHE_SECONDS:
        snapshot = cached[1]
        if snapshot is None or snapshot.is_active(moment):
            return snapshot
        # It lapsed while cached. Fall through and re-read: there may be an
        # older window still standing behind it.

    window = active_window(db, now=moment)
    snapshot = WindowSnapshot.of(window) if window is not None else None
    _cache = (monotonic(), snapshot)
    return snapshot


def active_window(db: Session, *, now: datetime | None = None) -> MaintenanceWindow | None:
    """The maintenance window in force, if any.

    Ordered newest first and takes the first live one. Two overlapping windows
    is an operator mistake rather than a state to model — the newest is the one
    somebody just started, which is the one they mean.
    """
    moment = now or datetime.now(timezone.utc)
    for window in db.scalars(
        select(MaintenanceWindow).order_by(MaintenanceWindow.started_at.desc()).limit(5)
    ):
        if window.is_active(moment):
            return window
    return None


def refuses(window: Window | None, *, method: str, is_staff: bool) -> bool:
    """Whether this request should be refused.

    Staff are let through when the window says so, and it says so by default:
    the people who need to fix whatever caused the maintenance are the ones
    holding staff grants, and a read-only mode that locks them out is one
    somebody works around by turning it off.
    """
    if window is None:
        return False
    if method.upper() in SAFE_METHODS:
        return False
    if is_staff and window.allow_staff:
        return False
    return True


def refusal_message(window: Window) -> str:
    """What the user is told. Never `window.reason`, which is the operator's own
    note — "migrating the primary database" is not something a customer can act
    on, and it names infrastructure to whoever is asking."""
    message = window.message.strip() or "Kryova is briefly read-only for maintenance."
    if window.expected_end_at is not None:
        return f"{message} Expected back at {window.expected_end_at:%H:%M UTC on %d %B}."
    return message


def live_announcements(db: Session, *, now: datetime | None = None) -> list[Announcement]:
    """Banners in force, newest first."""
    moment = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(Announcement).order_by(Announcement.starts_at.desc()).limit(20)
    ).all()
    return [row for row in rows if row.is_live(moment)]


__all__ = [
    "CACHE_SECONDS",
    "SAFE_METHODS",
    "Window",
    "WindowSnapshot",
    "active_window",
    "current_window",
    "invalidate",
    "live_announcements",
    "refusal_message",
    "refuses",
]
