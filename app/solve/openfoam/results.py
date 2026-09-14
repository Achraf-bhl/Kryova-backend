"""Reading what OpenFOAM wrote: fields, function-object tables and logs.

Everything here reads text, so it is tested on text a test wrote — and on the
files a real run wrote, where one is available.

Three things that give a wrong number quietly, each handled:

* **A field can be `uniform`.** A patch or an internal field with one value is
  written as `uniform 0` rather than a list, and a reader that only knows
  `nonuniform List<…>` finds no list and returns nothing, which reads as "no
  cells".
* **SIMPLE stops at `endTime` without saying it failed.** The log ends with `End`
  either way; only `SIMPLE solution converged in N iterations` says the residual
  targets were met. Its absence is non-convergence, and the run is refused on it.
* **The pressure is kinematic.** `p` is pressure over density, mm²/s² here. It
  becomes MPa once, in `kinematic_to_mpa`, where the density is applied:
  kg/m³ × mm²/s² = 1e-6 Pa = 1e-12 MPa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.solve.types import SolverError

#: kg/m³ × mm²/s² in MPa. The one conversion in this package.
KINEMATIC_TO_MPA: Final = 1e-12

_LIST = re.compile(r"internalField\s+nonuniform\s+List<(scalar|vector)>\s*(\d+)\s*\(")
_UNIFORM = re.compile(r"internalField\s+uniform\s+(\([^)]*\)|[-+0-9.eE]+)\s*;")
_CONVERGED = re.compile(r"SIMPLE solution converged in (\d+) iterations")
_TIME = re.compile(r"^Time = (\S+)$", re.MULTILINE)
_RESIDUAL = re.compile(r"Solving for (Ux|Uy|Uz|p|T), Initial residual = ([-+0-9.eE]+)")
_VERSION = re.compile(r"Version:\s*(\S+)")
_BUILD = re.compile(r"^Build\s*:\s*(\S+)", re.MULTILINE)
_CELLS = re.compile(r"^\s*cells:\s+(\d+)", re.MULTILINE)


def kinematic_to_mpa(kinematic_mm2_s2: float, density_kg_m3: float) -> float:
    return kinematic_mm2_s2 * density_kg_m3 * KINEMATIC_TO_MPA


def read_internal_field(text: str, cells: int | None = None) -> NDArray[np.float64]:
    """A volume field's cell values: (n,) for a scalar, (n, 3) for a vector."""
    listed = _LIST.search(text)
    if listed:
        kind, count = listed.group(1), int(listed.group(2))
        body = text[listed.end():]
        if kind == "vector":
            rows = re.findall(r"\(([^()]*)\)", body[: body.find("\n)")])
            values = np.array([[float(x) for x in row.split()] for row in rows[:count]], dtype=np.float64)
        else:
            values = np.array(body[: body.find(")")].split()[:count], dtype=np.float64)
        if len(values) != count:
            raise SolverError(f"A field file announced {count} values and held {len(values)}.")
        return values
    uniform = _UNIFORM.search(text)
    if uniform and cells is not None:
        token = uniform.group(1)
        if token.startswith("("):
            vector = np.array(token.strip("()").split(), dtype=np.float64)
            return np.tile(vector, (cells, 1))
        return np.full(cells, float(token), dtype=np.float64)
    raise SolverError(
        "A field file holds no internal field this reader understands (neither a "
        "nonuniform list nor a uniform value with a known cell count)."
    )


def last_value(table: str) -> float:
    """The final row's value from a `surfaceFieldValue.dat`."""
    rows = [line.split() for line in table.splitlines() if line.strip() and not line.startswith("#")]
    if not rows:
        raise SolverError("A function-object table holds no rows: the solver wrote no iteration.")
    return float(rows[-1][-1])


def patch_area(table: str) -> float:
    """The patch area a `surfaceFieldValue.dat` states in its header, mm².

    This is the area of the **snapped mesh's** faces, which is what a flux was
    applied over — not the drawn surface's, which a snapped wall only approximates
    (4706 mm² against 4712 on the test pipe).
    """
    found = re.search(r"^#\s*Area\s*:\s*([-+0-9.eE]+)", table, re.MULTILINE)
    if found is None:
        raise SolverError("A function-object table states no patch area in its header.")
    return float(found.group(1))


@dataclass(frozen=True)
class SolverLog:
    converged: bool
    iterations: int
    #: Initial residual of each equation at the last iteration.
    residuals: dict[str, float]
    #: `"2412 (build _45e7c4a0-20241224)"` — the banner's version and the build id,
    #: because two builds of one version are two solvers.
    version: str | None


def read_solver_log(text: str) -> SolverLog:
    converged = _CONVERGED.search(text)
    steps = list(_TIME.finditer(text))
    residuals: dict[str, float] = {}
    # The last iteration's block, found by the match itself: a search for a
    # preceding newline misses a log whose first line is the only `Time =`.
    last_block = text[steps[-1].start():] if steps else text
    for name, value in _RESIDUAL.findall(last_block):
        residuals[name] = float(value)
    version = _VERSION.search(text)
    build = _BUILD.search(text)
    iterations = int(converged.group(1)) if converged else (int(float(steps[-1].group(1))) if steps else 0)
    return SolverLog(
        converged=converged is not None,
        iterations=iterations,
        residuals=residuals,
        version=(
            f"{version.group(1)} (build {build.group(1)})" if version and build
            else version.group(1) if version else None
        ),
    )


def read_raw_surface(text: str) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """A `surfaceFormat raw` face-data file: (face centres (n, 3), values (n,)).

    The header says how many faces it holds (`# T  FACE_DATA 10560`), and a file
    holding a different number is refused rather than read short.
    """
    announced = re.search(r"FACE_DATA\s+(\d+)", text)
    rows = [line.split() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    data = np.array(rows, dtype=np.float64) if rows else np.empty((0, 4))
    if data.ndim != 2 or data.shape[1] != 4:
        raise SolverError("A surface data file does not hold rows of x, y, z and one value.")
    if announced is None or int(announced.group(1)) != len(data):
        raise SolverError(
            f"A surface data file announced {announced.group(1) if announced else 'no'} faces "
            f"and held {len(data)}."
        )
    return data[:, :3], data[:, 3]


def read_cell_count(check_mesh_log: str) -> int:
    found = _CELLS.search(check_mesh_log)
    if not found:
        raise SolverError("checkMesh reported no cell count; the mesh was not written.")
    return int(found.group(1))


def mesh_ok(check_mesh_log: str) -> bool:
    return "Mesh OK." in check_mesh_log


__all__ = [
    "KINEMATIC_TO_MPA",
    "SolverLog",
    "kinematic_to_mpa",
    "last_value",
    "mesh_ok",
    "patch_area",
    "read_cell_count",
    "read_internal_field",
    "read_raw_surface",
    "read_solver_log",
]
