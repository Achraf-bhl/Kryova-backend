"""Running Project Chrono — the process boundary, and why there is one (master plan E9.1).

**This boundary is not a licence boundary.** Chrono is BSD-3 and could be linked
in-process; `app/solve/openfoam/` is a separate process because OpenFOAM is GPL and
Decision 4 forbids linking it. This one is a **packaging** boundary, and the finding
behind it is in `app/dynamics/engine.ChronoEngine`'s docstring:

* `pip install pychrono` **succeeds and installs an unrelated timing utility** — an 11 kB
  pure-Python wheel with no simulation code in it. A naive probe reports the engine
  present.
* `projectchrono` on PyPI is the project's own 0.0.0 reservation placeholder.
* PyChrono ships through **conda only**, and Decision 1 keeps conda out of this
  deployment — `cadquery-ocp` was chosen precisely to avoid forcing it.

So the engine runs in a container that has its own conda, and this venv never grows one.
That is the same shape `app/solve/openfoam/run.py` has, arrived at for a different reason,
and the reason is written here so nobody "simplifies" it by adding a conda channel.

**No published image ships PyChrono**, so unlike OpenFOAM the image is *built* rather than
pulled — `scripts/chrono_image.sh` makes one from `mambaorg/micromamba:1.5.10`, which is
where pychrono 9.0.1 with Python 3.12 was measured on 2026-09-14. It is never built during
a run, for the reason a run never pulls one: a run that installs its own solver has a
version nobody chose.

Four things carried over from the OpenFOAM boundary because they go wrong quietly:

1. **Files written in a container belong to the container's user** unless it runs as ours,
   and a work directory the server cannot delete is a disk that fills. On POSIX the
   container runs as the calling uid:gid.
2. **Not installed is a different answer from failed.** `ChronoUnavailable` says which
   launcher and what is missing; `MechanismError` says the run failed and why.
3. **A run that does not stop must not hold a worker for ever.** There is a timeout, and
   it names the levers rather than just the time.
4. **The engine is named before it runs.** `engine_identity` is the image's *content id*,
   not its tag, because a tag can be re-pushed to point at a different build. A `local`
   launcher is not identifiable before it runs and returns `None`, which the job cache
   reads as "never reuse" — an unidentified engine matching a known one is the claim
   Decision 3 forbids.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app import observe
from app.dynamics.errors import EngineUnavailable, MechanismError

#: Built by `scripts/chrono_image.sh`; there is no published image to pull.
DEFAULT_IMAGE: Final = "kryova-chrono:9.0.1"
DEFAULT_TIMEOUT_S: Final = 1800.0
LAUNCHERS: Final = ("docker", "local")

#: The script the container runs. Shipped into the work directory rather than baked into
#: the image, so a change to the translation does not need the image rebuilt -- and so the
#: code that runs is the code in this repository at this commit, which is what a result's
#: provenance has to be able to say.
ENTRYPOINT: Final = "chrono_run.py"


class ChronoUnavailable(EngineUnavailable):
    """No way to run Chrono was found. A subclass so `except EngineUnavailable` still holds."""


@dataclass(frozen=True)
class ChronoRun:
    returncode: int
    output: str
    seconds: float
    launcher: str


def _image_present(image: str) -> bool:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def image_id(image: str) -> str | None:
    """The content id (`sha256:…`) of a local image, or None when it cannot be read.

    A tag is a name and can be rebuilt to point at different bytes; the id is the bytes.
    This is what a result can be bound to *before* the run happens.
    """
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = completed.stdout.strip()
    return found if completed.returncode == 0 and found.startswith("sha256:") else None


def engine_identity(launcher: str, image: str = DEFAULT_IMAGE) -> str | None:
    """What will answer a mechanism, identified before it runs — or None when it cannot be."""
    if launcher != "docker" or shutil.which("docker") is None:
        return None
    found = image_id(image)
    return f"docker {image} {found}" if found else None


def availability(launcher: str, image: str = DEFAULT_IMAGE) -> str | None:
    """None when the launcher can run a mechanism; otherwise the sentence saying why not."""
    if launcher not in LAUNCHERS:
        return f"CHRONO_LAUNCHER must be one of {', '.join(LAUNCHERS)}; got {launcher!r}."
    if launcher == "docker":
        if shutil.which("docker") is None:
            return (
                "Chrono runs in a container here and no `docker` executable is on PATH. "
                "Install Docker, or set CHRONO_LAUNCHER=local on a machine whose own "
                "conda environment has PyChrono importable."
            )
        if not _image_present(image):
            return (
                f"The Chrono image {image} is not present. Build it once with "
                f"`scripts/chrono_image.sh`; there is no published image to pull, because "
                "PyChrono ships through conda rather than as a container. It is never "
                "built during a run — a run that installs its own solver has a version "
                "nobody chose."
            )
        return None
    if shutil.which("python") is None:
        return (
            "CHRONO_LAUNCHER=local and no `python` is on PATH. Activate the conda "
            "environment holding PyChrono before starting the server, or use docker."
        )
    return None


def run_mechanism(
    directory: Path,
    payload: dict[str, Any],
    *,
    launcher: str = "docker",
    image: str = DEFAULT_IMAGE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[ChronoRun, dict[str, Any]]:
    """Run one mechanism in `directory`, and read back what the container wrote.

    The directory is the caller's and is the whole interface: `input.json` in,
    `output.json` out, plus the entry-point script. Nothing else crosses.
    """
    missing = availability(launcher, image)
    if missing:
        raise ChronoUnavailable(missing)

    directory = directory.resolve()
    (directory / "input.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    # Written with an explicit LF newline: on Windows the platform default would rewrite
    # every line ending, and this file is read by a Linux interpreter inside the
    # container. Same trap `scripts/plan_progress.py` hit from the other direction.
    #
    # **Measured 2026-09-20, and it is weaker than it reads.** Removing the `newline`
    # argument on Windows writes CRLF and the run still *succeeds*: the entry point is
    # invoked as `python /work/chrono_run.py`, an argument rather than an executable, so
    # its shebang is never parsed and CPython accepts CRLF source. Only
    # `test_the_entry_point_is_written_with_lf` fails. So this line is insurance against
    # a future caller that execs the file directly -- where a shebang ending in a
    # carriage return fails with "no such file or directory" naming an interpreter that plainly exists --
    # and not, today, the difference between a run and no run. Recorded because the
    # opposite was believed: THE QUEUE G6 listed LF endings as one of three things that
    # would stop this working on Windows, and it is the one that would not have.
    (directory / ENTRYPOINT).write_text(_entrypoint_source(), encoding="utf-8", newline="\n")

    if launcher == "docker":
        command = ["docker", "run", "--rm", "-v", f"{directory}:/work", "-w", "/work"]
        # `getattr`, not `hasattr` plus a bare call — see the same lines in
        # `app/solve/openfoam/run.py`: these names are absent on Windows, so a
        # bare reference is a type error there however the branch is guarded.
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        if getuid is not None and getgid is not None:
            command += ["--user", f"{getuid()}:{getgid()}"]
        # `--network none`: this run reads one file and writes one file. A solver that
        # can reach the network is a solver that can fetch something, and then the
        # engine identity above stops describing what actually answered.
        command += ["--network", "none", image, "python", f"/work/{ENTRYPOINT}"]
    else:
        command = ["python", str(directory / ENTRYPOINT)]

    started = time.perf_counter()
    with observe.span("dynamics.chrono.run", launcher=launcher) as timing:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as expired:
            raise MechanismError(
                f"Chrono did not finish within {timeout_s:g} s and was stopped. A "
                "multibody run this long is usually a step size far smaller than the "
                "motion needs, or a duration longer than the question — raise step_s, "
                "shorten the range, or raise the timeout if both are deliberate."
            ) from expired
        timing.set("returncode", completed.returncode)

    run = ChronoRun(
        returncode=completed.returncode,
        output=(completed.stdout or "") + (completed.stderr or ""),
        seconds=time.perf_counter() - started,
        launcher=launcher,
    )

    result_path = directory / "output.json"
    if run.returncode != 0 or not result_path.is_file():
        raise MechanismError(
            "Chrono did not produce a result. It exited with "
            f"{run.returncode} and said:\n{run.output[-4000:].strip() or '(nothing)'}"
        )
    try:
        written = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MechanismError(
            f"Chrono wrote a result file this build could not read: {exc}"
        ) from exc
    if not isinstance(written, dict):
        raise MechanismError("Chrono's result file is not an object.")
    if written.get("error"):
        raise MechanismError(f"Chrono refused this mechanism: {written['error']}")
    return run, written


def _entrypoint_source() -> str:
    """The container-side script, read from this package.

    A file rather than a string literal so it is lintable, readable and diffable like
    every other module here — and so `code_fingerprint` sees it change.
    """
    return (Path(__file__).parent / "_entrypoint.py").read_text(encoding="utf-8")


__all__ = [
    "DEFAULT_IMAGE",
    "DEFAULT_TIMEOUT_S",
    "ENTRYPOINT",
    "LAUNCHERS",
    "ChronoRun",
    "ChronoUnavailable",
    "availability",
    "engine_identity",
    "image_id",
    "run_mechanism",
]
