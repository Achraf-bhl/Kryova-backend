"""The outbound WebSocket: connect, stay connected, carry frames.

This is the transport and nothing else -- the decisions live in `session.py`.
Three properties matter here.

**It only ever dials out.** No socket is ever listened on. That is the whole
security argument for this design: there is no port on the engineer's machine
for a malicious web page to find, so the DNS-rebinding attack that a localhost
HTTP bridge invites does not exist, and it works behind a corporate NAT with
nothing to configure.

**It reconnects, with backoff and jitter.** Laptops sleep, VPNs drop, servers
restart. A daemon that gave up on the first disconnect would need a human to
restart it, and one that retried in a tight loop would hammer the server every
time it was down -- so the delay doubles to a ceiling, and jitter keeps a fleet
of workstations from all reconnecting on the same second after an outage.

**Blocking work runs off the event loop.** A CATIA operation blocks for seconds
and a STEP export for minutes. Running one inline would stall the same task that
has to answer the server's pings, so the connection would be declared dead
halfway through the export it was busy completing. Calls therefore go to a
worker thread, and the socket keeps breathing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import socket
from typing import Any

from .backend import CatiaBackend
from .config import BRIDGE_VERSION, BridgeConfig
from .session import BridgeSession

logger = logging.getLogger("kryova.catia.bridge")

INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0
#: Sockets that fail immediately, over and over, must not look like success.
MIN_SESSION_FOR_RESET_S = 30.0


class BridgeClient:
    def __init__(self, config: BridgeConfig, backend: CatiaBackend) -> None:
        self.config = config
        self.backend = backend
        self._outbox: asyncio.Queue[str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- the reconnect loop --------------------------------------------------

    async def run_forever(self) -> None:
        backoff = INITIAL_BACKOFF_S
        while True:
            started = asyncio.get_running_loop().time()
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is a retry
                logger.warning("Bridge connection failed: %s", exc)
            else:
                logger.info("Bridge connection closed by the server")

            lasted = asyncio.get_running_loop().time() - started
            if lasted >= MIN_SESSION_FOR_RESET_S:
                # A session that actually worked resets the backoff, so one bad
                # night does not leave the daemon reconnecting once a minute
                # forever afterwards.
                backoff = INITIAL_BACKOFF_S

            delay = min(backoff, MAX_BACKOFF_S) * (0.5 + random.random())
            logger.info("Reconnecting in %.1fs", delay)
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, MAX_BACKOFF_S)

    async def _run_once(self) -> None:
        import websockets

        url = self.config.websocket_url
        logger.info("Connecting to %s", url)
        async with websockets.connect(
            url,
            # The token rides in a header, never the URL: a query parameter is
            # written to every access log between here and the server.
            additional_headers={"Authorization": f"Bearer {self.config.device_token}"},
            # The server drives the heartbeat (20 s pings, two misses and it
            # hangs up); a second, independent keepalive on this side only adds
            # a way for the two to disagree about who is dead.
            ping_interval=None,
            max_size=None,
            open_timeout=20,
            close_timeout=5,
        ) as websocket:
            self._loop = asyncio.get_running_loop()
            self._outbox = asyncio.Queue()
            session = BridgeSession(
                self.backend,
                bridge_version=BRIDGE_VERSION,
                hostname=socket.gethostname(),
                send=self._enqueue,
            )

            await websocket.send(json.dumps(session.hello_frame()))
            logger.info("Connected as %s", self.config.device_name or self.config.device_id)

            sender = asyncio.create_task(self._drain(websocket), name="bridge-sender")
            try:
                async for raw in websocket:
                    text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                    # A tool call blocks for seconds; a STEP export for minutes.
                    # Off the loop it goes, or the ping this task owes the
                    # server never gets answered.
                    await asyncio.to_thread(session.handle_frame, text)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender

    # -- sending -------------------------------------------------------------

    def _enqueue(self, frame: dict[str, Any]) -> None:
        """Queue a frame from whichever thread produced it."""
        loop, outbox = self._loop, self._outbox
        if loop is None or outbox is None:  # pragma: no cover - closed mid-call
            logger.warning("Dropping a %s frame: the connection is gone", frame.get("type"))
            return
        payload = json.dumps(frame, default=str)
        loop.call_soon_threadsafe(outbox.put_nowait, payload)

    async def _drain(self, websocket: Any) -> None:
        assert self._outbox is not None  # noqa: S101 - set by _run_once
        while True:
            await websocket.send(await self._outbox.get())

    # -- unsolicited events --------------------------------------------------

    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Push an event up to the browser (via the server's SSE relay)."""
        self._enqueue({"type": "event", "event": event, "data": data or {}})


def run(config: BridgeConfig, backend: CatiaBackend) -> None:
    try:
        asyncio.run(BridgeClient(config, backend).run_forever())
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        backend.close()
