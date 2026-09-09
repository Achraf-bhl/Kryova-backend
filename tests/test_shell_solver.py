"""`ShellSolver` — the seam, and the half of it that Linux can check.

**Split deliberately.** Everything about the *deck* — which element, which
section card, which output request, what force lands on which node — is asserted
here against `write_shell_deck`'s string, offline and instantly, on a machine
with no `ccx`. Running that deck is the other half and is the one measurement
this file cannot make; it belongs to the Windows seat, THE QUEUE item A6.

That split is the point rather than a limitation: a function that both built and
ran the deck would have made the building untestable everywhere the solver is
absent, which is where nearly all of it was written.

Every test is named after the wrong answer it prevents. The quietest is
`OUTPUT=2D`: without it ccx writes the `.frd` for the *expanded* solid model,
every node index is in range, there is no length mismatch, and the reader
returns another model's answer.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.structural import ShellMesh
from app.solve.calculix.run import find_ccx
from app.solve.calculix.shell import ShellSolver, shell_von_mises, write_shell_deck
from app.solve.materials import material_for
from app.solve.postprocess import shell_element_average, summarise_shell_static
from app.solve.sections import ShellSection
from app.solve.types import (
    BoxSelector,
    Fixture,
    ForceLoad,
    LoadCase,
    PressureLoad,
)

WIDTH_MM = 100.0
HEIGHT_MM = 50.0
THICKNESS_MM = 3.0
MATERIAL = material_for("steel-1018")

EVERYTHING = BoxSelector(min=(-1.0, -1.0, -1.0), max=(WIDTH_MM + 1, HEIGHT_MM + 1, 1.0))
LEFT_EDGE = BoxSelector(min=(-1.0, -1.0, -1.0), max=(1.0, HEIGHT_MM + 1, 1.0))
RIGHT_EDGE = BoxSelector(min=(WIDTH_MM - 1, -1.0, -1.0), max=(WIDTH_MM + 1, HEIGHT_MM + 1, 1.0))


def _plate(order: int = 1) -> ShellMesh:
    """One quadrilateral, so every nodal load in the deck is checkable by hand."""
    nodes = [
        [0.0, 0.0, 0.0],
        [WIDTH_MM, 0.0, 0.0],
        [WIDTH_MM, HEIGHT_MM, 0.0],
        [0.0, HEIGHT_MM, 0.0],
    ]
    faces = np.array([[0, 1, 2, 3]])
    if order == 1:
        return ShellMesh(nodes=np.array(nodes), faces=faces)
    nodes += [
        [WIDTH_MM / 2, 0.0, 0.0],
        [WIDTH_MM, HEIGHT_MM / 2, 0.0],
        [WIDTH_MM / 2, HEIGHT_MM, 0.0],
        [0.0, HEIGHT_MM / 2, 0.0],
    ]
    return ShellMesh(nodes=np.array(nodes), faces=faces, midside=np.array([[4, 5, 6, 7]]))


#: `LoadCase` refuses an empty `loads` list, so the summary tests — which care
#: about volume and counts and not about what was applied — carry this one.
NOMINAL_LOAD = ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -1.0), name="nominal")


def _case(loads: list | None = None, name: str = "shell probe") -> LoadCase:
    return LoadCase(
        name=name,
        material=MATERIAL,
        fixtures=[Fixture(where=LEFT_EDGE, kind="clamp")],
        loads=loads if loads else [NOMINAL_LOAD],
    )


def _cloads(deck: str) -> dict[tuple[int, int], float]:
    """The `*CLOAD` block as {(node, dof): magnitude}, 1-based as written."""
    out: dict[tuple[int, int], float] = {}
    inside = False
    for line in deck.splitlines():
        if line.startswith("*"):
            inside = line.upper().startswith("*CLOAD")
            continue
        if inside and line.strip():
            node, dof, value = (part.strip() for part in line.split(","))
            out[(int(node), int(dof))] = float(value)
    return out


class TestTheDeckSaysWhatThisRepoMeansToAsk:
    @pytest.mark.parametrize(("order", "element"), [(1, "S4"), (2, "S8R")])
    def test_the_element_matches_the_meshs_order(self, order: int, element: str) -> None:
        deck, _ = write_shell_deck(
            _plate(order),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        assert f"*ELEMENT, TYPE={element}," in deck

    def test_the_shell_section_carries_the_thickness(self) -> None:
        """The thickness the mesh deliberately does not hold has to arrive
        somewhere, and this card is the only place it does."""
        deck, _ = write_shell_deck(
            _plate(),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        lines = deck.splitlines()
        card = next(i for i, line in enumerate(lines) if line.startswith("*SHELL SECTION"))
        assert lines[card + 1].strip() == "3.0"

    def test_both_output_requests_ask_for_two_dimensional_results(self) -> None:
        """**The quiet one.** ccx expands a shell into solids and writes the
        `.frd` for the expanded model unless asked otherwise. Without `OUTPUT=2D`
        the reader indexes another model's answer with every index in range and
        no length mismatch — a plausible wrong number, not an error."""
        deck, _ = write_shell_deck(
            _plate(),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        assert "*NODE FILE, OUTPUT=2D" in deck
        assert "*EL FILE, OUTPUT=2D" in deck

    def test_the_clamp_holds_all_six_degrees_of_freedom(self) -> None:
        """A shell node has six; a solid node three. A clamp written for a solid
        leaves a shell free to rotate about the restrained edge."""
        deck, _ = write_shell_deck(
            _plate(),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        held = [line for line in deck.splitlines() if line.startswith("FIX1,")]
        assert {line.split(",")[1].strip() for line in held} == {"1", "2", "3", "4", "5", "6"}


class TestTheLoadInTheDeckIsTheLoadThatWasAsked:
    def test_a_linear_quadrilateral_splits_the_force_four_ways(self) -> None:
        deck, warnings = write_shell_deck(
            _plate(),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        assert warnings == []
        loads = _cloads(deck)
        assert set(loads) == {(n, 3) for n in (1, 2, 3, 4)}
        assert all(value == pytest.approx(-125.0) for value in loads.values())
        assert sum(loads.values()) == pytest.approx(-500.0)

    def test_a_quadratic_quadrilateral_writes_negative_corner_loads(self) -> None:
        """The −1/12 corner factor, visible in the deck itself. A downward load
        pushes an S8R face's corners *up*, and anyone reading this deck expecting
        four downward corner loads will think it is a sign error. It is not."""
        deck, _ = write_shell_deck(
            _plate(2),
            _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -1200.0))]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        loads = _cloads(deck)
        corners = [loads[(n, 3)] for n in (1, 2, 3, 4)]
        midside = [loads[(n, 3)] for n in (5, 6, 7, 8)]
        assert all(value > 0.0 for value in corners)
        assert all(value < 0.0 for value in midside)
        assert all(value == pytest.approx(1200.0 / 12.0) for value in corners)
        assert all(value == pytest.approx(-1200.0 / 3.0) for value in midside)
        assert sum(corners) + sum(midside) == pytest.approx(-1200.0)

    def test_a_pressure_reaches_the_deck_as_area_times_pressure(self) -> None:
        deck, _ = write_shell_deck(
            _plate(),
            _case([PressureLoad(where=EVERYTHING, pressure_mpa=0.5)]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        total = sum(value for (_, dof), value in _cloads(deck).items() if dof == 3)
        assert total == pytest.approx(-0.5 * WIDTH_MM * HEIGHT_MM)

    def test_an_edge_load_warns_that_it_was_split_equally(self) -> None:
        """The gap named in `app/solve/shell_loads.py`: an edge selects no whole
        face, so the fallback delivers the right resultant with the wrong
        distribution. `write_shell_deck` must hand that warning back rather than
        swallow it — a silent fallback is the whole failure mode."""
        deck, warnings = write_shell_deck(
            _plate(),
            _case([ForceLoad(where=RIGHT_EDGE, force_n=(0.0, 0.0, -900.0), name="tip")]),
            ShellSection(thickness_mm=THICKNESS_MM),
        )
        assert any("tip" in w and "equally" in w for w in warnings)
        assert sum(_cloads(deck).values()) == pytest.approx(-900.0)


class TestTheStressIsAveragedInTheOrderThatIsNotBiasedHigh:
    def test_a_uniform_nodal_field_comes_back_exactly_uniform(self) -> None:
        """What the oracle comparison rests on. If face averaging did not
        reproduce a uniform field exactly, a closed-form uniform stress state
        could not be used to check the two solvers against each other."""
        mesh = _plate(2)
        tensor = np.tile(np.array([10.0, 4.0, 0.0, 2.0, 0.0, 0.0]), (mesh.node_count, 1))
        averaged = shell_element_average(mesh, tensor)
        assert averaged.shape == (mesh.face_count, 6)
        assert averaged[0] == pytest.approx(tensor[0])

    def test_midside_nodes_do_not_drag_a_face_towards_its_neighbours(self) -> None:
        """Corner-only, the same rule the solid path states. A midside node sits
        on an edge shared by more faces than a corner is."""
        mesh = _plate(2)
        values = np.zeros(mesh.node_count)
        values[np.asarray(mesh.midside).ravel()] = 1000.0
        assert shell_element_average(mesh, values) == pytest.approx(0.0)

    def test_the_tensor_is_averaged_before_the_invariant_is_taken(self) -> None:
        """von Mises is nonlinear, so the two orders differ — and the wrong one
        is biased high, exactly where a factor of safety is read. Two equal and
        opposite nodal tensors average to zero stress; taking the invariant
        first and averaging that gives a large positive number instead."""
        mesh = _plate()
        pull = np.array([100.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        tensor = np.array([pull, -pull, pull, -pull])
        assert shell_von_mises(mesh, tensor) == pytest.approx(0.0)


class TestTheSummaryIsMadeOfWhatAShellIsMadeOf:
    def test_volume_is_area_times_thickness_and_mass_follows(self) -> None:
        """A shell face has no volume of its own. Forgetting the thickness makes
        `mass_kg` wrong by a factor of the thickness, which reads as a units bug
        somewhere else entirely."""
        mesh = _plate()
        result = summarise_shell_static(
            mesh,
            _case(),
            THICKNESS_MM,
            np.zeros(3 * mesh.node_count),
            np.array([12.0]),
            [],
            0.1,
        )
        expected_volume = WIDTH_MM * HEIGHT_MM * THICKNESS_MM
        assert result.volume_mm3 == pytest.approx(expected_volume)
        assert result.mass_kg == pytest.approx(
            expected_volume * 1e-9 * MATERIAL.density_kg_m3
        )

    def test_the_element_count_is_faces_not_tetrahedra(self) -> None:
        mesh = _plate()
        result = summarise_shell_static(
            mesh, _case(), THICKNESS_MM, np.zeros(3 * mesh.node_count), np.array([12.0]), [], 0.1
        )
        assert result.element_count == mesh.face_count == 1
        assert result.node_count == mesh.node_count

    def test_a_shell_and_a_solid_agree_on_what_a_factor_of_safety_is(self) -> None:
        """Both summaries go through one `_summarise`, so this cannot drift
        without the shared function changing."""
        mesh = _plate()
        peak = 100.0
        result = summarise_shell_static(
            mesh,
            _case(),
            THICKNESS_MM,
            np.zeros(3 * mesh.node_count),
            np.array([peak]),
            [],
            0.1,
        )
        assert result.factor_of_safety == pytest.approx(
            MATERIAL.yield_strength_mpa / peak
        )
        assert result.yields is (peak >= MATERIAL.yield_strength_mpa)


@pytest.mark.skipif(
    find_ccx() is not None, reason="ccx is installed here, so the absence path cannot be taken"
)
class TestWithoutTheSolverItSaysSoRatherThanGuessing:
    def test_solving_without_ccx_names_the_missing_binary(self) -> None:
        """The Linux state. It must fail at the *run*, not at the deck — which
        is what proves everything above this line was reachable without it."""
        from app.solve.calculix.run import CalculiXUnavailable

        with pytest.raises(CalculiXUnavailable):
            ShellSolver().solve(
                _plate(),
                _case([ForceLoad(where=EVERYTHING, force_n=(0.0, 0.0, -500.0))]),
                ShellSection(thickness_mm=THICKNESS_MM),
            )
