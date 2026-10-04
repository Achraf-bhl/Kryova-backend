"""The one field `GeminiProvider` adds, and the boundary it adds it on.

Everything else about this provider is `OpenAICompatibleProvider`, which has its own
tests. What is new is `reasoning_effort`, and the interesting claim is not that it is sent
-- it is **when**: off for structured calls, left alone for agent turns. Getting that
backwards is invisible in the answer either way, which is why it is pinned rather than
reviewed.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.ai.providers import PROVIDER_NAMES
from app.ai.providers.gemini import DEFAULT_BASE_URL, NO_REASONING, GeminiProvider


class Answer(BaseModel):
    answer: str


TOOLS = [
    {
        "type": "function",
        "function": {"name": "f", "description": "d", "parameters": {"type": "object"}},
    }
]


def _provider() -> GeminiProvider:
    return GeminiProvider(api_key="test-key", model="gemini-2.5-flash", timeout_seconds=30.0)


class TestReasoningIsOffExactlyWhereItIsWaste:
    def test_a_structured_call_asks_for_no_reasoning(self) -> None:
        """Measured on Gemini: 81 total tokens with reasoning against 21 without, on a
        question whose prompt and answer were 22 of them. At a 24-token cap the hidden
        tokens exhaust the budget and the call returns `finish_reason: length`."""
        payload = _provider()._structured_payload("sys", "user", Answer, 24, "low")

        assert payload["reasoning_effort"] == NO_REASONING == "none"

    def test_an_agent_turn_does_not_carry_the_field_at_all(self) -> None:
        """A `chat()` turn with tools is the agent building geometry over interdependent
        steps, which is where reasoning earns its cost. Pinning it to a default this
        code has not measured would be worse than leaving the vendor's own."""
        payload = _provider()._chat_payload("sys", [{"role": "user", "content": "x"}], TOOLS, 100)

        assert "reasoning_effort" not in payload

    def test_a_chat_call_with_no_tools_does_not_reason(self) -> None:
        payload = _provider()._chat_payload("sys", [{"role": "user", "content": "x"}], [], 100)

        assert payload["reasoning_effort"] == NO_REASONING

    def test_a_structured_call_does_not_change_the_next_agent_turn(self) -> None:
        """The decision is returned per call, not stored on the shared provider. A flag
        flipped around a structured call raced with agent steps on other threads, and
        the failure was silent: a turn with reasoning left off still answers."""
        provider = _provider()
        provider._structured_payload("sys", "user", Answer, 24, "low")

        payload = provider._chat_payload("sys", [{"role": "user", "content": "x"}], TOOLS, 100)
        assert "reasoning_effort" not in payload


class TestAToolCallsOwnDataSurvivesTheRoundTrip:
    def test_extra_content_is_read_off_the_response_and_replayed(self) -> None:
        """Gemini 3.x refuses a follow-up that drops a call's `thought_signature` (400)."""
        extra = {"google": {"thought_signature": "abc"}}
        turn = _provider()._parse_turn(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "f", "arguments": "{}"},
                                    "extra_content": extra,
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        assert turn.tool_calls[0].provider_extra == extra

        from app.ai.providers.openai_compatible import _to_wire

        wire = _to_wire(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": {}},
                            "extra_content": extra,
                        }
                    ],
                }
            ]
        )
        assert wire[0]["tool_calls"][0]["extra_content"] == extra


class TestItIsWiredUpWhereItNeedsToBe:
    def test_the_factory_knows_the_name(self) -> None:
        assert "gemini" in PROVIDER_NAMES

    def test_it_defaults_to_geminis_openai_compatible_endpoint(self) -> None:
        """So AI_BASE_URL is optional -- the base is not something an operator should
        have to know, and a typo in it is a 404 that reads like a dead key."""
        assert _provider()._base_url == DEFAULT_BASE_URL
        assert DEFAULT_BASE_URL.endswith("/v1beta/openai")

    def test_an_explicit_base_url_still_wins(self) -> None:
        provider = GeminiProvider(
            api_key="k", model="m", timeout_seconds=1.0, base_url="http://localhost:9/v1"
        )

        assert provider._base_url == "http://localhost:9/v1"

    def test_it_reports_its_own_name_rather_than_the_base_classs(self) -> None:
        """`provider.name` reaches the cache key, the spend ledger and every honesty
        footnote, so a Gemini run filed as `openai_compatible` is a run nobody can find."""
        assert _provider().name == "gemini"

    def test_the_key_is_sent_as_a_bearer_token(self) -> None:
        assert _provider()._headers()["authorization"] == "Bearer test-key"

    def test_a_gemini_provider_without_a_key_is_refused_by_the_factory(self) -> None:
        """The endpoint answers 401 for a missing key, which reads like a bad key."""
        from app.ai.provider import LLMUnavailable
        from app.ai.providers import get_provider

        get_provider.cache_clear()
        from app.core.config import settings

        before_provider, before_key = settings.ai_provider, settings.ai_api_key
        settings.ai_provider, settings.ai_api_key = "gemini", None
        try:
            with pytest.raises(LLMUnavailable, match="requires AI_API_KEY"):
                get_provider()
        finally:
            settings.ai_provider, settings.ai_api_key = before_provider, before_key
            get_provider.cache_clear()
