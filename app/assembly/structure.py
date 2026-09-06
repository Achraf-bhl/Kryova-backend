"""The product structure: a graph of components, not a tree of copies.

Master plan 14.1 — product structure and BOM as **first-class data**, because a
conversation transcript is not a data structure. This is the level above
`app.design.spec`, and it is the same idea: a product is a *specification that is
compiled*, not a tree that is edited. The reason that works for a part — regenerating
from a spec has no downstream edit to break — is exactly the reason it works here, one
level up.

**A bolt used forty times is one component and forty occurrences.** That sentence is the
whole design. `Component` is a definition, held once; `Instance` is one *use* of a
component inside a parent, carrying only a placement and a local tag; `Occurrence` is
what a walk produces — a resolved path and a world frame, materialised on demand and
never stored. Get this wrong and a 5,000-part machine is 5,000 solids in memory, its
mass roll-up integrates the same bolt forty times, and "change that bolt" is a forty-
place edit that will be done in thirty-nine of them.

The graph is a DAG, not a tree: two different sub-assemblies may both instance the same
bracket, and there is exactly one bracket. A cycle is refused by name (`frame -> leg ->
frame`), because a component that contains itself has no finite occurrence set and the
walk would not terminate.

**A path is `frame/leg.2/bracket.1/bolt.3`, and its stability is load-bearing.** This is
the topological naming problem one level up, and it gets the same answer
`app.design.names` gives: the number is *declared by the author at the moment the
instance is created*, never derived from position in a list. Derive it from position and
inserting a second leg at the front renumbers every leg after it — silently, because
`leg.2` still exists, it is simply no longer the leg anyone meant. Every assertion,
clash exclusion, mass budget and drawing balloon that named `leg.2` now points somewhere
else. So `Instance.index` is a field, `spread()` allocates a run of them once, and
inserting into the list changes no existing path. `tests/test_assembly_structure.py`
pins that.

**Separators are chosen so a path parses back unambiguously.** `/` between levels, `.`
between a tag and its occurrence number, and both a component name and a tag are a
*single* `app.design.names` segment — lowercase, no dots. Reusing that validator rather
than writing a second regex is deliberate: two spellings of "what a name may be" is how
a name that one layer accepts becomes one the layer below refuses.

**What this does not have, stated plainly.** 14.1 also names *effectivity* — a component
valid from serial number 400, superseded at 700. There is none here. `Component.revision`
is free text carried through and read by nothing, and no code in this package selects a
component by date, serial or configuration. A structure walked today is the structure as
written. Saying otherwise in a docstring is the failure this repository has already had
twice.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from app.assembly.errors import StructureError
from app.assembly.placement import WORLD, Frame, compose, world
from app.design.errors import SemanticNameError
from app.design.names import SemanticName

#: Bumped when the serialised shape changes in a way an older reader would get wrong.
#: Refused rather than guessed at on load, for the same reason `DesignSpec` refuses one.
FORMAT_VERSION: Final = 1

#: Between two levels of nesting.
PATH_SEPARATOR: Final = "/"

#: Between an instance's tag and its occurrence number.
INDEX_SEPARATOR: Final = "."


def _check_segment(text: str, what: str) -> str:
    """One lowercase name segment, validated by `app.design.names`, no dots allowed.

    Delegating to `SemanticName` rather than re-deriving the rule keeps one definition
    of a usable name in the codebase — including its refusal of the operation vocabulary
    (`top`, `xy`, `min`), which a component called `top` would otherwise walk straight
    into two layers down.
    """
    try:
        parsed = SemanticName.parse(text)
    except SemanticNameError as exc:
        raise StructureError(f"{what} {text!r}: {exc}") from exc
    if len(parsed.parts) != 1:
        raise StructureError(
            f"{what} {text!r} contains a dot. A dot separates a tag from its occurrence "
            f"number in a path ('leg.2'), so a dotted name would make "
            f"{text!r} + '.2' ambiguous. Use an underscore: "
            f"{text.replace('.', '_')!r}."
        )
    return parsed.parts[0]


@dataclass(frozen=True)
class Instance:
    """One *use* of a component inside a parent component.

    Carries no geometry and no copy of the component — only which component, what it is
    called here, which one of them it is, and where it sits relative to the parent. That
    is what makes forty bolts cost forty of these and one `Component`.
    """

    component: str
    tag: str

    #: Which one of them this is: `leg.2`. **Declared, never positional** — see the
    #: module docstring. Starts at 1, because engineers count parts from one and a
    #: `leg.0` in a bill of materials reads as a mistake.
    index: int = 1

    #: Where it sits, relative to the parent component's own frame. Defaulted through
    #: `placement.world()` rather than the `WORLD` singleton because `Frame` is mutable
    #: and a shared default would be one object behind every unplaced instance.
    placement: Frame = field(default_factory=world)

    #: Why it is here — the rationale slot `FeatureSpec.note` is, for the same reason.
    note: str = ""

    def __post_init__(self) -> None:
        _check_segment(self.component, "A component name")
        _check_segment(self.tag, "An instance tag")
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 1:
            raise StructureError(
                f"{self.tag}: an occurrence number is a whole number from 1, got "
                f"{self.index!r}. It is the '2' in 'leg.2' and it is what every "
                "assertion, clash exclusion and drawing balloon refers to."
            )
        if not isinstance(self.placement, Frame):
            raise StructureError(
                f"{self.tag}.{self.index}: a placement is an app.dynamics.pose.Frame, "
                f"got {type(self.placement).__name__}. Build one with "
                "app.assembly.placement.at(x, y, z) or turned(axis, angle_rad)."
            )

    @property
    def segment(self) -> str:
        """The path segment this instance contributes: `leg.2`."""
        return f"{self.tag}{INDEX_SEPARATOR}{self.index}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "component": self.component,
            "tag": self.tag,
            "index": self.index,
        }
        if self.placement != WORLD:
            out["placement"] = {
                "rotation": list(self.placement.rotation),
                "origin_mm": list(self.placement.origin_mm),
            }
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Instance:
        unknown = set(data) - {"component", "tag", "index", "placement", "note"}
        if unknown:
            raise StructureError(
                f"Instance {data.get('tag')!r} carries unknown keys {sorted(unknown)}. A "
                "key this build does not understand is one it would silently drop."
            )
        placement = WORLD
        raw = data.get("placement")
        if raw is not None:
            rotation = tuple(float(v) for v in raw["rotation"])
            origin = tuple(float(v) for v in raw["origin_mm"])
            if len(rotation) != 9 or len(origin) != 3:
                raise StructureError(
                    f"Instance {data.get('tag')!r}: a placement is a 9-element row-major "
                    "rotation and a 3-element origin in mm."
                )
            placement = Frame(rotation, origin)  # type: ignore[arg-type]
        return cls(
            component=str(data["component"]),
            tag=str(data["tag"]),
            index=int(data.get("index", 1)),
            placement=placement,
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class Component:
    """A thing that is designed once, however many times it is used.

    A leaf component (no instances) is a part; one with instances is a sub-assembly.
    Nothing forbids a component from being both — a weldment that is itself machined
    after assembly is exactly that — so `design` and `instances` are independent fields
    rather than a discriminated union.
    """

    name: str

    #: The instances this component contains, in declared order. Order is the author's
    #: and is reported as written, the same rule `DesignSpec` keeps for features.
    instances: tuple[Instance, ...] = ()

    #: What this component *is*, for a leaf: a `DesignSpec` name, a catalogue
    #: designation (`app.parts` `Designation`), or a supplier part number. Free text on
    #: purpose — resolving it is the caller's job, and a structure must be walkable with
    #: no geometry anywhere near it.
    design: str = ""

    #: Material key (`app.solve.materials`), where the component's own design does not
    #: carry one. Read by the mass roll-up's measurer, never by this module.
    material: str = ""

    #: Free text. Carried, never interpreted — see the module docstring on effectivity.
    revision: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        _check_segment(self.name, "A component name")
        seen: dict[str, int] = {}
        for position, instance in enumerate(self.instances):
            if instance.segment in seen:
                raise StructureError(
                    f"{self.name}: two instances are both {instance.segment!r} "
                    f"(positions {seen[instance.segment]} and {position}). A path is how "
                    "everything downstream refers to one occurrence, so a duplicate "
                    "makes every reference to it a coin flip. Give the second one the "
                    f"next index — {instance.tag}.{max(i.index for i in self.instances) + 1}."
                )
            seen[instance.segment] = position

    @property
    def is_leaf(self) -> bool:
        return not self.instances

    def instance(self, segment: str) -> Instance:
        """The instance a path segment (`leg.2`) names."""
        for candidate in self.instances:
            if candidate.segment == segment:
                return candidate
        known = ", ".join(i.segment for i in self.instances) or "none"
        raise StructureError(
            f"{self.name} has no instance {segment!r}. It contains: {known}."
        )

    def with_instances(self, instances: Iterable[Instance]) -> Component:
        """A copy with a different instance list. Components are frozen; edits are copies."""
        return Component(
            name=self.name,
            instances=tuple(instances),
            design=self.design,
            material=self.material,
            revision=self.revision,
            description=self.description,
        )

    def next_index(self, tag: str) -> int:
        """The first unused occurrence number for `tag`.

        The allocator an author calls when adding one more of something. It reads the
        existing indices rather than counting them, so a structure that has had `leg.2`
        deleted does not hand `leg.3` back out as if it were fresh.
        """
        used = [i.index for i in self.instances if i.tag == tag]
        return max(used) + 1 if used else 1

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.design:
            out["design"] = self.design
        if self.material:
            out["material"] = self.material
        if self.revision:
            out["revision"] = self.revision
        if self.description:
            out["description"] = self.description
        out["instances"] = [instance.to_dict() for instance in self.instances]
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Component:
        unknown = set(data) - {
            "name",
            "instances",
            "design",
            "material",
            "revision",
            "description",
        }
        if unknown:
            raise StructureError(
                f"Component {data.get('name')!r} carries unknown keys {sorted(unknown)}. "
                "A key this build does not understand is one it would silently drop."
            )
        return cls(
            name=str(data["name"]),
            instances=tuple(Instance.from_dict(i) for i in data.get("instances") or ()),
            design=str(data.get("design") or ""),
            material=str(data.get("material") or ""),
            revision=str(data.get("revision") or ""),
            description=str(data.get("description") or ""),
        )


def spread(
    component: str,
    tag: str,
    placements: Sequence[Frame],
    *,
    start: int = 1,
    note: str = "",
) -> tuple[Instance, ...]:
    """`len(placements)` instances of one component, numbered from `start`.

    The forty-bolt constructor. Numbering is allocated **once, here**, and then lives in
    the data — so a later edit that inserts a bolt at the head of the list does not
    renumber the other thirty-nine. `Component.next_index` is what a later addition
    should be given as its `start`.
    """
    return tuple(
        Instance(component=component, tag=tag, index=start + offset, placement=frame, note=note)
        for offset, frame in enumerate(placements)
    )


@dataclass(frozen=True)
class Occurrence:
    """One resolved use of a component, at a path, in world coordinates.

    **Materialised by a walk, never stored.** A structure holds components and
    instances; occurrences are computed. That is what keeps a 5,000-occurrence machine
    a few dozen objects on disk, and it is why `occurrence_count()` can answer "how big
    is this" by multiplying over the graph without building a single one of these.
    """

    path: str
    component: str
    frame: Frame

    #: Component names from the root down to and including this one. What
    #: `app.assembly.contracts` uses to decide whether a change to a component reaches
    #: an interface written against an occurrence path.
    ancestry: tuple[str, ...] = ()

    @property
    def depth(self) -> int:
        """0 for the root, 1 for its direct children."""
        return len(self.ancestry) - 1

    @property
    def parent_path(self) -> str | None:
        head, sep, _ = self.path.rpartition(PATH_SEPARATOR)
        return head if sep else None

    def __str__(self) -> str:
        return f"{self.path} ({self.component})"


@dataclass(frozen=True)
class BomLine:
    """One line of a bill of materials: a component and how many of it the product uses.

    Quantity is computed by multiplying down the graph, not by counting occurrences, so
    a 40,000-fastener machine produces its BOM without enumerating 40,000 paths.
    """

    component: str
    quantity: int
    design: str = ""
    material: str = ""
    revision: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"component": self.component, "quantity": self.quantity}
        for key, value in (
            ("design", self.design),
            ("material", self.material),
            ("revision", self.revision),
        ):
            if value:
                out[key] = value
        return out


class ProductStructure:
    """A root component and every component reachable from it.

    Validated once, at construction: every referenced component exists, no component
    contains itself, no two instances of one parent share a path segment. Everything
    after that can walk without checking, which matters when the walk runs per pose of a
    motion sweep.

    Not a dataclass, because the interesting behaviour is the refusals and each one
    wants a message rather than a `False` — the same reasoning `app.design.names`
    records for `NameTable`.
    """

    __slots__ = ("_components", "_root", "_leaf_counts", "_node_counts")

    def __init__(self, root: str, components: Iterable[Component]) -> None:
        collected: dict[str, Component] = {}
        for component in components:
            if component.name in collected:
                raise StructureError(
                    f"Two components are both called {component.name!r}. A component is "
                    "the definition everything else points at, so a duplicate makes "
                    "every instance of it ambiguous — and one of the two is silently "
                    "ignored today."
                )
            collected[component.name] = component

        if root not in collected:
            known = ", ".join(sorted(collected)) or "none"
            raise StructureError(
                f"The root component {root!r} is not among the components given. "
                f"Defined: {known}."
            )

        self._root = root
        self._components = collected
        self._leaf_counts: dict[str, int] = {}
        self._node_counts: dict[str, int] = {}
        self._validate_references()
        self._refuse_cycles()

    # -- validation ----------------------------------------------------------

    def _validate_references(self) -> None:
        for component in self._components.values():
            for instance in component.instances:
                if instance.component not in self._components:
                    known = ", ".join(sorted(self._components))
                    raise StructureError(
                        f"{component.name}/{instance.segment} instances a component "
                        f"called {instance.component!r}, which is not defined. Defined: "
                        f"{known}. Add the component, or correct the reference — an "
                        "instance of nothing cannot be placed, measured or bought."
                    )

    def _refuse_cycles(self) -> None:
        """Depth-first, reporting the cycle as a path rather than as a fact.

        `frame -> leg -> frame` tells the author which instance to delete;
        "cycle detected" starts a search. Walks every component, not only those
        reachable from the root, because an unreachable cycle is still a defect and
        becomes a hang the moment somebody re-roots the product at it.
        """
        visiting: list[str] = []
        done: set[str] = set()

        def walk(name: str) -> None:
            if name in done:
                return
            if name in visiting:
                cycle = " -> ".join([*visiting[visiting.index(name) :], name])
                raise StructureError(
                    f"{name!r} contains itself: {cycle}. A component that contains "
                    "itself has no finite parts list, so nothing here can count, place "
                    "or weigh it. Break the loop by making the inner one a different "
                    "component."
                )
            visiting.append(name)
            for instance in self._components[name].instances:
                walk(instance.component)
            visiting.pop()
            done.add(name)

        for name in self._components:
            walk(name)

    # -- reading the graph ---------------------------------------------------

    @property
    def root(self) -> str:
        return self._root

    def component(self, name: str) -> Component:
        try:
            return self._components[name]
        except KeyError:
            known = ", ".join(sorted(self._components)) or "none"
            raise StructureError(
                f"No component called {name!r} in this product. Defined: {known}."
            ) from None

    def component_names(self) -> tuple[str, ...]:
        """Sorted, so a report over them is stable between runs."""
        return tuple(sorted(self._components))

    def __contains__(self, name: object) -> bool:
        return name in self._components

    def __len__(self) -> int:
        """How many *components* — definitions, not occurrences. See `occurrence_count`."""
        return len(self._components)

    def reachable(self) -> frozenset[str]:
        """Component names reachable from the root."""
        seen: set[str] = set()
        frontier = [self._root]
        while frontier:
            name = frontier.pop()
            if name in seen:
                continue
            seen.add(name)
            frontier.extend(i.component for i in self._components[name].instances)
        return frozenset(seen)

    def orphans(self) -> tuple[str, ...]:
        """Components defined and never used from the root.

        Not an error — a design in progress is full of them — but reported rather than
        ignored, because a part that is drawn, costed and never assembled is a finding
        and not nothing. The same rule `PartDocument.unused_sketches` applies to a
        sketch nobody built from.
        """
        return tuple(sorted(set(self._components) - self.reachable()))

    def used_by(self, name: str) -> tuple[str, ...]:
        """Which components instance this one directly — 14.1's *where-used*.

        Component names, not paths: "which parents would a change to this bracket
        affect" is answered at the graph level and costs one pass, where the path-level
        answer costs one pass per occurrence. `paths_of` is the other question.
        """
        self.component(name)
        return tuple(
            sorted(
                parent.name
                for parent in self._components.values()
                if any(i.component == name for i in parent.instances)
            )
        )

    # -- counting without enumerating ---------------------------------------

    def occurrence_count(self, *, leaves_only: bool = True) -> int:
        """How many occurrences the product has, computed over the graph.

        Multiplication down the DAG with memoisation, so a machine whose bolt appears
        40,000 times answers in the size of the *graph*, not of the walk. This is what a
        caller checks before asking for a pairwise clash check, which is O(n^2) in this
        number and has to be able to say so before it starts.
        """
        counts = self._leaf_counts if leaves_only else self._node_counts

        def count(name: str) -> int:
            cached = counts.get(name)
            if cached is not None:
                return cached
            component = self._components[name]
            if component.is_leaf:
                total = 1
            else:
                total = (0 if leaves_only else 1) + sum(
                    count(i.component) for i in component.instances
                )
            counts[name] = total
            return total

        return count(self._root)

    def quantities(self) -> dict[str, int]:
        """How many times each component is used in the whole product.

        Computed by propagating multiplicity down the graph, again without enumerating
        occurrences: the root has multiplicity 1, and each instance multiplies its
        parent's. A DAG is fine — two parents both using a bracket add up.
        """
        totals: dict[str, int] = {}
        # Depth-first with an explicit stack of (component, multiplicity). Terminates
        # because `_refuse_cycles` has already run.
        stack: list[tuple[str, int]] = [(self._root, 1)]
        while stack:
            name, multiplicity = stack.pop()
            totals[name] = totals.get(name, 0) + multiplicity
            for instance in self._components[name].instances:
                stack.append((instance.component, multiplicity))
        return totals

    def bill_of_materials(self, *, leaves_only: bool = True) -> tuple[BomLine, ...]:
        """The parts list, sorted by component name so two runs compare cleanly.

        `leaves_only` gives the buy/make list; `False` gives the indented structure's
        sub-assembly counts as well. The root is excluded either way — a product is not
        a line item of itself.
        """
        totals = self.quantities()
        lines = []
        for name in sorted(totals):
            if name == self._root:
                continue
            component = self._components[name]
            if leaves_only and not component.is_leaf:
                continue
            lines.append(
                BomLine(
                    component=name,
                    quantity=totals[name],
                    design=component.design,
                    material=component.material,
                    revision=component.revision,
                )
            )
        return tuple(lines)

    # -- walking -------------------------------------------------------------

    def occurrences(self, *, leaves_only: bool = True) -> Iterator[Occurrence]:
        """Every occurrence, depth-first in declared order.

        A generator, so a caller that only wants the first clash does not pay for the
        whole machine, and so memory is one occurrence rather than 5,000. Order is
        declaration order at every level and is therefore reproducible — a clash report
        that listed its pairs in hash order would produce a different diff every run.
        """

        def walk(name: str, path: str, frame: Frame, ancestry: tuple[str, ...]):
            component = self._components[name]
            if component.is_leaf or not leaves_only:
                yield Occurrence(path=path, component=name, frame=frame, ancestry=ancestry)
            for instance in component.instances:
                yield from walk(
                    instance.component,
                    f"{path}{PATH_SEPARATOR}{instance.segment}",
                    compose(frame, instance.placement),
                    (*ancestry, instance.component),
                )

        yield from walk(self._root, self._root, WORLD, (self._root,))

    def occurrence(self, path: str) -> Occurrence:
        """Resolve one path — `frame/leg.2/bracket.1` — without walking the rest.

        Refuses a path whose head is not the root, rather than resolving it relative to
        the root anyway: a path is absolute, and a caller that has half of one has a bug
        rather than a shorthand.
        """
        segments = [s for s in str(path).split(PATH_SEPARATOR) if s]
        if not segments:
            raise StructureError(
                "An occurrence path cannot be empty. The root's own path is "
                f"{self._root!r}."
            )
        if segments[0] != self._root:
            raise StructureError(
                f"{path!r} starts at {segments[0]!r}, but this product's root is "
                f"{self._root!r}. Paths are absolute — prefix it with the root."
            )

        name = self._root
        frame = WORLD
        ancestry: tuple[str, ...] = (self._root,)
        walked = self._root
        for segment in segments[1:]:
            instance = self.component(name).instance(segment)
            frame = compose(frame, instance.placement)
            name = instance.component
            ancestry = (*ancestry, name)
            walked = f"{walked}{PATH_SEPARATOR}{segment}"
        return Occurrence(path=walked, component=name, frame=frame, ancestry=ancestry)

    def paths_of(self, name: str) -> tuple[str, ...]:
        """Every occurrence path at which a component appears.

        The expensive half of where-used: O(occurrences), because every path has to be
        built to be reported. `used_by` answers the cheap half at the graph level, and a
        caller reporting on a 40,000-bolt machine should want that one.
        """
        self.component(name)
        return tuple(
            occurrence.path
            for occurrence in self.occurrences(leaves_only=False)
            if occurrence.component == name
        )

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Canonical form: components in sorted order, instances as declared.

        Sorted components because the registry is a mapping and its iteration order is
        not part of the product; declared instance order because it *is*. A digest that
        moved when a dict happened to be built differently would make the structure
        useless as a provenance record — the same argument `DesignSpec.to_dict` makes.
        """
        return {
            "format_version": FORMAT_VERSION,
            "root": self._root,
            "components": [
                self._components[name].to_dict() for name in sorted(self._components)
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProductStructure:
        version = data.get("format_version")
        if version != FORMAT_VERSION:
            raise StructureError(
                f"This product structure is format version {version!r}; this build reads "
                f"version {FORMAT_VERSION}. Refusing to guess at the difference."
            )
        unknown = set(data) - {"format_version", "root", "components"}
        if unknown:
            raise StructureError(
                f"This product structure carries unknown keys {sorted(unknown)}. A key "
                "this build does not understand is one it would silently drop."
            )
        return cls(
            root=str(data["root"]),
            components=[Component.from_dict(c) for c in data.get("components") or ()],
        )

    def digest(self) -> str:
        """A stable identity for the structure as written.

        Covers placements and notes, so it answers "is this the same product document",
        not "does this assemble to the same machine" — exactly the distinction
        `Plan.digest` records for a part, and for the same reason: rationale travels
        with the design and a provenance record wants the coarser answer.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=False, separators=(",", ":"))
        return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).hexdigest()

    def summary(self) -> str:
        occurrences = self.occurrence_count()
        components = len(self.reachable())
        line = (
            f"{self._root}: {occurrences} part occurrences from {components} distinct "
            f"components."
        )
        loose = self.orphans()
        if loose:
            line += f" Defined and never used: {', '.join(loose)}."
        return line


@dataclass(frozen=True)
class StructureBuilder:
    """A tiny mutable-feeling façade over frozen components, for readable test fixtures.

    Deliberately thin: it allocates occurrence numbers through `Component.next_index` so
    a fixture cannot accidentally reuse one, and it holds nothing a `ProductStructure`
    does not. Anything that wants more than this should build `Component`s directly.
    """

    components: dict[str, Component] = field(default_factory=dict)

    def define(
        self,
        name: str,
        *,
        design: str = "",
        material: str = "",
        revision: str = "",
        description: str = "",
    ) -> Component:
        if name in self.components:
            raise StructureError(
                f"{name!r} is already defined. A component is defined once and instanced "
                "many times — that is the whole point of the graph."
            )
        component = Component(
            name=name,
            design=design,
            material=material,
            revision=revision,
            description=description,
        )
        self.components[name] = component
        return component

    def add(
        self,
        parent: str,
        child: str,
        *,
        tag: str | None = None,
        placement: Frame = WORLD,
        note: str = "",
    ) -> Instance:
        """Instance `child` inside `parent`, taking the next free occurrence number."""
        if parent not in self.components:
            raise StructureError(f"No component called {parent!r} to add {child!r} to.")
        if child not in self.components:
            raise StructureError(f"No component called {child!r} to add to {parent!r}.")
        holder = self.components[parent]
        label = tag or child
        instance = Instance(
            component=child,
            tag=label,
            index=holder.next_index(label),
            placement=placement,
            note=note,
        )
        self.components[parent] = holder.with_instances((*holder.instances, instance))
        return instance

    def build(self, root: str) -> ProductStructure:
        return ProductStructure(root=root, components=list(self.components.values()))


__all__ = [
    "FORMAT_VERSION",
    "INDEX_SEPARATOR",
    "PATH_SEPARATOR",
    "BomLine",
    "Component",
    "Instance",
    "Occurrence",
    "ProductStructure",
    "StructureBuilder",
    "spread",
]
