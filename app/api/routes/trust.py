"""The trust surface — master plan P10.3, publishing 7.4.

Four endpoints, **none of them authenticated**, and that is the decision this
module is really about.

## Why it is public

The claim Kryova is sold on is that verification is the product. A page making
that claim behind a login is a sales asset: the only people who can read it are
people who already bought, and the person it needs to convince — an engineer
deciding whether to trust a number, a reviewer deciding whether to sign — is
exactly the person without an account. "Checkable by an outsider" is not a
metaphor here; if it needs a token, it is not checkable by an outsider.

The rest of this API is the opposite (`get_owned_project` returns 404 rather
than 403 so ids cannot be enumerated across accounts), so making one router
public is a decision that has to be paid for rather than assumed. It is paid for
by the payload containing nothing that came from a database:

* Every byte served here is a **module constant** — `app.verify.register.ANALYSES`,
  `app.verify.commitments.COMMITMENTS`, `app.verify.changelog.CHANGES` — plus
  benchmark outcomes, which come from a recorded suite and not from a tenant's
  work. No session, no `DbSession`, no `CurrentUser`, and no dependency in this
  file can reach one. The recording is one committed file read from the
  repository (`data/verify/validation-outcomes.json`), never a path a request can
  influence, and `app.verify.recorded.load` returns *nothing* rather than raising
  for every way of failing to read it — a trust page that 500s over a malformed
  artefact publishes less than one that says the evidence could not be read.
* `PUBLISHED_SUITE` is asserted at import to contain nothing runnable, so an
  unauthenticated request cannot make this process solve anything. Without that,
  a public route over a benchmark suite is a way to spend the server's CPU by
  asking politely.
* Benchmark provenance is reduced to an **allowlist** before it is printed
  (`register._public_provenance`) and every free-text field is passed through
  `register.scrub`. Two fields motivate that rather than paranoia:
  `geometry.source` is documented as free text and a customer file name lands in
  it, and an `ERRORED` outcome's `detail` is an exception's own message — on the
  Windows seat a `FileNotFoundError` puts `C:\\Users\\<the customer>` in one.

`tests/test_trust.py` asserts all of it, including that no route in this module
declares an auth or database dependency, so a later edit cannot make one of
these endpoints tenant-aware without the suite noticing.

## Caching

`Cache-Control: public, max-age=300`. The content changes when a build changes,
not when a user does, and a trust page that cannot be cached is one that falls
over the first time somebody links to it.
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter, Response

from app.verify import changelog, commitments
from app.verify.register import published_register

router = APIRouter(prefix="/trust", tags=["trust"])

#: Five minutes. Long enough that a link on an outside page cannot be used to
#: hammer this process, short enough that a redeploy is visible quickly.
_CACHE_CONTROL: Final = "public, max-age=300"


def _cache(response: Response) -> None:
    response.headers["Cache-Control"] = _CACHE_CONTROL


@router.get("")
def trust_index(response: Response) -> dict[str, Any]:
    """What is here, and the one sentence that summarises the register.

    The headline is repeated on the index deliberately: a reader who follows a
    link to /trust and no further should still leave knowing how much is *not*
    validated, which is the number the register exists to publish.
    """
    _cache(response)
    register = published_register()
    return {
        "scope": commitments.SCOPE,
        "validation_headline": register.headline(),
        "everything_validated": register.complete,
        "pages": {
            "validation_register": "/trust/validation-register",
            "commitments": "/trust/commitments",
            "accuracy_changelog": "/trust/changelog",
        },
    }


@router.get("/validation-register")
def validation_register(response: Response) -> dict[str, Any]:
    """Which analyses are validated, against what, to what accuracy — and which
    are not, which is the larger half of this payload today.

    `accuracy_changes_since_generated` is not an afterthought: a register is a
    claim about a build, and an accuracy-affecting change published after the
    register was generated means the page above it may be describing a product
    that no longer exists. Serving both together is what stops a stale green
    page standing on its own.
    """
    _cache(response)
    register = published_register()
    payload = register.to_dict()
    payload["accuracy_changes_since_generated"] = [
        change.to_dict() for change in register.superseded_by(changelog.CHANGES)
    ]
    return payload


@router.get("/commitments")
def what_kryova_will_not_claim(response: Response) -> dict[str, Any]:
    """The scope, the sign-off model, and each commitment with the file that
    keeps it."""
    _cache(response)
    return commitments.to_dict()


@router.get("/changelog")
def accuracy_changelog(response: Response) -> dict[str, Any]:
    """Changes that move a number, and what to do about each one."""
    _cache(response)
    return changelog.to_dict()
