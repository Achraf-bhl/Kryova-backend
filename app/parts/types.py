"""What a bought-in part is, here: a designation, geometry, and engineering data.

Phase 12.3. Seventy per cent of a real machine is bought, and Kryova's job with
a bought-in part is never to model it — it is to *place* it and to *check* it.
Those need different things:

* a **designation** a purchasing system understands — `M8x40 ISO 4014 8.8`;
* enough **geometry** to place it and to cut the hole it goes through;
* the **engineering data** that makes it checkable — proof load, clamp length
  range, tightening torque.

The third is the one that decides whether the part is usable. A bolt whose mass
is known but whose proof load is not cannot be checked, so a record that lacks
one **says which field is missing and why**, and `require` refuses rather than
returning a plausible number. This is the same rule the material library
applies, and it uses the same `Property` record — a bolt's proof load and an
alloy's yield strength are the same kind of claim, and giving them two
provenance models would mean two that drift.

Units are the codebase's mm-N-MPa: dimensions mm, loads N, areas mm², **torque
N·mm** (matching `MomentLoad.moment_n_mm`), mass kg. A datasheet quoting 25 N·m
becomes 25000 N·mm at `transcribe`, which is the only conversion boundary.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.solve.materials import Property, Source, register_quantity

# --- the parts vocabulary ---------------------------------------------------
#
# Registered rather than declared locally so that `Property` keeps one registry:
# one place answers "what unit is a proof load in?", and a second module cannot
# quietly answer it differently.

for _name, _unit, _meaning in [
    ("nominal_diameter_mm", "mm", "Nominal thread or shaft diameter"),
    ("thread_pitch_mm", "mm", "Thread pitch, coarse unless the designation says otherwise"),
    ("pitch_diameter_mm", "mm", "Thread pitch (flank) diameter, d2"),
    ("minor_diameter_mm", "mm", "Thread minor (root) diameter, d3"),
    ("length_mm", "mm", "Length under the head, excluding the head"),
    ("thread_length_mm", "mm", "Threaded length measured from the point, b"),
    ("width_across_flats_mm", "mm", "Hexagon width across flats, s"),
    ("width_across_corners_mm", "mm", "Hexagon width across corners"),
    ("head_height_mm", "mm", "Head height, k"),
    ("head_bearing_diameter_mm", "mm", "Diameter of the head's bearing face, dw"),
    ("nut_height_mm", "mm", "Nut height, m"),
    ("inner_diameter_mm", "mm", "Inside diameter of an annular part"),
    ("outer_diameter_mm", "mm", "Outside diameter of an annular part"),
    ("thickness_mm", "mm", "Thickness of a flat part"),
    ("clearance_hole_mm", "mm", "Diameter of the through hole the part passes through"),
    ("tensile_stress_area_mm2", "mm2", "Thread tensile stress area, As"),
    ("proof_load_n", "N", "Axial load the fastener takes with no permanent set"),
    ("min_breaking_load_n", "N", "Minimum axial load at which the fastener fails"),
    ("assembly_preload_n", "N", "Bolt tension aimed for at assembly"),
    ("tightening_torque_n_mm", "N.mm", "Torque applied at the head or nut to reach preload"),
    ("min_clamp_length_mm", "mm", "Shortest joint stack this fastener can clamp"),
    ("max_clamp_length_mm", "mm", "Longest joint stack this fastener can clamp"),
    ("mass_kg", "kg", "Mass of one piece"),
]:
    register_quantity(_name, _unit, _meaning)
del _name, _unit, _meaning


class PartKind(StrEnum):
    """What sort of bought-in part this is. Decides what data it ought to carry."""

    BOLT = "bolt"
    NUT = "nut"
    WASHER = "washer"


#: What a part of each kind must carry to be usable for the check its kind
#: exists for. A washer is not expected to carry a proof load — ISO 7089
#: specifies dimensions and hardness and nothing about load — so demanding one
#: would report a gap that is not a gap, and gaps nobody believes get ignored.
EXPECTED_ENGINEERING: Mapping[PartKind, tuple[str, ...]] = {
    PartKind.BOLT: (
        "tensile_stress_area_mm2",
        "proof_load_n",
        "min_breaking_load_n",
        "min_clamp_length_mm",
        "max_clamp_length_mm",
        "mass_kg",
    ),
    PartKind.NUT: ("proof_load_n", "nut_height_mm", "mass_kg"),
    PartKind.WASHER: ("thickness_mm", "mass_kg"),
}


class MissingEngineeringData(LookupError):
    """A part was asked for data it does not carry. Names the field."""


class UnknownPart(LookupError):
    """A designation that is not in the catalogue. Never substituted."""


class DesignationError(ValueError):
    """A designation that could not be read."""


_DESIGNATION_RE = re.compile(
    r"""^\s*
    (?P<size>M\d+(?:\.\d+)?)                 # M8, M2.5
    (?:\s*[x×X]\s*(?P<length>\d+(?:\.\d+)?))?  # x40, optional (nuts and washers)
    \s+(?P<standard>(?:ISO|DIN|EN|ANSI|ASME)\s*[\w.\-]+)
    (?:\s+(?P<grade>[\w.\-]+))?              # 8.8, 10.9, 8, 200HV
    \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True)
class Designation:
    """The name a part is ordered by — `M8x40 ISO 4014 8.8`.

    Kept structured rather than as a string because every field is asked about
    separately: the size decides the hole, the length decides the clamp range,
    the grade decides the proof load, and the standard decides the geometry.
    Round-trips through `str()` and `parse()`.
    """

    standard: str
    size: str
    length_mm: float | None = None
    grade: str = ""

    def __post_init__(self) -> None:
        if not self.standard.strip() or not self.size.strip():
            raise DesignationError(
                "A designation needs a standard and a size, e.g. "
                "Designation('ISO 4014', 'M8', 40, '8.8')."
            )
        if self.length_mm is not None and self.length_mm <= 0:
            raise DesignationError(
                f"A length of {self.length_mm} mm is not a length. Leave it out for a "
                f"part that has no length (a nut, a washer), or give a positive one."
            )

    def __str__(self) -> str:
        size = self.size if self.length_mm is None else f"{self.size}x{self.length_mm:g}"
        grade = f" {self.grade}" if self.grade else ""
        return f"{size} {self.standard}{grade}"

    @property
    def key(self) -> str:
        """The catalogue key: lower case, no spaces. `iso4014-m8x40-8.8`."""
        parts = [self.standard.replace(" ", "").lower(), self.size.lower()]
        if self.length_mm is not None:
            parts[1] = f"{parts[1]}x{self.length_mm:g}"
        if self.grade:
            parts.append(self.grade.lower())
        return "-".join(parts)

    @classmethod
    def parse(cls, text: str) -> "Designation":
        """Read a designation an engineer typed. Refuses rather than guessing."""
        match = _DESIGNATION_RE.match(text)
        if match is None:
            raise DesignationError(
                f"{text!r} is not a designation this reads. The shape is "
                f"'<size>[x<length>] <standard> [<grade>]', for example "
                f"'M8x40 ISO 4014 8.8', 'M8 ISO 4032 8' or 'M8 ISO 7089 200HV'."
            )
        length = match.group("length")
        standard = re.sub(r"\s+", " ", match.group("standard").upper()).strip()
        if " " not in standard:  # "ISO4014" -> "ISO 4014"
            standard = re.sub(r"^(ISO|DIN|EN|ANSI|ASME)", r"\1 ", standard).strip()
        return cls(
            standard=standard,
            size=match.group("size").upper(),
            length_mm=float(length) if length is not None else None,
            grade=(match.group("grade") or "").strip(),
        )


@dataclass(frozen=True)
class StandardPart:
    """One catalogue item: what it is called, its geometry, and its data.

    `geometry` and `engineering` are separate because they answer different
    questions and fail differently. Geometry comes straight off the standard and
    is essentially always complete; engineering data is the half that is
    routinely missing, and `absent` is where a record says so in words.
    """

    designation: Designation
    kind: PartKind
    display_name: str
    #: The standard the geometry was taken from.
    source: Source
    geometry: Mapping[str, Property] = field(default_factory=dict)
    engineering: Mapping[str, Property] = field(default_factory=dict)
    #: quantity name -> why this record does not carry it. The difference
    #: between "we have not got round to it" and "the standard does not specify
    #: it" is the whole value of the field, and it is what `require` quotes.
    absent: Mapping[str, str] = field(default_factory=dict)
    #: Material of the part itself, as a note rather than a `MATERIALS` slug:
    #: fastener steel is specified by property class (ISO 898-1), not by grade,
    #: and pretending 8.8 is `steel-1018` would put the wrong yield strength in
    #: front of somebody.
    material_note: str = ""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for group in (self.geometry, self.engineering):
            for key, prop in group.items():
                if key != prop.name:
                    raise ValueError(
                        f"{self.designation}: property filed under {key!r} but named "
                        f"{prop.name!r}. The key and the name must agree, or a lookup "
                        f"returns the wrong quantity."
                    )
        overlap = set(self.geometry) & set(self.engineering)
        if overlap:
            raise ValueError(
                f"{self.designation}: {sorted(overlap)} appear in both geometry and "
                f"engineering data. One quantity, one home — two copies drift."
            )
        clash = (set(self.geometry) | set(self.engineering)) & set(self.absent)
        if clash:
            raise ValueError(
                f"{self.designation}: {sorted(clash)} are recorded as absent and also "
                f"present. A record cannot both hold a value and explain why it does not."
            )

    @property
    def key(self) -> str:
        return self.designation.key

    def get(self, name: str) -> Property | None:
        """The quantity, or None when this part does not carry it. Never zero."""
        return self.geometry.get(name) or self.engineering.get(name)

    def value(self, name: str) -> float | None:
        prop = self.get(name)
        return None if prop is None else prop.value

    def require(self, name: str) -> Property:
        """The quantity, or a refusal naming it, why it is absent, and what is held."""
        prop = self.get(name)
        if prop is not None:
            return prop
        reason = self.absent.get(name)
        because = (
            f" {reason}"
            if reason
            else " No source for it is held here, so it is absent rather than zero."
        )
        have = ", ".join(sorted({*self.geometry, *self.engineering})) or "nothing"
        raise MissingEngineeringData(
            f"{self.designation} does not carry {name}.{because} It cannot be checked "
            f"against that quantity until the value is supplied from the supplier's "
            f"catalogue or the governing standard. This record has: {have}."
        )

    def missing_engineering(self) -> tuple[str, ...]:
        """Which of the data its kind needs to be checkable this part lacks.

        Empty means the part can be checked. Non-empty is not a defect in the
        record — it is the record being honest — but it does mean any check
        involving those quantities must refuse rather than run.
        """
        expected = EXPECTED_ENGINEERING.get(self.kind, ())
        return tuple(name for name in expected if self.get(name) is None)

    def geometry_mm(self) -> dict[str, float]:
        """Nominal dimensions as plain numbers, for whatever builds the solid.

        Nominal, not toleranced: these are the basic sizes from the standard, so
        the placed solid represents the part's envelope rather than any actual
        piece. A clash check against a nominal fastener is a nominal clash check.
        """
        return {
            name: prop.value for name, prop in self.geometry.items() if prop.unit == "mm"
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "designation": str(self.designation),
            "key": self.key,
            "kind": str(self.kind),
            "display_name": self.display_name,
            "standard": self.designation.standard,
            "source": self.source.to_dict(),
            "geometry": {n: p.to_dict() for n, p in sorted(self.geometry.items())},
            "engineering": {n: p.to_dict() for n, p in sorted(self.engineering.items())},
            "absent": dict(sorted(self.absent.items())),
            "missing_engineering": list(self.missing_engineering()),
            "material_note": self.material_note,
            "notes": list(self.notes),
        }


def indexed(parts: Iterable[StandardPart]) -> dict[str, StandardPart]:
    """Key a sequence of parts, refusing a duplicate rather than overwriting."""
    out: dict[str, StandardPart] = {}
    for part in parts:
        if part.key in out:
            raise ValueError(
                f"Two parts share the key {part.key!r} ({out[part.key].display_name} and "
                f"{part.display_name}). A catalogue key must identify one item; give the "
                f"designations something that distinguishes them."
            )
        out[part.key] = part
    return out
