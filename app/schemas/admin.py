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


class AiCacheHealthRead(BaseModel):
    """Whether the prompt cache is working, as `app/ai/cache_health.py` reads it (ROAD_TO_10 1.11).

    Every rate is **None** rather than 0.0 when there is nothing to divide by or too few turns to
    compare: a cache that is not measured and a cache that is missing are opposite states, and
    the console renders the first as a sentence, not as 0%. `reported` is false when no turn in
    the window recorded a cached count -- the provider does not say, so the rate means nothing.
    `alert` is the sentence the operator reads when the recent turns fell well below the earlier
    ones; it is None otherwise.
    """

    turns: int
    prompt_tokens: int
    cached_prompt_tokens: int
    hit_rate: float | None
    recent_turns: int
    recent_hit_rate: float | None
    reported: bool
    alert: str | None


class SiteLatencyRead(BaseModel):
    """One timed site's recent durations, from this worker's span ledger (ROAD_TO_10 9.6)."""

    name: str
    seen: int
    failures: int
    window_size: int
    median_seconds: float
    p95_seconds: float
    max_seconds: float
    #: True when `window_size` is too small for p95 to be anything but the maximum.
    p95_is_the_maximum: bool


class SpanLedgerRead(BaseModel):
    #: The sentence that says whose view this is: one process, not the fleet.
    scope: str
    sites: list[SiteLatencyRead]


class TurnCostRead(BaseModel):
    turns: int
    priced_turns: int
    #: Turns with no configured price. Counted apart: unpriced is not free.
    unpriced_turns: int
    total_micro_usd: int
    mean_micro_usd: int | None
    max_micro_usd: int | None
    median_wall_ms: int | None
    p95_wall_ms: int | None
    by_stop_reason: dict[str, int]


class OperationLatencyRead(BaseModel):
    tool: str
    count: int
    failures: int
    median_ms: int
    p95_ms: int
    max_ms: int
    p95_is_the_maximum: bool


class BridgeLatencyRead(BaseModel):
    operations: list[OperationLatencyRead]
    #: True when the window held more rows than were read; the figures are for the newest.
    truncated: bool


class ObservabilityRead(BaseModel):
    """What the operator reads to answer "is it slow, and where" (ROAD_TO_10 9.6)."""

    window_hours: int
    spans: SpanLedgerRead
    turns: TurnCostRead
    ai_cache: AiCacheHealthRead
    queue_depth: dict[str, int]
    bridge: BridgeLatencyRead


class HardwareRead(BaseModel):
    """What this machine has, as `app/core/hardware.py` read it (ROAD_TO_10 6.1).

    `physical_cores` and `total_ram_mb` are **None** where the platform does not say, and
    `notes` explains each one and anything that made a figure smaller than the host's own (a
    container limit, a CPU affinity mask). A blank would read as zero.
    """

    logical_cores: int
    physical_cores: int | None
    total_ram_mb: int | None
    source: str
    notes: list[str]


class ComputePlanRead(BaseModel):
    """The worker and thread counts in force and where each came from (ROAD_TO_10 6.2)."""

    job_workers: int
    solver_threads: int
    reserved_cores: int
    physical_cores: int
    physical_assumed: bool
    basis: dict[str, str]
    oversubscribed: bool


class ComputeHealthRead(BaseModel):
    """The machine, what is free on it now, and the plan derived from it.

    `available_ram_mb` is live on every read and is None where it cannot be read; the other two
    are fixed for the life of the process. Staff-only like the rest of `/admin/health`: a
    machine's specification is not for the public status page.
    """

    hardware: HardwareRead
    available_ram_mb: int | None
    plan: ComputePlanRead


class ComputeScalingRead(BaseModel):
    """The worker count the job table asks for (E15.2), and why.

    A recommendation, not an action: nothing in the application resizes a fleet.
    `capped` is true when `max_workers` cut the answer short, so a clipped number
    is never read as "the fleet is big enough".
    """

    desired_workers: int
    current_workers: int | None
    reason: str
    capped: bool
    policy: str
    queued: int
    running: int
    oldest_wait_s: float | None
    min_workers: int
    max_workers: int
    jobs_per_worker: int
    target_wait_s: float


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
    #: The prompt-cache hit rate of the agent's turns in the window (ROAD_TO_10 1.11). Added
    #: 2026-10-04; additive, so an older console that does not read it is unaffected.
    ai_cache: AiCacheHealthRead
    #: The machine and the worker plan derived from it (ROAD_TO_10 6.1/6.2). Added 2026-10-05;
    #: additive, so a console that does not read it is unaffected.
    compute: ComputeHealthRead
