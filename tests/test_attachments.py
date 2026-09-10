"""Attachments as first-class objects, with their citations (P4 tasks 1, 3, 4, 6).

The readers were done at P4.2 and the injection boundary at P4.5. What is new is
the *row*, and the claims worth pinning are the ones where a plausible
implementation would quietly lose something: a file that could not be read
vanishing from the list, an unsupported format counted as a failure, an OCR read
presented without its warning, and a number lifted out of a spreadsheet arriving
in a design with no way back to the cell it came from.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core import attachments
from app.documents.provenance import Locator, Reliability, SourceRef
from app.models import Conversation, Media, MediaKind, User
from app.models.attachment import Attachment, ExtractionStatus
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


@pytest.fixture
def owner(db_session: Session) -> User:
    user = User(email="attacher@kryova.dev", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def conversation(db_session: Session, owner: User) -> Conversation:
    row = Conversation(title="Bracket", owner_id=owner.id)
    db_session.add(row)
    db_session.flush()
    return row


def _media(db: Session, owner: User, filename: str, sha: str = "c" * 64) -> Media:
    media = Media(
        owner_id=owner.id,
        kind=MediaKind.CAD,
        filename=filename,
        size_bytes=64,
        sha256=sha,
        meta={},
    )
    db.add(media)
    db.flush()
    return media


class TestIngestion:
    def test_a_readable_file_becomes_a_ready_attachment(
        self, db_session: Session, owner: User, conversation: Conversation, tmp_path: Path
    ) -> None:
        path = tmp_path / "notes.txt"
        path.write_text("Wall thickness 8 mm.\nMaterial S235.\n", encoding="utf-8")

        ingested = attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "notes.txt"),
            filename="notes.txt",
            path=path,
            conversation=conversation,
        )

        assert ingested.attachment.status is ExtractionStatus.READY
        assert ingested.document is not None
        assert ingested.attachment.extracted is not None
        assert ingested.attachment.conversation_id == conversation.id

    def test_an_unsupported_format_is_recorded_not_refused(
        self, db_session: Session, owner: User, conversation: Conversation, tmp_path: Path
    ) -> None:
        """Refusing loses the fact that the user handed us something and expects
        it to have been seen."""
        path = tmp_path / "part.step"
        path.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")

        ingested = attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "part.step"),
            filename="part.step",
            path=path,
            conversation=conversation,
        )

        assert ingested.attachment.status is ExtractionStatus.UNSUPPORTED
        assert ingested.document is None
        assert ingested.attachment.id is not None

    def test_unsupported_is_not_a_kind_of_failed(self) -> None:
        """Telling somebody their STEP file is broken, when what happened is
        that they put geometry in the document slot, is worse than saying
        nothing."""
        assert ExtractionStatus.UNSUPPORTED is not ExtractionStatus.FAILED
        assert ExtractionStatus.UNSUPPORTED.settled
        assert not ExtractionStatus.UNSUPPORTED.readable

    def test_the_detail_on_an_unsupported_file_names_what_to_do_instead(
        self, db_session: Session, owner: User, tmp_path: Path
    ) -> None:
        path = tmp_path / "part.step"
        path.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")

        ingested = attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "part.step"),
            filename="part.step",
            path=path,
        )

        detail = ingested.attachment.status_detail or ""
        assert "geometry" in detail.lower()

    def test_a_file_that_is_not_there_is_a_failure_and_still_a_row(
        self, db_session: Session, owner: User, tmp_path: Path
    ) -> None:
        """An unreadable file must not lose the row that says it was handed
        over."""
        ingested = attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "gone.pdf"),
            filename="gone.pdf",
            path=tmp_path / "gone.pdf",
        )

        assert ingested.attachment.status is ExtractionStatus.FAILED
        assert ingested.attachment.status_detail

    def test_one_blob_attached_twice_is_two_rows_and_one_blob(
        self, db_session: Session, owner: User, tmp_path: Path
    ) -> None:
        """The dedup P4.1 asks for, and it falls out of the content-addressed
        store rather than being implemented here."""
        path = tmp_path / "sheet.txt"
        path.write_text("40 kg on the top face.", encoding="utf-8")
        media = _media(db_session, owner, "sheet.txt")

        for _ in range(2):
            attachments.attach(
                db_session, owner=owner, media=media, filename="sheet.txt", path=path
            )

        rows = db_session.query(Attachment).filter(Attachment.media_id == media.id).all()
        assert len(rows) == 2
        assert {row.media_id for row in rows} == {media.id}


class TestListing:
    def test_the_list_includes_what_could_not_be_read(
        self, db_session: Session, owner: User, conversation: Conversation, tmp_path: Path
    ) -> None:
        """A panel that silently omitted the STEP file somebody dropped in would
        leave them wondering whether it uploaded at all."""
        readable = tmp_path / "notes.txt"
        readable.write_text("hello", encoding="utf-8")
        unreadable = tmp_path / "part.step"
        unreadable.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
        attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "notes.txt", "d" * 64),
            filename="notes.txt",
            path=readable,
            conversation=conversation,
        )
        attachments.attach(
            db_session,
            owner=owner,
            media=_media(db_session, owner, "part.step", "e" * 64),
            filename="part.step",
            path=unreadable,
            conversation=conversation,
        )

        listed = attachments.list_for(db_session, conversation=conversation)

        assert len(listed) == 2
        assert {row.status for row in listed} == {
            ExtractionStatus.READY,
            ExtractionStatus.UNSUPPORTED,
        }


class TestTheUnverifiedReadLabel:
    def test_an_inferred_read_carries_the_warning(self, db_session: Session, owner: User) -> None:
        """A wrongly read tolerance is worse than an unread one, which is why
        dimension extraction is staged rather than promised."""
        attachment = Attachment(
            owner_id=owner.id,
            media_id=_media(db_session, owner, "scan.pdf", "f" * 64).id,
            filename="scan.pdf",
            reliability=Reliability.INFERRED.value,
        )

        assert attachment.needs_confirmation
        note = attachments.unverified_note(attachment)
        assert note and "Confirm every dimension" in note

    def test_a_transcribed_read_carries_no_warning(
        self, db_session: Session, owner: User
    ) -> None:
        attachment = Attachment(
            owner_id=owner.id,
            media_id=_media(db_session, owner, "text.pdf", "0" * 64).id,
            filename="text.pdf",
            reliability=Reliability.TRANSCRIBED.value,
        )

        assert not attachment.needs_confirmation
        assert attachments.unverified_note(attachment) is None

    def test_the_absent_warning_is_none_and_never_an_empty_string(
        self, db_session: Session, owner: User
    ) -> None:
        """A client cannot render a blank line where a warning belongs and
        believe it has handled the case."""
        attachment = Attachment(
            owner_id=owner.id,
            media_id=_media(db_session, owner, "text.pdf", "1" * 64).id,
            filename="text.pdf",
        )

        assert attachments.unverified_note(attachment) is None

    def test_there_is_one_wording_for_the_label(self) -> None:
        # A safety label with two implementations has two standards.
        assert attachments.UNVERIFIED_NOTE.startswith("Unverified read")


class TestProvenanceTaggedFacts:
    def _source(self, reliability: Reliability = Reliability.TRANSCRIBED) -> SourceRef:
        return SourceRef(
            filename="loads.xlsx",
            digest="a" * 64,
            attachment_id="att-1",
            locator=Locator(sheet="Cases", cell="C7"),
            reader="openpyxl",
            reliability=reliability,
        )

    def test_a_cited_fact_names_the_file_and_the_cell(self) -> None:
        """"Where did 42 mm come from" must answer "cell C7 of loads.xlsx"."""
        line = attachments.cite_fact(self._source(), "42 mm")

        assert "42 mm" in line
        assert "C7" in line
        assert "loads.xlsx" in line

    def test_an_inferred_fact_carries_its_warning_inside_the_citation(self) -> None:
        """A provenance line saying only "cell C7" for a number OCR guessed at
        is a citation that makes an unverified value look checked."""
        line = attachments.cite_fact(self._source(Reliability.INFERRED), "42 mm")

        assert "unverified read" in line

    def test_a_transcribed_fact_does_not_carry_a_warning_it_does_not_need(self) -> None:
        line = attachments.cite_fact(self._source(), "42 mm")

        assert "unverified" not in line


class TestDimensionsAreCandidatesOnly:
    def test_dimension_fragments_are_surfaced_with_their_locators(
        self, db_session: Session, owner: User
    ) -> None:
        attachment = Attachment(
            owner_id=owner.id,
            media_id=_media(db_session, owner, "drawing.dxf", "2" * 64).id,
            filename="drawing.dxf",
            extracted={
                "fragments": [
                    {"kind": "dimension", "text": "40 ±0.1", "where": 'layer "DIM"'},
                    {"kind": "prose", "text": "Machine all over", "where": ""},
                ]
            },
        )

        found = attachments.dimensions_in(attachment)

        assert len(found) == 1
        assert found[0]["where"]

    def test_nothing_here_turns_a_dimension_into_a_parameter(self) -> None:
        """The phase stages dimension and GD&T extraction as later work
        explicitly *not* promised early. This is that promise kept."""
        exported = set(attachments.__all__)

        assert not {name for name in exported if "parameter" in name or "apply" in name}


class TestTheRoutes:
    def _upload(self, auth_client: AuthenticatedTestClient, name: str, body: bytes) -> str:
        stored = auth_client.media.store_stream(
            iter([body]), filename=name, kind=MediaKind.CAD, owner_id=None
        )
        return stored.id

    def test_attaching_a_readable_file_is_201_and_ready(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        media = Media(
            owner_id=current_user_id,
            kind=MediaKind.CAD,
            filename="notes.txt",
            size_bytes=10,
            sha256="3" * 64,
            meta={},
        )
        db_session.add(media)
        db_session.flush()
        path = auth_client.store.path_for(media.sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Wall 8 mm", encoding="utf-8")

        response = auth_client.post(f"{API}/attachments", json={"media_id": media.id})

        assert response.status_code == 201
        assert response.json()["status"] == "ready"

    def test_another_users_blob_is_404(
        self, auth_client: AuthenticatedTestClient, db_session: Session, owner: User
    ) -> None:
        media = _media(db_session, owner, "theirs.txt", "4" * 64)

        response = auth_client.post(f"{API}/attachments", json={"media_id": media.id})

        assert response.status_code == 404

    def test_another_users_attachment_is_404_not_403(
        self, auth_client: AuthenticatedTestClient, db_session: Session, owner: User
    ) -> None:
        theirs = Attachment(
            owner_id=owner.id,
            media_id=_media(db_session, owner, "theirs.txt", "5" * 64).id,
            filename="theirs.txt",
        )
        db_session.add(theirs)
        db_session.flush()

        assert auth_client.get(f"{API}/attachments/{theirs.id}").status_code == 404

    def test_the_extraction_route_carries_the_unverified_label(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """The warning travels with the *content*, not as a decoration the
        client adds."""
        media = Media(
            owner_id=current_user_id,
            kind=MediaKind.CAD,
            filename="scan.pdf",
            size_bytes=10,
            sha256="6" * 64,
            meta={},
        )
        db_session.add(media)
        db_session.flush()
        attachment = Attachment(
            owner_id=current_user_id,
            media_id=media.id,
            filename="scan.pdf",
            status=ExtractionStatus.READY,
            reliability=Reliability.INFERRED.value,
            extracted={"fragments": [], "notes": [], "unread": [], "fragment_count": 0},
        )
        db_session.add(attachment)
        db_session.flush()

        body = auth_client.get(f"{API}/attachments/{attachment.id}/content").json()

        assert body["unverified_note"]
        assert "Confirm every dimension" in body["unverified_note"]
