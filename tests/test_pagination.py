from fastapi.testclient import TestClient


def test_projects_are_paginated(auth_client: TestClient) -> None:
    for index in range(3):
        response = auth_client.post("/api/v1/projects", json={"name": f"Project {index}"})
        assert response.status_code == 201

    first = auth_client.get("/api/v1/projects", params={"page": 1, "page_size": 2})
    second = auth_client.get("/api/v1/projects", params={"page": 2, "page_size": 2})
    assert first.status_code == 200 and second.status_code == 200
    assert len(first.json()["items"]) == 2
    assert len(second.json()["items"]) == 1
    assert first.json()["total"] == 3


def test_rejects_large_page_size(auth_client: TestClient) -> None:
    assert auth_client.get("/api/v1/projects", params={"page_size": 101}).status_code == 422
