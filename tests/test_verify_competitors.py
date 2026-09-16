"""The competitor register quotes pages, says how it read them, and expires — master plan
E23.1 and E23.2.

These pin the register's rules, not the competition: a claim names the page it was read on, a
claim that was not re-read carries no quote and says why, a confirmed or changed claim quotes
the page, and the register goes red once its review date has passed.
"""

from __future__ import annotations

import datetime
from datetime import date

import pytest

from app.verify import competitors
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


class TestTheJudgementTheRegisterFeeds:
    """E23.2's other half. The register is observations; this is the answer, and
    the reason it is a shape rather than a function is the task itself — "is
    nobody selling credibility?" is a judgement about a market, and one derived
    by code from quotes would be a verdict nobody reached wearing the authority
    of a measurement."""

    def _valid(self, **overrides: object) -> competitors.Assessment:
        claim = competitors.REGISTER[0]
        fields: dict = {
            "verdict": competitors.Verdict.OPEN,
            "author": "A Person",
            "written_on": "2026-09-16",
            "reasoning": (
                "The step from the quotes to the verdict, which is the part no "
                "quote contains."
            ),
            "cites": ((claim.subject, claim.claim),),
        }
        fields.update(overrides)
        return competitors.Assessment(**fields)  # type: ignore[arg-type]

    def test_nobody_has_written_one_and_the_product_says_so(self) -> None:
        """The honest state today. A page that silently printed `OPEN` because
        nobody had looked would be the most convincing wrong sentence in the
        product — the plan's four-year-old assumption read back as a finding."""
        assert competitors.ASSESSMENTS == ()

        said = competitors.answer(date(2026, 9, 16))

        assert said.startswith("Unanswered")
        assert competitors.REVIEW_BY.isoformat() in said

    def test_the_answer_is_never_empty_and_never_a_default_verdict(self) -> None:
        for today in (date(2026, 9, 16), date(2026, 12, 15), date(2027, 6, 1)):
            said = competitors.answer(today)
            assert said.strip()
            assert not said.startswith("open")

    def test_past_the_review_date_the_reader_is_told_to_re_read(self) -> None:
        """Not to move the date alone — the shape `eu_ai_act.REVIEW_BY` has."""
        said = competitors.answer(date(2027, 1, 1))

        assert "past its review date" in said
        assert "Re-read every source" in said

    def test_an_assessment_names_who_reached_it(self) -> None:
        """A verdict with no author is one nobody can be asked about."""
        with pytest.raises(ValueError, match="names who reached it"):
            self._valid(author="   ")

    def test_an_assessment_carries_a_date_in_the_registers_spelling(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            self._valid(written_on="16/09/2026")

    def test_an_assessment_says_why(self) -> None:
        """The quotes are in the register. What is wanted here is the step from
        them to the verdict."""
        with pytest.raises(ValueError, match="says why"):
            self._valid(reasoning="Still open.")

    def test_an_assessment_with_no_citations_is_an_opinion(self) -> None:
        with pytest.raises(ValueError, match="cites the claims"):
            self._valid(cites=())

    def test_a_citation_the_register_does_not_hold_is_refused(self) -> None:
        """The rule `Target` applies to a published basis, applied here: a
        verdict may only rest on something a reader can go and re-read."""
        with pytest.raises(ValueError, match="holds no claim"):
            self._valid(cites=(("Zoo", "Something nobody wrote down"),))

    def test_a_verdict_may_not_rest_on_a_page_nobody_could_open(self) -> None:
        """Two claims in the register are `NOT_REREAD` and carry no quote. A
        position built on one is the failure this task exists to prevent."""
        unread = next(
            claim
            for claim in competitors.REGISTER
            if claim.standing is competitors.Standing.NOT_REREAD
        )

        with pytest.raises(ValueError, match="was not re-read"):
            self._valid(cites=((unread.subject, unread.claim),))

    def test_an_assessment_answers_the_written_down_question(self) -> None:
        """So a later one cannot drift onto an easier question and still read as
        an answer to this one."""
        assert "credibility" in competitors.THE_QUESTION

        elsewhere = self._valid(question="Is anybody faster than us?")

        assert competitors.current_assessment(date(2026, 9, 16)) is None
        assert elsewhere.question != competitors.THE_QUESTION

    def test_an_assessment_does_not_outlive_the_reading_it_rests_on(self) -> None:
        """A verdict quoted from a register whose sources nobody has checked for
        six months is exactly the plan-assumes-an-unoccupied-niche failure."""
        import app.verify.competitors as module

        written = self._valid()
        original = module.ASSESSMENTS
        try:
            module.ASSESSMENTS = (written,)  # type: ignore[misc]
            assert module.current_assessment(date(2026, 9, 16)) is written
            assert module.current_assessment(date(2027, 1, 1)) is None
        finally:
            module.ASSESSMENTS = original  # type: ignore[misc]

    def test_the_newest_assessment_inside_the_window_is_the_one_reported(
        self,
    ) -> None:
        import app.verify.competitors as module

        older = self._valid(written_on="2026-09-16")
        newer = self._valid(written_on="2026-10-01", verdict=competitors.Verdict.NARROWING)
        original = module.ASSESSMENTS
        try:
            module.ASSESSMENTS = (older, newer)  # type: ignore[misc]
            assert module.current_assessment(date(2026, 11, 1)) is newer
            assert "narrowing" in module.answer(date(2026, 11, 1))
        finally:
            module.ASSESSMENTS = original  # type: ignore[misc]

    def test_the_vocabulary_admits_not_knowing(self) -> None:
        """Without `UNCLEAR` the only way to record "the register does not settle
        it" is to pick a side, which is how a register stops being read."""
        assert competitors.Verdict.UNCLEAR.value == "unclear"
        assert {v.value for v in competitors.Verdict} == {
            "open",
            "narrowing",
            "occupied",
            "unclear",
        }
