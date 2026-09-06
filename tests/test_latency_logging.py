"""Every request and every agent step reports how long it took.

A number nobody prints is a number nobody optimises. The clearest evidence is
the GPU offload defect found on 2026-09-06: the model had been running at 43%
of its speed for weeks, with one layer of thirty-four left on the CPU, and
nothing anywhere reported a token rate -- so every turn merely *felt* slow and
there was nothing to point at. Two lines of logging would have made it
obvious the first day.

Three timings, because they are optimised in three different places:

* **the endpoint** -- what the browser waited for, including the database;
* **the model** -- generation speed from Ollama's own counters, which is the
  only honest measure of whether the model is on the GPU (wall time mixes in
  the prompt, the queue and the network);
* **the tools** -- per call, named, because a slow turn is usually one COM
  round trip to CATIA and not the model at all.

`Server-Timing` is set as well as logged: it is the standard header for this
and the browser's own network panel renders it, so the number is in front of
whoever is looking at the frontend rather than only in a file on the server.

Offline: no database, no Ollama, no CATIA.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import SLOW_REQUEST_MS, AccessLogMiddleware

AGENT_SOURCE = (Path(__file__).resolve().parent.parent / "app" / "ai" / "agent.py").read_text(
    encoding="utf-8"
)
OLLAMA_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "ai" / "providers" / "ollama.py"
).read_text(encoding="utf-8")


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(AccessLogMiddleware)

    @application.get("/fast")
    def fast() -> dict[str, str]:
        return {"ok": "yes"}

    @application.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    @application.get("/boom")
    def boom() -> dict[str, str]:
        raise RuntimeError("no")

    return application


class TestTheEndpointLine:
    def test_the_response_carries_its_own_duration(self, app: FastAPI) -> None:
        response = TestClient(app).get("/fast")
        assert response.headers["Server-Timing"].startswith("app;dur=")
        assert float(response.headers["Server-Timing"].split("=")[1]) >= 0.0

    def test_the_line_names_method_path_status_and_milliseconds(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="app.main"):
            TestClient(app).get("/fast")
        line = next(r.getMessage() for r in caplog.records if "/fast" in r.getMessage())
        assert "GET" in line
        assert "200" in line
        assert "ms" in line

    def test_it_logs_the_route_template_not_the_id(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Otherwise every request to one endpoint is its own unique line and
        nothing can be aggregated -- which is most of the value."""
        with caplog.at_level(logging.INFO, logger="app.main"):
            TestClient(app).get("/items/8f3c9a")
        # Only this logger's own lines: the test client's httpx logs the real
        # URL, which is correct for httpx and not what is being asserted here.
        messages = [r.getMessage() for r in caplog.records if r.name == "app.main"]
        assert any("/items/{item_id}" in m for m in messages)
        assert not any("8f3c9a" in m for m in messages)

    def test_a_slow_request_is_a_warning(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A log at INFO among thousands is not a signal. The point past which
        a person notices waiting gets a level of its own."""
        monkeypatch.setattr("app.main.SLOW_REQUEST_MS", 0)
        with caplog.at_level(logging.INFO, logger="app.main"):
            TestClient(app).get("/fast")
        record = next(r for r in caplog.records if "/fast" in r.getMessage())
        assert record.levelno == logging.WARNING
        assert "(slow)" in record.getMessage()

    def test_the_threshold_is_a_human_number(self) -> None:
        assert SLOW_REQUEST_MS == 2_000

    def test_a_failed_request_still_reports_its_time(
        self, app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The most interesting latency there is: how long the user waited to
        be told it did not work."""
        with caplog.at_level(logging.INFO, logger="app.main"), pytest.raises(RuntimeError):
            TestClient(app, raise_server_exceptions=True).get("/boom")
        assert any("failed after" in r.getMessage() for r in caplog.records)


class TestTheAgentLine:
    def _loop(self) -> str:
        tree = ast.parse(AGENT_SOURCE)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "stream_agent"
        )
        return ast.get_source_segment(AGENT_SOURCE, node) or ""

    def test_the_model_and_the_tools_are_timed_apart(self) -> None:
        """A total says a turn was slow and nothing about which half to look
        at -- and they are fixed in completely different places: GPU offload
        on one side, COM round trips on the other."""
        body = self._loop()
        assert "thinking_ms" in body
        assert "step_timings" in body

    def test_each_tool_is_named_with_its_own_time(self) -> None:
        body = self._loop()
        assert "step_timings.append((call.name" in body
        assert "{name} {ms:.0f}ms" in body

    def test_it_is_logged_at_info_not_debug(self) -> None:
        """DEBUG is off in every deployment that matters, which is where the
        number is needed."""
        body = self._loop()
        assert "logger.info(" in body
        assert "agent step %d/%d" in body

    def test_the_prompt_size_is_on_the_same_line(self) -> None:
        """Prompt tokens drive both the cost and the speed, and a turn that
        got slower usually got longer first."""
        assert "prompt tokens" in self._loop()


class TestTheModelLine:
    def test_the_rate_comes_from_ollamas_own_counters(self) -> None:
        """Wall time mixes in the prompt, the queue and the network. A slow
        turn on a fast model has to look different from a fast turn on a slow
        one, or the offload regression is invisible again."""
        assert "eval_count" in OLLAMA_SOURCE
        assert "eval_duration" in OLLAMA_SOURCE
        assert "tok/s" in OLLAMA_SOURCE

    def test_a_response_with_no_counters_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A missing counter is not an error on the agent's path."""
        from app.ai.providers.ollama import _log_generation_speed

        with caplog.at_level(logging.INFO, logger="app.ai.providers.ollama"):
            _log_generation_speed("m", {}, 1.0)
            _log_generation_speed("m", {"eval_count": 0, "eval_duration": 0}, 1.0)
            _log_generation_speed("m", {"eval_count": "x", "eval_duration": None}, 1.0)
        assert not [r for r in caplog.records if "tok/s" in r.getMessage()]

    def test_the_rate_is_tokens_over_ollamas_own_duration(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app.ai.providers.ollama import _log_generation_speed

        with caplog.at_level(logging.INFO, logger="app.ai.providers.ollama"):
            # 120 tokens in 2 seconds is 60 tok/s, whatever the wall clock says.
            _log_generation_speed("qwen3.5:9b", {"eval_count": 120, "eval_duration": 2_000_000_000}, 9.9)
        line = next(r.getMessage() for r in caplog.records if "tok/s" in r.getMessage())
        assert "60.0 tok/s" in line
        assert "120 tokens" in line

    def test_it_runs_on_the_agent_path(self) -> None:
        """A helper nothing calls measures nothing."""
        tree = ast.parse(OLLAMA_SOURCE)
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "chat"
        )
        body = ast.get_source_segment(OLLAMA_SOURCE, node) or ""
        assert "_log_generation_speed(" in body


class TestItDoesNotCostWhatItMeasures:
    def test_the_middleware_adds_no_database_work(self) -> None:
        """A timing middleware that queries anything is a tax on every request
        including the ones it exists to find."""
        tree = ast.parse((Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8"))
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == "AccessLogMiddleware"
        )
        body = ast.get_source_segment(
            (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8"),
            node,
        ) or ""
        for forbidden in ("Session", "db.", "select(", "commit"):
            assert forbidden not in body, f"the access log is doing {forbidden} work"

    def test_it_uses_a_monotonic_clock(self) -> None:
        """`time.time()` goes backwards over an NTP correction and reports a
        negative duration, which reads as a bug in the endpoint."""
        source = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(
            encoding="utf-8"
        )
        assert "time.perf_counter()" in source


def _unused(value: Any) -> None:  # pragma: no cover - keeps `Any` honest
    return None
