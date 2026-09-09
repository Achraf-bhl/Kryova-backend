"""The shell mesher — the producer `app/mesh/structural.py` said did not exist.

Needs gmsh and OCCT, so it is slower than `test_mesh_structural.py` and skips
rather than fails where the kernel is absent. The surface every test meshes is a
**90 degree sector of a spherical zone, open at the pole**, built by revolving a
meridian arc: a curved surface in three dimensions, which is the shape
`generate_tri_mesh` exists to refuse. Its area is known in closed form, so "did
the mesher cover the surface" is a comparison against arithmetic rather than
against another mesh.

**It is not NAFEMS LE3's shape, and this docstring said it was until the
geometry was actually sourced.** LE3's hemisphere is *closed at the pole* —
point E is the pole itself — and the widely repeated "hemisphere with an 18 degree
hole" belongs to a different benchmark. See `docs/nafems-le3-geometry.md` §1.5,
which settles it from four independent sources including node coordinates. The
hole is kept here because it makes a better *mesher* fixture: it gives the zone
a second boundary, so `_refuse_a_closed_surface` has something to not fire on
and the exact-area arithmetic stays a one-line formula.

Every test is named after the wrong answer it prevents. Two of them are the
quiet kind: a quadratic element whose midside nodes arrive in gmsh's order
rather than CalculiX's, which solves and reports a wrong stiffness, and a
half-recombined mesh whose triangles are dropped, which reports a plausible
area for a surface with holes in it.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.kernel.occt.binding import available, require, symbol
from app.mesh.gmsh_mesher import generate_shell_mesh, generate_tri_mesh
from app.mesh.structural import ShellMesh, shell_quality
from app.mesh.types import MeshError

needs_kernel = pytest.mark.skipif(
    not available(), reason="OCCT (cadquery-ocp) is not installed in this environment"
)

RADIUS_MM = 10_000.0
HOLE_HALF_ANGLE_DEG = 18.0
SWEEP_DEG = 90.0

#: Exact area of the swept spherical zone, from the hole edge to the equator:
#: `sweep * R^2 * (cos(theta0) - cos(90 deg))`. Written out rather than measured,
#: because a mesh that chords the surface converges to this from *below* and a
#: mesh compared only against another mesh cannot tell that from converging to
#: the wrong number.
EXACT_AREA_MM2 = math.radians(SWEEP_DEG) * RADIUS_MM**2 * math.cos(math.radians(HOLE_HALF_ANGLE_DEG))

ALL_KINDS = [("tri", 1), ("tri", 2), ("quad", 1), ("quad", 2)]
EXPECTED_TYPE = {("tri", 1): "tri3", ("tri", 2): "tri6", ("quad", 1): "quad4", ("quad", 2): "quad8"}


def _hemisphere_shell(sweep_deg: float = SWEEP_DEG) -> Any:
    """The meridian arc from the hole edge to the equator, revolved about z.

    Revolving a **wire** gives a shell; revolving a face would give a solid, and
    that one-word difference is the whole of what makes this a shell model. The
    arc stopping short of the pole *is* the hole.
    """
    require()
    theta0 = math.radians(HOLE_HALF_ANGLE_DEG)

    def point(theta: float) -> Any:
        return symbol("gp_Pnt")(
            RADIUS_MM * math.sin(theta), 0.0, RADIUS_MM * math.cos(theta)
        )

    # Three-point arc form, for the reason `le11_geometry` documents: the
    # `gp_Circ` overload returns the major arc for both senses on this build.
    edge = symbol("BRepBuilderAPI_MakeEdge")(
        symbol("GC_MakeArcOfCircle")(
            point(theta0), point(0.5 * (theta0 + math.pi / 2.0)), point(math.pi / 2.0)
        ).Value()
    ).Edge()
    wire = symbol("BRepBuilderAPI_MakeWire")(edge).Wire()
    axis = symbol("gp_Ax1")(symbol("gp_Pnt")(0.0, 0.0, 0.0), symbol("gp_Dir")(0.0, 0.0, 1.0))
    return symbol("BRepPrimAPI_MakeRevol")(wire, axis, math.radians(sweep_deg)).Shape()


def _box_stl(dx: float, dy: float, dz: float) -> str:
    """A watertight box as ASCII STL — twelve triangles, no kernel needed.

    Written out rather than exported so the test does not depend on OCCT having
    an STL writer bound, and so that "this file is closed" is a property of the
    fixture rather than of a mesher setting somewhere else.
    """
    c = [
        (0.0, 0.0, 0.0),
        (dx, 0.0, 0.0),
        (dx, dy, 0.0),
        (0.0, dy, 0.0),
        (0.0, 0.0, dz),
        (dx, 0.0, dz),
        (dx, dy, dz),
        (0.0, dy, dz),
    ]
    quads = [
        (0, 3, 2, 1),  # bottom
        (4, 5, 6, 7),  # top
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    out = ["solid box"]
    for a, b, d, e in quads:
        for tri in ((a, b, d), (a, d, e)):
            out.append("facet normal 0 0 0")
            out.append("  outer loop")
            out.extend(f"    vertex {c[i][0]:g} {c[i][1]:g} {c[i][2]:g}" for i in tri)
            out.append("  endloop")
            out.append("endfacet")
    out.append("endsolid box")
    return "\n".join(out) + "\n"


@pytest.fixture(scope="module")
def hemisphere_step() -> Any:
    """The hemisphere written to STEP once, and meshed from that file.

    Once rather than per test, for `run_le1`'s reason: building the geometry
    inside the loop would make a geometry change indistinguishable from a mesh
    change.
    """
    from app.manufacture.export import write_step

    with tempfile.TemporaryDirectory(prefix="shell-mesh-") as workspace:
        path = Path(workspace) / "hemisphere.step"
        write_step(_hemisphere_shell(), path)
        yield path


@needs_kernel
class TestItMeshesACurvedSurfaceAtAll:
    """The thing `generate_tri_mesh` refuses, which is why this exists."""

    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_every_element_type_covers_the_hemisphere(
        self, hemisphere_step: Path, shape: str, order: int
    ) -> None:
        mesh, stats = generate_shell_mesh(
            hemisphere_step, "step", element_size_mm=900.0, element_order=order, face_shape=shape
        )
        assert isinstance(mesh, ShellMesh)
        assert mesh.element_type == EXPECTED_TYPE[(shape, order)]
        assert stats["mesher"] == "gmsh"
        assert stats["element_type"] == mesh.element_type
        # Straight-edged elements chord the sphere, so the mesh is slightly
        # *smaller* than the true surface — never larger.
        assert mesh.area_mm2 < EXACT_AREA_MM2
        assert mesh.area_mm2 == pytest.approx(EXACT_AREA_MM2, rel=0.01)

    def test_the_same_surface_is_refused_by_the_plane_mesher(
        self, hemisphere_step: Path
    ) -> None:
        """The two meshers are not interchangeable, and this is the difference:
        a plane model is a cross-section and must lie in z = 0, a shell is a
        surface in space. If this ever stops raising, the shell path has been
        given the plane path's assumptions."""
        with pytest.raises(MeshError, match="z = 0"):
            generate_tri_mesh(hemisphere_step, "step", element_size_mm=900.0)

    def test_refining_moves_the_area_towards_the_exact_one(
        self, hemisphere_step: Path
    ) -> None:
        """Convergence from below. A mesher that had subtly the wrong surface
        would converge just as smoothly onto a different number."""
        coarse, _ = generate_shell_mesh(hemisphere_step, "step", element_size_mm=2000.0)
        fine, _ = generate_shell_mesh(hemisphere_step, "step", element_size_mm=600.0)
        assert coarse.area_mm2 < fine.area_mm2 < EXACT_AREA_MM2
        assert fine.face_count > coarse.face_count


@needs_kernel
class TestTheMidsideNodesAreInCalculixOrder:
    """`app/mesh/structural.py` adopted CalculiX's midside order and named the
    debt: a gmsh mesher must permute at the boundary. Measured, the permutation
    is the identity — and these are what keep that a checked fact."""

    @pytest.mark.parametrize("shape", ["tri", "quad"])
    def test_each_midside_node_sits_between_the_corners_its_slot_names(
        self, hemisphere_step: Path, shape: str
    ) -> None:
        mesh, _ = generate_shell_mesh(
            hemisphere_step, "step", element_size_mm=900.0, element_order=2, face_shape=shape
        )
        assert mesh.midside is not None
        corners = mesh.nodes[mesh.faces]
        expected = np.stack(
            [0.5 * (corners[:, a] + corners[:, b]) for a, b in mesh.edge_table], axis=1
        )
        assert mesh.nodes[mesh.midside] == pytest.approx(expected)

    def test_a_quadratic_quadrilateral_has_eight_nodes_not_nine(
        self, hemisphere_step: Path
    ) -> None:
        """Without `Mesh.SecondOrderIncomplete` gmsh writes a 9-node quad with a
        bubble node at the centre. CalculiX's S8R is the 8-node element, so the
        ninth would be a column `ShellMesh` refuses or, worse, silently dropped
        — leaving a node in the deck that no element references."""
        mesh, _ = generate_shell_mesh(
            hemisphere_step, "step", element_size_mm=900.0, element_order=2, face_shape="quad"
        )
        assert mesh.connectivity.shape[1] == 8
        # Every node the mesh carries is referenced by an element; a bubble node
        # left behind would show up here as an unreferenced index.
        assert len(np.unique(mesh.connectivity)) == mesh.node_count

    def test_a_swapped_midside_slot_is_caught_far_from_the_origin_too(self) -> None:
        """`np.allclose`'s default `rtol=1e-5` is relative to the absolute
        coordinate, so on a part exported in assembly coordinates the tolerance
        grows with the part's position and swallows the swap. Detected at x = 0
        and at x = 1e3, and silently accepted at x = 1e5, until `rtol=0`."""
        from app.mesh.gmsh_mesher import _assert_shell_midside_ordering

        for offset in (0.0, 1.0e3, 1.0e5, 1.0e7):
            nodes = np.array(
                [
                    [0.0, 0.0, 0.0],
                    [1.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.5, 0.0, 0.0],
                    [0.5, 0.5, 0.0],
                    [0.0, 0.5, 0.0],
                ]
            ) + np.array([offset, 0.0, 0.0])
            # Slots 1 and 2 exchanged: still six nodes, still all in range.
            mesh = ShellMesh(
                nodes=nodes, faces=np.array([[0, 1, 2]]), midside=np.array([[3, 5, 4]])
            )
            with pytest.raises(MeshError, match="does not match"):
                _assert_shell_midside_ordering(mesh)

    def test_a_midside_node_lies_off_the_sphere_because_the_edges_are_straight(
        self, hemisphere_step: Path
    ) -> None:
        """`Mesh.SecondOrderLinear` is what makes the check above a coordinate
        comparison. It is visible as geometry: corners sit exactly on the sphere
        and midside nodes sit slightly inside it, on the chord."""
        mesh, _ = generate_shell_mesh(
            hemisphere_step, "step", element_size_mm=900.0, element_order=2
        )
        assert mesh.midside is not None
        corner_radius = np.linalg.norm(mesh.nodes[np.unique(mesh.faces)], axis=1)
        midside_radius = np.linalg.norm(mesh.nodes[np.unique(mesh.midside)], axis=1)
        assert corner_radius == pytest.approx(RADIUS_MM, rel=1e-6)
        assert (midside_radius < RADIUS_MM).all()


@needs_kernel
class TestItRefusesWhatItCannotHonestlyMesh:
    def test_a_solid_is_refused_rather_than_idealised(self) -> None:
        """Meshing the boundary of a solid would succeed and hand back a hollow
        shell of the caller's chosen thickness — a different structure that
        solves and reports numbers."""
        from app.manufacture.export import write_step

        require()
        box = symbol("BRepPrimAPI_MakeBox")(50.0, 40.0, 30.0).Shape()
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "box.step"
            write_step(box, path)
            with pytest.raises(MeshError, match="contains a solid body"):
                generate_shell_mesh(path, "step", element_size_mm=10.0)

    def test_a_solid_exported_as_stl_is_refused_too(self) -> None:
        """The hole the topology check cannot see. An STL is a bag of triangles,
        so `getEntities(3)` is empty for a watertight solid and the STEP branch
        is skipped for STL anyway — this box went straight through and reported
        an area of exactly 6200 mm^2, the closed boundary, until the closed-
        surface guard was added."""
        with tempfile.TemporaryDirectory() as workspace:
            path = Path(workspace) / "box.stl"
            path.write_text(_box_stl(50.0, 30.0, 20.0), encoding="utf-8")
            with pytest.raises(MeshError, match="surface is closed"):
                generate_shell_mesh(path, "stl", element_size_mm=10.0)

    def test_an_open_surface_is_not_mistaken_for_a_closed_one(
        self, hemisphere_step: Path
    ) -> None:
        """The guard above must not fire on the hemisphere, whose hole and two
        sweep edges give it a boundary."""
        mesh, _ = generate_shell_mesh(hemisphere_step, "step", element_size_mm=900.0)
        assert mesh.face_count > 0

    def test_an_unknown_element_order_is_refused(self, hemisphere_step: Path) -> None:
        with pytest.raises(MeshError, match="element_order must be 1 or 2"):
            generate_shell_mesh(hemisphere_step, "step", element_order=3)

    def test_an_unknown_face_shape_is_refused(self, hemisphere_step: Path) -> None:
        with pytest.raises(MeshError, match="face_shape must be"):
            generate_shell_mesh(hemisphere_step, "step", face_shape="hexagon")

    def test_a_negative_element_size_is_refused(self, hemisphere_step: Path) -> None:
        with pytest.raises(MeshError, match="element_size_mm must be positive"):
            generate_shell_mesh(hemisphere_step, "step", element_size_mm=-1.0)


class _FakeMesh:
    """The two gmsh calls `_extract_shell` makes, and nothing else."""

    def __init__(self, nodes: np.ndarray, elements: dict[int, list[list[int]]]) -> None:
        self._nodes = nodes
        self._elements = elements

    def getNodes(self) -> tuple[list[int], list[float], None]:  # noqa: N802 - gmsh's name
        return list(range(1, len(self._nodes) + 1)), list(self._nodes.ravel()), None

    def getElements(self, dim: int) -> tuple[list[int], list[Any], list[list[int]]]:  # noqa: N802
        kinds = sorted(self._elements)
        return (
            kinds,
            [[] for _ in kinds],
            [[n for row in self._elements[k] for n in row] for k in kinds],
        )


class _FakeGmsh:
    def __init__(self, nodes: np.ndarray, elements: dict[int, list[list[int]]]) -> None:
        self.model = type("_Model", (), {"mesh": _FakeMesh(nodes, elements)})()


class TestAHalfRecombinedMeshIsRefusedRatherThanTrimmed:
    """Recombination is a request, not a guarantee. The hemisphere above
    recombines cleanly every time, so the guard has to be driven directly — and
    it is the guard that matters most here, because the failure it prevents is
    a mesh with holes in it that still reports a plausible area."""

    #: One quadrilateral and one triangle sharing an edge — what gmsh returns
    #: when it recombines part of a surface and gives up on the rest.
    NODES = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            [2.0, 0.5, 0.0],
        ]
    )
    MIXED = {3: [[1, 2, 3, 4]], 2: [[2, 5, 3]]}

    def test_keeping_only_the_quadrilaterals_is_refused_by_name(self) -> None:
        from app.mesh.gmsh_mesher import _extract_shell

        with pytest.raises(MeshError, match="holes in it"):
            _extract_shell(_FakeGmsh(self.NODES, self.MIXED), 1, "quad")

    def test_it_names_both_counts_so_the_reader_can_see_the_split(self) -> None:
        from app.mesh.gmsh_mesher import _extract_shell

        with pytest.raises(MeshError) as caught:
            _extract_shell(_FakeGmsh(self.NODES, self.MIXED), 1, "quad")
        assert "1 4-node quadrilaterals" in str(caught.value)
        assert "1 3-node triangles" in str(caught.value)

    def test_a_clean_single_type_mesh_still_passes_through(self) -> None:
        from app.mesh.gmsh_mesher import _extract_shell

        mesh = _extract_shell(_FakeGmsh(self.NODES, {3: [[1, 2, 3, 4]]}), 1, "quad")
        assert mesh.element_type == "quad4"
        assert mesh.area_mm2 == pytest.approx(1.0)

    def test_asking_for_a_type_the_mesher_did_not_produce_names_what_came_back(self) -> None:
        from app.mesh.gmsh_mesher import _extract_shell

        with pytest.raises(MeshError) as caught:
            _extract_shell(_FakeGmsh(self.NODES, {2: [[1, 2, 3]]}), 1, "quad")
        assert "no 4-node quadrilaterals" in str(caught.value)
        assert "did produce 1 3-node triangles" in str(caught.value)

    def test_a_nine_node_quadrilateral_is_named_rather_than_skipped(self) -> None:
        """What gmsh returns when `Mesh.SecondOrderIncomplete` is off. Left
        unrecognised it would be skipped as an unknown type and reported as
        "recombination failed, try triangles" — the opposite end of the function
        from the actual cause."""
        from app.mesh.gmsh_mesher import _extract_shell

        nodes = np.vstack([self.NODES, [[0.5, 0.5, 0.0], [0.5, 0.0, 0.0]]])
        with pytest.raises(MeshError) as caught:
            _extract_shell(
                _FakeGmsh(nodes, {10: [[1, 2, 3, 4, 5, 6, 7, 1, 2]]}), 2, "quad"
            )
        assert "9-node quadrilateral" in str(caught.value)


class TestTheQualitySummaryReadsOneForAPerfectElement:
    """Pure — no gmsh, no kernel. The two constants in `shell_quality` are the
    whole of what makes a triangular and a quadrilateral mesh comparable."""

    def test_an_equilateral_triangle_reads_one(self) -> None:
        side = 12.0
        height = side * math.sqrt(3.0) / 2.0
        mesh = ShellMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [side, 0.0, 0.0], [side / 2, height, 0.0]]),
            faces=np.array([[0, 1, 2]]),
        )
        assert shell_quality(mesh)["min_quality"] == pytest.approx(1.0)

    def test_a_square_reads_one(self) -> None:
        mesh = ShellMesh(
            nodes=np.array(
                [[0.0, 0.0, 0.0], [7.0, 0.0, 0.0], [7.0, 7.0, 0.0], [0.0, 7.0, 0.0]]
            ),
            faces=np.array([[0, 1, 2, 3]]),
        )
        assert shell_quality(mesh)["min_quality"] == pytest.approx(1.0)

    def test_a_sliver_reads_near_zero(self) -> None:
        mesh = ShellMesh(
            nodes=np.array([[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [50.0, 0.05, 0.0]]),
            faces=np.array([[0, 1, 2]]),
        )
        assert shell_quality(mesh)["min_quality"] < 0.01

    def test_the_summary_reports_the_worst_element_not_the_first(self) -> None:
        """`min`, not `[0]`. A mesh whose one bad element is not the first would
        otherwise be reported as clean."""
        nodes = np.array(
            [
                [0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0],
                [5.0, 8.66, 0.0],
                [15.0, 0.01, 0.0],
            ]
        )
        mesh = ShellMesh(nodes=nodes, faces=np.array([[0, 1, 2], [0, 1, 3]]))
        stats = shell_quality(mesh)
        assert stats["min_quality"] < 0.01
        assert stats["mean_quality"] > stats["min_quality"]
