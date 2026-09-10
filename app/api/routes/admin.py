"""The operations console and the audit log (Phase P3).

Two routers live here and the split is the point.

`router` is `/admin`: the platform surface, reachable only by staff, guarded by
`require_staff` in `api/deps.py`. **Everything in it is read-only except three
routes** -- retry a job, fail a job, and the impersonation lifecycle -- and each
of those three writes an audit entry whether it succeeds or is refused.

`organisation_audit_router` is `/organisations/{id}/audit`: an organisation's
own slice of the log, for its owner. It is *not* a staff route and does not
consult staff standing at all, which is what makes it answer 404 to a staff
member who is not a member of that organisation. P3.1 promises enterprise
buyers their own audit view; a view that quietly widened for Kryova's own
employees would not be that.

Three rules hold across every route below:

- **A miss is 404, never 403.** For an ordinary user the whole `/admin` tree is
  simply not there -- a 403 would tell them where the console is and that their
  account is merely the wrong one.
- **No route special-cases staff into a tenant.** Staff standing opens the
  console; it opens no customer's data. The only way to a tenant's work is
  impersonation, which is recorded on every request it makes.
- **A refused action is logged.** It is the entry an incident actually turns
  on, and it is the one a naive implementation drops, because nothing happened.

There is deliberately no route that creates or revokes a staff grant. See
`StaffGrant`'s docstring: an endpoint that mints staff is an escalation path
from whichever staff account is compromised first.
"""

import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app import mail
from app.api.deps import (
    AuditDep,
    DbSession,
    JobQueueDep,
    MediaServiceDep,
    MediaStoreDep,
    OperatorStaff,
    OwnerOrganisation,
    PlatformAdminStaff,
    PrincipalDep,
    SessionScopeDep,
    SupportStaff,
)
from app.core import flags, lifecycle, maintenance
from app.core.audit import AuditService, Principal, audit_page
from app.core.config import settings
from app.core.security import create_impersonation_token
from app.models import (
    Announcement,
    AuditAction,
    AuditEvent,
    AuditOutcome,
    FeatureFlag,
    GeometryVersion,
    ImpersonationMode,
    ImpersonationSession,
    JobStatus,
    MaintenanceWindow,
    Media,
    Membership,
    Organisation,
    Project,
    SimulationJob,
    StaffRole,
    User,
    UserSession,
    live_staff_grant,
)
from app.models.base import utcnow
from app.schemas.admin import (
    AdminJobRead,
    AdminOrganisationRead,
    AdminUserRead,
    AnnouncementCreate,
    AnnouncementRead,
    AuditEventRead,
    ChainVerificationRead,
    DeletionCreate,
    FailureClassRead,
    FeatureFlagCreate,
    FeatureFlagOverrideCreate,
    FeatureFlagRead,
    FeatureFlagUpdate,
    FleetHealthRead,
    ImpersonationEscalate,
    ImpersonationIssued,
    ImpersonationRead,
    ImpersonationStart,
    JobFailRequest,
    LifecycleRead,
    MaintenanceCreate,
    MaintenanceRead,
    OrganisationUsageRead,
    PurgeRead,
    StaffRead,
    SuspensionCreate,
)
from app.schemas.pagination import Page
from app.simulation.runner import run_simulation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])
organisation_audit_router = APIRouter(prefix="/organisations", tags=["admin"])

#: How long a read-only session lasts, and how long it lasts once escalated.
#: The second is shorter, and shortening on escalation rather than extending is
#: the direction that matters: a session that gains power should not also gain
#: time. `IMPERSONATION_WRITE_TTL` is a ceiling, never an extension -- escalating
#: a session with eight minutes left leaves it with eight.
IMPERSONATION_READ_TTL = timedelta(minutes=30)
IMPERSONATION_WRITE_TTL = timedelta(minutes=15)

AuditPage = Page[AuditEventRead]
_NOT_FOUND = "Not found"


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)


# ---------------------------------------------------------------------------
# Who is asking
# ---------------------------------------------------------------------------


@router.get("/whoami", response_model=StaffRead)
def read_own_staff_standing(staff: SupportStaff, principal: PrincipalDep) -> StaffRead:
    """What the console may draw. Never a claim about a tenant."""
    return StaffRead(
        user_id=principal.actor_user_id,
        email=principal.actor_email,
        role=staff.role,
        granted_at=staff.created_at,
    )


# ---------------------------------------------------------------------------
# Organisations and users (P3.4, read-only)
# ---------------------------------------------------------------------------


@router.get("/organisations", response_model=Page[AdminOrganisationRead])
def list_organisations(
    staff: SupportStaff,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdminOrganisationRead]:
    conditions = []
    if q:
        pattern = f"%{q.lower()}%"
        conditions.append(
            func.lower(Organisation.name).like(pattern)
            | func.lower(Organisation.slug).like(pattern)
        )
    total = db.scalar(select(func.count()).select_from(Organisation).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Organisation)
            .where(*conditions)
            .order_by(Organisation.created_at.desc(), Organisation.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[AdminOrganisationRead](
        total=total,
        page=page,
        page_size=page_size,
        items=[_organisation_view(db, organisation) for organisation in rows],
    )


@router.get("/organisations/{organisation_id}", response_model=AdminOrganisationRead)
def read_organisation(
    organisation_id: str, staff: SupportStaff, db: DbSession
) -> AdminOrganisationRead:
    organisation = db.get(Organisation, organisation_id)
    if organisation is None:
        raise _not_found()
    return _organisation_view(db, organisation)


@router.get("/organisations/{organisation_id}/usage", response_model=OrganisationUsageRead)
def read_organisation_usage(
    organisation_id: str, staff: SupportStaff, db: DbSession
) -> OrganisationUsageRead:
    """Quota and usage for one tenant (P3.6).

    Every number here is counted, and the two that are not directly countable
    say how they were derived instead of quietly rounding: storage is attributed
    through media ownership because media rows have no tenant, and the quotas
    are the global settings in force, because per-tenant quota rows do not exist
    yet. Presenting either as something it is not would make the console a place
    where wrong numbers look official.
    """
    organisation = db.get(Organisation, organisation_id)
    if organisation is None:
        raise _not_found()

    project_ids = list(
        db.scalars(select(Project.id).where(Project.organisation_id == organisation_id))
    )
    geometry_versions = 0
    jobs_by_status = {member.value: 0 for member in JobStatus}
    if project_ids:
        geometry_versions = (
            db.scalar(
                select(func.count())
                .select_from(GeometryVersion)
                .where(GeometryVersion.project_id.in_(project_ids))
            )
            or 0
        )
        for job_status, count in db.execute(
            select(SimulationJob.status, func.count())
            .where(SimulationJob.project_id.in_(project_ids))
            .group_by(SimulationJob.status)
        ).all():
            jobs_by_status[JobStatus(job_status).value] = count

    member_ids = list(
        db.scalars(select(Membership.user_id).where(Membership.organisation_id == organisation_id))
    )
    storage_bytes = 0
    if member_ids:
        storage_bytes = (
            db.scalar(
                select(func.coalesce(func.sum(Media.size_bytes), 0)).where(
                    Media.owner_id.in_(member_ids)
                )
            )
            or 0
        )

    return OrganisationUsageRead(
        organisation_id=organisation_id,
        projects=len(project_ids),
        geometry_versions=geometry_versions,
        jobs_by_status=jobs_by_status,
        storage_bytes=int(storage_bytes),
        max_concurrent_simulations_per_user=settings.max_concurrent_simulations_per_user,
        max_media_bytes=settings.max_media_bytes,
        ai_daily_token_budget=settings.ai_daily_token_budget,
    )


@router.get("/users", response_model=Page[AdminUserRead])
def list_users(
    staff: SupportStaff,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=320)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdminUserRead]:
    conditions = []
    if q:
        pattern = f"%{q.lower()}%"
        conditions.append(
            func.lower(User.email).like(pattern) | func.lower(User.full_name).like(pattern)
        )
    total = db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(User)
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[AdminUserRead](
        total=total, page=page, page_size=page_size, items=[_user_view(db, row) for row in rows]
    )


@router.get("/users/{user_id}", response_model=AdminUserRead)
def read_user(user_id: str, staff: SupportStaff, db: DbSession) -> AdminUserRead:
    user = db.get(User, user_id)
    if user is None:
        raise _not_found()
    return _user_view(db, user)


# ---------------------------------------------------------------------------
# Jobs (P3.6). Reading is free; the two mutations are audited either way.
# ---------------------------------------------------------------------------


@router.get("/jobs", response_model=Page[AdminJobRead])
def list_jobs(
    staff: SupportStaff,
    db: DbSession,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[AdminJobRead]:
    conditions = [] if job_status is None else [SimulationJob.status == job_status]
    total = db.scalar(select(func.count()).select_from(SimulationJob).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(SimulationJob)
            .where(*conditions)
            .order_by(SimulationJob.created_at.desc(), SimulationJob.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page[AdminJobRead](
        total=total, page=page, page_size=page_size, items=[_job_view(db, job) for job in rows]
    )


@router.get("/jobs/{job_id}", response_model=AdminJobRead)
def read_job(job_id: str, staff: SupportStaff, db: DbSession) -> AdminJobRead:
    return _job_view(db, _job_or_404(db, job_id))


@router.post("/jobs/{job_id}/retry", response_model=AdminJobRead)
def retry_job(
    job_id: str,
    staff: OperatorStaff,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
    store: MediaStoreDep,
    queue: JobQueueDep,
    session_scope: SessionScopeDep,
) -> AdminJobRead:
    """Put a failed or stuck job back on the queue.

    Refused for a job that is queued or already succeeded, and **the refusal is
    logged**: "an operator tried to re-run a successful job at 03:12" is the
    line that explains a duplicate result to whoever finds one later.
    """
    job = _job_or_404(db, job_id)
    organisation_id = _job_organisation_id(db, job)
    if job.status not in (JobStatus.FAILED, JobStatus.RUNNING):
        _record_job_action(
            audit,
            AuditAction.JOB_RETRIED,
            AuditOutcome.REFUSED,
            principal,
            job,
            organisation_id,
            reason=f"job is {job.status.value}; only a failed or stuck run may be retried",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"This job is {job.status.value}. Only a failed run, or one stuck in "
                "running, can be retried."
            ),
        )

    previous_status = job.status
    job.status = JobStatus.QUEUED
    job.error = None
    job.started_at = None
    job.finished_at = None
    db.commit()

    # After the commit, never before: an entry claiming a retry that a failed
    # commit rolled back is a false record, and a false record is worse than a
    # missing one.
    _record_job_action(
        audit,
        AuditAction.JOB_RETRIED,
        AuditOutcome.SUCCEEDED,
        principal,
        job,
        organisation_id,
        detail={"previous_status": previous_status.value},
    )
    queue.submit(lambda: run_simulation(job.id, session_scope, store))
    db.refresh(job)
    return _job_view(db, job)


@router.post("/jobs/{job_id}/fail", response_model=AdminJobRead)
def fail_job(
    payload: JobFailRequest,
    job_id: str,
    staff: OperatorStaff,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
) -> AdminJobRead:
    """Close out a job that will never finish, with a reason on the record.

    The reason is written onto the job row *and* into the log, because they
    answer different questions: the row tells the engineer waiting on a result
    why it stopped, and the log says who decided that and when.
    """
    job = _job_or_404(db, job_id)
    organisation_id = _job_organisation_id(db, job)
    if job.status.is_terminal:
        _record_job_action(
            audit,
            AuditAction.JOB_FAILED,
            AuditOutcome.REFUSED,
            principal,
            job,
            organisation_id,
            reason=f"job is already {job.status.value}",
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This job has already finished ({job.status.value}); there is nothing to fail.",
        )

    job.status = JobStatus.FAILED
    job.error = f"Failed by Kryova operations: {payload.reason}"
    job.finished_at = utcnow()
    db.commit()
    _record_job_action(
        audit,
        AuditAction.JOB_FAILED,
        AuditOutcome.SUCCEEDED,
        principal,
        job,
        organisation_id,
        reason=payload.reason,
    )
    return _job_view(db, job)


# ---------------------------------------------------------------------------
# Impersonation (P3.3)
# ---------------------------------------------------------------------------


@router.post(
    "/impersonation", response_model=ImpersonationIssued, status_code=status.HTTP_201_CREATED
)
def start_impersonation(
    payload: ImpersonationStart,
    staff: SupportStaff,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
) -> ImpersonationIssued:
    """Open a **read-only**, time-boxed session as another user.

    Read-only is not an option the caller passes; there is no parameter for it
    on this route at all, so there is no value anybody can send that starts a
    write session. Escalation is a separate, differently-privileged call with
    its own reason, and both ends of that are in the log.

    Two subjects are refused, and both refusals are recorded. Yourself, because
    a session where the actor and the subject are the same person records
    nothing useful and blurs the one distinction the log exists to keep. And
    anyone who holds a staff grant, because impersonating a colleague is how a
    support account reaches platform-administrator power without anybody
    granting it.
    """
    subject = db.get(User, payload.subject_user_id)
    if subject is None or not subject.is_active:
        _record_impersonation_refusal(
            audit, principal, payload.subject_user_id, payload.reason, "no such active user"
        )
        raise _not_found()
    if subject.id == principal.actor_user_id:
        _record_impersonation_refusal(
            audit, principal, subject.id, payload.reason, "an actor may not impersonate themselves"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You are already yourself. Impersonation is for acting as another user.",
        )
    if live_staff_grant(db, subject.id) is not None:
        _record_impersonation_refusal(
            audit, principal, subject.id, payload.reason, "the subject holds a staff grant"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That account is staff. Impersonating a colleague would let one staff role "
                "borrow another's; ask them to act for themselves."
            ),
        )

    session_row = ImpersonationSession(
        actor_user_id=principal.actor_user_id,
        subject_user_id=subject.id,
        mode=ImpersonationMode.READ,
        reason=payload.reason,
        expires_at=utcnow() + IMPERSONATION_READ_TTL,
    )
    db.add(session_row)
    db.commit()

    audit.record(
        AuditAction.IMPERSONATION_STARTED,
        AuditOutcome.SUCCEEDED,
        principal=_as_impersonating(principal, subject, session_row.id),
        target_type="user",
        target_id=subject.id,
        reason=payload.reason,
        detail={
            "mode": ImpersonationMode.READ.value,
            "expires_at": session_row.expires_at.isoformat(),
        },
    )
    token = create_impersonation_token(
        actor_id=principal.actor_user_id,
        subject_id=subject.id,
        session_id=session_row.id,
        expires_at=session_row.expires_at,
    )
    return ImpersonationIssued(
        **ImpersonationRead.model_validate(session_row).model_dump(), token=token
    )


@router.get("/impersonation", response_model=Page[ImpersonationRead])
def list_impersonation_sessions(
    staff: SupportStaff,
    db: DbSession,
    live_only: bool = True,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ImpersonationRead]:
    conditions: list[ColumnElement[bool]] = []
    if live_only:
        conditions.append(ImpersonationSession.ended_at.is_(None))
        conditions.append(ImpersonationSession.expires_at > utcnow())
    total = (
        db.scalar(select(func.count()).select_from(ImpersonationSession).where(*conditions)) or 0
    )
    rows = db.scalars(
        select(ImpersonationSession)
        .where(*conditions)
        .order_by(ImpersonationSession.created_at.desc(), ImpersonationSession.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page[ImpersonationRead](
        total=total,
        page=page,
        page_size=page_size,
        items=[ImpersonationRead.model_validate(row) for row in rows],
    )


@router.post("/impersonation/{session_id}/escalate", response_model=ImpersonationRead)
def escalate_impersonation(
    payload: ImpersonationEscalate,
    session_id: str,
    staff: PlatformAdminStaff,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
) -> ImpersonationRead:
    """Grant write access to a live session, on the record.

    Three things make this the "second confirmation" P3.3 asks for rather than a
    formality: it is a **different call** from the one that started the session,
    it needs **platform administrator** standing where starting one needs only
    support, and it needs a **new reason** -- justifying a look is not
    justifying a change.

    No new token is issued, and that is the session row earning its place: write
    mode is read from the row on every request, so the token the staff member is
    already holding gains write the moment this returns and loses it the moment
    anybody ends the session. A token that carried the mode itself could not be
    de-escalated at all.
    """
    session_row = db.get(ImpersonationSession, session_id)
    # Somebody else's session is not found, not forbidden -- the same rule the
    # tenancy guards use, applied to the staff plane.
    if session_row is None or session_row.actor_user_id != principal.actor_user_id:
        raise _not_found()
    if not session_row.is_live():
        audit.record(
            AuditAction.IMPERSONATION_ESCALATED,
            AuditOutcome.REFUSED,
            principal=principal,
            target_type="impersonation_session",
            target_id=session_id,
            reason=payload.reason,
            detail={"refused_because": "the session has ended or expired"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That impersonation session has ended. Start a new one.",
        )

    session_row.mode = ImpersonationMode.WRITE
    session_row.escalation_reason = payload.reason
    # A ceiling, never an extension: `min` cannot move the deadline outwards.
    session_row.expires_at = min(session_row.expires_at, utcnow() + IMPERSONATION_WRITE_TTL)
    db.commit()

    subject = db.get(User, session_row.subject_user_id)
    audit.record(
        AuditAction.IMPERSONATION_ESCALATED,
        AuditOutcome.SUCCEEDED,
        principal=_as_impersonating(principal, subject, session_row.id),
        target_type="impersonation_session",
        target_id=session_row.id,
        reason=payload.reason,
        detail={
            "mode": ImpersonationMode.WRITE.value,
            "expires_at": session_row.expires_at.isoformat(),
            "opened_because": session_row.reason,
        },
    )
    return ImpersonationRead.model_validate(session_row)


@router.post("/impersonation/{session_id}/end", response_model=ImpersonationRead)
def end_impersonation(
    session_id: str,
    staff: SupportStaff,
    principal: PrincipalDep,
    db: DbSession,
    audit: AuditDep,
) -> ImpersonationRead:
    """End a session now. The staff member who opened it, or any administrator.

    Ending is idempotent and keeps the first `ended_at`, for the reason
    `UserSession.revoke` keeps the first revocation reason: the first is the
    true one, and a later housekeeping pass must not overwrite the moment
    somebody actually stopped.
    """
    session_row = db.get(ImpersonationSession, session_id)
    may_end = session_row is not None and (
        session_row.actor_user_id == principal.actor_user_id
        or staff.role.at_least(StaffRole.PLATFORM_ADMIN)
    )
    if session_row is None or not may_end:
        raise _not_found()

    already_ended = session_row.ended_at is not None
    if not already_ended:
        session_row.ended_at = utcnow()
        db.commit()
        subject = db.get(User, session_row.subject_user_id)
        audit.record(
            AuditAction.IMPERSONATION_ENDED,
            AuditOutcome.SUCCEEDED,
            principal=_as_impersonating(principal, subject, session_row.id),
            target_type="impersonation_session",
            target_id=session_row.id,
            reason=session_row.escalation_reason or session_row.reason,
            detail={"mode": session_row.mode.value},
        )
    return ImpersonationRead.model_validate(session_row)


# ---------------------------------------------------------------------------
# Reading the log (P3.1)
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=AuditPage)
def list_audit_events(
    staff: SupportStaff,
    db: DbSession,
    organisation_id: str | None = None,
    actor_user_id: str | None = None,
    subject_user_id: str | None = None,
    action: AuditAction | None = None,
    impersonated_only: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    total, rows = audit_page(
        db,
        organisation_id=organisation_id,
        actor_user_id=actor_user_id,
        subject_user_id=subject_user_id,
        action=action,
        impersonated_only=impersonated_only,
        page=page,
        page_size=page_size,
    )
    return AuditPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[AuditEventRead.model_validate(row) for row in rows],
    )


@router.get("/audit/verify", response_model=ChainVerificationRead)
def verify_audit_chain(staff: SupportStaff, audit: AuditDep) -> ChainVerificationRead:
    """Walk the whole chain and report the first break, if there is one.

    Whole, not a page: an entry's `previous_hash` refers to the row before it,
    so verifying a slice would either report a break that is not there or need
    an exception that is a hole to hide a row in.
    """
    result = audit.verify()
    return ChainVerificationRead(
        intact=result.intact,
        checked=result.checked,
        broken_at=result.broken_at,
        problem=result.problem,
        summary=result.summary,
    )


@organisation_audit_router.get("/{organisation_id}/audit", response_model=AuditPage)
def list_organisation_audit_events(
    organisation: OwnerOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """An organisation's own slice of the log, for its owner (P3.1).

    `OwnerOrganisation` and nothing else: this route never reads a staff grant,
    so a Kryova employee who is not a member of this organisation gets the same
    404 an unrelated customer does. That is the point -- the enterprise promise
    is "you can see what was done to you", not "you can see everyone".

    The filter is equality on `organisation_id`, so platform events, which carry
    no tenant, appear in nobody's slice.
    """
    total, rows = audit_page(
        db, organisation_id=organisation.id, page=page, page_size=page_size
    )
    return AuditPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[AuditEventRead.model_validate(row) for row in rows],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _organisation_view(db: Session, organisation: Organisation) -> AdminOrganisationRead:
    members = (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.organisation_id == organisation.id)
        )
        or 0
    )
    projects = (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.organisation_id == organisation.id)
        )
        or 0
    )
    return AdminOrganisationRead(
        id=organisation.id,
        name=organisation.name,
        slug=organisation.slug,
        is_personal=organisation.is_personal,
        created_at=organisation.created_at,
        member_count=members,
        project_count=projects,
    )


def _user_view(db: Session, user: User) -> AdminUserRead:
    grant = live_staff_grant(db, user.id)
    organisations = (
        db.scalar(select(func.count()).select_from(Membership).where(Membership.user_id == user.id))
        or 0
    )
    return AdminUserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        created_at=user.created_at,
        staff_role=grant.role if grant is not None else None,
        organisation_count=organisations,
    )


def _job_or_404(db: Session, job_id: str) -> SimulationJob:
    job = db.get(SimulationJob, job_id)
    if job is None:
        raise _not_found()
    return job


def _job_organisation_id(db: Session, job: SimulationJob) -> str | None:
    project = db.get(Project, job.project_id)
    return project.organisation_id if project is not None else None


def _job_view(db: Session, job: SimulationJob) -> AdminJobRead:
    return AdminJobRead(
        id=job.id,
        project_id=job.project_id,
        organisation_id=_job_organisation_id(db, job),
        status=job.status,
        solver=job.solver,
        solver_version=job.solver_version,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def _record_job_action(
    audit: AuditService,
    action: AuditAction,
    outcome: AuditOutcome,
    principal: Principal,
    job: SimulationJob,
    organisation_id: str | None,
    reason: str | None = None,
    detail: dict[str, object] | None = None,
) -> AuditEvent:
    return audit.record(
        action,
        outcome,
        principal=principal,
        organisation_id=organisation_id,
        target_type="simulation_job",
        target_id=job.id,
        reason=reason,
        detail=detail,
    )


def _record_impersonation_refusal(
    audit: AuditService, principal: Principal, subject_id: str, reason: str, refused_because: str
) -> None:
    """A session that was asked for and not given is still a request to record.

    Especially this one: "who tried to impersonate whom, and was stopped" is the
    first question after a support account is compromised, and it is exactly the
    row an implementation drops because nothing happened.
    """
    audit.record(
        AuditAction.IMPERSONATION_STARTED,
        AuditOutcome.REFUSED,
        principal=principal,
        target_type="user",
        target_id=subject_id,
        reason=reason,
        detail={"refused_because": refused_because},
    )


def _as_impersonating(
    principal: Principal, subject: User | None, session_id: str
) -> Principal:
    """The acting staff member, with the subject filled into the other half.

    The staff member's own request is not itself impersonated -- they are
    authenticated as themselves -- but the *event* is about both people, and an
    entry naming only the actor would leave "started impersonating whom" to be
    recovered from a free-text field. `impersonated` stays False, because it
    describes the request, and the subject columns carry the second identity.
    """
    if subject is None:
        return principal
    return Principal(
        actor_user_id=principal.actor_user_id,
        actor_email=principal.actor_email,
        subject_user_id=subject.id,
        subject_email=subject.email,
        impersonated=principal.impersonated,
        may_write=principal.may_write,
        session_id=session_id,
        reason=principal.reason,
        expires_at=principal.expires_at,
        ip_address=principal.ip_address,
        user_agent=principal.user_agent,
    )


__all__ = ["organisation_audit_router", "router"]


# ---------------------------------------------------------------------------
# Account lifecycle (P3.4)
# ---------------------------------------------------------------------------
#
# The only *destructive* routes in the console. Every one of them writes an
# audit entry, and every one of them tells the account holder by email — being
# suspended without being told is how a customer's first contact with the
# problem is a support ticket that starts "your product is broken".


def _lifecycle_view(user: User) -> LifecycleRead:
    return LifecycleRead(
        user_id=user.id,
        is_active=user.is_active,
        suspended_at=user.suspended_at,
        suspension_reason=user.suspension_reason,
        deletion_scheduled_at=user.deletion_scheduled_at,
    )


def _user_or_404(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise _not_found()
    return user


@router.post("/users/{user_id}/suspend", response_model=LifecycleRead)
def suspend_user(
    user_id: str,
    payload: SuspensionCreate,
    staff: PlatformAdminStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> LifecycleRead:
    """Withdraw access immediately and everywhere.

    `PlatformAdminStaff`, not operator: suspension ends somebody's ability to
    work, and the ladder in `deps.require_staff` puts the irreversible-feeling
    actions at the top on purpose.
    """
    user = _user_or_404(db, user_id)
    actor = db.get(User, principal.actor_user_id)
    if actor is None:  # pragma: no cover - the actor authenticated a line ago
        raise _not_found()
    try:
        lifecycle.suspend(db, user, reason=payload.reason, by=actor)
    except lifecycle.LifecycleError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.USER_SUSPENDED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="user",
        target_id=user.id,
        reason=payload.reason,
    )
    db.commit()
    mail.send(
        mail.templates.suspension_notice(
            to=user.email, reason=payload.reason, by="a Kryova administrator"
        )
    )
    return _lifecycle_view(user)


@router.post("/users/{user_id}/reinstate", response_model=LifecycleRead)
def reinstate_user(
    user_id: str,
    staff: PlatformAdminStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> LifecycleRead:
    user = _user_or_404(db, user_id)
    try:
        lifecycle.reinstate(db, user)
    except lifecycle.LifecycleError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.USER_REINSTATED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    return _lifecycle_view(user)


@router.post("/users/{user_id}/deletion", response_model=LifecycleRead)
def schedule_user_deletion(
    user_id: str,
    payload: DeletionCreate,
    staff: PlatformAdminStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> LifecycleRead:
    """Schedule erasure after a grace window (GDPR, P3.4).

    Scheduled, never immediate. The window is the feature: a deletion nobody can
    stop for thirty days is a support ticket, and one nobody can stop at all is
    a disaster. The email names the date, because a confirmation with no
    deadline gives the one person who can undo it no reason to act today.
    """
    user = _user_or_404(db, user_id)
    actor = db.get(User, principal.actor_user_id)
    if actor is None:  # pragma: no cover
        raise _not_found()
    try:
        purge_at = lifecycle.schedule_deletion(
            db, user, by=actor, grace_days=payload.grace_days
        )
    except lifecycle.LifecycleError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.USER_DELETION_SCHEDULED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="user",
        target_id=user.id,
        reason=payload.reason,
        detail={"purge_at": purge_at.isoformat()},
    )
    db.commit()
    mail.send(
        mail.templates.deletion_scheduled(
            to=user.email, purge_at=purge_at, by="a Kryova administrator"
        )
    )
    return _lifecycle_view(user)


@router.delete("/users/{user_id}/deletion", response_model=LifecycleRead)
def cancel_user_deletion(
    user_id: str,
    staff: PlatformAdminStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> LifecycleRead:
    user = _user_or_404(db, user_id)
    try:
        lifecycle.cancel_deletion(db, user)
    except lifecycle.LifecycleError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.USER_DELETION_CANCELLED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    return _lifecycle_view(user)


@router.post("/users/{user_id}/purge", response_model=PurgeRead)
def purge_user(
    user_id: str,
    staff: PlatformAdminStaff,
    db: DbSession,
    media: MediaServiceDep,
    audit: AuditDep,
    principal: PrincipalDep,
) -> PurgeRead:
    """Erase now. **Irreversible**, and refused before the grace window is up.

    The refusal is the guard: an administrator who can purge on the same day
    they schedule has a grace window in name only, and the whole point of the
    window is that a mistake is recoverable for thirty days. A genuine
    emergency ends the account with `suspend`, which is immediate and reversible.
    """
    user = _user_or_404(db, user_id)
    scheduled = user.deletion_scheduled_at
    if scheduled is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Schedule the deletion first. Purging is what happens when the window ends.",
        )
    if scheduled > lifecycle.utcnow():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The grace window runs until {scheduled:%d %B %Y}. "
                "Suspend the account if it needs to stop being usable now."
            ),
        )
    email = user.email
    report = lifecycle.purge(db, user, media)
    audit.record(
        AuditAction.USER_PURGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="user",
        target_id=user_id,
        detail={"email": email, "projects": report.projects, "blobs": report.blobs_removed},
    )
    db.commit()
    return PurgeRead(**vars(report))


# ---------------------------------------------------------------------------
# Feature flags (P3.5)
# ---------------------------------------------------------------------------


@router.get("/flags", response_model=list[FeatureFlagRead])
def list_flags(staff: SupportStaff, db: DbSession) -> list[FeatureFlagRead]:
    rows = db.scalars(select(FeatureFlag).order_by(FeatureFlag.key)).all()
    return [FeatureFlagRead.model_validate(row) for row in rows]


@router.post("/flags", response_model=FeatureFlagRead, status_code=status.HTTP_201_CREATED)
def create_flag(
    payload: FeatureFlagCreate,
    staff: OperatorStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> FeatureFlagRead:
    if db.scalar(select(FeatureFlag).where(FeatureFlag.key == payload.key)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"The flag '{payload.key}' exists."
        )
    flag = FeatureFlag(**payload.model_dump())
    db.add(flag)
    db.flush()
    audit.record(
        AuditAction.FLAG_CHANGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="feature_flag",
        target_id=flag.key,
        detail={"created": payload.model_dump()},
    )
    db.commit()
    return FeatureFlagRead.model_validate(flag)


@router.patch("/flags/{key}", response_model=FeatureFlagRead)
def update_flag(
    key: str,
    payload: FeatureFlagUpdate,
    staff: OperatorStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> FeatureFlagRead:
    """Change a flag, including pulling its kill switch.

    `OperatorStaff` rather than platform admin, deliberately: killing a flag is
    the action somebody takes at 03:00 when a feature is hurting people, and
    putting it behind the highest role means waiting for whoever holds it.
    """
    flag = db.scalar(select(FeatureFlag).where(FeatureFlag.key == key))
    if flag is None:
        raise _not_found()
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(flag, field, value)
    audit.record(
        AuditAction.FLAG_CHANGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="feature_flag",
        target_id=flag.key,
        detail=changes,
    )
    db.commit()
    return FeatureFlagRead.model_validate(flag)


@router.post("/flags/{key}/overrides", response_model=FeatureFlagRead)
def override_flag(
    key: str,
    payload: FeatureFlagOverrideCreate,
    staff: OperatorStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> FeatureFlagRead:
    flag = db.scalar(select(FeatureFlag).where(FeatureFlag.key == key))
    if flag is None:
        raise _not_found()
    try:
        flags.set_override(
            db,
            flag,
            enabled=payload.enabled,
            organisation_id=payload.organisation_id,
            user_id=payload.user_id,
        )
    except ValueError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(refused)
        ) from refused
    audit.record(
        AuditAction.FLAG_CHANGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="feature_flag",
        target_id=flag.key,
        detail={"override": payload.model_dump()},
    )
    db.commit()
    db.refresh(flag)
    return FeatureFlagRead.model_validate(flag)


# ---------------------------------------------------------------------------
# Announcements and maintenance mode (P3.7)
# ---------------------------------------------------------------------------


@router.get("/announcements", response_model=list[AnnouncementRead])
def list_announcements(staff: SupportStaff, db: DbSession) -> list[AnnouncementRead]:
    rows = db.scalars(
        select(Announcement).order_by(Announcement.starts_at.desc()).limit(50)
    ).all()
    return [AnnouncementRead.model_validate(row) for row in rows]


@router.post(
    "/announcements", response_model=AnnouncementRead, status_code=status.HTTP_201_CREATED
)
def publish_announcement(
    payload: AnnouncementCreate,
    staff: OperatorStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> AnnouncementRead:
    announcement = Announcement(
        message=payload.message,
        level=payload.level,
        starts_at=payload.starts_at or lifecycle.utcnow(),
        ends_at=payload.ends_at,
        published_by_id=principal.actor_user_id,
    )
    db.add(announcement)
    db.flush()
    audit.record(
        AuditAction.ANNOUNCEMENT_PUBLISHED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="announcement",
        target_id=announcement.id,
        detail={"level": payload.level.value},
    )
    db.commit()
    return AnnouncementRead.model_validate(announcement)


@router.delete("/announcements/{announcement_id}", response_model=AnnouncementRead)
def withdraw_announcement(
    announcement_id: str, staff: OperatorStaff, db: DbSession
) -> AnnouncementRead:
    announcement = db.get(Announcement, announcement_id)
    if announcement is None:
        raise _not_found()
    announcement.withdrawn_at = lifecycle.utcnow()
    db.commit()
    return AnnouncementRead.model_validate(announcement)


@router.get("/maintenance", response_model=MaintenanceRead | None)
def read_maintenance(staff: SupportStaff, db: DbSession) -> MaintenanceRead | None:
    window = maintenance.active_window(db)
    return MaintenanceRead.model_validate(window) if window else None


@router.post("/maintenance", response_model=MaintenanceRead, status_code=status.HTTP_201_CREATED)
def start_maintenance(
    payload: MaintenanceCreate,
    staff: OperatorStaff,
    db: DbSession,
    audit: AuditDep,
    principal: PrincipalDep,
) -> MaintenanceRead:
    """Put the service into read-only mode.

    Reads keep working. Mutations are refused with a `503` and the message in
    this payload — never with a 500, because the whole value of a maintenance
    mode is that somebody who tries to start a simulation is *told what is
    happening*, rather than seeing a broken application.
    """
    existing = maintenance.active_window(db)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Maintenance is already on. End it before starting another window.",
        )
    window = MaintenanceWindow(
        reason=payload.reason,
        message=payload.message,
        started_at=lifecycle.utcnow(),
        expected_end_at=payload.expected_end_at,
        allow_staff=payload.allow_staff,
        started_by_id=principal.actor_user_id,
    )
    db.add(window)
    db.flush()
    audit.record(
        AuditAction.MAINTENANCE_CHANGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="maintenance",
        target_id=window.id,
        reason=payload.reason,
        detail={"state": "started"},
    )
    db.commit()
    # After the commit, so no worker can cache a window that then rolls back.
    # Only this process; the rest expire within `maintenance.CACHE_SECONDS`.
    maintenance.invalidate()
    return MaintenanceRead.model_validate(window)


@router.delete("/maintenance", response_model=MaintenanceRead)
def end_maintenance(
    staff: OperatorStaff, db: DbSession, audit: AuditDep, principal: PrincipalDep
) -> MaintenanceRead:
    window = maintenance.active_window(db)
    if window is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Maintenance is not on."
        )
    window.ended_at = lifecycle.utcnow()
    audit.record(
        AuditAction.MAINTENANCE_CHANGED,
        AuditOutcome.SUCCEEDED,
        principal=principal,
        target_type="maintenance",
        target_id=window.id,
        detail={"state": "ended"},
    )
    db.commit()
    # Ending matters more urgently than starting: until this clears, writes are
    # still being refused by a window that is over.
    maintenance.invalidate()
    return MaintenanceRead.model_validate(window)


# ---------------------------------------------------------------------------
# The operations dashboard (P3.6)
# ---------------------------------------------------------------------------


@router.get("/health", response_model=FleetHealthRead)
def read_fleet_health(
    staff: SupportStaff,
    db: DbSession,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> FleetHealthRead:
    """"Is Kryova healthy" with one answer (P3.6).

    Every number is counted from rows, and the two that cannot be are named
    rather than estimated — the same rule `read_organisation_usage` follows and
    for the same reason: a console where a guess looks like a measurement is a
    console that produces confident wrong decisions during an incident.

    **Solver failures are grouped by their recorded message**, not by an
    exception taxonomy, because that is what the schema actually holds:
    `SimulationJob.error` is the human-readable sentence the runner wrote. The
    grouping is therefore coarse and says so in `failure_grouping`. A real
    taxonomy class on the job row is E15 task 5's deliverable and this endpoint
    will read it the day it exists — that is a gap in the data, not in the
    dashboard, and it is recorded here rather than papered over.
    """
    since = utcnow() - timedelta(hours=hours)

    queue_depth = {member.value: 0 for member in JobStatus}
    for job_status, count in db.execute(
        select(SimulationJob.status, func.count()).group_by(SimulationJob.status)
    ).all():
        queue_depth[JobStatus(job_status).value] = count

    recent = {member.value: 0 for member in JobStatus}
    for job_status, count in db.execute(
        select(SimulationJob.status, func.count())
        .where(SimulationJob.created_at >= since)
        .group_by(SimulationJob.status)
    ).all():
        recent[JobStatus(job_status).value] = count

    finished = recent[JobStatus.SUCCEEDED.value] + recent[JobStatus.FAILED.value]
    # `None`, not 0.0, when nothing finished. A success rate of zero and "no
    # runs to judge" are opposite states and a dashboard that shows 0% during a
    # quiet night sends somebody to look for an outage that is not there.
    success_rate = (
        recent[JobStatus.SUCCEEDED.value] / finished if finished else None
    )

    failures = [
        FailureClassRead(reason=(reason or "unrecorded")[:160], count=count)
        for reason, count in db.execute(
            select(SimulationJob.error, func.count())
            .where(
                SimulationJob.status == JobStatus.FAILED,
                SimulationJob.created_at >= since,
            )
            .group_by(SimulationJob.error)
            .order_by(func.count().desc())
            .limit(10)
        ).all()
    ]

    storage_bytes = int(db.scalar(select(func.coalesce(func.sum(Media.size_bytes), 0))) or 0)
    storage_added = int(
        db.scalar(
            select(func.coalesce(func.sum(Media.size_bytes), 0)).where(
                Media.created_at >= since
            )
        )
        or 0
    )

    live_sessions = (
        db.scalar(
            select(func.count())
            .select_from(UserSession)
            .where(
                UserSession.revoked_at.is_(None),
                UserSession.absolute_expires_at > utcnow(),
            )
        )
        or 0
    )

    return FleetHealthRead(
        window_hours=hours,
        queue_depth=queue_depth,
        jobs_in_window=recent,
        success_rate=success_rate,
        failures=failures,
        failure_grouping=(
            "by the runner's recorded message; a taxonomy class on the job row is E15.5"
        ),
        storage_bytes=storage_bytes,
        storage_bytes_added_in_window=storage_added,
        live_sessions=int(live_sessions),
        users_total=int(db.scalar(select(func.count()).select_from(User)) or 0),
        users_suspended=int(
            db.scalar(
                select(func.count()).select_from(User).where(User.suspended_at.is_not(None))
            )
            or 0
        ),
        users_awaiting_deletion=int(
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.deletion_scheduled_at.is_not(None))
            )
            or 0
        ),
        mail_delivers=mail.can_reach_real_mailboxes(),
        maintenance_active=maintenance.active_window(db) is not None,
    )
