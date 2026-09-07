"""How the connection decides whether to demand TLS (`app.core.database.sslmode_for`).

`create_engine` used to be handed a hardcoded ``connect_args={"sslmode":
"require"}``. That was correct for the only server that had ever existed here
and refuses every local one: a stock Postgres build ships ``ssl = off`` and
answers the handshake with a rejection, so psycopg raises "server does not
support SSL, but SSL was required" before a single statement runs. A developer
wanting a local Postgres -- which CLAUDE.md names as the fix if the ~4 minute
Neon suite ever gets painful -- could not have one without editing the module.

The claim under test is that reading it off the URL changed nothing for the
deployment that exists. Neon's connection string already carries
``?sslmode=require``, so the hardcoded argument was only restating what the URL
said, and a URL that says nothing is still refused an unencrypted connection.
Going plaintext has to be written down.

Each test is the guard broken: a URL that would have connected under the old
constant and one that would not, and the two ways a URL can decline to answer
the question at all.
"""

import pytest

from app.core.database import sslmode_for

NEON = "postgresql+psycopg://u:p@ep-x-pooler.eu-central-1.aws.neon.tech/db?sslmode=require"


def test_the_neon_url_still_requires_tls() -> None:
    """The deployment that exists is unchanged -- this is the regression guard.

    If this ever returns anything but ``require``, the production database is
    being connected to in plaintext, which is the whole risk of the change.
    """
    assert sslmode_for(NEON) == "require"


def test_a_url_that_says_nothing_still_requires_tls() -> None:
    """Silence is not permission. A URL with no `sslmode` gets the old constant.

    This is the case that matters most: it is what a hand-written or
    copy-pasted production URL looks like, and defaulting it to anything but
    `require` would turn a missing query parameter into an unencrypted
    connection to a remote host.
    """
    assert sslmode_for("postgresql+psycopg://u:p@db.example.com/kryova") == "require"


def test_a_local_server_can_ask_for_plaintext_in_the_url() -> None:
    """The point of the change: `sslmode=disable` is honoured, and only there.

    A local Postgres is reachable without the module being edited, and the
    decision to go plaintext is recorded in the one place a connection is
    already described.
    """
    assert sslmode_for("postgresql+psycopg://u:p@localhost:5432/kryova?sslmode=disable") == "disable"


@pytest.mark.parametrize(
    "mode", ["require", "disable", "prefer", "allow", "verify-ca", "verify-full"]
)
def test_every_libpq_mode_is_passed_through_rather_than_vetted(mode: str) -> None:
    """The URL is read, not judged.

    libpq owns this vocabulary and has extended it before (`verify-ca` and
    `verify-full` postdate the other four). A whitelist here would reject a mode
    Postgres itself accepts, and psycopg's own error for a genuinely bad value
    names the offending word -- which is a better message than anything this
    function could invent.
    """
    assert sslmode_for(f"postgresql+psycopg://u:p@h/db?sslmode={mode}") == mode


def test_a_repeated_key_reads_the_way_libpq_reads_it() -> None:
    """`?sslmode=require&sslmode=disable` is a tuple, not a string, and is real.

    Measured against SQLAlchemy 2.0's `make_url`, not assumed: a repeated key
    parses to `('require', 'disable')`. libpq takes the last occurrence, so this
    matches it. The alternative -- letting the `str` annotation be a lie and
    handing a tuple to `connect_args` -- fails at import time, on a URL Postgres
    itself would have accepted.
    """
    url = "postgresql+psycopg://u:p@h/db?sslmode=require&sslmode=disable"
    assert sslmode_for(url) == "disable"


def test_a_key_with_no_value_is_the_same_as_no_key() -> None:
    """`?sslmode=` requires TLS, and pins why the tuple branch needs no empty case.

    Measured: `make_url` drops a key with an empty value entirely, so `?sslmode=`
    reaches `sslmode_for` as an absent key and takes the default. It can never
    arrive as a zero-length tuple, which is why the repeated-key branch indexes
    `[-1]` without guarding for one -- a guard that cannot be made to fire is
    dead code standing where a real check should be.
    """
    assert sslmode_for("postgresql+psycopg://u:p@h/db?sslmode=") == "require"
