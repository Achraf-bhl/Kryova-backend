"""A queue that can be watched: depth, wait, run time, failures.

`app.jobs.JobQueue` has one method and stays that way — moving to Celery must
not touch routes, and it must not touch this either. So the meter is not a queue
implementation and not a decorator around one: it is three calls a queue makes
(`submit` → `started` → `finished`) and a snapshot anyone can read.

**The counters are always live; the spans are not.** This is the one place the
package deliberately does not wait to be switched on. "Is the queue backed up"
is a question asked *after* it is backed up, and a gauge that only starts
counting when an operator remembers to enable it answers by starting from zero
at the worst possible moment. The cost is a lock and three dict operations per
*job* — not per operation, per job, next to work measured in seconds — which is
not a cost. Span records, which are per-operation and unbounded, stay opt-in.

**A submitted job that never starts stays pending, and `oldest_pending_seconds`
keeps growing.** That is the signal, not a leak: a pool shut down with work
queued, or two workers against a hundred solves, both show up as an age that
climbs. Nothing here ever quietly forgets a ticket to make the numbers tidy.

**A ticket is idempotent and every out-of-order call is defined.**
`started()` twice, `finished()` twice, `finished()` without `started()` — all
have a meaning and none of them raise, because a queue whose metering throws has
made observability the reason the job did not run.

It is deliberately *not* wrapped in a blanket `except`, though. A metering bug
that is swallowed is a metering bug that ships: `_finished` as a counter shadowed
`_finished` as a method here for about ten minutes, `self._meter._finished(...)`
called an integer, and the `TypeError` died unread inside a `Future` while every
job quietly failed to be counted as finished. A visible crash would have been the
better failure, and the tests are what has to catch this class.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from app.observe.collect import is_collecting, record
from app.observe.records import Distribution, Span

#: How many recent wait/run samples are kept. Enough to see a shift; small
#: enough that the meter is a fixed, tiny amount of memory for the process life.
SAMPLE_WINDOW: Final = 256

#: Span names this module emits. Declared in `app.observe.catalogue`.
WAIT_SPAN: Final = "jobs.wait"
RUN_SPAN: Final = "jobs.run"


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """The queue as it is right now, plus the recent past.

    `pending` and `running` are derived from the lifecycle counters rather than
    from the executor, because the executor's own queue length says nothing
    about a job that has been running for nine minutes.
    """

    queues: tuple[str, ...]
    submitted: int
    started: int
    finished: int
    failed: int
    pending: int
    running: int
    oldest_pending_seconds: float | None
    longest_running_seconds: float | None
    #: Waits and runs over the recent window. `None` when nothing has completed
    #: that phase yet — never a row of zeros standing in for no data.
    wait: Distribution | None
    run: Distribution | None
    #: True when more jobs have been submitted than the sample window holds, so
    #: `wait` and `run` describe the recent tail and not the whole run.
    windowed: bool

    @property
    def succeeded(self) -> int:
        return self.finished - self.failed

    @property
    def failure_rate(self) -> float | None:
        """`None` rather than 0.0 when nothing has finished yet."""
        return None if self.finished == 0 else self.failed / self.finished


class JobTicket:
    """One job's passage through the queue. Handed out by `QueueMeter.submit`."""

    __slots__ = ("_key", "_meter", "_queue", "_started", "_state", "_submitted", "_waited")

    def __init__(self, meter: QueueMeter, queue: str, submitted: float, key: int) -> None:
        self._meter = meter
        #: Identity in the meter's books. A serial number rather than `id()`,
        #: which CPython reuses the moment a dropped ticket is collected — two
        #: jobs sharing a key would silently lose one from the pending count.
        self._key = key
        self._queue = queue
        self._submitted = submitted
        self._started: float | None = None
        self._waited = 0.0
        self._state = "pending"

    @property
    def queue(self) -> str:
        return self._queue

    @property
    def state(self) -> str:
        return self._state

    def started(self) -> None:
        """The worker picked it up. Ends the wait, begins the run."""
        if self._state != "pending":
            return
        self._started = time.perf_counter()
        self._waited = self._started - self._submitted
        self._state = "running"
        self._meter._started(self, self._waited)

    def finished(self, ok: bool = True, failure: str = "") -> None:
        """The job returned. `ok=False` records it as a failure."""
        if self._state == "finished":
            return
        if self._state == "pending":
            # A job that finished without ever reporting a start: the queue ran
            # it without metering the pickup. Counted, with a zero-length run
            # rather than a fabricated one, and the wait absorbs the whole span.
            self.started()
        end = time.perf_counter()
        run = end - (self._started if self._started is not None else self._submitted)
        self._state = "finished"
        self._meter._finished(self, self._waited, run, ok, failure)


class QueueMeter:
    """Lifetime counters and a recent-sample window for one process."""

    def __init__(self, sample_window: int = SAMPLE_WINDOW) -> None:
        self._lock = threading.Lock()
        self._sample_window = sample_window
        self._keys = itertools.count()
        self.reset()

    def reset(self) -> None:
        """Forget everything. For tests, and for nothing else.

        The counters are `_started_count` and `_finished_count` rather than
        `_started`/`_finished`: an instance attribute named after a method
        shadows it, and `self._meter._finished(...)` then calls an integer.
        That shipped for about ten minutes and every job silently failed to
        be counted as finished, because the exception died inside a Future
        nobody reads.
        """
        with self._lock:
            self._queues: dict[str, int] = {}
            self._submitted = 0
            self._started_count = 0
            self._finished_count = 0
            self._failed = 0
            self._waits: deque[float] = deque(maxlen=self._sample_window)
            self._runs: deque[float] = deque(maxlen=self._sample_window)
            self._pending: dict[int, float] = {}
            self._running: dict[int, float] = {}

    # -- what a queue calls ---------------------------------------------------

    def submit(self, queue: str = "unknown") -> JobTicket:
        now = time.perf_counter()
        ticket = JobTicket(self, queue, now, next(self._keys))
        with self._lock:
            self._submitted += 1
            self._queues[queue] = self._queues.get(queue, 0) + 1
            self._pending[ticket._key] = now
        return ticket

    def _started(self, ticket: JobTicket, waited: float) -> None:
        with self._lock:
            self._started_count += 1
            self._pending.pop(ticket._key, None)
            self._running[ticket._key] = time.perf_counter()
            self._waits.append(waited)

    def _finished(
        self, ticket: JobTicket, waited: float, ran: float, ok: bool, failure: str
    ) -> None:
        with self._lock:
            self._finished_count += 1
            if not ok:
                self._failed += 1
            self._running.pop(ticket._key, None)
            self._runs.append(ran)
        # Outside the lock: recording a span takes the recorder's lock, and
        # holding two locks in a fixed order across modules is how a deadlock
        # gets written by somebody who was not looking for one.
        if is_collecting():
            record(
                Span(
                    name=WAIT_SPAN,
                    seconds=max(0.0, waited),
                    ok=True,
                    fields=MappingProxyType({"queue": ticket.queue}),
                    thread=threading.current_thread().name,
                    started_at=time.time() - ran - waited,
                )
            )
            record(
                Span(
                    name=RUN_SPAN,
                    seconds=max(0.0, ran),
                    ok=ok,
                    failure=failure,
                    fields=MappingProxyType({"queue": ticket.queue}),
                    thread=threading.current_thread().name,
                    started_at=time.time() - ran,
                )
            )

    # -- what an operator reads -----------------------------------------------

    def snapshot(self) -> QueueSnapshot:
        now = time.perf_counter()
        with self._lock:
            # Earliest submission still unstarted, and earliest start still
            # unfinished: the two ages an operator actually asks about.
            oldest = min(self._pending.values(), default=None)
            longest = min(self._running.values(), default=None)
            return QueueSnapshot(
                queues=tuple(sorted(self._queues)),
                submitted=self._submitted,
                started=self._started_count,
                finished=self._finished_count,
                failed=self._failed,
                pending=len(self._pending),
                running=len(self._running),
                oldest_pending_seconds=None if oldest is None else now - oldest,
                longest_running_seconds=None if longest is None else now - longest,
                wait=Distribution.of(list(self._waits)) if self._waits else None,
                run=Distribution.of(list(self._runs)) if self._runs else None,
                windowed=self._submitted > self._sample_window,
            )


#: The process-wide meter. `app.jobs.queue` calls it; everything else reads it.
METER: Final = QueueMeter()
