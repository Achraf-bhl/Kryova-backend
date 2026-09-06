"""The loop will not let a turn close on a requirement nobody measured.

`tests/test_verification.py` pins the arithmetic; this pins what the loop does
with it, which is the half that actually changes a run. The provider is
scripted, so what is asserted is the loop's guarantee and not a model's mood.

The behaviour, in one sentence: when the model stops calling tools, what the
engineer asked for is compared against what this turn measured, and if a stated
number was never measured the model is handed those exact clauses and the turn
carries on. Once. If it closes anyway, the answer goes out with the unverified
requirements listed under it, because the user cannot tell measured from assumed
and the model's closing paragraph will not tell them either.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.ai.agent import DEFAULT_MAX_STEPS, MAX_VERIFICATION_NUDGES, run_agent
from app.ai.provider import AssistantTurn, ToolCall
from app.models import Conversation, Project, User
from tests import test_agent as _agent

# Re-exported by assignment rather than by `from ... import`: pytest collects a
# fixture by module attribute either way, and a bare import makes every test
# parameter of the same name read as a redefinition of it.
ScriptedProvider = _agent.ScriptedProvider
_toolbox = _agent._toolbox
user = _agent.user
project = _agent.project
conversation = _agent.conversation


def _run(
    db: Session,
    user: User,
    project: Project,
    conversation: Conversation,
    turns: list[AssistantTurn],
    message: str,
) -> Any:
    provider = ScriptedProvider(turns)
    reply = run_agent(
        db=db,
        provider=provider,
        conversation=conversation,
        toolbox=_toolbox(db, user, project),
        user_message=message,
    )
    return reply, provider


class TestTheTurnIsHeldOpen:
    def test_a_stated_number_nobody_measured_sends_the_model_back(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The measured shape of every failure this exists for: the model
        answers confidently, having measured nothing."""
        reply, provider = _run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="Done, the plate is built.")],
            "Make a plate 60 mm long",
        )
        # It was asked a second time -- the turn did not close on the first
        # answer -- and the request it was given names the clause.
        assert len(provider.seen_transcripts) >= 2
        assert "60 mm long" in provider.last_user_text
        assert "Not checked is not the same as fine." in provider.last_user_text

    def test_the_hold_happens_only_once(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """A model that comes back a second time without measuring is not going
        to on a third, and every extra round is a spinner the user watches."""
        reply, provider = _run(
            db_session,
            user,
            project,
            conversation,
            [
                AssistantTurn(text="Done."),
                AssistantTurn(text="It is definitely 60 mm."),
                AssistantTurn(text="Really, it is."),
            ],
            "Make a plate 60 mm long",
        )
        assert MAX_VERIFICATION_NUDGES == 1
        # One call for the first answer, one more after the single hold, and
        # then it closes: the third scripted answer is never asked for. Holding
        # twice would consume it.
        assert len(provider.seen_transcripts) == 1 + MAX_VERIFICATION_NUDGES
        assert "Really, it is." not in reply.text

    def test_a_request_with_no_number_closes_immediately(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """Nothing to measure, nothing to hold open. A documentation question
        must not be turned into two round trips."""
        reply, provider = _run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="6061-T6 yields at 276 MPa.")],
            "What does 6061 yield at?",
        )
        assert len(provider.seen_transcripts) == 1
        assert reply.text == "6061-T6 yields at 276 MPa."


class TestWhatTheUserEndsUpSeeing:
    def test_the_unverified_requirements_are_listed_under_the_answer(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The model's own words are kept; the omission is stated beside them."""
        reply, _ = _run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="Done."), AssistantTurn(text="All finished.")],
            "Make a plate 60 mm long",
        )
        assert "All finished." in reply.text
        assert "Not verified in this turn" in reply.text
        assert "60 mm long" in reply.text

    def test_an_answer_that_measured_everything_is_left_alone(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        """The footnote must not appear on a turn that did the work -- it would
        train the user to ignore it, which is worse than not having it."""
        reply, _ = _run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="Aluminium 6061-T6.")],
            "Which alloy should I use?",
        )
        assert "Not verified in this turn" not in reply.text


class TestTheRoundCapClosesHonestlyToo:
    """The path where it matters most, and the one it was missing.

    A turn that ends on the round cap has by definition not finished, and the
    closing summary is written with the tools already withdrawn -- the model
    could not measure anything now even if it wanted to. Measured on ladder
    prompt PRO4, 2026-09-07: seventeen steps, a C-frame built, and not one of
    the punching force, the lever ratio or the frame stiffness computed or
    checked. The summary went out with nothing saying so.
    """

    def test_the_summary_is_told_what_was_never_measured(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        wants_tools = AssistantTurn(
            text="", tool_calls=[ToolCall(id="1", name="list_projects", arguments={})]
        )
        provider = ScriptedProvider([wants_tools] * (DEFAULT_MAX_STEPS + 1))
        run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="Punch 6 mm holes through 3 mm steel and tell me the force",
        )
        # The last thing the model was shown before writing its summary.
        assert "6 mm" in provider.last_user_text or "3 mm" in provider.last_user_text
        assert "Not checked is not the same as fine." in provider.last_user_text

    def test_the_user_sees_the_list_under_the_summary(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        wants_tools = AssistantTurn(
            text="", tool_calls=[ToolCall(id="1", name="list_projects", arguments={})]
        )
        provider = ScriptedProvider([wants_tools] * (DEFAULT_MAX_STEPS + 1))
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="Punch 6 mm holes through 3 mm steel and tell me the force",
        )
        assert reply.truncated is True
        assert "Not verified in this turn" in reply.text
