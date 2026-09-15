"""The autoscale recommendation: a worker count from the job table, and why (E15.2).

The policy is pure and is tested offline, rule by rule, in the order the module
docstring states them. One class goes through the admin route against the
database, so the number an orchestrator reads is the one the rules give.

**Written on Linux on 2026-09-15 and not run there**, at the user's instruction
that the Windows machine runs the tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.jobs.autoscale import (
    POLICY_NAME,
    QueueSnapshot,
    ScalingPolicy,
    recommend,
    wait_seconds,
)
from app.models import StaffGrant, StaffRole
from tests.typing import AuthenticatedTestClient

API = "/api/v1"


def _policy(**overrides: object) -> ScalingPolicy:
    kwargs: dict[str, object] = {
        "min_workers": 1,
        "max_workers": 8,
        "jobs_per_worker": 2,
        "target_wait_s": 120.0,
    }
    kwargs.update(overrides)
    return ScalingPolicy(**kwargs)  # type: ignore[arg-type]


def _queue(queued: int, running: int, wait: float | None = None) -> QueueSnapshot:
    if queued and wait is None:
        wait = 1.0
    return QueueSnapshot(queued=queued, running=running, oldest_wait_s=wait)


class TestRuleOneTheBacklogDecides:
    def test_the_backlog_is_divided_by_jobs_per_worker_and_rounded_up(self) -> None:
        assert recommend(_queue(5, 0), _policy()).desired_workers == 3

    def test_running_jobs_count_toward_the_backlog(self) -> None:
        assert recommend(_queue(1, 4), _policy()).desired_workers == 3

    def test_an_empty_queue_falls_to_the_floor(self) -> None:
        answer = recommend(_queue(0, 0), _policy(min_workers=1))
        assert answer.desired_workers == 1
        assert "min_workers" in answer.reason

    def test_a_floor_of_zero_lets_an_idle_fleet_scale_to_nothing(self) -> None:
        assert recommend(_queue(0, 0), _policy(min_workers=0)).desired_workers == 0


class TestRuleTwoARunningJobKeepsItsWorker:
    def test_step_down_never_goes_below_what_is_running(self) -> None:
        answer = recommend(_queue(0, 6), _policy(), current_workers=4)
        assert answer.desired_workers == 3


class TestRuleThreeWaitTimeWins:
    def test_a_job_waiting_past_the_target_adds_a_worker_the_backlog_would_not(self) -> None:
        answer = recommend(_queue(1, 1, wait=600.0), _policy(), current_workers=1)
        assert answer.desired_workers == 2
        assert "600 s" in answer.reason

    def test_a_short_wait_adds_nothing(self) -> None:
        answer = recommend(_queue(1, 1, wait=5.0), _policy(), current_workers=1)
        assert answer.desired_workers == 1

    def test_without_the_current_size_the_wait_rule_cannot_act(self) -> None:
        """One more than an unknown fleet is not a number."""
        answer = recommend(_queue(1, 1, wait=600.0), _policy())
        assert answer.desired_workers == 1
        assert answer.current_workers is None


class TestRuleFourDownOneStepAtATime:
    def test_a_drained_queue_removes_one_worker_not_all_of_them(self) -> None:
        answer = recommend(_queue(0, 0), _policy(), current_workers=6)
        assert answer.desired_workers == 5
        assert "one step" in answer.reason

    def test_scaling_up_is_not_limited_to_one_step(self) -> None:
        assert recommend(_queue(12, 0), _policy(), current_workers=1).desired_workers == 6


class TestRuleFiveTheClampSaysWhenItClipped:
    def test_the_ceiling_caps_and_says_so(self) -> None:
        answer = recommend(_queue(100, 0), _policy(max_workers=4))
        assert answer.desired_workers == 4
        assert answer.capped
        assert "capped" in answer.reason
        assert "50" in answer.reason

    def test_an_uncapped_answer_is_not_flagged(self) -> None:
        assert not recommend(_queue(3, 0), _policy()).capped

    def test_every_answer_names_its_policy_and_ends_as_a_sentence(self) -> None:
        answer = recommend(_queue(3, 1), _policy())
        assert answer.policy == POLICY_NAME
        assert answer.reason.endswith(".")


class TestTheInputsRefuseWhatCannotBe:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"min_workers": -1},
            {"max_workers": 0},
            {"min_workers": 5, "max_workers": 4},
            {"jobs_per_worker": 0},
            {"target_wait_s": 0.0},
            {"target_wait_s": float("inf")},
        ],
    )
    def test_an_impossible_policy_is_refused(self, overrides: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            _policy(**overrides)

    def test_an_empty_queue_with_an_oldest_job_is_refused(self) -> None:
        with pytest.raises(ValueError, match="no oldest job"):
            QueueSnapshot(queued=0, running=0, oldest_wait_s=3.0)

    def test_a_queue_with_jobs_and_no_oldest_wait_is_refused(self) -> None:
        with pytest.raises(ValueError, match="oldest job"):
            QueueSnapshot(queued=2, running=0, oldest_wait_s=None)

    def test_a_negative_current_size_is_refused(self) -> None:
        with pytest.raises(ValueError, match="current_workers"):
            recommend(_queue(0, 0), _policy(), current_workers=-1)

    def test_a_clock_that_drifted_backwards_waits_zero_seconds(self) -> None:
        now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
        assert wait_seconds(now + timedelta(seconds=5), now) == 0.0
        assert wait_seconds(now - timedelta(seconds=90), now) == pytest.approx(90.0)
        assert wait_seconds(None, now) is None


class TestTheRouteAnswersFromTheJobTable:
    def test_an_empty_queue_recommends_the_floor_and_echoes_the_policy(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        db_session.flush()

        body = auth_client.get(f"{API}/admin/compute/scaling").json()

        assert body["queued"] == 0
        assert body["running"] == 0
        assert body["oldest_wait_s"] is None
        assert body["desired_workers"] == body["min_workers"]
        assert body["current_workers"] is None
        assert body["policy"] == POLICY_NAME

    def test_the_current_size_is_read_from_the_query(
        self, auth_client: AuthenticatedTestClient, db_session: Session, current_user_id: str
    ) -> None:
        db_session.add(StaffGrant(user_id=current_user_id, role=StaffRole.SUPPORT))
        db_session.flush()

        body = auth_client.get(f"{API}/admin/compute/scaling?current=5").json()

        assert body["current_workers"] == 5
        assert body["desired_workers"] == 4

    def test_a_user_who_is_not_staff_is_refused(self, auth_client: AuthenticatedTestClient) -> None:
        response = auth_client.get(f"{API}/admin/compute/scaling")
        assert response.status_code in (403, 404)
