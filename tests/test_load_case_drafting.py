"""The agent can ask for a load case, and a rejected one says what to fix.

Measured on ladder prompt H4, 2026-09-06 -- "a bracket that bolts to a wall
with two M8 fasteners and carries a 500 N load hanging 150 mm out, in mild
steel, with a safety factor of at least 2. Design it and tell me what it will
actually take." The bracket was built correctly: sketch, pad, two holes,
steel, fillets, every step green. Then the agent tried four times to run the
analysis and got, four times:

    That load case is not valid: 3 validation errors for LoadCase
    material
      Field required [type=missing, input_value={...}, input_type=dict]
        For further information visit https://errors.pydantic.dev/2.13/v/missing

Every line of that is about pydantic. None of it says what a load case is, and
the one thing that fixes it in a single round -- an example of the right shape
-- was nowhere. So the prompt whose entire point is the analysis produced a
part and no analysis.

Two things closed it, and only the first is a bug fix:

* the refusal now names the fields in words and carries one valid case in
  full;
* `draft_load_case` exposes to the agent what the product already had. Turning
  a sentence into a load case against a real bounding box has existed since the
  load-case route was written, reachable only from the web form. The capability
  was in the product and not in the tool set -- which is the failure mode this
  file exists to record, because it looks exactly like a missing feature from
  the outside.

Offline: no model, no database beyond the fixtures, no CATIA.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.ai.tools import _LOAD_CASE_EXAMPLE, ToolBox, ToolError, _load_case_problem
from app.models.project import Project
from app.models.user import User
from app.solve.types import LoadCase


@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="loadcase@kryova.dev", hashed_password=hash_password("a-long-enough-password")
    )
    db_session.add(account)
    db_session.flush()
    return account


@pytest.fixture
def project(db_session: Session, user: User) -> Project:
    row = Project(name="Wall bracket", owner_id=user.id)
    db_session.add(row)
    db_session.flush()
    return row


def named(box: ToolBox) -> dict[str, dict]:
    """The tool definitions the model would be sent, keyed by name."""
    return {
        schema["function"]["name"]: schema["function"]
        for schema in box.schemas(include_mutating=True)
    }


def rejection(payload: dict) -> str:
    with pytest.raises(ValidationError) as raised:
        LoadCase.model_validate(payload)
    return _load_case_problem(raised.value)


class TestTheExampleIsReal:
    def test_it_validates(self) -> None:
        """The one assertion that matters here. An invalid example is worse
        than none: the model copies it, is refused again, and now has evidence
        that the tool is broken. This caught exactly that -- the first version
        omitted `yield_strength_mpa` and would have sent every reader round a
        second time."""
        case = LoadCase.model_validate(json.loads(_LOAD_CASE_EXAMPLE))
        assert case.fixtures and case.loads

    def test_it_is_the_smallest_case_that_runs(self) -> None:
        """Anything longer buries the shape in detail the reader does not need."""
        payload = json.loads(_LOAD_CASE_EXAMPLE)
        assert set(payload) == {"name", "material", "fixtures", "loads"}
        assert len(payload["fixtures"]) == 1
        assert len(payload["loads"]) == 1


class TestTheRefusal:
    def test_a_missing_field_is_named_in_words(self) -> None:
        message = rejection({"force_n": 500, "direction": "down"})
        assert "Missing: material" in message
        assert "fixtures" in message and "loads" in message

    def test_a_wrong_shape_is_named_apart_from_a_missing_one(self) -> None:
        """They are different mistakes and need different fixes: one is
        something to add, the other something to rewrite."""
        message = rejection({"loads": [{"force_n": 500}], "fixtures": ["left"]})
        assert "Missing:" in message
        assert "Wrong:" in message
        assert "fixtures.0" in message

    def test_the_example_is_always_there(self) -> None:
        assert _LOAD_CASE_EXAMPLE in rejection({})

    def test_it_names_the_tool_that_would_have_worked(self) -> None:
        """The whole point. A refusal that only says no leaves the model to
        guess again, and it guesses the same thing -- four times, measured."""
        assert "draft_load_case" in rejection({})

    def test_pydantics_own_wording_is_gone(self) -> None:
        """`[type=missing, input_value=...]` and a link to pydantic's docs are
        noise to a model deciding what to send next."""
        message = rejection({})
        assert "type=missing" not in message
        assert "errors.pydantic.dev" not in message
        assert "validation error" not in message.lower()

    def test_a_long_list_of_errors_does_not_bury_the_example(self) -> None:
        """A model that got the shape wrong has one problem, not seven."""
        message = rejection(
            {
                "loads": [{"force_n": "a"}, {"force_n": "b"}, {"force_n": "c"}],
                "fixtures": ["a", "b", "c", "d", "e"],
            }
        )
        assert message.index("This one is valid") > 0
        assert message.count(";") <= 4


class TestThroughTheToolItself:
    """The refusal has to arrive from `run_simulation`, not only from a helper.

    The first version of these tests called `_load_case_problem` directly, and
    passed with the call site restored to pydantic's raw text -- a guard on a
    function nothing had to call. This goes the way the model does.
    """

    def test_the_readable_refusal_is_what_run_simulation_raises(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = ToolBox(
            db=db_session,
            user=user,
            project_id=project.id,
            job_queue=object(),
            session_scope=object(),
            media_store=object(),
        )
        with pytest.raises(ToolError) as raised:
            box.call(
                "run_simulation",
                {"load_case": {"force_n": 500, "direction": "down"}},
                allow_mutations=True,
            )
        message = str(raised.value)
        assert "Missing: material" in message
        assert _LOAD_CASE_EXAMPLE in message
        assert "draft_load_case" in message
        assert "type=missing" not in message


class TestTheTool:
    def test_it_is_offered(self, db_session: Session, user: User) -> None:
        assert "draft_load_case" in named(ToolBox(db=db_session, user=user))

    def test_it_says_the_part_must_be_exported_first(
        self, db_session: Session, user: User
    ) -> None:
        """A load case is resolved against a bounding box, and a part that is
        only open in CATIA has none here. The description names the tool that
        exports it rather than leaving the agent to find one."""
        spec = named(ToolBox(db=db_session, user=user))["draft_load_case"]
        assert "catia_export_step" in spec["description"]

    def test_without_a_provider_it_refuses_rather_than_pretending(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        """Drafting is a model call. A toolbox with no model must say so, not
        return an empty draft that reads like an answer."""
        box = ToolBox(db=db_session, user=user, project_id=project.id, provider=None)
        with pytest.raises(ToolError) as raised:
            box.call(
                "draft_load_case", {"description": "500 N on the end"}, allow_mutations=True
            )
        assert "No model is available" in str(raised.value)

    def test_without_geometry_it_names_the_way_to_get_some(
        self, db_session: Session, user: User, project: Project
    ) -> None:
        box = ToolBox(db=db_session, user=user, project_id=project.id, provider=object())
        with pytest.raises(ToolError) as raised:
            box.call(
                "draft_load_case", {"description": "500 N on the end"}, allow_mutations=True
            )
        assert "catia_export_step" in str(raised.value)

    def test_the_description_says_the_result_is_a_draft(
        self, db_session: Session, user: User
    ) -> None:
        """The numbers in it are choices the user did not make. A tool
        described as producing a load case invites the agent to run it
        unread."""
        spec = named(ToolBox(db=db_session, user=user))["draft_load_case"]
        assert "assumptions" in spec["description"]


class TestAMeshThatCannotFitIsRefusedAtSubmit:
    """Measured on H4 run 8 (2026-09-06): the agent asked for 2 mm elements
    on a bracket that would need 1.68 million of them. The runner refused --
    as a *failed job*, discovered by polling, answered by resubmitting: three
    of the twenty rounds. Here the refusal is the tool's own answer, in the
    same round, with the size that fits."""

    @pytest.fixture
    def geometry(self, db_session: Session, project: Project, user: User):
        from app.models import GeometryVersion, Media, MediaKind

        media = Media(
            owner_id=user.id,
            kind=MediaKind.CAD,
            filename="bracket.stl",
            content_type="model/stl",
            size_bytes=128,
            sha256="0" * 64,
        )
        db_session.add(media)
        db_session.flush()
        version = GeometryVersion(
            project_id=project.id,
            media_id=media.id,
            version_number=1,
            filename="bracket.stl",
            file_format="stl",
            stats={
                "bounding_box": {"min": [0, 0, 0], "max": [150, 40, 10], "size": [150, 40, 10]}
            },
        )
        db_session.add(version)
        db_session.flush()
        return version

    def test_the_refusal_comes_from_run_simulation_with_a_usable_size(
        self, db_session: Session, user: User, project: Project, geometry
    ) -> None:
        submitted: list[object] = []

        class _Queue:
            def submit(self, job):  # pragma: no cover - must never run
                submitted.append(job)

        box = ToolBox(
            db=db_session,
            user=user,
            project_id=project.id,
            job_queue=_Queue(),
            session_scope=object(),
            media_store=object(),
        )
        with pytest.raises(ToolError) as raised:
            box.call(
                "run_simulation",
                {"load_case": json.loads(_LOAD_CASE_EXAMPLE), "element_size_mm": 0.01},
                allow_mutations=True,
            )
        message = str(raised.value)
        assert "Use at least" in message
        assert "omit element_size_mm" in message
        # Verified by breaking it: nothing was queued and no job row exists.
        assert submitted == []
        from sqlalchemy import select

        from app.models import SimulationJob

        assert db_session.scalars(select(SimulationJob)).first() is None
