"""Draft angle against a pull direction — master plan 3.2.

Draft is what lets a moulded or cast part leave its tool. A face parallel to the
direction the mould opens has zero draft: it slides against the tool for its whole
depth, scuffs, and on a deep enough wall refuses to come out at all. Every moulding
house quotes a minimum — typically 0.5° to 3° depending on material and texture — and
the number a design asserts on is the *worst* face, not the average.

**The convention here is `draft = asin(n · pull)`, signed, in degrees.**

| `n · pull` | draft | what it is |
|---|---|---|
| +1 | +90° | a face looking straight up the pull — the top of the part |
| 0 | 0° | a wall parallel to the pull — the problem case |
| −1 | −90° | a face looking straight down — the bottom, pulled by the other half |

So the **sign says which half of the tool** takes the face, and the **magnitude is the
draft angle**. Reporting them as one signed number rather than two fields keeps the
minimum meaningful: `min(|draft|)` is the worst face on the whole part regardless of
which half it belongs to, which is the question being asked.

**A curved face has no single draft.** A cylinder whose axis lies along the pull has
zero draft along two opposite lines and ninety degrees at the poles. Averaging that
describes nothing, so a curved face is sampled and reports its **worst** sample — the
conservative reading, and the one that matches what fails in the tool. Planar faces skip
sampling entirely: one normal is exact, and `DraftReport` uses that distinction to decide
whether its answer is measured or approximated.
"""

from __future__ import annotations

import math
from typing import Any

from app.kernel.interrogation import DraftFace, DraftReport, unit_vector
from app.kernel.occt.binding import require
from app.kernel.occt.classify import face_area_mm2, face_normal, face_surface_type
from app.kernel.occt.interrogate.sampling import DEFAULT_GRID, sample_face
from app.kernel.occt.topology import faces


def analyse_draft(
    shape: Any,
    pull_direction: tuple[float, float, float] | list[float],
    *,
    required_deg: float = 0.0,
    grid: int = DEFAULT_GRID,
) -> DraftReport:
    """Draft angle for every face, against one pull direction.

    `required_deg` does not change any measurement — it only decides which faces are
    listed as undrafted. A scan with no requirement still reports the minimum, so
    "what draft does this part have" and "does it meet 1.5°" are the same call.
    """
    require()
    pull = unit_vector(pull_direction)

    entries: list[DraftFace] = []
    unevaluated = 0

    for index, face in enumerate(faces(shape)):
        planar = face_surface_type(face) == "Plane"
        angle = _planar_draft(face, pull) if planar else _curved_draft(face, pull, grid)
        if angle is None:
            unevaluated += 1
            continue
        entries.append(
            DraftFace(
                face_index=index,
                draft_deg=angle,
                area_mm2=face_area_mm2(face),
                planar=planar,
            )
        )

    return DraftReport(
        pull_direction=pull,
        faces=tuple(entries),
        unevaluated=unevaluated,
        required_deg=required_deg,
        samples_per_face=grid * grid,
    )


def _planar_draft(face: Any, pull: tuple[float, float, float]) -> float | None:
    normal = face_normal(face)
    if normal is None:
        return None
    return _angle_deg(normal, pull)


def _curved_draft(
    face: Any, pull: tuple[float, float, float], grid: int
) -> float | None:
    """The worst draft found across a curved face's samples.

    Worst by magnitude, keeping its sign: a cylinder around the pull axis has samples at
    +0.0° and −0.0°, and either is an honest report of a wall that will drag.
    """
    worst: float | None = None
    for surface_point in sample_face(face, grid=grid):
        angle = _angle_deg(surface_point.normal, pull)
        if worst is None or abs(angle) < abs(worst):
            worst = angle
    return worst


def _angle_deg(
    normal: tuple[float, float, float], pull: tuple[float, float, float]
) -> float:
    """`asin(n · pull)` in degrees, with the dot product clamped before it reaches asin.

    The clamp is not defensive noise. Both vectors are unit length, so the dot product is
    mathematically in [-1, 1], and floating point routinely returns 1.0000000000000002
    for a face exactly perpendicular to the pull — the most common face on a moulded
    part. Unclamped, `asin` raises `ValueError` on the easiest case in the whole scan.
    """
    dot = sum(normal[i] * pull[i] for i in range(3))
    return math.degrees(math.asin(max(-1.0, min(1.0, dot))))


__all__ = ["analyse_draft"]
