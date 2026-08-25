"""Agent loop tests.

The provider is scripted, so these assert the *loop's* guarantees -- it
terminates, it recovers from tool errors, it remembers, it refuses to mutate
without consent -- rather than whether some model happened to behave.
"""

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.agent import MAX_STEPS, run_agent
from app.ai.provider import AssistantTurn, LLMProvider, ToolCall
from app.ai.tools import ToolBox, ToolError
from app.models import Conversation, MessageRole, Project, User


class ScriptedProvider(LLMProvider):
    """Replays a fixed list of turns and records the transcript it was given."""

    name = "scripted"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.seen_transcripts: list[list[dict[str, Any]]] = []
        self.seen_tool_names: list[list[str]] = []

    def health(self) -> None:
        return None

    def complete(self, **_: Any) -> Any:  # not used by the agent loop
        raise NotImplementedError

    def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AssistantTurn:
        self.seen_transcripts.append(messages)
        self.seen_tool_names.append([t["function"]["name"] for t in tools])
        # Past the script, settle -- mirrors a model that stops calling tools.
        if not self._turns:
            return AssistantTurn(text="Done.")
        return self._turns.pop(0)


@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="agent@kryova.dev", hashed_password=hash_password("a-long-enough-password")
    )
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


def _toolbox(db_session: Session, user: User, project: Project) -> ToolBox:
    return ToolBox(db=db_session, user=user, project_id=project.id)


class TestTermination:
    def test_answers_directly_when_no_tools_are_needed(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider([AssistantTurn(text="Aluminium yields at 276 MPa.")])
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="What does 6061 yield at?",
        )
        assert reply.text == "Aluminium yields at 276 MPa."
        assert reply.steps == []
        assert reply.truncated is False

    def test_a_model_that_never_stops_is_cut_off(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The loop must terminate even against a model stuck in a tool loop."""
        looping = [
            AssistantTurn(tool_calls=[ToolCall(id=f"c{i}", name="list_projects", arguments={})])
            for i in range(MAX_STEPS + 5)
        ]
        provider = ScriptedProvider(looping)
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="loop forever",
        )
        assert reply.truncated is True
        assert len(reply.steps) == MAX_STEPS


class TestToolErrorRecovery:
    def test_a_failing_tool_becomes_a_result_the_model_can_read(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id="c1", name="get_simulation", arguments={"simulation_id": "nope"})
                    ]
                ),
                AssistantTurn(text="That run does not exist."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="show me run nope",
        )
        assert reply.steps[0].ok is False
        assert "nope" in str(reply.steps[0].result)
        # The turn survived and produced an answer rather than raising.
        assert reply.text == "That run does not exist."

    def test_an_unknown_tool_is_reported_not_raised(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(tool_calls=[ToolCall(id="c1", name="teleport", arguments={})]),
                AssistantTurn(text="No such tool."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="teleport",
        )
        assert reply.steps[0].ok is False
        assert "no tool called" in str(reply.steps[0].result).lower()

    def test_bad_arguments_are_reported_not_raised(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="list_projects", arguments={"nope": 1})]
                ),
                AssistantTurn(text="Recovered."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="go",
        )
        assert reply.steps[0].ok is False
        assert "bad arguments" in str(reply.steps[0].result).lower()


class TestMemory:
    def test_the_next_turn_replays_the_previous_one(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """Memory is the whole point: turn two must see turn one."""
        first = ScriptedProvider([AssistantTurn(text="Your project is Bracket.")])
        run_agent(
            db=db_session,
            provider=first,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="what projects do I have?",
        )

        second = ScriptedProvider([AssistantTurn(text="Still Bracket.")])
        run_agent(
            db=db_session,
            provider=second,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="and now?",
        )

        replayed = second.seen_transcripts[0]
        roles = [m["role"] for m in replayed]
        assert roles == ["user", "assistant", "user"]
        assert replayed[0]["content"] == "what projects do I have?"
        assert replayed[1]["content"] == "Your project is Bracket."

    def test_failed_tool_calls_stay_in_the_transcript(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The agent must be able to see what it already tried and failed."""
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id="c1", name="get_simulation", arguments={"simulation_id": "x"})
                    ]
                ),
                AssistantTurn(text="Not found."),
            ]
        )
        run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="show x",
        )

        stored = conversation.messages
        tool_turns = [m for m in stored if m.role is MessageRole.TOOL]
        assert len(tool_turns) == 1
        assert tool_turns[0].is_error is True
        assert tool_turns[0].tool_name == "get_simulation"

        # And that failure is replayed on the following turn.
        follow_up = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=follow_up,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="try again",
        )
        replayed = follow_up.seen_transcripts[0]
        assert any(m["role"] == "tool" and m["is_error"] for m in replayed)


class TestMutationGate:
    def test_mutating_tools_are_hidden_unless_allowed(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        readonly = [t["function"]["name"] for t in box.schemas(include_mutating=False)]
        full = [t["function"]["name"] for t in box.schemas(include_mutating=True)]
        assert "run_simulation" not in readonly
        assert "run_simulation" in full

    def test_calling_a_mutating_tool_without_consent_is_refused(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="confirmation"):
            box.call("run_simulation", {"load_case": {}}, allow_mutations=False)


class TestOwnershipScoping:
    def test_another_users_project_is_not_found(self, db_session: Session, user: User) -> None:
        """A hallucinated id must never reach another user's data."""
        from app.core.security import hash_password

        other = User(
            email="other@kryova.dev", hashed_password=hash_password("a-long-enough-password")
        )
        db_session.add(other)
        db_session.flush()
        theirs = Project(name="Secret", owner_id=other.id)
        db_session.add(theirs)
        db_session.flush()

        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("list_geometry", {"project_id": theirs.id}, allow_mutations=False)

    def test_listing_only_returns_the_callers_projects(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = ToolBox(db=db_session, user=user, project_id=None)
        result = box.call("list_projects", {}, allow_mutations=False)
        assert [p["id"] for p in result["projects"]] == [project.id]


class TestCreateProject:
    """The entry point of the chat-first flow: the agent makes the project."""

    def test_creates_a_project_owned_by_the_caller(
        self, db_session: Session, user: User
    ) -> None:
        box = ToolBox(db=db_session, user=user, project_id=None)
        result = box.call(
            "create_project",
            {"name": "Bracket", "description": "Motor mount"},
            allow_mutations=False,
        )

        row = db_session.get(Project, result["id"])
        assert row is not None
        assert row.owner_id == user.id
        assert row.name == "Bracket"
        assert row.description == "Motor mount"

    def test_adopts_the_new_project_as_the_conversation_scope(
        self, db_session: Session, user: User
    ) -> None:
        """Without this, every later tool in the turn has no project to resolve."""
        box = ToolBox(db=db_session, user=user, project_id=None)
        assert box.project_id is None

        result = box.call("create_project", {"name": "Arm"}, allow_mutations=False)

        assert box.project_id == result["id"]
        # A tool called with no project_id must now resolve to the new project.
        # It has no geometry yet, so the *content* of the error is the proof:
        # it names "Arm" rather than complaining there is no project in scope.
        with pytest.raises(ToolError, match="'Arm' has no geometry"):
            box.call("list_geometry", {}, allow_mutations=False)

    def test_is_available_without_the_mutation_gate(
        self, db_session: Session, user: User
    ) -> None:
        """Creating an empty project is cheap; only compute-burning tools are gated."""
        box = ToolBox(db=db_session, user=user, project_id=None)
        names = {s["function"]["name"] for s in box.schemas(include_mutating=False)}
        assert "create_project" in names
        assert "run_simulation" not in names

    def test_a_blank_name_is_a_tool_error_not_a_crash(
        self, db_session: Session, user: User
    ) -> None:
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError):
            box.call("create_project", {"name": "   "}, allow_mutations=False)

    def test_an_overlong_name_is_refused(self, db_session: Session, user: User) -> None:
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError):
            box.call("create_project", {"name": "x" * 256}, allow_mutations=False)
