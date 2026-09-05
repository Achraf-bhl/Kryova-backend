"""The design IR: a part described as a specification, compiled to CATIA operations.

Read `spec.py` first — it says what a design *is*. Then `compile.py`, which says
what happens to one. `names.py` and `params.py` are the two things a spec is
made of and both are usable on their own.

The one-paragraph version: the agent stops editing a feature tree and starts
editing a document that describes a part. That document is compiled into an
ordered plan of registry operations and the part is *regenerated*, never
patched. Everything the roadmap wants from Layer B — diffable designs,
deterministic replay, regression tests over geometry, immunity to topological
naming — follows from that one move.

Then `execute.py` runs a plan, `assertions.py` says whether what came out is
acceptable, `diff.py` says what an edit reached, and `correct.py` closes the
loop between them. Only `execute` touches anything outside this package, and it
does so through an injected callable — so a design can be compiled, diffed and
checked with no CATIA, no database and no network, which is why all of it tests
offline.
"""

from app.design.assertions import (
    Assertion,
    AssertionReport,
    AssertionResult,
    Outcome,
    check_assertions,
)
from app.design.compile import Created, Plan, PlannedCall, bind, compile_spec
from app.design.correct import (
    Attempt,
    Builder,
    CorrectionReport,
    Diagnosis,
    Measurer,
    Repairer,
    Stop,
    correct,
)
from app.design.diff import FeatureChange, ParameterChange, SpecDiff, diff_plans, diff_specs
from app.design.errors import (
    CycleError,
    DesignReferenceError,
    ExpressionError,
    FeatureError,
    ParameterError,
    PolicyError,
    SemanticNameError,
    SpecError,
    UnitError,
)
from app.design.execute import (
    BuildFailure,
    BuildReport,
    CallResult,
    CallRunner,
    execute_plan,
)
from app.design.names import NameTable, SemanticName
from app.design.params import (
    Dimension,
    Parameter,
    ParameterSet,
    Quantity,
    ResolvedParameters,
    Unit,
)
from app.design.spec import DesignSpec, FeatureSpec, expr, ref, refs

__all__ = [
    "Assertion",
    "AssertionReport",
    "AssertionResult",
    "Attempt",
    "BuildFailure",
    "BuildReport",
    "Builder",
    "CallResult",
    "CallRunner",
    "CorrectionReport",
    "Created",
    "CycleError",
    "DesignReferenceError",
    "DesignSpec",
    "Diagnosis",
    "Dimension",
    "ExpressionError",
    "FeatureChange",
    "FeatureError",
    "FeatureSpec",
    "Measurer",
    "NameTable",
    "Outcome",
    "Parameter",
    "ParameterChange",
    "ParameterError",
    "ParameterSet",
    "Plan",
    "PlannedCall",
    "PolicyError",
    "Quantity",
    "Repairer",
    "ResolvedParameters",
    "SemanticName",
    "SemanticNameError",
    "SpecDiff",
    "SpecError",
    "Stop",
    "Unit",
    "UnitError",
    "bind",
    "check_assertions",
    "compile_spec",
    "correct",
    "diff_plans",
    "diff_specs",
    "execute_plan",
    "expr",
    "ref",
    "refs",
]
