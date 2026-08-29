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
from app.ai.tools import ToolBox, ToolError
from app.core.config import settings
from app.jobs import InlineJobQueue
from app.models import (
    Conversation,
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
        """The loop must terminate even against a model stuck in a tool loop."""
        budget = max_steps()
        looping = [
            AssistantTurn(tool_calls=[ToolCall(id=f"c{i}", name="list_projects", arguments={})])
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
        provider = ScriptedProvider(
            [
                AssistantTurn(tool_calls=[ToolCall(id=f"c{i}", name="list_projects", arguments={})])
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
