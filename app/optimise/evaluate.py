"""One evaluation of a design, and the complete history of them.

**Every evaluation is recorded, including the ones that did not build.** That is
the requirement this module exists to meet and it is not bookkeeping: a part
that will not build at some parameter value is the most ordinary thing in the
world — a fillet radius the geometry cannot carry, a pocket that breaks through,
a wall driven negative — and an optimiser that dropped those points would report
a search of a design space it never actually visited. The reason it *can* be
recorded rather than crashed on is that `catia_set_parameter` already refuses
cleanly and leaves the part exactly as it was
(`app.kernel.occt.operations.parameters`): a failed evaluation costs a refusal
and nothing else.

Three rules, each of which is Decision 3 applied to a loop:

* **A failed build is a constraint violation, never an exception and never a
  gap in the record.** It comes back as an `Evaluation` with `built=False`, the
  kernel's own message in `reason`, and no objective value.
* **The number the driver sees is not the number that is recorded.** A driver
  needs a finite float at every point or SLSQP walks off; a *record* must not
  claim a mass was measured on a part that was never built. So the penalty value
  lives in `driver_objective()`, computed on demand, and `Evaluation.objective`
  is `None` when nothing was measured. Nothing that reads the history can mistake
  a penalty for a measurement.
* **An unmeasured constraint makes the point infeasible.** `assertions.py`
  already refuses to call an unmeasured claim a pass; this refuses to call a
  point with one a feasible design. A run that "converged" on a part whose wall
  thickness nobody could read has verified nothing.

The evaluator never raises. Every failure — the kernel refusing, the objective
path missing from the payload, the model itself throwing — becomes a recorded
`Evaluation` with a sentence saying what happened. The one thing that *does*
raise is a badly posed problem, and that is refused before any of this runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.design.assertions import AssertionResult, Outcome, check_assertions, read_measurement
from app.optimise.problem import OptimisationProblem, margin

#: What the driver is told the objective is at a point that would not build.
#: **Finite, because infinity and NaN both break every gradient-based optimiser
#: in a different and confusing way** — SLSQP's line search silently gives up on
#: an inf and reports success at the point before it. Large enough that no real
#: mass, volume or displacement in mm-N-MPa reaches it, and it is a *fallback*:
#: where feasible points have been seen, the penalty is scaled off the worst of
#: them instead, which keeps the numbers on the same order and the search
#: well-conditioned.
FAILED_BUILD_OBJECTIVE: Final = 1e12

#: How much worse than the worst point seen a failed build is made to look.
#: 10% rather than 10× because the purpose is only to point the search back
#: inside the buildable region, and a cliff of 10× is itself a numerical problem
#: for a finite-difference gradient taken across it.
FAILED_BUILD_PENALTY: Final = 0.1


class Model(Protocol):
    """What an optimisation is run against: something that rebuilds and measures.

    Injected, exactly like `execute`'s runner, `correct`'s builder and
    `sensitivity`'s probe — and for the same reason. Nothing in this package may
    learn whether OCCT, a CATIA seat, a solver or a closed-form expression
    answered, or every driver and every honesty rule here becomes backend
    specific.
    """

    @property
    def backend(self) -> str:
        """What produced the numbers, for the provenance record. e.g. `occt 7.9.3`."""

    def baseline(self) -> Mapping[str, float]:
        """Current value of every settable parameter. May be empty."""

    def evaluate(self, values: Mapping[str, float]) -> Mapping[str, Any]:
        """Rebuild at these parameter values and return the measurement payload.

        Raises where the part will not build. The message is shown to the user
        and put in the record, so it must say what could not be done.
        """


@dataclass(frozen=True)
class Evaluation:
    """One point in the design space, as it actually came back.

    Deliberately has no `value` or `score` attribute. `objective` is the measured
    number and is `None` where nothing was measured; anything a driver needs
    instead is computed by `driver_objective`, which takes the history as context
    and is never stored.
    """

    #: 0-based, in the order the evaluations happened. This is the only ordering
    #: that means anything — an optimiser revisits points and backtracks.
    index: int

    #: The design variables at this point, by name.
    values: Mapping[str, float]

    #: Did the part rebuild at all?
    built: bool

    #: The measured objective, in its own unit and in its own sense — a mass is
    #: a mass whether the run was minimising or maximising it. `None` where the
    #: build failed or the payload did not carry the path.
    objective: float | None = None

    #: The whole measurement payload, for anything else that wants to read it.
    #: Empty on a failed build.
    measurements: Mapping[str, Any] = field(default_factory=dict)

    #: Every declared constraint, checked. `UNMEASURED` results are in here.
    constraints: tuple[AssertionResult, ...] = ()

    #: What went wrong, as a sentence. Empty when nothing did.
    reason: str = ""

    #: Why this build was asked for: `"objective"` for a point the driver chose,
    #: `"gradient"` for a finite-difference probe, `"baseline"` for the start.
    #: Recorded because a history of 60 evaluations of which 48 were gradient
    #: probes is a different story from one that searched 60 designs.
    purpose: str = "objective"

    #: What produced the numbers. Part of binding every result to the thing that
    #: made it (Decision 3).
    backend: str = ""

    #: True where a number this evaluation actually read was not measured off
    #: real geometry — a ray-cast thickness, a mock mass. Carried from
    #: `AssertionReport.approximate`.
    approximate: bool = False

    @property
    def measured(self) -> bool:
        """Did this point produce an objective value at all?"""
        return self.objective is not None

    @property
    def unmeasured_constraints(self) -> tuple[AssertionResult, ...]:
        return tuple(one for one in self.constraints if one.outcome is Outcome.UNMEASURED)

    @property
    def violated(self) -> tuple[AssertionResult, ...]:
        return tuple(one for one in self.constraints if one.outcome is Outcome.FAILED)

    @property
    def feasible(self) -> bool:
        """Built, measured, and every constraint checked and passed.

        An unmeasured constraint makes this False. That is the whole point: a
        design nobody could check is not a design that passed.
        """
        if not self.built or self.objective is None:
            return False
        return all(one.outcome is Outcome.PASSED for one in self.constraints)

    @property
    def infeasibility(self) -> float:
        """Total constraint violation, 0.0 when feasible.

        An unmeasured constraint contributes `inf`, so a point carrying one can
        never compare as better than a point that was actually checked.
        """
        total = 0.0
        for result in self.constraints:
            room = margin(result)
            if room is None:
                return math.inf
            if room < 0:
                total += -room
        if not self.built:
            return math.inf
        return total

    def margins(self) -> dict[str, float | None]:
        """How much room each constraint has left, by name. None = unmeasured."""
        return {one.name: margin(one) for one in self.constraints}

    def __str__(self) -> str:
        where = ", ".join(f"{name}={value:g}" for name, value in self.values.items())
        if not self.built:
            return f"#{self.index} {where}: did not build — {self.reason}"
        if self.objective is None:
            return f"#{self.index} {where}: built, but {self.reason}"
        state = "feasible" if self.feasible else "infeasible"
        tail = ""
        if self.violated:
            tail = " (" + "; ".join(str(one) for one in self.violated) + ")"
        elif self.unmeasured_constraints:
            tail = " (" + "; ".join(str(one) for one in self.unmeasured_constraints) + ")"
        return f"#{self.index} {where}: {self.objective:g}, {state}{tail}"

    def to_dict(self) -> dict[str, Any]:
        """The record, flat enough to store beside a job row."""
        return {
            "index": self.index,
            "values": dict(self.values),
            "built": self.built,
            "objective": self.objective,
            "feasible": self.feasible,
            "infeasibility": None if math.isinf(self.infeasibility) else self.infeasibility,
            "constraints": [one.to_dict() for one in self.constraints],
            "reason": self.reason,
            "purpose": self.purpose,
            "backend": self.backend,
            "approximate": self.approximate,
        }


@dataclass
class EvaluationLog:
    """Every evaluation, in order, including the failures.

    Append-only and not deduplicated. An optimiser revisits the same point — a
    line search that backtracks to where it started, a gradient probe on top of
    an objective call — and collapsing those would make the count of rebuilds
    wrong, which is the number a caller is actually paying.
    """

    evaluations: list[Evaluation] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.evaluations)

    def __iter__(self) -> Any:
        return iter(self.evaluations)

    def __getitem__(self, index: int) -> Evaluation:
        return self.evaluations[index]

    def append(self, evaluation: Evaluation) -> Evaluation:
        self.evaluations.append(evaluation)
        return evaluation

    @property
    def next_index(self) -> int:
        return len(self.evaluations)

    def built(self) -> tuple[Evaluation, ...]:
        return tuple(one for one in self.evaluations if one.built)

    def failures(self) -> tuple[Evaluation, ...]:
        """The points where the part would not build, or would not measure."""
        return tuple(one for one in self.evaluations if not one.measured)

    def feasible(self) -> tuple[Evaluation, ...]:
        return tuple(one for one in self.evaluations if one.feasible)

    def best(self, problem: OptimisationProblem) -> Evaluation | None:
        """The best *feasible* point seen, or None if there was not one.

        Feasible only. A run's best infeasible point is not a candidate answer
        under any circumstances — it is a design that violates a stated
        requirement, and the requirement is why the constraint was written.
        Callers wanting to see it ask `closest_to_feasible`.
        """
        candidates = self.feasible()
        if not candidates:
            return None
        sense = problem.objective.sense.signum
        return min(candidates, key=lambda one: sense * float(one.objective or 0.0))

    def closest_to_feasible(self) -> Evaluation | None:
        """The least-violating built point. **Not an answer** — a diagnosis.

        What a caller looks at to understand why a run found nothing: which
        constraint was still short, and by how much.
        """
        candidates = [one for one in self.evaluations if one.measured]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda one: (one.infeasibility, abs(float(one.objective or 0.0))),
        )

    def counts(self) -> dict[str, int]:
        return {
            "evaluations": len(self.evaluations),
            "built": len(self.built()),
            "failed": len(self.failures()),
            "feasible": len(self.feasible()),
            "gradient_probes": sum(
                1 for one in self.evaluations if one.purpose == "gradient"
            ),
        }

    def summary(self) -> str:
        counts = self.counts()
        head = (
            f"{counts['evaluations']} evaluations: {counts['built']} built, "
            f"{counts['failed']} failed, {counts['feasible']} feasible "
            f"({counts['gradient_probes']} were gradient probes)."
        )
        return "\n".join([head, *(str(one) for one in self.evaluations)])

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "evaluations": [one.to_dict() for one in self.evaluations],
        }


class Evaluator:
    """Turns a `Model` into recorded `Evaluation`s. Never raises.

    Holds the log and the budget, because those are the two things every driver
    needs and neither should be reimplemented per driver. A driver asks for a
    point; it gets one back or it gets told the budget is spent.
    """

    __slots__ = ("_exhausted", "_model", "_problem", "log")

    def __init__(self, problem: OptimisationProblem, model: Model) -> None:
        self._problem = problem
        self._model = model
        self.log = EvaluationLog()
        self._exhausted = False

    @property
    def problem(self) -> OptimisationProblem:
        return self._problem

    @property
    def model(self) -> Model:
        return self._model

    @property
    def exhausted(self) -> bool:
        """True once the evaluation budget has been reached.

        Sticky. A driver that ignores the budget gets the same last evaluation
        back forever rather than an unbounded run, and the result says the
        budget ran out — which is a non-convergence, not a success.
        """
        return self._exhausted

    def at(self, values: Mapping[str, float], *, purpose: str = "objective") -> Evaluation:
        """Rebuild and measure at this point, recording whatever happened."""
        if len(self.log) >= self._problem.max_evaluations:
            self._exhausted = True
            # Not appended: the log is the record of what was *built*, and the
            # budget is exactly the number of rebuilds the caller agreed to pay
            # for. `index=-1` marks a point that was asked for and never run, so
            # a reader cannot mistake it for one that was. The driver sees the
            # failed-build penalty and stops soon after; the result says the
            # budget ran out, which is a non-convergence and not a success.
            return Evaluation(
                index=-1,
                values=dict(values),
                built=False,
                reason=(
                    f"the evaluation budget of {self._problem.max_evaluations} rebuilds "
                    "was spent before the optimiser converged. Raise max_evaluations, "
                    "narrow the bounds, or reduce the number of design variables."
                ),
                purpose=purpose,
                backend=_backend_of(self._model),
                constraints=_unbuilt_constraints(self._problem),
            )

        index = self.log.next_index
        backend = _backend_of(self._model)
        try:
            payload = self._model.evaluate(values)
        except Exception as exc:  # noqa: BLE001 - a failed build is data here
            return self.log.append(
                Evaluation(
                    index=index,
                    values=dict(values),
                    built=False,
                    reason=_sentence(exc),
                    purpose=purpose,
                    backend=backend,
                    constraints=_unbuilt_constraints(self._problem),
                )
            )

        objective = read_measurement(payload, self._problem.objective.measurement)
        report = check_assertions(self._problem.constraints, payload)
        reason = ""
        if objective is None:
            reason = (
                f"the build did not report {self._problem.objective.measurement!r}, so "
                "the objective could not be read. Check the measurement path against "
                "what the backend returns — catia_measure lists what a part reports."
            )
        return self.log.append(
            Evaluation(
                index=index,
                values=dict(values),
                built=True,
                objective=objective,
                measurements=payload,
                constraints=tuple(report.results),
                reason=reason,
                purpose=purpose,
                backend=backend,
                approximate=report.approximate,
            )
        )

    # -- the driver's view ---------------------------------------------------

    def driver_objective(self, evaluation: Evaluation) -> float:
        """The finite number a minimising driver is given for this point.

        **Never stored.** Computed here, from the log, so that a penalty for a
        part that would not build cannot end up in a record as though it were a
        measured mass. Where feasible points exist the penalty is scaled off the
        worst of them, which keeps the search well-conditioned; where none do it
        falls back to `FAILED_BUILD_OBJECTIVE`.
        """
        if evaluation.objective is not None:
            return self._problem.objective.driver_value(evaluation.objective)
        return self._penalty()

    def driver_constraints(self, evaluation: Evaluation) -> list[float]:
        """`g(x) >= 0` for every declared constraint, plus buildability.

        The last entry is the **buildability constraint**: +1 where the part
        built, -1 where it did not. It is not a declared constraint and does not
        appear in the record as one — it is how "this parameter value produces no
        part" reaches an optimiser that only understands numbers, which is the
        behaviour the brief asks for in as many words.

        An unmeasured constraint reports as violated (-1) rather than satisfied.
        A driver told an unmeasured claim was satisfied would converge on it.
        """
        out: list[float] = []
        for result in evaluation.constraints:
            room = margin(result)
            out.append(-1.0 if room is None else room)
        out.append(1.0 if evaluation.built else -1.0)
        return out

    def _penalty(self) -> float:
        seen = [
            self._problem.objective.driver_value(float(one.objective))
            for one in self.log.evaluations
            if one.objective is not None
        ]
        if not seen:
            return FAILED_BUILD_OBJECTIVE
        worst = max(seen)
        return worst + FAILED_BUILD_PENALTY * max(abs(worst), 1.0)


def _backend_of(model: Model) -> str:
    try:
        return str(model.backend)
    except Exception:  # noqa: BLE001 - provenance must not be able to fail a run
        return "unknown"


def _sentence(exc: BaseException) -> str:
    """A build failure as one readable line.

    `str.capitalize()` is not used here for the reason `app/kernel/errors.py`
    gives — it lowercases everything after the first character and turns
    `BRepFeat_MakePrism` into `brepfeat_makeprism`.
    """
    text = str(exc).strip() or exc.__class__.__name__
    return text if text.endswith((".", "!", "?")) else text + "."


def _unbuilt_constraints(problem: OptimisationProblem) -> tuple[AssertionResult, ...]:
    """Every constraint, as UNMEASURED, for a point that produced no part.

    Recorded rather than left empty so the history of a failed point has the same
    shape as the history of a successful one — a reader comparing two rows should
    not have to work out that an absent constraint list means "no part", and a
    `feasible` computed over an empty constraint tuple would say True.
    """
    return tuple(
        AssertionResult(
            assertion=one,
            outcome=Outcome.UNMEASURED,
            reason="the part did not build at these parameter values, so nothing "
            "could be measured on it.",
        )
        for one in problem.constraints
    )


def as_vector(problem: OptimisationProblem, values: Mapping[str, float]) -> Sequence[float]:
    """Named values in the driver's vector order. Here so drivers share one."""
    return problem.vector(values)


__all__ = [
    "FAILED_BUILD_OBJECTIVE",
    "FAILED_BUILD_PENALTY",
    "Evaluation",
    "EvaluationLog",
    "Evaluator",
    "Model",
    "as_vector",
]
