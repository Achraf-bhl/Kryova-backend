"""A conversation id handed to the browser must survive the turn that failed.

`chat_stream` emits `{"type": "start", "conversation_id": ...}` before it does
any work, and says why in its own comment: "a stream that dies mid-turn still
leaves a resumable conversation rather than an orphan." It did not. The row was
`flush`ed and not committed, and the `LLMError` handler's `db.rollback()` took
it with it -- so one provider hiccup on the very first message left the client
holding an id for a row that had never existed, and every later message in that
chat answered 404 "Conversation not found".

Reproduced against a live OpenRouter outage before it was fixed; pinned here so
it cannot come back.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai.provider import AssistantTurn, LLMError, LLMProvider, TokenUsage
from app.api.routes import ai as ai_routes


class _FailingProvider(LLMProvider):
    """A provider that dies the way a real one does: out of credits, 402, 5xx."""

    name = "failing-stub"

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("Chat failed (402): this request requires more credits")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        raise LLMError("Chat failed (402): this request requires more credits")


class _SilentProvider(LLMProvider):
    """Answers once, with no tool calls, so a turn can succeed cheaply."""

    name = "silent-stub"

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("not used in these tests")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        return AssistantTurn(text="Noted.", tool_calls=[], usage=TokenUsage(1, 1))


def _stream(auth_client: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Post one turn and return its decoded SSE events."""
    response = auth_client.post("/api/v1/ai/chat/stream", json=payload)
    assert response.status_code == 200, response.text
    events = []
    for block in response.text.split("\n\n"):
        line = next((ln for ln in block.split("\n") if ln.startswith("data:")), None)
        if line:
            events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture
def failing_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_routes, "get_provider", lambda: _FailingProvider())


@pytest.fixture
def silent_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_routes, "get_provider", lambda: _SilentProvider())


class TestConversationSurvivesAFailedTurn:
    def test_the_start_event_still_carries_an_id(
        self, auth_client: Any, failing_provider: None
    ) -> None:
        events = _stream(auth_client, {"message": "model a spur gear"})
        start = next(e for e in events if e["type"] == "start")
        assert start["conversation_id"]

    def test_the_turn_reports_the_provider_failure(
        self, auth_client: Any, failing_provider: None
    ) -> None:
        events = _stream(auth_client, {"message": "model a spur gear"})
        assert any(e["type"] == "error" for e in events)

    def test_that_id_is_readable_afterwards(self, auth_client: Any, failing_provider: None) -> None:
        events = _stream(auth_client, {"message": "model a spur gear"})
        conversation_id = next(e for e in events if e["type"] == "start")["conversation_id"]

        # This is the 404 the user hit: the id was real to the client and
        # missing from the database.
        response = auth_client.get(f"/api/v1/ai/conversations/{conversation_id}")
        assert response.status_code == 200, (
            "the conversation the client was told to store no longer exists; "
            "every later message in that chat will 404"
        )

    def test_the_next_message_continues_the_same_conversation(
        self, auth_client: Any, failing_provider: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = _stream(auth_client, {"message": "model a spur gear"})
        conversation_id = next(e for e in first if e["type"] == "start")["conversation_id"]

        # The provider recovers, exactly as it would once credits are topped up.
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _SilentProvider())
        second = _stream(
            auth_client,
            {"message": "use module 2 and 24 teeth", "conversation_id": conversation_id},
        )
        assert next(e for e in second if e["type"] == "start")["conversation_id"] == (
            conversation_id
        )
        assert not [e for e in second if e["type"] == "error"]

    def test_a_successful_turn_still_only_makes_one_conversation(
        self, auth_client: Any, silent_provider: None
    ) -> None:
        # The early commit must not leave a stray empty conversation behind.
        before = auth_client.get("/api/v1/ai/conversations").json()["total"]
        _stream(auth_client, {"message": "hello"})
        after = auth_client.get("/api/v1/ai/conversations").json()["total"]
        assert after == before + 1
