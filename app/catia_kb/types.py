"""The one record type the whole knowledge base is made of, and how to write it.

Every workbench, command, dialog field, file format, error message, aerospace
term, workflow and API object in this package is the same `Entry`. That is a
deliberate choice: the recogniser, the brief renderer and the query expander are
each one code path over one shape, so adding a new *category* of knowledge later
costs a data module and nothing else.

The optional fields are exactly the answer-quality requirements the assistant is
held to -- workbench path, toolbar, menu, icon description, dialog fields,
preconditions, licence tier, failure modes, alternatives, aerospace note. A
field left empty means "not recorded", never "not applicable": the renderer
omits it rather than asserting something.

**Two ways to write an entry.** The long-tail vocabulary -- the several hundred
command names a user might type that need recognising and locating but not
explaining in depth -- is written in a compact line format parsed by `bulk`:

    Pad | pad, extrude, protrusion, extrusion (FR) | Extrudes a closed profile

which is one line instead of a twelve-line literal, and reviewable in a diff.
Commands worth answering about in full get the `command()` constructor with
every field spelled out. Both produce identical `Entry` objects; nothing
downstream can tell which was used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Final, Iterable, Iterator, Sequence


class Kind(str, Enum):
    """What a record *is*, which decides how it is rendered and searched."""

    WORKBENCH = "workbench"
    COMMAND = "command"
    #: A native or neutral file format, and how CATIA reads or writes it.
    FORMAT = "format"
    #: A `Tools > Options` page, an environment variable, a startup switch.
    SETTING = "setting"
    #: An error message, a symptom, a performance problem, and its diagnosis.
    DIAGNOSTIC = "diagnostic"
    #: Domain vocabulary: airframe parts, composites terms, GD&T characteristics.
    TERM = "term"
    #: A multi-step process the assistant should recognise from a description.
    WORKFLOW = "workflow"
    #: Methodology: what to do, why, and what it costs when you do not.
    PRACTICE = "practice"
    #: A licence tier, a configuration, a product trigram.
    LICENCE = "licence"
    #: An automation object, method or language.
    API = "api"


#: Kinds whose names are ordinary English words often enough that matching them
#: unanchored produces noise. Entries of these kinds still match on a full alias,
#: but a bare one-word alias from them is only honoured when it is unambiguous.
_NOISY_KINDS: Final = frozenset({Kind.TERM, Kind.PRACTICE})


@dataclass(frozen=True, slots=True)
class Entry:
    """One thing a user can name, and everything worth saying about it.

    `key` is stable and is what `see_also` points at, so renaming the display
    name never breaks a cross-reference. Everything except `key`, `name` and
    `kind` is optional and is omitted from output when empty.
    """

    key: str
    name: str
    kind: Kind

    #: Every other way a user writes this: abbreviations, the French interface
    #: name, the common misname, the "button that looks like" description. The
    #: canonical name is added automatically, so it is never repeated here.
    aliases: tuple[str, ...] = ()

    #: One line. What it is, in the words an engineer would use.
    summary: str = ""

    #: For a command: the key of the workbench it lives in.
    workbench: str = ""
    #: The toolbar it sits on inside that workbench.
    toolbar: str = ""
    #: Full menu path, e.g. `Insert > Dress-Up Features > Edge Fillet`, or for a
    #: workbench the Start-menu path that opens it.
    menu: str = ""
    #: What the icon looks like, because that is how users describe commands
    #: they cannot name: "the button with the cube and one rounded edge".
    icon: str = ""
    #: Keyboard accelerator, where one exists by default.
    shortcut: str = ""

    #: Dialog fields and their options, one per entry.
    fields: tuple[str, ...] = ()
    #: What must already exist or be selected before this can run.
    needs: tuple[str, ...] = ()
    #: How it fails, and what the failure means. For a diagnostic, the causes.
    failures: tuple[str, ...] = ()
    #: What to do about a failure. For a diagnostic, the fix.
    fixes: tuple[str, ...] = ()
    #: Other commands that reach the same result, and the trade-off.
    alternatives: tuple[str, ...] = ()

    #: How this is used on an airframe, when that differs from general practice.
    aerospace: str = ""
    #: Licence tier and product trigram, e.g. `P2 -- Part Design 2 (PDG)`.
    licence: str = ""
    #: Release caveat, e.g. `R2016x and later` or `renamed in V5R21`.
    versions: str = ""
    #: Keys of related entries.
    see_also: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key or not self.name:
            raise ValueError(f"entry needs a key and a name, got {self.key!r}/{self.name!r}")

    # -- surfaces -----------------------------------------------------------

    def surfaces(self) -> tuple[str, ...]:
        """Every string that should match this entry, canonical name first."""
        seen: dict[str, None] = {self.name: None}
        for alias in self.aliases:
            seen.setdefault(alias, None)
        return tuple(seen)

    def noisy(self) -> bool:
        """Whether a single-word alias from this entry needs disambiguation."""
        return self.kind in _NOISY_KINDS

    def location(self) -> str:
        """Where the user has to go, as one readable phrase, or ''."""
        if self.menu:
            return self.menu
        parts = [p for p in (WORKBENCH_NAMES.get(self.workbench, ""), self.toolbar) if p]
        return " > ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Only the fields that carry something. Empty fields are not reported.

        A tool result that says `"aerospace": ""` invites the model to state that
        there is no aerospace application, which is a claim this package never
        makes -- an unrecorded field means unrecorded, not absent.
        """
        out: dict[str, Any] = {"name": self.name, "kind": self.kind.value}
        for name in (
            "summary",
            "toolbar",
            "menu",
            "icon",
            "shortcut",
            "aerospace",
            "licence",
            "versions",
        ):
            value = getattr(self, name)
            if value:
                out[name] = value
        if self.workbench:
            out["workbench"] = WORKBENCH_NAMES.get(self.workbench, self.workbench)
        for name in ("fields", "needs", "failures", "fixes", "alternatives", "see_also"):
            value = getattr(self, name)
            if value:
                out[name] = list(value)
        if self.aliases:
            out["also_called"] = list(self.aliases)
        return out


#: Filled by `workbenches.py` at import so `Entry.location` can name a workbench
#: from its key without importing the module that defines it -- which would be a
#: cycle, since every command module imports this one.
WORKBENCH_NAMES: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Writing entries.
# ---------------------------------------------------------------------------

_SLUG_RE: Final = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    """A stable key fragment from a display name."""
    return _SLUG_RE.sub("_", text.lower()).strip("_")


def command(
    name: str,
    *,
    workbench: str,
    toolbar: str = "",
    aliases: Sequence[str] = (),
    summary: str = "",
    menu: str = "",
    icon: str = "",
    shortcut: str = "",
    fields: Sequence[str] = (),
    needs: Sequence[str] = (),
    failures: Sequence[str] = (),
    fixes: Sequence[str] = (),
    alternatives: Sequence[str] = (),
    aerospace: str = "",
    licence: str = "",
    versions: str = "",
    see_also: Sequence[str] = (),
) -> Entry:
    """One fully described command. The key is derived from workbench and name."""
    return Entry(
        key=f"{workbench}.{slug(name)}",
        name=name,
        kind=Kind.COMMAND,
        workbench=workbench,
        toolbar=toolbar,
        aliases=tuple(aliases),
        summary=summary,
        menu=menu,
        icon=icon,
        shortcut=shortcut,
        fields=tuple(fields),
        needs=tuple(needs),
        failures=tuple(failures),
        fixes=tuple(fixes),
        alternatives=tuple(alternatives),
        aerospace=aerospace,
        licence=licence,
        versions=versions,
        see_also=tuple(see_also),
    )


def entry(
    key: str,
    name: str,
    kind: Kind,
    *,
    aliases: Sequence[str] = (),
    summary: str = "",
    **rest: Any,
) -> Entry:
    """A non-command entry: a format, a setting, a diagnostic, a term.

    Sequence fields are frozen to tuples on the way in, so a data module can
    write a list without silently producing an `Entry` that is only mostly
    immutable.
    """
    frozen: dict[str, Any] = {
        field_name: tuple(value) if isinstance(value, (list, tuple)) else value
        for field_name, value in rest.items()
    }
    return Entry(
        key=key,
        name=name,
        kind=kind,
        aliases=tuple(aliases),
        summary=summary,
        **frozen,
    )


#: Splits `Name | alias, alias | summary`. A backslash escapes a literal pipe,
#: which a few CATIA names genuinely contain in their dialog text.
_BULK_SPLIT: Final = re.compile(r"(?<!\\)\|")


def _parse_line(line: str) -> tuple[str, tuple[str, ...], str]:
    parts = [p.replace("\\|", "|").strip() for p in _BULK_SPLIT.split(line)]
    name = parts[0]
    raw_aliases = parts[1] if len(parts) > 1 else ""
    summary = parts[2] if len(parts) > 2 else ""
    aliases = tuple(a.strip() for a in raw_aliases.split(",") if a.strip())
    return name, aliases, summary


def bulk(
    spec: str,
    *,
    kind: Kind = Kind.COMMAND,
    workbench: str = "",
    toolbar: str = "",
    prefix: str = "",
    licence: str = "",
    aerospace: str = "",
) -> list[Entry]:
    """Parse the compact line format into entries.

    Blank lines and `#` comments are skipped, so a block can be annotated with
    why a group of commands belongs together without those notes reaching the
    index.

    `prefix` overrides the key namespace for kinds that have no workbench --
    formats, terms, diagnostics -- and defaults to the workbench key otherwise.
    Duplicate names inside one call are a mistake worth failing on: two entries
    with the same key silently shadow each other in the registry, and the one
    that wins depends on import order.
    """
    namespace = prefix or workbench or "misc"
    out: list[Entry] = []
    seen: set[str] = set()
    for raw in spec.strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, aliases, summary = _parse_line(line)
        if not name:
            continue
        key = f"{namespace}.{slug(name)}"
        if key in seen:
            raise ValueError(f"duplicate entry {key!r} in one bulk block")
        seen.add(key)
        out.append(
            Entry(
                key=key,
                name=name,
                kind=kind,
                aliases=aliases,
                summary=summary,
                workbench=workbench,
                toolbar=toolbar,
                licence=licence,
                aerospace=aerospace,
            )
        )
    return out


def with_defaults(entries: Iterable[Entry], **defaults: Any) -> list[Entry]:
    """Apply field defaults to entries that left those fields empty.

    Lets a module set `licence=` once for a whole workbench instead of on every
    line, without overriding the entries that say something more specific.
    """
    out: list[Entry] = []
    for item in entries:
        changes = {
            name: value for name, value in defaults.items() if not getattr(item, name, None)
        }
        out.append(replace(item, **changes) if changes else item)
    return out


@dataclass(frozen=True, slots=True)
class Disambiguation:
    """Two or more things a single word legitimately means.

    The specification this package implements is explicit that the assistant
    must never blur SMD with ASL, GSD with WSF, or GPS with GAS. A term that
    lands here is answered by *naming the fork*, not by picking a side.
    """

    term: str
    aliases: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    guidance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "means_any_of": list(self.options),
            "how_to_tell": self.guidance,
        }


@dataclass(slots=True)
class Section:
    """One data module's contribution, so the registry can report provenance."""

    name: str
    entries: list[Entry] = field(default_factory=list)
    disambiguations: list[Disambiguation] = field(default_factory=list)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)
