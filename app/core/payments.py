"""Taking money, behind a seam (P8.2).

**Kryova has no price list, and this module does not invent one.** That is a
product decision and inventing "the free tier gets 3,600 solver-seconds" would
put a number where an engineer would later read it as settled — the fabrication
Decision 3 exists to prevent, in the one place where it would be *billed for*.
`PLANS` in `core/metering.py` says the same thing about allowances, and this
module is the other half of the same stance: the machinery is complete, and the
numbers come from the deployment.

**Which is also the right shape rather than a compromise.** Decision 4 says
Kryova is free and open, and a self-hosted install has no price list at all —
its operator sets what its own users may do. So plan allowances are settings
(`app/core/config.py`, `PLAN_ALLOWANCE_SETTINGS` below), and a payment provider
is one adapter for deployments that sell. A build that never takes money never
constructs one.

**Three providers, chosen by setting:**

* `none` — the default, and the only one a self-hosted install needs. Plan
  changes are an operator action; there is no card and no invoice.
* `stripe` — metered billing plus prepaid credits, the hybrid the compute-heavy
  SaaS pattern converged on. Amounts are integer minor units end to end, which
  is Stripe's own representation, so the boundary is a transfer and not a
  conversion.
* `fake` — for tests. Records what it was asked to do and does nothing.

**No provider call is allowed to be the thing that loses a payment.** Every
method returns a `ProviderResult` rather than raising, for the same reason
`mail.send` does: the caller has usually already done the work, and a network
failure at the till must leave evidence rather than an exception in a log.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import BillingAccount, Organisation, Plan

logger = logging.getLogger(__name__)

#: Providers this build knows. Validated in `config.py` so a typo is a startup
#: error naming the valid values.
PROVIDERS: Final[frozenset[str]] = frozenset({"none", "stripe", "fake"})


class PaymentError(Exception):
    """A payment operation was refused, with a message meant for the user."""


class CreditReason(StrEnum):
    """Why a credit balance moved. A bare delta cannot be reconciled."""

    PURCHASE = "purchase"
    GRANT = "grant"
    REFUND = "refund"
    CONSUMPTION = "consumption"
    ADJUSTMENT = "adjustment"


@dataclass(frozen=True)
class ProviderResult:
    """What a provider did, returned rather than raised.

    `reference` is the provider's own id for whatever happened — a Stripe
    payment intent, a subscription. It is the only thing that lets somebody
    reconcile our ledger against theirs, so an operation that produced one and
    did not record it is worse than one that failed.
    """

    ok: bool
    reference: str = ""
    detail: str = ""


class PaymentProvider(ABC):
    """Somewhere a plan change or a credit purchase can be taken.

    Deliberately small. Kryova's billing model is *usage posted in aggregate*
    plus *prepaid credits*, so the provider needs to identify a customer, move a
    plan, take a payment and receive a usage total — and nothing else. A wider
    interface would be a wider surface to keep in step with one vendor's API.
    """

    name: str = "none"

    @property
    def takes_money(self) -> bool:
        """Whether this provider can actually charge anybody.

        Read by the billing surface, so "this deployment does not sell" is a
        state the UI can render rather than a plan page that leads nowhere.
        """
        return False

    @abstractmethod
    def ensure_customer(self, account: BillingAccount, organisation: Organisation) -> ProviderResult:
        """Make sure the tenant exists at the provider. Idempotent."""

    @abstractmethod
    def change_plan(self, account: BillingAccount, plan: Plan) -> ProviderResult:
        """Move a subscription."""

    @abstractmethod
    def purchase_credit(
        self, account: BillingAccount, *, amount_minor: int, currency: str
    ) -> ProviderResult:
        """Take a prepaid credit payment."""

    @abstractmethod
    def post_usage(
        self, account: BillingAccount, *, period_end: datetime, totals: dict[str, int]
    ) -> ProviderResult:
        """Report a sealed period's totals, in aggregate.

        Aggregates, never one event per action: the high-volume pattern, and the
        one `core/metering.py` already writes the ledger for.
        """


class NoProvider(PaymentProvider):
    """The default. Plans are an operator action and no money moves.

    Every method succeeds and says what it did *not* do. Returning failure would
    make a self-hosted install look broken; returning bare success would make a
    caller believe a card was charged.
    """

    name = "none"

    def ensure_customer(self, account: BillingAccount, organisation: Organisation) -> ProviderResult:
        return ProviderResult(ok=True, detail="no payment provider is configured")

    def change_plan(self, account: BillingAccount, plan: Plan) -> ProviderResult:
        return ProviderResult(
            ok=True, detail="plan changed locally; no subscription exists to move"
        )

    def purchase_credit(
        self, account: BillingAccount, *, amount_minor: int, currency: str
    ) -> ProviderResult:
        # Refused, not silently granted: a build with no provider must not be a
        # way to mint credit.
        return ProviderResult(
            ok=False,
            detail=(
                "This Kryova cannot take payments. An administrator can grant credit "
                "directly."
            ),
        )

    def post_usage(
        self, account: BillingAccount, *, period_end: datetime, totals: dict[str, int]
    ) -> ProviderResult:
        return ProviderResult(ok=True, detail="usage sealed locally; nothing to post")


@dataclass
class FakeProvider(PaymentProvider):
    """Records what it was asked and does nothing. For tests and dry runs."""

    name: str = "fake"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    succeed: bool = True

    @property
    def takes_money(self) -> bool:
        return True

    def _record(self, what: str, **detail: Any) -> ProviderResult:
        self.calls.append((what, detail))
        return ProviderResult(
            ok=self.succeed,
            reference=f"fake_{what}_{len(self.calls)}",
            detail="" if self.succeed else "the fake provider was told to fail",
        )

    def ensure_customer(self, account: BillingAccount, organisation: Organisation) -> ProviderResult:
        return self._record("ensure_customer", organisation_id=organisation.id)

    def change_plan(self, account: BillingAccount, plan: Plan) -> ProviderResult:
        return self._record("change_plan", plan=plan.value)

    def purchase_credit(
        self, account: BillingAccount, *, amount_minor: int, currency: str
    ) -> ProviderResult:
        return self._record("purchase_credit", amount_minor=amount_minor, currency=currency)

    def post_usage(
        self, account: BillingAccount, *, period_end: datetime, totals: dict[str, int]
    ) -> ProviderResult:
        return self._record("post_usage", totals=dict(totals))


class StripeProvider(PaymentProvider):
    """Stripe metered billing plus prepaid credits.

    **The `stripe` package is imported lazily, inside each method.** A build
    that does not sell must not need it installed, and this repository has
    already been bitten once by an import declared nowhere (`ezdxf`) — so the
    dependency is optional *and* the failure is a `ProviderResult` naming what
    is missing, rather than an ImportError at startup.

    Amounts are integer minor units throughout, which is Stripe's own
    representation. Nothing here converts to a float; the arithmetic that
    matters happens on integers on both sides of the boundary.

    **Not exercised against a live account.** The adapter is written from the
    API and tested against a stub; what it does with real keys is a measurement
    nobody here can make. That is stated rather than implied, and it is why the
    plan's status line for this task says what it says.
    """

    name = "stripe"

    def __init__(self, api_key: str) -> None:
        if not api_key.strip():
            raise ValueError("STRIPE_API_KEY is empty; the stripe provider has no credentials")
        self._api_key = api_key

    @property
    def takes_money(self) -> bool:
        return True

    def _client(self) -> Any:
        import stripe  # noqa: PLC0415 - optional dependency, see the class docstring

        stripe.api_key = self._api_key
        return stripe

    def _guarded(self, what: str, work: Any) -> ProviderResult:
        try:
            return work()
        except ImportError:
            return ProviderResult(
                ok=False,
                detail=(
                    "The stripe package is not installed. Install it, or set "
                    "PAYMENT_PROVIDER=none."
                ),
            )
        except Exception as exc:  # noqa: BLE001 - a till failure is data, not a crash
            logger.warning("stripe %s failed", what, extra={"detail": str(exc)})
            return ProviderResult(ok=False, detail=f"{type(exc).__name__}: {exc}")

    def ensure_customer(self, account: BillingAccount, organisation: Organisation) -> ProviderResult:
        def work() -> ProviderResult:
            if account.external_customer_ref:
                return ProviderResult(ok=True, reference=account.external_customer_ref)
            customer = self._client().Customer.create(
                name=organisation.name,
                metadata={"kryova_organisation_id": organisation.id},
            )
            return ProviderResult(ok=True, reference=str(customer["id"]))

        return self._guarded("ensure_customer", work)

    def change_plan(self, account: BillingAccount, plan: Plan) -> ProviderResult:
        def work() -> ProviderResult:
            if not account.external_customer_ref:
                return ProviderResult(ok=False, detail="This tenant has no Stripe customer yet.")
            self._client().Customer.modify(
                account.external_customer_ref, metadata={"kryova_plan": plan.value}
            )
            return ProviderResult(ok=True, reference=account.external_customer_ref)

        return self._guarded("change_plan", work)

    def purchase_credit(
        self, account: BillingAccount, *, amount_minor: int, currency: str
    ) -> ProviderResult:
        def work() -> ProviderResult:
            if not account.external_customer_ref:
                return ProviderResult(ok=False, detail="This tenant has no Stripe customer yet.")
            intent = self._client().PaymentIntent.create(
                amount=int(amount_minor),
                currency=currency,
                customer=account.external_customer_ref,
                metadata={"kryova_organisation_id": account.organisation_id},
            )
            return ProviderResult(ok=True, reference=str(intent["id"]))

        return self._guarded("purchase_credit", work)

    def post_usage(
        self, account: BillingAccount, *, period_end: datetime, totals: dict[str, int]
    ) -> ProviderResult:
        def work() -> ProviderResult:
            if not account.external_customer_ref:
                return ProviderResult(ok=False, detail="This tenant has no Stripe customer yet.")
            # One aggregate event per meter per period, never one per action.
            for meter, units in totals.items():
                self._client().billing.MeterEvent.create(
                    event_name=meter,
                    payload={
                        "stripe_customer_id": account.external_customer_ref,
                        "value": str(units),
                    },
                    timestamp=int(period_end.timestamp()),
                )
            return ProviderResult(ok=True, reference=account.external_customer_ref)

        return self._guarded("post_usage", work)


_provider: PaymentProvider | None = None


def build_provider(name: str) -> PaymentProvider:
    if name == "none":
        return NoProvider()
    if name == "fake":
        return FakeProvider()
    if name == "stripe":
        return StripeProvider(settings.stripe_api_key)
    raise ValueError(f"PAYMENT_PROVIDER must be one of {', '.join(sorted(PROVIDERS))}; got {name!r}")


def get_provider() -> PaymentProvider:
    global _provider
    if _provider is None:
        _provider = build_provider(settings.payment_provider)
    return _provider


def use_provider(provider: PaymentProvider | None) -> None:
    """Install a provider, or clear it so the next call rebuilds from settings."""
    global _provider
    _provider = provider


# ---------------------------------------------------------------------------
# Credit, which exists whether or not anybody sells
# ---------------------------------------------------------------------------


def grant_credit(
    db: Session,
    account: BillingAccount,
    *,
    amount_minor: int,
    reason: CreditReason = CreditReason.GRANT,
) -> int:
    """Move a tenant's balance. Returns the new balance.

    Integer minor units, and negative amounts are allowed so consumption and
    refunds go through the one function — a second path that subtracts is a
    second place to get the sign wrong.

    **The balance is not allowed below zero.** A negative prepaid balance is
    debt, which is a different product with different law attached, and letting
    one appear by accident is how it gets built without anyone deciding to.
    """
    del reason  # recorded by the caller's audit entry; kept for the signature
    new_balance = account.credit_balance_minor + int(amount_minor)
    if new_balance < 0:
        raise PaymentError(
            "That would take the balance below zero. Prepaid credit cannot go negative."
        )
    account.credit_balance_minor = new_balance
    db.flush()
    return new_balance


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PROVIDERS",
    "CreditReason",
    "FakeProvider",
    "NoProvider",
    "PaymentError",
    "PaymentProvider",
    "ProviderResult",
    "StripeProvider",
    "build_provider",
    "get_provider",
    "grant_credit",
    "use_provider",
    "utcnow",
]
