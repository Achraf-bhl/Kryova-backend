"""A part or a machine as a binary glTF file -- master plan P6.1, the packaging half.

`app/kernel/occt/tessellate.py` turns a shape into triangles; this writes them as GLB (glTF 2.0,
binary container) for the browser. Written from the Khronos specification
(`KhronosGroup/glTF`, `specification/2.0/Specification.adoc`, read 2026-09-15), and every rule
below is that document's, not a library's:

* **"The units for all linear distances are meters", and +Y is up.** Kryova is millimetres and
  CAD is +Z up, and nothing in this codebase converts (`CLAUDE.md`). So the vertex data stays in
  the part's own millimetres and CAD axes, and **one root node carries the whole change**: a
  scale of 0.001 and the rotation taking +Z to +Y. A reader of this file who wants millimetres
  back undoes one matrix, and a digest over the vertex bytes is the same number the
  tessellation produced.
* **That root matrix has a positive determinant**, which matters: the spec flips the winding
  of every triangle under a node whose global transform has a negative one. The rotation used
  (x, y, z) -> (x, z, -y) is proper; the mirror (x, z, y) would turn every part inside out.
  An instance frame that is not a proper rotation is refused for the same reason.
* **A POSITION accessor must state `min` and `max`**, per component. They are written from the
  float32 values actually stored, not the float64 ones they came from.
* **Matrices are column-major.** `Frame.rotation` is row-major (`app/dynamics/pose.py`), so the
  transpose happens in exactly one place, `_column_major`.
* **No normals are written.** The spec requires a client to compute flat normals when a
  primitive has none, and the tessellation does not weld vertices across faces, so flat is what
  it would get anyway -- at a quarter less geometry.
* **GLB layout**: a 12-byte header (magic `glTF`, version 2, total length), a JSON chunk padded
  with spaces, a BIN chunk padded with zeros, every chunk on a 4-byte boundary.

**Instancing** is the reason the product structure is a graph: a bolt used forty times is one
component (`app/assembly/structure.py`), so here it is one mesh and forty nodes. `scene_for`
reads the occurrences and never duplicates vertex data.

**Levels of detail are one file per level.** Core glTF 2.0 has no LOD construct, and an
extension a viewer may not implement would make the finest level the only one it ever loads.

**The output is deterministic** -- sorted JSON keys, fixed separators, little-endian arrays --
so the same meshes give the same bytes, and a content-addressed store holds one copy.

**Not done here, and the plan says so**: Draco and meshopt compression (neither library is
installed, and the plan asks for both to be *measured* on real parts before either is chosen),
and storing the result in the media store under `cache_key`.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

from app.assembly.structure import ProductStructure
from app.dynamics.pose import Frame, is_rotation
from app.kernel.occt.tessellate import TriangleMesh

GLB_MAGIC: Final = 0x46546C67  # "glTF"
GLB_VERSION: Final = 2
CHUNK_JSON: Final = 0x4E4F534A  # "JSON"
CHUNK_BIN: Final = 0x004E4942  # "BIN\0"

FLOAT: Final = 5126
UNSIGNED_INT: Final = 5125
ARRAY_BUFFER: Final = 34962
ELEMENT_ARRAY_BUFFER: Final = 34963
TRIANGLES: Final = 4

#: Bumped whenever the bytes this module writes for the same meshes change, so a cached file
#: written by an older layout is never served as the current one.
LAYOUT_VERSION: Final = 1

MM_TO_M: Final = 0.001

#: Column-major: CAD (x, y, z) in mm -> glTF (x, z, -y) in m. Determinant +1e-9.
ROOT_MATRIX: Final[tuple[float, ...]] = (
    MM_TO_M, 0.0, 0.0, 0.0,
    0.0, 0.0, -MM_TO_M, 0.0,
    0.0, MM_TO_M, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)  # fmt: skip

ROOT_NODE_NAME: Final = "kryova:mm-z-up"


class GltfError(ValueError):
    """A scene that cannot be written, or bytes that are not a GLB this module can read."""


@dataclass(frozen=True)
class Placement:
    """One node: which mesh, where. `name` is the occurrence path, so a pick names a part."""

    name: str
    mesh: str
    frame: Frame


@dataclass(frozen=True)
class Scene:
    """Meshes held once, by name, and the placements that use them."""

    meshes: Mapping[str, TriangleMesh]
    placements: tuple[Placement, ...]

    def __post_init__(self) -> None:
        if not self.placements:
            raise GltfError("A scene with no placements draws nothing.")
        unknown = sorted({p.mesh for p in self.placements} - set(self.meshes))
        if unknown:
            raise GltfError(f"No mesh was given for {', '.join(unknown)}.")
        names = [p.name for p in self.placements]
        if len(set(names)) != len(names):
            raise GltfError("Two placements share a name, so a pick could not say which was hit.")
        for placement in self.placements:
            if not is_rotation(placement.frame.rotation):
                raise GltfError(
                    f"{placement.name}'s placement is not a proper rotation. A mirror would turn "
                    "its triangles inside out in every viewer; model the mirrored part instead."
                )

    @property
    def used_meshes(self) -> tuple[str, ...]:
        """Mesh names in the order their first placement appears: the file's mesh order."""
        seen: dict[str, None] = {}
        for placement in self.placements:
            seen.setdefault(placement.mesh, None)
        return tuple(seen)


def part_scene(name: str, mesh: TriangleMesh) -> Scene:
    """One part at the origin."""
    return Scene({name: mesh}, (Placement(name, name, Frame()),))


def scene_for(structure: ProductStructure, meshes: Mapping[str, TriangleMesh]) -> Scene:
    """Every leaf occurrence of `structure` as a node on its component's one mesh."""
    placements = tuple(
        Placement(occurrence.path, occurrence.component, occurrence.frame)
        for occurrence in structure.occurrences(leaves_only=True)
    )
    missing = sorted({p.mesh for p in placements} - set(meshes))
    if missing:
        raise GltfError(
            f"No mesh for component(s) {', '.join(missing)}. A machine drawn without them looks "
            "complete and is not, so tessellate every leaf component first."
        )
    return Scene(meshes, placements)


def _column_major(frame: Frame) -> list[float]:
    r = frame.rotation
    x, y, z = frame.origin_mm
    return [
        float(r[0]), float(r[3]), float(r[6]), 0.0,
        float(r[1]), float(r[4]), float(r[7]), 0.0,
        float(r[2]), float(r[5]), float(r[8]), 0.0,
        float(x), float(y), float(z), 1.0,
    ]  # fmt: skip


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def write_glb(scene: Scene, *, generator: str = "Kryova") -> bytes:
    """The scene as GLB bytes. Deterministic for the same scene."""
    binary = bytearray()
    buffer_views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []
    meshes: list[dict[str, Any]] = []
    mesh_index: dict[str, int] = {}

    def view(data: bytes, target: int) -> int:
        offset = len(binary)
        binary.extend(_pad(data, b"\x00"))
        buffer_views.append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target}
        )
        return len(buffer_views) - 1

    for name in scene.used_meshes:
        mesh = scene.meshes[name]
        if not np.all(np.isfinite(mesh.positions)):
            raise GltfError(f"{name}'s mesh holds a non-finite coordinate.")
        positions = np.ascontiguousarray(mesh.positions, dtype="<f4")
        if mesh.indices.min() < 0 or mesh.indices.max() >= mesh.vertex_count:
            raise GltfError(f"{name}'s mesh indexes a vertex it does not have.")
        if mesh.vertex_count > 0xFFFFFFFF:
            raise GltfError(f"{name}'s mesh has more vertices than a uint32 index can name.")
        indices = np.ascontiguousarray(mesh.indices.reshape(-1), dtype="<u4")
        position_view = view(positions.tobytes(), ARRAY_BUFFER)
        index_view = view(indices.tobytes(), ELEMENT_ARRAY_BUFFER)
        accessors.append(
            {
                "bufferView": position_view,
                "componentType": FLOAT,
                "count": int(positions.shape[0]),
                "type": "VEC3",
                "min": [float(v) for v in positions.min(axis=0)],
                "max": [float(v) for v in positions.max(axis=0)],
            }
        )
        accessors.append(
            {
                "bufferView": index_view,
                "componentType": UNSIGNED_INT,
                "count": int(indices.shape[0]),
                "type": "SCALAR",
            }
        )
        meshes.append(
            {
                "name": name,
                "primitives": [
                    {
                        "attributes": {"POSITION": len(accessors) - 2},
                        "indices": len(accessors) - 1,
                        "mode": TRIANGLES,
                    }
                ],
                "extras": {
                    "units": "mm",
                    "linear_deflection_mm": mesh.linear_deflection_mm,
                    "angular_deflection_rad": mesh.angular_deflection_rad,
                },
            }
        )
        mesh_index[name] = len(meshes) - 1

    nodes: list[dict[str, Any]] = [
        {"name": ROOT_NODE_NAME, "matrix": list(ROOT_MATRIX), "children": []}
    ]
    for placement in scene.placements:
        nodes[0]["children"].append(len(nodes))
        nodes.append(
            {
                "name": placement.name,
                "mesh": mesh_index[placement.mesh],
                "matrix": _column_major(placement.frame),
            }
        )

    document = {
        "asset": {"version": "2.0", "generator": generator},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(binary)}],
    }
    json_chunk = _pad(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
        b" ",
    )
    bin_chunk = bytes(binary)
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return b"".join(
        (
            struct.pack("<III", GLB_MAGIC, GLB_VERSION, total),
            struct.pack("<II", len(json_chunk), CHUNK_JSON),
            json_chunk,
            struct.pack("<II", len(bin_chunk), CHUNK_BIN),
            bin_chunk,
        )
    )


def read_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    """The JSON document and the BIN chunk of a GLB, with the container checked."""
    if len(data) < 20:
        raise GltfError("Too short to be a GLB file.")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise GltfError("Not a GLB file: the magic number is wrong.")
    if version != GLB_VERSION:
        raise GltfError(f"GLB version {version}; only version 2 is read.")
    if length != len(data):
        raise GltfError(f"The header says {length} bytes and there are {len(data)}.")
    document: dict[str, Any] | None = None
    binary = b""
    offset = 12
    while offset < length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        if offset % 4 or chunk_length % 4:
            raise GltfError("A chunk is not on a 4-byte boundary.")
        body = data[offset + 8 : offset + 8 + chunk_length]
        if chunk_type == CHUNK_JSON:
            document = json.loads(body.decode("utf-8"))
        elif chunk_type == CHUNK_BIN:
            binary = body
        offset += 8 + chunk_length
    if document is None:
        raise GltfError("The GLB has no JSON chunk.")
    return document, binary


def mesh_arrays(document: Mapping[str, Any], binary: bytes, mesh: int) -> tuple[np.ndarray, np.ndarray]:
    """Positions (n, 3) float32 in mm and indices (m, 3) uint32 of one mesh, read back."""
    primitive = document["meshes"][mesh]["primitives"][0]

    def array(accessor_index: int, dtype: str, width: int) -> np.ndarray:
        accessor = document["accessors"][accessor_index]
        buffer_view = document["bufferViews"][accessor["bufferView"]]
        start = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        count = accessor["count"]
        flat = np.frombuffer(binary, dtype=dtype, count=count * width, offset=start)
        return flat.reshape(-1, width) if width > 1 else flat

    positions = array(primitive["attributes"]["POSITION"], "<f4", 3)
    indices = array(primitive["indices"], "<u4", 1).reshape(-1, 3)
    return positions, indices


def cache_key(
    geometry_digest: str,
    *,
    linear_deflection_mm: float,
    angular_deflection_rad: float,
) -> str:
    """What a stored display mesh is keyed on: the geometry, the tolerances and this layout.

    The geometry digest is the caller's, and it must identify the *shape* -- a plan digest does
    not (`app/design/diff.py`: a rewritten note moves it and builds the same part), so passing
    one only costs cache hits, never correctness. Anything that changes the bytes is in the key.
    """
    if not geometry_digest.strip():
        raise GltfError("A display mesh cannot be keyed on an empty geometry digest.")
    for label, value in (
        ("linear deflection", linear_deflection_mm),
        ("angular deflection", angular_deflection_rad),
    ):
        if not (math.isfinite(value) and value > 0.0):
            raise GltfError(f"A {label} of {value} cannot key a display mesh.")
    payload = {
        "geometry": geometry_digest,
        "linear_deflection_mm": float(linear_deflection_mm).hex(),
        "angular_deflection_rad": float(angular_deflection_rad).hex(),
        "layout": LAYOUT_VERSION,
        "format": "glb",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def levels(scenes: Iterable[Scene], *, generator: str = "Kryova") -> tuple[bytes, ...]:
    """One GLB per level of detail, in the order given (finest first, by convention)."""
    written = tuple(write_glb(scene, generator=generator) for scene in scenes)
    if not written:
        raise GltfError("Give at least one level.")
    return written


def instance_counts(document: Mapping[str, Any]) -> dict[str, int]:
    """How many nodes draw each mesh, by mesh name -- what instancing saved."""
    names: Sequence[str] = [mesh["name"] for mesh in document["meshes"]]
    counts = dict.fromkeys(names, 0)
    for node in document["nodes"]:
        if "mesh" in node:
            counts[names[node["mesh"]]] += 1
    return counts


__all__ = [
    "GltfError",
    "LAYOUT_VERSION",
    "Placement",
    "ROOT_MATRIX",
    "Scene",
    "cache_key",
    "instance_counts",
    "levels",
    "mesh_arrays",
    "part_scene",
    "read_glb",
    "scene_for",
    "write_glb",
]
