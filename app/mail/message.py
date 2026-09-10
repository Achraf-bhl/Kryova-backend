"""What an email is here, and what happened when we tried to send it.

Two dataclasses and no behaviour, so that the thing being sent can be asserted
about in a test without a transport, a socket or a settings object in sight.

**`Delivery` is returned, never raised.** Registering a user and telling them
about it are two different operations with different failure modes: an SMTP
server that is down must not roll back an account that was created correctly.
But the opposite mistake — swallowing the failure and showing "check your
email" to someone who will never receive one — is worse, because the user waits
instead of retrying. So every send answers with a record: what was attempted,
whether it left, and if not, why. Callers decide what to do with that; nothing
decides for them by throwing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class MailKind(StrEnum):
    """Every message kind, enumerated rather than left as free strings.

    The point is not validation — it is that this class lists everything the
    product will ever put in a stranger's inbox, which is a list somebody
    eventually has to review, and a set of f-strings scattered across route
    handlers is not one.
    """

    VERIFY_EMAIL = "verify_email"
    PASSWORD_RESET = "password_reset"
    ORG_INVITATION = "org_invitation"
    IMPERSONATION_NOTICE = "impersonation_notice"
    SUSPENSION_NOTICE = "suspension_notice"
    DELETION_SCHEDULED = "deletion_scheduled"
    SESSION_THEFT_NOTICE = "session_theft_notice"
    QUOTA_EXHAUSTED = "quota_exhausted"


@dataclass(frozen=True)
class Mail:
    """One message, fully rendered, addressed and ready to hand to a transport.

    Plain text only, deliberately. An HTML alternative doubles the surface that
    has to be escaped correctly and this product sends short transactional
    notes, all of which read fine as text. When a message needs a table, that is
    the moment to add `html` here — not before.

    `sensitive` marks a message whose body contains a credential (a
    verification link, a reset link). The console transport refuses to print
    the body of one outside development, because a token in a log file is a
    password in a log file — the rule `routes/auth.py` already followed by hand
    for password resets, made structural so the next message to carry a token
    inherits it instead of re-deriving it.
    """

    to: str
    subject: str
    body: str
    kind: MailKind
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not self.to.strip():
            raise ValueError("A message needs a recipient")
        if not self.subject.strip():
            raise ValueError("A message needs a subject")
        if "\n" in self.subject or "\r" in self.subject:
            # Header injection: a newline in the subject lets whoever supplied
            # it append `Bcc:` and turn a password reset into a mailing list.
            # Nothing here takes a subject from user input today, which is
            # exactly why this is worth pinning now rather than after something
            # does.
            raise ValueError("A subject cannot contain a newline")
        if "\n" in self.to or "\r" in self.to:
            raise ValueError("A recipient cannot contain a newline")


class DeliveryState(StrEnum):
    SENT = "sent"
    #: The transport accepted it and printed it instead of sending it. Its own
    #: outcome, not `SENT`, because a caller that wants to know whether a real
    #: person can receive this must be able to tell the two apart.
    LOGGED = "logged"
    FAILED = "failed"


@dataclass(frozen=True)
class Delivery:
    """The outcome of one attempt.

    `detail` is for an operator reading a log, not for the user: it can name
    the SMTP host and the server's refusal text. Nothing in it should ever be
    rendered into an API response, and the routes that surface a mail failure
    say "we could not send the email" and log this separately.
    """

    mail: Mail
    state: DeliveryState
    detail: str = ""

    @property
    def reached_a_mailbox(self) -> bool:
        """Whether a real person can be expected to receive this.

        The distinction `LOGGED` exists for. A development deployment printing
        an invitation to stdout has not invited anybody, and a caller that says
        "invitation sent" on the strength of a truthy return value is lying on
        every machine that has not configured SMTP.
        """
        return self.state is DeliveryState.SENT


@dataclass
class Outbox:
    """Everything a `MemoryTransport` was handed, in order.

    Lives here rather than in `transport.py` so a test can build one and assert
    against it without importing the transport machinery, and so the type is
    available to fixtures that never send anything.
    """

    messages: list[Mail] = field(default_factory=list)

    def clear(self) -> None:
        self.messages.clear()

    def of_kind(self, kind: MailKind) -> list[Mail]:
        return [mail for mail in self.messages if mail.kind is kind]

    def last(self) -> Mail | None:
        return self.messages[-1] if self.messages else None

    def __len__(self) -> int:
        return len(self.messages)


#: Wrapped at a width that survives every mail client's own re-wrapping.
BODY_WIDTH: Final = 72

__all__ = [
    "BODY_WIDTH",
    "Delivery",
    "DeliveryState",
    "Mail",
    "MailKind",
    "Outbox",
]
