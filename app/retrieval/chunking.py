"""Cutting extracted pages into the passages that actually get retrieved.

The unit of retrieval is not the document. A 400-page CATIA manual is a single
"document" only in the filesystem sense: indexing it whole means every query
matches it, term frequencies are meaningless, and the passage handed to the
model is 400 pages long. Cutting it into passages is what makes the answer
specific.

Passage size is a real trade-off with a narrow sweet spot. Too small and BM25
loses the term statistics it scores on -- a twenty-word passage has no
meaningful term frequency and the length normalisation starts fighting the
signal. Too large and a passage matches for reasons scattered across material
that has nothing to do with each other, and the model gets three paragraphs of
noise around the one sentence it needed. Roughly 180 words is where passage
retrieval settles for prose like this.

**Passages overlap.** A procedure split mid-step is a passage that answers half
a question and a passage that answers the other half, and neither scores well
enough to be retrieved. The overlap means every sentence appears in two
passages, so a sentence near a boundary is still findable with its context.

**Headings are carried, not discarded.** A passage under "Creating a Pocket"
that never repeats the word "pocket" is still about pockets, and the heading is
the only thing that says so. It is prepended to the passage text -- so it is
indexed -- and kept separately, so a citation can name the section rather than
just the page.

Heading detection is heuristic and deliberately cautious. A false positive
costs one mislabelled passage; being too eager would relabel body text as a
section title and poison the boost for everything under it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.retrieval.analyze import analyze
from app.retrieval.extract import Page

#: Target passage length in words. See the module docstring for why this
#: number: below ~120 BM25's term statistics degrade, above ~300 passages stop
#: being about one thing.
DEFAULT_CHUNK_WORDS: int = 180

#: Words repeated from the tail of the previous passage into the head of the
#: next. About a fifth of a passage -- enough to carry a procedure step across
#: a boundary, not so much that the index doubles in size.
DEFAULT_OVERLAP_WORDS: int = 40

#: A passage shorter than this is dropped. Below twenty words a passage is a
#: page header, a figure caption or a stray table cell: it will occasionally
#: outrank real content on a short query, because a two-word passage matching
#: one term looks perfectly on-topic to the length normalisation.
MIN_CHUNK_WORDS: int = 20

#: How many times a heading's terms are counted when indexing a passage. A
#: passage whose *heading* says "Pocket" is much stronger evidence than one
#: that mentions the word once in passing, so the heading's terms are repeated
#: into the term stream.
#:
#: Repeating the heading terms rather than scaling the whole passage is the
#: difference between BM25F and a boost that does nothing useful: scaling every
#: term frequency in a headed passage raises its score for *any* query, on-topic
#: or not, which just says "headed passages are better". Repeating only the
#: heading raises it for queries that match the heading, which is the claim
#: actually being made.
HEADING_TERM_REPEAT: int = 3

#: A numbered section heading: `3.2 Creating a Pad`, `Chapter 4 - Assembly`.
#:
#: Deliberately strict, and this is the single most tuned expression in the
#: module. These manuals are almost entirely numbered procedures, so the
#: obvious pattern -- a leading number and some text -- matches `6. Exit the
#: Sketcher workbench` on every page and labels thousands of instruction steps
#: as section titles. Two forms are accepted and a bare single-level number is
#: not: an explicit keyword (`Chapter 4`), or a multi-level number (`3.2`),
#: which a procedure step never has.
_NUMBERED_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"(?:chapter|chapitre|section|annexe|appendix|part|partie)\s+\d+(?:\.\d+)*\.?"
    r"|\d+\.\d+(?:\.\d+)*\.?"
    r")\s+\S.{0,70}$",
    re.IGNORECASE,
)

#: A line that opens with a step number -- `6.`, `15.`, `2)`. Never a heading,
#: whatever the rest of it looks like.
#:
#: This is the *rejecting* half of the numbered-heading logic, and it is not
#: redundant with `_NUMBERED_HEADING_RE` below: that expression decides what to
#: *accept*, and declining to accept a line only sends it on to the title-case
#: test, which is where the damage was done. A French procedure step wrapped
#: across a line break -- `6. Cliquez sur OK pour` -- has no terminal
#: punctuation to disqualify it, and it is title-case by the letter of the rule,
#: because `sur` and `pour` are both minor words and `Cliquez` and `OK` are both
#: capitalised. On the real corpus that labelled 1,143 of 5,003 passages with a
#: step number, which put "6. Cliquez sur OK pour" in the citation the model
#: shows the user and pointed the heading boost at `cliquez`.
#:
#: The negative lookahead is what keeps `3.2 Creating a Pad` a heading: a
#: multi-level number is a section, a single-level one is a step.
_ENUMERATOR_RE = re.compile(r"^\d+\s*[.)](?!\d)")

#: A fragment of a path rather than a section title: an install directory
#: (`C:\Program Files\intel\plsuite\bin`), a resource path
#: (`EN: /$OS/Startup/Components/MechanicalStandardParts`), or a menu path the
#: extractor cut at a line break (`Outils ->`). All three are short, unpunctuated
#: and title-cased, so they pass every other test, and all three are useless in
#: the citation the model shows the user.
#:
#: A single slash is deliberately allowed: `Stratégie GPS/FMS` and `NonVu/Vu
#: Permanent` are real headings in these manuals, and a rule that rejected one
#: slash would take them with it.
_PATH_FRAGMENT_RE = re.compile(r"\\|://|(?:/[^/\s]*){2,}|-?>\s*$")

#: An instruction, recognised by the verb it opens with.
#:
#: The enumerator and terminal-punctuation rules catch a numbered step that the
#: extractor left whole. They do not catch one whose number was on the previous
#: line and whose full stop was on the next, and that residue is not small:
#: `Cliquez sur OK` alone headed 262 of 4,912 passages -- the single most common
#: "section title" in the corpus -- because it ends on `OK` rather than on a
#: preposition. Measured over the built index, 422 passages (8.6%) open with one
#: of these verbs, and every one of them is a step rather than a section.
#:
#: Deliberately imperatives only. `type`, `open` and `close` are excluded even
#: though they appear here as verbs, because they are also ordinary nouns and
#: adjectives in real titles -- `Type of Constraint`, `Open Body` -- and a rule
#: that took those would cost more than it saves. These manuals title their
#: sections with gerunds (`Creating a Pocket`, `Selecting the Edges to Keep`),
#: so no legitimate heading starts with an imperative.
_INSTRUCTION_VERBS = frozenset(
    {
        "cliquez",
        "sélectionnez",
        "selectionnez",
        "choisissez",
        "entrez",
        "saisissez",
        "appuyez",
        "activez",
        "déplacez",
        "deplacez",
        "click",
        "select",
        "choose",
        "enter",
        "press",
        "drag",
    }
)

#: A sentence boundary inside the line. `Sélectionnez Plan.1. CATIA` is two
#: sentences the extractor ran together, and it passes title case on its merits
#: because every word happens to be capitalised. A real title does not contain a
#: full stop followed by a space; a multi-level section number (`15.3`) has no
#: space after its period and so is untouched.
_INNER_SENTENCE_RE = re.compile(r"[.!?]\s")

#: Boilerplate that opens a line: page numbers, copyright notices, vendor
#: banners. Every page of every manual carries several.
_BOILERPLATE_PREFIX_RE = re.compile(
    r"^\s*(?:page\s+\d+|\d+\s*/\s*\d+|copyright|©|all rights reserved"
    r"|dassault\s+syst|version\s+\d|printed\s+on)",
    re.IGNORECASE,
)

#: The running footer these guides carry -- `Assembly Modeling 11-57`. It is
#: Title Case, short, and unpunctuated, so it passes every heading test on its
#: merits and has to be excluded by shape. Anchored at the end, which is why it
#: cannot live in the prefix expression above.
_BOILERPLATE_SUFFIX_RE = re.compile(r"\s\d+-\d+\s*$")


def _is_boilerplate(line: str) -> bool:
    """Whether a line is page furniture rather than content."""
    return bool(_BOILERPLATE_PREFIX_RE.match(line) or _BOILERPLATE_SUFFIX_RE.search(line))


#: Words that stay lowercase in a correctly capitalised title.
#:
#: Without these, "Creating a Pocket" and "Working with Wireframe" are not
#: headings -- the every-word-capitalised test fails on `a` and `with`, which
#: rejects most of the real section titles in an English manual. French needs
#: the same allowance for its articles and prepositions.
_TITLE_MINOR_WORDS: frozenset[str] = frozenset(
    """
    a an the of in on to for and or with by from at as into over under
    de du des la le les et en sur dans pour avec par au aux un une
    """.split()
)


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage, with everything a citation needs."""

    text: str
    page: int
    heading: str | None

    @property
    def word_count(self) -> int:
        return len(self.text.split())


def _is_heading(line: str) -> bool:
    """Whether a line looks like a section title rather than body text.

    A heading is short, unpunctuated at the end, and either numbered
    (`3.2 Creating a Pad`) or title- or upper-cased.

    **The terminal-punctuation test comes first, before the numbered pattern,
    and that ordering is load-bearing.** These manuals are mostly numbered
    procedures, so `15. Cliquez sur OK pour confirmer l'opération.` is on nearly
    every page and matches the numbered-heading pattern perfectly. Testing the
    pattern first labelled thousands of procedure steps as section titles, which
    put a step number in every citation and pointed the heading boost at the
    word "cliquez". A heading does not end in a full stop; an instruction does.
    """
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False
    if _is_boilerplate(stripped):
        return False
    if stripped[-1] in ".,;:!?":
        return False
    if _ENUMERATOR_RE.match(stripped):
        return False
    if _PATH_FRAGMENT_RE.search(stripped):
        return False
    # A title starts on a word or a number, never on punctuation. `. Vous` and
    # `(Ref. No. ISO 10209-2:1993)` reached the citation because the title-case
    # test skips any word whose first character is not a letter -- so the lone
    # `.` was ignored and the capitalised word behind it carried the line.
    # 349 passages (7.1%) were headed this way.
    if not stripped[0].isalnum():
        return False
    if _INNER_SENTENCE_RE.search(stripped):
        return False
    if stripped.split()[0].lower().strip(".,") in _INSTRUCTION_VERBS:
        return False
    if _NUMBERED_HEADING_RE.match(stripped):
        return True

    words = stripped.split()
    if not (1 < len(words) <= 12):
        return False

    # A title does not end on a preposition or an article. `Creating a Pocket`
    # ends on `Pocket`; `Cliquez sur`, `Sélectionnez Insertion -> Outils de` and
    # `4. Modifiez les` are sentences the extractor cut at a line break, and they
    # pass every other test on their merits -- a capitalised first word and
    # nothing but minor words after it is precisely the shape of a wrapped
    # French instruction. Costs the occasional real title ("What to Look For");
    # that is one mislabelled passage against several hundred poisoned ones.
    if words[-1].lower().strip(".,") in _TITLE_MINOR_WORDS:
        return False

    letters = [char for char in stripped if char.isalpha()]
    if not letters:
        return False
    # ALL CAPS. Measured on letters rather than words so a heading like
    # "MULTI-SECTIONS SOLID" is not disqualified by its hyphen.
    upper_ratio = sum(char.isupper() for char in letters) / len(letters)
    if upper_ratio > 0.6:
        return True

    # Title Case, allowing the small words that stay lowercase in a real title.
    # The first word must still be capitalised: that is what distinguishes
    # "Creating a Pocket" from a fragment of a sentence that happens to be short.
    alphabetic = [word for word in words if word[0].isalpha()]
    if not alphabetic or not alphabetic[0][0].isupper():
        return False
    return all(
        word[0].isupper() or word.lower().strip(".,") in _TITLE_MINOR_WORDS
        for word in alphabetic
    )


def _clean(text: str) -> str:
    """Normalise the whitespace PDF extraction leaves behind.

    Extracted text is full of soft hyphens, non-breaking spaces and runs of
    alignment padding from `-layout`. Left alone, the padding becomes part of
    the token stream and the hyphens split words in half.
    """
    text = text.replace("­", "").replace(" ", " ")
    # A hyphen at end of line is a word broken across lines: rejoin it.
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_page(
    page: Page,
    *,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    inherited_heading: str | None = None,
) -> tuple[list[Chunk], str | None]:
    """Cut one page into passages.

    Returns the passages and the heading in force at the end of the page, which
    the caller threads into the next page -- a section runs across a page break
    far more often than it starts neatly at the top of one, and a passage on
    page 48 of a procedure that began on page 47 would otherwise lose its title.
    """
    cleaned = _clean(page.text)
    if not cleaned:
        return [], inherited_heading

    heading = inherited_heading
    chunks: list[Chunk] = []
    pending: list[str] = []
    pending_heading = heading

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        body = " ".join(pending).strip()
        if len(body.split()) >= MIN_CHUNK_WORDS:
            text = f"{pending_heading}\n{body}" if pending_heading else body
            chunks.append(Chunk(text=text, page=page.number, heading=pending_heading))
        pending = []

    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_boilerplate(stripped):
            continue

        if _is_heading(stripped):
            # A new section starts here: close the passage under the old
            # heading rather than letting it run across the boundary.
            flush()
            heading = stripped
            pending_heading = heading
            continue

        pending.extend(stripped.split())

        while len(pending) >= chunk_words:
            body = " ".join(pending[:chunk_words])
            text = f"{pending_heading}\n{body}" if pending_heading else body
            chunks.append(Chunk(text=text, page=page.number, heading=pending_heading))
            # Carry the tail forward. `max(1, ...)` guards a caller passing an
            # overlap greater than the chunk size, which would otherwise never
            # consume anything and loop forever.
            pending = pending[max(1, chunk_words - overlap_words) :]

    flush()
    return chunks, heading


def chunk_document(
    pages: Iterable[Page],
    *,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[Chunk]:
    """Cut a whole document into passages, threading headings across pages."""
    chunks: list[Chunk] = []
    heading: str | None = None
    for page in pages:
        produced, heading = chunk_page(
            page,
            chunk_words=chunk_words,
            overlap_words=overlap_words,
            inherited_heading=heading,
        )
        chunks.extend(produced)
    return chunks


def chunk_terms(chunk: Chunk) -> list[str]:
    """The term stream a passage is indexed as, headings weighted.

    `chunk.text` already carries the heading, so analysing it yields the
    heading's terms once. The extra repeats here are what make a heading match
    count for more than a passing mention -- see `HEADING_TERM_REPEAT`.

    The document length BM25 normalises by is the length of this stream, so a
    headed passage is also slightly "longer" and takes a small length penalty.
    That is the correct behaviour and not an oversight: the repeats are real
    evidence, and letting them inflate the score without paying the same length
    cost as any other term would make the boost unbounded.
    """
    terms = analyze(chunk.text)
    if chunk.heading:
        terms.extend(analyze(chunk.heading) * (HEADING_TERM_REPEAT - 1))
    return terms
