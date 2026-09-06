from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Path, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload

from app.core.audit import (
    SAFE_METHODS,
    AuditService,
    Principal,
    request_origin,
    staff_scope,
)
from app.core.config import settings
from app.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, requires_csrf, verify_csrf
from app.core.database import SessionLocal, get_db, tenant_scope
from app.core.security import ImpersonationClaims, decode_access_token, decode_impersonation_token
from app.jobs import JobQueue, get_job_queue
from app.media import LocalMediaStore, MediaService, get_media_store
from app.models import Membership, Organisation, OrgRole, Project, User
from app.models.audit import (
    AuditAction,
    AuditOutcome,
    ImpersonationSession,
    StaffGrant,
    StaffRole,
    live_staff_grant,
)
from app.models.organisation import (
    membership_for_user,
    organisation_ids_for_user,
)
from app.simulation.runner import SessionScope

DbSession = Annotated[Session, Depends(get_db)]

MediaStoreDep = Annotated[LocalMediaStore, Depends(get_media_store)]
JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_v1_prefix}/auth/login", auto_error=False
)


def get_media_service(db: DbSession, store: MediaStoreDep) -> MediaService:
    return MediaService(db, store)


MediaServiceDep = Annotated[MediaService, Depends(get_media_service)]


def get_session_scope() -> SessionScope:
    """A fresh session for background work.

    Background jobs outlive the request, so they cannot borrow its session --
    it is closed the moment the response is sent.
    """

    @contextmanager
    def scope() -> Iterator[Session]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    return scope


SessionScopeDep = Annotated[SessionScope, Depends(get_session_scope)]


def get_audit_service(scope: SessionScopeDep) -> AuditService:
    """The audit log's writer, on a session of its own.

    `SessionScopeDep`, not `DbSession`, and that is the whole design: the log
    has to survive the request's transaction being rolled back, because a
    refused or failed action is the entry you most want to keep. See
    `app/core/audit.py`.
    """
    return AuditService(scope)


AuditDep = Annotated[AuditService, Depends(get_audit_service)]


def _load_user(db: Session, user_id: str) -> User | None:
    """Fetch a user together with the tenancy rows every request then needs.

    One round trip instead of three. Authentication has to read the user, then
    the memberships behind the RLS tenant context, then -- on any project or
    organisation route -- the one membership that authorises the call; and
    creating a project reads the memberships a fourth time looking for the
    personal organisation. Against Neon each of those was ~80 ms of latency for
    rows that all hang off the user just fetched.

    `joinedload`, deliberately not `selectinload`: selectin issues a second
    statement, which is the round trip this exists to remove. The extra rows
    are one per membership, which is small by construction -- a person belongs
    to a handful of organisations, not thousands.

    Nothing downstream *depends* on the eager load: `membership_for_user`,
    `organisation_ids_for_user` and `personal_organisation` all fall back to
    their own queries when the collection is not loaded. This is an
    optimisation, not a new precondition.
    """
    return db.get(
        User,
        user_id,
        options=[joinedload(User.memberships).joinedload(Membership.organisation)],
    )


def get_current_user(
    request: Request,
    db: DbSession,
    audit: AuditDep,
    bearer_token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    cookie_token: Annotated[str | None, Cookie(alias="kryova_access")] = None,
) -> Iterator[User]:
    """Authenticate the request, and put the database inside its tenant.

    A generator dependency rather than a plain one so the RLS context covers the
    *whole* request and is torn down afterwards. Establishing it here rather
    than in each guard is the point: every authenticated route in the service,
    including ones written by people who have never read this file, runs with
    `kryova.organisation_ids` set to the caller's memberships. Application
    scoping remains primary; this is what is left when a query forgets it.

    **Impersonation composes here rather than beside here** (P3.3). A staff
    token resolves to the *subject* user, so every route, guard and RLS policy
    downstream sees exactly what that user would see -- no route learns about
    impersonation, and none can forget to. What the staff member gains is
    nothing: the tenant context is the subject's memberships, not theirs. What
    they lose is write access, unless the session has been escalated on the
    record. Both identities are put on `request.state.principal`, which is what
    the audit log reads, and the read-only refusal happens *before* the route
    function is entered -- so a route that never heard of impersonation cannot
    be the one that lets a write through.
    """
    token = bearer_token or cookie_token
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_error

    ip_address, user_agent = request_origin(request)
    claims = decode_impersonation_token(token)
    if claims is not None:
        user, principal = _impersonated_principal(
            request, db, audit, claims, ip_address, user_agent
        )
    else:
        user_id = decode_access_token(token)
        if user_id is None:
            raise credentials_error
        found = _load_user(db, user_id)
        if found is None:
            raise credentials_error
        user = found
        principal = Principal.of(user, ip_address, user_agent)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    if requires_csrf(request.method, "authorization" in request.headers) and not verify_csrf(
        request.headers.get(CSRF_HEADER_NAME), request.cookies.get(CSRF_COOKIE_NAME)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF failure")

    request.state.principal = principal
    with tenant_scope(db, organisation_ids_for_user(db, user)):
        yield user


def _impersonated_principal(
    request: Request,
    db: Session,
    audit: AuditService,
    claims: ImpersonationClaims,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[User, Principal]:
    """Resolve a staff token to (subject user, both-identity principal).

    Four things are re-checked on **every** request rather than trusted from the
    token, and each of them is a way a token outlives the authority behind it:
    the session row still exists and is live (so ending one takes effect at
    once, which a stateless token could not offer), the two identities on the
    token still match the row (so a re-signed token cannot repoint a live
    session at somebody else), the actor's staff grant is still live (so
    revoking staff ends the power immediately, rather than at the token's
    expiry), and the mode -- which is read from the row precisely so escalation
    and de-escalation are not decided by the oldest token in circulation.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="This impersonation session is no longer valid. Start a new one.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    session_row = db.get(ImpersonationSession, claims.session_id)
    if session_row is None or not session_row.is_live():
        raise credentials_error
    if (
        session_row.actor_user_id != claims.actor_id
        or session_row.subject_user_id != claims.subject_id
    ):
        raise credentials_error

    actor = db.get(User, claims.actor_id)
    # The subject is the identity every guard downstream reads, so it gets the
    # same eager load an ordinary sign-in does. The actor is only ever an
    # identity on the audit row and needs none of it.
    subject = _load_user(db, claims.subject_id)
    if actor is None or subject is None or not actor.is_active:
        raise credentials_error
    if live_staff_grant(db, actor.id) is None:
        raise credentials_error

    principal = Principal(
        actor_user_id=actor.id,
        actor_email=actor.email,
        subject_user_id=subject.id,
        subject_email=subject.email,
        impersonated=True,
        may_write=session_row.may_write,
        session_id=session_row.id,
        reason=session_row.escalation_reason or session_row.reason,
        expires_at=session_row.expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    where = {"method": request.method, "path": request.url.path}

    if not principal.may_write and request.method not in SAFE_METHODS:
        # Recorded *before* the refusal is raised, and on its own transaction,
        # so the entry survives everything that happens next. This is the row
        # the whole read-only default exists to produce.
        audit.record(
            AuditAction.IMPERSONATED_WRITE_REFUSED,
            AuditOutcome.REFUSED,
            principal=principal,
            target_type="request",
            target_id=f"{request.method} {request.url.path}"[:64],
            reason=session_row.reason,
            detail=where,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This impersonation session is read-only, which is the default. "
                "Ask a platform administrator to escalate it with a written reason "
                f"(POST /admin/impersonation/{session_row.id}/escalate) and use the "
                "token that returns."
            ),
        )

    # PERMITTED, not SUCCEEDED: this row says the door was opened. Whether the
    # work behind it worked is not this row's claim to make.
    audit.record(
        AuditAction.IMPERSONATED_REQUEST,
        AuditOutcome.PERMITTED,
        principal=principal,
        target_type="request",
        target_id=f"{request.method} {request.url.path}"[:64],
        reason=session_row.reason,
        detail=where,
    )
    return subject, principal


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_principal(request: Request, current_user: CurrentUser) -> Principal:
    """Both identities behind this request.

    Depends on `CurrentUser` for sequencing, not for its value: authentication
    is what puts the principal on `request.state`, so asking for one without
    having authenticated has to be impossible rather than merely unusual.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None:  # pragma: no cover - unreachable while the line above holds
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        )
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]


# --------------------------------------------------------------------------
# Tenancy guards. Every "may this person do this" answer in the service comes
# from this block, and every one of them is "at least this role". A route that
# needs a different answer extends the ladder here rather than reading a
# membership row itself -- a second place to decide is a second place to be
# wrong, and the wrong answer is a cross-tenant read.
#
# A miss is always 404, never 403: 403 confirms the id exists, which is a free
# enumeration oracle across accounts.
# --------------------------------------------------------------------------

_ORGANISATION_NOT_FOUND = "Organisation not found"
_PROJECT_NOT_FOUND = "Project not found"


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def require_organisation(
    minimum: OrgRole,
) -> Callable[[Session, User, str], Organisation]:
    """Build a dependency resolving an organisation the caller may act on."""

    def dependency(
        db: DbSession,
        current_user: CurrentUser,
        organisation_id: Annotated[str, Path()],
    ) -> Organisation:
        membership = membership_for_user(db, current_user, organisation_id)
        # Free when the membership came from the session: the eager load in
        # `_load_user` put the organisation in the identity map already.
        organisation = db.get(Organisation, organisation_id) if membership else None
        if membership is None or organisation is None:
            raise _not_found(_ORGANISATION_NOT_FOUND)
        if not membership.role.at_least(minimum):
            # Still 404. The caller can see the organisation exists (they are in
            # it) but telling them "you are only a viewer" on a resource they
            # cannot address is noise; the resource, for them, is not there.
            raise _not_found(_ORGANISATION_NOT_FOUND)
        return organisation

    return dependency


def require_membership(
    minimum: OrgRole,
) -> Callable[[Session, User, str], Membership]:
    """As `require_organisation`, but hands back the membership row itself."""

    def dependency(
        db: DbSession,
        current_user: CurrentUser,
        organisation_id: Annotated[str, Path()],
    ) -> Membership:
        membership = membership_for_user(db, current_user, organisation_id)
        if membership is None or not membership.role.at_least(minimum):
            raise _not_found(_ORGANISATION_NOT_FOUND)
        return membership

    return dependency


ViewerOrganisation = Annotated[Organisation, Depends(require_organisation(OrgRole.VIEWER))]
AdminOrganisation = Annotated[Organisation, Depends(require_organisation(OrgRole.ADMIN))]
OwnerOrganisation = Annotated[Organisation, Depends(require_organisation(OrgRole.OWNER))]
ViewerMembership = Annotated[Membership, Depends(require_membership(OrgRole.VIEWER))]
AdminMembership = Annotated[Membership, Depends(require_membership(OrgRole.ADMIN))]


def _project_for_role(
    db: Session, current_user: User, project_id: str, minimum: OrgRole
) -> Project:
    project = db.get(Project, project_id)
    # 404 rather than 403 for another tenant's project: don't leak which ids
    # exist. RLS makes the lie consistent -- under the caller's tenant context
    # the row genuinely is not visible to the database either.
    if project is None:
        raise _not_found(_PROJECT_NOT_FOUND)
    membership = membership_for_user(db, current_user, project.organisation_id)
    if membership is None or not membership.role.at_least(minimum):
        raise _not_found(_PROJECT_NOT_FOUND)
    return project


def get_readable_project(
    db: DbSession, current_user: CurrentUser, project_id: Annotated[str, Path()]
) -> Project:
    """Read access: any member of the owning organisation, viewers included."""
    return _project_for_role(db, current_user, project_id, OrgRole.VIEWER)


def get_owned_project(
    db: DbSession, current_user: CurrentUser, project_id: Annotated[str, Path()]
) -> Project:
    """Write access, and the name every existing route already depends on.

    Deliberately `MEMBER`, not `VIEWER`: this dependency guards geometry
    uploads, simulation runs and CATIA operations as well as reads, and every
    route outside this phase's scope uses it. Widening it to viewers to make
    read-only routes read nicely would have handed viewers write access to
    everything in one edit. Read-only routes ask for `ReadableProject`.
    """
    return _project_for_role(db, current_user, project_id, OrgRole.MEMBER)


def get_administrable_project(
    db: DbSession, current_user: CurrentUser, project_id: Annotated[str, Path()]
) -> Project:
    return _project_for_role(db, current_user, project_id, OrgRole.ADMIN)


ReadableProject = Annotated[Project, Depends(get_readable_project)]
OwnedProject = Annotated[Project, Depends(get_owned_project)]
AdministrableProject = Annotated[Project, Depends(get_administrable_project)]


# --------------------------------------------------------------------------
# Staff guards (P3.2). Separate from the tenancy ladder above and deliberately
# not part of it: staff standing is not a membership and grants nothing inside
# a tenant. The only route to a customer's work is impersonation, which is
# recorded.
# --------------------------------------------------------------------------

_ADMIN_NOT_FOUND = "Not found"


def require_staff(minimum: StaffRole) -> Callable[[Session, Principal], Iterator[StaffGrant]]:
    """Build a dependency admitting only staff of at least `minimum`.

    **A miss is 404, never 403**, and the reason is stronger here than anywhere
    else in the service: a 403 on `/admin/...` tells an ordinary user that an
    admin surface exists at that path and that their account is simply the wrong
    one, which is a map for whoever compromises the next account. To everyone
    but staff, the console is not there.

    **An impersonated principal is never staff**, whatever grant the actor
    holds. A staff member who has assumed a user's identity is doing that user's
    work; letting the admin API answer to that token would mean a read-only
    impersonation session could reach `/admin/jobs/{id}/fail`, and the identity
    every row was written under would be the customer's.

    A generator dependency so `staff_scope` covers the whole request: the
    audit log's RLS policy reads that setting, and it has to be published for
    the queries the route makes, not only for the moment the grant was checked.
    """

    def dependency(db: DbSession, principal: PrincipalDep) -> Iterator[StaffGrant]:
        grant = (
            None if principal.impersonated else live_staff_grant(db, principal.actor_user_id)
        )
        if grant is None or not grant.role.at_least(minimum):
            raise _not_found(_ADMIN_NOT_FOUND)
        with staff_scope(db):
            yield grant

    return dependency


SupportStaff = Annotated[StaffGrant, Depends(require_staff(StaffRole.SUPPORT))]
OperatorStaff = Annotated[StaffGrant, Depends(require_staff(StaffRole.OPERATOR))]
PlatformAdminStaff = Annotated[StaffGrant, Depends(require_staff(StaffRole.PLATFORM_ADMIN))]
