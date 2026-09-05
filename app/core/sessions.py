"""Issuing, rotating and revoking refresh-token families — master plan P1.2.

The rules live here rather than in `api/routes/auth.py` for the reason every
service in this codebase is separated from its route: the interesting behaviour
is a state machine over rows, and a state machine tested through HTTP is tested
at one remove, through a layer that can hide a bug by returning the same 401 for
four different reasons. Every branch below is reachable from a plain function
call with a session and a token.

**The state machine, which is the whole of P1.2.** A family is a row. It holds
the hash of the token that is currently valid and the hash of the one it
replaced, and a presented token can land in exactly one of five places:

1. **It matches the current hash.** Rotate: mint a new token, move the current
   hash to previous, and stamp `rotated_at`. This is the ordinary path.
2. **It matches the previous hash, within the grace window.** Two tabs refreshed
   together, or a request was retried after a dropped connection. Serve it —
   with the *current* token, not a fresh rotation, so the racing callers converge
   on one token instead of rotating each other's away in a loop.
3. **It matches the previous hash, outside the grace window.** This is the theft
   signal. The token was captured and replayed after the legitimate client had
   already moved on. **Revoke the whole family**, and say so: rotation without
   this half is theatre.
4. **It matches nothing.** Either forged, or from a family that was revoked and
   swept. Refused, and deliberately with the same message as (3) receives — the
   caller is not told which, because the difference is information about whether
   a guess was close.
5. **It matches a family that is revoked or past its absolute expiry.** Refused.
   The row is the truth, not the cookie: a cookie that survives a "sign out
   everywhere" must stop working, and it does because this looks the family up.

**Why case 2 returns the current token rather than rotating again.** Rotating on
a grace-window hit would give the two racing tabs two different valid tokens, one
of which is immediately stale — and the loser's next refresh would then land in
case 3 and revoke the family as theft. The bug would look exactly like an attack,
which is the worst kind of false positive to debug.

**Timing.** The lookup is by hash of the presented token, so the database does
the comparison and there is no secret-dependent branch here to time. The hash
itself is SHA-256 of a high-entropy random token, not a password: bcrypt would be
wrong — it is slow by design, and this runs on every refresh of every device.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_token, new_refresh_token
from app.models.base import utcnow
from app.models.session import SessionRevocation, UserSession
from app.models.user import User

#: How a user agent becomes something a person recognises in a device list.
#: Deliberately crude and ordered most-specific first: the alternative is a user
#: agent parsing library, which is a dependency and a rotting table, for a string
#: whose only job is to let someone tell their laptop from their phone.
_DEVICE_HINTS: tuple[tuple[str, str], ...] = (
    ("Tauri", "Kryova desktop"),
    ("Electron", "Desktop app"),
    ("Android", "Android device"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Macintosh", "Mac"),
    ("Windows", "Windows PC"),
    ("Linux", "Linux PC"),
)


class SessionError(Exception):
    """A refresh that cannot be served, with the reason and whether it was theft.

    Carries `compromised` so the route can decide what the *user* is told —
    which is the one case where a 401 is not the whole story, because a family
    revoked for reuse means someone had their token and they need to know.
    """

    def __init__(self, detail: str, *, compromised: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.compromised = compromised


@dataclass(frozen=True)
class IssuedToken:
    """A refresh token and the family row it belongs to.

    The plaintext token exists only here and in the response cookie; the row
    holds its hash. Returning them together is what lets the caller set the
    cookie without going back to the database for the row it just wrote.
    """

    token: str
    session: UserSession


def device_label(user_agent: str | None) -> str | None:
    """A short name for the device, or None when there is nothing to go on.

    None rather than "Unknown device": the device list can render its own
    placeholder, and a stored literal "Unknown" is indistinguishable from a
    device that genuinely reported that name.
    """
    if not user_agent:
        return None
    for needle, label in _DEVICE_HINTS:
        if needle in user_agent:
            return label
    return None


def start_session(
    db: Session,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    """Sign in on a device: a new family, with its own absolute deadline.

    The row is added but **not committed** — the caller owns the transaction, so
    that issuing a session and whatever else a login does land together or not
    at all. A session row committed beside a failed login is a credential for an
    event that did not happen.
    """
    moment = now or utcnow()
    token = new_refresh_token()
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        previous_token_hash=None,
        rotated_at=moment,
        last_used_at=moment,
        absolute_expires_at=moment
        + timedelta(days=settings.refresh_token_expire_days),
        device_label=device_label(user_agent),
        # Truncated to the column width rather than left to the database to
        # refuse: a 4 kB user agent is a header anyone can send, and a login
        # that fails on one is a denial of service with no attacker skill.
        user_agent=(user_agent or "")[:400] or None,
        ip_address=(ip_address or "")[:45] or None,
    )
    db.add(session)
    return IssuedToken(token=token, session=session)


def rotate_session(
    db: Session,
    token: str,
    *,
    ip_address: str | None = None,
    now: datetime | None = None,
) -> IssuedToken:
    """Exchange a refresh token for the next one in its family.

    Raises `SessionError` for every refusal, and the five cases it distinguishes
    are set out in this module's docstring. The caller sees one exception type
    with a message; only the `compromised` flag separates theft from an ordinary
    stale token, and that is deliberate — the *message* must not tell a caller
    how close their guess was.
    """
    moment = now or utcnow()
    presented = hash_token(token)

    session = db.scalars(
        select(UserSession).where(UserSession.token_hash == presented)
    ).first()

    if session is not None:
        return _rotate_current(db, session, moment=moment, ip_address=ip_address)

    replayed = db.scalars(
        select(UserSession).where(UserSession.previous_token_hash == presented)
    ).first()

    if replayed is None:
        raise SessionError("Invalid refresh token")

    if replayed.is_live(moment) and replayed.within_grace(moment):
        # Case 2: a race, not an attack. Hand back the token that is already
        # current rather than rotating again — see the module docstring for why
        # rotating here turns a flaky connection into a revoked family.
        replayed.last_used_at = moment
        return IssuedToken(token=token, session=replayed)

    # Case 3: the token was rotated away and has come back. Whoever presented it
    # is not the client that rotated it, because that client holds the new one.
    _revoke_family(db, replayed, SessionRevocation.TOKEN_REUSE, now=moment)
    raise SessionError(
        "This session was signed out because a refresh token was used twice, "
        "which means it was copied. Sign in again; if this was not you, change "
        "your password.",
        compromised=True,
    )


def _rotate_current(
    db: Session,
    session: UserSession,
    *,
    moment: datetime,
    ip_address: str | None,
) -> IssuedToken:
    """Case 1: the ordinary rotation."""
    if not session.is_live(moment):
        if session.revoked_at is None:
            # Past the absolute cap. Recorded rather than merely refused, so a
            # device list shows *why* it ended and a sweep job has nothing left
            # to do.
            session.revoke(SessionRevocation.EXPIRED, now=moment)
        raise SessionError("This session has ended. Sign in again.")

    token = new_refresh_token()
    session.previous_token_hash = session.token_hash
    session.token_hash = hash_token(token)
    session.rotated_at = moment
    session.last_used_at = moment
    if ip_address:
        session.ip_address = ip_address[:45]
    return IssuedToken(token=token, session=session)


def _revoke_family(
    db: Session,
    session: UserSession,
    reason: SessionRevocation,
    *,
    now: datetime | None = None,
) -> int:
    """End every live row in a family. Returns how many were ended.

    By family rather than by row, because the family is the device and a future
    design that appends a row per rotation must revoke the chain, not its tip.
    Today that is one row; writing it this way means the day it is not, nothing
    silently keeps working.
    """
    moment = now or utcnow()
    rows = db.scalars(
        select(UserSession).where(
            UserSession.family_id == session.family_id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        row.revoke(reason, now=moment)
    return len(rows)


def revoke_session(
    db: Session, session: UserSession, reason: SessionRevocation
) -> int:
    """Sign out one device."""
    return _revoke_family(db, session, reason)


def revoke_all(
    db: Session,
    user: User,
    reason: SessionRevocation,
    *,
    except_session_id: str | None = None,
) -> int:
    """Sign out everywhere, optionally sparing the device asking.

    Sparing the caller is what makes "sign out my other devices" a usable
    action: without it the person who has just noticed something wrong is
    themselves signed out, and their next act is to sign back in — creating a
    fresh session while the attacker's is already gone, which is fine, but they
    lose the page they were on and the sense that they were in control of it.
    """
    moment = utcnow()
    rows = db.scalars(
        select(UserSession).where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    ended = 0
    for row in rows:
        if except_session_id is not None and row.id == except_session_id:
            continue
        row.revoke(reason, now=moment)
        ended += 1
    return ended


def live_sessions(db: Session, user: User) -> list[UserSession]:
    """This user's signed-in devices, newest use first."""
    moment = utcnow()
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_used_at.desc())
    ).all()
    return [row for row in rows if row.absolute_expires_at > moment]


__all__ = [
    "IssuedToken",
    "SessionError",
    "device_label",
    "live_sessions",
    "revoke_all",
    "revoke_session",
    "rotate_session",
    "start_session",
]
