"""Per-user fan-out from daemon events to browser SSE subscribers.

The browser never talks to the daemon -- there is no port on the engineer's
machine to talk to. So when CATIA reports that the user changed a parameter by
hand, the path is daemon -> WebSocket -> this bus -> SSE -> browser, and this is
the piece in the middle.

Scoping is per user, not global. Publishing goes through the connection's owner
id, and a subscriber only ever sees its own user's stream, so one account's
document names can never appear in another's event feed.

Subscribers are bounded queues that drop their oldest entry when full. A browser
tab that stops reading (backgrounded, throttled, or simply gone) must not grow
until the process dies, and for a live activity feed the newest events are the
ones worth keeping.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from typing import Any

#: Events buffered per subscriber before the oldest are dropped.
SUBSCRIBER_BUFFER = 128

#: Event names the daemon may raise, per the protocol. Anything else is dropped
#: at the bus rather than relayed: the browser switches on this value, and an
#: unrecognised name is either a version skew or a daemon doing something it was
#: not asked to.
KNOWN_EVENTS = frozenset(
    {
        "document_opened",
        "document_saved",
        "geometry_changed",
        "parameters_changed",
        "checkpoint_created",
        "export_completed",
        "catia_lost",
    }
)


class Subscription:
    """One browser's event stream. Iterate it; close it when the request ends."""

    def __init__(self, bus: "EventBus", user_id: str) -> None:
        self._bus = bus
        self.user_id = user_id
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=SUBSCRIBER_BUFFER)
        self._closed = threading.Event()

    def offer(self, event: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Drop the oldest, keep the newest. A stalled reader loses history,
            # never liveness.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except (queue.Empty, queue.Full):  # pragma: no cover - racing readers
                pass

    def poll(self, timeout: float) -> dict[str, Any] | None:
        """Next event, or None if `timeout` elapsed. None is the keepalive cue."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def listen(self, keepalive_s: float = 15.0) -> Iterator[dict[str, Any] | None]:
        """Yield events, and None whenever `keepalive_s` passes without one.

        The Nones are what stop a proxy from reaping an idle SSE stream: the
        route turns each into an SSE comment. A stream that emits nothing for a
        minute is indistinguishable from a dead one to everything in between.
        """
        try:
            while not self._closed.is_set():
                yield self.poll(keepalive_s)
        finally:
            self.close()

    def close(self) -> None:
        self._closed.set()
        self._bus.unsubscribe(self)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> Subscription:
        subscription = Subscription(self, user_id)
        with self._lock:
            self._subscribers.setdefault(user_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            listeners = self._subscribers.get(subscription.user_id)
            if listeners is None:
                return
            listeners.discard(subscription)
            if not listeners:
                del self._subscribers[subscription.user_id]

    def publish(self, user_id: str, event: dict[str, Any]) -> int:
        """Fan one event out to a user's subscribers. Returns how many got it.

        Called from the event loop (the WebSocket reader); `offer` never blocks,
        so a slow browser cannot stall the socket that feeds it.
        """
        with self._lock:
            listeners = list(self._subscribers.get(user_id, ()))
        for subscription in listeners:
            subscription.offer(event)
        return len(listeners)

    def subscriber_count(self, user_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(user_id, ()))


bus = EventBus()
