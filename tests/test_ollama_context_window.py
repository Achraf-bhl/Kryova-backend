"""The context window has to be set explicitly, every time.

Ollama does not size the window to the model. A request that omits `num_ctx`
gets 4096 tokens whatever the model supports, and a longer prompt is truncated
from the front with no error and no flag in the response.

`OllamaProvider.chat` omitted it. The agent's prompt starts near 6k tokens
before the user types anything -- a ~2k system prompt plus ~4k of schemas for 26
tools -- so every agent turn was running on a transcript the model could only
half see. Measured on gpt-oss:20b before the fix: a 8997-token transcript
arrived as 3900 and the model returned empty content and no tool calls.

The symptoms were blamed on the model being small. They were this.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai.provider import LLMError
from app.ai.providers.ollama import (
    MAX_CONTEXT_WINDOW,
    MIN_CONTEXT_WINDOW,
    OLLAMA_DEFAULT_NUM_CTX,
    OllamaProvider,
)


class _Recorder:
    """Stands in for Ollama, remembering the payloads it was sent."""

    def __init__(self, *, context_length: int | None = 131_072, prompt_tokens: int = 100) -> None:
        self.context_length = context_length
        self.prompt_tokens = prompt_tokens
        self.chat_payloads: list[dict[str, Any]] = []
        self.show_calls = 0

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        request = httpx.Request("POST", url)
        if url.endswith("/api/show"):
            self.show_calls += 1
            info = (
                {"gptoss.context_length": self.context_length}
                if self.context_length is not None
                else {}
            )
            return httpx.Response(200, json={"model_info": info}, request=request)
        self.chat_payloads.append(json)
        return httpx.Response(
            200,
            request=request,
            json={
                "message": {"content": "ok", "tool_calls": []},
                "prompt_eval_count": self.prompt_tokens,
                "eval_count": 5,
            },
        )


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    stub = _Recorder()
    monkeypatch.setattr(httpx, "post", stub.post)
    return stub


def _provider() -> OllamaProvider:
    return OllamaProvider("http://localhost:11434", "gpt-oss:20b", 60.0)


def _chat(provider: OllamaProvider) -> Any:
    return provider.chat(
        system="s", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=1_000
    )


class TestNumCtxIsAlwaysSent:
    def test_chat_sets_it(self, recorder: _Recorder) -> None:
        _chat(_provider())
        options = recorder.chat_payloads[0]["options"]
        assert "num_ctx" in options, (
            "without num_ctx Ollama truncates the agent's prompt to 4096 tokens and reports nothing"
        )

    def test_it_beats_ollamas_default(self, recorder: _Recorder) -> None:
        _chat(_provider())
        assert recorder.chat_payloads[0]["options"]["num_ctx"] > OLLAMA_DEFAULT_NUM_CTX

    def test_complete_sets_it_too(self, recorder: _Recorder) -> None:
        from pydantic import BaseModel

        class Answer(BaseModel):
            ok: bool

        with pytest.raises(LLMError):
            # The stub answers "ok", which does not validate against `Answer`.
            # The payload is recorded before that, and it is what is under test.
            _provider().complete(
                system="s", user="u", schema=Answer, effort="high", max_tokens=1_000
            )
        assert "num_ctx" in recorder.chat_payloads[0]["options"]

    def test_complete_leaves_room_for_the_answer_as_well(self, recorder: _Recorder) -> None:
        from pydantic import BaseModel

        class Answer(BaseModel):
            ok: bool

        with pytest.raises(LLMError):
            _provider().complete(
                system="s", user="u", schema=Answer, effort="max", max_tokens=8_000
            )
        options = recorder.chat_payloads[0]["options"]
        # `num_ctx` covers prompt *and* completion, so a window sized to the
        # prompt alone truncates by exactly the length of the reply.
        assert options["num_ctx"] >= options["num_predict"]


class TestTheWindowComesFromTheModel:
    def test_a_large_model_is_capped(self, recorder: _Recorder) -> None:
        # 131072 is real for gpt-oss:20b, and allocating it evicts the model
        # from the GPU on any consumer card.
        _chat(_provider())
        assert recorder.chat_payloads[0]["options"]["num_ctx"] == MAX_CONTEXT_WINDOW

    def test_a_small_model_gets_the_floor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _Recorder(context_length=2_048)
        monkeypatch.setattr(httpx, "post", stub.post)
        _chat(_provider())
        assert stub.chat_payloads[0]["options"]["num_ctx"] == MIN_CONTEXT_WINDOW

    def test_a_model_that_will_not_say_still_gets_the_floor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _Recorder(context_length=None)
        monkeypatch.setattr(httpx, "post", stub.post)
        _chat(_provider())
        assert stub.chat_payloads[0]["options"]["num_ctx"] == MIN_CONTEXT_WINDOW

    def test_an_unreachable_show_endpoint_does_not_kill_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _Recorder()
        real_post = stub.post

        def post(url: str, **kwargs: Any) -> httpx.Response:
            if url.endswith("/api/show"):
                raise httpx.ConnectError("nope")
            return real_post(url, **kwargs)

        monkeypatch.setattr(httpx, "post", post)
        assert _chat(_provider()).text == "ok"

    def test_the_model_is_only_asked_once(self, recorder: _Recorder) -> None:
        # One round trip per process, not one per agent step: a 20-step CATIA
        # turn would otherwise make 20 extra calls to learn the same number.
        provider = _provider()
        for _ in range(3):
            _chat(provider)
        assert recorder.show_calls == 1


class TestTruncationIsLoud:
    """A prompt that did not fit must fail, not answer from half a transcript."""

    def test_a_saturated_prompt_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _Recorder(prompt_tokens=MAX_CONTEXT_WINDOW)
        monkeypatch.setattr(httpx, "post", stub.post)
        with pytest.raises(LLMError) as caught:
            _chat(_provider())
        assert "context" in str(caught.value).lower()

    def test_the_error_says_what_to_do(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _Recorder(prompt_tokens=MAX_CONTEXT_WINDOW)
        monkeypatch.setattr(httpx, "post", stub.post)
        with pytest.raises(LLMError) as caught:
            _chat(_provider())
        message = str(caught.value)
        assert "new conversation" in message or "larger window" in message

    def test_a_comfortable_prompt_does_not(self, recorder: _Recorder) -> None:
        assert _chat(_provider()).text == "ok"

    def test_the_margin_catches_ollamas_reserved_buffer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Ollama shaves a little off the window for generation, so a truncated
        # prompt reports just under `num_ctx` rather than exactly it.
        stub = _Recorder(prompt_tokens=MAX_CONTEXT_WINDOW - 8)
        monkeypatch.setattr(httpx, "post", stub.post)
        with pytest.raises(LLMError):
            _chat(_provider())


class TestTheWindowShrinksWhenTheGpuCannotFitIt:
    """The ceiling that suits one model is fatal for the next.

    Measured on an RTX 5070 Laptop (8 GB): `gpt-oss:20b` runs at 32768, while
    `qwen3-coder:30b` refuses to start there --

        llama-server reported out-of-memory during startup: CUDA error

    -- and works at 16384. Without this, changing AI_MODEL to a larger model
    breaks every AI request with an error about CUDA that tells the user
    nothing about what to do.
    """

    class _Oom:
        """Answers OOM until the request asks for `fits` or less."""

        def __init__(self, fits: int) -> None:
            self.fits = fits
            self.windows: list[int] = []

        def post(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
            request = httpx.Request("POST", url)
            if url.endswith("/api/show"):
                return httpx.Response(
                    200, request=request, json={"model_info": {"qwen3moe.context_length": 262_144}}
                )
            window = json["options"]["num_ctx"]
            self.windows.append(window)
            if window > self.fits:
                return httpx.Response(
                    500,
                    request=request,
                    json={
                        "error": "llama-server reported out-of-memory during startup: "
                        "CUDA error\nCUDA error: out of memory"
                    },
                )
            return httpx.Response(
                200,
                request=request,
                json={
                    "message": {"content": "ok", "tool_calls": []},
                    "prompt_eval_count": 100,
                    "eval_count": 5,
                },
            )

    def test_it_retries_at_half_the_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = self._Oom(fits=16_384)
        monkeypatch.setattr(httpx, "post", stub.post)
        assert _chat(_provider()).text == "ok"
        assert stub.windows == [MAX_CONTEXT_WINDOW, MAX_CONTEXT_WINDOW // 2]

    def test_the_smaller_window_is_remembered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Otherwise every single turn pays for a failed model load.
        stub = self._Oom(fits=16_384)
        monkeypatch.setattr(httpx, "post", stub.post)
        provider = _provider()
        _chat(provider)
        _chat(provider)
        assert stub.windows[-1] == MAX_CONTEXT_WINDOW // 2
        assert stub.windows.count(MAX_CONTEXT_WINDOW) == 1

    def test_shrinking_does_not_spend_the_transient_retry_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # This model needs two halvings; with the shrink counted as an attempt
        # the real request would never be sent.
        stub = self._Oom(fits=MIN_CONTEXT_WINDOW)
        monkeypatch.setattr(httpx, "post", stub.post)
        assert _chat(_provider()).text == "ok"
        assert stub.windows[-1] == MIN_CONTEXT_WINDOW

    def test_it_stops_at_the_floor_with_an_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Below the floor the agent's own prompt no longer fits, and a model
        # that cannot see its tool schemas writes tool calls as prose instead of
        # calling them -- a worse failure than an honest error.
        stub = self._Oom(fits=1_024)
        monkeypatch.setattr(httpx, "post", stub.post)
        with pytest.raises(LLMError) as caught:
            _chat(_provider())
        message = str(caught.value)
        assert "GPU memory" in message
        assert "smaller model" in message

    def test_an_unrelated_500_is_not_treated_as_oom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
            request = httpx.Request("POST", url)
            if url.endswith("/api/show"):
                return httpx.Response(200, request=request, json={"model_info": {}})
            calls.append(json["options"]["num_ctx"])
            return httpx.Response(500, request=request, json={"error": "something else"})

        monkeypatch.setattr(httpx, "post", post)
        with pytest.raises(LLMError):
            _chat(_provider())
        # Retried as a transient failure, at the same window -- not halved.
        assert len(set(calls)) == 1
