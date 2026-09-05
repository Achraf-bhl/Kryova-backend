"""The kernel endpoints — step 2 of the integration gap.

`GET /kernel/conversations/{id}/render` and `.../measure` are the first callers
`app/render/` and the measurement layer have ever had outside a test. These run
against the live test DB (auth + conversation ownership are real), and the
geometry half needs OCCT — the same split as everywhere else.

The refusals matter more than the happy path here: a render endpoint that
answered *something* for a part on a CATIA seat, or for an evicted document,
would be producing a picture of the wrong thing — and a picture is exactly the
artefact people trust without checking.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.geometry import backends
from app.models import Conversation, User


@pytest.fixture(autouse=True)
def _occt_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "geometry_backend", "occt")
    for key in list(backends._sessions):
        backends.forget(key)
    yield
    for key in list(backends._sessions):
        backends.forget(key)


def _conversation(db_session: Session, owner_id: str) -> Conversation:
    row = Conversation(owner_id=owner_id, title="A plate")
    db_session.add(row)
    db_session.flush()
    return row


def _build_plate(conversation_id: str) -> None:
    runner = backends.session_for(conversation_id)
    runner("catia_new_part", {"name": "Plate"})
    runner("catia_sketch_create", {"support": "XY", "name": "profile"})
    runner("catia_sketch_rectangle", {"sketch": "profile", "width_mm": 60.0, "height_mm": 40.0})
    runner("catia_pad", {"name": "slab", "sketch": "profile", "length_mm": 20.0})


class TestOwnership:
    def test_requires_authentication(self, client: Any) -> None:
        assert client.get("/api/v1/kernel/conversations/x/render").status_code == 401

    def test_someone_elses_conversation_is_404_never_403(
        self, auth_client: Any, db_session: Session
    ) -> None:
        other = User(email="other@kryova.dev", hashed_password="x")
        db_session.add(other)
        db_session.flush()
        theirs = _conversation(db_session, other.id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{theirs.id}/render")
        assert response.status_code == 404


class TestTheRefusalsAreDistinct:
    """Three different problems, three different sentences — never one shrug."""

    def test_a_catia_backend_part_is_refused_not_faked(
        self,
        auth_client: Any,
        db_session: Session,
        current_user_id: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A picture is the artefact people trust unchecked, so no picture of the wrong thing."""
        monkeypatch.setattr(settings, "geometry_backend", "catia")
        mine = _conversation(db_session, current_user_id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{mine.id}/render")
        assert response.status_code == 409
        assert "CATIA seat" in response.json()["detail"]

    def test_an_empty_conversation_says_build_something_first(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{mine.id}/render")
        assert response.status_code == 409
        assert "Nothing has been built" in response.json()["detail"]

    def test_an_evicted_document_is_named_not_redrawn_empty(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        backends.forget(mine.id)
        backends._evicted.add(mine.id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{mine.id}/render")
        assert response.status_code == 409
        assert "no longer in memory" in response.json()["detail"]

    def test_an_unknown_view_is_a_400_with_the_list(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = auth_client.get(
            f"/api/v1/kernel/conversations/{mine.id}/render", params={"view": "sideways"}
        )
        assert response.status_code == 400
        assert "iso" in response.json()["detail"]


class TestRendering:
    def test_a_built_part_renders_as_a_png_with_its_digest_as_etag(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{mine.id}/render")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(response.headers["etag"]) > 10
        assert response.headers["x-kryova-blank"] == "0"

    def test_the_same_part_returns_the_same_etag(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        """Deterministic bytes are what let a polling client get real 304s."""
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        url = f"/api/v1/kernel/conversations/{mine.id}/render"
        assert (
            auth_client.get(url).headers["etag"] == auth_client.get(url).headers["etag"]
        )

    def test_a_section_cut_renders_and_a_bad_axis_is_refused(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        url = f"/api/v1/kernel/conversations/{mine.id}/render"
        good = auth_client.get(url, params={"section": "x"})
        assert good.status_code == 200
        bad = auth_client.get(url, params={"section": "w"})
        assert bad.status_code == 400


class TestMeasuring:
    def test_the_plate_measures_to_the_closed_form_volume(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = auth_client.get(f"/api/v1/kernel/conversations/{mine.id}/measure")
        assert response.status_code == 200
        body = response.json()
        assert body["backend"] == "occt"
        assert body["measurements"]["volume_mm3"] == pytest.approx(48_000.0)

    def test_an_unknown_detail_level_is_refused_with_the_choices(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = auth_client.get(
            f"/api/v1/kernel/conversations/{mine.id}/measure", params={"detail": "everything"}
        )
        assert response.status_code == 400
        assert "bounds" in response.json()["detail"]
