"""Where every face and every bend of a folded part sits in space.

Phase 17.3 task 3, the arithmetic half. `unfold.py` lays the same part out flat;
this lays it out **folded** — one frame per flange and one cylindrical sector per
bend, in millimetres, in the part's own coordinates. `app.kernel.occt.sheetmetal`
turns the result into a solid; nothing here imports a kernel, so the placement is
testable in milliseconds and the package keeps the offline property its docstring
claims.

**The two layouts are one calculation seen twice.** Both consume
`unfold.tangent_extents`, so a leg is the same length flat and folded by
construction rather than by agreement, and both consume `Bend.allowance_mm` for
what the bend costs. That is the whole point of the module: `app/design/missions.py`
publishes `flat.volume_mismatch_mm3` — the solid's measured volume against the
volume the blank accounts for — as the one number tying the drawing to the part,
and a residual is only evidence if the two sides were not computed from the same
line of code twice.

**The model, in one paragraph.** A flange is a rectangular plate: its frame's
origin sits at the corner of its tangent rectangle, `u` runs along the flange away
from the bend that carries it, `v` runs along that bend, and the normal `n = u x v`
points through the material — so the plate occupies `u in [0, L]`, `v in [0, W]`,
`n in [0, t]`. A bend is a cylindrical sector between two tangent lines: the
material turns through `theta` about an axis parallel to the shared edge, from the
inside radius `r` to `r + t`. `BendDirection.UP` turns the child toward `+n` and
`DOWN` toward `-n`, which is the only place the direction enters any arithmetic in
this package (`bend.py` says so about the flat pattern, and it stays true: the
allowance, the setback and the deduction never read it).

**Where the bend centre goes, and why it differs by direction.** Bending up puts
the concave side on the `+n` face, so the centre is `r` beyond it: `t + r` from the
frame plane. Bending down puts the concave side on the frame plane itself, so the
centre is `r` *below* it. Get that the wrong way round and the part still builds,
still has the right volume, and has an outside dimension wrong by `2t` per bend.

**What the volume is, exactly.** Plates are `L*W*t`; a sector of angle `theta`
between `r` and `r+t` over a width `w` is `theta*t*(r + t/2)*w`. So the folded
volume is a closed form (`folded_volume_mm3`), which is what lets the kernel's
measured volume be checked against arithmetic rather than against recorded output.
It differs from the blank's `area * t` by exactly `sum(theta * t^2 * w * (0.5 - K))`
— zero when `K = 0.5`, and the sign says the obvious thing: a neutral axis inside
the mid-plane means the blank is shorter than the folded material would suggest.
`tests/test_sheetmetal_fold.py` pins both statements.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from app.sheetmetal.bend import Bend, BendDirection
from app.sheetmetal.errors import UnfoldError
from app.sheetmetal.unfold import (
    Edge,
    Joint,
    SheetMetalPart,
    hole_on_face,
    tangent_extents,
)

#: A point or a direction in part coordinates, in millimetres.
Vector3 = tuple[float, float, float]

#: How far off unit length a computed axis may drift before it is a defect rather
#: than rounding. The frames are built from cosines and sines of one angle, so a
#: real failure here is an algebra error, not accumulated error.
_UNIT_TOLERANCE: Final = 1e-9


def _add(a: Vector3, b: Vector3) -> Vector3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vector3, factor: float) -> Vector3:
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _negate(a: Vector3) -> Vector3:
    return (-a[0], -a[1], -a[2])


def _cross(a: Vector3, b: Vector3) -> Vector3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vector3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


@dataclass(frozen=True)
class FoldedFace:
    """One flat face of the folded part, as a placed rectangular plate.

    The plate occupies `u in [0, length_mm]`, `v in [0, width_mm]` and
    `n in [0, thickness_mm]` from `origin_mm`, where `n` is `u x v`. Lengths are
    tangent lengths — what is left of the flange between its bends — so a face
    here is exactly the face `unfold` draws on the blank, and never the
    mould-line rectangle the flange was declared with.
    """

    name: str
    origin_mm: Vector3
    u_dir: Vector3
    v_dir: Vector3
    length_mm: float
    width_mm: float
    thickness_mm: float

    @property
    def normal(self) -> Vector3:
        """Through the material, from the frame plane to the far surface."""
        return _cross(self.u_dir, self.v_dir)

    @property
    def volume_mm3(self) -> float:
        return self.length_mm * self.width_mm * self.thickness_mm

    def at(self, u_mm: float, v_mm: float, n_mm: float = 0.0) -> Vector3:
        """A point in the face's own coordinates, in part coordinates."""
        return _add(
            self.origin_mm,
            _add(
                _scale(self.u_dir, u_mm),
                _add(_scale(self.v_dir, v_mm), _scale(self.normal, n_mm)),
            ),
        )


@dataclass(frozen=True)
class FoldedBend:
    """The cylindrical sector between two faces, placed in part coordinates.

    `centre_mm` is a point on the bend axis at the start of the swept width;
    `axis_dir` is the axis, oriented so the sector runs from `start_dir` toward
    the child through a **positive** rotation about it by `angle_deg`.
    `start_dir` is the radial direction from the axis to the *inside* surface at
    the parent's tangent line, so `centre + start_dir * inside_radius_mm` is a
    point the parent and the bend share.

    `width_dir` is which way the material runs along the bend, and it is **not**
    `axis_dir`: an up bend turns about `-v` while its material still runs along
    `+v`, so a sector swept along the rotation axis lands on the far side of the
    part. It builds, it measures the right volume, and the part is mirrored — the
    reason this is a separate field rather than a sign somebody remembers.
    """

    name: str
    between: tuple[str, str]
    centre_mm: Vector3
    axis_dir: Vector3
    width_dir: Vector3
    start_dir: Vector3
    angle_deg: float
    inside_radius_mm: float
    thickness_mm: float
    width_mm: float
    direction: BendDirection

    #: `BA` for this bend, from `Bend.allowance_mm` — the length of blank the bend
    #: consumes. Carried so the folded and flat accounts of the same material can
    #: be differenced without re-walking the part and re-reading K.
    allowance_mm: float = 0.0

    @property
    def volume_mm3(self) -> float:
        """`theta * t * (r + t/2) * w` — the sector between `r` and `r + t`."""
        outer = self.inside_radius_mm + self.thickness_mm
        return (
            math.radians(self.angle_deg)
            / 2.0
            * (outer * outer - self.inside_radius_mm * self.inside_radius_mm)
            * self.width_mm
        )

    @property
    def blank_volume_mm3(self) -> float:
        """What the flat pattern accounts for over this bend: `BA * w * t`."""
        return self.allowance_mm * self.width_mm * self.thickness_mm


@dataclass(frozen=True)
class FoldedHole:
    """A declared hole, placed on the folded part: where it is and which way it goes.

    `centre_mm` is on the face's frame plane; the hole runs from there along
    `axis_dir` through the thickness. Carried because a folded solid that quietly
    ignored the holes on its own faces would weigh more than the part, and every
    mass, clash and packaging claim downstream reads that weight.
    """

    name: str
    face: str
    centre_mm: Vector3
    axis_dir: Vector3
    diameter_mm: float
    thickness_mm: float

    @property
    def volume_mm3(self) -> float:
        radius = self.diameter_mm / 2.0
        return math.pi * radius * radius * self.thickness_mm


@dataclass(frozen=True)
class FoldedLayout:
    """Every face and every bend of one part, placed, plus what it adds up to."""

    part_name: str
    thickness_mm: float
    faces: tuple[FoldedFace, ...]
    bends: tuple[FoldedBend, ...]
    holes: tuple[FoldedHole, ...] = ()

    @property
    def volume_mm3(self) -> float:
        """The exact volume of the folded solid, from arithmetic, not measurement.

        Holes are subtracted: they are cut through a flat face and nothing else,
        which `unfold.hole_on_face` is what guarantees — a hole in a bend zone is
        refused rather than modelled, so the cylinder never meets a curved
        surface and its volume is exactly `pi r^2 t`.
        """
        return (
            sum(face.volume_mm3 for face in self.faces)
            + sum(bend.volume_mm3 for bend in self.bends)
            - sum(hole.volume_mm3 for hole in self.holes)
        )

    def face_named(self, name: str) -> FoldedFace:
        for face in self.faces:
            if face.name == name:
                return face
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "part": self.part_name,
            "thickness_mm": self.thickness_mm,
            "volume_mm3": self.volume_mm3,
            "faces": [
                {
                    "name": face.name,
                    "origin_mm": list(face.origin_mm),
                    "u_dir": list(face.u_dir),
                    "v_dir": list(face.v_dir),
                    "normal": list(face.normal),
                    "length_mm": face.length_mm,
                    "width_mm": face.width_mm,
                }
                for face in self.faces
            ],
            "bends": [
                {
                    "name": bend.name,
                    "between": list(bend.between),
                    "centre_mm": list(bend.centre_mm),
                    "axis_dir": list(bend.axis_dir),
                    "width_dir": list(bend.width_dir),
                    "angle_deg": bend.angle_deg,
                    "inside_radius_mm": bend.inside_radius_mm,
                    "width_mm": bend.width_mm,
                    "direction": str(bend.direction),
                }
                for bend in self.bends
            ],
            "holes": [
                {
                    "name": hole.name,
                    "face": hole.face,
                    "centre_mm": list(hole.centre_mm),
                    "axis_dir": list(hole.axis_dir),
                    "diameter_mm": hole.diameter_mm,
                }
                for hole in self.holes
            ],
        }


def _hinge(
    parent: FoldedFace, joint: Joint, *, child_width_mm: float
) -> tuple[Vector3, Vector3, Vector3]:
    """Where a child hangs off its parent: the corner, the way out, the bend axis.

    Returned as `(point, out_dir, axis_dir)` in part coordinates. `point` is the
    parent's tangent line at the child's `v = 0`, `out_dir` is the in-plane
    direction the child extends before it is bent, and `axis_dir` is the child's
    own `v` axis — the bend runs along it.

    The four cases are `unfold._child_frame`'s four cases with the bend allowance
    taken out: flat, the child starts one allowance further along `out_dir`;
    folded, it starts one *arc* further round. Both read the same corner, and
    that is deliberate — the two layouts disagreeing about which corner a flange
    hangs off would be invisible in a picture and obvious only in a volume.
    """
    u = parent.u_dir
    v = parent.v_dir
    offset = joint.offset_mm
    if joint.edge is Edge.FAR:
        point = parent.at(parent.length_mm, offset)
        return point, u, v
    if joint.edge is Edge.NEAR:
        point = parent.at(0.0, offset + child_width_mm)
        return point, _negate(u), _negate(v)
    if joint.edge is Edge.LEFT:
        point = parent.at(offset, 0.0)
        return point, _negate(v), u
    point = parent.at(offset + child_width_mm, parent.width_mm)
    return point, v, _negate(u)


@dataclass(frozen=True)
class _Bent:
    """The child's frame after a bend, and where the sector that made it sits."""

    origin: Vector3
    u_dir: Vector3
    v_dir: Vector3
    centre: Vector3
    axis: Vector3
    start_dir: Vector3


def _bent_child(
    *,
    hinge_mm: Vector3,
    out_dir: Vector3,
    axis_dir: Vector3,
    normal: Vector3,
    bend: Bend,
    thickness_mm: float,
) -> _Bent:
    """The child's frame after the bend, and the axis the sector turns about.

    The rotation is written out rather than assembled from a matrix because there
    are only two cases and each one is two lines: about `-axis` for an up bend,
    about `+axis` for a down bend.
    """
    theta = math.radians(bend.angle_deg)
    cos = math.cos(theta)
    sin = math.sin(theta)
    radius = bend.inside_radius_mm
    outer = radius + thickness_mm

    if bend.direction is BendDirection.UP:
        # Concave side is the `+n` face, so the axis sits `t + r` above the frame
        # plane and the child's frame plane rides the *outer* surface round.
        centre = _add(hinge_mm, _scale(normal, thickness_mm + radius))
        start_dir = _negate(normal)
        radial = _add(_scale(normal, -cos), _scale(out_dir, sin))
        origin = _add(centre, _scale(radial, outer))
        u_dir = _add(_scale(out_dir, cos), _scale(normal, sin))
        axis = _negate(axis_dir)
    else:
        # Concave side is the frame plane, so the axis is `r` below it and the
        # child's frame plane rides the *inner* surface round.
        centre = _add(hinge_mm, _scale(normal, -radius))
        start_dir = normal
        radial = _add(_scale(normal, cos), _scale(out_dir, sin))
        origin = _add(centre, _scale(radial, radius))
        u_dir = _add(_scale(out_dir, cos), _scale(normal, -sin))
        axis = axis_dir

    if abs(_length(u_dir) - 1.0) > _UNIT_TOLERANCE:
        raise UnfoldError(  # pragma: no cover - an algebra error, not an input error
            f"Bending {bend.label!r} produced a face axis of length {_length(u_dir):.12f} "
            "instead of 1. That is a defect in this module's rotation arithmetic, not "
            "something a part can cause."
        )
    return _Bent(
        origin=origin,
        u_dir=u_dir,
        v_dir=axis_dir,
        centre=centre,
        axis=axis,
        start_dir=start_dir,
    )


def fold_layout(part: SheetMetalPart) -> FoldedLayout:
    """Place every face and every bend of a folded part, in part coordinates.

    The root flange's frame is the part's: origin at the world origin, `u` along
    `+x`, `v` along `+y`, so the sheet lies in the `z = 0` plane and its material
    fills `z in [0, t]`. Every other face follows from a bend.

    Raises `UnfoldError` for the parts `unfold` refuses for reasons that belong to
    the part rather than to the blank — a leg its bends have eaten, a flange
    hanging off the end of the edge it is bent from — because those refuse a solid
    too. It does **not** refuse a part whose *blank* would overlap itself: that is
    a property of the flat pattern, the folded part is perfectly buildable, and
    refusing it here would be the over-refusal `app/catia/` warns about, where the
    agent's recovery is to try something else and build the wrong thing.
    """
    thickness = part.material.thickness_mm
    extents = tangent_extents(part)
    faces: dict[str, FoldedFace] = {}
    ordered: list[FoldedFace] = []
    bends: list[FoldedBend] = []
    holes: list[FoldedHole] = []

    for flange, parent_flange, joint in part.walk():
        entry = extents[flange.name]
        if parent_flange is None or joint is None:
            origin: Vector3 = (0.0, 0.0, 0.0)
            u_dir: Vector3 = (1.0, 0.0, 0.0)
            v_dir: Vector3 = (0.0, 1.0, 0.0)
        else:
            parent_face = faces[parent_flange.name]
            hinge, out_dir, axis_dir = _hinge(
                parent_face, joint, child_width_mm=entry.tangent_width_mm
            )
            bent = _bent_child(
                hinge_mm=hinge,
                out_dir=out_dir,
                axis_dir=axis_dir,
                normal=parent_face.normal,
                bend=joint.bend,
                thickness_mm=thickness,
            )
            origin, u_dir, v_dir = bent.origin, bent.u_dir, bent.v_dir
            bends.append(
                FoldedBend(
                    name=joint.bend.label,
                    between=(parent_flange.name, flange.name),
                    centre_mm=bent.centre,
                    axis_dir=bent.axis,
                    width_dir=bent.v_dir,
                    start_dir=bent.start_dir,
                    angle_deg=joint.bend.angle_deg,
                    inside_radius_mm=joint.bend.inside_radius_mm,
                    thickness_mm=thickness,
                    width_mm=entry.tangent_width_mm,
                    direction=joint.bend.direction,
                    allowance_mm=joint.bend.allowance_mm(thickness),
                )
            )
        face = FoldedFace(
            name=flange.name,
            origin_mm=origin,
            u_dir=u_dir,
            v_dir=v_dir,
            length_mm=entry.tangent_length_mm,
            width_mm=entry.tangent_width_mm,
            thickness_mm=thickness,
        )
        faces[flange.name] = face
        ordered.append(face)
        for hole in flange.holes:
            u_mm, v_mm = hole_on_face(entry, hole, convention=part.convention)
            holes.append(
                FoldedHole(
                    name=hole.name,
                    face=flange.name,
                    centre_mm=face.at(u_mm, v_mm),
                    axis_dir=face.normal,
                    diameter_mm=hole.diameter_mm,
                    thickness_mm=thickness,
                )
            )

    return FoldedLayout(
        part_name=part.name,
        thickness_mm=thickness,
        faces=tuple(ordered),
        bends=tuple(bends),
        holes=tuple(holes),
    )


def folded_volume_mm3(part: SheetMetalPart) -> float:
    """The exact volume of the folded solid, in mm^3, without building anything.

    The oracle the kernel's measured volume is checked against. It is also the
    honest answer to "what should this weigh" on a machine with no OCCT.
    """
    return fold_layout(part).volume_mm3


def blank_volume_difference_mm3(part: SheetMetalPart) -> float:
    """Folded volume minus `blank area * t`, in closed form: what K costs.

    Each bend contributes `theta * t^2 * w * (0.5 - K)`: the sector holds
    `theta * t * (r + t/2) * w` of material and the blank accounts for
    `BA * w * t = theta * (r + K*t) * w * t`. Zero when every K is 0.5 — a neutral
    axis on the mid-plane conserves the material exactly — and positive below it,
    because the blank is then shorter than the arc of the mid-surface.

    Stated as arithmetic so a test can subtract two independently computed volumes
    and compare the residual against what it *should* be, rather than against a
    tolerance somebody chose. `app/design/missions.py` publishes the measured form
    of the same quantity as `flat.volume_mismatch_mm3`.
    """
    layout = fold_layout(part)
    return sum(bend.volume_mm3 - bend.blank_volume_mm3 for bend in layout.bends)


__all__ = [
    "FoldedBend",
    "FoldedFace",
    "FoldedHole",
    "FoldedLayout",
    "Vector3",
    "blank_volume_difference_mm3",
    "fold_layout",
    "folded_volume_mm3",
]
