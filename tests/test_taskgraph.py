"""The declared plan, its order, and its checkpoints (E16 tasks 2 and 5).

`planning.py` holds the *requirements*; this holds the *steps*, and the one
thing it adds over a list is that the order is enforced. So most of what is
worth testing is the moves it refuses — a task settled before what it stands on,
a cycle that would make "what is left" unanswerable, a plan long enough to be a
work breakdown structure — because each of those turns a graph back into a list
that merely looks like one.
"""

from __future__ import annotations

import pytest

from app.ai.taskgraph import (
    MAX_TASKS,
    PlanError,
    Task,
    TaskGraph,
    TaskState,
    graph_from_tasks,
)


def press() -> TaskGraph:
    """A small plan with a real shape: a checkpoint in the middle of a chain."""
    return TaskGraph.of(
        [
            Task("frame", "Size and build the C-frame"),
            Task("sizing", "Work out the punching force", checkpoint=True),
            Task("ram", "Build the ram", depends_on=("frame", "sizing")),
            Task("die", "Build the die block", depends_on=("frame",)),
            Task("assemble", "Assemble and constrain", depends_on=("ram", "die")),
        ]
    )


class TestConstruction:
    def test_a_cycle_is_refused_naming_the_path(self) -> None:
        """"There is a cycle" in a twenty-task plan sends somebody reading every
        edge. Naming the path is the difference between a minute and an
        afternoon."""
        with pytest.raises(PlanError) as refused:
            TaskGraph.of(
                [
                    Task("a", "A", depends_on=("c",)),
                    Task("b", "B", depends_on=("a",)),
                    Task("c", "C", depends_on=("b",)),
                ]
            )

        assert "->" in str(refused.value)
        assert "a" in str(refused.value) and "c" in str(refused.value)

    def test_a_task_cannot_depend_on_itself(self) -> None:
        with pytest.raises(PlanError, match="depends on itself"):
            Task("a", "A", depends_on=("a",))

    def test_a_dependency_on_nothing_is_refused_with_the_real_ids_listed(self) -> None:
        with pytest.raises(PlanError) as refused:
            TaskGraph.of([Task("a", "A", depends_on=("ghost",))])

        assert "ghost" in str(refused.value)
        assert "Tasks: a" in str(refused.value)

    def test_two_tasks_with_one_id_are_refused(self) -> None:
        with pytest.raises(PlanError, match="coin flip"):
            TaskGraph.of([Task("a", "A"), Task("a", "Also A")])

    def test_a_plan_longer_than_the_ceiling_is_refused(self) -> None:
        """Not a resource limit. A plan this long is evidence the model wrote a
        work breakdown structure, and Era VIII's finding is that ambitious
        multi-step strategies are where the strongest models fail hardest."""
        with pytest.raises(PlanError, match="work breakdown structure"):
            TaskGraph.of(Task(f"t{n}", f"Step {n}") for n in range(MAX_TASKS + 1))


class TestOrder:
    def test_dependencies_come_before_what_stands_on_them(self) -> None:
        order = [task.id for task in press().order()]

        assert order.index("frame") < order.index("ram")
        assert order.index("sizing") < order.index("ram")
        assert order.index("ram") < order.index("assemble")

    def test_the_order_is_stable_across_calls(self) -> None:
        """A sort that broke ties arbitrarily would reorder two independent
        tasks between one call and the next, so a model reading "what is left"
        twice would see two plans and conclude something had happened."""
        graph = press()

        assert [task.id for task in graph.order()] == [task.id for task in graph.order()]

    def test_ready_is_what_could_be_worked_on_now(self) -> None:
        ready = [task.id for task in press().ready()]

        assert ready[:2] == ["frame", "sizing"]
        assert "ram" not in ready

    def test_a_blocked_task_is_not_ready(self) -> None:
        """Reporting it as workable is how a model spends three steps retrying a
        call that will keep failing for the same reason."""
        graph = press().advance("frame", TaskState.BLOCKED, note="no seat")

        assert "frame" not in [task.id for task in graph.ready()]


class TestMoves:
    def test_a_task_cannot_be_done_before_what_it_stands_on(self) -> None:
        with pytest.raises(PlanError) as refused:
            press().advance("ram", TaskState.DONE)

        assert "frame" in str(refused.value)
        assert "sizing" in str(refused.value)

    def test_a_refused_move_leaves_the_old_graph_untouched(self) -> None:
        """Frozen for this reason: a caller that refuses the result is not left
        holding a half-applied plan."""
        graph = press()

        with pytest.raises(PlanError):
            graph.advance("ram", TaskState.DONE)

        assert graph.task("ram").state is TaskState.PENDING

    def test_a_skipped_dependency_clears_the_way(self) -> None:
        """`SKIPPED` is a task deliberately not done. It satisfies a dependency
        because the thing downstream was cleared to proceed — which is exactly
        what makes it different from `BLOCKED`."""
        graph = press()
        graph = graph.advance("frame", TaskState.DONE)
        graph = graph.advance("sizing", TaskState.SKIPPED, note="force given by the user")

        assert graph.advance("ram", TaskState.DONE).task("ram").state is TaskState.DONE

    def test_skipped_is_not_reported_as_done(self) -> None:
        """A reader asking "was the sizing worked out" must not be told yes."""
        graph = press().advance("sizing", TaskState.SKIPPED, note="given")

        assert graph.task("sizing").state is TaskState.SKIPPED
        assert "skipped" in graph.brief()

    def test_going_backwards_is_allowed(self) -> None:
        """A task found to be wrong after the fact goes back to pending.
        Refusing that would make the plan a thing to work around."""
        graph = press().advance("frame", TaskState.DONE)

        assert graph.advance("frame", TaskState.PENDING).task("frame").state is TaskState.PENDING

    def test_moving_a_task_that_is_not_in_the_plan_lists_the_ones_that_are(self) -> None:
        with pytest.raises(PlanError) as refused:
            press().advance("nope", TaskState.DONE)

        assert "frame" in str(refused.value)


class TestCheckpoints:
    def test_an_open_checkpoint_blocks_everything_downstream(self) -> None:
        graph = press().advance("frame", TaskState.DONE)

        assert "sizing" in graph.blocked_by("ram")
        assert "sizing" in graph.blocked_by("assemble")

    def test_blocked_by_is_transitive(self) -> None:
        """A task three steps behind an unfinished checkpoint is blocked by the
        checkpoint. Being told only about its immediate parent sends a reader up
        the chain one hop at a time."""
        blocked = press().blocked_by("assemble")

        assert set(blocked) >= {"frame", "sizing", "ram", "die"}

    def test_a_settled_checkpoint_stops_blocking(self) -> None:
        graph = press().advance("sizing", TaskState.DONE)

        assert "sizing" not in graph.blocked_by("ram")
        assert graph.open_checkpoints() == ()

    def test_the_brief_names_a_checkpoint_as_needing_sign_off(self) -> None:
        assert "needs sign-off" in press().brief()


class TestTheBrief:
    def test_it_names_the_next_step_rather_than_counting(self) -> None:
        """A count is not actionable. The point is that the model can see the
        specific next thing."""
        assert "Next: frame." in press().brief()

    def test_a_stuck_plan_says_so_instead_of_offering_no_next(self) -> None:
        """An empty "next" reads as "nothing to do" on a plan that is stuck,
        which is the reading that closes a turn reporting success."""
        graph = press()
        for task_id in ("frame", "sizing"):
            graph = graph.advance(task_id, TaskState.BLOCKED, note="waiting on the seat")

        assert "Nothing is ready" in graph.brief()

    def test_an_empty_plan_renders_nothing(self) -> None:
        # A block saying "no plan" on every turn is furniture in the one place
        # furniture is most expensive.
        assert TaskGraph().brief() == ""


class TestPersistence:
    def test_a_plan_round_trips(self) -> None:
        graph = press().advance("frame", TaskState.DONE)

        assert TaskGraph.from_dict(graph.to_dict()).to_dict() == graph.to_dict()

    def test_no_plan_loads_as_an_empty_plan_rather_than_an_error(self) -> None:
        assert len(TaskGraph.from_dict(None)) == 0
        assert len(TaskGraph.from_dict({})) == 0

    def test_a_future_format_is_refused_rather_than_guessed_at(self) -> None:
        with pytest.raises(PlanError, match="Refusing to guess"):
            TaskGraph.from_dict({"format_version": 99, "tasks": []})

    def test_an_unknown_key_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(PlanError, match="unknown keys"):
            TaskGraph.from_dict(
                {"format_version": 1, "tasks": [{"id": "a", "title": "A", "colour": "red"}]}
            )

    def test_an_unknown_state_lists_the_ones_that_exist(self) -> None:
        with pytest.raises(PlanError) as refused:
            TaskGraph.from_dict(
                {"format_version": 1, "tasks": [{"id": "a", "title": "A", "state": "nearly"}]}
            )

        assert "pending" in str(refused.value)

    def test_a_task_missing_a_title_says_what_a_title_is_for(self) -> None:
        with pytest.raises(PlanError) as refused:
            graph_from_tasks([{"id": "a"}])

        assert "what a person reads" in str(refused.value)
