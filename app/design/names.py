"""Persistent semantic names — the thing a design refers to itself by.

**The problem this exists to remove.** CATIA names what it builds: `Pad.1`,
`Sketch.3`, `EdgeFillet.2`. Those names are positional and they are the only
handle the automation API hands back, so a design that refers to its own
geometry by them is describing an *ordering*, not a part. Insert a feature
upstream and every reference downstream now points at a different piece of
geometry — silently, because `Pad.2` still exists, it is simply no longer the
pad anyone meant. That is the topological naming problem in its cheapest form,
and it is why editing a feature tree conversationally does not survive revision.

The fix is not clever, it is only disciplined: **every element a design creates
is named by the design, at the moment it is created, and that name never
changes and is never reused.** 65 of the 201 registry operations already take a
`name` parameter, so the hook exists on the CATIA side; what was missing was a
naming *authority* on this side, which is this module.

A name is dotted and reads as ownership: `swingarm.pivot_bore` is the pivot
bore of the swingarm. It is not a path into CATIA's tree and it does not have
to mirror one — two features in different bodies may sit under the same prefix,
and a feature may be renamed in CATIA by hand without the design losing track,
because the design's name is what was written into the feature at creation.

Two decisions here are worth defending because both look wrong at first.

**Names are never reused, even after the feature is deleted.** `retire()` puts
a name beyond `allocate()`'s reach for good. This is deliberately harsher than
it needs to be for the common case (delete a rib, add a rib back). What it buys
is that everything holding a reference — a design assertion, a simulation
result, a drawing dimension, a note in the operation log — fails loudly rather
than rebinding to a different piece of geometry that happens to have inherited
the name. A stale reference that errors costs an afternoon; one that silently
resolves costs a part. `revive()` exists for when the author genuinely means
"this is the same feature again", and its whole job is to make that an explicit
claim rather than an accident.

**The projection to a CATIA name is readable rather than provably injective.**
`a.b` becomes `a_b`, so `x.y_z` and `x_y.z` would both become `x_y_z`. Escaping
the separator (`x_y__z`) would make the projection injective by construction
and would also put names in the engineer's feature tree that no engineer would
write. Readability won, and `NameTable.allocate` refuses the collision instead
— the check costs nothing and the message says exactly which two names clash.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final

from app.catia.ops import limits, vocabulary
from app.design.errors import SemanticNameError

#: One segment of a dotted name. Lowercase, starts with a letter, and may carry
#: digits and underscores after that.
#:
#: Lowercase is enforced rather than folded because CATIA's own names are
#: capitalised (`Pad.1`) and the visual difference in a feature tree is the
#: fastest way to see which names the design owns and which CATIA assigned.
_SEGMENT_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")

#: Longest a single segment may be. Not a CATIA limit — a legibility one. A
#: name is for a human to read in a tree; a 60-character segment is a sentence.
MAX_SEGMENT_CHARS: Final = 40

#: Deepest a name may nest. Three levels is `product.component.feature`, which
#: is as far as ownership reads naturally; past that it is a filesystem.
MAX_DEPTH: Final = 6

#: Hex digits of digest appended when a projected name has to be truncated.
#: 8 hex digits is 32 bits — at the scale of names in one design, collision is
#: not the risk being managed here, silent *truncation* collision is.
_DIGEST_CHARS: Final = 8

#: Names the design may not take, because an operation would not be able to
#: tell the design's element from the vocabulary value of the same spelling.
#:
#: This is the concrete failure: name a plane `xy` and every
#: `catia_sketch_create(support="xy")` in the spec becomes ambiguous between
#: that plane and CATIA's origin plane — and the daemon resolves the origin
#: plane, so the sketch lands somewhere the author did not choose and nothing
#: errors.
RESERVED: Final[frozenset[str]] = frozenset(
    word.lower()
    for group in (
        vocabulary.ORIGIN_PLANES,
        vocabulary.NAMED_FACES,
        vocabulary.FACE_POSITIONS,
        vocabulary.EDGE_SELECTORS,
        vocabulary.SIDES,
    )
    for word in group
)


@dataclass(frozen=True, order=True)
class SemanticName:
    """A dotted, design-owned name for one created element.

    Ordered so a set of names has a stable, readable iteration order — the
    compiler's determinism guarantee leans on never iterating a set of these
    in hash order.
    """

    parts: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.parts:
            raise SemanticNameError("A semantic name needs at least one segment.")
        if len(self.parts) > MAX_DEPTH:
            raise SemanticNameError(
                f"{'.'.join(self.parts)!r} nests {len(self.parts)} deep; the limit is "
                f"{MAX_DEPTH}. Ownership past three or four levels reads as a file path "
                "rather than as a name — flatten it."
            )
        for segment in self.parts:
            if not _SEGMENT_RE.match(segment):
                raise SemanticNameError(
                    f"{segment!r} is not a usable name segment. Segments are lowercase, "
                    "start with a letter, and may then carry letters, digits and "
                    "underscores — for example 'pivot_bore' or 'rib_2'."
                )
            if len(segment) > MAX_SEGMENT_CHARS:
                raise SemanticNameError(
                    f"{segment!r} is {len(segment)} characters; segments stop at "
                    f"{MAX_SEGMENT_CHARS}. Split it across the dot instead."
                )
        if self.parts[0] in RESERVED:
            raise SemanticNameError(
                f"{self.parts[0]!r} is part of the operation vocabulary (an origin plane, "
                "a named face, an edge group or a side), so an operation could not tell "
                "your element from the vocabulary value of the same name. Choose another "
                "first segment — 'plate_top' rather than 'top'."
            )

    # -- construction --------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> SemanticName:
        """Build a name from its dotted form, refusing anything malformed."""
        if not isinstance(text, str):
            raise SemanticNameError(f"A semantic name is a string; got {type(text).__name__}.")
        stripped = text.strip()
        if not stripped:
            raise SemanticNameError("A semantic name cannot be empty.")
        if "#" in stripped:
            raise SemanticNameError(
                f"{stripped!r} uses the '#' sub-entity syntax, which is reserved for "
                "referring to a face or edge *of* a feature and is not resolved yet "
                "(it needs predicate selection, roadmap A3). Refer to the feature "
                "itself for now."
            )
        if stripped.startswith(".") or stripped.endswith(".") or ".." in stripped:
            raise SemanticNameError(
                f"{stripped!r} has an empty segment. Names are dotted like "
                "'swingarm.pivot_bore', with no leading, trailing or doubled dots."
            )
        return cls(parts=tuple(stripped.split(".")))

    def child(self, segment: str) -> SemanticName:
        """The name of something this element owns."""
        return SemanticName(parts=(*self.parts, segment))

    # -- reading -------------------------------------------------------------

    def __str__(self) -> str:
        return ".".join(self.parts)

    @property
    def parent(self) -> SemanticName | None:
        return SemanticName(parts=self.parts[:-1]) if len(self.parts) > 1 else None

    @property
    def leaf(self) -> str:
        return self.parts[-1]

    def is_under(self, other: SemanticName) -> bool:
        """Is this name owned by `other`? A name is not under itself."""
        return len(self.parts) > len(other.parts) and self.parts[: len(other.parts)] == other.parts

    # -- projection ----------------------------------------------------------

    def catia_name(self) -> str:
        """The string written into CATIA's `name` parameter for this element.

        Deterministic: the same semantic name always projects to the same CATIA
        name, on every machine and every rebuild. That is what makes "the
        design's name is the feature's name" true rather than aspirational, and
        it is a prerequisite for I5 (same spec ⇒ same geometry).

        Over-long names are truncated with a digest of the *full* name appended,
        so two names that share a long prefix stay distinguishable. Truncation
        is reported nowhere and needs no report — it is stable, and the design
        never reads the CATIA name back to work out what it meant.
        """
        joined = "_".join(self.parts)
        if len(joined) <= limits.MAX_NAME_CHARS:
            return joined
        digest = hashlib.blake2b(str(self).encode("utf-8"), digest_size=16).hexdigest()
        keep = limits.MAX_NAME_CHARS - _DIGEST_CHARS - 1
        return f"{joined[:keep]}_{digest[:_DIGEST_CHARS]}"


class NameTable:
    """The design's naming authority: who owns which name, and what is retired.

    Deliberately not a `set`. The interesting behaviour is all in the refusals
    — a duplicate, a retired name coming back, two names projecting onto one
    CATIA name — and each of those wants a message rather than a `False`.
    """

    #: Bumped when the serialised shape changes. Read on load; an unknown
    #: version is refused rather than guessed at, because a half-understood
    #: name table is worse than none.
    FORMAT_VERSION: Final = 1

    def __init__(self) -> None:
        self._live: dict[SemanticName, str] = {}
        self._retired: set[SemanticName] = set()

    # -- allocation ----------------------------------------------------------

    def allocate(self, name: SemanticName | str) -> str:
        """Claim a name and return the CATIA name to create the element under."""
        wanted = name if isinstance(name, SemanticName) else SemanticName.parse(name)

        if wanted in self._live:
            raise SemanticNameError(
                f"{wanted} is already the name of an element in this design. Two "
                "elements sharing a name means every reference to it is a coin flip; "
                "give this one its own name."
            )
        if wanted in self._retired:
            raise SemanticNameError(
                f"{wanted} was the name of an element that has since been removed, and "
                "retired names are not reused — anything still holding a reference to "
                "it would silently start pointing at this new element instead. Choose "
                "a different name, or call revive() if this really is the same feature "
                "coming back."
            )

        projected = wanted.catia_name()
        clash = self._owner_of_catia_name(projected)
        if clash is not None:
            raise SemanticNameError(
                f"{wanted} and {clash} both become {projected!r} in CATIA, because the "
                "dot separator and the underscore inside a segment project the same "
                "way. Rename one of them — 'pivot_bore' under 'arm' and 'bore' under "
                "'arm.pivot' cannot both exist."
            )

        self._live[wanted] = projected
        return projected

    def revive(self, name: SemanticName | str) -> str:
        """Take a retired name back, asserting it is the same element returning.

        The caller is claiming that every reference still pointing at this name
        — an assertion, a stored result, a drawing dimension — meant *this*
        feature. That claim is sometimes true and cannot be checked from here,
        so it is made explicitly rather than inferred from a name being free.
        """
        wanted = name if isinstance(name, SemanticName) else SemanticName.parse(name)
        if wanted not in self._retired:
            raise SemanticNameError(
                f"{wanted} is not a retired name, so there is nothing to revive. Use "
                "allocate() for a new element."
            )
        self._retired.discard(wanted)
        return self.allocate(wanted)

    def retire(self, name: SemanticName | str) -> None:
        """Remove an element from the design, permanently spending its name."""
        wanted = name if isinstance(name, SemanticName) else SemanticName.parse(name)
        if wanted not in self._live:
            raise SemanticNameError(
                f"{wanted} is not an element in this design, so it cannot be retired. "
                f"{self._nearby(wanted)}"
            )
        del self._live[wanted]
        self._retired.add(wanted)

    # -- reading -------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        if isinstance(name, str):
            try:
                name = SemanticName.parse(name)
            except SemanticNameError:
                return False
        return name in self._live

    def __len__(self) -> int:
        return len(self._live)

    def catia_name(self, name: SemanticName | str) -> str:
        """What this element is called in CATIA. Raises if it is not in the design."""
        wanted = name if isinstance(name, SemanticName) else SemanticName.parse(name)
        try:
            return self._live[wanted]
        except KeyError:
            retired = " It was retired earlier in this design." if wanted in self._retired else ""
            raise SemanticNameError(
                f"Nothing in this design is called {wanted}.{retired} {self._nearby(wanted)}"
            ) from None

    def names(self) -> tuple[SemanticName, ...]:
        """Every live name, in sorted order — never in hash order."""
        return tuple(sorted(self._live))

    def retired(self) -> tuple[SemanticName, ...]:
        return tuple(sorted(self._retired))

    def under(self, prefix: SemanticName | str) -> tuple[SemanticName, ...]:
        """Every live name owned by `prefix`, sorted."""
        root = prefix if isinstance(prefix, SemanticName) else SemanticName.parse(prefix)
        return tuple(name for name in self.names() if name.is_under(root))

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serialisable snapshot, with the retired set included.

        The retired set is the half that is easy to drop and expensive to lose:
        a table reloaded without it will happily hand a dead name to a new
        element, which is exactly the failure `retire()` exists to prevent.
        """
        return {
            "format_version": self.FORMAT_VERSION,
            "live": [str(name) for name in self.names()],
            "retired": [str(name) for name in self.retired()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NameTable:
        version = data.get("format_version")
        if version != cls.FORMAT_VERSION:
            raise SemanticNameError(
                f"This name table is format version {version!r}; this build writes and "
                f"reads version {cls.FORMAT_VERSION}. Refusing to guess at the "
                "difference — a misread name table hands live names to new elements."
            )
        table = cls()
        for text in data.get("live", []):
            table.allocate(text)
        # Retired names are restored *after* the live ones so a corrupt file
        # that lists a name in both is caught by allocate() rather than
        # producing a table where the name is simultaneously taken and spent.
        for text in data.get("retired", []):
            name = SemanticName.parse(text)
            if name in table._live:
                raise SemanticNameError(
                    f"{name} is listed as both live and retired in this name table. "
                    "One of the two is wrong and there is no safe way to pick."
                )
            table._retired.add(name)
        return table

    # -- helpers -------------------------------------------------------------

    def _owner_of_catia_name(self, projected: str) -> SemanticName | None:
        for name, existing in self._live.items():
            if existing == projected:
                return name
        return None

    def _nearby(self, wanted: SemanticName) -> str:
        """Suggest the names closest to the one that was not found.

        Nearness is by shared prefix rather than by edit distance: in a design
        the useful hint is almost always 'you have the ownership right and the
        leaf wrong', and a sibling says that where a spelling-distance match
        would offer something from a different subassembly.
        """
        if not self._live:
            return "This design has no named elements yet."
        parent = wanted.parent
        siblings = [str(n) for n in self.names() if n.parent == parent] if parent else []
        pool = siblings or [str(n) for n in self.names()]
        shown = ", ".join(pool[:8])
        more = f" (+{len(pool) - 8} more)" if len(pool) > 8 else ""
        return f"Named elements: {shown}{more}."
