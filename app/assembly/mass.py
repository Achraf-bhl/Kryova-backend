"""What the machine weighs, where its centre of mass is, and what was left out.

Master plan 14.2's mass budget, and the one number in this package where the honest
answer and the convenient answer differ most sharply. **An assembly mass that silently
omits a part is wrong in the direction that matters**: it is too light, so every mass
budget it is checked against passes, and the machine that gets built is heavier than the
one that was signed off.

So the rule here is the one `app.design.assertions` already applies to a claim it could
not measure, applied to a sum:

* every occurrence that was weighed is listed;
* every occurrence that was **not** weighed is listed too, with the reason;
* and `mass_kg` is published **only when nothing was left out**. When something was, the
  partial sum goes out as `measured_mass_kg` and `mass_kg` carries an `UNAVAILABLE`
  provenance record naming the occurrences that are missing. An assertion on `mass_kg`
  then comes back `UNMEASURED`, which is the verdict a partly-weighed machine deserves.

**Each component is weighed once, however many times it is used.** That is the graph
paying for itself: a bolt at forty places is one `BRepGProp` integration and forty frame
transforms, not forty integrations. `roll_up` caches on the component name and the tests
assert the measurer was called once for a component used forty times — because a cache
that quietly stopped working would show up only as a slow run, and a slow run is the
kind of defect that gets lived with.

**The centre of mass is exact arithmetic on measured numbers, not an approximation.**
Each component reports its own centre in its own coordinates; the occurrence's frame
places it (`Frame.point`, a rotation and a translation, no tolerance anywhere); and the
assembly's centre is the mass-weighted mean. A two-cube fixture with a closed-form
answer pins it in `tests/test_assembly_mass.py`, in the same discipline the solver tests
keep — checked against an answer known in advance, never against recorded output.

**A component with no density is unmeasured, not weightless.** `metrology.measure` sets
`mass_is_provisional` and omits `mass_kg` when it was given no density, precisely so a
part with no material cannot be reported as weighing something. Summing a payload that
has volume but no mass would put that guard back to sleep at the assembly level, so a
payload with no `mass_kg` is a missing occurrence with the reason quoted — from the
provenance sidecar where the payload carries one, from `mass_is_provisional` where it
does not.

Units, per the repository rule: mass is kilograms, lengths and centres are millimetres,
and nothing here converts. `app.kernel.measurement.mass_kg` remains the codebase's one
conversion and it happens below this module, not in it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.assembly.structure import Occurrence, ProductStructure
from app.dynamics.pose import Vec3

#: Called with a component name; returns that component's measurement payload in its own
#: local coordinates — `mass_kg` and `centre_of_mass_mm` at minimum. Injected, so this
#: module is testable with no OCCT, no seat and no file, which is the discipline
#: `app.design.execute` keeps for the one thing in it that touches the outside world.
ComponentMeasurer = Callable[[str], Mapping[str, Any]]

#: Payload keys this module reads. Spelled through `app.kernel.measurement`'s constants
#: at the seam (`from_payload`) rather than inline, so a rename there is one edit.
MASS_KG: Final = "mass_kg"
CENTRE_OF_MASS_MM: Final = "centre_of_mass_mm"

#: Same reason as `app.assembly.contracts.PROVENANCE_KEY`: spelled here so this package
#: imports without pulling ~166 MB of OCP in behind `app.kernel.__init__`. Asserted equal
#: to `app.kernel.provenance.PROVENANCE_KEY` by the tests.
PROVENANCE_KEY: Final = "provenance"


@dataclass(frozen=True)
class WeighedOccurrence:
    """One occurrence that was weighed, and where its mass acts in world coordinates."""

    path: str
    component: str
    mass_kg: float

    #: In world coordinates — the component's own centre, placed by the occurrence's
    #: frame. Stored placed rather than local because that is what the sum needs and
    #: storing both would be two things to keep in step.
    centre_of_mass_mm: Vec3

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "component": self.component,
            "mass_kg": self.mass_kg,
            "centre_of_mass_mm": list(self.centre_of_mass_mm),
        }


@dataclass(frozen=True)
class MissingMass:
    """One occurrence that could not be weighed, and why. Never merely absent."""

    path: str
    component: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "component": self.component, "reason": self.reason}

    def __str__(self) -> str:
        return f"{self.path} ({self.component}): {self.reason}"


@dataclass(frozen=True)
class MassRollup:
    """The assembly's mass properties, and the occurrences that are not in them.

    Truthy only when `complete` — nothing was left out. A partly-weighed machine is not
    a lighter machine, it is a machine nobody has weighed, and the reading that makes
    that hard to ignore is the conservative one.
    """

    weighed: tuple[WeighedOccurrence, ...] = ()
    missing: tuple[MissingMass, ...] = ()
    occurrences: int = 0

    #: Components the measurer was asked about. Equal to the number of *distinct*
    #: components, not occurrences — carried so a caller can see the graph working.
    components_measured: int = 0

    @property
    def complete(self) -> bool:
        return not self.missing and bool(self.weighed)

    def __bool__(self) -> bool:
        return self.complete

    @property
    def measured_mass_kg(self) -> float:
        """The sum over the occurrences that *were* weighed.

        Always a lower bound on the assembly's mass when the roll-up is incomplete, and
        named `measured_` rather than `total_` for that reason: nothing should be able to
        read this as the machine's mass without the word measured in front of it.
        """
        return sum(item.mass_kg for item in self.weighed)

    @property
    def mass_kg(self) -> float | None:
        """The assembly's mass — `None` unless every occurrence was weighed."""
        return self.measured_mass_kg if self.complete else None

    @property
    def centre_of_mass_mm(self) -> Vec3 | None:
        """The mass-weighted centre of the weighed occurrences, in world mm.

        `None` when nothing was weighed, or when the weighed mass is zero — a centroid
        of nothing is not the origin, and returning `(0, 0, 0)` would put the machine's
        centre of gravity at a point nobody computed.
        """
        total = self.measured_mass_kg
        if not self.weighed or total <= 0.0:
            return None
        return (
            sum(i.mass_kg * i.centre_of_mass_mm[0] for i in self.weighed) / total,
            sum(i.mass_kg * i.centre_of_mass_mm[1] for i in self.weighed) / total,
            sum(i.mass_kg * i.centre_of_mass_mm[2] for i in self.weighed) / total,
        )

    def heaviest(self, limit: int = 5) -> tuple[WeighedOccurrence, ...]:
        """The occurrences carrying the most mass — where a mass budget goes to look."""
        return tuple(sorted(self.weighed, key=lambda i: -i.mass_kg)[:limit])

    def by_component(self) -> dict[str, float]:
        """Mass per component summed over its occurrences, for a mass-by-part-number view."""
        totals: dict[str, float] = {}
        for item in self.weighed:
            totals[item.component] = totals.get(item.component, 0.0) + item.mass_kg
        return totals

    def to_payload(self) -> dict[str, Any]:
        """The roll-up as a measurement payload `app.design.assertions` can read.

        `mass_kg` and `centre_of_mass_mm` are `app.kernel.measurement`'s own spellings,
        so a mass-budget assertion written against a single part reads an assembly
        unchanged. That is the point of those constants being constants — and it is also
        why an incomplete roll-up must not fill them in: an assertion cannot tell the
        difference between a part's mass and an assembly's, so a partial sum published
        under that name is a false pass with no way to see it.
        """
        from app.kernel import measurement, provenance

        payload: dict[str, Any] = {
            "occurrence_count": self.occurrences,
            "weighed_occurrence_count": len(self.weighed),
            "unmeasured_occurrence_count": len(self.missing),
            "component_count": self.components_measured,
            "complete": self.complete,
        }

        if not self.weighed:
            provenance.attach(
                payload,
                measurement.MASS_KG,
                provenance.unavailable(
                    "no occurrence in this assembly could be weighed"
                    + (f": {self.missing[0].reason}" if self.missing else ".")
                ),
            )
            return payload

        if self.complete:
            payload[measurement.MASS_KG] = self.measured_mass_kg
            provenance.attach(
                payload,
                measurement.MASS_KG,
                provenance.measured(
                    f"sum over all {len(self.weighed)} occurrences, each from its "
                    "component's integrated volume and declared density"
                ),
            )
        else:
            payload["measured_mass_kg"] = self.measured_mass_kg
            names = ", ".join(item.path for item in self.missing[:3])
            more = "" if len(self.missing) <= 3 else f" and {len(self.missing) - 3} more"
            provenance.attach(
                payload,
                measurement.MASS_KG,
                provenance.unavailable(
                    f"{len(self.missing)} of {self.occurrences} occurrences could not be "
                    f"weighed ({names}{more}), so the sum of the rest is a lower bound on "
                    "the assembly's mass, not its mass. The partial sum is reported as "
                    f"measured_mass_kg. First reason: {self.missing[0].reason}"
                ),
            )

        centre = self.centre_of_mass_mm
        if centre is None:
            provenance.attach(
                payload,
                measurement.CENTRE_OF_MASS_MM,
                provenance.unavailable(
                    "the weighed occurrences carry no mass, so they have no centre of mass."
                ),
            )
        elif self.complete:
            payload[measurement.CENTRE_OF_MASS_MM] = list(centre)
            provenance.attach(
                payload,
                measurement.CENTRE_OF_MASS_MM,
                provenance.measured(
                    "mass-weighted mean of every occurrence's placed centre of mass"
                ),
            )
        else:
            payload["measured_centre_of_mass_mm"] = list(centre)
            provenance.attach(
                payload,
                measurement.CENTRE_OF_MASS_MM,
                provenance.unavailable(
                    f"{len(self.missing)} occurrences are missing from the sum, and a "
                    "centre of mass that omits part of the machine can be anywhere. The "
                    "centre of what was weighed is reported as "
                    "measured_centre_of_mass_mm."
                ),
            )
        return payload

    def summary(self) -> str:
        if not self.weighed and not self.missing:
            return "Nothing to weigh."
        head = (
            f"{len(self.weighed)} of {self.occurrences} occurrences weighed "
            f"({self.components_measured} components measured): "
            f"{self.measured_mass_kg:.4g} kg"
        )
        centre = self.centre_of_mass_mm
        if centre is not None:
            head += f", centre ({centre[0]:.4g}, {centre[1]:.4g}, {centre[2]:.4g}) mm"
        if not self.missing:
            return head + "."
        listing = "\n".join(f"  not weighed: {item}" for item in self.missing)
        return (
            head
            + ".\nThis is a lower bound, not the assembly's mass — "
            + f"{len(self.missing)} occurrence(s) were left out:\n"
            + listing
        )


def roll_up(structure: ProductStructure, measure: ComponentMeasurer) -> MassRollup:
    """Weigh every leaf occurrence, measuring each component exactly once.

    The measurer is allowed to fail. An exception is caught and recorded against every
    occurrence of that component — one pathological part must not cost the mass of the
    other 4,999, which is the contract every measuring surface in this codebase keeps.
    What it must never do is disappear: a component that could not be measured produces
    a `MissingMass` per occurrence, and that is what makes the roll-up incomplete.
    """
    weighed: list[WeighedOccurrence] = []
    missing: list[MissingMass] = []
    payloads: dict[str, Mapping[str, Any] | None] = {}
    reasons: dict[str, str] = {}
    total = 0

    for occurrence in structure.occurrences(leaves_only=True):
        total += 1
        if occurrence.component not in payloads:
            try:
                payloads[occurrence.component] = measure(occurrence.component)
            except Exception as exc:  # noqa: BLE001 - a measurer's failure is data
                payloads[occurrence.component] = None
                reasons[occurrence.component] = (
                    f"measuring {occurrence.component} raised "
                    f"{type(exc).__name__}: {exc}"
                )
        payload = payloads[occurrence.component]
        if payload is None:
            missing.append(
                MissingMass(occurrence.path, occurrence.component, reasons[occurrence.component])
            )
            continue
        placed = _weigh(occurrence, payload)
        if isinstance(placed, MissingMass):
            missing.append(placed)
        else:
            weighed.append(placed)

    return MassRollup(
        weighed=tuple(weighed),
        missing=tuple(missing),
        occurrences=total,
        components_measured=len(payloads),
    )


def _weigh(
    occurrence: Occurrence, payload: Mapping[str, Any]
) -> WeighedOccurrence | MissingMass:
    """One occurrence's mass and placed centre, or the reason it has neither.

    Reads `mass_kg` and `centre_of_mass_mm` and refuses to improvise either. In
    particular it does **not** fall back to volume times a guessed density — that is the
    invented number `metrology.measure` already refuses to produce, and re-inventing it
    here would defeat the guard from above.
    """
    mass = payload.get(MASS_KG)
    if mass is None or isinstance(mass, bool) or not isinstance(mass, (int, float)):
        return MissingMass(occurrence.path, occurrence.component, _why_no_mass(payload))

    centre = payload.get(CENTRE_OF_MASS_MM)
    if not isinstance(centre, (list, tuple)) or len(centre) != 3:
        return MissingMass(
            occurrence.path,
            occurrence.component,
            f"{occurrence.component} reports a mass of {float(mass):g} kg but no centre "
            "of mass, so its mass cannot be placed in the assembly. Measure it at "
            "Detail.FULL, which is where centre_of_mass_mm comes from.",
        )

    local: Vec3 = (float(centre[0]), float(centre[1]), float(centre[2]))
    return WeighedOccurrence(
        path=occurrence.path,
        component=occurrence.component,
        mass_kg=float(mass),
        centre_of_mass_mm=occurrence.frame.point(local),
    )


def _why_no_mass(payload: Mapping[str, Any]) -> str:
    """The most useful thing that can be said about a payload with no mass in it.

    Three cases in descending order of how much the payload knows, mirroring
    `assertions._why_missing`: a recorded provenance reason, the provisional-mass flag
    that says the density was never set, and finally the bare fact.
    """
    sidecar = payload.get(PROVENANCE_KEY)
    if isinstance(sidecar, Mapping):
        entry = sidecar.get(MASS_KG)
        if isinstance(entry, Mapping) and entry.get("reason"):
            return str(entry["reason"])
    if payload.get("mass_is_provisional"):
        return (
            "it has a volume but no density, so it has no mass. Set the component's "
            "material — an assembly mass that quietly treated it as weightless would be "
            "wrong in the direction every mass budget passes."
        )
    if payload.get("has_solid") is False:
        return (
            "it encloses no solid, so it has no volume and no mass. Check that the "
            "component's last operation succeeded."
        )
    return (
        "its measurement reports no mass_kg. Measure it at Detail.FULL with a density "
        "set, which is what produces one."
    )


def from_document(documents: Mapping[str, Any]) -> ComponentMeasurer:
    """A `ComponentMeasurer` reading each component's `PartDocument.measure()`.

    `documents` maps a component name to an `app.kernel.occt.PartDocument` — what
    `OcctRunner.document` hands back after a plan has been run. A component with no
    document raises `KeyError` from inside the measurer, which `roll_up` turns into a
    `MissingMass` naming it: a part nobody built is a hole in the mass, and it says so.
    """

    def measurer(component: str) -> Mapping[str, Any]:
        from app.kernel.measurement import Detail

        try:
            document = documents[component]
        except KeyError:
            raise KeyError(
                f"no document was supplied for component {component!r}, so it cannot be "
                "weighed. Build it, or leave it out of the structure — a component with "
                "no geometry is not a component with no mass."
            ) from None
        return document.measure(detail=Detail.FULL)

    return measurer


__all__ = [
    "CENTRE_OF_MASS_MM",
    "MASS_KG",
    "ComponentMeasurer",
    "MassRollup",
    "MissingMass",
    "WeighedOccurrence",
    "from_document",
    "roll_up",
]
