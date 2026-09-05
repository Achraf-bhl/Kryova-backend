"""Picking points that are actually on a face.

Every interrogation in this package starts from "a point on this surface, and the normal
there": thickness casts a ray inward from one, undercut casts two rays along the pull
direction, curvature evaluates the principal radii at one. So the sampler is written
once, here, and the analyses differ only in what they do with what it returns.

**A UV grid is not a set of points on the face.** A face is a *trimmed* region of a
surface: the surface of a cylinder with a rectangular window cut in it still reports the
full cylinder's parameter range, and a naive grid over that range puts most of its points
in the hole. Rays from those points measure the wall of a feature that is not there. So
every candidate is classified against the face's own boundary with
`BRepTopAdaptor_FClass2d` and the outside ones are dropped — which is why a sample count
is *requested*, never guaranteed, and why every report in `app.kernel.interrogation`
carries how many samples it actually got.

**The grid is deliberately offset from the boundary.** Sampling at u=umin puts the point
exactly on an edge, where the normal is shared between two faces and the ray leaves along
the seam. Points are taken at cell centres, which keeps them in the interior for any
sensible face.

**Cost.** One classifier is built per face and reused across that face's samples;
building one per point is the obvious way to write this and turns a scan of a 400-face
part into 25,000 classifier constructions.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

from app.kernel.occt.binding import symbol
from app.kernel.occt.classify import face_normal_at

#: Tolerance handed to the 2D face classifier. OCCT's own default for this class.
_CLASSIFIER_TOLERANCE: Final = 1e-6

#: Default grid resolution per face axis. 4×4 = 16 candidate points, of which a trimmed
#: face keeps fewer. Chosen as the smallest grid that puts a point in each quadrant of a
#: face, which is what catches a wall that thins towards one corner.
DEFAULT_GRID: Final = 4

#: How much finer the retry is when a face's grid lands entirely outside its trim. Four
#: means a rim one sixteenth the width of its face still gets sampled. Applied once, only
#: to faces that produced nothing.
_REFINEMENT_FACTOR: Final = 4


@dataclass(frozen=True)
class SurfacePoint:
    """A point on a face, with the outward normal and parameters that produced it."""

    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    u: float
    v: float


def sample_face(
    face: Any, grid: int = DEFAULT_GRID, *, refine_if_empty: bool = True
) -> list[SurfacePoint]:
    """Points spread over a face's interior, with outward normals.

    Returns fewer than `grid²` points whenever the face is trimmed, and never invents one
    — a point at the parametric centre of a face whose centre lies in a hole is worse
    than no point, because it is a measurement that looks taken.

    **`refine_if_empty` exists because of narrow faces, and it is not gold-plating.** The
    rim of a shelled box is a real case: a 2.5 mm-wide annulus on a 40 mm face, where a
    4×4 grid puts its nearest cell centre 5 mm in and lands every one of the sixteen in
    the opening. The face then contributes nothing, and — this is the part that matters —
    it goes on to be reported as *untested* by the undercut scan and absent from the
    thickness scan. So a face that yields nothing is retried once at
    `grid × _REFINEMENT_FACTOR`. The retry is rare by construction (only empty faces
    reach it) and bounded to one attempt, so the cost does not scale with part size the
    way simply raising the default grid would.
    """
    if grid < 1:
        raise ValueError(
            f"A sampling grid needs at least one point per axis, got {grid}. Use "
            f"grid={DEFAULT_GRID} unless a finer scan is genuinely needed — cost is "
            "quadratic in this number."
        )

    found = _sample_at(face, grid)
    if found or not refine_if_empty:
        return found
    return _sample_at(face, grid * _REFINEMENT_FACTOR)


def _sample_at(face: Any, grid: int) -> list[SurfacePoint]:
    umin, umax, vmin, vmax = symbol("BRepTools").UVBounds_s(face)
    if not all(math.isfinite(value) for value in (umin, umax, vmin, vmax)):
        return []

    classifier = symbol("BRepTopAdaptor_FClass2d")(face, _CLASSIFIER_TOLERANCE)
    surface = symbol("BRepAdaptor_Surface")(face)
    point2d = symbol("gp_Pnt2d")
    state_out = symbol("TopAbs_State").TopAbs_OUT

    found: list[SurfacePoint] = []
    for u, v in _cell_centres(umin, umax, vmin, vmax, grid):
        if classifier.Perform(point2d(u, v)) == state_out:
            continue
        normal = face_normal_at(face, u, v)
        if normal is None:
            continue
        position = surface.Value(u, v)
        found.append(
            SurfacePoint(
                point=(position.X(), position.Y(), position.Z()),
                normal=normal,
                u=u,
                v=v,
            )
        )
    return found


def sample_face_centre(face: Any) -> SurfacePoint | None:
    """One point on a face, preferring the parametric centre but never trusting it.

    What the undercut test wants: a single representative point per face, cheaply. The
    centre is tried first and, when it falls outside the trim, the full grid is used and
    its first hit returned — so a C-shaped face still gets a point, instead of being
    reported as untestable because its middle is in the notch.
    """
    umin, umax, vmin, vmax = symbol("BRepTools").UVBounds_s(face)
    if not all(math.isfinite(value) for value in (umin, umax, vmin, vmax)):
        return None

    classifier = symbol("BRepTopAdaptor_FClass2d")(face, _CLASSIFIER_TOLERANCE)
    surface = symbol("BRepAdaptor_Surface")(face)
    point2d = symbol("gp_Pnt2d")
    state_out = symbol("TopAbs_State").TopAbs_OUT

    u = (umin + umax) / 2.0
    v = (vmin + vmax) / 2.0
    if classifier.Perform(point2d(u, v)) != state_out:
        normal = face_normal_at(face, u, v)
        if normal is not None:
            position = surface.Value(u, v)
            return SurfacePoint(
                point=(position.X(), position.Y(), position.Z()),
                normal=normal,
                u=u,
                v=v,
            )

    grid = sample_face(face, grid=DEFAULT_GRID)
    return grid[0] if grid else None


def _cell_centres(
    umin: float, umax: float, vmin: float, vmax: float, grid: int
) -> Iterator[tuple[float, float]]:
    """Centres of a `grid × grid` division of the parameter rectangle.

    Cell centres rather than a boundary-inclusive `linspace`: the boundary of the
    parameter rectangle is where the face's edges are, and a normal evaluated on an edge
    belongs to two faces at once.
    """
    du = (umax - umin) / grid
    dv = (vmax - vmin) / grid
    for i in range(grid):
        u = umin + du * (i + 0.5)
        for j in range(grid):
            yield u, vmin + dv * (j + 0.5)


__all__ = ["DEFAULT_GRID", "SurfacePoint", "sample_face", "sample_face_centre"]
