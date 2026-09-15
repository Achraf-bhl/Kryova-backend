"""What the viewer is sent for a stored geometry version -- P6.1's levels, key and store.

`tessellate.py` makes triangles and `gltf.py` packages them; this decides **which** triangles a
level holds and **when** they are made, which is once per geometry and level, ever.

**A level is relative to the part's size, and the key does not need the part.** A fixed
chordal deflection is wrong at both ends: 0.05 mm is invisible detail on a 3 m frame and a
visibly faceted bore on a 6 mm pin. So a level's linear deflection is a fraction of the bounding
box diagonal, the same choice `app/render/project.py` makes for curve deflection. That makes the
absolute deflection a property of the shape, and reading the shape is the expensive part a cache
exists to skip. So the key is the **stored file's sha256** plus the level's *definition*
(fraction, angle) plus the GLB layout version -- all known before the file is opened. The same
bytes always give the same shape, so the same key always names the same GLB.

**The angular deflection coarsens with the level, not only the linear one.** The tighter of the
two decides a mesh's detail (measured in `tessellate.levels_of_detail`): at 0.05 rad every level
of a cylinder came out identical. The three levels below were chosen on that rule, not tuned on
a real machine, and the plan's measurement on real Kryova parts is still owed.

**The GLB is built in memory.** `write_glb` returns bytes; a hit is streamed from the store in
chunks, a miss is not. That is fine for a part and is not the answer for a 2,000-part machine,
which needs P6.2's per-component streaming anyway.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Final

from app.kernel.errors import KernelError
from app.kernel.occt.binding import require, symbol
from app.kernel.occt.tessellate import tessellate
from app.render.gltf import LAYOUT_VERSION, part_scene, write_glb

GLB_CONTENT_TYPE: Final = "model/gltf-binary"


@dataclass(frozen=True)
class DisplayLevel:
    """One level of detail: linear deflection as a fraction of the bounding-box diagonal."""

    relative_deflection: float
    angular_deflection_rad: float

    def __post_init__(self) -> None:
        if not (math.isfinite(self.relative_deflection) and 0.0 < self.relative_deflection < 1.0):
            raise KernelError(f"A relative deflection of {self.relative_deflection} is outside (0, 1).")
        if not (math.isfinite(self.angular_deflection_rad) and 0.0 < self.angular_deflection_rad < math.pi):
            raise KernelError(f"An angular deflection of {self.angular_deflection_rad} rad is outside (0, π).")


#: Finest first. Level 0 is what a close-up needs, level 2 what a machine overview needs.
LEVELS: Final[tuple[DisplayLevel, ...]] = (
    DisplayLevel(relative_deflection=0.0002, angular_deflection_rad=0.1),
    DisplayLevel(relative_deflection=0.001, angular_deflection_rad=0.35),
    DisplayLevel(relative_deflection=0.005, angular_deflection_rad=1.0),
)


def level(index: int) -> DisplayLevel:
    if not 0 <= index < len(LEVELS):
        raise KernelError(f"There is no display level {index}; the levels are 0 to {len(LEVELS) - 1}.")
    return LEVELS[index]


def display_key(geometry_sha256: str, display_level: DisplayLevel) -> str:
    """The stored GLB's key: the file's bytes, the level's definition, and this layout."""
    if len(geometry_sha256) != 64 or any(c not in "0123456789abcdef" for c in geometry_sha256):
        raise KernelError("A display mesh is keyed on the stored file's sha256, as 64 hex digits.")
    payload = {
        "geometry_sha256": geometry_sha256,
        "relative_deflection": float(display_level.relative_deflection).hex(),
        "angular_deflection_rad": float(display_level.angular_deflection_rad).hex(),
        "layout": LAYOUT_VERSION,
        "format": "glb",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def stored_filename(key: str) -> str:
    """The media row's filename, which is how a hit is found: `display-<key>.glb`."""
    return f"display-{key}.glb"


def diagonal_mm(shape: Any) -> float:
    require()
    box = symbol("Bnd_Box")()
    box.SetGap(0.0)
    symbol("BRepBndLib").Add_s(shape, box, True)
    if box.IsVoid():
        raise KernelError("This shape has no extent to draw.")
    x0, y0, z0, x1, y1, z1 = box.Get()
    return math.dist((x0, y0, z0), (x1, y1, z1))


def display_glb(shape: Any, display_level: DisplayLevel, *, name: str) -> tuple[bytes, dict[str, Any]]:
    """The GLB for one level, and what it was made with (for the media row's `meta`)."""
    diagonal = diagonal_mm(shape)
    linear = display_level.relative_deflection * diagonal
    mesh = tessellate(
        shape,
        linear_deflection_mm=linear,
        angular_deflection_rad=display_level.angular_deflection_rad,
    )
    data = write_glb(part_scene(name, mesh))
    return data, {
        "diagonal_mm": diagonal,
        "linear_deflection_mm": linear,
        "angular_deflection_rad": display_level.angular_deflection_rad,
        "relative_deflection": display_level.relative_deflection,
        "triangles": mesh.triangle_count,
        "vertices": mesh.vertex_count,
        "layout": LAYOUT_VERSION,
    }


__all__ = [
    "DisplayLevel",
    "GLB_CONTENT_TYPE",
    "LEVELS",
    "diagonal_mm",
    "display_glb",
    "display_key",
    "level",
    "stored_filename",
]
