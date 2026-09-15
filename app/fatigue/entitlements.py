"""What each fatigue document is, how Kryova holds it, and what the code takes from it (E21.5).

`sources.require_source` makes every factor name where it came from. This answers the question
one level up, which the master plan's E21.5 found nobody had asked: *under what right* the code
holds what it took. Every document `app/fatigue/` reads from is an entry here, and a test fails
when a module cites a document this register does not carry.

Facts only, each with the page it was read from and the date. `docs/fatigue-data-licences.md` is
the reading record in prose. Three things are recorded as **not known** rather than guessed,
because each is a legal question and this repository has no counsel:

* whether a copy of a standard found on a third-party site may be relied on for its text;
* whether encoding a standard's *tables* (category numbers and their conditions) is taking a
  method or reproducing an expression -- TRIPS Art. 9(2) draws the line in principle and does
  not say which side a table of numbers falls on;
* what the FKM guideline's purchase permits, which its shop page does not state -- and FKM
  methods already reach the code through pyLife, whose implementation is Apache-2.0 while the
  guideline it implements was not read.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Holding(StrEnum):
    #: Read from the publisher's own repository.
    PUBLISHER_COPY = "publisher's copy"
    #: Read from a copy on a site that is not the publisher's.
    THIRD_PARTY_COPY = "third-party copy"
    #: Not held; nothing in the code may be taken from it.
    NOT_HELD = "not held"


class Taken(StrEnum):
    #: Numbers read off the document's tables or figures are encoded.
    TABLES = "tables or figures encoded"
    #: Equations and procedure are implemented, no table content.
    METHOD = "method implemented"
    #: A method the document defines, called through pyLife (Apache-2.0, 2.3.1 installed), whose
    #: implementation is pyLife's. Kryova holds and read no copy of the document.
    THROUGH_PYLIFE = "method as pyLife implements it"
    NOTHING = "nothing"


@dataclass(frozen=True)
class Document:
    key: str
    title: str
    publisher: str
    holding: Holding
    taken: Taken
    #: Where the copy the code was written from was read, or "" when not held.
    read_from: str
    read_on: str
    #: Publisher's price and where it was read, or a sentence saying it was not read.
    price: str
    #: Licence terms as found, or where they were looked for and not found.
    terms: str
    #: Modules that take from it. Empty exactly when `taken` is NOTHING.
    used_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.taken is Taken.NOTHING) != (not self.used_by):
            raise ValueError(f"{self.key}: modules are listed exactly when something is taken.")
        if self.holding is Holding.NOT_HELD and self.taken not in (Taken.NOTHING, Taken.THROUGH_PYLIFE):
            raise ValueError(
                f"{self.key}: nothing may be taken from a document that is not held."
            )
        if self.holding is not Holding.NOT_HELD and not (self.read_from and self.read_on):
            raise ValueError(f"{self.key}: a held document names where and when it was read.")


#: TRIPS Art. 9(2), quoted from https://www.wto.org/english/docs_e/legal_e/27-trips_04_e.htm,
#: read 2026-09-15. It is the principle the plan's "a method is not copyrightable; its text is"
#: rests on, and it does not say where a table of numbers falls.
TRIPS_9_2: Final = (
    "Copyright protection shall extend to expressions and not to ideas, procedures, methods of "
    "operation or mathematical concepts as such."
)

REGISTER: Final[tuple[Document, ...]] = (
    Document(
        key="en-1993-1-9",
        title="BS EN 1993-1-9:2005 incorporating corrigendum AC2, Eurocode 3 Part 1-9: Fatigue",
        publisher="CEN, published nationally (here BSI)",
        holding=Holding.THIRD_PARTY_COPY,
        taken=Taken.TABLES,
        read_from=(
            "https://gaprojekt.com/wp-content/uploads/2021/11/"
            "Eurocode-3-Design-Of-Steel-Structures.pdf"
        ),
        read_on="2026-09-14",
        price=(
            "Not read. National standards bodies publish each EN Eurocode part as a national "
            "standard (JRC, 'L3 The Eurocodes', eurocodes.jrc.ec.europa.eu, read 2026-09-15)."
        ),
        terms=(
            "The copy is a Public.Resource.Org compilation marked 'Published under the rule of "
            "law', hosted by a third party. No licence from CEN or BSI was read. CJEU case "
            "C-588/21 P (5 March 2024) concerned four harmonised toy-safety standards, per the "
            "Court's summary as reproduced by INSIGHT EU Monitoring (read 2026-09-15); the "
            "judgment itself was not retrieved (curia and EUR-Lex refused the fetch), and whether "
            "its reasoning reaches EN 1993-1-9 was not established."
        ),
        used_by=(
            "app/fatigue/eurocode3.py",
            "app/fatigue/weld_catalogue.py",
            "app/fatigue/material.py",
        ),
    ),
    Document(
        key="iiw-1823-07",
        title=(
            "IIW-1823-07, Recommendations for fatigue design of welded joints and components "
            "(Hobbacher, 2008)"
        ),
        publisher="International Institute of Welding",
        holding=Holding.THIRD_PARTY_COPY,
        taken=Taken.TABLES,
        read_from="https://svv.cz/files/IIW182307FatigueRecomm20121017.pdf",
        read_on="2026-09-15",
        price="Not read.",
        terms="Not read. The copy is hosted by a third party.",
        used_by=("app/fatigue/hotspot.py",),
    ),
    Document(
        key="naca-tn-2805",
        title="Kuhn and Hardrath, NACA Technical Note 2805 (1952), notch-size effect in fatigue",
        publisher="NACA, held by NASA's Technical Reports Server",
        holding=Holding.PUBLISHER_COPY,
        taken=Taken.TABLES,
        read_from="https://ntrs.nasa.gov/api/citations/19930083528/downloads/19930083528.pdf",
        read_on="2026-09-15",
        price="None: served free by NTRS.",
        terms="Not read. No NTRS terms of use were looked up.",
        used_by=("app/fatigue/notch.py",),
    ),
    Document(
        key="fkm-7-en",
        title=(
            "FKM Guideline, Analytical Strength Assessment, 7th edition 2020 (EN), "
            "ISBN 978-3-8163-0745-7"
        ),
        publisher="VDMA Verlag",
        holding=Holding.NOT_HELD,
        taken=Taken.NOTHING,
        read_from="",
        read_on="",
        price=(
            "EUR 320.00 incl. VAT, 232 pages, product 107457, read 2026-09-15 from "
            "https://www.vdmashop.de/en/Analytical-Strength-Assessment-7th.-Ed.-2020-EN/107457 "
            "(shown to a visitor who is not logged in; the shop says members see other prices)."
        ),
        terms="Not stated on the shop page. Ask VDMA Verlag before anything is taken from it.",
    ),
    Document(
        key="fkm-via-pylife",
        title="The FKM guidelines, as pyLife names and implements them",
        publisher="VDMA Verlag",
        holding=Holding.NOT_HELD,
        taken=Taken.THROUGH_PYLIFE,
        read_from="",
        read_on="",
        price="See fkm-7-en for the static guideline; FKM Nonlinear's price was not read.",
        terms=(
            "Not read. Two methods reach Kryova through pyLife: the FKM-Goodman mean-stress "
            "correction (mean-stress sensitivity M, M2 = M/3) and the extended Neuber rule that "
            "pyLife's docstrings cite as FKM nonlinear (2019) §2.5.7, eqs 2.5-45 and 2.5-46. "
            "Neither guideline was read, so which FKM edition defines FKM-Goodman is not established."
        ),
        used_by=(
            "app/fatigue/assessment.py",
            "app/fatigue/backend.py",
            "app/fatigue/material.py",
            "app/fatigue/notch.py",
        ),
    ),
    Document(
        key="bs-7608",
        title="BS 7608, Guide to fatigue design and assessment of steel products",
        publisher="BSI",
        holding=Holding.NOT_HELD,
        taken=Taken.NOTHING,
        read_from="",
        read_on="",
        price="Not read.",
        terms="Not read. Nothing in the repository encodes it (docs/eurocode3-fatigue-reading.md).",
    ),
)


def document(key: str) -> Document:
    for entry in REGISTER:
        if entry.key == key:
            return entry
    raise KeyError(f"No fatigue document {key!r} in the register.")


__all__ = ["REGISTER", "TRIPS_9_2", "Document", "Holding", "Taken", "document"]
