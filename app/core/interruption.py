"""Stopping something that is already running (P5 task 6).

Two things in this product take long enough to be worth stopping: an agent turn
and a simulation job. They stop very differently, and the difference is worth
stating rather than smoothing over, because a "stop" button that quietly does
nothing is worse than no button — it teaches somebody that the product ignores
them at the moment they most want it to listen.

**An agent turn stops at a step boundary.** The loop is bounded and every step
is persisted as it happens, so ending between steps loses nothing: the work
already done stays in the transcript, and the next turn continues from it. A
tool call that has *begun* is allowed to finish. Half a CATIA operation is a
worse thing to own than four more seconds of waiting.

**A simulation stops before its next expensive stage.** A queued job never
starts. A running one is checked at the boundary between meshing and solving,
because those are the two stages, and each is a single call into gmsh or
CalculiX that this process cannot interrupt from the outside. So a solve that
has already been handed to CalculiX runs to completion and *is still billed* —
`app/simulation/runner.py` bills what really ran, and a cancel does not
retroactively make the machine time free. The API says this in words rather
than accepting the request and appearing to have stopped something.

**The signal is a database column, not an in-process flag.** Whoever is
streaming the turn and whoever pressed stop are almost never the same worker.
An in-memory registry works perfectly under `--workers 1` and silently does
nothing in production, which is the worst failure shape available for this
particular button.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, JobStatus, SimulationJob, User


@dataclass(frozen=True)
class Refusal:
    """Why a stop request could not be honoured, in words for the user."""

    reason: str


def request_turn_stop(
    db: Session, conversation: Conversation, *, by: User, now: datetime | None = None
) -> None:
    """Ask the turn streaming for `conversation` to end at its next boundary.

    Idempotent and never refuses. There is no reliable way to know from here
    whether a turn is in fact running — it is streaming in another process —
    and refusing on a stale belief that nothing is running would deny the
    request in exactly the case where it matters. A flag set with nothing to
    stop is cleared by the next turn that starts and costs nothing.
    """
    conversation.cancel_requested_at = now or datetime.now(timezone.utc)
    conversation.cancel_requested_by_id = by.id
    db.commit()


def clear_turn_stop(db: Session, conversation: Conversation) -> None:
    """Forget any pending stop. Called when a turn starts.

    Without this, one press of stop would end every subsequent turn instantly,
    each one looking to the user like the product refusing to work.

    The race it accepts: a stop that arrives between the request being made and
    the turn beginning is dropped. That window is milliseconds, and the button
    only exists on screen while a turn is streaming — by which time it has
    begun.
    """
    if conversation.cancel_requested_at is None and conversation.cancel_requested_by_id is None:
        return
    conversation.cancel_requested_at = None
    conversation.cancel_requested_by_id = None
    db.commit()


def turn_stop_requested(db: Session, conversation: Conversation) -> bool:
    """Whether somebody has asked this turn to stop, read afresh.

    **Goes to the database rather than reading the attribute**, because the
    attribute is whatever this session loaded, and the request was written by a
    different session in a different process. Reading `conversation
    .cancel_requested_at` directly here would produce a stop button that works
    in a single-process test and never in production — the exact failure this
    module's docstring is about.

    One primary-key read per step boundary, which is seconds apart.
    """
    return db.scalar(
        select(Conversation.cancel_requested_at).where(Conversation.id == conversation.id)
    ) is not None


#: What the user is told when a turn ends because they stopped it. Written here
#: rather than in the agent loop so that the stop path is the one exit that
#: costs no further model call: the user asked for this to end, and spending
#: another LLM call to write a farewell is the opposite of stopping.
TURN_STOPPED_MESSAGE = (
    "Stopped at your request. Everything up to here is kept — the steps above "
    "really ran, and the next message carries on from what is built."
)


def request_simulation_stop(
    db: Session, job: SimulationJob, *, by: User, now: datetime | None = None
) -> Refusal | None:
    """Cancel a simulation, or say why it cannot be cancelled.

    A **queued** job is cancelled here and now: nothing has started, so there is
    nothing to unwind, and the runner already refuses any job that is not
    `QUEUED` by the time it picks it up.

    A **running** job is marked, and the runner honours the mark at its next
    stage boundary. That is an honest partial: meshing and solving are each a
    single call this process cannot interrupt, so a solve already inside
    CalculiX finishes, and the machine time it used is billed. Saying so is the
    point — the alternative is a `202` that looks like it stopped something.
    """
    moment = now or datetime.now(timezone.utc)
    if job.status.is_terminal:
        return Refusal(
            f"This run already {job.status.value}, so there is nothing to stop."
        )

    job.cancel_requested_at = moment
    job.cancel_requested_by_id = by.id

    if job.status is JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = moment
        db.commit()
        return None

    # RUNNING. Marked, not stopped.
    db.commit()
    return None


def simulation_stop_requested(db: Session, job_id: str) -> bool:
    """Whether a running job has been asked to stop. Read afresh, as above."""
    return db.scalar(
        select(SimulationJob.cancel_requested_at).where(SimulationJob.id == job_id)
    ) is not None


class Cancelled(Exception):
    """Raised inside the runner when a stop has been requested.

    An exception rather than a return value because the check sits between
    stages of `_execute`, and every other way out of that function is already
    an exception. A `None` return would have to be threaded through three
    call sites that currently cannot produce one.
    """


__all__ = [
    "Cancelled",
    "Refusal",
    "TURN_STOPPED_MESSAGE",
    "clear_turn_stop",
    "request_simulation_stop",
    "request_turn_stop",
    "simulation_stop_requested",
    "turn_stop_requested",
]
