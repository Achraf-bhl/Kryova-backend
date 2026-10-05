import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.rate_limit import EXPOSED_HEADERS as EXPOSED_RATE_LIMIT_HEADERS
from app.api.rate_limit import RateLimitHeadersMiddleware
from app.api.router import api_router
from app.core.config import BASE_DIR, settings
from app.jobs import get_job_queue

logger = logging.getLogger(__name__)

APP_VERSION = "0.2.0"


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


class HumanLogFormatter(logging.Formatter):
    """One readable line per record, with the request id when there is one.

    The request id is appended rather than given a column, because most lines
    do not have one -- a startup message, a bridge reconnect, a corpus build --
    and a column that is empty four times out of five is a column that pushes
    the message off the terminal.
    """

    default_time_format = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record)} {record.levelname:<7} {record.name}: {record.getMessage()}"
        )
        request_id = getattr(record, "request_id", None)
        if request_id:
            base = f"{base}  [{request_id}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


#: Third-party loggers that say nothing worth a line at INFO. `httpx` logs one
#: line per request, which on the agent path means one per model round trip;
#: `python_multipart` narrates its parser token by token.
_NOISY_AT_INFO = ("httpx", "httpcore", "python_multipart", "watchfiles", "urllib3")


def _configure_logging() -> None:
    """JSON logs in production, human-readable in development.

    The development half of that sentence used to be a lie, and it is the kind
    this codebase has a rule against: the function returned immediately when
    `is_production` was false, so a dev server installed no root handler at
    all. The consequences were not subtle. Root defaults to WARNING with no
    handlers, so **every `logger.info` in `app/**` was discarded** -- which is
    most of the diagnostics in the CATIA dispatch, the bridge, the agent loop
    and the retrieval build -- and every warning and exception fell through to
    `logging.lastResort`, which writes the bare message to stderr with no
    timestamp, no level and no logger name. A real "refresh token reuse
    detected; session family revoked" was found in the log jammed onto the end
    of an unrelated line, attributable to nothing.

    Uvicorn's own loggers are left alone: its `LOGGING_CONFIG` gives `uvicorn`
    and `uvicorn.access` their own handlers with `propagate` off, so adding a
    root handler here does not double up the access log.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter() if settings.is_production else HumanLogFormatter())

    level = getattr(logging, settings.log_level.strip().upper(), None)
    if not isinstance(level, int):
        # A typo in LOG_LEVEL must not silence the server it was meant to make
        # louder, so fall back and say so once the handler is installed.
        level, bad_level = logging.INFO, settings.log_level
    else:
        bad_level = None

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in _NOISY_AT_INFO:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))

    if bad_level is not None:
        logging.getLogger(__name__).warning(
            "Unusable LOG_LEVEL %r; logging at INFO instead", bad_level
        )


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _warn_about_insecure_defaults()
    _start_local_postgres()
    _log_compute_plan()
    _check_rate_limit_backend()
    _fail_orphaned_jobs()
    _resume_waiting_runs()
    _warm_intent_router()
    _warm_geometry_kernel()
    yield
    get_job_queue().shutdown()
    _stop_local_catia_bridge()


def _log_compute_plan() -> None:
    """Say what this machine has and how many jobs and threads were chosen for it.

    One line each, at every start, because "why is a solve using five cores" and "why did two
    jobs not run together" have the same answer and it is otherwise nowhere an operator looks.
    Nothing here can stop the boot: a probe that fails is a missing line, never an outage.
    """
    from app.core import compute_plan, hardware

    try:
        machine = hardware.hardware()
        plan = compute_plan.current()
    except Exception:  # noqa: BLE001 - observability must not fail the boot
        logger.exception("Could not read this machine's hardware")
        return
    logger.info(
        "hardware: %d logical / %s physical cores, %s MB RAM (%s)",
        machine.logical_cores,
        machine.physical_cores if machine.physical_cores is not None else "unknown",
        machine.total_ram_mb if machine.total_ram_mb is not None else "unknown",
        machine.source,
    )
    for note in machine.notes:
        logger.info("hardware note: %s", note)
    logger.info(
        "compute plan: %d job worker(s) x %d solver thread(s); %s; %s",
        plan.job_workers,
        plan.solver_threads,
        plan.basis["job_workers"],
        plan.basis["solver_threads"],
    )
    if plan.oversubscribed:
        logger.warning(
            "compute plan oversubscribes this machine: %d x %d is more than the %d core(s) it can "
            "spare. This is what the settings ask for; remove JOB_WORKERS / SOLVER_THREADS to "
            "have it derived.",
            plan.job_workers,
            plan.solver_threads,
            plan.physical_cores - plan.reserved_cores,
        )


def _check_rate_limit_backend() -> None:
    """Say which backend counts the rate limits, and refuse the one that is wrong.

    `config._harden_production` refuses the *configuration* of several workers with no
    Redis URL. This is the other half: a URL that is set and a Redis that is not answering
    leaves the limiter on memory exactly as no URL would, and the configuration check had
    passed. One line in the log says which it is either way, because "the limit did
    nothing" is otherwise a question with two answers.
    """
    from app.api.rate_limit import backend_report, refuse_an_unshared_limiter_across_workers

    report = backend_report()
    logger.info("rate limits: %s (%s)", report.kind, report.detail)
    refuse_an_unshared_limiter_across_workers()


def _warm_intent_router() -> None:
    """Load Laya before the server accepts its first request, on this thread.

    Found live, 2026-09-23: loading it lazily, inside the request that first
    needed it, took the whole process down with no Python traceback and no
    Windows crash log -- and the identical load, run as a standalone script on
    the main thread with nothing else going on, worked every time. The
    difference is FastAPI's sync routes running on a worker thread out of
    `anyio`'s threadpool: a CUDA context's first initialisation happening
    there, concurrently with the event loop and the CATIA bridge's own COM
    apartment thread, is where this looked for trouble and stopped -- rather
    than spend longer proving which of the two was the actual conflict,
    moving the one-time cost to a point with neither running yet removes both
    candidates at once.

    Blocking here is deliberate, not an oversight: `_start_local_postgres`
    above does the same, and a server that can accept a request before its
    router can answer one is a worse failure than a slower boot. A load
    failure is logged and swallowed -- `laya_decide._get_agent` caches it, so
    the router answers `None` (lexical selection only) for the life of the
    process rather than retrying a GPU that was never going to appear.
    """
    if settings.ai_intent_router != "laya":
        return
    from app.ai.laya_decide import _get_agent

    try:
        loaded = _get_agent() is not None
    except Exception:  # noqa: BLE001 - a warm-up failure must not fail the boot
        logger.exception("Laya intent router failed to warm up")
        return
    if not loaded:
        logger.warning("Laya intent router unavailable; AI_INTENT_ROUTER=laya has no effect")


def _import_kernel() -> None:
    from app.kernel.occt import binding

    binding.symbol("TopoDS")


def _warm_geometry_kernel(importer: Callable[[], None] | None = None) -> threading.Thread | None:
    """Import OCCT in the background so the first geometry request does not pay for it.

    Measured 2026-10-05 on the dev machine: importing OCP costs 2.2 s and ~357 MB the first time
    (numpy and scipy 0.35 s, gmsh 0.16 s, the first BM25 search 0.06 s -- none of those is worth
    warming). A user's first geometry operation after a boot used to carry that 2.2 s.

    **A thread, never the boot.** `_warm_intent_router` blocks on purpose because it must finish
    before a request can be answered; this must not: `/health` and the first request stay
    instant, and a request that arrives while the import is running simply waits on Python's
    own per-module import lock for what is left of it. A failure is logged and swallowed -- the
    first real use would raise the same error in context.
    """
    if not settings.warm_geometry_kernel:
        return None

    def work() -> None:
        started = time.perf_counter()
        try:
            (importer or _import_kernel)()
        except Exception:  # noqa: BLE001 - a warm-up failure must not matter to anyone
            logger.exception("The geometry kernel could not be warmed up")
        else:
            logger.info("geometry kernel warmed in %.1f s", time.perf_counter() - started)

    thread = threading.Thread(target=work, name="kryova-warm-kernel", daemon=True)
    thread.start()
    return thread


def _warn_about_insecure_defaults() -> None:
    """Say out loud, at every startup, what production would refuse.

    `SECRET_KEY` sat at the public default "changeme" long enough to become a
    documented landmine, and the reason is that nothing ever mentioned it: a
    development server started silently and the deployment that forgot the
    environment variable looked exactly the same. Production refuses these
    outright (`Settings._harden_production`); here they are at least audible.
    """
    for problem in settings.insecure_defaults():
        logger.warning("Insecure setting: %s", problem)


def _start_local_postgres() -> None:
    """Start this machine's Postgres if it is configured and down.

    Before the first query, because that query is what used to wait forever on
    a database a reboot had stopped. A failure raises and the server does not
    boot -- see `app/core/local_postgres.py`.
    """
    from app.core.local_postgres import Action, ensure_running

    outcome = ensure_running(
        settings.database_url,
        settings.local_postgres_bin_dir,
        settings.local_postgres_data_dir,
    )
    if outcome.action is Action.STARTED:
        logger.warning("%s -- it was not running", outcome.detail)


def _stop_local_catia_bridge() -> None:
    """Kill a bridge daemon this process started.

    It is a child of ours and it holds a COM attachment to the engineer's CATIA
    session; leaving it running after the server it talks to has gone means a
    process nobody can see, holding a token nobody can use.
    """
    try:
        from app.catia.local_bridge import stop
    except Exception:  # noqa: BLE001 - CATIA support is optional
        return
    try:
        stop()
    except Exception:  # noqa: BLE001 - shutdown must not raise
        logger.warning("Could not stop the local CATIA bridge", exc_info=True)


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


def _resume_waiting_runs() -> None:
    """Start the runs that were waiting for a slot when the last process died (ROAD_TO_10 3.4).

    `_fail_orphaned_jobs` has just failed everything that was queued or running, which freed
    its owners' slots; the *waiting* runs were never handed to the dead queue, so they are
    intact -- and with nothing running there is no run whose end would promote them. A failure
    here is logged and the boot continues: a waiting run that is not resumed now is resumed
    by the next run of its owner's to finish, and a server that will not start is worse.
    """
    from app.api.deps import get_session_scope
    from app.jobs.queue import get_job_queue
    from app.media import get_media_store
    from app.simulation import waiting

    try:
        owners = waiting.resume(get_job_queue(), get_session_scope(), get_media_store())
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Could not resume the simulations that were waiting for a slot")
        return
    if owners:
        logger.info("Resumed the waiting simulations of %d user(s)", owners)


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


#: Above this, a request is called out as slow rather than merely logged. 2 s
#: is not a target -- it is the point past which a person notices waiting, and
#: an endpoint that crosses it wants a reason recorded next to it.
SLOW_REQUEST_MS = 2_000


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One line per request: method, path, status, milliseconds.

    Uvicorn's own access log gives the first three and never the fourth, so
    until now the only latency this service reported was the per-step number
    in the agent's own UI. That is the wrong half: the steps a user watches
    are the slow ones by construction, and the requests that quietly cost
    200 ms each -- the status poll, the conversation list, the CATIA
    heartbeat -- are invisible precisely because nobody is watching them.

    The path is the *route template* where there is one (`/projects/{id}`,
    not `/projects/8f3c...`), so lines for one endpoint aggregate instead of
    being unique per id. Falling back to the raw path keeps a 404 legible.

    Timed with `perf_counter` around `call_next`, which is the whole
    downstream stack including the handler, the database and any tool call it
    makes -- what the client actually waited for.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.warning(
                "%s %s failed after %.0f ms",
                request.method,
                request.url.path,
                elapsed_ms,
                extra={"request_id": getattr(request.state, "request_id", None)},
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        # `Server-Timing` is the standard header for this, and the browser's
        # own network panel renders it -- so the number is in front of whoever
        # is looking at the frontend, not only in a log file on the server.
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        level = logging.WARNING if elapsed_ms >= SLOW_REQUEST_MS else logging.INFO
        logger.log(
            level,
            "%s %s -> %d in %.0f ms%s",
            request.method,
            path,
            response.status_code,
            elapsed_ms,
            " (slow)" if elapsed_ms >= SLOW_REQUEST_MS else "",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
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
    # A header a browser cannot read is a header that does not exist. The two
    # render headers were being set by `api/routes/kernel.py` for nobody: the
    # frontend is a different origin, so without naming them here the fetch that
    # asks for a picture cannot tell an empty frame from a drawn one, and the
    # only alternative left to it is guessing from the compressed byte count.
    # `ETag` is exposed for the same reason — it is the render's own digest, and
    # a client that can read it can tell "the part has not moved" from "the part
    # is unchanged in this view" without another request.
    #
    # The rate-limit set is here for the same reason (ROAD_TO_10 3.2): the page's "you can
    # send again in 12 s" is built from `Retry-After`, which a cross-origin fetch cannot
    # read unless it is named.
    expose_headers=[
        REQUEST_ID_HEADER,
        "ETag",
        "X-Kryova-View",
        "X-Kryova-Blank",
        *EXPOSED_RATE_LIMIT_HEADERS,
    ],
)

app.add_middleware(GZipMiddleware, minimum_size=1024)
# Pure ASGI, so it can write its headers at the start of a *streaming* answer. Outside the
# CORS layer would work as well; it sits here because it reads state the routes write.
app.add_middleware(RateLimitHeadersMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
# Order matters: middleware added last runs first, so `RequestIdMiddleware`
# wraps `AccessLogMiddleware` and every access line already carries the id.
app.add_middleware(AccessLogMiddleware)
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
