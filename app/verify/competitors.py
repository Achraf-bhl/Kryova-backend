"""What the competition claims, re-read at its source and dated (master plan E23.1).

The plan's E23 paragraph was a research pass: accurate on the day, and certain to rot. This is
the register it asked for, kept like `nafems.SOURCES`. Every entry is one claim the plan made,
re-read on the page that makes it, with the words quoted. Where the page now says something
else, the entry says **changed** and quotes what it says now. Where the page could not be read,
the entry says **not re-read** and why, and carries no quote.

Two rules keep it honest:

* **A quote is the page's words, never a summary.** `read_as` says whether it came from the page
  itself or from a search engine's extract of that page, because the second is weaker.
* **The register expires.** `REVIEW_BY` is a quarter after the reading, and a test fails once it
  passes. The fix is to re-read every source and update what moved (E23.2), never to push the date.

Two sites refused automated reading on 2026-09-15 and were not worked around: the FreeCAD wiki
serves an anti-scraping challenge (Anubis) aimed at AI crawlers, and the FreeCAD blog answered
429. ISO's catalogue carries a notice restricting AI use of its content; see E21.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

READ_ON: Final = "2026-09-15"
#: A quarter after the reading. `tests/test_verify_competitors.py` fails after it.
REVIEW_BY: Final = date(2026, 12, 15)


class Standing(StrEnum):
    #: The page still says what the plan said.
    CONFIRMED = "confirmed"
    #: The page now says something else; `quote` is what it says now.
    CHANGED = "changed"
    #: The page could not be read; `note` says why, and there is no quote.
    NOT_REREAD = "not re-read"


class ReadAs(StrEnum):
    PAGE = "the page itself"
    SEARCH_EXTRACT = "a search engine's extract of the page"
    NOT_READ = "not read"


@dataclass(frozen=True)
class Claim:
    subject: str
    #: The claim as the plan made it.
    claim: str
    standing: Standing
    source: str
    read_as: ReadAs
    quote: str = ""
    #: The date the source itself carries, as it prints it, where it carries one.
    published: str = ""
    note: str = ""
    read_on: str = READ_ON

    def __post_init__(self) -> None:
        if not self.source.startswith("https://"):
            raise ValueError(f"{self.subject}: a claim names the page it was read on.")
        if self.standing is Standing.NOT_REREAD:
            if self.quote or self.read_as is not ReadAs.NOT_READ:
                raise ValueError(f"{self.subject}: a claim that was not re-read carries no quote.")
            if not self.note:
                raise ValueError(f"{self.subject}: say why the claim was not re-read.")
        elif not self.quote or self.read_as is ReadAs.NOT_READ:
            raise ValueError(f"{self.subject}: a confirmed or changed claim quotes the page.")


ZOO_FAQ: Final = "https://zoo.dev/docs/faq"
ZOO_V1: Final = "https://zoo.dev/blog/zoo-design-studio-v1"
PTC_LABS: Final = "https://www.ptc.com/en/news/2026/onshapelabs"
PTC_MCP: Final = "https://www.ptc.com/en/news/2026/onshape-launches-featurescript-mcp-server"
FREECAD_BLOG: Final = "https://blog.freecad.org/2026/03/25/freecad-version-1-1-released"
FREECAD_NOTES: Final = "https://wiki.freecad.org/Release_notes_1.1"
PHYSICSX: Final = (
    "https://www.physicsx.ai/newsroom/"
    "physicsx-announces-300m-series-c-to-accelerate-physics-ai-for-industrial-engineering"
)

REGISTER: Final[tuple[Claim, ...]] = (
    Claim(
        "Zoo",
        "Runs a proprietary, closed-source geometry engine with an open-source client.",
        Standing.CONFIRMED,
        ZOO_FAQ,
        ReadAs.PAGE,
        quote=(
            "Yes, the Zoo Design Studio application is open source and you can find it on GitHub. "
            "However, our CAD geometry engine is not open source."
        ),
    ),
    Claim(
        "Zoo",
        "KCL is the canonical text representation of a model.",
        Standing.CONFIRMED,
        ZOO_FAQ,
        ReadAs.PAGE,
        quote="with human-readable KCL as its source of truth",
    ),
    Claim(
        "Zoo",
        "Metered per-second API billing, plus reasoning time.",
        Standing.CONFIRMED,
        ZOO_V1,
        ReadAs.PAGE,
        quote=(
            "Update September 26, 2025: Text-to-CAD usage now bills per second at the same "
            "$0.50/min rate."
        ),
        published="May 21, 2025",
        note=(
            "The FAQ adds: \"We have paid tiers that include additional Zookeeper reasoning time.\""
        ),
    ),
    Claim(
        "Zoo",
        "Text-to-CAD produces single objects and cannot do assemblies.",
        Standing.CHANGED,
        ZOO_FAQ,
        ReadAs.PAGE,
        quote=(
            "Yes. Assemblies have been supported in Zoo Design Studio since v1.0. However, "
            "Assembly Mates are still on our roadmap."
        ),
        note=(
            "The FAQ answers for Design Studio, not for text-to-CAD alone. Whether Zookeeper, "
            "the agent, generates an assembly was not read."
        ),
    ),
    Claim(
        "Zoo",
        "Its FAQ admits its agent produces incorrect geometry and designs that may be "
        "unmanufacturable or unsafe.",
        Standing.CONFIRMED,
        ZOO_FAQ,
        ReadAs.PAGE,
        quote=(
            "Yes. Zookeeper can make mistakes - it may misunderstand intent, produce incorrect "
            "geometry, or suggest designs that aren't manufacturable or safe."
        ),
    ),
    Claim(
        "Zoo",
        "Not in the plan: Zoo also speaks MCP.",
        Standing.CHANGED,
        ZOO_FAQ,
        ReadAs.PAGE,
        quote="MCP connects Zoo-backed CAD tools to Claude, Codex, and other MCP-compatible AI clients.",
        note="E23.3's MCP server is therefore not a differentiator against Zoo.",
    ),
    Claim(
        "PTC / Onshape",
        "In March 2026 its one generally available AI feature was a documentation assistant, "
        "with agentic CAD in development.",
        Standing.CHANGED,
        PTC_LABS,
        ReadAs.PAGE,
        quote=(
            "BOSTON – July 14, 2026 – PTC (NASDAQ: PTC) today announced the Onshape Labs™ "
            "initiative, a new early-access program within its Onshape® cloud-native CAD and PDM "
            "platform that gives participating customers a first look at emerging functionalities, "
            "including new uses of AI, delivered directly inside the product development workflow."
        ),
        published="July 14, 2026",
        note="The March 2026 state itself was not re-read; this is what has been announced since.",
    ),
    Claim(
        "PTC / Onshape",
        "MCP is named as the third-party integration surface.",
        Standing.CONFIRMED,
        PTC_MCP,
        ReadAs.PAGE,
        quote=(
            "Built on the Model Context Protocol (MCP), engineers can connect AI directly with "
            "Onshape FeatureScript code."
        ),
        published="August 13, 2026",
        note="Available through Onshape Labs, per the same release.",
    ),
    Claim(
        "PTC / Onshape",
        "Its stated moat is data architecture: a fileless history of every modelling action.",
        Standing.NOT_REREAD,
        "https://www.onshape.com/en/blog/ai-artificial-intelligence-cloud-native-cad-pdm-platform",
        ReadAs.NOT_READ,
        note="No page stating it was found and read on 2026-09-15.",
    ),
    Claim(
        "FreeCAD",
        "FreeCAD 1.1 was released on 2026-03-25.",
        Standing.CONFIRMED,
        FREECAD_BLOG,
        ReadAs.SEARCH_EXTRACT,
        quote=(
            "we are delighted to announce that FreeCAD Version 1.1 is now released and available "
            "for download."
        ),
        published="2026/03/25 (in the post's URL)",
        note=(
            "The release notes, also read only as a search extract, say \"FreeCAD 1.1 was released "
            f"on 24 March 2026\" ({FREECAD_NOTES}). The two dates differ by a day."
        ),
    ),
    Claim(
        "FreeCAD",
        "FreeCAD 1.1 announced no AI feature at all.",
        Standing.NOT_REREAD,
        FREECAD_BLOG,
        ReadAs.NOT_READ,
        note=(
            "An absence needs the whole announcement, and neither whole page could be read: the "
            "blog answered 429 and the wiki serves an anti-AI-scraping challenge, which was "
            "respected rather than worked around."
        ),
    ),
    Claim(
        "PhysicsX",
        "Raised $300M at about $2.4B on 2026-06-08.",
        Standing.CONFIRMED,
        PHYSICSX,
        ReadAs.PAGE,
        quote=(
            "London, UK — 8 June 2026 — PhysicsX, the physics AI company for industrials, today "
            "announced an oversubscribed $300 million Series C financing at a valuation of "
            "approximately $2.4 billion."
        ),
        published="June 8, 2026",
    ),
    Claim(
        "PhysicsX",
        "Sells pre-trained Large Physics Models: surrogates, not a CAD or solver stack.",
        Standing.CONFIRMED,
        PHYSICSX,
        ReadAs.PAGE,
        quote="Its AI models predict physical behavior in seconds rather than hours or days",
        published="June 8, 2026",
        note=(
            "\"Not a CAD or solver stack\" is the plan's reading; the announcement says \"AI-native "
            "engineering platform\" and does not describe a solver."
        ),
    ),
    Claim(
        "PhysicsX",
        "The announcement does not mention certification, verification or validation.",
        Standing.CONFIRMED,
        PHYSICSX,
        ReadAs.PAGE,
        quote="We are also enabling more reliable, more efficient, and altogether new ways of doing engineering",
        published="June 8, 2026",
        note=(
            "Checked over the whole page text: none of 'certif', 'verif' or 'validat' occurs. The "
            "quote is the nearest it comes."
        ),
    ),
)


def claims_about(subject: str) -> tuple[Claim, ...]:
    return tuple(claim for claim in REGISTER if claim.subject == subject)


def is_current(today: date) -> bool:
    """Whether the register is still inside its review window."""
    return today <= REVIEW_BY




# --------------------------------------------------------------------------
# The judgement the register feeds (E23 task 2)
#
# The register above is observations. This half is the *answer* — and the
# reason it is a separate shape rather than a function is the whole of the
# task: "is nobody selling credibility?" is a judgement about a market, and a
# judgement derived by code from quotes would be a verdict nobody reached
# wearing the authority of a measurement. `app/verify/` already refuses that
# in two places: `Target` refuses a published basis with no source, and
# `core/status.py` refuses to infer "degraded" from a failure rate. This is
# the third.
#
# So nothing here computes an answer. What it does is give the answer
# somewhere to live, with the rules that keep it honest, and report that it is
# **unanswered** until a person writes one — which is a different thing from
# the plan quietly assuming an unoccupied niche for four years.
# --------------------------------------------------------------------------

#: The exact question an assessment answers. Written down so a later one
#: cannot drift onto an easier question and still read as an answer to this.
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

THE_QUESTION: Final = (
    "Is it still true that nobody in the funded competition is selling credibility — "
    "verification, validation, or a defensible engineering record?"
)


class Verdict(StrEnum):
    #: The gap the plan aims at is still open.
    OPEN = "open"
    #: Somebody has started selling it; the plan's differentiator is narrowing.
    NARROWING = "narrowing"
    #: Somebody is selling it properly. The plan needs rewriting, not renewing.
    OCCUPIED = "occupied"
    #: The register does not settle it either way, and the reader is told so
    #: rather than being handed the last quarter's answer.
    UNCLEAR = "unclear"


@dataclass(frozen=True)
class Assessment:
    """One person's dated answer, resting on claims anybody can re-read.

    Every field is required for a reason that has bitten this repository
    somewhere: a verdict with no author is one nobody can be asked about; a
    verdict with no citations is an opinion presented as a reading; and a
    citation the register does not hold is reasoning resting on a page nobody
    sourced.
    """

    verdict: Verdict
    #: Who reached it. A name, because the point of this field is that
    #: somebody can be asked what they meant.
    author: str
    #: The date they reached it, `YYYY-MM-DD`, as `READ_ON` is spelled.
    written_on: str
    #: Why, in their words. Not a summary of the quotes — the step from the
    #: quotes to the verdict, which is the part no quote contains.
    reasoning: str
    #: `(subject, claim)` pairs that must appear in `REGISTER`.
    cites: tuple[tuple[str, str], ...]
    question: str = THE_QUESTION

    def __post_init__(self) -> None:
        if not self.author.strip():
            raise ValueError("An assessment names who reached it.")
        if not _DATE_RE.match(self.written_on):
            raise ValueError(
                f"An assessment is dated YYYY-MM-DD; got {self.written_on!r}."
            )
        if len(self.reasoning.strip()) < 40:
            raise ValueError(
                "An assessment says why. The quotes are in the register; what is "
                "wanted here is the step from them to the verdict."
            )
        if not self.cites:
            raise ValueError(
                "An assessment cites the claims it rests on. Without them it is an "
                "opinion presented as a reading of the register."
            )
        for subject, claim in self.cites:
            matching = [
                entry
                for entry in REGISTER
                if entry.subject == subject and entry.claim == claim
            ]
            if not matching:
                raise ValueError(
                    f"{subject}: the register holds no claim {claim!r}. A verdict may "
                    "only rest on something a reader can go and re-read."
                )
            if not matching[0].quote:
                raise ValueError(
                    f"{subject}: {claim!r} was not re-read, so it carries no quote. A "
                    "position built on a page nobody could open is the failure this "
                    "task exists to prevent."
                )


#: Empty, and that is the current honest state of E23 task 2.
#:
#: The register was read on 2026-09-15 and its observations are above. **No
#: person has written the answer**, so the product says the question is
#: unanswered rather than repeating the plan's four-year-old assumption back
#: as a finding. The first entry is owed by whoever does the quarterly
#: re-reading before `REVIEW_BY`; appending one is the whole of the work, and
#: the rules above are what stop it from being written by a machine out of the
#: quotes it already has.
ASSESSMENTS: Final[tuple[Assessment, ...]] = ()


def current_assessment(today: date) -> Assessment | None:
    """The newest assessment still inside the register's review window.

    An assessment does not outlive the reading it rests on. A verdict quoted
    from a register whose sources nobody has checked for six months is exactly
    the "plan that assumes an unoccupied niche" this task names.
    """
    if not is_current(today):
        return None
    inside = sorted(
        (entry for entry in ASSESSMENTS if entry.question == THE_QUESTION),
        key=lambda entry: entry.written_on,
    )
    return inside[-1] if inside else None


def answer(today: date) -> str:
    """What a reader is told today, verdict or no verdict.

    Never an empty string and never a default verdict: a page that silently
    printed `OPEN` when nobody had looked would be the most convincing wrong
    sentence in the product.
    """
    found = current_assessment(today)
    if found is None:
        if not is_current(today):
            return (
                f"Unanswered: the competitor register was read on {READ_ON} and is "
                f"past its review date of {REVIEW_BY.isoformat()}. Re-read every "
                "source, then write the assessment."
            )
        return (
            f"Unanswered: the register was read on {READ_ON} and holds the "
            "observations, but nobody has written the judgement they feed. "
            "It is owed before " + REVIEW_BY.isoformat() + "."
        )
    return (
        f"{found.verdict.value} — {found.author}, {found.written_on}, "
        f"on {len(found.cites)} cited claim(s)."
    )


__all__ = [
    "ASSESSMENTS",
    "READ_ON",
    "REGISTER",
    "REVIEW_BY",
    "THE_QUESTION",
    "Assessment",
    "Claim",
    "ReadAs",
    "Standing",
    "Verdict",
    "answer",
    "claims_about",
    "current_assessment",
    "is_current",
]
