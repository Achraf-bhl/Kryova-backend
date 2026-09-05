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
instance. A mission that passed because a previous mission left a body behind is
the exact false green a regression suite exists to prevent, and it would be
invisible — the volumes would simply be right for the wrong reason.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.design.assertions import Assertion, AssertionReport, check_assertions
from app.design.compile import compile_spec
from app.design.errors import SpecError
from app.design.execute import BuildReport, CallRunner, execute_plan
from app.design.params import Parameter, Unit
from app.design.spec import DesignSpec, FeatureSpec, expr, ref


class MissionOutcome(StrEnum):
    """How one rung of the ladder came out.

    `PENDING` is not a skip and not a soft pass. It means the rung was never
    attempted because the capability it needs does not exist yet, and the
    programme has not climbed it.
    """

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True)
class Mission:
    """One rung: a machine, what makes it hard, and either a design or a reason.

    A rung is buildable exactly when it carries a `spec`. The two states are kept
    apart by validation rather than by convention, because the failure they guard
    against is a rung drifting into "declared but claiming nothing", which reads
    as coverage and is not.
    """

    rung: str
    title: str
    era: str

    #: What makes this machine hard — the master plan's own ladder column, kept
    #: with the rung so a reader of a failing report knows what it was testing.
    hard: str

    #: The design, when one exists. `None` means the rung is not yet reachable.
    spec: DesignSpec | None = None

    #: What must be true of the built part. Checked by `assertions.py` against the
    #: measurement payload the build reports.
    assertions: tuple[Assertion, ...] = ()

    #: Capability this rung waits on, each naming the phase that owns it. Required
    #: when there is no spec, forbidden when there is — a rung cannot be both
    #: buildable and waiting, and allowing both would hide a half-built mission.
    needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rung.startswith("M") or not self.rung[1:].isdigit():
            raise SpecError(
                f"A rung is named M1..M9; got {self.rung!r}. The name is how the "
                "ladder in the master plan and this suite are kept in step."
            )
        if self.spec is None:
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
        return self.spec is not None

    def __str__(self) -> str:
        return f"{self.rung} — {self.title}"


@dataclass(frozen=True)
class MissionResult:
    """What running one rung found."""

    mission: Mission
    outcome: MissionOutcome

    build: BuildReport | None = None
    checks: AssertionReport | None = None

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
        return f"{head} — {self.reason}" if self.reason else head

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
        if self.build is not None:
            out["build"] = self.build.to_dict()
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
    def complete(self) -> bool:
        """Is the ladder actually climbed? True only when nothing is pending."""
        return bool(self.results) and not self.failed and not self.pending

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
        line = ", ".join(bits) + "."
        detail = [str(r) for r in self.results if r.outcome is not MissionOutcome.PASSED]
        return line + ("\n" + "\n".join(detail) if detail else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "complete": self.complete,
            "passed": len(self.passed),
            "failed": len(self.failed),
            "pending": len(self.pending),
            "results": [result.to_dict() for result in self.results],
        }


def run_mission(mission: Mission, runner: CallRunner) -> MissionResult:
    """Build one rung and check what it claims about itself.

    Never raises for a build or assertion outcome — a rung that does not build is
    a finding, and a suite that raised would stop at the first one and never tell
    you about the other eight.
    """
    if mission.spec is None:
        return MissionResult(
            mission=mission,
            outcome=MissionOutcome.PENDING,
            reason="not yet buildable — waiting on " + "; ".join(mission.needs),
        )

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
        results.append(run_mission(mission, runner_factory()))
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
        needs=(
            "E6 — a real solver, to size anything",
            "E8.3 — weld classification to BS 7608 / Eurocode 3",
            "E17.4 — weldments and cut lists",
        ),
    ),
    Mission(
        rung="M3",
        title="Sheet-metal enclosure",
        era="IV",
        hard="Unfolding, bend allowance, DFM",
        needs=(
            "E17.3 — sheet metal: wall, bend, flange, unfold, K-factor",
            "E13.1 — design rules as assertions, per process",
        ),
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
        needs=(
            "E14.1 — product structure and BOM as first-class data",
            "E12.3 — standard parts",
        ),
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
    "LadderReport",
    "Mission",
    "MissionOutcome",
    "MissionResult",
    "mission",
    "run_ladder",
    "run_mission",
]
