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
rounds node coordinates to six figures has silently re-meshed the model — with
one bound on it that CalculiX imposes and `_number` enforces: a numeric field
wider than twenty characters is truncated by ccx's reader and the deck is
refused. The handful of values that cannot fit are narrowed to 13 significant
digits, which is still far finer than any mesh this writes.

**A temperature change that is not written is solved as isothermal, in silence.**
This is 6.4's half of the file and it was the live defect until 2026-09-08:
`LoadCase.delta_t_k` reached the in-house solver, which assembles a thermal load
from it, and reached CalculiX not at all. The same case therefore returned
`sigma = -E alpha dT` from one solver and zero from the other, with no error on
either side — and 6.5's oracle would have called that a bug in the deck writer,
correctly, had anything ever run a thermal case through both. Three facts make
the written form mean what `app/solve/thermal.py` means by it:

- **[M] `*INITIAL CONDITIONS, TYPE=TEMPERATURE`** sets the temperature the part
  starts at. Written explicitly as 0.0 rather than left to ccx's initialiser,
  because the number it holds is one half of a subtraction and the other half is
  four lines further down.
- **[M] `*EXPANSION`, parameter `ZERO`**: "temperature at which the thermal
  strains are zero (default: 0)". Also written explicitly, and for the same
  reason: `ZERO` and the initial condition must agree or `delta_t_k` stops being
  a *change*, and two numbers that must agree belong where a reader can see both.
- **[M] `*TEMPERATURE`** inside the `*STATIC` step then names the temperature the
  part is *at*. With the two above pinned to zero, `alpha * (T - ZERO)` is
  `alpha * delta_t_k` exactly, which is what `thermal.thermal_strain` computes.

And the trap that makes the refusal necessary: **a material with no expansion
coefficient writes no `*EXPANSION` card, and a deck with `*TEMPERATURE` and no
`*EXPANSION` heats the part and reports zero thermal stress.** No error, no
warning — the temperature is applied to a material that does not respond to it.
`write_deck` refuses that case by name instead, which is what `thermal_strain`
already does on the in-house path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.structural import BeamMesh, ShellMesh
from app.mesh.types import TetMesh
from app.solve.calculix.elements import (
    choose_element,
    element_rows,
    output_request_lines,
    require_orientation,
    require_section_for,
    section_lines,
)
from app.solve.constraints import (
    STRUCTURAL_DOFS,
    local_dofs,
    require_restrained,
)
from app.solve.loads import assemble_loads
from app.solve.sections import BeamSection, Section
from app.solve.selection import PointCloud, select_nodes
from app.solve.types import Fixture, LoadCase, Material, SolverError

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

#: The element set every element of a single-section model belongs to.
ALL_ELEMENTS: Final = "EALL"

#: The node set every node of the model belongs to. Written on `*NODE` and named
#: again by the temperature cards, which apply to the whole part.
ALL_NODES: Final = "NALL"

#: The temperature at which the thermal strain is zero, and the temperature the
#: part starts at. One number rather than two because they are the same fact:
#: `delta_t_k` is a *change*, so whatever it is measured from must also be what
#: `*EXPANSION, ZERO=` is measured from. Zero is chosen because it makes the
#: subtraction the identity and the deck readable.
REFERENCE_TEMPERATURE: Final = 0.0

#: Below this a nodal force is not worth a deck line. Tributary-area
#: distribution leaves exact zeros on most nodes, and writing them all out
#: multiplies the deck size by the node count for no effect on the answer.
_FORCE_EPS: Final = 0.0


#: CalculiX reads every numeric field with a Fortran `f20.0`, so a token wider
#: than this is truncated mid-number and the line fails to parse. It does not
#: complain about the width: `calinput` reports `*ERROR reading *NODE` with an
#: empty card image, which names neither the node nor the column.
_CCX_FIELD_WIDTH: Final = 20


def _number(value: float) -> str:
    """A float that reads back as itself, in a field CalculiX can read.

    `repr` on a Python float is the shortest string that round-trips, which is
    the right default: shorter loses geometry, longer is noise. But a
    full-precision double runs to 22 or 23 characters
    (`-7.819982591610898e-15`), and CalculiX truncates every numeric field at
    `_CCX_FIELD_WIDTH`, so `repr` alone writes decks it then refuses.

    **Only a real import produces such values, which is why the offline suite
    never saw this.** The meshes in the tests come from exact primitives whose
    coordinates are `5.0` and `200.0`; a STEP file that has been through CATIA
    and OCCT carries the accumulated noise of the transfer — `5.0` arrives as
    `-4.999999999999996` and a nominal zero as `-7.993605777301127e-15` — and
    those are the tokens that overflow. Measured at gate G1 (2026-09-08): the
    ccx-vs-`linear_static` oracle could not run at all on the cantilever the
    product had just built, and reported UNMEASURED.

    Falling back through decreasing precision rather than formatting everything
    the same way, because the deck is read by people when a solve goes wrong:
    `5.0` should stay `5.0` and not become `5.000000000000e+00`. Only the
    values that cannot fit are rewritten, and 12 decimal places of mantissa is
    still far more than millimetre geometry can carry.
    """
    text = repr(float(value))
    if len(text) <= _CCX_FIELD_WIDTH:
        return text
    for places in range(12, 5, -1):
        candidate = f"{float(value):.{places}e}"
        if len(candidate) <= _CCX_FIELD_WIDTH:
            return candidate
    # Unreachable for any finite double: `%.6e` of the widest is 13 characters
    # with sign and a three-digit exponent. Kept so the function is total.
    return f"{float(value):.6e}"


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


def _fixture_sets(
    mesh: PointCloud, fixtures: Sequence[Fixture]
) -> list[tuple[str, NDArray[np.int64]]]:
    """One node set per fixture, named for its position in the case.

    Named positionally rather than by the selector's contents: two fixtures can
    legitimately select overlapping regions with different held degrees of
    freedom, and merging them by name would silently drop one.
    """
    sets: list[tuple[str, NDArray[np.int64]]] = []
    for index, fixture in enumerate(fixtures):
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


def write_model(
    mesh: TetMesh,
    material: Material,
    fixtures: Sequence[Fixture],
    *,
    name: str = "Kryova",
) -> tuple[list[str], list[str]]:
    """The model half of a deck, and the restraints that go with it.

    Returns `(model lines, boundary data lines)` — everything from `*HEADING`
    down to `*SOLID SECTION`, including the per-fixture `*NSET` blocks, and
    separately the `*BOUNDARY` data rows for those sets **without the
    `*BOUNDARY` card itself**.

    Split that way because the card belongs to the *step* and the sets belong to
    the *model*. A static step, a `*FREQUENCY` step and a `*BUCKLE` step all
    restrain the same nodes, but each writes its own `*BOUNDARY` in its own
    place, and a modal case has no `LoadCase` at all to hand a combined writer.
    Taking the mesh, the material and the fixtures rather than a `LoadCase` is
    what makes it usable by all three: those three things are what a *model* is,
    and loads are what a *step* adds to it.

    `write_deck` is built on this rather than keeping its own copy. A second node
    writer, or a second DOF table, is how two decks quietly start describing
    different models — and 6.5's oracle would then localise a disagreement to
    the deck writer rather than to the physics, which is the one thing that
    comparison exists to rule out.
    """
    lines = _heading_and_nodes(mesh.nodes, name=name)

    lines.append(f"*ELEMENT, TYPE={element_type(mesh)}, ELSET={ALL_ELEMENTS}")
    lines.extend(_element_lines(_element_rows(mesh)))

    sets = _fixture_sets(mesh, fixtures)
    lines.extend(_nset_lines(sets))
    lines.extend(_material_lines(material))
    lines.append(
        f"*SOLID SECTION, ELSET={ALL_ELEMENTS}, MATERIAL={_material_name(material)}"
    )

    return lines, _boundary_data(sets, fixtures, rotations=False)


def _heading_and_nodes(
    nodes: NDArray[np.float64], *, name: str = "Kryova"
) -> list[str]:
    """`*HEADING` and the `*NODE` block. The one node writer in this backend.

    Shared by the solid and the frame model writers rather than copied. A second
    node writer is how two decks quietly start describing different models, and
    it would put 6.5's oracle in the position of localising a disagreement to
    the deck writer instead of to the physics — the one thing that comparison
    exists to rule out.
    """
    lines = [
        "*HEADING",
        f"{name} -- written by Kryova. Units: mm, N, MPa, tonne.",
        f"*NODE, NSET={ALL_NODES}",
    ]
    for index, (x, y, z) in enumerate(nodes):
        lines.append(f"{index + 1}, {_number(x)}, {_number(y)}, {_number(z)}")
    return lines


def _element_lines(rows: Iterable[tuple[int, list[int]]]) -> list[str]:
    """`*ELEMENT` data rows, wrapped and continued where they must be."""
    lines: list[str] = []
    for element_id, nodes in rows:
        chunks = _wrap([element_id, *nodes])
        lines.append(chunks[0] + ("," if len(chunks) > 1 else ""))
        lines.extend(chunks[1:])
    return lines


def _nset_lines(sets: Sequence[tuple[str, NDArray[np.int64]]]) -> list[str]:
    lines: list[str] = []
    for set_name, held_nodes in sets:
        lines.append(f"*NSET, NSET={set_name}")
        lines.extend(_wrap([int(n) + 1 for n in held_nodes]))
    return lines


def _material_lines(material: Material) -> list[str]:
    lines = [
        f"*MATERIAL, NAME={_material_name(material)}",
        "*ELASTIC, TYPE=ISO",
        f"{_number(material.youngs_modulus_mpa)}, {_number(material.poissons_ratio)}",
        "*DENSITY",
        _number(material.density_kg_m3 * DENSITY_KG_M3_TO_TONNE_MM3),
    ]
    if material.thermal_expansion_per_k is not None:
        # ZERO is written even though 0.0 is ccx's own default: it is one half of
        # the subtraction `*TEMPERATURE` completes, and the other half is the
        # `*INITIAL CONDITIONS` block. See the module docstring.
        lines.append(f"*EXPANSION, ZERO={_number(REFERENCE_TEMPERATURE)}")
        lines.append(_number(material.thermal_expansion_per_k))
    return lines


def _boundary_data(
    sets: Sequence[tuple[str, NDArray[np.int64]]],
    fixtures: Sequence[Fixture],
    *,
    rotations: bool,
) -> list[str]:
    """`*BOUNDARY` data rows for each fixture's node set, without the card.

    Which degrees of freedom a fixture holds is `constraints.local_dofs`'
    question, not this module's — a clamp holding all six on a beam and three on
    a solid is a statement about what the word means, and it is read here rather
    than restated so that the deck and the pre-solve restraint check can never
    disagree about what was held.
    """
    rows: list[str] = []
    for (set_name, _), fixture in zip(sets, fixtures, strict=True):
        for local in local_dofs(fixture, rotations=rotations):
            number = local + 1
            # start, end, value -- CalculiX takes a range, and a single dof is a
            # range of one. The explicit 0.0 matters: omitting it is legal and
            # means the same thing, but a reader diffing two decks should not
            # have to know that.
            rows.append(f"{set_name}, {number}, {number}, 0.0")
    return rows


def cload_data_lines(forces: NDArray[np.float64]) -> list[str]:
    """Nodal forces as `*CLOAD` data rows, without the card.

    Public because a buckling step carries its own load and has to write these
    after `*BUCKLE` rather than after `*STATIC`. Re-deriving the 1-based node
    numbering and the axis mapping at the second call site is exactly the
    duplication that would let 6.5's oracle localise a disagreement to the deck
    writer instead of to the physics.
    """
    lines: list[str] = []
    reshaped = forces.reshape(-1, 3)
    nonzero = np.argwhere(np.abs(reshaped) > _FORCE_EPS)
    for node_index, axis in nonzero:
        value = reshaped[node_index, axis]
        lines.append(f"{int(node_index) + 1}, {axis + 1}, {_number(value)}")
    return lines


def write_frame_model(
    mesh: ShellMesh | BeamMesh,
    material: Material,
    fixtures: Sequence[Fixture],
    section: Section,
    *,
    name: str = "Kryova",
) -> tuple[list[str], list[str]]:
    """`write_model` for a shell or beam mesh — master plan 6.3.

    Same contract, same return: `(model lines, boundary data lines)`, the
    boundary rows without their card, so a static, a `*FREQUENCY` and a `*BUCKLE`
    step can each place their own. What differs is only what a shell and a beam
    *are*: the element comes from `elements.choose_element`, the section card
    carries the thickness or the profile the mesh does not, and the restraints
    reach six degrees of freedom per node rather than three.

    Three things are refused before a line is written, each of which CalculiX
    would either accept quietly or complain about in the wrong terms:

    1. a section of the wrong kind for the mesh (`require_section_for`);
    2. a beam orientation lying along one of its own members
       (`require_orientation`) — the local frame is degenerate and the profile
       has nowhere to point;
    3. fixtures that leave a rigid-body motion, checked over all six degrees of
       freedom. That check has to be the six-DOF one: a cantilever clamped at a
       single node is properly restrained and the three-DOF form calls it free,
       while a straight beam is collinear and the three-DOF form calls its mesh
       degenerate. Both would be refusals of a model that is perfectly correct.
    """
    require_section_for(mesh, section)
    choice = choose_element(mesh, section)
    if isinstance(mesh, BeamMesh) and isinstance(section, BeamSection):
        require_orientation(mesh, section)

    lines = _heading_and_nodes(mesh.nodes, name=name)
    lines.append(f"*ELEMENT, TYPE={choice.calculix_type}, ELSET={ALL_ELEMENTS}")
    lines.extend(_element_lines(element_rows(mesh)))

    sets = _fixture_sets(mesh, fixtures)
    lines.extend(_nset_lines(sets))
    lines.extend(_material_lines(material))
    lines.extend(
        section_lines(
            choice,
            section,
            element_set=ALL_ELEMENTS,
            material_name=_material_name(material),
        )
    )

    return lines, _boundary_data(sets, fixtures, rotations=True)


def write_frame_deck(
    mesh: ShellMesh | BeamMesh,
    material: Material,
    fixtures: Sequence[Fixture],
    section: Section,
    *,
    forces: NDArray[np.float64],
    delta_t_k: float | None = None,
    name: str = "Kryova",
) -> str:
    """A complete linear static `.inp` for a shell or beam model.

    **It takes a force vector rather than a `LoadCase`, and that is a stated gap
    rather than an oversight.** `loads.assemble_loads` distributes a force over
    its region by tributary area, and tributary area is defined in
    `selection.distribute_force` over a *solid's boundary triangles* — a shell's
    faces and a beam's segments each need their own distribution, and the two are
    not the same rule. Accepting a `LoadCase` here and splitting the force
    equally between the selected nodes would run, look right, and be
    mesh-dependent in exactly the way 6.2's tributary-area rule exists to
    prevent: refine the mesh and the applied load moves. So the caller supplies
    the `(3 * n_nodes,)` vector it means and `cload_data_lines` writes it.

    **The shell half of that work now exists**, and a caller with a `ShellMesh`
    should not hand-build the vector: `app.solve.shell_loads.assemble_shell_loads`
    turns a list of `Load`s into exactly this array, area-weighted by the shape
    function integrals. This signature is unchanged all the same — taking the
    vector keeps the *beam* case honest, which still has no distribution, and
    keeps this function a writer rather than a second place where load rules
    live.

    Rotational loads are not written either. `*CLOAD` can name degrees of freedom
    4 to 6 on these elements — a moment applied straight to a node — and there is
    no vocabulary for one in `types.py` yet. `MomentLoad` is a moment *about an
    axis through a region*, resolved into tangential nodal forces, which is a
    different and more general thing; mapping it onto a beam node's rotational
    DOF is a decision, not a translation.
    """
    lines, boundary = write_frame_model(mesh, material, fixtures, section, name=name)
    require_restrained(mesh, fixtures, dofs_per_node=STRUCTURAL_DOFS)
    choice = choose_element(mesh, section)

    expected = 3 * mesh.node_count
    if forces.size != expected:
        raise SolverError(
            f"The force vector has {forces.size} entries and this mesh has "
            f"{mesh.node_count} nodes, so it needs {expected} — three translational "
            "components per node, in node order. A vector of the wrong length would "
            "otherwise be reshaped into loads on the wrong nodes."
        )

    if delta_t_k is not None:
        _require_expansion(material, delta_t_k)
        lines.extend(initial_temperature_lines())

    lines.append("*STEP")
    lines.append("*STATIC")
    lines.append("*BOUNDARY")
    lines.extend(boundary)

    cloads = cload_data_lines(forces)
    if cloads:
        lines.append("*CLOAD")
        lines.extend(cloads)

    if delta_t_k is not None:
        lines.append("*TEMPERATURE")
        lines.extend(temperature_data_lines(delta_t_k))

    # `OUTPUT=2D` rather than the plain cards `write_deck` uses. On an expanded
    # element the default writes the results at the *expanded* nodes, which the
    # caller has never seen and which outnumber the mesh's own — and every reader
    # in `frd.py` indexes by the submitted node numbers. See
    # `elements.py` consequence 2.
    lines.extend(output_request_lines(choice, node="U", element="S"))
    lines.append("*END STEP")
    lines.append("")
    return "\n".join(lines)


def initial_temperature_lines(reference: float | None = None) -> list[str]:
    """`*INITIAL CONDITIONS, TYPE=TEMPERATURE` over the whole model.

    A *model* card — it belongs before the first `*STEP`, not inside one, which
    is why it is written here rather than by `steps.py`. Public because a thermal
    case posed as a modal or buckling run would need the same block, and a second
    copy of the set name and the reference temperature is a second place for them
    to drift out of step with `*EXPANSION, ZERO=`.

    `None` rather than `REFERENCE_TEMPERATURE` as the default, and resolved in
    the body. A default argument is evaluated once when the module is imported,
    so spelling it the obvious way would freeze this function's idea of the
    reference at import time while `temperature_data_lines` and `write_model`
    went on reading the live constant — three cards, two references, and a
    `delta_t_k` that silently stops being a change. Found by the test that moves
    the reference, which is why that test moves it rather than trusting zero.
    """
    value = REFERENCE_TEMPERATURE if reference is None else float(reference)
    return [
        "*INITIAL CONDITIONS, TYPE=TEMPERATURE",
        f"{ALL_NODES}, {_number(value)}",
    ]


def temperature_data_lines(delta_t_k: float) -> list[str]:
    """`*TEMPERATURE` data rows for a uniform change, without the card.

    Uniform over the whole part, which is the only thermal problem
    `app/solve/thermal.py` accepts and says so: a temperature *field* needs a
    conduction solve with its own boundary conditions, and inventing one here
    would be answering a question nobody asked. `ALL_NODES` is therefore the
    right set, and the deck says as much by naming it.

    The value written is `REFERENCE_TEMPERATURE + delta_t_k` rather than
    `delta_t_k`, which are the same number today and would not be if the
    reference ever moved. Writing the sum is what makes `delta_t_k` a change.
    """
    return [f"{ALL_NODES}, {_number(REFERENCE_TEMPERATURE + float(delta_t_k))}"]


def _require_expansion(material: Material, delta_t_k: float) -> None:
    """Refuse a temperature change on a material that cannot respond to one.

    CalculiX will not refuse it. With no `*EXPANSION` card the temperature is
    applied to a material whose expansion coefficient is zero, the run succeeds,
    and the reported thermal stress is zero — which reads as "this part does not
    mind being heated" rather than as "nobody told the solver how it expands".

    The in-house path refuses the same case in `thermal.thermal_strain`. Said
    here in its own words rather than by importing that module: `thermal.py`
    pulls in `linear_static` and therefore scipy, and the deck writer is
    deliberately free of both. `tests/test_solver_calculix.py` pins the two
    messages to the same substance so they cannot drift into disagreeing.
    """
    if material.thermal_expansion_per_k is not None:
        return
    raise SolverError(
        f"This load case applies a temperature change of {delta_t_k:g} K, but "
        f"{material.name!r} has no coefficient of thermal expansion, so its thermal "
        "stress cannot be computed. Set thermal_expansion_per_k on the material "
        "(per kelvin -- 23.6e-6 for aluminium), or clear delta_t_k to solve the "
        "isothermal case."
    )


def write_deck(mesh: TetMesh, case: LoadCase, *, name: str = "Kryova") -> str:
    """The complete `.inp` for one linear static run.

    Returned as text rather than written to a path so it can be asserted against
    in a test with no filesystem — the same reason `execute.py` takes its runner
    as a callable.
    """
    material: Material = case.material

    # Refused here rather than diagnosed afterwards, because CalculiX does not
    # diagnose it at all. Measured against ccx 2.23 on 2026-09-06: a deck whose
    # *BOUNDARY block was removed came back exit 0, a complete .frd, no *ERROR,
    # no *WARNING - and a maximum displacement of 5.4e+11 mm. PaStiX factorises
    # the singular system and returns a finite, meaningless vector, which is the
    # same trap `linear_static._residual_is_small` exists to catch on our own
    # solver. There, the residual can catch it because the stiffness matrix is
    # in hand; here it is not, and federating is the reason. So the check moves
    # *before* the solve, where it is exact and needs no matrix at all: the
    # fixtures either remove all six rigid-body motions or they do not, and that
    # is a property of the load case rather than of whoever solves it.
    #
    # A static step only. `write_model` deliberately does not do this, because a
    # free-free modal analysis is a legitimate case with no fixtures at all -
    # its six zero-frequency modes are the answer, not a fault.
    #
    # And it runs *after* `write_model` rather than before it, so that a
    # fixture selecting no nodes at all is still reported as "fixture 2
    # selected nothing" rather than as "the model is under-constrained".
    # Both are true of that case; only the first says what to fix.
    lines, boundary = write_model(mesh, material, case.fixtures, name=name)
    require_restrained(mesh, case.fixtures)

    # Model cards, so before the first *STEP. Refused first: a deck that heats a
    # material with no expansion coefficient is accepted by ccx and reports no
    # thermal stress at all. See `_require_expansion`.
    if case.delta_t_k is not None:
        _require_expansion(material, case.delta_t_k)
        lines.extend(initial_temperature_lines())

    lines.append("*STEP")
    lines.append("*STATIC")

    lines.append("*BOUNDARY")
    lines.extend(boundary)

    forces, _warnings = assemble_loads(mesh, case.loads, material.density_kg_m3)
    cloads = cload_data_lines(forces)
    if cloads:
        lines.append("*CLOAD")
        lines.extend(cloads)

    if case.delta_t_k is not None:
        lines.append("*TEMPERATURE")
        lines.extend(temperature_data_lines(case.delta_t_k))

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


__all__ = [
    "ALL_ELEMENTS",
    "ALL_NODES",
    "C3D10_EDGES",
    "C3D10_MIDSIDE_ORDER",
    "DENSITY_KG_M3_TO_TONNE_MM3",
    "REFERENCE_TEMPERATURE",
    "cload_data_lines",
    "element_type",
    "initial_temperature_lines",
    "temperature_data_lines",
    "write_deck",
    "write_frame_deck",
    "write_frame_model",
    "write_model",
]
