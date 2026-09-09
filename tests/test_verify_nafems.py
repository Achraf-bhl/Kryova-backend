"""The NAFEMS catalogue — master plan 7.1.

`tests/test_verify_benchmarks.py` proves the machinery cannot be lied to. This
file proves the *catalogue built on it* does not lie, which is a different job
with a different failure mode: not a malformed target, but a well-formed one
carrying a number nobody read off a document.

Three things are pinned, and only the third is about arithmetic.

**Every citation is checkable.** A target's `source` must be one of the strings
in `SOURCES`, and every one of those must carry a URL and the date it was read.
An inline citation is the shape a remembered number takes when somebody is in a
hurry, and `register.py` republishes the string to readers outside this
repository, where a plausible citation and a real one look identical.

**Every case that does not run says what would make it run, in a vocabulary that
can be counted.** `Blocker` is an enum precisely so "three cases are waiting on
shells" is expressible; a case with no blocker is invisible in the one report
this catalogue exists to produce, and a `Blocker` no case uses is a claim about
the product that no case backs.

**Two cases actually validate.** Three grids each, a Grid Convergence Index, and
the answer compared against a published reference. The model is checked
separately from the answer in both, because every way of getting these wrong
produces a *plausible* number rather than an error: a plate accidentally clamped
flat has a believable frequency, a restraint band sized as a fraction of the
bounding box holds a slab instead of a face, and a mesh coarse enough to chord an
ellipse is solving a smaller part while still converging nicely.
"""

from __future__ import annotations

import math
import re

import numpy as np
import pytest

from app.mesh.types import TetMesh
from app.solve.modal import ModalEigenSolver
from app.solve.plane import PlaneState
from app.solve.selection import select_nodes
from app.solve.types import EllipticalWallSelector, FaceSelector, Fixture, ModalCase
from app.verify import nafems
from app.verify.benchmarks import (
    Benchmark,
    Outcome,
    Target,
    TargetBasis,
    run_benchmark,
    run_suite,
)
from app.verify.convergence import Verdict
from app.verify.nafems import (
    BY_ID,
    CASES,
    FV52_MATERIAL,
    FV52_QUANTITY,
    FV52_SIDE_MM,
    FV52_THICKNESS_MM,
    NAFEMS_SUITE,
    Blocker,
    Case,
    blockers,
    fv52_case,
    fv52_fixtures,
    fv52_mesh,
    report,
    run_fv52,
)

_URL = re.compile(r"https://\S+")
_DATE_READ = re.compile(r"\b20\d\d-\d\d-\d\d\b")


@pytest.fixture(scope="module")
def le10_outcome():
    """LE10 solved once for the whole module.

    Three grids of a meshed elliptical solid is about forty seconds; running it
    per test made this file the slowest in the suite for no extra evidence,
    since every assertion below is about the same single outcome.
    """
    return run_benchmark(BY_ID["nafems-le10"].benchmark)


@pytest.fixture(scope="module")
def le1_outcome():
    """LE1 solved once for the whole module, for the same reason as LE10.

    Four seconds rather than forty, which is the plane model earning its keep:
    the same curved boundary and the same published answer, on a tenth of the
    degrees of freedom.
    """
    return run_benchmark(BY_ID["nafems-le1"].benchmark)


@pytest.fixture(scope="module")
def le11_outcome():
    """LE11 solved once for the whole module."""
    return run_benchmark(BY_ID["nafems-le11"].benchmark)


class TestEveryNumberInTheCatalogueIsTraceableToADocument:
    def test_every_target_that_claims_a_value_cites_a_source_from_the_source_table(
        self,
    ) -> None:
        """An inline citation is how a remembered number gets in.

        `Target` already refuses an empty source, so the only remaining way to
        introduce one is to type a plausible sentence at the call site. Requiring
        the string to be a member of `SOURCES` makes every citation in the
        catalogue reviewable in one place.
        """
        citations = set(nafems.SOURCES.values())

        for case in CASES:
            target = case.benchmark.target
            if not target.known:
                continue
            assert target.source in citations, (
                f"{case.benchmark.id} cites a source that is not in SOURCES: "
                f"{target.source[:80]!r}"
            )

    def test_every_source_names_a_url_and_the_date_it_was_read(self) -> None:
        for key, source in nafems.SOURCES.items():
            assert _URL.search(source), f"{key} gives no URL to check it against"
            assert _DATE_READ.search(source), f"{key} does not say when it was read"

    def test_every_source_names_the_nafems_publication_it_reproduces(self) -> None:
        """The vendor manual is the copy; NAFEMS is the source of the number.

        A citation naming only the manual would leave a reader unable to tell a
        reproduction of the standard benchmark from that vendor's own example.
        """
        for key, source in nafems.SOURCES.items():
            assert "NAFEMS" in source, f"{key} does not name the NAFEMS publication"

    def test_every_source_in_the_table_is_used_by_a_case(self) -> None:
        """A citation with no case is a document nobody checked anything against."""
        cited = {case.benchmark.target.source for case in CASES}
        for reference in (r for case in CASES for r in case.benchmark.references):
            cited.add(reference)

        for key, source in nafems.SOURCES.items():
            assert source in cited, f"SOURCES[{key!r}] is not used by any case"


class TestACaseThatCannotRunSaysWhatWouldMakeItRun:
    def test_every_case_either_runs_or_names_a_blocker(self) -> None:
        for case in CASES:
            assert case.benchmark.runnable ^ (case.blocker is not None), (
                f"{case.benchmark.id} must do exactly one of run and declare a blocker"
            )

    def test_a_case_that_runs_and_also_names_a_blocker_is_refused(self) -> None:
        runnable = BY_ID["nafems-fv52"].benchmark

        with pytest.raises(ValueError, match="runs and also names a blocker"):
            Case(benchmark=runnable, blocker=Blocker.NO_SHELL_SOLVER)

    def test_a_case_that_does_not_run_and_names_no_blocker_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names no blocker"):
            Case(benchmark=BY_ID["nafems-le3"].benchmark)

    def test_every_blocker_in_the_vocabulary_is_used_by_a_case(self) -> None:
        """A declared blocker no case backs is a claim about the product with no
        evidence — and it inflates the one report this catalogue produces."""
        used = set(blockers())

        assert used == set(Blocker), f"unused blockers: {set(Blocker) - used}"

    def test_a_blocked_reason_leads_with_what_is_specific_to_the_case(self) -> None:
        """Five blocked cases sharing one family must still read as five lines.

        The shared explanation is appended, so the first sentence of each is the
        one a reader needs; a reason that opened with the family text would make
        a report of four cases look like one message repeated.
        """
        openings = set()
        for case in CASES:
            if case.blocker is None:
                continue
            reason = case.benchmark.blocked_reason
            shared = nafems.BLOCKER_DETAIL[case.blocker]
            assert reason.endswith(shared)
            openings.add(reason[: -len(shared)].strip())

        assert len(openings) == len([c for c in CASES if c.blocker is not None])

    def test_the_report_names_every_blocker_and_the_cases_it_holds(self) -> None:
        text = report()

        for blocker, ids in blockers().items():
            assert str(blocker) in text
            for case_id in ids:
                assert case_id in text


class TestTheSuiteIsWellFormed:
    def test_every_case_id_is_prefixed_so_a_register_row_names_its_origin(self) -> None:
        for case in CASES:
            assert case.benchmark.id.startswith("nafems-")

    def test_the_suite_holds_exactly_the_catalogued_cases_in_order(self) -> None:
        assert NAFEMS_SUITE.benchmarks == tuple(case.benchmark for case in CASES)

    def test_a_blocked_case_is_reported_even_when_slow_cases_are_skipped(self) -> None:
        """The blocker is the point, and it costs nothing to state."""
        outcomes = run_suite(NAFEMS_SUITE, include_slow=False)
        reported = {outcome.benchmark_id for outcome in outcomes}

        for case in CASES:
            if case.blocker is not None:
                assert case.benchmark.id in reported
        # Every case that runs is marked slow — each one meshes and solves three
        # grids — so with the slow ones skipped, everything reported is blocked.
        # If a fast runnable case is ever added this asserts the wrong thing and
        # should be narrowed rather than deleted.
        assert all(outcome.outcome is Outcome.BLOCKED for outcome in outcomes)
        assert reported == {
            case.benchmark.id for case in CASES if case.blocker is not None
        }

    def test_a_blocked_case_still_publishes_the_number_it_must_eventually_produce(
        self,
    ) -> None:
        """Being unable to compute an answer is a schedule fact, not a reason to
        drop the answer."""
        for case_id in ("nafems-le3", "nafems-le11"):
            target = BY_ID[case_id].benchmark.target
            assert target.basis is TargetBasis.PUBLISHED
            assert target.value is not None


class TestTheFV52ModelIsTheOneNAFEMSPosed:
    def test_the_plate_is_ten_metres_square_and_one_metre_thick_in_mm(self) -> None:
        assert (FV52_SIDE_MM, FV52_THICKNESS_MM) == (10_000.0, 1_000.0)

    def test_the_material_is_the_one_the_benchmark_states(self) -> None:
        assert FV52_MATERIAL.youngs_modulus_mpa == pytest.approx(200_000.0)
        assert FV52_MATERIAL.poissons_ratio == pytest.approx(0.3)
        assert FV52_MATERIAL.density_kg_m3 == pytest.approx(8000.0)

    def test_the_restraints_hold_only_the_out_of_plane_direction(self) -> None:
        for fixture in fv52_fixtures():
            assert fixture.held == ["z"]

    def test_the_restraints_pick_up_the_four_edges_and_not_the_whole_underside(
        self,
    ) -> None:
        """A face selector would clamp the plate flat.

        That model has no rigid-body modes and a much higher first frequency, and
        it would still solve and still look like a plate — which is why the count
        is asserted rather than the intent.
        """
        mesh = fv52_mesh(4)
        held = set()
        for fixture in fv52_fixtures():
            held.update(int(node) for node in select_nodes(mesh, fixture.where))

        underside = set(
            int(node)
            for node in select_nodes(mesh, FaceSelector(axis="z", side="min"))
        )
        assert held < underside, "the edge strips must be a strict subset of the face"
        # 5x5 corner nodes on the underside of a 4x4 grid, of which the 16 on the
        # boundary ring are held; tet10 adds a midside node per edge of the ring.
        assert len(held) == 32

    def test_three_rigid_body_modes_come_first_which_is_what_makes_mode_four_the_answer(
        self,
    ) -> None:
        """Held only out of plane, the plate can still slide in x and y and spin
        about z. NAFEMS's own reference row records the same three."""
        mesh = fv52_mesh(4)
        output = ModalEigenSolver().solve(mesh, fv52_case())

        assert output.result.rigid_body_modes == 3

    def test_the_quantity_read_is_the_fourth_frequency(self) -> None:
        mesh = fv52_mesh(4)
        output = ModalEigenSolver().solve(mesh, fv52_case())

        assert FV52_QUANTITY.read(mesh, output) == pytest.approx(
            output.frequencies_hz[3]
        )

    def test_refining_scales_all_three_edges_so_the_grids_are_one_family(self) -> None:
        """A GCI over grids refined only in plan measures a different sequence
        than the one its formula assumes."""
        coarse, fine = fv52_mesh(4), fv52_mesh(8)

        assert fine.tet_count == 8 * coarse.tet_count

    def test_the_mesh_is_quadratic_because_a_constant_strain_element_cannot_bend(
        self,
    ) -> None:
        assert fv52_mesh(4).element_order == 2

    def test_a_plan_division_count_that_leaves_no_thickness_is_refused(self) -> None:
        with pytest.raises(ValueError, match="through-thickness"):
            fv52_mesh(3)


class TestFV52ValidatesAgainstThePublishedReference:
    def test_the_case_converges_over_three_grids(self) -> None:
        run = run_fv52()

        assert run.convergence is not None
        assert run.convergence.verdict is Verdict.CONVERGED
        assert len(run.convergence.levels) == 3

    def test_the_reported_value_is_the_one_the_study_permits(self) -> None:
        """Not the finest raw answer read separately — the same object, so the
        number and its evidence cannot come apart."""
        run = run_fv52()

        assert run.convergence is not None
        assert run.value == run.convergence.stated_value

    def test_it_lands_inside_the_published_band(self) -> None:
        outcome = run_benchmark(BY_ID["nafems-fv52"].benchmark)

        assert outcome.outcome is Outcome.VALIDATED
        assert outcome.relative_deviation is not None
        assert abs(outcome.relative_deviation) < 0.05

    def test_the_provenance_binds_the_answer_to_the_mesh_it_was_read_from(self) -> None:
        run = run_fv52()
        payload = run.provenance.to_dict()

        assert payload["analysis"] == "modal"
        assert payload["notes"] == {"benchmark": "nafems-fv52"}
        assert payload["geometry"]["digest"].startswith("sha256:")
        assert payload["convergence"]["verdict"] == "converged"

    def test_an_unconverged_study_reports_no_number_rather_than_a_deviation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """7.2 reaching the benchmark layer.

        Two grids cannot yield an observed order, so the study refuses a value —
        and the case must arrive as `UNCONVERGED`, which is not a pass and is
        deliberately not `DEVIATED`: the model may be right and nobody can tell.
        """
        monkeypatch.setattr(nafems, "FV52_DIVISIONS", (4, 8))
        outcome = run_benchmark(
            Benchmark(
                id="nafems-fv52-two-grids",
                title="FV52 on two grids",
                analysis="modal",
                description="the same case, deliberately under-evidenced",
                target=BY_ID["nafems-fv52"].benchmark.target,
                run=run_fv52,
            )
        )

        assert outcome.outcome is Outcome.UNCONVERGED
        assert outcome.measured_value is None

    def test_a_target_the_catalogue_could_not_source_would_never_pass(self) -> None:
        """The shape an unsourced FV52 must take, pinned so it stays available.

        If a future case cannot be traced to a document, this is what it looks
        like: fully encoded, run, measured, and honestly not validated.
        """
        outcome = run_benchmark(
            Benchmark(
                id="nafems-fv52-unsourced",
                title="FV52 with no citable reference",
                analysis="modal",
                description="the same case with its target withheld",
                target=Target(
                    basis=TargetBasis.UNKNOWN,
                    unit="Hz",
                    reason="the reference row has not been read off a document",
                ),
                run=run_fv52,
            )
        )

        assert outcome.outcome is Outcome.MEASURED
        assert not outcome.passed
        assert outcome.measured_value == pytest.approx(43.6, abs=1.0)


class TestTheCaseIsSolvedTheWayTheRestOfTheCodebaseSolves:
    def test_the_mesh_is_a_tet_mesh_the_ordinary_solver_accepts(self) -> None:
        mesh = fv52_mesh(4)

        assert isinstance(mesh, TetMesh)
        assert ModalEigenSolver().solve(mesh, fv52_case()) is not None

    def test_the_case_is_a_modal_case_and_carries_no_loads(self) -> None:
        case = fv52_case()

        assert isinstance(case, ModalCase)
        assert case.modes == 8
        assert all(isinstance(fixture, Fixture) for fixture in case.fixtures)


class TestTheLE10ModelIsTheOneNAFEMSPosed:
    """LE10 — a quarter of an elliptical annular plate, 600 mm thick.

    Every assertion here is about the *model*, and each one guards a way of
    getting a believable wrong number rather than an error: a plate built as one
    piece has nowhere to put the midplane support, a restraint band sized as a
    fraction of the bounding box holds a slab rather than a face, and a mesh
    coarse enough to chord the ellipse is solving a smaller part.
    """

    def test_the_plate_is_the_published_ellipse_pair_and_thickness(self) -> None:
        assert (nafems.ANNULUS_OUTER_A, nafems.ANNULUS_OUTER_B) == (3250.0, 2750.0)
        assert (nafems.ANNULUS_INNER_A, nafems.ANNULUS_INNER_B) == (2000.0, 1000.0)
        assert nafems.LE10_THICKNESS_MM == 600.0

    def test_point_d_is_on_the_inner_edge_of_the_pressed_surface(self) -> None:
        assert nafems.LE10_POINT_D == (2000.0, 0.0, 600.0)

    def test_the_exact_volume_is_the_two_ellipse_areas_and_not_a_recorded_number(
        self,
    ) -> None:
        """Computed from the semi-axes, so a dimension change moves the guard
        with the part instead of leaving it describing the old one."""
        expected = (
            math.pi / 4.0 * (3250.0 * 2750.0 - 2000.0 * 1000.0) * 600.0
        )
        assert nafems.LE10_VOLUME_MM3 == pytest.approx(expected)

    def test_the_case_holds_the_two_symmetry_planes_the_quarter_model_needs(
        self,
    ) -> None:
        case = nafems.le10_case()
        symmetry = [f for f in case.fixtures if f.kind == "symmetry"]

        assert sorted(f.normal for f in symmetry) == ["x", "y"]
        assert all(f.held == [f.normal] for f in symmetry)

    def test_the_symmetry_bands_are_tightened_off_the_default(self) -> None:
        """The default face tolerance is 1% of the bounding box — 27 mm on this
        plate — which holds a slab of material flat rather than a face."""
        case = nafems.le10_case()
        faces = [f.where for f in case.fixtures if isinstance(f.where, FaceSelector)]
        planes = [f for f in faces if f.axis in ("x", "y")]

        assert planes and all(f.tolerance <= 0.001 for f in planes)

    def test_the_outer_wall_is_simply_supported_and_not_clamped(self) -> None:
        """Held in x and y over its whole height, free in z except on one ring.
        A clamp here is a different structure and a much stiffer one."""
        case = nafems.le10_case()
        walls = [f for f in case.fixtures if isinstance(f.where, EllipticalWallSelector)]

        assert len(walls) == 2
        full, ring = sorted(walls, key=lambda f: f.where.length is not None)
        assert full.held == ["x", "y"]
        assert full.where.length is None
        assert ring.held == ["z"]
        assert ring.where.length is not None

    def test_the_only_restraint_on_z_is_the_midplane_ring(self) -> None:
        """What makes it simply supported. If anything else held z the plate
        would not bend, and the answer would still look like a stress."""
        case = nafems.le10_case()
        holding_z = [f for f in case.fixtures if "z" in f.held]

        assert len(holding_z) == 1
        selector = holding_z[0].where
        assert isinstance(selector, EllipticalWallSelector)
        assert selector.axis_point[2] == pytest.approx(300.0 - 1.0)

    def test_the_pressure_acts_on_the_upper_surface(self) -> None:
        case = nafems.le10_case()

        assert len(case.loads) == 1
        load = case.loads[0]
        assert load.pressure_mpa == pytest.approx(1.0)
        assert isinstance(load.where, FaceSelector)
        assert (load.where.axis, load.where.side) == ("z", "max")

    def test_the_target_is_signed_because_the_face_is_the_claim(self) -> None:
        target = BY_ID["nafems-le10"].benchmark.target

        assert target.value == pytest.approx(-5.38)
        assert target.unit == "MPa"

    def test_the_grid_sizes_are_spaced_rather_than_neighbouring(self) -> None:
        """gmsh builds a fresh unstructured mesh at each size rather than
        refining the last one, so consecutive levels are not nested and the
        quantity wanders for reasons that are not convergence. Sizes close
        together let that noise dominate and the study correctly refuses them.
        """
        sizes = nafems.LE10_ELEMENT_SIZES_MM

        assert len(sizes) >= 3
        ratios = [a / b for a, b in zip(sizes, sizes[1:], strict=False)]
        assert all(ratio >= 1.35 for ratio in ratios), ratios


class TestLE10ValidatesAgainstThePublishedReference:
    def test_it_lands_inside_the_published_band(self, le10_outcome) -> None:
        assert le10_outcome.outcome is Outcome.VALIDATED, le10_outcome.detail
        assert le10_outcome.relative_deviation is not None
        assert abs(le10_outcome.relative_deviation) < 0.02

    def test_the_answer_is_compressive_on_the_pressed_face(self, le10_outcome) -> None:
        """Sign, not magnitude. The same benchmark is quoted as +5.38 MPa by
        manuals reading the opposite face, and a part bent the wrong way would
        pass a comparison on magnitude alone."""
        assert le10_outcome.measured_value is not None
        assert le10_outcome.measured_value < 0.0

    def test_it_converged_over_three_grids_of_the_same_solid(self, le10_outcome) -> None:
        assert le10_outcome.convergence is not None
        assert le10_outcome.convergence["verdict"] == "converged"
        assert len(le10_outcome.convergence["levels"]) == 3

    def test_every_level_meshed_the_whole_plate(self, le10_outcome) -> None:
        """The guard that caught the 300 mm level, which enclosed 7% less
        material than the plate has: straight element edges had chorded the
        ellipse so coarsely that it was a smaller part, not a coarser mesh of the
        right one. Nothing else in a convergence study can tell those apart —
        both look like a number that moves when you refine.
        """
        assert le10_outcome.convergence is not None
        for level in le10_outcome.convergence["levels"]:
            assert level["volume_mm3"] == pytest.approx(
                nafems.LE10_VOLUME_MM3, rel=nafems.LE10_VOLUME_TOLERANCE
            )

    def test_a_level_that_did_not_mesh_the_whole_plate_is_refused_by_name(self) -> None:
        """Directly, so the refusal is pinned without paying for a coarse solve."""
        assert nafems.LE10_VOLUME_TOLERANCE < 0.07, (
            "the guard must be tighter than the 7% shortfall it was written for"
        )
        shortfall = abs(nafems.LE10_VOLUME_MM3 * 0.93 - nafems.LE10_VOLUME_MM3)
        assert shortfall / nafems.LE10_VOLUME_MM3 > nafems.LE10_VOLUME_TOLERANCE


class TestTheLE1ModelIsTheOneNAFEMSPosed:
    """LE1 — a quarter of an elliptical annular membrane, 100 mm thick.

    The same plan geometry as LE10 and a different idealisation of it, which is
    why the two are defined from one set of constants. What this class pins is
    the modelling, separately from the answer: a membrane loaded the wrong way
    round returns very nearly the right magnitude with the wrong sign, and a
    quarter model missing a symmetry restraint is a mechanism, not a membrane.
    """

    def test_it_shares_its_ellipses_with_le10_rather_than_repeating_them(self) -> None:
        """Two benchmarks, one shape, one set of numbers. A second copy of four
        semi-axes is a second chance to get one wrong, and the two cases would
        then disagree about a geometry they are supposed to share."""
        assert (nafems.ANNULUS_OUTER_A, nafems.ANNULUS_OUTER_B) == (3250.0, 2750.0)
        assert (nafems.ANNULUS_INNER_A, nafems.ANNULUS_INNER_B) == (2000.0, 1000.0)
        assert nafems.LE10_POINT_D[:2] == nafems.LE1_POINT_D[:2]

    def test_the_membrane_is_a_hundred_millimetres_thick(self) -> None:
        assert nafems.LE1_THICKNESS_MM == 100.0

    def test_point_d_is_on_the_inner_edge_in_the_plane_of_the_membrane(self) -> None:
        assert nafems.LE1_POINT_D == (2000.0, 0.0, 0.0)

    def test_the_exact_area_is_the_quarter_annulus_of_the_two_ellipses(self) -> None:
        expected = (
            math.pi
            / 4.0
            * (3250.0 * 2750.0 - 2000.0 * 1000.0)
        )
        assert nafems.ANNULUS_AREA_MM2 == pytest.approx(expected)

    def test_the_face_is_planar_and_has_exactly_that_area(self) -> None:
        """Built with OCCT rather than trusted: a boolean that took the wrong
        quadrant, or an ellipse given its axes the other way round, still
        produces a face and a plausible picture."""
        from app.kernel.occt.binding import symbol

        props = symbol("GProp_GProps")()
        symbol("BRepGProp").SurfaceProperties_s(nafems.le1_face(), props)
        assert props.Mass() == pytest.approx(nafems.ANNULUS_AREA_MM2, rel=1e-9)

    def test_the_case_is_posed_in_plane_stress_not_plane_strain(self) -> None:
        """The membrane is free to contract through its thickness. Plane strain
        would hold it, stiffen the response and change the answer at D."""
        assert nafems.le1_case().state is PlaneState.STRESS

    def test_the_case_holds_the_two_symmetry_lines_the_quarter_model_needs(self) -> None:
        case = nafems.le1_case()
        held = {
            (f.where.axis, tuple(f.dofs or ()))
            for f in case.fixtures
            if isinstance(f.where, FaceSelector)
        }
        assert held == {("x", ("x",)), ("y", ("y",))}

    def test_each_symmetry_line_holds_only_its_own_direction(self) -> None:
        """A symmetry plane restrains the normal component and nothing else.
        Holding both directions on either line clamps the quarter model into a
        corner and every stress in it is wrong."""
        for fixture in nafems.le1_case().fixtures:
            if isinstance(fixture.where, FaceSelector):
                assert fixture.dofs == [fixture.where.axis]

    def test_the_symmetry_bands_are_tighter_than_the_default(self) -> None:
        """The default is 1% of the bounding box, which on 3250 mm is a 32 mm
        band — a third of an element at the coarse level, so it would hold a
        strip of material rather than a line."""
        case = nafems.le1_case()
        faces = [f.where for f in case.fixtures if isinstance(f.where, FaceSelector)]
        assert faces and all(f.tolerance <= 0.001 for f in faces)

    def test_the_load_is_the_outer_ellipse_pulled_outward(self) -> None:
        case = nafems.le1_case()
        (load,) = case.loads
        assert isinstance(load.where, EllipticalWallSelector)
        assert (load.where.semi_axis_a, load.where.semi_axis_b) == (3250.0, 2750.0)

    def test_the_pressure_is_negative_because_the_benchmark_pulls(self) -> None:
        """`PressureLoad` is positive inward. The benchmark says a uniform
        *outward* pressure, so the sign is the model: loaded the other way the
        membrane is squeezed, the stress at D comes back at nearly the right
        magnitude with the wrong sign, and only a signed target catches it."""
        (load,) = nafems.le1_case().loads
        assert load.pressure_mpa == -nafems.LE1_PRESSURE_MPA
        assert nafems.LE1_PRESSURE_MPA == 10.0

    def test_the_loaded_edge_band_admits_the_midside_nodes(self) -> None:
        """gmsh puts corner nodes exactly on the ellipse and each midside node at
        the straight midpoint of its chord, which is inside it by the sagitta
        h^2/(8R). At the coarsest level that is about 2.2e-3 normalised, so a
        1e-3 band would drop every midside node on the loaded edge and apply the
        traction through the corners alone — which for a quadratic edge is not
        even statically equivalent."""
        sagitta = max(nafems.LE1_ELEMENT_SIZES_MM) ** 2 / (8.0 * nafems.ANNULUS_OUTER_B)
        assert nafems.LE1_EDGE_TOLERANCE > sagitta / nafems.ANNULUS_OUTER_B
        assert nafems.LE1_EDGE_TOLERANCE < 0.1

    def test_the_target_is_the_published_one_with_a_band_and_a_reason(self) -> None:
        target = BY_ID["nafems-le1"].benchmark.target
        assert target.basis is TargetBasis.PUBLISHED
        assert target.value == pytest.approx(92.7)
        assert target.tolerance == pytest.approx(0.02)
        assert target.tolerance_reason.strip()

    def test_the_grids_are_a_refinement_family_not_three_arbitrary_sizes(self) -> None:
        sizes = nafems.LE1_ELEMENT_SIZES_MM
        assert len(sizes) == 3
        assert list(sizes) == sorted(sizes, reverse=True)
        assert all(a / b > 1.25 for a, b in zip(sizes, sizes[1:], strict=False))


class TestLE1ValidatesAgainstThePublishedReference:
    def test_it_lands_inside_the_published_band(self, le1_outcome) -> None:
        assert le1_outcome.outcome is Outcome.VALIDATED, le1_outcome.detail
        assert le1_outcome.relative_deviation is not None
        assert abs(le1_outcome.relative_deviation) < 0.02

    def test_the_answer_is_tensile_because_the_membrane_is_stretched(self) -> None:
        """Not a magnitude comparison. Reversing the traction gives a number of
        almost the same size, and the sign is the only thing that separates the
        benchmark from its mirror image."""
        assert BY_ID["nafems-le1"].benchmark.target.value > 0.0

    def test_the_measured_value_is_tensile_too(self, le1_outcome) -> None:
        assert le1_outcome.measured_value is not None
        assert le1_outcome.measured_value > 0.0

    def test_it_converged_over_three_grids_of_the_same_membrane(self, le1_outcome) -> None:
        assert le1_outcome.convergence is not None
        assert le1_outcome.convergence["verdict"] == "converged"
        assert len(le1_outcome.convergence["levels"]) == 3

    def test_every_level_meshed_the_whole_membrane(self, le1_outcome) -> None:
        """The same guard LE10 needed, kept even though it has never bound here:
        a plane mesh puts its boundary nodes exactly on the curve and only
        chords between them, so the coarsest level is 0.09% short where LE10's
        coarsest solid was 7%. 'It did not bind this time' is not a reason to
        remove a check."""
        assert le1_outcome.convergence is not None
        for level in le1_outcome.convergence["levels"]:
            assert level["area_mm2"] == pytest.approx(
                nafems.ANNULUS_AREA_MM2, rel=nafems.LE1_AREA_TOLERANCE
            )

    def test_the_study_reports_that_it_is_not_in_the_asymptotic_range(
        self, le1_outcome
    ) -> None:
        """The observed order comes out near 4.7 against a formal order of 2 for
        a quadratic triangle, which is the standard signal that a sequence is
        not yet asymptotic and that the GCI understates the error. It does. That
        has to reach the reader rather than be smoothed away, so the study is
        given the formal order and publishes the discrepancy as a caution.
        """
        assert le1_outcome.convergence is not None
        cautions = " ".join(le1_outcome.convergence["cautions"])
        assert "formal order" in cautions

    def test_the_plane_model_is_far_cheaper_than_the_solid_it_idealises(
        self, le1_outcome
    ) -> None:
        """The engineering reason a plane element family is worth having at all,
        stated as a fact about this run rather than as a claim in a docstring:
        the same curved boundary and the same class of answer on a fraction of
        the degrees of freedom."""
        assert le1_outcome.convergence is not None
        finest = le1_outcome.convergence["levels"][0]
        assert finest["node_count"] < 20_000
        assert le1_outcome.seconds < 30.0


class TestLE11RunsAndHonestlyDoesNotValidate:
    """The case that is worth having *because* it does not pass.

    LE11's geometry was blocked on a document nobody had and its load was
    blocked on a per-node temperature field; both were built. It now runs, and
    every level lands within 2% of the published −105 MPa — and the convergence
    study still refuses to state a value, because point A is a corner where the
    inner sphere meets the base plane and the answer scatters with where nodes
    happen to fall.

    That refusal is the outcome, not a failure to reach one. `UNCONVERGED` is
    never a pass, and a number picked out of the scatter would be a worse
    result than no number — which is the whole of Decision 3. What the case
    reports is a capability finding: a tetrahedral mesh cannot state this corner
    stress to better than its own noise, and the published solutions that do use
    curved-hex or p-version elements.
    """

    def test_it_runs_rather_than_reporting_a_blocker(self) -> None:
        case = BY_ID["nafems-le11"]

        assert case.benchmark.runnable
        assert case.blocker is None

    def test_the_outcome_is_unconverged_and_therefore_not_a_pass(
        self, le11_outcome
    ) -> None:
        assert le11_outcome.outcome is Outcome.UNCONVERGED
        assert le11_outcome.outcome is not Outcome.VALIDATED
        assert le11_outcome.measured_value is None

    def test_the_study_says_why_in_words(self, le11_outcome) -> None:
        assert le11_outcome.convergence is not None
        assert le11_outcome.convergence["verdict"] != "converged"
        assert le11_outcome.convergence["reason"].strip()

    def test_every_level_still_landed_near_the_published_value(
        self, le11_outcome
    ) -> None:
        """The distinction the outcome turns on. The answer is *not* wrong — each
        grid is inside the benchmark's own 2% band. What is missing is evidence
        that refining would not move it, and that is a different claim."""
        assert le11_outcome.convergence is not None
        for level in le11_outcome.convergence["levels"]:
            assert abs(level["value"] + 105.0) / 105.0 < 0.02

    def test_the_temperature_field_is_scaled_out_of_metres(self) -> None:
        """The trap the sourcing record puts in capitals. Every source publishes
        the field with coordinates in metres and this codebase is mm-N-MPa, so
        the same physical field is that expression over a thousand. Unscaled it
        gives about −105,000 MPa at A with nothing raising anywhere."""
        from app.mesh.primitives import box_mesh

        mesh = box_mesh((1000.0, 1000.0, 1000.0), (1, 1, 1))
        field = nafems.le11_temperatures(mesh)

        assert nafems.LE11_FIELD_SCALE == 1000.0
        assert float(np.max(field)) < 10.0

    def test_the_case_holds_both_annuli_and_both_symmetry_planes(self) -> None:
        """Four restraints and no mechanical load. Holding only the base would
        let the cylinder grow freely upward and change the answer entirely."""
        case = nafems.le11_case()
        held_z = [f for f in case.fixtures if f.dofs == ["z"]]

        assert len(case.fixtures) == 4
        assert len(held_z) == 2
        assert all(load.force_n == (0.0, 0.0, 0.0) for load in case.loads)
