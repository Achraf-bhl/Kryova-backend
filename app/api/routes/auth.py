import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app import mail
from app.api.deps import CurrentUser, DbSession
from app.api.rate_limit import auth_limiter, client_ip
from app.core import email_verification, mfa
from app.core.config import settings
from app.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, new_csrf_token, verify_csrf
from app.core.security import (
    MFA_CHALLENGE_MINUTES,
    create_access_token,
    create_mfa_challenge_token,
    decode_mfa_challenge_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.core.sessions import (
    SessionError,
    live_sessions,
    revoke_all,
    revoke_session,
    rotate_session,
    start_session,
)
from app.models import SessionRevocation, User, UserSession
from app.schemas import (
    DeviceSessionRead,
    EmailVerification,
    LoginResult,
    MfaChallenge,
    MfaCodeSubmission,
    MfaEnrolmentRead,
    MfaLogin,
    MfaStatusRead,
    PasswordReset,
    PasswordResetRequest,
    RecoveryCodesRead,
    SessionRead,
    UserCreate,
    UserRead,
    VerificationStatusRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_options() -> Any:
    return {"secure": settings.cookie_secure, "samesite": settings.cookie_samesite}


class IssuedSession(NamedTuple):
    """The tokens just written to `response`, returned rather than re-parsed.

    Recovering the CSRF token by string-splitting our own `Set-Cookie` header
    would break the moment cookie encoding changes, so hand it back directly.
    """

    csrf: str
    refresh: str


def _set_session_cookies(response: Response, user_id: str, refresh: str) -> IssuedSession:
    """Write the three cookies for a refresh token the caller already has.

    The refresh token is passed in rather than minted here, because since P1.2 it
    is issued by `core/sessions.py` together with the row that authorises it.
    Minting it in the cookie layer would allow a token to exist that no session
    row backs — which would be accepted by nothing, but only after the user had
    been told they were signed in.
    """
    access = create_access_token(user_id)
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


#: The `X-Forwarded-For` rule now lives in one place (`api/rate_limit.py`).
#: This alias is kept because the name is used throughout this module and in
#: `tests/test_auth_sessions.py`; the implementation is no longer duplicated.
_client_ip = client_ip


# A real bcrypt digest of a value nobody can present, used only to spend the
# same CPU on a login miss as on a hit. Computed once at import: doing it per
# request would itself be a timing signal.
_TIMING_EQUALISER_HASH = hash_password(secrets.token_urlsafe(32))


def _send_verification(db: DbSession, user: User) -> None:
    """Mint and post a verification link, if one is due.

    Failures are logged and swallowed. A registration that has already written
    the user row must not 500 because SMTP was briefly unreachable — the account
    exists, the address is unverified, and `/auth/verify-email/resend` is the
    documented way out. The `Delivery` record is what makes that a decision
    rather than an oversight.
    """
    now = email_verification.utcnow()
    outcome = email_verification.request_verification(db, user, now=now)
    if outcome.token is None:
        return
    delivery = mail.send(
        mail.templates.verify_email(
            to=user.email,
            token=outcome.token,
            hours_valid=settings.email_verification_ttl_hours,
        )
    )
    if delivery.state is mail.DeliveryState.FAILED:
        logger.warning(
            "could not send a verification email", extra={"detail": delivery.detail}
        )


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
    # The id is a Python-side default applied *during* the flush, and
    # `_send_verification` does not need it -- but the row must exist before the
    # verification columns are written to it, and a flush here makes the whole
    # thing one transaction rather than two.
    db.flush()
    _send_verification(db, user)
    db.commit()
    return user


def _issue_session(
    request: Request, response: Response, db: DbSession, user: User
) -> SessionRead:
    """Start a device family and write its cookies. The end of every sign-in.

    Factored out because there are now two ways in — password alone, and
    password followed by a second factor — and they must produce *identical*
    sessions. A second copy of this is how one of the two paths ends up without
    reuse detection.
    """
    # A login is a new device family (P1.1), not a rewrite of a shared slot.
    # Signing in on a second machine used to end the session on the first.
    started = start_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )
    issued = _set_session_cookies(response, user.id, started.token)
    db.commit()
    return SessionRead(user=UserRead.model_validate(user), csrf_token=issued.csrf)


@router.post("/login", response_model=LoginResult)
def login(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> SessionRead | MfaChallenge:
    """Sign in. Returns a session, or a demand for the second factor.

    **This response shape changed in P1.7.** It was always `SessionRead`; it is
    now a union, and an account with two-factor authentication enabled answers
    `202` with an `MfaChallenge` and **no cookies set**. A client that assumes
    the old shape reads `user` as undefined rather than failing loudly, so the
    frontend narrows on `mfa_required` — see `types/api.ts`.
    """
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

    if mfa.is_enrolled(db, user):
        # No session, no cookies, and a token that grants nothing. The password
        # has been proved and that is all: `MFA_CHALLENGE_TOKEN_TYPE` is refused
        # by `_decode_typed` everywhere a credential is expected, so this cannot
        # be presented as a half-login to any other route.
        response.status_code = status.HTTP_202_ACCEPTED
        return MfaChallenge(
            challenge_token=create_mfa_challenge_token(user.id),
            expires_in_seconds=MFA_CHALLENGE_MINUTES * 60,
            recovery_available=mfa.unused_recovery_code_count(db, user) > 0,
        )
    return _issue_session(request, response, db, user)


@router.post("/login/mfa", response_model=SessionRead)
def complete_mfa_login(
    request: Request, response: Response, payload: MfaLogin, db: DbSession
) -> SessionRead:
    """Finish a sign-in with a TOTP code or a recovery code.

    Rate-limited on its own key. Without that, the second factor is the one
    credential in the system a client may guess a million times: the password
    limit was already spent getting here, and six digits is 10^6.
    """
    if not auth_limiter.check(f"mfa:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again in a minute.",
        )
    user_id = decode_mfa_challenge_token(payload.challenge_token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in has expired. Start again.",
        )
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials"
        )

    now = mfa.utcnow()
    try:
        mfa.verify_code(db, user, payload.code, now=now)
    except mfa.MfaError as refused:
        # A recovery code is the fallback, tried second so a six-digit string
        # that is also a valid recovery code cannot burn one by accident.
        try:
            mfa.spend_recovery_code(db, user, payload.code, now=now)
        except mfa.MfaError:
            db.commit()  # `verify_code` may have burned a step; keep that.
            logger.info(
                "second factor refused",
                extra={"refusal": refused.refusal.value, "user_id": user.id},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="That code is not valid."
            ) from refused
    return _issue_session(request, response, db, user)


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

    try:
        rotated = rotate_session(db, token, ip_address=_client_ip(request))
    except SessionError as refused:
        # The family may have been revoked by the attempt itself (reuse
        # detection), so the write has to land before the response goes out —
        # otherwise a replayed token revokes nothing and can be replayed again.
        db.commit()
        _clear_session_cookies(response)
        if refused.compromised:
            logger.warning(
                "refresh token reuse detected; session family revoked",
                extra={"remote_addr": _client_ip(request)},
            )
            _notify_of_theft(db, token, request)
        raise HTTPException(status_code=401, detail=refused.detail) from refused

    user = db.get(User, rotated.session.user_id)
    if user is None:  # pragma: no cover - a live session for a deleted user
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    # A deactivated account keeps a valid refresh chain unless this is checked:
    # `login` and `get_current_user` both refuse an inactive user, so without it
    # the one path that *renews* a session is the one path that ignores the flag.
    if not user.is_active:
        revoke_session(db, rotated.session, SessionRevocation.ADMIN)
        db.commit()
        _clear_session_cookies(response)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    issued = _set_session_cookies(response, user.id, rotated.token)
    db.commit()
    return SessionRead(user=UserRead.model_validate(user), csrf_token=issued.csrf)


def _notify_of_theft(db: DbSession, token: str, request: Request) -> None:
    """Tell the account holder that a token of theirs was replayed.

    The detection is what protects the account; this is what lets the *person*
    act on it. Without it, reuse detection presents to the user as being
    mysteriously signed out — an event people shrug at and sign back in from,
    which is exactly the wrong response to evidence that somebody has a copy of
    their credentials.

    Best-effort by construction: the session row has already been revoked and
    committed, so a failure here loses a notification and never a revocation.
    """
    # `previous_token_hash`, not `token_hash`: by the time reuse is detected the
    # token presented is the *rotated-away* one, which is the column
    # `rotate_session` found it in. Looking in `token_hash` finds nothing and
    # the notification silently never sends — the failure this comment exists
    # to stop somebody reintroducing.
    session = db.scalars(
        select(UserSession).where(UserSession.previous_token_hash == hash_token(token))
    ).first()
    if session is None:
        return
    user = db.get(User, session.user_id)
    if user is None:  # pragma: no cover - a session for a deleted user
        return
    mail.send(
        mail.templates.session_theft_notice(
            to=user.email,
            at=datetime.now(timezone.utc),
            ip_address=_client_ip(request),
        )
    )


def _current_session(db: DbSession, request: Request) -> UserSession | None:
    """The session row behind this request's refresh cookie, if any.

    Returns None rather than raising when the cookie is missing or unknown: the
    callers below all want to *end* something, and an end that finds nothing has
    already achieved what it was asked to do.
    """
    token = request.cookies.get("kryova_refresh")
    if not token:
        return None
    presented = hash_token(token)
    return db.scalars(
        select(UserSession).where(UserSession.token_hash == presented)
    ).first()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, response: Response, current_user: CurrentUser, db: DbSession
) -> None:
    """Sign out this device only.

    Other devices are left alone, which is new: `refresh_token_hash` was one slot
    per user, so signing out anywhere signed out everywhere. The cookies are
    cleared whether or not a row was found — a client asking to be signed out
    must end up signed out even if its session had already expired.
    """
    session = _current_session(db, request)
    if session is not None and session.user_id == current_user.id:
        revoke_session(db, session, SessionRevocation.LOGOUT)
        db.commit()
    _clear_session_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    request: Request, response: Response, current_user: CurrentUser, db: DbSession
) -> None:
    """Sign out every device, including this one.

    This is the action someone takes when they believe their account is being
    used by somebody else, so it takes the caller with it rather than sparing
    them: a person who has just been told a token was stolen should end up with
    nothing valid anywhere, and sign in again deliberately.
    """
    revoke_all(db, current_user, SessionRevocation.LOGOUT_ALL)
    db.commit()
    _clear_session_cookies(response)


@router.get("/sessions", response_model=list[DeviceSessionRead])
def list_sessions(
    request: Request, current_user: CurrentUser, db: DbSession
) -> list[DeviceSessionRead]:
    """The devices this account is signed in on (P1.3's backing).

    The row is the truth, not the cookie — a session revoked from another device
    disappears from here immediately, which is the whole point of the feature.
    """
    here = _current_session(db, request)
    return [
        DeviceSessionRead(
            id=row.id,
            device_label=row.device_label,
            ip_address=row.ip_address,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            absolute_expires_at=row.absolute_expires_at,
            current=here is not None and row.id == here.id,
        )
        for row in live_sessions(db, current_user)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def end_session(session_id: str, current_user: CurrentUser, db: DbSession) -> None:
    """Sign out one named device.

    Another user's session is a 404, never a 403 — the same rule every resource
    here follows, so ids cannot be probed across accounts.
    """
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    revoke_session(db, session, SessionRevocation.LOGOUT)
    db.commit()


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
    user.refresh_token_hash = None  # Legacy column; nothing reads it (P1.1).
    # The real revocation. Clearing the dead column above left every live device
    # family untouched, so a password changed *because* it was compromised did
    # not sign the attacker out -- which is the one thing the user was trying to
    # do. `revoke_all` is the same machinery `logout-all` uses.
    revoke_all(db, user, SessionRevocation.PASSWORD_CHANGED)
    db.commit()


# ---------------------------------------------------------------------------
# Email verification (P1.5)
# ---------------------------------------------------------------------------


@router.post("/verify-email", response_model=UserRead)
def verify_email(request: Request, payload: EmailVerification, db: DbSession) -> User:
    """Confirm an address from the emailed link.

    Unauthenticated on purpose: the link is opened in whichever browser the mail
    client hands it to, which is routinely not the one holding the session. The
    token is the credential, it is single-use, and it expires.
    """
    if not auth_limiter.check(f"verifyemail:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again in a minute.",
        )
    user = email_verification.confirm(db, payload.token, now=email_verification.utcnow())
    if user is None:
        db.commit()  # An expired token is cleared; that write must land.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That confirmation link is invalid or has expired. Ask for a new one.",
        )
    db.commit()
    return user


@router.get("/verify-email", response_model=VerificationStatusRead)
def verification_status(current_user: CurrentUser, db: DbSession) -> VerificationStatusRead:
    now = email_verification.utcnow()
    wait = 0
    sent = current_user.email_verification_sent_at
    if sent is not None and not current_user.is_verified:
        elapsed = (now - sent).total_seconds()
        wait = max(0, int(settings.email_verification_resend_seconds - elapsed))
    return VerificationStatusRead(
        verified=current_user.is_verified,
        can_resend_in_seconds=wait,
        delivery_available=mail.can_reach_real_mailboxes(),
    )


@router.post("/verify-email/resend", response_model=VerificationStatusRead)
def resend_verification(current_user: CurrentUser, db: DbSession) -> VerificationStatusRead:
    """Send the confirmation link again, no more often than the throttle allows.

    Authenticated, unlike `/verify-email` itself: an unauthenticated resend
    taking an email address is a mail cannon pointed at anybody with an account
    here. The throttle is the second layer, not the only one.
    """
    now = email_verification.utcnow()
    outcome = email_verification.request_verification(db, current_user, now=now)
    if outcome.should_send and outcome.token is not None:
        mail.send(
            mail.templates.verify_email(
                to=current_user.email,
                token=outcome.token,
                hours_valid=settings.email_verification_ttl_hours,
            )
        )
    db.commit()
    return VerificationStatusRead(
        verified=outcome.already_verified or current_user.is_verified,
        can_resend_in_seconds=(
            outcome.retry_after_seconds
            if outcome.retry_after_seconds
            else settings.email_verification_resend_seconds
        ),
        delivery_available=mail.can_reach_real_mailboxes(),
    )


# ---------------------------------------------------------------------------
# Second factor (P1.7)
# ---------------------------------------------------------------------------


@router.get("/mfa", response_model=MfaStatusRead)
def mfa_status(current_user: CurrentUser, db: DbSession) -> MfaStatusRead:
    enrolment = mfa.enrolment_for(db, current_user)
    return MfaStatusRead(
        enabled=enrolment is not None and enrolment.is_active,
        pending=enrolment is not None and not enrolment.is_active,
        recovery_codes_remaining=mfa.unused_recovery_code_count(db, current_user),
    )


@router.post("/mfa", response_model=MfaEnrolmentRead, status_code=status.HTTP_201_CREATED)
def begin_mfa_enrolment(current_user: CurrentUser, db: DbSession) -> MfaEnrolmentRead:
    """Start setting up an authenticator app.

    Refuses when one is already confirmed. Silently replacing a working second
    factor is a takeover primitive: anyone with a live session could swap the
    factor for their own and lock the owner out, which is precisely the attack
    the factor exists to stop.
    """
    if mfa.is_enrolled(db, current_user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Two-factor authentication is already on for this account. "
                "Turn it off first if you want to set up a different app."
            ),
        )
    enrolment = mfa.begin_enrolment(db, current_user)
    db.commit()
    return MfaEnrolmentRead(
        secret=enrolment.secret,
        provisioning_uri=enrolment.uri,
        recovery_codes=enrolment.recovery_codes,
    )


@router.post("/mfa/confirm", response_model=MfaStatusRead)
def confirm_mfa(
    payload: MfaCodeSubmission, current_user: CurrentUser, db: DbSession
) -> MfaStatusRead:
    try:
        mfa.confirm_enrolment(db, current_user, payload.code, now=mfa.utcnow())
    except mfa.MfaError as refused:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.detail
        ) from refused
    db.commit()
    return MfaStatusRead(
        enabled=True,
        pending=False,
        recovery_codes_remaining=mfa.unused_recovery_code_count(db, current_user),
    )


@router.post("/mfa/recovery-codes", response_model=RecoveryCodesRead)
def regenerate_recovery_codes(current_user: CurrentUser, db: DbSession) -> RecoveryCodesRead:
    try:
        codes = mfa.regenerate_recovery_codes(db, current_user)
    except mfa.MfaError as refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=refused.detail
        ) from refused
    db.commit()
    return RecoveryCodesRead(recovery_codes=codes)


@router.delete("/mfa", status_code=status.HTTP_204_NO_CONTENT)
def disable_mfa(
    payload: MfaCodeSubmission, current_user: CurrentUser, db: DbSession
) -> None:
    """Turn the second factor off. Requires a current code or a recovery code.

    Asking for the factor before removing it is the point: a session that has
    been hijacked — which is what a second factor is defence in depth against —
    must not be able to remove the defence. A recovery code is accepted because
    "my phone is gone" is the ordinary reason to do this.
    """
    now = mfa.utcnow()
    try:
        mfa.verify_code(db, current_user, payload.code, now=now)
    except mfa.MfaError as refused:
        try:
            mfa.spend_recovery_code(db, current_user, payload.code, now=now)
        except mfa.MfaError:
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That code is not valid.",
            ) from refused
    mfa.disable(db, current_user)
    db.commit()
