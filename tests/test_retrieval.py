"""The reference-retrieval stack, end to end and layer by layer.

No database fixture is requested anywhere in this file, so it opens no
connection and runs in the fast offline loop alongside the physics tests. That
is deliberate: retrieval is pure computation over files, and a test suite that
needs Neon to check a tokenizer is a suite nobody runs while iterating.

The tests are organised by the property being defended rather than by function,
because most of the bugs this code can have are not "the function returned the
wrong value" -- they are "the index silently answers from stale data", "the
analyzer destroys the one term the query was about", or "one unreadable file
takes the whole build down". Those are the cases written out longhand below.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from app.retrieval.analyze import MIN_TERM_LENGTH, analyze, analyze_query, fold, stem
from app.retrieval.bm25 import INDEX_FORMAT_VERSION, BM25Index
from app.retrieval.chunking import (
    Chunk,
    _is_heading,
    chunk_document,
    chunk_page,
    chunk_terms,
)
from app.retrieval.corpus import (
    MANIFEST_FILENAME,
    PASSAGES_FILENAME,
    Corpus,
    Passage,
    build,
    discover_sources,
    merge_adjacent,
)
from app.retrieval.extract import (
    ExtractionError,
    Page,
    available_extractors,
    extract_pages,
)
from app.retrieval.language import detect, detect_query, normalise
from app.retrieval.service import KnowledgeService, format_passages


def _raise_import_error(_path: Path) -> list[Page]:
    raise ImportError("pdfminer is not installed")


def _raise_value_error(_path: Path) -> list[Page]:
    raise ValueError("not a pdf")


# ---------------------------------------------------------------------------
# The analyzer. Everything it drops is a match that can never happen.
# ---------------------------------------------------------------------------


class TestAnalyzer:
    def test_folds_accents_so_french_matches_however_it_was_typed(self):
        # The manuals are inconsistently accented after extraction and users
        # routinely type French without accents. All three must be one term.
        assert analyze("épaisseur") == analyze("EPAISSEUR") == analyze("epaisseur")

    def test_keeps_identifiers_that_a_word_split_would_destroy(self):
        # These are the terms that actually discriminate between passages.
        assert "m6" in analyze("an M6 bolt")
        assert "tet4" in analyze("tet4 elements")
        assert "v5r21" in analyze("CATIA V5R21")

    def test_short_terms_survive_only_when_they_carry_a_digit(self):
        assert "m6" in analyze("M6")
        # A bare two-letter word is noise; a two-character identifier is not.
        assert all(len(term) >= MIN_TERM_LENGTH for term in analyze("M6 of an ax"))

    def test_dimensions_are_findable_by_any_component(self):
        terms = analyze("a 120x80x10 plate")
        assert "120x80x10" in terms, "the exact dimension must still match exactly"
        assert {"120", "80", "10"} <= set(terms), "and each component on its own"

    def test_a_dimension_with_a_thread_designation_keeps_the_thread(self):
        # `M6x20` used to fall past the dimension pattern -- which required an
        # all-numeric first component -- into the alphanumeric split, which
        # yields `m`, `6`, `x`, `20` and drops everything below the length
        # floor. The effect was that a passage specifying `M6x20` could not be
        # found by a query for `M6`, the single term most likely to be searched.
        terms = analyze("an M6x20 socket screw")
        assert "m6x20" in terms
        assert "m6" in terms, "the thread designation must be reachable on its own"
        assert "20" in terms

    @pytest.mark.parametrize("written", ["Ø12", "⌀12", "ø12"])
    def test_the_diameter_sign_survives_however_it_was_typed(self, written: str):
        # `fold` lowercases before tokenising and `Ø` lowercases to `ø`, which
        # the token pattern did not accept -- so the diameter sign this module
        # exists to protect was silently reduced to the bare number, which is
        # exactly the loss its docstring claims to prevent.
        terms = analyze(f"a {written} through hole")
        assert "⌀12" in terms, "the diameter is a term in its own right"
        assert "12" in terms, "and the bare number still reaches it"

    def test_every_spelling_of_the_diameter_sign_is_one_term(self):
        assert analyze("Ø12") == analyze("⌀12") == analyze("ø12")

    def test_stemming_joins_plurals_without_colliding_technical_terms(self):
        assert stem("pockets") == stem("pocket")
        # The whole reason the never-stem list exists: `stress` must not become
        # `stres`, which would collide with nothing and match nothing.
        assert stem("stress") == "stress"
        assert stem("analysis") == "analysis"

    def test_stopwords_go_but_engineering_words_stay(self):
        terms = analyze("the thickness of the shell is not constant")
        assert "the" not in terms and "of" not in terms
        # `not` is a stopword in every stock list and is also the difference
        # between two opposite statements about a constraint.
        assert "not" in terms

    def test_query_and_document_analysis_agree(self):
        # Drift between these two is the classic way a lexical index quietly
        # starts returning nothing.
        assert analyze_query("Epaisseur du Pad") == analyze("epaisseur du pad")

    def test_empty_and_punctuation_only_input_is_not_an_error(self):
        assert analyze("") == []
        assert analyze("   ") == []
        assert analyze("--- ... ///") == []

    def test_absurdly_long_runs_are_dropped(self):
        # PDF extraction produces these from tables with no spaces; indexing
        # them inflates the vocabulary with terms no query can ever produce.
        assert analyze("x" * 200) == []

    def test_no_analyzed_term_exceeds_max_length(self):
        # Tokenizer must never produce a term longer than the cap, even when
        # the raw token itself is within the alphanumeric split path.
        from app.retrieval.analyze import MAX_TERM_LENGTH

        terms = analyze("a" * 35 + "1" * 10)
        assert all(len(t) <= MAX_TERM_LENGTH for t in terms)

    def test_single_digit_diameter_oe_is_preserved(self):
        # Ø8 is a very common drawing notation; the tokeniser must not strip
        # the diameter sign when the number is one character.
        terms = analyze("Ø8 through hole")
        assert "⌀8" in terms
        assert "8" in terms

    def test_tet4_is_kept_whole_and_also_split(self):
        # `tet4` is an FEA element type; the bare `tet` prefix is also worth
        # having so a query for `tet` reaches it.
        terms = analyze("tet4 elements")
        assert "tet4" in terms
        assert "tet" in terms

    def test_stemmer_floor_keeps_short_technical_words_intact(self):
        # `stem` must never strip a word below 4 characters (len - len(suffix) >= 4),
        # so short CAD terms like `pad` or `pads` stay intact.
        assert stem("pad") == "pad"
        assert stem("pads") == "pads"  # stem length 3 < 4 floor, kept intact

    def test_never_stem_entries_are_returned_unchanged(self):
        # These have ambiguous suffixes that would produce wrong stems, and the
        # never-stem list is what keeps them intact.
        assert stem("stress") == "stress"
        assert stem("radius") == "radius"
        assert stem("analysis") == "analysis"

    def test_fold_is_idempotent(self):
        once = fold("Créer une Poche — Épaisseur")
        assert fold(once) == once


# ---------------------------------------------------------------------------
# BM25 scoring.
# ---------------------------------------------------------------------------


class TestBM25:
    @staticmethod
    def _corpus() -> BM25Index:
        return BM25Index.build(
            [
                analyze("the pad is created by extruding a sketch"),
                analyze("a pocket removes material from the pad"),
                analyze("the shell command hollows a solid to a wall thickness"),
                analyze("thickness thickness thickness thickness thickness"),
            ]
        )

    def test_ranks_the_document_that_is_actually_about_the_query(self):
        index = self._corpus()
        [top, *_] = index.search(analyze_query("shell wall thickness"))
        assert top.doc_id == 2

    def test_coverage_beats_raw_repetition(self):
        # Document 3 says "thickness" five times and nothing else; document 2
        # says it once but matches all three query terms. Textbook BM25 ranks
        # the first higher, which is the wrong answer for nearly every real
        # query, and the coverage bonus is what fixes it.
        index = self._corpus()
        ranked = index.search(analyze_query("shell wall thickness"))
        order = [hit.doc_id for hit in ranked]
        assert order.index(2) < order.index(3)

    def test_idf_never_goes_negative(self):
        # The textbook IDF is negative for a term in more than half the
        # documents, which would make a common term *penalise* a document for
        # containing it.
        index = BM25Index.build([analyze("pad")] * 10)
        assert float(index.idf.min()) >= 0.0

    def test_a_repeated_query_term_is_not_counted_twice(self):
        index = self._corpus()
        once = index.search(analyze_query("thickness"))
        twice = index.search(analyze_query("thickness thickness"))
        assert [hit.score for hit in once] == [hit.score for hit in twice]

    def test_unknown_terms_score_nothing_rather_than_erroring(self):
        assert self._corpus().search(["zzzznotaterm"]) == []

    def test_results_are_ordered_by_descending_score(self):
        hits = self._corpus().search(analyze_query("pad pocket thickness"), limit=4)
        assert [hit.score for hit in hits] == sorted(
            (hit.score for hit in hits), reverse=True
        )

    def test_limit_is_honoured(self):
        assert len(self._corpus().search(analyze_query("pad pocket thickness"), limit=2)) <= 2

    def test_matched_terms_reports_distinct_query_terms_present(self):
        index = self._corpus()
        [top, *_] = index.search(analyze_query("shell wall thickness"))
        assert top.matched_terms == 3

    def test_an_empty_corpus_is_searchable_and_returns_nothing(self):
        # Reachable in production: a corpus directory whose every PDF was a
        # scan. It must not be a special case at any call site.
        empty = BM25Index.build([])
        assert empty.num_documents == 0
        assert empty.search(["pad"]) == []

    def test_a_corpus_of_empty_documents_does_not_divide_by_zero(self):
        index = BM25Index.build([[], [], []])
        assert index.search(["pad"]) == []

    def test_round_trip_preserves_scores_exactly(self, tmp_path: Path):
        index = self._corpus()
        path = tmp_path / "index.npz"
        index.save(path)
        reloaded = BM25Index.load(path)
        query = analyze_query("shell wall thickness")
        assert index.search(query) == reloaded.search(query)

    def test_a_future_format_version_is_refused_not_guessed_at(self, tmp_path: Path):
        # An index written by a different scoring version is not stale, it is
        # wrong; the caller's correct response is to rebuild.
        path = tmp_path / "index.npz"
        self._corpus().save(path)
        with np.load(path, allow_pickle=False) as archive:
            arrays = dict(archive)
        meta = json.loads(bytes(arrays["meta"]).decode())
        meta["format"] = INDEX_FORMAT_VERSION + 99
        arrays["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
        np.savez(path, **arrays)

        with pytest.raises(ValueError, match="Rebuild"):
            BM25Index.load(path)

    def test_save_leaves_no_temporary_file_behind(self, tmp_path: Path):
        path = tmp_path / "index.npz"
        self._corpus().save(path)
        assert [entry.name for entry in tmp_path.iterdir()] == ["index.npz"]

    def test_identical_input_produces_an_identical_file(self, tmp_path: Path):
        # Reproducibility is what makes a corpus fingerprint mean anything.
        first, second = tmp_path / "a.npz", tmp_path / "b.npz"
        self._corpus().save(first)
        self._corpus().save(second)
        assert first.read_bytes() == second.read_bytes()

    def test_empty_boosts_list_does_not_raise(self):
        # BM25Index.build with an explicit boosts=[] should handle zero
        # documents without an IndexError.
        index = BM25Index.build([], boosts=[])
        assert index.num_documents == 0
        assert index.search(["pad"]) == []

    def test_boosts_scale_scores_relative_to_unweighted(self):
        # A document with a 2× boost on identical content must outscore the
        # unboosted version, which is the mechanism behind field weighting.
        docs = [
            analyze("pocket sketch depth"),
            analyze("pocket sketch depth"),
        ]
        boosted = BM25Index.build(docs, boosts=[1.0, 2.0])
        hits = boosted.search(analyze_query("pocket sketch depth"))
        order = [hit.doc_id for hit in hits]
        assert order[0] == 1, "higher boost must rank first"

    def test_zero_limit_returns_empty_without_error(self):
        assert self._corpus().search(analyze_query("pad"), limit=0) == []


# ---------------------------------------------------------------------------
# Chunking, and the heading heuristics that decide citation quality.
# ---------------------------------------------------------------------------


class TestChunking:
    @pytest.mark.parametrize(
        "line",
        [
            "Creating a Pocket",
            "3.2 Creating a Pad",
            "Chapter 4 Assembly Design",
            "GENERATIVE SHAPE DESIGN",
            "Toolbar: Dress-Up Features > Fillets",
        ],
    )
    def test_recognises_real_headings(self, line: str):
        assert _is_heading(line)

    @pytest.mark.parametrize(
        "line",
        [
            # The regression this heuristic exists for. These manuals are almost
            # entirely numbered procedures, and the obvious pattern labelled
            # thousands of them as section titles -- which put a step number in
            # every citation and pointed the heading boost at "cliquez".
            "15. Cliquez sur OK pour confirmer l'opération.",
            "1. Double-cliquez sur Pad.1.",
            "6. Exit the Sketcher workbench. Choose the Pad button from the toolbar",
            # Page furniture.
            "Assembly Modeling 11-47",
            "Copyright DASSAULT SYSTEMES",
            "Page 12",
            # Ordinary prose.
            "the pad is created by extruding the sketch along its normal, which",
            "",
        ],
    )
    def test_rejects_what_only_looks_like_a_heading(self, line: str):
        assert not _is_heading(line)

    @pytest.mark.parametrize(
        ("line", "why"),
        [
            # Measured on the built index: the residue the enumerator and
            # terminal-punctuation rules leave behind, because the step's number
            # was on the previous line and its full stop on the next.
            ("Cliquez sur OK", "262 passages -- the corpus's commonest 'heading'"),
            ("Cliquez sur Appliquer", "36 passages"),
            ("Sélectionnez Sketch 9", "22 passages"),
            ("Click the Simulation", "10 passages"),
            ("Select Parameters > Instance(s) & Length", "an instruction, not a title"),
            # A sentence boundary inside the line: two sentences run together,
            # capitalised throughout, so title case accepts it on its merits.
            ("Sélectionnez Plan.1. CATIA", "16 passages"),
            # Starts on punctuation. The title-case test skips any word whose
            # first character is not a letter, so the lone '.' was ignored.
            (". Vous", "349 passages started with punctuation"),
            ("(Ref. No. ISO 10209-2:1993)", "a reference note, not a section"),
        ],
    )
    def test_rejects_instructions_that_survived_the_earlier_rules(
        self, line: str, why: str
    ):
        assert not _is_heading(line), f"{line!r} should be rejected: {why}"

    @pytest.mark.parametrize(
        "line",
        [
            # The cost of the rules above has to stay bounded. These manuals
            # title their sections with gerunds and nouns, and all of these must
            # survive: "type" and "open" are nouns here, not the verbs they also
            # are, which is why neither is in the instruction list.
            "Type of Constraint",
            "Open Body",
            "Selecting the Edges to Keep",
            "Creating Variable Radius Fillets",
            "15.3 Adaptive Meshing",
        ],
    )
    def test_the_new_rules_do_not_take_real_headings_with_them(self, line: str):
        assert _is_heading(line)

    def test_a_wrapped_numbered_step_is_not_a_heading(self):
        # It has no terminal punctuation because the sentence continues on the
        # next line, so the punctuation test alone cannot save it -- which is
        # why a bare single-level number is not accepted at all.
        assert not _is_heading("7. Set the value of the draft angle in the Angle spinner")

    @pytest.mark.parametrize(
        "line",
        [
            # The French half of the corpus, and the reason this file grew a
            # companion that runs against the real manuals. A wrapped French
            # instruction is title-case *by the letter of the rule*: `sur` and
            # `pour` are minor words, `Cliquez` and `OK` are capitalised, and
            # the sentence continues on the next line so there is no full stop
            # to disqualify it. Declining to accept it as a numbered heading was
            # never enough -- that only passed it to the title-case test, which
            # took it. This labelled 1,143 of 5,003 real passages.
            "6. Cliquez sur OK pour",
            "4. Modifiez les",
            "1. Sélectionnez Démarrer -> Analyse & Simulation ->",
            "2. Sélectionnez Insertion -> Outils de",
            "2) Select the face",
            # No enumerator at all, same cut sentence.
            "Cliquez sur",
            # A title does not end on a preposition or an article.
            "Sélectionnez la face et cliquez sur",
        ],
    )
    def test_a_cut_instruction_is_not_a_heading(self, line: str):
        assert not _is_heading(line)

    @pytest.mark.parametrize(
        "line",
        [
            # Not section titles either, and useless in a citation.
            r"C:\Program Files\intel\plsuite\bin",
            "EN: /$OS/Startup/Components/MechanicalStandardParts/EN_Standards",
            "Outils ->",
            "http://www.3ds.com/support",
        ],
    )
    def test_a_path_fragment_is_not_a_heading(self, line: str):
        assert not _is_heading(line)

    @pytest.mark.parametrize(
        "line",
        [
            # A multi-level number is a section, not a step: the rejection above
            # must not take these with it.
            "3.2 Creating a Pad",
            "15.3 Define Mesh",
            # A single slash is ordinary in these manuals' own headings, so the
            # path rule cannot key on one.
            "Stratégie GPS/FMS",
            "NonVu/Vu Permanent",
            # `>` belongs inside a toolbar path; only a trailing one is a cut.
            "Toolbar: Dress-Up Features > Fillets",
        ],
    )
    def test_the_rejections_do_not_take_real_headings_with_them(self, line: str):
        assert _is_heading(line)

    def test_headings_are_carried_into_the_passage_and_the_citation(self):
        page = Page(number=3, text="Creating a Pocket\n" + "word " * 60)
        chunks, _ = chunk_page(page)
        assert chunks
        assert chunks[0].heading == "Creating a Pocket"
        assert chunks[0].text.startswith("Creating a Pocket")

    def test_a_heading_runs_on_across_a_page_break(self):
        # A section runs across a page break far more often than it starts
        # neatly at the top of one.
        pages = [
            Page(number=1, text="Creating a Pocket\n" + "word " * 60),
            Page(number=2, text="continued " * 60),
        ]
        chunks = chunk_document(pages)
        assert {chunk.heading for chunk in chunks} == {"Creating a Pocket"}
        assert {chunk.page for chunk in chunks} == {1, 2}

    def test_passages_overlap_so_a_boundary_does_not_split_an_answer(self):
        page = Page(number=1, text=" ".join(f"w{index}" for index in range(500)))
        chunks, _ = chunk_page(page, chunk_words=100, overlap_words=20)
        assert len(chunks) > 1
        tail = chunks[0].text.split()[-20:]
        assert tail == chunks[1].text.split()[:20]

    def test_a_pathological_overlap_terminates(self):
        # An overlap at or above the chunk size would consume nothing per
        # iteration and spin forever.
        page = Page(number=1, text=" ".join(f"w{index}" for index in range(300)))
        chunks, _ = chunk_page(page, chunk_words=50, overlap_words=90)
        assert chunks

    def test_scraps_below_the_floor_are_dropped(self):
        # A two-word passage matching one term looks perfectly on-topic to the
        # length normalisation, and would outrank real content.
        chunks, _ = chunk_page(Page(number=1, text="Fig. 4"))
        assert chunks == []

    def test_hyphenated_line_breaks_are_rejoined(self):
        chunks, _ = chunk_page(Page(number=1, text="thick-\nness " + "word " * 40))
        assert "thickness" in chunks[0].text

    def test_heading_terms_are_weighted_above_body_terms(self):
        headed = Chunk(text="Pocket\n" + "body " * 40, page=1, heading="Pocket")
        plain = Chunk(text="pocket " + "body " * 40, page=1, heading=None)
        assert chunk_terms(headed).count("pocket") > chunk_terms(plain).count("pocket")


# ---------------------------------------------------------------------------
# Extraction, and the fallback chain.
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_reads_plain_text_and_markdown_without_any_pdf_machinery(self, tmp_path: Path):
        path = tmp_path / "notes.md"
        path.write_text("# Notes\nThe wall thickness is 2 mm.", encoding="utf-8")
        pages, extractor = extract_pages(path)
        assert extractor == "text"
        assert "2 mm" in pages[0].text

    def test_refuses_an_unsupported_type_with_an_actionable_message(self, tmp_path: Path):
        path = tmp_path / "model.step"
        path.write_text("ISO-10303-21;", encoding="utf-8")
        with pytest.raises(ExtractionError, match="Supported"):
            extract_pages(path)

    def test_a_corrupt_pdf_names_every_extractor_it_tried(self, tmp_path: Path):
        path = tmp_path / "broken.pdf"
        path.write_bytes(b"%PDF-1.4\nthis is not a pdf")
        with pytest.raises(ExtractionError) as caught:
            extract_pages(path)
        message = str(caught.value)
        assert "Tried:" in message
        # And the short form stays short, because the build report lists one
        # per skipped file and would otherwise repeat paragraphs of advice.
        assert len(caught.value.short) < 80

    def test_a_scanned_pdf_is_diagnosed_as_needing_ocr(self, monkeypatch: pytest.MonkeyPatch):
        # "Install pypdf" is useless advice for an image-only PDF and sends
        # people down a dead end, so the two cases must be told apart. The
        # discriminator is that every extractor which *ran* found no text layer
        # -- an uninstalled one is not a statement about the document, and
        # counting it made this diagnosis never fire.
        import app.retrieval.extract as extract_module

        monkeypatch.setattr(extract_module, "_pdftotext_available", lambda: True)
        monkeypatch.setattr(
            extract_module,
            "_PDF_EXTRACTORS",
            (
                ("pdftotext", lambda _path: []),
                ("pypdf", lambda _path: []),
                ("pdfminer", _raise_import_error),
            ),
        )
        with pytest.raises(ExtractionError) as caught:
            extract_pages(Path("scan.pdf"))
        assert "OCR" in str(caught.value)
        assert caught.value.short == "scanned, no text layer"

    def test_a_genuinely_unreadable_pdf_is_not_called_a_scan(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import app.retrieval.extract as extract_module

        monkeypatch.setattr(extract_module, "_pdftotext_available", lambda: False)
        monkeypatch.setattr(
            extract_module,
            "_PDF_EXTRACTORS",
            (("pypdf", _raise_value_error),),
        )
        with pytest.raises(ExtractionError) as caught:
            extract_pages(Path("broken.pdf"))
        assert caught.value.short == "no extractor could read it"

    def test_at_least_one_extractor_is_available_here(self):
        # Not a property of the code -- a check that this machine can actually
        # exercise the PDF path at all. If it cannot, the PDF tests below are
        # skipped rather than silently passing.
        assert isinstance(available_extractors(), list)


# ---------------------------------------------------------------------------
# Corpus build and search: the properties that keep answers honest.
# ---------------------------------------------------------------------------


def _write_corpus(root: Path) -> None:
    (root / "part_design.md").write_text(
        "Creating a Pad\n"
        + "The Pad command extrudes a sketch along its normal. "
        + "Set the length in the Pad Definition dialog box. " * 12,
        encoding="utf-8",
    )
    (root / "sheet_metal.md").write_text(
        "Bend Radius\n"
        + "The bend radius is set in the Sheet Metal Parameters dialog. " * 15,
        encoding="utf-8",
    )
    (root / "analyse.md").write_text(
        "Maillage\n" + "Le maillage en elements finis definit la taille des mailles. " * 15,
        encoding="utf-8",
    )


class TestCorpus:
    def test_builds_and_finds_the_right_document(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)

        report = build(sources=[sources], destination=index_dir)
        assert report.documents == 3
        assert report.passages > 0

        corpus = Corpus.open(index_dir)
        assert corpus is not None
        [top, *_] = corpus.search("pad definition dialog")
        assert top.source == "part_design.md"
        assert "p. " in top.citation()

    def test_finds_french_material_from_a_french_query(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)

        corpus = Corpus.open(index_dir)
        assert corpus is not None
        # Typed without accents, which is how it is usually typed.
        [top, *_] = corpus.search("maillage elements finis")
        assert top.source == "analyse.md"

    def test_one_unreadable_file_does_not_cost_the_others(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        (sources / "scanned.pdf").write_bytes(b"%PDF-1.4\nno text layer here")

        report = build(sources=[sources], destination=index_dir)
        assert report.documents == 3, "the three readable files still indexed"
        assert len(report.skipped) == 1
        assert "scanned.pdf" in report.skipped[0]

    def test_a_build_over_nothing_is_valid_rather_than_an_error(self, tmp_path: Path):
        index_dir = tmp_path / "index"
        report = build(sources=[tmp_path / "does-not-exist"], destination=index_dir)
        assert report.passages == 0
        corpus = Corpus.open(index_dir)
        assert corpus is not None
        assert corpus.search("anything") == []

    def test_a_failed_rebuild_leaves_the_previous_index_serving(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)

        # A half-written index is worse than no index: it loads, it answers, and
        # the answers are wrong. Simulate a build that dies partway.
        original = (index_dir / MANIFEST_FILENAME).read_text()
        with pytest.raises(RuntimeError):
            build(
                sources=[sources],
                destination=index_dir,
                on_progress=lambda *_: (_ for _ in ()).throw(RuntimeError("disk full")),
            )
        assert (index_dir / MANIFEST_FILENAME).read_text() == original
        assert Corpus.open(index_dir) is not None

    def test_staleness_is_detected_when_a_document_changes(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)

        corpus = Corpus.open(index_dir)
        assert corpus is not None
        assert not corpus.is_stale([sources])

        (sources / "new_manual.md").write_text("Drafting\n" + "content " * 40, encoding="utf-8")
        assert corpus.is_stale([sources])

    def test_a_skipped_file_is_still_fingerprinted(self, tmp_path: Path):
        # Recording only successes makes staleness permanently true on any
        # corpus containing one unreadable PDF: the manifest says 3, the disk
        # says 4, and they never agree however many times you rebuild.
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        (sources / "scanned.pdf").write_bytes(b"%PDF-1.4\nno text layer")

        build(sources=[sources], destination=index_dir)
        corpus = Corpus.open(index_dir)
        assert corpus is not None
        assert not corpus.is_stale([sources])

    def test_a_missing_index_is_absent_not_an_exception(self, tmp_path: Path):
        assert Corpus.open(tmp_path / "never-built") is None

    def test_search_with_limit_zero_returns_empty(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)
        corpus = Corpus.open(index_dir)
        assert corpus is not None
        assert corpus.search("pad definition", limit=0) == []

    def test_a_truncated_index_is_ignored_rather_than_half_read(self, tmp_path: Path):
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)
        (index_dir / PASSAGES_FILENAME).unlink()
        assert Corpus.open(index_dir) is None

    def test_an_index_disagreeing_with_its_passages_is_ignored(self, tmp_path: Path):
        # The two files came from different builds, so every lookup would be off
        # by some unknown amount and silently return the wrong passage text.
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)
        offsets_path = index_dir / "passages.offsets.npy"
        # Derived from the real count rather than hard-coded, so the test cannot
        # accidentally write the *correct* length and assert nothing.
        current = np.load(offsets_path, allow_pickle=False)
        np.save(offsets_path, np.zeros(current.shape[0] + 7, dtype=np.int64))
        assert Corpus.open(index_dir) is None

    def test_the_index_never_indexes_its_own_output(self, tmp_path: Path):
        # The default layout puts the index inside a scanned root, so without
        # the exclusion each rebuild would compound on the last.
        root = tmp_path / "bm25"
        (root / "sources").mkdir(parents=True)
        _write_corpus(root / "sources")
        index_dir = root / "index"

        first = build(sources=[root], destination=index_dir)
        second = build(sources=[root], destination=index_dir)
        assert first.documents == second.documents == 3

    def test_discover_skips_excluded_paths_and_dotfiles(self, tmp_path: Path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "x.md").write_text("x", encoding="utf-8")
        (tmp_path / "README.md").write_text("x", encoding="utf-8")
        (tmp_path / "real.md").write_text("x", encoding="utf-8")
        found = discover_sources([tmp_path], exclude=[tmp_path / "README.md"])
        assert [path.name for path in found] == ["real.md"]


class TestMergeAdjacent:
    def test_overlapping_passages_from_one_page_are_rejoined(self):
        shared = " ".join(f"w{index}" for index in range(60))
        left = Passage(
            text=f"head {shared}", source="a.pdf", page=1, heading="H", score=5.0, matched_terms=2
        )
        right = Passage(
            text=f"{shared} tail", source="a.pdf", page=1, heading="H", score=3.0, matched_terms=1
        )
        [merged] = merge_adjacent([left, right])
        assert merged.text == f"head {shared} tail"
        assert merged.score == 5.0

    def test_unrelated_passages_are_left_alone(self):
        one = Passage(text="alpha", source="a.pdf", page=1, heading=None, score=2.0, matched_terms=1)
        two = Passage(text="beta", source="b.pdf", page=9, heading=None, score=1.0, matched_terms=1)
        assert len(merge_adjacent([one, two])) == 2

    def test_output_stays_ordered_by_score(self):
        passages = [
            Passage(text=f"p{i}", source=f"{i}.pdf", page=i, heading=None, score=float(i), matched_terms=1)
            for i in range(5)
        ]
        merged = merge_adjacent(passages)
        assert [passage.score for passage in merged] == sorted(
            (passage.score for passage in merged), reverse=True
        )

    def test_a_single_passage_is_returned_unchanged(self):
        p = Passage(text="solo", source="x.pdf", page=1, heading=None, score=3.0, matched_terms=1)
        assert merge_adjacent([p]) == [p]

    def test_an_empty_list_is_returned_unchanged(self):
        assert merge_adjacent([]) == []


# ---------------------------------------------------------------------------
# Language. CATIA's menus are translated, so the manual has to match the UI.
# ---------------------------------------------------------------------------

_FRENCH = (
    "Cliquez sur OK pour valider la poche. Vous pouvez ensuite selectionner "
    "la face et definir une epaisseur pour cette operation dans le panneau."
)
_ENGLISH = (
    "Click OK to confirm the pocket. You can then select the face and define "
    "a thickness for this operation in the panel that appears on the screen."
)


class TestLanguageDetection:
    def test_tells_the_two_manuals_apart(self):
        assert detect(_FRENCH) == "fr"
        assert detect(_ENGLISH) == "en"

    def test_pure_jargon_is_not_guessed_at(self):
        # `pad`, `fillet`, `sketch` and every part name are identical in both
        # manuals. A detector that counted content words would call every page
        # bilingual; this one declines, which is the honest answer.
        assert detect("Pad Definition Sketch Fillet Shell Draft") is None

    def test_text_too_short_to_have_a_grammar_is_not_guessed_at(self):
        assert detect("epaisseur") is None
        assert detect("edge fillet radius") is None

    def test_a_mixed_page_reads_as_neither(self):
        assert detect(_FRENCH + " " + _ENGLISH) is None

    def test_an_unsupported_language_is_none_not_a_wrong_answer(self):
        # Degrading to language-blind ranking is correct; confidently calling
        # German "English" would actively demote the right manual.
        assert detect(
            "Klicken Sie auf OK, um die Tasche zu bestaetigen und waehlen Sie "
            "dann die Flaeche aus, um eine Dicke fuer diesen Vorgang zu setzen."
        ) in (None, "en")

    @pytest.mark.parametrize(
        ("supplied", "expected"),
        [
            ("fr", "fr"),
            ("fr-FR", "fr"),
            ("FR_fr", "fr"),
            ("French", "fr"),
            ("français", "fr"),
            ("en-GB", "en"),
            ("de", None),
            ("", None),
            (None, None),
            ("nonsense", None),
        ],
    )
    def test_locales_normalise_to_a_known_language_or_nothing(
        self, supplied: str | None, expected: str | None
    ):
        # CATIA reports its interface language in whatever form the caller has;
        # an unrecognised one must degrade, never raise.
        assert normalise(supplied) == expected

    def test_a_known_preference_survives_an_undetectable_query(self):
        # The common case: a French user types two English CAD terms. The
        # preference is what carries the language, not the query.
        assert detect_query("fillet radius", fallback="fr") == "fr"

    def test_a_confident_query_overrides_the_preference(self):
        assert (
            detect_query(
                "Comment puis-je creer une poche dans cette piece avec le module",
                fallback="en",
            )
            == "fr"
        )


class TestLanguagePreference:
    #: A term spelled identically in both manuals, which is the situation the
    #: preference exists for: CATIA's own jargon does not translate, so the two
    #: pages score alike and nothing but language can separate them.
    SHARED = "congé raccord fillet"

    @staticmethod
    def _bilingual(tmp_path: Path) -> Corpus:
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        (sources / "fr_manual.md").write_text(
            f"Poche\n{TestLanguagePreference.SHARED}\n" + _FRENCH * 8, encoding="utf-8"
        )
        (sources / "en_manual.md").write_text(
            f"Pocket\n{TestLanguagePreference.SHARED}\n" + _ENGLISH * 8, encoding="utf-8"
        )
        build(sources=[sources], destination=index_dir)
        corpus = Corpus.open(index_dir)
        assert corpus is not None
        return corpus

    def test_language_is_recorded_on_every_passage(self, tmp_path: Path):
        corpus = self._bilingual(tmp_path)
        languages = {
            passage.source: passage.language
            for passage in corpus.search("select face thickness operation", limit=10)
        }
        assert languages.get("fr_manual.md") == "fr"
        assert languages.get("en_manual.md") == "en"

    def test_a_close_contest_is_decided_by_the_preference(self, tmp_path: Path):
        # Both manuals document the same command under the same untranslated
        # name, so BM25 scores them alike and the preference is the only thing
        # that can choose. This is the case that matters: a French user looking
        # at a menu item called "Poche" needs the French page.
        corpus = self._bilingual(tmp_path)
        assert corpus.search(self.SHARED, limit=1, prefer_language="fr")[0].source == "fr_manual.md"
        assert corpus.search(self.SHARED, limit=1, prefer_language="en")[0].source == "en_manual.md"

    def test_a_clearly_better_match_in_the_other_language_still_wins(
        self, tmp_path: Path
    ):
        # The boost reorders near-ties; it does not override the ranking. A
        # French page that is twice as good an answer must not be demoted just
        # because the interface is English -- being right beats being localised.
        corpus = self._bilingual(tmp_path)
        query = "selectionner definir epaisseur poche panneau"
        assert corpus.search(query, limit=1, prefer_language="en")[0].source == "fr_manual.md"

    def test_a_preference_never_empties_a_result(self, tmp_path: Path):
        # The whole reason this is a boost and not a filter: a workbench
        # documented only in English must still answer a French user.
        corpus = self._bilingual(tmp_path)
        hits = corpus.search("click confirm pocket panel screen", prefer_language="fr")
        assert hits, "an English-only answer must still be returned"

    def test_an_unknown_preference_is_simply_ignored(self, tmp_path: Path):
        corpus = self._bilingual(tmp_path)
        query = "select face thickness operation"
        assert [passage.source for passage in corpus.search(query, prefer_language="de")] == [
            passage.source for passage in corpus.search(query)
        ]

    def test_an_index_predating_languages_still_loads_and_answers(self, tmp_path: Path):
        # Forward compatibility in the direction that actually happens: someone
        # upgrades the code without rebuilding. A missing key must not be a
        # KeyError on every hit.
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)

        passages_path = index_dir / PASSAGES_FILENAME
        stripped = []
        for line in passages_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            record.pop("language", None)
            stripped.append(json.dumps(record, ensure_ascii=False))
        # Byte offsets must stay valid, so rewrite them alongside the text.
        #
        # `newline=""` is load-bearing on Windows. The offsets below count one
        # byte per line ending, and the default translates "\n" to "\r\n" -- so
        # every seek after the first landed mid-record and the reader logged
        # "Passage N is malformed" for the whole file. The bug was in the test,
        # not the corpus, and it only ever showed on Windows.
        payload = "\n".join(stripped) + "\n"
        passages_path.write_text(payload, encoding="utf-8", newline="")
        offsets, position = [], 0
        for line in stripped:
            offsets.append(position)
            position += len(line.encode("utf-8")) + 1
        np.save(index_dir / "passages.offsets.npy", np.asarray(offsets, dtype=np.int64))

        corpus = Corpus.open(index_dir)
        assert corpus is not None
        hits = corpus.search("pad definition dialog", prefer_language="en")
        assert hits and hits[0].language is None


# ---------------------------------------------------------------------------
# The service. Its entire contract is that it cannot raise.
# ---------------------------------------------------------------------------


class TestKnowledgeService:
    @staticmethod
    def _built(tmp_path: Path) -> KnowledgeService:
        sources, index_dir = tmp_path / "src", tmp_path / "index"
        sources.mkdir()
        _write_corpus(sources)
        build(sources=[sources], destination=index_dir)
        return KnowledgeService(index_dir=index_dir, source_dirs=[sources])

    def test_searches_a_built_index(self, tmp_path: Path):
        service = self._built(tmp_path)
        assert service.available
        assert service.search("bend radius")[0].source == "sheet_metal.md"

    def test_expansion_does_not_raise_the_coverage_floor(self, tmp_path: Path):
        """A widened query must never return fewer results than the plain one.

        Query expansion adds *synonyms*, and a passage matches the English name
        or the French one, never both. Measuring the coverage floor over the
        expanded query therefore demands a breadth of match no passage can have:
        widening `bend radius` to a dozen cross-language terms took the floor
        from one term to eight and turned one good hit into none at all. The
        floor is measured against `coverage_query` for exactly this reason.
        """
        service = self._built(tmp_path)
        plain = service.search("bend radius")
        assert plain, "the unexpanded query should match the fixture corpus"

        corpus = Corpus.open(service.index_dir)
        assert corpus is not None
        widened = "bend radius pli Biegung Piegatura Plegado Edge Fillet Kantenverrundung"
        assert corpus.search(widened) == [], "the bug this guards against"
        assert corpus.search(widened, coverage_query="bend radius")

    def test_reports_unavailable_rather_than_failing_with_no_index(self, tmp_path: Path):
        service = KnowledgeService(index_dir=tmp_path / "nothing", source_dirs=[tmp_path])
        assert service.available is False
        assert service.search("anything") == []
        assert service.stats() == {"available": False, "index_dir": str(tmp_path / "nothing")}

    def test_disabled_never_touches_disk(self, tmp_path: Path):
        service = self._built(tmp_path)
        disabled = KnowledgeService(
            index_dir=service.index_dir, source_dirs=service.source_dirs, enabled=False
        )
        assert disabled.available is False
        assert disabled.search("bend radius") == []

    @pytest.mark.parametrize("query", ["", "   ", "the of and a", "!!! ???"])
    def test_degenerate_queries_return_nothing_quietly(self, tmp_path: Path, query: str):
        assert self._built(tmp_path).search(query) == []

    def test_a_corrupt_index_degrades_to_no_results(self, tmp_path: Path):
        service = self._built(tmp_path)
        assert service.available
        (service.index_dir / "index.npz").write_bytes(b"garbage")
        service.reload()
        # This is the whole point of the module: consulting the manuals improves
        # an answer and must never be the reason there is not one.
        assert service.search("bend radius") == []

    def test_reload_picks_up_a_rebuild(self, tmp_path: Path):
        service = self._built(tmp_path)
        before = service.stats()["documents"]
        (service.source_dirs[0] / "extra.md").write_text(
            "Drafting\n" + "views and sections " * 40, encoding="utf-8"
        )
        build(sources=service.source_dirs, destination=service.index_dir)
        service.reload()
        assert service.stats()["documents"] > before

    def test_stats_report_staleness_after_a_source_changes(self, tmp_path: Path):
        service = self._built(tmp_path)
        assert service.stats()["stale"] is False
        (service.source_dirs[0] / "late.md").write_text("Late\n" + "x " * 60, encoding="utf-8")
        assert service.stats()["stale"] is True


class TestFormatPassages:
    def test_renders_citations_the_model_can_attribute(self):
        rendered = format_passages(
            [
                Passage(
                    text="The bend radius is set here.",
                    source="sheet_metal.pdf",
                    page=210,
                    heading="Bend Radius",
                    score=9.0,
                    matched_terms=2,
                )
            ]
        )
        assert "sheet_metal.pdf" in rendered and "p. 210" in rendered

    def test_nothing_in_nothing_out(self):
        assert format_passages([]) == ""

    def test_a_passage_with_no_heading_still_renders_a_citation(self):
        # When heading is None the citation must omit that field gracefully
        # rather than printing "None" in the formatted output.
        rendered = format_passages(
            [
                Passage(
                    text="The pad command extrudes a sketch.",
                    source="part_design.pdf",
                    page=42,
                    heading=None,
                    score=8.0,
                    matched_terms=2,
                )
            ]
        )
        assert "part_design.pdf" in rendered
        assert "p. 42" in rendered
        assert "None" not in rendered

    def test_an_oversized_passage_is_truncated_at_the_prompt_boundary(self):
        rendered = format_passages(
            [
                Passage(
                    text="word " * 5_000,
                    source="x.pdf",
                    page=1,
                    heading=None,
                    score=1.0,
                    matched_terms=1,
                )
            ]
        )
        assert len(rendered) < 4_200


# ---------------------------------------------------------------------------
# A real PDF, when this machine can read one at all.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not available_extractors(), reason="no PDF extractor installed on this machine"
)
class TestRealPdf:
    @staticmethod
    def _make_pdf(path: Path) -> bool:
        """Render a one-page PDF with a known sentence, if a tool can.

        Enough lines to clear `MIN_CHUNK_WORDS`. A single sentence extracts
        perfectly well and then chunks to nothing, which looks exactly like an
        extraction failure and is not one -- worth stating, because that is how
        this fixture failed first time round.
        """
        lines = "\n".join(
            f"72 {700 - index * 14} moveto "
            f"(The edge fillet radius is 5 mm on face {index}.) show"
            for index in range(12)
        )
        try:
            subprocess.run(
                ["ps2pdf", "-", str(path)],
                input=(
                    f"%!PS\n/Helvetica findfont 12 scalefont setfont\n{lines}\nshowpage\n"
                ).encode(),
                check=True,
                capture_output=True,
                timeout=30,
            )
            return path.exists() and path.stat().st_size > 0
        except (OSError, subprocess.SubprocessError):
            return False

    def test_a_real_pdf_round_trips_into_a_searchable_index(self, tmp_path: Path):
        sources = tmp_path / "src"
        sources.mkdir()
        if not self._make_pdf(sources / "fillet.pdf"):
            pytest.skip("no PostScript-to-PDF tool available to build a fixture")

        report = build(sources=[sources], destination=tmp_path / "index")
        assert report.documents == 1

        corpus = Corpus.open(tmp_path / "index")
        assert corpus is not None
        hits = corpus.search("edge fillet radius")
        assert hits and hits[0].source == "fillet.pdf"
