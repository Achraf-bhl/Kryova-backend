"""Conversation management endpoints.

History used to be write-only: a transcript went in and only a client that had
kept the id could ever read it back. These cover the surface a sidebar needs --
list, rename, delete -- and the one thing rehydration has to get right, which is
that a reloaded page shows the agent's *work*, not only its prose.
"""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import Conversation, ConversationMessage, MessageRole, User
from tests.typing import AuthenticatedTestClient


@pytest.fixture
def account(db_session: Session, auth_client: AuthenticatedTestClient) -> User:
    from app.models import User as UserModel

    user_id = auth_client.get("/api/v1/auth/me").json()["id"]
    user = db_session.get(UserModel, user_id)
    assert user is not None
    return user


def _conversation(
    db_session: Session, owner: User, title: str = "Bracket fillet stress"
) -> Conversation:
    row = Conversation(owner_id=owner.id, title=title)
    db_session.add(row)
    db_session.flush()
    return row


def _turn(
    db_session: Session, conversation: Conversation, sequence: int, **fields: Any
) -> ConversationMessage:
    message = ConversationMessage(conversation_id=conversation.id, sequence=sequence, **fields)
    db_session.add(message)
    db_session.flush()
    return message


class TestListing:
    def test_requires_authentication(self, client: Any) -> None:
        assert client.get("/api/v1/ai/conversations").status_code == 401

    def test_lists_only_the_callers_conversations(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        from app.core.security import hash_password

        mine = _conversation(db_session, account, "Mine")
        stranger = User(
            email="stranger@kryova.dev",
            hashed_password=hash_password("a-long-enough-password"),
        )
        db_session.add(stranger)
        db_session.flush()
        _conversation(db_session, stranger, "Theirs")

        body = auth_client.get("/api/v1/ai/conversations").json()
        assert body["total"] == 1
        assert [item["conversation_id"] for item in body["items"]] == [mine.id]

    def test_is_paginated_newest_activity_first(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        for index in range(5):
            _conversation(db_session, account, f"Session {index}")

        page = auth_client.get(
            "/api/v1/ai/conversations", params={"page": 1, "page_size": 2}
        ).json()
        assert page["total"] == 5
        assert page["page_size"] == 2
        assert len(page["items"]) == 2

        second = auth_client.get(
            "/api/v1/ai/conversations", params={"page": 2, "page_size": 2}
        ).json()
        assert len(second["items"]) == 2
        first_ids = {item["conversation_id"] for item in page["items"]}
        assert first_ids.isdisjoint({item["conversation_id"] for item in second["items"]})

    def test_carries_what_a_sidebar_row_needs(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        conversation.prompt_tokens = 900
        conversation.completion_tokens = 120
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="hello")
        _turn(db_session, conversation, 1, role=MessageRole.ASSISTANT, content="hi")

        item = auth_client.get("/api/v1/ai/conversations").json()["items"][0]
        assert item["title"] == "Bracket fillet stress"
        assert item["message_count"] == 2
        assert item["has_catia_document"] is False
        assert item["prompt_tokens"] == 900
        assert item["completion_tokens"] == 120
        assert item["updated_at"]


class TestReading:
    def test_another_users_conversation_is_not_found(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        """404, never 403 -- a 403 would confirm the id exists."""
        from app.core.security import hash_password

        stranger = User(
            email="nosy@kryova.dev", hashed_password=hash_password("a-long-enough-password")
        )
        db_session.add(stranger)
        db_session.flush()
        theirs = _conversation(db_session, stranger, "Theirs")

        response = auth_client.get(f"/api/v1/ai/conversations/{theirs.id}")
        assert response.status_code == 404

    def test_a_tool_step_rehydrates_with_its_arguments_and_result(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        """Without these the reloaded page shows prose and loses the agent's work."""
        from app.ai.agent import _serialise

        conversation = _conversation(db_session, account)
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="what runs?")
        _turn(
            db_session,
            conversation,
            1,
            role=MessageRole.ASSISTANT,
            content="Let me check.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "list_simulations",
                        "arguments": {"limit": 5},
                    },
                }
            ],
        )
        _turn(
            db_session,
            conversation,
            2,
            role=MessageRole.TOOL,
            tool_call_id="call_1",
            tool_name="list_simulations",
            content=_serialise({"simulations": [{"id": "s1"}, {"id": "s2"}]}),
            duration_ms=42,
        )

        body = auth_client.get(f"/api/v1/ai/conversations/{conversation.id}").json()
        step = body["messages"][2]
        assert step["tool_name"] == "list_simulations"
        assert step["arguments"] == {"limit": 5}
        assert step["result"] == {"simulations": [{"id": "s1"}, {"id": "s2"}]}
        assert step["duration_ms"] == 42
        # The same label and summary the live SSE stream produced.
        assert step["label"] == "Reviewing previous runs"
        assert step["summary"] == "2 previous run(s)"
        assert step["is_error"] is False

    def test_a_failed_step_rehydrates_as_a_failure(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        from app.ai.agent import _serialise

        conversation = _conversation(db_session, account)
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="show run x")
        _turn(
            db_session,
            conversation,
            1,
            role=MessageRole.TOOL,
            tool_call_id="call_1",
            tool_name="get_simulation",
            content=_serialise({"error": "No simulation with id 'x'."}),
            is_error=True,
        )

        body = auth_client.get(f"/api/v1/ai/conversations/{conversation.id}").json()
        step = body["messages"][1]
        assert step["is_error"] is True
        assert "No simulation" in step["summary"]

    def test_reports_token_spend_and_catia_binding(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        conversation.prompt_tokens = 10
        conversation.completion_tokens = 4
        db_session.flush()

        body = auth_client.get(f"/api/v1/ai/conversations/{conversation.id}").json()
        assert body["prompt_tokens"] == 10
        assert body["completion_tokens"] == 4
        assert body["has_catia_document"] is False
        assert body["catia_document"] is None


class TestRoleRoundTrip:
    """A regression test for a bug that only appeared on the *second* request.

    `role` was a bare `String(16)`, so a message written in this session held a
    `MessageRole` while one loaded from the database held a plain `str`. Every
    `role is MessageRole.USER` check in the transcript replay therefore changed
    answer depending on where the object came from -- correct in a
    write-then-read test, and wrong on a fresh request, where the whole
    conversation loads from a SELECT and every message falls through to the
    tool branch.
    """

    def test_a_reloaded_message_still_carries_its_enum(
        self, db_session: Session, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="hello")
        _turn(db_session, conversation, 1, role=MessageRole.ASSISTANT, content="hi")
        db_session.commit()
        db_session.expire_all()

        reloaded = db_session.get(Conversation, conversation.id)
        assert reloaded is not None
        roles = [message.role for message in reloaded.messages]
        assert roles == [MessageRole.USER, MessageRole.ASSISTANT]
        # The identity check is the one that silently broke; `==` never did.
        assert roles[0] is MessageRole.USER
        assert roles[1] is MessageRole.ASSISTANT

    def test_a_reloaded_transcript_replays_correctly(
        self, db_session: Session, account: User
    ) -> None:
        from app.ai.context import build_messages

        conversation = _conversation(db_session, account)
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="hello")
        _turn(db_session, conversation, 1, role=MessageRole.ASSISTANT, content="hi")
        db_session.commit()
        db_session.expire_all()

        reloaded = db_session.get(Conversation, conversation.id)
        assert reloaded is not None
        replayed = build_messages(db_session, account, reloaded)
        assert [m["role"] for m in replayed] == ["user", "user", "assistant"]


class TestRename:
    def test_renames(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        response = auth_client.patch(
            f"/api/v1/ai/conversations/{conversation.id}",
            json={"title": "Motor mount mass reduction"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Motor mount mass reduction"
        assert conversation.title == "Motor mount mass reduction"

    def test_an_empty_title_is_refused(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        response = auth_client.patch(
            f"/api/v1/ai/conversations/{conversation.id}", json={"title": ""}
        )
        assert response.status_code == 422

    def test_another_users_conversation_cannot_be_renamed(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        from app.core.security import hash_password

        stranger = User(
            email="rename@kryova.dev",
            hashed_password=hash_password("a-long-enough-password"),
        )
        db_session.add(stranger)
        db_session.flush()
        theirs = _conversation(db_session, stranger)

        response = auth_client.patch(
            f"/api/v1/ai/conversations/{theirs.id}", json={"title": "Mine now"}
        )
        assert response.status_code == 404


class TestDelete:
    def test_deletes_the_conversation_and_its_transcript(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        conversation = _conversation(db_session, account)
        _turn(db_session, conversation, 0, role=MessageRole.USER, content="hello")
        conversation_id = conversation.id

        assert auth_client.delete(f"/api/v1/ai/conversations/{conversation_id}").status_code == 204
        assert db_session.get(Conversation, conversation_id) is None

    def test_the_spend_ledger_survives_the_delete(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        """Deleting a chat must not erase the record of what it cost."""
        from app.ai import usage as token_usage
        from app.ai.provider import TokenUsage
        from app.models import AITokenUsage

        conversation = _conversation(db_session, account)
        token_usage.record(
            db_session,
            user=account,
            usage=TokenUsage(500, 100),
            purpose=token_usage.PURPOSE_CHAT,
            provider="stub",
            model="stub-1",
            conversation=conversation,
        )
        db_session.commit()

        assert auth_client.delete(f"/api/v1/ai/conversations/{conversation.id}").status_code == 204
        rows = db_session.query(AITokenUsage).filter_by(user_id=account.id).all()
        assert len(rows) == 1
        assert rows[0].conversation_id is None
        assert rows[0].prompt_tokens == 500

    def test_another_users_conversation_cannot_be_deleted(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        from app.core.security import hash_password

        stranger = User(
            email="del@kryova.dev", hashed_password=hash_password("a-long-enough-password")
        )
        db_session.add(stranger)
        db_session.flush()
        theirs = _conversation(db_session, stranger)

        assert auth_client.delete(f"/api/v1/ai/conversations/{theirs.id}").status_code == 404
        assert db_session.get(Conversation, theirs.id) is not None


class TestTitleGeneration:
    def test_a_good_title_replaces_the_truncated_prompt(self) -> None:
        from app.ai.provider import AssistantTurn, LLMProvider, TokenUsage
        from app.ai.service import generate_title

        class Titler(LLMProvider):
            name = "titler"
            model = "titler-1"

            def health(self) -> None:
                return None

            def complete(self, **_: Any) -> Any:
                raise NotImplementedError

            def chat(self, **_: Any) -> AssistantTurn:
                return AssistantTurn(text='  "Bracket fillet stress"\n', usage=TokenUsage(40, 6))

        title, usage = generate_title(
            Titler(), user_message="can you look at my bracket", assistant_reply="sure"
        )
        # Quotes and whitespace stripped: a leading quote in a sidebar reads as a bug.
        assert title == "Bracket fillet stress"
        assert usage.total_tokens == 46

    def test_a_provider_failure_falls_back_to_the_truncated_prompt(self) -> None:
        """A sidebar label is never worth failing a turn over."""
        from app.ai.provider import LLMProvider, LLMUnavailable
        from app.ai.service import generate_title

        class Broken(LLMProvider):
            name = "broken"
            model = "broken-1"

            def health(self) -> None:
                return None

            def complete(self, **_: Any) -> Any:
                raise NotImplementedError

            def chat(self, **_: Any) -> Any:
                raise LLMUnavailable("nothing listening")

        title, usage = generate_title(
            Broken(),
            user_message="I need to check whether this bracket yields under a 500 N tip load",
            assistant_reply="",
        )
        assert title.startswith("I need to check whether this bracket")
        assert len(title) <= 60
        assert usage.total_tokens == 0

    def test_an_empty_answer_falls_back_too(self) -> None:
        from app.ai.provider import AssistantTurn, LLMProvider
        from app.ai.service import generate_title

        class Mute(LLMProvider):
            name = "mute"
            model = "mute-1"

            def health(self) -> None:
                return None

            def complete(self, **_: Any) -> Any:
                raise NotImplementedError

            def chat(self, **_: Any) -> AssistantTurn:
                return AssistantTurn(text="   ")

        title, _ = generate_title(Mute(), user_message="bracket", assistant_reply="ok")
        assert title == "bracket"
