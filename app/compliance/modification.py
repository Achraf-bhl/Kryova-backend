"""A change before a machine is on the market, and a change after — master plan
E19 task 5.

Kryova regenerates a machine from a changed requirement, and the regulation
treats that same act two ways depending on one date. **Before** the machine is
placed on the market it is design work on the manufacturer's own design.
**After**, it is one of two things that look identical on screen and are
different in law:

* the manufacturer changing its own design for machines it has still to
  produce — Article 10(4) requires that series production takes "adequate
  account" of changes in the design; or
* a change to a machine already in service which, if the manufacturer did not
  foresee or plan it and it affects safety by creating a new hazard or
  increasing an existing risk, is a *substantial modification* under Article
  3(16) — and whoever carries it out "shall be considered to be a manufacturer"
  under Article 18.

**Kryova cannot tell those two apart, and it does not assess whether a change
creates a hazard.** So after the date it says both, and says which question
decides it. What it does know is the date — the manufacturer records it — and
that is the whole of what this module decides on.

**The same day counts as after.** A machine placed on the market this morning
is on the market this afternoon; reading it the other way round would give the
one ambiguous day the more comfortable answer.

Article 3(16) is quoted to the end of its point (b). The definition continues
past it, and the rest was not captured by the reading — so nothing below
paraphrases what follows. Article 10(4)'s paragraph number was read; that it
sits in Article 10 is placed from the manufacturer obligations around it
(10(3) on keeping the technical documentation, 10(7) on instructions), which
were read with their numbers.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.provisions import QuotedClause, spelled

ARTICLE_3_16: Final = QuotedClause(
    citation="Article 3(16)",
    quote=(
        "'substantial modification' means a modification of machinery or a related product, by "
        "physical or digital means after that machinery or related product has been placed on the "
        "market or put into service, which is not foreseen or planned by the manufacturer, and "
        "which affects the safety of that machinery or related product, by creating a new hazard, "
        "or by increasing an existing risk, which requires: (a) the addition of guards or "
        "protective devices to that machinery or related product the processing of which "
        "necessitates the modification of the existing safety control system; or (b) the adoption "
        "of additional protective measures to ensure the stability or mechanical strength of that "
        "machinery or related product;"
    ),
    sources=(OJ_L_165,),
    note="The definition continues past point (b); the remainder was not captured.",
)
ARTICLE_18: Final = QuotedClause(
    citation="Article 18",
    quote=(
        "A natural or legal person that carries out a substantial modification of machinery or a "
        "related product shall be considered to be a manufacturer for the purposes of this "
        "Regulation and shall be subject to the obligations of the manufacturer set out in "
        "Article 10 for that machinery or related product or, if the substantial modification has "
        "an impact on the safety of only machinery or a related product that is part of an "
        "assembly of machinery, for that affected machinery or related product, as demonstrated "
        "in the risk assessment."
    ),
    sources=(OJ_L_165,),
)
ARTICLE_10_4: Final = QuotedClause(
    citation="Article 10(4)",
    quote=(
        "Manufacturers shall ensure that procedures are in place in order that machinery or "
        "related products that are part of a series production remain in conformity with this "
        "Regulation. Adequate account shall be taken of changes in the production process or in "
        "the design or characteristics of the machinery or related product, and changes in the "
        "harmonised standards, in other technical specifications, or in the common specifications "
        "referred to in Article 20 by reference to which the conformity of the machinery or "
        "related product is declared."
    ),
    sources=(OJ_L_165,),
    note="Placed in Article 10 by the numbered paragraphs around it; see the module docstring.",
)


class LegalCharacter(StrEnum):
    DESIGN_TIME = "design-time"
    AFTER_PLACING_ON_MARKET = "after-placing-on-market"


@dataclass(frozen=True, slots=True)
class Notice:
    """What a change to this design is, in words a person reads before making it."""

    character: LegalCharacter
    placed_on_market_on: datetime.date | None
    headline: str
    detail: str
    clauses: tuple[QuotedClause, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "character": self.character.value,
            "placed_on_market_on": self.placed_on_market_on,
            "headline": self.headline,
            "detail": self.detail,
            "citations": [clause.citation for clause in self.clauses],
        }


def character(placed_on_market_on: datetime.date | None, on: datetime.date) -> LegalCharacter:
    """Whether a change made on `on` comes after the machine was placed on the market."""
    if placed_on_market_on is not None and on >= placed_on_market_on:
        return LegalCharacter.AFTER_PLACING_ON_MARKET
    return LegalCharacter.DESIGN_TIME


def notice(placed_on_market_on: datetime.date | None, on: datetime.date) -> Notice:
    if character(placed_on_market_on, on) is LegalCharacter.DESIGN_TIME:
        headline = "Design-time change."
        detail = (
            "This machine has not been recorded as placed on the market"
            + (f" before {spelled(placed_on_market_on)}" if placed_on_market_on else "")
            + ", so a change now is work on the manufacturer's own design. If units of it are "
            "already on the market or in service, record the date they were placed: a change after "
            "that is a different legal act."
        )
        return Notice(LegalCharacter.DESIGN_TIME, placed_on_market_on, headline, detail, ())

    assert placed_on_market_on is not None
    headline = (
        f"This machine was placed on the market on {spelled(placed_on_market_on)}. "
        "This is not a design-time change."
    )
    detail = (
        "Two different legal acts look the same here. If it is the manufacturer changing its own "
        "design for machines still to be produced, series production has to take adequate account "
        "of it (Article 10(4)). If it is a change to a machine already in service that the "
        "manufacturer did not foresee or plan, and it affects safety by creating a new hazard or "
        "increasing an existing risk, it is a substantial modification (Article 3(16)) — and "
        "whoever carries it out is considered the manufacturer of that machine (Article 18). "
        "Kryova cannot tell which this is, and does not assess whether the change creates a hazard."
    )
    return Notice(
        LegalCharacter.AFTER_PLACING_ON_MARKET,
        placed_on_market_on,
        headline,
        detail,
        (ARTICLE_10_4, ARTICLE_3_16, ARTICLE_18),
    )


def refuse_a_future_date(day: datetime.date, today: datetime.date) -> None:
    """A placing on the market is a thing that happened, so it has a past date."""
    if day > today:
        raise ValueError(
            f"{spelled(day)} has not happened yet. Record the date a unit of this machine was "
            "first placed on the market once it has been, not the date it is planned for."
        )


__all__ = [
    "ARTICLE_10_4",
    "ARTICLE_18",
    "ARTICLE_3_16",
    "LegalCharacter",
    "Notice",
    "character",
    "notice",
    "refuse_a_future_date",
]
