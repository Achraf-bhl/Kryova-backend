"""Billing, usage and quotas for one tenant (Phase P8).

Everything here hangs off `/organisations/{organisation_id}/billing`, and that
is the security design rather than a URL choice: the guards in `app/api/deps.py`
resolve the organisation from the path and answer **404** to anybody who is not
a member of it — not 403, which would confirm the id is real and hand out a free
enumeration oracle across accounts. There is no second answer to "may this
person see this bill" anywhere in this file.

Reads need `ADMIN` of the organisation and the seal needs `OWNER`. A viewer on
an engineering project has no business reading what the company is spending, and
widening these to `VIEWER` to make a page render is the edit that would give it
to them.

Two things this surface refuses to do, and both are the same rule:

**A meter nothing feeds is reported by name, never as a zero.** `/summary`
carries an `unmetered` list, straight out of `app.core.metering.unwired_meters`,
because "this tenant used no CATIA seat time" and "nothing meters CATIA seat
time" are the same silence otherwise — and a customer reading a zero has been
told something false. It is the `UNMEASURED` discipline `app.design.assertions`
applies to a claim nobody measured, applied to a line on an invoice.

**Nothing here calls Stripe.** The rollups are exactly what an integration would
post and the two columns it would fill are on them, empty; the payload says so
in words rather than leaving a null to be read as a sync failure.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import (
    AdminOrganisation,
    AuditDep,
    DbSession,
    OwnerOrganisation,
    PrincipalDep,
    ViewerOrganisation,
)
from app.core.estimates import estimate_run
from app.core.metering import (
    METERING,
    billing_account,
    quota_envelope,
    seal_period,
    storage_attribution,
    unwired_meters,
    usage_units,
)
from app.core.payments import PaymentError, get_provider, grant_credit
from app.models.audit import AuditAction, AuditOutcome
from app.models.billing import (
    BillingAccount,
    Meter,
    MeteringFault,
    Plan,
    UsageRecord,
    UsageRollup,
)
from app.schemas.billing import (
    BillingAccountRead,
    BillingAccountUpdate,
    CreditPurchase,
    EstimateRead,
    MeteringFaultRead,
    MeteringHealthRead,
    MeterTotalRead,
    PlanChange,
    QuotaEnvelopeRead,
    QuotaLimitRead,
    RunEstimateRead,
    StorageAttributionRead,
    UnmeteredMeterRead,
    UsageCauseRead,
    UsageRecordRead,
    UsageRollupRead,
    UsageSummaryRead,
)
from app.schemas.pagination import Page

router = APIRouter(prefix="/organisations", tags=["billing"])

#: Clearing a per-tenant override, spelled explicitly. A JSON body cannot tell
#: an absent key from a null one, so "go back to the global setting" needs a
#: value of its own rather than a null that also means "leave it alone".
CLEAR_OVERRIDE = -1


def _default_period(start: date | None, end: date | None) -> tuple[date, date]:
    """The current UTC calendar month, half-open, unless asked otherwise.

    Half-open `[start, end)` because a monthly invoice is a sequence of adjacent
    periods and a shared endpoint would bill the boundary day twice.
    """
    today = datetime.now(timezone.utc).date()
    resolved_start = start or today.replace(day=1)
    if end is not None:
        resolved_end = end
    else:
        next_month = (resolved_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        resolved_end = next_month
    if resolved_end <= resolved_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"The period end ({resolved_end}) must be after its start "
                f"({resolved_start}). The range is half-open, so a single day is "
                f"start={resolved_start} and end={resolved_start + timedelta(days=1)}."
            ),
        )
    return resolved_start, resolved_end


def _envelope_view(db: DbSession, organisation_id: str) -> QuotaEnvelopeRead:
    envelope = quota_envelope(db, organisation_id)
    return QuotaEnvelopeRead(
        organisation_id=envelope.organisation_id,
        plan=envelope.plan,
        limits=[
            QuotaLimitRead(name=limit.name, limit=limit.limit, source=limit.source, what=limit.what)
            for limit in envelope.limits
        ],
        credit_balance_minor=envelope.credit_balance_minor,
        currency=envelope.currency,
        unlimited_meters=list(envelope.unlimited_meters),
        note=envelope.note,
    )


def _record_view(record: UsageRecord) -> UsageRecordRead:
    return UsageRecordRead(
        id=record.id,
        meter=record.meter,
        quantity=record.quantity,
        unit=record.unit,
        basis=record.basis,
        method=record.method,
        occurred_at=record.occurred_at,
        usage_date=record.usage_date,
        cause=UsageCauseRead(
            source=record.source,
            subject_type=record.subject_type,
            subject_id=record.subject_id,
            project_id=record.project_id,
            simulation_job_id=record.simulation_job_id,
            geometry_version_id=record.geometry_version_id,
            conversation_id=record.conversation_id,
            media_id=record.media_id,
            user_id=record.user_id,
        ),
        detail=dict(record.detail or {}),
        rollup_id=record.rollup_id,
    )


def _rollup_view(rollup: UsageRollup) -> UsageRollupRead:
    return UsageRollupRead(
        id=rollup.id,
        meter=rollup.meter,
        unit=rollup.meter.unit,
        period_start=rollup.period_start,
        period_end=rollup.period_end,
        quantity=rollup.quantity,
        record_count=rollup.record_count,
        sealed_at=rollup.sealed_at,
        posted_at=rollup.posted_at,
        external_ref=rollup.external_ref,
    )


# ---------------------------------------------------------------------------
# The account and its quotas
# ---------------------------------------------------------------------------


@router.get("/{organisation_id}/billing", response_model=BillingAccountRead)
def read_billing_account(
    organisation: AdminOrganisation, db: DbSession
) -> BillingAccountRead:
    """The plan, the credit balance and every limit in force, with its source.

    A tenant with no `billing_accounts` row is not an error and is not created
    here: it is on the free plan with every limit coming from the global
    settings, which is exactly what the envelope reports. Creating a row on read
    would make a GET a write and would put a plan on record that nobody chose.
    """
    account = billing_account(db, organisation.id)
    return BillingAccountRead(
        organisation_id=organisation.id,
        plan=account.plan if account is not None else Plan.FREE,
        credit_balance_minor=account.credit_balance_minor if account is not None else 0,
        currency=account.currency if account is not None else "usd",
        external_provider=account.external_provider if account is not None else None,
        external_customer_ref=account.external_customer_ref if account is not None else None,
        quotas=_envelope_view(db, organisation.id),
    )


@router.put("/{organisation_id}/billing", response_model=BillingAccountRead)
def update_billing_account(
    payload: BillingAccountUpdate, organisation: OwnerOrganisation, db: DbSession
) -> BillingAccountRead:
    """Set the plan and the per-tenant quota overrides.

    This is the gap `app/api/routes/admin.py` names — "per-tenant quota rows do
    not exist yet" — closed. An override of `-1` clears it, returning that field
    to the global setting, and the envelope then says `global settings` again
    rather than pretending the tenant chose the default.
    """
    account = billing_account(db, organisation.id)
    if account is None:
        account = BillingAccount(organisation_id=organisation.id)
        db.add(account)

    if payload.plan is not None:
        account.plan = payload.plan
    for name in (
        "max_concurrent_simulations_per_user",
        "max_media_bytes",
        "ai_daily_token_budget",
    ):
        value = getattr(payload, name)
        if value is None:
            continue
        setattr(account, name, None if value == CLEAR_OVERRIDE else value)

    db.flush()
    db.commit()
    return read_billing_account(organisation, db)


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


@router.get("/{organisation_id}/billing/usage", response_model=Page[UsageRecordRead])
def list_usage(
    organisation: AdminOrganisation,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    meter: Annotated[Meter | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[UsageRecordRead]:
    """Every metered event in a period, newest first, paginated."""
    period_start, period_end = _default_period(start, end)
    conditions = [
        UsageRecord.organisation_id == organisation.id,
        UsageRecord.usage_date >= period_start,
        UsageRecord.usage_date < period_end,
    ]
    if meter is not None:
        conditions.append(UsageRecord.meter == meter)

    total = db.scalar(select(func.count()).select_from(UsageRecord).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(UsageRecord)
            .where(*conditions)
            .order_by(UsageRecord.occurred_at.desc(), UsageRecord.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[UsageRecordRead](
        total=total,
        page=page,
        page_size=page_size,
        items=[_record_view(row) for row in rows],
    )


@router.get("/{organisation_id}/billing/usage/{record_id}", response_model=UsageRecordRead)
def read_usage_record(
    record_id: str, organisation: AdminOrganisation, db: DbSession
) -> UsageRecordRead:
    """One record, with the whole chain that produced it.

    A record belonging to another tenant is 404, not 403, and the check is on
    `organisation_id` rather than on the row existing: an id that resolves for
    one caller and 403s for another is the enumeration oracle the whole tenancy
    model refuses.
    """
    record = db.get(UsageRecord, record_id)
    if record is None or record.organisation_id != organisation.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _record_view(record)


@router.get("/{organisation_id}/billing/summary", response_model=UsageSummaryRead)
def read_usage_summary(
    organisation: AdminOrganisation,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> UsageSummaryRead:
    """Exact totals for a period, plus everything this could not measure."""
    period_start, period_end = _default_period(start, end)
    units = usage_units(db, organisation.id, period_start, period_end)

    counts = {
        Meter(meter): int(count)
        for meter, count in db.execute(
            select(UsageRecord.meter, func.count())
            .where(
                UsageRecord.organisation_id == organisation.id,
                UsageRecord.usage_date >= period_start,
                UsageRecord.usage_date < period_end,
            )
            .group_by(UsageRecord.meter)
        ).all()
    }

    storage = storage_attribution(db, organisation.id)
    snapshot = METERING.snapshot()
    return UsageSummaryRead(
        organisation_id=organisation.id,
        period_start=period_start,
        period_end=period_end,
        totals=[
            MeterTotalRead(
                meter=meter,
                unit=meter.unit,
                quantity=Decimal(units[meter]) / Decimal(meter.scale),
                records=counts.get(meter, 0),
            )
            for meter in sorted(units, key=lambda item: item.value)
        ],
        unmetered=[
            UnmeteredMeterRead(
                meter=site.meter, unit=site.meter.unit, reason=site.not_wired_because
            )
            for site in unwired_meters()
        ],
        storage=StorageAttributionRead(
            bytes_attributed=storage.bytes_attributed,
            media_rows=storage.media_rows,
            method=storage.method,
            unplaced_bytes=storage.unplaced_bytes,
            unplaced_rows=storage.unplaced_rows,
            unplaced_reason=storage.unplaced_reason,
        ),
        metering=MeteringHealthRead(
            events_recorded=snapshot.events_recorded,
            events_failed=snapshot.events_failed,
            spans_seen=snapshot.spans_seen,
            spans_attributed=snapshot.spans_attributed,
            spans_unattributed=snapshot.spans_unattributed,
            scopes_opened=snapshot.scopes_opened,
            last_fault_at=snapshot.last_fault_at,
        ),
        generated_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Aggregates — what would post to a billing provider
# ---------------------------------------------------------------------------


@router.get("/{organisation_id}/billing/rollups", response_model=Page[UsageRollupRead])
def list_rollups(
    organisation: AdminOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[UsageRollupRead]:
    conditions = [UsageRollup.organisation_id == organisation.id]
    total = db.scalar(select(func.count()).select_from(UsageRollup).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(UsageRollup)
            .where(*conditions)
            .order_by(UsageRollup.period_start.desc(), UsageRollup.meter)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[UsageRollupRead](
        total=total, page=page, page_size=page_size, items=[_rollup_view(row) for row in rows]
    )


@router.post(
    "/{organisation_id}/billing/periods/close",
    # Not paginated, and it is not the debt the other list endpoints carry: the
    # result is one row per meter, so its length is `len(Meter)` and cannot grow
    # with the tenant's usage.
    response_model=list[UsageRollupRead],
    status_code=status.HTTP_200_OK,
)
def close_period(
    organisation: OwnerOrganisation,
    db: DbSession,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> list[UsageRollupRead]:
    """Seal a period into one aggregate per meter.

    Idempotent: sealing the same period twice recomputes the totals rather than
    appending a second set, which is what makes a retry after a failed post safe
    once there is something to post to. Every record in the period is stamped
    with the rollup that covers it, so an aggregate can always be exploded back
    into the runs behind it.
    """
    period_start, period_end = _default_period(start, end)
    sealed = seal_period(db, organisation.id, period_start, period_end)
    db.commit()
    return [_rollup_view(rollup) for rollup in sealed]


# ---------------------------------------------------------------------------
# What the meter swallowed
# ---------------------------------------------------------------------------


@router.get("/{organisation_id}/billing/faults", response_model=Page[MeteringFaultRead])
def list_metering_faults(
    organisation: OwnerOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[MeteringFaultRead]:
    """Metering failures that were absorbed so the work could finish.

    This endpoint is the difference between "metering must never fail the thing
    it measures" and "metering fails silently". A tenant whose bill is short can
    see, in the product, that the meter dropped something and when.
    """
    conditions = [MeteringFault.organisation_id == organisation.id]
    total = db.scalar(select(func.count()).select_from(MeteringFault).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(MeteringFault)
            .where(*conditions)
            .order_by(MeteringFault.occurred_at.desc(), MeteringFault.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[MeteringFaultRead](
        total=total,
        page=page,
        page_size=page_size,
        items=[MeteringFaultRead.model_validate(row) for row in rows],
    )


# ---------------------------------------------------------------------------
# Plans, credit and estimates (P8.2, P8.3, P8.4)
# ---------------------------------------------------------------------------


def _account(db: DbSession, organisation_id: str) -> BillingAccount:
    """This tenant's billing row, created on first *write*.

    Never on a read — `read_billing_account` is careful not to, because a GET
    that writes a row puts a plan on record nobody chose.
    """
    account = billing_account(db, organisation_id)
    if account is None:
        account = BillingAccount(organisation_id=organisation_id)
        db.add(account)
        db.flush()
    return account


@router.get("/{organisation_id}/billing/estimate", response_model=RunEstimateRead)
def read_run_estimate(
    organisation: ViewerOrganisation, db: DbSession
) -> RunEstimateRead:
    """What a simulation is likely to cost this tenant (P8.4).

    **The same meters the bill is made of**, projected from this organisation's
    own recorded history — not a separate model of cost that could drift from
    the ledger. That identity is the whole of P8.4: "one number, two uses;
    divergence is a bug class of its own."

    An estimate with too little history says so and carries no number, rather
    than offering a figure nobody measured. A cost estimate is the most tempting
    place in a product to invent one, because it is advisory and never
    contradicted afterwards.
    """
    estimates = estimate_run(db, organisation.id, today=date.today())
    return RunEstimateRead(
        organisation_id=organisation.id,
        estimates=[
            EstimateRead(
                meter=estimate.meter,
                unit=estimate.meter.unit,
                units=estimate.units,
                basis=estimate.basis.value,
                how=estimate.how,
                samples=estimate.samples,
                sentence=estimate.human(),
            )
            for estimate in estimates
        ],
    )


@router.put("/{organisation_id}/billing/plan", response_model=BillingAccountRead)
def change_plan(
    payload: PlanChange,
    organisation: OwnerOrganisation,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> BillingAccountRead:
    """Move a tenant onto another plan.

    `OwnerOrganisation`: billing is an owner's power in P2's ladder, not an
    admin's. The provider is told *after* the local change is decided and
    *before* it is committed — a plan recorded here that the provider refused is
    a tenant on a plan nobody is charging for.
    """
    account = _account(db, organisation.id)
    provider = get_provider()
    result = provider.change_plan(account, payload.plan)
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The billing provider refused the change: {result.detail}",
        )
    previous = account.plan
    account.plan = payload.plan
    if result.reference:
        account.external_provider = provider.name
        account.external_customer_ref = result.reference
    audit.record(
        AuditAction.QUOTA_READ,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="billing_account",
        target_id=account.id,
        detail={"from": previous.value, "to": payload.plan.value, "provider": provider.name},
    )
    db.commit()
    return read_billing_account(organisation, db)


@router.post("/{organisation_id}/billing/credit", response_model=BillingAccountRead)
def add_credit(
    payload: CreditPurchase,
    organisation: OwnerOrganisation,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> BillingAccountRead:
    """Buy prepaid credit.

    **The payment is taken before the balance moves**, and the balance only
    moves if it succeeded. The other order — credit first, charge after —
    hands out credit whenever the provider is unreachable, which is the failure
    an attacker would go looking for.

    A deployment with no provider refuses this rather than granting it: a build
    that cannot take money must not be a way to mint credit.
    """
    account = _account(db, organisation.id)
    provider = get_provider()
    result = provider.purchase_credit(
        account, amount_minor=payload.amount_minor, currency=account.currency
    )
    if not result.ok:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=result.detail
        )
    try:
        grant_credit(db, account, amount_minor=payload.amount_minor)
    except PaymentError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.QUOTA_READ,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="billing_account",
        target_id=account.id,
        detail={
            "credit_minor": payload.amount_minor,
            "provider": provider.name,
            "reference": result.reference,
        },
    )
    db.commit()
    return read_billing_account(organisation, db)
