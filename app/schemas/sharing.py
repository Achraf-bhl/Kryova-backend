"""Request and response shapes for share links and transfers (P2.5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.sharing import DEFAULT_SHARE_DAYS, MAX_SHARE_DAYS


class ShareLinkCreate(BaseModel):
    label: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2000)
    #: Bounded in the schema *and* in `core/sharing.issue`. The duplication is
    #: deliberate: the schema gives the client a useful 422 and the service
    #: gives the rule one home, because the agent tools and the admin panel
    #: reach `issue` without passing through this model.
    days: int = Field(default=DEFAULT_SHARE_DAYS, ge=1, le=MAX_SHARE_DAYS)
    allow_geometry_download: bool = False


class ShareLinkRead(BaseModel):
    """A link as its *issuer* sees it. Carries no token — that is returned once."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    label: str | None
    note: str | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    allow_geometry_download: bool
    view_count: int
    last_viewed_at: datetime | None


class ShareLinkIssued(ShareLinkRead):
    """The one response that carries the raw token, at creation.

    A separate model rather than an optional field on `ShareLinkRead`, so that
    no list endpoint can grow a token by accident: the type simply does not
    have one.
    """

    token: str
    url: str


# ---------------------------------------------------------------------------
# What the recipient sees
# ---------------------------------------------------------------------------


class SharedGeometry(BaseModel):
    version_number: int
    filename: str | None
    created_at: datetime
    size_bytes: int | None


class SharedSimulation(BaseModel):
    """One run, as a package reader sees it.

    Deliberately not `SimulationRead`: that model carries ids, media handles and
    the internal solver name, and this response goes to somebody with no
    account. Values are in the units the rest of the system uses — mm-N-MPa,
    unconverted, as the standing rule requires.
    """

    analysis: str
    status: str
    created_at: datetime
    max_von_mises_mpa: float | None = None
    max_displacement_mm: float | None = None
    mass_kg: float | None = None
    #: Whether the number above is trustworthy on its own. `single-grid` is not
    #: a pass — a package that showed a peak stress without saying it came from
    #: one mesh would be exactly the claim Decision 3 forbids.
    mesh_convergence: str | None = None
    solver: str | None = None


class SharedPackage(BaseModel):
    """The whole read-only view, for an unauthenticated reader."""

    project_name: str
    project_description: str | None
    organisation_name: str
    shared_by: str
    label: str | None
    note: str | None
    expires_at: datetime
    allow_geometry_download: bool
    geometry: list[SharedGeometry]
    simulations: list[SharedSimulation]


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------


class ProjectTransferCreate(BaseModel):
    to_organisation_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=2000)


class ProjectTransferRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    from_organisation_id: str
    to_organisation_id: str
    transferred_by_id: str
    reason: str | None
    created_at: datetime


__all__ = [
    "ProjectTransferCreate",
    "ProjectTransferRead",
    "ShareLinkCreate",
    "ShareLinkIssued",
    "ShareLinkRead",
    "SharedGeometry",
    "SharedPackage",
    "SharedSimulation",
]
