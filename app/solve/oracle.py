"""Two solvers, one case — master plan 6.5.

Decision 2 says physics is federated and never re-implemented, and Phase 6 hands
the structural answer to CalculiX. That leaves the hand-written solver in
`linear_static.py` with a second job, and it is the more valuable one: it is the
**oracle**. Both solvers are given the same mesh, the same load case and the same
material, and a disagreement between them is a bug in the integration rather than
a matter of opinion — a deck that numbered nodes from zero, a results file read
in the wrong component order, a load applied to the wrong face.

That argument only holds if both were asked the same question, which is why
`deck.py` writes out `assemble_loads`' own force vector rather than re-deriving
one. Derive the loads twice and a disagreement stops localising: it could be
either end, and the oracle has told you nothing.

**Which quantities must agree, and which are allowed not to.** This is the whole
design of the comparison, and getting it wrong in either direction makes the
oracle worthless — too strict and it cries wolf at a stress concentration, too
loose and it passes a deck with a real fault in it.

* **Displacement must agree tightly.** Both solvers report it at nodes, from the
  same degrees of freedom, with no post-processing between the solve and the
  number. It is the quantity a wrong load or a wrong restraint moves first, and
  there is no legitimate reason for the two to differ by more than the linear
  solve's own conditioning.
* **Mass and volume must agree exactly.** Neither solver computes them; both read
  them off the same mesh. A difference here is not a physics disagreement at all,
  it is a sign the two runs were not given the same mesh — which invalidates
  everything else in the comparison, so it is checked first.
* **Peak stress is allowed to differ, and the direction is predictable.**
  CalculiX extrapolates stress to the nodes and averages it across every element
  meeting there; `linear_static` reports the element's own constant value. At a
  concentration the smoothed peak is *lower*, and by how much depends on the mesh
  rather than on either solver being wrong. So peak stress is reported as a
  measured difference with its sign, and only a difference in a **uniform** field
  is treated as a fault — there, smoothing is the identity and the two must
  agree.

**An oracle that could not run is UNMEASURED, never a pass.** `compare` returns
an `Agreement` with `ran=False` and the reason when there is no CalculiX binary;
it does not raise, and it does not quietly report agreement between one solver
and itself. This is `app/design/assertions.py`'s rule — a suite that skips what it
could not read reports green on a part nobody checked — applied to the solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.mesh.types import TetMesh
from app.solve.base import SolveOutput, Solver
from app.solve.types import LoadCase, SolverError

#: Relative tolerance on peak displacement. Loose enough for two different
#: direct solvers on the same matrix — SuperLU here, SPOOLES or PARDISO there,
#: with different pivot orders and therefore different rounding — and far tighter
#: than any real fault. A node numbered wrong moves a displacement by orders of
#: magnitude, not by parts in a thousand.
DISPLACEMENT_TOLERANCE = 1e-3

#: Relative tolerance on peak von Mises **in a uniform field only**. Held to the
#: same order as displacement because in a uniform field nodal smoothing is the
#: identity: every node carries the same tensor, so averaging changes nothing and
#: there is no legitimate source of difference left.
UNIFORM_STRESS_TOLERANCE = 1e-3

#: How uniform a field has to be before the stress check is applied at all,
#: measured as (peak - median) / peak. A bar in tension sits near zero; anything
#: with a hole or a fillet in it is far above, and its stress difference is
#: reported rather than judged.
UNIFORM_FIELD_SPREAD = 0.02


@dataclass
class Difference:
    """One quantity as both solvers reported it."""

    name: str
    reference: float
    candidate: float
    #: Signed and relative to the reference. Positive means the candidate read
    #: high. Signed rather than absolute because the *direction* is the finding
    #: for stress: smoothing reads low, and a candidate reading high at a
    #: concentration is a different and more alarming result.
    relative: float
    tolerance: float | None
    #: None when this quantity is reported rather than judged — see the module
    #: docstring on peak stress in a non-uniform field.
    agrees: bool | None

    def __str__(self) -> str:
        verdict = {True: "agrees", False: "DIFFERS", None: "reported"}[self.agrees]
        return (
            f"{self.name}: {self.reference:.6g} vs {self.candidate:.6g} "
            f"({self.relative:+.2%}, {verdict})"
        )


@dataclass
class Agreement:
    """What comparing two solvers on one case found.

    `ran=False` is the unmeasured state and is never a pass: `agrees` is False
    then too, so a caller that checks only the verdict cannot mistake "could not
    run" for "the two agree".
    """

    reference_name: str
    candidate_name: str
    ran: bool
    differences: list[Difference] = field(default_factory=list)
    #: Why it could not run, when it could not. Empty otherwise.
    reason: str = ""
    #: Whether the field was uniform enough for the stress check to mean
    #: anything. Recorded rather than inferred, so a reader can tell a stress
    #: comparison that was skipped from one that passed.
    uniform_field: bool = False

    @property
    def agrees(self) -> bool:
        """True only when the comparison ran and every judged quantity agreed."""
        return self.ran and all(
            difference.agrees is not False for difference in self.differences
        )

    def report(self) -> str:
        """The comparison as a human reads it, verdict first."""
        if not self.ran:
            return (
                f"{self.reference_name} vs {self.candidate_name}: UNMEASURED — "
                f"{self.reason}"
            )
        head = "agree" if self.agrees else "DISAGREE"
        lines = [f"{self.reference_name} vs {self.candidate_name}: {head}"]
        lines += [f"  {difference}" for difference in self.differences]
        if not self.uniform_field:
            lines.append(
                "  (peak stress is reported, not judged: the field is not uniform, "
                "so nodal smoothing legitimately reads below the element peak)"
            )
        return "\n".join(lines)


def field_is_uniform(von_mises: np.ndarray) -> bool:
    """Whether a stress field is flat enough for smoothing to be the identity.

    Median rather than mean: a handful of hot elements at a load introduction
    would drag a mean towards the peak and declare a notched part uniform, which
    is the one case this must not do.
    """
    peak = float(np.max(von_mises))
    if peak <= 0.0:
        return True
    median = float(np.median(von_mises))
    return (peak - median) / peak <= UNIFORM_FIELD_SPREAD


def compare(
    mesh: TetMesh,
    case: LoadCase,
    reference: Solver,
    candidate: Solver,
) -> Agreement:
    """Run both solvers on one case and say whether they agree.

    **Any failure of either solver makes the comparison UNMEASURED, and the
    reason is carried verbatim.** That includes a solver that ran and refused
    the model, which might look like it deserves to propagate — it does not,
    because what this function answers is "do these two agree", and if one of
    them declined to answer then they have not been compared. The distinction
    between "no binary" and "your load case is under-constrained" is not lost:
    it is the whole of `reason`, in the refusing solver's own words.
    """
    try:
        reference_output = reference.solve(mesh, case)
    except SolverError as failed:
        return _unmeasured(reference, candidate, f"{reference.name} could not run: {failed}")

    try:
        candidate_output = candidate.solve(mesh, case)
    except SolverError as failed:
        return _unmeasured(reference, candidate, f"{candidate.name} could not run: {failed}")

    return _compare_outputs(reference, candidate, reference_output, candidate_output)


def _unmeasured(reference: Solver, candidate: Solver, reason: str) -> Agreement:
    return Agreement(
        reference_name=reference.name,
        candidate_name=candidate.name,
        ran=False,
        reason=reason,
    )


def _compare_outputs(
    reference: Solver,
    candidate: Solver,
    reference_output: SolveOutput,
    candidate_output: SolveOutput,
) -> Agreement:
    left = reference_output.result
    right = candidate_output.result
    uniform = field_is_uniform(reference_output.von_mises) and field_is_uniform(
        candidate_output.von_mises
    )

    differences = [
        # Volume first: it is read off the mesh by both, so a difference here
        # means the two were not given the same mesh, and nothing below it means
        # anything. Zero tolerance for the same reason.
        _difference("volume_mm3", left.volume_mm3, right.volume_mm3, 0.0),
        _difference(
            "max_displacement_mm",
            left.max_displacement_mm,
            right.max_displacement_mm,
            DISPLACEMENT_TOLERANCE,
        ),
        _difference(
            "max_von_mises_mpa",
            left.max_von_mises_mpa,
            right.max_von_mises_mpa,
            UNIFORM_STRESS_TOLERANCE if uniform else None,
        ),
    ]

    return Agreement(
        reference_name=reference.name,
        candidate_name=candidate.name,
        ran=True,
        differences=differences,
        uniform_field=uniform,
    )


def _difference(
    name: str, reference: float, candidate: float, tolerance: float | None
) -> Difference:
    """One quantity, relative to the reference.

    A reference of exactly zero is handled rather than divided by: the relative
    difference is then zero if the candidate is zero too and infinite otherwise,
    which is the correct reading — going from nothing to something is not a small
    relative change however small the absolute number.
    """
    if reference == 0.0:
        relative = 0.0 if candidate == 0.0 else float("inf")
    else:
        relative = (candidate - reference) / abs(reference)

    agrees: bool | None
    if tolerance is None:
        agrees = None
    else:
        agrees = abs(relative) <= tolerance

    return Difference(
        name=name,
        reference=reference,
        candidate=candidate,
        relative=relative,
        tolerance=tolerance,
        agrees=agrees,
    )


__all__ = [
    "DISPLACEMENT_TOLERANCE",
    "UNIFORM_FIELD_SPREAD",
    "UNIFORM_STRESS_TOLERANCE",
    "Agreement",
    "Difference",
    "compare",
    "field_is_uniform",
]
