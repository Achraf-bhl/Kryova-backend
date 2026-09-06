"""The instrument itself: cheap when off, honest when on.

Two properties are load-bearing and both are pinned here.

**Off has to be nearly free.** An instrument that costs 5% is one somebody turns
off, and instrumentation that is off is worse than none — the call sites are
still there, and nobody trusts them. The disabled path is checked structurally
(`span()` returns the one shared inert object, so nothing is allocated and no
clock is read) *and* by measurement, with an absolute per-call ceiling. The
structural check is the one that cannot go flaky; the timing one is the one that
would catch somebody making the disabled path allocate.

**A span that failed is still a span.** The nine-minute solve that blew up is
exactly the one whose timing somebody needs, and a recorder that kept only the
successes would make the arithmetic on what is left look better than the system
is. Every failure route — an exception, an explicit `fail()`, a generator
abandoned mid-stream — records.

Offline: no database, no network, no kernel.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.observe import (
    INERT,
    Distribution,
    Span,
    Summary,
    collect,
    current_span,
    is_collecting,
    span,
)
from app.observe.collect import Recorder

#: Ceiling on what one disabled span may cost, measured as the difference
#: against an empty loop. Generous by two orders of magnitude against the real
#: figure (~0.2 microseconds), because this runs on a laptop under a test
#: runner: it is here to catch a disabled path that started allocating records
#: or reading a clock, not to police jitter.
DISABLED_CEILING_SECONDS = 5e-6

ITERATIONS = 100_000


class TestDisabledIsFree:
    def test_nothing_is_collecting_by_default(self) -> None:
        assert not is_collecting()

    def test_a_disabled_span_is_the_shared_inert_object(self) -> None:
        """The structural half of 'near-free': no allocation at all.

        If this ever returns something new per call, the disabled path is
        building an object per instrumented operation and the timing ceiling
        below is the only thing left guarding it.
        """
        assert span("mesh.gmsh.session") is INERT
        assert span("mesh.gmsh.session", nodes=1_200_000) is INERT
        assert span("mesh.gmsh.session") is span("media.write")

    def test_the_inert_span_supports_everything_a_call_site_uses(self) -> None:
        with span("media.write", bytes=1) as timing:
            timing.set("bytes", 2)
            timing.add("chunks", 1)
            timing.fail("nothing should happen")

    def test_a_disabled_span_costs_under_the_ceiling(self) -> None:
        def empty() -> None:
            for _ in range(ITERATIONS):
                pass

        def instrumented() -> None:
            for _ in range(ITERATIONS):
                with span("media.write", bytes=1):
                    pass

        # Warm both paths so neither pays for a first-call import or a cold
        # branch predictor, then take the best of three: the minimum is the
        # measurement least polluted by whatever else the machine was doing.
        empty()
        instrumented()
        baseline = min(_time(empty) for _ in range(3))
        measured = min(_time(instrumented) for _ in range(3))
        per_span = (measured - baseline) / ITERATIONS
        assert per_span < DISABLED_CEILING_SECONDS, f"{per_span * 1e6:.3f} us per disabled span"

    def test_a_disabled_span_records_nothing_anywhere(self) -> None:
        with span("media.write"):
            pass
        with collect() as recorder:
            pass
        assert recorder.spans == ()


class TestRecording:
    def test_a_span_records_its_name_duration_and_fields(self) -> None:
        with collect() as recorder:
            with span("media.write", deduplicated=False) as timing:
                timing.add("bytes", 40)
                timing.add("bytes", 60)
                timing.set("deduplicated", True)
                time.sleep(0.005)

        (recorded,) = recorder.spans
        assert recorded.name == "media.write"
        assert recorded.ok and recorded.failure == ""
        assert recorded.seconds >= 0.004
        assert recorded.fields["bytes"] == 100
        assert recorded.fields["deduplicated"] is True

    def test_fields_are_frozen_on_the_recorded_span(self) -> None:
        with collect() as recorder:
            with span("media.write") as timing:
                timing.set("bytes", 1)
        (recorded,) = recorder.spans
        with pytest.raises(TypeError):
            recorded.fields["bytes"] = 2  # type: ignore[index]

    def test_nesting_records_depth_and_parent(self) -> None:
        with collect() as recorder:
            assert current_span() is None
            with span("mesh.gmsh.wait"):
                with span("mesh.gmsh.session") as inner:
                    assert current_span() is inner
            assert current_span() is None

        by_name = {s.name: s for s in recorder.spans}
        assert by_name["mesh.gmsh.wait"].depth == 0
        assert by_name["mesh.gmsh.wait"].parent is None
        assert by_name["mesh.gmsh.session"].depth == 1
        assert by_name["mesh.gmsh.session"].parent == "mesh.gmsh.wait"

    def test_collection_restores_the_previous_state(self) -> None:
        with collect():
            assert is_collecting()
        assert not is_collecting()

    def test_a_nested_collection_restores_the_outer_one(self) -> None:
        with collect() as outer:
            with collect() as inner:
                with span("media.write"):
                    pass
            with span("media.read"):
                pass
        assert [s.name for s in inner.spans] == ["media.write"]
        assert [s.name for s in outer.spans] == ["media.read"]

    def test_a_span_from_a_worker_thread_lands(self) -> None:
        """Spans must not need a context to be propagated onto a worker.

        This is why collection is a module global and not a ContextVar: the
        expensive work in this system happens on job threads, and
        `ThreadPoolExecutor` does not carry a context across `submit`.
        """
        with collect() as recorder:
            worker = threading.Thread(target=_span_in_a_thread, name="probe")
            worker.start()
            worker.join()

        (recorded,) = recorder.spans
        assert recorded.thread == "probe"
        assert recorded.depth == 0 and recorded.parent is None


class TestFailuresAreRecorded:
    def test_an_exception_is_recorded_and_re_raised(self) -> None:
        with collect() as recorder:
            with pytest.raises(ValueError):
                with span("media.write"):
                    raise ValueError("no space left on device")

        (recorded,) = recorder.spans
        assert recorded.ok is False
        assert recorded.failure == "ValueError: no space left on device"

    def test_a_failure_message_is_bounded_and_flattened(self) -> None:
        with collect() as recorder:
            with pytest.raises(RuntimeError):
                with span("media.write"):
                    raise RuntimeError("line one\n\n   line two" + "x" * 1000)

        (recorded,) = recorder.spans
        assert "\n" not in recorded.failure
        assert len(recorded.failure) < 400

    def test_an_exception_with_no_message_still_names_its_type(self) -> None:
        with collect() as recorder:
            with pytest.raises(KeyError):
                with span("media.read"):
                    raise KeyError()
        (recorded,) = recorder.spans
        assert recorded.failure.startswith("KeyError")

    def test_fail_marks_a_span_that_never_raised(self) -> None:
        """For the operations that report failure by returning.

        `ccx` prints `*ERROR` and exits 0; a span that trusted the absence of an
        exception would count that run as a success.
        """
        with collect() as recorder:
            with span("solve.calculix.run") as timing:
                timing.fail("*ERROR in e_c3d: nonpositive jacobian")

        (recorded,) = recorder.spans
        assert recorded.ok is False
        assert "nonpositive jacobian" in recorded.failure

    def test_an_abandoned_generator_is_recorded_as_a_partial_read(self) -> None:
        def stream():
            with span("media.read") as timing:
                for i in range(10):
                    timing.add("bytes", 10)
                    yield i

        with collect() as recorder:
            reader = stream()
            next(reader)
            next(reader)
            reader.close()

        (recorded,) = recorder.spans
        assert recorded.ok is False
        assert "GeneratorExit" in recorded.failure
        assert recorded.fields["bytes"] == 20


class TestTheRecorderIsHonestWhenFull:
    def test_spans_past_the_ceiling_are_counted_not_kept(self) -> None:
        with collect(max_spans=3) as recorder:
            for _ in range(10):
                with span("media.write"):
                    pass

        assert len(recorder.spans) == 3
        assert recorder.dropped == {"media.write": 7}

    def test_a_ceiling_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            Recorder(max_spans=0)

    def test_an_open_span_is_reported_as_unfinished(self) -> None:
        with collect() as recorder:
            with span("mesh.gmsh.session"):
                assert recorder.unfinished == ("mesh.gmsh.session",)
                assert recorder.spans == ()
            assert recorder.unfinished == ()

    def test_the_window_is_measured_and_stops_when_collection_stops(self) -> None:
        with collect() as recorder:
            time.sleep(0.01)
        first = recorder.seconds
        time.sleep(0.01)
        assert recorder.seconds == first >= 0.01


class TestDistribution:
    def test_an_empty_sample_is_refused_rather_than_zeroed(self) -> None:
        """A `min_seconds` of 0.0 computed from nothing reads as 'instant'."""
        with pytest.raises(ValueError, match="at least one sample"):
            Distribution.of([])

    def test_the_percentile_is_a_nearest_rank_order_statistic(self) -> None:
        values = [float(i) for i in range(1, 101)]
        dist = Distribution.of(values)
        assert dist.count == 100
        assert dist.min_seconds == 1.0
        assert dist.max_seconds == 100.0
        assert dist.median_seconds == 50.0
        assert dist.p95_seconds == 95.0
        assert dist.mean_seconds == pytest.approx(50.5)
        assert not dist.p95_is_the_maximum

    def test_a_small_sample_says_its_p95_is_only_the_maximum(self) -> None:
        dist = Distribution.of([1.0, 2.0, 3.0])
        assert dist.p95_seconds == dist.max_seconds
        assert dist.p95_is_the_maximum


class TestSummary:
    def test_numeric_fields_are_summed_and_labels_are_not(self) -> None:
        spans = [
            Span(name="media.write", seconds=1.0, fields={"bytes": 10, "queue": "threadpool"}),
            Span(name="media.write", seconds=3.0, fields={"bytes": 5, "deduplicated": True}),
        ]
        summary = Summary.of("media.write", spans)
        assert summary.totals == {"bytes": 15.0}
        assert summary.count == 2
        assert summary.durations.total_seconds == 4.0

    def test_failures_and_the_success_rate_are_counted(self) -> None:
        spans = [
            Span(name="jobs.run", seconds=1.0),
            Span(name="jobs.run", seconds=1.0, ok=False, failure="boom"),
        ]
        summary = Summary.of("jobs.run", spans)
        assert summary.failures == 1
        assert summary.successes == 1
        assert summary.success_rate == 0.5

    def test_dropped_spans_mark_the_summary_incomplete(self) -> None:
        summary = Summary.of("media.write", [Span(name="media.write", seconds=1.0)], dropped=4)
        assert summary.incomplete and summary.dropped == 4

    def test_a_negative_duration_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Span(name="media.write", seconds=-0.1)


def _span_in_a_thread() -> None:
    with span("media.write"):
        pass


def _time(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start
