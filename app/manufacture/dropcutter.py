"""Drop-cutter and waterline over a triangulated part -- master plan E17 task 4.

The two primitives every 3-axis toolpath is made of:

* **drop-cutter**: a cutter held vertical over a point (x, y) is lowered until it first
  touches the part; the answer is the height of its tip there (the *cutter-location* z);
* **waterline**: at a fixed height, the closed loops where the cutter's tip can sit and only
  just touch the part.

**Plan change, recorded 2026-09-15.** The plan names OpenCAMLib for both. It has no wheel this
project's Python (3.12) can install (`pip` finds no matching distribution), so it cannot be
federated the way pyLife is. Drop-cutter is geometry, not physics, so Decision 2 does not
forbid writing it, and it is short enough to check exhaustively: `tests/test_manufacture_cam.py`
holds it to a brute-force oracle that samples each triangle densely and applies only the vertex
test, so a drop below any real surface point (a gouge) fails it.

**Drop-cutter is exact** for a flat end mill and a ball end mill. Against one triangle the
highest contact is one of three kinds and all three are closed forms:

* *vertex*: a vertex within the cutter's radius of the axis;
* *edge*: for a flat cutter, the highest point of the edge's part inside the cutter's circle,
  which lies at a clip end because the edge is straight; for a ball, the sphere centre height
  at which the edge is exactly one radius from it, a quadratic;
* *facet*: for a flat cutter, the point on the rim uphill of the axis, if it is inside the
  triangle; for a ball, the tangent point `q − r·n_xy`, if it is inside.

**The waterline is sampled, and says so.** A grid of drop-cutter queries at the step the
caller gives marks where the cutter at the waterline height would gouge; marching squares
joins the boundary into loops. **Each loop point is bisected along its grid line** to the
tolerance given, so every point is on the true waterline; what the step decides is how
finely the loop is traced between points, and a feature narrower than a step can be missed.
`Waterline.step_mm` travels with the answer.

A CL point is a cutter-tip height, not a G-code block. Nothing here post-processes for a
machine, and nothing should without that machine's post-processor.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.manufacture.errors import ManufactureError


class CamError(ManufactureError):
    """A toolpath question that cannot be answered as asked."""


class CutterShape(StrEnum):
    FLAT = "flat"
    BALL = "ball"


@dataclass(frozen=True)
class Cutter:
    shape: CutterShape
    diameter_mm: float
    #: Flute length, carried for the plan; the drop does not model the shank.
    length_mm: float
    source: str = ""

    def __post_init__(self) -> None:
        if not (self.diameter_mm > 0.0 and math.isfinite(self.diameter_mm)):
            raise CamError(f"A cutter {self.diameter_mm} mm across cuts nothing.")
        if self.length_mm <= 0.0:
            raise CamError(f"A cutter needs a positive flute length; got {self.length_mm}.")

    @property
    def radius_mm(self) -> float:
        return self.diameter_mm / 2.0


_EPS = 1e-12


def as_triangles(triangles: Any) -> NDArray[np.float64]:
    array = np.asarray(triangles, dtype=float)
    if array.ndim != 3 or array.shape[1:] != (3, 3) or len(array) == 0:
        raise CamError(
            f"Triangles are an (n, 3, 3) array of vertex coordinates in mm; got shape "
            f"{array.shape}."
        )
    if not np.all(np.isfinite(array)):
        raise CamError("A triangle has a non-finite coordinate.")
    return array


def _inside_xy(p: tuple[float, float], a: NDArray[np.float64], b: NDArray[np.float64], c: NDArray[np.float64]) -> bool:
    def cross(o: NDArray[np.float64], u: NDArray[np.float64]) -> float:
        return float((u[0] - o[0]) * (p[1] - o[1]) - (u[1] - o[1]) * (p[0] - o[0]))

    d1, d2, d3 = cross(a, b), cross(b, c), cross(c, a)
    negative = d1 < -1e-12 or d2 < -1e-12 or d3 < -1e-12
    positive = d1 > 1e-12 or d2 > 1e-12 or d3 > 1e-12
    return not (negative and positive)


def _flat_triangle(x: float, y: float, r: float, tri: NDArray[np.float64]) -> float | None:
    best: float | None = None

    def keep(z: float) -> None:
        nonlocal best
        if best is None or z > best:
            best = z

    for v in tri:
        if (v[0] - x) ** 2 + (v[1] - y) ** 2 <= r * r + 1e-12:
            keep(float(v[2]))
    for i in range(3):
        p0, p1 = tri[i], tri[(i + 1) % 3]
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        a = dx * dx + dy * dy
        if a <= _EPS:
            continue
        fx, fy = p0[0] - x, p0[1] - y
        b = 2.0 * (fx * dx + fy * dy)
        c = fx * fx + fy * fy - r * r
        disc = b * b - 4.0 * a * c
        if disc < 0.0:
            continue
        root = math.sqrt(disc)
        t0, t1 = (-b - root) / (2.0 * a), (-b + root) / (2.0 * a)
        lo, hi = max(t0, 0.0), min(t1, 1.0)
        if lo > hi:
            continue
        for t in (lo, hi):
            keep(float(p0[2] + t * (p1[2] - p0[2])))
    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    length = float(np.linalg.norm(normal))
    if length > _EPS:
        n = normal / length
        if abs(n[2]) > 1e-9:
            gx, gy = -n[0] / n[2], -n[1] / n[2]
            g = math.hypot(gx, gy)
            if g <= 1e-12:
                px, py = x, y
            else:
                px, py = x + r * gx / g, y + r * gy / g
            if _inside_xy((px, py), tri[0], tri[1], tri[2]):
                keep(float(tri[0][2] + gx * (px - tri[0][0]) + gy * (py - tri[0][1])))
    return best


def _ball_triangle(x: float, y: float, r: float, tri: NDArray[np.float64]) -> float | None:
    """The highest sphere-centre z touching the triangle, or None."""
    best: float | None = None

    def keep(z: float) -> None:
        nonlocal best
        if best is None or z > best:
            best = z

    for v in tri:
        d2 = (v[0] - x) ** 2 + (v[1] - y) ** 2
        if d2 <= r * r:
            keep(float(v[2] + math.sqrt(r * r - d2)))
    for i in range(3):
        p0, p1 = tri[i], tri[(i + 1) % 3]
        edge = p1 - p0
        length = float(np.linalg.norm(edge))
        if length <= _EPS:
            continue
        u = edge / length
        w = np.array([x - p0[0], y - p0[1], 0.0])
        wu = float(w @ u)
        a = 1.0 - u[2] * u[2]
        if a <= 1e-12:
            continue
        b = -2.0 * u[2] * wu
        c = float(w @ w) - wu * wu - r * r
        disc = b * b - 4.0 * a * c
        if disc < 0.0:
            continue
        s = (-b + math.sqrt(disc)) / (2.0 * a)
        t = wu + s * u[2]
        if 0.0 <= t <= length:
            keep(float(p0[2] + s))
    normal = np.cross(tri[1] - tri[0], tri[2] - tri[0])
    size = float(np.linalg.norm(normal))
    if size > _EPS:
        n = normal / size
        if n[2] < 0.0:
            n = -n
        if n[2] > 1e-9:
            px, py = x - r * n[0], y - r * n[1]
            if _inside_xy((px, py), tri[0], tri[1], tri[2]):
                plane_at_q = tri[0][2] - (n[0] * (x - tri[0][0]) + n[1] * (y - tri[0][1])) / n[2]
                keep(float(plane_at_q + r / n[2]))
    return best


def drop(
    triangles: Any, cutter: Cutter, x_mm: float, y_mm: float, *, floor_z_mm: float
) -> float:
    """The cutter-tip height at (x, y): the highest of every contact and the floor."""
    tris = as_triangles(triangles)
    r = cutter.radius_mm
    lo = np.min(tris[:, :, :2], axis=1)
    hi = np.max(tris[:, :, :2], axis=1)
    near = np.nonzero(
        (lo[:, 0] <= x_mm + r) & (hi[:, 0] >= x_mm - r) & (lo[:, 1] <= y_mm + r) & (hi[:, 1] >= y_mm - r)
    )[0]
    best = floor_z_mm
    for index in near:
        tri = tris[index]
        if cutter.shape is CutterShape.FLAT:
            z = _flat_triangle(x_mm, y_mm, r, tri)
        else:
            centre = _ball_triangle(x_mm, y_mm, r, tri)
            z = None if centre is None else centre - r
        if z is not None and z > best:
            best = z
    return best


def drop_many(
    triangles: Any, cutter: Cutter, points_xy_mm: Sequence[tuple[float, float]], *, floor_z_mm: float
) -> NDArray[np.float64]:
    tris = as_triangles(triangles)
    return np.array([drop(tris, cutter, x, y, floor_z_mm=floor_z_mm) for x, y in points_xy_mm])


@dataclass(frozen=True)
class Waterline:
    z_mm: float
    cutter: Cutter
    step_mm: float
    tolerance_mm: float
    loops: tuple[tuple[tuple[float, float], ...], ...]
    open_paths: tuple[tuple[tuple[float, float], ...], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "z_mm": self.z_mm,
            "cutter": {"shape": self.cutter.shape.value, "diameter_mm": self.cutter.diameter_mm},
            "step_mm": self.step_mm,
            "tolerance_mm": self.tolerance_mm,
            "loops": [[list(p) for p in loop] for loop in self.loops],
            "open_paths": [[list(p) for p in path] for path in self.open_paths],
            "basis": (
                "sampled: every point bisected onto the waterline to the tolerance; the loop is "
                "traced between points at the grid step, and a feature narrower than a step can "
                "be missed"
            ),
        }


def waterline(
    triangles: Any,
    cutter: Cutter,
    z_mm: float,
    *,
    step_mm: float,
    tolerance_mm: float = 1e-6,
) -> Waterline:
    """Loops at height `z_mm` where the cutter tip just touches the part."""
    tris = as_triangles(triangles)
    if not step_mm > 0.0:
        raise CamError(f"A waterline needs a positive grid step; got {step_mm}.")
    if not tolerance_mm > 0.0:
        raise CamError(f"A waterline needs a positive tolerance; got {tolerance_mm}.")
    r = cutter.radius_mm
    lo = tris[:, :, :2].reshape(-1, 2).min(axis=0) - r - step_mm
    hi = tris[:, :, :2].reshape(-1, 2).max(axis=0) + r + step_mm
    nx = int(math.ceil((hi[0] - lo[0]) / step_mm)) + 1
    ny = int(math.ceil((hi[1] - lo[1]) / step_mm)) + 1
    if nx * ny > 4_000_000:
        raise CamError(
            f"A {nx} x {ny} grid is too many drop-cutter queries. Increase step_mm."
        )
    xs = lo[0] + step_mm * np.arange(nx)
    ys = lo[1] + step_mm * np.arange(ny)
    # A tiny slack so a face lying exactly at the waterline height does not flicker.
    slack = 1e-9 * max(1.0, abs(z_mm))

    def gouges(x: float, y: float) -> bool:
        return drop(tris, cutter, x, y, floor_z_mm=-math.inf) > z_mm + slack

    state = np.array([[gouges(float(x), float(y)) for y in ys] for x in xs])
    if state[0, :].any() or state[-1, :].any() or state[:, 0].any() or state[:, -1].any():
        raise CamError("The waterline grid does not enclose the part; this is a defect here.")

    crossings: dict[tuple[str, int, int], tuple[float, float]] = {}

    def crossing(key: tuple[str, int, int]) -> tuple[float, float]:
        if key in crossings:
            return crossings[key]
        kind, i, j = key
        a = (float(xs[i]), float(ys[j]))
        b = (float(xs[i + 1]), float(ys[j])) if kind == "h" else (float(xs[i]), float(ys[j + 1]))
        inside_a = bool(state[i, j])
        for _ in range(200):
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            if gouges(*mid) == inside_a:
                a = mid
            else:
                b = mid
            if math.hypot(b[0] - a[0], b[1] - a[1]) <= tolerance_mm:
                break
        point = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        crossings[key] = point
        return point

    segments: list[tuple[tuple[str, int, int], tuple[str, int, int]]] = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            bl, br = bool(state[i, j]), bool(state[i + 1, j])
            tl, tr = bool(state[i, j + 1]), bool(state[i + 1, j + 1])
            bottom, top = ("h", i, j), ("h", i, j + 1)
            left, right = ("v", i, j), ("v", i + 1, j)
            crossed = [
                e
                for e, differs in ((bottom, bl != br), (right, br != tr), (top, tl != tr), (left, bl != tl))
                if differs
            ]
            if len(crossed) == 2:
                segments.append((crossed[0], crossed[1]))
            elif len(crossed) == 4:
                centre = gouges(float(xs[i] + step_mm / 2.0), float(ys[j] + step_mm / 2.0))
                if bl and tr:
                    pairs = [(bottom, right), (top, left)] if centre else [(bottom, left), (right, top)]
                else:
                    pairs = [(bottom, left), (right, top)] if centre else [(bottom, right), (top, left)]
                segments.extend(pairs)

    touching: dict[tuple[str, int, int], list[int]] = {}
    for index, (a_key, b_key) in enumerate(segments):
        touching.setdefault(a_key, []).append(index)
        touching.setdefault(b_key, []).append(index)

    used = [False] * len(segments)
    loops: list[tuple[tuple[float, float], ...]] = []
    open_paths: list[tuple[tuple[float, float], ...]] = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        first, current = segments[start]
        keys = [first, current]
        while True:
            following = [s for s in touching[current] if not used[s]]
            if not following:
                break
            nxt = following[0]
            used[nxt] = True
            a_key, b_key = segments[nxt]
            current = b_key if a_key == current else a_key
            if current == first:
                break
            keys.append(current)
        points = tuple(crossing(k) for k in keys)
        (loops if current == first else open_paths).append(points)

    return Waterline(
        z_mm=z_mm,
        cutter=cutter,
        step_mm=step_mm,
        tolerance_mm=tolerance_mm,
        loops=tuple(loops),
        open_paths=tuple(open_paths),
    )


__all__ = [
    "CamError",
    "Cutter",
    "CutterShape",
    "Waterline",
    "as_triangles",
    "drop",
    "drop_many",
    "waterline",
]
