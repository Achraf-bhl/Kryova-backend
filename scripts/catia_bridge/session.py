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

from .backend import (
    OUT_OF_BAND_TOOLS,
    TOOL_METHODS,
    CatiaBackend,
    CatiaOperationError,
    implemented_tools,
    unsupported,
)
from .tool_table import LONG_RUNNING, ToolRefused, check_call, tier_of

logger = logging.getLogger("kryova.catia.session")

#: Longest any single operation may run before the watchdog gives up on it.
#: Deliberately below the server's own per-call timeout so the daemon is the one
#: that explains the failure -- "CATIA is showing a dialog" is a better answer
#: than the server's "the bridge did not reply".
DEFAULT_OP_TIMEOUT_S = 25.0
EXPORT_OP_TIMEOUT_S = 170.0

#: Tools the watchdog gives the longer budget to. Generated from each
#: operation's own `long_running` flag rather than restated as a literal here,
#: so a newly-added slow tool cannot time out on this side for want of someone
#: remembering to add its name.
_LONG_RUNNING = LONG_RUNNING


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
            # Which language this CATIA's menus are in. The server needs it to
            # send a command under the name this seat answers to; empty means
            # "could not tell", which the server handles by falling back to
            # reading the live menu rather than by assuming English.
            "ui_language": self.backend.ui_language,
            # Exactly which tools this backend can execute, derived structurally
            # from the methods it defines. The server offers the agent this list
            # rather than the whole registry, so a model is never handed a tool
            # that would fail on the workstation it is actually connected to --
            # and an older daemon connecting to a newer server degrades to
            # "fewer tools" instead of "some tools mysteriously error".
            "tools": list(implemented_tools(self.backend)),
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

        # The registry is wider than any one backend. The server normally filters
        # the offered tools down to what this bridge reported in `hello`, so a
        # call landing here for a method the backend lacks means the two have
        # drifted -- an older daemon, or a call queued across a reconnect. Say so
        # plainly instead of raising AttributeError from `getattr` below, which
        # would surface as an unhandled internal error rather than as the
        # actionable "this bridge cannot do that".
        if not callable(getattr(self.backend, method, None)):
            raise unsupported(tool, self.backend)

        # One call at a time here too. The server already serialises per device,
        # but a daemon that assumed so would corrupt CATIA the first time that
        # assumption stopped holding -- a second server process, a retry, a bug.
        if not self._lock.acquire(timeout=_timeout_for(tool)):
            raise CatiaOperationError(
                "The bridge is still executing an earlier command. CATIA runs one "
                "operation at a time."
            )
        try:
            self._ensure_alive(tool)
            self._ensure_document(tool, frame.get("document"))
            return getattr(self.backend, method)(**arguments)
        finally:
            self._lock.release()

    def _ensure_document(self, tool: str, document: Any) -> None:
        """Point the backend at the document the server says this call is for.

        The `document` envelope field is advisory in shape and mandatory in
        effect: when the server sends one, the operation runs against that
        document or it does not run. The server omits it for the tools that
        define the binding (`catia_new_part`, `catia_open_document`) and for the
        ones that are not about a document at all, so "no field" means "the
        server did not scope this call", never "act on whatever is in front of
        you by choice".

        Deliberately inside the lock and after `_ensure_alive`: switching
        documents is a COM operation like any other, and it must not race a call
        still finishing, nor run against a handle that has gone stale.

        The field is read defensively rather than trusted, for the same reason
        the tier is taken from the daemon's own table and never from the frame.
        A malformed one is ignored -- the operation then behaves exactly as it
        did before this existed, which is the honest failure mode for a field
        that only ever narrows what a call may touch.
        """
        if tool in OUT_OF_BAND_TOOLS or not isinstance(document, dict):
            return
        doc_name = document.get("doc_name")
        if not isinstance(doc_name, str) or not doc_name:
            return
        remote_path = document.get("remote_path")
        switched = self.backend.ensure_document(
            doc_name=doc_name,
            remote_path=remote_path if isinstance(remote_path, str) else None,
        )
        if switched:
            # Worth a log line: it means CATIA was showing something other than
            # what the conversation is about, which is the moment an engineer
            # would otherwise watch their screen change with no explanation.
            logger.info("Reattached to %s before %s", doc_name, tool)

    def _ensure_alive(self, tool: str) -> None:
        """Two checks, on two threads, because they answer different questions.

        Unless the tool is one that reads or drives the interface. Those never
        touch COM -- they post window messages, which a modal dialog's own
        message loop delivers -- and a modal dialog is exactly when the probe
        below reports CATIA as dead. Gating them on it would mean the tools
        whose entire purpose is dismissing a stuck dialog could only run when no
        dialog was stuck. See `backend.OUT_OF_BAND_TOOLS`.

        `_check_alive` runs on a watchdog thread and asks "is CATIA responding
        at all, or is it wedged behind a modal dialog?" -- a question worth
        asking off-thread precisely because the answer can be "it never
        returns".

        `ensure_connected` runs *here*, on the thread that is about to do the
        work, and asks "is my own handle still good?". The first cannot answer
        the second: a COM proxy belongs to the apartment of the thread that
        acquired it, so the watchdog holds a different handle from the worker
        and reports a healthy CATIA while the worker's pointer is stale.
        """
        if tool in OUT_OF_BAND_TOOLS:
            return
        self._check_alive(tool)
        try:
            self.backend.ensure_connected()
        except Exception as exc:  # noqa: BLE001 - reported like any other failure
            raise CatiaOperationError(str(exc)) from exc

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
