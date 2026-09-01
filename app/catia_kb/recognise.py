"""Finding the CATIA entities a user named, however they named them.

The hard part is not matching; it is *not* matching. This vocabulary contains
`fit`, `web`, `run`, `add`, `bead`, `clip`, `plate`, `corner`, `table` and
`command`, all of which are ordinary English words. An index that matches them
unanchored turns "I need to fit this in the corner" into a lecture about DMU
Fitting Simulation and Sketcher's Corner command. Four rules keep that from
happening:

**Longest match wins, and it wins exclusively.** `edge fillet` is matched as one
term and the tokens it consumed are not reconsidered, so it never also produces
a bare `fillet` and a bare `edge`.

**Common words need corroboration.** A one-word alias that is also ordinary
English is held back until something else in the message establishes that the
subject is CATIA. `Add` in "how do I add an edge fillet" is then correctly not
the boolean Add operation, because by the time it is reconsidered the tokens it
would have used are already spoken for.

**Product codes need their capitals.** `PIP` is Piping Design and `pip` is a
Python tool; `FIT`, `GAS`, `EST`, `TUB`, `KIN` and `CUT` have the same problem.
Codes whose lowercase form is an ordinary word are only read as codes when the
raw text has them upper case. Codes with no such collision -- `GSD`, `ASL`,
`CPD` -- are matched either way, because users type them in lower case
constantly and nothing else claims those letters.

**Fuzzy matching is last, narrow, and length-gated.** `Genrative Shape Desing`
should still reach GSD; `generate` should not. So it runs only on what matched
nothing exactly, only over surfaces of six characters or more, and at a high
cutoff -- for single tokens and for two- to four-word spans alike, since a
misspelt workbench name is usually misspelt in the middle of its phrase.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Iterable, Sequence

from app.catia_kb.licensing import INFORMAL_TRIGRAMS, TRIGRAMS
from app.catia_kb.registry import Registry, _fold, registry
from app.catia_kb.types import Entry, Kind

#: Two tiers, kept disjoint, and the split is the whole design.
#:
#: `NEVER_BARE` is ordinary English that happens to collide with a CATIA name.
#: `Add` is the boolean operation and the commonest verb in the language;
#: `part`, `view`, `table`, `source`, `fit` and `box` are the same story. These
#: never match on their own, however strong the surrounding context, because
#: "can you add a note" is never a question about boolean operations. Every
#: entry they would have reached stays reachable through a phrase -- the boolean
#: Add answers to "boolean add" and "union" -- so nothing becomes unfindable.
NEVER_BARE: Final[frozenset[str]] = frozenset(
    """
    add remove insert delete change edit modify create make build open close
    start stop run set get find test move copy paste place replace group text
    table command action view part product source near law top box cut fit
    break search select check
    """.split()
)

#: `AMBIGUOUS_WORDS` is the middle tier: words that are genuinely CATIA terms
#: *and* genuinely ordinary. `web` is an ASL feature and a thing on the
#: internet; `frame` is a fuselage member and a picture frame. These are held
#: back until something else in the message establishes the subject.
#:
#: What is deliberately in *neither* list is the large set of words that are
#: nominally English but overwhelmingly CATIA in an engineering conversation:
#: `pocket`, `fillet`, `chamfer`, `sketch`, `joggle`, `stringer`, `loft`. Making
#: those need corroboration was tested and was wrong -- "how do I make a pocket"
#: is a question this assistant must answer, and it contains no other signal.
AMBIGUOUS_WORDS: Final[frozenset[str]] = frozenset(
    """
    web wall hole core zone frame skin plate corner line point plane circle arc
    axis body section detail measure scene layer sheet bead clip support signal
    pitch shim tape fabric contour sequence profile rectangle station material
    machine program setup wire bundle boundary bump twist bend crease smooth weld
    scale rotate translate split join fill extend extract activate deactivate
    isolate transfer develop trace replay revision insulation hanger configuration
    zoning stock spine flatten offset update junction bridge dowel recognize
    subdivide loop tool rule note
    """.split()
)

#: Kinds never accepted from a bare ambiguous word, even with corroboration. A
#: practice entry called "Common pitfalls" should be reached by asking about
#: pitfalls, not by using the word "check" somewhere in a sentence.
_CORROBORATION_EXEMPT: Final[frozenset[Kind]] = frozenset({Kind.PRACTICE, Kind.WORKFLOW})

#: A single word only establishes "this message is about CATIA" if it is long
#: enough and specific enough to be unmistakable. `joggle` qualifies; `box` does
#: not, and letting it qualify is how "in the corner of the box" turned into an
#: answer about Sketcher's Corner command.
_CORROBORATING_MIN_LENGTH: Final = 5

#: Codes whose lowercase spelling is an ordinary English word or a well-known
#: tool. These require capitals in the raw text; every other code does not.
#: Hand-listed rather than checked against a dictionary, because the package
#: carries no word list and must behave identically offline.
_CASE_SENSITIVE_CODES: Final[frozenset[str]] = frozenset(
    """
    fit pip tub kin gas est com cut cad cam api ram tag top tip map run add
    mas sam sap sac pas pad mid sip sis sea spa sun dom den cid ill fem
    """.split()
)

_TOKEN_RE: Final = re.compile(r"[^\W_]+", re.UNICODE)

#: How close a fuzzy candidate must be. High on purpose -- see the docstring.
_FUZZY_CUTOFF: Final = 0.88
_FUZZY_PHRASE_CUTOFF: Final = 0.85
_FUZZY_MIN_LENGTH: Final = 6
_FUZZY_PHRASE_MIN_LENGTH: Final = 9
_FUZZY_PHRASE_MAX_WORDS: Final = 4

#: An ambiguous surface can legitimately mean several things, but not eight.
#: Beyond this the brief becomes a list of everything and says nothing.
MAX_MEANINGS_PER_SURFACE: Final = 3


@dataclass(frozen=True, slots=True)
class Match:
    """One recognised entity and how it was recognised."""

    entry: Entry
    #: The user's own words that produced it, as they wrote them.
    surface: str
    #: "exact" (canonical name), "alias", "code" (product trigram), "fuzzy".
    how: str
    #: Token index in the message, so callers can preserve reading order.
    position: int
    #: True when the surface could mean more than one entry.
    ambiguous: bool = False

    @property
    def key(self) -> str:
        return self.entry.key


@dataclass(frozen=True, slots=True)
class Recognition:
    """Everything found in one message."""

    matches: tuple[Match, ...] = ()
    #: Surfaces that legitimately mean several things, normalised.
    forks: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.matches)

    def keys(self) -> list[str]:
        return [match.key for match in self.matches]

    def entries(self) -> list[Entry]:
        return [match.entry for match in self.matches]

    def of_kind(self, *kinds: Kind) -> list[Match]:
        wanted = set(kinds)
        return [match for match in self.matches if match.entry.kind in wanted]


#: Which kinds are most likely to be what the user meant, when a surface means
#: several things. Used to trim an over-broad surface rather than to hide
#: ambiguity: the fork is still reported.
_MEANING_RANK: Final[dict[Kind, int]] = {
    Kind.DIAGNOSTIC: 0,
    Kind.COMMAND: 1,
    Kind.WORKBENCH: 2,
    Kind.WORKFLOW: 3,
    Kind.SETTING: 4,
    Kind.FORMAT: 5,
    Kind.TERM: 6,
    Kind.API: 7,
    Kind.PRACTICE: 8,
    Kind.LICENCE: 9,
}


@lru_cache(maxsize=1)
def _fuzzy_candidates() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(single-word surfaces, multi-word surfaces)` long enough to fuzzy-match."""
    singles: list[str] = []
    phrases: list[str] = []
    for surface in registry().surfaces:
        if " " in surface:
            if len(surface) >= _FUZZY_PHRASE_MIN_LENGTH and surface.count(" ") < _FUZZY_PHRASE_MAX_WORDS:
                phrases.append(surface)
        elif len(surface) >= _FUZZY_MIN_LENGTH:
            singles.append(surface)
    return tuple(singles), tuple(phrases)


@lru_cache(maxsize=1)
def _code_surfaces() -> frozenset[str]:
    """Normalised surfaces that are product codes needing capitals."""
    codes = {*TRIGRAMS, *INFORMAL_TRIGRAMS}
    return frozenset(code.lower() for code in codes if code.lower() in _CASE_SENSITIVE_CODES)


def _spans(count: int, longest: int) -> Iterable[tuple[int, int]]:
    """Index ranges to try, longest first, then left to right."""
    for width in range(min(longest, count), 0, -1):
        for start in range(0, count - width + 1):
            yield start, start + width


def _worth_fuzzing(tokens: Sequence[str]) -> bool:
    """Whether a span is distinctive enough to be worth a near-miss search.

    A span made entirely of short and ordinary words is not a misspelt CATIA
    term, it is a sentence. Without this, `what is the` scores 0.86 against
    `what is this` and every question beginning "what is the ..." is answered
    with the What's This help command -- which is exactly what happened.
    """
    return any(
        len(token) >= _FUZZY_MIN_LENGTH
        and token not in NEVER_BARE
        and token not in AMBIGUOUS_WORDS
        for token in tokens
    )


def _trim(keys: Sequence[str], index: Registry) -> tuple[str, ...]:
    """Keep the most plausible meanings when a surface has many."""
    if len(keys) <= MAX_MEANINGS_PER_SURFACE:
        return tuple(keys)
    ranked = sorted(keys, key=lambda key: _MEANING_RANK.get(index.entries[key].kind, 99))
    return tuple(ranked[:MAX_MEANINGS_PER_SURFACE])


def recognise(
    text: str,
    *,
    limit: int = 12,
    assume_catia: bool = False,
    index: Registry | None = None,
) -> Recognition:
    """Find the CATIA entities named in `text`.

    `assume_catia` lowers the bar for ambiguous single words. Pass it when
    context has already established the subject -- a CATIA document is bound to
    the conversation, or an earlier turn matched something unmistakable. It
    never raises the bar, so a message full of unambiguous CATIA terms is
    recognised either way.

    `limit` caps the result. Order is by position in the message, so the cap
    keeps what the user mentioned first, which is almost always the subject.
    """
    if not text or not text.strip():
        return Recognition()

    reg = index or registry()
    raw_tokens = _TOKEN_RE.findall(text)
    if not raw_tokens:
        return Recognition()
    folded_tokens = [_fold(token) for token in raw_tokens]
    codes = _code_surfaces()

    taken = [False] * len(folded_tokens)
    found: dict[str, Match] = {}
    forks: list[str] = []

    # A message is "corroborated" once any multi-word or unambiguous surface has
    # matched: at that point a bare `web` really is likely to be the ASL Web.
    corroborated = assume_catia

    def claim(start: int, end: int, keys: Sequence[str], how: str) -> None:
        nonlocal corroborated
        surface = " ".join(raw_tokens[start:end])
        ambiguous = len(keys) > 1
        for key in keys:
            entry = reg.entries[key]
            found.setdefault(
                key,
                Match(entry=entry, surface=surface, how=how, position=start, ambiguous=ambiguous),
            )
        for position in range(start, end):
            taken[position] = True
        folded_surface = _fold(surface)
        # Only a strong match establishes the subject -- see the constant.
        if (
            end - start > 1
            or how == "code"
            or (
                len(folded_surface) >= _CORROBORATING_MIN_LENGTH
                and folded_surface not in AMBIGUOUS_WORDS
                and folded_surface not in NEVER_BARE
            )
        ):
            corroborated = True
        # A fork is worth reporting when the *index* is ambiguous, and also when
        # the disambiguation table says the term is -- `sheet metal` resolves to
        # one workbench here and still needs SMD-versus-ASL said out loud.
        folded_fork = _fold(surface)
        if (ambiguous or reg.disambiguation(folded_fork) is not None) and folded_fork not in forks:
            forks.append(folded_fork)

    # -- pass 1: longest exact n-gram over the normalised tokens -------------
    deferred: list[tuple[int, int, tuple[str, ...]]] = []
    for start, end in _spans(len(folded_tokens), reg.max_surface_words):
        if any(taken[start:end]):
            continue
        surface = " ".join(folded_tokens[start:end])
        keys = reg.surfaces.get(surface)
        if not keys:
            continue
        if end - start == 1:
            if surface in NEVER_BARE:
                continue
            if surface in codes and not raw_tokens[start].isupper():
                # `pip`, `fit`, `gas` in lower case are English, not products.
                continue
            if surface in AMBIGUOUS_WORDS:
                # Hold it back: something later in the message may corroborate
                # it, and deciding now would depend on scan order not content.
                deferred.append((start, end, _trim(keys, reg)))
                continue
        how = "exact" if _fold(reg.entries[keys[0]].name) == surface else "alias"
        claim(start, end, _trim(keys, reg), how)

    # -- pass 2: the deferred ambiguous words, now that context is known -----
    for start, end, keys in deferred:
        if any(taken[start:end]) or not corroborated:
            continue
        allowed = tuple(key for key in keys if reg.entries[key].kind not in _CORROBORATION_EXEMPT)
        if allowed:
            claim(start, end, allowed, "alias")

    # -- passes 3 and 4: fuzzy, but only once the subject is established.
    #
    # A typo in a CATIA question sits next to correctly spelled CATIA words --
    # "Genrative Shape Desing ... my loft", "the filet radius". A plain English
    # sentence has no such neighbour, and without this guard `document` scores
    # 0.94 against `Documents` and every sentence containing an ordinary long
    # word produces a match.
    #
    # The gate is "anything matched exactly", which is weaker than the
    # corroboration rule above on purpose: `loft` is too short to establish the
    # subject on its own, and is still plenty of evidence that the misspelt word
    # beside it is worth a second look.
    if not found:
        ordered = sorted(found.values(), key=lambda match: (match.position, match.key))
        return Recognition(matches=tuple(ordered[:limit]), forks=tuple(forks))

    singles, phrases = _fuzzy_candidates()
    for start, end in _spans(len(folded_tokens), _FUZZY_PHRASE_MAX_WORDS):
        if end - start < 2 or any(taken[start:end]):
            continue
        span_tokens = folded_tokens[start:end]
        span = " ".join(span_tokens)
        if len(span) < _FUZZY_PHRASE_MIN_LENGTH or not _worth_fuzzing(span_tokens):
            continue
        near = difflib.get_close_matches(span, phrases, n=1, cutoff=_FUZZY_PHRASE_CUTOFF)
        if near:
            keys = reg.surfaces.get(near[0])
            if keys:
                claim(start, end, _trim(keys, reg), "fuzzy")

    # -- pass 4: fuzzy over single tokens, for a misspelt word ---------------
    for position, token in enumerate(folded_tokens):
        if taken[position] or not _worth_fuzzing([token]):
            continue
        near = difflib.get_close_matches(token, singles, n=1, cutoff=_FUZZY_CUTOFF)
        if not near:
            continue
        keys = reg.surfaces.get(near[0])
        if keys:
            claim(position, position + 1, _trim(keys, reg), "fuzzy")

    ordered = sorted(found.values(), key=lambda match: (match.position, match.key))
    return Recognition(matches=tuple(ordered[:limit]), forks=tuple(forks))


def expand_query(text: str, *, max_added: int = 10, language: str | None = None) -> str:
    """Add the terms a lexical index needs to find what `text` is about.

    This is what makes an English question reach a French manual page. A user
    asking about "draft angle" gets `dépouille` added, so the French Part Design
    manual -- which never contains the phrase "draft angle" -- becomes reachable
    without anyone having translated the query.

    Only ever additive, and capped. The original words stay first and keep their
    weight; an expansion that doubled the query length would drown the terms the
    user actually chose, which is the classic way query expansion makes
    retrieval worse rather than better.
    """
    if not text or not text.strip():
        return text

    from app.catia_kb.languages import normalise_language, translations

    found = recognise(text, limit=6)
    if not found:
        return text

    already = {_fold(token) for token in _TOKEN_RE.findall(text)}
    prefer = normalise_language(language)
    added: list[str] = []

    for match in found.matches:
        entry = match.entry
        # The canonical English name first: it is what the English manuals use.
        for candidate in (entry.name, *entry.aliases[:2]):
            folded = _fold(candidate)
            if folded and folded not in already and len(folded) > 2:
                added.append(candidate)
                already.add(folded)
                break
        # Then the localised names, preferred language first, so the other half
        # of a bilingual corpus becomes reachable.
        table = translations(entry.key)
        codes = [prefer] if prefer and prefer in table else list(table)
        for code in codes:
            name = table.get(code or "")
            if not name:
                continue
            folded = _fold(name)
            if folded and folded not in already:
                added.append(name)
                already.add(folded)
        if len(added) >= max_added:
            break

    if not added:
        return text
    return f"{text} {' '.join(added[:max_added])}"
