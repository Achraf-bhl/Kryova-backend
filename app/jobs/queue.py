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

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from app.observe.queue import METER, JobTicket

logger = logging.getLogger(__name__)

Job = Callable[[], None]


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


@lru_cache
def get_job_queue() -> JobQueue:
    from app.core.config import settings

    if settings.inline_jobs or settings.job_queue_backend == "inline":
        return InlineJobQueue()
    return ThreadPoolJobQueue(max_workers=settings.job_workers)
