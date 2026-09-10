"""The design record over HTTP: read it, edit one parameter, diff two revisions.

P5 task 3 asks for "the spec rendered beside the chat, parameters editable with
units checked"; P5 task 6 asks for "edit a parameter mid-mission". Both were
`PARTIAL` with the same sentence — `DesignSpec` was an in-memory IR with no model
and no route, so there was nothing to render and nothing to PATCH. These are the
routes that were missing.

**The client cannot POST a spec.** The only write is a single parameter, and
nothing here accepts a `document`. A route that took a whole spec would let a
client author a design the server never compiled, and the first malformed one
would arrive as a compile error against a revision that had already been
written — with the design left in a state no build produced. Specs are authored
by the agent, through `app.core.designs.save`, which compiles before it writes.

**Every design is addressed through its conversation.** There is no
`/designs/{id}`: the ownership check is `conversation.owner_id`, one check in one
place, and an id-addressed route would need a second one that could disagree with
it. That the URL says which conversation is also what the frontend needs, since
the spec panel is rendered beside a conversation and never on its own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.core import designs
from app.design.errors import SpecError
from app.models.conversation import Conversation
from app.models.design import DesignDocument, DesignRevision
from app.models.user import User
from app.schemas.design import (
    DesignDiffRead,
    DesignEdited,
    DesignPage,
    DesignRead,
    DesignSummary,
    ParameterEdit,
    RevisionPage,
    RevisionRead,
)

router = APIRouter(prefix="/designs", tags=["designs"])

_NO_DESIGN = (
    "This conversation has no design yet. One appears when the agent first describes "
    "the part as a specification rather than as a sequence of operations."
)


def _owned_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    # 404 rather than 403 for someone else's conversation, matching the rest of
    # the API — never confirm that an id exists.
    if conversation is None or conversation.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _document(db: Session, user: User, conversation_id: str) -> DesignDocument:
    conversation = _owned_conversation(db, user, conversation_id)
    document = designs.load(db, conversation)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_DESIGN)
    return document


@router.get("", response_model=DesignPage)
def list_designs(
    db: DbSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DesignPage:
    """Every design this user owns, most recently edited first.

    Paginated like every other list in this API. The summary shape deliberately
    omits `document`: a page of twenty specs is a page of twenty feature trees,
    and a list view needs a name and a revision number.
    """
    owned = select(Conversation.id).where(Conversation.owner_id == current_user.id)
    total = (
        db.scalar(
            select(func.count())
            .select_from(DesignDocument)
            .where(DesignDocument.conversation_id.in_(owned))
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(DesignDocument)
            .where(DesignDocument.conversation_id.in_(owned))
            .order_by(DesignDocument.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return DesignPage(
        items=[DesignSummary.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{conversation_id}", response_model=DesignRead)
def read_design(db: DbSession, current_user: CurrentUser, conversation_id: str) -> DesignDocument:
    """The current spec for this conversation."""
    return _document(db, current_user, conversation_id)


@router.get("/{conversation_id}/revisions", response_model=RevisionPage)
def list_revisions(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> RevisionPage:
    """The design's history, newest first.

    The revision *documents* are not in this shape either — the same reason as
    the list above, and more sharply: the history of a fifty-feature part is
    fifty copies of it.
    """
    document = _document(db, current_user, conversation_id)
    total = (
        db.scalar(
            select(func.count())
            .select_from(DesignRevision)
            .where(DesignRevision.design_id == document.id)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(DesignRevision)
            .where(DesignRevision.design_id == document.id)
            .order_by(DesignRevision.revision_number.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return RevisionPage(
        items=[RevisionRead.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{conversation_id}/diff", response_model=DesignDiffRead)
def read_diff(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    from_revision: Annotated[int, Query(ge=1, alias="from")],
    to_revision: Annotated[int, Query(ge=1, alias="to")],
) -> DesignDiffRead:
    """What changed between two revisions of this design.

    Computed by `app.design.diff`, which compiles both sides — the same
    implementation the correction loop and the approval gate use. A second one
    here, or on the client, would be a second answer to "does this change the
    part", and the two would disagree the first time somebody rewrote a
    rationale note.
    """
    document = _document(db, current_user, conversation_id)
    before = designs.revision(db, document, from_revision)
    after = designs.revision(db, document, to_revision)
    missing = [
        number
        for number, found in ((from_revision, before), (to_revision, after))
        if found is None
    ]
    if missing or before is None or after is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"This design has {document.revision_number} revision(s); "
                f"{', '.join(str(n) for n in missing)} is not among them."
            ),
        )
    return DesignDiffRead(
        from_revision=from_revision,
        to_revision=to_revision,
        diff=designs.diff_between(document, before, after).to_dict(),
    )


@router.patch("/{conversation_id}/parameters/{name}", response_model=DesignEdited)
def edit_parameter(
    payload: ParameterEdit,
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: str,
    name: str,
) -> DesignEdited:
    """Change one decision in the design — P5 task 6's "edit a parameter mid-mission".

    A refusal comes back as 422 carrying the compiler's own sentence, which
    already names what went wrong and what to do instead: a parameter that is
    not declared is answered with the list of ones that are, and a *derived*
    parameter is answered with the formula it is derived from and the advice to
    change one of its inputs. Rewriting either here would make both worse.

    The edit lands as a revision, so a run in flight sees it the next time it
    reads the spec, and an audit afterwards sees who moved it and what moved
    with it.
    """
    document = _document(db, current_user, conversation_id)
    try:
        outcome = designs.set_parameter(
            db,
            document,
            name,
            payload.value,
            author=designs.AUTHOR_USER,
            author_id=current_user.id,
        )
    except SpecError as exc:
        # 422 rather than 400: the request was well-formed and the *content* was
        # refused, which is the distinction the rest of this API keeps.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    db.commit()
    db.refresh(outcome.document)
    return DesignEdited(
        design=DesignRead.model_validate(outcome.document),
        changed=outcome.changed,
        diff=outcome.diff.to_dict() if outcome.diff is not None else None,
    )
