from tests.typing import AuthenticatedTestClient

CREDENTIALS = {"email": "new@kryova.dev", "password": "a-long-enough-password"}


def test_register_returns_user_without_password(client: AuthenticatedTestClient) -> None:
    response = client.post("/api/v1/auth/register", json=CREDENTIALS)
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == CREDENTIALS["email"]
    assert "password" not in body and "hashed_password" not in body


def test_register_rejects_duplicate_email(client: AuthenticatedTestClient) -> None:
    client.post("/api/v1/auth/register", json=CREDENTIALS)
    response = client.post("/api/v1/auth/register", json=CREDENTIALS)
    assert response.status_code == 409


def test_email_is_normalised_to_lowercase(client: AuthenticatedTestClient) -> None:
    client.post("/api/v1/auth/register", json={**CREDENTIALS, "email": "Mixed@Kryova.dev"})
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "mixed@kryova.dev", "password": CREDENTIALS["password"]},
    )
    assert response.status_code == 200


def test_login_rejects_wrong_password(client: AuthenticatedTestClient) -> None:
    client.post("/api/v1/auth/register", json=CREDENTIALS)
    response = client.post(
        "/api/v1/auth/login",
        data={"username": CREDENTIALS["email"], "password": "not-the-password"},
    )
    assert response.status_code == 401


def test_me_requires_a_token(client: AuthenticatedTestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_returns_the_token_owner(auth_client: AuthenticatedTestClient) -> None:
    response = auth_client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "eng@kryova.dev"


def test_short_password_is_rejected(client: AuthenticatedTestClient) -> None:
    response = client.post("/api/v1/auth/register", json={"email": "x@kryova.dev", "password": "short"})
    assert response.status_code == 422


def test_timestamps_come_back_as_utc(auth_client: AuthenticatedTestClient) -> None:
    created_at = auth_client.get("/api/v1/auth/me").json()["created_at"]
    assert created_at.endswith("Z") or created_at.endswith("+00:00")
