"""The conditions digital instructions for use must meet — master plan E19 task 4,
as a checker E17 task 6 cannot ship past.

Regulation (EU) 2023/1230, Article 10(7), read from the Official Journal. The
plan's paraphrase of it was close and wrong in two places that change what gets
built, so the clause is quoted here and the paraphrase is not:

* **Paper is "at the request of the user at the time of the purchase", "free of
  charge within one month"** — not "on request at no extra cost" at any time.
  The window and the deadline are both in the clause, so a delivery plan that
  says "paper on request" has not said enough to be checked.
* **The online period is the expected lifetime *and* at least ten years after
  placing on the market** — whichever ends later. A machine expected to last
  twenty years is owed twenty, and one expected to last three is still owed
  ten. `required_online_until` computes it.

Two conditions the plan left out entirely, both in the same paragraph: the
machine (or, where that is not possible, its packaging or an accompanying
document) must be **marked with how to reach the digital instructions**, and the
instructions **"shall clearly describe the product model to which they
correspond"**. The print/download/save condition also applies **"where the
instructions for use are embedded in the software of the machinery"** — a help
screen on the machine's HMI is not an exemption.

**Nothing in the product generates instructions yet**; E17 task 6 will. So this
is a checker with no caller, on purpose: the constraint exists before the
generator does, and E17 task 6's status line names `unmet` as the gate it has to
pass. It checks a *delivery plan* the manufacturer declares. It cannot see
whether a URL is really online in 2037, and it does not pretend to.

**Ten years from 29 February** has no same-day anniversary. This module takes
1 March — the reading under which "at least ten years" is certainly met — and
that is a choice made here, not a rule read from an act on computing periods.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.provisions import QuotedClause


def _clause(citation: str, quote: str) -> QuotedClause:
    return QuotedClause(citation=citation, quote=quote, sources=(OJ_L_165,))


ACCOMPANIED: Final = _clause(
    "Article 10(7), first subparagraph",
    "Manufacturers shall ensure that the machinery or related products are accompanied by the "
    "instructions for use and the information set out in Annex III. The instructions may be "
    "provided in a digital format. Such instructions and information shall clearly describe the "
    "product model to which they correspond.",
)
MARKED: Final = _clause(
    "Article 10(7), second subparagraph, point (a)",
    "mark on the machinery or related product, or, where that is not possible, on its packaging "
    "or in an accompanying document, how to access the digital instructions;",
)
PRINTABLE: Final = _clause(
    "Article 10(7), second subparagraph, point (b)",
    "present them in a format that makes it possible for the user to print and download the "
    "instructions for use and save them on an electronic device so that he or she can access "
    "them at all times, in particular during a breakdown of the machinery or related product; "
    "this requirement also applies where the instructions for use are embedded in the software "
    "of the machinery or related product;",
)
ONLINE: Final = _clause(
    "Article 10(7), second subparagraph, point (c)",
    "make them accessible online during the expected lifetime of the machinery or related product "
    "and for at least 10 years after the placing on the market of the machinery or related "
    "product.",
)
PAPER: Final = _clause(
    "Article 10(7), third subparagraph",
    "However, at the request of the user at the time of the purchase, the manufacturer shall "
    "provide the instructions for use in paper format free of charge within one month.",
)

#: "within one month", as the longest delay this checker accepts. A calendar
#: month is 28 to 31 days; 28 is the only figure that is within one month
#: whenever the purchase happens.
PAPER_DAYS: Final = 28
ONLINE_YEARS: Final = 10


class Marking(StrEnum):
    """Where the manufacturer says how to reach the digital instructions."""

    ON_THE_MACHINE = "on-the-machine"
    ON_THE_PACKAGING = "on-the-packaging"
    IN_AN_ACCOMPANYING_DOCUMENT = "in-an-accompanying-document"


@dataclass(frozen=True, slots=True)
class PaperOffer:
    """What the manufacturer undertakes when a buyer asks for paper at purchase."""

    free_of_charge: bool
    delivered_within_days: int


@dataclass(frozen=True, slots=True)
class DigitalDelivery:
    """How a manufacturer plans to deliver one machine model's instructions digitally."""

    product_model: str
    #: The model the instructions themselves name, as written in them.
    model_named_in_instructions: str
    marking: Marking | None
    #: Required when `marking` is not on the machine: the clause allows the
    #: packaging or a document only "where that is not possible".
    marking_on_machine_impossible_because: str
    printable: bool
    downloadable: bool
    savable_on_a_device: bool
    embedded_in_machine_software: bool
    #: The same three, for the embedded copy. Ignored when nothing is embedded.
    embedded_copy_printable_downloadable_savable: bool
    placed_on_market: datetime.date
    expected_end_of_life: datetime.date
    online_until: datetime.date | None
    paper: PaperOffer | None


@dataclass(frozen=True, slots=True)
class Unmet:
    """One condition the delivery plan does not meet, and the words it fails."""

    clause: QuotedClause
    because: str

    def sentence(self) -> str:
        return f"{self.clause.citation}: {self.because}"


def years_after(day: datetime.date, years: int) -> datetime.date:
    """The same calendar date `years` later; 1 March when that year has no 29 February."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return datetime.date(day.year + years, 3, 1)


def required_online_until(
    placed_on_market: datetime.date, expected_end_of_life: datetime.date
) -> datetime.date:
    """The last day Article 10(7)(c) requires the instructions to be online."""
    return max(expected_end_of_life, years_after(placed_on_market, ONLINE_YEARS))


def unmet(delivery: DigitalDelivery) -> tuple[Unmet, ...]:
    """Every Article 10(7) condition this delivery plan does not meet. Empty means
    none that this checker can see — never that the instructions are adequate."""
    found: list[Unmet] = []

    if delivery.model_named_in_instructions.strip() != delivery.product_model.strip():
        found.append(
            Unmet(
                ACCOMPANIED,
                f"the instructions name {delivery.model_named_in_instructions!r} and the machine is "
                f"{delivery.product_model!r}; they must clearly describe the model they belong to.",
            )
        )

    if delivery.marking is None:
        found.append(Unmet(MARKED, "nothing says where the machine is marked with how to reach them."))
    elif delivery.marking is not Marking.ON_THE_MACHINE and not delivery.marking_on_machine_impossible_because.strip():
        found.append(
            Unmet(
                MARKED,
                f"the access is marked {delivery.marking.value.replace('-', ' ')}, which the clause "
                "allows only where marking the machine is not possible — and no reason is given why "
                "it is not.",
            )
        )

    missing = [
        word
        for word, present in (
            ("printed", delivery.printable),
            ("downloaded", delivery.downloadable),
            ("saved on a device", delivery.savable_on_a_device),
        )
        if not present
    ]
    if missing:
        found.append(Unmet(PRINTABLE, f"the format cannot be {', '.join(missing)}."))
    if delivery.embedded_in_machine_software and not delivery.embedded_copy_printable_downloadable_savable:
        found.append(
            Unmet(
                PRINTABLE,
                "the copy embedded in the machine's software cannot be printed, downloaded and "
                "saved, and the condition applies to it too.",
            )
        )

    owed = required_online_until(delivery.placed_on_market, delivery.expected_end_of_life)
    if delivery.online_until is None:
        found.append(Unmet(ONLINE, f"no end date is given; they must be online until at least {owed}."))
    elif delivery.online_until < owed:
        found.append(
            Unmet(
                ONLINE,
                f"they are online until {delivery.online_until} and are owed until {owed} — the later "
                f"of the expected end of life and {ONLINE_YEARS} years after placing on the market.",
            )
        )

    if delivery.paper is None:
        found.append(Unmet(PAPER, "nothing says what happens when a buyer asks for paper at purchase."))
    else:
        if not delivery.paper.free_of_charge:
            found.append(Unmet(PAPER, "paper on request at purchase is charged for; it must be free."))
        if delivery.paper.delivered_within_days > PAPER_DAYS:
            found.append(
                Unmet(
                    PAPER,
                    f"paper arrives within {delivery.paper.delivered_within_days} days, and one month "
                    f"is taken here as {PAPER_DAYS}.",
                )
            )

    return tuple(found)


__all__ = [
    "ACCOMPANIED",
    "MARKED",
    "ONLINE",
    "ONLINE_YEARS",
    "PAPER",
    "PAPER_DAYS",
    "PRINTABLE",
    "DigitalDelivery",
    "Marking",
    "PaperOffer",
    "Unmet",
    "required_online_until",
    "unmet",
    "years_after",
]
