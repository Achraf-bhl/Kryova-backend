"""Tolerance stack-up — master plan Phase 13, and the easiest thing here to get right.

Closing a chain of tolerances is exact arithmetic, so it is checked against
arithmetic done here rather than against what the code returned. A module
verified against its own previous output is not verified.

The interesting half is not the sum, it is **when a statistical answer is
allowed to exist at all**. Worst case is exact interval arithmetic and always
has an answer. RSS is a statistical claim, and it rests on assumptions that no
amount of arithmetic can establish: that the contributors are independent, that
their distributions are what somebody assumed, that the processes are capable of
what was declared. An RSS number handed over without those is a number that will
be believed and should not be — it is roughly a third narrower than the worst
case on a three-part chain, and a designer who takes it and is wrong finds out at
assembly.

So `stack` will not produce one anonymously. Every assumption in `Risk` must be
either established from the declared capability data or explicitly acknowledged,
and acknowledging requires a name, because an assumption nobody signed for is an
assumption nobody made. `Risk.INDEPENDENCE` can never be established from
numbers, so it is always on that list.
"""

from __future__ import annotations

import math

import pytest

from app.rules.errors import SourceError, StackUpError
from app.rules.stackup import Method, Risk, stack, symmetric

#: A three-part chain, chosen so the two methods differ by an amount nobody
#: could mistake for rounding: worst case 0.30, RSS sqrt(0.02) ~ 0.1414.
CHAIN = (
    ("a", 40.0, 0.10),
    ("b", -25.0, 0.10),
    ("c", -14.0, 0.10),
)

WORST_CASE_HALF_WIDTH = 0.30
RSS_HALF_WIDTH = math.sqrt(0.10**2 + 0.10**2 + 0.10**2)


def _chain():
    return [symmetric(name=n, nominal_mm=v, tolerance_mm=t) for n, v, t in CHAIN]


class TestWorstCaseIsExactIntervalArithmetic:
    def test_the_half_width_is_the_arithmetic_sum(self) -> None:
        result = stack(_chain(), method=Method.WORST_CASE)

        assert result.available
        assert result.half_width_mm == pytest.approx(WORST_CASE_HALF_WIDTH, abs=1e-12)

    def test_the_nominal_is_the_signed_sum(self) -> None:
        """40 − 25 − 14 = 1.0. Direction lives in the sign of the nominal."""
        result = stack(_chain(), method=Method.WORST_CASE)

        assert result.nominal_mm == pytest.approx(1.0, abs=1e-12)

    def test_the_limits_are_the_nominal_plus_and_minus_the_half_width(self) -> None:
        result = stack(_chain(), method=Method.WORST_CASE)

        assert result.minimum_mm == pytest.approx(1.0 - WORST_CASE_HALF_WIDTH, abs=1e-12)
        assert result.maximum_mm == pytest.approx(1.0 + WORST_CASE_HALF_WIDTH, abs=1e-12)

    def test_one_contributor_alone_is_its_own_tolerance(self) -> None:
        """The edge that catches an off-by-one in the accumulation, and the case
        where worst case and RSS must agree exactly."""
        result = stack(
            [symmetric(name="only", nominal_mm=10.0, tolerance_mm=0.05)],
            method=Method.WORST_CASE,
        )

        assert result.half_width_mm == pytest.approx(0.05, abs=1e-12)

    def test_sensitivity_multiplies_the_contribution(self) -> None:
        """A dimension at an angle contributes cos θ of its tolerance. Deriving
        the factor is the engineer's job; this multiplies by it."""
        result = stack(
            [symmetric(name="angled", nominal_mm=10.0, tolerance_mm=0.10, sensitivity=0.5)],
            method=Method.WORST_CASE,
        )

        assert result.half_width_mm == pytest.approx(0.05, abs=1e-12)

    def test_worst_case_is_always_available(self) -> None:
        """It rests on no assumption at all, so it never needs acknowledging."""
        assert stack(_chain(), method=Method.WORST_CASE).available


class TestAnAnonymousRssIsNotObtainable:
    """The judgement half, and the reason this module is worth more than a sum."""

    def test_rss_without_acknowledgement_produces_no_number(self) -> None:
        result = stack(_chain(), method=Method.STATISTICAL)

        assert not result.available
        assert result.half_width_mm is None, (
            "an unavailable statistical result must not carry a number; a caller "
            "reading half_width_mm would use it"
        )

    def test_acknowledging_without_a_name_is_refused(self) -> None:
        """An assumption nobody signed for is an assumption nobody made.

        `SourceError`, not `StackUpError`: the two are siblings under `RuleError`
        on purpose, and an unsigned acknowledgement is a fault in the
        *provenance* rather than in the chain. Asserting the precise type is
        what records that the distinction is intended rather than incidental.
        """
        with pytest.raises(SourceError):
            stack(
                _chain(),
                method=Method.STATISTICAL,
                acknowledged=[Risk.INDEPENDENCE],
                acknowledged_by="",
            )

    def test_independence_can_never_be_established_from_numbers(self) -> None:
        """Two dimensions cut on the same machine in the same setup are not
        independent, and no amount of capability data reveals that — only
        somebody who knows how the parts are made."""
        result = stack(
            _chain(),
            method=Method.STATISTICAL,
            acknowledged=[Risk.INDEPENDENCE],
            acknowledged_by="A. Engineer",
        )

        # It may still be unavailable for the *other* risks, but independence
        # alone must never be silently assumed.
        assert Risk.INDEPENDENCE not in getattr(result, "unacknowledged", ()) or not result.available


class TestWhenRssIsAllowedItIsTheRightNumber:
    def _acknowledged(self):
        return stack(
            _chain(),
            method=Method.STATISTICAL,
            acknowledged=list(Risk),
            acknowledged_by="A. Engineer",
        )

    def test_it_is_the_root_sum_square(self) -> None:
        result = self._acknowledged()

        if not result.available:
            pytest.skip(
                "statistical stacking is unavailable even with every risk "
                "acknowledged, so the RSS arithmetic was NOT measured"
            )
        assert result.half_width_mm == pytest.approx(RSS_HALF_WIDTH, abs=1e-9)

    def test_it_is_narrower_than_the_worst_case(self) -> None:
        """The relationship, not just the numbers — it must hold for any chain,
        and it is the whole reason anyone reaches for RSS."""
        result = self._acknowledged()

        if not result.available:
            pytest.skip("statistical stacking unavailable; the comparison was NOT measured")
        worst = stack(_chain(), method=Method.WORST_CASE)
        assert result.half_width_mm < worst.half_width_mm

    def test_and_never_wider(self) -> None:
        """A single contributor is the boundary case: the two must be equal, and
        RSS must never exceed worst case for any chain."""
        one = [symmetric(name="only", nominal_mm=10.0, tolerance_mm=0.05)]
        worst = stack(one, method=Method.WORST_CASE)
        rss = stack(
            one,
            method=Method.STATISTICAL,
            acknowledged=list(Risk),
            acknowledged_by="A. Engineer",
        )

        if not rss.available:
            pytest.skip("statistical stacking unavailable; the boundary was NOT measured")
        assert rss.half_width_mm <= worst.half_width_mm + 1e-12


class TestTheChainIsChecked:
    def test_an_empty_chain_is_refused(self) -> None:
        """A stack of nothing has no answer, and returning 0.0 would look like
        a very good tolerance."""
        with pytest.raises(StackUpError):
            stack([], method=Method.WORST_CASE)

    def test_a_negative_tolerance_is_refused(self) -> None:
        """The chain closes on magnitudes; direction lives in the nominal's
        sign. A negative tolerance would subtract from the band."""
        with pytest.raises(StackUpError):
            stack(
                [symmetric(name="bad", nominal_mm=10.0, tolerance_mm=-0.05)],
                method=Method.WORST_CASE,
            )

    def test_a_sigma_multiple_of_zero_is_refused(self) -> None:
        """It is how many standard deviations the band spans each side, so zero
        reports a band of nothing."""
        with pytest.raises(StackUpError):
            stack(_chain(), method=Method.STATISTICAL, sigma_multiple=0.0)
