"""DeepSeek: reasoning that must be echoed back, a different output cap, no json_schema.

Every fact here is read from DeepSeek's own API reference, not measured: this
machine has no outbound network (CLAUDE.md, *Tools*). What the file pins is the
exact request built and the exact response read, so a later edit cannot quietly
undo one of them. Whether the live endpoint agrees is a row in
`docs/WINDOWS_VERIFICATION.md` THE QUEUE -- a mock of a wire format is a copy of
what was believed, and this file does not pretend otherwise.

The failure worth a file: with `tools` in the request and thinking on, DeepSeek
answers **400** unless every earlier assistant turn's `reasoning_content` is
passed back. The generic provider has no such field, so it would work on step one
of an agent run and fail on step two -- nothing in a one-shot test shows it.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.provider import LLMUnavailable
from app.ai.providers import get_provider
from app.ai.providers.deepseek import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DeepSeekProvider,
    _level,
)
from app.ai.providers.openai_compatible import DEFAULT_REASONING_BUDGET

KEY = "sk-test"


class LoadCase(BaseModel):
    force_n: float
    axis: str


class _Endpoint:
    """A fake DeepSeek, recording every request and answering as told."""

    def __init__(self, message: dict[str, Any], finish_reason: str = "stop") -> None:
        self.message = message
        self.finish_reason = finish_reason
        self.requests: list[dict[str, Any]] = []

    def __call__(self, url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
        self.requests.append(json)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": self.message, "finish_reason": self.finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            request=httpx.Request("POST", url),
        )

    @property
    def last(self) -> dict[str, Any]:
        return self.requests[-1]


def provider(**overrides: Any) -> DeepSeekProvider:
    settings: dict[str, Any] = {"api_key": KEY, "model": DEFAULT_MODEL, "timeout_seconds": 30.0}
    settings.update(overrides)
    return DeepSeekProvider(**settings)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "catia_new_part",
            "description": "Create a new CATIA part document.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    }
]

A_TOOL_CALL = [
    {
        "id": "call-1",
        "type": "function",
        "function": {"name": "catia_new_part", "arguments": '{"name":"Bracket"}'},
    }
]

USER = {"role": "user", "content": "Make a part."}
ASSISTANT_WITH_REASONING = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "catia_new_part", "arguments": {"name": "Bracket"}},
        }
    ],
    "reasoning": "The user wants a part, so create a document first.",
}
TOOL_RESULT = {"role": "tool", "tool_call_id": "call-1", "name": "catia_new_part", "content": "{}"}


def _chat(
    prov: DeepSeekProvider,
    endpoint: _Endpoint,
    monkeypatch: pytest.MonkeyPatch,
    **kwargs: Any,
) -> Any:
    monkeypatch.setattr(httpx, "post", endpoint)
    return prov.chat(
        system="You drive CATIA.",
        messages=kwargs.get("messages", [USER]),
        tools=kwargs.get("tools", TOOLS),
        max_tokens=kwargs.get("max_tokens", 4000),
    )


class TestDefaults:
    def test_the_hosted_endpoint_and_model_are_the_defaults(self) -> None:
        prov = provider(model="", base_url=None)
        assert prov._base_url == DEFAULT_BASE_URL
        assert prov._model == DEFAULT_MODEL

    def test_the_model_is_whatever_ai_model_names(self) -> None:
        """Nothing here is pinned to one model: moving to v4-pro, or off DeepSeek, is config."""
        assert provider(model="deepseek-v4-pro")._model == "deepseek-v4-pro"

    def test_the_key_is_a_bearer_token(self) -> None:
        assert provider()._headers()["authorization"] == f"Bearer {KEY}"

    def test_the_documented_response_formats_exclude_json_schema(self) -> None:
        assert provider()._json_schema_supported is False


class TestTheEffortWordsMapOntoDeepSeeks:
    @pytest.mark.parametrize("word", ["none", "minimal", "low", "NONE", " Low "])
    def test_the_low_end_means_thinking_off(self, word: str) -> None:
        assert _level(word) is None

    @pytest.mark.parametrize(
        ("word", "level"),
        [("medium", "high"), ("high", "high"), ("xhigh", "max"), ("max", "max")],
    )
    def test_the_rest_map_to_a_documented_level(self, word: str, level: str) -> None:
        assert _level(word) == level

    @pytest.mark.parametrize("word", ["", "turbo", "hihg"])
    def test_an_unknown_word_reasons_at_the_default_rather_than_switching_off(
        self, word: str
    ) -> None:
        """A typo in a judgement task's setting is likelier than a request for none."""
        assert _level(word) == "high"


class TestAnAgentStepReasons:
    def test_thinking_is_enabled_at_the_configured_effort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(reasoning_effort="max"), endpoint, monkeypatch)
        assert endpoint.last["thinking"] == {"type": "enabled"}
        assert endpoint.last["reasoning_effort"] == "max"

    def test_the_cap_is_max_tokens_with_headroom_for_the_thinking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DeepSeek documents `max_tokens` and counts reasoning against it, so a
        call that reasons gets the budget on top or the answer is cut off after
        the thinking has used the cap."""
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(reasoning_budget=1000), endpoint, monkeypatch, max_tokens=4000)
        assert endpoint.last["max_tokens"] == 5000
        assert "max_completion_tokens" not in endpoint.last

    def test_the_default_headroom_is_the_shared_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(), endpoint, monkeypatch, max_tokens=4000)
        assert endpoint.last["max_tokens"] == 4000 + DEFAULT_REASONING_BUDGET

    def test_the_whole_switch_off_leaves_no_reasoning_anywhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`AI_THINKING=false` is the cheap, fast mode: thinking disabled, no headroom paid for."""
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(thinking=False), endpoint, monkeypatch, max_tokens=4000)
        assert endpoint.last["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in endpoint.last
        assert endpoint.last["max_tokens"] == 4000

    def test_only_documented_fields_are_added(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(), endpoint, monkeypatch)
        standard = {"model", "max_tokens", "messages", "tools", "tool_choice"}
        assert set(endpoint.last) - standard == {"thinking", "reasoning_effort"}


class TestReasoningIsEchoedBack:
    """The 400 this provider exists to avoid."""

    def test_reasoning_is_read_off_the_turn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint(
            {"content": "", "tool_calls": A_TOOL_CALL, "reasoning_content": "Create it first."},
            "tool_calls",
        )
        turn = _chat(provider(), endpoint, monkeypatch)
        assert turn.reasoning == "Create it first."

    def test_a_turn_with_no_reasoning_field_has_none_not_an_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NULL is "none was kept"; '' is "the model returned the field, empty"."""
        endpoint = _Endpoint({"content": "done"})
        assert _chat(provider(), endpoint, monkeypatch).reasoning is None

    def test_an_empty_reasoning_field_is_kept_as_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "done", "reasoning_content": ""})
        assert _chat(provider(), endpoint, monkeypatch).reasoning == ""

    def test_the_stored_reasoning_travels_with_its_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, ASSISTANT_WITH_REASONING, TOOL_RESULT])
        assistant = endpoint.last["messages"][2]
        assert assistant["reasoning_content"] == ASSISTANT_WITH_REASONING["reasoning"]
        assert "reasoning" not in assistant, "our key must not reach the wire"
        assert endpoint.last["thinking"] == {"type": "enabled"}

    def test_our_own_keys_never_reach_the_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, ASSISTANT_WITH_REASONING, TOOL_RESULT])
        tool_message = endpoint.last["messages"][3]
        assert set(tool_message) == {"role", "tool_call_id", "content"}

    def test_arguments_are_re_encoded_as_a_json_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, ASSISTANT_WITH_REASONING, TOOL_RESULT])
        sent = endpoint.last["messages"][2]["tool_calls"][0]["function"]["arguments"]
        assert sent == '{"name": "Bracket"}'


class TestAnUnechoableTranscriptRunsWithThinkingOff:
    """Sending thinking-on over a turn with no reasoning to give is the 400."""

    def test_one_turn_without_reasoning_turns_thinking_off_for_the_step(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy = {**ASSISTANT_WITH_REASONING, "reasoning": None}
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, legacy, TOOL_RESULT])
        assert endpoint.last["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in endpoint.last
        assert "reasoning_content" not in endpoint.last["messages"][2]

    def test_a_turn_before_the_newest_question_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DeepSeek ignores earlier questions' reasoning, so a stop notice or a row stored
        before the column existed must not switch thinking off for the next question."""
        old_answer = {"role": "assistant", "content": "Done.", "reasoning": None}
        endpoint = _Endpoint({"content": "done"})
        _chat(
            provider(),
            endpoint,
            monkeypatch,
            messages=[USER, old_answer, {"role": "user", "content": "And now a hole."}],
        )
        assert endpoint.last["thinking"] == {"type": "enabled"}

    def test_a_missing_key_counts_the_same_as_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stored_before_this_existed = {
            k: v for k, v in ASSISTANT_WITH_REASONING.items() if k != "reasoning"
        }
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, stored_before_this_existed, TOOL_RESULT])
        assert endpoint.last["thinking"] == {"type": "disabled"}

    def test_an_empty_string_can_be_echoed_so_it_does_not_disable_thinking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = {**ASSISTANT_WITH_REASONING, "reasoning": ""}
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER, empty, TOOL_RESULT])
        assert endpoint.last["thinking"] == {"type": "enabled"}
        assert endpoint.last["messages"][2]["reasoning_content"] == ""

    def test_a_transcript_with_no_assistant_turn_at_all_reasons(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "done"})
        _chat(provider(), endpoint, monkeypatch, messages=[USER])
        assert endpoint.last["thinking"] == {"type": "enabled"}


class TestOnlyJudgementReasons:
    """Parsing a sentence is reading; a reasoning model spends its tokens before it answers."""

    def test_a_chat_call_with_no_tools_does_not_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": "Bracket study"})
        _chat(provider(), endpoint, monkeypatch, tools=[])
        assert endpoint.last["thinking"] == {"type": "disabled"}
        assert "tools" not in endpoint.last
        assert endpoint.last["max_tokens"] == 4000

    def _complete(self, prov: DeepSeekProvider, monkeypatch: pytest.MonkeyPatch, effort: str) -> Any:
        endpoint = _Endpoint({"content": '{"force_n": 500, "axis": "z"}'})
        monkeypatch.setattr(httpx, "post", endpoint)
        prov.complete(
            system="Parse the load case.",
            user="Pull 500 N along z.",
            schema=LoadCase,
            effort=effort,
            max_tokens=200,
        )
        return endpoint

    def test_a_low_effort_parse_runs_with_thinking_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = self._complete(provider(), monkeypatch, "low")
        assert endpoint.last["thinking"] == {"type": "disabled"}
        assert endpoint.last["max_tokens"] == 200

    def test_a_high_effort_interpretation_reasons_and_is_given_headroom(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = self._complete(provider(), monkeypatch, "high")
        assert endpoint.last["thinking"] == {"type": "enabled"}
        assert endpoint.last["max_tokens"] == 200 + DEFAULT_REASONING_BUDGET

    def test_the_global_switch_beats_the_per_task_effort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = self._complete(provider(thinking=False), monkeypatch, "high")
        assert endpoint.last["thinking"] == {"type": "disabled"}


class TestStructuredOutputIsJsonObjectFromTheFirstRequest:
    def test_no_json_schema_is_ever_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DeepSeek documents `text` and `json_object` only; sending the other is a
        rejected request paid for once per process."""
        endpoint = _Endpoint({"content": '{"force_n": 500, "axis": "z"}'})
        monkeypatch.setattr(httpx, "post", endpoint)
        result = provider().complete(
            system="Parse.", user="500 N along z.", schema=LoadCase, effort="low", max_tokens=100
        )
        assert len(endpoint.requests) == 1
        assert endpoint.last["response_format"] == {"type": "json_object"}
        assert result.value == LoadCase(force_n=500, axis="z")

    def test_the_prompt_names_json_and_carries_the_schema(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The API requires the word "json" in the prompt, and nothing else tells
        the model the shape."""
        endpoint = _Endpoint({"content": '{"force_n": 1, "axis": "x"}'})
        monkeypatch.setattr(httpx, "post", endpoint)
        provider().complete(
            system="Parse.", user="1 N.", schema=LoadCase, effort="low", max_tokens=100
        )
        system = endpoint.last["messages"][0]["content"]
        assert "JSON" in system
        assert "force_n" in system


class TestTheFactory:
    def _configure(self, monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
        from app.core.config import settings

        get_provider.cache_clear()
        defaults: dict[str, Any] = {
            "ai_provider": "deepseek",
            "ai_model": "deepseek-flash",
            "ai_api_key": KEY,
            "ai_base_url": None,
            "ai_thinking": True,
            "ai_effort_chat": "high",
            "ai_reasoning_budget": 1234,
        }
        defaults.update(values)
        for name, value in defaults.items():
            monkeypatch.setattr(settings, name, value)

    def teardown_method(self) -> None:
        get_provider.cache_clear()

    def test_the_settings_reach_the_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._configure(monkeypatch, ai_effort_chat="max", ai_thinking=False)
        prov = get_provider()
        assert isinstance(prov, DeepSeekProvider)
        assert prov._thinking is False
        assert prov._reasoning_effort == "max"
        assert prov._reasoning_budget == 1234

    def test_no_key_is_an_unavailable_provider_that_says_how_to_fix_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._configure(monkeypatch, ai_api_key=None)
        with pytest.raises(LLMUnavailable, match="AI_API_KEY"):
            get_provider()

    def test_a_removed_provider_is_refused_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._configure(monkeypatch, ai_provider="ollama")
        with pytest.raises(LLMUnavailable, match="Unknown AI_PROVIDER 'ollama'"):
            get_provider()

    def test_the_vendor_is_config_not_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Moving to OpenAI is `AI_PROVIDER`, `AI_MODEL` and `AI_BASE_URL`."""
        from app.ai.providers import OpenAICompatibleProvider

        self._configure(
            monkeypatch,
            ai_provider="openai_compatible",
            ai_model="a-future-model",
            ai_base_url="https://api.openai.com/v1",
        )
        prov = get_provider()
        assert isinstance(prov, OpenAICompatibleProvider)
        assert not isinstance(prov, DeepSeekProvider)
        assert prov._model == "a-future-model"
        assert prov._base_url == "https://api.openai.com/v1"


class TestTheShippedDefaultsAreTheCheapOnes:
    def test_an_agent_step_does_not_reason_unless_asked_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A turn is ~20 agent steps; a reasoning step adds thousands of billed
        output tokens and tens of seconds. Judgement is bought, not inherited."""
        from app.core.config import Settings

        assert Settings.model_fields["ai_effort_chat"].default == "low"
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(reasoning_effort="low"), endpoint, monkeypatch)
        assert endpoint.last["thinking"] == {"type": "disabled"}
        assert endpoint.last["max_tokens"] == 4000, "no headroom is paid for thinking that is off"

    def test_the_default_provider_is_deepseek_flash(self) -> None:
        from app.core.config import Settings

        assert Settings.model_fields["ai_provider"].default == "deepseek"
        assert Settings.model_fields["ai_model"].default == "deepseek-flash"
