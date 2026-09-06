"""Design rules — Phase 13.1, and the three things that make one worth having.

`app/rules/engine.py` is a thin layer over machinery that is already tested, and
the tests below are deliberately about the seams rather than about the
arithmetic. `check_assertions` compares numbers and `tests/test_design_assertions.py`
proves it does; nothing here re-checks that `2.6 >= 2.5`.

What *is* checked here is the three claims the module makes about itself, each
of which is a way of not being lied to:

1. **A rule may only name a quantity the kernel contract documents, and it is
   refused at construction.** The alternative is a rule that reports
   `UNMEASURED` for ever, and `TestAnUncheckableRuleIsRefusedBeforeItCanBeRun`
   shows what that looks like by building the assertion directly, without the
   guard, and running it against a full payload.
2. **The verdicts are `app.design.assertions.Outcome`, not a fourth vocabulary.**
   Pinned by identity, and by running `check_rules` and `check_assertions` over
   the same inputs and requiring the outcomes to agree element for element.
3. **A pass on a sampled bound is not a proof.** This is the one worth the most.
   `minimum_wall_mm` and `undercut_face_count` come out of
   `app.kernel.interrogation` as bounds from a finite ray set; a rule that read
   one as a measured value would clear a part on a wall nobody measured. The
   asymmetry — the same number proving a violation and not proving a pass — is
   the whole content of `TestASampledBoundIsNotAMeasuredValue`.

Offline, and no seat: everything here is a dict. It does import
`app.kernel.contract`, which is what the rules read their vocabulary from, so it
pays OCP's import once.
"""

from __future__ import annotations

import ast
import math
import pathlib
from enum import Enum
from typing import Any

import pytest

from app.design.assertions import Assertion, AssertionReport, Outcome, check_assertions
from app.kernel import contract, provenance
from app.rules import engine as engine_module
from app.rules import vocabulary as vocabulary_module
from app.rules.engine import Rule, check_rules
from app.rules.errors import RuleError, SourceError, VocabularyError
from app.rules.vocabulary import SAMPLED_BOUNDS, BoundDirection, bound_direction

#: A wall thickness scan's own words, so the fixtures below are shaped like the
#: payload `ThicknessReport.to_payload` actually produces rather than like a
#: convenient invention.
RAY_CAST = "ray cast inward from 8 points per face (96 hits, 4 misses)"


def _with_basis(path: str, value: Any, record: provenance.Record) -> dict[str, Any]:
    """A payload holding one number and saying how it got there."""
    payload: dict[str, Any] = {path: value}
    provenance.attach(payload, path, record)
    return payload


def _sampled(path: str, value: Any, method: str = RAY_CAST) -> dict[str, Any]:
    return _with_basis(path, value, provenance.approximated(method))


def _measured(path: str, value: Any, method: str = "exact integration") -> dict[str, Any]:
    return _with_basis(path, value, provenance.measured(method))


def _wall_rule(comparison: str, limit: float, name: str = "minimum wall") -> Rule:
    return Rule(
        name=name,
        measure="minimum_wall_mm",
        comparison=comparison,
        limit=limit,
        source="process sheet PS-14, injection tooling",
    )


def _top_level_imports(module: Any) -> set[str]:
    """Every module named by an import at the top level of a module's source.

    Import statements inside a function are skipped, which is the point: both
    modules under test import `app.kernel` lazily on purpose, and the difference
    between the two placements is 166 MB of OCP.
    """
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    named: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            named.add(node.module)
    return named


class TestAnUncheckableRuleIsRefusedBeforeItCanBeRun:
    """Vocabulary is checked at construction, for the reason the contract is."""

    def test_a_quantity_nothing_measures_is_refused_at_construction(self) -> None:
        with pytest.raises(VocabularyError) as caught:
            Rule(
                name="edge distance",
                measure="hole_to_edge_mm",
                comparison=">=",
                limit=2.0,
                source="supplier guideline",
            )

        message = str(caught.value)
        assert "hole_to_edge_mm" in message
        # The refusal has to be actionable in both directions: what can be said
        # instead, and where a genuinely new quantity has to be declared.
        assert "app/kernel/contract.py" in message
        # …and a sample of what *is* measurable, so the answer is in the refusal.
        assert "bounding_box_mm.size" in message
        assert "app.kernel.contract.catalogue()" in message

    def test_that_refusal_is_a_rule_error_not_a_spec_error(self) -> None:
        """The recovery differs. A correction loop that caught this as a spec
        problem would go and change the part in response to a typo in a rule book."""
        from app.design.errors import SpecError

        with pytest.raises(RuleError):
            Rule(name="x", measure="nonsense_mm", comparison=">=", limit=1.0, source="s")
        with pytest.raises(VocabularyError) as caught:
            Rule(name="x", measure="nonsense_mm", comparison=">=", limit=1.0, source="s")
        assert not isinstance(caught.value, SpecError)

    def test_without_that_refusal_the_rule_would_be_unmeasured_for_ever(self) -> None:
        """Break the guard: build the same claim as a bare `Assertion`, which has
        no vocabulary check, and run it against a payload measuring everything
        the contract documents. It comes back `UNMEASURED` — not once, but on
        every payload that will ever exist, because nothing produces the path."""
        unguarded = Assertion(
            name="edge distance",
            measure="hole_to_edge_mm",
            comparison=">=",
            bound=2.0,
        )
        generous = {item.path: 1.0 for item in contract.QUANTITIES}

        report = check_assertions([unguarded], generous)

        assert report.results[0].outcome is Outcome.UNMEASURED
        # And the shape of the damage: nothing *failed*, so a report skimmed for
        # failures reads clean.
        assert report.failed == ()

    def test_a_superseded_spelling_is_told_what_replaced_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No path is superseded today, so the branch is exercised against a
        contract entry injected for the test. The behaviour is the point: an old
        rule gets an explanation, not a silence."""
        monkeypatch.setitem(
            contract._BY_PATH,
            "wall_thickness_mm",
            contract.Entry(
                path="wall_thickness_mm",
                unit="mm",
                summary="old spelling",
                typical_basis=provenance.Basis.APPROXIMATED,
                superseded_by="minimum_wall_mm",
            ),
        )

        with pytest.raises(VocabularyError) as caught:
            Rule(
                name="wall",
                measure="wall_thickness_mm",
                comparison=">=",
                limit=2.0,
                source="s",
            )

        assert "superseded by 'minimum_wall_mm'" in str(caught.value)

    def test_an_indexed_component_of_a_documented_vector_is_accepted(self) -> None:
        """The contract documents the vector; a rule addresses one component."""
        rule = Rule(
            name="height",
            measure="bounding_box_mm.size[2]",
            comparison="<=",
            limit=120.0,
            source="press shut height",
        )

        assert rule.unit == "mm"

    def test_the_unit_comes_from_the_contract_not_from_the_rule(self) -> None:
        assert _wall_rule(">=", 2.5).unit == "mm"
        counted = Rule(
            name="one piece",
            measure="solid_count",
            comparison="==",
            limit=1,
            source="the part is one body",
        )
        assert counted.unit == "count"

    def test_neither_rules_module_imports_the_kernel_at_module_level(self) -> None:
        """The laziness both modules document. Hoisting either import turns a
        rule set that is only being written out into a 166 MB OCP load."""
        for module in (engine_module, vocabulary_module):
            kernel_imports = {
                name for name in _top_level_imports(module) if name.startswith("app.kernel")
            }
            assert kernel_imports == set(), f"{module.__name__} imports {kernel_imports}"


class TestTheVerdictsAreTheAssertionModules:
    """Not a fourth vocabulary — the same three, from the same enum."""

    def test_an_outcome_is_literally_the_assertion_modules_enum(self) -> None:
        report = check_rules([_wall_rule(">=", 2.5)], _measured("minimum_wall_mm", 3.0))

        assert type(report.results[0].outcome) is Outcome

    def test_there_are_three_outcomes_and_the_engine_declares_none_of_them(self) -> None:
        assert set(Outcome) == {Outcome.PASSED, Outcome.FAILED, Outcome.UNMEASURED}
        declared_here = [
            value
            for name, value in vars(engine_module).items()
            if isinstance(value, type)
            and issubclass(value, Enum)
            and value.__module__ == engine_module.__name__
        ]
        assert declared_here == []

    def test_check_rules_and_check_assertions_agree_outcome_for_outcome(self) -> None:
        """The comparison is not re-implemented, so it cannot drift. Three rules
        covering all three outcomes, checked both ways."""
        rules = [
            _wall_rule(">=", 2.5, name="wall ok"),
            _wall_rule(">=", 9.0, name="wall broken"),
            Rule(
                name="mass",
                measure="mass_kg",
                comparison="<=",
                limit=4.2,
                source="weight budget",
            ),
        ]
        payload = _sampled("minimum_wall_mm", 2.6)

        report = check_rules(rules, payload)
        direct = check_assertions([rule.as_assertion() for rule in rules], payload)

        assert [r.outcome for r in report.results] == [r.outcome for r in direct.results]
        assert [r.outcome for r in report.results] == [
            Outcome.PASSED,
            Outcome.FAILED,
            Outcome.UNMEASURED,
        ]

    def test_the_underlying_assertion_report_is_kept_not_copied(self) -> None:
        """A caller that already speaks `AssertionReport` needs no adapter."""
        report = check_rules([_wall_rule(">=", 2.5)], _measured("minimum_wall_mm", 3.0))

        assert isinstance(report.assertions, AssertionReport)
        assert len(report.assertions) == len(report)

    def test_results_stay_paired_with_the_rules_that_produced_them(self) -> None:
        rules = [
            _wall_rule(">=", 2.5, name="first"),
            Rule(name="second", measure="mass_kg", comparison="<=", limit=1.0, source="s"),
            _wall_rule("<=", 1.0, name="third"),
        ]

        report = check_rules(rules, _measured("minimum_wall_mm", 3.0))

        assert [r.name for r in report.results] == ["first", "second", "third"]
        assert [r.rule.measure for r in report.results] == [
            "minimum_wall_mm",
            "mass_kg",
            "minimum_wall_mm",
        ]


class TestARuleIsAnAssertionWithASourceOnTop:
    """`Rule` validates by building its assertion, so it grows no second copy of
    the comparison rules — and inherits their wording."""

    def test_a_limit_with_no_source_is_refused(self) -> None:
        with pytest.raises(SourceError) as caught:
            Rule(name="wall", measure="minimum_wall_mm", comparison=">=", limit=2.5, source="")

        assert "source=" in str(caught.value)

    def test_a_rule_with_no_name_is_refused(self) -> None:
        with pytest.raises(RuleError):
            Rule(name="  ", measure="mass_kg", comparison="<=", limit=1.0, source="s")

    def test_an_unknown_comparison_is_refused_in_the_assertion_modules_words(self) -> None:
        with pytest.raises(RuleError) as caught:
            Rule(name="wall", measure="minimum_wall_mm", comparison="~=", limit=2.5, source="s")

        assert "is not a comparison this understands" in str(caught.value)

    def test_the_delegated_message_names_the_rule_once(self) -> None:
        """It printed 'wall: wall: …' before 2026-09-06 — the assertion is built
        with the rule's own name, so the prefix was being added twice."""
        with pytest.raises(RuleError) as caught:
            Rule(name="wall", measure="minimum_wall_mm", comparison="~=", limit=2.5, source="s")

        assert str(caught.value).count("wall:") == 1

    def test_an_exact_equality_on_a_measured_number_is_refused(self) -> None:
        with pytest.raises(RuleError) as caught:
            Rule(name="mass", measure="mass_kg", comparison="==", limit=4.2, source="s")

        assert "tolerance" in str(caught.value)

    def test_an_exact_equality_on_a_count_is_allowed(self) -> None:
        """The exception `counts_things` exists for: one solid is one solid."""
        rule = Rule(
            name="one piece", measure="solid_count", comparison="==", limit=1, source="s"
        )

        report = check_rules([rule], _measured("solid_count", 1))

        assert report.results[0].outcome is Outcome.PASSED

    @pytest.mark.parametrize("limit", [math.nan, math.inf, -math.inf])
    def test_a_non_finite_limit_is_refused(self, limit: float) -> None:
        """A rule that passes or fails everything, silently."""
        with pytest.raises(RuleError):
            Rule(name="wall", measure="minimum_wall_mm", comparison=">=", limit=limit, source="s")

    def test_a_boolean_limit_is_refused(self) -> None:
        """`True` is an `int` in Python and would compare as 1 without complaint."""
        with pytest.raises(RuleError):
            Rule(
                name="valid",
                measure="is_valid",
                comparison=">=",
                limit=True,  # type: ignore[arg-type]
                source="s",
            )

    def test_the_source_travels_into_the_assertions_note(self) -> None:
        """So a failure message is arguable rather than a bare number."""
        rule = Rule(
            name="wall",
            measure="minimum_wall_mm",
            comparison=">=",
            limit=2.5,
            source="process sheet PS-14",
            rationale="thinner than this short-fills",
        )

        note = rule.as_assertion().note

        assert "process sheet PS-14" in note
        assert "short-fills" in note


class TestASampledBoundIsNotAMeasuredValue:
    """The rule this module exists for, and the failure it prevents: a part
    cleared on a minimum wall thickness nobody measured.

    Every case below uses the same reported number where it can, so the variable
    is the *direction of the claim* and nothing else.
    """

    def test_a_pass_on_an_upper_bound_is_reached_but_not_proved(self) -> None:
        """minimum_wall_mm >= 2.5 measuring 2.6. The true minimum is at most 2.6
        and could be anything below it."""
        report = check_rules([_wall_rule(">=", 2.5)], _sampled("minimum_wall_mm", 2.6))
        result = report.results[0]

        assert result.outcome is Outcome.PASSED
        assert result.ok is True
        assert result.proven is False
        assert result.provisional is True

    def test_the_report_is_ok_and_not_proven_and_says_which(self) -> None:
        report = check_rules([_wall_rule(">=", 2.5)], _sampled("minimum_wall_mm", 2.6))

        assert report.ok is True
        assert report.proven is False
        assert report.provisional == report.results
        assert "provisional" in report.summary()
        assert "not every verdict is proved" in report.summary()

    def test_without_this_layer_the_same_check_reads_as_a_clean_pass(self) -> None:
        """Break the guard. `check_assertions` alone — the layer underneath, and
        what a rule engine that did not read the sidecar direction would report —
        says passed, ok, and nothing about the wall being a bound."""
        payload = _sampled("minimum_wall_mm", 2.6)

        underneath = check_assertions([_wall_rule(">=", 2.5).as_assertion()], payload)

        assert underneath.ok is True
        assert underneath.results[0].passed is True
        # It does carry the approximate flag — the honesty is present one layer
        # down — but nothing there turns it into "this pass proves nothing",
        # because nothing there knows which way the bound errs.
        assert underneath.approximate is True
        assert not hasattr(underneath, "proven")

    def test_the_same_bound_proves_a_violation(self) -> None:
        """minimum_wall_mm >= 2.5 measuring 2.0. The reported minimum is an upper
        bound, so the true minimum is at most 2.0 and certainly below 2.5."""
        report = check_rules([_wall_rule(">=", 2.5)], _sampled("minimum_wall_mm", 2.0))
        result = report.results[0]

        assert result.outcome is Outcome.FAILED
        assert result.proven is True
        assert result.provisional is False

    def test_a_claim_running_the_same_way_as_the_bound_is_proved(self) -> None:
        """minimum_wall_mm <= 6.0 measuring 2.6 — same number, same sampling, and
        now the pass is a proof, because reported >= truth."""
        report = check_rules([_wall_rule("<=", 6.0)], _sampled("minimum_wall_mm", 2.6))

        assert report.results[0].outcome is Outcome.PASSED
        assert report.results[0].proven is True
        assert report.proven is True

    def test_a_lower_bound_is_the_mirror_image(self) -> None:
        """A visibility scan can miss an undercut face; it cannot invent one. So
        `<= 0` measuring 0 proves nothing, and `>= 1` measuring 2 proves it."""
        none_found = _sampled("undercut_face_count", 0, method="two rays per face")
        two_found = _sampled("undercut_face_count", 2, method="two rays per face")
        clean = Rule(
            name="demouldable",
            measure="undercut_face_count",
            comparison="<=",
            limit=0,
            source="two-part tool, no side actions",
        )
        present = Rule(
            name="needs a side action",
            measure="undercut_face_count",
            comparison=">=",
            limit=1,
            source="tooling review",
        )

        assert check_rules([clean], none_found).results[0].proven is False
        assert check_rules([clean], none_found).results[0].outcome is Outcome.PASSED
        assert check_rules([present], two_found).results[0].proven is True

    @pytest.mark.parametrize("comparison", ["==", "!="])
    def test_an_equality_against_a_sampled_number_is_never_proved(
        self, comparison: str
    ) -> None:
        """Neither claim runs one way, so neither survives a bound in either
        direction — whichever way the sampling errs, the equality could break."""
        rule = Rule(
            name="undercuts",
            measure="undercut_face_count",
            comparison=comparison,
            limit=0,
            source="tooling review",
        )
        payload = _sampled("undercut_face_count", 0, method="two rays per face")

        assert check_rules([rule], payload).results[0].proven is False

    def test_a_backend_that_measures_exactly_is_not_penalised(self) -> None:
        """The direction table is only consulted when the payload's own sidecar
        says the number was approximated. A backend that measures wall thickness
        exactly gets a proved pass on the same rule and the same number."""
        report = check_rules([_wall_rule(">=", 2.5)], _measured("minimum_wall_mm", 2.6))
        result = report.results[0]

        assert result.sampled is False
        assert result.proven is True
        # The direction is still the table's answer — it is what *would* apply had
        # this payload sampled the number — and it is simply not consulted.
        assert result.direction is BoundDirection.UPPER_BOUND

    def test_a_payload_that_says_nothing_about_a_sampled_quantity_is_not_a_proof(
        self,
    ) -> None:
        """Found writing these tests, and not hypothetical: `thinnest_point_mm`
        is written into a real OCCT payload with no sidecar entry of its own, so
        `AssertionResult.approximate` is False for it and the verdict was coming
        back proved. `app.kernel.provenance` is explicit that no entry is *not*
        `MEASURED`, so the contract's typical basis is read as the last word."""
        silent = {"thinnest_point_mm": [12.0, 4.0, 1.5], "minimum_wall_mm": 2.6}
        wall = _wall_rule(">=", 2.5)
        where = Rule(
            name="thin spot is inboard",
            measure="thinnest_point_mm[0]",
            comparison="<=",
            limit=50.0,
            source="tooling review",
        )

        report = check_rules([wall, where], silent)

        assert [r.outcome for r in report.results] == [Outcome.PASSED, Outcome.PASSED]
        assert all(r.sampled for r in report.results)
        assert all(not r.proven for r in report.results)
        assert report.ok is True
        assert report.proven is False

    def test_a_normally_measured_quantity_with_a_silent_payload_stays_proved(self) -> None:
        """The converse, so the fallback above cannot quietly make everything
        provisional: mass is not a sampled quantity, and silence about it is not
        evidence that it was."""
        report = check_rules(
            [Rule(name="mass", measure="mass_kg", comparison="<=", limit=4.2, source="s")],
            {"mass_kg": 3.9},
        )

        assert report.results[0].sampled is False
        assert report.results[0].proven is True

    def test_a_number_the_payload_calls_approximate_with_no_direction_is_unknown(
        self,
    ) -> None:
        """The CATIA mock's case: mass computed from a bounding box less the
        swept volume of each cut. The contract calls mass exactly measured, so no
        direction is declared for it — and knowing a number is approximate
        without knowing which way is strictly less information than knowing it is
        exact, so it must not read as exact."""
        mock = _sampled("mass_kg", 3.9, method="bounding box less swept cuts")
        rule = Rule(name="mass", measure="mass_kg", comparison="<=", limit=4.2, source="s")

        result = check_rules([rule], mock).results[0]

        assert result.sampled is True
        assert result.direction is BoundDirection.UNKNOWN
        assert result.proven is False
        assert "nothing declares which way it errs" in str(result)

    def test_the_payload_wide_approximate_flag_is_still_honoured(self) -> None:
        """Payloads written before the sidecar existed carry one coarse flag."""
        rule = Rule(name="mass", measure="mass_kg", comparison="<=", limit=4.2, source="s")

        result = check_rules([rule], {"mass_kg": 3.9, "approximate": True}).results[0]

        assert result.sampled is True
        assert result.proven is False

    def test_a_provisional_verdict_says_so_in_words_and_in_the_payload(self) -> None:
        report = check_rules([_wall_rule(">=", 2.5)], _sampled("minimum_wall_mm", 2.6))
        result = report.results[0]

        assert "provisional" in str(result)
        assert "does not make this a proof" in str(result)
        as_dict = result.to_dict()
        assert as_dict["proven"] is False
        assert as_dict["provisional"] is True
        assert as_dict["sampled"] is True
        assert as_dict["bound_direction"] == str(BoundDirection.UPPER_BOUND)
        assert "provisional_because" in as_dict

    def test_a_proved_verdict_carries_no_provisional_explanation(self) -> None:
        as_dict = check_rules(
            [_wall_rule("<=", 6.0)], _sampled("minimum_wall_mm", 2.6)
        ).results[0].to_dict()

        assert as_dict["proven"] is True
        assert "provisional_because" not in as_dict


class TestTheDirectionTableCannotFallOutOfStepWithTheKernel:
    """A new sampled quantity must not be able to acquire a sound-looking pass
    merely by being added to the kernel — the promise `vocabulary.py` makes."""

    def test_every_normally_approximated_quantity_declares_a_direction(self) -> None:
        approximated = {
            item.path
            for item in contract.QUANTITIES
            if item.typical_basis is provenance.Basis.APPROXIMATED and not item.superseded_by
        }

        undeclared = approximated - set(SAMPLED_BOUNDS)

        assert undeclared == set(), (
            f"{sorted(undeclared)} are sampled by the kernel and have no bound direction. "
            "Declare one in app.rules.vocabulary.SAMPLED_BOUNDS, with the reason it errs "
            "that way, or every pass measured off them will read as proved."
        )

    def test_every_declared_direction_names_a_quantity_the_contract_documents(self) -> None:
        """Keyed on the contract's own spelling, so a rename shows up here as a
        missing key rather than as a silently wrong direction."""
        orphaned = {path for path in SAMPLED_BOUNDS if contract.entry(path) is None}

        assert orphaned == set()

    def test_an_undeclared_sampled_quantity_falls_back_to_unknown(self) -> None:
        """Break the guard the other way: what happens if the test above is
        ignored. The answer is the conservative one — unknown, never exact."""
        assert bound_direction("minimum_wall_mm") is BoundDirection.UPPER_BOUND
        with pytest.MonkeyPatch.context() as patch:
            patch.delitem(SAMPLED_BOUNDS, "minimum_wall_mm")  # type: ignore[arg-type]
            assert bound_direction("minimum_wall_mm") is BoundDirection.UNKNOWN

    def test_a_quantity_the_contract_calls_measured_is_exact(self) -> None:
        assert bound_direction("mass_kg") is BoundDirection.EXACT
        assert bound_direction("bounding_box_mm.size[2]") is BoundDirection.EXACT

    def test_the_direction_survives_an_index_suffix(self) -> None:
        assert bound_direction("thinnest_point_mm[1]") is BoundDirection.UNKNOWN


class TestTheReportIsHonestAboutWhatItCovered:
    def test_an_unmeasured_rule_is_not_a_pass(self) -> None:
        report = check_rules(
            [Rule(name="mass", measure="mass_kg", comparison="<=", limit=4.2, source="s")],
            {"volume_mm3": 1000.0},
        )

        assert report.results[0].outcome is Outcome.UNMEASURED
        assert report.ok is False
        assert bool(report) is False

    def test_an_unmeasured_rule_is_neither_proven_nor_provisional(self) -> None:
        """`provisional` means reached and not proved. Nothing was reached."""
        result = check_rules(
            [Rule(name="mass", measure="mass_kg", comparison="<=", limit=4.2, source="s")],
            {},
        ).results[0]

        assert result.proven is False
        assert result.provisional is False

    def test_an_unmeasured_rule_says_why_it_could_not_be_checked(self) -> None:
        result = check_rules([_wall_rule(">=", 2.5)], {"mass_kg": 1.0}).results[0]

        assert "not checked" in str(result)
        assert "minimum_wall_mm" in str(result)

    def test_an_empty_rule_set_is_not_a_pass(self) -> None:
        """A suite with nothing in it has verified nothing."""
        report = check_rules([], _measured("minimum_wall_mm", 3.0))

        assert report.ok is False
        assert report.proven is False
        assert report.summary() == "No design rules to check."

    def test_a_broken_rule_reports_the_gap(self) -> None:
        """The number a correction loop needs — 'failed' is not actionable."""
        result = check_rules([_wall_rule(">=", 2.5)], _measured("minimum_wall_mm", 2.0)).results[0]

        assert result.gap == pytest.approx(-0.5)

    def test_the_partitions_cover_every_result_exactly_once(self) -> None:
        rules = [
            _wall_rule(">=", 2.5, name="ok"),
            _wall_rule(">=", 9.0, name="broken"),
            Rule(name="absent", measure="mass_kg", comparison="<=", limit=1.0, source="s"),
        ]

        report = check_rules(rules, _measured("minimum_wall_mm", 3.0))

        assert len(report.passed) + len(report.failed) + len(report.unmeasured) == len(report)
        assert report.to_dict()["satisfied"] == 1
        assert report.to_dict()["broken"] == 1
        assert report.to_dict()["unmeasured"] == 1
        assert report.to_dict()["ok"] is False

    def test_a_fully_proved_report_says_nothing_about_provisional(self) -> None:
        report = check_rules([_wall_rule(">=", 2.5)], _measured("minimum_wall_mm", 3.0))

        assert report.proven is True
        assert "provisional" not in report.summary()
