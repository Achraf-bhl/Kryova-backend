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

    def test_the_render_headers_are_readable_by_the_browser_that_asks(self) -> None:
        """A header a cross-origin client cannot read is a header that does not exist.

        `X-Kryova-Blank` is the renderer saying it drew an empty frame, which is a
        valid PNG indistinguishable from a successful render of a part that falls
        outside the view. The frontend is a different origin, so without these
        names in `expose_headers` the only thing left to the one caller that wants
        the answer is guessing from the compressed byte count — which is what it
        was doing until this list grew. `ETag` for the same reason: it is the
        render's own digest and the whole basis of the 304.
        """
        from fastapi.middleware.cors import CORSMiddleware

        from app.main import app

        cors = next(
            one for one in app.user_middleware if one.cls is CORSMiddleware
        )
        exposed = set(cors.kwargs["expose_headers"])

        assert {"ETag", "X-Kryova-View", "X-Kryova-Blank"} <= exposed


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


# -- checking a specification against the part on the screen ------------------


PLATE_REQUIREMENTS = """
# The plate `_build_plate` makes: 60 x 40 x 20, so 48 000 mm^3.

REQ-001: The plate shall enclose no more than 50 000 cubic millimetres.
    measure: volume_mm3
    target: <= 50000
    source: customer
    rationale: It has to fit the pocket in the fixture.

REQ-002: The plate shall present no more than 10 000 square millimetres to be finished.
    measure: surface_area_mm2
    target: <= 10000
    source: derived
    parent: REQ-001
"""


class TestCheckingRequirementsAgainstTheLivePart:
    """Master plan 11.2/11.4 reaching the product — the third step of the same gap.

    Until this endpoint existed, `app/requirements/` could compile a requirement
    into an assertion, verify a set against a payload and report coverage, and
    **nothing outside a test had ever handed it one**. An engineer could not give
    the product a specification and be told whether the part met it.
    """

    def _check(self, auth_client: Any, conversation_id: str, **body: Any) -> Any:
        payload = {"document": PLATE_REQUIREMENTS, "name": "plate"}
        payload.update(body)
        return auth_client.post(
            f"/api/v1/kernel/conversations/{conversation_id}/requirements", json=payload
        )

    def test_a_met_specification_comes_back_met_with_its_evidence(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = self._check(auth_client, mine.id)
        assert response.status_code == 200
        body = response.json()
        report = body["report"]
        assert report["ok"] is True
        assert report["coverage"]["verified"] == 2
        by_id = {one["id"]: one for one in report["results"]}
        assert by_id["REQ-001"]["measured"] == pytest.approx(48_000.0)
        assert by_id["REQ-001"]["evidence"]["basis"] == "measured"

    def test_a_violated_requirement_names_the_gap_and_is_not_ok(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = self._check(
            auth_client,
            mine.id,
            document=PLATE_REQUIREMENTS.replace("<= 50000", "<= 40000"),
        )
        body = response.json()
        assert body["report"]["ok"] is False
        failed = next(
            one for one in body["report"]["results"] if one["id"] == "REQ-001"
        )
        assert failed["outcome"] == "failed"
        assert failed["gap"] == pytest.approx(8_000.0)
        assert "REQ-001 NOT MET" in body["summary"]

    def test_the_report_is_bound_to_what_produced_it(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        """Decision 3: a result nobody can trace back to a build is not evidence."""
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        body = self._check(auth_client, mine.id).json()
        bound = body["report"]["bound_to"]
        assert bound["backend"] == "occt"
        assert bound["conversation"] == mine.id
        assert body["report"]["contract_version"]

    def test_a_requirement_nothing_measured_is_reported_never_dropped(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        document = PLATE_REQUIREMENTS + (
            "\nREQ-003: The plate shall survive ten years.\n"
            "    needs: no fatigue solver is federated yet\n"
            "    source: customer\n"
        )
        body = self._check(auth_client, mine.id, document=document).json()
        assert body["report"]["ok"] is False
        waiting = next(
            one for one in body["report"]["results"] if one["id"] == "REQ-003"
        )
        assert waiting["outcome"] == "unmeasured"
        assert "fatigue solver" in waiting["reason"]
        assert body["report"]["coverage"]["unverified"] == 1

    def test_a_document_that_does_not_parse_lists_every_problem_at_once(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        response = self._check(
            auth_client,
            mine.id,
            document="REQ-001: The plate shall be light.\n    measur: mass_kg\n",
        )
        assert response.status_code == 422
        assert "measur" in response.json()["detail"]

    def test_a_conversation_with_no_part_is_the_same_409_as_the_others(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        mine = _conversation(db_session, current_user_id)
        response = self._check(auth_client, mine.id)
        assert response.status_code == 409
        assert "Ask for a part first" in response.json()["detail"]

    def test_someone_elses_conversation_is_404_never_403(
        self, auth_client: Any, db_session: Session
    ) -> None:
        other = User(email="req-other@kryova.dev", hashed_password="x")
        db_session.add(other)
        db_session.flush()
        theirs = _conversation(db_session, other.id)
        assert self._check(auth_client, theirs.id).status_code == 404

    def test_it_names_the_scan_a_wall_requirement_would_need(
        self, auth_client: Any, db_session: Session, current_user_id: str
    ) -> None:
        """A payload does not carry wall thickness until somebody asks for it, so
        the honest answer is UNMEASURED plus what to run — not a pass."""
        mine = _conversation(db_session, current_user_id)
        _build_plate(mine.id)
        document = (
            "REQ-010: No wall thinner than 6 mm.\n"
            "    measure: minimum_wall_mm\n"
            "    target: >= 6\n"
            "    source: customer\n"
        )
        body = self._check(auth_client, mine.id, document=document).json()
        assert body["report"]["results"][0]["outcome"] == "unmeasured"
        assert body["scans_needed"]
