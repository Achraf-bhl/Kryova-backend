"""Two guards that stop a weak model turning a near-miss into a dead end.

Both were written from observed behaviour, driving the real agent against a
local `gpt-oss:20b` over the same endpoint the browser uses.

1. A tool error has to name the mistake that was actually made. Asked to build a
   part, the model called `catia_new_part` with `project`, then `project_name`,
   then `part_name` -- never `name`. The validator answered "arguments.name is
   required" every time, which says nothing about the key it sent, so it kept
   guessing and the turn died.

2. One project per conversation. The tool description says so; the model created
   a second one on the next turn anyway, and the conversation silently moved to
   a project the user had never asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.ai.tools import ToolBox, ToolError
from app.catia.validation import SchemaError, validate
from app.models import Project

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


NEW_PART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1}},
    "required": ["name"],
    "additionalProperties": False,
}


class TestAWrongKeyNamesItself:
    """The error has to be actionable, not merely correct."""

    @pytest.mark.parametrize("wrong", ["project", "project_name", "part_name"])
    def test_the_rejected_key_is_named(self, wrong: str) -> None:
        with pytest.raises(SchemaError) as caught:
            validate({wrong: "Bracket"}, NEW_PART_SCHEMA)
        assert wrong in str(caught.value)

    @pytest.mark.parametrize("wrong", ["project", "project_name", "part_name"])
    def test_the_accepted_key_is_offered(self, wrong: str) -> None:
        with pytest.raises(SchemaError) as caught:
            validate({wrong: "Bracket"}, NEW_PART_SCHEMA)
        assert "name" in str(caught.value)

    def test_an_empty_call_says_what_was_missing_and_what_arrived(self) -> None:
        with pytest.raises(SchemaError) as caught:
            validate({}, NEW_PART_SCHEMA)
        message = str(caught.value)
        assert "name is required" in message
        assert "(nothing)" in message

    def test_a_correct_call_still_passes(self) -> None:
        validate({"name": "Bracket"}, NEW_PART_SCHEMA)

    def test_the_daemon_validator_agrees(self) -> None:
        # The daemon re-validates every call against its own copy. If the two
        # disagree, the server accepts what the workstation refuses.
        from catia_bridge.validation import SchemaError as DaemonSchemaError
        from catia_bridge.validation import validate as daemon_validate

        with pytest.raises(DaemonSchemaError) as caught:
            daemon_validate({"part_name": "Bracket"}, NEW_PART_SCHEMA)
        assert "part_name" in str(caught.value)
        assert "name" in str(caught.value)


class TestOneProjectPerConversation:
    def test_a_second_create_is_refused(self, db_session: Any, current_user_id: str) -> None:
        from app.models import User

        user = db_session.get(User, current_user_id)
        box = ToolBox(db=db_session, user=user)

        first = box.call("create_project", {"name": "Steel mounting bracket"}, allow_mutations=True)
        assert first["name"] == "Steel mounting bracket"

        with pytest.raises(ToolError) as caught:
            box.call("create_project", {"name": "Flat Plate"}, allow_mutations=True)

        message = str(caught.value)
        # The model needs the id to carry on in the right project.
        assert first["id"] in message
        assert "Steel mounting bracket" in message

    def test_the_second_project_is_not_created(self, db_session: Any, current_user_id: str) -> None:
        from sqlalchemy import func, select

        from app.models import User

        user = db_session.get(User, current_user_id)
        box = ToolBox(db=db_session, user=user)
        box.call("create_project", {"name": "Steel mounting bracket"}, allow_mutations=True)

        before = db_session.scalar(
            select(func.count()).select_from(Project).where(Project.owner_id == user.id)
        )
        with pytest.raises(ToolError):
            box.call("create_project", {"name": "Flat Plate"}, allow_mutations=True)
        after = db_session.scalar(
            select(func.count()).select_from(Project).where(Project.owner_id == user.id)
        )
        assert after == before

    def test_a_fresh_conversation_can_still_create_one(
        self, db_session: Any, current_user_id: str
    ) -> None:
        from app.models import User

        user = db_session.get(User, current_user_id)
        ToolBox(db=db_session, user=user).call(
            "create_project", {"name": "First"}, allow_mutations=True
        )
        # A different conversation means a different ToolBox with no scope.
        second = ToolBox(db=db_session, user=user).call(
            "create_project", {"name": "Second"}, allow_mutations=True
        )
        assert second["name"] == "Second"


class TestAnUnknownToolSuggestsTheRealOne:
    """26 tools in one alphabetical list buries the answer."""

    @pytest.fixture
    def box(self, db_session: Any, current_user_id: str) -> ToolBox:
        from app.models import User

        return ToolBox(db=db_session, user=db_session.get(User, current_user_id))

    @pytest.mark.parametrize(
        ("guess", "intended"),
        [
            # Observed live: the model prefixed a Kryova tool with `catia_`,
            # then gave up when the answer was eight names down the list.
            ("catia_list_projects", "list_projects"),
            ("list_project", "list_projects"),
            ("catia_newpart", "catia_new_part"),
        ],
    )
    def test_the_closest_real_name_is_offered_first(
        self, box: ToolBox, guess: str, intended: str
    ) -> None:
        with pytest.raises(ToolError) as caught:
            box.call(guess, {}, allow_mutations=True)
        message = str(caught.value)
        assert "Did you mean" in message
        suggestions = message.split("Did you mean: ")[1].split("?")[0]
        assert intended in suggestions

    def test_the_full_list_is_still_there(self, box: ToolBox) -> None:
        with pytest.raises(ToolError) as caught:
            box.call("utterly_unrelated_xyzzy", {}, allow_mutations=True)
        assert "Available:" in str(caught.value)
