"""The agent measured per duration bucket (master plan E22 task 3).

What is worth pinning here is not the arithmetic — it is the set of ways this
harness could produce a number that looks like a reliability measurement and is
not one: a rate over an empty bucket, a success rate inferred from completion, a
literature figure carrying a value nobody sourced, and a turn split on the loop's
own control messages.

Written on Linux and **not run** (the user's rule; Windows runs them — THE QUEUE
D6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.ai import prompts
from app.models import Conversation, ConversationMessage, MessageRole, User
from app.verify.horizon import (
    DEFAULT_BUCKETS,
    LITERATURE,
    NOT_A_SUCCESS_RATE,
    Basis,
    Bucket,
    Figure,
    TurnTrace,
    measure,
    read_traces,
)

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def trace(seconds: float, *, completed: bool = True, sequence: int = 0, conversation: str = "c1"):
    return TurnTrace(
        conversation_id=conversation,
        sequence=sequence,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=seconds),
        steps=1,
        answered=completed,
        truncated=not completed,
    )


class TestAnUnsourcedFigureCannotBeCompared:
    """`nafems.Target`'s rule, in the phase that quotes the most numbers.

    A remembered figure with a plausible citation is indistinguishable, once
    published, from one somebody checked. Here the same move is refused one
    level earlier: an unsourced figure may not carry a value at all.
    """

    def test_every_quoted_figure_is_marked_unsourced_today(self) -> None:
        assert [figure.id for figure in LITERATURE if figure.basis is not Basis.UNSOURCED] == []

    def test_no_unsourced_figure_carries_a_number(self) -> None:
        assert [figure.id for figure in LITERATURE if figure.value is not None] == []

    def test_an_unsourced_figure_with_a_value_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must not be comparable"):
            Figure(id="x", claim="c", basis=Basis.UNSOURCED, value=0.44, note="why")

    def test_an_unsourced_figure_must_say_why(self) -> None:
        with pytest.raises(ValueError, match="does not say why"):
            Figure(id="x", claim="c", basis=Basis.UNSOURCED)

    def test_a_read_figure_must_name_where_and_when(self) -> None:
        with pytest.raises(ValueError, match="citation nobody can follow"):
            Figure(id="x", claim="c", basis=Basis.READ, value=0.44)

    def test_a_read_figure_is_accepted_with_both(self) -> None:
        figure = Figure(
            id="x",
            claim="c",
            basis=Basis.READ,
            value=0.44,
            url="https://example.invalid/paper",
            read_on="2026-09-15",
        )

        assert figure.value == 0.44


class TestARateOverNothingIsNothing:
    def test_an_empty_bucket_reports_none_not_zero(self) -> None:
        """A zero on a chart is a measurement. `None` is the absence of one."""
        report = measure([trace(30)])

        empty = [one for one in report.buckets if one.turns == 0]
        assert empty
        assert all(one.completion_rate is None for one in empty)

    def test_a_populated_bucket_reports_its_rate(self) -> None:
        report = measure([trace(30), trace(40, completed=False, sequence=1)])

        first = report.buckets[0]
        assert first.turns == 2
        assert first.completion_rate == 0.5


class TestSuccessIsNeverInferredFromCompletion:
    """The distinction the whole phase turns on.

    A turn that confidently builds the wrong bracket completes perfectly, so
    completion cannot stand in for success. Anything that quietly let it would
    publish a success rate nobody measured.
    """

    def test_without_labels_every_success_rate_is_none(self) -> None:
        report = measure([trace(30), trace(90, sequence=1)])

        assert all(one.success_rate is None for one in report.buckets)

    def test_a_completed_turn_is_not_counted_as_a_success(self) -> None:
        report = measure([trace(30)], outcomes={("c1", 0): False})

        first = report.buckets[0]
        assert first.completion_rate == 1.0
        assert first.success_rate == 0.0

    def test_only_labelled_turns_are_in_the_success_denominator(self) -> None:
        report = measure(
            [trace(30), trace(40, sequence=1)],
            outcomes={("c1", 0): True},
        )

        first = report.buckets[0]
        assert first.turns == 2
        assert first.labelled == 1
        assert first.success_rate == 1.0

    def test_the_caveat_travels_with_the_report(self) -> None:
        assert measure([]).to_dict()["statement"] == NOT_A_SUCCESS_RATE


class TestBuckets:
    def test_a_turn_lands_in_exactly_one_bucket(self) -> None:
        report = measure([trace(seconds) for seconds in (0, 59, 60, 299, 300, 3_600)])

        assert sum(one.turns for one in report.buckets) == 6
        assert report.traces == 6

    def test_a_boundary_belongs_to_the_bucket_above(self) -> None:
        """Half-open, so two people's histograms of the same data agree."""
        assert Bucket("b", 60.0, 300.0).holds(60.0)
        assert not Bucket("b", 60.0, 300.0).holds(300.0)

    def test_the_last_bucket_is_open_ended(self) -> None:
        assert DEFAULT_BUCKETS[-1].high_s is None
        assert DEFAULT_BUCKETS[-1].holds(86_400.0)


class TestWhatCountsAsCompleted:
    def test_a_truncated_turn_is_not_completed(self) -> None:
        assert not TurnTrace("c", 0, NOW, NOW, 1, answered=True, truncated=True).completed

    def test_a_turn_with_no_answer_is_not_completed(self) -> None:
        assert not TurnTrace("c", 0, NOW, NOW, 1, answered=False, truncated=False).completed

    def test_a_tool_left_failing_is_not_completed(self) -> None:
        left = TurnTrace(
            "c", 0, NOW, NOW, 1, answered=True, truncated=False, failed_tools=("catia_pad",)
        )

        assert not left.completed


class TestReadingTheDurableRecord:
    """`TurnEvent` is a ten-minute buffer, so the study reads the transcript.

    A harness over the buffer would silently measure the last ten minutes of
    traffic and report it as history.
    """

    @pytest.fixture
    def conversation(self, db_session: Session) -> Conversation:
        user = User(email="horizon@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(user)
        db_session.flush()
        row = Conversation(owner_id=user.id, title="t")
        db_session.add(row)
        db_session.flush()
        return row

    def _message(
        self,
        db: Session,
        conversation: Conversation,
        sequence: int,
        role: MessageRole,
        content: str,
        at: datetime,
    ) -> None:
        db.add(
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=sequence,
                role=role,
                content=content,
                created_at=at,
            )
        )

    def test_a_turn_runs_from_a_user_message_to_the_last_before_the_next(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        self._message(db_session, conversation, 0, MessageRole.USER, "first", NOW)
        self._message(
            db_session, conversation, 1, MessageRole.ASSISTANT, "done", NOW + timedelta(seconds=30)
        )
        self._message(
            db_session, conversation, 2, MessageRole.USER, "second", NOW + timedelta(minutes=10)
        )
        self._message(
            db_session,
            conversation,
            3,
            MessageRole.ASSISTANT,
            "done again",
            NOW + timedelta(minutes=12),
        )
        db_session.flush()

        traces = read_traces(db_session, conversation_ids=[conversation.id])

        assert len(traces) == 2
        assert traces[0].duration_s == 30.0
        assert traces[1].duration_s == 120.0

    def test_a_control_message_does_not_open_a_turn(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """The loop injects its own instructions as user messages, because a
        provider takes no other role for them. Splitting on those would report
        one long turn as several short ones — the exact direction that would
        make this product look more reliable than it is."""
        self._message(db_session, conversation, 0, MessageRole.USER, "build it", NOW)
        self._message(
            db_session,
            conversation,
            1,
            MessageRole.USER,
            prompts.CONTROL_NOTE + "keep going",
            NOW + timedelta(seconds=10),
        )
        self._message(
            db_session,
            conversation,
            2,
            MessageRole.ASSISTANT,
            "done",
            NOW + timedelta(seconds=600),
        )
        db_session.flush()

        traces = read_traces(db_session, conversation_ids=[conversation.id])

        assert len(traces) == 1
        assert traces[0].duration_s == 600.0

    def test_a_tool_error_later_superseded_does_not_count(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """`app/ai/resume.py`'s rule: keyed on the tool, cleared by a later
        success, or every superseded retry counts for ever."""
        self._message(db_session, conversation, 0, MessageRole.USER, "build it", NOW)
        db_session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=1,
                role=MessageRole.TOOL,
                tool_name="catia_pad",
                content="{}",
                is_error=True,
                created_at=NOW + timedelta(seconds=5),
            )
        )
        db_session.add(
            ConversationMessage(
                conversation_id=conversation.id,
                sequence=2,
                role=MessageRole.TOOL,
                tool_name="catia_pad",
                content="{}",
                is_error=False,
                created_at=NOW + timedelta(seconds=9),
            )
        )
        self._message(
            db_session, conversation, 3, MessageRole.ASSISTANT, "built", NOW + timedelta(seconds=20)
        )
        db_session.flush()

        traces = read_traces(db_session, conversation_ids=[conversation.id])

        assert traces[0].failed_tools == ()
        assert traces[0].completed

    def test_a_trace_carries_no_message_text(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """A study file must not become a second copy of every conversation —
        including the quoted contents of everyone's attachments."""
        self._message(db_session, conversation, 0, MessageRole.USER, "a secret question", NOW)
        self._message(
            db_session, conversation, 1, MessageRole.ASSISTANT, "an answer", NOW + timedelta(1)
        )
        db_session.flush()

        traces = read_traces(db_session, conversation_ids=[conversation.id])

        assert "secret" not in repr(traces[0])
