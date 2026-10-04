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
    MIN_WINDOW_TOKENS,
    _collapses_history,
    _first_within,
    _history_budget,
    build_messages,
    estimate_tokens,
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


# ---------------------------------------------------------------------------
# Sized in tokens. A message count cannot see weight.
# ---------------------------------------------------------------------------

HEAVY = 6_000  # characters: the most a stored tool result can hold


def _turns(count: int, *, heavy: int = HEAVY, start: int = 0) -> list[ConversationMessage]:
    """`count` whole turns, each a question, a tool call, a heavy result and an answer."""
    rows: list[ConversationMessage] = []
    sequence = start
    for turn in range(count):
        rows += [
            _message(sequence, MessageRole.USER, content=f"question {turn}"),
            _message(sequence + 1, MessageRole.ASSISTANT, content=None, tool_calls=[{"id": "c"}]),
            _message(
                sequence + 2, MessageRole.TOOL, content="r" * heavy, tool_call_id="c", tool_name="t"
            ),
            _message(sequence + 3, MessageRole.ASSISTANT, content=f"answer {turn}"),
        ]
        sequence += 4
    return rows


def _tokens(messages: list[ConversationMessage]) -> int:
    return sum(estimate_tokens(message) for message in messages)


class TestTheWindowIsSizedInTokens:
    def test_heavy_results_are_windowed_by_weight_where_the_count_would_keep_them_all(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_max_context_messages", 40)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 5_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 6_000)
        rows = _turns(6)  # 24 messages: far under the count, ~10k tokens
        kept = window(_conversation(rows))
        assert 0 < len(kept) < len(rows)
        assert _tokens(kept) <= 6_000

    def test_with_the_budget_off_only_the_count_applies(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_max_context_messages", 40)
        monkeypatch.setattr(settings, "ai_context_token_budget", 0)
        rows = _turns(6)
        assert len(window(_conversation(rows))) == len(rows)

    def test_the_count_still_applies_when_the_messages_are_light(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_max_context_messages", 8)
        rows = _turns(6, heavy=10)
        assert len(window(_conversation(rows))) <= 8

    @pytest.mark.parametrize("budget", [300, 900, 2_000, 4_000, 7_500, 12_000])
    def test_a_window_cut_by_weight_never_starts_inside_a_tool_exchange(
        self, monkeypatch: pytest.MonkeyPatch, budget: int
    ):
        # The weight cut lands wherever it lands -- on a tool result, between a call and
        # its answer. The start must still be advanced to a question, or every hosted
        # provider answers 400 to an orphaned result.
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", budget // 2)
        monkeypatch.setattr(settings, "ai_context_token_budget", budget)
        kept = window(_conversation(_turns(6)))
        assert kept[0].role is MessageRole.USER

    def test_the_question_being_answered_survives_a_budget_it_alone_exceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 500)
        monkeypatch.setattr(settings, "ai_context_token_budget", 1_000)
        rows = [_message(0, MessageRole.USER, content="the question")]
        for index in range(1, 13, 2):
            rows.append(_message(index, MessageRole.ASSISTANT, content=None, tool_calls=[{"id": "x"}]))
            rows.append(
                _message(index + 1, MessageRole.TOOL, content="r" * HEAVY, tool_call_id="x", tool_name="t")
            )
        assert window(_conversation(rows))[0].content == "the question"

    def test_one_enormous_message_does_not_empty_the_window(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 500)
        monkeypatch.setattr(settings, "ai_context_token_budget", 1_000)
        rows = [_message(0, MessageRole.USER, content="x" * 200_000)]
        assert window(_conversation(rows)) == rows

    def test_a_lone_heavy_answer_with_no_question_left_is_still_replayed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # The question was folded into the summary, so there is no user turn to anchor on
        # and the assistant's own words are all the continuity there is. A budget it
        # exceeds must not turn that into an empty replay.
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 500)
        monkeypatch.setattr(settings, "ai_context_token_budget", 1_000)
        rows = [_message(5, MessageRole.ASSISTANT, content="a" * 200_000)]
        assert window(_conversation(rows, through=5)) == rows

    def test_the_summary_takes_its_share_of_the_budget(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 8_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 12_000)
        without = _conversation(_turns(8))
        with_summary = _conversation(_turns(8))
        with_summary.summary = "s" * 30_000  # ~8,300 tokens
        assert len(window(with_summary)) < len(window(without))

    def test_a_huge_summary_cannot_squeeze_the_window_to_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Budget less summary goes negative here; the floor is what is left. Light
        # messages all fit under it, heavy ones are bounded by it -- and neither case
        # may read the arithmetic as "the setting is off", which is what zero means.
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 8_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 12_000)
        light = _turns(3, heavy=100)  # ~40 tokens a message
        conversation = _conversation(light)
        conversation.summary = "s" * 4_000_000
        assert len(window(conversation)) == len(light)

        heavy = _conversation(_turns(6))
        heavy.summary = "s" * 4_000_000
        kept = window(heavy)
        assert kept and _tokens(kept) <= MIN_WINDOW_TOKENS

    def test_the_window_moves_a_whole_turn_at_a_time_never_a_message_at_a_time(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """What keeps the prompt cacheable: a start that slid per message would change the
        front of the prompt on every step. Appending one turn's messages to a transcript
        already over budget moves the start only to a later question, and only a few times."""
        monkeypatch.setattr(settings, "ai_max_context_messages", 400)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 5_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 6_000)
        rows = _turns(5)
        conversation = _conversation(list(rows))
        tail = _turns(1, start=rows[-1].sequence + 1)
        starts = [window(conversation)[0].sequence]
        for message in tail:
            conversation.messages.append(message)
            starts.append(window(conversation)[0].sequence)
        changes = sum(1 for before, after in zip(starts, starts[1:]) if before != after)
        assert changes < len(tail)
        assert starts == sorted(starts)


class TestTheFoldFiresOnTokens:
    @staticmethod
    def _heavy(count: int = 8) -> list[ConversationMessage]:
        rows: list[ConversationMessage] = []
        for index in range(count):
            rows.append(_message(2 * index, MessageRole.USER, content="x" * 3_000))
            rows.append(_message(2 * index + 1, MessageRole.ASSISTANT, content="y" * 3_000))
        return rows

    def test_a_few_heavy_messages_fold_where_the_count_would_not(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 4_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 8_000)
        assert fold_boundary(_conversation(self._heavy())) is not None  # 16 messages, ~13k tokens

    def test_with_the_budget_off_the_same_transcript_does_not_fold(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_context_token_budget", 0)
        assert fold_boundary(_conversation(self._heavy())) is None

    def test_light_messages_below_both_thresholds_do_not_fold(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 4_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 8_000)
        rows = [_message(index, MessageRole.USER, content="x") for index in range(10)]
        assert fold_boundary(_conversation(rows)) is None

    def test_a_fold_triggered_by_weight_reclaims_weight(self, monkeypatch: pytest.MonkeyPatch):
        """Without a token bound on what is kept, the fold keeps the last 15 messages -- which
        are the heavy ones -- and is due again on the very next turn, summarising every turn."""
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 4_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 8_000)
        rows = self._heavy() + [_message(16, MessageRole.USER, content="next")]
        boundary = fold_boundary(_conversation(rows))
        assert boundary is not None
        after = _conversation(rows, through=boundary)
        assert _tokens([m for m in rows if m.sequence >= boundary]) <= 4_000
        assert fold_boundary(after) is None

    def test_a_weight_fold_never_lands_inside_a_tool_exchange(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 3_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 6_000)
        rows = _turns(6) + [_message(24, MessageRole.USER, content="next")]
        boundary = fold_boundary(_conversation(rows))
        assert boundary is not None
        landed = next(message for message in rows if message.sequence == boundary)
        assert landed.role is not MessageRole.TOOL

    def test_the_newest_message_is_kept_however_heavy_it_is(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 1_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 2_000)
        rows = self._heavy(3) + [_message(6, MessageRole.USER, content="z" * 100_000)]
        boundary = fold_boundary(_conversation(rows))
        assert boundary == 6

    def test_a_token_fold_records_the_summary_like_any_other(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_messages", 30)
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 4_000)
        monkeypatch.setattr(settings, "ai_context_token_budget", 8_000)
        conversation = _conversation(self._heavy() + [_message(16, MessageRole.USER, content="next")])
        provider = _StubProvider("decisions: steel, 5 mm wall")
        maybe_summarise(_StubSession(), provider, conversation)  # type: ignore[arg-type]
        assert provider.calls == 1
        assert conversation.summary == "decisions: steel, 5 mm wall"
        assert conversation.summary_through_sequence > 0


class TestTheEstimate:
    def test_a_longer_message_weighs_more(self):
        short = _message(0, MessageRole.USER, content="x" * 36)
        long = _message(1, MessageRole.USER, content="x" * 3_600)
        assert estimate_tokens(long) - estimate_tokens(short) == 990  # 3,564 more characters

    def test_the_arguments_of_a_tool_call_count_as_well_as_the_words(self):
        bare = _message(0, MessageRole.ASSISTANT, content="ok", tool_calls=None)
        calling = _message(
            1,
            MessageRole.ASSISTANT,
            content="ok",
            tool_calls=[{"id": "c", "function": {"name": "t", "arguments": {"k": "v" * 3_600}}}],
        )
        assert estimate_tokens(calling) > estimate_tokens(bare) + 1_000

    def test_an_empty_message_still_costs_its_framing(self):
        assert estimate_tokens(_message(0, MessageRole.USER, content="")) > 0

    def test_it_never_under_counts_a_sum(self):
        # Rounded up per message: three one-character messages are not "zero tokens".
        rows = [_message(index, MessageRole.USER, content="x") for index in range(3)]
        assert _tokens(rows) >= 3


class TestWhereTheWeightCutFalls:
    """`_first_within` is the one place a budget becomes an index, so it is pinned directly:
    the window start is advanced to a question afterwards and hides a cut one message off."""

    @staticmethod
    def _each_14() -> list[ConversationMessage]:
        # 4 tokens of framing + ceil(36 / 3.6) = 14 tokens apiece
        return [_message(index, MessageRole.USER, content="x" * 36) for index in range(3)]

    @pytest.mark.parametrize(
        ("budget", "expected"),
        [(100, 0), (42, 0), (41, 1), (28, 1), (27, 2), (14, 2), (13, 3), (0, 3)],
    )
    def test_the_oldest_message_that_still_fits(self, budget: int, expected: int):
        assert estimate_tokens(self._each_14()[0]) == 14
        assert _first_within(self._each_14(), budget) == expected


class TestTheBudgetLeftForMessages:
    def test_no_summary_leaves_the_whole_budget(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 500)
        monkeypatch.setattr(settings, "ai_context_token_budget", 1_000)
        assert _history_budget(_conversation([])) == 1_000

    def test_a_giant_summary_leaves_the_floor_not_zero_and_not_less(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_context_token_budget", 30_000)
        conversation = _conversation([])
        conversation.summary = "s" * 4_000_000
        assert _history_budget(conversation) == MIN_WINDOW_TOKENS

    def test_the_floor_never_overrides_a_smaller_configured_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(settings, "ai_summarise_after_tokens", 400)
        monkeypatch.setattr(settings, "ai_context_token_budget", 800)
        conversation = _conversation([])
        conversation.summary = "s" * 4_000_000
        assert _history_budget(conversation) == 800

    def test_off_is_off(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(settings, "ai_context_token_budget", 0)
        assert _history_budget(_conversation([])) == 0
