"""Refresh-token families, rotation and reuse detection — master plan P1.1/P1.2.

**What was wrong before, measured rather than asserted.** `User.refresh_token_hash`
was one hash per user, so a person signed in on two devices had one session
between them: the second login overwrote the first's hash, and the first learned
about it at its next refresh as an indistinguishable "Invalid refresh token".
There was also no reuse detection at all — a stolen token and the real one wrote
to the same slot, so whoever refreshed last simply won, and nothing anywhere
noticed that a token had been used twice.

The tests here are deliberately written against `app/core/sessions.py` rather
than through HTTP wherever the question is about the state machine. A refusal
tested through the API is tested at one remove, through a layer that returns the
same 401 for four different reasons — which is correct for the client and
useless for a test. The HTTP tests below cover what only HTTP can: cookies,
CSRF, status codes, and cross-user isolation.

The grace window (`REUSE_GRACE_SECONDS`) is the one number here that is a
judgement rather than a derivation, so it is exercised from both sides: inside
it a repeat is a race and must be served, outside it the same bytes are theft
and must kill the family. A test that only checked one side would pass with the
window set to zero or to a year.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.security import hash_token
from app.core.sessions import (
    SessionError,
    device_label,
    live_sessions,
    revoke_all,
    revoke_session,
    rotate_session,
    start_session,
)
from app.models import SessionRevocation, User, UserSession
from app.models.base import utcnow
from app.models.session import REUSE_GRACE_SECONDS


@pytest.fixture()
def user(db_session) -> User:
    person = User(
        email="sessions@example.com",
        hashed_password="not-a-real-hash",
        full_name="Session Tester",
    )
    db_session.add(person)
    db_session.flush()
    return person


class TestOneDeviceIsOneFamily:
    def test_signing_in_twice_leaves_two_live_sessions(self, db_session, user) -> None:
        """The defect this phase exists to fix, stated as a test."""
        laptop = start_session(db_session, user, user_agent="Mozilla/5.0 (Macintosh)")
        phone = start_session(db_session, user, user_agent="Mozilla/5.0 (iPhone)")
        db_session.flush()

        assert laptop.session.family_id != phone.session.family_id
        assert len(live_sessions(db_session, user)) == 2

    def test_the_first_device_still_refreshes_after_the_second_signs_in(
        self, db_session, user
    ) -> None:
        laptop = start_session(db_session, user)
        db_session.flush()
        start_session(db_session, user)
        db_session.flush()

        rotated = rotate_session(db_session, laptop.token)

        assert rotated.session.id == laptop.session.id

    def test_rotation_stays_in_the_same_family(self, db_session, user) -> None:
        first = start_session(db_session, user)
        db_session.flush()
        second = rotate_session(db_session, first.token)
        third = rotate_session(db_session, second.token)

        assert third.session.family_id == first.session.family_id
        assert third.session.id == first.session.id

    def test_the_token_is_never_stored_in_the_clear(self, db_session, user) -> None:
        issued = start_session(db_session, user)
        db_session.flush()

        assert issued.session.token_hash != issued.token
        assert issued.session.token_hash == hash_token(issued.token)


class TestRotation:
    def test_each_refresh_returns_a_new_token(self, db_session, user) -> None:
        first = start_session(db_session, user)
        db_session.flush()
        second = rotate_session(db_session, first.token)

        assert second.token != first.token

    def test_the_old_token_becomes_the_previous_hash(self, db_session, user) -> None:
        """Kept on purpose: this is the reuse detector, not a leftover."""
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)

        assert first.session.previous_token_hash == hash_token(first.token)

    def test_an_unknown_token_is_refused(self, db_session, user) -> None:
        start_session(db_session, user)
        db_session.flush()

        with pytest.raises(SessionError):
            rotate_session(db_session, "not-a-token-anyone-issued")

    def test_rotation_does_not_extend_the_absolute_deadline(self, db_session, user) -> None:
        """A chain that rotates perfectly for ever must still end."""
        first = start_session(db_session, user)
        db_session.flush()
        deadline = first.session.absolute_expires_at

        rotate_session(db_session, first.token)

        assert first.session.absolute_expires_at == deadline

    def test_a_session_past_its_deadline_is_refused_and_recorded(
        self, db_session, user
    ) -> None:
        issued = start_session(db_session, user)
        db_session.flush()
        issued.session.absolute_expires_at = utcnow() - timedelta(seconds=1)

        with pytest.raises(SessionError):
            rotate_session(db_session, issued.token)

        assert issued.session.revoked_reason == SessionRevocation.EXPIRED.value


class TestReuseDetection:
    def test_replaying_a_rotated_token_revokes_the_family(self, db_session, user) -> None:
        """Rotation without this half is theatre — the plan's own words."""
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        # Move the rotation into the past so the replay is outside the window
        # that exists for racing tabs.
        first.session.rotated_at = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 1)

        with pytest.raises(SessionError) as refused:
            rotate_session(db_session, first.token)

        assert refused.value.compromised is True
        assert first.session.revoked_at is not None
        assert first.session.revoked_reason == SessionRevocation.TOKEN_REUSE.value
        assert first.session.compromised is True

    def test_the_legitimate_client_is_signed_out_too(self, db_session, user) -> None:
        """The whole family dies, which is the point: the thief holds a token
        from this family and nobody can tell which of the two is the thief."""
        first = start_session(db_session, user)
        db_session.flush()
        current = rotate_session(db_session, first.token)
        first.session.rotated_at = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 1)

        with pytest.raises(SessionError):
            rotate_session(db_session, first.token)

        with pytest.raises(SessionError):
            rotate_session(db_session, current.token)

    def test_other_devices_are_untouched(self, db_session, user) -> None:
        """A theft on one device must not sign the person out of the others —
        that would make the control so disruptive it gets turned off."""
        laptop = start_session(db_session, user)
        phone = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, laptop.token)
        laptop.session.rotated_at = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 1)

        with pytest.raises(SessionError):
            rotate_session(db_session, laptop.token)

        assert rotate_session(db_session, phone.token) is not None

    def test_replaying_again_is_still_reported_as_theft(self, db_session, user) -> None:
        """The revoked row keeps its previous hash, so a token replayed a second
        time is recognised a second time. That is the behaviour to want: the
        family is already dead, and the alternative — forgetting the hash on
        revocation — would turn the third replay into an anonymous "invalid
        token" and lose the signal that someone is still using stolen credentials.
        """
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        first.session.rotated_at = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 1)
        with pytest.raises(SessionError):
            rotate_session(db_session, first.token)

        with pytest.raises(SessionError) as again:
            rotate_session(db_session, first.token)

        assert again.value.compromised is True

    def test_a_token_nobody_issued_is_not_reported_as_theft(
        self, db_session, user
    ) -> None:
        """Only a *replay* is a theft signal. A forged or expired-and-swept token
        matches nothing, and reporting it as compromised would tell every user
        with a stale cookie that they had been attacked."""
        start_session(db_session, user)
        db_session.flush()

        with pytest.raises(SessionError) as refused:
            rotate_session(db_session, "a-token-from-nowhere")

        assert refused.value.compromised is False


class TestTheGraceWindow:
    def test_two_tabs_refreshing_together_are_served(self, db_session, user) -> None:
        """A retried request or a second tab presents the same token twice
        within a second. Treating that as theft signs people out for having
        flaky wifi."""
        first = start_session(db_session, user)
        db_session.flush()
        current = rotate_session(db_session, first.token)

        raced = rotate_session(db_session, first.token)

        assert first.session.revoked_at is None
        assert raced.session.id == current.session.id

    def test_the_racing_caller_gets_the_current_token_not_a_new_one(
        self, db_session, user
    ) -> None:
        """Rotating here would give the two tabs two different valid tokens, and
        the loser's next refresh would look exactly like an attack."""
        first = start_session(db_session, user)
        db_session.flush()
        current = rotate_session(db_session, first.token)

        raced = rotate_session(db_session, first.token)

        assert current.session.token_hash == hash_token(current.token)
        assert raced.token == first.token
        assert current.session.token_hash != hash_token(raced.token)

    def test_outside_the_window_the_same_bytes_are_theft(self, db_session, user) -> None:
        """Same token, same family, different verdict — the window is the whole
        of the difference, so it is exercised from both sides."""
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        first.session.rotated_at = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 1)

        with pytest.raises(SessionError):
            rotate_session(db_session, first.token)

    def test_an_hour_later_is_theft_whatever_the_constant_says(
        self, db_session, user
    ) -> None:
        """Stated in engineering terms rather than relative to the constant.

        The tests above age the family by `REUSE_GRACE_SECONDS + 1`, so they
        follow the constant wherever it is set — widening the window to a day
        left every one of them passing, which was measured, not guessed. A
        replay an hour after rotation is a replay under any defensible value of
        the window, and this fails if the window is ever set somewhere absurd.
        """
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        first.session.rotated_at = utcnow() - timedelta(hours=1)

        with pytest.raises(SessionError) as refused:
            rotate_session(db_session, first.token)

        assert refused.value.compromised is True

    def test_one_second_later_is_a_race_whatever_the_constant_says(
        self, db_session, user
    ) -> None:
        """The other side of the same clamp: a window of zero would sign people
        out for having two tabs open, and no defensible value does that."""
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        first.session.rotated_at = utcnow() - timedelta(seconds=1)

        raced = rotate_session(db_session, first.token)

        assert first.session.revoked_at is None
        assert raced.token == first.token

    def test_a_revoked_family_does_not_serve_the_grace_window(
        self, db_session, user
    ) -> None:
        """Otherwise "sign out everywhere" would keep working for ten seconds."""
        first = start_session(db_session, user)
        db_session.flush()
        rotate_session(db_session, first.token)
        revoke_all(db_session, user, SessionRevocation.LOGOUT_ALL)

        with pytest.raises(SessionError):
            rotate_session(db_session, first.token)


class TestRevocation:
    def test_signing_out_one_device_leaves_the_others(self, db_session, user) -> None:
        laptop = start_session(db_session, user)
        phone = start_session(db_session, user)
        db_session.flush()

        revoke_session(db_session, laptop.session, SessionRevocation.LOGOUT)

        assert [row.id for row in live_sessions(db_session, user)] == [phone.session.id]

    def test_signing_out_everywhere_ends_all_of_them(self, db_session, user) -> None:
        start_session(db_session, user)
        start_session(db_session, user)
        db_session.flush()

        ended = revoke_all(db_session, user, SessionRevocation.LOGOUT_ALL)

        assert ended == 2
        assert live_sessions(db_session, user) == []

    def test_a_revoked_session_cannot_refresh(self, db_session, user) -> None:
        """The row is the truth, not the cookie."""
        issued = start_session(db_session, user)
        db_session.flush()
        revoke_session(db_session, issued.session, SessionRevocation.LOGOUT)

        with pytest.raises(SessionError):
            rotate_session(db_session, issued.token)

    def test_the_first_reason_survives_a_later_revocation(self, db_session, user) -> None:
        """A family ended for token reuse and then swept by an expiry job must
        still read as compromised, or housekeeping erases the evidence."""
        issued = start_session(db_session, user)
        db_session.flush()
        issued.session.revoke(SessionRevocation.TOKEN_REUSE)

        issued.session.revoke(SessionRevocation.EXPIRED)

        assert issued.session.revoked_reason == SessionRevocation.TOKEN_REUSE.value
        assert issued.session.compromised is True

    def test_sign_out_others_can_spare_the_caller(self, db_session, user) -> None:
        here = start_session(db_session, user)
        start_session(db_session, user)
        db_session.flush()

        revoke_all(
            db_session,
            user,
            SessionRevocation.LOGOUT_ALL,
            except_session_id=here.session.id,
        )

        assert [row.id for row in live_sessions(db_session, user)] == [here.session.id]


class TestDeviceLabel:
    @pytest.mark.parametrize(
        ("agent", "expected"),
        [
            ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Mac"),
            ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Windows PC"),
            ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)", "iPhone"),
            ("Kryova/1.0 Tauri/2.0 (Windows NT 10.0)", "Kryova desktop"),
        ],
    )
    def test_it_names_something_a_person_recognises(self, agent, expected) -> None:
        assert device_label(agent) == expected

    def test_nothing_to_go_on_is_none_not_a_literal_unknown(self) -> None:
        """A stored "Unknown" is indistinguishable from a device that genuinely
        reported that name."""
        assert device_label(None) is None
        assert device_label("") is None
        assert device_label("curl/8.4.0") is None

    def test_a_very_long_user_agent_does_not_break_the_insert(
        self, db_session, user
    ) -> None:
        """A 4 kB header is something anyone can send, and a login that fails on
        one is a denial of service needing no skill."""
        issued = start_session(db_session, user, user_agent="M" * 5000)
        db_session.flush()

        assert issued.session.user_agent is not None
        assert len(issued.session.user_agent) <= 400


class TestOverHttp:
    """What only HTTP can answer: cookies, CSRF, status codes, isolation."""

    def test_login_then_refresh_rotates_the_cookie(self, auth_client) -> None:
        first = auth_client.cookies.get("kryova_refresh")

        response = auth_client.post("/api/v1/auth/refresh")

        assert response.status_code == 200, response.text
        assert auth_client.cookies.get("kryova_refresh") != first

    def test_the_session_list_marks_the_current_device(self, auth_client) -> None:
        response = auth_client.get("/api/v1/auth/sessions")

        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) >= 1
        assert sum(1 for row in rows if row["current"]) == 1

    def test_the_session_list_carries_no_token_or_hash(self, auth_client) -> None:
        """This is what a logged-in browser reads; nothing in it may be useful
        to someone who has it."""
        rows = auth_client.get("/api/v1/auth/sessions").json()

        for row in rows:
            keys = " ".join(row.keys()).lower()
            assert "token" not in keys
            assert "hash" not in keys

    def test_another_users_session_is_404_not_403(
        self, auth_client, db_session
    ) -> None:
        """Same rule as every other resource here: ids must not be probeable
        across accounts."""
        stranger = User(email="stranger@example.com", hashed_password="x")
        db_session.add(stranger)
        db_session.flush()
        theirs = start_session(db_session, stranger)
        db_session.flush()

        response = auth_client.delete(f"/api/v1/auth/sessions/{theirs.session.id}")

        assert response.status_code == 404

    def test_logout_ends_only_this_device(self, auth_client, db_session) -> None:
        me = auth_client.get("/api/v1/auth/me").json()
        person = db_session.get(User, me["id"])
        elsewhere = start_session(db_session, person, user_agent="Mozilla/5.0 (iPhone)")
        db_session.flush()

        assert auth_client.post("/api/v1/auth/logout").status_code == 204

        assert elsewhere.session.revoked_at is None

    def test_logout_all_ends_every_device(self, auth_client, db_session) -> None:
        me = auth_client.get("/api/v1/auth/me").json()
        person = db_session.get(User, me["id"])
        elsewhere = start_session(db_session, person, user_agent="Mozilla/5.0 (iPhone)")
        db_session.flush()

        response = auth_client.post("/api/v1/auth/logout-all")

        assert response.status_code == 204
        assert elsewhere.session.revoked_at is not None

    def test_refresh_without_csrf_is_refused(self, auth_client) -> None:
        del auth_client.headers["x-csrf-token"]

        response = auth_client.post("/api/v1/auth/refresh")

        assert response.status_code in (401, 403)

    def test_a_replayed_cookie_signs_the_family_out_over_http(
        self, auth_client, db_session
    ) -> None:
        """End to end through the browser's own cookie: rotate, age the family
        past the grace window, present the old cookie again, get 401."""
        stale = auth_client.cookies.get("kryova_refresh")
        assert auth_client.post("/api/v1/auth/refresh").status_code == 200
        # Every refresh mints a new CSRF token. A browser reads the cookie back
        # with JS on each request; the test client holds a header, so it has to
        # be re-synced or the next call is refused for CSRF before it ever
        # reaches the rotation this test is about.
        auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

        aged = utcnow() - timedelta(seconds=REUSE_GRACE_SECONDS + 5)
        row = (
            db_session.query(UserSession)
            .filter(UserSession.previous_token_hash == hash_token(stale))
            .one()
        )
        row.rotated_at = aged
        db_session.flush()

        auth_client.cookies.set("kryova_refresh", stale)
        response = auth_client.post("/api/v1/auth/refresh")

        assert response.status_code == 401
        db_session.refresh(row)
        assert row.compromised is True
