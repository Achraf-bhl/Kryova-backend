"""The server's own logs reach a terminal.

These exist because they did not. `_configure_logging` returned early on any
non-production environment, so a dev server ran with no root handler: root
defaults to WARNING with no handlers, which discards every `logger.info` in
`app/**` and pushes warnings through `logging.lastResort` -- bare text, no
timestamp, no level, no logger name.

Nothing caught it because nothing asserted on logging at all. A log that is
silently absent looks exactly like a system with nothing to say, which is the
worst failure mode a diagnostic can have.

No database fixture here on purpose: this is configuration, it runs offline in
milliseconds, and it should stay that way.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from app.core.config import settings
from app.main import HumanLogFormatter, JsonLogFormatter, _configure_logging


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    """Put the root logger back exactly as it was.

    `_configure_logging` clears root's handlers by design, and a test that ran
    it without restoring would silence pytest's own capture for everything
    after it in the session.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    noisy = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, restored in noisy.items():
            logging.getLogger(name).setLevel(restored)


def test_development_installs_a_root_handler() -> None:
    """The bug itself: a dev server used to install nothing."""
    assert not settings.is_production, "this test asserts the development branch"

    _configure_logging()

    root = logging.getLogger()
    assert root.handlers, "development installed no root handler, so app logs go nowhere"
    assert isinstance(root.handlers[0].formatter, HumanLogFormatter)


def test_an_info_line_from_the_app_is_actually_emitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An `app.*` logger at INFO reaches the stream, formatted and attributed.

    INFO specifically, because WARNING survived the bug -- via `lastResort` --
    and INFO did not. Asserting only on a warning would have passed against the
    broken code.
    """
    _configure_logging()

    logging.getLogger("app.catia.dispatch").info("activated document %s", "bracket.CATPart")

    written = capsys.readouterr().out
    assert "activated document bracket.CATPart" in written
    assert "INFO" in written
    assert "app.catia.dispatch" in written, "a line nobody can attribute is barely a log line"


def test_an_exception_carries_its_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    """`exc_info=True` must reach the terminal, or a failure is a one-liner."""
    _configure_logging()

    try:
        raise RuntimeError("the bridge dropped mid-operation")
    except RuntimeError:
        logging.getLogger("app.catia.local_bridge").warning("bridge lost", exc_info=True)

    written = capsys.readouterr().out
    assert "bridge lost" in written
    assert "RuntimeError: the bridge dropped mid-operation" in written
    assert "Traceback" in written


def test_a_request_id_is_appended_when_present() -> None:
    """The id the middleware stamps must be readable on the line."""
    formatter = HumanLogFormatter()
    record = logging.LogRecord("app.api", logging.INFO, __file__, 1, "saved", None, None)
    assert "[" not in formatter.format(record), "no id, no brackets"

    record.request_id = "abc123"  # type: ignore[attr-defined]
    assert "[abc123]" in formatter.format(record)


def test_an_unusable_log_level_still_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo in LOG_LEVEL must not silence the server it was meant to tune."""
    monkeypatch.setattr(settings, "log_level", "VERY LOUD")

    _configure_logging()

    assert logging.getLogger().level == logging.INFO
    written = capsys.readouterr().out
    assert "Unusable LOG_LEVEL" in written
    logging.getLogger("app.anything").info("still audible")
    assert "still audible" in capsys.readouterr().out


def test_production_still_emits_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production path is unchanged by the development fix."""
    monkeypatch.setattr(settings, "environment", "production")

    _configure_logging()

    assert isinstance(logging.getLogger().handlers[0].formatter, JsonLogFormatter)


def test_httpx_is_quietened_so_the_agent_loop_stays_readable() -> None:
    """One httpx line per model round trip buries the lines that matter."""
    _configure_logging()

    assert logging.getLogger("httpx").level >= logging.WARNING
