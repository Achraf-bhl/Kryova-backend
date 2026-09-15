"""A welded frame as something a shop can cut, fit and weld -- master plan E17 task 3.

A frame is already describable as beams: `app.solve.sections.BeamSection` carries a profile
and which way up it is, and M2's portal is solved that way. What a fabricator needs from the
same description is different: **a cut list** (how many of each length, with what end cuts),
**the welds** (where, how big, drawn with what symbol) and **whether each weld is big
enough**. This module derives all three from the members and the welds the caller declares.

**Cut list.** Members are grouped by profile, orientation-independent section dimensions,
length and end cuts. Where exactly two members end at one point, both ends are **mitred**:
the included angle φ between the members gives a cut of (180° − φ)/2 off square, and the
long-point length grows by (d/2)·tan of that angle at the end, with d the section's depth in
the plane of the joint. A section rotated so neither of its axes lies in that plane has no
single depth there, so its mitre length is refused by name rather than approximated. Where
three or more members meet, the ends are listed as **coped, not computed**: a cope is a
fitting decision, and inventing one would put a length on the list nobody derived.

**Welds.** A fillet weld is declared by its **throat** `a`, the size ISO 2553 prefixes with
`a` (the leg is prefixed `z`). For an equal-leg fillet between faces at 90° the leg is
a·√2 and the bead's cross-section is a², both geometry. The label is written in words
beside the dimension (`a5 fillet 200, both sides`), because a glyph typed into a string is
not the symbol a drawing standard draws, and the drawing that carries the real symbol is
`app/manufacture/drawing.py`'s job.

**Weld sizing** (moved here from E13 task 1). The throat-area method: a fillet weld resists
a force per unit length of `f_vw,d × a`, so the throat it needs is `w / f_vw,d`. The design
shear strength `f_vw,d` depends on the parent material, the electrode, the standard and its
national annex, so **it is the caller's, with its source**; nothing here computes one from a
remembered correlation or partial factor. An upper bound on the throat against the thinner
parent plate is likewise the caller's ratio. The check is an E5 assertion set like
`app/rules/joints.py`, recorded approximated: the method is a simplification of the weld's
real stress state.

A weldment is not built as a solid here. That needs the members swept along their lines
and fused, which no test or sweep yet asks the kernel for (Decision 1).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.manufacture.errors import ManufactureError
from app.rules.processes import Limit
from app.solve.sections import (
    BeamSection,
    BoxProfile,
    CircularProfile,
    PipeProfile,
    RectangularProfile,
    normalise_n1,
)

Vec3 = tuple[float, float, float]

#: Two member ends closer than this are one node. A fabrication drawing's own resolution
#: is coarser; this only has to separate points that are meant to coincide from ones that
#: are not.
NODE_TOLERANCE_MM: Final = 1e-3


class WeldmentError(ManufactureError):
    """A weldment that cannot be cut, fitted or sized as described."""


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _unit(a: Vec3) -> Vec3:
    length = _norm(a)
    return (a[0] / length, a[1] / length, a[2] / length)


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


@dataclass(frozen=True)
class Member:
    """One length of stock, from `start_mm` to `end_mm` along its centreline."""

    name: str
    section: BeamSection
    start_mm: Vec3
    end_mm: Vec3

    def __post_init__(self) -> None:
        if not self.name.strip() or "." in self.name:
            raise WeldmentError(
                f"A member needs a name without dots, for its measurement path; got {self.name!r}."
            )
        if self.length_mm <= NODE_TOLERANCE_MM:
            raise WeldmentError(
                f"{self.name}: its start and end are the same point, so it has no length to cut."
            )
        direction = _unit(_sub(self.end_mm, self.start_mm))
        n1 = normalise_n1(self.section.n1)
        if abs(_dot(direction, n1)) > 1e-6:
            raise WeldmentError(
                f"{self.name}: the section's axis 1 {self.section.n1} is not perpendicular to "
                "the member, so the profile's orientation about it is undefined. Give an n1 "
                "square to the member."
            )

    @property
    def length_mm(self) -> float:
        return _norm(_sub(self.end_mm, self.start_mm))

    @property
    def direction(self) -> Vec3:
        return _unit(_sub(self.end_mm, self.start_mm))

    def depth_in_direction_mm(self, across: Vec3) -> float | None:
        """The section's extent along `across` (a unit vector square to the member), or
        None when the section is rotated so neither axis lies along it."""
        profile = self.section.profile
        if isinstance(profile, CircularProfile | PipeProfile):
            return 2.0 * profile.radius_mm
        assert isinstance(profile, RectangularProfile | BoxProfile)
        n1 = normalise_n1(self.section.n1)
        n2 = _cross(self.direction, n1)
        along_1, along_2 = abs(_dot(across, n1)), abs(_dot(across, n2))
        if abs(along_1 - 1.0) <= 1e-6:
            return profile.width_mm
        if abs(along_2 - 1.0) <= 1e-6:
            return profile.height_mm
        return None

    def stock(self) -> str:
        """The profile as a stockist names it, independent of orientation."""
        profile = self.section.profile
        if isinstance(profile, BoxProfile):
            return f"RHS {profile.width_mm:g}x{profile.height_mm:g}x{profile.wall_mm:g}"
        if isinstance(profile, PipeProfile):
            return f"CHS {2 * profile.radius_mm:g}x{profile.wall_mm:g}"
        if isinstance(profile, CircularProfile):
            return f"Round bar {2 * profile.radius_mm:g}"
        assert isinstance(profile, RectangularProfile)
        return f"Flat bar {profile.width_mm:g}x{profile.height_mm:g}"


class EndCut(StrEnum):
    SQUARE = "square"
    MITRE = "mitre"
    COPED = "coped"


@dataclass(frozen=True)
class End:
    cut: EndCut
    #: Degrees off square, for a mitre. Zero for a square cut.
    angle_deg: float = 0.0
    #: How much longer the member's long point is than its centreline at this end, mm.
    #: None where the depth in the joint's plane is undefined, or the end is coped.
    long_point_extension_mm: float | None = 0.0
    note: str = ""

    def label(self) -> str:
        if self.cut is EndCut.MITRE:
            return f"mitre {self.angle_deg:.1f}"
        return self.cut.value


@dataclass(frozen=True)
class CutPiece:
    member: str
    stock: str
    centreline_mm: float
    ends: tuple[End, End]

    @property
    def long_point_mm(self) -> float | None:
        extensions = [end.long_point_extension_mm for end in self.ends]
        if any(e is None for e in extensions):
            return None
        return self.centreline_mm + sum(e for e in extensions if e is not None)


@dataclass(frozen=True)
class CutListLine:
    stock: str
    quantity: int
    centreline_mm: float
    long_point_mm: float | None
    ends: tuple[str, str]
    members: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock": self.stock,
            "quantity": self.quantity,
            "centreline_mm": self.centreline_mm,
            "long_point_mm": self.long_point_mm,
            "ends": list(self.ends),
            "members": list(self.members),
        }


class WeldSide(StrEnum):
    ARROW = "arrow side"
    OTHER = "other side"
    BOTH = "both sides"


@dataclass(frozen=True)
class FilletWeld:
    """An equal-leg fillet between two members, sized by its throat."""

    name: str
    members: tuple[str, str]
    throat_mm: float
    length_mm: float
    side: WeldSide = WeldSide.ARROW
    all_around: bool = False
    #: The design force per unit length the weld carries, N/mm, with its source.
    force_per_length_n_mm: float | None = None
    force_source: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip() or "." in self.name:
            raise WeldmentError(f"A weld needs a name without dots; got {self.name!r}.")
        if self.members[0] == self.members[1]:
            raise WeldmentError(f"{self.name}: a weld joins two different members.")
        if not (self.throat_mm > 0.0 and math.isfinite(self.throat_mm)):
            raise WeldmentError(f"{self.name}: a throat of {self.throat_mm} mm is no weld.")
        if not (self.length_mm > 0.0 and math.isfinite(self.length_mm)):
            raise WeldmentError(f"{self.name}: a weld {self.length_mm} mm long is no weld.")
        if (self.force_per_length_n_mm is None) != (not self.force_source.strip()):
            raise WeldmentError(
                f"{self.name}: a design force per unit length needs its source (the analysis "
                "or hand calculation it came from), and a source needs the force."
            )

    @property
    def runs(self) -> int:
        return 2 if self.side is WeldSide.BOTH else 1

    @property
    def leg_mm(self) -> float:
        """z = a·√2, for an equal-leg fillet between faces at 90 degrees."""
        return self.throat_mm * math.sqrt(2.0)

    @property
    def bead_volume_mm3(self) -> float:
        """Deposited metal: cross-section a² (legs z at 90°, z²/2) times length, per run."""
        return self.throat_mm**2 * self.length_mm * self.runs

    def label(self) -> str:
        text = f"a{self.throat_mm:g} fillet {self.length_mm:g}, {self.side.value}"
        return text + (", all around" if self.all_around else "")


@dataclass(frozen=True)
class WeldStrength:
    """What a weld sizing check needs that only the caller can know."""

    #: f_vw,d in MPa: the fillet's design shear strength for this parent material,
    #: electrode and standard.
    design_shear_strength: Limit
    #: The throat may be at most this fraction of the thinner parent wall. Optional.
    maximum_throat_to_wall: Limit | None = None


def _wall_mm(member: Member) -> float:
    profile = member.section.profile
    if isinstance(profile, BoxProfile | PipeProfile):
        return profile.wall_mm
    if isinstance(profile, RectangularProfile):
        return min(profile.width_mm, profile.height_mm)
    assert isinstance(profile, CircularProfile)
    return 2.0 * profile.radius_mm


@dataclass(frozen=True)
class Weldment:
    name: str
    members: tuple[Member, ...]
    welds: tuple[FilletWeld, ...] = ()
    density_kg_m3: float | None = None
    density_source: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        names = [m.name for m in self.members]
        if not names:
            raise WeldmentError(f"{self.name}: a weldment with no members has nothing to cut.")
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise WeldmentError(f"{self.name}: members named twice: {', '.join(duplicates)}.")
        weld_names = [w.name for w in self.welds]
        if len(set(weld_names)) != len(weld_names):
            raise WeldmentError(f"{self.name}: two welds share a name.")
        known = set(names)
        for weld in self.welds:
            stray = [m for m in weld.members if m not in known]
            if stray:
                raise WeldmentError(
                    f"{weld.name}: joins {', '.join(stray)}, which is not a member of {self.name}."
                )
        if (self.density_kg_m3 is None) != (not self.density_source.strip()):
            raise WeldmentError(
                f"{self.name}: a density needs its source (a material record), and a source "
                "needs the density."
            )

    def member(self, name: str) -> Member:
        return next(m for m in self.members if m.name == name)

    # --- cutting ---------------------------------------------------------------

    def _ends_at(self, point: Vec3) -> list[tuple[Member, int]]:
        found: list[tuple[Member, int]] = []
        for member in self.members:
            for which, end in ((0, member.start_mm), (1, member.end_mm)):
                if _norm(_sub(end, point)) <= NODE_TOLERANCE_MM:
                    found.append((member, which))
        return found

    def _end(self, member: Member, which: int) -> End:
        point = member.start_mm if which == 0 else member.end_mm
        meeting = self._ends_at(point)
        if len(meeting) == 1:
            return End(EndCut.SQUARE)
        if len(meeting) > 2:
            others = ", ".join(m.name for m, _ in meeting if m is not member)
            return End(
                EndCut.COPED,
                long_point_extension_mm=None,
                note=f"meets {others} at one node; the cope is a fitting decision, not computed",
            )
        other, other_which = next((m, w) for m, w in meeting if m is not member)
        away = member.direction if which == 0 else _scale(member.direction, -1.0)
        other_away = other.direction if other_which == 0 else _scale(other.direction, -1.0)
        cosine = max(-1.0, min(1.0, _dot(away, other_away)))
        included = math.degrees(math.acos(cosine))
        mitre = (180.0 - included) / 2.0
        if mitre <= 1e-9:
            return End(EndCut.SQUARE, note=f"butts {other.name} in line")
        normal = _cross(away, other_away)
        across = _unit(_cross(normal, member.direction))
        depth = member.depth_in_direction_mm(across)
        if depth is None:
            return End(
                EndCut.MITRE,
                angle_deg=mitre,
                long_point_extension_mm=None,
                note=(
                    f"mitred to {other.name}, but the section is rotated so neither of its axes "
                    "lies in the joint's plane; the long-point length is not computed"
                ),
            )
        return End(
            EndCut.MITRE,
            angle_deg=mitre,
            long_point_extension_mm=depth / 2.0 * math.tan(math.radians(mitre)),
            note=f"mitred to {other.name}",
        )

    def pieces(self) -> tuple[CutPiece, ...]:
        return tuple(
            CutPiece(
                member=member.name,
                stock=member.stock(),
                centreline_mm=member.length_mm,
                ends=(self._end(member, 0), self._end(member, 1)),
            )
            for member in self.members
        )

    def cut_list(self) -> tuple[CutListLine, ...]:
        """Identical pieces grouped: same stock, length (to 0.01 mm) and end cuts."""
        groups: dict[tuple[Any, ...], list[CutPiece]] = {}
        for piece in self.pieces():
            ends = tuple(sorted(end.label() for end in piece.ends))
            key = (piece.stock, round(piece.centreline_mm, 2), ends)
            groups.setdefault(key, []).append(piece)
        lines = [
            CutListLine(
                stock=stock,
                quantity=len(pieces),
                centreline_mm=pieces[0].centreline_mm,
                long_point_mm=pieces[0].long_point_mm,
                ends=(ends[0], ends[1]),
                members=tuple(p.member for p in pieces),
            )
            for (stock, _, ends), pieces in groups.items()
        ]
        return tuple(sorted(lines, key=lambda line: (line.stock, -line.centreline_mm)))

    def stock_lengths_mm(self) -> dict[str, float]:
        """Total centreline length per stock. Kerf and offcut are not in it."""
        totals: dict[str, float] = {}
        for member in self.members:
            totals[member.stock()] = totals.get(member.stock(), 0.0) + member.length_mm
        return totals

    def mass_kg(self) -> float | None:
        """Members on their centreline lengths plus the deposited weld metal, or None
        with no density. Mitre overlaps and cope removals are not subtracted."""
        if self.density_kg_m3 is None:
            return None
        steel = sum(m.section.mass_kg(m.length_mm, self.density_kg_m3) for m in self.members)
        weld = sum(w.bead_volume_mm3 for w in self.welds) * 1e-9 * self.density_kg_m3
        return steel + weld

    # --- sizing ----------------------------------------------------------------

    def _sizing_payload(self, strength: WeldStrength) -> dict[str, Any]:
        from app.kernel import provenance

        payload: dict[str, Any] = {"weld": {}}
        f_vw = strength.design_shear_strength.value
        for weld in self.welds:
            entry: dict[str, Any] = {"throat_mm": weld.throat_mm}
            thinner = min(_wall_mm(self.member(m)) for m in weld.members)
            entry["throat_to_wall"] = weld.throat_mm / thinner
            provenance.attach(
                payload,
                f"weld.{weld.name}.throat_to_wall",
                provenance.measured("declared throat over the thinner member's wall"),
            )
            if weld.force_per_length_n_mm is not None:
                required = weld.force_per_length_n_mm / f_vw
                entry["required_throat_mm"] = required
                entry["throat_margin_mm"] = weld.throat_mm - required
                provenance.attach(
                    payload,
                    f"weld.{weld.name}.throat_margin_mm",
                    provenance.approximated(
                        f"throat-area method: w / f_vw,d with w from {weld.force_source} and "
                        f"f_vw,d from {strength.design_shear_strength.source}"
                    ),
                )
            payload["weld"][weld.name] = entry
        return payload

    def sizing_assertions(self, strength: WeldStrength) -> tuple[Assertion, ...]:
        out: list[Assertion] = []
        for weld in self.welds:
            base = f"weld.{weld.name}"
            out.append(
                Assertion(
                    name=f"{weld.name}.strength",
                    measure=f"{base}.throat_margin_mm",
                    comparison=">=",
                    bound=0.0,
                    note=f"source: {strength.design_shear_strength.source}",
                )
            )
            if strength.maximum_throat_to_wall is not None:
                out.append(
                    Assertion(
                        name=f"{weld.name}.not_oversized",
                        measure=f"{base}.throat_to_wall",
                        comparison="<=",
                        bound=strength.maximum_throat_to_wall.value,
                        note=f"source: {strength.maximum_throat_to_wall.source}",
                    )
                )
        return tuple(out)

    def check_welds(self, strength: WeldStrength) -> AssertionReport:
        """Each weld's throat against its force, and against its parent wall where a ratio
        is given. A weld with no declared force is unmeasured, never passed."""
        return check_assertions(self.sizing_assertions(strength), self._sizing_payload(strength))

    def to_dict(self) -> dict[str, Any]:
        mass = self.mass_kg()
        return {
            "name": self.name,
            "cut_list": [line.to_dict() for line in self.cut_list()],
            "pieces": [
                {
                    "member": p.member,
                    "stock": p.stock,
                    "centreline_mm": p.centreline_mm,
                    "long_point_mm": p.long_point_mm,
                    "ends": [
                        {
                            "cut": e.cut.value,
                            "angle_deg": e.angle_deg,
                            "long_point_extension_mm": e.long_point_extension_mm,
                            "note": e.note,
                        }
                        for e in p.ends
                    ],
                }
                for p in self.pieces()
            ],
            "welds": [
                {
                    "name": w.name,
                    "members": list(w.members),
                    "label": w.label(),
                    "throat_mm": w.throat_mm,
                    "leg_mm": w.leg_mm,
                    "length_mm": w.length_mm,
                    "bead_volume_mm3": w.bead_volume_mm3,
                }
                for w in self.welds
            ],
            "stock_lengths_mm": self.stock_lengths_mm(),
            "mass_kg": mass,
            "mass_note": (
                f"centreline lengths plus weld metal, density from {self.density_source}; "
                "mitre overlaps and copes are not subtracted"
                if mass is not None
                else "no density given, so no mass"
            ),
            "not_computed": [
                "kerf and offcut allowances",
                "copes where three or more members meet",
                "the weldment as a solid",
            ],
        }


def weldment(
    name: str,
    members: Iterable[Member],
    welds: Sequence[FilletWeld] = (),
    *,
    density_kg_m3: float | None = None,
    density_source: str = "",
) -> Weldment:
    return Weldment(
        name=name,
        members=tuple(members),
        welds=tuple(welds),
        density_kg_m3=density_kg_m3,
        density_source=density_source,
    )


__all__ = [
    "NODE_TOLERANCE_MM",
    "CutListLine",
    "CutPiece",
    "End",
    "EndCut",
    "FilletWeld",
    "Member",
    "WeldSide",
    "WeldStrength",
    "Weldment",
    "WeldmentError",
    "weldment",
]
