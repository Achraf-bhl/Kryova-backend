"""ROAD_TO_10 9.6: observability an operator can read.

The span ledger is pure and is tested without a database; turn cost, bridge latency and the
queue are read from rows and use `db_session`. Each rule in `app/observe/ledger.py` and
`app/observe/ops.py` has a test that goes red when the rule is removed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import CatiaOperation, StaffGrant, StaffRole, TurnMetric
from app.models.base import utcnow
from app.observe import collect, ledger, ops
from app.observe.ledger import SCOPE, WINDOW, SpanLedger
from app.observe.records import Span
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _span(name: str, seconds: float, *, ok: bool = True) -> Span:
    return Span(name=name, seconds=seconds, ok=ok)


class TestTheLedgerKeepsTheNewestAndCountsEverything:
    def test_it_reports_percentiles_per_site(self) -> None:
        book = SpanLedger()
        for value in range(1, 101):
            book(_span("mesh.gmsh.session", value / 100))
        (row,) = book.snapshot().sites
        assert row.name == "mesh.gmsh.session"
        assert row.median_seconds == pytest.approx(0.50, abs=0.011)
        assert row.p95_seconds == pytest.approx(0.95, abs=0.011)
        assert row.max_seconds == pytest.approx(1.0)
        assert not row.p95_is_the_maximum

    def test_a_small_sample_says_its_p95_is_the_maximum(self) -> None:
        book = SpanLedger()
        for value in (0.1, 0.2, 0.9):
            book(_span("kernel.rebuild", value))
        (row,) = book.snapshot().sites
        assert row.p95_seconds == row.max_seconds == pytest.approx(0.9)
        assert row.p95_is_the_maximum

    def test_the_window_is_the_newest_n_and_seen_counts_past_it(self) -> None:
        book = SpanLedger(window=10)
        for value in range(25):
            book(_span("x", float(value)))
        (row,) = book.snapshot().sites
        assert row.window_size == 10
        assert row.seen == 25
        # Only the newest ten (15..24) are in the window.
        assert row.max_seconds == 24.0
        assert row.median_seconds >= 15.0

    def test_failures_are_counted_apart_from_durations(self) -> None:
        book = SpanLedger()
        book(_span("x", 0.1))
        book(_span("x", 0.2, ok=False))
        (row,) = book.snapshot().sites
        assert (row.seen, row.failures) == (2, 1)

    def test_sites_are_listed_in_name_order_and_an_empty_ledger_has_none(self) -> None:
        book = SpanLedger()
        assert book.snapshot().sites == ()
        book(_span("b", 1.0))
        book(_span("a", 1.0))
        assert [row.name for row in book.snapshot().sites] == ["a", "b"]

    def test_it_says_whose_view_it_is(self) -> None:
        assert SpanLedger().snapshot().scope == SCOPE
        assert "process" in SCOPE and str(WINDOW) in SCOPE

    def test_a_listener_that_cannot_record_does_not_break_the_work(self) -> None:
        book = SpanLedger()
        book("not a span")  # type: ignore[arg-type]  -- raises inside, and is swallowed
        assert book.snapshot().sites == ()

    def test_reset_forgets_everything(self) -> None:
        book = SpanLedger()
        book(_span("x", 1.0))
        book.reset()
        assert book.snapshot().sites == ()


class TestInstallingItIsReversible:
    def test_install_registers_one_listener_and_uninstall_removes_it(self) -> None:
        assert not ledger.is_installed()
        before = collect.listeners()
        try:
            ledger.install()
            ledger.install()  # idempotent
            assert collect.listeners().count(ledger.LEDGER) == 1
        finally:
            ledger.uninstall()
        assert collect.listeners() == before
        assert not ledger.is_installed()

    def test_a_span_run_while_installed_lands_in_the_ledger(self) -> None:
        ledger.LEDGER.reset()
        try:
            ledger.install()
            with collect.span("kernel.rebuild"):
                pass
        finally:
            ledger.uninstall()
        names = [row.name for row in ledger.LEDGER.snapshot().sites]
        ledger.LEDGER.reset()
        assert "kernel.rebuild" in names

    def test_after_uninstall_spans_are_inert_again(self) -> None:
        ledger.install()
        ledger.uninstall()
        assert collect.span("kernel.rebuild") is collect.INERT


def _turn(user_id: str, *, cost: int | None, wall_ms: int, reason: str = "finished") -> TurnMetric:
    return TurnMetric(
        user_id=user_id,
        provider="deepseek",
        model="deepseek-flash",
        stop_reason=reason,
        prompt_tokens=100,
        cost_micro_usd=cost,
        wall_ms=wall_ms,
        created_at=utcnow() - timedelta(minutes=5),
    )


class TestTurnCost:
    def test_an_empty_window_is_none_not_zero(
        self, db_session: Session
    ) -> None:
        got = ops.turn_cost(db_session, utcnow() - timedelta(hours=1))
        assert got.turns == 0 and got.mean_micro_usd is None and got.median_wall_ms is None

    def test_unpriced_turns_are_counted_apart_and_never_as_free(
        self, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add_all(
            [
                _turn(current_user_id, cost=3000, wall_ms=1000),
                _turn(current_user_id, cost=1000, wall_ms=3000),
                _turn(current_user_id, cost=None, wall_ms=2000),
            ]
        )
        db_session.flush()
        got = ops.turn_cost(db_session, utcnow() - timedelta(hours=1))
        assert (got.turns, got.priced_turns, got.unpriced_turns) == (3, 2, 1)
        assert got.total_micro_usd == 4000
        # The mean is over the two priced turns, not three: dividing by 3 would call the
        # unpriced turn free.
        assert got.mean_micro_usd == 2000
        assert got.max_micro_usd == 3000

    def test_turns_are_grouped_by_how_they_ended_most_common_first(
        self, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add_all(
            [
                _turn(current_user_id, cost=1, wall_ms=1, reason="step_budget"),
                _turn(current_user_id, cost=1, wall_ms=1, reason="step_budget"),
                _turn(current_user_id, cost=1, wall_ms=1, reason="finished"),
            ]
        )
        db_session.flush()
        got = ops.turn_cost(db_session, utcnow() - timedelta(hours=1))
        assert list(got.by_stop_reason.items()) == [("step_budget", 2), ("finished", 1)]

    def test_a_turn_outside_the_window_is_not_counted(
        self, db_session: Session, current_user_id: str
    ) -> None:
        old = _turn(current_user_id, cost=5, wall_ms=5)
        old.created_at = utcnow() - timedelta(hours=48)
        db_session.add(old)
        db_session.flush()
        assert ops.turn_cost(db_session, utcnow() - timedelta(hours=24)).turns == 0


def _operation(tool: str, ms: int, *, ok: bool = True) -> CatiaOperation:
    return CatiaOperation(
        tool=tool, tier="write", arguments={}, ok=ok, duration_ms=ms, created_at=utcnow()
    )


class TestBridgeLatency:
    def test_each_tool_has_its_own_figures_slowest_tail_first(self, db_session: Session) -> None:
        db_session.add_all(
            [_operation("catia_pad", ms) for ms in (100, 200, 300)]
            + [_operation("catia_fillet", 5000)]
            + [_operation("catia_pad", 400, ok=False)]
        )
        db_session.flush()
        got = ops.bridge_latency(db_session, utcnow() - timedelta(hours=1))
        assert [row.tool for row in got.operations] == ["catia_fillet", "catia_pad"]
        pad = got.operations[1]
        assert (pad.count, pad.failures, pad.max_ms) == (4, 1, 400)
        assert pad.p95_is_the_maximum  # four samples: p95 is not a tail
        assert not got.truncated

    def test_it_reads_a_bounded_number_of_rows_and_says_so(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ops, "MAX_OPERATIONS", 3)
        db_session.add_all([_operation("t", 10) for _ in range(5)])
        db_session.flush()
        got = ops.bridge_latency(db_session, utcnow() - timedelta(hours=1))
        assert got.truncated
        assert got.operations[0].count == 3

    def test_no_operations_is_an_empty_list(self, db_session: Session) -> None:
        got = ops.bridge_latency(db_session, utcnow() - timedelta(hours=1))
        assert got.operations == () and not got.truncated


class TestQueueDepth:
    def test_every_status_is_present_even_when_nothing_is_in_it(self, db_session: Session) -> None:
        depth = ops.queue_depth(db_session)
        assert depth and all(count >= 0 for count in depth.values())
        assert "waiting" in depth


class TestTheAdminRoute:
    def _staff(self, db_session: Session, user_id: str) -> None:
        db_session.add(StaffGrant(user_id=user_id, role=StaffRole.SUPPORT))
        db_session.flush()

    def test_a_customer_is_told_there_is_no_such_page(
        self, auth_client: AuthenticatedTestClient
    ) -> None:
        assert auth_client.get(f"{API}/admin/observability").status_code == 404

    def test_staff_get_all_four_sources_with_their_scopes(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        self._staff(db_session, current_user_id)
        ledger.LEDGER.reset()
        ledger.LEDGER(_span("kernel.rebuild", 0.25))
        body = auth_client.get(f"{API}/admin/observability").json()
        ledger.LEDGER.reset()
        assert set(body) == {"window_hours", "spans", "turns", "ai_cache", "queue_depth", "bridge"}
        assert "process" in body["spans"]["scope"]
        assert body["spans"]["sites"][0]["name"] == "kernel.rebuild"
        assert body["turns"]["turns"] == 0 and body["turns"]["mean_micro_usd"] is None
        assert body["ai_cache"]["reported"] is False
        assert body["bridge"]["operations"] == []

    def test_the_window_is_bounded(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        self._staff(db_session, current_user_id)
        assert auth_client.get(f"{API}/admin/observability?hours=0").status_code == 422
        assert auth_client.get(f"{API}/admin/observability?hours=721").status_code == 422
