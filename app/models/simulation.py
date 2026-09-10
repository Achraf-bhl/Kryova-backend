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
    #: Stopped on request (P5 task 6). **Not a kind of `FAILED`**, and every
    #: surface that groups the two is wrong: a failure is the product not
    #: working and belongs in the fleet's failure rate, while a cancellation is
    #: the product doing exactly what it was told. Counting them together would
    #: make a user who changes their mind twice look like an incident.
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED)


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
    #: The solver that ran. Written by the *runner* from `solver.name` once the
    #: solve is over, not by the route when the job was queued — a row naming a
    #: solver nobody consulted is provenance in name only.
    solver: Mapped[str] = mapped_column(String(64))
    #: Its version, when it can be read. `None` is an honest answer and is stored
    #: as one: Decision 3 binds a result to what produced it, and a stress figure
    #: whose provenance says only "calculix" cannot be reproduced in two years.
    solver_version: Mapped[str | None] = mapped_column(String(64), default=None)

    #: The mechanical case: fixtures, loads, material. **Nullable since
    #: 2026-09-09**, because a steady-conduction run genuinely has none — no
    #: fixture, no force, and a conductivity where a modulus would be. Storing an
    #: empty `LoadCase` on such a row would have put a material and a set of
    #: fixtures nobody chose into the provenance of a temperature field.
    load_case: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    #: The thermal case for a `thermal-conduction` run: conductivity, boundaries
    #: and any volumetric source. A sibling column rather than a widened
    #: `load_case`, for the reason `ThermalCase` is a sibling of `LoadCase` —
    #: folding them together invites a reader to believe the mechanical loads
    #: influenced the temperature.
    thermal_case: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    element_size_mm: Mapped[float | None] = mapped_column(Float, default=None)
    # 1 = tet4, 2 = tet10. Stored rather than derived because the mesh it
    # produced is not kept, and a result is only reproducible alongside the
    # element order that computed it.
    element_order: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: Which idealisation was solved: `solid`, `plane-stress`, `plane-strain` or
    #: `thermal-conduction`.
    #: Stored for the same reason `element_order` is — the mesh is not kept, and
    #: plane stress and plane strain give *different answers on the same mesh and
    #: the same load*, so a result whose row does not say which one ran cannot be
    #: reproduced or even argued with. `solid` is the server default, so every
    #: row written before this column existed keeps the meaning it had.
    analysis: Mapped[str] = mapped_column(
        String(32), default="solid", server_default="solid"
    )
    #: How many successively finer grids to solve. 1 is one mesh and the
    #: default, which is what every run before this column did. 3 or more turns
    #: the run into a **convergence study**: the same case solved on each grid
    #: and the peak stress assessed with a Grid Convergence Index, so the result
    #: can say how far the answer would move on a finer mesh instead of saying
    #: nothing. Stored because the meshes are not kept and a number is only
    #: reproducible alongside the evidence that was gathered for it.
    grids: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: Out-of-plane thickness in mm for a plane analysis; None for a solid, where
    #: the geometry carries its own thickness. Required by the plane solver and
    #: refused at the route rather than defaulted, because a thickness nobody
    #: chose scales every stress in the run.
    thickness_mm: Mapped[float | None] = mapped_column(Float, default=None)

    mesh_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    fields_media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media.id", ondelete="SET NULL"), default=None
    )
    error: Mapped[str | None] = mapped_column(Text, default=None)

    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    #: When somebody asked this run to stop, and who (P5 task 6). Kept on a
    #: `CANCELLED` row rather than inferred from the status, because "stopped by
    #: Achraf at 14:02" is the fact somebody wants three weeks later and the
    #: status alone cannot supply it.
    #:
    #: Set on a **running** job too, where it is a request rather than an
    #: outcome: the runner reads it at its next stage boundary. See
    #: `app/core/interruption.py` for why that boundary is the honest one.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    cancel_requested_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    project: Mapped["Project"] = relationship(back_populates="simulations")
    geometry_version: Mapped["GeometryVersion"] = relationship()
    fields_media: Mapped["Media | None"] = relationship(lazy="joined")
