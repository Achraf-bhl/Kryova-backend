"""The CalculiX deck — master plan 6.1 and 6.2.

Offline and instant: writing a deck is string generation over arrays, so none of
this needs `ccx` on the machine, a database, or a network. The tests that need
the solver binary itself belong with 6.4 and skip when it is absent, the way the
CATIA live tests do.

Every test here is named after the wrong answer it prevents, because every one of
these mistakes produces a deck CalculiX accepts and solves. None of them raises.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import TET10_EDGES
from app.solve.calculix import (
    C3D10_EDGES,
    C3D10_MIDSIDE_ORDER,
    DENSITY_KG_M3_TO_TONNE_MM3,
    element_type,
    write_deck,
)
from app.solve.calculix.deck import cload_data_lines as _cload_lines
from app.solve.calculix.deck import write_model
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, Fixture, ForceLoad, LoadCase, SolverError


def _case(**overrides) -> LoadCase:
    base = dict(
        name="8 kN pull",
        material=MATERIALS["steel-1018"],
        fixtures=[Fixture(where=FaceSelector(type="face", axis="z", side="min"))],
        loads=[
            ForceLoad(
                where=FaceSelector(type="face", axis="z", side="max"),
                force_n=[0.0, 0.0, 8000.0],
            )
        ],
    )
    base.update(overrides)
    return LoadCase(**base)


def _mesh(quadratic: bool = False):
    mesh = box_mesh((20.0, 20.0, 60.0), divisions=(2, 2, 3))
    return promote_to_tet10(mesh) if quadratic else mesh


def _section(deck: str, keyword: str) -> list[str]:
    """The logical rows under one `*KEYWORD`, up to the next keyword.

    Rows are rejoined across continuations. CalculiX continues a line that ends
    in a comma, and a C3D10 row is eleven numbers with its element id, so it
    legitimately wraps — a reader that took physical lines would see eight
    numbers and call a correct deck wrong.
    """
    rows: list[str] = []
    collecting = False
    pending = ""
    for line in deck.splitlines():
        if line.startswith("*"):
            if collecting:
                break
            collecting = line.upper().startswith(keyword.upper())
            continue
        if not collecting:
            continue
        pending += line.strip()
        if pending.endswith(","):
            continue
        rows.append(pending)
        pending = ""
    if pending:
        rows.append(pending)
    return rows


class TestNumberingIsOneBased:
    """An off-by-one does not crash. It shifts every load and restraint onto the
    neighbouring node and returns a field that looks entirely reasonable."""

    def test_the_first_node_is_numbered_one(self) -> None:
        deck = write_deck(_mesh(), _case())

        first = _section(deck, "*NODE")[0]
        assert first.split(",")[0].strip() == "1"

    def test_no_node_is_numbered_zero(self) -> None:
        ids = [int(line.split(",")[0]) for line in _section(write_deck(_mesh(), _case()), "*NODE")]

        assert min(ids) == 1
        assert max(ids) == len(_mesh().nodes)

    def test_the_highest_node_referenced_is_the_node_count(self) -> None:
        """A connectivity still 0-based would reference node 0 and miss the last."""
        mesh = _mesh()
        deck = write_deck(mesh, _case())
        referenced = set()
        for line in _section(deck, "*ELEMENT"):
            referenced.update(int(n) for n in line.split(",")[1:] if n.strip())

        assert min(referenced) >= 1
        assert max(referenced) == len(mesh.nodes)


class TestTheElementIsTheOneThatWasMeshed:
    def test_a_linear_mesh_writes_c3d4(self) -> None:
        assert element_type(_mesh()) == "C3D4"
        assert "TYPE=C3D4" in write_deck(_mesh(), _case())

    def test_a_quadratic_mesh_writes_c3d10(self) -> None:
        """6.3: C3D10 is CalculiX's documented recommended solid."""
        assert element_type(_mesh(quadratic=True)) == "C3D10"
        assert "TYPE=C3D10" in write_deck(_mesh(quadratic=True), _case())

    def test_a_linear_mesh_is_not_silently_promoted(self) -> None:
        """Promoting would answer a question the caller did not ask."""
        assert "C3D10" not in write_deck(_mesh(), _case())

    def test_a_c3d4_row_carries_five_numbers(self) -> None:
        rows = _section(write_deck(_mesh(), _case()), "*ELEMENT")
        assert all(len([n for n in row.split(",") if n.strip()]) == 5 for row in rows)


class TestTheMidsideNodesAreInCalculixsOrder:
    """The trap that produces a valid, solvable, differently-shaped element.

    Ours is gmsh's ordering for element type 11 and is the codebase's single
    source of truth; Abaqus — and so CalculiX — orders the last two the other way
    round. Nothing about a swapped pair fails: the element still has ten nodes,
    still has positive volume, and still solves.
    """

    def test_the_permutation_produces_calculixs_edge_order(self) -> None:
        """Checked against the two edge tables, never against the constant itself.

        A test asserting `C3D10_MIDSIDE_ORDER == (0,1,2,3,5,4)` would agree with
        any typo in that tuple. This one says what the permutation must *achieve*.
        """
        produced = tuple(TET10_EDGES[slot] for slot in C3D10_MIDSIDE_ORDER)

        assert produced == C3D10_EDGES

    def test_it_is_a_permutation_and_loses_no_node(self) -> None:
        assert sorted(C3D10_MIDSIDE_ORDER) == [0, 1, 2, 3, 4, 5]

    def test_only_the_last_two_move(self) -> None:
        """Named precisely, because "the ordering differs" sends a reader to check six."""
        assert C3D10_MIDSIDE_ORDER[:4] == (0, 1, 2, 3)
        assert C3D10_MIDSIDE_ORDER[4:] == (5, 4)

    def test_the_written_row_uses_the_permuted_order(self) -> None:
        mesh = _mesh(quadratic=True)
        deck = write_deck(mesh, _case())
        first = [int(n) for n in _section(deck, "*ELEMENT")[0].split(",") if n.strip()]
        # element id, four corners, six midsides
        assert len(first) == 11
        expected = [int(mesh.midside[0][slot]) + 1 for slot in C3D10_MIDSIDE_ORDER]
        assert first[5:] == expected

    def test_the_permuted_row_is_not_the_raw_row(self) -> None:
        """If it were, this whole class would be checking nothing."""
        mesh = _mesh(quadratic=True)
        raw = [int(n) + 1 for n in mesh.midside[0]]
        permuted = [int(mesh.midside[0][slot]) + 1 for slot in C3D10_MIDSIDE_ORDER]

        assert raw != permuted, "pick a mesh whose last two midside nodes differ"


class TestDensityLeavesTheSystemHere:
    """mm-N-MPa has tonne as its mass unit. Miss this and gravity is out by 1e12."""

    def test_steel_is_written_as_the_number_an_engineer_recognises(self) -> None:
        deck = write_deck(_mesh(), _case())
        written = float(_section(deck, "*DENSITY")[0])

        assert written == pytest.approx(7.87e-9, rel=1e-12)

    def test_the_factor_is_the_one_the_unit_algebra_gives(self) -> None:
        """1 kg/m3 = 1e-3 tonne / 1e9 mm3 = 1e-12 tonne/mm3."""
        assert DENSITY_KG_M3_TO_TONNE_MM3 == 1e-12

    def test_the_deck_is_not_written_in_kilograms(self) -> None:
        deck = write_deck(_mesh(), _case())

        assert "7870" not in _section(deck, "*DENSITY")[0]


class TestFixturesHoldOnlyWhatTheyHold:
    def test_a_clamp_writes_all_three_degrees_of_freedom(self) -> None:
        deck = write_deck(_mesh(), _case())
        rows = [r for r in _section(deck, "*BOUNDARY") if r.strip()]

        assert len(rows) == 3
        assert sorted(int(r.split(",")[1]) for r in rows) == [1, 2, 3]

    def test_a_roller_writes_only_its_normal(self) -> None:
        """A roller written as a clamp is a stiffer part that never says so."""
        case = _case(
            fixtures=[
                Fixture(
                    where=FaceSelector(type="face", axis="z", side="min"),
                    dofs=["z"],
                )
            ]
        )
        # write_model, not write_deck: a single roller plane leaves five
        # rigid-body motions free, and write_deck now refuses that before it
        # writes anything. What is under test here is the *syntax* a fixture
        # produces, which is the model half's job and is unaffected by whether
        # the assembled case could be solved.
        _lines, rows = write_model(_mesh(), case.material, case.fixtures)

        assert len(rows) == 1
        assert int(rows[0].split(",")[1]) == 3

    def test_each_fixture_gets_its_own_set(self) -> None:
        """Merging two fixtures by region would drop one's held directions."""
        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min"), dofs=["z"]),
                Fixture(where=FaceSelector(type="face", axis="x", side="min"), dofs=["x"]),
            ]
        )
        # Two roller planes still leave four motions free, so this is the
        # model half's question too - see the note above.
        lines, _rows = write_model(_mesh(), case.material, case.fixtures)
        deck = "\n".join(lines)

        assert "NSET=FIX1" in deck
        assert "NSET=FIX2" in deck

    def test_a_fixture_that_selects_nothing_says_which_fixture(self) -> None:
        """Silently writing an empty set gives an unconstrained model, which
        CalculiX solves into a rigid-body translation of arbitrary size.

        `select_nodes` already refuses this and already names the selector — the
        half it cannot supply is which of the fixtures asked, and on a case with
        three that is the first thing you need. Both halves must survive.
        """
        from app.solve.types import BoxSelector

        far_away = BoxSelector(type="box", min=[500, 500, 500], max=[600, 600, 600])
        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min")),
                Fixture(where=far_away),
            ]
        )
        with pytest.raises(SolverError) as raised:
            write_deck(_mesh(), case)

        assert "Fixture 2" in str(raised.value), "which fixture"
        assert "matched no nodes" in str(raised.value), "and what it could not find"


class TestTheLoadsAreTheOnesTheOtherSolverGets:
    """6.5 keeps the hand-written solver as an oracle, and that only means
    something if both were handed identical loads."""

    def test_the_total_written_force_is_the_force_that_was_asked_for(self) -> None:
        """Tributary-area distribution spreads 8 kN over a face; it must still sum to 8 kN."""
        deck = write_deck(_mesh(), _case())
        total = 0.0
        for row in _section(deck, "*CLOAD"):
            _node, dof, value = (p.strip() for p in row.split(","))
            if int(dof) == 3:
                total += float(value)

        assert total == pytest.approx(8000.0, rel=1e-9)

    def test_no_force_leaks_into_the_other_directions(self) -> None:
        deck = write_deck(_mesh(), _case())
        sideways = [
            float(row.split(",")[2])
            for row in _section(deck, "*CLOAD")
            if int(row.split(",")[1]) in (1, 2)
        ]

        assert sum(abs(v) for v in sideways) == pytest.approx(0.0, abs=1e-9)

    def test_exact_zeros_are_not_written(self) -> None:
        """Writing every node triples the deck and changes no answer."""
        forces = np.zeros(3 * 5)
        forces[4] = 12.0

        assert _cload_lines(forces) == ["2, 2, 12.0"]

    def test_no_lines_means_no_cload_keyword(self) -> None:
        """An empty `*CLOAD` is a parse error in some CalculiX builds.

        Reached at the helper rather than through `write_deck`: `LoadCase`
        requires at least one load, so a case with none cannot be constructed and
        the branch in `write_deck` is defensive. Testing the helper says what the
        guard does without pretending the caller can reach it.
        """
        assert _cload_lines(np.zeros(3 * 4)) == []


class TestTheDeckDescribesTheMeshItWasGiven:
    def test_coordinates_round_trip_exactly(self) -> None:
        """Six significant figures would silently re-mesh the part."""
        mesh = _mesh()
        deck = write_deck(mesh, _case())
        written = np.array(
            [[float(p) for p in line.split(",")[1:]] for line in _section(deck, "*NODE")]
        )

        assert np.array_equal(written, mesh.nodes)

    def test_every_element_is_written(self) -> None:
        mesh = _mesh()
        rows = _section(write_deck(mesh, _case()), "*ELEMENT")

        assert len(rows) == len(mesh.tets)

    def test_the_deck_declares_its_units(self) -> None:
        """The one place the tonne convention is stated for whoever opens the file."""
        assert "mm, N, MPa, tonne" in write_deck(_mesh(), _case())

    def test_it_asks_for_the_fields_the_solver_output_needs_and_no_more(self) -> None:
        deck = write_deck(_mesh(), _case())

        assert "*NODE FILE" in deck and "U" in _section(deck, "*NODE FILE")
        assert "*EL FILE" in deck and "S" in _section(deck, "*EL FILE")

    def test_the_step_is_closed(self) -> None:
        """An unterminated step makes CalculiX read to EOF and report nothing useful."""
        deck = write_deck(_mesh(), _case())

        assert deck.strip().endswith("*END STEP")
        assert deck.count("*STEP") == 1

    def test_the_material_name_carries_no_spaces(self) -> None:
        deck = write_deck(_mesh(), _case())
        name = [line for line in deck.splitlines() if line.startswith("*MATERIAL")][0]

        assert " " not in name.split("NAME=")[1]
        assert "STEEL_1018" in name


class TestTheModelSeam:
    """`write_deck` is built from `write_model`, and must not have drifted.

    The split exists because the `*BOUNDARY` card belongs to the *step* while
    the `*NSET` blocks belong to the *model*: a static step, a `*FREQUENCY` step
    and a `*BUCKLE` step restrain the same nodes but each writes its own card in
    its own place, and a modal case has no `LoadCase` to hand a combined writer.
    Doing it by copying `write_deck`'s first half would have left two node
    writers and two DOF tables free to describe different models — and then 6.5's
    oracle would localise a disagreement to the deck writer rather than to the
    physics, which is the one thing that comparison exists to rule out.

    So the contract worth pinning is not the seam's shape, it is that extracting
    it changed nothing.
    """

    def test_the_model_lines_are_the_first_half_of_the_deck(self) -> None:
        from app.solve.calculix.deck import write_model

        mesh = _mesh()
        case = _case()

        lines, _boundary = write_model(mesh, case.material, case.fixtures)
        whole = write_deck(mesh, case).splitlines()

        assert lines == whole[: len(lines)]
        assert lines[0] == "*HEADING"
        assert lines[-1].startswith("*SOLID SECTION")

    def test_the_boundary_rows_carry_no_card(self) -> None:
        """The card is the step's to write, and a modal step writes its own."""
        from app.solve.calculix.deck import write_model

        mesh = _mesh()
        case = _case()

        _lines, boundary = write_model(mesh, case.material, case.fixtures)

        assert boundary
        assert not any(row.startswith("*") for row in boundary)
        assert all(row.count(",") == 3 for row in boundary)

    def test_every_restrained_set_is_declared_in_the_model_half(self) -> None:
        """A `*BOUNDARY` row naming a set the model never declared is a deck
        CalculiX refuses — and the two halves are now written by one function
        precisely so they cannot disagree about which sets exist."""
        from app.solve.calculix.deck import write_model

        mesh = _mesh()
        case = _case()

        lines, boundary = write_model(mesh, case.material, case.fixtures)

        declared = {
            line.split("NSET=")[1].strip() for line in lines if line.startswith("*NSET")
        }
        used = {row.split(",")[0].strip() for row in boundary}
        assert used <= declared

    def test_cload_rows_are_the_same_rows_the_static_deck_writes(self) -> None:
        from app.solve.calculix.deck import cload_data_lines
        from app.solve.loads import assemble_loads

        mesh = _mesh()
        case = _case()
        forces, _ = assemble_loads(mesh, case.loads, case.material.density_kg_m3)

        rows = cload_data_lines(forces)
        deck = write_deck(mesh, case).splitlines()

        start = deck.index("*CLOAD") + 1
        assert rows == deck[start : start + len(rows)]


class TestAnUnsolvableModelIsRefusedBeforeItIsWritten:
    """CalculiX does not detect a singular system, so we have to.

    Measured against ccx 2.23 on 2026-09-06: a deck whose `*BOUNDARY` block was
    removed came back **exit 0, a complete .frd, no `*ERROR`, no `*WARNING`** and
    a maximum displacement of 5.4e+11 mm. PaStiX factorises the singular system
    and returns a finite, meaningless vector — the same trap
    `linear_static._residual_is_small` exists to catch on our own solver, where
    the stiffness matrix is in hand. Here it is not, and federating is the whole
    reason. So the check moves *before* the solve, where it needs no matrix: the
    fixtures either remove all six rigid-body motions or they do not, and that is
    a property of the load case rather than of whoever solves it.
    """

    def test_one_roller_plane_is_refused(self) -> None:
        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min"), dofs=["z"])
            ]
        )

        with pytest.raises(SolverError, match="under-constrained"):
            write_deck(_mesh(), case)

    def test_the_refusal_names_which_motions_are_free(self) -> None:
        """"Under-constrained" alone costs the engineer the diagnosis. Which of
        the six is loose tells them which face to hold."""
        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min"), dofs=["z"])
            ]
        )

        with pytest.raises(SolverError) as refused:
            write_deck(_mesh(), case)

        message = str(refused.value)
        assert "translation along x" in message
        assert "rotation about z" in message

    def test_a_properly_restrained_case_is_untouched(self) -> None:
        """The guard must not cost a legitimate model anything."""
        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min"), dofs=["z"]),
                Fixture(where=FaceSelector(type="face", axis="x", side="min"), dofs=["x"]),
                Fixture(where=FaceSelector(type="face", axis="y", side="min"), dofs=["y"]),
            ]
        )

        assert "*STATIC" in write_deck(_mesh(), case)

    def test_a_clamped_face_is_enough_on_its_own(self) -> None:
        """A fixture with no `dofs` holds all three, which removes all six
        motions by itself. The commonest real case must not be refused."""
        case = _case(
            fixtures=[Fixture(where=FaceSelector(type="face", axis="z", side="min"))]
        )

        assert "*STATIC" in write_deck(_mesh(), case)

    def test_an_empty_fixture_still_says_which_fixture(self) -> None:
        """Both faults are true of that case; only one says what to fix, so the
        order of the two checks is load-bearing."""
        from app.solve.types import BoxSelector

        case = _case(
            fixtures=[
                Fixture(where=FaceSelector(type="face", axis="z", side="min")),
                Fixture(where=BoxSelector(type="box", min=[500, 500, 500], max=[600, 600, 600])),
            ]
        )

        with pytest.raises(SolverError) as raised:
            write_deck(_mesh(), case)

        assert "Fixture 2" in str(raised.value)
