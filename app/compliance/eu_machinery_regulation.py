"""When Regulation (EU) 2023/1230 (the Machinery Regulation) applies, and to
what — read off the Official Journal. Master plan E19 task 1.

**The disagreement the task was written to settle has a precise answer: both
dates are in the Official Journal.** The Regulation as published (OJ L 165,
29.6.2023) says it "shall apply from 14 January 2027", and dates every early
provision to match. Five days later a corrigendum (OJ L 169, 4.7.2023, p. 35)
replaced fourteen dates, among them that one: item 10 reads "for: '14 January
2027', read: '20 January 2027'". The corrigendum gives no reason. Every
published date but one sits six days before its correction, which is what dates
counted from an earlier publication day would look like — entry into force, on
the twentieth day after the real one, is 19 July 2023, and the corrected early
dates are counted from there. That is an inference and is labelled as one.

So a source quoting 14 January 2027 is not inventing it. It is reading the text
as published — which is what the ELI link in the master plan
(`eli/reg/2023/1230/oj`) serves, and what an automated extract of the CELEX HTML
returned on 2026-09-14. **The date in force is the corrected one**, and every
provision below keeps the published date beside it with the corrigendum item
that moved it, so the error cannot be repeated from this module and the reason
people repeat it stays visible.

**How the text was read.** `WebFetch` and `curl` both received an empty body
from EUR-Lex; a rendered extract of the same CELEX HTML pages returned the
text. What was read is quoted in each provision's `quote`, as corrected. Not
everything in this register's predecessor survived the reading: Article 51(1)
repeals Directive 73/361/EEC with no date of its own, so no date is recorded
for it here — an earlier version said 20 January 2027, which follows only if
Article 54's general date governs a clause that names none, and that is an
inference, not a reading.

One oddity is recorded rather than smoothed: corrigendum item 12 moves Article
54(b) from "14 October 2023" to "20 October 2026" — three years, not six days —
so Article 50(1) (penalties) applies from the same date by which Member States
must notify their penalty rules under Article 50(2). The consolidated text, as
reproduced by a secondary source, carries the same date.
"""

from __future__ import annotations

import datetime
from typing import Final

from app.compliance.provisions import DatedProvision, LegalBasis, Source, SourceKind

_READ_ON: Final = datetime.date(2026, 9, 14)
_HOW: Final = (
    "Rendered extract of the EUR-Lex CELEX HTML; WebFetch and curl received an empty body."
)

OJ_L_165: Final = Source(
    id="oj-l-165-2023",
    kind=SourceKind.OFFICIAL_JOURNAL,
    document=(
        "Regulation (EU) 2023/1230 of the European Parliament and of the Council of 14 June "
        "2023 on machinery and repealing Directive 2006/42/EC of the European Parliament and "
        "of the Council and Council Directive 73/361/EEC, OJ L 165, 29.6.2023, p. 1"
    ),
    url="https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R1230",
    read_on=_READ_ON,
    how=_HOW,
)

CORRIGENDUM: Final = Source(
    id="oj-l-169-2023-corrigendum",
    kind=SourceKind.OFFICIAL_JOURNAL,
    document=(
        "Corrigendum to Regulation (EU) 2023/1230 of the European Parliament and of the "
        "Council of 14 June 2023 on machinery (Official Journal of the European Union L 165 "
        "of 29 June 2023), OJ L 169, 4.7.2023, p. 35"
    ),
    url="https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R1230R(01)",
    read_on=_READ_ON,
    how=_HOW,
)

PUBLISHED: Final = datetime.date(2023, 6, 29)


def _corrected(item: int) -> str:
    return f"Corrigendum, OJ L 169, 4.7.2023, p. 35, item {item}"


#: Every dated fact this module carries. A provision with no source, or claiming
#: the primary text without the clause and words read, is refused at construction.
PROVISIONS: Final[tuple[DatedProvision, ...]] = (
    DatedProvision(
        event="Regulation (EU) 2023/1230 published in the Official Journal",
        date=PUBLISHED,
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165,),
        citation="OJ L 165, page header",
        quote="29.6.2023 EN Official Journal of the European Union L 165/1",
        date_stated=False,
        note="The header writes the date numerically.",
    ),
    DatedProvision(
        event="Regulation (EU) 2023/1230 enters into force",
        date=PUBLISHED + datetime.timedelta(days=20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165,),
        citation="Article 54, first paragraph",
        quote=(
            "This Regulation shall enter into force on the twentieth day following that of its "
            "publication in the Official Journal of the European Union."
        ),
        date_stated=False,
        note="Computed from the publication date; the corrigendum's own dates agree.",
    ),
    DatedProvision(
        event="Article 6(7) and Articles 48 and 52 apply",
        date=datetime.date(2023, 7, 19),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 54, third paragraph, point (c)",
        quote="(c) Article 6(7) and Articles 48 and 52 from 19 July 2023;",
        as_published=datetime.date(2023, 7, 13),
        correction=_corrected(13),
    ),
    DatedProvision(
        event="Articles 26 to 42 (notified bodies) apply",
        date=datetime.date(2024, 1, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 54, third paragraph, point (a)",
        quote="(a) Articles 26 to 42 from 20 January 2024;",
        as_published=datetime.date(2024, 1, 14),
        correction=_corrected(11),
    ),
    DatedProvision(
        event="Article 6(2) to (6), (8) and (11) and Articles 47 and 53(3) apply",
        date=datetime.date(2024, 7, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 54, third paragraph, point (d)",
        quote="(d) Article 6(2) to (6), (8) and (11) and Articles 47 and 53(3) from 20 July 2024.",
        as_published=datetime.date(2024, 7, 14),
        correction=_corrected(14),
    ),
    DatedProvision(
        event="Member States notify the Commission of their rules on penalties",
        date=datetime.date(2026, 10, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 50(2)",
        quote=(
            "Member States shall, by 20 October 2026, notify the Commission of those rules and "
            "of those measures"
        ),
        as_published=datetime.date(2026, 10, 14),
        correction=_corrected(4),
    ),
    DatedProvision(
        event="Article 50(1) (penalties) applies",
        date=datetime.date(2026, 10, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 54, third paragraph, point (b)",
        quote="(b) Article 50(1) from 20 October 2026;",
        as_published=datetime.date(2023, 10, 14),
        correction=_corrected(12),
        note="The one correction that moves a year rather than six days.",
    ),
    DatedProvision(
        event="Directive 2006/42/EC repealed",
        date=datetime.date(2027, 1, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 51(2), first subparagraph",
        quote="Directive 2006/42/EC is repealed with effect from 20 January 2027.",
        as_published=datetime.date(2027, 1, 14),
        correction=_corrected(5),
    ),
    DatedProvision(
        event=(
            "Machinery placed on the market under Directive 2006/42/EC before this date may "
            "still be made available"
        ),
        date=datetime.date(2027, 1, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 52(1), first sentence",
        quote=(
            "Member States shall not impede the making available on the market of products which "
            "were placed on the market in conformity with Directive 2006/42/EC before "
            "20 January 2027."
        ),
        as_published=datetime.date(2027, 1, 14),
        correction=_corrected(6),
    ),
    DatedProvision(
        event="Regulation (EU) 2023/1230 applies",
        date=datetime.date(2027, 1, 20),
        basis=LegalBasis.PRIMARY_TEXT,
        sources=(OJ_L_165, CORRIGENDUM),
        citation="Article 54, second paragraph",
        quote="It shall apply from 20 January 2027.",
        as_published=datetime.date(2027, 1, 14),
        correction=_corrected(10),
        note="The date E19 was written to settle.",
    ),
)


def provision(citation: str) -> DatedProvision:
    """The provision at one clause, by the citation a reader would look up."""
    for candidate in PROVISIONS:
        if candidate.citation == citation:
            return candidate
    raise LookupError(f"No provision in the Machinery Regulation register cites {citation!r}.")


#: Derived from the table rather than typed a second time: E19 tasks 4 and 5 key
#: off it, and a date typed at two call sites is two chances to get it wrong.
APPLICATION_DATE: Final[datetime.date] = provision("Article 54, second paragraph").date
ENTRY_INTO_FORCE: Final[datetime.date] = provision("Article 54, first paragraph").date


__all__ = [
    "APPLICATION_DATE",
    "CORRIGENDUM",
    "ENTRY_INTO_FORCE",
    "OJ_L_165",
    "PROVISIONS",
    "PUBLISHED",
    "provision",
]
