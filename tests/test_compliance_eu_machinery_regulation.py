"""The Machinery Regulation's dated register says nothing it cannot cite —
master plan E19 task 1.

The failure this guards is the one the task was written to catch: two published
dates for one event. Both turned out to be in the Official Journal, so these
tests pin the properties that keep them apart — every date carries the clause
and words it was read from, a corrected date keeps the date it replaced and
names the corrigendum item, and the date in force is the corrected one. They do
not test the dates as facts about the world; the quotes are what a reader checks.
"""

from __future__ import annotations

import datetime

import pytest

from app.compliance import eu_machinery_regulation as mr
from app.compliance.provisions import DatedProvision, LegalBasis, Source, SourceKind, spelled

SECONDARY = Source(
    id="someone", kind=SourceKind.SECONDARY, document="A compliance blog",
    url="https://example.invalid", read_on=datetime.date(2026, 9, 14),
)


class TestAProvisionCarriesItsPedigree:
    def test_a_provision_with_no_sources_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no source"):
            DatedProvision(
                event="made up", date=datetime.date(2027, 1, 20),
                basis=LegalBasis.SECONDARY_CORROBORATED, sources=(),
            )

    def test_the_primary_text_cannot_be_claimed_from_a_blog(self) -> None:
        with pytest.raises(ValueError, match="no Official Journal"):
            DatedProvision(
                event="applies", date=datetime.date(2027, 1, 20), basis=LegalBasis.PRIMARY_TEXT,
                sources=(SECONDARY,), citation="Article 54", quote="It shall apply from 20 January 2027.",
            )

    def test_the_primary_text_cannot_be_claimed_without_the_words_read(self) -> None:
        with pytest.raises(ValueError, match="clause and the words"):
            DatedProvision(
                event="applies", date=datetime.date(2027, 1, 20), basis=LegalBasis.PRIMARY_TEXT,
                sources=(mr.OJ_L_165,), citation="Article 54",
            )

    def test_a_date_its_own_quote_does_not_state_is_refused(self) -> None:
        """The remembered-date failure, caught at construction: 14 typed, 20 read."""
        with pytest.raises(ValueError, match="does not say so"):
            DatedProvision(
                event="applies", date=datetime.date(2027, 1, 14), basis=LegalBasis.PRIMARY_TEXT,
                sources=(mr.OJ_L_165,), citation="Article 54, second paragraph",
                quote="It shall apply from 20 January 2027.",
            )

    @pytest.mark.parametrize(
        ("as_published", "correction"),
        [(datetime.date(2027, 1, 14), ""), (None, "Corrigendum, item 10")],
    )
    def test_a_correction_names_what_it_moved_and_from_where(self, as_published: datetime.date | None, correction: str) -> None:
        with pytest.raises(ValueError, match="both or neither"):
            DatedProvision(
                event="applies", date=datetime.date(2027, 1, 20), basis=LegalBasis.PRIMARY_TEXT,
                sources=(mr.OJ_L_165,), citation="Article 54, second paragraph",
                quote="It shall apply from 20 January 2027.", as_published=as_published, correction=correction,
            )

    def test_a_correction_that_moved_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError, match="changed nothing"):
            DatedProvision(
                event="applies", date=datetime.date(2027, 1, 20), basis=LegalBasis.PRIMARY_TEXT,
                sources=(mr.OJ_L_165,), citation="Article 54, second paragraph",
                quote="It shall apply from 20 January 2027.",
                as_published=datetime.date(2027, 1, 20), correction="Corrigendum, item 10",
            )

    def test_the_official_journal_spells_its_dates_without_a_leading_zero(self) -> None:
        assert spelled(datetime.date(2023, 7, 4)) == "4 July 2023"


class TestEveryDateIsReadFromTheOfficialJournal:
    def test_every_provision_is_primary_text(self) -> None:
        for provision in mr.PROVISIONS:
            assert provision.basis is LegalBasis.PRIMARY_TEXT, provision.event
            assert mr.OJ_L_165 in provision.sources, provision.event

    def test_every_corrected_date_cites_the_corrigendum_it_came_from(self) -> None:
        corrected = [p for p in mr.PROVISIONS if p.as_published is not None]

        assert len(corrected) == 8
        for provision in corrected:
            assert mr.CORRIGENDUM in provision.sources, provision.event
            assert provision.correction.startswith("Corrigendum, OJ L 169, 4.7.2023, p. 35, item ")

    def test_every_correction_moves_a_date_six_days_but_one(self) -> None:
        """A typo in either date shows up here as a gap that is not six days."""
        gaps = {p.citation: (p.date - p.as_published).days for p in mr.PROVISIONS if p.as_published}

        assert {citation for citation, days in gaps.items() if days != 6} == {
            "Article 54, third paragraph, point (b)"
        }
        assert mr.provision("Article 54, third paragraph, point (b)").as_published == datetime.date(2023, 10, 14)

    def test_the_sources_are_the_urls_that_were_read(self) -> None:
        assert mr.OJ_L_165.url.endswith("CELEX:32023R1230")
        assert mr.CORRIGENDUM.url.endswith("CELEX:32023R1230R(01)")


class TestTheDisputedDateIsSettled:
    """Two sources disagreed: 14 or 20 January 2027, one calling the other a
    widely repeated error. Both are Official Journal text."""

    def test_the_regulation_applies_from_the_corrected_date(self) -> None:
        assert mr.APPLICATION_DATE == datetime.date(2027, 1, 20)

    def test_the_published_date_is_kept_beside_it(self) -> None:
        applies = mr.provision("Article 54, second paragraph")

        assert applies.as_published == datetime.date(2027, 1, 14)
        assert applies.correction.endswith("item 10")

    def test_no_date_in_force_is_a_published_one_the_corrigendum_replaced(self) -> None:
        replaced = {p.as_published for p in mr.PROVISIONS if p.as_published}

        assert not replaced & {p.date for p in mr.PROVISIONS}

    def test_the_directive_is_repealed_on_the_day_the_regulation_applies(self) -> None:
        """A gap or an overlap between the two would leave machinery under neither
        or both, and no clause read says either happens."""
        assert mr.provision("Article 51(2), first subparagraph").date == mr.APPLICATION_DATE
        assert mr.provision("Article 52(1), first sentence").date == mr.APPLICATION_DATE

    def test_entry_into_force_is_the_twentieth_day_after_publication(self) -> None:
        assert mr.ENTRY_INTO_FORCE == datetime.date(2023, 7, 19)
        assert "twentieth day following" in mr.provision("Article 54, first paragraph").quote
        assert mr.provision("Article 54, third paragraph, point (c)").date == mr.ENTRY_INTO_FORCE

    def test_a_clause_the_register_does_not_carry_is_not_guessed(self) -> None:
        with pytest.raises(LookupError, match="Article 51\\(1\\)"):
            mr.provision("Article 51(1)")
