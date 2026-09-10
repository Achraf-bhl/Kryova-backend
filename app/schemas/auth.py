from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(default=None, max_length=255)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str | None
    is_active: bool
    created_at: datetime
    #: P1.5. On the user object every client already reads, so the frontend can
    #: show the "confirm your address" banner without a second request — and so
    #: it disappears the moment the address is confirmed, because the same
    #: response that carries the session carries this.
    is_verified: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SessionRead(BaseModel):
    user: UserRead
    csrf_token: str


class DeviceSessionRead(BaseModel):
    """One signed-in device, as the account's session list shows it.

    Carries no token and no hash of one, deliberately: this is the surface a
    logged-in browser reads, and nothing in it is useful to an attacker who has
    it. `current` is computed per request rather than stored, because "this
    device" is a property of who is asking, not of the row.
    """

    id: str
    device_label: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime
    absolute_expires_at: datetime
    current: bool


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordReset(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=256)


# ---------------------------------------------------------------------------
# Email verification (P1.5)
# ---------------------------------------------------------------------------


class VerificationStatusRead(BaseModel):
    """Where this account stands on proving its address.

    `can_resend_in_seconds` is 0 when a resend is available now. Returned rather
    than left for the client to compute from a timestamp, because the client's
    clock is the one thing here that is definitely not ours.
    """

    verified: bool
    can_resend_in_seconds: int = 0
    #: False when the deployment has no transport that reaches a mailbox. The UI
    #: renders "ask an administrator to confirm this address" instead of a
    #: resend button that would do nothing visible.
    delivery_available: bool = True


class EmailVerification(BaseModel):
    token: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Second factor (P1.7)
# ---------------------------------------------------------------------------


class MfaChallenge(BaseModel):
    """Returned by `/auth/login` when the password was right and a code is owed.

    `mfa_required` is a literal True so a client can discriminate the union
    without inspecting which keys are present — the shape a TypeScript client
    can narrow on, which is what `../Kryova-frontend/src/types/api.ts` does.
    """

    mfa_required: Literal[True] = True
    challenge_token: str
    expires_in_seconds: int
    #: Whether recovery codes are available as a way past a lost phone. False
    #: means all ten are spent, and the UI must not offer a route that cannot
    #: work.
    recovery_available: bool


#: What `/auth/login` answers with: a session, or a demand for the second
#: factor. Declared as a union rather than by widening `SessionRead` with
#: nullable fields, so neither shape can be read as the other by accident.
LoginResult = SessionRead | MfaChallenge


class MfaLogin(BaseModel):
    """Finishing a sign-in that was interrupted by the second factor."""

    challenge_token: str = Field(min_length=1)
    #: A six-digit TOTP code *or* a recovery code. One field, because the user
    #: is typing whichever they have and asking them to classify it first is
    #: friction with no purpose — the server can tell them apart.
    code: str = Field(min_length=1, max_length=64)


class MfaCodeSubmission(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class MfaEnrolmentRead(BaseModel):
    """The secret, its QR-code URI, and the recovery codes — shown once, ever.

    Nothing returns these again. A user who loses them regenerates, which is why
    `secret` is present at all: an app that cannot scan the QR code needs the
    base32 to type in, and there is no later opportunity to offer it.
    """

    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class RecoveryCodesRead(BaseModel):
    recovery_codes: list[str]


class MfaStatusRead(BaseModel):
    enabled: bool
    #: Started but never confirmed. Distinct from `enabled` because it is the
    #: state a user gets stuck in, and the UI has to be able to offer "finish
    #: setting this up" rather than "set this up".
    pending: bool = False
    recovery_codes_remaining: int = 0
