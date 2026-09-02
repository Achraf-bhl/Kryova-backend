"""Integration tests for the BM25 retrieval system against the real manual corpus.

Guarded by `pytest.mark.skipif` so that these tests run when the real index exists at
`data/bm25/index/`.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.retrieval.corpus import Corpus
from app.retrieval.service import KnowledgeService

INDEX_DIR = Path("data/bm25/index")
REAL_INDEX_EXISTS = (INDEX_DIR / "index.npz").exists() and (INDEX_DIR / "manifest.json").exists()


@pytest.mark.skipif(not REAL_INDEX_EXISTS, reason="Real BM25 index not found in data/bm25/index/")
class TestRealCorpusRetrieval:
    @pytest.fixture(autouse=True)
    def setup_corpus(self):
        self.corpus = Corpus.open(INDEX_DIR)
        assert self.corpus is not None, "Corpus should load cleanly"

    @pytest.mark.parametrize(
        ("query", "expected_substr"),
        [
            ("edge fillet radius", "Chapter5"),
            ("draft angle neutral element", "Chapter5"),
            ("von mises stress", "Koh"),
            ("clamp restraint boundary condition", "Koh"),
            ("poche esquisse profondeur", "part_design"),
            ("contrainte coincidence", "Assembly_Design"),
            ("maillage elements finis", "Structural"),
            ("rendering material shading", "photo-studio"),
            ("extrapolate surface boundary", "Wireframe"),
        ],
    )
    def test_top_results_relevance(self, query: str, expected_substr: str):
        hits = self.corpus.search(query, limit=5)
        assert hits, f"Query '{query}' returned no hits"
        sources = [hit.source for hit in hits]
        assert any(
            expected_substr.lower() in src.lower() for src in sources
        ), f"Expected '{expected_substr}' in top 5 sources for query '{query}', got: {sources}"

    def test_multi_term_coverage_floor(self):
        # A 3-term query should return hits matching at least 2 terms when available
        hits = self.corpus.search("draft angle neutral element", limit=5)
        for hit in hits:
            assert hit.matched_terms >= 2

    def test_knowledge_service_language_preference(self):
        service = KnowledgeService(
            index_dir=INDEX_DIR,
            source_dirs=[Path("data/bm25/sources"), Path("data")],
        )
        assert service.available

        # Asking in French with language preference
        fr_hits = service.search("contrainte coincidence assemblage", limit=3, language="fr")
        assert fr_hits, "Expected hits for French query"
        assert fr_hits[0].language == "fr" or "FR-" in fr_hits[0].source

        # Asking in English with language preference
        en_hits = service.search("edge fillet radius", limit=3, language="en")
        assert en_hits, "Expected hits for English query"
        assert en_hits[0].language == "en" or "EN-" in en_hits[0].source
