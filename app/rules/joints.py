"""Bolted-joint rules: clamp length, preload against the service load, and thread engagement (E13.1).

The rules in `processes.py` read the measured solid. A bolted joint's rules are not on any
one solid: they are about a bolt from `app.parts.fasteners`, the stack it clamps and the
hole it threads into. So they are checked here, as **assertions in the E5 vocabulary**
(`app.design.assertions`), over a payload this module builds, with provenance saying which
numbers are exact and which are estimates.

Three rules, each resting on something already sourced in the codebase or stated by the
caller:

* **The bolt clamps the stack.** The clamp-length range is on the catalogue record
  (`fasteners.clamp_length_range_mm`), estimated there from nominal geometry, so the margin
  is recorded approximated.
* **The preload holds the joint shut under the service load.** The assembly preload is
  VDI 2230's closed form (`fasteners.assembly_preload`), which is `ESTIMATED` and stays so,
  and the ratio preload / service load is checked against a minimum the caller states with
  its source. A joint calculation proper also needs the joint's stiffness and how the load
  is introduced; the note says this rule is a screen, not that calculation.
* **A tapped hole engages enough thread.** The engaged length is checked against a minimum
  multiple of the nominal diameter, which depends on the nut material and is the caller's
  with its source. A through bolt with a nut has no such rule and says so.

No factor has a default here, for the reason `processes.py` gives: a separation factor or
an engagement ratio typed into this file would be a remembered number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.parts.fasteners import assembly_preload, clamp_length_range_mm
from app.parts.types import StandardPart
from app.rules.errors import RuleError
from app.rules.processes import Limit


@dataclass(frozen=True)
class BoltedJoint:
    name: str
    bolt: StandardPart
    #: Everything the bolt clamps, washers included, in mm.
    stack_mm: float
    #: The axial load trying to separate the joint in service, in N.
    service_load_n: float
    service_load_source: str
    #: preload / service load must be at least this.
    minimum_preload_ratio: Limit
    thread_friction: float
    utilisation: float
    #: Engaged thread in a tapped hole, mm; None for a through bolt with a nut.
    engagement_mm: float | None = None
    #: engaged length / nominal diameter must be at least this, for a tapped hole.
    minimum_engagement_ratio: Limit | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or "." in self.name:
            raise RuleError(
                f"A joint needs a name without dots, for its measurement path; got {self.name!r}."
            )
        if self.stack_mm <= 0.0:
            raise RuleError(f"{self.name}: a clamped stack of {self.stack_mm} mm clamps nothing.")
        if self.service_load_n <= 0.0:
            raise RuleError(
                f"{self.name}: a service load of {self.service_load_n} N does not try to open "
                "the joint. Give the axial separating load, positive."
            )
        if not self.service_load_source.strip():
            raise RuleError(f"{self.name}: say where the service load came from.")
        if (self.engagement_mm is None) != (self.minimum_engagement_ratio is None):
            raise RuleError(
                f"{self.name}: a tapped hole needs both its engaged length and the minimum "
                "engagement ratio with its source; a through bolt with a nut needs neither."
            )


def _payload(joint: BoltedJoint) -> dict[str, Any]:
    from app.kernel import provenance

    base = f"joint.{joint.name}"
    low, high = clamp_length_range_mm(joint.bolt)
    preload = assembly_preload(
        joint.bolt, thread_friction=joint.thread_friction, utilisation=joint.utilisation
    )
    payload: dict[str, Any] = {
        "joint": {
            joint.name: {
                "clamp_margin_mm": min(joint.stack_mm - low, high - joint.stack_mm),
                "preload_n": preload.value,
                "preload_ratio": preload.value / joint.service_load_n,
            }
        }
    }
    provenance.attach(
        payload,
        f"{base}.clamp_margin_mm",
        provenance.approximated(
            "the catalogue's clamp-length range, itself estimated from the bolt's nominal geometry"
        ),
    )
    provenance.attach(
        payload,
        f"{base}.preload_ratio",
        provenance.approximated(
            f"VDI 2230 assembly preload at thread friction {joint.thread_friction:g} and "
            f"{joint.utilisation:g} of yield, over a service load from "
            f"{joint.service_load_source}; a screen, not a joint stiffness calculation"
        ),
    )
    if joint.engagement_mm is not None:
        diameter = joint.bolt.require("nominal_diameter_mm").value
        payload["joint"][joint.name]["engagement_ratio"] = joint.engagement_mm / diameter
        provenance.attach(
            payload, f"{base}.engagement_ratio", provenance.measured("stated engaged length")
        )
    return payload


def assertions(joint: BoltedJoint) -> tuple[Assertion, ...]:
    base = f"joint.{joint.name}"
    out = [
        Assertion(
            name=f"{joint.name}.clamps",
            measure=f"{base}.clamp_margin_mm",
            comparison=">=",
            bound=0.0,
            note=f"{joint.bolt.designation} must clamp a {joint.stack_mm:g} mm stack",
        ),
        Assertion(
            name=f"{joint.name}.preload",
            measure=f"{base}.preload_ratio",
            comparison=">=",
            bound=joint.minimum_preload_ratio.value,
            note=f"source: {joint.minimum_preload_ratio.source}",
        ),
    ]
    if joint.minimum_engagement_ratio is not None:
        out.append(
            Assertion(
                name=f"{joint.name}.engagement",
                measure=f"{base}.engagement_ratio",
                comparison=">=",
                bound=joint.minimum_engagement_ratio.value,
                note=f"source: {joint.minimum_engagement_ratio.source}",
            )
        )
    return tuple(out)


def check_joint(joint: BoltedJoint) -> AssertionReport:
    """The joint's rules checked, in the verdict vocabulary every other check uses."""
    return check_assertions(assertions(joint), _payload(joint))


__all__ = ["BoltedJoint", "assertions", "check_joint"]
