"""Phase 11.1 — a requirement as data, and the graph requirements form.

Offline, like `app/design/`: nothing here builds geometry, opens a socket or
touches a database. The measurement vocabulary is consulted, which reaches
`app.kernel.contract`, and that is deliberate — the refusal it provides is the
whole reason a requirement is validated at construction rather than at
verification.

Every guard below was verified by breaking the thing it guards: the note on each
test says what was broken and what came back.
"""

from __future__ import annotations

import pytest

from app.requirements.errors import RequirementCycleError, RequirementError, VocabularyError
from app.requirements.model import (
    FORMAT_VERSION,
    Requirement,
    RequirementSet,
    Source,
    Status,
)


def mass_requirement(**overrides: object) -> Requirement:
    fields: dict[str, object] = {
        "id": "REQ-001",
        "statement": "The frame shall weigh no more than 850 kg.",
        "measure": "mass_kg",
        "comparison": "<=",
        "target": 850.0,
        "source": Source.CUSTOMER,
    }
    fields.update(overrides)
    return Requirement(**fields)  # type: ignore[arg-type]


class TestARequirementIsAnAssertionWithProvenance:
    def test_it_compiles_to_an_assertion_named_after_itself(self) -> None:
        """Master plan 5.2: the report says REQ-001, not an anonymous inequality."""
        assertion = mass_requirement().assertion()
        assert assertion.name == "REQ-001"
        assert assertion.measure == "mass_kg"
        assert assertion.comparison == "<="
        assert assertion.bound == 850.0

    def test_the_statement_rides_in_the_assertion_note(self) -> None:
        """A failing number with the sentence beside it is arguable; without, it is not."""
        assertion = mass_requirement(rationale="The shop crane lifts one tonne.").assertion()
        assert "weigh no more than 850 kg" in assertion.note
        assert "shop crane" in assertion.note
        assert "customer" in assertion.note

    def test_the_unit_is_read_from_the_vocabulary_not_stored(self) -> None:
        """Nothing converts, so a stored unit could only ever disagree."""
        assert mass_requirement().unit == "kg"
        assert "unit" not in mass_requirement().to_dict()

    def test_a_formula_target_survives_unevaluated(self) -> None:
        """`=expression` is the marker the spec, params and Assertion already use."""
        one = mass_requirement(target="=target_mass_kg")
        assert one.assertion().bound == "=target_mass_kg"


class TestConstructionRefusals:
    def test_a_quantity_outside_the_contract_is_refused_when_written(self) -> None:
        """Broken to verify: `measure="mass"` (the real path is `mass_kg`).

        Without the check it constructs happily and verifies UNMEASURED forever,
        which is indistinguishable from an honest gap. With it, VocabularyError
        names the path and says where the vocabulary is.
        """
        with pytest.raises(VocabularyError) as caught:
            mass_requirement(measure="mass")
        assert "mass" in str(caught.value)
        assert "measurement contract" in str(caught.value)

    def test_a_machine_path_with_an_unknown_leaf_is_refused(self) -> None:
        with pytest.raises(VocabularyError) as caught:
            mass_requirement(measure="machine.fos.lift.safety")
        assert "machine.<check>.<leaf>" in str(caught.value)

    def test_a_real_machine_path_is_accepted(self) -> None:
        one = mass_requirement(
            measure="machine.fos.ram_stroke.factor_of_safety", comparison=">=", target=2.0
        )
        assert one.unit == "ratio"

    def test_equality_with_no_tolerance_is_refused_at_construction(self) -> None:
        """Broken to verify: the `self.assertion()` line in `_check_measurability`.

        Removing it lets the requirement construct and only fail when somebody
        verifies — in front of the customer. With it, the refusal is immediate and
        carries `Assertion`'s own message about a kernel not returning round decimals.
        """
        with pytest.raises(RequirementError) as caught:
            mass_requirement(comparison="==", target=850.0)
        assert "tolerance" in str(caught.value)

    def test_equality_on_a_count_needs_no_tolerance(self) -> None:
        """One solid is one solid — the exception `assertions.counts_things` carves."""
        one = mass_requirement(measure="solid_count", comparison="==", target=1)
        assert one.assertion().comparison == "=="

    def test_a_standard_with_no_citation_is_refused(self) -> None:
        """Broken to verify: dropped the citation branch — the requirement built and
        the report then said "per the standard" with no clause to look up."""
        with pytest.raises(RequirementError) as caught:
            mass_requirement(source=Source.STANDARD, citation="")
        assert "clause" in str(caught.value)

    def test_a_regulation_with_a_citation_is_accepted(self) -> None:
        one = mass_requirement(
            source=Source.REGULATORY, citation="2006/42/EC Annex I 1.3.2"
        )
        assert one.source is Source.REGULATORY

    def test_derived_with_no_parent_is_refused(self) -> None:
        with pytest.raises(RequirementError) as caught:
            mass_requirement(source=Source.DERIVED)
        assert "derived from nothing" in str(caught.value)

    def test_an_id_with_a_space_is_refused(self) -> None:
        """Trace links are found by scanning notes; an id with a space cannot be."""
        with pytest.raises(RequirementError) as caught:
            mass_requirement(id="REQ 001")
        assert "REQ-014" in str(caught.value)

    def test_a_requirement_with_no_statement_is_refused(self) -> None:
        with pytest.raises(RequirementError):
            mass_requirement(statement="   ")

    def test_a_negative_tolerance_is_refused(self) -> None:
        with pytest.raises(RequirementError):
            mass_requirement(tolerance=-0.1)

    def test_a_requirement_cannot_be_its_own_parent(self) -> None:
        with pytest.raises(RequirementCycleError):
            mass_requirement(source=Source.DERIVED, parents=("REQ-001",))

    def test_a_repeated_parent_is_refused(self) -> None:
        with pytest.raises(RequirementError) as caught:
            mass_requirement(source=Source.DERIVED, parents=("REQ-000", "REQ-000"))
        assert "more than once" in str(caught.value)


class TestSomethingUnmeasurableMustSayWhatWouldChangeThat:
    def test_no_measure_and_no_needs_is_refused(self) -> None:
        """11.2 — a requirement nothing checks is a wish. Broken to verify: dropped
        the `needs` branch, and a bare statement imported as a silent requirement
        that would never appear as a gap."""
        with pytest.raises(RequirementError) as caught:
            Requirement(id="REQ-009", statement="The frame shall be robust.")
        assert "wish" in str(caught.value)

    def test_no_measure_with_needs_is_accepted_and_is_not_measurable(self) -> None:
        one = Requirement(
            id="REQ-009",
            statement="The frame shall survive ten years of two-shift operation.",
            needs="no fatigue solver is federated yet (Phase 6)",
        )
        assert not one.measurable
        assert one.unit == ""

    def test_both_a_measure_and_a_needs_is_refused(self) -> None:
        with pytest.raises(RequirementError) as caught:
            mass_requirement(needs="a fatigue solver")
        assert "checkable or it is waiting" in str(caught.value)

    def test_an_unmeasurable_requirement_cannot_be_turned_into_an_assertion(self) -> None:
        one = Requirement(id="REQ-009", statement="Ten years.", needs="a fatigue solver")
        with pytest.raises(RequirementError) as caught:
            one.assertion()
        assert "verify_requirements" in str(caught.value)

    def test_a_comparison_with_nothing_to_measure_is_refused(self) -> None:
        with pytest.raises(RequirementError):
            Requirement(
                id="REQ-009", statement="Ten years.", comparison="<=", needs="a solver"
            )


class TestTheDecompositionGraph:
    def parent_and_child(self) -> RequirementSet:
        top = mass_requirement()
        child = Requirement(
            id="REQ-002",
            statement="The web shall be no thinner than 6 mm.",
            measure="minimum_wall_mm",
            comparison=">=",
            target=6.0,
            source=Source.DERIVED,
            parents=("REQ-001",),
        )
        return RequirementSet.of("press", [top, child])

    def test_children_are_derived_never_stored(self) -> None:
        found = self.parent_and_child()
        assert [one.id for one in found.children_of("REQ-001")] == ["REQ-002"]
        assert found.children_of("REQ-002") == ()

    def test_ancestors_answer_why_is_this_here(self) -> None:
        found = self.parent_and_child()
        assert [one.id for one in found.ancestors_of("REQ-002")] == ["REQ-001"]

    def test_roots_and_leaves(self) -> None:
        found = self.parent_and_child()
        assert [one.id for one in found.roots()] == ["REQ-001"]
        assert [one.id for one in found.leaves()] == ["REQ-002"]

    def test_a_parent_that_is_not_in_the_set_is_refused(self) -> None:
        """Broken to verify: dropped `_check_parents`, and `ancestors_of` then raised
        a bare lookup error at the moment somebody asked why a rib was there."""
        with pytest.raises(RequirementError) as caught:
            RequirementSet.of(
                "press",
                [
                    Requirement(
                        id="REQ-002",
                        statement="Web at least 6 mm.",
                        measure="minimum_wall_mm",
                        comparison=">=",
                        target=6.0,
                        source=Source.DERIVED,
                        parents=("REQ-404",),
                    )
                ],
            )
        assert "REQ-404" in str(caught.value)

    def test_a_two_node_cycle_is_refused_and_the_message_names_the_loop(self) -> None:
        """Broken to verify: removed `_check_acyclic` — `ancestors_of` then spun
        forever on the pair, which is the non-terminating "why is this here" walk
        the guard exists to prevent."""
        first = Requirement(
            id="REQ-001",
            statement="A.",
            measure="mass_kg",
            comparison="<=",
            target=1.0,
            source=Source.DERIVED,
            parents=("REQ-002",),
        )
        second = Requirement(
            id="REQ-002",
            statement="B.",
            measure="mass_kg",
            comparison="<=",
            target=1.0,
            source=Source.DERIVED,
            parents=("REQ-001",),
        )
        with pytest.raises(RequirementCycleError) as caught:
            RequirementSet.of("press", [first, second])
        message = str(caught.value)
        assert "REQ-001" in message and "REQ-002" in message
        assert "->" in message

    def test_a_three_node_cycle_is_refused(self) -> None:
        def derived(name: str, parent: str) -> Requirement:
            return Requirement(
                id=name,
                statement=name,
                measure="mass_kg",
                comparison="<=",
                target=1.0,
                source=Source.DERIVED,
                parents=(parent,),
            )

        with pytest.raises(RequirementCycleError):
            RequirementSet.of(
                "press",
                [
                    derived("REQ-001", "REQ-003"),
                    derived("REQ-002", "REQ-001"),
                    derived("REQ-003", "REQ-002"),
                ],
            )

    def test_a_diamond_is_not_a_cycle(self) -> None:
        """Two parents converging is normal decomposition and must not be refused."""

        def derived(name: str, *parents: str) -> Requirement:
            return Requirement(
                id=name,
                statement=name,
                measure="mass_kg",
                comparison="<=",
                target=1.0,
                source=Source.DERIVED,
                parents=parents,
            )

        found = RequirementSet.of(
            "press",
            [
                mass_requirement(),
                derived("REQ-002", "REQ-001"),
                derived("REQ-003", "REQ-001"),
                derived("REQ-004", "REQ-002", "REQ-003"),
            ],
        )
        assert {one.id for one in found.ancestors_of("REQ-004")} == {
            "REQ-001",
            "REQ-002",
            "REQ-003",
        }

    def test_a_duplicate_id_is_refused(self) -> None:
        with pytest.raises(RequirementError) as caught:
            RequirementSet.of("press", [mass_requirement(), mass_requirement()])
        assert "coin flip" in str(caught.value)


class TestStatusIsNotAVerdict:
    def test_obsolete_is_excluded_from_active_and_kept_in_the_set(self) -> None:
        found = RequirementSet.of(
            "press",
            [mass_requirement(), mass_requirement(id="REQ-002", status=Status.OBSOLETE)],
        )
        assert [one.id for one in found.active] == ["REQ-001"]
        assert [one.id for one in found.obsolete] == ["REQ-002"]
        assert len(found) == 2

    def test_assertions_cover_only_active_measurable_requirements(self) -> None:
        found = RequirementSet.of(
            "press",
            [
                mass_requirement(),
                mass_requirement(id="REQ-002", status=Status.OBSOLETE),
                Requirement(id="REQ-003", statement="Ten years.", needs="a fatigue solver"),
            ],
        )
        assert [one.name for one in found.assertions()] == ["REQ-001"]


class TestPersistence:
    def test_a_set_round_trips_through_dict(self) -> None:
        original = RequirementSet.of(
            "press",
            [
                mass_requirement(rationale="Crane limit.", citation="RFQ §2.1"),
                Requirement(
                    id="REQ-002",
                    statement="Ten years.",
                    needs="a fatigue solver (Phase 6)",
                    source=Source.CUSTOMER,
                ),
            ],
            origin="acme.kreq",
        )
        again = RequirementSet.from_dict(original.to_dict())
        assert again == original
        assert original.to_dict()["format_version"] == FORMAT_VERSION

    def test_an_unknown_key_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(RequirementError) as caught:
            Requirement.from_dict(
                {"id": "REQ-001", "statement": "x", "needs": "y", "priority": "high"}
            )
        assert "priority" in str(caught.value)

    def test_a_newer_format_version_is_refused(self) -> None:
        with pytest.raises(RequirementError) as caught:
            RequirementSet.from_dict(
                {"format_version": FORMAT_VERSION + 1, "name": "press", "requirements": []}
            )
        assert "Upgrade" in str(caught.value)

    def test_an_unknown_source_is_refused_with_the_list(self) -> None:
        with pytest.raises(RequirementError) as caught:
            Requirement.from_dict(
                {"id": "REQ-001", "statement": "x", "needs": "y", "source": "marketing"}
            )
        assert "customer" in str(caught.value)
