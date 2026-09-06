"""Choosing a solver — master plan 6.1, the part that makes the rest reachable.

`app/solve/calculix/` has been able to solve a real model since 2026-09-06, and
**nothing in the product could ask it to.** `simulation/runner.py` constructed
`LinearStaticSolver()` directly and the route recorded the name of that class as
a constant. That is the same failure `KRYOVA_BUILD_PLAN.md` records against the
OCCT kernel on 2026-09-05 — an entire era of work green on capability and wired
to nothing — and it is worth naming as a pattern rather than fixing twice in
silence: a seam that no configuration reaches is a seam nobody is using.

**Never chosen automatically.** `geometry_backend` sets the precedent and states
the reason: a deployment that silently fell back would hand the user a part built
by a different kernel without saying so. The same applies with more force here,
because a *result* is what Decision 3 binds to its provenance. So there is no
"use CalculiX if it is installed" — the operator names the solver, and a named
solver that cannot run is an error rather than a quiet substitution.

**The import is lazy, and that is load-bearing.** `app.solve` is imported by the
API, the job layer and the AI layer; `app.solve.calculix` pulls in a subprocess
boundary, a deck writer and an `.frd` parser that most of those have no use for.
The factory imports it when it is asked for and not before, which a test asserts
against `sys.modules` — otherwise the cost creeps back the first time somebody
adds a convenience re-export.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Final

from app.solve.base import Solver
from app.solve.types import SolverError

#: The in-house linear-static solver. The default, so an existing deployment is
#: unchanged by this module existing.
INTERNAL: Final = "internal"

#: CalculiX across a subprocess boundary (Decision 4: GPL, never linked).
CALCULIX: Final = "calculix"


def _internal(executable: str | os.PathLike[str] | None = None) -> Solver:
    from app.solve.linear_static import LinearStaticSolver

    return LinearStaticSolver()


def _calculix(executable: str | os.PathLike[str] | None = None) -> Solver:
    # Imported here rather than at module scope: see the module docstring.
    from app.solve.calculix.solver import CalculiXSolver

    return CalculiXSolver(executable=executable or None)


_FACTORIES: Final[Mapping[str, Callable[..., Solver]]] = {
    INTERNAL: _internal,
    CALCULIX: _calculix,
}


def available() -> tuple[str, ...]:
    """Every solver name this build knows, in a stable order."""
    return tuple(sorted(_FACTORIES))


def build_solver(
    name: str, *, executable: str | os.PathLike[str] | None = None
) -> Solver:
    """The solver called `name`, or a `SolverError` naming the ones that exist.

    Never a `KeyError`: a misspelled setting is an operator mistake, and the
    useful answer to one is the list of things they might have meant.
    """
    key = (name or "").strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        known = ", ".join(available())
        raise SolverError(
            f"No solver called {name!r}. This build has: {known}. Set SOLVER_BACKEND "
            "to one of those — it is never chosen automatically, because a result "
            "computed by a solver nobody selected cannot be relied on."
        )
    return factory(executable)


#: Cached per executable path, because reading it costs a subprocess and the
#: answer cannot change while the process runs. `None` means "asked and could
#: not tell", which is recorded as such rather than retried on every job.
_VERSIONS: dict[str, str | None] = {}

#: `ccx -v` prints "This is Version 2.23" and exits **201** — 201 is its generic
#: "did not run a job" code, not a failure, so the return code is ignored here
#: and the text is what is read. Measured against ccx 2.23 on 2026-09-06.
_VERSION_RE: Final = re.compile(r"[Vv]ersion\s+([0-9][0-9.]*)")


def solver_version(name: str, executable: str | os.PathLike[str] | None = None) -> str | None:
    """What version of `name` is installed, or `None` when it cannot be read.

    Decision 3 binds every result to the solver version that produced it, so this
    is not decoration: a stress figure whose provenance says only "calculix" is a
    figure nobody can reproduce in two years' time. `None` is an honest answer and
    is stored as one — an unmeasured version must not be guessed at.
    """
    key = (name or "").strip().lower()
    if key == INTERNAL:
        # The in-house solver's version *is* the codebase's — there is no other
        # artefact to point at, and the commit is what makes a result
        # reproducible. Imported here rather than at module scope because
        # `app.main` imports half the application, and this module is imported
        # by the job layer.
        from app.main import APP_VERSION, GIT_SHA

        return f"{APP_VERSION}+{GIT_SHA}" if GIT_SHA else APP_VERSION
    if key != CALCULIX:
        return None

    from app.solve.calculix.run import find_ccx

    binary = find_ccx(executable)
    if binary is None:
        return None

    cache_key = str(binary)
    if cache_key in _VERSIONS:
        return _VERSIONS[cache_key]

    version: str | None = None
    try:
        completed = subprocess.run(
            [cache_key, "-v"], capture_output=True, text=True, timeout=30, check=False
        )
        match = _VERSION_RE.search((completed.stdout or "") + (completed.stderr or ""))
        version = match.group(1) if match else None
    except (OSError, subprocess.SubprocessError):
        # A version that cannot be read is recorded as unknown. It must never
        # stop a solve: knowing which version ran is valuable, and refusing to
        # run because we could not find out would be the tail wagging the dog.
        version = None

    _VERSIONS[cache_key] = version
    return version


__all__ = [
    "CALCULIX",
    "INTERNAL",
    "available",
    "build_solver",
    "solver_version",
]
