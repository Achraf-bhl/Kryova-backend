"""What was consumed, who consumed it, and what produced it (Phase P8).

Four tables, and the order they are described in is the order they matter in.

**`usage_records` is the meter, and it is the hard half.** P8.4 says the meter
that bills and the meter shown in a cost estimate are one number with two uses,
and "divergence is a bug class of its own" -- so there is exactly one ledger and
everything reads it. Every row carries the *cause* that produced it, because a
number on an invoice that nobody can trace back to the run behind it is
unarguable in exactly the wrong direction. That binding is
`app.verify.provenance` applied to a bill rather than to a stress figure, and it
uses the same vocabulary: `basis` and `method` are
`app.kernel.provenance.Basis` and `Record.method`, not a second spelling of
them. A CATIA seat minute is `APPROXIMATED` and says how; a token count is
`MEASURED` because the provider reported it.

**Quantities are integers in the meter's smallest unit, never floats.** Money
and counted units are not floating point: a float sum over ten thousand solves
is a number that changes when the rows are read back in a different order, and
an invoice that will not reproduce is worse than no invoice. `Meter.scale` says
how many stored units make one whole unit -- microseconds for a duration, bytes
for storage, one for a token -- so a period total is an integer `SUM` on every
backend, and `Decimal` appears only at the presentation boundary
(`UsageRecord.quantity`). `app.core.metering.UsageEvent` refuses a `float`
outright; the single place a float is allowed to become a quantity is
`UsageEvent.from_seconds`, which is a documented boundary conversion in the same
sense as "units land in mm-N-MPa at the boundary, not deeper in".

**`usage_rollups` is what would post to a billing provider**, and it exists
because P8.1 is explicit that usage "accumulates locally and posts in aggregates
... never one event per action". A rollup is a sealed integer total for one
tenant, one meter and one half-open period, and it points back at the records it
covers through `UsageRecord.rollup_id`, so an aggregate can always be exploded
into the runs behind it. `posted_at` and `external_ref` are the Stripe seam and
are left null here: no key, no webhook endpoint and no sandbox account exists in
this repository, and a column that pretended otherwise would be the fabrication
Decision 3 exists to prevent.

**`billing_accounts` carries the plan and the per-tenant quota overrides.** This
is the gap `app/api/routes/admin.py` names in its own docstring -- "the quotas
are the global settings in force, because per-tenant quota rows do not exist
yet". They exist now, and every override is *nullable*: null means "the global
setting", which is what lets the quota surface say per field where its number
came from instead of implying the console can vary all of them.

**`metering_faults` is the answer to "metering must never fail the thing it
measures" not becoming "metering fails silently".** Recording usage cannot be
allowed to fail a simulation, so `app.core.metering` swallows its own errors --
and a swallowed metering bug is a metering bug that ships, which this repository
learned on 2026-09-06 when `QueueMeter._finished` shadowed its own counter and
no job was ever counted as finished, with the `TypeError` dying unread inside a
`Future`. So a swallowed failure lands in three places instead of nowhere: a
live process counter, a logged exception with its stack, and a row here.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey, utcnow
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.organisation import Organisation


class Meter(enum.StrEnum):
    """What is counted. One entry per thing this system actually spends.

    Deliberately *not* one entry per tool call or per endpoint: a meter is a
    resource with a cost, and a vocabulary that grows with the API is one nobody
    can price. P8.1 names solver-seconds by class, geometry-operation batches,
    storage-bytes and seats; AI tokens are the fifth because in a chat-first
    product they are the larger of the two bills (`app/ai/usage.py` says so).
    """

    #: Wall-clock seconds inside a solve, by solver class. A CalculiX nonlinear
    #: minute is not a linear-static second (P8.1), so the class travels in
    #: `UsageRecord.detail["solver"]` rather than forking the meter -- one meter
    #: with a recorded class stays summable; six meters do not.
    SOLVER_SECONDS = "solver_seconds"
    #: Elements multiplied by the seconds spent meshing them. Meshing cost is
    #: not a duration alone: forty seconds on 1.2 M nodes and forty seconds on
    #: 900 is the same bill under a pure time meter and obviously should not be.
    MESH_ELEMENT_SECONDS = "mesh_element_seconds"
    #: Seconds a CATIA seat was occupied by work Kryova drove. Approximated by
    #: construction -- see `CATIA_SEAT_METHOD` in `app.core.metering`.
    CATIA_SEAT_SECONDS = "catia_seat_seconds"
    #: Prompt plus completion tokens, as the provider reported them.
    AI_TOKENS = "ai_tokens"
    #: Bytes committed to the content-addressed store. A stored *stock*, not a
    #: flow: see `app.core.metering.storage_attribution`.
    STORAGE_BYTES = "storage_bytes"
    #: Geometry operations executed against a kernel, in batches (P8.1's
    #: "geometry-operation batches"). A plan for a machine is 10^5-10^6
    #: operations, so one record per operation is not a ledger, it is a flood.
    KERNEL_OPERATIONS = "kernel_operations"

    @property
    def unit(self) -> str:
        return _METER_FACTS[self][0]

    @property
    def scale(self) -> int:
        """How many stored integer units make one whole unit of this meter."""
        return _METER_FACTS[self][1]

    @property
    def what(self) -> str:
        """What the number means, in the words an operator would use."""
        return _METER_FACTS[self][2]


#: (unit, scale, meaning). The scale is what keeps a period total an exact
#: integer `SUM`: a duration is stored in microseconds, a byte count in bytes.
#: Sizing matters -- storage at a scale of 10^6 would put a terabyte at 10^18,
#: inside a signed 64-bit column but close enough to its ceiling that a sum over
#: a year of tenants would not be.
_METER_FACTS: Final[dict[Meter, tuple[str, int, str]]] = {
    Meter.SOLVER_SECONDS: ("s", 1_000_000, "seconds of solver wall clock"),
    Meter.MESH_ELEMENT_SECONDS: (
        "element·s",
        1_000,
        "elements meshed multiplied by the seconds it took",
    ),
    Meter.CATIA_SEAT_SECONDS: ("s", 1_000_000, "seconds a CATIA seat was occupied"),
    Meter.AI_TOKENS: ("token", 1, "prompt plus completion tokens"),
    Meter.STORAGE_BYTES: ("B", 1, "bytes committed to the blob store"),
    Meter.KERNEL_OPERATIONS: ("op", 1, "geometry operations executed"),
}

if set(_METER_FACTS) != set(Meter):  # pragma: no cover - caught at import
    raise ValueError("every Meter needs a unit, a scale and a meaning")


class MeterType(TypeDecorator):
    """Store the meter as text, and always hand back a `Meter`.

    The same trap `MessageRoleType` and `CatiaDeviceStatusType` document: a
    `Mapped[Meter]` over a bare `String` types the attribute on a freshly
    constructed object and hands back a plain `str` on one loaded from the
    database. `StrEnum` compares equal either way, so `==` keeps working and
    only the identity checks and the `.scale` lookups break -- and they break
    where nobody is looking, which for a meter means a quantity divided by the
    wrong scale.
    """

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return Meter(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Meter | None:
        if value is None:
            return None
        return Meter(value)


class Plan(enum.StrEnum):
    """P8.2's three plans. Their allowances live in `app.core.metering.PLANS`.

    In code rather than in a table, deliberately: changing what a plan includes
    is a product decision that should arrive with a release and a diff, not as
    an UPDATE somebody ran. What *is* in the database is the per-tenant
    override, because that is genuinely per-tenant.
    """

    FREE = "free"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class PlanType(TypeDecorator):
    """As `MeterType`, for the same reason."""

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return Plan(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Plan | None:
        if value is None:
            return None
        return Plan(value)


class UsageRecord(UUIDPrimaryKey, Base):
    """One metered event, bound to what caused it.

    Append-only in practice and in intent -- nothing in the application updates
    a row after it is written except to stamp `rollup_id` when a period is
    sealed. It is deliberately *not* defended by the append-only trigger
    `audit_events` carries: that table's contract is that a deletion is
    detectable, and it pays for it with a hash chain and a serialising advisory
    lock on every append. A meter written from inside a solve cannot afford a
    lock per record, and the honest claim here is the weaker one -- the ledger
    is reconstructible from its causes, not tamper-evident.

    `created_at` alone, no `updated_at`: an `updated_at` on a row nothing
    meaningfully updates is a claim the code does not honour
    (`CatiaCheckpoint` carries the same note).
    """

    __tablename__ = "usage_records"
    __table_args__ = (
        # The period rollup and the quota check are both exactly this lookup.
        Index("ix_usage_records_org_date_meter", "organisation_id", "usage_date", "meter"),
        # "Show me everything this job cost" -- the trace-to-cause query.
        Index("ix_usage_records_subject", "subject_type", "subject_id"),
    )

    #: CASCADE, and the choice is not the reflex. A usage row that cannot name
    #: its tenant is unbillable and sits outside every RLS policy, which is the
    #: same defect `Project.organisation_id` being NOT NULL exists to prevent.
    #: What survives a deleted tenant is the audit log, which is built for it;
    #: this ledger is an artefact of a live account.
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    meter: Mapped[Meter] = mapped_column(MeterType)
    #: The quantity in `meter.scale` units. Integer, on purpose -- see the
    #: module docstring. Read it through `.quantity`, never raw.
    quantity_units: Mapped[int] = mapped_column(BigInteger)

    #: `app.kernel.provenance.Basis` -- measured / approximated / unavailable.
    #: A metered quantity that was estimated says so here, in the same word the
    #: geometry kernel and the simulation provenance record use.
    basis: Mapped[str] = mapped_column(String(16))
    #: How it was arrived at, in the words a reviewer would want. Required: an
    #: approximated number that does not name its method is not auditable.
    method: Mapped[str] = mapped_column(String(300))

    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    #: The UTC calendar day, denormalised so a period sum is an index lookup
    #: rather than a timezone-sensitive expression over `occurred_at`. Exactly
    #: the reason `AITokenUsage.usage_date` exists.
    usage_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    # -- the cause ----------------------------------------------------------
    #
    # Two layers, and both are needed. The strings are denormalised and survive
    # the subject being deleted, exactly as `CatiaOperation.user_id` is
    # denormalised because "audit rows that lose their subject are worthless".
    # The foreign keys are what make a live record navigable, and every one of
    # them is SET NULL: deleting a project must not delete the record that its
    # work was billed.

    #: What produced this record -- "simulation.runner", "ai.chat",
    #: "catia.bridge", "kernel.occt". Module-shaped, matching `observe`'s span
    #: names, so a usage row and a span can be lined up by eye.
    source: Mapped[str] = mapped_column(String(64))
    #: The kind of thing that caused it: "simulation_job", "conversation",
    #: "geometry_version", "media", "plan".
    subject_type: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(64))

    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), default=None, index=True
    )
    simulation_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("simulation_jobs.id", ondelete="SET NULL"), default=None
    )
    geometry_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("geometry_versions.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), default=None
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    #: Everything else needed to reconstruct the number: the solver name and
    #: version, node and element counts, the mesh digest, the plan digest, the
    #: model name. This is what turns "0.42 solver-seconds" into a claim
    #: somebody can re-run.
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    #: Set when a period is sealed. Null means "not yet aggregated", which is
    #: also the query that finds what a rollup must cover.
    rollup_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_rollups.id", ondelete="SET NULL"), default=None, index=True
    )

    @property
    def quantity(self) -> Decimal:
        """The quantity in whole units, exactly. Never a float."""
        return Decimal(self.quantity_units) / Decimal(self.meter.scale)

    @property
    def unit(self) -> str:
        return self.meter.unit


class UsageRollup(UUIDPrimaryKey, Base):
    """A sealed total for one tenant, one meter and one half-open period.

    Half-open `[period_start, period_end)` on purpose: adjacent periods that
    share an endpoint double-count the boundary day, and a monthly invoice is
    exactly a sequence of adjacent periods.
    """

    __tablename__ = "usage_rollups"
    __table_args__ = (
        UniqueConstraint(
            "organisation_id",
            "meter",
            "period_start",
            "period_end",
            name="uq_usage_rollup_period",
        ),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    meter: Mapped[Meter] = mapped_column(MeterType)
    period_start: Mapped[date] = mapped_column(Date)
    #: Exclusive.
    period_end: Mapped[date] = mapped_column(Date)
    quantity_units: Mapped[int] = mapped_column(BigInteger)
    #: How many `usage_records` this total is over. A rollup whose count does
    #: not match the records still pointing at it has lost or gained rows, which
    #: is the cheapest possible integrity check on an aggregate.
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    sealed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    #: The billing-provider seam, and nothing in this repository fills them in.
    #: Stripe metered billing plus prepaid credits is the technology-register
    #: choice (P8.2); an integration needs an API key, a webhook endpoint and a
    #: sandbox account, none of which exist here. Two nullable columns are the
    #: whole of what the local ledger owes it.
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    external_ref: Mapped[str | None] = mapped_column(String(128), default=None)

    @property
    def quantity(self) -> Decimal:
        return Decimal(self.quantity_units) / Decimal(self.meter.scale)


class BillingAccount(UUIDPrimaryKey, TimestampMixin, Base):
    """One per organisation: the plan, the credit balance and the overrides."""

    __tablename__ = "billing_accounts"
    __table_args__ = (
        UniqueConstraint("organisation_id", name="uq_billing_account_organisation"),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    plan: Mapped[Plan] = mapped_column(PlanType, default=Plan.FREE)

    #: Prepaid credit, in **minor units** of `currency` -- cents, not dollars.
    #: Integer for the same reason quantities are: money is not floating point,
    #: and this is the representation Stripe itself uses, so the eventual
    #: integration is a transfer rather than a conversion.
    credit_balance_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="usd")

    #: The customer's identity at the billing provider, when there is one. Null
    #: everywhere today; see `UsageRollup.posted_at`.
    external_provider: Mapped[str | None] = mapped_column(String(32), default=None)
    external_customer_ref: Mapped[str | None] = mapped_column(String(128), default=None)

    #: Per-tenant quota overrides. **Null means "use the global setting"**, and
    #: that is what lets the quota surface say, per field, whether the number
    #: came from this row, from the plan or from `app/core/config.py`. A column
    #: defaulted to the current global value would look identical and would
    #: silently freeze that value forever.
    max_concurrent_simulations_per_user: Mapped[int | None] = mapped_column(
        Integer, default=None
    )
    max_media_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    ai_daily_token_budget: Mapped[int | None] = mapped_column(BigInteger, default=None)

    organisation: Mapped["Organisation"] = relationship()


class MeteringFault(UUIDPrimaryKey, Base):
    """A metering failure that was swallowed so the work could finish.

    The row exists because the alternative to "metering fails the job" is not
    "metering fails silently". Every field here is what somebody debugging the
    meter at 2 a.m. would ask for, and `organisation_id` is a plain string with
    **no foreign key** on purpose: the most likely cause of a metering failure
    is a bad or missing tenant id, and a foreign key would make recording that
    fact fail for the same reason the original write did.
    """

    __tablename__ = "metering_faults"

    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    #: Nullable: a failure can happen before a meter has been decided.
    meter: Mapped[str | None] = mapped_column(String(32), default=None)
    organisation_id: Mapped[str | None] = mapped_column(String(36), default=None)
    source: Mapped[str] = mapped_column(String(64), default="")
    #: `TypeError: unsupported operand ...` -- the exception class and message,
    #: shaped like `app.observe.collect._describe`'s failure text.
    failure: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


__all__ = [
    "BillingAccount",
    "Meter",
    "MeterType",
    "MeteringFault",
    "Plan",
    "PlanType",
    "UsageRecord",
    "UsageRollup",
]
