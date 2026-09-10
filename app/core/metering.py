"""What Kryova spends, counted so the number can be argued with (Phase P8).

Metering is the harder half of billing and it is built first, because a plan and
a price list are product decisions that can be changed in an afternoon, while a
meter nobody can reconstruct is a dispute nobody can win. **A billed number that
cannot be traced back to the run that produced it is the same defect as an
unmeasured claim, with money attached.** So every quantity here arrives bound to
its cause and to how it was obtained, in the vocabulary
`app.kernel.provenance` and `app.verify.provenance` already use — measured,
approximated, unavailable-with-a-reason — and not in a second one invented for
invoices.

Four decisions shape everything below.

**The meter reads `app.observe`; it does not instrument a second time.** That
package already times the CalculiX subprocess, the in-house solver, gmsh's
critical section and an OCCT rebuild. Timing them again beside those hooks would
produce two numbers for one event, and P8.4 is explicit that the meter that
bills and the meter shown in an estimate are one number with two uses —
"divergence is a bug class of its own". What `app.observe` did not have, and now
does, is a way to see a span when no `collect()` is running: a trace is opt-in
because it is expensive, and a bill cannot be. `app.observe.collect.add_listener`
is that seam, added for this, and it costs nothing until something registers.
`SPAN_METERS` below is the whole of the mapping from a span to a meter.

**A span carries no tenant, so a scope supplies one.** `usage_scope()` puts a
`Cause` — the organisation, the job, the project, the conversation — into a
`ContextVar` for the duration of the work, and every span that finishes inside
it is attributed to that cause. A `ContextVar` rather than a thread id because
that is what stays correct when two jobs run on two workers, and because it is
the same mechanism `app.observe` already uses for span parentage. A span that
finishes with no scope open is *counted as unattributed* rather than guessed at:
a wrong tenant on a bill is worse than a missing line on one.

**Quantities are integers; `Decimal` appears only at the edge.** See
`app.models.billing`. `UsageEvent.of` refuses a `float` outright, and
`UsageEvent.from_seconds` is the single sanctioned conversion — the boundary at
which a clock reading becomes a counted unit, in the same sense that units land
in mm-N-MPa at the boundary and never deeper in.

**Metering never fails the work, and never fails quietly.** Every write goes
through `emit_safely`, which cannot raise. What it *does* do on failure is move
a live process counter, log the exception with its stack, and write a
`MeteringFault` row. That triple exists because of a defect this repository
measured on 2026-09-06: `QueueMeter._finished` shadowed its own counter, the
resulting `TypeError` died unread inside a `Future`, and no job was counted as
finished for as long as it took somebody to notice. A swallowed metering bug is
a metering bug that ships. `METERING.snapshot()` is what a test — and an
operator — reads to prove the meter is still counting.
"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Any, Final, Protocol

from sqlalchemy import func, select, union, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.kernel.provenance import Basis
from app.models.base import utcnow
from app.models.billing import (
    BillingAccount,
    Meter,
    MeteringFault,
    Plan,
    UsageRecord,
    UsageRollup,
)
from app.observe import Span, add_listener, remove_listener

logger = logging.getLogger(__name__)

#: Same shape as `app.simulation.runner.SessionScope`, declared here rather than
#: imported: `app.core` sits under everything and must not depend upward on the
#: simulation package to name a callable.
SessionScope = Callable[[], AbstractContextManager[Session]]

_EMPTY: Final[Mapping[str, Any]] = MappingProxyType({})
#: The same empty mapping, typed for meter allowances. A separate name because
#: mypy is right that one `Mapping[str, Any]` cannot stand in for both.
_NO_ALLOWANCES: Final[Mapping["Meter", int]] = MappingProxyType({})

#: How a CATIA seat second is arrived at, spelled once so every record that
#: claims one says the same thing. It is `APPROXIMATED`, and the reason is not a
#: hedge: what is measured is the duration of one bridge call, and an engineer's
#: seat is occupied by the whole session around those calls — thinking time,
#: dialogs, the pauses between operations. The measured number is a *lower
#: bound* on seat occupancy, and reporting it as a measurement would be the
#: fabrication Decision 3 exists to prevent.
CATIA_SEAT_METHOD: Final = (
    "summed duration of the bridge calls Kryova drove; a lower bound on seat "
    "occupancy, which also includes the time between calls"
)


# ---------------------------------------------------------------------------
# What caused a number
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cause:
    """What produced a usage record, carried with the record.

    This is `app.verify.provenance.RunProvenance` applied to a bill: the same
    idea (a number is worthless without the chain behind it), the same refusal
    (a record that cannot say what produced it is rejected at construction), and
    deliberately none of its fields — a mesh digest belongs to a stress figure,
    not to a line on an invoice. What travels here is the *addressable* chain:
    the tenant, and the rows a person can open.

    `source` is module-shaped ("simulation.runner", "catia.bridge") and matches
    the dotted names in `app.observe.catalogue`, so a usage row and a span can
    be lined up by eye rather than by guesswork.
    """

    organisation_id: str
    source: str
    subject_type: str
    subject_id: str
    project_id: str | None = None
    simulation_job_id: str | None = None
    geometry_version_id: str | None = None
    conversation_id: str | None = None
    media_id: str | None = None
    user_id: str | None = None
    #: Anything else needed to reconstruct the number and nothing else models:
    #: the solver name and version, the model name, the plan digest.
    detail: Mapping[str, Any] = _EMPTY

    def __post_init__(self) -> None:
        """Refuse a cause that cannot name itself.

        The same refusal `RunProvenance.__post_init__` makes, for the same
        reason: a record with a blank tenant or a blank subject is not a weak
        binding, it is none, and nobody reading the invoice a year later can
        tell which run it describes. Failing here costs the record; discovering
        it later costs the argument.
        """
        missing = [
            name
            for name, value in (
                ("organisation_id", self.organisation_id),
                ("source", self.source),
                ("subject_type", self.subject_type),
                ("subject_id", self.subject_id),
            )
            if not (value or "").strip()
        ]
        if missing:
            raise ValueError(
                f"A usage record must say what caused it; {', '.join(missing)} is "
                "blank. A billed number whose cause cannot be named is not evidence "
                "of anything — bind it to the job, the conversation or the geometry "
                "version that produced it."
            )

    def with_detail(self, **extra: Any) -> Cause:
        """A copy carrying more reconstruction detail. Never mutates."""
        return replace(self, detail=MappingProxyType({**dict(self.detail), **extra}))

    def bind(self, **fields: Any) -> Cause:
        """A copy pointing at one more row — the blob a byte count is for.

        Separate from `with_detail` because these are *addressable*: a media id
        in `detail` is a string somebody has to go and look up, while one in
        `media_id` is a foreign key the record can be joined on and a person can
        follow. The distinction is the whole reason both exist.
        """
        return replace(self, **fields)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """One quantity, its provenance, and its cause. Ready to be written down."""

    meter: Meter
    #: In `meter.scale` units. Integer — see the module docstring.
    quantity_units: int
    basis: Basis
    method: str
    cause: Cause
    occurred_at: datetime

    @property
    def quantity(self) -> Decimal:
        return Decimal(self.quantity_units) / Decimal(self.meter.scale)

    @classmethod
    def of(
        cls,
        meter: Meter,
        quantity: int | Decimal,
        *,
        basis: Basis,
        method: str,
        cause: Cause,
        occurred_at: datetime | None = None,
    ) -> UsageEvent:
        """Build an event from an exact quantity.

        **A `float` is refused, not converted.** Accepting one would make the
        rule "money and counted units are never floating point" a comment rather
        than a guarantee, and the failure it prevents is silent: a float sum over
        ten thousand solves is a total that changes with the order of the rows.
        Where a float is genuinely what was measured — a clock — use
        `from_seconds`, which converts once and says so.

        `UNAVAILABLE` is refused too. A usage record is a number; a meter that
        could not be measured is a *gap*, reported by name through
        `UsageScope.gaps` and `unwired_meters()`, never a record with a
        placeholder in it.
        """
        if isinstance(quantity, bool) or isinstance(quantity, float):
            raise TypeError(
                f"{meter.value} was given a {type(quantity).__name__} quantity. Counted "
                "units and money are never floating point here: a float total is one "
                "that changes with the order it was summed in. Pass an int or a "
                "Decimal, or use UsageEvent.from_seconds for a clock reading."
            )
        if basis is Basis.UNAVAILABLE:
            raise ValueError(
                "An unavailable quantity is not a usage record. Report it as a gap "
                "(UsageScope.gaps / unwired_meters) so the summary can name what it "
                "could not measure, rather than writing a number nobody measured."
            )
        if not method.strip():
            raise ValueError(
                f"{meter.value} needs to say how it was arrived at. An approximated "
                "number with no method is not auditable, and a measured one with no "
                "method cannot be re-derived."
            )
        units = _to_units(meter, Decimal(quantity))
        if units < 0:
            raise ValueError(f"{meter.value} cannot be negative; got {quantity!r}.")
        return cls(
            meter=meter,
            quantity_units=units,
            basis=basis,
            method=method,
            cause=cause,
            occurred_at=occurred_at or utcnow(),
        )

    @classmethod
    def from_seconds(
        cls,
        meter: Meter,
        seconds: float,
        *,
        basis: Basis,
        method: str,
        cause: Cause,
        multiplier: int = 1,
        occurred_at: datetime | None = None,
    ) -> UsageEvent:
        """The one place a float becomes a counted unit, and it is a boundary.

        A clock returns a float; there is no honest way around that. So the
        conversion happens exactly once, here, through `Decimal(str(seconds))` —
        which takes the number as it prints rather than as it is stored in
        binary, so 0.1 seconds is a tenth of a second and not
        0.1000000000000000055511151231257827. From this point the quantity is an
        integer count of microseconds and every sum over it is exact.

        `multiplier` is for the meters whose unit is a product — element-seconds
        — and is an integer for the same reason everything else is.
        """
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError(f"{meter.value} needs a number of seconds; got {seconds!r}.")
        if math.isnan(seconds) or math.isinf(seconds):
            raise ValueError(f"{meter.value} was given {seconds!r} seconds.")
        if isinstance(multiplier, bool) or not isinstance(multiplier, int):
            raise TypeError(
                f"{meter.value} needs an integer multiplier; got {multiplier!r}."
            )
        quantity = Decimal(str(float(seconds))) * multiplier
        return cls.of(
            meter, quantity, basis=basis, method=method, cause=cause, occurred_at=occurred_at
        )


def _to_units(meter: Meter, quantity: Decimal) -> int:
    """Whole units of `meter` as an exact integer count of its smallest unit.

    Rounding happens here and nowhere afterwards. Half-up rather than banker's,
    matching the render layer's `floor(v + 0.5)` choice: a rule an operator can
    reproduce with a pencil beats one that is statistically nicer and surprising.
    """
    scaled = quantity * meter.scale
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Where events land
# ---------------------------------------------------------------------------


class MeterSink(Protocol):
    """Somewhere a batch of events goes.

    A batch, never a single event: P8.1 says usage "accumulates locally and
    posts in aggregates ... never one event per action", and a sink that took
    one event at a time would make the aggregate the caller's problem in every
    call site.
    """

    def emit(self, events: Sequence[UsageEvent]) -> None: ...


class LedgerSink:
    """Writes to `usage_records`, on a session of its own.

    `SessionScope`, not a borrowed `Session`, and that is the whole design — the
    same reasoning `get_audit_service` records. A metering write must not be
    inside the transaction that carries the work: if it were, a failed insert
    would roll back the simulation result it was measuring, which is precisely
    the "metering fails the thing it measures" outcome this phase forbids. It
    also means a metering write survives the work's transaction being rolled
    back, which is what you want for the run that failed.
    """

    def __init__(self, scope: SessionScope) -> None:
        self._scope = scope

    def emit(self, events: Sequence[UsageEvent]) -> None:
        if not events:
            return
        with self._scope() as session:
            for event in events:
                session.add(to_record(event))
            session.commit()


class CollectingSink:
    """Keeps events in memory. For tests, and for a dry-run estimate."""

    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def emit(self, events: Sequence[UsageEvent]) -> None:
        self.events.extend(events)

    def total(self, meter: Meter) -> Decimal:
        units = sum(e.quantity_units for e in self.events if e.meter is meter)
        return Decimal(units) / Decimal(meter.scale)


class NullSink:
    """Counts nothing, keeps nothing. The explicit way to turn metering off."""

    def emit(self, events: Sequence[UsageEvent]) -> None:
        return None


def to_record(event: UsageEvent) -> UsageRecord:
    """One event as the row it becomes."""
    cause = event.cause
    return UsageRecord(
        organisation_id=cause.organisation_id,
        meter=event.meter,
        quantity_units=event.quantity_units,
        basis=str(event.basis),
        method=event.method,
        occurred_at=event.occurred_at,
        usage_date=event.occurred_at.astimezone(timezone.utc).date(),
        source=cause.source,
        subject_type=cause.subject_type,
        subject_id=cause.subject_id,
        project_id=cause.project_id,
        simulation_job_id=cause.simulation_job_id,
        geometry_version_id=cause.geometry_version_id,
        conversation_id=cause.conversation_id,
        media_id=cause.media_id,
        user_id=cause.user_id,
        detail=dict(cause.detail),
    )


# ---------------------------------------------------------------------------
# Is the meter still counting?
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MeteringSnapshot:
    """The meter, watching itself."""

    scopes_opened: int
    spans_seen: int
    spans_attributed: int
    #: Spans that finished with no scope open. Not an error — most spans in a
    #: process are not billable work — but a number that should not climb during
    #: a metered job, and the one that would show a scope failing to open.
    spans_unattributed: int
    events_recorded: int
    events_failed: int
    faults: tuple[str, ...]
    last_fault_at: datetime | None

    @property
    def healthy(self) -> bool:
        """No swallowed failure since the last reset. Never a claim about zero."""
        return self.events_failed == 0


class MeteringMonitor:
    """Always-live counters for the meter itself.

    Always live, following `app.observe.queue.QueueMeter` and for its reason:
    "is the meter working" is a question asked *after* it stopped working, and a
    gauge that starts counting when an operator enables it answers by starting
    from zero at the worst possible moment. The cost is a lock and a couple of
    increments per *scope*, next to work measured in seconds.

    The counters are `_events_recorded`/`_events_failed` and not
    `_recorded`/`_failed`, deliberately: an instance attribute named after a
    method shadows it, and `self._failed(...)` then calls an integer. That
    shipped here once, in `QueueMeter`, and every job silently failed to be
    counted because the `TypeError` died inside a `Future`.
    """

    #: How many recent failures are kept for `snapshot()`. Enough to see a
    #: pattern, bounded so a broken meter cannot become a memory leak.
    KEEP_FAULTS: Final = 20

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Forget everything. For tests, and for nothing else."""
        with self._lock:
            self._scopes_opened = 0
            self._spans_seen = 0
            self._spans_attributed = 0
            self._spans_unattributed = 0
            self._events_recorded = 0
            self._events_failed = 0
            self._faults: list[str] = []
            self._last_fault_at: datetime | None = None

    def scope_opened(self) -> None:
        with self._lock:
            self._scopes_opened += 1

    def span_seen(self, attributed: bool) -> None:
        with self._lock:
            self._spans_seen += 1
            if attributed:
                self._spans_attributed += 1
            else:
                self._spans_unattributed += 1

    def recorded(self, count: int) -> None:
        with self._lock:
            self._events_recorded += count

    def failed(self, count: int, failure: str) -> None:
        with self._lock:
            self._events_failed += max(count, 1)
            self._faults.append(failure)
            del self._faults[: max(0, len(self._faults) - self.KEEP_FAULTS)]
            self._last_fault_at = utcnow()

    def snapshot(self) -> MeteringSnapshot:
        with self._lock:
            return MeteringSnapshot(
                scopes_opened=self._scopes_opened,
                spans_seen=self._spans_seen,
                spans_attributed=self._spans_attributed,
                spans_unattributed=self._spans_unattributed,
                events_recorded=self._events_recorded,
                events_failed=self._events_failed,
                faults=tuple(self._faults),
                last_fault_at=self._last_fault_at,
            )


#: The process-wide monitor. Everything in this module writes it; the billing
#: route and the tests read it.
METERING: Final = MeteringMonitor()


def describe_failure(exc: BaseException) -> str:
    """`TypeError: ...`, shaped like `app.observe.collect._describe`."""
    text = " ".join(str(exc).split())[:300]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def emit_safely(
    sink: MeterSink,
    events: Sequence[UsageEvent],
    *,
    fault_scope: SessionScope | None = None,
    source: str = "",
) -> bool:
    """Write a batch, absorbing every failure. Returns whether it landed.

    This is the function that makes "metering must never fail the thing it
    measures" true, and the three lines after the `except` are what stop it
    becoming "metering fails silently". A caller may check the return value; a
    caller that does not is still safe, because the counter, the log and the
    `MeteringFault` row all moved without it.
    """
    if not events:
        return True
    try:
        sink.emit(events)
    except Exception as exc:  # noqa: BLE001 - absorbing this is the whole contract
        failure = describe_failure(exc)
        METERING.failed(len(events), failure)
        logger.exception(
            "Metering failed for %d event(s); the work it measured is unaffected",
            len(events),
        )
        _persist_fault(fault_scope, events, failure, source)
        return False
    METERING.recorded(len(events))
    return True


def _persist_fault(
    fault_scope: SessionScope | None,
    events: Sequence[UsageEvent],
    failure: str,
    source: str,
) -> None:
    """Best effort, and it says so.

    A failure to record the failure cannot be allowed to propagate either — the
    most likely cause of both is the same unreachable database. When this loses
    too, the process counter and the logged stack are what is left, which is why
    neither of them is optional.
    """
    if fault_scope is None:
        return
    first = events[0]
    try:
        with fault_scope() as session:
            session.add(
                MeteringFault(
                    meter=str(first.meter),
                    organisation_id=first.cause.organisation_id or None,
                    source=source or first.cause.source,
                    failure=failure,
                    detail={
                        "events": len(events),
                        "meters": sorted({str(e.meter) for e in events}),
                        "subject_type": first.cause.subject_type,
                        "subject_id": first.cause.subject_id,
                    },
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Could not record a metering fault: %s", failure)


# ---------------------------------------------------------------------------
# From an observed span to a metered quantity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SpanMeter:
    """One `app.observe` span name, and what it costs.

    The mapping is data rather than code so it can be read in one place and
    asserted against `app.observe.catalogue` — a mapping that names a span the
    system cannot emit is a meter that will silently always be zero, which is
    the failure `catalogue` itself exists to prevent for timings.
    """

    span: str
    meter: Meter
    method: str
    basis: Basis = Basis.MEASURED
    #: When set, the quantity is the *sum of this span field* rather than the
    #: duration — an operation count, not a time.
    from_field: str | None = None
    #: When set, the duration is multiplied by this field (or by a scope
    #: annotation of the same name). Element-seconds is the case: forty seconds
    #: on 1.2 M nodes and forty seconds on nine hundred are not the same bill.
    times_field: str | None = None

    def __post_init__(self) -> None:
        if self.from_field and self.times_field:
            raise ValueError(
                f"{self.span} cannot both take its quantity from a field and "
                "multiply a duration by one; pick which the meter counts."
            )


SPAN_METERS: Final[tuple[SpanMeter, ...]] = (
    SpanMeter(
        span="solve.linear_static",
        meter=Meter.SOLVER_SECONDS,
        method="wall clock around assembly, factorisation and stress recovery "
        "(app.observe span solve.linear_static)",
    ),
    SpanMeter(
        span="solve.calculix.run",
        meter=Meter.SOLVER_SECONDS,
        method="wall clock around the ccx subprocess (app.observe span solve.calculix.run)",
    ),
    SpanMeter(
        span="mesh.gmsh.session",
        meter=Meter.MESH_ELEMENT_SECONDS,
        method="seconds holding the gmsh lock, multiplied by the elements produced",
        times_field="elements",
    ),
    SpanMeter(
        span="kernel.rebuild",
        meter=Meter.KERNEL_OPERATIONS,
        method="operations executed per OCCT regeneration (app.observe span kernel.rebuild)",
        from_field="operations",
    ),
)

BY_SPAN: Final[Mapping[str, SpanMeter]] = MappingProxyType(
    {entry.span: entry for entry in SPAN_METERS}
)

if len(BY_SPAN) != len(SPAN_METERS):  # pragma: no cover - a duplicate is a typo
    raise ValueError("two span meters share a span name")


@dataclass(frozen=True, slots=True)
class MeterSite:
    """Where a meter's numbers come from — including where they do not yet.

    `app.observe.catalogue` applied to billing, and for its reason: a summary
    that lists only what it happened to see cannot tell you what it failed to
    see, and "this tenant used no CATIA seat time" and "nothing meters CATIA
    seat time" are the same silence if the second one is not written down. The
    billing summary prints an unwired meter as unmetered rather than as zero.
    """

    meter: Meter
    how: str
    wired: bool = True
    not_wired_because: str = ""

    def __post_init__(self) -> None:
        if not self.wired and not self.not_wired_because:
            raise ValueError(f"{self.meter.value} is declared unwired with no reason given")
        if self.wired and self.not_wired_because:
            raise ValueError(f"{self.meter.value} is wired; it needs no excuse")


METER_SITES: Final[tuple[MeterSite, ...]] = (
    MeterSite(
        meter=Meter.SOLVER_SECONDS,
        how="observed from the solver spans inside a usage scope opened by "
        "app.simulation.runner.run_simulation",
    ),
    MeterSite(
        meter=Meter.MESH_ELEMENT_SECONDS,
        how="the gmsh span's duration multiplied by the element count the runner "
        "annotates onto the scope",
    ),
    MeterSite(
        meter=Meter.STORAGE_BYTES,
        how="recorded by record_storage when a blob is committed to the store",
    ),
    MeterSite(
        meter=Meter.KERNEL_OPERATIONS,
        how="observed from kernel.rebuild spans inside the usage scope "
        "app.catia.dispatch opens around every operation (P8.1, wired 2026-09-10)",
    ),
    MeterSite(
        meter=Meter.AI_TOKENS,
        how="record_tokens in app.api.routes.ai._meter_tokens, beside the existing "
        "app.ai.usage ledger write (P8.1, wired 2026-09-10). The two ledgers answer "
        "different questions and are deliberately not merged: app.ai.usage is a "
        "per-user daily *budget* read before the next call, this is a per-tenant "
        "*bill* summed over a period. A conversation with no project is attributed "
        "through the user's own organisation; one whose user has no organisation at "
        "all is skipped rather than guessed at.",
    ),
    MeterSite(
        meter=Meter.CATIA_SEAT_SECONDS,
        how="record_seat_time from the duration app.catia.dispatch already records "
        "on every CatiaOperation (P8.1, wired 2026-09-10). APPROXIMATED by "
        "construction and always will be — see CATIA_SEAT_METHOD: it sums the calls "
        "Kryova drove and a seat is also occupied between them, so the number is a "
        "lower bound on occupancy.",
    ),
)

if {site.meter for site in METER_SITES} != set(Meter):  # pragma: no cover
    raise ValueError("every meter must declare where its numbers come from")


def unwired_meters() -> tuple[MeterSite, ...]:
    """Meters nothing feeds yet. Reported, never rendered as a zero."""
    return tuple(site for site in METER_SITES if not site.wired)


# ---------------------------------------------------------------------------
# The scope: what binds a span to a tenant
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gap:
    """A meter that could have been recorded and could not be measured.

    The `UNMEASURED` discipline `app.design.assertions` applies to a claim
    nobody measured, applied to a bill: a meter whose multiplier was never
    annotated produces no record *and says so*, rather than a zero that reads as
    "this cost nothing".
    """

    meter: Meter
    reason: str


class UsageScope:
    """Everything metered under one cause, accumulated and posted once.

    Deliberately an accumulator rather than a writer: P8.1 says usage
    "accumulates locally and posts in aggregates ... never one event per
    action", and a solve that emits a row per gmsh call would put more rows in
    the ledger than there are seconds in the job.
    """

    def __init__(self, cause: Cause) -> None:
        self.cause = cause
        self._lock = threading.Lock()
        self._spans: dict[str, list[Span]] = {}
        self._annotations: dict[str, Any] = {}
        self._direct: list[UsageEvent] = []
        self._gaps: list[Gap] = []

    # -- what the work tells the scope ---------------------------------------

    def annotate(self, **fields: Any) -> None:
        """Facts the spans do not carry — the element count, the solver name.

        `mesh.gmsh.session` declares no fields at all in
        `app.observe.catalogue`, so the element count that turns a meshing
        duration into element-seconds has to come from the caller. That is the
        concrete thing `app.observe` needed and did not have.
        """
        with self._lock:
            self._annotations.update(fields)

    def count(
        self,
        meter: Meter,
        quantity: int | Decimal,
        *,
        method: str,
        basis: Basis = Basis.MEASURED,
        cause: Cause | None = None,
        **detail: Any,
    ) -> None:
        """Record a quantity that is not a duration — bytes, tokens, seats.

        `cause` narrows this one quantity's binding without changing the
        scope's: a byte count is caused by the job *and* is about one blob, and
        the blob id belongs in the foreign key rather than in a detail string
        somebody has to look up by hand.
        """
        base = cause if cause is not None else self.cause
        with self._lock:
            self._direct.append(
                UsageEvent.of(
                    meter,
                    quantity,
                    basis=basis,
                    method=method,
                    cause=base.with_detail(**detail) if detail else base,
                )
            )

    def observe(self, finished: Span) -> None:
        """Take one finished span, if it is one this scope bills."""
        entry = BY_SPAN.get(finished.name)
        if entry is None:
            return
        with self._lock:
            self._spans.setdefault(finished.name, []).append(finished)

    # -- what the scope produces ---------------------------------------------

    @property
    def gaps(self) -> tuple[Gap, ...]:
        return tuple(self._gaps)

    def events(self) -> tuple[UsageEvent, ...]:
        """Everything observed, as events. Computed once, at the end.

        Spans of several names can feed one meter — the in-house solver and
        CalculiX both feed `solver_seconds` — so the accumulation is per meter
        and the contributing span names travel in the detail. That is what keeps
        "a CalculiX nonlinear minute is not a linear-static second" (P8.1)
        answerable from the ledger without forking the meter into six.
        """
        with self._lock:
            spans = {name: list(items) for name, items in self._spans.items()}
            annotations = dict(self._annotations)
            direct = list(self._direct)
            self._gaps = []

        by_meter: dict[Meter, list[tuple[SpanMeter, list[Span]]]] = {}
        for name, items in spans.items():
            entry = BY_SPAN[name]
            by_meter.setdefault(entry.meter, []).append((entry, items))

        events = list(direct)
        for meter, groups in sorted(by_meter.items(), key=lambda pair: pair[0].value):
            event = self._event_for(meter, groups, annotations)
            if event is not None:
                events.append(event)
        return tuple(events)

    def _event_for(
        self,
        meter: Meter,
        groups: Sequence[tuple[SpanMeter, list[Span]]],
        annotations: Mapping[str, Any],
    ) -> UsageEvent | None:
        total = Decimal(0)
        methods: list[str] = []
        contributions: dict[str, Any] = {}
        basis = Basis.MEASURED

        for entry, items in sorted(groups, key=lambda pair: pair[0].span):
            seconds = math.fsum(item.seconds for item in items)
            contributions[entry.span] = {
                "count": len(items),
                "seconds": float(f"{seconds:.6f}"),
                "failures": sum(1 for item in items if not item.ok),
            }
            methods.append(entry.method)
            if entry.basis is Basis.APPROXIMATED:
                basis = Basis.APPROXIMATED

            if entry.from_field is not None:
                values = [item.fields.get(entry.from_field) for item in items]
                if any(not isinstance(v, int) or isinstance(v, bool) for v in values):
                    self._gaps.append(
                        Gap(
                            meter,
                            f"{entry.span} did not carry an integer "
                            f"{entry.from_field!r}, so the quantity could not be read. "
                            "The duration was measured; the count was not.",
                        )
                    )
                    return None
                counted = 0
                for value in values:
                    counted += int(value)  # type: ignore[arg-type]
                total += Decimal(counted)
                continue

            multiplier = 1
            if entry.times_field is not None:
                found = annotations.get(entry.times_field)
                if found is None:
                    found = _first_field(items, entry.times_field)
                if not isinstance(found, int) or isinstance(found, bool) or found < 0:
                    self._gaps.append(
                        Gap(
                            meter,
                            f"{entry.span} ran for {seconds:.3f} s but nothing supplied "
                            f"{entry.times_field!r}, so {meter.value} has no multiplier. "
                            "A duration alone would be a different meter, and a zero "
                            "here would read as 'this cost nothing'. Annotate the "
                            "scope with it.",
                        )
                    )
                    return None
                multiplier = int(found)
            total += Decimal(str(seconds)) * multiplier

        detail: dict[str, Any] = {"spans": contributions}
        if annotations:
            detail["annotations"] = _jsonable(annotations)
        return UsageEvent.of(
            meter,
            total,
            basis=basis,
            method="; ".join(dict.fromkeys(methods)),
            cause=self.cause.with_detail(**detail),
        )


def _first_field(items: Sequence[Span], name: str) -> Any:
    for item in items:
        if name in item.fields:
            return item.fields[name]
    return None


def _jsonable(values: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the detail payload to things JSONB can hold, without guessing."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        out[key] = value if isinstance(value, (str, int, float, bool, type(None))) else str(value)
    return out


_ACTIVE: ContextVar[UsageScope | None] = ContextVar("kryova_metering_scope", default=None)
_LISTENER_LOCK = threading.Lock()
_OPEN_SCOPES = 0


def current_scope() -> UsageScope | None:
    return _ACTIVE.get()


def _on_span(finished: Span) -> None:
    """The listener `app.observe` calls for every finished span.

    Guarded, and this is the one place in the two packages where that is right:
    `app.observe._notify` deliberately does not wrap listeners, because a
    swallowed instrumentation bug is one that ships — so the swallowing lives
    here, where it can be counted, logged and written down.
    """
    try:
        scope = _ACTIVE.get()
        METERING.span_seen(attributed=scope is not None and finished.name in BY_SPAN)
        if scope is not None:
            scope.observe(finished)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        METERING.failed(1, describe_failure(exc))
        logger.exception("Metering could not absorb the span %r", finished.name)


def _listen() -> None:
    global _OPEN_SCOPES
    with _LISTENER_LOCK:
        if _OPEN_SCOPES == 0:
            add_listener(_on_span)
        _OPEN_SCOPES += 1


def _stop_listening() -> None:
    global _OPEN_SCOPES
    with _LISTENER_LOCK:
        _OPEN_SCOPES = max(0, _OPEN_SCOPES - 1)
        if _OPEN_SCOPES == 0:
            remove_listener(_on_span)


@contextmanager
def usage_scope(
    cause: Cause,
    sink: MeterSink,
    *,
    fault_scope: SessionScope | None = None,
) -> Iterator[UsageScope]:
    """Attribute everything metered inside this block to `cause`.

    **The batch is posted in `finally`, so a run that raised is still billed.**
    That is the same judgement `app.observe.Span` makes about a failed span: the
    nine-minute solve that blew up is exactly the one whose cost somebody needs,
    and a ledger holding only the successes makes the arithmetic on what is left
    look better than the system is.

    Nothing in here can raise on the caller's behalf. `emit_safely` absorbs
    every write failure, `_on_span` absorbs every accumulation failure, and both
    leave evidence.
    """
    scope = UsageScope(cause)
    METERING.scope_opened()
    _listen()
    token = _ACTIVE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE.reset(token)
        _stop_listening()
        try:
            events = scope.events()
        except Exception as exc:  # noqa: BLE001 - a broken scope must not fail the work
            METERING.failed(1, describe_failure(exc))
            logger.exception("Metering could not assemble the events for %s", cause.source)
            events = ()
        emit_safely(sink, events, fault_scope=fault_scope, source=cause.source)


# ---------------------------------------------------------------------------
# The meters nothing observes: bytes, tokens, seat time
# ---------------------------------------------------------------------------


def record_storage(
    scope: UsageScope,
    *,
    size_bytes: int,
    media_id: str | None = None,
    sha256: str | None = None,
    deduplicated: bool = False,
) -> None:
    """Bytes committed to the blob store, as a stock rather than a flow.

    `deduplicated` travels on the record because it changes what the number
    means: two projects uploading the same STEP file cost one copy on disk, and
    a ledger that billed both for the bytes would be charging for a file that
    was never written. It is recorded rather than filtered so the reconciliation
    in `storage_attribution` can be checked against it.
    """
    scope.count(
        Meter.STORAGE_BYTES,
        int(size_bytes),
        method="size of the blob as the content-addressed store wrote it",
        cause=scope.cause.bind(media_id=media_id) if media_id else None,
        sha256=sha256 or "",
        deduplicated=deduplicated,
    )


def record_tokens(scope: UsageScope, *, prompt: int, completion: int, model: str) -> None:
    """LLM spend, as the provider reported it.

    `MEASURED` and not approximated: the count is the provider's own, not an
    estimate from the text. A provider that reports nothing reports zero, and a
    zero-token record is still written — "we spent nothing" and "we do not know
    what we spent" must not look identical, which is the rule
    `app.ai.usage.record` already keeps for the same reason.
    """
    scope.count(
        Meter.AI_TOKENS,
        int(prompt) + int(completion),
        method="prompt plus completion tokens as reported by the provider",
        model=model,
        prompt_tokens=int(prompt),
        completion_tokens=int(completion),
    )


def record_seat_time(scope: UsageScope, *, seconds: float, calls: int, tool: str = "") -> None:
    """CATIA seat occupancy. Approximated, and it says how — see the constant."""
    scope.count(
        Meter.CATIA_SEAT_SECONDS,
        Decimal(str(float(seconds))),
        method=CATIA_SEAT_METHOD,
        basis=Basis.APPROXIMATED,
        calls=int(calls),
        tool=tool,
    )


# ---------------------------------------------------------------------------
# Reading the ledger
# ---------------------------------------------------------------------------


def usage_units(
    db: Session, organisation_id: str, start: date, end: date
) -> dict[Meter, int]:
    """Exact integer totals per meter over `[start, end)`.

    Half-open, so consecutive periods do not double-count the boundary day, and
    integer, so the total is the same however the rows are ordered. `SUM` over a
    BIGINT column is exact on Postgres and on SQLite alike; a `Numeric` column
    would round-trip through a float on the second of those, which is exactly
    the trap this representation avoids.
    """
    rows = db.execute(
        select(UsageRecord.meter, func.sum(UsageRecord.quantity_units))
        .where(
            UsageRecord.organisation_id == organisation_id,
            UsageRecord.usage_date >= start,
            UsageRecord.usage_date < end,
        )
        .group_by(UsageRecord.meter)
    ).all()
    return {Meter(meter): int(total or 0) for meter, total in rows}


def usage_totals(
    db: Session, organisation_id: str, start: date, end: date
) -> dict[Meter, Decimal]:
    """The same totals in whole units, as `Decimal`. Never a float."""
    return {
        meter: Decimal(units) / Decimal(meter.scale)
        for meter, units in usage_units(db, organisation_id, start, end).items()
    }


def seal_period(
    db: Session, organisation_id: str, start: date, end: date
) -> list[UsageRollup]:
    """Aggregate `[start, end)` into one rollup per meter, idempotently.

    This is what would post to a billing provider (P8.1's "posts in aggregates")
    and the reason it stamps `UsageRecord.rollup_id` is that an aggregate has to
    be explodable: an invoice line nobody can turn back into the runs behind it
    is the defect this whole phase is built against. Re-sealing the same period
    recomputes rather than appends, so a retry after a failed post is safe.
    """
    totals = usage_units(db, organisation_id, start, end)
    existing = {
        rollup.meter: rollup
        for rollup in db.scalars(
            select(UsageRollup).where(
                UsageRollup.organisation_id == organisation_id,
                UsageRollup.period_start == start,
                UsageRollup.period_end == end,
            )
        )
    }

    sealed: list[UsageRollup] = []
    for meter, units in sorted(totals.items(), key=lambda pair: pair[0].value):
        count = (
            db.scalar(
                select(func.count())
                .select_from(UsageRecord)
                .where(
                    UsageRecord.organisation_id == organisation_id,
                    UsageRecord.meter == meter,
                    UsageRecord.usage_date >= start,
                    UsageRecord.usage_date < end,
                )
            )
            or 0
        )
        rollup = existing.get(meter)
        if rollup is None:
            rollup = UsageRollup(
                organisation_id=organisation_id,
                meter=meter,
                period_start=start,
                period_end=end,
            )
            db.add(rollup)
        rollup.quantity_units = units
        rollup.record_count = int(count)
        rollup.sealed_at = utcnow()
        sealed.append(rollup)

    db.flush()
    for rollup in sealed:
        db.execute(
            update(UsageRecord)
            .where(
                UsageRecord.organisation_id == organisation_id,
                UsageRecord.meter == rollup.meter,
                UsageRecord.usage_date >= start,
                UsageRecord.usage_date < end,
            )
            .values(rollup_id=rollup.id)
        )
    db.flush()
    return sealed


# ---------------------------------------------------------------------------
# Quotas (P8.3) — and where each limit came from
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanAllowance:
    """What a plan includes.

    **Every allowance here is `None`, and that is the honest state, not an
    oversight.** A price list is a product decision that has not been made, and
    inventing "the free tier gets 3,600 solver-seconds" would put a number
    somewhere an engineer would later read as settled — the fabrication
    Decision 3 exists to prevent, in the one place where it would be billed for.
    What *is* built and tested is the resolution order: a per-tenant override
    beats a plan allowance beats the global setting, and the envelope says per
    field which of the three answered. Filling these in later changes no code.
    """

    plan: Plan
    max_concurrent_simulations_per_user: int | None = None
    max_media_bytes: int | None = None
    ai_daily_token_budget: int | None = None
    #: Per-period allowance in each meter's scaled units. Empty for the same
    #: reason as the fields above.
    meter_allowances: Mapping[Meter, int] = _NO_ALLOWANCES


def _plan_meters(seconds: int, tokens: int, storage: int) -> Mapping[Meter, int]:
    """Scaled per-period allowances, skipping anything the operator left at 0.

    `0` means *unset*, not *none allowed*. An allowance of zero would refuse
    every request on that meter the moment the setting was introduced, which is
    the opposite of the intended default — so it is absent from the mapping,
    and `check_quota` reads an absent allowance as unlimited and says so.
    """
    scaled = {
        Meter.SOLVER_SECONDS: seconds * Meter.SOLVER_SECONDS.scale,
        Meter.AI_TOKENS: tokens * Meter.AI_TOKENS.scale,
        Meter.STORAGE_BYTES: storage * Meter.STORAGE_BYTES.scale,
    }
    return MappingProxyType({meter: units for meter, units in scaled.items() if units > 0})


def build_plans() -> Mapping[Plan, PlanAllowance]:
    """Plan allowances, from settings.

    **These are the operator's numbers, not Kryova's.** Decision 4 makes this
    product free and open: a self-hosted install has no price list at all and
    its operator decides what its own users may do, while a hosted one sets what
    it sells. Every default is 0 — unset — so a fresh deployment behaves exactly
    as it did before allowances existed, and nothing here is a figure this
    project is claiming.

    Enterprise is deliberately never bounded here: "custom quotas" is what the
    plan promises, and a custom quota is a per-tenant override on the billing
    account, which already outranks a plan.
    """
    return MappingProxyType(
        {
            Plan.FREE: PlanAllowance(
                plan=Plan.FREE,
                meter_allowances=_plan_meters(
                    settings.free_plan_solver_seconds,
                    settings.free_plan_ai_tokens,
                    settings.free_plan_storage_bytes,
                ),
            ),
            Plan.TEAM: PlanAllowance(
                plan=Plan.TEAM,
                meter_allowances=_plan_meters(
                    settings.team_plan_solver_seconds,
                    settings.team_plan_ai_tokens,
                    settings.team_plan_storage_bytes,
                ),
            ),
            Plan.ENTERPRISE: PlanAllowance(plan=Plan.ENTERPRISE),
        }
    )


#: Read through `plans()` rather than at import, so a test that changes a
#: setting sees the change. A module-level snapshot froze the values at the
#: first import and made every allowance test depend on collection order.
def plans() -> Mapping[Plan, PlanAllowance]:
    return build_plans()


class _LazyPlans(Mapping[Plan, PlanAllowance]):
    """`PLANS`, but read from settings at lookup rather than at import.

    A module-level dict froze the allowances at the first import of this module,
    so a test (or an operator reloading configuration) that changed a setting
    saw the old value — and which value depended on collection order. Reading
    through means the mapping is always the settings in force, and the name
    `PLANS` stays what every existing caller already uses.
    """

    def __getitem__(self, key: Plan) -> PlanAllowance:
        return build_plans()[key]

    def __iter__(self) -> Iterator[Plan]:
        return iter(build_plans())

    def __len__(self) -> int:
        return len(Plan)


#: Kept as a name because callers and tests read it. It is a *view* built on
#: demand; see `plans()`.
PLANS: Final[Mapping[Plan, PlanAllowance]] = _LazyPlans()

#: Said once, and printed by the quota surface, so nobody reads an unset
#: allowance as an unlimited one.
PROVISIONAL_PLANS = (
    "Plan allowances are set by whoever runs this Kryova, not by a Kryova price "
    "list. Any limit shown without one comes from a per-tenant override or from "
    "the global settings, and a meter with no allowance cannot run out."
)


@dataclass(frozen=True, slots=True)
class QuotaLimit:
    """One limit, and where the number came from."""

    name: str
    limit: int
    #: "tenant override", "plan" or "global settings". Never omitted: a limit
    #: presented without its source reads as policy when it is a default.
    source: str
    what: str


@dataclass(frozen=True, slots=True)
class QuotaEnvelope:
    """What a tenant may do, what it has, and where every number came from.

    P8.3 asks for "the honest envelope (what ran out, what it costs to continue,
    what remains free), never a bare 429". This is the data behind that sentence;
    the route renders it.
    """

    organisation_id: str
    plan: Plan
    limits: tuple[QuotaLimit, ...]
    credit_balance_minor: int
    currency: str
    #: Meters with no allowance set, so nothing can run out of them. Named
    #: rather than omitted, exactly as an unwired meter is.
    unlimited_meters: tuple[Meter, ...]
    note: str = PROVISIONAL_PLANS

    def limit_for(self, name: str) -> QuotaLimit | None:
        for limit in self.limits:
            if limit.name == name:
                return limit
        return None


_QUOTA_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    (
        "max_concurrent_simulations_per_user",
        "queued or running simulations one user may hold at once",
    ),
    ("max_media_bytes", "largest single blob the store will accept, in bytes"),
    ("ai_daily_token_budget", "tokens one user may spend per UTC day; 0 means unlimited"),
)


def billing_account(db: Session, organisation_id: str) -> BillingAccount | None:
    return db.scalar(
        select(BillingAccount).where(BillingAccount.organisation_id == organisation_id)
    )


def quota_envelope(db: Session, organisation_id: str) -> QuotaEnvelope:
    """Every limit in force for one tenant, with its provenance.

    This is the gap `app/api/routes/admin.py` names in its own docstring — "the
    quotas are the global settings in force, because per-tenant quota rows do
    not exist yet". They exist now, so a limit can honestly say *which* of the
    three sources answered instead of the surface having to disclaim all of them
    at once.
    """
    account = billing_account(db, organisation_id)
    plan = account.plan if account is not None else Plan.FREE
    allowance = PLANS[plan]

    limits: list[QuotaLimit] = []
    for name, what in _QUOTA_FIELDS:
        override = getattr(account, name, None) if account is not None else None
        from_plan = getattr(allowance, name, None)
        if override is not None:
            limits.append(QuotaLimit(name, int(override), "tenant override", what))
        elif from_plan is not None:
            limits.append(QuotaLimit(name, int(from_plan), f"plan {plan.value}", what))
        else:
            limits.append(
                QuotaLimit(name, int(getattr(settings, name)), "global settings", what)
            )

    return QuotaEnvelope(
        organisation_id=organisation_id,
        plan=plan,
        limits=tuple(limits),
        credit_balance_minor=account.credit_balance_minor if account is not None else 0,
        currency=account.currency if account is not None else "usd",
        unlimited_meters=tuple(
            meter for meter in Meter if meter not in allowance.meter_allowances
        ),
    )


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """Whether more of a meter may be spent, and the envelope either way.

    `allowed` is `True` when no allowance is set, and the `because` says so
    rather than leaving a caller to infer permission from silence — a quota
    system that denied whatever it had no policy for would stop the product the
    day it was switched on.
    """

    meter: Meter
    allowed: bool
    because: str
    used_units: int
    allowance_units: int | None
    envelope: QuotaEnvelope

    @property
    def remaining_units(self) -> int | None:
        if self.allowance_units is None:
            return None
        return max(0, self.allowance_units - self.used_units)

    def detail(self) -> dict[str, Any]:
        """The body of a refusal, in P8.3's register: never a bare number."""
        remaining = self.remaining_units
        return {
            "meter": str(self.meter),
            "unit": self.meter.unit,
            "allowed": self.allowed,
            "reason": self.because,
            "used": str(Decimal(self.used_units) / Decimal(self.meter.scale)),
            "allowance": (
                None
                if self.allowance_units is None
                else str(Decimal(self.allowance_units) / Decimal(self.meter.scale))
            ),
            "remaining": (
                None if remaining is None else str(Decimal(remaining) / Decimal(self.meter.scale))
            ),
            "credit_balance_minor": self.envelope.credit_balance_minor,
            "currency": self.envelope.currency,
            "plan": str(self.envelope.plan),
            "note": self.envelope.note,
        }


def check_quota(
    db: Session,
    organisation_id: str,
    meter: Meter,
    start: date,
    end: date,
    *,
    additional_units: int = 0,
) -> QuotaDecision:
    """May this tenant spend more of `meter` in `[start, end)`?"""
    envelope = quota_envelope(db, organisation_id)
    allowance = PLANS[envelope.plan].meter_allowances.get(meter)
    used = usage_units(db, organisation_id, start, end).get(meter, 0)
    if allowance is None:
        return QuotaDecision(
            meter=meter,
            allowed=True,
            because=(
                f"No allowance is set for {meter.value} on plan {envelope.plan.value}, "
                "so nothing can run out of it. " + PROVISIONAL_PLANS
            ),
            used_units=used,
            allowance_units=None,
            envelope=envelope,
        )
    projected = used + max(0, additional_units)
    allowed = projected <= allowance
    return QuotaDecision(
        meter=meter,
        allowed=allowed,
        because=(
            f"{projected / meter.scale:g} {meter.unit} of the "
            f"{allowance / meter.scale:g} {meter.unit} included in plan "
            f"{envelope.plan.value} for this period."
        ),
        used_units=used,
        allowance_units=allowance,
        envelope=envelope,
    )


# ---------------------------------------------------------------------------
# Storage attribution — the gap the admin console names
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StorageAttribution:
    """Bytes a tenant is responsible for, and the bytes nobody can place.

    `app/api/routes/admin.py` attributes storage "through the *owner* of a media
    row, because media rows carry no organisation, so a member of two
    organisations has their bytes counted against both". That is a real
    double-count and it does not need a schema change to fix: every blob that
    belongs to work is reachable from a project through the row that references
    it, and a project has a tenant. What is left over — a blob no geometry
    version, simulation or checkpoint points at — is reported as *unplaced*
    rather than folded into the total, because a number that quietly absorbs
    what it could not explain is the thing this codebase does not do.
    """

    organisation_id: str
    bytes_attributed: int
    media_rows: int
    method: str
    unplaced_bytes: int
    unplaced_rows: int
    unplaced_reason: str


#: Spelled once, and printed with the number.
STORAGE_METHOD: Final = (
    "summed over the distinct blobs reachable from this organisation's projects "
    "— geometry versions, simulation result fields and CATIA checkpoints. A blob "
    "shared by two rows is counted once, matching what the content-addressed "
    "store actually holds."
)

UNPLACED_REASON: Final = (
    "Blobs owned by a member of this organisation that no geometry version, "
    "simulation or checkpoint in any project references, so nothing says which "
    "tenant they belong to. They are excluded from the attributed total rather "
    "than assumed to be this one's."
)


def _placed_media_select(organisation_id: str | None):
    """Media ids reachable from a project, optionally one tenant's.

    Three linkages, and they are the three that exist: `GeometryVersion.media_id`
    (the uploaded CAD), `SimulationJob.fields_media_id` (the result fields) and
    `CatiaCheckpoint.media_id` (a document snapshot, reached through the
    conversation's project). Imported locally because `app.core` must not carry
    a module-level dependency on every model package in the service.
    """
    from app.models.catia import CatiaCheckpoint, CatiaDocument
    from app.models.conversation import Conversation
    from app.models.geometry import GeometryVersion
    from app.models.project import Project
    from app.models.simulation import SimulationJob

    def scoped(statement, project_column):
        statement = statement.join(Project, Project.id == project_column)
        if organisation_id is not None:
            statement = statement.where(Project.organisation_id == organisation_id)
        return statement

    geometry = scoped(
        select(GeometryVersion.media_id.label("media_id")).where(
            GeometryVersion.media_id.is_not(None)
        ),
        GeometryVersion.project_id,
    )
    fields = scoped(
        select(SimulationJob.fields_media_id.label("media_id")).where(
            SimulationJob.fields_media_id.is_not(None)
        ),
        SimulationJob.project_id,
    )
    checkpoints = scoped(
        select(CatiaCheckpoint.media_id.label("media_id"))
        .join(CatiaDocument, CatiaDocument.id == CatiaCheckpoint.document_id)
        .join(Conversation, Conversation.id == CatiaDocument.conversation_id)
        .where(CatiaCheckpoint.media_id.is_not(None)),
        Conversation.project_id,
    )
    return union(geometry, fields, checkpoints)


def storage_attribution(db: Session, organisation_id: str) -> StorageAttribution:
    """Bytes this tenant holds, counted exactly, with the residual named."""
    from app.models.media import Media
    from app.models.organisation import Membership

    placed = _placed_media_select(organisation_id).subquery()
    total, rows = db.execute(
        select(func.coalesce(func.sum(Media.size_bytes), 0), func.count()).where(
            Media.id.in_(select(placed.c.media_id))
        )
    ).one()

    member_ids = list(
        db.scalars(select(Membership.user_id).where(Membership.organisation_id == organisation_id))
    )
    unplaced_bytes, unplaced_rows = 0, 0
    if member_ids:
        anywhere = _placed_media_select(None).subquery()
        unplaced_bytes, unplaced_rows = db.execute(
            select(func.coalesce(func.sum(Media.size_bytes), 0), func.count()).where(
                Media.owner_id.in_(member_ids),
                Media.id.not_in(select(anywhere.c.media_id)),
            )
        ).one()

    return StorageAttribution(
        organisation_id=organisation_id,
        bytes_attributed=int(total or 0),
        media_rows=int(rows or 0),
        method=STORAGE_METHOD,
        unplaced_bytes=int(unplaced_bytes or 0),
        unplaced_rows=int(unplaced_rows or 0),
        unplaced_reason=UNPLACED_REASON,
    )


__all__ = [
    "CATIA_SEAT_METHOD",
    "METERING",
    "METER_SITES",
    "PLANS",
    "PROVISIONAL_PLANS",
    "SPAN_METERS",
    "STORAGE_METHOD",
    "UNPLACED_REASON",
    "Cause",
    "CollectingSink",
    "Gap",
    "LedgerSink",
    "MeterSink",
    "MeterSite",
    "MeteringMonitor",
    "MeteringSnapshot",
    "NullSink",
    "PlanAllowance",
    "QuotaDecision",
    "QuotaEnvelope",
    "QuotaLimit",
    "SpanMeter",
    "StorageAttribution",
    "UsageEvent",
    "UsageScope",
    "billing_account",
    "check_quota",
    "current_scope",
    "describe_failure",
    "emit_safely",
    "quota_envelope",
    "record_seat_time",
    "record_storage",
    "record_tokens",
    "seal_period",
    "storage_attribution",
    "to_record",
    "unwired_meters",
    "usage_scope",
    "usage_totals",
    "usage_units",
]
