"""Entering a public benchmark, and the arithmetic of its score (E23 task 4).

The IoU is checked against closed-form overlaps rather than against recorded
output, which is this repository's standing rule for anything that computes a
number. Two 10 mm cubes offset by 5 mm overlap on 500 mm³ of a 1,500 mm³ union,
so the answer is exactly 1/3 and nothing about the implementation gets a vote.

These open no database and need no model, which is the point: the scoring
arithmetic has to be trustworthy before a model is attached to it.

Written on Linux and **not run** (the user's rule; Windows runs them — THE QUEUE
D7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.kernel.occt.binding import symbol
from app.verify.benchcad import (
    NOT_SUBMITTED,
    SCORING_RULE_CAVEAT,
    SOURCE,
    Attempt,
    Case,
    DatasetMissing,
    run,
    score,
    voxel_iou,
)


def cube(x: float = 0.0, size: float = 10.0):
    box = symbol("BRepPrimAPI_MakeBox")
    point = symbol("gp_Pnt")
    return box(point(x, 0.0, 0.0), size, size, size).Shape()


class TestTheGeometryComparison:
    def test_a_part_against_itself_is_one(self) -> None:
        """Not approximately: `ON` counts as inside precisely so a grid aligned
        with a machined part's faces does not score it against itself at 0.97."""
        assert voxel_iou(cube(), cube(), pitch_mm=2.0) == 1.0

    def test_a_half_overlap_is_one_third(self) -> None:
        """500 mm³ of intersection over 1,500 mm³ of union, closed form."""
        assert voxel_iou(cube(), cube(5.0), pitch_mm=1.0) == pytest.approx(1 / 3, abs=1e-3)

    def test_disjoint_parts_score_zero(self) -> None:
        assert voxel_iou(cube(), cube(50.0), pitch_mm=2.0) == 0.0

    def test_both_parts_are_sampled_on_one_grid(self) -> None:
        """The correctness argument: a per-solid grid would put two parts in
        different coordinate systems and score two shapes that never touch."""
        far = voxel_iou(cube(), cube(50.0), pitch_mm=2.0)
        near = voxel_iou(cube(), cube(5.0), pitch_mm=2.0)

        assert far == 0.0
        assert near > 0.0

    def test_a_pitch_of_zero_is_refused_by_name(self) -> None:
        with pytest.raises(ValueError, match="edge of a sampling cube"):
            voxel_iou(cube(), cube(), pitch_mm=0.0)


class TestTheScore:
    def test_the_two_readings_of_the_published_rule_agree(self) -> None:
        """`mean(IoU | executed) x execution rate` equals `sum(IoU) / total`, so
        the ambiguity in the benchmark's sentence does not reach the number."""
        result = score([Attempt("a", True, 0.8), Attempt("b", False), Attempt("c", True, 0.4)])

        assert result.iou_score == pytest.approx(0.4)
        assert result.mean_iou_where_executed == pytest.approx(0.6)
        assert result.execution_rate == pytest.approx(2 / 3)
        assert result.iou_score == pytest.approx(
            result.mean_iou_where_executed * result.execution_rate
        )

    def test_a_failure_to_build_counts_against_the_score(self) -> None:
        """The execution half of an execution-verified benchmark. A harness that
        scored only what built would report a model that gives up as perfect."""
        built = score([Attempt("a", True, 1.0)])
        gave_up = score([Attempt("a", True, 1.0), Attempt("b", False)])

        assert built.iou_score == 1.0
        assert gave_up.iou_score == 0.5

    def test_no_cases_is_no_score(self) -> None:
        empty = score([])

        assert empty.iou_score is None
        assert empty.execution_rate is None

    def test_an_executed_case_must_carry_an_iou(self) -> None:
        """An executed case nobody compared is not a zero."""
        with pytest.raises(ValueError, match="not a zero"):
            Attempt("a", executed=True)

    def test_a_failed_case_must_not_carry_one(self) -> None:
        with pytest.raises(ValueError, match="did not execute"):
            Attempt("a", executed=False, iou=0.5)


class TestWhatIsClaimed:
    def test_the_source_names_where_and_when_it_was_read(self) -> None:
        assert SOURCE.paper_url.startswith("https://")
        assert SOURCE.site_url.startswith("https://")
        assert SOURCE.read_on == "2026-09-15"

    def test_both_licences_are_recorded(self) -> None:
        """The phase's argument is about what a free stack may publish, so the
        terms it is published under are part of the claim."""
        assert SOURCE.code_licence == "MIT"
        assert SOURCE.data_licence == "CC-BY-4.0"

    def test_the_report_says_it_was_not_submitted(self) -> None:
        """A local number is not a leaderboard entry, and a score published
        without that sentence reads as though it were."""
        assert score([Attempt("a", True, 1.0)]).to_dict()["not_submitted"] == NOT_SUBMITTED

    def test_the_report_carries_what_the_pages_did_not_settle(self) -> None:
        assert score([]).to_dict()["scoring_rule_caveat"] == SCORING_RULE_CAVEAT


class TestRunningIt:
    class _Silent:
        def build(self, case: Case) -> None:
            return None

    class _Broken:
        def build(self, case: Case) -> None:
            raise RuntimeError("the kernel refused it")

    def test_no_dataset_is_refused_rather_than_scored_zero(self) -> None:
        """"0 cases, score 0.0" is the shape somebody screenshots."""
        with pytest.raises(DatasetMissing, match="nothing to score"):
            run([], self._Silent())

    def test_a_pipeline_that_builds_nothing_scores_zero_without_raising(self) -> None:
        cases = [Case(id="c1", family="spring", reference_path=Path("/nonexistent.step"))]

        result = run(cases, self._Silent())

        assert result.executed == 0
        assert result.iou_score == 0.0

    def test_a_pipeline_that_raises_is_a_benchmark_outcome_not_a_crash(self) -> None:
        cases = [Case(id="c1", family="gear", reference_path=Path("/nonexistent.step"))]

        result = run(cases, self._Broken())

        assert result.executed == 0
        assert result.cases == 1
