"""Walking the routes this build actually serves.

`app.routes` is **not** flat on this FastAPI version: it holds a handful of
entries, one of which is an `_IncludedRouter` wrapping everything under
`/api/v1`. Iterating it directly finds nine paths out of a hundred and fifty, so
a guard written against it passes by finding almost nothing — which is the worst
way for a guard to be wrong, because it looks like it is working.

Extracted here after the second test needed it (`tests/test_docs.py` had the
first copy, and `tests/test_designs.py` and `tests/test_turn_events.py` each
wanted the same walk for a different reason). Every helper asserts it found
something, so a walk that stops working fails loudly rather than passing
vacuously.

Paths come back **without** the `/api/v1` prefix, the same way the routers spell
them.
"""

from __future__ import annotations

from typing import Any


def leaf_routes() -> list[Any]:
    """Every leaf route, descending through included routers."""

    from app.main import app

    def walk(routes: list[Any]) -> list[Any]:
        found: list[Any] = []
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                found.extend(walk(list(included.routes)))
                continue
            if getattr(route, "path", None) and getattr(route, "methods", None):
                found.append(route)
        return found

    routes = walk(list(app.routes))
    assert len(routes) > 50, "the route walk found almost nothing; it is not walking"
    return routes


def methods_for(path: str) -> set[str]:
    """The HTTP methods served at exactly `path`, HEAD excluded.

    FastAPI adds HEAD to every GET, which is never the thing a test is asking
    about, so it is dropped here rather than in each caller's assertion.
    """
    found = {
        method.upper()
        for route in leaf_routes()
        if route.path == path
        for method in route.methods
    }
    assert found, f"no route serves {path}; the assertion below would be vacuous"
    return found - {"HEAD"}


def methods_under(prefix: str) -> set[str]:
    """Every method served at or below `prefix`, HEAD excluded.

    Deliberately does **not** assert it found anything: the callers are checking
    that a *class* of write does not exist, and an empty answer is the passing
    one. They assert the prefix is served separately.
    """
    return {
        method.upper()
        for route in leaf_routes()
        if route.path.startswith(prefix)
        for method in route.methods
    } - {"HEAD"}
