"""Prepare a Pydantic schema for strict JSON-Schema-constrained decoding.

Hosted providers that enforce a schema (Anthropic's `json_schema` output format,
OpenAI's strict mode) require every object to carry `additionalProperties: false`
and to list all of its properties as required. Pydantic emits neither, so the
raw `model_json_schema()` is rejected. Ollama is more forgiving and takes the
schema unmodified, which is why this lives beside the strict providers rather
than in the shared seam.
"""

from typing import Any


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively close every object in `schema` and require all its keys.

    Mutates a deep copy, not the input -- `model_json_schema()` caches, so
    editing in place would corrupt the schema for every later call.
    """
    return _close(_deep_copy(schema))


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def _close(node: Any) -> Any:
    if isinstance(node, list):
        return [_close(item) for item in node]
    if not isinstance(node, dict):
        return node

    for key, value in node.items():
        node[key] = _close(value)

    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        # Strict mode has no notion of an optional key: a field with a default
        # must still be present, so every property is listed as required.
        node["required"] = list(node["properties"].keys())
    return node


# ---------------------------------------------------------------------------
# How much a schema costs a local model to decode against.
# ---------------------------------------------------------------------------

#: The most JSON Schema a schema-constrained call may hand a local model.
#: Measured on qwen3.5:9b, 2026-09-06, ladder prompt H4: the 14,445-character
#: `LoadCaseDraft` schema (fourteen definitions, two discriminated unions)
#: cost 146 s, 146 s and 38 s for one empty answer and two that did not match
#: it, while the 3.4k `LoadCaseSketch` that replaced it answers in seconds.
#: Grammar-constrained decoding is not free: the sampler walks the whole
#: grammar at every token, and a union of objects is a fork it can wander down
#: for thousands of tokens. The three schemas the product ships are all under
#: 3.5k; the budget sits just above them so the next field added to one is a
#: decision and not a drift.
LOCAL_SCHEMA_BUDGET_CHARS = 4_096

#: Keywords that make a schema a fork rather than a form. `anyOf` of a value
#: with `null` is harmless and is not counted; a union of objects is.
_UNION_KEYWORDS = ("anyOf", "oneOf")


def schema_characters(schema: dict[str, Any]) -> int:
    """The size of `schema` as Ollama would receive it."""
    import json

    return len(json.dumps(schema, separators=(",", ":")))


def object_unions(node: Any) -> int:
    """How many places in `schema` offer a choice between object shapes.

    A `discriminator` is counted as a union too: pydantic emits `oneOf` for a
    tagged union, and the tag is the thing a small model gets wrong.
    """
    count = 0
    if isinstance(node, list):
        return sum(object_unions(item) for item in node)
    if not isinstance(node, dict):
        return 0
    for key, value in node.items():
        if key in _UNION_KEYWORDS and isinstance(value, list):
            shapes = [
                one
                for one in value
                if isinstance(one, dict) and (one.get("type") == "object" or "$ref" in one)
            ]
            if len(shapes) > 1:
                count += 1
        if key == "discriminator":
            count += 1
        count += object_unions(value)
    return count


def local_decoding_problem(schema: dict[str, Any], *, name: str = "The schema") -> str | None:
    """Why a local model should not be asked to decode against `schema`, or None.

    A sentence, not a boolean, because the caller raises it: the person reading
    the error is the one who added the field, and they need to know which
    number to look at.
    """
    characters = schema_characters(schema)
    unions = object_unions(schema)
    problems: list[str] = []
    if characters > LOCAL_SCHEMA_BUDGET_CHARS:
        problems.append(
            f"is {characters:,} characters of JSON Schema, over the "
            f"{LOCAL_SCHEMA_BUDGET_CHARS:,} a local model decodes against reliably"
        )
    if unions:
        problems.append(
            f"offers {unions} choice(s) between object shapes, which a small model "
            "wanders through for minutes and then gets wrong"
        )
    if not problems:
        return None
    return (
        f"{name} " + " and ".join(problems) + ". Flatten it into words and numbers "
        "and build the real object in Python, the way LoadCaseSketch does."
    )
