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
from pathlib import Path
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
    local_bridge._process_user_id = None
    local_bridge._last_handover = 0.0
    local_bridge._last_attempt = 0.0
    local_bridge._last_error = {}
    yield
    local_bridge._process = None
    local_bridge._process_user_id = None
    local_bridge._last_handover = 0.0
    local_bridge._last_attempt = 0.0
    local_bridge._last_error = {}


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
        assert "revoked" in (local_bridge.last_error(user.id) or "").lower()


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
        assert local_bridge.last_error(user.id)

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
        assert "local-bridge.log" in (local_bridge.last_error(user.id) or "")

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
        monkeypatch.setattr(local_bridge, "last_error", lambda *a: None)

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


class TestTwoBackendsDoNotFightOverTheToken:
    """The failure that made the whole feature useless on the real machine.

    A random token minted per server process looks right and is not. Two
    backends were up -- one left over, one started by the desktop app -- and
    both supervise the same device row. Each mint overwrote the other's hash,
    so whichever daemon connected always presented a credential that had just
    been invalidated. The log is 403 after 403 after 403, and the assistant
    waited 25 seconds per `catia_*` call for a bridge that could never attach:

        18:31:48 Connecting to ws://127.0.0.1:8000/api/v1/catia/bridge/ws
        18:31:49 ERROR The server rejected this workstation's credentials (403)

    Deriving the token from the signing key and the device id removes the race
    rather than narrowing it. Any two processes now compute the same answer.
    """

    def test_two_processes_derive_the_same_token(self) -> None:
        assert local_bridge._device_token("device-1") == local_bridge._device_token("device-1")

    def test_a_different_device_gets_a_different_token(self) -> None:
        assert local_bridge._device_token("device-1") != local_bridge._device_token("device-2")

    def test_rotating_the_signing_key_rotates_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        before = local_bridge._device_token("device-1")
        monkeypatch.setattr(local_bridge.settings, "secret_key", "a-different-signing-key")
        assert local_bridge._device_token("device-1") != before

    def test_it_is_not_the_signing_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(local_bridge.settings, "secret_key", "the-actual-secret")
        assert "the-actual-secret" not in local_bridge._device_token("device-1")

    def test_a_second_supervisor_does_not_invalidate_the_first(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        # Simulating the two backends: provision twice, as two processes would,
        # and check the first daemon's token still authenticates afterwards.
        first = local_bridge._provision(db_session, user.id)
        assert first is not None
        device, token = first

        second = local_bridge._provision(db_session, user.id)
        assert second is not None

        db_session.refresh(device)
        assert device.token_hash == hash_token(token)

    def test_the_stored_hash_is_not_rewritten_every_turn(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        provisioned = local_bridge._provision(db_session, user.id)
        assert provisioned is not None
        device, _ = provisioned
        expiry = device.token_expires_at

        local_bridge._provision(db_session, user.id)
        db_session.refresh(device)
        # Untouched, so nothing downstream sees a credential change that is not one.
        assert device.token_expires_at == expiry


class TestOnlyOneDaemonPerMachine:
    """Two daemons for one device flap; they do not coexist.

    The server's registry keeps the newest socket and closes the older, whose
    owner reconnects and displaces it straight back. Duplicates are easy to
    reach -- the backend supervises one, the desktop app spawns another -- so
    the daemon refuses to be the second rather than trusting its launcher.
    """

    @pytest.fixture
    def bridge_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        sys.path.insert(0, str(local_bridge.BASE_DIR / "scripts"))
        from catia_bridge import config as bridge_config

        monkeypatch.setattr(bridge_config, "work_dir", lambda: tmp_path)
        return bridge_config

    def test_the_first_daemon_takes_the_lock(self, bridge_config: Any) -> None:
        handle = bridge_config.acquire_single_instance_lock()
        try:
            assert bridge_config.lock_path().exists()
        finally:
            handle.close()

    def test_the_second_is_refused(self, bridge_config: Any) -> None:
        handle = bridge_config.acquire_single_instance_lock()
        try:
            with pytest.raises(bridge_config.AlreadyRunning, match="already running"):
                bridge_config.acquire_single_instance_lock()
        finally:
            handle.close()

    def test_the_lock_is_released_when_the_holder_lets_go(self, bridge_config: Any) -> None:
        # Windows drops the lock when the process dies, so a killed daemon must
        # not leave the machine unable to start another.
        bridge_config.acquire_single_instance_lock().close()
        second = bridge_config.acquire_single_instance_lock()
        second.close()


class TestTheStatusPanelBringsTheBridgeUp:
    """The badge is what the user checks *before* asking for anything.

    The device row is created on demand, so a fresh account had none and the
    panel read "not connected" until a message had already been sent -- exactly
    backwards for an indicator whose job is to say whether asking is worth it.
    Asking whether the bridge is up is also the moment to bring it up.
    """

    def test_status_provisions_the_local_device(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        from app.catia.dispatch import status_payload

        assert local_bridge._local_device(db_session, user.id) is None
        status_payload(db_session, user.id, None)
        assert local_bridge._local_device(db_session, user.id) is not None
        assert spawned

    def test_it_does_not_block_the_call(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A status endpoint that waits 25 seconds for a socket is a status
        # endpoint nobody can poll.
        from app.catia import dispatch

        waits: list[float] = []

        def record(db: Any, user_id: str, *, wait_s: float = 0.0) -> bool:
            waits.append(wait_s)
            return False

        monkeypatch.setattr(dispatch.local_bridge, "ensure_started", record)
        dispatch.status_payload(db_session, user.id, None)
        assert waits == [0.0]


class TestTheBridgeFollowsThePersonUsingTheMachine:
    """One daemon per machine, and it belongs to whoever is using the machine.

    The bug this pins, observed live: an automated test account's daemon held
    the machine's single daemon slot, and the engineer's own account -- in the
    actual Kryova UI -- was told "no CATIA bridge is connected" on every turn.
    `ensure_started` saw a live process, spawned nothing, and waited the full
    connect timeout for a connection on a device that process was never paired
    as. A desktop workstation has one person at it; the daemon follows them.
    """

    @pytest.fixture
    def second_user(self, db_session: Session) -> User:
        from app.core.security import hash_password

        account = User(
            email="the-actual-engineer@kryova.dev",
            hashed_password=hash_password("another-long-password"),
        )
        db_session.add(account)
        db_session.commit()
        return account

    def test_a_daemon_held_by_another_account_is_handed_over(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        first_process = local_bridge._process
        assert first_process is not None

        local_bridge.ensure_started(db_session, second_user.id)

        assert first_process.terminated, "the old account's daemon must be stopped"
        assert len(spawned) == 2, "a daemon must be spawned for the new account"
        second_device = local_bridge._local_device(db_session, second_user.id)
        assert second_device is not None
        assert spawned[1]["env"]["KRYOVA_BRIDGE_DEVICE_ID"] == second_device.id

    def test_the_handover_is_not_blocked_by_the_other_accounts_cooldown(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        # The first account's spawn SUCCEEDED, so its cooldown timestamp is
        # fresh -- and must not make the second account sit out a minute with
        # "no bridge" while a healthy daemon for the wrong device runs.
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, second_user.id)
        assert len(spawned) == 2

    def test_a_fresh_handover_is_held_against_the_next_taker(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        # Two accounts polling at once must not kill each other's daemon every
        # couple of seconds -- observed live as an attach/connect/respawn loop
        # in which neither account ever held a usable connection.
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, second_user.id)
        taken = local_bridge._process
        assert taken is not None

        assert local_bridge.ensure_started(db_session, user.id) is False
        assert local_bridge._process is taken
        assert not taken.terminated
        assert len(spawned) == 2, "the hold must stop a third spawn"

    def test_the_hold_expires_so_the_machine_can_change_hands(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, second_user.id)
        # Walk the hold clock back rather than sleeping through it.
        local_bridge._last_handover -= local_bridge.HANDOVER_HOLD_S + 1

        local_bridge.ensure_started(db_session, user.id)
        assert len(spawned) == 3
        device = local_bridge._local_device(db_session, user.id)
        assert device is not None
        assert spawned[2]["env"]["KRYOVA_BRIDGE_DEVICE_ID"] == device.id

    def test_the_same_account_keeps_its_daemon(
        self, db_session: Session, user: User, spawned: list[dict[str, Any]]
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, user.id)
        assert len(spawned) == 1, "a live daemon for the same account is left alone"
        assert local_bridge._process is not None
        assert not local_bridge._process.terminated

    def test_one_accounts_reason_is_never_shown_to_another(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        # A single global error string told the account that actually OWNED the
        # daemon that somebody else had it.
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, second_user.id)
        local_bridge.ensure_started(db_session, user.id)  # refused by the hold

        assert "another signed-in account" in (local_bridge.last_error(user.id) or "")
        assert local_bridge.last_error(second_user.id) is None

    def test_each_account_gets_its_own_device_row(
        self,
        db_session: Session,
        user: User,
        second_user: User,
        spawned: list[dict[str, Any]],
    ) -> None:
        local_bridge.ensure_started(db_session, user.id)
        local_bridge.ensure_started(db_session, second_user.id)
        first = local_bridge._local_device(db_session, user.id)
        second = local_bridge._local_device(db_session, second_user.id)
        assert first is not None and second is not None
        assert first.id != second.id
        assert first.owner_id == user.id
        assert second.owner_id == second_user.id
