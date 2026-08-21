import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.user import User


class MediaKind(str, enum.Enum):
    CAD = "cad"
    MESH = "mesh"
    RESULT_FIELDS = "result_fields"
    VECTOR_INDEX = "vector_index"
    REPORT = "report"
    OTHER = "other"


class UploadStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABORTED = "aborted"


class Media(TimestampMixin, UUIDPrimaryKey, Base):
    """Metadata for one heavy file held on the local disk.

    The bytes never reach the database. `sha256` is the blob's address in the
    local store, and several rows may share one digest -- uploading the same
    part twice costs one copy on disk, not two.
    """

    __tablename__ = "media"
    __table_args__ = (
        # Deleting a row must not delete a blob another row still points at.
        Index("ix_media_sha256", "sha256"),
    )

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MediaKind] = mapped_column(
        Enum(MediaKind, native_enum=False, length=32), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    owner: Mapped["User"] = relationship()


class MediaUploadSession(TimestampMixin, UUIDPrimaryKey, Base):
    """A resumable chunked upload in progress.

    Large CAD files over a browser connection do not survive being sent as one
    request. Chunks land in a staging directory and are assembled into a blob
    only once every index has arrived, so a dropped connection costs the
    remaining chunks rather than the whole transfer.
    """

    __tablename__ = "media_upload_sessions"

    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[MediaKind] = mapped_column(Enum(MediaKind, native_enum=False, length=32))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus, native_enum=False, length=16),
        default=UploadStatus.IN_PROGRESS,
        index=True,
    )

    total_size_bytes: Mapped[int] = mapped_column(BigInteger)
    chunk_size: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    received_chunks: Mapped[list[int]] = mapped_column(JSONB, default=list)
    expected_sha256: Mapped[str | None] = mapped_column(String(64), default=None)

    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)

    media: Mapped["Media | None"] = relationship()

    @property
    def received_count(self) -> int:
        return len(self.received_chunks or [])

    @property
    def is_complete(self) -> bool:
        return self.received_count == self.total_chunks

    @property
    def missing_chunks(self) -> list[int]:
        received = set(self.received_chunks or [])
        return [index for index in range(self.total_chunks) if index not in received]
