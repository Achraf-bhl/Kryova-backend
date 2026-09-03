"""NVIDIA NIM: reasoning arrives beside the answer, and once it arrives instead.

`integrate.api.nvidia.com` is OpenAI-shaped, so most of this provider is
inherited. What is not inherited is what these tests are about, and all of it
was measured against the live endpoint on `nemotron-3.5-lightning-30b-a3b`
before any of it was written.

**The failure worth having a file for.** A Nemotron model keeps its chain of
thought in `reasoning_content` and its answer in `content`, cleanly separated —
right up until the generation is cut off. Truncate mid-reasoning and the partial
chain of thought appears in *both* fields, with `finish_reason: "length"`. The
base provider hands `content` to the agent as the turn's text, the agent shows
it to the user, and the user reads the model's private deliberation ("Here's a
thinking process: 1. Analyze User Input...") as the assistant's considered
reply. Nothing errors. `_parse_turn` drops it.

**The other measured facts**, each pinned below because each is something a
future edit could quietly undo:

* the endpoint rejects unknown parameters outright (`400 Validation:
  Unsupported parameter(s)`), so the two NVIDIA extension fields must never
  reach a generic endpoint — and `enable_thinking: false` must still be *sent*,
  because the model reasons by default and omitting the field is not the same
  as turning it off;
* structured output costs 890 completion tokens and 20.2s with thinking on
  versus ~20 tokens and 1.8s with it off, for the same correct answer, so
  `complete()` turns it off and `chat()` leaves it on;
* with thinking off the model returns `"\n\n"` alongside a tool call, and the
  agent yields any non-empty text as narration — an empty speech bubble before
  every tool that runs.

No live calls: the thing worth asserting is the exact request built and the
exact response read, and a live call would only tell us about either indirectly.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.providers.nvidia import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_REASONING_BUDGET,
    NvidiaProvider,
)

KEY = "nvapi-test"


class LoadCase(BaseModel):
    force_n: float
    axis: str


class _Endpoint:
    """A fake NIM, recording every request and answering as told."""

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


def provider(**overrides: Any) -> NvidiaProvider:
    settings: dict[str, Any] = {
        "api_key": KEY,
        "model": DEFAULT_MODEL,
        "timeout_seconds": 30.0,
    }
    settings.update(overrides)
    return NvidiaProvider(**settings)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "catia_new_part",
            "description": "Create a new CATIA part document.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Part name."}},
                "required": ["name"],
                "additionalProperties": False,
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


def _chat(prov: NvidiaProvider, endpoint: _Endpoint, monkeypatch: Any, **kwargs: Any) -> Any:
    monkeypatch.setattr(httpx, "post", endpoint)
    return prov.chat(
        system=kwargs.get("system", "You drive CATIA."),
        messages=kwargs.get("messages", [{"role": "user", "content": "Make a part."}]),
        tools=kwargs.get("tools", TOOLS),
        max_tokens=kwargs.get("max_tokens", 4000),
    )


class TestDefaults:
    def test_the_hosted_endpoint_is_the_default(self) -> None:
        """Configuration is a key and a model name, not a URL to remember."""
        assert provider()._base_url == DEFAULT_BASE_URL

    def test_a_self_hosted_nim_is_reachable_through_the_same_provider(self) -> None:
        assert provider(base_url="http://gpu-box:8000/v1")._base_url == "http://gpu-box:8000/v1"

    def test_an_empty_base_url_falls_back_to_the_hosted_one(self) -> None:
        """`settings.ai_base_url` is None unless someone sets it."""
        assert provider(base_url=None)._base_url == DEFAULT_BASE_URL

    def test_an_empty_model_falls_back_to_the_default(self) -> None:
        assert provider(model="")._model == DEFAULT_MODEL

    def test_the_key_is_sent_as_a_bearer_token(self) -> None:
        assert provider()._headers()["authorization"] == f"Bearer {KEY}"


class TestTheReasoningControlsAreSentCorrectly:
    """Two non-standard fields, on an endpoint that 400s on a third."""

    def test_chat_asks_for_thinking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(), endpoint, monkeypatch)
        assert endpoint.last["chat_template_kwargs"] == {"enable_thinking": True}
        assert endpoint.last["reasoning_budget"] == DEFAULT_REASONING_BUDGET

    def test_thinking_off_is_still_sent_rather_than_omitted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The model reasons by default; leaving the field out does not stop it."""
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(thinking=False), endpoint, monkeypatch)
        assert endpoint.last["chat_template_kwargs"] == {"enable_thinking": False}

    def test_no_reasoning_budget_is_sent_when_thinking_is_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A budget for reasoning that will not happen is a parameter for nothing."""
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(thinking=False), endpoint, monkeypatch)
        assert "reasoning_budget" not in endpoint.last

    def test_the_budget_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(reasoning_budget=512), endpoint, monkeypatch)
        assert endpoint.last["reasoning_budget"] == 512

    def test_only_the_two_known_extension_fields_are_added(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This endpoint answers 400 on any parameter it does not recognise, so
        a third field invented here would take out every call."""
        endpoint = _Endpoint({"content": "", "tool_calls": A_TOOL_CALL}, "tool_calls")
        _chat(provider(), endpoint, monkeypatch)
        standard = {"model", "max_completion_tokens", "messages", "tools", "tool_choice"}
        assert set(endpoint.last) - standard == {"chat_template_kwargs", "reasoning_budget"}

    def test_a_generic_endpoint_still_sends_neither(self) -> None:
        """The hook is on the base class; the base class must not use it."""
        from app.ai.providers.openai_compatible import OpenAICompatibleProvider

        assert OpenAICompatibleProvider("http://x/v1", None, "m", 5.0)._extra_body() == {}


class TestStructuredOutputTurnsThinkingOff:
    """890 tokens and 20.2s versus ~20 tokens and 1.8s, for the same answer."""

    def _complete(self, prov: NvidiaProvider, monkeypatch: pytest.MonkeyPatch) -> _Endpoint:
        endpoint = _Endpoint({"content": '{"force_n": 500, "axis": "z"}'})
        monkeypatch.setattr(httpx, "post", endpoint)
        prov.complete(
            system="Parse the load case.",
            user="Pull 500 N along z.",
            schema=LoadCase,
            effort="low",
            max_tokens=200,
        )
        return endpoint

    def test_complete_disables_thinking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = self._complete(provider(), monkeypatch)
        assert endpoint.last["chat_template_kwargs"] == {"enable_thinking": False}

    def test_complete_sends_no_reasoning_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert "reasoning_budget" not in self._complete(provider(), monkeypatch).last

    def test_json_schema_is_still_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NVIDIA supports schema-constrained decoding; verified live."""
        endpoint = self._complete(provider(), monkeypatch)
        assert endpoint.last["response_format"]["type"] == "json_schema"

    def test_the_flag_is_restored_afterwards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`complete()` toggles a flag around the call. If it leaked, every
        later agent turn would silently lose its reasoning."""
        prov = provider()
        self._complete(prov, monkeypatch)
        assert prov._extra_body() == {
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": DEFAULT_REASONING_BUDGET,
        }

    def test_the_flag_is_restored_even_when_the_call_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prov = provider()

        def explode(*args: Any, **kwargs: Any) -> None:
            raise httpx.ConnectError("nope")

        monkeypatch.setattr(httpx, "post", explode)
        with pytest.raises(Exception):
            prov.complete(system="s", user="u", schema=LoadCase, effort="low", max_tokens=200)
        assert prov._extra_body()["chat_template_kwargs"] == {"enable_thinking": True}


class TestReasoningIsNeverPresentedAsAnAnswer:
    """The failure this file exists for."""

    #: What the live endpoint returns when cut off mid-reasoning: the partial
    #: chain of thought, in both fields, with finish_reason "length".
    SPILL = (
        "Here's a thinking process:\n\n1.  **Analyze User Input:**\n"
        '   - User says: "Say the single word: ready"\n'
    )

    def test_a_turn_truncated_mid_reasoning_yields_no_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": self.SPILL, "reasoning_content": self.SPILL}, "length")
        turn = _chat(provider(), endpoint, monkeypatch)
        assert turn.text == ""

    def test_it_is_still_reported_as_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The agent appends "this answer was cut off" for a truncated turn, and
        that note with nothing attached is the honest outcome."""
        endpoint = _Endpoint({"content": self.SPILL, "reasoning_content": self.SPILL}, "length")
        assert _chat(provider(), endpoint, monkeypatch).truncated is True

    def test_a_completed_answer_is_kept_and_its_reasoning_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On a finished generation the two fields are cleanly separate, so the
        answer passes through untouched and the deliberation is not shown."""
        endpoint = _Endpoint(
            {"content": "The part is 4.2 kg.", "reasoning_content": "Let me compute the mass."}
        )
        turn = _chat(provider(), endpoint, monkeypatch)
        assert turn.text == "The part is 4.2 kg."
        assert "compute the mass" not in turn.text

    def test_a_truncated_answer_with_no_reasoning_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With thinking off there is no spill, so a cut-off answer is a real
        partial answer and the user is better off seeing it."""
        endpoint = _Endpoint({"content": "The part weighs", "reasoning_content": ""}, "length")
        turn = _chat(provider(thinking=False), endpoint, monkeypatch)
        assert turn.text == "The part weighs"
        assert turn.truncated is True


class TestWhitespaceContentBesideAToolCall:
    def test_whitespace_only_narration_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With thinking off the model returns "\\n\\n" beside its tool call, and
        the agent yields any non-empty text as narration — an empty speech
        bubble in the UI before every tool that runs."""
        endpoint = _Endpoint({"content": "\n\n", "tool_calls": A_TOOL_CALL}, "tool_calls")
        turn = _chat(provider(thinking=False), endpoint, monkeypatch)
        assert turn.text == ""
        assert [c.name for c in turn.tool_calls] == ["catia_new_part"]

    def test_real_narration_beside_a_tool_call_survives(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint(
            {"content": "Creating the part now.", "tool_calls": A_TOOL_CALL}, "tool_calls"
        )
        assert _chat(provider(), endpoint, monkeypatch).text == "Creating the part now."


class TestToolCallingIsInherited:
    def test_arguments_are_parsed_from_their_json_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        endpoint = _Endpoint({"content": None, "tool_calls": A_TOOL_CALL}, "tool_calls")
        turn = _chat(provider(), endpoint, monkeypatch)
        assert [(c.name, c.arguments) for c in turn.tool_calls] == [
            ("catia_new_part", {"name": "Bracket"})
        ]

    def test_a_replayed_transcript_re_encodes_its_arguments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The agent stores parsed dicts; this endpoint needs a JSON string."""
        endpoint = _Endpoint({"content": "done"})
        _chat(
            provider(),
            endpoint,
            monkeypatch,
            messages=[
                {"role": "user", "content": "Make a part."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "catia_new_part",
                                "arguments": {"name": "Bracket"},
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "catia_new_part",
                    "content": "{}",
                    "is_error": False,
                },
            ],
        )
        sent = endpoint.last["messages"]
        assert sent[2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"name": "Bracket"})
        assert "is_error" not in sent[3]

    def test_usage_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        endpoint = _Endpoint({"content": "done"})
        turn = _chat(provider(), endpoint, monkeypatch)
        assert (turn.usage.prompt_tokens, turn.usage.completion_tokens) == (10, 5)


class TestItIsRegistered:
    def test_the_name_is_in_the_provider_list(self) -> None:
        from app.ai.providers import PROVIDER_NAMES

        assert "nvidia" in PROVIDER_NAMES

    def test_the_provider_names_itself_for_error_messages(self) -> None:
        assert provider().name == "nvidia"

    def test_it_reports_its_model_for_the_usage_ledger(self) -> None:
        assert provider().model == DEFAULT_MODEL

    def test_building_it_without_a_key_says_where_to_get_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai.provider import LLMUnavailable
        from app.ai.providers import get_provider
        from app.core.config import settings

        monkeypatch.setattr(settings, "ai_provider", "nvidia")
        monkeypatch.setattr(settings, "ai_api_key", None)
        get_provider.cache_clear()
        try:
            with pytest.raises(LLMUnavailable, match="build.nvidia.com"):
                get_provider()
        finally:
            get_provider.cache_clear()
