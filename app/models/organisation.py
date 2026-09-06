"""Organisations, memberships and invitations -- the tenant boundary (P2.1).

An organisation is the thing that *owns* work. A user is who did it. Before
this, `Project.owner_id` was both, which meant there was exactly one way for a
project to be shared: not at all.

`Project.owner_id` survives and still means the person who created it -- that is
provenance, and provenance is not a permission. Authorisation now reads
`Project.organisation_id` and the membership behind it, in one place
(`app/api/deps.py`), so a new route cannot invent a second answer.

**Every user has a personal organisation**, created on demand, so there is
exactly one ownership model rather than "org projects" and "personal projects"
as separate cases with separate bugs. `_assign_owning_organisation` below is
what makes that structural: any code path that constructs a `Project` -- a
route, the agent's `create_project` tool, a future importer -- gets an owning
organisation whether it knew about organisations or not. A migration that
leaves rows orphaned cannot be deployed; neither can a code path that creates
them.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UTCDateTime, UUIDPrimaryKey, new_uuid
from app.models.project import Project

if TYPE_CHECKING:
    from app.models.user import User


class OrgRole(str, enum.Enum):
    """What a member may do to the organisation itself (P2.2, platform roles).

    Ordered, and the ordering is the whole permission model: every check in the
    codebase is "at least this role", never a set membership test, so adding a
    role later cannot leave one route comparing against a list that another
    route forgot to update.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def at_least(self, required: "OrgRole") -> bool:
        return self.rank >= required.rank


_ROLE_RANK: dict[OrgRole, int] = {
    OrgRole.VIEWER: 0,
    OrgRole.MEMBER: 1,
    OrgRole.ADMIN: 2,
    OrgRole.OWNER: 3,
}


class DomainRole(str, enum.Enum):
    """What a member may do to the *engineering* work (P2.2, domain roles).

    Deliberately a separate axis from `OrgRole`: an organisation owner is not
    automatically a reviewer, because a review sign-off from someone without
    `REVIEWER` is not a sign-off (16.5/P5 read this column). Nullable on
    `Membership` until the approval gates exist -- a column that means "not
    stated" is honest; one defaulted to `ENGINEER` would silently qualify
    everybody.
    """

    ENGINEER = "engineer"
    REVIEWER = "reviewer"
    OPERATOR = "operator"


def _role_enum(enum_type: type[enum.Enum], length: int) -> Enum:
    """Store the lowercase *values*, not the member names.

    SQLAlchemy stores `.name` by default, so the column would hold `OWNER`
    while every API payload, RLS predicate and hand-written migration says
    `owner`. One spelling, everywhere.
    """
    return Enum(
        enum_type,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
    )


class Organisation(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "organisations"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    #: A personal organisation is created for a user rather than by one. It is
    #: not a different *kind* of tenant -- every rule applies to it identically
    #: -- it is a flag so the UI can avoid showing someone a member list of one.
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )
    projects: Mapped[list[Project]] = relationship(back_populates="organisation")


class Membership(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organisation_id", "user_id", name="uq_membership_org_user"),
        Index("ix_memberships_user_org", "user_id", "organisation_id"),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[OrgRole] = mapped_column(_role_enum(OrgRole, 16), default=OrgRole.MEMBER)
    domain_role: Mapped[DomainRole | None] = mapped_column(
        _role_enum(DomainRole, 16), default=None
    )

    organisation: Mapped[Organisation] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")


class OrganisationInvitation(UUIDPrimaryKey, TimestampMixin, Base):
    """An offer of membership, addressed to an email, redeemable once.

    Only the SHA-256 of the token is stored, exactly as the password-reset flow
    does (`auth.request_password_reset`): a database read must not yield a
    working credential. The raw token exists in one response and in the
    invitee's mail, and nowhere else.
    """

    __tablename__ = "organisation_invitations"
    __table_args__ = (
        Index("ix_org_invitations_org_email", "organisation_id", "email"),
    )

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("organisations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[OrgRole] = mapped_column(_role_enum(OrgRole, 16), default=OrgRole.MEMBER)
    domain_role: Mapped[DomainRole | None] = mapped_column(
        _role_enum(DomainRole, 16), default=None
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    #: SET NULL, not CASCADE: deleting the account that invited someone must not
    #: erase the record that the invitation happened.
    invited_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    accepted_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)

    organisation: Mapped[Organisation] = relationship()

    def is_open(self, now: datetime) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > now


_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def personal_slug(user: "User") -> str:
    """A stable, unique slug for a user's own organisation.

    The user id suffix is what guarantees uniqueness; the readable prefix is a
    courtesy. Kept identical in shape to the backfill in the P2 migration so a
    row created today and a row backfilled yesterday look the same.
    """
    # The id has to be *materialised* here, not read. `UUIDPrimaryKey` supplies
    # it with a Python-side `default=`, which SQLAlchemy applies during the
    # flush — and this function is called from `before_flush`, which is
    # necessarily earlier. So a brand-new `User` still has `id is None`, and
    # reading it raised `AttributeError: 'NoneType' object has no attribute
    # 'replace'` for every user created inside a single flush.
    #
    # Assigning the id is safe and is not a second source of truth: it is the
    # same `new_uuid()` the column default would have called moments later, and
    # doing it now means the slug refers to the id the row actually gets.
    if user.id is None:
        user.id = new_uuid()
    local = _SLUG_UNSAFE.sub("-", (user.email or "").lower().split("@")[0]).strip("-")
    return f"{local[:32] or 'user'}-{user.id.replace('-', '')[:8]}"


def personal_organisation(session: Session, user: "User") -> Organisation:
    """The user's own organisation, created on first need.

    Registration does not create one, on purpose: the account and the tenancy
    model are owned by different phases, and an account that has never made
    anything needs no tenant. Everything that *creates* work goes through here.
    """
    existing = session.scalar(
        select(Organisation)
        .join(Membership, Membership.organisation_id == Organisation.id)
        .where(
            Membership.user_id == user.id,
            Organisation.is_personal.is_(True),
        )
        .order_by(Organisation.created_at, Organisation.id)
        .limit(1)
    )
    if existing is not None:
        return existing

    organisation = Organisation(
        name=(user.full_name or (user.email or "").split("@")[0] or "Personal"),
        slug=personal_slug(user),
        is_personal=True,
    )
    session.add(organisation)
    session.add(
        Membership(
            organisation=organisation,
            user_id=user.id,
            role=OrgRole.OWNER,
            domain_role=DomainRole.ENGINEER,
        )
    )
    return organisation


def membership_for(session: Session, user_id: str, organisation_id: str) -> Membership | None:
    return session.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.organisation_id == organisation_id,
        )
    )


def organisation_ids_for(session: Session, user_id: str) -> list[str]:
    """Every tenant this user belongs to -- the value RLS is fed with."""
    return list(
        session.scalars(
            select(Membership.organisation_id)
            .where(Membership.user_id == user_id)
            .order_by(Membership.organisation_id)
        )
    )


@event.listens_for(Session, "before_flush")
def _assign_owning_organisation(session: Session, _context: Any, _instances: Any) -> None:
    """Give every new project an owning organisation, whoever built it.

    `organisation_id` is NOT NULL in the database, and several call sites
    outside this package construct `Project` directly (the projects route, the
    agent's `create_project` tool). Requiring each of them to remember the
    tenant is how one of them eventually forgets and the insert fails in
    production. Resolving it here means the guarantee is a property of the
    model, not of four call sites agreeing.

    `before_flush` is the sanctioned place to add objects to a session: the
    personal organisation and its membership created here are flushed in the
    same unit of work as the project that needed them.
    """
    from app.models.user import User  # local: user.py imports this module's types

    pending = [
        obj
        for obj in session.new
        if isinstance(obj, Project) and obj.organisation_id is None
    ]
    if not pending:
        return

    for project in pending:
        owner = project.owner
        if owner is None and project.owner_id is not None:
            owner = session.get(User, project.owner_id)
        if owner is None:
            # Nothing to resolve from. Let the NOT NULL constraint speak: a
            # project with neither an organisation nor an owner is not a
            # project this system can place, and inventing a tenant for it
            # would put the row somewhere nobody asked for.
            continue
        project.organisation = personal_organisation(session, owner)
