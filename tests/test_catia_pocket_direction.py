"""Reversing a pocket must actually reverse it.

A sketch on an origin plane does not sit on the face you are looking at -- it
sits at the origin. So a pocket drawn on XY, cutting into a block that occupies
z = 0..30, goes the wrong way by default and removes nothing. `catia_com.pocket`
and `catia_com.hole` both handle that by doing it, measuring, and turning the
feature round if no material went.

Except the turn-round did nothing. Both wrote `DirectionOrientation = 1`, and a
fresh `Pocket` on V5-R33 reports that property as **1 already**. Measured on the
seat, 2026-09-06, with a volume reading after each step:

    block volume: 180000.0 mm3
    after AddNewPocket, before any flip: 180000.0 mm3   (cut nothing)
      DirectionOrientation reads 1                      (already)
      set DirectionOrientation=1 -> volume 180000.0 mm3 (no-op)
      set DirectionOrientation=0 -> volume 172000.0 mm3 (40x20x10 gone)

So the recovery had never run, and what the caller got instead was the refusal
underneath it -- "removed no material, so it missed the solid entirely" -- which
reads like a diagnosis of the sketch and was really the flip failing to happen.
Ladder prompt H3 died on exactly this twice: a 40x20 pocket 10 deep into the top
of a 100x60x30 block, which is about as ordinary as a feature gets.

Confirmed fixed on the seat through the bridge's own methods: the same sequence
now returns 172,000 mm3, which is 180,000 minus 40x20x10 to the millimetre.

These tests are offline. They pin the logic -- read what CATIA chose, write the
other value -- which is the part that was wrong; the seat run above is what
proves the property is the right one to write.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from catia_bridge.catia_com import CatiaCom  # noqa: E402

reverse = CatiaCom._reverse_direction


class _Feature:
    """A pocket that remembers what was written to it."""

    def __init__(self, orientation: object = 1) -> None:
        self.DirectionOrientation = orientation  # noqa: N815 - COM spelling


class _Unreadable:
    """A feature whose orientation cannot be read back.

    Not hypothetical: several CATIA feature properties raise on read while
    accepting a write, and `_discard_failed_feature` exists because of the same
    class of surprise.
    """

    def __init__(self) -> None:
        self._written: list[int] = []

    @property
    def DirectionOrientation(self) -> int:  # noqa: N802
        raise RuntimeError("La methode DirectionOrientation a echoue")

    @DirectionOrientation.setter
    def DirectionOrientation(self, value: int) -> None:  # noqa: N802
        self._written.append(value)


def test_the_default_orientation_is_flipped_to_zero() -> None:
    """The measured case, and the one that was broken.

    CATIA hands back 1; writing 1 changes nothing; 0 is what cuts.
    """
    feature = _Feature(1)
    reverse(feature)
    assert feature.DirectionOrientation == 0


def test_zero_is_flipped_back_to_one() -> None:
    """It is a reversal, not "set it to 0".

    Written as a toggle on purpose: 1-being-the-default is an observation about
    this release, and hard-coding the opposite of an observation is precisely
    how the original line came about.
    """
    feature = _Feature(0)
    reverse(feature)
    assert feature.DirectionOrientation == 1


@pytest.mark.parametrize("orientation", [1, "1", 1.0])
def test_com_hands_the_value_back_in_more_than_one_type(orientation: object) -> None:
    """Late-bound COM returns whatever the type library says, and pywin32 does
    not normalise it. A string "1" compared against the integer 1 would silently
    take the wrong branch and put the flip back where it started."""
    feature = _Feature(orientation)
    reverse(feature)
    assert feature.DirectionOrientation == 0


def test_an_unreadable_orientation_still_gets_flipped() -> None:
    """It must not give up silently.

    Falling through without writing anything would reproduce the original bug
    exactly -- a flip that does not flip, followed by a refusal blaming the
    sketch.
    """
    feature = _Unreadable()
    reverse(feature)
    assert feature._written == [0]


def test_no_call_site_assigns_the_orientation_directly() -> None:
    """The rule, so a third pocket-like operation cannot repeat it.

    `pad` is the one legitimate direct assignment: there the *caller* asked for
    `reversed`, so an absolute value is right and 1 is the direction meant. Any
    other direct write is a flip that may not flip.
    """
    import ast

    source = (
        Path(__file__).resolve().parent.parent / "scripts" / "catia_bridge" / "catia_com.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    klass = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CatiaCom"
    )

    offenders: list[str] = []
    for method in klass.body:
        if not isinstance(method, ast.FunctionDef) or method.name in {
            "pad",
            "_reverse_direction",
        }:
            continue
        for node in ast.walk(method):
            targets = (
                node.targets if isinstance(node, ast.Assign)
                else [node.target] if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "DirectionOrientation":
                    offenders.append(f"{method.name}() line {node.lineno}")

    assert not offenders, (
        f"These assign DirectionOrientation directly: {offenders}. Call "
        "self._reverse_direction(feature) -- CATIA already reports 1 on a fresh "
        "feature, so assigning 1 is a no-op and the reversal never happens."
    )
