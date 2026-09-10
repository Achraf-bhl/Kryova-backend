"""Request and response shapes for attachments (P4 tasks 1, 3, 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.attachment import ExtractionStatus


class AttachmentCreate(BaseModel):
    """Attach a blob that has already been uploaded.

    Takes a `media_id` rather than the bytes: the chunked-upload path is
    resumable and content-addressed and already tested, and a second upload
    route here would be the non-resumable one that large files fall down.
    """

    media_id: str
    conversation_id: str | None = None
    project_id: str | None = None
    #: The name to show. Defaults to the blob's, and is the *user's* text either
    #: way — untrusted, never rendered raw.
    filename: str | None = Field(default=None, max_length=512)


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str | None
    project_id: str | None
    media_id: str
    filename: str
    detected_kind: str
    detected_format: str
    status: ExtractionStatus
    #: Why, for `unsupported` and `failed`. Server-authored prose naming the
    #: next action — never the file's own bytes.
    status_detail: str | None
    reader: str
    reliability: str
    #: True when the characters were *inferred* rather than transcribed, so the
    #: client must show the warning. A property on the row rather than a rule
    #: the client re-derives: a safety label with two implementations has two
    #: standards.
    needs_confirmation: bool
    created_at: datetime
    extracted_at: datetime | None


class AttachmentPage(BaseModel):
    items: list[AttachmentRead]
    total: int
    page: int
    page_size: int


class AttachmentExtraction(BaseModel):
    """What was read out of one file, with a citation on every fragment.

    `unverified_note` is `None` or the whole sentence — never an empty string,
    so a client cannot render a blank line where a warning belongs and believe
    it has handled the case.
    """

    attachment_id: str
    status: ExtractionStatus
    status_detail: str | None
    reader: str
    reliability: str
    unverified_note: str | None
    #: Reader-level remarks: "3 sheets, 1 was empty". Server-authored prose.
    notes: list[str]
    #: True when more fragments were read than are stored. Said rather than
    #: hidden: a silently shortened extraction reads as a short document.
    truncated: bool
    fragment_count: int
    fragments: list[dict[str, Any]]
    #: Things the reader saw and deliberately did not interpret, with what would
    #: resolve each. Not errors — the extraction succeeded.
    unread: list[dict[str, Any]]
    #: Fragments a reader classified as dimensions. **Candidate numbers and
    #: nothing more**: dimension and GD&T extraction is staged as later work and
    #: nothing turns one of these into a parameter automatically.
    dimensions: list[dict[str, Any]]


__all__ = [
    "AttachmentCreate",
    "AttachmentExtraction",
    "AttachmentPage",
    "AttachmentRead",
]
