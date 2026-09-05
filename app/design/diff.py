"""What changed between two versions of a design, and what that reaches.

B4 in the roadmap: *"changing this parameter rebuilds these 40 features and
invalidates these 3 simulations."* Without it every edit is a full rebuild and a
full re-validation, which at machine scale is the difference between a design
that can be revised and one that can only be re-made.

**The comparison is made after compilation, not before.** This is the decision
the module turns on. Comparing two specs textually would answer a weaker
question — `wall_mm` changed from 4 to 5 — and then need a rule, guess or
heuristic to get from there to which features moved. Compiling both first means
every parameter is already a number in the argument list of the calls that use
it, so a feature is affected if and only if its resolved calls differ. A
parameter nothing reads changes nothing and says so; a parameter buried three
formulas deep in one pad's length reaches exactly that pad. No graph of
parameter usage has to be maintained, and none can fall out of step with what
the compiler actually emits.

**References are the half that compilation does not answer.** A pad that
extrudes `@plate.profile` has identical arguments whether or not that sketch
moved: the argument is the *name*, and the name is stable — that stability is
the entire point of Layer B. So the geometry of a feature can change while its
call does not, and the reference graph is what carries that. `_downstream` walks
it, so a changed sketch marks the pad that consumes it, and the fillet on the
pad, and so on to the end of the chain.

The two halves are reported separately (`changed_calls` and `downstream`)
because the recoveries differ. A feature in the first was edited, directly or
through a parameter. A feature in the second was not touched at all and may
still come out a different shape — which is the one an engineer is most likely
to be surprised by, and therefore the one worth naming.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.design import spec as spec_module
from app.design.compile import Plan, compile_spec
from app.design.params import Parameter
from app.design.spec import DesignSpec, FeatureSpec


@dataclass(frozen=True)
class ParameterChange:
    """One parameter that is not what it was.

    Both sides are carried, and so is the distinction between a decision (a
    value) and a consequence (an expression), because a diff that says
    `wall_mm: 4 -> 5` and a diff that says `wall_mm: 4 -> =plate_mm / 2` are
    different events: the second changed the *shape of the design*, not a number
    in it, and it is the one that can start moving on its own afterwards.
    """

    name: str
    before: str | None
    after: str | None

    @property
    def added(self) -> bool:
        return self.before is None

    @property
    def removed(self) -> bool:
        return self.after is None

    def __str__(self) -> str:
        if self.added:
            return f"{self.name}: added as {self.after}"
        if self.removed:
            return f"{self.name}: removed (was {self.before})"
        return f"{self.name}: {self.before} -> {self.after}"


@dataclass(frozen=True)
class FeatureChange:
    """One feature that was added, removed, or built differently."""

    name: str
    kind: str  # "added" | "removed" | "changed" | "suppressed" | "unsuppressed"
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.name}: {self.kind}{f' — {self.detail}' if self.detail else ''}"


@dataclass(frozen=True)
class SpecDiff:
    """Everything that differs between two designs, and everything it reaches.

    Falsey when the two designs build the same part, which is the check a
    regeneration wants first: if nothing is affected there is nothing to rebuild
    and nothing to re-validate, and saying so costs one comparison instead of a
    round trip to a workstation.
    """

    design: str
    before_digest: str
    after_digest: str
    before_plan_digest: str
    after_plan_digest: str

    parameters: tuple[ParameterChange, ...] = ()
    features: tuple[FeatureChange, ...] = ()
    material: ParameterChange | None = None

    #: Features whose compiled calls are not what they were — edited directly,
    #: or reached by a parameter that was.
    changed_calls: tuple[str, ...] = ()

    #: Features whose own calls are identical but which stand on something in
    #: `changed_calls`, directly or transitively. Not edited; may still come out
    #: a different shape.
    downstream: tuple[str, ...] = ()

    #: Does this build a different part at all?
    #:
    #: Computed by `builds_the_same`, **not** by comparing the two plan digests.
    #: A plan carries each feature's `note` — the rationale slot — and that is
    #: part of the design record on purpose, so rewriting a comment moves both
    #: digests while building a byte-identical part. A rebuild triggered by that
    #: is a wasted rebuild, and a no-progress check fooled by it is a loop that
    #: keeps going on a repair that did nothing.
    plan_changed: bool = False

    @property
    def affected(self) -> tuple[str, ...]:
        """Every feature whose geometry may differ, in build order."""
        return tuple(dict.fromkeys(self.changed_calls + self.downstream))

    def __bool__(self) -> bool:
        return self.plan_changed

    def affects(self, feature: str) -> bool:
        return feature in self.affected

    def invalidates(self, depended_on: Iterable[str]) -> bool:
        """Would a result computed from `depended_on` have to be recomputed?

        The question a stored simulation, a measurement or a passing assertion
        asks of a new revision. A result that read the mass of the whole part
        depends on every feature; one that measured a single bore depends on
        that bore and whatever it was cut into.

        An empty dependency list means "this depended on the part as a whole",
        and is invalidated by any change at all — the conservative reading, on
        the grounds that a stale number presented with confidence is the failure
        mode this exists to prevent.
        """
        wanted = tuple(depended_on)
        if not wanted:
            return self.plan_changed
        affected = set(self.affected)
        return any(name in affected for name in wanted)

    def what_changed(self) -> str:
        """The edit itself, with no design name in front of it.

        Separate from `summary` because a caller that is already talking about
        this design — a correction loop reporting what it did — would otherwise
        print the name twice in one sentence.
        """
        parts: list[str] = []
        if self.parameters:
            parts.append(
                f"{len(self.parameters)} parameter(s): "
                + "; ".join(str(change) for change in self.parameters)
            )
        if self.material is not None:
            parts.append(f"material {self.material.before} -> {self.material.after}")
        if self.features:
            parts.append(
                f"{len(self.features)} feature(s): "
                + "; ".join(str(change) for change in self.features)
            )
        return ". ".join(parts)

    def rebuild_sentence(self) -> str:
        """How much of the part this reaches."""
        tail = f"Rebuilds {len(self.affected)} feature(s)"
        if self.downstream:
            tail += (
                f", of which {len(self.downstream)} were not edited but stand on "
                f"something that was ({', '.join(self.downstream)})"
            )
        return tail + "."

    def summary(self) -> str:
        """One paragraph, in the register the rest of the codebase answers in."""
        if not self.plan_changed:
            return f"{self.design}: nothing that affects the built part changed."
        return f"{self.design}: {self.what_changed()}. {self.rebuild_sentence()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "plan_changed": self.plan_changed,
            "parameters": [
                {"name": c.name, "before": c.before, "after": c.after} for c in self.parameters
            ],
            "material": (
                None
                if self.material is None
                else {"before": self.material.before, "after": self.material.after}
            ),
            "features": [
                {"name": c.name, "kind": c.kind, "detail": c.detail} for c in self.features
            ],
            "changed_calls": list(self.changed_calls),
            "downstream": list(self.downstream),
            "affected": list(self.affected),
        }


def diff_specs(before: DesignSpec, after: DesignSpec) -> SpecDiff:
    """Compare two revisions of a design and work out what the change reaches.

    Both are compiled, so both must compile: a diff against a spec that does not
    build is not a diff, it is a compile error wearing one. The error is raised
    as-is rather than wrapped, because it already names the feature and says
    what to do.
    """
    before_plan = compile_spec(before)
    after_plan = compile_spec(after)
    return diff_plans(before, after, before_plan, after_plan)


def diff_plans(
    before: DesignSpec,
    after: DesignSpec,
    before_plan: Plan,
    after_plan: Plan,
) -> SpecDiff:
    """The comparison itself, when both plans are already compiled.

    Split out because a regeneration loop has usually just compiled the new plan
    in order to run it, and compiling it a second time to diff it would be pure
    waste — the compiler is deterministic, so the plan in hand is the plan this
    would produce.
    """
    parameters = _parameter_changes(before, after)
    material = _material_change(before, after)
    features = _feature_changes(before, after, before_plan, after_plan)
    changed = _changed_calls(before, after, before_plan, after_plan)
    downstream = _downstream(after, changed, after_plan)

    return SpecDiff(
        design=after.name,
        before_digest=before.digest(),
        after_digest=after.digest(),
        before_plan_digest=before_plan.digest(),
        after_plan_digest=after_plan.digest(),
        parameters=parameters,
        features=features,
        material=material,
        changed_calls=changed,
        downstream=downstream,
        plan_changed=not builds_the_same(before_plan, after_plan),
    )


def builds_the_same(before: Plan, after: Plan) -> bool:
    """Would these two plans produce the same part?

    The comparison the whole module rests on, and deliberately *not*
    `before.digest() == after.digest()`. A plan carries each call's `note`,
    because rationale travels with the design — so two plans that differ only in
    a rewritten comment have different digests and build the same geometry.
    Every consumer here is asking about the geometry: whether to rebuild,
    whether a stored result went stale, whether a proposed repair was a no-op.

    `Plan.digest()` is left answering the question it already answers, which
    covers the note on purpose and is what a provenance record wants.
    """
    return _buildable(before) == _buildable(after)


def _buildable(plan: Plan) -> list[dict[str, Any]]:
    """A plan reduced to what determines the geometry: the calls and their arguments."""
    return _comparable([call.to_dict() for call in plan.calls])


# -- parameters --------------------------------------------------------------


def _describe(parameter: Parameter) -> str:
    if parameter.is_derived:
        return f"={parameter.expression}"
    unit = f" {parameter.unit.value}" if parameter.unit.value else ""
    return f"{parameter.value:g}{unit}"


def _parameter_changes(before: DesignSpec, after: DesignSpec) -> tuple[ParameterChange, ...]:
    old = {p.name: p for p in before.parameters}
    new = {p.name: p for p in after.parameters}
    changes: list[ParameterChange] = []
    for name in sorted(set(old) | set(new)):
        was = old.get(name)
        now = new.get(name)
        if was is not None and now is not None:
            # Unit is part of the identity of a value here: nothing in this
            # codebase converts, so 5 mm becoming 5 deg is a real change even
            # though the number did not move.
            if _describe(was) == _describe(now):
                continue
        changes.append(
            ParameterChange(
                name=name,
                before=None if was is None else _describe(was),
                after=None if now is None else _describe(now),
            )
        )
    return tuple(changes)


def _material_change(before: DesignSpec, after: DesignSpec) -> ParameterChange | None:
    if before.material == after.material:
        return None
    return ParameterChange(name="material", before=before.material, after=after.material)


# -- features ----------------------------------------------------------------


def _feature_changes(
    before: DesignSpec,
    after: DesignSpec,
    before_plan: Plan,
    after_plan: Plan,
) -> tuple[FeatureChange, ...]:
    old = {feature.name: feature for feature in before.features}
    new = {feature.name: feature for feature in after.features}
    was_suppressed = set(before_plan.suppressed)
    is_suppressed = set(after_plan.suppressed)

    changes: list[FeatureChange] = []
    # Report in the new design's order where possible: a diff is read next to
    # the spec, and jumping around it to follow one is how a reader loses the
    # thread. Removed features have no place in that order, so they follow.
    for feature in after.features:
        name = feature.name
        if name not in old:
            changes.append(FeatureChange(name=name, kind="added", detail=feature.op))
            continue
        if name in was_suppressed and name not in is_suppressed:
            changes.append(
                FeatureChange(
                    name=name,
                    kind="unsuppressed",
                    detail=f"`when` is now true ({feature.when})",
                )
            )
            continue
        if name not in was_suppressed and name in is_suppressed:
            changes.append(
                FeatureChange(
                    name=name,
                    kind="suppressed",
                    detail=f"`when` is now false ({feature.when})",
                )
            )
            continue
        detail = _feature_detail(old[name], feature)
        if detail:
            changes.append(FeatureChange(name=name, kind="changed", detail=detail))

    for feature in before.features:
        if feature.name not in new:
            changes.append(FeatureChange(name=feature.name, kind="removed", detail=feature.op))

    return tuple(changes)


def _feature_detail(before: FeatureSpec, after: FeatureSpec) -> str:
    """What differs in the *written* feature, ignoring what only a rebuild shows.

    `note` is deliberately not compared. It is the rationale slot, and a design
    whose diff shouts because someone improved a comment is a diff people stop
    reading.
    """
    if before.op != after.op:
        return f"{before.op} -> {after.op}"
    if before.when != after.when:
        return f"`when` {before.when!r} -> {after.when!r}"
    old_args, new_args = dict(before.args), dict(after.args)
    touched = sorted(
        key for key in set(old_args) | set(new_args) if old_args.get(key) != new_args.get(key)
    )
    if touched:
        return "arguments " + ", ".join(touched)
    return ""


# -- impact ------------------------------------------------------------------


def _changed_calls(
    before: DesignSpec,
    after: DesignSpec,
    before_plan: Plan,
    after_plan: Plan,
) -> tuple[str, ...]:
    """Features whose compiled calls differ, in the new design's build order.

    This is where a parameter change becomes a feature list: by the time a plan
    exists, `length_mm: "=wall_mm * 3"` is the number 15, so the comparison is
    between two literal argument lists and needs to know nothing about formulas.
    """
    old_features = {feature.name for feature in before.features}
    changed: list[str] = []

    for feature in after.features:
        name = feature.name
        if name not in old_features:
            changed.append(name)
            continue
        old_calls = [call.to_dict() for call in before_plan.calls_for(name)]
        new_calls = [call.to_dict() for call in after_plan.calls_for(name)]
        # `index` moves whenever anything earlier is added or removed, and that
        # is not a change to *this* feature. Comparing the tool and the resolved
        # arguments is the question actually being asked.
        if _comparable(old_calls) != _comparable(new_calls):
            changed.append(name)

    return tuple(changed)


def _comparable(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A call list stripped of what says nothing about the geometry."""
    return [
        {"tool": call["tool"], "arguments": call.get("arguments", {})}
        for call in calls
    ]


def _downstream(after: DesignSpec, changed: Sequence[str], plan: Plan) -> tuple[str, ...]:
    """Features that stand on something that changed, transitively.

    Walked forward over the feature list rather than by a graph search, which
    works because a design's references always point backwards — the compiler
    refuses one that does not. So a single pass in build order reaches every
    dependent, and cannot loop.

    **A reference is not always a read.** `catia_sketch_rectangle(sketch=@profile)`
    draws *into* the profile; `catia_pad(sketch=@profile)` extrudes what it
    finds there. Widen the rectangle and the pad comes out a different shape
    while its own call is byte-identical — so following references alone would
    report the rectangle as the only thing that moved, and quietly leave every
    solid built on it out of the rebuild set.

    The two are told apart by something the compiler has already worked out
    rather than by a list kept here: a feature that creates a tree element gets
    an allocated name, and a feature that does not is *unaddressable* precisely
    because its effect landed on something else. So an unaddressable feature
    that changed makes what it references dirty, and everything reading that
    afterwards follows.
    """
    dirty = set(changed)
    downstream: list[str] = []

    for feature in after.features:
        references = _references(feature)
        if feature.name not in dirty:
            if not references & dirty:
                continue
            dirty.add(feature.name)
            downstream.append(feature.name)
        if feature.name in plan.unaddressable:
            # It built nothing of its own, so what it changed is what it wrote
            # into. Anything reading that later is standing on new geometry.
            dirty |= references

    return tuple(downstream)


def _references(feature: FeatureSpec) -> frozenset[str]:
    """Every `@other.feature` this feature's arguments point at, at any depth."""
    found: set[str] = set()
    _collect_references(feature.args, found)
    return frozenset(found)


def _collect_references(value: Any, found: set[str]) -> None:
    if isinstance(value, Mapping):
        for item in value.values():
            _collect_references(item, found)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_references(item, found)
        return
    if spec_module.is_reference(value):
        found.add(spec_module.reference_target(value))


__all__ = [
    "FeatureChange",
    "ParameterChange",
    "SpecDiff",
    "builds_the_same",
    "diff_plans",
    "diff_specs",
]
