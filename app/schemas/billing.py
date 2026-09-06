"""What the billing surface returns (Phase P8).

Three habits from elsewhere in the codebase are kept here deliberately.

**Every quantity is a `Decimal`, and Pydantic renders one as a JSON string.**
That is the point: a JSON number is an IEEE double the moment a browser parses
it, so a total shipped as `12.300000000000001` is a total somebody will
eventually quote in an argument. The ledger stores integers, this converts once,
and the wire carries the digits.

**Every derived number says how it was derived.** `UsageSummaryRead.storage`
carries its own `method` and its own `unplaced_reason`; a meter with nothing
feeding it appears under `unmetered` with the reason, never as a zero. That is
the same rule `OrganisationUsageRead` applies to `storage_attribution` and
`app.design.assertions` applies to a measurement nobody took: an unmeasured
claim is never a pass, and on an invoice it is never a zero either.

**The Stripe fields exist and are empty, and the payload says why.** No key, no
webhook, no sandbox account — see `BillingAccountRead.billing_provider`.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.billing import Meter, Plan


class UsageCauseRead(BaseModel):
    """What produced a record — every id a person could open to check it."""

    source: str
    subject_type: str
    subject_id: str
    project_id: str | None = None
    simulation_job_id: str | None = None
    geometry_version_id: str | None = None
    conversation_id: str | None = None
    media_id: str | None = None
    user_id: str | None = None


class UsageRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meter: Meter
    quantity: Decimal
    unit: str
    #: measured / approximated, in `app.kernel.provenance`'s words. An
    #: approximated quantity that did not say so would be the whole failure.
    basis: str
    method: str
    occurred_at: datetime
    usage_date: date
    cause: UsageCauseRead
    #: Solver name and version, span durations, element counts — whatever is
    #: needed to re-derive the number.
    detail: dict[str, Any] = Field(default_factory=dict)
    #: Set once the period this record falls in has been sealed.
    rollup_id: str | None = None


class MeterTotalRead(BaseModel):
    meter: Meter
    unit: str
    quantity: Decimal
    records: int


class UnmeteredMeterRead(BaseModel):
    """A meter nothing feeds yet, named rather than reported as zero."""

    meter: Meter
    unit: str
    reason: str


class StorageAttributionRead(BaseModel):
    bytes_attributed: int
    media_rows: int
    method: str
    #: Bytes owned by members that no project references. Excluded from the
    #: total on purpose; the reason travels with the number.
    unplaced_bytes: int
    unplaced_rows: int
    unplaced_reason: str


class MeteringHealthRead(BaseModel):
    """Whether the meter itself is still counting, in this process.

    Process-wide rather than per-tenant, and included because the alternative to
    "metering fails the job" is not "metering fails silently". `events_failed`
    above zero means something was swallowed; the rows behind it are at
    `/billing/faults`.
    """

    events_recorded: int
    events_failed: int
    spans_seen: int
    spans_attributed: int
    spans_unattributed: int
    scopes_opened: int
    last_fault_at: datetime | None = None


class UsageSummaryRead(BaseModel):
    organisation_id: str
    #: Half-open `[period_start, period_end)`, so consecutive periods do not
    #: double-count the boundary day.
    period_start: date
    period_end: date
    totals: list[MeterTotalRead]
    unmetered: list[UnmeteredMeterRead]
    storage: StorageAttributionRead
    metering: MeteringHealthRead
    generated_at: datetime


class QuotaLimitRead(BaseModel):
    name: str
    limit: int
    #: "tenant override", "plan <name>" or "global settings". Never omitted.
    source: str
    what: str


class QuotaEnvelopeRead(BaseModel):
    organisation_id: str
    plan: Plan
    limits: list[QuotaLimitRead]
    credit_balance_minor: int
    currency: str
    unlimited_meters: list[Meter]
    note: str


class BillingAccountRead(BaseModel):
    organisation_id: str
    plan: Plan
    credit_balance_minor: int
    currency: str
    external_provider: str | None = None
    external_customer_ref: str | None = None
    quotas: QuotaEnvelopeRead
    #: Said in the payload rather than only in a docstring, because the field
    #: above being null is otherwise indistinguishable from a broken sync.
    billing_provider: str = (
        "Stripe metered billing plus prepaid credits is the chosen technology "
        "(master plan P8.2) and is not integrated: this deployment holds no API "
        "key, exposes no webhook endpoint and has no sandbox account. The local "
        "ledger and the rollups are what an integration would post; nothing here "
        "calls Stripe."
    )


class BillingAccountUpdate(BaseModel):
    """Per-tenant quota overrides and plan, set by an organisation owner.

    Every field is optional and `None` means "leave it alone", which is not the
    same as clearing an override — clearing one is `-1`, spelled out, because a
    JSON body cannot distinguish an absent key from a null one without help.
    """

    plan: Plan | None = None
    max_concurrent_simulations_per_user: int | None = Field(default=None, ge=-1)
    max_media_bytes: int | None = Field(default=None, ge=-1)
    ai_daily_token_budget: int | None = Field(default=None, ge=-1)


class UsageRollupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meter: Meter
    unit: str
    period_start: date
    period_end: date
    quantity: Decimal
    record_count: int
    sealed_at: datetime
    #: Null until something posts it. See `BillingAccountRead.billing_provider`.
    posted_at: datetime | None = None
    external_ref: str | None = None


class MeteringFaultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    occurred_at: datetime
    meter: str | None = None
    source: str
    failure: str
    detail: dict[str, Any] = Field(default_factory=dict)
