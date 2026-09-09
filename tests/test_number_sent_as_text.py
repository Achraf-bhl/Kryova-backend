"""A dimension sent as the text of a number is that number.

The scalar sibling of `test_array_sent_as_text.py`, and it is here for the same
reason on the same kind of evidence. Measured at ladder Level 2 on 2026-09-09,
three runs of one prompt:

    catia_plane_offset(name="Plane_top", reference="XY", distance_mm="10")
      -> catia_plane_offset: distance_mm must be number, got str
    catia_plane_offset(name="Plane_top", reference="XY", distance_mm="10")
      -> catia_plane_offset: distance_mm must be number, got str

The agent then stopped and told the user that "the distance_mm argument keeps
being reported as a string when it should be a number, despite me passing a
numeric value ... a tool-system quirk that won't resolve by retrying", and asked
how to proceed. The plate was built; the boss it needed the plane for never was.

The refusal is accurate and the model cannot act on it -- it believes it sent a
number, because what it wrote *is* the number. That is exactly the case the
array repair records.

Nothing is guessed. The string must parse as JSON and must yield a real number.
A unit-carrying string is deliberately not repaired: this codebase is mm-N-MPa
throughout and converts nothing, so "10 mm" and "10 in" differ in a way no
repair here is entitled to resolve, and both still reach the validator.

The line this sits behind is the same one: **this changes a value's Python type
to the one the schema already demands, and then the schema runs.** Anything that
widens what is accepted belongs in the schema, where a reader can see it.

Offline: no CATIA, no database.
"""

from __future__ import annotations

import pytest

from app.catia.dispatch import _normalise
from app.catia.validation import SchemaError, validate

PLANE = {
    "type": "object",
    "properties": {
        "reference": {"type": "string"},
        "distance_mm": {"type": "number"},
        "name": {"type": "string"},
        "reversed": {"type": "boolean"},
        "count": {"type": "integer"},
    },
    "additionalProperties": False,
    "required": ["reference", "distance_mm"],
}


def _run(arguments: dict) -> dict:
    return _normalise("catia_plane_offset", dict(arguments), PLANE)


class TestWhatIsAccepted:
    def test_the_measured_call_now_goes_through(self) -> None:
        fixed = _run({"name": "Plane_top", "reference": "XY", "distance_mm": "10"})

        assert fixed["distance_mm"] == 10.0
        validate(fixed, PLANE)

    @pytest.mark.parametrize("text,expected", [
        ("10", 10.0),
        ("10.5", 10.5),
        (" 10 ", 10.0),
        ("-2.5", -2.5),
        ("2e3", 2000.0),
        ("0", 0.0),
    ])
    def test_the_spellings_a_model_writes(self, text: str, expected: float) -> None:
        assert _run({"reference": "XY", "distance_mm": text})["distance_mm"] == expected

    def test_a_number_that_was_already_a_number_is_untouched(self) -> None:
        assert _run({"reference": "XY", "distance_mm": 10.0})["distance_mm"] == 10.0

    def test_the_dictionary_is_not_copied_when_nothing_changes(self) -> None:
        """The repair allocates only when it repairs — the same property the
        array version keeps, for a path that runs on every call."""
        arguments = {"reference": "XY", "distance_mm": 10.0}

        assert _normalise("catia_plane_offset", arguments, PLANE) is arguments

    def test_an_integer_field_takes_integral_text(self) -> None:
        fixed = _run({"reference": "XY", "distance_mm": 1.0, "count": "4"})

        assert fixed["count"] == 4
        assert isinstance(fixed["count"], int)
        validate(fixed, PLANE)


class TestWhatIsStillRefused:
    def _refused(self, arguments: dict) -> str:
        fixed = _run(arguments)
        with pytest.raises(SchemaError) as raised:
            validate(fixed, PLANE)
        return str(raised.value)

    def test_a_unit_carrying_string_is_left_alone(self) -> None:
        """mm-N-MPa throughout, and nothing here converts. "10 mm" and "10 in"
        are not the same request and this is not the place to decide."""
        assert self._refused({"reference": "XY", "distance_mm": "10 mm"})

    def test_a_word_is_not_a_number(self) -> None:
        assert self._refused({"reference": "XY", "distance_mm": "ten"})

    def test_a_comma_decimal_is_not_repaired(self) -> None:
        """`"1,5"` is one and a half in most of Europe and a malformed list
        everywhere else. Guessing between those is not a type fix."""
        assert self._refused({"reference": "XY", "distance_mm": "1,5"})

    def test_an_empty_string_is_not_zero(self) -> None:
        assert self._refused({"reference": "XY", "distance_mm": ""})

    def test_a_boolean_as_text_is_not_a_number(self) -> None:
        """`bool` is a subclass of `int` in Python, which is why the schema's own
        number check excludes it. The repair must not reintroduce it."""
        assert self._refused({"reference": "XY", "distance_mm": "true"})

    def test_a_fractional_string_in_an_integer_field_is_refused(self) -> None:
        """Rounding here would silently change what was asked for."""
        assert self._refused({"reference": "XY", "distance_mm": 1.0, "count": "4.5"})

    def test_a_string_field_is_not_touched(self) -> None:
        """A name that happens to be digits stays a name."""
        assert _run({"reference": "XY", "distance_mm": 1.0, "name": "12"})["name"] == "12"

    def test_the_schema_still_runs_afterwards(self) -> None:
        """The repair fixes the type and nothing else — an unknown field is
        still refused exactly as before."""
        fixed = _run({"reference": "XY", "distance_mm": "10", "bogus": 1})
        with pytest.raises(SchemaError):
            validate(fixed, PLANE)
