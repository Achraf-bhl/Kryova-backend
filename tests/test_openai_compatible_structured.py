"""Not every OpenAI-compatible endpoint does schema-constrained output.

`response_format: {"type": "json_schema"}` is the good path: the server refuses
to emit anything but the shape asked for. The provider assumed every endpoint
had it, because OpenAI, vLLM and LM Studio all do.

DeepSeek does not. Both `deepseek-v4-pro` and `deepseek-v4-flash` answer

    400 {"error":{"message":"This response_format type is unavailable now"}}

which the provider surfaced as `Chat completion failed (400)` -- so pointing
`AI_BASE_URL` at DeepSeek left the agent's tool calling working and every
structured parse dead, which is the confusing half-broken state that is worse
than a clean failure.

So the request is downgraded on that specific rejection, once, and remembered.
`json_object` still guarantees syntactically valid JSON and the schema goes in
the system message; Pydantic validates either way, so nothing malformed reaches
a caller. What is lost is the server refusing a wrong shape at decode time,
which is why it is a fallback and not the default.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.provider import LLMError, LLMUnavailable
from app.ai.providers.openai_compatible import OpenAICompatibleProvider, _unfence

BASE = "https://api.example.test/v1"

REFUSAL = json.dumps(
    {
        "error": {
            "message": "This response_format type is unavailable now",
            "type": "invalid_request_error",
        }
    }
)


class LoadCase(BaseModel):
    force_n: float
    axis: str


def _reply(content: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class _Endpoint:
    """A fake server, recording what it was asked and answering as told."""

    def __init__(
        self, *, supports_json_schema: bool, content: str = '{"force_n": 500, "axis": "z"}'
    ):
        self.supports_json_schema = supports_json_schema
        self.content = content
        self.requests: list[dict[str, Any]] = []

    def __call__(self, url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
        self.requests.append(json)
        request = httpx.Request("POST", url)
        wants_schema = (json.get("response_format") or {}).get("type") == "json_schema"
        if wants_schema and not self.supports_json_schema:
            return httpx.Response(400, text=REFUSAL, request=request)
        return httpx.Response(200, json=_reply(self.content), request=request)


@pytest.fixture
def provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(BASE, "a-key", "deepseek-v4-pro", 30.0)


def _complete(provider: OpenAICompatibleProvider, endpoint: _Endpoint, monkeypatch: Any) -> Any:
    monkeypatch.setattr(httpx, "post", endpoint)
    return provider.complete(
        system="Parse the load case.",
        user="Pull 500 N along z.",
        schema=LoadCase,
        effort="low",
        max_tokens=200,
    )


class TestTheGoodPathIsStillTheDefault:
    def test_json_schema_is_tried_first(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint(supports_json_schema=True)
        result = _complete(provider, endpoint, monkeypatch)

        assert result.value == LoadCase(force_n=500, axis="z")
        assert len(endpoint.requests) == 1
        assert endpoint.requests[0]["response_format"]["type"] == "json_schema"
        assert endpoint.requests[0]["response_format"]["json_schema"]["strict"] is True

    def test_a_working_endpoint_is_never_downgraded(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint(supports_json_schema=True)
        for _ in range(3):
            _complete(provider, endpoint, monkeypatch)
        assert all(r["response_format"]["type"] == "json_schema" for r in endpoint.requests)


class TestTheFallback:
    def test_a_refusal_is_retried_as_json_object(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint(supports_json_schema=False)
        result = _complete(provider, endpoint, monkeypatch)

        assert result.value == LoadCase(force_n=500, axis="z")
        assert len(endpoint.requests) == 2
        assert endpoint.requests[1]["response_format"] == {"type": "json_object"}

    def test_the_schema_moves_into_the_system_message(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Without it, "answer in JSON" says nothing about which JSON.
        endpoint = _Endpoint(supports_json_schema=False)
        _complete(provider, endpoint, monkeypatch)

        system = endpoint.requests[1]["messages"][0]["content"]
        assert "force_n" in system and "axis" in system
        # And the original instruction survives rather than being replaced.
        assert "Parse the load case." in system

    def test_it_is_learned_once_not_paid_per_call(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint(supports_json_schema=False)
        for _ in range(3):
            _complete(provider, endpoint, monkeypatch)

        # One rejected attempt in total, not one per call.
        rejected = [r for r in endpoint.requests if r["response_format"]["type"] == "json_schema"]
        assert len(rejected) == 1
        assert len(endpoint.requests) == 4

    def test_pydantic_still_guards_the_result(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The server no longer constrains the shape, so this check is the only
        # thing left standing between a wrong answer and the caller.
        endpoint = _Endpoint(supports_json_schema=False, content='{"axis": "z"}')
        with pytest.raises(LLMError, match="does not match the expected schema"):
            _complete(provider, endpoint, monkeypatch)

    def test_a_fenced_answer_is_accepted(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `json_object` is a prompt instruction, and a model following one wraps
        # its answer in a code fence often enough to matter.
        endpoint = _Endpoint(
            supports_json_schema=False,
            content='```json\n{"force_n": 500, "axis": "z"}\n```',
        )
        assert _complete(provider, endpoint, monkeypatch).value.force_n == 500


class TestOnlyThatOneRejection:
    def test_an_unrelated_400_is_not_retried(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Retrying a genuinely bad request just doubles the latency before the
        # same failure, and hides which request was wrong.
        seen: list[dict[str, Any]] = []

        def endpoint(url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
            seen.append(json)
            return httpx.Response(
                400,
                text='{"error":{"message":"model not found"}}',
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx, "post", endpoint)
        with pytest.raises(LLMError, match="model not found"):
            provider.complete(system="s", user="u", schema=LoadCase, effort="low", max_tokens=10)
        assert len(seen) == 1

    def test_a_rejected_key_still_says_so(
        self, provider: OpenAICompatibleProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def endpoint(url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
            return httpx.Response(401, text="nope", request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", endpoint)
        with pytest.raises(LLMUnavailable, match="API key"):
            provider.complete(system="s", user="u", schema=LoadCase, effort="low", max_tokens=10)


class TestUnfencing:
    @pytest.mark.parametrize(
        "raw",
        [
            '{"a": 1}',
            '```json\n{"a": 1}\n```',
            '```JSON\n{"a": 1}\n```',
            '```\n{"a": 1}\n```',
            '  ```json\n{"a": 1}\n```  ',
        ],
    )
    def test_the_json_comes_out(self, raw: str) -> None:
        assert json.loads(_unfence(raw)) == {"a": 1}

    def test_an_unclosed_fence_still_yields_its_body(self) -> None:
        assert json.loads(_unfence('```json\n{"a": 1}')) == {"a": 1}
