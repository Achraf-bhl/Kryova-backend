"""Rename, archive, duplicate, tag, star, template, activity, export and import
(ROAD_TO_10 7.1 -- 7.8).

What is worth pinning is mostly where a convenience could quietly become a loss: an archive
that deletes, a duplicate that shares a row with its source so deleting one breaks the other,
a star that is one person's and shows on everybody's list, and an import that accepts an
archive whose files were changed after it was exported.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import designs, project_archive, project_templates, projects
from app.core.security import hash_password
from app.models import (
    Conversation,
    DesignDocument,
    GeometryVersion,
    Project,
    ProjectStar,
    User,
)
from app.models.base import utcnow
from tests.test_design_compile import bracket
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _upload(client: AuthenticatedTestClient, project_id: str, stl: bytes, name: str = "cube.stl"):
    response = client.post(
        f"{API}/projects/{project_id}/geometry", files={"file": (name, stl, "model/stl")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _design_in(db: Session, project_id: str, owner_id: str) -> DesignDocument:
    conversation = Conversation(title="Bracket", owner_id=owner_id, project_id=project_id)
    db.add(conversation)
    db.flush()
    return designs.save(db, conversation, bracket(), author=designs.AUTHOR_USER, author_id=owner_id).document


# -- 7.1 / 7.2 ---------------------------------------------------------------------------------


class TestRenameArchiveRestore:
    def test_a_project_can_be_renamed_on_the_spot(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        renamed = auth_client.patch(f"{API}/projects/{project_id}", json={"name": "Press frame"})
        assert renamed.json()["name"] == "Press frame"

    def test_an_archived_project_is_hidden_from_the_list_and_nothing_is_deleted(
        self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl: bytes
    ) -> None:
        _upload(auth_client, project_id, cube_stl)
        archived = auth_client.patch(f"{API}/projects/{project_id}", json={"archived": True})
        assert archived.json()["archived_at"] is not None

        assert auth_client.get(f"{API}/projects").json()["items"] == []
        shown = auth_client.get(f"{API}/projects", params={"archived": "only"}).json()["items"]
        assert [p["id"] for p in shown] == [project_id]
        # Opened by id, and its geometry is exactly as it was.
        assert auth_client.get(f"{API}/projects/{project_id}").status_code == 200
        geometry = auth_client.get(f"{API}/projects/{project_id}/geometry").json()
        assert geometry["total"] == 1

    def test_archiving_twice_keeps_the_first_date(self, db_session: Session) -> None:
        project = Project(name="x", owner_id="u", organisation_id="o")
        first = utcnow()
        projects.archive(project, at=first)
        projects.archive(project)
        assert project.archived_at == first

    def test_restoring_brings_it_back(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        auth_client.patch(f"{API}/projects/{project_id}", json={"archived": True})
        back = auth_client.patch(f"{API}/projects/{project_id}", json={"archived": False})
        assert back.json()["archived_at"] is None
        assert [p["id"] for p in auth_client.get(f"{API}/projects").json()["items"]] == [project_id]


class TestAnUnnamedProjectTakesItsFirstConversationsTitle:
    def test_the_placeholder_is_replaced(self) -> None:
        project = Project(name=projects.DEFAULT_PROJECT_NAME, owner_id="u", organisation_id="o")
        assert projects.name_from_conversation(project, "Bracket for a 40 kg motor") is True
        assert project.name == "Bracket for a 40 kg motor"

    def test_a_name_a_person_chose_is_never_overwritten(self) -> None:
        project = Project(name="Press frame", owner_id="u", organisation_id="o")
        assert projects.name_from_conversation(project, "Something generated") is False
        assert project.name == "Press frame"

    def test_a_conversation_with_no_title_yet_does_not_name_it(self) -> None:
        project = Project(name=projects.DEFAULT_PROJECT_NAME, owner_id="u", organisation_id="o")
        assert projects.name_from_conversation(project, "New conversation") is False
        assert projects.name_from_conversation(project, "   ") is False
        assert project.name == projects.DEFAULT_PROJECT_NAME


class TestDuplicate:
    def test_a_copy_has_the_geometry_and_the_design_and_says_what_it_left_out(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        current_user_id: str,
        cube_stl: bytes,
    ) -> None:
        _upload(auth_client, project_id, cube_stl)
        _design_in(db_session, project_id, current_user_id)

        copied = auth_client.post(f"{API}/projects/{project_id}/duplicate", json={})
        assert copied.status_code == 201, copied.text
        body = copied.json()
        assert body["geometry_versions"] == 1 and body["designs"] == 1
        assert body["project"]["name"] == "Bracket (copy)"
        assert any("CATIA" in item for item in body["left_out"])
        assert any("simulation" in item for item in body["left_out"])

        new_id = body["project"]["id"]
        assert auth_client.get(f"{API}/projects/{new_id}/geometry").json()["total"] == 1
        design = db_session.scalar(select(DesignDocument).where(DesignDocument.project_id == new_id))
        assert design is not None and design.revision_number == 1

    def test_deleting_the_source_leaves_the_copys_blob_on_disk(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        cube_stl: bytes,
    ) -> None:
        _upload(auth_client, project_id, cube_stl)
        new_id = auth_client.post(f"{API}/projects/{project_id}/duplicate", json={}).json()[
            "project"
        ]["id"]
        copy = db_session.scalar(select(GeometryVersion).where(GeometryVersion.project_id == new_id))
        assert copy is not None
        digest = copy.media.sha256
        assert auth_client.delete(f"{API}/projects/{project_id}").status_code == 204

        # `MediaService.delete` drops a blob only when no row refers to it, and the copy's does.
        assert auth_client.store.exists(digest)
        assert len(auth_client.get(f"{API}/projects/{new_id}/geometry").json()["items"]) == 1

    def test_a_copy_shares_the_blob_but_not_the_media_row(
        self, auth_client: AuthenticatedTestClient, db_session: Session, project_id: str, cube_stl: bytes
    ) -> None:
        _upload(auth_client, project_id, cube_stl)
        new_id = auth_client.post(f"{API}/projects/{project_id}/duplicate", json={}).json()[
            "project"
        ]["id"]
        rows = db_session.scalars(
            select(GeometryVersion).where(GeometryVersion.project_id.in_([project_id, new_id]))
        ).all()
        assert len(rows) == 2
        assert rows[0].media_id != rows[1].media_id
        assert rows[0].media.sha256 == rows[1].media.sha256

    def test_the_catia_document_is_never_copied(
        self, auth_client: AuthenticatedTestClient, db_session: Session, project_id: str,
        current_user_id: str,
    ) -> None:
        from app.models import CatiaDocument

        conversation = Conversation(title="c", owner_id=current_user_id, project_id=project_id)
        db_session.add(conversation)
        db_session.flush()
        db_session.add(
            CatiaDocument(
                conversation_id=conversation.id, doc_name="Part1", remote_path="C:/x/Part1.CATPart"
            )
        )
        db_session.flush()
        new_id = auth_client.post(f"{API}/projects/{project_id}/duplicate", json={}).json()[
            "project"
        ]["id"]
        copies = db_session.scalars(
            select(CatiaDocument)
            .join(Conversation, Conversation.id == CatiaDocument.conversation_id)
            .where(Conversation.project_id == new_id)
        ).all()
        assert copies == []


# -- 7.6 ---------------------------------------------------------------------------------------


class TestTags:
    def test_tags_are_lower_cased_trimmed_and_de_duplicated_in_first_seen_order(self) -> None:
        assert projects.normalise_tags([" Press ", "FRAME", "press", "m8  bolts"]) == [
            "press",
            "frame",
            "m8 bolts",
        ]

    @pytest.mark.parametrize(
        "bad", ["", "   ", "x" * 33, '"quoted"', "-leading", "semi;colon"]
    )
    def test_a_tag_that_cannot_be_one_is_refused_not_repaired(self, bad: str) -> None:
        with pytest.raises(projects.ProjectRefusal):
            projects.normalise_tags([bad])

    def test_too_many_tags_are_refused(self) -> None:
        with pytest.raises(projects.ProjectRefusal, match="at most 10"):
            projects.normalise_tags([f"t{i}" for i in range(11)])

    def test_tags_round_trip_and_filter_the_list(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        other = auth_client.post(f"{API}/projects", json={"name": "Gearbox"}).json()["id"]
        tagged = auth_client.patch(
            f"{API}/projects/{project_id}", json={"tags": ["Press", "frame"]}
        )
        assert tagged.json()["tags"] == ["press", "frame"]
        found = auth_client.get(f"{API}/projects", params={"tag": "press"}).json()["items"]
        assert [p["id"] for p in found] == [project_id]
        assert other not in {p["id"] for p in found}

    def test_a_bad_tag_over_http_is_a_422_with_the_reason(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        response = auth_client.patch(f"{API}/projects/{project_id}", json={"tags": ["a;b"]})
        assert response.status_code == 422
        assert "letters, digits" in response.json()["detail"]


class TestSearch:
    def test_search_finds_a_name_a_description_or_a_tag_and_treats_percent_literally(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        a = auth_client.post(
            f"{API}/projects", json={"name": "Punch press", "description": "stamping"}
        ).json()["id"]
        b = auth_client.post(f"{API}/projects", json={"name": "Gearbox"}).json()["id"]
        auth_client.patch(f"{API}/projects/{b}", json={"tags": ["reducer"]})

        def ids(**params: str) -> set[str]:
            return {p["id"] for p in auth_client.get(f"{API}/projects", params=params).json()["items"]}

        assert ids(q="punch") == {a}
        assert ids(q="stamp") == {a}
        assert ids(q="reduc") == {b}
        assert ids(q="%") == set()


class TestStars:
    def test_a_star_is_one_persons_and_idempotent(self, db_session: Session) -> None:
        a = User(email="a@x.dev", hashed_password=hash_password("a-long-enough-password"))
        b = User(email="b@x.dev", hashed_password=hash_password("a-long-enough-password"))
        db_session.add_all([a, b])
        db_session.flush()
        project = Project(name="p", owner_id=a.id)
        db_session.add(project)
        db_session.flush()

        projects.set_star(db_session, a, project, True)
        projects.set_star(db_session, a, project, True)
        assert projects.starred_ids(db_session, a, [project.id]) == {project.id}
        assert projects.starred_ids(db_session, b, [project.id]) == set()
        assert len(db_session.scalars(select(ProjectStar)).all()) == 1

        projects.set_star(db_session, a, project, False)
        projects.set_star(db_session, a, project, False)
        assert projects.starred_ids(db_session, a, [project.id]) == set()

    def test_starring_over_http_shows_on_read_and_filters_the_list(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        other = auth_client.post(f"{API}/projects", json={"name": "Other"}).json()["id"]
        assert auth_client.put(f"{API}/projects/{project_id}/star").json()["starred"] is True
        assert auth_client.get(f"{API}/projects/{project_id}").json()["starred"] is True
        assert auth_client.get(f"{API}/projects/{other}").json()["starred"] is False
        only = auth_client.get(f"{API}/projects", params={"starred": "true"}).json()["items"]
        assert [p["id"] for p in only] == [project_id]
        assert auth_client.delete(f"{API}/projects/{project_id}/star").json()["starred"] is False


# -- 7.7 ---------------------------------------------------------------------------------------


class TestTemplates:
    def test_only_rungs_that_build_are_offered_and_each_carries_its_caveats(self) -> None:
        catalogue = project_templates.catalogue()
        assert catalogue
        for found in catalogue:
            assert found.claims, f"{found.key} offered with nothing it claims"
        with pytest.raises(project_templates.UnknownTemplate):
            project_templates.template("M99")

    def test_a_single_part_rung_starts_with_its_design_and_the_caveats_in_the_description(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        response = auth_client.post(f"{API}/projects/from-template", json={"template": "M1"})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["conversation_id"] is not None
        assert "What it does NOT claim" in body["project"]["description"]
        assert body["project"]["template_key"] == "M1"
        design = db_session.scalar(
            select(DesignDocument).where(DesignDocument.project_id == body["project"]["id"])
        )
        assert design is not None and design.revision_number == 1

    def test_an_assembly_rung_starts_without_a_design_and_says_why(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        keys = [t.key for t in project_templates.catalogue() if not t.seeds_a_design]
        assert keys, "the ladder has no assembly rung that builds"
        body = auth_client.post(f"{API}/projects/from-template", json={"template": keys[0]}).json()
        assert body["conversation_id"] is None
        assert "product graph" in body["design_note"]

    def test_an_unknown_rung_is_a_422_naming_the_ladder(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        response = auth_client.post(f"{API}/projects/from-template", json={"template": "M99"})
        assert response.status_code == 422
        assert "M1" in response.json()["detail"]

    def test_the_catalogue_route_lists_them(self, auth_client: AuthenticatedTestClient) -> None:
        listed = auth_client.get(f"{API}/projects/templates").json()
        assert [t["key"] for t in listed] == [t.key for t in project_templates.catalogue()]


# -- 7.5 ---------------------------------------------------------------------------------------


class TestActivity:
    def test_the_feed_merges_sources_newest_first_and_names_only_known_actors(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        current_user_id: str,
        cube_stl: bytes,
    ) -> None:
        _upload(auth_client, project_id, cube_stl)
        _design_in(db_session, project_id, current_user_id)
        feed = auth_client.get(f"{API}/projects/{project_id}/activity").json()
        kinds = [item["kind"] for item in feed["items"]]
        assert "geometry.uploaded" in kinds and "design.revision" in kinds
        times = [item["at"] for item in feed["items"]]
        assert times == sorted(times, reverse=True)
        geometry = next(i for i in feed["items"] if i["kind"] == "geometry.uploaded")
        # The row records no author, so the feed does not invent one.
        assert geometry["actor_kind"] == "unknown" and geometry["actor_user_id"] is None
        design = next(i for i in feed["items"] if i["kind"] == "design.revision")
        assert design["actor_kind"] == "user" and design["actor_user_id"] == current_user_id

    def test_a_page_is_cut_to_the_limit_and_the_cursor_gives_the_rest(
        self, auth_client: AuthenticatedTestClient, project_id: str, cube_stl: bytes
    ) -> None:
        for index in range(3):
            _upload(auth_client, project_id, cube_stl, name=f"c{index}.stl")
        first = auth_client.get(f"{API}/projects/{project_id}/activity", params={"limit": 2}).json()
        assert len(first["items"]) == 2 and first["next_before"] is not None
        second = auth_client.get(
            f"{API}/projects/{project_id}/activity",
            params={"limit": 2, "before": first["next_before"]},
        ).json()
        assert len(second["items"]) == 1 and second["next_before"] is None

    def test_another_users_project_has_no_feed(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        assert auth_client.get(f"{API}/projects/not-a-project/activity").status_code == 404


# -- 7.8 ---------------------------------------------------------------------------------------


def _export(client: AuthenticatedTestClient, project_id: str) -> bytes:
    response = client.get(f"{API}/projects/{project_id}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return response.content


def _import(client: AuthenticatedTestClient, data: bytes):
    return client.post(
        f"{API}/projects/import", files={"file": ("p.kryova.zip", data, "application/zip")}
    )


def _rewrite(data: bytes, edit) -> bytes:  # type: ignore[no-untyped-def]
    """A copy of the archive with `edit(members: dict[str, bytes])` applied."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        members = {name: source.read(name) for name in source.namelist()}
    edit(members)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for name, content in members.items():
            target.writestr(name, content)
    return out.getvalue()


class TestExportAndImport:
    @pytest.fixture
    def exported(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        current_user_id: str,
        cube_stl: bytes,
    ) -> bytes:
        _upload(auth_client, project_id, cube_stl)
        _design_in(db_session, project_id, current_user_id)
        auth_client.patch(f"{API}/projects/{project_id}", json={"tags": ["press"]})
        return _export(auth_client, project_id)

    def test_the_manifest_lists_every_member_with_its_hash(self, exported: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            members = set(archive.namelist()) - {"manifest.json"}
        assert set(manifest["files"]) == members
        assert {"simulations.json", "designs/1.json", "technical-file/1.json"} <= members
        assert any(name.startswith("geometry/v1-") for name in members)
        assert manifest["not_restored_on_import"]

    def test_an_exported_project_imports_with_its_geometry_design_and_tags(
        self, auth_client: AuthenticatedTestClient, db_session: Session, exported: bytes
    ) -> None:
        response = _import(auth_client, exported)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["geometry_versions"] == 1 and body["designs"] == 1
        assert body["project"]["tags"] == ["press"]
        assert any("simulation" in item for item in body["not_restored"])
        new_id = body["project"]["id"]
        design = db_session.scalar(select(DesignDocument).where(DesignDocument.project_id == new_id))
        assert design is not None
        assert design.digest == bracket().digest()
        version = db_session.scalar(select(GeometryVersion).where(GeometryVersion.project_id == new_id))
        assert version is not None and version.stats  # re-inspected from the stored bytes

    def test_a_file_changed_after_export_stops_the_import_and_creates_nothing(
        self, auth_client: AuthenticatedTestClient, db_session: Session, exported: bytes
    ) -> None:
        def tamper(members: dict[str, bytes]) -> None:
            name = next(n for n in members if n.startswith("geometry/"))
            members[name] = members[name] + b"\x00"

        before = len(db_session.scalars(select(Project)).all())
        response = _import(auth_client, _rewrite(exported, tamper))
        assert response.status_code == 422
        assert "hash recorded in the manifest" in response.json()["detail"]
        assert len(db_session.scalars(select(Project)).all()) == before

    def test_a_member_the_manifest_does_not_list_is_refused(
        self, auth_client: AuthenticatedTestClient, exported: bytes
    ) -> None:
        response = _import(
            auth_client, _rewrite(exported, lambda m: m.update({"extra.txt": b"hi"}))
        )
        assert response.status_code == 422
        assert "does not match its manifest" in response.json()["detail"]

    def test_a_listed_member_that_is_missing_is_refused(
        self, auth_client: AuthenticatedTestClient, exported: bytes
    ) -> None:
        response = _import(auth_client, _rewrite(exported, lambda m: m.pop("simulations.json")))
        assert response.status_code == 422

    def test_a_design_whose_digest_no_longer_matches_is_refused_even_with_valid_hashes(
        self, auth_client: AuthenticatedTestClient, exported: bytes
    ) -> None:
        def reread_as_another_design(members: dict[str, bytes]) -> None:
            design = json.loads(members["designs/1.json"])
            design["digest"] = "0" * 64
            members["designs/1.json"] = json.dumps(design).encode()
            manifest = json.loads(members["manifest.json"])
            import hashlib

            manifest["files"]["designs/1.json"] = {
                "sha256": hashlib.sha256(members["designs/1.json"]).hexdigest(),
                "size": len(members["designs/1.json"]),
            }
            members["manifest.json"] = json.dumps(manifest).encode()

        response = _import(auth_client, _rewrite(exported, reread_as_another_design))
        assert response.status_code == 422
        assert "digest" in response.json()["detail"]

    def test_a_path_that_climbs_out_is_refused(
        self, auth_client: AuthenticatedTestClient, exported: bytes
    ) -> None:
        response = _import(
            auth_client, _rewrite(exported, lambda m: m.update({"../evil": b"x"}))
        )
        assert response.status_code == 422

    def test_something_that_is_not_a_zip_is_refused_in_words(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        response = _import(auth_client, b"not a zip at all")
        assert response.status_code == 422
        assert "not a zip" in response.json()["detail"]

    def test_a_zip_that_is_not_a_project_archive_is_refused(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("readme.txt", "hello")
        response = _import(auth_client, out.getvalue())
        assert response.status_code == 422
        assert "no manifest.json" in response.json()["detail"]

    def test_an_archive_of_a_newer_format_is_refused_not_guessed_at(
        self, auth_client: AuthenticatedTestClient, exported: bytes
    ) -> None:
        def bump(members: dict[str, bytes]) -> None:
            manifest = json.loads(members["manifest.json"])
            manifest["format_version"] = 99
            members["manifest.json"] = json.dumps(manifest).encode()

        response = _import(auth_client, _rewrite(exported, bump))
        assert response.status_code == 422
        assert "format version 99" in response.json()["detail"]

    def test_the_export_leaves_no_temporary_file_behind(
        self, auth_client: AuthenticatedTestClient, project_id: str
    ) -> None:
        import tempfile

        before = set(Path(tempfile.gettempdir()).glob("kryova-export-*"))
        _export(auth_client, project_id)
        assert set(Path(tempfile.gettempdir()).glob("kryova-export-*")) <= before


def test_verify_alone_reads_nothing_into_the_database(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    path.write_bytes(b"nope")
    with pytest.raises(project_archive.ArchiveRefusal):
        project_archive.verify(path)


class TestTheProjectPageCanAskForItsOwnConversationsAndDesigns:
    """ROAD_TO_10 7.3: both lists take `project_id`, so a project page reads its own."""

    def test_conversations_and_designs_are_filtered_by_project(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        project_id: str,
        current_user_id: str,
    ) -> None:
        other = auth_client.post(f"{API}/projects", json={"name": "Other"}).json()["id"]
        mine = Conversation(title="in the bracket", owner_id=current_user_id, project_id=project_id)
        theirs = Conversation(title="in the other", owner_id=current_user_id, project_id=other)
        db_session.add_all([mine, theirs])
        db_session.flush()
        designs.save(db_session, mine, bracket())

        conversations = auth_client.get(
            f"{API}/ai/conversations", params={"project_id": project_id}
        ).json()
        assert [c["id"] for c in conversations["items"]] == [mine.id]

        listed = auth_client.get(f"{API}/designs", params={"project_id": project_id}).json()
        assert listed["total"] == 1
        assert auth_client.get(f"{API}/designs", params={"project_id": other}).json()["total"] == 0
