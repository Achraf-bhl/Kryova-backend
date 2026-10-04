"""ROAD_TO_10 1.13: one tool call carries the whole recorded design.

`record_design` has always said *"Recording a design does NOT build it"*, and `execute_plan` has
always been a library function nobody in `app/` called, so a part the model had already written
down was still built one `catia_*` step at a time -- and every step resends the whole transcript.
`build_design` is the missing seam: compile the recorded design and run its plan through
`_call_catia`, the path every other geometry tool takes.

These go through the `ToolBox` and through `run_agent`, not through a runner, because
*Testing* item 8 of CLAUDE.md is the reason this file exists: a test that calls the kernel directly
proves the operations and says nothing about whether the agent is offered a way to use them.

Offline: the kernel half needs OCCT (skipped without it). The seat is not driven here -- whether a
twenty-feature build survives a real CATIA's per-call latency and a stuck dialog is THE QUEUE H13.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.agent import run_agent, summarise_step
from app.ai.provider import AssistantTurn, LLMProvider, TokenUsage, ToolCall
from app.ai.tools import BUILTIN_TOOL_LABELS, ToolBox, ToolError
from app.core.config import settings
from app.geometry import backends
from app.models import CatiaOperation, Conversation, Project, User


def _kernel_available() -> bool:
    try:
        from app.kernel.occt.binding import require

        require()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_kernel = pytest.mark.skipif(not _kernel_available(), reason="OCCT not installed")

#: A 60 x 40 plate, 8 mm thick. Volume is exact, so the assertion is a closed form.
PLATE_VOLUME_MM3 = 60.0 * 40.0 * 8.0


@pytest.fixture(autouse=True)
def _clean_sessions() -> Any:
    """Kernel sessions are process-global by necessity; a test must not inherit one."""
    for key in list(backends._sessions):
        backends.forget(key)
    yield
    for key in list(backends._sessions):
        backends.forget(key)


@pytest.fixture
def occt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "geometry_backend", "occt")


@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="builder@kryova.dev", hashed_password=hash_password("a-long-enough-password")
    )
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def conversation(db_session: Session, user: User) -> Conversation:
    project = Project(name="Plate", owner_id=user.id)
    db_session.add(project)
    db_session.flush()
    row = Conversation(owner_id=user.id, project_id=project.id, title="plate")
    db_session.add(row)
    # Committed, not flushed: the seat-seconds meter writes through its own connection and
    # would otherwise name a conversation that connection cannot see.
    db_session.commit()
    return row


@pytest.fixture
def box(db_session: Session, user: User, conversation: Conversation) -> ToolBox:
    return ToolBox(db=db_session, user=user, conversation=conversation)


def plate(thickness: float = 8.0) -> dict[str, Any]:
    """`record_design`'s arguments for the plate, written the way the model writes them."""
    return {
        "name": "Plate",
        "parameters": [{"name": "thick_mm", "unit": "mm", "value": thickness}],
        "features": [
            {"name": "plate.outline", "op": "catia_sketch_create", "args": {"support": "XY"}},
            {
                "name": "plate.profile",
                "op": "catia_sketch_rectangle",
                "args": {"sketch": "@plate.outline", "width_mm": 60, "height_mm": 40},
            },
            {
                "name": "plate.body",
                "op": "catia_pad",
                "args": {"sketch": "@plate.outline", "length_mm": "=thick_mm"},
            },
        ],
    }


#: Compiles (the compiler only checks the call is well-formed) and the kernel refuses it: a 50 mm
#: radius on an 8 mm plate. `length_mm = 0` would not do -- the compiler already refuses that.
TOO_BIG_A_FILLET: dict[str, Any] = {
    "name": "plate.round",
    "op": "catia_fillet",
    "args": {"radius_mm": 50.0},
}


def a_plate_whose_last_feature_fails() -> dict[str, Any]:
    return {"features": [*plate()["features"], TOO_BIG_A_FILLET]}


def record(box: ToolBox, **overrides: Any) -> dict[str, Any]:
    result = box.call("record_design", {**plate(), **overrides}, allow_mutations=True)
    assert isinstance(result, dict)
    return result


def planned_calls(box: ToolBox) -> int:
    """How many calls the recorded design compiles to -- derived, never typed."""
    from app.core import designs
    from app.design.compile import compile_spec

    assert box.conversation is not None
    document = designs.load(box.db, box.conversation)
    assert document is not None
    return len(compile_spec(designs.spec_of(document)).calls)


class TestTheWholeDesignIsOneCall:
    @needs_kernel
    def test_a_recorded_design_is_built_by_one_call(self, occt: None, box: ToolBox) -> None:
        record(box)

        result = box.call("build_design", {}, allow_mutations=True)

        assert result["calls"] == planned_calls(box)
        assert result["calls"] > 3  # several features, one step: that is the whole saving
        assert result["features_built"] == ["plate.outline", "plate.profile", "plate.body"]
        # The finished part is the plate the design describes, to the closed form.
        assert result["final"]["volume_mm3"] == pytest.approx(PLATE_VOLUME_MM3, rel=1e-9)
        assert result["final"]["solid_count"] == 1
        # What each feature is called *now* -- after the rename the plan makes, not the `Pad.1`
        # the kernel first gave it -- because the model's next call has to name one.
        assert result["created"]["plate.body"] == "plate_body"
        assert result["created"]["plate.outline"] == "plate_outline"
        assert result["feature"] == "plate_body"

    @needs_kernel
    def test_a_changed_parameter_is_what_gets_built(self, occt: None, box: ToolBox) -> None:
        """It builds the design *as recorded now*, not the first one it saw."""
        record(box)
        box.call("set_design_parameter", {"name": "thick_mm", "value": 12.0}, allow_mutations=True)

        result = box.call("build_design", {}, allow_mutations=True)

        assert result["final"]["volume_mm3"] == pytest.approx(60.0 * 40.0 * 12.0, rel=1e-9)

    @needs_kernel
    def test_every_call_it_makes_is_logged_so_a_later_turn_can_see_the_part(
        self, occt: None, box: ToolBox, db_session: Session, conversation: Conversation
    ) -> None:
        """`resume.py` reads `CatiaOperation`, not the transcript. A build that left no rows
        would be a part the next turn's state block could not account for."""
        record(box)
        box.call("build_design", {}, allow_mutations=True)

        logged = db_session.scalar(
            select(func.count())
            .select_from(CatiaOperation)
            .where(CatiaOperation.conversation_id == conversation.id)
        )
        assert logged == planned_calls(box)

    @needs_kernel
    def test_the_agent_builds_a_part_in_two_model_steps_not_one_per_feature(
        self, occt: None, box: ToolBox, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        """Through the loop the product runs, with a scripted model: record, build, answer."""
        calls: list[AssistantTurn] = [
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="c1", name="record_design", arguments=plate())],
                usage=TokenUsage(100, 10, 0),
            ),
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="c2", name="build_design", arguments={})],
                usage=TokenUsage(100, 10, 0),
            ),
            AssistantTurn(text="The plate is built.", usage=TokenUsage(100, 10, 0)),
        ]
        provider = _Scripted(calls)

        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=box,
            user_message="Make a 60 by 40 plate, 8 mm thick.",
            user=user,
            allow_mutations=True,
        )

        assert [step.tool for step in reply.steps] == ["record_design", "build_design"]
        assert all(step.ok for step in reply.steps)
        assert provider.model_calls == 3
        assert planned_calls(box) > provider.model_calls  # by hand this is one step per call
        built = reply.steps[1].result
        assert built["final"]["volume_mm3"] == pytest.approx(PLATE_VOLUME_MM3, rel=1e-9)

    def test_it_is_offered_to_the_agent(self, box: ToolBox) -> None:
        """Testing item 8: a green suite cannot see a tool the agent was never offered."""
        offered = {schema["function"]["name"] for schema in box.schemas(include_mutating=True)}
        assert "build_design" in offered

    def test_it_is_withheld_where_mutations_are_not_allowed(self, box: ToolBox) -> None:
        offered = {schema["function"]["name"] for schema in box.schemas(include_mutating=False)}
        assert "build_design" not in offered
        assert box.is_mutating("build_design") is True

    def test_it_needs_the_users_confirmation_like_any_write(self, box: ToolBox) -> None:
        record(box)
        with pytest.raises(ToolError, match="needs the user's confirmation"):
            box.call("build_design", {}, allow_mutations=False)

    def test_the_step_list_says_what_is_happening(self) -> None:
        assert BUILTIN_TOOL_LABELS["build_design"] == "Building the part from the design"

    def test_the_step_summary_counts_features_and_operations(self) -> None:
        line = summarise_step(
            "build_design",
            {"design": "Plate", "features_built": ["a", "b", "c"], "calls": 5},
            True,
        )
        assert line == "Built Plate: 3 features, 5 operations"


class TestAFailureSaysHowFarItGot:
    @needs_kernel
    def test_a_feature_the_kernel_refuses_stops_the_build_and_names_it(
        self, occt: None, box: ToolBox
    ) -> None:
        record(box, **a_plate_whose_last_feature_fails())

        with pytest.raises(ToolError) as refused:
            box.call("build_design", {}, allow_mutations=True)

        message = str(refused.value)
        assert "The build stopped at" in message
        assert "plate.round" in message  # the semantic name the design was written in
        assert "catia_fillet" in message
        assert f"of {planned_calls(box)} calls" in message
        assert "did not produce a shape" in message  # the kernel's own words, passed through
        assert "still in the part" in message
        assert "Do not call build_design again" in message

    @needs_kernel
    def test_what_was_built_before_the_failure_is_still_there(
        self, occt: None, box: ToolBox
    ) -> None:
        record(box, **a_plate_whose_last_feature_fails())
        with pytest.raises(ToolError):
            box.call("build_design", {}, allow_mutations=True)

        listed = box.call("catia_list_features", {}, allow_mutations=True)

        assert "plate_outline" in str(listed)
        assert "plate_body" in str(listed)

    @needs_kernel
    def test_a_second_build_on_the_open_kernel_is_refused_and_builds_nothing(
        self, occt: None, box: ToolBox
    ) -> None:
        record(box)
        box.call("build_design", {}, allow_mutations=True)

        with pytest.raises(ToolError) as refused:
            box.call("build_design", {}, allow_mutations=True)

        assert "The build could not start" in str(refused.value)
        assert "already owns the part" in str(refused.value)
        assert "Nothing was built" in str(refused.value)

    def test_no_recorded_design_says_what_to_do_first(self, box: ToolBox) -> None:
        with pytest.raises(ToolError, match="Call record_design first"):
            box.call("build_design", {}, allow_mutations=True)

    def test_a_design_that_no_longer_compiles_builds_nothing(
        self, box: ToolBox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registry can change between `record_design` and the build."""
        from app.design import compile as compile_module
        from app.design.errors import SpecError

        record(box)

        def refuse(*_: Any, **__: Any) -> Any:
            raise SpecError("plate.body: that operation no longer exists")

        monkeypatch.setattr(compile_module, "compile_spec", refuse)
        monkeypatch.setattr(
            box, "_call_catia", lambda *a, **k: pytest.fail("nothing may be called")
        )

        with pytest.raises(ToolError, match="does not compile, so nothing was built"):
            box.call("build_design", {}, allow_mutations=True)

    def _own_a_document(
        self, db_session: Session, conversation: Conversation, doc_name: str
    ) -> None:
        from app.models.catia import CatiaDocument

        db_session.add(CatiaDocument(conversation_id=conversation.id, doc_name=doc_name))
        db_session.flush()

    def test_a_workstation_that_already_started_this_part_is_refused_before_anything_runs(
        self,
        box: ToolBox,
        db_session: Session,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On a seat `catia_new_part` *succeeds* with a part owned -- a second part -- so a
        second build would quietly double the part. This is the only thing standing in the way."""
        record(box)
        self._own_a_document(db_session, conversation, "Plate")
        monkeypatch.setattr(backends, "is_local", lambda: False)
        monkeypatch.setattr(
            box, "_call_catia", lambda *a, **k: pytest.fail("nothing may be called")
        )

        with pytest.raises(ToolError) as refused:
            box.call("build_design", {}, allow_mutations=True)

        assert "The part 'Plate' was already started in this conversation" in str(refused.value)
        assert "second 'Plate'" in str(refused.value)

    def test_the_part_is_recognised_whatever_extension_the_seat_stored_it_under(
        self,
        box: ToolBox,
        db_session: Session,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        record(box)
        self._own_a_document(db_session, conversation, "plate.CATPart")
        monkeypatch.setattr(backends, "is_local", lambda: False)
        monkeypatch.setattr(
            box, "_call_catia", lambda *a, **k: pytest.fail("nothing may be called")
        )

        with pytest.raises(ToolError, match="already started"):
            box.call("build_design", {}, allow_mutations=True)

    def test_an_assemblys_next_part_is_not_refused_for_the_part_before_it(
        self,
        box: ToolBox,
        db_session: Session,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A seat owns a *set* of documents. Refusing on "owns anything" would make
        `build_design` unusable for the second part of every assembly."""
        record(box)
        self._own_a_document(db_session, conversation, "Bracket")
        monkeypatch.setattr(backends, "is_local", lambda: False)
        seen: list[str] = []

        def fake(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            seen.append(name)
            return {"feature": f"{name}.1"}

        monkeypatch.setattr(box, "_call_catia", fake)

        result = box.call("build_design", {}, allow_mutations=True)

        assert seen[0] == "catia_new_part"
        assert result["calls"] == len(seen)

    def test_a_workstation_with_no_part_is_not_refused(
        self, box: ToolBox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record(box)
        monkeypatch.setattr(backends, "is_local", lambda: False)
        seen: list[str] = []

        def fake(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            seen.append(name)
            return {"feature": f"{name}.1"}

        monkeypatch.setattr(box, "_call_catia", fake)

        result = box.call("build_design", {}, allow_mutations=True)

        assert seen[0] == "catia_new_part"
        assert result["calls"] == len(seen)

    def test_the_seat_refusal_names_no_tool_that_only_the_open_kernel_has(self) -> None:
        """The S2 defect (2026-09-06): a seat's refusal sent the agent to
        `catia_assembly_component`, which is `server_only`, and it answered "there is no tool
        called that". `test_agent.TestTheSecondPartRefusalIsBackendAccurate` counts the one
        permitted mention; this reads the handler that must not add a second."""
        import inspect

        source = inspect.getsource(ToolBox._build_design) + inspect.getsource(
            ToolBox._already_started
        )
        assert "catia_assembly_component" not in source


class TestTheStopButtonIsReadBetweenCalls:
    def test_a_stop_request_ends_the_build_between_two_features(
        self, box: ToolBox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A build is the one tool that can run for minutes, and between two calls is the
        only place the part is whole."""
        from app.core import interruption

        record(box)
        ran: list[str] = []
        monkeypatch.setattr(
            box, "_call_catia", lambda name, arguments: ran.append(name) or {"feature": name}
        )
        # Not stopped for the first two calls, stopped from the third.
        monkeypatch.setattr(
            interruption, "turn_stop_requested", lambda db, conversation: len(ran) >= 2
        )

        with pytest.raises(ToolError) as stopped:
            box.call("build_design", {}, allow_mutations=True)

        assert len(ran) == 2
        assert "Stopped at your request, between two features" in str(stopped.value)
        assert "2 of" in str(stopped.value)

    def test_without_a_stop_request_every_call_is_made(
        self, box: ToolBox, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record(box)
        ran: list[str] = []
        monkeypatch.setattr(
            box, "_call_catia", lambda name, arguments: ran.append(name) or {"feature": name}
        )

        box.call("build_design", {}, allow_mutations=True)

        assert len(ran) == planned_calls(box)


class _Scripted(LLMProvider):
    """Replays fixed turns and counts the model calls it was asked for."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.model_calls = 0

    def health(self) -> None:
        return None

    def complete(self, **_: Any) -> Any:
        raise NotImplementedError

    def chat(self, **_: Any) -> AssistantTurn:
        self.model_calls += 1
        if not self._turns:
            return AssistantTurn(text="Done.", usage=TokenUsage(3, 4))
        return self._turns.pop(0)
