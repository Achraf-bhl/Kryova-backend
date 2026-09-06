"""Payloads for the operations console (P3.4, P3.6) and the audit log (P3.1).

Two things here are deliberate and easy to undo by accident.

**`AuditEventRead` never collapses the two identities.** There is no
`user_id` field and there will not be one: a reader who is shown a single
"user" cannot tell an impersonated action from the subject's own, which is the
failure the whole phase exists to prevent. Both pairs are always present, and
`impersonated` says which case this is without the reader having to compare
them.

**`OrganisationUsageRead` says how each number was obtained.** Storage is
attributed through the *owner* of a media row, because media rows carry no
organisation, so a member of two organisations has their bytes counted against
both. That is a real limitation of the current schema; the field naming it is
how the console avoids presenting an estimate as a measurement (Decision 3).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit import AuditAction, AuditOutcome, ImpersonationMode, StaffRole
from app.models.simulation import JobStatus


class StaffRead(BaseModel):
    """The caller's own standing, so the frontend can draw the right console."""

    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str | None
    role: StaffRole
    granted_at: datetime


class AdminUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str | None
    is_active: bool
    created_at: datetime
    staff_role: StaffRole | None = None
    organisation_count: int = 0


class AdminOrganisationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    is_personal: bool
    created_at: datetime
    member_count: int = 0
    project_count: int = 0


class AdminJobRead(BaseModel):
    """A job as the console sees it: with the tenant it belongs to attached.

    `organisation_id` is resolved through the project, because a job row has no
    tenant column of its own -- and an operations console that cannot say which
    customer a stuck job belongs to cannot be used to answer the call about it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    organisation_id: str | None
    status: JobStatus
    solver: str
    solver_version: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobFailRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class OrganisationUsageRead(BaseModel):
    organisation_id: str
    projects: int
    geometry_versions: int
    #: Job counts keyed by `JobStatus` value, so a queue backing up is visible.
    jobs_by_status: dict[str, int]
    storage_bytes: int
    #: How `storage_bytes` was arrived at. Never omitted: an approximated number
    #: that does not say it is approximated is worse than no number.
    storage_attribution: str = "summed over media owned by this organisation's members"
    #: The limits actually in force. They come from settings today rather than
    #: from a per-tenant quota row, and the field says so rather than implying
    #: the console can vary them -- per-tenant quotas are P3.4/P3.5 work.
    quota_source: str = "global settings; per-tenant quotas are not implemented"
    max_concurrent_simulations_per_user: int
    max_media_bytes: int
    ai_daily_token_budget: int


class ImpersonationStart(BaseModel):
    subject_user_id: str = Field(min_length=1, max_length=36)
    #: Required, and not defaulted. "Because I could" is the answer an audit log
    #: gets when the reason is optional.
    reason: str = Field(min_length=8, max_length=500)


class ImpersonationEscalate(BaseModel):
    #: A *second* reason, not the first one repeated. Looking at an account and
    #: writing to it are different decisions and need different justifications.
    reason: str = Field(min_length=8, max_length=500)


class ImpersonationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_user_id: str
    subject_user_id: str
    mode: ImpersonationMode
    reason: str
    escalation_reason: str | None
    expires_at: datetime
    ended_at: datetime | None
    created_at: datetime


class ImpersonationIssued(ImpersonationRead):
    """The one response carrying the token, exactly as an invitation does.

    Returned only when the session is created. Escalation does **not** mint a
    new one, and that is the point of the session row: write mode is a property
    of the session, read on every request, so the token already in the staff
    member's hands starts working the moment an administrator escalates and
    stops the moment anybody ends it.
    """

    token: str


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    occurred_at: datetime
    action: AuditAction
    outcome: AuditOutcome
    actor_user_id: str | None
    actor_email: str | None
    subject_user_id: str | None
    subject_email: str | None
    impersonated: bool
    organisation_id: str | None
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    user_agent: str | None
    reason: str | None
    detail: dict[str, Any] | None
    previous_hash: str | None
    entry_hash: str


class ChainVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    intact: bool
    checked: int
    broken_at: int | None
    problem: str | None
    summary: str
