"""A closed sketch outline must be one simple loop -- `app/catia/ops/outline.py`.

The first class is the geometry, offline. The second goes through `call_catia`,
because a check the agent's path never reaches proves nothing (CLAUDE.md,
Testing item 8): the refusal happens in `dispatch._augment`, before either
backend, so it needs no seat and no kernel.
"""

from __future__ import annotations

import pytest

from app.catia.ops.outline import crossing

#: What qwen3.8 sent on 2026-09-24 for the flanged bushing. (25, 40) is visited
#: three times.
MEASURED = [[15, 0], [15, 40], [25, 40], [25, 48], [35, 48], [35, 40], [25, 40], [25, 0], [15, 0]]

#: The profile it meant: bore radius 15, tube radius 25 for 40 mm, flange radius
#: 35 for the last 8.
STEPPED = [[15, 0], [25, 0], [25, 40], [35, 40], [35, 48], [15, 48]]


class TestASimpleLoopIsAccepted:
    @pytest.mark.parametrize(
        "points",
        [
            [[0, 0], [60, 0], [60, 40], [0, 40]],
            STEPPED,
            # A C-frame: concave, still simple.
            [[0, 0], [100, 0], [100, 20], [20, 20], [20, 80], [100, 80], [100, 100], [0, 100]],
            # The first point repeated at the end is the same loop.
            [[0, 0], [60, 0], [60, 40], [0, 40], [0, 0]],
            # A collinear vertex mid-edge is not a fold.
            [[0, 0], [30, 0], [60, 0], [60, 40], [0, 40]],
        ],
    )
    def test_it_passes(self, points: list[list[float]]) -> None:
        assert crossing(points) is None


class TestAnOutlineThatIsNotOneLoopIsRefused:
    def test_the_measured_bushing_outline_is_refused_and_the_meeting_point_named(self) -> None:
        reason = crossing(MEASURED)

        assert reason is not None
        assert "(25, 40)" in reason
        assert "List each corner once" in reason

    def test_a_figure_eight_is_refused(self) -> None:
        assert crossing([[0, 0], [10, 10], [10, 0], [0, 10]]) is not None

    def test_an_edge_that_doubles_back_is_refused(self) -> None:
        reason = crossing([[0, 0], [10, 0], [5, 0], [5, 5]])

        assert reason is not None
        assert "doubles back" in reason

    def test_a_loop_pinched_at_one_vertex_is_refused(self) -> None:
        # Two triangles sharing the point (10, 0).
        assert crossing([[0, 0], [10, 0], [20, 5], [20, -5], [10, 0], [0, 5]]) is not None


class TestTheAgentsPathRefusesIt:
    def test_call_catia_refuses_a_self_crossing_closed_polyline(
        self, db_session, current_user_id
    ) -> None:
        from app.catia.dispatch import CatiaError, call_catia

        with pytest.raises(CatiaError, match=r"catia_sketch_polyline: .*\(25, 40\)"):
            call_catia(
                db_session,
                user_id=current_user_id,
                tool="catia_sketch_polyline",
                arguments={"points": MEASURED, "closed": True},
                conversation_id=None,
            )

    def test_an_open_polyline_is_left_alone(self, db_session, current_user_id) -> None:
        """An open polyline may be part of a profile finished by other elements,
        so it is not this check's business -- whatever happens next, it is not
        this refusal."""
        from app.catia.dispatch import call_catia

        try:
            call_catia(
                db_session,
                user_id=current_user_id,
                tool="catia_sketch_polyline",
                arguments={"points": MEASURED[:-1]},
                conversation_id=None,
            )
        except Exception as exc:  # noqa: BLE001 - no seat here; only this refusal matters
            assert "touches or crosses itself" not in str(exc)
