"""A tool call written as prose must never reach the user as an answer.

The failure this pins down is the quietest one the agent has. The model writes
out the call it *would* have made -- a fenced JSON block, a leaked harmony
channel header, a `<tool_call>` tag -- instead of issuing it. The provider
returns a turn with no tool calls, the loop reads that as "finished", and the
text becomes the reply. Nothing ran, so nothing failed, so there is no error
anywhere; and the text almost always says the work was done. Observed live:
"Project created", over a database with no new project in it.

Two halves, and the second is the one that keeps the feature usable:

- the shapes that *are* written calls are caught, and
- ordinary narration that merely names a tool is not, because a false positive
  turns a correct final answer into a retry the user sits through.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai.agent import MAX_CORRECTIONS, run_agent
from app.ai.malformed import correction_for, find_written_tool_calls, is_contentless
from app.ai.provider import AssistantTurn, TokenUsage, ToolCall
from app.ai.tools import ToolBox
from app.models import Conversation, MessageRole, Project, User
from tests.test_agent import ScriptedProvider

KNOWN = {"create_project", "catia_pad", "catia_new_part", "run_simulation", "list_projects"}


# Local fixtures rather than imports from `test_agent`: pulling a fixture across
# test modules works, but every consumer then shadows the imported name and the
# file fills with F811. These are four lines each.
@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="written-calls@kryova.dev", hashed_password=hash_password("a-long-enough-password")
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


class TestWhatCountsAsAWrittenCall:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            # The OpenAI call shape, written out in a fence.
            ('```json\n{"name": "catia_pad", "arguments": {"length_mm": 10}}\n```', "catia_pad"),
            # Nested under "function", as the wire format has it.
            (
                '{"function": {"name": "create_project", "arguments": {"name": "Bracket"}}}',
                "create_project",
            ),
            # Harmony channel header leaking into visible content -- gpt-oss.
            ("<|channel|>commentary to=functions.catia_new_part<|message|>{}", "catia_new_part"),
            # The tag family used by Qwen, Hermes and most llama.cpp templates.
            (
                '<tool_call>\n{"name": "run_simulation", "arguments": {}}\n</tool_call>',
                "run_simulation",
            ),
            # Written as source.
            ('I will now call catia_pad({"length_mm": 10}) to extrude it.', "catia_pad"),
            # Alternative key spellings a model reaches for.
            ('{"tool": "catia_pad", "parameters": {"length_mm": 10}}', "catia_pad"),
        ],
    )
    def test_it_is_caught(self, text: str, expected: str) -> None:
        assert expected in find_written_tool_calls(text, KNOWN)

    def test_nested_arguments_survive_the_scan(self) -> None:
        # A non-greedy regex would stop at the first `}` and miss the call
        # entirely -- and nested arguments are exactly what the CATIA and
        # simulation tools take.
        text = '{"name": "run_simulation", "arguments": {"load_case": {"material": {"name": "steel"}}}}'
        assert find_written_tool_calls(text, KNOWN) == ["run_simulation"]

    def test_several_calls_are_all_reported(self) -> None:
        text = (
            '{"name": "catia_new_part", "arguments": {}}\n'
            '{"name": "catia_pad", "arguments": {"length_mm": 10}}'
        )
        assert find_written_tool_calls(text, KNOWN) == ["catia_new_part", "catia_pad"]

    def test_an_unparseable_tag_still_counts(self) -> None:
        # The body is mangled, but nothing about `<tool_call>` reads as prose.
        assert find_written_tool_calls("<tool_call> pad the sketch </tool_call>", KNOWN)


class TestOrdinaryProseIsLeftAlone:
    """Every one of these is a correct final answer. None may be retried."""

    @pytest.mark.parametrize(
        "text",
        [
            "I'll use catia_pad to extrude the profile to 10 mm.",
            "The peak von Mises stress is 5.62 MPa, giving a factor of safety of 65.8.",
            "run_simulation failed because the geometry has no mesh yet.",
            "Call list_projects if you want to see the others.",
            # A tool *result* quoted back. It contains "name", which is why the
            # detector requires an arguments key alongside it.
            'The part came back as {"name": "Bracket", "mass_kg": 0.7496}.',
            "I created the project and padded it to 10 mm. Anything else?",
            "",
            "   ",
        ],
    )
    def test_no_correction_is_triggered(self, text: str) -> None:
        assert find_written_tool_calls(text, KNOWN) == []

    def test_a_hallucinated_name_is_not_a_written_call(self) -> None:
        # `catia_list_projects` does not exist. That is a different failure with
        # its own handling in ToolBox.call; treating it as a written call would
        # send the loop chasing a tool that is not there.
        text = '{"name": "catia_list_projects", "arguments": {}}'
        assert find_written_tool_calls(text, KNOWN) == []


class TestTheCorrectionIsActionable:
    def test_it_names_the_tool(self) -> None:
        assert "catia_pad" in correction_for(["catia_pad"])

    def test_it_says_nothing_ran(self) -> None:
        # The whole point: the model has to know the user was not told anything,
        # or it follows up as though the work were done.
        assert "nothing ran" in correction_for(["catia_pad"]).lower()

    def test_it_works_without_a_name(self) -> None:
        assert correction_for([""]).strip()


def _toolbox(db_session: Session, user: User, project: Project) -> ToolBox:
    return ToolBox(db=db_session, user=user, project_id=project.id)


class TestTheLoopCorrectsInsteadOfAnswering:
    def _run(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        turns: list[AssistantTurn],
    ) -> Any:
        provider = ScriptedProvider(turns)
        reply = run_agent(
            db=db_session,
            provider=provider,
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="Pad the sketch to 10 mm.",
            user=user,
            allow_mutations=True,
        )
        return provider, reply

    def test_the_written_call_is_not_shown_as_the_answer(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        faked = AssistantTurn(
            text='Project created.\n```json\n{"name": "create_project", "arguments": {"name": "X"}}\n```',
            usage=TokenUsage(1, 1),
        )
        _, reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [faked, AssistantTurn(text="Padded to 10 mm.", usage=TokenUsage(1, 1))],
        )
        assert reply.text == "Padded to 10 mm."

    def test_the_model_is_told_what_it_did_wrong(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        faked = AssistantTurn(
            text='{"name": "catia_pad", "arguments": {"length_mm": 10}}', usage=TokenUsage(1, 1)
        )
        provider, _ = self._run(
            db_session,
            user,
            project,
            conversation,
            [faked, AssistantTurn(text="Done.", usage=TokenUsage(1, 1))],
        )
        assert "catia_pad" in provider.last_user_text
        assert "written as text" in provider.last_user_text

    def test_a_real_tool_call_is_never_corrected(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        # Narration alongside a genuine call is normal and must pass straight
        # through, even when the narration quotes the arguments.
        real = AssistantTurn(
            text='Calling list_projects({"limit": 5}) now.',
            tool_calls=[ToolCall(id="c1", name="list_projects", arguments={})],
            usage=TokenUsage(1, 1),
        )
        _, reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [real, AssistantTurn(text="You have 1 project.", usage=TokenUsage(1, 1))],
        )
        assert [step.tool for step in reply.steps] == ["list_projects"]
        assert reply.text == "You have 1 project."

    def test_a_model_that_will_not_stop_is_told_nothing_ran(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        faked = AssistantTurn(
            text='Project created.\n{"name": "create_project", "arguments": {"name": "X"}}',
            usage=TokenUsage(1, 1),
        )
        _, reply = self._run(
            db_session, user, project, conversation, [faked] * (MAX_CORRECTIONS + 1)
        )
        # It claimed "Project created". The user has to be told it was not.
        assert "nothing has actually been done" in reply.text.lower()

    def test_no_project_was_created_by_the_written_call(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        from sqlalchemy import func, select

        faked = AssistantTurn(
            text='{"name": "create_project", "arguments": {"name": "Ghost"}}',
            usage=TokenUsage(1, 1),
        )
        before = db_session.scalar(select(func.count()).select_from(Project))
        self._run(db_session, user, project, conversation, [faked] * (MAX_CORRECTIONS + 1))
        after = db_session.scalar(select(func.count()).select_from(Project))
        # Parsing a call out of prose and running it would skip argument
        # validation entirely. It is not run -- it is corrected.
        assert after == before


class TestAnEmptyTurnIsNotAnAnswer:
    """gpt-oss returns this when its whole budget goes on internal reasoning."""

    def _run(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        turns: list[AssistantTurn],
    ) -> Any:
        return run_agent(
            db=db_session,
            provider=ScriptedProvider(turns),
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="How thick is it?",
            user=user,
            allow_mutations=True,
        )

    def test_it_retries_rather_than_showing_a_blank_reply(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [
                AssistantTurn(text="", usage=TokenUsage(1, 1)),
                AssistantTurn(text="10 mm.", usage=TokenUsage(1, 1)),
            ],
        )
        assert reply.text == "10 mm."

    def test_a_persistently_silent_model_says_so(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="   ", usage=TokenUsage(1, 1))] * (MAX_CORRECTIONS + 1),
        )
        assert reply.text.strip()
        assert "did not manage" in reply.text

    def test_the_empty_bubble_never_lands_in_the_transcript_as_the_answer(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        self._run(
            db_session,
            user,
            project,
            conversation,
            [
                AssistantTurn(text="", usage=TokenUsage(1, 1)),
                AssistantTurn(text="10 mm.", usage=TokenUsage(1, 1)),
            ],
        )
        db_session.flush()
        final = [
            message
            for message in conversation.messages
            if message.role is MessageRole.ASSISTANT and not message.tool_calls
        ]
        assert final[-1].content == "10 mm."


class TestAnEmptyJsonHuskIsNotAnAnswerEither:
    """Observed live, and it is the last line the user actually saw.

    A CATIA session ended with the assistant's final reply being the two
    characters `{}`. `not text.strip()` is true for whitespace and false for
    that, so the loop read it as the model's considered answer, wrote it to the
    transcript and closed the turn. The user's chat ends with an empty JSON
    object where the explanation of what was built should have been.

    It is handled as *blank* rather than as a written tool call because it names
    no tool and describes no work -- the right correction is "you said nothing,
    say something", which the empty-turn path already sends.
    """

    def _run(
        self,
        db_session: Session,
        user: User,
        project: Project,
        conversation: Conversation,
        turns: list[AssistantTurn],
    ) -> Any:
        return run_agent(
            db=db_session,
            provider=ScriptedProvider(turns),
            conversation=conversation,
            toolbox=_toolbox(db_session, user, project),
            user_message="Design an M8 50 mm hex bolt.",
            user=user,
            allow_mutations=True,
        )

    def test_a_bare_empty_object_is_retried_rather_than_shown(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [
                AssistantTurn(text="{}", usage=TokenUsage(1, 1)),
                AssistantTurn(text="The bolt is modelled.", usage=TokenUsage(1, 1)),
            ],
        )
        assert reply.text == "The bolt is modelled."

    def test_a_model_that_only_ever_emits_a_husk_says_so_plainly(
        self, db_session: Session, user: User, project: Project, conversation: Conversation
    ) -> None:
        reply = self._run(
            db_session,
            user,
            project,
            conversation,
            [AssistantTurn(text="{}", usage=TokenUsage(1, 1))] * (MAX_CORRECTIONS + 1),
        )
        assert "{}" not in reply.text
        assert "did not manage to produce an answer" in reply.text


class TestContentlessDetection:
    """The predicate on its own. A false positive here retries a good answer."""

    @pytest.mark.parametrize(
        "text",
        ["", "   ", "\n\n", "{}", "[]", "null", "{ }", '""', "```json\n{}\n```", "```\n{}\n```"],
    )
    def test_a_husk_is_contentless(self, text: str) -> None:
        assert is_contentless(text)

    @pytest.mark.parametrize(
        "text",
        [
            "10 mm.",
            "The bolt is modelled.",
            'I used {"name": "Bracket"} for the part.',
            "{} is an empty object in JSON, which is what your file contains.",
            '{"doc_name": "Bolt.CATPart"}',
            "0",
        ],
    )
    def test_a_real_answer_is_not(self, text: str) -> None:
        """Including one that merely *contains* JSON, or explains a `{}`.

        Treating every JSON-shaped reply as empty would retry good answers, so
        the rule is a small closed set of whole-body husks, not "does this parse".
        """
        assert not is_contentless(text)
