"""Sliding-window rate limiter for auth endpoints.

Uses Redis when REDIS_URL is configured so limits are shared across all
workers. Falls back to the in-memory implementation for development and
single-process deployments where Redis is not running.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod

from app.core.config import settings

logger = logging.getLogger(__name__)


class RateLimiterBackend(ABC):
    @abstractmethod
    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Return True if allowed, False if rate-limited."""

    @abstractmethod
    def reset(self, key: str | None) -> None:
        """Clear rate-limit state for a key, or all keys."""


class InMemoryBackend(RateLimiterBackend):
    def __init__(self, sweep_threshold: int = 1024) -> None:
        self._sweep_threshold = sweep_threshold
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _evict_expired(self, cutoff: float) -> None:
        stale = [k for k, hits in self._requests.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._requests[key]

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if len(self._requests) >= self._sweep_threshold:
                self._evict_expired(cutoff)
            timestamps = self._requests.get(key, [])
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= max_requests:
                return False
            timestamps.append(now)
            self._requests[key] = timestamps
            return True

    def reset(self, key: str | None) -> None:
        with self._lock:
            if key is None:
                self._requests.clear()
            else:
                self._requests.pop(key, None)


class RedisBackend(RateLimiterBackend):
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        try:
            self._client.ping()
        except redis.ConnectionError as exc:
            logger.warning("Redis unreachable at %s; falling back to in-memory", url)
            raise ConnectionError from exc

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        full_key = f"ratelimit:{key}"
        pipe = self._client.pipeline()
        pipe.incr(full_key)
        pipe.expire(full_key, window_seconds)
        results = pipe.execute()
        count = int(results[0])
        return count <= max_requests

    def reset(self, key: str | None) -> None:
        if key is None:
            keys = self._client.keys("ratelimit:*")
            if keys:
                self._client.delete(*keys)
        else:
            self._client.delete(f"ratelimit:{key}")


class RateLimiter:
    def __init__(
        self, max_requests: int, window_seconds: int, sweep_threshold: int = 1024
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._sweep_threshold = sweep_threshold
        self._backend: RateLimiterBackend | None = None

    def check(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        backend = self._get_backend()
        return backend.check(key, self._max, self._window)

    def reset(self, key: str | None = None) -> None:
        self._get_backend().reset(key)

    def _get_backend(self) -> RateLimiterBackend:
        if self._backend is not None:
            return self._backend
        redis_url = getattr(settings, "redis_url", None)
        if settings.environment == "production" and not redis_url:
            logger.warning(
                "REDIS_URL is not set in production; auth rate limits will be "
                "per-process and inconsistent across workers."
            )
        if redis_url:
            try:
                self._backend = RedisBackend(redis_url)
                return self._backend
            except (ConnectionError, Exception):
                pass
        self._backend = InMemoryBackend(sweep_threshold=self._sweep_threshold)
        return self._backend


auth_limiter = RateLimiter(max_requests=10, window_seconds=60)
