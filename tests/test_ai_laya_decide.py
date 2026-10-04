"""Laya as the tool-family router: the contract, offline.

No torch, no network, no real checkpoint: `laya_decider(agent=...)` takes the
seam this file injects a fake Laya `Agent` through, so what is under test is
the *contract* -- the question shape sent, the answer read back, and the fail-
open behaviour -- not the model. `MEASURED_LATENCY.md`-style claims about real
speed belong in a live check, not here; this module is a wiring proof.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.laya_decide import laya_decider, pick_device
from app.ai.tool_retrieval import INTENT_FAMILIES


class FakeLaya:
    """Answers whatever `laya.Agent.predict` would, from a canned choice.

    Records every call so a test can assert what was asked, the same role
    `Recorder` plays for `app/ai/decide.py`'s tests.
    """

    def __init__(self, answer: str = "none", raises: Exception | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def predict(self, *, state: str, questions: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"state": state, "questions": questions})
        if self._raises is not None:
            raise self._raises
        return {"answers": {"family": {"type": "choice", "choice": self._answer}}}


LABELS = tuple(list(INTENT_FAMILIES)[:3])


class TestTheQuestionItAsks:
    def test_every_label_and_none_are_offered_as_criteria(self) -> None:
        fake = FakeLaya(answer=LABELS[0])
        decide = laya_decider(agent=fake)

        decide("weigh 2.4 kg", LABELS)

        criteria = fake.calls[0]["questions"]["family"]["criteria"]
        assert set(criteria) == set(LABELS) | {"none"}

    def test_it_is_a_single_choice_question(self) -> None:
        fake = FakeLaya(answer=LABELS[0])
        decide = laya_decider(agent=fake)

        decide("weigh 2.4 kg", LABELS)

        assert set(fake.calls[0]["questions"]) == {"family"}
        assert fake.calls[0]["questions"]["family"]["type"] == "choice"

    def test_the_request_is_the_state(self) -> None:
        fake = FakeLaya(answer="none")
        decide = laya_decider(agent=fake)

        decide("make it weigh 2.4 kg", LABELS)

        assert fake.calls[0]["state"] == "make it weigh 2.4 kg"

    def test_one_call_per_decision(self) -> None:
        fake = FakeLaya(answer=LABELS[0])
        decide = laya_decider(agent=fake)

        decide("weigh 2.4 kg", LABELS)

        assert len(fake.calls) == 1


class TestTheAnswerIsReadBack:
    def test_a_named_family_is_returned(self) -> None:
        fake = FakeLaya(answer=LABELS[1])
        decide = laya_decider(agent=fake)

        assert decide("something", LABELS) == LABELS[1]

    def test_none_is_a_real_decline_not_an_absence(self) -> None:
        """The same doctrine `decider_for` states out loud: offering only the
        families would make "this needs no tool family at all" unsayable."""
        fake = FakeLaya(answer="none")
        decide = laya_decider(agent=fake)

        assert decide("what is 2+2", LABELS) is None

    def test_an_answer_outside_the_offered_labels_is_treated_as_no_decision(self) -> None:
        """Laya's schema is closed to what was offered, but a stale or
        mismatched checkpoint answering something else must not be trusted as
        one of *these* labels."""
        fake = FakeLaya(answer="a label that was never offered")
        decide = laya_decider(agent=fake)

        assert decide("something", LABELS) is None


class TestItFailsOpen:
    """A decider that cannot decide must never take a turn down with it."""

    def test_no_agent_available_declines_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # `agent=None` falls through to the lazy singleton, which on a machine
        # with a working GPU and Laya installed (this dev box) really does
        # load -- so "no agent available" has to be forced rather than assumed
        # true of the environment, or this test only means anything in CI.
        import app.ai.laya_decide as module

        monkeypatch.setattr(module, "_get_agent", lambda: None)
        decide = laya_decider(agent=None)

        assert decide("weigh 2.4 kg", LABELS) is None

    def test_a_raising_agent_declines_rather_than_raising(self) -> None:
        fake = FakeLaya(raises=RuntimeError("cuda out of memory"))
        decide = laya_decider(agent=fake)

        assert decide("weigh 2.4 kg", LABELS) is None

    def test_empty_request_or_labels_declines_without_calling_the_model(self) -> None:
        fake = FakeLaya(answer=LABELS[0])
        decide = laya_decider(agent=fake)

        assert decide("", LABELS) is None
        assert decide("weigh 2.4 kg", ()) is None
        assert fake.calls == []


class TestTheLazySingleton:
    """`_get_agent` is what a real deployment goes through; `agent=` in the
    tests above bypasses it on purpose. This class is the one place the
    singleton's own caching and fail-open behaviour are checked."""

    def test_a_load_failure_is_cached_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.ai.laya_decide as module

        monkeypatch.setattr(module, "_agent", None, raising=False)
        monkeypatch.setattr(module, "_agent_failed", False, raising=False)
        attempts = []

        def _explode() -> Any:
            attempts.append(1)
            raise RuntimeError("no torch")

        monkeypatch.setattr(module, "_load_agent", _explode)

        assert module._get_agent() is None
        assert module._get_agent() is None
        assert len(attempts) == 1


class TestWhereItRuns:
    """A 27B conversational model already spills a 16 GB card, so Laya has to be
    able to stay off it. Pure function of two inputs, so no torch is needed."""

    @pytest.mark.parametrize(
        ("requested", "cuda", "expected"),
        [
            ("auto", True, "cuda"),
            ("auto", False, "cpu"),
            ("cpu", True, "cpu"),
            ("cpu", False, "cpu"),
            ("cuda", True, "cuda"),
            ("cuda", False, "cpu"),
            (" CPU ", True, "cpu"),
            ("", True, "cuda"),
        ],
    )
    def test_the_choice_and_its_fallback(self, requested: str, cuda: bool, expected: str) -> None:
        assert pick_device(requested, cuda) == expected

    def test_a_typo_is_refused_loudly(self) -> None:
        with pytest.raises(ValueError, match="AI_INTENT_ROUTER_DEVICE"):
            pick_device("gpu", True)
