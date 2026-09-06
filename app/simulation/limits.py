"""Pre-flight limits on a meshing request, checked before gmsh is touched.

`max_elements` on its own is a limit you can only discover after the mesher has
already spent the memory and the minutes producing the mesh that breaks it, and
a small enough `element_size_mm` makes those minutes unbounded. Both checks here
run off the geometry's bounding box, which the inspection step already recorded
at upload time, so they cost nothing.

The estimate is deliberately crude. It exists to catch the request that is
orders of magnitude too fine, not to predict the element count -- gmsh's own
count is still the authority once the mesh exists.
"""

from typing import Any

import numpy as np

from app.core.config import settings
from app.mesh.types import MeshError

# A cube of side h fills with roughly six tetrahedra of that edge length (the
# Kuhn decomposition), so one tet occupies about h^3 / 6.
_TETS_PER_CUBE = 6.0


def bounding_box_size(stats: dict[str, Any] | None) -> tuple[float, float, float] | None:
    """The recorded bounding-box extents in mm, or None if unknown.

    Geometry uploaded before the format gained an inspector carries no box, and
    a missing box has to mean "cannot check", never "reject".
    """
    box = (stats or {}).get("bounding_box")
    if not isinstance(box, dict):
        return None
    size = box.get("size")
    if not isinstance(size, (list, tuple)) or len(size) != 3:
        return None
    try:
        extents = tuple(float(value) for value in size)
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(extent) and extent >= 0.0 for extent in extents):
        return None
    return extents  # type: ignore[return-value]


def estimate_element_count(volume_mm3: float, element_size_mm: float) -> float:
    """Roughly how many tets of `element_size_mm` fill `volume_mm3`."""
    return volume_mm3 / (element_size_mm**3 / _TETS_PER_CUBE)


def finest_element_size_mm(stats: dict[str, Any] | None) -> float | None:
    """The smallest element size this geometry can be meshed at within the limits.

    The size that lands exactly on the element budget, rounded *up* to two
    significant figures so the number in the message is one the request can
    actually use -- `4.2 mm` rather than `4.1837 mm`, and never a value that
    the estimate then refuses by a rounding hair. None when the geometry has
    no recorded box to estimate from.
    """
    extents = bounding_box_size(stats)
    if extents is None:
        return None
    diagonal = float(np.linalg.norm(np.asarray(extents, dtype=np.float64)))
    if diagonal <= 0.0:
        return None
    volume = _solid_volume(stats) or float(np.prod(np.asarray(extents, dtype=np.float64)))
    by_count = (volume * _TETS_PER_CUBE / settings.max_elements) ** (1.0 / 3.0)
    by_diagonal = diagonal / settings.max_elements_along_diagonal
    finest = max(by_count, by_diagonal)
    if finest <= 0.0:
        return None
    magnitude = 10.0 ** (np.floor(np.log10(finest)) - 1)
    return float(np.ceil(finest / magnitude) * magnitude)


def check_mesh_request(stats: dict[str, Any] | None, element_size_mm: float | None) -> None:
    """Raise `MeshError` for a request that cannot end well.

    Silent when the geometry has no recorded bounding box or the element size is
    automatic -- the automatic size is derived from that same box and is safe by
    construction.

    The refusal names the size that would fit. Measured on ladder prompt H4 run
    8 (2026-09-06): "increase element_size_mm to coarsen it" cost the agent a
    queued run, a failed run, a poll and a resubmission -- three of its twenty
    rounds -- to arrive at a number this function already knew.
    """
    if element_size_mm is None:
        return
    extents = bounding_box_size(stats)
    if extents is None:
        return

    diagonal = float(np.linalg.norm(np.asarray(extents, dtype=np.float64)))
    if diagonal <= 0.0:
        return

    finest = finest_element_size_mm(stats)
    advice = f" Use at least {finest:g} mm, or omit element_size_mm for an automatic size."

    floor = diagonal / settings.max_elements_along_diagonal
    if element_size_mm < floor:
        raise MeshError(
            f"An element size of {element_size_mm:g} mm is finer than "
            f"{settings.max_elements_along_diagonal:,} elements across the part's "
            f"{diagonal:,.1f} mm diagonal.{advice}"
        )

    # A solid volume, where the format gives one, beats the bounding box: a
    # bracket occupies a fraction of its box and would otherwise be refused for
    # a mesh it can comfortably produce.
    volume = _solid_volume(stats) or float(np.prod(np.asarray(extents, dtype=np.float64)))
    estimate = estimate_element_count(volume, element_size_mm)
    if estimate > settings.max_elements:
        raise MeshError(
            f"An element size of {element_size_mm:g} mm would produce roughly "
            f"{estimate:,.0f} elements, over the {settings.max_elements:,} limit.{advice}"
        )


def _solid_volume(stats: dict[str, Any] | None) -> float | None:
    value = (stats or {}).get("volume_mm3")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    volume = float(value)
    return volume if np.isfinite(volume) and volume > 0.0 else None
