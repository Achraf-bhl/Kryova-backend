"""The reference corpus: building it, loading it, searching it.

This is the layer that turns a directory of PDFs into something the agent can
consult, and it is built around one constraint that shapes everything else:
**the passage text is never all in memory at once.**

A few hundred megabytes of manuals chunk into six figures of passages. Holding
them in a list costs a gigabyte of resident memory per process, for data that a
query touches five records of. So the passages live in a JSONL file and an array
of byte offsets says where each one starts. A search scores against the BM25
arrays -- which are small, a few tens of megabytes at this corpus size -- and
then seeks directly to the handful of passages that won. Memory is flat in the
size of the corpus, and startup does not read the text at all.

**Builds are atomic at the directory level.** A half-written index is worse than
no index: it loads, it answers, and the answers are wrong. The build writes to a
sibling directory and swaps it in only once every file is complete, so a crash
or a full disk leaves the previous index serving.

**Staleness is detected, not assumed.** The manifest records a fingerprint of
every source file (name, size, modification time). `is_stale` compares it to
what is on disk now, which is what lets the setup flow say "you added two
manuals, rebuild" instead of silently serving an index that predates them.

**Nothing here raises into a request.** A missing index, a corrupt index, an
index written by an incompatible version -- all of them resolve to "no results",
logged once, because the agent consulting its references is an enhancement to
an answer and must never be the reason there is no answer.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from app.retrieval.analyze import analyze_query
from app.retrieval.bm25 import BM25Index, Hit
from app.retrieval.chunking import (
    DEFAULT_CHUNK_WORDS,
    DEFAULT_OVERLAP_WORDS,
    Chunk,
    chunk_document,
    chunk_terms,
)
from app.retrieval.extract import (
    SUPPORTED_SUFFIXES,
    ExtractionError,
    extract_pages,
)
from app.retrieval.language import detect

logger = logging.getLogger(__name__)

INDEX_FILENAME = "index.npz"
PASSAGES_FILENAME = "passages.jsonl"
OFFSETS_FILENAME = "passages.offsets.npy"
MANIFEST_FILENAME = "manifest.json"

#: Passages returned by default. Five is about as many as fits in a prompt
#: alongside a real conversation without crowding out the transcript.
DEFAULT_LIMIT: int = 5

#: A hit matching only one term of a multi-term query is nearly always noise --
#: the corpus is large enough that *something* contains any single word. Hits
#: below this fraction of the query's terms are dropped, unless the query itself
#: was a single term, in which case one match is all there is to have.
MIN_COVERAGE_RATIO: float = 0.4


#: How much a passage in the preferred language is favoured.
#:
#: A multiplier rather than a filter, and that is the whole design. Filtering to
#: one language means a French user asking about a workbench documented only in
#: the English manual gets nothing at all, which is strictly worse than getting
#: the English page. A same-language passage wins every close contest and still
#: loses to a much better match in the other language.
#:
#: 1.35 rather than a rounder number because it was measured. This corpus has
#: workbenches documented in one language only -- Photo Studio in English,
#: FreeStyle in French -- and they are the case that decides this constant: a
#: French question about rendering has no French page to find, so the boost is
#: not breaking a tie, it is demoting the only answer there is. Swept against
#: the corpus eval set, 1.6 lost both of those and scored MRR 0.934; 1.35 finds
#: every case (P@3 100%, MRR 0.974) and still carries every genuine near-tie,
#: because two translations of one page score close enough that any multiplier
#: above 1 separates them. Above ~1.45 the number stops breaking ties and starts
#: overriding relevance.
LANGUAGE_PREFERENCE_BOOST: float = 1.35


@dataclass(frozen=True)
class Passage:
    """One retrieved passage, with everything needed to cite it."""

    text: str
    source: str
    page: int
    heading: str | None
    score: float
    matched_terms: int
    #: Two-letter code, or None when the passage was too short or too mixed to
    #: tell. None never blocks a result; it only forgoes the preference boost.
    language: str | None = None

    def citation(self) -> str:
        """A human-readable pointer: document, section, page."""
        parts = [self.source]
        if self.heading:
            parts.append(self.heading)
        parts.append(f"p. {self.page}")
        return " — ".join(parts)


@dataclass(frozen=True)
class SourceFingerprint:
    name: str
    size: int
    modified: int

    @classmethod
    def of(cls, path: Path) -> "SourceFingerprint":
        stat = path.stat()
        return cls(name=path.name, size=stat.st_size, modified=int(stat.st_mtime))

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size, "modified": self.modified}


@dataclass(frozen=True)
class BuildReport:
    """What a build actually did, for the CLI and the setup check."""

    documents: int
    passages: int
    terms: int
    skipped: list[str]
    extractors: dict[str, str]
    elapsed_seconds: float

    def summary(self) -> str:
        line = (
            f"{self.passages:,} passages from {self.documents} document(s), "
            f"{self.terms:,} terms, in {self.elapsed_seconds:.1f}s"
        )
        if self.skipped:
            line += f"\n  skipped {len(self.skipped)}: " + "; ".join(self.skipped)
        return line


def discover_sources(
    roots: Sequence[Path], *, exclude: Sequence[Path] = ()
) -> list[Path]:
    """Every indexable file under `roots`, de-duplicated and ordered.

    Ordering is by resolved path so a rebuild on the same inputs produces the
    same passage ids, which is what makes two builds comparable and the on-disk
    index reproducible.

    Directories are walked recursively, but a root that is itself a file is
    accepted too -- convenient for indexing one document in a test.

    `exclude` names directories to skip. The default layout needs it: the index
    lives at `data/bm25/index/` and the sources at `data/bm25/sources/`, and the
    scan of `data/` that picks up a pre-existing corpus would otherwise walk
    straight into the index directory and index the build's own output. The
    README in `data/bm25/` is excluded the same way -- it is documentation about
    the corpus, not part of it, and indexing it means the assistant can cite it.
    """
    skip = [path.resolve() for path in exclude]

    def excluded(path: Path) -> bool:
        return any(path == root or root in path.parents for root in skip)

    found: dict[Path, None] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() in SUPPORTED_SUFFIXES and not excluded(root.resolve()):
                found[root.resolve()] = None
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            if any(part.startswith(".") for part in path.parts):
                continue
            resolved = path.resolve()
            if excluded(resolved):
                continue
            found[resolved] = None
    return sorted(found)


def build(
    *,
    sources: Sequence[Path],
    destination: Path,
    chunk_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    exclude: Sequence[Path] = (),
    on_progress: Any = None,
) -> BuildReport:
    """Index every file in `sources` into `destination`, atomically.

    A file that cannot be read is skipped and named in the report rather than
    failing the build: one unreadable PDF among twenty must not cost the other
    nineteen, and a build that refuses everything over one bad input is a build
    nobody runs twice.
    """
    started = time.monotonic()
    # The destination is always excluded, whatever the caller passed: a build
    # whose output directory sits under a source root would otherwise index its
    # own previous output, and each rebuild would compound it.
    files = discover_sources(sources, exclude=[*exclude, destination])

    chunks: list[Chunk] = []
    origins: list[str] = []
    fingerprints: list[SourceFingerprint] = []
    extractors: dict[str, str] = {}
    skipped: list[str] = []
    indexed = 0

    for path in files:
        # Fingerprint every file that was *considered*, not just the ones that
        # indexed. Recording only successes makes `is_stale` permanently true on
        # any corpus containing one unreadable PDF -- the manifest says 17, the
        # disk says 21, and they never agree however many times you rebuild.
        # It is also the behaviour wanted on its own merits: replacing a scanned
        # manual with an OCR'd one changes nothing about the successes and is
        # exactly when a rebuild is needed.
        try:
            fingerprints.append(SourceFingerprint.of(path))
        except OSError:
            # Vanished between discovery and here. Nothing to record, and the
            # extraction below will fail and skip it.
            pass

        try:
            pages, extractor = extract_pages(path)
        except ExtractionError as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            skipped.append(f"{path.name} ({exc.short})")
            continue
        except OSError as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            skipped.append(f"{path.name} (unreadable: {exc})")
            continue

        produced = chunk_document(pages, chunk_words=chunk_words, overlap_words=overlap_words)
        if not produced:
            skipped.append(f"{path.name} (no indexable text)")
            continue

        chunks.extend(produced)
        origins.extend([path.name] * len(produced))
        extractors[path.name] = extractor
        indexed += 1
        if on_progress is not None:
            on_progress(path.name, len(produced))

    index = BM25Index.build(
        (chunk_terms(chunk) for chunk in chunks),
    )

    staging = destination.with_name(destination.name + ".building")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        index.save(staging / INDEX_FILENAME)
        offsets = _write_passages(staging / PASSAGES_FILENAME, chunks, origins)
        np.save(staging / OFFSETS_FILENAME, offsets)
        (staging / MANIFEST_FILENAME).write_text(
            json.dumps(
                {
                    "built_at": time.time(),
                    "sources": [fingerprint.as_dict() for fingerprint in fingerprints],
                    "extractors": extractors,
                    "skipped": skipped,
                    "chunk_words": chunk_words,
                    "overlap_words": overlap_words,
                    "stats": index.stats(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # Swap last. Up to this point the previous index is still the one being
        # served; after it, the new one is, and there is no moment where a
        # partially written directory is live.
        _swap(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    return BuildReport(
        documents=indexed,
        passages=len(chunks),
        terms=index.num_terms,
        skipped=skipped,
        extractors=extractors,
        elapsed_seconds=time.monotonic() - started,
    )


def _write_passages(path: Path, chunks: Sequence[Chunk], origins: Sequence[str]) -> np.ndarray:
    """Write one JSON record per passage, returning each one's byte offset.

    The offsets are what make lazy reads possible: a search seeks straight to
    the passage it wants instead of parsing the whole file. They are recorded as
    bytes written rather than measured with `tell()`, because a buffered text
    handle's `tell()` is not the byte position of the next write.
    """
    offsets = np.empty(len(chunks), dtype=np.int64)
    position = 0
    with path.open("wb") as handle:
        for index, (chunk, origin) in enumerate(zip(chunks, origins, strict=True)):
            record = json.dumps(
                {
                    "text": chunk.text,
                    "source": origin,
                    "page": chunk.page,
                    "heading": chunk.heading,
                    # Detected once, at build time. Doing it per query would
                    # cost a scan of every candidate passage on every search,
                    # to answer a question whose answer cannot change.
                    "language": detect(chunk.text),
                },
                ensure_ascii=False,
            ).encode("utf-8") + b"\n"
            offsets[index] = position
            handle.write(record)
            position += len(record)
    return offsets


def _swap(staging: Path, destination: Path) -> None:
    """Replace `destination` with `staging` as close to atomically as POSIX allows.

    A directory rename over an existing directory is not atomic anywhere, so the
    old one is moved aside first and removed after. The window where neither is
    in place is two renames wide, and a reader that hits it gets "no index",
    which is already a handled state.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.with_name(destination.name + ".previous")
    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)
    if destination.exists():
        os.rename(destination, previous)
    os.rename(staging, destination)
    shutil.rmtree(previous, ignore_errors=True)


class Corpus:
    """A built index, loaded and queryable.

    Construct with `Corpus.open`, which returns `None` rather than raising when
    there is nothing usable on disk. Instances are read-only and safe to share
    across threads: the BM25 arrays are never mutated, and passage reads open
    their own file handle per call rather than sharing a cursor.
    """

    def __init__(
        self,
        *,
        index: BM25Index,
        passages_path: Path,
        offsets: np.ndarray,
        manifest: dict[str, Any],
    ) -> None:
        self._index = index
        self._passages_path = passages_path
        self._offsets = offsets
        self._manifest = manifest

    @classmethod
    def open(cls, directory: Path) -> "Corpus | None":
        """Load the index at `directory`, or `None` if there is not a usable one.

        Every failure mode -- absent, truncated, wrong format version, corrupt
        numpy payload -- returns `None` with a warning. The caller's job is to
        answer without the corpus, not to propagate a disk problem into a user's
        conversation.
        """
        try:
            index_path = directory / INDEX_FILENAME
            passages_path = directory / PASSAGES_FILENAME
            offsets_path = directory / OFFSETS_FILENAME
            manifest_path = directory / MANIFEST_FILENAME
            if not all(
                path.exists()
                for path in (index_path, passages_path, offsets_path, manifest_path)
            ):
                return None

            index = BM25Index.load(index_path)
            offsets = np.load(offsets_path, allow_pickle=False)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("Reference index at %s is unusable (%s); ignoring it", directory, exc)
            return None

        if index.num_documents != offsets.shape[0]:
            # The two files disagree about how many passages exist, which means
            # they came from different builds. Every lookup would be off by
            # some unknown amount, silently returning the wrong text.
            logger.warning(
                "Reference index at %s is inconsistent (%d documents, %d passages); ignoring it",
                directory,
                index.num_documents,
                offsets.shape[0],
            )
            return None

        return cls(
            index=index,
            passages_path=passages_path,
            offsets=offsets,
            manifest=manifest,
        )

    # -- reading ------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        prefer_language: str | None = None,
        coverage_query: str | None = None,
    ) -> list[Passage]:
        """The best `limit` passages for `query`, best first.

        Returns an empty list for an empty query, a query of nothing but
        stopwords, or a query no passage matches well enough -- all of which are
        ordinary outcomes, not errors.

        `prefer_language` favours passages written in that language without
        excluding the others; see `LANGUAGE_PREFERENCE_BOOST`. The reordering
        happens after materialisation because language lives on the passage
        record rather than in the BM25 arrays -- which is why the over-fetch
        below has to be generous enough that a preferred-language passage
        sitting just outside `limit` can still be promoted into it.

        `coverage_query` is what the coverage floor is measured against, when
        that differs from what is scored. It exists because query expansion and
        a coverage floor are otherwise in direct conflict: expansion adds
        *synonyms*, and a passage matches the English name or the French one,
        never both, so a floor computed over the expanded query demands a
        breadth of match that no passage can have. Widening `bend radius` to a
        dozen cross-language terms raised the floor from one term to eight and
        turned a good hit into no hits at all. Passing the user's original query
        here keeps the floor measuring what they actually asked for while the
        synonyms stay purely additive to the score.
        """
        terms = analyze_query(query)
        if not terms:
            return []

        # Over-fetch, then filter on coverage. Filtering after the cut would
        # sometimes return fewer than `limit` results when good ones existed
        # just below it.
        hits = self._index.search(terms, limit=limit * 4)
        floor_terms = analyze_query(coverage_query) if coverage_query else terms
        distinct = len(set(floor_terms)) or len(set(terms))
        floor = 1 if distinct == 1 else max(1, int(distinct * MIN_COVERAGE_RATIO))
        kept = [hit for hit in hits if hit.matched_terms >= floor]

        passages = self._materialise(kept[: limit * 4])
        if prefer_language:
            passages = sorted(
                passages,
                key=lambda passage: -(
                    passage.score
                    * (
                        LANGUAGE_PREFERENCE_BOOST
                        if passage.language == prefer_language
                        else 1.0
                    )
                ),
            )
        return passages[:limit]

    def _materialise(self, hits: Sequence[Hit]) -> list[Passage]:
        """Read the text for scored hits, seeking to each one.

        A read failure drops that passage rather than the whole result: the
        index and the passage file disagreeing about one record is survivable,
        and the other four hits are still worth returning.
        """
        if not hits:
            return []
        passages: list[Passage] = []
        try:
            with self._passages_path.open("rb") as handle:
                for hit in hits:
                    handle.seek(int(self._offsets[hit.doc_id]))
                    line = handle.readline()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Passage %d is malformed; skipping it", hit.doc_id)
                        continue
                    passages.append(
                        Passage(
                            text=record["text"],
                            source=record["source"],
                            page=int(record["page"]),
                            heading=record.get("heading"),
                            score=hit.score,
                            matched_terms=hit.matched_terms,
                            # `.get`, not `[...]`: an index built before
                            # languages existed has no such key, and must load
                            # and answer rather than raise a KeyError per hit.
                            language=record.get("language"),
                        )
                    )
        except OSError as exc:
            logger.warning("Could not read passages from %s: %s", self._passages_path, exc)
            return []
        return passages

    # -- introspection ------------------------------------------------------

    @property
    def manifest(self) -> dict[str, Any]:
        return self._manifest

    def stats(self) -> dict[str, Any]:
        stats = dict(self._index.stats())
        stats["sources"] = len(self._manifest.get("sources", []))
        stats["built_at"] = self._manifest.get("built_at")
        return stats

    def is_stale(self, sources: Sequence[Path], *, exclude: Sequence[Path] = ()) -> bool:
        """Whether the files on disk differ from the ones this index was built from.

        Compares the full fingerprint set, so an added, removed, edited or
        replaced document all register. A source that has vanished counts as a
        change too -- the index still contains its passages, and citing a
        document the user deleted is a bug.
        """
        recorded = {
            (entry["name"], entry["size"], entry["modified"])
            for entry in self._manifest.get("sources", [])
        }
        try:
            current = {
                (fingerprint.name, fingerprint.size, fingerprint.modified)
                for fingerprint in (
                    SourceFingerprint.of(path)
                    for path in discover_sources(sources, exclude=exclude)
                )
            }
        except OSError:
            # Cannot tell; assume fresh rather than triggering a rebuild loop.
            return False
        return recorded != current


def merge_adjacent(passages: Iterable[Passage]) -> list[Passage]:
    """Join passages that overlap on the same page of the same document.

    The chunker deliberately overlaps passages, so a strong match near a
    boundary frequently retrieves both halves and the model reads the shared
    words twice. Merging them restores the original continuous text and frees a
    slot for genuinely different material.
    """
    ordered = sorted(passages, key=lambda passage: (passage.source, passage.page, -passage.score))
    merged: list[Passage] = []
    for passage in ordered:
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.source == passage.source
            and previous.page == passage.page
            and previous.heading == passage.heading
        ):
            overlap = _overlap(previous.text, passage.text)
            if overlap:
                merged[-1] = replace(
                    previous,
                    text=previous.text + passage.text[overlap:],
                    score=max(previous.score, passage.score),
                    matched_terms=max(previous.matched_terms, passage.matched_terms),
                )
                continue
        merged.append(passage)
    return sorted(merged, key=lambda passage: -passage.score)


def _overlap(left: str, right: str) -> int:
    """Length of the longest suffix of `left` that prefixes `right`.

    Bounded search: the chunker's overlap is a known small number of words, so
    scanning the whole string would be work spent proving what is already known
    to be absent.
    """
    limit = min(len(left), len(right), 2_000)
    for size in range(limit, 40, -1):
        if left.endswith(right[:size]):
            return size
    return 0
