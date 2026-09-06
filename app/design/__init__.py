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

from typing import TYPE_CHECKING, Any, Final

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

if TYPE_CHECKING:  # pragma: no cover - for type checkers and readers, never at runtime
    from app.design.missions import (
        AssemblyDesign,
        AssemblyReport,
        LadderReport,
        Mission,
        MissionOutcome,
        MissionResult,
    )

#: Names that live in `app.design.missions` and are re-exported **lazily**.
#:
#: A layering fact rather than a taste. `missions.py` reaches *up* into
#: `app.assembly` — every rung above M1 is a product, not a part — while
#: `app.assembly.structure` reaches back down into `app.design.names` and
#: `app.design.errors`. Importing missions in this file would make `import
#: app.assembly` execute it, which would execute missions, which would import
#: `app.assembly.clash` while `app.assembly` was still half-built: an ImportError
#: that depends on which package a process happens to import first, which is the
#: worst kind. PEP 562 defers the import to the first attribute access, by which
#: time whichever package started is finished. `import app.design.missions` and
#: `from app.design import run_ladder` both still work; only the moment changes.
_MISSION_EXPORTS: Final[frozenset[str]] = frozenset(
    {
        "LADDER",
        "AssemblyDesign",
        "AssemblyReport",
        "LadderReport",
        "Mission",
        "MissionOutcome",
        "MissionResult",
        "mission",
        "run_ladder",
        "run_mission",
    }
)


def __getattr__(name: str) -> Any:
    if name in _MISSION_EXPORTS:
        from app.design import missions

        return getattr(missions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AssemblyDesign",
    "AssemblyReport",
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
    "LADDER",
    "LadderReport",
    "Measurer",
    "Mission",
    "MissionOutcome",
    "MissionResult",
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
    "mission",
    "ref",
    "refs",
    "run_ladder",
    "run_mission",
]
