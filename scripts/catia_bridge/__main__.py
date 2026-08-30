"""`kryova-catia-bridge` -- the command-line entry point.

Three commands, and no daemon-installer machinery:

    kryova-catia-bridge pair --code ABCD1234 [--server https://app.example.com]
    kryova-catia-bridge run [--mock]
    kryova-catia-bridge status

`status` is the one to reach for first when something is wrong: it reports
whether the workstation is paired, where the token is stored, whether CATIA is
reachable and which version, without connecting to the server -- so it separates
"the pairing is broken" from "CATIA is not running" from "the network is
blocked", which otherwise all present as "it does not work".

Run this as the desktop user, never elevated. It automates the CATIA session
that user already has open; running it as an administrator would give a remote
agent more authority over the machine than the person sitting at it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .backend import CatiaBackend, CatiaOperationError
from .bridge import run as run_bridge
from .config import (
    BRIDGE_VERSION,
    DEFAULT_SERVER,
    BridgeConfig,
    ConfigError,
    config_path,
    load,
    save,
    work_dir,
)

logger = logging.getLogger("kryova.catia")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _open_backend(mock: bool, workdir: Path) -> CatiaBackend:
    """The mock backend, or the real one -- never a silent fallback between them.

    An earlier version of this bridge fell back to mock mode when pywin32 was
    missing. That is the worst possible behaviour: the daemon reports itself
    connected, the agent builds a part, the user watches nothing happen in
    CATIA, and every number they are shown is invented. If the real backend
    cannot be opened, this exits.
    """
    from .mock_catia import MockCatia

    if mock:
        logger.warning("Running in MOCK mode: no CATIA is involved and every result is simulated.")
        return MockCatia(workdir)

    from .catia_com import CatiaCom

    return CatiaCom(workdir)


# -- pair --------------------------------------------------------------------


def command_pair(args: argparse.Namespace) -> int:
    server = args.server.rstrip("/")
    request = urllib.request.Request(
        f"{server}/api/v1/catia/devices/pair",
        data=json.dumps(
            {
                "code": args.code.strip().upper(),
                "hostname": _hostname(),
                "bridge_version": BRIDGE_VERSION,
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: dict[str, Any] = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = _detail(exc)
        print(f"Pairing failed ({exc.code}): {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(
            f"Could not reach {server}: {exc.reason}\n"
            "Check the --server URL and that this machine can reach it.",
            file=sys.stderr,
        )
        return 1

    path = save(
        BridgeConfig(
            server=server,
            device_token=payload["device_token"],
            device_id=payload.get("device_id", ""),
            device_name=payload.get("device_name", ""),
        )
    )
    print(f"Paired as {payload.get('device_name') or payload.get('device_id')}.")
    print(f"Token stored in {path} (readable only by you).")
    print("Start the bridge with:  kryova-catia-bridge run")
    return 0


def _detail(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read()).get("detail") or exc.reason)
    except Exception:  # noqa: BLE001
        return str(exc.reason)


def _hostname() -> str:
    import socket

    return socket.gethostname()


#: How often to look for CATIA while waiting for it to appear, in seconds.
CATIA_POLL_S = 5.0


def _wait_for_backend(mock: bool, workdir: Path, wait: bool) -> CatiaBackend:
    """Open the backend, optionally waiting for CATIA to be started.

    Without `--wait-for-catia` this is the original behaviour: no CATIA, no
    daemon, exit and say so. That is right for someone running the command by
    hand, who wants to be told immediately.

    It is wrong for the desktop app, which starts this at launch. There the
    ordinary sequence is that Kryova comes up first and CATIA is opened
    afterwards -- by the engineer, or by the assistant calling `open_in_catia`
    -- and a daemon that exited two seconds after login would mean the CATIA
    tools never appear no matter what the user does next. So it waits, and
    attaches when CATIA shows up.

    This never *starts* CATIA. Launching it from a background process spends a
    licence nobody asked to spend, which is the same reason the bridge attaches
    to a running session rather than creating one.
    """
    while True:
        try:
            return _open_backend(mock, workdir)
        except CatiaOperationError as exc:
            if not wait:
                raise
            logger.info("%s Waiting for CATIA...", exc)
            time.sleep(CATIA_POLL_S)


# -- run ---------------------------------------------------------------------


def command_run(args: argparse.Namespace) -> int:
    try:
        config = load()
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    workdir = work_dir()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        backend = _wait_for_backend(args.mock, workdir, args.wait_for_catia)
    except CatiaOperationError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 1

    print(f"Kryova CATIA bridge {BRIDGE_VERSION}")
    print(f"  server    {config.server}")
    print(f"  CATIA     {backend.catia_version}{' [MOCK]' if backend.is_mock else ''}")
    print(f"  workdir   {workdir}")
    print("Connecting (outbound only; no port is opened on this machine)...")
    run_bridge(config, backend)
    return 0


# -- status ------------------------------------------------------------------


def command_status(args: argparse.Namespace) -> int:
    print(f"Kryova CATIA bridge {BRIDGE_VERSION}")
    print(f"  config    {config_path()}")
    print(f"  workdir   {work_dir()}")

    try:
        config = load()
    except ConfigError as exc:
        print("  paired    no")
        print(f"\n{exc}")
        return 1
    print("  paired    yes")
    print(f"  server    {config.server}")
    print(f"  device    {config.device_name or config.device_id or '(unnamed)'}")

    # Deliberately does not connect to the server: the point is to isolate a
    # local CATIA problem from a network one.
    try:
        backend = _open_backend(args.mock, work_dir())
    except CatiaOperationError as exc:
        print(f"  CATIA     unavailable -- {exc}")
        return 1
    try:
        backend.health()
        print(f"  CATIA     {backend.catia_version}{' [MOCK]' if backend.is_mock else ''}")
    except CatiaOperationError as exc:
        print(f"  CATIA     not responding -- {exc}")
        return 1
    finally:
        backend.close()
    return 0


# -- argument parsing --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kryova-catia-bridge",
        description=(
            "Connect this CATIA workstation to Kryova. The bridge dials out over a "
            "WebSocket and never opens a port on this machine."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    commands = parser.add_subparsers(dest="command", required=True)

    pair = commands.add_parser("pair", help="Redeem a pairing code from Kryova.")
    pair.add_argument("--code", required=True, help="The 8-character code Kryova showed.")
    pair.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"Kryova base URL (default {DEFAULT_SERVER}).",
    )
    pair.set_defaults(handler=command_pair)

    run = commands.add_parser("run", help="Run the bridge in the foreground.")
    run.add_argument(
        "--mock",
        action="store_true",
        help="Simulate CATIA in memory. For testing without a CATIA licence.",
    )
    run.add_argument(
        "--wait-for-catia",
        action="store_true",
        help=(
            "Keep waiting instead of exiting when CATIA is not running yet. "
            "This is how the Kryova desktop app starts the bridge, because the "
            "app is usually up before CATIA is."
        ),
    )
    run.set_defaults(handler=command_run)

    status = commands.add_parser("status", help="Report pairing and CATIA availability.")
    status.add_argument("--mock", action="store_true", help="Check the mock backend instead.")
    status.set_defaults(handler=command_status)

    # `-v` is declared on the parent parser above, which means `run -v` is a
    # parse error while `-v run` works. The setup guide tells people to "add -v
    # to any command", so accept it in the place they will actually type it.
    for command in (pair, run, status):
        command.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
