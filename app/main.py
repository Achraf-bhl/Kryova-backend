import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.jobs import get_job_queue

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _fail_orphaned_jobs()
    yield
    get_job_queue().shutdown()


def _fail_orphaned_jobs(session_factory=None) -> None:
    """Mark jobs left mid-flight by a previous process as failed.

    The in-process queue does not survive a restart, so a job still marked
    RUNNING at startup has no worker and would otherwise be polled forever.

    `session_factory` exists so this can be tested without a running app.
    """
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models import JobStatus, SimulationJob

    with (session_factory or SessionLocal)() as db:
        stmt = select(SimulationJob).where(
            SimulationJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
        )
        orphans = list(db.scalars(stmt))
        for job in orphans:
            job.status = JobStatus.FAILED
            job.error = "Interrupted by a server restart. Run the simulation again."
        if orphans:
            db.commit()
            logger.warning("Failed %d simulation job(s) orphaned by a restart", len(orphans))


app = FastAPI(
    title=settings.project_name,
    openapi_url=f"{settings.api_v1_prefix}/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
