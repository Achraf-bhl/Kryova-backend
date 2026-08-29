"""Serialised access to the process-global gmsh singleton.

Gmsh keeps one model per process and is not thread-safe. FastAPI runs sync
endpoints in a threadpool and jobs run on worker threads, so every entry point
that touches gmsh -- meshing, and B-rep inspection at upload time -- has to come
through the same lock or two requests will corrupt each other's model.
"""

import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.geometry.formats import GEOMETRY_FORMATS

_GMSH_LOCK = threading.Lock()


@contextmanager
def gmsh_session() -> Iterator[Any]:
    """Exclusive use of gmsh, initialised and guaranteed to be finalised."""
    import gmsh

    with _GMSH_LOCK:
        # interruptible=False: gmsh otherwise installs a SIGINT handler, which
        # raises off the main thread -- and this never runs on the main thread.
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.Verbosity", 1)
            yield gmsh
        finally:
            gmsh.clear()
            gmsh.finalize()


@contextmanager
def staged_with_extension(
    path: Path,
    file_format: str,
    copy: Callable[[Path, Path], None] | None = None,
) -> Iterator[Path]:
    """Present a file to gmsh under a name whose extension it recognises.

    Gmsh picks its reader from the extension, and blobs in the media store are
    named by their SHA-256 with no extension at all. A hard link costs nothing
    and leaves the stored blob untouched; `copy` replaces it when the bytes
    themselves have to change on the way (see the STL header trap in
    `gmsh_mesher`). **The stored blob is never modified.**
    """
    named_correctly = path.suffix.lower().lstrip(".") in GEOMETRY_FORMATS.get(file_format, ())
    if copy is None and named_correctly:
        yield path
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        staged = Path(tmpdir) / f"model.{file_format}"
        if copy is not None:
            copy(path, staged)
        else:
            try:
                os.link(path, staged)
            except OSError:
                shutil.copyfile(path, staged)
        yield staged
