"""Everything that can be wrong with a design specification, named.

These exist as their own hierarchy rather than as `ValueError` for one reason
that matters more than tidiness: **a spec error must be distinguishable from a
CAD error.** The whole argument for compiling a spec is that a conflict is
caught before CATIA is touched, and the agent's recovery is different in each
case — a `SpecError` means edit the spec and recompile, a `CatiaOperationError`
means the seat refused something and the spec may be fine.

The messages follow the house register set by the route layer: say what is
wrong *and what to do about it*. "Unknown parameter 'wall'" is a shrug;
"Unknown parameter 'wall'. Declared parameters are: wall_mm, bore_mm" is a fix.
"""

from __future__ import annotations


class SpecError(Exception):
    """The design specification is wrong. CATIA was not touched."""


class NameError_(SpecError):
    """A semantic name is malformed, duplicated, or refers to nothing.

    Named with a trailing underscore because `NameError` is a builtin and
    shadowing it inside this package would make every genuine `NameError` in
    this code look like a spec problem. Exported as `SemanticNameError`.
    """


SemanticNameError = NameError_


class ParameterError(SpecError):
    """A parameter is undeclared, cyclic, dimensionally wrong, or unevaluable."""


class ExpressionError(ParameterError):
    """An expression could not be parsed, or used something not allowed in one.

    Expressions are evaluated from an AST with a whitelist rather than by
    `eval`, so this is also what an attempt to reach outside the whitelist
    raises — an attribute access, a call to something not in the function
    table, a comprehension.
    """


class UnitError(ParameterError):
    """Two quantities were combined whose units do not agree.

    The project rule is mm-N-MPa and **nothing converts**. So this is never
    resolved by scaling one side; it is resolved by fixing whichever operand
    was written in the wrong unit.
    """


class CycleError(ParameterError):
    """The parameter graph has a cycle. The message names the loop."""


class FeatureError(SpecError):
    """A feature names an operation that does not exist, or arguments it cannot take."""


class ReferenceError_(SpecError):
    """A feature refers to something that is not built yet, or not built at all.

    Trailing underscore for the same reason as `NameError_`. Exported as
    `DesignReferenceError`.
    """


DesignReferenceError = ReferenceError_


class PolicyError(SpecError):
    """The spec asks for something a compiled design is not allowed to do.

    Currently one thing: a destructive operation. A spec is a description of a
    part that should be reproducible by replaying it, and an operation that
    cannot be undone by restoring a checkpoint has no place in a replay.
    """


__all__ = [
    "CycleError",
    "DesignReferenceError",
    "ExpressionError",
    "FeatureError",
    "ParameterError",
    "PolicyError",
    "SemanticNameError",
    "SpecError",
    "UnitError",
]
