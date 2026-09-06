"""A clash check over a whole assembly, and an honest account of what it looked at.

Two things are being tested and they pull in opposite directions.

**The broad phase must never drop a pair that would have clashed.** It is allowed to be
loose — a conservative world box and a lower-bound separation — and it is not allowed to
be wrong. `TestTheBroadPhaseIsSound` checks the containment property against OCCT's own
bounding box on the transformed shape, which is the only comparison that can catch a
`Box.transformed` that has quietly become tight.

**Anything that was not checked is counted and named.** A clash check that skips pairs
silently is not a weaker check, it is one that passes everything — the same failure
`tests/test_dynamics_clearance.py` records at pose level, where twelve poses nobody
looked at were published as measured. So `TestNothingIsSkippedSilently` exhausts the
budget, breaks the measurer, and removes a bounding box, and asserts each time that the
report says so *and* that the headline `minimum_clearance_mm` is withheld — because a
minimum over a subset over-estimates clearance, which is the direction that makes a
machine look safer than it is.

The narrow phase is exercised against **real OCCT geometry built through `OcctRunner`**
in `TestAgainstRealGeometry`, where the answers (5 mm apart, 2000 mm3 of overlap) are
known by hand before the kernel is asked. Everything else uses an analytic box measurer
so the walk itself is testable with no kernel installed.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.assembly.clash import (
    ClashReport,
    combine,
    find_clashes,
    occt_bounds,
    occt_measurer,
    same_parent,
    touching_components,
)
from app.assembly.placement import Box, at, turned
from app.assembly.structure import Component, Instance, ProductStructure, spread
from app.design.assertions import Assertion, Outcome, check_assertions
from app.kernel.occt.binding import available

# -- an analytic stand-in whose answers are known in closed form ---------------


def _overlap_mm3(a: Box, b: Box) -> float:
    spans = [
        max(min(a.max_mm[i], b.max_mm[i]) - max(a.min_mm[i], b.min_mm[i]), 0.0)
        for i in range(3)
    ]
    return spans[0] * spans[1] * spans[2]


def _box_measurer(boxes: dict[str, Box], calls: list[tuple[str, str]] | None = None):
    """Exact for axis-aligned boxes under translation — which is what the fixtures use."""

    def measure(left, right):
        if calls is not None:
            calls.append((left.path, right.path))
        world_a = boxes[left.component].transformed(left.frame)
        world_b = boxes[right.component].transformed(right.frame)
        return SimpleNamespace(
            distance_mm=world_a.separation_mm(world_b),
            interference_mm3=_overlap_mm3(world_a, world_b),
            failure="",
        )

    return measure


def _unit_box(size: float = 10.0) -> Box:
    return Box(min_mm=(0.0, 0.0, 0.0), max_mm=(size, size, size))


def _row(count: int, pitch: float = 30.0) -> ProductStructure:
    """`count` 10 mm cubes in a row at `pitch` apart. Nothing touches when pitch > 10."""
    block = Component(name="block")
    rail = Component(
        name="rail",
        instances=spread("block", "block", [at(pitch * n, 0.0, 0.0) for n in range(count)]),
    )
    return ProductStructure(root="rail", components=[rail, block])


def _bounds(boxes: dict[str, Box], calls: list[str] | None = None):
    def provider(component: str) -> Box:
        if calls is not None:
            calls.append(component)
        return boxes[component]

    return provider


# -- the broad phase ----------------------------------------------------------


class TestTheBroadPhaseIsSound:
    def test_a_rotated_world_box_contains_the_rotated_shape(self) -> None:
        """The containment property the whole broad phase rests on.

        A 45-degree rotation is the worst case for an axis-aligned box, and it is the
        case a "tighter" implementation would get wrong. Checked against a box built by
        hand rather than against OCCT so it runs with no kernel; the OCCT half is
        `test_the_world_box_contains_occts_own_box_for_the_moved_shape`.
        """
        local = Box(min_mm=(-10.0, -10.0, 0.0), max_mm=(10.0, 10.0, 5.0))
        moved = local.transformed(turned((0.0, 0.0, 1.0), math.pi / 4))

        # Every corner of the rotated box is inside the re-boxed result.
        for corner in local.corners():
            placed = turned((0.0, 0.0, 1.0), math.pi / 4).point(corner)
            assert moved.contains(placed, tolerance_mm=1e-9)
        # And it is genuinely larger: sqrt(2) x 20 across the diagonal.
        assert moved.size_mm[0] == pytest.approx(20.0 * math.sqrt(2.0), abs=1e-9)

    def test_box_separation_never_exceeds_the_true_distance(self) -> None:
        """A lower bound, which is the direction that makes rejection safe."""
        a = Box(min_mm=(0.0, 0.0, 0.0), max_mm=(10.0, 10.0, 10.0))
        b = Box(min_mm=(30.0, 0.0, 0.0), max_mm=(40.0, 10.0, 10.0))
        assert a.separation_mm(b) == pytest.approx(20.0)

        diagonal = Box(min_mm=(30.0, 30.0, 0.0), max_mm=(40.0, 40.0, 10.0))
        assert a.separation_mm(diagonal) == pytest.approx(20.0 * math.sqrt(2.0))

        overlapping = Box(min_mm=(5.0, 5.0, 5.0), max_mm=(15.0, 15.0, 15.0))
        assert a.separation_mm(overlapping) == 0.0

    def test_a_box_built_upside_down_is_refused(self) -> None:
        """The guard, broken: an inverted box reports every pair as separated.

        That is a clash check that passes everything, and it looks exactly like a clean
        run — which is why it is refused at construction rather than diagnosed later.
        """
        with pytest.raises(ValueError, match="passes everything"):
            Box(min_mm=(10.0, 0.0, 0.0), max_mm=(0.0, 10.0, 10.0))

    def test_a_clashing_pair_is_never_rejected_by_bounds(self) -> None:
        boxes = {"block": _unit_box()}
        overlapping = ProductStructure(
            root="rail",
            components=[
                Component(
                    name="rail",
                    instances=spread("block", "block", [at(0.0), at(5.0), at(100.0)]),
                ),
                Component(name="block"),
            ],
        )
        report = find_clashes(overlapping, _box_measurer(boxes), _bounds(boxes))

        assert report.pairs_total == 3
        assert len(report.clashes) == 1
        assert report.clashes[0].interference_mm3 == pytest.approx(500.0)
        # The far pair was rejected; the clashing one was measured.
        assert len(report.rejected_by_bounds) == 2
        assert report.narrow_checked == 1
        assert report.complete

    def test_a_clearance_threshold_widens_what_is_measured(self) -> None:
        boxes = {"block": _unit_box()}
        structure = _row(2, pitch=25.0)  # 15 mm gap between the cubes

        tight = find_clashes(structure, _box_measurer(boxes), _bounds(boxes))
        assert tight.narrow_checked == 0
        assert tight.minimum_clearance_mm is None

        wide = find_clashes(
            structure, _box_measurer(boxes), _bounds(boxes), clearance_mm=20.0
        )
        assert wide.narrow_checked == 1
        assert wide.minimum_clearance_mm == pytest.approx(15.0)

    def test_each_components_box_is_asked_for_once(self) -> None:
        """The graph earning its keep: 40 occurrences, one bounding-box measurement."""
        asked: list[str] = []
        boxes = {"block": _unit_box()}
        find_clashes(_row(40), _box_measurer(boxes), _bounds(boxes, asked))
        assert asked == ["block"]


# -- honesty about what was checked -------------------------------------------


class TestNothingIsSkippedSilently:
    def test_every_pair_is_accounted_for(self) -> None:
        boxes = {"block": _unit_box()}
        report = find_clashes(_row(6), _box_measurer(boxes), _bounds(boxes))

        assert report.pairs_total == 15  # 6 * 5 / 2
        assert (
            report.narrow_checked
            + len(report.rejected_by_bounds)
            + len(report.excluded)
            + len(report.unchecked)
            == report.pairs_total
        )

    def test_a_budget_that_runs_out_says_so_and_withholds_the_minimum(self) -> None:
        """The guard this package exists for, exercised.

        Ten cubes at 5 mm pitch all overlap, so the broad phase rejects nothing and the
        budget bites. The partial minimum is real and is published under a different
        name; `minimum_clearance_mm` is withheld with a reason.
        """
        boxes = {"block": _unit_box()}
        crowded = ProductStructure(
            root="rail",
            components=[
                Component(
                    name="rail",
                    instances=spread("block", "block", [at(5.0 * n) for n in range(10)]),
                ),
                Component(name="block"),
            ],
        )
        report = find_clashes(crowded, _box_measurer(boxes), _bounds(boxes), budget=5)

        assert report.narrow_checked == 5
        assert len(report.unchecked) == report.pairs_total - 5 - len(report.rejected_by_bounds)
        assert not report.complete
        assert "budget of 5" in report.unchecked[0].reason
        assert "NEVER LOOKED AT" in report.summary()

        payload = report.to_payload()
        assert "minimum_clearance_mm" not in payload
        assert "checked_minimum_clearance_mm" in payload
        assert payload["unchecked_pair_count"] > 0

    def test_an_incomplete_check_makes_the_assertion_unmeasured_not_passed(self) -> None:
        """The guard, broken.

        `checked_minimum_clearance_mm` is 0.0 here, so an assertion written against the
        partial number *fails*; but publish a partial minimum from a run whose remaining
        pairs were the tight ones and it passes on pairs nobody looked at. The second
        half of this test shows exactly that: the same claim against the partial path
        comes back PASSED, which is the false green withholding the headline prevents.
        """
        boxes = {"block": _unit_box()}
        spread_out = ProductStructure(
            root="rail",
            components=[
                Component(
                    name="rail",
                    instances=spread("block", "block", [at(12.0 * n) for n in range(6)]),
                ),
                Component(name="block"),
            ],
        )
        report = find_clashes(
            spread_out, _box_measurer(boxes), _bounds(boxes), clearance_mm=50.0, budget=2
        )
        assert not report.complete
        payload = report.to_payload()

        claim = Assertion(
            name="two_millimetres_of_room",
            measure="minimum_clearance_mm",
            comparison=">=",
            bound=2.0,
        )
        (result,) = check_assertions([claim], payload)
        assert result.outcome is Outcome.UNMEASURED
        assert "never looked at" in result.reason

        # And what publishing it anyway would have produced:
        naive = Assertion(
            name="two_millimetres_of_room",
            measure="checked_minimum_clearance_mm",
            comparison=">=",
            bound=2.0,
        )
        (false_green,) = check_assertions([naive], payload)
        assert false_green.outcome is Outcome.PASSED

    def test_a_measurer_that_raises_lands_in_unchecked_with_its_text(self) -> None:
        boxes = {"block": _unit_box()}

        def broken(left, right):
            raise RuntimeError("BRepExtrema ran out of memory")

        report = find_clashes(_row(3, pitch=5.0), broken, _bounds(boxes))

        assert report.findings == ()
        assert len(report.unchecked) == 3
        assert "BRepExtrema ran out of memory" in report.unchecked[0].reason
        assert not report.complete
        assert "minimum_clearance_mm" not in report.to_payload()
        assert report.to_payload()["unchecked_pair_count"] == 3

    def test_a_component_with_no_bounding_box_is_named_not_dropped(self) -> None:
        boxes = {"block": _unit_box()}
        structure = ProductStructure(
            root="rail",
            components=[
                Component(
                    name="rail",
                    instances=(
                        Instance(component="block", tag="block", index=1, placement=at(0.0)),
                        Instance(component="ghost", tag="ghost", index=1, placement=at(5.0)),
                    ),
                ),
                Component(name="block"),
                Component(name="ghost"),
            ],
        )
        report = find_clashes(structure, _box_measurer(boxes), _bounds(boxes))

        assert len(report.unchecked) == 1
        assert "no bounding box for ghost" in report.unchecked[0].reason
        assert "KeyError" in report.unchecked[0].reason
        assert not report.complete

    def test_a_measurer_that_reports_a_failure_produces_a_finding_with_no_distance(
        self,
    ) -> None:
        def refuses(left, right):
            return SimpleNamespace(
                distance_mm=None,
                interference_mm3=0.0,
                failure="the minimum-distance search returned no solution",
            )

        boxes = {"block": _unit_box()}
        report = find_clashes(_row(2, pitch=5.0), refuses, _bounds(boxes))

        assert len(report.findings) == 1
        assert report.findings[0].distance_mm is None
        assert "no solution" in str(report.findings[0])
        # The pair *was* looked at, so the check is complete; the answer is missing and
        # the minimum is unavailable rather than wrong.
        assert report.complete
        assert report.minimum_clearance_mm is None
        assert "minimum_clearance_mm" not in report.to_payload()

    def test_an_empty_report_is_complete_only_when_something_was_measured(self) -> None:
        empty = ClashReport()
        assert empty.complete  # nothing skipped
        assert empty.minimum_clearance_mm is None
        assert "occurrence_count" in empty.to_payload()


class TestExclusionsAreNamed:
    def test_an_excluded_pair_carries_the_rule_s_reason(self) -> None:
        boxes = {"pin": _unit_box(), "link": _unit_box()}
        structure = ProductStructure(
            root="joint",
            components=[
                Component(
                    name="joint",
                    instances=(
                        Instance(component="pin", tag="pin", index=1, placement=at(0.0)),
                        Instance(component="link", tag="link", index=1, placement=at(2.0)),
                    ),
                ),
                Component(name="pin"),
                Component(name="link"),
            ],
        )
        rule = touching_components(
            [("pin", "link")], reason="a pin is inside its link by design"
        )
        report = find_clashes(structure, _box_measurer(boxes), _bounds(boxes), ignore=rule)

        assert report.clashes == ()
        assert len(report.excluded) == 1
        assert "by design" in report.excluded[0].reason
        # An exclusion the author chose does not make the check incomplete — it is in
        # the record, which is the difference from a pair nobody looked at.
        assert report.complete
        assert report.to_payload()["excluded_pair_count"] == 1

    def test_the_exclusion_is_unordered(self) -> None:
        boxes = {"pin": _unit_box(), "link": _unit_box()}
        structure = ProductStructure(
            root="joint",
            components=[
                Component(
                    name="joint",
                    instances=(
                        Instance(component="link", tag="link", index=1, placement=at(0.0)),
                        Instance(component="pin", tag="pin", index=1, placement=at(2.0)),
                    ),
                ),
                Component(name="pin"),
                Component(name="link"),
            ],
        )
        report = find_clashes(
            structure,
            _box_measurer(boxes),
            _bounds(boxes),
            ignore=touching_components([("pin", "link")]),
        )
        assert len(report.excluded) == 1

    def test_rules_combine_first_match_wins(self) -> None:
        boxes = {"block": _unit_box()}
        rule = combine(
            touching_components([("block", "block")], reason="stacked by design"),
            same_parent(),
        )
        report = find_clashes(
            _row(3, pitch=5.0), _box_measurer(boxes), _bounds(boxes), ignore=rule
        )
        assert len(report.excluded) == 3
        assert all("stacked by design" in s.reason for s in report.excluded)


# -- real geometry ------------------------------------------------------------


pytestmark_occt = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)


def _cube_document(size: float = 20.0):
    """A `size` cube built through `OcctRunner`, centred in XY and rising from z=0."""
    from app.kernel import OcctRunner

    runner = OcctRunner()
    runner("catia_new_part", {"name": "Cube"})
    runner("catia_sketch_create", {"support": "XY", "name": "outline"})
    runner(
        "catia_sketch_rectangle",
        {"sketch": "outline", "width_mm": size, "height_mm": size},
    )
    runner("catia_pad", {"sketch": "outline", "length_mm": size})
    return runner.document


@pytestmark_occt
class TestAgainstRealGeometry:
    def test_two_cubes_five_millimetres_apart_measure_five_millimetres(self) -> None:
        """The answer is known before the kernel is asked: 25 mm pitch, 20 mm cubes."""
        shape = _cube_document().shape
        shapes = {"cube": shape}
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=spread("cube", "cube", [at(0.0), at(25.0)]),
                ),
                Component(name="cube"),
            ],
        )
        report = find_clashes(
            structure,
            occt_measurer(shapes),
            occt_bounds(shapes),
            clearance_mm=10.0,
        )

        assert report.complete
        assert report.narrow_checked == 1
        assert report.minimum_clearance_mm == pytest.approx(5.0, abs=1e-6)
        assert report.clashes == ()

    def test_two_overlapping_cubes_report_the_exact_overlap_volume(self) -> None:
        """15 mm pitch on a 20 mm cube leaves a 5 x 20 x 20 = 2000 mm3 intersection."""
        shapes = {"cube": _cube_document().shape}
        structure = ProductStructure(
            root="pair",
            components=[
                Component(name="pair", instances=spread("cube", "cube", [at(0.0), at(15.0)])),
                Component(name="cube"),
            ],
        )
        report = find_clashes(structure, occt_measurer(shapes), occt_bounds(shapes))

        assert len(report.clashes) == 1
        assert report.clashes[0].interference_mm3 == pytest.approx(2000.0, rel=1e-9)
        assert report.to_payload()["interferes"] is True

    def test_the_world_box_contains_occts_own_box_for_the_moved_shape(self) -> None:
        """`Box.transformed` must contain the real transformed shape, rotation included.

        The one comparison that catches a `transformed` that has become tight: OCCT
        transforms the shape and measures it, and our re-boxed corners must still
        enclose that. A tight box would fail here and nowhere else — a broad phase that
        drops a pair produces a clean report.
        """
        from app.kernel.occt.binding import symbol
        from app.kernel.occt.metrology import bounding_box_mm

        shape = _cube_document().shape
        local = Box.from_payload(bounding_box_mm(shape))
        frame = turned((0.0, 0.0, 1.0), math.pi / 4)
        ours = local.transformed(frame)

        rotation, origin = frame.rotation, frame.origin_mm
        transform = symbol("gp_Trsf")()
        transform.SetValues(
            rotation[0], rotation[1], rotation[2], origin[0],
            rotation[3], rotation[4], rotation[5], origin[1],
            rotation[6], rotation[7], rotation[8], origin[2],
        )
        moved = symbol("BRepBuilderAPI_Transform")(shape, transform, True).Shape()
        theirs = Box.from_payload(bounding_box_mm(moved))

        for axis in range(3):
            assert ours.min_mm[axis] <= theirs.min_mm[axis] + 1e-6
            assert ours.max_mm[axis] >= theirs.max_mm[axis] - 1e-6

    def test_a_rotated_occurrence_is_measured_where_it_actually_sits(self) -> None:
        """A cube turned 90 degrees about Z and moved is still 5 mm from its neighbour.

        The rotation is a quarter turn about the part's own axis, which for a square
        prism changes nothing geometrically — so the distance must be identical to the
        unrotated case. A transform applied in the wrong order (translate then rotate)
        would swing the cube 25 mm away and the number would change.
        """
        shapes = {"cube": _cube_document().shape}
        from app.assembly.placement import compose

        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="cube", tag="cube", index=1, placement=at(0.0)),
                        Instance(
                            component="cube",
                            tag="cube",
                            index=2,
                            placement=compose(
                                at(25.0), turned((0.0, 0.0, 1.0), math.pi / 2)
                            ),
                        ),
                    ),
                ),
                Component(name="cube"),
            ],
        )
        report = find_clashes(
            structure, occt_measurer(shapes), occt_bounds(shapes), clearance_mm=10.0
        )
        assert report.minimum_clearance_mm == pytest.approx(5.0, abs=1e-6)

    def test_a_component_with_no_shape_is_reported_rather_than_assumed_clear(self) -> None:
        shapes = {"cube": _cube_document().shape}
        structure = ProductStructure(
            root="pair",
            components=[
                Component(
                    name="pair",
                    instances=(
                        Instance(component="cube", tag="cube", index=1),
                        Instance(component="missing", tag="missing", index=1, placement=at(5.0)),
                    ),
                ),
                Component(name="cube"),
                Component(name="missing"),
            ],
        )
        report = find_clashes(structure, occt_measurer(shapes), occt_bounds(shapes))

        assert not report.complete
        assert "no shape was supplied" in report.unchecked[0].reason
