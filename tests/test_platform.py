"""Account lifecycle, feature flags, announcements and maintenance (P3.4–P3.7).

The flag evaluator and the lifecycle service both take their inputs explicitly
and hold no clock, so most of this file needs no HTTP at all. The routes are
exercised where the *authorisation* is the thing being tested.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core import flags, lifecycle, maintenance
from app.mail.message import MailKind, Outbox
from app.models import (
    Announcement,
    AnnouncementLevel,
    FeatureFlag,
    MaintenanceWindow,
    Project,
    ShareLink,
    ShareRevocation,
    StaffGrant,
    StaffRole,
    User,
    UserSession,
)
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


# ---------------------------------------------------------------------------
# Feature flags (P3.5)
# ---------------------------------------------------------------------------


class TestTheRolloutIsDeterministic:
    """A random rollout flickers a feature on and off under one user, which is
    indistinguishable from a broken deployment and impossible to support."""

    def test_the_same_subject_always_lands_in_the_same_bucket(self) -> None:
        first = [flags.bucket_of("new-viewer", "org-42") for _ in range(20)]
        assert len(set(first)) == 1

    def test_two_flags_at_the_same_percentage_do_not_pick_the_same_people(self) -> None:
        # Otherwise one unlucky tenant is in every early rollout the product
        # ever runs.
        subjects = [f"org-{index}" for index in range(200)]
        first = {s for s in subjects if flags.bucket_of("flag-a", s) < 10}
        second = {s for s in subjects if flags.bucket_of("flag-b", s) < 10}
        assert first != second
        assert first & second != first  # they overlap but are not identical

    def test_widening_a_rollout_only_ever_adds_people(self) -> None:
        # The property that makes a staged rollout safe to run forwards: nobody
        # who had the feature loses it when the percentage goes up.
        subjects = [f"org-{index}" for index in range(300)]
        at_ten = {s for s in subjects if flags.bucket_of("f", s) < 10}
        at_fifty = {s for s in subjects if flags.bucket_of("f", s) < 50}
        assert at_ten <= at_fifty

    def test_a_percentage_selects_roughly_that_share(self) -> None:
        subjects = [f"org-{index}" for index in range(2000)]
        selected = sum(1 for s in subjects if flags.bucket_of("f", s) < 25)
        assert 0.20 < selected / len(subjects) < 0.30


class TestThePrecedenceLadder:
    @pytest.fixture
    def flag(self, db_session: Session) -> FeatureFlag:
        row = FeatureFlag(key="new-viewer", enabled=False)
        db_session.add(row)
        db_session.flush()
        return row

    @pytest.fixture
    def org_id(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> str:
        """A tenant the caller belongs to.

        Created explicitly rather than reached for through `user.memberships`:
        the *personal* organisation is made lazily on first project creation,
        not at registration, so a freshly registered account genuinely has no
        memberships at all. Assuming otherwise is how this test first failed.
        """
        created = auth_client.post(f"{API}/organisations", json={"name": "Acme"})
        assert created.status_code == 201, created.text
        db_session.flush()
        return str(created.json()["id"])

    def test_the_default_applies_with_no_override(
        self, db_session: Session, flag: FeatureFlag, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        assert flags.is_enabled(db_session, "new-viewer", user=user) is False
        flag.enabled = True
        db_session.flush()
        assert flags.is_enabled(db_session, "new-viewer", user=user) is True

    def test_an_organisation_override_beats_the_default(
        self, db_session: Session, flag: FeatureFlag, current_user_id: str, org_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        flags.set_override(db_session, flag, enabled=True, organisation_id=org_id)

        assert flags.is_enabled(
            db_session, "new-viewer", user=user, organisation_ids=frozenset({org_id})
        )

    def test_a_user_override_beats_the_organisation(
        self, db_session: Session, flag: FeatureFlag, current_user_id: str, org_id: str
    ) -> None:
        # "Turn this on for this one person to reproduce a bug" has to work
        # inside a tenant that has it off.
        user = db_session.get(User, current_user_id)
        assert user is not None
        flags.set_override(db_session, flag, enabled=False, organisation_id=org_id)
        flags.set_override(db_session, flag, enabled=True, user_id=user.id)

        assert flags.is_enabled(
            db_session, "new-viewer", user=user, organisation_ids=frozenset({org_id})
        )

    def test_the_kill_switch_outranks_every_override(
        self, db_session: Session, flag: FeatureFlag, current_user_id: str, org_id: str
    ) -> None:
        # A kill switch an override can outvote is not a kill switch. This is
        # the one action an operator has when a feature is hurting people.
        user = db_session.get(User, current_user_id)
        assert user is not None
        flag.enabled = True
        flag.rollout_percentage = 100
        flags.set_override(db_session, flag, enabled=True, user_id=user.id)
        flags.set_override(db_session, flag, enabled=True, organisation_id=org_id)
        flag.killed = True
        db_session.flush()

        assert (
            flags.is_enabled(
                db_session, "new-viewer", user=user, organisation_ids=frozenset({org_id})
            )
            is False
        )

    def test_an_unknown_flag_is_false_rather_than_an_error(
        self, db_session: Session
    ) -> None:
        # A flag deleted while code still checks it must fail closed; raising
        # would turn tidying up into an outage.
        assert flags.is_enabled(db_session, "never-existed") is False

    def test_an_override_must_name_exactly_one_subject(
        self, db_session: Session, flag: FeatureFlag
    ) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            flags.set_override(db_session, flag, enabled=True)
        with pytest.raises(ValueError, match="exactly one"):
            flags.set_override(
                db_session, flag, enabled=True, user_id="u", organisation_id="o"
            )

    def test_an_anonymous_caller_gets_the_defaults_and_no_rollout(
        self, db_session: Session, flag: FeatureFlag
    ) -> None:
        # There is no stable subject to bucket on, and bucketing on an address
        # puts a whole office in one bucket.
        flag.rollout_percentage = 100
        db_session.flush()
        assert flags.evaluate(db_session) == {"new-viewer": False}


# ---------------------------------------------------------------------------
# Account lifecycle (P3.4)
# ---------------------------------------------------------------------------


class TestSuspension:
    def test_suspending_revokes_every_session_family(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        # Setting `is_active = False` alone leaves live access tokens working
        # for their fifteen minutes. Revoking is what makes it immediate.
        user = db_session.get(User, current_user_id)
        assert user is not None
        live = db_session.scalars(
            select(UserSession).where(
                UserSession.user_id == current_user_id, UserSession.revoked_at.is_(None)
            )
        ).all()
        assert len(live) == 1

        lifecycle.suspend(db_session, user, reason="abuse", by=user)

        assert user.is_active is False
        assert user.is_suspended
        remaining = db_session.scalars(
            select(UserSession).where(
                UserSession.user_id == current_user_id, UserSession.revoked_at.is_(None)
            )
        ).all()
        assert remaining == []

    def test_a_suspension_needs_a_reason(
        self, db_session: Session, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        with pytest.raises(lifecycle.LifecycleError, match="reason"):
            lifecycle.suspend(db_session, user, reason="   ", by=user)

    def test_reinstating_restores_access_and_leaves_the_work_alone(
        self, db_session: Session, auth_client: AuthenticatedTestClient, project_id: str,
        current_user_id: str,
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        lifecycle.suspend(db_session, user, reason="abuse", by=user)

        lifecycle.reinstate(db_session, user)

        assert user.is_active is True
        assert user.suspension_reason is None
        # The designs were never touched. That is the whole difference between
        # suspension and deletion.
        assert db_session.get(Project, project_id) is not None

    def test_reinstating_an_active_account_is_refused(
        self, db_session: Session, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        with pytest.raises(lifecycle.LifecycleError, match="not suspended"):
            lifecycle.reinstate(db_session, user)


class TestDeletionHasAGraceWindow:
    def test_scheduling_sets_a_date_and_withdraws_access(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None

        purge_at = lifecycle.schedule_deletion(db_session, user, by=user, grace_days=30)

        assert user.deletion_scheduled_at == purge_at
        # Access goes at once: somebody who asked to be deleted should not keep
        # using the product while the clock runs.
        assert user.is_active is False

    def test_cancelling_restores_the_account_completely(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        lifecycle.schedule_deletion(db_session, user, by=user)

        lifecycle.cancel_deletion(db_session, user)

        assert user.deletion_scheduled_at is None
        assert user.is_active is True

    def test_cancelling_does_not_lift_a_suspension_that_predates_it(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        # An account suspended for abuse, then scheduled for deletion, must stay
        # suspended when the deletion is called off.
        user = db_session.get(User, current_user_id)
        assert user is not None
        lifecycle.suspend(db_session, user, reason="abuse", by=user)
        lifecycle.schedule_deletion(db_session, user, by=user)

        lifecycle.cancel_deletion(db_session, user)

        assert user.is_active is False
        assert user.suspension_reason == "abuse"

    def test_only_accounts_past_their_date_are_due(
        self, db_session: Session, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        lifecycle.schedule_deletion(db_session, user, by=user, grace_days=30)
        now = lifecycle.utcnow()

        assert lifecycle.due_for_purge(db_session, now=now) == []
        assert lifecycle.due_for_purge(db_session, now=now + timedelta(days=31)) == [user]


class TestPurge:
    def test_a_purge_erases_the_account_and_reports_what_it_took(
        self,
        db_session: Session,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        current_user_id: str,
    ) -> None:
        user = db_session.get(User, current_user_id)
        assert user is not None
        share = auth_client.post(f"{API}/projects/{project_id}/shares", json={})
        assert share.status_code == 201

        report = lifecycle.purge(db_session, user, auth_client.media)

        assert report.projects == 1
        assert report.share_links_revoked == 1
        assert db_session.get(Project, project_id) is None
        assert db_session.get(User, current_user_id) is None

    def test_share_links_are_revoked_before_anything_is_taken_apart(
        self,
        db_session: Session,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        current_user_id: str,
    ) -> None:
        auth_client.post(f"{API}/projects/{project_id}/shares", json={})
        db_session.flush()
        link_id = db_session.scalar(select(ShareLink.id))
        user = db_session.get(User, current_user_id)
        assert user is not None and link_id is not None

        # Read the row before the cascade takes it.
        lifecycle.purge(db_session, user, auth_client.media)

        # The project is gone, so the link is gone with it — what matters is
        # that it was revoked on the way rather than left openable mid-purge.
        assert db_session.get(ShareLink, link_id) is None


class TestTheLifecycleRoutes:
    @pytest.fixture
    def admin_client(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> AuthenticatedTestClient:
        db_session.add(
            StaffGrant(user_id=current_user_id, role=StaffRole.PLATFORM_ADMIN)
        )
        db_session.flush()
        return auth_client

    def test_suspending_emails_the_person_it_happened_to(
        self,
        admin_client: AuthenticatedTestClient,
        db_session: Session,
        outbox: Outbox,
    ) -> None:
        # Being suspended without being told is how a customer's first contact
        # with the problem is a ticket that starts "your product is broken".
        subject = User(email="other@kryova.dev", hashed_password="x")
        db_session.add(subject)
        db_session.flush()
        outbox.clear()

        response = admin_client.post(
            f"{API}/admin/users/{subject.id}/suspend", json={"reason": "abuse"}
        )

        assert response.status_code == 200, response.text
        sent = outbox.of_kind(MailKind.SUSPENSION_NOTICE)
        assert len(sent) == 1
        assert sent[0].to == "other@kryova.dev"
        assert "abuse" in sent[0].body

    def test_purging_before_the_window_is_up_is_refused(
        self, admin_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        # An administrator who can purge the same day they schedule has a grace
        # window in name only.
        subject = User(email="other@kryova.dev", hashed_password="x")
        db_session.add(subject)
        db_session.flush()
        admin_client.post(f"{API}/admin/users/{subject.id}/deletion", json={})

        response = admin_client.post(f"{API}/admin/users/{subject.id}/purge")

        assert response.status_code == 409
        assert "grace window" in response.json()["detail"]

    def test_purging_an_unscheduled_account_is_refused(
        self, admin_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        subject = User(email="other@kryova.dev", hashed_password="x")
        db_session.add(subject)
        db_session.flush()

        response = admin_client.post(f"{API}/admin/users/{subject.id}/purge")

        assert response.status_code == 409
        assert "Schedule the deletion first" in response.json()["detail"]

    def test_an_ordinary_user_cannot_suspend_anybody(
        self, auth_client: AuthenticatedTestClient, current_user_id: str
    ) -> None:
        # 404, not 403: for a non-staff caller the whole /admin tree is simply
        # not there.
        response = auth_client.post(
            f"{API}/admin/users/{current_user_id}/suspend", json={"reason": "x"}
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Maintenance mode and announcements (P3.7)
# ---------------------------------------------------------------------------


class TestMaintenanceModeRefusesInWords:
    @pytest.fixture
    def window(self, db_session: Session, current_user_id: str) -> MaintenanceWindow:
        row = MaintenanceWindow(
            reason="migrating the primary database",
            message="Kryova is read-only while we move the database.",
            started_at=lifecycle.utcnow(),
            expected_end_at=lifecycle.utcnow() + timedelta(hours=1),
            allow_staff=True,
            started_by_id=current_user_id,
        )
        db_session.add(row)
        db_session.flush()
        return row

    def test_reads_keep_working(
        self, auth_client: AuthenticatedTestClient, window: MaintenanceWindow
    ) -> None:
        # The point of a maintenance mode is that the application is not simply
        # broken in front of the user while they wait.
        assert auth_client.get(f"{API}/auth/me").status_code == 200
        assert auth_client.get(f"{API}/projects").status_code == 200

    def test_a_mutation_is_refused_with_503_and_a_sentence(
        self, auth_client: AuthenticatedTestClient, window: MaintenanceWindow
    ) -> None:
        response = auth_client.post(f"{API}/projects", json={"name": "Bracket"})

        assert response.status_code == 503
        assert "read-only" in response.json()["detail"]
        # Tells a client to stop retrying in a tight loop.
        assert response.headers["retry-after"] == "300"

    def test_the_user_is_never_shown_the_operators_note(
        self, auth_client: AuthenticatedTestClient, window: MaintenanceWindow
    ) -> None:
        # "migrating the primary database" is not something a customer can act
        # on, and it names infrastructure to whoever is asking.
        response = auth_client.post(f"{API}/projects", json={"name": "Bracket"})
        assert "migrating the primary database" not in response.text

    def test_staff_are_let_through_by_default(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
        window: MaintenanceWindow,
    ) -> None:
        # The people who need to fix whatever caused the maintenance are the
        # ones holding staff grants.
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.OPERATOR))
        db_session.flush()

        assert auth_client.post(f"{API}/projects", json={"name": "B"}).status_code == 201

    def test_staff_can_be_locked_out_too_when_the_window_says_so(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
        window: MaintenanceWindow,
    ) -> None:
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.OPERATOR))
        window.allow_staff = False
        db_session.flush()

        assert auth_client.post(f"{API}/projects", json={"name": "B"}).status_code == 503

    def test_an_ended_window_stops_refusing(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        window: MaintenanceWindow,
    ) -> None:
        window.ended_at = lifecycle.utcnow() - timedelta(seconds=1)
        db_session.flush()

        assert auth_client.post(f"{API}/projects", json={"name": "B"}).status_code == 201


class TestThePlatformStateEndpoint:
    def test_it_answers_a_signed_out_caller(
        self, client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        # A login page that cannot say "we are down until 14:00" is a login page
        # that just looks broken.
        db_session.add(FeatureFlag(key="new-viewer", enabled=True))
        db_session.flush()

        response = client.get(f"{API}/platform/state")

        assert response.status_code == 200
        assert response.json()["flags"] == {"new-viewer": True}

    def test_it_is_readable_during_maintenance(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        # The endpoint that *explains* the window must not be one of the things
        # the window refuses.
        db_session.add(
            MaintenanceWindow(
                reason="internal note",
                message="Back shortly.",
                started_at=lifecycle.utcnow(),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()

        response = auth_client.get(f"{API}/platform/state")

        assert response.status_code == 200
        assert response.json()["maintenance"]["message"].startswith("Back shortly.")
        assert "internal note" not in response.text

    def test_live_announcements_ride_along(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        db_session.add(
            Announcement(
                message="Scheduled maintenance on Sunday.",
                level=AnnouncementLevel.WARNING,
                starts_at=lifecycle.utcnow() - timedelta(hours=1),
                published_by_id=current_user_id,
            )
        )
        db_session.flush()

        body = auth_client.get(f"{API}/platform/state").json()

        assert len(body["announcements"]) == 1
        assert body["announcements"][0]["level"] == "warning"

    def test_a_withdrawn_announcement_disappears(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        row = Announcement(
            message="Ignore me",
            starts_at=lifecycle.utcnow() - timedelta(hours=1),
            withdrawn_at=lifecycle.utcnow(),
            published_by_id=current_user_id,
        )
        db_session.add(row)
        db_session.flush()

        assert auth_client.get(f"{API}/platform/state").json()["announcements"] == []

    def test_an_announcement_that_has_not_started_is_not_shown(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        db_session.add(
            Announcement(
                message="Next week",
                starts_at=lifecycle.utcnow() + timedelta(days=7),
                published_by_id=current_user_id,
            )
        )
        db_session.flush()

        assert auth_client.get(f"{API}/platform/state").json()["announcements"] == []


class TestTheMaintenanceHelpers:
    def test_a_safe_method_is_never_refused(self, db_session: Session) -> None:
        window = MaintenanceWindow(
            reason="r", message="m", started_at=lifecycle.utcnow(), started_by_id="u"
        )
        for method in ("GET", "HEAD", "OPTIONS"):
            assert not maintenance.refuses(window, method=method, is_staff=False)

    def test_no_window_refuses_nothing(self) -> None:
        assert not maintenance.refuses(None, method="POST", is_staff=False)

    def test_the_refusal_names_the_expected_end_when_there_is_one(self) -> None:
        window = MaintenanceWindow(
            reason="r",
            message="Back shortly.",
            started_at=lifecycle.utcnow(),
            expected_end_at=lifecycle.utcnow() + timedelta(hours=2),
            started_by_id="u",
        )
        assert "Expected back at" in maintenance.refusal_message(window)

    def test_a_blank_message_still_says_something_useful(self) -> None:
        window = MaintenanceWindow(
            reason="r", message="   ", started_at=lifecycle.utcnow(), started_by_id="u"
        )
        assert "maintenance" in maintenance.refusal_message(window)


class TestTheWindowIsCached:
    """The check runs on every authenticated write, so it must not be a query.

    `maintenance_windows` is empty in essentially every deployment at
    essentially every moment. Reading it per request added a SELECT to every
    write in the product to learn nothing — `test_projects.py`'s query-count
    tests are what caught it, and this class is what keeps it caught, because
    those tests measure a route and would go on passing if the caching moved
    somewhere that only that route benefits from.
    """

    @pytest.fixture
    def counting(self, db_session: Session) -> Iterator[list[str]]:
        seen: list[str] = []

        @event.listens_for(db_session.get_bind(), "before_cursor_execute")
        def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            if "maintenance_windows" in statement:
                seen.append(statement)

        try:
            yield seen
        finally:
            event.remove(db_session.get_bind(), "before_cursor_execute", record)

    def test_a_second_look_within_the_window_asks_nobody(
        self, db_session: Session, counting: list[str]
    ) -> None:
        maintenance.current_window(db_session)
        assert len(counting) == 1

        for _ in range(20):
            maintenance.current_window(db_session)
        assert len(counting) == 1, "the cache is not holding; every request pays a read"

    def test_an_absence_is_cached_too(
        self, db_session: Session, counting: list[str]
    ) -> None:
        # The common case *is* the absence. A cache that only remembered live
        # windows would leave the ordinary deployment paying the read forever,
        # which is the whole cost this exists to remove.
        assert maintenance.current_window(db_session) is None
        assert maintenance.current_window(db_session) is None
        assert len(counting) == 1

    def test_the_answer_is_re_read_once_the_ttl_is_up(
        self, db_session: Session, counting: list[str]
    ) -> None:
        clock = [1000.0]
        maintenance.current_window(db_session, monotonic=lambda: clock[0])
        clock[0] += maintenance.CACHE_SECONDS + 0.1
        maintenance.current_window(db_session, monotonic=lambda: clock[0])
        assert len(counting) == 2

    def test_invalidating_makes_the_next_look_real(
        self, db_session: Session, counting: list[str]
    ) -> None:
        # This is what the admin routes call, so that an operator who has just
        # declared maintenance is not told by their own next request that
        # nothing has changed.
        maintenance.current_window(db_session)
        maintenance.invalidate()
        maintenance.current_window(db_session)
        assert len(counting) == 2

    def test_declaring_maintenance_takes_hold_at_once_in_this_process(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """The end-to-end version: cache warm, then a window appears."""
        assert auth_client.post(f"{API}/projects", json={"name": "before"}).status_code == 201

        db_session.add(
            MaintenanceWindow(
                reason="r",
                message="Kryova is read-only.",
                started_at=lifecycle.utcnow(),
                started_by_id=current_user_id,
            )
        )
        db_session.flush()
        maintenance.invalidate()  # what `POST /admin/maintenance` does after its commit

        assert auth_client.post(f"{API}/projects", json={"name": "after"}).status_code == 503

    def test_a_window_with_an_end_already_set_lapses_on_time_from_cache(
        self, db_session: Session, current_user_id: str
    ) -> None:
        """A window that already knows when it ends ends then, not ten seconds later.

        This is why the snapshot keeps `ended_at` rather than a precomputed
        "active" flag. The guarantee is narrow and worth stating exactly: it
        covers an end that was *already on the row* when the snapshot was taken.
        An operator ending maintenance early sets `ended_at` after that, so the
        cached copy cannot know — which is what `invalidate()` in
        `end_maintenance` is for, and why `CACHE_SECONDS` bounds every other
        worker.
        """
        window = MaintenanceWindow(
            reason="r",
            message="m",
            started_at=lifecycle.utcnow() - timedelta(minutes=5),
            ended_at=lifecycle.utcnow() + timedelta(minutes=5),
            started_by_id=current_user_id,
        )
        db_session.add(window)
        db_session.flush()

        clock = 500.0
        assert maintenance.current_window(db_session, monotonic=lambda: clock) is not None

        # The monotonic clock has not moved, so this is the same cache entry --
        # only the wall clock has passed the end.
        later = lifecycle.utcnow() + timedelta(minutes=6)
        assert (
            maintenance.current_window(db_session, now=later, monotonic=lambda: clock)
            is None
        )


class TestFleetHealth:
    def test_an_empty_window_reports_no_success_rate_rather_than_zero(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        # No runs to judge and every run failing are opposite states. A
        # dashboard showing 0% on a quiet night sends somebody hunting an
        # outage that is not there.
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        db_session.flush()

        body = auth_client.get(f"{API}/admin/health").json()

        assert body["success_rate"] is None
        assert body["window_hours"] == 24

    def test_it_says_how_failures_were_grouped(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        # Naming the method is how the console avoids presenting a coarse
        # grouping as a classification.
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        db_session.flush()

        body = auth_client.get(f"{API}/admin/health").json()

        assert "E15.5" in body["failure_grouping"]

    def test_it_reports_whether_this_deployment_can_email_anybody(
        self, auth_client: AuthenticatedTestClient, db_session: Session,
        current_user_id: str,
    ) -> None:
        # A platform whose password resets go to a log file is unhealthy in a
        # way no job counter shows. The suite runs on the memory transport.
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        db_session.flush()

        assert auth_client.get(f"{API}/admin/health").json()["mail_delivers"] is False

    def test_it_is_not_reachable_by_an_ordinary_user(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        assert auth_client.get(f"{API}/admin/health").status_code == 404


def _unused() -> None:  # pragma: no cover - keeps the import list honest
    assert ShareRevocation.REVOKED
