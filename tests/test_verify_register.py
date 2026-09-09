"""The register's job is the denominator — master plan 7.4.

`tests/test_verify_benchmarks.py` pins the rule that stops one *case* lying.
This file pins the rule that stops a *summary* lying, which is a different
failure and the one that reaches a customer: every individual row can be true
while the page as a whole is false, because the page left out the analyses
nobody has looked at.

So the tests below are mostly about absence. An analysis with no benchmark must
appear. A case that ran and had nothing to compare against must not be counted.
A target the benchmark layer would refuse must not be reprinted here through the
side door. And each of those is checked twice — once that the honest thing
happens, once that the dishonest thing *would* have happened without the guard —
because a test asserting `validated == 0` on a register that is empty for
unrelated reasons proves nothing at all.

Offline: no database, no solver, no kernel. Every outcome here is constructed
directly, which is legitimate because `run_benchmark` is already tested and what
is under test is the roll-up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.verify.benchmarks import Benchmark, BenchmarkOutcome, Outcome, Suite, Target, TargetBasis
from app.verify.changelog import CHANGES, AccuracyChange, Effect, affecting, since
from app.verify.register import (
    ANALYSES,
    NO_BENCHMARK,
    WITHHELD,
    Analysis,
    AnalysisRow,
    Register,
    Standing,
    _blocked_outcomes,
    assert_publishable,
    published_register,
    scrub,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The analyses the shipped register reports as validated right now. Named
#: rather than counted, so a new benchmark has to be declared here instead of
#: nudging a number nobody reads.
VALIDATED_TODAY = {"modal", "linear-static"}


def _target(*, value: float = 100.0, tolerance: float = 0.02) -> Target:
    return Target(
        basis=TargetBasis.PUBLISHED,
        unit="MPa",
        value=value,
        tolerance=tolerance,
        tolerance_reason="the discretisation error of this mesh at this refinement",
        source="NAFEMS LE1, elliptic membrane",
    )


def _unknown_target(reason: str = "the published value has not been looked up") -> Target:
    return Target(basis=TargetBasis.UNKNOWN, unit="MPa", reason=reason)


def _outcome(
    outcome: Outcome,
    *,
    analysis: str = "linear-static",
    target: Target | None = None,
    benchmark_id: str = "case-1",
    measured: float | None = None,
    deviation: float | None = None,
) -> BenchmarkOutcome:
    return BenchmarkOutcome(
        benchmark_id=benchmark_id,
        title="a case",
        analysis=analysis,
        outcome=outcome,
        target=target if target is not None else _target(),
        measured_value=measured,
        relative_deviation=deviation,
    )


# ---------------------------------------------------------------------------
# 1. The denominator
# ---------------------------------------------------------------------------


class TestAnUnvalidatedAnalysisAppears:
    """The register's central claim: what is *not* validated is as visible as
    what is. Everything else in this file is a defence of that one."""

    def test_every_declared_analysis_gets_a_row_whether_or_not_a_case_touches_it(
        self,
    ) -> None:
        """Updated 2026-09-08, when 7.1's catalogue landed.

        Until then every row read `NO_BENCHMARK`, because there were none. The
        NAFEMS cases are now published here, so three rows carry a case instead —
        and the claim being defended is unchanged and is at last actually being
        exercised: an analysis nobody has benchmarked is as visible as one
        somebody has.
        """
        register = published_register()
        by_id = {row.analysis.id: row for row in register.rows}

        assert len(register.rows) == len(ANALYSES)
        assert set(by_id) == {a.id for a in ANALYSES}

        touched = {"linear-static", "thermal-stress", "modal"}
        assert all(by_id[name].because != NO_BENCHMARK for name in touched)
        assert all(
            row.because == NO_BENCHMARK and row.standing is Standing.UNVALIDATED
            for row in register.rows
            if row.analysis.id not in touched
        )

    def test_the_shipped_register_says_which_two_analyses_are_validated(self) -> None:
        """The honest state of this codebase today, asserted rather than
        described, so the day it changes somebody has to come here and say so.

        It changed twice on 2026-09-08 and somebody did, both times. Modal is
        validated against NAFEMS FV52 and linear static against LE10; the other
        nine are not, and `complete` stays false — which is the number this
        register exists to publish. Asserted as the *set of names* rather than as
        a count, so a third case still has to be declared here rather than
        quietly moving a number.
        """
        register = published_register()
        validated = {
            row.analysis.id for row in register.rows if row.standing is Standing.VALIDATED
        }

        assert validated == VALIDATED_TODAY
        assert register.complete is False
        assert len(register.unvalidated) == len(ANALYSES) - len(VALIDATED_TODAY)

    def test_a_case_this_product_cannot_run_is_published_with_the_reason(self) -> None:
        """A benchmark that executes must not be reachable from a public route.

        `assert_publishable` refuses one and `PUBLISHED_SUITE` filters on
        `runnable`, so the executed cases arrive from a recorded run instead —
        the blocked ones plus the recordings, which is what a reader of the page
        actually gets.

        The blocked count is read off the catalogue rather than written here as
        a literal. It has changed twice as cases were unblocked, and each time a
        hand-edited number made a genuine improvement look like a regression;
        what this test is actually for is that every blocked case *reaches the
        page*, not that there are precisely N of them.
        """
        from app.verify.nafems import CASES

        expected_blocked = sum(1 for case in CASES if case.blocker is not None)
        register = published_register()

        assert register.summary.benchmarks == len(CASES)
        assert register.summary.blocked == expected_blocked
        assert register.summary.measured_only == 0

    def test_the_headline_carries_the_denominator(self) -> None:
        """'3 analyses validated' is true of a product with three and of a
        product with thirty. The sentence must contain both numbers."""
        register = published_register()
        headline = register.headline()

        assert f"{len(VALIDATED_TODAY)} of {len(ANALYSES)}" in headline
        assert f"{len(ANALYSES) - len(VALIDATED_TODAY)} are not" in headline

    def test_one_benchmarked_analysis_does_not_hide_the_rest(self) -> None:
        register = Register.build([_outcome(Outcome.VALIDATED, deviation=0.001)])

        validated = [row for row in register.rows if row.validated]
        assert [row.analysis.id for row in validated] == ["linear-static"]
        # The point: ten other analyses are still on the page.
        assert len(register.unvalidated) == len(ANALYSES) - 1
        assert register.complete is False

    def test_breaking_it_the_register_would_read_green_if_it_only_listed_what_ran(
        self,
    ) -> None:
        """The guard, watched to fail.

        `Register.build` walks a *declared* list. Hand it a list containing only
        the analysis that was benchmarked — which is what a register assembled
        from whatever happened to run would be — and the identical outcome
        produces `complete=True` and a headline reading '1 of 1'. Nothing about
        the evidence changed; only the denominator did.
        """
        outcomes = [_outcome(Outcome.VALIDATED, deviation=0.001)]
        only_the_benchmarked_one = [a for a in ANALYSES if a.id == "linear-static"]

        flattering = Register.build(outcomes, analyses=only_the_benchmarked_one)

        assert flattering.complete is True
        assert "1 of 1" in flattering.headline()
        assert flattering.unvalidated == ()

    def test_the_not_validated_section_is_its_own_key_in_the_payload(self) -> None:
        """'Filter the analyses array yourself' is not as easy to find as what
        passed, so the unvalidated rows are published twice on purpose."""
        payload = published_register().to_dict()

        assert {row["id"] for row in payload["not_validated"]} == {
            a.id for a in ANALYSES if a.id not in VALIDATED_TODAY
        }
        assert all(row["because"] for row in payload["not_validated"])

    def test_a_row_that_is_not_validated_must_give_a_reason(self) -> None:
        with pytest.raises(ValueError) as refused:
            AnalysisRow(analysis=ANALYSES[0], standing=Standing.UNVALIDATED, because="   ")

        assert "reason" in str(refused.value).lower()


class TestABenchmarkForAnUndeclaredAnalysisIsNotSwallowed:
    def test_it_is_named_rather_than_filed_under_other(self) -> None:
        register = Register.build([_outcome(Outcome.VALIDATED, analysis="fluid-dynamics")])

        assert register.uncatalogued == ("fluid-dynamics",)
        assert register.complete is False
        assert register.to_dict()["uncatalogued_benchmark_groups"] == ["fluid-dynamics"]

    def test_an_uncatalogued_group_blocks_completeness_even_when_every_row_passes(
        self,
    ) -> None:
        """A typo in a benchmark's `analysis` string would otherwise hide a whole
        family of cases while the register looked finished."""
        outcomes = [
            _outcome(Outcome.VALIDATED, analysis=a.id, benchmark_id=a.id, deviation=0.001)
            for a in ANALYSES
        ]
        assert Register.build(outcomes).complete is True

        with_a_typo = [*outcomes, _outcome(Outcome.VALIDATED, analysis="lienar-static")]
        assert Register.build(with_a_typo).complete is False


# ---------------------------------------------------------------------------
# 2. MEASURED and UNCONVERGED are not coverage
# ---------------------------------------------------------------------------


class TestARanButUncomparedCaseIsNotValidation:
    def test_a_measured_only_case_leaves_the_analysis_unvalidated(self) -> None:
        register = Register.build(
            [_outcome(Outcome.MEASURED, target=_unknown_target(), measured=101.0)]
        )
        row = next(r for r in register.rows if r.analysis.id == "linear-static")

        assert row.standing is Standing.UNVALIDATED
        assert row.accuracy is None
        assert register.summary.validated == 0
        assert register.summary.measured_only == 1
        assert "None of these is a pass" in row.because

    def test_a_measured_case_is_marked_as_not_counting_in_the_payload(self) -> None:
        """A reader scanning the JSON must not have to know which of six outcome
        strings are passes."""
        register = Register.build(
            [_outcome(Outcome.MEASURED, target=_unknown_target(), measured=101.0)]
        )
        row = next(
            r for r in register.to_dict()["analyses"] if r["analysis"]["id"] == "linear-static"
        )

        assert row["benchmarks"][0]["counts_as_validated"] is False
        assert row["validated"] is False

    def test_an_unconverged_case_leaves_the_analysis_unvalidated(self) -> None:
        register = Register.build([_outcome(Outcome.UNCONVERGED)])
        row = next(r for r in register.rows if r.analysis.id == "linear-static")

        assert row.standing is Standing.UNVALIDATED
        assert register.summary.unconverged == 1
        assert register.summary.validated == 0

    def test_a_blocked_case_leaves_the_analysis_unvalidated(self) -> None:
        register = Register.build([_outcome(Outcome.BLOCKED)])

        assert register.summary.blocked == 1
        assert register.summary.validated == 0

    def test_a_deviated_case_is_disputed_rather_than_merely_unvalidated(self) -> None:
        """Louder than 'nobody checked': somebody checked and it missed."""
        register = Register.build([_outcome(Outcome.DEVIATED, deviation=0.31)])
        row = next(r for r in register.rows if r.analysis.id == "linear-static")

        assert row.standing is Standing.DISPUTED
        assert "outside the accepted band" in row.because
        assert register.summary.deviated == 1

    def test_one_deviated_case_beside_nine_passes_still_disputes_the_analysis(self) -> None:
        outcomes = [
            _outcome(Outcome.VALIDATED, benchmark_id=f"ok-{i}", deviation=0.001) for i in range(9)
        ]
        outcomes.append(_outcome(Outcome.DEVIATED, benchmark_id="miss", deviation=0.4))

        row = next(r for r in Register.build(outcomes).rows if r.analysis.id == "linear-static")
        assert row.standing is Standing.DISPUTED

    def test_breaking_it_only_a_validated_case_moves_the_standing(self) -> None:
        """The positive control. Without it every assertion above would also
        pass on a register that could never say `validated` at all."""
        for outcome in (Outcome.MEASURED, Outcome.UNCONVERGED, Outcome.BLOCKED, Outcome.ERRORED):
            target = _unknown_target() if outcome is Outcome.MEASURED else _target()
            built = Register.build([_outcome(outcome, target=target)])
            assert built.summary.validated == 0, outcome

        passing = Register.build([_outcome(Outcome.VALIDATED, deviation=0.004)])
        assert passing.summary.validated == 1


class TestAccuracyIsTheWorstCase:
    def test_it_reports_the_worst_deviation_not_the_average(self) -> None:
        outcomes = [
            _outcome(Outcome.VALIDATED, benchmark_id="a", deviation=0.001),
            _outcome(Outcome.VALIDATED, benchmark_id="b", deviation=-0.018),
        ]
        row = next(r for r in Register.build(outcomes).rows if r.analysis.id == "linear-static")

        assert row.accuracy is not None
        assert row.accuracy.worst_relative_deviation == pytest.approx(0.018)
        assert row.accuracy.cases == 2
        assert row.accuracy.sources == ("NAFEMS LE1, elliptic membrane",)


# ---------------------------------------------------------------------------
# 3. The register republishes nothing benchmarks.py would refuse
# ---------------------------------------------------------------------------


class TestATargetTheBenchmarkLayerWouldRefuseIsNotReprinted:
    """`Target.__post_init__` is the rule that stops a remembered number
    becoming a citation. A frozen dataclass is frozen against `t.value = 3` and
    nothing else, so the register re-runs the constructor before printing."""

    @staticmethod
    def _smuggled(**overrides: object) -> Target:
        target = _target(value=1234.5678)
        for name, value in overrides.items():
            object.__setattr__(target, name, value)
        return target

    def test_an_unknown_target_carrying_a_number_has_the_number_withheld(self) -> None:
        smuggled = self._smuggled(basis=TargetBasis.UNKNOWN, reason="never checked")
        register = Register.build(
            [_outcome(Outcome.VALIDATED, target=smuggled, measured=1234.5, deviation=0.0001)]
        )

        payload = register.to_dict()
        row = next(r for r in payload["analyses"] if r["analysis"]["id"] == "linear-static")
        assert row["benchmarks"][0]["target"]["value"] == WITHHELD
        assert row["benchmarks"][0]["target"]["source"] == WITHHELD
        assert "1234.5678" not in json.dumps(payload)

    def test_a_refused_target_cannot_validate_its_analysis(self) -> None:
        """The verdict was computed against a target the register has just
        refused to print, so the verdict cannot stand either."""
        smuggled = self._smuggled(source="   ")
        register = Register.build(
            [_outcome(Outcome.VALIDATED, target=smuggled, deviation=0.0001)]
        )
        row = next(r for r in register.rows if r.analysis.id == "linear-static")

        assert row.validated is False
        assert row.standing is Standing.DISPUTED
        assert register.summary.validated == 0
        assert register.summary.refused_targets == 1

    def test_the_refusal_itself_is_published(self) -> None:
        register = Register.build(
            [_outcome(Outcome.VALIDATED, target=self._smuggled(source=""), deviation=0.0)]
        )
        row = next(r for r in register.rows if r.analysis.id == "linear-static")

        assert len(row.refusals) == 1
        assert "case-1" in row.refusals[0]
        assert "source" in row.refusals[0].lower()

    def test_breaking_it_an_intact_target_is_published_in_full(self) -> None:
        """The guard watched from the other side. The same case with a target
        that was never tampered with validates and prints its citation, so the
        assertions above are about the tampering and not about the plumbing."""
        register = Register.build(
            [_outcome(Outcome.VALIDATED, target=_target(value=1234.5678), deviation=0.0001)]
        )
        row = next(r for r in register.rows if r.analysis.id == "linear-static")
        published = row.to_dict()["benchmarks"][0]["target"]

        assert row.standing is Standing.VALIDATED
        assert row.refusals == ()
        assert published["value"] == pytest.approx(1234.5678)
        assert published["source"] == "NAFEMS LE1, elliptic membrane"


# ---------------------------------------------------------------------------
# 4. The published register runs nothing
# ---------------------------------------------------------------------------


class TestThePublishedSuiteCannotRunAnything:
    def test_the_shipped_suite_is_publishable(self) -> None:
        from app.verify.register import PUBLISHED_SUITE

        assert_publishable(PUBLISHED_SUITE)

    def test_a_runnable_benchmark_in_a_published_suite_is_refused(self) -> None:
        """Breaking it: the route this suite feeds is unauthenticated, so a
        runnable case there is a way to spend the server's CPU by asking."""
        runnable = Suite(
            name="dangerous",
            benchmarks=(
                Benchmark(
                    id="nafems-le1",
                    title="elliptic membrane",
                    analysis="linear-static",
                    description="",
                    target=_target(),
                    run=lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
                ),
            ),
        )

        with pytest.raises(ValueError) as refused:
            assert_publishable(runnable)

        assert "nafems-le1" in str(refused.value)
        assert "Register.build" in str(refused.value)

    def test_a_blocked_benchmark_is_publishable_because_it_costs_nothing(self) -> None:
        blocked = Suite(
            name="fine",
            benchmarks=(
                Benchmark(
                    id="nafems-le10",
                    title="thick plate",
                    analysis="linear-static",
                    description="",
                    target=_target(),
                    blocked_reason="needs shell elements",
                ),
            ),
        )

        assert_publishable(blocked)
        assert Register.build(_blocked_outcomes()).summary.validated == 0


# ---------------------------------------------------------------------------
# 5. The declaration itself
# ---------------------------------------------------------------------------


class TestTheDeclaredAnalyses:
    def test_every_analysis_points_at_a_module_that_exists(self) -> None:
        """A register whose 'open this file' links rot is a register nobody can
        check, which is the only thing it is for."""
        for analysis in ANALYSES:
            path = REPO_ROOT / Path(*analysis.module.split("."))
            assert path.with_suffix(".py").exists() or path.is_dir(), analysis.module

    def test_every_analysis_says_what_it_answers(self) -> None:
        for analysis in ANALYSES:
            assert analysis.answers.strip(), analysis.id
            assert analysis.title.strip(), analysis.id

    def test_an_unavailable_analysis_must_say_why(self) -> None:
        with pytest.raises(ValueError) as refused:
            Analysis(
                id="cfd",
                title="Fluid dynamics",
                module="app.solve.cfd",
                answers="what does the air do",
                available=False,
            )

        assert "complaint" in str(refused.value)

    def test_an_available_analysis_may_not_carry_an_excuse(self) -> None:
        with pytest.raises(ValueError):
            Analysis(
                id="cfd",
                title="Fluid dynamics",
                module="app.solve.cfd",
                answers="what does the air do",
                unavailable_because="not written",
            )

    def test_closed_form_checks_are_labelled_as_verification_in_the_payload(self) -> None:
        """The ASME V&V 20 split, defended at the one place it would be lost: a
        reader seeing a list of closed-form checks under a key called
        `closed_form_checks` would reasonably read them as the validation."""
        payload = published_register().to_dict()
        rows = {row["analysis"]["id"]: row for row in payload["analyses"]}

        assert rows["linear-static"]["analysis"]["verification_closed_form_checks"]

        # The split is only visible on an analysis that has closed-form checks
        # and is *not* validated. Until 2026-09-08 linear static was the example;
        # LE10 validated it, so the example moved to buckling, which is checked
        # against the Euler column and has no published benchmark behind it.
        buckling = rows["buckling"]
        assert buckling["analysis"]["verification_closed_form_checks"]
        assert buckling["validated"] is False
        assert any("Verification and validation are different" in n for n in payload["notes"])


# ---------------------------------------------------------------------------
# 6. Scrubbing free text
# ---------------------------------------------------------------------------


class TestScrub:
    @pytest.mark.parametrize(
        "text",
        [
            r"FileNotFoundError: C:\Users\achraf\Desktop\part.stp",
            "IOError: /home/somebody/kryova/blob",
            r"\\fileserver\share\customer.CATPart",
            "could not open /var/lib/kryova/blobs/ab/cd",
        ],
    )
    def test_it_withholds_anything_that_looks_like_a_path(self, text: str) -> None:
        assert "[withheld" in scrub(text)

    @pytest.mark.parametrize(
        "text",
        [
            "101.2 against 100.0 MPa (+1.200%, band +/-2.000%)",
            "needs shell elements",
            "the observed order of convergence was negative",
        ],
    )
    def test_it_leaves_ordinary_result_text_alone(self, text: str) -> None:
        assert scrub(text) == text

    @pytest.mark.parametrize(
        "text",
        [
            "Read at https://abaqus-docs.mit.edu/2017/English/x.htm on 2026-09-08.",
            "see http://www.nafems.org/benchmarks for the publication",
            "ftp://example.invalid/tnsb.pdf",
        ],
    )
    def test_a_citation_url_survives_because_it_is_not_a_path(self, text: str) -> None:
        """The guard destroying what it protects, caught 2026-09-08.

        `scrub` replaces the *whole* string, and the drive-letter branch used to
        match the `s:/` inside `https://` — so on a page whose entire value is
        checkable references, every reason that cited its document would have
        published "[withheld: looked like a filesystem path]" instead.
        """
        assert scrub(text) == text

    def test_a_windows_path_is_still_withheld_when_a_url_sits_beside_it(self) -> None:
        """The narrowing must not be a hole: one character before the colon is
        the whole of the distinction, and a real drive letter has none."""
        text = r"see https://example.invalid, opened C:\Users\achraf\part.stp"

        assert "[withheld" in scrub(text)


# ---------------------------------------------------------------------------
# 7. The accuracy changelog
# ---------------------------------------------------------------------------


class TestTheAccuracyChangelog:
    def test_every_entry_names_analyses_the_register_declares(self) -> None:
        """Checked at import, asserted here so the failure is a named test
        rather than a stack trace during collection."""
        declared = {a.id for a in ANALYSES}
        for change in CHANGES:
            assert change.analyses or change.affects_everything, change.date
            assert set(change.analyses) <= declared, change.date

    def test_an_entry_naming_an_unknown_analysis_is_refused(self) -> None:
        with pytest.raises(ValueError) as refused:
            AccuracyChange(
                date="2026-09-06",
                summary="something",
                effect=Effect.RESULTS_CHANGE,
                analyses=("computational-astrology",),
                what_changed="x",
                what_to_do="y",
                commit="deadbee",
            )

        assert "not in the register" in str(refused.value)

    def test_an_entry_with_no_action_is_refused(self) -> None:
        """'We changed the solver' is a notification. This page exists so that
        an accuracy change is not merely announced."""
        with pytest.raises(ValueError) as refused:
            AccuracyChange(
                date="2026-09-06",
                summary="solver swapped",
                effect=Effect.RESULTS_CHANGE,
                analyses=("linear-static",),
                what_changed="x",
                what_to_do="",
                commit="deadbee",
            )

        assert "notification" in str(refused.value)

    def test_an_entry_nobody_can_look_up_is_refused(self) -> None:
        with pytest.raises(ValueError):
            AccuracyChange(
                date="2026-09-06",
                summary="trust us",
                effect=Effect.RESULTS_CHANGE,
                analyses=("linear-static",),
                what_changed="x",
                what_to_do="re-run it",
            )

    def test_a_refusal_is_counted_as_changing_results(self) -> None:
        """A number that used to come back and now does not is not something a
        user should have to reason about separately."""
        refused = next(c for c in CHANGES if c.effect is Effect.RESULTS_REFUSED)
        assert refused.changes_results is True

    def test_entries_are_newest_first(self) -> None:
        assert [c.date for c in CHANGES] == sorted((c.date for c in CHANGES), reverse=True)

    def test_since_and_affecting_select_the_right_entries(self) -> None:
        assert all(c.date > "2026-09-03" for c in since("2026-09-03"))
        assert all("modal" in c.analyses for c in affecting("modal"))
        assert affecting("modal")

    def test_a_register_generated_before_an_accuracy_change_says_so(self) -> None:
        """A register is a claim about a build. Serving a stale one beside a
        changelog nobody cross-referenced is how a green page outlives the
        product it described."""
        stale = Register.build(generated_at="2026-08-30T00:00:00+00:00")
        superseded = stale.superseded_by(CHANGES)

        assert superseded
        assert all(c.changes_results for c in superseded)

        fresh = Register.build(generated_at="2099-01-01T00:00:00+00:00")
        assert fresh.superseded_by(CHANGES) == ()


class TestTheNotesDoNotContradictTheNumbers:
    """The page's prose is published alongside its counts, and nothing was
    checking that the two agreed.

    They stopped agreeing the day a third case validated: a note still read
    "Nothing in this register is validated today" while the summary beside it
    said two analyses were. That is worse on a trust page than either statement
    alone — a reader cannot tell which half is stale, so neither is usable, and
    the whole value of the page is that a reader does not have to decide that.

    The notes are static text and the summary is computed, so the only way they
    can be kept honest is a test that reads both.
    """

    def test_no_note_claims_nothing_is_validated_when_something_is(self) -> None:
        register = published_register()
        prose = " ".join(register.notes).lower()

        if register.summary.validated > 0:
            for claim in ("nothing in this register is validated", "none is validated"):
                assert claim not in prose, (
                    f"a note says {claim!r} while the summary reports "
                    f"{register.summary.validated} validated analyses"
                )

    def test_the_notes_say_the_page_never_runs_a_benchmark(self) -> None:
        """`assert_publishable` enforces it in code; a reader of the page needs
        to be told, because a validated row otherwise reads as something this
        route computed on request."""
        prose = " ".join(published_register().notes).lower()

        assert "never runs a benchmark" in prose

    def test_the_notes_explain_that_a_validated_row_can_expire(self) -> None:
        """The fingerprint is what separates this page from a cached green tick,
        and it is invisible unless the page says so."""
        prose = " ".join(published_register().notes).lower()

        assert "fingerprint" in prose
        assert "discarded" in prose

    def test_the_verification_and_validation_split_is_still_stated_first(self) -> None:
        """ASME V&V 20's distinction is the one thing a reader must have before
        reading anything else on the page: the closed-form checks in the same
        payload are verification, and reading them as validation is the exact
        conflation this register exists to prevent."""
        first = published_register().notes[0].lower()

        assert "verification and validation are different questions" in first
