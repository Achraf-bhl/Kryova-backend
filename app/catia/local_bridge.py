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

import hashlib
import hmac
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

#: Minimum time the bridge stays with an account after being handed over.
#: Without it, two accounts whose requests interleave -- one engineer working,
#: another session polling -- kill each other's daemon every couple of seconds
#: and neither ever holds a connection long enough to use it. Observed live as
#: an attach/connect/respawn loop in the bridge log.
HANDOVER_HOLD_S = 30.0

_lock = threading.Lock()
_process: subprocess.Popen[bytes] | None = None
#: Whose device the running daemon is paired as. One daemon serves one device
#: row, and a device row belongs to one account -- so a daemon alive for any
#: OTHER account is not "the bridge is up", it is the bridge being held by
#: someone else. See the handover in `ensure_started`.
_process_user_id: str | None = None
_last_handover: float = 0.0
_last_attempt: float = 0.0
#: Why the bridge is not up, per account. Not one global string: the reasons
#: are now per-user ("another account holds it"), and a single slot showed one
#: account the message meant for another -- including telling the account that
#: actually owned the daemon that someone else had it.
_last_error: dict[str, str] = {}


def _device_token(device_id: str) -> str:
    """The local daemon's credential, derived rather than drawn at random.

    A random token per server process looks obviously right and is the bug that
    made this whole feature fail on the user's own machine. Two backends were
    up -- one left over, one started by the desktop app -- and both supervise
    the same device row. Each minted a token and overwrote the other's hash, so
    whichever daemon connected always presented a credential the last mint had
    just invalidated: every handshake came back 403, forever, and the assistant
    sat waiting 25 seconds per `catia_*` call for a bridge that could never
    attach. Any two backend processes do it -- `--reload`, two workers, a
    forgotten terminal.

    Deriving it from the server's signing key and the device id removes the
    race instead of narrowing it: every process computes the same token, so
    there is nothing to invalidate. It is never stored (the row still keeps
    only its SHA-256), it is scoped to one device, and rotating `SECRET_KEY`
    rotates it.
    """
    digest = hmac.new(
        settings.secret_key.encode("utf-8"),
        f"catia-local-bridge:{device_id}".encode(),
        hashlib.sha256,
    )
    return digest.hexdigest()


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


def last_error(user_id: str | None = None) -> str | None:
    """Why the bridge is not up for this account, for a status payload to explain."""
    if user_id is None:
        return None
    return _last_error.get(user_id)


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

    token = _device_token(device.id)
    expected = hash_token(token)
    expiry = device.token_expires_at
    # Written only when it would actually change. Rewriting the same hash on
    # every turn is harmless but pointless, and a no-op here is what makes two
    # backend processes agree instead of fighting.
    if device.token_hash != expected or expiry is None or expiry <= utcnow():
        device.token_hash = expected
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

    A signed-in user asking anything about CATIA is that person using this
    workstation, so the daemon follows them -- there is no caller that has to
    settle for someone else's daemon. `HANDOVER_HOLD_S` is what stops two
    accounts polling at once from killing each other's daemon every couple of
    seconds; whoever takes it keeps it long enough to actually model something.
    """
    global _process, _process_user_id, _last_handover, _last_attempt

    if not is_supported():
        return False

    try:
        if _connected(db, user_id):
            _last_error.pop(user_id, None)
            return True

        with _lock:
            # Re-checked under the lock: two agent turns arriving together must
            # not both spawn a daemon.
            if _connected(db, user_id):
                _last_error.pop(user_id, None)
                return True

            alive = _process is not None and _process.poll() is None
            handover = False
            if alive and _process_user_id != user_id:
                now = time.monotonic()
                if now - _last_handover < HANDOVER_HOLD_S:
                    # Two accounts fighting over one workstation. The holder
                    # keeps it for a beat; better one of them works than
                    # neither.
                    _last_error[user_id] = (
                        "The CATIA bridge on this machine is serving another "
                        "signed-in account; it becomes available again shortly."
                    )
                    return False
                # The machine's one daemon is paired as ANOTHER account's
                # device, so for this caller it is worse than no daemon: the
                # old logic saw "a process is alive", spawned nothing, waited
                # the full CONNECT_TIMEOUT_S for a connection on this user's
                # device that could never arrive, and the assistant reported
                # "no CATIA bridge is connected" on every turn. Observed live:
                # the test account's daemon held the slot, and the engineer's
                # own account was locked out of its own workstation. A desktop
                # machine follows whoever is using it -- hand the bridge over.
                logger.info(
                    "Handing the local CATIA bridge over to another account "
                    "(pid %s served a different user)",
                    _process.pid,
                )
                _terminate(_process)
                _process = None
                _process_user_id = None
                _last_handover = now
                alive = False
                handover = True

            if not alive:
                now = time.monotonic()
                # The cooldown exists so a machine that CANNOT start a daemon
                # (no pywin32, broken venv) does not spawn a doomed process on
                # every agent turn. A handover is the opposite case -- the
                # previous spawn succeeded and is being replaced on purpose --
                # so it must not sit out the other account's cooldown.
                if not handover and _last_attempt and now - _last_attempt < RETRY_COOLDOWN_S:
                    return False
                _last_attempt = now

                provisioned = _provision(db, user_id)
                if provisioned is None:
                    _last_error[user_id] = (
                        "The local CATIA bridge has been revoked for this account."
                    )
                    return False
                device, token = provisioned
                _process = _spawn(device, token)
                if _process is None:
                    _last_error[user_id] = (
                        "Kryova could not start the CATIA bridge process on this machine. "
                        f"See {_log_path()}."
                    )
                    return False
                _process_user_id = user_id
                _last_error.pop(user_id, None)
                logger.info("Started the local CATIA bridge (pid %s)", _process.pid)

        if wait_s <= 0:
            return _connected(db, user_id)

        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if _connected(db, user_id):
                return True
            if _process is not None and _process.poll() is not None:
                _last_error[user_id] = (
                    "The CATIA bridge exited immediately after starting. "
                    f"See {_log_path()} for why."
                )
                return False
            time.sleep(CONNECT_POLL_S)
        return _connected(db, user_id)
    except Exception:
        logger.warning("Local CATIA bridge supervision failed", exc_info=True)
        return False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """End a daemon process, escalating from terminate to kill."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def stop() -> None:
    """Stop a daemon this process started, on shutdown."""
    global _process, _process_user_id
    with _lock:
        process, _process = _process, None
        _process_user_id = None
    if process is None:
        return
    _terminate(process)
