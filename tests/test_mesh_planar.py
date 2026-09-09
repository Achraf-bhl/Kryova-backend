"""Plane meshing: `app.mesh.planar` and the triangular path through gmsh.

Two halves, deliberately. The `TriMesh` half is hand-built from exact
coordinates and touches no mesher at all, so a unit square has an area of
exactly 1 mm^2 and a boundary of exactly four edges -- there is nothing to
approximate and nothing to tolerate. The `generate_tri_mesh` half drives real
gmsh, because what that layer is worth is entirely whether an actual CAD face
comes back as a plane mesh a solver can use.
"""

import math
from pathlib import Path

import numpy as np
import pytest

from app.mesh.gmsh_mesher import generate_tri_mesh
from app.mesh.planar import TRI6_EDGES, TriMesh, planar_quality
from app.mesh.types import MeshError

# The unit square as two anticlockwise triangles sharing the diagonal 0--2.
_SQUARE_NODES = np.array(
    [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]]
)
_SQUARE_TRIS = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)


def unit_square() -> TriMesh:
    """Two tri3s tiling the unit square: area 1, four boundary edges, one diagonal."""
    return TriMesh(nodes=_SQUARE_NODES.copy(), tris=_SQUARE_TRIS.copy())


def promote_to_tri6(mesh: TriMesh) -> TriMesh:
    """Add a midside node at the middle of every edge, giving a tri6 mesh.

    The 2-D counterpart of `app.mesh.primitives.promote_to_tet10`, and it exists
    for the same reason: edges stay straight, so the element geometry -- and
    therefore the area and the boundary -- is exactly the tri3 mesh's, and only
    the interpolation order changes. Sorting each corner pair makes the two
    triangles sharing an edge agree on its key, so the shared edge gets one node
    rather than two coincident ones.
    """
    pairs = mesh.tris[:, np.asarray(TRI6_EDGES, dtype=np.int64)]  # (n_tris, 3, 2)
    keys = np.sort(pairs, axis=2).reshape(-1, 2)
    unique_edges, inverse = np.unique(keys, axis=0, return_inverse=True)
    midpoints = 0.5 * (mesh.nodes[unique_edges[:, 0]] + mesh.nodes[unique_edges[:, 1]])
    return TriMesh(
        nodes=np.vstack([mesh.nodes, midpoints]),
        tris=mesh.tris.copy(),
        midside=(len(mesh.nodes) + inverse).reshape(len(mesh.tris), 3),
    )


def write_step_face(
    path: Path,
    size: tuple[float, float],
    z: float = 0.0,
    in_the_x_zero_plane: bool = False,
) -> Path:
    """Author a real STEP file holding one rectangular face, via gmsh's OCC kernel.

    `z` offsets the face off the plane and `in_the_x_zero_plane` rotates it into
    another one; both exist so the planarity refusal can be tested against a
    file rather than against a hand-made array.
    """
    import gmsh

    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        tag = gmsh.model.occ.addRectangle(0.0, 0.0, z, size[0], size[1])
        if in_the_x_zero_plane:
            gmsh.model.occ.rotate([(2, tag)], 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, math.pi / 2)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.clear()
        gmsh.finalize()
    return path


def write_step_solid(path: Path, size: tuple[float, float, float]) -> Path:
    """A STEP file holding a solid body, which is not a plane model."""
    import gmsh

    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.addBox(0.0, 0.0, 0.0, *size)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.clear()
        gmsh.finalize()
    return path


@pytest.fixture
def step_face(tmp_path: Path) -> Path:
    return write_step_face(tmp_path / "plate.step", (40.0, 20.0))


class TestTheHandBuiltSquare:
    """Exact geometry, so every number here is exact rather than approximate."""

    def test_the_area_is_the_sum_of_the_two_triangles(self) -> None:
        assert unit_square().area == pytest.approx(1.0, abs=1e-15)

    def test_it_reports_two_elements_and_four_nodes(self) -> None:
        mesh = unit_square()
        assert mesh.element_count == 2
        assert mesh.node_count == 4

    def test_an_anticlockwise_triangle_has_a_positive_signed_area(self) -> None:
        assert unit_square().signed_areas() == pytest.approx([0.5, 0.5], abs=1e-15)

    def test_the_bounding_box_is_the_square_itself(self) -> None:
        lo, hi = unit_square().bounding_box
        assert lo == pytest.approx([0.0, 0.0, 0.0], abs=1e-15)
        assert hi == pytest.approx([1.0, 1.0, 0.0], abs=1e-15)

    def test_a_tri3_mesh_is_first_order_and_its_connectivity_is_its_corners(self) -> None:
        mesh = unit_square()
        assert mesh.element_order == 1
        assert mesh.element_type == "tri3"
        assert mesh.connectivity.shape == (2, 3)

    def test_promoting_it_changes_the_order_and_not_the_geometry(self) -> None:
        mesh = promote_to_tri6(unit_square())
        assert mesh.element_order == 2
        assert mesh.element_type == "tri6"
        assert mesh.connectivity.shape == (2, 6)
        # Straight edges: the corners are untouched, so the area is unchanged.
        assert mesh.area == pytest.approx(1.0, abs=1e-15)
        # The shared diagonal gets one midside node, not two coincident ones.
        assert mesh.node_count == 4 + 5


class TestWinding:
    """The plane strain-displacement matrix divides by twice the signed area, so
    a triangle wound the wrong way is legal geometry and a broken element."""

    def test_a_clockwise_triangle_has_a_negative_signed_area(self) -> None:
        mesh = TriMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
            tris=np.array([[0, 2, 1]], dtype=np.int64),
        )
        assert mesh.signed_areas()[0] == pytest.approx(-0.5, abs=1e-15)
        # `area` is the magnitude, so it cannot be used to spot the inversion.
        assert mesh.area == pytest.approx(0.5, abs=1e-15)

    def test_reversing_two_corners_flips_the_sign_and_nothing_else(self) -> None:
        mesh = unit_square()
        flipped = TriMesh(nodes=mesh.nodes.copy(), tris=mesh.tris[:, [0, 2, 1]].copy())
        assert flipped.signed_areas() == pytest.approx(-mesh.signed_areas(), abs=1e-15)
        assert flipped.area == pytest.approx(mesh.area, abs=1e-15)


class TestBoundaryEdges:
    """The 2-D counterpart of `TetMesh.surface_triangles`: an edge traction is
    distributed by tributary length, so the outer edges have to be identified
    before a pressure can be applied mesh-independently."""

    def test_the_square_has_its_four_outer_edges_and_not_the_diagonal(self) -> None:
        found = {frozenset(map(int, edge)) for edge in unit_square().boundary_edges}
        assert found == {frozenset(pair) for pair in [(0, 1), (1, 2), (2, 3), (3, 0)]}
        assert frozenset((0, 2)) not in found, "the shared diagonal is interior"

    def test_every_boundary_edge_is_wound_as_its_owning_triangle_wound_it(self) -> None:
        # Anticlockwise triangles, so the boundary walks anticlockwise too --
        # which is what lets an outward normal be recovered from the winding.
        walked = {(int(a), int(b)) for a, b in unit_square().boundary_edges}
        assert walked == {(0, 1), (1, 2), (2, 3), (3, 0)}

    def test_a_tri3_mesh_reports_no_boundary_midsides(self) -> None:
        assert unit_square().boundary_edge_midsides is None

    def test_each_boundary_midside_is_the_midpoint_of_its_edge(self) -> None:
        mesh = promote_to_tri6(unit_square())
        edges = mesh.boundary_edges
        midsides = mesh.boundary_edge_midsides
        assert midsides is not None
        assert midsides.shape == (len(edges),)
        expected = 0.5 * (mesh.nodes[edges[:, 0]] + mesh.nodes[edges[:, 1]])
        assert mesh.nodes[midsides] == pytest.approx(expected, abs=1e-15)

    def test_the_answer_is_the_same_on_a_second_read(self) -> None:
        # It is cached on the instance; a cache that returned a different
        # answer the second time would be worse than no cache.
        mesh = unit_square()
        first = mesh.boundary_edges.copy()
        assert np.array_equal(mesh.boundary_edges, first)


class TestTriMeshRefusals:
    def test_nodes_must_have_three_columns(self) -> None:
        with pytest.raises(MeshError, match=r"nodes must have shape"):
            TriMesh(nodes=np.zeros((4, 2)), tris=_SQUARE_TRIS.copy())

    def test_triangles_must_have_three_corners(self) -> None:
        with pytest.raises(MeshError, match=r"tris must have shape"):
            TriMesh(nodes=_SQUARE_NODES.copy(), tris=np.zeros((2, 4), dtype=np.int64))

    def test_a_mesh_with_no_triangles_is_refused(self) -> None:
        with pytest.raises(MeshError, match="no triangles"):
            TriMesh(nodes=_SQUARE_NODES.copy(), tris=np.zeros((0, 3), dtype=np.int64))

    def test_a_triangle_may_not_reference_a_node_that_is_not_there(self) -> None:
        with pytest.raises(MeshError, match="outside the node array"):
            TriMesh(nodes=_SQUARE_NODES[:3].copy(), tris=_SQUARE_TRIS.copy())

    def test_a_node_off_the_plane_is_refused_and_the_plane_is_named(self) -> None:
        nodes = _SQUARE_NODES.copy()
        nodes[2, 2] = 0.5
        with pytest.raises(MeshError, match=r"z = 0") as excinfo:
            TriMesh(nodes=nodes, tris=_SQUARE_TRIS.copy())
        assert "0.5 mm off it" in str(excinfo.value)

    def test_the_planarity_tolerance_absorbs_arithmetic_and_nothing_more(self) -> None:
        nodes = _SQUARE_NODES.copy()
        nodes[2, 2] = 1e-9  # a mesher placing a node that is exactly on z = 0
        assert TriMesh(nodes=nodes, tris=_SQUARE_TRIS.copy()).element_count == 2

    def test_midside_must_carry_three_nodes_for_every_triangle(self) -> None:
        with pytest.raises(MeshError, match=r"midside must have shape"):
            TriMesh(
                nodes=_SQUARE_NODES.copy(),
                tris=_SQUARE_TRIS.copy(),
                midside=np.zeros((2, 6), dtype=np.int64),
            )

    def test_a_midside_index_outside_the_node_array_is_refused(self) -> None:
        with pytest.raises(MeshError, match="midside node index"):
            TriMesh(
                nodes=_SQUARE_NODES.copy(),
                tris=_SQUARE_TRIS.copy(),
                midside=np.full((2, 3), 99, dtype=np.int64),
            )


class TestPlanarQuality:
    """The shape factor is 1 for an equilateral triangle and 0 for a degenerate
    one -- the same 0-to-1 scale the tet radius ratio uses, so a report can
    print either without a legend."""

    @staticmethod
    def _equilateral() -> TriMesh:
        return TriMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.5, math.sqrt(3) / 2, 0.0]]),
            tris=np.array([[0, 1, 2]], dtype=np.int64),
        )

    @staticmethod
    def _sliver() -> TriMesh:
        return TriMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 1e-3, 0.0]]),
            tris=np.array([[0, 1, 2]], dtype=np.int64),
        )

    def test_an_equilateral_triangle_is_perfect(self) -> None:
        stats = planar_quality(self._equilateral())
        assert stats["mean_quality"] == pytest.approx(1.0, abs=1e-12)
        assert stats["sliver_count"] == 0
        assert stats["area_mm2"] == pytest.approx(math.sqrt(3) / 4, abs=1e-15)

    def test_a_sliver_scores_near_zero_and_is_counted(self) -> None:
        stats = planar_quality(self._sliver())
        assert stats["mean_quality"] == pytest.approx(2.31e-4, rel=0.01)
        assert stats["sliver_count"] == 1

    def test_only_the_sliver_is_counted_when_it_sits_beside_a_good_element(self) -> None:
        good = self._equilateral()
        sliver = self._sliver()
        mesh = TriMesh(
            nodes=np.vstack([good.nodes, sliver.nodes]),
            tris=np.vstack([good.tris, sliver.tris + len(good.nodes)]),
        )
        stats = planar_quality(mesh)
        assert stats["element_count"] == 2
        assert stats["sliver_count"] == 1

    def test_the_summary_names_the_element_type_it_measured(self) -> None:
        assert planar_quality(unit_square())["element_type"] == "tri3"
        assert planar_quality(promote_to_tri6(unit_square()))["element_type"] == "tri6"

    def test_min_quality_is_the_worst_element_and_not_a_constant_zero(self) -> None:
        """`shape.min(initial=0.0)` would read as a sensible empty-array guard and
        would in fact fold 0.0 into every reduction, pinning `min_quality` at 0
        for every mesh ever measured -- a report whose worst element is always
        degenerate is one nobody can act on. This is what catches that."""
        assert planar_quality(self._equilateral())["min_quality"] == pytest.approx(1.0, abs=1e-12)
        assert planar_quality(unit_square())["min_quality"] == pytest.approx(
            planar_quality(unit_square())["mean_quality"], abs=1e-12
        )

    def test_min_quality_reports_the_sliver_and_not_its_good_neighbour(self) -> None:
        good = self._equilateral()
        sliver = self._sliver()
        mesh = TriMesh(
            nodes=np.vstack([good.nodes, sliver.nodes]),
            tris=np.vstack([good.tris, sliver.tris + len(good.nodes)]),
        )
        stats = planar_quality(mesh)
        assert stats["min_quality"] == pytest.approx(
            planar_quality(sliver)["min_quality"], abs=1e-15
        )
        assert stats["min_quality"] < stats["mean_quality"]


class TestPlanarMeshingThroughGmsh:
    """A real STEP face, meshed by real gmsh, in the plane the caller authored."""

    def test_a_rectangular_face_meshes_to_its_exact_area(self, step_face: Path) -> None:
        # Straight edges chord nothing on a rectangle, so the triangulation
        # covers it exactly.
        mesh, stats = generate_tri_mesh(step_face, "step")
        assert mesh.area == pytest.approx(40.0 * 20.0, rel=1e-9)
        assert stats["area_mm2"] == pytest.approx(800.0, rel=1e-9)
        assert stats["mesher"] == "gmsh"

    def test_the_mesh_lands_in_the_plane_it_was_authored_in(self, step_face: Path) -> None:
        mesh, _ = generate_tri_mesh(step_face, "step")
        lo, hi = mesh.bounding_box
        assert lo == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
        assert hi == pytest.approx([40.0, 20.0, 0.0], abs=1e-9)

    def test_first_order_is_the_default_and_gives_tri3(self, step_face: Path) -> None:
        mesh, stats = generate_tri_mesh(step_face, "step")
        assert mesh.element_order == 1
        assert mesh.element_type == "tri3"
        assert mesh.midside is None
        assert stats["element_type"] == "tri3"
        assert stats["element_count"] == mesh.element_count

    def test_second_order_round_trips_as_tri6(self, step_face: Path) -> None:
        mesh, stats = generate_tri_mesh(step_face, "step", element_order=2)
        assert mesh.element_order == 2
        assert stats["element_type"] == "tri6"
        assert mesh.midside is not None
        assert mesh.connectivity.shape[1] == 6

    def test_the_order_does_not_change_the_area_it_covers(self, step_face: Path) -> None:
        linear, _ = generate_tri_mesh(step_face, "step", element_size_mm=5.0)
        quadratic, _ = generate_tri_mesh(step_face, "step", element_size_mm=5.0, element_order=2)
        assert quadratic.element_count == linear.element_count
        assert quadratic.area == pytest.approx(800.0, rel=1e-9)

    def test_every_midside_node_sits_at_its_edge_midpoint(self, step_face: Path) -> None:
        # The check that would catch a gmsh node-ordering change, which would
        # otherwise return a plausible and wrong plane stiffness matrix.
        mesh, _ = generate_tri_mesh(step_face, "step", element_size_mm=8.0, element_order=2)
        assert mesh.midside is not None
        corners = mesh.nodes[mesh.tris]
        for local, (a, b) in enumerate(TRI6_EDGES):
            expected = 0.5 * (corners[:, a] + corners[:, b])
            assert mesh.nodes[mesh.midside[:, local]] == pytest.approx(expected, abs=1e-9)

    def test_a_shared_edge_gets_one_midside_node_not_two(self, step_face: Path) -> None:
        mesh, _ = generate_tri_mesh(step_face, "step", element_size_mm=8.0, element_order=2)
        assert mesh.midside is not None
        assert mesh.midside.size > len(np.unique(mesh.midside))

    def test_every_node_is_used_by_an_element(self, step_face: Path) -> None:
        # Gmsh keeps entity nodes the mesh never references; left in, the solver
        # would read them as unconstrained free DOFs and go singular.
        mesh, _ = generate_tri_mesh(step_face, "step")
        assert len(np.unique(mesh.connectivity)) == mesh.node_count

    def test_smaller_elements_give_a_denser_mesh_of_the_same_face(self, step_face: Path) -> None:
        coarse, _ = generate_tri_mesh(step_face, "step", element_size_mm=10.0)
        fine, _ = generate_tri_mesh(step_face, "step", element_size_mm=3.0)
        assert fine.element_count > coarse.element_count
        assert fine.area == pytest.approx(coarse.area, rel=1e-9)

    def test_no_triangle_comes_back_inverted(self, step_face: Path) -> None:
        mesh, _ = generate_tri_mesh(step_face, "step")
        assert (mesh.signed_areas() > 0.0).all()


class TestPlanarMeshingRefusals:
    def test_an_unsupported_element_order_is_refused(self, step_face: Path) -> None:
        with pytest.raises(MeshError, match="element_order must be 1 or 2"):
            generate_tri_mesh(step_face, "step", element_order=3)

    def test_a_face_offset_from_the_plane_is_refused_by_name(self, tmp_path: Path) -> None:
        # gmsh returns a face where the file put it. Projecting it would move
        # the geometry, so the file has to be fixed instead.
        path = write_step_face(tmp_path / "offset.step", (40.0, 20.0), z=5.0)
        with pytest.raises(MeshError, match=r"must be authored in the z = 0 plane") as excinfo:
            generate_tri_mesh(path, "step")
        message = str(excinfo.value)
        assert "the plane z = 5 mm" in message, "the message must name where the face is"
        assert "5 mm off it" in message

    def test_a_face_in_another_plane_is_refused_by_name(self, tmp_path: Path) -> None:
        # This one would project to a line, which is the worse half of the trap.
        path = write_step_face(tmp_path / "rotated.step", (40.0, 20.0), in_the_x_zero_plane=True)
        with pytest.raises(MeshError, match=r"must be authored in the z = 0 plane") as excinfo:
            generate_tri_mesh(path, "step")
        assert "the plane x = " in str(excinfo.value)

    def test_a_solid_body_is_refused_before_its_boundary_is_meshed(self, tmp_path: Path) -> None:
        path = write_step_solid(tmp_path / "box.step", (10.0, 10.0, 10.0))
        with pytest.raises(MeshError, match="contains a solid body"):
            generate_tri_mesh(path, "step")

    def test_a_negative_element_size_is_refused(self, step_face: Path) -> None:
        with pytest.raises(MeshError, match="must be positive"):
            generate_tri_mesh(step_face, "step", element_size_mm=-1.0)

    def test_an_unreadable_file_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.step"
        path.write_bytes(b"not a step file at all")
        with pytest.raises(MeshError):
            generate_tri_mesh(path, "step")
