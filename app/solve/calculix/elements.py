"""Which CalculiX element, and what CalculiX does with it — master plan 6.3.

The task's own words: *element strategy, informed by the CalculiX manual rather
than habit.* This module is that strategy in one place — the mapping from a mesh
in this codebase's vocabulary to an element type in CalculiX's, the section card
that must accompany it, and the facts about what ccx then does internally that
change how a result may be read.

Where the facts come from
-------------------------
**[M]** CalculiX CrunchiX USER'S MANUAL (Guido Dhondt), the element-type chapter
and the keyword sections `*SOLID SECTION`, `*SHELL SECTION`, `*BEAM SECTION`,
`*NODE FILE` and `*EL FILE`.

**There is no `ccx` on the machine this was written on, so not one line below has
been round-tripped through the real solver.** `steps.py` and `frd.py` say the
same about themselves. What follows is read from the manual; the first real run
is the measurement that turns it from documented into verified, and the tables
are tables — one constant per fact — so that run corrects a value rather than a
scattering of format strings.

The strategy, and the reason for each choice
--------------------------------------------
**Solids: C3D10 is the documented recommendation, and a tet4 mesh is still
written as C3D4.** [M] recommends the 10-node tetrahedron as the general-purpose
solid: it is stable and robust, where the linear tetrahedron locks in bending and
reads far too stiff. But silently promoting a tet4 mesh to a quadratic element
would answer a different question than the caller asked, and would do it without
the midside nodes that make the promotion mean anything. `element_type` writes
what was meshed; `app/mesh/primitives.promote_to_tet10` is how a caller asks for
the other thing.

**Shells and beams are expanded into solids by ccx before it solves.** This is
the fact the master plan singles out, and it is the one that surprises people.
CalculiX does not implement a shell formulation and a beam formulation with their
own degrees of freedom; it takes the 1-D or 2-D element, the section it was given,
and *builds a three-dimensional element out of it*, tying the expanded nodes back
to the original one with multiple-point constraints. `EXPANSIONS` below records
what each becomes.

Three consequences follow, and each one produces a plausible wrong number rather
than an error:

1. **One element through the thickness.** The through-thickness stress
   distribution is whatever a single element can represent — linear for the
   8-node incompatible-mode brick a `S4` or `B31` becomes, quadratic for the
   20-node one an `S8R` or `B32` becomes. That is enough for bending and it is
   not a through-thickness stress *analysis*: a peel stress at a bonded joint or
   a contact pressure across the wall is not a number this element can carry, and
   asking for one gets an answer anyway. `through_thickness_note` is the sentence
   that must travel with any stress read off an expanded element.

2. **The results file is written for the expanded model unless it is told not to
   be.** [M] gives `OUTPUT` on the output keywords as `2D` or `3D`, `3D` being
   the default, and `2D` meaning the results are stored at the nodes of the
   original 1-D or 2-D elements. The expanded model has *more nodes than the mesh
   that was submitted*, so a reader that indexes a `.frd` by the input mesh's own
   node numbers — which is exactly what `frd.displacements(frd, mesh.node_count)`
   does — silently reads a different model's answer, with every node number in
   range and nothing to complain about. `OUTPUT_REQUEST` is therefore written on
   every expanded model, and `output_parameter` is what decides it.

3. **A node of a shell or a beam has six degrees of freedom, not three.** A solid
   element has no rotational degree of freedom at all, so `*BOUNDARY` on a solid
   never mentions 4, 5 or 6. On an expanded element it must: a `clamp` that holds
   only the three translations is a **pin**, and a cantilever on a pin is a
   different structure, not a slightly softer one. That mapping lives in
   `app/solve/constraints.py` beside `Fixture`, because it is a statement about
   what a restraint *means* rather than about CalculiX.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.mesh.structural import BeamMesh, ShellMesh
from app.mesh.types import TetMesh
from app.solve.sections import BeamSection, Section, ShellSection, normalise_n1
from app.solve.types import Material, SolverError

#: The three families of element this backend writes. Named rather than spelled
#: out at each comparison, because "is this expanded?" is asked in four places.
SOLID: Final = "solid"
SHELL: Final = "shell"
BEAM: Final = "beam"

#: `OUTPUT=2D` on an output request stores results at the nodes of the original
#: 1-D or 2-D elements rather than at the expanded ones. [M] See consequence 2 in
#: the module docstring — without it the `.frd` describes a mesh the caller has
#: never seen, with node numbers that happen to be in range.
OUTPUT_REQUEST: Final = "OUTPUT=2D"


@dataclass(frozen=True)
class Expansion:
    """What CalculiX turns a 1-D or 2-D element into before solving.

    `into` and `nodes` are the mechanical facts; `consequence` is the sentence a
    reader of the result needs, and it is carried here rather than composed at
    the call site so that every caller says the same thing about the same
    element.
    """

    into: str
    nodes: int
    consequence: str


#: What each expanded element becomes. [M], the element-type chapter.
#:
#: The pattern is worth seeing whole: a linear element expands into an
#: incompatible-mode or wedge element that can carry linear bending through the
#: thickness, a quadratic one into a 20-node brick with reduced integration that
#: can carry quadratic. In every case it is **one element deep**, which is
#: consequence 1 above.
EXPANSIONS: Final[dict[str, Expansion]] = {
    "S3": Expansion(
        into="C3D6",
        nodes=6,
        consequence=(
            "a single 6-node wedge through the thickness, whose in-plane strain is "
            "constant — the coarsest of the shell elements, and it reads stiff in "
            "bending for the same reason a linear tetrahedron does"
        ),
    ),
    "S4": Expansion(
        into="C3D8I",
        nodes=8,
        consequence=(
            "a single 8-node brick with incompatible modes through the thickness, so "
            "bending is carried by the incompatible modes rather than by a through-"
            "thickness stress distribution"
        ),
    ),
    "S6": Expansion(
        into="C3D15",
        nodes=15,
        consequence=(
            "a single 15-node wedge through the thickness — quadratic in plane, and "
            "still one element deep"
        ),
    ),
    "S8R": Expansion(
        into="C3D20R",
        nodes=20,
        consequence=(
            "a single 20-node brick with reduced integration through the thickness; "
            "the through-thickness stress it can represent is quadratic, which is one "
            "element's worth however fine the surface mesh is"
        ),
    ),
    "B31": Expansion(
        into="C3D8I",
        nodes=8,
        consequence=(
            "a single 8-node brick with incompatible modes per element, so the cross "
            "section stays plane and the reported stress is the beam's, not a "
            "resolved distribution across the profile"
        ),
    ),
    "B32": Expansion(
        into="C3D20R",
        nodes=20,
        consequence=(
            "a single 20-node brick with reduced integration per element; the cross "
            "section is still one element, so a stress concentration at a wall corner "
            "of an RHS is not something this model contains"
        ),
    ),
    "B32R": Expansion(
        into="C3D20R",
        nodes=20,
        consequence=(
            "a single 20-node brick with reduced integration per element — the same "
            "expansion B32 gets, so the reading of the result is the same; the "
            "element differs only in being the one CalculiX will accept a hollow "
            "section on"
        ),
    ),
}


@dataclass(frozen=True)
class ElementChoice:
    """The element type for one mesh, and everything that follows from it."""

    calculix_type: str
    family: str
    nodes_per_element: int
    #: The keyword that must carry the section — `*SOLID SECTION`,
    #: `*SHELL SECTION`, `*BEAM SECTION`. An element written without its section
    #: card is refused by ccx, which is the one failure in this module that is
    #: loud rather than quiet.
    section_keyword: str
    #: Why this type and not another, in the manual's terms. Carried as data so
    #: it can be reported to a user asking what was solved, rather than living
    #: only in a comment nobody downstream can read.
    reason: str

    @property
    def expansion(self) -> Expansion | None:
        """What ccx expands this into, or None for a solid, which it does not."""
        return EXPANSIONS.get(self.calculix_type)

    @property
    def expanded(self) -> bool:
        return self.expansion is not None

    @property
    def dofs_per_node(self) -> int:
        """Six on an expanded element, three on a solid.

        A solid element has no rotational degree of freedom to hold, so a
        `*BOUNDARY` naming DOF 4 on one is meaningless. See consequence 3.
        """
        return 6 if self.expanded else 3

    def output_parameter(self) -> str:
        """`", OUTPUT=2D"` for an expanded element, and nothing for a solid.

        Returned with its leading separator so a caller appends it rather than
        deciding whether a comma is needed — which is the kind of decision that
        gets made two different ways in two places.
        """
        return f", {OUTPUT_REQUEST}" if self.expanded else ""

    def through_thickness_note(self) -> str:
        """The sentence that must travel with a stress read off this element.

        Empty for a solid: there is nothing to warn about, and a warning that
        fires on everything is a warning nobody reads.
        """
        expansion = self.expansion
        if expansion is None:
            return ""
        return (
            f"{self.calculix_type} is expanded by CalculiX into {expansion.into} "
            f"before it is solved: {expansion.consequence}. Stress through the "
            "thickness is therefore what one element can represent, whatever the "
            "surface mesh density — refine in plane and it does not improve. Model "
            "the region as a solid if the through-thickness distribution is the "
            "answer you need."
        )


#: Solids, by element order. [M] recommends C3D10 as the general-purpose solid;
#: C3D4 is written when a tet4 mesh is what was handed over, because promoting it
#: silently would answer a different question.
_SOLIDS: Final[dict[int, ElementChoice]] = {
    1: ElementChoice(
        calculix_type="C3D4",
        family=SOLID,
        nodes_per_element=4,
        section_keyword="*SOLID SECTION",
        reason=(
            "the mesh is linear. The 4-node tetrahedron locks in bending and reads too "
            "stiff; it is written because it is what was meshed, and "
            "mesh.promote_to_tet10 is how to ask for the recommended element instead"
        ),
    ),
    2: ElementChoice(
        calculix_type="C3D10",
        family=SOLID,
        nodes_per_element=10,
        section_keyword="*SOLID SECTION",
        reason=(
            "CalculiX's documented general-purpose solid: stable, robust, and free of "
            "the bending lock the linear tetrahedron suffers from"
        ),
    ),
}

#: Shells, by (triangular?, element order).
_SHELLS: Final[dict[tuple[bool, int], ElementChoice]] = {
    (True, 1): ElementChoice(
        calculix_type="S3",
        family=SHELL,
        nodes_per_element=3,
        section_keyword="*SHELL SECTION",
        reason="the mesh is linear triangles; S3 is the only shell element they can be",
    ),
    (True, 2): ElementChoice(
        calculix_type="S6",
        family=SHELL,
        nodes_per_element=6,
        section_keyword="*SHELL SECTION",
        reason="the mesh is quadratic triangles",
    ),
    (False, 1): ElementChoice(
        calculix_type="S4",
        family=SHELL,
        nodes_per_element=4,
        section_keyword="*SHELL SECTION",
        reason=(
            "the mesh is linear quadrilaterals. S4 expands to an incompatible-mode "
            "brick, which carries bending where a plain 8-node brick would lock"
        ),
    ),
    (False, 2): ElementChoice(
        calculix_type="S8R",
        family=SHELL,
        nodes_per_element=8,
        section_keyword="*SHELL SECTION",
        reason=(
            "the mesh is quadratic quadrilaterals. The reduced-integration form is "
            "chosen over S8 because full integration of a quadratic shell locks in "
            "the thin limit, which is the limit a shell is used in"
        ),
    ),
}

#: Beams, by element order.
_BEAMS: Final[dict[int, ElementChoice]] = {
    1: ElementChoice(
        calculix_type="B31",
        family=BEAM,
        nodes_per_element=2,
        section_keyword="*BEAM SECTION",
        reason=(
            "the mesh is two-node segments. B31 expands to an incompatible-mode brick, "
            "so a single element already carries linear bending — a frame member does "
            "not need to be subdivided to bend"
        ),
    ),
    2: ElementChoice(
        calculix_type="B32",
        family=BEAM,
        nodes_per_element=3,
        section_keyword="*BEAM SECTION",
        reason="the mesh is three-node segments",
    ),
}

#: The three-node beam with reduced integration. Same connectivity as `B32` and
#: the same expansion, so it is a substitution rather than a different mesh --
#: which is what makes `_HOLLOW_SECTIONS` below cheap to honour.
_B32R: Final = ElementChoice(
    calculix_type="B32R",
    family=BEAM,
    nodes_per_element=3,
    section_keyword="*BEAM SECTION",
    reason=(
        "the mesh is three-node segments and the profile is hollow — CalculiX "
        "carries a BOX or PIPE section on B32R only"
    ),
)

#: `SECTION=` names CalculiX will read **only** on a `B32R` element. Measured on
#: ccx 2.23 (2026-09-09), not read off the manual: every other combination is
#: refused at parse time with
#:
#:     *BEAM SECTION of type BOX can only be used for B32R elements.
#:     Element 1 is not a B32R element.
#:
#: swept across `B31`/`B32`/`B32R` x `RECT`/`CIRC`/`PIPE`/`BOX` and data lines of
#: one to eight values, so it is the section type that decides this and not the
#: value count. `RECT` solves on all three; `CIRC` parses everywhere but needs a
#: quadratic beam to expand.
_HOLLOW_SECTIONS: Final[frozenset[str]] = frozenset({"BOX", "PIPE"})


def choose_element(
    mesh: TetMesh | ShellMesh | BeamMesh, section: Section | None = None
) -> ElementChoice:
    """The CalculiX element for this mesh, with the reason it was chosen.

    One function rather than three, because the question a caller has is "what
    element is this mesh" and a caller that had to know the family already to ask
    would be doing the dispatch itself, in a place with no table to consult.

    **`section` is not decoration: for a beam the element and the profile are one
    choice, not two.** A hollow profile is readable by ccx only on `B32R`, so a
    caller that picked the element from the mesh alone and the section from the
    profile alone would write a deck naming a combination the solver refuses --
    which is what Kryova did until this was measured on the seat, and it made an
    RHS, the profile `sections.py` calls "the workhorse of a welded frame",
    unsolvable. Optional because a solid or a shell has no such coupling and most
    callers have no section to hand.
    """
    if isinstance(mesh, TetMesh):
        return _SOLIDS[mesh.element_order]
    if isinstance(mesh, ShellMesh):
        return _SHELLS[(mesh.is_triangular, mesh.element_order)]
    if isinstance(mesh, BeamMesh):
        if isinstance(section, BeamSection) and section.calculix_section_name in _HOLLOW_SECTIONS:
            return require_b32r(mesh, section)
        return _BEAMS[mesh.element_order]
    raise SolverError(  # pragma: no cover - the union makes this unreachable
        f"No CalculiX element is defined for a {type(mesh).__name__}."
    )


def require_b32r(mesh: BeamMesh, section: BeamSection) -> ElementChoice:
    """`B32R` for a hollow profile, or a refusal that says what to change.

    A linear beam mesh cannot carry one at all: `B32R` has three nodes and the
    mesh has two-node segments, so there is no element to substitute. Refused in
    words here rather than left to ccx, whose own message names the element type
    and not the profile -- true, and no help to somebody who asked for an RHS.
    """
    if mesh.element_order != 2:
        raise SolverError(
            f"A {section.calculix_section_name} profile needs a quadratic beam mesh. "
            "CalculiX reads a hollow section only on B32R, which has three nodes "
            f"per segment, and this mesh has {_BEAMS[mesh.element_order].nodes_per_element}. "
            "Mesh the frame with quadratic segments, or give the member a solid "
            "profile (RECT or CIRC) instead."
        )
    return _B32R


def require_section_for(mesh: ShellMesh | BeamMesh, section: Section) -> None:
    """Refuse a section that does not belong to the mesh it was handed with.

    A `BeamSection` on a shell mesh is not a type error at the boundary — both
    are `Section` — and it is not a CalculiX error either, because the deck
    writer would emit a `*BEAM SECTION` naming an element set full of `S4`
    elements, which ccx rejects with a message about the element type rather than
    about the mistake. Said properly here, once, before anything is written.
    """
    if isinstance(mesh, ShellMesh) and not isinstance(section, ShellSection):
        raise SolverError(
            "A shell mesh needs a ShellSection — the thickness the surface does not "
            f"carry. A {type(section).__name__} was given instead."
        )
    if isinstance(mesh, BeamMesh) and not isinstance(section, BeamSection):
        raise SolverError(
            "A beam mesh needs a BeamSection — the profile and the direction its axis "
            f"1 points. A {type(section).__name__} was given instead."
        )


def require_orientation(mesh: BeamMesh, section: BeamSection) -> None:
    """Refuse an `n1` that lies along a member.

    The cross section is oriented by a direction perpendicular to the member: the
    local 2-direction is `axis x n1`, so an `n1` parallel to the axis makes that
    cross product zero and the frame degenerate. It is refused rather than
    corrected, because there is no correct answer to guess at — a section has to
    be *put* somewhere, and every choice this function could make would be as
    arbitrary as the last.

    Checked per segment against the real geometry rather than once against a
    nominal direction: a frame's members do not all run the same way, and an `n1`
    that suits the posts is exactly the one that lies along the header.
    """
    unit = normalise_n1(section.n1)
    directions = mesh.directions()
    for index, axis in enumerate(directions):
        cross = (
            axis[1] * unit[2] - axis[2] * unit[1],
            axis[2] * unit[0] - axis[0] * unit[2],
            axis[0] * unit[1] - axis[1] * unit[0],
        )
        if sum(component * component for component in cross) <= _PARALLEL_TOLERANCE:
            raise SolverError(
                f"Segment {index + 1} runs along ({axis[0]:.4g}, {axis[1]:.4g}, "
                f"{axis[2]:.4g}) and n1 points the same way, so the cross section has "
                "no orientation about it — the local 2-direction would be a zero "
                "vector. Give an n1 across the member rather than along it; for a "
                "frame whose members run in several directions, one n1 cannot suit "
                "them all and the members need separate beam sections."
            )


#: How nearly parallel is parallel, as the squared magnitude of the cross product
#: of two unit vectors — i.e. `sin^2(angle)`. 1e-12 is an angle of about 6e-5
#: degrees, far below any orientation anybody means and far above the round-off
#: in normalising a direction.
_PARALLEL_TOLERANCE: Final = 1e-12


def section_lines(
    choice: ElementChoice,
    section: Section | None,
    *,
    element_set: str,
    material_name: str,
) -> list[str]:
    """The section card and its data lines for one element set.

    A solid takes no section object — its "section" is the whole element, which
    is why `*SOLID SECTION` has no data line at all.
    """
    if choice.family == SOLID:
        return [f"*SOLID SECTION, ELSET={element_set}, MATERIAL={material_name}"]

    if isinstance(section, ShellSection):
        card = f"*SHELL SECTION, ELSET={element_set}, MATERIAL={material_name}"
        if section.offset != 0.0:
            card += f", OFFSET={_number(section.offset)}"
        return [card, _data_line(section.calculix_data)]

    if isinstance(section, BeamSection):
        card = (
            f"*BEAM SECTION, ELSET={element_set}, MATERIAL={material_name}, "
            f"SECTION={section.calculix_section_name}"
        )
        # Two data lines, in this order: the dimensions, then the direction
        # cosines of the local 1-direction. [M] Swapping them is not a parse
        # error — both are rows of floats — it is a section of the wrong size
        # pointing the wrong way.
        return [
            card,
            _data_line(section.profile.calculix_data),
            _data_line(normalise_n1(section.n1)),
        ]

    raise SolverError(  # pragma: no cover - guarded by require_section_for
        f"A {choice.family} element needs a section and none was given."
    )


def element_rows(
    mesh: TetMesh | ShellMesh | BeamMesh,
) -> list[tuple[int, list[int]]]:
    """Each element as (1-based id, 1-based connectivity), in CalculiX order.

    For shells and beams this is `mesh.connectivity` renumbered and nothing else:
    `app/mesh/structural.py` defines its midside ordering to *be* CalculiX's, and
    says why. For a `TetMesh` it is not — gmsh's midside order differs from
    C3D10's in its last two slots — so that one keeps its permutation in
    `deck._element_rows` where the fact and the fix sit together.
    """
    return [
        (index + 1, [int(node) + 1 for node in row])
        for index, row in enumerate(mesh.connectivity)
    ]


def output_request_lines(choice: ElementChoice, *, node: str, element: str) -> list[str]:
    """`*NODE FILE` and `*EL FILE` with the right `OUTPUT` for this element.

    The whole reason this is a function: on an expanded element the default
    stores the results at nodes the caller has never seen, and every reader in
    `frd.py` indexes by the submitted mesh's node numbers. See consequence 2.
    """
    lines = [f"*NODE FILE{choice.output_parameter()}", node]
    if element:
        lines.extend([f"*EL FILE{choice.output_parameter()}", element])
    return lines


def _number(value: float) -> str:
    """`repr`-grade precision, matching `deck._number`.

    A thickness rounded to six figures is a different part, exactly as a node
    coordinate is.
    """
    return repr(float(value))


def _data_line(values: tuple[float, ...]) -> str:
    return ", ".join(_number(value) for value in values)


def describe(choice: ElementChoice, material: Material) -> str:
    """One paragraph on what is about to be solved, for a result's provenance.

    Decision 3 binds a result to what produced it, and "which element" is part of
    that: a frequency computed on S4 shells and one computed on C3D10 solids are
    not the same claim about the same part. Takes the material because the
    element and the material together are what a section card states.
    """
    head = (
        f"{choice.calculix_type} ({choice.family}) in {material.name}, chosen because "
        f"{choice.reason}."
    )
    note = choice.through_thickness_note()
    return f"{head} {note}".strip()


__all__ = [
    "BEAM",
    "EXPANSIONS",
    "OUTPUT_REQUEST",
    "SHELL",
    "SOLID",
    "ElementChoice",
    "Expansion",
    "choose_element",
    "describe",
    "element_rows",
    "output_request_lines",
    "require_orientation",
    "require_section_for",
    "section_lines",
]
