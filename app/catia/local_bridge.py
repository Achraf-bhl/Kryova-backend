"""Start and pair the CATIA bridge daemon on this machine, without asking.

Kryova's premise is that the assistant does the CAD work. On a desktop install
-- server, daemon and CATIA all on the engineer's own Windows box -- the pairing
flow got in the way of that premise rather than protecting anything.

The observed failure, in full: the user asks for a 30 mm x 150 mm steel shaft.
`open_in_catia` launches CATIA and creates `Part1.CATPart`, because that path
drives COM directly and needs no pairing. The `catia_*` modelling tools then
find no connected device, because nothing had ever run
`kryova-catia-bridge pair`, and the assistant answers by asking the user to pair
a workstation or upload a CAD file -- with CATIA open on screen, holding a part
it had just created itself. Every ingredient was present and the product still
handed the work back.

The pairing code exists to authenticate *a different machine's* daemon to an
account: someone reads a code off a screen and types it on the workstation. When
the daemon runs beside the server, as the same OS user, that ceremony is the
server authenticating to itself. So this module skips it -- it mints the device
token directly, hands it to the daemon it spawns, and waits for the socket.

Four things bound it.

**Local only.** Windows, `catia_enabled`, and `catia_local_bridge` (a setting, so
a hosted deployment can refuse outright). A server that is not the user's own
machine has no business spawning processes on behalf of an account.

**Revocation is honoured.** If the local device row has been revoked, nothing is
started. Revoking is the user saying no, and an auto-pairing that resurrects a
revoked device is a back door with a friendly name.

**Bounded and cooled down.** A failed start is not retried for
`RETRY_COOLDOWN_S`, so a machine without pywin32 does not spawn a process per
agent turn. The daemon is spawned with `--wait-for-catia`, so it attaches by
itself whenever CATIA appears rather than exiting when it is not there yet.

**The token never touches disk here.** It goes over the environment to the child
(see `ENV_TOKEN` in the daemon's config module) and dies with it, so a
supervised daemon leaves no credential behind for the next person on the box.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catia.connection import registry
from app.core.config import BASE_DIR, settings
from app.core.security import hash_token
from app.models.base import utcnow
from app.models.catia import CatiaDevice, CatiaDeviceStatus

logger = logging.getLogger(__name__)

#: The name of the device row this module owns. Matching by name is what makes
#: the row findable again without a schema change, and what keeps this module
#: from ever touching a workstation the engineer paired by hand.
LOCAL_DEVICE_NAME = "This workstation"

#: How long to wait for the daemon's WebSocket after spawning it. It has to
#: cover interpreter start plus a COM attach to a running CATIA; it does *not*
#: have to cover CATIA starting up, because a caller that has not launched CATIA
#: yet should be told so rather than blocked for a minute.
CONNECT_TIMEOUT_S = 25.0
CONNECT_POLL_S = 0.25

#: Not retried more often than this after a failure.
RETRY_COOLDOWN_S = 60.0

_lock = threading.Lock()
_process: subprocess.Popen[bytes] | None = None
_last_attempt: float = 0.0
_last_error: str | None = None
#: Minted once per process and reused, so restarting the daemon does not
#: invalidate the token a still-connected one is holding.
_tokens: dict[str, str] = {}


def _daemon_package() -> Path | None:
    package = BASE_DIR / "scripts" / "catia_bridge"
    return package if package.is_dir() else None


def is_supported() -> bool:
    """Whether auto-starting a local daemon is possible and permitted here."""
    return (
        sys.platform == "win32"
        and settings.catia_enabled
        and settings.catia_local_bridge
        and _daemon_package() is not None
    )


def last_error() -> str | None:
    """Why the last start attempt failed, for a status payload to explain."""
    return _last_error


def _log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    directory = Path(base) / "Kryova" / "catia"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "local-bridge.log"


def _local_device(db: Session, user_id: str) -> CatiaDevice | None:
    return db.scalar(
        select(CatiaDevice).where(
            CatiaDevice.owner_id == user_id,
            CatiaDevice.name == LOCAL_DEVICE_NAME,
        )
    )


def _provision(db: Session, user_id: str) -> tuple[CatiaDevice, str] | None:
    """The local device row and a live token for it, or None if not allowed.

    Returns None -- rather than raising -- when the row has been revoked, so a
    caller degrades to "no CATIA" exactly as it would on a machine that never
    had one.
    """
    import secrets
    import socket

    device = _local_device(db, user_id)
    if device is not None and device.status is CatiaDeviceStatus.REVOKED:
        logger.info("The local CATIA bridge is revoked for this account; not starting it.")
        return None

    if device is None:
        device = CatiaDevice(owner_id=user_id, name=LOCAL_DEVICE_NAME)
        db.add(device)
        db.flush()

    try:
        device.hostname = socket.gethostname()[:255]
    except OSError:
        pass

    token = _tokens.get(device.id)
    expiry = device.token_expires_at
    expired = expiry is None or expiry <= utcnow()
    if token is None or device.token_hash is None or expired:
        token = secrets.token_urlsafe(48)
        _tokens[device.id] = token
        device.token_hash = hash_token(token)
        device.token_expires_at = utcnow() + timedelta(days=settings.catia_device_token_ttl_days)

    device.status = CatiaDeviceStatus.ACTIVE
    db.commit()
    return device, token


def _spawn(device: CatiaDevice, token: str) -> subprocess.Popen[bytes] | None:
    scripts = BASE_DIR / "scripts"
    environment = {
        **os.environ,
        "KRYOVA_BRIDGE_SERVER": settings.catia_local_bridge_server,
        "KRYOVA_BRIDGE_TOKEN": token,
        "KRYOVA_BRIDGE_DEVICE_ID": device.id,
        "KRYOVA_BRIDGE_DEVICE_NAME": device.name,
        # The daemon is a sibling package under scripts/, not an installed one.
        "PYTHONPATH": os.pathsep.join(filter(None, [str(scripts), os.environ.get("PYTHONPATH")])),
    }
    # No console window: this is a service the user never asked to see, and on
    # Windows a bare Popen of a console app pops one up over their CAD session.
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        log = open(_log_path(), "ab", buffering=0)  # noqa: SIM115 - handed to the child
    except OSError:
        log = None
    try:
        return subprocess.Popen(
            [sys.executable, "-m", "catia_bridge", "run", "--wait-for-catia"],
            cwd=str(scripts),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except OSError as exc:
        logger.warning("Could not start the local CATIA bridge: %s", exc)
        return None
    finally:
        if log is not None:
            log.close()


def _connected(db: Session, user_id: str) -> bool:
    device = _local_device(db, user_id)
    if device is None:
        return False
    connection = registry.get(device.id)
    return connection is not None and connection.user_id == user_id


def ensure_started(db: Session, user_id: str, *, wait_s: float = 0.0) -> bool:
    """Make sure this machine's bridge daemon is running, and optionally wait.

    Returns whether a connection is up by the time it gives up. Never raises:
    every caller is already on a path that copes with "no CATIA", and a
    convenience that can break the turn is not a convenience.
    """
    global _process, _last_attempt, _last_error

    if not is_supported():
        return False

    try:
        if _connected(db, user_id):
            return True

        with _lock:
            # Re-checked under the lock: two agent turns arriving together must
            # not both spawn a daemon.
            if _connected(db, user_id):
                return True

            alive = _process is not None and _process.poll() is None
            if not alive:
                now = time.monotonic()
                if _last_attempt and now - _last_attempt < RETRY_COOLDOWN_S:
                    return False
                _last_attempt = now

                provisioned = _provision(db, user_id)
                if provisioned is None:
                    _last_error = "The local CATIA bridge has been revoked for this account."
                    return False
                device, token = provisioned
                _process = _spawn(device, token)
                if _process is None:
                    _last_error = (
                        "Kryova could not start the CATIA bridge process on this machine. "
                        f"See {_log_path()}."
                    )
                    return False
                _last_error = None
                logger.info("Started the local CATIA bridge (pid %s)", _process.pid)

        if wait_s <= 0:
            return _connected(db, user_id)

        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if _connected(db, user_id):
                return True
            if _process is not None and _process.poll() is not None:
                _last_error = (
                    "The CATIA bridge exited immediately after starting. "
                    f"See {_log_path()} for why."
                )
                return False
            time.sleep(CONNECT_POLL_S)
        return _connected(db, user_id)
    except Exception:
        logger.warning("Local CATIA bridge supervision failed", exc_info=True)
        return False


def stop() -> None:
    """Stop a daemon this process started, on shutdown."""
    global _process
    with _lock:
        process, _process = _process, None
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
