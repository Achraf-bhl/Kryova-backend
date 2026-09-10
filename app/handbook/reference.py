"""The API reference, built from the OpenAPI document FastAPI already serves.

The plan's wording is "API reference **from the OpenAPI schema that already
exists**", and the emphasis is the entire design. A hand-kept endpoint list is
the single most reliable way to publish something untrue about a product: it
starts correct, and every subsequent change makes it slightly less so, silently,
with no test able to notice.

**What this adds over linking straight to `/openapi.json`** is grouping and
triage — a reader wants "what can I do with a project", not four hundred lines
of JSON Schema. It deliberately does **not** re-describe anything: summaries and
descriptions come from the route docstrings, which is where they already are.

**Authenticated routes are marked as such rather than filtered out.** Someone
deciding whether this product fits needs to see the shape of the whole API, and
hiding the authenticated half would make it look like a toy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Route prefixes that are readable with no account at all. Kept as a tuple
#: here rather than inferred from the schema because OpenAPI does not record
#: what these routers' docstrings decide — `trust` and `share` are public by an
#: argued decision, and `tests/test_docs.py` checks this list against the
#: dependency graph rather than trusting it.
PUBLIC_PREFIXES: tuple[str, ...] = ("/trust", "/share", "/handbook", "/status", "/platform")


@dataclass(frozen=True)
class Operation:
    method: str
    path: str
    summary: str
    tag: str
    public: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "path": self.path,
            "summary": self.summary,
            "tag": self.tag,
            "public": self.public,
        }


def _is_public(path: str, prefix: str) -> bool:
    trimmed = path[len(prefix) :] if path.startswith(prefix) else path
    return trimmed.startswith(PUBLIC_PREFIXES)


def reference(schema: dict[str, Any], *, api_prefix: str = "/api/v1") -> dict[str, Any]:
    """Group an OpenAPI document into something a person can read.

    Takes the schema rather than reaching for `app.main.app`, so this module
    imports nothing from the application and the test can hand it a fixture.
    """
    operations: list[Operation] = []
    for path, methods in sorted(schema.get("paths", {}).items()):
        for method, operation in methods.items():
            if method.upper() not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                continue
            tags = operation.get("tags") or ["other"]
            operations.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    # The route's own docstring first line. Never re-written
                    # here: two descriptions of one endpoint is one description
                    # too many.
                    summary=operation.get("summary", "").strip(),
                    tag=str(tags[0]),
                    public=_is_public(path, api_prefix),
                )
            )

    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        grouped.setdefault(operation.tag, []).append(operation.to_dict())

    return {
        "title": schema.get("info", {}).get("title", "Kryova API"),
        "version": schema.get("info", {}).get("version", ""),
        "operation_count": len(operations),
        "public_operation_count": sum(1 for o in operations if o.public),
        "groups": [
            {"tag": tag, "operations": grouped[tag]} for tag in sorted(grouped)
        ],
        "note": (
            "Generated from this deployment's own OpenAPI document. If an endpoint "
            "is not listed here, this build does not serve it."
        ),
    }


__all__ = ["PUBLIC_PREFIXES", "Operation", "reference"]
