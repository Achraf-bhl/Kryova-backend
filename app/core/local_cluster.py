"""Make, and keep, the PostgreSQL cluster a desktop install owns (ROAD_TO_10 4.3).

`local_postgres.py` starts a server somebody else made. An installed desktop app has nobody
else: on a clean machine there is no Postgres, no data directory, no role and no tables, and
the person who installed it is not going to run `initdb`. So the first launch makes all of
it, and every later launch finds it where it was left.

    <home>/pgdata/            the cluster (`initdb`)
    <home>/pgdata.log         the server log, BESIDE the data directory (CLAUDE.md Database 4a)
    <home>/secrets.json       the token-signing key and the two database passwords
    <home>/backups/           a dump taken before any upgrade that migrates

Five rules, each pinned by a test that fails when it is removed:

1. **The application is never a superuser.** Row-level security is the safety net under every
   tenant check here and a superuser outranks `FORCE ROW LEVEL SECURITY` (CLAUDE.md Database 3),
   so the cluster has two roles: `kryova_admin`, which makes the database and takes the
   backup, and `kryova`, which the application connects as and which is `NOSUPERUSER
   NOBYPASSRLS`. A desktop install whose policies were inert would be the worst place for it:
   it holds an engineer's designs and nothing else is checking.
2. **Loopback only, and a password even there.** `listen_addresses = '127.0.0.1'` and
   `scram-sha-256` for every connection. A second account on a shared workstation can reach
   loopback, and a database with `trust` on it is a database that account can read.
3. **Secrets are generated once and never regenerated.** The token-signing key also derives the
   key that seals second-factor secrets at rest (`security.encrypt_at_rest`), so replacing it
   locks every enrolled user out. A secrets file that cannot be read is a refusal in words, and
   a cluster whose secrets file is gone is refused too -- neither is "fixed" by inventing a new
   one that matches nothing.
4. **A port that is taken is not a failure.** The cluster's port is written into its own
   `postgresql.conf`, and if the server is down and that port has since been taken by another
   program, a free one is chosen and written back. Something else already holding 5432 -- a
   developer's Postgres, another product's -- is the ordinary case on a workstation.
5. **A migration is preceded by a dump.** An upgrade that changes the schema and fails half
   way leaves a customer's only copy of their work in a state no release has seen. When the
   database is behind the code, `pg_dump` runs first, as the admin (a superuser reads every
   row past RLS, which the application role by design cannot), and the last
   `KEEP_BACKUPS` are kept.

Everything that touches a process takes an injected `run`, as `local_postgres.py` does, so the
decisions are tested without a database and the rest against a real one where the machine has
Postgres binaries.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy import make_url

from app.core import local_postgres

logger = logging.getLogger(__name__)

ADMIN_ROLE = "kryova_admin"
APP_ROLE = "kryova"
APP_DATABASE = "kryova"
#: The schema the tables live in. Stated rather than left to `DB_SCHEMA`: a developer's
#: `.env.local` sets one, and a migration run against `kryova` while the check looks in
#: `public` would find the database forever "behind" and take a backup on every launch.
APP_SCHEMA = "public"

#: First port tried for a new cluster. Nothing standard: 5432 is the one a workstation's own
#: Postgres has, and a second product's cluster on the same port is the collision to avoid.
DEFAULT_PORT = 54329
PORT_SEARCH_SPAN = 200

KEEP_BACKUPS = 3

SECRETS_FILE = "secrets.json"

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]


class ClusterError(RuntimeError):
    """The desktop database could not be made, started or migrated. The message says what to do."""


@dataclass(frozen=True)
class Secrets:
    """The three values an install generates once. `repr` shows none of them."""

    secret_key: str
    admin_password: str
    app_password: str

    def __repr__(self) -> str:
        return "Secrets(<withheld>)"


@dataclass(frozen=True)
class Cluster:
    """Where a prepared cluster is, and how the application reaches it."""

    data_dir: Path
    bin_dir: Path
    port: int
    app_url: str
    admin_url: str
    backup: Path | None
    migrated: bool
    #: The dump a restore request was applied from on this launch, if there was one.
    restored_from: Path | None = None


# --------------------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------------------


def _new_secrets() -> Secrets:
    return Secrets(
        secret_key=secrets.token_urlsafe(48),
        admin_password=secrets.token_urlsafe(24),
        app_password=secrets.token_urlsafe(24),
    )


def load_secrets(home: Path, *, cluster_exists: bool) -> Secrets:
    """Read `<home>/secrets.json`, or make it on a first launch -- and refuse everything else.

    `cluster_exists` is the guard for rule 3: no secrets file beside a cluster means the
    cluster's passwords are unrecoverable, and generating fresh ones would "work" right up to
    the first connection.
    """
    path = home / SECRETS_FILE
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Secrets(
                secret_key=_nonempty(data, "secret_key"),
                admin_password=_nonempty(data, "admin_password"),
                app_password=_nonempty(data, "app_password"),
            )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ClusterError(
                f"{path} exists and cannot be read ({error}). It holds the keys this install's "
                "database and sign-ins depend on, so Kryova will not replace it. Restore it "
                "from a backup, or delete it together with the data directory to start over."
            ) from error
    if cluster_exists:
        raise ClusterError(
            f"The database in {home / 'pgdata'} exists but {path} does not, so its passwords "
            "are not known. Kryova will not make new ones that match nothing. Restore the "
            "file, or delete the data directory to start over (this deletes the database)."
        )
    made = _new_secrets()
    _write_private(
        path,
        json.dumps(
            {
                "secret_key": made.secret_key,
                "admin_password": made.admin_password,
                "app_password": made.app_password,
            },
            indent=2,
        )
        + "\n",
    )
    return made


def _nonempty(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or len(value) < 16:
        raise ValueError(f"{key} is missing or too short")
    return value


def _write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read.

    On POSIX the mode is set when the file is created, so there is no window in which it is
    world-readable. On Windows the mode argument does nothing and the file takes the ACL of
    its folder -- `%LOCALAPPDATA%\\Kryova` is the user's profile, which other standard
    accounts cannot read. That is the protection there, and it is not a stronger one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(temporary, path)


# --------------------------------------------------------------------------------------
# Ports
# --------------------------------------------------------------------------------------


def port_is_free(port: int) -> bool:
    """Whether nothing is listening on loopback `port` right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def choose_port(preferred: int, is_free: Callable[[int], bool] = port_is_free) -> int:
    """`preferred` if it is free, else the next free port within `PORT_SEARCH_SPAN`."""
    for port in range(preferred, min(preferred + PORT_SEARCH_SPAN, 65535)):
        if is_free(port):
            return port
    raise ClusterError(
        f"No free port between {preferred} and {preferred + PORT_SEARCH_SPAN - 1} on this "
        "machine for Kryova's database. Close the program holding them and start again."
    )


_PORT_LINE = re.compile(r"(?m)^[ \t]*port[ \t]*=[ \t]*(\d+)")


def configured_port(data_dir: Path) -> int:
    """The port `postgresql.conf` names. Read from the file, so it survives a restart."""
    text = (data_dir / "postgresql.conf").read_text(encoding="utf-8", errors="replace")
    matches = _PORT_LINE.findall(text)
    if not matches:
        raise ClusterError(f"{data_dir / 'postgresql.conf'} has no `port =` line.")
    return int(matches[-1])  # Postgres reads the last assignment, so do we


def _set_port(data_dir: Path, port: int) -> None:
    conf = data_dir / "postgresql.conf"
    text = conf.read_text(encoding="utf-8", errors="replace")
    if not text.endswith("\n"):
        text += "\n"
    # Appended rather than edited in place: the last assignment wins, and the original
    # `#port = 5432` line stays where a person reading the file expects to find it.
    conf.write_text(
        text + f"port = {port}  # Kryova: chosen at install, rewritten if taken\n",
        encoding="utf-8",
        newline="\n",
    )


# --------------------------------------------------------------------------------------
# Making the cluster
# --------------------------------------------------------------------------------------


def _exe(bin_dir: Path, name: str) -> Path:
    return bin_dir / (f"{name}.exe" if os.name == "nt" else name)


def _quiet() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def cluster_exists(data_dir: Path) -> bool:
    return (data_dir / "PG_VERSION").is_file()


#: Chosen, not measured -- no benchmark of this database on a workstation has been run. The rule is
#: the usual one (PostgreSQL's own guidance is a quarter of RAM for shared_buffers) *scaled down*,
#: because this server shares the machine with CATIA and the solvers and holds a few thousand rows of
#: an engineer's designs and jobs, not a warehouse. Each figure has a floor (what a stock install
#: gets) and a ceiling (beyond which the rows it would cache do not exist).
_MAX_CONNECTIONS = 60


def tuning_for(total_ram_mb: int | None) -> dict[str, str]:
    """`postgresql.conf` memory settings sized to this machine, or `{}` when it will not say.

    Unknown memory writes nothing rather than a guess: Postgres's own defaults are safe on any
    machine and a figure sized for sixty-four gigabytes is not.
    """
    if not total_ram_mb or total_ram_mb <= 0:
        return {}

    def megabytes(value: float, floor: int, ceiling: int) -> str:
        return f"{int(min(max(value, floor), ceiling))}MB"

    return {
        # A shared cache of the hot pages: an eighth of the machine, 128 MB (stock) to 1 GB.
        "shared_buffers": megabytes(total_ram_mb / 8, 128, 1024),
        # A planner hint, not an allocation: roughly what the operating system will cache for it.
        "effective_cache_size": megabytes(total_ram_mb / 2, 512, 8192),
        # Per sort or hash per connection, so it is divided across the connections that could
        # each be using several: a quarter of the machine over 60 connections x 3 operations.
        "work_mem": megabytes(total_ram_mb / 4 / (_MAX_CONNECTIONS * 3), 4, 64),
        "maintenance_work_mem": megabytes(total_ram_mb / 16, 64, 512),
    }


def init_cluster(
    bin_dir: Path,
    data_dir: Path,
    secrets_: Secrets,
    *,
    preferred_port: int = DEFAULT_PORT,
    run: Runner = subprocess.run,
    is_free: Callable[[int], bool] = port_is_free,
    total_ram_mb: int | None | Callable[[], int | None] = None,
) -> int:
    """`initdb` a new cluster and shape its configuration. Returns the port it will use.

    `total_ram_mb` sizes the memory settings (`tuning_for`); left alone it is read from the
    machine (`app.core.hardware`, which imports no settings), and a callable is accepted so a test
    can say what the machine has without a probe.
    """
    initdb = _exe(bin_dir, "initdb")
    if not initdb.is_file():
        raise ClusterError(
            f"There is no {initdb.name} at {initdb}, so a database cannot be created. The "
            "installation is incomplete: reinstall Kryova."
        )
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ClusterError(
            f"{data_dir} exists, is not empty and is not a Postgres cluster. Kryova will not "
            "initialise a database inside a folder that holds something else."
        )
    data_dir.parent.mkdir(parents=True, exist_ok=True)

    # `initdb --pwfile` reads the superuser's password from a file rather than the command
    # line, where `tasklist /v` and Task Manager would show it to every account.
    descriptor, name = tempfile.mkstemp(prefix=".kryova-pw-", dir=str(data_dir.parent))
    pwfile = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(secrets_.admin_password + "\n")
        result = run(
            [
                str(initdb),
                "-D",
                str(data_dir),
                "-U",
                ADMIN_ROLE,
                "-E",
                "UTF8",
                "--auth=scram-sha-256",
                f"--pwfile={pwfile}",
            ],  # fmt: skip
            capture_output=True,
            text=True,
            timeout=180,
            **_quiet(),
        )
    finally:
        pwfile.unlink(missing_ok=True)
    if result.returncode != 0:
        raise ClusterError(
            f"`initdb` failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout or '').strip()[-600:]}"
        )

    port = choose_port(preferred_port, is_free)
    conf = data_dir / "postgresql.conf"
    text = conf.read_text(encoding="utf-8", errors="replace")
    if not text.endswith("\n"):
        text += "\n"
    if total_ram_mb is None:
        from app.core import hardware

        total_ram_mb = hardware.hardware().total_ram_mb
    elif callable(total_ram_mb):
        total_ram_mb = total_ram_mb()
    tuning = tuning_for(total_ram_mb)
    tuned = "".join(f"{key} = {value}\n" for key, value in tuning.items())
    conf.write_text(
        text + "\n# --- Kryova --------------------------------------------------------------\n"
        "listen_addresses = '127.0.0.1'\n"
        # No Unix socket: on POSIX it is a second, filesystem-permissioned way in that the
        # rules above do not describe, and Windows has none.
        "unix_socket_directories = ''\n"
        f"max_connections = {_MAX_CONNECTIONS}\n"
        f"port = {port}\n" + tuned,
        encoding="utf-8",
        newline="\n",
    )
    if tuning:
        logger.info("Sized the database's memory for %s MB of RAM: %s", total_ram_mb, tuning)
    logger.info("Created a Postgres cluster in %s on port %d", data_dir, port)
    return port


def database_url(role: str, password: str, port: int, database: str) -> str:
    """A connection URL. `sslmode=disable` is required, not a shortcut: this server has ssl off."""
    return f"postgresql://{role}:{password}@127.0.0.1:{port}/{database}?sslmode=disable"


def settle_port(
    data_dir: Path, status_running: bool, is_free: Callable[[int], bool] = port_is_free
) -> int:
    """The port to use now. Rewrites the configuration if the server is down and its port was taken."""
    port = configured_port(data_dir)
    if status_running or is_free(port):
        return port
    replacement = choose_port(port + 1, is_free)
    logger.warning(
        "Port %d is in use by another program; moving Kryova's database to %d", port, replacement
    )
    _set_port(data_dir, replacement)
    return replacement


def _server_running(bin_dir: Path, data_dir: Path, run: Runner) -> bool:
    status = run(
        [str(_exe(bin_dir, "pg_ctl")), "status", "-D", str(data_dir)],
        capture_output=True,
        text=True,
        timeout=15,
        **_quiet(),
    )
    return status.returncode == local_postgres.STATUS_RUNNING


def _provision(admin_url: str, secrets_: Secrets) -> None:
    """Make the application role and database, or bring existing ones back to the same state.

    Idempotent, and it resets the role's password and attributes every launch: the secrets
    file is the source of truth, so a role someone altered by hand is put back rather than
    trusted.
    """
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=10) as admin:
        exists = admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        admin.execute(
            sql.SQL(
                "{verb} ROLE {role} LOGIN PASSWORD {password} "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(
                verb=sql.SQL(verb),
                role=sql.Identifier(APP_ROLE),
                password=sql.Literal(secrets_.app_password),
            )
        )
        if not admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (APP_DATABASE,)
        ).fetchone():
            admin.execute(
                sql.SQL("CREATE DATABASE {db} OWNER {role}").format(
                    db=sql.Identifier(APP_DATABASE), role=sql.Identifier(APP_ROLE)
                )
            )
        admin.execute(
            sql.SQL("REVOKE ALL ON DATABASE {db} FROM PUBLIC").format(
                db=sql.Identifier(APP_DATABASE)
            )
        )


# --------------------------------------------------------------------------------------
# Migrating, after a dump
# --------------------------------------------------------------------------------------


def code_head(backend_dir: Path) -> str:
    """The one revision the migrations in `backend_dir` end at, read WITHOUT importing them.

    `ScriptDirectory.get_heads()` imports every revision file, and three of them import
    `app.core.config.settings` -- which builds the settings, once, from whatever environment
    exists at that moment. Called here, before `app.desktop` has set `DATABASE_URL`, that gave
    the whole process a stranger's database for its lifetime (caught by
    `tests/test_desktop.py::TestABundledBootOnAFreshHome`, not by reading). So the graph is read
    from the files' assignments instead; a test holds the answer equal to alembic's own.
    """
    revisions: dict[str, tuple[str, ...]] = {}
    for path in sorted((backend_dir / "migrations" / "versions").glob("*.py")):
        found: dict[str, Any] = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                found[target.id] = ast.literal_eval(value)
        if "revision" not in found:
            continue
        parents = found.get("down_revision")
        revisions[found["revision"]] = (
            () if parents is None else (parents,) if isinstance(parents, str) else tuple(parents)
        )
    referenced = {parent for parents in revisions.values() for parent in parents}
    heads = sorted(set(revisions) - referenced)
    if len(heads) != 1:
        raise ClusterError(
            f"The migrations have {len(heads)} heads ({', '.join(heads)}); this build is broken."
        )
    return heads[0]


def migration_state(
    url: str, backend_dir: Path, schema: str = APP_SCHEMA
) -> tuple[str | None, str]:
    """`(the revision the database is at, or None if it has none, the code's head)`."""
    head = code_head(backend_dir)
    current: str | None = None
    with psycopg.connect(url, connect_timeout=10) as connection:
        qualified = sql.Identifier(schema, "alembic_version").as_string(connection)
        exists = connection.execute("SELECT to_regclass(%s) IS NOT NULL", (qualified,)).fetchone()
        if exists and exists[0]:
            row = connection.execute(
                sql.SQL("SELECT version_num FROM {}").format(
                    sql.Identifier(schema, "alembic_version")
                )
            ).fetchone()
            current = row[0] if row else None
    return current, head


def back_up(
    bin_dir: Path,
    admin_url: str,
    backups: Path,
    label: str,
    *,
    stem: str | None = None,
    keep_glob: str = "kryova-before-*.dump",
    keep: int = KEEP_BACKUPS,
    row_security: bool = False,
    run: Runner = subprocess.run,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """`pg_dump` the application database to `backups/`, keeping the newest `keep` of a kind.

    The default is the dump taken before an upgrade, named for the revision it preserves.
    `stem`/`keep_glob`/`keep` let the scheduled dump (`app/core/backups.py`) share this one
    code path with its own name and its own retention, so a week of daily dumps never evicts
    the one taken before the last schema change. `row_security` adds `--enable-row-security`,
    which `CLAUDE.md` *Database* item 7 explains: complete only because every policy here admits
    every row when no tenant is set.
    """
    backups.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%dT%H%M%SZ")
    target = backups / f"{stem or f'kryova-before-{label}'}-{stamp}.dump"
    url = make_url(admin_url)
    result = run(
        [
            str(_exe(bin_dir, "pg_dump")),
            "-Fc",
            *(["--enable-row-security"] if row_security else []),
            "-h",
            url.host or "127.0.0.1",
            "-p",
            str(url.port),
            "-U",
            url.username or ADMIN_ROLE,
            "-d",
            url.database or APP_DATABASE,
            "-f",
            str(target),
        ],  # fmt: skip
        env={**os.environ, "PGPASSWORD": url.password or ""},
        capture_output=True,
        text=True,
        timeout=900,
        **_quiet(),
    )
    if result.returncode != 0:
        target.unlink(missing_ok=True)
        raise ClusterError(
            f"The backup before upgrading failed (pg_dump exit {result.returncode}): "
            f"{(result.stderr or '').strip()[-400:]} The database was left as it was."
        )
    for stale in sorted(backups.glob(keep_glob))[:-keep]:
        stale.unlink(missing_ok=True)
    return target


RESTORE_REQUEST = "restore-request.json"
RESTORE_FAILED = "restore-request.failed.json"

#: What a dump this module wrote is called: `kryova-before-<revision|restore>-<stamp>.dump` or
#: `kryova-scheduled-<stamp>.dump`. A restore names a file from this folder and nothing else.
BACKUP_NAME = re.compile(
    r"^kryova-(?:scheduled|before-[A-Za-z0-9_]+)-\d{8}T\d{6}Z\.dump$"
)


def request_restore(home: Path, name: str) -> Path:
    """Record that the next launch should restore `backups/<name>`. Returns the dump.

    A restore is **deferred to the next launch on purpose**: the running backend holds
    connections and open transactions on the very tables a restore drops, and a half-applied
    restore under a live application is the worst available shape of data loss. The request is
    a file, so it survives the app being closed between the click and the restart, and it can
    be taken back (`cancel_restore`) until then.

    `name` must be a bare file name matching what this module writes and must exist: a path, a
    `..` or a file somebody dropped in the folder by hand is refused, so the route that calls
    this cannot be made to feed `pg_restore` an arbitrary file.
    """
    if not BACKUP_NAME.fullmatch(name):
        raise ClusterError(f"{name!r} is not the name of a backup this app made.")
    dump = home / "backups" / name
    if not dump.is_file():
        raise ClusterError(f"There is no backup called {name!r} in the backups folder.")
    _write_private(
        home / RESTORE_REQUEST,
        json.dumps({"backup": name, "requested_at": datetime.now(UTC).isoformat()}),
    )
    return dump


def cancel_restore(home: Path) -> bool:
    """Take back a pending request. True if there was one."""
    path = home / RESTORE_REQUEST
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def pending_restore(home: Path) -> str | None:
    """The backup name a restore is waiting to apply, or None. Never raises."""
    try:
        data = json.loads((home / RESTORE_REQUEST).read_text(encoding="utf-8"))
        name = data.get("backup") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return name if isinstance(name, str) and BACKUP_NAME.fullmatch(name) else None


def apply_pending_restore(
    home: Path,
    bin_dir: Path,
    admin_url: str,
    *,
    run: Runner = subprocess.run,
) -> Path | None:
    """Apply a restore request, if there is one. Returns the dump restored from.

    Three rules, each because the alternative loses data quietly:

    1. **A safety dump of what is there comes first**, named `kryova-before-restore-...`. A
       restore is the one operation here that throws a database away on purpose, and the person
       who clicked it may have clicked the wrong file.
    2. **It runs as one transaction** (`--single-transaction`), so a restore that fails halfway
       leaves the database exactly as it was rather than half old and half new.
    3. **A failed restore does not become a launch that never ends.** The request is renamed
       to `restore-request.failed.json` with the reason, the launch carries on against the
       untouched database, and the reason is logged for the setup page. Retrying forever on
       every start is the other way to make an app unusable.
    """
    name = pending_restore(home)
    request = home / RESTORE_REQUEST
    if name is None:
        if request.exists():
            _fail_restore(home, "the request did not name a backup this app made")
        return None
    dump = home / "backups" / name
    if not dump.is_file():
        _fail_restore(home, f"{name} is no longer in the backups folder")
        return None
    url = make_url(admin_url)
    try:
        has_data = _has_tables(admin_url)
        if has_data:
            back_up(bin_dir, admin_url, home / "backups", "restore", run=run)
        result = run(
            [
                str(_exe(bin_dir, "pg_restore")),
                "--clean",
                "--if-exists",
                "--single-transaction",
                "--exit-on-error",
                "-h",
                url.host or "127.0.0.1",
                "-p",
                str(url.port),
                "-U",
                url.username or ADMIN_ROLE,
                "-d",
                url.database or APP_DATABASE,
                str(dump),
            ],  # fmt: skip
            env={**os.environ, "PGPASSWORD": url.password or ""},
            capture_output=True,
            text=True,
            timeout=1800,
            **_quiet(),
        )
    except (ClusterError, psycopg.Error, OSError, subprocess.SubprocessError) as error:
        _fail_restore(home, str(error))
        return None
    if result.returncode != 0:
        _fail_restore(
            home,
            f"pg_restore exit {result.returncode}: {(result.stderr or '').strip()[-400:]}",
        )
        return None
    request.unlink(missing_ok=True)
    logger.info("Restored the database from %s", dump)
    return dump


def _has_tables(admin_url: str) -> bool:
    with psycopg.connect(admin_url, connect_timeout=10) as connection:
        row = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s",
            (APP_SCHEMA,),
        ).fetchone()
    return bool(row and row[0])


def _fail_restore(home: Path, reason: str) -> None:
    logger.error(
        "The restore was not applied and the database is as it was: %s. The request is kept "
        "as %s.",
        reason,
        RESTORE_FAILED,
    )
    _write_private(
        home / RESTORE_FAILED,
        json.dumps({"reason": reason, "at": datetime.now(UTC).isoformat()}),
    )
    (home / RESTORE_REQUEST).unlink(missing_ok=True)


def migrate(
    app_url: str, backend_dir: Path, schema: str = APP_SCHEMA, *, run: Runner = subprocess.run
) -> None:
    """`alembic upgrade head` against exactly this database.

    In a child process, with the target in the child's environment: `env.py` reads
    `DATABASE_URL` from the settings, so a bare call migrates whatever the caller's
    environment names (CLAUDE.md Database 9 -- it migrated the wrong database once).
    """
    result = run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(backend_dir / "alembic.ini"),
            "upgrade",
            "head",
        ],
        cwd=str(backend_dir),
        env={**os.environ, "DATABASE_URL": app_url, "DB_SCHEMA": schema, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        timeout=900,
        **_quiet(),
    )
    if result.returncode != 0:
        raise ClusterError(
            f"The database upgrade failed (alembic exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout or '').strip()[-1200:]}\n"
            "The data is as it was before the upgrade; the backup, if one was taken, is in the "
            "backups folder beside the database."
        )


# --------------------------------------------------------------------------------------
# The whole of a launch
# --------------------------------------------------------------------------------------


def prepare(
    home: Path,
    bin_dir: Path,
    backend_dir: Path,
    secrets_: Secrets,
    *,
    preferred_port: int = DEFAULT_PORT,
    run: Runner = subprocess.run,
) -> Cluster:
    """Make or find the cluster, start it, provision it, back it up if it will change, migrate it."""
    data_dir = home / "pgdata"
    if not cluster_exists(data_dir):
        init_cluster(bin_dir, data_dir, secrets_, preferred_port=preferred_port, run=run)

    port = settle_port(data_dir, _server_running(bin_dir, data_dir, run))
    admin_url = database_url(ADMIN_ROLE, secrets_.admin_password, port, "postgres")
    app_url = database_url(APP_ROLE, secrets_.app_password, port, APP_DATABASE)

    try:
        local_postgres.ensure_running(admin_url, str(bin_dir), str(data_dir), run=run)
    except local_postgres.LocalPostgresError as error:
        raise ClusterError(str(error)) from error

    try:
        _provision(admin_url, secrets_)
        restored_from = apply_pending_restore(
            home,
            bin_dir,
            database_url(ADMIN_ROLE, secrets_.admin_password, port, APP_DATABASE),
            run=run,
        )
        if restored_from is not None:
            # A restore recreates objects as the admin; put the application role's state back
            # the way a first launch would have left it before anything connects as it.
            _provision(admin_url, secrets_)
        current, head = migration_state(app_url, backend_dir)
        backup: Path | None = None
        migrated = current != head
        if migrated:
            if current is not None:
                backup = back_up(
                    bin_dir,
                    database_url(ADMIN_ROLE, secrets_.admin_password, port, APP_DATABASE),
                    home / "backups",
                    current,
                    run=run,
                )
            migrate(app_url, backend_dir, run=run)
    except psycopg.Error as error:
        raise ClusterError(
            f"The local database refused a connection or a statement: {error}"
        ) from error

    return Cluster(
        data_dir=data_dir,
        bin_dir=bin_dir,
        port=port,
        app_url=app_url,
        admin_url=admin_url,
        backup=backup,
        migrated=migrated,
        restored_from=restored_from,
    )


def stop(bin_dir: Path, data_dir: Path, *, run: Runner = subprocess.run) -> bool:
    """Stop the server (`pg_ctl stop -m fast`). True if it was running and is now stopped.

    Called when the backend exits and again by the shell. A postmaster started by `pg_ctl`
    outlives the process that started it, and on Windows a running `postgres.exe` holds the
    install directory open: the next upgrade or uninstall then fails on "files in use" with no
    sign that a database is the cause. Never raises -- it runs on the way out.
    """
    try:
        if not _server_running(bin_dir, data_dir, run):
            return False
        result = run(
            [str(_exe(bin_dir, "pg_ctl")), "stop", "-D", str(data_dir), "-m", "fast", "-t", "30"],
            capture_output=True,
            text=True,
            timeout=60,
            **_quiet(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning("Could not stop the local Postgres: %s", error)
        return False
    if result.returncode != 0:
        logger.warning(
            "`pg_ctl stop` exited %d: %s", result.returncode, (result.stderr or "").strip()
        )
        return False
    return True
