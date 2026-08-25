from tests.typing import AuthenticatedTestClient


def test_project_crud_round_trip(auth_client: AuthenticatedTestClient) -> None:
    created = auth_client.post(
        "/api/v1/projects", json={"name": "Drone arm", "description": "carbon"}
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    listed = auth_client.get("/api/v1/projects")
    assert [p["id"] for p in listed.json()["items"]] == [project_id]

    updated = auth_client.patch(f"/api/v1/projects/{project_id}", json={"name": "Drone arm v2"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Drone arm v2"
    assert updated.json()["description"] == "carbon"  # untouched field survives PATCH

    assert auth_client.delete(f"/api/v1/projects/{project_id}").status_code == 204
    assert auth_client.get(f"/api/v1/projects/{project_id}").status_code == 404


def test_projects_require_authentication(client: AuthenticatedTestClient) -> None:
    assert client.get("/api/v1/projects").status_code == 401


def test_another_users_project_is_not_visible(
    auth_client: AuthenticatedTestClient, project_id: str
) -> None:
    auth_client.post(
        "/api/v1/auth/register", json={"email": "other@kryova.dev", "password": "another-password"}
    )
    auth_client.post(
        "/api/v1/auth/login",
        data={"username": "other@kryova.dev", "password": "another-password"},
    )
    auth_client.headers["x-csrf-token"] = auth_client.cookies["kryova_csrf"]

    response = auth_client.get(
        f"/api/v1/projects/{project_id}",
    )
    # 404 not 403: project ids must not be enumerable across accounts.
    assert response.status_code == 404
