"""Double-submit CSRF tokens: minting in one place, verification in one place.

This module used to mint a token and stop there, leaving verification hand-rolled
at two call sites -- `api/deps.py` and `routes/auth.py`. The two copies disagreed:
`auth.py` rejected an absent token, `deps.py` did not, because
`secrets.compare_digest("", "")` is `True`. A browser restart drops the
session-scoped CSRF cookie while the persistent access cookie survives, so that
gap was reachable on every ordinary sign-in, and every mutating request in that
window was accepted with no CSRF token at all.

Both halves now come through `verify_csrf` below. Keep it that way: a second
implementation is what produced the hole the first time.
"""

import secrets

CSRF_COOKIE_NAME = "kryova_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

# Methods that must carry a token. Anything not listed here is expected to be
# side-effect free; a handler that mutates state on GET is the bug, not this set.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(header_value: str | None, cookie_value: str | None) -> bool:
    """Whether the double-submit halves match.

    Absent is never valid. `compare_digest` answers "are these equal", and two
    missing values are equal -- so emptiness has to be rejected explicitly,
    before the comparison, or "sent nothing" reads as "sent the right thing".
    """
    if not header_value or not cookie_value:
        return False
    return secrets.compare_digest(header_value, cookie_value)


def requires_csrf(method: str, has_authorization_header: bool) -> bool:
    """Whether this request must present a matching token.

    Bearer-authenticated requests are exempt: a browser cannot attach an
    `Authorization` header cross-site without a preflight the CORS allow-list
    already governs, so the token adds nothing there. Cookie-authenticated
    mutations are exactly the case double-submit exists for.
    """
    if method.upper() in SAFE_METHODS:
        return False
    return not has_authorization_header
