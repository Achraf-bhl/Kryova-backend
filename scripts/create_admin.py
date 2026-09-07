"""Create (or re-point) a local account and give it platform-admin standing.

**There is deliberately no API that mints staff** (`app/models/audit.py`): an
endpoint that creates a `StaffGrant` escalates whoever can reach it, and the
first such caller would be whoever compromised a support account. Grants are
provisioned out of band by a human with the database password, and this is that
route made repeatable instead of retyped into a REPL at 2am.

Two things it does that the register endpoint cannot:

* **It accepts a password the API's own policy refuses.** `UserCreate` requires
  eight characters; `OAuth2PasswordRequestForm` on the login route imposes no
  length at all. So a short development password (`admin`) can be *used* to sign
  in but not *registered*, and the account has to be made here. That asymmetry is
  correct for production and inconvenient for exactly one machine, this one.
* **It grants staff standing**, which nothing reachable over HTTP does.

Idempotent, because the alternative is a script nobody dares re-run: an existing
account has its password and name reset rather than raising, and an existing live
grant of the same role is left alone rather than duplicated. Re-running it *is*
the way to reset a forgotten development password.

    venv\\Scripts\\python.exe scripts\\create_admin.py
    venv\\Scripts\\python.exe scripts\\create_admin.py --email a@b.dev --password s3cret --role support

Refuses to run against anything but a local database unless `--i-know` is
passed. The point of the guard is not that the operation is dangerous in itself
but that its blast radius is an account with unrestricted platform standing, and
a `DATABASE_URL` left pointing at Neon is the way that lands somewhere real.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.audit import StaffGrant, StaffRole
from app.models.user import User

#: Hosts a platform-admin grant may be created on without `--i-know`. Matched
#: against the URL's host rather than the whole string so a password containing
#: the word "localhost" cannot talk its way past the check.
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _host_of(url: str) -> str:
    from sqlalchemy.engine import make_url

    return (make_url(url).host or "").lower()


def create_admin(
    email: str,
    password: str,
    role: StaffRole,
    full_name: str | None = None,
) -> tuple[User, bool, bool]:
    """Return the account, whether it was created, and whether a grant was added."""
    email = email.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email))
        created = user is None
        if user is None:
            user = User(email=email, hashed_password=hash_password(password))
            if full_name is not None:
                user.full_name = full_name
            session.add(user)
        else:
            user.hashed_password = hash_password(password)
            user.is_active = True
            if full_name is not None:
                user.full_name = full_name

        # The id carries a Python-side default that SQLAlchemy applies *during*
        # the flush, so `user.id` is None until this line runs -- and the
        # StaffGrant below is a foreign key to it. Reading it before the flush is
        # the mistake that bit three separate times on 2026-09-06 (CLAUDE.md).
        session.flush()

        live = session.scalar(
            select(StaffGrant).where(
                StaffGrant.user_id == user.id,
                StaffGrant.role == role,
                StaffGrant.revoked_at.is_(None),
            )
        )
        granted = live is None
        if live is None:
            session.add(
                StaffGrant(
                    user_id=user.id,
                    role=role,
                    reason=(
                        "Local development and GUI verification account, "
                        f"provisioned out of band {datetime.now(timezone.utc):%Y-%m-%d}."
                    ),
                )
            )
        session.commit()
        session.refresh(user)
        return user, created, granted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="admin@admin.com")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--full-name", default="Kryova Admin")
    parser.add_argument(
        "--role",
        default=StaffRole.PLATFORM_ADMIN.value,
        choices=[r.value for r in StaffRole],
    )
    parser.add_argument(
        "--i-know",
        action="store_true",
        help="Create the grant even though DATABASE_URL is not a local host.",
    )
    args = parser.parse_args(argv)

    host = _host_of(settings.database_url)
    if host not in LOCAL_HOSTS and not args.i_know:
        print(
            f"Refusing: DATABASE_URL points at {host!r}, not a local server.\n"
            "This creates an account with platform-admin standing. If that is\n"
            "genuinely what you want on this database, pass --i-know.",
            file=sys.stderr,
        )
        return 2

    user, created, granted = create_admin(
        email=args.email,
        password=args.password,
        role=StaffRole(args.role),
        full_name=args.full_name,
    )
    print(f"{'Created' if created else 'Updated'} {user.email} (id {user.id}) on {host or 'local'}")
    print(f"{'Granted' if granted else 'Already held'} {args.role}")
    print(f"Sign in with: {args.email} / {args.password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
