"""Looking a bought-in part up, and the seam a bigger library would arrive through.

Two jobs. The first is lookup that **refuses rather than substitutes** — the
same rule the material library learned the hard way, where an unrecognised name
silently became steel and a part came back three times its real mass. A
designation that is not held raises and says what is held.

The second is `PartSource`: the boundary a larger catalogue plugs into. BOLTS,
a supplier's exported catalogue, a TraceParts import — each is a `PartSource`,
and nothing above this module learns which one answered. That is what keeps the
first-party set below from being a commitment: it is the fallback, not the
design.

Nothing here downloads anything or caches anything from a supplier. Supplier CAD
comes in by *import*, under that supplier's terms, and is never redistributed.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Protocol, runtime_checkable

from .fasteners import FASTENERS
from .types import (
    Designation,
    DesignationError,
    PartKind,
    StandardPart,
    UnknownPart,
)


@runtime_checkable
class PartSource(Protocol):
    """Somewhere standard parts come from.

    Deliberately tiny. A source answers "what have you got" and "give me this
    one" and nothing else — no search ranking, no units, no provenance rules —
    because everything else is the catalogue's job and duplicating it per source
    is how two sources start disagreeing.

    `get` returns None for a key it does not hold. It must not raise, and must
    not return a near miss: choosing the nearest part is the caller's decision
    to make explicitly, never a source's to make quietly.
    """

    @property
    def name(self) -> str:
        """What to call this source in a message to a person."""

    def keys(self) -> Iterable[str]:
        """Every key this source can answer for."""

    def get(self, key: str) -> StandardPart | None:
        """The part, or None. Never a substitute."""


class MappingSource:
    """A `PartSource` over an in-memory mapping. The first-party set uses this."""

    def __init__(self, name: str, parts: Mapping[str, StandardPart]) -> None:
        self._name = name
        self._parts = parts

    @property
    def name(self) -> str:
        return self._name

    def keys(self) -> Iterable[str]:
        return self._parts.keys()

    def get(self, key: str) -> StandardPart | None:
        return self._parts.get(key)


class Catalogue:
    """Every part source, searched in order, with one refusal message.

    Order is priority: the first source holding a key answers. That makes a
    licensed catalogue, once one is configured, take precedence over the
    first-party approximations without either of them knowing about the other.
    """

    def __init__(self, sources: Sequence[PartSource]) -> None:
        self._sources = tuple(sources)

    @property
    def sources(self) -> tuple[PartSource, ...]:
        return self._sources

    def keys(self) -> list[str]:
        seen: dict[str, None] = {}
        for source in self._sources:
            for key in source.keys():
                seen.setdefault(key, None)
        return sorted(seen)

    def __iter__(self) -> Iterator[StandardPart]:
        for key in self.keys():
            part = self._lookup(key)
            if part is not None:
                yield part

    def __len__(self) -> int:
        return len(self.keys())

    def _lookup(self, key: str) -> StandardPart | None:
        for source in self._sources:
            part = source.get(key)
            if part is not None:
                return part
        return None

    def find(self, designation: str | Designation) -> StandardPart:
        """The part for a designation, or a refusal. **Never a near miss.**

        Accepts either a `Designation` or the string an engineer would type
        (`"M8x40 ISO 4014 8.8"`). A string that is not a designation at all is
        refused with the shape it should have; a well-formed designation that is
        not stocked is refused with the nearest keys and the sizes that exist.
        """
        if isinstance(designation, Designation):
            parsed, spelling = designation, str(designation)
        else:
            spelling = designation
            try:
                parsed = Designation.parse(designation)
            except DesignationError:
                # Maybe they passed a catalogue key rather than a designation.
                part = self._lookup(designation.strip().lower())
                if part is not None:
                    return part
                raise

        part = self._lookup(parsed.key)
        if part is not None:
            return part

        keys = self.keys()
        close = difflib.get_close_matches(parsed.key, keys, n=3, cutoff=0.6)
        standards = sorted({key.split("-")[0] for key in keys})
        hint = f" Nearest held: {', '.join(close)}." if close else ""
        raise UnknownPart(
            f"{spelling} is not in the parts catalogue.{hint} "
            f"Standards held: {', '.join(standards)}, in {len(keys)} sizes and lengths "
            f"across {', '.join(source.name for source in self._sources)}. "
            f"Nothing near it is substituted: a bolt of a different length or class is "
            f"a different part with a different proof load. Add the item to a part "
            f"source, or name one that is held."
        )

    def search(
        self,
        *,
        kind: PartKind | None = None,
        standard: str | None = None,
        size: str | None = None,
        grade: str | None = None,
    ) -> list[StandardPart]:
        """Every held part matching all of the given filters, in key order."""
        wanted_standard = standard.replace(" ", "").lower() if standard else None
        return [
            part
            for part in self
            if (kind is None or part.kind is kind)
            and (
                wanted_standard is None
                or part.designation.standard.replace(" ", "").lower() == wanted_standard
            )
            and (size is None or part.designation.size.upper() == size.upper())
            and (grade is None or part.designation.grade.lower() == grade.lower())
        ]


#: The catalogue the rest of the system uses. One source today; a licensed or
#: imported catalogue goes in front of it without anything above changing.
CATALOGUE = Catalogue([MappingSource("the first-party ISO set", FASTENERS)])


def find(designation: str | Designation) -> StandardPart:
    """`Catalogue.find` against the default catalogue."""
    return CATALOGUE.find(designation)


def search(
    *,
    kind: PartKind | None = None,
    standard: str | None = None,
    size: str | None = None,
    grade: str | None = None,
) -> list[StandardPart]:
    """`Catalogue.search` against the default catalogue."""
    return CATALOGUE.search(kind=kind, standard=standard, size=size, grade=grade)


def bolt_for_clamp(
    size: str,
    stack_mm: float,
    *,
    grade: str = "8.8",
    catalogue: Catalogue = CATALOGUE,
) -> StandardPart:
    """The shortest stocked bolt of this size and class that clamps `stack_mm`.

    A first taste of the selection engine (12.4): the caller says what the joint
    is, not which bolt to use. Shortest rather than any, because a bolt longer
    than the stack needs sticks out, weighs more and is the one that fouls
    something in assembly.

    Refuses with the ranges that exist rather than returning the closest —
    a bolt whose thread does not reach through the nut is not nearly right.
    """
    if stack_mm <= 0:
        raise ValueError(
            f"A clamped stack of {stack_mm} mm is not a stack. Give the total thickness "
            f"of everything between the bolt head and the nut, washers included."
        )
    candidates = [
        part
        for part in catalogue.search(kind=PartKind.BOLT, size=size, grade=grade)
        if (low := part.value("min_clamp_length_mm")) is not None
        and (high := part.value("max_clamp_length_mm")) is not None
        and low <= stack_mm <= high
    ]
    if candidates:
        return min(candidates, key=lambda part: part.designation.length_mm or 0.0)

    stocked = catalogue.search(kind=PartKind.BOLT, size=size, grade=grade)
    if not stocked:
        raise UnknownPart(
            f"No {size} class {grade} bolts are stocked at all, so none can clamp "
            f"{stack_mm:g} mm. Held sizes and classes: "
            f"{', '.join(sorted({f'{p.designation.size} {p.designation.grade}' for p in catalogue.search(kind=PartKind.BOLT)}))}."
        )
    ranges = ", ".join(
        f"{part.designation.length_mm:g} mm clamps "
        f"{part.value('min_clamp_length_mm'):g}-{part.value('max_clamp_length_mm'):g}"
        for part in sorted(stocked, key=lambda p: p.designation.length_mm or 0.0)
        if part.value("min_clamp_length_mm") is not None
    )
    raise UnknownPart(
        f"No stocked {size} class {grade} ISO 4014 bolt clamps {stack_mm:g} mm. "
        f"Available: {ranges}. A stack below the shortest range needs a fully threaded "
        f"ISO 4017 screw, which this catalogue does not hold; above the longest needs a "
        f"length that is not stocked."
    )


#: bolt property class -> the ISO 898-2 nut class that suits it. A nut must be
#: at least its bolt's class: a weaker nut strips instead of the bolt breaking,
#: and a stripped thread gives no warning.
#:
#: There is deliberately no 12.9 row. This used to read
#: ``"8" if grade == "8.8" else "10"``, which handed a class 10 nut to a 12.9
#: bolt — under-strength, silently, and the exact substitution the rest of this
#: module refuses to make. ISO 898-2 numbers a nut class after the bolt class it
#: suits, so a 12.9 bolt needs a class 12 nut and this catalogue holds none.
_NUT_CLASS_FOR_BOLT_CLASS: Mapping[str, str] = {"8.8": "8", "10.9": "10"}


def nut_for(bolt: StandardPart, *, catalogue: Catalogue = CATALOGUE) -> StandardPart:
    """The ISO 4032 nut that matches a bolt, at a property class that suits it.

    Class 8 for an 8.8 bolt, class 10 for a 10.9 — a nut below its bolt's class
    strips before the bolt breaks, and stripped threads give no warning. A class
    this catalogue cannot match is refused rather than served the next one down.
    """
    if bolt.kind is not PartKind.BOLT:
        raise ValueError(
            f"{bolt.designation} is a {bolt.kind}, not a bolt, so it has no matching nut."
        )
    grade = _NUT_CLASS_FOR_BOLT_CLASS.get(bolt.designation.grade)
    if grade is None:
        needed = bolt.designation.grade.split(".")[0]
        pairs = ", ".join(
            f"{bolt_class} with nut class {nut_class}"
            for bolt_class, nut_class in _NUT_CLASS_FOR_BOLT_CLASS.items()
        )
        raise UnknownPart(
            f"No nut held here matches a class {bolt.designation.grade} bolt. ISO 898-2 "
            f"numbers a nut class after the bolt class it suits, so this one needs class "
            f"{needed}, and the catalogue holds {pairs} only. Nothing weaker is "
            f"substituted: a nut below its bolt's class strips before the bolt breaks, "
            f"and a stripped thread gives no warning. Add a class {needed} nut to a part "
            f"source, or specify the joint with a class this catalogue holds."
        )
    return catalogue.find(Designation("ISO 4032", bolt.designation.size, None, grade))


def washer_for(bolt: StandardPart, *, catalogue: Catalogue = CATALOGUE) -> StandardPart:
    """The ISO 7089 plain washer that matches a bolt."""
    if bolt.kind is not PartKind.BOLT:
        raise ValueError(
            f"{bolt.designation} is a {bolt.kind}, not a bolt, so it has no matching washer."
        )
    return catalogue.find(Designation("ISO 7089", bolt.designation.size, None, "200HV"))
