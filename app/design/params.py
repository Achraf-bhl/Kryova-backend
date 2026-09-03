"""The design's own parameter graph, resolved before CATIA is touched.

A design is a handful of decisions and a great many consequences of them:
wheelbase decides swingarm length decides pivot position; blank thickness
decides die clearance decides press force decides frame section. Today those
consequences are re-derived by hand in conversation, which means a change to
the decision does not reach them and nothing says so.

This module holds them as a graph and resolves it. Two properties are the whole
point:

* **Conflicts surface as spec errors.** A cycle, an undeclared parameter, a
  dimension that does not add up — all of it is caught here, with CATIA
  untouched. The alternative is a COM error thirty operations into a rebuild,
  which tells you a feature failed and nothing about why.
* **Resolution is deterministic.** Same parameters, same values, same order,
  every time. Ties in the topological sort break on declaration order, never on
  set iteration, because "same spec ⇒ same geometry" (roadmap I5) does not
  survive a `set` being walked in hash order.

**Dimensions are checked and units are never converted.** The project rule is
mm-N-MPa throughout and nothing in this codebase converts; that rule needs
enforcement at the point where quantities get combined, which is here. Adding a
length to a force is refused. Multiplying two lengths yields an area, and
declaring the result in `mm` is refused. What is *not* done is scaling anything
to make it fit: a mismatch means one of the two operands was written wrong, and
the fix is in the spec.

The one concession, made knowingly: **a bare number adopts the dimension of
whatever it is being combined with.** `wall_mm + 2` means 2 mm, `max(bore_mm, 8)`
means 8 mm, and `plate_mm >= 6` compares against 6 mm. Requiring every literal to
be dimensioned is defensible on paper and is not how any parametric CAD system
reads — nor how an engineer writes. The concession is applied at exactly one
place, `_same_dimension`, so it is uniform rather than a list of special cases,
and its cost is bounded: a bare literal can only ever *adopt*, never override, so
it can make a silent pair explicit but can never reconcile two real units that
disagree. `wall_mm + draft_deg` is still refused.

Expressions are evaluated from a parsed AST against a whitelist, never `eval`.
Anything outside the whitelist — an attribute, a subscript, a comprehension, a
call to a function not in the table — is an `ExpressionError` rather than a
capability.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.catia.ops import vocabulary
from app.design.errors import CycleError, ExpressionError, ParameterError, UnitError

# -- units and dimensions ---------------------------------------------------


class Unit(StrEnum):
    """A unit a design parameter may carry.

    The set is exactly `vocabulary.PARAMETER_UNITS`, and that is not a
    coincidence to be tidied away: those are the units a CATIA parameter can be
    created with, so a design parameter can be published into the part as a real
    CATIA parameter without a translation table in between. Adding a unit here
    that CATIA has no notion of would break that, silently, at the point of
    publication.
    """

    MM = "mm"
    DEG = "deg"
    KG = "kg"
    MM2 = "mm2"
    MM3 = "mm3"
    NEWTON = "N"
    MPA = "MPa"
    DEG_C = "deg_c"
    SECOND = "s"
    NONE = ""


@dataclass(frozen=True)
class Dimension:
    """What a quantity *is*, independent of how it is written.

    Six independent bases, not the physicist's four, because this codebase does
    not use a consistent unit system: mass is kilograms and force is newtons
    with no `kg·mm/s²` bridging them. Treating force as a base rather than
    deriving it is the honest model of what the code actually does, and it
    means `MPa = N/mm²` checks out while `N = kg·mm/s²` — which would be wrong
    here by a factor of 1000 — never arises.
    """

    length: int = 0
    mass: int = 0
    time: int = 0
    temperature: int = 0
    angle: int = 0
    force: int = 0

    def __mul__(self, other: Dimension) -> Dimension:
        return Dimension(*(a + b for a, b in zip(self.as_tuple(), other.as_tuple(), strict=True)))

    def __truediv__(self, other: Dimension) -> Dimension:
        return Dimension(*(a - b for a, b in zip(self.as_tuple(), other.as_tuple(), strict=True)))

    def __pow__(self, exponent: int) -> Dimension:
        return Dimension(*(a * exponent for a in self.as_tuple()))

    def as_tuple(self) -> tuple[int, int, int, int, int, int]:
        return (self.length, self.mass, self.time, self.temperature, self.angle, self.force)

    @property
    def is_dimensionless(self) -> bool:
        return not any(self.as_tuple())

    def root(self, degree: int) -> Dimension:
        """The `degree`-th root, refusing one that is not whole.

        `sqrt(area)` is a length; `sqrt(length)` is nothing this system can
        name, and silently rounding the exponent would let it through as one.
        """
        parts = self.as_tuple()
        if any(part % degree for part in parts):
            raise UnitError(
                f"Taking the root of degree {degree} of {self} does not give a whole "
                "dimension, so the result is not a quantity this design can express. "
                "Check which operand is written in the wrong unit."
            )
        return Dimension(*(part // degree for part in parts))

    def __str__(self) -> str:
        unit = UNIT_OF_DIMENSION.get(self)
        if unit is not None:
            return f"'{unit.value}'" if unit.value else "a plain number"
        names = ("mm", "kg", "s", "deg_c", "deg", "N")
        parts = [
            f"{name}^{power}" if power != 1 else name
            for name, power in zip(names, self.as_tuple(), strict=True)
            if power
        ]
        return "·".join(parts) or "a plain number"


DIMENSIONLESS: Final = Dimension()

#: What each unit measures. `MPa` is `N/mm²` here rather than a base of its own,
#: which is what makes `force / area` check out against a parameter declared in
#: MPa without a special case.
UNIT_DIMENSIONS: Final[dict[Unit, Dimension]] = {
    Unit.MM: Dimension(length=1),
    Unit.MM2: Dimension(length=2),
    Unit.MM3: Dimension(length=3),
    Unit.DEG: Dimension(angle=1),
    Unit.KG: Dimension(mass=1),
    Unit.NEWTON: Dimension(force=1),
    Unit.MPA: Dimension(force=1, length=-2),
    Unit.DEG_C: Dimension(temperature=1),
    Unit.SECOND: Dimension(time=1),
    Unit.NONE: DIMENSIONLESS,
}

#: The reverse map, used only to make error messages read in units rather than
#: in exponents. Unambiguous because no two units above share a dimension.
UNIT_OF_DIMENSION: Final[dict[Dimension, Unit]] = {
    dimension: unit for unit, dimension in UNIT_DIMENSIONS.items()
}


def _check_vocabulary_alignment() -> None:
    """Fail at import if `Unit` and `vocabulary.PARAMETER_UNITS` have drifted.

    Same argument as the registry's duplicate-name check: this pairing is only
    discoverable by reading two files side by side, so it gets a one-line
    assertion instead.
    """
    declared = {unit.value for unit in Unit}
    catia = set(vocabulary.PARAMETER_UNITS)
    if declared != catia:
        raise RuntimeError(
            "app.design.params.Unit and app.catia.ops.vocabulary.PARAMETER_UNITS have "
            f"drifted: only here {sorted(declared - catia)}, only there "
            f"{sorted(catia - declared)}. A design parameter must be publishable as a "
            "CATIA parameter without a translation table."
        )


_check_vocabulary_alignment()


@dataclass(frozen=True)
class Quantity:
    """A number and what it is. The unit of account inside an expression."""

    value: float
    dimension: Dimension = DIMENSIONLESS

    @classmethod
    def of(cls, value: float, unit: Unit) -> Quantity:
        return cls(value=float(value), dimension=UNIT_DIMENSIONS[unit])

    @property
    def unit(self) -> Unit | None:
        """The named unit, or None when the dimension has no single name."""
        return UNIT_OF_DIMENSION.get(self.dimension)


# -- expressions ------------------------------------------------------------

#: Named constants an expression may use. Dimensionless, all of them — an
#: expression that wants a dimensioned constant should declare a parameter, so
#: the number is visible in the design rather than buried in a formula.
_CONSTANTS: Final[dict[str, Quantity]] = {
    "pi": Quantity(math.pi),
    "e": Quantity(math.e),
}


def _same_dimension(name: str, left: Quantity, right: Quantity) -> Dimension:
    """The dimension two operands must share, letting a bare literal adopt.

    Every place in this module where two quantities have to agree goes through
    here, which is what makes the literal-adoption concession one rule rather
    than a scattering of special cases. `max(bore_mm, 8)` and `plate_mm >= 6`
    work for the same reason `wall_mm + 2` does.
    """
    if left.dimension == right.dimension:
        return left.dimension
    if left.dimension.is_dimensionless:
        return right.dimension
    if right.dimension.is_dimensionless:
        return left.dimension
    raise UnitError(
        f"{name} needs both sides to be the same kind of quantity, but one is "
        f"{left.dimension} and the other is {right.dimension}. Nothing here converts "
        "units — fix whichever operand is written in the wrong one."
    )


def _radians(argument: Quantity, function: str) -> float:
    """Read an angle for a trig function, insisting it is actually an angle.

    Degrees are the project's angle unit, so the conversion to radians happens
    here, inside the function, and never to a value the design stores. A plain
    number is accepted too — `sin(0)` should not need a unit — but a length is
    not, because `sin(wall_mm)` is a mistake every time.
    """
    if argument.dimension not in (UNIT_DIMENSIONS[Unit.DEG], DIMENSIONLESS):
        raise UnitError(
            f"{function}() takes an angle in degrees, but it was given {argument.dimension}."
        )
    return math.radians(argument.value)


def _fn_sqrt(x: Quantity) -> Quantity:
    return Quantity(math.sqrt(x.value), x.dimension.root(2))


def _fn_hypot(*args: Quantity) -> Quantity:
    dimension = args[0].dimension
    for other in args[1:]:
        dimension = _same_dimension("hypot()", Quantity(0.0, dimension), other)
    return Quantity(math.hypot(*(a.value for a in args)), dimension)


def _fn_minmax(chooser: Any, label: str) -> Any:
    """min/max over quantities that must agree — and one of them may be a literal.

    The dimension is accumulated rather than taken from the first argument, so
    `max(8, bore_mm)` means the same as `max(bore_mm, 8)`. Taking the first
    argument's would have made the answer depend on the order they were
    written, which is precisely the kind of quiet asymmetry this module exists
    to keep out of a design.
    """

    def call(*args: Quantity) -> Quantity:
        dimension = args[0].dimension
        for other in args[1:]:
            dimension = _same_dimension(f"{label}()", Quantity(0.0, dimension), other)
        return Quantity(chooser(a.value for a in args), dimension)

    return call


def _fn_dimensionless(fn: Any, label: str) -> Any:
    def call(x: Quantity) -> Quantity:
        if not x.dimension.is_dimensionless:
            raise UnitError(f"{label}() takes a plain number, but it was given {x.dimension}.")
        return Quantity(fn(x.value))

    return call


def _fn_trig(fn: Any, label: str) -> Any:
    def call(x: Quantity) -> Quantity:
        return Quantity(fn(_radians(x, label)))

    return call


def _fn_inverse_trig(fn: Any, label: str) -> Any:
    """Inverse trig returns an angle, and in this codebase an angle is degrees."""

    def call(x: Quantity) -> Quantity:
        if not x.dimension.is_dimensionless:
            raise UnitError(f"{label}() takes a plain number, got {x.dimension}.")
        return Quantity(math.degrees(fn(x.value)), UNIT_DIMENSIONS[Unit.DEG])

    return call


def _fn_atan2(y: Quantity, x: Quantity) -> Quantity:
    """Two-argument arctangent — the one that gets the quadrant right.

    Its operands are a rise and a run, so they must share a dimension; the
    result is an angle in degrees like every other angle here.
    """
    _same_dimension("atan2()", y, x)
    return Quantity(math.degrees(math.atan2(y.value, x.value)), UNIT_DIMENSIONS[Unit.DEG])


def _fn_round(x: Quantity, digits: Quantity | None = None) -> Quantity:
    if digits is not None and not digits.dimension.is_dimensionless:
        raise UnitError("round()'s second argument is a digit count, not a dimensioned value.")
    places = int(digits.value) if digits is not None else 0
    return Quantity(round(x.value, places), x.dimension)


def _fn_step(x: Quantity, size: Quantity) -> Quantity:
    """Round *up* to the next whole multiple of `size` — stock and standard sizes.

    Its own function rather than `ceil(x / size) * size` because that spelling
    loses the dimension on the way through `ceil` and reads worse than what it
    means: plate comes in 0.5 mm steps, bolts in preferred lengths, and a design
    that computes 6.3 mm of plate has to buy 6.5.
    """
    dimension = _same_dimension("step()", x, size)
    if size.value <= 0:
        raise ExpressionError("step()'s second argument is a step size and must be positive.")
    return Quantity(math.ceil(x.value / size.value) * size.value, dimension)


#: Every function an expression may call. Anything not here is refused by name,
#: which is the difference between a whitelist and a sandbox with holes in it.
_FUNCTIONS: Final[dict[str, Any]] = {
    "abs": lambda x: Quantity(abs(x.value), x.dimension),
    "min": _fn_minmax(min, "min"),
    "max": _fn_minmax(max, "max"),
    "sqrt": _fn_sqrt,
    "hypot": _fn_hypot,
    "floor": lambda x: Quantity(float(math.floor(x.value)), x.dimension),
    "ceil": lambda x: Quantity(float(math.ceil(x.value)), x.dimension),
    "round": _fn_round,
    "step": _fn_step,
    "sin": _fn_trig(math.sin, "sin"),
    "cos": _fn_trig(math.cos, "cos"),
    "tan": _fn_trig(math.tan, "tan"),
    "asin": _fn_inverse_trig(math.asin, "asin"),
    "acos": _fn_inverse_trig(math.acos, "acos"),
    "atan": _fn_inverse_trig(math.atan, "atan"),
    "atan2": _fn_atan2,
    "log": _fn_dimensionless(math.log, "log"),
    "exp": _fn_dimensionless(math.exp, "exp"),
}

#: How many arguments each function takes. Checked before the call so an arity
#: mistake reads as a spec error rather than as a Python TypeError.
_ARITY: Final[dict[str, tuple[int, int]]] = {
    "abs": (1, 1),
    "min": (1, 16),
    "max": (1, 16),
    "sqrt": (1, 1),
    "hypot": (2, 16),
    "floor": (1, 1),
    "ceil": (1, 1),
    "round": (1, 2),
    "step": (2, 2),
    "sin": (1, 1),
    "cos": (1, 1),
    "tan": (1, 1),
    "asin": (1, 1),
    "acos": (1, 1),
    "atan": (1, 1),
    "atan2": (2, 2),
    "log": (1, 1),
    "exp": (1, 1),
}


def parse(expression: str) -> ast.Expression:
    """Parse an expression, refusing anything that is not one."""
    text = expression.strip()
    if text.startswith("="):
        text = text[1:].strip()
    if not text:
        raise ExpressionError("An expression cannot be empty.")
    try:
        return ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"{expression!r} is not a valid expression: {exc.msg}.") from None


def dependencies(expression: str) -> tuple[str, ...]:
    """Every parameter name an expression reads, sorted and without duplicates.

    Sorted rather than in-order because this feeds the dependency graph, and the
    graph's edges must not depend on where in the formula a name happened to
    appear.
    """
    tree = parse(expression)
    found = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id not in _CONSTANTS
    }
    return tuple(sorted(found - set(_FUNCTIONS)))


def evaluate(expression: str, values: Mapping[str, Quantity]) -> Quantity:
    """Evaluate one expression against already-resolved parameter values."""
    return _Evaluator(values, expression).run(parse(expression))


class _Evaluator:
    """Walks the whitelisted AST, carrying a dimension alongside every number."""

    def __init__(self, values: Mapping[str, Quantity], source: str) -> None:
        self._values = values
        self._source = source

    def run(self, tree: ast.Expression) -> Quantity:
        return self._visit(tree.body)

    def _refuse(self, node: ast.AST) -> Any:
        raise ExpressionError(
            f"{type(node).__name__} is not allowed in a design expression "
            f"({self._source!r}). Expressions are arithmetic over declared parameters "
            f"and these functions: {', '.join(sorted(_FUNCTIONS))}."
        )

    def _visit(self, node: ast.AST) -> Quantity:
        handler = getattr(self, f"_on_{type(node).__name__}", None)
        if handler is None:
            return self._refuse(node)
        result: Quantity = handler(node)
        return result

    # -- leaves --------------------------------------------------------------

    def _on_Constant(self, node: ast.Constant) -> Quantity:  # noqa: N802 - ast visitor
        if isinstance(node.value, bool):
            return Quantity(1.0 if node.value else 0.0)
        if isinstance(node.value, (int, float)):
            return Quantity(float(node.value))
        raise ExpressionError(
            f"{node.value!r} is not a number. A design expression computes dimensions, "
            "not text."
        )

    def _on_Name(self, node: ast.Name) -> Quantity:  # noqa: N802 - ast visitor
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        try:
            return self._values[node.id]
        except KeyError:
            known = ", ".join(sorted(self._values)) or "none"
            raise ParameterError(
                f"{self._source!r} reads {node.id!r}, which is not a declared parameter. "
                f"Declared: {known}."
            ) from None

    # -- operators -----------------------------------------------------------

    def _on_UnaryOp(self, node: ast.UnaryOp) -> Quantity:  # noqa: N802 - ast visitor
        operand = self._visit(node.operand)
        if isinstance(node.op, ast.USub):
            return Quantity(-operand.value, operand.dimension)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.Not):
            if not operand.dimension.is_dimensionless:
                raise UnitError(f"'not' takes a condition, not {operand.dimension}.")
            return Quantity(0.0 if operand.value else 1.0)
        return self._refuse(node.op)

    def _on_BinOp(self, node: ast.BinOp) -> Quantity:  # noqa: N802 - ast visitor
        left = self._visit(node.left)
        right = self._visit(node.right)
        op = node.op

        if isinstance(op, (ast.Add, ast.Sub, ast.Mod)):
            dimension = self._additive_dimension(op, left, right)
            if isinstance(op, ast.Add):
                return Quantity(left.value + right.value, dimension)
            if isinstance(op, ast.Sub):
                return Quantity(left.value - right.value, dimension)
            if right.value == 0:
                raise ExpressionError(f"{self._source!r} takes a remainder modulo zero.")
            return Quantity(math.fmod(left.value, right.value), dimension)

        if isinstance(op, ast.Mult):
            return Quantity(left.value * right.value, left.dimension * right.dimension)

        if isinstance(op, ast.Div):
            if right.value == 0:
                raise ExpressionError(
                    f"{self._source!r} divides by zero. If the divisor is a parameter, "
                    "give it a non-zero value or guard the expression with an if."
                )
            return Quantity(left.value / right.value, left.dimension / right.dimension)

        if isinstance(op, ast.Pow):
            return self._power(node, left, right)

        return self._refuse(op)

    def _additive_dimension(
        self, op: ast.operator, left: Quantity, right: Quantity
    ) -> Dimension:
        """Add/subtract/modulo: dimensions must agree, a bare literal adopts.

        The adoption is the documented concession — `wall_mm + 2` means 2 mm.
        It only ever runs when one side is genuinely dimensionless, so it can
        make a *silent* pair explicit but can never reconcile two real units
        that disagree.
        """
        symbols: dict[type[ast.operator], str] = {ast.Add: "+", ast.Sub: "-", ast.Mod: "%"}
        symbol = symbols[type(op)]
        try:
            return _same_dimension(f"{symbol!r}", left, right)
        except UnitError:
            raise UnitError(
                f"{self._source!r} computes {left.dimension} {symbol} {right.dimension}. "
                "Nothing in this codebase converts units, so one of the two operands is "
                "written in the wrong one."
            ) from None

    def _power(self, node: ast.BinOp, left: Quantity, right: Quantity) -> Quantity:
        if not right.dimension.is_dimensionless:
            raise UnitError(f"An exponent must be a plain number, not {right.dimension}.")
        if left.dimension.is_dimensionless:
            if left.value < 0 and right.value != int(right.value):
                raise ExpressionError(
                    f"{self._source!r} raises a negative number to a fractional power, "
                    "which has no real answer."
                )
            return Quantity(float(left.value**right.value))
        if right.value != int(right.value):
            raise UnitError(
                f"{self._source!r} raises {left.dimension} to the power {right.value}, "
                "which is not a whole number and so is not a quantity this design can "
                "name. Only a plain number takes a fractional power."
            )
        power = int(right.value)
        return Quantity(left.value**power, left.dimension**power)

    def _on_Compare(self, node: ast.Compare) -> Quantity:  # noqa: N802 - ast visitor
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise ExpressionError(
                f"{self._source!r} chains comparisons. Write them out with 'and' — a "
                "chain reads as one claim and is two."
            )
        left = self._visit(node.left)
        right = self._visit(node.comparators[0])
        _same_dimension("A comparison", left, right)
        op = node.ops[0]
        table = {
            ast.Lt: left.value < right.value,
            ast.LtE: left.value <= right.value,
            ast.Gt: left.value > right.value,
            ast.GtE: left.value >= right.value,
            ast.Eq: left.value == right.value,
            ast.NotEq: left.value != right.value,
        }
        for kind, outcome in table.items():
            if isinstance(op, kind):
                return Quantity(1.0 if outcome else 0.0)
        return self._refuse(op)

    def _on_BoolOp(self, node: ast.BoolOp) -> Quantity:  # noqa: N802 - ast visitor
        values = [self._visit(value) for value in node.values]
        for value in values:
            if not value.dimension.is_dimensionless:
                raise UnitError(
                    f"'and'/'or' combine conditions, but one side is {value.dimension}. "
                    "Compare it against something first."
                )
        truths = [bool(value.value) for value in values]
        outcome = all(truths) if isinstance(node.op, ast.And) else any(truths)
        return Quantity(1.0 if outcome else 0.0)

    def _on_IfExp(self, node: ast.IfExp) -> Quantity:  # noqa: N802 - ast visitor
        condition = self._visit(node.test)
        if not condition.dimension.is_dimensionless:
            raise UnitError(f"An if-condition must be a condition, not {condition.dimension}.")
        # Both branches are evaluated so a dimension mistake in the untaken one
        # is still caught. A design is checked once and rebuilt many times; a
        # latent unit error in the branch nobody took today is exactly the kind
        # that surfaces during a rebuild six months later.
        taken = self._visit(node.body)
        other = self._visit(node.orelse)
        _same_dimension("The two branches of an if", taken, other)
        return taken if condition.value else other

    def _on_Call(self, node: ast.Call) -> Quantity:  # noqa: N802 - ast visitor
        if not isinstance(node.func, ast.Name):
            raise ExpressionError(
                f"{self._source!r} calls something that is not a plain function name."
            )
        name = node.func.id
        if name not in _FUNCTIONS:
            raise ExpressionError(
                f"{name}() is not available in a design expression. Available: "
                f"{', '.join(sorted(_FUNCTIONS))}."
            )
        if node.keywords:
            raise ExpressionError(f"{name}() takes its arguments positionally.")
        low, high = _ARITY[name]
        if not low <= len(node.args) <= high:
            wanted = f"{low}" if low == high else f"{low} to {high}"
            raise ExpressionError(
                f"{name}() takes {wanted} argument(s); {len(node.args)} were given."
            )
        arguments = [self._visit(argument) for argument in node.args]
        result: Quantity = _FUNCTIONS[name](*arguments)
        return result


# -- the parameter graph ----------------------------------------------------


@dataclass(frozen=True)
class Parameter:
    """One named design decision, or one consequence of others.

    Exactly one of `value` and `expression` is set. A parameter with a value is
    a decision someone made; a parameter with an expression is a consequence,
    and the difference is worth being able to see at a glance in a diff.
    """

    name: str
    unit: Unit = Unit.NONE
    value: float | None = None
    expression: str | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.isidentifier() or self.name != self.name.lower():
            raise ParameterError(
                f"{self.name!r} is not a usable parameter name. Names are lowercase "
                "identifiers — 'blank_thickness_mm', not 'Blank Thickness'. Expressions "
                "read them directly, so a name with a space or a dot in it could not be "
                "referred to at all."
            )
        if self.name in _FUNCTIONS or self.name in _CONSTANTS:
            raise ParameterError(
                f"{self.name!r} is the name of a built-in available inside expressions, "
                "so a formula could not tell the two apart. Rename the parameter."
            )
        if (self.value is None) == (self.expression is None):
            raise ParameterError(
                f"{self.name}: give it either a value or an expression, not both and not "
                "neither. A value is a decision; an expression is a consequence."
            )

    @property
    def is_derived(self) -> bool:
        return self.expression is not None


@dataclass(frozen=True)
class ResolvedParameters:
    """Every parameter's value, plus how it was arrived at.

    `order` is kept because it is what a rebuild replays and what a diff reads:
    knowing that `press_force_n` resolved after `blank_thickness_mm` is what
    lets B4 say which parameters a change reaches.
    """

    values: Mapping[str, Quantity]
    order: tuple[str, ...]
    depends_on: Mapping[str, tuple[str, ...]]

    def __getitem__(self, name: str) -> Quantity:
        try:
            return self.values[name]
        except KeyError:
            known = ", ".join(sorted(self.values)) or "none"
            raise ParameterError(f"No parameter called {name!r}. Declared: {known}.") from None

    def number(self, name: str) -> float:
        return self[name].value

    def dependents_of(self, name: str) -> tuple[str, ...]:
        """Every parameter that reads `name`, directly or through others.

        This is the parameter half of B4's impact analysis: change one number
        and this is the set that moves with it.
        """
        reached: set[str] = set()
        frontier = [name]
        while frontier:
            current = frontier.pop()
            for candidate, sources in self.depends_on.items():
                if current in sources and candidate not in reached:
                    reached.add(candidate)
                    frontier.append(candidate)
        return tuple(sorted(reached))

    def as_numbers(self) -> dict[str, float]:
        """Plain floats, for the places that only want the number."""
        return {name: quantity.value for name, quantity in self.values.items()}


@dataclass(frozen=True)
class ParameterSet:
    """The declared parameters of one design, in declaration order."""

    parameters: tuple[Parameter, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, parameters: Iterable[Parameter]) -> ParameterSet:
        collected = tuple(parameters)
        seen: dict[str, int] = {}
        for index, parameter in enumerate(collected):
            if parameter.name in seen:
                raise ParameterError(
                    f"{parameter.name!r} is declared twice (positions {seen[parameter.name]} "
                    f"and {index}). One of the two is silently ignored today; say which "
                    "one you meant."
                )
            seen[parameter.name] = index
        return cls(parameters=collected)

    def __iter__(self) -> Iterator[Parameter]:
        return iter(self.parameters)

    def __len__(self) -> int:
        return len(self.parameters)

    def names(self) -> tuple[str, ...]:
        return tuple(parameter.name for parameter in self.parameters)

    def resolve(self) -> ResolvedParameters:
        """Order the graph and evaluate it, in one pass, with CATIA untouched."""
        by_name = {parameter.name: parameter for parameter in self.parameters}
        edges = self._edges(by_name)
        order = self._topological(edges)

        values: dict[str, Quantity] = {}
        for name in order:
            parameter = by_name[name]
            if parameter.expression is None:
                assert parameter.value is not None  # noqa: S101 - guarded in __post_init__
                values[name] = Quantity.of(parameter.value, parameter.unit)
                continue
            computed = evaluate(parameter.expression, values)
            declared = UNIT_DIMENSIONS[parameter.unit]
            if computed.dimension != declared:
                raise UnitError(
                    f"{name} is declared in {parameter.unit.value or 'plain numbers'} but "
                    f"its expression {parameter.expression!r} computes {computed.dimension}. "
                    "Either the declaration or the formula is wrong; nothing here converts "
                    "between them."
                )
            values[name] = Quantity(computed.value, declared)

        return ResolvedParameters(
            values=values,
            order=order,
            depends_on={name: edges[name] for name in order},
        )

    # -- graph ---------------------------------------------------------------

    def _edges(self, by_name: Mapping[str, Parameter]) -> dict[str, tuple[str, ...]]:
        edges: dict[str, tuple[str, ...]] = {}
        for parameter in self.parameters:
            if parameter.expression is None:
                edges[parameter.name] = ()
                continue
            needed = dependencies(parameter.expression)
            missing = [name for name in needed if name not in by_name]
            if missing:
                known = ", ".join(sorted(by_name)) or "none"
                raise ParameterError(
                    f"{parameter.name} reads {', '.join(repr(m) for m in missing)}, which "
                    f"{'are' if len(missing) > 1 else 'is'} not declared. Declared: {known}."
                )
            if parameter.name in needed:
                raise CycleError(
                    f"{parameter.name} is defined in terms of itself: "
                    f"{parameter.expression!r}."
                )
            edges[parameter.name] = needed
        return edges

    def _topological(self, edges: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
        """Declaration-order-stable topological sort, reporting a real cycle path.

        Kahn's algorithm would find *that* there is a cycle; a design with a
        loop in it needs to be told *which* loop, because the author's next
        move is to break exactly one of those edges. So this is a depth-first
        walk that keeps its stack, and the stack is the message.
        """
        order: list[str] = []
        state: dict[str, int] = {}  # 0 unvisited, 1 on the stack, 2 done
        # Iteration is over declaration order, and each node's dependencies are
        # already sorted, so the output is a pure function of the input.
        for root in self.names():
            if state.get(root, 0) == 2:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            path: list[str] = []
            while stack:
                name, index = stack.pop()
                if index == 0:
                    if state.get(name, 0) == 2:
                        continue
                    if state.get(name, 0) == 1:
                        loop = [*path[path.index(name) :], name]
                        raise CycleError(
                            "These parameters depend on each other in a loop: "
                            + " -> ".join(loop)
                            + ". Break one of those links — a design cannot resolve a "
                            "circular definition, and neither could a person."
                        )
                    state[name] = 1
                    path.append(name)
                needs = edges[name]
                if index < len(needs):
                    stack.append((name, index + 1))
                    stack.append((needs[index], 0))
                    continue
                state[name] = 2
                path.pop()
                order.append(name)
        return tuple(order)
