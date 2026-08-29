"""Approval tokens for destructive CATIA operations.

`catia_restore` throws away work that cannot be recovered any other way, so the
protocol requires a token the server signs only after the user has clicked
something. This module mints and verifies it.

The token is a bare HMAC over the exact call it authorises -- user, tool,
conversation, target -- rather than an opaque database row, for one reason: it
must be impossible for an approval the user granted for *this* rollback to be
replayed against a different one. Binding the parameters into the signature
makes that structural rather than a check somebody has to remember to write.

It is deliberately not a JWT. A JWT would invite `alg: none` and library-version
questions for a 90-second, single-purpose string, and `python-jose` is already
carrying the session tokens where those questions have been answered.

**The daemon cannot verify this signature** and is not meant to: it holds no
server secret. What the daemon enforces is that a destructive call arrives with
*an* approval token at all, which is what stops a compromised agent stream from
inventing a destructive call the server never signed -- the server is the only
thing that can put a token on the wire.
"""

import base64
import hashlib
import hmac
import time

from app.core.config import settings

#: Long enough for the user to read the confirmation and click, short enough
#: that a token scraped from a log is worthless by the time it is found.
APPROVAL_TTL_S = 300


class ApprovalError(ValueError):
    """The approval token is missing, malformed, expired or for another call."""


def _payload(*, user_id: str, tool: str, conversation_id: str | None, target: str) -> str:
    # Length-prefixed rather than delimiter-joined: with a plain separator, a
    # conversation id ending in the delimiter could shift a field boundary and
    # make one signature valid for two different calls.
    parts = [user_id, tool, conversation_id or "-", target]
    return "|".join(f"{len(part)}:{part}" for part in parts)


def _sign(payload: str, expires_at: int) -> str:
    mac = hmac.new(
        settings.secret_key.encode("utf-8"),
        f"{expires_at}.{payload}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def mint_approval(*, user_id: str, tool: str, conversation_id: str | None, target: str) -> str:
    """Sign an approval for one destructive call. `target` is what it acts on."""
    expires_at = int(time.time()) + APPROVAL_TTL_S
    payload = _payload(user_id=user_id, tool=tool, conversation_id=conversation_id, target=target)
    return f"{expires_at}.{_sign(payload, expires_at)}"


def verify_approval(
    token: str, *, user_id: str, tool: str, conversation_id: str | None, target: str
) -> None:
    """Raise `ApprovalError` unless `token` approves exactly this call."""
    if not token:
        raise ApprovalError(
            f"{tool} destroys work and needs the user's explicit approval. "
            "Ask the user to confirm; the interface will supply an approval token."
        )
    expiry_text, _, signature = token.partition(".")
    if not signature:
        raise ApprovalError("The approval token is malformed. Ask the user to approve again.")
    try:
        expires_at = int(expiry_text)
    except ValueError as exc:
        raise ApprovalError(
            "The approval token is malformed. Ask the user to approve again."
        ) from exc
    if expires_at < time.time():
        raise ApprovalError(
            "The approval expired before the operation ran. Ask the user to approve again."
        )

    payload = _payload(user_id=user_id, tool=tool, conversation_id=conversation_id, target=target)
    if not hmac.compare_digest(signature, _sign(payload, expires_at)):
        # Deliberately not "signature mismatch": the common cause is an approval
        # granted for a different checkpoint, and saying so is more useful than
        # naming the cryptography.
        raise ApprovalError(
            "That approval was not granted for this operation. Ask the user to "
            "approve this exact change."
        )
