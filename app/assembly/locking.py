"""Several agents on one product without corrupting it — master plan 14.5.

`structure.py` says what a product *is*. This says what happens when two authors —
two engineers, or the agent and an engineer, or ten agents decomposing a machine in
parallel — work on one at the same time. Without it, `ProductStructure` is frozen
and safe and the *product* is not: two callers each read the head, each build a new
structure from it, each store theirs, and the second silently erases the first. The
data structure cannot see that happen; only a repository can.

**Two mechanisms, and they answer different questions.**

* **Optimistic concurrency** (`ProductRepository.commit`) answers *did anything move
  under me*. Every commit names the revision it was written against, and a commit
  against anything but the head is refused with what changed and who changed it.
  This is the guard that cannot be forgotten: it applies to every write, including
  the ones nobody thought to lock.
* **Leases** (`LeaseBook`) answer *is anyone else already on this*. They are taken
  per **component**, held for a stated time, and enforced at commit: while one
  author holds `frame`, another author's commit that touches `frame` is refused even
  if their base is current. That turns a merge conflict discovered after an hour of
  work into a refusal in the first second.

**Why leases are per component and never per occurrence.** A bolt used forty times
is one component and forty occurrences (`structure.py`'s first rule), and it is the
*definition* two authors would fight over. Locking `frame/leg.2` would let two people
edit the same leg design through two different paths and call it disjoint.

**There is no clock in this module.** Every call that cares about time takes `now`.
A lease whose expiry is read from the machine's clock cannot be tested without
sleeping, cannot be reasoned about across two processes whose clocks differ, and
turns "the lease expired" into an untraceable failure — the same argument
`app/design/execute.py` makes for injecting its runner. The caller owns the clock.

**A lease is not a permission to be wrong.** Holding one does not exempt a commit
from the base check: if the head moved while you held a lease on something else, you
are still writing against a stale product and are still refused. The two mechanisms
compose; neither replaces the other.

**Merging is by component and it is exact, not clever.** `merge` takes the base and
two structures descended from it and applies the changes that do not overlap. Where
both sides changed the same component to different values, it **refuses** and names
both authors: there is no rule that can pick between two engineers' bracket designs,
and a repository that guessed would be worse than one that stopped. Three-way merge
of the *contents* of a component is not attempted for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.assembly.errors import LockError, MergeConflict
from app.assembly.structure import Component, ProductStructure


@dataclass(frozen=True)
class Lease:
    """One author's claim on one component, until a stated time.

    `expires_at` is in the caller's own time base — seconds, usually monotonic — and
    is compared against the `now` every call is given. A lease that has expired is
    not deleted anywhere: it stays in the book as history and simply stops being
    live, so "who held this when the commit was refused" is still answerable.
    """

    component: str
    holder: str
    taken_at: float
    expires_at: float
    note: str = ""

    def live_at(self, now: float) -> bool:
        return now < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "holder": self.holder,
            "taken_at": self.taken_at,
            "expires_at": self.expires_at,
            "note": self.note,
        }


class LeaseBook:
    """Who is working on what, and until when.

    Not a dataclass for `ProductStructure`'s reason: the interesting behaviour is the
    refusals, and each one wants a message rather than a `False`.
    """

    __slots__ = ("_leases",)

    def __init__(self) -> None:
        self._leases: dict[str, Lease] = {}

    def take(
        self,
        component: str,
        holder: str,
        *,
        now: float,
        seconds: float,
        note: str = "",
    ) -> Lease:
        """Claim a component until `now + seconds`.

        Re-taking a lease you already hold extends it — that is a renewal, not a
        conflict, and refusing it would make a long edit impossible to keep alive.
        Taking one somebody else holds is refused, naming them and when they lose it.
        """
        if not holder.strip():
            raise LockError(
                "A lease needs a holder. It is the name a refusal will print at "
                "whoever collides with it, so 'who has the frame' has an answer."
            )
        if seconds <= 0.0:
            raise LockError(
                f"A lease on {component!r} for {seconds:g} s has expired before it was "
                "taken, so it would protect nothing while looking like protection. "
                "Give it the time the edit will actually take."
            )
        current = self._leases.get(component)
        if current is not None and current.live_at(now) and current.holder != holder:
            raise LockError(
                f"{holder!r} cannot take {component!r}: {current.holder!r} has held it "
                f"since {current.taken_at:g} and keeps it for another "
                f"{current.expires_at - now:g} s"
                + (f" ({current.note})" if current.note else "")
                + ". Wait for it, work on another component, or agree the handover — "
                "two authors editing one component is the merge conflict this book "
                "exists to make impossible."
            )
        lease = Lease(
            component=component,
            holder=holder,
            taken_at=now if current is None or current.holder != holder else current.taken_at,
            expires_at=now + seconds,
            note=note or (current.note if current is not None else ""),
        )
        self._leases[component] = lease
        return lease

    def release(self, component: str, holder: str, *, now: float) -> None:
        """Give a component back. Releasing what you do not hold is refused."""
        current = self._leases.get(component)
        if current is None or not current.live_at(now):
            raise LockError(
                f"{holder!r} released {component!r}, which nobody is holding. Releasing "
                "a lease that is not there hides the real problem — the lease expired "
                "mid-edit, or it was taken on a different component."
            )
        if current.holder != holder:
            raise LockError(
                f"{holder!r} cannot release {component!r}: it is held by "
                f"{current.holder!r}. A lease is released by its holder, or it expires; "
                "letting anyone drop anyone's lease is the same as having none."
            )
        del self._leases[component]

    def holder_of(self, component: str, *, now: float) -> str | None:
        """Who holds this component right now, or `None`."""
        current = self._leases.get(component)
        return current.holder if current is not None and current.live_at(now) else None

    def live(self, *, now: float) -> tuple[Lease, ...]:
        """Every lease still in force, by component name, for a report."""
        return tuple(
            lease
            for _name, lease in sorted(self._leases.items())
            if lease.live_at(now)
        )

    def held_by_others(
        self, components: Iterable[str], holder: str, *, now: float
    ) -> tuple[Lease, ...]:
        """The live leases somebody other than `holder` has on `components`."""
        return tuple(
            lease
            for lease in (self._leases.get(name) for name in sorted(set(components)))
            if lease is not None and lease.live_at(now) and lease.holder != holder
        )


@dataclass(frozen=True)
class Change:
    """What one commit did to the product, by component.

    Names only — the versions are in the revisions either side. A change set is what
    a lease check is run against and what a merge reasons about, and both want the
    question "which components did this touch" answered without loading two
    structures and diffing them again.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    root_changed: bool = False

    @property
    def touched(self) -> tuple[str, ...]:
        """Every component this change reached, sorted, without duplicates."""
        return tuple(sorted({*self.added, *self.removed, *self.modified}))

    @property
    def is_empty(self) -> bool:
        return not self.touched and not self.root_changed

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added {', '.join(self.added)}")
        if self.removed:
            parts.append(f"removed {', '.join(self.removed)}")
        if self.modified:
            parts.append(f"changed {', '.join(self.modified)}")
        if self.root_changed:
            parts.append("re-rooted the product")
        return "; ".join(parts) or "changed nothing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "modified": list(self.modified),
            "root_changed": self.root_changed,
        }


def changes_between(before: ProductStructure, after: ProductStructure) -> Change:
    """Which components differ between two structures.

    Compares components by value — they are frozen dataclasses — so a component
    rebuilt identically is *not* a change, and two authors who both add the same
    bolt do not conflict over it. That is deliberate: conflicting on equal content
    would make a rebuild-from-spec workflow unmergeable.
    """
    old = {name: before.component(name) for name in before.component_names()}
    new = {name: after.component(name) for name in after.component_names()}
    added = tuple(sorted(set(new) - set(old)))
    removed = tuple(sorted(set(old) - set(new)))
    modified = tuple(sorted(n for n in set(old) & set(new) if old[n] != new[n]))
    return Change(
        added=added,
        removed=removed,
        modified=modified,
        root_changed=before.root != after.root,
    )


@dataclass(frozen=True)
class Revision:
    """One committed state of the product, and how it got there.

    `parent` is the digest the author wrote against, so the history is a chain that
    can be checked rather than a list that is believed. `number` counts from 1 at the
    first commit, for a reader — the digest is the identity.
    """

    number: int
    digest: str
    structure: ProductStructure
    author: str
    change: Change
    parent: str | None = None
    note: str = ""
    at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "digest": self.digest,
            "author": self.author,
            "parent": self.parent,
            "note": self.note,
            "at": self.at,
            "change": self.change.to_dict(),
        }


class ProductRepository:
    """One product, its history, and the rules that keep two authors from erasing each other.

    The whole of 14.5 is here. A repository holds the current structure and every
    revision behind it; `commit` is the only way in, and it refuses three things:

    1. a commit written against anything but the current head — *lost update*;
    2. a commit touching a component somebody else has leased — *collision*;
    3. a commit that changed nothing — a history entry that records no work makes
       every later "what happened here" answer misleading.
    """

    __slots__ = ("_history", "_leases")

    def __init__(
        self,
        structure: ProductStructure,
        *,
        author: str = "initial",
        note: str = "initial revision",
        at: float | None = None,
    ) -> None:
        self._leases = LeaseBook()
        self._history: list[Revision] = [
            Revision(
                number=1,
                digest=structure.digest(),
                structure=structure,
                author=author,
                change=Change(),
                parent=None,
                note=note,
                at=at,
            )
        ]

    # -- reading -------------------------------------------------------------

    @property
    def head(self) -> Revision:
        return self._history[-1]

    @property
    def structure(self) -> ProductStructure:
        return self._history[-1].structure

    @property
    def leases(self) -> LeaseBook:
        return self._leases

    def history(self) -> tuple[Revision, ...]:
        return tuple(self._history)

    def revision(self, digest: str) -> Revision:
        for entry in self._history:
            if entry.digest == digest:
                return entry
        raise LockError(
            f"No revision {digest!r} in this product's history. It has "
            f"{len(self._history)} revisions ending at {self.head.digest!r}. A digest "
            "from another product, or from a structure that was never committed, is "
            "the usual cause."
        )

    # -- writing -------------------------------------------------------------

    def commit(
        self,
        structure: ProductStructure,
        *,
        author: str,
        base: str,
        note: str = "",
        now: float = 0.0,
        at: float | None = None,
    ) -> Revision:
        """Record a new revision, or refuse and say who to talk to.

        `base` is the digest the work was written against. Passing the head's digest
        is the whole protocol: an author who read the head, edited, and committed
        succeeds; one who read it, waited while somebody else committed, and then
        wrote is refused with what moved.
        """
        head = self.head
        if base != head.digest:
            known = any(entry.digest == base for entry in self._history)
            moved = (
                self.revision(base).number if known else None
            )
            behind = (
                f"revision {moved} of {len(self._history)}"
                if moved is not None
                else "a revision this product has never had"
            )
            raise LockError(
                f"{author!r} wrote against {behind} and the head is now "
                f"{head.number} ({head.digest!r}), committed by {head.author!r}, which "
                f"{head.change.describe()}. Nothing has been lost and nothing has been "
                "written: re-read the head and merge, or use merge() with the base you "
                "started from."
            )
        change = changes_between(head.structure, structure)
        if change.is_empty:
            raise LockError(
                f"{author!r} committed no change to {structure.root!r}. A revision that "
                "records no work makes the history lie about what happened — if the "
                "point is a note, put it on the revision that did the work."
            )
        blocked = self._leases.held_by_others(change.touched, author, now=now)
        if blocked:
            detail = "; ".join(
                f"{lease.component} is held by {lease.holder!r} for another "
                f"{lease.expires_at - now:g} s"
                for lease in blocked
            )
            raise LockError(
                f"{author!r} cannot commit: {detail}. That is exactly the collision "
                "leases exist to catch early — take the lease before the edit, or wait "
                "for theirs to end."
            )
        entry = Revision(
            number=head.number + 1,
            digest=structure.digest(),
            structure=structure,
            author=author,
            change=change,
            parent=head.digest,
            note=note,
            at=at,
        )
        self._history.append(entry)
        return entry

    def merge(
        self,
        base: str,
        ours: ProductStructure,
        theirs: ProductStructure,
        *,
        our_author: str = "ours",
        their_author: str = "theirs",
    ) -> ProductStructure:
        """Combine two structures descended from one base, or refuse naming both.

        Component by component. A component only one side touched takes that side's
        version; a component both sides changed the same way is not a conflict,
        because the result is unambiguous. Anything else raises `MergeConflict` with
        both authors on it — see the module docstring on why nothing is guessed.
        """
        origin = self.revision(base).structure
        ours_change = changes_between(origin, ours)
        theirs_change = changes_between(origin, theirs)
        if origin.root != ours.root and origin.root != theirs.root and ours.root != theirs.root:
            raise MergeConflict(
                f"{our_author!r} re-rooted the product at {ours.root!r} and "
                f"{their_author!r} at {theirs.root!r}, from {origin.root!r}. A product "
                "has one root and nothing here can choose between two — agree which "
                "assembly is the machine, then merge."
            )
        contested = set(ours_change.touched) & set(theirs_change.touched)
        merged: dict[str, Component] = {
            name: origin.component(name) for name in origin.component_names()
        }
        for name in contested:
            mine = ours.component(name) if name in ours else None
            yours = theirs.component(name) if name in theirs else None
            if mine == yours:
                continue
            raise MergeConflict(
                f"{our_author!r} and {their_author!r} both changed {name!r} from the "
                f"same base and their versions differ "
                f"({'removed it' if mine is None else 'kept it'} against "
                f"{'removed it' if yours is None else 'kept it'}). Nothing here can "
                "choose between two engineers' versions of one component: agree which "
                "one is the design, or split it into two components and instance both."
            )
        for source, change in ((ours, ours_change), (theirs, theirs_change)):
            for name in (*change.added, *change.modified):
                merged[name] = source.component(name)
            for name in change.removed:
                merged.pop(name, None)
        root = ours.root if ours.root != origin.root else theirs.root
        return ProductStructure(root=root, components=merged.values())

    def report(self, *, now: float = 0.0) -> str:
        """The state a person asks for: where the product is and who is holding what."""
        head = self.head
        lines = [
            f"{head.structure.root}: revision {head.number}, {head.digest}, "
            f"by {head.author} ({head.change.describe()}).",
        ]
        live = self._leases.live(now=now)
        if live:
            lines.append(
                "Held: "
                + ", ".join(
                    f"{lease.component} by {lease.holder} "
                    f"for {lease.expires_at - now:g} s"
                    for lease in live
                )
                + "."
            )
        else:
            lines.append("Nothing is leased.")
        return " ".join(lines)


@dataclass
class Workspace:
    """One author's view of a repository: the base they read and the leases they hold.

    A convenience over `ProductRepository`, and a deliberately thin one — it stores
    the base digest so an author cannot commit against a head they never read, which
    is the mistake the optimistic check exists to catch and the easiest one to make
    by passing `repository.head.digest` straight back in.

    **This one is mutable, and it is the only mutable thing in the package.** A
    workspace *is* the moving part: it advances onto its own commit and it drops the
    leases it held. Everything it points at — the structure, the revision, the lease
    — stays frozen, so nothing shared between two workspaces can be edited through
    one of them.
    """

    repository: ProductRepository
    author: str
    base: str
    held: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def open(cls, repository: ProductRepository, author: str) -> Workspace:
        return cls(repository=repository, author=author, base=repository.head.digest)

    @property
    def structure(self) -> ProductStructure:
        """The product as it was when this workspace was opened."""
        return self.repository.revision(self.base).structure

    def claim(self, *components: str, now: float, seconds: float, note: str = "") -> Workspace:
        """Take a lease on each component, and remember to give it back on commit."""
        for name in components:
            self.repository.leases.take(
                name, self.author, now=now, seconds=seconds, note=note
            )
        self.held = (*self.held, *components)
        return self

    def commit(
        self, structure: ProductStructure, *, note: str = "", now: float = 0.0
    ) -> Revision:
        """Commit against the base this workspace was opened at, then move onto it.

        The leases are released **after** the commit is accepted, never before: a
        refused commit leaves the author still holding their components, which is
        what lets them fix the work and try again rather than race somebody who
        picked the lease up in between.
        """
        entry = self.repository.commit(
            structure, author=self.author, base=self.base, note=note, now=now
        )
        for name in self.held:
            if self.repository.leases.holder_of(name, now=now) == self.author:
                self.repository.leases.release(name, self.author, now=now)
        self.base = entry.digest
        self.held = ()
        return entry

    def refresh(self) -> Workspace:
        """Move onto the current head, keeping the leases already held."""
        self.base = self.repository.head.digest
        return self


def merged_view(
    structure: ProductStructure, components: Mapping[str, Component]
) -> ProductStructure:
    """`structure` with some components replaced — the one edit primitive callers need.

    `ProductStructure` is frozen and validated at construction, so every edit is a
    rebuild; doing that by hand means re-listing every component and is where a
    caller drops one. Refuses an unknown component rather than adding it, because
    "replace" and "add" are different intentions and a typo in a name is far more
    likely than a deliberate insertion through this door.
    """
    existing = {name: structure.component(name) for name in structure.component_names()}
    unknown = sorted(set(components) - set(existing))
    if unknown:
        raise LockError(
            f"{', '.join(unknown)} is not in {structure.root!r}, so there is nothing to "
            "replace. Build the structure with the new component instead — this "
            "replaces, it does not add, so a mistyped name cannot quietly become a new "
            "part."
        )
    existing.update(components)
    return ProductStructure(root=structure.root, components=existing.values())


__all__ = [
    "Change",
    "Lease",
    "LeaseBook",
    "ProductRepository",
    "Revision",
    "Workspace",
    "changes_between",
    "merged_view",
]
