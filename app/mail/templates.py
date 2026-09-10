"""Every message this product sends, as a function that returns a `Mail`.

One module, so the set is enumerable: `MailKind` names them and this file
renders them, and a test asserts the two agree. The alternative — an f-string in
whichever route handler needed it — makes "what does Kryova email people?" a
question you answer by grepping, and makes the tone drift per author.

Three rules the wording follows, and they are not decoration:

1. **Say what happened and what to do, in that order.** These arrive out of
   context, often days later. "Somebody asked to reset the password on this
   account" is readable by a person who did not ask; "Click here to continue" is
   not.
2. **Name the actor where there is one.** A staff member reading a support
   ticket, an org owner who invited you, an administrator who suspended an
   account. An email that says "your account was accessed" and cannot say by
   whom is an alarm with no action attached to it.
3. **Never tell the reader something we do not know.** The invitation does not
   claim the sender is a colleague; the impersonation notice does not claim the
   session was legitimate. The whole point of sending it is that the reader is
   the one who can tell.

Links are built from `settings.frontend_url` because the frontend owns every
route a human clicks — there is no server-rendered page in this service and a
link into the API would be a dead end.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from urllib.parse import quote

from app.core.config import settings
from app.mail.message import BODY_WIDTH, Mail, MailKind


def _link(path: str, **params: str) -> str:
    """A frontend URL with query values escaped.

    `quote` rather than raw interpolation: a verification token is
    `secrets.token_urlsafe`, which is URL-safe by construction, but an email
    address is not, and `a+b@example.com` silently becomes `a b@example.com`
    through a query string that nobody escaped.
    """
    base = settings.frontend_url.rstrip("/")
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
    return f"{base}{path}?{query}" if query else f"{base}{path}"


def _wrap(text: str) -> str:
    """Re-wrap a paragraph-per-blank-line body to a readable width.

    Written as one call over the whole body rather than per paragraph so the
    templates below can be indented naturally in source and still come out flush
    left in the message.
    """
    paragraphs = [textwrap.dedent(part).strip() for part in text.strip().split("\n\n")]
    wrapped = []
    for paragraph in paragraphs:
        if paragraph.startswith(("http://", "https://")):
            # A URL must survive intact: a wrapped link is a broken link in
            # every mail client that does not re-join it, which is most of them.
            wrapped.append(paragraph)
        else:
            wrapped.append(textwrap.fill(paragraph, width=BODY_WIDTH))
    return "\n\n".join(wrapped) + "\n"


SIGNATURE = "\n--\nKryova\n"


def verify_email(*, to: str, token: str, hours_valid: int) -> Mail:
    link = _link("/verify-email", token=token)
    return Mail(
        to=to,
        subject="Confirm your Kryova email address",
        kind=MailKind.VERIFY_EMAIL,
        sensitive=True,
        body=_wrap(
            f"""
            Somebody created a Kryova account with this address. Confirming it
            lets you create projects; you can sign in and look around without
            it.

            {link}

            The link works once and expires in {hours_valid} hours. If this was
            not you, ignore this message — the account cannot create anything
            until somebody confirms the address, and nobody but you can.
            """
        )
        + SIGNATURE,
    )


def password_reset(*, to: str, token: str, hours_valid: int) -> Mail:
    link = _link("/reset-password", token=token)
    return Mail(
        to=to,
        subject="Reset your Kryova password",
        kind=MailKind.PASSWORD_RESET,
        sensitive=True,
        body=_wrap(
            f"""
            Somebody asked to reset the password on the Kryova account for this
            address.

            {link}

            The link works once and expires in {hours_valid} hours. If you did
            not ask for this, nothing has changed and you do not need to do
            anything — your current password still works and whoever asked did
            not need to know it.
            """
        )
        + SIGNATURE,
    )


def org_invitation(
    *, to: str, organisation: str, inviter: str, token: str, expires_at: datetime
) -> Mail:
    link = _link("/invitations/accept", token=token)
    return Mail(
        to=to,
        subject=f"{inviter} invited you to {organisation} on Kryova",
        kind=MailKind.ORG_INVITATION,
        sensitive=True,
        body=_wrap(
            f"""
            {inviter} invited you to join the organisation "{organisation}" on
            Kryova, an engineering platform for designing and analysing
            machines.

            {link}

            The invitation expires on {expires_at:%d %B %Y}. If you do not have
            a Kryova account, the link will offer to create one for this
            address. If you were not expecting this, ignore it — nothing is
            shared with you until you accept.
            """
        )
        + SIGNATURE,
    )


def impersonation_notice(
    *, to: str, actor: str, reason: str, started_at: datetime, write_mode: bool
) -> Mail:
    """Told *after the fact*, as P3 task 3 requires.

    Sent for a write-mode session and for a read-only one alike. The argument
    for only telling people about writes is that a read changes nothing, and it
    is wrong: somebody at Kryova reading your designs is the event you would
    want to know about whether or not they touched anything.
    """
    powers = "could change things" if write_mode else "could only read"
    return Mail(
        to=to,
        subject="A Kryova staff member accessed your account",
        kind=MailKind.IMPERSONATION_NOTICE,
        body=_wrap(
            f"""
            {actor}, a member of Kryova staff, opened a support session on your
            account on {started_at:%d %B %Y at %H:%M UTC}. During the session
            they {powers}.

            The reason they recorded was: {reason}

            Every action taken during a support session is in your
            organisation's audit log, including this one, and an owner can read
            it in the dashboard. If you did not ask for support and cannot
            account for this, reply to this message.
            """
        )
        + SIGNATURE,
    )


def suspension_notice(*, to: str, reason: str, by: str) -> Mail:
    return Mail(
        to=to,
        subject="Your Kryova account has been suspended",
        kind=MailKind.SUSPENSION_NOTICE,
        body=_wrap(
            f"""
            Your Kryova account has been suspended by {by} and you have been
            signed out everywhere.

            The reason recorded was: {reason}

            Your designs, simulations and files are not deleted and are not
            changed by a suspension. Reply to this message if you think this is
            a mistake.
            """
        )
        + SIGNATURE,
    )


def deletion_scheduled(*, to: str, purge_at: datetime, by: str) -> Mail:
    """The grace-window notice. P3 task 4 — deletion is scheduled, not immediate.

    The date is the whole message. A deletion confirmation with no deadline
    gives the one person who can stop it no reason to act today.
    """
    return Mail(
        to=to,
        subject="Your Kryova account is scheduled for deletion",
        kind=MailKind.DELETION_SCHEDULED,
        body=_wrap(
            f"""
            Your Kryova account and everything in it — designs, simulations,
            uploaded files and conversations — are scheduled to be permanently
            deleted on {purge_at:%d %B %Y}. This was requested by {by}.

            Until that date the deletion can be undone by signing in and
            cancelling it, or by replying to this message. After that date it
            cannot: the files are erased from storage, not flagged, and there is
            no copy to restore from.
            """
        )
        + SIGNATURE,
    )


def session_theft_notice(*, to: str, at: datetime, ip_address: str | None) -> Mail:
    """Sent when reuse detection revokes a family (P1 task 2's other half).

    The detection already worked — the family is dead by the time this is
    built. What it adds is that the *person* finds out, because a revoked family
    presents to them as an unexplained sign-out, and an unexplained sign-out is
    something people shrug at.
    """
    where = f" from {ip_address}" if ip_address else ""
    return Mail(
        to=to,
        subject="You were signed out of Kryova for a security reason",
        kind=MailKind.SESSION_THEFT_NOTICE,
        body=_wrap(
            f"""
            On {at:%d %B %Y at %H:%M UTC} a Kryova sign-in token for your
            account was presented for a second time{where}. A token is only ever
            valid once, so a second use means a copy of it exists somewhere it
            should not.

            We ended every session in that family immediately. Nothing else has
            been changed and your password still works.

            If you were signed out unexpectedly around that time, that is this.
            Sign in again, and change your password if you use it anywhere else.
            """
        )
        + SIGNATURE,
    )


def quota_exhausted(*, to: str, organisation: str, what: str, resets: str) -> Mail:
    return Mail(
        to=to,
        subject=f"{organisation} has used its {what} allowance",
        kind=MailKind.QUOTA_EXHAUSTED,
        body=_wrap(
            f"""
            The organisation "{organisation}" has used all of its {what}
            allowance on Kryova. Work that needs it will be refused until
            {resets}.

            Nothing has been deleted and nothing already finished is affected.
            An owner can raise the allowance in the organisation's billing
            settings.
            """
        )
        + SIGNATURE,
    )


#: Every builder, keyed by the kind it produces. A test walks this to prove the
#: two enumerations agree — a `MailKind` with no builder is a message somebody
#: intended to send and did not, and a builder with no kind cannot be routed.
BUILDERS = {
    MailKind.VERIFY_EMAIL: verify_email,
    MailKind.PASSWORD_RESET: password_reset,
    MailKind.ORG_INVITATION: org_invitation,
    MailKind.IMPERSONATION_NOTICE: impersonation_notice,
    MailKind.SUSPENSION_NOTICE: suspension_notice,
    MailKind.DELETION_SCHEDULED: deletion_scheduled,
    MailKind.SESSION_THEFT_NOTICE: session_theft_notice,
    MailKind.QUOTA_EXHAUSTED: quota_exhausted,
}

__all__ = [
    "BUILDERS",
    "SIGNATURE",
    "deletion_scheduled",
    "impersonation_notice",
    "org_invitation",
    "password_reset",
    "quota_exhausted",
    "session_theft_notice",
    "suspension_notice",
    "verify_email",
]
