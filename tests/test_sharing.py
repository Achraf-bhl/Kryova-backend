"""Share links and project transfer (P2.5).

The interesting half of this file is the **unauthenticated** route. It is the
only one in the service that answers with no principal, so most of what is
asserted here is about what it refuses and how uniformly it refuses it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import sharing
from app.core.sharing import MAX_SHARE_DAYS
from app.models import (
    Organisation,
    Project,
    ProjectTransfer,
    ShareLink,
    ShareRevocation,
)
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _issue(client: AuthenticatedTestClient, project_id: str, **body: object) -> dict:
    response = client.post(f"{API}/projects/{project_id}/shares", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestIssuingALink:
    def test_the_raw_token_comes_back_exactly_once(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        issued = _issue(auth_client, project_id, label="For Acme")

        assert issued["token"]
        assert issued["url"].endswith(issued["token"])
        # And never again: the list endpoint's model has no token field at all,
        # so one cannot be added there by accident.
        listed = auth_client.get(f"{API}/projects/{project_id}/shares").json()
        assert listed[0]["label"] == "For Acme"
        assert "token" not in listed[0]

    def test_the_token_is_stored_hashed(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        # A link readable in the database is a link every operator and every
        # backup can open.
        issued = _issue(auth_client, project_id)
        db_session.flush()
        row = db_session.scalar(select(ShareLink).where(ShareLink.project_id == project_id))
        assert row is not None
        assert issued["token"] not in row.token_hash

    def test_geometry_download_is_off_unless_asked_for(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # Sending somebody a picture of your stress field and sending them your
        # geometry are different decisions.
        assert _issue(auth_client, project_id)["allow_geometry_download"] is False
        assert (
            _issue(auth_client, project_id, allow_geometry_download=True)[
                "allow_geometry_download"
            ]
            is True
        )

    def test_a_link_cannot_outlive_the_ceiling(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # "Forever" is not on the menu: this is a credential with no account
        # behind it and no second factor.
        response = auth_client.post(
            f"{API}/projects/{project_id}/shares", json={"days": MAX_SHARE_DAYS + 1}
        )
        assert response.status_code == 422

    def test_another_users_project_cannot_be_shared(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        response = auth_client.post(f"{API}/projects/does-not-exist/shares", json={})
        assert response.status_code == 404


class TestReadingAPackageWithoutAnAccount:
    def test_a_live_link_returns_the_package(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        token = _issue(auth_client, project_id, label="For Acme", note="Rev B")["token"]

        # No credentials on this request at all -- that is the point.
        response = auth_client.get(f"{API}/share/{token}")

        assert response.status_code == 200, response.text
        package = response.json()
        assert package["project_name"] == "Bracket"
        assert package["label"] == "For Acme"
        assert package["note"] == "Rev B"
        assert package["shared_by"] == "eng@kryova.dev"

    def test_the_package_carries_no_ids_and_no_media_handles(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # `SimulationRead` carries ids, media handles and internal names. This
        # response goes to somebody with no account, so it is its own model.
        token = _issue(auth_client, project_id)["token"]
        package = auth_client.get(f"{API}/share/{token}").json()
        flat = str(package)
        assert project_id not in flat
        assert "media" not in package

    def test_every_refusal_is_the_same_refusal(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        """Expired, revoked and never-existed must be indistinguishable.

        Telling a holder their token *was* valid is the one piece of
        information that makes guessing worth continuing.
        """
        never = auth_client.get(f"{API}/share/not-a-real-token")

        revoked_token = _issue(auth_client, project_id)["token"]
        link = db_session.scalar(
            select(ShareLink).where(ShareLink.token_hash != "")
        )
        assert link is not None
        auth_client.delete(f"{API}/projects/{project_id}/shares/{link.id}")
        revoked = auth_client.get(f"{API}/share/{revoked_token}")

        expired_token = _issue(auth_client, project_id)["token"]
        expiring = db_session.scalar(
            select(ShareLink).where(ShareLink.revoked_at.is_(None))
        )
        assert expiring is not None
        expiring.expires_at = sharing.utcnow() - timedelta(seconds=1)
        db_session.flush()
        expired = auth_client.get(f"{API}/share/{expired_token}")

        assert never.status_code == revoked.status_code == expired.status_code == 404
        assert never.json()["detail"] == revoked.json()["detail"] == expired.json()["detail"]

    def test_opening_a_link_is_counted(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # "Has anybody opened this yet" is the question an issuer asks. A row
        # per hit would be an unbounded write anyone with the URL can drive.
        token = _issue(auth_client, project_id)["token"]
        auth_client.get(f"{API}/share/{token}")
        auth_client.get(f"{API}/share/{token}")

        listed = auth_client.get(f"{API}/projects/{project_id}/shares").json()
        assert listed[0]["view_count"] == 2
        assert listed[0]["last_viewed_at"] is not None

    def test_geometry_download_is_refused_as_a_404_not_a_403(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # A recipient sent a results package should not learn that the geometry
        # exists behind a flag somebody could flip.
        token = _issue(auth_client, project_id)["token"]
        response = auth_client.get(f"{API}/share/{token}/geometry/1")
        assert response.status_code == 404


class TestRevoking:
    def test_a_revoked_link_stops_working_immediately(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        token = _issue(auth_client, project_id)["token"]
        assert auth_client.get(f"{API}/share/{token}").status_code == 200
        link = db_session.scalar(select(ShareLink))
        assert link is not None

        assert (
            auth_client.delete(f"{API}/projects/{project_id}/shares/{link.id}").status_code
            == 204
        )

        assert auth_client.get(f"{API}/share/{token}").status_code == 404

    def test_a_revoked_link_is_still_listed(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        # "Who did we share this with, and is it still open" is one question. A
        # list that drops the dead rows answers only half of it.
        _issue(auth_client, project_id, label="Old supplier")
        link = db_session.scalar(select(ShareLink))
        assert link is not None
        auth_client.delete(f"{API}/projects/{project_id}/shares/{link.id}")

        listed = auth_client.get(f"{API}/projects/{project_id}/shares").json()
        assert len(listed) == 1
        assert listed[0]["revoked_at"] is not None

    def test_a_share_id_from_another_project_is_a_404(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        _issue(auth_client, project_id)
        link = db_session.scalar(select(ShareLink))
        assert link is not None
        other = auth_client.post(f"{API}/projects", json={"name": "Other"}).json()["id"]

        response = auth_client.delete(f"{API}/projects/{other}/shares/{link.id}")

        assert response.status_code == 404


class TestTransfer:
    @pytest.fixture
    def second_org(self, auth_client: AuthenticatedTestClient) -> str:
        created = auth_client.post(f"{API}/organisations", json={"name": "Acme Machines"})
        assert created.status_code == 201, created.text
        org_id: str = created.json()["id"]
        return org_id

    def test_a_project_moves_and_the_move_is_recorded(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        second_org: str,
        db_session: Session,
    ) -> None:
        response = auth_client.post(
            f"{API}/projects/{project_id}/transfer",
            json={"to_organisation_id": second_org, "reason": "new programme"},
        )

        assert response.status_code == 200, response.text
        db_session.flush()
        project = db_session.get(Project, project_id)
        assert project is not None
        assert project.organisation_id == second_org
        record = db_session.scalar(select(ProjectTransfer))
        assert record is not None
        assert record.to_organisation_id == second_org
        assert record.reason == "new programme"

    def test_the_creator_is_left_alone_because_it_is_provenance_not_permission(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        second_org: str,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        auth_client.post(
            f"{API}/projects/{project_id}/transfer", json={"to_organisation_id": second_org}
        )
        db_session.flush()

        project = db_session.get(Project, project_id)
        assert project is not None
        # Rewriting this would erase who actually did the work.
        assert project.owner_id == current_user_id

    def test_live_share_links_are_revoked_by_a_transfer(
        self,
        auth_client: AuthenticatedTestClient,
        project_id: str,
        second_org: str,
        db_session: Session,
    ) -> None:
        # A link issued by the old tenant is a window into a project that now
        # belongs to somebody else.
        token = _issue(auth_client, project_id)["token"]
        assert auth_client.get(f"{API}/share/{token}").status_code == 200

        auth_client.post(
            f"{API}/projects/{project_id}/transfer", json={"to_organisation_id": second_org}
        )

        assert auth_client.get(f"{API}/share/{token}").status_code == 404
        db_session.flush()
        link = db_session.scalar(select(ShareLink))
        assert link is not None
        assert link.revocation == ShareRevocation.TRANSFERRED

    def test_moving_a_project_to_where_it_already_is_is_refused(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        project = db_session.get(Project, project_id)
        assert project is not None
        response = auth_client.post(
            f"{API}/projects/{project_id}/transfer",
            json={"to_organisation_id": project.organisation_id},
        )
        assert response.status_code == 422

    def test_an_organisation_the_caller_is_not_in_is_a_404(
        self, auth_client: AuthenticatedTestClient, project_id: str, db_session: Session
    ) -> None:
        # Not a 403: an organisation you cannot administer is one you should
        # not be able to confirm the existence of by id.
        stranger = Organisation(name="Someone else", slug="someone-else", is_personal=False)
        db_session.add(stranger)
        db_session.flush()

        response = auth_client.post(
            f"{API}/projects/{project_id}/transfer", json={"to_organisation_id": stranger.id}
        )

        assert response.status_code == 404

    def test_the_history_is_readable_afterwards(
        self, auth_client: AuthenticatedTestClient, project_id: str, second_org: str
    ) -> None:
        auth_client.post(
            f"{API}/projects/{project_id}/transfer", json={"to_organisation_id": second_org}
        )

        history = auth_client.get(f"{API}/projects/{project_id}/transfers")

        assert history.status_code == 200
        assert len(history.json()) == 1
        assert history.json()[0]["to_organisation_id"] == second_org


class TestTheServiceLayerWithoutAClient:
    """`core/sharing.py` takes `now`, so expiry needs no waiting."""

    def test_a_link_is_dead_the_instant_it_expires(
        self, db_session: Session, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        project = db_session.get(Project, project_id)
        assert project is not None
        user = project.owner
        issued = sharing.issue(db_session, project=project, created_by=user, days=1)
        at = issued.link.expires_at

        assert issued.link.is_live(at - timedelta(seconds=1))
        assert not issued.link.is_live(at)

    def test_resolve_returns_nothing_for_an_empty_token(self, db_session: Session) -> None:
        assert sharing.resolve(db_session, "") is None

    def test_the_ceiling_binds_on_callers_that_never_see_the_schema(
        self, db_session: Session, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        """The service-layer bound, not the Pydantic one.

        `ShareLinkCreate` caps `days` at `MAX_SHARE_DAYS` and answers 422 first,
        so the route test above passes with the check in `issue` deleted — it
        was measuring the schema. This one drives `issue` directly, which is how
        the admin panel and any future agent tool will reach it.
        """
        project = db_session.get(Project, project_id)
        assert project is not None

        with pytest.raises(sharing.SharingError, match="at most"):
            sharing.issue(
                db_session,
                project=project,
                created_by=project.owner,
                days=MAX_SHARE_DAYS + 1,
            )

    def test_a_link_shorter_than_a_day_is_refused_the_same_way(
        self, db_session: Session, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        project = db_session.get(Project, project_id)
        assert project is not None
        with pytest.raises(sharing.SharingError, match="at least a day"):
            sharing.issue(db_session, project=project, created_by=project.owner, days=0)

    def test_a_link_whose_project_has_moved_is_not_live(
        self, db_session: Session, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        # The guard lives on the model rather than in the read path, so no
        # caller can forget it. A real organisation, because `projects` has a
        # foreign key and refuses an invented id — which is the schema working.
        project = db_session.get(Project, project_id)
        assert project is not None
        issued = sharing.issue(db_session, project=project, created_by=project.owner)
        elsewhere = Organisation(name="Elsewhere", slug="elsewhere", is_personal=False)
        db_session.add(elsewhere)
        db_session.flush()

        project.organisation_id = elsewhere.id
        db_session.flush()

        assert not issued.link.is_live()
