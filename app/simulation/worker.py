"""What a worker *process* runs (ROAD_TO_10 6.4).

`ProcessPoolJobQueue` cannot send a closure to a child, so a simulation is named by its id and
this function rebuilds what the closure held: a session scope and the media store, both from
settings, in the child's own interpreter. Nothing else crosses the boundary.

The imports are inside the function on purpose: the pool's initializer sets `OMP_NUM_THREADS`
before the first job runs, and a BLAS library reads it only when numpy loads, so numpy must not be
imported by this module's own import.
"""

from __future__ import annotations


def run_in_child(job_id: str) -> None:
    from app.api.deps import get_session_scope
    from app.media import get_media_store
    from app.simulation.runner import run_simulation

    run_simulation(job_id, get_session_scope(), get_media_store())
