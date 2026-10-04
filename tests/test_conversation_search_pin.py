"""Finding a conversation again, and keeping the important ones on top (ROAD_TO_10 2.6).

A sidebar of forty conversations needs two things and each has a way to be quietly wrong:

* **Search** is over the titles and the *user's own words*. A search for "M6" that also reads
  the model's answers and every tool result returns the conversation whose log mentions a
  bolt, not the one where the user asked for one; one that reads the server's own notes
  matches "pressed Continue" in every conversation that was ever continued. Another user's
  conversation must never appear, and the text is plain text: `%` and `_` are not wildcards.
* **Pinning** is about order, and pinning something is not working on it: `updated_at` means
  "last worked on", so a pin that bumped it would put a tidied-up conversation at the top of
  the list as if it had just been used.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.ai import prompts
from app.core.security import hash_password
from app.models import Conversation, ConversationMessage, MessageRole, User
from app.models.base import utcnow
from tests.typing import AuthenticatedTestClient


@pytest.fixture
def account(db_session: Session, auth_client: AuthenticatedTestClient) -> User:
    user = db_session.get(User, auth_client.get("/api/v1/auth/me").json()["id"])
    assert user is not None
    return user


def _conversation(
    db: Session, owner: User, title: str, *, said: str | None = None, age_minutes: int = 0
) -> Conversation:
    row = Conversation(owner_id=owner.id, title=title)
    row.updated_at = utcnow() - timedelta(minutes=age_minutes)
    db.add(row)
    db.flush()
    if said is not None:
        _say(db, row, 0, MessageRole.USER, said)
    return row


def _say(db: Session, row: Conversation, sequence: int, role: MessageRole, text: str) -> None:
    db.add(ConversationMessage(conversation_id=row.id, sequence=sequence, role=role, content=text))
    db.flush()


def _ids(client: AuthenticatedTestClient, **params: Any) -> list[str]:
    response = client.get("/api/v1/ai/conversations", params=params)
    assert response.status_code == 200, response.text
    return [item["conversation_id"] for item in response.json()["items"]]


def _page(client: AuthenticatedTestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/v1/ai/conversations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestSearchReadsTheUsersWords:
    def test_a_title_matches_whatever_its_case(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        wanted = _conversation(db_session, account, "Motor Mount Mass")
        _conversation(db_session, account, "Bracket fillet")

        page = _page(auth_client, q="motor mount")

        assert [item["conversation_id"] for item in page["items"]] == [wanted.id]
        assert page["items"][0]["match"] == "title"

    def test_something_the_user_said_matches_and_says_it_was_a_message(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        wanted = _conversation(db_session, account, "Untitled", said="Use M6 bolts on the flange")
        _conversation(db_session, account, "Other", said="A plain plate")

        page = _page(auth_client, q="m6 bolts")

        assert [item["conversation_id"] for item in page["items"]] == [wanted.id]
        assert page["items"][0]["match"] == "message"

    def test_an_answer_or_a_tool_result_is_not_the_users_words(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _conversation(db_session, account, "Untitled", said="Build a bracket")
        _say(db_session, row, 1, MessageRole.ASSISTANT, "I used M6 bolts for the flange")
        _say(db_session, row, 2, MessageRole.TOOL, '{"fastener": "M6"}')

        assert _ids(auth_client, q="M6") == []

    def test_the_servers_own_notes_are_not_searched(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        # Every continued conversation would otherwise match "pressed Continue".
        row = _conversation(db_session, account, "Untitled", said="Build a bracket")
        _say(
            db_session, row, 1, MessageRole.USER,
            prompts.CONTINUATION_NOTE + "The user pressed Continue and typed nothing.",
        )
        _say(db_session, row, 2, MessageRole.USER, prompts.CONTROL_NOTE + "Stop reading in circles.")

        assert _ids(auth_client, q="pressed Continue") == []
        assert _ids(auth_client, q="reading in circles") == []

    def test_another_users_conversation_never_appears(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        stranger = User(email="other@kryova.dev", hashed_password=hash_password("a-long-enough-pw"))
        db_session.add(stranger)
        db_session.flush()
        _conversation(db_session, stranger, "Secret gearbox", said="gearbox ratio 1:5")
        mine = _conversation(db_session, account, "My gearbox")

        page = _page(auth_client, q="gearbox")

        assert [item["conversation_id"] for item in page["items"]] == [mine.id]
        assert page["total"] == 1

    def test_the_text_is_plain_text_not_a_pattern(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        literal = _conversation(db_session, account, "50% infill study")
        _conversation(db_session, account, "500 mm plate")
        _conversation(db_session, account, "a_b bracket")
        _conversation(db_session, account, "axb bracket")

        assert _ids(auth_client, q="0% in") == [literal.id]
        assert len(_ids(auth_client, q="a_b")) == 1

    def test_the_text_of_a_message_is_plain_text_too(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        literal = _conversation(db_session, account, "A", said="the load is 50% of the maximum")
        _conversation(db_session, account, "B", said="the load is 500 of the maximum")

        assert _ids(auth_client, q="0% of") == [literal.id]

    def test_a_search_with_one_character_is_refused(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        assert auth_client.get("/api/v1/ai/conversations", params={"q": "a"}).status_code == 422

    def test_the_total_and_the_paging_follow_the_search(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        for index in range(5):
            _conversation(db_session, account, f"Flange {index}", age_minutes=index)
        _conversation(db_session, account, "Something else")

        page = _page(auth_client, q="flange", page=2, page_size=2)

        assert page["total"] == 5 and len(page["items"]) == 2

    def test_without_a_query_nothing_says_it_matched(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        _conversation(db_session, account, "Anything")
        assert _page(auth_client)["items"][0]["match"] is None


class TestPinning:
    def test_a_pinned_conversation_sorts_first_however_old_it_is(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        recent = _conversation(db_session, account, "Recent")
        old = _conversation(db_session, account, "Old", age_minutes=600)
        assert _ids(auth_client) == [recent.id, old.id]

        response = auth_client.patch(
            f"/api/v1/ai/conversations/{old.id}", json={"pinned": True}
        )

        assert response.status_code == 200, response.text
        assert response.json()["pinned"] is True
        assert _ids(auth_client) == [old.id, recent.id]
        assert _page(auth_client)["items"][0]["pinned"] is True

    def test_unpinning_puts_it_back_in_activity_order(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        recent = _conversation(db_session, account, "Recent")
        old = _conversation(db_session, account, "Old", age_minutes=600)
        auth_client.patch(f"/api/v1/ai/conversations/{old.id}", json={"pinned": True})

        auth_client.patch(f"/api/v1/ai/conversations/{old.id}", json={"pinned": False})

        assert _ids(auth_client) == [recent.id, old.id]
        assert _page(auth_client)["items"][1]["pinned"] is False

    def test_pinning_does_not_count_as_working_on_it(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        old = _conversation(db_session, account, "Old", age_minutes=600)
        before = auth_client.get(f"/api/v1/ai/conversations/{old.id}").json()["updated_at"]

        auth_client.patch(f"/api/v1/ai/conversations/{old.id}", json={"pinned": True})
        after = auth_client.get(f"/api/v1/ai/conversations/{old.id}").json()["updated_at"]

        assert after == before

    def test_the_newest_pin_is_on_top_and_pinning_again_changes_nothing(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        first = _conversation(db_session, account, "First")
        second = _conversation(db_session, account, "Second")
        auth_client.patch(f"/api/v1/ai/conversations/{first.id}", json={"pinned": True})
        auth_client.patch(f"/api/v1/ai/conversations/{second.id}", json={"pinned": True})

        # A second pin of the first must not jump it above the one pinned after it.
        auth_client.patch(f"/api/v1/ai/conversations/{first.id}", json={"pinned": True})

        assert _ids(auth_client)[:2] == [second.id, first.id]

    def test_a_pin_is_found_by_search_and_still_first(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        newer = _conversation(db_session, account, "Flange B")
        older = _conversation(db_session, account, "Flange A", age_minutes=300)
        auth_client.patch(f"/api/v1/ai/conversations/{older.id}", json={"pinned": True})

        assert _ids(auth_client, q="flange") == [older.id, newer.id]

    def test_someone_elses_conversation_cannot_be_pinned(
        self, db_session: Session, auth_client: AuthenticatedTestClient
    ) -> None:
        stranger = User(email="s@kryova.dev", hashed_password=hash_password("a-long-enough-pw"))
        db_session.add(stranger)
        db_session.flush()
        theirs = _conversation(db_session, stranger, "Theirs")

        response = auth_client.patch(
            f"/api/v1/ai/conversations/{theirs.id}", json={"pinned": True}
        )

        assert response.status_code == 404
        db_session.refresh(theirs)
        assert theirs.pinned_at is None


class TestRenamingStillWorks:
    def test_a_title_alone_still_renames(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _conversation(db_session, account, "Old name")

        response = auth_client.patch(f"/api/v1/ai/conversations/{row.id}", json={"title": "  New  "})

        assert response.status_code == 200 and response.json()["title"] == "New"
        assert response.json()["pinned"] is False

    def test_a_request_that_changes_nothing_is_refused(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _conversation(db_session, account, "Same")
        assert auth_client.patch(f"/api/v1/ai/conversations/{row.id}", json={}).status_code == 422

    def test_a_title_of_only_spaces_is_refused(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _conversation(db_session, account, "Same")
        response = auth_client.patch(f"/api/v1/ai/conversations/{row.id}", json={"title": "   "})
        assert response.status_code == 422

    def test_a_title_and_a_pin_together_both_apply(
        self, db_session: Session, auth_client: AuthenticatedTestClient, account: User
    ) -> None:
        row = _conversation(db_session, account, "Same")

        body = auth_client.patch(
            f"/api/v1/ai/conversations/{row.id}", json={"title": "Renamed", "pinned": True}
        ).json()

        assert body["title"] == "Renamed" and body["pinned"] is True
