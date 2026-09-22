"""The decision Kryova puts in front of a person, and the four rules it will not bend.

The user's request of 2026-09-22 was that Kryova *tell the user to intervene* when it
needs a decision, shown in the app for them to decide. The failure mode this guards is
not "no prompt appears" -- it is the opposite: a prompt that appears and quietly answers
itself. A default choice, a single option, or a list with no way out all render as a
decision and all take it away from the user, and none of them looks wrong on screen.

So the tests below are mostly refusals, and each one is a thing the surface must be
*unable* to do rather than a thing it merely does not do today.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai import recovery
from app.ai.intervention import (
    STOP,
    Choice,
    Intervention,
    InterventionKind,
    for_gate,
    from_failures,
    from_recovery,
)


# The turn-level tests at the bottom drive the real `stream_agent`, which needs a signed-in
# user, a project and a conversation. Declared here rather than imported from
# `tests/test_agent.py`, whose copies are module-local: re-exporting a fixture makes every
# test parameter that uses it read as a redefinition, and the alternative of reaching into
# another test module's internals is the more fragile of the two couplings.
@pytest.fixture
def user(db_session: Session):
    from app.core.security import hash_password
    from app.models import User

    account = User(
        email="intervention@kryova.dev",
        hashed_password=hash_password("a-long-enough-password"),
    )
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def project(db_session: Session, user):
    from app.models import Project

    row = Project(name="Bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def conversation(db_session: Session, user, project):
    from app.models import Conversation

    row = Conversation(owner_id=user.id, project_id=project.id, title="t")
    db_session.add(row)
    db_session.flush()
    return row


def _stuck(tool: str = "catia_pad", message: str = "", arguments: dict | None = None):
    """A `Recovery` that has exhausted its budget on one tool, which is the trigger."""
    failure = recovery.Failure(
        tool=tool,
        message=message or "No feature named 'plate.body' exists in this document.",
        arguments=arguments if arguments is not None else {"sketch": "plate.body"},
    )
    return recovery.Recovery([failure] * recovery.MAX_SAME_FAILURE)


class TestItAsksOnlyWhenItIsActuallyStuck:
    """A prompt on a turn that went fine is a nag, and a nag trains people to dismiss."""

    def test_a_turn_with_no_failures_gets_no_intervention(self) -> None:
        assert from_recovery(recovery.Recovery()) is None

    def test_one_failure_is_not_enough_to_interrupt_somebody(self) -> None:
        """One is noise and two can be a race -- `MAX_SAME_FAILURE` is three, and the
        intervention must not fire earlier than the escalation it mirrors."""
        one = recovery.Recovery([recovery.Failure("catia_pad", "it broke")])

        assert from_recovery(one) is None
        assert one.escalation() == ""

    def test_it_fires_exactly_when_the_prose_escalation_does(self) -> None:
        """The two surfaces must agree about *whether* there is a question, not only
        about its wording. One firing without the other is a decision with no record, or
        a record nobody is shown."""
        stuck = _stuck()

        assert from_recovery(stuck) is not None
        assert stuck.escalation() != ""


class TestItNeverAnswersItsOwnQuestion:
    """The rule the whole module exists for."""

    def test_no_choice_is_marked_as_a_default_or_a_recommendation(self) -> None:
        """Checked structurally rather than by reading: a `Choice` has no field that
        could carry a preference, so a later change that adds one fails here."""
        fields = set(Choice.__dataclass_fields__)

        assert fields == {"id", "label", "detail", "needs_reason"}, (
            "a Choice grew a field -- if it expresses a preference, this surface is "
            "answering its own question"
        )

    def test_an_intervention_cannot_offer_a_single_option(self) -> None:
        with pytest.raises(ValueError, match="at least two options"):
            Intervention(
                kind=InterventionKind.REPEATED_FAILURE,
                question="Carry on?",
                cause="something failed",
                choices=(Choice("yes", "Yes", "Carry on."),),
            )

    def test_a_typed_answer_is_always_accepted_and_cannot_be_switched_off(self) -> None:
        """`answer_in_words` is a property, so there is no constructor argument and no
        attribute to set. The options are the common answers, never the possible ones."""
        asking = from_recovery(_stuck())
        assert asking is not None
        assert asking.answer_in_words is True
        assert "answer_in_words" not in Intervention.__dataclass_fields__

        with pytest.raises(AttributeError):
            asking.answer_in_words = False  # type: ignore[misc]

    def test_stopping_is_offered_on_every_intervention_without_a_builder_asking(
        self,
    ) -> None:
        """Added by construction, not by each builder remembering. A decision screen with
        no way out traps somebody who wanted to go and think about it."""
        built = Intervention(
            kind=InterventionKind.REPEATED_FAILURE,
            question="Which way?",
            cause="it failed",
            choices=(Choice("a", "A", "do a"), Choice("b", "B", "do b")),
        )

        assert built.choice(STOP.id) == STOP
        assert [one.id for one in built.choices] == ["a", "b", "stop"]

    def test_stop_is_not_added_twice_when_a_builder_supplies_it(self) -> None:
        built = Intervention(
            kind=InterventionKind.REPEATED_FAILURE,
            question="Which way?",
            cause="it failed",
            choices=(Choice("a", "A", "do a"), Choice("b", "B", "do b"), STOP),
        )

        assert [one.id for one in built.choices].count("stop") == 1

    def test_two_choices_with_one_id_are_refused(self) -> None:
        with pytest.raises(ValueError, match="share an id"):
            Intervention(
                kind=InterventionKind.REPEATED_FAILURE,
                question="Which way?",
                cause="it failed",
                choices=(Choice("a", "A", "do a"), Choice("a", "A again", "do a again")),
            )

    def test_a_prompt_with_no_question_is_refused(self) -> None:
        """A notification is not a decision, and this surface is for decisions."""
        with pytest.raises(ValueError, match="must state what is being decided"):
            Intervention(
                kind=InterventionKind.REPEATED_FAILURE,
                question="   ",
                cause="it failed",
                choices=(Choice("a", "A", "do a"), Choice("b", "B", "do b")),
            )

    def test_every_choice_explains_what_pressing_it_will_do(self) -> None:
        """A button whose consequence the user cannot see is the failure mode this
        module exists to avoid. Swept over every kind rather than spot-checked."""
        for kind in (
            recovery.MISSING_SUBJECT,
            recovery.BAD_ARGUMENT,
            recovery.UNAVAILABLE,
            recovery.REFUSED,
            recovery.GEOMETRY,
            recovery.UNCLASSIFIED,
        ):
            asking = from_recovery(_stuck(message=_message_for(kind)))
            assert asking is not None, kind
            for choice in asking.choices:
                assert choice.label.strip(), f"{kind}: a choice with no label"
                assert len(choice.detail.strip()) > 20, (
                    f"{kind}/{choice.id}: a choice must say what it will do"
                )


def _message_for(kind: str) -> str:
    """An error message that `recovery.py` classifies as `kind`.

    Built by asking `recovery` itself rather than by copying its patterns, so this test
    cannot drift from the taxonomy it is sweeping.
    """
    samples = {
        recovery.MISSING_SUBJECT: "No feature named 'plate.body' exists in this document.",
        recovery.BAD_ARGUMENT: "Unknown argument 'lenght' for this operation.",
        recovery.UNAVAILABLE: "The workstation is not reachable.",
        recovery.REFUSED: "Refused: this call was already refused in this turn.",
        recovery.GEOMETRY: "The sketch has an open profile, so the pad will not build.",
        recovery.UNCLASSIFIED: "zzzz unrecognisable zzzz",
    }
    message = samples[kind]
    got = recovery.Failure("catia_pad", message).kind
    assert got == kind, f"the sample for {kind} now classifies as {got}"
    return message


class TestItSaysWhatIsBeingDecidedAndWhy:
    """A question with the failure invisible behind it is unanswerable."""

    def test_it_names_the_tool_the_subject_and_the_count(self) -> None:
        asking = from_recovery(_stuck())
        assert asking is not None

        assert asking.tool == "catia_pad"
        assert asking.subject == "plate.body"
        assert "catia_pad" in asking.cause
        assert "plate.body" in asking.cause
        assert "3 times" in asking.cause

    def test_the_machines_own_words_are_quoted_verbatim(self) -> None:
        """The one screen where the exact wording is the evidence. Nothing paraphrases
        it, and nothing here calls a model to reword it."""
        message = "No feature named 'plate.body' exists in this document."
        asking = from_recovery(_stuck(message=message))
        assert asking is not None

        assert asking.quote == message

    def test_a_long_message_is_truncated_visibly(self) -> None:
        """`recovery.one_line`'s contract: a quote cut off with no mark reads as the
        whole thing, and the point of quoting verbatim is that the quote is trustworthy."""
        asking = from_recovery(_stuck(message="No feature named 'x' exists. " + "z" * 500))
        assert asking is not None

        assert asking.quote.endswith("…")
        assert len(asking.quote) <= 240

    def test_the_question_is_the_same_one_the_transcript_records(self) -> None:
        """The button and the sentence are built from one `Failure` on purpose. If they
        forked, the user would press an option the record does not mention."""
        stuck = _stuck()
        asking = from_recovery(stuck)
        assert asking is not None

        assert asking.question in stuck.escalation()

    def test_a_failure_whose_arguments_name_nothing_leaves_the_subject_out(self) -> None:
        """Best effort, and honest about it -- `Failure.subject` invents nothing, so the
        prompt says less rather than something untrue."""
        asking = from_recovery(_stuck(arguments={}))
        assert asking is not None

        assert asking.subject == ""
        assert " on " not in asking.cause


class TestTheApprovalKind:
    """A checkpoint decided where the work is, instead of on another page."""

    def test_it_carries_the_gate_so_the_answer_reaches_the_row(self) -> None:
        asking = for_gate("gate-123", summary="Sign off the bracket before machining.")

        assert asking.kind is InterventionKind.APPROVAL
        assert asking.gate_id == "gate-123"
        assert "Sign off the bracket" in asking.cause

    def test_rejecting_demands_a_reason_and_approving_does_not(self) -> None:
        """`gates.py` rule 2: a rejection carries a reason, because the agent's next move
        depends entirely on why."""
        asking = for_gate("gate-123", summary="Sign this off.")

        approve = asking.choice("approve")
        reject = asking.choice("reject")
        assert approve is not None and reject is not None
        assert reject.needs_reason is True
        assert approve.needs_reason is False

    def test_a_failure_intervention_carries_no_gate(self) -> None:
        """`None` means "reply in the conversation". A gate id here would send an answer
        to a row that does not exist."""
        asking = from_recovery(_stuck())
        assert asking is not None

        assert asking.gate_id is None


class TestTheShapeTheAppReceives:
    """`to_dict` is the contract `lib/agent-stream.ts` decodes; it is not a debug dump."""

    def test_every_field_the_surface_needs_is_present(self) -> None:
        payload = from_recovery(_stuck()).to_dict()  # type: ignore[union-attr]

        assert set(payload) == {
            "kind", "question", "cause", "subject", "tool", "quote",
            "gate_id", "answer_in_words", "choices",
        }
        assert payload["kind"] == "repeated-failure"
        assert payload["answer_in_words"] is True
        for choice in payload["choices"]:
            assert set(choice) == {"id", "label", "detail", "needs_reason"}

    def test_it_is_json_serialisable_as_it_stands(self) -> None:
        import json

        payload = for_gate("g1", summary="Sign this off.").to_dict()

        assert json.loads(json.dumps(payload)) == payload

    def test_json_round_trips_a_gate_id_of_none_rather_than_dropping_it(self) -> None:
        """The surface branches on `gate_id`, so its absence has to arrive as an explicit
        null rather than as a missing key."""
        import json

        payload = json.loads(json.dumps(from_recovery(_stuck()).to_dict()))  # type: ignore[union-attr]

        assert "gate_id" in payload
        assert payload["gate_id"] is None

    def test_the_one_shot_form_takes_raw_failure_records(self) -> None:
        raw = [
            {
                "tool": "catia_pad",
                "message": "No feature named 'plate.body' exists in this document.",
                "arguments": {"sketch": "plate.body"},
            }
        ] * recovery.MAX_SAME_FAILURE

        asking = from_failures(raw)

        assert asking is not None
        assert asking.subject == "plate.body"
        assert from_failures(raw[:1]) is None


class TestTheTurnActuallyEmitsIt:
    """The path, not the tool.

    `CLAUDE.md`'s testing item 8: a test that builds an `Intervention` proves the module
    and says nothing about whether a real turn ever produces one. Both defects of that
    class that shipped here were invisible to a green suite in exactly this way, so this
    drives `stream_agent` and reads the events a client would receive.
    """

    def _hammering(self, times: int) -> list:
        from app.ai.provider import AssistantTurn, ToolCall

        return [
            AssistantTurn(
                tool_calls=[
                    ToolCall(id=str(i), name="delete_simulation", arguments={"simulation_id": "x"})
                ]
            )
            for i in range(times)
        ] + [AssistantTurn(text="I could not do that.")] * 3

    def _events(self, db_session, user, project, conversation) -> list[dict]:
        from app.ai.agent import stream_agent
        from tests.test_agent import ScriptedProvider, _toolbox

        return list(
            stream_agent(
                db=db_session,
                provider=ScriptedProvider(self._hammering(12)),
                toolbox=_toolbox(db_session, user, project),
                conversation=conversation,
                user_message="delete it",
                allow_mutations=True,
            )
        )

    def test_a_stuck_turn_puts_a_decision_on_the_stream(
        self, db_session, user, project, conversation
    ) -> None:
        events = self._events(db_session, user, project, conversation)

        asking = [event for event in events if event["type"] == "intervention"]
        assert len(asking) == 1, "a stuck turn must offer the user a decision, exactly once"
        assert asking[0]["question"].strip()
        assert len(asking[0]["choices"]) >= 3
        assert asking[0]["answer_in_words"] is True

    def test_the_decision_is_repeated_on_done_so_a_reconnect_cannot_lose_it(
        self, db_session, user, project, conversation
    ) -> None:
        """A client that reconnects mid-turn replays from the resume buffer and can land
        after the `intervention` event went past. A decision prompt is the one thing a
        dropped event must not lose."""
        events = self._events(db_session, user, project, conversation)

        done = next(event for event in events if event["type"] == "done")
        asking = next(event for event in events if event["type"] == "intervention")

        assert done["stop_reason"] == "needs_input"
        assert done["intervention"] is not None
        assert done["intervention"]["question"] == asking["question"]

    def test_the_decision_arrives_before_the_turn_says_done(
        self, db_session, user, project, conversation
    ) -> None:
        """Order matters to a client that renders on `done` and stops listening."""
        kinds = [event["type"] for event in self._events(db_session, user, project, conversation)]

        assert kinds.index("intervention") < kinds.index("done")

    def test_the_prose_question_is_still_in_the_answer(
        self, db_session, user, project, conversation
    ) -> None:
        """The structured form is the surface; the sentence in the transcript is the
        record. Replacing the prose would make the question vanish on a reload."""
        events = self._events(db_session, user, project, conversation)

        message = next(event for event in events if event["type"] == "message")
        asking = next(event for event in events if event["type"] == "intervention")

        assert asking["question"] in message["content"], (
            "the transcript must still carry the question, or refreshing loses it"
        )

    def test_a_turn_that_goes_fine_offers_no_decision(
        self, db_session, user, project, conversation
    ) -> None:
        """The nag check, driven through the real turn rather than the module."""
        from app.ai.agent import stream_agent
        from app.ai.provider import AssistantTurn
        from tests.test_agent import ScriptedProvider, _toolbox

        events = list(
            stream_agent(
                db=db_session,
                provider=ScriptedProvider([AssistantTurn(text="All done.")]),
                toolbox=_toolbox(db_session, user, project),
                conversation=conversation,
                user_message="hello",
            )
        )

        assert not [event for event in events if event["type"] == "intervention"]
        done = next(event for event in events if event["type"] == "done")
        assert done.get("intervention") is None
