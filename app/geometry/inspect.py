"""Lightweight, dependency-free inspection of uploaded CAD files.

Deliberately shallow: enough to show the user something real immediately after
upload (and to reject obviously broken files) without loading a CAD kernel.
Full B-rep inspection belongs behind the geometry kernel once it lands.
"""

import re
import struct
from pathlib import Path
from typing import Any

_BINARY_STL_HEADER = 84
_BINARY_STL_TRIANGLE = 50


class GeometryError(ValueError):
    """The uploaded file is not readable as the format it claims to be."""


def inspect(path: Path, file_format: str) -> dict[str, Any]:
    """Return best-effort stats for a geometry file. Never raises for a readable file."""
    if file_format == "stl":
        return _inspect_stl(path)
    if file_format in ("step", "iges"):
        return _inspect_text_cad(path, file_format)
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
