import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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

REQUEST_ID_HEADER = "X-Request-ID"


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line, for log shippers that parse rather than grep.

    Built with `json.dumps` rather than a `%`-format template: the obvious
    `"msg":%(message)r` spelling produces Python's repr, which quotes with
    apostrophes and escapes with Python rules, so every line it emitted was
    invalid JSON. There is no format string that escapes correctly.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging() -> None:
    """JSON logs in production, human-readable in development."""
    if not settings.is_production:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
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


def docs_urls() -> dict[str, str | None]:
    """Where the interactive docs are served, if at all.

    They enumerate every route, schema and validation rule: a gift in
    development and an attack map in production. Production serves none of the
    three -- including the OpenAPI document, which is the one that actually
    leaks; leaving it reachable while hiding the two HTML pages in front of it
    hides nothing.
    """
    if settings.is_production:
        return {"openapi_url": None, "docs_url": None, "redoc_url": None}
    return {
        "openapi_url": f"{settings.api_v1_prefix}/openapi.json",
        "docs_url": "/docs",
        "redoc_url": "/redoc",
    }


_docs = docs_urls()

app = FastAPI(
    title=settings.project_name,
    lifespan=lifespan,
    openapi_url=_docs["openapi_url"],
    docs_url=_docs["docs_url"],
    redoc_url=_docs["redoc_url"],
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Tag every request with an id, echoed back on the response.

    An id supplied by the caller is kept so a trace survives the hop from the
    frontend; anything else gets a fresh one. It is stored on `request.state` so
    handlers and log records can carry the same value.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


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
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
        REQUEST_ID_HEADER,
    ],
    expose_headers=[REQUEST_ID_HEADER],
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)

app.include_router(api_router, prefix=settings.api_v1_prefix)


def _probe(name: str, check) -> str | None:
    """Run one health check, returning its failure message or None."""
    try:
        check()
    except Exception as exc:  # noqa: BLE001 - any failure is a failed probe
        logger.warning("Health check %s failed: %s", name, exc)
        return f"{type(exc).__name__}: {exc}"
    return None


def _check_media_store() -> None:
    """Confirm the blob store's root exists and is writable.

    A full or unmounted media volume is the failure this catches: the database
    stays perfectly healthy while every upload and every finished simulation
    fails to persist its bytes.
    """
    from app.media import get_media_store

    root = get_media_store().root
    root.mkdir(parents=True, exist_ok=True)
    probe = root / ".health"
    probe.write_bytes(b"")
    probe.unlink()


@app.get("/health", tags=["health"])
def health_check() -> Response:
    """Readiness probe: 200 only when the dependencies a request needs are up.

    Both the database and the media store are checked, because the service can
    serve neither an upload nor a result without both, and a probe that only
    reports the process is alive would keep a broken instance in the load
    balancer.
    """
    from app.core.database import check_database

    failures = {
        name: message
        for name, check in (("database", check_database), ("media_store", _check_media_store))
        if (message := _probe(name, check)) is not None
    }
    healthy = not failures
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "version": APP_VERSION,
            "git_sha": GIT_SHA,
            "built_at": BUILT_AT,
            "checks": {name: failures.get(name, "ok") for name in ("database", "media_store")},
        },
    )
