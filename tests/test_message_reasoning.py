"""A reasoning model's chain of thought is kept with its message, and never shown.

DeepSeek rejects a tool-calling request whose earlier assistant turns lack their
`reasoning_content` (400), and the transcript sent on every agent step is rebuilt
from `conversation_messages`. So the reasoning has to be stored when the turn is
written and replayed when the next step builds its transcript -- or the second
step of every agent run fails while the first works.

The stored text is the provider's, not the user's: it is on no API response.
"""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.agent import run_agent
from app.ai.context import _replay
from app.ai.provider import AssistantTurn, LLMProvider, TokenUsage, ToolCall
from app.ai.tools import ToolBox
from app.api.routes.ai import ConversationMessageRead
from app.core.security import hash_password
from app.models import Conversation, ConversationMessage, MessageRole, Project, User


class _Reasoner(LLMProvider):
    name = "reasoner"
    model = "reasoner-1"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.transcripts: list[list[dict[str, Any]]] = []

    def health(self) -> None:
        return None

    def complete(self, **_: Any) -> Any:  # pragma: no cover - not used by the loop
        raise NotImplementedError

    def chat(self, *, system: str, messages: list[dict[str, Any]], tools: list[Any], max_tokens: int) -> AssistantTurn:
        self.transcripts.append(messages)
        return self._turns.pop(0) if self._turns else AssistantTurn(text="Done.")


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="reason@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def project(db_session: Session, user: User) -> Project:
    row = Project(name="Bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def conversation(db_session: Session, user: User, project: Project) -> Conversation:
    row = Conversation(owner_id=user.id, project_id=project.id, title="t")
    db_session.add(row)
    db_session.flush()
    return row


def _run(db: Session, user: User, project: Project, conversation: Conversation, provider: _Reasoner) -> None:
    run_agent(
        db=db,
        provider=provider,
        conversation=conversation,
        toolbox=ToolBox(db=db, user=user, project_id=project.id),
        user_message="rename it",
    )


def _assistant_rows(db: Session, conversation: Conversation) -> list[ConversationMessage]:
    return list(
        db.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == MessageRole.ASSISTANT,
            )
            .order_by(ConversationMessage.created_at)
        )
    )


class TestTheAgentKeepsWhatTheModelThought:
    def test_a_tool_calling_turn_and_a_final_answer_both_store_their_reasoning(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = _Reasoner(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="update_project", arguments={"name": "Bracket 2"})],
                    reasoning="Rename first.",
                    usage=TokenUsage(1, 1),
                ),
                AssistantTurn(text="Renamed.", reasoning="It worked.", usage=TokenUsage(1, 1)),
            ]
        )
        _run(db_session, user, project, conversation, provider)
        rows = _assistant_rows(db_session, conversation)
        assert [row.reasoning for row in rows] == ["Rename first.", "It worked."]

    def test_a_provider_with_no_reasoning_stores_null_not_an_empty_string(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = _Reasoner([AssistantTurn(text="Hello.")])
        _run(db_session, user, project, conversation, provider)
        assert [row.reasoning for row in _assistant_rows(db_session, conversation)] == [None]

    def test_the_second_step_is_sent_the_first_steps_reasoning(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The 400 this column exists to avoid."""
        provider = _Reasoner(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="update_project", arguments={"name": "B2"})],
                    reasoning="Rename first.",
                ),
                AssistantTurn(text="Renamed."),
            ]
        )
        _run(db_session, user, project, conversation, provider)
        second = provider.transcripts[1]
        assistants = [m for m in second if m["role"] == "assistant"]
        assert assistants[0]["reasoning"] == "Rename first."


class TestReplay:
    """`_replay` is what rebuilds the transcript from the stored rows on every step."""

    @staticmethod
    def _assistant(conversation: Conversation, **fields: Any) -> ConversationMessage:
        return ConversationMessage(
            conversation_id=conversation.id,
            sequence=1,
            role=MessageRole.ASSISTANT,
            content="hello",
            **fields,
        )

    def test_reasoning_is_replayed_when_it_was_kept(self, conversation: Conversation) -> None:
        row = self._assistant(conversation, reasoning="Greet them.")
        assert _replay(row)["reasoning"] == "Greet them."

    def test_a_message_with_none_has_no_reasoning_key_at_all(
        self, conversation: Conversation
    ) -> None:
        """Absent and empty are different facts to a provider that echoes it."""
        assert "reasoning" not in _replay(self._assistant(conversation))

    def test_an_empty_string_is_replayed_as_empty(self, conversation: Conversation) -> None:
        assert _replay(self._assistant(conversation, reasoning=""))["reasoning"] == ""

    def test_a_user_message_never_carries_it(self, conversation: Conversation) -> None:
        row = ConversationMessage(
            conversation_id=conversation.id, sequence=1, role=MessageRole.USER, content="hi"
        )
        assert "reasoning" not in _replay(row)

    def test_it_survives_a_round_trip_through_the_database(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        db_session.add(self._assistant(conversation, reasoning="Kept."))
        db_session.flush()
        db_session.expire_all()
        row = db_session.scalars(
            select(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
        ).one()
        assert _replay(row)["reasoning"] == "Kept."


class TestItIsNeverShown:
    def test_the_message_schema_has_no_reasoning_field(self) -> None:
        """The route builds each message field by field, so a field absent here is
        a field no response can carry."""
        assert "reasoning" not in ConversationMessageRead.model_fields
