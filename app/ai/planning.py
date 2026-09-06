"""What the engineer actually asked for, held apart from the conversation.

**Scope, honestly: this is the seam for master plan 16.2 and one first step of
it. It is not long-horizon planning, and nothing here sequences tools, chooses
an order, or replans after a failure.** What it does is turn a request into the
list of requirements it contains, and keep that list somewhere the context
window cannot trim. Anything more is 16.2 proper.

**Wired 2026-09-07**, and it is still not a tool: no schema was added to the
payload 16.1 is busy shrinking, and no prompt describes a call the model was not
given. Three consumers read it, all of them on the server. `state.py` renders
the requirements beside the user's message; `verification.py` decides which of
them the conversation's own measurements confirm; `agent.py` refuses to let a
turn close while a stated number has been measured by nothing. The extraction
here is regex over the engineer's own words, so all of that costs one model call
of nothing.

**Why this is the missing piece, measured rather than asserted.** On 2026-09-06
(`docs/verification-2026-09-06/REPORT.md`, attempt 3) the agent was asked for a
plate with six stated requirements. It built three of them, never pocketed the
sketch holding the other three, and closed with *"11.5 mm, 2.459 kg, within 20
grams of 2.4 kg"* — which was 59 grams out. Fourteen of fifteen tool calls
returned `ok`. Nothing in the system was holding the list of what had been
asked for, so nothing could notice that two thirds of it had not been done.

`app/ai/resume.py` answers *what was done* by reading `CatiaOperation`, because
the transcript trims and the summary is a paraphrase. This is the other half:
*what was asked for*, from the one turn where the engineer said it, kept in a
form that survives the same trimming. The two are complements, not duplicates —
one is a log of calls, the other a list of requirements, and neither can be
derived from the other.

**Nothing here converts a unit.** The whole codebase is mm-N-MPa and reads a
number in the coordinates it was written in; a plan is a record of what the
engineer said, so "2.4 kg" and "20 grams" are stored exactly as written, side
by side, with their units. Making them comparable is the consumer's job — for
`check_part` that means an assertion in the measurement's own unit — and doing
it here would put a conversion in the one layer that is supposed to be a
verbatim record.

**An objective that was never checked is not met.** `Plan.brief()` counts
confirmed, outstanding and missed separately and never folds "not checked" into
"fine" — the rule `assertions.py` applies to an unmeasured assertion and
`VisualReview` applies to a check that could not run, applied here to a
requirement nobody looked at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

#: Units worth recognising, mapped to what kind of requirement they make.
#: Recorded, never converted — see the module docstring.
_UNIT_KIND: Final[dict[str, str]] = {
    "mm": "length",
    "cm": "length",
    "m": "length",
    "in": "length",
    "inch": "length",
    "inches": "length",
    "thou": "length",
    "kg": "mass",
    "kgs": "mass",
    "g": "mass",
    "gram": "mass",
    "grams": "mass",
    "gramme": "mass",
    "grammes": "mass",
    "lb": "mass",
    "lbs": "mass",
    "t": "mass",
    "deg": "angle",
    "degree": "angle",
    "degrees": "angle",
    "rad": "angle",
    "n": "force",
    "kn": "force",
    "mpa": "stress",
    "gpa": "stress",
    "bar": "stress",
}

#: Counting words, because "four 12 mm clearance holes" states a count of four
#: and a diameter of twelve, and the count is the half that gets silently
#: dropped. Measured: three of the five recorded rung-3 runs built the wrong
#: number of holes, and in one of them the count was never mentioned again
#: after the first turn.
_NUMBER_WORDS: Final[dict[str, float]] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
    "a pair of": 2, "a couple of": 2,
}

#: Verbs that mean "build something". A clause carrying one is a requirement
#: even when it names no number — "with a chamfer on the top edge" has to be
#: done and has nothing to measure.
_BUILD_VERBS: Final[frozenset[str]] = frozenset(
    {
        "make", "build", "create", "add", "cut", "drill", "bore", "pad",
        "pocket", "extrude", "fillet", "chamfer", "round", "shell", "draft",
        "pattern", "mirror", "thread", "tap", "groove", "rib", "stiffen",
        "design", "model", "hollow", "trim", "split", "revolve", "sweep",
        "loft", "with", "weigh", "weighs", "carry", "hold", "clear", "fit",
    }
)

#: Clause boundaries. Deliberately crude: a requirement list written by an
#: engineer is punctuated prose, not a grammar, and a parser that tried harder
#: would be confidently wrong in a way this cannot be. What matters is that the
#: number and the noun beside it stay in the same clause.
#:
#: Both the full stop and the comma are guarded against digits on either side.
#: `2.4 kg` and `1,200 mm` are one number, and splitting inside one turns a
#: single requirement into two nonsense ones — measured on the rung-3 prompt,
#: which produced the objectives "It has to weigh 2" and "4 kg".
_SPLIT_RE: Final = re.compile(
    r"[;\n]|(?<!\d)\.(?!\d)|(?<!\d),(?!\d)|\band\b|\bthen\b|\bplus\b",
    re.IGNORECASE,
)

#: `12 mm`, `2.4 kg`, `70mm`, `0.5 in`. The unit is optional in the pattern and
#: checked against `_UNIT_KIND` afterwards, so `12 clearance` does not become a
#: quantity in an invented unit called "clearance".
_QUANTITY_RE: Final = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*([a-zA-Z]{1,7})?(?![\w.])"
)

#: "within 20 grams of", "+/- 0.1 mm", "to within 2%". A tolerance is the part
#: of a requirement that decides whether it was met, and it is the part a model
#: paraphrasing its own answer drops first.
_TOLERANCE_RE: Final = re.compile(
    r"(?:within|to within|\+/-|±|plus or minus)\s*(\d+(?:\.\d+)?)\s*([a-zA-Z%]{1,7})?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Quantity:
    """A number the engineer wrote, in the unit they wrote it in.

    `unit` is lower-cased and otherwise verbatim, and it is only ever a unit
    this module recognises: a word that follows a number is not made into a unit
    on the strength of following a number. "add 6 ribs" is a count of six, not
    six in a unit called ribs. `kind` is what the unit measures, or `"count"`.

    The word is not lost by that — `Objective.text` is the clause verbatim, so a
    human reading the plan still sees "6 ribs". What is avoided is a fabricated
    unit, which is a number that looks checkable and is not.
    """

    value: float
    unit: str = ""
    kind: str = "count"

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit}".strip()


@dataclass(frozen=True, slots=True)
class Objective:
    """One requirement, in the engineer's own words.

    `text` is the clause as written — not a normalisation, not a paraphrase.
    The point of this record is that it is quotable back at the user, and a
    paraphrase of a requirement is how a requirement quietly changes.
    """

    text: str
    #: Every number the clause names, in reading order.
    quantities: tuple[Quantity, ...] = ()
    #: The slack the clause allows, when it states one.
    tolerance: Quantity | None = None
    #: Where in the request it was stated, so a plan reads in the order it was
    #: asked for rather than in the order a regex happened to match.
    position: int = 0

    @property
    def measurable(self) -> bool:
        """True when the clause names a number something could be checked against.

        A clause with no number is still a requirement — "with a chamfer on the
        top edge" has to be built — it just cannot be *verified* by a number, and
        the distinction is worth keeping: those are the ones that need a picture.
        """
        return bool(self.quantities)


@dataclass
class Plan:
    """The requirements of one request, and what is known about each.

    Three states, never two. An objective is confirmed only when something
    measured it; `missed` is for one measured and found wrong; everything else
    is outstanding, including everything nobody looked at. Folding "not checked"
    into "fine" is the failure `check_part` exists to prevent, and a plan that
    reported "6 of 6" because nothing had contradicted it would reintroduce it
    one layer up.
    """

    objectives: tuple[Objective, ...] = ()
    #: Indices into `objectives`. Sets rather than flags on the objective so an
    #: `Objective` stays frozen and quotable.
    confirmed: set[int] = field(default_factory=set)
    missed: set[int] = field(default_factory=set)

    def __len__(self) -> int:
        return len(self.objectives)

    def mark_confirmed(self, index: int) -> None:
        """Record that objective `index` was measured and found right."""
        if 0 <= index < len(self.objectives):
            self.missed.discard(index)
            self.confirmed.add(index)

    def mark_missed(self, index: int) -> None:
        """Record that objective `index` was measured and found wrong."""
        if 0 <= index < len(self.objectives):
            self.confirmed.discard(index)
            self.missed.add(index)

    def outstanding(self) -> list[Objective]:
        """Everything not yet measured, in the order it was asked for."""
        decided = self.confirmed | self.missed
        return [obj for index, obj in enumerate(self.objectives) if index not in decided]

    def brief(self) -> str:
        """A few lines for a state block, or an empty string when there is nothing.

        Written for a model to read beside the user's message: it names the
        outstanding requirements rather than counting them, because a count is
        not actionable and the whole point is that the model can see which
        specific thing it has not done yet.
        """
        if not self.objectives:
            return ""
        lines = [
            f"What was asked for ({len(self.confirmed)} of {len(self.objectives)} "
            f"confirmed by measurement, {len(self.missed)} measured and wrong):"
        ]
        for index, objective in enumerate(self.objectives):
            if index in self.confirmed:
                mark = "confirmed"
            elif index in self.missed:
                mark = "MEASURED AND WRONG"
            else:
                mark = "not checked"
            lines.append(f"  - {objective.text} [{mark}]")
        if self.outstanding():
            lines.append(
                "Not checked is not the same as fine. Measure what is outstanding "
                "before saying the part is done."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objectives": [
                {
                    "text": objective.text,
                    "quantities": [str(quantity) for quantity in objective.quantities],
                    "tolerance": str(objective.tolerance) if objective.tolerance else None,
                    "measurable": objective.measurable,
                    "state": (
                        "confirmed"
                        if index in self.confirmed
                        else "missed"
                        if index in self.missed
                        else "outstanding"
                    ),
                }
                for index, objective in enumerate(self.objectives)
            ],
            "confirmed": len(self.confirmed),
            "missed": len(self.missed),
            "outstanding": len(self.outstanding()),
        }


def _quantities(clause: str) -> tuple[Quantity, ...]:
    found: list[Quantity] = []
    lowered = clause.lower()
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            found.append(Quantity(value, "", "count"))
    for match in _QUANTITY_RE.finditer(clause):
        value = float(match.group(1))
        unit = (match.group(2) or "").lower()
        # A word after a number is a unit only when it is one. "6 ribs" is a
        # count of six ribs, not six in a unit called ribs — and inventing the
        # unit is worse than not having it, because the clause text is verbatim
        # and a human reading the plan sees the word anyway.
        if unit in _UNIT_KIND:
            found.append(Quantity(value, unit, _UNIT_KIND[unit]))
        else:
            found.append(Quantity(value, "", "count"))
    return tuple(found)


def _tolerance(clause: str) -> Quantity | None:
    match = _TOLERANCE_RE.search(clause)
    if match is None:
        return None
    unit = (match.group(2) or "").lower()
    if unit == "%":
        return Quantity(float(match.group(1)), unit, "percent")
    if unit in _UNIT_KIND:
        return Quantity(float(match.group(1)), unit, _UNIT_KIND[unit])
    return Quantity(float(match.group(1)), "", "count")


def extract_objectives(message: str) -> tuple[Objective, ...]:
    """The requirements stated in one request, in the order they were stated.

    A clause counts as a requirement when it names a number or carries a build
    verb. Everything else — pleasantries, context, the reason for the part — is
    dropped, because a plan padded with prose is a plan nobody reads.

    Deliberately conservative in the other direction too: it does not try to
    resolve pronouns, merge clauses, or work out that "one 20 mm in from each
    corner" refers to the holes in the previous clause. Those are 16.2 proper.
    What it guarantees is that a stated number ends up in the list beside the
    words it qualified, which is the thing that was being lost.
    """
    if not message or not message.strip():
        return ()

    objectives: list[Objective] = []
    for position, raw in enumerate(_SPLIT_RE.split(message)):
        clause = " ".join(raw.split()).strip(" -:—")
        if not clause:
            continue
        quantities = _quantities(clause)
        words = {word.strip(".,;:").lower() for word in clause.split()}
        if not quantities and not (words & _BUILD_VERBS):
            continue
        objectives.append(
            Objective(
                text=clause,
                quantities=quantities,
                tolerance=_tolerance(clause),
                position=position,
            )
        )
    return tuple(objectives)


def plan_for(message: str) -> Plan:
    """A `Plan` over what `message` asked for. The one entry point worth calling."""
    return Plan(objectives=extract_objectives(message))


__all__ = [
    "Objective",
    "Plan",
    "Quantity",
    "extract_objectives",
    "plan_for",
]
