"""Frame handling: what the daemon does with a `call`, independent of transport.

Kept separate from `bridge.py` on purpose. The WebSocket, the reconnect loop and
the exponential backoff are one problem; deciding whether a call is allowed and
running it is another, and only the second is worth testing exhaustively. With
this split the test suite drives the *real* daemon logic -- the same schema
re-validation, the same tier table, the same watchdog, the same mock CATIA -- by
handing it frames directly, and `bridge.py` is the thin part that carries them.

The order of checks in `handle_call` is the security-relevant part:

1. look the tool up in the daemon's **own** table (never trust the frame);
2. enforce the tier from that table, including the approval-token requirement;
3. validate the arguments against the daemon's own schema;
4. only then execute, under a watchdog.

An agent stream that has been talked into asking for something destructive
cannot get past step 2, and one that has been talked into smuggling an extra
argument cannot get past step 3.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from .backend import TOOL_METHODS, CatiaBackend, CatiaOperationError
from .tool_table import ToolRefused, check_call, tier_of

logger = logging.getLogger("kryova.catia.session")

#: Longest any single operation may run before the watchdog gives up on it.
#: Deliberately below the server's own per-call timeout so the daemon is the one
#: that explains the failure -- "CATIA is showing a dialog" is a better answer
#: than the server's "the bridge did not reply".
DEFAULT_OP_TIMEOUT_S = 25.0
EXPORT_OP_TIMEOUT_S = 170.0

_LONG_RUNNING = frozenset({"catia_export_step"})


class BridgeSession:
    """Turns inbound frames into outbound frames. No IO of its own."""

    def __init__(
        self,
        backend: CatiaBackend,
        *,
        bridge_version: str,
        hostname: str,
        send: Callable[[dict[str, Any]], None],
    ) -> None:
        self.backend = backend
        self.bridge_version = bridge_version
        self.hostname = hostname
        self._send = send
        self._lock = threading.Lock()

    # -- frames --------------------------------------------------------------

    def hello_frame(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "catia_version": self.backend.catia_version,
            "bridge_version": self.bridge_version,
            "mock": self.backend.is_mock,
            "hostname": self.hostname,
            "capabilities": list(self.backend.capabilities),
        }

    def handle_frame(self, raw: str) -> None:
        """Process one inbound text frame, replying through `send`."""
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Ignoring a non-JSON frame from the server")
            return
        if not isinstance(frame, dict):
            logger.warning("Ignoring a non-object frame from the server")
            return

        kind = frame.get("type")
        if kind == "ping":
            self._send({"type": "pong", "t": frame.get("t", time.time())})
        elif kind == "call":
            self.handle_call(frame)
        elif kind in {"ready", "pong"}:
            pass
        else:
            logger.info("Ignoring unknown frame type %r", kind)

    def handle_call(self, frame: dict[str, Any]) -> None:
        call_id = frame.get("id")
        tool = frame.get("tool")
        if not isinstance(call_id, str) or not isinstance(tool, str):
            logger.warning("Ignoring a malformed call frame")
            return

        started = time.monotonic()
        try:
            data = self._run(tool, frame)
        except ToolRefused as exc:
            # Refusals are the interesting log line: they are the record of the
            # server (and the model behind it) asking for something this daemon
            # would not do.
            logger.warning("Refused %s: %s", tool, exc)
            self._send({"type": "result", "id": call_id, "ok": False, "error": str(exc)})
            return
        except CatiaOperationError as exc:
            logger.info("%s failed: %s", tool, exc)
            self._send({"type": "result", "id": call_id, "ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001 - a COM error must not kill the daemon
            logger.exception("%s raised", tool)
            self._send(
                {
                    "type": "result",
                    "id": call_id,
                    "ok": False,
                    # Type included because COM errors are frequently unhelpful
                    # on their own, and the type is often the only clue.
                    "error": f"{type(exc).__name__} while running {tool}: {exc}",
                }
            )
            return

        logger.info("%s ok in %.0f ms", tool, (time.monotonic() - started) * 1000)
        self._send({"type": "result", "id": call_id, "ok": True, "data": data})

    # -- execution -----------------------------------------------------------

    def _run(self, tool: str, frame: dict[str, Any]) -> dict[str, Any]:
        # Tier from the daemon's table, never from the frame: a call claiming
        # `"tier": "read"` gets whatever this table says the tool really is.
        tier_of(tool)
        arguments = check_call(
            tool,
            frame.get("arguments"),
            approval_token=_string_or_none(frame.get("approval_token")),
        )

        method = TOOL_METHODS.get(tool)
        if method is None:  # pragma: no cover - tool_table and this list agree
            raise ToolRefused(f"{tool!r} has no implementation in this bridge.")

        # One call at a time here too. The server already serialises per device,
        # but a daemon that assumed so would corrupt CATIA the first time that
        # assumption stopped holding -- a second server process, a retry, a bug.
        if not self._lock.acquire(timeout=_timeout_for(tool)):
            raise CatiaOperationError(
                "The bridge is still executing an earlier command. CATIA runs one "
                "operation at a time."
            )
        try:
            self._check_alive(tool)
            return getattr(self.backend, method)(**arguments)
        finally:
            self._lock.release()

    def _check_alive(self, tool: str) -> None:
        """Fail fast on a wedged CATIA instead of blocking until the timeout.

        `health()` on the real backend touches the automation surface with a
        trivial property read. When CATIA is showing a modal dialog that read
        does not return, so it is issued on a watchdog thread: the point is to
        turn "the call hangs and the server eventually gives up" into "the
        bridge says CATIA is busy with a dialog", which is actionable.
        """
        failure: list[BaseException] = []

        def probe() -> None:
            try:
                self.backend.health()
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failure.append(exc)

        watchdog = threading.Thread(target=probe, name="catia-health", daemon=True)
        watchdog.start()
        watchdog.join(_HEALTH_TIMEOUT_S)
        if watchdog.is_alive():
            raise CatiaOperationError(
                f"CATIA is not responding to automation, so {tool} was not attempted. "
                "This is almost always a modal dialog waiting for a click -- switch to "
                "CATIA, dismiss it, and try again."
            )
        if failure:
            raise CatiaOperationError(str(failure[0]))


#: How long the liveness probe may take before CATIA counts as wedged.
_HEALTH_TIMEOUT_S = 5.0


def _timeout_for(tool: str) -> float:
    return EXPORT_OP_TIMEOUT_S if tool in _LONG_RUNNING else DEFAULT_OP_TIMEOUT_S


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
