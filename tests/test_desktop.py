"""The backend as the installer runs it (ROAD_TO_10 4.2-4.3, `app/desktop.py`).

Three kinds of claim. The small rules -- where the home is, which keys a user's file may set,
who wins when two sources name one variable -- are pure and run in microseconds. The contract
with the shell is two files in two repositories that must agree, which nothing else sees both
of. And the one that matters: a clean home plus the bundled Postgres ends in an application
that boots on its own database, which `TestABundledBootOnAFreshHome` does end to end in a
child process, because the thing being proved is an import order.

No test requests a database fixture.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app import desktop
from app.core.config import Settings
from tests.test_local_cluster import CAN_INIT, POSTGRES_BIN

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "Kryova-frontend"


class TestWhereTheHomeIs:
    @pytest.mark.parametrize(
        ("platform", "env", "expected"),
        [
            ("win32", {"KRYOVA_HOME": "D:/k"}, "D:/k"),
            (
                "win32",
                {"LOCALAPPDATA": "C:/Users/a/AppData/Local"},
                "C:/Users/a/AppData/Local/Kryova",
            ),
            ("win32", {"USERPROFILE": "C:/Users/a"}, "C:/Users/a/AppData/Local/Kryova"),
            ("darwin", {"HOME": "/Users/a"}, "/Users/a/Library/Application Support/Kryova"),
            ("linux", {"XDG_DATA_HOME": "/data"}, "/data/kryova"),
            ("linux", {"HOME": "/home/a"}, "/home/a/.local/share/kryova"),
            ("linux", {"KRYOVA_HOME": "/srv/kryova", "HOME": "/home/a"}, "/srv/kryova"),
        ],
    )
    def test_each_platform_has_the_home_the_shell_has(
        self, platform: str, env: dict[str, str], expected: str
    ) -> None:
        assert desktop.resolve_home(env, platform) == Path(expected)

    @pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
    def test_with_nothing_to_go_on_it_refuses_rather_than_using_the_working_directory(
        self, platform: str
    ) -> None:
        # A database in the cwd is a database in the install directory, which an upgrade
        # replaces wholesale.
        with pytest.raises(SystemExit, match="nowhere to keep"):
            desktop.resolve_home({}, platform)


class TestTheUsersFileMayNotAimTheAppAtAnotherDatabase:
    def write(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / desktop.CONFIG_FILE
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_a_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert desktop.read_user_config(tmp_path / "config.env") == ({}, [])

    def test_ordinary_settings_are_accepted(self, tmp_path: Path) -> None:
        path = self.write(
            tmp_path,
            """
            AI_PROVIDER=anthropic
            AI_API_KEY=not-a-real-key
            MAIL_TRANSPORT=smtp
            """,
        )

        accepted, refused = desktop.read_user_config(path)

        assert accepted == {
            "AI_PROVIDER": "anthropic",
            "AI_API_KEY": "not-a-real-key",
            "MAIL_TRANSPORT": "smtp",
        }
        assert refused == []

    @pytest.mark.parametrize(
        "key", sorted(desktop.MANAGED_KEYS) + ["database_url", "KRYOVA_HOME", "KRYOVA_API_PORT"]
    )
    def test_every_managed_key_is_refused_by_name_whatever_its_case(
        self, tmp_path: Path, key: str
    ) -> None:
        path = self.write(tmp_path, f"{key}=somewhere-else\nAI_PROVIDER=openai\n")

        accepted, refused = desktop.read_user_config(path)

        assert accepted == {"AI_PROVIDER": "openai"}
        assert len(refused) == 1 and key in refused[0] and "ignored" in refused[0]


class TestWhoWinsWhenTwoSourcesNameOneVariable:
    def apply(self, env: dict[str, str], user: dict[str, str] | None = None) -> dict[str, str]:
        desktop.apply_environment(
            env,
            home=Path("/home/a/kryova"),
            app_url="postgresql://kryova:pw@127.0.0.1:54329/kryova?sslmode=disable",
            secret_key="s" * 64,
            bin_dir=Path("/opt/pg/bin"),
            data_dir=Path("/home/a/kryova/pgdata"),
            user_config=user or {},
        )
        return env

    def test_a_database_url_left_in_the_environment_by_another_product_does_not_reach_this_app(
        self,
    ) -> None:
        env = self.apply(
            {"DATABASE_URL": "postgresql://other@their-host/theirs", "SECRET_KEY": "x"}
        )

        assert env["DATABASE_URL"].startswith("postgresql://kryova:pw@127.0.0.1:54329/kryova")
        assert env["SECRET_KEY"] == "s" * 64

    def test_the_real_environment_beats_the_users_file_which_beats_the_defaults(self) -> None:
        env = self.apply(
            {"AI_MODEL": "from-the-environment"},
            {"AI_MODEL": "from-the-file", "AI_PROVIDER": "from-the-file"},
        )

        assert env["AI_MODEL"] == "from-the-environment"
        assert env["AI_PROVIDER"] == "from-the-file"
        assert env["REQUIRE_VERIFIED_EMAIL_FOR_PROJECTS"] == "false"

    def test_the_file_can_turn_email_verification_back_on_once_there_is_a_mail_server(self) -> None:
        env = self.apply({}, {"REQUIRE_VERIFIED_EMAIL_FOR_PROJECTS": "true"})

        assert env["REQUIRE_VERIFIED_EMAIL_FOR_PROJECTS"] == "true"

    def test_the_database_lives_in_the_public_schema_whatever_a_developer_has_set(self) -> None:
        # `local_cluster` checks the migration state in this schema. Letting `.env.local`'s
        # `DB_SCHEMA=kryova` through would make the database look forever "behind".
        assert self.apply({"DB_SCHEMA": "kryova"})["DB_SCHEMA"] == "public"

    def test_the_settings_the_application_builds_from_it_are_the_desktop_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = self.apply({})
        for key in list(os.environ):
            if key.isupper() and key.split("_")[0] in {
                "AI",
                "MAIL",
                "CORS",
                "ENVIRONMENT",
                "DATABASE",
            }:
                monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        settings = Settings(_env_file=None)  # type: ignore[call-arg]

        assert settings.cors_origins == ["http://127.0.0.1:3000"]
        assert settings.frontend_url == "http://127.0.0.1:3000"
        assert settings.environment == "development"
        assert settings.database_url.startswith("postgresql+psycopg://kryova:")
        assert settings.media_root == Path("/home/a/kryova/media")
        assert settings.require_verified_email_for_projects is False
        assert settings.local_postgres_data_dir == "/home/a/kryova/pgdata"


class TestThePortsAndPaths:
    def test_the_api_port_is_the_shells_or_8000(self) -> None:
        assert desktop.api_port({}) == 8000
        assert desktop.api_port({"KRYOVA_API_PORT": "8123"}) == 8123

    @pytest.mark.parametrize("bad", ["eight", "0", "70000", "-1"])
    def test_a_port_that_is_not_one_is_a_sentence(self, bad: str) -> None:
        with pytest.raises(SystemExit, match="KRYOVA_API_PORT"):
            desktop.api_port({"KRYOVA_API_PORT": bad})

    def test_postgres_is_where_the_shell_says_or_beside_the_backend_folder(self) -> None:
        assert desktop.postgres_bin_dir(
            {"KRYOVA_POSTGRES_BIN_DIR": "/x/bin"}, Path("/i/backend")
        ) == Path("/x/bin")
        assert desktop.postgres_bin_dir({}, Path("/i/backend")) == Path("/i/postgres/bin")

    def test_both_loopback_spellings_are_allowed_origins_by_default(self) -> None:
        # A browser treats `localhost` and `127.0.0.1` as different origins and the desktop
        # shell loads the app from the numeric one.
        defaults = Settings.model_fields["cors_origins"].default_factory()  # type: ignore[call-arg,misc]

        assert "http://127.0.0.1:3000" in defaults
        assert "http://localhost:3000" in defaults


@pytest.mark.skipif(not FRONTEND.is_dir(), reason="needs the sibling Kryova-frontend checkout")
class TestTheShellAndThisFileAgree:
    """`src-tauri/src/layout.rs` sets variables and starts a module; this reads and provides them."""

    layout = (
        (FRONTEND / "src-tauri/src/layout.rs").read_text(encoding="utf-8")
        if FRONTEND.is_dir()
        else ""
    )
    lib = (
        (FRONTEND / "src-tauri/src/lib.rs").read_text(encoding="utf-8") if FRONTEND.is_dir() else ""
    )

    def test_every_variable_the_shell_hands_the_backend_is_one_this_reads(self) -> None:
        body = self.layout[
            self.layout.index("pub fn backend_env") : self.layout.index("pub fn frontend_env")
        ]
        sent = set(re.findall(r'\("(KRYOVA_[A-Z_]+)"', body))
        source = (BACKEND / "app/desktop.py").read_text(encoding="utf-8")

        assert sent == {"KRYOVA_HOME", "KRYOVA_POSTGRES_BIN_DIR", "KRYOVA_API_PORT"}
        for name in sent:
            assert name in source, f"the shell sets {name} and app/desktop.py never reads it"

    def test_the_module_the_shell_starts_exists_and_runs_as_main(self) -> None:
        assert '.arg("app.desktop")' in self.lib
        source = (BACKEND / "app/desktop.py").read_text(encoding="utf-8")
        assert 'if __name__ == "__main__"' in source

    def test_the_shell_and_the_backend_name_the_same_database_folder_and_marker(self) -> None:
        # The shell decides "first launch, wait longer" from these two names. If either side
        # renames one, a first launch is never recognised as one and the window is declared
        # broken at 90 s while `initdb` is still running.
        source = (BACKEND / "app/desktop.py").read_text(encoding="utf-8")
        cluster = (BACKEND / "app/core/local_cluster.py").read_text(encoding="utf-8")

        assert 'home.join("pgdata")' in self.layout
        assert 'postgres_data(home).join("PG_VERSION")' in self.layout
        assert 'home / "pgdata"' in source
        assert 'home / "pgdata"' in cluster
        assert '(data_dir / "PG_VERSION").is_file()' in cluster

    def test_the_origin_this_allows_is_the_one_the_shell_loads(self) -> None:
        assert desktop.FRONTEND_ORIGIN == "http://127.0.0.1:3000"
        assert "FRONTEND_PORT: u16 = 3000" in self.lib
        assert "127.0.0.1:{FRONTEND_PORT}" in self.lib


@pytest.mark.skipif(not CAN_INIT, reason="needs PostgreSQL binaries and a non-root user")
class TestABundledBootOnAFreshHome:
    """What the installed app does on a clean machine, in a child, in the order it does it."""

    def test_the_settings_are_built_after_the_database_exists_and_the_app_boots_on_it(
        self, tmp_path: Path
    ) -> None:
        assert POSTGRES_BIN is not None
        home = tmp_path / "Kryova"
        script = textwrap.dedent(
            """
            import sys
            from pathlib import Path
            from app import desktop

            refused = desktop._apply_without_importing_app(Path(sys.argv[1]), Path(sys.argv[2]))
            # The claim: nothing read the settings before the environment was complete.
            from app.core.config import settings
            from sqlalchemy import make_url
            url = make_url(settings.database_url)
            print("ROLE", url.username, "DB", url.database, "HOST", url.host)
            print("REFUSED", len(refused))

            from fastapi.testclient import TestClient
            from app.main import app
            with TestClient(app) as client:
                health = client.get("/health")
                print("HEALTH", health.status_code)
            """
        )
        env = {
            **os.environ,
            "KRYOVA_HOME": str(home),
            "KRYOVA_POSTGRES_BIN_DIR": str(POSTGRES_BIN),
            # What a developer machine has and a customer's does not. It must not win.
            "DATABASE_URL": "postgresql://someone-else@nowhere.invalid/theirs",
            "SECRET_KEY": "a-different-key-that-is-long-enough-to-pass-validation",
            "KRYOVA_API_PORT": "8000",
        }
        try:
            result = subprocess.run(
                [sys.executable, "-c", script, str(home), str(BACKEND)],
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
        finally:
            from app.core import local_cluster

            local_cluster.stop(POSTGRES_BIN, home / "pgdata")

        assert result.returncode == 0, result.stderr[-2500:]
        assert "ROLE kryova DB kryova HOST 127.0.0.1" in result.stdout
        assert "HEALTH 200" in result.stdout, result.stdout
        # The state a customer's machine is left in.
        assert (home / "secrets.json").is_file()
        assert (home / "pgdata" / "PG_VERSION").is_file()
        assert "nowhere.invalid" not in (home / "secrets.json").read_text()
