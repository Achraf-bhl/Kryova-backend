"""One row per agent turn, and a turn that died is still billed for what it spent.

ROAD_TO_10 0.2 (the recorder) and the half of 1.4 that lives in the routes. The
claims a weaker test passes:

* a turn driven by a fake provider writes **exactly one** row, with the sums the
  fake reported -- and nothing is written for a turn that never reached a model;
* the loop counts what it offered, what ran, what failed and what it turned back,
  *separately*, because a blocked repeat is the model's loop and not a tool's fault;
* the tokens of steps 1..N-1 of a turn that raised at step N are in the ledger.
  The old streaming route read its total off the final `done` event, which a failed
  turn never emits, so those tokens were real spend that no budget ever saw.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.agent import stream_agent
from app.ai.provider import AssistantTurn, LLMError, LLMProvider, TokenUsage, ToolCall
from app.ai.tools import ToolBox
from app.ai.turn_metrics import STOP_ERROR, TurnMeter, record_turn
from app.api.routes import ai as ai_routes
from app.core.config import ModelPrice, settings
from app.core.security import hash_password
from app.models import AITokenUsage, Conversation, TurnMetric, User


class Scripted(LLMProvider):
    """Answers each `chat` with the next scripted turn, or raises it if it is an error."""

    name = "scripted"
    model = "m"

    def __init__(self, *steps: AssistantTurn | Exception) -> None:
        self.steps = list(steps)

    def health(self) -> None:
        return None

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise LLMError("not used")

    def chat(self, *args: Any, **kwargs: Any) -> AssistantTurn:
        if not self.steps:
            # Only the conversation's title is asked for after the script is spent.
            return AssistantTurn(text="A title", usage=TokenUsage(0, 0, 0))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"call-{name}", name=name, arguments=arguments)


def _wants(*calls: ToolCall, usage: TokenUsage = TokenUsage(100, 10, 0)) -> AssistantTurn:
    return AssistantTurn(tool_calls=list(calls), usage=usage)


def _says(text: str, usage: TokenUsage = TokenUsage(100, 10, 0)) -> AssistantTurn:
    return AssistantTurn(text=text, usage=usage)


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="metrics@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def conversation(db_session: Session, user: User) -> Conversation:
    row = Conversation(owner_id=user.id, title="t")
    db_session.add(row)
    db_session.flush()
    return row


def _drive(
    db: Session, user: User, conversation: Conversation, provider: LLMProvider, meter: TurnMeter
) -> list[dict[str, Any]]:
    toolbox = ToolBox(db=db, user=user, conversation=conversation)
    return list(
        stream_agent(
            db=db,
            provider=provider,
            conversation=conversation,
            toolbox=toolbox,
            user_message="list my projects",
            user=user,
            meter=meter,
        )
    )


class TestTheMeter:
    def test_charging_sums_usage_and_counts_calls(self) -> None:
        meter = TurnMeter()
        meter.charge(TokenUsage(100, 10, 60))
        meter.charge(TokenUsage(300, 20, 250))
        assert meter.usage == TokenUsage(400, 30, 310)
        assert meter.model_calls == 2

    def test_usage_with_no_call_behind_it_is_not_counted_as_one(self) -> None:
        meter = TurnMeter()
        meter.charge(TokenUsage(), calls=0)
        assert meter.model_calls == 0

    def test_the_peak_is_the_largest_single_request_not_the_total(self) -> None:
        meter = TurnMeter()
        for prompt in (1_000, 9_000, 4_000):
            meter.charge(TokenUsage(prompt, 1, 0))
        assert meter.peak_prompt_tokens == 9_000

    def test_an_unpriced_model_costs_unknown(self) -> None:
        meter = TurnMeter()
        meter.charge(TokenUsage(1_000, 100, 0))
        assert meter.cost_micro_usd("nobody-priced-this") is None


class TestTheLoopCountsWhatHappened:
    def test_a_two_step_turn_with_one_failed_call_is_counted_exactly(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        provider = Scripted(
            _wants(_call("list_projects"), _call("no_such_tool"), usage=TokenUsage(1_000, 50, 800)),
            _says("Done.", usage=TokenUsage(1_200, 30, 1_000)),
        )
        meter = TurnMeter()
        events = _drive(db_session, user, conversation, provider, meter)

        assert meter.rounds == 2
        assert meter.model_calls == 2
        assert meter.tool_calls == 2
        assert meter.tool_calls_failed == 1
        assert meter.tool_calls_blocked == 0
        # Without mutation consent the model is offered the read-only tools: the whole
        # of them, not a hand-picked few.
        offered = len(ToolBox(db=db_session, user=user).schemas(include_mutating=False))
        assert meter.tools_offered == offered > 30
        assert meter.usage == TokenUsage(2_200, 80, 1_800)
        assert meter.peak_prompt_tokens == 1_200
        assert meter.stop_reason == "finished"
        done = events[-1]
        assert done["type"] == "done"
        assert done["cached_prompt_tokens"] == 1_800
        assert done["prompt_tokens"] == 2_200
        assert done["wall_ms"] >= 0

    def test_the_done_event_prices_the_turn_from_the_configured_model(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            settings,
            "ai_prices",
            {"m": ModelPrice(input=Decimal("1"), cached_input=Decimal("0.1"), output=Decimal("2"))},
        )
        provider = Scripted(_says("Hi.", usage=TokenUsage(1_000, 100, 900)))
        done = _drive(db_session, user, conversation, provider, TurnMeter())[-1]
        # 100 fresh x 1 + 900 cached x 0.1 + 100 out x 2 = 100 + 90 + 200 micro-dollars.
        assert done["cost_micro_usd"] == 390

    def test_the_done_event_says_unknown_for_an_unpriced_model(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        done = _drive(
            db_session, user, conversation, Scripted(_says("Hi.")), TurnMeter()
        )[-1]
        assert done["cost_micro_usd"] is None

    def test_a_repeated_read_is_blocked_not_counted_as_a_failed_tool(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        same = _call("list_projects")
        provider = Scripted(
            _wants(same), _wants(same), _wants(same), _wants(same), _says("Done.")
        )
        meter = TurnMeter()
        _drive(db_session, user, conversation, provider, meter)
        assert meter.tool_calls_blocked >= 1
        assert meter.tool_calls_failed == 0
        assert meter.tool_calls + meter.tool_calls_blocked == 4

    def test_a_fold_of_the_older_transcript_is_billed_to_the_turn_that_triggered_it(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The summariser is a real provider call made on the user's behalf before
        # the first step, and nothing else in the turn would ever pay for it.
        monkeypatch.setattr(
            "app.ai.agent.maybe_summarise", lambda *a, **k: TokenUsage(3_000, 200, 0)
        )
        meter = TurnMeter()
        _drive(db_session, user, conversation, Scripted(_says("Done.")), meter)
        assert meter.usage == TokenUsage(3_100, 210, 0)
        assert meter.model_calls == 2

    def test_a_hosted_routing_decision_is_billed_to_the_turn_that_asked_for_it(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # `_shown_tools` documents that whatever a hosted decider spends is handed
        # back to be added to the turn; a paid call dropped here is invisible to the
        # budget and to billing alike.
        def routed(toolbox: Any, message: str, provider: Any = None, spent: Any = None) -> None:
            spent.append(TokenUsage(500, 20, 0))

        monkeypatch.setattr("app.ai.agent._shown_tools", routed)
        meter = TurnMeter()
        _drive(db_session, user, conversation, Scripted(_says("Done.")), meter)
        assert meter.usage == TokenUsage(600, 30, 0)

    def test_the_closing_answer_of_a_turn_that_ran_out_of_steps_is_billed(
        self,
        db_session: Session,
        user: User,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # One round allowed, spent on a tool call: the loop then asks for a closing
        # summary with the tools taken away. That call is spend like any other.
        monkeypatch.setattr("app.ai.agent.max_steps", lambda: 1)
        provider = Scripted(
            _wants(_call("list_projects"), usage=TokenUsage(1_000, 50, 0)),
            _says("I ran out of steps.", usage=TokenUsage(1_400, 60, 0)),
        )
        meter = TurnMeter()
        _drive(db_session, user, conversation, provider, meter)
        assert meter.stop_reason == "step_budget"
        assert meter.usage == TokenUsage(2_400, 110, 0)
        assert meter.model_calls == 2

    def test_the_meter_still_holds_the_spend_when_the_provider_raises(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        provider = Scripted(
            _wants(_call("list_projects"), usage=TokenUsage(1_000, 50, 0)),
            LLMError("402: out of credits", usage=TokenUsage(7, 0, 0)),
        )
        meter = TurnMeter()
        with pytest.raises(LLMError):
            _drive(db_session, user, conversation, provider, meter)
        assert meter.usage == TokenUsage(1_000, 50, 0)
        assert meter.rounds == 2


class TestOneRowPerTurn:
    def test_a_turn_writes_exactly_one_row_with_the_sums_the_provider_reported(
        self, db_session: Session, user: User, conversation: Conversation
    ) -> None:
        provider = Scripted(
            _wants(_call("list_projects"), usage=TokenUsage(1_000, 50, 800)),
            _says("Done.", usage=TokenUsage(1_200, 30, 1_000)),
        )
        meter = TurnMeter()
        _drive(db_session, user, conversation, provider, meter)
        record_turn(
            db_session, user=user, conversation=conversation, meter=meter,
            provider="scripted", model="m",
        )
        row = db_session.query(TurnMetric).filter_by(user_id=user.id).one()
        assert (row.prompt_tokens, row.cached_prompt_tokens, row.completion_tokens) == (
            2_200, 1_800, 80,
        )
        assert (row.rounds, row.model_calls, row.tool_calls, row.tool_calls_failed) == (2, 2, 1, 0)
        assert row.peak_prompt_tokens == 1_200
        assert row.stop_reason == "finished"
        assert row.conversation_id == conversation.id
        assert row.step_budget > 0 and row.tools_offered > 0

    def test_a_turn_that_never_reached_a_model_writes_nothing(
        self, db_session: Session, user: User
    ) -> None:
        assert (
            record_turn(
                db_session, user=user, conversation=None, meter=TurnMeter(),
                provider="scripted", model="m",
            )
            is None
        )
        assert db_session.query(TurnMetric).count() == 0

    def test_the_routes_reason_is_used_only_when_the_loop_gave_none(
        self, db_session: Session, user: User
    ) -> None:
        meter = TurnMeter()
        meter.charge(TokenUsage(10, 1, 0))
        row = record_turn(
            db_session, user=user, conversation=None, meter=meter,
            provider="scripted", model="m", default_stop_reason=STOP_ERROR,
        )
        assert row is not None and row.stop_reason == STOP_ERROR
        meter.stop_reason = "finished"
        row = record_turn(
            db_session, user=user, conversation=None, meter=meter,
            provider="scripted", model="m", default_stop_reason=STOP_ERROR,
        )
        assert row is not None and row.stop_reason == "finished"


def _events(response: Any) -> list[dict[str, Any]]:
    out = []
    for block in response.text.split("\n\n"):
        line = next((ln for ln in block.split("\n") if ln.startswith("data:")), None)
        if line:
            out.append(json.loads(line[5:].strip()))
    return out


class TestAFailedTurnIsBilledForWhatItSpent:
    @pytest.fixture
    def dies_on_step_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "ai_prices", {"m": ModelPrice(input=Decimal(1), output=Decimal(1))})
        monkeypatch.setattr(
            ai_routes,
            "get_provider",
            lambda: Scripted(
                _wants(_call("list_projects"), usage=TokenUsage(1_000, 50, 600)),
                LLMError("provider fell over", usage=TokenUsage(40, 0, 0)),
            ),
        )

    def test_the_streaming_route_bills_step_one_and_the_failing_call(
        self, auth_client: Any, db_session: Session, dies_on_step_two: None
    ) -> None:
        response = auth_client.post("/api/v1/ai/chat/stream", json={"message": "list my projects"})
        assert any(e["type"] == "error" for e in _events(response))
        rows = db_session.query(AITokenUsage).filter_by(purpose="chat").all()
        assert len(rows) == 1
        assert rows[0].prompt_tokens == 1_040
        assert rows[0].completion_tokens == 50
        assert rows[0].cached_prompt_tokens == 600
        assert rows[0].cost_micro_usd is not None

    def test_and_writes_a_metrics_row_that_says_it_ended_in_error(
        self, auth_client: Any, db_session: Session, dies_on_step_two: None
    ) -> None:
        auth_client.post("/api/v1/ai/chat/stream", json={"message": "list my projects"})
        row = db_session.query(TurnMetric).one()
        assert row.stop_reason == "error"
        assert row.rounds == 2 and row.tool_calls == 1

    def test_the_plain_route_bills_it_too_and_still_answers_with_an_error(
        self, auth_client: Any, db_session: Session, dies_on_step_two: None
    ) -> None:
        response = auth_client.post("/api/v1/ai/chat", json={"message": "list my projects"})
        assert response.status_code == 502
        assert db_session.query(AITokenUsage).filter_by(purpose="chat").one().prompt_tokens == 1_040
        assert db_session.query(TurnMetric).one().stop_reason == "error"

    def test_a_finished_turn_is_billed_once_with_its_loops_own_stop_reason(
        self, auth_client: Any, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ai_routes, "get_provider", lambda: Scripted(_says("Noted.", TokenUsage(30, 5, 20)))
        )
        auth_client.post("/api/v1/ai/chat/stream", json={"message": "hello"})
        assert db_session.query(AITokenUsage).filter_by(purpose="chat").count() == 1
        row = db_session.query(TurnMetric).one()
        assert (row.stop_reason, row.cached_prompt_tokens) == ("finished", 20)
