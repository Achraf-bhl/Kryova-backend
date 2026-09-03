"""Resolving an uploaded file for `catia_import`, and refusing to reach past it.

`catia_import` is the operation that lets a customer's existing data into the
product, and it is also the one with the widest blast radius if the name it
takes is resolved carelessly: a model naming a path would be an arbitrary-read
primitive on an engineer's workstation, and a model naming another project's
upload would be a cross-tenant read.

Neither is possible by construction — the model supplies a *name*, the server
looks it up inside this conversation's own project, and the bytes travel with
the call. These are the checks that it stays that way.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy.orm import Session

from app.catia.dispatch import CatiaError, _uploaded_file
from app.media import get_media_store
from app.models import Conversation, MediaKind, Project, User
from app.models.geometry import GeometryVersion
from app.models.media import Media

STEP_BYTES = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="importer@example.com", hashed_password="x", full_name="Importer")
    db_session.add(account)
    db_session.flush()
    return account


def _upload(db_session: Session, owner: User, project: Project, filename: str) -> GeometryVersion:
    """A real upload: bytes in the store, a Media row, a GeometryVersion."""
    blob = get_media_store().write_bytes(STEP_BYTES)
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename=filename,
        content_type="model/step",
        size_bytes=blob.size_bytes,
        sha256=blob.digest,
    )
    db_session.add(media)
    db_session.flush()
    version = GeometryVersion(
        project_id=project.id,
        media_id=media.id,
        version_number=1,
        filename=filename,
        file_format="step",
    )
    db_session.add(version)
    db_session.flush()
    return version


def _conversation(db_session: Session, owner: User, project: Project | None) -> Conversation:
    row = Conversation(
        owner_id=owner.id, project_id=project.id if project else None, title="t"
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def project(db_session: Session, user: User) -> Project:
    row = Project(name="Bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


class TestResolution:
    def test_a_named_upload_travels_as_bytes(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        # The daemon runs on the engineer's own machine, so a server path would
        # mean nothing there. The bytes go with the call, like a checkpoint's.
        _upload(db_session, user, project, "supplier.stp")
        conversation = _conversation(db_session, user, project)

        payload = _uploaded_file(db_session, conversation.id, "supplier.stp")

        assert base64.b64decode(payload["content_b64"]) == STEP_BYTES
        assert payload["filename"] == "supplier.stp"
        assert payload["content_hash"] == hashlib.sha256(STEP_BYTES).hexdigest()

    def test_the_name_may_be_given_without_its_extension(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        # The model has usually seen the name in a file listing and quotes it
        # back either way; refusing one spelling is a dead end it cannot debug.
        _upload(db_session, user, project, "supplier.stp")
        conversation = _conversation(db_session, user, project)

        assert _uploaded_file(db_session, conversation.id, "supplier")["filename"] == (
            "supplier.stp"
        )

    def test_an_unknown_name_lists_what_the_project_does_have(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        _upload(db_session, user, project, "supplier.stp")
        conversation = _conversation(db_session, user, project)

        with pytest.raises(CatiaError, match="supplier.stp"):
            _uploaded_file(db_session, conversation.id, "nothing-like-this.stp")


class TestScoping:
    def test_another_project_s_upload_is_not_reachable(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        # The same owner, a different project. Scoping to the conversation's
        # project is the access control -- and the refusal is worded as "no such
        # file", not "not allowed", so a probe learns nothing either way.
        other = Project(name="Confidential", owner_id=user.id)
        db_session.add(other)
        db_session.flush()
        _upload(db_session, user, other, "secret.stp")
        conversation = _conversation(db_session, user, project)

        with pytest.raises(CatiaError, match="no uploaded file called"):
            _uploaded_file(db_session, conversation.id, "secret.stp")

    def test_a_conversation_with_no_project_has_nothing_to_import(
        self, db_session: Session, user: User
    ) -> None:
        conversation = _conversation(db_session, user, None)

        with pytest.raises(CatiaError, match="not attached to a project"):
            _uploaded_file(db_session, conversation.id, "anything.stp")

    def test_no_conversation_at_all_is_refused_rather_than_searched(
        self, db_session: Session
    ) -> None:
        # Without a conversation there is no project to scope to, and a lookup
        # that fell back to "search everything" is exactly the bug this guards.
        with pytest.raises(CatiaError, match="not attached to a project"):
            _uploaded_file(db_session, None, "anything.stp")
