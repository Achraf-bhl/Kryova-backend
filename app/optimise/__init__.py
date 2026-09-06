r"""Optimisation over a built part — master plan Phase 10.3.

**The workload Decision 1 was made for.** OCCT is the internal engine "because a
design loop needs tens of rebuilds a minute and a seat gives one every few
seconds"; until now nothing in the codebase actually ran such a loop. An
optimisation is that loop: set parameters, rebuild, measure, decide, repeat —
a hundred times, in seconds, with no seat and no licence involved.

Almost none of the machinery is new here, and finding that out was the first job.
`app/design/sensitivity.py` has computed ∂measurement/∂parameter since Phase 5.3
and had no caller outside its tests; `app/design/assertions.py` already carries
the three-valued check that makes a constraint honest;
`app/kernel/occt/operations/parameters.py` already rewrites a recorded call and
replays the whole part deterministically into a fresh document, refusing cleanly
and leaving the part intact when a value will not build. What this package adds
is the vocabulary that binds those to an optimisation, the record of every
evaluation, and the honesty rules.

    from app.optimise import DesignVariable, Objective, OptimisationProblem, optimise

    problem = OptimisationProblem.of(
        "lighten the bracket",
        variables=[DesignVariable("web_thickness_mm", lower=2.0, upper=20.0)],
        objective=Objective("mass_kg"),
        constraints=[Assertion("stiff enough", "displacement_mm", "<=", 0.8)],
    )
    result = optimise(problem, PartModel(build=make_bracket))

    if result.converged:
        print(result.summary())        # the design, with every constraint quoted
    else:
        print(result.summary())        # why it did not, and no design at all

**The single most important behaviour in this package**: an optimisation that
did not converge reports that it did not converge, and never returns its last
iterate as though it were an answer. `OptimisationResult` has no `x`, no
`optimum` and no `design`; `solution` returns `None` unless the run converged
*and* the point it converged on was rebuilt, measured and found feasible here —
not in the driver's opinion. See `result.py`, where that gate is written twice
on purpose.

Everything else follows from it. A part that will not build at some parameter
value is normal, so it is recorded as an evaluation with `built=False` and
reaches the optimiser as a constraint violation rather than as a crash. A
constraint nobody could measure makes the point infeasible, because
`assertions.py` will not call an unmeasured claim a pass and neither will this.
The number the driver sees at a failed point is a penalty computed on demand and
is never stored, so nothing reading the history can mistake it for a measurement.

Reading order: `problem.py` (what an optimisation *is*), then `evaluate.py` (what
one step of it records), then `result.py` (what it is allowed to claim).
`gradients.py` is the wiring into `sensitivity.py`, `drivers.py` holds scipy and
OpenMDAO behind one seam, `models.py` is what a run is executed against, and
`run.py` is the front door.

**Scope.** This is the optimisation half of Phase 10. Conduction and CFD (10.1,
10.2) are a much larger integration — OpenFOAM meshing is the hard part and the
board already records thermal *stress* as shipped via `LoadCase.delta_t_k` — and
they are deliberately not here.
"""

from app.optimise.drivers import (
    OPENMDAO,
    SCIPY,
    Driver,
    DriverOutcome,
    OpenMdaoDriver,
    ScipyDriver,
    driver_named,
)
from app.optimise.errors import (
    DriverUnavailable,
    ObjectiveError,
    OptimisationError,
    VariableError,
)
from app.optimise.evaluate import Evaluation, EvaluationLog, Evaluator, Model
from app.optimise.gradients import GradientReport, objective_gradient
from app.optimise.models import AnalyticModel, PartModel, SpecModel
from app.optimise.problem import (
    DEFAULT_MAX_EVALUATIONS,
    DEFAULT_TOLERANCE,
    DesignVariable,
    Objective,
    OptimisationProblem,
    Sense,
    margin,
)
from app.optimise.result import OptimisationResult, Stop
from app.optimise.run import optimise

__all__ = [
    "DEFAULT_MAX_EVALUATIONS",
    "DEFAULT_TOLERANCE",
    "OPENMDAO",
    "SCIPY",
    "AnalyticModel",
    "DesignVariable",
    "Driver",
    "DriverOutcome",
    "DriverUnavailable",
    "Evaluation",
    "EvaluationLog",
    "Evaluator",
    "GradientReport",
    "Model",
    "Objective",
    "ObjectiveError",
    "OpenMdaoDriver",
    "OptimisationError",
    "OptimisationProblem",
    "OptimisationResult",
    "PartModel",
    "ScipyDriver",
    "Sense",
    "SpecModel",
    "Stop",
    "VariableError",
    "driver_named",
    "margin",
    "objective_gradient",
    "optimise",
]
