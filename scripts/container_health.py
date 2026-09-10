"""Is this container actually able to do the work? (P9 task 3's HEALTHCHECK.)

**A health check that only asks "is the web server up" is the most expensive
kind of green there is.** The fleet reports healthy, the load balancer keeps
sending traffic, and every simulation fails — because the image was built
without CalculiX, or gmsh's shared objects cannot load, or OCCT is absent. Each
of those produces a process that starts perfectly and cannot solve anything.

So this checks the three things the image exists to carry, and it checks them by
*asking the code that uses them*, not by looking for files. `find_ccx` is what
the solver actually calls; `import gmsh` is what the mesher actually does. A
check that stat'd `/usr/bin/ccx` would pass on an image where the binary is
present and unrunnable.

**Exit codes are the contract**: 0 healthy, 1 unhealthy, and the reason goes to
stdout where `docker inspect` keeps it. Nothing is raised — a traceback in a
health check is a reason nobody reads.

Run standalone too: `python -m scripts.container_health` on a developer machine
says which of the three are missing there, which is the same question and a
better answer than an import error halfway through a run.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator


def _check_calculix() -> tuple[bool, str]:
    """Ask the solver's own finder, not the filesystem.

    A file at `/usr/bin/ccx` that will not execute — wrong architecture, missing
    shared object, not the flag `find_ccx` looks for — passes a `stat` and fails
    every job.
    """
    try:
        from app.solve.calculix.run import find_ccx
    except Exception as exc:  # noqa: BLE001 - a broken import is an unhealthy image
        return False, f"CalculiX support could not be imported: {exc}"
    found = find_ccx()
    if found is None:
        return False, "no CalculiX binary on PATH (the image is missing calculix-ccx)"
    return True, f"CalculiX at {found}"


def _check_gmsh() -> tuple[bool, str]:
    try:
        import gmsh
    except Exception as exc:  # noqa: BLE001 - most often a missing libGL
        return False, f"gmsh will not import: {exc}"
    version = getattr(gmsh, "GMSH_API_VERSION", "unknown")
    return True, f"gmsh {version}"


def _check_occt() -> tuple[bool, str]:
    """OCCT is optional by contract and its absence is reported, not fatal.

    `app/kernel/` imports without it — the same contract the CATIA bridge keeps
    for pywin32 — and an install that omits it degrades to the CATIA backend.
    So this reports the state and does not fail the container: an image
    deliberately built for a CATIA-only deployment is healthy.
    """
    try:
        from app.kernel.occt.binding import occt_version
    except Exception as exc:  # noqa: BLE001
        return True, f"OCCT not available ({exc}); this image is CATIA-only"
    try:
        return True, f"OCCT {occt_version()}"
    except Exception as exc:  # noqa: BLE001
        return True, f"OCCT not available ({exc}); this image is CATIA-only"


#: Name, checker, and whether a failure makes the container unhealthy. OCCT is
#: the one that does not: see `_check_occt`.
CHECKS: tuple[tuple[str, object, bool], ...] = (
    ("calculix", _check_calculix, True),
    ("gmsh", _check_gmsh, True),
    ("occt", _check_occt, False),
)


def run() -> Iterator[tuple[str, bool, str, bool]]:
    for name, check, required in CHECKS:
        healthy, detail = check()  # type: ignore[operator]
        yield name, healthy, detail, required


def main() -> int:
    failures = 0
    for name, healthy, detail, required in run():
        mark = "ok" if healthy else ("FAIL" if required else "warn")
        print(f"{mark:>4}  {name}: {detail}")
        if not healthy and required:
            failures += 1
    if failures:
        print(f"unhealthy: {failures} required component(s) missing")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
