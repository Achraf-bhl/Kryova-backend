"""The installed desktop app's own database backups (ROAD_TO_10 9.1).

`app/core/backups.py` is the logic; this is the binding. Everything here is **404 unless the
backend was started by the desktop launcher** (`KRYOVA_HOME` set): on a hosted deployment the
database is the operator's, backed up by the operator, and a route that let a tenant dump or
replace it would be a hole, so it is absent rather than refused.

On a desktop install the signed-in user is the person who owns the machine and the data, which
is why any authenticated user may use it and no staff grant is asked for. That is a statement
about the install, not about the code: if a desktop ever hosts more than one person's account,
this needs the `OWNER` role of the first organisation before it ships to them.

**Restore is deferred to the next launch** (`local_cluster.request_restore`) and says so in its
response, because restoring under a serving application is how a restore corrupts what it
replaces. The response is 202 and the body tells the user to restart Kryova.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser
from app.api.rate_limit import RateLimit
from app.core import backups, local_cluster
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/desktop", tags=["desktop"])

#: A dump of a few hundred megabytes is real work; five a minute is far past a person clicking.
_write_limit = RateLimit("desktop.backups", max_requests=5, window_seconds=60)


class BackupRead(BaseModel):
    name: str
    kind: str = Field(description="`scheduled`, `upgrade` or `before-restore`.")
    taken_at: datetime
    size_bytes: int
    revision: str | None = None


class BackupListRead(BaseModel):
    backups: list[BackupRead]
    #: The backup the next launch will restore, if a restore was requested.
    restore_pending: str | None = None
    #: Why the last restore request was not applied, if it was not.
    last_restore_failure: str | None = None


class RestoreRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RestoreAccepted(BaseModel):
    restore_pending: str
    message: str


def _home() -> Path:
    if not settings.kryova_home:
        # Not "forbidden": on a hosted deployment this feature does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    return Path(settings.kryova_home)


def _read(item: backups.BackupFile) -> BackupRead:
    return BackupRead(
        name=item.name,
        kind=item.kind,
        taken_at=item.taken_at,
        size_bytes=item.size_bytes,
        revision=item.revision,
    )


def _last_failure(home: Path) -> str | None:
    try:
        data = json.loads((home / local_cluster.RESTORE_FAILED).read_text(encoding="utf-8"))
        reason = data.get("reason") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return reason if isinstance(reason, str) else None


@router.get("/backups", response_model=BackupListRead)
def list_backups(_user: CurrentUser) -> BackupListRead:
    home = _home()
    return BackupListRead(
        backups=[_read(item) for item in backups.list_backups(home / "backups")],
        restore_pending=local_cluster.pending_restore(home),
        last_restore_failure=_last_failure(home),
    )


@router.post(
    "/backups",
    response_model=BackupRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_write_limit)],
)
def back_up_now(_user: CurrentUser) -> BackupRead:
    """Take a dump now. Counts against the scheduled retention, like the daily one."""
    home = _home()
    if not settings.local_postgres_bin_dir:
        raise HTTPException(status.HTTP_409_CONFLICT, "This install has no bundled database.")
    try:
        target = backups.take_scheduled(
            Path(settings.local_postgres_bin_dir),
            backups.admin_url_for(home),
            home / "backups",
        )
    except local_cluster.ClusterError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    item = backups.describe(target)
    if item is None:  # pragma: no cover -- take_scheduled names what describe parses
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "The backup was not readable.")
    return _read(item)


@router.post(
    "/backups/restore",
    response_model=RestoreAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(_write_limit)],
)
def request_restore(body: RestoreRequest, user: CurrentUser) -> RestoreAccepted:
    """Ask for a backup to be restored the next time Kryova starts."""
    home = _home()
    try:
        local_cluster.request_restore(home, body.name)
    except local_cluster.ClusterError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    logger.warning(
        "Restore of %s requested by user %s; applies at next launch.", body.name, user.id
    )
    return RestoreAccepted(
        restore_pending=body.name,
        message=(
            "Close Kryova and open it again to restore this backup. A copy of the current "
            "database is saved first, and nothing changes until then."
        ),
    )


@router.delete("/backups/restore", status_code=status.HTTP_204_NO_CONTENT)
def cancel_restore(_user: CurrentUser) -> None:
    """Take back a restore request before the next launch applies it."""
    local_cluster.cancel_restore(_home())
