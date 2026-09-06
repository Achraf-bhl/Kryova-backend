"""Dimensions read out of the design, not measured off the solid.

**This is the leverage.** Every CAD system can put a dimension on a drawing by
measuring the model: it finds two faces, works out that they are 12.000 mm apart,
and writes 12. What it cannot say is *why* — whether 12 is a decision somebody
made, a consequence of two other decisions, or a number that happened to fall out
of a boolean. `app/design/` knows: a pad's length is `=wall_mm * 2`, `wall_mm` is
a declared parameter with a value and a description, and if it moves the part
moves with it. A dimension traced to its parameter is worth ten inferred from a
bounding box, because the traced one survives the next edit and the inferred one
silently stops describing the part.

So this module walks the `DesignSpec` — the authored form, with its expressions
intact — beside the compiled `Plan`, which holds the same arguments resolved to
literals. The spec says where the number came from; the plan says what it is.

**What is a dimension, and what is merely a number.** The rule is this codebase's
own argument-naming convention, which is real and enforced across
`app/catia/ops/`: an argument ending `_mm` is a length, one ending `_deg` is an
angle, and a name containing `diameter` or `radius` says which symbol the length
is written with. Every *other* numeric argument — a pattern count, an instance
index — is not a dimension, and is reported as `non_dimensional` rather than
dropped, because a report that does not mention something and a report that
decided it did not matter read identically.

**What this does NOT dimension, stated plainly rather than papered over:**

* Positions. `catia_hole`'s `position` is one of five named spots on a face, not
  a coordinate, so there is nothing to dimension *from*. A bolt circle placed by
  `catia_hole_at` carries real coordinates and they are traced — but this build
  places no positional dimension chain on a view, so they arrive `tabled`.
* Anything with no number in the design at all: a pocket cut `through_all`, a pad
  run `up_to_next`. These are correct modelling and they have no dimension
  because the design deliberately did not state one; the depth is a consequence
  of the geometry it stops against. They appear in no bucket, which is right —
  they are not missing dimensions, they are absent ones.
* Tolerances, GD&T, surface finish, datums. None of it exists in the design IR
  yet, so none of it is invented here. The drawing says so in words.
* Anything at all when the design is a build log rather than a spec. A part
  assembled call by call in conversation has no `DesignSpec`; `from_plan` traces
  what a bare `Plan` can support, which is the argument values but not the
  parameter names, so every dimension comes back untraced and the report says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.design.compile import Plan, compile_spec
from app.design.params import dependencies
from app.design.spec import DesignSpec, expression_source, is_expression
from app.manufacture.drawing import DimensionKind, TracedDimension

#: Argument-name suffixes that make a number a dimension, and what kind.
#: Ordered: the longest, most specific suffix must be tested first, or
#: `diameter_mm` matches the bare `_mm` rule and loses its Ø.
_SUFFIX_KINDS: tuple[tuple[str, DimensionKind], ...] = (
    ("diameter_mm", DimensionKind.DIAMETER),
    ("radius_mm", DimensionKind.RADIUS),
    ("_mm", DimensionKind.LINEAR),
    ("_deg", DimensionKind.ANGULAR),
)

#: The unit each kind is written in. Not a conversion table — the codebase is
#: mm-N-MPa throughout and nothing converts (CLAUDE.md); this only records which
#: of the two units a given dimension already is.
_KIND_UNITS: Mapping[DimensionKind, str] = {
    DimensionKind.LINEAR: "mm",
    DimensionKind.DIAMETER: "mm",
    DimensionKind.RADIUS: "mm",
    DimensionKind.ANGULAR: "deg",
}


def classify_argument(name: str) -> DimensionKind | None:
    """What kind of dimension an argument name denotes, or `None` for a bare number.

    Name-based and not value-based on purpose. A value cannot tell a radius from
    a length — both are 5.0 — and guessing from the surrounding operation would
    put a Ø on a chamfer the day somebody adds `catia_chamfer(diameter_mm=...)`.
    The suffix convention is written down in `app/catia/ops/` and is checked by
    that package's own tests, so reading it here is reuse rather than a second
    source of truth.
    """
    lowered = name.lower()
    for suffix, kind in _SUFFIX_KINDS:
        if lowered.endswith(suffix):
            return kind
    return None


def _is_number(value: Any) -> bool:
    """Is this argument a number?

    `bool` is excluded explicitly. `isinstance(True, int)` is true in Python, so
    without this a `through_all: true` becomes a 1 mm dimension — a plausible
    number on a drawing, attached to a feature that has no depth at all.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def trace_dimensions(
    spec: DesignSpec, plan: Plan | None = None
) -> tuple[tuple[TracedDimension, ...], tuple[str, ...]]:
    """Every dimension the design states, and the features that were suppressed.

    Returns `(dimensions, suppressed)`. The suppressed names come back separately
    rather than as dimensions with a flag: a suppressed feature is not on the
    part, so dimensioning it would put a number on a drawing for something that
    is not there — the single worst thing a drawing can do.

    The plan is compiled if not supplied. Compiling is cheap, deterministic and
    offline (`app/design/` touches nothing outside itself), and it is the only
    thing that can resolve `=wall_mm * 2` to a literal — reimplementing that
    resolution here would be a second answer to a question the compiler already
    answers, and the two would drift.
    """
    resolved_plan = plan if plan is not None else compile_spec(spec)
    suppressed = set(resolved_plan.suppressed)
    declared = set(spec.parameters.names())

    found: list[TracedDimension] = []
    for feature in spec.features:
        if feature.name in suppressed:
            continue
        built = _arguments_as_built(resolved_plan, feature.name, feature.op)
        for argument in sorted(feature.args):
            found.extend(
                _trace_one(
                    feature_name=feature.name,
                    op=feature.op,
                    argument=argument,
                    authored=feature.args[argument],
                    built=built.get(argument),
                    declared=declared,
                    note=feature.note,
                )
            )
    return tuple(found), tuple(sorted(suppressed))


def from_plan(plan: Plan) -> tuple[TracedDimension, ...]:
    """Dimensions from a plan alone, all of them untraced, and honestly so.

    The path for a part built call by call in a conversation rather than compiled
    from a spec: there is no authored expression to read, so every number is a
    literal with nothing behind it. That is a true statement about such a part —
    it has no parameters — and the report saying "seven numbers, none traceable
    to a design parameter" is the accurate description of what the shop is being
    handed.
    """
    found: list[TracedDimension] = []
    for call in plan.calls:
        for argument in sorted(call.arguments):
            found.extend(
                _trace_one(
                    feature_name=call.feature or call.tool,
                    op=call.tool,
                    argument=argument,
                    authored=call.arguments[argument],
                    built=call.arguments[argument],
                    declared=set(),
                    note=call.note,
                )
            )
    return tuple(found)


def _arguments_as_built(plan: Plan, feature: str, op: str) -> Mapping[str, Any]:
    """The resolved arguments of the call that *is* this feature.

    A feature can compile to more than one call — the compiler emits a rename
    after anything that creates a tree element — so the arguments are taken from
    the call whose tool is the feature's own operation. Taking the first call
    would dimension a rename, whose only argument is a name.
    """
    for call in plan.calls_for(feature):
        if call.tool == op:
            return call.arguments
    return {}


def _trace_one(
    *,
    feature_name: str,
    op: str,
    argument: str,
    authored: Any,
    built: Any,
    declared: set[str],
    note: str,
) -> list[TracedDimension]:
    """One authored argument as zero, one or several traced dimensions.

    Several, because a per-entity argument is a list: `catia_fillet` takes
    `radius_mm` as one radius or as one per selected edge, and "the four vertical
    corners at 2, 3, 4 and 5 mm" is four dimensions, not one. Each gets its own
    index in the argument name so the table names which edge it belongs to.
    """
    kind = classify_argument(argument)
    values = built if built is not None else authored

    if isinstance(values, (list, tuple)):
        numbers = [(f"{argument}[{index}]", one) for index, one in enumerate(values)]
    else:
        numbers = [(argument, values)]

    parameters, expression = _provenance(authored, declared)
    traced: list[TracedDimension] = []
    for name, value in numbers:
        if not _is_number(value):
            continue
        traced.append(
            TracedDimension(
                feature=feature_name,
                op=op,
                argument=name,
                # A number on an argument with no dimensional suffix is still
                # reported, as LINEAR with the report bucketing it as
                # non-dimensional. Giving it a kind here and letting the bucket
                # decide keeps one code path; inventing a NONE kind would put a
                # meaningless symbol on the table row.
                kind=kind or DimensionKind.LINEAR,
                value=float(value),
                unit=_KIND_UNITS.get(kind, "") if kind else "",
                parameters=parameters,
                expression=expression,
                note=note,
            )
        )
    return traced


def _provenance(authored: Any, declared: set[str]) -> tuple[tuple[str, ...], str | None]:
    """Which declared parameters stand behind an authored argument, and its formula.

    `dependencies` is the compiler's own expression parser, so a formula this
    package cannot read is a formula the compiler could not have compiled either
    — there is no way for the two to disagree about what a expression reads.

    Names that are not declared parameters are dropped rather than reported: an
    expression can call `max` or `sqrt`, and listing a built-in as a design
    parameter would put a row in the manifest claiming a decision nobody made.
    """
    if not is_expression(authored):
        return (), None
    source = expression_source(authored)
    try:
        reads = dependencies(source)
    except Exception:  # noqa: BLE001 - an unparseable expression traces to nothing
        return (), source
    return tuple(sorted(name for name in reads if name in declared)), source


def is_dimensional(traced: TracedDimension) -> bool:
    """Is this number a dimension, or merely a number the design happened to carry?

    The single predicate the report buckets on, in the module that owns the
    classification, so no consumer re-derives "does `_mm` make it a dimension"
    from the docstring.
    """
    return bool(traced.unit)


__all__ = [
    "classify_argument",
    "from_plan",
    "is_dimensional",
    "trace_dimensions",
]
