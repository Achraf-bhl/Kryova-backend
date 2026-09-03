"""The HTTP and WebSocket surface: pairing, devices, status, events.

The security-relevant assertions here are the pairing lifecycle (single use,
expiry, unguessable), the 404-not-403 posture across accounts, and the fact that
a device token in a query parameter authenticates nothing.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.api.rate_limit import auth_limiter
from app.api.routes.catia import catia_events, pairing_limiter
from app.catia.events import bus
from app.core.security import hash_token
from app.models import User
from app.models.base import utcnow
from app.models.catia import CatiaDevice, CatiaDeviceStatus
from tests.typing import AuthenticatedTestClient

PREFIX = "/api/v1/catia"


def create_device(client: AuthenticatedTestClient, name: str = "Office desktop") -> dict:
    pairing_limiter.reset()
    response = client.post(f"{PREFIX}/devices", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def pair(client: AuthenticatedTestClient, code: str, **extra) -> dict:
    pairing_limiter.reset()
    response = client.post(f"{PREFIX}/devices/pair", json={"code": code, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def second_user(client: AuthenticatedTestClient) -> AuthenticatedTestClient:
    """Sign the same client in as a different account."""
    auth_limiter.reset()
    credentials = {"email": "other@kryova.dev", "password": "correct-horse-battery"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    response = client.post(
        "/api/v1/auth/login",
        data={"username": credentials["email"], "password": credentials["password"]},
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = client.cookies["kryova_csrf"]
    return client


# -- device registration -----------------------------------------------------


def test_creating_a_device_returns_a_pairing_code_and_the_command_to_run(auth_client):
    created = create_device(auth_client)
    assert len(created["pairing_code"]) == 8
    # Crockford-ish alphabet: no I, L, O or U, so a code read off a screen
    # cannot be mistyped into a different valid code.
    assert not set(created["pairing_code"]) & set("ILOU")
    assert created["pairing_code"] in created["command"]
    assert created["device"]["status"] == "pending"
    assert created["device"]["online"] is False


def test_the_pairing_code_is_shown_once_and_never_listed_again(auth_client):
    create_device(auth_client)
    listed = auth_client.get(f"{PREFIX}/devices").json()
    assert len(listed) == 1
    # A ten-minute credential that can be re-read has no ten-minute lifetime.
    assert "pairing_code" not in listed[0]


def test_listing_devices_never_shows_another_users(auth_client, client):
    create_device(auth_client, "Mine")
    second_user(client)
    assert client.get(f"{PREFIX}/devices").json() == []


# -- pairing -----------------------------------------------------------------


def test_pairing_exchanges_the_code_for_a_token(auth_client, db_session):
    created = create_device(auth_client)
    paired = pair(auth_client, created["pairing_code"], hostname="WS-ENG-04")

    assert paired["device_id"] == created["device"]["id"]
    assert len(paired["device_token"]) > 40
    assert paired["websocket_path"] == "/api/v1/catia/bridge/ws"

    device = db_session.get(CatiaDevice, paired["device_id"])
    db_session.refresh(device)
    assert device.status is CatiaDeviceStatus.ACTIVE
    # Only the hash is stored. A database leak must not hand over a live
    # connection into an engineer's CAD session.
    assert device.token_hash == hash_token(paired["device_token"])
    assert paired["device_token"] not in json.dumps(
        {c.name: str(getattr(device, c.name)) for c in device.__table__.columns}
    )


def test_a_pairing_code_works_exactly_once(auth_client):
    created = create_device(auth_client)
    pair(auth_client, created["pairing_code"])

    pairing_limiter.reset()
    replay = auth_client.post(f"{PREFIX}/devices/pair", json={"code": created["pairing_code"]})
    assert replay.status_code == 400
    assert "already been used" in replay.json()["detail"]


def test_an_expired_pairing_code_is_refused(auth_client, db_session):
    created = create_device(auth_client)
    device = db_session.get(CatiaDevice, created["device"]["id"])
    device.pairing_expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    pairing_limiter.reset()
    response = auth_client.post(f"{PREFIX}/devices/pair", json={"code": created["pairing_code"]})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_an_unknown_pairing_code_is_refused_without_saying_why(auth_client):
    pairing_limiter.reset()
    response = auth_client.post(f"{PREFIX}/devices/pair", json={"code": "ZZZZZZZZ"})
    assert response.status_code == 400
    # One message for unknown, used and expired: the endpoint is unauthenticated
    # and must not confirm which codes exist.
    assert "not valid, has already been used, or has expired" in response.json()["detail"]


def test_pairing_is_rate_limited(auth_client):
    pairing_limiter.reset()
    codes = [
        auth_client.post(f"{PREFIX}/devices/pair", json={"code": "ZZZZZZZZ"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes, "the one unauthenticated endpoint must be grindable only slowly"


def test_pairing_needs_no_session(client, auth_client):
    """The daemon has no cookie; the code itself is the credential."""
    created = create_device(auth_client)
    client.cookies.clear()
    client.headers.pop("x-csrf-token", None)
    pairing_limiter.reset()
    response = client.post(f"{PREFIX}/devices/pair", json={"code": created["pairing_code"]})
    assert response.status_code == 200, response.text


# -- revocation --------------------------------------------------------------


def test_revoking_kills_the_token_but_keeps_the_row(auth_client, db_session):
    created = create_device(auth_client)
    paired = pair(auth_client, created["pairing_code"])

    assert auth_client.delete(f"{PREFIX}/devices/{paired['device_id']}").status_code == 204

    device = db_session.get(CatiaDevice, paired["device_id"])
    db_session.refresh(device)
    assert device.status is CatiaDeviceStatus.REVOKED
    assert device.token_hash is None
    assert device.revoked_at is not None
    # The row survives because the operation log points at it.
    assert db_session.get(CatiaDevice, paired["device_id"]) is not None


def test_another_users_device_is_404_not_403(auth_client, client):
    created = create_device(auth_client)
    second_user(client)
    # 403 would confirm the id exists; 404 does not.
    assert client.delete(f"{PREFIX}/devices/{created['device']['id']}").status_code == 404


def test_a_device_that_does_not_exist_is_404(auth_client):
    assert auth_client.delete(f"{PREFIX}/devices/does-not-exist").status_code == 404


# -- the bridge socket -------------------------------------------------------


def _ws_rejected(client, **kwargs) -> bool:
    """True when the upgrade is refused. Starlette raises on a pre-accept close."""
    try:
        with client.websocket_connect(f"{PREFIX}/bridge/ws", **kwargs):
            return False
    except Exception:
        return True


def test_the_bridge_socket_refuses_an_unauthenticated_upgrade(client):
    assert _ws_rejected(client)


def test_the_bridge_socket_refuses_an_unknown_token(client):
    assert _ws_rejected(client, headers={"Authorization": "Bearer nope"})


def test_a_token_in_the_query_string_authenticates_nothing(client, auth_client):
    """Query strings land in access logs; the header is the only accepted path."""
    created = create_device(auth_client)
    paired = pair(auth_client, created["pairing_code"])
    token = paired["device_token"]

    try:
        with client.websocket_connect(f"{PREFIX}/bridge/ws?token={token}"):
            raise AssertionError("a query-parameter token must not authenticate")
    except AssertionError:
        raise
    except Exception:
        pass


def test_a_revoked_device_cannot_reconnect(client, auth_client):
    created = create_device(auth_client)
    paired = pair(auth_client, created["pairing_code"])
    auth_client.delete(f"{PREFIX}/devices/{paired['device_id']}")

    assert _ws_rejected(client, headers={"Authorization": f"Bearer {paired['device_token']}"})


def test_an_expired_device_token_cannot_connect(client, auth_client, db_session):
    created = create_device(auth_client)
    paired = pair(auth_client, created["pairing_code"])
    device = db_session.get(CatiaDevice, paired["device_id"])
    device.token_expires_at = utcnow() - timedelta(days=1)
    db_session.commit()

    assert _ws_rejected(client, headers={"Authorization": f"Bearer {paired['device_token']}"})


# -- browser-facing surface --------------------------------------------------


@pytest.fixture
def remote_deployment(monkeypatch):
    """Pin the posture these two tests are about: the daemon is elsewhere.

    Without this they read differently depending on the OS running them. On a
    Windows workstation Kryova starts and pairs the bridge itself, and the
    status detail correctly stops mentioning pairing -- so these assertions
    would fail on a developer machine and pass in CI, which is worse than either
    outcome. The local wording is asserted in test_catia_local_bridge.py.
    """
    from app.catia import local_bridge

    monkeypatch.setattr(local_bridge, "is_supported", lambda: False)


def test_status_reports_nothing_paired_before_any_device_exists(auth_client, remote_deployment):
    status = auth_client.get(f"{PREFIX}/status").json()
    assert status["connected"] is False
    assert status["paired_devices"] == 0
    assert "No workstation has been paired" in status["detail"]


def test_status_distinguishes_paired_from_connected(auth_client, remote_deployment):
    created = create_device(auth_client)
    pair(auth_client, created["pairing_code"])
    status = auth_client.get(f"{PREFIX}/status").json()
    assert status["connected"] is False
    assert status["paired_devices"] == 1
    assert status["detail"] == "No workstation is connected."


def test_status_requires_a_session(client):
    client.cookies.clear()
    assert client.get(f"{PREFIX}/status").status_code == 401


def test_the_tool_list_reports_tiers_so_the_ui_cannot_get_them_wrong(auth_client):
    tools = auth_client.get(f"{PREFIX}/tools").json()["tools"]
    # A deliberate count, so adding or losing a tool is never silent. 44 since
    # the five assembly tools (save_part, new_product, add_component, constrain,
    # list_constraints) joined the eight interactive ones (list_commands,
    # run_command, describe_dialog, fill_dialog, dialog_action, press_key,
    # switch_workbench, select) and the 31 semantic ones.
    assert len(tools) == 44
    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["catia_measure"]["tier"] == "read"
    assert by_name["catia_measure"]["mutating"] is False
    assert by_name["catia_restore"]["tier"] == "destructive"
    assert by_name["catia_pad"]["tier"] == "write"
    assert by_name["catia_list_features"]["tier"] == "read"
    assert by_name["catia_shaft"]["tier"] == "write"
    assert by_name["catia_delete_feature"]["tier"] == "write"
    # Reading the interface is a read; driving it is a write, because pressing
    # OK on a dialog builds geometry exactly as catia_pad does.
    assert by_name["catia_describe_dialog"]["tier"] == "read"
    assert by_name["catia_list_commands"]["tier"] == "read"
    assert by_name["catia_run_command"]["tier"] == "write"
    assert by_name["catia_dialog_action"]["mutating"] is True
    # Assembling is writing: placing a component and constraining it change the
    # product exactly as padding changes a part. Only listing is a read.
    assert by_name["catia_save_part"]["tier"] == "write"
    assert by_name["catia_new_product"]["tier"] == "write"
    assert by_name["catia_add_component"]["tier"] == "write"
    assert by_name["catia_constrain"]["tier"] == "write"
    assert by_name["catia_constrain"]["mutating"] is True
    assert by_name["catia_list_constraints"]["tier"] == "read"
    assert by_name["catia_list_constraints"]["mutating"] is False


def _drain(response, count: int, timeout: float = 5.0) -> list[str]:
    """Pull `count` chunks out of a StreamingResponse.

    The SSE stream is endless by design, and Starlette's TestClient buffers a
    whole response body before returning it -- so `client.stream("GET", ...)`
    deadlocks on this endpoint before the first byte reaches the caller. That is
    a property of the test transport, not of the route (a browser's EventSource
    consumes it incrementally and is fine), so the route's own iterator is
    driven here instead of going through HTTP.
    """

    async def pull() -> list[str]:
        chunks: list[str] = []
        iterator = response.body_iterator.__aiter__()
        for _ in range(count):
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
            chunks.append(chunk if isinstance(chunk, str) else bytes(chunk).decode())
        if hasattr(response.body_iterator, "aclose"):
            await response.body_iterator.aclose()
        return chunks

    return asyncio.run(pull())


def test_the_event_stream_is_declared_as_sse_and_unbuffered(auth_client, db_session):
    user = db_session.get(User, auth_client.get("/api/v1/auth/me").json()["id"])
    response = catia_events(user)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    # Without this nginx buffers the stream and every event arrives at once, at
    # the end, which is the opposite of the point.
    assert response.headers["x-accel-buffering"] == "no"
    _drain(response, 1)


def test_the_event_stream_opens_with_a_live_frame(auth_client, db_session):
    user = db_session.get(User, auth_client.get("/api/v1/auth/me").json()["id"])
    first = _drain(catia_events(user), 1)[0]
    # A first frame straight away, so the client knows the stream is live rather
    # than merely accepted.
    assert first.startswith("data: ")
    assert json.loads(first[6:])["event"] == "stream_open"


def test_the_event_stream_delivers_a_published_event(auth_client, db_session, current_user_id):
    user = db_session.get(User, current_user_id)
    response = catia_events(user)
    opening = _drain_and_publish(response, current_user_id)
    payload = json.loads(opening[6:])
    assert payload["event"] == "document_saved"
    assert payload["data"]["doc_name"] == "Bracket"


def _drain_and_publish(response, user_id: str) -> str:
    async def pull() -> str:
        iterator = response.body_iterator.__aiter__()
        await asyncio.wait_for(iterator.__anext__(), 5.0)  # stream_open
        bus.publish(
            user_id,
            {"event": "document_saved", "at": "now", "data": {"doc_name": "Bracket"}},
        )
        chunk = await asyncio.wait_for(iterator.__anext__(), 5.0)
        if hasattr(response.body_iterator, "aclose"):
            await response.body_iterator.aclose()
        return chunk if isinstance(chunk, str) else bytes(chunk).decode()

    return asyncio.run(pull())


def test_the_event_stream_emits_keepalive_comments(auth_client, db_session, monkeypatch):
    """Without them a proxy reaps an idle stream and 'quiet' reads as 'gone'."""
    monkeypatch.setattr("app.api.routes.catia._SSE_KEEPALIVE_S", 0.05)
    user = db_session.get(User, auth_client.get("/api/v1/auth/me").json()["id"])
    chunks = _drain(catia_events(user), 3)
    assert chunks[0].startswith("data: ")
    assert all(chunk == ": keepalive\n\n" for chunk in chunks[1:])


def test_one_users_events_never_reach_another(auth_client, db_session, current_user_id):
    user = db_session.get(User, current_user_id)
    response = catia_events(user)

    async def pull() -> str | None:
        iterator = response.body_iterator.__aiter__()
        await asyncio.wait_for(iterator.__anext__(), 5.0)  # stream_open
        bus.publish("a-different-user", {"event": "document_saved", "data": {}})
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), 0.3)
        except TimeoutError:
            return None
        finally:
            if hasattr(response.body_iterator, "aclose"):
                await response.body_iterator.aclose()
        return chunk if isinstance(chunk, str) else bytes(chunk).decode()

    assert asyncio.run(pull()) is None


def test_approvals_are_refused_for_tools_that_are_not_destructive(auth_client):
    response = auth_client.post(
        f"{PREFIX}/approvals",
        json={"tool": "catia_pad", "checkpoint_id": "whatever"},
    )
    assert response.status_code == 422
    assert "needs no approval" in response.json()["detail"]


def test_an_approval_for_an_unknown_checkpoint_is_404(auth_client):
    response = auth_client.post(
        f"{PREFIX}/approvals",
        json={"tool": "catia_restore", "checkpoint_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_checkpoints_for_an_unknown_conversation_are_404(auth_client):
    assert auth_client.get(f"{PREFIX}/conversations/nope/checkpoints").status_code == 404


def test_devices_are_scoped_by_owner_in_the_database(auth_client, client, db_session):
    create_device(auth_client, "Mine")
    second_user(client)
    create_device(client, "Theirs")

    owners = {device.name: device.owner_id for device in db_session.scalars(select(CatiaDevice))}
    assert len(set(owners.values())) == 2
