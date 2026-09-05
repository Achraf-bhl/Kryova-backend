"""Writing the CalculiX input deck — master plan 6.1 and 6.2.

An `.inp` deck is the whole interface to CalculiX. Decision 4 binds us to it:
CalculiX is GPL, so it is invoked as a **separate process across a file/CLI
boundary**, never linked. That is a licence obligation, and it is also the right
architecture — solvers crash, and a crash should kill a subprocess rather than
the API.

**The loads are not re-derived here.** `loads.assemble_loads` already turns a
`LoadCase` into a nodal force vector, distributing a force over its region by
*tributary area* so refining the mesh does not change what was applied. This
writes that same vector out as `*CLOAD`. Two reasons, and the second is the
important one. Re-deriving would duplicate the load vocabulary — the thing the
master plan says is the real asset and must not be rewritten. And 6.5 keeps the
hand-written solver as an **oracle**: any linear static case must agree between
the two, and a disagreement is a bug in the integration. That comparison only
means anything if both solvers were given *identical* loads. Derive them twice
and a disagreement no longer localises — it could be either end.

Four things here will produce a wrong answer quietly rather than an error, which
is why each has its own test.

**Numbering is 1-based.** CalculiX numbers nodes and elements from 1; numpy from
0. An off-by-one does not crash: it shifts every load and restraint onto its
neighbouring node and returns a plausible field.

**A C3D10's midside nodes are not in our order.** `TET10_EDGES` is gmsh's
ordering for its 10-node tet (element type 11) and is the codebase's single
source of truth. Abaqus — and therefore CalculiX — orders the last two the other
way round:

    slot        4        5        6        7        8        9
    ours     (0,1)    (1,2)    (0,2)    (0,3)    (2,3)    (1,3)
    C3D10    (0,1)    (1,2)    (0,2)    (0,3)    (1,3)    (2,3)

so the permutation is `[0, 1, 2, 3, 5, 4]`. Getting it wrong swaps two midside
nodes on every quadratic element. The element is still valid and still solves; it
is simply a differently-shaped element than the one that was meshed.

**Density leaves the mm-N-MPa system here, and this is the boundary where it is
allowed to.** The codebase is mm-N-MPa throughout and converts nothing — but a
deck in mm, N and MPa has *tonne* as its mass unit, so density must be written in
tonne/mm³: `kg/m³ x 1e-12`. Steel's 7870 becomes 7.87e-9, which is the number an
engineer recognises in an Abaqus deck. Omit the conversion and a gravity load is
wrong by twelve orders of magnitude; it will not look like a rounding error, it
will look like the model exploded.

**A coordinate written at low precision is a different part.** Everything numeric
goes out at `repr`-grade precision (17 significant digits), because a deck that
rounds node coordinates to six figures has silently re-meshed the model.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import TetMesh
from app.solve.loads import assemble_loads
from app.solve.selection import select_nodes
from app.solve.types import LoadCase, Material, SolverError

#: Our midside slot order -> CalculiX's. See the module docstring; verified
#: against `TET10_EDGES` by a test rather than trusted as a comment.
C3D10_MIDSIDE_ORDER: Final[tuple[int, ...]] = (0, 1, 2, 3, 5, 4)

#: The C3D10 edge ordering this permutation is claiming to produce, as corner
#: pairs. Written out so the test can check the claim instead of the constant.
C3D10_EDGES: Final[tuple[tuple[int, int], ...]] = (
    (0, 1),
    (1, 2),
    (0, 2),
    (0, 3),
    (1, 3),
    (2, 3),
)

#: kg/m3 -> tonne/mm3. The one conversion in this codebase, and it happens at the
#: boundary where the numbers stop being ours.
DENSITY_KG_M3_TO_TONNE_MM3: Final = 1e-12

#: CalculiX degree-of-freedom numbers for a solid element.
_DOF: Final = {"x": 1, "y": 2, "z": 3}

#: Below this a nodal force is not worth a deck line. Tributary-area
#: distribution leaves exact zeros on most nodes, and writing them all out
#: multiplies the deck size by the node count for no effect on the answer.
_FORCE_EPS: Final = 0.0


def _number(value: float) -> str:
    """A float that reads back as itself.

    `repr` on a Python float is the shortest string that round-trips, which is
    exactly the requirement: shorter loses geometry, longer is noise.
    """
    return repr(float(value))


def element_type(mesh: TetMesh) -> str:
    """C3D4 for a linear mesh, C3D10 for a quadratic one.

    Master plan 6.3: C3D10 is CalculiX's documented recommended solid. tet4 is
    still written when that is what was meshed — silently promoting it would
    change the answer the caller asked for.
    """
    return "C3D10" if mesh.midside is not None else "C3D4"


def _element_rows(mesh: TetMesh) -> Iterable[tuple[int, list[int]]]:
    """Each element as (1-based id, 1-based connectivity) in CalculiX order."""
    quadratic = mesh.midside is not None
    for index, corners in enumerate(mesh.tets):
        nodes = [int(n) + 1 for n in corners]
        if quadratic:
            assert mesh.midside is not None  # narrowed for the type checker
            row = mesh.midside[index]
            nodes.extend(int(row[slot]) + 1 for slot in C3D10_MIDSIDE_ORDER)
        yield index + 1, nodes


def _wrap(numbers: Sequence[int], per_line: int = 8) -> list[str]:
    """CalculiX's fixed-format reader stops at 16 fields on a line.

    A C3D10 has eleven numbers with its element id, so it must wrap. Kept
    conservative at eight because a deck that is one field over the limit fails
    with a parse error naming a line number and nothing else.
    """
    return [
        ", ".join(str(n) for n in numbers[i : i + per_line])
        for i in range(0, len(numbers), per_line)
    ]


def _fixture_sets(mesh: TetMesh, case: LoadCase) -> list[tuple[str, NDArray[np.int64]]]:
    """One node set per fixture, named for its position in the case.

    Named positionally rather than by the selector's contents: two fixtures can
    legitimately select overlapping regions with different held degrees of
    freedom, and merging them by name would silently drop one.
    """
    sets: list[tuple[str, NDArray[np.int64]]] = []
    for index, fixture in enumerate(case.fixtures):
        try:
            nodes = select_nodes(mesh, fixture.where)
        except SolverError as empty:
            # `select_nodes` already refuses an empty selection and its message
            # names the selector, which is the better half of the answer. What it
            # cannot know is *which* fixture asked — and with three of them on a
            # part that is the first thing you need. So the index is prepended
            # rather than the message replaced.
            raise SolverError(f"Fixture {index + 1}: {empty}") from empty
        sets.append((f"FIX{index + 1}", nodes))
    return sets


def write_deck(mesh: TetMesh, case: LoadCase, *, name: str = "Kryova") -> str:
    """The complete `.inp` for one linear static run.

    Returned as text rather than written to a path so it can be asserted against
    in a test with no filesystem — the same reason `execute.py` takes its runner
    as a callable.
    """
    material: Material = case.material
    lines: list[str] = [
        "*HEADING",
        f"{name} -- written by Kryova. Units: mm, N, MPa, tonne.",
        "*NODE, NSET=NALL",
    ]

    for index, (x, y, z) in enumerate(mesh.nodes):
        lines.append(f"{index + 1}, {_number(x)}, {_number(y)}, {_number(z)}")

    lines.append(f"*ELEMENT, TYPE={element_type(mesh)}, ELSET=EALL")
    for element_id, nodes in _element_rows(mesh):
        chunks = _wrap([element_id, *nodes])
        lines.append(chunks[0] + ("," if len(chunks) > 1 else ""))
        for extra in chunks[1:]:
            lines.append(extra)

    sets = _fixture_sets(mesh, case)
    for set_name, held_nodes in sets:
        lines.append(f"*NSET, NSET={set_name}")
        lines.extend(_wrap([int(n) + 1 for n in held_nodes]))

    lines.append(f"*MATERIAL, NAME={_material_name(material)}")
    lines.append("*ELASTIC, TYPE=ISO")
    lines.append(
        f"{_number(material.youngs_modulus_mpa)}, {_number(material.poissons_ratio)}"
    )
    lines.append("*DENSITY")
    lines.append(_number(material.density_kg_m3 * DENSITY_KG_M3_TO_TONNE_MM3))
    if material.thermal_expansion_per_k is not None:
        lines.append("*EXPANSION")
        lines.append(_number(material.thermal_expansion_per_k))
    lines.append(f"*SOLID SECTION, ELSET=EALL, MATERIAL={_material_name(material)}")

    lines.append("*STEP")
    lines.append("*STATIC")

    lines.append("*BOUNDARY")
    for (set_name, _), fixture in zip(sets, case.fixtures, strict=True):
        for dof in fixture.held:
            number = _DOF[dof]
            # start, end, value -- CalculiX takes a range, and a single dof is a
            # range of one. The explicit 0.0 matters: omitting it is legal and
            # means the same thing, but a reader diffing two decks should not
            # have to know that.
            lines.append(f"{set_name}, {number}, {number}, 0.0")

    forces, _warnings = assemble_loads(mesh, case.loads, material.density_kg_m3)
    cloads = _cload_lines(forces)
    if cloads:
        lines.append("*CLOAD")
        lines.extend(cloads)

    # Ask for exactly what `SolveOutput` needs and nothing else. A .frd carrying
    # every field CalculiX can write is large, slow to parse, and full of
    # quantities no caller reads.
    lines.append("*NODE FILE")
    lines.append("U")
    lines.append("*EL FILE")
    lines.append("S")
    lines.append("*END STEP")
    lines.append("")
    return "\n".join(lines)


def _material_name(material: Material) -> str:
    """CalculiX names cannot carry spaces or the characters a slug might.

    Upper-cased because the deck is read case-insensitively but written by
    convention in capitals, and a mixed-case name in an otherwise upper-case deck
    reads like a typo.
    """
    cleaned = "".join(c if c.isalnum() else "_" for c in material.name)
    return cleaned.upper()[:80] or "MATERIAL"


def _cload_lines(forces: NDArray[np.float64]) -> list[str]:
    """Nodal forces as `*CLOAD`, skipping the exact zeros.

    Tributary-area distribution leaves most of the mesh at zero; writing those
    out multiplies the deck by the node count and changes no answer.
    """
    lines: list[str] = []
    reshaped = forces.reshape(-1, 3)
    nonzero = np.argwhere(np.abs(reshaped) > _FORCE_EPS)
    for node_index, axis in nonzero:
        value = reshaped[node_index, axis]
        lines.append(f"{int(node_index) + 1}, {axis + 1}, {_number(value)}")
    return lines


__all__ = [
    "C3D10_EDGES",
    "C3D10_MIDSIDE_ORDER",
    "DENSITY_KG_M3_TO_TONNE_MM3",
    "element_type",
    "write_deck",
]
