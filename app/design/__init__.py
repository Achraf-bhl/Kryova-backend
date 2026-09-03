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
"""

from app.design.compile import Created, Plan, PlannedCall, bind, compile_spec
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
    "Created",
    "CycleError",
    "DesignReferenceError",
    "DesignSpec",
    "Dimension",
    "ExpressionError",
    "FeatureError",
    "FeatureSpec",
    "NameTable",
    "Parameter",
    "ParameterError",
    "ParameterSet",
    "Plan",
    "PlannedCall",
    "PolicyError",
    "Quantity",
    "ResolvedParameters",
    "SemanticName",
    "SemanticNameError",
    "SpecError",
    "Unit",
    "UnitError",
    "bind",
    "compile_spec",
    "expr",
    "ref",
    "refs",
]
