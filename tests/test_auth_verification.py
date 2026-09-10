"""Email verification and the second factor, driven through the API (P1.5, P1.7).

`tests/test_totp.py` proves the arithmetic. This file proves the *product*: that
a link arrives, works once, expires; that an unverified account is stopped at
the one gate it should be stopped at and nowhere else; and that an account with
a second factor cannot be signed into with a password alone.
"""

from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.api.rate_limit import auth_limiter
from app.core import email_verification, mfa
from app.core.config import settings
from app.core.totp import code_at, step_for
from app.mail.message import MailKind, Outbox
from app.models import RecoveryCode, TotpEnrolment, User
from tests.typing import AuthenticatedTestClient

PASSWORD = "correct-horse-battery"


def _register(client: AuthenticatedTestClient, email: str = "new@kryova.dev") -> str:
    auth_limiter.reset()
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    user_id: str = response.json()["id"]
    return user_id


def _token_from(outbox: Outbox, kind: MailKind = MailKind.VERIFY_EMAIL) -> str:
    """Pull the credential out of the most recent message of a kind.

    Reads the link the way a person does — out of the body — rather than from
    the database, because the hash is all the database has and a link the user
    cannot use is exactly the failure worth catching.
    """
    messages = outbox.of_kind(kind)
    assert messages, f"no {kind.value} message was sent"
    found = re.search(r"https?://\S+", messages[-1].body)
    assert found, messages[-1].body
    query = parse_qs(urlparse(found.group(0)).query)
    return query["token"][0]


def _age_past_the_window(db_session, user_id: str) -> User:
    """Move the last-sent stamp back so a resend is due.

    Reaching into the row rather than sleeping: the throttle is a duration, and
    a test that waits for one is a test nobody runs.
    """
    user = db_session.get(User, user_id)
    assert user is not None and user.email_verification_sent_at is not None
    user.email_verification_sent_at -= timedelta(
        seconds=settings.email_verification_resend_seconds + 1
    )
    db_session.flush()
    return user


def _sign_in(client: AuthenticatedTestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login", data={"username": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = client.cookies["kryova_csrf"]


class TestRegistrationSendsALinkThatWorks:
    def test_registering_sends_exactly_one_verification_message(
        self, client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        _register(client)
        assert len(outbox.of_kind(MailKind.VERIFY_EMAIL)) == 1
        assert outbox.of_kind(MailKind.VERIFY_EMAIL)[0].to == "new@kryova.dev"

    def test_the_emailed_link_verifies_the_account(
        self, client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        user_id = _register(client)
        token = _token_from(outbox)

        response = client.post("/api/v1/auth/verify-email", json={"token": token})

        assert response.status_code == 200, response.text
        assert response.json()["id"] == user_id
        assert response.json()["is_verified"] is True

    def test_the_link_works_once(
        self, client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        # A confirmation forwarded to a mailing list must not be replayable.
        _register(client)
        token = _token_from(outbox)
        assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200

        again = client.post("/api/v1/auth/verify-email", json={"token": token})

        assert again.status_code == 422
        assert "expired" in again.json()["detail"]

    def test_an_unknown_token_is_refused_without_saying_whether_one_exists(
        self, client: AuthenticatedTestClient
    ) -> None:
        auth_limiter.reset()
        response = client.post("/api/v1/auth/verify-email", json={"token": "not-a-token"})
        assert response.status_code == 422

    def test_a_link_older_than_its_lifetime_is_refused_and_cleared(
        self, client: AuthenticatedTestClient, outbox: Outbox, db_session
    ) -> None:
        user_id = _register(client)
        token = _token_from(outbox)
        user = db_session.get(User, user_id)
        assert user.email_verification_sent_at is not None
        user.email_verification_sent_at -= timedelta(
            hours=settings.email_verification_ttl_hours + 1
        )
        db_session.flush()

        response = client.post("/api/v1/auth/verify-email", json={"token": token})

        assert response.status_code == 422
        db_session.refresh(user)
        # Cleared, so a found link cannot be probed forever afterwards.
        assert user.email_verification_token_hash is None


class TestTheResendThrottle:
    def test_registering_starts_the_window_so_an_immediate_resend_sends_nothing(
        self, client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        # The message registration sent counts. Without this, "register, then
        # resend" is two messages for one action and the throttle starts a
        # message late.
        _register(client)
        _sign_in(client, "new@kryova.dev")
        outbox.clear()

        response = client.post("/api/v1/auth/verify-email/resend")

        assert response.status_code == 200
        assert len(outbox.of_kind(MailKind.VERIFY_EMAIL)) == 0
        assert response.json()["can_resend_in_seconds"] > 0

    def test_a_second_request_inside_the_window_sends_nothing(
        self, client: AuthenticatedTestClient, outbox: Outbox, db_session
    ) -> None:
        # The abuse this stops is using Kryova as a free way to post mail at
        # somebody: sign up with their address, then hit resend.
        user_id = _register(client)
        _sign_in(client, "new@kryova.dev")
        _age_past_the_window(db_session, user_id)
        outbox.clear()

        first = client.post("/api/v1/auth/verify-email/resend")
        second = client.post("/api/v1/auth/verify-email/resend")

        assert first.status_code == 200
        assert second.status_code == 200
        assert len(outbox.of_kind(MailKind.VERIFY_EMAIL)) == 1
        assert second.json()["can_resend_in_seconds"] > 0

    def test_a_resend_after_the_window_sends_again(
        self, client: AuthenticatedTestClient, outbox: Outbox, db_session
    ) -> None:
        user_id = _register(client)
        _sign_in(client, "new@kryova.dev")
        outbox.clear()
        user = db_session.get(User, user_id)
        user.email_verification_sent_at -= timedelta(
            seconds=settings.email_verification_resend_seconds + 1
        )
        db_session.flush()

        assert client.post("/api/v1/auth/verify-email/resend").status_code == 200

        assert len(outbox.of_kind(MailKind.VERIFY_EMAIL)) == 1

    def test_a_resend_replaces_the_outstanding_link_rather_than_adding_one(
        self, client: AuthenticatedTestClient, outbox: Outbox, db_session
    ) -> None:
        # Two live links to one account doubles the window a leaked one is
        # usable in, and the older one keeping its power is the exact situation
        # a resend exists to escape.
        user_id = _register(client)
        _sign_in(client, "new@kryova.dev")
        first_token = _token_from(outbox)
        user = db_session.get(User, user_id)
        user.email_verification_sent_at -= timedelta(
            seconds=settings.email_verification_resend_seconds + 1
        )
        db_session.flush()
        outbox.clear()
        client.post("/api/v1/auth/verify-email/resend")
        second_token = _token_from(outbox)

        assert first_token != second_token
        stale = client.post("/api/v1/auth/verify-email", json={"token": first_token})
        assert stale.status_code == 422
        fresh = client.post("/api/v1/auth/verify-email", json={"token": second_token})
        assert fresh.status_code == 200

    def test_an_already_verified_account_is_told_so_and_sends_nothing(
        self, auth_client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        outbox.clear()
        response = auth_client.post("/api/v1/auth/verify-email/resend")
        assert response.status_code == 200
        assert response.json()["verified"] is True
        assert len(outbox.of_kind(MailKind.VERIFY_EMAIL)) == 0

    def test_the_status_says_whether_this_deployment_can_send_at_all(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        # On the memory transport nothing reaches a mailbox, and the UI has to
        # offer "ask an administrator" rather than a resend button that does
        # nothing visible.
        response = auth_client.get("/api/v1/auth/verify-email")
        assert response.status_code == 200
        assert response.json()["delivery_available"] is False


class TestVerificationGatesProjectsAndNothingElse:
    def test_an_unverified_account_cannot_create_a_project(
        self, client: AuthenticatedTestClient
    ) -> None:
        _register(client)
        _sign_in(client, "new@kryova.dev")

        response = client.post("/api/v1/projects", json={"name": "Bracket"})

        assert response.status_code == 403
        assert "Confirm your email" in response.json()["detail"]

    def test_an_unverified_account_can_still_sign_in_and_look_around(
        self, client: AuthenticatedTestClient
    ) -> None:
        # Friction where it protects, not where it annoys. Somebody who
        # mistyped their address must be able to get in and fix it.
        _register(client)
        _sign_in(client, "new@kryova.dev")

        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.get("/api/v1/projects").status_code == 200
        assert client.get("/api/v1/auth/sessions").status_code == 200

    def test_verifying_then_creating_a_project_works(
        self, client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        _register(client)
        client.post("/api/v1/auth/verify-email", json={"token": _token_from(outbox)})
        _sign_in(client, "new@kryova.dev")

        assert client.post("/api/v1/projects", json={"name": "Bracket"}).status_code == 201

    def test_the_gate_can_be_turned_off_for_a_deployment_with_no_mail(
        self, client: AuthenticatedTestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _register(client)
        _sign_in(client, "new@kryova.dev")
        monkeypatch.setattr(settings, "require_verified_email_for_projects", False)

        assert client.post("/api/v1/projects", json={"name": "Bracket"}).status_code == 201


class TestTheSecondFactorAtSignIn:
    def _enrol(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> tuple[str, list[str]]:
        started = auth_client.post("/api/v1/auth/mfa")
        assert started.status_code == 201, started.text
        secret = started.json()["secret"]
        codes = started.json()["recovery_codes"]
        now = mfa.utcnow()
        confirmed = auth_client.post(
            "/api/v1/auth/mfa/confirm",
            json={"code": code_at(secret, step_for(now.timestamp()))},
        )
        assert confirmed.status_code == 200, confirmed.text
        return secret, codes

    def test_enrolment_is_not_a_second_factor_until_it_is_confirmed(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        # A user who scans a broken QR code, or scans nothing, must not be
        # locked out of their own account by their own half-finished setup.
        auth_client.post("/api/v1/auth/mfa")
        status = auth_client.get("/api/v1/auth/mfa").json()

        assert status["enabled"] is False
        assert status["pending"] is True

        auth_client.post("/api/v1/auth/logout")
        auth_limiter.reset()
        response = auth_client.post(
            "/api/v1/auth/login", data={"username": "eng@kryova.dev", "password": PASSWORD}
        )
        assert response.status_code == 200
        assert "user" in response.json()

    def test_a_wrong_code_leaves_the_enrolment_usable_rather_than_destroying_it(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        started = auth_client.post("/api/v1/auth/mfa")
        secret = started.json()["secret"]

        refused = auth_client.post("/api/v1/auth/mfa/confirm", json={"code": "000000"})
        assert refused.status_code == 422

        now = mfa.utcnow()
        accepted = auth_client.post(
            "/api/v1/auth/mfa/confirm",
            json={"code": code_at(secret, step_for(now.timestamp()))},
        )
        assert accepted.status_code == 200

    def test_a_password_alone_no_longer_signs_in_and_sets_no_cookies(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        self._enrol(auth_client, db_session)
        auth_client.post("/api/v1/auth/logout")
        auth_client.cookies.clear()
        auth_limiter.reset()

        response = auth_client.post(
            "/api/v1/auth/login", data={"username": "eng@kryova.dev", "password": PASSWORD}
        )

        assert response.status_code == 202
        body = response.json()
        assert body["mfa_required"] is True
        assert body["recovery_available"] is True
        assert "kryova_access" not in response.cookies
        assert "kryova_refresh" not in response.cookies

    def test_the_challenge_token_is_not_accepted_as_a_credential_anywhere(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        # It grants nothing: `_decode_typed` refuses its type everywhere a
        # session is expected. Without that check it would be a five-minute
        # full-privilege bearer token handed out for a correct password alone.
        self._enrol(auth_client, db_session)
        auth_client.post("/api/v1/auth/logout")
        auth_client.cookies.clear()
        auth_limiter.reset()
        challenge = auth_client.post(
            "/api/v1/auth/login", data={"username": "eng@kryova.dev", "password": PASSWORD}
        ).json()["challenge_token"]

        response = auth_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {challenge}"}
        )

        assert response.status_code == 401

    def test_a_valid_code_finishes_the_sign_in(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        secret, _ = self._enrol(auth_client, db_session)
        auth_client.post("/api/v1/auth/logout")
        auth_client.cookies.clear()
        auth_limiter.reset()
        challenge = auth_client.post(
            "/api/v1/auth/login", data={"username": "eng@kryova.dev", "password": PASSWORD}
        ).json()["challenge_token"]

        # A step ahead of the one enrolment burned, which is what a user reading
        # their phone thirty seconds later actually presents.
        step = step_for(mfa.utcnow().timestamp()) + 1
        response = auth_client.post(
            "/api/v1/auth/login/mfa",
            json={"challenge_token": challenge, "code": code_at(secret, step)},
        )

        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == "eng@kryova.dev"

    def test_a_recovery_code_finishes_the_sign_in_and_is_then_spent(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        _, codes = self._enrol(auth_client, db_session)
        auth_client.post("/api/v1/auth/logout")
        auth_client.cookies.clear()

        for expected in (200, 401):
            auth_limiter.reset()
            challenge = auth_client.post(
                "/api/v1/auth/login",
                data={"username": "eng@kryova.dev", "password": PASSWORD},
            ).json()["challenge_token"]
            response = auth_client.post(
                "/api/v1/auth/login/mfa",
                json={"challenge_token": challenge, "code": codes[0]},
            )
            assert response.status_code == expected, response.text
            auth_client.cookies.clear()

    def test_a_wrong_code_is_refused_without_saying_which_way_it_was_wrong(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        # "That code was already used" tells an attacker holding a captured
        # code that they have the right code and the wrong moment.
        secret, _ = self._enrol(auth_client, db_session)
        auth_client.post("/api/v1/auth/logout")
        auth_client.cookies.clear()
        auth_limiter.reset()
        challenge = auth_client.post(
            "/api/v1/auth/login", data={"username": "eng@kryova.dev", "password": PASSWORD}
        ).json()["challenge_token"]

        burned = auth_client.post(
            "/api/v1/auth/login/mfa",
            json={
                "challenge_token": challenge,
                "code": code_at(secret, step_for(mfa.utcnow().timestamp())),
            },
        )
        nonsense = auth_client.post(
            "/api/v1/auth/login/mfa",
            json={"challenge_token": challenge, "code": "000000"},
        )

        assert burned.status_code == 401
        assert nonsense.status_code == 401
        assert burned.json()["detail"] == nonsense.json()["detail"]

    def test_an_expired_challenge_is_refused(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        self._enrol(auth_client, db_session)
        auth_limiter.reset()
        response = auth_client.post(
            "/api/v1/auth/login/mfa",
            json={"challenge_token": "not-a-token", "code": "123456"},
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"]


class TestManagingTheSecondFactor:
    def test_a_live_factor_cannot_be_silently_replaced(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        # Otherwise anyone with a live session swaps the factor for their own
        # and locks the owner out -- the takeover the factor exists to stop.
        TestTheSecondFactorAtSignIn()._enrol(auth_client, db_session)

        response = auth_client.post("/api/v1/auth/mfa")

        assert response.status_code == 409
        assert "already on" in response.json()["detail"]

    def test_turning_it_off_requires_a_code(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        # A hijacked session must not be able to remove the defence that exists
        # for hijacked sessions.
        TestTheSecondFactorAtSignIn()._enrol(auth_client, db_session)

        refused = auth_client.request(
            "DELETE", "/api/v1/auth/mfa", json={"code": "000000"}
        )

        assert refused.status_code == 422
        assert auth_client.get("/api/v1/auth/mfa").json()["enabled"] is True

    def test_turning_it_off_removes_the_recovery_codes_too(
        self, auth_client: AuthenticatedTestClient, db_session, current_user_id: str
    ) -> None:
        # Leaving them would mean an account with no second factor still had
        # ten standing credentials that skip one.
        _, codes = TestTheSecondFactorAtSignIn()._enrol(auth_client, db_session)

        removed = auth_client.request("DELETE", "/api/v1/auth/mfa", json={"code": codes[0]})

        assert removed.status_code == 204
        assert auth_client.get("/api/v1/auth/mfa").json()["enabled"] is False
        assert (
            db_session.scalars(
                select(RecoveryCode).where(RecoveryCode.user_id == current_user_id)
            ).all()
            == []
        )
        assert (
            db_session.scalar(
                select(TotpEnrolment).where(TotpEnrolment.user_id == current_user_id)
            )
            is None
        )

    def test_regenerating_recovery_codes_invalidates_the_old_set(
        self, auth_client: AuthenticatedTestClient, db_session
    ) -> None:
        _, old = TestTheSecondFactorAtSignIn()._enrol(auth_client, db_session)

        response = auth_client.post("/api/v1/auth/mfa/recovery-codes")

        assert response.status_code == 200
        new = response.json()["recovery_codes"]
        assert set(new).isdisjoint(old)
        assert auth_client.get("/api/v1/auth/mfa").json()["recovery_codes_remaining"] == len(new)

    def test_recovery_codes_cannot_be_regenerated_without_an_enrolment(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        response = auth_client.post("/api/v1/auth/mfa/recovery-codes")
        assert response.status_code == 422

    def test_the_secret_is_not_stored_in_the_clear(
        self, auth_client: AuthenticatedTestClient, db_session, current_user_id: str
    ) -> None:
        secret = auth_client.post("/api/v1/auth/mfa").json()["secret"]
        db_session.flush()

        stored = db_session.scalar(
            select(TotpEnrolment).where(TotpEnrolment.user_id == current_user_id)
        )

        assert stored is not None
        assert secret not in stored.secret_encrypted

    def test_a_recovery_code_is_not_stored_in_the_clear(
        self, auth_client: AuthenticatedTestClient, db_session, current_user_id: str
    ) -> None:
        codes = auth_client.post("/api/v1/auth/mfa").json()["recovery_codes"]
        db_session.flush()

        stored = db_session.scalars(
            select(RecoveryCode).where(RecoveryCode.user_id == current_user_id)
        ).all()

        hashes = {row.code_hash for row in stored}
        assert len(hashes) == len(codes)
        for code in codes:
            assert code not in hashes


class TestChangingAPasswordEndsEveryDevice:
    def test_a_password_reset_revokes_the_live_session_families(
        self, auth_client: AuthenticatedTestClient, db_session, current_user_id: str
    ) -> None:
        # This was the defect: the reset cleared `refresh_token_hash`, a column
        # nothing has read since P1.1, and left every device family alive. A
        # password changed *because* it was stolen did not sign the thief out.
        assert len(auth_client.get("/api/v1/auth/sessions").json()) == 1
        user = db_session.get(User, current_user_id)
        raw = "reset-me-please"
        from app.core.security import hash_token

        user.password_reset_token_hash = hash_token(raw)
        user.password_reset_expires_at = email_verification.utcnow() + timedelta(hours=1)
        db_session.flush()
        auth_limiter.reset()

        response = auth_client.post(
            "/api/v1/auth/password-reset",
            json={"token": raw, "new_password": "a-brand-new-password"},
        )

        assert response.status_code == 204
        assert auth_client.post("/api/v1/auth/refresh").status_code == 401
