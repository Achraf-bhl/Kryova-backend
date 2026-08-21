from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.geometry import GeometryVersion
    from app.models.simulation import SimulationJob
    from app.models.user import User


class Project(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    owner: Mapped["User"] = relationship(back_populates="projects")
    geometry_versions: Mapped[list["GeometryVersion"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="GeometryVersion.version_number",
    )
    # SQLite does not enforce ON DELETE CASCADE unless foreign keys are switched
    # on per connection, so the ORM owns the cascade instead.
    simulations: Mapped[list["SimulationJob"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
