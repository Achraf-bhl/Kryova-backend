"""What the engineer asked for is held beside the turn, not left in the transcript.

Master plan 16.2's first step, wired 2026-09-06. `app/ai/planning.py` had been
a tested library with no consumer since it was written, and the stated reason
for not wiring it was that half-wiring would add a schema to the payload 16.1
is shrinking. That reason does not apply to the state block: `extract_objectives`
is regex over the user's own words, so this costs no model call and adds no
schema at all.

Two measurements on 2026-09-06 say why it is needed:

* attempt 3 -- six stated requirements, three built, and a closing report of
  success, because nothing in the system was holding the list;
* ladder prompt H4 run 9, that evening, on the real seat. The request states a
  500 N load, a 150 mm reach, mild steel and a safety factor of 2. Twenty
  rounds in, the agent asked the user *"How wide should the vertical flange be?
  How thick should the bracket material be? What wall thickness for mounting?"*
  -- with the system prompt already saying, in as many words, that a
  requirement is not a missing dimension and that asking hands back the
  engineering the user came for.

The prompt says the right thing and is read once, at the top of a window being
trimmed from the front. The state block is rebuilt from the database every
turn and sits beside the user's message. That is the difference this file
pins.

Offline apart from the ordinary database fixtures: no model, no CATIA.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai.state import MAX_OBJECTIVES, _requirement_lines, build_state_block
from app.models import Conversation, ConversationMessage, MessageRole, Project, User

H4 = (
    "I need a bracket that bolts to a wall with two M8 fasteners and carries a "
    "500 N load hanging 150 mm out from the wall, in mild steel, with a safety "
    "factor of at least 2. Design it and tell me what it will actually take."
)


@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="requirements@kryova.dev", hashed_password=hash_password("a-long-enough-password")
    )
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def conversation(db_session: Session, user: User) -> Conversation:
    row = Conversation(owner_id=user.id, title="H4")
    db_session.add(row)
    db_session.flush()
    return row


def _say(db_session: Session, conversation: Conversation, role: MessageRole, text: str) -> None:
    db_session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            sequence=len(conversation.messages) + 1,
            role=role,
            content=text,
        )
    )
    db_session.flush()
    db_session.refresh(conversation)


class TestWhatItHolds:
    def test_the_h4_requirements_are_named(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, H4)
        block = "\n".join(_requirement_lines(conversation))
        assert "500 N" in block
        assert "150 mm" in block
        assert "M8" in block
        assert "safety factor" in block

    def test_it_says_they_are_not_questions_to_ask_back(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """The exact failure this exists for: the agent asked for the width,
        the thickness and the wall thickness of a bracket whose load, reach,
        material and factor of safety it had been given."""
        _say(db_session, conversation, MessageRole.USER, H4)
        block = "\n".join(_requirement_lines(conversation))
        assert "not questions to put back to the user" in block
        assert "derive it" in block

    def test_not_checked_is_never_folded_into_fine(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, H4)
        block = "\n".join(_requirement_lines(conversation))
        assert "none of them measured yet" in block
        assert "Not checked is not the same as fine" in block

    def test_a_request_with_no_requirements_adds_nothing(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Every line has to earn its place in the window on every turn."""
        _say(db_session, conversation, MessageRole.USER, "hello")
        assert _requirement_lines(conversation) == []

    def test_an_empty_conversation_adds_nothing(self, conversation: Conversation) -> None:
        assert _requirement_lines(conversation) == []


class TestItSurvivesTheTurn:
    """The whole point. A requirement stated on turn one has to be present on
    turn four, when the window has trimmed the message that stated it."""

    def test_the_original_request_is_still_there_after_later_turns(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, H4)
        _say(db_session, conversation, MessageRole.ASSISTANT, "I built a block.")
        _say(db_session, conversation, MessageRole.USER, "carry on")
        block = "\n".join(_requirement_lines(conversation))
        assert "500 N" in block

    def test_a_later_requirement_joins_the_first(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, H4)
        _say(db_session, conversation, MessageRole.USER, "Also keep it under 2 kg.")
        block = "\n".join(_requirement_lines(conversation))
        assert "500 N" in block
        assert "2 kg" in block

    def test_the_same_thing_said_twice_is_listed_once(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, H4)
        _say(db_session, conversation, MessageRole.USER, H4)
        lines = [line for line in _requirement_lines(conversation) if line.startswith("  - ")]
        assert len(lines) == len(set(lines))

    def test_an_assistant_turn_states_no_requirements(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Otherwise the model's own restatement of the brief comes back as a
        requirement the user never made, and it accumulates every turn."""
        _say(db_session, conversation, MessageRole.ASSISTANT, "I will make it 500 N rated.")
        assert _requirement_lines(conversation) == []

    def test_a_long_specification_is_capped(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        _say(
            db_session,
            conversation,
            MessageRole.USER,
            ". ".join(f"Requirement {i} is {i * 10} mm long" for i in range(1, 30)),
        )
        lines = [line for line in _requirement_lines(conversation) if line.startswith("  - ")]
        assert len(lines) <= MAX_OBJECTIVES + 1
        assert any("more" in line for line in lines)


class TestItReachesTheBlock:
    def test_the_state_block_carries_it(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        """A helper nothing calls holds nothing. This goes the way the agent
        does, through the function that builds the real block."""
        _say(db_session, conversation, MessageRole.USER, H4)
        block = build_state_block(db_session, user, conversation)
        assert "requirements_stated" in block
        assert "500 N" in block

    def test_it_is_absent_when_there_is_nothing_to_say(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        _say(db_session, conversation, MessageRole.USER, "hello")
        assert "requirements_stated" not in build_state_block(db_session, user, conversation)

    def test_a_project_scoped_conversation_still_gets_it(
        self, db_session: Session, user: User
    ) -> None:
        project = Project(name="Bracket", owner_id=user.id)
        db_session.add(project)
        db_session.flush()
        conversation = Conversation(owner_id=user.id, title="H4", project_id=project.id)
        db_session.add(conversation)
        db_session.flush()
        _say(db_session, conversation, MessageRole.USER, H4)
        assert "500 N" in build_state_block(db_session, user, conversation)
