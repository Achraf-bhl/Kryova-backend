"""A benchmark target that is not published says so — master plan Phase 7.

`app/verify/benchmarks.py` is deliberately machinery and no cases: it defines
what a benchmark *is*, what a target may claim, and how a run is classified. The
cases arrive later, and this is the layer that decides whether they can lie.

The failure it exists to prevent is specific and would be catastrophic here, in
the one part of the codebase whose whole purpose is to be trusted: **a target
that looks published and is not.** A NAFEMS number recalled from memory, or
reverse-engineered from what our own solver happened to return, is worse than no
benchmark at all — it converts "we have not validated this" into "we validated
this and it passed", and nobody downstream can tell the difference.

So the rules are structural rather than editorial. `PUBLISHED` requires a
citation. `UNKNOWN` **forbids a value** and requires a reason saying what would
make it known. And a run that landed on its target from a mesh nobody checked is
`UNCONVERGED`, not `VALIDATED` — because an unconverged number that happens to
be right is right by accident, and the plan says an unconverged number is worse
than no number.

Exactly one `Outcome` is a pass. Everything else — including `MEASURED`, which is
"we ran it and there was nothing to compare against" — is not.
"""

from __future__ import annotations

import pytest

from app.verify.benchmarks import Outcome, Target, TargetBasis


class TestAPublishedTargetMustBeCitable:
    def test_a_published_target_carries_a_value(self) -> None:
        target = Target(
            basis=TargetBasis.PUBLISHED,
            unit="mm",
            value=0.0009253,
            tolerance=0.02,
            tolerance_reason="the discretisation error of a tet10 mesh at this refinement",
            source="NAFEMS LE10, thick plate under pressure",
        )

        assert target.value == pytest.approx(0.0009253)

    def test_a_published_target_without_a_source_is_refused(self) -> None:
        """A citation nobody can follow is the same as no citation. This is the
        rule that stops a remembered number becoming a benchmark."""
        with pytest.raises(ValueError):
            Target(basis=TargetBasis.PUBLISHED, unit="mm", value=1.0, tolerance=0.02, source="")

    def test_a_published_target_without_a_value_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Target(
                basis=TargetBasis.PUBLISHED,
            unit="mm",
                value=None,
                tolerance=0.02,
                source="NAFEMS LE1",
            )


class TestAnUnknownTargetForbidsANumber:
    """The state that makes the whole scheme honest: a case can be *encoded*
    without being *validated*, and say which it is."""

    def test_it_refuses_to_carry_a_value(self) -> None:
        with pytest.raises(ValueError) as refused:
            Target(
                basis=TargetBasis.UNKNOWN,
            unit="mm",
                value=1.234,
                tolerance=0.02,
                source="",
                reason="the published value has not been obtained",
            )

        assert "guess" in str(refused.value).lower()

    def test_it_must_say_what_would_make_it_known(self) -> None:
        """"Unknown" with no route out of it is a shrug. The reason is what a
        later session acts on."""
        with pytest.raises(ValueError):
            Target(basis=TargetBasis.UNKNOWN, unit="mm", value=None, tolerance=0.02, reason="")

    def test_a_properly_declared_unknown_is_allowed(self) -> None:
        target = Target(
            basis=TargetBasis.UNKNOWN,
            unit="mm",
            value=None,
            tolerance=0.02,
            tolerance_reason="a placeholder band; it means nothing until the target does",
            reason="the NAFEMS booklet is not held here; obtain LE11's target value",
        )

        assert target.value is None
        assert target.reason


class TestTheArithmeticOfATolerance:
    def test_a_zero_target_cannot_carry_a_relative_tolerance(self) -> None:
        """Two percent of zero is zero, so a relative band on a zero target
        accepts nothing at all — including the exactly-right answer. A test that
        can never pass is not a strict test, it is a broken one."""
        with pytest.raises(ValueError):
            Target(
                basis=TargetBasis.DERIVED,
            unit="mm",
                value=0.0,
                tolerance=0.02,
                # Supplied deliberately: without it this raises for a MISSING
                # justification and the test passes for the wrong reason, which
                # is what a mutation of the zero-target guard revealed.
                tolerance_reason="discretisation error",
                source="a quantity that is exactly zero by symmetry",
            )

    def test_a_negative_tolerance_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Target(
                basis=TargetBasis.DERIVED,
            unit="mm",
                value=1.0,
                tolerance=-0.01,
                tolerance_reason="discretisation error",
                source="sigma = F/A",
            )


class TestExactlyOneOutcomeIsAPass:
    def test_validated_is_the_only_pass(self) -> None:
        """Stated as a test because it is the thing a caller gets wrong: a
        reader who treats anything-but-errored as success has converted every
        honest non-result into a green tick."""
        assert Outcome.VALIDATED == "validated"

        not_passes = {
            Outcome.DEVIATED,
            Outcome.MEASURED,
            Outcome.UNCONVERGED,
            Outcome.BLOCKED,
            Outcome.ERRORED,
        }
        assert Outcome.VALIDATED not in not_passes

    def test_measured_is_not_a_pass(self) -> None:
        """"We ran it and there was nothing to compare against" is the outcome
        of a case whose target is UNKNOWN. It is a real result and it is not
        validation, and conflating the two is exactly the failure this module
        exists to prevent."""
        assert Outcome.MEASURED != Outcome.VALIDATED

    def test_unconverged_is_not_a_pass_even_when_the_number_is_right(self) -> None:
        """An unconverged number that lands on its target is right by accident.
        The plan's own words: an unconverged number is worse than no number."""
        assert Outcome.UNCONVERGED != Outcome.VALIDATED

    def test_blocked_is_not_a_pass(self) -> None:
        """A case that could not run at all — no solver, no mesher — must not
        disappear from the register. It is counted, as blocked."""
        assert Outcome.BLOCKED != Outcome.VALIDATED


class TestATolerancveMustBeJustified:
    """A rule worth stating on its own, because it is about what happens *later*.

    A band with no stated reason is a band nobody can defend when a run misses
    it — and the thing that happens then is that somebody widens it, because
    widening it is the only move available to a person who does not know what it
    was covering. A tolerance that says "the discretisation error of a tet10
    mesh at this refinement" cannot be widened without somebody noticing that
    the justification no longer matches the number.
    """

    def test_a_band_with_no_reason_is_refused(self) -> None:
        with pytest.raises(ValueError) as refused:
            Target(
                basis=TargetBasis.PUBLISHED,
                unit="mm",
                value=1.0,
                tolerance=0.02,
                source="NAFEMS LE1",
            )

        assert "justified" in str(refused.value).lower()

    def test_the_refusal_says_what_a_justification_is_for(self) -> None:
        """It names the failure mode rather than just demanding a field, which
        is the difference between a rule people follow and one they route
        around with 'n/a'."""
        with pytest.raises(ValueError) as refused:
            Target(
                basis=TargetBasis.PUBLISHED,
                unit="mm",
                value=1.0,
                tolerance=0.02,
                source="NAFEMS LE1",
            )

        assert "widened" in str(refused.value).lower()
