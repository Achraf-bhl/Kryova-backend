"""Branching a conversation, and rewinding its newest turn (ROAD_TO_10 2.5).

The two things that cannot be done honestly from outside a seat are rolling a CATIA document
back and copying one, so what is worth testing is that nothing here *claims* to:

* a branch carries the messages, the design as it stood **at that answer**, and a summary or
  plan only where they are still true -- and never a document, and says so;
* a rewind refuses a turn that changed anything, naming what ran, and deletes nothing when it
  refuses; and the one thing a rewind must not leave behind is a Continue for a turn whose
  answer it just deleted.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import branching, continuation, prompts, turn_events
from app.ai.state import build_state_block
from app.ai.taskgraph import Task, TaskGraph, TaskState
from app.ai.tools import ToolBox
from app.core import designs
from app.core.security import hash_password
from app.models import (
    ApprovalGate,
    Conversation,
    ConversationMessage,
    GateState,
    MessageRole,
    Organisation,
    TurnMetric,
    User,
)
from app.models.base import utcnow
from app.models.catia import CatiaDocument
from app.models.conversation import TurnEvent
from app.models.design import DesignRevision
from tests.test_design_compile import bracket
from tests.typing import AuthenticatedTestClient


@pytest.fixture
def account(db_session: Session, auth_client: AuthenticatedTestClient) -> User:
    user = db_session.get(User, auth_client.get("/api/v1/auth/me").json()["id"])
    assert user is not None
    return user


T0 = utcnow() - timedelta(hours=2)


def _at(minutes: int):  # type: ignore[no-untyped-def]
    return T0 + timedelta(minutes=minutes)


def _say(
    db: Session,
    row: Conversation,
    sequence: int,
    role: MessageRole,
    content: str | None,
    *,
    minutes: int | None = None,
    **fields: Any,
) -> ConversationMessage:
    message = ConversationMessage(
        conversation_id=row.id,
        sequence=sequence,
        role=role,
        content=content,
        created_at=_at(minutes if minutes is not None else sequence),
        **fields,
    )
    db.add(message)
    db.flush()
    return message


def _built(db: Session, owner: User, title: str = "Plate") -> Conversation:
    """Two exchanges: a build with a tool call (0-3), then a plain follow-up (4-5)."""
    row = Conversation(owner_id=owner.id, title=title)
    db.add(row)
    db.flush()
    _say(db, row, 0, MessageRole.USER, "build a 60 x 40 plate")
    _say(
        db, row, 1, MessageRole.ASSISTANT, None,
        tool_calls=[{"id": "c1", "name": "catia_new_part", "arguments": {"name": "Plate"}}],
    )
    _say(
        db, row, 2, MessageRole.TOOL, '{"ok": true}', tool_call_id="c1", tool_name="catia_new_part",
        duration_ms=12,
    )
    _say(db, row, 3, MessageRole.ASSISTANT, "The plate is built.")
    _say(db, row, 4, MessageRole.USER, "how heavy is it?")
    _say(db, row, 5, MessageRole.ASSISTANT, "About 0.05 kg.")
    db.refresh(row)
    return row


def _read_only(db: Session, owner: User) -> Conversation:
    row = Conversation(owner_id=owner.id, title="Look")
    db.add(row)
    db.flush()
    _say(db, row, 0, MessageRole.USER, "list my projects")
    _say(
        db, row, 1, MessageRole.ASSISTANT, None,
        tool_calls=[{"id": "c1", "name": "list_projects", "arguments": {}}],
    )
    _say(db, row, 2, MessageRole.TOOL, "[]", tool_call_id="c1", tool_name="list_projects")
    _say(db, row, 3, MessageRole.ASSISTANT, "You have none.")
    db.refresh(row)
    return row


def _toolbox(db: Session, owner: User, row: Conversation) -> ToolBox:
    return ToolBox(db=db, user=owner, conversation=row)


def _branch(client: AuthenticatedTestClient, source_id: str, **body: Any) -> Any:
    return client.post(f"/api/v1/ai/conversations/{source_id}/branch", json=body)


def _messages(db: Session, conversation_id: str) -> list[ConversationMessage]:
    return list(
        db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.sequence)
        )
    )


# -- branch -----------------------------------------------------------------


class TestABranchCopiesTheTranscriptUpToAnAnswer:
    def test_by_default_it_takes_everything_up_to_the_newest_answer(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        response = _branch(auth_client, source.id)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["from_sequence"] == 5 and body["copied_messages"] == 6
        assert body["conversation_id"] != source.id
        assert body["title"] == "Plate (branch)"

    def test_it_can_start_at_an_earlier_answer(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        body = _branch(auth_client, source.id, from_sequence=3).json()

        assert body["copied_messages"] == 4
        copied = _messages(db_session, body["conversation_id"])
        assert [m.sequence for m in copied] == [0, 1, 2, 3]
        assert copied[-1].content == "The plate is built."

    def test_the_copy_is_faithful_and_the_original_is_untouched(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        body = _branch(auth_client, source.id).json()

        copied = _messages(db_session, body["conversation_id"])
        original = _messages(db_session, source.id)
        assert len(original) == 6
        for mine, theirs in zip(copied, original, strict=True):
            assert (mine.sequence, mine.role, mine.content) == (
                theirs.sequence, theirs.role, theirs.content,
            )
            assert mine.tool_calls == theirs.tool_calls
            assert (mine.tool_call_id, mine.tool_name, mine.duration_ms) == (
                theirs.tool_call_id, theirs.tool_name, theirs.duration_ms,
            )
            # The history's own clock, not the day it was copied.
            assert mine.created_at == theirs.created_at
            assert mine.id != theirs.id

    def test_editing_the_copy_does_not_edit_the_original(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        body = _branch(auth_client, source.id).json()
        copied = _messages(db_session, body["conversation_id"])
        copied[1].tool_calls[0]["name"] = "something_else"  # type: ignore[index]
        db_session.flush()
        db_session.expire_all()

        assert _messages(db_session, source.id)[1].tool_calls[0]["name"] == "catia_new_part"  # type: ignore[index]

    @pytest.mark.parametrize(
        ("sequence", "why"),
        [(0, "user"), (1, "calls"), (2, "tool"), (99, "missing")],
    )
    def test_it_will_not_start_anywhere_but_an_answer(
        self,
        db_session: Session,
        auth_client: AuthenticatedTestClient,
        account: User,
        sequence: int,
        why: str,
    ) -> None:
        source = _built(db_session, account)

        response = _branch(auth_client, source.id, from_sequence=sequence)

        assert response.status_code == 422, (why, response.text)
        count = db_session.scalar(select(func.count()).select_from(Conversation))
        assert count == 1  # nothing was created

    def test_an_answer_that_also_asks_for_a_tool_is_not_an_answer_yet(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        # "Let me build that" plus the call: the result comes after it, so a branch cut here
        # would hand the model a call with no result.
        row = Conversation(owner_id=account.id, title="Chatty")
        db_session.add(row)
        db_session.flush()
        _say(db_session, row, 0, MessageRole.USER, "build it")
        _say(db_session, row, 1, MessageRole.ASSISTANT, "Done, thanks.")
        _say(db_session, row, 2, MessageRole.USER, "now a hole")
        _say(
            db_session, row, 3, MessageRole.ASSISTANT, "Let me cut that.",
            tool_calls=[{"id": "c1", "name": "catia_pocket", "arguments": {}}],
        )

        by_default = _branch(auth_client, row.id).json()
        explicit = _branch(auth_client, row.id, from_sequence=3)

        assert by_default["from_sequence"] == 1  # skipped the message with a call in it
        assert explicit.status_code == 422

    def test_the_transcript_marks_where_a_branch_may_start(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        messages = auth_client.get(f"/api/v1/ai/conversations/{source.id}").json()["messages"]

        # The UI offers Branch exactly where the server would accept one.
        assert {m["sequence"] for m in messages if m["branchable"]} == {3, 5}
        for sequence in (0, 1, 2, 4):
            assert _branch(auth_client, source.id, from_sequence=sequence).status_code == 422

    def test_a_conversation_with_no_answer_yet_cannot_be_branched(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = Conversation(owner_id=account.id, title="Empty")
        db_session.add(row)
        db_session.flush()
        _say(db_session, row, 0, MessageRole.USER, "hello")

        response = _branch(auth_client, row.id)

        assert response.status_code == 409
        assert "no answer" in response.text

    def test_someone_elses_conversation_is_a_404(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        stranger = User(email="b@kryova.dev", hashed_password=hash_password("a-long-enough-pw"))
        db_session.add(stranger)
        db_session.flush()
        theirs = _built(db_session, stranger)

        assert _branch(auth_client, theirs.id).status_code == 404

    def test_the_branch_says_where_it_came_from(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account, "Plate")
        body = _branch(auth_client, source.id, from_sequence=3, title="Thicker").json()

        detail = auth_client.get(f"/api/v1/ai/conversations/{body['conversation_id']}").json()

        assert detail["title"] == "Thicker"
        assert detail["branched_from"] == {
            "conversation_id": source.id, "title": "Plate", "at_sequence": 3,
        }
        listing = auth_client.get("/api/v1/ai/conversations").json()["items"]
        assert {item["conversation_id"]: item["branched_from_id"] for item in listing}[
            body["conversation_id"]
        ] == source.id

    def test_a_branch_outlives_its_source(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        body = _branch(auth_client, source.id).json()

        assert auth_client.delete(f"/api/v1/ai/conversations/{source.id}").status_code == 204

        detail = auth_client.get(f"/api/v1/ai/conversations/{body['conversation_id']}")
        assert detail.status_code == 200
        assert detail.json()["branched_from"] is None
        assert len(detail.json()["messages"]) == 6

    def test_a_branch_starts_with_no_spend_of_its_own(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        source.prompt_tokens, source.completion_tokens = 9_000, 800
        db_session.flush()

        body = _branch(auth_client, source.id).json()

        branch = db_session.get(Conversation, body["conversation_id"])
        assert branch is not None
        assert (branch.prompt_tokens, branch.completion_tokens) == (0, 0)


class TestWhatIsCopiedOnlyWhereItIsStillTrue:
    def test_the_summary_is_kept_when_everything_it_covers_was_copied(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        source.summary, source.summary_facts = "the note", "the facts"
        source.summary_through_sequence = 4
        db_session.flush()

        body = _branch(auth_client, source.id).json()

        assert body["summary_kept"] is True
        branch = db_session.get(Conversation, body["conversation_id"])
        assert branch is not None
        assert (branch.summary, branch.summary_facts, branch.summary_through_sequence) == (
            "the note", "the facts", 4,
        )

    def test_the_summary_is_dropped_when_it_covers_messages_after_the_point(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        source.summary, source.summary_facts = "the note", "the facts"
        source.summary_through_sequence = 5
        db_session.flush()

        body = _branch(auth_client, source.id, from_sequence=3).json()

        assert body["summary_kept"] is False
        branch = db_session.get(Conversation, body["conversation_id"])
        assert branch is not None
        assert (branch.summary, branch.summary_facts, branch.summary_through_sequence) == (
            None, None, 0,
        )
        assert any("summary was not copied" in note for note in body["notes"])

    def test_the_plan_is_copied_for_a_whole_copy_and_not_for_an_older_point(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        source.task_graph = TaskGraph.of(
            [Task("t1", "Sketch", state=TaskState.DONE), Task("t2", "Holes")]
        ).to_dict()
        db_session.flush()

        whole = _branch(auth_client, source.id).json()
        older = _branch(auth_client, source.id, from_sequence=3).json()

        assert whole["plan_copied"] is True and older["plan_copied"] is False
        copy = db_session.get(Conversation, whole["conversation_id"])
        assert copy is not None and copy.task_graph == source.task_graph
        other = db_session.get(Conversation, older["conversation_id"])
        assert other is not None and other.task_graph is None
        assert any("plan was not copied" in note for note in older["notes"])


def _thick_of(document: Any) -> float:
    return float(designs.spec_of(document).parameters.resolve().values["thick_mm"].value)


class TestTheDesignIsReadAsOfTheAnswer:
    def _designed(self, db: Session, owner: User) -> Conversation:
        """Revision 1 (thick 8) before the first answer; revision 2 (thick 10) after it."""
        source = _built(db, owner)
        document = designs.save(db, source, bracket()).document
        first = db.scalar(select(DesignRevision).where(DesignRevision.design_id == document.id))
        assert first is not None
        first.created_at = _at(2)  # before message 3
        designs.set_parameter(db, document, "thick_mm", 10.0)
        second = db.scalar(
            select(DesignRevision).where(
                DesignRevision.design_id == document.id, DesignRevision.revision_number == 2
            )
        )
        assert second is not None
        second.created_at = _at(4)  # after message 3, before message 5
        db.flush()
        return source

    def _thick(self, db: Session, conversation_id: str) -> float:
        branch = db.get(Conversation, conversation_id)
        assert branch is not None
        document = designs.load(db, branch)
        assert document is not None
        return _thick_of(document)

    def test_an_earlier_answer_gets_the_design_that_stood_then(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = self._designed(db_session, account)

        body = _branch(auth_client, source.id, from_sequence=3).json()

        assert body["design_revision"] == 1
        assert self._thick(db_session, body["conversation_id"]) == 8.0

    def test_the_newest_answer_gets_the_design_as_it_is(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = self._designed(db_session, account)

        body = _branch(auth_client, source.id).json()

        assert body["design_revision"] == 2
        assert self._thick(db_session, body["conversation_id"]) == 10.0

    def test_the_copy_is_the_branchs_own_first_revision_not_a_shared_one(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = self._designed(db_session, account)
        body = _branch(auth_client, source.id).json()
        branch = db_session.get(Conversation, body["conversation_id"])
        assert branch is not None

        copied = designs.load(db_session, branch)
        original = designs.load(db_session, source)

        assert copied is not None and original is not None and copied.id != original.id
        assert copied.revision_number == 1 and original.revision_number == 2
        # Editing the branch's design leaves the original's alone.
        designs.set_parameter(db_session, copied, "thick_mm", 14.0)
        assert _thick_of(original) == 10.0

    def test_a_branch_of_a_branch_finds_the_same_design(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        # The copy keeps the original revision's timestamp, so a second-generation branch's
        # "the design in force at this answer" is not decided by the day of the first copy.
        source = self._designed(db_session, account)
        first = _branch(auth_client, source.id, from_sequence=3).json()

        second = _branch(auth_client, first["conversation_id"]).json()

        assert second["design_revision"] == 1
        assert self._thick(db_session, second["conversation_id"]) == 8.0

    def test_a_conversation_with_no_design_says_nothing_about_one(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        body = _branch(auth_client, source.id).json()

        assert body["design_revision"] is None
        assert not any("recorded design" in note for note in body["notes"])

    def test_a_design_this_build_cannot_read_does_not_stop_the_branch(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = self._designed(db_session, account)
        for revision in db_session.scalars(select(DesignRevision)):
            revision.document = {"format_version": 9_999}
        db_session.flush()

        response = _branch(auth_client, source.id)

        assert response.status_code == 201
        assert response.json()["design_revision"] is None
        assert any("could not be copied" in note for note in response.json()["notes"])
        assert response.json()["copied_messages"] == 6


class TestTheDocumentIsNeverCopiedAndTheModelIsToldSo:
    def test_the_response_says_so_every_time(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)

        notes = _branch(auth_client, source.id).json()["notes"]

        assert any("CATIA document was not copied" in note for note in notes)

    def test_the_branch_owns_no_document_even_when_the_source_does(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        db_session.add(CatiaDocument(conversation_id=source.id, doc_name="Plate"))
        db_session.flush()

        body = _branch(auth_client, source.id).json()

        owned = db_session.scalar(
            select(func.count())
            .select_from(CatiaDocument)
            .where(CatiaDocument.conversation_id == body["conversation_id"])
        )
        assert owned == 0

    def test_a_branch_is_told_in_the_state_block_until_it_has_a_document(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        source = _built(db_session, account)
        body = _branch(auth_client, source.id).json()
        branch = db_session.get(Conversation, body["conversation_id"])
        assert branch is not None

        before = build_state_block(db_session, account, branch)
        db_session.add(CatiaDocument(conversation_id=branch.id, doc_name="Rebuilt"))
        db_session.flush()
        after = build_state_block(db_session, account, branch)

        assert "branched: this conversation was branched from another one at message 5" in before
        assert "does not have" in before
        assert "branched:" not in after

    def test_an_ordinary_conversation_gets_no_such_line(
        self, db_session: Session, account: User
    ) -> None:
        assert "branched:" not in build_state_block(db_session, account, _built(db_session, account))


# -- rewind -----------------------------------------------------------------


class TestRewindDeletesTheNewestTurnAndHandsTheTextBack:
    def test_a_read_only_turn_is_removed_and_the_text_returned(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _read_only(db_session, account)

        response = auth_client.post(f"/api/v1/ai/conversations/{row.id}/rewind")

        assert response.status_code == 200, response.text
        assert response.json() == {"message": "list my projects", "removed_messages": 4}
        assert _messages(db_session, row.id) == []

    def test_only_the_newest_exchange_goes(
        self, db_session: Session, account: User
    ) -> None:
        row = _built(db_session, account)
        # The second exchange (4-5) read nothing and changed nothing.
        outcome = branching.rewind(db_session, row, _toolbox(db_session, account, row))

        assert outcome.message == "how heavy is it?" and outcome.removed_messages == 2
        assert [m.sequence for m in _messages(db_session, row.id)] == [0, 1, 2, 3]

    def test_the_servers_own_notes_are_not_the_message_to_retry(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        _say(db_session, row, 4, MessageRole.USER, prompts.CONTROL_NOTE + "Stop reading in circles.")
        db_session.refresh(row)

        outcome = branching.rewind(db_session, row, _toolbox(db_session, account, row))

        assert outcome.message == "list my projects" and outcome.removed_messages == 5

    def test_a_conversation_with_nothing_of_the_users_is_refused(
        self, db_session: Session, account: User
    ) -> None:
        row = Conversation(owner_id=account.id, title="Empty")
        db_session.add(row)
        db_session.flush()

        with pytest.raises(branching.Refused, match="no message of yours"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

    def test_someone_elses_conversation_is_a_404(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        stranger = User(email="c@kryova.dev", hashed_password=hash_password("a-long-enough-pw"))
        db_session.add(stranger)
        db_session.flush()
        theirs = _read_only(db_session, stranger)

        assert auth_client.post(f"/api/v1/ai/conversations/{theirs.id}/rewind").status_code == 404
        assert len(_messages(db_session, theirs.id)) == 4

    def test_the_buffer_of_the_deleted_turn_is_cleared(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        turn_events.record(db_session, row.id, "turn-1", {"type": "done"})

        branching.rewind(db_session, row, _toolbox(db_session, account, row))

        assert turn_events.latest_sequence(db_session, row.id) == 0


class TestRewindRefusesWhatItCannotUndo:
    def test_a_turn_that_changed_the_part_is_refused_and_nothing_is_deleted(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _built(db_session, account)
        # Make the *newest* turn the one with the mutating call.
        for message in _messages(db_session, row.id)[4:]:
            db_session.delete(message)
        db_session.flush()
        _say(db_session, row, 4, MessageRole.USER, "add a hole")
        _say(
            db_session, row, 5, MessageRole.ASSISTANT, None,
            tool_calls=[{"id": "c2", "name": "catia_pocket", "arguments": {}}],
        )
        _say(
            db_session, row, 6, MessageRole.TOOL, '{"ok": true}', tool_call_id="c2",
            tool_name="catia_pocket",
        )
        _say(db_session, row, 7, MessageRole.ASSISTANT, "Done.")

        response = auth_client.post(f"/api/v1/ai/conversations/{row.id}/rewind")

        assert response.status_code == 409
        assert "catia_pocket" in response.text and "Branch from the answer before it" in response.text
        assert len(_messages(db_session, row.id)) == 8
        db_session.refresh(row)
        assert row.rewound_at is None

    def test_a_call_that_never_came_back_still_counts(
        self, db_session: Session, account: User
    ) -> None:
        # A crash between the call and its result leaves only the request.
        row = Conversation(owner_id=account.id, title="Crash")
        db_session.add(row)
        db_session.flush()
        _say(db_session, row, 0, MessageRole.USER, "cut a pocket")
        _say(
            db_session, row, 1, MessageRole.ASSISTANT, None,
            tool_calls=[{"id": "c1", "name": "catia_pocket", "arguments": {}}],
        )
        db_session.refresh(row)

        with pytest.raises(branching.Refused, match="catia_pocket"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

    def test_a_tool_nobody_recognises_is_treated_as_changing_things(
        self, db_session: Session, account: User
    ) -> None:
        row = Conversation(owner_id=account.id, title="Old")
        db_session.add(row)
        db_session.flush()
        _say(db_session, row, 0, MessageRole.USER, "do it")
        _say(
            db_session, row, 1, MessageRole.TOOL, "x", tool_call_id="c", tool_name="retired_tool_9"
        )
        db_session.refresh(row)

        with pytest.raises(branching.Refused, match="retired_tool_9"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

    def test_a_sign_off_raised_in_the_turn_is_a_record_not_a_draft(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        organisation = Organisation(name="Gate Co", slug="gate-co-2", is_personal=False)
        db_session.add(organisation)
        db_session.flush()
        db_session.add(
            ApprovalGate(
                organisation_id=organisation.id,
                conversation_id=row.id,
                title="Release",
                question="Release it?",
                state=GateState.PENDING,
                subject_type="plan",
                subject_id="p",
                subject_digest="0" * 64,
                requested_by_id=account.id,
            )
        )
        db_session.flush()

        with pytest.raises(branching.Refused, match="sign-off"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

    def test_a_message_folded_into_the_summary_is_too_far_back(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        row.summary, row.summary_through_sequence = "a note", 3
        db_session.flush()

        with pytest.raises(branching.Refused, match="folded into the summary"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

    def test_a_turn_still_running_is_not_pulled_out_from_under_itself(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        turn_events.record(db_session, row.id, "turn-1", {"type": "tool_start"})

        with pytest.raises(branching.Refused, match="still running"):
            branching.rewind(db_session, row, _toolbox(db_session, account, row))

        assert len(_messages(db_session, row.id)) == 4

    def test_a_finished_turn_is_not_running(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        turn_events.record(db_session, row.id, "turn-1", {"type": "tool_start"})
        turn_events.record(db_session, row.id, "turn-1", {"type": "done"})

        assert branching.rewind(db_session, row, _toolbox(db_session, account, row)).removed_messages == 4

    def test_a_turn_that_has_said_nothing_for_minutes_is_not_running_either(
        self, db_session: Session, account: User
    ) -> None:
        row = _read_only(db_session, account)
        turn_events.record(db_session, row.id, "turn-1", {"type": "tool_start"})
        event = db_session.scalar(select(TurnEvent).where(TurnEvent.conversation_id == row.id))
        assert event is not None
        event.created_at = utcnow() - timedelta(seconds=turn_events.FOLLOW_IDLE_TIMEOUT_S + 30)
        db_session.flush()

        assert not turn_events.in_flight(db_session, row.id)
        assert branching.rewind(db_session, row, _toolbox(db_session, account, row)).removed_messages == 4


class TestARewoundTurnIsNotContinued:
    def _stopped(self, db: Session, owner: User) -> Conversation:
        row = _read_only(db, owner)
        db.add(
            TurnMetric(
                user_id=owner.id, conversation_id=row.id, provider="p", model="m",
                stop_reason="step_budget",
            )
        )
        db.flush()
        db.refresh(row)
        return row

    def test_before_the_rewind_it_could_have_been(self, db_session: Session, account: User) -> None:
        row = self._stopped(db_session, account)
        assert continuation.pending(db_session, row) is not None

    def test_after_it_the_answer_to_continue_is_gone(
        self, db_session: Session, account: User
    ) -> None:
        # Two exchanges; the newest stopped on its budget and is rewound. What is now last
        # in the transcript is the earlier exchange's finished answer.
        row = _built(db_session, account)
        db_session.add(
            TurnMetric(
                user_id=account.id, conversation_id=row.id, provider="p", model="m",
                stop_reason="step_budget",
            )
        )
        db_session.flush()
        assert continuation.pending(db_session, row) is not None

        branching.rewind(db_session, row, _toolbox(db_session, account, row))

        assert continuation.pending(db_session, row) is None

    def test_a_turn_recorded_after_the_rewind_is_continuable_again(
        self, db_session: Session, account: User
    ) -> None:
        row = _built(db_session, account)
        db_session.add(
            TurnMetric(
                user_id=account.id, conversation_id=row.id, provider="p", model="m",
                stop_reason="step_budget",
            )
        )
        db_session.flush()
        branching.rewind(db_session, row, _toolbox(db_session, account, row))
        # The retried message ran, ended on its budget, and was recorded.
        _say(db_session, row, 4, MessageRole.USER, "how heavy is it?", minutes=200)
        _say(db_session, row, 5, MessageRole.ASSISTANT, "Partway.", minutes=201)
        db_session.add(
            TurnMetric(
                user_id=account.id, conversation_id=row.id, provider="p", model="m",
                stop_reason="step_budget", created_at=utcnow() + timedelta(seconds=5),
            )
        )
        db_session.flush()
        db_session.refresh(row)

        assert continuation.pending(db_session, row) is not None
