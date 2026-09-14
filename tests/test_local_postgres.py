"""The server starts this machine's Postgres, and never hangs on a dead one.

Two defects combined into the most-seen failure on the Windows workstation
(2026-09-14): the zip-archive Postgres does not survive a reboot, and psycopg
on Windows never notices a refused port, so uvicorn's lifespan waited forever
on its first query and the desktop app said "the API server is not reachable".

`ensure_running` is driven through an injected runner, so none of this touches
a real `pg_ctl`; the timeout test opens a real connection to a real closed
port, because the hang was only ever visible against a real socket.
"""

from __future__ import annotations

import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, exc

from app.core.database import connect_args_for
from app.core.local_postgres import (
    STATUS_NOT_RUNNING,
    STATUS_RUNNING,
    Action,
    LocalPostgresError,
    ensure_running,
    is_local,
)

LOCAL = "postgresql://kryova:pw@localhost:5432/kryova?sslmode=disable"
NEON = "postgresql://u:p@ep-x-pooler.eu-west-2.aws.neon.tech/db?sslmode=require"


class FakePgCtl:
    """Records every call and answers `status` and `start` as told."""

    def __init__(self, status: int = STATUS_NOT_RUNNING, start: int = 0) -> None:
        self.status, self.start = status, start
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(args), kwargs))
        code = self.status if args[1] == "status" else self.start
        return subprocess.CompletedProcess(args, code, stdout="", stderr="")

    @property
    def commands(self) -> list[str]:
        return [args[1] for args, _ in self.calls]


@pytest.fixture
def install(tmp_path: Path) -> tuple[str, str]:
    bin_dir = tmp_path / "pgsql" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "pg_ctl.exe").write_bytes(b"")
    (bin_dir / "pg_ctl").write_bytes(b"")
    data = tmp_path / "pgdata"
    data.mkdir()
    return str(bin_dir), str(data)


class TestNothingIsStartedUnlessAsked:
    def test_an_unconfigured_server_runs_nothing(self) -> None:
        runner = FakePgCtl()
        assert ensure_running(LOCAL, None, None, run=runner).action is Action.NOT_CONFIGURED
        assert runner.calls == []

    def test_half_a_configuration_runs_nothing(self, install: tuple[str, str]) -> None:
        runner = FakePgCtl()
        assert ensure_running(LOCAL, install[0], None, run=runner).action is Action.NOT_CONFIGURED
        assert runner.calls == []

    def test_a_database_on_another_host_is_never_started_here(
        self, install: tuple[str, str]
    ) -> None:
        runner = FakePgCtl()
        outcome = ensure_running(NEON, *install, run=runner)
        assert outcome.action is Action.NOT_LOCAL
        assert runner.calls == []

    @pytest.mark.parametrize(
        "url",
        [
            LOCAL,
            "postgresql://u:p@127.0.0.1:5432/db",
            "postgresql://u:p@[::1]:5432/db",
            "postgresql+psycopg://u:p@LOCALHOST/db",
        ],
    )
    def test_every_spelling_of_this_machine_is_local(self, url: str) -> None:
        assert is_local(url)

    def test_a_remote_host_is_not_local(self) -> None:
        assert not is_local(NEON)


class TestItStartsWhatIsDown:
    def test_a_running_server_is_left_alone(self, install: tuple[str, str]) -> None:
        runner = FakePgCtl(status=STATUS_RUNNING)
        assert ensure_running(LOCAL, *install, run=runner).action is Action.ALREADY_RUNNING
        assert runner.commands == ["status"]

    def test_a_stopped_server_is_started_and_waited_for(self, install: tuple[str, str]) -> None:
        runner = FakePgCtl(status=STATUS_NOT_RUNNING)
        assert ensure_running(LOCAL, *install, run=runner).action is Action.STARTED
        assert runner.commands == ["status", "start"]
        args, _ = runner.calls[1]
        assert args[args.index("-D") + 1] == install[1]
        # Without -w the call returns before the server accepts connections and
        # the very next query races it.
        assert "-w" in args
        log = Path(args[args.index("-l") + 1])
        # Inside the data directory, crash recovery's fsync collides with the
        # open log and retries for 30 s on Windows.
        assert Path(install[1]) not in log.parents
        assert log == Path(install[1]).with_name("pgdata.log")

    def test_the_start_output_is_never_captured_through_a_pipe(
        self, install: tuple[str, str]
    ) -> None:
        """The postmaster inherits pg_ctl's handles; a pipe would never close."""
        runner = FakePgCtl()
        ensure_running(LOCAL, *install, run=runner)
        _, kwargs = runner.calls[1]
        assert "capture_output" not in kwargs
        for stream in ("stdin", "stdout", "stderr"):
            assert kwargs[stream] is subprocess.DEVNULL


class TestAFailureIsSaidInWords:
    def test_a_start_that_fails_quotes_the_server_log(self, install: tuple[str, str]) -> None:
        Path(install[1]).with_name("pgdata.log").write_text(
            "FATAL:  could not create lock file \"postmaster.pid\": Permission denied\n",
            encoding="utf-8",
        )
        with pytest.raises(LocalPostgresError, match="Permission denied"):
            ensure_running(LOCAL, *install, run=FakePgCtl(start=1))

    def test_a_missing_pg_ctl_is_named(self, tmp_path: Path, install: tuple[str, str]) -> None:
        empty = tmp_path / "nothing"
        empty.mkdir()
        with pytest.raises(LocalPostgresError, match="LOCAL_POSTGRES_BIN_DIR"):
            ensure_running(LOCAL, str(empty), install[1], run=FakePgCtl())

    def test_a_missing_data_directory_is_named(
        self, tmp_path: Path, install: tuple[str, str]
    ) -> None:
        with pytest.raises(LocalPostgresError, match="LOCAL_POSTGRES_DATA_DIR"):
            ensure_running(LOCAL, install[0], str(tmp_path / "gone"), run=FakePgCtl())

    def test_an_unreadable_data_directory_is_refused_rather_than_started(
        self, install: tuple[str, str]
    ) -> None:
        runner = FakePgCtl(status=4)
        with pytest.raises(LocalPostgresError, match="could not read"):
            ensure_running(LOCAL, *install, run=runner)
        assert runner.commands == ["status"]


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class TestADeadDatabaseFailsRatherThanHangs:
    def test_every_connection_carries_a_timeout(self) -> None:
        assert connect_args_for(LOCAL, 7)["connect_timeout"] == 7

    def test_connecting_to_nothing_raises_within_the_timeout(self) -> None:
        """Measured 2026-09-14: without `connect_timeout` this never returned on Windows."""
        url = f"postgresql+psycopg://u:p@127.0.0.1:{_closed_port()}/db?sslmode=disable"
        engine = create_engine(url, connect_args=connect_args_for(url, 2))
        raised: list[BaseException] = []

        def connect() -> None:
            try:
                engine.connect().close()
            except BaseException as error:  # noqa: BLE001 - the error is the result
                raised.append(error)

        worker = threading.Thread(target=connect, daemon=True)
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive(), "a connection to a closed port hung instead of failing"
        assert raised and isinstance(raised[0], exc.OperationalError)
