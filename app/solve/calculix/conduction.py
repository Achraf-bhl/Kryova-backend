"""Steady conduction federated to CalculiX — THE QUEUE E3, closed 2026-09-17.

`app/solve/conduction.py` solves `div(k grad T) + q_v = 0` in-house and is
checked against three closed forms: the linear bar, the logarithmic tube wall
and the convecting bar's Biot tip temperature. That is *code verification
against mathematics*, and it is the weaker of the two claims available. The
stronger one is agreement with an independent implementation of the same
physics, which is what `app/solve/oracle.py` exists to produce and what
`CONDUCTION_BACKEND` was given its own setting to make selectable. This module
is the second implementation: CalculiX's `*HEAT TRANSFER, STEADY STATE`.

**Everything below was measured against ccx 2.23 on the Windows seat on
2026-09-17**, unlike the structural deck writer, which was written blind from
the manual a day before it could be run. The facts that were measured rather
than assumed, each of which would have been a plausible guess:

* **The step needs no data line.** `*HEAT TRANSFER, STEADY STATE` on its own
  runs; ccx reports `convergence` after one Newton iteration on a linear model.
* **The temperature degree of freedom is 11**, so `*BOUNDARY` holds a node's
  temperature with `<node>, 11, 11, <K>` — the same card the structural deck
  uses for displacement, with a different number in it.
* **`*INITIAL CONDITIONS, TYPE=TEMPERATURE` is required** and its value does not
  reach the answer of a linear steady solve. `case.reference_temperature_k` is
  written into it, because that is the one temperature a `ThermalCase` already
  carries and inventing a second would put a number in the deck nobody chose.
* **The results file carries `NDTEMP` (component `T`) and `RFL` (component
  `RFL`), and both are written for every node**, not only for the prescribed
  ones. `RFL`'s sign is already this repository's — positive is heat into the
  part — and it is the *pure* reaction, excluding any `*CFLUX` applied at the
  same node, which is not what `fixed_temperature_heat_w` means. `_boundary_heat`
  carries the measurement and the 1.0 W that found it.
* **A 100 mm bar held at 300 K and 400 K reproduces `T(x) = 300 + x` with a
  maximum nodal error of exactly 0.0** on a 99-node tet4 mesh.

What each boundary becomes, and why
-----------------------------------
**A fixed temperature becomes `*BOUNDARY` on DOF 11.** Exact, and the node set
comes from `SteadyConductionSolver._prescribed`, so two overlapping regions
holding one node at two temperatures are refused here by the same code and with
the same message as in-house. A second reading of "what is held where" is
exactly the duplication that would let the oracle localise a disagreement to the
deck writer instead of to the physics.

**A heat flux and a volumetric source become one `*CFLUX` vector**, written from
the in-house `_flux_load` and `volumetric_source_load` rather than re-derived
from the selectors. This follows `deck.py`'s rule for mechanical loads verbatim:
derive the loads twice and a disagreement stops localising, because it could be
either end. The oracle then compares the *solve*, which is what it is for.

**A convection film becomes `*FILM`, and it cannot be handed over the same
way.** A film is not a load: it puts `integral h N_i N_j dA` into the
conductivity matrix as well as `integral h T_inf N_i dA` into the right-hand
side, and there is no vector that expresses it. So CalculiX has to integrate it
itself, over element faces named `<element>, F<n>`. That is why
`TetMesh.surface_face_owners` exists — without it a film could only be written
as a node list, which CalculiX has no card for, or as an equivalent flux, which
would ask the two solvers different questions. The faces are selected through
`selection.surface_face_rows_within`, the same mask the in-house film integrates
over, so the two films act on exactly the same triangles.

**The face-label mapping is derived, never typed.** `TET_FACE_TO_CCX` below is
computed by matching this repository's `_TET_FACES` corner triples against
CalculiX's, as sets, and `tests/test_calculix_conduction.py` checks the
derivation rather than the constant. It happens to come out as the identity
(local face *i* is CalculiX's `F(i+1)`), which is exactly the kind of
coincidence that a hand-written table records as a fact and then stops
protecting.

What this module does **not** claim
-----------------------------------
**The heat flux it returns is not CalculiX's.** `ThermalField.heat_flux_w_m2` is
one Fourier flux per element, and CalculiX writes `HFL` extrapolated to nodes —
a different quantity on a different grid. Rather than reshape one into the
other, the flux here is recovered from *CalculiX's temperature field* by this
repository's own `SteadyConductionSolver.heat_flux`, and is therefore **not
independent evidence**: it is our differentiation of their answer. A caller
comparing fluxes between the two solvers would be comparing one gradient
operator with itself. `compare_conduction` deliberately compares temperatures
and the boundary heat, which are the quantities both solvers genuinely produce.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.mesh.types import _TET_FACES, TetMesh
from app.solve.base import ConductionSolver
from app.solve.calculix.deck import (
    _element_lines,
    _element_rows,
    _heading_and_nodes,
    _number,
    element_type,
)
from app.solve.calculix.diagnose import diagnose
from app.solve.calculix.frd import FrdFile, parse_frd
from app.solve.calculix.run import DEFAULT_TIMEOUT_S, run_ccx
from app.solve.calculix.solver import _with_evidence
from app.solve.conduction import (
    CONDUCTIVITY_W_MK_TO_W_MMK,
    FILM_W_M2K_TO_W_MM2K,
    FLUX_W_M2_TO_W_MM2,
    SOURCE_W_M3_TO_W_MM3,
    ConductionResult,
    Convection,
    FixedTemperature,
    SteadyConductionSolver,
    ThermalCase,
    ThermalField,
    volumetric_source_load,
)
from app.solve.selection import select_nodes, surface_face_rows_within
from app.solve.types import SolverError

#: CalculiX's degree of freedom for temperature. Measured 2026-09-17: a
#: `*BOUNDARY` row `<node>, 11, 11, <K>` holds a node's temperature, and the
#: solved field reproduces the analytic profile exactly.
TEMPERATURE_DOF: Final = 11

#: The `.frd` entity carrying the nodal temperature, component `T`.
NODAL_TEMPERATURE: Final = "NDTEMP"

#: The `.frd` entity carrying the nodal reaction flux, component `RFL`. Written
#: for **every** node, not only the prescribed ones — measured, and the reason
#: the boundary heat is summed over the held nodes rather than over the block.
REACTION_FLUX: Final = "RFL"

#: The material name the conduction deck writes. A conduction run needs only a
#: conductivity, which arrives on the `ThermalCase` rather than on a `Material`
#: (see `app/solve/conduction.py`'s docstring), so there is no material name to
#: take and one is supplied.
CONDUCTOR: Final = "KRYOVA_CONDUCTOR"

#: The element set every element belongs to. Its own constant rather than
#: `deck.ALL_ELEMENTS` reused, because a body flux names it and a future
#: multi-conductivity deck would have to split it.
ALL_ELEMENTS: Final = "EALL"

#: CalculiX's four tetrahedron faces as local **corner** indices, 0-based, in
#: the manual's order: face 1 is 1-2-3, face 2 is 1-4-2, face 3 is 2-4-3, face 4
#: is 3-4-1. Written as the manual writes them so the derivation below can be
#: read against the source rather than against a conclusion.
CCX_TET_FACE_CORNERS: Final = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))


def _tet_face_to_ccx() -> tuple[int, ...]:
    """This repository's local face order mapped onto CalculiX's face numbers.

    Derived by matching corner *sets*, because both orderings are orientation
    conventions over the same four triples and only the membership is shared. A
    face that matched nothing would be a real disagreement about what a
    tetrahedron is, so it raises rather than defaulting.
    """
    ours = [frozenset(int(i) for i in face) for face in _TET_FACES]
    theirs = [frozenset(face) for face in CCX_TET_FACE_CORNERS]
    mapping: list[int] = []
    for local, corners in enumerate(ours):
        try:
            mapping.append(theirs.index(corners) + 1)
        except ValueError:  # pragma: no cover - a tetrahedron has these faces
            raise SolverError(
                f"Local tetrahedron face {local} has corners {sorted(corners)}, which "
                "is not one of CalculiX's four. The two element libraries disagree "
                "about the element, and no face label can be written."
            ) from None
    return tuple(mapping)


#: Local face index -> CalculiX face number, for `*FILM` and `*DFLUX`.
TET_FACE_TO_CCX: Final = _tet_face_to_ccx()


def _conductivity_lines(case: ThermalCase) -> list[str]:
    """`*MATERIAL`, `*CONDUCTIVITY` and the section, in W/(mm·K).

    The unit boundary, and the only one in this module: `app/solve/conduction.py`
    converts at the top of its own `solve`, so both solvers cross from the units
    an engineer quotes into the mm system in exactly one place each, through the
    same named constants.
    """
    return [
        f"*MATERIAL, NAME={CONDUCTOR}",
        "*CONDUCTIVITY",
        _number(case.conductivity_w_mk * CONDUCTIVITY_W_MK_TO_W_MMK),
        f"*SOLID SECTION, ELSET={ALL_ELEMENTS}, MATERIAL={CONDUCTOR}",
    ]


def cflux_data_lines(heat_w: NDArray[np.float64]) -> list[str]:
    """Nodal heat as `*CFLUX` data rows, without the card.

    Exact zeros are dropped for `cload_data_lines`' reason: shape-function
    integrals leave most nodes at zero, and writing them all multiplies the deck
    by the node count for no effect on the answer. The threshold is exact zero
    rather than an epsilon, because a heat load has no counterpart of a force's
    round-off floor — the values here are sums of areas times fluxes, and a
    genuinely tiny one is a genuinely tiny facet.
    """
    lines: list[str] = []
    for index in np.flatnonzero(heat_w):
        lines.append(f"{int(index) + 1}, {TEMPERATURE_DOF}, {_number(float(heat_w[index]))}")
    return lines


def film_data_lines(
    mesh: TetMesh, films: list[Convection], warnings: list[str]
) -> list[str]:
    """`*FILM` data rows, without the card: `<element>, F<n>, <T_inf>, <h>`.

    A film whose region matched no complete surface facet contributes nothing
    and **says so** rather than being dropped silently — the same warning the
    in-house solver raises for the same region, because both read the same mask.
    A film that selects nothing is almost always a selector with too tight a
    tolerance, and a model that quietly loses its only cooling path solves
    cleanly and reports a part far hotter than it is.
    """
    rows: list[str] = []
    owners = mesh.surface_face_owners
    for index, film in enumerate(films):
        label = film.name or f"convection[{index}]"
        nodes = select_nodes(mesh, film.where)
        selected = surface_face_rows_within(mesh, nodes)
        if len(selected) == 0:
            warnings.append(
                f"{label!r} selected no complete surface facets, so no film was "
                "applied there. Widen the selector, or name a region that lies on "
                "the boundary."
            )
            continue
        h = film.film_coefficient_w_m2k * FILM_W_M2K_TO_W_MM2K
        for element, local in owners[selected]:
            rows.append(
                f"{int(element) + 1}, F{TET_FACE_TO_CCX[int(local)]}, "
                f"{_number(film.ambient_temperature_k)}, {_number(h)}"
            )
    return rows


@dataclass(frozen=True)
class _Deck:
    """The deck, and the two things reading its results needs back.

    Returned together rather than recomputed in `solve`, because the applied
    heat and the held nodes are what turn CalculiX's `RFL` block into the same
    quantity the in-house solver reports — see `_boundary_heat`. Deriving them a
    second time there would be the duplication `deck.py` refuses for mechanical
    loads, in the one place where it would change a published number.
    """

    text: str
    #: (n_nodes,) watts applied at each node by heat flux and volumetric source.
    applied_heat_w: NDArray[np.float64]
    #: Node indices whose temperature is prescribed.
    held: NDArray[np.int64]


def write_conduction_deck(mesh: TetMesh, case: ThermalCase, *, name: str = "Kryova") -> str:
    """The complete `.inp` for one steady conduction run.

    Text rather than a path, for `write_deck`'s reason: it can then be asserted
    against on a machine with no `ccx`, which is where most of this repository's
    tests run.

    The thermally-floating refusal happens **here**, before the solver sees the
    deck, for the reason `write_deck` refuses an unrestrained structure here:
    CalculiX does not diagnose it. A conduction model whose every boundary is
    insulated or carries only a flux determines the temperature up to an
    additive constant, and a direct factorisation of that system returns a
    finite, meaningless field with no error and no warning.
    """
    warnings: list[str] = []
    return _write_conduction_deck(mesh, case, name=name, warnings=warnings).text


def _write_conduction_deck(
    mesh: TetMesh, case: ThermalCase, *, name: str, warnings: list[str]
) -> _Deck:
    fixed = [b for b in case.boundaries if isinstance(b, FixedTemperature)]
    films = [b for b in case.boundaries if isinstance(b, Convection)]
    if not fixed and not films:
        raise SolverError(
            "The model is thermally floating: nothing sets its temperature level "
            "(every boundary is insulated or carries only a heat flux), so a steady "
            "conduction solve determines the temperature only up to an arbitrary "
            "constant. Add a fixed_temperature boundary on a region whose temperature "
            "you know, or a convection boundary with a film coefficient and an ambient "
            "temperature."
        )

    lines = _heading_and_nodes(mesh.nodes, name=name)
    lines.append(f"*ELEMENT, TYPE={element_type(mesh)}, ELSET={ALL_ELEMENTS}")
    lines.extend(_element_lines(_element_rows(mesh)))
    lines.extend(_conductivity_lines(case))

    # Required by ccx for a heat-transfer step, and invariant for a linear steady
    # solve — measured. See the module docstring on why it is the reference
    # temperature and not a number invented here.
    lines.append("*INITIAL CONDITIONS, TYPE=TEMPERATURE")
    lines.append(f"NALL, {_number(case.reference_temperature_k)}")

    lines.append("*STEP")
    lines.append("*HEAT TRANSFER, STEADY STATE")

    held_nodes, held_values = SteadyConductionSolver._prescribed(mesh, fixed)
    if len(held_nodes) > 0:
        lines.append("*BOUNDARY")
        for node, value in zip(held_nodes, held_values, strict=True):
            lines.append(
                f"{int(node) + 1}, {TEMPERATURE_DOF}, {TEMPERATURE_DOF}, {_number(float(value))}"
            )

    heat = volumetric_source_load(mesh, case.volumetric_source_w_m3 * SOURCE_W_M3_TO_W_MM3)
    heat = heat + SteadyConductionSolver._flux_load(mesh, case.boundaries, warnings)
    cfluxes = cflux_data_lines(heat)
    if cfluxes:
        lines.append("*CFLUX")
        lines.extend(cfluxes)

    film_rows = film_data_lines(mesh, films, warnings)
    if film_rows:
        lines.append("*FILM")
        lines.extend(film_rows)

    if len(held_nodes) == 0 and not film_rows:
        raise SolverError(
            "The model is thermally floating: the only boundaries that could set its "
            "level are convection films that matched no surface facets. Widen those "
            "selectors, or add a fixed_temperature boundary."
        )

    lines.append("*NODE FILE")
    lines.append(f"NT, {REACTION_FLUX}")
    lines.append("*END STEP")
    lines.append("")
    return _Deck(text="\n".join(lines), applied_heat_w=heat, held=held_nodes)


def _boundary_heat(
    parsed: FrdFile, node_count: int, deck: _Deck, warnings: list[str]
) -> float:
    """The heat the held regions supply to the part, W — positive in.

    `ConductionResult.fixed_temperature_heat_w`'s definition, computed from
    CalculiX's own numbers. **Two facts about `RFL` were measured on 2026-09-17
    and neither was guessable**, and the second was found by the oracle
    disagreeing:

    1. **The sign is already ours.** `RFL` at a held node is positive when heat
       flows *into* the part there, matching this repository's convention, so
       nothing is negated. A bar held hot at one end and cooled by a film at the
       other gave in-house `+2.54661 W` and `sum(RFL) = +2.54662 W`.

    2. **`RFL` does not include the `*CFLUX` applied at the same node**, and
       ours does. In-house the quantity is `sum(K T - f)` over every node, which
       nets off the heat *applied at* a held node; CalculiX reports the pure
       reaction `K T`. On a bar with a 20 W volumetric source and 2.5 W entering
       the far face, the held face must remove all 22.5 W — in-house
       `-22.5000 W`, raw `sum(RFL) = -21.5 W`, **exactly 1.0 W apart**, which is
       the source's own share of the half-layer of material tributary to the
       held face. So the applied heat at the held nodes is subtracted here, and
       the two solvers then agree. Reading the block raw would have published a
       boundary heat 4.4% wrong on any model with a source or a flux, with
       nothing in either run looking unhealthy.

    A model with nothing held has no such heat at all — not zero watts, but no
    quantity — and 0.0 is the honest value there because that is what the
    in-house definition returns for the same model: a sum over an empty set.
    """
    if len(deck.held) == 0:
        return 0.0
    block = parsed.blocks.get(REACTION_FLUX)
    if block is None:
        warnings.append(
            "CalculiX wrote no reaction-flux block, so the heat crossing the "
            "fixed-temperature regions is unmeasured rather than zero."
        )
        return float("nan")
    reactions = block.as_array(node_count)[:, 0]
    return float(np.sum(reactions[deck.held]) - np.sum(deck.applied_heat_w[deck.held]))


def temperatures(frd_text: str, node_count: int) -> NDArray[np.float64]:
    """(node_count,) absolute kelvin from a `.frd`, in the mesh's node order."""
    block = parse_frd(frd_text).require(NODAL_TEMPERATURE)
    if len(block.components) != 1:
        raise SolverError(
            f"{NODAL_TEMPERATURE} declares {len(block.components)} components; one "
            f"temperature per node was expected. It carries: {block.components}."
        )
    values: NDArray[np.float64] = block.as_array(node_count)[:, 0]
    return values


class CalculiXConductionSolver(ConductionSolver):
    """Steady heat conduction through a separate `ccx` process.

    Decision 4: CalculiX is GPL and is invoked across a file and CLI boundary,
    never linked. The structural `CalculiXSolver` is the shape this follows, and
    everything downstream of the deck — `run_ccx`, `diagnose`, `parse_frd` — is
    shared rather than re-implemented.
    """

    name = "calculix-conduction"

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        threads: int | None = None,
    ) -> None:
        self.executable = executable if executable is not None else os.environ.get("CCX_PATH")
        self.timeout_s = timeout_s
        self.threads = threads

    def solve(self, mesh: TetMesh, case: ThermalCase) -> ThermalField:
        started = time.perf_counter()
        warnings: list[str] = []
        deck = _write_conduction_deck(mesh, case, name=case.name, warnings=warnings)

        run = run_ccx(
            deck.text,
            executable=self.executable,
            timeout_s=self.timeout_s,
            threads=self.threads,
        )
        failure = diagnose(run)
        if failure is not None:
            raise SolverError(_with_evidence(failure))

        parsed = parse_frd(run.frd)
        field = temperatures(run.frd, mesh.node_count)
        supplied = _boundary_heat(parsed, mesh.node_count, deck, warnings)

        # Our gradient of their field, and not independent evidence — see the
        # module docstring. Computed rather than omitted because `ThermalField`
        # has no shape for "no flux", and an array of zeros would read as a part
        # with no heat moving through it.
        flux = SteadyConductionSolver.heat_flux(
            mesh, field, case.conductivity_w_mk * CONDUCTIVITY_W_MK_TO_W_MMK
        )
        warnings.append(
            "heat_flux_w_m2 is this repository's own gradient of CalculiX's "
            "temperature field, not CalculiX's HFL output, and is not an "
            "independent check of it."
        )

        result = ConductionResult(
            name=case.name,
            min_temperature_k=float(field.min()),
            max_temperature_k=float(field.max()),
            min_temperature_node=int(np.argmin(field)),
            max_temperature_node=int(np.argmax(field)),
            fixed_temperature_heat_w=supplied,
            node_count=mesh.node_count,
            element_count=mesh.tet_count,
            solve_seconds=time.perf_counter() - started,
            warnings=warnings,
        )
        return ThermalField(
            result=result,
            temperatures_k=field,
            heat_flux_w_m2=flux / FLUX_W_M2_TO_W_MM2,
        )


__all__ = [
    "ALL_ELEMENTS",
    "CCX_TET_FACE_CORNERS",
    "CONDUCTOR",
    "NODAL_TEMPERATURE",
    "REACTION_FLUX",
    "TEMPERATURE_DOF",
    "TET_FACE_TO_CCX",
    "CalculiXConductionSolver",
    "cflux_data_lines",
    "film_data_lines",
    "temperatures",
    "write_conduction_deck",
]
