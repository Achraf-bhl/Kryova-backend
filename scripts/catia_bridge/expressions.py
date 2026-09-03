"""Evaluating a CATIA formula, safely, without CATIA.

A formula in a CATIA part is an expression over other parameters —
`Width * 2 + Clearance`, `Bore / 2`. The mock has to actually evaluate them or
its formulas would be labels rather than behaviour, and a formula that does not
drive its parameter is worse than no formula at all: it looks correct in the
tree and silently stops propagating.

`eval` is not an option. These strings arrive from a language model, which is
one prompt injection away from being attacker-controlled, and `eval` on such a
string is arbitrary code execution inside the bridge process — the same process
holding a COM handle to the engineer's CATIA. So this parses to an AST and walks
it, admitting exactly the node types arithmetic needs and refusing every other
one by default. There is no name lookup that is not a parameter, no attribute
access, no call to anything but the short list below.

Units are stripped rather than converted, because a CATIA expression carries
them inline (`10mm + 2mm`) and everything in this system is already millimetres.
A unit that is *not* millimetres is refused rather than silently treated as if
it were.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any, Callable

#: The arithmetic each operator node maps to. Anything absent is refused, which
#: is how bitwise, matrix-multiply and the walrus stay out without being named.
_BINARY: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

_UNARY: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

#: Functions a dimension expression legitimately uses. Deliberately short: this
#: is a formula language for CAD parameters, not a scripting environment.
_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": lambda value, digits=0: round(value, int(digits)),
    "sqrt": math.sqrt,
    "sin": lambda degrees: math.sin(math.radians(degrees)),
    "cos": lambda degrees: math.cos(math.radians(degrees)),
    "tan": lambda degrees: math.tan(math.radians(degrees)),
    "atan": lambda value: math.degrees(math.atan(value)),
    "floor": math.floor,
    "ceil": math.ceil,
    "pi": lambda: math.pi,
}

#: Unit suffixes CATIA writes inline. Only the ones this system already works
#: in; anything else is a real mismatch and is reported rather than dropped.
_SUPPORTED_UNITS = {"mm", "deg", "kg", "N", "MPa", "mm2", "mm3", "s"}

#: `10mm`, `2.5 deg` — a number immediately followed by a unit word.
_INLINE_UNIT = re.compile(r"(?<=[\d.\s])([A-Za-z][A-Za-z0-9_]*)\b")

#: The largest exponent an expression may raise to. `2 ** 10 ** 10` is a
#: three-token denial of service, and there is no CAD formula that needs it.
_MAX_EXPONENT = 64


class ExpressionError(ValueError):
    """The formula could not be evaluated, phrased for whoever wrote it."""


def parameter_names(expression: str) -> list[str]:
    """Every parameter an expression reads, in the order they appear.

    Used to detect a formula that refers to something that does not exist
    *before* it is stored, so the failure names the parameter rather than
    surfacing later as a stale value.
    """
    try:
        tree = ast.parse(_strip_units(expression), mode="eval")
    except SyntaxError as error:
        raise ExpressionError(f"{expression!r} is not a valid expression: {error.msg}.") from error

    # Sorted by position, because `ast.walk` is breadth-first and would report
    # `Width * 2 + Clearance` as Clearance-then-Width. The order shows up in
    # error messages next to the formula the reader is looking at.
    names = sorted(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id not in _FUNCTIONS
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    found: list[str] = []
    for node in names:
        if node.id not in found:
            found.append(node.id)
    return found


def evaluate(expression: str, parameters: dict[str, float]) -> float:
    """The value of `expression`, given the parameters it may read.

    Refuses anything that is not arithmetic over those parameters. See the
    module docstring for why that refusal is the point rather than a limitation.
    """
    cleaned = _strip_units(expression)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as error:
        raise ExpressionError(
            f"{expression!r} is not a valid expression: {error.msg}."
        ) from error

    try:
        return float(_walk(tree.body, parameters))
    except ExpressionError:
        raise
    except ZeroDivisionError as error:
        raise ExpressionError(f"{expression!r} divides by zero.") from error
    except (TypeError, ValueError, OverflowError) as error:
        raise ExpressionError(f"{expression!r} could not be evaluated: {error}") from error


def _strip_units(expression: str) -> str:
    """Remove inline unit suffixes, refusing any this system does not work in."""

    def replace(match: re.Match[str]) -> str:
        word = match.group(1)
        if word in _FUNCTIONS:
            return word
        if word in _SUPPORTED_UNITS:
            return ""
        # Not a unit at all: almost certainly a parameter name, which is left
        # alone so the name resolution below can report it properly.
        return word

    return _INLINE_UNIT.sub(replace, expression)


def _walk(node: ast.AST, parameters: dict[str, float]) -> Any:
    """Evaluate one node, refusing every node type not explicitly handled."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ExpressionError(
            f"{node.value!r} is not a number. A formula computes a dimension, so it "
            "may only contain numbers, parameter names and arithmetic."
        )

    if isinstance(node, ast.Name):
        if node.id in parameters:
            return parameters[node.id]
        known = ", ".join(sorted(parameters)[:12]) or "(none)"
        raise ExpressionError(
            f"The formula refers to {node.id!r}, which is not a parameter of this "
            f"part. Defined parameters: {known}."
        )

    if isinstance(node, ast.BinOp):
        handler = _BINARY.get(type(node.op))
        if handler is None:
            raise ExpressionError(
                f"{type(node.op).__name__} is not an operator a formula may use."
            )
        left, right = _walk(node.left, parameters), _walk(node.right, parameters)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > _MAX_EXPONENT:
            raise ExpressionError(
                f"An exponent of {right} is too large to evaluate. Formulas raise to "
                f"at most {_MAX_EXPONENT}."
            )
        return handler(left, right)

    if isinstance(node, ast.UnaryOp):
        handler = _UNARY.get(type(node.op))
        if handler is None:
            raise ExpressionError(
                f"{type(node.op).__name__} is not an operator a formula may use."
            )
        return handler(_walk(node.operand, parameters))

    if isinstance(node, ast.Compare):
        # Comparisons are here for `catia_check_create`, whose whole job is to
        # assert a condition; they are of no use to a dimension formula but do
        # no harm there either.
        left = _walk(node.left, parameters)
        for op, comparator in zip(node.ops, node.comparators, strict=False):
            handler = _COMPARE.get(type(op))
            if handler is None:
                raise ExpressionError(
                    f"{type(op).__name__} is not a comparison a check may use."
                )
            right = _walk(comparator, parameters)
            if not handler(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        values = [_walk(value, parameters) for value in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            name = getattr(node.func, "id", "that")
            raise ExpressionError(
                f"{name!r} is not a function a formula may call. Available: "
                f"{', '.join(sorted(_FUNCTIONS))}."
            )
        if node.keywords:
            raise ExpressionError("Formula functions take positional arguments only.")
        arguments = [_walk(argument, parameters) for argument in node.args]
        return _FUNCTIONS[node.func.id](*arguments)

    raise ExpressionError(
        f"{type(node).__name__} is not allowed in a formula. A formula may contain "
        "numbers, parameter names, arithmetic and the standard maths functions."
    )
