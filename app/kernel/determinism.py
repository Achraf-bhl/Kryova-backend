"""Making "same spec plus same version ⇒ same geometry" a checkable claim.

Master plan Phase 1.6 and roadmap I5. Determinism is not a nicety here: a cached
simulation result, a provenance record and the whole regeneration model all assume that
rebuilding a design produces the same part. Assuming it is easy; *checking* it needs a
stable identity for a piece of geometry, which is what this module provides.

**Why a measured digest rather than hashing the B-rep.** OCCT's serialised B-rep carries
tolerances, ordering and internal identifiers that can differ between runs without the
geometry differing at all — hashing it answers a stricter question than the one anyone
is asking, and would report false differences forever. So the digest is taken over
*measured, rounded quantities*: volume, area, centre of mass, bounding box and topology
counts. Two shapes with the same digest are the same shape to the precision declared;
two with different digests really do differ.

**Rounding is part of the contract, not a fudge.** Floating-point integration over a
tessellated surface is not bit-reproducible across platforms, so the digest rounds to a
declared number of decimals. `DIGEST_DECIMALS` is that declaration: tight enough that a
real geometric change moves it, loose enough that the same build on two machines does
not. Changing it changes every stored digest, which is why it is a named constant with
this note attached rather than a literal inside a function.

**The environment is part of the identity.** A digest means nothing without the kernel
version that produced it — "same version" is half of the claim — so `environment()`
records what a provenance entry needs alongside it.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Mapping
from typing import Any, Final

from app.kernel import measurement as spec
from app.kernel.measurement import Detail
from app.kernel.occt.binding import DISTRIBUTION, available, occt_version

#: Decimal places kept before hashing. 6 puts the threshold at a nanometre on a
#: millimetre-scale part — far below any manufacturable feature, far above the noise of
#: repeated integration over the same shape.
DIGEST_DECIMALS: Final = 6

#: Quantities that make up the identity of a shape, in a fixed order. Ordered explicitly
#: rather than taken from the payload's keys, because a payload gaining a key would
#: otherwise silently change every digest ever computed.
DIGEST_KEYS: Final[tuple[str, ...]] = (
    spec.HAS_SOLID,
    "solid_count",
    "face_count",
    "edge_count",
    spec.VOLUME_MM3,
    spec.SURFACE_AREA_MM2,
    spec.CENTRE_OF_MASS_MM,
    spec.BOUNDING_BOX_MM,
)


def geometry_digest(measurement: Mapping[str, Any]) -> str:
    """A stable identity for one piece of geometry, from its measurements.

    Requires a payload measured at `Detail.FULL` or better: a cheaper one omits volume
    and centre of mass, and a digest computed over what is left would collide between
    genuinely different parts. That is checked rather than assumed, because a silently
    weaker digest is worse than none.
    """
    missing = [
        key
        for key in (spec.VOLUME_MM3, spec.SURFACE_AREA_MM2, spec.BOUNDING_BOX_MM)
        if key not in measurement
    ]
    if missing and measurement.get(spec.HAS_SOLID, False):
        raise ValueError(
            f"A geometry digest needs a full measurement; {', '.join(missing)} "
            f"is missing. Measure at Detail.FULL ({Detail.FULL}) or better."
        )

    canonical = {key: _round(measurement.get(key)) for key in DIGEST_KEYS}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _round(value: Any) -> Any:
    """Round every number in a nested structure to the declared precision."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        # `+ 0.0` normalises -0.0 to 0.0: they are equal but serialise differently, and
        # a centre of mass on a symmetry plane lands on one or the other by luck.
        return round(float(value), DIGEST_DECIMALS) + 0.0
    if isinstance(value, Mapping):
        return {key: _round(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    return value


def environment() -> dict[str, str]:
    """What a provenance record needs alongside a digest.

    "Same spec plus same version ⇒ same geometry" is uncheckable without recording the
    version. Python and platform are included because a kernel is a compiled binary and
    the platform is part of what produced the number.
    """
    from app.kernel.contract import CONTRACT_VERSION

    return {
        "kernel": occt_version() if available() else "unavailable",
        "binding": DISTRIBUTION,
        "python": platform.python_version(),
        "platform": f"{platform.system()}-{platform.machine()}",
        "implementation": sys.implementation.name,
        "digest_decimals": str(DIGEST_DECIMALS),
        # The measurement contract is part of what produced the numbers: a stored result
        # is read back by a later version that needs to know which quantities existed and
        # what they meant. Imported here rather than at module scope because `contract`
        # imports `interrogation`, which would make every determinism import drag the
        # interrogation stack in behind it.
        "contract": CONTRACT_VERSION,
    }


def is_reproducible(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Did two builds of the same design produce the same geometry?"""
    return geometry_digest(first) == geometry_digest(second)


__all__ = [
    "DIGEST_DECIMALS",
    "DIGEST_KEYS",
    "environment",
    "geometry_digest",
    "is_reproducible",
]
