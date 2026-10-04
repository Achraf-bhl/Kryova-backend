"""ROAD_TO_10 1.11: the prompt-cache hit rate as a number an operator can watch.

`assess` is arithmetic and is tested with numbers; `read` and the admin route are tested
against real `turn_metrics` rows. Each rule in `app/ai/cache_health.py`'s docstring has a test
here that goes red when the rule is removed.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.ai import cache_health
from app.ai.cache_health import (
    DROP_FACTOR,
    HEALTHY_RATE,
    MIN_BASELINE_TURNS,
    MIN_RECENT_TURNS,
    RECENT_TURNS,
    assess,
)
from app.models import StaffGrant, StaffRole, TurnMetric
from app.models.base import utcnow
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _window(
    recent: list[tuple[int, int]], earlier: list[tuple[int, int]]
) -> cache_health.CacheHealth:
    """`assess` over `earlier` turns followed by `recent` ones, totals computed the way SQL does."""
    everything = recent + earlier
    return assess(
        turns=len(everything),
        prompt_tokens=sum(p for p, _ in everything),
        cached_prompt_tokens=sum(c for _, c in everything),
        recent=recent,
    )


def _steady(rate: float, turns: int, prompt: int = 10_000) -> list[tuple[int, int]]:
    return [(prompt, int(prompt * rate))] * turns


class TestTheRateIsWeightedByTokens:
    def test_a_huge_turn_outweighs_many_tiny_ones(self) -> None:
        # Ten cold one-step turns of 100 tokens and one warm sixty-step turn of 100,000:
        # averaged per turn this is ~9%, weighted by tokens it is ~90% -- the money is in
        # the big turn, and the number has to say so.
        small = [(100, 0)] * 10
        big = [(100_000, 90_000)]
        health = assess(
            turns=11,
            prompt_tokens=1_000 + 100_000,
            cached_prompt_tokens=90_000,
            recent=big + small,
        )
        assert health.hit_rate == pytest.approx(90_000 / 101_000)
        assert health.hit_rate is not None and health.hit_rate > 0.85

    def test_the_recent_rate_is_weighted_too(self) -> None:
        recent = [(100_000, 90_000)] + [(100, 0)] * 11
        health = _window(recent, _steady(0.9, MIN_BASELINE_TURNS))
        assert health.recent_hit_rate == pytest.approx(90_000 / 101_100)

    def test_a_cached_count_larger_than_the_prompt_cannot_exceed_one(self) -> None:
        # A provider that reports a cached count above the prompt count is wrong about
        # one of them; a rate over 100% on a dashboard is how that goes unnoticed.
        health = assess(turns=1, prompt_tokens=100, cached_prompt_tokens=250, recent=[(100, 250)])
        assert health.hit_rate == 1.0


class TestAZeroRateIsNotADrop:
    def test_a_provider_that_reports_no_cache_never_alerts(self) -> None:
        health = _window(_steady(0.0, 20), _steady(0.0, 40))
        assert health.alert is None
        assert health.reported is False
        assert health.hit_rate == 0.0

    def test_an_earlier_stretch_below_the_healthy_floor_is_not_a_baseline(self) -> None:
        # 30% then 5%: a six-fold fall, but 30% is below HEALTHY_RATE, which is what a
        # deployment with a barely-working cache looks like all the time.
        assert 0.3 < HEALTHY_RATE
        health = _window(_steady(0.05, 20), _steady(0.3, 40))
        assert health.alert is None
        assert health.reported is True

    def test_the_floor_is_inclusive(self) -> None:
        recent = _steady(0.1, 20)
        health = _window(recent, _steady(HEALTHY_RATE, 40))
        assert health.alert is not None

    def test_reported_is_true_as_soon_as_one_turn_recorded_a_cached_count(self) -> None:
        health = _window([(10_000, 0)] * 19 + [(10_000, 1)], _steady(0.0, 40))
        assert health.reported is True


class TestTheAlert:
    def test_a_real_fall_alerts_and_names_both_rates(self) -> None:
        health = _window(_steady(0.1, 20), _steady(0.85, 60))
        assert health.alert is not None
        assert "85%" in health.alert
        assert "10%" in health.alert
        assert "20" in health.alert  # the number of recent turns it was measured over

    def test_it_says_what_to_look_at_not_just_that_something_is_wrong(self) -> None:
        health = _window(_steady(0.1, 20), _steady(0.85, 60))
        assert health.alert is not None
        assert "ahead of the transcript" in health.alert
        assert "two consecutive steps" in health.alert

    def test_a_small_wobble_is_not_a_fall(self) -> None:
        health = _window(_steady(0.7, 20), _steady(0.85, 60))
        assert health.alert is None

    def test_exactly_at_the_drop_factor_is_not_a_fall(self) -> None:
        # 0.5 vs 0.8333 would be "under"; pick numbers that land exactly on the factor.
        baseline = 1.0
        recent = DROP_FACTOR * baseline
        health = _window(_steady(recent, 20), _steady(baseline, 60))
        assert health.alert is None

    def test_just_under_the_drop_factor_is_a_fall(self) -> None:
        health = _window(_steady(DROP_FACTOR - 0.05, 20), _steady(1.0, 60))
        assert health.alert is not None

    def test_a_rise_never_alerts(self) -> None:
        assert _window(_steady(0.95, 20), _steady(0.45, 60)).alert is None


class TestTooFewTurnsIsNotARate:
    def test_too_few_recent_turns_gives_no_recent_rate_and_no_alert(self) -> None:
        recent = _steady(0.0, MIN_RECENT_TURNS - 1)
        health = _window(recent, _steady(0.9, 60))
        assert health.recent_hit_rate is None
        assert health.alert is None
        assert health.recent_turns == MIN_RECENT_TURNS - 1

    def test_the_minimum_recent_count_is_enough(self) -> None:
        health = _window(_steady(0.0, MIN_RECENT_TURNS), _steady(0.9, 60))
        assert health.recent_hit_rate == 0.0
        assert health.alert is not None

    def test_too_few_earlier_turns_is_no_baseline_and_no_alert(self) -> None:
        health = _window(_steady(0.0, 20), _steady(0.9, MIN_BASELINE_TURNS - 1))
        assert health.alert is None
        assert health.recent_hit_rate == 0.0

    def test_the_minimum_earlier_count_is_enough(self) -> None:
        health = _window(_steady(0.0, 20), _steady(0.9, MIN_BASELINE_TURNS))
        assert health.alert is not None

    def test_an_empty_window_has_no_rate_at_all(self) -> None:
        health = assess(turns=0, prompt_tokens=0, cached_prompt_tokens=0, recent=[])
        assert health.hit_rate is None
        assert health.recent_hit_rate is None
        assert health.reported is False
        assert health.alert is None


class TestTheRecentStretchIsBounded:
    def test_only_the_newest_turns_count_as_recent(self) -> None:
        # Sixty rows are handed in; only RECENT_TURNS of them (the first, newest) are the
        # recent stretch, and the baseline is everything else. Newest rows are cold, the rest
        # warm: if `recent` were not clipped the "earlier" stretch would be misjudged.
        cold = _steady(0.0, RECENT_TURNS)
        warm = _steady(0.9, 40)
        health = assess(
            turns=len(cold) + len(warm),
            prompt_tokens=sum(p for p, _ in cold + warm),
            cached_prompt_tokens=sum(c for _, c in cold + warm),
            recent=cold + warm,
        )
        assert health.recent_turns == RECENT_TURNS
        assert health.recent_hit_rate == 0.0
        assert health.alert is not None

    def test_the_earlier_stretch_is_the_totals_less_the_recent_turns(self) -> None:
        # Recent is as cold as possible and the earlier stretch exactly at the floor: if the
        # recent turns were left inside the baseline it would read below the floor and the
        # alert would vanish.
        health = _window(_steady(0.0, 20), _steady(HEALTHY_RATE, 20))
        assert health.alert is not None


# --- against the table ---------------------------------------------------------------------


def _add_turns(
    db: Session,
    user_id: str,
    rows: list[tuple[int, int]],
    *,
    newest_minutes_ago: int = 1,
) -> None:
    """`rows` newest first, one minute apart, the newest `newest_minutes_ago` back."""
    now = utcnow()
    for i, (prompt, cached) in enumerate(rows):
        db.add(
            TurnMetric(
                user_id=user_id,
                provider="deepseek",
                model="deepseek-flash",
                stop_reason="finished",
                prompt_tokens=prompt,
                cached_prompt_tokens=cached,
                created_at=now - timedelta(minutes=newest_minutes_ago + i),
            )
        )
    db.flush()


class TestReadingTheTable:
    def test_the_newest_rows_are_the_recent_stretch(
        self, db_session: Session, current_user_id: str
    ) -> None:
        _add_turns(db_session, current_user_id, _steady(0.0, 20) + _steady(0.9, 40))
        health = cache_health.read(db_session, utcnow() - timedelta(hours=24))
        assert health.turns == 60
        assert health.recent_hit_rate == 0.0
        assert health.alert is not None

    def test_the_same_rows_the_other_way_round_do_not_alert(
        self, db_session: Session, current_user_id: str
    ) -> None:
        _add_turns(db_session, current_user_id, _steady(0.9, 20) + _steady(0.0, 40))
        health = cache_health.read(db_session, utcnow() - timedelta(hours=24))
        assert health.recent_hit_rate is not None and health.recent_hit_rate > 0.85
        assert health.alert is None

    def test_a_turn_that_reached_no_model_is_not_a_turn_of_this_question(
        self, db_session: Session, current_user_id: str
    ) -> None:
        _add_turns(db_session, current_user_id, [(0, 0)] * 50 + _steady(0.9, 30))
        health = cache_health.read(db_session, utcnow() - timedelta(hours=24))
        assert health.turns == 30
        assert health.hit_rate == pytest.approx(0.9)

    def test_rows_before_the_window_are_left_out(
        self, db_session: Session, current_user_id: str
    ) -> None:
        _add_turns(db_session, current_user_id, _steady(0.9, 30))
        _add_turns(db_session, current_user_id, _steady(0.0, 30), newest_minutes_ago=60 * 48)
        health = cache_health.read(db_session, utcnow() - timedelta(hours=24))
        assert health.turns == 30
        assert health.hit_rate == pytest.approx(0.9)

    def test_an_empty_table_is_unmeasured_not_zero(self, db_session: Session) -> None:
        health = cache_health.read(db_session, utcnow() - timedelta(hours=24))
        assert health.turns == 0
        assert health.hit_rate is None
        assert health.alert is None

    def test_a_long_window_is_two_queries_not_a_scan_of_every_row(
        self, db_session: Session, current_user_id: str
    ) -> None:
        from sqlalchemy import event

        _add_turns(db_session, current_user_id, _steady(0.5, 150))
        statements: list[str] = []

        def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
            statements.append(statement)

        bind = db_session.get_bind()
        event.listen(bind, "before_cursor_execute", record)
        try:
            cache_health.read(db_session, utcnow() - timedelta(hours=24))
        finally:
            event.remove(bind, "before_cursor_execute", record)
        assert len([s for s in statements if "turn_metrics" in s]) == 2


class TestTheAdminConsoleReadsIt:
    def _staff(self, db_session: Session, user_id: str) -> None:
        db_session.add(StaffGrant(user_id=user_id, role=StaffRole.SUPPORT))
        db_session.flush()

    def test_an_empty_deployment_reports_unmeasured_not_a_rate_of_zero(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        self._staff(db_session, current_user_id)
        body = auth_client.get(f"{API}/admin/health").json()
        assert body["ai_cache"] == {
            "turns": 0,
            "prompt_tokens": 0,
            "cached_prompt_tokens": 0,
            "hit_rate": None,
            "recent_turns": 0,
            "recent_hit_rate": None,
            "reported": False,
            "alert": None,
        }

    def test_a_fall_in_the_cache_reaches_the_console_with_its_sentence(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        self._staff(db_session, current_user_id)
        _add_turns(db_session, current_user_id, _steady(0.0, 20) + _steady(0.9, 40))
        body = auth_client.get(f"{API}/admin/health").json()["ai_cache"]
        assert body["turns"] == 60
        assert body["reported"] is True
        assert body["alert"] is not None
        assert "fell from 90% over the earlier turns to 0%" in body["alert"]

    def test_the_window_parameter_narrows_it(
        self,
        auth_client: AuthenticatedTestClient,
        db_session: Session,
        current_user_id: str,
    ) -> None:
        self._staff(db_session, current_user_id)
        _add_turns(db_session, current_user_id, _steady(0.9, 30))
        _add_turns(db_session, current_user_id, _steady(0.9, 30), newest_minutes_ago=60 * 5)
        narrow = auth_client.get(f"{API}/admin/health?hours=2").json()["ai_cache"]
        wide = auth_client.get(f"{API}/admin/health?hours=24").json()["ai_cache"]
        assert narrow["turns"] == 30
        assert wide["turns"] == 60
