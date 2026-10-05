"""The agent asks the HTTP layer's question about a project, once (ROAD_TO_10 7.4).

`ToolBox._project` used to ask for the project's *owner*. A colleague with a perfectly good
`MEMBER` role was told "no project belongs to you" by the agent about a project the button
opened (flagged 2026-09-15, *Known landmines* 7). It now asks for a role in the owning
organisation, which is what `get_readable_project` and `get_owned_project` ask: a viewer reads,
a member changes and runs, a stranger is told the same sentence as for an id that does not exist
so an id cannot be probed.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.ai.tools import ToolBox, ToolError
from app.core.security import hash_password
from app.models import Membership, Organisation, OrgRole, Project, User
from app.models.base import utcnow


def _user(db: Session, email: str) -> User:
    account = User(email=email, hashed_password=hash_password("a-long-enough-password"))
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def team(db_session: Session) -> dict[str, object]:
    organisation = Organisation(name="Press shop", slug="press-shop", is_personal=False)
    db_session.add(organisation)
    db_session.flush()
    people = {role: _user(db_session, f"{role.value}@shop.dev") for role in OrgRole}
    for role, person in people.items():
        db_session.add(Membership(organisation_id=organisation.id, user_id=person.id, role=role))
    stranger = _user(db_session, "stranger@elsewhere.dev")
    db_session.flush()
    project = Project(
        name="Punch press",
        owner_id=people[OrgRole.OWNER].id,
        organisation_id=organisation.id,
    )
    db_session.add(project)
    db_session.flush()
    for person in (*people.values(), stranger):
        db_session.expire(person, ["memberships"])
    return {"project": project, "people": people, "stranger": stranger}


def _box(db: Session, user: User) -> ToolBox:
    return ToolBox(db=db, user=user, project_id=None)


class TestAViewerReads:
    def test_a_viewer_can_read_a_project_they_did_not_create(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        viewer = team["people"][OrgRole.VIEWER]  # type: ignore[index]
        result = _box(db_session, viewer).call(
            "get_project", {"project_id": project.id}, allow_mutations=False  # type: ignore[attr-defined]
        )
        assert result["name"] == "Punch press"

    def test_a_viewer_gets_past_the_role_check_on_a_read_tool(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        viewer = team["people"][OrgRole.VIEWER]  # type: ignore[index]
        # The project has no geometry, so the tool's own answer is "has no geometry yet" --
        # which is what proves the *role* check let a viewer through to it.
        with pytest.raises(ToolError, match="has no geometry yet"):
            _box(db_session, viewer).call(
                "list_geometry", {"project_id": project.id}, allow_mutations=False  # type: ignore[attr-defined]
            )


class TestAViewerDoesNotWrite:
    def test_a_viewer_cannot_rename_it(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        viewer = team["people"][OrgRole.VIEWER]  # type: ignore[index]
        with pytest.raises(ToolError, match="belongs to you or to an organisation in which you may change it"):
            _box(db_session, viewer).call(
                "update_project",
                {"project_id": project.id, "name": "Mine now"},  # type: ignore[attr-defined]
                allow_mutations=True,
            )
        assert project.name == "Punch press"  # type: ignore[attr-defined]

    def test_a_viewer_cannot_delete_it(self, db_session: Session, team: dict[str, object]) -> None:
        project = team["project"]
        viewer = team["people"][OrgRole.VIEWER]  # type: ignore[index]
        with pytest.raises(ToolError, match="in which you may change it"):
            _box(db_session, viewer).call(
                "delete_project",
                {"project_id": project.id},  # type: ignore[attr-defined]
                allow_mutations=True,
            )
        assert db_session.get(Project, project.id) is not None  # type: ignore[attr-defined]


class TestAMemberChangesAndRuns:
    def test_a_member_can_rename_a_project_they_did_not_create(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        member = team["people"][OrgRole.MEMBER]  # type: ignore[index]
        _box(db_session, member).call(
            "update_project",
            {"project_id": project.id, "name": "Punch press rev B"},  # type: ignore[attr-defined]
            allow_mutations=True,
        )
        assert project.name == "Punch press rev B"  # type: ignore[attr-defined]


class TestAStrangerIsToldNothing:
    def test_a_stranger_gets_the_same_sentence_as_for_an_id_that_does_not_exist(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        stranger = team["stranger"]
        messages = []
        for project_id in (project.id, "no-such-id"):  # type: ignore[attr-defined]
            with pytest.raises(ToolError) as refused:
                _box(db_session, stranger).call(  # type: ignore[arg-type]
                    "get_project", {"project_id": project_id}, allow_mutations=False
                )
            messages.append(str(refused.value).replace(project_id, "X"))
        assert messages[0] == messages[1]


class TestListingShowsTheTeamsProjects:
    def test_list_projects_includes_a_colleagues_project_and_not_an_archived_one(
        self, db_session: Session, team: dict[str, object]
    ) -> None:
        project = team["project"]
        member = team["people"][OrgRole.MEMBER]  # type: ignore[index]
        archived = Project(
            name="Old press",
            owner_id=member.id,
            organisation_id=project.organisation_id,  # type: ignore[attr-defined]
            archived_at=utcnow(),
        )
        db_session.add(archived)
        db_session.flush()
        names = {
            row["name"]
            for row in _box(db_session, member).call(
                "list_projects", {}, allow_mutations=False
            )["projects"]
        }
        assert "Punch press" in names
        assert "Old press" not in names
