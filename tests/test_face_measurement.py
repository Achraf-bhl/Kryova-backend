"""What `catia_list_faces` reports, and why none of it came from Python.

Measured on V5-R33 on 2026-09-06, during ladder prompt H4. A face `Measurable`
answers `Area` and essentially nothing else:

    Area                  -> 0.006          (square METRES, for a 6000 mm2 face)
    GetCOG(list)          -> returns, list untouched
    GetPlane(list)        -> returns, list untouched

So every face of every part was reported with centre [0, 0, 0], normal
[0, 0, 0] and an area a millionth of its real one. On H4 the agent read that,
could not place a bolt hole from it, and spent the rest of its twenty rounds
creating empty sketches. `min_area_mm2` was worse than useless: a filter of 1
would have hidden every face there was.

Inside CATIA the same three calls fill their arrays, so `vba.face_map` sweeps
the part in one Evaluate -- the twin of `edge_map`, for the same reason.

Three things this file pins that are not obvious:

* **The area unit.** m2 in, mm2 out, converted at the boundary, which is where
  CLAUDE.md's mm-N-MPa rule says a unit has to land.
* **The face type is CATIA's, not deduced.** A face search reports
  `PlanarFace`, `CylindricalFace` and friends. The old code deduced the kind
  from which measurement calls succeeded -- a guess where a fact was sitting
  in the search result. It also means the *edge* filter (`TriDim` in the type)
  matched no face at all, which is why the first version of this fix reported
  every part as having no faces.
* **A plane normal is not an outward normal.** The bottom face of a block
  reports [0, 0, 1], pointing up into the solid. Where the face lies in a
  bounding-box plane the outward direction is a fact and is corrected; anywhere
  else the sign is CATIA's and is reported as unresolved rather than guessed.

Offline: the parser and the geometry, with CATIA's answers as fixtures.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge"
sys.path.insert(0, str(BRIDGE.parent))

from catia_bridge import vba  # noqa: E402
from catia_bridge.com.reference import _face_kind, _outward_normal  # noqa: E402

#: The block H3 built, as `KryovaFaceMap` really answered it on the seat:
#: index; area(m2); cx;cy;cz; then nine plane numbers (origin, then two
#: in-plane axes). Decimal commas, because the seat is French.
SEAT_ROWS = """\
1;0,006;0;0;30;0;0;30;1;0;0;0;1;0
2;0,006;0;0;0;0;0;0;1;0;0;0;1;0
3;0,0018;-50;0;15;-50;30;0;0;-1;0;0;0;1
4;0,003;4,736951571734E-16;30;15;50;30;0;-1;0;0;0;0;1
5;0,0018;50;0;15;50;-30;0;0;1;0;0;0;1
6;0,003;-4,736951571734E-16;-30;15;-50;-30;0;1;0;0;-0;0;1
"""

BOX = (-50.0, -30.0, 0.0, 50.0, 30.0, 30.0)


class _App:
    """Stands in for CATIA, returning what the seat returned."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.SystemService = self  # noqa: N815 - COM spelling

    def Evaluate(self, script: str, language: int, function: str, parameters: list) -> str:  # noqa: N802
        assert function == "KryovaFaceMap"
        return self.answer


def measure(answer: str = SEAT_ROWS) -> dict[int, vba.FaceFacts]:
    return vba.face_map(_App(answer), object(), "Topologie.Face,sel", object())


class TestTheSweep:
    def test_every_face_comes_back(self) -> None:
        assert sorted(measure()) == [1, 2, 3, 4, 5, 6]

    def test_areas_are_millimetres_squared(self) -> None:
        """0.006 m2 is 6000 mm2. Reported raw it was 0.006 mm2, and a face
        smaller than a grain of sand passes no `min_area_mm2` a caller would
        ever set."""
        areas = sorted(round(f.area_mm2, 3) for f in measure().values())
        assert areas == [1800.0, 1800.0, 3000.0, 3000.0, 6000.0, 6000.0]

    def test_the_total_is_the_blocks_real_surface_area(self) -> None:
        total = sum(f.area_mm2 for f in measure().values())
        assert total == pytest.approx(2 * (6000 + 3000 + 1800))

    def test_centres_are_where_the_faces_are(self) -> None:
        assert measure()[1].centre == pytest.approx((0.0, 0.0, 30.0))
        assert measure()[3].centre == pytest.approx((-50.0, 0.0, 15.0))

    def test_the_decimal_comma_is_read_as_a_decimal_point(self) -> None:
        """VBScript formats with the workstation's locale separator, so this
        seat answers `0,006`. Read as English it is 6 -- a thousand times the
        area -- and read as an error the face disappears."""
        assert measure()[4].area_mm2 == pytest.approx(3000.0)

    def test_a_scientific_notation_coordinate_survives(self) -> None:
        """`4,736951571734E-16` is CATIA's way of saying zero, and it appears
        in real output. A parser that chokes on it drops the face."""
        assert measure()[4].centre[0] == pytest.approx(0.0, abs=1e-9)

    def test_normals_come_from_the_planes_two_axes(self) -> None:
        assert measure()[1].normal == pytest.approx((0.0, 0.0, 1.0))
        assert measure()[3].normal == pytest.approx((-1.0, 0.0, 0.0))

    def test_a_face_with_no_plane_has_no_normal(self) -> None:
        """A cylinder answers `GetPlane` with nothing, so its nine numbers stay
        zero. None means "not planar", which is a fact; [0, 0, 0] is a vector
        that points nowhere and reads as a measurement."""
        cylinder = "7;0,002;0;0;15;" + ";".join(["0"] * 9)
        assert measure(cylinder)[7].normal is None

    def test_a_malformed_row_is_skipped_not_fatal(self) -> None:
        """One unmeasurable face must not cost the caller the other eleven."""
        rows = SEAT_ROWS + "7;not-a-number;0;0;0;0;0;0;0;0;0;0;0;0\n8;0,001;1;2;3\n"
        assert sorted(measure(rows)) == [1, 2, 3, 4, 5, 6]


class TestOutwardNormals:
    def test_the_bottom_face_of_a_block_faces_down(self) -> None:
        """The measured failure. Its plane normal is +Z, into the material."""
        normal, outward = _outward_normal((0.0, 0.0, 1.0), (0.0, 0.0, 0.0), BOX)
        assert normal == (0.0, 0.0, -1.0)
        assert outward is True

    def test_the_top_face_keeps_its_direction(self) -> None:
        normal, outward = _outward_normal((0.0, 0.0, 1.0), (0.0, 0.0, 30.0), BOX)
        assert normal == (0.0, 0.0, 1.0)
        assert outward is True

    @pytest.mark.parametrize(
        ("normal", "centre", "expected"),
        [
            ((1.0, 0.0, 0.0), (50.0, 0.0, 15.0), (1.0, 0.0, 0.0)),
            ((1.0, 0.0, 0.0), (-50.0, 0.0, 15.0), (-1.0, 0.0, 0.0)),
            ((0.0, 1.0, 0.0), (0.0, 30.0, 15.0), (0.0, 1.0, 0.0)),
            ((0.0, -1.0, 0.0), (0.0, -30.0, 15.0), (0.0, -1.0, 0.0)),
        ],
    )
    def test_every_side_of_the_box_is_resolved(
        self, normal: tuple, centre: tuple, expected: tuple
    ) -> None:
        found, outward = _outward_normal(normal, centre, BOX)
        assert found == expected
        assert outward is True

    def test_a_pocket_floor_is_left_unresolved(self) -> None:
        """It is inside the box, so which side the material is on cannot be
        settled from the box alone. Reported as unresolved, not guessed: an
        unmarked wrong direction gets drilled into."""
        normal, outward = _outward_normal((0.0, 0.0, 1.0), (0.0, 0.0, 20.0), BOX)
        assert normal == (0.0, 0.0, 1.0)
        assert outward is False

    def test_a_slanted_face_is_left_unresolved(self) -> None:
        """A draft or a chamfer face is not axis-aligned, so the box says
        nothing about it even when it touches one."""
        _, outward = _outward_normal((0.707, 0.0, 0.707), (50.0, 0.0, 30.0), BOX)
        assert outward is False

    def test_no_bounding_box_means_no_claim(self) -> None:
        normal, outward = _outward_normal((0.0, 0.0, 1.0), (0.0, 0.0, 0.0), None)
        assert normal == (0.0, 0.0, 1.0)
        assert outward is False


class TestFaceKinds:
    @pytest.mark.parametrize(
        ("reported", "kind"),
        [
            ("PlanarFace", "planar"),
            ("CylindricalFace", "cylindrical"),
            ("ConicalFace", "conical"),
            ("SphericalFace", "spherical"),
            ("Face", "other"),
            ("SomethingNobodyHasSeen", "other"),
        ],
    )
    def test_catias_own_type_is_the_answer(self, reported: str, kind: str) -> None:
        assert _face_kind(reported) == kind


class TestTheRoutesThatDoNotWork:
    """Structural, so the dead calls cannot come back one line at a time."""

    def _list_faces_body(self) -> str:
        source = (BRIDGE / "com" / "reference.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "list_faces"
        )
        return "\n".join(
            ast.get_source_segment(source, statement) or ""
            for statement in node.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        )

    @pytest.mark.parametrize("dead", ["GetCOG", "GetPlane"])
    def test_the_out_array_calls_are_not_made_from_python(self, dead: str) -> None:
        assert dead not in self._list_faces_body(), (
            f"catia_list_faces is calling {dead} from Python again -- it returns "
            "without error having written nothing, and every face reports [0, 0, 0]"
        )

    def test_the_face_search_does_not_filter_on_the_edge_type(self) -> None:
        """`TriDim` is what an *edge* search reports. Copied to faces it matched
        nothing, and every part looked as though it had no faces at all."""
        source = (BRIDGE / "com" / "reference.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_found_faces"
        )
        body = "\n".join(
            ast.get_source_segment(source, statement) or ""
            for statement in node.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        )
        assert '"Face" in str(' in body
        # The comment above the filter names TriDim as the thing it is not, so
        # the check is on the code and not on the explanation beside it.
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        assert "TriDim" not in code

    def test_the_face_types_are_read_before_the_bounding_box(self) -> None:
        """`_bounding_box` builds and measures reference planes, which clears
        the Selection -- so a type read after it raises "iIndex is not correct,
        give a value between 1 and 0". Ordering, asserted as ordering."""
        body = self._list_faces_body()
        assert body.index("Item2(index).Type") < body.index("_bounding_box()"), (
            "the face types are being read after the bounding box measurement, "
            "which has already emptied the selection they come from"
        )
