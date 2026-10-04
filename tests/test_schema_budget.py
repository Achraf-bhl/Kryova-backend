"""A model is never handed a schema it cannot be expected to answer reliably.

Measured on a small model, ladder prompt H4, 2026-09-06: `LoadCaseDraft` --
14,445 characters, fourteen definitions, two discriminated unions -- handed to a
provider as a decoding grammar cost 146 s, 146 s and 38 s and produced one
empty answer and two that did not match the schema. Three of the turn's
twenty rounds and five and a half minutes, for nothing.

The specific schema is replaced (`test_load_case_sketch.py`). This file is the
general rule, so the next one is caught before it costs a run:

* every schema the product hands a provider is under a measured budget and
  offers no choice between object shapes.

The bounded retry that used to be tested here -- a structured answer that comes
back empty or invalid is asked for once more with the problem fed back, and
never a third time -- is the hosted provider's now, and is tested in
`tests/test_openai_compatible_resilience.py`.

Offline: nothing here makes a request.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest
from pydantic import BaseModel

from app.ai import schemas
from app.ai.providers._json_schema import (
    SCHEMA_BUDGET_CHARS,
    object_unions,
    schema_characters,
    schema_problem,
)
from app.ai.schemas import (
    ImageReading,
    LoadCaseDraft,
    LoadCaseSketch,
    ResultInterpretation,
    VisualCheck,
)

#: The schemas the product actually sends. `LoadCaseDraft` is deliberately
#: absent: it is what comes *back* from drafting, built in Python.
SENT_TO_A_PROVIDER: tuple[type[BaseModel], ...] = (
    ResultInterpretation,
    VisualCheck,
    ImageReading,
    LoadCaseSketch,
)


class TestTheBudgetCoversEverySchemaSent:
    @pytest.mark.parametrize("schema", SENT_TO_A_PROVIDER, ids=lambda s: s.__name__)
    def test_it_is_within_budget(self, schema: type[BaseModel]) -> None:
        assert schema_problem(schema.model_json_schema()) is None

    def test_the_list_above_is_the_list_the_code_sends(self) -> None:
        """A schema added to `service.py` or `vision.py` and not to this
        list would be tested by nothing."""
        from app.ai import service, vision

        source = inspect.getsource(service) + inspect.getsource(vision)
        for name in ("ResultInterpretation", "VisualCheck", "ImageReading", "LoadCaseSketch"):
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
        assert SCHEMA_BUDGET_CHARS - largest < SCHEMA_BUDGET_CHARS / 4


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
        problem = schema_problem(LoadCaseDraft.model_json_schema(), name="LoadCaseDraft")
        assert problem is not None
        assert re.search(r"LoadCaseDraft is 1[0-9],[0-9]{3} characters", problem)
        assert "choice(s) between object shapes" in problem
        assert "LoadCaseSketch" in problem

    def test_the_problem_names_the_number_to_look_at(self) -> None:
        big = {"type": "object", "properties": {"x": {"description": "y" * 5_000}}}
        problem = schema_problem(big, name="Big")
        assert problem is not None
        assert f"over the {SCHEMA_BUDGET_CHARS:,}" in problem
