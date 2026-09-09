"""Shell and beam meshes — master plan 6.3.

Offline and instant: no gmsh, no `ccx`, no database. These meshes are authored
rather than meshed today (nothing produces one — see `app/mesh/structural.py`),
so every fixture below is written out by hand and its geometry is known exactly.

Every test is named after the wrong answer it prevents. The ones that matter are
the quiet ones: a quadrilateral whose area was taken as a single triangle, a
midside array one column short, a segment of zero length whose direction is
`0/0`.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.mesh.structural import (
    BEAM_EDGES,
    SHELL_QUAD_EDGES,
    SHELL_TRI_EDGES,
    BeamMesh,
    ShellMesh,
)
from app.mesh.types import MeshError


def _plate(quadratic: bool = False) -> ShellMesh:
    """A flat 100 x 50 plate as two quadrilaterals, in the z = 0 plane.

    Two rather than one so that a bug summing only the first face is visible,
    and unequal so that a bug taking the mean rather than the sum is too.
    """
    nodes = np.array(
        [
            [0.0, 0.0, 0.0],
            [60.0, 0.0, 0.0],
            [100.0, 0.0, 0.0],
            [0.0, 50.0, 0.0],
            [60.0, 50.0, 0.0],
            [100.0, 50.0, 0.0],
        ]
    )
    faces = np.array([[0, 1, 4, 3], [1, 2, 5, 4]])
    if not quadratic:
        return ShellMesh(nodes=nodes, faces=faces)

    midside_nodes = np.array(
        [
            [30.0, 0.0, 0.0],
            [60.0, 25.0, 0.0],
            [30.0, 50.0, 0.0],
            [0.0, 25.0, 0.0],
            [80.0, 0.0, 0.0],
            [100.0, 25.0, 0.0],
            [80.0, 50.0, 0.0],
        ]
    )
    return ShellMesh(
        nodes=np.vstack([nodes, midside_nodes]),
        faces=faces,
        midside=np.array([[6, 7, 8, 9], [10, 11, 12, 7]]),
    )


def _cantilever(segments: int = 4, length: float = 800.0) -> BeamMesh:
    """A straight beam along x, in `segments` equal pieces."""
    xs = np.linspace(0.0, length, segments + 1)
    nodes = np.stack([xs, np.zeros_like(xs), np.zeros_like(xs)], axis=1)
    ends = np.array([[i, i + 1] for i in range(segments)])
    return BeamMesh(nodes=nodes, segments=ends)


class TestAShellMeshKnowsWhatShapeItIs:
    def test_a_triangular_mesh_says_so(self) -> None:
        mesh = ShellMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]]),
            faces=np.array([[0, 1, 2]]),
        )

        assert mesh.is_triangular
        assert mesh.element_order == 1
        assert mesh.element_type == "tri3"

    def test_a_quadrilateral_mesh_says_so(self) -> None:
        mesh = _plate()

        assert not mesh.is_triangular
        assert mesh.element_order == 1
        assert mesh.element_type == "quad4"

    def test_midside_nodes_make_it_quadratic(self) -> None:
        mesh = _plate(quadratic=True)

        assert mesh.element_order == 2
        assert mesh.element_type == "quad8"

    def test_the_element_type_is_ours_and_not_calculixs(self) -> None:
        """A mesh naming CalculiX's element would hand one backend's vocabulary
        to every other one, and a second backend would inherit a spelling it has
        no use for. The mapping lives in the CalculiX adapter."""
        assert "S" not in _plate().element_type.upper().replace("QUAD", "")


class TestAShellMeshReportsTheAreaItReallyHas:
    def test_the_plate_measures_its_own_outline(self) -> None:
        """100 x 50, split 60/40. Computed here from the dimensions rather than
        read off the mesh."""
        assert _plate().area_mm2 == pytest.approx(100.0 * 50.0)

    def test_both_faces_are_counted(self) -> None:
        areas = _plate().face_areas()

        assert len(areas) == 2
        assert areas[0] == pytest.approx(60.0 * 50.0)
        assert areas[1] == pytest.approx(40.0 * 50.0)

    def test_a_quadrilateral_is_not_measured_as_one_triangle(self) -> None:
        """The commonest way to get this wrong, and it halves the answer — which
        for a mass roll-up looks like a lighter design rather than a bug."""
        one_quad = ShellMesh(
            nodes=np.array(
                [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 4.0, 0.0], [0.0, 4.0, 0.0]]
            ),
            faces=np.array([[0, 1, 2, 3]]),
        )

        assert one_quad.area_mm2 == pytest.approx(40.0)

    def test_a_triangle_is_half_its_bounding_rectangle(self) -> None:
        mesh = ShellMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 4.0, 0.0]]),
            faces=np.array([[0, 1, 2]]),
        )

        assert mesh.area_mm2 == pytest.approx(20.0)

    def test_area_does_not_depend_on_which_plane_the_plate_lies_in(self) -> None:
        """A shell is a surface in space, not a drawing. An area computed from
        two coordinates rather than from the cross product would read zero for a
        vertical wall — which is most of a frame's cladding."""
        upright = ShellMesh(
            nodes=np.array(
                [[0.0, 0.0, 0.0], [0.0, 0.0, 10.0], [0.0, 4.0, 10.0], [0.0, 4.0, 0.0]]
            ),
            faces=np.array([[0, 1, 2, 3]]),
        )

        assert upright.area_mm2 == pytest.approx(40.0)

    def test_promoting_to_quadratic_does_not_move_the_area(self) -> None:
        """Corner nodes only, deliberately: a quadratic face bulges, and an area
        that grew on promotion would make the same plate weigh two different
        amounts depending on the element order."""
        assert _plate(quadratic=True).area_mm2 == pytest.approx(_plate().area_mm2)

    def test_the_volume_is_the_area_times_the_thickness(self) -> None:
        assert _plate().volume_mm3(1.5) == pytest.approx(100.0 * 50.0 * 1.5)

    def test_a_zero_thickness_is_refused_rather_than_returning_no_material(self) -> None:
        with pytest.raises(MeshError, match="encloses no material"):
            _plate().volume_mm3(0.0)


class TestAShellMeshRefusesWhatItCannotBe:
    def test_a_mixed_mesh_is_refused(self) -> None:
        """CalculiX writes one `*ELEMENT, TYPE=` per element type, so a mesh of
        triangles and quadrilaterals is two element sets and two section cards.
        Accepting one array of both would write a deck whose second half was
        silently dropped."""
        with pytest.raises(MeshError, match=r"\(m, 3\)"):
            ShellMesh(
                nodes=np.zeros((5, 3)),
                faces=np.array([[0, 1, 2, 3, 4]]),
            )

    def test_a_face_referring_to_a_node_that_is_not_there_is_refused(self) -> None:
        """Measured on 2026-09-05: CalculiX accepts an out-of-range node number
        silently and returns a stress 260 times too high. `TetMesh` already
        refuses it at construction and so does this."""
        with pytest.raises(MeshError, match="outside the node array"):
            ShellMesh(nodes=np.zeros((3, 3)), faces=np.array([[0, 1, 7]]))

    def test_a_midside_array_one_column_short_is_refused(self) -> None:
        """One node per *edge*, so a quadrilateral needs four and not three. A
        three-column array would be read as a triangle's and shift every midside
        node onto the wrong edge."""
        with pytest.raises(MeshError, match="one node per edge"):
            ShellMesh(
                nodes=np.zeros((8, 3)),
                faces=np.array([[0, 1, 2, 3]]),
                midside=np.array([[4, 5, 6]]),
            )

    def test_an_empty_mesh_is_refused(self) -> None:
        with pytest.raises(MeshError, match="no faces"):
            ShellMesh(nodes=np.zeros((3, 3)), faces=np.zeros((0, 3), dtype=np.int64))

    def test_the_edge_table_matches_the_shape(self) -> None:
        assert _plate().edge_table == SHELL_QUAD_EDGES
        triangle = ShellMesh(nodes=np.zeros((3, 3)), faces=np.array([[0, 1, 2]]))
        assert triangle.edge_table == SHELL_TRI_EDGES

    def test_every_edge_table_walks_the_corners_in_a_closed_loop(self) -> None:
        """The property that makes a table an edge table at all: slot k joins
        corner k to corner k+1, and the last one closes back to the first. A
        table with a transposed pair still has the right shape and puts a midside
        node on a diagonal."""
        for table, corners in ((SHELL_TRI_EDGES, 3), (SHELL_QUAD_EDGES, 4), (BEAM_EDGES, 2)):
            assert len(table) == (corners if corners > 2 else 1)
            for slot, (first, second) in enumerate(table):
                assert first == slot
                assert second == (slot + 1) % corners


class TestABeamMeshKnowsWhereItsMembersRun:
    def test_the_length_is_the_sum_of_the_segments(self) -> None:
        assert _cantilever(segments=4, length=800.0).length_mm == pytest.approx(800.0)

    def test_a_diagonal_member_is_measured_along_itself(self) -> None:
        """A length taken from a bounding box rather than from the chord reads a
        3-4-5 brace as 4 mm long instead of 5."""
        mesh = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [3.0, 4.0, 0.0]]),
            segments=np.array([[0, 1]]),
        )

        assert mesh.length_mm == pytest.approx(5.0)

    def test_directions_are_unit_vectors(self) -> None:
        directions = _cantilever().directions()

        assert np.allclose(np.linalg.norm(directions, axis=1), 1.0)

    def test_the_direction_runs_from_the_first_end_to_the_second(self) -> None:
        """A sign error here turns the local frame inside out, and every section
        oriented against it is mirrored — which for an asymmetric profile is a
        different member."""
        mesh = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, -10.0]]),
            segments=np.array([[0, 1]]),
        )

        assert mesh.directions()[0] == pytest.approx([0.0, 0.0, -1.0])

    def test_a_quadratic_beam_takes_its_axis_from_the_chord(self) -> None:
        """Not from the middle node. The cross section is oriented about the
        member, and a middle node that has been displaced is a mesh to refine,
        not a new definition of which way the member runs."""
        straight = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 1.0, 0.0]]),
            segments=np.array([[0, 1]]),
            midside=np.array([[2]]),
        )

        assert straight.directions()[0] == pytest.approx([1.0, 0.0, 0.0])
        assert straight.length_mm == pytest.approx(10.0)

    def test_the_volume_is_the_length_times_the_area(self) -> None:
        assert _cantilever(length=1000.0).volume_mm3(736.0) == pytest.approx(736000.0)


class TestABeamMeshRefusesWhatItCannotBe:
    def test_a_segment_of_zero_length_is_refused(self) -> None:
        """`directions` would divide by zero, and a local frame built on a zero
        vector orients the cross section nowhere at all."""
        with pytest.raises(MeshError, match="no length and no direction"):
            BeamMesh(nodes=np.zeros((2, 3)), segments=np.array([[0, 0]]))

    def test_a_segment_referring_to_a_missing_node_is_refused(self) -> None:
        with pytest.raises(MeshError, match="outside the node array"):
            BeamMesh(nodes=np.zeros((2, 3)), segments=np.array([[0, 5]]))

    def test_a_three_column_segment_array_is_refused(self) -> None:
        """The middle node goes in `midside`, deliberately: which node is the
        middle one is then a fact about the data structure rather than a
        convention a reader has to carry."""
        with pytest.raises(MeshError, match=r"\(m, 2\)"):
            BeamMesh(nodes=np.zeros((3, 3)), segments=np.array([[0, 1, 2]]))

    def test_an_empty_mesh_is_refused(self) -> None:
        with pytest.raises(MeshError, match="no segments"):
            BeamMesh(nodes=np.zeros((2, 3)), segments=np.zeros((0, 2), dtype=np.int64))


class TestConnectivityPutsCornersBeforeMidsideNodes:
    """Shells list their corners first. **Beams do not, and assuming they did
    was a defect that shipped** — see the beam test below.

    A connectivity in the wrong order still has the right node count and every
    index in range, so it describes a differently shaped element rather than
    failing — the C3D10 trap, one shape up.
    """

    def test_a_linear_shell_is_its_own_connectivity(self) -> None:
        mesh = _plate()

        assert np.array_equal(mesh.connectivity, mesh.faces)

    def test_a_quadratic_shell_appends_its_midside_nodes(self) -> None:
        mesh = _plate(quadratic=True)

        assert mesh.connectivity.shape == (2, 8)
        assert np.array_equal(mesh.connectivity[:, :4], mesh.faces)
        assert mesh.midside is not None
        assert np.array_equal(mesh.connectivity[:, 4:], mesh.midside)

    def test_a_quadratic_beam_puts_the_middle_node_in_the_middle(self) -> None:
        """The exception to the rule this class is named for, and it is CalculiX's.

        This asserted `[0, 1, 2]` — corners then midside — until 2026-09-09, and
        the code agreed with it, which is why nothing offline caught it: the test
        was written from the same understanding as the code and was wrong in the
        same direction. On the seat, ccx reads a beam row *along the member*, so
        `[start, end, middle]` makes the far end the midside node and folds the
        element back on itself:

            *ERROR in e_c3d: nonpositive jacobian determinant in element 1

        No quadratic beam deck this repo wrote had ever solved.
        """
        mesh = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 0.0, 0.0]]),
            segments=np.array([[0, 1]]),
            midside=np.array([[2]]),
        )

        assert list(mesh.connectivity[0]) == [0, 2, 1]

    def test_the_middle_node_is_geometrically_between_the_ends(self) -> None:
        """The property the ordering exists for, stated in coordinates rather
        than indices — an index order is only right because the point it names
        lies between the two it sits between."""
        mesh = BeamMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 0.0, 0.0]]),
            segments=np.array([[0, 1]]),
            midside=np.array([[2]]),
        )

        xs = mesh.nodes[mesh.connectivity[0]][:, 0]

        assert xs[0] < xs[1] < xs[2]


class TestGeometryThatEveryMeshMustReport:
    def test_a_shell_bounding_box_covers_the_plate(self) -> None:
        low, high = _plate().bounding_box

        assert low == pytest.approx([0.0, 0.0, 0.0])
        assert high == pytest.approx([100.0, 50.0, 0.0])

    def test_a_beam_bounding_box_covers_the_member(self) -> None:
        low, high = _cantilever(length=800.0).bounding_box

        assert low == pytest.approx([0.0, 0.0, 0.0])
        assert high == pytest.approx([800.0, 0.0, 0.0])

    def test_counts_are_what_was_given(self) -> None:
        assert _plate().face_count == 2
        assert _plate().node_count == 6
        assert _cantilever(segments=4).segment_count == 4
        assert _cantilever(segments=4).node_count == 5

    def test_a_warped_quadrilateral_measures_its_folded_area(self) -> None:
        """No single flat area exists for a warped face; the two triangles the
        0-2 split produces are what a mid-surface load would act on. Pinned so
        that a later change to a projected area is a decision somebody makes
        rather than one that happens.

        Checked with **Heron's formula from the edge lengths**, which shares no
        code and no algebra with the cross product the mesh uses — and which is
        also the only reason this test is right: the obvious expectation, one
        flat triangle plus one tilted one, describes a split along the *other*
        diagonal and is 5.9 mm^2 short.
        """
        corners = np.array(
            [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 10.0, 5.0], [0.0, 10.0, 0.0]]
        )
        warped = ShellMesh(nodes=corners, faces=np.array([[0, 1, 2, 3]]))

        def heron(a: int, b: int, c: int) -> float:
            sides = [
                float(np.linalg.norm(corners[i] - corners[j]))
                for i, j in ((a, b), (b, c), (c, a))
            ]
            half = sum(sides) / 2.0
            return math.sqrt(math.prod(half - side for side in sides) * half)

        assert warped.area_mm2 == pytest.approx(heron(0, 1, 2) + heron(0, 2, 3))
        assert warped.area_mm2 > 100.0  # a warped face is larger than its projection
