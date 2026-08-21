from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.media import MediaKind, UploadStatus


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: MediaKind
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    meta: dict[str, Any]
    created_at: datetime


class UploadSessionCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    total_size_bytes: int = Field(gt=0)
    kind: MediaKind = MediaKind.CAD
    content_type: str = "application/octet-stream"
    chunk_size: int | None = Field(default=None, gt=0)
    expected_sha256: str | None = Field(
        default=None,
        pattern="^[0-9a-fA-F]{64}$",
        description="Optional: the assembled file is rejected if it does not match.",
    )


class UploadSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: MediaKind
    filename: str
    status: UploadStatus
    total_size_bytes: int
    chunk_size: int
    total_chunks: int
    received_chunks: list[int]
    missing_chunks: list[int]
    media_id: str | None
    error: str | None
    expires_at: datetime
    created_at: datetime
