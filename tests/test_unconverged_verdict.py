"""A verdict may not be stated from a solve that holds no evidence about itself.

Master plan E7 task 7, and the defect that produced it is worth stating exactly,
because it is the one Decision 3 exists to prevent and it shipped.

Measured through the GUI on the Windows seat, 2026-09-10
(`docs/verification-2026-09-10-night/`). Asked for a cantilever bracket that
"has to stay under 150 MPa", the product built it, solved it, and answered:

    PASSES. The peak stress of 140.6 MPa is below your limit of 150 MPa. The
    bracket has approximately 9 MPa of margin.

The result it had just read carried
`mesh_convergence: {converged: false, basis: "single-grid"}` and the sentence
"Solved on one mesh. Nothing here measures how much the answer would move on a
finer one, so treat the numbers as indicative." Neither reached the answer.

It was not marginal. The mesh was 808 **linear** tets -- one element through a
10 mm thickness -- and tip deflection came out 0.31 mm against beam theory's
1.17 mm. Challenged in the same conversation the agent got it entirely right,
running tet10 at 5 mm and 3.5 mm for 1.14 and 1.13 mm and ~74 MPa. So the coarse
mesh **overstated** peak stress by about 90%, and the "pass with 9 MPa of
margin" was luck: the same error the other way fails a good part.

**The model already had the block and ignored it**, which is why the fix is a
server-appended footnote and not a line in the system prompt. `unverified_footnote`
is the precedent -- the model's own words stand and what they rest on is stated
beside them.
"""

from __future__ import annotations

import inspect

from app.ai.verification import unconverged_footnote


class TestTheDefaultMeshIsQuadratic:
    """Linear tets were the default until 2026-09-11 and they are not fit to be.

    Three runs of one 200x40x10 cantilever from the identical request, against
    beam theory's 90.0 MPa and 1.171 mm:

        tet4    140.6 MPa (1.56x)   0.314 mm (0.27x)
        tet4     50.4 MPa (0.56x)   0.321 mm (0.27x)
        tet4     68.8 MPa (0.76x)   0.331 mm (0.28x)
        tet10    77.8 MPa (0.86x)   1.145 mm (0.98x)
        tet10    73.6 MPa (0.82x)   1.129 mm (0.96x)

    Deflection wrong by 3.6x systematically; peak stress scattered 2.8x across
    identical inputs, and every one of those three was reported as a verdict
    against a stated 150 MPa limit.
    """

    def test_the_api_default_is_quadratic(self) -> None:
        from app.schemas.simulation import SimulationCreate

        assert SimulationCreate.model_fields["element_order"].default == 2

    def test_the_agent_tool_default_is_quadratic(self) -> None:
        """The route and the tool default independently, and the tool is the one
        the agent actually calls -- so pinning only the schema would leave the
        defect exactly where it was measured."""
        from app.ai.tools import ToolBox

        signature = inspect.signature(ToolBox._run_simulation)

        assert signature.parameters["element_order"].default == 2

    def test_the_benchmarks_and_the_product_agree(self) -> None:
        """The argument that settled it: every NAFEMS case already passed
        `element_order=2` explicitly, so the product validated itself with
        quadratic elements while serving customers linear ones. If the default
        ever goes back to 1, that contradiction returns."""
        from app.ai.tools import ToolBox
        from app.schemas.simulation import SimulationCreate

        benchmark_order = 2
        assert SimulationCreate.model_fields["element_order"].default == benchmark_order
        assert (
            inspect.signature(ToolBox._run_simulation).parameters["element_order"].default
            == benchmark_order
        )

SINGLE_GRID = {
    "basis": "single-grid",
    "grids": 1,
    "converged": False,
    "detail": "Solved on one mesh. Nothing here measures how much the answer would move.",
    "gci_percent": None,
    "observed_order": None,
}

CONVERGED = {
    "basis": "grid-convergence-index",
    "grids": 3,
    "converged": True,
    "gci_percent": 1.8,
    "observed_order": 1.97,
}


def _run(convergence: dict, run_id: str = "df1302ba", mesh_stats: dict | None = None) -> dict:
    """The shape `get_simulation` really returns -- result nested under `result`,
    and `mesh_stats` a *sibling* of it rather than part of it."""
    return {
        "id": run_id,
        "status": "succeeded",
        "mesh_stats": mesh_stats if mesh_stats is not None else {"sliver_count": 0},
        "result": {
            "max_von_mises_mpa": 140.56729603861288,
            "max_displacement_mm": 0.3140543152905493,
            "factor_of_safety": 2.632191202556557,
            "mesh_convergence": convergence,
        },
    }


#: The mesh that produced 140.6 MPa on the seat, 2026-09-10.
SLIVERED = {"sliver_count": 1, "min_quality": 0.06163734076386371, "element_count": 808}
#: The mesh that produced 50.4 MPa from the same load case, minutes later.
CLEAN = {"sliver_count": 0, "min_quality": 0.4904821967555558, "element_count": 809}


class TestAnUnconvergedRunSaysSo:
    def test_a_single_grid_result_gets_a_footnote(self) -> None:
        note = unconverged_footnote([_run(SINGLE_GRID)])

        assert "not converged" in note
        assert "df1302ba" in note, "the reader must be able to tell which run"

    def test_it_says_a_margin_is_not_a_verdict(self) -> None:
        """The sentence has to reach the thing the user was about to do with the
        number -- rely on 9 MPa of margin -- not merely describe the mesh."""
        note = unconverged_footnote([_run(SINGLE_GRID)])

        assert "indicative, not a verdict" in note
        assert "margin" in note

    def test_it_names_the_way_out(self) -> None:
        """A caveat with no remedy trains people to scroll past it."""
        assert "grids: 3" in unconverged_footnote([_run(SINGLE_GRID)])

    def test_a_converged_run_is_left_alone(self) -> None:
        """Decision 3's rule is that an *unconverged* number is worse than no
        number, not that every number needs a disclaimer. A study that ran and
        converged is evidence, and repeating it on every answer is noise that
        makes the real warning invisible."""
        assert unconverged_footnote([_run(CONVERGED)]) == ""

    def test_a_turn_that_solved_nothing_gets_nothing(self) -> None:
        assert unconverged_footnote([{"feature": "Pad.1"}, {"mass_kg": 0.63}]) == ""
        assert unconverged_footnote([]) == ""

    def test_a_result_that_is_not_a_dict_cannot_break_the_turn(self) -> None:
        """Tool results are JSON from a daemon; a string or a None must not take
        down the answer that was about to be shown."""
        assert unconverged_footnote(["ok", None, 42, _run(SINGLE_GRID)]) != ""

    def test_one_run_read_three_times_is_named_once(self) -> None:
        """The agent polls `get_simulation` while a job runs. Three identical
        warnings for one solve reads as three bad solves."""
        note = unconverged_footnote([_run(SINGLE_GRID)] * 3)

        assert note.count("not converged") == 1

    def test_two_different_runs_are_both_named(self) -> None:
        note = unconverged_footnote([_run(SINGLE_GRID, "aaa"), _run(SINGLE_GRID, "bbb")])

        assert "aaa" in note and "bbb" in note

    def test_a_flat_result_without_the_nested_key_is_still_read(self) -> None:
        """`get_simulation` nests under `result`; `run_simulation` and any tool
        added later may not. Keyed on the shape, not on the caller."""
        flat = {"id": "flat-1", "max_von_mises_mpa": 140.6, "mesh_convergence": SINGLE_GRID}

        assert "flat-1" in unconverged_footnote([flat])

    def test_a_sliver_is_named_because_it_is_where_the_peak_stress_came_from(self) -> None:
        """Measured 2026-09-10 and the reason this exists: two runs of the same
        cantilever, same load case, 808 and 809 elements, reported **140.6 MPa**
        and **50.4 MPa** — a factor of 2.8 — with deflection barely moving
        (0.314 vs 0.321 mm). The only thing separating them was `min_quality`
        0.062 against 0.490. Both were shown to the user as a verdict against a
        150 MPa limit. The mesher had counted the sliver all along.
        """
        note = unconverged_footnote([_run(SINGLE_GRID, mesh_stats=SLIVERED)])

        assert "sliver" in note
        assert "0.062" in note, "the reader needs the number, not just the word"
        assert "factor of two" in note, "and what it does to the answer"

    def test_a_clean_mesh_is_not_accused_of_slivers(self) -> None:
        note = unconverged_footnote([_run(SINGLE_GRID, mesh_stats=CLEAN)])

        assert "not converged" in note
        assert "sliver" not in note

    def test_missing_mesh_stats_do_not_invent_a_sliver(self) -> None:
        """An older row, or a solver that reported no stats, must not be
        described as having a mesh defect nobody measured."""
        run = _run(SINGLE_GRID)
        del run["mesh_stats"]

        note = unconverged_footnote([run])

        assert "not converged" in note
        assert "sliver" not in note

    def test_a_missing_converged_flag_is_treated_as_unconverged(self) -> None:
        """Absent is not the same as true. An older row, or a solver that did
        not fill the block in, must not read as evidence it does not have."""
        note = unconverged_footnote([_run({"basis": "single-grid", "grids": 1})])

        assert "not converged" in note
