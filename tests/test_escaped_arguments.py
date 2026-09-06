"""A name the model wrote as its own escape sequence is that name.

Measured on ladder prompt S2, 2026-09-06, on a French seat. The agent read the
feature list, which reported the revolution under its French name with an acute
accent, and then called `catia_list_faces` with the six literal characters of
that letter's JSON escape. The refusal came back as

    No feature named 'R\\u00e9volution.1' in this part. Features: Revolution.1

-- with the accent on the second name -- which prints two strings that look
different and are the same name. There is nothing the model can do with that,
and it lost the rest of the turn.

The repair is at the boundary, in `_normalise`, and applies to every tool and
every string argument rather than to a list of tools it has been seen on: the
same double encoding reaches a material name, a component name and a sketch
name by exactly the same route, and a French, German or Japanese seat names
every feature this way.

It is a transport repair, not a spelling guess. CATIA puts no backslash in a
feature name, so a backslash followed by a valid escape body can only be an
encoding that survived one round too many.
"""

from __future__ import annotations

from typing import Any

from app.catia.dispatch import _decode_literal_escapes, _normalise, _repair_escapes

BS = chr(92)  # a real backslash, spelled so no reader has to count them


class TestTheMeasuredCase:
    def test_the_french_revolution(self) -> None:
        assert _decode_literal_escapes(f"R{BS}u00e9volution.1") == "Révolution.1"

    def test_the_french_pocket(self) -> None:
        assert _decode_literal_escapes(f"Poche extrud{BS}u00e9e.1") == "Poche extrudée.1"

    def test_a_german_umlaut(self) -> None:
        assert _decode_literal_escapes(f"Aussparung{BS}u00fc") == "Aussparungü"

    def test_a_short_hex_escape(self) -> None:
        assert _decode_literal_escapes(f"R{BS}xe9volution.1") == "Révolution.1"


class TestNothingElseIsTouched:
    def test_a_name_that_was_already_right(self) -> None:
        assert _decode_literal_escapes("Révolution.1") == "Révolution.1"

    def test_a_plain_ascii_name(self) -> None:
        assert _decode_literal_escapes("Pad.1") == "Pad.1"

    def test_a_windows_path_keeps_its_backslashes(self) -> None:
        """`C:{BS}work{BS}...` has backslashes and no escape body after them.
        Decoding the whole string with unicode_escape would eat them; only a
        backslash followed by a *valid* escape body is touched."""
        path = f"C:{BS}work{BS}parts{BS}Shaft.CATPart"
        assert _decode_literal_escapes(path) == path

    def test_a_windows_path_whose_folders_start_with_escape_letters(self) -> None:
        """The dangerous one, and the reason the pattern is narrow rather than
        a blanket unicode_escape of anything after a backslash. Every document
        this system writes lands under a path it composes itself, and a folder
        called `new` or `temp` puts `{BS}n` and `{BS}t` in it -- both valid
        Python escapes. Widened by one character, the repair would turn a real
        remote_path into one containing a newline and a tab, and the file would
        be looked for somewhere that cannot exist."""
        path = f"C:{BS}new{BS}temp{BS}bell{BS}Shaft.CATPart"
        assert _decode_literal_escapes(path) == path
        assert BS + "n" in _decode_literal_escapes(path)
        assert BS + "t" in _decode_literal_escapes(path)

    def test_a_backslash_before_an_invalid_escape_body(self) -> None:
        assert _decode_literal_escapes(f"Sketch{BS}u00zz") == f"Sketch{BS}u00zz"

    def test_a_lone_backslash(self) -> None:
        assert _decode_literal_escapes(f"odd{BS}name") == f"odd{BS}name"


class TestEveryStringInTheCall:
    def test_a_plain_argument(self) -> None:
        repaired = _repair_escapes({"feature": f"R{BS}u00e9volution.1"})
        assert repaired["feature"] == "Révolution.1"

    def test_strings_inside_a_list(self) -> None:
        """`catia_constrain` takes its elements as a list, and a component name
        on a French seat carries accents just as a feature name does."""
        repaired = _repair_escapes({"elements": [f"Man{BS}u00e7on/YZ", "Shaft/YZ"]})
        assert repaired["elements"] == ["Mançon/YZ", "Shaft/YZ"]

    def test_numbers_and_booleans_are_untouched(self) -> None:
        arguments: dict[str, Any] = {"length_mm": 40.0, "through_all": True, "n": 3}
        assert _repair_escapes(arguments) == arguments

    def test_a_call_with_nothing_to_repair_is_returned_unchanged(self) -> None:
        """Identity, not a copy: the common path must not allocate."""
        arguments = {"feature": "Pad.1", "length_mm": 10}
        assert _repair_escapes(arguments) is arguments

    def test_a_mixed_list_keeps_its_non_strings(self) -> None:
        repaired = _repair_escapes({"at": [f"a{BS}u00e9", 2, None]})
        assert repaired["at"] == ["aé", 2, None]


class TestItRunsInNormalise:
    """The wiring, not just the helper -- an unwired repair fixes nothing."""

    def test_a_feature_name_is_repaired_on_the_way_through(self) -> None:
        out = _normalise("catia_list_faces", {"feature": f"R{BS}u00e9volution.1"}, None)
        assert out["feature"] == "Révolution.1"

    def test_it_applies_to_a_tool_it_was_never_measured_on(self) -> None:
        out = _normalise("catia_set_material", {"material": f"acier{BS}u00e9"}, None)
        assert out["material"] == "acieré"

    def test_it_composes_with_the_array_string_repair(self) -> None:
        """Both repairs in one call: a list sent as text, whose members are
        double-encoded. The array parse must run first or the escape repair
        sees a single string and the list never parses."""
        schema = {"properties": {"elements": {"type": "array"}}}
        out = _normalise(
            "catia_constrain",
            {"elements": '["Man' + BS + BS + 'u00e7on/YZ", "Shaft/YZ"]'},
            schema,
        )
        assert out["elements"] == ["Mançon/YZ", "Shaft/YZ"]

    def test_the_hole_repair_still_runs(self) -> None:
        out = _normalise("catia_hole", {"through_all": True, "depth_mm": 0}, None)
        assert "depth_mm" not in out
