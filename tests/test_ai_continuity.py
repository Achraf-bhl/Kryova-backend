"""What a long conversation must not forget.

Continuity in this system rests on two mechanisms that both fail silently. The
rolling window can leave nothing to replay; the running summary can be replaced
by one that dropped most of what it held. Neither raises, neither logs anything
a user sees, and the symptom in both cases is an assistant that contradicts
itself several turns later for no visible reason.

These tests build `Conversation` and `ConversationMessage` rows in memory
without a session, so the file needs no database and runs in the fast offline
loop. `maybe_summarise` is given a stub session and a stub provider for the same
reason: what is being checked is a decision, not a write.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.context import (
    MIN_SUMMARY_RETENTION,
    _collapses_history,
    build_messages,
    fold_boundary,
    maybe_summarise,
    window,
)
from app.ai.provider import LLMError, TokenUsage
from app.core.config import settings
from app.models import Conversation, ConversationMessage, MessageRole


def _message(sequence: int, role: MessageRole, **fields: Any) -> ConversationMessage:
    """A transcript row, populated enough to be replayed. Never persisted."""
    row = ConversationMessage(sequence=sequence, role=role, **fields)
    # Column defaults are applied on flush, and nothing here flushes.
    if row.tool_calls is None:
        row.tool_calls = fields.get("tool_calls")
    row.is_error = bool(fields.get("is_error", False))
    return row


def _conversation(messages: list[ConversationMessage], *, through: int = 0) -> Conversation:
    row = Conversation(title="t", summary_through_sequence=through)
    row.summary = None
    row.messages = messages
    return row


class _StubSession:
    """Just enough Session for `maybe_summarise`."""

    def __init__(self) -> None:
        self.flushed = 0

    def flush(self) -> None:
        self.flushed += 1


class _StubProvider:
    """Returns a canned summary, or raises, on demand."""

    def __init__(self, text: str | None = None, *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0

    def chat(self, **_: Any) -> Any:
        self.calls += 1
        if self.fail:
            raise LLMError("provider down")

        class _Turn:
            pass

        turn = _Turn()
        turn.text = self.text or ""
        turn.usage = TokenUsage(prompt_tokens=10, completion_tokens=5)
        return turn


# ---------------------------------------------------------------------------
# The rolling window.
# ---------------------------------------------------------------------------


class TestWindow:
    def test_a_window_always_starts_on_a_user_turn(self, monkeypatch: pytest.MonkeyPatch):
        # A window opening on an orphaned tool_result is a 400 from every hosted
        # provider, which kills the conversation rather than degrading it.
        monkeypatch.setattr(settings, "ai_max_context_messages", 3)
        conversation = _conversation(
            [
                _message(0, MessageRole.USER, content="build a bracket"),
                _message(1, MessageRole.ASSISTANT, content=None, tool_calls=[{"id": "1"}]),
                _message(2, MessageRole.TOOL, content="{}", tool_call_id="1", tool_name="t"),
                _message(3, MessageRole.ASSISTANT, content="done"),
            ]
        )
        replayed = window(conversation)
        assert replayed[0].role is MessageRole.USER

    def test_the_question_being_answered_is_never_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # One turn can produce more messages than the whole window: twenty tool
        # rounds each write an assistant turn plus a result. A naive tail pushes
        # the user's own message out mid-loop, leaving the model to infer what
        # it was asked from tool output alone.
        monkeypatch.setattr(settings, "ai_max_context_messages", 4)
        messages = [_message(0, MessageRole.USER, content="the question")]
        for index in range(1, 12, 2):
            messages.append(
                _message(index, MessageRole.ASSISTANT, content=None, tool_calls=[{"id": "x"}])
            )
            messages.append(
                _message(index + 1, MessageRole.TOOL, content="{}", tool_call_id="x", tool_name="t")
            )

        replayed = window(_conversation(messages))
        assert replayed[0].content == "the question"

    def test_folded_messages_are_not_replayed_again(self):
        conversation = _conversation(
            [
                _message(0, MessageRole.USER, content="old"),
                _message(1, MessageRole.ASSISTANT, content="older answer"),
                _message(2, MessageRole.USER, content="recent"),
            ],
            through=2,
        )
        assert [message.content for message in window(conversation)] == ["recent"]

    def test_an_empty_conversation_replays_nothing(self):
        assert window(_conversation([])) == []

    def test_with_no_user_turn_left_the_assistant_still_speaks_for_itself(self):
        # The regression this branch exists for. Returning [] here left the next
        # turn with only the summary and the state block -- neither of which
        # carries what the assistant *just said*, so it could contradict its own
        # last answer with nothing available to notice.
        conversation = _conversation(
            [
                _message(0, MessageRole.USER, content="folded away"),
                _message(1, MessageRole.ASSISTANT, content="the answer I just gave"),
            ],
            through=1,
        )
        replayed = window(conversation)
        assert [message.content for message in replayed] == ["the answer I just gave"]

    def test_but_never_an_orphaned_tool_exchange(self):
        # Assistant turns replay standalone; a tool_result without its call is
        # exactly what providers reject, so those must still be filtered.
        conversation = _conversation(
            [
                _message(0, MessageRole.USER, content="folded away"),
                _message(1, MessageRole.ASSISTANT, content="narration", tool_calls=[{"id": "1"}]),
                _message(2, MessageRole.TOOL, content="{}", tool_call_id="1", tool_name="t"),
                _message(3, MessageRole.ASSISTANT, content="plain answer"),
            ],
            through=1,
        )
        replayed = window(conversation)
        assert all(message.role is MessageRole.ASSISTANT for message in replayed)
        assert all(not message.tool_calls for message in replayed)
        assert [message.content for message in replayed] == ["plain answer"]


# ---------------------------------------------------------------------------
# Folding.
# ---------------------------------------------------------------------------


class TestFoldBoundary:
    def test_no_fold_before_the_threshold(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        messages = [_message(index, MessageRole.USER, content="x") for index in range(5)]
        assert fold_boundary(_conversation(messages)) is None

    def test_a_fold_never_lands_inside_a_tool_exchange(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 4)
        messages = [
            _message(index, MessageRole.TOOL, content="{}", tool_call_id="1", tool_name="t")
            if index % 2
            else _message(index, MessageRole.ASSISTANT, content="a")
            for index in range(12)
        ]
        boundary = fold_boundary(_conversation(messages))
        assert boundary is not None
        assert messages[boundary].role is not MessageRole.TOOL


class TestSummaryCollapse:
    def test_a_first_summary_has_nothing_to_lose(self):
        assert not _collapses_history(None, "anything")
        assert not _collapses_history("", "anything")

    def test_tightened_wording_is_accepted(self):
        previous = "x" * 100
        assert not _collapses_history(previous, "y" * 80)

    def test_halving_the_record_is_a_collapse(self):
        previous = "x" * 100
        assert _collapses_history(previous, "y" * (int(100 * MIN_SUMMARY_RETENTION) - 1))

    def test_growth_is_always_fine(self):
        assert not _collapses_history("x" * 100, "y" * 400)


class TestMaybeSummarise:
    @staticmethod
    def _long(monkeypatch: pytest.MonkeyPatch) -> Conversation:
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 4)
        messages = [
            _message(index, MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT, content="m")
            for index in range(12)
        ]
        return _conversation(messages)

    def test_a_fold_records_the_summary_and_advances_the_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        conversation = self._long(monkeypatch)
        session, provider = _StubSession(), _StubProvider("decisions: steel, 5 mm wall")
        maybe_summarise(session, provider, conversation)  # type: ignore[arg-type]
        assert conversation.summary == "decisions: steel, 5 mm wall"
        assert conversation.summary_through_sequence > 0

    def test_a_provider_outage_costs_memory_not_the_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        conversation = self._long(monkeypatch)
        maybe_summarise(_StubSession(), _StubProvider(fail=True), conversation)  # type: ignore[arg-type]
        assert conversation.summary is None
        assert conversation.summary_through_sequence == 0

    def test_an_empty_summary_never_replaces_a_real_one(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        conversation = self._long(monkeypatch)
        conversation.summary = "the record so far"
        maybe_summarise(_StubSession(), _StubProvider("   "), conversation)  # type: ignore[arg-type]
        assert conversation.summary == "the record so far"

    def test_a_collapsed_summary_is_discarded_and_retried_next_turn(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The summariser was asked to merge; a result a fraction of the size
        # means it summarised the summary instead. Accepting it loses the
        # material silently, and nothing afterwards records that it existed.
        conversation = self._long(monkeypatch)
        conversation.summary = "a long and detailed record " * 20
        before = conversation.summary

        maybe_summarise(_StubSession(), _StubProvider("brief."), conversation)  # type: ignore[arg-type]

        assert conversation.summary == before
        # Boundary unmoved, so the same messages are still eligible and the fold
        # is simply attempted again.
        assert conversation.summary_through_sequence == 0

    def test_nothing_to_fold_costs_no_provider_call(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        provider = _StubProvider("unused")
        usage = maybe_summarise(
            _StubSession(),
            provider,  # type: ignore[arg-type]
            _conversation([_message(0, MessageRole.USER, content="hi")]),
        )
        assert provider.calls == 0
        assert usage.prompt_tokens == 0


class TestBuildMessages:
    def test_the_state_block_sits_directly_before_the_newest_question(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Chosen for prompt caching: everything ahead of the block is stable
        # across turns and caches, and the block -- the one part that changes
        # every turn -- sits as late as possible.
        monkeypatch.setattr("app.ai.context.build_state_block", lambda *_: "STATE")
        conversation = _conversation(
            [
                _message(0, MessageRole.USER, content="first"),
                _message(1, MessageRole.ASSISTANT, content="answer"),
                _message(2, MessageRole.USER, content="second"),
            ]
        )
        built = build_messages(None, None, conversation)  # type: ignore[arg-type]
        contents = [entry["content"] for entry in built]
        assert contents.index("STATE") == contents.index("second") - 1

    def test_the_summary_leads_when_there_is_one(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("app.ai.context.build_state_block", lambda *_: "STATE")
        conversation = _conversation([_message(0, MessageRole.USER, content="q")])
        conversation.summary = "earlier decisions"
        built = build_messages(None, None, conversation)  # type: ignore[arg-type]
        assert "earlier decisions" in built[0]["content"]
