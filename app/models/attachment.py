"""An attachment as a first-class object (P4 task 1).

The readers have existed since P4.2 and the injection boundary since P4.5. What
was missing is the row: *"every attachment is a first-class object: owner,
conversation link, extraction status, provenance"*. Without it an attachment was
a `Media` blob with no memory of why it was uploaded, so nothing could list what
a conversation had been given, nothing could say whether a file had been read
yet, and `SourceRef.attachment_id` — which exists and is documented as "None
until P4.1 has a table" — was always None.

**The blob is not duplicated.** `media_id` points at the content-addressed store,
so the same datasheet attached to four conversations is one blob and four rows.
That is the dedup P4.1 asks for, and it falls out of the store rather than being
implemented here.

**Extraction is a state, not a flag.** `PENDING`, `READY`, `UNSUPPORTED`,
`FAILED` — and `UNSUPPORTED` is deliberately not a kind of `FAILED`. A STEP file
attached to a conversation is not a broken upload; it is geometry, and the
message says so and names what to do with it instead. Grouping the two would put
a working action into a failure count and would tell a user their file was
corrupt.

**`extracted` holds what the reader produced, not the file.** Fragments and their
locators, so a citation can be rendered without re-reading a blob that may be
large — and so a re-read after a reader upgrade is a *visible* change rather than
one that silently rewrites the citations in an old conversation.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import EnumText
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.media import Media
    from app.models.user import User


class ExtractionStatus(StrEnum):
    """Where the reading of one attachment got to.

    `UNSUPPORTED` is **not** a kind of `FAILED`, and the distinction is the
    product's rather than the code's. A failure is something that went wrong; an
    unsupported format is the system saying "this is not a document, here is
    what it is instead". Counting them together would report a correctly handled
    STEP upload as a broken one.
    """

    PENDING = "pending"
    READY = "ready"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"

    @property
    def settled(self) -> bool:
        return self is not ExtractionStatus.PENDING

    @property
    def readable(self) -> bool:
        """Is there extracted content to quote?"""
        return self is ExtractionStatus.READY


class Attachment(UUIDPrimaryKey, TimestampMixin, Base):
    """One file a user handed to a conversation, and what came of it."""

    __tablename__ = "attachments"
    __table_args__ = (
        # The list view: this conversation's attachments, newest first.
        Index("ix_attachments_conversation_created", "conversation_id", "created_at"),
        # "Has this blob been read before" — the dedup question, per owner.
        Index("ix_attachments_owner_media", "owner_id", "media_id"),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: Nullable: a file can be attached to a project before a conversation is
    #: chosen, and a conversation can be deleted without destroying the record
    #: that a document was read. `ondelete` is CASCADE for exactly the opposite
    #: reason to `AITokenUsage` — an attachment has no meaning outside the
    #: conversation it was handed to, whereas spend does.
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, default=None
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, default=None
    )
    #: RESTRICT, matching `GeometryVersion`: a blob under an attachment must not
    #: be deleted out from under the citations that point into it.
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id", ondelete="RESTRICT"), index=True)

    #: The name the *user* gave it, which is not `Media.filename` in general and
    #: is not the blob's name in the store. Untrusted text: never rendered raw.
    filename: Mapped[str] = mapped_column(String(512))
    #: What `app.documents.kinds.sniff` decided, by content and never by
    #: extension. Recorded so a puzzling extraction can be traced.
    detected_kind: Mapped[str] = mapped_column(String(32), default="unknown")
    detected_format: Mapped[str] = mapped_column(String(32), default="unknown")

    status: Mapped[ExtractionStatus] = mapped_column(
        EnumText(ExtractionStatus, 16), default=ExtractionStatus.PENDING
    )
    #: Why, when `status` is `UNSUPPORTED` or `FAILED`. Server-authored prose
    #: naming the next action, never the file's own bytes.
    status_detail: Mapped[str | None] = mapped_column(Text, default=None)
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    #: Which reader produced the content — `pypdf`, `ezdxf`, `docling`. Stored
    #: rather than re-derived, because the answer changes when a reader is
    #: upgraded and an old citation must keep saying who made it.
    reader: Mapped[str] = mapped_column(String(32), default="unknown")
    #: `Reliability`, as its value. `inferred` is the one that needs a human to
    #: confirm before anything is acted on — see `app/documents/provenance.py`,
    #: which owns the vocabulary.
    reliability: Mapped[str] = mapped_column(String(16), default="transcribed")

    #: `ExtractedDocument`, serialised: fragments with their locators, the
    #: unread parts, the reader's notes. Not the file — a citation must be
    #: renderable without re-reading a blob that may be large, and a re-read
    #: after a reader upgrade should be a visible change rather than one that
    #: silently rewrites old citations.
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)

    owner: Mapped["User"] = relationship()
    conversation: Mapped["Conversation | None"] = relationship()
    media: Mapped["Media"] = relationship(lazy="joined")

    @property
    def sha256(self) -> str:
        return self.media.sha256

    @property
    def size_bytes(self) -> int:
        return self.media.size_bytes

    @property
    def needs_confirmation(self) -> bool:
        """Should the product label this "unverified read" (P4 task 3)?

        True for an inferred read — OCR, or anything that guessed at characters
        rather than transcribing them. **A wrongly read tolerance is worse than
        an unread one**, so the label travels with the content rather than
        being a property of the screen that happens to show it.
        """
        return self.reliability == "inferred"
