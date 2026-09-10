"""Where a running simulation has got to (P5 task 2).

The run view's last missing surface. A solve used to be one opaque call: the
status said `RUNNING` from the moment the job was picked up until the moment it
finished, whether that was eight seconds or twenty minutes, and a user watching
had no way to tell a mesh being built from a solver that had wedged.

**What this reports and what it deliberately does not.** It reports the *stage* —
meshing, solving, reading results, storing them — and, for a convergence study,
which grid of how many. Both are counted facts. It does **not** report a
percentage inside a stage, and that is the decision rather than an omission.
CalculiX writes a `.sta` file during a run, but for the linear-static workload
that ships it holds a single increment, so any "37%" derived from it would be
invented — and an invented progress bar over a twenty-minute solve is worse than
an honest spinner, because it teaches a user to predict a finish time from a
number nobody measured. A study genuinely does have countable progress, so a
study genuinely gets a count.

**Every write opens its own session, and that is load-bearing.** It is the mirror
image of `app.core.interruption`: there the *reader* had to escape the runner's
long transaction, here the *writer* does. The runner holds one transaction for
the whole job, so a progress update written into it would be invisible to the
API worker serving the results page until the job committed — which is to say,
until the run was over, which is precisely when progress stops being interesting.

A progress write must never fail a run. Losing a progress line costs a watcher a
few seconds of staleness; losing the run costs them the solve.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.models import SimulationJob

if TYPE_CHECKING:
    from app.simulation.runner import SessionScope

logger = logging.getLogger(__name__)


class Stage(StrEnum):
    """The stages a run passes through, in order.

    Named for what is happening rather than for the module doing it: a user
    watching should read "meshing", not "gmsh". The values are the wire form and
    the frontend labels them, so adding one is a frontend change too — which is
    the right amount of friction for a vocabulary the product speaks out loud.
    """

    MESHING = "meshing"
    SOLVING = "solving"
    READING = "reading"
    STORING = "storing"


#: Order, for a client that wants to show what is still to come. A list rather
#: than the enum's own order so that reordering is a deliberate edit.
STAGE_ORDER: tuple[Stage, ...] = (Stage.MESHING, Stage.SOLVING, Stage.READING, Stage.STORING)


def snapshot(
    stage: Stage,
    *,
    detail: str = "",
    index: int | None = None,
    total: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The stored shape. Built here so the runner and the tests agree on it.

    `index`/`total` are present together or not at all: half a count is a
    fraction with no denominator, and a client would have to guess what to
    render. A stage with no count is a stage whose length is genuinely unknown,
    and saying so is the honest rendering.
    """
    if (index is None) != (total is None):
        raise ValueError(
            "A progress count needs both an index and a total, or neither. "
            "A numerator with no denominator is not progress."
        )
    return {
        "stage": stage.value,
        "detail": detail,
        "index": index,
        "total": total,
        "at": (now or datetime.now(timezone.utc)).isoformat(),
    }


def report(
    session_scope: "SessionScope",
    job_id: str,
    stage: Stage,
    *,
    detail: str = "",
    index: int | None = None,
    total: int | None = None,
) -> None:
    """Record where this run has got to, in its own committed transaction.

    Swallows everything. See the module docstring: a progress line is worth a
    few seconds of a watcher's patience and is never worth a run.
    """
    try:
        payload = snapshot(stage, detail=detail, index=index, total=total)
        with session_scope() as db:
            job = db.get(SimulationJob, job_id)
            if job is None:
                return
            job.progress = payload
            db.commit()
    except Exception:  # noqa: BLE001 - progress must never fail a run
        logger.warning("Could not record progress for simulation %s", job_id, exc_info=True)


def describe(progress: dict[str, Any] | None) -> str:
    """One line for a person, from a stored snapshot.

    Lives here rather than on the client so the API and any log line say the
    same thing. Returns an empty string for a run that has not reported yet,
    which the caller renders as "starting" — never as stage zero of four, which
    would claim a stage had begun.
    """
    if not progress:
        return ""
    stage = str(progress.get("stage") or "")
    labels = {
        Stage.MESHING.value: "Building the mesh",
        Stage.SOLVING.value: "Solving",
        Stage.READING.value: "Reading the results",
        Stage.STORING.value: "Storing the field data",
    }
    line = labels.get(stage, stage or "Running")
    index, total = progress.get("index"), progress.get("total")
    if isinstance(index, int) and isinstance(total, int) and total > 1:
        line += f" — grid {index} of {total}"
    detail = str(progress.get("detail") or "")
    return f"{line} ({detail})" if detail else line
