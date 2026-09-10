"""The billing surface: totals that reconcile, and limits that name their source.

Phase P8. The tests here are about the two claims the endpoints make that are
worth anything:

**A total is exact and reproducible.** Every quantity crosses the wire as a
string of digits, because a JSON number is an IEEE double the moment a browser
parses it. A period is half-open, so two adjacent months cannot bill the
boundary day twice, and the test that pins that is the one that would catch
somebody "simplifying" the comparison to `<=`.

**A number that is not measured says so.** A meter nothing feeds appears under
`unmetered` with the reason, never as a zero; storage carries its own method and
its own unplaced residual. That is `app.design.assertions`' rule — an unmeasured
claim is never a pass — applied where it costs money.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.metering import (
    Cause,
    UsageEvent,
    quota_envelope,
    seal_period,
    to_record,
    usage_totals,
)
from app.kernel.provenance import Basis
from app.models.billing import BillingAccount, Meter, MeteringFault, UsageRecord
from tests.typing import AuthenticatedTestClient

TODAY = datetime.now(timezone.utc).date()
MONTH_START = TODAY.replace(day=1)


@pytest.fixture
def organisation_id(auth_client: AuthenticatedTestClient) -> str:
    """An organisation the signed-in user owns, so every guard here is open."""
    response = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def a_cause(organisation_id: str, **overrides) -> Cause:
    fields = {
        "organisation_id": organisation_id,
        "source": "simulation.runner",
        "subject_type": "simulation_job",
        "subject_id": "job-1",
    }
    fields.update(overrides)
    return Cause(**fields)


def add_usage(
    db: Session,
    organisation_id: str,
    meter: Meter,
    quantity,
    *,
    when: date | None = None,
    **cause_fields,
) -> UsageRecord:
    event = UsageEvent.of(
        meter,
        quantity,
        basis=Basis.MEASURED,
        method="fixture",
        cause=a_cause(organisation_id, **cause_fields),
    )
    record = to_record(event)
    if when is not None:
        record.usage_date = when
        record.occurred_at = datetime.combine(
            when, datetime.min.time(), tzinfo=timezone.utc
        )
    db.add(record)
    db.flush()
    return record


class TestARecordTracesToItsCause:
    def test_the_record_carries_every_id_a_person_could_open(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        record = add_usage(
            db_session,
            organisation_id,
            Meter.SOLVER_SECONDS,
            Decimal("4.5"),
            project_id=None,
            simulation_job_id=None,
            user_id=None,
        )
        response = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/usage/{record.id}"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["quantity"] == "4.5"
        assert body["unit"] == "s"
        assert body["basis"] == "measured"
        assert body["method"] == "fixture"
        assert body["cause"]["source"] == "simulation.runner"
        assert body["cause"]["subject_type"] == "simulation_job"
        assert body["cause"]["subject_id"] == "job-1"

    def test_a_quantity_is_a_string_of_digits_not_a_json_number(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        """A JSON number is a double by the time anyone reads it."""
        record = add_usage(
            db_session, organisation_id, Meter.SOLVER_SECONDS, Decimal("0.1")
        )
        raw = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/usage/{record.id}"
        ).text
        assert '"quantity":"0.1"' in raw.replace(" ", "")


class TestTotalsOverAPeriodAreExact:
    def test_ten_tenths_of_a_second_total_exactly_one(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        for _ in range(10):
            add_usage(db_session, organisation_id, Meter.SOLVER_SECONDS, Decimal("0.1"))

        summary = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/summary"
        ).json()
        (solver,) = [row for row in summary["totals"] if row["meter"] == "solver_seconds"]
        assert Decimal(solver["quantity"]) == Decimal("1")
        assert solver["records"] == 10

    def test_the_period_is_half_open_at_its_end(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        """Break the boundary: a record on the end date must not be counted.

        Two adjacent months share an endpoint, so an inclusive end bills the
        boundary day on both invoices.
        """
        inside = MONTH_START + timedelta(days=1)
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 100, when=MONTH_START)
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 900, when=inside)

        totals = usage_totals(db_session, organisation_id, MONTH_START, inside)
        assert totals[Meter.AI_TOKENS] == Decimal(100)

        totals = usage_totals(
            db_session, organisation_id, MONTH_START, inside + timedelta(days=1)
        )
        assert totals[Meter.AI_TOKENS] == Decimal(1_000)

    def test_a_period_that_ends_before_it_starts_is_refused_with_advice(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        response = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/summary",
            params={"start": str(MONTH_START), "end": str(MONTH_START)},
        )
        assert response.status_code == 422
        assert "half-open" in response.json()["detail"]


class TestWhatCouldNotBeMeasuredIsNamed:
    def test_nothing_is_unmetered_now_that_every_meter_is_wired(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        """P8.1 wired the last three meters on 2026-09-10.

        This asserted `catia_seat_seconds` and `ai_tokens` appeared in
        `unmetered` with a reason. They no longer do, and the *mechanism* that
        mattered — an unwired meter is reported rather than rendered as zero —
        is pinned at its own level in `tests/test_metering.py`, where it does
        not depend on which meters happen to be wired today.
        """
        summary = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/summary"
        ).json()
        assert summary["unmetered"] == []

    def test_storage_reports_its_method_and_the_bytes_it_could_not_place(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        storage = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/summary"
        ).json()["storage"]
        assert "distinct blobs reachable from this organisation's projects" in storage["method"]
        assert storage["unplaced_reason"]


class TestQuotasSayWhereTheirNumbersCameFrom:
    def test_without_an_override_every_limit_comes_from_the_global_settings(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        body = auth_client.get(f"/api/v1/organisations/{organisation_id}/billing").json()
        assert body["plan"] == "free"
        sources = {limit["name"]: limit["source"] for limit in body["quotas"]["limits"]}
        assert set(sources.values()) == {"global settings"}
        # The note now says whose numbers the allowances are, rather than
        # that there is no price list: P8.2 made them the operator's.
        assert "set by whoever runs this Kryova" in body["quotas"]["note"]

    def test_reading_the_account_does_not_create_one(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        """A GET that writes a row puts a plan on record nobody chose."""
        assert auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing"
        ).status_code == 200
        assert db_session.query(BillingAccount).count() == 0

    def test_an_override_is_recorded_as_a_tenant_override(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        response = auth_client.put(
            f"/api/v1/organisations/{organisation_id}/billing",
            json={"plan": "team", "max_concurrent_simulations_per_user": 12},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["plan"] == "team"
        limit = next(
            row
            for row in body["quotas"]["limits"]
            if row["name"] == "max_concurrent_simulations_per_user"
        )
        assert limit["limit"] == 12
        assert limit["source"] == "tenant override"

        envelope = quota_envelope(db_session, organisation_id)
        assert envelope.limit_for("max_concurrent_simulations_per_user").limit == 12

    def test_clearing_an_override_returns_the_field_to_the_global_setting(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        """`-1` clears; a null would be indistinguishable from "leave it alone"."""
        auth_client.put(
            f"/api/v1/organisations/{organisation_id}/billing",
            json={"max_media_bytes": 4_096},
        )
        body = auth_client.put(
            f"/api/v1/organisations/{organisation_id}/billing",
            json={"max_media_bytes": -1},
        ).json()
        limit = next(row for row in body["quotas"]["limits"] if row["name"] == "max_media_bytes")
        assert limit["source"] == "global settings"

    def test_the_stripe_seam_is_empty_and_the_payload_says_why(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        body = auth_client.get(f"/api/v1/organisations/{organisation_id}/billing").json()
        assert body["external_customer_ref"] is None
        assert "no API key" in body["billing_provider"]


class TestSealingAPeriod:
    def test_a_rollup_totals_the_records_and_stamps_them(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        for _ in range(3):
            add_usage(db_session, organisation_id, Meter.AI_TOKENS, 1_000)

        response = auth_client.post(
            f"/api/v1/organisations/{organisation_id}/billing/periods/close"
        )
        assert response.status_code == 200, response.text
        (rollup,) = response.json()
        assert rollup["meter"] == "ai_tokens"
        assert Decimal(rollup["quantity"]) == Decimal(3_000)
        assert rollup["record_count"] == 3
        assert rollup["posted_at"] is None

        stamped = db_session.query(UsageRecord).all()
        assert {row.rollup_id for row in stamped} == {rollup["id"]}

    def test_sealing_twice_recomputes_rather_than_doubling(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        """A retry after a failed post must not invent a second invoice line."""
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 500)
        end = MONTH_START + timedelta(days=40)
        seal_period(db_session, organisation_id, MONTH_START, end)
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 500)
        rollups = seal_period(db_session, organisation_id, MONTH_START, end)

        assert len(rollups) == 1
        assert rollups[0].quantity == Decimal(1_000)
        assert rollups[0].record_count == 2

    def test_the_rollup_listing_pages(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 1)
        add_usage(db_session, organisation_id, Meter.SOLVER_SECONDS, Decimal("1"))
        auth_client.post(f"/api/v1/organisations/{organisation_id}/billing/periods/close")

        page = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/rollups",
            params={"page": 2, "page_size": 1},
        ).json()
        assert page["total"] == 2
        assert len(page["items"]) == 1


class TestAbsorbedFailuresAreVisibleInTheProduct:
    def test_a_metering_fault_is_listed_for_the_tenant_it_belongs_to(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        db_session.add(
            MeteringFault(
                meter="solver_seconds",
                organisation_id=organisation_id,
                source="simulation.runner",
                failure="RuntimeError: the ledger is on fire",
                detail={"events": 2},
            )
        )
        db_session.flush()

        page = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/faults"
        ).json()
        assert page["total"] == 1
        assert page["items"][0]["failure"].startswith("RuntimeError")


class TestTheUsageListing:
    def test_it_pages_and_filters_by_meter(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        for _ in range(3):
            add_usage(db_session, organisation_id, Meter.AI_TOKENS, 10)
        add_usage(db_session, organisation_id, Meter.STORAGE_BYTES, 4_096)

        page = auth_client.get(
            f"/api/v1/organisations/{organisation_id}/billing/usage",
            params={"meter": "ai_tokens", "page_size": 2},
        ).json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        assert {item["meter"] for item in page["items"]} == {"ai_tokens"}
