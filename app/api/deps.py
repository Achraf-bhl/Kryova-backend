from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Path, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, requires_csrf, verify_csrf
from app.core.database import SessionLocal, get_db, tenant_scope
from app.core.security import decode_access_token
from app.jobs import JobQueue, get_job_queue
from app.media import LocalMediaStore, MediaService, get_media_store
from app.models import Membership, Organisation, OrgRole, Project, User
from app.models.organisation import membership_for, organisation_ids_for
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


def get_current_user(
    request: Request,
    db: DbSession,
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
    """
    token = bearer_token or cookie_token
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    user_id = decode_access_token(token) if token else None
    if user_id is None:
        raise credentials_error
    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    if requires_csrf(request.method, "authorization" in request.headers) and not verify_csrf(
        request.headers.get(CSRF_HEADER_NAME), request.cookies.get(CSRF_COOKIE_NAME)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF failure")

    with tenant_scope(db, organisation_ids_for(db, user.id)):
        yield user


CurrentUser = Annotated[User, Depends(get_current_user)]


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
        membership = membership_for(db, current_user.id, organisation_id)
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
        membership = membership_for(db, current_user.id, organisation_id)
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
    membership = membership_for(db, current_user.id, project.organisation_id)
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
