"""The docs site and the public status page (P10.2, P10.4).

**Unauthenticated, like `trust`, and for the same reason** — documentation that
needs an account can only be read by people who already bought, and a status
page only its operator can read is a private dashboard. But the two routers here
are *not* paid for identically, and the difference is worth stating rather than
inheriting:

* `handbook` serves module constants and a schema this process generated. It
  touches no database at all, exactly like `trust`.
* `status` **does** take a `DbSession`, which `trust` deliberately never does.
  It is allowed because of what it reads: `MaintenanceWindow` and `Announcement`
  are operator-declared rows with no tenant column and no customer content, and
  the two free-text fields it publishes are the ones written *for* customers.
  It never reads a job, a project, a user or a usage record.

The line that keeps that true is a test: `tests/test_docs.py` asserts no route
in this module depends on `get_current_user`, and asserts by name the fields
`GET /admin/health` carries which this page must not — queue depth, storage
bytes, live sessions, user totals. Those are the size of the business and the
hours nobody is watching, and none of them answers "is it working".
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, HTTPException, Response
from fastapi import status as http_status

# The functions, imported directly. Not `from app.handbook import gallery` and
# then `gallery.gallery()`: `gallery.py` defines a function of the same name, so
# a package-level re-export rebinds the attribute and the module form raises
# `AttributeError` at request time and nowhere else. `app/handbook/__init__.py`
# records the trap.
from app.api.deps import DbSession
from app.core.status import read_status
from app.handbook.gallery import gallery, headline
from app.handbook.guides import GUIDES, guide_by_slug
from app.handbook.reference import reference

router = APIRouter(prefix="/handbook", tags=["handbook"])
status_router = APIRouter(prefix="/status", tags=["status"])

#: Five minutes, matching `trust`. Long enough that an outside link cannot be
#: used to hammer this process, short enough that a redeploy shows quickly.
_CACHE_CONTROL: Final = "public, max-age=300"

#: Thirty seconds for the status page. Shorter than the docs on purpose: this is
#: the page people refresh *during* an incident, and five-minute-stale
#: information at that moment is worse than none — it is the page telling
#: somebody the outage is still on after it has been fixed.
_STATUS_CACHE_CONTROL: Final = "public, max-age=30"


def _cache(response: Response, value: str = _CACHE_CONTROL) -> None:
    response.headers["Cache-Control"] = value


@router.get("")
def handbook_index(response: Response) -> dict[str, Any]:
    """What is here, with the counts, so a reader can see the shape."""
    _cache(response)
    entries = gallery()
    return {
        "guides": [
            {"slug": guide.slug, "title": guide.title, "outcome": guide.outcome}
            for guide in GUIDES
        ],
        "gallery_headline": headline(),
        "mission_count": len(entries),
        "buildable_mission_count": sum(1 for entry in entries if entry.buildable),
    }


@router.get("/guides")
def list_guides(response: Response) -> dict[str, Any]:
    _cache(response)
    return {"guides": [guide.to_dict() for guide in GUIDES]}


@router.get("/guides/{slug}")
def read_guide(slug: str, response: Response) -> dict[str, Any]:
    _cache(response)
    guide = guide_by_slug(slug)
    if guide is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="No such guide.")
    return guide.to_dict()


@router.get("/gallery")
def mission_gallery(response: Response) -> dict[str, Any]:
    """Every ladder rung, with what it does not claim.

    Derived from `app.design.missions.LADDER`, so it cannot describe a suite
    this build does not have.
    """
    _cache(response)
    return {
        "headline": headline(),
        "missions": [entry.to_dict() for entry in gallery()],
    }


@router.get("/reference")
def api_reference(request_response: Response) -> dict[str, Any]:
    """The API reference, from this deployment's own OpenAPI document.

    Imported inside the function: `app.main` imports the router, so a
    module-level import of the application would be a cycle. It is also the
    honest place for it — the schema is a property of the running app, not of
    this module.
    """
    from app.main import app

    _cache(request_response)
    return reference(app.openapi())


@status_router.get("")
def service_status(db: DbSession, response: Response) -> dict[str, Any]:
    """Is Kryova working, and what happened recently (P10.4).

    Carries **none** of the fleet's numbers. See this module's docstring: queue
    depth, storage, live sessions and user totals are the size of the business
    and the hours nobody is watching, and none of them answers the question.
    """
    _cache(response, _STATUS_CACHE_CONTROL)
    return read_status(db).to_dict()
