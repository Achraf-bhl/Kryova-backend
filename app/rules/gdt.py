"""Geometric dimensioning and tolerancing, held as data and checked for sense.

Phase 13.2, and the half of it that is honest today. A feature control frame is a
sentence in a formal language — characteristic, tolerance zone, material
condition, datum reference frame — and most of what is wrong with a real one is
wrong *in the sentence*, before any geometry is consulted: a datum letter that
nothing establishes, a material modifier on a characteristic that cannot take
one, a diametral zone on a flatness, a zero tolerance with no MMC to make it
mean something. Those are refusals this module can make with certainty and a
useful message, so it makes them.

**What it deliberately does not do: evaluate a tolerance zone.** Nothing here
takes a measurement payload, a mesh or a shape. Whether an actual axis lies
inside a Ø0.2 cylindrical zone at MMC is an *inspection* question — it needs the
as-produced feature size to know the bonus tolerance, and as-produced sizes are
CMM data, not model data. A module that answered it from the model would be
answering a different question in the same words, and the answer would be
believed. `app.rules.stackup` says the same thing about the same boundary and
that is why it points here. When Kryova federates an inspection source, the
evaluator is a new module that reads this one; it is not a method that quietly
appears on `FeatureControlFrame`.

**The tables are ASME Y14.5 read as data, and the entries with real ambiguity are
the ones left permissive.** An over-refusal is not the safe direction — the
recovery from a refusal is to write something else, so a frame refused wrongly
becomes a drawing that says something wrong — so where practice genuinely varies
(how many datum references a runout callout may carry) the check is wide, and
where the standard is unambiguous (circularity is a surface element control and
takes no material modifier; concentricity and symmetry are RFS only) it is
exact. Every entry carries its reason in the table.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.rules.errors import GdtError

#: Letters a datum may not be, because they are read as digits or as other
#: symbols on a drawing. Y14.5 names exactly these three.
RESERVED_DATUM_LETTERS: Final = frozenset({"I", "O", "Q"})

#: The most datum features one frame may reference: primary, secondary, tertiary.
#: A fourth is not a precedence anybody can order.
MAXIMUM_DATUM_REFERENCES: Final = 3


class MaterialCondition(StrEnum):
    """The material condition a tolerance or a datum reference is taken at.

    `RFS` — regardless of feature size — is the default in Y14.5 and is what an
    unmarked frame means, so it is the enum's zero value rather than `None`: a
    frame that says nothing is making a statement, not omitting one.
    """

    RFS = "rfs"
    MMC = "mmc"
    LMC = "lmc"


#: The symbol each condition prints as, for a frame rendered into a note.
_CONDITION_SYMBOL: Final[dict[MaterialCondition, str]] = {
    MaterialCondition.RFS: "",
    MaterialCondition.MMC: "Ⓜ",  # circled M
    MaterialCondition.LMC: "Ⓛ",  # circled L
}


class Category(StrEnum):
    """Which family a characteristic belongs to. Decides whether datums apply."""

    FORM = "form"
    PROFILE = "profile"
    ORIENTATION = "orientation"
    LOCATION = "location"
    RUNOUT = "runout"


class DatumRule(StrEnum):
    """What a characteristic does about datum references."""

    #: A form control is a claim about a feature against itself. A datum on one is
    #: not a stricter callout, it is a different characteristic written wrongly.
    FORBIDDEN = "forbidden"

    #: Profile may be related to a datum reference frame or may be a free-standing
    #: form-and-size control. Both are correct callouts.
    OPTIONAL = "optional"

    #: Orientation, location and runout are all relationships. Without a datum
    #: there is nothing to be oriented, located or run out *to*.
    REQUIRED = "required"


class Characteristic(StrEnum):
    """The fourteen geometric characteristics."""

    STRAIGHTNESS = "straightness"
    FLATNESS = "flatness"
    CIRCULARITY = "circularity"
    CYLINDRICITY = "cylindricity"
    PROFILE_OF_A_LINE = "profile_of_a_line"
    PROFILE_OF_A_SURFACE = "profile_of_a_surface"
    ANGULARITY = "angularity"
    PERPENDICULARITY = "perpendicularity"
    PARALLELISM = "parallelism"
    POSITION = "position"
    CONCENTRICITY = "concentricity"
    SYMMETRY = "symmetry"
    CIRCULAR_RUNOUT = "circular_runout"
    TOTAL_RUNOUT = "total_runout"


@dataclass(frozen=True)
class Grammar:
    """What one characteristic is allowed to say, with why."""

    category: Category
    datums: DatumRule
    symbol: str

    #: Material conditions this characteristic's *tolerance* may be taken at. A
    #: modifier is only meaningful on a feature of size, so the surface-element
    #: controls carry `{RFS}` alone.
    conditions: frozenset[MaterialCondition]

    #: Whether the zone may be declared diametral. A zone around an axis or a
    #: point can be; a zone between two parallel planes or two concentric circles
    #: cannot, and writing the diameter symbol on one is a drawing error.
    diametral_zone: bool

    #: Why the two restrictions above are what they are, quoted into the refusal.
    reason: str


#: Y14.5 as a table. The `conditions` column is the one worth reading twice: a
#: material modifier adjusts a tolerance by how far the feature is from its
#: worst-case size, so it is meaningful only where the control applies to a
#: feature of size — an axis or a derived median plane. Circularity and
#: cylindricity control surface elements and have no such derived feature;
#: concentricity and symmetry are defined on the derived median points and are
#: RFS by definition in Y14.5-2009 (and withdrawn entirely in 2018); runout is
#: measured with the part rotating about the datum axis and has no size term at
#: all.
GRAMMAR: Final[Mapping[Characteristic, Grammar]] = {
    Characteristic.STRAIGHTNESS: Grammar(
        category=Category.FORM,
        datums=DatumRule.FORBIDDEN,
        symbol="—",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=True,
        reason=(
            "straightness applied to the axis of a feature of size takes a material "
            "modifier and a diametral zone; applied to a surface line element it takes "
            "neither, and the modifier is what distinguishes the two"
        ),
    ),
    Characteristic.FLATNESS: Grammar(
        category=Category.FORM,
        datums=DatumRule.FORBIDDEN,
        symbol="⬱",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=False,
        reason=(
            "a flatness zone is the space between two parallel planes, which has no "
            "diameter; the material modifier is legal only in the derived-median-plane "
            "form of the control"
        ),
    ),
    Characteristic.CIRCULARITY: Grammar(
        category=Category.FORM,
        datums=DatumRule.FORBIDDEN,
        symbol="○",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "circularity controls surface elements in a cross-section, so there is no "
            "feature of size for a material modifier to act on, and the zone is the "
            "radial space between two concentric circles rather than a diameter"
        ),
    ),
    Characteristic.CYLINDRICITY: Grammar(
        category=Category.FORM,
        datums=DatumRule.FORBIDDEN,
        symbol="⌭",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "cylindricity controls the whole surface against two coaxial cylinders; the "
            "zone is the radial space between them and there is no derived feature for a "
            "material modifier"
        ),
    ),
    Characteristic.PROFILE_OF_A_LINE: Grammar(
        category=Category.PROFILE,
        datums=DatumRule.OPTIONAL,
        symbol="⌒",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "a profile zone is a band about the true profile, related to datums or not; "
            "it has no diameter and no size term for a modifier"
        ),
    ),
    Characteristic.PROFILE_OF_A_SURFACE: Grammar(
        category=Category.PROFILE,
        datums=DatumRule.OPTIONAL,
        symbol="⌓",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "a profile zone is a band about the true profile, related to datums or not; "
            "it has no diameter and no size term for a modifier"
        ),
    ),
    Characteristic.ANGULARITY: Grammar(
        category=Category.ORIENTATION,
        datums=DatumRule.REQUIRED,
        symbol="∠",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=True,
        reason=(
            "orientation is a relationship, so it needs the datum it is oriented to; "
            "applied to a feature of size it may take a modifier and a diametral zone"
        ),
    ),
    Characteristic.PERPENDICULARITY: Grammar(
        category=Category.ORIENTATION,
        datums=DatumRule.REQUIRED,
        symbol="⊥",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=True,
        reason=(
            "orientation is a relationship, so it needs the datum it is oriented to; "
            "applied to a feature of size it may take a modifier and a diametral zone"
        ),
    ),
    Characteristic.PARALLELISM: Grammar(
        category=Category.ORIENTATION,
        datums=DatumRule.REQUIRED,
        symbol="∥",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=True,
        reason=(
            "orientation is a relationship, so it needs the datum it is oriented to; "
            "applied to a feature of size it may take a modifier and a diametral zone"
        ),
    ),
    Characteristic.POSITION: Grammar(
        category=Category.LOCATION,
        datums=DatumRule.REQUIRED,
        symbol="⌖",
        conditions=frozenset({MaterialCondition.RFS, MaterialCondition.MMC,
                              MaterialCondition.LMC}),
        diametral_zone=True,
        reason=(
            "position locates a feature of size in a datum reference frame, which is why "
            "it is the one characteristic that takes everything: a diametral zone about "
            "the true position and a material modifier that earns bonus tolerance"
        ),
    ),
    Characteristic.CONCENTRICITY: Grammar(
        category=Category.LOCATION,
        datums=DatumRule.REQUIRED,
        symbol="◎",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=True,
        reason=(
            "concentricity controls the derived median points of a feature about a datum "
            "axis and is regardless of feature size by definition in Y14.5-2009. If a "
            "modifier is wanted here, the callout that was meant is position"
        ),
    ),
    Characteristic.SYMMETRY: Grammar(
        category=Category.LOCATION,
        datums=DatumRule.REQUIRED,
        symbol="⍐",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "symmetry controls the derived median points about a datum centre plane, is "
            "regardless of feature size by definition, and its zone is between two "
            "parallel planes. If a modifier is wanted here, the callout meant is position"
        ),
    ),
    Characteristic.CIRCULAR_RUNOUT: Grammar(
        category=Category.RUNOUT,
        datums=DatumRule.REQUIRED,
        symbol="↗",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "runout is full indicator movement measured with the part rotating about the "
            "datum axis: it is always regardless of feature size, and the zone is a "
            "radial band, not a diameter"
        ),
    ),
    Characteristic.TOTAL_RUNOUT: Grammar(
        category=Category.RUNOUT,
        datums=DatumRule.REQUIRED,
        symbol="⤨",
        conditions=frozenset({MaterialCondition.RFS}),
        diametral_zone=False,
        reason=(
            "runout is full indicator movement measured with the part rotating about the "
            "datum axis: it is always regardless of feature size, and the zone is a "
            "radial band, not a diameter"
        ),
    ),
}


def _check_letter(letter: str, *, where: str) -> str:
    cleaned = str(letter).strip().upper()
    if not cleaned:
        raise GdtError(f"{where}: a datum needs a letter. Use A, B, C … .")
    if len(cleaned) != 1 or not cleaned.isascii() or not cleaned.isalpha():
        raise GdtError(
            f"{where}: {letter!r} is not a datum letter. A datum identifier is a single "
            "letter A-Z. Compound datums written 'A-B' are two datum features "
            "establishing one datum and are not modelled here — declare them separately "
            "and reference both."
        )
    if cleaned in RESERVED_DATUM_LETTERS:
        reserved = ", ".join(sorted(RESERVED_DATUM_LETTERS))
        raise GdtError(
            f"{where}: {cleaned!r} may not be a datum letter. {reserved} are excluded "
            "because they read as digits or as other drawing symbols. Use the next free "
            "letter."
        )
    return cleaned


@dataclass(frozen=True)
class Datum:
    """One datum, and the feature that establishes it.

    `feature` is required and is free text — the name of the face, bore or slot on
    the part. This module never resolves it against geometry; it is what a person
    and a downstream inspection plan read, and a datum with nothing named against
    it is a letter in a frame that nobody can set up to.
    """

    letter: str
    feature: str
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "letter", _check_letter(self.letter, where="datum"))
        if not self.feature or not self.feature.strip():
            raise GdtError(
                f"Datum {self.letter} names no feature. A datum is established by "
                "something you can put an indicator on — a face, a bore, a pair of "
                "flats. Pass feature='…'."
            )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"letter": self.letter, "feature": self.feature}
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class DatumReference:
    """One datum letter as referenced by a frame, at a material boundary."""

    letter: str
    condition: MaterialCondition = MaterialCondition.RFS

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "letter", _check_letter(self.letter, where="datum reference")
        )

    def __str__(self) -> str:
        return f"{self.letter}{_CONDITION_SYMBOL[self.condition]}"

    def to_dict(self) -> dict[str, Any]:
        return {"letter": self.letter, "condition": str(self.condition)}


@dataclass(frozen=True)
class DatumScheme:
    """The datums declared on one part.

    Held apart from the frames because a datum letter is declared once and
    referenced many times, and because "B is referenced but never established" is
    the commonest thing wrong with a real drawing — a check that needs both halves
    in one place to be possible at all.
    """

    datums: tuple[Datum, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "datums", tuple(self.datums))
        seen: set[str] = set()
        for datum in self.datums:
            if datum.letter in seen:
                raise GdtError(
                    f"Datum {datum.letter} is declared twice. A letter names one datum; "
                    "two features establishing one datum is a compound datum and is "
                    "written differently."
                )
            seen.add(datum.letter)

    @property
    def letters(self) -> frozenset[str]:
        return frozenset(datum.letter for datum in self.datums)

    def get(self, letter: str) -> Datum | None:
        cleaned = str(letter).strip().upper()
        return next((d for d in self.datums if d.letter == cleaned), None)

    def to_dict(self) -> dict[str, Any]:
        return {"datums": [datum.to_dict() for datum in self.datums]}


@dataclass(frozen=True)
class FeatureControlFrame:
    """One geometric tolerance on one feature.

    Everything checkable without knowing the part's datum scheme is checked here,
    at construction. Whether the letters it references exist is checked by
    `Tolerancing`, which is the only object that knows both halves.
    """

    feature: str
    characteristic: Characteristic
    tolerance_mm: float
    datums: tuple[DatumReference, ...] = ()
    condition: MaterialCondition = MaterialCondition.RFS
    diametral: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "datums", tuple(self.datums))
        where = f"{self.feature or '<unnamed feature>'} {self.characteristic}"
        if not self.feature or not self.feature.strip():
            raise GdtError(
                "A feature control frame needs the feature it applies to; it is what an "
                "inspection plan and a failure are written against."
            )
        grammar = GRAMMAR[self.characteristic]

        if not math.isfinite(self.tolerance_mm) or self.tolerance_mm < 0:
            raise GdtError(
                f"{where}: a tolerance zone of {self.tolerance_mm} is not a zone. The "
                "value is the width of the zone in millimetres and is zero or more."
            )
        if self.tolerance_mm == 0 and self.condition is MaterialCondition.RFS:
            raise GdtError(
                f"{where}: a zero tolerance means something only at MMC or LMC, where the "
                "zone grows with the feature's departure from that boundary. Regardless "
                "of feature size, it demands perfect geometry and no part can be made to "
                "it. Add the material condition, or give the zone a width."
            )

        if self.condition not in grammar.conditions:
            allowed = ", ".join(sorted(str(c) for c in grammar.conditions))
            raise GdtError(
                f"{where}: {self.condition} is not a material condition this "
                f"characteristic can take — {grammar.reason}. Allowed here: {allowed}."
            )
        if self.diametral and not grammar.diametral_zone:
            raise GdtError(
                f"{where}: a diametral zone is not available on this characteristic — "
                f"{grammar.reason}."
            )

        if grammar.datums is DatumRule.FORBIDDEN and self.datums:
            listed = ", ".join(ref.letter for ref in self.datums)
            raise GdtError(
                f"{where}: a {grammar.category} control takes no datum reference, and "
                f"this one names {listed}. Form is a claim about a feature against "
                "itself. If the intent was to relate it to {listed}, the characteristic "
                "meant is an orientation or a location one."
            )
        if grammar.datums is DatumRule.REQUIRED and not self.datums:
            raise GdtError(
                f"{where}: a {grammar.category} control is a relationship and needs the "
                "datum it is measured to. Add at least a primary datum reference."
            )
        if len(self.datums) > MAXIMUM_DATUM_REFERENCES:
            raise GdtError(
                f"{where}: {len(self.datums)} datum references, and a datum reference "
                f"frame holds at most {MAXIMUM_DATUM_REFERENCES} — primary, secondary, "
                "tertiary. A fourth has no precedence to sit in."
            )
        seen: set[str] = set()
        for ref in self.datums:
            if ref.letter in seen:
                raise GdtError(
                    f"{where}: datum {ref.letter} is referenced twice in one frame. Each "
                    "reference takes a place in the precedence, and one datum cannot be "
                    "both primary and secondary."
                )
            seen.add(ref.letter)
            if ref.condition is not MaterialCondition.RFS and (
                ref.condition not in grammar.conditions
            ):
                raise GdtError(
                    f"{where}: datum {ref.letter} is referenced at {ref.condition}, a "
                    f"material boundary this characteristic cannot use — {grammar.reason}."
                )

    @property
    def grammar(self) -> Grammar:
        return GRAMMAR[self.characteristic]

    @property
    def category(self) -> Category:
        return self.grammar.category

    def __str__(self) -> str:
        zone = f"{'Ø' if self.diametral else ''}{self.tolerance_mm:g}"
        parts = [self.grammar.symbol, zone + _CONDITION_SYMBOL[self.condition]]
        parts.extend(str(ref) for ref in self.datums)
        return f"{self.feature}: [{' | '.join(parts)}]"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "feature": self.feature,
            "characteristic": str(self.characteristic),
            "category": str(self.category),
            "tolerance_mm": self.tolerance_mm,
            "diametral": self.diametral,
            "condition": str(self.condition),
            "datums": [ref.to_dict() for ref in self.datums],
        }
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class Tolerancing:
    """Every frame on one part, against the datum scheme they reference.

    Constructing one is the check: a frame naming a datum nothing establishes is
    refused here, because this is the first place both halves are known.
    """

    scheme: DatumScheme = field(default_factory=DatumScheme)
    frames: tuple[FeatureControlFrame, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "frames", tuple(self.frames))
        declared = self.scheme.letters
        for frame in self.frames:
            for ref in frame.datums:
                if ref.letter in declared:
                    continue
                known = ", ".join(sorted(declared)) if declared else "none are declared"
                raise GdtError(
                    f"{frame.feature} {frame.characteristic}: references datum "
                    f"{ref.letter}, which no feature on this part establishes "
                    f"({known}). Declare the datum feature — a datum letter nothing "
                    "sets up to cannot be inspected and cannot be manufactured to."
                )

    @property
    def unreferenced_datums(self) -> tuple[Datum, ...]:
        """Datums declared and never used.

        Reported, never refused: a datum scheme is often written before the frames
        that use it, and refusing the intermediate state would make the document
        unwritable in the order people write it.
        """
        used = {ref.letter for frame in self.frames for ref in frame.datums}
        return tuple(datum for datum in self.scheme.datums if datum.letter not in used)

    def frames_for(self, feature: str) -> tuple[FeatureControlFrame, ...]:
        return tuple(frame for frame in self.frames if frame.feature == feature)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.scheme.to_dict(),
            "frames": [frame.to_dict() for frame in self.frames],
            "unreferenced_datums": [d.letter for d in self.unreferenced_datums],
        }


def datum_scheme(datums: Iterable[Datum]) -> DatumScheme:
    """`DatumScheme` from any iterable, for a caller building one up in a loop."""
    return DatumScheme(tuple(datums))


__all__ = [
    "GRAMMAR",
    "MAXIMUM_DATUM_REFERENCES",
    "RESERVED_DATUM_LETTERS",
    "Category",
    "Characteristic",
    "Datum",
    "DatumReference",
    "DatumRule",
    "DatumScheme",
    "FeatureControlFrame",
    "Grammar",
    "MaterialCondition",
    "Tolerancing",
    "datum_scheme",
]
