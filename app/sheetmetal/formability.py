"""Whether the part can actually be made: three limits, each with its source.

Phase 17.3. `unfold.py` answers "does a blank exist"; this answers "will a press
brake produce the part from it". The two are separate on purpose — a part can be
perfectly flattenable and impossible to form, and a check that conflated them
would report a geometry problem for a tooling one and send a correction loop
after the wrong thing.

**Three rules, and every one of them refuses rather than warns.**

1. **The bend radius is at or above the material minimum.** Below it the outer
   fibre cracks. The limit is a material property with a source
   (`material.MinimumBendRadius`); a material that has not stated one gives an
   **`UNMEASURED` finding naming what is missing**, which is never a pass.
   The finding also carries the exact outer-fibre strain `(1-K)*t/(r + K*t)`,
   because "you are at 0.9 of the minimum radius" and "the outside of that bend
   stretches 31%" are the same fact and the second is the one an engineer can
   act on.
2. **The flange is long enough to form.** In air bending the workpiece rests on
   the two die shoulders; a flange that does not reach past the shoulder is not
   held by anything and the part slips into the die. The limit is geometric —
   `V/2 + r + t` measured to the outside mould line — and the die opening is
   `AIR_BEND_DIE_RATIO * t` when the caller does not name one.
3. **A hole is far enough from a bend.** The material inside a bend zone
   stretches, so a hole near one comes out oval and pulled toward the bend. The
   limit is `HOLE_EDGE_TO_TANGENT_FACTOR * t` from the hole's edge to the bend
   tangent line.

**There is no warning outcome, and that is a decision.** `Outcome` comes from
`app.design.assertions` — `PASSED`, `FAILED`, `UNMEASURED` — reused rather than
restated, for the reason `app/rules/engine.py` gives about itself: a second
verdict vocabulary is a second set of edge cases to keep in step. A warning is a
failure somebody is allowed to ignore, and on a part that cracks in the press
that is not a service to anyone.

**The checks return a report and the raise is a separate step.** A shop wants
every reason at once — "this bend is too tight *and* that flange is 3 mm short"
— not the first one and then another run. So `check_part()` collects, and
`FormabilityReport.raise_for_findings()` is the explicit refusal for a caller
that wants one.

**Two of the three limits are adopted conventions, not theorems, and say so.**
`AIR_BEND_DIE_RATIO` and `HOLE_EDGE_TO_TANGENT_FACTOR` are shop-floor practice
and are named constants a caller can override per part, following
`app/rules/stackup.py`'s `MINIMUM_CONTRIBUTORS_FOR_RSS`: a number that is a
convention is a constant with the convention written beside it, never a literal
inside the arithmetic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from app.design.assertions import Outcome
from app.sheetmetal.bend import STRAIGHT_ANGLE_DEG, Bend, LengthConvention, setback_mm
from app.sheetmetal.errors import FormabilityError
from app.sheetmetal.unfold import Edge, Flange, Hole, Joint, SheetMetalPart, unfold
from app.solve.materials import Source, SourceKind

#: V-die opening as a multiple of material thickness, for air bending. Eight is
#: the usual starting point in press-brake practice and the figure the natural
#: inside radius (about 0.16 V) is normally quoted against. It is an **adopted
#: convention, not a theorem** — a shop with a fixed set of dies has a real
#: number and should pass it — which is why it is a named constant rather than
#: an 8 inside the formula.
AIR_BEND_DIE_RATIO: Final = 8.0

#: How far a hole's edge must sit from a bend tangent line, as a multiple of
#: thickness. Also an adopted convention. The same rule is often written as
#: "hole centre at least 2.5t + r from the bend line"; measured from the mould
#: line that works out within a few tenths of this for small holes, and the
#: edge-to-tangent form is used here because it is the physically meaningful
#: one — what matters is the material between the hole and the deforming zone,
#: not where the hole's middle is.
HOLE_EDGE_TO_TANGENT_FACTOR: Final = 2.0

_DIE_SHOULDER_SOURCE: Final = Source(
    citation=(
        "Air-bending geometry: the flange must reach past the die shoulder at V/2 from "
        "the bend centreline, so the outside mould-line flange must exceed V/2 + r + t"
    ),
    kind=SourceKind.DERIVED,
    note=(
        "A geometric argument on the die, not a published limit. The die opening defaults "
        "to 8t, which is press-brake practice rather than a standard."
    ),
)

_HOLE_SOURCE: Final = Source(
    citation=(
        "Sheet-metal design practice: a hole within about 2t of a bend tangent distorts "
        "as the bend zone stretches"
    ),
    kind=SourceKind.DERIVED,
    note=(
        "Adopted shop practice, commonly written as 2.5t + r from hole centre to bend "
        "line. Overridable per part; not a standard."
    ),
)

_UNSTATED_K_SOURCE: Final = Source(
    citation="Kryova Decision 3: an unmeasured claim is never a pass",
    kind=SourceKind.DERIVED,
)


@dataclass(frozen=True)
class Finding:
    """One check against one thing, with the number, the limit and the source.

    `measured` and `limit` are `None` for an `UNMEASURED` finding, which is the
    point of it: there was no number to compare.
    """

    check: str
    subject: str
    outcome: Outcome
    message: str
    source: Source
    measured: float | None = None
    limit: float | None = None
    unit: str = "mm"

    @property
    def ok(self) -> bool:
        """True only for a pass. An unmeasured check is not one."""
        return self.outcome is Outcome.PASSED

    @property
    def margin(self) -> float | None:
        """How much room there is, in the finding's unit. Negative is a violation."""
        if self.measured is None or self.limit is None:
            return None
        return self.measured - self.limit

    def __str__(self) -> str:
        return f"[{self.outcome}] {self.check} on {self.subject}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "subject": self.subject,
            "outcome": str(self.outcome),
            "message": self.message,
            "measured": self.measured,
            "limit": self.limit,
            "margin": self.margin,
            "unit": self.unit,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True)
class FormabilityReport:
    """Every finding about one part, and the two questions they answer.

    `ok` is "nothing failed". `complete` is "nothing was left unmeasured".
    They are different questions and both are on the report, exactly as
    `app/rules/engine.py` keeps `ok` and `proven` apart: a part with no failures
    and three unmeasured checks has not been assessed, and a single boolean
    cannot say so.
    """

    part_name: str
    findings: tuple[Finding, ...]

    @property
    def failed(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.outcome is Outcome.FAILED)

    @property
    def unmeasured(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.outcome is Outcome.UNMEASURED)

    @property
    def passed(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.outcome is Outcome.PASSED)

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def complete(self) -> bool:
        return not self.unmeasured

    def summary(self) -> str:
        parts = [
            f"{self.part_name}: {len(self.passed)} passed, {len(self.failed)} failed, "
            f"{len(self.unmeasured)} unmeasured."
        ]
        parts.extend(f"  {finding}" for finding in self.findings if not finding.ok)
        if self.ok and not self.complete:
            parts.append(
                "  Nothing failed, but the part has not been fully assessed — an "
                "unmeasured check is not a pass."
            )
        return "\n".join(parts)

    def raise_for_findings(self, *, include_unmeasured: bool = False) -> None:
        """Refuse the part, naming every reason at once.

        The explicit refusal the module docstring describes. `include_unmeasured`
        turns "we could not check this" into a refusal too, which is the right
        setting for anything about to be cut.
        """
        problems = list(self.failed)
        if include_unmeasured:
            problems.extend(self.unmeasured)
        if not problems:
            return
        detail = "\n".join(f"  - {finding}" for finding in problems)
        raise FormabilityError(
            f"{self.part_name} cannot be made as declared:\n{detail}\n"
            f"Each line names the limit and the measured value; fix the geometry or "
            f"supply the process data the check is missing."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_name": self.part_name,
            "ok": self.ok,
            "complete": self.complete,
            "findings": [f.to_dict() for f in self.findings],
        }


def air_bend_die_opening_mm(thickness_mm: float, *, ratio: float = AIR_BEND_DIE_RATIO) -> float:
    """The V-die a shop would reach for, absent a stated one. See `AIR_BEND_DIE_RATIO`."""
    return ratio * thickness_mm


def minimum_flange_mm(
    *, thickness_mm: float, inside_radius_mm: float, die_opening_mm: float
) -> float:
    """`V/2 + r + t` — the shortest outside mould-line flange the die can hold."""
    return die_opening_mm / 2.0 + inside_radius_mm + thickness_mm


def _outside_setback_mm(bend: Bend, thickness_mm: float) -> float:
    return setback_mm(
        angle_deg=bend.angle_deg,
        inside_radius_mm=bend.inside_radius_mm,
        thickness_mm=thickness_mm,
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
    )


def _bends_on(flange: Flange, parent_joint: Joint | None) -> dict[Edge, Bend]:
    out: dict[Edge, Bend] = {}
    if parent_joint is not None:
        out[Edge.NEAR] = parent_joint.bend
    for joint in flange.joints:
        out[joint.edge] = joint.bend
    return out


def _check_bend_radius(part: SheetMetalPart, bend: Bend) -> Finding:
    material = part.material
    strain = material.outer_fibre_strain(
        inside_radius_mm=bend.inside_radius_mm, k=bend.k.value
    )
    limit = material.minimum_bend_radius_mm()
    if limit is None or material.minimum_bend_radius is None:
        return Finding(
            check="minimum bend radius",
            subject=bend.label,
            outcome=Outcome.UNMEASURED,
            message=(
                f"{material.name} has no minimum bend radius on record, so there is "
                f"nothing to compare R{bend.inside_radius_mm:g} against. The outside of "
                f"this bend stretches {strain * 100:.1f}%; give the material a "
                f"MinimumBendRadius with its source before anybody forms it."
            ),
            source=_UNSTATED_K_SOURCE,
        )
    record = material.minimum_bend_radius
    if bend.inside_radius_mm + 1e-9 < limit:
        return Finding(
            check="minimum bend radius",
            subject=bend.label,
            outcome=Outcome.FAILED,
            message=(
                f"R{bend.inside_radius_mm:g} mm is tighter than the {limit:.3f} mm "
                f"minimum for {material.name} ({record}); the outside of the bend would "
                f"stretch {strain * 100:.1f}% and the material cracks. Open the radius to "
                f"at least {limit:.3f} mm, or move to a grade or temper that takes it."
            ),
            source=record.source,
            measured=bend.inside_radius_mm,
            limit=limit,
        )
    return Finding(
        check="minimum bend radius",
        subject=bend.label,
        outcome=Outcome.PASSED,
        message=(
            f"R{bend.inside_radius_mm:g} mm against a {limit:.3f} mm minimum for "
            f"{material.name}; outer fibre strain {strain * 100:.1f}%."
        ),
        source=record.source,
        measured=bend.inside_radius_mm,
        limit=limit,
    )


def _check_flange_length(
    part: SheetMetalPart,
    flange: Flange,
    bends: dict[Edge, Bend],
    *,
    die_opening_mm: float,
    tangent_length_mm: float,
) -> Finding | None:
    """Whether the flange reaches past the die shoulder of the bends that form it."""
    if not bends:
        return None
    thickness = part.material.thickness_mm
    hems = [b.label for b in bends.values() if b.angle_deg >= STRAIGHT_ANGLE_DEG]
    if hems:
        return Finding(
            check="minimum flange length",
            subject=flange.name,
            outcome=Outcome.UNMEASURED,
            message=(
                f"{flange.name} is formed by {', '.join(hems)}, which is a hem: it has no "
                f"outside mould line, so the die-shoulder rule below cannot be stated in "
                f"the same terms. A hem is formed by flattening a smaller bend and needs "
                f"its own process check."
            ),
            source=_DIE_SHOULDER_SOURCE,
        )
    outside_length = tangent_length_mm + sum(
        _outside_setback_mm(bend, thickness)
        for edge, bend in bends.items()
        if edge in (Edge.NEAR, Edge.FAR)
    )
    limit = max(
        minimum_flange_mm(
            thickness_mm=thickness,
            inside_radius_mm=bend.inside_radius_mm,
            die_opening_mm=die_opening_mm,
        )
        for bend in bends.values()
    )
    if outside_length + 1e-9 < limit:
        widest_radius = max(bend.inside_radius_mm for bend in bends.values())
        die_that_would_hold = 2.0 * (outside_length - thickness - widest_radius)
        remedy = (
            f"a {die_that_would_hold:.3f} mm opening would hold it"
            if die_that_would_hold > 0.0
            else "no die opening is narrow enough — the flange has to grow"
        )
        return Finding(
            check="minimum flange length",
            subject=flange.name,
            outcome=Outcome.FAILED,
            message=(
                f"{flange.name} is {outside_length:.3f} mm to the outside mould line and "
                f"needs {limit:.3f} mm (V/2 + r + t on a {die_opening_mm:.3f} mm die). A "
                f"flange that does not reach the die shoulder is not held by anything and "
                f"drops into the die. Lengthen it, or form it on a narrower die: {remedy}."
            ),
            source=_DIE_SHOULDER_SOURCE,
            measured=outside_length,
            limit=limit,
        )
    return Finding(
        check="minimum flange length",
        subject=flange.name,
        outcome=Outcome.PASSED,
        message=(
            f"{outside_length:.3f} mm to the outside mould line against a {limit:.3f} mm "
            f"minimum on a {die_opening_mm:.3f} mm die."
        ),
        source=_DIE_SHOULDER_SOURCE,
        measured=outside_length,
        limit=limit,
    )


def _check_hole(
    part: SheetMetalPart,
    flange: Flange,
    hole: Hole,
    bends: dict[Edge, Bend],
    *,
    near_setback_mm: float,
    left_setback_mm: float,
    tangent_length_mm: float,
    tangent_width_mm: float,
    factor: float,
) -> list[Finding]:
    """One finding per bend on the hole's own face."""
    thickness = part.material.thickness_mm
    limit = factor * thickness
    radius = hole.diameter_mm / 2.0
    u = hole.u_mm - near_setback_mm
    v = hole.v_mm - left_setback_mm
    distances = {
        Edge.NEAR: u - radius,
        Edge.FAR: tangent_length_mm - u - radius,
        Edge.LEFT: v - radius,
        Edge.RIGHT: tangent_width_mm - v - radius,
    }
    out: list[Finding] = []
    for edge, bend in sorted(bends.items()):
        distance = distances[edge]
        subject = f"{flange.name}.{hole.name} to {bend.label}"
        if distance + 1e-9 < limit:
            out.append(
                Finding(
                    check="hole distance to bend",
                    subject=subject,
                    outcome=Outcome.FAILED,
                    message=(
                        f"The edge of {hole.name} is {distance:.3f} mm from the tangent "
                        f"line of {bend.label} and needs {limit:.3f} mm "
                        f"({factor:g}t). Material this close to a bend stretches with it, "
                        f"so the hole comes out oval and pulled toward the bend. Move it "
                        f"{limit - distance:.3f} mm away, or pierce it after forming."
                    ),
                    source=_HOLE_SOURCE,
                    measured=distance,
                    limit=limit,
                )
            )
        else:
            out.append(
                Finding(
                    check="hole distance to bend",
                    subject=subject,
                    outcome=Outcome.PASSED,
                    message=(
                        f"{distance:.3f} mm from the edge of {hole.name} to the tangent "
                        f"line of {bend.label}, against {limit:.3f} mm."
                    ),
                    source=_HOLE_SOURCE,
                    measured=distance,
                    limit=limit,
                )
            )
    return out


def _check_k_basis(part: SheetMetalPart) -> Finding:
    """Whether every K in the part came from somewhere a reviewer could look.

    Never `FAILED`: an assumed K does not make a part unformable, it makes the
    blank length unjustified. `UNMEASURED` is the exact statement — the flat
    length was computed from a number nobody has checked — and it keeps
    `FormabilityReport.complete` false until somebody does.
    """
    unstated = [bend.label for bend in part.bends if not bend.k.has_stated_basis]
    if not unstated:
        return Finding(
            check="K-factor basis",
            subject=part.name,
            outcome=Outcome.PASSED,
            message="Every bend's K-factor names a source.",
            source=_UNSTATED_K_SOURCE,
            unit="",
        )
    return Finding(
        check="K-factor basis",
        subject=part.name,
        outcome=Outcome.UNMEASURED,
        message=(
            "K has no stated basis on " + ", ".join(unstated) + ". The flat length is a "
            "linear function of K, so the blank those bends produce has not been "
            "justified — take K from a cited table or a test bend."
        ),
        source=_UNSTATED_K_SOURCE,
        unit="",
    )


def check_part(
    part: SheetMetalPart,
    *,
    die_opening_mm: float | None = None,
    hole_factor: float = HOLE_EDGE_TO_TANGENT_FACTOR,
) -> FormabilityReport:
    """Every formability check this package has, run over one part.

    `die_opening_mm` defaults to `AIR_BEND_DIE_RATIO * t`; pass the shop's real
    die when there is one. `hole_factor` is the multiple of thickness a hole's
    edge must keep from a bend tangent.

    Geometry is not re-derived here: the tangent extents and setbacks come from
    the same `unfold()` that produces the blank, so a part that cannot be
    flattened raises `UnfoldError` from this call too rather than being assessed
    on numbers that do not describe it.
    """
    if die_opening_mm is None:
        die_opening_mm = air_bend_die_opening_mm(part.material.thickness_mm)
    if not math.isfinite(die_opening_mm) or die_opening_mm <= 0.0:
        raise FormabilityError(
            f"A die opening of {die_opening_mm!r} mm is not a die. Give the V opening in "
            f"millimetres, or leave it out to use {AIR_BEND_DIE_RATIO:g}t."
        )
    pattern = unfold(part)
    findings: list[Finding] = [_check_k_basis(part)]
    for bend in part.bends:
        findings.append(_check_bend_radius(part, bend))
    convention = part.convention
    thickness = part.material.thickness_mm
    for flange, _parent, joint in part.walk():
        bends = _bends_on(flange, joint)
        face = pattern.face_named(flange.name)
        flange_finding = _check_flange_length(
            part,
            flange,
            bends,
            die_opening_mm=die_opening_mm,
            tangent_length_mm=face.tangent_length_mm,
        )
        if flange_finding is not None:
            findings.append(flange_finding)
        near = (
            bends[Edge.NEAR].setback_mm(thickness, convention) if Edge.NEAR in bends else 0.0
        )
        left = (
            bends[Edge.LEFT].setback_mm(thickness, convention) if Edge.LEFT in bends else 0.0
        )
        for hole in flange.holes:
            findings.extend(
                _check_hole(
                    part,
                    flange,
                    hole,
                    bends,
                    near_setback_mm=near,
                    left_setback_mm=left,
                    tangent_length_mm=face.tangent_length_mm,
                    tangent_width_mm=face.tangent_width_mm,
                    factor=hole_factor,
                )
            )
    return FormabilityReport(part_name=part.name, findings=tuple(findings))


__all__ = [
    "AIR_BEND_DIE_RATIO",
    "HOLE_EDGE_TO_TANGENT_FACTOR",
    "Finding",
    "FormabilityReport",
    "air_bend_die_opening_mm",
    "check_part",
    "minimum_flange_mm",
]
