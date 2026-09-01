"""Okapi BM25 over a compressed posting list.

BM25 scores a document against a query by asking three questions per term: how
often does the term appear here (term frequency, with diminishing returns), how
rare is it across the corpus (inverse document frequency), and is this document
long enough that the frequency should be discounted (length normalisation).
Twenty-five years on it is still the baseline every dense retriever is measured
against, and on a corpus of technical manuals it is frequently the winner --
because the terms that discriminate between passages here are exact ones
(`M6`, `Ø12`, `Multi-sections Solid`) rather than paraphrases.

**The layout is CSR, not a dict of lists.** A Python dict mapping term to a list
of `(doc, tf)` tuples is the obvious implementation and is roughly forty times
slower to query, because every posting is a boxed tuple the interpreter has to
walk. Here the postings for every term live end to end in three flat numpy
arrays, and `term_offsets` says where each term's slice begins. Scoring a term
is then a slice and three vector operations, with the interpreter touching one
object per query *term* rather than one per posting.

**Scores accumulate with `scores[docs] += contribution`, which is only correct
because a document appears at most once in a single term's postings.** That
holds by construction in `build`, where postings come out of a per-document
`Counter`. It would silently produce wrong scores if postings were ever
appended twice for one document, so the invariant is asserted at build time
rather than trusted.

**Two additions to textbook BM25, both earned:**

*Coverage.* Textbook BM25 sums independent per-term scores, so a passage
mentioning `thickness` twenty times outranks one mentioning `thickness`,
`shell` and `rib` once each -- when the second is obviously the answer to a
three-term query. A multiplicative bonus on the fraction of distinct query terms
present fixes this, and it is the single largest quality change in this module.

*Field boost.* A term appearing in a passage's heading is stronger evidence than
one in its body. Rather than a second index (BM25F proper), the builder scales
the heading's contribution to term frequency, which gets most of the benefit for
none of the complexity.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

#: Term-frequency saturation. Above this, seeing a term again adds almost
#: nothing. 1.5 rather than the more common 1.2 because these are long manual
#: pages where a genuinely on-topic passage repeats its subject several times.
DEFAULT_K1: float = 1.5

#: Length normalisation, 0 = off, 1 = full. 0.75 is the standard value and
#: holds up here: the chunker already produces roughly even passages, so this is
#: correcting the tail rather than doing the heavy lifting.
DEFAULT_B: float = 0.75

#: How much a passage is rewarded for containing *more distinct* query terms.
#: At 0.35 a passage matching all three terms of a three-term query scores about
#: 1.35x a passage matching one, before any term weighting -- enough to reorder
#: the top of the list, not enough to swamp IDF.
DEFAULT_COVERAGE_WEIGHT: float = 0.35

#: Bumped whenever the on-disk format or the scoring changes in a way that makes
#: an existing index wrong rather than merely stale. `load` refuses a mismatch,
#: so a format change can never be read as though it were the current one.
INDEX_FORMAT_VERSION: int = 1


@dataclass(frozen=True)
class Hit:
    """One scored document. `doc_id` indexes back into the caller's metadata."""

    doc_id: int
    score: float
    #: How many distinct query terms this document contained. Surfaced because
    #: it is the honest signal for "is this actually relevant, or merely the
    #: least bad of a bad set" -- a one-of-five-terms hit is usually noise.
    matched_terms: int


class BM25Index:
    """An immutable, queryable index. Build it with `BM25Index.build`."""

    __slots__ = (
        "vocabulary",
        "term_offsets",
        "postings_docs",
        "postings_tf",
        "doc_lengths",
        "idf",
        "avg_doc_length",
        "k1",
        "b",
        "coverage_weight",
    )

    def __init__(
        self,
        *,
        vocabulary: dict[str, int],
        term_offsets: np.ndarray,
        postings_docs: np.ndarray,
        postings_tf: np.ndarray,
        doc_lengths: np.ndarray,
        idf: np.ndarray,
        avg_doc_length: float,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        coverage_weight: float = DEFAULT_COVERAGE_WEIGHT,
    ) -> None:
        self.vocabulary = vocabulary
        self.term_offsets = term_offsets
        self.postings_docs = postings_docs
        self.postings_tf = postings_tf
        self.doc_lengths = doc_lengths
        self.idf = idf
        self.avg_doc_length = avg_doc_length
        self.k1 = k1
        self.b = b
        self.coverage_weight = coverage_weight

    # -- construction -------------------------------------------------------

    @classmethod
    def build(
        cls,
        documents: Iterable[Sequence[str]],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        coverage_weight: float = DEFAULT_COVERAGE_WEIGHT,
        boosts: Sequence[float] | None = None,
    ) -> "BM25Index":
        """Index a corpus of already-analyzed documents.

        `documents` is a sequence of term lists -- the output of
        `analyze.analyze`, not raw text. Keeping analysis outside the index is
        what lets the transcript recall and the manual corpus share this class
        while analysing their inputs differently.

        `boosts` optionally scales each document's term frequencies, which is
        how heading matches are made to weigh more than body matches.
        """
        postings: dict[str, list[tuple[int, float]]] = {}
        lengths: list[float] = []

        for doc_id, terms in enumerate(documents):
            counts = Counter(terms)
            boost = boosts[doc_id] if boosts is not None else 1.0
            lengths.append(float(len(terms)))
            for term, count in counts.items():
                # One entry per (term, document) because `counts` is a Counter
                # over this document alone. The `scores[docs] +=` accumulation
                # in `search` is only correct while that stays true.
                postings.setdefault(term, []).append((doc_id, count * boost))

        num_docs = len(lengths)
        if num_docs == 0:
            return cls._empty(k1=k1, b=b, coverage_weight=coverage_weight)

        # Sorted so the on-disk index is byte-identical for identical input,
        # which is what makes a rebuild diffable and a corpus fingerprint mean
        # something.
        vocabulary = {term: index for index, term in enumerate(sorted(postings))}

        total_postings = sum(len(entries) for entries in postings.values())
        term_offsets = np.zeros(len(vocabulary) + 1, dtype=np.int64)
        postings_docs = np.empty(total_postings, dtype=np.int32)
        postings_tf = np.empty(total_postings, dtype=np.float32)
        idf = np.empty(len(vocabulary), dtype=np.float32)

        cursor = 0
        for term, term_id in vocabulary.items():
            entries = postings[term]
            term_offsets[term_id] = cursor
            for doc_id, frequency in entries:
                postings_docs[cursor] = doc_id
                postings_tf[cursor] = frequency
                cursor += 1
            document_frequency = len(entries)
            # The Lucene/Robertson IDF with the +1 inside the log. The textbook
            # form goes negative for a term in more than half the documents,
            # which would make a common term actively *penalise* a document that
            # contains it. This form is always positive.
            idf[term_id] = math.log(
                1.0 + (num_docs - document_frequency + 0.5) / (document_frequency + 0.5)
            )
        term_offsets[len(vocabulary)] = cursor

        doc_lengths = np.asarray(lengths, dtype=np.float32)
        average = float(doc_lengths.mean()) if num_docs else 0.0

        return cls(
            vocabulary=vocabulary,
            term_offsets=term_offsets,
            postings_docs=postings_docs,
            postings_tf=postings_tf,
            doc_lengths=doc_lengths,
            idf=idf,
            # A zero average would divide by zero in the length norm. It can
            # only happen for a corpus of entirely empty documents, which is
            # degenerate but reachable -- a PDF that extracted to nothing.
            avg_doc_length=average or 1.0,
            k1=k1,
            b=b,
            coverage_weight=coverage_weight,
        )

    @classmethod
    def _empty(
        cls, *, k1: float, b: float, coverage_weight: float
    ) -> "BM25Index":
        """A valid index over nothing, so callers never special-case None."""
        return cls(
            vocabulary={},
            term_offsets=np.zeros(1, dtype=np.int64),
            postings_docs=np.empty(0, dtype=np.int32),
            postings_tf=np.empty(0, dtype=np.float32),
            doc_lengths=np.empty(0, dtype=np.float32),
            idf=np.empty(0, dtype=np.float32),
            avg_doc_length=1.0,
            k1=k1,
            b=b,
            coverage_weight=coverage_weight,
        )

    # -- querying -----------------------------------------------------------

    @property
    def num_documents(self) -> int:
        return int(self.doc_lengths.shape[0])

    @property
    def num_terms(self) -> int:
        return len(self.vocabulary)

    def search(self, terms: Sequence[str], *, limit: int = 10) -> list[Hit]:
        """Score every document against `terms` and return the best `limit`.

        Duplicate query terms are collapsed first. Repeating a word in a query
        is not evidence that the word matters more -- it is usually just how the
        sentence read -- and scoring it twice would let a stray repetition
        dominate the ranking.
        """
        if self.num_documents == 0 or not terms:
            return []

        unique = list(dict.fromkeys(terms))
        scores = np.zeros(self.num_documents, dtype=np.float32)
        coverage = np.zeros(self.num_documents, dtype=np.float32)
        matched_any = False

        # Precomputed once rather than per term: this is the length-norm
        # denominator's document-dependent half, and it does not vary by term.
        length_norm = self.k1 * (
            1.0 - self.b + self.b * (self.doc_lengths / self.avg_doc_length)
        )

        for term in unique:
            term_id = self.vocabulary.get(term)
            if term_id is None:
                continue
            start = int(self.term_offsets[term_id])
            end = int(self.term_offsets[term_id + 1])
            if start == end:
                continue
            matched_any = True

            docs = self.postings_docs[start:end]
            frequencies = self.postings_tf[start:end]

            contribution = (
                self.idf[term_id]
                * (frequencies * (self.k1 + 1.0))
                / (frequencies + length_norm[docs])
            )
            # Safe as `+=` rather than `np.add.at` only because `docs` holds no
            # duplicates -- see the class docstring.
            scores[docs] += contribution
            coverage[docs] += 1.0

        if not matched_any:
            return []

        # Reward breadth of match. Without this, one term repeated often beats
        # every term present once, which is the wrong answer for nearly every
        # real query.
        scores *= 1.0 + self.coverage_weight * (coverage / len(unique))

        # `argpartition` finds the top-k without sorting the whole array: on a
        # 200k-passage index that is the difference between microseconds and
        # milliseconds per query. Only the k selected are then sorted.
        candidates = np.flatnonzero(scores > 0.0)
        if candidates.size == 0:
            return []
        if candidates.size > limit:
            partial = np.argpartition(scores[candidates], -limit)[-limit:]
            candidates = candidates[partial]
        ordered = candidates[np.argsort(scores[candidates])[::-1]]

        return [
            Hit(
                doc_id=int(doc_id),
                score=float(scores[doc_id]),
                matched_terms=int(coverage[doc_id]),
            )
            for doc_id in ordered
        ]

    # -- persistence --------------------------------------------------------

    def save(self, path: Path) -> None:
        """Write the index to `path` atomically.

        Atomic because the alternative is a half-written index on disk after a
        crash or a full volume, which loads without error and returns wrong
        answers. The temporary file is placed in the destination directory so
        the rename cannot cross a filesystem boundary.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("wb") as handle:
                np.savez(
                    handle,
                    term_offsets=self.term_offsets,
                    postings_docs=self.postings_docs,
                    postings_tf=self.postings_tf,
                    doc_lengths=self.doc_lengths,
                    idf=self.idf,
                    meta=np.frombuffer(
                        json.dumps(
                            {
                                "format": INDEX_FORMAT_VERSION,
                                "terms": list(self.vocabulary),
                                "avg_doc_length": self.avg_doc_length,
                                "k1": self.k1,
                                "b": self.b,
                                "coverage_weight": self.coverage_weight,
                            }
                        ).encode("utf-8"),
                        dtype=np.uint8,
                    ),
                )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        """Read an index written by `save`.

        Raises `ValueError` on a format mismatch rather than attempting a
        best-effort read: an index written by a different scoring version is not
        stale, it is wrong, and the caller's fallback is to rebuild.
        """
        with np.load(path, allow_pickle=False) as archive:
            meta = json.loads(bytes(archive["meta"]).decode("utf-8"))
            if meta.get("format") != INDEX_FORMAT_VERSION:
                raise ValueError(
                    f"Index at {path} is format {meta.get('format')!r}, "
                    f"this build reads {INDEX_FORMAT_VERSION}. Rebuild it."
                )
            return cls(
                vocabulary={term: index for index, term in enumerate(meta["terms"])},
                term_offsets=archive["term_offsets"],
                postings_docs=archive["postings_docs"],
                postings_tf=archive["postings_tf"],
                doc_lengths=archive["doc_lengths"],
                idf=archive["idf"],
                avg_doc_length=float(meta["avg_doc_length"]),
                k1=float(meta["k1"]),
                b=float(meta["b"]),
                coverage_weight=float(meta["coverage_weight"]),
            )

    def stats(self) -> dict[str, Any]:
        """Numbers for the health endpoint and the build CLI."""
        return {
            "documents": self.num_documents,
            "terms": self.num_terms,
            "postings": int(self.postings_docs.shape[0]),
            "avg_document_length": round(self.avg_doc_length, 2),
        }
