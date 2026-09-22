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


def _provider() -> GeminiProvider:
    return GeminiProvider(api_key="test-key", model="gemini-2.5-flash", timeout_seconds=30.0)


class TestReasoningIsOffExactlyWhereItIsWaste:
    def test_a_structured_call_asks_for_no_reasoning(self) -> None:
        """Measured on Gemini: 81 total tokens with reasoning against 21 without, on a
        question whose prompt and answer were 22 of them. At a 24-token cap the hidden
        tokens exhaust the budget and the call returns `finish_reason: length`."""
        payload = _provider()._structured_payload("sys", "user", Answer, 24)

        assert payload["reasoning_effort"] == NO_REASONING == "none"

    def test_an_agent_turn_does_not_carry_the_field_at_all(self) -> None:
        """A `chat()` turn is the agent building geometry over interdependent steps,
        which is where reasoning earns its cost. Pinning it to a default this code has
        not measured would be worse than leaving the vendor's own."""
        assert _provider()._extra_body() == {}

    def test_the_flag_is_restored_after_a_structured_call(self) -> None:
        provider = _provider()
        provider._structured_payload("sys", "user", Answer, 24)

        assert provider._thinking_now is True
        assert provider._extra_body() == {}

    def test_it_is_restored_even_when_the_call_raises(self) -> None:
        """The failure this guards is silent: a turn with reasoning left off still
        answers, so the agent would quietly stop thinking for the rest of the process."""
        provider = _provider()

        class Exploding(BaseModel):
            @classmethod
            def model_json_schema(cls, *args: object, **kwargs: object) -> dict:
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            provider._structured_payload("sys", "user", Exploding, 24)

        assert provider._thinking_now is True


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
