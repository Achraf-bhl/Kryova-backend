"""Which number answers a limit, for whom (ROAD_TO_10 3.5).

Six limits used to be global constants: the concurrent-run ceiling, the length of the
queue behind it, and the per-minute budgets for chat, simulations, MCP and CATIA
operations. A hosted deployment sells different plans and a self-hosted one has a
different operator, and neither can say "this organisation may send forty chat requests a
minute" if the number is a module constant read at import.

**One resolver, and the same order everywhere:** a per-tenant override on the billing
account, then the plan's limit (`PLAN_LIMITS` in settings), then the global setting of the
same name. It is `metering.quota_envelope` that applies that order -- the quota surface the
organisation owner reads already says per field which of the three answered -- and this
module is how the *enforcement sites* ask it, so what an owner is shown and what a request
meets cannot be two different numbers. Before this, the envelope listed
`max_concurrent_simulations_per_user` with its source while the route and the agent tool
both read `settings` directly and ignored it: an override was displayed and never applied.

Two questions, because two kinds of limit have two kinds of "whose":

* `for_organisation` -- work done *on a project* (how many runs may be held, how many may
  wait) belongs to the project's organisation, whoever on it started the run.
* `for_user` -- a rate is decided before the request body is read, so there is no project
  yet. A person who belongs to several organisations gets **the most generous of them**:
  a rate rations a person, and a paid team's engineer is not throttled to a free
  organisation's budget because somebody invited them to it. Stated here because the
  alternative (the first organisation by id, as billing uses) is also defensible and
  gives a different number.

`for_user` is cached for `CACHE_SECONDS` per person, because it sits on the hot path of
every chat turn and MCP call and a plan lookup is a database round trip (about 250 ms on a
remote one). The cost is that a plan change reaches *other* workers that late; the process
that made the change forgets at once (`forget`).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from app.core.config import LIMIT_FIELDS, settings
from app.models.organisation import organisation_ids_for

#: How long one person's resolved limits are reused. Short enough that a plan upgrade is felt
#: within a minute on every worker; long enough that a burst of requests pays for one lookup.
CACHE_SECONDS: Final = 30.0

SOURCE_GLOBAL: Final = "global settings"


@dataclass(frozen=True, slots=True)
class ResolvedLimit:
    """A limit's value and where it came from, so a refusal can say whose number it was."""

    name: str
    value: int
    #: "tenant override", "plan free", "global settings" -- the envelope's own words.
    source: str
    #: The organisation whose limit answered, or None when it fell through to the global one.
    organisation_id: str | None

    @property
    def from_settings(self) -> bool:
        return self.source == SOURCE_GLOBAL


def global_limit(name: str) -> ResolvedLimit:
    """The setting of this name, which is what answers when nothing more specific does."""
    _require_known(name)
    return ResolvedLimit(name, int(getattr(settings, name)), SOURCE_GLOBAL, None)


def for_organisation(db: Session, organisation_id: str, name: str) -> ResolvedLimit:
    """The limit in force for one organisation, with the source the quota surface shows."""
    _require_known(name)
    from app.core.metering import (
        quota_envelope,  # local: metering reaches the CATIA dispatcher, which reaches us
    )

    limit = quota_envelope(db, organisation_id).limit_for(name)
    if limit is None:  # pragma: no cover - `_QUOTA_FIELDS` and `LIMIT_FIELDS` are held equal by a test
        return global_limit(name)
    return ResolvedLimit(name, limit.limit, limit.source, organisation_id)


def for_user(db: Session, user_id: str, name: str) -> ResolvedLimit:
    """The most generous limit among the organisations this person belongs to.

    A person in none is answered by the global setting rather than refused: a limit that
    needs a tenant must not stop the one user who somehow has none. Ties keep the first
    organisation by id, so the answer is the same on every call.
    """
    _require_known(name)
    return _for_user(db, user_id)[name]


def forget() -> None:
    """Drop every cached resolution. For a plan change in this process, and for tests."""
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------


_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, ResolvedLimit]]] = {}


def _require_known(name: str) -> None:
    if name not in LIMIT_FIELDS:
        raise KeyError(
            f"{name!r} is not a limit a plan can change. The limits are {', '.join(LIMIT_FIELDS)}."
        )


def _for_user(db: Session, user_id: str) -> dict[str, ResolvedLimit]:
    from app.core.metering import quota_envelope  # local, as in `for_organisation`

    now = time.monotonic()
    with _lock:
        cached = _cache.get(user_id)
        if cached is not None and cached[0] > now:
            return cached[1]

    organisations = organisation_ids_for(db, user_id)
    resolved: dict[str, ResolvedLimit] = {}
    if not organisations:
        resolved = {name: global_limit(name) for name in LIMIT_FIELDS}
    else:
        envelopes = [quota_envelope(db, organisation) for organisation in organisations]
        for name in LIMIT_FIELDS:
            candidates = []
            for envelope in envelopes:
                limit = envelope.limit_for(name)
                if limit is not None:
                    candidates.append(
                        ResolvedLimit(name, limit.limit, limit.source, envelope.organisation_id)
                    )
            # `max` keeps the first of equals, and `organisation_ids_for` is sorted.
            resolved[name] = max(candidates, key=lambda c: c.value) if candidates else global_limit(name)

    with _lock:
        _cache[user_id] = (now + CACHE_SECONDS, resolved)
    return resolved
