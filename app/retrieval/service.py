"""The process-wide handle on the reference corpus.

One index, loaded once, shared by every request. The BM25 arrays are read-only
after construction and passage reads open their own file handle, so sharing is
safe without a lock on the hot path -- the lock here guards only the load
itself, so that a cold start under concurrent traffic builds the index handle
once rather than once per waiting thread.

**`search` cannot raise.** That is the whole contract of this module. Consulting
the reference material improves an answer; it is never the reason a user does
not get one. A missing index, a corrupt index, a disk that has gone away -- all
of them come back as an empty list, logged once rather than once per query,
because the alternative is a log file that is one message repeated ten thousand
times.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Sequence

from app.core.config import settings
from app.retrieval.corpus import (
    DEFAULT_LIMIT,
    Corpus,
    Passage,
    discover_sources,
    merge_adjacent,
)
from app.retrieval.language import detect_query, normalise

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Lazy, fault-tolerant access to the built reference index."""

    def __init__(
        self,
        *,
        index_dir: Path,
        source_dirs: Sequence[Path],
        enabled: bool = True,
        exclude: Sequence[Path] = (),
    ) -> None:
        self._index_dir = index_dir
        self._source_dirs = list(source_dirs)
        # The index directory and the corpus README both sit inside a scanned
        # root in the default layout. Neither is reference material: indexing
        # the first means indexing the build's own output, and the second means
        # the assistant can cite a note about the corpus as though it were CAD
        # documentation.
        self._exclude = [*exclude, index_dir, index_dir.parent / "README.md"]
        self._enabled = enabled
        self._corpus: Corpus | None = None
        self._loaded = False
        self._lock = threading.Lock()
        #: Set once when loading finds nothing, so a deployment with no index
        #: logs one line at startup instead of one per query forever.
        self._warned = False

    # -- lifecycle ----------------------------------------------------------

    def _ensure_loaded(self) -> Corpus | None:
        if self._loaded:
            return self._corpus
        with self._lock:
            # Re-check inside the lock: several threads can arrive at a cold
            # cache together, and only the first should do the work.
            if self._loaded:
                return self._corpus
            corpus = None
            if self._enabled:
                try:
                    corpus = Corpus.open(self._index_dir)
                except Exception:  # noqa: BLE001 - loading must never propagate
                    logger.exception("Reference index at %s could not be loaded", self._index_dir)
                    corpus = None
            self._corpus = corpus
            self._loaded = True
            if corpus is None and not self._warned:
                self._warned = True
                logger.info(
                    "No reference index at %s; the assistant will answer without it. "
                    "Build one with `python -m app.retrieval.build`.",
                    self._index_dir,
                )
            return corpus

    def reload(self) -> None:
        """Drop the loaded index so the next query picks up a fresh build.

        Called by the build CLI when it runs in-process. A rebuild swaps the
        directory underneath a live server, and without this the old arrays stay
        mapped for the lifetime of the process.
        """
        with self._lock:
            self._corpus = None
            self._loaded = False
            self._warned = False

    # -- reading ------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether there is an index to search. Never raises."""
        return self._ensure_loaded() is not None

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        language: str | None = None,
    ) -> list[Passage]:
        """Best passages for `query`. Returns `[]` for every failure mode.

        `language` is the caller's known preference -- the language CATIA's
        interface is running in, or the one the conversation is being held in.
        It is a hint, not a filter: an unrecognised code, or one no document
        happens to be written in, simply forgoes the preference rather than
        emptying the result.

        When no preference is supplied the query itself is examined, which
        catches the common case of a user typing French at an assistant that
        was never told what language they work in. Short queries are usually
        undetectable and correctly yield no preference at all.

        **The query is widened before it is run.** A lexical index matches
        terms, and half these manuals are French, so an English question for
        "draft angle" cannot reach the pages that call it `dépouille` however
        good the scorer is. `app.catia_kb` recognises the CATIA entity behind
        the words and adds its name in the other languages, which is what makes
        a bilingual corpus searchable from one language. The preference above
        then decides which of the now-reachable pages ranks first -- widening
        and preferring do different jobs and both are wanted.

        Expansion is additive and capped, and it can only fail closed: on any
        error the original query is used, so the worst case is the behaviour
        this method had before.
        """
        if not query or not query.strip():
            return []
        corpus = self._ensure_loaded()
        if corpus is None:
            return []
        try:
            prefer = detect_query(query, fallback=normalise(language))
            widened = self._widen(query, language=prefer or language)
            return merge_adjacent(
                corpus.search(
                    widened,
                    limit=limit,
                    prefer_language=prefer,
                    # The floor stays measured against what the user asked for.
                    # Synonyms are additive to the score and must not raise the
                    # bar for breadth of match -- see `Corpus.search`.
                    coverage_query=query if widened != query else None,
                )
            )[:limit]
        except Exception:  # noqa: BLE001 - see the module docstring
            logger.exception("Reference lookup failed for %r", query[:120])
            return []

    @staticmethod
    def _widen(query: str, *, language: str | None) -> str:
        """`query` plus its cross-language synonyms, or `query` unchanged."""
        if not settings.catia_knowledge_expand_queries:
            return query
        # Imported here rather than at module scope: the retrieval package is
        # usable on its own, and a hard dependency on the CATIA reference would
        # make the layering a lie.
        from app.catia_kb import catia_knowledge

        return catia_knowledge().expand(query, language=language)

    def stats(self) -> dict[str, Any]:
        """Index statistics for the health endpoint, or a reason there are none."""
        corpus = self._ensure_loaded()
        if corpus is None:
            return {"available": False, "index_dir": str(self._index_dir)}
        stats: dict[str, Any] = {"available": True, "index_dir": str(self._index_dir)}
        stats.update(corpus.stats())
        try:
            stats["stale"] = corpus.is_stale(self._source_dirs, exclude=self._exclude)
        except Exception:  # noqa: BLE001 - a stat() failure is not a health failure
            stats["stale"] = None
        return stats

    @property
    def source_dirs(self) -> list[Path]:
        return list(self._source_dirs)

    @property
    def index_dir(self) -> Path:
        return self._index_dir

    @property
    def exclude(self) -> list[Path]:
        """Paths the scan skips. The build CLI passes these straight through."""
        return list(self._exclude)

    def sources(self) -> list[Path]:
        """Every indexable file currently on disk, whether or not it is indexed."""
        return discover_sources(self._source_dirs, exclude=self._exclude)


_service: KnowledgeService | None = None
_service_lock = threading.Lock()


def knowledge_service() -> KnowledgeService:
    """The process-wide service, built from settings on first use.

    A module-level singleton rather than a FastAPI dependency because the index
    is process state, not request state: rebuilding the handle per request would
    re-read tens of megabytes of numpy arrays every time.
    """
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is None:
            _service = KnowledgeService(
                index_dir=settings.knowledge_index_dir,
                source_dirs=settings.knowledge_source_dirs,
                enabled=settings.knowledge_enabled,
            )
    return _service


def reset_knowledge_service() -> None:
    """Forget the singleton. For tests, which point it at their own fixtures."""
    global _service
    with _service_lock:
        _service = None


def format_passages(passages: Sequence[Passage]) -> str:
    """Render passages as the text a model reads.

    Each passage is labelled with its citation so the model can attribute what
    it says, and truncated defensively: a passage is bounded by the chunker, but
    this is the boundary where corpus text enters a prompt, and a boundary that
    trusts its input is a boundary that has not been thought about.
    """
    if not passages:
        return ""
    blocks: list[str] = []
    for index, passage in enumerate(passages, start=1):
        body = passage.text.strip()
        if len(body) > 4_000:
            body = body[:4_000].rsplit(" ", 1)[0] + " …"
        blocks.append(f"[{index}] {passage.citation()}\n{body}")
    return "\n\n".join(blocks)
