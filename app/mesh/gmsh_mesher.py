"""Gmsh-backed tetrahedral meshing of uploaded CAD files.

Gmsh is a process-global singleton and is not thread-safe, so every call here
serialises on a module lock. FastAPI runs sync endpoints in a threadpool, which
would otherwise let two requests corrupt each other's model. Meshing belongs on
a job queue anyway; the lock is the correctness floor, not the plan.
"""

import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

from app.geometry.formats import GEOMETRY_FORMATS
from app.geometry.inspect import is_binary_stl
from app.mesh.types import MeshError, TetMesh, quality

_GMSH_LOCK = threading.Lock()

_TET4_ELEMENT_TYPE = 4

# Default target: this many elements along the bounding-box diagonal. Enough to
# resolve a simple part in seconds; refinement is a user-facing knob.
_DEFAULT_ELEMENTS_ALONG_DIAGONAL = 20

# STL is a bag of triangles with no topology. These control how gmsh infers
# faces from it: dihedral angles sharper than this start a new surface.
_STL_FEATURE_ANGLE_DEG = 40.0
_STL_CURVE_ANGLE_DEG = 180.0

# Binary STL: 80 bytes of free-form comment before the triangle count.
_STL_HEADER_BYTES = 80
# Must not begin with a NUL, and must not begin with "solid".
_STAGED_STL_HEADER = b"Kryova staged binary STL".ljust(_STL_HEADER_BYTES, b" ")


def generate_tet_mesh(
    path: Path, file_format: str, element_size_mm: float | None = None
) -> tuple[TetMesh, dict[str, Any]]:
    """Mesh a CAD file into linear tetrahedra.

    Returns the mesh and its quality summary. `element_size_mm` overrides the
    automatic target size.
    """
    import gmsh

    with _GMSH_LOCK, _named_for_gmsh(path, file_format) as readable:
        # interruptible=False: gmsh otherwise installs a SIGINT handler, which
        # raises off the main thread -- and meshing always runs on a worker.
        gmsh.initialize(interruptible=False)
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            gmsh.option.setNumber("General.Verbosity", 1)
            _load(gmsh, readable, file_format)
            _set_element_size(gmsh, element_size_mm)
            try:
                gmsh.model.mesh.generate(3)
            except Exception as exc:  # gmsh raises bare Exception subclasses
                raise MeshError(f"Meshing failed: {exc}") from exc
            mesh = _extract(gmsh)
        finally:
            gmsh.clear()
            gmsh.finalize()

    stats = quality(mesh)
    stats["mesher"] = "gmsh"
    return mesh, stats


@contextmanager
def _named_for_gmsh(path: Path, file_format: str) -> Iterator[Path]:
    """Present the file to gmsh under a name and header it can actually read.

    Two things trip gmsh up:

    * It chooses its reader from the file extension, and blobs in the media
      store are named by their SHA-256 with no extension at all.
    * Its STL sniffer walks the file looking for a line it can classify as
      "solid" (ASCII) or not (binary), but skips any line starting with a NUL.
      A binary STL with the conventional zeroed 80-byte header and no 0x0A byte
      anywhere -- which happens whenever the coordinates are NUL-heavy, e.g.
      exact powers of two -- has no such line, so gmsh reaches EOF and reports
      only "Error loading". Replacing the header with text costs one copy and
      makes the file classify immediately.

    A hard link is used where the bytes need no change; it costs nothing and
    leaves the blob untouched.
    """
    rewrite_header = file_format == "stl" and _header_defeats_gmsh(path)
    named_correctly = path.suffix.lower().lstrip(".") in GEOMETRY_FORMATS.get(file_format, ())
    if named_correctly and not rewrite_header:
        yield path
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        staged = Path(tmpdir) / f"model.{file_format}"
        if rewrite_header:
            _copy_with_text_header(path, staged)
        else:
            try:
                os.link(path, staged)
            except OSError:
                shutil.copyfile(path, staged)
        yield staged


def _header_defeats_gmsh(path: Path) -> bool:
    """True for a binary STL whose header starts with a NUL (see above)."""
    with path.open("rb") as fh:
        first = fh.read(1)
    return bool(first) and first[0] == 0 and is_binary_stl(path)


def _copy_with_text_header(source: Path, target: Path) -> None:
    """Copy an STL, replacing only its 80-byte comment header.

    The header is free-form by convention and carries no geometry, so this
    changes nothing a mesher cares about.
    """
    with source.open("rb") as src, target.open("wb") as dst:
        src.seek(_STL_HEADER_BYTES)
        dst.write(_STAGED_STL_HEADER)
        shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)


def _load(gmsh, path: Path, file_format: str) -> None:
    try:
        gmsh.merge(str(path))
    except Exception as exc:
        raise MeshError(f"Gmsh could not read the file: {exc}") from exc

    if file_format == "stl":
        _build_volume_from_triangles(gmsh)
        return

    # STEP and IGES arrive with real topology. If the file carried only surfaces
    # there is nothing to fill with tets, and that is worth saying plainly.
    if not gmsh.model.getEntities(3):
        raise MeshError(
            "The file contains no solid body, only surfaces. Structural analysis "
            "needs a closed solid."
        )


def _build_volume_from_triangles(gmsh) -> None:
    """Turn a raw STL triangle soup into a meshable solid."""
    try:
        gmsh.model.mesh.classifySurfaces(
            np.radians(_STL_FEATURE_ANGLE_DEG),
            True,
            True,
            np.radians(_STL_CURVE_ANGLE_DEG),
        )
        gmsh.model.mesh.createGeometry()
        surfaces = [entity[1] for entity in gmsh.model.getEntities(2)]
        if not surfaces:
            raise MeshError("No surfaces could be recovered from the STL")
        loop = gmsh.model.geo.addSurfaceLoop(surfaces)
        gmsh.model.geo.addVolume([loop])
        gmsh.model.geo.synchronize()
    except MeshError:
        raise
    except Exception as exc:
        raise MeshError(
            f"The STL could not be closed into a solid ({exc}). Non-watertight meshes "
            "must be repaired before analysis."
        ) from exc


def _set_element_size(gmsh, element_size_mm: float | None) -> None:
    if element_size_mm is not None:
        if element_size_mm <= 0:
            raise MeshError("element_size_mm must be positive")
        target = element_size_mm
    else:
        lo = np.array(gmsh.model.getBoundingBox(-1, -1)[:3])
        hi = np.array(gmsh.model.getBoundingBox(-1, -1)[3:])
        diagonal = float(np.linalg.norm(hi - lo))
        if not np.isfinite(diagonal) or diagonal <= 0:
            raise MeshError("The model has no measurable extent")
        target = diagonal / _DEFAULT_ELEMENTS_ALONG_DIAGONAL

    gmsh.option.setNumber("Mesh.MeshSizeMin", target * 0.25)
    gmsh.option.setNumber("Mesh.MeshSizeMax", target)
    # Ignore sizes baked into the CAD file; the target above is the intent.
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)


def _extract(gmsh) -> TetMesh:
    node_tags, coordinates, _ = gmsh.model.mesh.getNodes()
    if len(node_tags) == 0:
        raise MeshError("Meshing produced no nodes")
    nodes = np.asarray(coordinates, dtype=np.float64).reshape(-1, 3)

    # Gmsh tags are 1-based and may have gaps; remap them to dense indices.
    tags = np.asarray(node_tags, dtype=np.int64)
    lookup = np.full(int(tags.max()) + 1, -1, dtype=np.int64)
    lookup[tags] = np.arange(len(tags), dtype=np.int64)

    element_types, _, element_nodes = gmsh.model.mesh.getElements(3)
    for element_type, connectivity in zip(element_types, element_nodes, strict=True):
        if element_type != _TET4_ELEMENT_TYPE:
            continue
        tets = lookup[np.asarray(connectivity, dtype=np.int64).reshape(-1, 4)]
        if (tets < 0).any():
            raise MeshError("Meshing produced an element referencing an unknown node")
        return _drop_unused_nodes(nodes, tets)

    raise MeshError("Meshing produced no tetrahedra; the geometry may not be a closed solid")


def _drop_unused_nodes(nodes: np.ndarray, tets: np.ndarray) -> TetMesh:
    """Gmsh keeps surface-only nodes in its node list; the solver would read
    them as unconstrained free DOFs and make the system singular."""
    used, inverse = np.unique(tets, return_inverse=True)
    return TetMesh(nodes=nodes[used], tets=inverse.reshape(tets.shape).astype(np.int64))
