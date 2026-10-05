"""The desktop database's backups: listed, scheduled, and restorable (ROAD_TO_10 9.1).

`local_cluster.py` already takes a dump before an upgrade that migrates, and applies a restore
request at launch. What an installed app still lacked is the half a person meets: a backup that
happens without anyone remembering to make one, and a way to see and choose one.

* **Scheduled.** A dump at most once a day while the app runs, the first one five minutes after
  launch (not during it -- a launch is already the slow moment). Named `kryova-scheduled-...`
  with its own retention, so a week of dailies never evicts the dump taken before the last
  schema change. `pg_dump --enable-row-security`, as `CLAUDE.md` *Database* item 7 requires.
* **Listed** from the folder, never from a table: the folder is what survives a database that
  will not start, which is when a backup is wanted.
* **Restored at the next launch** (`local_cluster.request_restore`), not while serving.

Imports `local_cluster` only, and never `app.core.config`: the launcher uses this before the
environment is complete (`tests/test_local_cluster.py` pins that for `local_cluster`; the same
rule applies here because the scheduler is started from the launcher).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.core import local_cluster as lc

logger = logging.getLogger(__name__)

SCHEDULED_STEM = "kryova-scheduled"
SCHEDULED_GLOB = f"{SCHEDULED_STEM}-*.dump"
KEEP_SCHEDULED = 7
INTERVAL = timedelta(hours=24)
FIRST_CHECK_AFTER_S = 300.0
CHECK_EVERY_S = 3600.0

Kind = Literal["scheduled", "upgrade", "before-restore"]

_PARTS = re.compile(
    r"^kryova-(?P<kind>scheduled|before-(?P<label>[A-Za-z0-9_]+))-(?P<stamp>\d{8}T\d{6}Z)\.dump$"
)


@dataclass(frozen=True)
class BackupFile:
    name: str
    kind: Kind
    taken_at: datetime
    size_bytes: int
    #: The schema revision an upgrade dump preserves; None for the others.
    revision: str | None = None


def describe(path: Path) -> BackupFile | None:
    """One backup file, or None if the name is not one this app writes."""
    match = _PARTS.fullmatch(path.name)
    if match is None:
        return None
    label = match.group("label")
    kind: Kind = (
        "scheduled" if label is None else "before-restore" if label == "restore" else "upgrade"
    )
    try:
        size = path.stat().st_size
    except OSError:
        return None
    taken = datetime.strptime(match.group("stamp"), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return BackupFile(
        name=path.name,
        kind=kind,
        taken_at=taken,
        size_bytes=size,
        revision=label if kind == "upgrade" else None,
    )


def list_backups(backups: Path) -> list[BackupFile]:
    """Every backup in the folder, newest first. A missing folder is an empty list."""
    if not backups.is_dir():
        return []
    found = [item for item in (describe(path) for path in backups.glob("*.dump")) if item]
    return sorted(found, key=lambda item: (item.taken_at, item.name), reverse=True)


def due(backups: Path, now: datetime, interval: timedelta = INTERVAL) -> bool:
    """True when no scheduled dump is younger than `interval`."""
    scheduled = [item for item in list_backups(backups) if item.kind == "scheduled"]
    return not scheduled or now - scheduled[0].taken_at >= interval


def take_scheduled(
    bin_dir: Path,
    admin_url: str,
    backups: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    run: lc.Runner = subprocess.run,
) -> Path:
    """A dump now, whether or not one is due -- the "back up now" button uses this directly."""
    return lc.back_up(
        bin_dir,
        admin_url,
        backups,
        "scheduled",
        stem=SCHEDULED_STEM,
        keep_glob=SCHEDULED_GLOB,
        keep=KEEP_SCHEDULED,
        row_security=True,
        run=run,
        now=now,
    )


def take_if_due(
    bin_dir: Path,
    admin_url: str,
    backups: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    run: lc.Runner = subprocess.run,
) -> Path | None:
    """The scheduled dump if one is due, else None. Never raises: a failed backup is logged
    and tried again next hour, and must never be why the app is not serving."""
    try:
        if not due(backups, now()):
            return None
        target = take_scheduled(bin_dir, admin_url, backups, now=now, run=run)
    except (lc.ClusterError, OSError, subprocess.SubprocessError) as error:
        logger.warning("The scheduled backup did not run: %s", error)
        return None
    logger.info("Scheduled backup written: %s", target)
    return target


def admin_url_for(home: Path) -> str:
    """The admin connection to the application database, from what the install generated.

    The backend process does not hold the admin password (`apply_environment` sets only the
    application role's), so a route that makes a dump reads it from `secrets.json` the way the
    launcher did. Raises `ClusterError` if this is not an installed app's home.
    """
    data_dir = home / "pgdata"
    secrets_ = lc.load_secrets(home, cluster_exists=lc.cluster_exists(data_dir))
    port = lc.configured_port(data_dir)
    return lc.database_url(lc.ADMIN_ROLE, secrets_.admin_password, port, lc.APP_DATABASE)


class Scheduler:
    """A daemon thread that calls `take_if_due` every hour until stopped."""

    def __init__(
        self,
        bin_dir: Path,
        admin_url: str,
        backups: Path,
        *,
        first_after: float = FIRST_CHECK_AFTER_S,
        every: float = CHECK_EVERY_S,
        run: lc.Runner = subprocess.run,
    ) -> None:
        self._args = (bin_dir, admin_url, backups)
        self._first_after = first_after
        self._every = every
        self._run = run
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="kryova-backups", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = self._first_after
        while not self._stop.wait(wait):
            take_if_due(*self._args, run=self._run)
            wait = self._every
