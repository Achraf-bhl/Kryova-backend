"""Region selection: the sphere, the body, and the failures the others report.

Region selection is by geometric selector and never by face id, because face ids
are meaningless across a re-export. That makes the selectors themselves
load-bearing — a selector that picks the wrong nodes applies the load somewhere
else and the solve succeeds, reporting a stress for a part nobody designed.

`test_solver_loads.py` already drives the face, box and cylinder selectors
through real load cases. What it never reaches is the sphere selector, the body
selector, and three of the refusals — so those are here, tested directly against
a mesh whose node positions are known exactly.

Offline by construction: `box_mesh` is an exact primitive, so no gmsh, no
database, sub-second.
"""

import numpy as np
import pytest

from app.mesh.primitives import box_mesh
from app.solve.selection import radial_offsets, select_nodes
from app.solve.types import (
    BodySelector,
    BoxSelector,
    CylinderSelector,
    FaceSelector,
    SolverError,
    SphereSelector,
)

#: A 20x20x20 cube with nodes on a 5 mm lattice, so every expected count below
#: can be worked out by hand rather than read off a previous run.
SIDE = 20.0
DIVISIONS = 4


@pytest.fixture
def cube():
    return box_mesh((SIDE, SIDE, SIDE), divisions=(DIVISIONS, DIVISIONS, DIVISIONS))


class TestSphereSelector:
    def test_it_takes_the_nodes_inside_the_sphere(self, cube) -> None:
        selected = select_nodes(cube, SphereSelector(centre=(0.0, 0.0, 0.0), radius=6.0))
        distances = np.linalg.norm(cube.nodes[selected], axis=1)
        assert selected.size
        assert float(distances.max()) <= 6.0

    def test_the_boundary_is_inclusive(self, cube) -> None:
        """A node exactly on the radius is in. A node at 5 mm on the axis must
        be selected by `radius=5`, or naming a round number never works."""
        selected = select_nodes(cube, SphereSelector(centre=(0.0, 0.0, 0.0), radius=5.0))
        picked = {tuple(row) for row in cube.nodes[selected]}
        assert (5.0, 0.0, 0.0) in picked

    def test_a_sphere_that_catches_nothing_says_where_it_was(self, cube) -> None:
        with pytest.raises(SolverError, match="sphere"):
            select_nodes(cube, SphereSelector(centre=(500.0, 500.0, 500.0), radius=1.0))

    def test_a_sphere_around_a_corner_takes_only_that_corner(self, cube) -> None:
        selected = select_nodes(cube, SphereSelector(centre=(0.0, 0.0, 0.0), radius=1.0))
        assert selected.size == 1
        assert tuple(cube.nodes[selected][0]) == (0.0, 0.0, 0.0)


class TestBodySelector:
    def test_it_takes_every_node(self, cube) -> None:
        """Gravity and centrifugal loads act on all the material, not a surface."""
        selected = select_nodes(cube, BodySelector())
        assert selected.size == cube.node_count
        assert np.array_equal(selected, np.arange(cube.node_count))

    def test_it_cannot_be_empty_for_a_mesh_that_exists(self, cube) -> None:
        """So it skips the emptiness check by construction rather than by
        exception — which is why it returns early in `select_nodes`."""
        tiny = box_mesh((1.0, 1.0, 1.0), divisions=(1, 1, 1))
        assert select_nodes(tiny, BodySelector()).size == tiny.node_count


class TestBoxSelector:
    def test_an_inverted_box_is_refused_rather_than_selecting_nothing(self, cube) -> None:
        """"Matched no nodes" would send the author looking at the mesh. The
        real fault is that the two corners are the wrong way round."""
        with pytest.raises(SolverError, match="below its min corner"):
            select_nodes(cube, BoxSelector(min=(10.0, 10.0, 10.0), max=(0.0, 0.0, 0.0)))

    def test_the_bounds_are_inclusive(self, cube) -> None:
        selected = select_nodes(cube, BoxSelector(min=(0.0, 0.0, 0.0), max=(0.0, 0.0, 0.0)))
        assert selected.size == 1


class TestCylinderSelector:
    def test_length_clips_the_selection_along_the_axis(self, cube) -> None:
        """One hole in a stack of them has to be nameable on its own."""
        full = select_nodes(
            cube,
            CylinderSelector(
                axis_point=(10.0, 10.0, 0.0),
                axis_direction=(0.0, 0.0, 1.0),
                radius=10.0,
                radius_tolerance=0.5,
            ),
        )
        clipped = select_nodes(
            cube,
            CylinderSelector(
                axis_point=(10.0, 10.0, 0.0),
                axis_direction=(0.0, 0.0, 1.0),
                radius=10.0,
                radius_tolerance=0.5,
                length=5.0,
            ),
        )
        assert clipped.size < full.size
        assert float(cube.nodes[clipped][:, 2].max()) <= 5.0

    def test_it_is_a_band_and_not_a_solid_disc(self, cube) -> None:
        """A bolt hole is selected by naming its radius; the material outside it
        must not come along."""
        selected = select_nodes(
            cube,
            CylinderSelector(
                axis_point=(10.0, 10.0, 0.0),
                axis_direction=(0.0, 0.0, 1.0),
                radius=10.0,
                radius_tolerance=0.5,
            ),
        )
        offsets, _ = radial_offsets(cube.nodes[selected], (10.0, 10.0, 0.0), (0.0, 0.0, 1.0))
        radii = np.linalg.norm(offsets, axis=1)
        assert float(radii.min()) >= 9.5

    def test_a_zero_direction_says_what_to_do_about_it(self, cube) -> None:
        with pytest.raises(SolverError, match="at least one non-zero component"):
            select_nodes(
                cube,
                CylinderSelector(
                    axis_point=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 0.0), radius=5.0
                ),
            )


class TestFaceSelectorOnADegenerateSpan:
    def test_a_flat_part_falls_back_to_the_diagonal_for_its_tolerance(self) -> None:
        """A zero span on one axis would collapse the band to an exact
        comparison, and floating-point node coordinates do not compare exactly.

        `box_mesh` cannot make a zero-thickness part, so the fallback is driven
        directly: a mesh whose z extent is zero after being flattened.
        """
        flat = box_mesh((20.0, 20.0, 20.0), divisions=(2, 2, 2))
        flat.nodes[:, 2] = 0.0

        selected = select_nodes(flat, FaceSelector(axis="z", side="min"))

        assert selected.size == flat.node_count


class TestRadialOffsets:
    def test_it_splits_a_position_into_perpendicular_and_along(self) -> None:
        """Shared by the cylinder selector, the moment load and the centrifugal
        load — all three need exactly this decomposition."""
        nodes = np.array([[3.0, 4.0, 7.0]], dtype=np.float64)
        perpendicular, along = radial_offsets(nodes, (0.0, 0.0, 0.0), (0.0, 0.0, 2.0))
        assert float(along[0]) == pytest.approx(7.0)
        assert float(np.linalg.norm(perpendicular[0])) == pytest.approx(5.0)

    def test_the_direction_need_not_be_normalised(self) -> None:
        nodes = np.array([[3.0, 4.0, 7.0]], dtype=np.float64)
        one, _ = radial_offsets(nodes, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        many, _ = radial_offsets(nodes, (0.0, 0.0, 0.0), (0.0, 0.0, 100.0))
        assert np.allclose(one, many)


class TestTheEllipticalWall:
    """The selector NAFEMS LE10 could not be posed without.

    A curved outer edge is not any region the other five can name: a box takes
    the material inside it, a face selector takes a flat plane the wall is not,
    and a cylinder is the wrong shape. LE10 restrains that edge in x and y and
    restrains `uz` on one ring of it — two different regions of the same wall —
    so `length` is load-bearing rather than a convenience.
    """

    @pytest.fixture
    def wall_mesh(self):
        """Nodes placed by hand on and off a 30 x 10 ellipse, extruded in z.

        Built rather than meshed so every expected answer is arithmetic: two
        nodes exactly on the wall at each height, one well inside, one outside.
        """
        from app.mesh.types import TetMesh

        points = []
        for z in (0.0, 5.0, 10.0):
            points.extend(
                [
                    [30.0, 0.0, z],  # on the wall, at the flat end
                    [0.0, 10.0, z],  # on the wall, at the sharp end
                    [15.0, 0.0, z],  # halfway in
                    [45.0, 0.0, z],  # outside
                ]
            )
        nodes = np.array(points, dtype=np.float64)
        # One tet, only so the object is a mesh; selection reads nodes alone.
        return TetMesh(nodes=nodes, tets=np.array([[0, 1, 2, 4]], dtype=np.int64))

    def _wall(self, **overrides):
        from app.solve.types import EllipticalWallSelector

        return EllipticalWallSelector(
            **{
                "axis": "z",
                "axis_point": (0.0, 0.0, 0.0),
                "semi_axis_a": 30.0,
                "semi_axis_b": 10.0,
                **overrides,
            }
        )

    def test_it_takes_the_wall_at_both_ends_of_the_ellipse(self, wall_mesh) -> None:
        """The point of testing both: a band on *distance* would reach 30 mm
        into the material at the flat end while missing the sharp one. The test
        is on the normalised radius, which is 1 all the way round."""
        selected = set(int(index) for index in select_nodes(wall_mesh, self._wall()))

        on_the_wall = {
            index
            for index, node in enumerate(wall_mesh.nodes)
            if abs((node[0] / 30.0) ** 2 + (node[1] / 10.0) ** 2 - 1.0) < 1e-9
        }
        assert selected == on_the_wall
        assert len(selected) == 6

    def test_it_leaves_the_material_inside_and_outside_alone(self, wall_mesh) -> None:
        selected = select_nodes(wall_mesh, self._wall())
        radii = np.sqrt(
            (wall_mesh.nodes[selected, 0] / 30.0) ** 2
            + (wall_mesh.nodes[selected, 1] / 10.0) ** 2
        )
        assert np.allclose(radii, 1.0, atol=0.02)

    def test_length_clips_it_to_one_ring(self, wall_mesh) -> None:
        """LE10's line EE' is the midplane ring of the outer wall, and it is the
        only thing holding the plate up in z."""
        selected = select_nodes(
            wall_mesh, self._wall(axis_point=(0.0, 0.0, 4.0), length=2.0)
        )
        assert sorted(wall_mesh.nodes[selected, 2]) == [5.0, 5.0]

    def test_it_reads_the_ellipse_in_the_plane_normal_to_its_axis(self) -> None:
        """`axis` is the sweep direction, and the two semi-axes are measured
        along the remaining coordinate axes in x, y, z order. Swapping them is
        the mistake this pins."""
        from app.mesh.types import TetMesh

        nodes = np.array([[7.0, 30.0, 0.0], [7.0, 0.0, 10.0]], dtype=np.float64)
        mesh = TetMesh(
            nodes=np.vstack([nodes, [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]]),
            tets=np.array([[0, 1, 2, 3]], dtype=np.int64),
        )
        selected = select_nodes(
            mesh,
            self._wall(axis="x", axis_point=(0.0, 0.0, 0.0), semi_axis_a=30.0, semi_axis_b=10.0),
        )
        assert sorted(int(i) for i in selected) == [0, 1]

    def test_a_wall_that_is_not_there_is_refused_rather_than_returning_nothing(
        self, wall_mesh
    ) -> None:
        with pytest.raises(SolverError, match="elliptical wall"):
            select_nodes(wall_mesh, self._wall(semi_axis_a=500.0, semi_axis_b=400.0))

    def test_the_tolerance_is_on_the_normalised_radius_not_on_a_distance(self) -> None:
        """One number has to mean the same thing at both ends of the ellipse.

        These two nodes sit at the same *proportional* depth — 80% of the way
        out — and at very different distances from the wall: 6 mm at the flat
        end, 2 mm at the sharp one. A band on distance would take one and not
        the other, quietly stiffening the part along half its edge. A band on
        the normalised radius takes both or neither, and this asserts both
        directions so a tolerance that happened to be generous cannot pass it.
        """
        from app.mesh.types import TetMesh

        mesh = TetMesh(
            nodes=np.array(
                [[24.0, 0.0, 0.0], [0.0, 8.0, 0.0], [30.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
                dtype=np.float64,
            ),
            tets=np.array([[0, 1, 2, 3]], dtype=np.int64),
        )

        both = set(int(i) for i in select_nodes(mesh, self._wall(tolerance=0.25)))
        assert {0, 1} <= both

        neither = set(int(i) for i in select_nodes(mesh, self._wall(tolerance=0.15)))
        assert neither.isdisjoint({0, 1})
