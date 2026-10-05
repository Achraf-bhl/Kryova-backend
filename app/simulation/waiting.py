"""Waiting for a slot instead of being refused (ROAD_TO_10 3.4).

A user may hold `max_concurrent_simulations_per_user` runs at once -- queued or running --
so that one account cannot occupy every worker. The run past that ceiling used to be
refused with a 429, and a person who had asked for four analyses was told to come back
later and ask again. Now it **waits**: it is accepted, shown with its position, and started
when one of that user's runs finishes.

**Three outcomes, decided in one place** (`admit`): a free slot starts the run; no free
slot but room in the waiting line makes it `WAITING`; and a full line is refused, naming
both numbers. `max_waiting_simulations_per_user` is the line's length and `0` turns waiting
off, which is exactly the old behaviour -- a deployment that wants the refusal back sets one
number.

**`WAITING` is not `QUEUED`**, and the difference is who is being waited for. `QUEUED`
means handed to the job queue and waiting for a worker thread -- the fleet's wait, which
the autoscaler reads. `WAITING` means held back by this user's own ceiling and not handed
to anything. A waiting run costs nothing: no thread, no memory, no meshing.

**A slot is handed on when a run ends**, by the same code that ran it (`start`): after the
runner returns -- succeeded, failed or cancelled -- the owner's oldest waiting runs are
promoted into whatever slots are free. A run's end is the only thing that frees a slot, so
it is the only moment a promotion is owed; a server restart is the other (`resume`), because
the waiting runs were never handed to the queue that just died and nothing else would
ever notice them.

**Two workers must not promote one run twice, and must not both fill the same slot.** The
state change is a conditional `UPDATE ... WHERE status = 'waiting'` and only the caller whose
update matched a row submits it, and the count-then-admit is serialised per owner with a
transaction-scoped advisory lock (`SET LOCAL`'s sibling: it dies at COMMIT, so it is safe
behind a transaction-pooling PgBouncer). On a database without advisory locks it is a no-op,
and the cost is the old one -- two simultaneous requests can each see a free slot.

**The order is first in, first out per owner**, across all of their projects, and strict:
a run whose organisation's limit blocks it holds the ones behind it. A person with work in two
organisations on different plans is rare, and "the second one jumped the queue" is a worse
thing to explain than "the second one waited".
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.core import limits
from app.jobs.queue import JobQueue, RemoteCall, Work
from app.media import LocalMediaStore
from app.models import SLOT_HOLDERS, JobStatus, Project, SimulationJob
from app.simulation.runner import SessionScope, run_simulation

logger = logging.getLogger(__name__)

SLOT_LIMIT = "max_concurrent_simulations_per_user"
WAIT_LIMIT = "max_waiting_simulations_per_user"


class Refused(Exception):
    """The run can neither start nor wait. The message is what the user is told."""


@dataclass(frozen=True, slots=True)
class Admission:
    """What `admit` decided for one new run."""

    #: `QUEUED` -- hand it to the queue now -- or `WAITING` -- store it and leave it.
    status: JobStatus
    #: 1-based place in the owner's waiting line; None when it starts now.
    position: int | None
    slot_limit: int
    wait_limit: int


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


def lock_owner(db: Session, owner_id: str) -> None:
    """Serialise this owner's admissions and promotions until the transaction ends.

    Postgres only; elsewhere there is nothing to take and the caller keeps the old
    check-then-insert window. `hashtext` folds the id into the lock's integer key, and two
    owners colliding on it merely wait for one another briefly.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"simulations:{owner_id}"}
    )


def slots_held(db: Session, owner_id: str) -> int:
    """How many of this owner's runs are counted against the ceiling right now."""
    return int(
        db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .join(Project, Project.id == SimulationJob.project_id)
            .where(Project.owner_id == owner_id, SimulationJob.status.in_(SLOT_HOLDERS))
        )
        or 0
    )


def waiting_count(db: Session, owner_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(SimulationJob)
            .join(Project, Project.id == SimulationJob.project_id)
            .where(Project.owner_id == owner_id, SimulationJob.status == JobStatus.WAITING)
        )
        or 0
    )


def admit(db: Session, owner_id: str, organisation_id: str) -> Admission:
    """Decide whether a new run starts, waits, or is refused.

    Takes the owner's lock, which the caller's commit releases: call it in the transaction
    that inserts the job, or two requests can each be told the last slot is theirs.
    """
    lock_owner(db, owner_id)
    slot_limit = limits.for_organisation(db, organisation_id, SLOT_LIMIT).value
    wait_limit = limits.for_organisation(db, organisation_id, WAIT_LIMIT).value
    held = slots_held(db, owner_id)
    if held < slot_limit:
        return Admission(JobStatus.QUEUED, None, slot_limit, wait_limit)

    waiting = waiting_count(db, owner_id)
    if waiting < wait_limit:
        return Admission(JobStatus.WAITING, waiting + 1, slot_limit, wait_limit)

    raise Refused(_refusal(held, slot_limit, waiting, wait_limit))


def _refusal(held: int, slot_limit: int, waiting: int, wait_limit: int) -> str:
    if wait_limit == 0:
        return (
            f"You already have {held} simulation(s) queued or running, which is the "
            f"limit of {slot_limit}. Wait for one to finish, or delete a queued run, "
            "before starting another."
        )
    return (
        f"You already have {held} simulation(s) queued or running, which is the limit of "
        f"{slot_limit}, and {waiting} more waiting for a slot, which is the limit of "
        f"{wait_limit}. Wait for one to finish, or delete a waiting run, before starting "
        "another."
    )


# ---------------------------------------------------------------------------
# Where a waiting run stands
# ---------------------------------------------------------------------------


def positions(db: Session, owner_id: str, job_ids: Iterable[str]) -> dict[str, int]:
    """The 1-based place of each given run in its owner's waiting line, in one query.

    A run not waiting is absent. The rank is taken over *all* of the owner's waiting runs,
    not over the ids asked for, because a page of one project's runs says nothing about the
    other projects' runs ahead of them.
    """
    ids = list(job_ids)
    if not ids:
        return {}
    ranked = (
        select(
            SimulationJob.id.label("id"),
            func.row_number()
            .over(order_by=(SimulationJob.created_at, SimulationJob.id))
            .label("position"),
        )
        .join(Project, Project.id == SimulationJob.project_id)
        .where(Project.owner_id == owner_id, SimulationJob.status == JobStatus.WAITING)
        .subquery()
    )
    rows = db.execute(select(ranked.c.id, ranked.c.position).where(ranked.c.id.in_(ids)))
    return {row.id: int(row.position) for row in rows}


def annotate(db: Session, owner_id: str, jobs: Iterable[SimulationJob]) -> None:
    """Set `queue_position` on each waiting job, and clear it on the rest."""
    jobs = list(jobs)
    found = positions(db, owner_id, [job.id for job in jobs if job.status is JobStatus.WAITING])
    for job in jobs:
        job.queue_position = found.get(job.id)


def annotate_one(db: Session, job: SimulationJob) -> SimulationJob:
    """`annotate` for a job whose owner is not already in hand (the project's owner)."""
    if job.status is JobStatus.WAITING:
        owner_id = db.scalar(select(Project.owner_id).where(Project.id == job.project_id))
        if owner_id is not None:
            annotate(db, owner_id, [job])
            return job
    job.queue_position = None
    return job


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


def promote(db: Session, owner_id: str) -> list[str]:
    """Move this owner's oldest waiting runs into the free slots. Returns the ids moved.

    The caller commits and *then* submits them: a worker looks a job up by id in its own
    session and would find nothing inside an open transaction.
    """
    lock_owner(db, owner_id)
    held = slots_held(db, owner_id)
    waiting = db.execute(
        select(SimulationJob.id, Project.organisation_id)
        .join(Project, Project.id == SimulationJob.project_id)
        .where(Project.owner_id == owner_id, SimulationJob.status == JobStatus.WAITING)
        .order_by(SimulationJob.created_at, SimulationJob.id)
    ).all()

    promoted: list[str] = []
    for job_id, organisation_id in waiting:
        if held >= limits.for_organisation(db, organisation_id, SLOT_LIMIT).value:
            break  # strict first-in-first-out: a blocked head holds the line
        moved = db.execute(
            update(SimulationJob)
            .where(SimulationJob.id == job_id, SimulationJob.status == JobStatus.WAITING)
            .values(status=JobStatus.QUEUED)
        )
        if moved.rowcount == 1:  # type: ignore[attr-defined]
            promoted.append(job_id)
            held += 1
    return promoted


def hand_on(
    queue: JobQueue, session_scope: SessionScope, store: LocalMediaStore, owner_id: str
) -> None:
    """Give a finished run's slot to its owner's next waiting runs."""
    with session_scope() as db:
        promoted = promote(db, owner_id)
        db.commit()
    for job_id in promoted:
        logger.info("Simulation %s left the waiting line and is queued", job_id)
        start(queue, job_id, session_scope, store)


def start(
    queue: JobQueue, job_id: str, session_scope: SessionScope, store: LocalMediaStore
) -> None:
    """Hand one `QUEUED` job to the queue, and when it ends pass its slot on.

    Every path that starts a run goes through here -- the HTTP route, the agent's tools, the
    operator's retry and a promotion -- so the hand-on cannot be forgotten by one of them,
    which would strand that user's waiting runs until the next restart.
    """
    queue.submit(
        Work(
            lambda: _run_then_hand_on(queue, job_id, session_scope, store),
            # A queue with processes runs this in a child that rebuilds its own session and
            # store (`app/simulation/worker.py`), and the hand-on and a crash record happen back
            # here. A queue without them calls the closure above and never looks at these.
            remote=RemoteCall("app.simulation.worker:run_in_child", (job_id,)),
            after=lambda: _hand_on_after(queue, job_id, session_scope, store),
            on_crash=lambda reason: _record_crash(job_id, session_scope, reason),
        )
    )


def _record_crash(job_id: str, session_scope: SessionScope, reason: str) -> None:
    """A run whose worker process vanished is failed with that said, not left `RUNNING`.

    Only a run still `QUEUED` or `RUNNING` is touched: a child that finished and wrote its own
    outcome before dying (or whose exit raced the result) keeps what it wrote.
    """
    with session_scope() as db:
        job = db.get(SimulationJob, job_id)
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.RUNNING):
            return
        job.status = JobStatus.FAILED
        job.error = (
            "The worker process running this simulation ended unexpectedly "
            f"({reason}). It was most likely stopped for using too much memory. "
            "Increase element_size_mm to coarsen the mesh, or run it again."
        )
        db.commit()


def _run_then_hand_on(
    queue: JobQueue, job_id: str, session_scope: SessionScope, store: LocalMediaStore
) -> None:
    try:
        run_simulation(job_id, session_scope, store)
    finally:
        _hand_on_after(queue, job_id, session_scope, store)


def _hand_on_after(
    queue: JobQueue, job_id: str, session_scope: SessionScope, store: LocalMediaStore
) -> None:
    """Never raises: a failure here must not replace the run's own outcome, and a run that
    did finish has already recorded it. What it risks is a stranded waiting run, which
    `resume` finds at the next start and the log names now."""
    try:
        with session_scope() as db:
            owner_id = db.scalar(
                select(Project.owner_id)
                .join(SimulationJob, SimulationJob.project_id == Project.id)
                .where(SimulationJob.id == job_id)
            )
        if owner_id is not None:
            hand_on(queue, session_scope, store, owner_id)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.exception("Could not hand a finished simulation's slot to the next waiting run")


def resume(queue: JobQueue, session_scope: SessionScope, store: LocalMediaStore) -> int:
    """After a restart, start whatever the free slots can hold. Returns how many owners were served.

    The runs that were `QUEUED` or `RUNNING` when the process died have been failed
    (`main._fail_orphaned_jobs`), which freed their owners' slots. The `WAITING` ones were
    never handed to the dead queue, so they are intact -- and with nothing running there is no
    run whose end would promote them.
    """
    with session_scope() as db:
        owners = list(
            db.scalars(
                select(Project.owner_id)
                .join(SimulationJob, SimulationJob.project_id == Project.id)
                .where(SimulationJob.status == JobStatus.WAITING)
                .distinct()
            )
        )
    for owner_id in owners:
        hand_on(queue, session_scope, store, owner_id)
    return len(owners)


__all__ = [
    "Admission",
    "Refused",
    "admit",
    "annotate",
    "annotate_one",
    "hand_on",
    "positions",
    "promote",
    "resume",
    "slots_held",
    "start",
    "waiting_count",
]
