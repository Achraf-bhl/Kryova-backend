"""The agent's plan, checkpoints and pre-run estimate, through the tools (E16 2/5/6).

`test_taskgraph.py` covers the graph itself. This is about what the *agent* can
and cannot do with it: that the server refuses an out-of-order move rather than
trusting the model to keep the order, that a checkpoint really ends the turn
rather than being announced and walked past, and that an estimate we do not have
comes back as "not enough history" rather than as a small number.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai.taskgraph import TaskGraph, TaskState
from app.ai.tools import ToolBox, ToolError
from app.models import ApprovalGate, Conversation, GateState, Membership, Organisation, User
from app.models.organisation import OrgRole

PLAN = [
    {"id": "frame", "title": "Build the C-frame"},
    {"id": "sizing", "title": "Work out the punching force", "checkpoint": True},
    {"id": "ram", "title": "Build the ram", "depends_on": ["frame", "sizing"]},
]


@pytest.fixture
def organisation(db_session: Session) -> Organisation:
    org = Organisation(name="Press Co", slug="press-co", is_personal=False)
    db_session.add(org)
    db_session.flush()
    return org


@pytest.fixture
def engineer(db_session: Session, organisation: Organisation) -> User:
    user = User(email="planner@kryova.dev", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        Membership(organisation_id=organisation.id, user_id=user.id, role=OrgRole.MEMBER)
    )
    db_session.flush()
    return user


@pytest.fixture
def conversation(db_session: Session, engineer: User) -> Conversation:
    row = Conversation(title="Punch press", owner_id=engineer.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def toolbox(db_session: Session, engineer: User, conversation: Conversation) -> ToolBox:
    return ToolBox(db=db_session, user=engineer, conversation=conversation)


class TestDeclaringAPlan:
    def test_the_plan_is_stored_on_the_conversation_and_says_what_is_ready(
        self, toolbox: ToolBox, conversation: Conversation
    ) -> None:
        result = toolbox._plan_work(PLAN)

        assert result["tasks"] == 3
        assert result["ready"] == ["frame", "sizing"]
        assert result["checkpoints"] == ["sizing"]
        assert conversation.task_graph is not None

    def test_a_cycle_comes_back_as_a_tool_error_naming_the_path(
        self, toolbox: ToolBox
    ) -> None:
        with pytest.raises(ToolError) as refused:
            toolbox._plan_work(
                [
                    {"id": "a", "title": "A", "depends_on": ["b"]},
                    {"id": "b", "title": "B", "depends_on": ["a"]},
                ]
            )

        assert "->" in str(refused.value)

    def test_planning_again_replaces_rather_than_merges(
        self, toolbox: ToolBox, conversation: Conversation
    ) -> None:
        """Whole-graph on purpose. A merge needs a rule for "the model omitted a
        task" and both readings are defensible, so whichever was chosen would be
        wrong half the time and silently."""
        toolbox._plan_work(PLAN)

        toolbox._plan_work([{"id": "only", "title": "One step"}])

        assert TaskGraph.from_dict(conversation.task_graph).order()[0].id == "only"
        assert len(TaskGraph.from_dict(conversation.task_graph)) == 1


class TestMovingATask:
    def test_the_server_refuses_an_out_of_order_finish(self, toolbox: ToolBox) -> None:
        """The one thing this adds over a list. Without it the model ticks off
        whatever it likes and the plan is a list that looks like a graph."""
        toolbox._plan_work(PLAN)

        with pytest.raises(ToolError) as refused:
            toolbox._update_task("ram", "done")

        assert "frame" in str(refused.value)
        assert "sizing" in str(refused.value)

    def test_a_legal_move_reports_what_is_ready_next(self, toolbox: ToolBox) -> None:
        toolbox._plan_work(PLAN)

        result = toolbox._update_task("frame", "done")

        assert result["ready"] == ["sizing"]
        assert result["settled"] == "1 of 3"

    def test_an_open_checkpoint_is_named_in_the_result(self, toolbox: ToolBox) -> None:
        toolbox._plan_work(PLAN)

        result = toolbox._update_task("frame", "done")

        assert result["awaiting_sign_off"] == ["sizing"]

    def test_a_stuck_plan_says_so_rather_than_returning_an_empty_list(
        self, toolbox: ToolBox
    ) -> None:
        """A model handed `[]` reads it as "nothing to do" and closes the turn
        reporting success."""
        toolbox._plan_work(PLAN)
        toolbox._update_task("frame", "blocked", note="no seat")

        result = toolbox._update_task("sizing", "blocked", note="waiting on the customer")

        assert "Nothing is ready" in result["note"]

    def test_updating_with_no_plan_says_to_write_one(self, toolbox: ToolBox) -> None:
        with pytest.raises(ToolError, match="plan_work first"):
            toolbox._update_task("frame", "done")

    def test_a_completed_plan_says_so(self, toolbox: ToolBox) -> None:
        toolbox._plan_work([{"id": "only", "title": "One step"}])

        result = toolbox._update_task("only", "done")

        assert "Every step" in result["note"]


class TestRequestingApproval:
    def test_it_raises_a_pending_gate_and_asks_the_loop_to_stop(
        self, toolbox: ToolBox, db_session: Session, conversation: Conversation
    ) -> None:
        """Ending the turn is the point rather than a side effect: a checkpoint
        the agent announces and then walks past is not a checkpoint."""
        toolbox._plan_work(PLAN)

        result = toolbox._request_approval(
            "Punching force", "70 kN or 90 kN — which do you want to size for?", task_id="sizing"
        )

        assert result["awaiting_approval"] is True
        gate = db_session.get(ApprovalGate, result["gate_id"])
        assert gate is not None
        assert gate.state is GateState.PENDING
        assert gate.conversation_id == conversation.id

    def test_a_gate_with_no_design_yet_is_pinned_to_the_plan(
        self, toolbox: ToolBox, db_session: Session
    ) -> None:
        toolbox._plan_work(PLAN)

        result = toolbox._request_approval("Approach", "Weldment or casting?")

        assert result["pinned_to"] == "plan"
        gate = db_session.get(ApprovalGate, result["gate_id"])
        assert gate is not None and gate.subject_digest

    def test_a_gate_is_pinned_to_the_design_once_there_is_one(
        self, toolbox: ToolBox, db_session: Session, conversation: Conversation
    ) -> None:
        """What makes the eventual approval mean something: `decide` re-digests
        what the decider is looking at, so an approval cannot land on a design
        that moved while the gate was open."""
        from app.core import designs
        from tests.test_design_compile import bracket

        designs.save(db_session, conversation, bracket())

        result = toolbox._request_approval("Wall thickness", "8 mm or 12 mm?")

        gate = db_session.get(ApprovalGate, result["gate_id"])
        assert result["pinned_to"] == "design"
        assert gate is not None and gate.evidence["design"] == "Bracket"

    def test_an_account_with_no_organisation_says_there_is_nobody_to_ask(
        self, db_session: Session
    ) -> None:
        loner = User(email="loner@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(loner)
        db_session.flush()
        conversation = Conversation(title="Solo", owner_id=loner.id)
        db_session.add(conversation)
        db_session.flush()
        toolbox = ToolBox(db=db_session, user=loner, conversation=conversation)

        with pytest.raises(ToolError, match="nobody to ask"):
            toolbox._request_approval("Anything", "Yes or no?")


class TestEstimatingCost:
    def test_an_account_with_no_history_is_told_so_rather_than_given_a_number(
        self, toolbox: ToolBox
    ) -> None:
        """A cost estimate is the one number in a product nobody contradicts
        afterwards, which is exactly why a made-up one survives."""
        result = toolbox._estimate_cost()

        assert result["estimates"]
        for estimate in result["estimates"]:
            assert estimate["known"] is False
            assert "cannot estimate" in estimate["sentence"]

    def test_the_sentence_comes_from_the_meter_rather_than_being_assembled(
        self, toolbox: ToolBox
    ) -> None:
        """P8.4's one-meter rule reaching the agent: assembling our own from
        `units` and `unit` would be a second place for the wording to drift."""
        result = toolbox._estimate_cost()

        for estimate in result["estimates"]:
            assert estimate["sentence"]
            assert "meter" in estimate

    def test_an_account_with_no_organisation_says_so_without_raising(
        self, db_session: Session
    ) -> None:
        loner = User(email="nobody@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(loner)
        db_session.flush()
        conversation = Conversation(title="Solo", owner_id=loner.id)
        db_session.add(conversation)
        db_session.flush()
        toolbox = ToolBox(db=db_session, user=loner, conversation=conversation)

        result = toolbox._estimate_cost()

        assert result["estimates"] == []
        assert "rather than guessing" in result["note"]


class TestTheStateBlock:
    def test_the_plan_is_beside_the_users_message_every_turn(
        self, db_session: Session, engineer: User, conversation: Conversation, toolbox: ToolBox
    ) -> None:
        """The prompt says the right thing and is read once, at the top of a
        window being trimmed from the front. This is beside the message."""
        from app.ai.state import build_state_block

        toolbox._plan_work(PLAN)

        block = build_state_block(db_session, engineer, conversation)

        assert "Build the C-frame" in block
        assert "needs sign-off" in block

    def test_the_design_parameters_are_in_the_block_so_they_are_not_asked_for_again(
        self, db_session: Session, engineer: User, conversation: Conversation
    ) -> None:
        """A model asking the user what the wall thickness is, three turns after
        setting it, is the failure this prevents."""
        from app.ai.state import build_state_block
        from app.core import designs
        from tests.test_design_compile import bracket

        designs.save(db_session, conversation, bracket())

        block = build_state_block(db_session, engineer, conversation)

        assert "thick_mm=8" in block
        assert "Do not ask the user" in block

    def test_a_conversation_with_no_plan_gets_no_plan_lines(
        self, db_session: Session, engineer: User, conversation: Conversation
    ) -> None:
        # A block saying "no plan" every turn is furniture in the one place
        # furniture is most expensive.
        from app.ai.state import build_state_block

        assert "The plan (" not in build_state_block(db_session, engineer, conversation)

    def test_the_four_new_tools_all_carry_step_labels(self) -> None:
        from app.ai.tools import BUILTIN_TOOL_LABELS

        for name in ("plan_work", "update_task", "request_approval", "estimate_cost"):
            assert name in BUILTIN_TOOL_LABELS


def test_the_states_the_tool_offers_are_exactly_the_ones_the_graph_knows(
    toolbox: ToolBox,
) -> None:
    """A schema offering a state the graph refuses teaches a model to make a
    call that always fails; one withholding a state the graph accepts hides a
    move it needs. Read off the live schema rather than restated, so the two
    cannot drift apart silently."""
    schema = next(
        entry["function"]["parameters"]
        for entry in toolbox.schemas(include_mutating=True)
        if entry["function"]["name"] == "update_task"
    )

    assert set(schema["properties"]["state"]["enum"]) == {state.value for state in TaskState}
