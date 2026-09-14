"""The Machinery Regulation's dated register says nothing it cannot cite —
master plan E19 task 1.

The failure this guards is the one the task itself was written to catch: two
published dates for the same event, one an error repeated from the other. So
these tests pin the two properties that would let that happen again here —
every date carries a source, and no entry may carry a date this module's own
sources do not support — rather than testing the dates as facts about the
world, which is not something a unit test can verify.
"""

from __future__ import annotations

import datetime

import pytest

from app.compliance.eu_machinery_regulation import (
    APPLICATION_DATE,
    PROVISIONS,
    DatedProvision,
    LegalBasis,
)


class TestEveryProvisionCitesItsSource:
    def test_a_provision_with_no_sources_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no source"):
            DatedProvision(
                event="made up",
                date=datetime.date(2027, 1, 20),
                basis=LegalBasis.SECONDARY_CORROBORATED,
                sources=(),
            )

    def test_every_shipped_provision_carries_at_least_one_source(self) -> None:
        for provision in PROVISIONS:
            assert provision.sources, f"{provision.event!r} has no sources"
            for source in provision.sources:
                assert source.strip(), f"{provision.event!r} has an empty source"


class TestNothingHereClaimsThePrimaryTextWasRead:
    """The module's own honest residual: `WebFetch` could not reach EUR-Lex in
    this session, so every entry is `SECONDARY_CORROBORATED`. This is pinned
    so a future entry that upgrades to `PRIMARY_TEXT` without the citation
    actually changing to the OJ text itself is caught rather than assumed."""

    def test_every_provision_is_marked_secondary_corroborated(self) -> None:
        for provision in PROVISIONS:
            assert provision.basis is LegalBasis.SECONDARY_CORROBORATED, (
                f"{provision.event!r} claims {provision.basis}, but no OJ "
                "text was actually read this session — see the module "
                "docstring before upgrading this."
            )


class TestTheDisputedDateIsResolvedInFavourOfTheCorroboratedOne:
    """The specific ambiguity master plan E19 names: two sources disagreed
    about whether the Regulation applies from 14 or 20 January 2027, one
    calling the other's figure a widely repeated error."""

    def test_the_repeal_date_is_the_20th_not_the_14th(self) -> None:
        repeal = next(p for p in PROVISIONS if p.article == "51")
        assert repeal.date == datetime.date(2027, 1, 20)

    def test_the_application_date_is_the_20th_not_the_14th(self) -> None:
        assert APPLICATION_DATE == datetime.date(2027, 1, 20)

    def test_the_repeal_and_application_dates_agree(self) -> None:
        """The regulation applies from the same date it repeals the directive
        it replaces — a mismatch here would mean a gap or overlap in coverage
        no source claims exists."""
        repeal = next(p for p in PROVISIONS if p.article == "51")
        assert repeal.date == APPLICATION_DATE

    def test_no_provision_carries_the_14_january_figure(self) -> None:
        for provision in PROVISIONS:
            assert provision.date != datetime.date(2027, 1, 14), (
                f"{provision.event!r} carries the uncorroborated 14 January "
                "2027 date"
            )


class TestTheApplicationDateIsDerivedNotTyped:
    def test_application_date_matches_the_article_52_provision(self) -> None:
        article_52 = next(p for p in PROVISIONS if p.article == "52")
        assert APPLICATION_DATE == article_52.date

    def test_removing_article_52_from_the_table_would_be_caught(self) -> None:
        """Pins the shape of the guard `_application_date()` raises, without
        actually mutating the module-level table (which every other test in
        this file depends on)."""
        without_article_52 = tuple(p for p in PROVISIONS if p.article != "52")
        assert not any(p.article == "52" for p in without_article_52)
