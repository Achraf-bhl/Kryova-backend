"""An ordered, dependency-aware task graph with checkpoints (E16 task 2).

`app/ai/planning.py` is the seam this stands on: it turns a request into the
*requirements* it states and keeps them where the context window cannot trim
them. Its own docstring says what it deliberately is not — "nothing here
sequences tools, chooses an order, or replans after a failure". This module is
the sequencing half, and it is the smallest thing that honestly is one.

**It is deliberately not an autonomous planner, and the evidence for that is in
the plan.** Era VIII records a measured finding the earlier eras did not know:
memory scaffolds degraded long-horizon performance in *all ten* models tested,
additional orchestration does not consistently help, and the strongest models
show the highest catastrophic-failure rates because they attempt the most
ambitious multi-step strategies. What is measured to help is **shortening the
horizon**. So this holds a plan the agent declared and *enforces its order*; it
does not generate one, re-generate one, or ask a model to reason about it. The
model does the thinking; the server holds the list and refuses the moves that
are out of order.

That distinction is the whole design:

**A task cannot be finished before what it stands on.** `advance` refuses to
mark a task done while a dependency is not, and names the dependency. Without
that, a "plan" is a list the model can tick off in any order it likes — which is
a list, not a graph, and is exactly what `planning.py` already provides.

**A checkpoint is a task nothing may pass without a decision.** `checkpoint=True`
marks a task whose completion is a human's to confirm (E16 task 5), and
`blocked_by` reports it as blocking everything downstream. The gate record and
its UI are P5.5's; this is the thing in the agent's own plan that points at one.

**A cycle is refused at construction.** A graph with a cycle has no order, so
every question this module answers — what is ready, what is blocked, what is
left — has no answer. Refusing on the way in means the failure arrives naming
the two tasks that point at each other, rather than as a `ready()` that
mysteriously returns nothing forever.

**Pure, and stored as a dict.** No session, no models, no imports from
`app/models`. `Conversation.task_graph` holds `to_dict()`, the same way the
design record holds `DesignSpec.to_dict()`, so this stays testable without a
database and the storage layer stays ignorant of the vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final

#: Bumped when the serialised shape changes in a way an older reader would get
#: wrong. Refused rather than guessed at on load — the same rule `DesignSpec`
#: keeps, and for the same reason: a half-understood plan is worse than none.
FORMAT_VERSION: Final = 1

#: Ceiling on tasks in one graph. Not a resource limit — a graph this size is
#: evidence that the model has written a work breakdown structure rather than a
#: plan, and Era VIII's finding is that ambitious multi-step strategies are
#: where the strongest models fail hardest. Refusing is more useful than
#: quietly accepting sixty steps nobody will follow.
MAX_TASKS: Final = 40


class PlanError(ValueError):
    """A plan that cannot be held, or a move that cannot be made in it."""


class TaskState(StrEnum):
    """Five states, and `BLOCKED` is not a kind of `DONE` or a kind of failure.

    `SKIPPED` is a task deliberately not done — the design went a different way
    — and it is distinct from `DONE` because a reader asking "was the fillet
    added" must not be told yes. It satisfies a dependency, because the thing
    downstream was cleared to proceed; that is what makes it different from
    `BLOCKED`.
    """

    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    BLOCKED = "blocked"
    SKIPPED = "skipped"

    @property
    def settled(self) -> bool:
        """Does this state clear the way for what depends on it?"""
        return self in (TaskState.DONE, TaskState.SKIPPED)


@dataclass(frozen=True, slots=True)
class Task:
    """One step, what it stands on, and where it got to."""

    id: str
    title: str
    #: Ids of tasks that must be settled before this one can be. Order is not
    #: significant and duplicates are harmless.
    depends_on: tuple[str, ...] = ()
    state: TaskState = TaskState.PENDING
    #: Why it is where it is: what blocked it, what was skipped and why. Free
    #: text, written by whoever moved it.
    note: str = ""
    #: A human has to confirm this one (E16 task 5). Nothing downstream of a
    #: checkpoint may be settled until it is.
    checkpoint: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise PlanError("A task needs an id; it is how other tasks refer to it.")
        if not self.title or not self.title.strip():
            raise PlanError(f"Task {self.id!r} needs a title a person could read.")
        if self.id in self.depends_on:
            raise PlanError(f"Task {self.id!r} depends on itself, so it can never start.")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "title": self.title, "state": self.state.value}
        if self.depends_on:
            out["depends_on"] = list(self.depends_on)
        if self.note:
            out["note"] = self.note
        if self.checkpoint:
            out["checkpoint"] = True
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Task:
        unknown = set(data) - {"id", "title", "state", "depends_on", "note", "checkpoint"}
        if unknown:
            raise PlanError(
                f"Task {data.get('id')!r} carries unknown keys {sorted(unknown)}. A key "
                "this build does not understand is one it would silently drop."
            )
        raw_state = str(data.get("state") or TaskState.PENDING.value)
        try:
            state = TaskState(raw_state)
        except ValueError:
            allowed = ", ".join(s.value for s in TaskState)
            raise PlanError(
                f"Task {data.get('id')!r} is in state {raw_state!r}, which is not one this "
                f"build knows. Allowed: {allowed}."
            ) from None
        return cls(
            id=str(data["id"]),
            title=str(data["title"]),
            depends_on=tuple(str(item) for item in (data.get("depends_on") or ())),
            state=state,
            note=str(data.get("note") or ""),
            checkpoint=bool(data.get("checkpoint")),
        )


@dataclass(frozen=True)
class TaskGraph:
    """The plan, validated on construction and never invalid afterwards.

    Frozen, like `DesignSpec` and for the same reason: an edit is a copy, so a
    graph handed to three readers cannot be changed under any of them, and a
    refused move leaves the old graph intact rather than a half-applied one.
    """

    tasks: tuple[Task, ...] = ()

    def __post_init__(self) -> None:
        if len(self.tasks) > MAX_TASKS:
            raise PlanError(
                f"This plan has {len(self.tasks)} tasks and the limit is {MAX_TASKS}. A "
                "plan that long is a work breakdown structure, not a plan — break the "
                "work into stages and plan the one in front of you."
            )
        seen: set[str] = set()
        for task in self.tasks:
            if task.id in seen:
                raise PlanError(
                    f"Two tasks are both called {task.id!r}. Tasks refer to each other by "
                    "id, so a duplicate makes every reference to it a coin flip."
                )
            seen.add(task.id)
        for task in self.tasks:
            for dependency in task.depends_on:
                if dependency not in seen:
                    known = ", ".join(sorted(seen))
                    raise PlanError(
                        f"Task {task.id!r} depends on {dependency!r}, which is not in this "
                        f"plan. Tasks: {known}."
                    )
        self._refuse_cycles()

    # -- construction --------------------------------------------------------

    @classmethod
    def of(cls, tasks: Iterable[Task]) -> TaskGraph:
        return cls(tasks=tuple(tasks))

    def with_task(self, task: Task) -> TaskGraph:
        """A copy with one task replaced or appended."""
        if any(existing.id == task.id for existing in self.tasks):
            return TaskGraph.of(
                task if existing.id == task.id else existing for existing in self.tasks
            )
        return TaskGraph.of([*self.tasks, task])

    # -- reading -------------------------------------------------------------

    def __iter__(self) -> Iterator[Task]:
        return iter(self.tasks)

    def __len__(self) -> int:
        return len(self.tasks)

    def __bool__(self) -> bool:
        return bool(self.tasks)

    def task(self, task_id: str) -> Task:
        for candidate in self.tasks:
            if candidate.id == task_id:
                return candidate
        known = ", ".join(task.id for task in self.tasks) or "none"
        raise PlanError(f"No task called {task_id!r} in this plan. Tasks: {known}.")

    def order(self) -> tuple[Task, ...]:
        """A stable topological order: dependencies first, declaration order otherwise.

        Stable on purpose. A topological sort that broke ties arbitrarily would
        reorder two independent tasks between one call and the next, so a model
        reading "what is left" twice would see two different plans and conclude
        something had happened.
        """
        remaining = list(self.tasks)
        placed: set[str] = set()
        ordered: list[Task] = []
        while remaining:
            ready = [
                task
                for task in remaining
                if all(dependency in placed for dependency in task.depends_on)
            ]
            # Cycles are refused at construction, so this cannot empty. Kept as
            # a guard rather than an assert: a silent infinite loop here would
            # hang a request thread.
            if not ready:  # pragma: no cover - unreachable while __post_init__ holds
                raise PlanError("This plan has no order; it contains a cycle.")
            for task in ready:
                ordered.append(task)
                placed.add(task.id)
                remaining.remove(task)
        return tuple(ordered)

    def ready(self) -> tuple[Task, ...]:
        """Tasks that could be worked on now: unsettled, with everything they stand on settled.

        A `BLOCKED` task is not ready — it is waiting on something outside the
        plan, and reporting it as workable is how a model spends three steps
        retrying a call that will keep failing for the same reason.
        """
        settled = {task.id for task in self.tasks if task.state.settled}
        return tuple(
            task
            for task in self.order()
            if task.state in (TaskState.PENDING, TaskState.ACTIVE)
            and all(dependency in settled for dependency in task.depends_on)
        )

    def blocked_by(self, task_id: str) -> tuple[str, ...]:
        """Which of this task's dependencies are not settled, in plan order.

        Transitive on purpose: a task three steps behind an unfinished
        checkpoint is blocked by the checkpoint, and being told only about its
        immediate parent would send a reader up the chain one hop at a time.
        """
        unsettled: list[str] = []
        seen: set[str] = set()

        def walk(current: str) -> None:
            for dependency in self.task(current).depends_on:
                if dependency in seen:
                    continue
                seen.add(dependency)
                if not self.task(dependency).state.settled:
                    unsettled.append(dependency)
                    walk(dependency)

        walk(task_id)
        order = {task.id: index for index, task in enumerate(self.order())}
        return tuple(sorted(unsettled, key=lambda item: order.get(item, 0)))

    def checkpoints(self) -> tuple[Task, ...]:
        """Every checkpoint, settled or not — a reader wants both."""
        return tuple(task for task in self.order() if task.checkpoint)

    def open_checkpoints(self) -> tuple[Task, ...]:
        return tuple(task for task in self.checkpoints() if not task.state.settled)

    @property
    def complete(self) -> bool:
        return bool(self.tasks) and all(task.state.settled for task in self.tasks)

    # -- moves ---------------------------------------------------------------

    def advance(self, task_id: str, state: TaskState, *, note: str = "") -> TaskGraph:
        """Move one task, refusing the moves the order does not allow.

        The one rule this enforces: **a task cannot be settled while something
        it stands on is not.** Everything else is permitted, including going
        backwards — a task found to be wrong after the fact goes back to
        `PENDING`, and refusing that would make the plan a thing to work around
        rather than a thing to work in.

        Returns a new graph. The old one is untouched, so a caller that refuses
        the result is not left holding a half-applied plan.
        """
        task = self.task(task_id)
        if state.settled:
            outstanding = self.blocked_by(task_id)
            if outstanding:
                names = ", ".join(outstanding)
                raise PlanError(
                    f"{task_id!r} cannot be marked {state.value} while it is waiting on "
                    f"{names}. Finish those first, or mark them skipped if the design "
                    "went a different way."
                )
        return self.with_task(replace(task, state=state, note=note or task.note))

    # -- rendering -----------------------------------------------------------

    def brief(self) -> str:
        """A few lines for the state block, or nothing when there is no plan.

        Names what is ready rather than counting what is left, for the reason
        `Plan.brief` gives: a count is not actionable, and the point is that the
        model can see the specific next thing.
        """
        if not self.tasks:
            return ""
        lines = [f"The plan ({self.settled_count()} of {len(self.tasks)} settled):"]
        for task in self.order():
            marks = [task.state.value]
            if task.checkpoint and not task.state.settled:
                marks.append("needs sign-off")
            blocked = self.blocked_by(task.id)
            if blocked and not task.state.settled:
                marks.append("waiting on " + ", ".join(blocked))
            suffix = f" [{'; '.join(marks)}]"
            lines.append(f"  - {task.id}: {task.title}{suffix}")
            if task.note:
                lines.append(f"      note: {task.note}")
        nxt = self.ready()
        if nxt:
            lines.append(f"Next: {nxt[0].id}.")
        elif not self.complete:
            # Every unsettled task is blocked. Saying so beats an empty "next",
            # which reads as "nothing to do" on a plan that is stuck.
            lines.append(
                "Nothing is ready: everything left is blocked or waiting on a sign-off. "
                "Say what is needed rather than retrying."
            )
        return "\n".join(lines)

    def settled_count(self) -> int:
        return sum(1 for task in self.tasks if task.state.settled)

    # -- persistence ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "tasks": [task.to_dict() for task in self.order()],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> TaskGraph:
        """Load a stored plan. `None` and `{}` are an empty plan, not an error.

        A conversation that has never declared one is the ordinary case, and
        raising on it would make every reader write the same `if`.
        """
        if not data:
            return cls()
        version = data.get("format_version")
        if version != FORMAT_VERSION:
            raise PlanError(
                f"This plan is format version {version!r}; this build reads version "
                f"{FORMAT_VERSION}. Refusing to guess at the difference."
            )
        return cls.of(Task.from_dict(entry) for entry in (data.get("tasks") or ()))

    # -- internals -----------------------------------------------------------

    def _refuse_cycles(self) -> None:
        """Depth-first, reporting the cycle it found rather than that one exists.

        "There is a cycle" in a twenty-task plan is a message that sends
        somebody reading every edge. Naming the path is the difference between
        a minute and an afternoon.
        """
        by_id = {task.id: task for task in self.tasks}
        state: dict[str, int] = {}  # 0 unvisited, 1 on the stack, 2 done
        path: list[str] = []

        def visit(task_id: str) -> None:
            if state.get(task_id) == 2:
                return
            if state.get(task_id) == 1:
                cycle = path[path.index(task_id) :] + [task_id]
                raise PlanError(
                    "This plan has a cycle and therefore no order: "
                    + " -> ".join(cycle)
                    + ". Break one of those dependencies."
                )
            state[task_id] = 1
            path.append(task_id)
            for dependency in by_id[task_id].depends_on:
                visit(dependency)
            path.pop()
            state[task_id] = 2

        for task in self.tasks:
            visit(task.id)


def graph_from_tasks(entries: Sequence[Mapping[str, Any]]) -> TaskGraph:
    """Build a graph from the shape an agent tool receives.

    A named function rather than `TaskGraph.of(Task.from_dict(...))` inline at
    the call site, because the tool handler needs every failure to arrive as one
    `PlanError` carrying a sentence the model can act on — and the two-step
    version raises from two places with two vocabularies.
    """
    tasks: list[Task] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise PlanError(f"Task {index + 1} is not an object; each task needs an id and a title.")
        if "id" not in entry or "title" not in entry:
            raise PlanError(
                f"Task {index + 1} needs both an id and a title. The id is how other "
                "tasks depend on it; the title is what a person reads."
            )
        tasks.append(Task.from_dict(entry))
    return TaskGraph.of(tasks)


#: Exported so `state.py` and the tools agree on the empty case.
EMPTY: Final = TaskGraph()

__all__ = [
    "EMPTY",
    "FORMAT_VERSION",
    "MAX_TASKS",
    "PlanError",
    "Task",
    "TaskGraph",
    "TaskState",
    "graph_from_tasks",
]
