"""Start this machine's own Postgres when the server needs it and it is down.

The development workstation runs PostgreSQL from its zip archive -- no
installer, no Windows service, no admin rights (docs/LOCAL_POSTGRES.md). That
choice has one standing cost: **the database does not survive a reboot**. Every
launch of the desktop app after a restart spawned uvicorn against a database
that was not there, the lifespan's first query waited, and the user was shown
"The API server is not reachable. Start it with uvicorn" -- advice that was
wrong twice over, because uvicorn *was* running and the thing to start was
Postgres. It happened often enough to be the first thing seen on most days.

So the server starts it. Three rules keep that from being a surprise:

1. **Opt-in, by naming the install.** With `LOCAL_POSTGRES_BIN_DIR` and
   `LOCAL_POSTGRES_DATA_DIR` unset nothing is ever started, and a deployment
   that never heard of this is unaffected.
2. **Only for a database on this machine.** A URL naming another host is
   somebody else's server; starting a local one beside it would answer a
   question nobody asked, and connect to nothing.
3. **A failure raises, in words, with the server's own log.** A server that
   boots against a database that is not there is the hang this replaces.

`pg_ctl status` decides whether it is running rather than probing the port:
it is definitive, it is instant where a refused connect on Windows costs two
seconds per address, and `pg_ctl start` already clears a stale
`postmaster.pid` left by the reboot itself.
"""

from __future__ import annotations

import enum
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import make_url

logger = logging.getLogger(__name__)

#: Host spellings that mean "this machine". An empty host is a Unix socket,
#: which is local by definition.
LOOPBACK_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})

#: `pg_ctl status` exit codes, from the pg_ctl reference page: 0 running,
#: 3 not running, 4 no accessible data directory.
STATUS_RUNNING = 0
STATUS_NOT_RUNNING = 3

#: How long `pg_ctl start -w` waits for the server to accept connections. A
#: cold start after a crash replays WAL, so this is generous on purpose.
START_WAIT_SECONDS = 60

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]


class LocalPostgresError(RuntimeError):
    """The local database was needed, configured, and could not be started."""


class Action(enum.StrEnum):
    NOT_CONFIGURED = "not-configured"
    NOT_LOCAL = "not-local"
    ALREADY_RUNNING = "already-running"
    STARTED = "started"


@dataclass(frozen=True)
class Outcome:
    action: Action
    detail: str


def is_local(database_url: str) -> bool:
    host = make_url(database_url).host or ""
    return host.strip("[]").lower() in LOOPBACK_HOSTS


def _pg_ctl(bin_dir: Path) -> Path:
    name = "pg_ctl.exe" if os.name == "nt" else "pg_ctl"
    return bin_dir / name


def _quiet() -> dict[str, Any]:
    """Keep a packaged launch from flashing a console window per call."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _log_tail(log: Path, lines: int = 15) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"(no server log at {log})"
    tail = text.strip().splitlines()[-lines:]
    return "\n".join(tail) if tail else f"(the server log at {log} is empty)"


def ensure_running(
    database_url: str,
    bin_dir: str | None,
    data_dir: str | None,
    *,
    run: Runner = subprocess.run,
) -> Outcome:
    """Start the configured local Postgres if it is not running.

    Returns what it did. Raises `LocalPostgresError` when it was asked to and
    could not, naming the path or quoting the server log.
    """
    if not bin_dir or not data_dir:
        return Outcome(Action.NOT_CONFIGURED, "LOCAL_POSTGRES_BIN_DIR/DATA_DIR are not set")
    if not is_local(database_url):
        return Outcome(
            Action.NOT_LOCAL,
            f"DATABASE_URL names {make_url(database_url).host!r}, not this machine",
        )

    pg_ctl = _pg_ctl(Path(bin_dir))
    data = Path(data_dir)
    if not pg_ctl.is_file():
        raise LocalPostgresError(
            f"LOCAL_POSTGRES_BIN_DIR is set but there is no {pg_ctl.name} at {pg_ctl}. "
            "Point it at the directory holding the Postgres binaries, or unset it."
        )
    if not data.is_dir():
        raise LocalPostgresError(
            f"LOCAL_POSTGRES_DATA_DIR is set but {data} is not a directory. "
            "Point it at the cluster made by initdb, or unset it."
        )

    status = run(
        _args(pg_ctl, "status", data),
        capture_output=True,
        text=True,
        timeout=15,
        **_quiet(),
    )
    if status.returncode == STATUS_RUNNING:
        return Outcome(Action.ALREADY_RUNNING, f"Postgres is already running from {data}")
    if status.returncode != STATUS_NOT_RUNNING:
        raise LocalPostgresError(
            f"`pg_ctl status` could not read the data directory {data} "
            f"(exit {status.returncode}): {(status.stderr or status.stdout or '').strip()}"
        )

    log = log_path(data)
    logger.info("Local Postgres is not running; starting it from %s", data)
    # The output goes nowhere, deliberately. The postmaster inherits pg_ctl's
    # standard handles, so a captured pipe stays open for as long as the
    # database runs and `run` would wait on it forever. What went wrong is in
    # the server log, which is read below.
    started = run(
        [*_args(pg_ctl, "start", data), "-l", str(log), "-w", "-t", str(START_WAIT_SECONDS)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=START_WAIT_SECONDS + 30,
        **_quiet(),
    )
    if started.returncode != 0:
        raise LocalPostgresError(
            f"The local Postgres in {data} did not start (pg_ctl exit {started.returncode}). "
            f"The end of its log, {log}:\n{_log_tail(log)}"
        )
    logger.info("Local Postgres started from %s", data)
    return Outcome(Action.STARTED, f"Started Postgres from {data}")


def log_path(data: Path) -> Path:
    """The server log, beside the data directory and never inside it.

    Measured 2026-09-14 on the first start after a reboot: crash recovery
    fsyncs every file under the data directory, `server.log` inside it is held
    open by the postmaster writing to it, and Windows answers with a sharing
    violation that Postgres retries for 30 s. The documented command put the
    log there, so every post-reboot start cost half a minute for nothing.
    """
    return data.with_name(f"{data.name}.log")


def _args(pg_ctl: Path, command: str, data: Path) -> Sequence[str]:
    return [str(pg_ctl), command, "-D", str(data)]
