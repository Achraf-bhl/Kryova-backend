"""Evaluating feature flags, on the server, once per request (P3.5).

**Server-evaluated is the design, not an implementation detail.** The resolved
map rides to the frontend with the session, so the UI and the API always agree
about what is on. A flag the client re-decides is a feature that is half
enabled — the button is there and the endpoint refuses, or the endpoint works
and nobody can reach it — and both of those present as bug reports nobody can
reproduce.

**The precedence ladder, highest first, and each rung has a reason:**

1. `killed` — the operator's one action when a feature is hurting people. It
   beats *everything*, including a per-tenant override somebody set last month.
   A kill switch an override can outvote is not a kill switch.
2. A **user** override — the most specific subject wins, so "turn this on for
   this one person to reproduce a bug" works inside a tenant that has it off.
3. An **organisation** override — the tenant-wide decision.
4. The **rollout percentage** — deterministic, see below.
5. `enabled` — the default.

**The rollout is a hash, never a random draw.** `random()` per request would
flicker a feature on and off under one user: they would see a button, reload,
and lose it, which is indistinguishable from a broken deployment and impossible
to support. Bucketing on `sha256(key + subject)` means a given subject gets the
same answer forever, and raising the percentage only ever *adds* people —
nobody who had the feature loses it when the rollout widens, which is the
property that makes a staged rollout safe to run forwards.
"""

from __future__ import annotations

import hashlib
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeatureFlag, FeatureFlagOverride, User

#: The subject a rollout buckets on: the organisation where there is one, the
#: user otherwise. Bucketing on the tenant rather than the person matters — a
#: team where three of five engineers have a feature is a team that cannot talk
#: to itself about the product.
BUCKETS: Final = 100


def bucket_of(key: str, subject: str) -> int:
    """Which of 100 buckets `subject` falls in for `key`. Stable forever.

    Keyed on the flag *and* the subject, so two flags at 10% do not select the
    same tenth of the population — otherwise an unlucky tenant is in every early
    rollout the product ever runs.
    """
    digest = hashlib.sha256(f"{key}:{subject}".encode()).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


def _override_for(
    flag: FeatureFlag, user_id: str, organisation_ids: frozenset[str]
) -> bool | None:
    """The most specific override that applies, or None."""
    user_answer: bool | None = None
    org_answer: bool | None = None
    for override in flag.overrides:
        if override.user_id == user_id:
            user_answer = override.enabled
        elif override.organisation_id in organisation_ids:
            # A person in two organisations, one of which has the flag on:
            # `any` rather than "the first row we happened to load", so the
            # answer does not depend on row order. Enabled wins because the
            # tenant that turned it on is the one relying on it.
            org_answer = bool(org_answer) or override.enabled
    if user_answer is not None:
        return user_answer
    return org_answer


def evaluate(
    db: Session,
    *,
    user: User | None = None,
    organisation_ids: frozenset[str] | None = None,
) -> dict[str, bool]:
    """Every flag resolved for this caller.

    An anonymous caller (`user=None`) gets the global defaults with no rollout
    and no overrides: there is no stable subject to bucket on, and bucketing on
    an address would put a whole office in one bucket and move people between
    buckets when they change network.
    """
    flags = list(db.scalars(select(FeatureFlag)).all())
    if not flags:
        return {}

    tenants = organisation_ids or frozenset()
    resolved: dict[str, bool] = {}
    for flag in flags:
        resolved[flag.key] = _resolve(flag, user=user, organisation_ids=tenants)
    return resolved


def _resolve(
    flag: FeatureFlag, *, user: User | None, organisation_ids: frozenset[str]
) -> bool:
    if flag.killed:
        return False
    if user is None:
        return flag.enabled

    override = _override_for(flag, user.id, organisation_ids)
    if override is not None:
        return override

    if flag.rollout_percentage > 0:
        # The tenant where there is one, so a team is in or out together.
        subject = next(iter(sorted(organisation_ids)), user.id)
        if bucket_of(flag.key, subject) < flag.rollout_percentage:
            return True

    return flag.enabled


def is_enabled(
    db: Session,
    key: str,
    *,
    user: User | None = None,
    organisation_ids: frozenset[str] | None = None,
) -> bool:
    """One flag, for a server-side branch.

    **An unknown key is False**, never an error. A flag removed from the
    database while code still checks it must fail closed: the alternative is a
    500 on every request that touches the branch, which turns tidying up into an
    outage.
    """
    flag = db.scalar(select(FeatureFlag).where(FeatureFlag.key == key))
    if flag is None:
        return False
    return _resolve(flag, user=user, organisation_ids=organisation_ids or frozenset())


def set_override(
    db: Session,
    flag: FeatureFlag,
    *,
    enabled: bool,
    organisation_id: str | None = None,
    user_id: str | None = None,
) -> FeatureFlagOverride:
    """Force a flag on or off for one tenant or one person."""
    if (organisation_id is None) == (user_id is None):
        raise ValueError("An override names exactly one of an organisation or a user")
    existing = db.scalar(
        select(FeatureFlagOverride).where(
            FeatureFlagOverride.feature_flag_id == flag.id,
            FeatureFlagOverride.organisation_id == organisation_id,
            FeatureFlagOverride.user_id == user_id,
        )
    )
    if existing is not None:
        existing.enabled = enabled
        db.flush()
        return existing
    override = FeatureFlagOverride(
        feature_flag_id=flag.id,
        organisation_id=organisation_id,
        user_id=user_id,
        enabled=enabled,
    )
    db.add(override)
    db.flush()
    return override


__all__ = ["BUCKETS", "bucket_of", "evaluate", "is_enabled", "set_override"]
