"""What an edge is, worked out from the only measurement this seat will give.

`catia_list_edges` shipped asking `Measurable` for `GetCOG` and `GetDirection`
per edge. Measured against a real V5-R33 on 2026-09-06, on the block ladder
prompt H3 built:

    Length                       -> 60.0
    Radius                       -> COM error, CATIAMeasurable (not an arc)
    GetCOG(list)                 -> COM error, E_NOTIMPL
    GetDirection(list)           -> "succeeds", leaves [0.0, 0.0, 0.0]

`GetDirection` is the interesting one: the out-array is passed by value from
Python, so the call returns without error having written nothing. So every
edge of every part came back `kind: "unknown"` with no midpoint, and the tool's
own `kind` filter could never match anything -- which is what the agent was
reaching for when it tried `kind="vertical"` and was refused, then tried to
`catia_select` eight edge ids and was refused again.

What does work is `vba.edge_map`: `GetPointsOnCurve` run *inside* CATIA, one
Evaluate for the whole part, three points per edge. `_select_edges` has
classified edges from those three points since the fillet tool was made to
work on the seat, so `edges.py` is that arithmetic lifted out of a closure and
shared -- the list tool and the fillet selector must agree on what "vertical"
means, or the agent reads one set of edges and rounds another.

These tests are offline and pure: three points in, a fact out. The seat run
above is what proves the *inputs* are the ones available; this is what proves
the arithmetic over them is right.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge import edges  # noqa: E402

# A 100 x 60 x 30 block at the origin, z = 0..30 -- H3's block, whose edges are
# the ones the agent could not find.
Z_TOP, Z_BOTTOM = 30.0, 0.0


def line(start: tuple[float, float, float], end: tuple[float, float, float]) -> edges.Triple:
    """A straight edge, with the midpoint CATIA would report for it."""
    middle = tuple((a + b) / 2.0 for a, b in zip(start, end, strict=True))
    return start, middle, end  # type: ignore[return-value]


def arc(
    centre: tuple[float, float],
    radius: float,
    z: float,
    start_deg: float,
    end_deg: float,
) -> edges.Triple:
    """A circular arc in a z-plane, sampled at start, parametric middle and end."""

    def point(degrees: float) -> tuple[float, float, float]:
        angle = math.radians(degrees)
        return (
            centre[0] + radius * math.cos(angle),
            centre[1] + radius * math.sin(angle),
            z,
        )

    return point(start_deg), point((start_deg + end_deg) / 2.0), point(end_deg)


class TestOrientation:
    def test_a_corner_of_the_block_is_vertical(self) -> None:
        """The four edges H3 asked for by name and never got."""
        edge = line((50.0, 30.0, 0.0), (50.0, 30.0, 30.0))
        assert "vertical" in edges.orientations(edge, Z_TOP, Z_BOTTOM)

    def test_a_top_rim_is_both_top_and_horizontal(self) -> None:
        """An edge is not one thing. Reporting a single kind would force the
        tool to choose between two true answers."""
        edge = line((-50.0, 30.0, 30.0), (50.0, 30.0, 30.0))
        assert edges.orientations(edge, Z_TOP, Z_BOTTOM) == frozenset({"top", "horizontal"})

    def test_a_bottom_rim_is_bottom_and_horizontal(self) -> None:
        edge = line((-50.0, 30.0, 0.0), (50.0, 30.0, 0.0))
        assert edges.orientations(edge, Z_TOP, Z_BOTTOM) == frozenset({"bottom", "horizontal"})

    def test_a_horizontal_edge_partway_up_is_horizontal_and_nothing_else(self) -> None:
        """The rim of a pocket cut into the side. `top` means the top of the
        part, not "the upper edge of this face"."""
        edge = line((-50.0, 30.0, 12.0), (50.0, 30.0, 12.0))
        assert edges.orientations(edge, Z_TOP, Z_BOTTOM) == frozenset({"horizontal"})

    def test_a_slanted_edge_is_none_of_them(self) -> None:
        """A chamfer's edge. It must not be swept into `vertical` by a loose
        test, because a fillet applied to it is a fillet on the wrong edge."""
        edge = line((50.0, 30.0, 0.0), (45.0, 30.0, 30.0))
        assert edges.orientations(edge, Z_TOP, Z_BOTTOM) == frozenset()

    def test_a_bore_rim_on_the_top_face_is_top_and_horizontal(self) -> None:
        """A circular edge is classified by where it lies, not by being curved."""
        edge = arc((0.0, 0.0), 6.0, 30.0, 0.0, 180.0)
        assert edges.orientations(edge, Z_TOP, Z_BOTTOM) == frozenset({"top", "horizontal"})

    def test_a_vertical_semicircle_is_not_a_vertical_edge(self) -> None:
        """The tightening this move paid for.

        `_select_edges` tested only the two ends: same x, same y, different z.
        A bore through a side face leaves a semicircular edge whose ends sit
        one above the other and whose middle bulges sideways -- it passed that
        test and joined "the vertical edges". Filleting it is not what anyone
        means by rounding the corners of a plate. The middle point is the
        whole difference, and it is why `orientations` takes three points and
        not two.
        """
        start = (50.0, 0.0, 5.0)
        middle = (50.0, 6.0, 11.0)
        end = (50.0, 0.0, 17.0)
        assert "vertical" not in edges.orientations((start, middle, end), Z_TOP, Z_BOTTOM)

    def test_the_z_extent_comes_from_every_point_of_every_edge(self) -> None:
        """`top` is relative to the part, so the extent has to be measured over
        the same set of edges being classified -- taking it from the ends only
        would miss an arc that crests above both of them."""
        top, bottom = edges.z_extent([line((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), arc((0.0, 0.0), 5.0, 30.0, 0.0, 90.0)])
        assert (top, bottom) == (30.0, 0.0)


class TestCurveKind:
    def test_a_straight_edge_is_linear(self) -> None:
        assert edges.is_linear(line((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)))

    def test_an_arc_is_not_linear(self) -> None:
        assert not edges.is_linear(arc((0.0, 0.0), 8.0, 0.0, 0.0, 90.0))

    def test_a_metre_long_edge_is_still_straight(self) -> None:
        """The case a length-scaled tolerance was invented for. It is straight
        under the plain absolute test too, because CATIA's coordinates are
        exact and the floating-point noise on a 2 m edge is ~1e-13 mm."""
        assert edges.is_linear(line((-1000.0, 0.0, 0.0), (1000.0, 0.0, 0.0)))

    def test_the_shallowest_arc_worth_noticing_is_not_called_straight(self) -> None:
        """The property the tolerance actually has to hold: a 10 m radius over
        a 50 mm edge -- a barely visible crown -- bulges 0.03 mm off its chord,
        four orders of magnitude above the tolerance. Anything a length-scaled
        tolerance would have loosened is still caught."""
        radius, span = 10_000.0, 50.0
        half = math.degrees(math.asin(span / (2.0 * radius)))
        assert not edges.is_linear(arc((0.0, 0.0), radius, 0.0, -half, half))

    @pytest.mark.parametrize("degrees", [30.0, 90.0, 180.0, 270.0, 350.0])
    def test_the_arc_length_is_right_including_past_a_semicircle(self, degrees: float) -> None:
        """The parametric middle is the arc's middle, so each half is under a
        semicircle even when the whole arc is not -- which is what keeps a
        270-degree arc from being reported as its 90-degree complement."""
        radius = 8.0
        found = edges.circle_through(arc((3.0, -2.0), radius, 4.0, 0.0, degrees))
        assert found is not None
        measured_radius, length = found
        assert measured_radius == pytest.approx(radius, rel=1e-9)
        assert length == pytest.approx(radius * math.radians(degrees), rel=1e-9)

    def test_a_closed_circle_is_a_full_circumference(self) -> None:
        """A bore's rim arrives as one closed edge: start == end, middle
        diametrically opposite. The general formula divides by the chord, which
        is zero here, so this case is answered first."""
        radius = 6.0
        found = edges.circle_through(((radius, 0.0, 0.0), (-radius, 0.0, 0.0), (radius, 0.0, 0.0)))
        assert found is not None
        assert found[0] == pytest.approx(radius)
        assert found[1] == pytest.approx(2.0 * math.pi * radius)

    def test_three_collinear_points_are_not_a_circle(self) -> None:
        """No division by zero dressed up as a radius."""
        assert edges.circle_through(line((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))) is None


class TestWhatTheToolReports:
    def test_a_straight_edge_reports_its_length_and_orientation(self) -> None:
        facts = edges.describe(line((50.0, 30.0, 0.0), (50.0, 30.0, 30.0)), Z_TOP, Z_BOTTOM)
        assert facts["kind"] == "linear"
        assert facts["length_mm"] == 30.0
        assert facts["orientation"] == ["vertical"]
        assert facts["midpoint"] == [50.0, 30.0, 15.0]

    def test_a_curve_is_circular_only_on_catias_own_radius(self) -> None:
        """Any three points define *some* circle, so three points cannot tell a
        circle from a spline. `Measurable.Radius` can, it is the one per-edge
        call that works over COM, and it refuses on anything that is not an arc
        -- so it is the evidence, and its absence is the answer.
        """
        curve = arc((0.0, 0.0), 8.0, 30.0, 0.0, 90.0)
        assert edges.describe(curve, Z_TOP, Z_BOTTOM)["kind"] == "other"
        assert edges.describe(curve, Z_TOP, Z_BOTTOM, radius_mm=8.0)["kind"] == "circular"

    def test_a_spline_says_its_length_is_a_chord(self) -> None:
        """An unmeasured length that looks measured is the failure this whole
        module exists to end. `length_is_chord` is how it says so."""
        spline = ((0.0, 0.0, 0.0), (4.0, 9.0, 1.0), (10.0, 0.0, 0.0))
        facts = edges.describe(spline, Z_TOP, Z_BOTTOM)
        assert facts["kind"] == "other"
        assert facts["length_is_chord"] is True
        assert facts["length_mm"] == 10.0

    def test_the_radius_reported_is_catias_and_not_the_one_fitted(self) -> None:
        """Where they disagree, CATIA's is the measurement and the fit is
        arithmetic over three sampled points."""
        curve = arc((0.0, 0.0), 8.0, 30.0, 0.0, 90.0)
        facts = edges.describe(curve, Z_TOP, Z_BOTTOM, radius_mm=8.0004)
        assert facts["radius_mm"] == 8.0004


class TestOneClassifierForBothTools:
    """The list tool and the fillet selector must not drift apart.

    They are two answers to one question -- "which edges are the vertical
    ones" -- and the whole point of the H3 fix is that reading them and
    rounding them agree. A second copy of the arithmetic would pass its own
    tests and round a different set of edges.
    """

    def test_neither_classifies_edges_for_itself(self) -> None:
        import ast

        root = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
        for path in (root / "catia_com.py", root / "com" / "reference.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name in {"_select_edges", "list_edges"}:
                    source = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
                    assert "edge_geometry." in source, (
                        f"{path.name}:{node.name} no longer goes through edges.py -- the "
                        "two tools can now disagree about what 'vertical' means"
                    )

    def test_the_dead_com_calls_are_gone(self) -> None:
        """`GetCOG` and `GetDirection` on an *edge* are the two calls measured
        as unusable. They are still right for faces and for the solid, so this
        checks the edge listing specifically rather than banning the names."""
        source = (
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "catia_bridge"
            / "com"
            / "reference.py"
        ).read_text(encoding="utf-8")
        import ast

        tree = ast.parse(source)
        listing = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "list_edges"
        )
        # The docstring names both calls, because *why* they are gone is worth
        # keeping next to the code. So the check is over the statements.
        statements = [
            ast.get_source_segment(source, node) or ""
            for node in listing.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        ]
        body = "\n".join(statements)
        for dead in ("GetCOG", "GetDirection"):
            assert dead not in body, (
                f"catia_list_edges is calling {dead} again -- measured on V5-R33 it "
                "either raises or silently writes nothing, and every edge comes back "
                "'unknown'"
            )
