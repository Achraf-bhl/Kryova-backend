"""What a measured part reports, and how two measurements are compared.

Backend-neutral on purpose. OCCT produces these numbers today and the CATIA daemon
produces the same shape of payload from a real seat; the whole two-backend design rests
on neither being able to tell which one an assertion is reading. So the vocabulary
lives here, above both, rather than inside whichever backend happened to be written
first.

`app.design.assertions` reads these keys by path — `mass_kg`,
`bounding_box_mm.size[2]`, `centre_of_mass_mm[0]`. Renaming one silently turns every
assertion that used it into `UNMEASURED`, which is honest and useless, so the names are
declared here as constants and the backends fill them in.

**Detail levels exist for latency, not for taste.** Volume, centre of mass and inertia
each cost a full `BRepGProp` integration over the shape; a bounding box and a face count
cost a traversal. A plan for a machine is 10⁵–10⁶ operations, and computing the full set
after every one of them would dominate the run — which is exactly the cost model
Decision 1 exists to escape. `Detail.SHAPE` is what a batch replay wants; `Detail.FULL`
is what an agent watching a single edit wants, because it cannot react to a number it
was not given.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Final

#: Keys `app.design.assertions` is known to read. Declared so a rename here is a
#: deliberate act with one obvious place to check, rather than a silent downgrade of
#: every assertion that used the old spelling.
MASS_KG: Final = "mass_kg"
VOLUME_MM3: Final = "volume_mm3"
SURFACE_AREA_MM2: Final = "surface_area_mm2"
BOUNDING_BOX_MM: Final = "bounding_box_mm"
CENTRE_OF_MASS_MM: Final = "centre_of_mass_mm"
INERTIA_TENSOR_MM5: Final = "inertia_tensor_mm5"
HAS_SOLID: Final = "has_solid"

#: mm³ × (kg/m³) → kg. One cubic metre is 1e9 mm³, so this factor turns a millimetre
#: volume and a per-cubic-metre density into kilograms. **The only unit conversion in
#: the codebase**, named so it can be found, and appearing in exactly one function.
MM3_KG_PER_M3_TO_KG: Final = 1e-9

#: Default slack when comparing two backends' numbers for the same part. Kernels differ
#: in tessellation and integration order, so bit-equality is not a reasonable ask; this
#: is tight enough that a real modelling divergence cannot hide under it.
CONFORMANCE_TOLERANCE_MM3: Final = 1e-6


class Detail(StrEnum):
    """How much of the payload to compute.

    Ordered cheapest-first. Each level is a superset of the one above it.
    """

    #: Topology counts only. One traversal, no integration. What a bulk replay needs
    #: to know a step produced *something*.
    SHAPE = "shape"

    #: Adds the bounding box — still a traversal, no integration. Enough for envelope
    #: checks and for a viewer to place the part.
    BOUNDS = "bounds"

    #: Adds volume, surface area, centre of mass and mass. Three `BRepGProp`
    #: integrations. The default for interactive work.
    FULL = "full"

    #: Adds the inertia tensor. Only a dynamics or MBD hand-off needs it, and it is
    #: never computed speculatively.
    INERTIA = "inertia"

    def includes(self, other: Detail) -> bool:
        order = (Detail.SHAPE, Detail.BOUNDS, Detail.FULL, Detail.INERTIA)
        return order.index(self) >= order.index(other)


def compare(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    tolerance: float = CONFORMANCE_TOLERANCE_MM3,
) -> list[str]:
    """Which measured quantities disagree between two payloads.

    The comparison the two-backend conformance harness runs: build one plan on OCCT and
    the same plan on a CATIA seat, then ask whether they made the same thing.

    Returns the *disagreeing keys* rather than a bool, because that is what turns a
    divergence into a finding — "volume agrees, centre of mass does not" localises the
    bug immediately, where `False` starts a bisect. A key absent from both sides is not
    a disagreement; a key present on one side only is.
    """
    disagreements: list[str] = []

    for key in (VOLUME_MM3, SURFACE_AREA_MM2, MASS_KG):
        disagreements.extend(_scalar_disagreement(left, right, key, tolerance))

    for key in ("face_count", "edge_count", "solid_count", HAS_SOLID):
        if key in left or key in right:
            if left.get(key) != right.get(key):
                disagreements.append(key)

    if _vector_differs(left.get(CENTRE_OF_MASS_MM), right.get(CENTRE_OF_MASS_MM), tolerance):
        disagreements.append(CENTRE_OF_MASS_MM)

    left_size = (left.get(BOUNDING_BOX_MM) or {}).get("size")
    right_size = (right.get(BOUNDING_BOX_MM) or {}).get("size")
    if _vector_differs(left_size, right_size, tolerance):
        disagreements.append(f"{BOUNDING_BOX_MM}.size")

    return disagreements


def _scalar_disagreement(
    left: Mapping[str, Any], right: Mapping[str, Any], key: str, tolerance: float
) -> list[str]:
    if key not in left and key not in right:
        return []
    a, b = left.get(key), right.get(key)
    if a is None or b is None:
        return [] if a is b else [key]
    return [key] if abs(float(a) - float(b)) > tolerance else []


def _vector_differs(
    left: Sequence[float] | None, right: Sequence[float] | None, tolerance: float
) -> bool:
    if left is None or right is None:
        return left is not right
    if len(left) != len(right):
        return True
    return any(abs(float(a) - float(b)) > tolerance for a, b in zip(left, right, strict=True))


def mass_kg(volume_mm3: float, density_kg_m3: float) -> float:
    """The codebase's one unit conversion, in the one place it is allowed to happen."""
    return volume_mm3 * density_kg_m3 * MM3_KG_PER_M3_TO_KG


__all__ = [
    "BOUNDING_BOX_MM",
    "CENTRE_OF_MASS_MM",
    "CONFORMANCE_TOLERANCE_MM3",
    "HAS_SOLID",
    "INERTIA_TENSOR_MM5",
    "MASS_KG",
    "MM3_KG_PER_M3_TO_KG",
    "SURFACE_AREA_MM2",
    "VOLUME_MM3",
    "Detail",
    "compare",
    "mass_kg",
]
