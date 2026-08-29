import pytest
from fastapi.testclient import TestClient

from tests.conftest import binary_stl

ASCII_STL = b"""solid part
facet normal 0 0 1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 2 0
  endloop
endfacet
endsolid part
"""

STEP_FILE = b"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('bracket.step','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 -1 1 5 4 }'));
ENDSEC;
DATA;
ENDSEC;
END-ISO-10303-21;
"""


def upload(client: TestClient, project_id: str, name: str, data: bytes, **form):
    return client.post(
        f"/api/v1/projects/{project_id}/geometry",
        files={"file": (name, data, "application/octet-stream")},
        data=form,
    )


def test_binary_stl_upload_reports_bounding_box(auth_client, project_id, cube_stl) -> None:
    response = upload(auth_client, project_id, "part.stl", cube_stl, note="first pass")
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["version_number"] == 1
    assert body["file_format"] == "stl"
    assert body["note"] == "first pass"
    assert body["size_bytes"] == len(cube_stl)
    assert body["stats"]["encoding"] == "binary"
    assert body["stats"]["triangle_count"] == 2
    assert body["stats"]["bounding_box"]["size"] == [10.0, 20.0, 5.0]


def test_ascii_stl_is_detected(auth_client, project_id) -> None:
    response = upload(auth_client, project_id, "part.stl", ASCII_STL)
    assert response.status_code == 201, response.text
    assert response.json()["stats"]["encoding"] == "ascii"
    assert response.json()["stats"]["triangle_count"] == 1


def test_step_upload_records_schema(auth_client, project_id) -> None:
    response = upload(auth_client, project_id, "bracket.STEP", STEP_FILE)
    assert response.status_code == 201, response.text
    assert response.json()["file_format"] == "step"
    assert "AUTOMOTIVE_DESIGN" in response.json()["stats"]["schema"]


def test_uploads_increment_version_numbers(auth_client, project_id, cube_stl) -> None:
    for expected in (1, 2, 3):
        response = upload(auth_client, project_id, "part.stl", cube_stl)
        assert response.json()["version_number"] == expected

    listed = auth_client.get(f"/api/v1/projects/{project_id}/geometry").json()
    assert [v["version_number"] for v in listed["items"]] == [3, 2, 1]  # newest first


def test_download_returns_the_original_bytes(auth_client, project_id, cube_stl) -> None:
    upload(auth_client, project_id, "part.stl", cube_stl)
    response = auth_client.get(f"/api/v1/projects/{project_id}/geometry/1/download")
    assert response.status_code == 200
    assert response.content == cube_stl


def test_unsupported_extension_is_rejected(auth_client, project_id) -> None:
    response = upload(auth_client, project_id, "notes.txt", b"hello")
    assert response.status_code == 415


def test_malformed_step_is_rejected(auth_client, project_id) -> None:
    response = upload(auth_client, project_id, "bracket.step", b"this is not a step file")
    assert response.status_code == 422


def test_empty_stl_is_rejected(auth_client, project_id) -> None:
    response = upload(auth_client, project_id, "empty.stl", binary_stl([]))
    assert response.status_code == 422


def test_oversized_upload_is_rejected(auth_client, project_id, monkeypatch) -> None:
    from app.api.routes import geometry as geometry_routes

    monkeypatch.setattr(geometry_routes.settings, "max_upload_bytes", 100)
    response = upload(auth_client, project_id, "part.stl", binary_stl([]) + b"\0" * 500)
    assert response.status_code == 413


def test_deleting_a_project_removes_its_blobs(auth_client, project_id, cube_stl) -> None:
    version = upload(auth_client, project_id, "part.stl", cube_stl).json()
    digest = version["checksum_sha256"]
    assert auth_client.store.exists(digest)

    auth_client.delete(f"/api/v1/projects/{project_id}")
    assert not auth_client.store.exists(digest)


def test_missing_version_is_404(auth_client, project_id) -> None:
    assert auth_client.get(f"/api/v1/projects/{project_id}/geometry/7").status_code == 404


class TestBrepInspection:
    """STEP and IGES used to come back with a schema string and nothing else --
    no bounding box, which is what the load-case editor and the AI load-case
    drafting both select regions against. That locked every CAD user out."""

    @staticmethod
    def _cad_bytes(tmp_path, suffix: str, size=(10.0, 30.0, 40.0)) -> bytes:
        from tests.test_mesh import write_step_box

        return write_step_box(tmp_path / f"box{suffix}", size).read_bytes()

    def test_a_step_upload_reports_its_bounding_box(
        self, auth_client, project_id, tmp_path
    ) -> None:
        data = self._cad_bytes(tmp_path, ".step")
        response = upload(auth_client, project_id, "bracket.step", data)
        assert response.status_code == 201, response.text

        stats = response.json()["stats"]
        assert stats["bounding_box"]["size"] == pytest.approx([10.0, 30.0, 40.0], abs=1e-6)
        assert stats["bounding_box"]["min"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)

    def test_a_step_upload_reports_its_solid_volume(
        self, auth_client, project_id, tmp_path
    ) -> None:
        data = self._cad_bytes(tmp_path, ".step")
        stats = upload(auth_client, project_id, "bracket.step", data).json()["stats"]
        assert stats["volume_mm3"] == pytest.approx(10.0 * 30.0 * 40.0, rel=1e-6)
        assert stats["solid_count"] == 1

    def test_the_step_schema_is_still_reported(self, auth_client, project_id, tmp_path) -> None:
        # The B-rep pass adds to the text inspection; it must not replace it.
        data = self._cad_bytes(tmp_path, ".step")
        stats = upload(auth_client, project_id, "bracket.step", data).json()["stats"]
        assert "schema" in stats and "bounding_box" in stats

    def test_an_iges_upload_reports_its_bounding_box(
        self, auth_client, project_id, tmp_path
    ) -> None:
        data = self._cad_bytes(tmp_path, ".iges", size=(5.0, 6.0, 7.0))
        response = upload(auth_client, project_id, "bracket.iges", data)
        assert response.status_code == 201, response.text
        stats = response.json()["stats"]
        assert stats["bounding_box"]["size"] == pytest.approx([5.0, 6.0, 7.0], abs=1e-5)

    def test_a_file_the_kernel_cannot_open_still_uploads(self, auth_client, project_id) -> None:
        # `inspect` promises never to raise for a readable file. The mesher is
        # what reports a real problem, in terms of meshing, when a run happens.
        minimal_step = (
            b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN'));\n"
            b"ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
        )
        response = upload(auth_client, project_id, "empty.step", minimal_step)
        assert response.status_code == 201
        assert "bounding_box" not in response.json()["stats"]

    def test_the_bounding_box_matches_what_the_mesher_measures(self, tmp_path) -> None:
        # The inspection is only useful if it agrees with the mesh a simulation
        # would build from the same file.
        from app.geometry.inspect import inspect
        from app.mesh.gmsh_mesher import generate_tet_mesh
        from tests.test_mesh import write_step_box

        path = write_step_box(tmp_path / "box.step", (12.0, 8.0, 25.0))
        stats = inspect(path, "step")
        mesh, _ = generate_tet_mesh(path, "step", element_size_mm=8.0)

        lo, hi = mesh.bounding_box
        assert stats["bounding_box"]["min"] == pytest.approx(lo, abs=1e-6)
        assert stats["bounding_box"]["max"] == pytest.approx(hi, abs=1e-6)
        assert stats["volume_mm3"] == pytest.approx(mesh.volume, rel=1e-4)
