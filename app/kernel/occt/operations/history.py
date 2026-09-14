"""Taking a feature back out of a part: `catia_delete_feature` and `catia_feature_parents`.

**Why these exist, and the need was measured rather than assumed.** Driving the
GUI on 2026-09-11, the agent padded 200 mm where 10 was asked, said so itself
("the current part has wrong thickness"), and then had no way to act on it. With
no delete on the open kernel, a botched part could not be recovered inside the
conversation. The agent spent three calls on `catia_new_part` looking for a way
back, and the turn ended on an escalation. That is the trigger Decision 1 names
for adding an operation: a real conversation that could not finish.

`catia_feature_parents` comes with it because `catia_delete_feature`'s own
summary tells the agent to call it first. Offering the delete without it would
send the agent to a tool this backend does not offer, which is the vocabulary
gap `CLAUDE.md` *Testing* item 8 describes.

**A delete is a rebuild without the call.** The open kernel keeps no feature
tree. What it keeps is the build log `app.kernel.occt.operations.parameters`
already replays, so deleting a feature means replaying every call except the
ones that made it. The replay runs into a fresh document and is swapped in only
once it has finished. A delete that cannot be rebuilt therefore changes nothing,
and the refusal says which later call failed.

**Dependence is read from names, and that is its limit.** A call depends on a
feature when one of its arguments names it: `sketch="outline"`,
`feature="Pad.1"`, `"Pad.1#top"`. Those dependents are refused unless
`with_children` is true, and then they go too. What names cannot show is the
material a later cut or boss acted on. A pocket drawn on `XY` does not name the
pad it cut. So that dependence is caught by the rebuild instead. Remove the pad,
and the pocket that cut only the pad now removes no material; the kernel refuses
that, the rebuild fails, and the part stays as it was.

**Names do not move.** Replaying `Pad.2` into a document that never saw `Pad.1`
would allocate it `Pad.1`, and every later `"Pad.2#top"` would then resolve
against a different feature, or silently against the wrong one. So each call is
replayed under the number it was originally given, the rebuilt part keeps the
old document's counters, so a deleted number is not handed out again. A rebuild
that reports any name other than the one recorded is refused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from app import observe
from app.kernel.errors import GeometryError, OperationNotSupported
from app.kernel.occt.operations.context import BuildContext, JournalEntry

DELETE: Final = "catia_delete_feature"
PARENTS: Final = "catia_feature_parents"

#: Tools whose call *is* the thing it names, rather than something built on it.
#: A rename is bookkeeping on the feature it renames, and a compiled design
#: emits one after almost every feature, so counting it as a dependent would
#: refuse every delete of a named feature for no reason. It goes with its feature.
_PART_OF_ITS_TARGET: Final = frozenset({"catia_feature_rename"})

#: Tools that open a sketch. A sketch's name can be reused (an unnamed sketch is
#: always called `sketch`), so these are what decide which call a name means.
_OPENS_A_SKETCH: Final = frozenset({"catia_sketch_create"})

#: The prefix of every call that draws into a sketch. Such a call reports the
#: sketch it drew into as its `feature`, which is how it is told apart from a
#: call that merely names a sketch (a pad).
_DRAWS_IN_A_SKETCH: Final = "catia_sketch_"


@dataclass(frozen=True)
class _Found:
    """The calls that made one named thing, and everything that names it."""

    creator: int
    aliases: frozenset[str]
    own: tuple[int, ...]
    #: What it is called now: its last rename, else the name it was created under.
    current: str


def feature_parents(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """What a feature names, and what names it, read from the build log."""
    context.require_document()
    name = _required_name(arguments, PARENTS)
    depth = _depth(arguments.get("depth"))
    found = _find(context.journal, name)

    creator = context.journal[found.creator]
    parents = sorted(
        {
            _display(context.journal, index)
            for reference in _references(creator.arguments)
            if (index := _creator_of(context.journal, reference, before=found.creator))
            is not None
        }
    )

    levels: list[dict[str, Any]] = []
    frontier = [found]
    seen: set[int] = set(found.own)
    for level in range(1, depth + 1):
        next_frontier: list[_Found] = []
        for item in frontier:
            for index in _direct_dependents(context.journal, item):
                if index in seen:
                    continue
                seen.add(index)
                child = _as_found(context.journal, index)
                seen.update(child.own)
                next_frontier.append(child)
                levels.append(
                    {
                        "feature": _display(context.journal, index),
                        "tool": context.journal[index].tool,
                        "depth": level,
                    }
                )
        frontier = next_frontier
        if not frontier:
            break

    return {
        "feature": name,
        "tool": creator.tool,
        "parents": parents,
        "children": levels,
        "depth": depth,
        "note": (
            "Read from the calls that built this part: a call depends on a feature when "
            "it names it. A cut or boss also depends on the material that was already "
            "there, which it does not name, so that dependence is not listed. "
            "catia_delete_feature rebuilds the part without the feature and refuses if "
            "a later call no longer builds."
        ),
    }


def delete_feature(context: BuildContext, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Rebuild the part without one feature, and without its dependents if asked."""
    document = context.require_document()
    name = _required_name(arguments, DELETE)
    with_children = arguments.get("with_children") is True

    if name in document.body_names():
        raise OperationNotSupported(
            f"deleting the body {name!r}",
            "The features built in a body do not name it, so the build log cannot tell "
            "which calls belong to it. Delete its features one at a time instead",
        )

    found = _find(context.journal, name)
    if context.journal[found.creator].tool == "catia_new_part":
        raise GeometryError(
            f"{name!r} is the part itself, not a feature in it, so there is nothing to "
            "delete it from. To start again from nothing, call catia_assembly_component "
            "to put this part away, then catia_new_part."
        )

    removed = set(found.own)
    frontier = [found]
    while frontier:
        next_frontier: list[_Found] = []
        for item in frontier:
            for index in _direct_dependents(context.journal, item):
                if index in removed:
                    continue
                child = _as_found(context.journal, index)
                removed.update(child.own)
                next_frontier.append(child)
        frontier = next_frontier

    taken_with_it = _deleted_names(context.journal, removed - set(found.own))
    if taken_with_it and not with_children:
        # Every name that would go, not only the ones naming it directly. Told
        # "boss depends on it" alone, the agent deletes the boss sketch and is
        # refused again for the pad built on that sketch.
        raise GeometryError(
            f"{name!r} cannot be deleted on its own: {', '.join(taken_with_it)} "
            "depend on it and would have nothing to build on. Pass with_children: true "
            "to delete them too, or delete them first. Nothing was changed."
        )

    before = document.measure(detail=context.detail)
    deleted = _deleted_names(context.journal, removed)
    rebuilt = _rebuild_without(context, removed, deleted_label=name)

    context.document = rebuilt.document
    context.journal = rebuilt.journal

    after = rebuilt.document.measure(detail=context.detail) if rebuilt.document else {}
    return {
        "deleted": deleted,
        "calls_removed": len(removed),
        "features": rebuilt.document.feature_names() if rebuilt.document else [],
        "sketches": rebuilt.document.sketch_names() if rebuilt.document else [],
        "volume_before_mm3": before.get("volume_mm3"),
        **after,
    }


# -- reading the build log -----------------------------------------------------


def _required_name(arguments: Mapping[str, Any], tool: str) -> str:
    name = str(arguments.get("feature") or "").strip()
    if not name:
        raise GeometryError(
            f"{tool} needs the name of a feature or sketch. catia_list_features lists them."
        )
    return name


def _depth(value: Any) -> int:
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GeometryError(f"depth is a whole number of levels, 1 or more; got {value!r}.")
    return min(value, 10)


def _aliases_of(entry: JournalEntry) -> set[str]:
    """Every name one call's result is known by.

    A rename reports its target under the new name, so it shows up here too.
    That is harmless: `_find` takes the earliest call a name reaches, which is
    the feature, and `_as_found` counts the rename as part of it.
    """
    names = set()
    if entry.feature:
        names.add(entry.feature)
    given = entry.arguments.get("name")
    if isinstance(given, str) and given.strip():
        names.add(given.strip())
    return names


def _references(value: Any) -> Iterable[str]:
    """Every string a call's arguments hold, including inside lists and objects."""
    if isinstance(value, str):
        yield value
        if "#" in value:
            yield value.split("#", 1)[0]
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _references(item)


def _names_it(entry: JournalEntry, aliases: frozenset[str]) -> bool:
    return any(reference in aliases for reference in _references(entry.arguments))


def _find(journal: list[JournalEntry], name: str) -> _Found:
    """The call that created `name`, the calls that are part of it, and its names."""
    creators = [
        index
        for index, entry in enumerate(journal)
        if _aliases_of(entry) and name in _as_found(journal, index).aliases
    ]
    if not creators:
        known = sorted(
            {_as_found(journal, index).current for index in range(1, len(journal))}
            - {""}
        )
        raise GeometryError(
            f"No feature or sketch called {name!r} in this part. Built so far: "
            f"{', '.join(known) or 'nothing yet'}."
        )
    opened = [index for index in creators if journal[index].tool in _OPENS_A_SKETCH]
    if len(opened) > 1:
        raise GeometryError(
            f"{len(opened)} sketches in this part are called {name!r}, so the name does not "
            "say which one to delete. Give sketches their own names when you open them."
        )
    creator = opened[0] if opened else creators[0]
    return _as_found(journal, creator)


def _as_found(journal: list[JournalEntry], creator: int) -> _Found:
    """A thing's creating call, plus the later calls that are part of it.

    Two kinds are part of it rather than built on it: a rename of it, and, for a
    sketch, what was drawn into it. A rectangle is geometry inside the sketch
    and leaves no tree row of its own (`sketcher._add_profile`), so counting it
    as a dependent would refuse the delete of every sketch that has anything in
    it.
    """
    aliases = set(_aliases_of(journal[creator]))
    current = journal[creator].feature or ""
    is_sketch = journal[creator].tool in _OPENS_A_SKETCH
    own = [creator]
    for index in range(creator + 1, len(journal)):
        entry = journal[index]
        if is_sketch and entry.tool in _OPENS_A_SKETCH and _aliases_of(entry) & aliases:
            break  # the name now means a different sketch
        if entry.tool in _PART_OF_ITS_TARGET and entry.arguments.get("feature") in aliases:
            own.append(index)
            new_name = entry.arguments.get("name")
            if isinstance(new_name, str) and new_name:
                aliases.add(new_name)
                current = new_name
        elif is_sketch and entry.tool.startswith(_DRAWS_IN_A_SKETCH) and entry.feature in aliases:
            own.append(index)
    return _Found(
        creator=creator, aliases=frozenset(aliases), own=tuple(own), current=current
    )


def _direct_dependents(journal: list[JournalEntry], found: _Found) -> list[int]:
    """Later calls that name the thing, or add to it under its own name."""
    out = []
    for index in range(found.creator + 1, len(journal)):
        if index in found.own:
            continue
        entry = journal[index]
        if entry.tool in _OPENS_A_SKETCH and _aliases_of(entry) & found.aliases:
            break  # the name now means a different sketch
        if _names_it(entry, found.aliases) or (entry.feature in found.aliases):
            out.append(index)
    return out


def _creator_of(journal: list[JournalEntry], reference: str, *, before: int) -> int | None:
    for index in range(before - 1, -1, -1):
        entry = journal[index]
        if (
            _aliases_of(entry)
            and entry.tool != "catia_new_part"
            and reference in _as_found(journal, index).aliases
        ):
            return index
    return None


def _display(journal: list[JournalEntry], index: int) -> str:
    """What to call a call's result in a message: its current name, or its tool."""
    return _as_found(journal, index).current or journal[index].tool.removeprefix("catia_")


def _deleted_names(journal: list[JournalEntry], removed: set[int]) -> list[str]:
    names: list[str] = []
    for index in sorted(removed):
        entry = journal[index]
        if entry.tool in _PART_OF_ITS_TARGET:
            continue
        shown = _display(journal, index)
        if shown not in names:
            names.append(shown)
    return names


# -- rebuilding ------------------------------------------------------------------


def _numbered(name: str | None) -> tuple[str, int] | None:
    """`Pad.2` as `("Pad", 2)`, or None for a name that is not a counter's."""
    if not name or "." not in name:
        return None
    stem, _, number = name.rpartition(".")
    return (stem, int(number)) if stem and number.isdigit() else None


def _rebuild_without(context: BuildContext, removed: set[int], *, deleted_label: str) -> Any:
    from app.kernel.occt.operations import HANDLERS

    old = context.document
    rebuilt = BuildContext(detail=context.detail)
    kept = [entry for index, entry in enumerate(context.journal) if index not in removed]
    with observe.span("kernel.rebuild", operations=len(kept)):
        for entry in kept:
            handler = HANDLERS.get(entry.tool)
            if handler is None:  # pragma: no cover - the journal only holds handled tools
                continue
            numbered = _numbered(entry.feature)
            if numbered is not None and rebuilt.document is not None:
                rebuilt.document.continue_numbering(numbered[0], numbered[1] - 1)
            try:
                result = handler(rebuilt, dict(entry.arguments))
            except GeometryError as exc:
                raise GeometryError(
                    f"Without {deleted_label!r}, the part no longer builds at "
                    f"{entry.tool} ({entry.feature or 'unnamed'}): {exc} Nothing was "
                    "deleted; the part is exactly as it was. That call depends on the "
                    "material the deleted feature made, even though it does not name it."
                ) from exc
            rebuilt.record(entry.tool, entry.arguments, result)
            reported = rebuilt.journal[-1].feature
            if entry.feature and reported != entry.feature:
                raise GeometryError(
                    f"Rebuilding without {deleted_label!r} would rename {entry.feature!r} to "
                    f"{reported!r}, and every later reference to it would then point at "
                    "something else. Nothing was deleted."
                )
    if rebuilt.document is not None and old is not None:
        rebuilt.document.adopt_numbering(old)
    return rebuilt


__all__ = ["DELETE", "PARENTS", "delete_feature", "feature_parents"]
