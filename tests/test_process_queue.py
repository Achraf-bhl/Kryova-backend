"""The process-pool job queue (ROAD_TO_10 6.4).

Offline: no database, no solver. The work a child runs here is a function in this module, named
by `RemoteCall`, so what is measured is the queue -- that jobs really run in other processes, at
the same time, with their parent-side callbacks, and that a child that dies fails its job rather
than sticking it -- and not meshing, which has its own tests.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from app.jobs.queue import (
    ProcessPoolJobQueue,
    RemoteCall,
    ThreadPoolJobQueue,
    Work,
    get_job_queue,
)

# -- what a child runs ------------------------------------------------------------------------


def stamp(directory: str, name: str) -> None:
    """Record this process's pid and the thread-count variable it was started with."""
    Path(directory, f"{name}.pid").write_text(
        f"{os.getpid()} {os.environ.get('OMP_NUM_THREADS', '-')}", encoding="utf-8"
    )


def rendezvous(directory: str, mine: str, theirs: str) -> None:
    """Leave a marker, then wait for the other job's. Only two jobs running at once can pass."""
    Path(directory, f"{mine}.here").write_text("x", encoding="utf-8")
    deadline = time.monotonic() + 30
    while not Path(directory, f"{theirs}.here").exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{theirs} never started: the jobs did not overlap")
        time.sleep(0.02)
    stamp(directory, mine)


def mesh_a_box(directory: str, name: str, size: float) -> None:
    """Mesh the same box in this process and write what came out."""
    from app.mesh.gmsh_mesher import generate_tet_mesh
    from tests.test_mesh import box_stl

    path = Path(directory, f"{name}.stl")
    path.write_bytes(box_stl((20.0, 20.0, 60.0)))
    mesh, _ = generate_tet_mesh(path, "stl", size, element_order=2)
    Path(directory, f"{name}.mesh").write_text(
        f"{mesh.tet_count} {mesh.node_count}", encoding="utf-8"
    )


def die(code: int) -> None:
    os._exit(code)


def fail_politely() -> None:
    raise ValueError("the solver refused it")


MODULE = "tests.test_process_queue"


def call(function: str, *args: object) -> RemoteCall:
    return RemoteCall(f"{MODULE}:{function}", tuple(args))


def _wait(predicate, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def pool():
    queues: list[ProcessPoolJobQueue] = []

    def make(workers: int = 2, **kwargs) -> ProcessPoolJobQueue:
        queue = ProcessPoolJobQueue(max_workers=workers, **kwargs)
        queues.append(queue)
        return queue

    yield make
    for queue in queues:
        queue.shutdown()


class TestJobsRunInTheirOwnProcesses:
    def test_a_job_runs_in_a_child_and_not_in_this_process(self, pool, tmp_path: Path) -> None:
        queue = pool(1)
        queue.submit(Work(lambda: None, remote=call("stamp", str(tmp_path), "a")))
        assert _wait(lambda: (tmp_path / "a.pid").exists())
        pid = int((tmp_path / "a.pid").read_text(encoding="utf-8").split()[0])
        assert pid != os.getpid()

    def test_two_jobs_overlap_in_wall_time(self, pool, tmp_path: Path) -> None:
        # Each waits for the other to have started. A queue that ran them one at a time would
        # time the first out; only genuine concurrency lets both through.
        queue = pool(2)
        d = str(tmp_path)
        queue.submit(Work(lambda: None, remote=call("rendezvous", d, "a", "b")))
        queue.submit(Work(lambda: None, remote=call("rendezvous", d, "b", "a")))
        assert _wait(lambda: (tmp_path / "a.pid").exists() and (tmp_path / "b.pid").exists())
        pids = {(tmp_path / f"{n}.pid").read_text(encoding="utf-8").split()[0] for n in ("a", "b")}
        assert len(pids) == 2, "two jobs, two processes"

    def test_each_child_is_told_how_many_threads_it_may_use(self, pool, tmp_path: Path) -> None:
        queue = pool(1, solver_threads=3)
        queue.submit(Work(lambda: None, remote=call("stamp", str(tmp_path), "t")))
        assert _wait(lambda: (tmp_path / "t.pid").exists())
        assert (tmp_path / "t.pid").read_text(encoding="utf-8").split()[1] == "3"

    def test_two_meshing_jobs_each_produce_the_mesh_a_single_process_run_produces(
        self, pool, tmp_path: Path
    ) -> None:
        from app.mesh.gmsh_mesher import generate_tet_mesh
        from tests.test_mesh import box_stl

        reference_path = tmp_path / "reference.stl"
        reference_path.write_bytes(box_stl((20.0, 20.0, 60.0)))
        reference, _ = generate_tet_mesh(reference_path, "stl", 8.0, element_order=2)
        expected = f"{reference.tet_count} {reference.node_count}"

        queue = pool(2)
        d = str(tmp_path)
        for name in ("a", "b"):
            queue.submit(Work(lambda: None, remote=call("mesh_a_box", d, name, 8.0)))
        assert _wait(lambda: (tmp_path / "a.mesh").exists() and (tmp_path / "b.mesh").exists())
        for name in ("a", "b"):
            assert (tmp_path / f"{name}.mesh").read_text(encoding="utf-8") == expected


class TestWhatHappensBackInTheParent:
    def test_the_after_callback_runs_here_when_a_job_succeeds(self, pool, tmp_path: Path) -> None:
        queue = pool(1)
        seen: list[tuple[int, str]] = []
        queue.submit(
            Work(
                lambda: None,
                remote=call("stamp", str(tmp_path), "a"),
                after=lambda: seen.append((os.getpid(), threading.current_thread().name)),
            )
        )
        assert _wait(lambda: bool(seen))
        assert seen[0][0] == os.getpid()
        assert not seen[0][1].startswith("MainThread")

    def test_the_after_callback_runs_when_a_job_raises(self, pool) -> None:
        queue = pool(1)
        ran: list[str] = []
        crashes: list[str] = []
        queue.submit(
            Work(
                lambda: None,
                remote=call("fail_politely"),
                after=lambda: ran.append("after"),
                on_crash=crashes.append,
            )
        )
        assert _wait(lambda: bool(ran))
        assert crashes and "the solver refused it" in crashes[0]

    def test_a_child_that_dies_fails_its_job_and_the_queue_still_works(
        self, pool, tmp_path: Path
    ) -> None:
        queue = pool(1)
        crashes: list[str] = []
        ran: list[str] = []
        queue.submit(
            Work(
                lambda: None,
                remote=call("die", 9),
                after=lambda: ran.append("after"),
                on_crash=crashes.append,
            )
        )
        assert _wait(lambda: bool(ran))
        assert crashes and "BrokenProcessPool" in crashes[0]

        # The pool was rebuilt: the next job runs.
        queue.submit(Work(lambda: None, remote=call("stamp", str(tmp_path), "after-crash")))
        assert _wait(lambda: (tmp_path / "after-crash.pid").exists())

    def test_a_callback_that_raises_does_not_stop_the_next_one(self, pool) -> None:
        queue = pool(1)
        later: list[str] = []

        def broken() -> None:
            raise RuntimeError("the hand-on failed")

        queue.submit(Work(lambda: None, remote=call("fail_politely"), after=broken))
        queue.submit(
            Work(lambda: None, remote=call("fail_politely"), after=lambda: later.append("x"))
        )
        assert _wait(lambda: bool(later))


class TestAClosureIsNeverSilentlyRunInline:
    def test_a_job_with_no_remote_call_runs_on_a_thread_here_and_the_queue_says_so(
        self, pool, caplog: pytest.LogCaptureFixture
    ) -> None:
        queue = pool(1)
        done: list[tuple[int, str]] = []
        with caplog.at_level("WARNING", logger="app.jobs.queue"):
            queue.submit(lambda: done.append((os.getpid(), threading.current_thread().name)))
            assert _wait(lambda: bool(done))
        assert done[0][0] == os.getpid()
        assert "without a RemoteCall" in caplog.text

    def test_a_job_submitted_after_shutdown_is_refused(self, pool) -> None:
        queue = pool(1)
        queue.shutdown()
        with pytest.raises(RuntimeError, match="shutting down"):
            queue.submit(Work(lambda: None, remote=call("fail_politely")))


class TestEveryOtherQueueStillRunsTheClosure:
    def test_a_work_object_called_directly_runs_its_local_job(self) -> None:
        ran: list[str] = []
        Work(lambda: ran.append("local"), remote=call("fail_politely"))()
        assert ran == ["local"]

    def test_the_thread_pool_runs_a_work_object_without_a_child_process(self) -> None:
        queue = ThreadPoolJobQueue(max_workers=1)
        ran: list[int] = []
        queue.submit(Work(lambda: ran.append(os.getpid()), remote=call("die", 9)))
        queue.shutdown()
        assert ran == [os.getpid()], "the remote call is for a process queue and was not used"


class TestTheSettingSelectsIt:
    def test_process_is_a_known_backend(self) -> None:
        from app.core.config import JOB_QUEUE_BACKENDS, Settings

        assert "process" in JOB_QUEUE_BACKENDS
        assert Settings(job_queue_backend="process").job_queue_backend == "process"  # type: ignore[call-arg]

    def test_get_job_queue_builds_the_process_queue_sized_by_the_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import compute_plan
        from app.core.config import settings

        monkeypatch.setattr(settings, "inline_jobs", False)
        monkeypatch.setattr(settings, "job_queue_backend", "process")
        monkeypatch.setattr(settings, "job_workers", 3)
        monkeypatch.setattr(settings, "solver_threads", 2)
        compute_plan.reset()
        get_job_queue.cache_clear()
        try:
            queue = get_job_queue()
            assert isinstance(queue, ProcessPoolJobQueue)
            assert queue._max_workers == 3 and queue._threads == 2
            queue.shutdown()
        finally:
            get_job_queue.cache_clear()
            compute_plan.reset()


class TestASimulationIsStartedAsWork:
    def test_start_hands_the_queue_a_work_object_a_child_can_run(self, monkeypatch) -> None:
        from app.simulation import waiting

        taken: list[object] = []

        class Recorder(ThreadPoolJobQueue):
            def submit(self, job) -> None:  # type: ignore[override]
                taken.append(job)

        waiting.start(Recorder(1), "job-1", lambda: None, object())  # type: ignore[arg-type]
        job = taken[0]
        assert isinstance(job, Work)
        assert job.remote == RemoteCall("app.simulation.worker:run_in_child", ("job-1",))
        assert job.after is not None and job.on_crash is not None
        assert RemoteCall("app.simulation.worker:run_in_child").resolve().__name__ == "run_in_child"
