"""Where the daemon keeps its token and its working files.

The device token is a long-lived credential for one engineer's CAD session, so
where it lands matters:

* On Windows it goes in `%APPDATA%\\Kryova\\bridge.json`. That directory is
  inside the user's profile, whose ACL already excludes other standard users --
  which is the actual protection. `os.chmod(0o600)` is applied as well, but be
  clear-eyed about it: on Windows Python's chmod only toggles the read-only
  attribute, so it is belt-and-braces, not the mechanism.
* Everywhere else it goes in `$XDG_CONFIG_HOME/kryova/bridge.json` (or
  `~/.config/kryova/`), created 0700, with the file 0600. There the mode *is*
  the mechanism.

The working directory is separate from the config directory, and every path the
daemon ever touches is resolved beneath it. That is what makes "no filesystem
paths from the model" enforceable: there is no code path that turns a string
from a tool call into an arbitrary path.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BRIDGE_VERSION = "1.0.0"

DEFAULT_SERVER = "http://localhost:8000"

#: Credentials handed straight to the daemon by whatever started it, instead
#: of being read from `bridge.json`. This is the single-machine install: the
#: Kryova server is on the same box, under the same user, and it minted the
#: token itself, so the pairing round trip would be the server authenticating
#: to itself and the file would be a copy of a secret nobody else needs.
#: An environment variable is at least as private as the file (a 0600 file in
#: the profile, versus a value only this user's processes can read) and it
#: dies with the process, so a supervised daemon leaves no credential behind.
ENV_SERVER = "KRYOVA_BRIDGE_SERVER"
ENV_TOKEN = "KRYOVA_BRIDGE_TOKEN"
ENV_DEVICE_ID = "KRYOVA_BRIDGE_DEVICE_ID"
ENV_DEVICE_NAME = "KRYOVA_BRIDGE_DEVICE_NAME"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "Kryova"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "kryova"


def work_dir() -> Path:
    """Where documents and snapshots live. Never inside the config directory.

    Keeping data out of the credential directory means the working tree can be
    deleted wholesale to recover from a bad state without also un-pairing the
    workstation.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "Kryova" / "catia"
    base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    return Path(base) / "kryova" / "catia"


def lock_path() -> Path:
    """The file whose exclusive lock means "a bridge is already running here"."""
    return work_dir() / "bridge.lock"


class AlreadyRunning(RuntimeError):
    """Another bridge daemon holds the single-instance lock."""


def acquire_single_instance_lock() -> Any:
    """Take the machine-wide bridge lock, or raise `AlreadyRunning`.

    Two daemons for one device do not coexist quietly: the server's registry
    keeps the newest socket and closes the older one, whose owner reconnects
    and displaces it right back. The pair flap forever and every call lands on
    whichever happens to hold the slot.

    A duplicate is easy to end up with -- the backend supervises one, the
    desktop app spawns another, and a second backend process supervises a third.
    So the daemon refuses to be the second one rather than relying on whoever
    starts it to check.

    The lock is an open file handle held for the process's lifetime; Windows
    releases it when the process dies, including on a kill, so a crashed daemon
    leaves nothing to clean up. The handle is returned and must stay referenced.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - held for the process lifetime
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(
            f"Another Kryova CATIA bridge is already running on this machine "
            f"({path} is locked). Only one may be connected at a time."
        ) from exc
    return handle


def config_path() -> Path:
    return config_dir() / "bridge.json"


@dataclass
class BridgeConfig:
    server: str
    device_token: str
    device_id: str = ""
    device_name: str = ""

    @property
    def websocket_url(self) -> str:
        """The bridge endpoint, derived from the server's own base URL.

        Derived rather than configured separately: two URLs that must agree and
        are set independently will eventually disagree, and the failure ("it
        pairs but never connects") is opaque.
        """
        base = self.server.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/api/v1/catia/bridge/ws"
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :] + "/api/v1/catia/bridge/ws"
        raise ValueError(f"server must start with http:// or https://, got {self.server!r}")

    @property
    def pair_url(self) -> str:
        return self.server.rstrip("/") + "/api/v1/catia/devices/pair"


class ConfigError(RuntimeError):
    """The daemon is not paired, or its stored configuration is unusable."""


def save(config: BridgeConfig) -> Path:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    _restrict(directory, stat.S_IRWXU)

    path = config_path()
    # Write with the restrictive mode from the start. Creating the file world
    # readable and chmod-ing afterwards leaves a window in which the token is
    # readable, and that window is all an attacker needs.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "server": config.server,
                "device_token": config.device_token,
                "device_id": config.device_id,
                "device_name": config.device_name,
            },
            handle,
            indent=1,
        )
    _restrict(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_env() -> BridgeConfig | None:
    """Credentials from the environment, or None if none were handed over.

    Checked before the file so a supervisor can override a stale pairing --
    a `bridge.json` left over from a different server is otherwise a config the
    daemon will keep failing to use, and the user never asked for either.
    """
    token = os.environ.get(ENV_TOKEN)
    if not token:
        return None
    return BridgeConfig(
        server=os.environ.get(ENV_SERVER) or DEFAULT_SERVER,
        device_token=token,
        device_id=os.environ.get(ENV_DEVICE_ID) or "",
        device_name=os.environ.get(ENV_DEVICE_NAME) or "",
    )


def load() -> BridgeConfig:
    from_env = load_env()
    if from_env is not None:
        return from_env

    path = config_path()
    if not path.is_file():
        raise ConfigError(
            f"This workstation is not paired yet ({path} does not exist).\n"
            "In Kryova, open Settings > CATIA bridge, click Connect CATIA, then run:\n"
            "  kryova-catia-bridge pair --code <CODE>"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{path} could not be read: {exc}. Pair again to rewrite it.") from exc

    token = data.get("device_token")
    if not token:
        raise ConfigError(f"{path} holds no device token. Pair again.")
    return BridgeConfig(
        server=data.get("server") or DEFAULT_SERVER,
        device_token=token,
        device_id=data.get("device_id") or "",
        device_name=data.get("device_name") or "",
    )


def _restrict(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:  # pragma: no cover - unusual filesystems
        # Not fatal on Windows, where the profile ACL is the real control.
        pass
