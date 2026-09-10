"""Which seat serves this conversation's next call (E15 task 4).

**This is correctness before it is scheduling, and that is the part worth
getting right.** A CATIA document is open on *one* workstation. It is not
replicated, it cannot be migrated, and a call routed to a different seat does
not fail with "wrong device" — it fails with "no such document", or worse,
succeeds against a *different* part with the same name. So a conversation that
has a document is pinned to the machine holding it, and if that machine is
offline the honest answer is to say which machine and wait.

The alternative — quietly picking another online seat — is the failure this
module exists to prevent, and it is a silent one: the agent gets a plausible
error, decides the pad failed, and rebuilds a part that already exists somewhere
nobody is looking at.

**Only the unpinned case is a choice**, and it is made by least-loaded. Two seats
paired to one user is the ordinary shape of a small team, and sending every new
conversation to the first one in a dictionary means one machine does everything.

**Pure, and that is what makes it testable.** No sockets, no COM, no seat. The
caller supplies which devices are online (`CatiaRegistry` knows) and which
documents are bound to which device (the database knows); this decides. There is
no CATIA on this machine and there never will be, so anything with a decision in
it has to be reachable without one.

**Crash recovery is named here and is not solved here.** `Outcome.stranded` is
the state a conversation is in when its seat vanished mid-build: the document
exists, the work is real, and nothing can touch it until that machine comes
back. What the *product* does about it — resume from `CatiaCheckpoint`, or offer
to rebuild elsewhere from the design record — needs a seat to develop against
and is stated as open in the phase rather than guessed at here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class Reason(StrEnum):
    """Why a seat was chosen, or why none was.

    A closed vocabulary rather than free text, because the caller renders a
    different thing for each: `PINNED` is routine, `STRANDED` is a message
    naming a machine the user has to go and wake, and `NONE_ONLINE` is the
    ordinary "no workstation" the chip already shows.
    """

    #: The conversation owns a document and the seat holding it is online.
    PINNED = "pinned"
    #: No document yet; the least-loaded online seat was chosen.
    BALANCED = "balanced"
    #: The conversation owns a document and its seat is **offline**. No other
    #: seat can serve it, whatever else is online.
    STRANDED = "stranded"
    #: No seat is online at all and the conversation owns no document.
    NONE_ONLINE = "none-online"


@dataclass(frozen=True)
class Outcome:
    """Which device to call, or nothing and why.

    `device_id` is `None` for both failing reasons, and they are *different*
    failures: `NONE_ONLINE` is solved by connecting any workstation,
    `STRANDED` is solved only by connecting **that** one. Collapsing them into
    "no workstation" would send a user to start the wrong machine.
    """

    device_id: str | None
    reason: Reason
    #: For `STRANDED`, the device the document is on. Carried even though it is
    #: offline: naming it is the entire content of the message.
    holding_device_id: str | None = None

    def __bool__(self) -> bool:
        return self.device_id is not None

    @property
    def stranded(self) -> bool:
        return self.reason is Reason.STRANDED

    def message(self) -> str:
        """What to tell the user, in the register the rest of the product uses."""
        if self.reason is Reason.PINNED:
            return f"Using workstation {self.device_id}, which holds this conversation's document."
        if self.reason is Reason.BALANCED:
            return f"Using workstation {self.device_id}."
        if self.reason is Reason.STRANDED:
            return (
                f"This conversation's CATIA document is open on workstation "
                f"{self.holding_device_id}, which is not connected. It cannot be reached from "
                "another machine — the document is on that one and nowhere else. Start the "
                "bridge there and the work carries on from where it stopped; nothing is lost."
            )
        return "No CATIA workstation is connected."


def choose(
    *,
    online: Sequence[str],
    document_device_id: str | None,
    load: Mapping[str, int] | None = None,
) -> Outcome:
    """Pick the seat for the next call.

    `document_device_id` is the device holding this conversation's document, or
    `None` when it has none — read from `CatiaDocument`, which is the single
    source of truth for that binding.

    `load` is how many conversations each online device is currently serving.
    A device missing from it counts as zero, so a caller that has no load
    information gets a stable choice rather than an error.
    """
    connected = list(dict.fromkeys(online))

    if document_device_id is not None:
        if document_device_id in connected:
            return Outcome(device_id=document_device_id, reason=Reason.PINNED)
        # Deliberately does **not** fall through to picking another seat. The
        # document is on that machine and nowhere else; another seat would fail
        # confusingly or, worse, succeed against a different part of the same
        # name.
        return Outcome(
            device_id=None,
            reason=Reason.STRANDED,
            holding_device_id=document_device_id,
        )

    if not connected:
        return Outcome(device_id=None, reason=Reason.NONE_ONLINE)

    counts = load or {}
    # Ties broken by device id rather than by iteration order: two idle seats
    # must be chosen between the same way on every worker, or two workers pick
    # differently for the same conversation and both open a document.
    chosen = min(connected, key=lambda device: (counts.get(device, 0), device))
    return Outcome(device_id=chosen, reason=Reason.BALANCED)


def rebalance_needed(load: Mapping[str, int], *, threshold: int = 3) -> bool:
    """Is one seat carrying much more than another?

    Reported rather than acted on. Moving a live conversation between seats is
    exactly the thing `choose` refuses to do, so the only honest response to an
    imbalance is to let it drain — new conversations already go to the quiet
    machine. This exists so an operator can *see* the imbalance rather than
    infer it from a slow seat.
    """
    if len(load) < 2:
        return False
    return max(load.values()) - min(load.values()) >= threshold


__all__ = ["Outcome", "Reason", "choose", "rebalance_needed"]
