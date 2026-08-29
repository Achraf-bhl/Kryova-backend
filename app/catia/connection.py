"""Live bridge WebSockets, and the queue that keeps CATIA from being asked two
things at once.

This module straddles a thread boundary on purpose. The WebSocket lives on the
event loop; every caller of `call_catia` is a synchronous route or agent tool
running in FastAPI's worker threadpool. Rather than making the whole call path
async -- which would mean an async database session and a second driver this
codebase does not have -- the crossing is made explicit and kept in one file:

* outbound frames are handed to the loop with `call_soon_threadsafe`;
* the caller then blocks on a `threading.Event` that the loop's reader sets when
  the matching `result` frame arrives.

**One call at a time per device.** CATIA's automation surface is effectively
single-threaded (COM STA, and the modeller core does not parallelise); two
concurrent calls into one session corrupt it or deadlock it. `_Turnstile` admits
exactly one call per device and queues the rest in strict arrival order. Strict
order matters: a sketch followed by a pad that extrudes it are not commutative,
and `threading.Lock` makes no fairness promise at all.

**Every wait is bounded.** A modal dialog in CATIA -- "Do you want to save?" --
blocks its automation surface indefinitely. If nothing timed out, the first such
dialog would wedge the device's queue until the engineer noticed, which might be
the next morning. So a call has a deadline, the queue wait has a deadline, and
the heartbeat has a deadline; all three fail loudly rather than hang.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect

from app.models.base import utcnow

logger = logging.getLogger(__name__)

#: Server pings this often; the daemon must answer each one.
PING_INTERVAL_S = 20.0
#: Two unanswered pings and the device is considered gone -- the protocol's
#: stated rule. One missed pong is a slow network; two is a dead peer.
MAX_MISSED_PONGS = 2
#: How long to wait for the daemon's `hello` before hanging up. A socket that
#: connects and says nothing is either a probe or a broken client, and either
#: way it must not hold a slot.
HELLO_TIMEOUT_S = 15.0
#: A frame larger than this is refused unread. Screenshots and STEP bodies come
#: back base64-encoded inside a result frame, so the ceiling has to clear those
#: (see `CatiaLimits` in dispatch) while still bounding memory per connection.
MAX_FRAME_BYTES = 96 * 1024 * 1024


class BridgeError(RuntimeError):
    """Base for failures of the transport itself, as opposed to of a tool."""


class BridgeGone(BridgeError):
    """The device disconnected, or was never connected."""


class BridgeBusy(BridgeError):
    """The device's queue did not clear within the admission deadline."""


class BridgeTimeout(BridgeError):
    """The call was sent and the device never answered."""


class BridgeCallFailed(BridgeError):
    """The device ran the tool and it failed. Carries the daemon's message."""


class _Turnstile:
    """Admits one holder at a time, in strict arrival order.

    Handing the slot directly to the head of the queue (rather than releasing a
    lock and letting whoever wakes first take it) is what makes the ordering
    strict. Without it, a burst of calls into one CATIA session executes in
    whatever order the OS scheduler prefers, and "sketch, then pad that sketch"
    stops being a reliable sequence.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = False
        self._waiters: deque[threading.Event] = deque()

    def acquire(self, timeout: float) -> bool:
        with self._lock:
            if not self._held:
                self._held = True
                return True
            ticket = threading.Event()
            self._waiters.append(ticket)

        if ticket.wait(timeout):
            return True

        with self._lock:
            try:
                self._waiters.remove(ticket)
            except ValueError:
                # The holder released to us in the instant between our wait
                # expiring and taking the lock. We own the slot we no longer
                # want, so pass it on rather than stranding the queue behind a
                # holder that has already given up.
                self._release_locked()
        return False

    def release(self) -> None:
        with self._lock:
            self._release_locked()

    def _release_locked(self) -> None:
        if self._waiters:
            self._waiters.popleft().set()
        else:
            self._held = False

    def abandon(self) -> None:
        """Wake everybody. Used when the connection dies: a waiter must find out
        the device is gone rather than sit out its full admission timeout."""
        with self._lock:
            while self._waiters:
                self._waiters.popleft().set()
            self._held = False

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._waiters) + (1 if self._held else 0)


@dataclass
class _PendingCall:
    call_id: str
    tool: str
    done: threading.Event = field(default_factory=threading.Event)
    ok: bool = False
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class BridgeHello:
    """The daemon's opening frame, already validated."""

    catia_version: str
    bridge_version: str
    hostname: str
    mock: bool
    capabilities: tuple[str, ...]

    @classmethod
    def parse(cls, frame: dict[str, Any]) -> "BridgeHello":
        if frame.get("type") != "hello":
            raise BridgeError(f"expected a 'hello' frame, got {frame.get('type')!r}")
        capabilities = frame.get("capabilities") or []
        if not isinstance(capabilities, list):
            raise BridgeError("hello.capabilities must be a list of strings")
        return cls(
            # Everything here is peer-supplied and lands in the UI and in
            # prompts, so it is truncated at the door. Full sanitising happens
            # in `app.catia.sanitize` on the way out.
            catia_version=str(frame.get("catia_version") or "unknown")[:64],
            bridge_version=str(frame.get("bridge_version") or "unknown")[:32],
            hostname=str(frame.get("hostname") or "")[:255],
            mock=bool(frame.get("mock")),
            capabilities=tuple(str(c)[:32] for c in capabilities[:32]),
        )


class DeviceConnection:
    """One live daemon socket, callable from any thread."""

    def __init__(
        self,
        *,
        device_id: str,
        user_id: str,
        hello: BridgeHello,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.device_id = device_id
        self.user_id = user_id
        self.hello = hello
        self.connected_at: datetime = utcnow()

        self._loop = loop
        self._outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._pending: dict[str, _PendingCall] = {}
        self._lock = threading.Lock()
        self._turnstile = _Turnstile()
        self._closed = threading.Event()
        self._close_reason = "disconnected"
        self._missed_pongs = 0
        self._last_activity = time.monotonic()

    # -- state ---------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return not self._closed.is_set()

    @property
    def queue_depth(self) -> int:
        return self._turnstile.depth

    def close(self, reason: str = "disconnected") -> None:
        """Mark the connection dead and fail everything waiting on it.

        Idempotent: the reader, the sender, the heartbeat and the route's
        `finally` all call it, and whichever gets there first names the reason.
        """
        if self._closed.is_set():
            return
        self._close_reason = reason
        self._closed.set()

        with self._lock:
            pending, self._pending = list(self._pending.values()), {}
        for call in pending:
            call.ok = False
            call.error = reason
            call.done.set()
        self._turnstile.abandon()

        # Unblock the sender task so it can finish rather than await forever.
        try:
            self._loop.call_soon_threadsafe(self._outbox.put_nowait, None)
        except RuntimeError:  # pragma: no cover - loop already torn down
            pass

    # -- calling out ---------------------------------------------------------

    def call(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        conversation_id: str | None,
        timeout_s: float,
        queue_timeout_s: float,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        """Run one tool on the device and return its data, blocking the caller.

        `approval_token` is forwarded on the frame for destructive tools. The
        daemon cannot verify the signature -- it holds no server secret -- but
        it refuses a destructive call that arrives without one, and only the
        server can put one on the wire. That is what stops a compromised agent
        stream from inventing a destructive call the server never signed.

        `queue_timeout_s` bounds the wait for the device to be free and
        `timeout_s` bounds the call itself. They are separate numbers because
        they are separate problems, and a caller deserves to be told which one
        it hit: "CATIA is busy" is a retry, "CATIA did not answer" is a wedged
        session someone has to go and look at.
        """
        if self._closed.is_set():
            raise BridgeGone("The CATIA bridge disconnected.")

        if not self._turnstile.acquire(queue_timeout_s):
            raise BridgeBusy(
                f"CATIA is still working on an earlier operation and did not free up "
                f"within {queue_timeout_s:g}s. CATIA runs one command at a time; wait "
                f"for the current one to finish and try again."
            )
        try:
            if self._closed.is_set():
                raise BridgeGone(f"The CATIA bridge {self._close_reason}.")

            pending = _PendingCall(call_id=str(uuid.uuid4()), tool=tool)
            with self._lock:
                self._pending[pending.call_id] = pending

            frame: dict[str, Any] = {
                "type": "call",
                "id": pending.call_id,
                "tool": tool,
                "conversation_id": conversation_id,
                "arguments": arguments,
            }
            if approval_token:
                frame["approval_token"] = approval_token
            self._send(frame)

            if not pending.done.wait(timeout_s):
                with self._lock:
                    self._pending.pop(pending.call_id, None)
                raise BridgeTimeout(
                    f"CATIA did not answer {tool} within {timeout_s:g}s. It is most "
                    f"often a modal dialog waiting for a click on the workstation; "
                    f"check CATIA, dismiss it, and try again."
                )
            if not pending.ok:
                raise BridgeCallFailed(pending.error or f"{tool} failed in CATIA.")
            return pending.data
        finally:
            self._turnstile.release()

    def _send(self, frame: dict[str, Any]) -> None:
        payload = json.dumps(frame, default=str)
        try:
            self._loop.call_soon_threadsafe(self._outbox.put_nowait, payload)
        except RuntimeError as exc:  # the loop shut down under us
            self.close("was interrupted by a server shutdown")
            raise BridgeGone("The CATIA bridge connection was closed.") from exc

    # -- receiving -----------------------------------------------------------

    def handle_frame(self, raw: str) -> dict[str, Any] | None:
        """Process one inbound frame. Returns an `event` frame for the bus, or None.

        Runs on the event loop. Nothing here blocks, and a malformed frame is
        logged and dropped rather than killing the connection -- a daemon that
        emits one bad frame is still worth talking to.
        """
        self._last_activity = time.monotonic()
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("CATIA device %s sent a non-JSON frame", self.device_id)
            return None
        if not isinstance(frame, dict):
            logger.warning("CATIA device %s sent a non-object frame", self.device_id)
            return None

        kind = frame.get("type")
        if kind == "result":
            self._resolve(frame)
            return None
        if kind == "pong":
            self._missed_pongs = 0
            return None
        if kind == "event":
            return frame
        if kind == "ping":
            # The daemon may ping too; answering keeps a NAT mapping alive from
            # either end.
            self._send({"type": "pong", "t": frame.get("t", time.time())})
            return None
        logger.warning("CATIA device %s sent unknown frame type %r", self.device_id, kind)
        return None

    def _resolve(self, frame: dict[str, Any]) -> None:
        call_id = frame.get("id")
        with self._lock:
            pending = self._pending.pop(call_id, None) if isinstance(call_id, str) else None
        if pending is None:
            # A result for a call that already timed out. Expected, not an
            # error: the daemon finished after we stopped waiting.
            logger.info("CATIA device %s answered unknown call %r", self.device_id, call_id)
            return
        pending.ok = bool(frame.get("ok"))
        data = frame.get("data")
        pending.data = data if isinstance(data, dict) else {}
        error = frame.get("error")
        pending.error = str(error)[:2000] if error is not None else None
        pending.done.set()

    # -- the transport loops -------------------------------------------------

    async def run_sender(self, websocket: WebSocket) -> None:
        """Drain the outbox onto the socket. One writer, so frames never interleave."""
        while True:
            payload = await self._outbox.get()
            if payload is None:  # close() sentinel
                return
            try:
                await websocket.send_text(payload)
            except (WebSocketDisconnect, RuntimeError):
                self.close("disconnected")
                return

    async def run_heartbeat(self) -> None:
        """Ping every 20 s; give up after two unanswered pings."""
        while not self._closed.is_set():
            await asyncio.sleep(PING_INTERVAL_S)
            if self._closed.is_set():
                return
            if self._missed_pongs >= MAX_MISSED_PONGS:
                logger.warning(
                    "CATIA device %s missed %d heartbeats; marking offline",
                    self.device_id,
                    self._missed_pongs,
                )
                self.close("stopped responding to heartbeats")
                return
            self._missed_pongs += 1
            self._send({"type": "ping", "t": time.time()})


class CatiaRegistry:
    """Which devices are online, in this process.

    Deliberately in-process: the daemon holds a socket to one worker, so only
    that worker can talk to it. Running several workers means a call must land
    on the one holding the socket, which needs a shared bus (Redis pub/sub) --
    that is the next step, and until it is taken, run the bridge behind a single
    worker. Nothing here silently pretends otherwise: `find_online` simply
    reports no device, and the caller says the bridge is not connected.
    """

    def __init__(self) -> None:
        self._by_device: dict[str, DeviceConnection] = {}
        self._lock = threading.Lock()

    def register(self, connection: DeviceConnection) -> DeviceConnection | None:
        """Add a connection, returning any previous one for the same device.

        A laptop that sleeps and wakes reconnects before the old socket's
        heartbeat has noticed it is dead, so the same device legitimately
        appears twice. The newest socket wins; the caller closes the old one.
        """
        with self._lock:
            previous = self._by_device.get(connection.device_id)
            self._by_device[connection.device_id] = connection
        return previous

    def unregister(self, connection: DeviceConnection) -> None:
        with self._lock:
            # Only if it is still *this* connection: a reconnect may already
            # have replaced it, and the loser's cleanup must not evict the
            # winner.
            if self._by_device.get(connection.device_id) is connection:
                del self._by_device[connection.device_id]

    def get(self, device_id: str) -> DeviceConnection | None:
        with self._lock:
            connection = self._by_device.get(device_id)
        return connection if connection is not None and connection.is_open else None

    def find_for_user(self, user_id: str) -> list[DeviceConnection]:
        with self._lock:
            connections = list(self._by_device.values())
        return [c for c in connections if c.user_id == user_id and c.is_open]

    def online_device_ids(self) -> set[str]:
        with self._lock:
            return {d for d, c in self._by_device.items() if c.is_open}

    def close_all(self, reason: str = "was disconnected by a server shutdown") -> None:
        with self._lock:
            connections = list(self._by_device.values())
            self._by_device.clear()
        for connection in connections:
            connection.close(reason)


registry = CatiaRegistry()
