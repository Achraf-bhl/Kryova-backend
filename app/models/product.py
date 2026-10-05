"""A product's revision chain and its leases, in the database (ROAD_TO_10 9.4).

`app/assembly/locking.py` is an in-memory repository: correct inside one process and wrong
across two, because each worker would hold its own head and its own lease book and the
optimistic check would pass in both. These two tables are the shared state; `app/assembly/
store.py` serialises every read-modify-write on one product with a transaction-scoped
advisory lock, so the *same* `ProductRepository` rules run against rows instead of a list.

**The rows are the repository's, not a second model of it.** A revision stores the structure
verbatim (`ProductStructure.to_dict()`, which carries its own `format_version`) and its digest,
and the digest is recomputed on load and must agree: a stored document that has drifted from its
hash is refused, not served. Leases keep the repository's own semantics -- an expired lease stays
as history -- and the times are whatever clock the caller passed in. **Across processes that
clock must be shared** (epoch seconds from `time.time()`), because "the lease expired" compares
one process's `now` with another's `expires_at`.

`product_key` names a product within a project: a project can hold several (an assembly and a
sub-assembly), and the unique `(project, key, number)` index is what makes a lost update a
database error even if the lock were bypassed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKey
from app.models.types import JSONB_compat as JSONB


class ProductRevisionRow(UUIDPrimaryKey, TimestampMixin, Base):
    """One committed state of one product. Append-only."""

    __tablename__ = "product_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "product_key", "number", name="uq_product_revision_number"),
        Index("ix_product_revisions_product", "project_id", "product_key"),
    )

    #: Carried for row-level security, always the project's own organisation.
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    product_key: Mapped[str] = mapped_column(String(128))

    number: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    parent: Mapped[str | None] = mapped_column(String(64), default=None)
    author: Mapped[str] = mapped_column(String(255))
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[float | None] = mapped_column(Float, default=None)
    structure: Mapped[dict[str, Any]] = mapped_column(JSONB)
    change: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProductLeaseRow(UUIDPrimaryKey, TimestampMixin, Base):
    """One author's claim on one component of one product. At most one per component."""

    __tablename__ = "product_leases"
    __table_args__ = (
        UniqueConstraint("project_id", "product_key", "component", name="uq_product_lease"),
        Index("ix_product_leases_product", "project_id", "product_key"),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    product_key: Mapped[str] = mapped_column(String(128))

    component: Mapped[str] = mapped_column(String(255))
    holder: Mapped[str] = mapped_column(String(255))
    taken_at: Mapped[float] = mapped_column(Float)
    expires_at: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
