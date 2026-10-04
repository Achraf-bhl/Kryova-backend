"""Continue as a typed action (ROAD_TO_10 2.2, 2.4).

A turn that ran out of tool rounds used to leave the user to type "go on", which the
model read as a new request. What is worth testing is the ways a button could quietly
send the wrong thing: a client-chosen reason, a Continue under an answer that was already
continued, a Continue past a checkpoint that was waiting for a person, a stop the user
asked for being resumed behind their back -- and the transcript recording any of it as
the user's own words.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai import agent, continuation, prompts, turn_metrics
from app.ai.agent import stream_agent
from app.ai.provider import AssistantTurn, LLMError, LLMProvider, TokenUsage, ToolCall
from app.ai.taskgraph import Task, TaskGraph, TaskState
from app.ai.tools import ToolBox
from app.api.routes import ai as ai_routes
from app.core.security import hash_password
from app.models import (
    Conversation,
    ConversationMessage,
    Membership,
    MessageRole,
    Organisation,
    TurnMetric,
    User,
)
from app.models.organisation import OrgRole


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph.of(tasks)


PLAN = _graph(
    Task("t1", "Sketch the plate", state=TaskState.DONE),
    Task("t2", "Cut the mounting holes", depends_on=("t1",)),
    Task("t3", "Add the fillets", depends_on=("t2",)),
)


# -- which stops offer it ----------------------------------------------------


_STOPS = {
    name: value for name, value in vars(turn_metrics).items() if name.startswith("STOP_")
}

#: Every stop reason the loop or a route can record, and whether pressing Continue is the
#: right next step. A reason added to `turn_metrics` and not listed here fails
#: `test_every_stop_reason_has_been_decided`, so the choice is made rather than defaulted.
_DECIDED = {
    turn_metrics.STOP_FINISHED: False,
    turn_metrics.STOP_CANCELLED: False,
    turn_metrics.STOP_STEP_BUDGET: True,
    turn_metrics.STOP_REPEATED_CALLS: True,
    turn_metrics.STOP_NEEDS_INPUT: False,
    turn_metrics.STOP_AWAITING_APPROVAL: False,
    turn_metrics.STOP_ERROR: False,
    turn_metrics.STOP_DISCONNECTED: False,
    turn_metrics.STOP_TASK_BOUNDARY: True,
    turn_metrics.STOP_PROVIDER_BUSY: True,
}


class TestWhichStopsOfferContinue:
    def test_every_stop_reason_has_been_decided(self) -> None:
        assert set(_STOPS.values()) == set(_DECIDED), (
            "A stop reason was added or removed. Decide whether Continue is the right "
            "next step for it and say so in _DECIDED."
        )

    @pytest.mark.parametrize("reason", sorted(_DECIDED))
    def test_a_stop_offers_it_exactly_when_it_was_decided_it_should(self, reason: str) -> None:
        action = continuation.for_stop(reason, PLAN)
        assert (action is not None) is _DECIDED[reason]

    def test_a_question_waiting_on_a_person_is_not_answered_by_a_button(self) -> None:
        # needs_input and awaiting_approval already carry a typed Intervention. A bare
        # Continue would answer the question by ignoring it, and past an approval gate
        # it would walk through the checkpoint the gate exists for.
        for reason in (turn_metrics.STOP_NEEDS_INPUT, turn_metrics.STOP_AWAITING_APPROVAL):
            assert continuation.for_stop(reason, PLAN) is None

    def test_a_stop_the_user_asked_for_is_not_resumed_behind_their_back(self) -> None:
        assert continuation.for_stop(turn_metrics.STOP_CANCELLED, PLAN) is None

    def test_it_lists_what_is_open_and_names_the_next_task(self) -> None:
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, PLAN)

        assert action is not None
        assert [task.id for task in action.open_tasks] == ["t2", "t3"]
        assert "t2: Cut the mounting holes" in action.detail
        payload = action.to_dict()
        assert payload["kind"] == "continue" and payload["label"] == "Continue"
        assert payload["open_tasks"][0] == {
            "id": "t2", "title": "Cut the mounting holes", "state": "pending",
        }

    def test_it_works_with_no_plan_at_all(self) -> None:
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, TaskGraph())
        assert action is not None and action.open_tasks == ()

    def test_a_task_boundary_says_what_starts_next(self) -> None:
        action = continuation.for_stop(turn_metrics.STOP_TASK_BOUNDARY, PLAN)
        assert action is not None and action.detail.startswith("Starts the next task: t2")

    def test_a_blocked_task_is_listed_but_not_offered_as_the_next_one(self) -> None:
        blocked = _graph(
            Task("t1", "Wait for the supplier", state=TaskState.BLOCKED),
            Task("t2", "Order the stock", depends_on=("t1",)),
        )
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, blocked)
        assert action is not None
        assert [task.id for task in action.open_tasks] == ["t1", "t2"]
        assert "Next in the plan" not in action.detail


# -- what the model is told --------------------------------------------------


def _conversation_with(plan: TaskGraph | None) -> Conversation:
    row = Conversation(title="t")
    row.task_graph = plan.to_dict() if plan else None
    return row


class TestTheInstructionIsTheServers:
    def test_it_is_marked_so_the_transcript_can_tell_it_from_the_engineer(self) -> None:
        conversation = _conversation_with(PLAN)
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, PLAN)
        assert action is not None

        text = continuation.message_for(action, conversation)

        assert text.startswith(prompts.CONTINUATION_NOTE)
        assert text.startswith(prompts.CONTROL_NOTE)
        assert continuation.is_continuation(text)
        assert not continuation.is_continuation("continue please")

    def test_it_names_the_first_open_task_and_how_many_remain(self) -> None:
        conversation = _conversation_with(PLAN)
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, PLAN)
        assert action is not None

        text = continuation.message_for(action, conversation)

        assert "Carry on with t2: Cut the mounting holes" in text
        assert "2 task(s) of the plan are still open" in text

    def test_with_no_plan_it_tells_the_model_to_read_before_it_acts(self) -> None:
        conversation = _conversation_with(None)
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, TaskGraph())
        assert action is not None

        text = continuation.message_for(action, conversation)

        assert "do not redo work that is already recorded" in text

    def test_a_repeated_call_is_not_resent(self) -> None:
        conversation = _conversation_with(PLAN)
        action = continuation.for_stop(turn_metrics.STOP_REPEATED_CALLS, PLAN)
        assert action is not None

        assert "Do not send the call that was refused again" in continuation.message_for(
            action, conversation
        )

    def test_each_stop_tells_the_model_its_own_reason(self) -> None:
        # One sentence for every reason would tell the model the wrong thing about most.
        conversation = _conversation_with(PLAN)
        sentences = set()
        for reason in (
            turn_metrics.STOP_STEP_BUDGET,
            turn_metrics.STOP_REPEATED_CALLS,
            turn_metrics.STOP_TASK_BOUNDARY,
            turn_metrics.STOP_PROVIDER_BUSY,
        ):
            action = continuation.for_stop(reason, PLAN)
            assert action is not None
            sentences.add(continuation.message_for(action, conversation).split(". ")[1])
        assert len(sentences) == 4, sentences

    def test_a_task_title_cannot_carry_an_instruction_past_the_cap(self) -> None:
        long_title = "Cut holes " + "x" * 500
        plan = _graph(Task("t1", long_title))
        action = continuation.for_stop(turn_metrics.STOP_STEP_BUDGET, plan)
        assert action is not None
        text = continuation.message_for(action, _conversation_with(plan))
        assert len(text) < 700


# -- pending: the stored record decides --------------------------------------


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="cont@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


def _turn(user: User, conversation: Conversation, reason: str) -> TurnMetric:
    return TurnMetric(
        user_id=user.id,
        conversation_id=conversation.id,
        provider="scripted",
        model="m",
        stop_reason=reason,
    )


def _stopped_conversation(
    db: Session, user: User, reason: str | None, *, plan: TaskGraph | None = PLAN
) -> Conversation:
    row = Conversation(owner_id=user.id, title="t")
    row.task_graph = plan.to_dict() if plan else None
    db.add(row)
    db.flush()
    db.add(ConversationMessage(conversation_id=row.id, sequence=0, role=MessageRole.USER, content="build it"))
    db.add(ConversationMessage(conversation_id=row.id, sequence=1, role=MessageRole.ASSISTANT, content="partial"))
    if reason is not None:
        db.add(_turn(user, row, reason))
    db.flush()
    db.refresh(row)
    return row


class TestPendingReadsTheStoredTurn:
    def test_a_turn_that_ran_out_of_rounds_can_be_continued(
        self, db_session: Session, user: User
    ) -> None:
        conversation = _stopped_conversation(db_session, user, turn_metrics.STOP_STEP_BUDGET)
        action = continuation.pending(db_session, conversation)
        assert action is not None and action.reason == "step_budget"

    def test_a_finished_turn_cannot(self, db_session: Session, user: User) -> None:
        conversation = _stopped_conversation(db_session, user, turn_metrics.STOP_FINISHED)
        assert continuation.pending(db_session, conversation) is None

    def test_nothing_can_be_continued_once_something_was_said_since(
        self, db_session: Session, user: User
    ) -> None:
        # A Continue under an answer that has already been answered would resume work
        # that is already running.
        conversation = _stopped_conversation(db_session, user, turn_metrics.STOP_STEP_BUDGET)
        db_session.add(
            ConversationMessage(
                conversation_id=conversation.id, sequence=2, role=MessageRole.USER, content="no, wait"
            )
        )
        db_session.flush()
        db_session.refresh(conversation)

        assert continuation.pending(db_session, conversation) is None

    def test_only_the_newest_turn_counts(self, db_session: Session, user: User) -> None:
        conversation = _stopped_conversation(db_session, user, turn_metrics.STOP_STEP_BUDGET)
        db_session.add(
            _turn(user, conversation, "finished")
        )
        db_session.flush()

        assert continuation.pending(db_session, conversation) is None

    def test_a_conversation_with_no_turn_record_offers_nothing(
        self, db_session: Session, user: User
    ) -> None:
        conversation = _stopped_conversation(db_session, user, None)
        assert continuation.pending(db_session, conversation) is None

    def test_another_conversations_turn_is_not_read(self, db_session: Session, user: User) -> None:
        other = _stopped_conversation(db_session, user, turn_metrics.STOP_STEP_BUDGET)
        mine = _stopped_conversation(db_session, user, None)
        assert other.id != mine.id
        assert continuation.pending(db_session, mine) is None

    def test_a_plan_this_build_cannot_read_does_not_take_the_button_away(
        self, db_session: Session, user: User
    ) -> None:
        conversation = _stopped_conversation(db_session, user, turn_metrics.STOP_STEP_BUDGET, plan=None)
        conversation.task_graph = {"format_version": 99, "tasks": []}
        db_session.flush()

        action = continuation.pending(db_session, conversation)

        assert action is not None and action.open_tasks == ()


# -- 2.4: ending between tasks -----------------------------------------------


class TestEndingBetweenTasksOnPurpose:
    @pytest.mark.parametrize(
        ("rounds_used", "tasks_closed", "rounds_left", "pauses"),
        [
            # Two rounds a task, plenty left: carry on.
            (4, 2, 50, False),
            # The first task took 55 rounds and 5 remain: the next one will not fit.
            (55, 1, 5, True),
            # Never fewer rounds than the floor, however quick the first task was.
            (1, 1, continuation.MIN_ROUNDS_PER_TASK - 1, True),
            (1, 1, continuation.MIN_ROUNDS_PER_TASK, False),
            # Exactly enough at the measured rate is enough.
            (10, 2, 5, False),
            (10, 2, 4, True),
        ],
    )
    def test_it_pauses_only_when_the_next_task_would_not_fit(
        self, rounds_used: int, tasks_closed: int, rounds_left: int, pauses: bool
    ) -> None:
        assert (
            continuation.should_pause_at_boundary(
                PLAN, rounds_used=rounds_used, tasks_closed=tasks_closed, rounds_left=rounds_left
            )
            is pauses
        )

    def test_a_plan_with_nothing_ready_has_nothing_to_pause_for(self) -> None:
        finished = _graph(Task("t1", "Do it", state=TaskState.DONE))
        assert not continuation.should_pause_at_boundary(
            finished, rounds_used=50, tasks_closed=1, rounds_left=0
        )


# -- the loop: what a stopped turn offers, and when it chooses to stop ---------


class _Scripted(LLMProvider):
    """Plays the scripted turns, remembering the system prompt of every call."""

    name = "scripted"
    model = "m"

    def __init__(self, *turns: AssistantTurn) -> None:
        self.turns = list(turns)
        self.systems: list[str] = []

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("not used")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        self.systems.append(kwargs.get("system", ""))
        if not self.turns:
            return AssistantTurn(text="Done.", usage=TokenUsage(3, 4))
        return self.turns.pop(0)


def _call(call_id: str, name: str, **arguments: Any) -> AssistantTurn:
    return AssistantTurn(
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
        usage=TokenUsage(10, 5),
    )


_THREE_TASKS = [
    {"id": "t1", "title": "Sketch the plate"},
    {"id": "t2", "title": "Cut the mounting holes", "depends_on": ["t1"]},
    {"id": "t3", "title": "Add the fillets", "depends_on": ["t2"]},
]


def _run(
    db: Session, user: User, conversation: Conversation, provider: LLMProvider
) -> list[dict[str, Any]]:
    return list(
        stream_agent(
            db=db,
            provider=provider,
            conversation=conversation,
            toolbox=ToolBox(db=db, user=user, conversation=conversation),
            user_message="build the bracket",
            user=user,
            allow_mutations=True,
        )
    )


def _done(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(event for event in events if event["type"] == "done")


@pytest.fixture
def conversation(db_session: Session, user: User) -> Conversation:
    row = Conversation(owner_id=user.id, title="t")
    db_session.add(row)
    db_session.flush()
    return row


def _plan_then_close_two(extra_read: bool = True) -> list[AssistantTurn]:
    """Plan (1), close t1 (2), start t2 (3), read (4), close t2 (5)."""
    turns = [
        _call("c1", "plan_work", tasks=_THREE_TASKS),
        _call("c2", "update_task", id="t1", state="done"),
        _call("c3", "update_task", id="t2", state="active"),
    ]
    if extra_read:
        turns.append(_call("c4", "list_projects"))
    turns.append(_call("c5", "update_task", id="t2", state="done"))
    return turns


class TestADoneEventAlwaysSaysWhatContinueDoes:
    def test_a_finished_turn_offers_nothing_but_says_so(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        events = _run(db_session, user, conversation, _Scripted(AssistantTurn(text="All done.")))

        done = _done(events)
        assert done["stop_reason"] == "finished"
        # Present, and null: one shape from every exit.
        assert "next_action" in done and done["next_action"] is None

    def test_a_turn_that_runs_out_of_rounds_offers_continue_with_the_open_tasks(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_MAX_STEPS", "3")
        # Nothing closes, so there is no boundary to stop at and the budget is what ends it.
        provider = _Scripted(
            _call("c1", "plan_work", tasks=_THREE_TASKS),
            _call("c2", "list_projects", page=1),
            _call("c3", "list_projects", page=2),
        )

        done = _done(_run(db_session, user, conversation, provider))

        assert done["stop_reason"] == "step_budget"
        action = done["next_action"]
        assert action["kind"] == "continue" and action["reason"] == "step_budget"
        assert [task["id"] for task in action["open_tasks"]] == ["t1", "t2", "t3"]
        assert "t1: Sketch the plate" in action["detail"]

    def test_the_closing_call_of_such_a_turn_does_not_ask_the_user_to_retype_it(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_MAX_STEPS", "1")
        provider = _Scripted(_call("c1", "list_projects"))

        _run(db_session, user, conversation, provider)

        assert provider.systems[-1].endswith(prompts.AGENT_OUT_OF_STEPS)
        assert "press Continue" in prompts.AGENT_OUT_OF_STEPS

    def test_a_stop_for_a_decision_offers_no_button(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        # An approval gate ends the turn; Continue would walk through the checkpoint.
        organisation = Organisation(name="Gate Co", slug="gate-co", is_personal=False)
        db_session.add(organisation)
        db_session.flush()
        db_session.add(
            Membership(organisation_id=organisation.id, user_id=user.id, role=OrgRole.MEMBER)
        )
        db_session.flush()
        provider = _Scripted(
            _call("c1", "plan_work", tasks=[{"id": "g", "title": "Sign off", "checkpoint": True}]),
            _call(
                "c2", "request_approval", title="Release", question="Release the drawing?",
                task_id="g",
            ),
        )

        done = _done(_run(db_session, user, conversation, provider))

        assert done["stop_reason"] == "awaiting_approval"
        assert done["next_action"] is None


class TestTheLoopEndsBetweenTasksWhenTheNextWouldNotFit:
    def test_it_stops_at_the_boundary_with_a_report_and_a_continue(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Seven rounds. Five are spent when t2 closes, two tasks done at about three rounds
        # each: t3 would need three and two are left, so ending here beats ending mid-task.
        monkeypatch.setenv("AI_MAX_STEPS", "7")
        provider = _Scripted(*_plan_then_close_two())

        events = _run(db_session, user, conversation, provider)

        done = _done(events)
        assert done["stop_reason"] == "task_boundary"
        assert done["steps"] == 5
        action = done["next_action"]
        assert action["reason"] == "task_boundary"
        assert [task["id"] for task in action["open_tasks"]] == ["t3"]
        assert action["detail"].startswith("Starts the next task: t3")

    def test_the_closing_call_is_a_progress_report_not_a_cry_for_help(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_MAX_STEPS", "7")
        provider = _Scripted(*_plan_then_close_two())

        _run(db_session, user, conversation, provider)

        closing = provider.systems[-1]
        assert closing.endswith(prompts.AGENT_TASK_BOUNDARY)
        assert prompts.AGENT_ENDED_EARLY not in closing
        assert prompts.AGENT_OUT_OF_STEPS not in closing

    def test_with_rounds_to_spare_it_carries_on_into_the_next_task(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_MAX_STEPS", "30")
        provider = _Scripted(*_plan_then_close_two(), AssistantTurn(text="Both done, one left."))

        done = _done(_run(db_session, user, conversation, provider))

        assert done["stop_reason"] == "finished"
        assert done["next_action"] is None

    def test_a_task_settled_before_this_turn_does_not_count_against_it(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # t1 was closed in an earlier turn. Nothing is closed in this one, so there is no
        # boundary to stop at and the loop runs on to its own end.
        conversation.task_graph = TaskGraph.of(
            [Task("t1", "Sketch", state=TaskState.DONE), Task("t2", "Holes")]
        ).to_dict()
        db_session.flush()
        monkeypatch.setenv("AI_MAX_STEPS", "3")
        provider = _Scripted(_call("c1", "list_projects"), AssistantTurn(text="Looked."))

        done = _done(_run(db_session, user, conversation, provider))

        assert done["stop_reason"] == "finished"

    def test_the_boundary_is_recorded_as_the_turns_stop_reason(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_MAX_STEPS", "7")
        meter = turn_metrics.TurnMeter()
        list(
            stream_agent(
                db=db_session,
                provider=_Scripted(*_plan_then_close_two()),
                conversation=conversation,
                toolbox=ToolBox(db=db_session, user=user, conversation=conversation),
                user_message="build the bracket",
                user=user,
                allow_mutations=True,
                meter=meter,
            )
        )

        assert meter.stop_reason == turn_metrics.STOP_TASK_BOUNDARY

    def test_every_ending_has_a_fallback_sentence_for_a_failed_closing_call(self) -> None:
        for reason in ("step_budget", "task_boundary"):
            assert reason in agent._CLOSING_FALLBACK
        assert "Continue" in agent._CLOSING_FALLBACK["task_boundary"]
        assert "Nothing went wrong" in agent._CLOSING_FALLBACK["task_boundary"]


# -- the routes -----------------------------------------------------------------


def _stream(auth_client: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    response = auth_client.post("/api/v1/ai/chat/stream", json=payload)
    assert response.status_code == 200, response.text
    events = []
    for block in response.text.split("\n\n"):
        line = next((ln for ln in block.split("\n") if ln.startswith("data:")), None)
        if line:
            events.append(json.loads(line[5:].strip()))
    return events


class _RouteProvider(LLMProvider):
    """Calls one read-only tool per round, forever; settles when its tools are withdrawn."""

    name = "route-stub"
    model = "m"

    def __init__(self) -> None:
        self.users: list[str] = []
        self.rounds = 0
        self.closings = 0

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("not used")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        messages = kwargs.get("messages", [])
        self.users.append(
            "\n".join(str(m.get("content")) for m in messages if m.get("role") == "user")
        )
        if not kwargs.get("tools"):
            # Different every time, so a title asked for twice would come out differently.
            self.closings += 1
            return AssistantTurn(text=f"Partway there {self.closings}.", usage=TokenUsage(3, 4))
        self.rounds += 1
        return AssistantTurn(
            tool_calls=[
                ToolCall(
                    id=f"r{self.rounds}-{len(self.users)}",
                    name="list_projects",
                    arguments={"page": self.rounds},
                )
            ],
            usage=TokenUsage(3, 4),
        )


@pytest.fixture
def stopped_early(
    auth_client: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, str, _RouteProvider]:
    """A conversation whose first turn ran out of rounds, with the stub that did it."""
    provider = _RouteProvider()
    monkeypatch.setattr(ai_routes, "get_provider", lambda: provider)
    monkeypatch.setenv("AI_MAX_STEPS", "2")
    events = _stream(auth_client, {"message": "build the bracket"})
    done = next(event for event in events if event["type"] == "done")
    assert done["stop_reason"] == "step_budget", done
    return auth_client, done["conversation_id"], provider


class TestTheRequestShape:
    def test_a_continuation_carries_no_text(self, auth_client: Any) -> None:
        response = auth_client.post(
            "/api/v1/ai/chat/stream",
            json={"continuation": "continue", "conversation_id": "x", "message": "go on"},
        )
        assert response.status_code == 422
        assert "carries no message" in response.text

    def test_a_continuation_needs_a_conversation(self, auth_client: Any) -> None:
        response = auth_client.post("/api/v1/ai/chat/stream", json={"continuation": "continue"})
        assert response.status_code == 422
        assert "conversation_id" in response.text

    def test_a_request_with_neither_is_refused(self, auth_client: Any) -> None:
        response = auth_client.post("/api/v1/ai/chat/stream", json={})
        assert response.status_code == 422

    def test_only_one_kind_of_continuation_exists(self, auth_client: Any) -> None:
        response = auth_client.post(
            "/api/v1/ai/chat/stream", json={"continuation": "retry", "conversation_id": "x"}
        )
        assert response.status_code == 422

    def test_an_ordinary_message_is_unchanged(
        self, auth_client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ai_routes, "get_provider", lambda: _RouteProvider())
        monkeypatch.setenv("AI_MAX_STEPS", "1")
        events = _stream(auth_client, {"message": "hello"})
        assert next(e for e in events if e["type"] == "done")["conversation_id"]


class TestPressingContinue:
    def test_the_conversation_says_what_continue_would_do_so_a_reload_keeps_the_button(
        self, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        client, conversation_id, _ = stopped_early

        detail = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()

        assert detail["next_action"]["kind"] == "continue"
        assert detail["next_action"]["reason"] == "step_budget"

    def test_pressing_it_runs_a_turn_and_stores_the_servers_instruction_marked(
        self, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        client, conversation_id, provider = stopped_early

        events = _stream(client, {"continuation": "continue", "conversation_id": conversation_id})

        assert any(event["type"] == "done" for event in events)
        # The model was handed the server's own sentence...
        assert prompts.CONTINUATION_NOTE in provider.users[-1]
        assert "The user pressed Continue and typed nothing" in provider.users[-1]
        # ...and the transcript records an act, not prose in the user's voice.
        messages = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["messages"]
        marked = [m for m in messages if m["continuation"]]
        assert len(marked) == 1 and marked[0]["role"] == "user"
        assert [m for m in messages if m["role"] == "user" and not m["continuation"]][0][
            "content"
        ] == "build the bracket"

    def test_the_button_is_gone_once_it_has_been_pressed_and_answered(
        self, stopped_early: tuple[Any, str, _RouteProvider], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, conversation_id, _ = stopped_early
        monkeypatch.setenv("AI_MAX_STEPS", "50")
        silent = _RouteProvider()
        silent.chat = lambda *a, **k: AssistantTurn(text="Finished.", usage=TokenUsage(1, 1))  # type: ignore[method-assign]
        monkeypatch.setattr(ai_routes, "get_provider", lambda: silent)

        _stream(client, {"continuation": "continue", "conversation_id": conversation_id})

        detail = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()
        assert detail["next_action"] is None

    def test_a_second_press_under_an_answer_already_continued_is_refused(
        self, stopped_early: tuple[Any, str, _RouteProvider], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, conversation_id, _ = stopped_early
        monkeypatch.setenv("AI_MAX_STEPS", "50")
        finisher = _RouteProvider()
        finisher.chat = lambda *a, **k: AssistantTurn(text="Finished.", usage=TokenUsage(1, 1))  # type: ignore[method-assign]
        monkeypatch.setattr(ai_routes, "get_provider", lambda: finisher)
        _stream(client, {"continuation": "continue", "conversation_id": conversation_id})

        again = client.post(
            "/api/v1/ai/chat/stream",
            json={"continuation": "continue", "conversation_id": conversation_id},
        )

        assert again.status_code == 409
        assert "nothing to continue" in again.text

    def test_a_conversation_that_finished_cannot_be_continued(
        self, auth_client: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _RouteProvider()
        provider.chat = lambda *a, **k: AssistantTurn(text="Done.", usage=TokenUsage(1, 1))  # type: ignore[method-assign]
        monkeypatch.setattr(ai_routes, "get_provider", lambda: provider)
        events = _stream(auth_client, {"message": "what is aluminium's density"})
        conversation_id = next(e for e in events if e["type"] == "done")["conversation_id"]

        response = auth_client.post(
            "/api/v1/ai/chat/stream",
            json={"continuation": "continue", "conversation_id": conversation_id},
        )

        assert response.status_code == 409

    def test_the_non_streaming_route_honours_it_too(
        self, stopped_early: tuple[Any, str, _RouteProvider], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, conversation_id, provider = stopped_early

        response = client.post(
            "/api/v1/ai/chat",
            json={"continuation": "continue", "conversation_id": conversation_id},
        )

        assert response.status_code == 200, response.text
        assert prompts.CONTINUATION_NOTE in provider.users[-1]

    def test_someone_elses_conversation_is_a_404_not_a_409(
        self, auth_client: Any, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        response = auth_client.post(
            "/api/v1/ai/chat/stream",
            json={"continuation": "continue", "conversation_id": "no-such-conversation"},
        )
        assert response.status_code == 404

    def test_pressing_it_does_not_rename_the_conversation(
        self, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        # A Continue has no words of the user's to name a conversation from. Before this
        # was guarded, a press inside the first dozen messages re-titled the chat from
        # the server's own instruction.
        client, conversation_id, _ = stopped_early
        before = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["title"]

        _stream(client, {"continuation": "continue", "conversation_id": conversation_id})

        after = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["title"]
        assert after == before
        assert "kryova" not in after.lower()

    def test_the_original_request_is_still_what_the_model_reads_after_a_press(
        self, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        # The window keeps the engineer's request in view by skipping the server's own
        # notes (`context._is_the_question`); the continuation is one, because its marker
        # starts with `CONTROL_NOTE` (pinned above). The window itself is pinned by
        # `test_ai_context`; this is that rule seen from the route.
        client, conversation_id, provider = stopped_early
        _stream(client, {"continuation": "continue", "conversation_id": conversation_id})

        assert "build the bracket" in provider.users[-1]


class TestAReturningUserIsToldFromTheRecord:
    """2.3: the resume block carries the plan and the design as the server stored them."""

    def test_a_conversation_with_neither_says_null_and_not_zero(
        self, stopped_early: tuple[Any, str, _RouteProvider]
    ) -> None:
        client, conversation_id, _ = stopped_early

        resume = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["resume"]

        assert resume["plan"] is None and resume["design"] is None

    def test_an_open_plan_is_listed_with_what_is_next(
        self, stopped_early: tuple[Any, str, _RouteProvider], db_session: Session
    ) -> None:
        client, conversation_id, _ = stopped_early
        row = db_session.get(Conversation, conversation_id)
        assert row is not None
        row.task_graph = PLAN.to_dict()
        db_session.flush()

        plan = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["resume"]["plan"]

        assert (plan["total"], plan["settled"]) == (3, 1)
        assert [task["id"] for task in plan["open"]] == ["t2", "t3"]
        assert plan["next"] == {"id": "t2", "title": "Cut the mounting holes"}

    def test_a_recorded_design_is_named_with_its_revision_and_parameter_count(
        self, stopped_early: tuple[Any, str, _RouteProvider], db_session: Session
    ) -> None:
        from app.core import designs
        from tests.test_design_compile import bracket

        client, conversation_id, _ = stopped_early
        row = db_session.get(Conversation, conversation_id)
        assert row is not None
        document = designs.save(db_session, row, bracket()).document
        designs.set_parameter(db_session, document, "thick_mm", 10.0)

        design = client.get(f"/api/v1/ai/conversations/{conversation_id}").json()["resume"][
            "design"
        ]

        assert design == {"name": "Bracket", "revision": 2, "parameters": 5}

    def test_a_design_this_build_cannot_read_does_not_fail_the_transcript(
        self, stopped_early: tuple[Any, str, _RouteProvider], db_session: Session
    ) -> None:
        from app.core import designs
        from tests.test_design_compile import bracket

        client, conversation_id, _ = stopped_early
        row = db_session.get(Conversation, conversation_id)
        assert row is not None
        document = designs.save(db_session, row, bracket()).document
        document.document = {"format_version": 9_999}
        db_session.flush()

        response = client.get(f"/api/v1/ai/conversations/{conversation_id}")

        assert response.status_code == 200
        assert response.json()["resume"]["design"] is None
