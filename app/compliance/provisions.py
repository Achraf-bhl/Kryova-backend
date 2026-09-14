"""A dated legal fact, and the rules that stop one being written from memory.

Shared by every register in `app.compliance`. The shape is `app.verify.nafems`'s
applied to law: a claim carries the document it was read from, the clause it is
in, and the words as read — and the construction refuses the combinations that
would let a remembered date pass for a read one.

Three refusals, each for a failure this package has already met or been warned
about:

* **No source, no provision.** A dated legal claim nobody can check is the
  thing the whole package exists to keep out.
* **`PRIMARY_TEXT` needs an Official Journal source, a clause and a quote**, and
  a date the clause states must appear in the quote spelled the way the Official
  Journal spells dates ("20 January 2027"). A typed date that disagrees with its
  own quotation is refused rather than published.
* **A corrected date names its correction and keeps the date it replaced.** The
  Machinery Regulation's application date reads *14* January 2027 in the text as
  published and *20* January 2027 after its corrigendum; both are in the Official
  Journal, and the ELI link a careful reader follows serves the first. Dropping
  the published date would hide exactly how the "widely repeated error" is made.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum


class LegalBasis(StrEnum):
    """Where a dated claim about a legal text came from."""

    #: Read off the Official Journal text: the act as published, a corrigendum
    #: to it, or an amending act — with the clause and the words as read.
    PRIMARY_TEXT = "primary-text"
    #: Not read in the Official Journal; two or more secondary sources that do
    #: not appear to quote one another agree on it.
    SECONDARY_CORROBORATED = "secondary-corroborated"


class SourceKind(StrEnum):
    OFFICIAL_JOURNAL = "official-journal"
    SECONDARY = "secondary"


@dataclass(frozen=True, slots=True)
class Source:
    """One document a provision was read from."""

    id: str
    kind: SourceKind
    #: The document as it cites itself, OJ reference included where it has one.
    document: str
    url: str
    read_on: datetime.date
    #: How it was read, when that is not obvious — EUR-Lex serves an empty body
    #: to a plain HTTP fetch, which is worth knowing before trying again.
    how: str = ""


def spelled(day: datetime.date) -> str:
    """A date the way the Official Journal writes it in running text."""
    return f"{day.day} {day.strftime('%B')} {day.year}"


@dataclass(frozen=True, slots=True)
class DatedProvision:
    """One dated fact about an act, with its pedigree attached."""

    event: str
    #: The date in force today — after any corrigendum or amendment.
    date: datetime.date
    basis: LegalBasis
    sources: tuple[Source, ...]
    #: The clause, as a reader would look it up: "Article 54, second paragraph".
    citation: str = ""
    #: The operative words as read, carrying the date in force.
    quote: str = ""
    #: False where the clause gives a rule rather than a date ("the twentieth day
    #: following that of its publication"), so the date is computed from it.
    date_stated: bool = True
    #: The date the text as first published gave, where a correction moved it.
    as_published: datetime.date | None = None
    #: Which correction moved it: document and item.
    correction: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(
                f"{self.event!r} carries no source. A dated legal claim with nothing "
                "to check it against is exactly the failure this package exists to prevent."
            )
        if self.basis is LegalBasis.PRIMARY_TEXT:
            if not any(s.kind is SourceKind.OFFICIAL_JOURNAL for s in self.sources):
                raise ValueError(
                    f"{self.event!r} claims the primary text and cites no Official Journal "
                    "document. Mark it SECONDARY_CORROBORATED, or cite what was read."
                )
            if not (self.citation and self.quote):
                raise ValueError(
                    f"{self.event!r} claims the primary text without the clause and the words "
                    "read. Both are what lets a reader check it."
                )
            if self.date_stated and spelled(self.date) not in self.quote:
                raise ValueError(
                    f"{self.event!r} is dated {spelled(self.date)}, and its own quotation does "
                    f"not say so: {self.quote!r}."
                )
        if (self.as_published is None) != (not self.correction):
            raise ValueError(
                f"{self.event!r}: a corrected date names its correction and keeps the date "
                "it replaced — both or neither."
            )
        if self.as_published is not None and self.as_published == self.date:
            raise ValueError(f"{self.event!r} records a correction that changed nothing.")


@dataclass(frozen=True, slots=True)
class QuotedClause:
    """Words of an act that a decision rests on, where the point is not a date.

    Held to the primary-text rule outright: a clause is only worth quoting from
    the Official Journal, and a paraphrase from elsewhere is what this replaces.
    """

    citation: str
    quote: str
    sources: tuple[Source, ...]
    note: str = ""

    def __post_init__(self) -> None:
        if not (self.citation and self.quote):
            raise ValueError("A quoted clause needs the clause and the words read.")
        if not any(s.kind is SourceKind.OFFICIAL_JOURNAL for s in self.sources):
            raise ValueError(
                f"{self.citation!r} is quoted from no Official Journal document. A clause "
                "a decision rests on is read from the act, not from a summary of it."
            )


__all__ = ["DatedProvision", "LegalBasis", "QuotedClause", "Source", "SourceKind", "spelled"]
