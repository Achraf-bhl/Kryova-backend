import secrets
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.rate_limit import auth_limiter
from app.core.config import settings
from app.core.csrf import new_csrf_token
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import User
from app.schemas import SessionRead, UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_options() -> Any:
    return {"secure": settings.cookie_secure, "samesite": settings.cookie_samesite}


class IssuedSession(NamedTuple):
    """The tokens just written to `response`, returned rather than re-parsed.

    The refresh token has to be hashed onto the user row after the cookie is
    set. Recovering it by string-splitting our own `Set-Cookie` header would
    break the moment cookie encoding changes, so hand it back directly.
    """

    csrf: str
    refresh: str


def _set_session_cookies(response: Response, user_id: str) -> IssuedSession:
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    csrf = new_csrf_token()
    common = _cookie_options()
    response.set_cookie(
        "kryova_access", access,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True, path="/", **common,
    )
    response.set_cookie(
        "kryova_refresh", refresh,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True, path=f"{settings.api_v1_prefix}/auth", **common,
    )
    response.set_cookie("kryova_csrf", csrf, httponly=False, path="/", **common)
    response.headers["cache-control"] = "no-store"
    return IssuedSession(csrf=csrf, refresh=refresh)


def _clear_session_cookies(response: Response) -> None:
    common = _cookie_options()
    response.delete_cookie("kryova_access", path="/", **common)
    response.delete_cookie("kryova_refresh", path=f"{settings.api_v1_prefix}/auth", **common)
    response.delete_cookie("kryova_csrf", path="/", **common)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: UserCreate, db: DbSession) -> User:
    if not auth_limiter.check(f"register:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again in a minute.",
        )
    email = payload.email.lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
    )
    db.add(user)
    db.commit()
    return user


@router.post("/login", response_model=SessionRead)
def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> SessionRead:
    if not auth_limiter.check(f"login:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a minute.",
        )
    user = db.scalar(select(User).where(User.email == form_data.username.lower()))
    if user is None or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    issued = _set_session_cookies(response, user.id)
    user.refresh_token_hash = hash_token(issued.refresh)
    db.commit()
    return SessionRead(user=UserRead.model_validate(user), csrf_token=issued.csrf)


@router.post("/refresh", response_model=SessionRead)
def refresh_session(request: Request, response: Response, db: DbSession) -> SessionRead:
    token = request.cookies.get("kryova_refresh")
    if token is None:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    user_id = decode_refresh_token(token)
    user = db.get(User, user_id) if user_id else None
    expected_hash = user.refresh_token_hash if user else None
    if (
        user is None or expected_hash is None
        or not secrets.compare_digest(hash_token(token), expected_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Rotate: the presented token is single-use, so the row only ever holds the
    # hash of the newest one. Assigned in a single commit -- never blanked and
    # re-set, which would log the user out if the second write failed.
    issued = _set_session_cookies(response, user.id)
    user.refresh_token_hash = hash_token(issued.refresh)
    db.commit()
    return SessionRead(user=UserRead.model_validate(user), csrf_token=issued.csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, current_user: CurrentUser, db: DbSession) -> None:
    current_user.refresh_token_hash = None
    db.commit()
    _clear_session_cookies(response)


@router.get("/me", response_model=UserRead)
def read_current_user(current_user: CurrentUser) -> User:
    return current_user
