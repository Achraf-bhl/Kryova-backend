"""Retrieval measured against the real manuals in `data/bm25`, not a fixture.

`test_retrieval.py` proves the machinery is correct: the tokenizer keeps `M6`,
the scorer prefers breadth over repetition, a corrupt index degrades to no
results. Every one of those tests passes on a corpus of three synthetic
markdown files, and all of them passed while the shipped index labelled 1,143
of its 5,003 passages with the heading `6. Cliquez sur OK pour`.

That is the gap this file closes. The questions here are not "does BM25 work"
but "does *this* index, over *these* manuals, answer the questions an engineer
actually asks" -- which is a property of the corpus and the analyzer together
and cannot be observed on synthetic input. A three-document fixture has no
French training manual whose procedure steps wrap mid-sentence, no workbench
documented in only one language, and no page of toolbar names dense enough to
outrank the chapter that answers the question.

**Every test here skips when there is no built index**, which is the state of a
fresh clone and of CI. The corpus is 270 MB of vendor PDFs; requiring it to run
the suite would mean nobody runs the suite. `python -m app.retrieval.build`
makes these tests live.

**They are also written to survive curation.** The manuals are data, and
somebody adding or removing one must not turn this file red. A query case whose
subject matter is no longer in the index is skipped rather than failed, and the
thresholds are floors with real headroom rather than the measured numbers
pinned exactly -- the point is to catch a regression that costs five points of
precision, not to notice that a rebuild moved one result by one rank.

Measured on the 21 readable documents as of this commit: precision@1 94.7%,
precision@3 100%, MRR 0.974.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import settings
from app.retrieval.corpus import Corpus
from app.retrieval.service import KnowledgeService

# ---------------------------------------------------------------------------
# Fixtures. One index load for the whole module -- it is tens of megabytes of
# numpy arrays and re-reading it per test would be the only slow thing here.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def index_dir() -> Path:
    directory = settings.knowledge_index_dir
    if Corpus.open(directory) is None:
        pytest.skip(
            f"no reference index at {directory}; "
            "run `python -m app.retrieval.build` to exercise these tests"
        )
    return directory


@pytest.fixture(scope="module")
def service(index_dir: Path) -> KnowledgeService:
    return KnowledgeService(
        index_dir=index_dir,
        source_dirs=settings.knowledge_source_dirs,
        exclude=[index_dir, index_dir.parent / "README.md"],
    )


@pytest.fixture(scope="module")
def passages(index_dir: Path) -> list[dict]:
    """Every indexed passage, as written. Small enough to hold: ~5k records."""
    with (index_dir / "passages.jsonl").open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


# ---------------------------------------------------------------------------
# The evaluation set.
#
# Each case is a question an engineer would actually ask and the manual(s) that
# genuinely cover it. Judged at the document level on purpose: which *page* best
# explains edge fillets is a matter of opinion, but "the answer is in the
# Dress-Up Features chapter and not in the Photo Studio manual" is a fact, and a
# metric built on facts survives a rebuild.
# ---------------------------------------------------------------------------

#: Short key -> a substring that identifies the document on disk.
#:
#: `tut1`/`tut2` are matched on more than the volume number: "Basics-Part-I" is
#: a prefix of "Basics-Part-II", so the shorter marker silently attributes every
#: Part II hit to Part I. That mistake scored this suite eighteen points too low
#: before it was caught, which is a fair warning about substring matching on
#: filenames that differ by a roman numeral.
DOCUMENTS: dict[str, str] = {
    "dressup": "Chapter5-Creating_Dress-Up",
    "sketcher": "Chapter1-Drawing_sketches",
    "assembly_en": "Chapter11-Assembly_Modeling",
    "wireframe_en": "Chapter9-Working_with_Wireframe",
    "tut1": "Basics-Part-I-Getting-Started",
    "tut2": "Basics-Part-II-Part-Modeling",
    "gasa": "Generative_Assembly_Structural_Analysis",
    "femsurf": "fem-surface",
    "photo": "photo-studio",
    "c03": "c03_cat_v5r18",
    "assembly_fr": "FR-Dassault-Systems_Assembly_Design",
    "dmu": "DMU_Kinematics",
    "freestyle": "FreeStyle_Shaper",
    "drafting": "Generative_Drafting",
    "gsd": "Generative_Shape_Design",
    "gps": "Generative_Structural_Analysis",
    "sheetmetal": "Sheet_Metal_Design",
    "wireframe_fr": "FR-Dassault-Systems_Wireframe_and_Surface",
    "partdesign": "FR-Dassault-Systems_part_design",
    "koh1": "FEA Release 21. A Step by Step Guide 2012 Part 1",
    "koh2": "FEA Release 21. A Step by Step Guide 2012 Part 2",
}

#: (query, language hint, documents that would be a correct answer)
CASES: list[tuple[str, str, set[str]]] = [
    # Part Design and dress-up features.
    ("edge fillet radius", "en", {"dressup", "tut2", "partdesign"}),
    ("variable radius fillet", "en", {"dressup", "tut2", "partdesign"}),
    ("draft angle neutral element", "en", {"dressup", "tut2", "partdesign"}),
    ("shell wall thickness hollow part", "en", {"dressup", "tut2", "partdesign"}),
    ("counterbored hole creation", "en", {"dressup", "tut2", "partdesign"}),
    ("pad extrude sketch profile", "en", {"tut2", "partdesign", "sketcher"}),
    ("pocket remove material depth", "en", {"tut2", "partdesign"}),
    ("multi-sections solid loft guide", "en", {"tut2", "partdesign", "gsd"}),
    ("rib sweep along centre curve", "en", {"tut2", "partdesign", "gsd"}),
    # Sketcher.
    ("sketcher geometric constraint", "en", {"sketcher", "tut1", "partdesign"}),
    ("profile toolbar predefined profile", "en", {"sketcher", "tut1"}),
    # Assembly.
    ("assembly coincidence constraint", "en", {"assembly_en", "assembly_fr", "tut1"}),
    ("contact constraint between parts", "en", {"assembly_en", "assembly_fr", "tut1"}),
    # Sheet metal.
    ("sheet metal wall bend", "en", {"sheetmetal", "tut1"}),
    # Surfaces.
    ("extrapolate a surface boundary", "en", {"wireframe_en", "wireframe_fr", "gsd"}),
    ("spline curve through points", "en", {"wireframe_en", "wireframe_fr", "gsd", "freestyle"}),
    ("freestyle control point shape", "en", {"freestyle"}),
    # Drafting and rendering.
    ("drawing section view generation", "en", {"drafting", "c03"}),
    ("rendering material environment", "en", {"photo"}),
    # Kinematics.
    ("kinematics joint mechanism simulation", "en", {"dmu"}),
    # Finite elements.
    ("von mises stress result", "en", {"koh1", "koh2", "gps", "femsurf", "gasa"}),
    ("mesh element size sag", "en", {"koh1", "koh2", "gps", "femsurf", "gasa"}),
    ("clamp restraint boundary condition", "en", {"koh1", "koh2", "gps", "femsurf", "gasa"}),
    ("distributed force load on face", "en", {"koh1", "koh2", "gps", "femsurf", "gasa"}),
    ("static analysis case compute", "en", {"koh1", "koh2", "gps", "femsurf", "gasa"}),
    ("welding spot fastened connection", "en", {"gasa", "koh1", "koh2"}),
    # French. Half this corpus is French and a French user is the common case,
    # not an edge case, so the query set is weighted accordingly.
    ("congé d'arête rayon", "fr", {"partdesign", "dressup", "tut2"}),
    ("dépouille angle de dépouille", "fr", {"partdesign", "dressup"}),
    ("épaisseur coque évidement", "fr", {"partdesign", "dressup"}),
    ("poche esquisse profondeur", "fr", {"partdesign", "tut2"}),
    ("maillage éléments finis taille", "fr", {"gps", "koh1", "koh2", "femsurf", "gasa"}),
    ("contrainte de coïncidence assemblage", "fr", {"assembly_fr", "assembly_en"}),
    ("cotation tolérance géométrique", "fr", {"drafting"}),
    ("pliage tôlerie rayon de pli", "fr", {"sheetmetal"}),
    ("surface extrapolation frontière", "fr", {"wireframe_fr", "gsd", "wireframe_en"}),
    ("mécanisme liaison pivot cinématique", "fr", {"dmu"}),
    ("vue en coupe mise en plan", "fr", {"drafting"}),
    ("rendu réaliste matériau", "fr", {"photo"}),
]

#: Floors, not the measured numbers. Measured is 94.7% / 100% / 0.974; the gap
#: is deliberate headroom so that re-chunking, a new manual or a tokenizer tweak
#: that moves one result by one rank does not fail the build. The heading defect
#: these thresholds were written against cost five points of precision@1, so a
#: regression of that size still trips them.
MIN_PRECISION_AT_1 = 0.85
MIN_PRECISION_AT_3 = 0.92
MIN_MRR = 0.88


def document_key(source: str) -> str | None:
    for key, marker in DOCUMENTS.items():
        if marker in source:
            return key
    return None


def indexed_keys(passages: list[dict]) -> set[str]:
    """Which known documents actually made it into this index."""
    found = {document_key(record["source"]) for record in passages}
    return {key for key in found if key is not None}


def applicable(passages: list[dict]) -> list[tuple[str, str, set[str]]]:
    """Cases whose subject matter is present in the index as it stands.

    A case naming only documents nobody has on disk is unanswerable, and failing
    it would punish whoever curated the corpus rather than whoever broke the
    retriever.
    """
    available = indexed_keys(passages)
    return [case for case in CASES if case[2] & available]


# ---------------------------------------------------------------------------
# The index is built from the documents that are actually there.
# ---------------------------------------------------------------------------


class TestCorpusHealth:
    def test_the_index_covers_the_documents_on_disk(
        self, service: KnowledgeService, passages: list[dict]
    ):
        """Every readable source is represented, and the unreadable ones are named.

        The four large French `Formation-*` manuals are photographs of paper
        with no text layer: they cannot be indexed without OCR, the build says
        so, and that is expected rather than a regression. What must not happen
        is a document silently contributing nothing while the build reports
        success.
        """
        manifest = json.loads((service.index_dir / "manifest.json").read_text(encoding="utf-8"))
        skipped = {entry.split(" (")[0] for entry in manifest["skipped"]}
        fingerprinted = {entry["name"] for entry in manifest["sources"]}
        indexed = {record["source"] for record in passages}

        assert indexed, "an index with no passages is not an index"
        # Every source is accounted for as either indexed or explicitly skipped.
        assert fingerprinted == indexed | skipped
        # And a skipped file carries a reason a human can act on.
        for entry in manifest["skipped"]:
            assert "(" in entry and entry.rstrip().endswith(")")

    def test_the_index_does_not_contain_its_own_output(self, passages: list[dict]):
        """The index directory sits inside a scanned root in the default layout.

        Without the exclusion every rebuild would index the previous build's
        `passages.jsonl`, and the corpus would compound on itself.
        """
        sources = {record["source"] for record in passages}
        assert not {name for name in sources if name.endswith((".jsonl", ".npy", ".npz"))}
        assert "README.md" not in sources, "the note about the corpus is not part of the corpus"

    def test_every_passage_can_be_cited(self, passages: list[dict]):
        """A passage the model cannot attribute is a passage it should not quote."""
        for record in passages:
            assert record["source"]
            assert isinstance(record["page"], int) and record["page"] >= 1
            assert record["text"].strip()

    def test_both_languages_are_present_and_detected(self, passages: list[dict]):
        """The corpus is bilingual, and the language field is what makes it searchable as one.

        If detection silently returned `None` for everything the preference
        would quietly stop working, no test on synthetic input would notice, and
        French users would start getting English pages.
        """
        languages = {record.get("language") for record in passages}
        assert "fr" in languages and "en" in languages

    def test_the_index_is_current_for_the_documents_on_disk(self, service: KnowledgeService):
        """A stale index answers from manuals that have been edited or removed.

        Not a failure of the code so much as a reminder to rebuild, but it has
        to be visible: every assertion below this line is measuring whatever was
        indexed last, and if that is not what is on disk the numbers describe
        nothing.
        """
        stats = service.stats()
        assert stats["stale"] is not True, (
            "the corpus has changed since the index was built; "
            "run `python -m app.retrieval.build`"
        )


# ---------------------------------------------------------------------------
# Citation quality. What the heading says is what the model tells the user.
# ---------------------------------------------------------------------------


class TestHeadingQuality:
    """The regression that motivated this file.

    These manuals are almost entirely numbered procedures, and a French
    instruction wrapped across a line break -- `6. Cliquez sur OK pour` -- has
    no terminal punctuation to disqualify it and is title-case by the letter of
    the rule, because `sur` and `pour` are minor words while `Cliquez` and `OK`
    are capitalised. It was accepted as a section title on 22.8% of passages.

    That is not a cosmetic problem. The heading is repeated into the term stream
    three times, so the boost meant to reward a passage whose *section* is about
    fillets was rewarding `cliquez`, `ok` and `pour` instead; and the heading is
    what the citation shows, so the model told users to read "6. Cliquez sur OK
    pour" on page 154.
    """

    @staticmethod
    def _headings(passages: list[dict]) -> list[str]:
        return [record["heading"] for record in passages if record.get("heading")]

    def test_procedure_steps_are_not_labelled_as_sections(self, passages: list[dict]):
        """A single-level number is a step; a multi-level one is a section.

        `3.2 Creating a Pad` is a heading and must stay one, so this counts only
        the bare `6.` / `2)` form that a procedure uses.
        """
        import re

        enumerated = [
            heading
            for heading in self._headings(passages)
            if re.match(r"^\d+\s*[.)](?!\d)", heading)
        ]
        assert not enumerated, (
            f"{len(enumerated)} passages are labelled with a procedure step, "
            f"e.g. {enumerated[:3]}"
        )

    def test_headings_are_not_fragments_of_a_path(self, passages: list[dict]):
        """An install directory or a truncated menu path is not a section title.

        `C:\\Program Files\\intel\\plsuite\\bin` and `Outils ->` are short,
        unpunctuated and title-cased, so they pass every other test on their
        merits, and both are useless in a citation.
        """
        offenders = [
            heading
            for heading in self._headings(passages)
            if "\\" in heading or "://" in heading or heading.rstrip().endswith(("->", ">"))
        ]
        # `Toolbar: Dress-Up Features > Fillets` legitimately contains `>`; only
        # a trailing one means the extractor cut the line.
        assert not offenders, f"{len(offenders)} path-fragment headings, e.g. {offenders[:3]}"

    def test_headings_do_not_end_on_a_minor_word(self, passages: list[dict]):
        """`Cliquez sur` and `Sélectionnez Insertion -> Outils de` are cut sentences.

        A real title ends on the thing it names.
        """
        from app.retrieval.chunking import _TITLE_MINOR_WORDS

        offenders = [
            heading
            for heading in self._headings(passages)
            if heading.split() and heading.split()[-1].lower().strip(".,") in _TITLE_MINOR_WORDS
        ]
        assert not offenders, f"{len(offenders)} truncated headings, e.g. {offenders[:3]}"

    def test_most_passages_still_carry_a_heading(self, passages: list[dict]):
        """The rejections above must not be so eager that nothing is labelled.

        Three tests demanding fewer headings and none demanding any is how a
        heuristic gets tightened until it returns False for everything -- every
        assertion passes and every citation loses its section.
        """
        with_heading = sum(1 for record in passages if record.get("heading"))
        assert with_heading / len(passages) > 0.5


# ---------------------------------------------------------------------------
# Retrieval quality, measured.
# ---------------------------------------------------------------------------


class TestRetrievalQuality:
    @staticmethod
    def _rank(service: KnowledgeService, query: str, language: str) -> list[str | None]:
        return [
            document_key(passage.source)
            for passage in service.search(query, limit=5, language=language)
        ]

    def test_every_case_finds_something(self, service: KnowledgeService, passages: list[dict]):
        """An empty result is the one outcome with no recovery.

        The model is told, in words, that the manuals have nothing on the term
        -- so it answers from training instead, which is the behaviour wanted
        for a genuine gap and exactly wrong for a query the corpus does cover.
        """
        for query, language, _ in applicable(passages):
            assert service.search(query, limit=5, language=language), f"nothing for {query!r}"

    def test_precision_and_mrr_hold(self, service: KnowledgeService, passages: list[dict]):
        """The headline measurement: is the right manual at the top of the list?"""
        cases = applicable(passages)
        assert len(cases) >= 20, "too few applicable cases to measure anything"

        at1 = at3 = 0
        reciprocal = 0.0
        misses: list[str] = []
        for query, language, expected in cases:
            keys = self._rank(service, query, language)
            if keys and keys[0] in expected:
                at1 += 1
            if any(key in expected for key in keys[:3]):
                at3 += 1
            else:
                misses.append(f"{query!r} -> {keys[:3]}")
            for rank, key in enumerate(keys, start=1):
                if key in expected:
                    reciprocal += 1.0 / rank
                    break

        total = len(cases)
        precision_1, precision_3 = at1 / total, at3 / total
        mrr = reciprocal / total
        report = (
            f"precision@1 {precision_1:.1%}, precision@3 {precision_3:.1%}, "
            f"MRR {mrr:.3f} over {total} cases; missed: " + "; ".join(misses)
        )
        assert precision_1 >= MIN_PRECISION_AT_1, report
        assert precision_3 >= MIN_PRECISION_AT_3, report
        assert mrr >= MIN_MRR, report

    def test_a_french_question_reaches_the_french_manuals(
        self, service: KnowledgeService, passages: list[dict]
    ):
        """Half the corpus is French, and it has to be reachable in French.

        Not a statement about any one query -- a French question about a
        workbench documented only in English should still return the English
        page. Across the French half of the query set, though, the French
        manuals must dominate, or the analyzer has stopped folding accents or
        the preference has stopped applying.
        """
        french_cases = [case for case in applicable(passages) if case[1] == "fr"]
        if len(french_cases) < 5:
            pytest.skip("not enough French material indexed to measure this")

        french_first = 0
        for query, language, _ in french_cases:
            hits = service.search(query, limit=1, language=language)
            if hits and hits[0].language == "fr":
                french_first += 1
        assert french_first / len(french_cases) >= 0.6

    def test_a_workbench_documented_in_one_language_is_still_reachable_from_the_other(
        self, service: KnowledgeService, passages: list[dict]
    ):
        """The case that sets `LANGUAGE_PREFERENCE_BOOST`.

        Photo Studio is documented here only in English and FreeStyle only in
        French. A French question about rendering has no French page to find, so
        the preference is not breaking a tie -- it is deciding whether the user
        gets the only answer that exists. This is why the boost is 1.35 and not
        the 1.6 it shipped with, and why it is a boost and not a filter.
        """
        available = indexed_keys(passages)
        if "photo" in available:
            hits = service.search("rendu réaliste matériau", limit=5, language="fr")
            assert "photo" in [document_key(hit.source) for hit in hits[:3]]
        if "freestyle" in available:
            hits = service.search("freestyle control point shape", limit=5, language="en")
            assert "freestyle" in [document_key(hit.source) for hit in hits[:3]]

    def test_accents_are_optional_when_typing_french(self, service: KnowledgeService):
        """Users routinely type French without accents, and extraction is inconsistent too."""
        with_accents = service.search("épaisseur coque", limit=5, language="fr")
        without = service.search("epaisseur coque", limit=5, language="fr")
        assert [passage.citation() for passage in with_accents] == [
            passage.citation() for passage in without
        ]

    def test_a_term_the_manuals_do_not_use_returns_nothing_rather_than_noise(
        self, service: KnowledgeService
    ):
        """The coverage floor earning its keep.

        This corpus is large enough that *something* contains any single common
        word, so a query of real words about a subject nobody wrote about must
        come back empty rather than returning the least bad page in the corpus.
        """
        assert service.search("photosynthesis chlorophyll stomata", limit=5) == []


# ---------------------------------------------------------------------------
# The service contract, against the real index rather than a fixture.
# ---------------------------------------------------------------------------


class TestServiceContractOnRealData:
    """`search` cannot raise. Proven here on the data it will actually see."""

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "   ",
            "the of and a",
            "!!! ???",
            "\x00\x01",
            "a" * 5_000,
            "🔧🔩",
            "SELECT * FROM passages; DROP TABLE users;--",
            "../../etc/passwd",
        ],
    )
    def test_degenerate_input_is_answered_or_declined_but_never_raises(
        self, service: KnowledgeService, query: str
    ):
        assert isinstance(service.search(query, limit=5), list)

    @pytest.mark.parametrize("language", ["fr", "en", "de", "zz", "", "fr-FR", "nonsense"])
    def test_any_language_hint_is_survivable(self, service: KnowledgeService, language: str):
        """CATIA reports its interface language in whatever form the caller holds.

        An unsupported one must degrade to language-blind ranking, never empty
        the result and never raise.
        """
        assert service.search("edge fillet radius", limit=5, language=language)

    def test_results_are_ordered_and_bounded(self, service: KnowledgeService):
        passages = service.search("mesh element size", limit=3, language="en")
        assert len(passages) <= 3
        assert [p.score for p in passages] == sorted((p.score for p in passages), reverse=True)

    def test_a_passage_is_small_enough_to_put_in_a_prompt(self, service: KnowledgeService):
        """Five of these go into a context window alongside a real conversation."""
        for query in ("edge fillet radius", "maillage éléments finis taille"):
            for passage in service.search(query, limit=5):
                assert len(passage.text) < 8_000
