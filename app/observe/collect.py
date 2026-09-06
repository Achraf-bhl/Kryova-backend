"""Timing and counting that is near-free when nobody is collecting.

The one rule this module exists to obey: **an instrument that costs 5% is an
instrument somebody turns off**, and an instrument that is off is worth less
than none at all, because the code around it still carries the clutter. So the
disabled path is a module-global read, one small kwargs dict, and a shared
singleton — no clock read, no allocation of a record, no lock, no thread-local
lookup. `tests/test_observe.py` measures it and pins an absolute ceiling.

    with span("mesh.gmsh.session", nodes=len(mesh.nodes)) as held:
        ...
        held.add("elements", n)

Four properties worth knowing before changing anything here.

**Collection is process-wide, not per-request.** `collect()` swaps a module
global. That is deliberate: the spans most worth having come off background
worker threads — meshing, solving, a job that outlived the request that queued
it — and a `ContextVar`-scoped recorder would miss every one of them, because
`ThreadPoolExecutor` does not carry a context across `submit`. The cost is that
two concurrent collections in one process would see each other's spans; this is
a diagnostic tool with one operator, and the tests are sequential.

**Parentage is per-context, and does not cross the queue.** The *current* span
is a `ContextVar`, so nesting works within a thread and a worker thread starts
at depth 0 with no parent. A job's relationship to whatever queued it travels on
the queue ticket (`app.observe.queue`), not on a span, because a span whose
parent finished ten minutes ago is a misleading picture of what nested in what.

**A span that raised is recorded, with its failure.** `__exit__` records on
every path, including `GeneratorExit` — which is how an abandoned streaming read
shows up as a partial one, rather than as a read that never happened.

**A listener sees every span, whether or not anything is collecting.** Added
for P8: metering has to be *always on* — a bill that only counts what an
operator remembered to enable is not a bill — while a trace stays opt-in for the
reason above. So `add_listener` is the seam, and it is the only thing that can
make `span()` do work when no `collect()` is running. With no listener
registered the disabled path is byte-for-byte what it was: a module-global read
and the shared `INERT` object.

Two consequences worth stating. **A listener is called on the instrumented
thread, inside the `with` block's exit**, so it must be quick — the metering
listener accumulates into a context-local scope and defers every database write
to the end of the scope. And **a listener that raises breaks the code it was
observing**; that is deliberate and matches `app.observe.queue`'s refusal to
wrap itself in a blanket `except`. Swallowing belongs to whoever needs it, where
it can be counted and reported — `app.core.metering` does exactly that and
records a `MeteringFault` for every failure it absorbs.

**A recorder is bounded and says when it dropped.** Ten thousand spans is a
generous trace and a bounded one; past that, spans are counted by name and
discarded, and the report refuses to present the affected numbers as measured.
Silently keeping the first ten thousand and reporting the mean as if it were the
mean of everything is the failure this package is supposed to make impossible.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType, TracebackType
from typing import Any, Final, Literal

from app.observe.records import Note, Span

#: How much of an exception's `str` is kept on a span. Long enough to name the
#: file gmsh could not read; short enough that a stack-trace-in-a-message does
#: not become the trace.
_FAILURE_CHARS: Final = 300

#: Default ceiling on retained spans. A trace bigger than this is a profiler's
#: job, not this package's.
DEFAULT_MAX_SPANS: Final = 10_000


class Recorder:
    """Somewhere for spans to land. Thread-safe; bounded; honest when full."""

    def __init__(self, max_spans: int = DEFAULT_MAX_SPANS, label: str = "") -> None:
        if max_spans < 1:
            raise ValueError("max_spans must be at least 1")
        self.label = label
        self.max_spans = max_spans
        self.started_at = time.time()
        self.stopped_at: float | None = None
        self._started_perf = time.perf_counter()
        self._stopped_perf: float | None = None
        self._lock = threading.Lock()
        self._spans: list[Span] = []
        self._dropped: dict[str, int] = {}
        self._notes: list[Note] = []
        self._open: dict[int, str] = {}
        self._sequence = itertools.count()

    # -- writing --------------------------------------------------------------

    def record(self, span: Span) -> None:
        with self._lock:
            if len(self._spans) < self.max_spans:
                self._spans.append(span)
            else:
                self._dropped[span.name] = self._dropped.get(span.name, 0) + 1

    def note(self, text: str, **fields: Any) -> None:
        """Attach an observation that is not a duration."""
        with self._lock:
            self._notes.append(Note(text=text, fields=dict(fields)))

    def _opened(self, key: int, name: str) -> int:
        with self._lock:
            self._open[key] = name
            return next(self._sequence)

    def _closed(self, key: int) -> None:
        with self._lock:
            self._open.pop(key, None)

    # -- reading --------------------------------------------------------------

    @property
    def spans(self) -> tuple[Span, ...]:
        with self._lock:
            return tuple(self._spans)

    @property
    def notes(self) -> tuple[Note, ...]:
        with self._lock:
            return tuple(self._notes)

    @property
    def dropped(self) -> Mapping[str, int]:
        """Spans discarded because the buffer was full, by name."""
        with self._lock:
            return MappingProxyType(dict(self._dropped))

    @property
    def unfinished(self) -> tuple[str, ...]:
        """Names of spans that were open when this was read.

        A span still running is not a span that took zero seconds, and it is not
        a span that did not happen. It has no duration yet, so it is reported as
        something the collection could not measure.
        """
        with self._lock:
            return tuple(sorted(self._open.values()))

    @property
    def seconds(self) -> float:
        """Wall-clock length of the collection window."""
        end = self._stopped_perf if self._stopped_perf is not None else time.perf_counter()
        return end - self._started_perf

    def _stop(self) -> None:
        self._stopped_perf = time.perf_counter()
        self.stopped_at = time.time()


class LiveSpan:
    """An open span. Only exists while something is being collected."""

    __slots__ = (
        "_depth",
        "_failure",
        "_fields",
        "_parent_name",
        "_recorder",
        "_start",
        "_token",
        "name",
        "sequence",
    )

    def __init__(self, name: str, fields: dict[str, Any], recorder: Recorder | None) -> None:
        self.name = name
        self._fields = fields
        #: `None` when the span exists only because a listener is registered —
        #: metering is on, tracing is not. Everything below tolerates it rather
        #: than branching at the call site.
        self._recorder = recorder
        self._failure = ""
        self._start = 0.0
        self._token: Any = None
        self._depth = 0
        self._parent_name: str | None = None
        self.sequence = 0

    # -- what the call site records -------------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Record what this span was working on. Last write wins."""
        self._fields[key] = value

    def add(self, key: str, amount: float) -> None:
        """Accumulate — bytes as chunks stream, elements as they are read."""
        self._fields[key] = self._fields.get(key, 0) + amount

    def fail(self, reason: str) -> None:
        """Mark the span failed without raising.

        For the operations that report failure by returning something rather
        than by throwing — `ccx` printing `*ERROR` and exiting 0 is the case
        this exists for.
        """
        self._failure = reason or "failed"

    # -- lifecycle ------------------------------------------------------------

    def __enter__(self) -> LiveSpan:
        parent = _CURRENT.get()
        self.sequence = 0 if self._recorder is None else self._recorder._opened(id(self), self.name)
        self._parent_name = parent.name if parent is not None else None
        self._depth = 0 if parent is None else parent._depth + 1
        self._token = _CURRENT.set(self)
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        seconds = time.perf_counter() - self._start
        if self._token is not None:
            _CURRENT.reset(self._token)
        failure = self._failure
        if exc_type is not None:
            failure = _describe(exc_type, exc)
        finished = Span(
            name=self.name,
            seconds=seconds,
            ok=not failure,
            failure=failure,
            fields=MappingProxyType(dict(self._fields)),
            depth=self._depth,
            parent=self._parent_name,
            started_at=time.time() - seconds,
            sequence=self.sequence,
            thread=threading.current_thread().name,
        )
        if self._recorder is not None:
            self._recorder._closed(id(self))
            self._recorder.record(finished)
        _notify(finished)
        return False


class _InertSpan:
    """What `span()` returns when nothing is collecting.

    One shared instance, no state, no clock. Every method a call site may use is
    present and does nothing, so instrumented code never branches on whether
    collection is on.
    """

    __slots__ = ()

    def __enter__(self) -> _InertSpan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Literal[False]:
        return False

    def set(self, key: str, value: Any) -> None:
        return None

    def add(self, key: str, amount: float) -> None:
        return None

    def fail(self, reason: str) -> None:
        return None


#: The singleton returned on the disabled path. Exported for the test that pins
#: the disabled path as allocation-free: `span("x") is INERT`.
INERT: Final = _InertSpan()

#: What a listener is handed: one finished span, already frozen.
SpanListener = Callable[[Span], None]

_RECORDER: Recorder | None = None
#: A tuple rather than a list so `span()` reads it without a lock and can never
#: see a half-mutated sequence; every mutation replaces it wholesale.
_LISTENERS: tuple[SpanListener, ...] = ()
_SWAP = threading.Lock()
_CURRENT: ContextVar[LiveSpan | None] = ContextVar("kryova_observe_current_span", default=None)


def span(name: str, /, **fields: Any) -> LiveSpan | _InertSpan:
    """Time a block of work. Free-ish when nobody is collecting.

    `name` is dotted and belongs to `app.observe.catalogue` — a span name that
    is not declared there fails `tests/test_observe_report.py`, which is what
    keeps the report's "what I could not measure" list honest: it can only name
    a site it knows exists.
    """
    recorder = _RECORDER
    if recorder is None and not _LISTENERS:
        return INERT
    return LiveSpan(name, fields, recorder)


def record(span_record: Span) -> None:
    """Record an already-measured span.

    For the places that time something themselves because the start and the end
    are in different call frames — the queue ticket, whose wait begins on the
    submitting thread and ends on a worker.
    """
    recorder = _RECORDER
    if recorder is not None:
        recorder.record(span_record)
    _notify(span_record)


def note(text: str, **fields: Any) -> None:
    """Attach a non-duration observation to whatever is collecting."""
    recorder = _RECORDER
    if recorder is not None:
        recorder.note(text, **fields)


def _notify(finished: Span) -> None:
    """Hand a finished span to every listener.

    Deliberately not wrapped in `try`. A listener that raises breaks the block
    it was observing, and that is the same judgement `app.observe.queue` records
    about its own metering: a swallowed instrumentation bug is one that ships.
    A listener that needs to survive its own failures owns that responsibility
    and has to be able to say it failed — `app.core.metering` counts, logs and
    persists every error it absorbs.
    """
    for listener in _LISTENERS:
        listener(finished)


def add_listener(listener: SpanListener) -> None:
    """Watch every span from now on, whether or not a collection is running.

    Registering the first listener is what turns `span()` from a module-global
    read into a real measurement, so it is not free — add one for something that
    must be always on (metering), never to avoid calling `collect()`.
    """
    global _LISTENERS
    with _SWAP:
        if listener not in _LISTENERS:
            _LISTENERS = (*_LISTENERS, listener)


def remove_listener(listener: SpanListener) -> None:
    """Stop watching. A listener that was never added is not an error."""
    global _LISTENERS
    with _SWAP:
        _LISTENERS = tuple(existing for existing in _LISTENERS if existing != listener)


def listeners() -> tuple[SpanListener, ...]:
    return _LISTENERS


def is_collecting() -> bool:
    return _RECORDER is not None


def current_recorder() -> Recorder | None:
    return _RECORDER


def current_span() -> LiveSpan | None:
    return _CURRENT.get()


@contextmanager
def collect(max_spans: int = DEFAULT_MAX_SPANS, label: str = "") -> Iterator[Recorder]:
    """Turn collection on for the duration of the block, process-wide.

    Nested calls replace and restore; spans opened under the inner recorder
    still land in it, because a `LiveSpan` holds its recorder rather than
    looking one up on the way out.
    """
    global _RECORDER
    recorder = Recorder(max_spans=max_spans, label=label)
    with _SWAP:
        previous = _RECORDER
        _RECORDER = recorder
    try:
        yield recorder
    finally:
        with _SWAP:
            _RECORDER = previous
        recorder._stop()


def _describe(exc_type: type[BaseException], exc: BaseException | None) -> str:
    text = str(exc) if exc is not None else ""
    text = " ".join(text.split())[:_FAILURE_CHARS]
    return f"{exc_type.__name__}: {text}" if text else exc_type.__name__
