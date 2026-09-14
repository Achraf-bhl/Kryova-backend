"""What Kryova is under the Machinery Regulation, what it may produce, and what it
must never produce — master plan E19 task 2, decided in writing.

**The plan's premise was wrong in a way that matters, and reading the annexes
corrected it.** The task said Annex I — the list that triggers third-party
conformity assessment — contains item 18 "software ensuring safety functions"
and item 19 "safety components with fully or partially self-evolving
behaviour". In Regulation (EU) 2023/1230 as published those two are items 18
and 19 of **Annex II, the indicative list of safety components**. Annex I Part A
(the categories under Article 25(2)) carries the self-evolving machine-learning
safety components at items 5 and 6, Annex I Part B's items 18 and 19 are
roll-over and falling-object protective structures, and recital (55) says the
third-party assessment of software ensuring safety functions applies only to
self-evolving machine-learning systems — not to "software incapable of learning
or evolving, and programmed only to execute certain automated functions". Every
one of those is quoted below, from the Official Journal.

**The position, decided.** Kryova is design and analysis software used by a
machine's manufacturer. It is not placed on the market as, or in, a machine; it
is not a safety component on the indicative list; and nothing it produces runs
on the machine — no code path in this repository emits controller logic, PLC
code or firmware, which is checkable by reading the repository rather than
taken on trust. So Kryova is not an Annex I category and not an Annex II safety
component, and the manufacturer of the machine is the manufacturer.

**What that position rests on, stated so it can be attacked.** It holds only
while nothing Kryova generates executes a safety function. The day a feature
emits logic that runs on a machine, `NEVER` below is what it collides with, and
the position has to be re-decided before that feature ships — not after. It has
not been reviewed by anyone who has taken a machine through CE marking; that
review is E19's phase proof, and until it happens this is Kryova's reading, not
advice to a customer.

**It narrows what the product may say about itself**, as the plan warned it
would. `FORBIDDEN_CLAIMS` is scanned over every string in `app/` by
`tests/test_compliance_boundary.py`, and the one sentence a salesperson may say
is `SALES_SENTENCE`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.provisions import QuotedClause
from app.compliance.technical_file import POINTS


def _clause(citation: str, quote: str, note: str = "") -> QuotedClause:
    return QuotedClause(citation=citation, quote=quote, sources=(OJ_L_165,), note=note)


ANNEX_I_PART_A_5: Final = _clause(
    "Annex I, Part A, point 5",
    "Safety components with fully or partially self-evolving behaviour using machine learning "
    "approaches ensuring safety functions.",
)
ANNEX_I_PART_A_6: Final = _clause(
    "Annex I, Part A, point 6",
    "Machinery that has embedded systems with fully or partially self-evolving behaviour using "
    "machine learning approaches ensuring safety functions that have not been placed "
    "independently on the market, in respect only of those systems.",
)
ANNEX_I_PART_B_17: Final = _clause(
    "Annex I, Part B, point 17",
    "Logic units to ensure safety functions.",
)
ANNEX_II_18: Final = _clause(
    "Annex II, point 18",
    "Software ensuring safety functions.",
    note="The plan placed this in Annex I. It is on the indicative list of safety components.",
)
ANNEX_II_19: Final = _clause(
    "Annex II, point 19",
    "Safety components with fully or partially self-evolving behaviour using machine learning "
    "approaches ensuring safety functions.",
)
RECITAL_55: Final = _clause(
    "Recital (55)",
    "Provisions related to the third-party conformity assessment of software ensuring safety "
    "functions set out in this Regulation should only apply to systems with a fully or partially "
    "self-evolving behaviour using machine learning approaches ensuring safety functions. On the "
    "contrary, those provisions should not apply to software incapable of learning or evolving, "
    "and programmed only to execute certain automated functions of machinery or related products.",
    note="A recital: it explains the annexes and binds nobody on its own.",
)
ARTICLE_10_2: Final = _clause(
    "Article 10(2)",
    "Before placing machinery or a related product on the market or putting it into service, "
    "manufacturers shall draw up the technical documentation set out in Annex IV, Part A and "
    "carry out the relevant conformity assessment procedure referred to in Article 25 or have it "
    "carried out. Where compliance of machinery or a related product with the essential health "
    "and safety requirements laid down in Annex III has been demonstrated by that conformity "
    "assessment procedure, manufacturers shall draw up the EU declaration of conformity in "
    "accordance with Article 21 and affix the CE marking in accordance with Article 24.",
)
#: Annex IV, Part A, quoted once in `technical_file` and shared, so the boundary
#: and the export cannot quote one clause two ways.
ANNEX_IV_A_B: Final = POINTS["b"]
ANNEX_IV_A_C: Final = POINTS["c"]
ANNEX_IV_A_D: Final = POINTS["d"]
ANNEX_IV_A_G: Final = POINTS["g"]
ANNEX_IV_A_I: Final = POINTS["i"]


class Stance(StrEnum):
    """What Kryova does with one kind of output."""

    #: Kryova produces it; an engineer reviews it as they review any design work.
    MAY_GENERATE = "may-generate"
    #: Kryova may prepare it, and it is never presented as finished: it is a
    #: draft until a named person completes it and takes it as their own. This is
    #: the task's "must refuse to generate unattended".
    DRAFT_FOR_A_NAMED_PERSON = "draft-for-a-named-person"
    #: Not produced at all, attended or not.
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class Output:
    what: str
    stance: Stance
    why: str
    rests_on: tuple[QuotedClause, ...]

    def __post_init__(self) -> None:
        if not self.rests_on:
            raise ValueError(f"{self.what!r} is placed on the boundary with no clause behind it.")


OUTPUTS: Final[tuple[Output, ...]] = (
    Output(
        what="Design and manufacturing drawings of the machine and its components",
        stance=Stance.MAY_GENERATE,
        why="Design work the manufacturer's engineers review; one element of their technical file.",
        rests_on=(ANNEX_IV_A_C,),
    ),
    Output(
        what="Descriptions and explanations needed to understand those drawings",
        stance=Stance.MAY_GENERATE,
        why="The design record's rationale and revision history are exactly this.",
        rests_on=(ANNEX_IV_A_D,),
    ),
    Output(
        what="Reports and results of design calculations and analyses",
        stance=Stance.MAY_GENERATE,
        why=(
            "Each one carries its verification basis and the statement that nothing has been "
            "validated against a physical part, so it can be weighed, not merely believed."
        ),
        rests_on=(ANNEX_IV_A_G,),
    ),
    Output(
        what="Risk assessment documentation",
        stance=Stance.DRAFT_FOR_A_NAMED_PERSON,
        why=(
            "Kryova can list hazards its analyses touch; it cannot know every use, misuse and "
            "environment of a machine, so a risk assessment it presented as complete would be "
            "the most dangerous document it could write."
        ),
        rests_on=(ANNEX_IV_A_B,),
    ),
    Output(
        what="Instructions for use",
        stance=Stance.DRAFT_FOR_A_NAMED_PERSON,
        why="A draft the manufacturer completes; delivery must meet Article 10(7) (`instructions`).",
        rests_on=(ANNEX_IV_A_I,),
    ),
    Output(
        what="Software or logic that performs a safety function on the machine",
        stance=Stance.NEVER,
        why=(
            "Producing it would make Kryova's output a safety component — and, if it learned, "
            "one that needs third-party assessment. It is also the one output that would "
            "overturn the position this module records."
        ),
        rests_on=(ANNEX_II_18, ANNEX_I_PART_B_17, ANNEX_I_PART_A_5, ANNEX_I_PART_A_6, RECITAL_55),
    ),
    Output(
        what="An EU declaration of conformity",
        stance=Stance.NEVER,
        why="The manufacturer draws it up once a conformity assessment has demonstrated compliance.",
        rests_on=(ARTICLE_10_2,),
    ),
    Output(
        what="A CE marking, or an image or label of one",
        stance=Stance.NEVER,
        why="The manufacturer affixes it, on the same condition.",
        rests_on=(ARTICLE_10_2,),
    ),
    Output(
        what="A statement that a machine complies with Regulation (EU) 2023/1230",
        stance=Stance.NEVER,
        why=(
            "Compliance is demonstrated by a conformity assessment procedure, which Kryova does "
            "not carry out. A design that passes every check here has passed those checks."
        ),
        rests_on=(ARTICLE_10_2,),
    ),
)

#: The one thing a salesperson may say about Kryova and the Machinery Regulation.
#: Everything else they say about it should be a quotation of this.
SALES_SENTENCE: Final = (
    "Kryova is design and analysis software for machine builders: it produces the drawings, "
    "design calculations and analysis reports that go into a machine's technical documentation, "
    "and it certifies nothing — the manufacturer carries out the risk assessment and the "
    "conformity assessment, draws up the EU declaration of conformity and affixes the CE marking."
)

_LAW: Final = (
    r"(?:the\s+)?(?:EU\s+)?(?:Machinery\s+(?:Regulation|Directive)|(?:Regulation\s+\(EU\)\s+)?"
    r"2023/1230|(?:Directive\s+)?2006/42/EC|(?:EU\s+)?AI\s+Act|CE\s+requirements)"
)
_WHO: Final = r"(?:Kryova|we|the\s+(?:agent|assistant|product|platform|software))"

#: Claims nothing in `app/` may make about Kryova or its output. Each is a
#: positive claim, not a mention: "the manufacturer affixes the CE marking" is
#: the boundary stated, "Kryova affixes the CE marking" is the boundary crossed.
FORBIDDEN_CLAIMS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bCE[- ](?:certified|certification|certificate|compliant|compliance|approved|ready|marked)\b", re.I),
    re.compile(rf"\b(?:compliant|conformant|conforms?|conforming|complies)\s+(?:with|to)\s+{_LAW}", re.I),
    re.compile(rf"\b(?:certified|approved)\s+(?:to|under|for|against)\s+{_LAW}", re.I),
    re.compile(
        rf"\b{_WHO}\s+(?:can\s+|will\s+)?(?:generates?|produces?|drafts?|issues?|writes?|creates?|"
        r"affix(?:es)?|appl(?:y|ies)|signs?|certif(?:y|ies)|performs?|carr(?:y|ies)\s+out)\s+"
        r"(?:an?\s+|the\s+|your\s+)?(?:EU\s+)?(?:declarations?\s+of\s+conformity|CE[- ]mark(?:ing)?|"
        r"conformity\s+assessments?)\b",
        re.I,
    ),
    # A risk assessment may be drafted for a named person, so drafting is not
    # the claim; finishing one, or standing behind one, is.
    re.compile(
        rf"\b{_WHO}\s+(?:can\s+|will\s+)?(?:completes?|performs?|carr(?:y|ies)\s+out|signs?|"
        r"certif(?:y|ies)|approves?)\s+(?:an?\s+|the\s+|your\s+)?risk\s+assessments?\b",
        re.I,
    ),
    re.compile(r"\b(?:guarantees?|ensures?)\s+(?:\w+\s+){0,3}(?:legal\s+)?(?:compliance|conformity)\b", re.I),
    re.compile(r"\bKryova[- ]certified\b|\bcertified\s+by\s+Kryova\b", re.I),
)


def claims_in(text: str) -> list[str]:
    """Every forbidden claim `text` makes, as the words that made it."""
    return [match.group(0) for pattern in FORBIDDEN_CLAIMS for match in pattern.finditer(text)]


def outputs(stance: Stance) -> tuple[Output, ...]:
    return tuple(output for output in OUTPUTS if output.stance is stance)


__all__ = [
    "FORBIDDEN_CLAIMS",
    "OUTPUTS",
    "SALES_SENTENCE",
    "Output",
    "Stance",
    "claims_in",
    "outputs",
]
