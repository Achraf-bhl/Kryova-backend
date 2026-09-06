"""The billet question, documented for months and emitted by nothing.

`contract.py` has declared `oriented_bounding_box_mm.size` and `.volume_mm3`
since version 1.1, `metrology.oriented_bounding_box` was written and exported —
and **nothing ever called it**. So `app/requirements/` would accept a requirement
on either path (the vocabulary allows a documented one) and then verify it
`UNMEASURED` for ever: exactly the "wish with a number on it" that the vocabulary
refusal exists to prevent, arriving through the front door.

Found on 2026-09-06 by an agent connecting the requirements model to a real part.
`contract.undocumented_paths()` is asserted empty, which catches a payload key
nobody documented; nothing caught the converse.

Why the quantity is worth emitting rather than the entry worth deleting: the
axis-aligned box answers "what volume does this occupy in a scene", and the
oriented box answers "what stock do I cut it from". They are different questions
with very different answers, and the second is the one somebody spends money on.
"""

from __future__ import annotations

import pytest

from app.kernel import OcctRunner


def _bar(turn_deg: float = 0.0):
    """A 100x10x10 bar, optionally lying at an angle in the XY plane."""
    runner = OcctRunner()
    runner("catia_new_part", {"name": "bar"})
    runner("catia_sketch_create", {"support": "XY", "name": "s"})
    runner("catia_sketch_rectangle", {"sketch": "s", "width_mm": 100.0, "height_mm": 10.0})
    runner("catia_pad", {"sketch": "s", "length_mm": 10.0})
    if turn_deg:
        runner("catia_rotate", {"angle_deg": turn_deg, "axis": "z"})
    return runner


class TestItIsEmittedAtAll:
    def test_a_measurement_carries_it(self) -> None:
        """The whole defect: it was documented and absent."""
        measured = _bar()("catia_measure", {})

        assert "oriented_bounding_box_mm" in measured

    def test_it_carries_the_paths_the_contract_documents(self) -> None:
        """`contract.py` names `.size` and `.volume_mm3` specifically. A payload
        that emits the key but not those paths satisfies nothing."""
        box = _bar()("catia_measure", {})["oriented_bounding_box_mm"]

        assert "size" in box
        assert "volume_mm3" in box

    def test_an_axis_aligned_part_agrees_with_its_aabb(self) -> None:
        """The sanity case. A box lying square on the axes has no tighter box."""
        measured = _bar()("catia_measure", {})

        assert sorted(measured["oriented_bounding_box_mm"]["size"], reverse=True) == pytest.approx(
            sorted(measured["bounding_box_mm"]["size"], reverse=True), abs=1e-6
        )


class TestItAnswersTheQuestionTheAabbCannot:
    """The reason the quantity exists, stated as a test rather than a docstring."""

    def test_a_diagonal_bar_has_a_far_larger_axis_aligned_box(self) -> None:
        """A 100 mm bar at 45° spans about 78 mm in x and in y, so its
        axis-aligned box is roughly six times the part. Buying billet from that
        buys the wrong billet."""
        measured = _bar(turn_deg=45.0)("catia_measure", {})

        aabb = measured["bounding_box_mm"]["size"]
        assert aabb[0] > 70.0 and aabb[1] > 70.0

    def test_but_the_oriented_box_still_finds_the_bar(self) -> None:
        """Same part, and the box that matters is still 100 x 10 x 10."""
        measured = _bar(turn_deg=45.0)("catia_measure", {})

        size = sorted(measured["oriented_bounding_box_mm"]["size"], reverse=True)
        assert size[0] == pytest.approx(100.0, abs=0.5)
        assert size[1] == pytest.approx(10.0, abs=0.5)
        assert size[2] == pytest.approx(10.0, abs=0.5)

    def test_the_oriented_volume_is_much_smaller_than_the_aabb(self) -> None:
        """The number a stock decision is made on."""
        measured = _bar(turn_deg=45.0)("catia_measure", {})

        aabb = measured["bounding_box_mm"]["size"]
        axis_aligned = aabb[0] * aabb[1] * aabb[2]
        oriented = measured["oriented_bounding_box_mm"]["volume_mm3"]

        assert oriented < axis_aligned / 3.0

    def test_it_is_never_smaller_than_the_part(self) -> None:
        """A box tighter than the solid it encloses is arithmetic nobody should
        trust, and is the direction that would understate the stock."""
        measured = _bar(turn_deg=30.0)("catia_measure", {})

        assert measured["oriented_bounding_box_mm"]["volume_mm3"] >= measured["volume_mm3"]


class TestTheGeneralGapIsNamedRatherThanGuarded:
    """**This instance is fixed; the class of bug is not, and cannot be yet.**

    `contract.undocumented_paths()` is asserted empty, so a payload key nobody
    documented is caught. The converse — a documented path that *nothing emits* —
    has no guard, and that is how `oriented_bounding_box_mm` sat documented and
    unproduced since version 1.1 while `requirements.vocabulary.require` happily
    accepted requirements on it.

    A general test cannot be written honestly today. `contract.Entry` carries
    `path`, `unit`, `summary`, `typical_basis`, `since`, `superseded_by` and
    `indexed` — but **not which call produces the quantity**. Some come from
    `measure()`, most from `catia_analysis_part`'s interrogations, some only
    when a material is set, and some are per-feature or per-selector. Splitting
    them needs a hand-written list, and a hand-written list of what a contract
    ought to contain is precisely the thing that goes stale and then lies.

    The fix is a `source` field on `Entry` — which call emits this, or that
    nothing does yet and why. That is a change to the contract's own shape and is
    recorded here rather than made in passing. Until then, this class is a
    signpost: the guard is missing, and it is missing on purpose rather than by
    oversight.
    """

    def test_the_contract_records_no_source_for_a_quantity(self) -> None:
        """Pinned so that adding one turns this red and sends whoever added it
        to write the guard that becomes possible."""
        from app.kernel import contract

        entry = contract.QUANTITIES[0]

        assert not hasattr(entry, "source"), (
            "Entry has grown a source field — the 'documented but never emitted' "
            "guard can now be written properly; see this class's docstring."
        )

    def test_the_instance_that_prompted_this_is_closed(self) -> None:
        measured = _bar()("catia_measure", {})

        assert "oriented_bounding_box_mm" in measured
