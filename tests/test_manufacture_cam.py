"""Drop-cutter, waterline and the machining plan (E17.4).

Contacts are checked against closed forms worked beside each assertion, and against a
brute-force oracle: sample every triangle densely and apply only the vertex test. The oracle's
points are real surface points, so the exact answer may never sit below it (that would be a
gouge), and it may sit above only by the sampling's reach.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction that the
Windows machine runs the tests.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.manufacture.cam import StockBlock, plan, raster
from app.manufacture.dropcutter import CamError, Cutter, CutterShape, drop, waterline
from app.rules.gdt import Datum, DatumScheme
from app.rules.processes import Limit

FIXTURE = "test fixture, not a shop's figure"
FLAT = Cutter(CutterShape.FLAT, diameter_mm=10.0, length_mm=30.0)
BALL = Cutter(CutterShape.BALL, diameter_mm=10.0, length_mm=30.0)


def _box(a: float, b: float, h: float) -> np.ndarray:
    v = [
        (0, 0, 0), (a, 0, 0), (a, b, 0), (0, b, 0),
        (0, 0, h), (a, 0, h), (a, b, h), (0, b, h),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),  # bottom
        (4, 5, 6), (4, 6, 7),  # top
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7),
    ]
    return np.array([[v[i] for i in face] for face in faces], dtype=float)


PLATEAU = _box(100.0, 100.0, 10.0)


class TestDropCutterClosedForms:
    def test_both_cutters_sit_on_a_flat_top(self) -> None:
        assert drop(PLATEAU, FLAT, 50.0, 50.0, floor_z_mm=-1.0) == pytest.approx(10.0)
        assert drop(PLATEAU, BALL, 50.0, 50.0, floor_z_mm=-1.0) == pytest.approx(10.0)

    def test_a_flat_cutter_overhanging_an_edge_still_sits_on_it(self) -> None:
        # axis 3 mm outside the edge, radius 5: the rim still reaches the top.
        assert drop(PLATEAU, FLAT, -3.0, 50.0, floor_z_mm=-1.0) == pytest.approx(10.0)

    def test_beyond_its_radius_it_falls_to_the_floor(self) -> None:
        assert drop(PLATEAU, FLAT, -6.0, 50.0, floor_z_mm=-1.0) == pytest.approx(-1.0)

    def test_a_ball_rolls_over_an_edge(self) -> None:
        # centre 3 mm outside the edge: 10 + sqrt(25 - 9) - 5 = 9.
        assert drop(PLATEAU, BALL, -3.0, 50.0, floor_z_mm=-1.0) == pytest.approx(9.0)

    def test_on_a_slope_a_flat_cutter_touches_uphill_at_its_rim(self) -> None:
        slope = np.array([[(-1000, -1000, -100), (1000, -1000, 100), (0, 1000, 0)]], dtype=float)
        # z = 0.1 x everywhere on this plane; the rim reaches x = 55.
        assert drop(slope, FLAT, 50.0, 0.0, floor_z_mm=-1e6) == pytest.approx(5.5)

    def test_on_a_slope_a_ball_sits_one_radius_along_the_normal(self) -> None:
        slope = np.array([[(-1000, -1000, -100), (1000, -1000, 100), (0, 1000, 0)]], dtype=float)
        # centre = plane(q) + r / n_z with n_z = 1/sqrt(1.01); tip = centre - r.
        expected = 5.0 + 5.0 * (math.sqrt(1.01) - 1.0)
        assert drop(slope, BALL, 50.0, 0.0, floor_z_mm=-1e6) == pytest.approx(expected)


def _brute(tri: np.ndarray, cutter: Cutter, x: float, y: float, n: int = 300) -> float:
    i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
    keep = i + j <= n
    u, v = i[keep] / n, j[keep] / n
    pts = tri[0] + u[:, None] * (tri[1] - tri[0]) + v[:, None] * (tri[2] - tri[0])
    d2 = (pts[:, 0] - x) ** 2 + (pts[:, 1] - y) ** 2
    r = cutter.radius_mm
    inside = d2 <= r * r
    if not inside.any():
        return -math.inf
    if cutter.shape is CutterShape.FLAT:
        return float(pts[inside, 2].max())
    return float((pts[inside, 2] + np.sqrt(r * r - d2[inside])).max() - r)


class TestDropCutterNeverGouges:
    @pytest.mark.parametrize("cutter", [FLAT, BALL], ids=["flat", "ball"])
    def test_the_exact_drop_is_never_below_a_sampled_surface_point(self, cutter: Cutter) -> None:
        rng = np.random.default_rng(20260915)
        for _ in range(40):
            tri = np.column_stack(
                [rng.uniform(0, 30, 3), rng.uniform(0, 30, 3), rng.uniform(0, 15, 3)]
            )
            x, y = rng.uniform(-5, 35, 2)
            exact = drop(tri[None], cutter, float(x), float(y), floor_z_mm=-math.inf)
            brute = _brute(tri, cutter, float(x), float(y))
            if brute == -math.inf:
                continue
            # Never below a real surface point: that is a gouge, and it is exact.
            assert exact >= brute - 1e-9
            # Above it only by what the sampling can miss, on a triangle that is not a
            # sliver (a sliver's slope makes the sampling's reach unbounded).
            edges = [np.linalg.norm(tri[(k + 1) % 3, :2] - tri[k, :2]) for k in range(3)]
            area = 0.5 * abs(np.cross(tri[1, :2] - tri[0, :2], tri[2, :2] - tri[0, :2]))
            if 2.0 * area / max(edges) >= 5.0:
                reach = 0.5 if cutter.shape is CutterShape.FLAT else 1.5
                assert exact <= brute + reach


class TestTheWaterline:
    BLOCK = _box(40.0, 20.0, 30.0)

    @staticmethod
    def _distance_to_block(x: float, y: float) -> float:
        dx = max(-x, 0.0, x - 40.0)
        dy = max(-y, 0.0, y - 20.0)
        return math.hypot(dx, dy)

    @pytest.mark.parametrize("cutter", [FLAT, BALL], ids=["flat", "ball"])
    def test_around_a_block_it_is_one_loop_offset_by_the_radius(self, cutter: Cutter) -> None:
        line = waterline(self.BLOCK, cutter, 15.0, step_mm=1.0, tolerance_mm=1e-7)
        assert len(line.loops) == 1
        assert not line.open_paths
        for x, y in line.loops[0]:
            assert self._distance_to_block(x, y) == pytest.approx(5.0, abs=1e-4)

    def test_above_the_part_there_is_nothing_to_follow(self) -> None:
        assert waterline(self.BLOCK, FLAT, 35.0, step_mm=1.0).loops == ()

    def test_it_says_it_is_sampled(self) -> None:
        payload = waterline(self.BLOCK, FLAT, 15.0, step_mm=2.0).to_dict()
        assert payload["step_mm"] == 2.0
        assert payload["basis"].startswith("sampled")

    def test_a_step_of_zero_is_refused(self) -> None:
        with pytest.raises(CamError, match="positive grid step"):
            waterline(self.BLOCK, FLAT, 15.0, step_mm=0.0)


class TestTheRaster:
    def test_lines_alternate_and_ride_the_top(self) -> None:
        path = raster(PLATEAU, FLAT, stepover_mm=25.0, sample_mm=10.0, floor_z_mm=-1.0)
        assert len(path.lines) == 5
        assert path.lines[0][0][0] < path.lines[0][-1][0]
        assert path.lines[1][0][0] > path.lines[1][-1][0]
        assert all(z == pytest.approx(10.0) for _, _, z in path.lines[2])


MEASURED = {
    "bounding_box_mm": {"size": [60.0, 40.0, 20.0]},
    "volume_mm3": 30000.0,
    "minimum_concave_radius_mm": 4.0,
}
ALLOWANCE = Limit(2.0, FIXTURE)
STOCK = [
    StockBlock((100.0, 50.0, 25.0), FIXTURE),
    StockBlock((70.0, 50.0, 30.0), FIXTURE),
    StockBlock((25.0, 65.0, 45.0), FIXTURE),
]
CUTTERS = [
    Cutter(CutterShape.FLAT, 6.0, 20.0, FIXTURE),
    Cutter(CutterShape.FLAT, 8.0, 20.0, FIXTURE),
    Cutter(CutterShape.FLAT, 10.0, 25.0, FIXTURE),
]


class TestTheMachiningPlan:
    def test_the_smallest_block_that_holds_the_part_in_any_orientation(self) -> None:
        # needed 64 x 44 x 24; the 25 x 65 x 45 block holds it turned, and is smallest.
        result = plan(["catia_pad"], MEASURED, allowance=ALLOWANCE, stock_list=STOCK)
        assert result.stock_needed_mm == (64.0, 44.0, 24.0)
        assert result.stock is not None and result.stock.size_mm == (25.0, 65.0, 45.0)
        assert result.removed_volume_mm3 == pytest.approx(25 * 65 * 45 - 30000.0)

    def test_the_oriented_box_is_preferred_and_named(self) -> None:
        measured = {**MEASURED, "oriented_bounding_box_mm": {"size": [58.0, 38.0, 20.0]}}
        result = plan(["catia_pad"], measured, allowance=ALLOWANCE)
        assert result.stock_needed_mm == (62.0, 42.0, 24.0)
        assert result.stock_basis.startswith("oriented")
        assert result.stock is None

    def test_a_pocket_takes_the_largest_cutter_its_corner_allows(self) -> None:
        result = plan(["catia_pocket"], MEASURED, allowance=ALLOWANCE, cutters=CUTTERS)
        [pocket] = result.operations
        assert pocket.kind == "pocket"
        assert pocket.cutter is not None and pocket.cutter.diameter_mm == 8.0

    def test_an_unscanned_corner_leaves_the_cutter_open_and_says_why(self) -> None:
        measured = {k: v for k, v in MEASURED.items() if k != "minimum_concave_radius_mm"}
        [pocket] = plan(["catia_pocket"], measured, allowance=ALLOWANCE, cutters=CUTTERS).operations
        assert pocket.cutter is None
        assert "curvature scan" in pocket.note

    def test_every_feature_is_accounted_for(self) -> None:
        tools = ["catia_pad", "catia_hole", "catia_mirror"]
        kinds = [op.kind for op in plan(tools, MEASURED, allowance=ALLOWANCE).operations]
        assert kinds == ["contour", "drill", "none"]

    def test_fixturing_is_three_two_one_over_the_datums(self) -> None:
        scheme = DatumScheme(
            (Datum("A", "base face"), Datum("B", "long side"), Datum("C", "end face"))
        )
        result = plan(["catia_pad"], MEASURED, allowance=ALLOWANCE, datums=scheme)
        assert [(loc.datum, loc.points) for loc in result.locators] == [("A", 3), ("B", 2), ("C", 1)]

    def test_one_datum_says_how_many_freedoms_are_left_to_the_clamps(self) -> None:
        scheme = DatumScheme((Datum("A", "base face"),))
        result = plan(["catia_pad"], MEASURED, allowance=ALLOWANCE, datums=scheme)
        assert any("lock 3 of six" in note for note in result.notes)

    def test_no_datums_no_fixturing(self) -> None:
        result = plan(["catia_pad"], MEASURED, allowance=ALLOWANCE)
        assert result.locators == ()
        assert any("cannot be located" in note for note in result.notes)

    def test_no_bounding_box_is_refused(self) -> None:
        with pytest.raises(CamError, match="no bounding box"):
            plan(["catia_pad"], {"volume_mm3": 1.0}, allowance=ALLOWANCE)
