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


class TestTheDisplayMesh:
    """`GET .../geometry/{n}/display` -- a STEP version as a GLB, made once per file and level (P6.1).

    Written on Linux on 2026-09-15 and not run there as pytest (the user's rule). The route's
    tessellation and store path were checked by a one-off script on a box read back from STEP.
    """

    @staticmethod
    def _step_version(auth_client, project_id, tmp_path, size=(10.0, 30.0, 40.0)) -> None:
        from tests.test_mesh import write_step_box

        data = write_step_box(tmp_path / "box.step", size).read_bytes()
        assert upload(auth_client, project_id, "box.step", data).status_code == 201

    def test_a_step_version_is_served_as_a_glb_holding_the_part(
        self, auth_client, project_id, tmp_path
    ) -> None:
        from app.render.gltf import mesh_arrays, read_glb

        self._step_version(auth_client, project_id, tmp_path)
        response = auth_client.get(f"/api/v1/projects/{project_id}/geometry/1/display")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "model/gltf-binary"
        assert response.headers["x-display-cache"] == "miss"
        document, binary = read_glb(response.content)
        positions, indices = mesh_arrays(document, binary, 0)
        assert positions.min(axis=0).tolist() == pytest.approx([0.0, 0.0, 0.0], abs=1e-5)
        assert positions.max(axis=0).tolist() == pytest.approx([10.0, 30.0, 40.0], abs=1e-5)
        assert document["meshes"][0]["name"] == "box"

    def test_the_second_request_is_served_from_the_store_with_the_same_bytes(
        self, auth_client, project_id, tmp_path, db_session
    ) -> None:
        from sqlalchemy import func, select

        from app.models import Media, MediaKind

        self._step_version(auth_client, project_id, tmp_path)
        url = f"/api/v1/projects/{project_id}/geometry/1/display"
        first = auth_client.get(url)
        second = auth_client.get(url)
        assert second.status_code == 200
        assert second.headers["x-display-cache"] == "hit"
        assert second.content == first.content
        assert second.headers["x-display-key"] == first.headers["x-display-key"]
        meshes = db_session.scalar(
            select(func.count()).select_from(Media).where(Media.kind == MediaKind.MESH)
        )
        assert meshes == 1

    def test_the_same_file_uploaded_again_is_the_same_key(
        self, auth_client, project_id, tmp_path
    ) -> None:
        """One buffer uploaded twice, not `_step_version` called twice.

        **OCCT's STEP writer is not deterministic across two writes in one
        process**: it names the product `'Open CASCADE STEP translator 7.8 N'`
        with N incrementing per process, so two calls to `write_step_box` with
        identical arguments differ on two lines and hash differently. Measured
        2026-09-17. Calling the helper twice therefore uploaded two *different*
        files and then asserted they shared a content-addressed key — a test
        that could never have passed on any machine, and had never run on one.
        """
        from tests.test_mesh import write_step_box

        data = write_step_box(tmp_path / "box.step", (10.0, 30.0, 40.0)).read_bytes()
        for _ in range(2):
            assert upload(auth_client, project_id, "box.step", data).status_code == 201
        base = f"/api/v1/projects/{project_id}/geometry"
        first = auth_client.get(f"{base}/1/display")
        second = auth_client.get(f"{base}/2/display")
        assert second.headers["x-display-cache"] == "hit"
        assert second.headers["x-display-key"] == first.headers["x-display-key"]

    def test_each_level_is_its_own_key(
        self, auth_client, project_id, tmp_path
    ) -> None:
        self._step_version(auth_client, project_id, tmp_path)
        base = f"/api/v1/projects/{project_id}/geometry/1/display"
        keys = {auth_client.get(f"{base}?level={n}").headers["x-display-key"] for n in (0, 1, 2)}
        assert len(keys) == 3

    @pytest.mark.parametrize("level", [-1, 3])
    def test_a_level_that_does_not_exist_is_refused(
        self, auth_client, project_id, tmp_path, level
    ) -> None:
        self._step_version(auth_client, project_id, tmp_path)
        response = auth_client.get(f"/api/v1/projects/{project_id}/geometry/1/display?level={level}")
        assert response.status_code == 422

    def test_an_stl_version_is_refused_and_told_to_send_step(
        self, auth_client, project_id, cube_stl
    ) -> None:
        upload(auth_client, project_id, "part.stl", cube_stl)
        response = auth_client.get(f"/api/v1/projects/{project_id}/geometry/1/display")
        assert response.status_code == 422
        assert "STEP" in response.json()["detail"]

    def test_a_missing_version_is_404(self, auth_client, project_id) -> None:
        assert auth_client.get(f"/api/v1/projects/{project_id}/geometry/9/display").status_code == 404
