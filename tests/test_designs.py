"""The persisted design record: revisions, edits, and the routes (P5.3, P5.6).

Both of those tasks were `PARTIAL` with the same sentence — `DesignSpec` had no
model and no route, so there was nothing to render beside the chat and nothing
to PATCH a parameter into. What is worth testing here is therefore mostly about
the ways a *history* can quietly stop being one: a chain that grows a revision
per no-op save, a summary that changes when the compiler does, a diff that
silently spans two different designs, or two writers both claiming revision 4.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy.orm import Session

from app.core import designs
from app.design.errors import SpecError
from app.design.spec import DesignSpec, FeatureSpec, expr, ref
from app.models import Conversation, DesignDocument, DesignRevision, User
from app.models.base import utcnow
from tests.test_design_compile import bracket
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


@pytest.fixture
def owner(db_session: Session) -> User:
    user = User(email="designer@kryova.dev", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def conversation(db_session: Session, owner: User) -> Conversation:
    row = Conversation(title="Bracket", owner_id=owner.id)
    db_session.add(row)
    db_session.flush()
    return row


class TestSavingADesign:
    def test_the_first_save_creates_the_document_and_revision_one(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        outcome = designs.save(db_session, conversation, bracket())

        assert outcome.changed
        assert outcome.document.revision_number == 1
        assert outcome.revision.revision_number == 1
        assert outcome.document.digest == bracket().digest()
        assert outcome.document.name == "Bracket"

    def test_revision_one_carries_no_summary(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """It changed nothing — it is the beginning. A sentence here would have
        to be invented, and an invented history entry is worse than none."""
        outcome = designs.save(db_session, conversation, bracket())

        assert outcome.revision.summary == ""
        assert outcome.diff is None

    def test_an_identical_save_writes_no_revision(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """The agent re-saves after every step and most steps do not touch the
        design. Appending an identical spec each time gives a design six hundred
        revisions of which four matter, which does not make the history longer,
        it makes it unreadable."""
        designs.save(db_session, conversation, bracket())

        second = designs.save(db_session, conversation, bracket())

        assert not second.changed
        assert second.document.revision_number == 1
        assert (
            db_session.query(DesignRevision)
            .filter(DesignRevision.design_id == second.document.id)
            .count()
            == 1
        )

    def test_a_changed_save_appends_a_revision_and_keeps_the_old_one(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        designs.save(db_session, conversation, bracket())
        thicker = bracket().set_parameter("thick_mm", 12.0)

        second = designs.save(db_session, conversation, thicker)

        assert second.document.revision_number == 2
        assert second.document.digest == thicker.digest()
        kept = designs.revision(db_session, second.document, 1)
        assert kept is not None
        # Against `bracket()` rather than against the earlier `SaveOutcome`: the
        # document is one ORM object, so the first outcome's `.digest` has
        # already moved to the new value by the time this line runs. The
        # question is whether *revision 1* still holds what was there.
        assert kept.digest == bracket().digest()
        assert designs.spec_of_revision(kept).digest() == bracket().digest()

    def test_the_summary_is_written_at_the_time_not_derived_on_read(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Stored, so a history cannot rewrite itself when the compiler changes.
        The diff is made *after* compilation on purpose, so recomputing it later
        against a changed operation registry gives a different answer about what
        happened in the past."""
        designs.save(db_session, conversation, bracket())

        outcome = designs.save(db_session, conversation, bracket().set_parameter("thick_mm", 12.0))

        assert "thick_mm" in outcome.revision.summary
        assert "8" in outcome.revision.summary and "12" in outcome.revision.summary

    def test_a_supplied_summary_is_preferred_to_the_diffs(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """The agent knows what it just did better than a diff does. "Thickened
        the web to clear the bolt head" is the useful sentence; "thick_mm: 8 ->
        12" is the one that can be reconstructed from the two revisions anyway."""
        designs.save(db_session, conversation, bracket())

        outcome = designs.save(
            db_session,
            conversation,
            bracket().set_parameter("thick_mm", 12.0),
            summary="Thickened the plate to clear the bolt head.",
        )

        assert outcome.revision.summary == "Thickened the plate to clear the bolt head."

    def test_a_spec_that_does_not_compile_is_refused_before_anything_is_written(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Rule 3. A half-written revision chain is worse than a refusal,
        because the design is then in a state no build produced."""
        designs.save(db_session, conversation, bracket())
        broken = bracket().with_features(
            [
                *bracket().features,
                FeatureSpec("plate.nowhere", "catia_pad", {"sketch": ref("does.not.exist")}),
            ]
        )

        with pytest.raises(SpecError):
            designs.save(db_session, conversation, broken)

        document = designs.load(db_session, conversation)
        assert document is not None
        assert document.revision_number == 1
        assert document.digest == bracket().digest()

    def test_the_project_follows_the_conversation(
        self, db_session: Session, conversation: Conversation, project_id: str
    ) -> None:
        """A conversation can acquire a project after its design was started,
        and a denormalised column is only useful if it follows."""
        designs.save(db_session, conversation, bracket())
        conversation.project_id = project_id
        db_session.flush()

        outcome = designs.save(db_session, conversation, bracket().set_parameter("thick_mm", 10.0))

        assert outcome.document.project_id == project_id

    def test_a_design_belongs_to_one_conversation(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        """The pairing CATIA already enforces. A design reachable from two
        conversations makes "the part we are talking about" ambiguous in the one
        place the agent resolves it without asking."""
        other = Conversation(title="Other", owner_id=owner.id)
        db_session.add(other)
        db_session.flush()
        designs.save(db_session, conversation, bracket())

        designs.save(db_session, other, bracket())

        rows = db_session.query(DesignDocument).count()
        assert rows == 2


class TestEditingOneParameter:
    def test_an_edit_is_a_revision_naming_both_sides(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        document = designs.save(db_session, conversation, bracket()).document

        outcome = designs.set_parameter(db_session, document, "thick_mm", 12.0)

        assert outcome.changed
        assert outcome.document.revision_number == 2
        assert outcome.revision.summary == "thick_mm: 8 -> 12"

    def test_the_edit_reports_what_it_reaches_including_what_nobody_touched(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """`thick_mm` feeds the pad's length directly and the fillet's radius
        through a formula. The fillet is the one a reviewer misses."""
        document = designs.save(db_session, conversation, bracket()).document

        outcome = designs.set_parameter(db_session, document, "thick_mm", 12.0)

        assert outcome.diff is not None
        assert "plate.body" in outcome.diff.affected
        assert "plate.edges" in outcome.diff.affected

    def test_a_derived_parameter_is_refused_with_the_formula_named(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        """Overwriting a consequence with a literal leaves a formula in the spec
        that no longer describes the value, which is worse than either
        alternative. `DesignSpec` says so; this asserts nothing here softens it."""
        document = designs.save(db_session, conversation, bracket()).document

        with pytest.raises(SpecError) as refused:
            designs.set_parameter(db_session, document, "fillet_mm", 3.0)

        assert "thick_mm / 2" in str(refused.value)

    def test_an_unknown_parameter_is_refused_with_the_known_ones_listed(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        document = designs.save(db_session, conversation, bracket()).document

        with pytest.raises(SpecError) as refused:
            designs.set_parameter(db_session, document, "not_a_parameter", 3.0)

        assert "width_mm" in str(refused.value)

    def test_setting_a_parameter_to_what_it_already_is_writes_nothing(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        document = designs.save(db_session, conversation, bracket()).document

        outcome = designs.set_parameter(db_session, document, "thick_mm", 8.0)

        assert not outcome.changed
        assert outcome.document.revision_number == 1


class TestDiffingTwoRevisions:
    def test_two_revisions_of_one_design_diff(
        self, db_session: Session, conversation: Conversation
    ) -> None:
        document = designs.save(db_session, conversation, bracket()).document
        designs.set_parameter(db_session, document, "thick_mm", 12.0)
        one = designs.revision(db_session, document, 1)
        two = designs.revision(db_session, document, 2)
        assert one is not None and two is not None

        diff = designs.diff_between(document, one, two)

        assert diff.plan_changed
        assert [change.name for change in diff.parameters] == ["thick_mm"]

    def test_a_revision_from_another_design_is_refused(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        """A diff across two designs has a shape the type permits and the
        meaning does not — every feature added and every feature removed — and
        it would look like an answer."""
        mine = designs.save(db_session, conversation, bracket()).document
        other_conversation = Conversation(title="Other", owner_id=owner.id)
        db_session.add(other_conversation)
        db_session.flush()
        theirs = designs.save(db_session, other_conversation, bracket()).document
        mine_one = designs.revision(db_session, mine, 1)
        theirs_one = designs.revision(db_session, theirs, 1)
        assert mine_one is not None and theirs_one is not None

        with pytest.raises(ValueError):
            designs.diff_between(mine, mine_one, theirs_one)


def _record(db: Session, user_id: str, spec: DesignSpec, title: str = "Bracket") -> str:
    """Create a conversation and store a design in it, through the service.

    The API has no write for a whole spec on purpose, so this reaches past it —
    which is exactly the asymmetry `test_the_api_has_no_way_to_post_a_whole_spec`
    asserts is deliberate.
    """
    conversation = Conversation(title=title, owner_id=user_id)
    db.add(conversation)
    db.flush()
    designs.save(db, conversation, spec)
    db.flush()
    return conversation.id


class TestTheRoutes:
    def test_a_design_reads_back_with_its_spec_inline(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        response = auth_client.get(f"{API}/designs/{conversation_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Bracket"
        assert body["revision_number"] == 1
        assert body["document"]["format_version"] == 1
        assert [f["name"] for f in body["document"]["features"]][0] == "plate.profile"

    def test_a_conversation_with_no_design_says_so_rather_than_erroring(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation = Conversation(title="Empty", owner_id=current_user_id)
        db_session.add(conversation)
        db_session.flush()

        response = auth_client.get(f"{API}/designs/{conversation.id}")

        assert response.status_code == 404
        assert "no design yet" in response.json()["detail"]

    def test_another_users_design_is_404_not_403(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        """The rule the whole API keeps: never confirm that an id exists."""
        stranger = User(email="stranger@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(stranger)
        db_session.flush()
        theirs = Conversation(title="Theirs", owner_id=stranger.id)
        db_session.add(theirs)
        db_session.flush()
        designs.save(db_session, theirs, bracket())
        db_session.commit()

        response = auth_client.get(f"{API}/designs/{theirs.id}")

        assert response.status_code == 404
        assert response.json()["detail"] == "Conversation not found"

    def test_patching_a_parameter_returns_the_new_head_and_what_it_reaches(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        response = auth_client.patch(
            f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is True
        assert body["design"]["revision_number"] == 2
        assert "plate.edges" in body["diff"]["affected"]

    def test_patching_a_derived_parameter_is_422_carrying_the_compilers_sentence(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        response = auth_client.patch(
            f"{API}/designs/{conversation_id}/parameters/fillet_mm", json={"value": 3.0}
        )

        assert response.status_code == 422
        assert "thick_mm / 2" in response.json()["detail"]

    def test_a_user_edit_records_who_made_it(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """Null `author_id` means the agent, not "unknown". Conflating the two
        makes "did a person do this" unanswerable, which is the question an
        audit of a signed-off design is entirely about."""
        conversation_id = _record(db_session, current_user_id, bracket())

        auth_client.patch(
            f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0}
        )

        head = db_session.query(DesignRevision).filter(DesignRevision.revision_number == 2).one()
        assert head.author == designs.AUTHOR_USER
        assert head.author_id == current_user_id

    def test_the_revision_list_is_newest_first_and_paginates(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())
        for value in (10.0, 12.0, 14.0):
            auth_client.patch(
                f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": value}
            )

        response = auth_client.get(f"{API}/designs/{conversation_id}/revisions?page_size=2")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 4
        assert [row["revision_number"] for row in body["items"]] == [4, 3]

    def test_the_diff_route_compares_any_two_revisions(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())
        auth_client.patch(
            f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0}
        )

        response = auth_client.get(f"{API}/designs/{conversation_id}/diff?from=1&to=2")

        assert response.status_code == 200
        body = response.json()
        assert body["diff"]["plan_changed"] is True
        assert body["diff"]["parameters"][0]["name"] == "thick_mm"

    def test_a_revision_that_does_not_exist_is_404_saying_how_many_there_are(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        response = auth_client.get(f"{API}/designs/{conversation_id}/diff?from=1&to=9")

        assert response.status_code == 404
        assert "1 revision" in response.json()["detail"]

    def test_the_api_has_no_way_to_post_a_whole_spec(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        """A route taking a whole spec would let a client author a design the
        server never compiled, and the first malformed one would arrive as a
        compile error against a revision already written. Asserted rather than
        left as a convention, because the convention is one PR from being lost.
        """
        from tests.routes import leaf_routes, methods_for, methods_under

        # Asserted first, because `methods_under` returning nothing is the
        # *passing* answer and would be indistinguishable from a route walk
        # that had stopped working.
        assert methods_for("/designs/{conversation_id}") == {"GET"}

        assert "POST" not in methods_under("/designs")
        # One PUT exists, and it carries a date, not a spec (E19 task 5). Pinned
        # by path *and* by body, so a second PUT — or a spec slipped into this
        # one — fails here rather than being waved through with it.
        puts = [
            route
            for route in leaf_routes()
            if route.path.startswith("/designs") and "PUT" in route.methods
        ]
        assert [route.path for route in puts] == ["/designs/{conversation_id}/placed-on-market"]
        (put,) = puts
        assert put.body_field is not None
        assert set(put.body_field.field_info.annotation.model_fields) == {"placed_on_market_on"}

    def test_the_design_list_paginates_and_omits_the_documents(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """A page of twenty specs is a page of twenty feature trees. A list view
        needs a name and a revision number."""
        _record(db_session, current_user_id, bracket())
        _record(db_session, current_user_id, bracket(name="Second"), title="Second")

        response = auth_client.get(f"{API}/designs")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert "document" not in body["items"][0]


class TestPlacingOnTheMarket:
    """E19 task 5, the product half: a change after the recorded placing on the
    market must read differently from a design-time one, on both surfaces that
    can make it. The legal wording is pinned in `test_compliance_modification.py`."""

    def _today(self) -> datetime.date:
        return utcnow().date()

    def test_a_design_with_nothing_recorded_reads_as_design_time(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        body = auth_client.get(f"{API}/designs/{conversation_id}").json()

        assert body["placed_on_market_on"] is None
        assert body["modification"]["character"] == "design-time"
        assert body["modification"]["citations"] == []

    def test_recording_a_past_date_makes_every_read_say_it_is_not_design_time(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())
        placed = self._today() - datetime.timedelta(days=30)

        response = auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": placed.isoformat()},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["placed_on_market_on"] == placed.isoformat()
        assert body["modification"]["character"] == "after-placing-on-market"
        assert "Article 3(16)" in body["modification"]["citations"]
        document = db_session.query(DesignDocument).filter_by(conversation_id=conversation_id).one()
        assert document.placed_on_market_recorded_by_id == current_user_id

    def test_recording_the_date_writes_no_revision(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """The machine did not change. A revision per typed date would put
        something that changed nothing about the part into its history."""
        conversation_id = _record(db_session, current_user_id, bracket())

        body = auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": self._today().isoformat()},
        ).json()

        assert body["revision_number"] == 1

    def test_a_future_date_is_refused_and_nothing_is_recorded(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())

        response = auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": (self._today() + datetime.timedelta(days=1)).isoformat()},
        )

        assert response.status_code == 422
        assert "has not happened yet" in response.json()["detail"]
        assert auth_client.get(f"{API}/designs/{conversation_id}").json()["placed_on_market_on"] is None

    def test_an_edit_after_placing_comes_back_saying_so(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())
        auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": self._today().isoformat()},
        )

        body = auth_client.patch(
            f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0}
        ).json()

        assert body["changed"] is True
        assert body["design"]["modification"]["character"] == "after-placing-on-market"
        assert "substantial modification" in body["design"]["modification"]["detail"]

    def test_the_history_marks_which_revisions_came_after_placing(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        conversation_id = _record(db_session, current_user_id, bracket())
        first = db_session.query(DesignRevision).filter(DesignRevision.revision_number == 1).one()
        first.created_at = utcnow() - datetime.timedelta(days=60)
        db_session.flush()
        auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": (self._today() - datetime.timedelta(days=10)).isoformat()},
        )
        auth_client.patch(f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0})

        items = auth_client.get(f"{API}/designs/{conversation_id}/revisions").json()["items"]

        assert [(row["revision_number"], row["after_placing_on_market"]) for row in items] == [
            (2, True),
            (1, False),
        ]

    def test_another_users_design_cannot_have_a_date_recorded(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        stranger = User(email="placer@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(stranger)
        db_session.flush()
        conversation_id = _record(db_session, stranger.id, bracket(), title="Theirs")
        db_session.commit()

        response = auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": self._today().isoformat()},
        )

        assert response.status_code == 404

    def test_the_agents_edit_after_placing_carries_the_notice(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        toolbox = _toolbox(db_session, owner, conversation)
        outcome = designs.save(db_session, conversation, bracket())
        designs.record_placed_on_market(
            db_session, outcome.document, self._today(), recorded_by_id=owner.id, today=self._today()
        )

        result = toolbox._set_design_parameter("thick_mm", 12.0)

        assert result["legal_character"] == "after-placing-on-market"
        assert "substantial modification" in result["legal_notice"]
        assert toolbox._read_design()["placed_on_market_on"] == self._today().isoformat()

    def test_the_agents_design_time_edit_carries_no_notice(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        toolbox = _toolbox(db_session, owner, conversation)
        designs.save(db_session, conversation, bracket())

        result = toolbox._set_design_parameter("thick_mm", 12.0)

        assert result["legal_character"] == "design-time"
        assert "legal_notice" not in result


class TestTheTechnicalFile:
    """E19 task 3, the export route. The structure and the hashing are pinned
    offline in `test_compliance_technical_file.py`; these pin what reaches the
    file from the record, and who may see which part of it."""

    def test_it_downloads_a_file_that_verifies_and_is_organised_by_annex_iv(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        from app.compliance import technical_file as tf
        from app.verify.standards import NOT_VALIDATED

        conversation_id = _record(db_session, current_user_id, bracket())
        auth_client.patch(f"{API}/designs/{conversation_id}/parameters/thick_mm", json={"value": 12.0})

        response = auth_client.get(f"{API}/designs/{conversation_id}/technical-file")

        assert response.status_code == 200
        assert response.headers["content-disposition"] == (
            'attachment; filename="Bracket-r2-technical-file.json"'
        )
        body = response.json()
        assert tf.verify(body)
        assert [e["point"] for e in body["elements"]] == list("abcdefghijklmno")
        assert body["statements"]["validation"] == NOT_VALIDATED
        by_id = {a["id"]: a["content"] for a in body["artefacts"]}
        assert {p["name"]: p.get("value") for p in by_id[tf.SPECIFICATION]["parameters"]}["thick_mm"] == 12.0
        assert by_id[tf.BUILD_PLAN]["compiled"] is True
        assert by_id[tf.BUILD_PLAN]["spec_digest"] == body["design"]["digest"]
        assert [(r["revision"], r["author"]) for r in by_id[tf.REVISIONS]] == [(1, "agent"), (2, "user")]

    def test_the_operations_that_built_it_are_in_the_file_failures_included(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        from app.compliance import technical_file as tf
        from app.models import CatiaOperation

        conversation_id = _record(db_session, current_user_id, bracket())
        for ok, error in ((True, None), (False, "Sketch is not closed.")):
            db_session.add(
                CatiaOperation(
                    conversation_id=conversation_id,
                    user_id=current_user_id,
                    tool="catia_pad",
                    tier="kernel",
                    arguments={"length_mm": 8},
                    ok=ok,
                    error=error,
                )
            )
        db_session.flush()

        body = auth_client.get(f"{API}/designs/{conversation_id}/technical-file").json()

        (operations,) = [a["content"] for a in body["artefacts"] if a["id"] == tf.OPERATIONS]
        assert [(o["ok"], o["error"]) for o in operations] == [(True, None), (False, "Sketch is not closed.")]

    def test_the_projects_analyses_are_included_and_say_the_link_is_not_recorded(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
        project_id: str,
    ) -> None:
        from app.compliance import technical_file as tf

        conversation_id = _record(db_session, current_user_id, bracket())
        conversation = db_session.get(Conversation, conversation_id)
        assert conversation is not None
        conversation.project_id = project_id
        designs.save(db_session, conversation, bracket().set_parameter("thick_mm", 9.0))
        _simulation(db_session, current_user_id, project_id)

        body = auth_client.get(f"{API}/designs/{conversation_id}/technical-file").json()

        (analyses,) = [a for a in body["artefacts"] if a["id"] == tf.ANALYSES]
        assert "not recorded" in analyses["description"]
        (run,) = analyses["content"]["runs"]
        assert run["solver"] == "calculix"
        assert run["geometry"] == {"filename": "frame.stl", "version": 1, "sha256": "0" * 64}

    def test_a_project_the_exporter_cannot_read_contributes_no_analyses(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        """The conversation is the owner's; a project they have lost access to
        is not, and exporting a design must not become a way back into it."""
        from app.compliance import technical_file as tf
        from app.models import Project

        stranger = User(email="projectless@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(stranger)
        db_session.flush()
        theirs = Project(name="Secret press", owner_id=stranger.id)
        db_session.add(theirs)
        db_session.flush()
        _simulation(db_session, stranger.id, theirs.id)
        conversation = Conversation(title="Mine", owner_id=current_user_id, project_id=theirs.id)
        db_session.add(conversation)
        db_session.flush()
        designs.save(db_session, conversation, bracket())
        db_session.flush()

        body = auth_client.get(f"{API}/designs/{conversation.id}/technical-file").json()

        (analyses,) = [a for a in body["artefacts"] if a["id"] == tf.ANALYSES]
        assert analyses["content"]["runs"] == []
        assert "not readable" in analyses["description"]

    def test_a_placed_machine_s_file_carries_the_notice_and_marks_later_revisions(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        from app.compliance import technical_file as tf

        conversation_id = _record(db_session, current_user_id, bracket())
        auth_client.put(
            f"{API}/designs/{conversation_id}/placed-on-market",
            json={"placed_on_market_on": utcnow().date().isoformat()},
        )

        body = auth_client.get(f"{API}/designs/{conversation_id}/technical-file").json()

        assert body["design"]["modification"]["character"] == "after-placing-on-market"
        (revisions,) = [a["content"] for a in body["artefacts"] if a["id"] == tf.REVISIONS]
        assert revisions[0]["after_placing_on_market"] is True

    def test_another_users_technical_file_is_404(
        self, auth_client: AuthenticatedTestClient, db_session: Session
    ) -> None:
        stranger = User(email="exporter@kryova.dev", hashed_password="x", is_active=True)
        db_session.add(stranger)
        db_session.flush()
        conversation_id = _record(db_session, stranger.id, bracket(), title="Theirs")
        db_session.commit()

        response = auth_client.get(f"{API}/designs/{conversation_id}/technical-file")

        assert response.status_code == 404


def _simulation(db: Session, owner_id: str, project_id: str) -> None:
    from app.models import GeometryVersion, JobStatus, Media, SimulationJob
    from app.models.media import MediaKind

    media = Media(
        owner_id=owner_id, kind=MediaKind.CAD, filename="frame.stl", size_bytes=1024, sha256="0" * 64, meta={}
    )
    db.add(media)
    db.flush()
    geometry = GeometryVersion(
        project_id=project_id, media_id=media.id, version_number=1, filename="frame.stl", file_format="stl", stats={}
    )
    db.add(geometry)
    db.flush()
    db.add(
        SimulationJob(
            project_id=project_id,
            geometry_version_id=geometry.id,
            status=JobStatus.SUCCEEDED,
            solver="calculix",
            solver_version="2.20",
            load_case={},
            result={"max_von_mises_mpa": 41.0},
        )
    )
    db.flush()


class TestTheAgentTools:
    def test_recording_a_design_stores_it_and_reports_the_digest(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        toolbox = _toolbox(db_session, owner, conversation)

        result = toolbox._record_design(
            name="Plate",
            parameters=[{"name": "thick_mm", "unit": "mm", "value": 8.0}],
            features=[
                {"name": "plate.profile", "op": "catia_sketch_create", "args": {"support": "XY"}},
                {
                    "name": "plate.body",
                    "op": "catia_pad",
                    "args": {"sketch": "@plate.profile", "length_mm": "=thick_mm"},
                    "note": "Extrude the footprint.",
                },
            ],
        )

        assert result["changed"] is True
        assert result["revision"] == 1
        assert result["features"] == ["plate.profile", "plate.body"]
        stored = designs.load(db_session, conversation)
        assert stored is not None and stored.digest == result["digest"]

    def test_re_recording_the_same_design_says_nothing_changed(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        """A model told "saved" after a no-op has no way to notice the change it
        thought it made did not happen, and will report it to the user as done."""
        toolbox = _toolbox(db_session, owner, conversation)
        payload = dict(
            name="Plate",
            parameters=[{"name": "thick_mm", "unit": "mm", "value": 8.0}],
            features=[
                {"name": "plate.profile", "op": "catia_sketch_create", "args": {"support": "XY"}}
            ],
        )
        toolbox._record_design(**payload)  # type: ignore[arg-type]

        result = toolbox._record_design(**payload)  # type: ignore[arg-type]

        assert result["changed"] is False
        assert "nothing changed" in result["note"]

    def test_a_design_that_does_not_compile_comes_back_as_a_tool_error(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        from app.ai.tools import ToolError

        toolbox = _toolbox(db_session, owner, conversation)
        toolbox._record_design(
            name="Plate",
            features=[
                {"name": "plate.profile", "op": "catia_sketch_create", "args": {"support": "XY"}}
            ],
        )

        with pytest.raises(ToolError):
            toolbox._record_design(
                name="Plate",
                features=[
                    {"name": "plate.body", "op": "catia_pad", "args": {"sketch": "@nothing.here"}}
                ],
            )

    def test_reading_a_design_before_one_exists_says_so_without_raising(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        toolbox = _toolbox(db_session, owner, conversation)

        result = toolbox._read_design()

        assert result["design"] is None
        assert "record_design" in result["note"]

    def test_setting_a_parameter_before_a_design_exists_says_what_to_do(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        from app.ai.tools import ToolError

        toolbox = _toolbox(db_session, owner, conversation)

        with pytest.raises(ToolError) as refused:
            toolbox._set_design_parameter("thick_mm", 12.0)

        assert "record_design first" in str(refused.value)

    def test_the_agents_edit_reports_the_downstream_it_did_not_touch(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        toolbox = _toolbox(db_session, owner, conversation)
        designs.save(db_session, conversation, bracket())

        result = toolbox._set_design_parameter("thick_mm", 12.0)

        assert "plate.edges" in result["affected"]
        assert result["revision"] == 2

    def test_a_parameter_no_feature_reads_says_the_part_did_not_move(
        self, db_session: Session, conversation: Conversation, owner: User
    ) -> None:
        """The useful answer, not an empty one. A model that changed a number
        and was told "done" would report a change the part does not have."""
        toolbox = _toolbox(db_session, owner, conversation)
        spec = DesignSpec.of(
            "Plate",
            parameters=[
                _parameter("thick_mm", 8.0),
                _parameter("unused_mm", 3.0),
            ],
            features=[
                FeatureSpec("plate.profile", "catia_sketch_create", {"support": "XY"}),
                FeatureSpec(
                    "plate.body",
                    "catia_pad",
                    {"sketch": ref("plate.profile"), "length_mm": expr("thick_mm")},
                ),
            ],
        )
        designs.save(db_session, conversation, spec)

        result = toolbox._set_design_parameter("unused_mm", 5.0)

        assert "no feature reads this parameter" in result["note"]

    def test_the_three_tools_all_carry_step_labels(self) -> None:
        """An unlabelled tool renders as its function name in the step list, and
        a user reading `set_design_parameter` has to decode it."""
        from app.ai.tools import BUILTIN_TOOL_LABELS

        for name in ("record_design", "read_design", "set_design_parameter"):
            assert name in BUILTIN_TOOL_LABELS


def _parameter(name: str, value: float):
    from app.design.params import Parameter, Unit

    return Parameter(name, Unit.MM, value=value)


def _toolbox(db: Session, user: User, conversation: Conversation):
    from app.ai.tools import ToolBox

    return ToolBox(db=db, user=user, conversation=conversation)
