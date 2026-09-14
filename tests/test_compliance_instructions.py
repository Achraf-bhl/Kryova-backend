"""Digital instructions meet Article 10(7), or the checker names the words they
fail — master plan E19 task 4."""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from app.compliance import instructions as ins
from app.compliance.eu_machinery_regulation import OJ_L_165
from app.compliance.instructions import DigitalDelivery, Marking, PaperOffer

PLACED = datetime.date(2027, 3, 1)

COMPLIANT = DigitalDelivery(
    product_model="KP-200 stamping press",
    model_named_in_instructions="KP-200 stamping press",
    marking=Marking.ON_THE_MACHINE,
    marking_on_machine_impossible_because="",
    printable=True,
    downloadable=True,
    savable_on_a_device=True,
    embedded_in_machine_software=True,
    embedded_copy_printable_downloadable_savable=True,
    placed_on_market=PLACED,
    expected_end_of_life=datetime.date(2047, 3, 1),
    online_until=datetime.date(2047, 3, 1),
    paper=PaperOffer(free_of_charge=True, delivered_within_days=14),
)


def _with(**changes: object) -> DigitalDelivery:
    return dataclasses.replace(COMPLIANT, **changes)  # type: ignore[arg-type]


def _citations(delivery: DigitalDelivery) -> list[str]:
    return [u.clause.citation for u in ins.unmet(delivery)]


class TestTheClausesAreRead:
    def test_every_condition_is_quoted_from_the_official_journal(self) -> None:
        for clause in (ins.ACCOMPANIED, ins.MARKED, ins.PRINTABLE, ins.ONLINE, ins.PAPER):
            assert OJ_L_165 in clause.sources

    def test_paper_is_at_purchase_free_and_within_a_month(self) -> None:
        assert "at the time of the purchase" in ins.PAPER.quote
        assert "free of charge within one month" in ins.PAPER.quote


class TestAPlanThatMeetsEveryConditionPasses:
    def test_nothing_unmet(self) -> None:
        assert ins.unmet(COMPLIANT) == ()


class TestHowLongTheyStayOnline:
    def test_a_long_lived_machine_is_owed_its_lifetime(self) -> None:
        assert ins.required_online_until(PLACED, datetime.date(2050, 1, 1)) == datetime.date(2050, 1, 1)

    def test_a_short_lived_machine_is_still_owed_ten_years(self) -> None:
        assert ins.required_online_until(PLACED, datetime.date(2030, 1, 1)) == datetime.date(2037, 3, 1)

    def test_ten_years_from_a_leap_day_is_not_cut_short(self) -> None:
        assert ins.years_after(datetime.date(2028, 2, 29), 10) == datetime.date(2038, 3, 1)

    def test_online_for_ten_years_is_not_enough_for_a_twenty_year_machine(self) -> None:
        delivery = _with(online_until=datetime.date(2037, 3, 1))

        (only,) = ins.unmet(delivery)
        assert only.clause is ins.ONLINE
        assert "2047-03-01" in only.because

    def test_no_end_date_is_unmet(self) -> None:
        assert _citations(_with(online_until=None)) == [ins.ONLINE.citation]


class TestEachConditionIsNamedWhenItFails:
    @pytest.mark.parametrize(
        ("changes", "clause"),
        [
            ({"model_named_in_instructions": "KP-100"}, ins.ACCOMPANIED),
            ({"marking": None}, ins.MARKED),
            ({"marking": Marking.ON_THE_PACKAGING}, ins.MARKED),
            ({"printable": False}, ins.PRINTABLE),
            ({"downloadable": False}, ins.PRINTABLE),
            ({"savable_on_a_device": False}, ins.PRINTABLE),
            ({"embedded_copy_printable_downloadable_savable": False}, ins.PRINTABLE),
            ({"paper": None}, ins.PAPER),
            ({"paper": PaperOffer(free_of_charge=False, delivered_within_days=14)}, ins.PAPER),
            ({"paper": PaperOffer(free_of_charge=True, delivered_within_days=45)}, ins.PAPER),
        ],
    )
    def test_the_failing_clause_is_the_one_cited(self, changes: dict[str, object], clause: object) -> None:
        assert _citations(_with(**changes)) == [clause.citation]  # type: ignore[attr-defined]

    def test_packaging_is_allowed_where_the_machine_cannot_be_marked_and_says_why(self) -> None:
        delivery = _with(
            marking=Marking.ON_THE_PACKAGING,
            marking_on_machine_impossible_because="The part is a 4 mm bearing shim.",
        )

        assert ins.unmet(delivery) == ()

    def test_an_embedded_copy_is_not_checked_when_nothing_is_embedded(self) -> None:
        delivery = _with(embedded_in_machine_software=False, embedded_copy_printable_downloadable_savable=False)

        assert ins.unmet(delivery) == ()

    def test_every_failure_reads_as_a_sentence_with_its_clause(self) -> None:
        (failure,) = ins.unmet(_with(paper=None))

        assert failure.sentence().startswith("Article 10(7), third subparagraph: ")
