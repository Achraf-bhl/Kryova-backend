"""What a rate limit is counted against (P1.6).

The window itself was already right and tested through the auth routes. What
this file pins is the *key* — the half that decides whether a limit rations the
thing it was meant to.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit import RateLimit, RateLimiter, client_ip, limit_key
from app.core.config import settings
from app.core.security import create_access_token


class _Request:
    """The two things `client_ip` and `limit_key` read. No ASGI needed."""

    def __init__(
        self, *, peer: str = "10.0.0.1", headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.client = type("Client", (), {"host": peer})()
        self.headers = headers or {}
        self.cookies = cookies or {}


class TestTheForwardedForRuleIsBelievedOnlyBehindAProxy:
    def test_the_header_is_ignored_when_no_proxy_is_declared(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Otherwise a client that rotates the header has no rate limit at all.
        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        request = _Request(peer="10.0.0.1", headers={"x-forwarded-for": "1.2.3.4"})
        assert client_ip(request) == "10.0.0.1"  # type: ignore[arg-type]

    def test_the_client_is_counted_from_the_right_with_one_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Each proxy appends the address *it* saw, so with one in front the
        # rightmost entry is what nginx observed — the real client. Everything
        # to its left arrived in the header the caller wrote and is worthless.
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        monkeypatch.setattr(settings, "trusted_proxy_count", 1)
        request = _Request(
            peer="10.0.0.1", headers={"x-forwarded-for": "forged, 203.0.113.9"}
        )
        assert client_ip(request) == "203.0.113.9"  # type: ignore[arg-type]

    def test_two_proxies_move_the_client_one_further_from_the_end(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The reason the count is configured rather than assumed: with two
        # proxies the rightmost entry is the *first* proxy's address, and
        # rate-limiting on that puts every customer in one bucket.
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        monkeypatch.setattr(settings, "trusted_proxy_count", 2)
        request = _Request(
            peer="10.0.0.1",
            headers={"x-forwarded-for": "forged, 203.0.113.9, 10.0.0.2"},
        )
        assert client_ip(request) == "203.0.113.9"  # type: ignore[arg-type]

    def test_a_chain_shorter_than_configured_falls_back_to_the_socket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The chain is not what the deployment claims, so believe the socket
        # rather than pick whichever entry happens to be leftmost -- which is
        # the one the caller controls.
        monkeypatch.setattr(settings, "trust_proxy_headers", True)
        monkeypatch.setattr(settings, "trusted_proxy_count", 3)
        request = _Request(peer="10.0.0.1", headers={"x-forwarded-for": "1.2.3.4"})
        assert client_ip(request) == "10.0.0.1"  # type: ignore[arg-type]


class TestALimitIsCountedAgainstThePrincipalWhereThereIsOne:
    def test_a_signed_in_caller_is_keyed_on_their_user_id(self) -> None:
        # One office behind one NAT is a single address and hundreds of
        # engineers. Keyed on the address, a per-user budget either throttles a
        # customer or is set so high it stops nothing.
        request = _Request(cookies={"kryova_access": create_access_token("user-42")})
        assert limit_key(request, scope="ai.chat") == "ai.chat:user:user-42"  # type: ignore[arg-type]

    def test_a_bearer_token_is_read_as_well_as_the_cookie(self) -> None:
        token = create_access_token("user-42")
        request = _Request(headers={"authorization": f"Bearer {token}"})
        assert limit_key(request, scope="ai.chat") == "ai.chat:user:user-42"  # type: ignore[arg-type]

    def test_an_anonymous_caller_falls_back_to_the_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        request = _Request(peer="203.0.113.9")
        assert limit_key(request, scope="login") == "login:ip:203.0.113.9"  # type: ignore[arg-type]

    def test_a_broken_token_does_not_buy_a_bigger_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Falling through to the address is the safe direction: an attacker
        # cannot get a *larger* budget by presenting rubbish, only the one they
        # already had.
        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        request = _Request(peer="203.0.113.9", cookies={"kryova_access": "not-a-jwt"})
        assert limit_key(request, scope="login") == "login:ip:203.0.113.9"  # type: ignore[arg-type]

    def test_a_refresh_token_is_not_accepted_as_a_principal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `decode_access_token` checks the type claim. Without it a refresh
        # token in the access cookie would name a principal.
        from app.core.security import create_refresh_token

        monkeypatch.setattr(settings, "trust_proxy_headers", False)
        request = _Request(
            peer="203.0.113.9", cookies={"kryova_access": create_refresh_token("user-42")}
        )
        assert limit_key(request, scope="login") == "login:ip:203.0.113.9"  # type: ignore[arg-type]

    def test_two_scopes_on_one_principal_do_not_share_a_budget(self) -> None:
        # A noisy endpoint spending another one's budget is how a rate limit
        # takes down the feature next to the one being abused.
        request = _Request(cookies={"kryova_access": create_access_token("user-42")})
        assert limit_key(request, scope="a") != limit_key(request, scope="b")  # type: ignore[arg-type]

    def test_a_user_key_and_an_address_key_cannot_collide(self) -> None:
        signed_in = _Request(cookies={"kryova_access": create_access_token("10.0.0.1")})
        anonymous = _Request(peer="10.0.0.1")
        assert limit_key(signed_in, scope="s") != limit_key(anonymous, scope="s")  # type: ignore[arg-type]


class TestTheDependencyRefusesOverBudget:
    def _app(self, limit: RateLimit) -> TestClient:
        app = FastAPI()

        @app.get("/thing", dependencies=[Depends(limit)])
        def thing() -> dict[str, bool]:
            return {"ok": True}

        return TestClient(app)

    def test_requests_inside_the_budget_are_served(self) -> None:
        client = self._app(RateLimit("test.a", max_requests=3, window_seconds=60))
        for _ in range(3):
            assert client.get("/thing").status_code == 200

    def test_the_request_over_budget_is_refused_with_a_retry_after(self) -> None:
        # A client that knows when to come back stops hammering; one that does
        # not retries in a tight loop and turns the limit into the load it was
        # meant to prevent.
        limit = RateLimit("test.b", max_requests=2, window_seconds=45)
        client = self._app(limit)
        client.get("/thing")
        client.get("/thing")

        response = client.get("/thing")

        assert response.status_code == 429
        assert response.headers["retry-after"] == "45"
        assert "2 per 45 seconds" in response.json()["detail"]

    def test_two_principals_have_separate_budgets(self) -> None:
        limit = RateLimit("test.c", max_requests=1, window_seconds=60)
        client = self._app(limit)
        first = {"kryova_access": create_access_token("user-1")}
        second = {"kryova_access": create_access_token("user-2")}

        assert client.get("/thing", cookies=first).status_code == 200
        assert client.get("/thing", cookies=first).status_code == 429
        assert client.get("/thing", cookies=second).status_code == 200


class TestTheBackend:
    def test_the_budget_is_readable_so_a_refusal_can_state_it(self) -> None:
        assert RateLimiter(max_requests=7, window_seconds=60).max_requests == 7

    def test_the_in_memory_backend_forgets_a_key_that_has_aged_out(self) -> None:
        from app.api.rate_limit import InMemoryBackend

        backend = InMemoryBackend()
        assert backend.check("k", max_requests=1, window_seconds=0)
        assert backend.check("k", max_requests=1, window_seconds=0)
