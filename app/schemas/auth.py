from datetime import datetime

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
