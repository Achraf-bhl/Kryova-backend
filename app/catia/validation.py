"""A small JSON Schema validator, for the subset the tool specs use.

`jsonschema` is not a dependency and is not worth becoming one: the tool
parameter schemas are hand-written, deliberately boring, and use exactly the
keywords below. A hundred lines here buys the same guarantee without adding a
package to every deployment and to the daemon's Windows install.

The daemon carries its own copy of this file (`scripts/catia_bridge/validation.py`)
because it must be able to re-validate a call without importing the server. The
duplication is intentional and load-bearing -- see the security invariants in
docs/CATIA_BRIDGE_PROTOCOL.md.

Supported: type (string/number/integer/boolean/object/array/null and lists of
those), properties, required, additionalProperties, enum, minimum, maximum,
exclusiveMinimum, minLength, maxLength, items, minItems, maxItems.
Anything else in a schema is ignored, so do not reach for a keyword this does
not implement and assume it is being enforced.
"""

from typing import Any

_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    # `bool` is a subclass of `int` in Python, so a bare isinstance check would
    # accept `true` everywhere a number is wanted.
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    """The value does not satisfy the schema. The message names the field."""


def validate(value: Any, schema: dict[str, Any], path: str = "") -> None:
    """Raise `SchemaError` if `value` does not satisfy `schema`."""
    where = path or "arguments"

    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_TYPE_CHECKS[opt](value) for opt in options if opt in _TYPE_CHECKS):
            raise SchemaError(f"{where} must be {' or '.join(options)}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(option) for option in schema["enum"])
        raise SchemaError(f"{where} must be one of: {allowed}")

    if isinstance(value, str):
        _validate_string(value, schema, where)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        _validate_number(value, schema, where)
    elif isinstance(value, dict):
        _validate_object(value, schema, path)
    elif isinstance(value, list):
        _validate_array(value, schema, where)


def _validate_string(value: str, schema: dict[str, Any], where: str) -> None:
    minimum = schema.get("minLength")
    if minimum is not None and len(value) < minimum:
        raise SchemaError(f"{where} must be at least {minimum} character(s)")
    maximum = schema.get("maxLength")
    if maximum is not None and len(value) > maximum:
        raise SchemaError(f"{where} must be at most {maximum} characters")


def _validate_number(value: float, schema: dict[str, Any], where: str) -> None:
    minimum = schema.get("minimum")
    if minimum is not None and value < minimum:
        raise SchemaError(f"{where} must be at least {minimum}")
    exclusive = schema.get("exclusiveMinimum")
    if exclusive is not None and value <= exclusive:
        raise SchemaError(f"{where} must be greater than {exclusive}")
    maximum = schema.get("maximum")
    if maximum is not None and value > maximum:
        raise SchemaError(f"{where} must be at most {maximum}")


def _validate_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    where = path or "arguments"
    properties: dict[str, Any] = schema.get("properties", {})

    # Unknown fields are reported *before* missing required ones, and the order
    # is the whole point. A model that means the right call but guesses the key
    # -- `catia_new_part({"part_name": ...})` instead of `{"name": ...}` -- trips
    # both rules at once. Answering "arguments.name is required" tells it
    # nothing about the key it actually sent, so it guesses again: observed live
    # cycling through `project`, `project_name` and `part_name` and never
    # recovering. Answering "unknown field part_name, accepted: name" is the
    # same information the other branch already phrased well, and it ends the
    # loop in one turn.
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(value) - set(properties))
        if unknown:
            # Naming the accepted keys turns "rejected" into "here is the call
            # you meant", which is the difference between a model that recovers
            # on the next turn and one that repeats itself.
            allowed = ", ".join(sorted(properties)) or "(none)"
            raise SchemaError(
                f"{where} has unknown field(s): {', '.join(unknown)}. Accepted: {allowed}"
            )

    for name in schema.get("required", []):
        if name not in value:
            supplied = ", ".join(sorted(value)) or "(nothing)"
            raise SchemaError(f"{where}.{name} is required; you sent: {supplied}")

    for name, subschema in properties.items():
        if name in value:
            validate(value[name], subschema, f"{path}.{name}" if path else name)


def _validate_array(value: list[Any], schema: dict[str, Any], where: str) -> None:
    minimum = schema.get("minItems")
    if minimum is not None and len(value) < minimum:
        raise SchemaError(f"{where} must have at least {minimum} item(s)")
    maximum = schema.get("maxItems")
    if maximum is not None and len(value) > maximum:
        raise SchemaError(f"{where} must have at most {maximum} item(s)")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            validate(item, items, f"{where}[{index}]")
