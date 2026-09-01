"""One index over every data module, built once at import.

The registry is the only place that knows all the data modules exist. Nothing
below it imports anything above it, so a data module can never depend on the
index that contains it, and a new module is added in exactly one place.

Duplicate keys are a hard error rather than a last-one-wins merge. Two entries
sharing a key means one is invisible, and which one depends on import order --
the kind of bug that is discovered months later by someone wondering why the
assistant never mentions Pocket.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Iterator, Mapping, Sequence

from app.catia_kb import (
    aerospace,
    formats,
    languages,
    licensing,
    platform,
    practice,
    troubleshooting,
    workbenches,
    workflows,
)
from app.catia_kb.commands import SECTIONS as COMMAND_SECTIONS
from app.catia_kb.types import Disambiguation, Entry, Kind, Section

#: Order is report order. `workbenches` is first because it publishes
#: `WORKBENCH_NAMES`, which command entries read when rendering their location.
SECTIONS: tuple[Section, ...] = (
    workbenches.SECTION,
    *COMMAND_SECTIONS,
    platform.SECTION,
    languages.SECTION,
    licensing.SECTION,
    formats.SECTION,
    aerospace.SECTION,
    workflows.SECTION,
    practice.SECTION,
    troubleshooting.SECTION,
)


def _fold(text: str) -> str:
    """Lowercase, strip accents, and reduce everything else to single spaces.

    The same normalisation is applied to aliases at index time and to the user's
    words at query time, so `Congé d'arête`, `conge d arete` and `CONGE D'ARETE`
    are one key. Hyphens and slashes become spaces because CATIA's own naming is
    inconsistent about them (`Multi-sections Solid`, `Thread/Tap`) and users are
    more inconsistent still.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    out = []
    for ch in stripped:
        out.append(ch if (ch.isalnum() or ch in "&+#") else " ")
    return " ".join("".join(out).split())


@dataclass(frozen=True, slots=True)
class Registry:
    """Every entry, indexed by key and by normalised surface form."""

    entries: Mapping[str, Entry]
    #: normalised surface -> the keys it can mean, in insertion order
    surfaces: Mapping[str, tuple[str, ...]]
    disambiguations: Mapping[str, Disambiguation]
    #: The longest alias, in words. The recogniser scans n-grams down from here.
    max_surface_words: int

    def get(self, key: str) -> Entry | None:
        return self.entries.get(key)

    def by_kind(self, kind: Kind) -> list[Entry]:
        return [item for item in self.entries.values() if item.kind is kind]

    def by_workbench(self, workbench: str) -> list[Entry]:
        return [item for item in self.entries.values() if item.workbench == workbench]

    def lookup(self, surface: str) -> tuple[Entry, ...]:
        """Every entry a surface form can mean. Empty when nothing matches."""
        keys = self.surfaces.get(_fold(surface), ())
        return tuple(self.entries[key] for key in keys)

    def disambiguation(self, surface: str) -> Disambiguation | None:
        return self.disambiguations.get(_fold(surface))

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries.values())

    def __len__(self) -> int:
        return len(self.entries)

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {"entries": len(self.entries), "surfaces": len(self.surfaces)}
        for kind in Kind:
            counts[kind.value] = sum(1 for item in self.entries.values() if item.kind is kind)
        counts["disambiguations"] = len(self.disambiguations)
        return counts


def _index_surfaces(
    entries: Mapping[str, Entry], extra: Iterable[tuple[str, str]]
) -> tuple[dict[str, tuple[str, ...]], int]:
    """Map every surface form to the keys it can mean.

    A surface deliberately maps to a *list*: `flange` is genuinely both an SMD
    command and an ASL one, and collapsing that to whichever was imported first
    is how an airframe engineer gets sent to the wrong workbench. Ambiguity is
    data here, resolved by the disambiguation table and by the brief, not by the
    index.
    """
    surfaces: dict[str, list[str]] = {}
    longest = 1

    def add(raw: str, key: str) -> None:
        nonlocal longest
        folded = _fold(raw)
        if not folded:
            return
        bucket = surfaces.setdefault(folded, [])
        if key not in bucket:
            bucket.append(key)
        longest = max(longest, len(folded.split()))

    for key, item in entries.items():
        for surface in item.surfaces():
            add(surface, key)
    # Localised command names, indexed exactly like any other alias so a German
    # or Italian question needs no language detection to be understood.
    for name, key in extra:
        if key in entries:
            add(name, key)
    return {surface: tuple(keys) for surface, keys in surfaces.items()}, longest


@lru_cache(maxsize=1)
def registry() -> Registry:
    """Build the index. Cached, because it is pure and the data is frozen."""
    entries: dict[str, Entry] = {}
    for section in SECTIONS:
        for item in section.entries:
            existing = entries.get(item.key)
            if existing is not None:
                raise ValueError(
                    f"duplicate entry key {item.key!r}: {existing.name!r} in an earlier "
                    f"section and {item.name!r} in {section.name!r}. One of them would be "
                    "invisible, and which one depends on import order."
                )
            entries[item.key] = item

    surfaces, longest = _index_surfaces(entries, languages.alias_pairs())

    disambiguations: dict[str, Disambiguation] = {}
    for section in SECTIONS:
        for fork in section.disambiguations:
            for surface in (fork.term, *fork.aliases):
                disambiguations.setdefault(_fold(surface), fork)

    return Registry(
        entries=entries,
        surfaces=surfaces,
        disambiguations=disambiguations,
        max_surface_words=longest,
    )


def missing_cross_references() -> list[tuple[str, str]]:
    """`(entry key, dangling see_also key)` for every reference that resolves to nothing.

    Exposed rather than asserted at import: a dangling cross-reference is a data
    defect worth failing a test over, but not worth refusing to start the server
    for. The renderer already skips what it cannot resolve.
    """
    index = registry()
    return [
        (item.key, ref)
        for item in index
        for ref in item.see_also
        if ref not in index.entries
    ]


def untranslated(keys: Sequence[str] | None = None) -> list[str]:
    """Entry keys named in the translation tables that no longer exist.

    The translation tables are keyed by entry key, so renaming a command's
    display name silently orphans its German name. This is what notices.
    """
    index = registry()
    candidates = keys if keys is not None else list(languages.NAMES)
    return [key for key in candidates if key not in index.entries]
