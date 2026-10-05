"""What a limit tells the client, and what counts it (ROAD_TO_10 3.1 and 3.2).

`tests/test_rate_limit.py` pins what a limit is counted *against*. This file pins the
other half: that a refusal carries a number a client can act on, that the number is the
same on a success and a refusal, that it reaches a browser on another origin, and that
the backend which counts it is the shared one wherever more than one worker runs.

The Redis tests start a real `redis-server` on a free port and skip when the binary is
absent. A fake of Redis's pipeline would be a copy of what the code's author believed it
does, and the defect this file exists for -- a window that never ended -- lives in what
`EXPIRE` really does.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.api import rate_limit
from app.api.rate_limit import (
    Decision,
    InMemoryBackend,
    RateLimit,
    RateLimiter,
    RateLimitHeadersMiddleware,
    RedisBackend,
    auth_limiter,
    backend_report,
    enforce,
    refuse_an_unshared_limiter_across_workers,
)
from app.core.config import settings


class TestADecisionCarriesTheNumbers:
    def test_an_allowed_request_says_what_is_left(self) -> None:
        backend = InMemoryBackend()

        first = backend.hit("k", 3, 60)
        second = backend.hit("k", 3, 60)

        assert (first.allowed, first.limit, first.remaining) == (True, 3, 2)
        assert (second.allowed, second.remaining) == (True, 1)

    def test_the_refused_request_has_nothing_left_and_a_wait_it_can_use(self) -> None:
        backend = InMemoryBackend()
        for _ in range(2):
            backend.hit("k", 2, 60)

        refused = backend.hit("k", 2, 60)

        assert refused.allowed is False
        assert refused.remaining == 0
        # Whole seconds, rounded up, and never zero: "come back in 0 s" is an
        # instruction to retry at once, into a second refusal.
        assert 1 <= refused.reset_seconds <= 60

    def test_the_wait_is_when_the_oldest_request_leaves_the_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = [1000.0]
        monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
        backend = InMemoryBackend()
        backend.hit("k", 2, 60)
        clock[0] += 20
        backend.hit("k", 2, 60)
        clock[0] += 10

        refused = backend.hit("k", 2, 60)

        # The first request was 30 s ago, so it is the first to leave, in 30 s --
        # not 60 (the window) and not 50 (the newest request's).
        assert refused.reset_seconds == 30

    def test_a_fraction_of_a_second_rounds_up_so_a_retry_is_not_early(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = [50.0]
        monkeypatch.setattr(rate_limit.time, "monotonic", lambda: clock[0])
        backend = InMemoryBackend()
        backend.hit("k", 1, 10)
        clock[0] += 7.5

        # 2.5 s left. Told "2", a client retries half a second early into a second 429.
        assert backend.hit("k", 1, 10).reset_seconds == 3

    def test_the_boolean_form_still_answers_the_same_question(self) -> None:
        backend = InMemoryBackend()
        assert backend.check("k", 1, 60) is True
        assert backend.check("k", 1, 60) is False

    def test_the_headers_are_the_three_ratelimit_fields_and_retry_after_only_on_a_refusal(
        self,
    ) -> None:
        allowed = Decision(allowed=True, limit=20, remaining=7, reset_seconds=33)
        refused = Decision(allowed=False, limit=20, remaining=0, reset_seconds=12)

        assert allowed.headers() == {
            "RateLimit-Limit": "20",
            "RateLimit-Remaining": "7",
            "RateLimit-Reset": "33",
        }
        assert refused.headers()["Retry-After"] == "12"
        assert refused.headers()["RateLimit-Reset"] == "12"


class TestTheHeadersReachTheClient:
    def _app(self, *limits: RateLimit) -> TestClient:
        app = FastAPI()
        app.add_middleware(RateLimitHeadersMiddleware)

        @app.get("/limited", dependencies=[Depends(limit) for limit in limits])
        def limited() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/free")
        def free() -> dict[str, bool]:
            return {"ok": True}

        @app.get("/stream", dependencies=[Depends(limit) for limit in limits])
        def stream() -> StreamingResponse:
            def chunks() -> Iterator[bytes]:
                yield b"data: one\n\n"
                yield b"data: two\n\n"

            return StreamingResponse(chunks(), media_type="text/event-stream")

        return TestClient(app)

    def test_a_served_request_reports_the_budget_and_what_is_left(self) -> None:
        client = self._app(RateLimit("hdr.a", max_requests=5, window_seconds=60))

        response = client.get("/limited")

        assert response.status_code == 200
        assert response.headers["ratelimit-limit"] == "5"
        assert response.headers["ratelimit-remaining"] == "4"
        assert 1 <= int(response.headers["ratelimit-reset"]) <= 60
        assert "retry-after" not in response.headers

    def test_remaining_counts_down_with_every_request(self) -> None:
        client = self._app(RateLimit("hdr.b", max_requests=3, window_seconds=60))

        seen = [client.get("/limited").headers["ratelimit-remaining"] for _ in range(3)]

        assert seen == ["2", "1", "0"]

    def test_the_refusal_carries_retry_after_and_the_same_numbers(self) -> None:
        client = self._app(RateLimit("hdr.c", max_requests=1, window_seconds=60))
        client.get("/limited")

        refused = client.get("/limited")

        assert refused.status_code == 429
        assert refused.headers["ratelimit-remaining"] == "0"
        assert refused.headers["retry-after"] == refused.headers["ratelimit-reset"]
        # One of each: the exception carries the set and so does the middleware, and a
        # header sent twice is one a client may read either way.
        raw = [k for k, _ in refused.headers.raw if k.lower() == b"ratelimit-limit"]
        assert len(raw) == 1

    def test_a_streaming_answer_carries_them_too(self) -> None:
        # The chat turn is a stream, which is where the limit matters most and where a
        # header-adding middleware that buffers the body would defeat the stream.
        client = self._app(RateLimit("hdr.d", max_requests=4, window_seconds=60))

        with client.stream("GET", "/stream") as response:
            body = b"".join(response.iter_bytes())

        assert body == b"data: one\n\ndata: two\n\n"
        assert response.headers["ratelimit-limit"] == "4"
        assert response.headers["ratelimit-remaining"] == "3"

    def test_a_route_nothing_limited_carries_no_budget(self) -> None:
        client = self._app(RateLimit("hdr.e", max_requests=4, window_seconds=60))

        response = client.get("/free")

        assert not [name for name in response.headers if name.lower().startswith("ratelimit")]

    def test_two_limits_on_one_route_report_the_one_nearer_to_running_out(self) -> None:
        roomy = RateLimit("hdr.roomy", max_requests=100, window_seconds=60)
        tight = RateLimit("hdr.tight", max_requests=2, window_seconds=60)
        client = self._app(roomy, tight)

        response = client.get("/limited")

        # "99 left" on the request that spent half of the other limit would send a
        # client to the wall with no warning.
        assert response.headers["ratelimit-limit"] == "2"
        assert response.headers["ratelimit-remaining"] == "1"

    def test_a_refusal_beats_an_allowance_whichever_limit_comes_first(self) -> None:
        tight = RateLimit("hdr.t2", max_requests=1, window_seconds=60)
        roomy = RateLimit("hdr.r2", max_requests=100, window_seconds=60)
        client = self._app(tight, roomy)
        client.get("/limited")

        refused = client.get("/limited")

        assert refused.status_code == 429
        assert refused.headers["ratelimit-limit"] == "1"

    def test_a_budget_that_depends_on_the_caller_is_resolved_per_request(self) -> None:
        # ROAD_TO_10 3.5 needs this: a plan's allowance is known only once the caller is.
        def budget(request: Request) -> int:
            return 1 if request.headers.get("x-plan") == "free" else 3

        client = self._app(RateLimit("hdr.plan", budget, window_seconds=60))

        free = {"x-plan": "free"}
        assert client.get("/limited", headers=free).headers["ratelimit-limit"] == "1"
        assert client.get("/limited", headers=free).status_code == 429
        assert client.get("/limited", headers={"x-plan": "pro"}).headers["ratelimit-limit"] == "3"


class TestTheBrowserMayReadThem:
    def test_the_cors_layer_exposes_the_rate_limit_headers(self) -> None:
        # Cross-origin, a header not named in Access-Control-Expose-Headers does not
        # exist as far as the page's fetch is concerned, and "you can send again in
        # 12 s" is built from Retry-After.
        from app.main import app

        client = TestClient(app)
        origin = settings.cors_origins[0]

        response = client.get("/health", headers={"Origin": origin})

        exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
        assert {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "retry-after"} <= exposed

    def test_the_real_app_adds_them_to_a_limited_route(self, client: TestClient) -> None:
        # Through the whole stack: the middleware is installed on the application, not
        # just on a test app that happens to include it.
        response = client.post(
            "/api/v1/auth/login",
            data={"username": "nobody@kryova.dev", "password": "wrong-password"},
        )

        assert response.status_code == 401
        assert int(response.headers["ratelimit-limit"]) >= 10
        assert "ratelimit-remaining" in response.headers


class TestEnforceRecordsBeforeItRefuses:
    def test_a_refusal_raised_by_enforce_still_carries_the_budget(self) -> None:
        from fastapi import HTTPException

        limiter = RateLimiter(max_requests=1, window_seconds=60)
        request = type("R", (), {"state": type("S", (), {})()})()
        enforce(request, limiter, "k")  # type: ignore[arg-type]

        with pytest.raises(HTTPException) as refused:
            enforce(request, limiter, "k", detail="Slow down.")  # type: ignore[arg-type]

        assert refused.value.status_code == 429
        assert refused.value.detail == "Slow down."
        assert refused.value.headers is not None
        assert refused.value.headers["Retry-After"]
        assert request.state.rate_limit.allowed is False  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# A real Redis
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def redis_url(tmp_path: Path) -> Iterator[str]:
    binary = shutil.which("redis-server")
    if binary is None:
        pytest.skip("redis-server is not installed")
    port = _free_port()
    process = subprocess.Popen(
        [binary, "--port", str(port), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no",
         "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                RedisBackend(url)
                break
            except ConnectionError:
                if time.monotonic() > deadline:
                    pytest.skip("redis-server did not start")
                time.sleep(0.1)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


class TestARealRedisCountsOnAFixedWindow:
    def test_the_budget_is_spent_and_reported(self, redis_url: str) -> None:
        backend = RedisBackend(redis_url)

        decisions = [backend.hit("budget", 3, 60) for _ in range(4)]

        assert [d.allowed for d in decisions] == [True, True, True, False]
        assert [d.remaining for d in decisions] == [2, 1, 0, 0]
        assert all(1 <= d.reset_seconds <= 60 for d in decisions)

    def test_a_client_that_keeps_asking_does_not_hold_its_own_window_open(
        self, redis_url: str
    ) -> None:
        # The defect this replaced: EXPIRE ran on every hit, so each retry pushed the
        # expiry out and a client with no Retry-After -- retrying at once -- stayed
        # refused for as long as it kept asking. The window is anchored to the first
        # request and nothing a refused request does can move it.
        backend = RedisBackend(redis_url)
        started = time.monotonic()
        first = backend.hit("persistent", 1, 2)
        assert first.allowed

        admitted_again = False
        while time.monotonic() - started < 4:
            if backend.hit("persistent", 1, 2).allowed:
                admitted_again = True
                break
            time.sleep(0.2)

        assert admitted_again, "a refused client's retries kept the window from ever ending"
        assert time.monotonic() - started < 3.5

    def test_the_wait_shrinks_as_the_window_runs_down(self, redis_url: str) -> None:
        backend = RedisBackend(redis_url)
        backend.hit("shrinks", 1, 5)
        early = backend.hit("shrinks", 1, 5).reset_seconds
        time.sleep(1.3)
        later = backend.hit("shrinks", 1, 5).reset_seconds

        assert later < early

    def test_a_counter_with_no_expiry_is_given_one_rather_than_blocking_for_ever(
        self, redis_url: str
    ) -> None:
        import redis

        client = redis.Redis.from_url(redis_url, decode_responses=True)
        client.set("ratelimit:stuck", 50)  # no TTL: e.g. a crash between INCR and EXPIRE
        backend = RedisBackend(redis_url)

        decision = backend.hit("stuck", 5, 30)

        assert decision.allowed is False
        assert 0 < client.ttl("ratelimit:stuck") <= 30

    def test_reset_clears_one_key(self, redis_url: str) -> None:
        backend = RedisBackend(redis_url)
        backend.hit("a", 1, 60)
        backend.hit("b", 1, 60)

        backend.reset("a")

        assert backend.hit("a", 1, 60).allowed
        assert not backend.hit("b", 1, 60).allowed


# ---------------------------------------------------------------------------
# Several workers, one budget (ROAD_TO_10 3.1)
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `auth_limiter` choose its backend again under the settings a test patches."""
    monkeypatch.setattr(auth_limiter, "_backend", None)


class TestSeveralWorkersNeedASharedLimiter:
    def test_a_running_redis_is_reported_as_shared(
        self, monkeypatch: pytest.MonkeyPatch, redis_url: str, fresh_backend: None
    ) -> None:
        monkeypatch.setattr(settings, "redis_url", redis_url)

        report = backend_report()

        assert (report.kind, report.shared) == ("redis", True)

    def test_a_url_that_is_set_and_not_answering_is_reported_as_memory(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{_free_port()}/0")

        report = backend_report()

        assert (report.kind, report.shared) == ("memory", False)
        assert "could not be reached" in report.detail

    def test_no_url_is_reported_as_memory(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        monkeypatch.setattr(settings, "redis_url", None)

        assert backend_report().shared is False

    def test_startup_refuses_several_workers_on_an_unreachable_redis(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        # The configuration check passes -- a URL *is* set. The fact is that nothing
        # answers, and every worker would enforce its own copy of every limit.
        monkeypatch.setattr(settings, "environment", "production")
        monkeypatch.setattr(settings, "web_concurrency", 4)
        monkeypatch.setattr(settings, "redis_url", f"redis://127.0.0.1:{_free_port()}/0")

        with pytest.raises(RuntimeError, match="4 workers.*4 times"):
            refuse_an_unshared_limiter_across_workers()

    def test_startup_accepts_several_workers_on_a_live_redis(
        self, monkeypatch: pytest.MonkeyPatch, redis_url: str, fresh_backend: None
    ) -> None:
        monkeypatch.setattr(settings, "environment", "production")
        monkeypatch.setattr(settings, "web_concurrency", 4)
        monkeypatch.setattr(settings, "redis_url", redis_url)

        refuse_an_unshared_limiter_across_workers()

    def test_one_worker_keeps_its_in_process_limiter_in_production(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        # The desktop app is one process; Redis there is a service to install for nothing.
        monkeypatch.setattr(settings, "environment", "production")
        monkeypatch.setattr(settings, "web_concurrency", 1)
        monkeypatch.setattr(settings, "redis_url", None)

        refuse_an_unshared_limiter_across_workers()

    def test_development_is_left_alone_whatever_the_worker_count(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        monkeypatch.setattr(settings, "environment", "development")
        monkeypatch.setattr(settings, "web_concurrency", 8)
        monkeypatch.setattr(settings, "redis_url", None)

        refuse_an_unshared_limiter_across_workers()

    def test_the_application_will_not_start_on_the_wrong_limiter(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None
    ) -> None:
        # A check nothing calls is a comment. This enters the application's own lifespan,
        # so a refused boot is a refused boot and not a function that merely exists. The
        # other startup steps are stubbed: they need a database and a job queue, and none
        # of them is what is being asked.
        import asyncio

        from app import main

        for step in (
            "_warn_about_insecure_defaults",
            "_start_local_postgres",
            "_fail_orphaned_jobs",
            "_resume_waiting_runs",
            "_warm_intent_router",
        ):
            monkeypatch.setattr(main, step, lambda: None)
        monkeypatch.setattr(settings, "environment", "production")
        monkeypatch.setattr(settings, "web_concurrency", 2)
        monkeypatch.setattr(settings, "redis_url", None)

        async def start() -> None:
            async with main.lifespan(main.app):
                pass

        with pytest.raises(RuntimeError, match="2 workers"):
            asyncio.run(start())

    def test_the_application_says_which_limiter_it_started_with(
        self, monkeypatch: pytest.MonkeyPatch, fresh_backend: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from app import main

        monkeypatch.setattr(settings, "environment", "development")
        monkeypatch.setattr(settings, "redis_url", None)

        with caplog.at_level(logging.INFO, logger="app.main"):
            main._check_rate_limit_backend()

        assert any("rate limits: memory" in record.getMessage() for record in caplog.records)
