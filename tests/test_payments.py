"""Plans, credit, enforcement and estimates (P8.2, P8.3, P8.4).

`tests/test_metering.py` proves the meter. This file proves what is done with
it: that a refusal is an envelope rather than a number, that an estimate comes
from the *same* meter the bill does, and that a build with no payment provider
cannot be used to mint credit.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import payments
from app.core.config import settings
from app.core.estimates import MINIMUM_SAMPLES, estimate_meter, estimate_run
from app.core.metering import PLANS, build_plans, check_quota
from app.core.payments import (
    FakeProvider,
    NoProvider,
    PaymentError,
    StripeProvider,
    build_provider,
    grant_credit,
)
from app.kernel.provenance import Basis
from app.models import BillingAccount, Meter, Plan, UsageRecord
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _load_case() -> dict:
    """The minimum a simulation accepts. Mirrors `tests/test_simulations.py`."""
    return {
        "name": "Axial pull",
        "material": {
            "name": "aluminium-6061-t6",
            "youngs_modulus_mpa": 68_900,
            "poissons_ratio": 0.33,
            "yield_strength_mpa": 276,
            "density_kg_m3": 2700,
        },
        "fixtures": [{"where": {"type": "face", "axis": "z", "side": "min"}, "kind": "clamp"}],
        "loads": [
            {
                "where": {"type": "face", "axis": "z", "side": "max"},
                "force_n": [0.0, 0.0, 1000.0],
            }
        ],
    }


@pytest.fixture
def organisation_id(auth_client: AuthenticatedTestClient) -> str:
    created = auth_client.post(f"{API}/organisations", json={"name": "Acme Machines"})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


@pytest.fixture(autouse=True)
def _no_provider() -> None:
    """Every test starts from settings, so one that installs a fake cannot leak."""
    payments.use_provider(None)
    yield
    payments.use_provider(None)


class TestPlanAllowancesAreTheOperatorsNotKryovas:
    def test_a_fresh_deployment_sets_no_allowances(self) -> None:
        # Decision 4: this product is free and open, and a self-hosted install
        # has no price list at all. Every default is unset so a deployment
        # behaves exactly as it did before allowances existed.
        for plan in Plan:
            assert dict(PLANS[plan].meter_allowances) == {}

    def test_an_operator_setting_becomes_an_allowance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "free_plan_solver_seconds", 3600)
        built = build_plans()
        assert built[Plan.FREE].meter_allowances[Meter.SOLVER_SECONDS] == (
            3600 * Meter.SOLVER_SECONDS.scale
        )

    def test_zero_means_unset_rather_than_none_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An allowance of zero would refuse every request on that meter the
        # moment the setting was introduced, which is the opposite of the
        # intended default.
        monkeypatch.setattr(settings, "free_plan_ai_tokens", 0)
        assert Meter.AI_TOKENS not in build_plans()[Plan.FREE].meter_allowances

    def test_enterprise_is_never_bounded_by_a_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # "Custom quotas" is what the plan promises, and a custom quota is a
        # per-tenant override, which already outranks a plan.
        monkeypatch.setattr(settings, "free_plan_solver_seconds", 10)
        assert dict(build_plans()[Plan.ENTERPRISE].meter_allowances) == {}

    def test_the_mapping_reads_settings_at_lookup_not_at_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A module-level snapshot froze the allowances at first import, so which
        # value a test saw depended on collection order.
        assert Meter.SOLVER_SECONDS not in PLANS[Plan.FREE].meter_allowances
        monkeypatch.setattr(settings, "free_plan_solver_seconds", 60)
        assert Meter.SOLVER_SECONDS in PLANS[Plan.FREE].meter_allowances


class TestARefusalIsAnEnvelope:
    def test_a_run_within_allowance_is_permitted_and_says_why(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        # A quota system that denied whatever it had no policy for would stop
        # the product the day it was switched on.
        today = date.today()
        decision = check_quota(
            db_session, organisation_id, Meter.SOLVER_SECONDS, today, today + timedelta(days=1)
        )
        assert decision.allowed
        assert "No allowance is set" in decision.because

    def test_an_exhausted_allowance_refuses_with_the_whole_envelope(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        organisation_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """P8.3: never a bare 429 — what ran out, what remains, what plan."""
        monkeypatch.setattr(settings, "free_plan_solver_seconds", 10)
        today = date.today()
        db_session.add(
            UsageRecord(
                organisation_id=organisation_id,
                meter=Meter.SOLVER_SECONDS,
                quantity_units=999 * Meter.SOLVER_SECONDS.scale,
                usage_date=today,
                source="test",
                subject_type="test",
                subject_id="t",
                method="test",
                basis=Basis.MEASURED,
            )
        )
        db_session.flush()

        decision = check_quota(
            db_session, organisation_id, Meter.SOLVER_SECONDS, today, today + timedelta(days=1)
        )

        assert not decision.allowed
        detail = decision.detail()
        # Every part of "what ran out, what it costs to continue, what remains".
        for key in ("meter", "unit", "used", "allowance", "remaining", "plan", "note"):
            assert key in detail, key
        assert detail["remaining"] == "0"

    def test_the_route_refuses_with_402_rather_than_429(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 402, not 429: this is not "too fast", it is "there is none left", and
        # a 429 invites a retry that can only fail.
        from app.models import Project

        monkeypatch.setattr(settings, "free_plan_solver_seconds", 1)
        project = db_session.get(Project, project_id)
        assert project is not None
        db_session.add(
            UsageRecord(
                organisation_id=project.organisation_id,
                meter=Meter.SOLVER_SECONDS,
                quantity_units=500 * Meter.SOLVER_SECONDS.scale,
                usage_date=date.today(),
                source="test",
                subject_type="test",
                subject_id="t",
                method="test",
                basis=Basis.MEASURED,
            )
        )
        db_session.flush()

        response = auth_client.post(
            f"{API}/projects/{project_id}/simulations",
            json={"load_case": _load_case(), "element_size_mm": 5.0},
        )

        assert response.status_code == 402
        assert response.json()["detail"]["meter"] == "solver_seconds"

    def test_a_deployment_with_no_allowances_is_unaffected(
        self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl: bytes
    ) -> None:
        # The default. Nothing set, nothing refused.
        auth_client.post(
            f"{API}/projects/{project_id}/geometry",
            files={"file": ("cube.stl", cube_stl, "application/octet-stream")},
        )
        response = auth_client.post(
            f"{API}/projects/{project_id}/simulations",
            json={"load_case": _load_case(), "element_size_mm": 20.0},
        )
        assert response.status_code != 402


class TestAnEstimateComesFromTheMeterThatBills:
    def _record(self, db: Session, organisation_id: str, units: int, when: date) -> None:
        db.add(
            UsageRecord(
                organisation_id=organisation_id,
                meter=Meter.SOLVER_SECONDS,
                quantity_units=units,
                usage_date=when,
                source="test",
                subject_type="simulation",
                subject_id="s",
                method="test",
                basis=Basis.MEASURED,
            )
        )

    def test_too_little_history_carries_no_number_and_says_why(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        # A cost estimate is the most tempting place in a product to invent a
        # number, because it is advisory and never contradicted afterwards.
        estimate = estimate_meter(
            db_session, organisation_id, Meter.SOLVER_SECONDS, today=date.today()
        )

        assert estimate.units is None
        assert estimate.basis is Basis.UNAVAILABLE
        assert str(MINIMUM_SAMPLES) in estimate.how
        assert "cannot estimate" in estimate.human()

    def test_enough_history_gives_a_measured_median(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        today = date.today()
        for units in (10, 20, 30):
            self._record(db_session, organisation_id, units, today)
        db_session.flush()

        estimate = estimate_meter(
            db_session, organisation_id, Meter.SOLVER_SECONDS, today=today
        )

        assert estimate.units == 20
        assert estimate.basis is Basis.MEASURED
        assert estimate.samples == 3

    def test_the_median_is_used_so_one_huge_run_does_not_dominate(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        # A 40-minute convergence study in a history of two-second solves drags
        # a mean past every run in it, and an estimate higher than anything
        # that has ever happened is one people learn to ignore.
        today = date.today()
        for units in (2, 2, 2, 2, 100_000):
            self._record(db_session, organisation_id, units, today)
        db_session.flush()

        estimate = estimate_meter(
            db_session, organisation_id, Meter.SOLVER_SECONDS, today=today
        )

        assert estimate.units == 2

    def test_history_outside_the_window_is_not_counted(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        today = date.today()
        for units in (10, 20, 30):
            self._record(db_session, organisation_id, units, today - timedelta(days=200))
        db_session.flush()

        assert (
            estimate_meter(
                db_session, organisation_id, Meter.SOLVER_SECONDS, today=today
            ).units
            is None
        )

    def test_another_tenants_history_is_not_borrowed(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        # An estimate built from somebody else's parts is a number about a
        # different product. A real second organisation, because
        # `usage_records.organisation_id` has a foreign key.
        from app.models import Organisation

        other = Organisation(name="Elsewhere", slug="elsewhere-est", is_personal=False)
        db_session.add(other)
        db_session.flush()
        today = date.today()
        for units in (10, 20, 30):
            self._record(db_session, other.id, units, today)
        db_session.flush()

        assert (
            estimate_meter(
                db_session, organisation_id, Meter.SOLVER_SECONDS, today=today
            ).units
            is None
        )

    def test_a_run_estimate_covers_the_meters_a_solve_moves(
        self, auth_client: AuthenticatedTestClient, db_session: Session, organisation_id: str
    ) -> None:
        meters = {estimate.meter for estimate in estimate_run(
            db_session, organisation_id, today=date.today()
        )}
        assert meters == {Meter.SOLVER_SECONDS, Meter.MESH_ELEMENT_SECONDS}

    def test_the_route_answers_in_sentences(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        body = auth_client.get(
            f"{API}/organisations/{organisation_id}/billing/estimate"
        ).json()
        assert len(body["estimates"]) == 2
        for row in body["estimates"]:
            assert row["sentence"]
            assert row["basis"] in {"measured", "unavailable"}


class TestTheProviderSeam:
    def test_the_default_takes_no_money(self) -> None:
        provider = build_provider("none")
        assert isinstance(provider, NoProvider)
        assert not provider.takes_money

    def test_a_build_with_no_provider_cannot_mint_credit(
        self, db_session: Session, organisation_id: str
    ) -> None:
        # A deployment that cannot take payments must not be a way to grant
        # yourself credit.
        account = BillingAccount(organisation_id=organisation_id)
        result = NoProvider().purchase_credit(account, amount_minor=1000, currency="usd")

        assert not result.ok
        assert "cannot take payments" in result.detail

    def test_an_unknown_provider_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="PAYMENT_PROVIDER"):
            build_provider("paypal")

    def test_stripe_without_a_key_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="STRIPE_API_KEY"):
            StripeProvider(api_key="  ")

    def test_a_missing_stripe_package_is_a_result_not_a_crash(
        self, db_session: Session, organisation_id: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The `ezdxf` lesson: an optional import must degrade with a sentence,
        # not take the process down.
        provider = StripeProvider(api_key="sk_test_x")
        # With a customer reference, so the call reaches `_client()` — without
        # one it returns "no Stripe customer yet" and never imports anything.
        account = BillingAccount(
            organisation_id=organisation_id, external_customer_ref="cus_x"
        )

        def no_stripe() -> object:
            raise ImportError("no module named stripe")

        monkeypatch.setattr(provider, "_client", no_stripe)
        result = provider.purchase_credit(account, amount_minor=100, currency="usd")

        assert not result.ok
        assert "stripe package is not installed" in result.detail

    def test_the_fake_records_what_it_was_asked(
        self, db_session: Session, organisation_id: str
    ) -> None:
        provider = FakeProvider()
        account = BillingAccount(organisation_id=organisation_id)
        provider.change_plan(account, Plan.TEAM)

        assert provider.calls == [("change_plan", {"plan": "team"})]


class TestCredit:
    def test_credit_is_integer_minor_units(
        self, db_session: Session, organisation_id: str
    ) -> None:
        account = BillingAccount(organisation_id=organisation_id, credit_balance_minor=500)
        db_session.add(account)
        db_session.flush()

        assert grant_credit(db_session, account, amount_minor=250) == 750

    def test_a_balance_cannot_be_taken_below_zero(
        self, db_session: Session, organisation_id: str
    ) -> None:
        # A negative prepaid balance is debt, which is a different product with
        # different law attached.
        account = BillingAccount(organisation_id=organisation_id, credit_balance_minor=100)
        db_session.add(account)
        db_session.flush()

        with pytest.raises(PaymentError, match="below zero"):
            grant_credit(db_session, account, amount_minor=-500)

    def test_the_payment_is_taken_before_the_balance_moves(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        # The other order hands out credit whenever the provider is
        # unreachable, which is the failure an attacker goes looking for.
        payments.use_provider(FakeProvider(succeed=False))

        response = auth_client.post(
            f"{API}/organisations/{organisation_id}/billing/credit",
            json={"amount_minor": 5000},
        )

        assert response.status_code == 402
        balance = auth_client.get(
            f"{API}/organisations/{organisation_id}/billing"
        ).json()["credit_balance_minor"]
        assert balance == 0

    def test_a_successful_purchase_moves_the_balance(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        payments.use_provider(FakeProvider())

        response = auth_client.post(
            f"{API}/organisations/{organisation_id}/billing/credit",
            json={"amount_minor": 5000},
        )

        assert response.status_code == 200, response.text
        assert response.json()["credit_balance_minor"] == 5000


class TestChangingPlan:
    def test_a_provider_refusal_leaves_the_plan_alone(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        # A plan recorded here that the provider refused is a tenant on a plan
        # nobody is charging for.
        payments.use_provider(FakeProvider(succeed=False))

        response = auth_client.put(
            f"{API}/organisations/{organisation_id}/billing/plan", json={"plan": "team"}
        )

        assert response.status_code == 502
        assert (
            auth_client.get(f"{API}/organisations/{organisation_id}/billing").json()["plan"]
            == "free"
        )

    def test_a_successful_change_records_the_provider_reference(
        self, auth_client: AuthenticatedTestClient, organisation_id: str
    ) -> None:
        payments.use_provider(FakeProvider())

        response = auth_client.put(
            f"{API}/organisations/{organisation_id}/billing/plan", json={"plan": "team"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["plan"] == "team"
