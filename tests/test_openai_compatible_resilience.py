"""The shared hosted-model transport: retries, a bounded repair, and streaming.

Offline: `httpx.post` and `httpx.stream` are replaced and `time.sleep` is
patched, so nothing waits and nothing leaves the machine. What each class pins is
a rule about *spending*: a hosted model bills every token, so a retry that
repeats forever, a repair that asks a third time, or a stream that is re-issued
after it was shown are all money, not just latency.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.provider import Finished, LLMBusy, LLMError, LLMUnavailable, TextDelta
from app.ai.providers import openai_compatible as module
from app.ai.providers.openai_compatible import (
    HTTP_ATTEMPTS,
    STRUCTURED_ATTEMPTS,
    OpenAICompatibleProvider,
)


class Shape(BaseModel):
    force_n: float
    axis: str


GOOD = '{"force_n": 5, "axis": "z"}'


def _provider(**kwargs: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider("http://x/v1", "k", "m", 5.0, **kwargs)


def _ok(content: str = GOOD, finish: str = "stop", usage: tuple[int, int] = (10, 5)) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


class _Script:
    """Answers each `httpx.post` with the next scripted thing: a body, a status, or an exception."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
        self.requests.append(json)
        step = self.steps.pop(0)
        request = httpx.Request("POST", url)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, int):
            return httpx.Response(step, text=f"status {step}", request=request)
        if isinstance(step, tuple):
            status, headers_, text = step
            return httpx.Response(status, headers=headers_, text=text, request=request)
        return httpx.Response(200, json=step, request=request)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr(module.time, "sleep", waited.append)
    return waited


def _complete(prov: OpenAICompatibleProvider, script: _Script, mp: pytest.MonkeyPatch) -> Any:
    mp.setattr(httpx, "post", script)
    return prov.complete(system="s", user="u", schema=Shape, effort="low", max_tokens=100)


class TestTransientFailuresAreRetried:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_a_shed_request_is_retried_and_then_succeeds(
        self, status: int, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(status, _ok())
        assert _complete(_provider(), script, monkeypatch).value.axis == "z"
        assert len(script.requests) == 2
        assert len(sleeps) == 1

    def test_a_connection_error_is_retried(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(httpx.ConnectError("reset"), _ok())
        assert _complete(_provider(), script, monkeypatch).value.axis == "z"

    def test_the_attempts_are_bounded(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every attempt is a request; an unbounded retry is an unbounded bill."""
        script = _Script(*([503] * 10))
        with pytest.raises(LLMError, match="503"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == HTTP_ATTEMPTS

    def test_retry_after_is_honoured_but_capped(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script((429, {"retry-after": "3"}, "slow down"), (429, {"retry-after": "999"}, ""), _ok())
        _complete(_provider(), script, monkeypatch)
        assert sleeps == [3.0, module.MAX_RETRY_WAIT_S]

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_a_client_error_is_not_retried(
        self, status: int, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It would fail identically forever."""
        script = _Script(status, _ok())
        with pytest.raises(LLMError):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 1
        assert sleeps == []

    def test_a_timeout_is_not_retried(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The caller's patience is spent; a second wait doubles it."""
        script = _Script(httpx.ReadTimeout("slow"), _ok())
        with pytest.raises(LLMError, match="did not respond"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 1

    def test_a_body_that_is_not_json_is_a_readable_error(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script((200, {}, "<html>gateway</html>"))
        with pytest.raises(LLMError, match="not JSON"):
            _complete(_provider(), script, monkeypatch)


class TestTheKeyAndTheBalanceAreSaidInWords:
    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_unavailable_and_not_retried(
        self, status: int, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(status, _ok())
        with pytest.raises(LLMUnavailable, match="key was rejected"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 1

    def test_no_balance_is_unavailable_and_says_to_top_up(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(402, _ok())
        with pytest.raises(LLMUnavailable, match="no balance"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 1


class TestAnInterruptedGenerationIsNotAnAnswer:
    @pytest.mark.parametrize("reason", ["insufficient_system_resource", "aborted"])
    def test_it_is_asked_again(
        self, reason: str, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_ok('{"force_n":', reason), _ok())
        assert _complete(_provider(), script, monkeypatch).value.axis == "z"

    def test_it_is_reported_after_the_last_attempt(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(*[_ok("", "aborted") for _ in range(HTTP_ATTEMPTS)])
        with pytest.raises(LLMError, match="short of capacity"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == HTTP_ATTEMPTS


class TestStructuredOutputIsRepairedOnceAndNoMore:
    def test_an_empty_answer_is_asked_again_with_the_problem_named(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DeepSeek documents that JSON mode "may occasionally return empty content"."""
        script = _Script(_ok(""), _ok())
        result = _complete(_provider(), script, monkeypatch)
        assert result.value.force_n == 5
        repair = script.requests[1]["messages"][-1]
        assert repair["role"] == "user"
        assert "empty" in repair["content"]

    def test_an_invalid_answer_is_asked_again(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_ok('{"force_n": "lots"}'), _ok())
        assert _complete(_provider(), script, monkeypatch).value.axis == "z"
        assert "does not match" in script.requests[1]["messages"][-1]["content"]

    def test_a_fenced_answer_needs_no_second_call(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_ok(f"```json\n{GOOD}\n```"))
        assert _complete(_provider(), script, monkeypatch).value.axis == "z"
        assert len(script.requests) == 1

    def test_it_never_asks_a_third_time(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A third attempt draws from the same distribution as the second."""
        script = _Script(_ok(""), _ok(""), _ok(GOOD))
        with pytest.raises(LLMError, match="empty"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == STRUCTURED_ATTEMPTS

    def test_the_usage_of_every_attempt_is_billed(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry is spend; a ledger that counted only the winner would under-report."""
        script = _Script(_ok("", usage=(100, 7)), _ok(usage=(120, 9)))
        usage = _complete(_provider(), script, monkeypatch).usage
        assert (usage.prompt_tokens, usage.completion_tokens) == (220, 16)

    def test_a_truncated_answer_is_not_repaired(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Asking again at the same cap spends the same tokens to be cut off again."""
        script = _Script(_ok('{"force_n":', "length"), _ok())
        with pytest.raises(LLMError, match="output limit"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 1


class TestAnEndpointWithoutJsonSchemaIsLearnedOnce:
    REJECTION = (400, {}, '{"error": {"message": "This response_format type is unavailable now"}}')

    def test_it_falls_back_to_json_object_and_remembers(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider()
        script = _Script(self.REJECTION, _ok(), _ok())
        monkeypatch.setattr(httpx, "post", script)
        for _ in range(2):
            prov.complete(system="s", user="u", schema=Shape, effort="low", max_tokens=100)
        kinds = [r["response_format"]["type"] for r in script.requests]
        assert kinds == ["json_schema", "json_object", "json_object"]

    def test_neither_format_accepted_is_a_clear_error_not_a_loop(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(self.REJECTION, self.REJECTION, self.REJECTION)
        with pytest.raises(LLMError, match="neither json_schema nor json_object"):
            _complete(_provider(), script, monkeypatch)
        assert len(script.requests) == 2


class TestLookResendsNothingItDoesNotHaveTo:
    def test_the_pictures_go_before_the_question_as_data_uris(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_ok())
        monkeypatch.setattr(httpx, "post", script)
        _provider(vision_model="v").look(
            system="s", user="u", images=[b"\x89PNG\r\n\x1a\nabc"], schema=Shape,
            effort="low", max_tokens=100,
        )
        sent = script.requests[0]
        assert sent["model"] == "v"
        parts = sent["messages"][-1]["content"]
        assert parts[0]["type"] == "image_url"
        assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert parts[-1]["type"] == "text"

    def test_an_empty_image_list_costs_no_request(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_ok())
        monkeypatch.setattr(httpx, "post", script)
        with pytest.raises(LLMError, match="at least one image"):
            _provider().look(
                system="s", user="u", images=[], schema=Shape, effort="low", max_tokens=100
            )
        assert script.requests == []


# ---------------------------------------------------------------------------
# Streaming.
# ---------------------------------------------------------------------------


def _sse(*events: Any) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines.append(event if isinstance(event, str) else "data: " + json.dumps(event))
        lines.append("")
    return lines


def _delta(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def _finish(reason: str = "stop") -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


class _Stream:
    """Replaces `httpx.stream`, yielding scripted lines or raising where told."""

    def __init__(self, lines: list[str] | None = None, *, status: int = 200, fail_after: bool = False) -> None:
        self.lines = lines or []
        self.status = status
        self.fail_after = fail_after
        self.payloads: list[dict[str, Any]] = []

    @contextmanager
    def __call__(self, method: str, url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Iterator[Any]:
        self.payloads.append(json)
        request = httpx.Request(method, url)
        response = httpx.Response(self.status, text="rejected", request=request)
        lines = self.lines
        fail_after = self.fail_after

        class _Live:
            def raise_for_status(self) -> None:
                response.raise_for_status()

            def iter_lines(self) -> Iterator[str]:
                yield from lines
                if fail_after:
                    raise httpx.ReadError("connection dropped")

        yield _Live()


def _collect(prov: OpenAICompatibleProvider) -> list[Any]:
    return list(
        prov.stream_chat(system="s", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=100)
    )


class TestAStreamIsFoldedBackIntoOneTurn:
    def test_text_arrives_as_deltas_and_the_turn_is_the_answer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(
            _sse(
                _delta(content="Hel"),
                _delta(content="lo"),
                _finish(),
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
                "data: [DONE]",
            )
        )
        monkeypatch.setattr(httpx, "stream", stream)
        events = _collect(_provider())
        assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
        finished = events[-1]
        assert isinstance(finished, Finished)
        assert finished.turn.text == "Hello"
        assert (finished.turn.usage.prompt_tokens, finished.turn.usage.completion_tokens) == (12, 3)

    def test_tool_call_fragments_are_assembled_by_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(
            _sse(
                _delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "f", "arguments": ""}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": '{"a":'}}]),
                _delta(tool_calls=[{"index": 0, "function": {"arguments": " 1}"}}]),
                _delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "g", "arguments": "{}"}}]),
                _finish("tool_calls"),
            )
        )
        monkeypatch.setattr(httpx, "stream", stream)
        turn = _collect(_provider())[-1].turn
        assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
            ("c1", "f", {"a": 1}),
            ("c2", "g", {}),
        ]

    def test_a_repeated_name_is_not_doubled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = _Stream(
            _sse(
                _delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "f", "arguments": "{"}}]),
                _delta(tool_calls=[{"index": 0, "function": {"name": "f", "arguments": "}"}}]),
                _finish("tool_calls"),
            )
        )
        monkeypatch.setattr(httpx, "stream", stream)
        turn = _collect(_provider())[-1].turn
        assert turn.tool_calls[0].name == "f"

    def test_reasoning_is_collected_for_a_vendor_that_keeps_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai.providers.deepseek import DeepSeekProvider

        stream = _Stream(
            _sse(
                _delta(reasoning_content="Think "),
                _delta(reasoning_content="first."),
                _delta(content="Done."),
                _finish(),
            )
        )
        monkeypatch.setattr(httpx, "stream", stream)
        prov = DeepSeekProvider(api_key="k", model="m", timeout_seconds=5)
        turn = _collect(prov)[-1].turn
        assert turn.reasoning == "Think first."
        assert turn.text == "Done."

    def test_keepalives_and_unreadable_lines_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(
            _sse(": keep-alive", "data: {not json", _delta(content="ok"), _finish())
        )
        monkeypatch.setattr(httpx, "stream", stream)
        assert _collect(_provider())[-1].turn.text == "ok"

    def test_usage_is_requested_on_the_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without it a streamed turn reports zero tokens and the ledger misses the bill."""
        stream = _Stream(_sse(_delta(content="ok"), _finish()))
        monkeypatch.setattr(httpx, "stream", stream)
        _collect(_provider())
        assert stream.payloads[0]["stream"] is True
        assert stream.payloads[0]["stream_options"] == {"include_usage": True}


class TestABrokenStreamFallsBackToOneWholeRequest:
    def test_a_stream_dropped_midway_is_repeated_whole_once(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(_sse(_delta(content="Hel")), fail_after=True)
        whole = _Script(_ok("Hello there"))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        events = _collect(_provider())
        assert isinstance(events[-1], Finished)
        assert events[-1].turn.text == "Hello there"
        assert len(whole.requests) == 1

    def test_a_stream_with_no_finish_is_repeated_whole(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(_sse(_delta(content="Hel")))
        whole = _Script(_ok("Hello there"))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        assert _collect(_provider())[-1].turn.text == "Hello there"

    @pytest.mark.parametrize("reason", ["aborted", "insufficient_system_resource"])
    def test_an_interrupted_stream_is_not_an_answer(
        self, reason: str, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(_sse(_delta(content="Hel"), _finish(reason)))
        whole = _Script(_ok("Hello there"))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        assert _collect(_provider())[-1].turn.text == "Hello there"

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_a_server_that_rejects_streaming_is_not_asked_again(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider()
        stream = _Stream(status=status)
        whole = _Script(_ok("one"), _ok("two"))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        assert _collect(prov)[-1].turn.text == "one"
        assert prov._streaming_supported is False
        assert _collect(prov)[-1].turn.text == "two"
        assert len(stream.payloads) == 1, "the second turn must not try to stream"

    def test_a_rejected_key_surfaces_in_words_from_the_whole_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = _Stream(status=401)
        whole = _Script(401)
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        with pytest.raises(LLMUnavailable, match="key was rejected"):
            _collect(_provider())

    def test_a_transient_failure_does_not_switch_streaming_off_for_good(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = _provider()
        stream = _Stream(status=503)
        whole = _Script(_ok("one"))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)
        _collect(prov)
        assert prov._streaming_supported is True


class TestAProviderThatDoesNotStreamAnswersWhole:
    def test_nvidia_is_not_asked_to_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ai.providers.nvidia import NvidiaProvider

        def refuse(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("NVIDIA's chunking is unmeasured; it must not stream")

        whole = _Script(_ok("hi"))
        monkeypatch.setattr(httpx, "stream", refuse)
        monkeypatch.setattr(httpx, "post", whole)
        prov = NvidiaProvider(api_key="k", model="m", timeout_seconds=5)
        events = _collect(prov)
        assert len(events) == 1
        assert isinstance(events[0], Finished)
        assert "stream" not in whole.requests[0]


class TestThePromptCacheIsReportedWhenTheServerSaysSo:
    def test_hits_are_logged_for_a_whole_request(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _ok()
        body["usage"].update({"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100})
        monkeypatch.setattr(httpx, "post", _Script(body))
        with caplog.at_level("INFO", logger=module.logger.name):
            _complete(_provider(), _Script(body), monkeypatch)
        assert any("900 of 1000" in r.getMessage() for r in caplog.records)

    def test_a_vendor_that_reports_nothing_logs_nothing(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with caplog.at_level("INFO", logger=module.logger.name):
            _complete(_provider(), _Script(_ok()), monkeypatch)
        assert not any("prompt cache" in r.getMessage() for r in caplog.records)


class TestARetryThatRunsOutIsBusyNotBroken:
    """ROAD_TO_10 3.6: "not now" is its own type, because the agent does something else with it.

    The transport already retried these. What it raised afterwards was a bare `LLMError`
    reading "Chat completion failed (503)", which the loop could not tell from a fault -- so
    a busy service ended a turn in an error and a busy service is the one failure where the
    right answer is "press Continue".
    """

    @pytest.mark.parametrize("status", [429, 502, 503, 504])
    def test_a_busy_status_that_outlasts_the_retries_is_busy(
        self, status: int, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(*([status] * 10))

        with pytest.raises(LLMBusy, match=str(status)):
            _complete(_provider(), script, monkeypatch)

        assert len(script.requests) == HTTP_ATTEMPTS

    def test_an_internal_error_is_not_called_busy(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Telling a user "it was only busy" about a fault in the service is a guess.
        script = _Script(*([500] * 10))

        with pytest.raises(LLMError) as raised:
            _complete(_provider(), script, monkeypatch)

        assert not isinstance(raised.value, LLMBusy)

    @pytest.mark.parametrize("status", [400, 401, 402, 404])
    def test_a_client_or_account_error_is_not_called_busy(
        self, status: int, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(status)

        with pytest.raises(LLMError) as raised:
            _complete(_provider(), script, monkeypatch)

        assert not isinstance(raised.value, LLMBusy)

    def test_the_providers_own_wait_is_carried_uncapped(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The transport caps how long it *sleeps*; the user is told what the service asked.
        script = _Script(*[(429, {"retry-after": "999"}, "") for _ in range(HTTP_ATTEMPTS)])

        with pytest.raises(LLMBusy) as raised:
            _complete(_provider(), script, monkeypatch)

        assert raised.value.retry_after_s == 999.0
        assert max(sleeps) == module.MAX_RETRY_WAIT_S

    @pytest.mark.parametrize("header", [{}, {"retry-after": "soon"}, {"retry-after": "-4"}])
    def test_no_usable_wait_is_none_and_not_a_guess(
        self, header: dict[str, str], sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(*[(503, header, "") for _ in range(HTTP_ATTEMPTS)])

        with pytest.raises(LLMBusy) as raised:
            _complete(_provider(), script, monkeypatch)

        assert raised.value.retry_after_s is None

    def test_a_generation_cut_short_for_capacity_is_busy(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(*[_ok("", "aborted") for _ in range(HTTP_ATTEMPTS)])

        with pytest.raises(LLMBusy, match="short of capacity"):
            _complete(_provider(), script, monkeypatch)

    def test_busy_is_still_an_llm_error_so_every_older_handler_keeps_working(self) -> None:
        assert issubclass(LLMBusy, LLMError)
        assert not issubclass(LLMBusy, LLMUnavailable)

    def test_a_stream_whose_whole_request_fallback_is_refused_is_busy(
        self, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The streaming call fails, the one repeat as a whole request is shed on every
        # attempt, and what surfaces from `stream_chat` is the same typed answer.
        stream = _Stream(status=503)
        whole = _Script(*([503] * 10))
        monkeypatch.setattr(httpx, "stream", stream)
        monkeypatch.setattr(httpx, "post", whole)

        with pytest.raises(LLMBusy):
            _collect(_provider())
