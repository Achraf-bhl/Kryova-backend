"""Admitting a solve by the memory it needs (ROAD_TO_10 6.3).

Two direct solves at once is how a box runs out of memory, and a box out of memory does not fail
one job: the operating system kills a process, and with CATIA on the same workstation it may be
the wrong one. So a solve that would not fit waits for one that frees memory, and one that can
never fit is refused in words *before* the machine pays for it.

**The estimate is measured, on one machine, and says so.** Peak resident memory of one in-house
solve on a tet10 box, each size in its own process (2026-10-05, Linux x86-64, CPython 3.12,
12 logical cores; the table and how it was taken are in `docs/MAKING_IT_FASTER.md`):

    direct (SuperLU)    4,131 DOF → +47 MB     12,675 → +257     28,611 → +692     54,243 → +1,912
    iterative (CG, Jacobi)  143,811 DOF → +1,465 MB    212,355 → +2,185

The direct path is **super-linear** — a fit through the two largest points gives ~DOF^1.5 and is
5 % over at 28 k and 16 % under at 12 k, so a constant floor is added — and it is the reason a
92 k-DOF solve (just under `ITERATIVE_THRESHOLD_DOF`) needs about 4.5 GB and did not fit in a
3.5 GB address-space cap at all. The iterative path is linear at about 10 KB per DOF.

    direct     = FLOOR_MB + 1.6e-4 · DOF^1.5
    iterative  = FLOOR_MB + 1.05e-2 · DOF

**What the estimate does not cover**, and what a caller must not read into it. It was measured on
tet10 boxes with the in-house solver; a tet4 mesh has fewer non-zeros per DOF and uses less, so it
is conservative there. **CalculiX's own memory is unmeasured** — it is a separate process with its
own solver — and the in-house figure is used for it as a stand-in, labelled as one. A mesh with a
different connectivity than a structured box (thin shells of elements, high aspect ratios) can
fill differently under SuperLU's ordering; the estimate is for planning an admission, never a bound.

**Admission is two constraints, and either can bind.** `baseline − reserved − reserve` protects
against a running job that has not yet allocated what it will (live `available` does not see it);
`available − reserve` protects against anything *else* on the machine growing (CATIA opening a big
assembly). The smaller wins. `baseline` is the available memory measured while this governor held
no reservation, so memory somebody else already holds stays theirs.

**What it cannot see.** It governs the threads of *one process*. A process pool (6.4) needs the
same decision made across processes, which this object does not do, and two server workers each
run their own governor — the figure that is shared is the live `available`, which is the second
constraint, so they still protect each other, but only reactively.

**Unknown memory is admitted and said to be.** Where the platform reports no available memory the
governor cannot judge, and refusing every job on such a machine would turn a missing reader into an
outage. The reservation carries `basis="unmeasured"` and the job's log line says so.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.core import hardware
from app.core.config import settings
from app.solve.linear_static import ITERATIVE_THRESHOLD_DOF
from app.solve.types import SolverError

logger = logging.getLogger(__name__)

#: Interpreter, numpy and the mesh arrays: what a solve costs before it factorises anything.
FLOOR_MB = 50

#: `peak = FLOOR_MB + coefficient · DOF^1.5` on the direct path. Measured; see the module docstring.
DIRECT_MB_PER_DOF_1_5 = 1.6e-4

#: `peak = FLOOR_MB + coefficient · DOF` on the iterative path. Measured; see the module docstring.
ITERATIVE_MB_PER_DOF = 1.05e-2

#: How often a waiting solve looks at the machine again. Releases wake it at once; this catches
#: *other* processes giving memory back, which nothing here is told about.
POLL_S = 1.0

#: Degrees of freedom per element, by element order, measured on structured boxes. tet10: 5.38 at
#: 768 elements, 4.66 at 6,144, 4.32 at 49,152 and 4.21 at 165,888, settling toward ~4.2 because a
#: tet10 mesh has about 1.4 nodes an element; 4.3 is used for a mesh too big to have been measured.
#: tet4: 0.67, 0.58 and 0.55 at 6,144, 49,152 and 165,888 elements; 0.6. A tet4 mesh of the same
#: element count is an eighth of the work, so a limit derived with one ratio over-refuses the other
#: by that factor, and over-refusal is not safe: the user's recovery is a coarser, wrong mesh.
DOF_PER_ELEMENT = {1: 0.6, 2: 4.3}

#: The most elements the product has ever accepted, and the ceiling of the derived limit: a
#: machine with more memory than this needs does not get a higher limit than the one the
#: verification work was done at.
DEFAULT_MAX_ELEMENTS = 400_000


class InsufficientMemory(SolverError):
    """A solve that cannot start: it needs more than the machine has, or waited too long."""


@dataclass(frozen=True)
class Estimate:
    peak_mb: int
    path: str  # "direct" or "iterative"
    basis: str  # what the number rests on, in a sentence


def estimate_peak_mb(degrees_of_freedom: int) -> Estimate:
    """About how much resident memory one in-house solve of this size will reach."""
    dof = max(int(degrees_of_freedom), 0)
    if dof > ITERATIVE_THRESHOLD_DOF:
        peak = FLOOR_MB + ITERATIVE_MB_PER_DOF * dof
        path = "iterative"
    else:
        peak = FLOOR_MB + DIRECT_MB_PER_DOF_1_5 * dof**1.5
        path = "direct"
    return Estimate(
        peak_mb=int(round(peak)),
        path=path,
        basis=(
            "measured on tet10 boxes with the in-house solver, 2026-10-05; "
            "CalculiX's own memory is unmeasured and this is a stand-in for it"
        ),
    )


@dataclass(frozen=True)
class Reservation:
    mb: int
    #: "measured" when the machine's free memory was read; "unmeasured" when it could not be.
    basis: str
    waited_s: float


def reserve_mb() -> int:
    """Memory always left for everything else: `MEMORY_RESERVE_MB`, or a tenth of the machine's."""
    if settings.memory_reserve_mb is not None:
        return settings.memory_reserve_mb
    total = hardware.hardware().total_ram_mb
    return max(1024, total // 10) if total else 1024


class MemoryGovernor:
    """Decides whether a solve may start now, must wait, or can never fit."""

    def __init__(
        self,
        *,
        total_mb: Callable[[], int | None] | None = None,
        available_mb: Callable[[], int | None] | None = None,
        reserve: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._total = total_mb or (lambda: hardware.hardware().total_ram_mb)
        self._available = available_mb or (lambda: hardware.available_ram_mb())
        self._reserve = reserve or reserve_mb
        self._clock = clock
        self._cond = threading.Condition()
        self._reserved = 0
        self._baseline: int | None = None

    @property
    def reserved_mb(self) -> int:
        with self._cond:
            return self._reserved

    def _free(self) -> tuple[int | None, str]:
        """Memory a new solve may take now, or None when the machine will not say."""
        available = self._available()
        if available is None:
            return None, "unmeasured"
        if self._reserved == 0 or self._baseline is None:
            self._baseline = available
        reserve = self._reserve()
        return min(self._baseline - self._reserved - reserve, available - reserve), "measured"

    @contextmanager
    def admit(
        self,
        mb: int,
        *,
        label: str = "solve",
        wait_s: float | None = None,
        on_wait: Callable[[int], None] | None = None,
    ) -> Iterator[Reservation]:
        """Hold `mb` for the block, or raise `InsufficientMemory` saying why not.

        `on_wait(free_mb)` is called once, outside the governor's lock, the first time the solve
        has to wait, so a caller can say so where a person is looking. A callback that raises
        is logged and ignored: telling someone it is waiting must never be why it did not run.
        """
        limit = settings.memory_wait_s if wait_s is None else wait_s
        reservation = self._acquire(int(mb), label, limit, on_wait)
        try:
            yield reservation
        finally:
            with self._cond:
                self._reserved = max(self._reserved - reservation.mb, 0)
                self._cond.notify_all()

    def _acquire(
        self, mb: int, label: str, wait_s: float, on_wait: Callable[[int], None] | None
    ) -> Reservation:
        started = self._clock()
        with self._cond:
            total = self._total()
            ceiling = None if total is None else total - self._reserve()
            if ceiling is not None and mb > ceiling:
                raise InsufficientMemory(
                    f"This run needs about {mb:,} MB and this machine has {total:,} MB in all, "
                    f"{self._reserve():,} MB of which is kept for everything else, so it cannot "
                    "fit however long it waits. Increase element_size_mm to coarsen the mesh, "
                    "or run it on a machine with more memory."
                )
        announced = False
        while True:
            announce_free: int | None = None
            with self._cond:
                free, basis = self._free()
                if free is None or mb <= free:
                    self._reserved += mb
                    waited = self._clock() - started
                    if waited > 0.5:
                        logger.info("%s waited %.1f s for %d MB", label, waited, mb)
                    return Reservation(mb=mb, basis=basis, waited_s=waited)
                remaining = wait_s - (self._clock() - started)
                if remaining <= 0:
                    raise InsufficientMemory(
                        f"This run needs about {mb:,} MB; {max(free, 0):,} MB is free and "
                        f"{self._reserved:,} MB is held by runs already going, and it waited "
                        f"{wait_s:.0f} s. Try again when they finish, or increase "
                        "element_size_mm to coarsen the mesh."
                    )
                if announced:
                    self._cond.wait(timeout=min(POLL_S, remaining))
                else:
                    announced = True
                    announce_free = max(free, 0)
            if announce_free is not None and on_wait is not None:
                try:
                    on_wait(announce_free)
                except Exception:  # noqa: BLE001 - a status line must not stop the run
                    logger.exception("%s: the waiting notice failed", label)


GOVERNOR = MemoryGovernor()


def admit_solve(
    degrees_of_freedom: int,
    *,
    label: str = "solve",
    on_wait: Callable[[int], None] | None = None,
):
    """`GOVERNOR.admit` for a solve of this size, with the estimate worked out."""
    estimate = estimate_peak_mb(degrees_of_freedom)
    logger.info(
        "%s: %s DOF, about %s MB on the %s path",
        label,
        f"{degrees_of_freedom:,}",
        f"{estimate.peak_mb:,}",
        estimate.path,
    )
    return GOVERNOR.admit(estimate.peak_mb, label=label, on_wait=on_wait)


def max_dof_for(budget_mb: float) -> int:
    """The most degrees of freedom whose estimated peak fits in `budget_mb`, 0 if none does.

    The estimate is *not* monotonic across `ITERATIVE_THRESHOLD_DOF`: the direct path at the
    threshold needs ~5 GB and the iterative path just above it ~1.1 GB, because the iterative
    method is the cheaper one. So the answer is the iterative figure where that clears the
    threshold, and otherwise the direct one below it.
    """
    spare = budget_mb - FLOOR_MB
    if spare <= 0:
        return 0
    iterative = int(spare / ITERATIVE_MB_PER_DOF)
    if iterative > ITERATIVE_THRESHOLD_DOF:
        return iterative
    # No clamp to the threshold here: the iterative branch above takes every budget that would
    # reach it, and the largest budget left for this line (~1,100 MB) is ~35,000 DOF on the direct
    # path -- `test_the_direct_branch_never_reaches_the_threshold` sweeps that.
    return int((spare / DIRECT_MB_PER_DOF_1_5) ** (2.0 / 3.0))


def derive_element_limit(
    total_mb: int | None, reserve: int, element_order: int = 2
) -> tuple[int, str]:
    """`(limit, basis)`: the most elements one solve may have on a machine of `total_mb`.

    Never above `DEFAULT_MAX_ELEMENTS`, which is the size the product was verified at. Where the
    machine will not say how much memory it has, the default stands and the basis says it was
    not derived.
    """
    if total_mb is None:
        return DEFAULT_MAX_ELEMENTS, "the machine's memory is unknown, so the default stands"
    budget = total_mb - reserve
    elements = int(max_dof_for(budget) / DOF_PER_ELEMENT.get(element_order, DOF_PER_ELEMENT[2]))
    limit = max(1, min(DEFAULT_MAX_ELEMENTS, elements))
    return limit, (
        f"derived: {total_mb:,} MB less {reserve:,} MB kept for everything else leaves "
        f"{max(budget, 0):,} MB, which one order-{element_order} solve of about {limit:,} "
        "elements needs"
    )


def default_element_limit() -> int:
    """The limit for an analysis the memory estimate does not cover: the setting, else 400,000."""
    return settings.max_elements if settings.max_elements is not None else DEFAULT_MAX_ELEMENTS


def element_limit(element_order: int = 2) -> int:
    """The mesh size above which a structural run is refused: `MAX_ELEMENTS` if it was set,
    else what this machine's memory supports for that element order."""
    if settings.max_elements is not None:
        return settings.max_elements
    return derive_element_limit(hardware.hardware().total_ram_mb, reserve_mb(), element_order)[0]


def element_limit_basis(element_order: int = 2) -> str:
    if settings.max_elements is not None:
        return "set explicitly (MAX_ELEMENTS)"
    return derive_element_limit(hardware.hardware().total_ram_mb, reserve_mb(), element_order)[1]


__all__ = [
    "DEFAULT_MAX_ELEMENTS",
    "DOF_PER_ELEMENT",
    "GOVERNOR",
    "Estimate",
    "InsufficientMemory",
    "MemoryGovernor",
    "Reservation",
    "admit_solve",
    "default_element_limit",
    "derive_element_limit",
    "element_limit",
    "element_limit_basis",
    "estimate_peak_mb",
    "max_dof_for",
    "reserve_mb",
]
