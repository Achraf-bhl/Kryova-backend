import base64
import hashlib
import secrets
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


ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


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
