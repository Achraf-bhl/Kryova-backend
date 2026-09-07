"""A small JSON Schema validator, for the subset the tool specs use.

`jsonschema` is not a dependency and is not worth becoming one: the tool
parameter schemas are hand-written, deliberately boring, and use exactly the
keywords below. A hundred lines here buys the same guarantee without adding a
package to every deployment and to the daemon's Windows install.

This is the daemon's copy. It is byte-identical to `app/catia/validation.py` on
the server apart from this paragraph, and that duplication is deliberate: the
daemon re-validates every incoming call *without* importing anything from the
server, so a server that has been talked into sending a malformed call cannot
also be the thing that decides the call is well formed. Keep the two in step --
the tests assert they behave identically.

Supported: type (string/number/integer/boolean/object/array/null and lists of
those), properties, required, additionalProperties, enum, minimum, maximum,
exclusiveMinimum, minLength, maxLength, items, minItems, maxItems.
Anything else in a schema is ignored, so do not reach for a keyword this does
not implement and assume it is being enforced.

One keyword here is **not** JSON Schema: `nonZero` on an array, which refuses a
direction vector whose components are all zero. It was added on 2026-09-07 for
an unhappy reason -- `spec.direction3`'s description had been telling the model
"All three components zero is refused" since it was written, and nothing
anywhere refused it. That is the docstring-claims-a-capability failure CLAUDE.md
records having already happened twice, and this file is the right place to end
it: a zero direction reaches CATIA as an axis that points nowhere, and the
product's rule is that a bad argument comes back as a named refusal rather than
as a wrongly built part.
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


def _type_phrase(subschema: dict[str, Any]) -> str:
    """How a field's type reads in a refusal: `array of number`, `string`."""
    expected = subschema.get("type")
    if expected is None:
        return "any"
    names = expected if isinstance(expected, list) else [expected]
    phrase = " or ".join(str(name) for name in names)
    items = subschema.get("items")
    if phrase == "array" and isinstance(items, dict):
        item_type = items.get("type")
        if isinstance(item_type, str):
            phrase = f"array of {item_type}"
    if "enum" in subschema:
        phrase += " (one of: " + ", ".join(repr(o) for o in subschema["enum"]) + ")"
    return phrase


def describe_properties(schema: dict[str, Any]) -> str:
    """Every accepted field with its type and whether it is required.

    Naming the *keys* was already an improvement over naming nothing, and the
    comment below records why. It did not go far enough. Measured on the seat
    on 2026-09-06 with a 9B model, every attempt to build any geometry died in
    the same place, taking one round trip per fact:

        catia_sketch_create: unknown field(s): plane. Accepted: name, origin, support
        catia_sketch_create: arguments.support is required; you sent: name, origin
        catia_sketch_create: origin must be array, got str

    Each of those messages is correct and each carries exactly one fact, so the
    model fixed one thing and broke another, three turns running, and never
    built anything. The fields, their types and their required-ness are all
    known here at the moment of the first refusal; withholding two thirds of it
    is what turned one recoverable mistake into an unrecoverable loop.

    `(none)` is spelled out as a sentence for the same reason: `Accepted:
    (none)` was read by the model as "you may not call this", and it responded
    by trying a different tool, when what it needed was to call the same one
    with `{}`.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    if not properties:
        return "this tool takes no arguments at all -- call it with {}"
    required = set(schema.get("required", []))
    return ", ".join(
        f"{name} ({_type_phrase(subschema)}, "
        f"{'required' if name in required else 'optional'})"
        for name, subschema in sorted(properties.items())
    )


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
            raise SchemaError(
                f"{where} has unknown field(s): {', '.join(unknown)}. "
                f"Accepted: {describe_properties(schema)}"
            )

    for name in schema.get("required", []):
        if name not in value:
            supplied = ", ".join(sorted(value)) or "(nothing)"
            raise SchemaError(
                f"{where}.{name} is required; you sent: {supplied}. "
                f"Accepted: {describe_properties(schema)}"
            )

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
    if schema.get("nonZero") and _is_all_zero(value):
        raise SchemaError(
            f"{where} is a direction and every component is zero, which points nowhere. "
            "Give the axis you mean -- [0, 0, 1] for +Z, [1, 0, 0] for +X -- or a negative "
            "component to reverse it. The length is not used, only the direction."
        )


def _is_all_zero(value: list[Any]) -> bool:
    """Every component is a real number equal to zero.

    Empty is **not** all-zero: `minItems` owns "you sent nothing", and reporting
    an empty list as a direction pointing nowhere would answer a question the
    caller did not ask. A non-numeric entry is not this rule's business either
    -- `items` has already refused it, with a message naming the index.
    """
    return bool(value) and all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and n == 0 for n in value
    )
