"""Read-only share links, and the record of a project changing hands (P2.5).

Two capabilities that sound unrelated and are the same problem: **letting work
leave a tenant without leaving a hole in the tenant boundary.**

`ShareLink` is how a supplier or a customer sees a design package without an
account. `ProjectTransfer` is how a project moves between organisations with its
history intact rather than being copied.

**A share link is a credential, so it is stored hashed** — the same rule as
refresh tokens, reset tokens and recovery codes. A link readable in the database
is a link every operator and every backup can open.

**A share link is scoped to exactly one project and can never widen.** It
carries no user, no membership and no organisation: the read path resolves the
token to one `project_id` and reads that, so a bug in it cannot become a
cross-tenant read the way a bug in a membership check can. That is deliberate —
this is the one path in the service that answers without a principal, so it is
built to have the least possible reach rather than to be carefully guarded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey
from app.models.types import EnumText

if TYPE_CHECKING:
    from app.models.organisation import Organisation
    from app.models.project import Project
    from app.models.user import User


class ShareRevocation(StrEnum):
    """Why a link stopped working. Kept for the same reason `SessionRevocation`
    is: "revoked" alone cannot be acted on, and "the customer's link expired"
    and "we pulled it after a leak" need different responses."""

    REVOKED = "revoked"
    PROJECT_DELETED = "project_deleted"
    TRANSFERRED = "transferred"


class ShareLink(UUIDPrimaryKey, TimestampMixin, Base):
    """One expiring, revocable, read-only view of one project."""

    __tablename__ = "share_links"
    __table_args__ = (
        Index("ix_share_links_project_live", "project_id", "revoked_at"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    #: The tenant that issued it, denormalised from the project deliberately.
    #: A transfer moves the project to another organisation, and a link issued
    #: by the *previous* owner must stop working rather than silently become a
    #: window into the new one. `is_live` compares the two.
    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    #: SHA-256 of the raw token. The raw value is returned exactly once, at
    #: creation, and is not recoverable — regenerate rather than look up.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    #: Shown to whoever opens the link, so a recipient knows what they are
    #: looking at and who sent it. Free text from the issuer.
    label: Mapped[str | None] = mapped_column(String(255), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)

    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    #: `EnumText`, not `String(32)`: a bare string column hands the enum back as
    #: a plain `str` for any row loaded from the database, so `revocation is
    #: ShareRevocation.X` silently changes answer depending on whether the row
    #: came from the identity map or a SELECT. See `models/types.EnumText` —
    #: this is the third column in the codebase to have carried that bug.
    revocation: Mapped[ShareRevocation | None] = mapped_column(
        EnumText(ShareRevocation, 32), default=None
    )

    #: Whether the recipient may download the CAD itself, as opposed to reading
    #: the results and the summary. **Off by default**: sending somebody a
    #: picture of your stress field and sending them your geometry are different
    #: decisions, and the second one should be taken on purpose. A supplier
    #: quoting a part needs it; a customer reviewing a report does not.
    allow_geometry_download: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Counted rather than logged per hit: the useful question an issuer asks is
    #: "has anyone opened this yet", and a row per view of a public link is an
    #: unbounded write from an unauthenticated route.
    view_count: Mapped[int] = mapped_column(default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    project: Mapped["Project"] = relationship()
    organisation: Mapped["Organisation"] = relationship()
    created_by: Mapped["User"] = relationship()

    def is_live(self, now: datetime | None = None) -> bool:
        """Whether this link should open. Three ways it may not.

        Takes `now` so expiry is testable without waiting, the rule the rest of
        this codebase follows (`assembly/locking.py`, `core/totp.py`).
        """
        moment = now or datetime.now(timezone.utc)
        if self.revoked_at is not None:
            return False
        if moment >= self.expires_at:
            return False
        # A project that has moved tenants since the link was issued. The check
        # is here rather than in the read path so no caller can forget it.
        return self.project is None or self.project.organisation_id == self.organisation_id

    def revoke(self, reason: ShareRevocation, *, now: datetime | None = None) -> None:
        if self.revoked_at is None:
            self.revoked_at = now or datetime.now(timezone.utc)
            self.revocation = reason


class ProjectTransfer(UUIDPrimaryKey, TimestampMixin, Base):
    """A project moving between organisations, kept rather than merely done.

    The row exists because a transfer is the one operation that changes who can
    see a body of work, and "this project used to belong to Acme" is a question
    somebody will eventually ask under pressure. The audit log (P3.1) records
    the *action*; this records the *provenance*, and unlike the audit log it
    travels with the project.
    """

    __tablename__ = "project_transfers"
    __table_args__ = (
        Index("ix_project_transfers_project_time", "project_id", "created_at"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    from_organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE")
    )
    to_organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE")
    )
    #: Who moved it. Not nullable: an unattributed transfer is exactly the kind
    #: of event this table exists to attribute.
    transferred_by_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str | None] = mapped_column(Text, default=None)

    project: Mapped["Project"] = relationship()


__all__ = ["ProjectTransfer", "ShareLink", "ShareRevocation"]
