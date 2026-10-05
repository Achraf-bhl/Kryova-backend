"""Warming the geometry kernel at startup (ROAD_TO_10 6.7).

The claim is about what the warm-up must *not* do -- hold up the boot, hold up `/health`, fail it --
more than about what it does, so those are the tests.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from app import main
from app.core.config import settings


@pytest.fixture
def warm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "warm_geometry_kernel", True)


class TestTheWarmUpNeverHoldsAnythingUp:
    def test_it_returns_before_a_slow_import_finishes(self, warm) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow() -> None:
            started.set()
            release.wait(10)

        began = time.monotonic()
        thread = main._warm_geometry_kernel(slow)
        assert time.monotonic() - began < 1.0
        assert thread is not None and started.wait(5) and thread.is_alive()
        release.set()
        thread.join(5)
        assert not thread.is_alive()

    def test_the_thread_is_a_daemon_so_it_cannot_keep_the_process_alive(self, warm) -> None:
        release = threading.Event()
        thread = main._warm_geometry_kernel(lambda: release.wait(10))
        try:
            assert thread is not None and thread.daemon
        finally:
            release.set()

    def test_a_failing_import_is_logged_and_nothing_else(
        self, warm, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken() -> None:
            raise ImportError("no OCP on this machine")

        with caplog.at_level(logging.ERROR, logger="app.main"):
            thread = main._warm_geometry_kernel(broken)
            assert thread is not None
            thread.join(5)
        assert "could not be warmed up" in caplog.text

    def test_a_success_says_how_long_it_took(self, warm, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="app.main"):
            thread = main._warm_geometry_kernel(lambda: None)
            assert thread is not None
            thread.join(5)
        assert "geometry kernel warmed in" in caplog.text


class TestItCanBeSwitchedOff:
    def test_off_starts_no_thread_and_imports_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "warm_geometry_kernel", False)
        called: list[int] = []
        assert main._warm_geometry_kernel(lambda: called.append(1)) is None
        assert called == []

    def test_the_suite_runs_with_it_off(self) -> None:
        # The autouse fixture in conftest, so a test run never imports OCCT behind someone's back.
        assert settings.warm_geometry_kernel is False


class TestTheLifespanCallsIt:
    def test_startup_warms_the_kernel_after_the_router(self) -> None:
        import inspect

        source = inspect.getsource(main.lifespan)
        assert source.index("_warm_intent_router()") < source.index("_warm_geometry_kernel()")

    def test_the_real_import_is_the_one_the_first_geometry_call_makes(self) -> None:
        # Not run here (it would import OCCT): the claim is that the warm-up touches the binding
        # module the kernel itself goes through, so a rename that orphans it fails this test.
        import inspect

        assert "binding.symbol" in inspect.getsource(main._import_kernel)
        from app.kernel.occt import binding

        assert callable(binding.symbol)
