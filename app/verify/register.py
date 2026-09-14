"""The verification register: which analyses agree with a published benchmark,
against what, to what accuracy — and, the number that earns the module, **which
do not**. Nothing in it is validated, and it says so.

## What this register used to call itself, and why it stopped (master plan 20.3)

Until 2026-09-14 this was "the validation register", its best standing was
`VALIDATED`, and its first published note said validation "is answered only by a
published benchmark with a published result". ASME's definitions say otherwise
(`app.verify.standards.DEFINITIONS`): a NAFEMS target is the answer to a stated
mathematical problem, so agreeing with it is **code verification**, and
validation needs experimental data — measurements of a physical part — which
this product has never had. So the register's name was a claim it could not make
on its own behalf, published on the one page whose value is that every word on
it can be checked. The standings are now `AGREES` / `DISPUTED` / `UNCONFIRMED`
against a published benchmark, every piece of evidence is labelled as the kind
of verification it is, and the payload carries `standards.validation_block()` —
the statement that nothing is validated, with the terms quoted and sourced.

Why not simply `VERIFIED`: the closed-form checks are verification too. An
analysis with Euler's column formula behind it and no NAFEMS case would then read
"unverified", which is false in the other direction. What the standing measures
is agreement with a *published benchmark*, so that is what it is called.

Master plan **7.4**, published through **P10.3**. `benchmarks.py` decided what a
benchmark may claim; this decides what a *roll-up of benchmarks* may claim, which
is a different and easier thing to get wrong. A register is the one artefact in
this codebase whose readers are outside it, and the failure mode of a
publishable summary is not a wrong number — it is a true number with the
denominator left out.

## The three rules, and why each is structural rather than editorial

**1. The denominator is declared, not discovered.** `ANALYSES` below lists every
analysis this product will report a number from, and the register is built by
walking *that* list and attaching whatever benchmark outcomes exist for each. An
analysis nobody has benchmarked therefore appears, as `UNCONFIRMED`, with a
reason. This is `app.observe.catalogue` applied to benchmarks and it is here for
the identical reason: a report assembled from what happened to run cannot
distinguish "there is no benchmark for buckling" from "buckling was never
benchmarked" from "somebody deleted the buckling suite". Assembled from a
declared list, all three read as the same visible hole, which is the honest
answer to all three.

**2. Only `Outcome.AGREED` counts, and the four honest non-results are
counted separately rather than dropped.** `MEASURED` — it ran, there was nothing
to compare against — is the one that would do the damage, because it is a real
result and it is *not* agreement. `UNCONVERGED` is worse to conflate: the case
ran, produced a number, and 7.2 refused to let the number out. Both appear in
`Summary`, in their own fields, and neither moves an analysis to `AGREES`.
`benchmarks.BenchmarkOutcome.passed` already encodes this; the register calls it
rather than re-deriving it, so there is one definition of a pass in the package.

**3. The register republishes nothing `benchmarks.py` would refuse.**
`Target.__post_init__` is the rule that stops a remembered number becoming a
citation, and a frozen dataclass is only frozen against ordinary assignment — a
`Target` reached through `object.__setattr__`, `__new__`, or an unpickle never
sees that constructor. So `_republish` reconstructs every target before printing
it, and a target that fails reconstruction has its **number withheld** and the
refusal published in its place. The row stays: withholding a value is honest,
deleting the row is not.

## Why a refused target does not raise

`run_benchmark` never raises because a suite is a report and one bad case must
not remove the evidence for the other twenty. The same argument applies one
level up with more force, since this report is public: a register that 500s
because one target was malformed publishes nothing at all, which is a strictly
worse outcome than publishing nineteen good rows and one loud refusal. So
refusals are data (`AnalysisRow.refusals`, `Summary.refused_targets`) and they
force the row's standing to `DISPUTED` regardless of the outcome recorded
against it.

## What the register does *not* do: run anything

`published_register()` reports on `PUBLISHED_SUITE`, and `assert_publishable`
refuses that suite at import if it contains a runnable case. A published
benchmark is a mesh convergence study — minutes of CPU, several solves — and
running one inside an HTTP request thread on a public, unauthenticated route is
a denial-of-service tool with a nice name. So `PUBLISHED_SUITE` is the *blocked*
half of `app.verify.nafems`, and a case that executes reaches this register only
as a recorded outcome passed to `Register.build` — which is why that function
takes outcomes as an argument rather than a suite.

## What it says

The recorded run decides how many analyses agree; `tests/test_verify_register.py`
names them rather than this docstring, because a count typed into prose is the
one that goes stale. The blocked cases always appear, with their published
targets and their named blockers: the numbers the product must eventually
reproduce, and why it cannot yet.

The closed-form checks listed against several analyses are real, pinned by
tests, and code verification exactly as a NAFEMS agreement is — but they are
checked in the suite rather than recorded for publication, so they are carried
in a field of their own and do not move a standing. None of either kind is
validation. The terms are ASME VVUQ 1's split (`standards.TERMINOLOGY_SOURCE`),
quoted as V&V 10 and V&V 20 define them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Final

from app.verify import recorded, standards
from app.verify.benchmarks import BenchmarkOutcome, Outcome, Suite, Target
from app.verify.nafems import NAFEMS_SUITE


class Standing(StrEnum):
    """Where one analysis stands against published benchmark solutions.

    Three, not six: an `Outcome` is a verdict on one *case*, and a reader of a
    trust page is asking about an *analysis*. The mapping is deliberately
    pessimistic in both directions — one deviated case makes the analysis
    `DISPUTED` even if nine others agreed, because "nine of ten agreed" is the
    sentence a reader must be given rather than a green tick.

    **None of the three is a validation standing**, including the best one:
    agreeing with a published reference solution is code verification. See the
    module docstring for the name this enum used to carry.
    """

    #: At least one case agreed with a known target, and no case deviated,
    #: errored, or carried a target the register would not republish.
    AGREES = "agrees"
    #: At least one case ran against a known target and landed outside its band,
    #: or errored. Not a pass, and louder than merely unconfirmed.
    DISPUTED = "disputed"
    #: Everything else, including — and mostly — "nobody has benchmarked this".
    #: `AnalysisRow.because` always says which.
    UNCONFIRMED = "unconfirmed"


@dataclass(frozen=True, slots=True)
class Analysis:
    """One analysis the product will report a number from.

    Declared whether or not a benchmark exists for it. That is the whole
    mechanism: the register's denominator comes from this tuple, so writing a
    new analysis and not benchmarking it makes the register *worse-looking*,
    which is the correct incentive.
    """

    id: str
    title: str
    #: The dotted module a reader can open. Checked to exist by the tests, so a
    #: rename cannot leave the register pointing at nothing.
    module: str
    #: What question the analysis answers, in the words an engineer would use.
    answers: str
    #: Closed-form solutions the suite already checks this against.
    #: **Code verification, not validation** — see the module docstring.
    #: Each entry names the identity and the test that pins it.
    closed_form_checks: tuple[str, ...] = ()
    #: False when the analysis is declared but a user cannot reach it yet.
    available: bool = True
    unavailable_because: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("An analysis needs an id; the register keys on it.")
        if not self.available and not self.unavailable_because.strip():
            raise ValueError(
                f"Analysis {self.id!r} is declared unavailable with no reason. "
                "'Not available' with no next step is a complaint, not a task."
            )
        if self.available and self.unavailable_because.strip():
            raise ValueError(f"Analysis {self.id!r} is available; it needs no excuse.")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "module": self.module,
            "answers": self.answers,
            "available": self.available,
            # Always present, always labelled. A field named
            # `closed_form_checks` that vanished when empty would let a reader
            # assume the ones they *do* see are the same kind of evidence as an
            # agreeing row.
            "verification_closed_form_checks": list(self.closed_form_checks),
        }
        if not self.available:
            payload["unavailable_because"] = self.unavailable_because
        return payload


#: Every analysis Kryova will state a number from. The register's denominator.
#:
#: Wider than "the FEA solvers" on purpose. A clash distance, an unfolded blank
#: length and a fatigue damage sum are all numbers a user acts on, and an
#: engineer reading a verification register wants to know which of them anybody
#: has checked against an outside answer. Restricting the list to the three
#: solvers would make the register look nearly complete the day three benchmarks
#: land, while most of what the product actually reports went unmentioned.
ANALYSES: Final[tuple[Analysis, ...]] = (
    Analysis(
        id="linear-static",
        title="Linear static stress and displacement",
        module="app.solve.linear_static",
        answers="How far does it move and how hard is it working, under one static load set?",
        closed_form_checks=(
            "bar in tension, sigma = F/A and delta = FL/AE, to 1e-6 "
            "(tests/test_solver.py)",
            "quadratic tet10 beats linear tet4 against the Euler-Bernoulli cantilever "
            "at equal element count (tests/test_solver.py::TestQuadraticElements)",
        ),
    ),
    Analysis(
        id="modal",
        title="Natural frequencies and mode shapes",
        module="app.solve.modal",
        answers="What does it ring at, and will a drive frequency sit on top of a mode?",
        closed_form_checks=(
            "axial bar, f = (2n-1)/4L * sqrt(E/rho) (tests/test_modal.py)",
            "cantilever Euler-Bernoulli modes 1-3 (tests/test_modal.py)",
            "six rigid-body modes on a free-free part (tests/test_modal.py)",
        ),
    ),
    Analysis(
        id="buckling",
        title="Linear buckling load factor",
        module="app.solve.buckling",
        answers="At what multiple of this load does it go unstable rather than yield?",
        closed_form_checks=("Euler column, P = pi^2 EI / (KL)^2 (tests/test_buckling.py)",),
    ),
    Analysis(
        id="thermal-stress",
        title="Thermal stress from a temperature change",
        module="app.solve.thermal",
        answers="What stress does a restrained part carry when it is not allowed to expand?",
        closed_form_checks=(
            "fully restrained bar, sigma = -E alpha deltaT "
            "(tests/test_thermal.py::TestThermalStrain)",
        ),
    ),
    Analysis(
        id="steady-conduction",
        title="Steady heat conduction",
        module="app.solve.conduction",
        answers="How hot does it get, held, heated and cooled the way it is?",
        closed_form_checks=(
            "bar held at both ends, a linear profile (tests/test_conduction.py::TestABarHeldAtBothEnds)",
            "hollow cylinder, the logarithmic wall profile "
            "(tests/test_conduction.py::TestAHollowCylinder)",
            "convecting bar, the Biot tip temperature T_tip = (T_b + Bi T_inf)/(1 + Bi) "
            "(tests/test_conduction.py::TestAConvectionBoundary)",
        ),
    ),
    Analysis(
        id="transient-conduction",
        title="Transient heat conduction",
        module="app.solve.conduction",
        answers="How long until it reaches a temperature, and what path does it take?",
        closed_form_checks=(
            "lumped-capacitance cooling, T(t) = T_inf + (T0 - T_inf) exp(-t/tau) "
            "(tests/test_conduction.py::TestALumpedCapacitanceCooldown)",
        ),
    ),
    Analysis(
        id="laminar-flow",
        title="Laminar duct flow and forced convection (OpenFOAM)",
        module="app.solve.openfoam",
        answers=(
            "What pressure does it take to push a fluid through this passage, and how much "
            "heat does the flow carry away?"
        ),
        closed_form_checks=(
            "round pipe, Hagen-Poiseuille dp/dz = 8 nu U / R^2 and u_max = 2U "
            "(tests/test_solver_openfoam.py::TestAPipeIsHagenPoiseuille)",
            "square duct from a tet mesh, the rectangular-duct series solution "
            "(tests/test_solver_openfoam.py::TestASquareDuctFromATetMesh)",
            "developed Nusselt number 3.657 on an isothermal wall and 48/11 under a "
            "uniform flux, with the heat balance "
            "(tests/test_solver_openfoam.py::TestAPipeIsHagenPoiseuille, "
            "TestAPipeUnderAUniformHeatFlux)",
            "all of these run only where the pinned OpenFOAM image is present, and CI has none",
        ),
    ),
    Analysis(
        id="fatigue",
        title="Fatigue damage and life",
        module="app.fatigue.assessment",
        answers="How long does it last under a repeating duty cycle, not one static load?",
    ),
    Analysis(
        id="kinematics",
        title="Mechanism kinematics",
        module="app.dynamics.kinematics",
        answers="Where does every body sit at each point of the travel?",
    ),
    Analysis(
        id="clearance",
        title="Clearance through a motion range",
        module="app.dynamics.clearance",
        answers="Does the moving part clear its surroundings everywhere it goes?",
    ),
    Analysis(
        id="clash",
        title="Static interference between parts",
        module="app.assembly.clash",
        answers="Do two placed parts occupy the same space?",
    ),
    Analysis(
        id="mass-properties",
        title="Mass, volume, centroid and inertia",
        module="app.kernel.measurement",
        answers="What does the part weigh, and where is its mass?",
    ),
    Analysis(
        id="sheet-metal-unfold",
        title="Sheet-metal unfolding and bend allowance",
        module="app.sheetmetal.unfold",
        answers="How big is the flat blank, given the bend radii and the K-factor?",
    ),
    Analysis(
        id="optimisation",
        title="Parameter optimisation and design sensitivity",
        module="app.optimise.run",
        answers="Which parameter should move, and how far, to hit the target?",
    ),
)

BY_ID: Final[dict[str, Analysis]] = {analysis.id: analysis for analysis in ANALYSES}

if len(BY_ID) != len(ANALYSES):  # pragma: no cover - a duplicate is a typo, caught at import
    raise ValueError("two analyses share an id")


#: Reasons an analysis agrees with no benchmark, spelled once so the register
#: cannot report the same hole in three different words.
NO_BENCHMARK: Final = (
    "No benchmark is encoded for this analysis. Nothing has been compared against "
    "a published answer, so no benchmark agreement is claimed."
)


# --------------------------------------------------------------------------
# Republishing a target
# --------------------------------------------------------------------------

#: What a withheld target says instead of a number. Not an empty field: an
#: absent value reads as "no target", which is a different and gentler claim
#: than "there was a target and it was not fit to print".
WITHHELD: Final = "withheld"


def _republish(target: Target) -> tuple[dict[str, Any], str | None]:
    """The target as the register may print it, and the refusal if it may not.

    Reconstructs the target through `Target.__post_init__` rather than trusting
    the instance handed in. A frozen dataclass is frozen against `t.value = 3`
    and nothing else — `object.__setattr__`, `Target.__new__` and an unpickle
    all produce an instance whose constructor never ran. This is the only place
    in the register a benchmark number becomes public, so it is the place the
    check belongs.
    """
    try:
        Target(
            basis=target.basis,
            unit=target.unit,
            value=target.value,
            tolerance=target.tolerance,
            tolerance_reason=target.tolerance_reason,
            source=target.source,
            reason=target.reason,
        )
    except (ValueError, TypeError) as exc:
        return (
            {
                "basis": str(target.basis),
                "unit": str(target.unit),
                "value": WITHHELD,
                "source": WITHHELD,
            },
            str(exc),
        )
    return target.to_dict(), None


# --------------------------------------------------------------------------
# Scrubbing, because this is published
# --------------------------------------------------------------------------

#: Provenance keys the public register may print, and nothing else. An allowlist
#: rather than a denylist: `RunProvenance.to_dict` grows, and a denylist grows
#: only when somebody remembers to extend it.
#:
#: What is left out and why: `geometry.source` is free text and is documented as
#: holding "a media blob's sha256 **or** a description" — a customer's file name
#: lands there; `load_case.name` is user-typed; `material` may be a
#: customer-defined alloy rather than a library one; `notes` is free text.
_PUBLIC_PROVENANCE: Final[dict[str, tuple[str, ...]]] = {
    "mesh": (
        "element_type",
        "element_order",
        "node_count",
        "element_count",
        "volume_mm3",
        "min_quality",
        "sliver_count",
        "element_size_mm",
    ),
    "solver": ("name", "version", "code_digest"),
    "environment": ("python", "numpy", "scipy", "platform"),
    "geometry": ("digest",),
    "load_case": ("type", "digest"),
}

#: Anything that looks like a filesystem path. `detail` on an `ERRORED` outcome
#: carries an exception's own message, and `FileNotFoundError` puts an absolute
#: path in one — on the Windows seat that path starts `C:\Users\<the customer>`.
#: The register is published, so a path in it is a leak whoever wrote it.
#:
#: **A drive letter is exactly one character, and the lookbehind is what says
#: so.** Without it the branch also matches the `s:/` inside `https://`, and
#: `scrub` replaces the *whole* string — so a benchmark whose reason cites the
#: document it came from would publish "[withheld: looked like a filesystem
#: path]" instead of the citation. On a page whose entire value is checkable
#: references, that is the guard destroying what it protects. Found 2026-09-08
#: when `app.verify.nafems` landed, carrying a URL in every source.
_PATH_RE: Final = re.compile(
    r"""(
        (?<![A-Za-z])[A-Za-z]:[\\/]    # C:\ or C:/ — but not the s:/ of https://
        | \\\\[^\s\\]+\\               # \\server\share
        | (?:^|(?<=[\s"'(]))/(?:home|Users|root|mnt|var|tmp|opt|srv)/
    )""",
    re.VERBOSE,
)

_WITHHELD_PATH: Final = "[withheld: looked like a filesystem path]"


def scrub(text: str) -> str:
    """Free text as it may be published.

    Whole-string replacement rather than surgery on the match: a message with a
    path in it has already told a reader something about the machine, and half
    of it is not safer than all of it.
    """
    return _WITHHELD_PATH if _PATH_RE.search(text) else text


def _public_provenance(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """A provenance record reduced to what an outsider may see.

    Returns `None` for a record with nothing publishable left, so the key is
    absent rather than an empty dict pretending to be evidence.
    """
    if not payload:
        return None
    kept: dict[str, Any] = {}
    for section, allowed in _PUBLIC_PROVENANCE.items():
        block = payload.get(section)
        if not isinstance(block, dict):
            continue
        subset = {
            key: value
            for key, value in block.items()
            if key in allowed and not isinstance(value, dict | list)
        }
        if subset:
            kept[section] = {
                key: scrub(value) if isinstance(value, str) else value
                for key, value in subset.items()
            }
    return kept or None


def _public_outcome(outcome: BenchmarkOutcome, target: dict[str, Any]) -> dict[str, Any]:
    """One case, as the published register prints it."""
    payload: dict[str, Any] = {
        "id": scrub(outcome.benchmark_id),
        "title": scrub(outcome.title),
        "outcome": str(outcome.outcome),
        "evidence": str(standards.EvidenceKind.CODE_VERIFICATION),
        "counts_as_agreement": outcome.passed,
        "target": target,
        "measured_value": outcome.measured_value,
        "relative_deviation": outcome.relative_deviation,
        "seconds": round(outcome.seconds, 3),
    }
    if outcome.detail:
        payload["detail"] = scrub(outcome.detail)
    provenance = _public_provenance(outcome.provenance)
    if provenance is not None:
        payload["provenance"] = provenance
    if outcome.convergence is not None:
        payload["convergence"] = {
            "evidence": str(standards.EvidenceKind.SOLUTION_VERIFICATION),
            **outcome.convergence,
        }
    return payload


# --------------------------------------------------------------------------
# Rows and the register
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Accuracy:
    """How close the agreeing cases came — the "to what accuracy" of 7.4.

    The *worst* deviation, not the mean. A register quoting an average is
    quoting a number no individual result ever had, and the question an engineer
    is asking is "how wrong could this be", which only the worst case answers.
    """

    worst_relative_deviation: float
    widest_band: float
    cases: int
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "worst_relative_deviation": self.worst_relative_deviation,
            "worst_percent": round(self.worst_relative_deviation * 100.0, 4),
            "widest_accepted_band": self.widest_band,
            "cases": self.cases,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class AnalysisRow:
    """One analysis, its evidence, and — when there is none — why."""

    analysis: Analysis
    standing: Standing
    because: str
    outcomes: tuple[BenchmarkOutcome, ...] = ()
    refusals: tuple[str, ...] = ()
    accuracy: Accuracy | None = None
    #: The published form of each outcome's target, index-aligned with
    #: `outcomes`. Held here rather than recomputed so a target is reconstructed
    #: exactly once per register.
    published_targets: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.standing is not Standing.AGREES and not self.because.strip():
            raise ValueError(
                f"{self.analysis.id!r} agrees with no benchmark and gives no reason. "
                "The reason is the register's most useful output; it is not optional."
            )

    @property
    def agrees(self) -> bool:
        return self.standing is Standing.AGREES

    def count(self, outcome: Outcome) -> int:
        return sum(1 for item in self.outcomes if item.outcome is outcome)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "analysis": self.analysis.to_dict(),
            "standing": str(self.standing),
            "agrees_with_a_benchmark": self.agrees,
            "benchmarks": [
                _public_outcome(outcome, target)
                for outcome, target in zip(self.outcomes, self.published_targets, strict=True)
            ],
            "benchmark_count": len(self.outcomes),
            # Printed even when empty and even when the row agrees, the
            # rule `app.observe.report` states: a section that disappears when
            # it has nothing to say trains a reader not to look for it.
            "not_confirmed_because": self.because,
            "refused_targets": [scrub(reason) for reason in self.refusals],
        }
        if self.accuracy is not None:
            payload["accuracy"] = self.accuracy.to_dict()
        return payload


@dataclass(frozen=True, slots=True)
class Summary:
    """The counts, with the non-results kept apart from the results."""

    analyses_declared: int
    agreeing: int
    disputed: int
    unconfirmed: int
    benchmarks: int
    #: Ran, produced a number, nothing to compare it against. Never a pass.
    measured_only: int
    #: Ran; 7.2 would not permit the number out. Never a pass.
    unconverged: int
    blocked: int
    errored: int
    deviated: int
    refused_targets: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyses_declared": self.analyses_declared,
            "analyses_agreeing": self.agreeing,
            "analyses_disputed": self.disputed,
            "analyses_unconfirmed": self.unconfirmed,
            "benchmarks_reported": self.benchmarks,
            "not_agreement": {
                "measured_only": self.measured_only,
                "unconverged": self.unconverged,
                "blocked": self.blocked,
                "errored": self.errored,
                "deviated": self.deviated,
            },
            "refused_targets": self.refused_targets,
        }


@dataclass(frozen=True, slots=True)
class Register:
    """Which analyses agree with a published benchmark, against what, to what
    accuracy, and when."""

    generated_at: str
    rows: tuple[AnalysisRow, ...]
    summary: Summary
    #: Benchmarks whose `analysis` string matches no declared analysis. They are
    #: not silently filed under "other": an unrecognised group means either a
    #: typo in a benchmark or an analysis missing from `ANALYSES`, and both are
    #: register bugs that would otherwise hide a whole family of cases.
    uncatalogued: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """True only when every declared analysis agrees with a benchmark. False
        today, and expected to be false for a long time — `ok` and `complete` are
        separate questions, the split `app.design.missions` makes for the same
        reason."""
        return all(row.agrees for row in self.rows) and not self.uncatalogued

    @property
    def unconfirmed(self) -> tuple[AnalysisRow, ...]:
        return tuple(row for row in self.rows if not row.agrees)

    def headline(self) -> str:
        """The sentence a human reads, with the denominator in it.

        Written as one sentence containing both numbers because that is the
        thing a summary loses first: "3 analyses agree" is true of a product with
        three and of a product with thirty. The last clause is 20.3's, and it is
        in the headline rather than a note because the headline is what gets
        quoted.
        """
        total = len(self.rows)
        agreeing = sum(1 for row in self.rows if row.agrees)
        disputed = sum(1 for row in self.rows if row.standing is Standing.DISPUTED)
        sentence = (
            f"{agreeing} of {total} analyses agree with a published benchmark "
            f"solution; {total - agreeing} do not yet."
        )
        if disputed:
            sentence += f" {disputed} ran against a target and missed it."
        sentence += (
            " That is verification evidence; none of it is validation, because no "
            "result has been compared against measurements of a physical part."
        )
        return sentence

    def superseded_by(self, changes: Iterable[Any]) -> tuple[Any, ...]:
        """Accuracy-affecting changes dated after this register was generated.

        The register is a claim about a build. A solver swap or a mesh-default
        change moves results, so a register generated before one is describing
        a product that no longer exists — and saying nothing would leave a stale
        green page up. Takes the changes as an argument rather than importing
        `app.verify.changelog`, so the changelog can name analyses from here
        without the two modules importing each other.
        """
        return tuple(
            change
            for change in changes
            if getattr(change, "changes_results", False)
            and str(getattr(change, "date", "")) > self.generated_at[:10]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "headline": self.headline(),
            "complete": self.complete,
            "summary": self.summary.to_dict(),
            "analyses": [row.to_dict() for row in self.rows],
            # Duplicated deliberately. The point of 7.4 is that what does *not*
            # agree is as easy to find as what does, and "filter the analyses
            # array yourself" is not as easy to find.
            "unconfirmed": [
                {
                    "id": row.analysis.id,
                    "title": row.analysis.title,
                    "standing": str(row.standing),
                    "because": row.because,
                }
                for row in self.unconfirmed
            ],
            "uncatalogued_benchmark_groups": list(self.uncatalogued),
            "validation": standards.validation_block(),
            "notes": list(self.notes),
        }

    # -- construction ------------------------------------------------------

    @staticmethod
    def build(
        outcomes: Sequence[BenchmarkOutcome] = (),
        *,
        analyses: Sequence[Analysis] = ANALYSES,
        generated_at: str | None = None,
        notes: Sequence[str] = (),
    ) -> Register:
        """Roll a set of benchmark outcomes up against the declared analyses.

        Takes outcomes rather than a suite: see the module docstring on why the
        published register must not run anything.
        """
        by_analysis: dict[str, list[BenchmarkOutcome]] = {a.id: [] for a in analyses}
        uncatalogued: list[str] = []
        for outcome in outcomes:
            bucket = by_analysis.get(outcome.analysis)
            if bucket is None:
                if outcome.analysis not in uncatalogued:
                    uncatalogued.append(outcome.analysis)
                continue
            bucket.append(outcome)

        rows = tuple(_row(analysis, tuple(by_analysis[analysis.id])) for analysis in analyses)
        return Register(
            generated_at=generated_at or datetime.now(UTC).isoformat(),
            rows=rows,
            summary=_summarise(rows),
            uncatalogued=tuple(sorted(uncatalogued)),
            notes=tuple(notes),
        )


def _row(analysis: Analysis, outcomes: tuple[BenchmarkOutcome, ...]) -> AnalysisRow:
    published: list[dict[str, Any]] = []
    refusals: list[str] = []
    refused_ids: set[str] = set()
    for outcome in outcomes:
        target, refusal = _republish(outcome.target)
        published.append(target)
        if refusal is not None:
            refused_ids.add(outcome.benchmark_id)
            refusals.append(f"{outcome.benchmark_id}: {refusal}")

    # A case whose target could not be republished cannot agree with anything,
    # whatever verdict was recorded against it: the verdict was computed from a
    # target the register has just refused to print.
    passes = tuple(o for o in outcomes if o.passed and o.benchmark_id not in refused_ids)
    trouble = tuple(
        o
        for o in outcomes
        if o.outcome in (Outcome.DEVIATED, Outcome.ERRORED) or o.benchmark_id in refused_ids
    )

    if not outcomes:
        return AnalysisRow(
            analysis=analysis,
            standing=Standing.UNCONFIRMED,
            because=NO_BENCHMARK,
            published_targets=(),
        )

    if trouble:
        deviated = sum(1 for o in trouble if o.outcome is Outcome.DEVIATED)
        errored = sum(1 for o in trouble if o.outcome is Outcome.ERRORED)
        parts = []
        if deviated:
            parts.append(f"{deviated} case(s) landed outside the accepted band")
        if errored:
            parts.append(f"{errored} case(s) raised")
        if refusals:
            parts.append(f"{len(refusals)} target(s) were not fit to publish")
        return AnalysisRow(
            analysis=analysis,
            standing=Standing.DISPUTED,
            because="; ".join(parts) + ".",
            outcomes=outcomes,
            refusals=tuple(refusals),
            published_targets=tuple(published),
        )

    if not passes:
        counts = {
            "measured with no target to compare against": sum(
                1 for o in outcomes if o.outcome is Outcome.MEASURED
            ),
            "unconverged, so no number may be stated": sum(
                1 for o in outcomes if o.outcome is Outcome.UNCONVERGED
            ),
            "blocked on a missing capability": sum(
                1 for o in outcomes if o.outcome is Outcome.BLOCKED
            ),
        }
        detail = ", ".join(f"{count} {what}" for what, count in counts.items() if count)
        return AnalysisRow(
            analysis=analysis,
            standing=Standing.UNCONFIRMED,
            because=(
                f"{len(outcomes)} benchmark(s) exist and none agreed: {detail}. "
                "None of these is a pass."
            ),
            outcomes=outcomes,
            refusals=tuple(refusals),
            published_targets=tuple(published),
        )

    return AnalysisRow(
        analysis=analysis,
        standing=Standing.AGREES,
        because="",
        outcomes=outcomes,
        refusals=(),
        accuracy=_accuracy(passes),
        published_targets=tuple(published),
    )


def _accuracy(passes: tuple[BenchmarkOutcome, ...]) -> Accuracy | None:
    deviations = [abs(o.relative_deviation) for o in passes if o.relative_deviation is not None]
    bands = [o.target.tolerance for o in passes if o.target.tolerance is not None]
    if not deviations or not bands:
        return None
    sources = sorted({o.target.source for o in passes if o.target.source})
    return Accuracy(
        worst_relative_deviation=max(deviations),
        widest_band=max(bands),
        cases=len(passes),
        sources=tuple(sources),
    )


def _summarise(rows: tuple[AnalysisRow, ...]) -> Summary:
    def total(outcome: Outcome) -> int:
        return sum(row.count(outcome) for row in rows)

    return Summary(
        analyses_declared=len(rows),
        agreeing=sum(1 for row in rows if row.standing is Standing.AGREES),
        disputed=sum(1 for row in rows if row.standing is Standing.DISPUTED),
        unconfirmed=sum(1 for row in rows if row.standing is Standing.UNCONFIRMED),
        benchmarks=sum(len(row.outcomes) for row in rows),
        measured_only=total(Outcome.MEASURED),
        unconverged=total(Outcome.UNCONVERGED),
        blocked=total(Outcome.BLOCKED),
        errored=total(Outcome.ERRORED),
        deviated=total(Outcome.DEVIATED),
        refused_targets=sum(len(row.refusals) for row in rows),
    )


# --------------------------------------------------------------------------
# The published register
# --------------------------------------------------------------------------

#: The cases the published register reports on: **the blocked half of 7.1's
#: NAFEMS catalogue**, and nothing else.
#:
#: The filter is what makes this safe rather than a rule somebody must remember.
#: `assert_publishable` refuses a runnable case, and this module runs that
#: assertion at import — so selecting the catalogue wholesale would turn the day
#: somebody makes LE10 runnable into the day the application stops booting.
#: Filtering on `runnable` cannot fail that way, and it is the same statement:
#: a benchmark that executes reaches this register from a recorded run, never
#: from a request.
#:
#: Publishing the blocked ones is the point rather than a consolation. Each
#: carries the number we must eventually produce and the named reason we cannot
#: yet, which is exactly what 7.4 asks a register to say about what does *not*
#: agree.
PUBLISHED_SUITE: Final = Suite(
    name="kryova-published-verification",
    benchmarks=tuple(b for b in NAFEMS_SUITE.benchmarks if not b.runnable),
)

#: Notes printed with the register. Not decoration: without the first line a
#: reader sees rows of closed-form checks and benchmark agreements and reads
#: them as validation — the conflation ASME VVUQ 1 separates
#: (`app.verify.standards.DEFINITIONS`). **The first note said exactly that
#: until 2026-09-14**, telling readers validation "is answered only by a
#: published benchmark with a published result"; see the module docstring.
PUBLISHED_NOTES: Final[tuple[str, ...]] = (
    "Verification and validation are different questions, and everything on this "
    "page answers the first. In ASME's terms, verification asks whether the "
    "computational model fits its mathematical description: agreeing with a "
    "closed-form identity or with a published NAFEMS reference solution is code "
    "verification, and a mesh convergence study is solution verification. "
    "Validation asks whether the model represents the real world, and is answered "
    "only by comparing against measurements of a physical part. Kryova has none, "
    "so nothing here is validated. The definitions, quoted with their sources, are "
    "in this payload's validation block.",
    "This page never runs a benchmark. A benchmark case is several solves and "
    "this route is unauthenticated, so an agreeing row is a *recording* — the "
    "outcome of a run made elsewhere, carried in with a fingerprint of every "
    "source file that decides an answer. If that fingerprint stops matching the "
    "code, the recording is discarded and the page reverts to publishing only "
    "the blocked half, with the reason, rather than a result nobody re-checked. "
    "A blocked case still shows the published target it must eventually "
    "reproduce and the named reason it cannot be run yet.",
    "An analysis appears here whether or not anybody has benchmarked it. The list "
    "is declared in app/verify/register.py and is not assembled from whatever "
    "happened to run.",
)


def assert_publishable(suite: Suite) -> None:
    """Refuse a suite the published register would have to execute.

    The published register is served from an unauthenticated route. A runnable
    benchmark is a mesh convergence study — several solves — so a suite with one
    in it turns that route into a way to spend the server's CPU by asking
    politely. When the catalogue lands, its outcomes come from a recorded CI run
    and go into `Register.build`.
    """
    runnable = sorted(b.id for b in suite.benchmarks if b.runnable)
    if runnable:
        raise ValueError(
            f"Suite {suite.name!r} contains runnable benchmarks ({', '.join(runnable)}) "
            "and is used to build the register served on a public route. Do not run "
            "them there: record the outcomes in CI and pass them to Register.build."
        )


assert_publishable(PUBLISHED_SUITE)


def _blocked_outcomes() -> tuple[BenchmarkOutcome, ...]:
    """The cases this product cannot run, as outcomes. Costs nothing to state."""
    return tuple(
        BenchmarkOutcome(
            benchmark_id=benchmark.id,
            title=benchmark.title,
            analysis=benchmark.analysis,
            outcome=Outcome.BLOCKED,
            target=benchmark.target,
            detail=benchmark.blocked_reason,
        )
        for benchmark in PUBLISHED_SUITE.benchmarks
    )


def published_register(*, generated_at: str | None = None) -> Register:
    """The register as the trust page publishes it.

    Cheap by construction — `assert_publishable` guarantees there is nothing to
    run here, and the results of the cases that *can* run are read from a
    recorded artefact rather than produced on the request.

    **A recorded run that no longer describes this code publishes nothing**, and
    says so. `recorded.load` compares a fingerprint of the solvers, the mesher
    and the verification modules against the working tree, so the register falls
    back to the blocked-only view the moment a solver changes — which is the
    honest state until somebody records the run again. Reading it per request
    rather than at import is deliberate: a register cached at startup would keep
    publishing a result the file on disk had already retired.
    """
    outcomes, discarded = recorded.load()
    notes = PUBLISHED_NOTES
    if discarded:
        outcomes = _blocked_outcomes()
        notes = (*PUBLISHED_NOTES, discarded)

    return Register.build(outcomes, generated_at=generated_at, notes=notes)


__all__ = [
    "ANALYSES",
    "BY_ID",
    "NO_BENCHMARK",
    "PUBLISHED_NOTES",
    "PUBLISHED_SUITE",
    "WITHHELD",
    "Accuracy",
    "Analysis",
    "AnalysisRow",
    "Register",
    "Standing",
    "Summary",
    "assert_publishable",
    "published_register",
    "scrub",
]
