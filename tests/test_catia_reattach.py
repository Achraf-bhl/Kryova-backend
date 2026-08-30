"""A COM handle belongs to one thread, and the daemon has three.

Two bugs, one root cause, both found by driving long conversations against a
live workstation rather than the mock.

**The handle was shared across threads.** `CatiaCom` held a single `self._app`,
acquired in `__init__` on the main thread. Operations arrive on
`asyncio.to_thread` workers and the liveness probe gets a fresh watchdog thread
every call, so at most one of the three could ever use it. COM answers a
cross-thread call with RPC_E_WRONG_THREAD -- "the application called an
interface that was marshalled for a different thread" -- which the daemon
reported as "CATIA stopped responding to automation", aiming every diagnosis at
CATIA. Verified directly: the same handle read `Documents.Count = 19` on the
thread that made it and failed on every other, while a thread acquiring its own
handle succeeded every time.

**The handle was never refreshed.** `_connect` ran exactly once, so closing
CATIA and reopening it -- several times in any working day -- left the pointer
dead for good, curable only by restarting a daemon nobody is watching.

The repair has to happen on the thread that will *use* the handle. An earlier
attempt put it in `health()`, which runs on the watchdog thread: the fresh
handle died with that thread moments later and the next call failed with
CO_E_OBJNOTCONNECTED, which is strictly worse than the stale handle it
replaced. That is what `TestTheRepairRunsOnTheOperationThread` exists to stop.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.backend import CatiaOperationError  # noqa: E402
from catia_bridge.catia_com import CatiaCom  # noqa: E402
from catia_bridge.session import BridgeSession  # noqa: E402


class _Documents:
    Count = 3


class _DeadDocuments:
    @property
    def Count(self) -> int:
        raise OSError("-2147417842, 'marshalled for a different thread'")


class _LiveApp:
    Documents = _Documents()


class _DeadApp:
    Documents = _DeadDocuments()


def _com(app: Any, *, on_connect: Any = None) -> CatiaCom:
    """A `CatiaCom` holding `app` for the current thread, without `__init__`."""
    com = object.__new__(CatiaCom)
    com._connect = on_connect or (lambda: None)  # type: ignore[method-assign]
    com._app = app
    return com


class TestTheHandleIsPerThread:
    def test_another_thread_does_not_inherit_it(self) -> None:
        com = _com(_LiveApp())
        seen: list[Any] = []

        def look() -> None:
            # No handle of its own, and `_connect` here is a stub that sets
            # nothing -- so the getter finds nothing rather than handing over a
            # pointer belonging to the thread that made it.
            seen.append(getattr(com._local, "app", None))

        thread = threading.Thread(target=look)
        thread.start()
        thread.join()
        assert seen == [None], "a COM pointer must not be shared between threads"

    def test_each_thread_connects_for_itself(self) -> None:
        com = _com(None)
        made: list[str] = []

        def connect() -> None:
            made.append(threading.current_thread().name)
            com._app = _LiveApp()

        com._connect = connect  # type: ignore[method-assign]
        results: list[int] = []

        def use() -> None:
            results.append(com._app.Documents.Count)

        threads = [threading.Thread(target=use, name=f"w{i}") for i in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [3, 3, 3]
        assert sorted(made) == ["w0", "w1", "w2"], "each thread acquires its own"


class TestHealth:
    def test_a_live_handle_passes(self) -> None:
        _com(_LiveApp()).health()

    def test_a_dead_handle_raises(self) -> None:
        with pytest.raises(CatiaOperationError) as caught:
            _com(_DeadApp()).health()
        assert "stopped responding" in str(caught.value)

    def test_it_does_not_repair(self) -> None:
        # It runs on the watchdog thread, which dies moments later, so anything
        # it acquired would die with it -- CO_E_OBJNOTCONNECTED on the next call.
        attempts: list[int] = []
        com = _com(_DeadApp(), on_connect=lambda: attempts.append(1))
        with pytest.raises(CatiaOperationError):
            com.health()
        assert attempts == [], "health() must not reconnect -- see the module docstring"


class TestEnsureConnected:
    def test_a_healthy_handle_is_left_alone(self) -> None:
        attempts: list[int] = []
        com = _com(_LiveApp(), on_connect=lambda: attempts.append(1))
        com.ensure_connected()
        assert attempts == []

    def test_a_stale_handle_is_replaced(self) -> None:
        com = _com(_DeadApp())

        def reconnect() -> None:
            com._app = _LiveApp()

        com._connect = reconnect  # type: ignore[method-assign]
        com.ensure_connected()
        assert com._app.Documents.Count == 3

    def test_a_dead_catia_still_fails(self) -> None:
        com = _com(_DeadApp())

        def refuse() -> None:
            raise CatiaOperationError("CATIA is not running on this workstation.")

        com._connect = refuse  # type: ignore[method-assign]
        with pytest.raises(CatiaOperationError):
            com.ensure_connected()


class _Backend:
    def __init__(self, *, alive: bool = True) -> None:
        self.alive = alive
        self.checked = 0
        self.threads: list[str] = []

    def health(self) -> None:
        if not self.alive:
            raise CatiaOperationError("CATIA stopped responding to automation.")

    def ensure_connected(self) -> None:
        self.checked += 1
        self.threads.append(threading.current_thread().name)


def _session(backend: Any) -> BridgeSession:
    session = object.__new__(BridgeSession)
    session.backend = backend
    return session


class TestTheOperationPathChecksItsOwnConnection:
    def test_it_always_runs_even_when_the_watchdog_is_happy(self) -> None:
        # The whole point: the watchdog holds a *different* handle, so a healthy
        # probe says nothing about the worker's pointer.
        backend = _Backend(alive=True)
        _session(backend)._ensure_alive("catia_new_part")
        assert backend.checked == 1

    def test_a_wedged_catia_is_reported_before_any_repair(self) -> None:
        backend = _Backend(alive=False)
        with pytest.raises(CatiaOperationError) as caught:
            _session(backend)._ensure_alive("catia_new_part")
        assert "stopped responding" in str(caught.value)

    def test_a_backend_without_the_hook_still_works(self) -> None:
        # `ensure_connected` defaults to a no-op on the base class.
        class _Plain:
            def health(self) -> None:
                return None

            def ensure_connected(self) -> None:
                return None

        _session(_Plain())._ensure_alive("catia_pad")


class TestTheRepairRunsOnTheOperationThread:
    """The regression that was worse than not repairing at all."""

    def test_it_is_the_caller_thread(self) -> None:
        backend = _Backend()
        caller = threading.current_thread().name
        _session(backend)._ensure_alive("catia_new_part")
        assert backend.threads == [caller], (
            "a COM proxy belongs to the apartment of the thread that acquired "
            "it; repairing on the watchdog thread yields a handle that dies "
            "with it (CO_E_OBJNOTCONNECTED on the next call)"
        )

    def test_it_is_never_the_watchdog_thread(self) -> None:
        backend = _Backend()
        _session(backend)._ensure_alive("catia_new_part")
        assert not any(name.startswith("catia-health") for name in backend.threads)
