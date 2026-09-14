"""A change before placing on the market reads differently from one after —
master plan E19 task 5, the legal half. The product half is in
`tests/test_designs.py::TestPlacingOnTheMarket`."""

from __future__ import annotations

import datetime

import pytest

from app.compliance import modification as mod
from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.modification import LegalCharacter

PLACED = datetime.date(2027, 3, 1)


class TestTheCharacterOfAChange:
    def test_nothing_recorded_is_design_time(self) -> None:
        assert mod.character(None, datetime.date(2030, 1, 1)) is LegalCharacter.DESIGN_TIME

    def test_the_day_before_placing_is_design_time(self) -> None:
        assert mod.character(PLACED, PLACED - datetime.timedelta(days=1)) is LegalCharacter.DESIGN_TIME

    def test_the_day_of_placing_is_already_after(self) -> None:
        """The one ambiguous day does not get the more comfortable answer."""
        assert mod.character(PLACED, PLACED) is LegalCharacter.AFTER_PLACING_ON_MARKET


class TestTheNoticeSaysWhatItCannotTell:
    def test_after_placing_it_names_the_date_and_both_legal_acts(self) -> None:
        notice = mod.notice(PLACED, datetime.date(2028, 1, 1))

        assert "1 March 2027" in notice.headline
        assert "not a design-time change" in notice.headline
        assert "Article 10(4)" in notice.detail
        assert "substantial modification (Article 3(16))" in notice.detail
        assert "considered the manufacturer" in notice.detail
        assert "cannot tell which" in notice.detail

    def test_after_placing_it_carries_the_clauses_it_rests_on(self) -> None:
        notice = mod.notice(PLACED, datetime.date(2028, 1, 1))

        assert [c.citation for c in notice.clauses] == ["Article 10(4)", "Article 3(16)", "Article 18"]
        for clause in notice.clauses:
            assert OJ_L_165 in clause.sources

    def test_design_time_carries_no_clauses_and_says_how_it_would_change(self) -> None:
        notice = mod.notice(None, datetime.date(2028, 1, 1))

        assert notice.character is LegalCharacter.DESIGN_TIME
        assert notice.clauses == ()
        assert "record the date" in notice.detail

    def test_the_definition_is_quoted_with_its_conditions(self) -> None:
        assert "by physical or digital means" in mod.ARTICLE_3_16.quote
        assert "not foreseen or planned by the manufacturer" in mod.ARTICLE_3_16.quote
        assert "shall be considered to be a manufacturer" in mod.ARTICLE_18.quote

    def test_it_serialises_for_the_api(self) -> None:
        payload = mod.notice(PLACED, PLACED).to_dict()

        assert payload["character"] == "after-placing-on-market"
        assert payload["citations"] == ["Article 10(4)", "Article 3(16)", "Article 18"]


class TestAPlacingIsSomethingThatHappened:
    def test_a_future_date_is_refused(self) -> None:
        with pytest.raises(ValueError, match="has not happened yet"):
            mod.refuse_a_future_date(datetime.date(2027, 3, 2), today=datetime.date(2027, 3, 1))

    def test_today_is_accepted(self) -> None:
        mod.refuse_a_future_date(PLACED, today=PLACED)
