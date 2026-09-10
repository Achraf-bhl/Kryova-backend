from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.organisation import DomainRole, OrgRole


class OrganisationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")


class OrganisationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class OrganisationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    is_personal: bool
    created_at: datetime
    updated_at: datetime


class OrganisationMembershipRead(OrganisationRead):
    """An organisation as seen by one of its members, with that member's role."""

    role: OrgRole
    domain_role: DomainRole | None


class MemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    full_name: str | None
    role: OrgRole
    domain_role: DomainRole | None


class MemberUpdate(BaseModel):
    role: OrgRole | None = None
    domain_role: DomainRole | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: OrgRole = OrgRole.MEMBER
    domain_role: DomainRole | None = None


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    organisation_id: str
    email: str
    role: OrgRole
    domain_role: DomainRole | None
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class InvitationIssued(InvitationRead):
    """The one response that may carry the raw token.

    Only the SHA-256 is stored, so this is the single moment the token exists
    outside the invitee's mail.

    **It is populated only when the email did not reach a mailbox** (P1.5 gave
    this service a mail transport; before that it was always returned). If the
    invitation is in an inbox, returning the token as well would put a live
    credential in the inviter's browser history and in any log that records
    response bodies, for no gain -- the recipient already has it. When delivery
    fails, or on a development deployment whose transport reaches nobody, the
    token comes back so the inviter can pass it on rather than believing an
    invitation is in flight that is not.
    """

    token: str | None = None


class InvitationAccept(BaseModel):
    token: str = Field(min_length=1, max_length=256)
