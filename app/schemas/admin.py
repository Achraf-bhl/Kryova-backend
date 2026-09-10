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

from app.core.lifecycle import DELETION_GRACE_DAYS
from app.models.audit import AuditAction, AuditOutcome, ImpersonationMode, StaffRole
from app.models.platform import AnnouncementLevel
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


# ---------------------------------------------------------------------------
# Account lifecycle (P3.4)
# ---------------------------------------------------------------------------


class SuspensionCreate(BaseModel):
    #: Required, and refused when blank by `core/lifecycle.suspend` as well. It
    #: goes in the audit log and in the email the user receives, so "because"
    #: is the difference between a support conversation and an argument.
    reason: str = Field(min_length=3, max_length=2000)


class DeletionCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
    grace_days: int = Field(default=DELETION_GRACE_DAYS, ge=1, le=365)


class LifecycleRead(BaseModel):
    """An account's standing, after a lifecycle change."""

    user_id: str
    is_active: bool
    suspended_at: datetime | None
    suspension_reason: str | None
    deletion_scheduled_at: datetime | None


class PurgeRead(BaseModel):
    """What a purge erased. Reported rather than assumed."""

    user_id: str
    projects: int
    media_rows: int
    blobs_removed: int
    share_links_revoked: int


# ---------------------------------------------------------------------------
# Feature flags (P3.5)
# ---------------------------------------------------------------------------


class FeatureFlagCreate(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9._-]+$")
    description: str = Field(default="", max_length=2000)
    enabled: bool = False
    rollout_percentage: int = Field(default=0, ge=0, le=100)


class FeatureFlagUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    #: The kill switch. Outranks every override — see `core/flags.py`.
    killed: bool | None = None


class FeatureFlagOverrideCreate(BaseModel):
    enabled: bool
    organisation_id: str | None = None
    user_id: str | None = None


class FeatureFlagOverrideRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str | None
    user_id: str | None
    enabled: bool


class FeatureFlagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    description: str
    enabled: bool
    rollout_percentage: int
    killed: bool
    overrides: list[FeatureFlagOverrideRead] = []


# ---------------------------------------------------------------------------
# Announcements and maintenance (P3.7)
# ---------------------------------------------------------------------------


class AnnouncementCreate(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    level: AnnouncementLevel = AnnouncementLevel.INFO
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class AnnouncementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message: str
    level: AnnouncementLevel
    starts_at: datetime
    ends_at: datetime | None
    withdrawn_at: datetime | None


class MaintenanceCreate(BaseModel):
    #: The operator's note, for the incident review.
    reason: str = Field(min_length=3, max_length=2000)
    #: What the user is told. Never `reason` — see `core/maintenance.py`.
    message: str = Field(min_length=3, max_length=2000)
    expected_end_at: datetime | None = None
    allow_staff: bool = True


class MaintenanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    reason: str
    message: str
    started_at: datetime
    ended_at: datetime | None
    expected_end_at: datetime | None
    allow_staff: bool


class MaintenanceNotice(BaseModel):
    message: str
    expected_end_at: datetime | None = None


class PlatformStateRead(BaseModel):
    """What the frontend needs to know before it renders anything (P3.5, P3.7).

    One request, three answers, because they are read at the same moment and by
    the same shell. `flags` is **server-evaluated** — the UI must never
    re-decide, or the button and the endpoint can disagree.
    """

    flags: dict[str, bool] = {}
    announcements: list[AnnouncementRead] = []
    #: Present only while read-only mode is on. `message` is the user-facing
    #: half; the operator's `reason` is deliberately not in this model.
    maintenance: MaintenanceNotice | None = None


# ---------------------------------------------------------------------------
# Fleet health (P3.6)
# ---------------------------------------------------------------------------


class FailureClassRead(BaseModel):
    """One recurring solver failure and how often it happened."""

    reason: str
    count: int


class FleetHealthRead(BaseModel):
    """"Is Kryova healthy", with one answer and no estimates.

    `success_rate` is **None** rather than 0.0 when nothing finished in the
    window: no runs to judge and every run failing are opposite states, and a
    dashboard that shows 0% on a quiet night sends somebody hunting an outage
    that is not there.

    `failure_grouping` says how `failures` was computed. It is grouped on the
    runner's recorded message because that is what the schema holds today; a
    taxonomy class on the job row is E15 task 5. Naming the method is how the
    console avoids presenting a coarse grouping as a classification.
    """

    window_hours: int
    #: Every job ever, by status — the standing queue.
    queue_depth: dict[str, int]
    #: Jobs created inside the window, by status.
    jobs_in_window: dict[str, int]
    success_rate: float | None
    failures: list[FailureClassRead]
    failure_grouping: str
    storage_bytes: int
    storage_bytes_added_in_window: int
    live_sessions: int
    users_total: int
    users_suspended: int
    users_awaiting_deletion: int
    #: Whether this deployment can actually email anybody (P1.5). A platform
    #: whose password resets go to a log file is unhealthy in a way no job
    #: counter shows.
    mail_delivers: bool
    maintenance_active: bool
