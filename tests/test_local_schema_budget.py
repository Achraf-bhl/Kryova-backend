"""A local model is never handed a schema it cannot decode against.

Measured on qwen3.5:9b, ladder prompt H4, 2026-09-06: `LoadCaseDraft` --
14,445 characters, fourteen definitions, two discriminated unions -- handed to
Ollama as a decoding grammar cost 146 s, 146 s and 38 s and produced one
empty answer and two that did not match the schema. Three of the turn's
twenty rounds and five and a half minutes, for nothing.

The specific schema is replaced (`test_load_case_sketch.py`). This file is the
general rule, so the next one is caught before it costs a seat session:

* every schema the product hands a provider is under a measured budget and
  offers no choice between object shapes;
* the Ollama provider refuses one that is over, in no time, with the number;
* a structured answer that comes back empty or invalid is asked for once more
  with the problem fed back -- Phase 16.4's diagnose, repair, bounded retry --
  and never a third time;
* the answer's token budget is the effort level's, not the product ceiling, so
  a model lost in a grammar stops in seconds rather than minutes.

Offline: Ollama is a recorder.
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai import schemas
from app.ai.provider import LLMError
from app.ai.providers._json_schema import (
    LOCAL_SCHEMA_BUDGET_CHARS,
    local_decoding_problem,
    object_unions,
    schema_characters,
)
from app.ai.providers.ollama import (
    _EFFORT_PREDICT,
    STRUCTURED_ATTEMPTS,
    OllamaProvider,
    _answer_budget,
)
from app.ai.schemas import LoadCaseDraft, LoadCaseSketch, ResultInterpretation, VisualCheck

#: The schemas the product actually sends. `LoadCaseDraft` is deliberately
#: absent: it is what comes *back* from drafting, built in Python.
SENT_TO_A_PROVIDER: tuple[type[BaseModel], ...] = (
    ResultInterpretation,
    VisualCheck,
    LoadCaseSketch,
)


class _Answer(BaseModel):
    value: int


class _Ollama:
    """Stands in for Ollama, answering from a script and remembering payloads."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.payloads: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        request = httpx.Request("POST", url)
        if url.endswith("/api/show"):
            return httpx.Response(200, json={"model_info": {}}, request=request)
        self.payloads.append(json)
        content = self.contents.pop(0) if self.contents else ""
        return httpx.Response(
            200,
            request=request,
            json={
                "message": {"content": content},
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )


@pytest.fixture
def provider() -> OllamaProvider:
    return OllamaProvider("http://localhost:11434", "qwen3.5:9b", 5.0)


def _complete(provider: OllamaProvider, schema: type = _Answer) -> Any:
    return provider.complete(
        system="s", user="u", schema=schema, effort="low", max_tokens=8_000
    )


class TestTheBudgetCoversEverySchemaSent:
    @pytest.mark.parametrize("schema", SENT_TO_A_PROVIDER, ids=lambda s: s.__name__)
    def test_it_is_within_budget(self, schema: type[BaseModel]) -> None:
        assert local_decoding_problem(schema.model_json_schema()) is None

    def test_the_list_above_is_the_list_the_code_sends(self) -> None:
        """A schema added to `service.py` or `vision.py` and not to this
        list would be tested by nothing."""
        from app.ai import service, vision

        source = inspect.getsource(service) + inspect.getsource(vision)
        for name in ("ResultInterpretation", "VisualCheck", "LoadCaseSketch"):
            assert f"schema={name}" in source
        assert "schema=LoadCaseDraft" not in source

    def test_every_model_in_schemas_is_either_sent_or_the_draft(self) -> None:
        """`schemas.py` is where provider schemas live. Anything defined
        there that is not in the sent list and not the draft is a new schema
        nobody has budgeted."""
        top_level = {
            obj
            for _, obj in inspect.getmembers(schemas, inspect.isclass)
            if issubclass(obj, BaseModel) and obj.__module__ == schemas.__name__
        }
        components = {
            schemas.Finding,
            schemas.DesignSuggestion,
            schemas.Discrepancy,
            schemas.Support,
            schemas.AppliedLoad,
        }
        unaccounted = top_level - set(SENT_TO_A_PROVIDER) - components - {LoadCaseDraft}
        assert not unaccounted, sorted(one.__name__ for one in unaccounted)

    def test_the_budget_is_just_above_the_largest_schema_sent(self) -> None:
        """Headroom is what lets a schema grow unnoticed. Under a quarter of
        the budget, so the next field added is a decision."""
        largest = max(schema_characters(s.model_json_schema()) for s in SENT_TO_A_PROVIDER)
        assert LOCAL_SCHEMA_BUDGET_CHARS - largest < LOCAL_SCHEMA_BUDGET_CHARS / 4


class TestMeasuringASchema:
    def test_characters_are_counted_compactly(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
        assert schema_characters(schema) == len(json.dumps(schema, separators=(",", ":")))

    def test_a_union_of_objects_is_counted(self) -> None:
        schema = {"anyOf": [{"type": "object"}, {"$ref": "#/$defs/Other"}]}
        assert object_unions(schema) == 1

    def test_a_nullable_value_is_not_a_union(self) -> None:
        schema = {"anyOf": [{"type": "number"}, {"type": "null"}]}
        assert object_unions(schema) == 0

    def test_a_discriminator_is_a_union(self) -> None:
        assert object_unions({"discriminator": {"propertyName": "type"}}) == 1

    def test_the_solvers_load_case_is_over_on_both_counts(self) -> None:
        problem = local_decoding_problem(LoadCaseDraft.model_json_schema(), name="LoadCaseDraft")
        assert problem is not None
        assert re.search(r"LoadCaseDraft is 1[0-9],[0-9]{3} characters", problem)
        assert "choice(s) between object shapes" in problem
        assert "LoadCaseSketch" in problem

    def test_the_problem_names_the_number_to_look_at(self) -> None:
        big = {"type": "object", "properties": {"x": {"description": "y" * 5_000}}}
        problem = local_decoding_problem(big, name="Big")
        assert problem is not None
        assert f"over the {LOCAL_SCHEMA_BUDGET_CHARS:,}" in problem


class TestOllamaRefusesAnUndecodableSchema:
    def test_before_any_request_is_made(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verified by breaking it: hand it the schema that cost 146 s."""
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        with pytest.raises(LLMError, match=r"LoadCaseDraft is 1[0-9],[0-9]{3} characters"):
            _complete(provider, LoadCaseDraft)
        assert fake.payloads == []

    def test_the_vision_path_has_the_same_guard(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        monkeypatch.setattr(provider, "_sees", lambda: True)
        with pytest.raises(LLMError, match=r"LoadCaseDraft is 1[0-9],[0-9]{3} characters"):
            provider.look(
                system="s",
                user="u",
                images=[b"png"],
                schema=LoadCaseDraft,
                effort="low",
                max_tokens=100,
            )
        assert fake.payloads == []

    def test_a_small_schema_goes_straight_through(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        assert _complete(provider).value.value == 1
        assert len(fake.payloads) == 1


class TestTheBoundedRetry:
    def test_there_is_exactly_one_more_attempt(self) -> None:
        assert STRUCTURED_ATTEMPTS == 2

    def test_an_empty_answer_is_asked_for_again_with_the_problem(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(["", '{"value": 2}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        assert _complete(provider).value.value == 2
        assert len(fake.payloads) == 2
        repair = fake.payloads[1]["messages"][-1]
        assert repair["role"] == "user"
        assert "empty" in repair["content"]
        assert "only the JSON object" in repair["content"]

    def test_an_invalid_answer_is_asked_for_again_with_the_error(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": "not a number"}', '{"value": 3}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        assert _complete(provider).value.value == 3
        repair = fake.payloads[1]["messages"][-1]["content"]
        assert "does not match the expected schema" in repair

    def test_the_first_attempt_carries_no_repair_message(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        _complete(provider)
        assert [m["role"] for m in fake.payloads[0]["messages"]] == ["system", "user"]

    def test_two_failures_are_the_end(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verified by breaking it: a third answer is scripted and must never
        be asked for."""
        fake = _Ollama(["", "", '{"value": 9}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        with pytest.raises(LLMError, match="empty"):
            _complete(provider)
        assert len(fake.payloads) == 2

    def test_usage_is_the_sum_over_both_attempts(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failed attempt cost real tokens; the meter must see them."""
        fake = _Ollama(["", '{"value": 2}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        completion = _complete(provider)
        assert completion.usage.prompt_tokens == 20
        assert completion.usage.completion_tokens == 10


class TestTheAnswerBudget:
    def test_low_effort_is_the_low_budget_not_the_ceiling(self) -> None:
        assert _answer_budget("low", 8_000) == _EFFORT_PREDICT["low"]

    def test_the_callers_ceiling_still_caps_it(self) -> None:
        assert _answer_budget("max", 500) == 500

    def test_an_unknown_effort_falls_back_to_the_ceiling(self) -> None:
        assert _answer_budget("whatever", 700) == 700

    def test_it_is_what_is_sent(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        _complete(provider)
        assert fake.payloads[0]["options"]["num_predict"] == _EFFORT_PREDICT["low"]


class TestThinkingIsOffForStructuredOutput:
    """Reasoning and the answer share one token budget, and reasoning goes first.

    Measured on the seat, 2026-09-06, ladder prompt H4 run 11 -- the same
    request, schema and model, three ways:

        thinking on,  num_predict 1024 -> 20.7 s, 1024 tokens of thinking,
                                          content '', done_reason 'length'
        thinking on,  num_predict 4096 -> 82.0 s, 4220 tokens, valid answer
        think: false, num_predict 1024 ->  8.9 s,  280 tokens, valid answer

    Nine times faster and correct. `qwen3.5:9b` keeps its reasoning in
    `message.thinking`, which the JSON grammar does not constrain, so the model
    reasons until the budget runs out and never starts the JSON. That is the
    whole history of the drafting defect: 146 s against a 13,510-character
    grammar, then 42 s of "empty response" after the schema was flattened.
    """

    def test_the_flag_is_sent(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        _complete(provider)
        assert fake.payloads[0]["think"] is False

    def test_the_vision_call_sends_it_too(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _Ollama(['{"value": 1}'])
        monkeypatch.setattr(httpx, "post", fake.post)
        monkeypatch.setattr(provider, "_sees", lambda: True)
        provider.look(
            system="s", user="u", images=[b"png"], schema=_Answer, effort="low", max_tokens=100
        )
        assert fake.payloads[-1]["think"] is False

    def test_chat_does_not_send_it(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Choosing a tool is exactly the decision reasoning helps with, and
        there is no grammar competing with it there."""
        fake = _Ollama(["ok"])
        monkeypatch.setattr(httpx, "post", fake.post)
        provider.chat(
            system="s", messages=[{"role": "user", "content": "hi"}], tools=[], max_tokens=100
        )
        assert "think" not in fake.payloads[-1]


class TestAnEmptyAnswerIsDiagnosed:
    """"Ollama returned an empty response" was reported for three different
    conditions, and described the common one worst."""

    def test_a_thinking_model_cut_off_says_so(self) -> None:
        from app.ai.providers.ollama import _no_content_reason

        problem = _no_content_reason(
            {"done_reason": "length", "message": {"content": "", "thinking": "a b c d"}}
        )
        assert "still reasoning" in problem
        assert "num_predict" in problem

    def test_a_plain_truncation_says_truncation(self) -> None:
        from app.ai.providers.ollama import _no_content_reason

        problem = _no_content_reason({"done_reason": "length", "message": {"content": ""}})
        assert "token limit" in problem
        assert "reasoning" not in problem

    def test_reasoning_that_simply_stopped_is_a_retry(self) -> None:
        from app.ai.providers.ollama import _no_content_reason

        problem = _no_content_reason({"done_reason": "stop", "message": {"thinking": "hmm"}})
        assert "Retrying" in problem

    def test_a_genuinely_empty_answer_still_says_that(self) -> None:
        from app.ai.providers.ollama import _no_content_reason

        assert _no_content_reason({"done_reason": "stop", "message": {}}) == (
            "Ollama returned an empty response."
        )

    def test_it_is_what_reaches_the_caller(
        self, provider: OllamaProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A helper nothing calls diagnoses nothing."""

        class _Truncated(_Ollama):
            def post(self, url, *, json, timeout):
                response = super().post(url, json=json, timeout=timeout)
                if url.endswith("/api/chat"):
                    body = response.json()
                    body["done_reason"] = "length"
                    body["message"] = {"content": "", "thinking": "one two three"}
                    return httpx.Response(200, json=body, request=response.request)
                return response

        fake = _Truncated(["", ""])
        monkeypatch.setattr(httpx, "post", fake.post)
        with pytest.raises(LLMError, match="still reasoning"):
            _complete(provider)
