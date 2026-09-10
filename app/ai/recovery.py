"""Diagnose, repair, retry within a bound, then escalate with a *specific* question.

E16 task 4. Three of the four were already there and are behavioural: the agent
refuses a read repeated verbatim (`MAX_IDENTICAL_READS`), refuses a write already
refused (`_refused_before`), refuses opening empty documents as though it were
progress (`MAX_EMPTY_DOCUMENTS`), and ends a turn after a few of any of them.
Those bound the retry. What was missing is the last word: **escalate with a
specific question** — and the difference between that and what a bounded loop
does on its own is the whole reason this module exists.

A turn that ends on a guard currently says the agent stopped repeating itself.
That is true and it is not answerable. The user reads "the agent kept repeating
a call that had already been refused" and has no idea what to type next. What
they can act on is *"the pad at `plate.body` failed three times because the
sketch it extrudes is open — should I close the profile, or is that gap
deliberate?"* — a question with a subject, a cause and two options.

**Nothing here calls a model.** Escalation is built from the tool's own error
text and the arguments the call was made with, both of which are already in
hand. Asking an LLM to write the question would put a paraphrase between the
user and the failure, on the one screen where the exact wording is the evidence
— and it would cost a model call at the moment the turn has already gone wrong.

**A failure it cannot classify escalates anyway, quoting verbatim.** The same
contract `app/solve/calculix/diagnose.py` holds: `classified` is False when
nothing matched, and the unrecognised text comes back whole rather than
summarised into a shrug. A taxonomy that labelled everything would destroy the
evidence the next pattern is written from.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

#: How many times one tool may fail *in the same way* before the turn escalates
#: rather than trying again. Three: one is noise, two can be a race, three is a
#: reason. Lower than the step budget on purpose — the budget bounds a turn that
#: is working, and this bounds one that is not.
MAX_SAME_FAILURE: Final = 3

#: Failure kinds. Named for what the *user* has to decide about, not for the
#: layer that raised — "the seat is not there" and "the argument is wrong" need
#: different people, and that is what the split is for.
MISSING_SUBJECT: Final = "missing-subject"
BAD_ARGUMENT: Final = "bad-argument"
UNAVAILABLE: Final = "unavailable"
REFUSED: Final = "refused"
GEOMETRY: Final = "geometry"
UNCLASSIFIED: Final = "unclassified"

#: Ordered most specific first, and the order is significant for the same
#: reason it is in the CalculiX taxonomy: "no such parameter" is also a bad
#: argument, and the useful instruction is always the specific one.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str], str], ...]] = (
    (
        MISSING_SUBJECT,
        re.compile(
            r"\b(no (such|feature|parameter|sketch|document|project)|not found|does not exist"
            r"|no (?:\w+ )?called)\b",
            re.IGNORECASE,
        ),
        "it names something that is not there",
    ),
    (
        UNAVAILABLE,
        re.compile(
            r"\b(not (reachable|available|running|connected)|no workstation|unavailable"
            r"|timed out|timeout|connection refused|is asleep)\b",
            re.IGNORECASE,
        ),
        "the thing it needs is not reachable",
    ),
    (
        REFUSED,
        re.compile(
            # `over the [...] limit` with the middle optional: the real message
            # is "over the 400,000 limit" and the shortest form is "over the
            # limit", and a pattern requiring something between matches only
            # the first — which is the way a taxonomy silently stops covering
            # the wording it was written for.
            r"\b(refus\w+|not permitted|forbidden|read[- ]only|requires approval"
            r"|over the [\w\s,.]*limit|exceeds)\b",
            re.IGNORECASE,
        ),
        "the system refused it",
    ),
    (
        GEOMETRY,
        re.compile(
            r"\b(open (profile|sketch|contour)|self[- ]intersect\w*|zero (volume|thickness|length)"
            r"|degenerate|non[- ]manifold|cannot be (padded|filleted|cut)|invalid geometry)\b",
            re.IGNORECASE,
        ),
        "the geometry will not take it",
    ),
    (
        BAD_ARGUMENT,
        re.compile(
            r"\b(invalid|must be|expected|unknown (key|argument|option)|missing (argument|required)"
            r"|out of range|cannot be read|not a (number|mapping|string))\b",
            re.IGNORECASE,
        ),
        "an argument is wrong",
    ),
)

#: What to *try* for each kind, in the imperative, addressed to the agent. One
#: line each: a repair suggestion nobody reads is a repair suggestion that does
#: not happen.
_REPAIRS: Final[dict[str, str]] = {
    MISSING_SUBJECT: (
        "List what actually exists before naming it again — read_design, "
        "design_history or list_projects, depending on what went missing."
    ),
    BAD_ARGUMENT: (
        "Re-read the tool's description and send the arguments it asks for. Do not "
        "re-send the same ones."
    ),
    UNAVAILABLE: (
        "Do not retry immediately. Say what is unreachable and carry on with work "
        "that does not need it."
    ),
    REFUSED: (
        "This will not succeed by being repeated. Either change what is being asked "
        "for, or ask the user to decide."
    ),
    GEOMETRY: (
        "Change the geometry rather than the call — the operation is being asked to "
        "do something the shape does not allow."
    ),
    UNCLASSIFIED: (
        "Read the message and change something specific before trying again. A "
        "repeat with nothing changed is the same request."
    ),
}


@dataclass(frozen=True)
class Failure:
    """One tool call that did not work.

    `arguments` is kept because the question at the end has to be able to name
    the *subject*: "the pad failed" is not answerable, "the pad at
    `plate.body` failed" is.
    """

    tool: str
    message: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        for kind, pattern, _ in _PATTERNS:
            if pattern.search(self.message):
                return kind
        return UNCLASSIFIED

    @property
    def classified(self) -> bool:
        return self.kind != UNCLASSIFIED

    @property
    def because(self) -> str:
        """The cause in a clause, for the middle of a sentence."""
        for kind, pattern, clause in _PATTERNS:
            if pattern.search(self.message):
                return clause
        return "it failed and the reason was not one this build recognises"

    @property
    def subject(self) -> str:
        """What the call was about, from its own arguments.

        Best effort and honest about it: an empty string when the arguments name
        nothing recognisable, which the sentence builder handles by leaving the
        subject out rather than by inventing one.
        """
        for key in ("name", "feature", "sketch", "document", "parameter", "target", "id"):
            value = self.arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def repair(self) -> str:
        return _REPAIRS[self.kind]


@dataclass
class Recovery:
    """What has failed in this turn, and whether it is time to stop trying.

    Counts failures **by (tool, kind)** rather than by exact message. Two
    attempts at the same pad that fail with slightly different wording are the
    same problem, and a counter keyed on the message would never reach its
    bound — which is the way a retry budget quietly stops existing.
    """

    failures: list[Failure] = field(default_factory=list)

    def record(self, failure: Failure) -> None:
        self.failures.append(failure)

    def repeats(self) -> Counter[tuple[str, str]]:
        return Counter((failure.tool, failure.kind) for failure in self.failures)

    def exhausted(self) -> tuple[str, str] | None:
        """The (tool, kind) that has failed too many times, or `None`."""
        for key, count in self.repeats().most_common(1):
            if count >= MAX_SAME_FAILURE:
                return key
        return None

    def worst(self) -> Failure | None:
        """The most recent failure of the kind that has repeated most.

        Most recent rather than first: the message from the latest attempt is
        the one describing the state the part is actually in, and an escalation
        quoting the first attempt would describe a problem that may have moved.
        """
        key = self.exhausted()
        if key is None:
            return self.failures[-1] if self.failures else None
        return next(
            (
                failure
                for failure in reversed(self.failures)
                if (failure.tool, failure.kind) == key
            ),
            None,
        )

    def escalation(self) -> str:
        """The specific question to put to the user, or an empty string.

        Empty when nothing has failed enough to be worth asking about — the
        caller renders nothing rather than a question about a turn that went
        fine, which is the difference between an escalation and a nag.
        """
        failure = self.worst()
        if failure is None or self.exhausted() is None:
            return ""
        count = self.repeats()[(failure.tool, failure.kind)]
        subject = f" on {failure.subject}" if failure.subject else ""
        head = (
            f"`{failure.tool}`{subject} failed {count} times and each time "
            f"{failure.because}."
        )
        quote = f' It said: "{_one_line(failure.message)}"'
        return f"{head}{quote} {_QUESTIONS[failure.kind]}"


#: The question itself, per kind. Each names a decision the *user* can make,
#: which is what stops an escalation being a restatement of the error.
_QUESTIONS: Final[dict[str, str]] = {
    MISSING_SUBJECT: (
        "Should I work from what does exist, or has something been renamed or deleted "
        "that I should know about?"
    ),
    BAD_ARGUMENT: (
        "I am sending this wrong. Can you tell me the value you expect, so I stop "
        "guessing at it?"
    ),
    UNAVAILABLE: (
        "Is the workstation meant to be running? I can carry on with everything that "
        "does not need it, or wait."
    ),
    REFUSED: (
        "This needs your decision rather than another attempt — do you want to change "
        "what is being asked for, or approve it as it stands?"
    ),
    GEOMETRY: (
        "The shape will not take this operation as it stands. Should I change the "
        "geometry to make it fit, or is the current shape the one you want?"
    ),
    UNCLASSIFIED: (
        "I do not recognise this failure, so I have quoted it exactly. Does it mean "
        "anything to you, or should I try a different approach?"
    ),
}


def escalate(failures: Sequence[Failure]) -> str:
    """One-shot form, for a caller that already has the list."""
    recovery = Recovery(list(failures))
    return recovery.escalation()


def _one_line(message: str, limit: int = 240) -> str:
    """The message on one line, truncated with the truncation shown.

    Truncated *visibly*: a quote cut off with no mark reads as the whole thing,
    and the whole point of quoting verbatim is that the user can trust the
    quote.
    """
    flattened = " ".join(message.split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: limit - 1] + "…"


__all__ = [
    "BAD_ARGUMENT",
    "GEOMETRY",
    "MAX_SAME_FAILURE",
    "MISSING_SUBJECT",
    "REFUSED",
    "UNAVAILABLE",
    "UNCLASSIFIED",
    "Failure",
    "Recovery",
    "escalate",
]
