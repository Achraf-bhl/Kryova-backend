import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import JSONB_compat as JSONB

if TYPE_CHECKING:
    from app.models.geometry import GeometryVersion
    from app.models.media import Media
    from app.models.project import Project


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED)


class SimulationJob(UUIDPrimaryKey, TimestampMixin, Base):
    """One mesh-and-solve run against a specific geometry version.

    The summary lands here; the full displacement and stress fields are far too
    large for a row and live on local disk as a `Media` blob.
    """

    __tablename__ = "simulation_jobs"
    __table_args__ = (Index("ix_simulation_project_created", "project_id", "created_at"),)

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    geometry_version_id: Mapped[str] = mapped_column(
        ForeignKey("geometry_versions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=16), default=JobStatus.QUEUED, index=True
    )
    solver: Mapped[str] = mapped_column(String(64))

    load_case: Mapped[dict[str, Any]] = mapped_column(JSONB)
    element_size_mm: Mapped[float | None] = mapped_column(Float, default=None)
    # 1 = tet4, 2 = tet10. Stored rather than derived because the mesh it
    # produced is not kept, and a result is only reproducible alongside the
    # element order that computed it.
    element_order: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    mesh_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    fields_media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    project: Mapped["Project"] = relationship(back_populates="simulations")
    geometry_version: Mapped["GeometryVersion"] = relationship()
    fields_media: Mapped["Media | None"] = relationship(lazy="joined")
