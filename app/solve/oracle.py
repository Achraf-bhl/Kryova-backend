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
from app.solve.base import ConductionSolver, SolveOutput, Solver
from app.solve.conduction import ThermalCase
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

#: Relative tolerance on every temperature row of a conduction comparison,
#: measured against the reference field's **span** rather than against a kelvin
#: value — see `_difference`. Held at the same order as displacement for the
#: same reason: two direct solvers on one symmetric positive-definite system
#: differ by their pivot orders and nothing else, and a wrong boundary face
#: moves a temperature by whole kelvin rather than by parts in a thousand.
TEMPERATURE_TOLERANCE = 1e-3

#: Relative tolerance on the heat crossing the fixed-temperature regions. Looser
#: than the field by an order of magnitude on purpose: it is a *sum of
#: reactions*, so it accumulates the round-off of every held node, and the two
#: solvers reach it by genuinely different routes — ours from `K T - f` over the
#: assembled system, CalculiX's from the `RFL` block its own solver wrote.
HEAT_TOLERANCE = 1e-2

#: Below this the boundary heat is reported rather than judged, in watts. A
#: model with two held regions at different temperatures supplies exactly as
#: much as it removes, so the true value is zero and a relative tolerance
#: against it is not a test. The floor is one milliwatt: small enough that any
#: model with a real heat path through a held region is judged, large enough
#: that the residue of a balanced one is not.
HEAT_FLOOR_W = 1e-3


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
    #: What `relative` was divided by, when that is not the reference's own
    #: magnitude. Recorded rather than implied: a reader of a +0.02% row has to
    #: be able to tell 0.02% of 400 K from 0.02% of the 100 K the problem
    #: actually spans, and those differ by a factor of four.
    scale: float | None = None

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
    name: str,
    reference: float,
    candidate: float,
    tolerance: float | None,
    scale: float | None = None,
) -> Difference:
    """One quantity, relative to the reference — or to `scale` where given.

    A reference of exactly zero is handled rather than divided by: the relative
    difference is then zero if the candidate is zero too and infinite otherwise,
    which is the correct reading — going from nothing to something is not a small
    relative change however small the absolute number.

    **`scale` exists because a quantity's own magnitude is not always the right
    denominator.** A displacement of 0.012 mm and a stress of 25 MPa are both
    measured from a physical zero, so a part-per-thousand of the value means
    something. An absolute temperature is not: 400.4 K against 400 K is a
    thousandth of the *kelvin scale* and a whole four hundredth of a problem
    that spans 100 K, and judging it against 400 would pass a solver that got
    the temperature rise wrong by 0.4%. So a conduction comparison passes the
    field's own span, and the row records what it divided by.
    """
    denominator = abs(scale) if scale is not None else abs(reference)
    if denominator == 0.0:
        relative = 0.0 if candidate == reference else float("inf")
    else:
        relative = (candidate - reference) / denominator

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
        scale=scale,
    )


def compare_conduction(
    mesh: TetMesh,
    case: "ThermalCase",
    reference: "ConductionSolver",
    candidate: "ConductionSolver",
) -> Agreement:
    """Run both conduction solvers on one case and say whether they agree.

    The same contract as `compare`: any failure of either solver makes the
    comparison UNMEASURED with the refusing solver's own words, because "do
    these two agree" has no answer when one of them declined to answer.

    **What is judged, and against what scale.** Every temperature row is divided
    by the *reference field's span* rather than by its own magnitude, for the
    reason `_difference` gives — a kelvin temperature is measured from a zero
    the problem did not choose, so a relative error against 400 K flatters a
    solver that got a 100 K rise wrong.

    * **The node count must match exactly.** Both read it off the same mesh, so
      a difference means the two were not given the same model and nothing below
      it means anything. This is `compare`'s volume row in its thermal form.
    * **The peak nodal difference is the finding.** A comparison of minima and
      maxima alone passes two fields that are wrong in opposite places by the
      same amount, which is exactly what a mis-mapped boundary face produces.
      Its reference is 0.0 — the whole claim is that the two fields coincide —
      and dividing by the reference there would be meaningless, which is the
      other half of what `scale` is for.
    * **The boundary heat is judged only when there is enough of it to judge.**
      `fixed_temperature_heat_w` is legitimately zero on a model with two held
      regions at different temperatures (what one supplies the other removes),
      and a relative tolerance against zero is not a test. Below
      `HEAT_FLOOR_W` it is reported rather than judged, and the report says so.

    **Heat flux is not compared at all**, and that is deliberate:
    `CalculiXConductionSolver` derives its flux by differentiating CalculiX's
    temperature field with *this repository's* gradient operator, so comparing
    it would be comparing one operator with itself and would read as
    corroboration.
    """
    try:
        reference_field = reference.solve(mesh, case)
    except SolverError as failed:
        return _unmeasured_named(
            reference.name, candidate.name, f"{reference.name} could not run: {failed}"
        )

    try:
        candidate_field = candidate.solve(mesh, case)
    except SolverError as failed:
        return _unmeasured_named(
            reference.name, candidate.name, f"{candidate.name} could not run: {failed}"
        )

    left = reference_field.result
    right = candidate_field.result
    span = left.max_temperature_k - left.min_temperature_k
    # A uniform field is a legitimate answer — a part entirely at ambient — and
    # has no span to divide by. Its own level is then the only scale available,
    # and it is a fair one because there is no rise to be wrong about.
    scale = span if span > 0.0 else abs(left.max_temperature_k) or 1.0

    peak = 0.0
    if left.node_count == right.node_count:
        peak = float(
            np.max(np.abs(candidate_field.temperatures_k - reference_field.temperatures_k))
        )

    heat = left.fixed_temperature_heat_w
    heat_tolerance = (
        HEAT_TOLERANCE if np.isfinite(heat) and abs(heat) >= HEAT_FLOOR_W else None
    )

    differences = [
        _difference("node_count", float(left.node_count), float(right.node_count), 0.0),
        _difference(
            "min_temperature_k",
            left.min_temperature_k,
            right.min_temperature_k,
            TEMPERATURE_TOLERANCE,
            scale=scale,
        ),
        _difference(
            "max_temperature_k",
            left.max_temperature_k,
            right.max_temperature_k,
            TEMPERATURE_TOLERANCE,
            scale=scale,
        ),
        _difference(
            "peak_nodal_difference_k", 0.0, peak, TEMPERATURE_TOLERANCE, scale=scale
        ),
        _difference(
            "fixed_temperature_heat_w",
            heat,
            right.fixed_temperature_heat_w,
            heat_tolerance,
        ),
    ]

    return Agreement(
        reference_name=reference.name,
        candidate_name=candidate.name,
        ran=True,
        differences=differences,
        # A temperature field has no stress in it to smooth, so the caveat
        # `report` prints for a non-uniform field does not apply and would read
        # as a hedge about something this comparison did not do.
        uniform_field=True,
    )


def _unmeasured_named(reference: str, candidate: str, reason: str) -> Agreement:
    return Agreement(
        reference_name=reference, candidate_name=candidate, ran=False, reason=reason
    )


__all__ = [
    "DISPLACEMENT_TOLERANCE",
    "HEAT_FLOOR_W",
    "HEAT_TOLERANCE",
    "TEMPERATURE_TOLERANCE",
    "UNIFORM_FIELD_SPREAD",
    "UNIFORM_STRESS_TOLERANCE",
    "Agreement",
    "Difference",
    "compare",
    "compare_conduction",
    "field_is_uniform",
]
