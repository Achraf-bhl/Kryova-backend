"""How a number was arrived at, carried beside the number itself.

Master plan 3.5, and the generalisation of a discipline this codebase already keeps in
three places: the CATIA mock says its mass is estimated rather than measured, the
measurement payload omits volume on a shape with no solid rather than reporting zero,
and `app.design.assertions` treats a missing measurement as `UNMEASURED` rather than as
a pass. Each of those is the same rule applied locally. This is the rule itself.

**Three bases, and the middle one is why this module exists.**

* `MEASURED` — the kernel integrated or evaluated it exactly. Volume, area, centre of
  mass, a face's area, the angle between a normal and an axis.
* `APPROXIMATED` — computed by sampling, ray casting or discretisation, so the answer
  depends on how densely it was sampled and is a *bound*, not a value. Wall thickness
  and undercut visibility are the honest examples: both cast rays from a finite set of
  points, and neither can prove the thin spot lies at one of them.
* `UNAVAILABLE` — could not be computed, with a reason. Never rendered as `0`, `None`
  or a silently absent key with no explanation.

**Values stay plain; provenance is a sidecar.** `assertions.read_measurement` walks
paths like `bounding_box_mm.size[2]` into the payload, so wrapping each number in
`{"value": …, "basis": …}` would break every assertion ever written and force a second
path syntax. Instead the payload keeps `minimum_wall_mm: 2.8` where it always was, and
`provenance.minimum_wall_mm` records how it got there. `measurable_paths` skips the
sidecar for free, because its entries are strings rather than numbers.

**An `UNAVAILABLE` entry has no value at all.** Recording the reason without a number is
the point: an assertion on it comes back `UNMEASURED` — which it would anyway — but now
it can say *why* instead of "nothing reports that", which is the difference between a
message someone can act on and one they cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

#: Reserved key under which the sidecar lives in a measurement payload. Chosen to be a
#: word no geometric quantity would use, because a payload key collision here would
#: silently reclassify a real measurement.
PROVENANCE_KEY: Final = "provenance"


class Basis(StrEnum):
    """How a reported number was obtained."""

    MEASURED = "measured"
    APPROXIMATED = "approximated"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Record:
    """One quantity's provenance.

    `method` names the technique in words an engineer reviewing the part would want —
    "ray cast from 64 points per face", "BRepGProp volume integration". It is what makes
    an approximated number auditable rather than merely flagged.
    """

    basis: Basis
    method: str = ""

    #: Why the quantity is unavailable. Required for `UNAVAILABLE`, empty otherwise.
    reason: str = ""

    def __post_init__(self) -> None:
        if self.basis is Basis.UNAVAILABLE and not self.reason:
            raise ValueError(
                "An unavailable measurement must say why. A quantity that is simply "
                "absent, with no reason, is indistinguishable from one nobody asked for."
            )

    def to_dict(self) -> dict[str, str]:
        out: dict[str, str] = {"basis": str(self.basis)}
        if self.method:
            out["method"] = self.method
        if self.reason:
            out["reason"] = self.reason
        return out


def measured(method: str = "") -> Record:
    return Record(basis=Basis.MEASURED, method=method)


def approximated(method: str) -> Record:
    """An approximated number must name its method — that is the whole of its honesty."""
    if not method.strip():
        raise ValueError(
            "An approximated measurement must name how it was approximated, so a "
            "reviewer can judge whether the sampling was dense enough for the claim."
        )
    return Record(basis=Basis.APPROXIMATED, method=method)


def unavailable(reason: str) -> Record:
    return Record(basis=Basis.UNAVAILABLE, reason=reason)


def attach(payload: dict[str, Any], path: str, record: Record) -> None:
    """Record one quantity's provenance into a payload's sidecar, creating it if needed.

    Mutates rather than returning a copy: a measurement payload is assembled key by key
    across several modules, and copying it at each step would be the kind of quiet
    per-operation cost `Detail` exists to avoid.
    """
    sidecar = payload.setdefault(PROVENANCE_KEY, {})
    sidecar[path] = record.to_dict()


def basis_of(payload: Mapping[str, Any], path: str) -> Basis | None:
    """The basis recorded for one path, or None if the payload does not say.

    None is not `MEASURED`. A payload that carries no provenance at all — every one
    written before this module existed, and every one a third-party backend returns —
    is making no claim either way, and inventing `MEASURED` for it would be exactly the
    false confidence this module exists to prevent.
    """
    sidecar = payload.get(PROVENANCE_KEY)
    if not isinstance(sidecar, Mapping):
        return None
    entry = sidecar.get(path)
    if not isinstance(entry, Mapping):
        return None
    try:
        return Basis(str(entry.get("basis")))
    except ValueError:
        return None


def reason_for(payload: Mapping[str, Any], path: str) -> str:
    """Why a path is unavailable, or an empty string if no reason was recorded."""
    sidecar = payload.get(PROVENANCE_KEY)
    if not isinstance(sidecar, Mapping):
        return ""
    entry = sidecar.get(path)
    if not isinstance(entry, Mapping):
        return ""
    return str(entry.get("reason") or "")


def method_for(payload: Mapping[str, Any], path: str) -> str:
    """How a path was computed, or an empty string if no method was recorded."""
    sidecar = payload.get(PROVENANCE_KEY)
    if not isinstance(sidecar, Mapping):
        return ""
    entry = sidecar.get(path)
    if not isinstance(entry, Mapping):
        return ""
    return str(entry.get("method") or "")



__all__ = [
    "PROVENANCE_KEY",
    "Basis",
    "Record",
    "approximated",
    "attach",
    "basis_of",
    "measured",
    "method_for",
    "reason_for",
    "unavailable",
]
