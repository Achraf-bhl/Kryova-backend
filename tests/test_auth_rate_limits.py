"""The sign-in limits, counted per address *and* per account (ROAD_TO_10 3.3).

One address is not one person: an office behind one NAT is a single address and a
hundred engineers, and ten sign-ins a minute across all of them is a lockout on a Monday
morning. So the address budget for the routes a signed-in client uses all day is wider
(`AUTH_IP_REQUESTS_PER_MINUTE`, 30), and what actually bounds *guessing* moved to the
account the request names, at ten a minute, whatever address it comes from.

What these tests have to hold at once, because each half is the reason for the other:
the address budget is wider, rotating addresses buys nothing against one account, the
account budget says nothing about which accounts exist, and the routes that were not
meant to widen did not.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.rate_limit import account_key
from app.core.config import settings
from app.core.security import create_mfa_challenge_token

LOGIN = "/api/v1/auth/login"
MFA = "/api/v1/auth/login/mfa"
RESET = "/api/v1/auth/password-reset-request"


def _login(client: TestClient, email: str, *, from_address: str | None = None):
    headers = {"x-forwarded-for": f"forged, {from_address}"} if from_address else {}
    return client.post(
        LOGIN, data={"username": email, "password": "not-the-password"}, headers=headers
    )


@pytest.fixture
def behind_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Believe `X-Forwarded-For`, so a test can come from many addresses."""
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)


class TestAnOfficeBehindOneAddressCanSignIn:
    def test_more_than_ten_different_people_sign_in_from_one_address_in_a_minute(
        self, client: TestClient
    ) -> None:
        statuses = [_login(client, f"engineer{i}@office.example").status_code for i in range(12)]

        # All refused for the wrong password, none for the address's budget: ten a
        # minute across a whole office was the lockout this removes.
        assert statuses == [401] * 12

    def test_the_address_budget_still_ends_somewhere(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Credential stuffing across many accounts from one address is what the wider
        # budget still has to stop; it is a number, not an absence of one.
        from app.api import rate_limit

        monkeypatch.setattr(rate_limit.login_limiter, "_max", 3)

        statuses = [_login(client, f"stuffing{i}@office.example").status_code for i in range(4)]

        assert statuses == [401, 401, 401, 429]


class TestGuessingAtOneAccountIsBoundedWhateverTheAddress:
    def test_the_eleventh_attempt_at_one_account_is_refused(self, client: TestClient) -> None:
        statuses = [_login(client, "target@kryova.dev").status_code for _ in range(11)]

        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429

    def test_rotating_addresses_does_not_buy_a_fresh_budget_against_one_account(
        self, client: TestClient, behind_a_proxy: None
    ) -> None:
        # The point of the account key. Each attempt comes from a new address, so the
        # address budget never trips; only the account's does.
        statuses = [
            _login(client, "target@kryova.dev", from_address=f"203.0.113.{i}").status_code
            for i in range(11)
        ]

        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429

    def test_another_account_from_the_same_address_is_not_refused(
        self, client: TestClient
    ) -> None:
        for _ in range(11):
            _login(client, "target@kryova.dev")

        assert _login(client, "someone-else@kryova.dev").status_code == 401

    def test_capital_letters_do_not_buy_a_fresh_budget(self, client: TestClient) -> None:
        for _ in range(10):
            _login(client, "target@kryova.dev")

        assert _login(client, "TARGET@Kryova.dev").status_code == 429

    def test_the_refusal_says_when_to_come_back(self, client: TestClient) -> None:
        for _ in range(10):
            _login(client, "target@kryova.dev")

        refused = _login(client, "target@kryova.dev")

        assert refused.status_code == 429
        assert 1 <= int(refused.headers["retry-after"]) <= 60
        assert refused.headers["ratelimit-remaining"] == "0"


class TestTheAccountBudgetDoesNotSayWhoHasAnAccount:
    def test_a_name_nobody_registered_is_refused_at_the_same_attempt_with_the_same_words(
        self, auth_client: TestClient
    ) -> None:
        def eleventh(email: str) -> tuple[int, str]:
            for _ in range(10):
                _login(auth_client, email)
            refused = _login(auth_client, email)
            return refused.status_code, refused.json()["detail"]

        registered = eleventh("eng@kryova.dev")
        # A fresh address budget: the first run spent 11 of it, the second spends 11 more.
        unregistered = eleventh("ghost@kryova.dev")

        # If a 429 differed between the two, it would be an oracle for "does this
        # address have an account".
        assert registered == unregistered


class TestTheSecondFactorIsBoundedPerAccount:
    def _attempt(self, client: TestClient, challenge: str, *, from_address: str | None = None):
        headers = {"x-forwarded-for": f"forged, {from_address}"} if from_address else {}
        return client.post(
            MFA, json={"challenge_token": challenge, "code": "000000"}, headers=headers
        )

    def test_six_digits_is_not_guessable_faster_by_changing_address(
        self, client: TestClient, behind_a_proxy: None
    ) -> None:
        challenge = create_mfa_challenge_token(str(uuid.uuid4()))

        statuses = [
            self._attempt(client, challenge, from_address=f"198.51.100.{i}").status_code
            for i in range(11)
        ]

        # 401 for "no such user" until the account's budget runs out; the address
        # budget (30) never trips because every attempt is from a new one.
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429

    def test_a_different_challenge_has_its_own_budget(self, client: TestClient) -> None:
        spent = create_mfa_challenge_token(str(uuid.uuid4()))
        for _ in range(11):
            self._attempt(client, spent)

        other = create_mfa_challenge_token(str(uuid.uuid4()))

        assert self._attempt(client, other).status_code == 401


class TestARequestForAResetIsBoundedPerAddressAsked:
    def _ask(self, client: TestClient, email: str, from_address: str):
        return client.post(
            RESET,
            json={"email": email},
            headers={"x-forwarded-for": f"forged, {from_address}"},
        )

    def test_many_sources_cannot_fill_one_inbox(
        self, client: TestClient, behind_a_proxy: None
    ) -> None:
        statuses = [
            self._ask(client, "victim@kryova.dev", f"192.0.2.{i}").status_code for i in range(11)
        ]

        assert statuses[:10] == [204] * 10
        assert statuses[10] == 429


class TestTheRoutesThatWereNotMeantToWidenDidNot:
    def test_registration_is_still_ten_an_address(self, client: TestClient) -> None:
        statuses = [
            client.post(
                "/api/v1/auth/register",
                json={"email": f"new{i}@office.example", "password": "correct-horse-battery"},
            ).status_code
            for i in range(11)
        ]

        assert statuses[:10] == [201] * 10
        assert statuses[10] == 429

    def test_a_refusal_on_registration_carries_retry_after_now(self, client: TestClient) -> None:
        for i in range(10):
            client.post(
                "/api/v1/auth/register",
                json={"email": f"fresh{i}@office.example", "password": "correct-horse-battery"},
            )

        refused = client.post(
            "/api/v1/auth/register",
            json={"email": "one-too-many@office.example", "password": "correct-horse-battery"},
        )

        # Seven auth routes answered 429 with no `Retry-After` before `enforce`.
        assert refused.status_code == 429
        assert 1 <= int(refused.headers["retry-after"]) <= 60

    def test_the_silent_refresh_has_the_wider_address_budget(self, client: TestClient) -> None:
        # Every signed-in client calls it on a timer; an office is one address.
        statuses = [client.post("/api/v1/auth/refresh").status_code for _ in range(12)]

        assert 429 not in statuses
        assert set(statuses) == {401}

    def test_pairing_a_device_refusal_carries_retry_after(self, client: TestClient) -> None:
        statuses = []
        last = None
        for _ in range(12):
            last = client.post("/api/v1/catia/devices/pair", json={"code": "ZZZZZZZZ"})
            statuses.append(last.status_code)

        assert 429 in statuses
        assert last is not None and last.status_code == 429
        assert 1 <= int(last.headers["retry-after"]) <= 60


class TestAccountKeys:
    def test_the_address_is_not_in_the_key(self) -> None:
        key = account_key("login", "Someone@Example.com")

        assert "someone" not in key.lower()
        assert "example" not in key.lower()

    def test_case_and_edge_whitespace_do_not_change_the_key(self) -> None:
        assert account_key("login", " Someone@Example.com ") == account_key(
            "login", "someone@example.com"
        )

    def test_two_scopes_are_two_budgets(self) -> None:
        assert account_key("login", "a@b.c") != account_key("pwreset", "a@b.c")
