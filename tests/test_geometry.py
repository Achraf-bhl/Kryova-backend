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
