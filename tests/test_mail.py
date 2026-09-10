"""The mail seam: what a message is, how it leaves, and what it reports.

No database anywhere in this file — the whole point of `app/mail/` is that a
message can be built and asserted about without one.
"""

from __future__ import annotations

import smtplib
from datetime import datetime, timezone

import pytest

from app.core.config import DELIVERING_MAIL_TRANSPORTS, MAIL_TRANSPORTS, Settings, settings
from app.mail import templates
from app.mail.message import Delivery, DeliveryState, Mail, MailKind, Outbox
from app.mail.transport import (
    ConsoleTransport,
    MemoryTransport,
    SmtpTransport,
    build_transport,
)


class TestAMessageRefusesToBeMalformed:
    def test_a_newline_in_the_subject_is_refused_because_it_would_add_a_header(self) -> None:
        # SMTP headers are newline-separated, so a subject carrying one can
        # append `Bcc:` and turn any message into a mailing list. Nothing takes
        # a subject from user input today; this is pinned so the day something
        # does, it is already refused.
        with pytest.raises(ValueError, match="newline"):
            Mail(
                to="a@example.com",
                subject="Hello\nBcc: everyone@example.com",
                body="hi",
                kind=MailKind.VERIFY_EMAIL,
            )

    def test_a_newline_in_the_recipient_is_refused_for_the_same_reason(self) -> None:
        with pytest.raises(ValueError, match="newline"):
            Mail(
                to="a@example.com\nBcc: everyone@example.com",
                subject="Hello",
                body="hi",
                kind=MailKind.VERIFY_EMAIL,
            )

    def test_a_message_with_no_recipient_is_refused(self) -> None:
        with pytest.raises(ValueError, match="recipient"):
            Mail(to="   ", subject="Hello", body="hi", kind=MailKind.VERIFY_EMAIL)

    def test_a_message_with_no_subject_is_refused(self) -> None:
        with pytest.raises(ValueError, match="subject"):
            Mail(to="a@example.com", subject="", body="hi", kind=MailKind.VERIFY_EMAIL)


class TestDeliveryTellsSentApartFromLogged:
    def test_a_logged_message_has_not_reached_a_mailbox(self) -> None:
        # The distinction the whole `DeliveryState` enum exists for. A
        # development deployment printing an invitation to stdout has not
        # invited anybody, and code that treats a truthy return as "sent" is
        # wrong on every machine without SMTP.
        mail = Mail(to="a@example.com", subject="s", body="b", kind=MailKind.VERIFY_EMAIL)
        assert not Delivery(mail=mail, state=DeliveryState.LOGGED).reached_a_mailbox
        assert Delivery(mail=mail, state=DeliveryState.SENT).reached_a_mailbox
        assert not Delivery(mail=mail, state=DeliveryState.FAILED).reached_a_mailbox


class TestTheTransports:
    def test_the_memory_transport_collects_and_sends_nothing(self) -> None:
        collected = Outbox()
        transport = MemoryTransport(collected)
        message = templates.verify_email(to="a@example.com", token="tok", hours_valid=24)

        delivery = transport.send(message)

        assert delivery.state is DeliveryState.LOGGED
        assert len(collected) == 1
        assert collected.of_kind(MailKind.VERIFY_EMAIL) == [message]
        assert collected.last() is message

    def test_only_the_smtp_transport_claims_to_reach_real_mailboxes(self) -> None:
        assert not MemoryTransport().reaches_real_mailboxes
        assert not ConsoleTransport().reaches_real_mailboxes
        assert SmtpTransport(host="mail.example.com", port=25).reaches_real_mailboxes

    def test_an_smtp_transport_with_no_host_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="SMTP_HOST"):
            SmtpTransport(host="  ", port=587)

    def test_an_unreachable_smtp_server_is_a_failed_delivery_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A registration has already written the user row by the time mail is
        # sent. Raising here would roll it back and 500 on an account that was
        # created correctly, so the failure is data.
        def refuse(*args: object, **kwargs: object) -> None:
            raise smtplib.SMTPConnectError(421, "nope")

        monkeypatch.setattr(smtplib, "SMTP", refuse)
        transport = SmtpTransport(host="mail.example.com", port=25)

        delivery = transport.send(
            templates.password_reset(to="a@example.com", token="t", hours_valid=1)
        )

        assert delivery.state is DeliveryState.FAILED
        assert "SMTPConnectError" in delivery.detail

    def test_a_bug_in_building_the_message_is_not_reported_as_a_delivery_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `send` catches SMTP and OS errors by name. A TypeError from our own
        # code must propagate, or an operator reads "the mail server refused
        # it" and spends an afternoon on a mail server that is fine.
        #
        # This test is also what showed the message had to be built *before*
        # the socket is opened: with `_build` inside the `try`, the connection
        # to a nonexistent host failed first and the bug was never reached.
        transport = SmtpTransport(host="mail.example.com", port=25)
        monkeypatch.setattr(
            transport, "_build", lambda mail: (_ for _ in ()).throw(TypeError("our bug"))
        )
        with pytest.raises(TypeError, match="our bug"):
            transport.send(
                templates.verify_email(to="a@example.com", token="t", hours_valid=1)
            )

    def test_an_unknown_transport_name_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="MAIL_TRANSPORT"):
            build_transport("carrier-pigeon")


class TestProductionRefusesATransportThatDeliversToNobody:
    """The startup guard, in the same class as `SECRET_KEY=changeme` booting."""

    def _production(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "environment": "production",
            "secret_key": "x" * 48,
            "cookie_secure": True,
            "cors_origins": ["https://app.kryova.dev"],
            "mail_transport": "smtp",
            "smtp_host": "mail.example.com",
        }
        base.update(overrides)
        return base

    def test_production_starts_with_a_configured_smtp_transport(self) -> None:
        assert Settings(**self._production()).mail_reaches_real_mailboxes  # type: ignore[arg-type]

    def test_production_refuses_the_console_transport(self) -> None:
        # Left on console, a password reset still returns 204 and the frontend
        # still says "check your email". Every component reports success and
        # the user is locked out with nothing to retry.
        with pytest.raises(ValueError, match="delivers to nobody"):
            Settings(**self._production(mail_transport="console"))  # type: ignore[arg-type]

    def test_production_refuses_the_memory_transport(self) -> None:
        with pytest.raises(ValueError, match="delivers to nobody"):
            Settings(**self._production(mail_transport="memory"))  # type: ignore[arg-type]

    def test_production_refuses_smtp_with_no_host(self) -> None:
        with pytest.raises(ValueError, match="SMTP_HOST is empty"):
            Settings(**self._production(smtp_host=""))  # type: ignore[arg-type]

    def test_development_is_allowed_to_use_the_console_transport(self) -> None:
        # The whole point of it: a developer clicks the link out of the log.
        relaxed = Settings(environment="development", mail_transport="console")
        assert not relaxed.mail_reaches_real_mailboxes

    def test_an_unknown_transport_name_is_a_startup_error_naming_the_valid_ones(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="MAIL_TRANSPORT must be one of"):
            Settings(mail_transport="smpt")

    def test_the_delivering_set_is_a_subset_of_the_known_set(self) -> None:
        # Otherwise production could require a transport `build_transport`
        # cannot build, and the refusal would name a value nothing accepts.
        assert DELIVERING_MAIL_TRANSPORTS <= MAIL_TRANSPORTS


class TestEveryMessageThisProductSends:
    def test_every_kind_has_a_builder_and_every_builder_has_a_kind(self) -> None:
        # A `MailKind` with no builder is a message somebody intended to send
        # and did not; a builder with no kind cannot be routed or filtered.
        assert set(templates.BUILDERS) == set(MailKind)

    @pytest.mark.parametrize("kind", list(MailKind))
    def test_the_builder_produces_the_kind_it_is_filed_under(self, kind: MailKind) -> None:
        assert _build(kind).kind is kind

    @pytest.mark.parametrize("kind", list(MailKind))
    def test_no_message_is_empty_or_unsigned(self, kind: MailKind) -> None:
        message = _build(kind)
        assert message.body.strip()
        assert message.body.rstrip().endswith("Kryova")

    def test_a_link_is_never_wrapped_because_a_wrapped_link_is_a_broken_link(self) -> None:
        message = templates.verify_email(
            to="a@example.com", token="t" * 60, hours_valid=24
        )
        link = [line for line in message.body.splitlines() if line.startswith("http")]
        assert len(link) == 1
        assert link[0].endswith("t" * 60)

    def test_an_address_with_a_plus_survives_the_query_string(self) -> None:
        # `a+b@example.com` becomes `a b@example.com` through an unescaped
        # query string, and the resulting link points at a different account.
        message = templates.org_invitation(
            to="a+b@example.com",
            organisation="Acme",
            inviter="Dana",
            token="tok/en+with/slashes",
            expires_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        assert "tok%2Fen%2Bwith%2Fslashes" in message.body

    def test_messages_carrying_a_credential_are_marked_sensitive(self) -> None:
        carries_a_token = {
            MailKind.VERIFY_EMAIL,
            MailKind.PASSWORD_RESET,
            MailKind.ORG_INVITATION,
        }
        for kind in MailKind:
            assert _build(kind).sensitive is (kind in carries_a_token), kind

    def test_the_impersonation_notice_is_sent_for_a_read_only_session_too(self) -> None:
        # Somebody at Kryova reading your designs is the event you would want to
        # know about whether or not they changed anything.
        read_only = templates.impersonation_notice(
            to="a@example.com",
            actor="support@kryova.dev",
            reason="ticket 41",
            started_at=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc),
            write_mode=False,
        )
        assert "could only read" in read_only.body
        assert "ticket 41" in read_only.body

    def test_the_deletion_notice_names_the_date_it_becomes_irreversible(self) -> None:
        message = templates.deletion_scheduled(
            to="a@example.com",
            purge_at=datetime(2026, 10, 8, tzinfo=timezone.utc),
            by="an administrator",
        )
        assert "08 October 2026" in message.body
        assert "cannot" in message.body


def _build(kind: MailKind) -> Mail:
    """One representative message per kind, for the parametrised checks."""
    at = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)
    made: dict[MailKind, Mail] = {
        MailKind.VERIFY_EMAIL: templates.verify_email(
            to="a@example.com", token="tok", hours_valid=24
        ),
        MailKind.PASSWORD_RESET: templates.password_reset(
            to="a@example.com", token="tok", hours_valid=1
        ),
        MailKind.ORG_INVITATION: templates.org_invitation(
            to="a@example.com",
            organisation="Acme",
            inviter="Dana",
            token="tok",
            expires_at=at,
        ),
        MailKind.IMPERSONATION_NOTICE: templates.impersonation_notice(
            to="a@example.com",
            actor="support@kryova.dev",
            reason="ticket 41",
            started_at=at,
            write_mode=True,
        ),
        MailKind.SUSPENSION_NOTICE: templates.suspension_notice(
            to="a@example.com", reason="abuse", by="an administrator"
        ),
        MailKind.DELETION_SCHEDULED: templates.deletion_scheduled(
            to="a@example.com", purge_at=at, by="the account owner"
        ),
        MailKind.SESSION_THEFT_NOTICE: templates.session_theft_notice(
            to="a@example.com", at=at, ip_address="203.0.113.7"
        ),
        MailKind.QUOTA_EXHAUSTED: templates.quota_exhausted(
            to="a@example.com", organisation="Acme", what="solver-second", resets="1 October"
        ),
    }
    return made[kind]


class TestTheSuiteCannotSendRealMail:
    def test_the_autouse_outbox_is_installed_for_every_test(self, outbox: Outbox) -> None:
        # If this ever fails, some test has installed its own transport and not
        # put it back -- which on a machine with SMTP configured means the
        # suite is emailing real people.
        from app import mail as mail_module

        assert isinstance(mail_module.get_transport(), MemoryTransport)
        assert not mail_module.can_reach_real_mailboxes()
        assert len(outbox) == 0

    def test_settings_default_to_a_transport_that_cannot_reach_anyone(self) -> None:
        assert settings.mail_transport in {"console", "memory"}
