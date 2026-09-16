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
from dataclasses import dataclass, field, replace
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
from app.dynamics.assembly import JointDeclaration, body_name, derive
from app.dynamics.kinematics import evaluate as evaluate_motion
from app.dynamics.reactions import compute as compute_reactions
from app.dynamics.reactions import free_body_check
from app.dynamics.types import (
    GRAVITY_DOWN_MM_S2,
    Driver,
    JointReaction,
    Mechanism,
    MotionPath,
    MotionRange,
    Vec3,
)
from app.parts.bearings import CATALOGUE, Bearing, Duty, Refusal, Selection, select
from app.rules.stackup import Contributor, Method, StackVerdict, check, stack, symmetric
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
    folded_volume_mm3,
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
class MovingDesign:
    """A rung that moves: the product, the joints between its parts, and a motion.

    **The fourth kind of rung, and it exists for the reason the third did.** M3 added
    `folded` because a sheet-metal part is two descriptions that must agree and no
    single one of them is the part. A mechanism is the same shape of problem one level
    up: an arm is a product graph *and* a chain of joints, and the load on its shoulder
    bearing is a consequence of both together. Neither half can state it. A rung that
    was only an `assembly` could check that the arm is the right size and weight and
    would be silent about the only number anybody buys a robot for.

    **The joints are declared against the product graph, not beside it.** Every
    `JointDeclaration.child` is an *occurrence path* in `assembly.structure`, so a
    body's mass and centre of mass come from the same roll-up the geometry claims are
    checked against — `app.dynamics.assembly.derive` refuses a body it cannot weigh,
    because a reaction computed on a partial mass is too small, which is the direction
    every check passes. Rename a component and the mechanism stops resolving rather
    than quietly moving a different part.

    **Inertia is supplied here and is not measured, and that is a real limitation.**
    `derive` will take a `measure_inertia` answering at `Detail.INERTIA`, and building
    one needs `app.assembly.inertia.from_document`, which reads an OCCT document —
    and this package may not import the kernel (the rule that keeps its tests offline
    and under a second). So a mission hands over `inertia_kg_mm2` computed in closed
    form from the same dimensions its specs are drawn from. That is exact for the
    idealised solids a rung is made of and is *not* the kernel's integration of the
    part that was actually built; a rung using it says so in `unproven`, and E9.6 owns
    closing the gap.

    A body left out of `inertia_kg_mm2` is a **point mass**, which costs the rotary
    term of its own spin. `app.dynamics` states that cost rather than hiding it, and a
    rung that leaves a body out inherits the statement.
    """

    assembly: AssemblyDesign
    joints: tuple[JointDeclaration, ...]
    motion: MotionRange
    drivers: tuple[Driver, ...] = ()

    #: Principal inertia about each body's own centre of mass, in world axes at the
    #: assembled pose, keyed by occurrence path. See the class docstring.
    inertia_kg_mm2: Mapping[str, Vec3] | None = None
    gravity_mm_s2: Vec3 = GRAVITY_DOWN_MM_S2

    def __post_init__(self) -> None:
        if not self.joints:
            raise SpecError(
                "A moving rung with no joints is an assembly rung. Declare how the "
                "first moving part is held — `JointDeclaration(parent=None)` is ground "
                "— or give the mission an `assembly` instead."
            )
        driven = {driver.joint for driver in self.drivers}
        declared = {joint.name for joint in self.joints}
        unknown = sorted(driven - declared)
        if unknown:
            raise SpecError(
                f"Drivers name joints this mechanism does not have: {', '.join(unknown)}. "
                f"The joints are {', '.join(sorted(declared))}. A driver on a joint "
                "nobody declared moves nothing and reports no error at run time."
            )
        if not driven:
            raise SpecError(
                "A moving rung needs at least one driver, or nothing moves and every "
                "reaction it reports is the static one wearing a time axis. Drive a "
                "joint, or make this an assembly rung."
            )


@dataclass(frozen=True)
class MotionReport:
    """What running the mechanism over its motion found.

    Everything here is `app.dynamics`' own data except `payload`, the one mapping the
    assertion vocabulary reads — the same split `FoldedReport` keeps.
    """

    mechanism: Mechanism
    path: MotionPath
    reactions: Mapping[str, JointReaction]

    #: The Newton-Euler consistency check: the residual of the whole chain's free-body
    #: balance, in newtons. Not a claim that the answer is right — a claim that it is
    #: self-consistent, which is the strongest thing an inverse-dynamics run can say
    #: about itself.
    free_body_residual_n: float = 0.0

    #: What `derive` had to say about bodies it could only treat as point masses, plus
    #: any caveat a reaction carries about its own moment.
    notes: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)


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

    A rung is buildable exactly when it carries a `spec`, an `assembly`, a `folded`
    **or** a `moving` — one part, a product graph of them, a folded sheet, or a
    product graph with joints between its parts. The four states are kept apart from
    "waiting" by validation rather than by convention, because the failure they guard
    against is a rung drifting into "declared but claiming nothing", which reads as
    coverage and is not.
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

    #: The mechanism, when the rung moves. Mutually exclusive with the three above,
    #: and note that a `MovingDesign` *carries* an `AssemblyDesign` — so a rung
    #: setting `assembly` and `moving` would build one product and check its claims
    #: against the other. `is_assembly` is deliberately False here: a moving rung is
    #: run through the assembly path, but "is this rung an assembly" and "does this
    #: rung move" are different questions and only one of them is about the payload.
    moving: MovingDesign | None = None

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
                ("a mechanism", self.moving),
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
            self.spec is not None
            or self.assembly is not None
            or self.folded is not None
            or self.moving is not None
        )

    @property
    def is_assembly(self) -> bool:
        return self.assembly is not None

    @property
    def is_folded(self) -> bool:
        return self.folded is not None

    @property
    def is_moving(self) -> bool:
        return self.moving is not None

    @property
    def product(self) -> AssemblyDesign | None:
        """The product graph this rung builds, however it was declared.

        A moving rung is an assembly rung with joints on top, and several callers —
        the runner-count check, the gallery — want the product without caring which
        field it arrived in. Without this they each grow the same two-branch read,
        and the day a fifth kind lands one of them is missed.
        """
        if self.assembly is not None:
            return self.assembly
        return None if self.moving is None else self.moving.assembly

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

    #: Everything a moving rung produced beyond its geometry: the derived mechanism,
    #: the motion path, the joint reactions and the free-body residual. The product's
    #: own builds, clash and roll-up are on `assembly`, where an assembly rung's
    #: always are — a moving rung *is* an assembly rung with joints, and putting its
    #: geometry somewhere else would give "what did it build" a second answer.
    motion: MotionReport | None = None

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

    if mission.moving is not None:
        if runner_factory is None:
            raise SpecError(
                f"{mission.rung} moves, so it is a product of "
                f"{len(mission.moving.assembly.parts)} parts and needs one runner "
                "each. Pass runner_factory=, not runner=."
            )
        return _run_moving(mission, mission.moving, runner_factory)

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
    *,
    augment: Callable[[Mapping[str, Any], Mapping[str, Mapping[str, Any]]], Mapping[str, Any]]
    | None = None,
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
    # A moving rung adds its mechanism's numbers here rather than in a second payload,
    # so one `check_assertions` call sees the geometry and the motion together and a
    # claim may compare them — "the shoulder carries more than the arm weighs" needs
    # both and would otherwise have nowhere to live. Default None leaves every other
    # rung's behaviour byte-identical.
    if augment is not None:
        payload = dict(augment(payload, payloads))
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


def _run_moving(
    mission: Mission,
    design: MovingDesign,
    runner_factory: Callable[[], CallRunner],
) -> MissionResult:
    """Build the product, derive the mechanism from it, and move it.

    The order matters and is the order the answers depend on each other: the parts are
    built and weighed first, because `derive` refuses a body it cannot weigh; then the
    mechanism is assembled *from that roll-up*, so the mass a reaction is computed
    against is the mass the geometry claims were checked against and not a second
    number somebody typed; then the motion is evaluated exactly, with no time stepping;
    then the reactions.

    This delegates to `_run_assembly` rather than repeating it. A moving rung's
    geometry claims are an assembly rung's geometry claims — same builds, same clash,
    same roll-up, same contracts — and a second copy of that path would be a second
    place for them to drift.
    """
    captured: list[MotionReport] = []

    def augment(
        payload: Mapping[str, Any], payloads: Mapping[str, Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        report = _evaluate_motion(design, payloads)
        captured.append(report)
        return {**payload, **report.payload}

    result = _run_assembly(mission, design.assembly, runner_factory, augment=augment)
    return result if not captured else replace(result, motion=captured[0])


def _evaluate_motion(
    design: MovingDesign, payloads: Mapping[str, Mapping[str, Any]]
) -> MotionReport:
    """The mechanism, its motion and its reactions, from the built product.

    The measurer reads what the build already reported, exactly as `_run_assembly`'s
    roll-up does and for the same reason: every backend's mutating calls return mass
    and centre of mass, so going back to the document would make this unobtainable on
    a CATIA seat for numbers the seat has already sent.
    """
    derived = derive(
        design.assembly.structure,
        design.joints,
        lambda component: payloads[component],
        drivers=design.drivers,
        gravity_mm_s2=design.gravity_mm_s2,
        inertia_kg_mm2=design.inertia_kg_mm2,
    )
    path = evaluate_motion(derived.mechanism, design.motion)
    reactions = compute_reactions(derived.mechanism, path)
    residual = free_body_check(derived.mechanism, path, reactions)
    notes = (
        *derived.notes,
        *path.warnings,
        *(
            f"{reaction.joint}: {reaction.moment_caveat}"
            for reaction in reactions.values()
            if reaction.moment_caveat
        ),
    )
    return MotionReport(
        mechanism=derived.mechanism,
        path=path,
        reactions=reactions,
        free_body_residual_n=residual,
        notes=notes,
        payload=_motion_payload(derived.mechanism, path, reactions, residual),
    )


def _motion_payload(
    mechanism: Mechanism,
    path: MotionPath,
    reactions: Mapping[str, JointReaction],
    residual_n: float,
) -> dict[str, Any]:
    """The mechanism's numbers, under `motion.`, for the assertion vocabulary.

    **A reaction that is not available publishes nothing, and that is the whole of the
    honesty here.** `JointReaction.available` is false when the chain could not be
    resolved at that joint, and the reaction then carries `unavailable_reason` instead
    of numbers. Writing a zero, or the static value, would turn "we could not compute
    this" into "this joint carries nothing" — which passes every upper-bound claim
    anybody would write. Omitting the key makes the claim UNMEASURED, which is
    `app/design/assertions.py`'s own answer and never a pass. The *count* of such
    joints is published so a rung can assert there are none.

    `peak_reaction_force_n` is over the available joints only, for the same reason, and
    is therefore a lower bound whenever `unavailable_reaction_count` is not zero — a
    rung that wants it to mean what it says asserts the count is zero first.
    """
    available = [reaction for reaction in reactions.values() if reaction.available]
    motion: dict[str, Any] = {
        "step_count": len(path.times_s),
        "duration_s": path.times_s[-1] - path.times_s[0] if path.times_s else 0.0,
        "body_count": len(mechanism.bodies),
        "joint_count": len(mechanism.joints),
        "driver_count": len(mechanism.drivers),
        "total_mass_kg": mechanism.total_mass_kg,
        "total_weight_n": mechanism.total_mass_kg * abs(mechanism.gravity_mm_s2[2]) * 1e-3,
        "free_body_residual_n": residual_n,
        "unavailable_reaction_count": len(reactions) - len(available),
        "warning_count": len(path.warnings),
        "point_mass_count": sum(1 for body in mechanism.bodies if body.is_point_mass),
        "joint": {
            reaction.joint: {
                "peak_force_n": reaction.peak_force_n,
                "peak_moment_n_mm": reaction.peak_moment_n_mm,
            }
            for reaction in available
        },
        "body": {
            name: {
                "peak_acceleration_mm_s2": body.peak_acceleration_mm_s2,
                "travel_mm": body.travel_mm,
            }
            for name, body in path.bodies.items()
        },
    }
    if available:
        motion["peak_reaction_force_n"] = max(r.peak_force_n for r in available)
    # **Nested, not flattened, and this cost a build to learn.** It was written first
    # as flat keys spelled "motion.total_mass_kg", and every motion claim came back
    # NOT CHECKED against a payload that visibly contained them: the assertion
    # resolver reads `.` as a path separator, so it looked for payload["motion"]
    # ["total_mass_kg"] and found a mapping with no "motion" in it at all. Exactly how
    # `_combined_payload` nests `clash`, and for the same reason.
    return {"motion": motion}


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


# ---------------------------------------------------------------------------
# M4 — the gearbox
# ---------------------------------------------------------------------------
#
# A single-stage spur reducer: a pinion and a wheel on two parallel shafts, each
# shaft on two ball bearings, inside a rectangular housing closed by a cover at
# each end. Ratio 2:1, module 3, 24 and 48 teeth.
#
# **Why this rung moves now.** Its declared `needs` were E12.3 (standard parts,
# so bearings are bought rather than modelled), E12.4 (a parts selection engine)
# and E13.2 (tolerance and GD&T, for the stacks). E12 is complete; E13.2 is
# `PARTIAL`, and what is open in it is a **document** — ISO 286's deviation
# tables are not transcribed, and CATIA FTA needs a seat — while the arithmetic
# this rung needs, `app/rules/stackup.py`, is in and tested. A rung held pending
# on the half of a prerequisite it does not use is the ladder M6's entry warns
# about: one that has stopped measuring anything. So it moves, and the half it
# does not have is in `_M4_UNPROVEN` rather than hidden.
#
# **The rung's difficulty is that a gearbox is three claims that must agree and
# only one of them is geometry.**
#
# * **The mesh is arithmetic before it is a shape.** Centre distance is
#   `m(z1 + z2)/2` and nothing about the solids knows that. So the gear pair is
#   declared as an `Interface` — the contract both gears are built from — and the
#   claim that closes it is measured *between the built solids*: the gap between
#   the two root cylinders must be `2.5 m`, which is true exactly when the centre
#   distance is right. A pinion built to a different module cannot be placed at a
#   centre distance that satisfies it.
# * **The axial stack is a chain, and the chain closes on a gap that must not
#   close.** Bearing, spacer, gear hub, bearing, inside the housing's seat span:
#   five dimensions, five tolerances, and an end float of 4 mm nominal. A stack
#   that closes clamps the bearings axially and they fail in months. The stack is
#   `app/rules/stackup.py`'s worst case — exact interval arithmetic, no
#   assumption about a factory — and it is asserted **against the built housing**,
#   so a housing machined to a length the stack was not computed for is a red
#   build rather than a drawing nobody re-checked.
# * **The bearings are bought, and the product says so by refusing to size
#   them.** `app.parts.bearings.select` runs, considers every 6-series bearing
#   that fits the shaft, and returns a `Refusal`: every one of them is missing
#   `C` and `C0`, because a load rating is the maker's number and this library
#   ships ISO 15 boundary dimensions only. That refusal is a *finding of this
#   rung*, measured on 2026-09-16 rather than assumed, and it is why the bearings
#   here are placed by their boundary dimensions and their life is not claimed.
#
# **What a gearbox cannot be on this kernel, and it is the rung's other finding.**
# There is no gear-tooth operation in the OCCT backend. `catia_sketch_gear_profile`
# exists on the CATIA side — it generates a real involute from module, tooth count
# and pressure angle — and the open kernel's `refusals.py` answers it with
# `_not_needed()`, which this rung shows is false: the involute is exactly what the
# open kernel cannot draw and cannot approximate from its primitives, and there is
# no "instead". So each gear is modelled as its **root cylinder** — the blank
# below the tooth roots, which is material that is certainly there — and the tooth
# material the model does not carry is published as a number rather than left to be
# noticed. This is M3's shape exactly: the rung builds what it can, states the
# residual, and names the phase that owns the gap.

#: The gear pair. Module and tooth counts are the design; everything else below
#: is derived from them, because a diameter typed beside a tooth count is a
#: diameter that stops agreeing with it the day somebody changes the ratio.
_M4_MODULE_MM: Final = 3.0
_M4_PINION_TEETH: Final = 24
_M4_WHEEL_TEETH: Final = 48
#: Declared and not used by any geometry here: the root and tip diameters below
#: are the same for any pressure angle, and the flank shape — which is the only
#: thing the angle decides — is what this kernel cannot draw. Carried so the
#: specification is complete and so the gap is legible.
_M4_PRESSURE_ANGLE_DEG: Final = 20.0
_M4_FACE_WIDTH_MM: Final = 30.0

#: Standard full-depth proportions, in module: addendum 1, dedendum 1.25. So the
#: tip circle is `m(z + 2)` and the root circle `m(z - 2.5)`, and the clearance
#: between one gear's root and the other's tip is `0.25 m` — which makes the gap
#: between the two *root* cylinders `2.5 m` at the correct centre distance. That
#: last number is the one this rung measures.
_M4_ADDENDUM_FACTOR: Final = 1.0
_M4_DEDENDUM_FACTOR: Final = 1.25

_M4_CENTRE_DISTANCE_MM: Final = (
    _M4_MODULE_MM * (_M4_PINION_TEETH + _M4_WHEEL_TEETH) / 2.0
)
_M4_PINION_PITCH_MM: Final = _M4_MODULE_MM * _M4_PINION_TEETH
_M4_WHEEL_PITCH_MM: Final = _M4_MODULE_MM * _M4_WHEEL_TEETH
_M4_PINION_TIP_MM: Final = _M4_PINION_PITCH_MM + 2.0 * _M4_ADDENDUM_FACTOR * _M4_MODULE_MM
_M4_WHEEL_TIP_MM: Final = _M4_WHEEL_PITCH_MM + 2.0 * _M4_ADDENDUM_FACTOR * _M4_MODULE_MM
_M4_PINION_ROOT_MM: Final = _M4_PINION_PITCH_MM - 2.0 * _M4_DEDENDUM_FACTOR * _M4_MODULE_MM
_M4_WHEEL_ROOT_MM: Final = _M4_WHEEL_PITCH_MM - 2.0 * _M4_DEDENDUM_FACTOR * _M4_MODULE_MM

#: The gap between the two root cylinders when the centre distance is right.
#: `a - r_f1 - r_f2 = m(z1+z2)/2 - m(z1-2.5)/2 - m(z2-2.5)/2 = 2.5 m`, with the
#: tooth counts cancelling — so this number is a fact about the tooth *system*
#: and not about this particular pair, which is what makes it worth measuring.
_M4_ROOT_CLEARANCE_MM: Final = (
    _M4_CENTRE_DISTANCE_MM - _M4_PINION_ROOT_MM / 2.0 - _M4_WHEEL_ROOT_MM / 2.0
)

#: How far the tip circles overlap: `2 m`. Nothing is built to it — it is here
#: because it is the reason the gears are modelled as root cylinders and not as
#: tip cylinders. A gear pair modelled at its tip diameter **always** interferes,
#: by construction, so a clash check over tip cylinders reports a clash on a
#: correct gearbox and there is no threshold that fixes it.
_M4_TIP_OVERLAP_MM: Final = (
    _M4_PINION_TIP_MM / 2.0 + _M4_WHEEL_TIP_MM / 2.0 - _M4_CENTRE_DISTANCE_MM
)

#: The shafts, and the bearings that carry them. The bearing designations are
#: looked up in `app.parts.bearings.CATALOGUE` rather than typed, so the boundary
#: dimensions this rung places carry ISO 15 as their source and a change to the
#: shipped table moves the gearbox rather than silently disagreeing with it.
_M4_INPUT_SHAFT_MM: Final = 25.0
_M4_OUTPUT_SHAFT_MM: Final = 35.0
_M4_INPUT_BEARING: Final = "6005"
_M4_OUTPUT_BEARING: Final = "6007"


def _m4_bearing(designation: str, bore_mm: float) -> Bearing:
    """One catalogue bearing, checked against the shaft it is being put on.

    The check is here rather than left to the reader because the failure it
    catches is silent: a designation typed one digit out is a bearing that exists,
    has plausible dimensions, and does not fit the shaft — and every number
    downstream of it, the stack included, would be arithmetic about a bearing
    nobody could assemble.
    """
    bearing = CATALOGUE.get(designation)
    if bearing is None:  # pragma: no cover - the shipped table is a constant
        raise SpecError(
            f"M4 asks for bearing {designation!r}, which is not in the shipped "
            "catalogue. The table is ISO 15 boundary dimensions; add the "
            "designation there rather than typing its dimensions here."
        )
    if bearing.bore_mm != bore_mm:
        raise SpecError(
            f"M4 puts {designation} (bore {bearing.bore_mm:g} mm) on a "
            f"{bore_mm:g} mm shaft. A bearing that does not fit its shaft is a "
            "transcription error that reads as a design."
        )
    return bearing


_M4_INPUT_BEARING_PART: Final = _m4_bearing(_M4_INPUT_BEARING, _M4_INPUT_SHAFT_MM)
_M4_OUTPUT_BEARING_PART: Final = _m4_bearing(_M4_OUTPUT_BEARING, _M4_OUTPUT_SHAFT_MM)

#: Spacer sleeves. Each one is what sets its shaft's gear on the mesh centreline
#: and takes up the rest of the seat span; the widths below are what makes both
#: chains close on the same end float, which is why they are different numbers.
_M4_INPUT_SPACER_OD_MM: Final = 32.0
_M4_OUTPUT_SPACER_OD_MM: Final = 45.0

#: The housing's seat span: the distance between the two covers' inner faces,
#: which is the housing tube's own length. It is the first contributor in the
#: axial chain and the one the built solid is checked against.
_M4_SEAT_SPAN_MM: Final = 80.0
_M4_END_FLOAT_MM: Final = 4.0

_M4_OUTPUT_SPACER_MM: Final = (
    _M4_SEAT_SPAN_MM
    - _M4_END_FLOAT_MM
    - 2.0 * _M4_OUTPUT_BEARING_PART.width_mm
    - _M4_FACE_WIDTH_MM
)
_M4_INPUT_SPACER_MM: Final = (
    _M4_SEAT_SPAN_MM
    - _M4_END_FLOAT_MM
    - 2.0 * _M4_INPUT_BEARING_PART.width_mm
    - _M4_FACE_WIDTH_MM
)

#: Tolerances on the five dimensions in the chain. **These are this mission's own
#: drawing and nothing more.** ISO 492's width deviations for a rolling bearing
#: are not transcribed anywhere in this repository — they are a document, the same
#: gap E13.2 carries for ISO 286 — so the bearing figure below is a declaration by
#: this design, not a standard's value, and `_M4_UNPROVEN` says so. A tolerance
#: quietly attributed to a standard nobody read is exactly the failure
#: `app/verify/` exists to prevent.
_M4_SEAT_SPAN_TOL_MM: Final = 0.10
_M4_BEARING_WIDTH_TOL_MM: Final = 0.06
_M4_SPACER_TOL_MM: Final = 0.05
_M4_FACE_WIDTH_TOL_MM: Final = 0.05

#: How much room the gears need inside the housing, measured from the mesh
#: centreline out to the furthest tip circle. Derived, never typed: a housing
#: sized by hand is a housing that stops clearing the wheel the day the ratio
#: changes, and the wheel is the part that grows.
_M4_RADIAL_CLEARANCE_MM: Final = 10.0
_M4_CAVITY_HALF_HEIGHT_MM: Final = (
    _M4_CENTRE_DISTANCE_MM / 2.0
    + max(_M4_PINION_TIP_MM, _M4_WHEEL_TIP_MM) / 2.0
    + _M4_RADIAL_CLEARANCE_MM
)
_M4_CAVITY_HEIGHT_MM: Final = 2.0 * _M4_CAVITY_HALF_HEIGHT_MM
_M4_CAVITY_WIDTH_MM: Final = 2.0 * (
    max(_M4_PINION_TIP_MM, _M4_WHEEL_TIP_MM) / 2.0 + _M4_RADIAL_CLEARANCE_MM
)
_M4_WALL_MM: Final = 15.0
_M4_HOUSING_HEIGHT_MM: Final = _M4_CAVITY_HEIGHT_MM + 2.0 * _M4_WALL_MM
_M4_HOUSING_WIDTH_MM: Final = _M4_CAVITY_WIDTH_MM + 2.0 * _M4_WALL_MM

_M4_COVER_MM: Final = 12.0
#: Clearance on the shaft where it passes through a cover. A cover bore sized to
#: the shaft would put two cylindrical faces exactly on each other, which is a
#: fit question (E13.2's `fits.py`) and not a clash question — and a clash check
#: asked about coincident faces answers neither "clear" nor "interfering".
_M4_COVER_BORE_CLEARANCE_MM: Final = 2.0
_M4_SHAFT_PROTRUSION_MM: Final = 48.0
_M4_SHAFT_LENGTH_MM: Final = (
    _M4_SEAT_SPAN_MM + 2.0 * _M4_COVER_MM + _M4_SHAFT_PROTRUSION_MM
)

#: The axes. The wheel sits below the pinion, which is why the machine's centre of
#: mass is below its own mid-plane — the one claim here that knows which way up
#: the gearbox is, and the one a mirrored placement breaks.
_M4_PINION_AXIS_Z_MM: Final = _M4_CENTRE_DISTANCE_MM / 2.0
_M4_WHEEL_AXIS_Z_MM: Final = -_M4_CENTRE_DISTANCE_MM / 2.0

#: Where each part starts along the shaft axis. The housing's cavity runs from
#: x = 0 to x = the seat span; everything inside is stacked from x = 0, which is
#: the chain the tolerance stack closes.
_M4_HOUSING_X_MM: Final = 0.0
_M4_FRONT_COVER_X_MM: Final = -_M4_COVER_MM
_M4_REAR_COVER_X_MM: Final = _M4_SEAT_SPAN_MM
_M4_INPUT_SHAFT_X_MM: Final = -_M4_COVER_MM - _M4_SHAFT_PROTRUSION_MM
_M4_OUTPUT_SHAFT_X_MM: Final = -_M4_COVER_MM

_M4_STEEL_DENSITY_KG_M3: Final = _M2_DENSITY_KG_M3

#: How far apart two parts are still worth measuring, and it is not zero for M2's
#: reason: the mesh gap is `2.5 m` = 7.5 mm, so a contact-only broad phase throws
#: away the one pair this rung exists to measure and `_boundary_payload` answers
#: the mesh contract UNMEASURED — which is not a pass, but is also not the finding.
_M4_INSPECTION_MM: Final = 12.0


def _annulus_mm2(outer_mm: float, bore_mm: float) -> float:
    return math.pi / 4.0 * (outer_mm**2 - bore_mm**2)


def m4_gear_pair(
    *,
    module_mm: float = _M4_MODULE_MM,
    pinion_teeth: int = _M4_PINION_TEETH,
    wheel_teeth: int = _M4_WHEEL_TEETH,
) -> dict[str, float]:
    """The mesh, as arithmetic, before anything is drawn.

    **Every diameter a spur gear has is `module x (teeth +/- a constant)`**, and
    the centre distance is the mean of the two pitch diameters. None of that is
    geometry: it is the tooth system, it is what decides whether two gears mesh
    at all, and a gearbox whose shafts are bored at anything but `m(z1+z2)/2`
    binds or rattles however well each part is made.

    Returned as a dict for `m6_counts`'s reason — every value here is a length in
    millimetres and two of them transposed would go unnoticed in a tuple.
    """
    if module_mm <= 0:
        raise SpecError(
            "A gear module is a positive length: it is the pitch diameter per "
            "tooth, and every other diameter on the gear is a multiple of it."
        )
    if pinion_teeth < 1 or wheel_teeth < 1:
        raise SpecError(
            f"A gear needs at least one tooth; got {pinion_teeth} and {wheel_teeth}. "
            "A zero-tooth gear has a pitch diameter of zero and a root diameter "
            "below it, which builds as a negative cylinder rather than failing."
        )
    pinion_pitch = module_mm * pinion_teeth
    wheel_pitch = module_mm * wheel_teeth
    addendum = _M4_ADDENDUM_FACTOR * module_mm
    dedendum = _M4_DEDENDUM_FACTOR * module_mm
    centre_distance = (pinion_pitch + wheel_pitch) / 2.0
    pinion_root = pinion_pitch - 2.0 * dedendum
    wheel_root = wheel_pitch - 2.0 * dedendum
    pinion_tip = pinion_pitch + 2.0 * addendum
    wheel_tip = wheel_pitch + 2.0 * addendum
    return {
        "module_mm": module_mm,
        "centre_distance_mm": centre_distance,
        "ratio": wheel_teeth / pinion_teeth,
        "pinion_pitch_diameter_mm": pinion_pitch,
        "wheel_pitch_diameter_mm": wheel_pitch,
        "pinion_tip_diameter_mm": pinion_tip,
        "wheel_tip_diameter_mm": wheel_tip,
        "pinion_root_diameter_mm": pinion_root,
        "wheel_root_diameter_mm": wheel_root,
        # The two numbers the solids can be checked against.
        "root_clearance_mm": centre_distance - pinion_root / 2.0 - wheel_root / 2.0,
        "tip_overlap_mm": pinion_tip / 2.0 + wheel_tip / 2.0 - centre_distance,
    }


def m4_axial_chain(
    *,
    seat_span_mm: float = _M4_SEAT_SPAN_MM,
    bearing_width_mm: float = _M4_OUTPUT_BEARING_PART.width_mm,
    spacer_mm: float = _M4_OUTPUT_SPACER_MM,
    face_width_mm: float = _M4_FACE_WIDTH_MM,
) -> tuple[Contributor, ...]:
    """The output shaft's axial chain, closing on the end float.

    **The signs are the whole of it.** The seat span makes the gap larger and
    every part stacked inside it makes the gap smaller, which is `Contributor`'s
    own convention — `+80` and four negatives closing on `+4`. Written the other
    way round the arithmetic still runs and the stack comes out at 156 mm, which
    is not a number anybody would query.

    Every `source` below says *this design*, because that is what it is. The one
    figure a reader would reasonably expect to come from a standard — the
    bearing's width deviation, ISO 492 — is not transcribed anywhere here, and
    attributing this design's own choice to a standard nobody read is the exact
    move `app/verify/nafems.py` refuses for a benchmark target.
    """
    drawing = "M4 drawing; not a standard's value"
    return (
        symmetric(
            "housing seat span",
            seat_span_mm,
            _M4_SEAT_SPAN_TOL_MM,
            source=drawing,
        ),
        symmetric(
            "bearing width, cover side",
            -bearing_width_mm,
            _M4_BEARING_WIDTH_TOL_MM,
            source=f"{drawing}; ISO 492's width deviations are not transcribed here",
        ),
        symmetric("spacer sleeve", -spacer_mm, _M4_SPACER_TOL_MM, source=drawing),
        symmetric("gear hub width", -face_width_mm, _M4_FACE_WIDTH_TOL_MM, source=drawing),
        symmetric(
            "bearing width, drive side",
            -bearing_width_mm,
            _M4_BEARING_WIDTH_TOL_MM,
            source=f"{drawing}; ISO 492's width deviations are not transcribed here",
        ),
    )


def m4_end_float() -> StackVerdict:
    """Can the axial chain close on a gearbox built to this drawing?

    **Worst case, and deliberately not RSS.** `stackup.stack` will not produce a
    statistical number without somebody's name against the independence
    assumption, and rightly: this is a five-dimension chain in which two of the
    contributors are the same bearing from the same batch, which is precisely the
    correlation an RSS band assumes away. Worst case is exact interval arithmetic
    and needs no signature, so the rung uses the method a regression suite can
    actually stand behind.

    `at_least_mm=0.0` is the claim: **the float may be small and may not close.**
    A stack that goes negative is not a tight fit, it is a pair of bearings
    clamped axially through their balls, which is a failure measured in months.
    """
    return check(
        stack(m4_axial_chain(), method=Method.WORST_CASE),
        name="output shaft end float",
        at_least_mm=0.0,
    )


#: Every occurrence in the gearbox, as `(volume_mm3, z_of_its_centroid)`. The
#: closed form below is a sum over this table and the product graph is built
#: separately, so the two disagree whenever a part is placed that the arithmetic
#: does not know about — which is M6's rung applied to a machine whose parts are
#: all different rather than all the same.
def _m4_volumes() -> dict[str, float]:
    """Each component's volume from its own formula. Boxes and annuli, exactly."""
    cover_bore_in = _M4_INPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM
    cover_bore_out = _M4_OUTPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM
    return {
        "housing": (
            _M4_HOUSING_WIDTH_MM * _M4_HOUSING_HEIGHT_MM
            - _M4_CAVITY_WIDTH_MM * _M4_CAVITY_HEIGHT_MM
        )
        * _M4_SEAT_SPAN_MM,
        "cover": (
            _M4_HOUSING_WIDTH_MM * _M4_HOUSING_HEIGHT_MM
            - math.pi / 4.0 * cover_bore_in**2
            - math.pi / 4.0 * cover_bore_out**2
        )
        * _M4_COVER_MM,
        "pinion": _annulus_mm2(_M4_PINION_ROOT_MM, _M4_INPUT_SHAFT_MM)
        * _M4_FACE_WIDTH_MM,
        "wheel": _annulus_mm2(_M4_WHEEL_ROOT_MM, _M4_OUTPUT_SHAFT_MM) * _M4_FACE_WIDTH_MM,
        "input_shaft": math.pi / 4.0 * _M4_INPUT_SHAFT_MM**2 * _M4_SHAFT_LENGTH_MM,
        "output_shaft": math.pi / 4.0 * _M4_OUTPUT_SHAFT_MM**2 * _M4_SHAFT_LENGTH_MM,
        "input_bearing": _annulus_mm2(
            _M4_INPUT_BEARING_PART.outer_diameter_mm, _M4_INPUT_SHAFT_MM
        )
        * _M4_INPUT_BEARING_PART.width_mm,
        "output_bearing": _annulus_mm2(
            _M4_OUTPUT_BEARING_PART.outer_diameter_mm, _M4_OUTPUT_SHAFT_MM
        )
        * _M4_OUTPUT_BEARING_PART.width_mm,
        "input_spacer": _annulus_mm2(_M4_INPUT_SPACER_OD_MM, _M4_INPUT_SHAFT_MM)
        * _M4_INPUT_SPACER_MM,
        "output_spacer": _annulus_mm2(_M4_OUTPUT_SPACER_OD_MM, _M4_OUTPUT_SHAFT_MM)
        * _M4_OUTPUT_SPACER_MM,
    }


#: Component -> the z of each of its occurrences' centroids. Every part here is a
#: prism or a cylinder about its own axis, so its centroid sits on that axis and
#: the only coordinate that varies is z. A cover is the exception and is handled
#: where it is computed: its two bores are different sizes at opposite heights, so
#: removing them moves the cover's centroid off the mid-plane.
_M4_OCCURRENCE_AXES: Final[tuple[tuple[str, float], ...]] = (
    ("housing", 0.0),
    ("cover", 0.0),
    ("cover", 0.0),
    ("pinion", _M4_PINION_AXIS_Z_MM),
    ("wheel", _M4_WHEEL_AXIS_Z_MM),
    ("input_shaft", _M4_PINION_AXIS_Z_MM),
    ("output_shaft", _M4_WHEEL_AXIS_Z_MM),
    ("input_bearing", _M4_PINION_AXIS_Z_MM),
    ("input_bearing", _M4_PINION_AXIS_Z_MM),
    ("output_bearing", _M4_WHEEL_AXIS_Z_MM),
    ("output_bearing", _M4_WHEEL_AXIS_Z_MM),
    ("input_spacer", _M4_PINION_AXIS_Z_MM),
    ("output_spacer", _M4_WHEEL_AXIS_Z_MM),
)


def _m4_cover_centroid_z_mm() -> float:
    """Where a cover's own centroid sits, which is not on its mid-plane.

    The plate is symmetric; the two bores are not. A 37 mm bore below the
    centreline and a 27 mm bore above it remove more material from the bottom
    half, so the cover's centroid rises. Small — well under a millimetre — and it
    is in the closed form because a centre-of-mass claim accurate to a tolerance
    it does not meet is a claim that gets its tolerance widened until it passes.
    """
    bore_in = _M4_INPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM
    bore_out = _M4_OUTPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM
    plate = _M4_HOUSING_WIDTH_MM * _M4_HOUSING_HEIGHT_MM
    hole_in = math.pi / 4.0 * bore_in**2
    hole_out = math.pi / 4.0 * bore_out**2
    moment = -hole_in * _M4_PINION_AXIS_Z_MM - hole_out * _M4_WHEEL_AXIS_Z_MM
    return moment / (plate - hole_in - hole_out)


def m4_mass_kg() -> float:
    """The closed form: every occurrence's volume, summed, times the density.

    Held apart from the product graph and compared against the roll-up by an
    assertion, for M6's reason. The failure it catches on a machine of thirteen
    different parts is not a count that drifted — it is a part placed twice, or a
    diameter changed in one of the two places it is written.
    """
    volumes = _m4_volumes()
    total = sum(volumes[component] for component, _ in _M4_OCCURRENCE_AXES)
    return total * 1e-9 * _M4_STEEL_DENSITY_KG_M3


def m4_centre_of_mass_z_mm() -> float:
    """Where the gearbox balances, vertically. Negative: the wheel is below.

    **The one number here that knows which way up the machine is.** Every mass,
    every diameter and every clearance in this rung is unchanged by swapping the
    two shafts; this is not, because the wheel's blank is nearly five times the
    pinion's and it hangs below the mesh centreline.
    """
    volumes = _m4_volumes()
    cover_z = _m4_cover_centroid_z_mm()
    moment = 0.0
    total = 0.0
    for component, axis_z in _M4_OCCURRENCE_AXES:
        volume = volumes[component]
        centroid = cover_z if component == "cover" else axis_z
        moment += volume * centroid
        total += volume
    return moment / total


def m4_tooth_volume_not_modelled_mm3() -> float:
    """The material between root and tip that the blanks do not carry.

    **An exact bound, not an estimate of the teeth.** The true gear sits somewhere
    between the root cylinder — all tooth space removed — and the tip cylinder,
    with none removed; this is the whole of the difference, so the gearbox's real
    mass is above what this rung reports by at most this much. It is published
    rather than estimated because the obvious estimate (teeth and spaces are equal
    at the pitch circle, so take half) is a rule of thumb, and a rule of thumb
    dressed as a volume is the kind of number that ends up in a quotation.
    """
    pinion = _annulus_mm2(_M4_PINION_TIP_MM, _M4_PINION_ROOT_MM) * _M4_FACE_WIDTH_MM
    wheel = _annulus_mm2(_M4_WHEEL_TIP_MM, _M4_WHEEL_ROOT_MM) * _M4_FACE_WIDTH_MM
    return pinion + wheel


def m4_bearing_verdict(
    *,
    radial_n: float = 2_500.0,
    speed_rpm: float = 480.0,
    required_life_hours: float = 20_000.0,
) -> Selection | Refusal:
    """Ask the parts library to size the output bearing, and publish the answer.

    **This is called for the answer it gives, not for the bearing it returns.**
    Every 6-series bearing that fits a 35 mm shaft is in the shipped table with
    its ISO 15 boundary dimensions and without `C` or `C0`, because a load rating
    is the maker's number and differs between makers for the same envelope. So
    this returns a `Refusal` naming every candidate it could not size, the rung
    carries the refusal instead of a life, and `_M4_UNPROVEN` names the phase that
    owns the missing data. A mission that quietly skipped the call would be a
    gearbox whose bearings nobody had even asked about.
    """
    return select(
        Duty(
            radial_n=radial_n,
            speed_rpm=speed_rpm,
            required_life_hours=required_life_hours,
        ),
        bore_mm=_M4_OUTPUT_SHAFT_MM,
    )


def _m4_gear_parameters() -> ParameterSet:
    """The mesh, as the numbers both gears are built from.

    On the interface rather than in either gear's spec, for `_M2_JOINT`'s reason
    and more strongly: two gears drawn from two copies of the module is a pair
    that stops meshing when one copy is edited, and the machine still builds.
    """
    pair = m4_gear_pair()
    return ParameterSet.of(
        [
            Parameter(
                "module_mm",
                Unit.MM,
                value=pair["module_mm"],
                description="Pitch diameter per tooth. Both gears share it or they do not mesh.",
            ),
            Parameter(
                "pinion_teeth", Unit.NONE, value=float(_M4_PINION_TEETH)
            ),
            Parameter("wheel_teeth", Unit.NONE, value=float(_M4_WHEEL_TEETH)),
            Parameter(
                "pressure_angle_deg",
                Unit.NONE,
                value=_M4_PRESSURE_ANGLE_DEG,
                description=(
                    "Declared and unused: it decides the flank shape, which is the "
                    "one thing this kernel cannot draw."
                ),
            ),
            Parameter("face_width_mm", Unit.MM, value=_M4_FACE_WIDTH_MM),
            Parameter(
                "centre_distance_mm",
                Unit.MM,
                value=pair["centre_distance_mm"],
                description="m(z1 + z2)/2. What the housing's bores must be bored at.",
            ),
            Parameter(
                "pinion_root_diameter_mm",
                Unit.MM,
                value=pair["pinion_root_diameter_mm"],
                description="The blank below the tooth roots — what is actually built.",
            ),
            Parameter(
                "wheel_root_diameter_mm", Unit.MM, value=pair["wheel_root_diameter_mm"]
            ),
            Parameter(
                "root_clearance_mm",
                Unit.MM,
                value=pair["root_clearance_mm"],
                description=(
                    "2.5 x module, whatever the tooth counts. The gap between the "
                    "two blanks, and the measurement that says the centre distance "
                    "is right."
                ),
            ),
        ]
    )


def _m4_gear_spec(name: str, *, root_diameter: str, bore_mm: float) -> DesignSpec:
    """One gear, as its root cylinder with the shaft bore through it.

    **What is built is the blank, and the rung says so everywhere it can.** The
    teeth are not here: there is no gear-tooth operation in the open kernel, the
    involute cannot be approximated from the primitives that are there, and
    `catia_sketch_gear_profile` — which does generate one — is refused by
    `app/kernel/occt/refusals.py` as "not needed". This rung is the case that
    needs it.

    The diameter comes from the interface, so a gear built to a root diameter that
    does not belong to the pair's module is a compile error at the boundary rather
    than a mesh that binds.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        description="Spur gear blank: the root cylinder, not the toothed gear.",
        parameters=[
            Parameter("bore_mm", Unit.MM, value=bore_mm, description="Shaft seat."),
        ],
        features=[
            FeatureSpec("gear.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "gear.outline",
                "catia_sketch_circle",
                {"sketch": ref("gear.profile"), "diameter_mm": expr(root_diameter)},
            ),
            FeatureSpec(
                "gear.body",
                "catia_pad",
                {"sketch": ref("gear.profile"), "length_mm": expr("face_width_mm")},
                note="The blank, to the face width the axial chain was closed on.",
            ),
            FeatureSpec("gear.bore_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "gear.bore_outline",
                "catia_sketch_circle",
                {"sketch": ref("gear.bore_sketch"), "diameter_mm": expr("bore_mm")},
            ),
            FeatureSpec(
                "gear.bore",
                "catia_pocket",
                {"sketch": ref("gear.bore_sketch"), "limit": "up_to_last"},
                note="The shaft seat. No keyway: that is a feature nobody has asked for yet.",
            ),
        ],
    )


def _m4_sleeve_spec(
    name: str, *, outer_mm: float, bore_mm: float, length_mm: float, description: str
) -> DesignSpec:
    """A tube: bearing, spacer, or anything else that is an annulus.

    One function for three components because they are one shape. The bearings
    are **bought parts modelled as the envelope they occupy** — M6's roller
    exactly — and the envelope *over*-states a bearing's mass, because a rolling
    bearing is two rings, a set of balls and a cage inside that envelope and is
    mostly air. The gear blanks under-state theirs. Neither is corrected, because
    a correction would be a number nobody measured; both are in `_M4_UNPROVEN`.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        description=description,
        parameters=[
            Parameter("outer_mm", Unit.MM, value=outer_mm),
            Parameter("bore_mm", Unit.MM, value=bore_mm),
            Parameter("length_mm", Unit.MM, value=length_mm),
        ],
        features=[
            FeatureSpec("sleeve.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "sleeve.outline",
                "catia_sketch_circle",
                {"sketch": ref("sleeve.profile"), "diameter_mm": expr("outer_mm")},
            ),
            FeatureSpec(
                "sleeve.body",
                "catia_pad",
                {"sketch": ref("sleeve.profile"), "length_mm": expr("length_mm")},
            ),
            FeatureSpec("sleeve.bore_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "sleeve.bore_outline",
                "catia_sketch_circle",
                {"sketch": ref("sleeve.bore_sketch"), "diameter_mm": expr("bore_mm")},
            ),
            FeatureSpec(
                "sleeve.bore",
                "catia_pocket",
                {"sketch": ref("sleeve.bore_sketch"), "limit": "up_to_last"},
            ),
        ],
    )


def _m4_shaft_spec(name: str, diameter_mm: float) -> DesignSpec:
    """A plain shaft: one cylinder, no steps and no keyways.

    A real gearbox shaft is stepped, and the steps are what locate the bearings
    axially — which is why this one is not, and why the spacer sleeves exist
    instead. A step is a second pad on an offset plane, and the chain it would
    replace is exactly the chain `m4_axial_chain` closes; modelling it both ways
    would put the same tolerance in two places.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        description="Plain shaft: located by spacer sleeves, not by its own shoulders.",
        parameters=[
            Parameter("diameter_mm", Unit.MM, value=diameter_mm),
            Parameter("length_mm", Unit.MM, value=_M4_SHAFT_LENGTH_MM),
        ],
        features=[
            FeatureSpec("shaft.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "shaft.outline",
                "catia_sketch_circle",
                {"sketch": ref("shaft.profile"), "diameter_mm": expr("diameter_mm")},
            ),
            FeatureSpec(
                "shaft.body",
                "catia_pad",
                {"sketch": ref("shaft.profile"), "length_mm": expr("length_mm")},
            ),
        ],
    )


def _m4_housing_spec(seat_span_mm: float = _M4_SEAT_SPAN_MM) -> DesignSpec:
    """The housing: a rectangular tube, open at both ends, closed by the covers.

    **The sketch's own axes are not the machine's, and the mapping is the trap.**
    Every part here is built along its own +Z and turned a quarter turn about Y to
    lay it along the machine's X, which sends the sketch's +X to the world's *-Z*.
    So the rectangle's `width_mm` is the housing's height in the world and its
    `height_mm` is the width. Measured on the real kernel rather than reasoned
    about, because this is the same asymmetry `app/render/project.py` and
    `occt/sheetmetal.py` each document from their own side, and it produces a
    machine that is correct in every dimension and lying on its side.
    """
    return DesignSpec.of(
        "M4 housing",
        material="steel-1018",
        description="Gearbox housing: a rectangular tube on the shaft axis.",
        parameters=[
            Parameter("tall_mm", Unit.MM, value=_M4_HOUSING_HEIGHT_MM),
            Parameter("across_mm", Unit.MM, value=_M4_HOUSING_WIDTH_MM),
            Parameter("cavity_tall_mm", Unit.MM, value=_M4_CAVITY_HEIGHT_MM),
            Parameter("cavity_across_mm", Unit.MM, value=_M4_CAVITY_WIDTH_MM),
            Parameter(
                "seat_span_mm",
                Unit.MM,
                value=seat_span_mm,
                description="Cover face to cover face: the first link in the axial chain.",
            ),
        ],
        features=[
            FeatureSpec("housing.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "housing.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("housing.profile"),
                    # Sketch +X becomes world -Z once the part is turned; see the
                    # docstring. The names say which world direction each is.
                    "width_mm": expr("tall_mm"),
                    "height_mm": expr("across_mm"),
                },
            ),
            FeatureSpec(
                "housing.body",
                "catia_pad",
                {"sketch": ref("housing.profile"), "length_mm": expr("seat_span_mm")},
                note="The tube's length is the seat span the tolerance stack closes on.",
            ),
            FeatureSpec("housing.cavity_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "housing.cavity_outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("housing.cavity_sketch"),
                    "width_mm": expr("cavity_tall_mm"),
                    "height_mm": expr("cavity_across_mm"),
                },
            ),
            FeatureSpec(
                "housing.cavity",
                "catia_pocket",
                {"sketch": ref("housing.cavity_sketch"), "limit": "up_to_last"},
                note="Through, both ends: the covers close it and locate the bearings.",
            ),
        ],
    )


def _m4_cover_spec() -> DesignSpec:
    """One cover, with a bore for each shaft. Two occurrences of one design.

    **The bores are drawn at negative sketch x for the shaft that runs high.**
    The quarter turn about Y sends sketch +X to world -Z, so the input shaft —
    which sits above the mesh centreline in the machine — is bored below the
    centreline in the sketch. Get it the wrong way round and the covers build, the
    mass is unchanged, every dimension is right, and each shaft runs through solid
    plate: the clash check is what catches it, which is why `clash.clash_count`
    is one of this rung's claims.
    """
    return DesignSpec.of(
        "M4 cover",
        material="steel-1018",
        description="End cover: closes the housing and locates the bearings axially.",
        parameters=[
            Parameter("tall_mm", Unit.MM, value=_M4_HOUSING_HEIGHT_MM),
            Parameter("across_mm", Unit.MM, value=_M4_HOUSING_WIDTH_MM),
            Parameter("thickness_mm", Unit.MM, value=_M4_COVER_MM),
            Parameter(
                "input_bore_mm",
                Unit.MM,
                value=_M4_INPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM,
            ),
            Parameter(
                "output_bore_mm",
                Unit.MM,
                value=_M4_OUTPUT_SHAFT_MM + _M4_COVER_BORE_CLEARANCE_MM,
            ),
        ],
        features=[
            FeatureSpec("cover.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "cover.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("cover.profile"),
                    "width_mm": expr("tall_mm"),
                    "height_mm": expr("across_mm"),
                },
            ),
            FeatureSpec(
                "cover.body",
                "catia_pad",
                {"sketch": ref("cover.profile"), "length_mm": expr("thickness_mm")},
            ),
            FeatureSpec("cover.input_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "cover.input_outline",
                "catia_sketch_circle",
                {
                    "sketch": ref("cover.input_sketch"),
                    "diameter_mm": expr("input_bore_mm"),
                    "at": [-_M4_PINION_AXIS_Z_MM, 0.0],
                },
            ),
            FeatureSpec(
                "cover.input_bore",
                "catia_pocket",
                {"sketch": ref("cover.input_sketch"), "limit": "up_to_last"},
            ),
            FeatureSpec("cover.output_sketch", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "cover.output_outline",
                "catia_sketch_circle",
                {
                    "sketch": ref("cover.output_sketch"),
                    "diameter_mm": expr("output_bore_mm"),
                    "at": [-_M4_WHEEL_AXIS_Z_MM, 0.0],
                },
            ),
            FeatureSpec(
                "cover.output_bore",
                "catia_pocket",
                {"sketch": ref("cover.output_sketch"), "limit": "up_to_last"},
            ),
        ],
    )


#: The mesh, as a contract. Provider and consumer are the two gears, and the one
#: claim that matters is measured *between* them: `boundary.minimum_clearance_mm`
#: is the gap between the built root cylinders, and it is `2.5 x module` exactly
#: when the centre distance is `m(z1 + z2)/2`.
_M4_MESH: Final = Interface(
    name="spur gear mesh",
    provider="pinion",
    consumer="wheel",
    parameters=_m4_gear_parameters(),
    claims=(
        Assertion(
            name="the pinion is the blank its tooth count and module give",
            measure="provider.bounding_box_mm.size[0]",
            comparison="==",
            bound="=pinion_root_diameter_mm",
            tolerance=1e-3,
            note="A gear whose blank is not m(z - 2.5) across cannot carry the teeth it claims.",
        ),
        Assertion(
            name="the wheel is the blank its tooth count and module give",
            measure="consumer.bounding_box_mm.size[0]",
            comparison="==",
            bound="=wheel_root_diameter_mm",
            tolerance=1e-3,
            note=(
                "Checked on both sides rather than inferred from one: two gears built "
                "from one module is the thing the contract exists to guarantee."
            ),
        ),
        Assertion(
            name="both gears are the face width the axial chain was closed on",
            measure="provider.bounding_box_mm.size[2]",
            comparison="==",
            bound="=face_width_mm",
            tolerance=1e-3,
            note=(
                "The gear hub is one of the five dimensions in the stack. A hub built "
                "wider than the chain assumed closes the end float with nothing red."
            ),
        ),
        Assertion(
            name="the wheel is the face width the axial chain was closed on",
            measure="consumer.bounding_box_mm.size[2]",
            comparison="==",
            bound="=face_width_mm",
            tolerance=1e-3,
        ),
        Assertion(
            name="the blanks stand 2.5 modules apart, so the centre distance is right",
            # `minimum_clearance_mm`, unprefixed. `contracts.measurements()` merges
            # the boundary's own numbers at the top level and namespaces only the
            # two parties — measured on 2026-09-16 by writing it the other way and
            # watching the claim come back NOT CHECKED, which is not a pass and is
            # also not the finding anybody wants from a gear mesh.
            measure="minimum_clearance_mm",
            comparison="==",
            bound="=root_clearance_mm",
            tolerance=1e-3,
            note=(
                "The rung's mesh claim, measured on the real solids rather than "
                "computed from the placement that put them there. m(z1+z2)/2 minus "
                "the two root radii is 2.5m with the tooth counts cancelling, so a "
                "pair bored at the wrong centre distance fails here whatever the "
                "ratio — and a gearbox that binds or rattles is exactly this number "
                "wrong by a fraction of a millimetre."
            ),
        ),
    ),
)


def _m4_structure(
    *,
    centre_distance_mm: float = _M4_CENTRE_DISTANCE_MM,
    upside_down: bool = False,
    spare_bearing_at_mm: float | None = None,
) -> ProductStructure:
    """The gearbox as a graph. X along the shafts, Z up, Y across.

    Every cylindrical part is built along its own +Z and laid along the machine's
    +X by a quarter turn about Y — `compose(at(...), turned(...))`, translation
    after rotation, the idiom M6's stringers use.

    **The three arguments exist so a test can build it wrong**, M6's reason: a
    guard nobody has seen fail is a guard nobody has verified. `centre_distance_mm`
    bores the shafts at a spacing the tooth counts do not give;`upside_down` puts
    the wheel on top, which changes nothing any other claim here measures; and
    `spare_bearing_at_mm` places one more bearing on the protruding output shaft,
    where it fouls nothing — a part in the graph that the bill of materials does
    not know about, which is the failure M6's pair of counting claims exists for.
    """
    turn = turned((0.0, 1.0, 0.0), math.pi / 2.0)
    high, low = centre_distance_mm / 2.0, -centre_distance_mm / 2.0
    pinion_axis_z, wheel_axis_z = (low, high) if upside_down else (high, low)
    builder = StructureBuilder()
    builder.define("gearbox", description="Single-stage spur reducer, 2:1.")
    builder.define(
        "housing", design="M4 housing", material="steel-1018",
        description="Rectangular tube on the shaft axis.",
    )
    builder.define(
        "cover", design="M4 cover", material="steel-1018",
        description="End cover, bored for both shafts.",
    )
    builder.define(
        "pinion", design="M4 pinion", material="steel-1018",
        description=f"{_M4_PINION_TEETH} teeth, module {_M4_MODULE_MM:g} — blank only.",
    )
    builder.define(
        "wheel", design="M4 wheel", material="steel-1018",
        description=f"{_M4_WHEEL_TEETH} teeth, module {_M4_MODULE_MM:g} — blank only.",
    )
    builder.define(
        "input_shaft", design="M4 input shaft", material="steel-1018",
        description="Drive shaft.",
    )
    builder.define(
        "output_shaft", design="M4 output shaft", material="steel-1018",
        description="Driven shaft.",
    )
    builder.define(
        "input_bearing", design="M4 input bearing", material="steel-1018",
        description=f"Bought {_M4_INPUT_BEARING} ball bearing: envelope only.",
    )
    builder.define(
        "output_bearing", design="M4 output bearing", material="steel-1018",
        description=f"Bought {_M4_OUTPUT_BEARING} ball bearing: envelope only.",
    )
    builder.define(
        "input_spacer", design="M4 input spacer", material="steel-1018",
        description="Sleeve setting the pinion on the mesh centreline.",
    )
    builder.define(
        "output_spacer", design="M4 output spacer", material="steel-1018",
        description="Sleeve setting the wheel on the mesh centreline.",
    )

    builder.add(
        "gearbox", "housing",
        placement=compose(at(_M4_HOUSING_X_MM, 0.0, 0.0), turn),
        note="The tube, from the front cover face to the rear one.",
    )
    for label, x in (("front", _M4_FRONT_COVER_X_MM), ("rear", _M4_REAR_COVER_X_MM)):
        builder.add(
            "gearbox", "cover",
            placement=compose(at(x, 0.0, 0.0), turn),
            note=f"{label.title()} cover.",
        )

    builder.add(
        "gearbox", "input_shaft",
        placement=compose(at(_M4_INPUT_SHAFT_X_MM, 0.0, pinion_axis_z), turn),
        note="Drive end out of the front cover.",
    )
    builder.add(
        "gearbox", "output_shaft",
        placement=compose(at(_M4_OUTPUT_SHAFT_X_MM, 0.0, wheel_axis_z), turn),
        note="Driven end out of the rear cover — the opposite end from the input.",
    )

    # The two chains, stacked from x = 0 in the order the tolerance stack names.
    input_run = (
        ("input_bearing", _M4_INPUT_BEARING_PART.width_mm),
        ("input_spacer", _M4_INPUT_SPACER_MM),
        ("pinion", _M4_FACE_WIDTH_MM),
        ("input_bearing", _M4_INPUT_BEARING_PART.width_mm),
    )
    output_run = (
        ("output_bearing", _M4_OUTPUT_BEARING_PART.width_mm),
        ("output_spacer", _M4_OUTPUT_SPACER_MM),
        ("wheel", _M4_FACE_WIDTH_MM),
        ("output_bearing", _M4_OUTPUT_BEARING_PART.width_mm),
    )
    for run, axis_z in ((input_run, pinion_axis_z), (output_run, wheel_axis_z)):
        x = 0.0
        for component, width in run:
            builder.add(
                "gearbox", component,
                placement=compose(at(x, 0.0, axis_z), turn),
                note=f"{component.replace('_', ' ')} at {x:g} mm along the seat span.",
            )
            x += width
    if spare_bearing_at_mm is not None:
        builder.add(
            "gearbox", "output_bearing",
            placement=compose(at(spare_bearing_at_mm, 0.0, wheel_axis_z), turn),
            note="A bearing nobody counted. Only a test places this.",
        )
    return builder.build("gearbox")


def _m4_design(
    *,
    seat_span_mm: float = _M4_SEAT_SPAN_MM,
    structure: ProductStructure | None = None,
) -> AssemblyDesign:
    """The gearbox: the graph, ten part designs, the mesh contract and the numbers.

    `seat_span_mm` machines the housing to a length the tolerance stack was not
    closed on — the one break that exercises the stack claim, because the stack's
    own numbers stay where they are and only the metal moves.
    """
    from app.assembly.contracts import bind_into

    float_verdict = m4_end_float()
    assert float_verdict.result.maximum_mm is not None  # noqa: S101 - worst case always resolves
    stacked_maximum = _M4_SEAT_SPAN_MM + _M4_SEAT_SPAN_TOL_MM - float_verdict.result.minimum_mm
    return AssemblyDesign(
        structure=_m4_structure() if structure is None else structure,
        parts={
            "housing": _m4_housing_spec(seat_span_mm),
            "cover": _m4_cover_spec(),
            "pinion": bind_into(
                _M4_MESH,
                _m4_gear_spec(
                    "M4 pinion",
                    root_diameter="pinion_root_diameter_mm",
                    bore_mm=_M4_INPUT_SHAFT_MM,
                ),
            ),
            "wheel": bind_into(
                _M4_MESH,
                _m4_gear_spec(
                    "M4 wheel",
                    root_diameter="wheel_root_diameter_mm",
                    bore_mm=_M4_OUTPUT_SHAFT_MM,
                ),
            ),
            "input_shaft": _m4_shaft_spec("M4 input shaft", _M4_INPUT_SHAFT_MM),
            "output_shaft": _m4_shaft_spec("M4 output shaft", _M4_OUTPUT_SHAFT_MM),
            "input_bearing": _m4_sleeve_spec(
                "M4 input bearing",
                outer_mm=_M4_INPUT_BEARING_PART.outer_diameter_mm,
                bore_mm=_M4_INPUT_SHAFT_MM,
                length_mm=_M4_INPUT_BEARING_PART.width_mm,
                description=(
                    f"Bought {_M4_INPUT_BEARING}: the envelope it occupies, not the "
                    "maker's geometry."
                ),
            ),
            "output_bearing": _m4_sleeve_spec(
                "M4 output bearing",
                outer_mm=_M4_OUTPUT_BEARING_PART.outer_diameter_mm,
                bore_mm=_M4_OUTPUT_SHAFT_MM,
                length_mm=_M4_OUTPUT_BEARING_PART.width_mm,
                description=(
                    f"Bought {_M4_OUTPUT_BEARING}: the envelope it occupies, not the "
                    "maker's geometry."
                ),
            ),
            "input_spacer": _m4_sleeve_spec(
                "M4 input spacer",
                outer_mm=_M4_INPUT_SPACER_OD_MM,
                bore_mm=_M4_INPUT_SHAFT_MM,
                length_mm=_M4_INPUT_SPACER_MM,
                description="Spacer sleeve, pinion side.",
            ),
            "output_spacer": _m4_sleeve_spec(
                "M4 output spacer",
                outer_mm=_M4_OUTPUT_SPACER_OD_MM,
                bore_mm=_M4_OUTPUT_SHAFT_MM,
                length_mm=_M4_OUTPUT_SPACER_MM,
                description="Spacer sleeve, wheel side.",
            ),
        },
        interfaces=(_M4_MESH,),
        clearance_mm=_M4_INSPECTION_MM,
        parameters=ParameterSet.of(
            [
                *_m4_gear_parameters(),
                Parameter(
                    "seat_span_mm",
                    Unit.MM,
                    value=_M4_SEAT_SPAN_MM,
                    description="Cover face to cover face.",
                ),
                Parameter(
                    "stacked_maximum_mm",
                    Unit.MM,
                    value=stacked_maximum,
                    description=(
                        "The widest the four parts inside the housing can be, all at "
                        "their limits at once. The seat span must be at least this or "
                        "the end float closes."
                    ),
                ),
                Parameter(
                    "end_float_nominal_mm", Unit.MM, value=float_verdict.result.nominal_mm
                ),
                Parameter(
                    "end_float_worst_case_min_mm",
                    Unit.MM,
                    value=float_verdict.result.minimum_mm or 0.0,
                ),
                Parameter("housing_height_mm", Unit.MM, value=_M4_HOUSING_HEIGHT_MM),
                Parameter("housing_width_mm", Unit.MM, value=_M4_HOUSING_WIDTH_MM),
                Parameter(
                    "overall_length_mm",
                    Unit.MM,
                    value=_M4_SHAFT_LENGTH_MM + _M4_SHAFT_PROTRUSION_MM,
                    description=(
                        "Front shaft end to rear shaft end. **Not the shaft length**: "
                        "the two shafts protrude at opposite ends, so the machine is "
                        "one shaft plus one more protrusion. Written as the shaft "
                        "length first, and the envelope claim caught it."
                    ),
                ),
                Parameter(
                    "occurrence_count",
                    Unit.NONE,
                    value=float(len(_M4_OCCURRENCE_AXES)),
                    description="What the bill of materials says is in this gearbox.",
                ),
                Parameter(
                    "mass_closed_form_kg",
                    Unit.KG,
                    value=m4_mass_kg(),
                    description=(
                        "Summed over the occurrence table, not read off the graph. "
                        "The assertion that this equals the roll-up is the rung."
                    ),
                ),
                Parameter(
                    "centre_of_mass_z_mm",
                    Unit.MM,
                    value=m4_centre_of_mass_z_mm(),
                    description=(
                        "Negative: the wheel is the heavy blank and it hangs below "
                        "the mesh centreline. The one number that knows which way up "
                        "the machine is."
                    ),
                ),
                Parameter(
                    "tooth_volume_not_modelled_mm3",
                    Unit.MM3,
                    value=m4_tooth_volume_not_modelled_mm3(),
                    description=(
                        "Material between root and tip that the blanks do not carry. "
                        "An exact bound on how much this gearbox under-reports its "
                        "own gears, not an estimate of the teeth."
                    ),
                ),
            ]
        ),
    )


_M4_ASSERTIONS: Final = (
    Assertion(
        name="the roll-up equals the closed form over every occurrence",
        measure="mass_kg",
        comparison="==",
        bound="=mass_closed_form_kg",
        tolerance=1e-6,
        note=(
            "The rung. Thirteen occurrences of ten different parts, weighed once by "
            "the product graph and once by arithmetic over the table — so a part "
            "placed twice, or a diameter changed in one of the two places it is "
            "written, is red rather than a gearbox that is quietly 2 kg out."
        ),
    ),
    Assertion(
        name="the graph holds exactly the parts the bill of materials counts",
        measure="clash.occurrence_count",
        comparison="==",
        bound="=occurrence_count",
        note=(
            "The BOM against the graph directly, beside the mass claim that checks it "
            "by arithmetic — M6's pair of checks, for M6's reason."
        ),
    ),
    Assertion(
        name="nothing in the gearbox occupies the same space as anything else",
        measure="clash.clash_count",
        comparison="==",
        bound=0.0,
        note=(
            "The claim that catches a mirrored cover. The quarter turn about Y sends "
            "each part's sketch +X to the world's -Z, so a bore drawn on the wrong "
            "side of the sketch centreline builds a cover of exactly the right mass "
            "with the shaft running through solid plate."
        ),
    ),
    Assertion(
        name="the seat span leaves room for the widest stack the drawing allows",
        measure="housing.bounding_box_mm.size[2]",
        comparison=">=",
        bound="=stacked_maximum_mm",
        note=(
            "The tolerance stack, read off the built housing rather than off the "
            "drawing it was computed from. Bearing, spacer, hub and bearing all at "
            "their upper limits inside a seat span at its lower one: if that does not "
            "fit, the end float closes and the bearings are clamped through their "
            "balls. `app/rules/stackup.py` worst case, which is interval arithmetic "
            "and assumes nothing about anybody's factory."
        ),
    ),
    Assertion(
        name="the bearing is as wide as the chain says it is",
        measure="output_bearing.bounding_box_mm.size[2]",
        comparison="==",
        bound=_M4_OUTPUT_BEARING_PART.width_mm,
        tolerance=1e-3,
        note=(
            "A contributor checked against the solid it describes. The stack is "
            "arithmetic over five numbers and it is only worth anything while those "
            "five numbers are the machine's."
        ),
    ),
    Assertion(
        name="the spacer is as wide as the chain says it is",
        measure="output_spacer.bounding_box_mm.size[2]",
        comparison="==",
        bound=_M4_OUTPUT_SPACER_MM,
        tolerance=1e-3,
    ),
    Assertion(
        name="the machine is as long as its shafts",
        measure="envelope_mm.size[0]",
        comparison="==",
        bound="=overall_length_mm",
        tolerance=1e-3,
        note="Front shaft end to rear shaft end: the space the gearbox needs on a bench.",
    ),
    Assertion(
        name="the machine is as tall as its housing",
        measure="envelope_mm.size[2]",
        comparison="==",
        bound="=housing_height_mm",
        tolerance=1e-3,
        note=(
            "The wheel's tip circle clears the cavity by the radial clearance, so the "
            "housing is the tallest thing here. A cavity sized by hand rather than "
            "derived from the tip diameters stops being true the day the ratio changes."
        ),
    ),
    Assertion(
        name="the gearbox is symmetric across the plane the shafts lie in",
        measure="centre_of_mass_mm[1]",
        comparison="==",
        bound=0.0,
        tolerance=1e-6,
        note="Every part is a body of revolution or a centred prism; one placed off-axis moves this.",
    ),
    Assertion(
        name="the weight hangs below the mesh centreline",
        measure="centre_of_mass_mm[2]",
        comparison="==",
        bound="=centre_of_mass_z_mm",
        tolerance=1e-4,
        note=(
            "The one claim that knows which way up the gearbox is. Swap the two "
            "shafts and every mass, every diameter and every clearance here is "
            "unchanged; this moves, because the wheel's blank is nearly five times "
            "the pinion's and it is the part that hangs."
        ),
    ),
)


#: What M4 builds and does **not** claim. Its ladder column is "gear geometry,
#: bearings, tolerance stacks, lubrication" — the stack is checked, the bearings
#: are bought and the other two are not here at all. Printed beside every pass.
_M4_UNPROVEN: Final = (
    "E1 — there are no teeth: the gears are root cylinders, because the open "
    "kernel has no gear-profile operation and cannot approximate an involute from "
    "the primitives it has. `catia_sketch_gear_profile` generates one on a CATIA "
    "seat and `app/kernel/occt/refusals.py` answers it 'not needed', which this "
    "rung is the case against",
    f"E1 — the blanks under-report the gears by {m4_tooth_volume_not_modelled_mm3():,.0f} "
    "mm3 of tooth material, an exact bound and not an estimate: the real pair is "
    "somewhere between these root cylinders and the tip cylinders, and nothing "
    "here says where",
    "E12.4 — no bearing life: `app.parts.bearings.select` runs on this shaft, "
    "considers every 6-series bearing that fits it and refuses, because not one of "
    "them carries C or C0. A load rating is the maker's number and this library "
    "ships ISO 15 boundary dimensions only, so the bearings are placed and not sized",
    "E12.4 — the bearing envelopes over-state their mass: a rolling bearing is two "
    "rings, a ball set and a cage inside the annulus drawn here, so the roll-up is "
    "high on those four occurrences and low on the two gears, and neither is corrected",
    "E13.2 — the stack is worst case only. An RSS band needs a signature against "
    "the independence assumption and this chain contains two widths off the same "
    "bearing, which is the correlation RSS assumes away. No fit is checked either: "
    "`app/rules/fits.py` has the arithmetic and ISO 286's deviation tables are not "
    "transcribed, so the bearing seats and the gear bores are basic sizes with no zones",
    "E13.2 — the bearing width tolerance in the chain is this drawing's own "
    "declaration. ISO 492 is not transcribed anywhere here, so no figure in the "
    "stack rests on a standard somebody read",
    "E6 — no load case: nothing says the shafts carry the mesh separating force, "
    "that the housing does not deflect enough to open the mesh, or that the covers "
    "hold the bearings against anything",
    "E8 — no tooth rating. ISO 6336's bending and contact stresses are what decides "
    "whether these gears last, and the module and face width here were chosen to "
    "make a machine, not to carry a torque",
    "E9 — nothing turns: this is a static layout, so there is no mesh frequency, no "
    "transmission error, no bearing speed check and no dynamic load",
    "E13 — no lubrication, no thermal, no efficiency and no seals. A gearbox is an "
    "oil bath with a level, a breather and two shaft seals, and none of those is "
    "modelled or checked",
)


# ---------------------------------------------------------------------------
# M5 — the sheet-metal stamping press
# ---------------------------------------------------------------------------
#
# A 400 kN gap-frame mechanical press: bed, column and crown in a C, a bolster,
# a two-post die set, a slide driven by a crank and connecting rod, and a folded
# sheet guard over the drive. Twelve occurrences of eleven parts.
#
# **The master plan calls this "the honest mid-point milestone" — structure,
# mechanism, sheet metal, bought parts, fatigue and guarding at once, and if M5
# does not work the phases before it were decoration.** So the useful thing this
# rung produces is not that it builds. It is the list of what it found it cannot
# say, which is at the bottom of this section.
#
# **Why it moves now.** Its declared `needs` were E17.3, E12.3, E13 and E14.
# E17.3 is complete, E12 and E14 are complete, and E13 is three tasks done out of
# four with the open one — E13.2 — open on a *document*. That is M4's argument two
# days later, and it is M6's rule: a rung held pending on the half of a
# prerequisite it does not use is a ladder that has stopped measuring anything.
#
# **The rung's spine is that a press is a chain of dimensions from the crank down
# to the strip, and every one of them is somebody else's part.**
#
# The whole vertical layout here is *derived downward from the crank axis*: the
# slide's face at bottom dead centre is `crank_axis - (rod + throw)`, the upper
# shoe hangs under it, the lower shoe sits under that, the bolster under that and
# the bed under that. Nothing in that chain is typed. What falls out at the bottom
# is the gap between the two die shoes at bottom dead centre — and **that gap is
# the strip being stamped**. Lengthen the connecting rod by a millimetre and the
# dies crash into each other through the work; shorten it and the press never
# cuts. It is measured between the two built solids, through an interface
# contract, exactly as M4 measures its gear mesh.
#
# **The tonnage claim is inverted on purpose, and that is this rung's sharpest
# finding.** A blanking force is `F = perimeter x thickness x shear strength`, and
# **nothing in this repository carries a shear strength.** `app/solve/materials.py`
# ships yield and ultimate *tensile* strength for steel-1018, both transcribed
# with a source; there is no shear column, and every ratio between shear and
# tensile that a shop table would give is a rule of thumb this file is not
# entitled to invent. So the press does not claim it can stamp this blank. It
# states the limit the other way round — **the greatest shear strength its rating
# covers over this blank's perimeter** — which is exact arithmetic needing no
# material property at all, and then says, in `_M5_UNPROVEN`, that it cannot tell
# you whether mild steel is inside it.
#
# **The guard is where sheet metal meets an assembly, and they do not meet.**
# `AssemblyDesign.parts` maps a component to a `DesignSpec`, and a
# `SheetMetalPart` cannot compile to one — M3's finding, still true, and here it
# blocks one level up. So the guard is declared twice, the way M3's cover is: as a
# fold tree, which is what unfolds into a blank a laser cuts, and as the same
# radiused section drawn by hand and extruded, which is what the kernel builds.
# The number tying them together is the volume, and it is asked to agree exactly.
# Above that sits the other half of E17.3's residual: **there is no sheet-metal
# operation in the CATIA registry and deliberately is not one** (THE QUEUE E1), so
# every sheet claim on this press is an open-kernel claim and the mission says so
# rather than implying a seat could build it.

#: Rated force. A 40-tonne gap-frame press — small enough to be a real machine and
#: large enough that its die set, guard and frame are all real parts.
_M5_RATED_FORCE_N: Final = 400_000.0

#: The blank this press is set up to make: a rectangle, so its cut perimeter is
#: arithmetic rather than a number off a drawing.
_M5_BLANK_LENGTH_MM: Final = 120.0
_M5_BLANK_WIDTH_MM: Final = 80.0
#: The strip. It is also the gap between the two die shoes at bottom dead centre,
#: which is the whole reason the vertical chain below is derived rather than typed.
_M5_STRIP_MM: Final = 2.0

#: The drive. Stroke is twice the throw, exactly; the rod length decides nothing
#: about the stroke and everything about where the stroke *sits*.
_M5_THROW_MM: Final = 50.0
_M5_ROD_MM: Final = 400.0
_M5_CRANK_AXIS_Z_MM: Final = 1_182.0
_M5_PIN_DIAMETER_MM: Final = 80.0
_M5_PIN_LENGTH_MM: Final = 200.0

#: The vertical chain, derived downward from the crank axis. Every one of these is
#: a consequence of the one above it; the only independent numbers are the part
#: thicknesses, which are what a press shop actually buys.
_M5_SLIDE_MM: Final = 300.0
_M5_UPPER_SHOE_MM: Final = 50.0
_M5_LOWER_SHOE_MM: Final = 50.0
_M5_BOLSTER_MM: Final = 80.0
_M5_BED_MM: Final = 250.0

#: At bottom dead centre the crank pin is one throw below the axis and the rod is
#: vertical, so the slide's top face is `rod + throw` below the axis. This is the
#: one line in the file that makes the press a mechanism rather than a stack.
_M5_SLIDE_TOP_MM: Final = _M5_CRANK_AXIS_Z_MM - (_M5_ROD_MM + _M5_THROW_MM)
_M5_SLIDE_BOTTOM_MM: Final = _M5_SLIDE_TOP_MM - _M5_SLIDE_MM
_M5_UPPER_SHOE_BOTTOM_MM: Final = _M5_SLIDE_BOTTOM_MM - _M5_UPPER_SHOE_MM
_M5_LOWER_SHOE_TOP_MM: Final = _M5_UPPER_SHOE_BOTTOM_MM - _M5_STRIP_MM
_M5_LOWER_SHOE_BOTTOM_MM: Final = _M5_LOWER_SHOE_TOP_MM - _M5_LOWER_SHOE_MM
_M5_BOLSTER_BOTTOM_MM: Final = _M5_LOWER_SHOE_BOTTOM_MM - _M5_BOLSTER_MM
_M5_BED_BOTTOM_MM: Final = _M5_BOLSTER_BOTTOM_MM - _M5_BED_MM

#: The rod body is drawn from the slide face up to the pin's *surface*: the big end
#: that wraps the pin is not modelled, because two solids sharing a journal is an
#: interference by construction and the clash check would be right to say so.
_M5_PIN_CENTRE_Z_MM: Final = _M5_CRANK_AXIS_Z_MM - _M5_THROW_MM
_M5_ROD_BODY_MM: Final = (
    _M5_PIN_CENTRE_Z_MM - _M5_PIN_DIAMETER_MM / 2.0 - _M5_SLIDE_TOP_MM
)

#: The frame. X across the press, Y from the front of the throat to the back of
#: the column, Z up.
_M5_FRAME_WIDTH_MM: Final = 900.0
_M5_THROAT_DEPTH_MM: Final = 600.0
_M5_COLUMN_DEPTH_MM: Final = 250.0
_M5_CROWN_MM: Final = 250.0
_M5_COLUMN_FRONT_MM: Final = _M5_THROAT_DEPTH_MM / 2.0
_M5_COLUMN_HEIGHT_MM: Final = _M5_SLIDE_TOP_MM + _M5_ROD_BODY_MM + 408.0

_M5_BOLSTER_WIDTH_MM: Final = 800.0
_M5_BOLSTER_DEPTH_MM: Final = 500.0
_M5_LOWER_SHOE_WIDTH_MM: Final = 560.0
_M5_SHOE_DEPTH_MM: Final = 300.0
_M5_UPPER_SHOE_WIDTH_MM: Final = 400.0
_M5_SLIDE_WIDTH_MM: Final = 400.0
_M5_SLIDE_DEPTH_MM: Final = 500.0
_M5_ROD_SECTION_MM: Final = 120.0

#: The die set's guide posts: two, outboard of the upper shoe so they guide it
#: without passing through it. A real die set has bushes in the upper shoe; a bush
#: is a bore, and a post through a bore is the coincident-cylinder question
#: `app/rules/fits.py` owns rather than the clash check.
_M5_POST_DIAMETER_MM: Final = 50.0
_M5_POST_X_MM: Final = 240.0
_M5_POST_MM: Final = 320.0

#: The guard: a folded channel of 2 mm cold-rolled mild steel over the drive, open
#: at the bottom so the connecting rod passes through it.
_M5_GUARD_GRADE: Final = "steel_mild_cr"
_M5_GUARD_THICKNESS_MM: Final = 2.0
_M5_GUARD_RADIUS_MM: Final = 3.0
_M5_GUARD_WIDTH_MM: Final = 400.0
_M5_GUARD_HEIGHT_MM: Final = 300.0
_M5_GUARD_LENGTH_MM: Final = 800.0
_M5_GUARD_TOP_Z_MM: Final = 1_200.0
_M5_GUARD_BEND_ANGLE_DEG: Final = 90.0

_M5_DENSITY_KG_M3: Final = _M2_DENSITY_KG_M3

#: Two parts that are meant to touch are 0 mm apart, and a fit-up gap of a few
#: hundredths is still a joint. Ten millimetres is a part cut short, and a
#: contact-only broad phase throws that pair away as "safely apart" — M2 measured
#: exactly that and the claim came back UNMEASURED rather than red.
_M5_INSPECTION_MM: Final = 25.0


def m5_stroke_mm(*, throw_mm: float = _M5_THROW_MM) -> float:
    """Twice the crank throw, and nothing else decides it.

    Not a rule of thumb: the slide's travel is the difference between the
    slider-crank's two extremes, `(l + r) - (l - r)`, and the rod length cancels.
    A press bought for a 100 mm stroke and built with a longer rod has the same
    stroke in a different place, which is the confusion this states out of the way.
    """
    return 2.0 * throw_mm


def m5_slide_drop_mm(
    angle_deg: float, *, throw_mm: float = _M5_THROW_MM, rod_mm: float = _M5_ROD_MM
) -> float:
    """How far below the crank axis the slide's face sits, at a crank angle.

    The slider-crank, exactly: `r cos(theta) + sqrt(l^2 - r^2 sin^2(theta))`,
    measured with theta = 0 at bottom dead centre so that the number grows as the
    slide rises. No small-angle approximation and no harmonic substitute — the
    second-order term is what makes a press's velocity asymmetric about mid-stroke,
    and it is the reason a die's shear is not the same on the way in as on the way
    out.
    """
    if rod_mm <= throw_mm:
        raise SpecError(
            f"A connecting rod of {rod_mm:g} mm on a {throw_mm:g} mm throw cannot "
            "turn: the rod must be longer than the throw or the crank locks. The "
            "square root below would be the square root of a negative number, which "
            "is where a mechanism stops being a mechanism."
        )
    theta = math.radians(angle_deg)
    return throw_mm * math.cos(theta) + math.sqrt(
        rod_mm**2 - (throw_mm * math.sin(theta)) ** 2
    )


def m5_blank_perimeter_mm(
    *, length_mm: float = _M5_BLANK_LENGTH_MM, width_mm: float = _M5_BLANK_WIDTH_MM
) -> float:
    """The cut line of the blank. A rectangle, so this is arithmetic."""
    return 2.0 * (length_mm + width_mm)


def m5_shear_strength_limit_mpa(
    *,
    rated_force_n: float = _M5_RATED_FORCE_N,
    strip_mm: float = _M5_STRIP_MM,
) -> float:
    """The greatest shear strength this press's rating covers, over this blank.

    **The capacity claim, inverted, and the inversion is the honest part.** A
    blanking force is `F = L x t x tau`, so a press of rated force `F` can blank a
    perimeter `L` in a strip `t` out of any material whose shear strength is at
    most `F / (L t)`. That is exact and needs no material property.

    What it does *not* say is whether the strip in the die is inside that limit,
    and this rung deliberately does not say it: **nothing in this repository
    carries a shear strength.** `app/solve/materials.py` transcribes yield and
    ultimate tensile strength for steel-1018 with a source and has no shear
    column, and every published ratio between the two is a shop rule of thumb that
    this file is not entitled to invent — which is the `app/verify/` rule about
    recalled figures, applied to a material instead of a benchmark. So the number
    below is a limit, `_M5_UNPROVEN` says what cannot be checked against it, and
    E12 owns the gap.
    """
    perimeter = m5_blank_perimeter_mm()
    if perimeter <= 0 or strip_mm <= 0:
        raise SpecError(
            "A blank with no perimeter or no thickness needs no force, which is not "
            "a press capacity — check the blank dimensions."
        )
    return rated_force_n / (perimeter * strip_mm)


def _m5_guard_part(
    *,
    width_mm: float = _M5_GUARD_WIDTH_MM,
    height_mm: float = _M5_GUARD_HEIGHT_MM,
    length_mm: float = _M5_GUARD_LENGTH_MM,
    thickness_mm: float = _M5_GUARD_THICKNESS_MM,
    inside_radius_mm: float = _M5_GUARD_RADIUS_MM,
    grade: str = _M5_GUARD_GRADE,
) -> SheetMetalPart:
    """The guard as a fold tree: a chain of three flanges and two bends.

    Rooted at a wall rather than at the top, for M3's reason and it is the whole
    of why this is a chain: rooted at the top the part is a *tree* — two walls off
    one panel — and `unfold` refuses a flat length for a tree, because a branching
    blank has an extent in two directions and no chain to sum along. Rooted at a
    wall it is a chain, and `flat_length_mm` is computed twice, once from the bend
    deductions and once from the allowances, and refused if the two disagree.

    The K-factor comes from `_m3_k_factor`, which is DIN 6935 for cold-formed
    steel — one K doctrine for the whole ladder, so a guard and an enclosure folded
    from the same coil do not get their neutral axes from different traditions.
    """
    sheet = sheet_material(grade, thickness_mm=thickness_mm)
    bend_k = _m3_k_factor(
        inside_radius_mm=inside_radius_mm, thickness_mm=thickness_mm, grade=grade
    )
    # See `_m5_guard_formability` below: an unformable guard is refused here rather
    # than asserted about later, because an assembly's parameters cannot be measured.

    def bend(name: str) -> Bend:
        return Bend(
            angle_deg=_M5_GUARD_BEND_ANGLE_DEG,
            inside_radius_mm=inside_radius_mm,
            direction=BendDirection.DOWN,
            k=bend_k,
            name=name,
        )

    part = SheetMetalPart(
        name="M5 drive guard",
        material=sheet,
        convention=LengthConvention.OUTSIDE_MOULD_LINE,
        root=Flange(
            name="wall_left",
            length_mm=height_mm,
            width_mm=length_mm,
            joints=(
                Joint(
                    edge=Edge.FAR,
                    bend=bend("corner_left"),
                    flange=Flange(
                        name="roof",
                        length_mm=width_mm,
                        joints=(
                            Joint(
                                edge=Edge.FAR,
                                bend=bend("corner_right"),
                                flange=Flange(name="wall_right", length_mm=height_mm),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    return _m5_guard_formability(part)


def _m5_guard_formability(part: SheetMetalPart) -> SheetMetalPart:
    """Refuse a guard that cannot be folded, and say which check failed.

    **This is a refusal rather than an assertion, and the reason is a fact about the
    assembly machinery that cost a build to find** (2026-09-16). It was first written
    as a claim in `_M5_ASSERTIONS` measuring a `guard_formability_failure_count`
    parameter, and the ladder came back **NOT CHECKED**: an `AssemblyDesign`'s
    `parameters` resolve the *bound* side of an assertion — `bound="=mass_closed_form_kg"`
    works — and never the *measured* side, which comes from `_combined_payload` and is
    geometry the kernel reported. A formability finding is not geometry. The kernel
    never sees the fold tree, so there is no measurement for this claim to read and
    there never could be.

    An unchecked assertion is `UNMEASURED`, which is honest and useless. So the check
    moves to construction, where it is stronger than a claim: **M5 cannot be built
    with a guard that cannot be folded.** `check_part` is `app/sheetmetal/`'s, and
    both halves of its report are read — `failed` refuses, and `unmeasured` refuses
    too, because a guard whose formability was not assessed is not a guard that
    passed, which is the distinction `FormabilityReport` keeps `ok` and `complete`
    apart for.
    """
    report = check_part(part)
    if report.failed:
        findings = "; ".join(f"{f.check}: {f.message}" for f in report.failed)
        raise SpecError(
            f"The M5 guard cannot be folded from {part.material.name}: {findings}. "
            "Open the bend radius, lengthen the flange, or choose a grade that will "
            "take the bend — a guard that cracks at the corner is a guard nobody fits."
        )
    if report.unmeasured:
        open_checks = ", ".join(f.check for f in report.unmeasured)
        raise SpecError(
            f"The M5 guard's formability was not fully assessed ({open_checks} could "
            "not be measured), and a part nobody assessed is not a part that passed. "
            "Supply what the check needs — a die opening, a grade's minimum radius — "
            "or state the gap in `_M5_UNPROVEN` rather than building past it."
        )
    return part


def _m5_guard_section(
    *,
    thickness_mm: float,
    inside_radius_mm: float,
    width_mm: float,
    height_mm: float,
    sketch: str,
) -> list[FeatureSpec]:
    """The guard's folded cross-section, drawn segment by segment as one contour.

    **The part the design IR cannot say**, in M3's words, and the same twelve-line
    answer: there is no `catia_wall`, no `catia_flange`, no `catia_bend` and no
    `catia_unfold` in the OCCT backend, so a folded solid is drawn the way a
    draughtsman would have drawn one — the section, by hand, and extruded.

    Every coordinate is *derived* from the fold rather than chosen, which is why
    they are computed here from the same constants the fold tree is built from.
    The corner arcs are the real bend radii, inside and out, so this section is not
    an approximation of the folded part: it is the folded part's section, and the
    volumes are asked to agree exactly rather than within a tolerance.
    """
    t, r = thickness_mm, inside_radius_mm
    outer = r + t
    inset = t + r
    top, floor = 0.0, -height_mm
    left, right = 0.0, width_mm
    centres = {
        "corner_left": (inset, top - inset),
        "corner_right": (right - inset, top - inset),
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

    # Outside, from the bottom of the left wall, up and over.
    line((left, floor), (left, top - inset))
    arc(centres["corner_left"], outer, 180.0, 90.0)
    line((inset, top), (right - inset, top))
    arc(centres["corner_right"], outer, 90.0, 0.0)
    line((right, top - inset), (right, floor))
    # Across the free edge of the right wall and back along the inside.
    line((right, floor), (right - t, floor))
    line((right - t, floor), (right - t, top - inset))
    arc(centres["corner_right"], r, 0.0, 90.0)
    line((right - inset, top - t), (inset, top - t))
    arc(centres["corner_left"], r, 90.0, 180.0)
    line((left + t, top - inset), (left + t, floor))
    line((left + t, floor), (left, floor))

    return [
        FeatureSpec(f"guard.seg{index + 1:02d}", tool, {"sketch": ref(sketch), **arguments})
        for index, (tool, arguments) in enumerate(calls)
    ]


def _m5_guard_spec(
    *,
    width_mm: float = _M5_GUARD_WIDTH_MM,
    height_mm: float = _M5_GUARD_HEIGHT_MM,
    length_mm: float = _M5_GUARD_LENGTH_MM,
    thickness_mm: float = _M5_GUARD_THICKNESS_MM,
    inside_radius_mm: float = _M5_GUARD_RADIUS_MM,
) -> DesignSpec:
    """The same guard as geometry: one sketch of the section, extruded."""
    return DesignSpec.of(
        "M5 drive guard",
        material="steel-1018",
        description=(
            "Folded sheet guard over the drive. Drawn as a section because a "
            "SheetMetalPart cannot compile to a DesignSpec."
        ),
        parameters=[
            Parameter("guard_length_mm", Unit.MM, value=length_mm),
            Parameter("guard_width_mm", Unit.MM, value=width_mm),
            Parameter("guard_height_mm", Unit.MM, value=height_mm),
            Parameter("guard_thickness_mm", Unit.MM, value=thickness_mm),
        ],
        features=[
            FeatureSpec("guard.section", "catia_sketch_create", {"support": "XY"}),
            *_m5_guard_section(
                thickness_mm=thickness_mm,
                inside_radius_mm=inside_radius_mm,
                width_mm=width_mm,
                height_mm=height_mm,
                sketch="guard.section",
            ),
            FeatureSpec(
                "guard.body",
                "catia_pad",
                {"sketch": ref("guard.section"), "length_mm": expr("guard_length_mm")},
                note="Extrude the folded section along the guard's length.",
            ),
        ],
    )


def _solid_block_spec(
    name: str, *, width_mm: float, depth_mm: float, height_mm: float, description: str
) -> DesignSpec:
    """A plate or a block. Most of a press frame is one of these, and most of an arm.

    Built centred on its own origin in X and Y and rising from z = 0, which is what
    a padded rectangle does — measured on the kernel rather than assumed, because
    "the sketch is centred" and "the sketch starts at the origin" are both plausible
    and only one of them is true.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        description=description,
        parameters=[
            Parameter("width_mm", Unit.MM, value=width_mm),
            Parameter("depth_mm", Unit.MM, value=depth_mm),
            Parameter("height_mm", Unit.MM, value=height_mm),
        ],
        features=[
            FeatureSpec("block.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "block.outline",
                "catia_sketch_rectangle",
                {
                    "sketch": ref("block.profile"),
                    "width_mm": expr("width_mm"),
                    "height_mm": expr("depth_mm"),
                },
            ),
            FeatureSpec(
                "block.body",
                "catia_pad",
                {"sketch": ref("block.profile"), "length_mm": expr("height_mm")},
            ),
        ],
    )


def _solid_cylinder_spec(
    name: str, *, diameter_mm: float, length_mm: float, description: str
) -> DesignSpec:
    """A round bar, built along its own +Z.

    Separate from `_solid_block_spec` for a reason the mass claim found on 2026-09-16:
    the crank pin was *drawn* by the block helper and *weighed* as a cylinder, so the
    closed form and the roll-up disagreed by 2.16 kg out of 5,691 — 0.04%, which is
    far too small to notice by eye and exactly the size of error the assertion exists
    to catch. Two shapes for one part is the failure; one helper per shape is the fix.
    """
    return DesignSpec.of(
        name,
        material="steel-1018",
        description=description,
        parameters=[
            Parameter("diameter_mm", Unit.MM, value=diameter_mm),
            Parameter("length_mm", Unit.MM, value=length_mm),
        ],
        features=[
            FeatureSpec("bar.profile", "catia_sketch_create", {"support": "XY"}),
            FeatureSpec(
                "bar.outline",
                "catia_sketch_circle",
                {"sketch": ref("bar.profile"), "diameter_mm": expr("diameter_mm")},
            ),
            FeatureSpec(
                "bar.body",
                "catia_pad",
                {"sketch": ref("bar.profile"), "length_mm": expr("length_mm")},
            ),
        ],
    )


#: The die set, as a contract. The claim is the gap at bottom dead centre, and it
#: is the strip: the press's entire vertical chain exists to put the two shoe faces
#: exactly one material thickness apart at the bottom of the stroke.
_M5_DIE_SET: Final = Interface(
    name="die set at bottom dead centre",
    provider="lower_shoe",
    consumer="upper_shoe",
    parameters=ParameterSet.of(
        [
            Parameter(
                "strip_mm",
                Unit.MM,
                value=_M5_STRIP_MM,
                description="The material in the die. Also the shut gap, by construction.",
            ),
            Parameter("shoe_depth_mm", Unit.MM, value=_M5_SHOE_DEPTH_MM),
        ]
    ),
    claims=(
        Assertion(
            name="the shoes close on the strip and not on each other",
            measure="minimum_clearance_mm",
            comparison="==",
            bound="=strip_mm",
            tolerance=1e-3,
            note=(
                "The rung. The slide's face at bottom dead centre is "
                "`crank_axis - (rod + throw)` and everything below it hangs off that, "
                "so this gap is a consequence of the connecting rod's length. A rod a "
                "millimetre long crashes the dies through the work; a millimetre short "
                "and the press never cuts. Measured between the built solids."
            ),
        ),
        Assertion(
            name="the shoes are the same depth, so the strip is supported across the die",
            measure="provider.bounding_box_mm.size[1]",
            comparison="==",
            bound="=shoe_depth_mm",
            tolerance=1e-3,
        ),
        Assertion(
            name="the upper shoe is the depth the die set was laid out to",
            measure="consumer.bounding_box_mm.size[1]",
            comparison="==",
            bound="=shoe_depth_mm",
            tolerance=1e-3,
            note="Checked on both sides: a contract that only looks at one party has one party.",
        ),
    ),
)


#: The force path where it leaves the frame. The bolster carries the whole rated
#: force into the bed, and the two must be in contact over the joint rather than
#: near it — a bolster sitting on a high spot is a cracked bed.
_M5_FORCE_PATH: Final = Interface(
    name="bolster to bed",
    provider="bed",
    consumer="bolster",
    parameters=ParameterSet.of(
        [Parameter("fit_up_mm", Unit.MM, value=0.05, description="What a machined joint may open to.")]
    ),
    claims=(
        Assertion(
            name="the bolster sits on the bed",
            measure="minimum_clearance_mm",
            comparison="<=",
            bound="=fit_up_mm",
        ),
        Assertion(
            name="and sits on it everywhere, not at one corner",
            measure="widest_gap_mm",
            comparison="<=",
            bound="=fit_up_mm",
            note=(
                "The minimum alone cannot carry a fit-up claim: a bolster touching at "
                "one edge and 10 mm clear at the other has a minimum clearance of zero. "
                "M2 learned this from the other side and `_boundary_payload` publishes "
                "both numbers for exactly this reason."
            ),
        ),
    ),
)


def _m5_volumes() -> dict[str, float]:
    """Each component's volume from its own formula. Blocks, cylinders and a fold."""
    return {
        "bed": _M5_FRAME_WIDTH_MM * _M5_THROAT_DEPTH_MM * _M5_BED_MM,
        "column": _M5_FRAME_WIDTH_MM * _M5_COLUMN_DEPTH_MM * _M5_COLUMN_HEIGHT_MM,
        "crown": _M5_FRAME_WIDTH_MM * _M5_THROAT_DEPTH_MM * _M5_CROWN_MM,
        "bolster": _M5_BOLSTER_WIDTH_MM * _M5_BOLSTER_DEPTH_MM * _M5_BOLSTER_MM,
        "lower_shoe": _M5_LOWER_SHOE_WIDTH_MM * _M5_SHOE_DEPTH_MM * _M5_LOWER_SHOE_MM,
        "upper_shoe": _M5_UPPER_SHOE_WIDTH_MM * _M5_SHOE_DEPTH_MM * _M5_UPPER_SHOE_MM,
        "guide_post": math.pi / 4.0 * _M5_POST_DIAMETER_MM**2 * _M5_POST_MM,
        "slide": _M5_SLIDE_WIDTH_MM * _M5_SLIDE_DEPTH_MM * _M5_SLIDE_MM,
        "connecting_rod": _M5_ROD_SECTION_MM**2 * _M5_ROD_BODY_MM,
        "crank_pin": math.pi / 4.0 * _M5_PIN_DIAMETER_MM**2 * _M5_PIN_LENGTH_MM,
        # Not a formula of its own: the folded volume is `app/sheetmetal/fold.py`'s
        # closed form over the faces and the bend sectors, and asking the guard's
        # volume here any other way would be a second opinion about one part.
        "guard": folded_volume_mm3(_m5_guard_part()),
    }


#: Every occurrence in the press. Two guide posts, one of everything else.
_M5_OCCURRENCES: Final[tuple[str, ...]] = (
    "bed",
    "column",
    "crown",
    "bolster",
    "lower_shoe",
    "upper_shoe",
    "guide_post",
    "guide_post",
    "slide",
    "connecting_rod",
    "crank_pin",
    "guard",
)


def m5_mass_kg() -> float:
    """The press weighed by arithmetic, against the roll-up from the graph.

    M6's rung on a machine of eleven different parts. The failure it catches here
    is not a count that drifted: it is a plate whose thickness was changed in the
    layout chain and not in the volume table, which moves the machine's mass and
    every dimension below it.
    """
    volumes = _m5_volumes()
    return sum(volumes[name] for name in _M5_OCCURRENCES) * 1e-9 * _M5_DENSITY_KG_M3


def _m5_structure(
    *,
    rod_mm: float = _M5_ROD_MM,
    guard_top_z_mm: float = _M5_GUARD_TOP_Z_MM,
    post_x_mm: tuple[float, float] = (-_M5_POST_X_MM, _M5_POST_X_MM),
    spare_post_at_mm: float | None = None,
) -> ProductStructure:
    """The press as a graph. X across, Y front to back, Z up.

    **The vertical positions are recomputed here from `rod_mm`**, not read off the
    module constants, so lengthening the connecting rod moves the slide, the upper
    shoe and the die gap together — which is the point of the rung and the only way
    a test can build it wrong without editing a source file.

    `guard_top_z_mm` and `spare_post_at_mm` are the other two breaks: a guard
    dropped onto the drive it is meant to cover, and a part in the graph nobody
    counted.
    """
    slide_top = _M5_CRANK_AXIS_Z_MM - (rod_mm + _M5_THROW_MM)
    slide_bottom = slide_top - _M5_SLIDE_MM
    upper_shoe_bottom = slide_bottom - _M5_UPPER_SHOE_MM
    lower_shoe_top = _M5_LOWER_SHOE_TOP_MM
    lower_shoe_bottom = _M5_LOWER_SHOE_BOTTOM_MM

    upright = None
    across = turned((0.0, 1.0, 0.0), math.pi / 2.0)
    # The guard is drawn in its own section plane and has to end up with its roof
    # horizontal and its opening downward. One quarter turn about Y lays the
    # extrusion along X; a second about X brings the section's own axes upright.
    # Measured on the kernel rather than reasoned about — the same asymmetry
    # `app/render/project.py` and `occt/sheetmetal.py` each document.
    guard_turn = compose(turned((1.0, 0.0, 0.0), math.pi / 2.0), across)

    builder = StructureBuilder()
    builder.define("press", description="400 kN gap-frame mechanical press.")
    for name, description in (
        ("bed", "The C-frame's foot; carries the bolster and the rated force."),
        ("column", "The C's back. Everything above the throat hangs off it."),
        ("crown", "The C's head; carries the crankshaft bearings."),
        ("bolster", "The plate the lower die bolts to."),
        ("lower_shoe", "Die set, lower shoe: carries the die block."),
        ("upper_shoe", "Die set, upper shoe: carries the punch."),
        ("guide_post", "Die-set guide post."),
        ("slide", "The ram."),
        ("connecting_rod", "Crank pin to slide."),
        ("crank_pin", "The throw. The crankshaft itself is not modelled."),
        ("guard", "Folded sheet guard over the drive."),
    ):
        builder.define(name, design=f"M5 {name.replace('_', ' ')}",
                       material="steel-1018", description=description)

    def block(component: str, *, y_mm: float, z_mm: float, note: str) -> None:
        builder.add("press", component, placement=at(0.0, y_mm, z_mm), note=note)

    block("bed", y_mm=0.0, z_mm=_M5_BED_BOTTOM_MM, note="Sits on the floor.")
    builder.add(
        "press", "column",
        placement=at(
            0.0,
            _M5_COLUMN_FRONT_MM + _M5_COLUMN_DEPTH_MM / 2.0,
            _M5_BED_BOTTOM_MM,
        ),
        note="Behind the throat, full height.",
    )
    block(
        "crown", y_mm=0.0,
        z_mm=_M5_BED_BOTTOM_MM + _M5_COLUMN_HEIGHT_MM - _M5_CROWN_MM,
        note="Over the throat, level with the top of the column.",
    )
    block("bolster", y_mm=0.0, z_mm=_M5_BOLSTER_BOTTOM_MM, note="Bolted to the bed.")
    block("lower_shoe", y_mm=0.0, z_mm=lower_shoe_bottom, note="On the bolster.")
    block("upper_shoe", y_mm=0.0, z_mm=upper_shoe_bottom, note="Under the slide face.")
    block("slide", y_mm=0.0, z_mm=slide_bottom, note="At bottom dead centre.")
    block("connecting_rod", y_mm=0.0, z_mm=slide_top, note="Vertical at bottom dead centre.")

    # Given as two positions rather than one half-width, so a test can move *one*
    # post. Moving both keeps the press symmetric and proves nothing; adding a third
    # fails the mass and count claims as well, so neither isolates the centre of mass.
    for x_mm in post_x_mm:
        builder.add(
            "press", "guide_post",
            placement=at(x_mm, 0.0, lower_shoe_top),
            note=f"Guide post at x = {x_mm:g} mm, outboard of the upper shoe.",
        )
    if spare_post_at_mm is not None:
        builder.add(
            "press", "guide_post",
            placement=at(spare_post_at_mm, 0.0, lower_shoe_top),
            note="A post nobody counted. Only a test places this.",
        )

    builder.add(
        "press", "crank_pin",
        placement=compose(
            at(-_M5_PIN_LENGTH_MM / 2.0, 0.0, _M5_PIN_CENTRE_Z_MM), across
        ),
        note="One throw below the crank axis: bottom dead centre.",
    )
    builder.add(
        "press", "guard",
        placement=compose(
            at(
                -_M5_GUARD_LENGTH_MM / 2.0,
                -_M5_GUARD_WIDTH_MM / 2.0,
                guard_top_z_mm,
            ),
            guard_turn,
        ),
        note="Over the drive, open at the bottom so the rod passes through.",
    )
    assert upright is None  # noqa: S101 - every block is placed unrotated on purpose
    return builder.build("press")


def _m5_design(
    *,
    rod_mm: float = _M5_ROD_MM,
    structure: ProductStructure | None = None,
) -> AssemblyDesign:
    """The press: the graph, eleven part designs, two contracts and the numbers."""
    from app.assembly.contracts import bind_into

    guard = _m5_guard_part()
    pattern = unfold(guard)
    formability = check_part(guard)
    return AssemblyDesign(
        structure=_m5_structure(rod_mm=rod_mm) if structure is None else structure,
        parts={
            "bed": bind_into(
                _M5_FORCE_PATH,
                _solid_block_spec(
                    "M5 bed",
                    width_mm=_M5_FRAME_WIDTH_MM,
                    depth_mm=_M5_THROAT_DEPTH_MM,
                    height_mm=_M5_BED_MM,
                    description="C-frame foot.",
                ),
            ),
            "column": _solid_block_spec(
                "M5 column",
                width_mm=_M5_FRAME_WIDTH_MM,
                depth_mm=_M5_COLUMN_DEPTH_MM,
                height_mm=_M5_COLUMN_HEIGHT_MM,
                description="C-frame back.",
            ),
            "crown": _solid_block_spec(
                "M5 crown",
                width_mm=_M5_FRAME_WIDTH_MM,
                depth_mm=_M5_THROAT_DEPTH_MM,
                height_mm=_M5_CROWN_MM,
                description="C-frame head.",
            ),
            "bolster": bind_into(
                _M5_FORCE_PATH,
                _solid_block_spec(
                    "M5 bolster",
                    width_mm=_M5_BOLSTER_WIDTH_MM,
                    depth_mm=_M5_BOLSTER_DEPTH_MM,
                    height_mm=_M5_BOLSTER_MM,
                    description="The plate the lower die bolts to.",
                ),
            ),
            "lower_shoe": bind_into(
                _M5_DIE_SET,
                _solid_block_spec(
                    "M5 lower shoe",
                    width_mm=_M5_LOWER_SHOE_WIDTH_MM,
                    depth_mm=_M5_SHOE_DEPTH_MM,
                    height_mm=_M5_LOWER_SHOE_MM,
                    description="Die set, lower shoe.",
                ),
            ),
            "upper_shoe": bind_into(
                _M5_DIE_SET,
                _solid_block_spec(
                    "M5 upper shoe",
                    width_mm=_M5_UPPER_SHOE_WIDTH_MM,
                    depth_mm=_M5_SHOE_DEPTH_MM,
                    height_mm=_M5_UPPER_SHOE_MM,
                    description="Die set, upper shoe.",
                ),
            ),
            "guide_post": _solid_cylinder_spec(
                "M5 guide post",
                diameter_mm=_M5_POST_DIAMETER_MM,
                length_mm=_M5_POST_MM,
                description="Die-set guide post. Hardened and ground in reality.",
            ),
            "slide": _solid_block_spec(
                "M5 slide",
                width_mm=_M5_SLIDE_WIDTH_MM,
                depth_mm=_M5_SLIDE_DEPTH_MM,
                height_mm=_M5_SLIDE_MM,
                description="The ram.",
            ),
            "connecting_rod": _solid_block_spec(
                "M5 connecting rod",
                width_mm=_M5_ROD_SECTION_MM,
                depth_mm=_M5_ROD_SECTION_MM,
                height_mm=_M5_ROD_BODY_MM,
                description="Crank pin to slide. The big end is not modelled.",
            ),
            "crank_pin": _solid_cylinder_spec(
                "M5 crank pin",
                diameter_mm=_M5_PIN_DIAMETER_MM,
                length_mm=_M5_PIN_LENGTH_MM,
                description="The throw. The crankshaft it belongs to is not modelled.",
            ),
            "guard": _m5_guard_spec(),
        },
        interfaces=(_M5_DIE_SET, _M5_FORCE_PATH),
        clearance_mm=_M5_INSPECTION_MM,
        parameters=ParameterSet.of(
            [
                Parameter("strip_mm", Unit.MM, value=_M5_STRIP_MM),
                Parameter("stroke_mm", Unit.MM, value=m5_stroke_mm()),
                Parameter("rated_force_n", Unit.NEWTON, value=_M5_RATED_FORCE_N),
                Parameter(
                    "shear_strength_limit_mpa",
                    Unit.MPA,
                    value=m5_shear_strength_limit_mpa(),
                    description=(
                        "The greatest shear strength this rating covers over this "
                        "blank's perimeter. Not a claim that the strip is inside it: "
                        "nothing here carries a shear strength."
                    ),
                ),
                Parameter("blank_perimeter_mm", Unit.MM, value=m5_blank_perimeter_mm()),
                Parameter(
                    "guard_blank_length_mm",
                    Unit.MM,
                    value=pattern.flat_length_mm or 0.0,
                    description="What the laser cuts, from the unfold.",
                ),
                Parameter(
                    "guard_volume_mm3",
                    Unit.MM3,
                    value=folded_volume_mm3(guard),
                    description=(
                        "The fold's closed form. Asked to equal the extruded "
                        "section's measured volume exactly, not within a tolerance."
                    ),
                ),
                Parameter(
                    "guard_formability_checks_passed",
                    Unit.NONE,
                    value=float(len(formability.passed)),
                    description=(
                        "Radius, flange reach and hole-to-bend, from app/sheetmetal/. "
                        "Published as a record and deliberately not asserted on: an "
                        "assembly's parameters are the bound side of a claim and never "
                        "the measured side, so a formability assertion here is always "
                        "NOT CHECKED. `_m5_guard_formability` refuses instead."
                    ),
                ),
                Parameter("frame_width_mm", Unit.MM, value=_M5_FRAME_WIDTH_MM),
                Parameter(
                    "overall_height_mm", Unit.MM, value=_M5_COLUMN_HEIGHT_MM
                ),
                Parameter(
                    "occurrence_count", Unit.NONE, value=float(len(_M5_OCCURRENCES))
                ),
                Parameter("mass_closed_form_kg", Unit.KG, value=m5_mass_kg()),
            ]
        ),
    )


_M5_ASSERTIONS: Final = (
    Assertion(
        name="the roll-up equals the closed form over every occurrence",
        measure="mass_kg",
        comparison="==",
        bound="=mass_closed_form_kg",
        tolerance=1e-6,
        note=(
            "Twelve occurrences of eleven parts, weighed once by the product graph and "
            "once by arithmetic — and the guard's share of that arithmetic is "
            "`app/sheetmetal/fold.py`'s closed form, so this claim reaches into the "
            "sheet-metal package as well as the kernel."
        ),
    ),
    Assertion(
        name="the graph holds exactly the parts the bill of materials counts",
        measure="clash.occurrence_count",
        comparison="==",
        bound="=occurrence_count",
    ),
    Assertion(
        name="nothing in the press occupies the same space as anything else",
        measure="clash.clash_count",
        comparison="==",
        bound=0.0,
        note=(
            "The guard is the claim that earns this. It is a 2 mm shell hanging over a "
            "moving rod and a crank pin, and a guard that fouls the drive is the one "
            "mistake on this machine that injures somebody rather than scrapping a part."
        ),
    ),
    Assertion(
        name="the guard the kernel built is the guard the fold tree describes",
        measure="guard.volume_mm3",
        comparison="==",
        bound="=guard_volume_mm3",
        tolerance=1e-6,
        note=(
            "**Exactly, not within a tolerance.** The section is drawn with the real "
            "bend radii inside and out, so it is not an approximation of the folded "
            "part — it is its section. Two descriptions of one part with nothing in "
            "the code base connecting them, which is M3's finding and is why this "
            "number is worth asking for."
        ),
    ),
    Assertion(
        name="the press is as tall as its frame",
        measure="envelope_mm.size[2]",
        comparison="==",
        bound="=overall_height_mm",
        tolerance=1e-3,
    ),
    Assertion(
        name="the press is as wide as its frame",
        measure="envelope_mm.size[0]",
        comparison="==",
        bound="=frame_width_mm",
        tolerance=1e-3,
        note=(
            "The guide posts sit outboard of the upper shoe and inboard of the frame, "
            "so a post moved out far enough to foul the slide shows up here as well as "
            "in the clash count."
        ),
    ),
    Assertion(
        name="the press is symmetric about its own centre plane",
        measure="centre_of_mass_mm[0]",
        comparison="==",
        bound=0.0,
        tolerance=1e-6,
        note=(
            "Two guide posts, one each side. **Moving one of them moves this claim and "
            "no other** — measured on 2026-09-16 rather than assumed: a post shifted "
            "10 mm leaves the mass, the occurrence count, the envelope and both "
            "contracts untouched, so this is the only claim standing between a "
            "die set that guides squarely and one that cocks the slide."
        ),
    ),
    Assertion(
        name="the mass sits behind the throat",
        measure="centre_of_mass_mm[1]",
        comparison=">",
        bound=0.0,
        note=(
            "A gap-frame press is back-heavy — the column is the single heaviest part "
            "and it is all behind the work — which is why one is bolted to the floor. "
            "The claim that knows which way round the C faces."
        ),
    ),
)


_M5_UNPROVEN: Final = (
    "E1 — there is no sheet-metal operation in the CATIA registry and deliberately "
    "is not one (THE QUEUE E1), so the guard is an **open-kernel claim**: the COM "
    "half is unwritten and unverifiable without a seat, and nothing here says a "
    "CATIA user could build this part at all",
    "E17.3 — a `SheetMetalPart` still cannot compile to a `DesignSpec`, so the guard "
    "is declared twice — as a fold tree and as a hand-drawn section — and the only "
    "thing holding the two descriptions together is a volume. M3 found this on one "
    "part; here it is what stops a folded part being a *component* of an assembly",
    "E12 — **nothing in this repository carries a shear strength, and it is not even "
    "a property the material vocabulary can name**: `MATERIAL_PROPERTIES` holds "
    "`shear_modulus_mpa`, which is elasticity and not strength, so no material here "
    "could carry one. So the press states the greatest shear strength its rating "
    "covers over this blank and cannot say whether the strip in the die is inside it. "
    "steel-1018 has sourced yield and ultimate tensile and both are `TYPICAL` rather "
    "than design basis, and every published ratio between tensile and shear is a shop "
    "rule of thumb this file will not invent",
    "E6 — **frame stiffness is not measured, and it is the thing a gap-frame press is "
    "bought or rejected on**. A C opens under load: the throat deflects, the slide "
    "tips, and the die's clearance goes uneven down one side. No load case has been "
    "run, so 'the force path is continuous' is a geometry claim and not a stiffness one",
    "E8 — no fatigue. A press frame sees one full load cycle per stroke and runs at "
    "tens of strokes a minute, so its duty cycle is millions of cycles a year and the "
    "throat corner is the classic crack. Nothing here counts them",
    "E9 — nothing moves. The slide is placed at bottom dead centre and the kinematics "
    "are arithmetic; there is no inertia of the slide, no flywheel energy budget, no "
    "clutch and brake, and no snap-through when the blank breaks — which is the load "
    "case that actually shakes a press",
    "E9 — the crankshaft is not modelled at all: only its pin is, because a pin and "
    "the shaft it belongs to are one part, and two solids sharing a journal interfere "
    "by construction. The connecting rod's big end is absent for the same reason, so "
    "the drive here is a bar and a stub and not a mechanism that could be assembled",
    "E12.3 — nothing is bolted. A press is held together by tie rods, bolster bolts "
    "and die clamps, and not one fastener is in this bill of materials",
    "E13 — no guarding standard. A guard is sheet metal *and* a reach distance, an "
    "interlock and a stopping time, and none of those is a geometry question. Nothing "
    "here has read a standard about it",
    "E13 — no cost and no DFM. Eleven parts, no quotation, and no view on whether a "
    "900 mm frame plate is a sensible thing to ask a shop to machine",
)


# ---------------------------------------------------------------------------
# M7 — the 6-axis robot arm
# ---------------------------------------------------------------------------
#
# A serial chain: base, shoulder, upper arm, forearm, wrist, hand, flange, with six
# revolute joints between them. Seven occurrences of seven parts, six of which move.
#
# **This is the first rung on the ladder whose answer is not a dimension.** Every rung
# before it asks how big something is or whether two things fit. An arm's dimensions
# are the easy half; what an engineer buys a robot for is what its shoulder bearing
# carries when the thing is moving, and no amount of geometry contains that number.
# So M7 is the first `MovingDesign`, and the reason that kind exists is written on it.
#
# **Its wait is over, and it ended the way M4's and M5's did — by the prerequisite
# landing, not by anyone deciding.** M7's declared `needs` were E9 (multibody, for
# loads that come from the machine moving) and E9.3 (motion range and swept volume).
# E9.1 put Project Chrono behind a container, E9.6 put inertia tensors in the mass
# roll-up, and `app/dynamics/` has carried an exact serial-chain recursion, Newton-Euler
# inverse dynamics and a travel model since the phase opened.
#
# **The joints are declared against the product graph, not beside it**, and that is the
# whole design of the rung. Each `JointDeclaration.child` is an occurrence path in the
# same `ProductStructure` the mass roll-up and the clash check walk, so the mass a
# reaction is computed against *is* the mass the geometry claims were checked against.
# `app.dynamics.assembly.derive` refuses a body it cannot weigh, in words, because a
# reaction computed on a partial mass is too small — and too small is the direction in
# which every check passes.
#
# **Three traps carried from E9.1, each of which yields a plausible wrong number rather
# than an error, and none of which this rung can hit — because it does not integrate.**
# Chrono's default iterative solver does not satisfy a revolute constraint (a pendulum
# whose closed-form pivot reaction is 29.42 N reported 4286 N); `GetReaction1` is the
# load on the *child* and the wrong one has the right magnitude and the wrong sign; and
# reaction *n* is expressed in frame *n*, so rotating by the wrong frame leaves a
# constant force on a mass going round a circle. `engines()` keeps Chrono behind
# `KinematicEngine` permanently for exactly this reason: **where both can answer, the
# kinematic engine is exact and Chrono integrates.** A driven serial chain is a case
# both can answer, so this rung takes the exact one and never starts a container. That
# is also what keeps its tests offline, which is the property `app/design/` is built on.
#
# **What it cannot say is in `_M7_UNPROVEN`, and the sharpest entry is stiffness.** The
# master plan's own column for this rung is "kinematics, dynamic loads, stiffness under
# motion". Two of the three are here. The third is not: nothing has run a load case on a
# link, so the arm is rigid, and a rigid arm has no deflection at the tool, which is the
# number a robot's repeatability specification is actually about.

#: The chain, bottom to top. Each link sits on the one below it, so the assembled pose
#: is the "candle" — every joint at zero. It is the pose a robot is shipped and
#: calibrated in, and it is the one pose in which a serial chain's clash check is
#: worth running, because it is the only one the product graph actually holds.
_M7_BASE_DIAMETER_MM: Final = 300.0
_M7_BASE_MM: Final = 200.0
_M7_SHOULDER_MM: Final = (260.0, 260.0, 220.0)
_M7_UPPER_ARM_MM: Final = (160.0, 180.0, 600.0)
_M7_FOREARM_MM: Final = (140.0, 160.0, 500.0)
_M7_WRIST_MM: Final = (120.0, 120.0, 160.0)
_M7_HAND_MM: Final = (100.0, 100.0, 120.0)
_M7_FLANGE_DIAMETER_MM: Final = 90.0
_M7_FLANGE_MM: Final = 40.0

_M7_DENSITY_KG_M3: Final = _M2_DENSITY_KG_M3

#: Where each link's underside sits, derived by stacking rather than typed — the same
#: rule M5's vertical chain follows, and for the same reason: a thickness changed in
#: one of two places is the failure, and there is only one place.
_M7_BASE_TOP_MM: Final = _M7_BASE_MM
_M7_SHOULDER_TOP_MM: Final = _M7_BASE_TOP_MM + _M7_SHOULDER_MM[2]
_M7_UPPER_ARM_TOP_MM: Final = _M7_SHOULDER_TOP_MM + _M7_UPPER_ARM_MM[2]
_M7_FOREARM_TOP_MM: Final = _M7_UPPER_ARM_TOP_MM + _M7_FOREARM_MM[2]
_M7_WRIST_TOP_MM: Final = _M7_FOREARM_TOP_MM + _M7_WRIST_MM[2]
_M7_HAND_TOP_MM: Final = _M7_WRIST_TOP_MM + _M7_HAND_MM[2]
_M7_REACH_MM: Final = _M7_HAND_TOP_MM + _M7_FLANGE_MM

#: The motion. Two seconds at 20 Hz — long enough that every harmonic joint passes
#: through a full cycle and its acceleration changes sign, which is what makes the
#: reaction a range rather than a number.
_M7_DURATION_S: Final = 2.0
_M7_SAMPLES: Final = 41

#: A contact-only broad phase would throw away the pairs that matter here: in the
#: candle pose every link touches the next, so "safely apart" is the wrong default and
#: the interesting pairs are the ones a few millimetres from each other.
_M7_INSPECTION_MM: Final = 20.0

_M7_ROOT: Final = "arm"


def _m7_path(component: str) -> str:
    """The occurrence path of a link, which is how `app.dynamics` names a body.

    Written once here rather than spelled out six times in the joint table: a body
    addressed by a path that does not resolve is refused by `derive`, which is good,
    but a *typo* that happens to resolve to the wrong link is not refusable and this
    is what stops one being written.
    """
    return f"{_M7_ROOT}/{component}.1"


def _m7_body_name(component: str) -> str:
    """What `app.dynamics` calls a link, which is not what the product graph calls it.

    `derive` names a body from its occurrence path through
    `app.dynamics.assembly.body_name`, and a mechanism name may not contain `.`, `[`
    or `]` — those are the assertion vocabulary's own path syntax, so a body called
    `arm/flange.1` would make `motion.body.arm/flange.1.travel_mm` unparseable. The
    sanitised name is therefore the one a reaction and a load case are reported under,
    and this is the single place the mapping is written down.
    """
    return body_name(_m7_path(component))


def _m7_links() -> dict[str, tuple[float, float, float]]:
    """Each moving link's bounding dimensions. The base is not here: it is ground."""
    return {
        "shoulder": _M7_SHOULDER_MM,
        "upper_arm": _M7_UPPER_ARM_MM,
        "forearm": _M7_FOREARM_MM,
        "wrist": _M7_WRIST_MM,
        "hand": _M7_HAND_MM,
    }


def _m7_volumes() -> dict[str, float]:
    """Every part's volume from its own shape. Two cylinders and five boxes."""
    volumes = {
        name: dimensions[0] * dimensions[1] * dimensions[2]
        for name, dimensions in _m7_links().items()
    }
    volumes["base"] = math.pi / 4.0 * _M7_BASE_DIAMETER_MM**2 * _M7_BASE_MM
    volumes["flange"] = math.pi / 4.0 * _M7_FLANGE_DIAMETER_MM**2 * _M7_FLANGE_MM
    return volumes


def m7_reach_mm() -> float:
    """Tip height in the candle pose: the arm stood straight up, base to flange face.

    **Not the working reach**, and the difference is the point of the caveat that goes
    with it. A robot's published reach is the radius of the sphere its tool frame can
    touch, which is a property of the joint limits and the link lengths *together*.
    This is one number off one pose, and E9.3's swept volume is what would answer the
    other question.
    """
    return _M7_REACH_MM


def m7_mass_kg() -> float:
    """The arm weighed by arithmetic, base included."""
    return sum(_m7_volumes().values()) * 1e-9 * _M7_DENSITY_KG_M3


def m7_moving_mass_kg() -> float:
    """What the base carries: everything above the first joint.

    The claim this one earns is that the mechanism's own `total_mass_kg` equals it —
    two counts of the same six links, one from the product graph through
    `app.dynamics.assembly.derive` and one from arithmetic here. A base accidentally
    declared as a body would double-count 111 kg and pass every geometry claim.
    """
    volumes = _m7_volumes()
    return (
        sum(volume for name, volume in volumes.items() if name != "base")
        * 1e-9
        * _M7_DENSITY_KG_M3
    )


def _m7_box_inertia(
    dimensions: tuple[float, float, float], mass_kg: float
) -> tuple[float, float, float]:
    """A rectangular block's principal inertia about its own centre, in kg mm^2.

    `m (b^2 + c^2) / 12` and its two rotations. Exact for the solid this rung draws,
    which is the whole of the claim: it is *not* the kernel's integration of the part
    that was built. See `_M7_UNPROVEN` and `MovingDesign`'s docstring for why the
    design package cannot ask the kernel for the measured tensor.
    """
    a, b, c = dimensions
    return (
        mass_kg * (b * b + c * c) / 12.0,
        mass_kg * (a * a + c * c) / 12.0,
        mass_kg * (a * a + b * b) / 12.0,
    )


def _m7_cylinder_inertia(
    diameter_mm: float, length_mm: float, mass_kg: float
) -> tuple[float, float, float]:
    """A solid cylinder about its own centre, its axis along z. `m r^2 / 2` about it."""
    radius = diameter_mm / 2.0
    across = mass_kg * (3.0 * radius * radius + length_mm * length_mm) / 12.0
    return (across, across, mass_kg * radius * radius / 2.0)


def _m7_inertia() -> dict[str, tuple[float, float, float]]:
    """The principal diagonal of every moving body, keyed by occurrence path.

    Every link is axis-aligned in the candle pose, so the body axes *are* the world
    axes and the closed form needs no rotation — which is exactly the condition
    `derive` documents for `inertia_kg_mm2` ("in world axes at the assembled pose").
    A rung whose assembled pose was not axis-aligned could not use this shortcut, and
    would have to rotate each tensor or leave the body a point mass.
    """
    volumes = _m7_volumes()
    inertia = {
        _m7_path(name): _m7_box_inertia(
            dimensions, volumes[name] * 1e-9 * _M7_DENSITY_KG_M3
        )
        for name, dimensions in _m7_links().items()
    }
    inertia[_m7_path("flange")] = _m7_cylinder_inertia(
        _M7_FLANGE_DIAMETER_MM,
        _M7_FLANGE_MM,
        volumes["flange"] * 1e-9 * _M7_DENSITY_KG_M3,
    )
    return inertia


def _m7_joints(*, base_is_a_body: bool = False) -> tuple[JointDeclaration, ...]:
    """The six revolutes, in chain order, each hung from the link below it.

    `base_is_a_body` is a break knob and nothing else: declaring the base as a moving
    body is the mistake that puts 111 kg of bolted-down casting into the arm's moving
    mass, and it is the one error here that makes every *reaction* bigger — which is
    the safe direction, so nothing downstream would complain.
    """
    first_parent = _m7_path("base") if base_is_a_body else None
    joints = [
        JointDeclaration(
            name="j1_base",
            kind="revolute",
            child=_m7_path("shoulder"),
            parent=first_parent,
            at_mm=(0.0, 0.0, _M7_BASE_TOP_MM),
            axis=(0.0, 0.0, 1.0),
        ),
        JointDeclaration(
            name="j2_shoulder",
            kind="revolute",
            child=_m7_path("upper_arm"),
            parent=_m7_path("shoulder"),
            at_mm=(0.0, 0.0, _M7_SHOULDER_TOP_MM),
            axis=(0.0, 1.0, 0.0),
        ),
        JointDeclaration(
            name="j3_elbow",
            kind="revolute",
            child=_m7_path("forearm"),
            parent=_m7_path("upper_arm"),
            at_mm=(0.0, 0.0, _M7_UPPER_ARM_TOP_MM),
            axis=(0.0, 1.0, 0.0),
        ),
        JointDeclaration(
            name="j4_roll",
            kind="revolute",
            child=_m7_path("wrist"),
            parent=_m7_path("forearm"),
            at_mm=(0.0, 0.0, _M7_FOREARM_TOP_MM),
            axis=(0.0, 0.0, 1.0),
        ),
        JointDeclaration(
            name="j5_pitch",
            kind="revolute",
            child=_m7_path("hand"),
            parent=_m7_path("wrist"),
            at_mm=(0.0, 0.0, _M7_WRIST_TOP_MM),
            axis=(0.0, 1.0, 0.0),
        ),
        JointDeclaration(
            name="j6_flange",
            kind="revolute",
            child=_m7_path("flange"),
            parent=_m7_path("hand"),
            at_mm=(0.0, 0.0, _M7_HAND_TOP_MM),
            axis=(0.0, 0.0, 1.0),
        ),
    ]
    if base_is_a_body:
        joints.insert(
            0,
            JointDeclaration(
                name="j0_floor",
                # Fixed, not revolute: the base is bolted down. A revolute here is
                # refused by `app.dynamics` in words — "nothing drives it, so its
                # motion is an output of the forces on it, which is a dynamics
                # problem, not a kinematic one" — which is the package declining to
                # guess rather than quietly integrating something.
                kind="fixed",
                child=_m7_path("base"),
                parent=None,
                at_mm=(0.0, 0.0, 0.0),
                axis=(0.0, 0.0, 1.0),
            ),
        )
    return tuple(joints)


def _m7_drivers() -> tuple[Driver, ...]:
    """What moves, and how. Four harmonics and two constant rates.

    Harmonic rather than constant on the three joints that carry the arm's mass,
    because a constant rate is a *steady* rotation: its angular acceleration is zero,
    so every body's acceleration is pure centripetal and the reaction never changes
    sign. That is a real load case and it is the easy one. A harmonic joint reverses
    twice a cycle, which is where a robot's peak joint torque actually occurs, and
    `Driver.derivatives_are_exact` is true for it — the velocity and acceleration are
    differentiated in closed form rather than finite-differenced off the position, so
    the peak is the peak and not a sampling artefact.
    """
    return (
        Driver(joint="j1_base", kind="harmonic", amplitude=0.6, frequency_hz=0.5),
        Driver(
            joint="j2_shoulder",
            kind="harmonic",
            amplitude=0.4,
            frequency_hz=0.5,
            phase_rad=math.pi / 2.0,
        ),
        Driver(
            joint="j3_elbow",
            kind="harmonic",
            amplitude=0.5,
            frequency_hz=0.5,
            phase_rad=math.pi,
        ),
        Driver(joint="j4_roll", kind="constant", rate=1.0),
        Driver(
            joint="j5_pitch",
            kind="harmonic",
            amplitude=0.3,
            frequency_hz=0.5,
            phase_rad=math.pi / 4.0,
        ),
        Driver(joint="j6_flange", kind="constant", rate=2.0),
    )


def _m7_structure(*, flange_offset_mm: float = 0.0) -> ProductStructure:
    """The arm as a graph, in the candle pose. Every link stacked on the one below.

    `flange_offset_mm` is a break knob: a tool flange machined off-centre is the
    classic way an arm acquires a wobble nothing in the drawing explains, and it is
    invisible to every dimension claim because no part changed size.
    """
    builder = StructureBuilder()
    builder.define(_M7_ROOT, description="Six-axis articulated robot arm.")
    for name, description in (
        ("base", "Bolted to the floor. Ground: it is not a body of the mechanism."),
        ("shoulder", "Rotates about the base's axis on j1."),
        ("upper_arm", "Shoulder to elbow."),
        ("forearm", "Elbow to wrist."),
        ("wrist", "Roll."),
        ("hand", "Pitch."),
        ("flange", "The tool flange. Nothing is mounted to it."),
    ):
        builder.define(
            name,
            design=f"M7 {name.replace('_', ' ')}",
            material="steel-1018",
            description=description,
        )

    for component, bottom_mm in (
        ("base", 0.0),
        ("shoulder", _M7_BASE_TOP_MM),
        ("upper_arm", _M7_SHOULDER_TOP_MM),
        ("forearm", _M7_UPPER_ARM_TOP_MM),
        ("wrist", _M7_FOREARM_TOP_MM),
        ("hand", _M7_WRIST_TOP_MM),
    ):
        builder.add(
            _M7_ROOT,
            component,
            placement=at(0.0, 0.0, bottom_mm),
            note=f"{component} underside at z = {bottom_mm:g} mm.",
        )
    builder.add(
        _M7_ROOT,
        "flange",
        placement=at(flange_offset_mm, 0.0, _M7_HAND_TOP_MM),
        note="Tool flange on the hand's face.",
    )
    return builder.build(_M7_ROOT)


def _m7_assembly(*, structure: ProductStructure | None = None) -> AssemblyDesign:
    """The arm's geometry: the graph and a design per link."""
    parts: dict[str, DesignSpec] = {
        name: _solid_block_spec(
            f"M7 {name.replace('_', ' ')}",
            width_mm=dimensions[0],
            depth_mm=dimensions[1],
            height_mm=dimensions[2],
            description=f"Robot arm link: {name.replace('_', ' ')}.",
        )
        for name, dimensions in _m7_links().items()
    }
    parts["base"] = _solid_cylinder_spec(
        "M7 base",
        diameter_mm=_M7_BASE_DIAMETER_MM,
        length_mm=_M7_BASE_MM,
        description="Bolted to the floor.",
    )
    parts["flange"] = _solid_cylinder_spec(
        "M7 flange",
        diameter_mm=_M7_FLANGE_DIAMETER_MM,
        length_mm=_M7_FLANGE_MM,
        description="Tool flange.",
    )
    return AssemblyDesign(
        structure=_m7_structure() if structure is None else structure,
        parts=parts,
        clearance_mm=_M7_INSPECTION_MM,
        parameters=ParameterSet.of(
            [
                Parameter("reach_mm", Unit.MM, value=m7_reach_mm()),
                Parameter("base_diameter_mm", Unit.MM, value=_M7_BASE_DIAMETER_MM),
                Parameter("mass_closed_form_kg", Unit.KG, value=m7_mass_kg()),
                Parameter(
                    "moving_mass_closed_form_kg",
                    Unit.KG,
                    value=m7_moving_mass_kg(),
                    description=(
                        "Everything above the first joint. The base is ground and is "
                        "deliberately not a body; a rung that declared it one would "
                        "make every reaction larger, which is the direction nothing "
                        "downstream complains about."
                    ),
                ),
                Parameter("occurrence_count", Unit.NONE, value=7.0),
                Parameter("joint_count", Unit.NONE, value=6.0),
                Parameter("sample_count", Unit.NONE, value=float(_M7_SAMPLES)),
                Parameter(
                    "free_body_tolerance_n",
                    Unit.NEWTON,
                    value=1e-6,
                    description=(
                        "What the Newton-Euler balance may fail to close by. Not a "
                        "physical slack: the recursion is closed form, so anything "
                        "above rounding is a missing term."
                    ),
                ),
            ]
        ),
    )


def _m7_design(
    *,
    structure: ProductStructure | None = None,
    joints: tuple[JointDeclaration, ...] | None = None,
    drivers: tuple[Driver, ...] | None = None,
    inertia_kg_mm2: Mapping[str, tuple[float, float, float]] | None = -1,  # type: ignore[assignment]
    samples: int = _M7_SAMPLES,
) -> MovingDesign:
    """The arm, its joints and its motion.

    `inertia_kg_mm2` defaults to a sentinel rather than to `None` so that a test can
    ask for *no* inertia — every body a point mass — which is a different thing from
    "use the rung's own tensors" and would otherwise be unsayable.
    """
    return MovingDesign(
        assembly=_m7_assembly(structure=structure),
        joints=_m7_joints() if joints is None else joints,
        drivers=_m7_drivers() if drivers is None else drivers,
        motion=MotionRange(duration_s=_M7_DURATION_S, samples=samples),
        inertia_kg_mm2=_m7_inertia() if inertia_kg_mm2 == -1 else inertia_kg_mm2,
    )


_M7_ASSERTIONS: Final = (
    Assertion(
        name="the roll-up equals the closed form over every link",
        measure="mass_kg",
        comparison="==",
        bound="=mass_closed_form_kg",
        tolerance=1e-6,
    ),
    Assertion(
        name="the graph holds exactly the parts the bill of materials counts",
        measure="clash.occurrence_count",
        comparison="==",
        bound="=occurrence_count",
    ),
    Assertion(
        name="no two links occupy the same space in the shipped pose",
        measure="clash.clash_count",
        comparison="==",
        bound=0.0,
        note=(
            "The candle pose only. A serial chain's interesting clashes are the ones it "
            "finds *while moving*, and this claim does not look for them — E9.3's swept "
            "volume does, and `app/dynamics/clearance.py` is where that question lives."
        ),
    ),
    Assertion(
        name="the arm stands as tall as its links add up to",
        measure="envelope_mm.size[2]",
        comparison="==",
        bound="=reach_mm",
        tolerance=1e-3,
    ),
    Assertion(
        name="the arm is as wide as its base",
        measure="envelope_mm.size[0]",
        comparison="==",
        bound="=base_diameter_mm",
        tolerance=1e-3,
        note="Every link is narrower than the base, so in this pose the base is the plan view.",
    ),
    Assertion(
        name="the arm is stacked on its own axis",
        measure="centre_of_mass_mm[0]",
        comparison="==",
        bound=0.0,
        tolerance=1e-6,
        note="A flange machined off-centre moves this and no dimension on the drawing.",
    ),
    Assertion(
        name="the mechanism carries everything above the first joint and nothing below",
        measure="motion.total_mass_kg",
        comparison="==",
        bound="=moving_mass_closed_form_kg",
        tolerance=1e-9,
        note=(
            "**The claim that joins the two halves of this rung.** The left side is the "
            "product graph rolled up through `app.dynamics.assembly.derive`; the right is "
            "arithmetic over the same links. The base is ground, so it is in the mass and "
            "not in the mechanism, and that is the one thing about this arm a reader "
            "would get wrong."
        ),
    ),
    Assertion(
        name="every joint reported a reaction",
        measure="motion.unavailable_reaction_count",
        comparison="==",
        bound=0.0,
        note=(
            "Asserted *before* any claim about the peak, because "
            "`motion.peak_reaction_force_n` is a maximum over the joints that answered: "
            "with a joint missing it is a lower bound wearing the name of a peak."
        ),
    ),
    Assertion(
        name="the chain's free-body balance closes",
        measure="motion.free_body_residual_n",
        comparison="<=",
        bound="=free_body_tolerance_n",
        note=(
            "Newton-Euler consistency: every body's `ma` is accounted for by the joint "
            "loads on it. **Not a claim that the answer is right** — it is a claim that "
            "the answer is self-consistent, which is the strongest thing an inverse "
            "dynamics run can say about itself, and it is what would catch a mass, an "
            "inertia or a gravity term dropped on one link."
        ),
    ),
    Assertion(
        name="the arm in motion loads its base harder than standing still would",
        measure="motion.peak_reaction_force_n",
        comparison=">",
        bound="=moving_mass_closed_form_kg",
        note=(
            "A deliberately weak inequality with a strong purpose: the peak reaction in "
            "newtons must exceed the moving mass in kilogrammes, which it does by roughly "
            "g — so this fails loudly if a unit is dropped somewhere between the roll-up's "
            "kilogrammes and the reaction's newtons. The *interesting* comparison, against "
            "the static weight, is in the tests, where the static case can be built."
        ),
    ),
    Assertion(
        name="the motion was sampled as asked",
        measure="motion.step_count",
        comparison="==",
        bound="=sample_count",
    ),
    Assertion(
        name="no link was reduced to a point mass",
        measure="motion.point_mass_count",
        comparison="==",
        bound=0.0,
        note=(
            "A point mass has no rotary term of its own, so a spinning link contributes "
            "nothing to the torque at the joint driving it. `app.dynamics` states that "
            "cost rather than hiding it; this rung refuses to pay it."
        ),
    ),
    Assertion(
        name="nothing about the motion needed a warning",
        measure="motion.warning_count",
        comparison="==",
        bound=0.0,
        note=(
            "`MotionPath.warnings` is where a tabulated driver's finite-differenced "
            "derivatives are declared, among other things. Every driver here is closed "
            "form, so a warning appearing means one stopped being."
        ),
    ),
)


_M7_UNPROVEN: Final = (
    "E6 — **the arm is rigid, and stiffness under motion is a third of what the master "
    "plan says this rung is about**. No load case has been run on any link, so there is "
    "no deflection at the tool — which is the number a robot's repeatability "
    "specification is actually about, and the reason a long forearm is a casting rather "
    "than a bar",
    "E9.3 — the clash check runs in the shipped pose only. A serial chain's interesting "
    "collisions are the ones it finds *while moving*, and the swept volume that would "
    "find them is `app/dynamics/clearance.py`'s question, not asked here",
    "E9.3 — `m7_reach_mm` is the tip height in one pose and **not the working reach**, "
    "which is a property of the joint limits and the link lengths together. There are no "
    "joint limits on this mechanism at all, so every driver swings through whatever "
    "angle it is given and nothing refuses an elbow that folds through its own forearm",
    "E9 — the inertia tensors are **closed forms for the idealised solids this rung "
    "draws, not the kernel's integration of the parts that were built**. E9.6 put "
    "measured tensors in the roll-up and `derive` will take them, but building that "
    "measurer needs `app.assembly.inertia.from_document`, which reads an OCCT document "
    "— and this package may not import the kernel, which is what keeps its tests "
    "offline. For seven axis-aligned prisms the two agree exactly; for a casting they "
    "would not, and nothing here would notice",
    "E9 — nothing is driven *by* anything. The joint angles are prescribed functions of "
    "time, so there are no motors, no gearboxes, no torque limits and no check that a "
    "drive able to produce the computed joint torque exists. A motion no real arm could "
    "perform is reported with the same confidence as one it could",
    "E9 — no contact, friction, springs, end stops or flexible bodies, and no case where "
    "the motion is an output rather than an input. `app/dynamics/` refuses each of those "
    "by name rather than approximating it, and this rung inherits the refusals",
    "E8 — no fatigue. A robot's duty cycle is the same move a few million times, which "
    "is precisely the case `app/fatigue/duty.py` was built for, and the reactions this "
    "rung computes are the input it wants. Nothing has joined them up",
    "E12.3 — there is not one bearing, gearbox, motor or bolt in this bill of materials. "
    "The six joints are kinematic declarations; a real arm's joint is a bearing pair and "
    "a reducer, and `app.parts.bearings.select` would refuse to size them anyway because "
    "the shipped table carries no load ratings",
    "E13 — the links are solid steel prisms. A real arm's upper arm is a thin-walled "
    "casting or a welded box, so this arm is several times heavier than its equivalent "
    "and every reaction it reports is correspondingly large. The numbers are right for "
    "the machine described and the machine described is not one anybody would build",
    "E17 — nothing is fastened. How a link is attached to the one below it is the whole "
    "engineering content of a robot joint, and this rung's joints are declarations",
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
        # Its declared needs were E12.3 (standard parts), E12.4 (a parts selection
        # engine) and E13.2 (tolerance and GD&T, for the stacks). E12 is complete;
        # E13.2 is PARTIAL and what is open in it is a document — ISO 286's
        # deviation tables — while `app/rules/stackup.py`, the half this rung uses,
        # is in. So it moved on 2026-09-16, by M6's rule rather than by anyone
        # deciding it should, and what it does not have is in `_M4_UNPROVEN`.
        assembly=_m4_design(),
        assertions=_M4_ASSERTIONS,
        unproven=_M4_UNPROVEN,
    ),
    Mission(
        rung="M5",
        title="Sheet-metal stamping press",
        era="V",
        hard="Force path, frame stiffness, die set, drive, guarding",
        # Its declared needs were E17.3, E12.3, E13 and E14. Three of the four are
        # complete; E13 is three tasks of four with the open one — E13.2 — open on a
        # *document*, ISO 286's deviation tables, which this rung does not read. So
        # it moved on 2026-09-16 by M6's rule, as M4 did the same day, and what a
        # press needs that nobody here has checked is in `_M5_UNPROVEN` — eleven
        # entries, of which frame stiffness is the one a buyer would ask about first.
        assembly=_m5_design(),
        assertions=_M5_ASSERTIONS,
        unproven=_M5_UNPROVEN,
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
        # Its declared needs were E9 and E9.3. E9.1 put Project Chrono behind a
        # container, E9.6 put inertia tensors in the roll-up, and the exact
        # serial-chain recursion and Newton-Euler reactions have been in
        # `app/dynamics/` since the phase opened. So it moved on 2026-09-16, and
        # it is the first `MovingDesign`: the first rung whose answer is a load
        # rather than a dimension. Stiffness — the third of the three things the
        # `hard` column names — is in `_M7_UNPROVEN` and is E6's.
        moving=_m7_design(),
        assertions=_M7_ASSERTIONS,
        unproven=_M7_UNPROVEN,
    ),
    Mission(
        rung="M8",
        title="Motorcycle chassis + swingarm",
        era="VI",
        hard="Fatigue under real duty cycles, MBD loads, homologation",
        needs=(
            "E8 — fatigue and durability (pyLife)",
            "E9.4 — joint-load extraction feeding FEA",
            "E17.3 — tubular weldments",
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
