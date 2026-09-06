"""Rigid-body arithmetic in mm and radians. No engine, no numpy, no state.

Deliberately plain tuples rather than numpy arrays. A mechanism sweep is a few hundred
poses over a few dozen bodies — thousands of three-element operations, not millions — so
the array machinery would buy nothing and cost the package a dependency it can be tested
without. Everything here is a pure function of its arguments, which is what lets the
kinematics above it be checked against a closed form to 1e-12 rather than to a tolerance
somebody chose.

**Rotations are 3x3 matrices, row-major, as a flat 9-tuple.** Not quaternions: the
recursion in `kinematics.py` needs `R·v` and `R_a·R_b` and nothing else, matrices do both
without a conversion, and a matrix that has drifted off orthonormal is visible to
`is_rotation` where a drifted quaternion is not.

**Angles are radians everywhere in this package, with no exceptions.** An angular rate
that feeds `ω × (ω × r)` must be in radians or the answer is wrong by 57.3², and a
codebase that spells one quantity two ways eventually multiplies by the wrong one.
Degrees appear only in reporting, suffixed `_deg`, converted at the point of printing.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]

#: Row-major 3x3, flattened: (m00, m01, m02, m10, m11, m12, m20, m21, m22).
Rot3 = tuple[float, float, float, float, float, float, float, float, float]

IDENTITY: Rot3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
ZERO: Vec3 = (0.0, 0.0, 0.0)


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, k: float) -> Vec3:
    return (a[0] * k, a[1] * k, a[2] * k)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3) -> Vec3:
    """`a` normalised.

    Raises on a zero vector rather than returning one. A silently un-normalised axis
    makes a revolute joint rotate by `|a|·q` instead of `q`, which looks like a
    plausible mechanism moving at the wrong speed — the hardest kind of wrong to see.
    """
    length = norm(a)
    if length <= 0.0:
        raise ValueError(
            "A direction of zero length has no direction. Give the axis a non-zero "
            "vector, e.g. (0, 0, 1) for a pin about z."
        )
    return (a[0] / length, a[1] / length, a[2] / length)


def apply(r: Rot3, v: Vec3) -> Vec3:
    return (
        r[0] * v[0] + r[1] * v[1] + r[2] * v[2],
        r[3] * v[0] + r[4] * v[1] + r[5] * v[2],
        r[6] * v[0] + r[7] * v[1] + r[8] * v[2],
    )


def compose(a: Rot3, b: Rot3) -> Rot3:
    """`a · b` — apply b first, then a."""
    return (
        a[0] * b[0] + a[1] * b[3] + a[2] * b[6],
        a[0] * b[1] + a[1] * b[4] + a[2] * b[7],
        a[0] * b[2] + a[1] * b[5] + a[2] * b[8],
        a[3] * b[0] + a[4] * b[3] + a[5] * b[6],
        a[3] * b[1] + a[4] * b[4] + a[5] * b[7],
        a[3] * b[2] + a[4] * b[5] + a[5] * b[8],
        a[6] * b[0] + a[7] * b[3] + a[8] * b[6],
        a[6] * b[1] + a[7] * b[4] + a[8] * b[7],
        a[6] * b[2] + a[7] * b[5] + a[8] * b[8],
    )


def transpose(r: Rot3) -> Rot3:
    return (r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8])


def from_axis_angle(axis: Vec3, angle_rad: float) -> Rot3:
    """Rodrigues' formula. `axis` need not be normalised; it is normalised here."""
    x, y, z = unit(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    t = 1.0 - c
    return (
        t * x * x + c,
        t * x * y - s * z,
        t * x * z + s * y,
        t * x * y + s * z,
        t * y * y + c,
        t * y * z - s * x,
        t * x * z - s * y,
        t * y * z + s * x,
        t * z * z + c,
    )


def is_rotation(r: Rot3, tolerance: float = 1e-9) -> bool:
    """Whether `r` is still orthonormal with determinant +1.

    Used by the tests rather than by the recursion: composing a few hundred rotations
    drifts, and this says by how much before anybody has to argue about it.
    """
    rt_r = compose(transpose(r), r)
    if any(abs(rt_r[i] - IDENTITY[i]) > tolerance for i in range(9)):
        return False
    det = (
        r[0] * (r[4] * r[8] - r[5] * r[7])
        - r[1] * (r[3] * r[8] - r[5] * r[6])
        + r[2] * (r[3] * r[7] - r[4] * r[6])
    )
    return abs(det - 1.0) <= tolerance


class Frame:
    """Where a body is: a rotation and a translation, in mm.

    A plain class rather than a dataclass so the two fields can be positional and
    cheap — this is allocated once per body per sample.
    """

    __slots__ = ("rotation", "origin_mm")

    def __init__(self, rotation: Rot3 = IDENTITY, origin_mm: Vec3 = ZERO) -> None:
        self.rotation = rotation
        self.origin_mm = origin_mm

    def point(self, local_mm: Vec3) -> Vec3:
        """A point given in this frame, expressed in world coordinates."""
        return add(self.origin_mm, apply(self.rotation, local_mm))

    def direction(self, local: Vec3) -> Vec3:
        """A direction given in this frame, expressed in world coordinates."""
        return apply(self.rotation, local)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Frame):
            return NotImplemented
        return self.rotation == other.rotation and self.origin_mm == other.origin_mm

    def __repr__(self) -> str:
        x, y, z = self.origin_mm
        return f"Frame(origin_mm=({x:.6g}, {y:.6g}, {z:.6g}))"


__all__ = [
    "IDENTITY",
    "ZERO",
    "Frame",
    "Rot3",
    "Vec3",
    "add",
    "apply",
    "compose",
    "cross",
    "dot",
    "from_axis_angle",
    "is_rotation",
    "norm",
    "scale",
    "sub",
    "transpose",
    "unit",
]
