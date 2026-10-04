"""Steady conduction through CalculiX, and the oracle that compares it with ours.

THE QUEUE E3. Two halves, and they are separated on purpose:

* **The deck writer is tested with no `ccx` at all**, the way
  `tests/test_shell_solver.py` tests its deck — the cards, the degree of
  freedom, the face labels and the refusals are all assertions about text, and
  they must keep working on a machine that cannot run a solver.
* **The oracle needs the real binary** and is skipped without it, loudly. A skip
  is not a pass; the whole point of this file's second half is that it ran.

The numbers in the seat-run tests were measured on ccx 2.23 on 2026-09-17, on
the Windows machine, and the two solvers agreed on all five cases including a
convection film.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.primitives import box_mesh, promote_to_tet10
from app.mesh.types import _TET_FACES, TetMesh
from app.solve.calculix.conduction import (
    CCX_TET_FACE_CORNERS,
    TEMPERATURE_DOF,
    TET_FACE_TO_CCX,
    CalculiXConductionSolver,
    write_conduction_deck,
)
from app.solve.calculix.run import find_ccx
from app.solve.conduction import (
    Convection,
    FixedTemperature,
    HeatFlux,
    SteadyConductionSolver,
    ThermalCase,
    convecting_bar_tip_temperature_k,
)
from app.solve.oracle import compare_conduction
from app.solve.registry import build_conduction_solver
from app.solve.types import SolverError

LENGTH = 100.0
SIDE = 10.0
CONDUCTIVITY = 51.9

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

needs_ccx = pytest.mark.skipif(
    find_ccx() is None, reason="no CalculiX binary on this machine"
)


def _bar() -> TetMesh:
    return box_mesh((LENGTH, SIDE, SIDE), (10, 2, 2))


def _held_ends() -> ThermalCase:
    return ThermalCase(
        name="linear bar",
        conductivity_w_mk=CONDUCTIVITY,
        boundaries=[
            FixedTemperature(
                where={"type": "face", "axis": "x", "side": "min"}, temperature_k=300.0
            ),
            FixedTemperature(
                where={"type": "face", "axis": "x", "side": "max"}, temperature_k=400.0
            ),
        ],
    )


def _convecting_tip() -> ThermalCase:
    return ThermalCase(
        name="convecting tip",
        conductivity_w_mk=CONDUCTIVITY,
        boundaries=[
            FixedTemperature(
                where={"type": "face", "axis": "x", "side": "min"}, temperature_k=400.0
            ),
            Convection(
                where={"type": "face", "axis": "x", "side": "max"},
                film_coefficient_w_m2k=500.0,
                ambient_temperature_k=300.0,
            ),
        ],
    )


class TestTheFaceLabelsAreDerivedNotTyped:
    """The one table in this module that could silently be wrong."""

    def test_every_local_face_maps_to_exactly_one_calculix_face(self) -> None:
        assert sorted(TET_FACE_TO_CCX) == [1, 2, 3, 4]

    def test_each_mapping_names_a_face_with_the_same_corners(self) -> None:
        """The derivation restated as its own claim, corner set by corner set.

        Asserting `TET_FACE_TO_CCX == (1, 2, 3, 4)` alone would pass on a table
        somebody typed out from memory that happened to be the identity. This
        checks the *property* the mapping has to have.
        """
        for local, ccx_face in enumerate(TET_FACE_TO_CCX):
            ours = frozenset(int(i) for i in _TET_FACES[local])
            theirs = frozenset(CCX_TET_FACE_CORNERS[ccx_face - 1])
            assert ours == theirs, (local, ccx_face, sorted(ours), sorted(theirs))

    def test_it_comes_out_as_the_identity_on_this_build(self) -> None:
        """Recorded because it is load-bearing and surprising.

        Two element libraries written a decade and a continent apart order a
        tetrahedron's faces the same way. Written down so a change to
        `_TET_FACES` fails here rather than quietly relabelling every film.
        """
        assert TET_FACE_TO_CCX == (1, 2, 3, 4)


class TestTheOwnerMappingIsAFunction:
    def test_every_boundary_triangle_names_one_tet_and_one_of_its_faces(self) -> None:
        mesh = _bar()
        owners = mesh.surface_face_owners
        assert owners.shape == (len(mesh.surface_triangles), 2)
        assert owners[:, 0].max() < mesh.tet_count
        assert set(np.unique(owners[:, 1])) <= {0, 1, 2, 3}

    def test_the_named_face_really_carries_the_triangle_s_corners(self) -> None:
        """The join, checked against the geometry rather than against itself.

        A wrong `// 4` or `% 4` would still produce in-range indices and a
        plausible-looking film; only the corner sets catch it.
        """
        mesh = _bar()
        for triangle, (element, local) in zip(
            mesh.surface_triangles, mesh.surface_face_owners, strict=True
        ):
            corners = mesh.tets[element][_TET_FACES[local]]
            assert set(int(n) for n in corners) == set(int(n) for n in triangle)


class TestTheDeckSaysWhatItMeans:
    def test_a_held_temperature_is_a_boundary_card_on_dof_eleven(self) -> None:
        deck = write_conduction_deck(_bar(), _held_ends())
        assert "*HEAT TRANSFER, STEADY STATE" in deck
        assert "*BOUNDARY" in deck
        assert f", {TEMPERATURE_DOF}, {TEMPERATURE_DOF}, 300" in deck
        assert f", {TEMPERATURE_DOF}, {TEMPERATURE_DOF}, 400" in deck

    def test_the_conductivity_is_written_in_watts_per_millimetre_kelvin(self) -> None:
        """The unit boundary, asserted as a number and not as a card name."""
        deck = write_conduction_deck(_bar(), _held_ends())
        lines = deck.splitlines()
        value = lines[lines.index("*CONDUCTIVITY") + 1]
        assert float(value) == pytest.approx(CONDUCTIVITY * 1e-3)

    def test_an_initial_temperature_is_written_because_ccx_needs_one(self) -> None:
        deck = write_conduction_deck(_bar(), _held_ends())
        assert "*INITIAL CONDITIONS, TYPE=TEMPERATURE" in deck

    def test_it_asks_for_the_temperature_and_the_reaction_flux(self) -> None:
        deck = write_conduction_deck(_bar(), _held_ends())
        assert "*NODE FILE" in deck
        assert "NT, RFL" in deck

    def test_a_film_becomes_film_rows_naming_elements_and_faces(self) -> None:
        deck = write_conduction_deck(_bar(), _convecting_tip())
        rows = [line for line in deck.splitlines() if line.startswith(("1,", "2,")) is False]
        assert "*FILM" in deck
        film_index = deck.splitlines().index("*FILM")
        first = deck.splitlines()[film_index + 1]
        element, face, ambient, coefficient = [part.strip() for part in first.split(",")]
        assert face in {"F1", "F2", "F3", "F4"}
        assert float(ambient) == pytest.approx(300.0)
        # W/(m^2 K) -> W/(mm^2 K), the same conversion the in-house film makes.
        assert float(coefficient) == pytest.approx(500.0 * 1e-6)
        assert int(element) >= 1
        assert rows  # the deck is not empty, which would make the above vacuous

    def test_a_flux_and_a_source_become_one_cflux_block(self) -> None:
        """Both are nodal heat, and writing two blocks would double-count a node
        that carries a flux *and* a share of the source."""
        case = ThermalCase(
            name="flux and source",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                FixedTemperature(
                    where={"type": "face", "axis": "x", "side": "min"}, temperature_k=320.0
                ),
                HeatFlux(
                    where={"type": "face", "axis": "x", "side": "max"}, flux_w_m2=25_000.0
                ),
            ],
            volumetric_source_w_m3=2.0e6,
        )
        deck = write_conduction_deck(_bar(), case)
        assert deck.count("*CFLUX") == 1
        lines = deck.splitlines()
        start = lines.index("*CFLUX") + 1
        total = 0.0
        for line in lines[start:]:
            if line.startswith("*"):
                break
            _, dof, value = [part.strip() for part in line.split(",")]
            assert int(dof) == TEMPERATURE_DOF
            total += float(value)
        # 2 MW/m^3 over 1e-5 m^3 is 20 W, plus 25 kW/m^2 over 1e-4 m^2 is 2.5 W.
        assert total == pytest.approx(22.5, rel=1e-9)

    def test_a_model_with_nothing_holding_its_level_is_refused_before_the_solver(
        self,
    ) -> None:
        """CalculiX factorises the singular system and returns a finite,
        meaningless field, so this cannot be left to the solver."""
        case = ThermalCase(
            name="floating",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                HeatFlux(where={"type": "face", "axis": "x", "side": "max"}, flux_w_m2=100.0)
            ],
        )
        with pytest.raises(SolverError, match="thermally floating"):
            write_conduction_deck(_bar(), case)

    def test_two_regions_holding_one_node_at_two_temperatures_are_refused(self) -> None:
        """The same refusal the in-house solver makes, from the same code."""
        case = ThermalCase(
            name="overlapping",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                FixedTemperature(
                    where={"type": "box", "min": [-1, -1, -1], "max": [1, 11, 11]},
                    temperature_k=300.0,
                    name="left",
                ),
                FixedTemperature(
                    where={"type": "box", "min": [-1, -1, -1], "max": [1, 11, 11]},
                    temperature_k=400.0,
                    name="also left",
                ),
            ],
        )
        with pytest.raises(SolverError, match="cannot prescribe one node twice"):
            write_conduction_deck(_bar(), case)


class TestTheRegistryOffersIt:
    def test_calculix_is_now_a_conduction_backend(self) -> None:
        assert isinstance(
            build_conduction_solver("calculix"), CalculiXConductionSolver
        )

    def test_an_unknown_name_is_still_refused_with_the_list(self) -> None:
        with pytest.raises(SolverError, match="calculix, internal"):
            build_conduction_solver("nothing-of-the-sort")


@needs_ccx
class TestTheTwoSolversAgree:
    """Measured on ccx 2.23, Windows seat, 2026-09-17.

    Every case here ran and agreed. The tolerances are `oracle`'s own, not
    loosened for this file: the temperature rows are judged against the field's
    span at 1e-3, and the boundary heat at 1e-2.
    """

    def test_a_bar_held_at_both_ends(self) -> None:
        agreement = compare_conduction(
            _bar(), _held_ends(), SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert agreement.ran, agreement.reason
        assert agreement.agrees, agreement.report()

    def test_the_same_bar_on_quadratic_elements(self) -> None:
        mesh = promote_to_tet10(box_mesh((LENGTH, SIDE, SIDE), (5, 1, 1)))
        agreement = compare_conduction(
            mesh, _held_ends(), SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert agreement.ran, agreement.reason
        assert agreement.agrees, agreement.report()

    def test_a_convection_film_lands_on_the_right_faces(self) -> None:
        """The case that proves `TET_FACE_TO_CCX`.

        A film written onto the wrong faces solves perfectly cleanly and answers
        a different problem. Nothing but a comparison catches it, which is why
        this is the case the face mapping exists for.
        """
        agreement = compare_conduction(
            _bar(), _convecting_tip(), SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert agreement.ran, agreement.reason
        assert agreement.agrees, agreement.report()

    def test_the_convecting_tip_also_matches_its_closed_form(self) -> None:
        """Both solvers against the Biot tip temperature, not just each other.

        Two implementations agreeing on a wrong answer is the failure mode an
        oracle cannot see, so the case that exercises the film is also held to
        mathematics. Measured: closed form 350.932287 K, in-house 350.932287 K,
        CalculiX 350.932000 K — the last limited by the `.frd`'s fixed-width
        field rather than by the solve.
        """
        mesh = _bar()
        case = _convecting_tip()
        expected = convecting_bar_tip_temperature_k(
            base_temperature_k=400.0,
            ambient_temperature_k=300.0,
            film_coefficient_w_m2k=500.0,
            conductivity_w_mk=CONDUCTIVITY,
            length_mm=LENGTH,
        )
        tip = np.flatnonzero(np.isclose(mesh.nodes[:, 0], LENGTH))
        for solver in (SteadyConductionSolver(), CalculiXConductionSolver()):
            field = solver.solve(mesh, case)
            measured = float(field.temperatures_k[tip].mean())
            assert measured == pytest.approx(expected, abs=1e-3), solver.name

    def test_a_flux_and_a_source_agree_including_the_boundary_heat(self) -> None:
        """The case that found the `RFL` defect.

        Read raw, CalculiX's reaction block gave -21.5 W where the held face
        must remove all 22.5 W that entered — exactly the source's own share of
        the material tributary to the held nodes. `_boundary_heat` subtracts the
        applied heat there, and the two now agree; without that subtraction this
        assertion fails at 4.4%.
        """
        case = ThermalCase(
            name="flux and source",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                FixedTemperature(
                    where={"type": "face", "axis": "x", "side": "min"}, temperature_k=320.0
                ),
                HeatFlux(
                    where={"type": "face", "axis": "x", "side": "max"}, flux_w_m2=25_000.0
                ),
            ],
            volumetric_source_w_m3=2.0e6,
        )
        agreement = compare_conduction(
            _bar(), case, SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert agreement.ran, agreement.reason
        assert agreement.agrees, agreement.report()
        heat = next(
            d for d in agreement.differences if d.name == "fixed_temperature_heat_w"
        )
        assert heat.reference == pytest.approx(-22.5, rel=1e-6)
        assert heat.candidate == pytest.approx(-22.5, rel=1e-3)

    def test_films_alone_set_the_level_with_no_held_region(self) -> None:
        """A film is a well-posed boundary on its own, and both solvers say so."""
        case = ThermalCase(
            name="all-film",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                Convection(
                    where={"type": "face", "axis": "x", "side": "min"},
                    film_coefficient_w_m2k=2000.0,
                    ambient_temperature_k=300.0,
                ),
                Convection(
                    where={"type": "face", "axis": "x", "side": "max"},
                    film_coefficient_w_m2k=50.0,
                    ambient_temperature_k=500.0,
                ),
            ],
            volumetric_source_w_m3=1.0e6,
        )
        agreement = compare_conduction(
            _bar(), case, SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert agreement.ran, agreement.reason
        assert agreement.agrees, agreement.report()

    def test_a_solver_that_refuses_makes_the_comparison_unmeasured(self) -> None:
        """Never a pass, and the refusing solver's own words are carried."""
        case = ThermalCase(
            name="floating",
            conductivity_w_mk=CONDUCTIVITY,
            boundaries=[
                HeatFlux(where={"type": "face", "axis": "x", "side": "max"}, flux_w_m2=100.0)
            ],
        )
        agreement = compare_conduction(
            _bar(), case, SteadyConductionSolver(), CalculiXConductionSolver()
        )
        assert not agreement.ran
        assert not agreement.agrees
        assert "thermally floating" in agreement.reason
        assert "UNMEASURED" in agreement.report()


class TestTheComparisonJudgesTheRightScale:
    def test_a_temperature_row_is_divided_by_the_span_not_by_the_kelvin_value(
        self,
    ) -> None:
        """The reason `Difference.scale` exists.

        400.4 K against 400 K is a thousandth of the kelvin scale and a whole
        four-hundredth of a problem that spans 100 K. Judged the first way, a
        solver that got the rise wrong by 0.4% passes.
        """
        from app.solve.oracle import _difference

        against_value = _difference("t", 400.0, 400.4, 1e-3)
        against_span = _difference("t", 400.0, 400.4, 1e-3, scale=100.0)
        assert against_value.agrees is True
        assert against_span.agrees is False
        assert against_span.scale == 100.0

    def test_a_zero_reference_with_a_scale_is_a_fraction_of_that_scale(self) -> None:
        """`peak_nodal_difference_k` has reference 0 — the claim is that the two
        fields coincide — so dividing by the reference would be meaningless."""
        from app.solve.oracle import _difference

        difference = _difference("peak", 0.0, 0.05, 1e-3, scale=100.0)
        assert difference.relative == pytest.approx(5e-4)
        assert difference.agrees is True
