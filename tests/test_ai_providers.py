"""Provider-level tests: the wire shape, and what it costs.

The Anthropic SDK is an optional dependency and is not installed here, so its
client is mocked. That is not a weaker test than a live call -- the thing worth
asserting is the *exact request* the provider builds, which a live call would
only tell us about indirectly and only when it happened to fail.

These exist because the provider shipped a request shape nothing had ever
exercised: no test constructed it, the SDK was absent so no import error
surfaced, and the first person to set `AI_PROVIDER=anthropic` would have been
the one to find out.
"""

import sys
import types
from typing import Any

import pytest

from app.ai.provider import LLMError, LLMRefusal, LLMUnavailable, TokenUsage
from app.ai.providers.anthropic import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    AnthropicProvider,
    _effort,
    _to_anthropic_messages,
)
from app.ai.schemas import Finding


class _Block:
    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


class _Usage:
    def __init__(self, input_tokens: int = 0, output_tokens: int = 0, **extra: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = extra.get("cache_read_input_tokens", 0)
        self.cache_creation_input_tokens = extra.get("cache_creation_input_tokens", 0)


class _Response:
    def __init__(
        self,
        content: list[_Block],
        stop_reason: str = "end_turn",
        usage: _Usage | None = None,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage or _Usage()


class _Messages:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _Client:
    def __init__(self, response: Any) -> None:
        self.messages = _Messages(response)


def _fake_sdk() -> types.ModuleType:
    """A stand-in for the `anthropic` package, exception hierarchy included.

    The hierarchy matters: `AuthenticationError` subclasses `APIStatusError`, so
    a provider that checked the base class first would flatten a rejected key
    into a generic bad-gateway. The order is what these tests pin.
    """
    module = types.ModuleType("anthropic")

    class APIError(Exception):
        pass

    class APIStatusError(APIError):
        def __init__(self, message: str, status_code: int = 400) -> None:
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    class AuthenticationError(APIStatusError):
        pass

    class RateLimitError(APIStatusError):
        pass

    class APIConnectionError(APIError):
        pass

    module.APIError = APIError  # type: ignore[attr-defined]
    module.APIStatusError = APIStatusError  # type: ignore[attr-defined]
    module.AuthenticationError = AuthenticationError  # type: ignore[attr-defined]
    module.RateLimitError = RateLimitError  # type: ignore[attr-defined]
    module.APIConnectionError = APIConnectionError  # type: ignore[attr-defined]
    module.Anthropic = lambda **_: None  # type: ignore[attr-defined]
    return module


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = _fake_sdk()
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def _provider(response: Any, sdk: types.ModuleType, model: str = "claude-opus-5") -> Any:
    provider = AnthropicProvider(api_key="key", model=model, timeout_seconds=30.0)
    provider._sdk = sdk
    provider._client = _Client(response)
    return provider


class TestCompleteRequestShape:
    """The exact request built for schema-constrained output."""

    def test_the_schema_goes_under_output_config_format(self, sdk: types.ModuleType) -> None:
        response = _Response(
            [_Block(type="text", text='{"title":"t","detail":"d","severity":"info"}')],
            usage=_Usage(input_tokens=120, output_tokens=30),
        )
        provider = _provider(response, sdk)
        provider.complete(system="S", user="U", schema=Finding, effort="high", max_tokens=1_000)

        request = provider._client.messages.requests[0]
        assert request["model"] == "claude-opus-5"
        assert request["max_tokens"] == 1_000
        assert request["output_config"]["format"]["type"] == "json_schema"
        assert request["output_config"]["effort"] == "high"
        # The deprecated top-level parameter must not reappear.
        assert "output_format" not in request

    def test_the_schema_is_strictified(self, sdk: types.ModuleType) -> None:
        """A schema that leaves objects open is rejected by the API."""
        response = _Response(
            [_Block(type="text", text='{"title":"t","detail":"d","severity":"info"}')]
        )
        provider = _provider(response, sdk)
        provider.complete(system="S", user="U", schema=Finding, effort="low", max_tokens=100)

        schema = provider._client.messages.requests[0]["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])

    def test_sampling_parameters_are_never_sent(self, sdk: types.ModuleType) -> None:
        """`temperature`, `top_p` and `top_k` are a 400 on current models."""
        response = _Response(
            [_Block(type="text", text='{"title":"t","detail":"d","severity":"info"}')]
        )
        provider = _provider(response, sdk)
        provider.complete(system="S", user="U", schema=Finding, effort="low", max_tokens=100)

        request = provider._client.messages.requests[0]
        for forbidden in ("temperature", "top_p", "top_k"):
            assert forbidden not in request

    def test_the_system_prompt_is_marked_for_caching(self, sdk: types.ModuleType) -> None:
        response = _Response(
            [_Block(type="text", text='{"title":"t","detail":"d","severity":"info"}')]
        )
        provider = _provider(response, sdk)
        provider.complete(system="frozen", user="U", schema=Finding, effort="low", max_tokens=1)

        system = provider._client.messages.requests[0]["system"]
        assert system == [
            {"type": "text", "text": "frozen", "cache_control": {"type": "ephemeral"}}
        ]

    def test_usage_is_captured_including_cache_tokens(self, sdk: types.ModuleType) -> None:
        response = _Response(
            [_Block(type="text", text='{"title":"t","detail":"d","severity":"info"}')],
            usage=_Usage(
                input_tokens=10,
                output_tokens=4,
                cache_read_input_tokens=90,
                cache_creation_input_tokens=6,
            ),
        )
        provider = _provider(response, sdk)
        completion = provider.complete(
            system="S", user="U", schema=Finding, effort="low", max_tokens=100
        )
        # Cache reads and writes bill as input, so they belong in the input total.
        assert completion.usage == TokenUsage(prompt_tokens=106, completion_tokens=4)


class TestEffort:
    def test_valid_levels_pass_through(self) -> None:
        for level in ("low", "medium", "high", "xhigh", "max"):
            assert _effort(level) == level

    def test_an_unknown_level_is_clamped_not_shipped(self) -> None:
        """A typo in AI_EFFORT_INTERPRET is a 400 that kills the whole feature."""
        assert _effort("agressive") == DEFAULT_EFFORT
        assert _effort("") == DEFAULT_EFFORT
        assert _effort("HIGH") == "high"


class TestStopReasons:
    def test_a_refusal_is_reported_before_content_is_read(self, sdk: types.ModuleType) -> None:
        """A refusal returns 200 with empty content -- indexing it is an IndexError."""
        provider = _provider(_Response([], stop_reason="refusal"), sdk)
        with pytest.raises(LLMRefusal):
            provider.complete(system="S", user="U", schema=Finding, effort="low", max_tokens=100)

    def test_a_truncated_structured_answer_is_an_error(self, sdk: types.ModuleType) -> None:
        provider = _provider(
            _Response([_Block(type="text", text='{"title":')], stop_reason="max_tokens"), sdk
        )
        with pytest.raises(LLMError, match="output limit"):
            provider.complete(system="S", user="U", schema=Finding, effort="low", max_tokens=100)

    def test_a_truncated_chat_turn_is_surfaced_not_discarded(self, sdk: types.ModuleType) -> None:
        """In a loop there is usable text; throwing the turn away wastes it."""
        provider = _provider(
            _Response(
                [_Block(type="text", text="The peak stress is 41% of yi")],
                stop_reason="max_tokens",
            ),
            sdk,
        )
        turn = provider.chat(system="S", messages=[], tools=[], max_tokens=10)
        assert turn.truncated is True
        assert turn.text.startswith("The peak stress")


class TestChatRequestShape:
    def test_tools_are_translated_to_the_anthropic_shape(self, sdk: types.ModuleType) -> None:
        provider = _provider(_Response([_Block(type="text", text="ok")]), sdk)
        provider.chat(
            system="S",
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "list_projects",
                        "description": "List them",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            max_tokens=100,
        )
        request = provider._client.messages.requests[0]
        assert request["tools"] == [
            {
                "name": "list_projects",
                "description": "List them",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    def test_an_empty_tool_list_is_omitted_not_sent(self, sdk: types.ModuleType) -> None:
        """`tools: []` is a validation error, not "answer without tools"."""
        provider = _provider(_Response([_Block(type="text", text="ok")]), sdk)
        provider.chat(system="S", messages=[], tools=[], max_tokens=100)
        assert "tools" not in provider._client.messages.requests[0]

    def test_tool_calls_come_back_as_tool_calls(self, sdk: types.ModuleType) -> None:
        provider = _provider(
            _Response(
                [
                    _Block(type="text", text="Let me check."),
                    _Block(type="tool_use", id="tu_1", name="list_projects", input={"limit": 3}),
                ]
            ),
            sdk,
        )
        turn = provider.chat(system="S", messages=[], tools=[], max_tokens=100)
        assert turn.text == "Let me check."
        assert turn.wants_tools
        assert turn.tool_calls[0].name == "list_projects"
        assert turn.tool_calls[0].arguments == {"limit": 3}


class TestSdkErrorTranslation:
    def test_a_rejected_key_is_unavailable_not_a_bad_gateway(self, sdk: types.ModuleType) -> None:
        provider = _provider(sdk.AuthenticationError("bad key", 401), sdk)
        with pytest.raises(LLMUnavailable, match="rejected"):
            provider.chat(system="S", messages=[], tools=[], max_tokens=1)

    def test_a_rate_limit_says_to_retry(self, sdk: types.ModuleType) -> None:
        provider = _provider(sdk.RateLimitError("slow down", 429), sdk)
        with pytest.raises(LLMError, match="Retry"):
            provider.chat(system="S", messages=[], tools=[], max_tokens=1)

    def test_a_connection_failure_names_the_api(self, sdk: types.ModuleType) -> None:
        provider = _provider(sdk.APIConnectionError("no route"), sdk)
        with pytest.raises(LLMError, match="reach the Anthropic API"):
            provider.chat(system="S", messages=[], tools=[], max_tokens=1)

    def test_a_missing_sdk_explains_how_to_install_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing optional dependency must be a 503, not a 500.

        The old code did a bare `import anthropic` at the top of each method, so
        the ModuleNotFoundError escaped before any handling ran.
        """
        import builtins

        real_import = builtins.__import__

        def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "anthropic":
                raise ModuleNotFoundError("No module named 'anthropic'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setitem(sys.modules, "anthropic", None)
        monkeypatch.delitem(sys.modules, "anthropic")
        monkeypatch.setattr(builtins, "__import__", refuse)

        provider = AnthropicProvider(api_key="k", model="claude-opus-5", timeout_seconds=5)
        with pytest.raises(LLMUnavailable, match="pip install anthropic"):
            provider.chat(system="S", messages=[], tools=[], max_tokens=1)


class TestHealth:
    def test_a_missing_key_is_reported(self, sdk: types.ModuleType) -> None:
        provider = AnthropicProvider(api_key="", model="claude-opus-5", timeout_seconds=5)
        provider._sdk = sdk
        with pytest.raises(LLMUnavailable, match="AI_API_KEY"):
            provider.health()

    def test_a_non_anthropic_model_id_is_caught_before_the_first_request(
        self, sdk: types.ModuleType
    ) -> None:
        """The default AI_MODEL is an Ollama tag; pointed at Anthropic it 404s."""
        provider = AnthropicProvider(api_key="k", model="qwen2.5-coder:7b", timeout_seconds=5)
        provider._sdk = sdk
        with pytest.raises(LLMUnavailable, match="not an Anthropic model id"):
            provider.health()

    def test_an_empty_model_falls_back_to_a_real_default(self) -> None:
        provider = AnthropicProvider(api_key="k", model="  ", timeout_seconds=5)
        assert provider.model == DEFAULT_MODEL
        assert provider.model.startswith("claude-")


class TestMessageTranslation:
    """Anthropic's content-block shape differs structurally from the normal form."""

    def test_tool_results_become_blocks_in_a_user_turn(self) -> None:
        out = _to_anthropic_messages(
            [
                {"role": "tool", "tool_call_id": "c1", "name": "f", "content": "{}"},
                {"role": "tool", "tool_call_id": "c2", "name": "g", "content": "[]"},
            ]
        )
        # Merged into one user turn: the API rejects a tool_use that is not
        # answered inside a single turn.
        assert len(out) == 1
        assert out[0]["role"] == "user"
        assert [block["tool_use_id"] for block in out[0]["content"]] == ["c1", "c2"]

    def test_an_error_result_keeps_its_flag(self) -> None:
        out = _to_anthropic_messages(
            [
                {
                    "role": "tool",
                    "tool_call_id": "c1",
                    "name": "f",
                    "content": "boom",
                    "is_error": True,
                }
            ]
        )
        assert out[0]["content"][0]["is_error"] is True

    def test_assistant_tool_calls_become_tool_use_blocks(self) -> None:
        out = _to_anthropic_messages(
            [
                {
                    "role": "assistant",
                    "content": "Checking.",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {"name": "list_projects", "arguments": {"a": 1}},
                        }
                    ],
                }
            ]
        )
        assert out[0]["content"][0] == {"type": "text", "text": "Checking."}
        assert out[0]["content"][1] == {
            "type": "tool_use",
            "id": "c1",
            "name": "list_projects",
            "input": {"a": 1},
        }


class TestUsageAcrossProviders:
    """Every provider reports usage under its own spelling."""

    def test_ollama_reports_its_own_field_names(self) -> None:
        from app.ai.providers.ollama import _usage

        assert _usage({"prompt_eval_count": 12, "eval_count": 5}) == TokenUsage(12, 5)
        # A server that reports nothing gets zeros, not an invented estimate.
        assert _usage({}) == TokenUsage(0, 0)

    def test_openai_compatible_reports_its_own_field_names(self) -> None:
        from app.ai.providers.openai_compatible import _usage

        assert _usage({"usage": {"prompt_tokens": 7, "completion_tokens": 2}}) == TokenUsage(7, 2)
        assert _usage({}) == TokenUsage(0, 0)
