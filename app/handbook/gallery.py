"""The mission gallery: every ladder rung as a worked example (P10.2).

**Derived from `app.design.missions.LADDER`, never written out again.** The
ladder is the suite that decides whether this product works, and a gallery
typed up beside it is a second list that goes stale the day a rung changes —
publishing a claim that no longer matches what the code does, on the page whose
whole purpose is showing what the code does.

**What each rung does *not* claim travels with it.** `Mission.unproven` is
already part of the model: M2's frame is geometry and its welds are not sized,
so "M2 passed" must never be readable as "the welds are sized". A gallery that
printed the passes and dropped the caveats would be the most misleading page in
the product, because it would be the most convincing one. `not_claimed` is
therefore a required field of every entry, and `tests/test_docs.py` refuses an
entry that has assertions and drops the caveats.

**A rung that is not yet buildable is published as such**, with what it waits
on. The ladder models "waiting" explicitly (`needs`), and hiding those rungs
would let the gallery read as full coverage of a ladder it covers half of.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.missions import LADDER, Mission


@dataclass(frozen=True)
class GalleryEntry:
    """One rung, as an outsider reads it."""

    rung: str
    title: str
    era: str
    #: The master plan's own "what makes this hard" column, carried across so a
    #: reader knows what the rung was testing rather than only that it passed.
    hard: str
    #: `"part"`, `"assembly"`, `"sheet"` or `"pending"`. Four rather than a
    #: boolean, because "builds one part" and "builds a product graph" are
    #: different demonstrations and a reader deciding whether this product suits
    #: them cares which.
    builds: str
    #: How many claims are checked against the built part.
    assertions: int
    #: What this rung explicitly does not claim. See the module docstring: this
    #: is the field that stops a gallery from being a brochure.
    not_claimed: tuple[str, ...]
    #: What a pending rung is waiting on, each naming the phase that owns it.
    waiting_on: tuple[str, ...]

    @property
    def buildable(self) -> bool:
        return self.builds != "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "title": self.title,
            "era": self.era,
            "hard": self.hard,
            "builds": self.builds,
            "buildable": self.buildable,
            "assertions": self.assertions,
            "not_claimed": list(self.not_claimed),
            "waiting_on": list(self.waiting_on),
        }


def _builds(mission: Mission) -> str:
    if mission.spec is not None:
        return "part"
    if mission.assembly is not None:
        return "assembly"
    if mission.folded is not None:
        return "sheet"
    return "pending"


def entry_for(mission: Mission) -> GalleryEntry:
    return GalleryEntry(
        rung=mission.rung,
        title=mission.title,
        era=mission.era,
        hard=mission.hard,
        builds=_builds(mission),
        assertions=len(mission.assertions),
        not_claimed=tuple(mission.unproven),
        waiting_on=tuple(mission.needs),
    )


def gallery() -> tuple[GalleryEntry, ...]:
    """Every rung in ladder order."""
    return tuple(entry_for(mission) for mission in LADDER)


def headline() -> str:
    """One sentence, with the denominator.

    The denominator is not decoration. "Four missions build" invites the reader
    to supply their own idea of how many there are; "four of nine" tells them
    what they are looking at, and is the same discipline the validation
    register's headline follows.
    """
    entries = gallery()
    buildable = sum(1 for entry in entries if entry.buildable)
    return (
        f"{buildable} of the {len(entries)} ladder missions build today. "
        "Each carries what it does not claim, because a rung that passes is not "
        "a machine that is finished."
    )


__all__ = ["GalleryEntry", "entry_for", "gallery", "headline"]
