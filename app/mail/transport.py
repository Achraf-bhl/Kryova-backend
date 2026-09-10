"""How a message actually leaves — or doesn't, and says so.

Three transports, chosen by setting and never guessed:

* **`smtp`** — the real one. Standard library `smtplib` and `email.message`; no
  dependency, because sending a plain-text message over SMTP is a solved
  problem in the standard library and a client library would only add a way for
  it to break.
* **`console`** — development. Writes the message to the log so a developer can
  follow the verification link without running a mail server.
* **`memory`** — tests. Appends to an `Outbox` and sends nothing.

**Production refuses anything but `smtp`, at startup, in `config.py`.** The
failure this guards is specific and has a shape: a deployment left on the
console transport still returns 204 from `/auth/password-reset-request`, still
shows the user "check your email", and still logs a line nobody reads. Every
part of the system reports success and the user is locked out. That is the same
class of failure as `SECRET_KEY=changeme` booting, and it gets the same
treatment — the process does not start.

**No transport raises out of `send`.** Each catches its own failures and
returns a `FAILED` delivery, because the callers are route handlers whose real
work has already succeeded. See `message.Delivery` for why that is a return
value rather than an exception.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from email.message import EmailMessage

from app.core.config import MAIL_TRANSPORTS, settings
from app.mail.message import Delivery, DeliveryState, Mail, Outbox

logger = logging.getLogger(__name__)

#: The transports this build knows, re-exported from `config.py` where the
#: validator needs them. One list, so a typo in `MAIL_TRANSPORT` is a startup
#: error naming the valid values rather than a silent fallback to whichever
#: branch of `build_transport` happened to be last.
TRANSPORTS = MAIL_TRANSPORTS


class Transport(ABC):
    """Somewhere a message can be handed to."""

    @abstractmethod
    def send(self, mail: Mail) -> Delivery:
        """Attempt one message. Never raises; returns what happened."""

    @property
    def reaches_real_mailboxes(self) -> bool:
        """Whether this transport can deliver to a person who is not us.

        Read by `/setup` and the operations dashboard so "email is configured"
        is a measured property of the running process rather than a claim in a
        deployment document.
        """
        return False


class MemoryTransport(Transport):
    """Collects messages for assertions. Sends nothing, ever."""

    def __init__(self, outbox: Outbox | None = None) -> None:
        self.outbox = outbox if outbox is not None else Outbox()

    def send(self, mail: Mail) -> Delivery:
        self.outbox.messages.append(mail)
        return Delivery(mail=mail, state=DeliveryState.LOGGED, detail="collected in memory")


class ConsoleTransport(Transport):
    """Writes the message to the log. Development only — see the module note.

    The body is written in full including any link, which is the point: a
    developer needs to click it. `sensitive` is therefore not honoured by
    suppressing anything here; it is honoured by `config.py` refusing to let
    this transport exist in production at all. Suppressing the token would make
    the transport useless for the one job it has, and would leave the *real*
    hazard — a production deployment on the console transport — untouched.
    """

    def send(self, mail: Mail) -> Delivery:
        logger.info(
            "[mail:%s] to=%s subject=%s\n%s",
            mail.kind.value,
            mail.to,
            mail.subject,
            mail.body,
        )
        return Delivery(mail=mail, state=DeliveryState.LOGGED, detail="written to the log")


class SmtpTransport(Transport):
    """Standard-library SMTP, with STARTTLS by default.

    A connection is opened per message rather than pooled. Kryova's mail volume
    is transactional — a registration, a reset, an invitation — so a pool would
    be an idle socket and a class of stale-connection bugs in exchange for
    nothing measurable. If a bulk path ever appears (it would be an
    announcement fan-out, P3 task 7), that is the moment to revisit, and the
    interface above does not change when it does.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str = "",
        password: str = "",
        starttls: bool = True,
        timeout: float = 15.0,
    ) -> None:
        if not host.strip():
            raise ValueError("SMTP_HOST is empty; the smtp transport has nowhere to connect")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._starttls = starttls
        self._timeout = timeout

    @property
    def reaches_real_mailboxes(self) -> bool:
        return True

    def _build(self, mail: Mail) -> EmailMessage:
        message = EmailMessage()
        message["From"] = settings.mail_from
        message["To"] = mail.to
        message["Subject"] = mail.subject
        # Lets a receiving system — and our own bounce handling, later — sort
        # transactional classes without parsing the subject line.
        message["X-Kryova-Kind"] = mail.kind.value
        if mail.sensitive:
            # Asks well-behaved intermediaries not to keep a copy. It is a
            # request, not a guarantee, and is not load-bearing anywhere.
            message["Sensitivity"] = "Private"
        message.set_content(mail.body)
        return message

    def send(self, mail: Mail) -> Delivery:
        # Built before the socket is opened, on purpose. Rendering needs no
        # connection, so a message this code cannot build should not cost a TCP
        # handshake to find out -- and keeping it outside the `try` is what
        # stops a bug in `_build` being caught by the handler below and
        # reported to an operator as "the mail server refused it".
        message = self._build(mail)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
                if self._starttls:
                    client.starttls(context=ssl.create_default_context())
                if self._username:
                    client.login(self._username, self._password)
                client.send_message(message)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Named exception types rather than `Exception`: a bug in `_build`
            # is not a delivery failure and must not be reported as one, or the
            # operator reads "the mail server refused it" and spends an
            # afternoon on a mail server that is fine.
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "mail delivery failed", extra={"kind": mail.kind.value, "detail": detail}
            )
            return Delivery(mail=mail, state=DeliveryState.FAILED, detail=detail)
        return Delivery(mail=mail, state=DeliveryState.SENT)


_transport: Transport | None = None


def build_transport(name: str) -> Transport:
    """The transport `name` asks for. Raises on an unknown name."""
    if name == "memory":
        return MemoryTransport()
    if name == "console":
        return ConsoleTransport()
    if name == "smtp":
        return SmtpTransport(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            starttls=settings.smtp_starttls,
        )
    raise ValueError(f"MAIL_TRANSPORT must be one of {', '.join(TRANSPORTS)}; got {name!r}")


def get_transport() -> Transport:
    """The process-wide transport, built once from settings."""
    global _transport
    if _transport is None:
        _transport = build_transport(settings.mail_transport)
    return _transport


def use_transport(transport: Transport | None) -> None:
    """Install a transport, or clear it so the next call rebuilds from settings.

    Exists for tests and for `/setup`'s send-a-test-message check. It is a
    module-level global rather than a dependency because mail is sent from
    places that have no request in scope (the deletion purge job, the
    announcement fan-out), and threading a transport through those would put a
    parameter nobody reads on every function between here and there.
    """
    global _transport
    _transport = transport


__all__ = [
    "TRANSPORTS",
    "ConsoleTransport",
    "MemoryTransport",
    "SmtpTransport",
    "Transport",
    "build_transport",
    "get_transport",
    "use_transport",
]
