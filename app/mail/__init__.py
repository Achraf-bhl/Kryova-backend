"""Transactional email: what we send, how it leaves, and what happened.

`message.py` is the shape, `templates.py` is every message this product sends,
`transport.py` is how it leaves. This module is the one function callers use.

    from app import mail
    delivery = mail.send(mail.templates.verify_email(to=..., token=..., hours_valid=24))
    if not delivery.reached_a_mailbox:
        ...

**Why there is no `MailService` injected through `Depends`.** Mail is sent from
places that have no request in scope — the deletion purge job, the announcement
fan-out, the session-theft notice raised inside `rotate_session` — and threading
a service through every function between a route and those would add a parameter
that most of the path only forwards. The transport is process-wide and swapped
with `transport.use_transport`, which is what the fixtures do.
"""

from __future__ import annotations

import logging

from app.mail import templates
from app.mail.message import Delivery, DeliveryState, Mail, MailKind, Outbox
from app.mail.transport import (
    ConsoleTransport,
    MemoryTransport,
    SmtpTransport,
    Transport,
    get_transport,
    use_transport,
)

logger = logging.getLogger(__name__)


def send(mail: Mail) -> Delivery:
    """Hand one message to the configured transport.

    Never raises, including on a transport that fails to *build* — a deployment
    with `MAIL_TRANSPORT=smtp` and an empty `SMTP_HOST` is refused at startup in
    production, but a development machine can reach here with a broken
    configuration, and a registration that 500s because mail is misconfigured is
    a worse outcome than one that succeeds and reports the mail failed.
    """
    try:
        transport = get_transport()
    except (ValueError, TypeError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("no usable mail transport", extra={"detail": detail})
        return Delivery(mail=mail, state=DeliveryState.FAILED, detail=detail)
    return transport.send(mail)


def can_reach_real_mailboxes() -> bool:
    """Whether the configured transport delivers to people who are not us.

    Asked by `/setup` and the operations dashboard. Reads the transport rather
    than the setting so a transport installed by a test or by a runtime swap is
    answered for honestly.
    """
    try:
        return get_transport().reaches_real_mailboxes
    except (ValueError, TypeError):
        return False


__all__ = [
    "ConsoleTransport",
    "Delivery",
    "DeliveryState",
    "Mail",
    "MailKind",
    "MemoryTransport",
    "Outbox",
    "SmtpTransport",
    "Transport",
    "can_reach_real_mailboxes",
    "get_transport",
    "send",
    "templates",
    "use_transport",
]
