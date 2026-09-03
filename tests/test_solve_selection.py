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
