"""Composing and checking an assembly on the open kernel.

`app/assembly/` shipped a product structure, a clash check and a mass roll-up with
**no agent-facing surface at all** — no tool, no route, no persistence. This module
and the six operations it serves are that surface. It is deliberately six and not
twenty: Decision 1 says an operation is added to this backend when a test, a sweep
or an optimisation needs one, and what M2 (a welded frame) needs is to *create* an
assembly, *define* a component, *place* instances of it, *list* what is in there,
*check clashes* and *roll up the mass*. Constraints, exploded views, assembly
features and component replacement are all declared in `app/catia/ops/assembly.py`
for a seat and are not implemented here, because nothing yet needs them.

**A component is taken from the conversation, not opened from a file.** A seat
names a CATPart on disk; this backend has a live `PartDocument` and the next
`catia_new_part` replaces it. So `catia_assembly_component` *takes* the open part —
records its shape, material and document, and then leaves the context with no
document open. That is not a side effect to tidy away later, it is the mechanism:
`app/ai/tools.py` refuses a second `catia_new_part` while a document is still live
in memory, so without the hand-off the agent could build exactly one part per
conversation and an assembly would be unreachable from the product. Closing the
part is what makes the next one startable, and the tool summary says so.

**The build journal is cleared with it, and that is not optional either.**
`catia_set_parameter` rewrites one argument and *replays the whole journal into a
fresh document*. Left alone, the journal after two components would replay part
one's pad into part two. So the journal is reset when a component is taken, and the
three mutating assembly operations are kept out of `RECORDED` entirely — a replay
that re-ran `catia_assembly_component` would hand the fresh context's document away
mid-rebuild and every entry after it would fail with "no document is open". The
honest consequence, stated rather than hidden: **a component that has been taken can
no longer have its dimensions changed with `catia_set_parameter`.** Correct it before
recording it, or rebuild it under the same name — recording a name twice replaces
that component's definition, which is the recovery.

**Placement is the part that goes wrong, so it is expressed the way a drawing is.**
`app/catia/ops/placement.py` records the measured failure this is modelled on: asked
for four holes on a 70 mm bolt circle, the model computed (35, 35) — a 99 mm circle —
because the vocabulary made it do coordinate arithmetic the prompt forbids. A 3D
placement is the same trap one level up and worse, because the value a model would
have to type is a rotation matrix. So the vocabulary is *position* (`at`) and *turn*
(`turn_axis` + `turn_deg`), the two things an engineer actually says about where a
part goes in a frame, and the composition into a `pose.Frame` happens once, here,
through `app.assembly.placement.at` / `turned` / `compose`. No trigonometry is
written in this module and none is asked of the model. What that deliberately does
not offer is an arbitrary orientation — a part turned about a skew axis, or about
two axes at once — because a welded frame does not need one and an unused rotation
vocabulary is a place for a wrong answer to hide. It is one more `turned()` in
`_frame` when something needs it.

**Names are normalised before they are refused.** `app.design.names` requires a
lowercase segment, and a local model types `Frame` and `Top Rail` all day. Refusing
those costs a turn and teaches nothing, so they are lowercased and their spaces and
hyphens become underscores *before* validation, and the normalised name is reported
back in the result so the model sees what it must refer to next. Anything still
malformed after that (`9leg`, a reserved vocabulary word like `top`) is refused with
the reason, because guessing at it would be inventing a name nobody chose.

**`app.assembly` is imported inside the handlers, never at module scope.**
`operations/__init__.py` keeps the CATIA registry out of a geometry-only import for
its own reasons; `app.assembly` reaches `app.design`, which reaches that registry,
so a module-level import here would drag the whole thing in behind `app.kernel`.
The same discipline `app/assembly/` keeps in the other direction, for the same
reason.

**Nothing here is persisted.** The assembly lives on the conversation's
`BuildContext` and dies with it — an LRU eviction, a worker recycle or a restart
takes it, exactly as it takes the part. Storing one needs a table keyed by
conversation holding `ProductStructure.to_dict()` (which is already canonical and
versioned) plus, for the geometry, a saved shape per component: the structure
serialises today and the `PartDocument`s do not. That is a model and a migration,
which is why it is reported rather than attempted here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from app.kernel.errors import GeometryError
from app.kernel.measurement import Detail
from app.kernel.occt.document import PartDocument
from app.kernel.occt.operations.context import BuildContext, as_point

PRODUCT_CREATE: Final = "catia_product_create"
COMPONENT: Final = "catia_assembly_component"
PLACE: Final = "catia_assembly_place"
BILL_OF_MATERIALS: Final = "catia_bill_of_materials"
CLASH: Final = "catia_assembly_clash"
ANALYSIS: Final = "catia_assembly_analysis"

#: The three that change the assembly. Kept out of the build journal — see the
#: module docstring — and named here so `operations/__init__.py` excludes exactly
#: these rather than a hand-copied list that can fall out of step.
MUTATING: Final[frozenset[str]] = frozenset({PRODUCT_CREATE, COMPONENT, PLACE})

#: `turn_axis` as a direction vector. The whole of the rotation vocabulary: three
#: entries, because a machine frame is built from quarter turns about the assembly's
#: own axes and anything else would be a rotation the model had to compute.
_AXES: Final[dict[str, tuple[float, float, float]]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

#: Kinds `catia_assembly_analysis` declares that this backend cannot answer, each
#: with what to do instead. A refusal that names the reason is the contract this
#: codebase keeps for an operation a backend does not have; a bare failure would
#: read to the agent as a broken assembly and it would set about "fixing" it.
_ANALYSIS_REFUSALS: Final[dict[str, str]] = {
    "constraints": (
        "components are placed by an explicit position and turn here, not by "
        "constraints, so there are no constraints to analyse. Move a part with "
        "catia_assembly_place instead"
    ),
    "degrees_of_freedom": (
        "every component is placed outright, so nothing is left free to move and "
        "the answer would be zero for every part whether that was true or not"
    ),
    "broken_links": (
        "a component holds the geometry it was recorded with, so there is no link "
        "to a file that could break"
    ),
    "dependencies": (
        "the structure is a flat list of placements under one root, so "
        "catia_bill_of_materials already reports the whole of it"
    ),
}


# -- what a conversation is holding ------------------------------------------


@dataclass(frozen=True)
class Placed:
    """One instance, exactly as the agent asked for it.

    The `Frame` is *derived* from this rather than stored beside it, so there is one
    answer to "where is leg.2" and no second copy to drift. `index` is allocated once,
    when the instance is created, and never renumbered — `app.assembly.structure`'s
    rule, and the reason a clash finding written down last week still names the same
    part.
    """

    component: str
    tag: str
    index: int
    origin_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    turn_axis: str = ""
    turn_deg: float = 0.0
    note: str = ""


@dataclass
class AssemblyState:
    """The assembly this conversation is composing. Held in memory, never stored."""

    name: str
    part_number: str = ""

    #: Component name -> the finished part. Keeps the whole `PartDocument` rather
    #: than its shape alone because the mass roll-up measures through
    #: `PartDocument.measure()`, which is where a density that was set after the
    #: geometry gets applied.
    documents: dict[str, PartDocument] = field(default_factory=dict)
    descriptions: dict[str, str] = field(default_factory=dict)
    placements: list[Placed] = field(default_factory=list)

    def next_index(self, tag: str) -> int:
        used = [p.index for p in self.placements if p.tag == tag]
        return max(used) + 1 if used else 1


def _held(context: BuildContext, tool: str) -> AssemblyState:
    """The assembly, or the refusal that says how to start one."""
    state = context.assembly
    if not isinstance(state, AssemblyState):
        raise GeometryError(
            f"{tool} needs an assembly and none has been created. Call "
            f"{PRODUCT_CREATE} first — it holds the components and the placements "
            "between them."
        )
    return state


def _segment(text: Any, *, what: str, tool: str) -> str:
    """One usable name, normalised first and refused second.

    Lowercasing and turning spaces and hyphens into underscores is not politeness:
    `app.design.names` requires a lowercase segment and a local model types `Frame`
    and `Top Rail`, so without this the ordinary case is a refusal that costs a turn
    and produces the same part. What is *not* guessed at is anything still malformed
    afterwards — a leading digit, a dot, or one of the operation vocabulary's own
    words — because inventing a name nobody chose is how a later reference resolves
    to the wrong thing.
    """
    from app.design.errors import SemanticNameError
    from app.design.names import SemanticName

    raw = str(text or "").strip()
    if not raw:
        raise GeometryError(f"{tool} needs {what} and none was given.")
    normalised = raw.lower().replace(" ", "_").replace("-", "_")
    try:
        parsed = SemanticName.parse(normalised)
    except SemanticNameError as exc:
        raise GeometryError(f"{tool}: {raw!r} is not usable as {what}. {exc}") from exc
    if len(parsed.parts) != 1:
        raise GeometryError(
            f"{tool}: {raw!r} is not usable as {what} — it contains a dot, and a dot "
            "separates an instance's tag from its occurrence number in a path "
            f"('leg.2'). Use an underscore: {normalised.replace('.', '_')!r}."
        )
    return parsed.parts[0]


def _frame(placed: Placed) -> Any:
    """Where this instance sits, as an `app.dynamics.pose.Frame`.

    The one place the placement vocabulary becomes a transform. `turned` rotates
    about the assembly origin and `compose` then moves the result to `at`, so the
    two arguments mean what an engineer means by them: *turn it, then put it there*.
    """
    from app.assembly.placement import at, compose, turned

    position = at(*placed.origin_mm)
    if not placed.turn_axis:
        return position
    return compose(position, turned(_AXES[placed.turn_axis], math.radians(placed.turn_deg)))


def _structure(state: AssemblyState, tool: str) -> Any:
    """The `ProductStructure` this state describes, built fresh on every read.

    Fresh rather than cached: building it is a few dozen objects and it is the thing
    that validates the state (a placement of a component that is gone, a duplicated
    occurrence segment, a root that collides with a component). A cached structure
    would be a second answer to "what is in this assembly" and the two would diverge
    the first time one of them was rebuilt and the other was not.
    """
    from app.assembly.errors import StructureError
    from app.assembly.structure import Component, Instance, ProductStructure

    try:
        leaves = [
            Component(
                name=name,
                design=document.name,
                material=document.material or "",
                description=state.descriptions.get(name, ""),
            )
            for name, document in state.documents.items()
        ]
        root = Component(
            name=state.name,
            instances=tuple(
                Instance(
                    component=placed.component,
                    tag=placed.tag,
                    index=placed.index,
                    placement=_frame(placed),
                    note=placed.note,
                )
                for placed in state.placements
            ),
            revision=state.part_number,
        )
        return ProductStructure(root=state.name, components=[root, *leaves])
    except StructureError as exc:
        raise GeometryError(f"{tool}: {exc}") from exc


def _shapes(state: AssemblyState) -> dict[str, Any]:
    """Component name -> its shape, **omitting any component that has none**.

    Omitted rather than mapped to None on purpose: `app.assembly.clash`'s providers
    raise `KeyError` naming the component, and `find_clashes` turns that into an
    `unchecked` pair carrying the reason. A component with no geometry is not a
    component with no clashes, and the report says which it is.
    """
    return {
        name: document.shape
        for name, document in state.documents.items()
        if document.shape is not None
    }


def _overview(state: AssemblyState, structure: Any) -> dict[str, Any]:
    """The few lines every assembly result carries, so the agent can see where it is.

    The occurrence count is read from the placements rather than from
    `ProductStructure.occurrence_count()`, and the difference is not cosmetic: a root
    with no instances **is a leaf**, so an assembly nobody has placed anything in
    counts as one occurrence — itself. That is right for the walk and wrong for the
    sentence, and reporting it would tell an agent it had assembled something.
    """
    placed = len(state.placements)
    return {
        "assembly": state.name,
        "components": sorted(state.documents),
        "occurrence_count": placed,
        "summary": (
            structure.summary()
            if placed
            else (
                f"{state.name}: nothing placed yet, "
                f"{len(state.documents)} component(s) recorded."
            )
        ),
    }


def _require_placements(state: AssemblyState, tool: str) -> None:
    """Refuse a whole-assembly question before anything has been placed.

    Same root cause as `_overview`: with no instances the walk yields the root as its
    own leaf occurrence, and a clash check or a mass roll-up would then go looking for
    the geometry of a component that is the assembly. The refusal says what is
    missing; the alternative is a report about a part that does not exist.
    """
    if not state.placements:
        raise GeometryError(
            f"{tool}: nothing has been placed in {state.name!r} yet, so there is "
            f"nothing to report on. Record each part with {COMPONENT} and place "
            f"instances of it with {PLACE}."
        )


def _restricted_to(chosen: frozenset[str]) -> Callable[[Any, Any], str]:
    """An `app.assembly.clash` ignore rule for a check the caller narrowed.

    A rule rather than a pruned walk, so the pairs it drops land in the report's
    `excluded` list with the reason. A filter that made pairs simply vanish would be
    the exact lie `clash.py` is written against.
    """

    def rule(left: Any, right: Any) -> str:
        if left.component in chosen or right.component in chosen:
            return ""
        return "neither part is among the components this check was restricted to"

    return rule


# -- the operations ----------------------------------------------------------


def product_create(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Start the assembly the components will be placed into.

    Refuses a second one rather than replacing the first. Replacing would drop every
    component and placement already recorded and report success for it — the failure
    mode this codebase calls a wrongly built part, one level up.
    """
    existing = context.assembly
    if isinstance(existing, AssemblyState):
        raise GeometryError(
            f"This conversation is already composing the assembly {existing.name!r}, "
            f"with {len(existing.documents)} component(s) and "
            f"{len(existing.placements)} placement(s). Creating another would discard "
            "all of it. Add to this one with catia_assembly_component and "
            f"{PLACE}."
        )

    name = _segment(arguments.get("name"), what="an assembly name", tool=PRODUCT_CREATE)
    state = AssemblyState(name=name, part_number=str(arguments.get("part_number") or ""))
    context.assembly = state
    structure = _structure(state, PRODUCT_CREATE)
    return {
        **_overview(state, structure),
        "part_number": state.part_number,
        "note": (
            "The assembly is empty. Build each part with catia_new_part, record it "
            f"with {COMPONENT}, and place instances of it with {PLACE}."
        ),
    }


def component(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Take the open part into the assembly as a reusable component definition.

    The part is *handed over*: after this the context holds no document, which is
    what lets `catia_new_part` start the next component (see the module docstring —
    `app/ai/tools.py` refuses one while a document is still live). The build journal
    goes with it, so a later `catia_set_parameter` rewrites the part being built now
    and not the one already recorded.
    """
    state = _held(context, COMPONENT)
    document = context.require_document()
    name = _segment(arguments.get("name"), what="a component name", tool=COMPONENT)

    if name == state.name:
        raise GeometryError(
            f"{COMPONENT}: {name!r} is the assembly's own name, so a component called "
            "that could not be told from the assembly it sits in. Give the component "
            "its own name."
        )
    if document.shape is None:
        raise GeometryError(
            f"{COMPONENT}: nothing has been built in {document.name!r}, so there is no "
            "geometry to record as a component. Build the part first — a component "
            "with no shape cannot be placed, weighed or checked for clashes."
        )

    replaced = name in state.documents
    state.documents[name] = document
    description = str(arguments.get("description") or "")
    if description:
        state.descriptions[name] = description

    # The hand-off, and the journal with it. Both are load-bearing; see the module
    # docstring. Cleared *after* the state has taken the document, so a refusal above
    # leaves the part exactly as it was.
    context.document = None
    context.journal.clear()

    structure = _structure(state, COMPONENT)
    measured = document.measure(detail=Detail.FULL)
    return {
        **_overview(state, structure),
        "component": name,
        "design": document.name,
        "material": document.material or "",
        "replaced": replaced,
        "mass_kg": measured.get("mass_kg"),
        "placed": sum(1 for p in state.placements if p.component == name),
        "note": (
            f"{document.name!r} is now the component {name!r} and is no longer open, "
            "so catia_new_part can start the next one. Its dimensions can no longer "
            "be changed with catia_set_parameter — record it again under the same "
            "name to replace it. "
            + (
                f"Place it with {PLACE}."
                if not replaced
                else "Every existing placement of it now uses the new geometry."
            )
        ),
    }


def place(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Add one instance of a component at a position and a turn."""
    state = _held(context, PLACE)
    name = _segment(arguments.get("component"), what="a component name", tool=PLACE)
    if name not in state.documents:
        known = ", ".join(sorted(state.documents)) or "none yet"
        raise GeometryError(
            f"{PLACE}: {state.name!r} has no component called {name!r}. Recorded so "
            f"far: {known}. Build the part and record it with {COMPONENT} before "
            "placing it."
        )

    tag = (
        _segment(arguments.get("tag"), what="an instance tag", tool=PLACE)
        if arguments.get("tag")
        else name
    )
    turn_axis = str(arguments.get("turn_axis") or "").lower()
    turn_deg = float(arguments.get("turn_deg") or 0.0)
    if turn_deg and not turn_axis:
        raise GeometryError(
            f"{PLACE}: turn_deg was given without turn_axis, so there is nothing to "
            "turn the component about. Give turn_axis as 'x', 'y' or 'z'."
        )
    if turn_axis and turn_axis not in _AXES:
        raise GeometryError(
            f"{PLACE}: turn_axis must be 'x', 'y' or 'z'; got {turn_axis!r}."
        )

    placed = Placed(
        component=name,
        tag=tag,
        index=state.next_index(tag),
        origin_mm=as_point(arguments.get("at"), argument="at"),
        turn_axis=turn_axis,
        turn_deg=turn_deg,
        note=str(arguments.get("note") or ""),
    )
    state.placements.append(placed)
    try:
        structure = _structure(state, PLACE)
    except GeometryError:
        # The structure is what validates a placement, so a refused one must not be
        # left in the state: the next call would rebuild and fail on somebody else's
        # placement, naming a fault the agent did not just make.
        state.placements.pop()
        raise

    occurrence = structure.occurrence(f"{state.name}/{placed.tag}.{placed.index}")
    return {
        **_overview(state, structure),
        "occurrence": occurrence.path,
        "component": name,
        "placed_at_mm": list(occurrence.frame.origin_mm),
        "turned": (
            f"{turn_deg:g} degrees about {turn_axis}" if turn_axis else "not turned"
        ),
        "instances_of_component": len(structure.paths_of(name)),
    }


def bill_of_materials(
    context: BuildContext, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    """What is in the assembly: the parts list, and every occurrence when asked.

    **`recursive` changes nothing today and the payload says so** rather than being
    quietly dropped. Every component sits directly under the root — the tool layer
    has no way to place a sub-assembly inside another (`app/assembly/` supports it;
    this surface does not yet) — so the indented structure and the buy/make list are
    the same list. Reporting the argument back with `recursive_has_no_effect` is the
    honest form of "carried, not implemented".

    Components recorded and never placed are reported under
    `defined_but_never_placed` rather than being absent, which is
    `ProductStructure.orphans()`' own rule: a part that was drawn and never assembled
    is a finding, not nothing.
    """
    state = _held(context, BILL_OF_MATERIALS)
    structure = _structure(state, BILL_OF_MATERIALS)
    detailed = str(arguments.get("format") or "summary") == "detailed"

    payload: dict[str, Any] = {
        **_overview(state, structure),
        "lines": [line.to_dict() for line in structure.bill_of_materials(leaves_only=True)],
        "defined_but_never_placed": list(structure.orphans()),
        "recursive_has_no_effect": (
            "every component sits directly under the root, so there is no "
            "sub-assembly whose contents could be left out"
        ),
    }
    if detailed:
        payload["occurrences"] = [
            {
                "path": occurrence.path,
                "component": occurrence.component,
                "at_mm": [round(v, 6) for v in occurrence.frame.origin_mm],
            }
            for occurrence in structure.occurrences(leaves_only=True)
        ]
    return payload


def clash(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Does anything hit anything, and what was actually looked at.

    Everything honest about this lives in `app.assembly.clash` already — the broad
    phase is sound because a bounding box separation is a lower bound, a minimum over
    a subset is published under a different name because it over-estimates clearance,
    and a pair nobody looked at is counted rather than dropped. This wires it to the
    conversation's components and adds nothing to the reasoning.

    `components` is honoured as an exclusion rule rather than by pruning the walk, so
    the pairs it removes appear in the report as `excluded` with the reason. A filter
    that made pairs vanish would be the same lie the module is written against.
    """
    state = _held(context, CLASH)
    _require_placements(state, CLASH)
    structure = _structure(state, CLASH)
    clearance = float(arguments.get("clearance_mm") or 0.0)
    kind = str(arguments.get("kind") or "clash")
    if kind == "clearance" and clearance <= 0.0:
        raise GeometryError(
            f"{CLASH}: kind='clearance' asks how much room there is between parts, so "
            "it needs clearance_mm — the gap you want to be sure of. Use kind='clash' "
            "to look for overlap alone."
        )

    from app.assembly.clash import find_clashes, occt_bounds, occt_measurer

    wanted = arguments.get("components")
    ignore: Callable[[Any, Any], str] | None = None
    if wanted:
        chosen = frozenset(
            _segment(name, what="a component name", tool=CLASH) for name in wanted
        )
        unknown = sorted(chosen - set(state.documents))
        if unknown:
            known = ", ".join(sorted(state.documents)) or "none"
            raise GeometryError(
                f"{CLASH}: no component called {', '.join(unknown)} in "
                f"{state.name!r}. Recorded: {known}."
            )
        ignore = _restricted_to(chosen)

    shapes = _shapes(state)
    report = find_clashes(
        structure,
        occt_measurer(shapes),
        occt_bounds(shapes),
        ignore=ignore,
        clearance_mm=clearance,
    )
    return {
        **_overview(state, structure),
        **report.to_payload(),
        "kind": kind,
        "clearance_mm": clearance,
        "findings": [finding.to_dict() for finding in report.findings],
        "excluded": [pair.to_dict() for pair in report.excluded],
        "unchecked": [pair.to_dict() for pair in report.unchecked],
        "detail": report.summary(),
    }


def analysis(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Mass properties for the assembly; a named refusal for the other four kinds.

    `MassRollup.to_payload` is what decides whether there is a mass at all: a
    component whose material was never set has a volume and no mass, and the roll-up
    publishes no `mass_kg` for the assembly rather than a lighter one. An assertion
    then comes back UNMEASURED, which is the honest verdict and the whole reason the
    roll-up is used here instead of a sum written in this module.
    """
    state = _held(context, ANALYSIS)
    kind = str(arguments.get("kind") or "mass")
    refusal = _ANALYSIS_REFUSALS.get(kind)
    if refusal is not None:
        raise GeometryError(
            f"{ANALYSIS}: kind={kind!r} is not answerable for an assembly composed "
            f"here — {refusal}. kind='mass' is."
        )
    if kind != "mass":
        raise GeometryError(f"{ANALYSIS}: kind={kind!r} is not a kind this backend knows.")

    structure = _structure(state, ANALYSIS)

    from app.assembly.mass import from_document, roll_up

    if arguments.get("component"):
        wanted = _segment(arguments["component"], what="a component name", tool=ANALYSIS)
        if wanted not in state.documents:
            known = ", ".join(sorted(state.documents)) or "none"
            raise GeometryError(
                f"{ANALYSIS}: no component called {wanted!r} in {state.name!r}. "
                f"Recorded: {known}."
            )
        measured = state.documents[wanted].measure(detail=Detail.FULL)
        return {
            **_overview(state, structure),
            "component": wanted,
            "occurrences_of_component": len(structure.paths_of(wanted)),
            **measured,
        }

    _require_placements(state, ANALYSIS)
    rollup = roll_up(structure, from_document(state.documents))
    return {
        **_overview(state, structure),
        **rollup.to_payload(),
        "by_component": rollup.by_component(),
        "heaviest": [item.to_dict() for item in rollup.heaviest()],
        "missing": [item.to_dict() for item in rollup.missing],
        "detail": rollup.summary(),
    }


__all__ = [
    "ANALYSIS",
    "BILL_OF_MATERIALS",
    "CLASH",
    "COMPONENT",
    "MUTATING",
    "PLACE",
    "PRODUCT_CREATE",
    "AssemblyState",
    "Placed",
    "analysis",
    "bill_of_materials",
    "clash",
    "component",
    "place",
    "product_create",
]
