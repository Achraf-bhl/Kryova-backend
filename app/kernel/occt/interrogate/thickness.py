"""How thin the part gets — master plan 3.2's wall-thickness scan.

**The method is ray casting, and its limitation is structural.** From points spread over
every face, a ray is fired *into* the material along the inward normal; the distance to
the first thing it meets on the way out is the wall thickness at that point. The minimum
over all such rays is the reported minimum wall.

That is an **upper bound on the true minimum**, never the true minimum. A wall that thins
to nothing between two sample points is not found, and no finite grid removes that. This
is why `ThicknessReport` is always `APPROXIMATED` and why it names its sample count: a
reviewer needs to judge whether the scan was dense enough for the claim being made on it,
and cannot do that from a bare number.

**Why not OCCT's `BRepOffsetAPI_MakeThickSolid` or a medial axis?** An offset-based
thickness measure answers a different question — how far the surface can be moved before
it self-intersects — and fails outright on the geometry where thickness matters most
(narrow ribs, internal corners). A true medial-axis transform is the correct answer and
is a research project in its own right. Ray casting is the method every commercial
thickness checker actually ships, for the same reasons.

**The rays are the cost of this package.** One curve/surface intersection against the
whole shape, per sample, per face. A 400-face part at the default 4×4 grid is 6,400
casts. That is affordable and is not free, which is why nothing calls this speculatively —
it runs when a design asserts on wall thickness.
"""

from __future__ import annotations

from typing import Any

from app.kernel.interrogation import ThicknessReport, ThicknessSample
from app.kernel.occt.binding import require
from app.kernel.occt.interrogate.raycast import first_hit_distance, opposite
from app.kernel.occt.interrogate.sampling import DEFAULT_GRID, sample_face
from app.kernel.occt.topology import faces


def scan_thickness(shape: Any, *, grid: int = DEFAULT_GRID) -> ThicknessReport:
    """Cast rays inward from every face and report the thinnest wall found.

    A ray that leaves without hitting anything is counted as a miss rather than as a
    zero-thickness wall. Misses are normal on an open shell and near an opening, and
    their count is what tells a reader whether the scan saw the part or saw through it.
    """
    require()

    samples: list[ThicknessSample] = []
    misses = 0

    for index, face in enumerate(faces(shape)):
        for surface_point in sample_face(face, grid=grid):
            inward = opposite(surface_point.normal)
            distance = first_hit_distance(shape, surface_point.point, inward)
            if distance is None:
                misses += 1
                continue
            samples.append(
                ThicknessSample(
                    point=surface_point.point,
                    thickness_mm=distance,
                    face_index=index,
                )
            )

    return ThicknessReport(
        samples=tuple(samples),
        misses=misses,
        samples_per_face=grid * grid,
    )


__all__ = ["scan_thickness"]
