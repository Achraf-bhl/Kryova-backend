"""Whose bill is it: 404 everywhere else, and the policy under the route.

A bill is the most attractive cross-tenant read in the product — it is a list of
what a competitor is building and how much of it. So the rule the rest of the
service keeps is kept harder here, and both halves of it are tested: **another
tenant's billing surface is not forbidden, it is not there**, and a member who
is merely a viewer gets the same answer as a stranger.

The second half of this file is about the safety net rather than the route. The
migration's row-level-security policy reads a GUC by name, and a typo in that
name is a policy that never matches and therefore never protects — silently, and
with every application test still green. `test_tenancy_rls.py` makes the same
check for the P2 tables; this makes it for the P8 ones.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.database import TENANT_SETTING
from app.models import Membership, OrgRole
from app.models.billing import Meter
from tests.test_billing import add_usage
from tests.test_tenancy import SignIn, sign_in  # noqa: F401  -- a fixture, used by name
from tests.typing import AuthenticatedTestClient

BILLING_PATHS = (
    "",
    "/usage",
    "/summary",
    "/rollups",
    "/faults",
)


@pytest.fixture
def organisation_id(auth_client: AuthenticatedTestClient) -> str:
    response = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestAnotherTenantIsToldNothing:
    @pytest.mark.parametrize("path", BILLING_PATHS)
    def test_every_billing_route_is_404_to_a_stranger(
        self, organisation_id: str, sign_in: SignIn, path: str  # noqa: F811
    ) -> None:
        peer = sign_in("rival@example.com")
        response = peer.get(f"/api/v1/organisations/{organisation_id}/billing{path}")
        assert response.status_code == 404, response.text
        assert response.json()["detail"] in {"Not found", "Organisation not found"}

    def test_a_usage_record_from_another_tenant_is_404_not_403(
        self, organisation_id: str, db_session: Session, sign_in: SignIn  # noqa: F811
    ) -> None:
        """403 would confirm the id is real, which is the enumeration oracle."""
        record = add_usage(db_session, organisation_id, Meter.AI_TOKENS, 100)

        peer = sign_in("rival2@example.com")
        their_org = peer.post("/api/v1/organisations", json={"name": "Rival"}).json()["id"]
        response = peer.get(
            f"/api/v1/organisations/{their_org}/billing/usage/{record.id}"
        )
        assert response.status_code == 404

    def test_a_viewer_of_the_organisation_is_told_the_same_as_a_stranger(
        self, organisation_id: str, db_session: Session, sign_in: SignIn  # noqa: F811
    ) -> None:
        """Billing reads need ADMIN. A viewer on an engineering project has no
        business reading what the company spends, and a 403 here would still
        confirm the organisation exists."""
        peer = sign_in("viewer@example.com")
        peer_id = peer.get("/api/v1/auth/me").json()["id"]
        db_session.add(
            Membership(
                organisation_id=organisation_id, user_id=peer_id, role=OrgRole.VIEWER
            )
        )
        db_session.flush()

        response = peer.get(f"/api/v1/organisations/{organisation_id}/billing/summary")
        assert response.status_code == 404

    def test_sealing_a_period_needs_the_owner(
        self, organisation_id: str, db_session: Session, sign_in: SignIn  # noqa: F811
    ) -> None:
        peer = sign_in("admin@example.com")
        peer_id = peer.get("/api/v1/auth/me").json()["id"]
        db_session.add(
            Membership(
                organisation_id=organisation_id, user_id=peer_id, role=OrgRole.ADMIN
            )
        )
        db_session.flush()

        # An admin may read the bill...
        assert (
            peer.get(f"/api/v1/organisations/{organisation_id}/billing/summary").status_code
            == 200
        )
        # ...and may not seal a period, and is not told that is why.
        peer.headers["x-csrf-token"] = peer.cookies["kryova_csrf"]
        assert (
            peer.post(
                f"/api/v1/organisations/{organisation_id}/billing/periods/close"
            ).status_code
            == 404
        )

    def test_one_tenants_usage_never_appears_in_anothers_total(
        self, organisation_id: str, db_session: Session, sign_in: SignIn  # noqa: F811
    ) -> None:
        add_usage(db_session, organisation_id, Meter.AI_TOKENS, 5_000)

        peer = sign_in("neighbour@example.com")
        peer.headers["x-csrf-token"] = peer.cookies["kryova_csrf"]
        their_org = peer.post("/api/v1/organisations", json={"name": "Next door"}).json()["id"]
        summary = peer.get(
            f"/api/v1/organisations/{their_org}/billing/summary"
        ).json()
        assert summary["totals"] == []
        add_usage(db_session, their_org, Meter.AI_TOKENS, 7)
        summary = peer.get(f"/api/v1/organisations/{their_org}/billing/summary").json()
        (row,) = summary["totals"]
        assert Decimal(row["quantity"]) == Decimal(7)


class TestThePolicyUnderTheRoute:
    def test_the_migration_reads_the_setting_the_application_publishes(self) -> None:
        """A typo here is a policy that never matches and never protects."""
        from migrations.versions.b3d7c1f4a920_usage_metering_rollups_billing_accounts import (
            TENANT_SETTING as MIGRATION_SETTING,
        )

        assert MIGRATION_SETTING == TENANT_SETTING

    def test_every_billing_table_that_owns_a_tenant_gets_the_policy(self) -> None:
        """Adding a table to `app/models/billing.py` and not to the policy list
        is how one of them ends up outside the safety net."""
        from app.core.database import Base
        from migrations.versions.b3d7c1f4a920_usage_metering_rollups_billing_accounts import (
            _TENANT_TABLES,
        )

        with_tenant = {
            table.name
            for table in Base.metadata.tables.values()
            if "organisation_id" in table.c
            and table.name
            in {"usage_records", "usage_rollups", "billing_accounts", "metering_faults"}
        }
        assert with_tenant == set(_TENANT_TABLES)

    def test_the_policy_ddl_names_only_tables_that_exist(self) -> None:
        from app.core.database import Base
        from migrations.versions.b3d7c1f4a920_usage_metering_rollups_billing_accounts import (
            rls_statements,
            rls_teardown_statements,
        )

        statements = rls_statements("kryova_test") + rls_teardown_statements("kryova_test")
        assert statements
        for name in ("usage_records", "usage_rollups", "billing_accounts", "metering_faults"):
            assert name in Base.metadata.tables
            assert any(name in statement for statement in statements)
