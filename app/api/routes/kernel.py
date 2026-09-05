"""Looking at the part the agent is building.

Step 2 of the integration gap found on 2026-09-05: `app/render/` renders eight
canonical views deterministically, cuts sections and diffs two of them, and until
this module existed **nothing outside a test ever called it**. Same for
`app/kernel/measurement`. The capability was green on the board and unreachable
from the product, which is a different thing from being finished.

Two endpoints, both about the part a *conversation* owns:

* `GET .../render` — a PNG of the current state, from any canonical view.
* `GET .../measure` — what the kernel measures on it, with provenance.

**These serve the open-kernel backend only, and say so rather than guessing.** On
`GEOMETRY_BACKEND=catia` the part lives on the workstation, not in this process;
producing a picture of it means asking the seat for a screenshot, which is a
different mechanism (and a different fidelity) from HLR projection. Answering with
a plausible image built from something else would be worse than refusing, so this
refuses, names the backend it needs, and stays honest about which kernel drew what.

The render's own digest is the ETag. That is not a trick: 4.1 makes the bytes a
deterministic function of the geometry, so "the same part renders to the same
bytes" is exactly the guarantee an ETag needs, and a browser polling this while an
agent works gets a 304 until the part actually changes.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.geometry import backends
from app.models import Conversation, User
from app.render.views import ALL_VIEWS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kernel", tags=["kernel"])

#: Canvas bounds for a requested render. The floor keeps a request from producing
#: an image too small to read; the ceiling keeps one from asking this process to
#: allocate a poster. Both are generous — the defaults sit well inside them.
MIN_PIXELS = 128
MAX_PIXELS = 4096


def _owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    """The caller's conversation, or 404.

    404 and never 403, like every other resource here, so ids cannot be
    enumerated across accounts.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


def _live_document(conversation_id: str) -> Any:
    """The `PartDocument` this conversation has built, or an explained refusal.

    The document rather than the bare shape, because the two callers want
    different things from it — a render wants `.shape`, a measurement wants
    `.measure()`, and the document is what owns the cache behind the second.

    Three distinct 409s rather than one, because the remedies are different and
    the user has to be able to tell them apart: the wrong backend is a setting,
    an evicted document means starting the part again, and nothing built yet
    simply means asking the agent for something first.
    """
    if not backends.is_local():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This part is being built on a CATIA seat, so there is nothing in this "
                "process to draw. Rendering here serves the open kernel; set "
                "GEOMETRY_BACKEND=occt to build in-process, or take a screenshot on the "
                "workstation."
            ),
        )
    if backends.was_evicted(conversation_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The part this conversation was building is no longer in memory — too "
                "many documents were open at once and this one was closed. Nothing was "
                "saved. Ask the agent to build it again."
            ),
        )

    runner = backends.peek_session(conversation_id)
    document = getattr(runner, "document", None) if runner is not None else None
    if document is None or getattr(document, "shape", None) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Nothing has been built in this conversation yet. Ask for a part first — "
                "for example, a 60 by 40 by 20 plate."
            ),
        )
    return document


@router.get(
    "/conversations/{conversation_id}/render",
    responses={200: {"content": {"image/png": {}}}},
    response_class=Response,
)
def render_conversation_part(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    view: Annotated[str, Query(description=f"One of: {', '.join(ALL_VIEWS)}")] = "iso",
    width: Annotated[int, Query(ge=MIN_PIXELS, le=MAX_PIXELS)] = 1024,
    height: Annotated[int, Query(ge=MIN_PIXELS, le=MAX_PIXELS)] = 768,
    section: Annotated[
        str | None,
        Query(description="Cut through the middle of an axis before drawing: x, y or z."),
    ] = None,
) -> Response:
    """A PNG of the part this conversation is building.

    `section` cuts the part in half through the middle of the named axis and draws
    the cut face hatched, which is the only way to see an internal feature in a
    wireframe — a bore reads as two dashed lines and a pocket reads as nothing.
    """
    _owned_conversation(db, current_user, conversation_id)
    shape = _live_document(conversation_id).shape

    from app.render import render
    from app.render.section import SectionError, mid_section, render_section
    from app.render.views import view_named

    try:
        camera = view_named(view)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        if section:
            cut = mid_section(shape, section.strip().lower())  # type: ignore[arg-type]
            shot = render_section(shape, cut, camera, width=width, height=height)
        else:
            shot = render(shape, camera, width=width, height=height)
    except SectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a kernel fault must not 500 the viewer
        logger.exception("Rendering failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be drawn: {exc}",
        ) from exc

    return Response(
        content=shot.png,
        media_type="image/png",
        headers={
            # Deterministic bytes make this a real content hash, so a client
            # polling during a build gets 304s until the geometry actually moves.
            "ETag": f'"{shot.digest}"',
            # Never cached by age: the part changes when the agent acts, not on a
            # timer, and a stale picture of a part is worse than a slow one.
            "Cache-Control": "no-cache",
            "X-Kryova-View": shot.view,
            "X-Kryova-Blank": "1" if shot.is_blank else "0",
        },
    )


@router.get("/conversations/{conversation_id}/measure")
def measure_conversation_part(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    detail: Annotated[
        str, Query(description="How much to measure: shape, bounds, full or inertia.")
    ] = "full",
) -> dict[str, Any]:
    """What the kernel measures on the part this conversation is building.

    Carries the provenance sidecar, so a caller can tell an integrated mass from a
    ray-cast wall thickness. `Detail` exists for latency rather than for taste —
    the full set integrates over the whole shape.
    """
    _owned_conversation(db, current_user, conversation_id)
    document = _live_document(conversation_id)

    from app.kernel.measurement import Detail

    try:
        level = Detail(detail.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(one.value for one in Detail)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{detail!r} is not a detail level. Use one of: {allowed}.",
        ) from exc

    try:
        payload = document.measure(detail=level)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Measuring failed for conversation %s", conversation_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The part could not be measured: {exc}",
        ) from exc

    return {
        "backend": backends.selected_backend(),
        "backend_version": backends.backend_version(),
        "detail": level.value,
        "measurements": payload,
    }


__all__ = ["router"]
