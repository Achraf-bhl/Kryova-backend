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
from datetime import datetime, timezone
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
from sqlalchemy import inspect as sa_inspect
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

#: Stand-in for the `created_at` of a row that has not been flushed yet, so a
#: pending organisation can be sorted beside stored ones without comparing
#: `None` to a datetime (which raises rather than ordering).
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def known_memberships(session: Session, user: "User") -> list["Membership"] | None:
    """This user's membership rows if the session already holds them, else None.

    `None` means "ask the database" -- it is *not* "no memberships". Every
    caller below distinguishes the two, because confusing them turns a
    cold-cache read into a cross-tenant refusal.

    Two sources, and both are needed for the answer to be as good as a query's.
    The loaded relationship is the flushed state; `session.new` is the part of
    this unit of work that has not reached the database yet -- the personal
    organisation created a few lines below is exactly that, so without the
    second half a caller in the same flush would fail to see the membership it
    just asked for.
    """
    state = sa_inspect(user, raiseerr=False)
    if state is None or state.detached or "memberships" in state.unloaded:
        return None
    known = list(user.memberships)
    known.extend(
        pending
        for pending in session.new
        if isinstance(pending, Membership)
        and pending.user_id == user.id
        and pending not in known
    )
    return known


def _personal_from_session(
    session: Session, user: "User"
) -> tuple[Organisation | None, bool]:
    """(the personal organisation, whether the session could answer at all).

    A `True` second element with a `None` first is a real answer -- the session
    holds every membership this user has and none of them is personal -- so the
    caller may create one without a confirming SELECT. That is the whole point:
    it removes a round trip from the first project a user ever creates, and a
    different round trip from every one after it.
    """
    memberships = known_memberships(session, user)
    if memberships is None:
        return None, False

    personal: list[Organisation] = []
    for membership in memberships:
        membership_state = sa_inspect(membership, raiseerr=False)
        if membership_state is None or "organisation" in membership_state.unloaded:
            # One unloaded organisation is enough to make the session's answer
            # incomplete: the row it would have named could be the personal one.
            return None, False
        organisation = membership.organisation
        if organisation is not None and organisation.is_personal:
            personal.append(organisation)
    if not personal:
        return None, True
    # Same tie-break as the query below. A pending organisation has no
    # `created_at` yet, and sorts first -- which is the right answer anyway,
    # since it is the one this unit of work just made.
    personal.sort(
        key=lambda org: (org.created_at is not None, org.created_at or _EPOCH, org.id or "")
    )
    return personal[0], True


def personal_organisation(session: Session, user: "User") -> Organisation:
    """The user's own organisation, created on first need.

    Registration does not create one, on purpose: the account and the tenancy
    model are owned by different phases, and an account that has never made
    anything needs no tenant. Everything that *creates* work goes through here.

    The session is asked before the database is. Authentication eager-loads the
    caller's memberships and their organisations (`app/api/deps.py`), so on the
    request path this normally answers with no query at all -- which was worth
    doing because this runs inside `before_flush` on the way to *every* project
    insert in the service.
    """
    existing, session_knows = _personal_from_session(session, user)
    if existing is not None:
        return existing
    if not session_knows:
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


def membership_for_user(
    session: Session, user: "User", organisation_id: str
) -> Membership | None:
    """`membership_for`, answered from the session when it can be.

    Same answer, one fewer round trip on every authenticated request that
    guards a project or an organisation. Safe because the session's copy is not
    a cache with its own lifetime: it is the identity map of the transaction
    asking the question, and `known_memberships` folds in the rows this unit of
    work has added but not yet flushed. When the collection is not loaded this
    falls through to the query, so a caller holding a `User` from anywhere else
    is unaffected.
    """
    memberships = known_memberships(session, user)
    if memberships is None:
        return membership_for(session, user.id, organisation_id)
    for membership in memberships:
        if membership.organisation_id == organisation_id:
            return membership
    return None


def organisation_ids_for(session: Session, user_id: str) -> list[str]:
    """Every tenant this user belongs to -- the value RLS is fed with."""
    return list(
        session.scalars(
            select(Membership.organisation_id)
            .where(Membership.user_id == user_id)
            .order_by(Membership.organisation_id)
        )
    )


def organisation_ids_for_user(session: Session, user: "User") -> list[str]:
    """`organisation_ids_for`, answered from the session when it can be.

    Authentication needs this value on every request to publish the RLS tenant
    context, and it used to cost a round trip of its own beside the one that
    fetched the user. Eager-loading the memberships with the user makes it free.
    """
    memberships = known_memberships(session, user)
    if memberships is None:
        return organisation_ids_for(session, user.id)
    return sorted({membership.organisation_id for membership in memberships})


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
