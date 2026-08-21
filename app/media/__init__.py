from functools import lru_cache

from app.core.config import settings
from app.media.service import MediaService, UploadError
from app.media.store import (
    BlobInfo,
    LocalMediaStore,
    MediaError,
    MediaNotFound,
    MediaTooLarge,
)


@lru_cache
def get_media_store() -> LocalMediaStore:
    return LocalMediaStore(settings.media_root, chunk_size=settings.media_chunk_size)


__all__ = [
    "BlobInfo",
    "LocalMediaStore",
    "MediaError",
    "MediaNotFound",
    "MediaService",
    "MediaTooLarge",
    "UploadError",
    "get_media_store",
]
