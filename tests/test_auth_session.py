import pytest
from fastapi.testclient import TestClient

from app.core.config import INSECURE_SECRET_KEY, Settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from tests.typing import AuthenticatedTestClient


def test_login_sets_httponly_cookies_and_csrf(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"email": "session@kryova.dev", "password": "a-long-enough-password"},
    )
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": "session@kryova.dev",
            "password": "a-long-enough-password",
        },
    )
    assert response.status_code == 200
    assert response.json()["csrf_token"]
    assert any(
        "kryova_access=" in value and "HttpOnly" in value
        for value in response.headers.get_list("set-cookie")
    )
    assert client.cookies.get("kryova_access")
    assert client.cookies.get("kryova_csrf")


def test_refresh_rotates_token_and_me_uses_cookie(
    auth_client: AuthenticatedTestClient,
) -> None:
    old_refresh = auth_client.cookies["kryova_refresh"]
    response = auth_client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert auth_client.cookies["kryova_refresh"] != old_refresh
    assert auth_client.get("/api/v1/auth/me").status_code == 200


def test_cookie_mutation_requires_csrf(auth_client: AuthenticatedTestClient) -> None:
    csrf = auth_client.cookies["kryova_csrf"]
    denied = auth_client.post(
        "/api/v1/projects", headers={"x-csrf-token": "wrong"}, json={"name": "Nope"}
    )
    allowed = auth_client.post(
        "/api/v1/projects", headers={"x-csrf-token": csrf}, json={"name": "Yes"}
    )
    assert denied.status_code == 403
    assert allowed.status_code == 201


class TestTokenTypeIsolation:
    """Access and refresh tokens share a signing key, so type must be enforced.

    Without the `type` claim check a refresh token is a 30-day access token --
    and because a bearer header also skips CSRF, it would be a CSRF-exempt one.
    """

    def test_refresh_token_is_rejected_as_an_access_token(self) -> None:
        assert decode_access_token(create_refresh_token("user-1")) is None

    def test_access_token_is_rejected_as_a_refresh_token(self) -> None:
        assert decode_refresh_token(create_access_token("user-1")) is None

    def test_each_token_still_decodes_as_its_own_type(self) -> None:
        assert decode_access_token(create_access_token("user-1")) == "user-1"
        assert decode_refresh_token(create_refresh_token("user-1")) == "user-1"

    def test_refresh_cookie_cannot_authenticate_an_api_call(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        """The end-to-end version: present the refresh token as a bearer token."""
        refresh = auth_client.cookies["kryova_refresh"]
        auth_client.cookies.clear()
        response = auth_client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh}"}
        )
        assert response.status_code == 401

    def test_refresh_endpoint_rejects_an_access_token(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        access = auth_client.cookies["kryova_access"]
        auth_client.cookies.set("kryova_refresh", access)
        assert auth_client.post("/api/v1/auth/refresh").status_code == 401


class TestProductionHardening:
    """`ENVIRONMENT=production` must refuse development-grade secrets."""

    def _settings(self, **overrides: object) -> Settings:
        base: dict[str, object] = {
            "environment": "production",
            "database_url": "postgresql://u:p@example.com/db",
            "secret_key": "x" * 48,
            "cookie_secure": True,
            "cors_origins": ["https://app.kryova.dev"],
        }
        return Settings(**{**base, **overrides})  # type: ignore[arg-type]

    def test_accepts_a_properly_configured_production_env(self) -> None:
        assert self._settings().is_production

    def test_rejects_the_default_secret_key(self) -> None:
        with pytest.raises(ValueError, match="changeme"):
            self._settings(secret_key=INSECURE_SECRET_KEY)

    def test_rejects_a_short_secret_key(self) -> None:
        with pytest.raises(ValueError, match="shorter than"):
            self._settings(secret_key="too-short")

    def test_rejects_insecure_cookies(self) -> None:
        with pytest.raises(ValueError, match="COOKIE_SECURE"):
            self._settings(cookie_secure=False)

    def test_rejects_a_plaintext_cors_origin(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            self._settings(cors_origins=["http://app.kryova.dev"])

    def test_development_is_left_alone(self) -> None:
        relaxed = Settings(
            environment="development",
            database_url="postgresql://u:p@localhost/db",
            secret_key=INSECURE_SECRET_KEY,
            cookie_secure=False,
        )  # type: ignore[arg-type]
        assert not relaxed.is_production
