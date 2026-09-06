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


class TestAListWrittenOneItemPerLine:
    """The call that lost ladder prompt PRO4, 2026-09-07, on the seat.

    The model sent the punch press's C-frame outline as twelve `[x, y]` pairs,
    one per line -- the right profile, correctly ordered, for the frame the
    prompt asks for. `json.loads` refuses it: no outer brackets, newlines where
    the commas should be. The call came back `points must be array, got str`
    and the agent did not recover -- it padded an empty sketch, failed, created
    two more sketches it could not use, and the turn ended on the round cap with
    no machine built.

    The bar is unchanged: the repaired string still has to parse as JSON and
    still has to yield a list, and the schema still runs afterwards.
    """

    #: Copied from the operation log, not retyped from memory.
    C_FRAME = (
        "[0, 0]\n[200, 0]\n[200, 150]\n[150, 150]\n[150, 180]\n[200, 180]\n"
        "[200, 350]\n[0, 350]\n[0, 180]\n[50, 180]\n[50, 150]\n[0, 150]"
    )
    SCHEMA = {"properties": {"points": {"type": "array"}}}

    def test_the_c_frame_profile_is_recovered(self) -> None:
        out = _normalise("catia_sketch_polyline", {"points": self.C_FRAME}, self.SCHEMA)
        assert out["points"][0] == [0, 0]
        assert out["points"][-1] == [0, 150]
        assert len(out["points"]) == 12

    def test_trailing_commas_per_line_are_tolerated(self) -> None:
        out = _normalise(
            "catia_sketch_polyline", {"points": "[0, 0],\n[10, 0],\n[10, 5]"}, self.SCHEMA
        )
        assert out["points"] == [[0, 0], [10, 0], [10, 5]]

    def test_plain_numbers_one_per_line(self) -> None:
        out = _normalise("catia_pattern", {"points": "1\n2\n3"}, self.SCHEMA)
        assert out["points"] == [1, 2, 3]

    def test_strict_json_still_wins(self) -> None:
        out = _normalise(
            "catia_sketch_polyline", {"points": "[[0, 0], [10, 0]]"}, self.SCHEMA
        )
        assert out["points"] == [[0, 0], [10, 0]]

    def test_prose_is_left_alone_for_the_validator_to_refuse(self) -> None:
        """The repair must not turn an explanation into a list. Anything that
        does not parse as JSON arrives exactly as it was sent."""
        for text in ("a rectangle 200 by 350", "the outline of the frame", "200 x 350"):
            out = _normalise("catia_sketch_polyline", {"points": text}, self.SCHEMA)
            assert out["points"] == text

    def test_multi_line_prose_is_left_alone_too(self) -> None:
        """The dangerous shape: several lines, which is what the repair looks
        for, and none of them JSON. Wrapping this would hand the daemon a list
        of sentences and turn a clear refusal into a confusing one."""
        text = "start at the origin\nrun 200 across\nthen 350 up"
        out = _normalise("catia_sketch_polyline", {"points": text}, self.SCHEMA)
        assert out["points"] == text

    def test_a_single_line_is_not_wrapped_into_a_list(self) -> None:
        """`"[0, 0]"` is one point, not a profile. Wrapping it would invent a
        one-point polyline out of what is far more likely a mistake."""
        out = _normalise("catia_sketch_polyline", {"points": "0, 0"}, self.SCHEMA)
        assert out["points"] == "0, 0"

    def test_a_field_the_schema_does_not_call_an_array_is_untouched(self) -> None:
        out = _normalise(
            "catia_sketch_polyline",
            {"note": "[0, 0]\n[10, 0]"},
            {"properties": {"note": {"type": "string"}}},
        )
        assert out["note"] == "[0, 0]\n[10, 0]"
