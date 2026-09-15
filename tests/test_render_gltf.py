"""A shape as a display mesh, and a machine as one GLB (P6.1).

Tessellation runs on the open kernel; the GLB half is checked by reading the bytes back against
the glTF 2.0 specification's own rules, not against a library that might share a mistake.

**Written on Linux on 2026-09-15 and not run there as pytest**, at the user's instruction that the
Windows machine runs the tests. The box volume, the forty-bolt instance count, the untouched
caller shape and the location offset were checked by a one-off script against this OCP build.
"""

from __future__ import annotations

import json
import math
import struct

import numpy as np
import pytest

from app.assembly.placement import at, turned
from app.assembly.structure import Component, Instance, ProductStructure
from app.dynamics.pose import Frame
from app.kernel.errors import KernelError
from app.kernel.occt.binding import symbol
from app.kernel.occt.tessellate import TriangleMesh, levels_of_detail, tessellate
from app.kernel.occt.topology import FACE, explore_oriented
from app.render.gltf import (
    ARRAY_BUFFER,
    CHUNK_BIN,
    CHUNK_JSON,
    ELEMENT_ARRAY_BUFFER,
    FLOAT,
    GLB_MAGIC,
    ROOT_MATRIX,
    UNSIGNED_INT,
    GltfError,
    Placement,
    Scene,
    cache_key,
    instance_counts,
    mesh_arrays,
    part_scene,
    read_glb,
    scene_for,
    write_glb,
)


def _box(dx: float = 10.0, dy: float = 20.0, dz: float = 30.0) -> object:
    return symbol("BRepPrimAPI_MakeBox")(dx, dy, dz).Shape()


def _cylinder(radius: float = 5.0, height: float = 10.0) -> object:
    return symbol("BRepPrimAPI_MakeCylinder")(radius, height).Shape()


def _determinant(matrix: list[float]) -> float:
    m = np.array(matrix, dtype=float).reshape(4, 4, order="F")
    return float(np.linalg.det(m[:3, :3]))


class TestTheTrianglesAreTheSolid:
    def test_a_box_encloses_its_volume_so_every_triangle_faces_out(self) -> None:
        mesh = tessellate(_box(), linear_deflection_mm=0.1)
        assert mesh.signed_volume_mm3 == pytest.approx(6000.0, rel=1e-12)
        assert mesh.triangle_count == 12

    def test_a_reversed_face_is_rewound(self) -> None:
        # A box's six faces include REVERSED ones; without the swap the signed volume is not
        # the volume. Asserted here so the test says why the box test is a winding test.
        reversed_ = symbol("TopAbs_Orientation").TopAbs_REVERSED
        orientations = {face.Orientation() for face in explore_oriented(_box(), FACE)}
        assert reversed_ in orientations

    def test_a_curved_part_is_inscribed_and_converges_as_the_deflection_shrinks(self) -> None:
        exact = math.pi * 5.0**2 * 10.0
        coarse = tessellate(_cylinder(), linear_deflection_mm=0.5, angular_deflection_rad=0.5)
        fine = tessellate(_cylinder(), linear_deflection_mm=0.005, angular_deflection_rad=0.05)
        assert 0.0 < coarse.signed_volume_mm3 < exact
        assert coarse.signed_volume_mm3 <= fine.signed_volume_mm3 < exact
        assert fine.triangle_count > coarse.triangle_count

    def test_a_located_shape_is_drawn_where_it_is(self) -> None:
        transform = symbol("gp_Trsf")()
        transform.SetTranslation(symbol("gp_Vec")(100.0, 0.0, 0.0))
        moved = _box().Moved(symbol("TopLoc_Location")(transform))  # type: ignore[attr-defined]
        low, high = tessellate(moved, linear_deflection_mm=0.1).bounds_mm
        assert low == pytest.approx((100.0, 0.0, 0.0))
        assert high == pytest.approx((110.0, 20.0, 30.0))

    def test_the_callers_shape_carries_no_triangulation_afterwards(self) -> None:
        shape = _box()
        tessellate(shape, linear_deflection_mm=0.1)
        tool = symbol("BRep_Tool")
        location = symbol("TopLoc_Location")
        for face in explore_oriented(shape, FACE):
            assert tool.Triangulation_s(symbol("TopoDS").Face_s(face), location()) is None

    @pytest.mark.parametrize("deflection", [0.0, -1.0, float("nan"), float("inf")])
    def test_a_deflection_that_is_not_a_tolerance_is_refused(self, deflection: float) -> None:
        with pytest.raises(KernelError, match="not a tolerance"):
            tessellate(_box(), linear_deflection_mm=deflection)

    def test_an_angular_deflection_outside_the_open_half_turn_is_refused(self) -> None:
        with pytest.raises(KernelError, match="outside"):
            tessellate(_box(), linear_deflection_mm=0.1, angular_deflection_rad=math.pi)


class TestLevelsOfDetail:
    def test_one_mesh_per_level_finest_first(self) -> None:
        meshes = levels_of_detail(_cylinder(), (0.01, 0.1, 1.0), angular_deflection_rad=1.5)
        assert [m.linear_deflection_mm for m in meshes] == [0.01, 0.1, 1.0]
        counts = [m.triangle_count for m in meshes]
        assert counts[0] > counts[1] > counts[2]  # 280, 88, 32 on 2026-09-15

    def test_a_tight_angular_deflection_holds_every_level_at_one_detail(self) -> None:
        # The tighter of the two deflections decides; this is why the levels take a loose angle.
        meshes = levels_of_detail(_cylinder(), (0.01, 0.1, 1.0), angular_deflection_rad=0.05)
        assert len({m.triangle_count for m in meshes}) == 1

    @pytest.mark.parametrize("deflections", [(), (1.0, 0.1), (0.1, 0.1)])
    def test_levels_out_of_order_are_refused(self, deflections: tuple[float, ...]) -> None:
        with pytest.raises(KernelError):
            levels_of_detail(_box(), deflections)


class TestTheContainerIsAGlb:
    def test_header_chunks_and_alignment_follow_the_specification(self) -> None:
        data = write_glb(part_scene("box", tessellate(_box(), linear_deflection_mm=0.1)))
        magic, version, length = struct.unpack_from("<III", data, 0)
        assert (magic, version, length) == (GLB_MAGIC, 2, len(data))
        json_length, json_type = struct.unpack_from("<II", data, 12)
        assert json_type == CHUNK_JSON and json_length % 4 == 0
        assert json.loads(data[20 : 20 + json_length])["asset"]["version"] == "2.0"
        bin_offset = 20 + json_length
        bin_length, bin_type = struct.unpack_from("<II", data, bin_offset)
        assert bin_type == CHUNK_BIN and bin_length % 4 == 0
        assert bin_offset + 8 + bin_length == len(data)

    def test_the_same_scene_writes_the_same_bytes(self) -> None:
        mesh = tessellate(_box(), linear_deflection_mm=0.1)
        assert write_glb(part_scene("box", mesh)) == write_glb(part_scene("box", mesh))

    def test_positions_and_indices_read_back_exactly(self) -> None:
        mesh = tessellate(_cylinder(), linear_deflection_mm=0.05)
        document, binary = read_glb(write_glb(part_scene("cyl", mesh)))
        positions, indices = mesh_arrays(document, binary, 0)
        assert np.array_equal(indices, mesh.indices)
        assert np.array_equal(positions, mesh.positions.astype(np.float32))

    def test_the_position_accessor_states_min_and_max_of_what_is_stored(self) -> None:
        document, _ = read_glb(write_glb(part_scene("box", tessellate(_box(), linear_deflection_mm=0.1))))
        position = document["accessors"][document["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
        assert position["componentType"] == FLOAT and position["type"] == "VEC3"
        assert position["min"] == [0.0, 0.0, 0.0] and position["max"] == [10.0, 20.0, 30.0]
        index = document["accessors"][document["meshes"][0]["primitives"][0]["indices"]]
        assert index["componentType"] == UNSIGNED_INT
        targets = {view["target"] for view in document["bufferViews"]}
        assert targets == {ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER}
        assert all(view["byteOffset"] % 4 == 0 for view in document["bufferViews"])

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda b: b[:4] + b"\x00" + b[5:],
            lambda b: b"XXXX" + b[4:],
            lambda b: b[:-4],
        ],
    )
    def test_a_damaged_container_is_refused(self, mutate) -> None:  # type: ignore[no-untyped-def]
        data = write_glb(part_scene("box", tessellate(_box(), linear_deflection_mm=0.1)))
        with pytest.raises(GltfError):
            read_glb(mutate(data))


class TestMillimetresAndZUpStayInTheData:
    def test_one_root_node_scales_to_metres_and_turns_z_to_y(self) -> None:
        document, _ = read_glb(write_glb(part_scene("box", tessellate(_box(), linear_deflection_mm=0.1))))
        root = document["nodes"][document["scenes"][0]["nodes"][0]]
        assert root["matrix"] == list(ROOT_MATRIX)
        m = np.array(root["matrix"]).reshape(4, 4, order="F")
        assert m[:3, :3] @ np.array([0.0, 0.0, 1.0]) == pytest.approx([0.0, 0.001, 0.0])
        assert m[:3, :3] @ np.array([0.0, 1.0, 0.0]) == pytest.approx([0.0, 0.0, -0.001])

    def test_the_root_transform_keeps_the_winding(self) -> None:
        # glTF flips the winding under a negative determinant; this one must be positive.
        assert _determinant(list(ROOT_MATRIX)) > 0.0

    def test_the_mesh_says_its_units_and_tolerance(self) -> None:
        document, _ = read_glb(write_glb(part_scene("box", tessellate(_box(), linear_deflection_mm=0.1))))
        assert document["meshes"][0]["extras"]["units"] == "mm"
        assert document["meshes"][0]["extras"]["linear_deflection_mm"] == 0.1


def _bolted_frame() -> ProductStructure:
    bolts = tuple(
        Instance(component="bolt", tag="bolt", index=k, placement=at(k * 50.0)) for k in range(1, 41)
    )
    plate = Instance(component="plate", tag="plate", index=1, placement=turned((0.0, 0.0, 1.0), math.pi / 2))
    return ProductStructure(
        "frame",
        [Component(name="frame", instances=(*bolts, plate)), Component(name="bolt"), Component(name="plate")],
    )


class TestInstancing:
    def test_forty_bolts_are_one_mesh_and_forty_nodes(self) -> None:
        bolt = tessellate(_cylinder(3.0, 20.0), linear_deflection_mm=0.05)
        plate = tessellate(_box(), linear_deflection_mm=0.1)
        document, binary = read_glb(write_glb(scene_for(_bolted_frame(), {"bolt": bolt, "plate": plate})))
        assert instance_counts(document) == {"bolt": 40, "plate": 1}
        assert len(document["meshes"]) == 2
        # the vertex data is exactly one bolt and one plate, however many bolts are placed
        one_bolt = len(read_glb(write_glb(part_scene("bolt", bolt)))[1])
        one_plate = len(read_glb(write_glb(part_scene("plate", plate)))[1])
        assert len(binary) == one_bolt + one_plate

    def test_a_node_is_named_by_its_occurrence_path(self) -> None:
        bolt = tessellate(_cylinder(3.0, 20.0), linear_deflection_mm=0.05)
        plate = tessellate(_box(), linear_deflection_mm=0.1)
        document, _ = read_glb(write_glb(scene_for(_bolted_frame(), {"bolt": bolt, "plate": plate})))
        names = {node["name"] for node in document["nodes"]}
        assert {"frame/bolt.1", "frame/bolt.40", "frame/plate.1"} <= names

    def test_a_placement_is_written_column_major(self) -> None:
        bolt = tessellate(_cylinder(3.0, 20.0), linear_deflection_mm=0.05)
        plate = tessellate(_box(), linear_deflection_mm=0.1)
        document, _ = read_glb(write_glb(scene_for(_bolted_frame(), {"bolt": bolt, "plate": plate})))
        by_name = {node["name"]: node for node in document["nodes"]}
        assert by_name["frame/bolt.2"]["matrix"][12:15] == [100.0, 0.0, 0.0]
        turned_matrix = np.array(by_name["frame/plate.1"]["matrix"]).reshape(4, 4, order="F")
        # 90 degrees about +z takes +x to +y.
        assert turned_matrix[:3, :3] @ np.array([1.0, 0.0, 0.0]) == pytest.approx([0.0, 1.0, 0.0])

    def test_a_leaf_component_with_no_mesh_is_refused_by_name(self) -> None:
        with pytest.raises(GltfError, match="plate"):
            scene_for(_bolted_frame(), {"bolt": tessellate(_box(), linear_deflection_mm=0.1)})

    def test_a_mirror_is_refused(self) -> None:
        mesh = tessellate(_box(), linear_deflection_mm=0.1)
        mirror = Frame((-1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        with pytest.raises(GltfError, match="proper rotation"):
            Scene({"box": mesh}, (Placement("box", "box", mirror),))

    def test_two_placements_with_one_name_are_refused(self) -> None:
        mesh = tessellate(_box(), linear_deflection_mm=0.1)
        with pytest.raises(GltfError, match="share a name"):
            Scene({"box": mesh}, (Placement("a", "box", Frame()), Placement("a", "box", Frame())))

    def test_an_index_past_the_vertices_is_refused(self) -> None:
        bad = TriangleMesh(np.zeros((3, 3)), np.array([[0, 1, 3]]), 0.1, 0.5)
        with pytest.raises(GltfError, match="does not have"):
            write_glb(part_scene("bad", bad))


class TestTheCacheKey:
    def test_every_input_moves_the_key(self) -> None:
        base = cache_key("abc", linear_deflection_mm=0.1, angular_deflection_rad=0.5)
        assert base == cache_key("abc", linear_deflection_mm=0.1, angular_deflection_rad=0.5)
        assert base != cache_key("abd", linear_deflection_mm=0.1, angular_deflection_rad=0.5)
        assert base != cache_key("abc", linear_deflection_mm=0.10000000000000002, angular_deflection_rad=0.5)
        assert base != cache_key("abc", linear_deflection_mm=0.1, angular_deflection_rad=0.4)

    def test_the_layout_version_is_in_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.render.gltf as gltf

        base = cache_key("abc", linear_deflection_mm=0.1, angular_deflection_rad=0.5)
        monkeypatch.setattr(gltf, "LAYOUT_VERSION", gltf.LAYOUT_VERSION + 1)
        assert base != gltf.cache_key("abc", linear_deflection_mm=0.1, angular_deflection_rad=0.5)

    @pytest.mark.parametrize(
        ("digest", "linear", "angular"),
        [(" ", 0.1, 0.5), ("abc", 0.0, 0.5), ("abc", 0.1, float("nan"))],
    )
    def test_a_key_on_nothing_is_refused(self, digest: str, linear: float, angular: float) -> None:
        with pytest.raises(GltfError):
            cache_key(digest, linear_deflection_mm=linear, angular_deflection_rad=angular)
