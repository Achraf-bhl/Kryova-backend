"""Rendering entries for the two audiences that read them: a model, and a tool.

`describe` builds the structured payload the `explain_catia_term` tool returns.
`brief` builds the short text injected beside a user's turn, and it is
deliberately terse: it costs tokens on every turn it fires, so it carries the
things a model gets *wrong* from memory -- the workbench, the exact menu path,
the localised name, the licence -- and nothing it would get right anyway.

The localisation rule is enforced here rather than left to the caller: when a
language is asked for and no translation is recorded, the output says so. It
never presents the English name as though it were the German one.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.catia_kb.languages import (
    LANGUAGE_BY_CODE,
    localised,
    normalise_language,
    translations,
)
from app.catia_kb.recognise import Match, Recognition, recognise
from app.catia_kb.registry import registry
from app.catia_kb.types import Entry, Kind

#: Hard cap on the injected brief. A state block that grows without bound is a
#: context window that shrinks without anyone noticing.
MAX_BRIEF_ENTRIES = 5
MAX_BRIEF_CHARS = 1_200


def describe(entry: Entry, *, language: str | None = None) -> dict[str, Any]:
    """The full record for one entry, as a tool result.

    Adds the localisation block on top of `Entry.to_dict`, which is the one
    thing the dataclass cannot answer for itself.
    """
    payload = entry.to_dict()
    payload["key"] = entry.key

    where = entry.location()
    if where and "menu" not in payload:
        payload["where"] = where

    code = normalise_language(language)
    if code and code != "en":
        name = localised(entry.key, code)
        label = LANGUAGE_BY_CODE[code].label() if code in LANGUAGE_BY_CODE else code
        if name:
            payload["localised_name"] = {"language": label, "name": name}
        else:
            payload["localised_name"] = {
                "language": label,
                "name": None,
                "note": (
                    f"The {label} name for this is not recorded. Give the English name "
                    "and the menu position, which is the same in every language; do not "
                    "guess a translation."
                ),
            }
    table = translations(entry.key)
    if table and not code:
        payload["names_in_other_languages"] = table
    return payload


def _one_line(entry: Entry, *, language: str | None = None) -> str:
    """A single entry, compressed to one readable line.

    The workbench is named *and* the menu path is given, rather than one or the
    other. They answer different questions -- "where do I switch to" and "what
    do I click once I am there" -- and a menu path alone is the one a user
    cannot act on when they are in the wrong workbench.
    """
    bits: list[str] = [entry.name]

    name = localised(entry.key, language)
    if name:
        code = normalise_language(language) or ""
        bits.append(f'{code} "{name}"')

    index = registry()
    workbench = index.entries.get(entry.workbench) if entry.workbench else None
    if workbench is not None:
        bits.append(workbench.name)

    where = entry.location()
    if where and where not in bits:
        bits.append(where)

    if entry.licence:
        bits.append(entry.licence)

    line = " | ".join(bits)
    if entry.summary:
        line = f"{line} -- {entry.summary}"
    return line


def brief(
    found: Recognition | str,
    *,
    language: str | None = None,
    limit: int = MAX_BRIEF_ENTRIES,
) -> str:
    """A compact block naming what the user's words refer to. `''` when nothing did.

    Accepts either a `Recognition` or raw text, so callers that have already
    recognised do not pay for it twice.
    """
    if isinstance(found, str):
        found = recognise(found)
    if not found:
        return ""

    ranked = _rank(found.matches)[:limit]
    if not ranked:
        return ""

    lines = [f"- {_one_line(match.entry, language=language)}" for match in ranked]

    index = registry()
    for surface in found.forks[:2]:
        fork = index.disambiguation(surface)
        if fork is None:
            continue
        options = "; ".join(fork.options)
        lines.append(f'- "{fork.term}" is ambiguous: {options}. {fork.guidance}')

    body = "\n".join(lines)
    if len(body) > MAX_BRIEF_CHARS:
        body = body[:MAX_BRIEF_CHARS].rsplit("\n", 1)[0]
    return "CATIA terms in this message:\n" + body


#: Kinds worth spending brief lines on, most useful first. A workbench or a
#: command answers "where is it"; a diagnostic answers "why did it fail". A bare
#: vocabulary term rarely earns its tokens unless nothing better matched.
_KIND_RANK = {
    Kind.DIAGNOSTIC: 0,
    Kind.COMMAND: 1,
    Kind.WORKBENCH: 2,
    Kind.WORKFLOW: 3,
    Kind.SETTING: 4,
    Kind.FORMAT: 5,
    Kind.API: 6,
    Kind.TERM: 7,
    Kind.PRACTICE: 8,
    Kind.LICENCE: 9,
}


def _rank(matches: Sequence[Match]) -> list[Match]:
    """Most useful first, position breaking ties."""
    return sorted(
        matches,
        key=lambda m: (_KIND_RANK.get(m.entry.kind, 99), 0 if m.how != "fuzzy" else 1, m.position),
    )
