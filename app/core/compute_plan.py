"""Worker and thread counts derived from the machine, and what overrides them (ROAD_TO_10 6.2).

`job_workers = 2` was typed, not derived. On a four-core laptop it is two solves each wanting four
BLAS threads; on a sixty-four-core server it is two solves on a machine that could run twenty.
Neither is a decision anybody made about *this* machine, so this derives the figure from
`hardware()` and says in words where each number came from.

**The rule, and it is a policy rather than a measurement.** MAKING_IT_FASTER §2.3: requested
`job_workers × threads per job` must not exceed the cores that are actually there, or every solve
runs slower than it would alone and the machine thrashes. So:

    budget  = physical cores − reserve
    workers = clamp(budget // MIN_THREADS_PER_JOB, 1, MAX_DERIVED_WORKERS)
    threads = max(1, budget // workers)

`MIN_THREADS_PER_JOB` and `MAX_DERIVED_WORKERS` are chosen, not measured: a direct sparse solve
stops scaling long before a dozen threads, so thinner jobs side by side beat one fat one up to a
point, and gmsh's global lock serialises meshing so more than a handful of workers only queue at
it. No timing here shows these are the best values; they are stated so a measurement on a real
workstation has something to move.

**The reserve is for the rest of the machine.** CATIA shares this workstation and a solve that
takes every core freezes the model the engineer is looking at. One core on a small machine, two
on a large one, none on a dual-core where there is nothing to spare.

**Physical cores, and an assumption said out loud.** Where the platform does not report them the
plan assumes two logical cores per physical one *and says it assumed* (`physical_assumed`), because
planning by the logical count would double the budget on every hyperthreaded machine.

**Explicit settings always win.** `JOB_WORKERS` set in the environment is used as given, even if it
oversubscribes: the operator may know something this does not (a queue that is mostly meshing, a
machine that is dedicated). The plan then reports `explicit`, and `oversubscribed` if the product
exceeds the budget, so the log says it rather than the thrashing.

**What it cannot do.** BLAS threads inside *this* process are fixed when numpy loads and cannot be
changed per worker thread afterwards, so `solver_threads` is applied where it can be: the
CalculiX child's `OMP_NUM_THREADS` (`app/solve/calculix/run.py`) and any child process this
application starts. Per-worker BLAS pinning for in-process solves needs processes (6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core import hardware as _hardware
from app.core.hardware import Hardware

#: A job thinner than this does not use its cores any better than a fatter one next to it.
MIN_THREADS_PER_JOB = 3

#: Meshing serialises on gmsh's lock, so workers beyond a handful only queue at it.
MAX_DERIVED_WORKERS = 4

#: Logical cores per physical one, assumed only when the platform does not report physical cores.
ASSUMED_SMT = 2


@dataclass(frozen=True)
class ComputePlan:
    job_workers: int
    #: Threads per job, for the CalculiX child and for any child process.
    solver_threads: int
    reserved_cores: int
    physical_cores: int
    #: True when `physical_cores` is the logical count halved rather than a reading.
    physical_assumed: bool
    #: Where each figure came from: `job_workers` and `solver_threads` map to a sentence.
    basis: dict[str, str]
    #: `job_workers × solver_threads` exceeds the budget. Only an explicit setting can do this.
    oversubscribed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "job_workers": self.job_workers,
            "solver_threads": self.solver_threads,
            "reserved_cores": self.reserved_cores,
            "physical_cores": self.physical_cores,
            "physical_assumed": self.physical_assumed,
            "basis": dict(self.basis),
            "oversubscribed": self.oversubscribed,
        }


def reserve_for(physical: int) -> int:
    """Cores left for CATIA and the operating system."""
    if physical <= 2:
        return 0
    return 1 if physical <= 8 else 2


def derive(
    hardware: Hardware,
    *,
    explicit_workers: int | None = None,
    explicit_threads: int | None = None,
) -> ComputePlan:
    """The plan for this machine. `explicit_*` are settings the operator gave."""
    assumed = hardware.physical_cores is None
    physical = (
        hardware.physical_cores
        if hardware.physical_cores is not None
        else max(1, hardware.logical_cores // ASSUMED_SMT)
    )
    reserved = reserve_for(physical)
    budget = max(physical - reserved, 1)

    basis: dict[str, str] = {}
    derived_workers = max(1, min(MAX_DERIVED_WORKERS, budget // MIN_THREADS_PER_JOB))

    if explicit_workers is not None and explicit_workers >= 1:
        workers = explicit_workers
        basis["job_workers"] = "set explicitly (JOB_WORKERS)"
    else:
        workers = derived_workers
        how = "assumed from the logical count" if assumed else "read"
        basis["job_workers"] = (
            f"derived: {physical} physical cores ({how}) less {reserved} reserved leaves a "
            f"budget of {budget}, at least {MIN_THREADS_PER_JOB} threads each"
        )

    if explicit_threads is not None and explicit_threads >= 1:
        threads = explicit_threads
        basis["solver_threads"] = "set explicitly"
    else:
        threads = max(1, budget // workers)
        basis["solver_threads"] = f"derived: the budget of {budget} shared by {workers} worker(s)"

    return ComputePlan(
        job_workers=workers,
        solver_threads=threads,
        reserved_cores=reserved,
        physical_cores=physical,
        physical_assumed=assumed,
        basis=basis,
        oversubscribed=workers * threads > budget,
    )


@lru_cache(maxsize=1)
def current() -> ComputePlan:
    """The plan this process runs under: this machine, and whatever the settings pin.

    Read once, because the queue is built once and a worker count that moved under a running
    pool would describe a pool that no longer exists. `reset()` is for tests and for a settings
    change that must be seen without a restart.
    """
    from app.core.config import settings

    return derive(
        _hardware.hardware(),
        explicit_workers=settings.job_workers,
        explicit_threads=settings.solver_threads,
    )


def reset() -> None:
    current.cache_clear()


__all__ = [
    "ASSUMED_SMT",
    "MAX_DERIVED_WORKERS",
    "MIN_THREADS_PER_JOB",
    "ComputePlan",
    "current",
    "derive",
    "reserve_for",
    "reset",
]
