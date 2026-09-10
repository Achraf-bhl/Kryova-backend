"""Resuming a dropped stream, and streaming the answer as it is written (P5.1).

Both halves of task 1 that were absent, and the plan said so: token streaming
was a *provider contract* gap rather than a frontend one, and reconnect-and-
resume needed an event cursor on the wire so a reconnect could say where it got
to.

What is worth pinning here is the ways a resume can be worse than no resume: a
replay that silently omits the events it no longer has, a cursor that is reused
across turns, a POST-shaped "resume" that starts a second turn, and a recording
failure that takes down the turn it was only supposed to be a buffer for.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai import turn_events
from app.ai.provider import AssistantTurn, Finished, LLMProvider, TextDelta, TokenUsage
from app.models import Conversation, TurnEvent, User

API = "/api/v1"


@pytest.fixture
def owner(db_session: Session) -> User:
    user = User(email="streamer@kryova.dev", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def conversation(db_session: Session, owner: User) -> Conversation:
    row = Conversation(title="Bracket", owner_id=owner.id)
    db_session.add(row)
    db_session.flush()
    return row


class TestTheCursor:
    def test_sequences_are_monotonic_within_a_conversation(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        first = turn_events.record(db_session, conversation.id, "turn-a", {"type": "start"})
        second = turn_events.record(db_session, conversation.id, "turn-a", {"type": "thinking"})

        assert (first, second) == (1, 2)

    def test_a_second_turn_continues_the_numbering_rather_than_restarting(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """A cursor has to be unambiguous across a turn boundary. A client that
        reconnects saying "I had up to 2" must not be handed turn 2's event 3
        when it meant turn 1's."""
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "start"})
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "done"})

        third = turn_events.record(db_session, conversation.id, "turn-b", {"type": "start"})

        assert third == 3

    def test_two_conversations_number_independently(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        other = Conversation(title="Other", owner_id=owner.id)
        db_session.add(other)
        db_session.flush()
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "start"})

        assert turn_events.record(db_session, other.id, "turn-b", {"type": "start"}) == 1

    def test_reading_after_a_cursor_returns_only_what_follows(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        for index in range(4):
            turn_events.record(
                db_session, conversation.id, "turn-a", {"type": "thinking", "step": index}
            )

        replay = turn_events.read_after(db_session, conversation.id, 2)

        assert [event["seq"] for event in replay.events] == [3, 4]
        assert not replay.gap

    def test_the_replayed_payload_is_what_went_out_plus_its_cursor(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """A replayed event and a live one must be byte-identical apart from
        the cursor, or the client needs two code paths for one wire format."""
        turn_events.record(
            db_session, conversation.id, "turn-a", {"type": "narration", "content": "Checking."}
        )

        event = turn_events.read_after(db_session, conversation.id, 0).events[0]

        assert event == {
            "type": "narration",
            "content": "Checking.",
            "seq": 1,
            "turn_id": "turn-a",
        }


class TestARetentionGap:
    def test_a_pruned_hole_is_reported_rather_than_papered_over(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Handing back a turn with a silent bite out of the middle renders as
        an agent that skipped three steps — the one reading that must never be
        available."""
        for _ in range(3):
            turn_events.record(db_session, conversation.id, "turn-a", {"type": "thinking"})
        db_session.query(TurnEvent).filter(TurnEvent.sequence <= 2).delete()
        db_session.flush()

        replay = turn_events.read_after(db_session, conversation.id, 1)

        assert replay.gap

    def test_no_gap_is_reported_when_the_next_event_is_the_expected_one(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        for _ in range(3):
            turn_events.record(db_session, conversation.id, "turn-a", {"type": "thinking"})

        assert not turn_events.read_after(db_session, conversation.id, 2).gap

    def test_a_client_with_no_cursor_never_sees_a_gap(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """`after=0` means "whatever you still hold", so there is nothing it
        expected and nothing to be missing."""
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "thinking"})
        db_session.query(TurnEvent).delete()
        db_session.flush()

        assert not turn_events.read_after(db_session, conversation.id, 0).gap

    def test_pruning_deletes_past_the_retention_window_and_keeps_the_rest(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "old"})
        turn_events.record(db_session, conversation.id, "turn-a", {"type": "new"})
        old = db_session.query(TurnEvent).filter(TurnEvent.sequence == 1).one()
        old.created_at = turn_events.utcnow() - timedelta(
            minutes=turn_events.RETENTION_MINUTES + 1
        )
        db_session.flush()

        removed = turn_events.prune(db_session)

        assert removed == 1
        assert db_session.query(TurnEvent).count() == 1


class TestTheResumeRoute:
    def test_it_replays_from_the_cursor_and_closes_on_the_terminal_event(
        self, auth_client, db_session: Session, current_user_id: str
    ) -> None:
        conversation = Conversation(title="Bracket", owner_id=current_user_id)
        db_session.add(conversation)
        db_session.flush()
        turn_events.record(db_session, conversation.id, "t", {"type": "start"})
        turn_events.record(db_session, conversation.id, "t", {"type": "narration"})
        turn_events.record(db_session, conversation.id, "t", {"type": "done"})
        db_session.flush()

        response = auth_client.get(
            f"{API}/ai/conversations/{conversation.id}/stream?after=1"
        )

        assert response.status_code == 200
        assert '"narration"' in response.text
        assert '"start"' not in response.text
        assert '"done"' in response.text

    def test_a_gap_answers_with_reload_advice_and_stops(
        self, auth_client, db_session: Session, current_user_id: str
    ) -> None:
        conversation = Conversation(title="Bracket", owner_id=current_user_id)
        db_session.add(conversation)
        db_session.flush()
        for _ in range(3):
            turn_events.record(db_session, conversation.id, "t", {"type": "thinking"})
        db_session.query(TurnEvent).filter(TurnEvent.sequence <= 2).delete()
        db_session.flush()

        response = auth_client.get(
            f"{API}/ai/conversations/{conversation.id}/stream?after=1"
        )

        assert "resume_gap" in response.text
        assert "Reload the conversation" in response.text
        assert '"thinking"' not in response.text

    def test_another_users_conversation_is_404_not_403(
        self, auth_client, db_session: Session
    ) -> None:
        stranger = User(email="stranger2@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(stranger)
        db_session.flush()
        theirs = Conversation(title="Theirs", owner_id=stranger.id)
        db_session.add(theirs)
        db_session.flush()

        response = auth_client.get(f"{API}/ai/conversations/{theirs.id}/stream")

        assert response.status_code == 404

    def test_resuming_is_a_get_so_it_cannot_start_a_second_turn(self) -> None:
        """A POST-shaped resume would mean a flaky connection doubles every turn
        it interrupts. Asserted rather than left as a convention.

        Walked with `tests.routes.declared`, not with `app.routes` directly:
        that list is not flat, and iterating it finds nine paths out of a
        hundred and fifty — so this check would have passed by finding nothing
        at all, which is the worst way for a guard to be wrong.
        """
        from tests.routes import methods_for

        assert methods_for("/ai/conversations/{conversation_id}/stream") == {"GET"}


class _StreamingProvider(LLMProvider):
    """A provider that streams, for the loop test. Nothing here reaches a model."""

    name = "fake-streaming"
    model = "fake"

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, **kwargs: object):  # pragma: no cover - unused
        raise NotImplementedError

    def chat(self, **kwargs: object) -> AssistantTurn:
        return AssistantTurn(text=self._text, usage=TokenUsage(1, 1))

    def stream_chat(self, **kwargs: object):
        for piece in self._text.split(" "):
            yield TextDelta(piece + " ")
        yield Finished(AssistantTurn(text=self._text, usage=TokenUsage(1, 1)))

    def health(self) -> None:
        return None


class _SilentProvider(_StreamingProvider):
    """One that cannot stream — it inherits the base `stream_chat`."""

    name = "fake-silent"

    stream_chat = LLMProvider.stream_chat  # type: ignore[assignment]


class TestTheStreamingSeam:
    def test_a_provider_that_cannot_stream_emits_no_deltas_at_all(self) -> None:
        """Deliberately not "the whole answer as one delta". That would make
        "the model wrote this at once" indistinguishable from "this provider
        does not stream", and the UI would render the text twice."""
        provider = _SilentProvider("the plate is 8 mm thick")

        events = list(
            provider.stream_chat(system="s", messages=[], tools=[], max_tokens=10)
        )

        assert len(events) == 1
        assert isinstance(events[0], Finished)
        assert events[0].turn.text == "the plate is 8 mm thick"

    def test_a_streaming_provider_ends_with_exactly_one_finished(self) -> None:
        provider = _StreamingProvider("two words")

        events = list(
            provider.stream_chat(system="s", messages=[], tools=[], max_tokens=10)
        )

        assert sum(1 for event in events if isinstance(event, Finished)) == 1
        assert isinstance(events[-1], Finished)

    def test_the_finished_turn_is_the_answer_not_the_joined_deltas(self) -> None:
        """The contract callers must not break. Reassembling the deltas builds
        a second copy of the answer that drifts — most obviously on a provider
        that streams a corrected token."""
        provider = _StreamingProvider("a b c")

        events = list(
            provider.stream_chat(system="s", messages=[], tools=[], max_tokens=10)
        )
        final = events[-1]
        assert isinstance(final, Finished)

        assert final.turn.text == "a b c"
