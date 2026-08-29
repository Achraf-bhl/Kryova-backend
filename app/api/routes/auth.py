import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.rate_limit import auth_limiter
from app.core.config import settings
from app.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, new_csrf_token, verify_csrf
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import User
from app.schemas import PasswordReset, PasswordResetRequest, SessionRead, UserCreate, UserRead

logger = logging.getLogger(__name__)

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
        "kryova_access",
        access,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        path="/",
        **common,
    )
    response.set_cookie(
        "kryova_refresh",
        refresh,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=True,
        path=f"{settings.api_v1_prefix}/auth",
        **common,
    )
    # Same lifetime as the refresh cookie, and deliberately NOT a session cookie.
    # With no `max_age` the browser dropped this on restart while the persistent
    # access cookie survived, leaving a window where the CSRF check had nothing
    # to compare -- which `verify_csrf` now refuses outright, but a user whose
    # token silently vanished would just see 403s on every action instead.
    # `httponly=False` is required: double-submit needs JS to read it back.
    response.set_cookie(
        "kryova_csrf",
        csrf,
        max_age=settings.refresh_token_expire_days * 86400,
        httponly=False,
        path="/",
        **common,
    )
    response.headers["cache-control"] = "no-store"
    return IssuedSession(csrf=csrf, refresh=refresh)


def _clear_session_cookies(response: Response) -> None:
    common = _cookie_options()
    response.delete_cookie("kryova_access", path="/", **common)
    response.delete_cookie("kryova_refresh", path=f"{settings.api_v1_prefix}/auth", **common)
    response.delete_cookie("kryova_csrf", path="/", **common)


def _client_ip(request: Request) -> str:
    """The address the rate limiter counts against.

    `X-Forwarded-For` is written by whoever sends the request, so trusting it
    unconditionally means a client that rotates the header has no rate limit at
    all. It is read only when `trust_proxy_headers` says a reverse proxy is in
    front, and then from the right: each trusted proxy appends the address it
    saw, so with N of them the real client is N entries from the end. Everything
    to the left of that was supplied by the caller.
    """
    peer = request.client.host if request.client else "unknown"
    if not settings.trust_proxy_headers:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    index = len(hops) - settings.trusted_proxy_count
    if not hops or index < 0:
        # Fewer hops than the deployment claims: the chain is not what was
        # configured, so believe the socket rather than guess.
        return peer
    return hops[index]


# A real bcrypt digest of a value nobody can present, used only to spend the
# same CPU on a login miss as on a hit. Computed once at import: doing it per
# request would itself be a timing signal.
_TIMING_EQUALISER_HASH = hash_password(secrets.token_urlsafe(32))


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
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
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
    if user is None:
        # Hash against a throwaway digest so a miss costs the same ~275ms a hit
        # does. Returning early here made "no such account" and "wrong password"
        # trivially distinguishable by response time -- a free user-enumeration
        # oracle on an endpoint that is deliberately vague in its wording.
        verify_password(form_data.password, _TIMING_EQUALISER_HASH)
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
    # Rate-limited like every other credential-bearing auth route. This one was
    # the exception, which made it the cheapest endpoint to grind refresh tokens
    # against.
    if not auth_limiter.check(f"refresh:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many refresh attempts. Try again in a minute.",
        )
    token = request.cookies.get("kryova_refresh")
    if token is None:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    if not verify_csrf(
        request.headers.get(CSRF_HEADER_NAME), request.cookies.get(CSRF_COOKIE_NAME)
    ):
        raise HTTPException(status_code=403, detail="CSRF failure")
    user_id = decode_refresh_token(token)
    user = db.get(User, user_id) if user_id else None
    expected_hash = user.refresh_token_hash if user else None
    if (
        user is None
        or expected_hash is None
        or not secrets.compare_digest(hash_token(token), expected_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    # A deactivated account keeps a valid refresh chain unless this is checked:
    # `login` and `get_current_user` both refuse an inactive user, so without it
    # the one path that *renews* a session is the one path that ignores the flag.
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

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


@router.post("/password-reset-request", status_code=status.HTTP_204_NO_CONTENT)
def request_password_reset(
    request: Request,
    payload: PasswordResetRequest,
    db: DbSession,
) -> None:
    """Mint a single-use reset token for an account, if it exists.

    TODO: there is no mail transport in this service yet, so nothing delivers
    the token to the address that asked for it. Until an email sender exists
    this endpoint is only usable in development, where the token is logged at
    DEBUG; a production deployment records that a reset was requested and
    nothing more, because a token in a log file is a password in a log file.
    """
    if not auth_limiter.check(f"pwreset:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many reset requests. Try again in a minute.",
        )

    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    # Always return 204 so the response does not reveal whether the email exists.
    if user is None:
        return

    raw_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    user.password_reset_token_hash = hash_token(raw_token)
    user.password_reset_expires_at = expires
    db.commit()

    if settings.is_production:
        logger.info("Password reset requested; no mail transport is configured to deliver it")
    else:
        logger.debug("Password reset token for %s: %s", payload.email, raw_token)


@router.post("/password-reset", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(
    request: Request,
    payload: PasswordReset,
    db: DbSession,
) -> None:
    if not auth_limiter.check(f"pwconfirm:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again in a minute.",
        )

    token_hash = hash_token(payload.token)
    user = db.scalar(select(User).where(User.password_reset_token_hash == token_hash))
    if (
        user is None
        or user.password_reset_expires_at is None
        or user.password_reset_expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid or expired reset token",
        )

    user.hashed_password = hash_password(payload.new_password)
    user.password_reset_token_hash = None
    user.password_reset_expires_at = None
    user.refresh_token_hash = None  # Invalidate all existing sessions
    db.commit()
