"""Tokens at their true price: the cache split, the price table and the cost budget.

ROAD_TO_10 1.1, 1.2 and 1.4. The claims, each of which a wrong implementation
satisfies a weaker test for:

* a cached prompt token is a *subset* of the prompt, billed at the cached rate,
  and is read from both spellings a vendor uses;
* a price lives in configuration, an absent price is **unknown** (never free), and
  two calls with the same token count and a different cache share cost different
  amounts;
* the daily budget trips on cost, and says so in words that name the cause;
* a call that fails after a billed attempt still reports what it spent.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai import decide, pricing
from app.ai import usage as token_usage
from app.ai.provider import LLMError, LLMRefusal, TokenUsage
from app.ai.providers import anthropic as anthropic_module
from app.ai.providers import openai_compatible as module
from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.core.config import ModelPrice, Settings, settings
from app.core.security import hash_password
from app.models import AITokenUsage, User

PRICE = ModelPrice(input=Decimal("0.50"), cached_input=Decimal("0.05"), output=Decimal("2.00"))


@pytest.fixture
def priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prices for the model the tests spend on, and nothing else."""
    monkeypatch.setattr(settings, "ai_prices", {"m": PRICE})


@pytest.fixture
def user(db_session: Session) -> User:
    account = User(email="price@kryova.dev", hashed_password=hash_password("a-long-enough-password"))
    db_session.add(account)
    db_session.flush()
    return account


class TestTheCachedShareIsASubsetOfThePrompt:
    def test_adding_usages_sums_the_cached_share_too(self) -> None:
        total = TokenUsage(100, 10, 40) + TokenUsage(50, 5, 10)
        assert (total.prompt_tokens, total.completion_tokens, total.cached_prompt_tokens) == (
            150,
            15,
            50,
        )

    def test_fresh_is_the_prompt_minus_the_cache_reads(self) -> None:
        assert TokenUsage(100, 0, 40).fresh_prompt_tokens == 60

    def test_a_vendor_reporting_more_hits_than_prompt_tokens_is_clamped(self) -> None:
        """Otherwise `fresh` goes negative and a call is priced below zero."""
        clamped = TokenUsage(prompt_tokens=100, cached_prompt_tokens=500)
        assert clamped.cached_prompt_tokens == 100
        assert clamped.fresh_prompt_tokens == 0

    def test_a_negative_hit_count_is_clamped_to_none(self) -> None:
        assert TokenUsage(prompt_tokens=100, cached_prompt_tokens=-5).cached_prompt_tokens == 0

    def test_the_old_two_argument_form_still_means_nothing_was_cached(self) -> None:
        assert TokenUsage(500, 100).cached_prompt_tokens == 0


class TestEachVendorsSpellingOfACacheHit:
    def test_deepseek_reports_hits_at_the_top_of_the_usage_block(self) -> None:
        body = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 900,
                "prompt_cache_miss_tokens": 100,
            }
        }
        assert module._usage(body) == TokenUsage(1000, 50, 900)

    def test_openai_nests_it_under_prompt_tokens_details(self) -> None:
        body = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 640},
            }
        }
        assert module._usage(body).cached_prompt_tokens == 640

    def test_a_server_that_reports_neither_is_read_as_no_hits(self) -> None:
        assert module._usage({"usage": {"prompt_tokens": 7, "completion_tokens": 1}}) == TokenUsage(
            7, 1, 0
        )

    def test_a_server_with_no_usage_block_is_all_zeros(self) -> None:
        assert module._usage({}) == TokenUsage()

    def test_a_boolean_is_not_a_token_count(self) -> None:
        """`True` is an `int` in Python; a vendor's flag must not become one token."""
        body = {"usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": True}}
        assert module._usage(body).cached_prompt_tokens == 0

    def test_anthropic_cache_reads_are_split_out_and_writes_stay_fresh(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                cache_read_input_tokens=800,
                cache_creation_input_tokens=50,
                output_tokens=20,
            )
        )
        got = anthropic_module._usage(response)
        assert got.prompt_tokens == 950
        assert got.cached_prompt_tokens == 800
        assert got.fresh_prompt_tokens == 150


class TestAPriceIsConfigurationAndAbsenceIsUnknown:
    def test_two_calls_of_the_same_size_cost_different_amounts_by_their_cache(
        self, priced: None
    ) -> None:
        cold = pricing.cost_micro_usd(TokenUsage(10_000, 1_000, 0), "m")
        warm = pricing.cost_micro_usd(TokenUsage(10_000, 1_000, 9_000), "m")
        assert cold is not None and warm is not None
        assert warm < cold
        # 0.50 USD/M x 10,000 + 2.00 USD/M x 1,000 = 7,000 micro-dollars cold;
        # 0.50 x 1,000 + 0.05 x 9,000 + 2.00 x 1,000 = 2,950 warm.
        assert (cold, warm) == (7_000, 2_950)

    def test_a_model_with_no_price_is_unknown_not_free(self) -> None:
        assert pricing.cost_micro_usd(TokenUsage(1_000_000, 1_000_000), "nobody-priced-this") is None

    def test_a_priced_call_that_used_nothing_is_a_known_zero(self, priced: None) -> None:
        assert pricing.cost_micro_usd(TokenUsage(), "m") == 0

    def test_the_cached_price_defaults_to_the_full_input_price(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An entry the operator did not finish over-estimates; it never under-estimates."""
        monkeypatch.setattr(
            settings, "ai_prices", {"m": ModelPrice(input=Decimal("1"), output=Decimal("0"))}
        )
        assert pricing.cost_micro_usd(TokenUsage(1_000, 0, 1_000), "m") == 1_000

    def test_a_model_name_is_matched_regardless_of_the_vendors_casing(self, priced: None) -> None:
        assert pricing.price_for("M") == PRICE

    def test_the_cost_rounds_half_up_to_a_whole_micro_dollar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            settings, "ai_prices", {"m": ModelPrice(input=Decimal("0.5"), output=Decimal("0"))}
        )
        assert pricing.cost_micro_usd(TokenUsage(3, 0), "m") == 2  # 1.5 -> 2

    def test_the_price_table_is_read_from_a_json_environment_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "AI_PRICES",
            '{"deepseek-flash": {"input": 0.14, "cached_input": 0.014, "output": 0.28}}',
        )
        loaded = Settings(_env_file=None)  # type: ignore[call-arg]
        assert loaded.ai_prices["deepseek-flash"].cached_input == Decimal("0.014")

    def test_a_negative_price_is_refused_at_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_PRICES", '{"m": {"input": -1, "output": 0}}')
        with pytest.raises(ValueError):
            Settings(_env_file=None)  # type: ignore[call-arg]


class TestProductionRefusesACostBudgetThatCannotTrip:
    def _production(self, monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("SECRET_KEY", "x" * 48)
        monkeypatch.setenv("COOKIE_SECURE", "true")
        monkeypatch.setenv("CORS_ORIGINS", '["https://app.example.com"]')
        monkeypatch.setenv("MAIL_TRANSPORT", "smtp")
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("AI_MODEL", "deepseek-flash")
        for key, value in extra.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)  # type: ignore[call-arg]

    def test_a_budget_with_no_price_for_the_model_does_not_boot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValueError, match="AI_PRICES has no entry"):
            self._production(monkeypatch, AI_DAILY_COST_BUDGET_USD="5")

    def test_the_same_budget_with_a_price_boots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loaded = self._production(
            monkeypatch,
            AI_DAILY_COST_BUDGET_USD="5",
            AI_PRICES='{"deepseek-flash": {"input": 0.1, "output": 0.2}}',
        )
        assert loaded.ai_daily_cost_budget_usd == Decimal(5)

    def test_no_budget_needs_no_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._production(monkeypatch).ai_prices == {}

    def test_development_is_told_at_startup_instead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The same rule serves both: production refuses, a dev machine is told once."""
        monkeypatch.setattr(settings, "ai_daily_cost_budget_usd", Decimal(5))
        monkeypatch.setattr(settings, "ai_prices", {})
        assert any("will never trip" in line for line in settings.insecure_defaults())
        monkeypatch.setattr(settings, "ai_prices", {settings.ai_model.upper(): PRICE})
        assert not any("will never trip" in line for line in settings.insecure_defaults())


class TestTheLedgerRecordsTheSplitAndThePrice:
    def _record(self, db: Session, user: User, usage: TokenUsage, model: str = "m") -> None:
        token_usage.record(
            db,
            user=user,
            usage=usage,
            purpose=token_usage.PURPOSE_CHAT,
            provider="p",
            model=model,
        )

    def test_a_row_carries_the_cache_share_and_the_cost(
        self, db_session: Session, user: User, priced: None
    ) -> None:
        self._record(db_session, user, TokenUsage(10_000, 1_000, 9_000))
        row = db_session.query(AITokenUsage).filter_by(user_id=user.id).one()
        assert row.prompt_tokens == 10_000
        assert row.cached_prompt_tokens == 9_000
        assert row.cost_micro_usd == 2_950

    def test_an_unpriced_model_is_stored_as_unknown_not_zero(
        self, db_session: Session, user: User
    ) -> None:
        self._record(db_session, user, TokenUsage(10, 1), model="unpriced")
        row = db_session.query(AITokenUsage).filter_by(user_id=user.id).one()
        assert row.cost_micro_usd is None

    def test_a_price_change_does_not_rewrite_what_an_earlier_call_cost(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ai_prices", {"m": PRICE})
        self._record(db_session, user, TokenUsage(1_000_000, 0))
        monkeypatch.setattr(
            settings, "ai_prices", {"m": ModelPrice(input=Decimal(99), output=Decimal(99))}
        )
        row = db_session.query(AITokenUsage).filter_by(user_id=user.id).one()
        assert row.cost_micro_usd == 500_000

    def test_the_lifetime_totals_include_the_cache_share(
        self, db_session: Session, user: User, priced: None
    ) -> None:
        self._record(db_session, user, TokenUsage(100, 10, 60))
        self._record(db_session, user, TokenUsage(50, 5, 20))
        assert token_usage.user_totals(db_session, user.id) == TokenUsage(150, 15, 80)


class TestTheDailyBudgetTripsOnCost:
    def _spend(self, db: Session, user: User, usage: TokenUsage, model: str = "m") -> None:
        token_usage.record(
            db,
            user=user,
            usage=usage,
            purpose=token_usage.PURPOSE_CHAT,
            provider="p",
            model=model,
        )

    @pytest.fixture(autouse=True)
    def _only_the_cost_budget(self, monkeypatch: pytest.MonkeyPatch, priced: None) -> None:
        monkeypatch.setattr(settings, "ai_daily_token_budget", 0)
        monkeypatch.setattr(settings, "ai_daily_cost_budget_usd", Decimal("0.005"))  # 5,000 micro

    def test_the_cheap_cached_call_stays_under_and_the_cold_one_of_equal_size_trips_it(
        self, db_session: Session, user: User
    ) -> None:
        self._spend(db_session, user, TokenUsage(10_000, 1_000, 9_000))  # 2,950 micro
        assert not token_usage.over_budget(db_session, user.id)
        self._spend(db_session, user, TokenUsage(10_000, 1_000, 0))  # +7,000 = 9,950
        assert token_usage.exceeded(db_session, user.id) == "cost"

    def test_the_refusal_names_dollars_and_when_it_clears(
        self, db_session: Session, user: User
    ) -> None:
        self._spend(db_session, user, TokenUsage(10_000, 1_000, 0))
        message = token_usage.budget_message(db_session, user.id)
        assert "$0.01" in message and "00:00 UTC" in message and "tokens" not in message

    def test_calls_nobody_priced_are_counted_and_named_not_summed_as_free(
        self, db_session: Session, user: User
    ) -> None:
        self._spend(db_session, user, TokenUsage(10_000, 1_000, 0))
        self._spend(db_session, user, TokenUsage(10, 1), model="unpriced")
        used = token_usage.usage_today(db_session, user.id)
        assert used.unpriced_calls == 1
        assert used.cost_micro_usd == 7_000
        assert "1 call(s) on a model with no configured price" in token_usage.budget_message(
            db_session, user.id
        )

    def test_a_zero_budget_is_unlimited(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ai_daily_cost_budget_usd", Decimal(0))
        self._spend(db_session, user, TokenUsage(10_000_000, 1_000_000, 0))
        assert token_usage.exceeded(db_session, user.id) is None

    def test_the_token_budget_still_trips_on_its_own(
        self, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "ai_daily_cost_budget_usd", Decimal(0))
        monkeypatch.setattr(settings, "ai_daily_token_budget", 1_000)
        self._spend(db_session, user, TokenUsage(900, 200, 0), model="unpriced")
        assert token_usage.exceeded(db_session, user.id) == "tokens"
        assert "1,000 tokens" in token_usage.budget_message(db_session, user.id)


# -- 1.4: a failure after a billed attempt still reports what it spent -------------


class Shape(BaseModel):
    force_n: float


class _Script:
    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)

    def __call__(self, url: str, *, json: dict[str, Any], headers: Any, timeout: Any) -> Any:
        step = self.steps.pop(0)
        request = httpx.Request("POST", url)
        if isinstance(step, int):
            return httpx.Response(step, text=f"status {step}", request=request)
        return httpx.Response(200, json=step, request=request)


def _answer(content: str, usage: tuple[int, int, int] = (100, 10, 0), finish: str = "stop") -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage[0],
            "completion_tokens": usage[1],
            "prompt_cache_hit_tokens": usage[2],
        },
    }


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider("http://x/v1", "k", "m", 5.0)


@pytest.fixture(autouse=False)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.time, "sleep", lambda _s: None)


class TestAFailedCallStillReportsWhatItSpent:
    def test_an_answer_that_stays_unusable_after_the_repair_reports_both_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx, "post", _Script(_answer("not json", (100, 10, 60)), _answer("still not", (120, 12, 90)))
        )
        with pytest.raises(LLMError) as raised:
            _provider().complete(system="s", user="u", schema=Shape, effort="low", max_tokens=50)
        assert raised.value.usage == TokenUsage(220, 22, 150)

    def test_a_transport_failure_on_the_repair_carries_the_first_attempts_spend(
        self, monkeypatch: pytest.MonkeyPatch, no_sleep: None
    ) -> None:
        monkeypatch.setattr(httpx, "post", _Script(_answer("not json", (100, 10, 0)), 503, 503, 503, 503))
        with pytest.raises(LLMError) as raised:
            _provider().complete(system="s", user="u", schema=Shape, effort="low", max_tokens=50)
        assert raised.value.usage.prompt_tokens == 100

    def test_a_refusal_after_a_billed_attempt_keeps_the_type_and_the_spend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            httpx,
            "post",
            _Script(_answer("not json", (100, 10, 0)), _answer("", (100, 10, 0), "content_filter")),
        )
        with pytest.raises(LLMRefusal) as raised:
            _provider().complete(system="s", user="u", schema=Shape, effort="low", max_tokens=50)
        assert raised.value.usage.prompt_tokens == 200

    def test_a_request_that_never_reached_a_model_spent_nothing(
        self, monkeypatch: pytest.MonkeyPatch, no_sleep: None
    ) -> None:
        monkeypatch.setattr(httpx, "post", _Script(503, 503, 503, 503))
        with pytest.raises(LLMError) as raised:
            _provider().complete(system="s", user="u", schema=Shape, effort="low", max_tokens=50)
        assert raised.value.usage == TokenUsage()

    def test_a_decision_that_fell_back_after_a_paid_attempt_still_reports_it(self) -> None:
        class Flaky:
            name = "flaky"
            model = "m"

            def complete(self, **_: Any) -> Any:
                raise LLMError("gave up", usage=TokenUsage(300, 30, 100))

        decision = decide.judge(Flaky(), "Is it a bracket?", fallback=False)  # type: ignore[arg-type]
        assert decision.taken_by_fallback
        assert decision.usage == TokenUsage(300, 30, 100)
