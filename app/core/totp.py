"""RFC 6238 time-based one-time passwords, from the standard library.

Hand-written rather than `pyotp`, and the reason is not the dependency count —
it is that the whole algorithm is HMAC-SHA1 over a counter plus a modulo, about
fifteen lines, and every one of them is testable against the RFC's own vectors.
A library here would be a supply-chain edge on a security primitive in exchange
for saving those fifteen lines. `TestTheRfcVectors` runs the published table.

Three things in here are the difference between a real second factor and a
decorative one, and none of them is in the RFC's pseudocode:

1. **A code that was accepted is burned.** TOTP is valid for its whole 30-second
   step (and this implementation accepts one step either side, for clock skew,
   which is 90 seconds of validity). Without a record of the last step used, a
   code read over somebody's shoulder — or off a phishing page, in real time —
   is replayable for that window. `consume` refuses a step at or below the last
   accepted one, so a code works exactly once.
2. **Comparison is constant-time.** `==` on the formatted code leaks how many
   leading digits were right, one request at a time.
3. **The secret is encrypted at rest.** See `security.encrypt_at_rest` for what
   that does and does not buy — the honest summary is that it defends a stolen
   database and not a stolen host.

Recovery codes live in `models/mfa.py` and are hashed with the same one-way
function as refresh tokens: they are credentials, and a recovery code readable
in the database is a bypass of the factor it backs up.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from typing import Final
from urllib.parse import quote

#: Seconds per step. 30 is the RFC default and what every authenticator app
#: assumes; it is not configurable because a server and a phone that disagree
#: about it produce a factor that never validates and no message explaining why.
STEP_SECONDS: Final = 30

#: Digits in a code. Six, for the same reason.
DIGITS: Final = 6

#: How many steps either side of "now" are accepted, for clock drift between the
#: server and the phone. One step each way = 90 seconds of total validity, which
#: is the usual compromise; two would double the replay window that `consume`
#: exists to close.
SKEW_STEPS: Final = 1

#: Bytes of entropy in a new secret. 20 is the RFC 4226 recommendation and
#: matches SHA-1's block use; it renders as 32 base32 characters.
SECRET_BYTES: Final = 20


def generate_secret() -> str:
    """A fresh base32 secret, in the form an authenticator app expects.

    Unpadded: `=` is legal base32 but several popular apps refuse a
    `otpauth://` URI containing one, and the padding carries no information.
    """
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode(secret: str) -> bytes:
    """Base32 back to bytes, tolerating missing padding and lowercase.

    Users retype these off a screen when an app cannot scan the QR code, so the
    input is whatever their keyboard produced. Everything else about the value
    is exact.
    """
    cleaned = secret.strip().replace(" ", "").upper()
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding, casefold=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("That is not a valid base32 secret") from exc


def step_for(timestamp: float) -> int:
    """Which 30-second step a unix timestamp falls in."""
    return int(timestamp) // STEP_SECONDS


def code_at(secret: str, step: int) -> str:
    """The six-digit code for one step. The whole of RFC 6238."""
    key = _decode(secret)
    counter = struct.pack(">Q", step)
    digest = hmac.new(key, counter, hashlib.sha1).digest()
    # Dynamic truncation: the low nibble of the last byte picks where to read a
    # 4-byte window, and the top bit is masked off so the value is positive on
    # platforms that would read it as signed.
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**DIGITS)).zfill(DIGITS)


def matching_step(secret: str, code: str, *, now: float, skew: int = SKEW_STEPS) -> int | None:
    """The step `code` is valid for, or None.

    Returns the step rather than a boolean so the caller can burn it. A `bool`
    here is what makes replay protection impossible to add later without
    changing every call site — the information needed to prevent it is computed
    right here and thrown away.

    Every candidate step is checked even after a match, so the work done does
    not depend on which step matched. The comparison itself is constant-time.
    """
    candidate = (code or "").strip().replace(" ", "")
    if len(candidate) != DIGITS or not candidate.isdigit():
        return None
    current = step_for(now)
    found: int | None = None
    for offset in range(-skew, skew + 1):
        step = current + offset
        if hmac.compare_digest(code_at(secret, step), candidate):
            found = step
    return found


def consume(
    secret: str, code: str, *, now: float, last_step: int | None, skew: int = SKEW_STEPS
) -> int | None:
    """The step this code burns, or None if it is wrong *or already used*.

    `last_step` is the highest step this account has accepted. A code from that
    step or earlier is refused even when it is arithmetically correct, which is
    what makes a captured code useless the moment it has been used once —
    including by the person who captured it, if they are a second behind.
    """
    step = matching_step(secret, code, now=now, skew=skew)
    if step is None:
        return None
    if last_step is not None and step <= last_step:
        return None
    return step


def provisioning_uri(*, secret: str, account: str, issuer: str = "Kryova") -> str:
    """The `otpauth://` URI an authenticator app scans.

    The issuer appears twice on purpose — once as a label prefix and once as a
    parameter. Older apps read only the prefix, newer ones only the parameter,
    and an app that reads neither files the account under a bare email address
    with no clue which service it belongs to.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


#: How many recovery codes are issued at once, and how long each is. Ten is
#: enough to survive a lost phone and few enough to print; the codes are
#: `secrets.token_hex`-grade rather than memorable, because a memorable recovery
#: code is a guessable one.
RECOVERY_CODE_COUNT: Final = 10
RECOVERY_CODE_BYTES: Final = 5


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh recovery codes, formatted in two groups for transcription.

    Returned in the clear exactly once, at generation. Only hashes are stored,
    so a lost set is regenerated rather than recovered — the same rule as every
    other credential here.
    """
    codes = []
    for _ in range(count):
        raw = secrets.token_hex(RECOVERY_CODE_BYTES)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalise_recovery_code(code: str) -> str:
    """What a recovery code hashes as, whatever the user typed.

    Case and the grouping hyphen carry no information, and somebody reading one
    off paper will get at least one of them wrong. Normalising before hashing is
    the only place that can be fixed: after `hash_token` the two differ
    completely.
    """
    return (code or "").strip().lower().replace("-", "").replace(" ", "")


__all__ = [
    "DIGITS",
    "RECOVERY_CODE_COUNT",
    "SECRET_BYTES",
    "SKEW_STEPS",
    "STEP_SECONDS",
    "code_at",
    "consume",
    "generate_recovery_codes",
    "generate_secret",
    "matching_step",
    "normalise_recovery_code",
    "provisioning_uri",
    "step_for",
]
