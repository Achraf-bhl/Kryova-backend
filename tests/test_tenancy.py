"""Tenancy: who owns what, and what the other tenant is told (P2.1, P2.4).

The rule under test everywhere in this file is that a resource outside your
tenant *does not exist*. Not "forbidden" -- 403 confirms the id is real, which
is a free enumeration oracle across accounts.

These run on whatever `TEST_DATABASE_URL` resolves to, SQLite included: they
test the application's scoping. `test_tenancy_rls.py` is the other half, and
tests what is left when the application's scoping is wrong.
"""

from collections.abc import Callable
from typing import cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.rate_limit import auth_limiter
from app.main import app
from app.models import Membership, Organisation, OrgRole, Project, User
from tests.typing import AuthenticatedTestClient

SignIn = Callable[[str], AuthenticatedTestClient]


@pytest.fixture
def sign_in(client: AuthenticatedTestClient) -> SignIn:
    """Register and sign in another account against the same app and session.

    Depends on `client` for its dependency overrides, not for its cookies: each
    account gets its own `TestClient`, because cookies are per-client and a
    shared one would make "the other tenant" the same tenant.
    """

    def _sign_in(email: str, password: str = "correct-horse-battery") -> AuthenticatedTestClient:
        auth_limiter.reset()
        peer = cast(AuthenticatedTestClient, TestClient(app))
        registered = peer.post("/api/v1/auth/register", json={"email": email, "password": password})
        assert registered.status_code == 201, registered.text
        signed_in = peer.post(
            "/api/v1/auth/login", data={"username": email, "password": password}
        )
        assert signed_in.status_code == 200, signed_in.text
        peer.headers["x-csrf-token"] = peer.cookies["kryova_csrf"]
        return peer

    return _sign_in


class TestEveryProjectLandsInAnOrganisation:
    """The backfill's invariant, enforced going forward as well as backward.

    A project with no owning organisation sits outside every RLS policy, so it
    is protected by nothing. The migration's job is that no *existing* row is
    like that; these are about the rows made after it.
    """

    def test_a_project_created_through_the_api_gets_a_personal_organisation(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        created = auth_client.post("/api/v1/projects", json={"name": "Bracket"})
        assert created.status_code == 201, created.text

        project = db_session.get(Project, created.json()["id"])
        assert project is not None
        assert project.organisation_id is not None
        organisation = db_session.get(Organisation, project.organisation_id)
        assert organisation is not None and organisation.is_personal

    def test_a_project_constructed_outside_the_route_layer_gets_one_too(
        self, auth_client: AuthenticatedTestClient, current_user_id: str, db_session: Session
    ) -> None:
        """`app/ai/tools.py` builds a `Project` directly, and so will the next
        importer somebody writes. The guarantee has to live in the model, not
        in every call site remembering."""
        project = Project(name="Made by the agent", owner_id=current_user_id)
        db_session.add(project)
        db_session.flush()

        assert project.organisation_id is not None

    def test_the_personal_organisation_is_created_once_and_reused(
        self, auth_client: AuthenticatedTestClient, current_user_id: str, db_session: Session
    ) -> None:
        for name in ("One", "Two", "Three"):
            assert auth_client.post("/api/v1/projects", json={"name": name}).status_code == 201

        owned = list(
            db_session.scalars(select(Project).where(Project.owner_id == current_user_id))
        )
        assert len(owned) == 3
        assert len({project.organisation_id for project in owned}) == 1

        memberships = list(
            db_session.scalars(select(Membership).where(Membership.user_id == current_user_id))
        )
        assert [membership.role for membership in memberships] == [OrgRole.OWNER]

    def test_the_creator_owns_their_personal_organisation(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        assert auth_client.post("/api/v1/projects", json={"name": "Bracket"}).status_code == 201
        listed = auth_client.get("/api/v1/organisations")
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert body["total"] == 1
        assert body["items"][0]["role"] == "owner"
        assert body["items"][0]["is_personal"] is True

    def test_an_account_that_has_made_nothing_has_no_organisation(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        """Registration deliberately does not mint a tenant. Nothing has been
        created, so there is nothing to own."""
        assert auth_client.get("/api/v1/organisations").json()["total"] == 0


class TestCrossTenantReadsAre404:
    def test_another_tenants_project_is_not_found(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn
    ) -> None:
        mine = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        stranger = sign_in("stranger@kryova.dev")

        assert stranger.get(f"/api/v1/projects/{mine}").status_code == 404
        assert stranger.patch(f"/api/v1/projects/{mine}", json={"name": "Mine now"}).status_code == 404
        assert stranger.delete(f"/api/v1/projects/{mine}").status_code == 404

    def test_the_children_of_another_tenants_project_are_not_found_either(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn
    ) -> None:
        mine = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        stranger = sign_in("stranger@kryova.dev")

        for path in (f"/api/v1/projects/{mine}/geometry", f"/api/v1/projects/{mine}/simulations"):
            response = stranger.get(path)
            assert response.status_code == 404, f"{path} -> {response.status_code}"

    def test_another_tenants_organisation_is_not_found(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn
    ) -> None:
        organisation = auth_client.post(
            "/api/v1/organisations", json={"name": "Kryova Machines"}
        ).json()["id"]
        stranger = sign_in("stranger@kryova.dev")

        assert stranger.get(f"/api/v1/organisations/{organisation}").status_code == 404
        assert stranger.get(f"/api/v1/organisations/{organisation}/members").status_code == 404
        assert stranger.get(f"/api/v1/organisations/{organisation}/projects").status_code == 404
        assert stranger.patch(
            f"/api/v1/organisations/{organisation}", json={"name": "Mine now"}
        ).status_code == 404
        assert stranger.post(
            f"/api/v1/organisations/{organisation}/invitations",
            json={"email": "friend@kryova.dev"},
        ).status_code == 404

    def test_a_miss_and_a_stranger_are_indistinguishable(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn
    ) -> None:
        """The whole point of 404-not-403: the response to "someone else's
        project" must be byte-identical to the response to "no such id"."""
        mine = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        stranger = sign_in("stranger@kryova.dev")

        real = stranger.get(f"/api/v1/projects/{mine}")
        imaginary = stranger.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
        assert real.status_code == imaginary.status_code == 404
        assert real.json() == imaginary.json()

    def test_a_project_the_agent_created_is_still_another_tenants(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn, db_session: Session
    ) -> None:
        """The `before_flush` hook must place the project in the *owner's*
        tenant, not in whichever one happened to be handy."""
        stranger = sign_in("stranger@kryova.dev")
        owner = db_session.scalar(select(User).where(User.email == "stranger@kryova.dev"))
        assert owner is not None

        project = Project(name="Agent build", owner_id=owner.id)
        db_session.add(project)
        db_session.flush()

        assert auth_client.get(f"/api/v1/projects/{project.id}").status_code == 404
        assert stranger.get(f"/api/v1/projects/{project.id}").status_code == 200


class TestRoles:
    """Permission is one ladder, and every check is "at least this role"."""

    @pytest.fixture
    def shared_organisation(self, auth_client: AuthenticatedTestClient) -> str:
        response = auth_client.post("/api/v1/organisations", json={"name": "Kryova Machines"})
        assert response.status_code == 201, response.text
        return cast(str, response.json()["id"])

    def _join(
        self,
        auth_client: AuthenticatedTestClient,
        organisation: str,
        joiner: AuthenticatedTestClient,
        email: str,
        role: str,
    ) -> None:
        invited = auth_client.post(
            f"/api/v1/organisations/{organisation}/invitations",
            json={"email": email, "role": role},
        )
        assert invited.status_code == 201, invited.text
        accepted = joiner.post(
            "/api/v1/organisations/invitations/accept", json={"token": invited.json()["token"]}
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == role

    def test_ordering(self) -> None:
        assert OrgRole.OWNER.at_least(OrgRole.ADMIN)
        assert OrgRole.ADMIN.at_least(OrgRole.MEMBER)
        assert OrgRole.MEMBER.at_least(OrgRole.VIEWER)
        assert OrgRole.VIEWER.at_least(OrgRole.VIEWER)
        assert not OrgRole.VIEWER.at_least(OrgRole.MEMBER)
        assert not OrgRole.MEMBER.at_least(OrgRole.ADMIN)
        assert not OrgRole.ADMIN.at_least(OrgRole.OWNER)

    def test_a_member_can_open_the_teams_project_and_a_stranger_cannot(
        self,
        auth_client: AuthenticatedTestClient,
        sign_in: SignIn,
        shared_organisation: str,
        db_session: Session,
    ) -> None:
        colleague = sign_in("colleague@kryova.dev")
        self._join(auth_client, shared_organisation, colleague, "colleague@kryova.dev", "member")

        # A project owned by the shared organisation rather than a personal one.
        created = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        project = db_session.get(Project, created)
        assert project is not None
        project.organisation_id = shared_organisation
        db_session.flush()

        assert colleague.get(f"/api/v1/projects/{created}").status_code == 200
        assert colleague.patch(
            f"/api/v1/projects/{created}", json={"name": "Press frame rev B"}
        ).status_code == 200

        stranger = sign_in("stranger@kryova.dev")
        assert stranger.get(f"/api/v1/projects/{created}").status_code == 404

    def test_a_viewer_reads_but_does_not_write(
        self,
        auth_client: AuthenticatedTestClient,
        sign_in: SignIn,
        shared_organisation: str,
        db_session: Session,
    ) -> None:
        watcher = sign_in("watcher@kryova.dev")
        self._join(auth_client, shared_organisation, watcher, "watcher@kryova.dev", "viewer")

        created = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        project = db_session.get(Project, created)
        assert project is not None
        project.organisation_id = shared_organisation
        db_session.flush()

        assert watcher.get(f"/api/v1/projects/{created}").status_code == 200
        assert watcher.patch(
            f"/api/v1/projects/{created}", json={"name": "Mine now"}
        ).status_code == 404
        assert watcher.delete(f"/api/v1/projects/{created}").status_code == 404
        # And a viewer cannot invite anybody in behind them.
        assert watcher.post(
            f"/api/v1/organisations/{shared_organisation}/invitations",
            json={"email": "someone@kryova.dev"},
        ).status_code == 404

    def test_a_member_cannot_administer_the_organisation(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn, shared_organisation: str
    ) -> None:
        colleague = sign_in("colleague@kryova.dev")
        self._join(auth_client, shared_organisation, colleague, "colleague@kryova.dev", "member")

        assert colleague.get(f"/api/v1/organisations/{shared_organisation}").status_code == 200
        assert colleague.patch(
            f"/api/v1/organisations/{shared_organisation}", json={"name": "Mine"}
        ).status_code == 404
        assert colleague.get(
            f"/api/v1/organisations/{shared_organisation}/invitations"
        ).status_code == 404

    def test_an_admin_cannot_mint_an_owner(
        self, auth_client: AuthenticatedTestClient, sign_in: SignIn, shared_organisation: str
    ) -> None:
        """The one promotion an admin must not have: it is how an admin takes
        the tenant from the person who made it."""
        deputy = sign_in("deputy@kryova.dev")
        self._join(auth_client, shared_organisation, deputy, "deputy@kryova.dev", "admin")

        refused = deputy.post(
            f"/api/v1/organisations/{shared_organisation}/invitations",
            json={"email": "accomplice@kryova.dev", "role": "owner"},
        )
        assert refused.status_code == 403

        accomplice = sign_in("accomplice@kryova.dev")
        self._join(
            auth_client, shared_organisation, accomplice, "accomplice@kryova.dev", "member"
        )
        promoted = deputy.patch(
            f"/api/v1/organisations/{shared_organisation}/members/"
            f"{accomplice.get('/api/v1/auth/me').json()['id']}",
            json={"role": "owner"},
        )
        assert promoted.status_code == 404

    def test_the_last_owner_cannot_be_demoted_or_removed(
        self, auth_client: AuthenticatedTestClient, current_user_id: str, shared_organisation: str
    ) -> None:
        demoted = auth_client.patch(
            f"/api/v1/organisations/{shared_organisation}/members/{current_user_id}",
            json={"role": "admin"},
        )
        assert demoted.status_code == 409
        removed = auth_client.delete(
            f"/api/v1/organisations/{shared_organisation}/members/{current_user_id}"
        )
        assert removed.status_code == 409

    def test_removing_a_member_removes_their_access(
        self,
        auth_client: AuthenticatedTestClient,
        sign_in: SignIn,
        shared_organisation: str,
        db_session: Session,
    ) -> None:
        colleague = sign_in("colleague@kryova.dev")
        self._join(auth_client, shared_organisation, colleague, "colleague@kryova.dev", "member")

        created = auth_client.post("/api/v1/projects", json={"name": "Press frame"}).json()["id"]
        project = db_session.get(Project, created)
        assert project is not None
        project.organisation_id = shared_organisation
        db_session.flush()
        assert colleague.get(f"/api/v1/projects/{created}").status_code == 200

        member_id = colleague.get("/api/v1/auth/me").json()["id"]
        assert auth_client.delete(
            f"/api/v1/organisations/{shared_organisation}/members/{member_id}"
        ).status_code == 204
        assert colleague.get(f"/api/v1/projects/{created}").status_code == 404


class TestAUserCreatedInsideOneFlush:
    """A regression, found only by running the whole suite against Postgres.

    `personal_slug` read `user.id`, and `UUIDPrimaryKey` supplies that with a
    Python-side `default=` which SQLAlchemy applies **during** the flush. The
    hook that creates a personal organisation runs on `before_flush`, which is
    necessarily earlier — so a brand-new `User` still had `id is None` and every
    creation raised `AttributeError: 'NoneType' object has no attribute
    'replace'`.

    It did not show up in the tenancy tests because those build a user, flush,
    and *then* act. `tests/test_startup.py` creates one inside a single flush,
    which is what an ordinary registration does.
    """

    def test_a_project_for_a_brand_new_user_gets_an_organisation(self, db_session) -> None:
        """The path that actually broke. The hook places a `Project` that was
        constructed without a tenant — which is what `app/ai/tools.py` does —
        and to do that it must build the owner's personal organisation, whose
        slug needs the owner's id. A user and their first project created in
        one flush is exactly the ordinary registration case."""
        from app.models import Organisation, Project, User

        person = User(email="fresh@example.com", hashed_password="x")
        project = Project(name="First", owner=person)
        db_session.add_all([person, project])
        db_session.flush()

        assert person.id is not None
        assert project.organisation_id is not None
        organisation = db_session.get(Organisation, project.organisation_id)
        assert organisation is not None
        assert organisation.slug.endswith(person.id.replace("-", "")[:8])

    def test_the_slug_refers_to_the_id_the_row_actually_got(self, db_session) -> None:
        """The fix assigns the id rather than inventing a separate one, so the
        slug and the row cannot disagree."""
        from app.models import User
        from app.models.organisation import personal_slug

        person = User(email="stable@example.com", hashed_password="x")
        slug = personal_slug(person)
        assigned = person.id
        db_session.add(person)
        db_session.flush()

        assert person.id == assigned
        assert slug.endswith(person.id.replace("-", "")[:8])
