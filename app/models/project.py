from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.geometry import GeometryVersion
    from app.models.organisation import Organisation
    from app.models.simulation import SimulationJob
    from app.models.user import User


class Project(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    #: Who created it. Provenance, not permission -- since P2 the answer to
    #: "may this person open it" comes from `organisation_id` and the
    #: membership behind it, never from here.
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: The tenant that owns the work. NOT NULL in the database: an orphaned
    #: project is a project outside every RLS policy, which is to say visible
    #: to nobody and protected by nothing. Left nullable in Python only so the
    #: `before_flush` hook in `models/organisation.py` can fill it in for call
    #: sites that predate organisations.
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True, default=None
    )

    owner: Mapped["User"] = relationship(back_populates="projects")
    organisation: Mapped["Organisation"] = relationship(back_populates="projects")
    geometry_versions: Mapped[list["GeometryVersion"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="GeometryVersion.version_number",
    )
    simulations: Mapped[list["SimulationJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
