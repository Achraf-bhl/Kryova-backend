"""Split the suite into the part that needs a database and the part that does not.

CI runs on machines with no Neon connection, so most of `tests/` cannot run on
every push. The temptation is to run what can run and let the badge say "CI
passing" -- which is the exact failure this project's verification stance
exists to prevent: a green mark standing for a suite nobody executed.

So the split is explicit and it is *reported*. `--offline-only` runs the tests
that open no connection and prints, in the terminal summary, how many database
tests it deliberately did not run. `--database-only` runs the other half and
**refuses to start** unless it has a real PostgreSQL to run against.

That refusal is the load-bearing part. `tests/conftest.py` falls back to
in-memory SQLite when `TEST_DATABASE_URL` is unset -- a good default for a
laptop, and a silent lie in CI, where it would let a job named "database"
report green having exercised no Postgres at all. CLAUDE.md is explicit that
testing on SQLite while shipping on Postgres is the drift that hides JSONB,
enum and cascade bugs; a job that claims to have closed that gap and did not is
worse than no job.

The classification is by **fixture**, not by filename. `item.fixturenames` is
the transitive closure of what a test resolves, and every database-backed
fixture in `conftest.py` (`db_session`, `client`, `auth_client`, `project_id`,
`current_user_id`) reaches the database through exactly one root fixture. So
the rule below is the whole rule -- it needs no maintenance when a test file is
added, and it is correct *per test* rather than per file, which matters:
`tests/test_database_isolation.py` holds both kinds.

Run it the same way locally as CI does:

    PYTHONPATH=scripts pytest -p pytest_split --offline-only
    TEST_DATABASE_URL=postgresql://... PYTHONPATH=scripts pytest -p pytest_split --database-only

On Windows, `venv\\Scripts\\python.exe -m pytest ...` with `set PYTHONPATH=scripts`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _pytest.config import Config
    from _pytest.config.argparsing import Parser
    from _pytest.nodes import Item
    from _pytest.terminal import TerminalReporter

#: The one fixture every database-backed test resolves through, directly or
#: transitively. Change this only if `tests/conftest.py` grows a second root
#: that opens a connection without going through `db_connection`.
DATABASE_FIXTURE = "db_connection"

_DESELECTED_COUNT = pytest.StashKey[int]()
_MODE = pytest.StashKey[str]()


def pytest_addoption(parser: Parser) -> None:
    group = parser.getgroup("kryova", "Kryova suite selection")
    group.addoption(
        "--offline-only",
        action="store_true",
        default=False,
        help="Run only tests that need no database (no connection is opened).",
    )
    group.addoption(
        "--database-only",
        action="store_true",
        default=False,
        help="Run only tests that need a database. Requires a real TEST_DATABASE_URL.",
    )


def pytest_configure(config: Config) -> None:
    offline = bool(config.getoption("offline_only"))
    database = bool(config.getoption("database_only"))

    if offline and database:
        raise pytest.UsageError(
            "--offline-only and --database-only select disjoint halves of the suite; "
            "passing both selects nothing. Run pytest twice, or pass neither to run all."
        )

    config.stash[_MODE] = "offline" if offline else "database" if database else "all"
    if not database:
        return

    # Everything below is the guard described in the module docstring: a
    # database job that quietly ran on SQLite must not be able to report green.
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        raise pytest.UsageError(
            "--database-only needs TEST_DATABASE_URL. Left unset, tests/conftest.py "
            "falls back to in-memory SQLite and the run would prove nothing about "
            "PostgreSQL while reporting as the database suite."
        )
    if url.startswith("sqlite"):
        raise pytest.UsageError(
            f"--database-only was given a SQLite TEST_DATABASE_URL ({url!r}). "
            "The point of this job is the PostgreSQL behaviour -- JSONB, enums, "
            "cascades, schema translation -- that SQLite does not have."
        )


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    mode = config.stash.get(_MODE, "all")
    if mode == "all":
        return

    wanted_needs_database = mode == "database"
    selected: list[Item] = []
    deselected: list[Item] = []
    for item in items:
        needs_database = DATABASE_FIXTURE in getattr(item, "fixturenames", ())
        (selected if needs_database == wanted_needs_database else deselected).append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected

    config.stash[_DESELECTED_COUNT] = len(deselected)


def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    """Say out loud what this run did not cover.

    A reader of the log should never have to know which flag was passed to work
    out whether the database half ran.
    """
    config = terminalreporter.config
    mode = config.stash.get(_MODE, "all")
    if mode == "all":
        return

    count = config.stash.get(_DESELECTED_COUNT, 0)
    if mode == "offline":
        terminalreporter.write_sep(
            "-",
            f"offline suite only: {count} database-backed tests were NOT run in this job",
        )
    else:
        terminalreporter.write_sep(
            "-",
            f"database suite only: {count} offline tests were NOT run in this job "
            f"(TEST_DATABASE_URL={os.environ.get('TEST_DATABASE_URL', '')!r})",
        )
