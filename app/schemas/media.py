from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import MAX_UPLOAD_CHUNK_BYTES
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
    # A chunk is staged as one file and its length is checked in one go, so the
    # client does not get to choose how much of the server a single PUT occupies.
    chunk_size: int | None = Field(
        default=None,
        gt=0,
        le=MAX_UPLOAD_CHUNK_BYTES,
        description=f"Bytes per chunk, at most {MAX_UPLOAD_CHUNK_BYTES}.",
    )
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
