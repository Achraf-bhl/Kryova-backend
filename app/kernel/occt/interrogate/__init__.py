"""Asking a built shape the questions a manufacturer would ask.

Master plan Phase 3.2 and 3.3. `metrology.py` next door answers what a part *is* — mass,
volume, bounds, centre of mass — and those are always defined and always exact. This
package answers what a part *can be made into*, and those answers have premises ("pulled
along +Z"), can be "not applicable", and are frequently approximations. Keeping them
apart is what stops an approximation being read as a measurement.

| Question | Module | Basis |
|---|---|---|
| How thin does it get? | `thickness` | approximated — ray cast on a grid |
| Will it leave the tool? | `draft` | measured on planes, approximated on curves |
| Can the tool reach every face? | `undercut` | approximated — one visibility ray pair per face |
| What cutter does it need? | `curvature` | approximated — sampled principal radii |
| Where are the sharp joins? | `continuity` | measured — exact normals at the edge |
| Is the B-rep even well-formed? | `validity` | measured — OCCT's own consistency check |
| Do these two parts clash? | `proximity` | measured — exact extremum search |

Every one returns a report from `app.kernel.interrogation`, which is backend-neutral, so
a CATIA seat answering the same question through `catia_analysis_part` produces the same
shape of result and an assertion cannot tell which measured it.

**Nothing here runs speculatively.** Each scan is worth thousands of kernel calls on a
real part — the ray casts especially — so `measure()` in `metrology.py` does not call
any of it. An interrogation runs when a design asserts on its result.

`sampling` and `raycast` are the shared machinery, exported because they are the seam
where a future accelerated implementation (a BVH, a tessellated proxy) would go in
without any analysis above them changing.
"""

from __future__ import annotations

from app.kernel.occt.interrogate.continuity import scan_continuity
from app.kernel.occt.interrogate.curvature import scan_curvature
from app.kernel.occt.interrogate.draft import analyse_draft
from app.kernel.occt.interrogate.proximity import (
    measure_clearance,
    minimum_distance_mm,
    plane_separation,
)
from app.kernel.occt.interrogate.raycast import (
    escape_point,
    first_hit_distance,
    forward_hit_distances,
    is_blocked,
)
from app.kernel.occt.interrogate.sampling import (
    DEFAULT_GRID,
    SurfacePoint,
    sample_face,
    sample_face_centre,
)
from app.kernel.occt.interrogate.thickness import scan_thickness
from app.kernel.occt.interrogate.undercut import find_undercuts
from app.kernel.occt.interrogate.validity import check_validity

__all__ = [
    "DEFAULT_GRID",
    "SurfacePoint",
    "analyse_draft",
    "check_validity",
    "escape_point",
    "find_undercuts",
    "first_hit_distance",
    "forward_hit_distances",
    "is_blocked",
    "measure_clearance",
    "minimum_distance_mm",
    "plane_separation",
    "sample_face",
    "sample_face_centre",
    "scan_continuity",
    "scan_curvature",
    "scan_thickness",
]
