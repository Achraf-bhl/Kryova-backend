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

**A refusal is a decision with a number in it** (ROAD_TO_10 3.2). `hit` returns a
`Decision` -- allowed or not, the budget, what is left and how long until it
moves -- instead of a bare boolean, because "429" alone teaches a client nothing
and a client that is not told when to come back retries in a tight loop and turns
a limit into the load it was meant to prevent. The numbers travel as
`RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` on every response
from a limited route (`RateLimitHeadersMiddleware`), and as `Retry-After` on the
429 itself.

`client_ip` lives here rather than in `routes/auth.py` because three modules had
grown their own copy of the `X-Forwarded-For` rule, and a security decision with
three implementations has three chances to be the wrong one.
"""

import logging
import math
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import limits
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_access_token

logger = logging.getLogger(__name__)

HEADER_LIMIT = "RateLimit-Limit"
HEADER_REMAINING = "RateLimit-Remaining"
HEADER_RESET = "RateLimit-Reset"
HEADER_RETRY_AFTER = "Retry-After"

#: What a browser on another origin may read. `app/main.py` lists exactly these in the
#: CORS `expose_headers`: a header the client cannot read is a header that does not exist,
#: and the frontend's "you can send again in 12 s" is built from `Retry-After`.
EXPOSED_HEADERS: tuple[str, ...] = (
    HEADER_LIMIT,
    HEADER_REMAINING,
    HEADER_RESET,
    HEADER_RETRY_AFTER,
)


@dataclass(frozen=True, slots=True)
class Decision:
    """The answer to one request against one limit.

    `reset_seconds` is whole seconds, rounded **up**, until the budget moves: for the
    in-process sliding window, until the oldest counted request leaves the window; for
    Redis's fixed window, until the window ends. On a refusal both mean the same thing --
    the earliest moment a retry can succeed -- which is why it doubles as `Retry-After`.
    Rounding down would send a client back a fraction of a second early into a second 429.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int

    def headers(self) -> dict[str, str]:
        """The three `RateLimit-*` headers, plus `Retry-After` on a refusal."""
        pairs = {
            HEADER_LIMIT: str(self.limit),
            HEADER_REMAINING: str(self.remaining),
            HEADER_RESET: str(self.reset_seconds),
        }
        if not self.allowed:
            pairs[HEADER_RETRY_AFTER] = str(self.reset_seconds)
        return pairs


def _tighter(current: Decision | None, new: Decision) -> Decision:
    """Which of two decisions on one request the client should be told about.

    A route can sit behind two limits (a per-minute one and a per-account one). The
    client needs the one it will hit first, so a refusal beats an allowance and, between
    two allowances, the smaller `remaining` wins. Telling it about the roomier limit would
    read "9 left" on the request that spent the last of the other.
    """
    if current is None:
        return new
    if new.allowed != current.allowed:
        return current if not current.allowed else new
    if new.remaining != current.remaining:
        return new if new.remaining < current.remaining else current
    return new if new.reset_seconds > current.reset_seconds else current


def note(request: Request, decision: Decision) -> None:
    """Record `decision` on the request for `RateLimitHeadersMiddleware` to send.

    Stored in the ASGI scope's `state` dict, which every layer of one request shares --
    unlike a response object, which the endpoint may never build (a refusal raises) and
    a streaming endpoint builds after its headers are already decided.
    """
    request.state.rate_limit = _tighter(getattr(request.state, "rate_limit", None), decision)


class RateLimiterBackend(ABC):
    @abstractmethod
    def hit(self, key: str, max_requests: int, window_seconds: int) -> Decision:
        """Count one request against `key` and say whether it was within budget."""

    def check(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Return True if allowed, False if rate-limited."""
        return self.hit(key, max_requests, window_seconds).allowed

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

    def hit(self, key: str, max_requests: int, window_seconds: int) -> Decision:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if len(self._requests) >= self._sweep_threshold:
                self._evict_expired(cutoff)
            timestamps = self._requests.get(key, [])
            timestamps[:] = [t for t in timestamps if t > cutoff]
            allowed = len(timestamps) < max_requests
            if allowed:
                timestamps.append(now)
            self._requests[key] = timestamps
            # The oldest counted request is the first thing to leave the window, so it
            # is when the budget next moves -- and on a refusal, when a retry can work.
            reset = (
                max(1, math.ceil(timestamps[0] + window_seconds - now))
                if timestamps
                else window_seconds
            )
            return Decision(
                allowed=allowed,
                limit=max_requests,
                remaining=max(0, max_requests - len(timestamps)),
                reset_seconds=reset,
            )

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

    def hit(self, key: str, max_requests: int, window_seconds: int) -> Decision:
        """A fixed window whose clock starts at the first request and is never extended.

        `SET … NX EX` creates the counter with its expiry **only when it does not exist**,
        so the window is anchored to the first request. This used to call `EXPIRE` on every
        hit, which moved the expiry forward each time: a client over its budget that kept
        retrying -- exactly what a client with no `Retry-After` does -- held its own window
        open forever, and a 429 became permanent for as long as it kept asking. The three
        commands run in one MULTI/EXEC, so a process dying between creating the counter and
        giving it an expiry cannot leave a key that never expires.
        """
        full_key = f"ratelimit:{key}"
        pipe = self._client.pipeline()
        pipe.set(full_key, 0, ex=window_seconds, nx=True)
        pipe.incr(full_key)
        pipe.pttl(full_key)
        _, count, pttl = pipe.execute()
        if int(pttl) < 0:
            # A counter with no expiry -- left by the old code's crash window, or by a
            # key somebody wrote by hand. It would block this caller for ever, so give it one.
            self._client.expire(full_key, window_seconds)
            pttl = window_seconds * 1000
        count = int(count)
        return Decision(
            allowed=count <= max_requests,
            limit=max_requests,
            remaining=max(0, max_requests - count),
            reset_seconds=max(1, math.ceil(int(pttl) / 1000)),
        )

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

    @property
    def window_seconds(self) -> int:
        return self._window

    def hit(self, key: str, max_requests: int | None = None) -> Decision:
        """Count one request and return the full decision.

        `max_requests` overrides the budget for this call: a plan's allowance is known only
        once the caller is (ROAD_TO_10 3.5), so the limiter cannot be built with it.
        """
        backend = self._get_backend()
        return backend.hit(key, self._max if max_requests is None else max_requests, self._window)

    def check(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        return self.hit(key).allowed

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


#: Per-address budget for the routes where nobody has signed in yet and the address is
#: the only denominator there is: registration, password reset and confirmation, email
#: verification. Ten a minute is generous for a person and stops a script.
auth_limiter = RateLimiter(max_requests=10, window_seconds=60)

#: The same, for the three routes a *signed-in client* hits all day -- login, the second
#: factor, and the silent refresh. An office behind one NAT is one address and a hundred
#: engineers, and ten a minute across all of them is a lockout on a Monday morning
#: (ROAD_TO_10 3.3). The wider address budget is safe only because each of those routes is
#: also counted per *account* below, so guessing at one account stays at ten a minute
#: whatever the address does.
login_limiter = RateLimiter(max_requests=settings.auth_ip_requests_per_minute, window_seconds=60)

#: Per-account budget, keyed on the identifier the request names (a hash of the submitted
#: email, or the user a second-factor challenge belongs to), whether or not such an account
#: exists -- so a 429 here says nothing about who has one. See `account_key`.
account_limiter = RateLimiter(max_requests=10, window_seconds=60)


# ---------------------------------------------------------------------------
# Which backend is answering, and whether it is shared (ROAD_TO_10 3.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BackendReport:
    kind: str
    #: True only for Redis. An in-process budget is per worker, so with N workers the
    #: real limit is N times the configured one -- configured, working, and doing less.
    shared: bool
    detail: str


def backend_report() -> BackendReport:
    """What is counting, answered by the limiter that does the counting.

    Asks `auth_limiter` for its backend rather than reading `REDIS_URL`: a URL that is set
    and unreachable falls back to memory with a log line nobody reads at 3 a.m., and this is
    the one place that says what actually happened.
    """
    backend = auth_limiter._get_backend()
    if isinstance(backend, RedisBackend):
        return BackendReport("redis", True, "Rate limits are counted in Redis, shared by every worker.")
    configured = bool(getattr(settings, "redis_url", None))
    return BackendReport(
        "memory",
        False,
        "REDIS_URL is set but Redis could not be reached, so each worker counts its own budget."
        if configured
        else "No REDIS_URL, so each worker counts its own budget.",
    )


def refuse_an_unshared_limiter_across_workers() -> None:
    """Fail startup when several workers would each enforce their own budget.

    `config._harden_production` refuses the *configuration* (several workers, no Redis URL).
    This refuses the *fact*: a URL that is set, and a Redis that is not answering, leaves
    the limiter on memory exactly as no URL would -- and the configuration check passed.
    One worker keeps its in-process limiter, which is correct for it: the desktop app is one
    process, and Redis there would be a service to install for nothing.
    """
    if not settings.is_production or settings.web_concurrency <= 1:
        return
    report = backend_report()
    if report.shared:
        return
    raise RuntimeError(
        f"Refusing to start: {settings.web_concurrency} workers are configured but "
        f"{report.detail} Every worker would enforce its own copy of each limit, so the real "
        f"limit would be {settings.web_concurrency} times the configured one. Start Redis and "
        "set REDIS_URL, or run one worker."
    )


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
    principal = principal_id(request)
    if principal is not None:
        return f"{scope}:user:{principal}"
    return f"{scope}:ip:{client_ip(request)}"


def principal_id(request: Request) -> str | None:
    """The user id in the request's access token, or None. A signature check, no database."""
    token = request.cookies.get("kryova_access")
    if token is None:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if token:
        return decode_access_token(token)
    return None


def account_key(scope: str, identifier: str) -> str:
    """`scope:acct:<sha256>` for a per-account budget on a route nobody is signed in to.

    The identifier is what the request *names* -- a submitted email, a challenge's user id --
    and is counted whether or not an account answers to it, so a 429 cannot be used to ask
    "does this address have an account". It is hashed so the limiter's store (Redis, with
    its own retention and its own readers) never holds an address in clear.

    **The cost, stated:** anyone can spend an account's budget by naming it. Ten wrong
    passwords a minute against a stranger's address locks *that stranger* out for the rest of
    the minute, from an address of the attacker's choosing. A per-account limit trades that
    for the guarantee that online guessing at one account cannot exceed ten a minute however
    many addresses it comes from. The window is a minute, not an hour, to keep the first
    cost small; `docs/ROAD_TO_10.md` 3.3 and CLAUDE.md record the choice.
    """
    import hashlib

    digest = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    return f"{scope}:acct:{digest}"


def too_many_requests(decision: Decision, detail: str | None = None, *, limiter: "RateLimiter | None" = None) -> HTTPException:
    """The 429 for a refused `decision`, carrying `Retry-After` and the `RateLimit-*` set."""
    if detail is None:
        window = f" per {limiter.window_seconds} seconds" if limiter is not None else ""
        detail = f"Too many requests. This limit is {decision.limit}{window}."
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers=decision.headers(),
    )


def enforce(
    request: Request,
    limiter: RateLimiter,
    key: str,
    *,
    detail: str | None = None,
    max_requests: int | None = None,
) -> Decision:
    """Count one request against `key`; record the answer on the request; refuse if over.

    The one way a route spends a budget. `limiter.check(...)` directly returns a boolean and
    forgets the numbers, which is how seven auth routes came to answer 429 with no
    `Retry-After`. Here the decision is recorded for the response headers *before* it can
    raise, so the refusal itself carries the same set a success does.
    """
    decision = limiter.hit(key, max_requests)
    note(request, decision)
    if not decision.allowed:
        raise too_many_requests(decision, detail, limiter=limiter)
    return decision


class RateLimit:
    """A FastAPI dependency that refuses over-budget requests.

    Attach with `dependencies=[Depends(RateLimit("ai.chat", 30, 60))]`. The
    scope name is part of the key, so two limits on one principal are counted
    separately and a noisy endpoint cannot spend another one's budget.

    Refuses with `Retry-After`, which is not decoration: a client that knows
    when to come back stops hammering, and one that does not retries in a tight
    loop and turns a limit into the load it was meant to prevent.

    `max_requests` is a number, or a function of the request for a budget that depends on
    who is asking -- the plan's allowance (ROAD_TO_10 3.5). The function runs only once the
    caller's key is known, and must not raise: a limiter that errors turns "slow down" into
    "broken".
    """

    def __init__(
        self,
        scope: str,
        max_requests: "int | Callable[[Request], int]",
        window_seconds: int,
    ) -> None:
        self.scope = scope
        self.window_seconds = window_seconds
        self._budget = max_requests
        default = max_requests if isinstance(max_requests, int) else 0
        self._limiter = RateLimiter(max_requests=default, window_seconds=window_seconds)

    def budget_for(self, request: Request) -> int:
        return self._budget if isinstance(self._budget, int) else self._budget(request)

    def __call__(self, request: Request) -> None:
        enforce(
            request,
            self._limiter,
            limit_key(request, scope=self.scope),
            max_requests=self.budget_for(request),
            detail=None,
        )

    def reset(self) -> None:
        """Clear this limit's state. For tests and for the admin panel."""
        self._limiter.reset()


class PlanRateLimit(RateLimit):
    """A `RateLimit` whose budget is the caller's plan's, not a constant (ROAD_TO_10 3.5).

    `field` names one of the per-minute settings (`chat_requests_per_minute`, ...). The
    budget is resolved per request -- tenant override, then plan, then that global setting,
    through `core/limits.for_user` -- and **the key is unchanged**: still the principal where
    there is one, so two people on one plan do not share a budget. A caller nobody has signed
    in (an address, not a person) has no plan and gets the global setting, which is what the
    budget was before plans carried limits.

    The database session is the request's own (`get_db` is cached per request), so the lookup
    costs nothing extra in a route that already has one, and a test that overrides `get_db`
    sees the same rows the route does.
    """

    def __init__(self, scope: str, field: str, window_seconds: int = 60) -> None:
        super().__init__(scope, lambda _request: int(getattr(settings, field)), window_seconds)
        self.field = field

    def __call__(  # type: ignore[override]
        self, request: Request, db: Annotated[Session, Depends(get_db)]
    ) -> None:
        principal = principal_id(request)
        budget = (
            limits.for_user(db, principal, self.field).value
            if principal is not None
            else limits.global_limit(self.field).value
        )
        enforce(
            request,
            self._limiter,
            limit_key(request, scope=self.scope),
            max_requests=budget,
            detail=None,
        )


# ---------------------------------------------------------------------------
# Telling the client what is left (ROAD_TO_10 3.2)
# ---------------------------------------------------------------------------


class RateLimitHeadersMiddleware:
    """Adds the `RateLimit-*` headers to every response from a route that was limited.

    **Pure ASGI, not `BaseHTTPMiddleware`, because the chat answer is a stream.** The
    header set has to be written at `http.response.start`, and by then a dependency has
    already run and recorded its `Decision` in the shared scope state (`note`). Wrapping
    `send` is the one hook that sees the start of a streaming body without buffering it,
    and the one that still fires when the endpoint raised and Starlette built the 429.

    A route nothing limited carries no headers: an absent header is the honest answer for
    "there is no budget here", and a made-up one would be a number nobody enforces.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: MutableMapping[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_limits(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                decision = (scope.get("state") or {}).get("rate_limit")
                if isinstance(decision, Decision):
                    wanted = decision.headers()
                    replaced = {name.lower() for name in wanted}
                    # Replace, never append: a 429 raised by `enforce` already carries the
                    # same set, and two `RateLimit-Limit` lines is a header a client may
                    # read either way.
                    kept = [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.decode("latin-1").lower() not in replaced
                    ]
                    kept += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in wanted.items()]
                    message = {**message, "headers": kept}
            await send(message)

        await self.app(scope, receive, send_with_limits)
