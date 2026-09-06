"""Changes that move a number — P10.3's third page.

An ordinary changelog is a list of features. This one answers a different
question, and only that question: **did anything change that makes a result I
already have wrong, or differently right?** A solver swap, a mesh default, a
stress-recovery fix, an element formulation, a units decision — each of those
silently restates every number produced before it, and a user with a report in a
drawer has no way to know unless somebody tells them loudly.

So an entry is not free text. It is:

* the **date and commit**, so it can be looked up;
* the **analyses affected**, by id from `app.verify.register.ANALYSES` — checked
  at import, so renaming an analysis and forgetting this file is an ImportError
  rather than a page that quietly stops matching the register;
* an **`Effect`**, which is the loud part: results move, results that used to be
  returned are now refused, or the number is unchanged and only the evidence
  around it changed;
* **`what_to_do`**, required. "We changed the solver" with no next step is a
  notification; "re-run anything you signed before this date" is a changelog.

`Effect.RESULTS_REFUSED` deserves its own value rather than being folded into
"results change". A number that used to come back and now does not is a change a
user experiences as a regression, and burying it under "results may differ"
guarantees a support conversation that starts from the wrong place.

**What this file is not**: a release history, and not a complete one. It records
the accuracy-affecting changes that are *known* and traceable to a commit in
this repository; it starts where the practice started, and it says so in
`RECORD_BEGINS`. A changelog that implied completeness back to the first commit
would be making exactly the sort of unearned claim this package exists to stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.verify.register import BY_ID as _ANALYSIS_IDS

_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Effect(StrEnum):
    """What the change did to a number a user already has."""

    #: The same input now produces a different number.
    RESULTS_CHANGE = "results-change"
    #: A number that used to be returned is now refused, because it should
    #: never have been stated.
    RESULTS_REFUSED = "results-refused"
    #: The numbers are unchanged; what changed is the evidence carried with
    #: them, or the ability to reproduce them.
    EVIDENCE_CHANGE = "evidence-change"


@dataclass(frozen=True, slots=True)
class AccuracyChange:
    """One change that a user holding an old result needs to know about."""

    date: str
    summary: str
    effect: Effect
    #: Analysis ids from `app.verify.register.ANALYSES`. Empty means "all of
    #: them", which must be said explicitly via `affects_everything`.
    analyses: tuple[str, ...]
    what_changed: str
    what_to_do: str
    #: Short git sha, where the change is one commit. Blank where it is not, in
    #: which case `evidence` carries the test or document that records it.
    commit: str = ""
    evidence: tuple[str, ...] = ()
    affects_everything: bool = False

    def __post_init__(self) -> None:
        if not _DATE_RE.match(self.date):
            raise ValueError(f"{self.date!r} is not an ISO date; the page sorts on it.")
        if not self.what_to_do.strip():
            raise ValueError(
                f"The {self.date} entry says what changed and not what to do about it. "
                "A changelog entry with no action is a notification, and this page "
                "exists precisely so an accuracy change is not merely announced."
            )
        if not self.analyses and not self.affects_everything:
            raise ValueError(
                f"The {self.date} entry names no analysis. Say which, or set "
                "affects_everything — an entry that quietly applies to nothing is "
                "worse than absent, because it makes the page look longer."
            )
        unknown = sorted(set(self.analyses) - set(_ANALYSIS_IDS))
        if unknown:
            raise ValueError(
                f"The {self.date} entry names analyses that are not in the register: "
                f"{', '.join(unknown)}. Ids come from app.verify.register.ANALYSES, so "
                "the changelog and the register cannot drift apart."
            )
        if not self.commit.strip() and not self.evidence:
            raise ValueError(
                f"The {self.date} entry is neither a commit nor evidenced. An accuracy "
                "claim nobody can look up is not a record."
            )

    @property
    def changes_results(self) -> bool:
        """True when a stored number may no longer be the answer.

        `RESULTS_REFUSED` counts: a result that would now be refused is one that
        should not be relied on either.
        """
        return self.effect in (Effect.RESULTS_CHANGE, Effect.RESULTS_REFUSED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "summary": self.summary,
            "effect": str(self.effect),
            "changes_results": self.changes_results,
            "analyses": ["all"] if self.affects_everything else list(self.analyses),
            "what_changed": self.what_changed,
            "what_to_do": self.what_to_do,
            "commit": self.commit,
            "evidence": list(self.evidence),
        }


#: The date this record starts. Anything before it is not covered, and the page
#: says so rather than letting an empty tail read as "nothing changed".
RECORD_BEGINS: Final = "2026-08-29"


CHANGES: Final[tuple[AccuracyChange, ...]] = (
    AccuracyChange(
        date="2026-09-06",
        commit="ecb0eb7",
        summary="CalculiX became a selectable solver alongside the in-house one.",
        effect=Effect.RESULTS_CHANGE,
        analyses=("linear-static", "modal", "buckling", "thermal-stress"),
        what_changed=(
            "SOLVER_BACKEND=calculix routes a solve to CalculiX across a subprocess "
            "boundary instead of the in-house assembly and SuperLU factorisation. Two "
            "solvers do not produce identical numbers: element formulation, "
            "integration and the linear algebra all differ."
        ),
        what_to_do=(
            "Check which solver produced a result before comparing two of them. The "
            "provenance record on every result carries the solver name, its version "
            "where it declares one, and a digest of the solver source."
        ),
        evidence=("app/solve/registry.py", "app/verify/provenance.py"),
    ),
    AccuracyChange(
        date="2026-09-06",
        commit="f90f4be",
        summary="A model with no restraints is refused rather than solved.",
        effect=Effect.RESULTS_REFUSED,
        analyses=("linear-static",),
        what_changed=(
            "An under-constrained model was returning a finite, meaningless "
            "displacement field — 5.4e+11 mm in the case that found it — because a "
            "sparse direct solve returns a vector for a singular system rather than "
            "failing. The equilibrium residual now refuses it."
        ),
        what_to_do=(
            "Any displacement or stress from before this date that came back "
            "implausibly large was not a units problem. Re-run it; if it is now "
            "refused, the fixtures did not remove all six rigid-body motions."
        ),
        evidence=("app/solve/linear_static.py",),
    ),
    AccuracyChange(
        date="2026-09-03",
        commit="df7c24f",
        summary=(
            "Thermal strain is subtracted during stress recovery, and the modal mass "
            "matrix is integrated analytically."
        ),
        effect=Effect.RESULTS_CHANGE,
        analyses=("thermal-stress", "modal"),
        what_changed=(
            "Adding thermal strain as a load without subtracting it again during "
            "recovery reports the stress of a freely expanding part — wrong sign and "
            "wrong magnitude. Separately, the modal mass matrix is integrated in "
            "barycentric coordinates rather than reusing the stiffness assembly's "
            "four-point Gauss rule, which is exact only to degree 2 while tet10's "
            "N^T N is quartic: reusing it was wrong by a few percent, which is the "
            "worst size of error to have."
        ),
        what_to_do=(
            "Re-run any thermal-stress or modal result from before this date. A "
            "frequency that was a few percent out is not visibly wrong."
        ),
        evidence=(
            "tests/test_thermal.py::TestThermalStrain",
            "tests/test_modal.py",
        ),
    ),
    AccuracyChange(
        date="2026-09-02",
        summary="Quadratic tetrahedra (tet10) work; they were previously dead code.",
        effect=Effect.RESULTS_CHANGE,
        analyses=("linear-static", "modal", "buckling", "thermal-stress"),
        what_changed=(
            "Stiffness assembly now dispatches on whether the mesh carries midside "
            "nodes, integrating tet10 at four Gauss points with a matching branch in "
            "stress recovery. Before this, a quadratic mesh did not do what its "
            "docstring said it did."
        ),
        what_to_do=(
            "A result computed on a mesh with midside nodes before this date should "
            "be re-run. Quadratic elements now beat linear at equal element count "
            "against the closed-form cantilever, which is the check that pins it."
        ),
        evidence=("tests/test_solver.py::TestQuadraticElements",),
    ),
    AccuracyChange(
        date="2026-08-29",
        commit="0b3ae1a",
        summary="A diverging conjugate-gradient solve no longer returns its last iterate.",
        effect=Effect.RESULTS_REFUSED,
        analyses=("linear-static",),
        what_changed=(
            "The iterative path could return a vector from a solve that had not "
            "converged, which is a number with no relationship to the model."
        ),
        what_to_do=(
            "Re-run any static result from before this date on a large model. There "
            "is no way to tell a diverged iterate from a converged one by looking at "
            "it."
        ),
        evidence=("app/solve/linear_static.py",),
    ),
)

if [c.date for c in CHANGES] != sorted((c.date for c in CHANGES), reverse=True):
    raise ValueError(  # pragma: no cover - ordering is fixed in the literal above
        "CHANGES must be newest first; the page does not sort at render time so that "
        "the source reads the way the page does."
    )


def since(date: str) -> tuple[AccuracyChange, ...]:
    """Every entry strictly after `date` (ISO, `YYYY-MM-DD`)."""
    return tuple(change for change in CHANGES if change.date > date)


def affecting(analysis_id: str) -> tuple[AccuracyChange, ...]:
    """Every entry that moves numbers for one analysis."""
    return tuple(
        change
        for change in CHANGES
        if change.affects_everything or analysis_id in change.analyses
    )


def to_dict() -> dict[str, Any]:
    """The page, as data."""
    return {
        "record_begins": RECORD_BEGINS,
        "note": (
            "Only changes that affect a number are listed here. This is not a release "
            "history, and it does not reach back before "
            f"{RECORD_BEGINS} — earlier changes are not covered rather than absent."
        ),
        "summary": {
            "entries": len(CHANGES),
            "results_change": sum(1 for c in CHANGES if c.effect is Effect.RESULTS_CHANGE),
            "results_refused": sum(1 for c in CHANGES if c.effect is Effect.RESULTS_REFUSED),
            "evidence_change": sum(1 for c in CHANGES if c.effect is Effect.EVIDENCE_CHANGE),
        },
        "changes": [change.to_dict() for change in CHANGES],
    }


__all__ = [
    "CHANGES",
    "RECORD_BEGINS",
    "AccuracyChange",
    "Effect",
    "affecting",
    "since",
    "to_dict",
]
