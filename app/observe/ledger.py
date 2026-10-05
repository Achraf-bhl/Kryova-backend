"""What each timed site has been taking, over its last N occurrences (ROAD_TO_10 9.6).

`collect()` is an opt-in trace of one operation and `app.core.metering` bills; neither answers
"is meshing slower this afternoon than this morning". The ledger does, and it is the only thing
here that is *always on in a running server*: a listener that keeps the last `WINDOW` durations of
every span name in a bounded deque, plus how many it has ever seen and how many failed.

Three honesty rules, each pinned by a test that fails when it is removed:

1. **It is one process's view, and says so.** A server with several workers has several ledgers
   and this reads only the one that answered. `LedgerSnapshot.scope` is the sentence that tells a
   reader; nothing here pretends to be fleet-wide, because a fleet-wide p95 averaged from per-
   worker p95s is not a p95.
2. **A percentile of a small sample is the maximum and is labelled so**
   (`Distribution.p95_is_the_maximum`, reused from `app.observe.records` rather than
   re-derived): nineteen samples put "p95" on the slowest one, and a console quoting that as a
   tail would be quoting a worst case under a name that suggests a distribution.
3. **The window is the newest N, not a time window**, because a deque cannot be cut by age
   without a clock per entry; `window_size` and `seen` are both reported, so "p95 over the last
   512 of 80,000" is visible and a quiet site's old samples are not mistaken for recent ones.

The listener runs on the instrumented thread inside the span's exit, so it does one append under a
lock and nothing else. It never raises: unlike metering it has no writes to defer or lose, and a
dashboard must not be able to break the work it watches.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from app.observe.collect import Span, add_listener, remove_listener
from app.observe.records import Distribution

#: Newest durations kept per span name.
WINDOW = 512

SCOPE = (
    "This worker process only, since it started, newest "
    f"{WINDOW} occurrences per site. A server with several workers has several of these."
)


@dataclass(frozen=True, slots=True)
class SiteLatency:
    name: str
    #: Occurrences this process has ever seen, which is more than `window_size` once it wraps.
    seen: int
    failures: int
    window_size: int
    median_seconds: float
    p95_seconds: float
    max_seconds: float
    #: True when `window_size` is too small for p95 to be anything but the maximum.
    p95_is_the_maximum: bool


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    scope: str
    sites: tuple[SiteLatency, ...]


class SpanLedger:
    def __init__(self, window: int = WINDOW) -> None:
        self._window = window
        self._lock = threading.Lock()
        self._durations: dict[str, deque[float]] = {}
        self._seen: dict[str, int] = {}
        self._failures: dict[str, int] = {}

    def __call__(self, finished: Span) -> None:
        try:
            with self._lock:
                bucket = self._durations.get(finished.name)
                if bucket is None:
                    bucket = self._durations[finished.name] = deque(maxlen=self._window)
                bucket.append(finished.seconds)
                self._seen[finished.name] = self._seen.get(finished.name, 0) + 1
                if not finished.ok:
                    self._failures[finished.name] = self._failures.get(finished.name, 0) + 1
        except Exception:  # noqa: BLE001 - a dashboard may not break the work it watches
            return

    def snapshot(self) -> LedgerSnapshot:
        with self._lock:
            copies = {name: list(bucket) for name, bucket in self._durations.items()}
            seen = dict(self._seen)
            failures = dict(self._failures)
        rows: list[SiteLatency] = []
        for name in sorted(copies):
            samples = copies[name]
            if not samples:
                continue
            spread = Distribution.of(samples)
            rows.append(
                SiteLatency(
                    name=name,
                    seen=seen.get(name, len(samples)),
                    failures=failures.get(name, 0),
                    window_size=spread.count,
                    median_seconds=spread.median_seconds,
                    p95_seconds=spread.p95_seconds,
                    max_seconds=spread.max_seconds,
                    p95_is_the_maximum=spread.p95_is_the_maximum,
                )
            )
        return LedgerSnapshot(scope=SCOPE, sites=tuple(rows))

    def reset(self) -> None:
        with self._lock:
            self._durations.clear()
            self._seen.clear()
            self._failures.clear()


#: The server's ledger. Registered by `install()` from the app's lifespan and removed at
#: shutdown, so importing this module (or running the suite) leaves `span()` inert.
LEDGER = SpanLedger()
_installed = False
_install_lock = threading.Lock()


def install() -> None:
    global _installed
    with _install_lock:
        if not _installed:
            add_listener(LEDGER)
            _installed = True


def uninstall() -> None:
    global _installed
    with _install_lock:
        if _installed:
            remove_listener(LEDGER)
            _installed = False


def is_installed() -> bool:
    return _installed
