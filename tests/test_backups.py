"""The desktop database's backups: scheduled, listed and restored (ROAD_TO_10 9.1).

Everything here drives `app/core/backups.py` and the restore half of `app/core/local_cluster.py`
through an injected process runner, so no `pg_dump` and no server is needed: it proves the
decisions (what is due, what is kept, what a restore may name, what a failed restore leaves) and
the arguments a real `pg_dump`/`pg_restore` would be given. That a real dump restores into a real
cluster is THE QUEUE G11, which needs the bundled Postgres on the seat.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import backups
from app.core import local_cluster as lc
from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[1]
ADMIN = "postgresql://kryova_admin:secret-admin-pw@127.0.0.1:54329/kryova?sslmode=disable"


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeRun:
    behave: Any = None
    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls.append((list(args), kwargs))
        return self.behave(args, kwargs) if self.behave else Result()

    def programs(self) -> list[str]:
        return [Path(call[0][0]).stem for call in self.calls]


def writes_the_dump(args: list[str], kwargs: dict[str, Any]) -> Result:
    if "-f" in args:
        Path(args[args.index("-f") + 1]).write_text("dump")
    return Result()


def fake_bin(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("pg_dump", "pg_restore"):
        (bin_dir / (f"{name}.exe" if os.name == "nt" else name)).write_text("")
    return bin_dir


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def clock(day: int) -> Callable[[], datetime]:
    return lambda: at(day)


def touch(folder: Path, name: str, size: int = 4) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    return path


class TestAScheduledDumpIsItsOwnKind:
    def test_it_is_named_and_flagged_for_the_scheduled_kind(self, tmp_path: Path) -> None:
        run = FakeRun(writes_the_dump)

        target = backups.take_scheduled(
            fake_bin(tmp_path), ADMIN, tmp_path / "backups", now=lambda: at(5), run=run
        )

        assert target.name == "kryova-scheduled-20261005T120000Z.dump"
        args = run.calls[0][0]
        assert "--enable-row-security" in args and "-Fc" in args
        assert run.calls[0][1]["env"]["PGPASSWORD"] == "secret-admin-pw"

    def test_a_week_of_dailies_never_evicts_the_dump_taken_before_an_upgrade(
        self, tmp_path: Path
    ) -> None:
        folder = tmp_path / "backups"
        upgrade = touch(folder, "kryova-before-abc123-20260901T000000Z.dump")
        for day in range(1, 15):
            backups.take_scheduled(
                fake_bin(tmp_path), ADMIN, folder, now=clock(day), run=FakeRun(writes_the_dump)
            )

        scheduled = [b for b in backups.list_backups(folder) if b.kind == "scheduled"]
        assert len(scheduled) == backups.KEEP_SCHEDULED
        assert upgrade.exists()

    def test_an_upgrade_dump_still_keeps_only_its_own_newest_three(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        touch(folder, "kryova-scheduled-20260101T000000Z.dump")
        for day in range(1, 7):
            lc.back_up(
                fake_bin(tmp_path), ADMIN, folder, "abc", now=clock(day),
                run=FakeRun(writes_the_dump),
            )  # fmt: skip

        kinds = [b.kind for b in backups.list_backups(folder)]
        assert kinds.count("upgrade") == lc.KEEP_BACKUPS
        assert kinds.count("scheduled") == 1


class TestWhenOneIsDue:
    def test_none_yet_means_due(self, tmp_path: Path) -> None:
        assert backups.due(tmp_path / "backups", at(5))

    def test_a_young_one_means_not_due_and_an_old_one_means_due(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        touch(folder, "kryova-scheduled-20261005T060000Z.dump")

        assert not backups.due(folder, datetime(2026, 10, 5, 18, tzinfo=UTC))
        assert backups.due(folder, datetime(2026, 10, 6, 6, tzinfo=UTC))
        assert not backups.due(
            folder, datetime(2026, 10, 6, 5, 59, tzinfo=UTC)
        )  # one minute short of a day

    def test_an_upgrade_dump_does_not_count_as_the_daily_one(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        touch(folder, "kryova-before-abc-20261005T110000Z.dump")

        assert backups.due(folder, at(5))

    def test_take_if_due_writes_once_and_then_leaves_it_alone(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        run = FakeRun(writes_the_dump)
        bin_dir = fake_bin(tmp_path)

        first = backups.take_if_due(bin_dir, ADMIN, folder, now=lambda: at(5), run=run)
        second = backups.take_if_due(bin_dir, ADMIN, folder, now=lambda: at(5, 13), run=run)

        assert first is not None and second is None
        assert len(run.calls) == 1

    def test_a_failing_dump_is_swallowed_and_leaves_no_file(self, tmp_path: Path) -> None:
        def fails(args: list[str], kwargs: dict[str, Any]) -> Result:
            Path(args[args.index("-f") + 1]).write_text("half")
            return Result(returncode=1, stderr="pg_dump: error: connection refused")

        folder = tmp_path / "backups"

        assert (
            backups.take_if_due(
                fake_bin(tmp_path), ADMIN, folder, now=lambda: at(5), run=FakeRun(fails)
            )
            is None
        )
        assert list(folder.glob("*.dump")) == []


class TestTheFolderIsTheList:
    def test_each_kind_is_read_from_its_name_newest_first(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        touch(folder, "kryova-scheduled-20261003T010000Z.dump", 10)
        touch(folder, "kryova-before-0f0bec54f55e-20261001T010000Z.dump", 20)
        touch(folder, "kryova-before-restore-20261004T010000Z.dump", 30)

        listed = backups.list_backups(folder)

        assert [(b.kind, b.size_bytes) for b in listed] == [
            ("before-restore", 30),
            ("scheduled", 10),
            ("upgrade", 20),
        ]
        assert listed[2].revision == "0f0bec54f55e" and listed[0].revision is None
        assert listed[1].taken_at == datetime(2026, 10, 3, 1, tzinfo=UTC)

    def test_a_file_this_app_did_not_write_is_not_listed(self, tmp_path: Path) -> None:
        folder = tmp_path / "backups"
        touch(folder, "my-own-copy.dump")
        touch(folder, "kryova-scheduled-garbage.dump")

        assert backups.list_backups(folder) == []

    def test_a_missing_folder_is_an_empty_list(self, tmp_path: Path) -> None:
        assert backups.list_backups(tmp_path / "nope") == []


class TestARestoreIsRequestedThenAppliedAtLaunch:
    def home_with(self, tmp_path: Path, *names: str) -> Path:
        for name in names:
            touch(tmp_path / "backups", name)
        return tmp_path

    NAME = "kryova-scheduled-20261004T010000Z.dump"

    def test_a_request_names_a_backup_that_exists(self, tmp_path: Path) -> None:
        home = self.home_with(tmp_path, self.NAME)

        lc.request_restore(home, self.NAME)

        assert lc.pending_restore(home) == self.NAME
        assert lc.cancel_restore(home) is True
        assert lc.pending_restore(home) is None
        assert lc.cancel_restore(home) is False

    @pytest.mark.parametrize(
        "name",
        [
            "../secrets.json",
            "..\\..\\evil.dump",
            "/etc/passwd",
            "kryova-scheduled-20261004T010000Z.dump/../../x",
            "mine.dump",
            "kryova-before--20261004T010000Z.dump",
        ],
    )
    def test_a_path_or_a_foreign_name_is_refused(self, tmp_path: Path, name: str) -> None:
        home = self.home_with(tmp_path, self.NAME)

        with pytest.raises(lc.ClusterError):
            lc.request_restore(home, name)

        assert lc.pending_restore(home) is None

    def test_a_backup_that_is_not_there_is_refused_in_words(self, tmp_path: Path) -> None:
        with pytest.raises(lc.ClusterError, match="no backup called"):
            lc.request_restore(tmp_path, self.NAME)

    def test_the_launch_restores_it_in_one_transaction_after_a_safety_dump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = self.home_with(tmp_path, self.NAME)
        lc.request_restore(home, self.NAME)
        monkeypatch.setattr(lc, "_has_tables", lambda url: True)
        run = FakeRun(writes_the_dump)

        restored = lc.apply_pending_restore(home, fake_bin(tmp_path), ADMIN, run=run)

        assert restored == home / "backups" / self.NAME
        assert run.programs() == ["pg_dump", "pg_restore"]
        safety = next(home.joinpath("backups").glob("kryova-before-restore-*.dump"))
        assert safety.exists()
        restore_args, kwargs = run.calls[1]
        assert "--single-transaction" in restore_args and "--clean" in restore_args
        assert restore_args[-1] == str(home / "backups" / self.NAME)
        assert kwargs["env"]["PGPASSWORD"] == "secret-admin-pw"
        assert all("secret-admin-pw" not in argument for argument in restore_args)
        assert lc.pending_restore(home) is None  # the request is consumed

    def test_an_empty_database_is_not_dumped_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = self.home_with(tmp_path, self.NAME)
        lc.request_restore(home, self.NAME)
        monkeypatch.setattr(lc, "_has_tables", lambda url: False)
        run = FakeRun(writes_the_dump)

        lc.apply_pending_restore(home, fake_bin(tmp_path), ADMIN, run=run)

        assert run.programs() == ["pg_restore"]

    def test_a_failed_restore_leaves_the_launch_to_carry_on_and_records_why(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = self.home_with(tmp_path, self.NAME)
        lc.request_restore(home, self.NAME)
        monkeypatch.setattr(lc, "_has_tables", lambda url: True)

        def restore_fails(args: list[str], kwargs: dict[str, Any]) -> Result:
            if Path(args[0]).stem == "pg_restore":
                return Result(returncode=1, stderr="pg_restore: error: could not execute query")
            return writes_the_dump(args, kwargs)

        restored = lc.apply_pending_restore(
            home, fake_bin(tmp_path), ADMIN, run=FakeRun(restore_fails)
        )

        assert restored is None
        assert lc.pending_restore(home) is None  # not retried on every start
        failure = json.loads((home / lc.RESTORE_FAILED).read_text(encoding="utf-8"))
        assert "could not execute query" in failure["reason"]

    def test_a_dump_that_vanished_between_request_and_launch_is_a_recorded_failure(
        self, tmp_path: Path
    ) -> None:
        home = self.home_with(tmp_path, self.NAME)
        lc.request_restore(home, self.NAME)
        (home / "backups" / self.NAME).unlink()

        run = FakeRun(writes_the_dump)
        assert lc.apply_pending_restore(home, fake_bin(tmp_path), ADMIN, run=run) is None

        assert run.calls == []
        assert (home / lc.RESTORE_FAILED).exists()

    def test_a_request_file_somebody_hand_edited_to_a_path_is_not_followed(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / lc.RESTORE_REQUEST).write_text(json.dumps({"backup": "../../etc/passwd"}))

        run = FakeRun(writes_the_dump)
        assert lc.apply_pending_restore(tmp_path, fake_bin(tmp_path), ADMIN, run=run) is None

        assert run.calls == []
        assert not (tmp_path / lc.RESTORE_REQUEST).exists()

    def test_no_request_means_nothing_runs(self, tmp_path: Path) -> None:
        run = FakeRun()
        assert lc.apply_pending_restore(tmp_path, fake_bin(tmp_path), ADMIN, run=run) is None
        assert run.calls == []


class TestTheSchedulerNeverTakesTheAppDown:
    def test_it_runs_a_check_and_stops_when_told(self, tmp_path: Path) -> None:
        run = FakeRun(writes_the_dump)
        scheduler = backups.Scheduler(
            fake_bin(tmp_path), ADMIN, tmp_path / "backups", first_after=0.0, every=3600.0, run=run
        )

        scheduler.start()
        for _ in range(200):
            if run.calls:
                break
            scheduler._stop.wait(0.05)
        scheduler.stop()
        scheduler._thread.join(timeout=5)

        assert len(run.calls) == 1
        assert not scheduler._thread.is_alive()

    def test_the_launcher_starts_it_from_the_environment_it_set(self) -> None:
        source = (BACKEND / "app" / "desktop.py").read_text(encoding="utf-8")
        assert "_start_backups(home)" in source and "scheduler.stop()" in source


class TestTheBackupModulesDoNotReadTheSettings:
    def test_importing_them_leaves_the_settings_unbuilt(self) -> None:
        # The launcher starts the scheduler after it sets the environment, but both modules are
        # imported by `prepare` before that; reading `settings` would freeze a stale URL.
        probe = (
            "import sys; import app.core.backups, app.core.local_cluster; "
            "sys.exit(1 if 'app.core.config' in sys.modules else 0)"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr


class TestTheRoutesExistOnlyOnADesktop:
    def test_a_hosted_deployment_answers_404_to_all_four(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", None)

        assert auth_client.get("/api/v1/desktop/backups").status_code == 404
        assert auth_client.post("/api/v1/desktop/backups").status_code == 404
        assert (
            auth_client.post("/api/v1/desktop/backups/restore", json={"name": "x"}).status_code
            == 404
        )
        assert auth_client.delete("/api/v1/desktop/backups/restore").status_code == 404

    def test_signed_out_is_refused_before_anything_is_said_about_the_install(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))

        assert client.get("/api/v1/desktop/backups").status_code == 401

    def test_listing_and_requesting_a_restore_on_a_desktop(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))
        name = "kryova-scheduled-20261004T010000Z.dump"
        touch(tmp_path / "backups", name, 99)

        listed = auth_client.get("/api/v1/desktop/backups").json()
        assert [b["name"] for b in listed["backups"]] == [name]
        assert (
            listed["backups"][0]["kind"] == "scheduled" and listed["backups"][0]["size_bytes"] == 99
        )
        assert listed["restore_pending"] is None

        accepted = auth_client.post("/api/v1/desktop/backups/restore", json={"name": name})
        assert accepted.status_code == 202
        assert "Close Kryova" in accepted.json()["message"]
        assert auth_client.get("/api/v1/desktop/backups").json()["restore_pending"] == name

        assert auth_client.delete("/api/v1/desktop/backups/restore").status_code == 204
        assert auth_client.get("/api/v1/desktop/backups").json()["restore_pending"] is None

    def test_a_path_in_the_body_is_404_not_a_file_read(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))

        response = auth_client.post(
            "/api/v1/desktop/backups/restore", json={"name": "../secrets.json"}
        )

        assert response.status_code == 404
        assert lc.pending_restore(tmp_path) is None

    def test_a_failed_restore_is_shown_to_the_user(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))
        (tmp_path / lc.RESTORE_FAILED).write_text(json.dumps({"reason": "pg_restore exit 1"}))

        assert auth_client.get("/api/v1/desktop/backups").json()["last_restore_failure"] == (
            "pg_restore exit 1"
        )

    def test_backing_up_now_without_a_bundled_database_is_409(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))
        monkeypatch.setattr(settings, "local_postgres_bin_dir", None)

        assert auth_client.post("/api/v1/desktop/backups").status_code == 409

    def test_backing_up_now_writes_a_scheduled_dump(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(settings, "kryova_home", str(tmp_path))
        monkeypatch.setattr(settings, "local_postgres_bin_dir", str(fake_bin(tmp_path)))
        monkeypatch.setattr(backups, "admin_url_for", lambda home: ADMIN)
        run = FakeRun(writes_the_dump)
        monkeypatch.setattr(subprocess, "run", run)

        response = auth_client.post("/api/v1/desktop/backups")

        assert response.status_code == 201, response.text
        assert response.json()["kind"] == "scheduled"
        assert run.programs() == ["pg_dump"]

    def test_the_four_routes_are_in_the_openapi_document(self) -> None:
        from app.main import app

        paths = app.openapi()["paths"]
        assert set(paths["/api/v1/desktop/backups"]) == {"get", "post"}
        assert set(paths["/api/v1/desktop/backups/restore"]) == {"post", "delete"}


def test_a_restore_window_constant_is_a_day() -> None:
    assert backups.INTERVAL == timedelta(hours=24)
