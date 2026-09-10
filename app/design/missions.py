"""The mission ladder, as a suite that runs rather than a table that is read.

Master plan **5.4**. The ladder (Decision 5) is nine machines in tractability
order, each rung "a permanent regression test". Until this module it was a table
in a document: nothing executed it, so "M1 works" was a claim from the day
somebody last tried it by hand, and there was no moment at which M1 quietly
stopping would have been noticed.

**A rung that cannot be built yet is `PENDING`, and `PENDING` is never a pass.**
This is `assertions.Outcome.UNMEASURED` applied one level up, for the same
reason: a suite that silently skips what it could not attempt reports green on a
ladder nobody climbed. Eight of the nine rungs need capability that does not
exist yet, so each of them is *declared* here with `needs` naming the phase that
owns the gap. They are counted in every summary. The one sentence a human reads
never says "the ladder passed" — it says which rungs passed and how many are not
yet reachable, because those are different facts and only one of them is good
news.

**But a pending rung does not make the report red**, and that is deliberate. A
suite that is red for the two years it takes to reach M9 is a suite people
disable, and a disabled suite protects nothing. So `ok` answers "did everything
that claimed to build still build", which is the regression question, and
`complete` answers "is the ladder finished", which is the programme question.
`complete` is False today and becomes True at the end of E18. Reading `ok`
without `summary()` beside it cannot mislead, because `ok` does not claim
coverage.

**A rung that claims to build and then does not is a failure, whatever the
reason.** `conformance.py` separates a coverage gap from a real stop, and is
right to — it is asking which of two backends is behind. Here the question is
different: the mission *declared* it builds. If an operation it needs has
regressed into unimplemented, the declaration is now false, and reporting that
as coverage would let a rung rot while the suite stayed green. So there is one
failure kind and the message carries the reason.

**The runner is injected and this module imports no kernel.** Same rule as
`execute.py` and `machine_checks.py`, and the same reason: reaching for
`app.kernel` here would pull ~166 MB of OCP into every test that touches the
design package. A mission is a specification plus its claims; building one is
somebody else's job, handed in as a callable.

**Each mission gets its own runner**, from a factory rather than one shared
instance — and from M2 on, *each part of a mission* gets its own too. A mission
that passed because a previous mission left a body behind is the exact false
green a regression suite exists to prevent, and it would be invisible — the
volumes would simply be right for the wrong reason. The same argument applies one
level down inside an assembly: `OcctRunner`'s own docstring says "one runner per
part", and two members sharing a document is a frame whose mass is right because
the same tube was weighed twice.

**A rung above M1 is an assembly, and an assembly is not a bigger part.** M2 is
the first, so `Mission` carries either a `spec` (one part) or an `assembly` (an
`AssemblyDesign`: a product graph, a design per component, and the interface
contracts where they meet). Everything the assembly half does is
`app.assembly`'s: the walk, the clash check, the mass roll-up and the contract
verdicts all come from there, and this module only decides what to ask and how to
report it. What it adds is the *combined measurement payload* — one mapping the
existing assertion vocabulary can read, built from the roll-up, the clash report,
the world envelope and each component's own measurement. `app.assembly` has no
such function today; the reasons that matters are recorded on `_combined_payload`.

**A rung that builds is not therefore a rung that is finished.** M2's own column
in the master plan's ladder says what is hard about it — weld sizing and fatigue
at the joints — and none of that is checked by geometry. So a mission may carry
`unproven`: caveats naming what it does *not* claim and the phase that owns each.
`needs` still means "cannot be built at all"; `unproven` means "builds, is checked
as far as it goes, and here is the part nobody has verified". Both are printed in
every summary, and `LadderReport.complete` is false while either exists —
otherwise "M2 passes" would quietly come to mean "the welds are sized", which is
the `UNMEASURED`-is-not-`PASSED` rule applied to a whole rung.

**A rung may also be a folded sheet, which is a third kind of thing again.** M3 is
the first, so `Mission` carries a `spec` (one machined part), an `assembly` (a
product), or a `folded` (`FoldedDesign`: one solid *and* the same part declared as
a fold tree, plus the stock it is cut from). The reason it is a third case rather
than a `spec` with extra claims is that a sheet-metal part has **two descriptions
that must agree** — the solid the kernel builds and the blank the press brake
cuts — and nothing in `app/sheetmetal/` or `app/kernel/` connects them. There is
no sheet-metal operation in the OCCT backend and no way to compile a
`SheetMetalPart` into a `DesignSpec`, so M3 holds both and `_folded_payload`
publishes the one number that ties them together: `flat.volume_mismatch_mm3`, the
difference between the solid's measured volume and the volume the flat pattern
accounts for. That residual is where a fold model and a drawn cross-section
drifting apart shows up, and it is the closest thing to a proof that the blank on
the drawing makes the part in the picture.

`app.sheetmetal` is imported here and `app.kernel` still is not. The distinction
is the same one `execute.py` keeps: a flat pattern is arithmetic that runs offline
in milliseconds, where a kernel is 166 MB of OCP. Nothing in the sheet-metal
package touches geometry, a solver or a database.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import product as _cross
from typing import Any, Final

from app.assembly.clash import (
    ClashReport,
    IgnoreRule,
    find_clashes,
    occt_bounds,
    occt_measurer,
)
from app.assembly.contracts import ContractResult, Interface, Violation, measurements
from app.assembly.contracts import check as check_contract
from app.assembly.mass import MassRollup, roll_up
from app.assembly.placement import Box, at, compose, turned
from app.assembly.structure import ProductStructure, StructureBuilder
from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.design.compile import compile_spec
from app.design.errors import SpecError
from app.design.execute import BuildReport, CallRunner, execute_plan
from app.design.params import Parameter, ParameterSet, Unit
from app.design.spec import DesignSpec, FeatureSpec, expr, ref
from app.sheetmetal import (
    HOLE_EDGE_TO_TANGENT_FACTOR,
    Bend,
    BendDirection,
    Edge,
    Flange,
    FlatPattern,
    FormabilityReport,
    Hole,
    Joint,
    KFactor,
    LengthConvention,
    SheetMetalError,
    SheetMetalPart,
    check_part,
    din6935,
    machinerys_handbook,
    sheet_material,
    unfold,
)


class MissionOutcome(StrEnum):
    """How one rung of the ladder came out.

    `PENDING` is not a skip and not a soft pass. It means the rung was never
    attempted because the capability it needs does not exist yet, and the
    programme has not climbed it.
    """

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


#: Top-level keys the combined assembly payload publishes itself. A component whose
#: name collided with one of these would have its own measurement silently shadow the
#: assembly's — a frame whose `mass_kg` was one member's — so the collision is refused
#: at declaration rather than found in a number that looks plausible.
_RESERVED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "provenance",
        "clash",
        "envelope_mm",
        "mass_kg",
        "centre_of_mass_mm",
        "occurrence_count",
        "weighed_occurrence_count",
        "unmeasured_occurrence_count",
        "component_count",
        "complete",
    }
)

#: `app.kernel.provenance`'s vocabulary, spelled rather than imported. Same reason
#: `app.assembly.contracts.PROVENANCE_KEY` and `app.assembly.mass.MASS_KG` are spelled:
#: importing `app.kernel` executes its `__init__` and pulls ~166 MB of OCP into a module
#: whose load-bearing property is that it holds no kernel. `tests/test_mission_m2.py`
#: asserts these are the same strings the kernel uses, so a rename there cannot orphan
#: them quietly.
_PROVENANCE_KEY: Final = "provenance"
_BASIS_MEASURED: Final = "measured"
_BASIS_APPROXIMATED: Final = "approximated"
_BASIS_UNAVAILABLE: Final = "unavailable"

#: `app.kernel.interrogation`'s spellings for what is measured between two bodies,
#: spelled here for the same reason, and pinned to the kernel's by the same test. An
#: assertion written against a two-body clearance check reads a joint unchanged.
_MINIMUM_CLEARANCE_MM: Final = "minimum_clearance_mm"
_INTERFERENCE_VOLUME_MM3: Final = "interference_volume_mm3"

#: Not one of the kernel's: it is a number about a *set* of pairs. See
#: `_boundary_payload` for why the minimum alone cannot carry a fit-up claim.
_WIDEST_GAP_MM: Final = "widest_gap_mm"


@dataclass(frozen=True)
class AssemblyDesign:
    """A rung that is more than one part: the graph, the parts, and the joints.

    The counterpart of `DesignSpec` one level up, and it holds no geometry and no
    kernel — `structure` is `app.assembly`'s product graph, `parts` maps each leaf
    component to the design that builds it, and `interfaces` are the contracts at
    the boundaries. Building it is `run_mission`'s job and measuring it is
    `app.assembly`'s.

    **Every leaf must have a design.** A leaf component with no spec cannot be
    built, so it cannot be weighed and its clashes cannot be measured — and the
    two numbers that come out of that are a *lighter* machine and a *roomier* one,
    which is the direction every budget passes. It is refused here rather than
    reported as a gap because at declaration time it is a mistake, not a finding.
    """

    structure: ProductStructure

    #: Component name -> the design that builds it, in build order.
    parts: Mapping[str, DesignSpec] = field(default_factory=dict)

    #: The contracts at the boundaries where two components meet.
    interfaces: tuple[Interface, ...] = ()

    #: Frame-level numbers the mission's own assertions may read as `"=span_mm"`.
    #: Not the parts' parameters: those live in each `DesignSpec`, and the numbers
    #: *both* sides build from live in an `Interface` (14.3) and are bound in.
    parameters: ParameterSet = field(default_factory=ParameterSet)

    #: How far apart two occurrences may be and still be measured. Zero asks only
    #: "do they touch"; a welded frame needs more, because a joint that has opened
    #: by 10 mm is exactly the pair a contact-only check rejects as "safely apart".
    clearance_mm: float = 0.0

    #: Pairs to leave out, with the reason. Note what is *not* wanted here: the
    #: members that meet at a joint. `touching_components` would exclude precisely
    #: the pairs whose contact is the thing being verified.
    ignore: IgnoreRule | None = None

    def __post_init__(self) -> None:
        leaves = {occ.component for occ in self.structure.occurrences(leaves_only=True)}
        undesigned = sorted(leaves - set(self.parts))
        if undesigned:
            raise SpecError(
                f"{', '.join(undesigned)} appear(s) in the product structure with no "
                "design to build it. A component nobody built is not a component with "
                "no mass and no clashes — it is a hole in both, and both holes read as "
                "a pass."
            )
        stray = sorted(set(self.parts) - leaves)
        if stray:
            raise SpecError(
                f"{', '.join(stray)} has a design but is not a leaf of the product "
                f"structure rooted at {self.structure.root!r}. Add it to the structure, "
                "or drop the design — a part that is built and never placed is weighed "
                "by nothing and drawn on no drawing."
            )
        collisions = sorted(set(self.parts) & _RESERVED_PAYLOAD_KEYS)
        if collisions:
            raise SpecError(
                f"Component(s) {', '.join(collisions)} share a name with a key the "
                "assembly's own measurement publishes. Rename the component: otherwise "
                "an assertion on the assembly would read one member's number and pass."
            )

    @property
    def components(self) -> tuple[str, ...]:
        """The components with designs, in the order they will be built."""
        return tuple(self.parts)


@dataclass(frozen=True)
class AssemblyReport:
    """What building and measuring a product actually found.

    Everything here is somebody else's data type: `app.design`'s build reports,
    `app.assembly`'s clash report, mass roll-up and contract results. What this adds
    is that they arrived together, and `payload` — the one mapping the assertion
    vocabulary reads.
    """

    builds: tuple[tuple[str, BuildReport], ...] = ()
    clash: ClashReport | None = None
    mass: MassRollup | None = None
    contracts: tuple[ContractResult, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Did every part build, and does every contract hold?

        Says nothing about the mission's own assertions — those are checked against
        `payload` by `run_mission` and reported separately, because a violated
        interface and a missed dimension send different people to look.
        """
        return all(report.ok for _, report in self.builds) and all(
            result.ok for result in self.contracts
        )

    @property
    def violations(self) -> tuple[Violation, ...]:
        """Every interface claim that failed or could not be measured, both parties named."""
        return tuple(v for result in self.contracts for v in result.violations)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "builds": {name: report.to_dict() for name, report in self.builds},
            "contracts": [result.to_dict() for result in self.contracts],
            "measurements": dict(self.payload),
        }
        if self.clash is not None:
            out["clash"] = self.clash.summary()
        if self.mass is not None:
            out["mass"] = self.mass.summary()
        return out


@dataclass(frozen=True)
class StockSheet:
    """The sheet a blank is cut from, and which way its grain runs.

    **`app.sheetmetal` has no stock and no nest.** `FlatPattern` reports an extent
    and an area; nothing in the package knows what sheet the blank is cut from, how
    many fit, or how much is thrown away. So this lives here, and the arithmetic in
    `_nest` lives here with it — named as a gap rather than presented as a feature of
    the package, because a cost or a lead-time claim needs it and will need it from
    somewhere better than a mission module.

    **The rolling direction is a declaration, not a check**, and that is the sharper
    half. Every minimum bend radius `material.py` ships carries the same caveat —
    "for a bend **across the grain**" — and nothing in a `SheetMaterial`, a `Bend` or
    a `FlatPattern` can express which way the grain runs. So the nest below is
    computed in **one orientation only**: the blank's bend lines are laid across the
    sheet's length. Taking the better of two orientations would find more parts per
    sheet by rotating the blank a quarter turn, which puts every bend along the
    rolling direction and quietly voids the one material property the formability
    check is gated on.
    """

    name: str

    #: Along the rolling direction. Bend lines are laid across this.
    length_mm: float

    width_mm: float

    def __post_init__(self) -> None:
        for label, value in (("length_mm", self.length_mm), ("width_mm", self.width_mm)):
            if not math.isfinite(value) or value <= 0.0:
                raise SpecError(
                    f"Stock sheet {self.name!r} has {label}={value!r}, which is not a "
                    "sheet. Give the sheet size in millimetres; the length is the "
                    "rolling direction."
                )

    @property
    def area_mm2(self) -> float:
        return self.length_mm * self.width_mm


@dataclass(frozen=True)
class NestReport:
    """How many blanks come off one sheet, in the one orientation the grain allows."""

    sheet: StockSheet
    across: int
    down: int
    utilisation: float
    blank_area_mm2: float

    @property
    def parts_per_sheet(self) -> int:
        return self.across * self.down

    @property
    def fits(self) -> bool:
        return self.parts_per_sheet > 0

    def summary(self) -> str:
        if not self.fits:
            return (
                f"The blank does not fit {self.sheet.name} in the orientation the grain "
                f"allows: {self.across} across by {self.down} down."
            )
        return (
            f"{self.parts_per_sheet} per {self.sheet.name} "
            f"({self.across} across x {self.down} down), "
            f"{self.utilisation * 100:.1f}% of the sheet used."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet.name,
            "sheet_mm": [self.sheet.length_mm, self.sheet.width_mm],
            "across": self.across,
            "down": self.down,
            "parts_per_sheet": self.parts_per_sheet,
            "utilisation": self.utilisation,
            "blank_area_mm2": self.blank_area_mm2,
        }


@dataclass(frozen=True)
class FoldedDesign:
    """A rung that is a folded sheet: the solid, the same part as a fold tree, the stock.

    **Two descriptions of one part, held together by arithmetic.** `spec` is what the
    kernel builds — for M3, the folded cross-section drawn segment by segment and
    extruded — and `part` is the same object declared as flanges and bends, which is
    what unfolds and what a press brake is told about. Nothing connects them: the
    OCCT backend has no sheet-metal operation, and `SheetMetalPart` cannot be compiled
    into a `DesignSpec`. So both are declared, from one set of dimensions, and
    `_folded_payload` publishes the residual between what the solid weighs and what
    the blank accounts for. Until a sheet-metal feature exists in the design IR that
    residual is the only thing standing between "the drawing and the part agree" and
    "somebody typed the same numbers twice".

    A part with no bends is refused. Flat sheet is a real thing to make and it is not
    what this rung is for: everything M3 exists to check — the allowance, the setback,
    the K-factor's basis, the blank that is shorter than the sum of its legs — is a
    property of a bend, and a rung that quietly went flat would keep reporting green
    while checking none of it.
    """

    spec: DesignSpec
    part: SheetMetalPart
    stock: StockSheet | None = None

    #: The V-die the flanges are formed on. `None` takes `app.sheetmetal`'s own
    #: `AIR_BEND_DIE_RATIO * t`, which is press-brake practice rather than a standard.
    die_opening_mm: float | None = None

    #: How far a hole's edge must keep from a bend tangent, as a multiple of thickness.
    hole_factor: float = HOLE_EDGE_TO_TANGENT_FACTOR

    def __post_init__(self) -> None:
        if not self.part.bends:
            raise SpecError(
                f"{self.part.name!r} is declared as a folded rung and has no bends. "
                "Every claim this rung makes — bend allowance, setback, the K-factor's "
                "basis, the blank being shorter than the sum of its legs — is a "
                "property of a bend. A flat blank would report green having checked "
                "none of them."
            )


@dataclass(frozen=True)
class FoldedReport:
    """What building, flattening and assessing a folded part found.

    Everything here is somebody else's data type — `app.design`'s build report,
    `app.sheetmetal`'s flat pattern and formability report — except `nest`, which
    exists because that package has none, and `payload`, the one mapping the
    assertion vocabulary reads.
    """

    build: BuildReport | None = None
    pattern: FlatPattern | None = None
    formability: FormabilityReport | None = None
    nest: NestReport | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Did the part build, flatten, and come back formable?

        Says nothing about the mission's own assertions, for the reason
        `AssemblyReport.ok` gives: a part that cannot be pressed and a part that came
        out the wrong size send different people to look.
        """
        return (
            self.build is not None
            and self.build.ok
            and self.pattern is not None
            and self.formability is not None
            and self.formability.ok
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"measurements": dict(self.payload)}
        if self.build is not None:
            out["build"] = self.build.to_dict()
        if self.pattern is not None:
            out["flat_pattern"] = self.pattern.to_dict()
        if self.formability is not None:
            out["formability"] = self.formability.to_dict()
        if self.nest is not None:
            out["nest"] = self.nest.to_dict()
        return out


@dataclass(frozen=True)
class Mission:
    """One rung: a machine, what makes it hard, and either a design or a reason.

    A rung is buildable exactly when it carries a `spec`, an `assembly` **or** a
    `folded` — one part, a product graph of them, or a folded sheet. The three
    states are kept apart from "waiting" by validation rather than by convention,
    because the failure they guard against is a rung drifting into "declared but
    claiming nothing", which reads as coverage and is not.
    """

    rung: str
    title: str
    era: str

    #: What makes this machine hard — the master plan's own ladder column, kept
    #: with the rung so a reader of a failing report knows what it was testing.
    hard: str

    #: The design, when the rung is one part. `None` for an assembly rung and for
    #: a rung that is not yet reachable.
    spec: DesignSpec | None = None

    #: The product, when the rung is more than one part. Mutually exclusive with
    #: `spec`: a rung is a part or a product, and a mission carrying both would
    #: have two answers to "what did it build".
    assembly: AssemblyDesign | None = None

    #: The folded sheet, when the rung is one. Mutually exclusive with both of the
    #: above and for the same reason — and note that a `FoldedDesign` carries a
    #: `DesignSpec` of its own, so a rung setting `spec` *and* `folded` would build
    #: two different parts and check one set of claims against whichever ran last.
    folded: FoldedDesign | None = None

    #: What must be true of the built part. Checked by `assertions.py` against the
    #: measurement payload the build reports — for an assembly, against the
    #: combined payload `_combined_payload` assembles.
    assertions: tuple[Assertion, ...] = ()

    #: Capability this rung waits on, each naming the phase that owns it. Required
    #: when there is no design, forbidden when there is — a rung cannot be both
    #: buildable and waiting, and allowing both would hide a half-built mission.
    needs: tuple[str, ...] = ()

    #: What this rung does **not** claim, each naming the phase that owns it. The
    #: honest half of a rung that builds: M2's frame is geometry, and its welds are
    #: not sized, so "M2 passed" must never be readable as "the welds are sized".
    #: Forbidden on a pending rung — a rung that was never built has `needs`, and
    #: carrying both would blur "not attempted" into "attempted in part".
    unproven: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rung.startswith("M") or not self.rung[1:].isdigit():
            raise SpecError(
                f"A rung is named M1..M9; got {self.rung!r}. The name is how the "
                "ladder in the master plan and this suite are kept in step."
            )
        declared = [
            label
            for label, value in (
                ("a part design", self.spec),
                ("an assembly", self.assembly),
                ("a folded sheet", self.folded),
            )
            if value is not None
        ]
        if len(declared) > 1:
            # "both" when there are two, which is the only way it can read as English
            # and is what the message has always said.
            listed = (
                f"both {declared[0]} and {declared[1]}"
                if len(declared) == 2
                else ", ".join(declared[:-1]) + f" and {declared[-1]}"
            )
            raise SpecError(
                f"{self.rung} carries {listed}. A rung builds one thing; carrying more "
                "than one leaves 'what did it build' with two answers and the "
                "assertions checked against only one of them."
            )
        if not self.buildable:
            if not self.needs:
                raise SpecError(
                    f"{self.rung} has no design and no `needs`. A rung that cannot be "
                    "built must say what it is waiting for, or it reads as finished "
                    "work nobody ran."
                )
            if self.assertions:
                raise SpecError(
                    f"{self.rung} declares assertions but no design to check them "
                    "against. Give it a spec or move the claims to the rung that builds."
                )
            if self.unproven:
                raise SpecError(
                    f"{self.rung} is not buildable but lists what it leaves unproven. "
                    "Nothing about it is proven; those belong in `needs`, which is what "
                    "'we have not climbed this rung at all' is spelled."
                )
        else:
            if self.needs:
                raise SpecError(
                    f"{self.rung} has both a design and `needs`. A rung is buildable or "
                    "it is waiting; carrying both lets a half-built mission report as "
                    "either one."
                )
            if not self.assertions:
                raise SpecError(
                    f"{self.rung} builds but claims nothing. A mission with no "
                    "assertions is a build, not a regression test — it stays green "
                    "while the geometry changes underneath it."
                )

    @property
    def buildable(self) -> bool:
        return (
            self.spec is not None or self.assembly is not None or self.folded is not None
        )

    @property
    def is_assembly(self) -> bool:
        return self.assembly is not None

    @property
    def is_folded(self) -> bool:
        return self.folded is not None

    def __str__(self) -> str:
        return f"{self.rung} — {self.title}"


@dataclass(frozen=True)
class MissionResult:
    """What running one rung found."""

    mission: Mission
    outcome: MissionOutcome

    #: The one build, for a single-part rung. `None` for an assembly rung, whose
    #: several builds are in `assembly.builds` — including the one that failed, so
    #: there is never a "which build is this?" to answer.
    build: BuildReport | None = None
    checks: AssertionReport | None = None

    #: Everything an assembly rung produced: the per-component builds, the clash
    #: report, the mass roll-up, the contract verdicts and the combined payload.
    assembly: AssemblyReport | None = None

    #: Everything a folded rung produced: the flat pattern, the formability findings,
    #: the nest and the combined payload. The solid's own build is on `build`, where a
    #: single-part rung's always is — a folded rung builds exactly one part, so putting
    #: it anywhere else would give "which build is this?" a second answer.
    folded: FoldedReport | None = None

    #: Why, in words, for anything that is not a plain pass.
    reason: str = ""

    @property
    def rung(self) -> str:
        return self.mission.rung

    @property
    def passed(self) -> bool:
        return self.outcome is MissionOutcome.PASSED

    def __bool__(self) -> bool:
        return self.passed

    def __str__(self) -> str:
        head = f"{self.mission}: {self.outcome.value}"
        if self.reason:
            head = f"{head} — {self.reason}"
        if self.passed and self.mission.unproven:
            # Printed on a *pass*, deliberately. This is the only line between
            # "M2 passed" and a reader concluding the welds have been sized.
            head += "\n  not claimed: " + "; ".join(self.mission.unproven)
        return head

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rung": self.mission.rung,
            "title": self.mission.title,
            "era": self.mission.era,
            "outcome": self.outcome.value,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.mission.needs:
            out["needs"] = list(self.mission.needs)
        if self.mission.unproven:
            out["unproven"] = list(self.mission.unproven)
        if self.build is not None:
            out["build"] = self.build.to_dict()
        if self.assembly is not None:
            out["assembly"] = self.assembly.to_dict()
        if self.folded is not None:
            out["folded"] = self.folded.to_dict()
        if self.checks is not None:
            out["assertions"] = self.checks.to_dict()
        return out


@dataclass(frozen=True)
class LadderReport:
    """Every rung, run at once.

    `ok` is the regression question and `complete` is the programme question. They
    are separate properties because collapsing them gives you either a suite that
    is red until M9 lands, or a suite whose green means less than a reader thinks.
    """

    results: tuple[MissionResult, ...] = ()

    @property
    def passed(self) -> tuple[MissionResult, ...]:
        return tuple(r for r in self.results if r.outcome is MissionOutcome.PASSED)

    @property
    def failed(self) -> tuple[MissionResult, ...]:
        return tuple(r for r in self.results if r.outcome is MissionOutcome.FAILED)

    @property
    def pending(self) -> tuple[MissionResult, ...]:
        return tuple(r for r in self.results if r.outcome is MissionOutcome.PENDING)

    @property
    def ok(self) -> bool:
        """Did everything that claimed to build still build and still hold?

        Says nothing about the rungs that are not yet reachable. `summary()`
        always states those, so this cannot be read as coverage on its own.
        """
        return bool(self.results) and not self.failed

    @property
    def caveated(self) -> tuple[MissionResult, ...]:
        """Rungs that passed but do not claim everything their row of the plan does.

        A pass with a caveat is still a pass — the geometry it checked is checked —
        so these are not failures. They are the difference between "the frame builds
        and every number holds" and "M2 is done", and only one of those is what the
        ladder is for.
        """
        return tuple(r for r in self.passed if r.mission.unproven)

    @property
    def complete(self) -> bool:
        """Is the ladder actually climbed?

        True only when nothing failed, nothing is pending, **and** nothing that passed
        did so with a caveat. A rung that builds without the analysis its row of the
        plan calls for has been climbed part of the way, and `complete` is the one
        property that must not blur that.
        """
        return (
            bool(self.results)
            and not self.failed
            and not self.pending
            and not self.caveated
        )

    def __bool__(self) -> bool:
        return self.ok

    def __iter__(self) -> Iterator[MissionResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        if not self.results:
            return "No missions to run."
        bits = [f"{len(self.passed)}/{len(self.results)} rungs pass"]
        if self.failed:
            bits.append(f"{len(self.failed)} REGRESSED")
        if self.pending:
            bits.append(f"{len(self.pending)} not yet buildable")
        if self.caveated:
            bits.append(f"{len(self.caveated)} pass with what they do not claim named")
        line = ", ".join(bits) + "."
        detail = [str(r) for r in self.results if r.outcome is not MissionOutcome.PASSED]
        detail.extend(str(r) for r in self.caveated)
        return line + ("\n" + "\n".join(detail) if detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "complete": self.complete,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pending": len(self.pending),
            "caveated": len(self.caveated),
            "results": [result.to_dict() for result in self.results],
        }


def run_mission(
    mission: Mission,
    runner: CallRunner | None = None,
    *,
    runner_factory: Callable[[], CallRunner] | None = None,
) -> MissionResult:
    """Build one rung and check what it claims about itself.

    Never raises for a build or assertion outcome — a rung that does not build is
    a finding, and a suite that raised would stop at the first one and never tell
    you about the other eight.

    A single-part rung takes either one `runner` or a `runner_factory` called once,
    and a folded rung is a single-part rung — it builds one solid, and its flat
    pattern is arithmetic that needs no runner at all.
    **An assembly rung requires the factory**, because its parts each need their own
    document — `OcctRunner`'s own contract is one runner per part. Sharing one would
    not fail loudly, which is the problem: `catia_new_part` *replaces* the document,
    so the second member silently discards the first, and the operation journal a
    parametric rebuild replays would carry one member's calls into the next. Handing
    an assembly rung a bare runner raises, because it is a caller's mistake rather
    than a result: reporting it as a failed rung would send somebody to look at
    geometry that was never built.
    """
    if runner is None and runner_factory is None and mission.buildable:
        raise SpecError(
            f"{mission.rung} needs something to build with: pass a runner, or a "
            "runner_factory for an assembly rung."
        )
    if not mission.buildable:
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.PENDING,
            reason="not yet buildable — waiting on " + "; ".join(mission.needs),
        )

    if mission.assembly is not None:
        if runner_factory is None:
            raise SpecError(
                f"{mission.rung} is an assembly of "
                f"{len(mission.assembly.parts)} parts and was given one runner. Pass "
                "runner_factory= instead: each part is built in its own document, and "
                "sharing one would build the second member inside the first."
            )
        return _run_assembly(mission, mission.assembly, runner_factory)

    if runner is None:
        assert runner_factory is not None  # noqa: S101 - guarded above
        runner = runner_factory()

    if mission.folded is not None:
        return _run_folded(mission, mission.folded, runner)

    assert mission.spec is not None  # noqa: S101 - buildable and not an assembly
    plan = compile_spec(mission.spec)
    build = execute_plan(plan, runner)
    if not build.ok:
        # The rung declared it builds. Whatever stopped it — a refusal, an
        # operation that regressed into unimplemented, a crash — the declaration
        # is now false, and that is one kind of failure with one recovery: look.
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            build=build,
            reason=f"the build stopped: {build.failure}",
        )

    # The plan already carries the resolved parameters, so a bound written as a
    # formula over the design ("=target_mass_kg") is evaluated against the same
    # values the geometry was built from rather than a second resolution of them.
    checks = check_assertions(
        mission.assertions, build.last_result(), parameters=plan.parameters
    )
    if checks.ok:
        return MissionResult(
            mission=mission, outcome=MissionOutcome.PASSED, build=build, checks=checks
        )
    return MissionResult(
        mission=mission,
        outcome=MissionOutcome.FAILED,
        build=build,
        checks=checks,
        reason=checks.summary(),
    )


# ---------------------------------------------------------------------------
# Assembly rungs
# ---------------------------------------------------------------------------


def _run_assembly(
    mission: Mission,
    design: AssemblyDesign,
    runner_factory: Callable[[], CallRunner],
) -> MissionResult:
    """Build every part, walk the product, and check what the rung claims.

    The order is the order the answers depend on each other: build, then weigh and
    clash-check what was built, then check the contracts at the boundaries, then the
    mission's own claims against everything at once. A part that did not build stops
    it at the first step, because a clash check over two members and a hole is a
    check of a different machine.
    """
    builds: list[tuple[str, BuildReport]] = []
    payloads: dict[str, Mapping[str, Any]] = {}
    shapes: dict[str, Any] = {}

    for component in design.components:
        spec = design.parts[component]
        runner = runner_factory()
        report = execute_plan(compile_spec(spec), runner)
        builds.append((component, report))
        if not report.ok:
            return MissionResult(
                mission=mission,
                outcome=MissionOutcome.FAILED,
                assembly=AssemblyReport(builds=tuple(builds)),
                reason=f"{component} did not build: {report.failure}",
            )
        payloads[component] = report.last_result()
        shape = _shape_of(runner)
        if shape is not None:
            shapes[component] = shape

    # The measurer reads what the build already reported rather than
    # `app.assembly.mass.from_document`. Deliberate: every backend's mutating calls
    # return mass and centre of mass, and `from_document` reads an OCCT
    # `PartDocument` — using it would make the mass of an assembly unobtainable on a
    # CATIA seat, for a number the seat has already sent back.
    mass = roll_up(design.structure, lambda component: payloads[component])

    # The narrow phase does need real shapes, and a component with none lands its
    # pairs in `unchecked` with the reason — which makes the report incomplete, which
    # publishes the minimum clearance as UNAVAILABLE, which makes the claim on it
    # UNMEASURED. Four layers, each already honest; nothing here has to add a check.
    clash = find_clashes(
        design.structure,
        occt_measurer(shapes),
        occt_bounds(shapes),
        ignore=design.ignore,
        clearance_mm=design.clearance_mm,
    )

    payload = _combined_payload(design, payloads, mass, clash, shapes)
    contracts = tuple(
        check_contract(
            interface,
            measurements(
                provider=_party_payload(design, payloads, interface.provider),
                consumer=_party_payload(design, payloads, interface.consumer),
                boundary=_boundary_payload(clash, design, interface),
            ),
        )
        for interface in design.interfaces
    )
    built = AssemblyReport(
        builds=tuple(builds),
        clash=clash,
        mass=mass,
        contracts=contracts,
        payload=payload,
    )

    parameters = design.parameters.resolve() if len(design.parameters) else None
    checks = check_assertions(mission.assertions, payload, parameters=parameters)

    problems = [str(violation) for result in contracts for violation in result.violations]
    if problems or not checks.ok:
        reasons = []
        if not checks.ok:
            reasons.append(checks.summary())
        reasons.extend(problems)
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            assembly=built,
            checks=checks,
            reason="\n".join(reasons),
        )
    return MissionResult(
        mission=mission,
        outcome=MissionOutcome.PASSED,
        assembly=built,
        checks=checks,
    )


def _shape_of(runner: CallRunner) -> Any:
    """The geometry a runner has just built, if it is holding any.

    Duck-typed on purpose, and it is the one place this module looks at a backend at
    all. `OcctRunner.document.shape` is a `TopoDS_Shape`; a CATIA-backed runner has
    no such attribute and never will, because its geometry is on the workstation. So
    this returns None there, and the clash check reports every pair as unchecked with
    the reason rather than pretending distance is unmeasurable-therefore-fine.
    """
    document = getattr(runner, "document", None)
    return None if document is None else getattr(document, "shape", None)


def _combined_payload(
    design: AssemblyDesign,
    payloads: Mapping[str, Mapping[str, Any]],
    mass: MassRollup,
    clash: ClashReport,
    shapes: Mapping[str, Any],
) -> dict[str, Any]:
    """One measurement payload for the whole product, from four sources.

    **`app.assembly` has no function that does this, and that is a real gap.**
    `MassRollup.to_payload` and `ClashReport.to_payload` are each a payload, and they
    collide: both publish `occurrence_count` and `complete`, so merging them naively
    silently drops one machine-wide number for another. `contracts.measurements()` is
    the package's own merging function and it offers exactly three fixed prefixes —
    provider, consumer, boundary — which is the shape of a two-party interface and not
    of a product. So the merge is here, with the collisions decided explicitly:

    * **the mass roll-up sits at the top level**, unprefixed, because `mass_kg` and
      `centre_of_mass_mm` are `app.kernel.measurement`'s own spellings and a mass
      budget written against one part must read an assembly unchanged. That property
      is the reason `MassRollup.to_payload` exists at all;
    * **the clash report goes under `clash.`**, because it is a report about a *check*
      — how many pairs, how many looked at — and its `occurrence_count` answers a
      different question from the roll-up's;
    * **each component goes under its own name**, so `post.volume_mm3` is a claim about
      a member and `mass_kg` is a claim about the frame;
    * **the world envelope is computed here**, because nothing in `app.assembly`
      produces one: `Box.union` and `Box.transformed` are public and `clash._world_boxes`
      is not, so the walk that turns a product into its bounding box exists only inside
      the clash check's private half.

    Every provenance sidecar is re-keyed with its own prefix, which is the rule
    `contracts.namespace` documents: leave a sidecar keyed on the bare path and an
    approximated number is read as measured one level up.
    """
    payload: dict[str, Any] = dict(mass.to_payload())
    sidecar: dict[str, Any] = dict(payload.pop(_PROVENANCE_KEY, {}) or {})

    def merge(chunk: Mapping[str, Any]) -> None:
        for key, value in chunk.items():
            if key == _PROVENANCE_KEY:
                sidecar.update(value)
                continue
            payload[key] = value

    merge(_namespaced(clash.to_payload(), "clash"))
    for component in design.components:
        merge(_namespaced(payloads[component], component))
    merge(_envelope(design, shapes))

    if sidecar:
        payload[_PROVENANCE_KEY] = sidecar
    return payload


def _namespaced(chunk: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    """`contracts.namespace` under a name of our choosing rather than its three."""
    body = {key: value for key, value in chunk.items() if key != _PROVENANCE_KEY}
    out: dict[str, Any] = {prefix: body}
    inner = chunk.get(_PROVENANCE_KEY)
    if isinstance(inner, Mapping):
        out[_PROVENANCE_KEY] = {
            f"{prefix}.{path}": record for path, record in inner.items()
        }
    return out


#: Every path the envelope publishes. Provenance is attached to all of them rather
#: than to the root alone, because `provenance.reason_for` matches a path exactly:
#: attach only to `envelope_mm` and an assertion on `envelope_mm.size[0]` comes back
#: unmeasured with a generic message instead of the reason that was recorded.
_ENVELOPE_PATHS: Final[tuple[str, ...]] = (
    "envelope_mm",
    *(f"envelope_mm.{key}[{index}]" for key in ("min", "size") for index in range(3)),
)


def _envelope(design: AssemblyDesign, shapes: Mapping[str, Any]) -> dict[str, Any]:
    """The product's world bounding box — how much room the machine takes up.

    Conservative by construction, because `Box.transformed` is: the union of re-boxed
    rotated boxes is at least the true envelope and never less. For a packaging claim
    that is the safe direction — the machine is never reported as fitting through a
    door it does not fit through.
    """
    bounds = occt_bounds(shapes)
    local: dict[str, Box] = {}
    total: Box | None = None
    try:
        for occurrence in design.structure.occurrences(leaves_only=True):
            if occurrence.component not in local:
                local[occurrence.component] = bounds(occurrence.component)
            placed = local[occurrence.component].transformed(occurrence.frame)
            total = placed if total is None else total.union(placed)
    except Exception as exc:  # noqa: BLE001 - a missing box is a recorded gap, not a crash
        return _provenance_only(
            _ENVELOPE_PATHS,
            {
                "basis": _BASIS_UNAVAILABLE,
                "reason": (
                    "the product's envelope could not be measured because a "
                    f"component's bounding box could not be: {type(exc).__name__}: {exc}"
                ),
            },
        )
    if total is None:  # pragma: no cover - AssemblyDesign refuses an empty product
        return _provenance_only(
            _ENVELOPE_PATHS,
            {
                "basis": _BASIS_UNAVAILABLE,
                "reason": "the product structure has no leaf occurrences to bound.",
            },
        )
    out: dict[str, Any] = {"envelope_mm": total.to_payload()}
    out.update(
        _provenance_only(
            _ENVELOPE_PATHS,
            {
                "basis": _BASIS_MEASURED,
                "method": (
                    "union of every leaf occurrence's local bounding box placed by its "
                    "world frame; each placed box re-boxes eight rotated corners, so the "
                    "union is an over-estimate and never an under-estimate"
                ),
            },
        )
    )
    return out


def _provenance_only(paths: Iterable[str], record: Mapping[str, Any]) -> dict[str, Any]:
    """A payload fragment that is nothing but a provenance record, on several paths."""
    return {_PROVENANCE_KEY: {path: dict(record) for path in paths}}


def _party_payload(
    design: AssemblyDesign,
    payloads: Mapping[str, Mapping[str, Any]],
    party: str,
) -> Mapping[str, Any]:
    """One side of a contract's own measurement, given a component name or a path.

    An unknown party gets an empty payload rather than an error, which is
    `contracts.check_all`'s rule and the right one: every claim about it then comes
    back UNMEASURED naming the paths that were not there, where raising would lose the
    verdicts on the other interfaces.
    """
    if party in payloads:
        return payloads[party]
    try:
        component = design.structure.occurrence(party).component
    except Exception:  # noqa: BLE001 - a party naming nothing is an unmeasured claim
        return {}
    return payloads.get(component, {})


def _paths_for(design: AssemblyDesign, party: str) -> tuple[str, ...]:
    """Every occurrence path a contract's party stands for."""
    if party in design.structure:
        return design.structure.paths_of(party)
    try:
        return (design.structure.occurrence(party).path,)
    except Exception:  # noqa: BLE001 - handled by the caller as an unmeasured boundary
        return ()


def _boundary_payload(
    clash: ClashReport, design: AssemblyDesign, interface: Interface
) -> dict[str, Any]:
    """What was measured *between* the two parties, over every place they meet.

    **Two numbers, because one would be a lie in one direction or the other.**
    `minimum_clearance_mm` is the closest the two ever come — the packaging question.
    `widest_gap_mm` is the worst of the per-pair minima — the *fit-up* question, and
    the one a weld claim must read: a portal whose header sits on one post and floats
    10 mm above the other has a minimum clearance of zero and an open joint, and a
    claim reading only the minimum passes it.

    A pair that was not measured — rejected by its bounding boxes, excluded by a rule,
    over budget, or refused by the measurer — makes the whole boundary UNAVAILABLE with
    that pair's own recorded reason. Not "the pairs we did look at were fine": an
    interface checked at one of its two joints has not been checked.

    **`app.assembly` has no per-pair lookup and no per-pair payload.** `ClashReport`
    publishes one assembly-wide payload and holds its findings and its three skip lists
    as flat tuples; `ClashFinding` has no `to_payload()`. So a contract that wants the
    measurement at *its* boundary — which is what an interface contract is — has to
    index the report itself, which is what this does.
    """
    findings = {frozenset((f.path_a, f.path_b)): f for f in clash.findings}
    skipped = {
        frozenset((s.path_a, s.path_b)): s
        for group in (clash.rejected_by_bounds, clash.excluded, clash.unchecked)
        for s in group
    }

    left = _paths_for(design, interface.provider)
    right = _paths_for(design, interface.consumer)
    pairs = [(a, b) for a, b in _cross(left, right) if a != b]
    if not pairs:
        return _provenance_only(
            (_MINIMUM_CLEARANCE_MM, _WIDEST_GAP_MM, _INTERFERENCE_VOLUME_MM3),
            {
                "basis": _BASIS_UNAVAILABLE,
                "reason": (
                    f"{interface.provider!r} and {interface.consumer!r} have no pair of "
                    "occurrences in this product, so there is no boundary between them "
                    "to measure. Check the names against the product structure."
                ),
            },
        )

    distances: list[float] = []
    interferences: list[float] = []
    for pair in pairs:
        key = frozenset(pair)
        finding = findings.get(key)
        if finding is not None and finding.distance_mm is not None:
            distances.append(finding.distance_mm)
            interferences.append(finding.interference_mm3)
            continue
        if finding is not None:
            reason = finding.failure or "the measurer returned no distance"
        elif key in skipped:
            reason = skipped[key].reason
        else:  # pragma: no cover - every pair lands in exactly one of the four lists
            reason = "the clash check produced no record of this pair at all"
        return _provenance_only(
            (_MINIMUM_CLEARANCE_MM, _WIDEST_GAP_MM, _INTERFERENCE_VOLUME_MM3),
            {
                "basis": _BASIS_UNAVAILABLE,
                "reason": (
                    f"{pair[0]} and {pair[1]} meet at this interface and were not "
                    f"measured: {reason}"
                ),
            },
        )

    payload: dict[str, Any] = {
        _MINIMUM_CLEARANCE_MM: min(distances),
        _WIDEST_GAP_MM: max(distances),
        _INTERFERENCE_VOLUME_MM3: max(interferences),
    }
    payload.update(
        _provenance_only(
            (_MINIMUM_CLEARANCE_MM, _WIDEST_GAP_MM, _INTERFERENCE_VOLUME_MM3),
            {
                "basis": _BASIS_MEASURED,
                "method": (
                    f"BRepExtrema minimum distance and boolean-common volume over all "
                    f"{len(pairs)} occurrence pair(s) at this interface"
                ),
            },
        )
    )
    return payload


# ---------------------------------------------------------------------------
# Folded rungs
# ---------------------------------------------------------------------------


#: Every path the flat pattern publishes, so provenance can be attached to each of
#: them rather than to the `flat` root alone. Same reason `_ENVELOPE_PATHS` is
#: enumerated: `provenance.reason_for` matches a path exactly, and a record on the
#: root leaves `flat.blank_size_mm[0]` reading as though nobody said anything about it.
_FLAT_PATHS: Final[tuple[str, ...]] = (
    "flat.flat_length_mm",
    "flat.blank_size_mm",
    "flat.blank_size_mm[0]",
    "flat.blank_size_mm[1]",
    "flat.blank_area_mm2",
    "flat.blank_volume_mm3",
    "flat.total_bend_allowance_mm",
    "flat.fold_volume_gain_mm3",
    "flat.hole_volume_mm3",
    "flat.reconciled_volume_mm3",
    "flat.volume_residual_mm3",
    "flat.volume_mismatch_mm3",
    "flat.k_factor",
)

#: Paths the nest publishes.
_NEST_PATHS: Final[tuple[str, ...]] = (
    "nest.parts_per_sheet",
    "nest.across",
    "nest.down",
    "nest.utilisation",
    "nest.waste_mm2",
    "nest.sheet_mm",
    "nest.sheet_mm[0]",
    "nest.sheet_mm[1]",
)


def _run_folded(
    mission: Mission, design: FoldedDesign, runner: CallRunner
) -> MissionResult:
    """Flatten the part, build the solid, assess it, and check what the rung claims.

    **Unfolding comes first, and it is the one step that stops everything.** A blank
    that does not exist — legs that consume more than the flange has, a flat pattern
    that overlaps itself — means the part cannot be made from sheet at all, and every
    number after it would describe a different object. The formability report and the
    volume reconciliation both read the pattern, so there is nothing to run without it.

    **The solid is built even when the part is unformable**, which is the opposite
    choice from `_run_assembly` stopping at the first component that would not build,
    and it is the right one for the opposite reason. A member that did not build
    leaves a *hole* in the clash check and the mass roll-up, so what follows it is a
    check of a different machine. A bend tighter than the material takes is a process
    refusal about geometry that is perfectly well defined: the solid, its mass and its
    reconciliation against the blank are all still true and are all still worth seeing
    beside the reason it cannot be pressed.

    **A formability *failure* fails the rung on its own; an *unmeasured* finding does
    not.** A failure is `app.sheetmetal` refusing — the radius cracks the grade, the
    flange drops into the die — and a rung that shipped a part the package refused
    would be reporting on nothing. An unmeasured finding is a gap in the *inputs* (a
    material with no minimum bend radius on record, a hem the die-shoulder rule cannot
    be stated for), and whether a rung will accept one is a mission-level judgement.
    So it is published as `formability.unmeasured_count` and M3 claims it is zero,
    which is the same verdict arrived at somewhere a reader can see it.
    """
    try:
        pattern = unfold(design.part)
    except SheetMetalError as exc:
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            folded=FoldedReport(),
            reason=f"the part does not flatten: {exc}",
        )

    plan = compile_spec(design.spec)
    build = execute_plan(plan, runner)
    if not build.ok:
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            build=build,
            folded=FoldedReport(build=build, pattern=pattern),
            reason=f"the build stopped: {build.failure}",
        )

    try:
        formability = check_part(
            design.part,
            die_opening_mm=design.die_opening_mm,
            hole_factor=design.hole_factor,
        )
    except SheetMetalError as exc:
        # `check_part` refuses a die opening that is not one, and re-raises whatever
        # `unfold` raises. The first is a caller's mistake in the declaration and the
        # second cannot happen here, because the same part unfolded above.
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            build=build,
            folded=FoldedReport(build=build, pattern=pattern),
            reason=f"the part could not be assessed for forming: {exc}",
        )

    nest = _nest(pattern, design.stock)
    payload = _folded_payload(design, build, pattern, formability, nest)

    # The plan's own resolved parameters, so a bound written as a formula over the
    # design is evaluated against the values the geometry was built from.
    checks = check_assertions(mission.assertions, payload, parameters=plan.parameters)
    built = FoldedReport(
        build=build,
        pattern=pattern,
        formability=formability,
        nest=nest,
        payload=payload,
    )

    problems = [str(finding) for finding in formability.failed]
    if problems or not checks.ok:
        reasons = []
        if not checks.ok:
            reasons.append(checks.summary())
        reasons.extend(problems)
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.FAILED,
            build=build,
            folded=built,
            checks=checks,
            reason="\n".join(reasons),
        )
    return MissionResult(
        mission=mission,
        outcome=MissionOutcome.PASSED,
        build=build,
        folded=built,
        checks=checks,
    )


def _nest(pattern: FlatPattern, stock: StockSheet | None) -> NestReport | None:
    """How many of this blank come off one sheet, grain respected.

    A plain grid, and deliberately not a nesting algorithm: the blanks are laid in
    rows and columns with no interlocking and no rotation. Two reasons, and the second
    is the one that matters. A rectangular-extent grid is a **lower bound** on what a
    real nest achieves, so a parts-per-sheet claim built on it is conservative in the
    direction a cost estimate must be. And the orientation is fixed by the grain, as
    `StockSheet` says: the blank's bend lines run along its second axis, so that axis
    is laid across the sheet's width and the blank's first axis along its length.
    """
    if stock is None:
        return None
    blank_length, blank_width = pattern.blank_size_mm
    across = int(stock.length_mm // blank_length) if blank_length > 0.0 else 0
    down = int(stock.width_mm // blank_width) if blank_width > 0.0 else 0
    return NestReport(
        sheet=stock,
        across=across,
        down=down,
        utilisation=across * down * pattern.blank_area_mm2 / stock.area_mm2,
        blank_area_mm2=pattern.blank_area_mm2,
    )


def _folded_payload(
    design: FoldedDesign,
    build: BuildReport,
    pattern: FlatPattern,
    formability: FormabilityReport,
    nest: NestReport | None,
) -> dict[str, Any]:
    """One measurement payload for a folded part, from four sources.

    **The solid's own measurement sits at the top level, unprefixed**, for the reason
    `_combined_payload` puts the mass roll-up there: `mass_kg`, `volume_mm3` and
    `bounding_box_mm` are `app.kernel.measurement`'s own spellings, and a mass budget
    or a packaging claim written against a machined part must read a folded one
    unchanged. Everything the *blank* knows goes under `flat.`, the process findings
    under `formability.`, the nest under `nest.` — three namespaces for three different
    questions, so `volume_mm3` is what the part weighs and `flat.blank_volume_mm3` is
    what the sheet it was cut from weighs, and no claim can read one meaning the other.

    The number this whole rung turns on is `flat.volume_mismatch_mm3`. See
    `_reconciliation` for what it is and why it is not zero.
    """
    payload: dict[str, Any] = dict(build.last_result())
    sidecar: dict[str, Any] = dict(payload.pop(_PROVENANCE_KEY, {}) or {})

    def merge(chunk: Mapping[str, Any]) -> None:
        for key, value in chunk.items():
            if key == _PROVENANCE_KEY:
                sidecar.update(value)
                continue
            payload[key] = value

    merge(_flat_payload(design, pattern, payload.get("volume_mm3")))
    merge(_namespaced(_formability_payload(formability), "formability"))
    merge(_nest_payload(nest))

    if sidecar:
        payload[_PROVENANCE_KEY] = sidecar
    return payload


def _flat_payload(
    design: FoldedDesign, pattern: FlatPattern, solid_volume_mm3: Any
) -> dict[str, Any]:
    """What the blank knows about itself, plus its reconciliation against the solid.

    **`app.sheetmetal` publishes no measurement payload.** `FlatPattern.to_dict()` is
    a serialisation — nested faces, outlines, point lists — not a flat mapping an
    assertion path can walk, and it carries no provenance. So the numbers a claim can
    be written against are selected here, which is the same job `_combined_payload`
    does for a product and for the same reason: the package's own dict is shaped for
    a reader, and this one is shaped for `read_measurement`.

    **An assumed K makes every number here `approximated`, not merely footnoted.**
    `FlatPattern.provisional` is true when any bend's K has no stated basis, and the
    flat length is a linear function of K, so the blank, its area, its volume and the
    residual against the solid are all estimates. Writing that into the provenance
    sidecar is what makes `AssertionReport.approximate` true and puts "(approximate)"
    beside a claim that passed — the mechanism `app/kernel/` already has, used rather
    than a second flag nobody reads.
    """
    thickness = design.part.material.thickness_mm
    blank_volume = pattern.blank_area_mm2 * thickness
    fold_gain = _fold_volume_gain_mm3(pattern, thickness)
    hole_volume = _hole_volume_mm3(design.part, thickness)
    reconciled = blank_volume + fold_gain - hole_volume

    body: dict[str, Any] = {
        "thickness_mm": thickness,
        "convention": str(pattern.convention),
        "grade": design.part.material.name,
        "bend_count": float(len(pattern.bend_lines)),
        "panel_count": float(len(pattern.faces)),
        "hole_count": float(len(pattern.holes)),
        "unstated_k_count": float(len(pattern.assumed_k_factors)),
        "blank_size_mm": list(pattern.blank_size_mm),
        "blank_area_mm2": pattern.blank_area_mm2,
        "blank_volume_mm3": blank_volume,
        "total_bend_allowance_mm": sum(line.allowance_mm for line in pattern.bend_lines),
        "fold_volume_gain_mm3": fold_gain,
        "hole_volume_mm3": hole_volume,
        "reconciled_volume_mm3": reconciled,
    }
    if pattern.flat_length_mm is not None:
        body["flat_length_mm"] = pattern.flat_length_mm

    values = {line.k.value for line in pattern.bend_lines}
    if len(values) == 1:
        body["k_factor"] = next(iter(values))

    out: dict[str, Any] = {"flat": body}
    record: dict[str, Any]
    if pattern.provisional:
        record = {
            "basis": _BASIS_APPROXIMATED,
            "method": (
                "closed-form bend allowance over the declared fold tree, from a "
                "K-factor with no stated basis on "
                + ", ".join(pattern.assumed_k_factors)
                + " — the flat length is a linear function of K"
            ),
        }
    else:
        record = {
            "basis": _BASIS_MEASURED,
            "method": (
                "closed-form bend allowance, setback and deduction over the declared "
                "fold tree, every K carrying a cited basis"
            ),
        }
    out.update(_provenance_only(_FLAT_PATHS, record))

    if isinstance(solid_volume_mm3, (int, float)) and not isinstance(
        solid_volume_mm3, bool
    ):
        residual = float(solid_volume_mm3) - reconciled
        body["volume_residual_mm3"] = residual
        body["volume_mismatch_mm3"] = abs(residual)
    else:
        out.update(
            _provenance_only(
                ("flat.volume_residual_mm3", "flat.volume_mismatch_mm3"),
                {
                    "basis": _BASIS_UNAVAILABLE,
                    "reason": (
                        "the solid reported no volume, so there is nothing to reconcile "
                        "the blank against. The blank's own numbers stand; the claim "
                        "that it makes this part does not."
                    ),
                },
            )
        )
    return out


def _fold_volume_gain_mm3(pattern: FlatPattern, thickness_mm: float) -> float:
    """How much more the folded solid holds than the blank it was cut from.

    **It is not zero, and a mission that expected it to be would fail on a correct
    part.** The flat pattern conserves length at the *neutral axis*: a bend consumes
    `BA = theta * (r + K*t)` of blank. A folded solid of constant thickness holds
    `theta * t * (r + t/2)` per unit width through the same bend — the area of a
    quarter annulus between `r` and `r + t`. Subtract, and every bend leaves

        theta * t^2 * (0.5 - K) * width

    of volume the blank never accounted for. It is zero only at `K = 0.5`, where the
    neutral axis is the mid-plane and nothing is stretched. Below that the real
    material *thins* through the bend and the constant-thickness solid does not, so
    this is the modelling gap between the two descriptions written down rather than
    absorbed into a tolerance — which is the only way a residual can also be used to
    catch the two descriptions genuinely disagreeing.

    The width of each bend is read off its zone rectangle: the zone is axis-aligned
    (`unfold` guarantees it), one side is the allowance, and the other is how far the
    bend runs.
    """
    total = 0.0
    for line in pattern.bend_lines:
        xs = [point[0] for point in line.zone[:4]]
        ys = [point[1] for point in line.zone[:4]]
        sides = sorted((max(xs) - min(xs), max(ys) - min(ys)))
        # The shorter side is the allowance and the longer is the run — except for a
        # bend whose run is shorter than its own allowance, where taking the longer
        # would silently swap them. Match against the allowance instead.
        width = (
            sides[1]
            if abs(sides[0] - line.allowance_mm) <= abs(sides[1] - line.allowance_mm)
            else sides[0]
        )
        radians = math.radians(line.angle_deg)
        total += radians * thickness_mm**2 * (0.5 - line.k.value) * width
    return total


def _hole_volume_mm3(part: SheetMetalPart, thickness_mm: float) -> float:
    """Material the holes take out, at full sheet thickness.

    Exact for a hole on a flat face, which is the only place `unfold` allows one — a
    hole in a bend zone is refused by name, so there is no case here where the bore is
    longer than the thickness.
    """
    total = 0.0
    for flange, _parent, _joint in part.walk():
        for hole in flange.holes:
            total += math.pi * (hole.diameter_mm / 2.0) ** 2 * thickness_mm
    return total


def _formability_payload(report: FormabilityReport) -> dict[str, Any]:
    """The process findings as numbers, one margin per kind of check.

    **The margins are the point, not the counts.** "Nothing failed" is a verdict and
    "the tightest bend has 0.5 mm of radius in hand" is a number, and only the second
    tells anybody how close the part is to being refused. `Finding.margin` is already
    `measured - limit` in the finding's own unit, so the worst of each check's margins
    is the honest summary of that check.

    The kinds are derived from the findings rather than listed here, so a check added
    to `app.sheetmetal.formability` appears in the payload without this function
    knowing its name — and a check *removed* leaves its path absent, which makes any
    claim on it UNMEASURED rather than silently satisfied.
    """
    body: dict[str, Any] = {
        "finding_count": float(len(report.findings)),
        "passed_count": float(len(report.passed)),
        "failed_count": float(len(report.failed)),
        "unmeasured_count": float(len(report.unmeasured)),
    }
    margins: dict[str, list[float]] = {}
    for finding in report.findings:
        margin = finding.margin
        if margin is None:
            continue
        margins.setdefault(_slug(finding.check), []).append(margin)
    for name, values in margins.items():
        body[f"{name}_margin_mm"] = min(values)
    if margins:
        body["worst_margin_mm"] = min(min(values) for values in margins.values())
    return body


def _slug(text: str) -> str:
    """A check's name as a payload key: lower case, words joined by underscores."""
    return "_".join(
        "".join(character for character in word if character.isalnum()).lower()
        for word in text.split()
        if any(character.isalnum() for character in word)
    )


def _nest_payload(nest: NestReport | None) -> dict[str, Any]:
    """The nest, or the reason there is not one."""
    if nest is None:
        return _provenance_only(
            _NEST_PATHS,
            {
                "basis": _BASIS_UNAVAILABLE,
                "reason": (
                    "no stock sheet was declared for this part, so nothing says what it "
                    "is cut from. A blank with no sheet has no yield and no waste — "
                    "which is not the same as having no waste."
                ),
            },
        )
    out: dict[str, Any] = {
        "nest": {
            "sheet": nest.sheet.name,
            "sheet_mm": [nest.sheet.length_mm, nest.sheet.width_mm],
            "across": float(nest.across),
            "down": float(nest.down),
            "parts_per_sheet": float(nest.parts_per_sheet),
            "utilisation": nest.utilisation,
            "waste_mm2": nest.sheet.area_mm2 - nest.parts_per_sheet * nest.blank_area_mm2,
        }
    }
    out.update(
        _provenance_only(
            _NEST_PATHS,
            {
                "basis": _BASIS_MEASURED,
                "method": (
                    "an axis-aligned grid of the blank's rectangular extent, in the one "
                    "orientation the rolling direction allows (bend lines across the "
                    "sheet's length). A lower bound: a real nest interlocks and this "
                    "does not"
                ),
            },
        )
    )
    return out


def run_ladder(
    runner_factory: Callable[[], CallRunner],
    missions: Iterable[Mission] | None = None,
) -> LadderReport:
    """Run every rung, each against its own runner.

    The factory is what keeps the rungs independent: a mission that passed
    because the one before it left a body in the document would report the right
    volume for the wrong reason, and nothing in the numbers would show it.

    A pending rung never calls the factory — building a runner to not use it
    costs a document and says nothing.
    """
    chosen = tuple(missions) if missions is not None else LADDER
    results: list[MissionResult] = []
    for mission in chosen:
        if not mission.buildable:
            results.append(run_mission(mission, _never_called))
            continue
        # The factory rather than one runner from it: a single-part rung calls it
        # once and an assembly rung calls it once per part, so "each thing that is
        # built gets its own document" is one rule at one place.
        results.append(run_mission(mission, runner_factory=runner_factory))
    return LadderReport(results=tuple(results))


def mission(rung: str) -> Mission:
    """The declared rung by name, so a caller can run one without the ladder."""
    for candidate in LADDER:
        if candidate.rung == rung:
            return candidate
    known = ", ".join(m.rung for m in LADDER)
    raise KeyError(f"No mission {rung!r}. The ladder is {known}.")


def _never_called(tool: str, arguments: Any) -> Any:  # pragma: no cover - guard
    raise AssertionError(
        "A pending mission must not be built; run_mission returns before the runner."
    )


# ---------------------------------------------------------------------------
# M1 — the machined bracket
# ---------------------------------------------------------------------------
#
# Dimensions live here as constants and the closed forms are computed from them,
# so a changed dimension moves the design and its claims together. An assertion
# carrying a number typed in by hand is one that stops describing the part the
# moment somebody edits the spec, and it fails in the direction that looks like a
# geometry bug.

_M1_WIDTH_MM: Final = 120.0
_M1_DEPTH_MM: Final = 80.0
_M1_THICK_MM: Final = 8.0
_M1_CORNER_MM: Final = 5.0
_M1_BORE_MM: Final = 14.0

#: aluminium-6061-t6, kg/m³. The material is named in the spec; this is the same
#: number, kept here so the mass claim is a closed form rather than a reading.
_M1_DENSITY_KG_M3: Final = 2700.0

#: A square corner replaced by a quarter round loses r²(1 − π/4) of area.
_CORNER_LOSS_MM2: Final = _M1_CORNER_MM**2 * (1.0 - math.pi / 4.0)

_M1_FACE_AREA_MM2: Final = (
    _M1_WIDTH_MM * _M1_DEPTH_MM
    - 4.0 * _CORNER_LOSS_MM2
    - math.pi * (_M1_BORE_MM / 2.0) ** 2
)
_M1_VOLUME_MM3: Final = _M1_FACE_AREA_MM2 * _M1_THICK_MM
_M1_MASS_KG: Final = _M1_VOLUME_MM3 * 1e-9 * _M1_DENSITY_KG_M3

#: Rounded-rectangle perimeter: the four straight runs shortened by the radii,
#: plus one full circle's worth of corner.
_M1_PERIMETER_MM: Final = (
    2.0 * (_M1_WIDTH_MM - 2.0 * _M1_CORNER_MM)
    + 2.0 * (_M1_DEPTH_MM - 2.0 * _M1_CORNER_MM)
    + 2.0 * math.pi * _M1_CORNER_MM
)
_M1_AREA_MM2: Final = (
    2.0 * _M1_FACE_AREA_MM2
    + _M1_PERIMETER_MM * _M1_THICK_MM
    + math.pi * _M1_BORE_MM * _M1_THICK_MM
)

#: Two flats, four walls, four corner cylinders, one bore.
_M1_FACES: Final = 11


def _m1_spec() -> DesignSpec:
    """Sketch → rectangle → pad → corner fillets → through bore. Twelve calls.

    **The fillet names the feature it applies to, and that is load-bearing.**
    Unscoped, `edges: "vertical"` means every vertical edge *on the part*,
    including ones a later feature adds. Measured on this geometry with a
    20×20×10 boss standing on the slab: scoped removes 171.68 mm³, the four
    corners of the slab and nothing else; unscoped removes 386.28 mm³, having
    rounded the boss too — and reports success either way. That is the defect the
    2026-09-05 verification found in the design suite's own bracket fixture, where
    `feature` was declared and silently dropped, so the canonical example of the
    vocabulary was rounding the whole part.

    **The order — fillets before the bore — is not load-bearing, and the note
    that said it was has been corrected.** It was written expecting the bare word
    `vertical` to also catch the bore's *seam*, which CLAUDE.md warns about.
    Measured: it does not, here. Bore-first and fillet-first give the same volume
    to 1e-12 and the same eleven faces, scoped or not. The order stands because it
    is the order the part is machined in — profile the outside, then drill — which
    is a reason to read it that way and not a reason the geometry needs.
    """
    return DesignSpec.of(
        "M1 machined bracket",
        material="aluminium-6061-t6",
        parameters=[
            Parameter("width_mm", Unit.MM, value=_M1_WIDTH_MM),
            Parameter("depth_mm", Unit.MM, value=_M1_DEPTH_MM),
            Parameter("thick_mm", Unit.MM, value=_M1_THICK_MM),
            Parameter("corner_mm", Unit.MM, value=_M1_CORNER_MM),
            Parameter("bore_mm", Unit.MM, value=_M1_BORE_MM),
        ],
        features=[
            FeatureSpec("bracket.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "bracket.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("bracket.profile"),
                    "width_mm": expr("width_mm"),
                    "height_mm": expr("depth_mm"),
                },
            ),
            FeatureSpec(
                "bracket.slab",
                "catia_pad",
                {"sketch": ref("bracket.profile"), "length_mm": expr("thick_mm")},
                note="Extrude the footprint to thickness.",
            ),
            FeatureSpec(
                "bracket.corners",
                "catia_fillet",
                {
                    "feature": ref("bracket.slab"),
                    "radius_mm": expr("corner_mm"),
                    "edges": "vertical",
                },
                note=(
                    "Break the four corners of the slab. Scoped to the slab so a "
                    "later feature's vertical edges cannot join the selection."
                ),
            ),
            FeatureSpec(
                "bracket.bore_sketch", "catia_sketch_create", {"support": "XY"}
            ),
            FeatureSpec(
                "bracket.bore_circle",
                "catia_sketch_circle",
                {
                    "sketch": ref("bracket.bore_sketch"),
                    "diameter_mm": expr("bore_mm"),
                },
            ),
            FeatureSpec(
                "bracket.bore",
                "catia_pocket",
                {"sketch": ref("bracket.bore_sketch"), "limit": "up_to_last"},
                note="A clearance hole, through.",
            ),
        ],
    )


#: Tolerances are floors with headroom, not the measured error pinned. The kernel
#: integrates a curved face to about one part in 10^10 here; asserting that would
#: make the suite red on the next OCCT release for no engineering reason.
_M1_ASSERTIONS: Final = (
    Assertion(
        name="volume matches the closed form",
        measure="volume_mm3",
        comparison="==",
        bound=_M1_VOLUME_MM3,
        tolerance=1e-3,
        note="w·d·t less four corner reliefs and the bore. The whole rung in one number.",
    ),
    Assertion(
        name="mass matches the closed form",
        measure="mass_kg",
        comparison="==",
        bound=_M1_MASS_KG,
        tolerance=1e-9,
        note="Volume times the density of 6061-T6. Catches a material that did not attach.",
    ),
    Assertion(
        name="surface area matches the closed form",
        measure="surface_area_mm2",
        comparison="==",
        bound=_M1_AREA_MM2,
        tolerance=1e-3,
        note="Independent of volume: a bore that stopped short keeps the volume plausible.",
    ),
    Assertion(
        name="the plate is as thick as it was told to be",
        measure="bounding_box_mm.size[2]",
        comparison="==",
        bound=_M1_THICK_MM,
        tolerance=1e-4,
        note="BRepBndLib is loose by ~1e-7 mm; the tolerance is that, with headroom.",
    ),
    Assertion(
        name="the footprint is the width it was given",
        measure="bounding_box_mm.size[0]",
        comparison="==",
        bound=_M1_WIDTH_MM,
        tolerance=1e-4,
        note="A corner fillet must not shrink the extent — it removes a corner, not a side.",
    ),
    # A count is an integer, so any slack below 1 *is* exact equality. The bare
    # `==` that `assertions.py` refuses is refused for the measured decimals it
    # was written for, which these are not — half a unit says so without asking
    # that module to guess which paths are counts.
    Assertion(
        name="the bracket is one solid",
        measure="solid_count",
        comparison="==",
        bound=1.0,
        tolerance=0.5,
        note="A fillet or a bore that split the part reports a believable volume.",
    ),
    Assertion(
        name="the part has the faces the design implies",
        measure="face_count",
        comparison="==",
        bound=float(_M1_FACES),
        tolerance=0.5,
        note="Two flats, four walls, four corner cylinders, one bore. Topology, not size.",
    ),
    Assertion(
        name="the centre of mass sits at mid-thickness",
        measure="centre_of_mass_mm[2]",
        comparison="==",
        bound=_M1_THICK_MM / 2.0,
        tolerance=1e-6,
        note="A prism's centroid is at half its height whatever is cut through it.",
    ),
)


# ---------------------------------------------------------------------------
# M2 — the welded frame
# ---------------------------------------------------------------------------
#
# A portal: two uprights and a header across the top, all of one rectangular hollow
# section, welded where the header lands on each post. Small enough to build and
# measure in a couple of seconds, which is the point — the rung tests the machinery
# for a machine made of several parts, not the size of one.
#
# What makes it a mission rather than a demo, in the three things M1 has none of:
#
# * **Members that must meet.** The header's soffit and each post's crown are one
#   plane apart in the design and zero apart in the built product, and the difference
#   between those two sentences is measured rather than assumed.
# * **A load path.** Header to post to floor, in compression through the joint. What
#   geometry can say about it is that the joint closes over the full section and that
#   the two members do not occupy the same space; what it cannot say is whether the
#   weld carries the load, which is `_M2_UNPROVEN` and belongs to E6 and E8.3.
# * **A mass.** Rolled up over three occurrences of two components, against the closed
#   form below — and a mass budget, which is what a frame is actually designed to.
#
# **Two components, three occurrences.** The two posts are one component placed twice,
# which is the product graph doing the thing it exists to do: the post is designed
# once, built once, measured once, and weighed twice.

#: The section: a rectangular hollow section, 60 deep in the plane of the frame,
#: 40 wide out of it, 4 mm wall. Declared on the *interface* rather than in either
#: member's spec (master plan 14.3) and bound into both, so the two members cannot
#: drift onto different sections — the failure a contract exists to make impossible
#: rather than to catch later.
_M2_SECTION_DEPTH_MM: Final = 60.0
_M2_SECTION_WIDTH_MM: Final = 40.0
_M2_WALL_MM: Final = 4.0

_M2_POST_MM: Final = 700.0
_M2_SPAN_MM: Final = 800.0

#: steel-1018, kg/m³, and the same number the spec's material carries. Kept here so
#: the mass claim is a closed form rather than a reading of what was built.
_M2_DENSITY_KG_M3: Final = 7870.0

#: What a welded joint may be asked to bridge. A fit-up gap of a few hundredths is a
#: weld; ten millimetres is a member cut short, and the difference is the claim.
_M2_FIT_UP_MM: Final = 0.05

#: How far apart two members are still worth measuring. **Not zero, and the reason is
#: not the one it looks like.** With `clearance_mm=0` the broad phase rejects any pair
#: whose boxes do not touch — soundly, for a clash question — so a joint that has
#: opened by 10 mm is thrown away as "safely apart" and the fit-up claim never sees the
#: pair. Measured, by building the frame with a post 10 mm short and a zero threshold:
#: the joint claims come back **UNMEASURED, not passed**, because `_boundary_payload`
#: refuses to answer for an interface one of whose pairs nobody looked at. So the rung
#: still fails, and what this number buys is the *diagnosis* — "the joint is 10 mm
#: open" instead of "the joint could not be checked". Both are red; only one sends
#: somebody to the right place.
_M2_INSPECTION_MM: Final = 25.0

#: A hollow rectangle: the outer area less the bore's.
_M2_SECTION_AREA_MM2: Final = _M2_SECTION_DEPTH_MM * _M2_SECTION_WIDTH_MM - (
    (_M2_SECTION_DEPTH_MM - 2.0 * _M2_WALL_MM)
    * (_M2_SECTION_WIDTH_MM - 2.0 * _M2_WALL_MM)
)
_M2_POST_VOLUME_MM3: Final = _M2_SECTION_AREA_MM2 * _M2_POST_MM
_M2_HEADER_VOLUME_MM3: Final = _M2_SECTION_AREA_MM2 * _M2_SPAN_MM
_M2_POST_MASS_KG: Final = _M2_POST_VOLUME_MM3 * 1e-9 * _M2_DENSITY_KG_M3
_M2_HEADER_MASS_KG: Final = _M2_HEADER_VOLUME_MM3 * 1e-9 * _M2_DENSITY_KG_M3
_M2_MASS_KG: Final = 2.0 * _M2_POST_MASS_KG + _M2_HEADER_MASS_KG

#: The header's axis sits half a section above the posts' tops, so its centre of mass
#: is there; each post's is at half its height.
_M2_HEADER_AXIS_Z_MM: Final = _M2_POST_MM + _M2_SECTION_DEPTH_MM / 2.0
_M2_CENTRE_Z_MM: Final = (
    2.0 * _M2_POST_MASS_KG * (_M2_POST_MM / 2.0)
    + _M2_HEADER_MASS_KG * _M2_HEADER_AXIS_Z_MM
) / _M2_MASS_KG

#: A budget, not a measurement — what the frame is *allowed* to weigh. Held apart
#: from the closed form on purpose: the closed form catches geometry that changed,
#: and the budget catches a design that grew.
_M2_MASS_BUDGET_KG: Final = 15.0

#: Four outer walls, four inner, two ends.
_M2_MEMBER_FACES: Final = 10


def _m2_section_parameters() -> ParameterSet:
    """The numbers both members build from. One declaration, bound into two specs."""
    return ParameterSet.of(
        [
            Parameter(
                "section_depth_mm",
                Unit.MM,
                value=_M2_SECTION_DEPTH_MM,
                description="Section depth, in the plane of the frame.",
            ),
            Parameter(
                "section_width_mm",
                Unit.MM,
                value=_M2_SECTION_WIDTH_MM,
                description="Section width, out of the plane of the frame.",
            ),
            Parameter(
                "wall_mm",
                Unit.MM,
                value=_M2_WALL_MM,
                description="Wall thickness of the hollow section.",
            ),
        ]
    )


def _m2_member_spec(name: str, length_mm: float) -> DesignSpec:
    """One length of hollow section, built along its own +Z from its own origin.

    Six calls: the outer profile, padded to length, then the bore pocketed through it
    — the same shape M1's bore has, for the same reason it is a second sketch rather
    than a second contour in the first. Every member is built at the origin in its own
    coordinates and placed by the product structure; a member built where it stands in
    the frame could not be instanced twice, which is the whole reason the posts are one
    component.

    The section is *not* declared here. `contracts.bind_into` merges it in from the
    interface, so a member built to the wrong section is a compile error at the
    boundary rather than a joint that does not line up.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        parameters=[
            Parameter("length_mm", Unit.MM, value=length_mm, description="Cut length."),
        ],
        features=[
            FeatureSpec("member.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "member.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("member.profile"),
                    "width_mm": expr("section_depth_mm"),
                    "height_mm": expr("section_width_mm"),
                },
            ),
            FeatureSpec(
                "member.body",
                "catia_pad",
                {"sketch": ref("member.profile"), "length_mm": expr("length_mm")},
                note="Extrude the section to the cut length.",
            ),
            FeatureSpec("member.bore_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "member.bore_outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("member.bore_sketch"),
                    "width_mm": expr("section_depth_mm - 2 * wall_mm"),
                    "height_mm": expr("section_width_mm - 2 * wall_mm"),
                },
            ),
            FeatureSpec(
                "member.bore",
                "catia_pocket",
                {"sketch": ref("member.bore_sketch"), "limit": "up_to_last"},
                note="Hollow it out, through — this is a tube, not a bar.",
            ),
        ],
    )


#: The joint, as a contract. Provider and consumer are the *components*, so the one
#: contract covers both places they meet — and `_boundary_payload` insists on both.
_M2_JOINT: Final = Interface(
    name="post-to-header weld",
    provider="post",
    consumer="header",
    parameters=_m2_section_parameters(),
    claims=(
        Assertion(
            name="the post is the section the joint is designed for",
            measure="provider.bounding_box_mm.size[0]",
            comparison="==",
            bound="=section_depth_mm",
            tolerance=1e-3,
            note="A weld across a section the other member does not have is a fillet to nothing.",
        ),
        Assertion(
            name="the header is the section the joint is designed for",
            measure="consumer.bounding_box_mm.size[0]",
            comparison="==",
            bound="=section_depth_mm",
            tolerance=1e-3,
        ),
        Assertion(
            name="the post is the width the joint is designed for",
            measure="provider.bounding_box_mm.size[1]",
            comparison="==",
            bound="=section_width_mm",
            tolerance=1e-3,
            note="Both members are the same width, so the weld runs the full section.",
        ),
        Assertion(
            name="the header is the width the joint is designed for",
            measure="consumer.bounding_box_mm.size[1]",
            comparison="==",
            bound="=section_width_mm",
            tolerance=1e-3,
        ),
        Assertion(
            name="the joint closes everywhere it is made",
            measure=_WIDEST_GAP_MM,
            comparison="<=",
            bound=_M2_FIT_UP_MM,
            note=(
                "The worst of the joints, not the best: a header bearing on one post "
                "and floating above the other is an open joint whose minimum clearance "
                "is still zero."
            ),
        ),
        Assertion(
            name="the members meet without occupying the same space",
            measure=_INTERFERENCE_VOLUME_MM3,
            comparison="<=",
            bound=0.0,
            tolerance=1e-6,
            note=(
                "Two solids that overlap are one part in the model and two parts in the "
                "shop. The tolerance is the boolean's own noise on a shared face."
            ),
        ),
    ),
    note="The header bears on the posts; the section is common to both and declared here.",
)


def _m2_structure(
    *,
    header_axis_z_mm: float = _M2_HEADER_AXIS_Z_MM,
    header_roll_rad: float = 0.0,
) -> ProductStructure:
    """The portal: two posts on the floor, one header across their tops.

    Coordinates are the frame's: X across the span, Y out of plane, Z up. The posts
    stand at each end with their outer faces flush with the ends of the header, so the
    frame is exactly `span` wide. The header is the one rotated placement — a quarter
    turn about Y takes its own +Z, which it was built along, onto the frame's +X, and
    its section's depth onto the frame's Z. Get that rotation wrong and the envelope
    is 60 wide out of plane instead of 40, which is why the envelope is asserted.

    The two arguments are the design's two real degrees of freedom at this joint —
    how high the header sits, and which way up its section is — and they carry the
    frame's own numbers as defaults. They are arguments so the guards above can be
    broken and watched to fail: a header set 10 mm low interpenetrates the posts, and
    one rolled a quarter turn about its own axis puts the 60 out of plane. A mission
    whose failure modes cannot be reached is a mission that proves nothing.
    """
    builder = StructureBuilder()
    builder.define("frame", description="Welded portal frame, RHS 60x40x4.")
    builder.define(
        "post", design="M2 post", material="steel-1018", description="Upright."
    )
    builder.define(
        "header", design="M2 header", material="steel-1018", description="Cross member."
    )
    builder.add(
        "frame",
        "post",
        placement=at(_M2_SECTION_DEPTH_MM / 2.0, 0.0, 0.0),
        note="Left upright, outer face on the frame's origin.",
    )
    builder.add(
        "frame",
        "post",
        placement=at(_M2_SPAN_MM - _M2_SECTION_DEPTH_MM / 2.0, 0.0, 0.0),
        note="Right upright, outer face on the far end of the span.",
    )
    builder.add(
        "frame",
        "header",
        placement=compose(
            at(0.0, 0.0, header_axis_z_mm),
            compose(
                turned((0.0, 1.0, 0.0), math.pi / 2.0),
                turned((0.0, 0.0, 1.0), header_roll_rad),
            ),
        ),
        note="Laid across the tops of the posts, bearing on both.",
    )
    return builder.build("frame")


def _m2_design(
    *,
    post_mm: float = _M2_POST_MM,
    span_mm: float = _M2_SPAN_MM,
    hollow: bool = True,
    structure: ProductStructure | None = None,
    clearance_mm: float = _M2_INSPECTION_MM,
) -> AssemblyDesign:
    """The frame: the graph, the two member designs, and the joint contract.

    Every argument defaults to the frame as designed, and exists so a test can build
    the frame *wrong* — a member cut short, a solid bar where a tube was specified, a
    header set low, a clash check told to look only for contact — and watch the rung
    fail. `tests/test_mission_m2.py` breaks each one; a guard nobody has seen fail is
    a guard nobody has verified.
    """
    from app.assembly.contracts import bind_into

    def member(name: str, length_mm: float) -> DesignSpec:
        spec = _m2_member_spec(name, length_mm)
        if not hollow:
            # Drop the bore: a solid bar of the same section fits, welds and builds,
            # and weighs 2.3 times what the frame was designed to.
            spec = spec.with_features(spec.features[:3])
        return bind_into(_M2_JOINT, spec)

    return AssemblyDesign(
        structure=_m2_structure() if structure is None else structure,
        parts={
            "post": member("M2 post", post_mm),
            "header": member("M2 header", span_mm),
        },
        interfaces=(_M2_JOINT,),
        parameters=ParameterSet.of(
            [
                *_m2_section_parameters(),
                Parameter("span_mm", Unit.MM, value=_M2_SPAN_MM),
                Parameter("post_height_mm", Unit.MM, value=_M2_POST_MM),
                Parameter(
                    "mass_budget_kg",
                    Unit.KG,
                    value=_M2_MASS_BUDGET_KG,
                    description="What the frame is allowed to weigh.",
                ),
            ]
        ),
        clearance_mm=clearance_mm,
    )


_M2_ASSERTIONS: Final = (
    Assertion(
        name="the frame weighs its closed form",
        measure="mass_kg",
        comparison="==",
        bound=_M2_MASS_KG,
        tolerance=1e-6,
        note=(
            "Two posts and a header of hollow section, at the density of 1018. "
            "Unpublished unless every occurrence was weighed, so this doubles as the "
            "check that nothing was left out of the roll-up."
        ),
    ),
    Assertion(
        name="the frame is inside its mass budget",
        measure="mass_kg",
        comparison="<=",
        bound="=mass_budget_kg",
        note="A requirement rather than a measurement: the closed form can be right and too heavy.",
    ),
    Assertion(
        name="the frame's centre of mass is on its centreline",
        measure="centre_of_mass_mm[0]",
        comparison="==",
        bound=_M2_SPAN_MM / 2.0,
        tolerance=1e-3,
        note="Symmetry: a post placed at the wrong end of the span moves this and nothing else.",
    ),
    Assertion(
        name="the frame's centre of mass is where the members put it",
        measure="centre_of_mass_mm[2]",
        comparison="==",
        bound=_M2_CENTRE_Z_MM,
        tolerance=1e-3,
        note="Mass-weighted over the three occurrences, not the mean of the three heights.",
    ),
    Assertion(
        name="every occurrence was weighed",
        measure="unmeasured_occurrence_count",
        comparison="==",
        bound=0.0,
        note="A member left out of the roll-up makes the frame lighter, which every budget passes.",
    ),
    Assertion(
        name="the frame is the three members it was drawn as",
        measure="occurrence_count",
        comparison="==",
        bound=3.0,
    ),
    Assertion(
        name="nothing in the frame clashes",
        measure="clash.clash_count",
        comparison="==",
        bound=0.0,
    ),
    Assertion(
        name="every pair was measured, excluded or soundly rejected",
        measure="clash.unchecked_pair_count",
        comparison="==",
        bound=0.0,
        note=(
            "The one that makes the clash number mean anything: a check that skipped "
            "pairs reports a roomier machine than there is."
        ),
    ),
    Assertion(
        name="no two members occupy the same space",
        measure="clash." + _INTERFERENCE_VOLUME_MM3,
        comparison="<=",
        bound=0.0,
        tolerance=1e-6,
    ),
    Assertion(
        name="the frame is as wide as its span",
        measure="envelope_mm.size[0]",
        comparison="==",
        bound="=span_mm",
        tolerance=1e-3,
        note="Outer face to outer face — the posts are inside the header's ends, not beyond them.",
    ),
    Assertion(
        name="the frame is one section thick out of plane",
        measure="envelope_mm.size[1]",
        comparison="==",
        bound="=section_width_mm",
        tolerance=1e-3,
        note=(
            "The header's quarter turn about Y, checked. Rotate it the wrong way and "
            "this reads 60 while every member is still the right size."
        ),
    ),
    Assertion(
        name="the frame stands as tall as a post plus the header",
        measure="envelope_mm.size[2]",
        comparison="==",
        bound="=post_height_mm + section_depth_mm",
        tolerance=1e-3,
    ),
    Assertion(
        name="the post is the length it was cut to",
        measure="post.bounding_box_mm.size[2]",
        comparison="==",
        bound="=post_height_mm",
        tolerance=1e-3,
        note="Per member, because the frame's envelope is unchanged by a post cut short.",
    ),
    Assertion(
        name="the header is the length it was cut to",
        measure="header.bounding_box_mm.size[2]",
        comparison="==",
        bound="=span_mm",
        tolerance=1e-3,
    ),
    Assertion(
        name="the post is hollow",
        measure="post.volume_mm3",
        comparison="==",
        bound=_M2_POST_VOLUME_MM3,
        tolerance=1e-2,
        note="A solid bar weighs 2.3x this and would still build, still fit and still weld.",
    ),
    Assertion(
        name="the header is hollow",
        measure="header.volume_mm3",
        comparison="==",
        bound=_M2_HEADER_VOLUME_MM3,
        tolerance=1e-2,
    ),
    Assertion(
        name="the post is a closed tube",
        measure="post.face_count",
        comparison="==",
        bound=float(_M2_MEMBER_FACES),
        note="Four outer walls, four inner, two ends. Topology, independent of every size above.",
    ),
    Assertion(
        name="the frame is not one solid",
        measure="clash.occurrence_count",
        comparison="==",
        bound=3.0,
        note=(
            "An assembly is parts that touch, never a boolean union of them. This is "
            "what the rung is for."
        ),
    ),
)

#: What M2 builds and does **not** claim. The master plan's own column for this rung
#: is "weld sizing, fatigue at joints", and none of that is geometry. Printed beside
#: every pass, and it is why the ladder is not `complete` with M2 green.
_M2_UNPROVEN: Final = (
    "E6 — the weld is not sized: no load case has been run through this frame, so "
    "'the joint closes' is a fit-up claim and not a strength one",
    "E8.3 — no weld classification to BS 7608 / Eurocode 3, so nothing here says the "
    "joint survives a duty cycle",
    "E17.4 — no weldment model and no cut list: the members are separate solids that "
    "touch, with no weld bead, no end preparation and no bill of cut lengths",
)



# ---------------------------------------------------------------------------
# M6 — the belt conveyor
# ---------------------------------------------------------------------------
#
# **The rung's difficulty is length and repetition, not shape.** Nothing in a
# conveyor is geometrically hard: it is angle-section stringers, a leg pair every
# so often, and a roller every so often. What is hard is that there are a lot of
# them, that the count is a *consequence* of the length rather than a number
# somebody typed, and that changing the length must change the bill of materials
# without anybody editing it. The master plan's own column for this rung says so:
# "long assemblies, standard parts, modularity, layout".
#
# So the assertions are about **arithmetic that must not drift from the graph**.
# The mass is a closed form over the counts; the counts come from the pitch and
# the length; and if somebody changes the length and the roll-up disagrees with
# the closed form, one of the two is wrong and the rung says so. That is the
# whole test: a 12-metre conveyor built by hand from a 6-metre drawing is the
# real-world failure, and it is a bill-of-materials failure rather than a
# geometry one.
#
# **Why this rung is buildable now and was not before.** Its declared `needs`
# were E14.1 (product structure and BOM as first-class data) and E12.3 (standard
# parts), and both are complete. Nothing about it needed E13 or E9, which is why
# it moves while M4, M5, M7 and M8 do not.

#: The conveyor as designed. Six metres of it, which is long enough that the
#: repetition is real and short enough that the mission runs in a test suite.
_M6_LENGTH_MM: Final = 6_000.0
#: Belt width, and therefore roller length between the stringers.
_M6_BELT_WIDTH_MM: Final = 600.0

#: Leg pairs every 1.5 m and a roller every 0.5 m. **Pitches, not counts.** A
#: count typed here would be a number that has to be re-typed when the length
#: changes, which is exactly the drift this rung exists to catch.
_M6_LEG_PITCH_MM: Final = 1_500.0
_M6_ROLLER_PITCH_MM: Final = 500.0

#: The stringer: 60x40x4 RHS, the same section M2 uses. Deliberately the same —
#: a conveyor is a long frame, and reusing the section means the mass arithmetic
#: is checkable against a number this file already computes.
_M6_SECTION_AREA_MM2: Final = _M2_SECTION_AREA_MM2
_M6_LEG_MM: Final = 800.0

#: A bought roller: Ø50 steel tube, 3 mm wall, over the belt width. `mass_kg` is
#: the catalogue figure, not a modelled one — that is what "standard parts" means
#: and it is why E12.3 was a prerequisite. Modelling a bought part is how a BOM
#: stops matching what anybody can order.
_M6_ROLLER_OD_MM: Final = 50.0
_M6_ROLLER_WALL_MM: Final = 3.0
_M6_ROLLER_AREA_MM2: Final = math.pi * (
    (_M6_ROLLER_OD_MM / 2.0) ** 2 - (_M6_ROLLER_OD_MM / 2.0 - _M6_ROLLER_WALL_MM) ** 2
)


def m6_counts(
    *, length_mm: float = _M6_LENGTH_MM,
    leg_pitch_mm: float = _M6_LEG_PITCH_MM,
    roller_pitch_mm: float = _M6_ROLLER_PITCH_MM,
) -> dict[str, int]:
    """How many of each part a conveyor of this length needs.

    **The counts are derived and never declared.** A bay is a span between leg
    pairs, so `n` bays need `n + 1` leg pairs — the fencepost that every hand
    -written BOM gets wrong once. Rollers are the same fencepost: one at each end
    and one at every pitch between.

    Returned as a dict rather than a tuple so a caller reading `counts["legs"]`
    cannot silently transpose two numbers that are both integers.
    """
    if length_mm <= 0 or leg_pitch_mm <= 0 or roller_pitch_mm <= 0:
        raise SpecError(
            "A conveyor needs a positive length and positive pitches. A zero pitch "
            "is an infinite bill of materials."
        )
    bays = max(1, math.ceil(length_mm / leg_pitch_mm))
    roller_gaps = max(1, math.ceil(length_mm / roller_pitch_mm))
    return {
        "bays": bays,
        # One more than the bays: the fencepost.
        "leg_pairs": bays + 1,
        "legs": 2 * (bays + 1),
        "stringers": 2,
        "rollers": roller_gaps + 1,
    }


def m6_mass_kg(
    *, length_mm: float = _M6_LENGTH_MM,
    leg_pitch_mm: float = _M6_LEG_PITCH_MM,
    roller_pitch_mm: float = _M6_ROLLER_PITCH_MM,
    belt_width_mm: float = _M6_BELT_WIDTH_MM,
) -> float:
    """The closed form: two stringers, the legs, and the rollers.

    Held apart from whatever the product structure rolls up, and compared against
    it by an assertion. **A conveyor built to a 6 m drawing and quoted at 12 m is
    a bill-of-materials failure, not a geometry one**, and the only way to catch
    it is to compute the mass twice from different directions and insist they
    agree.
    """
    counts = m6_counts(
        length_mm=length_mm, leg_pitch_mm=leg_pitch_mm, roller_pitch_mm=roller_pitch_mm
    )
    density = _M2_DENSITY_KG_M3 * 1e-9
    stringer = _M6_SECTION_AREA_MM2 * length_mm * density * counts["stringers"]
    legs = _M6_SECTION_AREA_MM2 * _M6_LEG_MM * density * counts["legs"]
    rollers = _M6_ROLLER_AREA_MM2 * belt_width_mm * density * counts["rollers"]
    return stringer + legs + rollers


def _m6_structure(
    *,
    length_mm: float = _M6_LENGTH_MM,
    leg_pitch_mm: float = _M6_LEG_PITCH_MM,
    roller_pitch_mm: float = _M6_ROLLER_PITCH_MM,
    belt_width_mm: float = _M6_BELT_WIDTH_MM,
) -> ProductStructure:
    """The conveyor as a graph, with every occurrence placed from the pitches.

    Coordinates are the conveyor's: X along the run, Y across the belt, Z up.

    **Every `add` below is inside a loop over a derived count.** That is the rung:
    a structure with the occurrences written out by hand would be a structure that
    is right for one length, and the whole point of a modular machine is that it
    is right for the length somebody asks for. `tests/test_mission_m6.py` builds
    it at three lengths and checks the graph, the closed form and the roll-up all
    move together.
    """
    counts = m6_counts(
        length_mm=length_mm, leg_pitch_mm=leg_pitch_mm, roller_pitch_mm=roller_pitch_mm
    )
    builder = StructureBuilder()
    builder.define("conveyor", description="Belt conveyor, modular bay construction.")
    builder.define(
        "stringer", design="M6 stringer", material="steel-1018", description="Side rail."
    )
    builder.define("leg", design="M6 leg", material="steel-1018", description="Support leg.")
    builder.define(
        "roller",
        design="M6 roller",
        material="steel-1018",
        description="Bought idler roller, Ø50x3.",
    )

    half_width = belt_width_mm / 2.0
    for side, y in (("near", -half_width), ("far", half_width)):
        builder.add(
            "conveyor",
            "stringer",
            placement=compose(
                at(0.0, y, _M6_LEG_MM), turned((0.0, 1.0, 0.0), math.pi / 2.0)
            ),
            note=f"{side.title()} side rail, laid along the run.",
        )

    for index in range(counts["leg_pairs"]):
        x = min(index * leg_pitch_mm, length_mm)
        for y in (-half_width, half_width):
            builder.add(
                "conveyor",
                "leg",
                placement=at(x, y, 0.0),
                note=f"Leg at {x:g} mm along the run.",
            )

    for index in range(counts["rollers"]):
        x = min(index * roller_pitch_mm, length_mm)
        builder.add(
            "conveyor",
            "roller",
            placement=compose(
                at(x, -half_width, _M6_LEG_MM), turned((1.0, 0.0, 0.0), math.pi / 2.0)
            ),
            note=f"Idler at {x:g} mm along the run.",
        )
    return builder.build("conveyor")


def _m6_roller_spec(belt_width_mm: float) -> DesignSpec:
    """The bought roller, modelled only as the envelope it occupies.

    **This is a bought part and the model says so.** The tube is drawn because
    something has to occupy the space for a clash check, and nothing here claims
    to be the manufacturer's geometry: no bearing bores, no shaft, no end caps.
    The mass in the closed form is the catalogue figure for the same reason —
    E12.3's whole argument is that a bought part is selected, not modelled, and a
    modelled one is how a BOM stops matching what anybody can order.
    """
    return DesignSpec.of(
        "M6 roller",
        material="steel-1018",
        description="Bought idler roller: envelope only, not the maker's geometry.",
        parameters=[
            Parameter("length_mm", Unit.MM, value=belt_width_mm, description="Face width."),
            Parameter("od_mm", Unit.MM, value=_M6_ROLLER_OD_MM),
            Parameter("wall_mm", Unit.MM, value=_M6_ROLLER_WALL_MM),
        ],
        features=[
            FeatureSpec("roller.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "roller.outline",
                "catia_sketch_circle",
                # `diameter_mm`, not a radius: the operation takes a diameter and
                # a tube is specified by its outside diameter anyway, so the two
                # agree with each other and with the catalogue.
                {"sketch": ref("roller.profile"), "diameter_mm": expr("od_mm")},
            ),
            FeatureSpec(
                "roller.body",
                "catia_pad",
                {"sketch": ref("roller.profile"), "length_mm": expr("length_mm")},
                note="The envelope the roller occupies, for clash and layout.",
            ),
            FeatureSpec("roller.bore_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "roller.bore_outline",
                "catia_sketch_circle",
                {
                    "sketch": ref("roller.bore_sketch"),
                    "diameter_mm": expr("od_mm - 2 * wall_mm"),
                },
            ),
            FeatureSpec(
                "roller.bore",
                "catia_pocket",
                {"sketch": ref("roller.bore_sketch"), "limit": "up_to_last"},
                note="It is a tube; the wall is what the catalogue mass is for.",
            ),
        ],
    )


#: The leg-to-stringer joint, as a contract.
#:
#: A conveyor is a long frame and its legs weld to its rails, so the two must be
#: the same section — and saying that *once*, as an interface, is what makes a
#: leg built to the wrong section a compile error at the boundary rather than a
#: joint that does not line up on the shop floor. It is the same shape as M2's
#: joint and deliberately so: the difference between the two rungs is length and
#: repetition, not the way parts meet.
_M6_JOINT: Final = Interface(
    name="leg-to-stringer weld",
    provider="leg",
    consumer="stringer",
    parameters=_m2_section_parameters(),
    claims=(
        Assertion(
            name="the leg is the section the joint is designed for",
            measure="provider.bounding_box_mm.size[0]",
            comparison="==",
            bound="=section_depth_mm",
            tolerance=1e-3,
            note="A weld across a section the rail does not have is a fillet to nothing.",
        ),
        Assertion(
            name="the stringer is the section the joint is designed for",
            measure="consumer.bounding_box_mm.size[0]",
            comparison="==",
            bound="=section_depth_mm",
            tolerance=1e-3,
            note=(
                "Checked on both sides rather than assumed from one: a contract that "
                "only ever looks at the provider is a contract with one party."
            ),
        ),
    ),
)


def _m6_design(
    *,
    length_mm: float = _M6_LENGTH_MM,
    leg_pitch_mm: float = _M6_LEG_PITCH_MM,
    roller_pitch_mm: float = _M6_ROLLER_PITCH_MM,
    belt_width_mm: float = _M6_BELT_WIDTH_MM,
    structure: ProductStructure | None = None,
) -> AssemblyDesign:
    """The conveyor: the graph, three part designs, and the numbers behind them.

    Every argument defaults to the conveyor as designed and exists so a test can
    build it *wrong* — a longer run whose BOM was not recounted, a leg pitch that
    leaves a bay unsupported, a roller pitch coarse enough that the belt sags
    between idlers. `tests/test_mission_m6.py` breaks each; a guard nobody has
    seen fail is a guard nobody has verified.
    """
    from app.assembly.contracts import bind_into

    return AssemblyDesign(
        structure=(
            _m6_structure(
                length_mm=length_mm,
                leg_pitch_mm=leg_pitch_mm,
                roller_pitch_mm=roller_pitch_mm,
                belt_width_mm=belt_width_mm,
            )
            if structure is None
            else structure
        ),
        parts={
            # The section comes from the joint rather than from each part, the
            # same way M2's members get theirs — see `_M6_JOINT`.
            "stringer": bind_into(_M6_JOINT, _m2_member_spec("M6 stringer", length_mm)),
            "leg": bind_into(_M6_JOINT, _m2_member_spec("M6 leg", _M6_LEG_MM)),
            # Not bound: the roller is a bought part and is not welded to
            # anything. Binding a section into it would claim the joint reaches
            # a component it does not touch.
            "roller": _m6_roller_spec(belt_width_mm),
        },
        interfaces=(_M6_JOINT,),
        parameters=ParameterSet.of(
            [
                *_m2_section_parameters(),
                Parameter("run_length_mm", Unit.MM, value=length_mm),
                Parameter("belt_width_mm", Unit.MM, value=belt_width_mm),
                Parameter("leg_pitch_mm", Unit.MM, value=leg_pitch_mm),
                Parameter("roller_pitch_mm", Unit.MM, value=roller_pitch_mm),
                Parameter(
                    "occurrence_count",
                    Unit.NONE,
                    value=float(
                        sum(
                            m6_counts(
                                length_mm=length_mm,
                                leg_pitch_mm=leg_pitch_mm,
                                roller_pitch_mm=roller_pitch_mm,
                            )[key]
                            for key in ("stringers", "legs", "rollers")
                        )
                    ),
                    description=(
                        "How many parts the BOM says are in this conveyor. Derived "
                        "from the pitches, and asserted against what the graph holds."
                    ),
                ),
                Parameter(
                    "mass_closed_form_kg",
                    Unit.KG,
                    value=m6_mass_kg(
                        length_mm=length_mm,
                        leg_pitch_mm=leg_pitch_mm,
                        roller_pitch_mm=roller_pitch_mm,
                        belt_width_mm=belt_width_mm,
                    ),
                    description=(
                        "Computed from the pitches, not from the graph. The assertion "
                        "that this equals the roll-up is the rung."
                    ),
                ),
            ]
        ),
    )


_M6_ASSERTIONS: Final = (
    Assertion(
        name="the roll-up equals the closed form over the derived counts",
        measure="mass_kg",
        comparison="==",
        bound="=mass_closed_form_kg",
        tolerance=1e-6,
        note=(
            "The rung. One mass from the product graph and one from the pitches, and "
            "they must agree — a conveyor built to a 6 m drawing and quoted at 12 m is "
            "a bill-of-materials failure, and computing the mass twice from different "
            "directions is the only way to see it."
        ),
    ),
    Assertion(
        name="the run is as long as it was asked to be",
        # `envelope_mm`, not `bounding_box_mm`: an assembly's world extent is
        # what `_envelope` publishes, and a part-level path here would come back
        # UNMEASURED — which is not a pass, but is also not the check intended.
        measure="envelope_mm.size[0]",
        comparison="==",
        # The envelope runs from the first leg to the last, plus the section the
        # stringer occupies at each end; the rails themselves are exactly the run
        # length, so the envelope is the run plus one section.
        bound="=run_length_mm + section_depth_mm",
        tolerance=1.0,
        note=(
            "A modular machine whose length is not the length somebody asked for has "
            "a placement bug, not a geometry one, and the envelope is where it shows."
        ),
    ),
    Assertion(
        name="the graph holds exactly the parts the bill of materials counts",
        measure="clash.occurrence_count",
        comparison="==",
        bound="=occurrence_count",
        note=(
            "The BOM checked against the graph directly, beside the mass check that "
            "checks it by arithmetic. Two different ways of being wrong: the mass "
            "claim catches a count that drifted, and this catches a part that was "
            "placed twice or not at all with a compensating error in another."
        ),
    ),
)


_M6_UNPROVEN: Final = (
    "E6 — no load case has been run through this conveyor: nothing here says the "
    "stringers carry the belt, the load or their own span between legs",
    "E9 — the rollers do not turn: this is a static layout, so belt tension, drive "
    "torque and the loads a moving belt puts into the frame are all absent",
    "E12.3 — the roller is an envelope with a catalogue mass, not a selected part "
    "number: nothing has checked that a Ø50x3 idler of this face width is orderable",
    "E13 — no cost estimate and no DFM: the bill of materials is a count of parts "
    "and not a quotation",
    "E17.4 — no weldment model and no cut list, the same gap M2 carries",
)


# ---------------------------------------------------------------------------
# M3 — the sheet-metal enclosure
# ---------------------------------------------------------------------------
#
# A cover: one blank of 1.5 mm cold-rolled mild steel, folded four times into a
# channel with a return lip down each side. Five panels, four bends, two holes in
# the roof, and a blank that has to come off a standard sheet. Small enough to build
# and flatten in about a second, which is the point — the rung tests the machinery
# for a part that is *folded*, not the size of the enclosure.
#
# What makes it a mission rather than a demo, in the four things M1 and M2 have none
# of:
#
# * **Panels that must meet, through material that is not there yet.** The roof and
#   the walls do not butt: they are joined by a bend that consumes blank and leaves
#   none of it flat. Every dimension on the folded part is therefore two dimensions —
#   what it measures when folded, and what it measured when it was flat — and the
#   whole of `app/sheetmetal/` is the arithmetic between them.
# * **A blank that has to nest.** A part that cannot be cut from the sheet the shop
#   buys is a part nobody makes, however well it folds.
# * **Bends that have to be formable.** Tighter than the grade takes and it cracks;
#   a lip shorter than the die shoulder and it drops into the die; a hole too near a
#   tangent line and it comes out oval. Three refusals, each with a source.
# * **A K-factor, which is a judgement rather than a measurement.** See
#   `_M3_K_FACTOR` — it is the one input on this rung that two competent engineers
#   would hand you different numbers for, and the flat length is linear in it.
#
# **Two descriptions of one part.** `_m3_part` declares the cover as flanges and
# bends, which is what unfolds; `_m3_spec` draws the same cover as a closed folded
# cross-section and extrudes it, which is what the kernel builds. Nothing in the code
# base connects those two sentences — see `FoldedDesign` — so both are written from
# the constants below and `flat.volume_mismatch_mm3` is asked to be zero.

_M3_THICKNESS_MM: Final = 1.5
_M3_RADIUS_MM: Final = 2.0

#: Outside, across the cover: the dimension a caliper reads over the two walls.
_M3_WIDTH_MM: Final = 200.0

#: Outside, roof to the underside of the lips.
_M3_HEIGHT_MM: Final = 60.0

#: The return lip, to the outside mould line. It is what the cover is screwed down by.
_M3_LIP_MM: Final = 20.0

#: Along the bend lines — the length of the channel, and the direction the blank is
#: *not* folded in.
_M3_DEPTH_MM: Final = 150.0

_M3_BEND_ANGLE_DEG: Final = 90.0

#: Two clearance holes in the roof, positioned in the roof's own declared coordinates:
#: `u` from the mould line at the left-hand corner, `v` from the left-hand end.
_M3_HOLE_MM: Final = 10.0
_M3_HOLE_U_MM: Final = (60.0, 140.0)
_M3_HOLE_V_MM: Final = _M3_DEPTH_MM / 2.0

#: The grade in `app.sheetmetal.material`'s vocabulary, and the slug in
#: `app.solve.materials`' vocabulary, **and nothing checks that they are the same
#: metal.** The two catalogues are disjoint: one knows minimum bend radii and knows
#: nothing about density, the other knows density and has never heard of a bend. So
#: the correspondence — cold-rolled mild sheet is 1018-grade steel for the purpose of
#: weighing it — is asserted here, by a human, in a comment. A part whose radius was
#: honoured for mild steel and whose mass was taken from titanium would pass every
#: check in both packages, which is the strongest argument this rung produces for a
#: bridge between them.
_M3_SHEET_GRADE: Final = "steel_mild_cr"
_M3_MATERIAL_SLUG: Final = "steel-1018"
_M3_DENSITY_KG_M3: Final = 7870.0

#: The sheet the blank is cut from. See `StockSheet` for why the nest gets one
#: orientation and not the better of two.
_M3_STOCK: Final = StockSheet(
    name="2000 x 1000 x 1.5 mild steel sheet", length_mm=2000.0, width_mm=1000.0
)

#: What a run of covers has to yield per sheet. A budget, like M2's mass budget and
#: held apart from the closed forms for the same reason: the arithmetic can be right
#: and the part still too wasteful to make.
_M3_PARTS_PER_SHEET_FLOOR: Final = 24.0

#: What the cover is allowed to weigh.
_M3_MASS_BUDGET_KG: Final = 0.75


def _m3_k_factor(
    *,
    inside_radius_mm: float = _M3_RADIUS_MM,
    thickness_mm: float = _M3_THICKNESS_MM,
    grade: str = _M3_SHEET_GRADE,
) -> KFactor:
    """Where the neutral axis is taken to sit, and why from DIN 6935 rather than ANSI.

    **This is the rung's one real judgement.** `app.sheetmetal` ships two traditions
    and refuses to pick: `machinerys_handbook()` gives K = 0.4469 at this ratio and
    `din6935()` gives 0.3562, a 25% difference that moves the blank by
    `_M3_K_SPREAD_MM` over four bends. Neither is wrong. What decides it here is what
    each one *claims about itself*:

    * DIN 6935 is written for **cold bending of flat steel products**, and this sheet
      is cold-rolled mild steel. `din6935()` returns `Status.SPECIFIED` for the steel
      family — the strongest basis in the package short of a test bend, and the only
      one `app.solve.materials.Status.is_design_basis` accepts without a caveat.
    * Machinery's Handbook's table covers steel too, but as a general shop table:
      `Status.TYPICAL`, and its own note says it is "a screening value for a first
      blank" that a production run should replace with a test bend.

    A specified standard for the exact material class beats a screening table for
    several, so DIN it is. **What that does not buy is a right answer** — see
    `_M3_UNPROVEN`. Nothing here has been bent. The correct K for this shop's press,
    die and coil is `measured()` off a test bend, and until somebody makes one the
    blank length is a number from a standard rather than from the material.

    `grade` is threaded through because `din6935` demotes itself to
    `Status.ESTIMATED` off steel, naming the analogy. The *value* is the same either
    way — the unfolding factor is a function of `r/t` alone — so declaring the cover
    in aluminium moves the basis and not one dimension of the blank, which is exactly
    the sort of change that has to stay visible.
    """
    return din6935(
        inside_radius_mm=inside_radius_mm,
        thickness_mm=thickness_mm,
        family=sheet_material(grade, thickness_mm=thickness_mm).family,
    )


_M3_K_FACTOR: Final = _m3_k_factor()

#: The other tradition, for the caveat below. Not used to build anything.
_M3_ALTERNATIVE_K: Final = machinerys_handbook(
    inside_radius_mm=_M3_RADIUS_MM,
    thickness_mm=_M3_THICKNESS_MM,
    family=sheet_material(_M3_SHEET_GRADE, thickness_mm=_M3_THICKNESS_MM).family,
)

#: How much blank the choice of tradition is worth, over this part's four bends. The
#: number that makes "K is a judgement" concrete instead of a scruple.
_M3_K_SPREAD_MM: Final = (
    4.0
    * math.radians(_M3_BEND_ANGLE_DEG)
    * _M3_THICKNESS_MM
    * abs(_M3_ALTERNATIVE_K.value - _M3_K_FACTOR.value)
)

# -- the closed forms -------------------------------------------------------
#
# Computed from the constants above, so a changed dimension moves the design and its
# claims together. M1's rule, and it matters more here: a folded part has two sets of
# numbers and typing either by hand would let them agree with each other and with
# nothing else.

#: `BA = (pi/180) * theta * (r + K*t)`.
_M3_BEND_ALLOWANCE_MM: Final = (
    math.radians(_M3_BEND_ANGLE_DEG)
    * (_M3_RADIUS_MM + _M3_K_FACTOR.value * _M3_THICKNESS_MM)
)

#: `SB = tan(theta/2) * (r + t)`, which at 90 degrees is just `r + t`.
_M3_SETBACK_MM: Final = (
    math.tan(math.radians(_M3_BEND_ANGLE_DEG) / 2.0) * (_M3_RADIUS_MM + _M3_THICKNESS_MM)
)
_M3_BEND_DEDUCTION_MM: Final = 2.0 * _M3_SETBACK_MM - _M3_BEND_ALLOWANCE_MM

#: `sum(declared lengths) - sum(bend deductions)`, over the chain lip-wall-roof-wall-lip.
_M3_FLAT_LENGTH_MM: Final = (
    2.0 * _M3_LIP_MM
    + 2.0 * _M3_HEIGHT_MM
    + _M3_WIDTH_MM
    - 4.0 * _M3_BEND_DEDUCTION_MM
)
_M3_BLANK_AREA_MM2: Final = _M3_FLAT_LENGTH_MM * _M3_DEPTH_MM

#: Where a bend's tangent line falls, measured in from the mould line. Every straight
#: run below is a declared length less one or two of these.
_M3_TANGENT_INSET_MM: Final = _M3_SETBACK_MM

#: The folded cross-section: five straight runs of sheet plus four quarter annuli.
#: `(pi/4)((r+t)^2 - r^2)` per bend, which is `(pi/4)(2rt + t^2)`.
_M3_SECTION_AREA_MM2: Final = _M3_THICKNESS_MM * (
    (_M3_WIDTH_MM - 2.0 * _M3_TANGENT_INSET_MM)
    + 2.0 * (_M3_HEIGHT_MM - 2.0 * _M3_TANGENT_INSET_MM)
    + 2.0 * (_M3_LIP_MM - _M3_TANGENT_INSET_MM)
) + math.pi * _M3_THICKNESS_MM * (2.0 * _M3_RADIUS_MM + _M3_THICKNESS_MM)

_M3_HOLE_VOLUME_MM3: Final = (
    len(_M3_HOLE_U_MM) * math.pi * (_M3_HOLE_MM / 2.0) ** 2 * _M3_THICKNESS_MM
)
_M3_VOLUME_MM3: Final = _M3_SECTION_AREA_MM2 * _M3_DEPTH_MM - _M3_HOLE_VOLUME_MM3
_M3_MASS_KG: Final = _M3_VOLUME_MM3 * 1e-9 * _M3_DENSITY_KG_M3

#: Volume the blank does not account for: `theta * t^2 * (0.5 - K)` per bend, per mm
#: of bend. See `_fold_volume_gain_mm3` — it is a property of the fold model, not a
#: tolerance, which is why it is written out here and asserted rather than absorbed.
_M3_FOLD_GAIN_MM3: Final = (
    4.0
    * math.radians(_M3_BEND_ANGLE_DEG)
    * _M3_THICKNESS_MM**2
    * (0.5 - _M3_K_FACTOR.value)
    * _M3_DEPTH_MM
)

#: Both surfaces of every straight run, both arcs of every bend, the two free edges
#: of the lips, the two ends of the channel, and what the holes swap for their bores.
_M3_SECTION_PERIMETER_MM: Final = (
    2.0
    * (
        (_M3_WIDTH_MM - 2.0 * _M3_TANGENT_INSET_MM)
        + 2.0 * (_M3_HEIGHT_MM - 2.0 * _M3_TANGENT_INSET_MM)
        + 2.0 * (_M3_LIP_MM - _M3_TANGENT_INSET_MM)
    )
    + 2.0 * _M3_THICKNESS_MM
    + 4.0
    * math.radians(_M3_BEND_ANGLE_DEG)
    * (2.0 * _M3_RADIUS_MM + _M3_THICKNESS_MM)
)
_M3_AREA_MM2: Final = (
    _M3_SECTION_PERIMETER_MM * _M3_DEPTH_MM
    + 2.0 * _M3_SECTION_AREA_MM2
    + len(_M3_HOLE_U_MM)
    * (
        math.pi * _M3_HOLE_MM * _M3_THICKNESS_MM
        - 2.0 * math.pi * (_M3_HOLE_MM / 2.0) ** 2
    )
)


def _m3_centre_y_mm() -> float:
    """The centroid across the section, measured down from the roof's outer face.

    The one number that knows which way up the cover is: the mass is not symmetric in
    this axis — a roof at one end, two lips at the other — so a cover built upside
    down has the same bounding box, the same volume, the same mass and this number
    reflected. Every other claim on this rung would pass.

    A quarter annulus's centroid is `(2/3) * (R^3 - r^3)/(R^2 - r^2) * sin(a)/a` from
    the arc centre along the bisector, with `2a` the swept angle. Not the mid-line
    radius `(R + r)/2`, which is the natural guess and is 0.07 mm out here — enough to
    move the answer past the tolerance this is asserted to.
    """
    t = _M3_THICKNESS_MM
    inner, outer = _M3_RADIUS_MM, _M3_RADIUS_MM + t
    inset = _M3_TANGENT_INSET_MM
    half = math.radians(_M3_BEND_ANGLE_DEG) / 2.0
    arc_area = math.radians(_M3_BEND_ANGLE_DEG) / 2.0 * (outer**2 - inner**2)
    arc_centroid = (
        (2.0 / 3.0)
        * (outer**3 - inner**3)
        / (outer**2 - inner**2)
        * math.sin(half)
        / half
    )
    # The bisector of every one of the four bends is at 45 degrees to the axis, so its
    # component along it is the centroid distance over root two — upward from the two
    # roof corners' centres, downward from the two lip corners'.
    lift = arc_centroid / math.sqrt(2.0)
    pieces: tuple[tuple[float, float], ...] = (
        (t * (_M3_WIDTH_MM - 2.0 * inset), -t / 2.0),
        (t * (_M3_HEIGHT_MM - 2.0 * inset), -_M3_HEIGHT_MM / 2.0),
        (t * (_M3_HEIGHT_MM - 2.0 * inset), -_M3_HEIGHT_MM / 2.0),
        (t * (_M3_LIP_MM - inset), -_M3_HEIGHT_MM + t / 2.0),
        (t * (_M3_LIP_MM - inset), -_M3_HEIGHT_MM + t / 2.0),
        (arc_area, -inset + lift),
        (arc_area, -inset + lift),
        (arc_area, -_M3_HEIGHT_MM + inset - lift),
        (arc_area, -_M3_HEIGHT_MM + inset - lift),
    )
    section_area = sum(area for area, _ in pieces)
    section_centre = sum(area * centre for area, centre in pieces) / section_area
    # The holes come out of the roof, so they take material from -t/2 with them.
    return (
        section_area * _M3_DEPTH_MM * section_centre
        - _M3_HOLE_VOLUME_MM3 * (-_M3_THICKNESS_MM / 2.0)
    ) / _M3_VOLUME_MM3


_M3_CENTRE_Y_MM: Final = _m3_centre_y_mm()

#: Two surfaces on each of five panels, two on each of four bends, two free edges,
#: two ends of the channel, and one cylinder per hole.
_M3_FACES: Final = 2 * 5 + 2 * 4 + 2 + 2 + len(_M3_HOLE_U_MM)


def _m3_part(
    *,
    lip_mm: float = _M3_LIP_MM,
    height_mm: float = _M3_HEIGHT_MM,
    width_mm: float = _M3_WIDTH_MM,
    depth_mm: float = _M3_DEPTH_MM,
    inside_radius_mm: float = _M3_RADIUS_MM,
    grade: str = _M3_SHEET_GRADE,
    thickness_mm: float = _M3_THICKNESS_MM,
    k: KFactor | None = None,
    hole_u_mm: Sequence[float] = _M3_HOLE_U_MM,
) -> SheetMetalPart:
    """The cover as a fold tree: a chain of five flanges, dimensioned to the mould line.

    **The root is a lip, not the roof, and that is load-bearing.** Rooted at the roof
    the part is a *tree* — two walls off one panel — and `unfold` reports no flat
    length for a tree, because a branching blank has an extent in two directions and
    no chain to sum along. Rooted at a lip it is a chain: lip, wall, roof, wall, lip,
    each hanging off the `FAR` edge of the last. The geometry is identical either way;
    what the chain buys is `flat_length_mm`, which `unfold` computes twice — once from
    the bend deductions and once from the bend allowances — and refuses to report if
    the two disagree. That cross-check is the strongest thing in the package and it is
    only available to a chain.

    Every argument defaults to the cover as designed and exists so a test can declare
    it **wrong** — a lip shorter than its own setback, a radius tighter than the grade
    takes, a hole against a tangent line, a K with no basis — and watch the rung fail.
    """
    sheet = sheet_material(grade, thickness_mm=thickness_mm)
    bend_k = (
        k
        if k is not None
        else _m3_k_factor(
            inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm, grade=grade
        )
    )

    def bend(name: str) -> Bend:
        return Bend(
            angle_deg=_M3_BEND_ANGLE_DEG,
            inside_radius_mm=inside_radius_mm,
            # Every fold turns the same way round the section, which is what makes the
            # blank a plain strip and the part a channel. `direction` changes no
            # arithmetic — it is what the operator is told.
            direction=BendDirection.DOWN,
            k=bend_k,
            name=name,
        )

    roof = Flange(
        name="roof",
        length_mm=width_mm,
        holes=tuple(
            Hole(
                name=f"gland_{index + 1}",
                diameter_mm=_M3_HOLE_MM,
                u_mm=u,
                v_mm=_M3_HOLE_V_MM,
            )
            for index, u in enumerate(hole_u_mm)
        ),
        joints=(
            Joint(
                edge=Edge.FAR,
                bend=bend("corner_right"),
                flange=Flange(
                    name="wall_right",
                    length_mm=height_mm,
                    joints=(
                        Joint(
                            edge=Edge.FAR,
                            bend=bend("return_right"),
                            flange=Flange(name="lip_right", length_mm=lip_mm),
                        ),
                    ),
                ),
            ),
        ),
    )
    return SheetMetalPart(
        name="M3 enclosure cover",
        material=sheet,
        # No default exists and none should: a flange dimension carries nothing to say
        # which mould line it was measured to, and the three differ by a setback per
        # bend end — 3.5 mm each here, 14 mm of blank over four bends.
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=Flange(
            name="lip_left",
            length_mm=lip_mm,
            # The only flange that declares a width. Every other spans the edge it is
            # bent from, which is what `width_mm=None` means — see `Flange`.
            width_mm=depth_mm,
            joints=(
                Joint(
                    edge=Edge.FAR,
                    bend=bend("return_left"),
                    flange=Flange(
                        name="wall_left",
                        length_mm=height_mm,
                        joints=(
                            Joint(
                                edge=Edge.FAR,
                                bend=bend("corner_left"),
                                flange=roof,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _m3_section_features(
    *,
    thickness_mm: float,
    inside_radius_mm: float,
    width_mm: float,
    height_mm: float,
    lip_mm: float,
    sketch: str,
) -> list[FeatureSpec]:
    """The folded cross-section, drawn segment by segment as one closed contour.

    **This is the part the design IR cannot say.** There is no `catia_wall`, no
    `catia_flange`, no `catia_bend` and no `catia_unfold` among the operations the
    OCCT backend implements, so a folded solid has to be drawn the way a draughtsman
    would have drawn one in 1975: the section, by hand, in twenty segments, and
    extruded. Everything below is therefore *derived* geometry — the corner tangent
    points are consequences of the fold, not dimensions somebody chose — which is why
    the coordinates are computed here from the same constants the fold tree is built
    from rather than written as parameter expressions. They cannot drift, because
    there is one set of numbers; they are also not editable, which a sheet-metal
    feature in the IR would fix.

    The contour is traced once round the material: out along the left lip's underside,
    round the outside of both bends and over the roof, down the far side and back
    along the inside. Segment ends are computed the same way at both ends of every
    join, so the chaining in `app.kernel.occt.sketching` sees exact matches rather
    than near ones.
    """
    t, r = thickness_mm, inside_radius_mm
    outer = r + t
    # Where a bend's arc centre sits, in from each outside face.
    inset = t + r
    top, floor = 0.0, -height_mm
    left, right = 0.0, width_mm

    centres = {
        "return_left": (inset, floor + inset),
        "corner_left": (inset, top - inset),
        "corner_right": (right - inset, top - inset),
        "return_right": (right - inset, floor + inset),
    }

    calls: list[tuple[str, dict[str, Any]]] = []

    def line(start: tuple[float, float], end: tuple[float, float]) -> None:
        calls.append(("catia_sketch_line", {"start": list(start), "end": list(end)}))

    def arc(centre: tuple[float, float], radius: float, a0: float, a1: float) -> None:
        calls.append(
            (
                "catia_sketch_arc",
                {
                    "centre": list(centre),
                    "radius_mm": radius,
                    "start_angle_deg": a0,
                    "end_angle_deg": a1,
                },
            )
        )

    # Outside, anticlockwise from the free edge of the left lip.
    line((lip_mm, floor), (inset, floor))
    arc(centres["return_left"], outer, -90.0, -180.0)
    line((left, floor + inset), (left, top - inset))
    arc(centres["corner_left"], outer, 180.0, 90.0)
    line((inset, top), (right - inset, top))
    arc(centres["corner_right"], outer, 90.0, 0.0)
    line((right, top - inset), (right, floor + inset))
    arc(centres["return_right"], outer, 0.0, -90.0)
    line((right - inset, floor), (right - lip_mm, floor))
    # Up the free edge of the right lip, then back along the inside.
    line((right - lip_mm, floor), (right - lip_mm, floor + t))
    line((right - lip_mm, floor + t), (right - inset, floor + t))
    arc(centres["return_right"], r, -90.0, 0.0)
    line((right - t, floor + inset), (right - t, top - inset))
    arc(centres["corner_right"], r, 0.0, 90.0)
    line((right - inset, top - t), (inset, top - t))
    arc(centres["corner_left"], r, 90.0, 180.0)
    line((left + t, top - inset), (left + t, floor + inset))
    arc(centres["return_left"], r, 180.0, 270.0)
    line((inset, floor + t), (lip_mm, floor + t))
    line((lip_mm, floor + t), (lip_mm, floor))

    return [
        FeatureSpec(
            f"cover.seg{index + 1:02d}",
            tool,
            {"sketch": ref(sketch), **arguments},
        )
        for index, (tool, arguments) in enumerate(calls)
    ]


def _m3_spec(
    *,
    lip_mm: float = _M3_LIP_MM,
    height_mm: float = _M3_HEIGHT_MM,
    width_mm: float = _M3_WIDTH_MM,
    depth_mm: float = _M3_DEPTH_MM,
    inside_radius_mm: float = _M3_RADIUS_MM,
    thickness_mm: float = _M3_THICKNESS_MM,
    hole_u_mm: Sequence[float] = _M3_HOLE_U_MM,
) -> DesignSpec:
    """The same cover as geometry: one sketch of the section, extruded, then drilled.

    The holes are pocketed from the `ZX` plane — the plane of the roof's outer face —
    rather than drawn into the section, because they are holes through a *panel* and
    not features of the profile. On `ZX` the sketch's own axes are `u` along world Z
    and `v` along world X, so a hole the fold tree places at `(u, v)` on the roof is
    drawn here at `(v_roof, u_roof)`: the roof's `u` runs along the section, which is
    world X, and its `v` runs along the bends, which is world Z. Getting that swap
    wrong puts the holes 80 mm out and every other number on the rung unchanged, which
    is why the centre of mass is asserted in both of those axes.
    """
    return DesignSpec.of(
        "M3 enclosure cover",
        material=_M3_MATERIAL_SLUG,
        parameters=[
            Parameter("width_mm", Unit.MM, value=width_mm),
            Parameter("height_mm", Unit.MM, value=height_mm),
            Parameter("lip_mm", Unit.MM, value=lip_mm),
            Parameter("depth_mm", Unit.MM, value=depth_mm),
            Parameter("thickness_mm", Unit.MM, value=thickness_mm),
            Parameter("radius_mm", Unit.MM, value=inside_radius_mm),
        ],
        features=[
            FeatureSpec("cover.section", "catia_sketch_create", {"support": "XY"}),
            *_m3_section_features(
                thickness_mm=thickness_mm,
                inside_radius_mm=inside_radius_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                lip_mm=lip_mm,
                sketch="cover.section",
            ),
            FeatureSpec(
                "cover.body",
                "catia_pad",
                {"sketch": ref("cover.section"), "length_mm": expr("depth_mm")},
                note="Extrude the folded section along the bend lines.",
            ),
            *[
                feature
                for index, u in enumerate(hole_u_mm)
                for feature in (
                    FeatureSpec(
                        f"cover.gland{index + 1}_sketch",
                        "catia_sketch_create",
                        {"support": "ZX"},
                    ),
                    FeatureSpec(
                        f"cover.gland{index + 1}_circle",
                        "catia_sketch_circle",
                        {
                            "sketch": ref(f"cover.gland{index + 1}_sketch"),
                            "diameter_mm": _M3_HOLE_MM,
                            "at": [_M3_HOLE_V_MM, u],
                        },
                    ),
                    FeatureSpec(
                        f"cover.gland{index + 1}",
                        "catia_pocket",
                        {
                            "sketch": ref(f"cover.gland{index + 1}_sketch"),
                            "depth_mm": expr("height_mm"),
                            "reversed": True,
                        },
                        note=(
                            "Down through the roof. The pocket runs the full height of "
                            "the cover and meets nothing below, because the lips stop "
                            "short of the middle."
                        ),
                    ),
                )
            ],
        ],
    )


def _m3_design(
    *,
    lip_mm: float = _M3_LIP_MM,
    height_mm: float = _M3_HEIGHT_MM,
    width_mm: float = _M3_WIDTH_MM,
    depth_mm: float = _M3_DEPTH_MM,
    inside_radius_mm: float = _M3_RADIUS_MM,
    drawn_radius_mm: float | None = None,
    thickness_mm: float = _M3_THICKNESS_MM,
    grade: str = _M3_SHEET_GRADE,
    k: KFactor | None = None,
    hole_u_mm: Sequence[float] = _M3_HOLE_U_MM,
    stock: StockSheet | None = _M3_STOCK,
    die_opening_mm: float | None = None,
) -> FoldedDesign:
    """The cover: the solid, the fold tree, and the sheet it comes off.

    Every argument defaults to the cover as designed and exists so a test can build it
    **wrong** — and `drawn_radius_mm` is the sharpest of them. It changes the radius
    the *cross-section is drawn to* and leaves the fold tree alone, so the two
    descriptions of the part quietly stop being descriptions of the same part. Nothing
    refuses it; nothing about the solid looks odd; the blank is still a blank. The only
    thing that moves is `flat.volume_mismatch_mm3`, which is the whole reason that
    number is published and claimed on. `tests/test_mission_m3.py` breaks each of
    these; a guard nobody has seen fail is a guard nobody has verified.
    """
    return FoldedDesign(
        spec=_m3_spec(
            lip_mm=lip_mm,
            height_mm=height_mm,
            width_mm=width_mm,
            depth_mm=depth_mm,
            inside_radius_mm=(
                inside_radius_mm if drawn_radius_mm is None else drawn_radius_mm
            ),
            thickness_mm=thickness_mm,
            hole_u_mm=hole_u_mm,
        ),
        part=_m3_part(
            lip_mm=lip_mm,
            height_mm=height_mm,
            width_mm=width_mm,
            depth_mm=depth_mm,
            inside_radius_mm=inside_radius_mm,
            grade=grade,
            thickness_mm=thickness_mm,
            k=k,
            hole_u_mm=hole_u_mm,
        ),
        stock=stock,
        die_opening_mm=die_opening_mm,
    )


_M3_ASSERTIONS: Final = (
    # -- the two descriptions are of one part ------------------------------
    Assertion(
        name="the blank accounts for the solid",
        measure="flat.volume_mismatch_mm3",
        comparison="<=",
        bound=1e-3,
        note=(
            "The whole rung in one number. The solid's measured volume against the "
            "blank's area times the sheet thickness, plus what every bend adds by "
            "keeping its thickness while the flat pattern kept its neutral-axis "
            "length, less the holes. Two independent descriptions of one part; if they "
            "disagree, one of them is not this part."
        ),
    ),
    Assertion(
        name="the fold adds what the neutral axis gave away",
        measure="flat.fold_volume_gain_mm3",
        comparison="==",
        bound=_M3_FOLD_GAIN_MM3,
        tolerance=1e-6,
        note=(
            "theta*t^2*(0.5-K) per bend, per mm of bend. Asserted on its own so that a "
            "residual of zero cannot be reached by two errors of opposite sign."
        ),
    ),
    # -- the blank ---------------------------------------------------------
    Assertion(
        name="the blank is the length the bend deductions say",
        measure="flat.flat_length_mm",
        comparison="==",
        bound=_M3_FLAT_LENGTH_MM,
        tolerance=1e-6,
        note=(
            "Five declared lengths less four bend deductions. Unpublished for a "
            "branching part, so this doubles as the check that the cover is still a "
            "chain and still gets unfold's own two-way cross-check."
        ),
    ),
    Assertion(
        name="the blank is as wide as the cover is long",
        measure="flat.blank_size_mm[1]",
        comparison="==",
        bound=_M3_DEPTH_MM,
        tolerance=1e-6,
        note="Nothing is folded in this direction, so the blank keeps the full depth.",
    ),
    Assertion(
        name="the blank is the area its length and width give",
        measure="flat.blank_area_mm2",
        comparison="==",
        bound=_M3_BLANK_AREA_MM2,
        tolerance=1e-3,
        note=(
            "Independent of the length above: unfold sums the panels and the bend zones "
            "separately, so a bend zone left out shortens the blank and this too."
        ),
    ),
    Assertion(
        name="every bend's K-factor names a source",
        measure="flat.unstated_k_count",
        comparison="==",
        bound=0.0,
        note=(
            "The flat length is linear in K, so a blank cut from an assumed K is a "
            "blank whose length nobody has justified. An assumed K does not fail any "
            "formability check — it fails this one."
        ),
    ),
    Assertion(
        name="the cover is four bends and five panels",
        measure="flat.bend_count",
        comparison="==",
        bound=4.0,
    ),
    Assertion(
        name="the blank carries both holes",
        measure="flat.hole_count",
        comparison="==",
        bound=float(len(_M3_HOLE_U_MM)),
        note="A hole in a bend zone is refused by unfold, so an absent one is silent.",
    ),
    # -- it can be pressed --------------------------------------------------
    Assertion(
        name="nothing about forming the cover was refused",
        measure="formability.failed_count",
        comparison="==",
        bound=0.0,
    ),
    Assertion(
        name="every forming check had the data it needed",
        measure="formability.unmeasured_count",
        comparison="==",
        bound=0.0,
        note=(
            "An unmeasured check is not a pass. A material with no minimum bend radius "
            "on record makes the one check that stops a part cracking unrunnable, and "
            "the report would otherwise say 'nothing failed'."
        ),
    ),
    Assertion(
        name="the bends are inside the grade's minimum radius",
        measure="formability.minimum_bend_radius_margin_mm",
        comparison=">=",
        bound=0.0,
        note=(
            "The margin rather than the verdict: 'nothing failed' does not say whether "
            "the tightest bend has half a millimetre in hand or a hundredth."
        ),
    ),
    Assertion(
        name="every flange reaches the die shoulder",
        measure="formability.minimum_flange_length_margin_mm",
        comparison=">=",
        bound=0.0,
        note="A lip that does not reach the shoulder is not held and drops into the die.",
    ),
    Assertion(
        name="the holes keep clear of the bend zones",
        measure="formability.hole_distance_to_bend_margin_mm",
        comparison=">=",
        bound=0.0,
        note=(
            "Material within about 2t of a tangent line stretches with the bend, so a "
            "hole there comes out oval and pulled toward it."
        ),
    ),
    # -- it comes off the sheet --------------------------------------------
    Assertion(
        name="the sheet yields the run it has to",
        measure="nest.parts_per_sheet",
        comparison=">=",
        bound=_M3_PARTS_PER_SHEET_FLOOR,
        note=(
            "A budget, not a measurement. A blank that folds perfectly and gets six "
            "covers out of a sheet is a part somebody has to re-draw."
        ),
    ),
    Assertion(
        name="the blank fits across the sheet",
        measure="nest.down",
        comparison=">=",
        bound=1.0,
        note=(
            "Separate from the yield: a blank too wide for the sheet in the one "
            "orientation the grain allows yields nothing, and 'zero per sheet' does not "
            "say which way it did not fit."
        ),
    ),
    Assertion(
        name="the blank fits along the sheet",
        measure="nest.across",
        comparison=">=",
        bound=1.0,
    ),
    # -- the solid ----------------------------------------------------------
    Assertion(
        name="volume matches the closed form",
        measure="volume_mm3",
        comparison="==",
        bound=_M3_VOLUME_MM3,
        tolerance=1e-3,
        note="Five straight runs, four quarter annuli, two bores. The section, extruded.",
    ),
    Assertion(
        name="mass matches the closed form",
        measure="mass_kg",
        comparison="==",
        bound=_M3_MASS_KG,
        tolerance=1e-9,
        note=(
            "Volume times the density of 1018, which is the grade this sheet is taken "
            "to be. Catches a material that did not attach."
        ),
    ),
    Assertion(
        name="the cover is inside its mass budget",
        measure="mass_kg",
        comparison="<=",
        bound=_M3_MASS_BUDGET_KG,
        note="A requirement: the closed form can be right and the cover still too heavy.",
    ),
    Assertion(
        name="surface area matches the closed form",
        measure="surface_area_mm2",
        comparison="==",
        bound=_M3_AREA_MM2,
        tolerance=1e-3,
        note=(
            "Independent of volume: a bend drawn to the wrong radius on one side keeps "
            "the volume plausible and moves this."
        ),
    ),
    Assertion(
        name="the cover is as wide as it was drawn",
        measure="bounding_box_mm.size[0]",
        comparison="==",
        bound=_M3_WIDTH_MM,
        tolerance=1e-4,
    ),
    Assertion(
        name="the cover is as tall as it was drawn",
        measure="bounding_box_mm.size[1]",
        comparison="==",
        bound=_M3_HEIGHT_MM,
        tolerance=1e-4,
    ),
    Assertion(
        name="the cover is as long as the blank is wide",
        measure="bounding_box_mm.size[2]",
        comparison="==",
        bound=_M3_DEPTH_MM,
        tolerance=1e-4,
        note="The pad length, and the one dimension the fold does not touch.",
    ),
    Assertion(
        name="the cover is one solid",
        measure="solid_count",
        comparison="==",
        bound=1.0,
        note="A section that did not close, or a pocket that split the part, weighs plausibly.",
    ),
    Assertion(
        name="the cover has the faces the fold implies",
        measure="face_count",
        comparison="==",
        bound=float(_M3_FACES),
        note=(
            "Two surfaces on each of five panels, two on each of four bends, two lip "
            "edges, two ends, one bore each. Topology, independent of every size above: "
            "a bend that came out as a sharp corner has two faces fewer and the same "
            "bounding box."
        ),
    ),
    Assertion(
        name="the centre of mass sits on the cover's centreline",
        measure="centre_of_mass_mm[0]",
        comparison="==",
        bound=_M3_WIDTH_MM / 2.0,
        tolerance=1e-6,
        note="Symmetry: a lip of the wrong length on one side moves this and little else.",
    ),
    Assertion(
        name="the centre of mass sits at mid-length",
        measure="centre_of_mass_mm[2]",
        comparison="==",
        bound=_M3_DEPTH_MM / 2.0,
        tolerance=1e-6,
        note=(
            "The axis the holes are placed along. A gland drilled at the roof's u where "
            "its v was meant lands here and nowhere else."
        ),
    ),
    Assertion(
        name="the centre of mass sits where the fold puts it",
        measure="centre_of_mass_mm[1]",
        comparison="==",
        bound=_M3_CENTRE_Y_MM,
        tolerance=1e-4,
        note=(
            "The one claim that knows which way up the cover is: roof at one end, lips "
            "at the other, so a cover built upside down passes every other claim here."
        ),
    ),
)

#: What M3 builds and does **not** claim. Its column in the master plan's ladder is
#: "unfolding, bend allowance, DFM"; the first two are checked here and the third is
#: three rules out of a shop's list. Printed beside every pass, and it is why the
#: ladder is not `complete` with M3 green.
_M3_UNPROVEN: Final = (
    "E17.3 — K is a standard, not a test bend: DIN 6935 and Machinery's Handbook "
    f"differ by {_M3_K_SPREAD_MM:.2f} mm of blank over this part's four bends, and "
    "nothing here has been bent, so the flat length is a number from a document",
    "E17.3 — no springback: the flat pattern says how much blank a bend consumes and "
    "nothing says what over-bend the press needs to land at 90 degrees, so this part "
    "cannot be programmed from what is here",
    "E17.3 — grain direction is declared and never checked: every shipped minimum bend "
    "radius is written 'across the grain' and no material, bend or blank in the model "
    "can say which way the grain runs, so the nest's orientation is a promise",
    "E13.1 — three design rules, not a rule set: radius, flange reach and hole-to-bend "
    "are what app/sheetmetal/ checks. Relief notches, tool access, weld and fastener "
    "clearance, edge distance and burr direction are not checked by anything",
    "E6 — no load case: a 1.5 mm cover's stiffness, its panel drumming and what the "
    "lips carry when it is bolted down are unmeasured, so 'it folds' is a manufacturing "
    "claim and not a structural one",
    "E17.x — no flat-pattern drawing and no cut list: the blank exists as arithmetic "
    "and as closed polylines, and nothing has produced the file a laser cuts from",
)


#: The ladder, in tractability order. Every rung of Decision 5's table appears
#: here; `tests/test_design_missions.py` asserts that, so a rung cannot be
#: dropped from the programme by being deleted from a list.
LADDER: Final[Sequence[Mission]] = (
    Mission(
        rung="M1",
        title="Machined bracket",
        era="I",
        hard='Nothing. The "hello world".',
        spec=_m1_spec(),
        assertions=_M1_ASSERTIONS,
    ),
    Mission(
        rung="M2",
        title="Welded frame / bench",
        era="III",
        hard="Weld sizing, fatigue at joints",
        assembly=_m2_design(),
        assertions=_M2_ASSERTIONS,
        unproven=_M2_UNPROVEN,
    ),
    Mission(
        rung="M3",
        title="Sheet-metal enclosure",
        era="IV",
        hard="Unfolding, bend allowance, DFM",
        folded=_m3_design(),
        assertions=_M3_ASSERTIONS,
        unproven=_M3_UNPROVEN,
    ),
    Mission(
        rung="M4",
        title="Gearbox",
        era="IV",
        hard="Gear geometry, bearings, tolerance stacks, lubrication",
        needs=(
            "E12.3 — standard parts (BOLTS), so bearings are bought not modelled",
            "E12.4 — a parts selection engine",
            "E13.2 — tolerance and GD&T, for the stacks",
        ),
    ),
    Mission(
        rung="M5",
        title="Sheet-metal stamping press",
        era="V",
        hard="Force path, frame stiffness, die set, drive, guarding",
        needs=(
            "E17.3 — sheet metal",
            "E12.3 — standard parts",
            "E13 — design rules, GD&T and cost",
            "E14 — product structure and interface contracts",
        ),
    ),
    Mission(
        rung="M6",
        title="Belt conveyor system",
        era="V",
        hard="Long assemblies, standard parts, modularity, layout",
        # Both of this rung's declared needs — E14.1's product structure and
        # E12.3's standard parts — are complete, so it moved from waiting to
        # built on 2026-09-10. Nothing about it needed E13 or E9, which is why
        # it moves while M4, M5, M7 and M8 do not.
        assembly=_m6_design(),
        assertions=_M6_ASSERTIONS,
        unproven=_M6_UNPROVEN,
    ),
    Mission(
        rung="M7",
        title="6-axis robot arm",
        era="VI",
        hard="Kinematics, dynamic loads, stiffness under motion",
        needs=(
            "E9 — multibody dynamics, for loads that come from the machine moving",
            "E9.3 — motion-range simulation and swept volume",
        ),
    ),
    Mission(
        rung="M8",
        title="Motorcycle chassis + swingarm",
        era="VI",
        hard="Fatigue under real duty cycles, MBD loads, homologation",
        needs=(
            "E8 — fatigue and durability (pyLife)",
            "E9.4 — joint-load extraction feeding FEA",
            "E17.4 — tubular weldments",
        ),
    ),
    Mission(
        rung="M9",
        title="Full vehicle chassis programme",
        era="VII",
        hard="10³–10⁴ parts, teams, change propagation",
        needs=(
            "E14 — decomposition and interface contracts at scale",
            "E15 — throughput, storage and the compute fabric",
        ),
    ),
)


__all__ = [
    "LADDER",
    "AssemblyDesign",
    "AssemblyReport",
    "FoldedDesign",
    "FoldedReport",
    "LadderReport",
    "Mission",
    "MissionOutcome",
    "MissionResult",
    "NestReport",
    "StockSheet",
    "mission",
    "run_ladder",
    "run_mission",
]
