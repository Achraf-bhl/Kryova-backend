from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GeometryVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    media_id: str
    version_number: int
    filename: str
    file_format: str
    size_bytes: int
    checksum_sha256: str
    note: str | None
    stats: dict[str, Any]
    created_at: datetime
