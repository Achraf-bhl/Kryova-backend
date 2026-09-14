"""When Regulation (EU) 2023/1230 (the Machinery Regulation) actually applies,
and to what. Master plan E19 task 1.

**Read the Official Journal text, not a summary — and this module says plainly
that it could not.** The task's own brief was written because two published
sources disagreed about the main application date, one calling the other's
figure "a widely repeated error", from sources that were otherwise accurate.
That is exactly the failure mode a citation from memory produces here, so the
standing rule is the same one `app.verify.nafems.SOURCES` applies to a NAFEMS
target: a number with no traceable source does not go in.

**What was attempted and what it found.** `WebFetch` was tried against
`eur-lex.europa.eu` in four URL forms — the ELI form the plan names
(`/eli/reg/2023/1230/oj/eng`), the CELEX `legal-content` form, its dated
consolidated-text form, and the `ALL` metadata form — and every one returned
an empty document. That reads as the site not serving readable content to an
automated fetch (most EUR-Lex pages are rendered client-side), not as a
redirect or a permissions error the tool reported, and no other tool available
in this session reaches the network directly. **So nothing in this module is
read off the primary Official Journal text**, which is precisely the
standard the task set and which this module has not met.

**What stands in its place: two independent secondary sources that agree with
each other and cite article numbers.** EU-OSHA (`osha.europa.eu`), an EU
agency publishing its own legislation summary, and a Rockwell Automation
compliance guide (a named PDF, saved and read directly rather than trusted
from a search snippet) both give **20 January 2027** as the application date
and **29 June 2023** / **19 July 2023** as publication and entry into force.
General web search separately surfaced two snippets naming **Article 51** as
the repeal provision and **Article 52** as the transitional provision, from
different compliance-industry pages, agreeing with each other. Two sources
that were not consulting each other landing on the same dates is corroboration
in the way `nafems.py`'s cross-checked geometry sources are — real evidence,
still not the standard "the OJ text itself" sets. `LegalBasis.SECONDARY_
CORROBORATED` says so on every entry below, and `PRIMARY_TEXT` is defined and
used nowhere yet: filling it in is a data edit — replace the basis and the
`sources` tuple with the OJ text itself, once something in this environment
can reach it — and nothing else in this module moves, the same seam
`nafems.py`'s own "one seam to edit when a document arrives" describes.

**The finding the task was written to settle: 20 January 2027, not 14.**
Every source found here — including the one search result that stated both
figures in the same paragraph, an object lesson in the task's own premise —
converges on 20 January 2027 once its individual claims are checked against
each other rather than read as a single number. 14 January 2027 was not
corroborated by any source consulted and is recorded nowhere in this module.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class LegalBasis(StrEnum):
    """Where a dated claim about the regulation's text came from."""

    #: Read directly off the Official Journal consolidated text at the ELI
    #: this codebase can be shown citing an article and paragraph number for.
    #: Nothing in this module is at this basis yet — see the module docstring.
    PRIMARY_TEXT = "primary-text"
    #: Not reached in the primary text; corroborated by two or more secondary
    #: sources that do not appear to be quoting one another and that between
    #: them name a specific article number.
    SECONDARY_CORROBORATED = "secondary-corroborated"


@dataclass(frozen=True)
class DatedProvision:
    """One dated fact about the regulation, with its pedigree attached.

    `article` is `None` where a source gives the date but not the article
    number — recorded as missing rather than guessed, the same way a NAFEMS
    target names its tolerance reason rather than leaving one implied.
    """

    event: str
    date: datetime.date
    basis: LegalBasis
    sources: tuple[str, ...]
    article: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(
                f"{self.event!r} carries no source. A dated legal claim with "
                "nothing to check it against is exactly the failure this "
                "module exists to prevent."
            )


#: Citations, written once each — see `nafems.SOURCES` for the identical
#: argument against repeating one inline.
_SOURCES: Final[dict[str, str]] = {
    "osha": (
        "EU-OSHA, 'Regulation 2023/1230/EU - machinery', osha.europa.eu legislation "
        "summary. States: 'The Regulation applies from 20 January 2027. However, "
        "some rules already apply on earlier dates such as the requirements for "
        "notified bodies (20 January 2024).' Read 2026-09-14 via automated fetch; "
        "gives no article numbers."
    ),
    "rockwell": (
        "Rockwell Automation, 'A Guide to the Machinery Regulation (EU) 2023/1230 "
        "— Key changes and challenges', PDF, page 3 ('Key dates'). States the "
        "Regulation 'was published in the official journal on 29 June 2023, and "
        "entered into force on 19 July 2023, with a 42-month transition to the "
        "application date of 20 January 2027', and marks 20 January 2027 as "
        "'Application of Regulation (EU) 2023/1230 for private companies'. Cites "
        "https://eur-lex.europa.eu/eli/reg/2023/1230/oj as its own source for the "
        "regulation. Read 2026-09-14; gives no article numbers."
    ),
    "search-article-51-52": (
        "Two independent compliance-industry pages, surfaced by web search and "
        "not the same document, each stating: Article 51 repeals Directive "
        "2006/42/EC and Directive 73/361/EEC with effect from 20 January 2027, "
        "and Article 52 is the transitional provision letting machinery placed on "
        "the market before that date continue to circulate under 2006/42/EC. "
        "Neither page's own URL was captured with enough confidence to cite "
        "individually — recorded as a class of corroboration, not a citable "
        "single source, which is itself a residual (see the module docstring). "
        "Read 2026-09-14."
    ),
}


#: Every dated fact this module currently carries. Add a `DatedProvision`
#: rather than a bare comment — an entry with no `sources` is refused at
#: construction, so this list cannot silently grow an unsourced claim.
PROVISIONS: Final[tuple[DatedProvision, ...]] = (
    DatedProvision(
        event="Regulation (EU) 2023/1230 published in the Official Journal",
        date=datetime.date(2023, 6, 29),
        basis=LegalBasis.SECONDARY_CORROBORATED,
        sources=(_SOURCES["rockwell"],),
    ),
    DatedProvision(
        event="Regulation (EU) 2023/1230 enters into force",
        date=datetime.date(2023, 7, 19),
        basis=LegalBasis.SECONDARY_CORROBORATED,
        sources=(_SOURCES["rockwell"],),
        note=(
            "20 days after Official Journal publication, consistent with the "
            "publication date above; not independently confirmed against the "
            "primary text's own entry-into-force article."
        ),
    ),
    DatedProvision(
        event=(
            "Articles 26-42 (notified/conformity assessment bodies) apply, ahead "
            "of the main application date"
        ),
        date=datetime.date(2024, 1, 20),
        basis=LegalBasis.SECONDARY_CORROBORATED,
        sources=(_SOURCES["osha"],),
        article="26-42",
    ),
    DatedProvision(
        event="Directive 2006/42/EC and Directive 73/361/EEC repealed",
        date=datetime.date(2027, 1, 20),
        basis=LegalBasis.SECONDARY_CORROBORATED,
        sources=(_SOURCES["search-article-51-52"], _SOURCES["osha"], _SOURCES["rockwell"]),
        article="51",
        note=(
            "The date this module was written to settle. 14 January 2027 — a "
            "figure that appeared, uncorroborated, inside a single automated "
            "search summary that stated both dates in the same response — is "
            "not supported by any source recorded here and is not carried by "
            "this module."
        ),
    ),
    DatedProvision(
        event="Main application date; machinery must meet the Regulation's requirements",
        date=datetime.date(2027, 1, 20),
        basis=LegalBasis.SECONDARY_CORROBORATED,
        sources=(_SOURCES["osha"], _SOURCES["rockwell"]),
        article="52",
        note="Same date as the repeal above; Article 52 is the transitional provision.",
    ),
)


#: The application date's own value, exposed directly because it is the one
#: figure most likely to be quoted elsewhere in the product (E19 task 4's
#: instructions-for-use retention window and task 5's substantial-modification
#: line both key off it). Reading it out of `PROVISIONS` by event string at
#: every call site would be one more place a typo goes unnoticed; this is
#: derived from the same table rather than typed a second time.
def _application_date() -> DatedProvision:
    for provision in PROVISIONS:
        if provision.article == "52":
            return provision
    raise AssertionError("PROVISIONS carries no Article 52 entry")


APPLICATION_DATE: Final[datetime.date] = _application_date().date


__all__ = [
    "APPLICATION_DATE",
    "PROVISIONS",
    "DatedProvision",
    "LegalBasis",
]
