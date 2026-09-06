"""Phase 11 — what a requirement is allowed to constrain.

The two tests that earn this file are the ones that keep the vocabulary in step
with its two sources of truth rather than with somebody's memory:
`TestKeptInStepWithMachineChecks` instantiates every check in the library and
asserts each path it can emit resolves here, and
`TestKeptInStepWithTheMeasurementContract` asserts every current contract path
does. Either can go red from an edit in a package this one does not own, which is
the point — a vocabulary that drifts silently turns a real requirement into a
permanent UNMEASURED line that reads like an honest gap.
"""

from __future__ import annotations

import pytest

from app.design import machine_checks
from app.kernel import contract
from app.requirements import vocabulary
from app.requirements.errors import VocabularyError


class TestKeptInStepWithMachineChecks:
    def library(self) -> list[machine_checks.MachineCheck]:
        """One of every check in the 5.1 library, with plausible arguments."""
        return [
            machine_checks.MassBudget(4.2),
            machine_checks.Envelope(400.0, 300.0, 200.0),
            machine_checks.MinimumWall(2.0),
            machine_checks.ClearanceThroughMotion(against=object(), floor_mm=5.0),
            machine_checks.FirstNaturalFrequency(above_hz=120.0),
            machine_checks.FactorOfSafety("lift", at_least=1.5),
            machine_checks.BucklingFactor("lift", at_least=3.0),
            machine_checks.StackUp(
                [("bore", 40.0, 0.05), ("bearing", -32.0, 0.02)], limit_mm=0.10
            ),
            machine_checks.CostBudget(1200.0),
        ]

    def test_every_path_a_check_asserts_on_resolves(self) -> None:
        """Broken to verify: deleted `factor_of_safety` from `MACHINE_LEAVES`, and
        this went red naming the path — instead of a factor-of-safety requirement
        being refused as unmeasurable in front of whoever wrote it."""
        for check in self.library():
            for assertion in check.assertions():
                term = vocabulary.resolve(assertion.measure)
                assert term is not None, f"{assertion.measure} is not in the vocabulary"
                assert term.origin == vocabulary.MACHINE_ORIGIN
                assert term.unit

    def test_the_namespace_is_taken_from_the_module_that_owns_it(self) -> None:
        assert vocabulary.MACHINE_NAMESPACE == machine_checks.NAMESPACE

    def test_a_bare_namespace_is_not_a_path(self) -> None:
        assert vocabulary.resolve("machine") is None
        assert vocabulary.resolve("machine.mass") is None

    def test_an_empty_segment_is_refused(self) -> None:
        assert vocabulary.resolve("machine..kg") is None


class TestKeptInStepWithTheMeasurementContract:
    def test_every_current_contract_path_resolves(self) -> None:
        for entry in contract.QUANTITIES:
            if entry.superseded_by:
                continue
            term = vocabulary.resolve(entry.path)
            assert term is not None, f"{entry.path} is documented but not resolvable"
            assert term.unit == entry.unit
            assert term.origin == vocabulary.CONTRACT_ORIGIN

    def test_an_indexed_component_resolves_to_the_vector_it_belongs_to(self) -> None:
        term = vocabulary.resolve("bounding_box_mm.size[2]")
        assert term is not None
        assert term.unit == "mm"

    def test_the_contract_version_is_reported_for_stamping_results(self) -> None:
        assert vocabulary.contract_version() == contract.CONTRACT_VERSION

    def test_the_catalogue_covers_both_origins(self) -> None:
        listed = vocabulary.catalogue()
        assert any(line.startswith("mass_kg") for line in listed)
        assert any(line.startswith("machine.<check>.") for line in listed)


class TestRefusalsAreActionable:
    def test_an_unknown_path_names_where_the_vocabulary_is(self) -> None:
        with pytest.raises(VocabularyError) as caught:
            vocabulary.require("weight", requirement_id="REQ-001")
        message = str(caught.value)
        assert message.startswith("REQ-001: ")
        assert "catalogue()" in message
        assert "needs" in message

    def test_a_machine_path_with_a_bad_leaf_lists_the_leaves(self) -> None:
        with pytest.raises(VocabularyError) as caught:
            vocabulary.require("machine.fos.lift.margin")
        assert "factor_of_safety" in str(caught.value)

    def test_a_superseded_path_is_told_what_replaced_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The contract has no superseded entry today, so one is injected.

        Worth testing rather than waiting for the first real rename: the branch
        exists precisely so an old requirement gets an explanation instead of the
        generic "nothing measures that", and a branch nothing exercises is a
        branch that is wrong when it first runs.
        """
        replaced = contract.Entry(
            path="old_mass_kg",
            unit="kg",
            summary="Superseded spelling.",
            typical_basis=contract.provenance.Basis.MEASURED,
            superseded_by="mass_kg",
        )
        monkeypatch.setitem(contract._BY_PATH, "old_mass_kg", replaced)
        with pytest.raises(VocabularyError) as caught:
            vocabulary.require("old_mass_kg", requirement_id="REQ-001")
        assert "mass_kg" in str(caught.value)
        assert "replaced by" in str(caught.value)
