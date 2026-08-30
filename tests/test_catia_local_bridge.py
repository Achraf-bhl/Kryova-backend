"""Kryova opens and drives CATIA itself, or it is not the product it claims.

The bug these tests pin, observed end to end: "Create a cylindrical shaft 30 mm
by 150 mm in steel and tell me its mass." The assistant launched CATIA, created
`Part1.CATPart` -- and then answered "the Kryova CATIA bridge isn't yet
attached... pair your workstation, or upload a CAD file", to a user who had
CATIA open on screen holding the part it had just made.

Nothing was broken. `open_in_catia` drives COM directly and needs no pairing;
the `catia_*` modelling tools go through a daemon that has to be paired and
running, and on a single-machine install nothing ever started it. The pairing
ceremony authenticates *another* machine's daemon to an account, so on this one
it was the server asking itself for a code.

Three rules follow.

**Nothing is asked of the user.** The device is provisioned, the token minted
and the daemon started by the server. The messages on this path are tested for
what they tell the model to do about a bridge that is not up yet, because
"ask them to pair a workstation" is the answer that produced the bug.

**Except consent.** A revoked device is never resurrected, and a hosted
deployment can refuse the whole mechanism with one setting.

**A failure stays cheap.** No CATIA, no pywin32, no daemon package: the attempt
is made once and then left alone, not repeated on every agent turn.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.catia import local_bridge
from app.core.security import hash_token
from app.models import User
from app.models.catia import CatiaDevice, CatiaDeviceStatus


@pytest.fixture(autouse=True)
def _reset_supervisor() -> Any:
    """Module state is global; a test must not inherit the previous one's."""
    local_bridge._process = None
    local_bridge._last_attempt = 0.0
    local_bridge._last_error = None
    local_bridge._tokens.clear()
    yield
    local_bridge._process = None
    local_bridge._last_attempt = 0.0
    local_bridge._last_error = None
    local_bridge._tokens.clear()


@pytest.fixture
def user(db_session: Session) -> User:
    from app.core.security import hash_password

    account = User(
        email="local-bridge@kryova.dev",
        hashed_password=hash_password("a-long-enough-password"),
    )
    db_session.add(account)
    db_session.commit()
    return account


class _FakeProcess:
    """A spawned daemon that is alive until told otherwise."""

    def __init__(self, returncode: int | None = None) -> None:
        self.pid = 4321
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture spawns instead of starting a real daemon."""
    calls: list[dict[str, Any]] = []

    def fake_popen(argv: list[str], **kwargs: Any) -> _FakeProcess:
        calls.append({"argv": argv, **kwargs})
        return _FakeProcess()

    monkeypatch.setattr(local_bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_bridge, "is_supported", lambda: True)
    return calls


class TestWhenItIsAllowedToRun:
    def test_a_hosted_deployment_can_refuse_outright(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The server is not the user's workstation there, so spawning processes
        # on behalf of an account would be someone else's machine entirely.
        monkeypatch.setattr(local_bridge.settings, "catia_local_bridge", False)
        assert local_bridge.is_supported() is False

    def test_it_is_off_when_catia_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_bridge.settings, "catia_enabled", False)
        assert local_bridge.is_supported() is False

    def test_it_is_windows_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_bridge.sys, "platform", "linux")
        assert local_bridge.is_supported() is False

    def test_unsupported_starts_nothing(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_bridge, "is_supported", lambda: False)
        monkeypatch.setattr(
            local_bridge.subprocess,
            "Popen",
            lambda *a, **k: pytest.fail("must not spawn"),
        )
        assert local_bridge.ensure_started(db_session, user.id) is False


class TestProvisioning:
    def test_it_pairs_itself_without_a_code(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)

        device = local_bridge._local_device(db_session, user.id)
        assert device is not None
        # ACTIVE without anyone having redeemed a pairing code: that is the
        # whole point on a machine where the server minted the token itself.
        assert device.status is CatiaDeviceStatus.ACTIVE
        assert device.pairing_code is None
        assert device.token_hash

    def test_only_the_hash_is_stored(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        device = local_bridge._local_device(db_session, user.id)
        assert device is not None

        token = spawned[0]["env"]["KRYOVA_BRIDGE_TOKEN"]
        # Same rule as the hand-paired path: a database leak must not hand
        # anyone a live connection into the engineer's CAD session.
        assert token != device.token_hash
        assert hash_token(token) == device.token_hash

    def test_the_token_goes_over_the_environment_not_a_file(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        # A supervised daemon should leave no credential behind for whoever sits
        # at the machine next.
        local_bridge.ensure_started(db_session, user.id)
        environment = spawned[0]["env"]
        assert environment["KRYOVA_BRIDGE_TOKEN"]
        assert environment["KRYOVA_BRIDGE_SERVER"].startswith("http")

    def test_the_daemon_waits_for_catia_rather_than_exiting(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        # Kryova comes up before CATIA does. Without this the daemon would exit
        # seconds after login and the CATIA tools would never appear, no matter
        # what the user did next.
        local_bridge.ensure_started(db_session, user.id)
        assert "--wait-for-catia" in spawned[0]["argv"]
        assert spawned[0]["argv"][:2] == [sys.executable, "-m"]

    def test_it_reuses_its_device_row(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        local_bridge._process = None
        local_bridge._last_attempt = 0.0
        local_bridge.ensure_started(db_session, user.id)

        devices = [
            d
            for d in db_session.query(CatiaDevice).filter(CatiaDevice.owner_id == user.id).all()
            if d.name == local_bridge.LOCAL_DEVICE_NAME
        ]
        assert len(devices) == 1
        # Same token too: a restart must not invalidate the credential a
        # still-connected daemon is holding.
        assert spawned[0]["env"]["KRYOVA_BRIDGE_TOKEN"] == spawned[1]["env"]["KRYOVA_BRIDGE_TOKEN"]

    def test_a_hand_paired_workstation_is_left_alone(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        theirs = CatiaDevice(owner_id=user.id, name="Design office PC")
        theirs.token_hash = hash_token("their-token")
        theirs.status = CatiaDeviceStatus.ACTIVE
        db_session.add(theirs)
        db_session.commit()

        local_bridge.ensure_started(db_session, user.id)
        db_session.refresh(theirs)
        assert theirs.token_hash == hash_token("their-token")
        assert theirs.name == "Design office PC"


class TestConsent:
    def test_a_revoked_device_is_never_resurrected(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        # Revoking is the user saying no. An auto-pairing that undoes it is a
        # back door with a friendly name.
        device = CatiaDevice(owner_id=user.id, name=local_bridge.LOCAL_DEVICE_NAME)
        device.status = CatiaDeviceStatus.REVOKED
        db_session.add(device)
        db_session.commit()

        assert local_bridge.ensure_started(db_session, user.id) is False
        assert not spawned
        db_session.refresh(device)
        assert device.status is CatiaDeviceStatus.REVOKED
        assert "revoked" in (local_bridge.last_error() or "").lower()


class TestFailingCheaply:
    def test_a_failed_start_is_not_retried_every_turn(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[int] = []

        def failing_popen(argv: list[str], **kwargs: Any) -> Any:
            attempts.append(1)
            raise OSError("python is not on the path")

        monkeypatch.setattr(local_bridge, "is_supported", lambda: True)
        monkeypatch.setattr(local_bridge.subprocess, "Popen", failing_popen)

        for _ in range(5):
            assert local_bridge.ensure_started(db_session, user.id) is False
        assert len(attempts) == 1
        assert local_bridge.last_error()

    def test_a_daemon_that_exits_immediately_is_reported_not_awaited(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_bridge, "is_supported", lambda: True)
        monkeypatch.setattr(
            local_bridge.subprocess,
            "Popen",
            lambda argv, **kwargs: _FakeProcess(returncode=1),
        )

        assert local_bridge.ensure_started(db_session, user.id, wait_s=5.0) is False
        # Told where to look, rather than "unavailable".
        assert "local-bridge.log" in (local_bridge.last_error() or "")

    def test_a_broken_database_does_not_break_the_turn(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_bridge, "is_supported", lambda: True)

        def exploding(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("connection reset")

        monkeypatch.setattr(local_bridge, "_local_device", exploding)
        assert local_bridge.ensure_started(db_session, user.id) is False


class TestStopping:
    def test_shutdown_kills_the_daemon_it_started(self) -> None:
        process = _FakeProcess()
        local_bridge._process = process
        local_bridge.stop()
        assert process.terminated
        assert local_bridge._process is None

    def test_stopping_twice_is_harmless(self) -> None:
        local_bridge.stop()
        local_bridge.stop()


class TestNothingAsksTheUserToPair:
    def test_the_unavailable_message_points_at_open_in_catia(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.catia.dispatch import CatiaUnavailable, _resolve_connection

        monkeypatch.setattr(local_bridge, "is_supported", lambda: True)
        monkeypatch.setattr(local_bridge, "ensure_started", lambda *a, **k: False)

        with pytest.raises(CatiaUnavailable) as excinfo:
            _resolve_connection(db_session, user.id)

        message = str(excinfo.value).lower()
        assert "open_in_catia" in message
        # It tells the model to do the thing itself, and forbids by name the two
        # hand-offs it reached for instead: pairing and asking for a file.
        assert "do not ask the user to pair a workstation" in message
        assert "ask the user to start" not in message

    def test_the_status_detail_does_not_mention_pairing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.catia.dispatch import _offline_detail

        monkeypatch.setattr(local_bridge, "is_supported", lambda: True)
        monkeypatch.setattr(local_bridge, "last_error", lambda: None)

        detail = _offline_detail([]).lower()
        assert "pair" not in detail
        assert "catia is open" in detail

    def test_a_remote_deployment_still_explains_pairing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The ceremony is right when the daemon really is on another machine;
        # this must not be deleted along with the local case.
        from app.catia.dispatch import _offline_detail

        monkeypatch.setattr(local_bridge, "is_supported", lambda: False)
        assert "paired" in _offline_detail([]).lower()

    def test_the_state_block_tells_the_model_not_to_ask(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ai import state as state_module

        monkeypatch.setattr(state_module, "_local_bridge_supported", lambda: True)

        class _Conversation:
            catia_state: dict[str, Any] = {}

        lines = state_module._catia_lines(_Conversation(), False, None)
        bridge_line = next(line for line in lines if line.startswith("catia_bridge:"))
        assert "open_in_catia" in bridge_line
        assert "never ask the user to pair" in bridge_line.lower()


class TestTheDaemonAcceptsHandedOverCredentials:
    def test_the_environment_beats_a_stale_config_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sys.path.insert(0, str(local_bridge.BASE_DIR / "scripts"))
        from catia_bridge import config as bridge_config

        monkeypatch.setenv(bridge_config.ENV_TOKEN, "handed-over")
        monkeypatch.setenv(bridge_config.ENV_SERVER, "http://127.0.0.1:8000")
        monkeypatch.setenv(bridge_config.ENV_DEVICE_ID, "dev-1")

        loaded = bridge_config.load()
        assert loaded.device_token == "handed-over"
        assert loaded.device_id == "dev-1"
        # And it derives the socket URL from that server, not from a second
        # setting that could disagree with it.
        assert loaded.websocket_url == "ws://127.0.0.1:8000/api/v1/catia/bridge/ws"

    def test_no_environment_means_the_file_as_before(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sys.path.insert(0, str(local_bridge.BASE_DIR / "scripts"))
        from catia_bridge import config as bridge_config

        monkeypatch.delenv(bridge_config.ENV_TOKEN, raising=False)
        assert bridge_config.load_env() is None


class TestItDoesNotOpenAConsoleWindow:
    def test_the_daemon_is_spawned_hidden(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        # A bare Popen of a console app throws a window over the CAD session the
        # engineer is working in.
        local_bridge.ensure_started(db_session, user.id)
        expected = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        assert spawned[0]["creationflags"] == expected


class TestASupersededDaemonStopsInsteadOfLooping:
    """An orphaned daemon used to re-offer a dead token every 47 seconds.

    The supervisor mints a fresh token per server process, so a backend restart
    invalidates the credential the previous daemon is holding. That daemon then
    failed its handshake with HTTP 403 and treated it like any other connection
    failure: back off, reconnect, fail again, forever. Six of them were found
    running on this machine after an afternoon of restarts.

    A 401 or 403 on the handshake is an answer, not an outage. Retrying cannot
    change it, so the daemon exits and lets whatever supervises it decide.
    """

    @pytest.fixture
    def rejected(self) -> Any:
        sys.path.insert(0, str(local_bridge.BASE_DIR / "scripts"))
        from catia_bridge.bridge import _is_auth_rejection

        return _is_auth_rejection

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_refused_credential_is_recognised_from_the_status(
        self, rejected: Any, status: int
    ) -> None:
        class Rejection(Exception):
            status_code = status

        assert rejected(Rejection("refused")) is True

    @pytest.mark.parametrize("status", [401, 403])
    def test_and_from_the_message_when_the_status_is_not_exposed(
        self, rejected: Any, status: int
    ) -> None:
        # websockets has carried the code on different attributes across
        # versions; this is the text it actually logged.
        message = f"server rejected WebSocket connection: HTTP {status}"
        assert rejected(Exception(message)) is True

    def test_and_from_a_nested_response(self, rejected: Any) -> None:
        class Response:
            status_code = 403

        class Rejection(Exception):
            response = Response()

        assert rejected(Rejection("refused")) is True

    @pytest.mark.parametrize(
        "failure",
        [
            ConnectionRefusedError("the server is not up yet"),
            TimeoutError("no route"),
            Exception("server rejected WebSocket connection: HTTP 500"),
            Exception("server rejected WebSocket connection: HTTP 503"),
        ],
    )
    def test_a_real_outage_is_still_retried(self, rejected: Any, failure: Exception) -> None:
        # The daemon is started before CATIA and before the server is listening,
        # so exiting on an ordinary connection failure would defeat the point.
        assert rejected(failure) is False
