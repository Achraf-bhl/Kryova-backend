"""What the client needs to know before it renders anything (P3.5, P3.7).

One route, three answers — resolved feature flags, live announcements, and the
maintenance notice if there is one — because they are read at the same moment,
by the same shell, and three requests to paint one page is three chances for the
page to be internally inconsistent.

**The flags are resolved here and never re-decided in the browser.** That is the
whole of Decision 6 applied to this feature: the UI and the API must agree about
what is on, or a user gets a button whose endpoint refuses, or an endpoint
nobody can reach. `core/flags.py` is the one evaluator.

**Unauthenticated is allowed and answers the global defaults.** A signed-out
visitor still needs the maintenance banner — arguably needs it most, since they
may be trying to sign in during the window — and a login page that cannot say
"we are down until 14:00" is a login page that just looks broken.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends

from app.api.deps import DbSession
from app.api.rate_limit import RateLimit
from app.core import flags, maintenance
from app.core.security import decode_access_token
from app.models import User
from app.models.organisation import organisation_ids_for_user
from app.schemas.admin import AnnouncementRead, MaintenanceNotice, PlatformStateRead

router = APIRouter(prefix="/platform", tags=["platform"])

#: Polled by both clients, so it is cheap and rate-limited per principal. The
#: budget is generous: this is one small read and a client that polls it every
#: thirty seconds is behaving correctly.
_state_limit = RateLimit("platform.state", max_requests=120, window_seconds=60)


@router.get("/state", response_model=PlatformStateRead, dependencies=[Depends(_state_limit)])
def read_platform_state(
    db: DbSession,
    access_token: Annotated[str | None, Cookie(alias="kryova_access")] = None,
) -> PlatformStateRead:
    """Flags, banners and the maintenance notice, for whoever is asking.

    The token is decoded here rather than depending on `CurrentUser`, and that
    is deliberate: `get_current_user` refuses a signed-out caller and — since
    P3.7 — also refuses *mutations* during maintenance. Depending on it would
    make the endpoint that explains the maintenance unreachable to exactly the
    people who need the explanation.

    A token that does not decode is treated as no token at all. Nothing here is
    private: the flag map is the same one the UI would infer from which buttons
    work, and an announcement is a public banner by construction.
    """
    user: User | None = None
    tenants: frozenset[str] = frozenset()
    if access_token:
        user_id = decode_access_token(access_token)
        if user_id is not None:
            user = db.get(User, user_id)
            if user is not None:
                tenants = frozenset(organisation_ids_for_user(db, user))

    window = maintenance.active_window(db)
    return PlatformStateRead(
        flags=flags.evaluate(db, user=user, organisation_ids=tenants),
        announcements=[
            AnnouncementRead.model_validate(row)
            for row in maintenance.live_announcements(db)
        ],
        maintenance=(
            MaintenanceNotice(
                # The user-facing half only. `window.reason` is the operator's
                # note and names infrastructure; it is not in this model at all
                # rather than merely omitted here.
                message=maintenance.refusal_message(window),
                expected_end_at=window.expected_end_at,
            )
            if window is not None
            else None
        ),
    )


__all__ = ["router"]
