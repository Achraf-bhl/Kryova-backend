"""Content-addressed local blob store.

Every heavy artefact -- CAD uploads, volume meshes, result fields, FAISS indexes
-- lives on this machine's disk. Only small metadata rows go to the cloud
database, so a 400 MB STEP file never crosses the network to Neon.

Blobs are keyed by the SHA-256 of their contents, which buys deduplication for
free: re-uploading the same part after a failed run costs no extra disk, and an
identical mesh produced by two runs is stored once. It also makes every read
verifiable -- the name *is* the checksum.

All IO is chunked. Nothing here ever loads a whole file into memory.
"""

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_DIGEST_LENGTH = 64  # hex characters of a SHA-256


class MediaError(RuntimeError):
    """The blob store could not satisfy the request."""


class MediaTooLarge(MediaError):
    """The stream exceeded the configured size ceiling."""


class MediaNotFound(MediaError):
    """No blob is stored under that digest."""


@dataclass(frozen=True)
class BlobInfo:
    digest: str
    size_bytes: int
    deduplicated: bool
    """True when the content was already stored and no new bytes were written."""


class LocalMediaStore:
    def __init__(self, root: Path, chunk_size: int = 8 * 1024 * 1024) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.root = Path(root)
        self.chunk_size = chunk_size
        self.root.mkdir(parents=True, exist_ok=True)

    # -- layout ---------------------------------------------------------------

    def path_for(self, digest: str) -> Path:
        """Where a digest lives on disk.

        Sharded two levels deep: a flat directory of a hundred thousand blobs is
        slow to list and unpleasant on most filesystems.
        """
        digest = self._validate(digest)
        return self.root / "blobs" / digest[:2] / digest[2:4] / digest

    @staticmethod
    def _validate(digest: str) -> str:
        value = digest.lower()
        if len(value) != _DIGEST_LENGTH or any(c not in "0123456789abcdef" for c in value):
            raise MediaError(f"not a SHA-256 digest: {digest!r}")
        return value

    # -- writing --------------------------------------------------------------

    def write(self, source: BinaryIO, max_bytes: int | None = None) -> BlobInfo:
        """Stream `source` into the store, hashing as it goes.

        The digest is only known once the whole stream has been read, so bytes
        land in a temp file first and are moved into place afterwards. A failure
        part-way leaves no half-written blob.
        """
        digest = hashlib.sha256()
        size = 0
        staging = self.root / "_incoming"
        staging.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(dir=staging, suffix=".part")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as tmp:
                while chunk := source.read(self.chunk_size):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise MediaTooLarge(f"stream exceeds the {max_bytes} byte limit")
                    digest.update(chunk)
                    tmp.write(chunk)
            return self._commit(tmp_path, digest.hexdigest(), size)
        finally:
            tmp_path.unlink(missing_ok=True)

    def write_file(self, path: Path, max_bytes: int | None = None) -> BlobInfo:
        with Path(path).open("rb") as fh:
            return self.write(fh, max_bytes=max_bytes)

    def write_bytes(self, data: bytes) -> BlobInfo:
        import io

        return self.write(io.BytesIO(data))

    def _commit(self, tmp_path: Path, digest: str, size: int) -> BlobInfo:
        target = self.path_for(digest)
        if target.exists():
            # Same content already stored. Nothing to write, and nothing to
            # verify: the digest matching is the verification.
            return BlobInfo(digest=digest, size_bytes=size, deduplicated=True)

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(tmp_path, target)
        except OSError as exc:  # pragma: no cover - crossing a filesystem boundary
            shutil.copyfile(tmp_path, target)
            if not target.exists():
                raise MediaError(f"could not store blob {digest}: {exc}") from exc
        return BlobInfo(digest=digest, size_bytes=size, deduplicated=False)

    # -- reading --------------------------------------------------------------

    def open(self, digest: str) -> BinaryIO:
        path = self.path_for(digest)
        if not path.is_file():
            raise MediaNotFound(f"no blob stored for {digest}")
        return path.open("rb")

    def iter_chunks(self, digest: str, chunk_size: int | None = None) -> Iterator[bytes]:
        """Read a blob in chunks -- the only safe way to serve a large file."""
        size = chunk_size or self.chunk_size
        with self.open(digest) as fh:
            while chunk := fh.read(size):
                yield chunk

    def local_path(self, digest: str) -> Path:
        """The real filesystem path, for subprocesses like gmsh that need one."""
        path = self.path_for(digest)
        if not path.is_file():
            raise MediaNotFound(f"no blob stored for {digest}")
        return path

    def exists(self, digest: str) -> bool:
        return self.path_for(digest).is_file()

    def size(self, digest: str) -> int:
        return self.local_path(digest).stat().st_size

    # -- integrity and cleanup ------------------------------------------------

    def verify(self, digest: str) -> bool:
        """Re-hash a blob and check it still matches its name."""
        actual = hashlib.sha256()
        for chunk in self.iter_chunks(digest):
            actual.update(chunk)
        return actual.hexdigest() == self._validate(digest)

    def delete(self, digest: str) -> None:
        """Remove a blob. Callers must be sure nothing else references it --
        content addressing means two records can share one file."""
        self.path_for(digest).unlink(missing_ok=True)

    def iter_digests(self) -> Iterator[str]:
        blobs = self.root / "blobs"
        if not blobs.is_dir():
            return
        for path in blobs.rglob("*"):
            if path.is_file():
                yield path.name

    def total_bytes(self) -> int:
        return sum(
            path.stat().st_size for path in (self.root / "blobs").rglob("*") if path.is_file()
        )
