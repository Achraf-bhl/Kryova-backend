"""The ASME document behind every standards word `app.verify` uses — E20 task 1.

The failure this guards is the same shape `test_verify_nafems.py` guards
against a remembered figure: a citation that looks precise and was never
checked. `app.verify.standards` exists because three modules cited "the ASME
V&V 20 split" for a claim V&V 20-2009 — scoped to fluids and heat transfer —
does not actually make; the document for the terminology itself is VVUQ 1-2022,
and this codebase's own benchmark suite is entirely solid mechanics, V&V
10-2019's territory. These tests pin the correction and the two documents that
must never be cited as this codebase's own.
"""

from __future__ import annotations

import re

from app.verify.standards import (
    NO_STANDARD,
    NOT_OURS,
    SOLID_MECHANICS_SOURCE,
    STANDARDS,
    TERMINOLOGY_SOURCE,
)


class TestEveryEntryIsACitationAndNotAParaphrase:
    def test_every_standards_entry_names_a_real_document_number(self) -> None:
        """A citation with no document number is a paraphrase wearing a
        citation's clothes — the same rule `nafems.SOURCES` is tested against."""
        pattern = re.compile(r"(V&V|VVUQ)\s*\d")
        for key, citation in STANDARDS.items():
            assert pattern.search(citation), f"{key!r} does not name a document"

    def test_every_entry_is_non_empty(self) -> None:
        for registry in (STANDARDS, NOT_OURS, NO_STANDARD):
            for key, citation in registry.items():
                assert citation.strip(), f"{key!r} has no citation text"


class TestTheTerminologySourceIsTheRightDocument:
    def test_it_points_at_vvuq_1_not_at_vv_20(self) -> None:
        """This is the correction task 1 exists to make: the general
        verification/validation split is VVUQ 1's, not V&V 20's."""
        assert TERMINOLOGY_SOURCE == "vvuq-1"
        assert TERMINOLOGY_SOURCE in STANDARDS

    def test_the_solid_mechanics_source_is_vv_10_not_vv_20(self) -> None:
        """Every case `app.verify.nafems` currently encodes — LE1, LE3, LE10,
        LE11, FV52 — is solid mechanics. V&V 20 is the fluids/heat document."""
        assert SOLID_MECHANICS_SOURCE == "vv-10"
        assert SOLID_MECHANICS_SOURCE in STANDARDS

    def test_vv_20_is_still_registered_for_its_own_method(self) -> None:
        """Removing V&V 20 entirely would overcorrect: `app.verify.convergence`
        genuinely implements its Grid Convergence Index procedure, and that
        citation is real and must stay findable here."""
        assert "vv-20" in STANDARDS
        assert "convergence" in STANDARDS["vv-20"].lower()


class TestTheTwoDocumentsThatMustNeverBeCitedAsOurs:
    def test_vv_40_is_recorded_as_medical_device_scoped(self) -> None:
        assert "vv-40" in NOT_OURS
        assert "medical" in NOT_OURS["vv-40"].lower()
        assert "vv-40" not in STANDARDS

    def test_vvuq_70_is_recorded_as_unpublished(self) -> None:
        """Bounds what E10 task 4 (the surrogate flywheel) or any future
        ML-based result may claim: no ASME-normative basis exists yet."""
        assert "vvuq-70" in NO_STANDARD
        assert "no published standard" in NO_STANDARD["vvuq-70"].lower()
        assert "vvuq-70" not in STANDARDS

    def test_neither_forbidden_document_is_quietly_also_a_real_source(self) -> None:
        """The two vocabularies must not overlap — a document appearing in
        both `STANDARDS` and `NOT_OURS`/`NO_STANDARD` would say two things
        about itself at once."""
        assert STANDARDS.keys().isdisjoint(NOT_OURS)
        assert STANDARDS.keys().isdisjoint(NO_STANDARD)


class TestLiveModulesCiteTheCorrectedSource:
    """The correction has to reach the modules that were wrong, or the
    register is a second opinion nobody reads. Greps the actual source text
    rather than trusting the module list, because a docstring is exactly
    where the original mis-citation lived."""

    def test_no_live_verify_module_still_calls_it_the_v_and_v_20_split(self) -> None:
        """The exact wrong phrase this task corrects, not every co-occurrence
        of the two words — `standards.py` and `__init__.py` both legitimately
        *discuss* V&V 20 and 'split' in the same sentence while explaining
        why it is the wrong citation, which a broader pattern would flag."""
        import pathlib

        verify_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "verify"
        offenders = []
        for path in verify_dir.glob("*.py"):
            if path.name == "standards.py":
                # Quotes the wrong phrase once, deliberately, as the history
                # of the mistake it corrects — not a live mis-citation.
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"(the )?ASME V&V 20 split", text):
                offenders.append(path.name)
        assert not offenders, f"still attributing the terminology split to V&V 20: {offenders}"

    def test_benchmarks_and_register_and_init_cite_vvuq_1(self) -> None:
        import pathlib

        verify_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "verify"
        for name in ("benchmarks.py", "register.py", "__init__.py"):
            text = (verify_dir / name).read_text(encoding="utf-8")
            assert "VVUQ 1" in text, f"{name} does not cite VVUQ 1"


# ---------------------------------------------------------------------------
# E20 task 3 — the words as ASME uses them, and the product saying so
# ---------------------------------------------------------------------------


class TestTheDefinitionsAreQuotedFromADocument:
    def test_every_definition_cites_a_recorded_source(self) -> None:
        from app.verify.standards import DEFINITIONS, SOURCES

        for key, definition in DEFINITIONS.items():
            assert definition.source in SOURCES.values(), key
            assert definition.attributed_to.startswith("ASME"), key

    def test_every_source_carries_a_url_and_the_date_it_was_read(self) -> None:
        from app.verify.standards import SOURCES

        for key, source in SOURCES.items():
            assert "https://" in source, key
            assert re.search(r"Read \d{4}-\d{2}-\d{2}", source), key

    def test_a_definition_with_no_source_is_refused(self) -> None:
        import pytest

        from app.verify.standards import Definition

        with pytest.raises(ValueError, match="paraphrase from memory"):
            Definition(term="validation", text="whatever we think", attributed_to="ASME", source="")

    def test_the_second_hand_basis_is_stated_rather_than_implied(self) -> None:
        """The standards themselves are sold and were not read; the citation
        says so, so nobody mistakes a presentation quoting V&V 10 for V&V 10."""
        from app.verify.standards import SOURCES

        assert "second-hand" in SOURCES["sandia-sand2016-5342c"]

    def test_validation_is_defined_against_the_real_world(self) -> None:
        from app.verify.standards import DEFINITIONS

        assert "real world" in DEFINITIONS["validation"].text
        assert "mathematical model" in DEFINITIONS["verification"].text


class TestNothingKryovaHoldsIsValidation:
    def test_the_validation_evidence_is_empty(self) -> None:
        from app.verify.standards import KRYOVA_EVIDENCE, EvidenceKind

        assert KRYOVA_EVIDENCE[EvidenceKind.VALIDATION] == ()
        assert KRYOVA_EVIDENCE[EvidenceKind.CODE_VERIFICATION]
        assert KRYOVA_EVIDENCE[EvidenceKind.SOLUTION_VERIFICATION]

    def test_benchmarks_are_filed_as_code_verification_not_validation(self) -> None:
        """The correction itself: a NAFEMS agreement is verification."""
        from app.verify.standards import KRYOVA_EVIDENCE, EvidenceKind

        code = " ".join(KRYOVA_EVIDENCE[EvidenceKind.CODE_VERIFICATION])
        assert "NAFEMS" in code
        assert "closed-form" in code

    def test_the_statement_says_what_is_missing_and_what_exists(self) -> None:
        from app.verify.standards import NOT_VALIDATED

        assert NOT_VALIDATED.startswith("Not validated.")
        assert "measurement" in NOT_VALIDATED
        assert "verification" in NOT_VALIDATED

    def test_the_published_block_is_derived_from_the_evidence(self) -> None:
        from app.verify.standards import NOT_VALIDATED, validation_block

        block = validation_block()
        assert block["validated"] is False
        assert block["statement"] == NOT_VALIDATED

    def test_recording_validation_evidence_without_rewriting_the_statement_fails_import(
        self,
    ) -> None:
        """The guard, watched to fail: a copy of the module with one piece of
        validation evidence recorded refuses to import, so the product cannot go
        on saying "no Kryova result has been compared against a measurement"
        the day one has."""
        import importlib.util
        import sys
        from pathlib import Path

        import pytest

        import app.verify.standards as real

        source = Path(real.__file__).read_text(encoding="utf-8")
        marker = "    EvidenceKind.VALIDATION: (),"
        assert source.count(marker) == 1
        mutated = source.replace(
            marker, '    EvidenceKind.VALIDATION: ("strain gauges on bracket S/N 1",),'
        )
        name = "standards_mutant"
        spec = importlib.util.spec_from_loader(name, loader=None)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        # A dataclass resolves its annotations through `sys.modules`, so the
        # copy has to be findable there while it runs, and gone afterwards.
        sys.modules[name] = module
        try:
            with pytest.raises(ValueError, match="Rewrite the statement"):
                exec(compile(mutated, f"{name}.py", "exec"), module.__dict__)
        finally:
            del sys.modules[name]

        # And the unmutated source imports, so it is the one line that raised.
        sys.modules[name] = module
        try:
            exec(compile(source, f"{name}.py", "exec"), module.__dict__)
        finally:
            del sys.modules[name]


class TestTheStatementReachesEverySurfaceThatShowsANumber:
    """Where a user reads a number, the server's one sentence travels with it.
    The trust pages are pinned in `tests/test_trust.py`, the register payload in
    `tests/test_verify_register.py` and the agent's reply in
    `tests/test_agent_verification.py`; these are the response models."""

    def test_a_simulation_read_carries_it(self) -> None:
        from datetime import UTC, datetime

        from app.schemas.simulation import SimulationRead
        from app.verify.standards import NOT_VALIDATED

        read = SimulationRead.model_validate(
            {
                "id": "sim-1",
                "project_id": "proj-1",
                "geometry_version_id": "geo-1",
                "status": "succeeded",
                "solver": "linear-static",
                "load_case": None,
                "thermal_case": None,
                "element_size_mm": 4.0,
                "element_order": 2,
                "grids": 3,
                "analysis": "linear-static",
                "thickness_mm": None,
                "mesh_stats": None,
                "result": {"mesh_convergence": {"converged": True}},
                "fields_media_id": None,
                "error": None,
                "created_at": datetime.now(UTC),
                "started_at": None,
                "finished_at": None,
            }
        )

        assert read.model_dump()["validation"] == NOT_VALIDATED

    def test_a_shared_package_carries_it(self) -> None:
        from datetime import UTC, datetime

        from app.schemas.sharing import SharedPackage
        from app.verify.standards import NOT_VALIDATED

        package = SharedPackage(
            project_name="p",
            project_description=None,
            organisation_name="o",
            shared_by="a@b.c",
            label=None,
            note=None,
            expires_at=datetime.now(UTC),
            allow_geometry_download=False,
            geometry=[],
            simulations=[],
        )

        assert package.model_dump()["validation"] == NOT_VALIDATED

    def test_the_interpretation_carries_it_and_the_model_is_never_asked_for_it(
        self,
    ) -> None:
        """Serialised for the reader; absent from the validation-mode schema the
        providers constrain decoding with, so the model cannot write or soften
        it."""
        from app.ai.schemas import ResultInterpretation
        from app.verify.standards import NOT_VALIDATED

        grammar = ResultInterpretation.model_json_schema()
        assert "validation" not in grammar["properties"]

        interpretation = ResultInterpretation.model_validate(
            {
                "verdict": "safe",
                "headline": "Peak stress is 41% of yield.",
                "findings": [{"title": "t", "detail": "d", "severity": "info"}],
                "suggestions": [],
                "confidence": "high",
                "caveat": "Linear static.",
            }
        )
        assert interpretation.model_dump()["validation"] == NOT_VALIDATED

    def test_a_client_cannot_send_a_softer_statement_in(self) -> None:
        from app.ai.schemas import ResultInterpretation
        from app.verify.standards import NOT_VALIDATED

        interpretation = ResultInterpretation.model_validate(
            {
                "verdict": "safe",
                "headline": "h",
                "findings": [{"title": "t", "detail": "d", "severity": "info"}],
                "suggestions": [],
                "confidence": "high",
                "caveat": "c",
                "validation": "Validated against test data.",
            }
        )
        assert interpretation.model_dump()["validation"] == NOT_VALIDATED
