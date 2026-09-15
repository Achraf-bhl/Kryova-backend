"""Every operation the open kernel does not implement says why (master plan E1.3).

Until 2026-09-14 the 85 unimplemented operations were refused with one sentence,
"not implemented in the open kernel yet". True, and it did not tell the agent
whether to look for another route or to stop asking. `app/kernel/occt/refusals.py`
holds a reason per operation; these tests hold it to the registry, and hold every
reason to naming only tools this backend really serves.

Offline: no seat, no database.
"""

from __future__ import annotations

import re

import pytest

from app.kernel import available
from app.kernel.occt.refusals import REASONS, reason_for

pytestmark = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


def _served() -> set[str]:
    from app.geometry.backends import LOCALLY_SERVED
    from app.kernel.occt.operations import HANDLERS

    return set(HANDLERS) | set(LOCALLY_SERVED)


def _declared() -> set[str]:
    from app.catia.ops.registry import OPERATIONS_BY_NAME

    return set(OPERATIONS_BY_NAME)


class TestEveryDeclaredOperationIsAccountedFor:
    def test_each_one_is_implemented_served_or_refused_with_a_reason(self) -> None:
        """An operation added to the registry with no implementation fails here
        until somebody writes down why the open kernel does not have it."""
        unaccounted = _declared() - _served() - set(REASONS)

        assert unaccounted == set()

    def test_no_operation_is_both_served_and_refused(self) -> None:
        """A reason left behind after the operation is implemented would be read
        by nothing and believed by whoever opens the file."""
        assert _served() & set(REASONS) == set()

    def test_every_reason_is_for_an_operation_the_registry_declares(self) -> None:
        assert set(REASONS) - _declared() == set()

    def test_every_reason_says_something(self) -> None:
        for tool, reason in REASONS.items():
            assert len(reason.split()) >= 8, f"{tool}: {reason!r} is not a reason"


class TestAReasonSendsTheAgentOnlyWhereItCanGo:
    def test_every_tool_a_reason_names_is_one_this_backend_serves(self) -> None:
        """A refusal that names another refused operation costs the agent a turn
        and teaches it nothing."""
        served = _served()
        dead_ends = {
            tool: named
            for tool, reason in REASONS.items()
            if (named := [name for name in re.findall(r"catia_\w+", reason) if name not in served])
        }

        assert dead_ends == {}

    def test_taking_a_feature_back_points_at_the_delete_that_now_exists(self) -> None:
        for tool in ("catia_checkpoint", "catia_restore", "catia_feature_activate"):
            assert "catia_delete_feature" in REASONS[tool]


class TestTheRunnerCarriesTheReason:
    def test_a_refused_operation_raises_with_its_reason(self) -> None:
        from app.kernel import OcctRunner
        from app.kernel.errors import OperationNotSupported

        runner = OcctRunner()
        runner("catia_new_part", {"name": "Plate"})

        with pytest.raises(OperationNotSupported) as caught:
            runner("catia_select", {"names": ["Pad.1"]})

        assert caught.value.subject == "catia_select"
        assert caught.value.reason == REASONS["catia_select"]
        assert "no interface to drive" in str(caught.value)

    def test_a_name_nothing_declares_has_no_reason_rather_than_a_crash(self) -> None:
        assert reason_for("catia_nothing_by_this_name") == ""


class TestTheAgentIsToldTheReason:
    def test_the_dispatcher_keeps_the_reason_beside_the_coverage(self, monkeypatch) -> None:
        """`_execute_locally` is where the agent reads a refusal. The coverage
        number stays, because it makes "not available" checkable."""
        from app.catia.dispatch import CatiaError, _execute_locally
        from app.catia.ops.registry import OPERATIONS_BY_NAME
        from app.core.config import settings
        from app.geometry import backends

        monkeypatch.setattr(settings, "geometry_backend", "occt")
        try:
            backends.session_for("refused")("catia_new_part", {"name": "Plate"})
            with pytest.raises(CatiaError) as caught:
                _execute_locally(
                    spec=OPERATIONS_BY_NAME["catia_drawing_create"],
                    conversation_id="refused",
                    arguments={},
                )
        finally:
            backends.forget("refused")

        message = str(caught.value)
        assert "catia_drawing_create is not available on the open kernel" in message
        assert "app/manufacture" in message, "the reason says where drawings come from"
        assert "operations are" in message


class TestNothingOfferedCanOnlyRefuse:
    def test_no_handler_is_a_refusal_in_disguise(self) -> None:
        """A handler whose whole body is a `raise` is offered to the agent by
        `local_tool_names` and can never work. Its reason belongs in `REASONS`,
        where the dispatcher already carries it. `catia_sketch_constrain` was one
        until 2026-09-15."""
        import ast
        import inspect
        import textwrap

        from app.kernel.occt.operations import HANDLERS

        def only_raises(handler) -> bool:
            function = ast.parse(textwrap.dedent(inspect.getsource(handler))).body[0]
            assert isinstance(function, ast.FunctionDef), handler
            body = [
                statement
                for statement in function.body
                if not (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                )
            ]
            return len(body) == 1 and isinstance(body[0], ast.Raise)

        assert [tool for tool, handler in HANDLERS.items() if only_raises(handler)] == []

    def test_constraining_a_sketch_is_refused_rather_than_offered(self) -> None:
        from app.geometry.backends import local_tool_names

        assert "catia_sketch_constrain" not in local_tool_names()
        reason = REASONS["catia_sketch_constrain"]
        assert "PlaneGCS" in reason
        assert "catia_sketch_polyline" in reason, "it says how to get the profile instead"
