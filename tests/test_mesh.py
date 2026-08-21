"""Meshing tests.

These exercise gmsh for real rather than mocking it: the value of this layer is
entirely in whether an actual CAD file comes back as a solvable volume mesh.
"""

import struct
from pathlib import Path

import numpy as np
import pytest

from app.mesh.gmsh_mesher import generate_tet_mesh
from app.mesh.types import MeshError
from app.solve.linear_static import LinearStaticSolver
from app.solve.materials import MATERIALS
from app.solve.types import FaceSelector, Fixture, Load, LoadCase

# Outward-wound triangulation of an axis-aligned box, as index pairs into its
# eight corners (bit i of the index selects the high side of axis i).
_BOX_TRIANGLES = [
    (0, 2, 1), (1, 2, 3),  # x = min
    (4, 5, 6), (5, 7, 6),  # x = max
    (0, 1, 4), (1, 5, 4),  # y = min
    (2, 6, 3), (3, 6, 7),  # y = max
    (0, 4, 2), (2, 4, 6),  # z = min
    (1, 3, 5), (3, 7, 5),  # z = max
]


def box_stl(size: tuple[float, float, float]) -> bytes:
    """A watertight binary STL of a box with one corner at the origin."""
    sx, sy, sz = size
    corners = [
        (sx if i & 4 else 0.0, sy if i & 2 else 0.0, sz if i & 1 else 0.0) for i in range(8)
    ]
    out = bytearray(b"\0" * 80) + struct.pack("<I", len(_BOX_TRIANGLES))
    for tri in _BOX_TRIANGLES:
        p = [np.array(corners[i]) for i in tri]
        normal = np.cross(p[1] - p[0], p[2] - p[0])
        norm = np.linalg.norm(normal)
        out += struct.pack("<3f", *(normal / norm if norm else normal))
        for vertex in p:
            out += struct.pack("<3f", *vertex)
        out += struct.pack("<H", 0)
    return bytes(out)


def write_step_box(path: Path, size: tuple[float, float, float]) -> Path:
    """Author a real STEP file via gmsh's OCC kernel, to mesh it back in."""
    import gmsh

    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.occ.addBox(0, 0, 0, *size)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.clear()
        gmsh.finalize()
    return path


@pytest.fixture
def stl_box(tmp_path: Path) -> Path:
    path = tmp_path / "box.stl"
    path.write_bytes(box_stl((20.0, 20.0, 60.0)))
    return path


class TestStlMeshing:
    def test_produces_a_solid_mesh(self, stl_box: Path) -> None:
        mesh, stats = generate_tet_mesh(stl_box, "stl")
        assert mesh.tet_count > 0
        assert stats["element_type"] == "tet4"
        assert stats["mesher"] == "gmsh"

    def test_volume_matches_the_solid(self, stl_box: Path) -> None:
        mesh, _ = generate_tet_mesh(stl_box, "stl")
        # A tet mesh of a flat-faced box fills it exactly, up to float error.
        assert mesh.volume == pytest.approx(20.0 * 20.0 * 60.0, rel=1e-6)

    def test_no_inverted_or_degenerate_elements(self, stl_box: Path) -> None:
        _, stats = generate_tet_mesh(stl_box, "stl")
        assert stats["inverted_count"] == 0
        assert stats["min_quality"] > 0.0

    def test_bounding_box_is_preserved(self, stl_box: Path) -> None:
        mesh, _ = generate_tet_mesh(stl_box, "stl")
        lo, hi = mesh.bounding_box
        assert lo == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)
        assert hi == pytest.approx([20.0, 20.0, 60.0], abs=1e-6)

    def test_smaller_elements_give_a_denser_mesh(self, stl_box: Path) -> None:
        coarse, _ = generate_tet_mesh(stl_box, "stl", element_size_mm=20.0)
        fine, _ = generate_tet_mesh(stl_box, "stl", element_size_mm=8.0)
        assert fine.tet_count > coarse.tet_count
        # Refinement must not change the volume it fills.
        assert fine.volume == pytest.approx(coarse.volume, rel=1e-6)

    def test_every_node_is_used_by_an_element(self, stl_box: Path) -> None:
        # Gmsh keeps surface-only nodes; leaving them in would add free DOFs
        # and make the stiffness matrix singular.
        mesh, _ = generate_tet_mesh(stl_box, "stl")
        assert len(np.unique(mesh.tets)) == mesh.node_count


class TestStepMeshing:
    def test_step_file_meshes_to_the_right_volume(self, tmp_path: Path) -> None:
        path = write_step_box(tmp_path / "box.step", (10.0, 30.0, 40.0))
        mesh, stats = generate_tet_mesh(path, "step")
        assert mesh.volume == pytest.approx(10.0 * 30.0 * 40.0, rel=1e-6)
        assert stats["element_count"] == mesh.tet_count


class TestBadInput:
    def test_open_shell_is_rejected(self, tmp_path: Path) -> None:
        # Two disconnected triangles: not a closed solid, nothing to fill.
        path = tmp_path / "open.stl"
        out = bytearray(b"\0" * 80) + struct.pack("<I", 1)
        out += struct.pack("<3f", 0, 0, 1)
        out += struct.pack("<3f", 0, 0, 0) + struct.pack("<3f", 10, 0, 0)
        out += struct.pack("<3f", 0, 10, 0) + struct.pack("<H", 0)
        path.write_bytes(bytes(out))

        with pytest.raises(MeshError):
            generate_tet_mesh(path, "stl")

    def test_unreadable_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "junk.stl"
        path.write_bytes(b"not an stl at all")
        with pytest.raises(MeshError):
            generate_tet_mesh(path, "stl")

    def test_negative_element_size_is_rejected(self, stl_box: Path) -> None:
        with pytest.raises(MeshError, match="must be positive"):
            generate_tet_mesh(stl_box, "stl", element_size_mm=-1.0)


class TestMeshedGeometrySolves:
    """The point of the whole pipeline: an unstructured mesh straight out of
    gmsh must reproduce the same closed-form answer as a structured one."""

    def test_stl_to_stress_matches_hand_calculation(self, stl_box: Path) -> None:
        mesh, _ = generate_tet_mesh(stl_box, "stl", element_size_mm=10.0)
        material = MATERIALS["aluminium-6061-t6"]
        force = 8_000.0  # N

        case = LoadCase(
            material=material,
            fixtures=[
                Fixture(where=FaceSelector(axis="z", side="min"), dofs=["z"]),
                Fixture(where=FaceSelector(axis="x", side="min"), dofs=["x"]),
                Fixture(where=FaceSelector(axis="y", side="min"), dofs=["y"]),
            ],
            loads=[Load(where=FaceSelector(axis="z", side="max"), force_n=(0.0, 0.0, force))],
        )
        output = LinearStaticSolver().solve(mesh, case)

        expected_stress = force / (20.0 * 20.0)  # MPa
        assert output.result.max_von_mises_mpa == pytest.approx(expected_stress, rel=1e-6)

        expected_extension = force * 60.0 / (400.0 * material.youngs_modulus_mpa)
        far = np.flatnonzero(np.isclose(mesh.nodes[:, 2], 60.0))
        assert output.displacements[far, 2].mean() == pytest.approx(expected_extension, rel=1e-6)
        assert not output.result.yields


class TestExtensionlessInput:
    """Media-store blobs are named by their SHA-256 and carry no extension.

    Gmsh picks its reader from the extension, so without staging these would all
    be read as an unknown format -- which is exactly how this broke the first
    time the pipeline moved onto content-addressed storage.
    """

    def test_a_file_with_no_extension_still_meshes(self, tmp_path: Path) -> None:
        blob = tmp_path / "e3b0c44298fc1c149afbf4c8996fb924"
        blob.write_bytes(box_stl((10.0, 10.0, 30.0)))

        mesh, _ = generate_tet_mesh(blob, "stl")
        assert mesh.volume == pytest.approx(10.0 * 10.0 * 30.0, rel=1e-6)

    def test_a_misleading_extension_is_overridden_by_the_declared_format(
        self, tmp_path: Path
    ) -> None:
        blob = tmp_path / "part.bin"
        blob.write_bytes(box_stl((10.0, 10.0, 30.0)))

        mesh, _ = generate_tet_mesh(blob, "stl")
        assert mesh.volume == pytest.approx(3000.0, rel=1e-6)


class TestNulHeavyStl:
    """Gmsh cannot classify a binary STL whose zeroed 80-byte header is followed
    by no 0x0A byte anywhere -- its sniffer skips NUL-starting lines and runs off
    the end. Power-of-two coordinates pack as `00 00 00 4X`, which is exactly
    NUL-heavy enough to trigger it on a small part."""

    def test_a_power_of_two_cube_meshes(self, tmp_path: Path) -> None:
        blob = tmp_path / "cube.stl"
        data = box_stl((8.0, 8.0, 8.0))
        assert bytes([0x0A]) not in data, "fixture no longer reproduces the failing shape"
        blob.write_bytes(data)

        mesh, _ = generate_tet_mesh(blob, "stl")
        assert mesh.volume == pytest.approx(512.0, rel=1e-6)

    def test_a_text_header_is_left_alone(self, tmp_path: Path) -> None:
        # Nothing to fix here, so the file must be used as-is.
        blob = tmp_path / "cube.stl"
        data = box_stl((2.0, 2.0, 2.0))
        blob.write_bytes(b"solid-ish writer name".ljust(80, b" ") + data[80:])

        mesh, _ = generate_tet_mesh(blob, "stl")
        assert mesh.volume == pytest.approx(8.0, rel=1e-6)

    def test_the_original_blob_is_never_modified(self, tmp_path: Path) -> None:
        blob = tmp_path / "cube.stl"
        data = box_stl((8.0, 8.0, 8.0))
        blob.write_bytes(data)

        generate_tet_mesh(blob, "stl")
        assert blob.read_bytes() == data, "staging must not touch the stored blob"

    def test_step_with_no_extension_still_meshes(self, tmp_path: Path) -> None:
        source = write_step_box(tmp_path / "box.step", (10.0, 10.0, 10.0))
        blob = tmp_path / "a1b2c3d4e5f6"
        blob.write_bytes(source.read_bytes())

        mesh, _ = generate_tet_mesh(blob, "step")
        assert mesh.volume == pytest.approx(1000.0, rel=1e-6)
