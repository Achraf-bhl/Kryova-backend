"""Attachments over HTTP: hand one over, list them, read what came out (P4 1/3/6).

**Uploading and attaching are two steps, on purpose.** The bytes go up through
the existing chunked-upload path — resumable, content-addressed, deduplicated,
and already tested — and *this* route takes the resulting `media_id` and says
what it is for. Reimplementing the upload here to save a round trip would give a
second, non-resumable path for large files, which is the one case resumability
exists for.

**An attachment that could not be read still gets a row and still appears in the
list.** A panel that silently omitted the STEP file somebody dropped in would
leave them wondering whether it uploaded at all; what they need is the row, the
status, and the sentence saying what the file actually is.

**`GET /attachments/{id}/content` renders the extraction, never the file.** The
blob is downloadable through the media routes, which already own that. What this
returns is the fragments with their citations — and for an inferred read, the
"unverified read" label travels *with* the content rather than being left to the
screen that happens to show it (P4 task 3).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, MediaServiceDep
from app.core import attachments
from app.models import Conversation, Media, User
from app.models.attachment import Attachment
from app.schemas.attachment import (
    AttachmentCreate,
    AttachmentExtraction,
    AttachmentPage,
    AttachmentRead,
)

router = APIRouter(prefix="/attachments", tags=["attachments"])


def _owned(db: Session, user: User, attachment_id: str) -> Attachment:
    attachment = db.get(Attachment, attachment_id)
    # 404 rather than 403, the rule the whole API keeps.
    if attachment is None or attachment.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return attachment


@router.post("", response_model=AttachmentRead, status_code=status.HTTP_201_CREATED)
def create_attachment(
    payload: AttachmentCreate,
    db: DbSession,
    current_user: CurrentUser,
    media_service: MediaServiceDep,
) -> Attachment:
    """Attach an already-uploaded blob to a conversation, and read it.

    Answers `201` whatever the reading produced. An unsupported format is not a
    failed request — the row exists, the status says `unsupported`, and the
    detail names what the file is and what to do with it instead. Returning an
    error here would lose the record that the user handed us something.
    """
    stored = db.get(Media, payload.media_id)
    if stored is None or stored.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    conversation: Conversation | None = None
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if conversation is None or conversation.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

    ingested = attachments.attach(
        db,
        owner=current_user,
        media=stored,
        filename=payload.filename or stored.filename,
        path=media_service.local_path(stored),
        conversation=conversation,
        project_id=payload.project_id,
    )
    db.commit()
    db.refresh(ingested.attachment)
    return ingested.attachment


@router.get("", response_model=AttachmentPage)
def list_attachments(
    db: DbSession,
    current_user: CurrentUser,
    conversation_id: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AttachmentPage:
    """This user's attachments, newest first, optionally scoped to a conversation.

    The list is deliberately complete: unsupported and failed rows are in it.
    Filtering them out would make the panel disagree with what the user
    remembers doing.
    """
    conversation: Conversation | None = None
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

    rows = list(attachments.list_for(db, conversation=conversation, owner=current_user))
    start = (page - 1) * page_size
    return AttachmentPage(
        items=[AttachmentRead.model_validate(row) for row in rows[start : start + page_size]],
        total=len(rows),
        page=page,
        page_size=page_size,
    )


@router.get("/{attachment_id}", response_model=AttachmentRead)
def read_attachment(
    db: DbSession, current_user: CurrentUser, attachment_id: str
) -> Attachment:
    return _owned(db, current_user, attachment_id)


@router.get("/{attachment_id}/content", response_model=AttachmentExtraction)
def read_extraction(
    db: DbSession, current_user: CurrentUser, attachment_id: str
) -> AttachmentExtraction:
    """What was read out of this file, with a citation on every fragment.

    Not the file — the media routes own downloading that. And **the
    "unverified read" label is part of this response**, not a decoration the
    client adds: a wrongly read tolerance is worse than an unread one, so the
    warning travels with the content wherever it goes.
    """
    attachment = _owned(db, current_user, attachment_id)
    extracted = attachment.extracted or {}
    return AttachmentExtraction(
        attachment_id=attachment.id,
        status=attachment.status,
        status_detail=attachment.status_detail,
        reader=attachment.reader,
        reliability=attachment.reliability,
        unverified_note=attachments.unverified_note(attachment),
        notes=list(extracted.get("notes") or []),
        truncated=bool(extracted.get("truncated")),
        fragment_count=int(extracted.get("fragment_count") or 0),
        fragments=list(extracted.get("fragments") or []),
        unread=list(extracted.get("unread") or []),
        dimensions=attachments.dimensions_in(attachment),
    )


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_attachment(db: DbSession, current_user: CurrentUser, attachment_id: str) -> None:
    """Detach a file from a conversation.

    The **blob survives**: `media_id` is `RESTRICT`, the store is
    content-addressed, and the same bytes may be attached elsewhere. Deleting
    the row removes the attachment and its extraction; whether the blob is
    orphaned is `MediaService.delete_digests_if_orphaned`'s question, and it is
    deliberately not asked here — a delete that reached into the store would
    make removing one attachment able to break another.
    """
    attachment = _owned(db, current_user, attachment_id)
    db.delete(attachment)
    db.commit()
