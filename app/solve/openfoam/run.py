"""Running OpenFOAM — the process boundary, which Decision 4 is about.

OpenFOAM is GPL, so it is invoked as a separate process across a file boundary:
a case directory in, a case directory out, nothing linked. Two launchers reach it:

* **`docker`** (the default) runs the case inside a pinned image —
  `opencfd/openfoam-default:2412` unless `OPENFOAM_IMAGE` says otherwise. The
  image is the version pin: the same tag gives the same build on Linux and on
  Windows' Docker Desktop, where OpenFOAM has no native release.
* **`local`** runs `bash` on a machine whose OpenFOAM environment is already on
  `PATH` (the `bashrc` sourced before the server started).

Four things here that would otherwise go wrong quietly:

1. **A login shell changes directory.** OpenFOAM's image sources its environment
   through `bash -l`, whose profile leaves the shell somewhere else; logs written
   by relative path then fail with "Permission denied" in a directory nobody
   named. `Allrun` changes to its own directory first.
2. **Files written in a container belong to the container's user** unless it runs
   as ours, and a case directory the server cannot delete is a disk that fills.
   On POSIX the container runs as the calling uid:gid.
3. **Not installed is a different answer from failed.** `OpenFoamUnavailable`
   says which launcher and what is missing; `SolverError` says the case failed and
   which step, from `Allrun`'s exit code.
4. **A run that does not stop must not hold a worker for ever.** There is a
   timeout, and it names the cell count lever rather than just the time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app import observe
from app.solve.types import SolverError

DEFAULT_IMAGE: Final = "opencfd/openfoam-default:2412"
DEFAULT_TIMEOUT_S: Final = 3600.0
LAUNCHERS: Final = ("docker", "local")


class OpenFoamUnavailable(SolverError):
    """No way to run OpenFOAM was found. A subclass so `except SolverError` still holds."""


@dataclass(frozen=True)
class FoamRun:
    returncode: int
    output: str
    seconds: float
    launcher: str


def _image_present(image: str) -> bool:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def image_id(image: str) -> str | None:
    """The content id (`sha256:…`) of a local image, or None when it cannot be read.

    A tag is a name and can be re-pushed to point at a different build; the id is
    the bytes. This is what a result can be bound to before the run happens.
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
    """What will answer a case, identified before it runs — or None when it cannot be.

    A docker run is identified by its image's content id. A local install is
    not identifiable until it prints its banner, which is after the work, so it
    is None — and the job cache treats None as "do not reuse", because an
    unidentified engine matching a known one is the claim Decision 3 forbids.
    """
    if launcher != "docker" or shutil.which("docker") is None:
        return None
    found = image_id(image)
    return f"docker {image} {found}" if found else None


def availability(launcher: str, image: str = DEFAULT_IMAGE) -> str | None:
    """None when the launcher can run a case; otherwise the sentence saying why not."""
    if launcher not in LAUNCHERS:
        return f"OPENFOAM_LAUNCHER must be one of {', '.join(LAUNCHERS)}; got {launcher!r}."
    if launcher == "docker":
        if shutil.which("docker") is None:
            return (
                "OpenFOAM runs in a container here and no `docker` executable is on PATH. "
                "Install Docker, or set OPENFOAM_LAUNCHER=local on a machine with OpenFOAM installed."
            )
        if not _image_present(image):
            return (
                f"The OpenFOAM image {image} is not present. Pull it once with "
                f"`docker pull {image}`; it is never pulled during a run, because a run that "
                "downloads a solver has a version nobody chose."
            )
        return None
    if shutil.which("simpleFoam") is None or shutil.which("snappyHexMesh") is None:
        return (
            "OPENFOAM_LAUNCHER=local and simpleFoam/snappyHexMesh are not on PATH. Source "
            "OpenFOAM's etc/bashrc before starting the server, or use the docker launcher."
        )
    return None


def run_case(
    directory: Path,
    *,
    launcher: str = "docker",
    image: str = DEFAULT_IMAGE,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> FoamRun:
    """Execute `Allrun` in `directory`. The directory is the caller's; nothing else is written."""
    missing = availability(launcher, image)
    if missing:
        raise OpenFoamUnavailable(missing)
    directory = directory.resolve()
    if launcher == "docker":
        command = ["docker", "run", "--rm", "-v", f"{directory}:/case"]
        if hasattr(os, "getuid"):
            command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        command += [image, "bash", "-lc", "bash /case/Allrun"]
    else:
        command = ["bash", str(directory / "Allrun")]

    started = time.perf_counter()
    with observe.span("solve.openfoam.run", launcher=launcher) as timing:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as expired:
            raise SolverError(
                f"OpenFOAM did not finish within {timeout_s:g} s and was stopped. A laminar "
                "duct that runs this long is usually a background grid far finer than the "
                "passage needs — raise cell_size_mm, or raise the timeout if the size is deliberate."
            ) from expired
        timing.set("returncode", completed.returncode)
    return FoamRun(
        returncode=completed.returncode,
        output=(completed.stdout or "") + (completed.stderr or ""),
        seconds=time.perf_counter() - started,
        launcher=launcher,
    )


__all__ = [
    "DEFAULT_IMAGE",
    "DEFAULT_TIMEOUT_S",
    "LAUNCHERS",
    "FoamRun",
    "OpenFoamUnavailable",
    "availability",
    "engine_identity",
    "image_id",
    "run_case",
]
