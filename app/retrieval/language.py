"""Which language a piece of text is in.

The manuals are bilingual today and the product is not: a user running CATIA in
French asks in French and wants the French manual, and the English translation
of the same chapter is a worse answer even when it scores higher. So each
passage records the language it is written in, and a search can prefer one.

**Detection is by function words, not by content words.** The technical
vocabulary is exactly what does *not* discriminate -- `pad`, `fillet`, `sketch`,
`CATIA` and every part name appear identically in both manuals, and a detector
that counted them would call every page bilingual. What differs reliably is the
grammar around them: `le`, `la`, `des`, `cliquez`, `sélectionnez` against `the`,
`and`, `click`, `select`. Those are frequent, short, and almost never shared.

**Two thresholds, both deliberate.** A text must be long enough to have a
grammar at all -- a heading like "Pad Definition" has no function words in
either language and is not evidence of anything. And the winner must lead by a
margin, because the tail of a French page is often an English product name and
the tail of an English page often carries French UI labels. Below either
threshold the answer is `None`, which every caller reads as "do not prefer
anything" -- the same behaviour the system had before languages existed.

Adding a language is adding a row to `_MARKERS`. A language with no row is
detected as `None` rather than mis-detected as one of these two, so the corpus
degrades to language-blind ranking for it rather than actively ranking it wrong.
"""

from __future__ import annotations

import re
from typing import Final

from app.retrieval.analyze import fold

#: Function words that reliably mark a language, and are not shared with the
#: others listed here. Deliberately short lists of very frequent words: length
#: buys nothing once the frequent ones are covered, and every added word is
#: another chance to collide with a neighbouring language.
#:
#: `_shared` below removes any word that appears in more than one row, so a
#: collision introduced by a future language is neutralised rather than silently
#: skewing both.
_MARKERS: Final[dict[str, frozenset[str]]] = {
    "en": frozenset(
        """
        the and you this that with from for are was were have has been will
        click select choose then when where which while their there these those
        into onto about after before during your not but all any each
        """.split()
    ),
    "fr": frozenset(
        """
        le la les un une des du de et ou dans pour avec sur sous par au aux
        est sont etait ete avoir cette cet ces vous nous ils elle elles
        cliquez selectionnez choisissez puis lorsque ainsi donc mais tout tous
        toute toutes plus moins entre chaque leur leurs qui que quoi dont
        """.split()
    ),
}

#: Words appearing in more than one marker set carry no signal and are dropped
#: from all of them. `de` and `des` are French here and would be safe, but a
#: future Spanish or Italian row shares both, and this is what stops that
#: addition from quietly degrading French detection.
_shared: Final[frozenset[str]] = frozenset(
    word
    for word, count in (
        (word, sum(word in markers for markers in _MARKERS.values()))
        for word in set().union(*_MARKERS.values())
    )
    if count > 1
)

_DISTINCTIVE: Final[dict[str, frozenset[str]]] = {
    code: markers - _shared for code, markers in _MARKERS.items()
}

#: Every language this module can name. Exposed so a caller can validate an
#: input without importing the tables.
SUPPORTED: Final[tuple[str, ...]] = tuple(sorted(_MARKERS))

#: Below this many words there is not enough grammar to judge. A passage under
#: the chunker's floor never reaches this, but a query does -- and a two-word
#: query is exactly where a confident guess would be wrong.
MIN_WORDS: Final = 8

#: The winner must hold this share of the marker hits. At 0.6 a page that is
#: three-quarters French and quotes an English dialog name still reads as
#: French, while a genuinely mixed page reads as neither.
MIN_SHARE: Final = 0.6

_WORD_RE: Final = re.compile(r"[a-z]+")


def detect(text: str) -> str | None:
    """The language of `text`, or `None` when it cannot be told.

    `None` is a first-class answer and by far the most common one for short
    input. Callers must treat it as "no preference", never as a default
    language: guessing English for an eight-word French query is worse than not
    guessing, because it actively demotes the right manual.
    """
    if not text:
        return None

    words = _WORD_RE.findall(fold(text))
    if len(words) < MIN_WORDS:
        return None

    seen = set(words)
    # Distinct markers rather than total occurrences: one `the` repeated forty
    # times in a table of contents is one piece of evidence, not forty.
    hits = {code: len(seen & markers) for code, markers in _DISTINCTIVE.items()}
    total = sum(hits.values())
    if total == 0:
        return None

    best = max(hits, key=lambda code: hits[code])
    if hits[best] / total < MIN_SHARE:
        return None
    return best


def detect_query(text: str, *, fallback: str | None = None) -> str | None:
    """The language of a search query, falling back to a known preference.

    Queries are short and often pure jargon (`fillet radius`, `depouille`), so
    `detect` refuses them most of the time -- which is correct, and useless on
    its own. `fallback` is what the caller already knows from elsewhere: the
    language CATIA's interface is running in, or the language the conversation
    has been held in. Detection only overrides it when it is confident.
    """
    return detect(text) or fallback


def normalise(code: str | None) -> str | None:
    """Reduce a locale to a language this module knows, or `None`.

    Accepts what real callers actually hold: `fr`, `fr-FR`, `fr_FR`, `French`,
    `EN`. Anything unrecognised becomes `None` rather than an error -- an
    unsupported CATIA interface language must degrade to language-blind
    ranking, not fail a search.
    """
    if not code:
        return None
    text = code.strip().lower().replace("_", "-")
    if not text:
        return None

    named = {"english": "en", "french": "fr", "francais": "fr", "français": "fr"}
    if text in named:
        return named[text]

    primary = text.split("-", 1)[0]
    return primary if primary in _MARKERS else None
