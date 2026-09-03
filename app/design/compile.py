"""Lowering a design specification into an ordered plan of CATIA operations.

This is the compiler half of the design IR. It takes a `DesignSpec` — declared
parameters, declared features, references by semantic name — and produces a
`Plan`: a flat, ordered, fully-resolved sequence of registry calls that builds
the part from nothing.

Three properties are what the rest of the roadmap leans on.

**It is deterministic.** The same spec produces the same plan, argument for
argument, on any machine. Feature order is the author's, parameter resolution
breaks ties on declaration order, and every dictionary that reaches the output
is emitted with sorted keys. `Plan.digest()` is therefore a real identity, which
is what a simulation result can be bound to (roadmap D11) and what "same spec ⇒
same geometry" can be checked against (I5).

**It fails at the spec, not at the seat.** An operation that does not exist, an
argument the operation does not take, a value outside the schema's bounds, a
reference to a feature that is not built yet, a formula that adds a length to an
angle — all of it is refused here, with CATIA untouched and with a message that
names the feature. The alternative, which is what happens today, is a COM error
thirty operations into a rebuild that says a feature failed and nothing about
why.

**Every element the design refers to is named by the design.** The 65
operations that take a `name` get the semantic name written in at creation. Most
of Part Design — pad, pocket, hole, fillet, shell — cannot be named on creation
at all, so the compiler follows those with a `catia_feature_rename`
(`CREATES_TREE_FEATURE` is the list, and the reasoning for its narrowness is
there). That is the whole of B2, and it is done with operations that already
ship and are already tested rather than by adding a `name` parameter to fifty
backend methods that nobody can validate against a real seat today.

Anything in neither group — a line drawn inside a sketch, a constraint, an
update — is built and then reported as *unaddressable*, and a reference to it is
refused. Saying so is the point: emitting a rename for something that leaves no
row in the tree would move the failure back to the workstation, which is what
this compiler exists to stop.

**The one late-bound value.** A rename has to say *which* feature to rename, and
the only handle on a freshly-created pad is the name CATIA invented for it,
which is knowable at run time and not before. Predicting it (`Pad.1`, `Pad.2`)
is exactly the positional-naming fragility this package exists to remove. So a
plan may carry one symbolic value, `Created(feature)`, meaning "whatever the
call that created this feature reported". `bind()` resolves it against the
results collected so far. Everything else in a plan is a literal.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.catia.ops import registry
from app.catia.ops.spec import Operation, Tier
from app.catia.validation import SchemaError, validate
from app.design import spec as spec_module
from app.design.errors import (
    DesignReferenceError,
    FeatureError,
    PolicyError,
    SpecError,
    UnitError,
)
from app.design.names import NameTable, SemanticName
from app.design.params import (
    UNIT_DIMENSIONS,
    Dimension,
    Quantity,
    ResolvedParameters,
    Unit,
    evaluate,
)
from app.design.spec import DesignSpec, FeatureSpec

#: The key a late-bound "whatever CATIA called it" value serialises under.
CREATED_KEY: Final = "$created"

#: Operations a compiled design may not contain, and why. The reason is carried
#: rather than implied because an over-refusal has to be arguable: the agent's
#: recovery from a refusal is to try something else, and "no" without a reason
#: is how that becomes a wrongly built part.
#:
#: Destructive and server-only operations are refused from the registry itself
#: rather than listed here, so the list cannot fall behind a new one.
NOT_REPRODUCIBLE: Final[dict[str, str]] = {
    "catia_new_part": (
        "the compiler emits this itself, from the design's own name — a second one "
        "in the feature list would abandon everything built before it"
    ),
    "catia_open_document": (
        "a plan builds a part from nothing; opening an existing document makes what "
        "it produces depend on what was already on disk"
    ),
    "catia_checkpoint": (
        "checkpoints belong to a working session, not to the description of a part"
    ),
    "catia_list_commands": "it reads the seat's menus, which differ between installs",
    "catia_run_command": (
        "it drives the interface by menu label, which is localised and can be greyed "
        "out — a plan has to build the same part on a French seat and an English one"
    ),
    "catia_describe_dialog": "a dialog belongs to a person, not to a plan",
    "catia_fill_dialog": "a dialog belongs to a person, not to a plan",
    "catia_dialog_action": "a dialog belongs to a person, not to a plan",
    "catia_press_key": "a keystroke is not a description of a part",
    "catia_switch_workbench": (
        "the operations carry their own workbench; switching by hand makes the plan "
        "depend on what was open when it started"
    ),
}

#: Write-tier operations that create a top-level element in the specification
#: tree but take no `name` parameter, so the design's name has to be applied to
#: them straight afterwards with `catia_feature_rename`.
#:
#: **The criterion is narrow and the default is the safe one.** An operation is
#: in here only if it adds a row to the tree that CATIA lets you rename. Two
#: kinds of operation are deliberately absent and must stay absent:
#:
#: * the sketch-internal family (`catia_sketch_line`, `catia_sketch_rectangle`,
#:   `catia_sketch_constrain`, ...), which draws *inside* a sketch and produces
#:   no tree row of its own -- renaming what a rectangle "created" would either
#:   fail or rename the wrong thing;
#: * operations that change something rather than create it (`catia_update`,
#:   `catia_delete_feature`, `catia_feature_activate`, the constraint and
#:   dimension families, the assembly and drafting modifiers).
#:
#: A feature whose operation is in neither this set nor the 65 that take a
#: `name` is *unaddressable*: it is built, and nothing else in the design may
#: refer to it. That is reported plainly rather than papered over, because the
#: alternative -- emitting a rename and hoping -- fails at the workstation,
#: which is the failure this whole package exists to move earlier.
#:
#: `tests/test_design_compile.py` checks every entry still exists, is still
#: write-tier and still has no `name` parameter, so an upstream rename or a
#: `name` being added to one of these shows up here rather than at a seat.
CREATES_TREE_FEATURE: Final[frozenset[str]] = frozenset(
    {
        # Sketch-based solid features
        "catia_pad",
        "catia_pocket",
        "catia_shaft",
        "catia_groove",
        "catia_rib",
        "catia_slot",
        "catia_stiffener",
        "catia_multi_section_solid",
        "catia_pad_drafted_filleted",
        # Holes and threads
        "catia_hole",
        "catia_hole_at",
        "catia_hole_pattern",
        "catia_thread",
        # Dress-up
        "catia_fillet",
        "catia_fillet_edges",
        "catia_fillet_variable",
        "catia_fillet_face",
        "catia_fillet_tritangent",
        "catia_chamfer",
        "catia_draft",
        "catia_shell",
        "catia_shell_faces",
        "catia_thickness",
        "catia_remove_face",
        "catia_replace_face",
        # Bodies and booleans
        "catia_boolean",
        "catia_solid_combine",
        # Transformations
        "catia_translate",
        "catia_rotate",
        "catia_symmetry",
        "catia_mirror",
        "catia_scale",
        "catia_affinity",
        # Patterns
        "catia_pattern_rectangular",
        "catia_pattern_circular",
        "catia_pattern_user",
        # Surface to solid
        "catia_close_surface",
        "catia_thick_surface",
        "catia_sew_surface",
    }
)

def _rename_operation() -> Operation:
    """The operation the compiler emits itself, resolved once at import.

    Looked up rather than assumed: if `catia_feature_rename` were ever removed
    or renamed, B2 would quietly stop applying to every Part Design feature, and
    the first sign would be a design whose references no longer resolved.
    """
    operation = registry.get("catia_feature_rename")
    if operation is None:  # pragma: no cover - the registry test catches this first
        raise RuntimeError(
            "catia_feature_rename is gone from the registry. The design compiler needs "
            "it to give the design's own name to every Part Design feature CATIA will "
            "not let it name on creation."
        )
    return operation


_RENAME: Final[Operation] = _rename_operation()

#: Parameter-name suffix -> the dimension a value for it must have.
#:
#: The registry's numeric parameters are named by unit almost without exception
#: (98 end in `_mm`, 36 in `_deg`), which makes this a real check rather than a
#: heuristic: `length_mm: "=draft_deg * 2"` is caught here instead of building a
#: pad two degrees long. Suffixes that are not in this table are not checked —
#: a count or a ratio has no dimension to check against.
_SUFFIX_DIMENSIONS: Final[dict[str, Dimension]] = {
    "mm": UNIT_DIMENSIONS[Unit.MM],
    "mm2": UNIT_DIMENSIONS[Unit.MM2],
    "mm3": UNIT_DIMENSIONS[Unit.MM3],
    "deg": UNIT_DIMENSIONS[Unit.DEG],
}


@dataclass(frozen=True)
class Created:
    """Whatever CATIA called the element that this feature's call created.

    The only value in a plan that is not a literal. It exists because renaming
    a pad requires naming the pad, and a freshly built pad is called whatever
    CATIA decided — knowable when the call returns and not one moment sooner.
    """

    feature: str

    def to_dict(self) -> dict[str, str]:
        return {CREATED_KEY: self.feature}


@dataclass(frozen=True)
class PlannedCall:
    """One operation, with every argument resolved."""

    index: int
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    #: The semantic name of the feature this call belongs to, or None for the
    #: preamble (creating the document, setting the material).
    feature: str | None = None

    #: Why this call is here. Carried from the feature's `note`, or written by
    #: the compiler for the calls it emits itself.
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"index": self.index, "tool": self.tool}
        if self.feature is not None:
            out["feature"] = self.feature
        out["arguments"] = _canonical(self.arguments)
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class Plan:
    """An ordered, resolved, replayable build of one design."""

    design: str
    spec_digest: str
    calls: tuple[PlannedCall, ...]
    parameters: ResolvedParameters
    names: NameTable

    #: Features whose `when` evaluated false. Kept rather than dropped silently:
    #: "the lightening pocket is not there because the plate is under 6 mm" is an
    #: answer, and "there is no pocket" is a mystery.
    suppressed: tuple[str, ...] = ()

    #: Features that were built but that nothing else in the design can refer
    #: to, because their operation neither takes a `name` nor leaves a tree row
    #: that could be renamed into one. Semantic name -> the operation that built
    #: it. Reported so the gap is visible; `@` references to these are refused.
    unaddressable: Mapping[str, str] = field(default_factory=dict)

    def __iter__(self) -> Any:
        return iter(self.calls)

    def __len__(self) -> int:
        return len(self.calls)

    def tools(self) -> tuple[str, ...]:
        return tuple(call.tool for call in self.calls)

    def calls_for(self, feature: str) -> tuple[PlannedCall, ...]:
        return tuple(call for call in self.calls if call.feature == feature)

    def catia_name(self, feature: str) -> str:
        """What `feature` will be called in CATIA once this plan has run."""
        return self.names.catia_name(feature)

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "spec_digest": self.spec_digest,
            "calls": [call.to_dict() for call in self.calls],
            "suppressed": list(self.suppressed),
            "unaddressable": dict(sorted(self.unaddressable.items())),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    def digest(self) -> str:
        """Identity of the *plan*, distinct from the identity of the spec.

        Two different specs can compile to the same plan — rename a parameter
        that only ever appears inside one formula and the geometry is identical.
        The spec digest answers "is this the same design?"; this answers "does
        this build the same thing?", which is the question a cached result and a
        determinism check both actually ask.

        So `spec_digest` is deliberately **excluded** from what is hashed, even
        though `to_dict` carries it. Leaving it in would make this digest a
        slower spelling of the spec's, and the second question would have no
        answer.
        """
        buildable = {key: value for key, value in self.to_dict().items() if key != "spec_digest"}
        canonical = json.dumps(
            buildable, ensure_ascii=False, separators=(",", ":"), sort_keys=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -- compilation ------------------------------------------------------------


def compile_spec(design: DesignSpec, *, create_document: bool = True) -> Plan:
    """Compile a design into the plan that builds it.

    `create_document=False` compiles the features alone, for the case where the
    document already exists and is known to be empty — a partial regeneration,
    or a test that only cares about the feature calls. It is not a way to append
    to a part built by something else: a plan is only reproducible if it starts
    from nothing.
    """
    resolved = design.parameters.resolve()
    names = NameTable()
    calls: list[PlannedCall] = []
    suppressed: list[str] = []
    unaddressable: dict[str, str] = {}
    declared = {feature.name for feature in design.features}

    if create_document:
        calls.append(
            PlannedCall(
                index=len(calls),
                tool="catia_new_part",
                arguments={"name": design.name},
                note="Start the document this design describes.",
            )
        )
        if design.material is not None:
            calls.append(
                PlannedCall(
                    index=len(calls),
                    tool="catia_set_material",
                    arguments={"material": design.material},
                    note="Set the material before anything is weighed or simulated.",
                )
            )

    for feature in design.features:
        operation = _operation_for(feature)
        _check_policy(feature, operation)

        if not _is_built(feature, resolved):
            suppressed.append(feature.name)
            continue

        takes_a_name = any(param.name == "name" for param in operation.params)
        addressable = takes_a_name or feature.op in CREATES_TREE_FEATURE
        if addressable:
            catia_name = names.allocate(feature.semantic_name)
        else:
            # Recorded *before* its own arguments are resolved, but that is
            # harmless — a feature cannot refer to itself — and it has to happen
            # before the next feature is compiled, which is what makes a
            # reference to it fail with the reason rather than with a name the
            # design has no way to make CATIA use.
            unaddressable[feature.name] = feature.op

        arguments = _resolve_arguments(
            feature, operation, resolved, names, declared, suppressed, unaddressable
        )

        if takes_a_name and "name" not in arguments:
            arguments["name"] = catia_name

        _validate(feature, operation, arguments)
        calls.append(
            PlannedCall(
                index=len(calls),
                tool=feature.op,
                arguments=arguments,
                feature=feature.name,
                note=feature.note,
            )
        )

        if addressable and not takes_a_name:
            # B2 for the operations that cannot carry a name: build it, then
            # immediately give it the design's name, so every later reference
            # resolves to a literal string rather than to a position in a tree.
            rename = {"feature": Created(feature.name), "name": catia_name}
            # Validated like any other call, even though the compiler wrote it.
            # It is the one call whose arguments no author reviewed, and the
            # only one carrying a late-bound value, so it is the last place that
            # should be taken on trust — and validating it is what keeps
            # `catia_feature_rename`'s schema and this emission in step.
            _validate(feature, _RENAME, rename)
            calls.append(
                PlannedCall(
                    index=len(calls),
                    tool=_RENAME.name,
                    arguments=rename,
                    feature=feature.name,
                    note=(
                        f"{feature.op} cannot be named on creation, so the design's "
                        "name is applied straight afterwards."
                    ),
                )
            )

    return Plan(
        design=design.name,
        spec_digest=design.digest(),
        calls=tuple(calls),
        parameters=resolved,
        names=names,
        suppressed=tuple(suppressed),
        unaddressable=dict(sorted(unaddressable.items())),
    )


def _operation_for(feature: FeatureSpec) -> Operation:
    operation = registry.get(feature.op)
    if operation is not None:
        return operation
    near = difflib.get_close_matches(feature.op, list(registry.OPERATIONS_BY_NAME), n=3, cutoff=0.6)
    hint = f" Did you mean {', '.join(near)}?" if near else ""
    raise FeatureError(
        f"{feature.name}: there is no operation called {feature.op!r}.{hint}"
    )


def _check_policy(feature: FeatureSpec, operation: Operation) -> None:
    """Refuse the operations a reproducible build has no business containing."""
    if operation.server_only:
        raise PolicyError(
            f"{feature.name}: {operation.name} is answered by the server and never "
            "reaches a workstation, so it builds nothing. Remove it from the design."
        )
    if operation.tier is Tier.DESTRUCTIVE:
        raise PolicyError(
            f"{feature.name}: {operation.name} is destructive, and a design is a "
            "description of a part that can be rebuilt by replaying it. Something that "
            "cannot be undone by restoring a checkpoint has no place in a replay."
        )
    reason = NOT_REPRODUCIBLE.get(operation.name)
    if reason is not None:
        raise PolicyError(f"{feature.name}: {operation.name} cannot appear in a design — {reason}.")


def _is_built(feature: FeatureSpec, resolved: ResolvedParameters) -> bool:
    """Evaluate a feature's `when` gate. Absent means built."""
    if feature.when is None:
        return True
    condition = evaluate(feature.when, resolved.values)
    if not condition.dimension.is_dimensionless:
        raise UnitError(
            f"{feature.name}: its `when` computes {condition.dimension}, which is a "
            "quantity rather than a condition. Compare it against something — "
            "'plate_mm >= 6' rather than 'plate_mm'."
        )
    return bool(condition.value)


def _resolve_arguments(
    feature: FeatureSpec,
    operation: Operation,
    resolved: ResolvedParameters,
    names: NameTable,
    declared: set[str],
    suppressed: Sequence[str],
    unaddressable: Mapping[str, str],
) -> dict[str, Any]:
    """Turn expressions into numbers and references into CATIA names."""
    known = {param.name for param in operation.params if not param.supplied_by_server}
    out: dict[str, Any] = {}
    for key in sorted(feature.args):
        if key not in known:
            near = difflib.get_close_matches(key, sorted(known), n=3, cutoff=0.6)
            hint = f" Did you mean {', '.join(near)}?" if near else ""
            accepted = ", ".join(sorted(known)) or "none"
            raise FeatureError(
                f"{feature.name}: {operation.name} takes no argument called {key!r}."
                f"{hint} It accepts: {accepted}."
            )
        out[key] = _resolve_value(
            feature.args[key],
            feature=feature,
            path=key,
            expected=_SUFFIX_DIMENSIONS.get(key.rsplit("_", 1)[-1]),
            resolved=resolved,
            names=names,
            declared=declared,
            suppressed=suppressed,
            unaddressable=unaddressable,
        )
    return out


def _resolve_value(
    value: Any,
    *,
    feature: FeatureSpec,
    path: str,
    expected: Dimension | None,
    resolved: ResolvedParameters,
    names: NameTable,
    declared: set[str],
    suppressed: Sequence[str],
    unaddressable: Mapping[str, str],
) -> Any:
    """One argument value, recursively. Lists and objects are walked."""
    if isinstance(value, list):
        # Nested values are not dimension-checked: the element of a `point3` is
        # a millimetre but its name is an index, so there is no suffix to read.
        return [
            _resolve_value(
                item,
                feature=feature,
                path=f"{path}[{index}]",
                expected=None,
                resolved=resolved,
                names=names,
                declared=declared,
                suppressed=suppressed,
                unaddressable=unaddressable,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        return {
            key: _resolve_value(
                item,
                feature=feature,
                path=f"{path}.{key}",
                expected=None,
                resolved=resolved,
                names=names,
                declared=declared,
                suppressed=suppressed,
                unaddressable=unaddressable,
            )
            for key, item in sorted(value.items())
        }
    if spec_module.is_reference(value):
        return _resolve_reference(
            spec_module.reference_target(value),
            feature=feature,
            path=path,
            names=names,
            declared=declared,
            suppressed=suppressed,
            unaddressable=unaddressable,
        )
    if spec_module.is_expression(value):
        return _resolve_expression(
            spec_module.expression_source(value),
            feature=feature,
            path=path,
            expected=expected,
            resolved=resolved,
        )
    return value


def _resolve_reference(
    target: str,
    *,
    feature: FeatureSpec,
    path: str,
    names: NameTable,
    declared: set[str],
    suppressed: Sequence[str],
    unaddressable: Mapping[str, str],
) -> str:
    """Resolve `@other.feature` to the name it will carry in CATIA."""
    try:
        SemanticName.parse(target)
    except SpecError as exc:
        raise DesignReferenceError(f"{feature.name}.{path}: {exc}") from None

    if target in names:
        return names.catia_name(target)
    if target in unaddressable:
        raise DesignReferenceError(
            f"{feature.name}.{path} refers to {target!r}, which is built by "
            f"{unaddressable[target]} -- an operation that neither takes a name nor "
            "leaves a renameable row in the tree, so the design has no handle on what "
            "it made. Refer to the feature it was applied to instead."
        )
    if target in suppressed:
        gate = "its `when` condition"
        raise DesignReferenceError(
            f"{feature.name}.{path} refers to {target!r}, which is in this design but was "
            f"not built — {gate} was false for these parameter values. Either gate this "
            "feature the same way, or change the condition."
        )
    if target in declared:
        raise DesignReferenceError(
            f"{feature.name}.{path} refers to {target!r}, which is declared later in this "
            "design. Features are built in the order they are written, so a reference has "
            f"to point backwards — move {target!r} above {feature.name!r}."
        )
    built = ", ".join(str(name) for name in names.names()) or "nothing yet"
    raise DesignReferenceError(
        f"{feature.name}.{path} refers to {target!r}, which is not a feature in this "
        f"design. Built so far: {built}."
    )


def _resolve_expression(
    source: str,
    *,
    feature: FeatureSpec,
    path: str,
    expected: Dimension | None,
    resolved: ResolvedParameters,
) -> float:
    """Evaluate `=formula` and check it against what the argument's name implies."""
    try:
        computed: Quantity = evaluate(source, resolved.values)
    except SpecError as exc:
        raise type(exc)(f"{feature.name}.{path}: {exc}") from None
    if expected is not None and computed.dimension != expected:
        raise UnitError(
            f"{feature.name}.{path} is a {expected} by its name, but {source!r} computes "
            f"{computed.dimension}. Nothing here converts units — one of the two is wrong."
        )
    return computed.value


def _validate(feature: FeatureSpec, operation: Operation, arguments: Mapping[str, Any]) -> None:
    """Check the resolved arguments against the operation's own schema.

    Deliberately the *same* validator the dispatcher runs, against the *same*
    schema, so a plan that compiles is a plan whose calls will not be refused
    for shape. Writing a second, looser check here would move the failure back
    to the workstation, which is the entire thing this compiler exists to stop.

    Late-bound values are exempt: `Created` is a string by the time the call is
    made, and there is nothing here that could check it earlier.
    """
    checkable = {
        key: value for key, value in arguments.items() if not isinstance(value, Created)
    }
    schema = operation.json_schema()
    if len(checkable) != len(arguments):
        # Drop `required` for the late-bound keys rather than for all of them:
        # a missing *literal* argument must still be caught here.
        missing = set(arguments) - set(checkable)
        schema = {
            **schema,
            "required": [name for name in schema.get("required", []) if name not in missing],
        }
    try:
        validate(checkable, schema)
    except SchemaError as exc:
        raise FeatureError(f"{feature.name} ({operation.name}): {exc}.") from None


# -- execution-side helpers -------------------------------------------------


def bind(arguments: Mapping[str, Any], created: Mapping[str, str]) -> dict[str, Any]:
    """Resolve a call's late-bound values against what earlier calls reported.

    `created` maps a feature's semantic name to whatever CATIA called the
    element that feature's call produced — the `feature` key of the creating
    tool's result. An executor collects that as it goes and passes it here.

    Kept in the compiler rather than in an executor because it is the other half
    of `Created`, and splitting a two-sided contract across two modules is how
    the two sides drift.
    """
    out: dict[str, Any] = {}
    for key, value in arguments.items():
        if not isinstance(value, Created):
            out[key] = value
            continue
        try:
            out[key] = created[value.feature]
        except KeyError:
            raise DesignReferenceError(
                f"{key} needs the CATIA name of {value.feature!r}, but no result has been "
                "recorded for it. A late-bound name can only be resolved after the call "
                "that creates it has run and reported its `feature`."
            ) from None
    return out


def _canonical(value: Any) -> Any:
    """Serialisable form with every mapping key sorted, for a stable digest."""
    if isinstance(value, Created):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value
