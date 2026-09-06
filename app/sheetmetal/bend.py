"""Bend allowance, setback and deduction. The arithmetic, and the conventions it needs.

Phase 17.3, and the part of it that is exact. Three formulae, all closed form,
all verified in `tests/test_sheetmetal_bend.py` against arithmetic done by hand
rather than against recorded output:

    BA = (pi/180) * theta * (r + K*t)          bend allowance
    SB = tan(theta/2) * (r + t)                setback, to the outside mould line
    BD = 2*SB - BA                             bend deduction

`theta` is the angle the material **turns through**, not the included angle
between the legs. A 90 degree bend turns through 90 degrees and leaves its legs
at 90 degrees to each other; a 135 degree bend leaves them at 45. The two
conventions differ by `180 - theta` and a part built on the wrong one is wrong
by a large amount, which is why the argument is `angle_deg` on a `Bend` whose
docstring says which and why nothing in this package accepts an included angle.

**Setback depends on which mould line you dimension from, and the choice is not
a preference.** `SB = tan(theta/2)*(r+t)` is the distance from the bend tangent
to the intersection of the two **outside** faces. Dimension from the inside
faces instead and the offset is `r`, not `r+t`, and the flat length moves by
`2*t*tan(theta/2)` per bend — 4 mm on a four-bend 2 mm part. So
`LengthConvention` is a required field on a part, with no default:
`app/manufacture/sheet.py` may default its projection because the choice is
stamped on every sheet it produces, and a flange dimension carries no such
stamp.

**All three conventions are one formula.** The offset from the tangent line is
`r + t` (outside), `r` (inside), or `0` (tangent-to-tangent, where the declared
length *is* the flat length of the leg). `setback_mm` takes the convention and
`bend_deduction_mm` is `2*SB - BA` in every case, so the identity

    flat length = sum(declared lengths) - sum(bend deductions)

holds for all three. In the tangent convention SB is zero and the deduction is
`-BA`, which is the same statement as "add the bend allowance between the legs".
`unfold.py` relies on this identity and `tests/test_sheetmetal_unfold.py` pins
it both ways round.

**A 180 degree bend has no mould line.** `tan(90 deg)` is infinite: the outside
faces of a hem are parallel and never meet, so there is no apex to dimension to.
The bend allowance is perfectly well defined there and is returned; the setback
and the deduction are **refused**, with the message telling the caller to
dimension that flange tangent-to-tangent. Returning a huge float instead would
put a plausible-looking blank length on a drawing.

**Bend direction does not enter the arithmetic.** Up and down produce the same
allowance, the same setback and the same flat pattern; the direction decides
which way the leg goes when it is folded and how the bend line is marked for
the operator. It is carried on `Bend` for the flat pattern and for the shop, and
`bend_allowance_mm` never reads it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.sheetmetal.errors import BendError
from app.sheetmetal.kfactor import DEGREE, KFactor

#: Bends at or above this angle have no outside mould line — the faces are
#: parallel or folded back on themselves. Named rather than written as 180.0 in
#: three places.
STRAIGHT_ANGLE_DEG: Final = 180.0


class BendDirection(StrEnum):
    """Which way the material turns, seen from the face the bend is measured on.

    `UP` turns toward the side the face's outward normal points; `DOWN` away
    from it. It changes nothing in `bend_allowance_mm`, `setback_mm` or
    `bend_deduction_mm` — those depend on the angle, the radius, the thickness
    and K, and on nothing else. It is on the bend because the flat pattern has
    to tell the operator which way to fold, and a blank with unmarked bend
    directions is a blank that gets folded into a mirror image.
    """

    UP = "up"
    DOWN = "down"


class LengthConvention(StrEnum):
    """Where a flange's declared length is measured from and to.

    There is deliberately **no default**. All three are in daily use, they
    differ by `t*tan(theta/2)` per bend end, and unlike a drawing's projection
    convention (`app/manufacture/sheet.py`, which may default because the
    choice is printed on the sheet) a flange dimension arrives with nothing
    attached to say which was meant.
    """

    #: To the intersection of the outside faces — the apex a caliper reaches
    #: across the outside of the corner. The commonest way a folded part is
    #: dimensioned, and the one the plan's `SB = tan(theta/2)*(r+t)` describes.
    OUTSIDE_MOULD_LINE = "outside mould line"

    #: To the intersection of the inside faces. Offset `r` rather than `r+t`.
    INSIDE_MOULD_LINE = "inside mould line"

    #: The flat portion of the leg, tangent line to tangent line. No setback at
    #: all: the declared length is already what gets cut.
    TANGENT = "tangent"


def _mould_line_offset_mm(
    convention: LengthConvention, *, inside_radius_mm: float, thickness_mm: float
) -> float:
    """How far the mould line sits from the bend centre, radially.

    The single place the three conventions differ. Everything else in this
    module is convention-free.
    """
    if convention is LengthConvention.OUTSIDE_MOULD_LINE:
        return inside_radius_mm + thickness_mm
    if convention is LengthConvention.INSIDE_MOULD_LINE:
        return inside_radius_mm
    return 0.0


def _check_geometry(*, angle_deg: float, inside_radius_mm: float, thickness_mm: float) -> None:
    if not math.isfinite(angle_deg) or angle_deg <= 0.0:
        raise BendError(
            f"A bend angle of {angle_deg!r} degrees is not a bend. The angle is the one "
            f"the material turns through, so it is greater than zero; a flat joint "
            f"between two flanges is one flange."
        )
    if angle_deg > STRAIGHT_ANGLE_DEG:
        raise BendError(
            f"A bend angle of {angle_deg:g} degrees turns the material back past itself. "
            f"The angle is the turn, not the included angle between the legs — if the "
            f"legs sit at {angle_deg:g} degrees to each other, the bend is "
            f"{STRAIGHT_ANGLE_DEG - angle_deg:g} degrees."
        )
    if not math.isfinite(inside_radius_mm) or inside_radius_mm <= 0.0:
        raise BendError(
            f"An inside bend radius of {inside_radius_mm!r} mm is not a bend. Even a "
            f"sharp corner has the radius the tooling actually leaves; state it."
        )
    if not math.isfinite(thickness_mm) or thickness_mm <= 0.0:
        raise BendError(
            f"A sheet thickness of {thickness_mm!r} mm is not a sheet. Bend allowance "
            f"needs the material thickness in millimetres."
        )


def bend_allowance_mm(
    *, angle_deg: float, inside_radius_mm: float, thickness_mm: float, k: float
) -> float:
    """`BA = (pi/180) * theta * (r + K*t)` — the arc length of the neutral axis.

    The length of material consumed by the bend, and therefore the gap between
    the two tangent lines on the flat blank.
    """
    _check_geometry(
        angle_deg=angle_deg, inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm
    )
    if not math.isfinite(k) or not 0.0 < k <= 0.5:
        raise BendError(
            f"K = {k!r} is not a position for the neutral axis; it must lie in (0, 0.5]. "
            f"Build a KFactor, which refuses this at construction and carries its source."
        )
    return DEGREE * angle_deg * (inside_radius_mm + k * thickness_mm)


def setback_mm(
    *,
    angle_deg: float,
    inside_radius_mm: float,
    thickness_mm: float,
    convention: LengthConvention = LengthConvention.OUTSIDE_MOULD_LINE,
) -> float:
    """`SB = tan(theta/2) * offset` — tangent line to mould line, along the leg.

    `offset` is `r+t` for the outside mould line (the plan's formula), `r` for
    the inside one, and zero tangent-to-tangent.

    Refused at 180 degrees: the outside faces of a hem are parallel and their
    intersection does not exist. `tan(90 deg)` in floating point is 1.6e16
    rather than an error, so without this the caller gets a blank length of
    tens of millions of millimetres and no exception.
    """
    _check_geometry(
        angle_deg=angle_deg, inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm
    )
    offset = _mould_line_offset_mm(
        convention, inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm
    )
    if offset == 0.0:
        return 0.0
    if angle_deg >= STRAIGHT_ANGLE_DEG:
        raise BendError(
            f"A {angle_deg:g} degree bend has no {convention}: the two faces are parallel "
            f"and never meet, so there is no apex to dimension to. Declare the flanges "
            f"either side of it with LengthConvention.TANGENT, which needs no setback."
        )
    return math.tan(math.radians(angle_deg) / 2.0) * offset


def bend_deduction_mm(
    *,
    angle_deg: float,
    inside_radius_mm: float,
    thickness_mm: float,
    k: float,
    convention: LengthConvention = LengthConvention.OUTSIDE_MOULD_LINE,
) -> float:
    """`BD = 2*SB - BA` — how much shorter the blank is than the mould-line lengths.

    Subtract one of these per bend from the sum of the declared flange lengths
    and the result is the flat length. In the tangent convention SB is zero, so
    the deduction is `-BA` and the subtraction becomes the addition every
    tangent-to-tangent calculation does by hand.
    """
    allowance = bend_allowance_mm(
        angle_deg=angle_deg, inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm, k=k
    )
    setback = setback_mm(
        angle_deg=angle_deg,
        inside_radius_mm=inside_radius_mm,
        thickness_mm=thickness_mm,
        convention=convention,
    )
    return 2.0 * setback - allowance


@dataclass(frozen=True)
class Bend:
    """One bend: how far it turns, how tight, which way, and on what K.

    The thickness is **not** here. A sheet-metal part has one thickness by
    definition (`material.py`), and putting it on the bend is how two bends in
    one part come to disagree about it. Every method therefore takes the
    thickness, and the free functions above take primitives so a test can check
    the arithmetic without building anything.
    """

    #: Degrees the material turns through. 90 for a right-angle corner.
    angle_deg: float
    inside_radius_mm: float
    direction: BendDirection
    k: KFactor
    name: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.angle_deg) or not 0.0 < self.angle_deg <= STRAIGHT_ANGLE_DEG:
            raise BendError(
                f"A bend angle of {self.angle_deg!r} degrees is not a bend the material "
                f"can turn through. It must be greater than 0 and at most "
                f"{STRAIGHT_ANGLE_DEG:g}; the angle is the turn, not the included angle "
                f"between the legs."
            )
        if not math.isfinite(self.inside_radius_mm) or self.inside_radius_mm <= 0.0:
            raise BendError(
                f"An inside bend radius of {self.inside_radius_mm!r} mm is not a bend. "
                f"State the radius the tooling leaves, however small."
            )

    @property
    def label(self) -> str:
        """What this bend is called on a flat pattern. Never empty."""
        return self.name or f"bend {self.angle_deg:g} deg R{self.inside_radius_mm:g}"

    def r_over_t(self, thickness_mm: float) -> float:
        return self.inside_radius_mm / thickness_mm

    def allowance_mm(self, thickness_mm: float) -> float:
        return bend_allowance_mm(
            angle_deg=self.angle_deg,
            inside_radius_mm=self.inside_radius_mm,
            thickness_mm=thickness_mm,
            k=self.k.value,
        )

    def setback_mm(self, thickness_mm: float, convention: LengthConvention) -> float:
        return setback_mm(
            angle_deg=self.angle_deg,
            inside_radius_mm=self.inside_radius_mm,
            thickness_mm=thickness_mm,
            convention=convention,
        )

    def deduction_mm(self, thickness_mm: float, convention: LengthConvention) -> float:
        return bend_deduction_mm(
            angle_deg=self.angle_deg,
            inside_radius_mm=self.inside_radius_mm,
            thickness_mm=thickness_mm,
            k=self.k.value,
            convention=convention,
        )

    def to_dict(self, thickness_mm: float | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.label,
            "angle_deg": self.angle_deg,
            "inside_radius_mm": self.inside_radius_mm,
            "direction": str(self.direction),
            "k": self.k.to_dict(),
        }
        if thickness_mm is not None:
            out["r_over_t"] = self.r_over_t(thickness_mm)
            out["bend_allowance_mm"] = self.allowance_mm(thickness_mm)
        return out


__all__ = [
    "STRAIGHT_ANGLE_DEG",
    "Bend",
    "BendDirection",
    "LengthConvention",
    "bend_allowance_mm",
    "bend_deduction_mm",
    "setback_mm",
]
