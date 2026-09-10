"""The meter itself: exact, attributed, and unable to fail the work (P8.1).

Offline — no database, no network, no kernel call. Everything here is about the
three properties that make a bill arguable rather than assertable:

**Exact.** A counted unit is an integer and a total is an integer sum. The float
path is refused at construction rather than tolerated, and one test does the
arithmetic both ways so the refusal is visibly buying something.

**Attributed.** A span is a duration with no tenant on it. What binds one to an
organisation is the scope open around it, and a span that finishes outside every
scope is counted as unattributed rather than guessed at.

**Unable to fail the work.** Every path that could raise inside metering is
provoked here — a sink that throws, a listener handed a broken scope, a
multiplier nobody supplied — and in each case the block completes and the
failure is visible in `METERING.snapshot()`.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from app.core.metering import (
    BY_SPAN,
    METER_SITES,
    METERING,
    PLANS,
    SPAN_METERS,
    Cause,
    CollectingSink,
    UsageEvent,
    UsageScope,
    emit_safely,
    record_storage,
    record_tokens,
    unwired_meters,
    usage_scope,
)
from app.kernel.provenance import Basis
from app.models.billing import Meter, Plan
from app.observe import INERT, Span, span
from app.observe.catalogue import BY_NAME as SPAN_CATALOGUE


@pytest.fixture(autouse=True)
def _fresh_monitor():
    """The monitor is process-wide, exactly as `app.observe.queue.METER` is."""
    METERING.reset()
    yield
    METERING.reset()


def a_cause(**overrides) -> Cause:
    fields = {
        "organisation_id": "org-1",
        "source": "simulation.runner",
        "subject_type": "simulation_job",
        "subject_id": "job-1",
    }
    fields.update(overrides)
    return Cause(**fields)


class TestQuantitiesAreExactAndNeverFloat:
    def test_a_float_quantity_is_refused_rather_than_converted(self) -> None:
        """Break the guard by handing it exactly what it exists to refuse."""
        with pytest.raises(TypeError, match="float"):
            UsageEvent.of(
                Meter.SOLVER_SECONDS,
                1.5,  # type: ignore[arg-type]
                basis=Basis.MEASURED,
                method="x",
                cause=a_cause(),
            )

    def test_a_bool_is_not_an_integer_quantity(self) -> None:
        """`isinstance(True, int)` is True, so this needs its own refusal."""
        with pytest.raises(TypeError):
            UsageEvent.of(
                Meter.AI_TOKENS,
                True,  # type: ignore[arg-type]
                basis=Basis.MEASURED,
                method="x",
                cause=a_cause(),
            )

    def test_seconds_become_an_exact_microsecond_count(self) -> None:
        event = UsageEvent.from_seconds(
            Meter.SOLVER_SECONDS, 0.1, basis=Basis.MEASURED, method="clock", cause=a_cause()
        )
        assert event.quantity_units == 100_000
        assert event.quantity == Decimal("0.1")

    def test_a_total_of_ten_tenths_is_one_second_and_a_float_sum_is_not(self) -> None:
        """The whole argument for integers, done both ways in one test.

        `0.1` is not a tenth in binary, so accumulating ten of them lands on
        0.9999999999999999 — a total that changes with the order of the rows and
        that somebody will eventually quote in an argument about an invoice.
        (Written as an explicit `+=` rather than `sum()`, which since CPython
        3.12 compensates and would hide the very thing being shown.)
        """
        events = [
            UsageEvent.from_seconds(
                Meter.SOLVER_SECONDS, 0.1, basis=Basis.MEASURED, method="clock", cause=a_cause()
            )
            for _ in range(10)
        ]
        units = sum(event.quantity_units for event in events)
        assert Decimal(units) / Decimal(Meter.SOLVER_SECONDS.scale) == Decimal("1")

        drifting = 0.0
        for _ in range(10):
            drifting += 0.1
        assert drifting != 1.0

    def test_a_decimal_quantity_survives_the_round_trip(self) -> None:
        event = UsageEvent.of(
            Meter.MESH_ELEMENT_SECONDS,
            Decimal("12.345"),
            basis=Basis.MEASURED,
            method="x",
            cause=a_cause(),
        )
        assert event.quantity_units == 12_345
        assert event.quantity == Decimal("12.345")

    def test_a_negative_quantity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            UsageEvent.of(
                Meter.AI_TOKENS, -1, basis=Basis.MEASURED, method="x", cause=a_cause()
            )

    def test_an_unavailable_quantity_is_a_gap_and_not_a_record(self) -> None:
        with pytest.raises(ValueError, match="not a usage record"):
            UsageEvent.of(
                Meter.AI_TOKENS,
                0,
                basis=Basis.UNAVAILABLE,
                method="x",
                cause=a_cause(),
            )

    def test_a_quantity_with_no_method_is_refused(self) -> None:
        with pytest.raises(ValueError, match="how it was arrived at"):
            UsageEvent.of(
                Meter.AI_TOKENS, 10, basis=Basis.MEASURED, method="   ", cause=a_cause()
            )


class TestACauseCannotBeBlank:
    @pytest.mark.parametrize(
        "blank", ["organisation_id", "source", "subject_type", "subject_id"]
    )
    def test_every_binding_field_is_required(self, blank: str) -> None:
        """Break the binding one field at a time; each has to cost the record."""
        with pytest.raises(ValueError, match=blank):
            a_cause(**{blank: "  "})

    def test_detail_is_carried_without_mutating_the_original(self) -> None:
        cause = a_cause()
        richer = cause.with_detail(solver="internal")
        assert richer.detail["solver"] == "internal"
        assert "solver" not in cause.detail


class TestTheMapIsHonestAboutWhatItCovers:
    def test_every_metered_span_is_one_the_system_can_actually_emit(self) -> None:
        """A meter keyed on a span nothing emits is silently always zero.

        The same failure `app.observe.catalogue` exists to prevent for timings:
        "nothing spent this" and "nothing measures this" must not be the same
        silence.
        """
        for entry in SPAN_METERS:
            assert entry.span in SPAN_CATALOGUE, entry.span
            assert SPAN_CATALOGUE[entry.span].wired, entry.span

    def test_every_meter_declares_where_its_numbers_come_from(self) -> None:
        assert {site.meter for site in METER_SITES} == set(Meter)

    def test_every_meter_is_wired_and_says_how(self) -> None:
        """Replaces `test_an_unwired_meter_names_what_wiring_it_would_take`.

        That test asserted at least one meter was unwired and said in its own
        body: "if every meter is wired, delete this test rather than weaken it".
        P8.1 wired the last three on 2026-09-10 (AI tokens, CATIA seat time,
        kernel operations), so it was deleted rather than loosened — and this
        is the claim that outlives the state: every meter declares where its
        numbers come from, and an unwired one still has to give a real reason.
        """
        assert unwired_meters() == ()
        for site in METER_SITES:
            assert site.how.strip(), site.meter

    def test_an_unwired_meter_would_still_have_to_explain_itself(self) -> None:
        # The guard that made the deleted test worth having, kept at the level
        # it actually lives: a `MeterSite` refuses to be unwired silently.
        from app.core.metering import MeterSite

        with pytest.raises(ValueError, match="no reason given"):
            MeterSite(meter=Meter.AI_TOKENS, how="x", wired=False)
        with pytest.raises(ValueError, match="needs no excuse"):
            MeterSite(meter=Meter.AI_TOKENS, how="x", not_wired_because="y")

    def test_a_span_meter_cannot_be_both_a_count_and_a_scaled_duration(self) -> None:
        from app.core.metering import SpanMeter

        with pytest.raises(ValueError, match="pick which the meter counts"):
            SpanMeter(
                span="kernel.rebuild",
                meter=Meter.KERNEL_OPERATIONS,
                method="x",
                from_field="operations",
                times_field="elements",
            )

    def test_no_plan_pretends_to_an_allowance_nobody_priced(self) -> None:
        """Decision 3 at the till: an invented limit reads as settled policy."""
        for plan in Plan:
            allowance = PLANS[plan]
            assert allowance.max_concurrent_simulations_per_user is None
            assert allowance.meter_allowances == {}


class TestASpanBecomesAMeteredQuantity:
    def test_a_solver_span_inside_a_scope_is_attributed_to_its_cause(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(project_id="p1"), sink):
            with span("solve.linear_static", nodes=10):
                time.sleep(0.01)

        assert len(sink.events) == 1
        event = sink.events[0]
        assert event.meter is Meter.SOLVER_SECONDS
        assert event.quantity > Decimal("0")
        assert event.cause.subject_id == "job-1"
        assert event.cause.project_id == "p1"
        assert event.cause.detail["spans"]["solve.linear_static"]["count"] == 1

    def test_two_solvers_feed_one_meter_and_the_ledger_still_says_which(self) -> None:
        """P8.1: "a CalculiX nonlinear minute is not a linear-static second"."""
        sink = CollectingSink()
        with usage_scope(a_cause(), sink):
            with span("solve.linear_static"):
                pass
            with span("solve.calculix.run"):
                pass

        (event,) = [e for e in sink.events if e.meter is Meter.SOLVER_SECONDS]
        assert set(event.cause.detail["spans"]) == {
            "solve.linear_static",
            "solve.calculix.run",
        }

    def test_element_seconds_multiply_the_duration_by_the_annotated_count(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink) as usage:
            usage.annotate(elements=1_000)
            with span("mesh.gmsh.session"):
                time.sleep(0.01)

        (event,) = [e for e in sink.events if e.meter is Meter.MESH_ELEMENT_SECONDS]
        seconds = Decimal(str(event.cause.detail["spans"]["mesh.gmsh.session"]["seconds"]))
        # Compared with Decimal arithmetic, not `pytest.approx`: approx computes
        # its tolerance as a float times the expected value and cannot take a
        # Decimal at all, which is a small illustration of the wider point.
        assert abs(event.quantity - seconds * 1_000) < Decimal("0.01")

    def test_a_missing_multiplier_is_a_gap_and_never_a_zero(self) -> None:
        """Break it by not annotating: the meter must refuse, not report nothing.

        A zero here would tell a customer their meshing cost nothing. The rule
        is `app.design.assertions`': a measurement nobody took is UNMEASURED,
        never a pass — and on a bill, never a zero.
        """
        sink = CollectingSink()
        with usage_scope(a_cause(), sink) as usage:
            with span("mesh.gmsh.session"):
                time.sleep(0.005)
            scope = usage

        assert [e for e in sink.events if e.meter is Meter.MESH_ELEMENT_SECONDS] == []
        (gap,) = scope.gaps
        assert gap.meter is Meter.MESH_ELEMENT_SECONDS
        assert "multiplier" in gap.reason

    def test_an_operation_count_comes_from_the_span_field_not_the_clock(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink):
            with span("kernel.rebuild", operations=42):
                time.sleep(0.005)

        (event,) = [e for e in sink.events if e.meter is Meter.KERNEL_OPERATIONS]
        assert event.quantity == Decimal(42)

    def test_a_failed_run_is_still_billed_for_what_it_consumed(self) -> None:
        """The nine-minute solve that blew up is the one somebody needs costed."""
        sink = CollectingSink()
        with pytest.raises(RuntimeError):
            with usage_scope(a_cause(), sink):
                with span("solve.linear_static"):
                    time.sleep(0.005)
                raise RuntimeError("the solve fell over afterwards")

        assert [e.meter for e in sink.events] == [Meter.SOLVER_SECONDS]

    def test_a_span_outside_every_scope_is_counted_not_guessed_at(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink):
            pass
        # The scope is closed; nothing may attribute this to the tenant above.
        with span("solve.linear_static"):
            pass
        assert sink.events == []

    def test_the_disabled_path_is_untouched_when_nothing_is_metering(self) -> None:
        """The property `tests/test_observe.py` pins, re-checked from this side.

        The listener seam this phase added to `app.observe` must cost nothing
        when no scope is open, or every instrumented call site in the service
        pays for billing that is not happening.
        """
        assert span("solve.linear_static") is INERT
        with usage_scope(a_cause(), CollectingSink()):
            assert span("solve.linear_static") is not INERT
        assert span("solve.linear_static") is INERT

    def test_an_unrelated_span_is_ignored_rather_than_billed(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink):
            with span("media.read", bytes=10):
                pass
        assert sink.events == []


class TestMeteringNeverFailsTheWorkAndNeverFailsQuietly:
    def test_a_sink_that_raises_does_not_escape_the_scope(self) -> None:
        class BrokenSink:
            def emit(self, events) -> None:
                raise RuntimeError("the ledger is on fire")

        finished = False
        with usage_scope(a_cause(), BrokenSink()):
            with span("solve.linear_static"):
                pass
            finished = True

        assert finished
        snapshot = METERING.snapshot()
        assert snapshot.events_failed >= 1
        assert not snapshot.healthy
        assert any("RuntimeError" in fault for fault in snapshot.faults)

    def test_the_failure_is_counted_rather_than_swallowed_whole(self) -> None:
        """Break the guard the other way: prove silence is not the outcome.

        This is the lesson of `QueueMeter._finished` shadowing its own counter
        on 2026-09-06 — the exception died unread inside a `Future` and no job
        was counted for as long as nobody looked. A swallowed metering bug is a
        metering bug that ships.
        """

        class BrokenSink:
            def emit(self, events) -> None:
                raise ValueError("nope")

        before = METERING.snapshot()
        emit_safely(
            BrokenSink(),
            [
                UsageEvent.of(
                    Meter.AI_TOKENS, 10, basis=Basis.MEASURED, method="x", cause=a_cause()
                )
            ],
        )
        after = METERING.snapshot()
        assert after.events_failed == before.events_failed + 1
        assert after.last_fault_at is not None
        assert after.faults[-1].startswith("ValueError")

    def test_a_listener_failure_cannot_break_the_instrumented_block(self) -> None:
        """`app.observe` calls listeners unguarded on purpose; metering guards.

        Broken deliberately by handing the scope a span object that is not a
        span, which is what a future refactor of `app.observe.records` would do
        by accident.
        """
        from app.core import metering

        class ExplodingScope(UsageScope):
            def observe(self, finished: Span) -> None:
                raise TypeError("not a span")

        sink = CollectingSink()
        scope = ExplodingScope(a_cause())
        token = metering._ACTIVE.set(scope)
        metering._listen()
        try:
            with span("solve.linear_static"):
                pass
        finally:
            metering._ACTIVE.reset(token)
            metering._stop_listening()

        assert METERING.snapshot().events_failed >= 1
        assert sink.events == []

    def test_an_exception_in_the_body_still_reaches_the_caller(self) -> None:
        with pytest.raises(ZeroDivisionError):
            with usage_scope(a_cause(), CollectingSink()):
                1 / 0

    def test_the_scope_stops_listening_even_when_the_body_raises(self) -> None:
        from app.core import metering

        with pytest.raises(RuntimeError):
            with usage_scope(a_cause(), CollectingSink()):
                raise RuntimeError("boom")
        assert metering._OPEN_SCOPES == 0
        assert span("solve.linear_static") is INERT


class TestTheMetersNothingObserves:
    def test_storage_records_the_bytes_and_whether_they_were_deduplicated(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink) as usage:
            record_storage(usage, size_bytes=4_096, media_id="m1", sha256="ab", deduplicated=True)

        (event,) = sink.events
        assert event.meter is Meter.STORAGE_BYTES
        assert event.quantity == Decimal(4_096)
        assert event.cause.detail["deduplicated"] is True

    def test_tokens_are_measured_because_the_provider_counted_them(self) -> None:
        sink = CollectingSink()
        with usage_scope(a_cause(), sink) as usage:
            record_tokens(usage, prompt=100, completion=25, model="qwen3-coder:30b")

        (event,) = sink.events
        assert event.quantity == Decimal(125)
        assert event.basis is Basis.MEASURED

    def test_seat_time_says_it_is_a_lower_bound(self) -> None:
        from app.core.metering import CATIA_SEAT_METHOD, record_seat_time

        sink = CollectingSink()
        with usage_scope(a_cause(), sink) as usage:
            record_seat_time(usage, seconds=12.5, calls=4, tool="catia_pad")

        (event,) = sink.events
        assert event.basis is Basis.APPROXIMATED
        assert event.method == CATIA_SEAT_METHOD
        assert "lower bound" in event.method

    def test_every_span_meter_name_is_unique(self) -> None:
        assert len(BY_SPAN) == len(SPAN_METERS)
