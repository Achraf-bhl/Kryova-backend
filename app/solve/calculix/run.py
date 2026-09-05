"""Running `ccx` — master plan 6.1, the process boundary itself.

`deck.py` writes the question and `frd.py` reads the answer; this is the part in
between, and it is the part Decision 4 is actually about. CalculiX is GPL, so it
is invoked as **a separate process across a file/CLI boundary** — argv in, files
out, nothing linked. That is a licence obligation first. It is also why a solver
that segfaults on a bad element costs a subprocess rather than the API.

Six things here produce a wrong answer, or no answer, quietly.

**`ccx` is given a job name, never a filename.** `ccx -i job` reads `job.inp` and
writes `job.frd`, `job.dat`, `job.sta`, `job.cvg`. Handing it `job.inp` makes it
look for `job.inp.inp`, and the failure is a bare "cannot open" naming a file the
caller never mentioned. The name is fixed here rather than taken from the caller
for the same reason the directory is: both are ours.

**A run happens in its own directory and nothing else is written.** Every output
file is a sibling of the deck, named after the job, so a shared directory means
two concurrent solves overwrite each other's results — silently, because the
second one's `.frd` parses perfectly well. The directory is removed afterwards
unless `keep` names somewhere to put it, which exists for the case where the deck
that failed is the thing you need to look at.

**A non-zero exit is not how `ccx` reports most failures.** It prints `*ERROR`
and stops, and depending on build and version it may still exit 0 and may still
leave a `.frd` behind — a truncated one, or one from a previous step. So success
is decided by the *output*, not by the return code: the frd has to exist and be
non-empty, and the stdout has to carry no `*ERROR`. Both are checked, and the
diagnosis of what the error means is `diagnose.py`'s job rather than this one's.

**A solve that does not terminate must not hold a worker for ever.** There is a
timeout, it kills the process group, and it is deliberately generous — a real
static solve on a real part is minutes, and a timeout tuned to a unit test would
turn a slow answer into a wrong one. `SolverError` says how long it waited and
what to do, because "it timed out" alone leaves the caller with no next step.

**Threads are set explicitly or not at all.** CalculiX reads `OMP_NUM_THREADS`,
and its default when unset is build-dependent — on some builds every core, which
on a shared machine means one solve starves everything else. Passing `threads`
sets it for the child only; the parent's environment is never mutated, because a
process-wide `os.environ` write from inside a solver is exactly the kind of
action-at-a-distance the codebase's pooled-connection rule exists to prevent.

**Not installed is a different answer from failed to solve**, and the two must
not arrive as one exception. `CalculiXUnavailable` says the binary is missing and
how to get one; `SolverError` says the model could not be solved as posed. A
caller that cannot tell them apart reports a missing dependency as a bad mesh.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.solve.types import SolverError

#: The job name every run uses. Fixed, because the directory is ours and holds
#: exactly one job; and free of dots and spaces, both of which CalculiX's own
#: input reader treats as significant in a file name.
JOB_NAME = "kryova"

#: Executable names to look for, in order. `ccx` is the plain name; the versioned
#: ones are what the official Linux binaries and most distribution packages
#: install as, and a machine with two versions gets the newest by this ordering.
#: `.exe` is not listed: `shutil.which` applies `PATHEXT` on Windows itself, and
#: spelling it here would find `ccx.exe` and miss `ccx.bat` wrappers.
CCX_NAMES: tuple[str, ...] = (
    "ccx",
    "ccx_2.22",
    "ccx_2.21",
    "ccx_2.20",
    "ccx_2.19",
    "CalculiX",
)

#: Long enough that a real part is not cut off mid-factorisation. A static solve
#: of a few hundred thousand degrees of freedom is minutes on this class of
#: machine, and a timeout that fires during one turns a slow answer into a
#: reported failure — which is worse, because the caller then changes the model.
DEFAULT_TIMEOUT_S = 900.0

#: What the installation message points at. Kept here rather than inline so the
#: two places that raise it cannot drift.
_WHERE_TO_GET_IT = (
    "CalculiX is a separate program and is not bundled: install it (Linux: the "
    "'calculix-ccx' package; Windows: the bConverged or PrePoMax distribution, "
    "which ships ccx.exe) and either put it on PATH or pass its full path."
)


class CalculiXUnavailable(SolverError):
    """No `ccx` binary could be found.

    A subclass of `SolverError` so an existing `except SolverError` still catches
    it, and a distinct type so a caller that wants to fall back to the in-house
    solver can tell "not installed" from "your model is wrong". Reporting a
    missing dependency as a bad mesh sends the user to fix geometry that is fine.
    """


@dataclass
class CcxRun:
    """What one `ccx` invocation produced."""

    #: The `.frd` as text. Empty when the solver wrote none.
    frd: str
    #: Everything `ccx` said, stdout and stderr merged in the order it said it.
    #: Merged rather than kept apart because CalculiX writes its errors to stdout
    #: interleaved with its progress, and separating them loses which step failed.
    output: str
    returncode: int
    seconds: float
    #: Files the run left behind, by name. `.sta` and `.cvg` are the convergence
    #: record and are what a non-convergence diagnosis reads.
    artefacts: dict[str, str] = field(default_factory=dict)

    @property
    def wrote_results(self) -> bool:
        return bool(self.frd.strip())


def find_ccx(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Where `ccx` is, or `None`.

    An explicit path is checked for existence and returned as given — including
    when it does not exist, as `None`, so a caller that configured a wrong path
    is told the same way as one that configured none. Resolving a bad explicit
    path to something on PATH would be worse: the run would succeed with a solver
    the operator did not choose.
    """
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        found = shutil.which(str(explicit))
        return Path(found) if found else None

    for name in CCX_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def require_ccx(explicit: str | os.PathLike[str] | None = None) -> Path:
    """`find_ccx`, but raising the actionable message instead of returning None."""
    found = find_ccx(explicit)
    if found is None:
        tried = ", ".join(CCX_NAMES)
        asked = f" Configured path: {explicit!r}." if explicit else ""
        raise CalculiXUnavailable(
            f"No CalculiX executable found.{asked} Looked on PATH for: {tried}. "
            f"{_WHERE_TO_GET_IT}"
        )
    return found


def run_ccx(
    deck: str,
    *,
    executable: str | os.PathLike[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    threads: int | None = None,
    keep: str | os.PathLike[str] | None = None,
) -> CcxRun:
    """Write the deck, run the solver on it, and read back what it produced.

    Raises `CalculiXUnavailable` when there is no binary, and `SolverError` when
    the run timed out. **A solver that ran and refused the model is not an
    exception here** — it comes back as a `CcxRun` with its output and no
    results, because the caller needs the text to diagnose it and an exception
    would throw away everything but the message.
    """
    binary = require_ccx(executable)

    with tempfile.TemporaryDirectory(prefix="kryova-ccx-") as tmp:
        directory = Path(tmp)
        (directory / f"{JOB_NAME}.inp").write_text(deck, encoding="utf-8")

        environment = dict(os.environ)
        if threads is not None:
            # The child's copy only. A solver reaching into os.environ would
            # change the thread count of every other solve in the process.
            environment["OMP_NUM_THREADS"] = str(int(threads))

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [str(binary), "-i", JOB_NAME],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            raise SolverError(
                f"CalculiX did not finish within {timeout_s:g} s and was stopped. "
                "A static solve that runs this long is usually an over-refined "
                "mesh or a model with far more degrees of freedom than intended — "
                "coarsen the mesh, or raise the timeout if the size is deliberate."
            ) from expired
        seconds = time.perf_counter() - started

        # stderr after stdout rather than interleaved: the two are separate pipes
        # and their true interleaving is not recoverable, so inventing one would
        # be a guess presented as a record.
        output = (completed.stdout or "") + (completed.stderr or "")

        frd_path = directory / f"{JOB_NAME}.frd"
        frd = frd_path.read_text(encoding="utf-8", errors="replace") if frd_path.is_file() else ""

        artefacts = {
            path.suffix.lstrip("."): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix in {".sta", ".cvg", ".dat"}
        }

        if keep is not None:
            destination = Path(keep)
            destination.mkdir(parents=True, exist_ok=True)
            for path in sorted(directory.iterdir()):
                if path.is_file():
                    shutil.copy2(path, destination / path.name)

    return CcxRun(
        frd=frd,
        output=output,
        returncode=completed.returncode,
        seconds=seconds,
        artefacts=artefacts,
    )


__all__ = [
    "CCX_NAMES",
    "DEFAULT_TIMEOUT_S",
    "JOB_NAME",
    "CalculiXUnavailable",
    "CcxRun",
    "find_ccx",
    "require_ccx",
    "run_ccx",
]
