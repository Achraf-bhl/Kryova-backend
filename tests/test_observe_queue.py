"""The queue as numbers, and the three hooks this change installed.

`app/jobs/`, `app/media/` and `app/mesh/gmsh_session.py` each gained a call into
`app.observe`. These tests are the other half of that: they check the hook
records the right thing, and — for the gmsh one, which changed a `with` into an
explicit acquire/release — that the thing being instrumented still behaves.

The queue meter is deliberately always on, so every test here resets it first.
That is the one piece of process-wide mutable state this package owns and it is
worth naming: `METER.reset()` exists for tests and for nothing else.

Offline: no database, no network. `gmsh` is imported (it is a project
dependency, not an optional one) and the session is opened and closed.
"""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path

import pytest

from app.jobs import InlineJobQueue, ThreadPoolJobQueue
from app.media.store import LocalMediaStore, MediaNotFound, MediaTooLarge
from app.observe import METER, collect
from app.observe.queue import QueueMeter


@pytest.fixture(autouse=True)
def _clean_meter():
    METER.reset()
    yield
    METER.reset()


class TestTheMeter:
    def test_a_submitted_job_is_pending_and_ages(self) -> None:
        """Depth is a number, and a job nobody picked up keeps getting older.

        This is the whole point: a pool shut down with work queued and two
        workers against a hundred solves both show up here as an age that
        climbs, where today they show up as nothing at all.
        """
        meter = QueueMeter()
        meter.submit("threadpool")
        time.sleep(0.02)
        snapshot = meter.snapshot()
        assert snapshot.submitted == 1
        assert snapshot.pending == 1 and snapshot.running == 0
        assert snapshot.oldest_pending_seconds is not None
        assert snapshot.oldest_pending_seconds >= 0.02
        assert snapshot.wait is None and snapshot.run is None
        assert snapshot.failure_rate is None

    def test_the_lifecycle_moves_a_job_through_pending_running_finished(self) -> None:
        meter = QueueMeter()
        ticket = meter.submit("threadpool")
        time.sleep(0.01)
        ticket.started()
        assert meter.snapshot().pending == 0
        assert meter.snapshot().running == 1
        assert meter.snapshot().longest_running_seconds is not None
        ticket.finished(ok=True)

        snapshot = meter.snapshot()
        assert (snapshot.pending, snapshot.running, snapshot.finished) == (0, 0, 1)
        assert snapshot.wait is not None and snapshot.wait.max_seconds >= 0.01
        assert snapshot.run is not None
        assert snapshot.failure_rate == 0.0

    def test_each_ticket_reports_its_own_wait_not_the_last_one_seen(self) -> None:
        """Two jobs with very different waits must not share a number."""
        meter = QueueMeter()
        slow = meter.submit("threadpool")
        time.sleep(0.03)
        fast = meter.submit("threadpool")
        fast.started()
        fast.finished()
        slow.started()
        slow.finished()

        dist = meter.snapshot().wait
        assert dist is not None
        assert dist.count == 2
        assert dist.min_seconds < 0.01 < dist.max_seconds

    def test_a_failure_is_counted_and_named(self) -> None:
        meter = QueueMeter()
        ticket = meter.submit("threadpool")
        ticket.started()
        ticket.finished(ok=False, failure="MeshError: element_size_mm too small")
        snapshot = meter.snapshot()
        assert snapshot.failed == 1 and snapshot.succeeded == 0
        assert snapshot.failure_rate == 1.0

    def test_a_ticket_is_idempotent_and_never_raises(self) -> None:
        meter = QueueMeter()
        ticket = meter.submit("inline")
        ticket.started()
        ticket.started()
        ticket.finished()
        ticket.finished(ok=False, failure="ignored")
        snapshot = meter.snapshot()
        assert (snapshot.started, snapshot.finished, snapshot.failed) == (1, 1, 0)
        assert ticket.state == "finished"

    def test_finishing_without_starting_still_counts_the_job(self) -> None:
        meter = QueueMeter()
        ticket = meter.submit("inline")
        ticket.finished()
        snapshot = meter.snapshot()
        assert (snapshot.started, snapshot.finished, snapshot.pending) == (1, 1, 0)

    def test_the_sample_window_is_bounded_and_says_so(self) -> None:
        meter = QueueMeter(sample_window=4)
        for _ in range(10):
            ticket = meter.submit("threadpool")
            ticket.started()
            ticket.finished()
        snapshot = meter.snapshot()
        assert snapshot.submitted == 10
        assert snapshot.run is not None and snapshot.run.count == 4
        assert snapshot.windowed


class TestTheMeterEmitsSpans:
    def test_nothing_is_recorded_when_nobody_is_collecting(self) -> None:
        meter = QueueMeter()
        ticket = meter.submit("threadpool")
        ticket.started()
        ticket.finished()
        with collect() as recorder:
            pass
        assert recorder.spans == ()

    def test_a_finished_job_records_its_wait_and_its_run(self) -> None:
        meter = QueueMeter()
        with collect() as recorder:
            ticket = meter.submit("threadpool")
            time.sleep(0.01)
            ticket.started()
            time.sleep(0.01)
            ticket.finished(ok=False, failure="MeshError: no volume was produced")

        by_name = {s.name: s for s in recorder.spans}
        assert by_name["jobs.wait"].seconds >= 0.01
        assert by_name["jobs.wait"].ok
        assert by_name["jobs.run"].seconds >= 0.01
        assert by_name["jobs.run"].ok is False
        assert "no volume was produced" in by_name["jobs.run"].failure
        assert by_name["jobs.run"].fields["queue"] == "threadpool"


class TestTheJobQueuesReportThemselves:
    def test_the_inline_queue_meters_and_still_propagates(self) -> None:
        queue = InlineJobQueue()
        queue.submit(lambda: None)
        with pytest.raises(ValueError):
            queue.submit(_raise)

        snapshot = METER.snapshot()
        assert snapshot.submitted == 2 and snapshot.finished == 2
        assert snapshot.failed == 1
        assert snapshot.queues == ("inline",)

    def test_the_thread_pool_meters_a_job_that_ran(self) -> None:
        queue = ThreadPoolJobQueue(max_workers=2)
        done = threading.Event()
        queue.submit(done.set)
        assert done.wait(5)
        queue.shutdown()

        snapshot = METER.snapshot()
        assert snapshot.submitted == 1 and snapshot.finished == 1 and snapshot.failed == 0
        assert snapshot.queues == ("threadpool",)

    def test_a_job_that_raises_is_recorded_and_the_worker_survives(self) -> None:
        queue = ThreadPoolJobQueue(max_workers=1)
        queue.submit(_raise)
        after = threading.Event()
        queue.submit(after.set)
        assert after.wait(5)
        queue.shutdown()

        snapshot = METER.snapshot()
        assert snapshot.finished == 2
        assert snapshot.failed == 1

    def test_a_saturated_pool_shows_the_wait_rather_than_hiding_it(self) -> None:
        """One worker, two jobs: the second one's wait is the queueing delay.

        The ticket is taken in `submit`, not on the worker. Taken at pickup it
        would always read zero -- the reassuring version of the same
        measurement, and the reason queue depth is invisible today.
        """
        queue = ThreadPoolJobQueue(max_workers=1)
        release = threading.Event()
        second = threading.Event()
        queue.submit(lambda: release.wait(5))
        queue.submit(second.set)
        time.sleep(0.05)
        release.set()
        assert second.wait(5)
        queue.shutdown()

        wait = METER.snapshot().wait
        assert wait is not None
        assert wait.max_seconds >= 0.05


class TestTheMediaHooks:
    def test_a_write_records_its_bytes_and_whether_it_deduplicated(
        self, tmp_path: Path
    ) -> None:
        store = LocalMediaStore(tmp_path, chunk_size=16)
        with collect() as recorder:
            first = store.write_bytes(b"x" * 100)
            store.write_bytes(b"x" * 100)

        writes = [s for s in recorder.spans if s.name == "media.write"]
        assert len(writes) == 2
        assert writes[0].fields == {"bytes": 100, "deduplicated": False}
        assert writes[1].fields["deduplicated"] is True
        assert first.digest

    def test_a_write_refused_at_the_ceiling_records_how_far_it_got(
        self, tmp_path: Path
    ) -> None:
        """A rejected upload still did the IO. A span saying it moved nothing
        would hide the cost of the rejection."""
        store = LocalMediaStore(tmp_path, chunk_size=16)
        with collect() as recorder:
            with pytest.raises(MediaTooLarge):
                store.write(io.BytesIO(b"y" * 100), max_bytes=50)

        (write,) = [s for s in recorder.spans if s.name == "media.write"]
        assert write.ok is False
        assert "MediaTooLarge" in write.failure
        assert write.fields["bytes"] > 0

    def test_a_read_records_bytes_and_chunks(self, tmp_path: Path) -> None:
        store = LocalMediaStore(tmp_path, chunk_size=16)
        info = store.write_bytes(b"z" * 100)
        with collect() as recorder:
            assert b"".join(store.iter_chunks(info.digest)) == b"z" * 100

        (read,) = [s for s in recorder.spans if s.name == "media.read"]
        assert read.fields == {"bytes": 100, "chunks": 7}
        assert read.ok

    def test_a_missing_blob_records_the_read_that_failed(self, tmp_path: Path) -> None:
        store = LocalMediaStore(tmp_path)
        with collect() as recorder:
            with pytest.raises(MediaNotFound):
                list(store.iter_chunks("a" * 64))

        (read,) = [s for s in recorder.spans if s.name == "media.read"]
        assert read.ok is False and "MediaNotFound" in read.failure

    def test_verify_records_the_bytes_it_re_hashed_and_the_verdict(
        self, tmp_path: Path
    ) -> None:
        store = LocalMediaStore(tmp_path)
        info = store.write_bytes(b"w" * 64)
        with collect() as recorder:
            assert store.verify(info.digest)

        (verify,) = [s for s in recorder.spans if s.name == "media.verify"]
        assert verify.fields["bytes"] == 64
        assert verify.fields["intact"] is True


class TestTheGmshLockHook:
    """The one hook that changed control flow rather than wrapping it.

    `with _GMSH_LOCK:` became an explicit acquire/release so the wait and the
    hold could be separated. The lock's scope is unchanged, and these check
    that: two sessions in a row, and a session that raised, both leave the lock
    free. A hook that leaked the lock would make the second mesh in the process
    hang for ever, which is a far worse defect than the one it measures.
    """

    def test_the_wait_and_the_hold_are_timed_apart(self) -> None:
        from app.mesh.gmsh_session import gmsh_session

        with collect() as recorder:
            with gmsh_session():
                pass

        names = [s.name for s in recorder.spans]
        assert "mesh.gmsh.wait" in names
        assert "mesh.gmsh.session" in names
        held = next(s for s in recorder.spans if s.name == "mesh.gmsh.session")
        waited = next(s for s in recorder.spans if s.name == "mesh.gmsh.wait")
        assert held.seconds >= waited.seconds

    def test_the_lock_is_released_for_the_next_session(self) -> None:
        from app.mesh.gmsh_session import _GMSH_LOCK, gmsh_session

        with gmsh_session():
            assert _GMSH_LOCK.locked()
        assert not _GMSH_LOCK.locked()
        with gmsh_session():
            pass
        assert not _GMSH_LOCK.locked()

    def test_a_failure_inside_the_session_still_releases_the_lock(self) -> None:
        from app.mesh.gmsh_session import _GMSH_LOCK, gmsh_session

        with pytest.raises(RuntimeError):
            with gmsh_session():
                raise RuntimeError("meshing blew up")
        assert not _GMSH_LOCK.locked()

    def test_a_contended_lock_shows_up_as_wait_time(self) -> None:
        """Two threads meshing at once look like one slow job today.

        The point of splitting the wait out is that this becomes visible: the
        second thread's `mesh.gmsh.wait` carries the time it spent blocked.
        """
        from app.mesh.gmsh_session import _GMSH_LOCK

        _GMSH_LOCK.acquire()
        try:
            waits: list[float] = []
            started = threading.Event()

            def blocked() -> None:
                from app.mesh.gmsh_session import gmsh_session

                started.set()
                with gmsh_session():
                    pass

            with collect() as recorder:
                worker = threading.Thread(target=blocked, name="contender")
                worker.start()
                assert started.wait(5)
                time.sleep(0.05)
                _GMSH_LOCK.release()
                worker.join(10)
                assert not worker.is_alive()
                waits = [s.seconds for s in recorder.spans if s.name == "mesh.gmsh.wait"]
        finally:
            if _GMSH_LOCK.locked():  # pragma: no cover - only on a failed run
                _GMSH_LOCK.release()

        assert waits and max(waits) >= 0.05


def _raise() -> None:
    raise ValueError("the job failed")
