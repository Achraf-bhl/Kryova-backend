"""What is sent on every model call must be byte-stable, because a hosted model bills it.

DeepSeek (and most hosted APIs) reuse the computed prefix of a prompt that begins
with the same bytes as an earlier request, at a small fraction of the price of a
fresh one. An agent step resends the system prompt, the whole tool registry
(~230 schemas, ~58k tokens with every tool offered) and the transcript, so the
single cheapest saving available is to not change any of it between steps: the
prefix then costs almost nothing after the first call. A varying byte early in
the prompt silently turns every later step back into a full-price one -- nothing
errors, the bill is just several times larger.

Offline: the registry is read, no model is called.
"""

import json
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai import prompts
from app.ai.agent import system_prompt
from app.ai.context import build_messages
from app.ai.tools import ToolBox
from app.core.security import hash_password
from app.models import Conversation, ConversationMessage, MessageRole, Project, User


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="cache@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def project(db_session: Session, user: User) -> Project:
    row = Project(name="Bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


def _schemas(db: Session, user: User, project: Project, *, mutating: bool) -> str:
    toolbox = ToolBox(db=db, user=user, project_id=project.id)
    return json.dumps(toolbox.schemas(include_mutating=mutating), sort_keys=False)


class TestTheToolRegistryIsByteStable:
    @pytest.mark.parametrize("mutating", [False, True])
    def test_two_toolboxes_send_identical_bytes(
        self, db_session: Session, user: User, project: Project, mutating: bool
    ) -> None:
        """Order included: a registry built from a set or a clock-dependent description
        would reorder or reword the prefix from one turn to the next."""
        first = _schemas(db_session, user, project, mutating=mutating)
        second = _schemas(db_session, user, project, mutating=mutating)
        assert first == second

    def test_a_different_project_does_not_change_the_tool_text(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        other = Project(name="Another", owner_id=user.id)
        db_session.add(other)
        db_session.flush()
        assert _schemas(db_session, user, project, mutating=True) == _schemas(
            db_session, user, other, mutating=True
        )

    def test_the_read_only_set_is_a_prefix_free_subset_not_a_reordering(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        """Granting mutation consent changes the tool list once, not its order."""
        read_only = [s["function"]["name"] for s in json.loads(_schemas(db_session, user, project, mutating=False))]
        everything = [s["function"]["name"] for s in json.loads(_schemas(db_session, user, project, mutating=True))]
        assert [name for name in everything if name in set(read_only)] == read_only


class TestTheSystemPromptIsFrozen:
    def test_it_is_one_of_the_four_constants_and_never_assembled(self) -> None:
        assert system_prompt() in {
            prompts.AGENT_SYSTEM,
            prompts.AGENT_SYSTEM_DOCS,
            prompts.AGENT_SYSTEM_CATIA,
            prompts.AGENT_SYSTEM_CATIA_DOCS,
        }

    def test_two_calls_are_the_same_string(self) -> None:
        assert system_prompt() == system_prompt()


class TestTheOnePartThatVariesSitsLast:
    def test_the_state_block_is_next_to_the_newest_question(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        """Everything ahead of it is the cached prefix; it is the only block rebuilt every turn."""
        conversation = Conversation(owner_id=user.id, project_id=project.id, title="t")
        db_session.add(conversation)
        db_session.flush()
        for sequence, (role, text) in enumerate(
            [(MessageRole.USER, "one"), (MessageRole.ASSISTANT, "two"), (MessageRole.USER, "three")],
            start=1,
        ):
            db_session.add(
                ConversationMessage(
                    conversation_id=conversation.id, sequence=sequence, role=role, content=text
                )
            )
        db_session.flush()
        db_session.refresh(conversation)

        messages: list[dict[str, Any]] = build_messages(db_session, user, conversation)

        assert [m["content"] for m in messages[:2]] == ["one", "two"]
        assert messages[-1]["content"] == "three"
        assert messages[-2]["role"] == "user"  # the state block, between history and question
        assert messages[-2]["content"] not in {"one", "three"}
