"""Reading CalculiX's `.frd` result file — master plan 6.1, the return half.

`deck.py` writes the question; this reads the answer. The two are separated
because they fail differently: a bad deck is refused by `ccx` with a line number,
and a misread result is a number that looks fine.

**This parser has not yet been run against output from a real `ccx`, and it says
so rather than implying otherwise.** It is written to the format documented in
the CalculiX/cgx manual, and its tests are built from that documentation. That is
enough to be worth having and not enough to be trusted: the same distinction the
codebase draws between a mock and a measurement. `describe()` exists for exactly
this — the first real run should call it, and it reports every block found and
every record it could not classify, with the bytes it saw. That is the pattern
`catia_describe_dialog` uses for unrecognised Win32 controls, and for the same
reason: the first session on real input should produce the answer rather than a
shrug.

**The format is fixed-width, and it has to be parsed that way.** A results record
is `" -1"`, then the node number in ten columns, then values in twelve. Splitting
on whitespace works right up until two values pack against each other —
`-1.23456E+00-2.34567E+00` is two numbers with no space between them, which is
ordinary in a file full of negative stresses, and a whitespace parser silently
reads it as one. That failure produces a shorter row, which then either raises
somewhere unrelated or, worse, lines up by accident.

**A `.frd` reports stress at nodes, not at elements.** `*EL FILE, S` writes
element results, but CalculiX extrapolates them to nodes and averages there
before writing. `SolveOutput.von_mises` is per element, so somebody has to bridge
that, and the bridge is a *choice* rather than a detail: averaging a node's
neighbours smooths the peak that a factor of safety is computed from. The choice
is not made here. This module returns what the file says — nodal values, labelled
as nodal — and `solver.py` decides, where the decision is visible.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from app.solve.types import SolverError

#: Column widths of a results data record: " -1", node number, then values.
_MARKER_WIDTH: Final = 3
_NODE_WIDTH: Final = 10
_VALUE_WIDTH: Final = 12

#: The block introducers this parser understands. Anything else is recorded by
#: `describe` rather than ignored, because an unknown block in a real file is a
#: finding and silence about it is how a format surprise becomes a wrong number.
_NODE_BLOCK: Final = "2C"
_ELEMENT_BLOCK: Final = "3C"
_RESULT_BLOCK: Final = "100C"

#: Entity names as CalculiX writes them in a `-4` header.
DISPLACEMENT: Final = "DISP"
STRESS: Final = "STRESS"

_ENTITY_RE: Final = re.compile(r"^\s*-4\s+(?P<name>\S+)\s+(?P<count>\d+)")
_COMPONENT_RE: Final = re.compile(r"^\s*-5\s+(?P<name>\S+)")


@dataclass
class Block:
    """One `-4` entity and the values it carried, keyed by node number."""

    name: str
    components: list[str] = field(default_factory=list)
    #: CalculiX node number (1-based, as written) -> that node's values.
    values: dict[int, list[float]] = field(default_factory=dict)

    def as_array(self, node_count: int) -> NDArray[np.float64]:
        """Values in 0-based node order, for a mesh of `node_count` nodes.

        Missing nodes are an error rather than a zero row: a results file that
        does not mention a node has not solved it, and a zero displacement is a
        perfectly plausible-looking value for a node that was never computed.
        """
        width = len(self.components)
        out = np.zeros((node_count, width), dtype=np.float64)
        seen = np.zeros(node_count, dtype=bool)
        for number, row in self.values.items():
            index = number - 1
            if not 0 <= index < node_count:
                raise SolverError(
                    f"{self.name} names node {number}, which is outside the mesh "
                    f"of {node_count} nodes. The deck and the results file "
                    "disagree about the model."
                )
            out[index] = row[:width]
            seen[index] = True
        if not seen.all():
            missing = int((~seen).sum())
            raise SolverError(
                f"{self.name} is missing {missing} of {node_count} nodes. A node "
                "with no result was not solved, and reading it as zero would be a "
                "displacement of zero rather than an absence."
            )
        return out


@dataclass
class FrdFile:
    """What one `.frd` contained."""

    blocks: dict[str, Block] = field(default_factory=dict)

    #: Records the parser could not classify, kept verbatim. Empty is the
    #: expected state; anything here is a finding for the first real run.
    unrecognised: list[str] = field(default_factory=list)

    def require(self, name: str) -> Block:
        if name not in self.blocks:
            found = ", ".join(sorted(self.blocks)) or "nothing"
            raise SolverError(
                f"The results file carries no {name} block; it has {found}. Check "
                f"that the deck asked for it — {name} needs its own output request."
            )
        return self.blocks[name]

    def describe(self) -> dict[str, Any]:
        """What was found, for a human meeting real output for the first time.

        Deliberately not a log line. The first `ccx` run on this machine is the
        measurement that turns this parser from documented to verified, and it
        should hand back everything it saw.
        """
        return {
            "blocks": {
                name: {
                    "components": block.components,
                    "nodes": len(block.values),
                    "first_node": min(block.values) if block.values else None,
                    "last_node": max(block.values) if block.values else None,
                }
                for name, block in sorted(self.blocks.items())
            },
            "unrecognised_records": len(self.unrecognised),
            "unrecognised_sample": self.unrecognised[:5],
        }


def _split_fixed(line: str) -> tuple[int, list[float]]:
    """One `-1` record: its node number and its values, by column.

    Fixed width rather than `split()`, because two values pack together with no
    separator when the second is negative, and whitespace parsing reads the pair
    as one number.
    """
    body = line[_MARKER_WIDTH:]
    node_text = body[:_NODE_WIDTH]
    try:
        node = int(node_text)
    except ValueError as bad:
        raise SolverError(
            f"Expected a node number in the ten columns after ' -1', got "
            f"{node_text!r}. The file may not be a CalculiX .frd, or may be "
            "written in a wider format than this parser knows."
        ) from bad

    values: list[float] = []
    rest = body[_NODE_WIDTH:]
    for start in range(0, len(rest), _VALUE_WIDTH):
        chunk = rest[start : start + _VALUE_WIDTH].strip()
        if not chunk:
            continue
        try:
            values.append(float(chunk))
        except ValueError as bad:
            raise SolverError(
                f"Could not read {chunk!r} as a number in a results record for "
                f"node {node}. Columns are {_VALUE_WIDTH} wide; a file written "
                "wider than that needs this parser widened deliberately."
            ) from bad
    return node, values


def _records(text: str) -> Iterator[str]:
    for line in text.splitlines():
        if line.strip():
            yield line


def parse_frd(text: str) -> FrdFile:
    """Read a `.frd`, keeping every nodal result block it declares."""
    out = FrdFile()
    current: Block | None = None

    for line in _records(text):
        stripped = line.strip()

        entity = _ENTITY_RE.match(line)
        if entity:
            current = Block(name=entity.group("name"))
            out.blocks[current.name] = current
            continue

        component = _COMPONENT_RE.match(line)
        if component and current is not None:
            name = component.group("name")
            # ALL is a summary row CalculiX appends to name the whole entity; it
            # is not a component and counting it would widen every row by one.
            if name.upper() != "ALL":
                current.components.append(name)
            continue

        if stripped.startswith("-1") and current is not None:
            node, values = _split_fixed(line)
            current.values[node] = values
            continue

        if stripped.startswith("-3"):
            current = None
            continue

        if stripped.startswith("-1") and current is None:
            # A -1 record outside any entity is a coordinate or element row from
            # the 2C/3C blocks, which we do not need: the mesh is ours already.
            continue

        if _is_structural(stripped):
            continue

        out.unrecognised.append(line)

    return out


def _is_structural(stripped: str) -> bool:
    """Block introducers and bookkeeping this parser deliberately steps over."""
    if stripped.startswith(("1C", "1U", "1P", "-2", "9999")):
        return True
    for block in (_NODE_BLOCK, _ELEMENT_BLOCK, _RESULT_BLOCK):
        if stripped.startswith(block):
            return True
    return stripped.upper().startswith(("PSTEP", "P STEP"))


def displacements(frd: FrdFile, node_count: int) -> NDArray[np.float64]:
    """(node_count, 3) displacements in mm, in the mesh's own node order."""
    block = frd.require(DISPLACEMENT)
    if len(block.components) < 3:
        raise SolverError(
            f"{DISPLACEMENT} declares {len(block.components)} components; three "
            "are needed for a displacement field. The deck must request U."
        )
    return block.as_array(node_count)[:, :3]


def nodal_stress_tensor(frd: FrdFile, node_count: int) -> NDArray[np.float64]:
    """(node_count, 6) stress in MPa, ordered SXX SYY SZZ SXY SYZ SZX.

    That order is CalculiX's own and is *not* the order every other code uses —
    Abaqus writes S12 S13 S23 where this writes SXY SYZ SZX. The components are
    read by the names the file declares rather than by position, so a build that
    reorders them is handled rather than silently transposed.
    """
    block = frd.require(STRESS)
    wanted = ["SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"]
    declared = [c.upper() for c in block.components]
    missing = [name for name in wanted if name not in declared]
    if missing:
        raise SolverError(
            f"{STRESS} is missing {', '.join(missing)}; it declares "
            f"{', '.join(declared) or 'nothing'}. Six components are needed for a "
            "stress tensor."
        )
    raw = block.as_array(node_count)
    order = [declared.index(name) for name in wanted]
    return raw[:, order]


def von_mises_from_tensor(tensor: NDArray[np.float64]) -> NDArray[np.float64]:
    """Von Mises from (n, 6) SXX SYY SZZ SXY SYZ SZX, in MPa.

    **The hand-written solver's own function, not a second copy of it.** It takes
    the same six components in the same order, so re-deriving the invariant here
    would be duplication of exactly the kind Decision 2 warns against — and it
    would make 6.5's oracle comparison weaker, not stronger: two implementations
    of one formula can drift, and a drift in the *invariant* would be read as a
    disagreement about the *stresses*, which is a different and much more
    alarming finding.

    What this wrapper adds is the component-order contract. CalculiX writes
    SXY SYZ SZX where Abaqus writes S12 S13 S23, so `nodal_stress_tensor` sorts
    by declared name before anything reaches here; this function's only claim is
    that what it receives is already in the order `linear_static` expects.
    """
    from app.solve.linear_static import von_mises

    return von_mises(tensor)


__all__ = [
    "DISPLACEMENT",
    "STRESS",
    "Block",
    "FrdFile",
    "displacements",
    "nodal_stress_tensor",
    "parse_frd",
    "von_mises_from_tensor",
]
