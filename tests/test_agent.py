"""Agent loop tests.

The provider is scripted, so these assert the *loop's* guarantees -- it
terminates, it recovers from tool errors, it remembers, it stays inside a
context window, it refuses to mutate without consent, and it actually submits
the work it claims to have submitted -- rather than whether some model happened
to behave.
"""

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.agent import DEFAULT_MAX_STEPS, max_steps, run_agent
from app.ai.provider import AssistantTurn, Completion, LLMProvider, TokenUsage, ToolCall
from app.ai.state import bound_document_name
from app.ai.tools import ToolBox, ToolError
from app.core.config import settings
from app.jobs import InlineJobQueue
from app.models import (
    Conversation,
    ConversationMessage,
    GeometryVersion,
    JobStatus,
    Media,
    MediaKind,
    MessageRole,
    Project,
    SimulationJob,
    User,
)

LOAD_CASE: dict[str, Any] = {
    "name": "Tip load",
    "material": {
        "name": "aluminium-6061-t6",
        "youngs_modulus_mpa": 68_900,
        "poissons_ratio": 0.33,
        "yield_strength_mpa": 276,
        "density_kg_m3": 2700,
    },
    "fixtures": [{"where": {"type": "face", "axis": "z", "side": "min"}, "dofs": ["x", "y", "z"]}],
    "loads": [{"where": {"type": "face", "axis": "z", "side": "max"}, "force_n": [0, 0, -500]}],
}


class ScriptedProvider(LLMProvider):
    """Replays a fixed list of turns and records what it was given."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.seen_transcripts: list[list[dict[str, Any]]] = []
        self.seen_systems: list[str] = []
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
        self.seen_systems.append(system)
        self.seen_tool_names.append([t["function"]["name"] for t in tools])
        # Past the script, settle -- mirrors a model that stops calling tools.
        if not self._turns:
            return AssistantTurn(text="Done.", usage=TokenUsage(3, 4))
        return self._turns.pop(0)

    @property
    def last_user_text(self) -> str:
        """Everything in the most recent transcript, flattened."""
        return "\n".join(str(message.get("content", "")) for message in self.seen_transcripts[-1])


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


@pytest.fixture
def geometry(db_session: Session, project: Project, user: User) -> GeometryVersion:
    media = Media(
        owner_id=user.id,
        kind=MediaKind.CAD,
        filename="bracket.stl",
        content_type="model/stl",
        size_bytes=128,
        sha256="0" * 64,
    )
    db_session.add(media)
    db_session.flush()
    version = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename="bracket.stl",
        file_format="stl",
        stats={"bounding_box": {"min": [0, 0, 0], "max": [10, 20, 5]}},
    )
    db_session.add(version)
    db_session.flush()
    return version


def _toolbox(db_session: Session, user: User, project: Project, **kwargs: Any) -> ToolBox:
    return ToolBox(db=db_session, user=user, project_id=project.id, **kwargs)


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
        """The loop must terminate even against a model that never answers.

        The calls vary, because a model repeating one call verbatim is stopped
        sooner and for a different reason -- see
        `TestATurnStopsRepeatingItself`. This is the backstop underneath that:
        a model doing genuinely new work forever still stops at the budget.

        **The calls must also SUCCEED, and that is what this got wrong until
        2026-09-10.** It drove the loop with `list_projects(limit=...)`, and
        `_list_projects` takes no arguments -- so every step failed with the
        same "Bad arguments" error. That was invisible while nothing counted
        failures, and E16.4 now escalates a tool failing three times for the
        same reason, so the turn ended at step 3 of 60 and this read as the
        budget being broken. It was the escalation working. `update_project`
        with a fresh name is real work, it varies, and it succeeds.
        """
        budget = max_steps()
        looping = [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id=f"c{i}", name="update_project", arguments={"name": f"step {i}"})
                ]
            )
            for i in range(budget + 5)
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
        assert len(reply.steps) == budget

    def test_the_step_budget_is_configurable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A CATIA session legitimately needs more steps than a lookup does."""
        monkeypatch.delenv("AI_MAX_STEPS", raising=False)
        assert max_steps() == DEFAULT_MAX_STEPS

        monkeypatch.setenv("AI_MAX_STEPS", "3")
        assert max_steps() == 3

        # A nonsense value must not take the agent down with it.
        monkeypatch.setenv("AI_MAX_STEPS", "many")
        assert max_steps() == DEFAULT_MAX_STEPS

    def test_a_truncated_answer_is_labelled_as_one(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A half-sentence about a part must not read as a finished thought."""
        provider = ScriptedProvider(
            [AssistantTurn(text="The peak stress is 41% of yi", truncated=True)]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="how did it go?",
        )
        assert "cut off" in reply.text


class TestToolErrorRecovery:
    def test_a_failing_tool_becomes_a_result_the_model_can_read(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(
                            id="c1", name="get_simulation", arguments={"simulation_id": "nope"}
                        )
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
        assert [m["role"] for m in replayed] == ["user", "assistant", "user", "user"]
        assert replayed[0]["content"] == "what projects do I have?"
        assert replayed[1]["content"] == "Your project is Bracket."
        # The state block is spliced in immediately before the newest question.
        assert "<current_state>" in replayed[2]["content"]
        assert replayed[3]["content"] == "and now?"

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
        assert tool_turns[0].duration_ms is not None

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


class TestContextWindow:
    """A long design session must stay inside the model's context window."""

    def test_a_hundred_message_conversation_stays_inside_the_window(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        for _ in range(50):
            run_agent(
                db=db_session,
                provider=ScriptedProvider([AssistantTurn(text="ack")]),
                conversation=conversation,
                toolbox=_toolbox(db_session, user, project),
                user_message="another question",
            )
        assert len(conversation.messages) == 100

        final = ScriptedProvider([AssistantTurn(text="done")])
        run_agent(
            db=db_session,
            provider=final,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="and finally?",
        )

        replayed = final.seen_transcripts[0]
        # The window, plus the summary block and the state block. Without the
        # cap this would be 101 messages and growing forever.
        assert len(replayed) <= settings.ai_max_context_messages + 2
        # The newest question always survives, whatever else was dropped.
        assert replayed[-1]["content"] == "and finally?"

    def test_older_turns_are_folded_into_a_stored_summary(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        for _ in range(30):
            run_agent(
                db=db_session,
                provider=ScriptedProvider([AssistantTurn(text="ack")]),
                conversation=conversation,
                toolbox=_toolbox(db_session, user, project),
                user_message="tell me about the bracket",
            )

        assert conversation.summary is not None
        assert conversation.summary_through_sequence > 0

        follow_up = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=follow_up,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="carry on",
        )
        assert "<conversation_summary>" in follow_up.seen_transcripts[-1][0]["content"]

    def test_a_tool_heavy_turn_never_loses_the_question_it_is_answering(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """One turn can outproduce the whole window. The question must survive.

        Twenty tool rounds write forty messages after the user's message, so a
        naive tail of the last `ai_max_context_messages` drops the question and
        leaves the model inferring what it was asked from tool output.
        """
        from app.ai.context import window

        budget = max_steps()
        # Varied arguments, so the turn runs its full length rather than being
        # stopped early as a repeat -- what is under test here is the window,
        # not the loop guard. The calls must also succeed: `list_projects` takes
        # no arguments, so the varied `limit` this used until 2026-09-10 failed
        # every step and E16.4 escalated the turn at step 3. See
        # `TestTermination.test_a_model_that_never_stops_is_cut_off`.
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id=f"c{i}", name="update_project", arguments={"name": f"step {i}"})
                    ]
                )
                for i in range(budget)
            ]
        )
        run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="the question that must not be lost",
        )

        assert len(conversation.messages) > settings.ai_max_context_messages
        kept = window(conversation)
        assert kept[0].role is MessageRole.USER
        assert kept[0].content == "the question that must not be lost"
        # Every transcript the provider saw during that loop carried it too.
        for transcript in provider.seen_transcripts:
            flattened = "\n".join(str(m.get("content", "")) for m in transcript)
            assert "the question that must not be lost" in flattened

    def test_the_window_never_opens_on_an_orphaned_tool_result(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A transcript starting on a tool_result is a 400 from every provider."""
        from app.ai.context import window

        for index in range(60):
            run_agent(
                db=db_session,
                provider=ScriptedProvider(
                    [
                        AssistantTurn(
                            tool_calls=[
                                ToolCall(id=f"c{index}", name="list_projects", arguments={})
                            ]
                        ),
                        AssistantTurn(text="ok"),
                    ]
                ),
                conversation=conversation,
                toolbox=_toolbox(db_session, user, project),
                user_message="check",
            )

        kept = window(conversation)
        assert kept
        assert kept[0].role is MessageRole.USER


class TestStateBlock:
    """The transcript is history. Only the state block is current."""

    def test_the_state_block_reflects_the_database_not_the_transcript(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        first = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=first,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="hello",
        )
        assert "Bracket" in first.last_user_text

        # Something changes out of band -- another tab, another request, an
        # admin. The transcript still says "Bracket" and must not be believed.
        project.name = "Renamed motor mount"
        db_session.flush()

        second = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=second,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="what am I working on?",
        )
        assert "Renamed motor mount" in second.last_user_text

    def test_a_run_finishing_out_of_band_shows_up_next_turn(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        geometry: GeometryVersion,
    ) -> None:
        """A worker finishing a job never touches the transcript. It must still land."""
        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=geometry.id,
            status=JobStatus.QUEUED,
            solver="linear-static",
            load_case=LOAD_CASE,
        )
        db_session.add(job)
        db_session.flush()

        first = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=first,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="anything running?",
        )
        assert "status=queued" in first.last_user_text
        assert "runs_in_flight: 1" in first.last_user_text

        # The background worker finishes it, out of band.
        job.status = JobStatus.SUCCEEDED
        job.result = {"factor_of_safety": 2.4, "max_von_mises_mpa": 115.0}
        db_session.flush()

        second = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=second,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="and now?",
        )
        assert "status=succeeded" in second.last_user_text
        assert "factor_of_safety=2.4" in second.last_user_text
        assert "runs_in_flight" not in second.last_user_text

    def test_the_state_block_carries_geometry(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        geometry: GeometryVersion,
    ) -> None:
        provider = ScriptedProvider([AssistantTurn(text="ok")])
        run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="what geometry do I have?",
        )
        assert "bracket.stl" in provider.last_user_text
        assert "latest_bounding_box_mm" in provider.last_user_text


class TestMutationGate:
    def test_mutating_tools_are_hidden_unless_allowed(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        readonly = [t["function"]["name"] for t in box.schemas(include_mutating=False)]
        full = [t["function"]["name"] for t in box.schemas(include_mutating=True)]
        assert "run_simulation" not in readonly
        assert "delete_simulation" not in readonly
        assert "run_simulation" in full
        assert "delete_simulation" in full

    def test_calling_a_mutating_tool_without_consent_is_refused(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="confirmation"):
            box.call("run_simulation", {"load_case": {}}, allow_mutations=False)


class TestRunSimulation:
    """The tool that used to lie: it said "queued" and queued nothing."""

    def test_a_job_row_is_created_and_submitted(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        submitted: list[Any] = []

        class RecordingQueue(InlineJobQueue):
            def submit(self, job: Any) -> None:
                # Record rather than run: the point of this test is the queue
                # handoff, not gmsh.
                submitted.append(job)

        box = _toolbox(
            db_session,
            user,
            project,
            job_queue=RecordingQueue(),
            session_scope=lambda: None,
            media_store=object(),
        )
        result = box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

        assert result["status"] == JobStatus.QUEUED.value
        assert len(submitted) == 1

        job = db_session.get(SimulationJob, result["id"])
        assert job is not None
        assert job.project_id == project.id
        assert job.geometry_version_id == geometry.id
        assert job.status is JobStatus.QUEUED
        assert job.load_case["name"] == "Tip load"

    def test_it_never_reports_a_result(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        """The old shape invited the agent to announce an outcome. This one does not."""
        box = _toolbox(
            db_session,
            user,
            project,
            job_queue=InlineJobQueue(),
            session_scope=lambda: None,
            media_store=object(),
        )

        class NoopQueue(InlineJobQueue):
            def submit(self, job: Any) -> None:
                return None

        box.job_queue = NoopQueue()
        result = box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)
        assert "ready_to_submit" not in result
        assert "factor_of_safety" not in result
        assert "poll" in result["note"].lower() or "get_simulation" in result["note"]

    def test_it_refuses_rather_than_pretending_when_there_is_no_queue(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="cannot be submitted"):
            box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(SimulationJob)
                .where(SimulationJob.project_id == project.id)
            )
            == 0
        )

    def test_quadratic_elements_can_be_requested(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        """The tool must offer every knob the HTTP route does, or the agent cannot."""

        class NoopQueue(InlineJobQueue):
            def submit(self, job: Any) -> None:
                return None

        box = _toolbox(
            db_session,
            user,
            project,
            job_queue=NoopQueue(),
            session_scope=lambda: None,
            media_store=object(),
        )
        result = box.call(
            "run_simulation",
            {"load_case": LOAD_CASE, "element_order": 2},
            allow_mutations=True,
        )
        job = db_session.get(SimulationJob, result["id"])
        assert job is not None
        assert job.element_order == 2

    def test_a_nonsense_element_order_is_a_tool_error(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        box = _toolbox(db_session, user, project, job_queue=InlineJobQueue())
        with pytest.raises(ToolError, match="element_order must be 1"):
            box.call(
                "run_simulation",
                {"load_case": LOAD_CASE, "element_order": 3},
                allow_mutations=True,
            )

    def test_the_per_user_quota_binds_across_projects(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        """The per-project check misses the case the shared queue cares about."""
        from app.core.config import settings as app_settings

        for index in range(app_settings.max_concurrent_simulations_per_user):
            other = Project(name=f"Other {index}", owner_id=user.id)
            db_session.add(other)
            db_session.flush()
            db_session.add(
                SimulationJob(
                    project_id=other.id,
                    geometry_version_id=geometry.id,
                    status=JobStatus.QUEUED,
                    solver="linear-static",
                    load_case=LOAD_CASE,
                )
            )
        db_session.flush()

        box = _toolbox(
            db_session,
            user,
            project,
            job_queue=InlineJobQueue(),
            session_scope=lambda: None,
            media_store=object(),
        )
        with pytest.raises(ToolError, match="which is the limit"):
            box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)

    def test_a_second_run_is_refused_while_one_is_in_flight(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        class NoopQueue(InlineJobQueue):
            def submit(self, job: Any) -> None:
                return None

        box = _toolbox(
            db_session,
            user,
            project,
            job_queue=NoopQueue(),
            session_scope=lambda: None,
            media_store=object(),
        )
        box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)
        with pytest.raises(ToolError, match="already queued or running"):
            box.call("run_simulation", {"load_case": LOAD_CASE}, allow_mutations=True)


class TestDeleteSimulation:
    def test_an_unfinished_run_cannot_be_deleted(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=geometry.id,
            status=JobStatus.RUNNING,
            solver="linear-static",
            load_case=LOAD_CASE,
        )
        db_session.add(job)
        db_session.flush()

        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="wait for it to finish"):
            box.call("delete_simulation", {"simulation_id": job.id}, allow_mutations=True)

    def test_a_finished_run_is_deleted(
        self,
        db_session: Session,
        user: User,
        project: Project,
        geometry: GeometryVersion,
    ) -> None:
        job = SimulationJob(
            project_id=project.id,
            geometry_version_id=geometry.id,
            status=JobStatus.SUCCEEDED,
            solver="linear-static",
            load_case=LOAD_CASE,
            result={"factor_of_safety": 2.1},
        )
        db_session.add(job)
        db_session.flush()
        job_id = job.id

        box = _toolbox(db_session, user, project)
        result = box.call("delete_simulation", {"simulation_id": job_id}, allow_mutations=True)
        assert result["deleted"]["id"] == job_id
        assert db_session.get(SimulationJob, job_id) is None


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

    def test_creates_a_project_owned_by_the_caller(self, db_session: Session, user: User) -> None:
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

    def test_is_available_without_the_mutation_gate(self, db_session: Session, user: User) -> None:
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


class TestTokenAccounting:
    def test_the_turn_reports_what_it_spent(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id="c1", name="list_projects", arguments={})],
                    usage=TokenUsage(100, 20),
                ),
                AssistantTurn(text="Found them.", usage=TokenUsage(150, 30)),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="list them",
        )
        assert reply.usage.prompt_tokens == 250
        assert reply.usage.completion_tokens == 50
        assert reply.usage.total_tokens == 300

    def test_usage_adds(self) -> None:
        assert (TokenUsage(1, 2) + TokenUsage(3, 4)) == TokenUsage(4, 6)


def test_completion_carries_both_the_value_and_the_cost() -> None:
    """`complete` must not drop usage on the floor the way it used to."""
    from app.ai.schemas import Finding

    finding = Finding(title="t", detail="d", severity="info")
    completion = Completion(value=finding, usage=TokenUsage(10, 5))
    assert completion.value is finding
    assert completion.usage.total_tokens == 15


class TestEveryScopedToolChecksOwnership:
    """The ownership check on each tool must be load-bearing, not decorative.

    `TestOwnershipScoping` above covers `list_geometry` only. That left the
    guard in `ToolBox._simulation` -- which fronts both `get_simulation` (a
    cross-tenant read) and `delete_simulation` (a cross-tenant destroy) --
    completely uncovered: deleting the line `self._project(job.project_id)`
    left the entire suite green. These tests exist so that stops being true.

    Every tool that resolves an id the model supplied gets a case here. If you
    add another, add it to this class in the same commit.
    """

    @pytest.fixture
    def stranger(self, db_session: Session) -> tuple[User, Project, str]:
        """Another user, holding a project with one finished simulation."""
        from app.core.security import hash_password

        other = User(
            email="stranger@kryova.dev",
            hashed_password=hash_password("a-long-enough-password"),
        )
        db_session.add(other)
        db_session.flush()
        theirs = Project(name="Their bracket", owner_id=other.id)
        db_session.add(theirs)
        db_session.flush()
        media = Media(
            owner_id=other.id,
            kind=MediaKind.CAD,
            filename="theirs.stl",
            content_type="model/stl",
            size_bytes=128,
            sha256="1" * 64,
        )
        db_session.add(media)
        db_session.flush()
        version = GeometryVersion(
            project_id=theirs.id,
            media_id=media.id,
            version_number=1,
            filename="theirs.stl",
            file_format="stl",
            stats={},
        )
        db_session.add(version)
        db_session.flush()
        job = SimulationJob(
            project_id=theirs.id,
            geometry_version_id=version.id,
            status=JobStatus.SUCCEEDED,
            solver="linear-static",
            load_case={"name": "theirs"},
            result={"max_von_mises_mpa": 42.0},
        )
        db_session.add(job)
        db_session.flush()
        return other, theirs, job.id

    def test_get_simulation_refuses_another_users_run(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, _, job_id = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("get_simulation", {"simulation_id": job_id}, allow_mutations=False)

    def test_delete_simulation_refuses_another_users_run(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        """The destructive half of the same guard."""
        _, _, job_id = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("delete_simulation", {"simulation_id": job_id}, allow_mutations=True)
        # And it must still be there.
        assert db_session.get(SimulationJob, job_id) is not None

    def test_list_simulations_refuses_another_users_project(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, theirs, _ = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("list_simulations", {"project_id": theirs.id}, allow_mutations=False)

    def test_get_project_refuses_another_users_project(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, theirs, _ = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("get_project", {"project_id": theirs.id}, allow_mutations=False)

    def test_update_project_refuses_another_users_project(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, theirs, _ = stranger
        original = theirs.name
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call(
                "update_project",
                {"project_id": theirs.id, "name": "hijacked"},
                allow_mutations=True,
            )
        assert theirs.name == original

    def test_delete_project_refuses_another_users_project(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, theirs, _ = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call("delete_project", {"project_id": theirs.id}, allow_mutations=True)
        assert db_session.get(Project, theirs.id) is not None

    def test_run_simulation_refuses_another_users_project(
        self, db_session: Session, user: User, stranger: tuple[User, Project, str]
    ) -> None:
        _, theirs, _ = stranger
        box = ToolBox(db=db_session, user=user, project_id=None)
        with pytest.raises(ToolError, match="belongs to you"):
            box.call(
                "run_simulation",
                {"project_id": theirs.id, "load_case": {"name": "x"}},
                allow_mutations=True,
            )


class TestProjectManagementTools:
    """`update_project` / `delete_project` / `get_project`.

    These exist because the agent is the only surface that can rename or delete
    a project -- the web UI never shipped either control.
    """

    def test_get_project_reports_counts_without_loading_rows(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        result = box.call("get_project", {}, allow_mutations=False)
        assert result["id"] == project.id
        assert result["geometry_version_count"] == 0
        assert result["simulation_count"] == 0
        assert result["latest_geometry"] is None

    def test_update_project_renames_without_clearing_the_description(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        project.description = "Motor mount"
        db_session.flush()
        box = _toolbox(db_session, user, project)
        result = box.call("update_project", {"name": "Renamed"}, allow_mutations=True)
        assert result["name"] == "Renamed"
        # Omitting a field must leave it alone, not blank it.
        assert project.description == "Motor mount"
        assert result["updated"] == ["name"]

    def test_update_project_clears_a_description_on_an_explicit_empty_string(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        project.description = "Motor mount"
        db_session.flush()
        box = _toolbox(db_session, user, project)
        box.call("update_project", {"description": ""}, allow_mutations=True)
        assert project.description is None

    def test_update_project_refuses_a_blank_name(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="cannot be blank"):
            box.call("update_project", {"name": "   "}, allow_mutations=True)

    def test_update_project_refuses_a_no_op_call(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="Nothing to change"):
            box.call("update_project", {}, allow_mutations=True)

    def test_delete_project_is_gated_behind_confirmation(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        """Destroying every file in a project must never happen unconfirmed."""
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError):
            box.call("delete_project", {}, allow_mutations=False)
        assert db_session.get(Project, project.id) is not None

    def test_delete_project_refuses_while_a_run_is_in_flight(
        self, db_session: Session, user: User, project: Project, geometry: GeometryVersion
    ) -> None:
        """A live worker must not have its project deleted out from under it."""
        db_session.add(
            SimulationJob(
                project_id=project.id,
                geometry_version_id=geometry.id,
                status=JobStatus.RUNNING,
                solver="linear-static",
                load_case={"name": "in flight"},
            )
        )
        db_session.flush()
        box = _toolbox(db_session, user, project)
        with pytest.raises(ToolError, match="queued or running"):
            box.call("delete_project", {}, allow_mutations=True)
        assert db_session.get(Project, project.id) is not None

    def test_delete_project_reports_what_it_destroyed(
        self, db_session: Session, user: User, project: Project, geometry: GeometryVersion
    ) -> None:
        db_session.add(
            SimulationJob(
                project_id=project.id,
                geometry_version_id=geometry.id,
                status=JobStatus.SUCCEEDED,
                solver="linear-static",
                load_case={"name": "done"},
            )
        )
        db_session.flush()
        project_id = project.id
        box = _toolbox(db_session, user, project)
        result = box.call("delete_project", {}, allow_mutations=True)
        assert result["simulations_deleted"] == 1
        assert db_session.get(Project, project_id) is None
        # The conversation's scope pointed at the row that just vanished.
        assert box.project_id is None


class TestTheOpenKernelBindingSurvivesTheAgentLayer:
    """`GEOMETRY_BACKEND=occt` through the tool layer, not through the dispatcher.

    `tests/test_geometry_backends.py` drives `dispatch.call_catia` directly and
    proved the kernel builds. The refusals that gate it live one layer up, here,
    and nothing exercised the two together — so on the Windows seat on
    2026-09-05 the real chat endpoint could create a part and then do nothing
    whatever to it, and a `uvicorn` restart left the conversation unusable for
    good. Both are pinned below.
    """

    @pytest.fixture(autouse=True)
    def _occt(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from app.geometry import backends

        monkeypatch.setattr(settings, "geometry_backend", "occt")
        for key in list(backends._sessions):
            backends.forget(key)
        yield
        for key in list(backends._sessions):
            backends.forget(key)

    @staticmethod
    def _bind(db_session: Session, conversation: Conversation, name: str = "Part") -> None:
        """The row `catia_new_part` writes, without needing the kernel installed."""
        from app.models.catia import CatiaDocument

        db_session.add(
            CatiaDocument(conversation_id=conversation.id, device_id=None, doc_name=name)
        )
        db_session.flush()

    def test_a_local_document_binds_with_no_device(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        """`device_id` is NULL for the open kernel: there is no seat to name.

        The column has been nullable since revoking a laptop had to leave the
        record of what was built on it behind, so this needs no migration — but
        nothing wrote such a row until the local branch learned to.
        """
        from app.models.catia import CatiaDocument

        self._bind(db_session, conversation, "Bracket")
        row = db_session.scalar(
            select(CatiaDocument).where(CatiaDocument.conversation_id == conversation.id)
        )
        assert row is not None and row.device_id is None
        assert bound_document_name(db_session, conversation.id) == "Bracket"

    def test_a_bound_part_with_no_live_session_may_be_rebuilt(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        """The deadlock: a durable row naming a document that no longer exists.

        The binding is in Postgres and survives a restart; the kernel document
        is live OCAF state in this process and does not. Gating `catia_new_part`
        on the row alone meant every scoped tool answered "No document is open"
        from the runner while the only tool that could open one was refused from
        the database — with no way out of the conversation.
        """
        from app.geometry import backends

        self._bind(db_session, conversation, "Bracket")
        assert backends.peek_session(conversation.id) is None

        box = ToolBox(db=db_session, user=user, conversation=conversation)
        with pytest.raises(ToolError) as refused:
            box._call_catia("catia_pad", {"sketch": "profile", "length_mm": 20.0})
        # A scoped tool is still refused -- there is genuinely nothing to act on.
        assert "no live" not in str(refused.value).lower()

        # ...but starting again must not be refused, and must not name a tool
        # the open kernel never offers.
        message = ""
        try:
            box._call_catia("catia_new_part", {"name": "Bracket"})
        except ToolError as exc:  # the kernel may be absent; the *guard* is the subject
            message = str(exc)
        assert "already owns" not in message, message
        assert "catia_open_document" not in message, message

    def test_no_refusal_here_names_a_tool_the_open_kernel_does_not_offer(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        """`catia_open_document` reopens a file from disk; the kernel has none.

        It is not in `backends.local_tool_names()`, so a refusal that tells the
        model to call it sends it after a tool it was never given a schema for.
        Observed live: the model called it, was told no such tool exists, retried
        `catia_new_part`, and looped.
        """
        from app.geometry import backends

        assert "catia_open_document" not in backends.local_tool_names()

        box = ToolBox(db=db_session, user=user, conversation=conversation)
        with pytest.raises(ToolError) as refused:
            box._call_catia("catia_pad", {"sketch": "profile", "length_mm": 20.0})
        assert "catia_open_document" not in str(refused.value)


class TestTheCorrectionBudgetIsConsecutive:
    """A blank the model recovered from is not evidence of a stuck model.

    Measured on ladder prompt H4 turn 2, 2026-09-06, on the seat. The model
    went blank at step 3 and again at step 6. Both were corrected, and both
    recovered immediately -- the steps after them ran real CATIA calls. That
    spent a lifetime budget of two, so the blank at step 11 had nothing left
    and ended the turn: five tool calls of work done, no write-up, and nine of
    the twenty steps never used.
    """

    def test_a_blank_between_two_working_steps_does_not_use_up_the_budget(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """Three blanks, each recovered by a real tool call, then an answer.
        On a lifetime budget the third blank ends the turn with no text."""
        provider = ScriptedProvider(
            [
                AssistantTurn(tool_calls=[ToolCall(id="1", name="list_projects", arguments={})]),
                AssistantTurn(text=""),
                AssistantTurn(tool_calls=[ToolCall(id="2", name="list_projects", arguments={})]),
                AssistantTurn(text=""),
                AssistantTurn(tool_calls=[ToolCall(id="3", name="list_projects", arguments={})]),
                AssistantTurn(text=""),
                AssistantTurn(tool_calls=[ToolCall(id="4", name="list_projects", arguments={})]),
                AssistantTurn(text="Here is what I found."),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look around",
        )
        assert reply.text == "Here is what I found."
        assert "did not manage to write up" not in reply.text

    def test_two_blanks_in_a_row_still_stop(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The budget still does its job: a model that is actually stuck gets
        two corrections and no more, because nothing resets the count."""
        provider = ScriptedProvider(
            [
                AssistantTurn(tool_calls=[ToolCall(id="1", name="list_projects", arguments={})]),
                AssistantTurn(text=""),
                AssistantTurn(text=""),
                AssistantTurn(text=""),
                AssistantTurn(text="too late"),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look around",
        )
        assert "did not manage to write up" in reply.text


class TestARepeatedReadIsRefused:
    """A read that has been answered cannot answer anything new.

    Measured on ladder prompt H4, 2026-09-06, on the seat. From step 12 the
    agent ran catia_select -> design_history -> catia_list_features ->
    catia_select -> design_history -> catia_select -> design_history: nine of
    its twenty rounds, every call succeeding, every call returning exactly what
    it had returned before, and no geometry built. The turn ended on the round
    cap with a rectangle and a polygon in one sketch and nothing extruded.
    """

    def _read(self, times: int) -> list[AssistantTurn]:
        return [
            AssistantTurn(tool_calls=[ToolCall(id=str(i), name="list_projects", arguments={})])
            for i in range(times)
        ] + [AssistantTurn(text="done")]

    def test_the_third_identical_read_does_not_run(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(self._read(4))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look",
        )
        errors = [s for s in reply.steps if not s.ok]
        assert errors, "the loop was never broken"
        assert "already called list_projects" in str(errors[0].result)

    def test_two_are_allowed_because_a_re_read_is_legitimate(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """Check a list, act, check it again is normal. Three with nothing in
        between is not."""
        provider = ScriptedProvider(self._read(2))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look",
        )
        assert all(s.ok for s in reply.steps)

    def test_the_refusal_says_what_to_do_instead(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A bare refusal sends the model to a neighbouring read, which is the
        same loop one tool over."""
        provider = ScriptedProvider(self._read(4))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look",
        )
        message = str([s for s in reply.steps if not s.ok][0].result)
        assert "act on it" in message
        assert "reading something does not alter it" in message

    def test_different_arguments_are_a_different_read(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[ToolCall(id=str(i), name="get_project", arguments={"project_id": p})]
                )
                for i, p in enumerate([project.id] * 2 + [project.id] * 2)
            ]
            + [AssistantTurn(text="done")]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look",
        )
        # Same arguments four times: the guard fires. This is the control for
        # the fingerprint being about arguments and not only about the name.
        assert any(not s.ok for s in reply.steps)

    def test_a_mutating_tool_is_never_blocked_by_it(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A repeated write may be a deliberate second hole. Writes have their
        own guards and this is not one of them."""
        from app.ai.tools import ToolBox

        box = _toolbox(db_session, user, project)
        assert box.is_mutating("run_simulation") is True
        assert box.is_mutating("list_projects") is False
        assert ToolBox.is_mutating(box, "no_such_tool") is True


class TestARefusedWriteIsNotRepeated:
    """A refused write ran nothing, so sending it again is the same dead end.

    The read guard exempts mutating tools, because a repeated write can be a
    deliberate second hole. A repeated *refused* write cannot.

    Measured on ladder prompt S1, 2026-09-06, on the seat. `catia_new_part` was
    refused at step 10 -- "this conversation already owns the CATIA document
    'Steel counterweight'" -- and sent again, byte for byte, at step 19, for
    the same refusal. Two of twenty rounds on a turn that ended out of rounds
    with a 62.88 kg block against a 2.4 kg target.
    """

    def _calls(self, times: int) -> list[AssistantTurn]:
        return [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id=str(i), name="delete_simulation", arguments={"simulation_id": "x"})
                ]
            )
            for i in range(times)
        ] + [AssistantTurn(text="done")]

    def test_the_second_identical_refusal_does_not_run_the_tool(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(self._calls(2))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="delete it",
            allow_mutations=True,
        )
        failures = [s for s in reply.steps if not s.ok]
        assert len(failures) == 2
        assert "second time delete_simulation" in str(failures[1].result)

    def test_the_original_reason_is_repeated_first(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """It is the useful half, and the model plainly did not act on it."""
        provider = ScriptedProvider(self._calls(2))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="delete it",
            allow_mutations=True,
        )
        first, second = (str(s.result) for s in reply.steps if not s.ok)
        original = first.split("\n")[0][:40]
        assert original[:20] in second

    def test_a_write_that_succeeded_is_never_blocked(self) -> None:
        """Two identical holes is a legitimate request, so only *refusals* are
        remembered. Asserted on the helper because every mutating tool in the
        toolbox refuses a second identical call on its own grounds -- which is
        the point: nothing here adds a rule, it only stops a refusal being
        re-earned."""
        from app.ai.agent import _refused_before

        assert _refused_before("catia_hole", {"diameter_mm": 9}, {}) is None

    def test_only_a_refusal_is_remembered(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A successful mutating call leaves nothing behind, so it can be
        repeated. Verified by running one and reading the record it did not
        write."""
        from app.ai.agent import _read_fingerprint, _refused_before

        recorded: dict[str, str] = {}
        box = _toolbox(db_session, user, project)
        box.call("update_project", {"project_id": project.id, "name": "Block"},
                 allow_mutations=True)
        key = _read_fingerprint("update_project", {"project_id": project.id, "name": "Block"})
        assert key not in recorded
        assert _refused_before("update_project", {"project_id": project.id}, recorded) is None

    def test_different_arguments_are_a_different_call(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id="1", name="delete_simulation", arguments={"simulation_id": "a"})
                    ]
                ),
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id="2", name="delete_simulation", arguments={"simulation_id": "b"})
                    ]
                ),
                AssistantTurn(text="done"),
            ]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="delete them",
            allow_mutations=True,
        )
        failures = [str(s.result) for s in reply.steps if not s.ok]
        assert len(failures) == 2
        assert not any("second time" in f for f in failures)


class TestATurnStopsRepeatingItself:
    """The repeat guards stop the work; they have to stop the rounds too.

    Measured on the seat, 2026-09-06, ladder prompt S2 -- a shaft and a bushing
    with a clash check. The agent finished the shaft, called `catia_new_part`
    for the bushing, was refused because a conversation owns one document, and
    called it **six more times**. Each refusal took 0 ms and cost a round, so a
    guard written to save rounds spent seven of twenty.
    """

    def _hammering(self, times: int) -> list[AssistantTurn]:
        return [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id=str(i), name="delete_simulation", arguments={"simulation_id": "x"})
                ]
            )
            for i in range(times)
        ] + [AssistantTurn(text="I could not do that.")] * 3

    def test_the_turn_ends_rather_than_spending_the_budget(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        from app.ai.agent import MAX_BLOCKED_REPEATS

        provider = ScriptedProvider(self._hammering(12))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="delete it",
            allow_mutations=True,
        )
        # The first call runs and is refused on its own merits; the repeats
        # after it are what the counter counts.
        assert len(reply.steps) <= MAX_BLOCKED_REPEATS + 2
        assert len(reply.steps) < 12

    def test_the_done_event_says_which_of_the_two_endings_this_was(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A turn ended here is not a turn that ran out of budget, and the
        stream has to say which, because the two have opposite remedies.

        **Measured on ladder prompt PRO1, 2026-09-08.** The turn was ended at
        step 31 of 60 for re-issuing a refused read, and the user was shown "the
        agent ran out of tool rounds -- ask for one thing at a time". Half the
        budget was unspent and asking for less would not have stopped the
        repeat, so the one piece of advice on screen pointed at the only thing
        that was not wrong.
        """
        from app.ai.agent import stream_agent

        provider = ScriptedProvider(self._hammering(12))
        done = [
            event
            for event in stream_agent(
                db=db_session,
                provider=provider,
                toolbox=_toolbox(db_session, user, project),
                conversation=conversation,
                user_message="delete it",
                allow_mutations=True,
            )
            if event["type"] == "done"
        ]

        assert len(done) == 1
        assert done[0]["truncated"] is True
        # `needs_input`, not `repeated_calls`, since E16.4 (2026-09-10). One
        # tool hammered is now escalated rather than merely stopped: both
        # counters reach three on the same step, and `recovery.exhausted()` is
        # tested first on purpose. That is the better of the two endings here --
        # the user gets the specific question E16.4 exists to ask instead of
        # "the agent kept repeating itself", which PRO1 measured as true and
        # unactionable. What this test protects is unchanged: the ending is
        # named, and it is not the budget running out.
        assert done[0]["stop_reason"] == "needs_input"
        assert done[0]["stop_reason"] != "max_steps"
        # Well short of the budget -- which is the whole point, and what makes
        # "ran out of tool rounds" the wrong sentence for this ending.
        assert done[0]["steps"] < 12

    def test_repeats_spread_across_tools_still_end_as_repeated_calls(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """`repeated_calls` did not become unreachable when E16.4 landed, and
        this is what says so.

        `recovery.exhausted()` counts by (tool, kind) and wins whenever one tool
        is hammered. `MAX_BLOCKED_REPEATS` counts blocked repeats whatever the
        tool, so three *different* refused writes, each re-issued once, reach it
        with every per-tool counter still at two. Without this test the branch at
        `agent.py`'s `blocked >= MAX_BLOCKED_REPEATS` would be dead and nothing
        would say so.
        """
        from app.ai.agent import stream_agent

        refused = [
            ToolCall(id="a1", name="delete_simulation", arguments={"simulation_id": "x"}),
            ToolCall(id="a2", name="delete_simulation", arguments={"simulation_id": "x"}),
            ToolCall(id="b1", name="delete_project", arguments={"project_id": "y"}),
            ToolCall(id="b2", name="delete_project", arguments={"project_id": "y"}),
            ToolCall(id="c1", name="catia_delete_feature", arguments={"feature": "z"}),
            ToolCall(id="c2", name="catia_delete_feature", arguments={"feature": "z"}),
        ]
        provider = ScriptedProvider(
            [AssistantTurn(tool_calls=[call]) for call in refused]
            + [AssistantTurn(text="I could not do that.")] * 3
        )
        done = [
            event
            for event in stream_agent(
                db=db_session,
                provider=provider,
                toolbox=_toolbox(db_session, user, project),
                conversation=conversation,
                user_message="delete them",
                allow_mutations=True,
            )
            if event["type"] == "done"
        ]

        assert len(done) == 1
        assert done[0]["stop_reason"] == "repeated_calls"

    def test_a_finished_turn_says_so_rather_than_leaving_the_field_out(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The field is on both `done` events, so nothing downstream has to
        distinguish "finished" from "an older backend that never sent it"."""
        from app.ai.agent import stream_agent

        provider = ScriptedProvider([AssistantTurn(text="Nothing to do.")])
        done = [
            event
            for event in stream_agent(
                db=db_session,
                provider=provider,
                toolbox=_toolbox(db_session, user, project),
                conversation=conversation,
                user_message="hello",
                allow_mutations=True,
            )
            if event["type"] == "done"
        ]

        assert done[0]["truncated"] is False
        assert done[0]["stop_reason"] == "finished"

    def test_the_user_still_gets_an_answer(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """Ending early must not end silently."""
        provider = ScriptedProvider(self._hammering(4))
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="delete it",
            allow_mutations=True,
        )
        assert reply.text.strip()

    def test_a_turn_with_no_repeats_runs_its_full_course(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The counter must not fire on ordinary work."""
        provider = ScriptedProvider(
            [
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id=str(i), name="get_project", arguments={"project_id": project.id})
                    ]
                )
                for i in range(2)
            ]
            + [AssistantTurn(text="done")]
        )
        reply = run_agent(
            db=db_session,
            provider=provider,
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="look",
        )
        assert reply.text == "done"
        assert all(s.ok for s in reply.steps)


class TestTheSecondPartRefusalIsBackendAccurate:
    """A refusal may only name a tool the backend actually has, and a seat is
    no longer refused a second part at all.

    Measured on the seat, 2026-09-06, ladder prompt S2. An earlier version of
    the seat's refusal sent the agent to `catia_assembly_component` -- which is
    `server_only`, the open kernel's route -- and it came back "there is no
    tool called that". Then Phase 14 made a conversation own several
    documents, so on a seat `catia_new_part` with a document already active is
    simply allowed: it starts the second part and the first stays owned.

    The open kernel still refuses (one live document, replaced on new_part),
    and its refusal names its own route, which really does exist there.
    """

    def test_the_kernel_refusal_names_its_own_route(self) -> None:
        from pathlib import Path

        from app.ai import tools as tools_module

        source = Path(tools_module.__file__).read_text(encoding="utf-8")
        at = source.index("catia_assembly_component")
        # The only mention in a refusal sits inside the `backends.is_local()`
        # branch -- that is the guard that makes naming it truthful. Found by
        # walking back to the nearest such `if`, and asserting no `else:` at
        # that indentation lies between it and the mention.
        guard = source.rfind("if backends.is_local():", 0, at)
        assert guard != -1
        between = source[guard:at]
        assert "\n            else:" not in between
        assert source.count("catia_assembly_component") == 1

    def test_a_seat_is_not_refused_a_second_part(self) -> None:
        """The refusal that cost seven rounds is gone: no ToolError in the
        seat's path says one conversation holds one part."""
        from pathlib import Path

        from app.ai import tools as tools_module

        source = Path(tools_module.__file__).read_text(encoding="utf-8")
        assert "one conversation holds one part" not in source
        assert "Do not call catia_new_part again here" not in source

    def test_every_tool_named_in_a_refusal_exists(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        """The general rule the S2 defect broke: a tool named in a refusal has
        to be a tool that is really there.

        Reads the string literals of every `ToolError(...)` in the file -- not
        the docstrings, which legitimately discuss names that do not exist as
        examples of what goes wrong.
        """
        import ast
        import re
        from pathlib import Path

        from app.ai import tools as tools_module

        box = _toolbox(db_session, user, project)
        known = {tool["function"]["name"] for tool in box.schemas(include_mutating=True)}
        # Read the file, not `inspect.getsource`: that goes through linecache,
        # which serves the source as it was when the module was first imported.
        # A guard reading a stale copy of the thing it guards passes whatever
        # you do to the file.
        tree = ast.parse(Path(tools_module.__file__).read_text(encoding="utf-8"))
        named: set[str] = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "ToolError"):
                continue
            for piece in ast.walk(node):
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    named |= set(re.findall(r"\bcatia_[a-z_]+\b", piece.value))
        # The one name that is deliberately backend-specific, and the branch
        # that writes it is guarded on `backends.is_local()`.
        unreachable = named - known - {"catia_assembly_component"}
        assert not unreachable, sorted(unreachable)


class TestOpeningDocumentsIsNotBuildingParts:
    """A turn that opens document after document and puts a solid in none of
    them is told so, once.

    **Measured on ladder prompt PRO1, 2026-09-08.** The agent was asked to
    "produce the frame, the ram, the rack, the pinion and the table as parts",
    and answered the shape of that request rather than its substance: thirty-one
    steps, five `catia_new_part` calls, six sketches, and not one pad. Its own
    closing words were "We have 5 empty part documents created but no geometry
    built yet" -- it could see the problem, at the point where the turn was over
    and the budget spent.

    Neither existing guard can see this, and that is the point of a third one.
    `MAX_IDENTICAL_READS` needs a call repeated byte for byte, and these have a
    different name every time. `MAX_READS_WITHOUT_PROGRESS` asks whether
    anything mutated -- and `catia_new_part` *is* a successful mutation, so
    every one of the five reset the barren counter. Creating a document is the
    one mutation that changes nothing about the part: it makes the container,
    not the content.
    """

    def _opening(self, count: int) -> list[AssistantTurn]:
        return [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id=str(i), name="catia_new_part", arguments={"name": f"Part{i}"})
                ]
            )
            for i in range(count)
        ] + [AssistantTurn(text="Those are the parts.")]

    @staticmethod
    def _answering(monkeypatch: pytest.MonkeyPatch, results: dict[str, dict[str, Any]]) -> None:
        """Make the named tools succeed with the payload the seat would send.

        The distinction under test lives in those payloads: every solid comes
        back through `_feature_result`, which carries `feature`, and opening a
        document carries `doc_name` and no solid.
        """

        def call(self: ToolBox, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:
            if name not in results:
                raise ToolError(f"unexpected tool {name}")
            return results[name]

        monkeypatch.setattr(ToolBox, "call", call)

    def _notes(self, db_session: Session, conversation: Conversation) -> list[str]:
        db_session.flush()
        return [
            str(m.content)
            for m in db_session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation.id)
                .order_by(ConversationMessage.sequence)
            )
            if m.role == MessageRole.USER and "built no solid" in str(m.content)
        ]

    def test_a_turn_that_opens_documents_and_builds_nothing_is_told(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._answering(monkeypatch, {"catia_new_part": {"doc_name": "Part", "ok": True}})
        run_agent(
            db=db_session,
            provider=ScriptedProvider(self._opening(5)),
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="produce the frame, the ram and the table as parts",
            allow_mutations=True,
        )

        notes = self._notes(db_session, conversation)
        assert len(notes) == 1, notes
        # The remedy has to name the order the tools go in. "Build something"
        # produces another sketch; naming the pad produces a solid.
        assert "catia_pad" in notes[0]
        assert "Finish one document before opening another" in notes[0]

    def test_it_is_said_once_and_not_every_round(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same reasoning as MAX_VERIFICATION_NUDGES: a model that ignores this
        once will ignore it twice, and the rounds are better spent building."""
        self._answering(monkeypatch, {"catia_new_part": {"doc_name": "Part", "ok": True}})
        run_agent(
            db=db_session,
            provider=ScriptedProvider(self._opening(9)),
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="make all the parts",
            allow_mutations=True,
        )

        assert len(self._notes(db_session, conversation)) == 1

    def test_two_documents_are_not_nagged(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Opening a part and then a product to hold it is an ordinary opening
        move, and a guard that refused it would refuse every assembly."""
        self._answering(monkeypatch, {"catia_new_part": {"doc_name": "Part", "ok": True}})
        run_agent(
            db=db_session,
            provider=ScriptedProvider(self._opening(2)),
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="start a part",
            allow_mutations=True,
        )

        assert self._notes(db_session, conversation) == []

    def test_a_turn_that_actually_builds_is_never_nudged(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The regression that would matter most: a false positive here nags a
        model that is doing exactly the right thing, on every real build."""
        self._answering(
            monkeypatch,
            {
                "catia_new_part": {"doc_name": "Part", "ok": True},
                "catia_pad": {"feature": "Extrusion.1", "mass_kg": 0.3},
            },
        )
        turns = []
        for i in range(5):
            turns.append(
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id=f"n{i}", name="catia_new_part", arguments={"name": f"P{i}"})
                    ]
                )
            )
            turns.append(
                AssistantTurn(
                    tool_calls=[
                        ToolCall(id=f"p{i}", name="catia_pad", arguments={"sketch": "S", "length_mm": 10})
                    ]
                )
            )
        turns.append(AssistantTurn(text="Five parts, each with a solid."))

        run_agent(
            db=db_session,
            provider=ScriptedProvider(turns),
            toolbox=_toolbox(db_session, user, project),
            conversation=conversation,
            user_message="make five parts",
            allow_mutations=True,
        )

        assert self._notes(db_session, conversation) == []
