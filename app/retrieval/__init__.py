"""Lexical retrieval over the reference corpus.

The assistant answers questions about CATIA and FEA, and the authoritative
answers live in a few hundred megabytes of vendor manuals. This package is what
lets it consult them: extract, chunk, index, search.

**Why lexical rather than embeddings.** Three properties of this deployment
decide it, and they all point the same way.

The provider is pluggable and the default is local. `AI_PROVIDER` is `ollama`,
`anthropic` or `openai_compatible`, and **Anthropic publishes no embedding
model at all** -- an embedding-based index would either force a second vendor
into a deployment that had deliberately chosen one, or force an extra model pull
onto an install whose whole point is that it runs offline with no key. A lexical
index has no model, so it works identically under all three.

The corpus is technical manuals, which is the regime where lexical retrieval is
strongest rather than merely adequate. What discriminates between passages here
is exact terms -- `M6`, `Ø12`, `tet4`, `Multi-sections Solid`, `V5R21` -- and
those are precisely what embeddings blur and what a term index matches exactly.
Published comparisons on engineering and financial documents put BM25 ahead of
strong commercial embedding models on most metrics for exactly this reason.

The corpus is bilingual and fixed. French and English manuals sit in one index;
accent folding handles the query typed without accents, which no amount of
embedding quality does for free.

The honest caveat: on open-domain prose, hybrid lexical-plus-dense retrieval
beats either alone, and that remains true here. `Retriever` is a protocol and
`Corpus.search` is behind it precisely so a dense stage can be added later and
fused, without the agent or the tool layer knowing a second retriever exists.
That is the same seam discipline the solver and job queue already follow.

**The layers**, each usable and testable alone:

- `analyze`  — text to index terms; bilingual, jargon-preserving
- `bm25`     — the scorer, over flat numpy arrays
- `extract`  — PDF and text to pages, with a fallback chain of extractors
- `chunking` — pages to retrievable passages, headings carried
- `corpus`   — build, load, search; passage text read lazily off disk
- `service`  — the process-wide handle, whose `search` never raises
"""

from app.retrieval.analyze import analyze, analyze_query
from app.retrieval.bm25 import BM25Index, Hit
from app.retrieval.corpus import (
    BuildReport,
    Corpus,
    Passage,
    build,
    discover_sources,
    merge_adjacent,
)
from app.retrieval.service import (
    KnowledgeService,
    format_passages,
    knowledge_service,
    reset_knowledge_service,
)

__all__ = [
    "BM25Index",
    "BuildReport",
    "Corpus",
    "Hit",
    "KnowledgeService",
    "Passage",
    "analyze",
    "analyze_query",
    "build",
    "discover_sources",
    "format_passages",
    "knowledge_service",
    "merge_adjacent",
    "reset_knowledge_service",
]
