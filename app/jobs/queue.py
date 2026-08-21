"""Background job execution.

Meshing and solving take seconds to minutes, which is far too long to hold a
request open. `JobQueue` is the seam: a thread pool is enough for a single-node
deployment, and swapping in Celery or RQ later means implementing one method,
not rewriting the API layer.
"""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

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
        job()


class ThreadPoolJobQueue(JobQueue):
    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="kryova-job"
        )
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, job: Job) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job queue is shutting down")
            self._pool.submit(self._run, job)

    @staticmethod
    def _run(job: Job) -> None:
        try:
            job()
        except Exception:
            # The runner records failures on the job row; anything reaching here
            # is a bug in the runner itself and must not kill the worker thread.
            logger.exception("Unhandled error in background job")

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True)


@lru_cache
def get_job_queue() -> JobQueue:
    from app.core.config import settings

    if settings.inline_jobs:
        return InlineJobQueue()
    return ThreadPoolJobQueue(max_workers=settings.job_workers)
