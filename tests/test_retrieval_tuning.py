"""Parameter tuning validation for the BM25 retrieval stack.

Sweeps parameter values (k1, b, coverage_weight) to verify that current defaults
(k1=1.5, b=0.75, coverage_weight=0.35) are optimal or near-optimal (Pareto frontier)
for the actual manual dataset.
"""

from __future__ import annotations

from pathlib import Path
import pytest

from app.retrieval.bm25 import BM25Index, DEFAULT_K1, DEFAULT_B, DEFAULT_COVERAGE_WEIGHT
from app.retrieval.analyze import analyze_query

INDEX_DIR = Path("data/bm25/index")
REAL_INDEX_EXISTS = (INDEX_DIR / "index.npz").exists() and (INDEX_DIR / "manifest.json").exists()

# Evaluation dataset: (query, target document filename substring)
EVAL_SET = [
    ("edge fillet radius", "Chapter5"),
    ("draft angle neutral element", "Chapter5"),
    ("von mises stress", "Koh"),
    ("clamp restraint boundary condition", "Koh"),
    ("poche esquisse profondeur", "part_design"),
    ("contrainte coincidence", "Assembly_Design"),
    ("maillage elements finis", "Structural"),
    ("rendering material shading", "photo-studio"),
    ("extrapolate surface boundary", "Wireframe"),
    ("sheet metal bend radius", "Basics-Part-I"),
    ("multi-sections solid loft", "Basics-Part-II"),
    ("kinematics revolute joint", "DMU_Kinematics"),
]


def calculate_mrr(index: BM25Index, passages_sources: list[str]) -> float:
    rr_total = 0.0
    for query, target in EVAL_SET:
        terms = analyze_query(query)
        hits = index.search(terms, limit=10)
        rank = 0
        for i, hit in enumerate(hits, start=1):
            source = passages_sources[hit.doc_id]
            if target.lower() in source.lower():
                rank = i
                break
        if rank > 0:
            rr_total += 1.0 / rank
    return rr_total / len(EVAL_SET)


@pytest.mark.skipif(not REAL_INDEX_EXISTS, reason="Real BM25 index not found in data/bm25/index/")
class TestBM25ParameterTuning:
    def test_default_parameters_are_optimal_or_near_optimal(self):
        # Load existing index arrays
        orig_index = BM25Index.load(INDEX_DIR / "index.npz")
        
        # Read passages sources for document matching
        import json
        passages_sources = []
        with (INDEX_DIR / "passages.jsonl").open("r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                passages_sources.append(rec["source"])

        # Calculate MRR for default parameters
        default_mrr = calculate_mrr(orig_index, passages_sources)

        # Parameter grid
        k1_grid = [1.2, 1.5, 1.8]
        b_grid = [0.5, 0.75, 0.9]
        cov_grid = [0.2, 0.35, 0.5]

        max_mrr = 0.0
        for k1 in k1_grid:
            for b in b_grid:
                for cov in cov_grid:
                    test_index = BM25Index(
                        vocabulary=orig_index.vocabulary,
                        term_offsets=orig_index.term_offsets,
                        postings_docs=orig_index.postings_docs,
                        postings_tf=orig_index.postings_tf,
                        doc_lengths=orig_index.doc_lengths,
                        idf=orig_index.idf,
                        avg_doc_length=orig_index.avg_doc_length,
                        k1=k1,
                        b=b,
                        coverage_weight=cov,
                    )
                    mrr = calculate_mrr(test_index, passages_sources)
                    if mrr > max_mrr:
                        max_mrr = mrr

        # Defaults should be within 0.05 of the maximum possible MRR on the grid
        assert default_mrr >= max_mrr - 0.05, (
            f"Default parameters (k1={DEFAULT_K1}, b={DEFAULT_B}, cov={DEFAULT_COVERAGE_WEIGHT}) "
            f"gave MRR={default_mrr:.4f}, but maximum on grid was MRR={max_mrr:.4f}"
        )
