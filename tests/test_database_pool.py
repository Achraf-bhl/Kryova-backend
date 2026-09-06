"""The pool's idle-threshold pre-ping (`app/core/database.install_idle_pre_ping`).

SQLAlchemy's own `pool_pre_ping` was costing a full Neon round trip on every
request -- 76 ms of a measured 640 ms `POST /projects`, for a connection that
had been in use a few milliseconds earlier. The replacement keeps the check and
drops the price by skipping it on a connection that has not had time to die.

Two claims are worth pinning, and both are pinned here by breaking the thing
they guard: a rested connection *is* pinged, and a ping that fails costs the
caller nothing because the pool reconnects underneath it.
"""

from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import QueuePool

from app.core.database import IDLE_SINCE_KEY, install_idle_pre_ping


class _CountingDialect:
    """Wraps a real dialect and records every `do_ping`, optionally failing."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.pings = 0
        self.fail_next = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def do_ping(self, dbapi_connection: Any) -> bool:
        self.pings += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise OSError("server closed the connection unexpectedly")
        return bool(self._wrapped.do_ping(dbapi_connection))


@pytest.fixture
def probe_engine(tmp_path: Any) -> Engine:
    """A real file-backed SQLite engine with a real (one-slot) pool.

    File-backed rather than in-memory: an in-memory database dies with the
    connection, so the reconnect path below would have nothing to reconnect to.
    """
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'pool.db'}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
    )
    engine.dialect = _CountingDialect(engine.dialect)  # type: ignore[assignment]
    return engine


def _counter(engine: Engine) -> _CountingDialect:
    return engine.dialect  # type: ignore[return-value]


def test_a_connection_just_returned_is_not_pinged(probe_engine: Engine) -> None:
    install_idle_pre_ping(probe_engine, idle_seconds=60.0)

    with probe_engine.connect() as first:
        first.execute(text("select 1"))
    pings_after_first = _counter(probe_engine).pings

    with probe_engine.connect() as second:
        second.execute(text("select 1"))

    # The first checkout has no recorded idle time, so it pings; the second
    # reuses a connection returned microseconds ago and must not.
    assert _counter(probe_engine).pings == pings_after_first == 1


def test_a_rested_connection_is_pinged(probe_engine: Engine) -> None:
    install_idle_pre_ping(probe_engine, idle_seconds=60.0)

    with probe_engine.connect() as first:
        first.execute(text("select 1"))

    # Age the pooled connection past the threshold without sleeping for a
    # minute: the record's stamp is what the listener reads.
    for record in [probe_engine.pool._pool.queue[0]]:  # type: ignore[attr-defined]
        record.info[IDLE_SINCE_KEY] -= 3600.0

    with probe_engine.connect() as second:
        second.execute(text("select 1"))

    assert _counter(probe_engine).pings == 2


def test_a_dead_pooled_connection_is_replaced_rather_than_raised(
    probe_engine: Engine,
) -> None:
    """The whole point of pinging at all: the caller never sees the failure."""
    install_idle_pre_ping(probe_engine, idle_seconds=0.0)

    with probe_engine.connect() as first:
        first.execute(text("select 1"))

    _counter(probe_engine).fail_next = 1
    with probe_engine.connect() as second:
        assert second.execute(text("select 1")).scalar() == 1

    # Two successful pings either side of the one that failed, and no error out.
    assert _counter(probe_engine).pings == 3
