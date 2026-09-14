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

**Two tables, one per ABC.** `build_solver` selects a `Solver`;
`build_conduction_solver` selects a `ConductionSolver`. They are separate because
the ABCs are separate, and merging them would push a union-typed return into
every caller — the same branch the four ABCs in `base.py` exist to keep out of
callers. `solver_version` serves both, because a version is a fact about the
*backend* (`internal` = this codebase, `calculix` = that binary) and not about
which analysis asked. The reasoning in full is on `_CONDUCTION_FACTORIES`.

**The import is lazy, and that is load-bearing.** `app.solve` is imported by the
API, the job layer and the AI layer; `app.solve.calculix` pulls in a subprocess
boundary, a deck writer and an `.frd` parser that most of those have no use for.
The factory imports it when it is asked for and not before, which a test asserts
against `sys.modules` — otherwise the cost creeps back the first time somebody
adds a convenience re-export.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from typing import Final

from app.solve.base import ConductionSolver, Solver, TransientConductionSolver
from app.solve.types import SolverError

#: The in-house linear-static solver. The default, so an existing deployment is
#: unchanged by this module existing.
INTERNAL: Final = "internal"

#: CalculiX across a subprocess boundary (Decision 4: GPL, never linked).
CALCULIX: Final = "calculix"

#: OpenFOAM, also across a process boundary. Not in any factory table — a flow run
#: has one engine and no setting that chooses it — but it is a backend a result
#: can be bound to, so `backend_of` names it.
OPENFOAM: Final = "openfoam"

#: Which backend each solver's own `name` belongs to. A row records the solver's
#: name (`linear-static`), a setting names a backend (`internal`), and a version is
#: a fact about the backend — so reading a version by the solver's name asked the
#: registry about a backend that does not exist, and **every in-house result was
#: recorded with no solver version at all** until 2026-09-14. A test builds every
#: solver every table can make and checks its name is here, so a new solver whose
#: name is missing fails the suite rather than going unversioned.
_BACKEND_OF: Final[Mapping[str, str]] = {
    "linear-static": INTERNAL,
    "plane": INTERNAL,
    "steady-conduction": INTERNAL,
    "transient-conduction": INTERNAL,
    "calculix": CALCULIX,
    "openfoam": OPENFOAM,
}


def backend_of(solver_name: str) -> str | None:
    """The backend a solver's own `name` belongs to, or None for one this build never made."""
    return _BACKEND_OF.get((solver_name or "").strip().lower())


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


def _internal_conduction(
    executable: str | os.PathLike[str] | None = None,
) -> ConductionSolver:
    # Lazy for the same reason `_calculix` is, one step weaker: `app.solve.
    # conduction` pulls in `linear_static`, `thermal` and `selection` for the
    # coupling helpers, and a caller that only ever asks for a static solve
    # should not pay for the thermal half of the package.
    from app.solve.conduction import SteadyConductionSolver

    return SteadyConductionSolver()


#: Conduction backends, in a table of their own.
#:
#: **This is a parallel lookup and not an entry in `_FACTORIES`, and that is the
#: decision rather than a shortcut.** `_FACTORIES` is typed `Callable[...,
#: Solver]` and `build_solver` returns `Solver`, because that is what its callers
#: hold: `simulation/runner.py` takes the result and calls `solve(mesh,
#: load_case)` on it. A `ConductionSolver` is not a `Solver` — deliberately, see
#: `base.ConductionSolver` — so putting one in that table means widening the
#: return type to `Solver | ConductionSolver`, and then **every** caller of
#: `build_solver` has to narrow it back before it can call anything. That is the
#: union-typed `solve()` the four ABCs exist to avoid, moved one level up into
#: the factory; the seam would still be there and nobody would be respecting it.
#:
#: The cost of two tables is one duplicated eight-line lookup. The cost of one
#: table is a branch in every caller and a runtime `isinstance` standing in for
#: a type the operator's setting was supposed to determine. Two tables.
#:
#: `solver_version` is deliberately **not** duplicated: it is keyed on the
#: backend, not on the analysis. `internal` means "this codebase" whichever
#: table selected it, and if CalculiX is federated for heat transfer later it
#: will be the same `ccx` binary reporting the same version.
_CONDUCTION_FACTORIES: Final[Mapping[str, Callable[..., ConductionSolver]]] = {
    INTERNAL: _internal_conduction,
}


def conduction_available() -> tuple[str, ...]:
    """Every conduction solver name this build knows, in a stable order."""
    return tuple(sorted(_CONDUCTION_FACTORIES))


def _internal_transient_conduction(
    executable: str | os.PathLike[str] | None = None,
) -> TransientConductionSolver:
    # Lazy for the same reason `_internal_conduction` is.
    from app.solve.conduction import BackwardEulerConductionSolver

    return BackwardEulerConductionSolver()


#: Transient conduction backends, a third table for the third `Conduction*`
#: ABC — the same reasoning `_CONDUCTION_FACTORIES`'s own comment gives for why
#: it is not folded into `_FACTORIES`: `TransientConductionSolver.solve` takes
#: a `TransientThermalCase` and returns a `TransientThermalField`, neither of
#: which a caller holding `Solver` or `ConductionSolver` back has, so merging
#: the tables would push a three-way union into `build_solver`'s return type
#: and into every caller that has to narrow it back out again.
_TRANSIENT_CONDUCTION_FACTORIES: Final[Mapping[str, Callable[..., TransientConductionSolver]]] = {
    INTERNAL: _internal_transient_conduction,
}


def transient_conduction_available() -> tuple[str, ...]:
    """Every transient conduction solver name this build knows, in a stable order."""
    return tuple(sorted(_TRANSIENT_CONDUCTION_FACTORIES))


def build_transient_conduction_solver(
    name: str, *, executable: str | os.PathLike[str] | None = None
) -> TransientConductionSolver:
    """The transient conduction solver called `name`, or a `SolverError` naming
    what exists. Same contract as `build_conduction_solver`: never automatic,
    never a `KeyError`, never a silent substitution.
    """
    key = (name or "").strip().lower()
    factory = _TRANSIENT_CONDUCTION_FACTORIES.get(key)
    if factory is None:
        known = ", ".join(transient_conduction_available())
        raise SolverError(
            f"No transient conduction solver called {name!r}. This build has: {known}. "
            "It is never chosen automatically, because a result computed by a solver "
            "nobody selected cannot be relied on."
        )
    return factory(executable)


def build_conduction_solver(
    name: str, *, executable: str | os.PathLike[str] | None = None
) -> ConductionSolver:
    """The conduction solver called `name`, or a `SolverError` naming what exists.

    Same contract as `build_solver` and the same refusal: never automatic, never
    a `KeyError`, never a silent substitution.

    **No setting names this yet, and saying so is part of the honesty.** There
    is a `SOLVER_BACKEND` and no `CONDUCTION_BACKEND`, so a deployment that had
    set `SOLVER_BACKEND=calculix` and then asked for a conduction solve would be
    refused here rather than quietly handed the in-house one. That refusal is
    the correct behaviour under Decision 3 — a result computed by a solver
    nobody chose cannot be relied on — and adding the separate setting is a
    change to `app/core/config.py`, which this lane does not own. It is worth
    making only when the second backend exists; one table with one entry does
    not need a dedicated environment variable to disambiguate it.
    """
    key = (name or "").strip().lower()
    factory = _CONDUCTION_FACTORIES.get(key)
    if factory is None:
        known = ", ".join(conduction_available())
        federated = (
            " CalculiX can solve steady conduction with a *HEAT TRANSFER step, but that "
            "step is not written and nothing is federated behind this seam yet, so asking "
            "for it is refused rather than answered by a different solver."
            if key == CALCULIX
            else ""
        )
        raise SolverError(
            f"No conduction solver called {name!r}. This build has: {known}.{federated} "
            "It is never chosen automatically, because a result computed by a solver "
            "nobody selected cannot be relied on."
        )
    return factory(executable)


#: Cached per executable **file** — path, size and modification time — because
#: reading it costs a subprocess. Keyed on the path alone until 2026-09-14, on the
#: stated ground that the answer "cannot change while the process runs"; it can: a
#: package upgrade replaces the binary at the same path under a running server,
#: and every result after it was then recorded against the old version. `None`
#: means "asked and could not tell", recorded as such rather than retried per job.
_VERSIONS: dict[str, str | None] = {}

#: The sha256 of a binary, cached on the same key for the same reason.
_DIGESTS: dict[str, str | None] = {}


def _file_key(path: os.PathLike[str] | str) -> str | None:
    """Path, size and mtime — what changes when a binary is replaced in place."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return f"{os.fspath(path)}\0{stat.st_size}\0{stat.st_mtime_ns}"

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

    cache_key = _file_key(binary)
    if cache_key is None:
        return None
    if cache_key in _VERSIONS:
        return _VERSIONS[cache_key]

    version: str | None = None
    try:
        completed = subprocess.run(
            [str(binary), "-v"], capture_output=True, text=True, timeout=30, check=False
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


def calculix_identity(executable: str | os.PathLike[str] | None = None) -> str | None:
    """The CalculiX that would answer, named by version **and bytes** — or None.

    What the job cache keys a CalculiX run on, read before the run. A version is a
    name, the way an image tag is: two builds of 2.20 against different sparse
    solvers both print "Version 2.20". The digest is the build. None when there is
    no binary, its version cannot be read, or its bytes cannot be — and the cache
    treats None as "do not reuse", because an engine nobody identified matching a
    known one is the claim Decision 3 forbids.
    """
    from app.solve.calculix.run import find_ccx

    binary = find_ccx(executable)
    if binary is None:
        return None
    version = solver_version(CALCULIX, binary)
    key = _file_key(binary)
    if version is None or key is None:
        return None
    if key not in _DIGESTS:
        digest = hashlib.sha256()
        try:
            with open(binary, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            _DIGESTS[key] = "sha256:" + digest.hexdigest()
        except OSError:
            _DIGESTS[key] = None
    found = _DIGESTS[key]
    return f"{CALCULIX} {version} {found}" if found else None


__all__ = [
    "CALCULIX",
    "INTERNAL",
    "OPENFOAM",
    "available",
    "backend_of",
    "build_conduction_solver",
    "build_solver",
    "calculix_identity",
    "conduction_available",
    "solver_version",
]
