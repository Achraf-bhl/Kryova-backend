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
    response = client.post(
        "/api/v1/auth/register", json={"email": "x@kryova.dev", "password": "short"}
    )
    assert response.status_code == 422


def test_timestamps_come_back_as_utc(auth_client: AuthenticatedTestClient) -> None:
    created_at = auth_client.get("/api/v1/auth/me").json()["created_at"]
    assert created_at.endswith("Z") or created_at.endswith("+00:00")


class TestClientAddressForRateLimiting:
    """`X-Forwarded-For` is written by whoever sends the request. Trusted
    unconditionally, a client that rotates it has no rate limit at all -- which
    is what this used to do."""

    @staticmethod
    def _request(headers: dict[str, str], peer: str = "10.0.0.9"):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (peer, 51234),
        }
        return Request(scope)

    def test_the_header_is_ignored_when_no_proxy_is_configured(self, monkeypatch) -> None:
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", False)
        request = self._request({"x-forwarded-for": "1.2.3.4"})
        assert auth._client_ip(request) == "10.0.0.9"

    def test_one_proxy_yields_the_address_that_proxy_saw(self, monkeypatch) -> None:
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
        monkeypatch.setattr(auth.settings, "trusted_proxy_count", 1)
        # nginx appends the peer it saw, so the real client is the LAST entry.
        request = self._request({"x-forwarded-for": "203.0.113.7"})
        assert auth._client_ip(request) == "203.0.113.7"

    def test_a_forged_prefix_cannot_shift_the_index(self, monkeypatch) -> None:
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
        monkeypatch.setattr(auth.settings, "trusted_proxy_count", 1)
        # Everything left of the trusted hop was supplied by the caller.
        request = self._request({"x-forwarded-for": "9.9.9.9, 8.8.8.8, 203.0.113.7"})
        assert auth._client_ip(request) == "203.0.113.7"

    def test_two_proxies_count_two_from_the_right(self, monkeypatch) -> None:
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
        monkeypatch.setattr(auth.settings, "trusted_proxy_count", 2)
        request = self._request({"x-forwarded-for": "203.0.113.7, 172.16.0.1"})
        assert auth._client_ip(request) == "203.0.113.7"

    def test_a_shorter_chain_than_configured_falls_back_to_the_socket(self, monkeypatch) -> None:
        # The chain is not what the deployment claims, so guessing an index
        # would hand the caller its own forged value.
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
        monkeypatch.setattr(auth.settings, "trusted_proxy_count", 2)
        request = self._request({"x-forwarded-for": "203.0.113.7"})
        assert auth._client_ip(request) == "10.0.0.9"

    def test_a_missing_header_falls_back_to_the_socket(self, monkeypatch) -> None:
        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "trust_proxy_headers", True)
        assert auth._client_ip(self._request({})) == "10.0.0.9"


class TestPasswordResetTokenIsNotLogged:
    """There is no mail transport, so the token has nowhere to go. Logging it
    at INFO put a working password-equivalent in every production log line."""

    @staticmethod
    def _request_reset(client: AuthenticatedTestClient) -> None:
        client.post("/api/v1/auth/register", json=CREDENTIALS)
        response = client.post(
            "/api/v1/auth/password-reset-request", json={"email": CREDENTIALS["email"]}
        )
        assert response.status_code == 204

    def test_production_logs_the_request_but_never_the_token(
        self, client: AuthenticatedTestClient, monkeypatch, caplog
    ) -> None:
        import logging

        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "environment", "production")
        with caplog.at_level(logging.DEBUG, logger="app.api.routes.auth"):
            self._request_reset(client)

        emitted = " ".join(record.getMessage() for record in caplog.records)
        assert "reset" in emitted.lower()
        assert CREDENTIALS["email"] not in emitted
        assert not any(len(word) > 30 for word in emitted.split())

    def test_development_logs_the_token_at_debug_only(
        self, client: AuthenticatedTestClient, monkeypatch, caplog
    ) -> None:
        import logging

        from app.api.routes import auth

        monkeypatch.setattr(auth.settings, "environment", "development")
        with caplog.at_level(logging.INFO, logger="app.api.routes.auth"):
            self._request_reset(client)
        assert caplog.records == []

        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="app.api.routes.auth"):
            self._request_reset(client)
        assert any("token" in r.getMessage() for r in caplog.records)
