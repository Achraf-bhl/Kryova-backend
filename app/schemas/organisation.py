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
    """The one response that carries the raw token.

    Only the SHA-256 is stored, so this is the single moment the token exists
    outside the invitee's mail. It is returned rather than only mailed because
    there is no mail transport in this service yet -- the same honest gap the
    password-reset flow has.
    """

    token: str | None = None


class InvitationAccept(BaseModel):
    token: str = Field(min_length=1, max_length=256)
