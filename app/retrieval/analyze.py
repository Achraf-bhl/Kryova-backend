"""Turning text into the terms an index is built and queried on.

The analyzer is the single most important choice in a lexical index: two
documents match only if they produce a term in common, so anything the analyzer
throws away is a match that can never happen, and anything it fails to
normalise is a match that silently misses.

This corpus is CATIA and FEA reference material, which makes three demands that
a stock English analyzer gets wrong:

**It is bilingual, in the same index.** The manuals are half French, half
English, and a user asks in either language. Running two per-language indexes
would need language detection at query time -- on a five-word query, which is
exactly where detection is least reliable. Instead one analyzer handles both:
the stopword list is the union, and accent folding maps `épaisseur` and
`epaisseur` onto one term, which also rescues the very common case of a French
query typed without accents.

**Its vocabulary is identifiers, not prose.** `M6`, `R18`, `V5R21`, `tet4`,
`120x80` and `Ø12` are the terms that actually discriminate between passages,
and every one of them is destroyed by a naive `\\w+` split or by aggressive
stemming. They are preserved deliberately, and a dimension like `120x80x10` is
additionally split into its components so a query for `120 mm` still reaches it.

**Its jargon must not be stemmed into collision.** Porter-style stemming maps
`pocket` and `pockets` together, which is wanted, but it also happily maps
distinct CAD features onto one root. The stemmer here is deliberately a light
suffix-stripper -- plurals and the handful of French verb/noun endings that
matter -- rather than a full algorithmic stemmer. Under-stemming costs a little
recall; over-stemming costs precision on the exact terms that carry the query,
and in a technical corpus that is the worse trade.

Everything is pure Python over the standard library. No NLTK, no spaCy, no
model download: the analyzer must run identically on a developer's laptop, in
CI, and on a machine with no network at all.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

#: Terms shorter than this carry no retrieval signal on their own *unless* they
#: contain a digit -- `M6`, `Ø8` and `R5` are exactly the two-character terms
#: this corpus is full of, so the digit test is what keeps them.
MIN_TERM_LENGTH: Final = 2

#: Upper bound on a single term. Anything longer is a run-together artefact of
#: PDF extraction (a table with no spaces, a URL, a base64 blob) and indexing it
#: only inflates the vocabulary with terms no query will ever produce.
MAX_TERM_LENGTH: Final = 40

#: English and French stopwords in one list, because the index is one index.
#:
#: Deliberately conservative. A stopword list is a recall risk in a technical
#: corpus: `no` and `not` are stopwords in every stock list and are also the
#: difference between "the constraint is applied" and "the constraint is not
#: applied". Words that could carry engineering meaning are left in, and the
#: cost of that is a few percent of index size, which is nothing here.
STOPWORDS: Final[frozenset[str]] = frozenset(
    """
    a about above after again against all am an and any are as at
    be because been before being below between both but by
    can cannot could did do does doing down during
    each few for from further had has have having he her here hers herself him
    himself his how i if in into is it its itself
    just me more most my myself of off on once only or other our ours ourselves
    out over own same she should so some such than that the their theirs them
    themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why
    will with you your yours yourself yourselves

    au aux avec ce ces dans de des du elle en et eux il ils je la le les leur
    lui ma mais me meme mes moi mon ne nos notre nous on ou par pas pour qu que
    qui sa se ses son sur ta te tes toi ton tu un une vos votre vous y
    est sont etre ete avoir avait ainsi alors donc dont apres avant chaque
    comme cet cette celui ceux tout tous toute toutes plus moins tres
    """.split()
)

#: Tokens are runs of letters, digits and the few symbols that belong *inside* a
#: technical term. The diameter sign is one of them: `Ø12` is a term, and a
#: split on non-alphanumerics would leave the bare `12`.
_TOKEN_RE: Final = re.compile(r"[0-9Ø⌀]?[a-z0-9Ø⌀]*[a-z0-9]|[a-z]+", re.UNICODE)

#: A dimension written the way a drawing writes it: `120x80`, `120x80x10`,
#: `M6x20`. Split into components so a query naming one number still reaches it.
_DIMENSION_RE: Final = re.compile(r"^(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))+$")

#: A term that is a letter prefix followed by digits -- `M6`, `R18`, `V5R21`,
#: `tet4`. Indexed whole *and* split, so `tet4` is reachable by `tet` too.
_ALNUM_SPLIT_RE: Final = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")

#: English suffixes stripped, longest first. Order matters: `ing` must be tried
#: before `s` or `running` becomes `running` minus nothing useful.
_EN_SUFFIXES: Final = ("ements", "ement", "ings", "ing", "ies", "ers", "er", "es", "s")

#: French suffixes. `ements`/`ement` are shared with English above and are not
#: repeated. These are the endings that actually appear in CATIA's French
#: manuals -- plurals and the nominalising `-tion`/`-ure` families.
_FR_SUFFIXES: Final = ("ations", "ation", "ures", "ure", "eurs", "eur", "aux")

#: Words whose ending merely *looks* like a suffix. Stripping it produces a
#: stem that collides with an unrelated term, which is the failure mode this
#: stemmer is built to avoid: `axes` -> `ax` would merge with nothing useful,
#: and `bosses` -> `boss` is right while `stress` -> `stres` is wrong.
_NEVER_STEM: Final[frozenset[str]] = frozenset(
    """
    stress process access axis analysis basis mass class cross gauss less
    plus status radius modulus focus bonus minus
    series species
    ansys catia gmsh
    """.split()
)


def fold(text: str) -> str:
    """Lowercase and strip accents, leaving the base letters.

    NFKD splits an accented character into its base plus a combining mark; the
    marks are then dropped. This is what makes `épaisseur`, `EPAISSEUR` and
    `epaisseur` one term, which matters twice over: the manuals are
    inconsistently accented after PDF extraction, and users routinely type
    French without accents.
    """
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def stem(term: str) -> str:
    """Strip one plural or nominalising suffix, conservatively.

    At most one suffix is removed and the result must still be a substantial
    word (four characters), so short technical terms survive intact. A term on
    the never-stem list is returned unchanged -- see `_NEVER_STEM` for why that
    list exists.
    """
    if term in _NEVER_STEM or len(term) <= 4 or any(char.isdigit() for char in term):
        return term
    for suffix in (*_EN_SUFFIXES, *_FR_SUFFIXES):
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[: -len(suffix)]
    return term


def _expand(token: str) -> list[str]:
    """A raw token plus the extra terms it should also be findable by.

    Only ever *adds*. The token itself is always kept, so an exact query for
    `120x80` still matches exactly and simply outscores a query for `120`,
    which now also matches.
    """
    terms = [token]

    dimension = _DIMENSION_RE.match(token)
    if dimension:
        terms.extend(part for part in re.split(r"x", token) if part)
        return terms

    if any(char.isdigit() for char in token) and any(char.isalpha() for char in token):
        parts = [part for part in _ALNUM_SPLIT_RE.split(token) if len(part) >= MIN_TERM_LENGTH]
        # `M6` splits to `m` and `6`, both below the length floor, so the guard
        # above leaves `parts` empty and the whole term is kept as-is. That is
        # the intent: `M6` is the term, not `m` and `6`.
        terms.extend(parts)

    return terms


def analyze(text: str, *, stem_terms: bool = True) -> list[str]:
    """Text in, index terms out, in document order.

    Order is preserved because callers use it: the proximity signal in
    `bm25.py` needs to know which terms landed near each other, and a set would
    throw that away along with the term frequencies BM25 is built on.

    `stem_terms=False` exists for tests and for diagnostics, where seeing the
    surface form is the whole point.
    """
    if not text:
        return []

    folded = fold(text)
    terms: list[str] = []

    for token in _TOKEN_RE.findall(folded):
        if len(token) > MAX_TERM_LENGTH:
            continue
        for term in _expand(token):
            if len(term) > MAX_TERM_LENGTH:
                continue
            # A term is worth indexing if it is long enough, or if it contains a
            # digit -- which is what keeps `M6`, `R5` and `Ø8`.
            if len(term) < MIN_TERM_LENGTH and not any(c.isdigit() for c in term):
                continue
            if term in STOPWORDS:
                continue
            terms.append(stem(term) if stem_terms else term)

    return terms


def analyze_query(text: str) -> list[str]:
    """Analyze a query the way the index was built.

    A separate name rather than a bare `analyze` call at every call site: query
    and document analysis drifting apart is the classic way a lexical index
    starts returning nothing, and giving the query path its own function is what
    makes that drift visible in a diff.
    """
    return analyze(text, stem_terms=True)
