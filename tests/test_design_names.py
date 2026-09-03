"""Semantic names: the design's own handle on what it built.

The failure every test here guards against is the same one, and it is quiet:
a reference that still resolves after it has stopped meaning what it meant.
`Pad.2` survives an insertion upstream and points at a different pad; a retired
name handed to a new feature keeps every assertion green while measuring
something else. Nothing throws, and the part is wrong.

Offline by construction — no database fixture, no CATIA, no gmsh.
"""

import pytest

from app.catia.ops import limits
from app.design.errors import SemanticNameError
from app.design.names import MAX_DEPTH, MAX_SEGMENT_CHARS, RESERVED, NameTable, SemanticName


class TestParsing:
    def test_a_dotted_name_reads_as_ownership(self) -> None:
        name = SemanticName.parse("swingarm.pivot_bore")
        assert name.parts == ("swingarm", "pivot_bore")
        assert str(name) == "swingarm.pivot_bore"
        assert name.leaf == "pivot_bore"
        assert str(name.parent) == "swingarm"

    def test_a_single_segment_is_a_name(self) -> None:
        name = SemanticName.parse("frame")
        assert name.parts == ("frame",)
        assert name.parent is None

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            ".leading",
            "trailing.",
            "double..dot",
            "Upper.case",
            "9starts_with_a_digit",
            "has space",
            "has-hyphen",
            "has/slash",
        ],
    )
    def test_malformed_names_are_refused(self, text: str) -> None:
        with pytest.raises(SemanticNameError):
            SemanticName.parse(text)

    def test_the_sub_entity_syntax_is_reserved_rather_than_half_supported(self) -> None:
        """`bore#face(inner)` needs predicate selection, which does not exist yet.

        Reserving the spelling now means a design written today does not have to
        be rewritten when A3 lands, and refusing it explicitly means nobody gets
        a name containing a '#' that silently resolves to a whole feature.
        """
        with pytest.raises(SemanticNameError, match="not resolved yet"):
            SemanticName.parse("swingarm.pivot_bore#face(inner)")

    def test_a_non_string_is_refused_rather_than_coerced(self) -> None:
        with pytest.raises(SemanticNameError):
            SemanticName.parse(None)  # type: ignore[arg-type]

    def test_depth_and_segment_length_are_bounded(self) -> None:
        with pytest.raises(SemanticNameError, match="deep"):
            SemanticName.parse(".".join("abcdefg"[: MAX_DEPTH + 1]))
        with pytest.raises(SemanticNameError, match="characters"):
            SemanticName.parse("a" * (MAX_SEGMENT_CHARS + 1))


class TestReservedWords:
    """A design element must never be spellable as a vocabulary value.

    Name a plane `xy` and `catia_sketch_create(support="xy")` is ambiguous
    between it and CATIA's origin plane — and the daemon resolves the origin
    plane, so the sketch lands somewhere nobody chose and nothing errors.
    """

    @pytest.mark.parametrize("word", ["xy", "yz", "zx", "top", "bottom", "all", "normal"])
    def test_vocabulary_words_cannot_be_the_first_segment(self, word: str) -> None:
        assert word in RESERVED
        with pytest.raises(SemanticNameError, match="vocabulary"):
            SemanticName.parse(word)

    def test_they_are_fine_deeper_in_the_name(self) -> None:
        """`plate.top` is unambiguous — only a bare `top` collides."""
        assert SemanticName.parse("plate.top").leaf == "top"


class TestOwnership:
    def test_is_under_walks_the_prefix(self) -> None:
        arm = SemanticName.parse("swingarm")
        bore = SemanticName.parse("swingarm.pivot.bore")
        assert bore.is_under(arm)
        assert bore.is_under(SemanticName.parse("swingarm.pivot"))
        assert not bore.is_under(SemanticName.parse("frame"))

    def test_a_name_is_not_under_itself(self) -> None:
        """Otherwise `under()` returns the prefix among its own children."""
        arm = SemanticName.parse("swingarm")
        assert not arm.is_under(arm)

    def test_child_extends_the_name(self) -> None:
        assert str(SemanticName.parse("arm").child("bore")) == "arm.bore"

    def test_names_sort_readably(self) -> None:
        """Sorted order is load-bearing: the compiler must never walk a set."""
        names = [SemanticName.parse(t) for t in ("b.a", "a.b", "a.a")]
        assert [str(n) for n in sorted(names)] == ["a.a", "a.b", "b.a"]


class TestCatiaProjection:
    def test_dots_become_underscores(self) -> None:
        assert SemanticName.parse("swingarm.pivot_bore").catia_name() == "swingarm_pivot_bore"

    def test_the_projection_is_deterministic(self) -> None:
        """Same name, same CATIA name — on every machine, every rebuild.

        This is what makes "the design's name is the feature's name" checkable
        rather than aspirational, and it is a prerequisite for roadmap I5.
        """
        name = SemanticName.parse("a.b.c")
        assert name.catia_name() == SemanticName.parse("a.b.c").catia_name()

    def test_an_over_long_name_is_truncated_with_a_digest(self) -> None:
        long_a = SemanticName.parse(".".join(["a" * MAX_SEGMENT_CHARS] * 4))
        long_b = SemanticName.parse(
            ".".join(["a" * MAX_SEGMENT_CHARS] * 3 + ["a" * (MAX_SEGMENT_CHARS - 1) + "b"])
        )
        assert len(long_a.catia_name()) <= limits.MAX_NAME_CHARS
        assert len(long_b.catia_name()) <= limits.MAX_NAME_CHARS
        # They share every character inside the truncation window; only the
        # digest keeps them apart, which is the whole reason it is appended.
        assert long_a.catia_name() != long_b.catia_name()


class TestAllocation:
    def test_allocating_returns_the_catia_name(self) -> None:
        table = NameTable()
        assert table.allocate("plate.body") == "plate_body"
        assert "plate.body" in table
        assert table.catia_name("plate.body") == "plate_body"

    def test_a_duplicate_is_refused(self) -> None:
        table = NameTable()
        table.allocate("plate.body")
        with pytest.raises(SemanticNameError, match="already"):
            table.allocate("plate.body")

    def test_two_names_that_project_onto_one_catia_name_are_refused(self) -> None:
        """`arm.pivot_bore` and `arm_pivot.bore` both become `arm_pivot_bore`.

        The projection is readable rather than injective on purpose — escaping
        the separator would put `arm_pivot__bore` in an engineer's feature tree.
        The collision is rare, and it is caught here rather than by two features
        quietly sharing a name in CATIA.
        """
        table = NameTable()
        table.allocate("arm.pivot_bore")
        with pytest.raises(SemanticNameError, match="both become"):
            table.allocate("arm_pivot.bore")

    def test_names_are_reported_sorted(self) -> None:
        table = NameTable()
        for name in ("c.a", "a.b", "b.c"):
            table.allocate(name)
        assert [str(n) for n in table.names()] == ["a.b", "b.c", "c.a"]

    def test_under_lists_what_a_prefix_owns(self) -> None:
        table = NameTable()
        for name in ("arm.bore", "arm.rib", "frame.rib"):
            table.allocate(name)
        assert [str(n) for n in table.under("arm")] == ["arm.bore", "arm.rib"]


class TestRetirement:
    """The harsh rule, and why it is the right one."""

    def test_a_retired_name_cannot_be_allocated_again(self) -> None:
        table = NameTable()
        table.allocate("arm.rib")
        table.retire("arm.rib")
        assert "arm.rib" not in table
        with pytest.raises(SemanticNameError, match="retired"):
            table.allocate("arm.rib")

    def test_reviving_is_an_explicit_claim_rather_than_an_accident(self) -> None:
        table = NameTable()
        table.allocate("arm.rib")
        table.retire("arm.rib")
        assert table.revive("arm.rib") == "arm_rib"
        assert "arm.rib" in table

    def test_reviving_a_name_that_was_never_retired_is_refused(self) -> None:
        table = NameTable()
        with pytest.raises(SemanticNameError, match="not a retired name"):
            table.revive("arm.rib")

    def test_retiring_something_that_is_not_there_says_what_is(self) -> None:
        table = NameTable()
        table.allocate("arm.bore")
        with pytest.raises(SemanticNameError, match="arm.bore"):
            table.retire("arm.rib")

    def test_looking_up_a_retired_name_says_it_was_retired(self) -> None:
        """A stale reference gets told *why* it is stale, not just that it is."""
        table = NameTable()
        table.allocate("arm.rib")
        table.retire("arm.rib")
        with pytest.raises(SemanticNameError, match="retired earlier"):
            table.catia_name("arm.rib")


class TestPersistence:
    def test_a_table_round_trips(self) -> None:
        table = NameTable()
        table.allocate("arm.bore")
        table.allocate("arm.rib")
        table.retire("arm.rib")

        restored = NameTable.from_dict(table.to_dict())

        assert [str(n) for n in restored.names()] == ["arm.bore"]
        assert [str(n) for n in restored.retired()] == ["arm.rib"]

    def test_the_retired_set_survives_the_round_trip(self) -> None:
        """The half that is easy to drop and expensive to lose.

        A table reloaded without its retired names hands a dead name to a new
        element, which is exactly what retirement exists to prevent.
        """
        table = NameTable()
        table.allocate("arm.rib")
        table.retire("arm.rib")

        restored = NameTable.from_dict(table.to_dict())

        with pytest.raises(SemanticNameError, match="retired"):
            restored.allocate("arm.rib")

    def test_an_unknown_format_version_is_refused_rather_than_guessed_at(self) -> None:
        with pytest.raises(SemanticNameError, match="format version"):
            NameTable.from_dict({"format_version": 99, "live": [], "retired": []})

    def test_a_name_listed_as_both_live_and_retired_is_refused(self) -> None:
        with pytest.raises(SemanticNameError, match="both live and retired"):
            NameTable.from_dict(
                {"format_version": 1, "live": ["arm.rib"], "retired": ["arm.rib"]}
            )


class TestContainsIsTotal:
    def test_a_malformed_string_is_simply_absent(self) -> None:
        """`in` must answer, not raise — it is used in conditionals everywhere."""
        table = NameTable()
        assert "not a name!" not in table
        assert 17 not in table
