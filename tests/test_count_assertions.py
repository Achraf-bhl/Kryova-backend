"""A count is the one measurement where `==` needs no tolerance.

`Assertion` refuses an exact equality with no slack, and it is right to: a
kernel integrating a volume does not return 101701.91 on the nose, so a claim
written that way fails on a part that is correct. But the rule was applied to
every measurement, and a count is not that kind of number. One solid is one
solid. Eleven faces are eleven faces. Half a face of slack is not a thing.

Measured end to end on 2026-09-05: asked to check the flange it had just built,
the model claimed `solid_count == 1` — correct, and exactly checkable — and was
refused and told to give it a tolerance. It spent three of its turn's steps
being taught that our own verification tool does not mean what it says.

The path is what decides, because `app/design/` must not import
`app.kernel.contract` (that pulls ~166 MB of OCP into a package whose tests run
offline in under a second). `TestItAgreesWithTheKernelsOwnContract` is what
keeps the two honest: it reads the real contract, where `unit="count"` actually
lives, and asserts the convention matches it on every declared path.
"""

from __future__ import annotations

import pytest

from app.design.assertions import Assertion, counts_things
from app.design.errors import SpecError


class TestWhatCountsAsACount:
    @pytest.mark.parametrize(
        "measure",
        ["solid_count", "face_count", "edge_count", "shell_count", "vertex_count"],
    )
    def test_a_bare_count_path(self, measure: str) -> None:
        assert counts_things(measure) is True

    @pytest.mark.parametrize(
        "measure",
        ["mass_kg", "volume_mm3", "surface_area_mm2", "min_wall_mm", "draft_angle_deg"],
    )
    def test_a_measured_quantity_is_not(self, measure: str) -> None:
        assert counts_things(measure) is False

    def test_a_nested_count(self) -> None:
        assert counts_things("interrogation.undercut.face_count") is True

    def test_a_nested_quantity_is_not(self) -> None:
        assert counts_things("bounding_box_mm.size") is False

    def test_an_indexed_path_reads_its_leaf(self) -> None:
        assert counts_things("bounding_box_mm.size[2]") is False

    def test_the_word_count_alone(self) -> None:
        assert counts_things("elements.count") is True

    def test_a_word_merely_ending_in_count_is_still_a_count(self) -> None:
        """`_count` is the kernel's own suffix and there is no other reading."""
        assert counts_things("open_edge_count") is True

    def test_whitespace_does_not_change_the_answer(self) -> None:
        assert counts_things("  solid_count  ") is True

    def test_something_that_merely_contains_count(self) -> None:
        """`countersink_mm` is a depth, not a tally."""
        assert counts_things("countersink_mm") is False


class TestTheAssertionAcceptsAnExactCount:
    def test_solid_count_equals_one_is_accepted(self) -> None:
        """The claim the model was refused for making."""
        claim = Assertion(name="one solid", measure="solid_count", comparison="==", bound=1)

        assert claim.tolerance == 0.0

    def test_face_count_equals_eleven_is_accepted(self) -> None:
        Assertion(name="faces", measure="face_count", comparison="==", bound=11)

    def test_it_actually_passes_against_a_measurement(self) -> None:
        from app.design.assertions import check_assertions

        report = check_assertions(
            [Assertion(name="one solid", measure="solid_count", comparison="==", bound=1)],
            {"solid_count": 1},
        )

        assert report.ok is True

    def test_and_actually_fails_when_the_count_is_wrong(self) -> None:
        """An exact claim that cannot fail would be worse than no claim."""
        from app.design.assertions import check_assertions

        report = check_assertions(
            [Assertion(name="one solid", measure="solid_count", comparison="==", bound=1)],
            {"solid_count": 2},
        )

        assert report.ok is False

    def test_a_tolerance_on_a_count_is_still_allowed(self) -> None:
        """Existing designs wrote `tolerance=0.5` to get round the refusal, with
        a comment saying half a unit *is* exactness for a count. They must not
        start failing."""
        Assertion(name="one solid", measure="solid_count", comparison="==",
                  bound=1, tolerance=0.5)


class TestTheRefusalStandsForEverythingElse:
    def test_a_mass_still_needs_a_tolerance(self) -> None:
        with pytest.raises(SpecError, match="round decimals"):
            Assertion(name="mass", measure="mass_kg", comparison="==", bound=0.324)

    def test_a_volume_still_needs_a_tolerance(self) -> None:
        with pytest.raises(SpecError, match="round decimals"):
            Assertion(name="volume", measure="volume_mm3", comparison="==", bound=101207.47)

    def test_the_refusal_now_names_the_exception(self) -> None:
        """So a model refused on a length learns the rule rather than the case."""
        with pytest.raises(SpecError, match="solid_count"):
            Assertion(name="mass", measure="mass_kg", comparison="==", bound=0.324)

    def test_a_negative_tolerance_is_still_refused_on_a_count(self) -> None:
        with pytest.raises(SpecError, match="stricter than exact"):
            Assertion(name="one solid", measure="solid_count", comparison="==",
                      bound=1, tolerance=-1.0)


class TestItAgreesWithTheKernelsOwnContract:
    """The convention is a string rule here and a declared unit there.

    This is the only place the two meet. It imports the kernel, which the design
    package deliberately does not, so a drift between the suffix convention and
    `unit="count"` is caught here rather than by a model being given wrong advice
    in a conversation.
    """

    @staticmethod
    def _contract_entries():
        from app.kernel.contract import QUANTITIES

        return list(QUANTITIES)

    def test_every_count_in_the_contract_is_recognised(self) -> None:
        missed = [
            entry.path
            for entry in self._contract_entries()
            if entry.unit == "count" and not counts_things(entry.path)
        ]

        assert not missed, f"the contract calls these counts and this does not: {missed}"

    def test_nothing_else_in_the_contract_is_mistaken_for_one(self) -> None:
        wrong = [
            entry.path
            for entry in self._contract_entries()
            if entry.unit != "count" and counts_things(entry.path)
        ]

        assert not wrong, f"treated as counts but the contract gives them a unit: {wrong}"

    def test_the_contract_actually_declares_some_counts(self) -> None:
        """Otherwise the two tests above pass vacuously."""
        counts = [e.path for e in self._contract_entries() if e.unit == "count"]

        assert len(counts) >= 3, counts
