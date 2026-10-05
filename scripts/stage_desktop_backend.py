"""Assemble the backend half of the desktop installer (ROAD_TO_10 4.2).

`../Kryova-frontend/scripts/stage-desktop.mjs` puts a pinned, relocatable CPython at
`<out>/python`. This finishes the backend beside it:

    <out>/app/               the application, from an allow-list
    <out>/migrations/        what `app.desktop` runs on first launch and on every upgrade
    <out>/alembic.ini
    <out>/scripts/catia_bridge/   the workstation daemon the backend spawns itself
    <out>/data/verify/benchmark-outcomes.json   what the public trust page publishes
    <out>/python/Lib/site-packages/   every wheel in `requirements-desktop.lock.txt`
    <out>/bundle-manifest.json

**An allow-list, never a directory copy.** Copying the checkout and deleting what should not
ship is how a developer's `.env.local` -- which holds a live model API key -- ends up inside a
signed installer. Nothing is copied unless it is named below, and `bundle_problems` re-checks
the result for credential files, the test suite, a virtualenv and the 450 MB of reference
manuals before the script reports success.

**Wheels come from a lock, with hashes** (`requirements-desktop.lock.txt`, made by `uv pip
compile --python-platform windows --python-version 3.14 --generate-hashes`). `requirements.txt`
leaves transitive versions to pip, so two installers built a month apart could carry different
numpy builds; the lock cannot. `tests/test_desktop_stage.py` fails when a direct pin in
`requirements.txt` and the lock disagree.

**Not PyInstaller**: its import scanning has a long record of missing OCP's and gmsh's native
plugins. The interpreter is a stock relocatable CPython and what ships is what pip installed.

`laya`/`torch` stay out (`requirements-laya.txt`): no local model, decided 2026-10-04.

On a Windows machine every step is native. Elsewhere the wheels are cross-installed with `uv`,
which proves the tree is complete and cannot compile bytecode with a Windows interpreter -- the
manifest says which happened (`bytecode`), so an installer built without it is not mistaken for
one that has it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LOCK = BACKEND / "requirements-desktop.lock.txt"

#: What is copied, relative to the backend checkout. Directories are copied whole except for
#: `SKIP_NAMES`; a file is copied as itself. Anything not named here does not ship.
INCLUDE = (
    "app",
    "migrations",
    "alembic.ini",
    "scripts/catia_bridge",
    "data/verify/benchmark-outcomes.json",
    "LICENSE",
)

#: Names that are never copied, wherever they turn up under an included directory.
SKIP_NAMES = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
SKIP_SUFFIXES = (".pyc", ".pyo")

#: Names that must not be in the finished tree. A credential file is the one that matters.
FORBIDDEN_BASENAMES = frozenset({".env", ".env.local", ".env.production", ".git"})
FORBIDDEN_PATHS = ("tests", "venv", ".venv", "media_data", "data/bm25")

#: The embedded interpreter is the one the lock was resolved for.
PYTHON_VERSION = "3.14"

REQUIRED = ("app/desktop.py", "app/main.py", "alembic.ini", "migrations/env.py")


def _skipped(path: Path) -> bool:
    return path.name in SKIP_NAMES or path.name.endswith(SKIP_SUFFIXES)


def copy_tree(source: Path, dest: Path) -> int:
    """Copy `source` (a file or a directory) to `dest`, minus the skipped names. Returns the file count."""
    if source.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return 1
    count = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(_skipped(Path(part)) for part in relative.parts) or path.is_dir():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def bundle_problems(root: Path) -> list[str]:
    """What is wrong with a staged backend tree, as sentences. Empty means it is sound."""
    problems = [f"missing {name}" for name in REQUIRED if not (root / name).is_file()]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("python/"):
            continue  # the interpreter's own tree has a `tests` in every wheel
        if path.name in FORBIDDEN_BASENAMES or relative in FORBIDDEN_PATHS:
            problems.append(f"must not ship: {relative}")
    return problems


def lock_pins(lock: Path = LOCK) -> dict[str, str]:
    """`{normalised name: version}` for every `name==version` line of a lock file."""
    pins: dict[str, str] = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--")) or "==" not in line:
            continue
        name, _, rest = line.partition("==")
        version = rest.split()[0].rstrip("\\").strip()
        pins[normalise(name.split("[")[0])] = version
    return pins


def normalise(name: str) -> str:
    """PEP 503: case and `-_.` runs are not significant."""
    out = []
    for char in name.strip().lower():
        out.append("-" if char in "_." else char)
    return "-".join(part for part in "".join(out).split("-") if part)


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def install_wheels(out: Path, lock: Path, *, native: bool) -> None:
    """Install the locked wheels into the embedded interpreter's site-packages."""
    if native:
        python = out / "python" / "python.exe"
        command = [
            str(python), "-m", "pip", "install",
            "--require-hashes", "--no-deps", "--only-binary=:all:",
            "--no-warn-script-location", "--disable-pip-version-check",
            "-r", str(lock),
        ]  # fmt: skip
    else:
        uv = shutil.which("uv")
        if uv is None:
            raise SystemExit(
                "Cross-installing Windows wheels from this machine needs `uv` "
                "(pip install uv). On a Windows machine this step uses the embedded "
                "interpreter's own pip instead."
            )
        command = [
            uv, "pip", "install",
            "--python-platform", "windows", "--python-version", PYTHON_VERSION,
            "--target", str(out / "python" / "Lib" / "site-packages"),
            "--only-binary", ":all:", "--require-hashes", "--no-deps",
            "-r", str(lock),
        ]  # fmt: skip
    subprocess.run(command, check=True)


def compile_bytecode(out: Path) -> None:
    """Precompile, so the first launch is not spent compiling a gigabyte of Python.

    The install directory is read-only to the person running the app and the shell sets
    `PYTHONDONTWRITEBYTECODE`, so what is not compiled here is compiled again at every launch.
    """
    python = out / "python" / "python.exe"
    subprocess.run(
        [str(python), "-m", "compileall", "-q", "-j", "0", str(out / "app"),
         str(out / "python" / "Lib" / "site-packages")],
        check=True,
    )  # fmt: skip


def stage(
    out: Path,
    *,
    lock: Path = LOCK,
    wheels: bool = True,
    include: Iterable[str] = INCLUDE,
    source: Path = BACKEND,
    native: bool | None = None,
) -> dict[str, object]:
    """Build the backend tree under `out` and return the manifest it wrote."""
    native = sys.platform == "win32" if native is None else native
    out.mkdir(parents=True, exist_ok=True)

    copied = 0
    for name in include:
        origin = source / name
        if not origin.exists():
            raise SystemExit(f"{origin} is on the include list and does not exist.")
        # Cleared first, so a file deleted from the checkout is not shipped from last time.
        target = out / name
        if target.is_dir():
            shutil.rmtree(target)
        copied += copy_tree(origin, target)

    if wheels:
        install_wheels(out, lock, native=native)
    compiled = False
    if wheels and native:
        compile_bytecode(out)
        compiled = True

    problems = bundle_problems(out)
    if problems:
        raise SystemExit("The backend tree is not sound:\n  - " + "\n  - ".join(problems))

    manifest: dict[str, object] = {
        "backend_git": _git_sha(),
        "python": PYTHON_VERSION,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "wheels": len(lock_pins(lock)) if wheels else 0,
        "app_files": copied,
        "bytecode": "precompiled" if compiled else "absent",
    }
    (out / "bundle-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="the bundle's backend directory")
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--skip-wheels", action="store_true")
    args = parser.parse_args(argv)
    manifest = stage(args.out.resolve(), lock=args.lock, wheels=not args.skip_wheels)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
