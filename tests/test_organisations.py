"""Organisations, members and invitations (P2.1, P2.2).

The invitation half is the security-relevant one: a token that is stored in
clear, or accepted by whoever holds it regardless of who it was addressed to,
is a way into a tenant.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import mail
from app.core.security import hash_token
from app.mail.message import MailKind, Outbox
from app.models import Membership, Organisation, OrganisationInvitation, OrgRole
from tests.test_tenancy import SignIn, sign_in  # noqa: F401  -- a fixture, used by name
from tests.typing import AuthenticatedTestClient


@pytest.fixture
def organisation_id(auth_client: AuthenticatedTestClient) -> str:
    response = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


class TestCreatingAnOrganisation:
    def test_the_creator_becomes_its_owner(self, auth_client: AuthenticatedTestClient) -> None:
        created = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["role"] == "owner"
        assert body["domain_role"] == "engineer"
        assert body["is_personal"] is False
        assert body["slug"].startswith("kryova-machines-")

    def test_a_taken_slug_is_refused_with_something_to_do_about_it(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        first = auth_client.post(
            "/api/v1/organisations", json={"name": "Kryova Machines", "slug": "kryova"}
        )
        assert first.status_code == 201
        second = auth_client.post(
            "/api/v1/organisations", json={"name": "Another", "slug": "kryova"}
        )
        assert second.status_code == 409
        assert "Choose another" in second.json()["detail"]

    def test_a_slug_with_spaces_or_slashes_is_rejected_before_it_reaches_a_url(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        response = auth_client.post(
            "/api/v1/organisations", json={"name": "Kryova", "slug": "kry ova/machines"}
        )
        assert response.status_code == 422

    def test_listing_pages(self, auth_client: AuthenticatedTestClient) -> None:
        for index in range(3):
            assert (
                auth_client.post(
                    "/api/v1/organisations", json={"name": f"Org {index}"}
                ).status_code
                == 201
            )
        page = auth_client.get("/api/v1/organisations", params={"page": 2, "page_size": 2})
        assert page.status_code == 200
        body = page.json()
        assert body["total"] == 3
        assert len(body["items"]) == 1


class TestInvitations:
    def _invite(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, email: str, **extra: object
    ) -> dict:
        response = auth_client.post(
            f"/api/v1/organisations/{organisation_id}/invitations",
            json={"email": email, **extra},
        )
        assert response.status_code == 201, response.text
        return dict(response.json())

    def test_only_the_hash_of_the_token_is_stored(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, db_session: Session
    ) -> None:
        """Same contract as the password-reset flow: a database read must not
        yield a working credential."""
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        token = issued["token"]
        assert token

        rows = list(db_session.scalars(select(OrganisationInvitation)))
        assert len(rows) == 1
        assert token not in rows[0].token_hash
        assert rows[0].token_hash == hash_token(token)
        assert len(rows[0].token_hash) == 64

    def test_accepting_joins_the_organisation_at_the_invited_role(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        issued = self._invite(
            auth_client, organisation_id, "friend@kryova.dev", role="admin", domain_role="reviewer"
        )
        friend = sign_in("friend@kryova.dev")

        accepted = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "admin"
        assert accepted.json()["domain_role"] == "reviewer"
        assert accepted.json()["id"] == organisation_id

    def test_a_token_is_single_use(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        friend = sign_in("friend@kryova.dev")
        assert (
            friend.post(
                "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
            ).status_code
            == 200
        )
        replayed = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
        )
        assert replayed.status_code == 422

    def test_a_token_is_bound_to_the_address_it_was_sent_to(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
        db_session: Session,
    ) -> None:
        """Otherwise an invitation forwarded, leaked or guessed lets whoever
        holds it into the tenant."""
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        interloper = sign_in("interloper@kryova.dev")

        refused = interloper.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
        )
        assert refused.status_code == 422
        assert db_session.scalar(select(OrganisationInvitation)).accepted_at is None

    def test_an_expired_token_is_refused(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
        db_session: Session,
    ) -> None:
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        invitation = db_session.scalar(select(OrganisationInvitation))
        assert invitation is not None
        invitation.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.flush()

        friend = sign_in("friend@kryova.dev")
        assert (
            friend.post(
                "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
            ).status_code
            == 422
        )

    def test_a_revoked_token_is_refused(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        assert (
            auth_client.delete(
                f"/api/v1/organisations/{organisation_id}/invitations/{issued['id']}"
            ).status_code
            == 204
        )
        friend = sign_in("friend@kryova.dev")
        assert (
            friend.post(
                "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
            ).status_code
            == 422
        )

    def test_an_unknown_token_reads_exactly_like_a_used_one(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")
        friend = sign_in("friend@kryova.dev")
        friend.post("/api/v1/organisations/invitations/accept", json={"token": issued["token"]})

        used = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
        )
        invented = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": "not-a-token"}
        )
        assert used.status_code == invented.status_code == 422
        assert used.json() == invented.json()

    def test_accepting_twice_never_lowers_an_existing_role(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
        db_session: Session,
    ) -> None:
        """An invitation is an offer, not a demotion. A stale `viewer` invite
        arriving after someone was promoted must not take their access away."""
        admin_invite = self._invite(
            auth_client, organisation_id, "friend@kryova.dev", role="admin"
        )
        viewer_invite = self._invite(
            auth_client, organisation_id, "friend@kryova.dev", role="viewer"
        )
        friend = sign_in("friend@kryova.dev")

        assert (
            friend.post(
                "/api/v1/organisations/invitations/accept", json={"token": admin_invite["token"]}
            ).json()["role"]
            == "admin"
        )
        late = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": viewer_invite["token"]}
        )
        assert late.status_code == 200
        assert late.json()["role"] == "admin"

        membership = db_session.scalar(
            select(Membership).where(Membership.organisation_id == organisation_id)
        )
        assert membership is not None

    def test_a_revoke_is_scoped_to_the_organisation_in_the_path(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        """Fetching by id alone would let an admin of any tenant revoke any
        invitation in the system."""
        issued = self._invite(auth_client, organisation_id, "friend@kryova.dev")

        other = sign_in("other-owner@kryova.dev")
        theirs = other.post("/api/v1/organisations", json={"name": "Elsewhere"}).json()["id"]

        assert (
            other.delete(
                f"/api/v1/organisations/{theirs}/invitations/{issued['id']}"
            ).status_code
            == 404
        )

    def test_the_token_is_never_returned_in_production(
        self, auth_client: AuthenticatedTestClient, organisation_id: str, monkeypatch
    ) -> None:
        """A token in a response body that a proxy or an access log records is
        a credential in a log file -- the reason the password-reset flow does
        not print one either."""
        from app.core.config import settings

        monkeypatch.setattr(type(settings), "is_production", property(lambda _self: True))
        created = auth_client.post(
            f"/api/v1/organisations/{organisation_id}/invitations",
            json={"email": "friend@kryova.dev"},
        )
        assert created.status_code == 201
        assert created.json()["token"] is None


class TestMembers:
    def test_the_member_list_shows_roles(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        issued = auth_client.post(
            f"/api/v1/organisations/{organisation_id}/invitations",
            json={"email": "friend@kryova.dev", "role": "viewer"},
        ).json()
        friend = sign_in("friend@kryova.dev")
        friend.post("/api/v1/organisations/invitations/accept", json={"token": issued["token"]})

        listed = auth_client.get(f"/api/v1/organisations/{organisation_id}/members")
        assert listed.status_code == 200
        roles = {item["email"]: item["role"] for item in listed.json()["items"]}
        assert roles == {"eng@kryova.dev": "owner", "friend@kryova.dev": "viewer"}

    def test_an_owner_can_hand_over_and_then_step_down(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
        current_user_id: str,
    ) -> None:
        issued = auth_client.post(
            f"/api/v1/organisations/{organisation_id}/invitations",
            json={"email": "successor@kryova.dev"},
        ).json()
        successor = sign_in("successor@kryova.dev")
        successor.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued["token"]}
        )
        successor_id = successor.get("/api/v1/auth/me").json()["id"]

        promoted = auth_client.patch(
            f"/api/v1/organisations/{organisation_id}/members/{successor_id}",
            json={"role": "owner"},
        )
        assert promoted.status_code == 200, promoted.text

        # With a second owner in place, the first is no longer the last one.
        stepped_down = auth_client.patch(
            f"/api/v1/organisations/{organisation_id}/members/{current_user_id}",
            json={"role": "member"},
        )
        assert stepped_down.status_code == 200

    def test_a_non_member_is_not_found_rather_than_reported_missing_by_role(
        self,
        auth_client: AuthenticatedTestClient,
        organisation_id: str,
        sign_in: SignIn,  # noqa: F811
    ) -> None:
        outsider = sign_in("outsider@kryova.dev")
        outsider_id = outsider.get("/api/v1/auth/me").json()["id"]
        assert (
            auth_client.patch(
                f"/api/v1/organisations/{organisation_id}/members/{outsider_id}",
                json={"role": "admin"},
            ).status_code
            == 404
        )


class TestPersonalOrganisations:
    def test_a_personal_organisation_is_flagged_as_one(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        auth_client.post("/api/v1/projects", json={"name": "Bracket"})
        organisations = list(db_session.scalars(select(Organisation)))
        assert [organisation.is_personal for organisation in organisations] == [True]
        assert organisations[0].slug.startswith("eng-")

    def test_it_is_an_ordinary_tenant_in_every_other_respect(
        self,
        auth_client: AuthenticatedTestClient,
        sign_in: SignIn,  # noqa: F811
        db_session: Session,
    ) -> None:
        """Same rules, same tables, same policies -- the point of having one
        ownership model rather than two."""
        auth_client.post("/api/v1/projects", json={"name": "Bracket"})
        personal = db_session.scalar(select(Organisation))
        assert personal is not None

        issued = auth_client.post(
            f"/api/v1/organisations/{personal.id}/invitations",
            json={"email": "friend@kryova.dev", "role": "viewer"},
        )
        assert issued.status_code == 201
        friend = sign_in("friend@kryova.dev")
        accepted = friend.post(
            "/api/v1/organisations/invitations/accept", json={"token": issued.json()["token"]}
        )
        assert accepted.status_code == 200
        assert accepted.json()["is_personal"] is True

    def test_the_personal_membership_is_an_owner_membership(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        auth_client.post("/api/v1/projects", json={"name": "Bracket"})
        membership = db_session.scalar(select(Membership))
        assert membership is not None
        assert membership.role is OrgRole.OWNER


class TestAnInvitationIsActuallySent:
    """P2.1 declared invitations by email; until P1.5 nothing could send one.

    The route now posts the message and hands the raw token back **only when
    delivery failed**, so the two branches are worth pinning separately: one is
    the production path and one is the development-and-outage path.
    """

    def test_the_invitation_email_goes_to_the_invitee(
        self, auth_client: AuthenticatedTestClient, outbox: Outbox
    ) -> None:
        organisation = auth_client.post(
            "/api/v1/organisations", json={"name": "Acme Machines"}
        ).json()
        outbox.clear()

        response = auth_client.post(
            f"/api/v1/organisations/{organisation['id']}/invitations",
            json={"email": "new@supplier.dev", "role": "member"},
        )

        assert response.status_code == 201, response.text
        sent = outbox.of_kind(MailKind.ORG_INVITATION)
        assert len(sent) == 1
        assert sent[0].to == "new@supplier.dev"
        assert "Acme Machines" in sent[0].body
        assert "eng@kryova.dev" in sent[0].subject

    def test_the_token_comes_back_when_nothing_could_be_delivered(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        # The suite runs on the memory transport, which reaches nobody. The
        # inviter must be able to pass the link on themselves rather than
        # believing an invitation is in flight that is not.
        organisation = auth_client.post(
            "/api/v1/organisations", json={"name": "Acme Machines"}
        ).json()

        issued = auth_client.post(
            f"/api/v1/organisations/{organisation['id']}/invitations",
            json={"email": "new@supplier.dev", "role": "member"},
        ).json()

        assert issued["token"]

    def test_the_token_is_withheld_once_the_email_really_arrives(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        # Returning it as well would put a live credential in the inviter's
        # browser history and in any log that records response bodies, for no
        # gain -- the recipient already has it.
        class Delivering(mail.MemoryTransport):
            @property
            def reaches_real_mailboxes(self) -> bool:
                return True

            def send(self, message: mail.Mail) -> mail.Delivery:
                super().send(message)
                return mail.Delivery(mail=message, state=mail.DeliveryState.SENT)

        organisation = auth_client.post(
            "/api/v1/organisations", json={"name": "Acme Machines"}
        ).json()
        mail.use_transport(Delivering())

        issued = auth_client.post(
            f"/api/v1/organisations/{organisation['id']}/invitations",
            json={"email": "new@supplier.dev", "role": "member"},
        ).json()

        assert issued["token"] is None
