"""Moving file bytes across the bridge.

The daemon has no route to the blob store: it sits behind a corporate NAT and
dials out, so it cannot be given an upload URL to POST to without also being
given a credential and a reachable host. Files therefore ride back inside the
result frame, base64-encoded.

That is a real constraint and it is stated rather than hidden. Base64 costs a
third in size and the whole frame is buffered, so there is a hard ceiling
(`INLINE_TRANSFER_MAX_BYTES`), and above it a checkpoint records the daemon-side
snapshot only. When exports of hundreds of megabytes matter, the fix is a
device-token-authenticated upload endpoint the daemon can PUT to -- not a bigger
number here.

Decoding is chunked into a temporary file. Everywhere else in this codebase
heavy bytes are streamed rather than materialised, and `base64.b64decode` of a
whole payload would put a second full copy of every export in memory at once.
"""

import base64
import binascii
import hashlib
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Ceiling on one inline transfer, measured on the decoded bytes.
INLINE_TRANSFER_MAX_BYTES = 64 * 1024 * 1024

#: Base64 decodes in 4-character groups, so the chunk must be a multiple of 4
#: or the decoder sees a truncated group and raises.
_B64_CHUNK = 4 * 256 * 1024


class TransferError(RuntimeError):
    """The daemon's inline payload was missing, malformed or too large."""


@dataclass(frozen=True)
class ReceivedFile:
    path: Path
    digest: str
    size_bytes: int


def _b64_chunks(text: str) -> Iterator[str]:
    for start in range(0, len(text), _B64_CHUNK):
        yield text[start : start + _B64_CHUNK]


def receive_inline_file(
    payload: dict[str, Any],
    *,
    field: str = "content_b64",
    max_bytes: int = INLINE_TRANSFER_MAX_BYTES,
    expected_digest_field: str = "sha256",
) -> ReceivedFile:
    """Decode a base64 payload into a temp file, hashing as it goes.

    The caller owns the returned path and must unlink it. It is deliberately not
    a context manager: the usual next step is `MediaService.store_path`, which
    moves the bytes into the content-addressed store, and wrapping that in a
    `with` would suggest the file outlives it.
    """
    encoded = payload.get(field)
    if not isinstance(encoded, str) or not encoded:
        raise TransferError(
            f"The bridge reported success but sent no file data ({field} was empty)."
        )
    # 4 base64 characters carry 3 bytes; refuse before decoding rather than
    # after materialising something oversized.
    if len(encoded) // 4 * 3 > max_bytes:
        raise TransferError(
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB the bridge "
            "can transfer in one piece."
        )

    digest = hashlib.sha256()
    size = 0
    handle = tempfile.NamedTemporaryFile(prefix="catia-", suffix=".bin", delete=False)
    path = Path(handle.name)
    try:
        with handle:
            for chunk in _b64_chunks(encoded):
                try:
                    data = base64.b64decode(chunk, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise TransferError(
                        "The bridge sent a corrupted file payload; ask it to retry."
                    ) from exc
                size += len(data)
                if size > max_bytes:
                    raise TransferError(
                        f"The file is larger than the {max_bytes // (1024 * 1024)} MB "
                        "the bridge can transfer in one piece."
                    )
                digest.update(data)
                handle.write(data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    actual = digest.hexdigest()
    claimed = payload.get(expected_digest_field)
    if isinstance(claimed, str) and claimed and claimed.lower() != actual:
        path.unlink(missing_ok=True)
        raise TransferError(
            "The transferred file does not match the checksum the bridge reported; "
            "the transfer was corrupted."
        )
    if size == 0:
        path.unlink(missing_ok=True)
        raise TransferError("The bridge sent an empty file.")
    return ReceivedFile(path=path, digest=actual, size_bytes=size)


def encode_inline_file(path: Path, *, max_bytes: int = INLINE_TRANSFER_MAX_BYTES) -> str:
    """Base64 a local file for sending down to the daemon (checkpoint restore)."""
    size = path.stat().st_size
    if size > max_bytes:
        raise TransferError(
            f"The stored checkpoint is {size // (1024 * 1024)} MB, larger than the "
            f"{max_bytes // (1024 * 1024)} MB the bridge can transfer in one piece."
        )
    with path.open("rb") as handle:
        return base64.b64encode(handle.read()).decode("ascii")
