"""Inspection of uploaded CAD files: enough to show the user something real
immediately after upload, and to reject a file that is not what it claims.

STL is read directly -- it is a bag of triangles and needs no kernel. STEP and
IGES are B-rep formats, so their bounding box and volume come from the same
OpenCASCADE kernel the mesher uses, through gmsh. That costs an import and a
kernel load, but the alternative is what this module used to do: return only a
schema string, leaving every CAD user without the bounding box that the load
case editor and the AI load-case drafting both work from.
"""

import logging
import re
import struct
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BINARY_STL_HEADER = 84
_BINARY_STL_TRIANGLE = 50


class GeometryError(ValueError):
    """The uploaded file is not readable as the format it claims to be."""


def inspect(path: Path, file_format: str) -> dict[str, Any]:
    """Return best-effort stats for a geometry file. Never raises for a readable file."""
    if file_format == "stl":
        return _inspect_stl(path)
    if file_format in ("step", "iges"):
        stats = _inspect_text_cad(path, file_format)
        stats.update(_inspect_brep(path, file_format))
        return stats
    return {}


def _inspect_stl(path: Path) -> dict[str, Any]:
    if is_binary_stl(path):
        triangles, bbox = _read_binary_stl(path)
        encoding = "binary"
    else:
        triangles, bbox = _read_ascii_stl(path)
        encoding = "ascii"

    if triangles == 0:
        raise GeometryError("STL file contains no triangles")

    stats: dict[str, Any] = {"encoding": encoding, "triangle_count": triangles}
    if bbox is not None:
        lo, hi = bbox
        stats["bounding_box"] = {
            "min": list(lo),
            "max": list(hi),
            "size": [hi[i] - lo[i] for i in range(3)],
        }
    return stats


def is_binary_stl(path: Path) -> bool:
    size = path.stat().st_size
    if size < _BINARY_STL_HEADER:
        return False
    with path.open("rb") as fh:
        header = fh.read(80)
        (count,) = struct.unpack("<I", fh.read(4))
    if size == _BINARY_STL_HEADER + count * _BINARY_STL_TRIANGLE:
        return True
    # Some writers pad the file; fall back to sniffing the header.
    return not header.lstrip()[:5].lower().startswith(b"solid")


def _read_binary_stl(path: Path) -> tuple[int, tuple[tuple[float, ...], tuple[float, ...]] | None]:
    with path.open("rb") as fh:
        fh.seek(80)
        (count,) = struct.unpack("<I", fh.read(4))
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        read = 0
        while read < count:
            chunk = fh.read(_BINARY_STL_TRIANGLE)
            if len(chunk) < _BINARY_STL_TRIANGLE:
                break
            values = struct.unpack("<12fH", chunk)[:12]
            for vertex in range(1, 4):  # values[0:3] is the facet normal
                for axis in range(3):
                    coord = values[vertex * 3 + axis]
                    lo[axis] = min(lo[axis], coord)
                    hi[axis] = max(hi[axis], coord)
            read += 1
    return read, (tuple(lo), tuple(hi)) if read else None


_VERTEX_RE = re.compile(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", re.IGNORECASE)


def _read_ascii_stl(path: Path) -> tuple[int, tuple[tuple[float, ...], tuple[float, ...]] | None]:
    facets = 0
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen_vertex = False
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.lower().startswith("facet"):
                facets += 1
                continue
            match = _VERTEX_RE.match(stripped)
            if not match:
                continue
            try:
                coords = [float(value) for value in match.groups()]
            except ValueError:
                continue
            seen_vertex = True
            for axis in range(3):
                lo[axis] = min(lo[axis], coords[axis])
                hi[axis] = max(hi[axis], coords[axis])
    return facets, (tuple(lo), tuple(hi)) if seen_vertex else None


_STEP_SCHEMA_RE = re.compile(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", re.IGNORECASE)


def _inspect_text_cad(path: Path, file_format: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        head = fh.read(8192)

    if file_format == "step":
        if "ISO-10303" not in head:
            raise GeometryError("file does not look like a STEP (ISO-10303) file")
        match = _STEP_SCHEMA_RE.search(head)
        return {"schema": match.group(1)} if match else {}

    # IGES: fixed 80-column records, section letter in column 73.
    if not any(len(line) >= 73 and line[72] in "SGDPT" for line in head.splitlines()):
        raise GeometryError("file does not look like an IGES file")
    return {}


# Gmsh reports an empty model's bounding box as this sentinel rather than
# failing, and a box built from it would be nonsense in every consumer.
_GMSH_EMPTY_BBOX = 1e21


def _inspect_brep(path: Path, file_format: str) -> dict[str, Any]:
    """Bounding box, volume and solid count for a STEP or IGES file.

    Read through the same OpenCASCADE kernel the mesher uses, so the numbers
    here are the numbers a simulation will see. Without this a CAD user gets no
    bounding box at all, which is what both the load-case editor and the AI
    load-case drafting select regions against.

    Best effort by contract: `inspect` promises never to raise for a readable
    file. A file the text sniff accepted but the kernel cannot open still
    uploads -- the mesher will report the real problem, in terms of meshing,
    when a simulation is actually run against it.
    """
    from app.mesh.gmsh_session import gmsh_session, staged_with_extension

    try:
        with staged_with_extension(path, file_format) as readable, gmsh_session() as gmsh:
            gmsh.model.occ.importShapes(str(readable))
            gmsh.model.occ.synchronize()
            solids = gmsh.model.getEntities(3)
            if not gmsh.model.getEntities():
                return {}
            extents = list(gmsh.model.getBoundingBox(-1, -1))
            volume = float(sum(gmsh.model.occ.getMass(3, tag) for _, tag in solids))
    except Exception as exc:  # noqa: BLE001 - any kernel failure is "no stats"
        logger.info("Could not read %s through OpenCASCADE: %s", path.name, exc)
        return {}

    if any(abs(value) >= _GMSH_EMPTY_BBOX for value in extents):
        return {}

    lo, hi = extents[:3], extents[3:]
    stats: dict[str, Any] = {
        "bounding_box": {
            "min": lo,
            "max": hi,
            "size": [hi[axis] - lo[axis] for axis in range(3)],
        },
        "solid_count": len(solids),
    }
    # A surfaces-only IGES has no volume, and reporting 0.0 mm^3 would read as a
    # measurement rather than as "this file has no solid in it".
    if volume > 0.0:
        stats["volume_mm3"] = volume
    return stats
