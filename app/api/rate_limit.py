"""In-memory sliding-window rate limiter for auth endpoints.

Sufficient for a single-process deployment. For multi-worker production use,
swap the backing store to Redis -- the interface stays the same.
"""

import threading
import time


class RateLimiter:
    def __init__(
        self, max_requests: int, window_seconds: int, sweep_threshold: int = 1024
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._sweep_threshold = sweep_threshold
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _evict_expired(self, cutoff: float) -> None:
        """Drop keys whose entire window has aged out.

        Without this the dict is append-only: a key is pruned only when that
        same key is seen again, so one request each from many addresses grows
        memory forever. Sweeping every key is O(n) but only runs once the map is
        large enough to be worth it.
        """
        stale = [key for key, hits in self._requests.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._requests[key]

    def check(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if len(self._requests) >= self._sweep_threshold:
                self._evict_expired(cutoff)
            timestamps = self._requests.get(key, [])
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= self._max:
                return False
            timestamps.append(now)
            self._requests[key] = timestamps
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._requests.clear()
            else:
                self._requests.pop(key, None)


auth_limiter = RateLimiter(max_requests=10, window_seconds=60)
