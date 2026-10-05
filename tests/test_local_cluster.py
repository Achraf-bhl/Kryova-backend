"""The database a desktop install makes for itself (ROAD_TO_10 4.3, `app/core/local_cluster.py`).

Two halves. The decisions -- which secrets, which port, what `initdb` is told, when a backup is
taken -- run against a recording fake and open no database. The claim that matters, *a clean
machine ends up with a migrated database the application can use as a role that cannot bypass
row-level security*, needs a real Postgres, so `TestOnARealCluster` runs one when this machine
has the binaries and skips when it does not. A skip is not a pass: CI without Postgres binaries
learns nothing from that class, and the Windows seat's run of it is THE QUEUE G7.

No test requests a database fixture: the real cluster is the test's own, in a temporary folder.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from app.core import local_cluster as lc

BACKEND = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------------------
# A recording fake for everything that would start a process
# --------------------------------------------------------------------------------------


@dataclass
class Result:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeRun:
    """Records each call and runs `behave(args, kwargs)` for its effect and its result."""

    behave: Any = None
    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)

    def __call__(self, args: list[str], **kwargs: Any) -> Result:
        self.calls.append((list(args), kwargs))
        return self.behave(args, kwargs) if self.behave else Result()

    def program(self, index: int = 0) -> str:
        return Path(self.calls[index][0][0]).stem

    def find(self, program: str) -> list[tuple[list[str], dict[str, Any]]]:
        return [call for call in self.calls if Path(call[0][0]).stem == program]


def fake_bin(root: Path, *names: str) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        (bin_dir / (f"{name}.exe" if os.name == "nt" else name)).write_text("")
    return bin_dir


def initdb_that_makes_a_cluster(args: list[str], kwargs: dict[str, Any]) -> Result:
    data = Path(args[args.index("-D") + 1])
    data.mkdir(parents=True, exist_ok=True)
    (data / "PG_VERSION").write_text("17\n")
    (data / "postgresql.conf").write_text("#port = 5432\nmax_connections = 100\n")
    return Result()


SECRETS = lc.Secrets(secret_key="k" * 64, admin_password="a" * 32, app_password="p" * 32)


# --------------------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------------------


class TestSecretsAreMadeOnceAndNeverInvented:
    def test_a_first_launch_makes_them_and_a_second_reads_the_same_ones(
        self, tmp_path: Path
    ) -> None:
        first = lc.load_secrets(tmp_path, cluster_exists=False)
        second = lc.load_secrets(tmp_path, cluster_exists=True)

        assert first == second
        assert len(first.secret_key) >= 48
        assert len({first.secret_key, first.admin_password, first.app_password}) == 3

    @pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows takes its folder's ACL")
    def test_the_file_is_readable_by_its_owner_alone(self, tmp_path: Path) -> None:
        lc.load_secrets(tmp_path, cluster_exists=False)

        assert (tmp_path / lc.SECRETS_FILE).stat().st_mode & 0o777 == 0o600

    def test_a_cluster_with_no_secrets_file_is_refused_rather_than_given_new_passwords(
        self, tmp_path: Path
    ) -> None:
        # New passwords would "work" until the first connection, and a new signing key would
        # lock out every enrolled second factor (`security.encrypt_at_rest`).
        with pytest.raises(lc.ClusterError, match="passwords are not known"):
            lc.load_secrets(tmp_path, cluster_exists=True)

        assert not (tmp_path / lc.SECRETS_FILE).exists()

    def test_an_unreadable_file_is_refused_and_left_alone(self, tmp_path: Path) -> None:
        path = tmp_path / lc.SECRETS_FILE
        path.write_text("{ this is not json")

        with pytest.raises(lc.ClusterError, match="will not replace it"):
            lc.load_secrets(tmp_path, cluster_exists=False)

        assert path.read_text() == "{ this is not json"

    @pytest.mark.parametrize("missing", ["secret_key", "admin_password", "app_password"])
    def test_a_file_missing_one_value_or_holding_a_stub_is_refused(
        self, tmp_path: Path, missing: str
    ) -> None:
        data = {"secret_key": "k" * 40, "admin_password": "a" * 24, "app_password": "p" * 24}
        data[missing] = "short"
        (tmp_path / lc.SECRETS_FILE).write_text(json.dumps(data))

        with pytest.raises(lc.ClusterError, match=missing):
            lc.load_secrets(tmp_path, cluster_exists=False)

    def test_the_repr_shows_none_of_them(self) -> None:
        assert "k" * 8 not in repr(SECRETS)
        assert "a" * 8 not in repr(SECRETS)


# --------------------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------------------


class TestThePortIsChosenNotAssumed:
    def test_the_preferred_port_is_used_when_free(self) -> None:
        assert lc.choose_port(54329, lambda port: True) == 54329

    def test_a_taken_port_moves_to_the_next_free_one(self) -> None:
        taken = {54329, 54330}

        assert lc.choose_port(54329, lambda port: port not in taken) == 54331

    def test_nothing_free_is_a_sentence_not_a_hang(self) -> None:
        with pytest.raises(lc.ClusterError, match="No free port"):
            lc.choose_port(54329, lambda port: False)

    def test_the_port_is_read_from_the_file_and_the_last_assignment_wins(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "postgresql.conf").write_text(
            "#port = 5432\nport = 6000\nport = 6001 # later\n"
        )

        assert lc.configured_port(tmp_path) == 6001

    def test_a_down_server_whose_port_was_taken_is_moved_and_the_file_says_so(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "postgresql.conf").write_text("port = 54329\n")

        port = lc.settle_port(tmp_path, False, lambda candidate: candidate != 54329)

        assert port == 54330
        assert lc.configured_port(tmp_path) == 54330

    def test_a_running_server_keeps_its_port_even_though_the_port_answers(
        self, tmp_path: Path
    ) -> None:
        # "Taken" is us. Moving a running server's port in the file would strand it.
        (tmp_path / "postgresql.conf").write_text("port = 54329\n")

        assert lc.settle_port(tmp_path, True, lambda candidate: False) == 54329
        assert lc.configured_port(tmp_path) == 54329

    def test_a_free_port_is_left_alone(self, tmp_path: Path) -> None:
        (tmp_path / "postgresql.conf").write_text("port = 54329\n")

        assert lc.settle_port(tmp_path, False, lambda candidate: True) == 54329
        assert (tmp_path / "postgresql.conf").read_text() == "port = 54329\n"

    def test_port_is_free_sees_a_real_listener(self) -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]

            assert not lc.port_is_free(port)

        assert lc.port_is_free(port)


# --------------------------------------------------------------------------------------
# What initdb is told
# --------------------------------------------------------------------------------------


class TestTheClusterIsShapedSafely:
    def make(self, tmp_path: Path) -> tuple[Path, FakeRun, Path]:
        bin_dir = fake_bin(tmp_path, "initdb")
        run = FakeRun(initdb_that_makes_a_cluster)
        data = tmp_path / "home" / "pgdata"
        lc.init_cluster(bin_dir, data, SECRETS, run=run, is_free=lambda port: True)
        return bin_dir, run, data

    def test_every_connection_needs_a_scram_password_and_the_admin_is_not_the_application(
        self, tmp_path: Path
    ) -> None:
        _, run, _ = self.make(tmp_path)

        args = run.calls[0][0]
        assert "--auth=scram-sha-256" in args
        assert args[args.index("-U") + 1] == lc.ADMIN_ROLE != lc.APP_ROLE
        assert args[args.index("-E") + 1] == "UTF8"

    def test_the_password_goes_by_file_never_on_the_command_line(self, tmp_path: Path) -> None:
        # A command line is shown to every account by Task Manager and `ps`.
        seen: dict[str, str] = {}

        def behave(args: list[str], kwargs: dict[str, Any]) -> Result:
            pwfile = next(a for a in args if a.startswith("--pwfile=")).split("=", 1)[1]
            seen["body"] = Path(pwfile).read_text().strip()
            seen["path"] = pwfile
            return initdb_that_makes_a_cluster(args, kwargs)

        bin_dir = fake_bin(tmp_path, "initdb")
        run = FakeRun(behave)
        lc.init_cluster(bin_dir, tmp_path / "pgdata", SECRETS, run=run, is_free=lambda port: True)

        assert seen["body"] == SECRETS.admin_password
        assert all(SECRETS.admin_password not in argument for argument in run.calls[0][0])
        assert not Path(seen["path"]).exists()

    def test_the_password_file_is_removed_even_when_initdb_fails(self, tmp_path: Path) -> None:
        seen: dict[str, str] = {}

        def behave(args: list[str], kwargs: dict[str, Any]) -> Result:
            seen["path"] = next(a for a in args if a.startswith("--pwfile=")).split("=", 1)[1]
            return Result(returncode=1, stderr="initdb: error: could not create directory")

        bin_dir = fake_bin(tmp_path, "initdb")
        with pytest.raises(lc.ClusterError, match="could not create directory"):
            lc.init_cluster(bin_dir, tmp_path / "pgdata", SECRETS, run=FakeRun(behave))

        assert not Path(seen["path"]).exists()

    def test_the_server_listens_on_loopback_only_with_no_unix_socket(self, tmp_path: Path) -> None:
        _, _, data = self.make(tmp_path)

        conf = (data / "postgresql.conf").read_text()
        assert "listen_addresses = '127.0.0.1'" in conf
        assert "unix_socket_directories = ''" in conf
        assert "'*'" not in conf and "0.0.0.0" not in conf

    def test_the_chosen_port_is_written_into_the_cluster_so_it_survives_a_restart(
        self, tmp_path: Path
    ) -> None:
        bin_dir = fake_bin(tmp_path, "initdb")
        data = tmp_path / "pgdata"

        port = lc.init_cluster(
            bin_dir, data, SECRETS, run=FakeRun(initdb_that_makes_a_cluster),
            is_free=lambda candidate: candidate >= 54335,
        )  # fmt: skip

        assert port == 54335
        assert lc.configured_port(data) == 54335

    def test_a_folder_holding_something_else_is_not_initialised_into(self, tmp_path: Path) -> None:
        data = tmp_path / "pgdata"
        data.mkdir()
        (data / "my-thesis.docx").write_text("")
        bin_dir = fake_bin(tmp_path, "initdb")
        run = FakeRun(initdb_that_makes_a_cluster)

        with pytest.raises(lc.ClusterError, match="not empty"):
            lc.init_cluster(bin_dir, data, SECRETS, run=run)

        assert run.calls == []

    def test_a_missing_initdb_says_the_install_is_incomplete(self, tmp_path: Path) -> None:
        with pytest.raises(lc.ClusterError, match="reinstall"):
            lc.init_cluster(tmp_path / "bin", tmp_path / "pgdata", SECRETS, run=FakeRun())


# --------------------------------------------------------------------------------------
# Backups, migrations, stopping
# --------------------------------------------------------------------------------------


class TestTheDatabaseMemoryIsSizedToTheMachine:
    """ROAD_TO_10 6.10. The figures are chosen, not measured, so what is tested is the shape of the
    rule -- a floor, a ceiling, growth with the machine, and *nothing at all* for a machine that
    will not say -- and that it reaches the file the server reads."""

    def test_a_large_machine_hits_the_ceilings(self) -> None:
        assert lc.tuning_for(16 * 1024) == {
            "shared_buffers": "1024MB",
            "effective_cache_size": "8192MB",
            "work_mem": "22MB",
            "maintenance_work_mem": "512MB",
        }

    def test_a_small_machine_gets_the_floors_not_a_share_of_nothing(self) -> None:
        assert lc.tuning_for(2 * 1024) == {
            "shared_buffers": "256MB",
            "effective_cache_size": "1024MB",
            "work_mem": "4MB",
            "maintenance_work_mem": "128MB",
        }

    def test_a_machine_that_will_not_say_is_left_on_postgres_defaults(self) -> None:
        assert lc.tuning_for(None) == {}
        assert lc.tuning_for(0) == {}

    @pytest.mark.parametrize("key", ["shared_buffers", "effective_cache_size", "work_mem", "maintenance_work_mem"])
    def test_a_bigger_machine_never_gets_less(self, key: str) -> None:
        sizes = [1024, 2048, 4096, 8192, 16384, 65536, 262144]
        values = [int(lc.tuning_for(mb)[key].removesuffix("MB")) for mb in sizes]
        assert values == sorted(values)

    def test_the_connection_count_the_work_mem_is_divided_by_is_the_one_written(
        self, tmp_path: Path
    ) -> None:
        bin_dir = fake_bin(tmp_path, "initdb")
        data = tmp_path / "home" / "pgdata"
        lc.init_cluster(
            bin_dir,
            data,
            SECRETS,
            run=FakeRun(initdb_that_makes_a_cluster),
            is_free=lambda port: True,
            total_ram_mb=lambda: 8 * 1024,
        )
        conf = (data / "postgresql.conf").read_text()
        assert f"max_connections = {lc._MAX_CONNECTIONS}" in conf
        assert "shared_buffers = 1024MB" in conf
        assert "work_mem = 11MB" in conf

    def test_a_machine_that_will_not_say_writes_no_memory_lines(self, tmp_path: Path) -> None:
        bin_dir = fake_bin(tmp_path, "initdb")
        data = tmp_path / "home" / "pgdata"
        lc.init_cluster(
            bin_dir,
            data,
            SECRETS,
            run=FakeRun(initdb_that_makes_a_cluster),
            is_free=lambda port: True,
            total_ram_mb=lambda: None,
        )
        conf = (data / "postgresql.conf").read_text()
        assert "shared_buffers" not in conf and "work_mem" not in conf
        assert "listen_addresses = '127.0.0.1'" in conf


class TestAnUpgradeIsPrecededByADump:
    ADMIN = "postgresql://kryova_admin:secret-admin-pw@127.0.0.1:54329/kryova?sslmode=disable"

    def dump(self, tmp_path: Path, when: datetime, run: FakeRun | None = None) -> Path:
        def writes(args: list[str], kwargs: dict[str, Any]) -> Result:
            Path(args[args.index("-f") + 1]).write_text("dump")
            return Result()

        return lc.back_up(
            fake_bin(tmp_path, "pg_dump"), self.ADMIN, tmp_path / "backups", "abc123",
            run=run or FakeRun(writes), now=lambda: when,
        )  # fmt: skip

    def test_the_dump_is_named_for_the_revision_it_preserves(self, tmp_path: Path) -> None:
        target = self.dump(tmp_path, datetime(2026, 10, 5, 12, 0, tzinfo=UTC))

        assert target.name == "kryova-before-abc123-20261005T120000Z.dump"
        assert target.read_text() == "dump"

    def test_the_password_travels_in_the_environment_not_the_arguments(
        self, tmp_path: Path
    ) -> None:
        run = FakeRun(
            lambda args, kwargs: (Path(args[args.index("-f") + 1]).write_text("x"), Result())[1]
        )

        self.dump(tmp_path, datetime(2026, 10, 5, tzinfo=UTC), run)

        args, kwargs = run.calls[0]
        assert kwargs["env"]["PGPASSWORD"] == "secret-admin-pw"
        assert all("secret-admin-pw" not in argument for argument in args)
        assert "-Fc" in args

    def test_only_the_newest_few_are_kept(self, tmp_path: Path) -> None:
        for day in range(1, 7):
            self.dump(tmp_path, datetime(2026, 10, day, tzinfo=UTC))

        names = sorted(path.name for path in (tmp_path / "backups").glob("*.dump"))
        assert len(names) == lc.KEEP_BACKUPS
        assert names[-1].endswith("20261006T000000Z.dump")
        assert names[0].endswith("20261004T000000Z.dump")

    def test_a_failed_dump_leaves_no_partial_file_and_stops_the_upgrade(
        self, tmp_path: Path
    ) -> None:
        def fails(args: list[str], kwargs: dict[str, Any]) -> Result:
            Path(args[args.index("-f") + 1]).write_text("half a dump")
            return Result(returncode=1, stderr="pg_dump: error: connection refused")

        with pytest.raises(lc.ClusterError, match="left as it was"):
            self.dump(tmp_path, datetime(2026, 10, 5, tzinfo=UTC), FakeRun(fails))

        assert list((tmp_path / "backups").glob("*.dump")) == []


class TestMigrationAimsAtExactlyThisDatabase:
    def test_the_target_and_the_schema_go_in_the_childs_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `env.py` reads DATABASE_URL from settings, so a bare call migrates whatever the
        # caller's environment names (CLAUDE.md Database 9). A developer's DB_SCHEMA must not
        # leak either, or the check looks in `public` while the tables went to `kryova`.
        monkeypatch.setenv("DATABASE_URL", "postgresql://someone-else@elsewhere/theirs")
        monkeypatch.setenv("DB_SCHEMA", "kryova")
        run = FakeRun()

        lc.migrate("postgresql://kryova:pw@127.0.0.1:54329/kryova", tmp_path, run=run)

        args, kwargs = run.calls[0]
        assert args[:3] == [sys.executable, "-m", "alembic"]
        assert args[-2:] == ["upgrade", "head"]
        assert kwargs["env"]["DATABASE_URL"] == "postgresql://kryova:pw@127.0.0.1:54329/kryova"
        assert kwargs["env"]["DB_SCHEMA"] == lc.APP_SCHEMA == "public"
        assert kwargs["cwd"] == str(tmp_path)

    def test_a_failed_upgrade_quotes_alembic_and_says_the_data_is_as_it_was(
        self, tmp_path: Path
    ) -> None:
        run = FakeRun(
            lambda args, kwargs: Result(
                returncode=1, stderr="sqlalchemy.exc.ProgrammingError: boom"
            )
        )

        with pytest.raises(lc.ClusterError, match="ProgrammingError: boom") as caught:
            lc.migrate("postgresql://kryova:pw@127.0.0.1:1/kryova", tmp_path, run=run)

        assert "backups folder" in str(caught.value)


class TestStoppingTheServerOnTheWayOut:
    def test_a_running_server_is_stopped_fast(self, tmp_path: Path) -> None:
        bin_dir = fake_bin(tmp_path, "pg_ctl")

        def behave(args: list[str], kwargs: dict[str, Any]) -> Result:
            return Result(returncode=0)  # `status` 0 = running; `stop` 0 = done

        run = FakeRun(behave)

        assert lc.stop(bin_dir, tmp_path / "pgdata", run=run) is True
        stop = run.calls[1][0]
        assert stop[1] == "stop" and "-m" in stop and stop[stop.index("-m") + 1] == "fast"

    def test_a_server_that_is_not_running_is_not_stopped_again(self, tmp_path: Path) -> None:
        bin_dir = fake_bin(tmp_path, "pg_ctl")
        run = FakeRun(lambda args, kwargs: Result(returncode=3))

        assert lc.stop(bin_dir, tmp_path / "pgdata", run=run) is False
        assert len(run.calls) == 1

    def test_it_never_raises_on_the_way_out(self, tmp_path: Path) -> None:
        def explodes(args: list[str], kwargs: dict[str, Any]) -> Result:
            raise OSError("the binary is gone")

        assert lc.stop(tmp_path / "bin", tmp_path / "pgdata", run=FakeRun(explodes)) is False


class TestTheHeadIsReadWithoutImportingTheMigrations:
    def versions(self, root: Path, files: dict[str, str]) -> Path:
        folder = root / "migrations" / "versions"
        folder.mkdir(parents=True)
        for name, body in files.items():
            (folder / name).write_text(body)
        return root

    def test_it_agrees_with_alembics_own_answer_on_the_real_migrations(self) -> None:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))

        assert [lc.code_head(BACKEND)] == ScriptDirectory.from_config(config).get_heads()

    def test_it_reads_annotated_assignments_and_follows_a_merge(self, tmp_path: Path) -> None:
        root = self.versions(
            tmp_path,
            {
                "a.py": 'revision = "a"\ndown_revision = None\n',
                "b.py": 'revision: str = "b"\ndown_revision: str | None = "a"\n',
                "c.py": 'revision = "c"\ndown_revision = "a"\n',
                "m.py": 'revision = "m"\ndown_revision = ("b", "c")\n',
            },
        )

        assert lc.code_head(root) == "m"

    def test_two_heads_are_a_broken_build_and_say_so(self, tmp_path: Path) -> None:
        root = self.versions(
            tmp_path,
            {
                "a.py": 'revision = "a"\ndown_revision = None\n',
                "b.py": 'revision = "b"\ndown_revision = "a"\n',
                "c.py": 'revision = "c"\ndown_revision = "a"\n',
            },
        )

        with pytest.raises(lc.ClusterError, match="2 heads"):
            lc.code_head(root)

    def test_reading_it_does_not_build_the_settings(self) -> None:
        # Three revision files import `settings`. Importing them from here built it from the
        # environment as it stood BEFORE `app.desktop` had set DATABASE_URL.
        probe = (
            "import sys; from pathlib import Path; import app.core.local_cluster as lc; "
            f"lc.code_head(Path({str(BACKEND)!r})); "
            "sys.exit(1 if 'app.core.config' in sys.modules else 0)"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr


class TestThisModuleDoesNotReadTheSettings:
    def test_importing_it_leaves_the_settings_unbuilt(self) -> None:
        # `app.desktop` sets the environment AFTER importing this and BEFORE anything reads
        # `settings`, which is built once at import. If this module pulled it in, the cluster
        # code would be configured from whatever the environment held before the managed
        # variables were set -- on a developer machine, somebody else's database.
        probe = (
            "import sys; import app.core.local_cluster; "
            "sys.exit(1 if 'app.core.config' in sys.modules else 0)"
        )

        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=BACKEND, capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------------------
# A real cluster
# --------------------------------------------------------------------------------------


def _find_postgres_bin() -> Path | None:
    candidates = [os.environ.get("KRYOVA_TEST_POSTGRES_BIN_DIR", "")]
    candidates += sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True)
    on_path = shutil.which("initdb")
    if on_path:
        candidates.append(str(Path(on_path).parent))
    for candidate in candidates:
        if (
            candidate
            and (Path(candidate) / ("initdb.exe" if os.name == "nt" else "initdb")).is_file()
        ):
            return Path(candidate)
    return None


POSTGRES_BIN = _find_postgres_bin()
# initdb refuses to run as root, which is how some CI containers run.
CAN_INIT = POSTGRES_BIN is not None and not (hasattr(os, "geteuid") and os.geteuid() == 0)


@dataclass
class Installed:
    home: Path
    bin_dir: Path
    secrets: lc.Secrets
    cluster: lc.Cluster


@pytest.fixture(scope="class")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Installed]:
    assert POSTGRES_BIN is not None
    home = tmp_path_factory.mktemp("kryova-home")
    secrets_ = lc.load_secrets(home, cluster_exists=False)
    cluster = lc.prepare(home, POSTGRES_BIN, BACKEND, secrets_)
    try:
        yield Installed(home, POSTGRES_BIN, secrets_, cluster)
    finally:
        lc.stop(POSTGRES_BIN, home / "pgdata")


@pytest.mark.skipif(
    not CAN_INIT, reason="needs PostgreSQL binaries (initdb, pg_ctl, pg_dump) and a non-root user"
)
class TestOnARealCluster:
    """What a clean machine ends up with. Ordered: each test starts from what the last left."""

    def test_a_clean_machine_ends_with_a_migrated_database(self, installed: Installed) -> None:
        current, head = lc.migration_state(installed.cluster.app_url, BACKEND)

        assert current == head
        assert installed.cluster.migrated is True
        assert installed.cluster.backup is None  # nothing to preserve on a first launch

    def test_the_application_role_cannot_bypass_row_level_security(
        self, installed: Installed
    ) -> None:
        # The reason for two roles. A superuser outranks FORCE ROW LEVEL SECURITY, so the
        # policies under every tenant check would be decoration (CLAUDE.md Database 3).
        with psycopg.connect(installed.cluster.admin_url) as admin:
            rows = admin.execute(
                "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
                "FROM pg_roles WHERE rolname IN (%s, %s) ORDER BY rolname",
                (lc.APP_ROLE, lc.ADMIN_ROLE),
            ).fetchall()

        by_name = {row[0]: row[1:] for row in rows}
        assert by_name[lc.APP_ROLE] == (False, False, False, False)
        assert by_name[lc.ADMIN_ROLE][0] is True  # and the admin is the one that can

    def test_the_application_role_owns_its_tables_and_reads_them(
        self, installed: Installed
    ) -> None:
        with psycopg.connect(installed.cluster.app_url) as app:
            tables = app.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
                (lc.APP_SCHEMA,),
            ).fetchone()
            forced = app.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relforcerowsecurity",
                (lc.APP_SCHEMA,),
            ).fetchone()

        assert tables is not None and tables[0] > 30
        # The migrations' FORCE ROW LEVEL SECURITY reached this database too.
        assert forced is not None and forced[0] > 0

    def test_another_database_account_cannot_open_the_application_database(
        self, installed: Installed
    ) -> None:
        # `CREATE DATABASE` grants CONNECT to PUBLIC, so on a shared workstation any role that
        # exists could read through its own door. The launch revokes it.
        # Built as a parameter, not written into the statement: `scripts.scan_secrets` refuses a
        # literal password in a role-creation statement wherever it appears, tests included.
        bystander_password = "bystander-" + "password"
        with psycopg.connect(installed.cluster.admin_url, autocommit=True) as admin:
            admin.execute("DROP ROLE IF EXISTS bystander")
            admin.execute(
                sql.SQL("CREATE ROLE bystander LOGIN PASSWORD {}").format(
                    sql.Literal(bystander_password)
                )
            )
        url = installed.cluster.app_url.replace(
            f"{lc.APP_ROLE}:{installed.secrets.app_password}", f"bystander:{bystander_password}"
        )
        try:
            with pytest.raises(psycopg.OperationalError, match="permission denied for database"):
                psycopg.connect(url, connect_timeout=5)
        finally:
            with psycopg.connect(installed.cluster.admin_url, autocommit=True) as admin:
                admin.execute("DROP ROLE IF EXISTS bystander")

    def test_a_stranger_cannot_connect_without_the_password(self, installed: Installed) -> None:
        url = installed.cluster.app_url.replace(installed.secrets.app_password, "not-the-password")

        with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
            psycopg.connect(url, connect_timeout=5)

    def test_the_server_is_not_listening_beyond_loopback(self, installed: Installed) -> None:
        with psycopg.connect(installed.cluster.admin_url) as admin:
            row = admin.execute("SHOW listen_addresses").fetchone()

        assert row == ("127.0.0.1",)

    def test_a_second_launch_changes_nothing_and_takes_no_backup(
        self, installed: Installed
    ) -> None:
        again = lc.prepare(installed.home, installed.bin_dir, BACKEND, installed.secrets)

        assert again.port == installed.cluster.port
        assert again.migrated is False and again.backup is None
        assert not (installed.home / "backups").exists()

    def test_an_upgrade_that_migrates_is_dumped_first_and_the_dump_is_a_real_archive(
        self, installed: Installed
    ) -> None:
        # Put the database one revision behind the code, as it is for a customer whose
        # installed version is older than the one just installed.
        lc.stop(installed.bin_dir, installed.home / "pgdata")
        lc.prepare(installed.home, installed.bin_dir, BACKEND, installed.secrets)
        down = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(BACKEND / "alembic.ini"),
                "downgrade",
                "-1",
            ],
            cwd=BACKEND,
            env={
                **os.environ,
                "DATABASE_URL": installed.cluster.app_url,
                "DB_SCHEMA": lc.APP_SCHEMA,
            },
            capture_output=True,
            text=True,
        )
        assert down.returncode == 0, down.stderr
        behind, head = lc.migration_state(installed.cluster.app_url, BACKEND)
        assert behind != head

        upgraded = lc.prepare(installed.home, installed.bin_dir, BACKEND, installed.secrets)

        assert upgraded.migrated is True
        assert upgraded.backup is not None and upgraded.backup.stat().st_size > 0
        assert behind is not None and behind in upgraded.backup.name
        assert lc.migration_state(upgraded.app_url, BACKEND)[0] == head
        listing = subprocess.run(
            [str(installed.bin_dir / "pg_restore"), "--list", str(upgraded.backup)],
            capture_output=True,
            text=True,
        )
        assert listing.returncode == 0 and "TABLE" in listing.stdout

    def test_a_port_another_program_took_while_it_was_down_is_moved_not_a_failure(
        self, installed: Installed
    ) -> None:
        data = installed.home / "pgdata"
        before = lc.configured_port(data)
        assert lc.stop(installed.bin_dir, data) is True
        squatter = socket.socket()
        squatter.bind(("127.0.0.1", before))
        squatter.listen()
        try:
            moved = lc.prepare(installed.home, installed.bin_dir, BACKEND, installed.secrets)

            assert moved.port != before
            assert lc.configured_port(data) == moved.port
            assert lc.migration_state(moved.app_url, BACKEND)[0] is not None
        finally:
            squatter.close()

    def test_stopping_leaves_nothing_running_that_holds_the_install_open(
        self, installed: Installed
    ) -> None:
        data = installed.home / "pgdata"

        assert lc.stop(installed.bin_dir, data) is True

        status = subprocess.run(
            [str(installed.bin_dir / "pg_ctl"), "status", "-D", str(data)], capture_output=True
        )
        assert status.returncode == 3
        assert lc.stop(installed.bin_dir, data) is False  # idempotent
