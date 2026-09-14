"""Where the AI Act stands for machinery, as a register that expires — master
plan E19 task 6.

**The plan's paragraph was written before the law it described existed.** It
gave the "AI Omnibus" dates from a political agreement, with a warning that one
of them had survived adversarial checking only 2-1. The agreement has since
become Regulation (EU) 2026/1744 of 8 July 2026 (OJ L, 2026/1744, 24.7.2026),
and every date below is read from that act's text:

* It entered into force on **27 July 2026** — its Article 4 says the third day
  after publication, and publication was 24 July.
* Its Article 1(40) rewrites Article 113 of the AI Act (Regulation (EU)
  2024/1689): the high-risk rules apply from **2 December 2027** for Annex III
  systems and from **2 August 2028** for Annex I systems.
* Its Article 3(1) amends the Machinery Regulation itself: the Commission
  **shall** adopt delegated acts adding requirements for AI systems that are
  high-risk because they are a safety component of machinery, or are machinery
  themselves, to its Annex III — and "those delegated acts shall apply by
  2 August 2028". That date is in the operative text, not only in a recital; an
  earlier draft of this module said otherwise because the first reading stopped
  one sentence short.

**The narrowing the plan describes is real, and narrower than the plan said.**
Article 6(1a), inserted by Article 1(8), says systems "solely used for
non-safety related aspects" of assistance, optimisation, efficiency, automation,
convenience or quality control "shall not qualify as safety components" — and
the next paragraph, 6(1b), which the plan left out, says a system whose failure
"would endanger health and safety" shall qualify regardless. Both are quoted
below.

**One claim in the plan was not found in what was read**: that a manufacturer
performs one conformity assessment under the Machinery Regulation rather than
two. It is in `NOT_FOUND`, not in a provision.

**This register expires.** Law in this area moved twice between the plan being
written and this module; a register that is not re-read is a paragraph that was
true once. `REVIEW_BY` is a date, and `tests/test_compliance_eu_ai_act.py` fails
once it has passed — the rule `app.verify.recorded` applies to a benchmark run
whose code has changed, applied to a statute whose text may have. Re-read the
consolidated AI Act and any amending act on EUR-Lex, update what moved, and set
a new date.
"""

from __future__ import annotations

import datetime
from typing import Final

from app.compliance.provisions import (
    DatedProvision,
    LegalBasis,
    QuotedClause,
    Source,
    SourceKind,
)

_READ_ON: Final = datetime.date(2026, 9, 14)

#: After this date the register is stale and the suite says so. Six months from
#: the reading: the machinery delegated acts are due and unwritten, so the next
#: change is a matter of when.
REVIEW_BY: Final = datetime.date(2027, 3, 14)

OJ_2026_1744: Final = Source(
    id="oj-l-2026-1744",
    kind=SourceKind.OFFICIAL_JOURNAL,
    document=(
        "Regulation (EU) 2026/1744 of the European Parliament and of the Council of 8 July "
        "2026 amending Regulations (EU) 2024/1689, (EU) 2018/1139 and (EU) 2023/1230 as regards "
        "the simplification of the implementation of harmonised rules on artificial "
        "intelligence (Digital Omnibus on AI), OJ L, 2026/1744, 24.7.2026"
    ),
    url="https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32026R1744",
    read_on=_READ_ON,
    how="Rendered extracts of the EUR-Lex CELEX HTML; a plain HTTP fetch receives an empty body.",
)

PUBLISHED: Final = datetime.date(2026, 7, 24)

PROVISIONS: Final[tuple[DatedProvision, ...]] = (
    DatedProvision(
        event="Regulation (EU) 2026/1744 (Digital Omnibus on AI) published",
        date=PUBLISHED,
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation="Publication reference",
        quote="OJ L, 2026/1744, 24.7.2026",
        date_stated=False,
        note="The reference writes the date numerically.",
    ),
    DatedProvision(
        event="Regulation (EU) 2026/1744 enters into force",
        date=PUBLISHED + datetime.timedelta(days=3),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation="Regulation (EU) 2026/1744, Article 4, first paragraph",
        quote=(
            "This Regulation shall enter into force on the third day following that of its "
            "publication in the Official Journal of the European Union."
        ),
        date_stated=False,
    ),
    DatedProvision(
        event="AI Act Articles 102 to 110 apply",
        date=datetime.date(2026, 7, 27),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2024/1689, Article 113, third paragraph, point (d), added by "
            "Regulation (EU) 2026/1744, Article 1(40)(c)"
        ),
        quote="(d) Articles 102 to 110 shall apply from 27 July 2026.",
    ),
    DatedProvision(
        event="AI Act Article 5(1) points (ba) and (bb), and Article 5(1a) and (1b), apply",
        date=datetime.date(2026, 12, 2),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2024/1689, Article 113, third paragraph, point (a), as replaced by "
            "Regulation (EU) 2026/1744, Article 1(40)(a)"
        ),
        quote=(
            "(a) Chapters I and II shall apply from 2 February 2025, with the exception of "
            "Article 5(1), first subparagraph, points (ba) and (bb), and Article 5(1a) and (1b) "
            "which shall apply from 2 December 2026;"
        ),
    ),
    DatedProvision(
        event="High-risk AI rules apply to systems classified under Article 6(2) and Annex III",
        date=datetime.date(2027, 12, 2),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2024/1689, Article 113, third paragraph, point (c)(i), as replaced by "
            "Regulation (EU) 2026/1744, Article 1(40)(b)"
        ),
        quote=(
            "(c) Chapter III, Sections 1, 2, and 3, with the exception of Article 6(5), shall apply "
            "from: (i) 2 December 2027 as regards AI systems classified as high-risk pursuant to "
            "Article 6(2) and Annex III;"
        ),
    ),
    DatedProvision(
        event="High-risk AI rules apply to systems classified under Article 6(1) and Annex I",
        date=datetime.date(2028, 8, 2),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2024/1689, Article 113, third paragraph, point (c)(ii), as replaced "
            "by Regulation (EU) 2026/1744, Article 1(40)(b)"
        ),
        quote=(
            "(ii) 2 August 2028 as regards AI systems classified as high-risk pursuant to "
            "Article 6(1) and Annex I;"
        ),
        note=(
            "The class the Machinery Regulation's new Article 8 paragraph addresses: AI systems "
            "high-risk under Article 6(1) because they are a safety component of machinery, or "
            "are machinery themselves."
        ),
    ),
    DatedProvision(
        event=(
            "The Commission's power to adopt the Machinery Regulation's AI delegated acts runs "
            "for five years from this date"
        ),
        date=datetime.date(2026, 7, 27),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2023/1230, Article 47(2), second sentence, as replaced by "
            "Regulation (EU) 2026/1744, Article 3(3)(a)"
        ),
        quote=(
            "The power to adopt delegated acts referred to in Article 8, third paragraph, shall be "
            "conferred on the Commission for a period of five years from 27 July 2026."
        ),
    ),
    DatedProvision(
        event="The Machinery Regulation's delegated acts on high-risk AI systems apply, at the latest",
        date=datetime.date(2028, 8, 2),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_2026_1744,),
        citation=(
            "Regulation (EU) 2023/1230, Article 8, the paragraph following the third, added by "
            "Regulation (EU) 2026/1744, Article 3(1)"
        ),
        quote=(
            "When adopting the delegated acts referred to in the third paragraph, the Commission "
            "shall take into account the objectives of Regulation (EU) 2024/1689 and ensure a "
            "level of protection consistent with that Regulation. Those delegated acts shall "
            "apply by 2 August 2028."
        ),
        note=(
            "The same date as the AI Act's Annex I class, and a recital gives the reason: 'to "
            "avoid a legal gap'. Which number the paragraph carries was not captured."
        ),
    ),
)

CLAUSES: Final[tuple[QuotedClause, ...]] = (
    QuotedClause(
        citation=(
            "Regulation (EU) 2023/1230, Article 8, third paragraph, added by Regulation (EU) "
            "2026/1744, Article 3(1)"
        ),
        quote=(
            "The Commission shall adopt delegated acts in accordance with Article 47 of this "
            "Regulation to amend Annex III to this Regulation by adding health and safety "
            "requirements in respect of Artificial Intelligence (AI) systems that are classified "
            "as high-risk pursuant to Article 6(1) of Regulation (EU) 2024/1689 [...] due to the "
            "fact that they are a safety component in a product covered by this Regulation, or "
            "they are themselves a product covered by this Regulation."
        ),
        sources=(OJ_2026_1744,),
        note=(
            "The paragraph is 'Article 8, third paragraph' by the amended Article 47(2)'s own "
            "reference to it. Those requirements are unwritten as of the reading, so nothing in "
            "this product can be checked against them yet."
        ),
    ),
    QuotedClause(
        citation=(
            "Regulation (EU) 2024/1689, Article 6(1a), inserted by Regulation (EU) 2026/1744, "
            "Article 1(8)"
        ),
        quote=(
            "For the purposes of this Regulation, including paragraph 1 of this Article, AI "
            "systems that are solely used for non-safety related aspects of user assistance, "
            "performance optimisation, service efficiency, automation or convenience or quality "
            "control shall not qualify as safety components."
        ),
        sources=(OJ_2026_1744,),
    ),
    QuotedClause(
        citation=(
            "Regulation (EU) 2024/1689, Article 6(1b), inserted by Regulation (EU) 2026/1744, "
            "Article 1(8)"
        ),
        quote=(
            "Notwithstanding paragraph 1a, AI systems the failure or malfunctioning of which would "
            "endanger health and safety shall qualify as safety components."
        ),
        sources=(OJ_2026_1744,),
        note="Omitted from the plan's summary; it is what keeps paragraph 1a from being a loophole.",
    ),
    QuotedClause(
        citation="Regulation (EU) 2026/1744, recital (number not captured)",
        quote=(
            "manufacturers should be free to rely on harmonised standards or common specifications "
            "referenced or adopted pursuant to Regulation (EU) 2024/1689 that cover the relevant "
            "essential requirements"
        ),
        sources=(OJ_2026_1744,),
        note=(
            "The route to a presumption of conformity before machinery-specific standards exist. "
            "A recital's words: it explains the act and binds nobody."
        ),
    ),
)

#: What the plan asserted and the reading did not find. Kept, so the claim is not
#: rediscovered as though settled, and never promoted without a quote.
NOT_FOUND: Final[tuple[str, ...]] = (
    "A manufacturer performs one conformity assessment under the Machinery Regulation rather "
    "than two (master plan E19 task 6). Not found in the parts of Regulation (EU) 2026/1744 read "
    "on 2026-09-14.",
)


def is_current(today: datetime.date) -> bool:
    """Whether the register is still inside its review window."""
    return today <= REVIEW_BY


__all__ = [
    "CLAUSES",
    "NOT_FOUND",
    "OJ_2026_1744",
    "PROVISIONS",
    "PUBLISHED",
    "REVIEW_BY",
    "is_current",
]
