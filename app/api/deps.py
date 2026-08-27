import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Path, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal, get_async_db, get_db
from app.core.security import decode_access_token
from app.jobs import JobQueue, get_job_queue
from app.media import LocalMediaStore, MediaService, get_media_store
from app.models import Project, User
from app.simulation.runner import SessionScope

DbSession = Annotated[Session, Depends(get_db)]
AsyncDbSession = Annotated[AsyncSession, Depends(get_async_db)]

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
) -> User:
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
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and "authorization" not in request.headers
        and not secrets.compare_digest(
            request.headers.get("x-csrf-token") or "",
            request.cookies.get("kryova_csrf") or "",
        )
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF failure")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_owned_project(
    db: DbSession, current_user: CurrentUser, project_id: Annotated[str, Path()]
) -> Project:
    project = db.get(Project, project_id)
    # 404 rather than 403 for someone else's project: don't leak which ids exist.
    if project is None or project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


OwnedProject = Annotated[Project, Depends(get_owned_project)]
