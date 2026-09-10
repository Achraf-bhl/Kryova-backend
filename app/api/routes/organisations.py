"""Organisations, members and invitations (P2.1, P2.2, P2.4).

Every authorisation decision here comes from `api/deps.py`; nothing in this
module reads a membership row to decide what a caller may do. A miss is a 404
on every route -- including "you are in this organisation but only as a
viewer", because a 403 there still confirms which ids exist.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app import mail
from app.api.deps import (
    AdminOrganisation,
    CurrentUser,
    DbSession,
    ViewerOrganisation,
)
from app.core.config import settings
from app.core.security import hash_token
from app.models import (
    Membership,
    Organisation,
    OrganisationInvitation,
    OrgRole,
    Project,
    User,
)
from app.models.organisation import DomainRole, membership_for
from app.schemas.organisation import (
    InvitationAccept,
    InvitationCreate,
    InvitationIssued,
    InvitationRead,
    MemberRead,
    MemberUpdate,
    OrganisationCreate,
    OrganisationMembershipRead,
    OrganisationRead,
    OrganisationUpdate,
)
from app.schemas.pagination import Page, ProjectPage
from app.schemas.project import ProjectRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/organisations", tags=["organisations"])

INVITATION_TTL_HOURS = 72

MemberPage = Page[MemberRead]
InvitationPage = Page[InvitationRead]


def _membership_view(organisation: Organisation, membership: Membership) -> dict:
    return {
        **OrganisationRead.model_validate(organisation).model_dump(),
        "role": membership.role,
        "domain_role": membership.domain_role,
    }


def _slugify(name: str, user_id: str) -> str:
    base = "".join(character if character.isalnum() else "-" for character in name.lower())
    base = "-".join(part for part in base.split("-") if part)[:32] or "org"
    return f"{base}-{user_id.replace('-', '')[:8]}"


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------


@router.post("", response_model=OrganisationMembershipRead, status_code=status.HTTP_201_CREATED)
def create_organisation(
    payload: OrganisationCreate, db: DbSession, current_user: CurrentUser
) -> dict:
    slug = payload.slug or _slugify(payload.name, current_user.id)
    if db.scalar(select(Organisation).where(Organisation.slug == slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"The slug '{slug}' is taken. Choose another, or leave it out to get one.",
        )
    organisation = Organisation(name=payload.name, slug=slug, is_personal=False)
    membership = Membership(
        organisation=organisation,
        user_id=current_user.id,
        role=OrgRole.OWNER,
        domain_role=DomainRole.ENGINEER,
    )
    db.add(organisation)
    db.add(membership)
    db.commit()
    return _membership_view(organisation, membership)


@router.get("", response_model=Page[OrganisationMembershipRead])
def list_organisations(
    db: DbSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[OrganisationMembershipRead]:
    base = (
        select(Organisation, Membership)
        .join(Membership, Membership.organisation_id == Organisation.id)
        .where(Membership.user_id == current_user.id)
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.user_id == current_user.id)
        )
        or 0
    )
    rows = db.execute(
        base.order_by(Organisation.created_at, Organisation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[OrganisationMembershipRead](
        total=total,
        page=page,
        page_size=page_size,
        items=[
            OrganisationMembershipRead.model_validate(_membership_view(org, membership))
            for org, membership in rows
        ],
    )


# Declared before `/{organisation_id}`: a literal segment registered after a
# path parameter is never reached, and "accept" would be looked up as an id.
@router.post("/invitations/accept", response_model=OrganisationMembershipRead)
def accept_invitation(
    payload: InvitationAccept, db: DbSession, current_user: CurrentUser
) -> dict:
    """Redeem an invitation token for a membership.

    The token is looked up by hash, exactly as `confirm_password_reset` does.
    Every failure -- unknown, expired, revoked, already used, addressed to
    someone else -- is the same 422 with the same wording: a distinguishable
    error here tells an attacker which organisations exist and who was invited.
    """
    invitation = db.scalar(
        select(OrganisationInvitation).where(
            OrganisationInvitation.token_hash == hash_token(payload.token)
        )
    )
    now = datetime.now(timezone.utc)
    rejected = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="This invitation is not valid. Ask for a new one.",
    )
    if invitation is None or not invitation.is_open(now):
        raise rejected
    if invitation.email.lower() != (current_user.email or "").lower():
        raise rejected

    existing = membership_for(db, current_user.id, invitation.organisation_id)
    if existing is None:
        membership = Membership(
            organisation_id=invitation.organisation_id,
            user_id=current_user.id,
            role=invitation.role,
            domain_role=invitation.domain_role,
        )
        db.add(membership)
    else:
        # Already a member: accepting is a no-op on the role rather than a
        # downgrade. An invitation must never be able to *reduce* someone's
        # standing -- that would be a privilege attack dressed as an offer.
        membership = existing
    invitation.accepted_at = now
    invitation.accepted_by_id = current_user.id
    db.commit()

    organisation = db.get(Organisation, invitation.organisation_id)
    assert organisation is not None  # FK-guaranteed; the row was just joined to
    return _membership_view(organisation, membership)


@router.get("/{organisation_id}", response_model=OrganisationRead)
def read_organisation(organisation: ViewerOrganisation) -> Organisation:
    return organisation


@router.patch("/{organisation_id}", response_model=OrganisationRead)
def update_organisation(
    payload: OrganisationUpdate, organisation: AdminOrganisation, db: DbSession
) -> Organisation:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(organisation, field, value)
    db.commit()
    return organisation


@router.get("/{organisation_id}/projects", response_model=ProjectPage)
def list_organisation_projects(
    organisation: ViewerOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProjectPage:
    """Every project the tenant owns, whoever created it.

    `GET /projects` still answers with what *you* made; this is what the *team*
    owns, which is the thing an organisation exists to make visible.
    """
    condition = Project.organisation_id == organisation.id
    total = db.scalar(select(func.count()).select_from(Project).where(condition)) or 0
    rows = db.scalars(
        select(Project)
        .where(condition)
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ProjectPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[ProjectRead.model_validate(project) for project in rows],
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{organisation_id}/members", response_model=MemberPage)
def list_members(
    organisation: ViewerOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> MemberPage:
    condition = Membership.organisation_id == organisation.id
    total = db.scalar(select(func.count()).select_from(Membership).where(condition)) or 0
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(condition)
        .order_by(Membership.created_at, Membership.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return MemberPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            MemberRead(
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=membership.role,
                domain_role=membership.domain_role,
            )
            for membership, user in rows
        ],
    )


def _last_owner(db: DbSession, organisation_id: str, user_id: str) -> bool:
    """Is this membership the only `owner` the organisation has left?

    An organisation with no owner is unadministrable: nobody can invite, nobody
    can delete it, and nobody can promote anyone -- a tenant that has to be
    fixed by hand in the database.
    """
    owners = (
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.organisation_id == organisation_id,
                Membership.role == OrgRole.OWNER,
            )
        )
        or 0
    )
    if owners > 1:
        return False
    membership = membership_for(db, user_id, organisation_id)
    return membership is not None and membership.role is OrgRole.OWNER


@router.patch("/{organisation_id}/members/{user_id}", response_model=MemberRead)
def update_member(
    payload: MemberUpdate,
    organisation: AdminOrganisation,
    user_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> MemberRead:
    membership = membership_for(db, user_id, organisation.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    changes = payload.model_dump(exclude_unset=True)
    new_role = changes.get("role")
    if new_role is not None and new_role is not OrgRole.OWNER:
        if _last_owner(db, organisation.id, user_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This is the only owner. Promote someone else to owner first.",
            )
    if new_role is OrgRole.OWNER:
        actor = membership_for(db, current_user.id, organisation.id)
        # Only an owner makes an owner. An admin who could mint owners could
        # promote themselves and take the tenant.
        if actor is None or actor.role is not OrgRole.OWNER:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    for field, value in changes.items():
        setattr(membership, field, value)
    db.commit()

    member = db.get(User, user_id)
    assert member is not None
    return MemberRead(
        user_id=member.id,
        email=member.email,
        full_name=member.full_name,
        role=membership.role,
        domain_role=membership.domain_role,
    )


@router.delete("/{organisation_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(organisation: AdminOrganisation, user_id: str, db: DbSession) -> None:
    membership = membership_for(db, user_id, organisation.id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    if _last_owner(db, organisation.id, user_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the only owner. Promote someone else to owner first.",
        )
    db.delete(membership)
    db.commit()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.post(
    "/{organisation_id}/invitations",
    response_model=InvitationIssued,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    payload: InvitationCreate,
    organisation: AdminOrganisation,
    db: DbSession,
    current_user: CurrentUser,
) -> InvitationIssued:
    """Offer membership to an email address, with a hashed single-use token.

    An admin may not invite an owner, for the same reason they may not promote
    one: it is the one move that would let them take the tenant.
    """
    if payload.role is OrgRole.OWNER:
        actor = membership_for(db, current_user.id, organisation.id)
        if actor is None or actor.role is not OrgRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an owner can invite another owner.",
            )

    raw_token = secrets.token_urlsafe(32)
    invitation = OrganisationInvitation(
        organisation_id=organisation.id,
        email=payload.email.lower(),
        role=payload.role,
        domain_role=payload.domain_role,
        token_hash=hash_token(raw_token),
        invited_by_id=current_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=INVITATION_TTL_HOURS),
    )
    db.add(invitation)
    db.commit()

    delivery = mail.send(
        mail.templates.org_invitation(
            to=invitation.email,
            organisation=organisation.name,
            inviter=current_user.full_name or current_user.email,
            token=raw_token,
            expires_at=invitation.expires_at,
        )
    )

    issued = InvitationIssued.model_validate(invitation)
    if delivery.reached_a_mailbox:
        # It is in an inbox. Returning the token as well would put a live
        # credential in the admin's browser history and in any log that records
        # response bodies, for no gain — the recipient already has it.
        return issued
    if settings.is_production:
        # **Production never returns a token, even when the send failed.** This
        # predates the mail transport and is deliberately kept: a token in a
        # response body is a token in every proxy and access log that records
        # one, and an SMTP outage is not a good enough reason to put one there.
        # The operator's route is to fix mail and re-invite. Loosening this to
        # "return it whenever delivery failed" would have been a silent
        # weakening of an existing decision, which is why it is spelled out.
        logger.warning(
            "invitation email failed in production; the token is not returned",
            extra={"organisation_id": organisation.id, "detail": delivery.detail},
        )
        return issued
    # Development, or any deployment whose transport reaches nobody. Hand the
    # token back so the inviter can pass it on themselves, rather than leaving
    # them believing an invitation is in flight that is not.
    if delivery.state is mail.DeliveryState.FAILED:
        logger.warning(
            "invitation email failed; returning the token to the inviter",
            extra={"organisation_id": organisation.id, "detail": delivery.detail},
        )
    return issued.model_copy(update={"token": raw_token})


@router.get("/{organisation_id}/invitations", response_model=InvitationPage)
def list_invitations(
    organisation: AdminOrganisation,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> InvitationPage:
    condition = OrganisationInvitation.organisation_id == organisation.id
    total = (
        db.scalar(select(func.count()).select_from(OrganisationInvitation).where(condition)) or 0
    )
    rows = db.scalars(
        select(OrganisationInvitation)
        .where(condition)
        .order_by(OrganisationInvitation.created_at.desc(), OrganisationInvitation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return InvitationPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[InvitationRead.model_validate(row) for row in rows],
    )


@router.delete(
    "/{organisation_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_invitation(
    organisation: AdminOrganisation, invitation_id: str, db: DbSession
) -> None:
    invitation = db.get(OrganisationInvitation, invitation_id)
    # Scoped to the organisation in the path as well as fetched by id: without
    # the second check an admin of any tenant could revoke any invitation in
    # the system by guessing an id.
    if invitation is None or invitation.organisation_id != organisation.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")
    if invitation.revoked_at is None and invitation.accepted_at is None:
        invitation.revoked_at = datetime.now(timezone.utc)
        db.commit()
