"""When Kryova needs a person: the decision, its options, and no default.

**The user's request, 2026-09-22:** *"add the option that kryova tells the user to
intervene when it needs a user decision -- this should be shown as a message on the app for
the user to decide what to do."*

The agent already knew when it needed a person. It had two ways of saying so and neither
one put a decision in front of anybody:

* `recovery.escalation()` (E16.4) builds a specific question -- subject, cause, two options
  -- and the turn **appends it to the end of the answer as prose**. It is correct and it is
  invisible: it renders as the last paragraph of a long reply, in the same type as the
  rest, and the frontend's banner says only *"the question is at the end of its answer"*.
* `core/gates.py` (P5.5) raises an `ApprovalGate`, which is a row, correctly, for the
  reasons that module gives. But the turn ends and the conversation says *"approve or
  reject it under Approvals"* -- a different page, reached by navigating away from the thing
  being approved.

So a person who asked for a bracket and got a question had to read for it, and a person
who hit a checkpoint had to leave. This module is the third thing: **the decision as
data**, so the conversation can render it as a decision -- the question, what it is about,
and the options as things you press.

## What an Intervention is, and the four rules it enforces

1. **It carries options, and never a default.** No `recommended`, no `preferred`, no
   pre-selection. This is the rule `optimise/screening.py` states for `ParetoFront` ("a
   front is a choice for a person") and `gates.py` states for `EXPIRED` ("filling
   `decided_by_id` would put a name against a decision nobody made"). A default on a
   screen like this one is not a convenience, it is the product answering its own
   question and recording a person's name against it.
2. **A typed answer is always accepted**, and `answer_in_words` is a property rather than
   a field so that no construction can switch it off. A fixed option list that does not
   contain the real answer is worse than no options at all: it stops being a question and
   becomes a form, and the user picks the closest wrong one because that is what forms
   teach. The options are the common answers, not the possible ones.
3. **Two choices at minimum**, refused at construction. A "decision" with one option is an
   announcement wearing a button.
4. **Nothing here calls a model.** The same rule, and the same reason, as `recovery.py`:
   the question is built from the tool's own error text and the call's own arguments, both
   already in hand. A paraphrase between the user and the failure would be put on the one
   screen where the exact wording is the evidence -- and it would cost a model call at the
   moment the turn has already gone wrong.

## What it does not do

**It does not replace the prose.** The turn still appends `recovery.escalation()` to the
answer, and that is deliberate: `ConversationMessage` is what survives a reload, a resume
gap and the transcript window, and an intervention that existed only as a live event would
be a question that disappears when somebody refreshes. The structured form is the *surface*;
the sentence in the transcript is the *record*. They are built from one `Failure` so they
cannot disagree about what is being asked.

**It does not decide anything.** Answering an approval intervention goes to
`gates.decide`, which re-digests the subject and refuses if it has moved -- this module
carries the `gate_id` so the surface knows where to send the answer, and carries no
authority of its own.

**One gap, stated rather than left to be discovered.** `for_gate` is covered at the module
level and the *repeated-failure* path is covered end to end through `stream_agent`
(`tests/test_ai_intervention.py::TestTheTurnActuallyEmitsIt`). The **approval** path is
not driven end to end, because raising a real gate needs `request_approval` to run against
an organisation, a subject and a digest. So "a checkpoint puts a decision on the stream" is
believed here and not measured -- which is exactly the distinction `CLAUDE.md`'s testing
item 8 says to write down rather than assume, since both defects of that class shipped
green. It is a task, not a footnote: drive `request_approval` through `stream_agent` and
assert the `intervention` event carries that gate's id.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.ai import recovery as _recovery


class InterventionKind(StrEnum):
    """Why a person is being asked, which decides what the options are.

    Named for what the *user* has to decide, never for the layer that stopped -- the same
    rule `recovery.py`'s failure kinds follow, and for the same reason: "the seat is not
    there" and "the argument is wrong" need different people.
    """

    #: A tool failed the same way three times. `recovery.MAX_SAME_FAILURE` is the bound.
    REPEATED_FAILURE = "repeated-failure"
    #: A task marked `checkpoint: true` raised an approval gate and the turn ended.
    APPROVAL = "approval"


@dataclass(frozen=True)
class Choice:
    """One answer the user can give by pressing something.

    `detail` is not decoration. A button saying "Carry on" is a promise whose content the
    user cannot see, and the whole failure mode this module exists to avoid is somebody
    answering a question they have not understood.
    """

    id: str
    label: str
    detail: str
    #: True where choosing this needs words as well -- a gate rejection carries a reason
    #: (`gates.py` rule 2), because the agent's next move depends entirely on why.
    needs_reason: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "detail": self.detail,
            "needs_reason": self.needs_reason,
        }


#: Offered on every intervention, and added by `__post_init__` rather than by each
#: builder remembering to. Stopping is a legitimate answer to any question the product
#: can ask, and a decision screen with no way out is a screen that traps somebody who
#: wanted to think about it.
STOP = Choice(
    id="stop",
    label="Stop here",
    detail="Keep everything that is built and end the turn. Nothing is undone.",
)


@dataclass(frozen=True)
class Intervention:
    """A decision Kryova cannot take, put where the person already is.

    Every field is meant to be read by somebody who has not been watching: `question` is
    what to decide, `subject` is what it is about, `cause` is why it stopped, and `quote`
    is the machine's own words, verbatim and never summarised.
    """

    kind: InterventionKind
    question: str
    cause: str
    choices: tuple[Choice, ...]
    subject: str = ""
    tool: str = ""
    quote: str = ""
    #: Where an answer goes, for the approval kind. `None` for everything else, which the
    #: surface reads as "reply in the conversation".
    gate_id: str | None = None

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError(
                "An intervention must state what is being decided. A prompt with no "
                "question is a notification, and notifications do not belong on this "
                "surface."
            )
        offered = tuple(self.choices)
        if not any(choice.id == STOP.id for choice in offered):
            object.__setattr__(self, "choices", (*offered, STOP))
        if len({choice.id for choice in self.choices}) != len(self.choices):
            raise ValueError("Two choices share an id, so an answer would be ambiguous.")
        if len(self.choices) < 3:
            # Two real options plus STOP. Fewer than two real ones is an announcement.
            raise ValueError(
                "An intervention offers at least two options besides stopping. One option "
                "is not a decision, and presenting it as one asks somebody to take "
                "responsibility for a choice they were never given."
            )

    @property
    def answer_in_words(self) -> bool:
        """Always true, and a property so that no construction can make it false.

        The options are the *common* answers, never the possible ones. A list that cannot
        be escaped turns a question into a form, and a user answering a form picks the
        closest wrong option rather than saying the thing that is actually true.
        """
        return True

    def choice(self, choice_id: str) -> Choice | None:
        return next((one for one in self.choices if one.id == choice_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "question": self.question,
            "cause": self.cause,
            "subject": self.subject,
            "tool": self.tool,
            "quote": self.quote,
            "gate_id": self.gate_id,
            "answer_in_words": self.answer_in_words,
            "choices": [choice.to_dict() for choice in self.choices],
        }


#: The options per failure kind, in `recovery.py`'s taxonomy. Each pair is the fork the
#: question in `recovery._QUESTIONS` already poses -- written as things to press rather
#: than re-worded, so the button and the sentence in the transcript cannot drift apart.
_CHOICES: Final[dict[str, tuple[Choice, ...]]] = {
    _recovery.MISSING_SUBJECT: (
        Choice(
            "work-from-what-exists",
            "Work from what is there",
            "Carry on using what the document actually contains now, and leave the "
            "missing thing out.",
        ),
        Choice(
            "it-moved",
            "Something was renamed or deleted",
            "Tell me what it is now and I will use that instead of looking for the old "
            "name.",
            needs_reason=True,
        ),
    ),
    _recovery.BAD_ARGUMENT: (
        Choice(
            "give-the-value",
            "Give me the value",
            "Type the value you expect and I will use it instead of guessing.",
            needs_reason=True,
        ),
        Choice(
            "different-approach",
            "Try another way",
            "Reach the same result with a different operation rather than fixing this "
            "call.",
        ),
    ),
    _recovery.UNAVAILABLE: (
        Choice(
            "wait",
            "Wait for the workstation",
            "Hold this step until the seat answers, and run it then.",
        ),
        Choice(
            "carry-on-without",
            "Carry on without it",
            "Do everything that does not need the workstation, and leave the rest "
            "listed as not done.",
        ),
    ),
    _recovery.REFUSED: (
        Choice(
            "change-the-request",
            "Change what I am asking for",
            "Narrow or alter the request so the refusal no longer applies.",
            needs_reason=True,
        ),
        Choice(
            "approve-as-it-stands",
            "It is right as it stands",
            "Confirm the request is what you want, so the refusal is the thing to work "
            "around.",
        ),
    ),
    _recovery.GEOMETRY: (
        Choice(
            "change-the-geometry",
            "Change the shape",
            "Alter the geometry so the operation fits, and tell you what moved.",
        ),
        Choice(
            "keep-the-shape",
            "Keep the shape",
            "Leave the geometry alone and drop the operation, recorded as not applied.",
        ),
    ),
    _recovery.UNCLASSIFIED: (
        Choice(
            "i-know-what-this-means",
            "I know what this means",
            "Tell me what to do about it in your own words.",
            needs_reason=True,
        ),
        Choice(
            "different-approach",
            "Try another way",
            "Attempt the same goal by a different route and see whether it lands.",
        ),
    ),
}


def from_recovery(recovery: _recovery.Recovery) -> Intervention | None:
    """The decision a stuck turn owes its user, or `None` if it is not stuck.

    `None` rather than an empty intervention, for `escalation()`'s reason: a surface that
    renders a decision prompt on a turn that went fine is a nag, and a product that nags
    teaches people to dismiss the prompt that matters.
    """
    if recovery.exhausted() is None:
        return None
    failure = recovery.worst()
    if failure is None:
        return None

    count = recovery.repeats()[(failure.tool, failure.kind)]
    subject = failure.subject
    named = f" on {subject}" if subject else ""
    return Intervention(
        kind=InterventionKind.REPEATED_FAILURE,
        question=_recovery.QUESTIONS[failure.kind],
        cause=(
            f"`{failure.tool}`{named} failed {count} times and each time "
            f"{failure.because}."
        ),
        subject=subject,
        tool=failure.tool,
        # Verbatim, and truncated visibly if at all -- `recovery.one_line`'s contract.
        # This is the one place the exact wording is the evidence.
        quote=_recovery.one_line(failure.message),
        choices=_CHOICES[failure.kind],
    )


def for_gate(gate_id: str, *, summary: str, subject: str = "") -> Intervention:
    """The decision an approval gate is waiting on, rendered where the work is.

    The gate itself stays the record -- this only carries `gate_id` so the surface knows
    where to send the answer. `gates.decide` re-digests the subject and refuses if it has
    moved, which is what makes the signature mean something, and nothing here weakens it.
    """
    return Intervention(
        kind=InterventionKind.APPROVAL,
        question="This is a checkpoint. Approve it to carry on, or reject it and say why.",
        cause=summary,
        subject=subject,
        gate_id=gate_id,
        choices=(
            Choice(
                "approve",
                "Approve",
                "Sign this off and let the turn carry on from here.",
            ),
            Choice(
                "reject",
                "Reject",
                "Stop here and say what is wrong -- the reason decides what happens next.",
                needs_reason=True,
            ),
        ),
    )


def from_failures(failures: Sequence[Mapping[str, Any]]) -> Intervention | None:
    """One-shot form for a caller holding raw failure records rather than a `Recovery`."""
    recovery = _recovery.Recovery(
        [
            _recovery.Failure(
                tool=str(one.get("tool", "")),
                message=str(one.get("message", "")),
                arguments=dict(one.get("arguments", {}) or {}),
            )
            for one in failures
        ]
    )
    return from_recovery(recovery)


__all__ = [
    "STOP",
    "Choice",
    "Intervention",
    "InterventionKind",
    "for_gate",
    "from_failures",
    "from_recovery",
]
