from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import JSONB_compat as JSONB

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

    #: When it was put away, or None. An archived project is hidden from the default list and
    #: read-only in spirit, but nothing is deleted: its geometry, runs and conversations are
    #: exactly as they were, which is the whole difference from a delete (ROAD_TO_10 7.1).
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None, index=True)
    #: Free labels, lower-case, at most `MAX_TAGS` of `MAX_TAG_LENGTH` each, normalised by
    #: `app.core.projects.normalise_tags` (ROAD_TO_10 7.6). A list in a JSON column rather
    #: than a table because a tag has no identity of its own to join on.
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    #: The mission rung this project was started from (`app.design.missions`), or None. A
    #: record of where it came from; nothing reads it to decide what the project may do
    #: (ROAD_TO_10 7.7).
    template_key: Mapped[str | None] = mapped_column(String(64), default=None)

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


class ProjectStar(UUIDPrimaryKey, TimestampMixin, Base):
    """One user's star on one project.

    A star is **per person**: a project belongs to an organisation, and the project one
    colleague pins is not the one another wants at the top of their list. So it is a row
    keyed on the pair, never a column on `Project` (ROAD_TO_10 7.6).
    """

    __tablename__ = "project_stars"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uq_project_star_user_project"),
        Index("ix_project_stars_project", "project_id"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE", name="fk_project_stars_user"), index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_project_stars_project")
    )
