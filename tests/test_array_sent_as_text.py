"""A coordinate sent as the text of a list is that list.

Measured on ladder prompts H4 and H5, 2026-09-06, on three separate runs:

    catia_sketch_line(start="[0, 0]", end="[150, 0]")
      -> catia_sketch_line: start must be array, got str
    catia_sketch_line(start="[0, 0]", end="[150, 0]")
      -> catia_sketch_line: start must be array, got str

One run spent four of its twenty rounds there. The refusal is accurate and it
does not help: the model already believes it sent a list, because what it wrote
*is* the list, so reading the message gives it nothing to do differently. It
sends the same thing again.

Nothing is guessed. The string must parse as JSON and must yield a list;
everything else arrives at the validator exactly as before, and the validator
then refuses everything it refused before -- a list of the wrong length, a
list of strings where numbers are wanted, a string that is not a list at all.
What this removes is one round trip whose outcome was never in doubt.

The line this sits behind matters, because it is the one that keeps this from
becoming "accept whatever arrives": **this changes a value's Python type to
the one the schema already demands, and then the schema runs.** Anything that
would actually widen what is accepted belongs in the schema, where a reader
can see it.

Offline: no CATIA, no database.
"""

from __future__ import annotations

import pytest

from app.catia.dispatch import _normalise
from app.catia.validation import SchemaError, validate

POINT = {
    "type": "object",
    "properties": {
        "start": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "end": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
        "sketch": {"type": "string"},
        "construction": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def normalised(arguments: dict, schema: dict = POINT) -> dict:
    return _normalise("catia_sketch_line", arguments, schema)


class TestWhatIsAccepted:
    def test_the_measured_call_now_goes_through(self) -> None:
        """The exact arguments from the seat, three runs running."""
        result = normalised({"start": "[0, 0]", "end": "[150, 0]"})
        assert result == {"start": [0, 0], "end": [150, 0]}
        validate(result, POINT)

    def test_a_list_that_was_already_a_list_is_untouched(self) -> None:
        original = {"start": [0.0, 0.0], "end": [150.0, 0.0]}
        assert normalised(original) == original

    def test_the_dictionary_is_not_copied_when_nothing_changes(self) -> None:
        """The ordinary path is every call that was already correct. It must
        not pay for the one that was not."""
        original = {"start": [0, 0], "sketch": "Sketch.1"}
        assert normalised(original) is original

    @pytest.mark.parametrize("text", ['[0,0]', '[0, 0]', '[ 0 , 0 ]', '[0.5,-2]'])
    def test_the_spellings_a_model_writes(self, text: str) -> None:
        assert isinstance(normalised({"start": text})["start"], list)


class TestWhatIsStillRefused:
    def test_a_string_that_is_not_a_list_is_left_alone(self) -> None:
        """`"Sketch.1"` is a name, not a coordinate, and a field expecting an
        array that gets one is a real mistake."""
        assert normalised({"start": "Sketch.1"})["start"] == "Sketch.1"
        with pytest.raises(SchemaError):
            validate(normalised({"start": "Sketch.1"}), POINT)

    def test_a_json_object_is_not_a_list(self) -> None:
        assert normalised({"start": '{"x": 0}'})["start"] == '{"x": 0}'

    def test_a_number_as_text_is_not_a_list(self) -> None:
        """`"5"` parses as JSON perfectly well and is not an array."""
        assert normalised({"start": "5"})["start"] == "5"

    def test_a_list_of_the_wrong_length_still_fails_validation(self) -> None:
        """The parse is not the check. This is what stops it becoming one."""
        parsed = normalised({"start": "[0, 0, 0]"})
        assert parsed["start"] == [0, 0, 0]
        with pytest.raises(SchemaError):
            validate(parsed, POINT)

    def test_a_list_of_strings_still_fails_validation(self) -> None:
        parsed = normalised({"start": '["a", "b"]'})
        with pytest.raises(SchemaError):
            validate(parsed, POINT)

    def test_a_string_field_is_never_parsed(self) -> None:
        """`sketch` is declared a string. A sketch that happens to be named
        `[1]` must stay that name."""
        assert normalised({"sketch": "[1]"})["sketch"] == "[1]"

    def test_a_field_the_schema_does_not_declare_is_untouched(self) -> None:
        """An unknown field is refused for being unknown, and this must not
        quietly reshape it on the way to that refusal."""
        assert normalised({"points": "[[0,0],[1,1]]"})["points"] == "[[0,0],[1,1]]"

    def test_python_syntax_is_not_accepted(self) -> None:
        """`ast.literal_eval` would take a tuple, a set and an expression.
        The point is to accept the thing the model meant and nothing more."""
        assert normalised({"start": "(0, 0)"})["start"] == "(0, 0)"


class TestItDoesNotDisturbWhatWasThere:
    def test_a_through_hole_still_loses_its_zero_depth(self) -> None:
        """The normaliser's other case, which predates this one."""
        schema = {
            "type": "object",
            "properties": {
                "through_all": {"type": "boolean"},
                "depth_mm": {"type": "number", "exclusiveMinimum": 0},
            },
        }
        result = _normalise("catia_hole", {"through_all": True, "depth_mm": 0}, schema)
        assert "depth_mm" not in result

    def test_it_works_with_no_schema_at_all(self) -> None:
        """A tool whose spec could not be resolved must not raise here -- the
        refusal for that is a better one and comes later."""
        assert _normalise("catia_sketch_line", {"start": "[0, 0]"}, None) == {"start": "[0, 0]"}

    def test_it_is_called_with_the_tools_own_schema(self) -> None:
        """A normaliser given no schema silently does nothing, so the wiring
        is the whole feature."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "app" / "catia" / "dispatch.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "call_catia"
        )
        body = ast.get_source_segment(source, node) or ""
        assert "_normalise(tool, arguments, spec.parameters)" in body

    def test_the_parse_runs_before_validation(self) -> None:
        """After it, every one of these calls is still refused."""
        import ast
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent / "app" / "catia" / "dispatch.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "call_catia"
        )
        body = ast.get_source_segment(source, node) or ""
        assert body.index("_normalise(tool, arguments") < body.index("validate(arguments")
