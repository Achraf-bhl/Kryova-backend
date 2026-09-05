"""Building a `.frd` by hand, for tests on both sides of the parser.

Extracted from `test_solver_calculix_frd.py` when a second suite needed the same
builders — `test_solver_calculix_solver.py` drives a fake `ccx` that has to
*produce* a results file, and a second copy of the record layout would be a
second thing to get wrong. The layout is the whole subject of these tests: two
copies of it could agree with each other and disagree with CalculiX.

**Written to the documented format, not captured from a real run.** No `ccx`
binary exists on this machine yet, and the module under test says the same about
itself. A fixture built from the same documentation as the code can be wrong in
the same direction, so the tests that use these pin *behaviour that must hold
whatever the format turns out to be* — fixed-width reading, components matched by
name, a missing node refused rather than zeroed — rather than the bytes.
"""

from __future__ import annotations

from collections.abc import Sequence

#: The one-line file header every `.frd` opens with.
HEADER = "    1C\n"

#: The component names CalculiX declares for a stress block, in its own order.
#: Not the order every code uses — Abaqus writes S12 S13 S23 where this writes
#: SXY SYZ SZX — which is exactly why the parser matches them by name.
STRESS_COMPONENTS = ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")


def record(node: int, *values: float) -> str:
    """One ` -1` data record at the documented column widths.

    Ten columns for the node number, twelve per value, and no separators. The
    width is the point: two negative values pack against each other with no
    space between them, which a whitespace parser reads as one number.
    """
    out = " -1" + f"{node:10d}"
    for value in values:
        out += f"{value:12.5E}"
    return out


def disp_block(rows: Sequence[tuple[int, float, float, float]]) -> str:
    """A DISP block for the given `(node, dx, dy, dz)` rows."""
    lines = [
        "    1PSTEP                         1",
        "  100CL  101  1.00000E+00" + f"{len(rows):12d}",
        " -4  DISP        4    1",
        " -5  D1          1    2    1    0",
        " -5  D2          1    2    2    0",
        " -5  D3          1    2    3    0",
        " -5  ALL         1    2    0    0    1ALL",
    ]
    lines += [record(n, x, y, z) for n, x, y, z in rows]
    lines.append(" -3")
    return "\n".join(lines)


def stress_block(rows: Sequence[tuple[int, tuple[float, ...]]]) -> str:
    """A STRESS block for the given `(node, six components)` rows."""
    lines = [" -4  STRESS      6    1"]
    for index, name in enumerate(STRESS_COMPONENTS, start=1):
        # The trailing pair is the component's (row, column) in the tensor;
        # CalculiX writes them and the parser ignores them, but a fixture that
        # omitted them would be testing a shorter line than the real one.
        row, column = _tensor_position(index)
        lines.append(f" -5  {name:<12s}1    4{row:5d}{column:5d}")
    lines += [record(n, *values) for n, values in rows]
    lines.append(" -3")
    return "\n".join(lines)


def frd_text(
    displacements: Sequence[tuple[int, float, float, float]],
    stresses: Sequence[tuple[int, tuple[float, ...]]],
) -> str:
    """A whole `.frd` carrying one displacement and one stress block.

    What a deck asking for `U` and `S` gets back, and therefore what a fake
    solver has to write for the pipeline above it to be exercised at all.
    """
    return HEADER + disp_block(displacements) + "\n" + stress_block(stresses) + "\n"


def uniform_stress(node_count: int, sigma_zz: float) -> list[tuple[int, tuple[float, ...]]]:
    """Every node at the same uniaxial stress along z — a bar in tension.

    The state where a nodal field and an element field are the same field, which
    is what makes it the case an oracle comparison can be posed in.
    """
    return [(node + 1, (0.0, 0.0, sigma_zz, 0.0, 0.0, 0.0)) for node in range(node_count)]


def _tensor_position(component: int) -> tuple[int, int]:
    """Where the nth CalculiX stress component sits in the 3x3 tensor."""
    return ((1, 1), (2, 2), (3, 3), (1, 2), (2, 3), (3, 1))[component - 1]


__all__ = [
    "HEADER",
    "STRESS_COMPONENTS",
    "disp_block",
    "frd_text",
    "record",
    "stress_block",
    "uniform_stress",
]
