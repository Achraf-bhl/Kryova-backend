import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.router import api_router
from app.core.config import BASE_DIR, settings
from app.jobs import get_job_queue

logger = logging.getLogger(__name__)

APP_VERSION = "0.1.1"


def _resolve_git_sha() -> str:
    """The short sha of the running build.

    An installer build has no `.git` directory to inspect, so the value is
    baked in as `KRYOVA_GIT_SHA` at build time (see the Windows integration
    build stamp). A dev server falls back to asking git directly.
    """
    env_sha = os.environ.get("KRYOVA_GIT_SHA")
    if env_sha:
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


GIT_SHA = _resolve_git_sha()
BUILT_AT = os.environ.get("KRYOVA_BUILT_AT") or datetime.now(UTC).isoformat()


def _configure_logging() -> None:
    """JSON logs in production, human-readable in development."""
    if settings.environment != "production":
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)r}',
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_configure_logging()


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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'"
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Requested-With",
    ],
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "git_sha": GIT_SHA,
        "built_at": BUILT_AT,
    }
