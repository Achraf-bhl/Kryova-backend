from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.media import Media
    from app.models.project import Project


class GeometryVersion(UUIDPrimaryKey, TimestampMixin, Base):
    """One uploaded CAD file. Each upload to a project is a new, immutable version.

    The file itself is a local `Media` blob; this row holds only what the API
    and the mesher need to know about it.
    """

    __tablename__ = "geometry_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version_number", name="uq_geometry_project_version"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[str] = mapped_column(ForeignKey("media.id", ondelete="RESTRICT"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(512))
    file_format: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text, default=None)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    project: Mapped["Project"] = relationship(back_populates="geometry_versions")
    media: Mapped["Media"] = relationship(lazy="joined")

    @property
    def size_bytes(self) -> int:
        return self.media.size_bytes

    @property
    def checksum_sha256(self) -> str:
        return self.media.sha256
