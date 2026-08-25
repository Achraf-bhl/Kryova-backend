"""Media service: the database registry on top of the local blob store.

The store knows about bytes; this knows about ownership, kinds, and the fact
that two rows can legitimately point at one blob. Deletion goes through here for
exactly that reason -- removing a row must not remove a file something else
still references.
"""

import logging
import math
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.media.store import BlobInfo, LocalMediaStore, MediaError
from app.models.media import Media, MediaKind, MediaUploadSession, UploadStatus

logger = logging.getLogger(__name__)


class UploadError(MediaError):
    """A chunked upload session was used incorrectly."""


class MediaService:
    def __init__(self, db: Session, store: LocalMediaStore) -> None:
        self.db = db
        self.store = store

    # -- single-shot storage --------------------------------------------------

    def store_stream(
        self,
        *,
        owner_id: str,
        kind: MediaKind,
        filename: str,
        stream: BinaryIO,
        content_type: str = "application/octet-stream",
        meta: dict | None = None,
        max_bytes: int | None = None,
    ) -> Media:
        info = self.store.write(stream, max_bytes=max_bytes or settings.max_media_bytes)
        return self._register(
            owner_id=owner_id,
            kind=kind,
            filename=filename,
            content_type=content_type,
            info=info,
            meta=meta,
        )

    def store_path(
        self,
        *,
        owner_id: str,
        kind: MediaKind,
        path: Path,
        filename: str | None = None,
        content_type: str = "application/octet-stream",
        meta: dict | None = None,
    ) -> Media:
        info = self.store.write_file(path, max_bytes=settings.max_media_bytes)
        return self._register(
            owner_id=owner_id,
            kind=kind,
            filename=filename or Path(path).name,
            content_type=content_type,
            info=info,
            meta=meta,
        )

    def _register(
        self,
        *,
        owner_id: str,
        kind: MediaKind,
        filename: str,
        content_type: str,
        info: BlobInfo,
        meta: dict | None,
    ) -> Media:
        media = Media(
            owner_id=owner_id,
            kind=kind,
            filename=Path(filename).name,
            content_type=content_type,
            size_bytes=info.size_bytes,
            sha256=info.digest,
            meta={**(meta or {}), "deduplicated": info.deduplicated},
        )
        self.db.add(media)
        self.db.flush()
        return media

    # -- reading --------------------------------------------------------------

    def open(self, media: Media) -> BinaryIO:
        return self.store.open(media.sha256)

    def iter_chunks(self, media: Media, chunk_size: int | None = None) -> Iterator[bytes]:
        return self.store.iter_chunks(media.sha256, chunk_size)

    def local_path(self, media: Media) -> Path:
        return self.store.local_path(media.sha256)

    def exists(self, media: Media) -> bool:
        return self.store.exists(media.sha256)

    def verify(self, media: Media) -> bool:
        return self.store.verify(media.sha256)

    # -- deletion -------------------------------------------------------------

    def delete(self, media: Media) -> None:
        """Delete a media record, and its blob only if nothing else uses it."""
        digest = media.sha256
        self.db.delete(media)
        self.db.flush()
        self._drop_blob_if_orphaned(digest)

    def delete_digests_if_orphaned(self, digests: list[str]) -> None:
        """Sweep blobs after their rows have already gone (e.g. a cascade)."""
        for digest in dict.fromkeys(digests):
            self._drop_blob_if_orphaned(digest)

    def _drop_blob_if_orphaned(self, digest: str) -> None:
        still_referenced = self.db.scalar(
            select(func.count()).select_from(Media).where(Media.sha256 == digest)
        )
        if not still_referenced:
            self.store.delete(digest)

    # -- resumable chunked uploads -------------------------------------------

    def begin_upload(
        self,
        *,
        owner_id: str,
        kind: MediaKind,
        filename: str,
        total_size_bytes: int,
        content_type: str = "application/octet-stream",
        chunk_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> MediaUploadSession:
        if total_size_bytes <= 0:
            raise UploadError("total_size_bytes must be positive")
        if total_size_bytes > settings.max_media_bytes:
            raise UploadError(
                f"File is larger than the {settings.max_media_bytes} byte limit"
            )

        size = chunk_size or settings.media_chunk_size
        session = MediaUploadSession(
            owner_id=owner_id,
            kind=kind,
            filename=Path(filename).name,
            content_type=content_type,
            total_size_bytes=total_size_bytes,
            chunk_size=size,
            total_chunks=math.ceil(total_size_bytes / size),
            received_chunks=[],
            expected_sha256=expected_sha256.lower() if expected_sha256 else None,
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.upload_session_ttl_hours),
        )
        self.db.add(session)
        self.db.flush()
        self._staging_dir(session).mkdir(parents=True, exist_ok=True)
        return session

    def save_chunk(self, session: MediaUploadSession, index: int, data: bytes) -> MediaUploadSession:
        if session.status is not UploadStatus.IN_PROGRESS:
            raise UploadError(f"Upload session is {session.status.value}")
        if not 0 <= index < session.total_chunks:
            raise UploadError(
                f"Chunk index {index} is outside 0..{session.total_chunks - 1}"
            )

        is_last = index == session.total_chunks - 1
        expected = (
            session.total_size_bytes - session.chunk_size * index
            if is_last
            else session.chunk_size
        )
        if len(data) != expected:
            raise UploadError(
                f"Chunk {index} is {len(data)} bytes; expected {expected}"
            )

        path = self._staging_dir(session) / f"{index:08d}.part"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        # Re-uploading a chunk after a timeout is normal; keep the set unique.
        received = sorted({*(session.received_chunks or []), index})
        session.received_chunks = received
        self.db.flush()
        return session

    def complete_upload(self, session: MediaUploadSession) -> Media:
        if session.status is UploadStatus.COMPLETED and session.media is not None:
            return session.media
        if session.status is not UploadStatus.IN_PROGRESS:
            raise UploadError(f"Upload session is {session.status.value}")
        if not session.is_complete:
            raise UploadError(
                f"Upload is incomplete: {len(session.missing_chunks)} chunk(s) still missing"
            )

        staging = self._staging_dir(session)
        stream: BinaryIO = cast(BinaryIO, _ChunkReader(staging, session.total_chunks))
        with stream:
            info = self.store.write(stream, max_bytes=settings.max_media_bytes)

        if info.size_bytes != session.total_size_bytes:
            self.abort_upload(session, error="Assembled size did not match the declared size")
            raise UploadError(
                f"Assembled file is {info.size_bytes} bytes; expected {session.total_size_bytes}"
            )
        if session.expected_sha256 and info.digest != session.expected_sha256:
            self.abort_upload(session, error="Checksum mismatch")
            raise UploadError(
                "Assembled file does not match the declared SHA-256; the transfer was corrupted"
            )

        media = self._register(
            owner_id=session.owner_id,
            kind=session.kind,
            filename=session.filename,
            content_type=session.content_type,
            info=info,
            meta={"uploaded_in_chunks": session.total_chunks},
        )
        session.status = UploadStatus.COMPLETED
        session.media_id = media.id
        self.db.flush()
        shutil.rmtree(staging, ignore_errors=True)
        return media

    def abort_upload(self, session: MediaUploadSession, error: str | None = None) -> None:
        session.status = UploadStatus.ABORTED
        session.error = error
        self.db.flush()
        shutil.rmtree(self._staging_dir(session), ignore_errors=True)

    def sweep_expired_uploads(self) -> int:
        """Drop staged chunks for sessions nobody finished. Returns the count."""
        stmt = select(MediaUploadSession).where(
            MediaUploadSession.status == UploadStatus.IN_PROGRESS,
            MediaUploadSession.expires_at < datetime.now(timezone.utc),
        )
        expired = list(self.db.scalars(stmt))
        for session in expired:
            self.abort_upload(session, error="Expired before completion")
        return len(expired)

    def _staging_dir(self, session: MediaUploadSession) -> Path:
        return settings.media_staging_dir / session.id


class _ChunkReader:
    """A read-only file-like view over an ordered run of chunk files.

    Lets the store hash and copy an assembled upload without ever holding the
    whole file -- or even a whole chunk more than necessary -- in memory.
    """

    def __init__(self, directory: Path, total_chunks: int) -> None:
        self._paths = [directory / f"{i:08d}.part" for i in range(total_chunks)]
        self._index = 0
        self._current: BinaryIO | None = None

    def __enter__(self) -> "_ChunkReader":
        return self

    def __exit__(self, *exc_info) -> None:
        if self._current is not None:
            self._current.close()

    def read(self, size: int = -1) -> bytes:
        while True:
            if self._current is None:
                if self._index >= len(self._paths):
                    return b""
                path = self._paths[self._index]
                if not path.is_file():
                    raise UploadError(f"Staged chunk {self._index} is missing")
                self._current = path.open("rb")

            data = self._current.read(size)
            if data:
                return data

            self._current.close()
            self._current = None
            self._index += 1
