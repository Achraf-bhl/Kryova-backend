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
