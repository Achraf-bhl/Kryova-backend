"""Background job execution.

Meshing and solving take seconds to minutes, which is far too long to hold a
request open. `JobQueue` is the seam: a thread pool is enough for a single-node
deployment, and swapping in Celery or RQ later means implementing one method,
not rewriting the API layer.

Both implementations report their lifecycle to `app.observe.queue.METER` --
submitted, started, finished -- which is what turns "the queue is backed up"
from a feeling into a number. The `JobQueue` ABC is untouched: metering is three
calls a queue makes, not a method a queue has to implement, so a future
`CeleryJobQueue` can report the same three points without the abstraction
growing. The meter counts unconditionally rather than only while something is
collecting, because queue depth is asked about *after* it becomes a problem; the
span records it also emits stay opt-in. See that module for the argument.

That seam is all there is today. A `CeleryJobQueue` used to sit here that looked
for a `simulation_id` attribute no caller ever set and, on the `except` branch,
ran the job inline -- so selecting it silently moved four minutes of FEA onto the
request thread. Celery is not a dependency and was never wired to one; the
config validator now refuses the value outright rather than pretending.
"""

import importlib
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from functools import lru_cache
from multiprocessing import get_context
from typing import Any

from app.observe.queue import METER, JobTicket

logger = logging.getLogger(__name__)

Job = Callable[[], None]


@dataclass(frozen=True)
class RemoteCall:
    """What a child process runs: a module-level function by name, with plain-data arguments.

    A closure cannot cross a process boundary (it holds a database session, a queue and a media
    store, none of which pickle), so the work a child does is named as `"package.module:function"`
    and rebuilds what it needs from settings on the far side. Arguments must pickle.
    """

    target: str
    args: tuple[Any, ...] = ()

    def resolve(self) -> Callable[..., Any]:
        module, _, name = self.target.partition(":")
        return getattr(importlib.import_module(module), name)  # type: ignore[no-any-return]


class Work:
    """A job that can run in this process or, where the queue has processes, in a child.

    `local` is the closure every queue can run. `remote` is the same work spelled so a child
    process can run it; `after` is what must happen back in the *parent* when it ends, whether it
    succeeded, failed or took the child down with it (the next waiting run is handed the slot);
    `on_crash` records a run whose process vanished, because a child that was killed cannot write
    its own failure. A queue without processes calls the object and gets `local`, so a caller
    builds one `Work` and never asks which queue it has.
    """

    def __init__(
        self,
        local: Job,
        *,
        remote: RemoteCall | None = None,
        after: Job | None = None,
        on_crash: Callable[[str], None] | None = None,
    ) -> None:
        self.local = local
        self.remote = remote
        self.after = after
        self.on_crash = on_crash

    def __call__(self) -> None:
        self.local()


class JobQueue(ABC):
    @abstractmethod
    def submit(self, job: Job) -> None:
        """Schedule `job`. Must not raise for a job that later fails."""

    def shutdown(self) -> None:
        """Wait for in-flight jobs. Called on application shutdown."""


class InlineJobQueue(JobQueue):
    """Runs jobs immediately on the calling thread.

    Used by tests and by `--reload` dev servers, where a background thread
    holding a database session across a reload causes more confusion than the
    concurrency is worth.
    """

    def submit(self, job: Job) -> None:
        ticket = METER.submit("inline")
        ticket.started()
        try:
            job()
        except BaseException as exc:
            # Recorded and re-raised: an inline queue propagates to its caller,
            # and swallowing here to keep the books tidy would change what the
            # request thread sees. The meter never decides whether a job runs.
            ticket.finished(ok=False, failure=f"{type(exc).__name__}: {exc}")
            raise
        ticket.finished(ok=True)


class ThreadPoolJobQueue(JobQueue):
    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kryova-job")
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, job: Job) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is shutting down")
            # The ticket is taken here rather than on the worker so the wait it
            # measures is the queueing delay -- the number that says whether two
            # workers are enough. A ticket taken at pickup would always read
            # zero, which is the reassuring version of the same measurement.
            ticket = METER.submit("threadpool")
            self._pool.submit(self._run, job, ticket)

    @staticmethod
    def _run(job: Job, ticket: JobTicket) -> None:
        ticket.started()
        try:
            job()
        except Exception as exc:
            # The runner records failures on the job row; anything reaching here
            # is a bug in the runner itself and must not kill the worker thread.
            logger.exception("Unhandled error in background job")
            ticket.finished(ok=False, failure=f"{type(exc).__name__}: {exc}")
        else:
            ticket.finished(ok=True)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)


#: Where a child process learns how many BLAS threads it may use. Read before numpy loads, which
#: is the only moment a BLAS library reads it -- so it can only be set per *process*, and this is
#: why per-job thread pinning needs processes at all (`app/core/compute_plan.py`).
_BLAS_THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def _init_child(threads: int, memory_limit_mb: int | None) -> None:
    """Runs once in each child, before its first job imports a solver."""
    for name in _BLAS_THREAD_VARIABLES:
        os.environ[name] = str(threads)
    if memory_limit_mb is not None:
        _limit_address_space(memory_limit_mb)


def _limit_address_space(megabytes: int) -> None:
    """A hard ceiling on this process's address space, so one runaway solve ends *itself*.

    POSIX only (`RLIMIT_AS`); where `resource` does not exist the limit is not applied and the
    queue says so at construction. It limits address space, which is more than resident memory
    -- threaded BLAS reserves large virtual arenas -- so it is off unless asked for, and a value
    below a few GB can stop a solve that would have fitted in RAM.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows
        return
    limit = megabytes * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _run_remote(call: RemoteCall) -> None:
    call.resolve()(*call.args)


class ProcessPoolJobQueue(JobQueue):
    """Runs each job in its own worker process, so the GIL, gmsh's global singleton and one
    runaway solve stop being shared between jobs (ROAD_TO_10 6.4, MAKING_IT_FASTER 2.4).

    * **Children are spawned, never forked.** A fork copies the parent's database connections,
      its gmsh state and its locks into a process that cannot use them safely.
    * **A job owns its session.** The child rebuilds a session and a media store from settings;
      nothing a request held crosses over (CLAUDE.md, *Background jobs own their own session*).
    * **Only `Work` with a `RemoteCall` goes to a child.** A bare closure cannot be sent, so it
      runs on a small thread pool in this process and the first one is logged. That is a
      fallback with a named cost, not silent inline execution (the Celery lesson in the module
      docstring): every route that starts a simulation builds a `Work`.
    * **A child that dies is a failed job, not a stuck one.** `BrokenProcessPool` fails every
      job in flight, the pool is rebuilt for the next, and each job's `on_crash` records why on
      its row -- the out-of-memory killer does not write a failure message.
    * **Admission is still per process.** The memory governor (`app/simulation/memory.py`)
      runs inside each child and cannot see another child's reservation; what is shared is the
      live free-memory reading, so two children protect each other reactively and not by plan.
    """

    def __init__(
        self,
        max_workers: int,
        *,
        solver_threads: int = 1,
        memory_limit_mb: int | None = None,
    ) -> None:
        self._max_workers = max(1, max_workers)
        self._threads = max(1, solver_threads)
        self._memory_limit_mb = memory_limit_mb
        self._lock = threading.Lock()
        self._closed = False
        self._warned_closure = False
        self._pool = self._new_pool()
        # Parent-side callbacks (after / on_crash) and closures run here, never on the pool's
        # management thread, whose exceptions would be swallowed.
        self._local = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="kryova-job-local"
        )

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=self._max_workers,
            mp_context=get_context("spawn"),
            initializer=_init_child,
            initargs=(self._threads, self._memory_limit_mb),
        )

    def submit(self, job: Job) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is shutting down")
            ticket = METER.submit("processpool")
            remote = job.remote if isinstance(job, Work) else None
            if remote is None:
                if not self._warned_closure:
                    self._warned_closure = True
                    logger.warning(
                        "A job without a RemoteCall was submitted to the process queue; "
                        "it runs in this process, on a thread"
                    )
                self._local.submit(ThreadPoolJobQueue._run, job, ticket)
                return
            try:
                future = self._pool.submit(_run_remote, remote)
            except BrokenProcessPool:
                # A child died and its callback has not rebuilt the pool yet.
                self._replace_locked(self._pool)
                future = self._pool.submit(_run_remote, remote)
            pool = self._pool
            # The done callback only hands off. CPython calls it on the executor's management
            # thread, and for a broken pool from inside `terminate_broken`, which holds the
            # executor's non-reentrant `_shutdown_lock` -- so a callback that calls
            # `shutdown()` on that pool waits on its own thread forever, and so does every
            # later `shutdown(wait=True)`. Measured on Python 3.14, 2026-10-05: the crash test
            # hung there and took the whole suite with it.
            future.add_done_callback(
                lambda done: self._local.submit(self._ended, done, job, ticket, pool)  # type: ignore[arg-type]
            )
            ticket.started()  # accepted by the pool; a child picks it up as soon as one is free

    def _ended(
        self, future: Future[None], job: Work, ticket: JobTicket, pool: ProcessPoolExecutor
    ) -> None:
        error = future.exception()
        if isinstance(error, BrokenProcessPool):
            self._rebuild_pool(pool)
        if error is None:
            ticket.finished(ok=True)
        else:
            reason = f"{type(error).__name__}: {error}"
            logger.error("A job's worker process failed: %s", reason)
            ticket.finished(ok=False, failure=reason)
            if job.on_crash is not None:
                self._local.submit(self._guarded, job.on_crash, reason)
        if job.after is not None:
            self._local.submit(self._guarded, job.after)

    @staticmethod
    def _guarded(function: Callable[..., None], *args: Any) -> None:
        try:
            function(*args)
        except Exception:  # noqa: BLE001 - a callback must never take a worker thread down
            logger.exception("A job-queue callback failed")

    def _rebuild_pool(self, broken: ProcessPoolExecutor) -> None:
        with self._lock:
            if self._closed:
                return
            self._replace_locked(broken)

    def _replace_locked(self, broken: ProcessPoolExecutor) -> None:
        # Two jobs in flight when a child dies both fail with BrokenProcessPool; only the
        # first replaces the pool, or the second would discard the fresh one and its jobs.
        if self._pool is not broken:
            return
        logger.error("A worker process ended abnormally; starting a fresh pool")
        broken.shutdown(wait=False, cancel_futures=False)
        self._pool = self._new_pool()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)
        self._local.shutdown(wait=True)


@lru_cache
def get_job_queue() -> JobQueue:
    from app.core import compute_plan
    from app.core.config import settings

    if settings.inline_jobs or settings.job_queue_backend == "inline":
        return InlineJobQueue()
    plan = compute_plan.current()
    if settings.job_queue_backend == "process":
        return ProcessPoolJobQueue(
            max_workers=plan.job_workers,
            solver_threads=plan.solver_threads,
            memory_limit_mb=settings.job_memory_limit_mb,
        )
    return ThreadPoolJobQueue(max_workers=plan.job_workers)
