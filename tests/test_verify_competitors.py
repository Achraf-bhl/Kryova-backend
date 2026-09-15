"""The competitor register quotes pages, says how it read them, and expires — master plan
E23.1 and E23.2.

These pin the register's rules, not the competition: a claim names the page it was read on, a
claim that was not re-read carries no quote and says why, a confirmed or changed claim quotes
the page, and the register goes red once its review date has passed.
"""

from __future__ import annotations

import datetime

import pytest

from app.verify import competitors as reg
from app.verify.competitors import Claim, ReadAs, Standing

_PAGE = "https://example.com/a-page"


class TestAClaimSaysWhereItWasRead:
    def test_a_claim_with_no_https_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names the page"):
            Claim("X", "a claim", Standing.CONFIRMED, "example.com/page", ReadAs.PAGE, quote="q")

    def test_a_claim_not_re_read_carrying_a_quote_is_refused(self) -> None:
        with pytest.raises(ValueError, match="carries no quote"):
            Claim(
                "X", "a claim", Standing.NOT_REREAD, _PAGE, ReadAs.NOT_READ, quote="q", note="why"
            )

    def test_a_claim_not_re_read_that_says_it_read_the_page_is_refused(self) -> None:
        with pytest.raises(ValueError, match="carries no quote"):
            Claim("X", "a claim", Standing.NOT_REREAD, _PAGE, ReadAs.PAGE, note="why")

    def test_a_claim_not_re_read_must_say_why(self) -> None:
        with pytest.raises(ValueError, match="say why"):
            Claim("X", "a claim", Standing.NOT_REREAD, _PAGE, ReadAs.NOT_READ)

    @pytest.mark.parametrize("standing", [Standing.CONFIRMED, Standing.CHANGED])
    def test_a_confirmed_or_changed_claim_with_no_quote_is_refused(
        self, standing: Standing
    ) -> None:
        with pytest.raises(ValueError, match="quotes the page"):
            Claim("X", "a claim", standing, _PAGE, ReadAs.PAGE)

    @pytest.mark.parametrize("standing", [Standing.CONFIRMED, Standing.CHANGED])
    def test_a_confirmed_or_changed_claim_that_read_nothing_is_refused(
        self, standing: Standing
    ) -> None:
        with pytest.raises(ValueError, match="quotes the page"):
            Claim("X", "a claim", standing, _PAGE, ReadAs.NOT_READ, quote="q")

    def test_a_well_formed_claim_of_each_standing_is_accepted(self) -> None:
        Claim("X", "a claim", Standing.CONFIRMED, _PAGE, ReadAs.PAGE, quote="q")
        Claim("X", "a claim", Standing.CHANGED, _PAGE, ReadAs.SEARCH_EXTRACT, quote="q")
        Claim("X", "a claim", Standing.NOT_REREAD, _PAGE, ReadAs.NOT_READ, note="why")


class TestTheRegisterCoversThePlan:
    def test_every_competitor_the_plan_names_has_a_claim(self) -> None:
        for subject in ("Zoo", "PTC / Onshape", "FreeCAD", "PhysicsX"):
            assert reg.claims_about(subject), subject

    def test_claims_about_partitions_the_register(self) -> None:
        subjects = {claim.subject for claim in reg.REGISTER}
        regrouped = [claim for subject in subjects for claim in reg.claims_about(subject)]
        assert sorted(map(id, regrouped)) == sorted(map(id, reg.REGISTER))

    def test_no_claim_is_recorded_twice(self) -> None:
        keys = [(claim.subject, claim.claim) for claim in reg.REGISTER]
        assert len(keys) == len(set(keys))

    def test_every_claim_was_read_on_the_register_date(self) -> None:
        for claim in reg.REGISTER:
            assert datetime.date.fromisoformat(claim.read_on) <= reg.REVIEW_BY, claim.claim

    def test_a_quote_is_never_padded(self) -> None:
        for claim in reg.REGISTER:
            assert claim.quote == claim.quote.strip(), claim.claim


class TestTheRegisterExpires:
    def test_the_review_date_is_a_quarter_after_the_reading(self) -> None:
        read_on = datetime.date.fromisoformat(reg.READ_ON)
        assert read_on < reg.REVIEW_BY <= read_on + datetime.timedelta(days=92)

    def test_the_boundary_day_is_still_current_and_the_day_after_is_not(self) -> None:
        assert reg.is_current(reg.REVIEW_BY)
        assert not reg.is_current(reg.REVIEW_BY + datetime.timedelta(days=1))

    def test_the_register_has_been_read_recently_enough_to_be_believed(self) -> None:
        """Fails on purpose after REVIEW_BY. The fix is to re-read every source in
        `app/verify/competitors.py`, update what moved, and set a new date — never to move the
        date alone (E23.2)."""
        assert reg.is_current(datetime.date.today()), (
            f"The competitor register was last read on {reg.READ_ON} and was due to be re-read "
            f"by {reg.REVIEW_BY}. Re-read every source it cites and update what moved."
        )
