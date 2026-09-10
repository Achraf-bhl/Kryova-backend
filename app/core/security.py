import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = settings.jwt_algorithm


def _prehash(password: str) -> bytes:
    """bcrypt silently ignores bytes past 72; sha256 first so long passwords keep entropy."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Secrets that have to be readable again (P1.7)
# ---------------------------------------------------------------------------
#
# Almost every credential here is hashed, because nothing needs the original
# back. A TOTP shared secret is the exception: validating a code requires the
# secret itself, so it must be stored recoverably.
#
# **What this buys and what it does not.** The key is derived from
# `SECRET_KEY`, which lives in the environment, so an attacker who has the host
# has both the ciphertext and the key and this stops them cold for zero seconds.
# What it does defend is the much more common shape: a database compromised on
# its own — a leaked backup, a read-only replica, SQL injection, a misconfigured
# managed instance. In that case every second factor stays a second factor. That
# is a real distinction and it is why this is worth the twenty lines; it is not
# a claim that the secrets are safe from a rooted machine, and nothing in the
# product says otherwise.
#
# A KMS or an HSM-held key is the upgrade, and it changes only `_at_rest_key`.

_AT_REST_INFO = b"kryova-secret-at-rest-v1"
_AT_REST_VERSION = "v1"


def _at_rest_key() -> bytes:
    """A 256-bit key derived from `SECRET_KEY` for AES-GCM.

    HKDF rather than a bare SHA-256 of the key: `SECRET_KEY` is also the JWT
    signing key, and deriving with a distinct `info` string means the two uses
    cannot be made to interact even if one of them is later attacked.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    derived: bytes = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_AT_REST_INFO
    ).derive(settings.secret_key.encode("utf-8"))
    return derived


def encrypt_at_rest(plaintext: str) -> str:
    """AES-256-GCM, returned as `v1:<nonce>:<ciphertext>` in base64url.

    Versioned in the string so a future key rotation or algorithm change can
    read old rows rather than orphaning every enrolled second factor — the
    failure mode of an unversioned envelope is that the upgrade silently locks
    everybody out of their own account.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_at_rest_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    encode = base64.urlsafe_b64encode
    return f"{_AT_REST_VERSION}:{encode(nonce).decode()}:{encode(sealed).decode()}"


def decrypt_at_rest(blob: str) -> str | None:
    """The plaintext back, or None if this cannot be read.

    None rather than an exception because the realistic cause is a rotated
    `SECRET_KEY`, and the caller's honest response to that is "this second
    factor can no longer be validated, ask the user to re-enrol" — not a 500.
    """
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        version, nonce_b64, sealed_b64 = blob.split(":", 2)
    except ValueError:
        return None
    if version != _AT_REST_VERSION:
        return None
    try:
        decode = base64.urlsafe_b64decode
        opened = AESGCM(_at_rest_key()).decrypt(decode(nonce_b64), decode(sealed_b64), None)
    except (InvalidTag, ValueError, TypeError):
        return None
    return opened.decode("utf-8")


ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

#: Short-lived token issued when a password was right and a second factor is
#: still owed. It is *not* an access token and grants nothing: `_decode_typed`
#: refuses it everywhere a session is expected, which is the only reason it is
#: safe to hand to a browser mid-login.
MFA_CHALLENGE_TOKEN_TYPE = "mfa_challenge"

#: Five minutes to type six digits. Long enough to open an authenticator app and
#: read a code that is about to roll over; short enough that a challenge left in
#: a browser's memory is not a standing half-credential.
MFA_CHALLENGE_MINUTES = 5


def create_mfa_challenge_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject,
            "exp": now + timedelta(minutes=MFA_CHALLENGE_MINUTES),
            "iat": now,
            "type": MFA_CHALLENGE_TOKEN_TYPE,
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_mfa_challenge_token(token: str) -> str | None:
    """Whose half-finished login this is, or None."""
    return _decode_typed(token, MFA_CHALLENGE_TOKEN_TYPE)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    return jwt.encode(
        {"sub": subject, "exp": expire, "iat": now, "type": ACCESS_TOKEN_TYPE},
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_refresh_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=settings.refresh_token_expire_days)
    unique = secrets.token_urlsafe(8)
    return jwt.encode(
        {"sub": subject, "exp": expire, "iat": now, "type": REFRESH_TOKEN_TYPE, "jti": unique},
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def _decode_typed(token: str, expected_type: str) -> str | None:
    """Subject of `token`, but only if it is a valid JWT of exactly `expected_type`.

    The type claim is load-bearing, not decorative. Access and refresh tokens are
    signed with the same key, so without this check a 30-day refresh token is
    accepted anywhere a 15-minute access token is -- and because a bearer header
    also bypasses the CSRF check, that would be a long-lived, CSRF-exempt,
    full-privilege credential. Reject anything whose type is missing or wrong.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def decode_refresh_token(token: str) -> str | None:
    """Return the subject of a refresh token, or None if it is not one."""
    return _decode_typed(token, REFRESH_TOKEN_TYPE)


def decode_access_token(token: str) -> str | None:
    """Return the subject of an access token, or None if it is not one."""
    return _decode_typed(token, ACCESS_TOKEN_TYPE)


# ---------------------------------------------------------------------------
# Impersonation (P3.3)
# ---------------------------------------------------------------------------

IMPERSONATION_TOKEN_TYPE = "impersonation"


@dataclass(frozen=True)
class ImpersonationClaims:
    """The two identities a staff token carries, and the session behind them.

    `subject` is who the work runs as; `actor` is who is really doing it. They
    are separate claims, never one "effective user": a token that carried only
    the subject would be indistinguishable from that user's own credential, and
    every row it produced would libel them.

    `session_id` is what makes the token revocable. The mode is *not* carried
    here on purpose -- it is read from `impersonation_sessions` on every
    request, so ending or escalating a session takes effect at once and cannot
    be outvoted by a token minted before the change.
    """

    actor_id: str
    subject_id: str
    session_id: str


def create_impersonation_token(
    *, actor_id: str, subject_id: str, session_id: str, expires_at: datetime
) -> str:
    """Mint a token that runs as `subject_id` while naming `actor_id`.

    Signed with the same key as everything else, and distinguished by `type` --
    which is exactly why `_decode_typed` refuses to accept one anywhere an
    access token is expected. Without that check an impersonation token would
    be a normal login as the subject, with the actor's name silently dropped.
    """
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": subject_id,
            "act": actor_id,
            "sid": session_id,
            "exp": expires_at,
            "iat": now,
            "type": IMPERSONATION_TOKEN_TYPE,
        },
        settings.secret_key,
        algorithm=ALGORITHM,
    )


def decode_impersonation_token(token: str) -> ImpersonationClaims | None:
    """Both identities, or None if this is not a valid impersonation token."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("type") != IMPERSONATION_TOKEN_TYPE:
        return None
    actor, subject, session_id = payload.get("act"), payload.get("sub"), payload.get("sid")
    if not (isinstance(actor, str) and isinstance(subject, str) and isinstance(session_id, str)):
        return None
    return ImpersonationClaims(actor_id=actor, subject_id=subject, session_id=session_id)
