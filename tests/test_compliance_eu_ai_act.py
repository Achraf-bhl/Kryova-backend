"""The AI Act register says what the amending act says, and says when to re-read
it — master plan E19 task 6.

These pin the register's properties, not the law: every date is quoted from the
Official Journal, the two machinery dates agree with each other, the claim that
was not found stays unpromoted, and the register goes red once its review date
has passed.
"""

from __future__ import annotations

import datetime

import pytest

from app.compliance import eu_ai_act as ai
from app.compliance import eu_machinery_regulation as mr
from app.compliance.provisions import LegalBasis, QuotedClause, Source, SourceKind


def _dated(fragment: str) -> datetime.date:
    matches = [p for p in ai.PROVISIONS if fragment in p.citation]
    assert len(matches) == 1, f"{fragment!r} matches {len(matches)} provisions"
    return matches[0].date


class TestTheRegisterIsReadNotRemembered:
    def test_every_date_is_primary_text_from_the_amending_act(self) -> None:
        for provision in ai.PROVISIONS:
            assert provision.basis is LegalBasis.PRIMARY_TEXT, provision.event
            assert ai.OJ_2026_1744 in provision.sources, provision.event

    def test_every_clause_is_quoted_from_the_official_journal(self) -> None:
        for clause in ai.CLAUSES:
            assert ai.OJ_2026_1744 in clause.sources, clause.citation

    def test_a_clause_quoted_from_a_summary_is_refused(self) -> None:
        blog = Source(
            id="blog", kind=SourceKind.SECONDARY, document="An explainer",
            url="https://example.invalid", read_on=datetime.date(2026, 9, 14),
        )
        with pytest.raises(ValueError, match="no Official Journal"):
            QuotedClause(citation="Article 6(1a)", quote="shall not qualify", sources=(blog,))

    def test_a_clause_without_its_words_is_refused(self) -> None:
        with pytest.raises(ValueError, match="clause and the words"):
            QuotedClause(citation="Article 6(1a)", quote="", sources=(ai.OJ_2026_1744,))


class TestTheDatesAgreeWithOneAnother:
    def test_the_act_entered_into_force_three_days_after_publication(self) -> None:
        assert _dated("2026/1744, Article 4") == datetime.date(2026, 7, 27)

    def test_annex_iii_systems_come_before_annex_i_systems(self) -> None:
        assert _dated("point (c)(i)") == datetime.date(2027, 12, 2)
        assert _dated("point (c)(ii)") == datetime.date(2028, 8, 2)

    def test_the_machinery_delegated_acts_are_due_when_the_annex_i_rules_apply(self) -> None:
        """The recital's 'legal gap': a gap between these two dates would be one."""
        assert _dated("Article 8, the paragraph following the third") == _dated("point (c)(ii)")

    def test_the_delegation_runs_from_the_day_the_amending_act_took_effect(self) -> None:
        assert _dated("Article 47(2)") == _dated("2026/1744, Article 4")

    def test_the_machinery_regulation_applies_before_its_ai_requirements_do(self) -> None:
        """A machine placed on the market in 2027 meets 2023/1230 with no AI annex yet."""
        assert mr.APPLICATION_DATE < _dated("Article 8, the paragraph following the third")


class TestTheNarrowingIsQuotedWhole:
    def test_the_carve_out_is_quoted_with_the_paragraph_that_limits_it(self) -> None:
        citations = [c.citation for c in ai.CLAUSES]

        assert any("Article 6(1a)" in c for c in citations)
        assert any("Article 6(1b)" in c for c in citations)

    def test_a_system_whose_failure_endangers_safety_is_still_a_safety_component(self) -> None:
        (limit,) = [c for c in ai.CLAUSES if "Article 6(1b)" in c.citation]

        assert "would endanger health and safety shall qualify as safety components" in limit.quote


class TestWhatWasNotFoundStaysNotFound:
    def test_the_single_conformity_assessment_claim_is_not_a_provision(self) -> None:
        assert any("one conformity assessment" in claim for claim in ai.NOT_FOUND)
        for text in [p.quote for p in ai.PROVISIONS] + [c.quote for c in ai.CLAUSES]:
            assert "one conformity assessment" not in text


class TestTheRegisterExpires:
    def test_the_review_date_is_after_the_reading_and_before_the_next_deadline(self) -> None:
        assert ai.OJ_2026_1744.read_on < ai.REVIEW_BY < _dated("point (c)(i)")

    def test_the_boundary_day_is_still_current_and_the_day_after_is_not(self) -> None:
        assert ai.is_current(ai.REVIEW_BY)
        assert not ai.is_current(ai.REVIEW_BY + datetime.timedelta(days=1))

    def test_the_register_has_been_read_recently_enough_to_be_believed(self) -> None:
        """Fails on purpose after REVIEW_BY. The fix is to re-read EUR-Lex, update
        what moved in `app/compliance/eu_ai_act.py`, and set a new date — never to
        move the date alone."""
        assert ai.is_current(datetime.date.today()), (
            f"The AI Act register was last read on {ai.OJ_2026_1744.read_on} and was due to be "
            f"re-read by {ai.REVIEW_BY}. Re-read Regulation (EU) 2024/1689 as amended on EUR-Lex."
        )
