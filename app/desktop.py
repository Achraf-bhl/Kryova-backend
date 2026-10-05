"""The backend as the desktop installer runs it: `python -m app.desktop` (ROAD_TO_10 4.2, 4.3).

The development shell starts `uvicorn app.main:app` against a Postgres somebody set up. An
installed app has nobody to set anything up, so this is the whole of "start the backend" for
it: find a home for what must survive an upgrade, make or find the database, migrate it, hand
the application its configuration, and serve on loopback.

    <home>/                  %LOCALAPPDATA%\\Kryova on Windows (the shell passes KRYOVA_HOME)
      pgdata/  pgdata.log    the database and its log (`core/local_cluster.py`)
      secrets.json           generated once, owner-readable, never regenerated
      media/                 every uploaded and derived file
      backups/               a dump before any upgrade that changes the schema, and a daily one
                             (`core/backups.py`); a restore request is `restore-request.json`
      config.env             the user's own settings -- a model key, SMTP -- optional

**The order is the point.** `app.core.config.settings` is read once, at import, so every
variable below is set before anything under `app` is imported. Importing it first would give
the cluster code a `DATABASE_URL` from whatever the environment happened to hold -- on a
developer machine, somebody else's database.

Two kinds of key, and the file may only touch one:

* **Managed** -- the database, the signing key, the media folder, the origins: the facts that
  tie this install's pieces together. They are set here, from what was just made, and a line in
  `config.env` naming one is ignored with a warning rather than honoured. A user who pasted a
  production `DATABASE_URL` into it would otherwise point the desktop at a stranger's database
  with their own sign-in.
* **Everything else** -- `AI_API_KEY`, `AI_PROVIDER`, `MAIL_*`, limits. `config.env` overrides
  the defaults below and is itself overridden by a variable in the real environment.

Defaults chosen for one person on one machine, each of them stated because each is a decision:

* `REQUIRE_VERIFIED_EMAIL_FOR_PROJECTS=false`. Mail goes to the log on an install with no SMTP,
  so with the server's default the first account could never create a project and the only
  explanation is in a log file nobody opens. Put SMTP in `config.env` and turn it back on.
* `ENVIRONMENT=development`, not `production`: production refuses `http://` origins and
  insecure cookies, and the shell serves both on loopback by design.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

logger = logging.getLogger("kryova.desktop")

FRONTEND_ORIGIN = "http://127.0.0.1:3000"
DEFAULT_API_PORT = 8000
CONFIG_FILE = "config.env"

#: What `config.env` may not set. Each one is a fact `prepare` just established.
MANAGED_KEYS = frozenset(
    {
        "DATABASE_URL",
        "DB_SCHEMA",
        "SECRET_KEY",
        "LOCAL_POSTGRES_BIN_DIR",
        "LOCAL_POSTGRES_DATA_DIR",
        "MEDIA_ROOT",
        "CORS_ORIGINS",
        "FRONTEND_URL",
        "ENVIRONMENT",
    }
)

#: Applied under the user's file and the real environment.
DEFAULTS: Mapping[str, str] = {
    "REQUIRE_VERIFIED_EMAIL_FOR_PROJECTS": "false",
}


def resolve_home(env: Mapping[str, str], platform: str) -> Path:
    """Where the install keeps what outlives an upgrade. The same rule as the shell's `layout.rs`.

    Never the install directory: it is read-only to the person running the app and replaced
    wholesale by an upgrade.
    """
    explicit = env.get("KRYOVA_HOME", "")
    if explicit:
        return Path(explicit)
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or (
            str(Path(env["USERPROFILE"]) / "AppData" / "Local") if env.get("USERPROFILE") else ""
        )
        if base:
            return Path(base) / "Kryova"
    elif platform == "darwin":
        if env.get("HOME"):
            return Path(env["HOME"]) / "Library" / "Application Support" / "Kryova"
    else:
        base = env.get("XDG_DATA_HOME") or (
            str(Path(env["HOME"]) / ".local" / "share") if env.get("HOME") else ""
        )
        if base:
            return Path(base) / "kryova"
    raise SystemExit(
        "Kryova has nowhere to keep its database: none of KRYOVA_HOME, LOCALAPPDATA, "
        "USERPROFILE, HOME or XDG_DATA_HOME is set."
    )


def read_user_config(path: Path) -> tuple[dict[str, str], list[str]]:
    """The settings the user's `config.env` may apply, and a sentence for each it may not.

    A missing file is not an error: most installs never have one.
    """
    if not path.is_file():
        return {}, []
    accepted: dict[str, str] = {}
    refused: list[str] = []
    for key, value in dotenv_values(path, encoding="utf-8").items():
        if value is None:
            continue
        name = key.strip().upper()
        if name in MANAGED_KEYS or name.startswith("KRYOVA_"):
            refused.append(
                f"{path.name}: {key} is ignored. Kryova sets it from the database it manages, "
                "and a copy here could only point the app somewhere else."
            )
            continue
        accepted[key.strip()] = value
    return accepted, refused


def apply_environment(
    env: MutableMapping[str, str],
    *,
    home: Path,
    app_url: str,
    secret_key: str,
    bin_dir: Path,
    data_dir: Path,
    user_config: Mapping[str, str],
) -> None:
    """Set every variable the application reads.

    Highest precedence first: the managed keys, then the real environment, then the user's
    file, then `DEFAULTS`. The real environment outranks the file and the defaults (somebody
    exporting `AI_API_KEY` meant it) but never a managed key: a `DATABASE_URL` left in the
    Windows environment by another product must not reach this app.
    """
    for key, value in {**DEFAULTS, **user_config}.items():
        env.setdefault(key, value)
    managed = {
        "DATABASE_URL": app_url,
        "DB_SCHEMA": "public",
        "SECRET_KEY": secret_key,
        "LOCAL_POSTGRES_BIN_DIR": str(bin_dir),
        "LOCAL_POSTGRES_DATA_DIR": str(data_dir),
        "MEDIA_ROOT": str(home / "media"),
        # Turns on `/desktop/backups` (app/api/routes/desktop.py), which 404s without it. The
        # shell passes it only sometimes, so the launcher states it from the home it resolved.
        "KRYOVA_HOME": str(home),
        # JSON, because `cors_origins` is a list and that is how pydantic-settings reads one.
        "CORS_ORIGINS": f'["{FRONTEND_ORIGIN}"]',
        "FRONTEND_URL": FRONTEND_ORIGIN,
        "ENVIRONMENT": "development",
    }
    env.update(managed)


def api_port(env: Mapping[str, str]) -> int:
    raw = env.get("KRYOVA_API_PORT", "")
    try:
        port = int(raw) if raw else DEFAULT_API_PORT
    except ValueError:
        raise SystemExit(f"KRYOVA_API_PORT is {raw!r}; it must be a port number.") from None
    if not 1 <= port <= 65535:
        raise SystemExit(f"KRYOVA_API_PORT is {port}; it must be between 1 and 65535.")
    return port


def postgres_bin_dir(env: Mapping[str, str], backend_dir: Path) -> Path:
    """The bundled Postgres: named by the shell, else `postgres/bin` beside the backend folder."""
    named = env.get("KRYOVA_POSTGRES_BIN_DIR", "")
    return Path(named) if named else backend_dir.parent / "postgres" / "bin"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Nothing under `app` is imported until the environment is complete -- see the docstring.
    backend_dir = Path(__file__).resolve().parents[1]
    home = resolve_home(os.environ, sys.platform)
    home.mkdir(parents=True, exist_ok=True)
    for line in _apply_without_importing_app(home, backend_dir):
        logger.warning("%s", line)

    import uvicorn

    port = api_port(os.environ)
    logger.info("Serving on 127.0.0.1:%d from %s", port, home)
    scheduler = _start_backups(home)
    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info", workers=1)
    finally:
        if scheduler is not None:
            scheduler.stop()
        from app.core import local_cluster

        local_cluster.stop(
            Path(os.environ["LOCAL_POSTGRES_BIN_DIR"]), Path(os.environ["LOCAL_POSTGRES_DATA_DIR"])
        )
    return 0


def _start_backups(home: Path) -> Any:
    """Start the daily backup thread. A backup problem must never be why the app is not serving."""
    try:
        from app.core import backups

        scheduler = backups.Scheduler(
            Path(os.environ["LOCAL_POSTGRES_BIN_DIR"]),
            backups.admin_url_for(home),
            home / "backups",
        )
        scheduler.start()
        return scheduler
    except Exception as error:  # noqa: BLE001 -- see the docstring
        logger.warning("Scheduled backups are off: %s", error)
        return None


def _apply_without_importing_app(home: Path, backend_dir: Path) -> list[str]:
    """Make the database, then set the environment. Returns the warnings to log."""
    # `local_cluster` imports nothing that reads `settings`: `app/core/__init__.py` is empty and
    # `local_postgres` is standard-library plus SQLAlchemy's URL parser. Pinned by a test.
    from app.core import local_cluster

    bin_dir = postgres_bin_dir(os.environ, backend_dir)
    data_dir = home / "pgdata"
    try:
        secrets_ = local_cluster.load_secrets(
            home, cluster_exists=local_cluster.cluster_exists(data_dir)
        )
        cluster = local_cluster.prepare(home, bin_dir, backend_dir, secrets_)
    except local_cluster.ClusterError as error:
        # The shell shows its setup page when the port never opens, and that page reads this
        # log. A traceback here would bury the one sentence that says what to do.
        logger.error("%s", error)
        raise SystemExit(2) from None

    user_config, refused = read_user_config(home / CONFIG_FILE)
    apply_environment(
        os.environ,
        home=home,
        app_url=cluster.app_url,
        secret_key=secrets_.secret_key,
        bin_dir=bin_dir,
        data_dir=cluster.data_dir,
        user_config=user_config,
    )
    if cluster.backup is not None:
        logger.info("Backed up the database before upgrading it: %s", cluster.backup)
    if cluster.restored_from is not None:
        logger.info("Restored the database from %s", cluster.restored_from)
    return refused


if __name__ == "__main__":
    raise SystemExit(main())
