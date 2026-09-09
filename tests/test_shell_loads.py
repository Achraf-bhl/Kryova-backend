"""Loads on a shell — the distribution `app/solve/loads.py` could not do.

Offline and instant: no gmsh, no `ccx`, no database. Every mesh below is a flat
plate authored by hand, so its area, its tributary areas and the resultant of
every load on it are known in closed form rather than measured.

Every test is named after the wrong answer it prevents. The quiet ones are the
ones that matter here, because all four of them solve: an S8R face whose corners
were given a positive share, a load whose resultant drifts when the mesh is
refined, a body load that forgot the thickness, and a pressure on a dome
integrated as one resultant vector.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.mesh.structural import ShellMesh
from app.solve.shell_loads import (
    SHELL_SHAPE_INTEGRALS,
    assemble_shell_loads,
    distribute_force_over_shell,
    face_normals,
    shell_faces_within,
    shell_node_weights,
)
from app.solve.types import (
    BearingLoad,
    BoxSelector,
    CentrifugalLoad,
    CylinderSelector,
    ForceLoad,
    GravityLoad,
    MomentLoad,
    PressureLoad,
    SolverError,
)

WIDTH_MM = 120.0
HEIGHT_MM = 60.0
THICKNESS_MM = 2.5
DENSITY_KG_M3 = 7850.0
PLATE_AREA_MM2 = WIDTH_MM * HEIGHT_MM


def _plate(shape: str, order: int, nx: int = 3, ny: int = 2) -> ShellMesh:
    """A flat `WIDTH x HEIGHT` plate in z = 0, wound so its normal is +z.

    Built as an `nx` by `ny` grid so the same plate can be produced at two
    densities — which is what makes the mesh-independence tests below mean
    something — and quadratically promoted by deduplicating shared edges, so a
    midside node is one node rather than one per face that touches it.
    """
    xs = np.linspace(0.0, WIDTH_MM, nx + 1)
    ys = np.linspace(0.0, HEIGHT_MM, ny + 1)
    nodes = [[x, y, 0.0] for y in ys for x in xs]

    def corner(i: int, j: int) -> int:
        return j * (nx + 1) + i

    faces = []
    for j in range(ny):
        for i in range(nx):
            a, b, c, d = corner(i, j), corner(i + 1, j), corner(i + 1, j + 1), corner(i, j + 1)
            if shape == "quad":
                faces.append([a, b, c, d])
            else:
                faces.extend(([a, b, c], [a, c, d]))

    mesh = ShellMesh(nodes=np.array(nodes), faces=np.array(faces))
    if order == 1:
        return mesh
    return _promote(mesh)


def _promote(mesh: ShellMesh) -> ShellMesh:
    """Add one midside node per distinct edge, in `edge_table` order.

    Deduplicated on the sorted node pair, so two faces sharing an edge share its
    midside node. A promotion that did not would double the node count on the
    shared edges and leave the mesh disconnected there — it would still solve,
    and the plate would behave as a set of loose panels.
    """
    nodes = list(map(list, mesh.nodes))
    index: dict[tuple[int, int], int] = {}
    midside = []
    for face in mesh.faces:
        row = []
        for a, b in mesh.edge_table:
            key = (min(int(face[a]), int(face[b])), max(int(face[a]), int(face[b])))
            if key not in index:
                index[key] = len(nodes)
                nodes.append(list(0.5 * (mesh.nodes[key[0]] + mesh.nodes[key[1]])))
            row.append(index[key])
        midside.append(row)
    return ShellMesh(nodes=np.array(nodes), faces=mesh.faces, midside=np.array(midside))


ALL_KINDS = [("tri", 1), ("tri", 2), ("quad", 1), ("quad", 2)]
WHOLE_PLATE = BoxSelector(min=(-1.0, -1.0, -1.0), max=(WIDTH_MM + 1, HEIGHT_MM + 1, 1))


def _resultant(vector: np.ndarray) -> np.ndarray:
    """The net force of a flat DOF vector — three sums, one per direction."""
    return vector.reshape(-1, 3).sum(axis=0)


class TestTheShapeFunctionIntegralsAreConsistent:
    """The table is the whole of the physics; if a row is wrong everything is."""

    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_every_row_sums_to_one_over_the_faces_nodes(self, shape: str, order: int) -> None:
        mesh = _plate(shape, order)
        corners = 3 if shape == "tri" else 4
        corner_factor, midside_factor = SHELL_SHAPE_INTEGRALS[mesh.element_type]
        midsides = corners if order == 2 else 0
        assert corners * corner_factor + midsides * midside_factor == pytest.approx(1.0)

    def test_the_eight_node_quadrilaterals_corners_are_negative(self) -> None:
        """The trap this table exists for. A tributary-area intuition says every
        node takes a positive share; serendipity shape functions say otherwise,
        and a face loaded the plausible way solves and is wrong."""
        corner_factor, midside_factor = SHELL_SHAPE_INTEGRALS["quad8"]
        assert corner_factor == pytest.approx(-1.0 / 12.0)
        assert midside_factor == pytest.approx(1.0 / 3.0)

    def test_the_six_node_triangles_corners_carry_nothing(self) -> None:
        assert SHELL_SHAPE_INTEGRALS["tri6"] == (0.0, 1.0 / 3.0)

    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_the_node_weights_sum_to_the_plates_area(self, shape: str, order: int) -> None:
        """Weights are areas, so their total is the area — including the
        quad8 row, where four negative corners are cancelled by four
        over-weighted midside nodes."""
        weights = shell_node_weights(_plate(shape, order))
        assert weights.sum() == pytest.approx(PLATE_AREA_MM2)


class TestAForcesResultantIsTheForceThatWasAsked:
    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_the_whole_plate_delivers_the_requested_resultant(
        self, shape: str, order: int
    ) -> None:
        mesh = _plate(shape, order)
        force = (300.0, -120.0, 45.0)
        vector, warnings = assemble_shell_loads(
            mesh, [ForceLoad(where=WHOLE_PLATE, force_n=force)], THICKNESS_MM, DENSITY_KG_M3
        )
        assert warnings == []
        assert _resultant(vector) == pytest.approx(np.array(force))

    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_refining_the_mesh_does_not_move_the_load(self, shape: str, order: int) -> None:
        """The invariant the module exists for. An equal split between selected
        nodes would pass the resultant test above and fail this one."""
        coarse = _plate(shape, order, nx=3, ny=2)
        fine = _plate(shape, order, nx=9, ny=7)
        force = (0.0, 0.0, -1000.0)
        load = [ForceLoad(where=WHOLE_PLATE, force_n=force)]

        a, _ = assemble_shell_loads(coarse, load, THICKNESS_MM, DENSITY_KG_M3)
        b, _ = assemble_shell_loads(fine, load, THICKNESS_MM, DENSITY_KG_M3)
        assert _resultant(a) == pytest.approx(np.array(force))
        assert _resultant(b) == pytest.approx(np.array(force))

    def test_a_quadratic_triangle_puts_nothing_on_its_corners(self) -> None:
        """`tri6`'s corner factor is zero, so a load on the whole plate leaves
        every original grid node at exactly zero and sits on the midside nodes."""
        mesh = _plate("tri", 2)
        vector, _ = assemble_shell_loads(
            mesh,
            [ForceLoad(where=WHOLE_PLATE, force_n=(0.0, 0.0, -900.0))],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        per_node = vector.reshape(-1, 3)
        corners = np.unique(mesh.faces)
        assert per_node[corners] == pytest.approx(0.0)
        assert per_node[np.unique(mesh.midside)][:, 2].sum() == pytest.approx(-900.0)

    def test_a_quadratic_quadrilateral_pulls_its_corners_the_other_way(self) -> None:
        """The sign that looks like a bug and is not: the corners of an S8R face
        take -1/12 each, so a downward load pushes them *up*."""
        mesh = _plate("quad", 2)
        vector, _ = assemble_shell_loads(
            mesh,
            [ForceLoad(where=WHOLE_PLATE, force_n=(0.0, 0.0, -1200.0))],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        per_node = vector.reshape(-1, 3)
        corners = np.unique(mesh.faces)
        assert (per_node[corners][:, 2] > 0.0).all()
        assert per_node[corners][:, 2].sum() + per_node[np.unique(mesh.midside)][
            :, 2
        ].sum() == pytest.approx(-1200.0)

    def test_a_bigger_element_carries_more_of_the_load(self) -> None:
        """Area weighting, seen directly. The plate is split into two columns of
        unequal width; the shared interior nodes see the average of the two, so
        the outer edges are what carry the ratio."""
        nodes = np.array(
            [
                [0.0, 0.0, 0.0],
                [30.0, 0.0, 0.0],
                [120.0, 0.0, 0.0],
                [0.0, 60.0, 0.0],
                [30.0, 60.0, 0.0],
                [120.0, 60.0, 0.0],
            ]
        )
        mesh = ShellMesh(nodes=nodes, faces=np.array([[0, 1, 4, 3], [1, 2, 5, 4]]))
        weights = shell_node_weights(mesh)
        # Left face is 30 x 60, right is 90 x 60: a 1 : 3 area ratio.
        assert weights[0] / weights[2] == pytest.approx(1.0 / 3.0)

        # And the ratio survives the whole assembly path, which is the half that
        # matters: an equal split between the six nodes delivers the same
        # *resultant* as an area split, so every test above still passes under
        # it and only this one fails.
        vector, _ = assemble_shell_loads(
            mesh,
            [ForceLoad(where=WHOLE_PLATE, force_n=(0.0, 0.0, -7200.0))],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        per_node = vector.reshape(-1, 3)
        assert per_node[0][2] == pytest.approx(-450.0)  # a quarter of 30 x 60
        assert per_node[2][2] == pytest.approx(-1350.0)  # a quarter of 90 x 60

    def test_a_selection_with_no_whole_face_falls_back_and_says_so(self) -> None:
        """A band of nodes across the middle of the faces rather than a region
        covering any of them. Equal split is the only thing left, and silence
        would make it look like an area distribution."""
        mesh = _plate("quad", 1)
        sliver = BoxSelector(min=(-1.0, -1.0, -1.0), max=(1.0, HEIGHT_MM + 1, 1.0))
        nodes = np.array([0, 4], dtype=np.int64)  # one edge, no complete face
        _, _, warning = distribute_force_over_shell(mesh, nodes, np.array([0.0, 0.0, -10.0]))
        assert warning is not None
        assert "no complete shell faces" in warning

        vector, warnings = assemble_shell_loads(
            mesh,
            [ForceLoad(where=sliver, force_n=(0.0, 0.0, -10.0), name="edge pull")],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        assert any("edge pull" in w for w in warnings)
        assert _resultant(vector) == pytest.approx(np.array([0.0, 0.0, -10.0]))

    def test_a_selection_with_no_nodes_at_all_is_refused_by_name(self) -> None:
        mesh = _plate("quad", 1)
        with pytest.raises(SolverError, match="selects no nodes"):
            distribute_force_over_shell(
                mesh, np.array([], dtype=np.int64), np.array([0.0, 0.0, -1.0])
            )


class TestPressureActsOverRealArea:
    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_a_flat_plate_carries_pressure_times_area(self, shape: str, order: int) -> None:
        """The plate is wound normal-up, so a positive pressure pushes down."""
        mesh = _plate(shape, order)
        pressure = 0.4
        vector, _ = assemble_shell_loads(
            mesh,
            [PressureLoad(where=WHOLE_PLATE, pressure_mpa=pressure)],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        expected = np.array([0.0, 0.0, -pressure * PLATE_AREA_MM2])
        assert _resultant(vector) == pytest.approx(expected)

    def test_refining_the_mesh_does_not_change_the_pressure_load(self) -> None:
        load = [PressureLoad(where=WHOLE_PLATE, pressure_mpa=0.4)]
        coarse, _ = assemble_shell_loads(_plate("quad", 2, 2, 2), load, THICKNESS_MM, DENSITY_KG_M3)
        fine, _ = assemble_shell_loads(_plate("quad", 2, 11, 8), load, THICKNESS_MM, DENSITY_KG_M3)
        assert _resultant(coarse) == pytest.approx(_resultant(fine))

    def test_a_folded_surface_does_not_get_one_resultant_vector(self) -> None:
        """Two faces meeting at a right angle. Integrated face by face the
        resultant is the vector sum of two perpendicular pushes; treated as one
        vector over the total area it would be half as large again."""
        nodes = np.array(
            [
                [0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0],
                [10.0, 10.0, 0.0],
                [0.0, 10.0, 0.0],
                [20.0, 0.0, 10.0],
                [20.0, 10.0, 10.0],
            ]
        )
        # Second face is the first one folded up 45 degrees about the x = 10 edge.
        mesh = ShellMesh(nodes=nodes, faces=np.array([[0, 1, 2, 3], [1, 4, 5, 2]]))
        # A box tall enough to hold the folded-up face; `WHOLE_PLATE` is flat and
        # would silently select only the first face.
        both = BoxSelector(min=(-1.0, -1.0, -1.0), max=(21.0, 11.0, 11.0))
        vector, _ = assemble_shell_loads(
            mesh, [PressureLoad(where=both, pressure_mpa=1.0)], THICKNESS_MM, DENSITY_KG_M3
        )
        normals = face_normals(mesh)
        expected = -(mesh.face_areas()[:, None] * normals).sum(axis=0)
        assert _resultant(vector) == pytest.approx(expected)
        # And it is genuinely smaller than the scalar p*A the two faces total.
        assert np.linalg.norm(expected) < mesh.area_mm2

    def test_a_pressure_on_a_selection_with_no_whole_face_is_refused(self) -> None:
        mesh = _plate("quad", 1)
        sliver = BoxSelector(min=(-1.0, -1.0, -1.0), max=(1.0, HEIGHT_MM + 1, 1.0))
        with pytest.raises(SolverError, match="no complete shell face"):
            assemble_shell_loads(
                mesh,
                [PressureLoad(where=sliver, pressure_mpa=1.0)],
                THICKNESS_MM,
                DENSITY_KG_M3,
            )


class TestABodyLoadWeighsTheMaterialAFaceStandsFor:
    @pytest.mark.parametrize(("shape", "order"), ALL_KINDS)
    def test_gravity_is_density_times_area_times_thickness(self, shape: str, order: int) -> None:
        """A shell face has no volume; the thickness the mesh deliberately does
        not carry is what turns its area into mass. Dropping it would give a
        load 2.5 times too small here and would look like a units problem."""
        mesh = _plate(shape, order)
        g = 9810.0
        vector, _ = assemble_shell_loads(
            mesh,
            [GravityLoad(direction=(0.0, 0.0, -1.0), magnitude_mm_s2=g)],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        mass_tonne = DENSITY_KG_M3 * 1e-12 * PLATE_AREA_MM2 * THICKNESS_MM
        assert _resultant(vector) == pytest.approx(np.array([0.0, 0.0, -mass_tonne * g]))

    def test_doubling_the_thickness_doubles_the_weight(self) -> None:
        mesh = _plate("quad", 2)
        load = [GravityLoad(direction=(0.0, 0.0, -1.0), magnitude_mm_s2=9810.0)]
        thin, _ = assemble_shell_loads(mesh, load, THICKNESS_MM, DENSITY_KG_M3)
        thick, _ = assemble_shell_loads(mesh, load, 2.0 * THICKNESS_MM, DENSITY_KG_M3)
        assert _resultant(thick) == pytest.approx(2.0 * _resultant(thin))

    def test_a_zero_thickness_shell_is_refused_by_name(self) -> None:
        with pytest.raises(SolverError, match="encloses no material"):
            assemble_shell_loads(_plate("quad", 1), [], 0.0, DENSITY_KG_M3)

    def test_centrifugal_load_is_quadratic_in_speed(self) -> None:
        mesh = _plate("tri", 1)
        def spin(rpm: float) -> np.ndarray:
            vector, _ = assemble_shell_loads(
                mesh,
                [CentrifugalLoad(rpm=rpm, axis_point=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 1.0))],
                THICKNESS_MM,
                DENSITY_KG_M3,
            )
            return _resultant(vector)

        assert spin(2000.0) == pytest.approx(4.0 * spin(1000.0))


class TestTheLoadsAShellCannotCarryAreRefusedRatherThanApproximated:
    def test_a_bearing_load_is_refused_with_the_reason(self) -> None:
        """A shell's hole is an edge, not a bore wall, so the cosine
        distribution has no surface to be defined over."""
        mesh = _plate("quad", 1)
        bearing = BearingLoad(
            where=CylinderSelector(
                axis_point=(0.0, 0.0, 0.0), axis_direction=(0.0, 0.0, 1.0), radius=10.0
            ),
            force_n=(100.0, 0.0, 0.0),
        )
        with pytest.raises(SolverError, match="no bore wall"):
            assemble_shell_loads(mesh, [bearing], THICKNESS_MM, DENSITY_KG_M3)


class TestAMomentReproducesItself:
    def test_the_summed_cross_product_is_the_requested_moment(self) -> None:
        mesh = _plate("quad", 1)
        moment = np.array([0.0, 0.0, 5.0e4])
        vector, _ = assemble_shell_loads(
            mesh,
            [MomentLoad(where=WHOLE_PLATE, moment_n_mm=tuple(moment))],
            THICKNESS_MM,
            DENSITY_KG_M3,
        )
        forces = vector.reshape(-1, 3)
        centroid = mesh.nodes[np.unique(mesh.faces)].mean(axis=0)
        applied = np.cross(mesh.nodes - centroid, forces).sum(axis=0)
        assert applied == pytest.approx(moment)
        assert _resultant(vector) == pytest.approx(np.zeros(3), abs=1e-9)


class TestFaceSelectionIsByCornersOnly:
    def test_a_face_is_selected_when_its_corners_are(self) -> None:
        mesh = _plate("quad", 2)
        half = BoxSelector(min=(-1.0, -1.0, -1.0), max=(40.1, HEIGHT_MM + 1, 1.0))
        from app.solve.selection import select_nodes

        faces = shell_faces_within(mesh, select_nodes(mesh, half))
        # The first column of the 3 x 2 grid: one face per row, and the midside
        # nodes bulging outside the box do not stop either being selected.
        assert len(faces) == 2
        assert mesh.face_areas()[faces].sum() == pytest.approx(40.0 * HEIGHT_MM)
