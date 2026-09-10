"""Sliding-window rate limiting, and what a limit is counted against.

Uses Redis when REDIS_URL is configured so limits are shared across all
workers. Falls back to the in-memory implementation for development and
single-process deployments where Redis is not running.

**The key matters as much as the window** (P1.6). A limit counted against the
connecting address rations the wrong thing for an authenticated route: one
office behind one NAT is a single IP and hundreds of engineers, so a per-IP
budget either throttles a customer or is set so high it stops nothing. Where a
principal exists, `limit_key` counts against *them* — the identity that actually
owns the work — and falls back to the address only for routes where nobody has
authenticated yet, which is where an IP is genuinely the best available
denominator.

`client_ip` lives here rather than in `routes/auth.py` because three modules had
grown their own copy of the `X-Forwarded-For` rule, and a security decision with
three implementations has three chances to be the wrong one.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.security import decode_access_token

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
        except redis.RedisError as exc:
            # Every redis-level failure -- unreachable, wrong password, wrong
            # database -- is normalised to one exception type so the caller can
            # name what it catches instead of catching everything.
            raise ConnectionError(f"Redis is not usable at {url}: {exc}") from exc

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
    def __init__(self, max_requests: int, window_seconds: int, sweep_threshold: int = 1024) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._sweep_threshold = sweep_threshold
        self._backend: RateLimiterBackend | None = None

    @property
    def max_requests(self) -> int:
        """The budget, so a refusal can state it rather than guess."""
        return self._max

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
            except (ConnectionError, OSError, ImportError, ValueError) as exc:
                # Redis being down must not take auth down with it, so the
                # in-memory limiter takes over -- but say so. A bare `except
                # Exception` here also swallowed typos and bad URLs, which then
                # looked like a working Redis deployment that silently was not.
                logger.warning(
                    "Falling back to the in-process rate limiter: %s: %s",
                    type(exc).__name__,
                    exc,
                )
        self._backend = InMemoryBackend(sweep_threshold=self._sweep_threshold)
        return self._backend


auth_limiter = RateLimiter(max_requests=10, window_seconds=60)


# ---------------------------------------------------------------------------
# What a limit is counted against (P1.6)
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    """The address to count an unauthenticated request against.

    `X-Forwarded-For` is written by whoever sends the request, so trusting it
    unconditionally means a client that rotates the header has no rate limit at
    all. It is read only when `trust_proxy_headers` says a reverse proxy is in
    front, and then from the right: each trusted proxy appends the address it
    saw, so with N of them the real client is N entries from the end. Everything
    to the left of that was supplied by the caller.

    Moved here from `routes/auth.py` on 2026-09-10 — `routes/catia.py` and
    `core/audit.py` had each grown a variant, and one of them counting from the
    left is the kind of difference nobody notices until a limit does nothing.
    """
    peer = request.client.host if request.client else "unknown"
    if not settings.trust_proxy_headers:
        return peer

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    index = len(hops) - settings.trusted_proxy_count
    if not hops or index < 0:
        # Fewer hops than the deployment claims: the chain is not what was
        # configured, so believe the socket rather than guess.
        return peer
    return hops[index]


def limit_key(request: Request, *, scope: str) -> str:
    """`scope:user:<id>` where somebody is signed in, `scope:ip:<addr>` otherwise.

    The token is decoded rather than the request's principal read, deliberately:
    this runs as a dependency and must not require `get_current_user` to have
    resolved first. A JWT decode is a signature check with no database in it, so
    the cost is microseconds, and a token that fails to decode simply falls
    through to the address — an attacker cannot get a *larger* budget by
    presenting a broken token, only the one they already had.

    The two namespaces cannot collide: a user id is a UUID and an address is
    not, and they are prefixed regardless so that stays true if either changes.
    """
    token = request.cookies.get("kryova_access")
    if token is None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if token:
        user_id = decode_access_token(token)
        if user_id is not None:
            return f"{scope}:user:{user_id}"
    return f"{scope}:ip:{client_ip(request)}"


class RateLimit:
    """A FastAPI dependency that refuses over-budget requests.

    Attach with `dependencies=[Depends(RateLimit("ai.chat", 30, 60))]`. The
    scope name is part of the key, so two limits on one principal are counted
    separately and a noisy endpoint cannot spend another one's budget.

    Refuses with `Retry-After`, which is not decoration: a client that knows
    when to come back stops hammering, and one that does not retries in a tight
    loop and turns a limit into the load it was meant to prevent.
    """

    def __init__(self, scope: str, max_requests: int, window_seconds: int) -> None:
        self.scope = scope
        self.window_seconds = window_seconds
        self._limiter = RateLimiter(max_requests=max_requests, window_seconds=window_seconds)

    def __call__(self, request: Request) -> None:
        if not self._limiter.check(limit_key(request, scope=self.scope)):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many requests. This limit is {self._limiter.max_requests} per "
                    f"{self.window_seconds} seconds."
                ),
                headers={"Retry-After": str(self.window_seconds)},
            )

    def reset(self) -> None:
        """Clear this limit's state. For tests and for the admin panel."""
        self._limiter.reset()
