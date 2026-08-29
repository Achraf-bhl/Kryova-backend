"""The CATIA desktop bridge's database rows.

Four tables, each earning its place:

* `CatiaDevice` -- one paired Windows workstation. The device token is a
  long-lived credential, so only its SHA-256 is stored: a database leak must not
  hand an attacker a live connection into an engineer's CAD session.
* `CatiaDocument` -- the conversation-to-document binding that makes "come back
  tomorrow and keep building" work. One document per conversation, enforced by a
  unique constraint rather than by convention.
* `CatiaCheckpoint` -- a snapshot of the document taken before every mutating
  operation. CATIA's in-session undo is not a safety net for an agent: it is
  lost on close, it is not addressable, and it cannot be replayed.
* `CatiaOperation` -- an append-only log of every call, its arguments and its
  result. This is the defence against silent model corruption discovered weeks
  later, and it doubles as a replayable script of how a part was built.

See docs/CATIA_BRIDGE_PROTOCOL.md for the wire format these rows describe.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey, utcnow
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.media import Media
    from app.models.user import User


class CatiaDeviceStatus(StrEnum):
    #: Created in the UI, pairing code issued, daemon has not redeemed it yet.
    PENDING = "pending"
    #: Paired. Holds a device token and may open a bridge WebSocket.
    ACTIVE = "active"
    #: Revoked by the user. The token is dead; the row is kept so the operation
    #: log still resolves the device that performed historical work.
    REVOKED = "revoked"


class CatiaDeviceStatusType(TypeDecorator):
    """Round-trip `CatiaDeviceStatus` as the enum, not as a bare string.

    Annotating a column `Mapped[CatiaDeviceStatus]` over a plain `String` looks
    like it types the attribute, and on a freshly constructed object it does --
    but a row loaded back from Postgres hands you `'active'`, a `str`. Because
    `StrEnum` compares equal to its value, `==` keeps working and the problem
    stays invisible, right up until something uses `is`:

        if device.status is not CatiaDeviceStatus.ACTIVE:  # always true!
            return None

    That is the WebSocket authentication check, and with a plain column it would
    have refused every device that had ever been through the database. Storage
    stays `VARCHAR(16)` -- this changes no DDL, only what comes back out.
    """

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        return CatiaDeviceStatus(value).value

    def process_result_value(self, value, dialect) -> CatiaDeviceStatus | None:  # noqa: ANN001
        if value is None:
            return None
        return CatiaDeviceStatus(value)


class CatiaDevice(UUIDPrimaryKey, TimestampMixin, Base):
    """One paired Windows workstation running the bridge daemon.

    A device belongs to exactly one user for the lifetime of the row. Tool calls
    are routed only to devices owned by the requesting user -- the same
    404-not-403 posture as the rest of the API, applied to hardware.
    """

    __tablename__ = "catia_devices"
    __table_args__ = (
        # The bridge authenticates by presenting a token; the server hashes it
        # and looks the hash up. That lookup is on the hot path of every
        # reconnect, and reconnects are frequent on a laptop that sleeps.
        Index("ix_catia_devices_token_hash", "token_hash", unique=True),
        # Redeeming a pairing code is an unauthenticated lookup by code alone,
        # so it must not be a table scan.
        Index("ix_catia_devices_pairing_code", "pairing_code", unique=True),
        Index("ix_catia_devices_owner_status", "owner_id", "status"),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: What the user called it in the UI ("Workstation, office").
    name: Mapped[str] = mapped_column(String(120))
    #: Reported by the daemon in its `hello` frame. Advisory only -- it is
    #: attacker-controlled in the same way any client-supplied string is, and is
    #: sanitised before it reaches a prompt or a page.
    hostname: Mapped[str | None] = mapped_column(String(255), default=None)

    #: SHA-256 of the device token, never the token. Nullable until pairing.
    token_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    token_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    #: Single-use, short-lived, cleared the moment it is redeemed. A code that
    #: survived redemption would be a second, weaker path to a device token.
    pairing_code: Mapped[str | None] = mapped_column(String(16), default=None)
    pairing_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    catia_version: Mapped[str | None] = mapped_column(String(64), default=None)
    bridge_version: Mapped[str | None] = mapped_column(String(32), default=None)
    #: True when the daemon reported itself running without CATIA (`--mock`).
    #: Surfaced to the UI so nobody mistakes a simulated part for a real one.
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)

    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    status: Mapped[CatiaDeviceStatus] = mapped_column(
        CatiaDeviceStatusType, default=CatiaDeviceStatus.PENDING
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    owner: Mapped["User"] = relationship()


class CatiaDocument(UUIDPrimaryKey, TimestampMixin, Base):
    """The CATIA document bound to one conversation.

    `conversation_id` is unique: a conversation owns at most one document. That
    is the product mechanic, not an implementation detail -- it is what lets the
    agent say "open the part we were working on" without the user naming a file.

    `remote_path` is opaque here on purpose. The daemon resolves every path
    inside its own working directory and the model never sees or supplies one;
    this column exists so the daemon can be told which of *its* files to reopen,
    and for no other reason.
    """

    __tablename__ = "catia_documents"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_catia_document_conversation"),
        Index("ix_catia_documents_device_id", "device_id"),
    )

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: The device the document lives on. SET NULL rather than CASCADE: revoking
    #: a laptop must not erase the record of what was built on it.
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("catia_devices.id", ondelete="SET NULL"), default=None
    )
    doc_name: Mapped[str] = mapped_column(String(255))
    remote_path: Mapped[str | None] = mapped_column(Text, default=None)
    latest_checkpoint_id: Mapped[str | None] = mapped_column(String(36), default=None)

    conversation: Mapped["Conversation"] = relationship()
    device: Mapped["CatiaDevice | None"] = relationship()


class CatiaCheckpoint(UUIDPrimaryKey, Base):
    """A snapshot of a document, taken before it was mutated.

    Two independent copies exist and either can restore the part:

    * `media_id` -- the document's bytes in the content-addressed blob store,
      shipped up over the bridge. This is the copy that survives the engineer's
      laptop being reimaged.
    * `remote_ref` -- the daemon's own snapshot file, kept in its working
      directory. This is the copy that is used when the document is larger than
      the inline transfer ceiling, and it is why `media_id` is nullable: a
      200 MB assembly must still get a checkpoint, and a checkpoint that only
      half-exists is better recorded honestly than not recorded at all.

    Append-only, hence `created_at` alone: an `updated_at` on a row nothing ever
    updates would be a claim the code does not honour.
    """

    __tablename__ = "catia_checkpoints"
    __table_args__ = (Index("ix_catia_checkpoints_document_created", "document_id", "created_at"),)

    document_id: Mapped[str] = mapped_column(
        ForeignKey("catia_documents.id", ondelete="CASCADE"), index=True
    )
    #: RESTRICT, matching `geometry_versions.media_id`: deleting a blob a
    #: checkpoint still points at must fail loudly rather than orphan the row.
    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media.id", ondelete="RESTRICT"), default=None, index=True
    )
    #: SHA-256 of the document as the daemon hashed it. Present even when the
    #: bytes were too large to ship, so a later upload can be matched to it.
    digest: Mapped[str | None] = mapped_column(String(64), default=None)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    #: The daemon-side snapshot identifier, opaque to the server.
    remote_ref: Mapped[str | None] = mapped_column(Text, default=None)
    #: Why the checkpoint was taken -- "before catia_pocket", or a user label.
    label: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    document: Mapped["CatiaDocument"] = relationship()
    media: Mapped["Media | None"] = relationship()


class CatiaOperation(UUIDPrimaryKey, Base):
    """Append-only audit log: every tool call that reached a device.

    Written whether the call succeeded, failed, timed out or was refused before
    it ever left the server, because the interesting question after the fact is
    usually "what did it *try* to do". The rows are a replayable transcript of
    how a part was built, and the only way to answer "when did this dimension
    change, and which turn changed it" weeks later.

    `arguments` and `result` are stored as given. They are bounded before they
    land here -- see `app.catia.dispatch` -- because a screenshot or a STEP body
    has no business in a log table.
    """

    __tablename__ = "catia_operations"
    __table_args__ = (
        Index("ix_catia_operations_conversation_created", "conversation_id", "created_at"),
        Index("ix_catia_operations_device_created", "device_id", "created_at"),
    )

    #: Nullable: `catia_status` and device-level calls are not tied to a chat.
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    #: SET NULL on device deletion -- the audit trail outlives the hardware.
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("catia_devices.id", ondelete="SET NULL"), default=None
    )
    #: Denormalised so the log is still attributable after a conversation or a
    #: device row is gone. Audit rows that lose their subject are worthless.
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True
    )
    tool: Mapped[str] = mapped_column(String(64), index=True)
    tier: Mapped[str] = mapped_column(String(16))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
